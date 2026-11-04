"""Meta feeds that the Tag Explorer's ad_daily does not hold: account-level reach (day / ISO week / month grain), unique outbound clicks by product,
and the daily bid settings. Ported from meta_account_reach.py, product_daily_sync.py (meta half) and meta_bids_daily.py.

Auth: env META_ACCESS_TOKEN (already on the etl service; locally the Odylic Lens helper through etl.meta_api.get_token). Read-only Graph calls,
all through etl.meta_api.graph_get (backs off on rate limits including code 80004; never logs the token).
Spend, clicks, purchases and revenue by ad and day are NOT pulled here: they are the Tag Explorer's ad_daily rows (etl.run_daily).
"""
from __future__ import annotations

import calendar
import collections
import datetime as dt
import json
import re
from zoneinfo import ZoneInfo

from psycopg import sql

from etl import meta_api

from .common import AuthError, Ctx, FeedEmpty, FeedError, Span, days_between, round2, scrub
from .feed import Feed, Metric

G = meta_api.G
ACCOUNTS = meta_api.ACCOUNTS                       # [(act_id, name)] Main, BackUp
BID_ACCOUNTS = [("act_1671338033619223", "Lonvera Main", "America/Los_Angeles"), ("act_1543579384022317", "Lonvera BackUp", "America/New_York")]
REACH_FIELDS = ("spend,impressions,reach,frequency,inline_link_clicks,unique_inline_link_clicks,unique_outbound_clicks,actions,unique_actions,"
                "date_start,date_stop")
GRAINS = ("day", "week", "month")
TOL = 0.005                                         # day-sum vs week/month row reconciliation
# Meta serves unique clicks for a filtered set of ad sets only about 120 days back; earlier periods are not stored (the sheet's rule).
UOC_FLOOR = {"day": dt.date(2026, 7, 5), "week": dt.date(2026, 7, 6), "month": dt.date(2026, 7, 1)}
PRODUCT_RX = (
    ("VitaBull", re.compile(r"vita ?bull|vital ?bull|(?<![a-z])bull(?![a-z])")),
    ("Beef Liver", re.compile(r"beef ?liver|(?<![a-z])liver(?![a-z])")),
    ("Boneflex", re.compile(r"bone ?flex|(?<![a-z])bone(?![a-z])")),
    ("Other", re.compile(r"vital ?belle|vita ?belle|vitalbelle|vitabelle|vital ?flow|vitaflow|vitalflow")),
)


# ---------------------------------------------------------------------------------------------------------------- Graph wrapper

class Graph:
    def __init__(self, ctx: Ctx):
        self.ctx = ctx
        self.token = (ctx.env.get("META_ACCESS_TOKEN") or "").strip()
        if not self.token:
            try:
                self.token = meta_api.get_token()
            except meta_api.GraphError as e:
                raise FeedError(scrub(e)) from None

    def get(self, url: str, params: dict | None, what: str) -> dict:
        p = dict(params or {})
        p["access_token"] = self.token
        try:
            return meta_api.graph_get(url, p, what, sleep=self.ctx.sleep)
        except meta_api.TokenRejected as e:
            raise AuthError(scrub(e)) from None
        except meta_api.TooBig:
            raise
        except meta_api.GraphError as e:
            raise FeedError(scrub(e)) from None

    def pages(self, url: str, params: dict | None, what: str, pace: float = 0.3) -> list[dict]:
        rows: list[dict] = []
        p = params
        while url:
            j = self.get(url, p, what)
            rows += j.get("data", [])
            url, p = j.get("paging", {}).get("next"), None
            self.ctx.sleep(pace)
        return rows


def chunks(since: dt.date, until: dt.date, days: int):
    d = since
    while d <= until:
        e = min(d + dt.timedelta(days=days - 1), until)
        yield d, e
        d = e + dt.timedelta(days=1)


