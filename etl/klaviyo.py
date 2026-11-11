"""Klaviyo flow + campaign metrics -> klaviyo_daily (+ klaviyo_campaigns / klaviyo_flows / klaviyo_flow_messages). Read-only against Klaviyo (GET lists, POST metric-aggregates).

  python -m etl.klaviyo --days 120                  trailing window of complete Pacific days (through yesterday); replaces that window of klaviyo_daily, upserts the lists
  python -m etl.klaviyo --days 14                   the nightly call: re-pulls two weeks so late opens, clicks and orders land
  python -m etl.klaviyo --days 120 --dry-run        pull and print totals, write nothing
  python -m etl.klaviyo --days 120 --out DIR        also save the pulled rows as JSONL files in DIR

Key: env KLAVIYO_API_KEY (never printed or logged). Without it the job logs one line and exits 0 (so the nightly run carries on). For local runs the key is read from
the Lonvera sheets folder's .env when the env var is unset. Revenue / orders = Klaviyo-attributed "Placed Order - Upstackified" (S6xLbC) on the day the order happened.
Day buckets are America/Los_Angeles. Unique opens / clicks are per-day uniques: they are not additive across days. Flow SMS unsubscribes are not exposed by Klaviyo.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import time
from collections import defaultdict
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote
from zoneinfo import ZoneInfo

from . import db

PT = ZoneInfo("America/Los_Angeles")
BASE = "https://a.klaviyo.com/api"
REVISION = "2025-10-15"
CONV_METRIC = "S6xLbC"      # Placed Order - Upstackified (CheckoutChamp / Upstack server feed); Shopify's own "Placed Order" (TcvLJT) reads ~10% lower
M = dict(recvE="UK3Tdc", bounceE="XsYQzm", openE="SWBZGH", clickE="Xf85FG", unsubE="TS95yp", spamE="VEyX4S",
         recvS="X8tPbM", failS="UH2T4F", clickS="SUAZyc", unsubS="WvsLza")
DEFAULT_ENV_FILE = Path.home() / "Desktop" / "Google Sheets" / "lonvera_klaviyo" / ".env"
FIELDS = ["sends", "delivered", "bounced", "opens", "unique_opens", "clicks", "unique_clicks", "unsubscribes", "spam_complaints", "orders", "revenue"]
DAILY_COLS = ["day", "source", "parent_id", "message_id", "channel", "parent_name"] + FIELDS


def load_key() -> str | None:
    k = os.environ.get("KLAVIYO_API_KEY")
    if k:
        return k.strip()
    f = Path(os.environ.get("KLAVIYO_ENV_FILE") or DEFAULT_ENV_FILE)
    if f.exists():
        for line in f.read_text().splitlines():
            if line.startswith("KLAVIYO_API_KEY="):
                return line.split("=", 1)[1].strip() or None
    return None


class Client:
    """Minimal Klaviyo JSON:API client: retries 429 (Retry-After) and 5xx / network errors with backoff. Tests replace this class with a fake."""

    def __init__(self, key: str, sleep=time.sleep):
        self.key = key
        self.sleep = sleep

    def req(self, method: str, path: str, **kw) -> dict:
        import requests
        url = path if path.startswith("http") else BASE + path
        h = {"Authorization": "Klaviyo-API-Key " + self.key, "revision": REVISION, "accept": "application/vnd.api+json", "content-type": "application/vnd.api+json"}
        for i in range(8):
            try:
                r = requests.request(method, url, headers=h, timeout=120, **kw)
            except requests.exceptions.RequestException:
                self.sleep(2 ** min(i, 5))
                continue
            if r.status_code == 429:
                self.sleep(float(r.headers.get("Retry-After", 2 ** i)) + 0.5)
                continue
            if r.status_code >= 500:
                self.sleep(2 ** i)
                continue
            if r.status_code >= 400:
                raise RuntimeError(f"Klaviyo {r.status_code} {method} {path}: {db.scrub(r.text[:300])}")
            return r.json()
        raise RuntimeError(f"Klaviyo retries exhausted: {method} {path}")

    def paged(self, path: str, params=None, with_included=False):
        data, inc, url, p = [], [], path, params
        while url:
            d = self.req("GET", url, params=p)
            p = None
            data += d["data"]
            inc += d.get("included", [])
            url = (d.get("links") or {}).get("next")
        return (data, inc) if with_included else data


def utc(day: str) -> str:
    """Pacific midnight of YYYY-MM-DD as a naive UTC timestamp (the filter is read as UTC)."""
    return dt.datetime.fromisoformat(day).replace(tzinfo=PT).astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def agg(c, metric, by, measures, start, end) -> dict:
    """metric-aggregates, day interval, Pacific, fully paginated -> {(day, dims...): {measure: value}}; all-zero cells dropped."""
    out, cursor = {}, None
    while True:
        attrs = {"metric_id": metric, "measurements": measures, "interval": "day", "page_size": 500, "timezone": "America/Los_Angeles", "by": by,
                 "filter": [f"greater-or-equal(datetime,{utc(start)})", f"less-than(datetime,{utc(end)})"]}
        if cursor:
            attrs["page_cursor"] = cursor
        d = c.req("POST", "/metric-aggregates/", json={"data": {"type": "metric-aggregate", "attributes": attrs}})
        a = d["data"]["attributes"]
        dates = [dt.datetime.fromisoformat(x).astimezone(PT).strftime("%Y-%m-%d") for x in a["dates"]]
        for g in a["data"]:
            dims = tuple(g["dimensions"])
            for i, day in enumerate(dates):
                vals = {m: g["measurements"][m][i] for m in measures}
                if any(vals.values()):
                    out[(day,) + dims] = vals
        nxt = (d.get("links") or {}).get("next")
        if not nxt:
            return out
        m = re.search(r"page(?:%5B|\[)cursor(?:%5D|\])=([^&]+)|page_cursor=([^&]+)", nxt)
        cursor = unquote(m.group(1) or m.group(2)) if m else None
        if not cursor:
            raise RuntimeError("unparseable next link " + nxt)


def ts(v):
    return v or None


def pull_lists(c) -> tuple[list[dict], list[dict]]:
    """Campaigns (both channels, with their messages) and flows."""
    camps = []
    for ch in ("email", "sms"):
        data, inc = c.paged("/campaigns/", {"filter": f"equals(messages.channel,'{ch}')", "include": "campaign-messages", "page[size]": 50}, with_included=True)
        msgs = {m["id"]: m for m in inc if m["type"] == "campaign-message"}
        for x in data:
            a = x["attributes"]
            mids = [m["id"] for m in x["relationships"]["campaign-messages"]["data"]]
            defs = {i: ((msgs.get(i) or {}).get("attributes", {}).get("definition", {}) or {}) for i in mids}
            ml = [{"id": i, "label": ((msgs.get(i) or {}).get("attributes", {}).get("definition", {}) or {}).get("label"),
                   "subject": (((msgs.get(i) or {}).get("attributes", {}).get("definition", {}) or {}).get("content") or {}).get("subject")} for i in mids]
            subj = next((m["subject"] for m in ml if m["subject"]), None)
            camps.append(dict(id=x["id"], name=a.get("name"), channel=ch, status=a.get("status"), send_time=ts(a.get("send_time")), subject=subj,
                              created_at=ts(a.get("created_at")), updated_at=ts(a.get("updated_at")), raw={"attributes": a, "messages": ml}, _defs=defs))
    flows = [dict(id=x["id"], name=x["attributes"].get("name"), status=x["attributes"].get("status"), trigger_type=x["attributes"].get("trigger_type"),
                  created_at=ts(x["attributes"].get("created")), updated_at=ts(x["attributes"].get("updated")), raw=x["attributes"]) for x in c.paged("/flows/", {"page[size]": 50})]
    return camps, flows


def pull_flow_messages(c, flow_ids) -> list[dict]:
    out = []
    for fid in flow_ids:
        for act in c.paged(f"/flows/{fid}/flow-actions/"):
            for m in c.paged(f"/flow-actions/{act['id']}/flow-messages/"):
                d = m["attributes"].get("definition") or {}
                out.append(dict(id=m["id"], flow_id=fid, name=d.get("name"), channel=(m["attributes"].get("channel") or "").lower() or None,
                                subject=d.get("subject_line"), raw={"attributes": m["attributes"], "action_id": act["id"]}))
    return out


class _Text(HTMLParser):
    """HTML -> readable text: drops head / script / style, keeps link text, breaks lines at block tags; collects link targets and real (non-tracking) images."""
    BLOCK = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "section", "hr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.links, self.images, self.skip = [], [], 0, 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("script", "style", "head", "title"):
            self.skip += 1
        elif tag == "a" and (a.get("href") or "").startswith(("http://", "https://")):
            self.links.append(a["href"])
        elif tag == "img":
            w, h, src = a.get("width") or "", a.get("height") or "", a.get("src") or ""
            tiny = any(x.strip().rstrip("px").isdigit() and int(x.strip().rstrip("px")) <= 1 for x in (w, h))
            if not tiny and not re.search(r"track|pixel|open\.gif|/wf/open|/open/", src, re.I):
                self.images += 1
        if tag in self.BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head", "title") and self.skip:
            self.skip -= 1
        if tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def html_to_text(html: str | None) -> tuple[str, list[str], int]:
    """(readable text, distinct link urls in order, real image count)."""
    if not html:
        return "", [], 0
    p = _Text()
    p.feed(html)
    p.close()
    joined = re.sub(r"\{%.*?%\}", " ", "".join(p.out), flags=re.S)   # Klaviyo template tags ({% unsubscribe %}, {% if ... %}) are not copy
    lines = [re.sub(r"[ \t\r\f\v ​‌͏]+", " ", ln).strip() for ln in joined.split("\n")]
    return "\n".join(ln for ln in lines if ln), list(dict.fromkeys(p.links)), p.images


def content_row(source: str, parent_id: str, channel: str, mid: str, d: dict, tmpl: dict | None, err: str | None = None) -> dict:
    """One klaviyo_message_content row. d = the message definition (campaign message definition or flow message definition); tmpl = the template attributes (email) or None."""
    cont = d.get("content") or {}
    html = (tmpl or {}).get("html")
    if channel == "sms":
        body = cont.get("body") if "body" in cont else d.get("body")
        text, links, imgs = (body or "").strip(), list(dict.fromkeys(re.findall(r"https?://[^\s]+", body or ""))), 1 if (cont.get("media_url") or d.get("image_id")) else 0
        subject = preview = None
    else:
        text, links, imgs = html_to_text(html)
        subject, preview = cont.get("subject") or d.get("subject_line"), cont.get("preview_text") or d.get("preview_text")
    basis = html if channel == "email" else text
    raw = {k: v for k, v in d.items() if k not in ("body", "html")}
    if err:
        raw["fetch_error"] = err
    return dict(message_id=mid, source=source, parent_id=parent_id, channel=channel, subject=subject, preview_text=preview,
                from_name=cont.get("from_label") or d.get("from_label"), from_email=cont.get("from_email") or d.get("from_email"),
                template_id=(tmpl or {}).get("_id") or d.get("template_id"), editor_type=(tmpl or {}).get("editor_type"), html=html if channel == "email" else None,
                body_text=text or None, image_count=imgs, link_urls=links, content_hash=hashlib.sha256(basis.encode()).hexdigest() if basis else None, raw=raw)


def pull_content(c, camps, fmsgs, flows, daily_rows, extra_flow_ids=()) -> list[dict]:
    """Copy of every message that had activity in the pulled rows, plus every message of a live flow. Email = template html via /{campaign|flow}-messages/{id}/template/; SMS = the message body."""
    seen = {r["message_id"] for r in daily_rows if r["message_id"]}
    live = {f["id"] for f in flows if f["status"] == "live"}
    out, done = [], set()
    for x in camps:
        for mid, d in x["_defs"].items():
            if mid in seen and mid not in done:
                done.add(mid)
                out.append(_fetch("campaign", x["id"], x["channel"], mid, d, c))
    for m in fmsgs:
        if (m["id"] in seen or m["flow_id"] in live) and m["id"] not in done:
            done.add(m["id"])
            d = dict((m["raw"]["attributes"].get("definition") or {}))
            out.append(_fetch("flow", m["flow_id"], m["channel"] or "email", m["id"], d, c))
    return out


def _fetch(source, parent_id, channel, mid, d, c) -> dict:
    if channel == "sms":
        return content_row(source, parent_id, channel, mid, d, None)
    try:
        t = c.req("GET", f"/{source}-messages/{mid}/template/")
        data = t.get("data")
        if not data:
            return content_row(source, parent_id, channel, mid, d, None, "no template")
        return content_row(source, parent_id, channel, mid, d, {**data["attributes"], "_id": data["id"]})
    except RuntimeError as e:
        return content_row(source, parent_id, channel, mid, d, None, str(e)[:200])


def pull_daily(c, camps, flows, start: str, end: str) -> tuple[list[dict], dict]:
    """Rows for [start, end) Pacific. Returns (rows, notes). A message maps to its campaign through the campaign list; everything else keeps its raw ids."""
    fname = {f["id"]: f["name"] for f in flows}
    cname = {x["id"]: x["name"] for x in camps}
    camp_by_id = {x["id"]: x for x in camps}
    msg2camp = {m["id"]: x["id"] for x in camps for m in x["raw"]["messages"]}   # campaign message id -> campaign id
    name2cid = defaultdict(list)
    for x in camps:
        name2cid[x["name"]].append(x["id"])
    rows: dict = defaultdict(lambda: defaultdict(float))
    names: dict = {}

    def ent(flow, message, variation):
        """Flow events carry $flow + $message (the flow message id); campaign events carry $message = the CAMPAIGN id and $variation = the campaign message id."""
        if flow:
            names[("flow", flow)] = fname.get(flow, f"(unknown flow {flow})")
            return "flow", flow, message or ""
        cid = message if message in cname else msg2camp.get(variation) or msg2camp.get(message)
        if cid:
            names[("campaign", cid)] = cname[cid]
            only = [m["id"] for m in camp_by_id[cid]["raw"]["messages"]]
            return "campaign", cid, variation or (message if message in msg2camp else "") or (only[0] if len(only) == 1 else "")   # no variation on the event: a one-message campaign still has one message
        if message:
            names[("campaign", message)] = f"(unknown campaign {message})"
            return "campaign", message, variation or ""
        return None

    def add(day, ch, e, field, v):
        if e is not None and v:
            rows[(day, e[0], e[1], e[2], ch)][field] += v

    plan = {"email": [("recvE", [("count", "delivered"), ("count", "sends")]), ("bounceE", [("count", "sends"), ("count", "bounced")]),
                      ("openE", [("count", "opens"), ("unique", "unique_opens")]), ("clickE", [("count", "clicks"), ("unique", "unique_clicks")]),
                      ("unsubE", [("count", "unsubscribes")]), ("spamE", [("count", "spam_complaints")])],
            "sms": [("recvS", [("count", "delivered"), ("count", "sends")]), ("failS", [("count", "sends"), ("count", "bounced")]),
                    ("clickS", [("count", "clicks"), ("unique", "unique_clicks")])]}
    for ch, steps in plan.items():
        for mk, fields in steps:
            bot = mk in ("clickE", "clickS")   # drop Klaviyo-flagged bot clicks (link scanners), as Klaviyo's own reports do
            for k, v in agg(c, M[mk], ["$flow", "$message", "$variation"] + (["Bot Click"] if bot else []), ["count", "unique"], start, end).items():
                if bot and k[4] == "1.0":
                    continue
                for meas, f in fields:
                    add(k[0], ch, ent(k[1], k[2], k[3]), f, v[meas])
    lost = 0   # SMS unsubscribes are only exposed by campaign name (no message, no flow level)
    for k, v in agg(c, M["unsubS"], ["Campaign Name"], ["count"], start, end).items():
        ids = name2cid.get(k[1])
        if ids and len(ids) == 1:
            names[("campaign", ids[0])] = cname[ids[0]]
            add(k[0], "sms", ("campaign", ids[0], ""), "unsubscribes", v["count"])
        else:
            lost += v["count"]
    for k, v in agg(c, CONV_METRIC, ["$attributed_flow", "$attributed_message", "$attributed_variation", "$attributed_channel"], ["count", "sum_value"], start, end).items():
        e = ent(k[1], k[2], k[3])
        ch = "sms" if "sms" in (k[4] or "").lower() else "email"
        add(k[0], ch, e, "orders", v["count"])
        add(k[0], ch, e, "revenue", v["sum_value"])
    out = []
    for (day, src, pid, mid, ch), f in sorted(rows.items()):
        if any(f.values()):
            out.append(dict(day=day, source=src, parent_id=pid, message_id=mid, channel=ch, parent_name=names[(src, pid)],
                            **{x: (round(f.get(x, 0), 2) if x == "revenue" else int(round(f.get(x, 0)))) for x in FIELDS}))
    return out, {"sms_unsubs_unattributed": int(lost)}


def totals(rows) -> dict:
    t: dict = defaultdict(lambda: defaultdict(float))
    for r in rows:
        for x in ("sends", "delivered", "unique_opens", "unique_clicks", "orders", "revenue", "unsubscribes"):
            t[(r["source"], r["channel"])][x] += r[x]
    return t


def window(days: int, today: dt.date | None = None) -> tuple[str, str]:
    """(start inclusive, end exclusive) Pacific: the last `days` complete days, ending yesterday."""
    today = today or dt.datetime.now(PT).date()
    return (today - dt.timedelta(days=days)).isoformat(), today.isoformat()


CONTENT_COLS = ["message_id", "source", "parent_id", "channel", "subject", "preview_text", "from_name", "from_email", "template_id", "editor_type", "html", "body_text",
                "image_count", "link_urls", "content_hash"]


def write_content(conn, content) -> None:
    """Upsert; the latest copy wins and fetched_at moves only when the content hash changes (no edit history is kept)."""
    sets = ", ".join(f"{k}=EXCLUDED.{k}" for k in CONTENT_COLS[1:] + ["raw"])
    for x in content:
        conn.execute(f"""INSERT INTO klaviyo_message_content ({', '.join(CONTENT_COLS)}, raw) VALUES ({', '.join(['%s'] * (len(CONTENT_COLS) + 1))})
                         ON CONFLICT (message_id) DO UPDATE SET {sets},
                           fetched_at = CASE WHEN klaviyo_message_content.content_hash IS DISTINCT FROM EXCLUDED.content_hash THEN now() ELSE klaviyo_message_content.fetched_at END""",
                     tuple(x[k] for k in CONTENT_COLS) + (db.jsonb(x["raw"]),))


def write(conn, camps, flows, fmsgs, rows, start, end, content=()) -> None:
    with conn.transaction():
        for x in camps:
            conn.execute("""INSERT INTO klaviyo_campaigns (id, name, channel, status, send_time, subject, created_at, updated_at, raw) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                            ON CONFLICT (id) DO UPDATE SET name=EXCLUDED.name, channel=EXCLUDED.channel, status=EXCLUDED.status, send_time=EXCLUDED.send_time, subject=EXCLUDED.subject,
                              created_at=EXCLUDED.created_at, updated_at=EXCLUDED.updated_at, raw=EXCLUDED.raw, loaded_at=now()""",
                         (x["id"], x["name"], x["channel"], x["status"], x["send_time"], x["subject"], x["created_at"], x["updated_at"], db.jsonb(x["raw"])))
        for x in flows:
            conn.execute("""INSERT INTO klaviyo_flows (id, name, status, trigger_type, created_at, updated_at, raw) VALUES (%s,%s,%s,%s,%s,%s,%s)
                            ON CONFLICT (id) DO UPDATE SET name=EXCLUDED.name, status=EXCLUDED.status, trigger_type=EXCLUDED.trigger_type, created_at=EXCLUDED.created_at,
                              updated_at=EXCLUDED.updated_at, raw=EXCLUDED.raw, loaded_at=now()""",
                         (x["id"], x["name"], x["status"], x["trigger_type"], x["created_at"], x["updated_at"], db.jsonb(x["raw"])))
        for x in fmsgs:
            conn.execute("""INSERT INTO klaviyo_flow_messages (id, flow_id, name, channel, subject, raw) VALUES (%s,%s,%s,%s,%s,%s)
                            ON CONFLICT (id) DO UPDATE SET flow_id=EXCLUDED.flow_id, name=EXCLUDED.name, channel=EXCLUDED.channel, subject=EXCLUDED.subject, raw=EXCLUDED.raw, loaded_at=now()""",
                         (x["id"], x["flow_id"], x["name"], x["channel"], x["subject"], db.jsonb(x["raw"])))
        write_content(conn, content)
        conn.execute("DELETE FROM klaviyo_daily WHERE day >= %s AND day < %s", (start, end))   # replace the window, so rows Klaviyo no longer reports do not linger
        with conn.cursor() as cur:
            cur.executemany(f"INSERT INTO klaviyo_daily ({', '.join(DAILY_COLS)}) VALUES ({', '.join(['%s'] * len(DAILY_COLS))})", [tuple(r[k] for k in DAILY_COLS) for r in rows])


def run(c, days: int, dry_run=False, out_dir: str | None = None, conn=None, today: dt.date | None = None) -> dict:
    start, end = window(days, today)
    camps, flows = pull_lists(c)
    db.log(f"[klaviyo] lists: {len(camps)} campaigns, {len(flows)} flows; pulling daily metrics {start}..{end}")
    rows, notes = pull_daily(c, camps, flows, start, end)
    active_flows = sorted({r["parent_id"] for r in rows if r["source"] == "flow"} | {f["id"] for f in flows if f["status"] == "live"})
    fmsgs = pull_flow_messages(c, active_flows)
    db.log(f"[klaviyo] {len(rows)} daily rows, {len(fmsgs)} flow messages; fetching message copy")
    content = pull_content(c, camps, fmsgs, flows, rows)
    stats = {"window": f"{start}..{(dt.date.fromisoformat(end) - dt.timedelta(days=1)).isoformat()}", "campaigns": len(camps), "flows": len(flows), "flow_messages": len(fmsgs),
             "daily_rows": len(rows), "content_messages": len(content), "content_with_copy": sum(1 for x in content if x["body_text"]), **notes}
    for k, v in sorted(totals(rows).items()):
        db.log("  ", k, {n: round(x, 2) for n, x in v.items()})
    if out_dir:
        p = Path(out_dir)
        p.mkdir(parents=True, exist_ok=True)
        for name, data in (("klaviyo_daily", rows), ("klaviyo_campaigns", camps), ("klaviyo_flows", flows), ("klaviyo_flow_messages", fmsgs), ("klaviyo_message_content", content)):
            with open(p / f"{name}.jsonl", "w") as fh:
                for r in data:
                    fh.write(json.dumps({k: v for k, v in r.items() if not k.startswith('_')}, default=str) + "\n")
    if not dry_run:
        write(conn, camps, flows, fmsgs, rows, start, end, content)
    return stats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=120)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    key = load_key()
    if not key:
        db.log("[klaviyo] KLAVIYO_API_KEY is not set; skipping")
        return 0
    with db.JobRun("klaviyo") as run_:
        if a.dry_run:
            run_.stats = run(Client(key), a.days, True, a.out)
        else:
            with db.connect() as conn:
                run_.stats = run(Client(key), a.days, False, a.out, conn)
        run_.rows_written = 0 if a.dry_run else run_.stats["daily_rows"]
        run_.data_through = dt.date.fromisoformat(run_.stats["window"].split("..")[1])
        db.log(f"  {run_.stats}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
