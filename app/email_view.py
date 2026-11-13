"""Email (/email): Klaviyo email and text message (SMS) performance by flow, by campaign and over time, read from klaviyo_daily and its dimensions
(migration 206; loaded nightly by etl.klaviyo). See docs/EMAIL.md for the definitions and the caveats.

Design: one parameterised SELECT per request pulls the daily rows the answer needs (klaviyo_daily is about 18.5K rows for 120 days and its primary key leads
with the day, so a range scan is a few milliseconds); everything else is arithmetic in Python. The builders take a *source* object (DbSource below; the tests
and the UI mock use an in-memory one with the same methods), so the numbers can be checked against plain Python.

Rules (the ones the rest of the app follows, plus the Klaviyo-specific ones):
  * Days are Pacific days (the date as stored). Weeks are ISO weeks Monday to Sunday, months calendar months.
  * Every rate is a ratio of sums over the window, never an average of daily ratios.
  * unique_opens and unique_clicks are per-day uniques, so a sum over days overstates the window's true uniques (about +22% campaign email, +54% flow
    email on unique opens): rates built from them are approximate, good for comparing, not Klaviyo's own reported rates.
  * Open rate = unique opens / delivered, over email only (text messages have no opens, so they stay out of the denominator). Click rate = unique clicks /
    delivered (email and text). Click to open = email unique clicks / unique opens. Revenue per delivered = revenue / delivered. Unsubscribe rate =
    unsubscribes / delivered, over every message whose unsubscribes Klaviyo reports (not flow text messages).
  * Revenue and orders are Klaviyo-attributed ('Placed Order - Upstackified'), include open-attributed orders and are NOT additive with Meta, Google or
    Upstack revenue.
  * A bucket (week, month) is partial when it runs past the last loaded day or starts before the first loaded day; it is marked, never plotted as a drop.
  * The prior period is the same number of days straight before the window and is shown only when every one of its days is loaded.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Protocol

from . import queries as q
from .trends import daterange, label_of, span_of

WINDOWS = {"7d": 7, "30d": 30, "90d": 90, "all": None}
DEFAULT_WINDOW = "30d"
KINDS = ("all", "flows", "campaigns")
CHANNELS = ("all", "email", "sms")
GRAINS = ("day", "week", "month")
KIND_SOURCE = {"flows": "flow", "campaigns": "campaign"}
MAX_CAMPAIGNS = 1500

FIELDS = ("sends", "delivered", "opens", "unique_opens", "clicks", "unique_clicks", "unsubscribes", "orders", "revenue")

# key, label, kind (how the number is written), polarity of a rise (good / bad / neutral), one-line help
METRICS = [
    ("sends", "Sends", "int", "neutral", "Messages Klaviyo tried to send (delivered plus bounced)."),
    ("delivered", "Delivered", "int", "neutral", "Messages that arrived."),
    ("opens", "Opens (views)", "int", "neutral", "Email opens, counted as distinct people per day and added up over the days, so it reads a little high. Apple Mail Privacy Protection opens count."),
    ("open_rate", "Open rate", "rate", "good", "Unique opens divided by delivered email. Approximate: Apple Mail Privacy Protection inflates it and per-day uniques added over days overstate it."),
    ("click_rate", "Click rate", "rate", "good", "Unique clicks divided by delivered. Clicks Klaviyo flags as bots are left out. Per-day uniques added over days read a little high."),
    ("click_to_open", "Click to open", "rate", "good", "Email unique clicks divided by unique opens."),
    ("orders", "Orders", "int", "good", "Orders Klaviyo attributes to the message (Klaviyo-attributed)."),
    ("revenue", "Revenue", "money", "good", "Klaviyo-attributed revenue. Includes orders credited to an open, so it is not additive with Meta, Google or Upstack revenue."),
    ("rev_per_delivered", "Revenue per delivered", "money2", "good", "Klaviyo-attributed revenue divided by delivered."),
    ("orders_per_delivered", "Orders per delivered", "rate", "good", "Klaviyo-attributed orders divided by delivered."),
    ("unsub_rate", "Unsubscribe rate", "rate", "bad", "Unsubscribes divided by delivered. Klaviyo does not report text message unsubscribes for flows, so those are left out."),
]
METRIC_KEYS = [m[0] for m in METRICS]
PP_KINDS = {"rate"}                                   # a change in a rate is written in percentage points, everything else in percent
TILES = ["sends", "delivered", "open_rate", "click_rate", "orders", "revenue", "rev_per_delivered", "unsub_rate"]


class Source(Protocol):
    def bounds(self) -> tuple[dt.date | None, dt.date | None]: ...
    def rows(self, start: dt.date, end: dt.date, kind: str, channel: str, parent_id: str | None = None, message_id: str | None = None) -> list[dict]: ...
    def flows(self) -> dict[str, dict]: ...
    def campaigns(self) -> dict[str, dict]: ...
    def flow_messages(self, flow_id: str | None = None) -> dict[str, dict]: ...
    def content(self, message_ids: list[str]) -> dict[str, dict]: ...


# ------------------------------------------------------------------------------------------------------------------ the database

class DbSource:
    def __init__(self, conn):
        self.conn = conn

    def bounds(self):
        r = self.conn.execute("SELECT min(day) AS first, max(day) AS last FROM klaviyo_daily").fetchone()
        return r["first"], r["last"]

    def rows(self, start, end, kind, channel, parent_id=None, message_id=None):
        where, args = ["day BETWEEN %s AND %s"], [start, end]
        if kind in KIND_SOURCE:
            where.append("source = %s")
            args.append(KIND_SOURCE[kind])
        if channel != "all":
            where.append("channel = %s")
            args.append(channel)
        if parent_id is not None:
            where.append("parent_id = %s")
            args.append(parent_id)
        if message_id is not None:
            where.append("message_id = %s")
            args.append(message_id)
        sql = ("SELECT day, source, channel, parent_id, message_id, parent_name, sends, delivered, opens, unique_opens, clicks, unique_clicks, unsubscribes, orders, "
               "revenue FROM klaviyo_daily WHERE " + " AND ".join(where))
        return self.conn.execute(sql, args).fetchall()

    def flows(self):
        return {r["id"]: r for r in self.conn.execute("SELECT id, name, status FROM klaviyo_flows").fetchall()}

    def campaigns(self):
        return {r["id"]: r for r in self.conn.execute(
            "SELECT id, name, channel, status, subject, (send_time AT TIME ZONE 'America/Los_Angeles')::date AS send_date FROM klaviyo_campaigns").fetchall()}

    def flow_messages(self, flow_id=None):
        sql = "SELECT id, flow_id, name, channel, subject FROM klaviyo_flow_messages"
        rows = self.conn.execute(sql + (" WHERE flow_id = %s" if flow_id else ""), ([flow_id] if flow_id else None)).fetchall()
        return {r["id"]: r for r in rows}

    def content(self, message_ids):
        if not message_ids:
            return {}
        rows = self.conn.execute(
            "SELECT message_id, source, channel, parent_id, subject, preview_text, from_name, body_text, image_count, coalesce(cardinality(link_urls), 0) AS link_count, fetched_at "
            "FROM klaviyo_message_content WHERE message_id = ANY(%s)", (list(message_ids),)).fetchall()
        return {r["message_id"]: r for r in rows}


# ------------------------------------------------------------------------------------------------------------------ arithmetic

def zero() -> dict[str, float]:
    z: dict[str, float] = dict.fromkeys(FIELDS, 0)
    z.update(d_open=0, uc_open=0, d_unsub=0)
    return z


def add(acc: dict, r: dict) -> None:
    for f in FIELDS:
        acc[f] += float(r[f]) if f == "revenue" else int(r[f])
    if r["channel"] == "email":
        acc["d_open"] += int(r["delivered"])
        acc["uc_open"] += int(r["unique_clicks"])
    if not (r["source"] == "flow" and r["channel"] == "sms"):
        acc["d_unsub"] += int(r["delivered"])


def _div(a: float, b: float, nd: int | None = None):
    if not b:
        return None
    v = a / b
    return round(v, nd) if nd is not None else v


def metrics_of(acc: dict) -> dict[str, Any]:
    """Every metric of one set of sums. A rate is None (a dash), never 0, when its denominator is 0."""
    return {
        "sends": int(acc["sends"]), "delivered": int(acc["delivered"]), "opens": int(acc["unique_opens"]), "clicks": int(acc["unique_clicks"]),
        "open_rate": _div(acc["unique_opens"], acc["d_open"]),
        "click_rate": _div(acc["unique_clicks"], acc["delivered"]),
        "click_to_open": _div(acc["uc_open"], acc["unique_opens"]),
        "orders": int(acc["orders"]), "revenue": round(acc["revenue"], 2),
        "rev_per_delivered": _div(acc["revenue"], acc["delivered"]),
        "orders_per_delivered": _div(acc["orders"], acc["delivered"]),
        "unsub_rate": _div(acc["unsubscribes"], acc["d_unsub"]),
    }


def sum_rows(rows) -> dict:
    acc = zero()
    for r in rows:
        add(acc, r)
    return acc


def change_of(key: str, now: float | None, before: float | None) -> dict | None:
    """The change against the prior period: percent for counts and money, percentage points for rates (as Trends writes it). None when there is nothing to compare."""
    if now is None or before is None:
        return None
    kind = next(m[2] for m in METRICS if m[0] == key)
    if kind in PP_KINDS:
        return {"v": round(now - before, 6), "t": "pp"}
    if before == 0:
        return None
    return {"v": round(now / before - 1, 6), "t": "pct"}


def metric_defs() -> list[dict]:
    return [{"key": k, "label": lb, "kind": kd, "pol": pol, "help": hp} for k, lb, kd, pol, hp in METRICS]


# ------------------------------------------------------------------------------------------------------------------ parameters and windows

def parse_params(args) -> dict:
    window = (args.get("window") or DEFAULT_WINDOW).strip().lower()
    kind = (args.get("kind") or "all").strip().lower()
    channel = (args.get("channel") or "all").strip().lower()
    if window not in WINDOWS:
        raise q.ApiError(400, "window must be 7d, 30d, 90d or all")
    if kind not in KINDS:
        raise q.ApiError(400, "kind must be all, flows or campaigns")
    if channel not in CHANNELS:
        raise q.ApiError(400, "channel must be all, email or sms")
    return {"window": window, "kind": kind, "channel": channel}


def resolve_window(name: str, first: dt.date, through: dt.date) -> dict:
    n = WINDOWS[name]
    if n is None:
        return {"name": name, "start": first, "end": through, "days": (through - first).days + 1, "prior": None,
                "prior_why": "All loaded days have no earlier period to compare with."}
    start = max(first, through - dt.timedelta(days=n - 1))
    days = (through - start).days + 1
    pend = start - dt.timedelta(days=1)
    pstart = pend - dt.timedelta(days=days - 1)
    covered = days == n and pstart >= first
    return {"name": name, "start": start, "end": through, "days": days,
            "prior": {"start": pstart, "end": pend} if covered else None,
            "prior_why": None if covered else f"The {n} days before this window are not all loaded (data starts {first.isoformat()}), so there is no comparison."}


def _iso(d: dt.date | None) -> str | None:
    return d.isoformat() if d else None


def window_info(w: dict, first: dt.date, through: dt.date) -> dict:
    p = w["prior"]
    return {"name": w["name"], "from": _iso(w["start"]), "to": _iso(w["end"]), "days": w["days"], "through": _iso(through), "first_day": _iso(first),
            "prior": {"from": _iso(p["start"]), "to": _iso(p["end"])} if p else None, "prior_why": w["prior_why"]}


def _empty(p: dict) -> dict:
    return {"empty": True, "params": p, "window": None, "metrics": metric_defs(), "tiles": TILES,
            "note": "No Klaviyo data is loaded yet."}


def _flags(channel: str) -> dict:
    return {"sms_unsub_incomplete": channel != "email"}


# ------------------------------------------------------------------------------------------------------------------ summary

def summary(src: Source, p: dict) -> dict:
    first, through = src.bounds()
    if through is None:
        return _empty(p)
    w = resolve_window(p["window"], first, through)
    lo = w["prior"]["start"] if w["prior"] else w["start"]
    rows = src.rows(lo, through, p["kind"], p["channel"])
    now = metrics_of(sum_rows(r for r in rows if r["day"] >= w["start"]))
    before = metrics_of(sum_rows(r for r in rows if w["prior"] and w["prior"]["start"] <= r["day"] <= w["prior"]["end"])) if w["prior"] else None
    change = {k: change_of(k, now[k], before[k]) for k in METRIC_KEYS} if before else {}
    return {"empty": False, "params": p, "window": window_info(w, first, through), "metrics": metric_defs(), "tiles": TILES, "totals": now,
            "prior_totals": before, "change": change, "flags": _flags(p["channel"])}


# ------------------------------------------------------------------------------------------------------------------ over time

def buckets_for(grain: str, start: dt.date, end: dt.date) -> list[tuple[dt.date, dt.date]]:
    out, seen = [], set()
    for d in daterange(start, end):
        s, e = span_of(grain, d)
        if s not in seen:
            seen.add(s)
            out.append((s, e))
    return out


def timeseries(src: Source, p: dict, grain: str, split: bool) -> dict:
    if grain not in GRAINS:
        raise q.ApiError(400, "grain must be day, week or month")
    first, through = src.bounds()
    if through is None:
        return _empty(p) | {"grain": grain, "buckets": []}
    w = resolve_window(p["window"], first, through)
    bks = buckets_for(grain, w["start"], w["end"])
    rows = src.rows(max(first, bks[0][0]), through, p["kind"], p["channel"])
    by_start: dict[dt.date, list] = {s: [] for s, _ in bks}
    starts = [s for s, _ in bks]
    for r in rows:
        s = span_of(grain, r["day"])[0]
        if s in by_start:
            by_start[s].append(r)
    do_split = split and p["kind"] == "all"
    out = []
    for s, e in bks:
        cov_start, cov_end = max(s, first), min(e, through)
        label, sub = label_of(grain, s, e)
        partial = e > through or s < first
        b = {"start": _iso(s), "end": _iso(e), "label": label, "sub": sub, "partial": partial, "days": (e - s).days + 1, "days_cov": (cov_end - cov_start).days + 1,
             "partial_why": ("Runs past the last loaded day, so it is still filling in." if e > through else "Starts before the first loaded day, so it holds fewer days than a full period.") if partial else None,
             "v": metrics_of(sum_rows(by_start[s]))}
        if do_split:
            b["split"] = {k: metrics_of(sum_rows(r for r in by_start[s] if r["source"] == KIND_SOURCE[k])) for k in ("flows", "campaigns")}
        out.append(b)
    return {"empty": False, "params": p, "grain": grain, "window": window_info(w, first, through), "metrics": metric_defs(), "buckets": out, "split": do_split,
            "flags": _flags(p["channel"]), "starts": [_iso(x) for x in starts]}


# ------------------------------------------------------------------------------------------------------------------ flows and campaigns

def _group(rows, key):
    g: dict[Any, list] = {}
    for r in rows:
        g.setdefault(key(r), []).append(r)
    return g


def _cut(subject: str | None, n: int = 200) -> str | None:
    return subject if subject is None or len(subject) <= n else subject[:n].rstrip() + "..."


def flows(src: Source, p: dict) -> dict:
    first, through = src.bounds()
    if through is None:
        return _empty(p) | {"rows": [], "total": None}
    w = resolve_window(p["window"], first, through)
    rows = src.rows(w["start"], w["end"], "flows", p["channel"])
    dims = src.flows()
    total_rev = sum(float(r["revenue"]) for r in rows)
    out = []
    for fid, rs in _group(rows, lambda r: r["parent_id"]).items():
        m = metrics_of(sum_rows(rs))
        d = dims.get(fid) or {}
        out.append({"id": fid, "name": d.get("name") or max((r["parent_name"] or "" for r in rs), default="") or fid, "status": d.get("status"),
                    "messages": len({r["message_id"] for r in rs if r["message_id"]}), "channels": sorted({r["channel"] for r in rs}), "m": m,
                    "share": _div(m["revenue"], total_rev)})
    out.sort(key=lambda r: (-r["m"]["revenue"], r["name"].lower()))
    total = metrics_of(sum_rows(rows))
    return {"empty": False, "params": p, "window": window_info(w, first, through), "metrics": metric_defs(), "rows": out, "total": total,
            "flags": _flags(p["channel"])}


def flow_messages(src: Source, p: dict, flow_id: str) -> dict:
    first, through = src.bounds()
    if through is None:
        raise q.ApiError(404, "flow not found")
    w = resolve_window(p["window"], first, through)
    rows = src.rows(w["start"], w["end"], "flows", p["channel"], parent_id=flow_id)
    dims = src.flows().get(flow_id)
    if not rows and not dims:
        raise q.ApiError(404, "flow not found")
    mdims = src.flow_messages(flow_id)
    msgs = _group([r for r in rows if r["message_id"]], lambda r: r["message_id"])
    cont = src.content(list(msgs))
    out = []
    for mid, rs in msgs.items():
        c = cont.get(mid) or {}
        md = mdims.get(mid) or {}
        out.append({"id": mid, "name": md.get("name"), "channel": rs[0]["channel"], "subject": _cut(c.get("subject") or md.get("subject")),
                    "has_copy": bool(c.get("body_text")), "m": metrics_of(sum_rows(rs))})
    out.sort(key=lambda r: (-r["m"]["revenue"], r["id"]))
    name = (dims or {}).get("name") or max((r["parent_name"] or "" for r in rows), default="") or flow_id
    return {"empty": False, "params": p, "window": window_info(w, first, through), "metrics": metric_defs(), "flow": {"id": flow_id, "name": name, "status": (dims or {}).get("status")},
            "rows": out, "total": metrics_of(sum_rows(rows)), "flags": _flags(p["channel"])}


def campaigns(src: Source, p: dict, text: str | None = None) -> dict:
    first, through = src.bounds()
    if through is None:
        return _empty(p) | {"rows": [], "total": None}
    w = resolve_window(p["window"], first, through)
    rows = src.rows(w["start"], w["end"], "campaigns", p["channel"])
    dims = src.campaigns()
    groups = _group(rows, lambda r: r["parent_id"])
    # the copy of the message each campaign row opens: its largest message by delivered (an A/B campaign has several)
    top: dict[str, str | None] = {}
    for cid, rs in groups.items():
        per = {}
        for r in rs:
            if r["message_id"]:
                per[r["message_id"]] = per.get(r["message_id"], 0) + int(r["delivered"])
        top[cid] = max(per, key=lambda k: (per[k], k)) if per else None
    cont = src.content([m for m in top.values() if m])
    out = []
    for cid, rs in groups.items():
        d = dims.get(cid) or {}
        m = metrics_of(sum_rows(rs))
        send_days = [r["day"] for r in rs if int(r["sends"]) > 0]
        mid = top[cid]
        c = cont.get(mid) or {}
        send_date = d.get("send_date") or (min(send_days) if send_days else None)
        out.append({"id": cid, "name": d.get("name") or max((r["parent_name"] or "" for r in rs), default="") or cid, "channel": d.get("channel") or rs[0]["channel"],
                    "send_date": _iso(send_date), "subject": _cut(d.get("subject") or c.get("subject")), "message_id": mid,
                    "messages": len({r["message_id"] for r in rs if r["message_id"]}), "sent_in_window": bool(send_days), "m": m})
    if text:
        t = text.strip().lower()
        out = [r for r in out if t in (r["name"] or "").lower() or t in (r["subject"] or "").lower()]
    out.sort(key=lambda r: (r["send_date"] or "", r["name"]), reverse=True)
    total = metrics_of(sum_rows(r for r in rows if r["parent_id"] in {x["id"] for x in out}))
    return {"empty": False, "params": p, "window": window_info(w, first, through), "metrics": metric_defs(), "rows": out[:MAX_CAMPAIGNS], "total": total,
            "truncated": len(out) > MAX_CAMPAIGNS, "flags": _flags(p["channel"])}


# ------------------------------------------------------------------------------------------------------------------ one message

def message(src: Source, p: dict, message_id: str) -> dict:
    first, through = src.bounds()
    if through is None:
        raise q.ApiError(404, "message not found")
    w = resolve_window(p["window"], first, through)
    rows = src.rows(w["start"], w["end"], "all", "all", message_id=message_id)
    cont = src.content([message_id]).get(message_id)
    mdim = src.flow_messages().get(message_id)
    if not rows and not cont and not mdim:
        raise q.ApiError(404, "message not found")
    ref = rows[0] if rows else None
    source = ref["source"] if ref else (cont or {}).get("source") or ("flow" if mdim else "campaign")
    channel = ref["channel"] if ref else (cont or {}).get("channel") or (mdim or {}).get("channel") or "email"
    parent_id = ref["parent_id"] if ref else (cont or {}).get("parent_id") or (mdim or {}).get("flow_id")
    pname = None
    if rows:
        pname = max((r["parent_name"] or "" for r in rows), default="") or None
    if source == "flow":
        pname = (src.flows().get(parent_id) or {}).get("name") or pname
    else:
        pname = (src.campaigns().get(parent_id) or {}).get("name") or pname
    by_day = _group(rows, lambda r: r["day"])
    series = []
    for d in daterange(w["start"], w["end"]):
        m = metrics_of(sum_rows(by_day.get(d, [])))
        series.append({"day": d.isoformat(), "sends": m["sends"], "delivered": m["delivered"], "clicks": m["clicks"], "opens": m["opens"], "orders": m["orders"], "revenue": m["revenue"]})
    c = cont or {}
    body = c.get("body_text")
    return {"empty": False, "params": p, "window": window_info(w, first, through), "metrics": metric_defs(),
            "message": {"id": message_id, "source": source, "parent_id": parent_id, "parent_name": pname, "name": (mdim or {}).get("name"), "channel": channel,
                        "subject": c.get("subject") or (mdim or {}).get("subject"), "preview_text": c.get("preview_text"), "from_name": c.get("from_name"),
                        "body_text": body, "body_chars": len(body) if body else 0, "link_count": c.get("link_count"), "image_count": c.get("image_count"),
                        "copy_fetched_at": c["fetched_at"].isoformat() if c.get("fetched_at") else None, "has_copy": bool(body)},
            "totals": metrics_of(sum_rows(rows)), "series": series, "flags": _flags(channel)}