def period_key(grain: str, start: dt.date) -> str:
    if grain == "day":
        return start.isoformat()
    if grain == "week":
        y, w, _ = start.isocalendar()
        return f"{y}-W{w:02d}"
    return f"{start.year}-{start.month:02d}"


def full_end(grain: str, start: dt.date) -> dt.date:
    if grain == "day":
        return start
    if grain == "week":
        return start + dt.timedelta(days=6)
    return start.replace(day=calendar.monthrange(start.year, start.month)[1])


def is_complete(grain: str, start: dt.date, today: dt.date) -> bool:
    return full_end(grain, start) < today


def expected_periods(grain: str, since: dt.date, until: dt.date) -> list[tuple[dt.date, dt.date]]:
    out = []
    d = since
    while d <= until:
        e = min(full_end(grain, d), until) if grain != "day" else d
        out.append((d, e))
        d = (full_end(grain, d) if grain != "day" else d) + dt.timedelta(days=1)
    return out


# ---------------------------------------------------------------------------------------------------------------- account reach

def reach_windows(span: Span) -> dict[str, tuple[dt.date, dt.date]]:
    """Day window = the span; week and month windows start at the Monday / first of the month holding the span's first day (so every period that
    overlaps the day window is re-pulled whole). All end today (Pacific), the partial day included, as the sheet's tab does."""
    since, today = span.since, span.until
    return {"day": (since, today), "week": (since - dt.timedelta(days=since.weekday()), today), "month": (since.replace(day=1), today)}


def reach_span(ctx: Ctx, days: int) -> Span:
    return Span(ctx.today - dt.timedelta(days=days - 1), ctx.today)


def _stat(lst, key: str, default: float = 0.0) -> float:
    d = {x["action_type"]: float(x["value"]) for x in (lst or [])}
    return d.get(key, default)


def parse_reach_row(grain: str, account: str, a: dict) -> dict:
    impr, reach = float(a.get("impressions") or 0), float(a.get("reach") or 0)
    acts, uacts = a.get("actions"), a.get("unique_actions")
    clicks = _stat(acts, "link_click", float(a.get("inline_link_clicks") or 0)) if acts else float(a.get("inline_link_clicks") or 0)
    uclicks = _stat(uacts, "link_click", float(a.get("unique_inline_link_clicks") or 0)) if uacts else float(a.get("unique_inline_link_clicks") or 0)
    return {"grain": grain, "account": account, "start": dt.date.fromisoformat(a["date_start"]), "end": dt.date.fromisoformat(a["date_stop"]),
            "spend": float(a.get("spend") or 0), "impr": impr, "reach": reach, "uclicks": uclicks, "clicks": clicks,
            "uout": _stat(a.get("unique_outbound_clicks"), "outbound_click")}


def pull_reach_account(g: Graph, acct: str, name: str, grain: str, since: dt.date, until: dt.date) -> list[dict]:
    inc = {"day": 1, "week": 7, "month": "monthly"}[grain]
    out = []
    for c0, c1 in (chunks(since, until, 31) if grain == "day" else [(since, until)]):
        params = {"level": "account", "time_increment": inc, "fields": REACH_FIELDS, "limit": 500,
                  "time_range": json.dumps({"since": c0.isoformat(), "until": c1.isoformat()})}
        out += [parse_reach_row(grain, name, a) for a in g.pages(f"{G}/{acct}/insights", params, f"{name} {grain}")]
    return out


def check_alignment(rows: list[dict], today: dt.date) -> list[str]:
    bad = []
    for r in rows:
        gr, s, e = r["grain"], r["start"], r["end"]
        tag = f"{r['account']} {gr} {s}..{e}"
        if e > today:
            bad.append(f"{tag}: ends after today")
        if gr == "week" and (s.weekday() != 0 or (e - s).days > 6):
            bad.append(f"{tag}: week bucket not Monday-aligned")
        if gr == "month" and s.day != 1:
            bad.append(f"{tag}: month bucket not on the 1st")
    return bad


def reconcile(rows: list[dict], today: dt.date, day_since: dt.date, tol: float = TOL) -> list[str]:
    """Day-grain sums vs each complete week / month row (spend and impressions) per account. -> problems."""
    days: dict[str, dict[dt.date, tuple[float, float]]] = {}
    for r in rows:
        if r["grain"] == "day":
            days.setdefault(r["account"], {})[r["start"]] = (r["spend"], r["impr"])
    bad = []
    for r in rows:
        gr, s = r["grain"], r["start"]
        if gr == "day" or not is_complete(gr, s, today) or s < day_since:
            continue
        ds = di = 0.0
        for d in days_between(s, full_end(gr, s)):
            sp, im = days.get(r["account"], {}).get(d, (0.0, 0.0))
            ds += sp
            di += im

        def off(x: float, y: float) -> float:
            return abs(x - y) / y if y else (0.0 if not x else float("inf"))
        if off(ds, r["spend"]) > tol or off(di, r["impr"]) > tol:
            bad.append(f"{r['account']} {gr} {period_key(gr, s)}: day sums differ from the {gr} row beyond {tol:.1%}")
    return bad


def pull_meta_reach(ctx: Ctx, span: Span) -> list[tuple]:
    g = Graph(ctx)
    wins = reach_windows(span)
    rows: list[dict] = []
    for acct, name in ACCOUNTS:
        for grain in GRAINS:
            rows += pull_reach_account(g, acct, name, grain, *wins[grain])
    if not rows:
        raise FeedEmpty("meta reach: no rows returned")
    bad = check_alignment(rows, span.until)
    if bad:
        raise FeedError("meta reach: " + "; ".join(bad[:3]))
    for msg in reconcile(rows, span.until, span.since)[:3]:
        ctx.warn(msg)
    # Meta omits periods with no delivery: add zero rows so every period of the window exists (the sheet does the same)
    have = {(r["account"], r["grain"], r["start"]) for r in rows}
    for _acct, name in ACCOUNTS:
        for grain in GRAINS:
            for s, e in expected_periods(grain, *wins[grain]):
                if (name, grain, s) not in have:
                    rows.append({"grain": grain, "account": name, "start": s, "end": e, "spend": 0.0, "impr": 0.0, "reach": 0.0,
                                 "uclicks": 0.0, "clicks": 0.0, "uout": 0.0})
    agg: dict[tuple, list] = {}
    for r in rows:
        k = (r["grain"], period_key(r["grain"], r["start"]))
        cur = agg.setdefault(k, [r["start"], r["end"], 0.0, 0.0, 0.0, 0.0, 0.0])
        for i, f in enumerate(("impr", "reach", "uclicks", "clicks", "uout")):
            cur[2 + i] += r[f]
    return sorted((g_, k, v[0], v[1], *(round(x) for x in v[2:])) for (g_, k), v in agg.items())


def reach_delete_where(span: Span):
    w = reach_windows(span)
    return (sql.SQL("(grain = 'day' AND period_start >= %s) OR (grain = 'week' AND period_start >= %s) OR (grain = 'month' AND period_start >= %s)"),
            (w["day"][0], w["week"][0], w["month"][0]))


# ---------------------------------------------------------------------------------------------------------------- unique outbound clicks by product

def one_product(text: str | None) -> str | None:
    t = str(text or "").lower()
    hits = [p for p, rx in PRODUCT_RX if rx.search(t)]
    return hits[0] if len(hits) == 1 else None


def classify(campaign: str | None, adset: str | None) -> tuple[str, str]:
    for lvl, text in (("campaign", campaign), ("adset", adset)):
        p = one_product(text)
        if p:
            return p, lvl
    return "Other", "unresolved"


def unique_outbound(a: dict) -> float:
    d = {x["action_type"]: float(x["value"]) for x in (a.get("unique_outbound_clicks") or [])}
    return d.get("outbound_click", sum(d.values()))


def period_buckets(grain: str, since: dt.date, until: dt.date) -> list[tuple[dt.date, dt.date]]:
    out = []
    if grain == "week":
        d = since - dt.timedelta(days=since.weekday())
        while d <= until:
            out.append((d, min(d + dt.timedelta(days=6), until)))
            d += dt.timedelta(days=7)
    else:
        d = since.replace(day=1)
        while d <= until:
            nxt = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
            out.append((d, min(nxt - dt.timedelta(days=1), until)))
            d = nxt
    return out


def uoc_filtered(g: Graph, acct: str, ids: set[str], a: dt.date, b: dt.date, daily: bool, what: str) -> list[tuple[str, float]]:
    """Unique outbound clicks for a set of ad sets, de-duplicated by Meta across the whole set (account insights filtered to the ad set ids)."""
    p = {"level": "account", "limit": 500, "fields": "spend,unique_outbound_clicks,date_start",
         "time_range": json.dumps({"since": a.isoformat(), "until": b.isoformat()}),
         "filtering": json.dumps([{"field": "adset.id", "operator": "IN", "value": sorted(ids)}])}
    if daily:
        p["time_increment"] = 1
    return [(r["date_start"], unique_outbound(r)) for r in g.pages(f"{G}/{acct}/insights", p, what)]


def pull_product_reach(ctx: Ctx, span: Span) -> list[tuple]:
    g = Graph(ctx)
    since, until = span.since, span.until
    flt = json.dumps([{"field": "spend", "operator": "GREATER_THAN", "value": 0}])
    per: dict[tuple, float] = collections.defaultdict(float)
    for acct, name in ACCOUNTS:
        for a, b in chunks(since, until, 14):
            rows = g.pages(f"{G}/{acct}/insights", {"level": "adset", "time_increment": 1, "limit": 500, "filtering": flt,
                                                    "fields": "campaign_name,adset_name,adset_id,spend", "time_range": json.dumps({"since": a.isoformat(), "until": b.isoformat()})},
                           f"{name} adsets {a}")
            ids: dict[str, set] = collections.defaultdict(set)
            for r in rows:
                prod, _ = classify(r.get("campaign_name"), r.get("adset_name"))
                ids[prod].add(str(r["adset_id"]))
            for prod, s_ids in ids.items():
                if prod == "Other":
                    continue
                for ds, u in uoc_filtered(g, acct, s_ids, a, b, True, f"{name} uoc {prod} {a}"):
                    x = dt.date.fromisoformat(ds)
                    per[("day", x, x, prod)] += u
        for grain in ("week", "month"):
            for a, b in period_buckets(grain, since, until):
                rows = g.pages(f"{G}/{acct}/insights", {"level": "adset", "limit": 500, "filtering": flt, "fields": "campaign_name,adset_name,adset_id,spend",
                                                        "time_range": json.dumps({"since": a.isoformat(), "until": b.isoformat()})}, f"{name} {grain} {a}")
                ids = collections.defaultdict(set)
                for r in rows:
                    prod, _ = classify(r.get("campaign_name"), r.get("adset_name"))
                    ids[prod].add(str(r["adset_id"]))
                for prod, s_ids in ids.items():
                    if prod == "Other":
                        continue
                    for _ds, u in uoc_filtered(g, acct, s_ids, a, b, False, f"{name} {grain} uoc {prod} {a}"):
                        per[(grain, a, b, prod)] += u
    out = [(grain, period_key(grain, st), st, en, prod, round(u)) for (grain, st, en, prod), u in per.items() if st >= UOC_FLOOR[grain]]
    if not out:
        raise FeedEmpty("meta product reach: no rows returned")
    return sorted(out)


def product_reach_span(ctx: Ctx, days: int) -> Span:
    return Span(ctx.today - dt.timedelta(days=days - 1), ctx.today)


# ---------------------------------------------------------------------------------------------------------------- bid settings

STRAT_NORM = {"MINIMUM_ROAS": "LOWEST_COST_WITH_MIN_ROAS", "LOWEST_COST_BID_STRATEGY": "LOWEST_COST_WITHOUT_CAP", "BID_CAP": "LOWEST_COST_WITH_BID_CAP",
              "TARGET_COST": "TARGET_COST", "HIGHEST_VALUE": "LOWEST_COST_WITHOUT_CAP"}
CAP_STRATS = {"COST_CAP", "LOWEST_COST_WITH_BID_CAP"}
FLOOR_STRATS = {"LOWEST_COST_WITH_MIN_ROAS"}


def mk_state(strategy, amount_cents, floor):
    """(strategy, cap $ or None, floor or None): the amount is kept for cap strategies only, the floor for min-ROAS only."""
    s = None if strategy in (None, "") else STRAT_NORM.get(strategy, strategy)
    if s is None:
        return None
    cap = round(float(amount_cents) / 100.0, 2) if (s in CAP_STRATS and amount_cents not in (None, "")) else None
    fl = round(float(floor), 4) if (s in FLOOR_STRATS and floor not in (None, "")) else None
    return (s, cap, fl)


def parse_event(e: dict):
    if e.get("event_type") != "update_ad_set_bid_strategy":
        return None
    try:
        x = json.loads(e["extra_data"])
    except Exception:                                       # noqa: BLE001
        return None
    if x.get("type") not in (None, "bid_type"):
        return None
    av = x.get("additional_value") or {}
    fl = x.get("adset_minimum_return_on_ad_spend") or {}
    old = mk_state(x.get("old_value"), av.get("old_value"), fl.get("old_value"))
    new = mk_state(x.get("new_value"), av.get("new_value"), fl.get("new_value"))
    ts = dt.datetime.strptime(e["event_time"], "%Y-%m-%dT%H:%M:%S%z").astimezone(dt.timezone.utc)
    return ts, str(e["object_id"]), old, new


def build_timelines(events: list[dict]) -> dict:
    by: dict[str, list] = collections.defaultdict(list)
    seen = set()
    for e in events:
        p = parse_event(e)
        if p:
            if p in seen:
                continue
            seen.add(p)
            by[p[1]].append(p)
    out = {}
    for aid, evs in by.items():
        evs.sort(key=lambda t: t[0])
        out[aid] = {"init": evs[0][2], "tl": [(t, n, False) for t, _a, _o, n in evs]}
    return out


def state_at(tlr: dict, end_ts: dt.datetime):
    st = tlr["init"]
    for ts, new, _inf in tlr["tl"]:
        if ts < end_ts:
            st = new
        else:
            break
    return st


def end_of_day_utc(d: dt.date, tz: ZoneInfo) -> dt.datetime:
    return dt.datetime(d.year, d.month, d.day, tzinfo=tz).astimezone(dt.timezone.utc) + dt.timedelta(days=1)


def build_bid_rows(spend_rows, timelines, current, tzs, now_utc) -> list[tuple]:
    """spend_rows: [(date, account, adset_id, spend)] -> [(date, spend, capped, spend x cap, floor spend, spend x floor, strategy)]."""
    out = []
    for d, an, aid, sp in spend_rows:
        tlr = timelines.get(aid)
        if tlr:
            st = state_at(tlr, min(end_of_day_utc(d, tzs[an]), now_utc + dt.timedelta(seconds=1)))
        elif aid in current:
            st = current[aid]
        else:
            st = None
        strat, cap, fl = (st[0], st[1], st[2]) if st else ("UNKNOWN", None, None)
        out.append((d, round(sp, 2), round(sp if cap is not None else 0.0, 2), round(sp * cap, 2) if cap is not None else 0.0,
                    round(sp if fl is not None else 0.0, 2), round(sp * fl, 2) if fl is not None else 0.0, strat))
    return out


def pull_adset_spend(g: Graph, acct: str, name: str, since: dt.date, until: dt.date) -> list[tuple]:
    out = []
    for c0, c1 in chunks(since, until, 7):
        p = {"level": "adset", "time_increment": 1, "limit": 500, "fields": "adset_id,adset_name,campaign_id,spend,date_start",
             "time_range": json.dumps({"since": c0.isoformat(), "until": c1.isoformat()}),
             "filtering": json.dumps([{"field": "spend", "operator": "GREATER_THAN", "value": 0}])}
        try:
            got = g.pages(f"{G}/{acct}/insights", p, f"{name} spend")
        except meta_api.TooBig:
            got = []
            for d0, d1 in chunks(c0, c1, 1):
                p["time_range"] = json.dumps({"since": d0.isoformat(), "until": d1.isoformat()})
                got += g.pages(f"{G}/{acct}/insights", p, f"{name} spend 1d")
        out += [(dt.date.fromisoformat(a["date_start"]), name, str(a["adset_id"]), float(a["spend"])) for a in got]
    return out


def pull_bid_events(g: Graph, acct: str, name: str, since: dt.date, until: dt.date) -> list[dict]:
    out = []
    for c0, c1 in chunks(since, until + dt.timedelta(days=1), 2):
        p = {"limit": 500, "since": c0.isoformat(), "until": (c1 + dt.timedelta(days=1)).isoformat(), "category": "AD_SET",
             "fields": "event_time,event_type,object_id,object_name,extra_data,actor_name"}
        try:
            got = g.pages(f"{G}/{acct}/activities", p, f"{name} log")
        except FeedError:                                   # Meta intermittently errors on category=AD_SET for some days; the unfiltered feed works
            p.pop("category")
            got = g.pages(f"{G}/{acct}/activities", p, f"{name} log (all categories)")
        out += [e for e in got if e.get("event_type") == "update_ad_set_bid_strategy"]
    return out


def pull_bid_current(g: Graph, acct: str, name: str) -> tuple[dict, dict]:
    camps = {c["id"]: c.get("bid_strategy") for c in g.pages(f"{G}/{acct}/campaigns", {"limit": 500, "fields": "id,bid_strategy"}, f"{name} campaigns")}
    out, upd = {}, {}
    for a in g.pages(f"{G}/{acct}/adsets", {"limit": 500, "fields": "id,campaign_id,bid_strategy,bid_amount,bid_constraints,updated_time"}, f"{name} adsets"):
        upd[str(a["id"])] = a.get("updated_time")
        strat = a.get("bid_strategy") or camps.get(a.get("campaign_id"))
        fl = (a.get("bid_constraints") or {}).get("roas_average_floor")
        out[str(a["id"])] = mk_state(strat, a.get("bid_amount"), (float(fl) / 10000.0) if fl not in (None, "") else None)
    return out, upd


def pull_bids(ctx: Ctx, span: Span) -> list[tuple]:
    g = Graph(ctx)
    since, until = span.since, span.until
    tzs = {an: ZoneInfo(tz) for _a, an, tz in BID_ACCOUNTS}
    spend, events, current, updated = [], [], {}, {}
    for acct, name, _tz in BID_ACCOUNTS:
        spend += pull_adset_spend(g, acct, name, since - dt.timedelta(days=1), until)        # +1 day: New York account dates run a day ahead of Pacific at night
        cur, upd = pull_bid_current(g, acct, name)
        current.update(cur)
        updated.update(upd)
        events += pull_bid_events(g, acct, name, since - dt.timedelta(days=1), until)
    spend = [r for r in spend if since <= r[0] <= until + dt.timedelta(days=1)]
    if not spend:
        raise FeedEmpty("meta bids: no ad set spend rows returned")
    tl = build_timelines(events)
    now_utc = ctx.now_utc
    for aid, cur in current.items():                        # logged changes miss some bulk edits: add one inferred change at the ad set's updated_time
        t = tl.get(aid)
        if t and t["tl"][-1][1] != cur:
            last_ts = t["tl"][-1][0]
            u = updated.get(aid)
            ts = dt.datetime.strptime(u, "%Y-%m-%dT%H:%M:%S%z").astimezone(dt.timezone.utc) if u else now_utc
            t["tl"].append((max(ts, last_ts + dt.timedelta(seconds=1)), cur, True))
    rows = build_bid_rows(spend, tl, current, tzs, now_utc)
    unknown = sum(r[1] for r in rows if r[6] == "UNKNOWN")
    total = sum(r[1] for r in rows)
    if total and unknown / total > 0.02:
        ctx.warn(f"bids: {unknown / total:.1%} of spend is on ad sets with unknown settings")
    agg: dict[dt.date, list[float]] = {}
    for r in rows:
        v = agg.setdefault(r[0], [0.0] * 5)
        for i in range(5):
            v[i] += r[1 + i]
    return [(d, *(round2(x) for x in v)) for d, v in sorted(agg.items())]


def bids_span(ctx: Ctx, days: int) -> Span:
    return Span(ctx.today - dt.timedelta(days=days - 1), ctx.today)


# ---------------------------------------------------------------------------------------------------------------- feeds

FEEDS = [
    Feed("meta_reach", "Meta reach and unique outbound clicks", "meta", "proj_feed_meta_reach", "proj_feedx_meta_reach",
         ("grain", "period_key", "period_start", "period_end", "impressions", "reach", "unique_link_clicks", "link_clicks", "unique_outbound_clicks"),
         ("grain", "period_key"), "period_start", pull=pull_meta_reach, days=14, stale_after=2, span=reach_span, delete_where=reach_delete_where,
         metrics=(Metric("impressions", 5.0, TOL), Metric("reach", 5.0, TOL), Metric("unique_link_clicks", 5.0, TOL), Metric("link_clicks", 5.0, TOL),
                  Metric("unique_outbound_clicks", 5.0, TOL)),
         sheet_tab="Meta Account - Reach", sheet_parser=("etl.projection_pacing", "parse_meta_reach"),
         skip_compare=lambda row, today: row[3] >= today, fresh_where="grain = 'day'",
         notes="Whole periods are re-pulled (reach is not additive). Periods still open today are not compared."),
    Feed("product_reach", "Meta unique outbound clicks by product", "meta", "proj_feed_product_reach", "proj_feedx_product_reach",
         ("grain", "period_key", "period_start", "period_end", "product", "unique_outbound_clicks"), ("grain", "period_key", "product"), "period_start",
         pull=pull_product_reach, days=21, stale_after=2, span=product_reach_span,
         delete_where=lambda span: (sql.SQL("period_end >= %s"), (span.since,)),
         metrics=(Metric("unique_outbound_clicks", 5.0, TOL),), sheet_tab="Meta Product - Reach",
         sheet_parser=("etl.projection_pacing", "parse_product_reach"), skip_compare=lambda row, today: row[3] >= today, fresh_where="grain = 'day'",
         notes="Available from 2026-07-05 only (Meta serves filtered unique clicks about 120 days back)."),
    Feed("bids", "Meta bid caps and ROAS floors", "meta", "proj_feed_bids", "proj_feedx_bids",
         ("day", "spend", "capped_spend", "spend_x_cap", "floor_spend", "spend_x_floor"), ("day",), "day", pull=pull_bids, days=3, stale_after=2, span=bids_span,
         metrics=(Metric("spend", 1.0, 0.01), Metric("capped_spend", 1.0, 0.01), Metric("spend_x_cap", 5.0, 0.01), Metric("floor_spend", 1.0, 0.01),
                  Metric("spend_x_floor", 5.0, 0.01)), sheet_tab="Meta Bids - Daily", sheet_parser=("etl.projection_pacing", "parse_bids"),
         notes="Settings of past days are rebuilt from the ad account activity log, so history cannot be re-pulled; seed it once from the sheet."),
]
