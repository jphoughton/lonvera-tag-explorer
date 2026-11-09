"""Trends (/trends): the key marketing metrics by Day (DoD), Week (WoW) or Month (MoM), laid out in the bands of the Google Sheet 'Lonvera Projections'
(Pacing - Daily / Rollup - Weekly / Rollup - Monthly: see docs/TRENDS.md), each period with its change against the previous period.

One endpoint, one SQL round per source (a GROUP BY day over an indexed daily table), everything else is arithmetic in Python on a small per-day frame, so every
interval answers in well under a second uncached; there is no per-request recomputation of anything heavy. See docs/TRENDS.md for the definitions.

Rules (the same ones the rest of the app follows):
  * Days are Pacific days (the date as stored); weeks are ISO weeks Monday-Sunday (labelled W45 + date range); months are calendar months.
  * Every ratio is a ratio of the period's sums, never an average of daily ratios. Spend adds across platforms, purchases and revenue never do.
  * Total NC ROAS = actual new-customer revenue (upstack_orders_daily) / Meta + Google spend of the same days (app/platforms.py, docs/PLATFORMS.md).
  * NULL is not zero: a metric whose source does not cover EVERY day of the period shows a dash with the reason, never a zero or a partial sum.
  * Like for like: the open (partial) period is flagged 'so far' and compared with the SAME elapsed days of the previous period, never with the full one.
    Reach is not additive (Meta de-duplicates it), so it is only ever read from the feed's own day / week / month rows.
"""
from __future__ import annotations

import datetime as dt
import zoneinfo
from typing import Any, Callable

from . import platforms as pf, queries as q

PT = zoneinfo.ZoneInfo("America/Los_Angeles")
INTERVALS = ("day", "week", "month")
DEFAULT_PERIODS = {"day": 14, "week": 12, "month": 12}
MAX_PERIODS = {"day": 120, "week": 104, "month": 36}
QUERY_TIMEOUT_MS = 8000                      # a bad query answers a clear JSON error (503 'query timed out'), not a gateway hang
MAX_PLACEHOLDERS = 3                         # rows shown for periods that have not loaded yet (a long-stale feed must not fill the page with them)
STALE_AFTER_DAYS = 2                         # a block whose newest day is this far behind yesterday is flagged stale
FIRST_DAY = dt.date(2025, 1, 1)              # the earliest day any feed holds (the sheet-fed history); nothing earlier is asked for
PRODUCTS = (("vitabull", "VitaBull"), ("beefliver", "Beef Liver"), ("boneflex", "Boneflex"))
GOOGLE_UPSTACK_FIRST = dt.date(2025, 5, 13)  # fallback for app_meta google_upstack_first_day

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


# ---------------------------------------------------------------------------------------------------------------- dates

def pacific_today(now_utc: dt.datetime | None = None) -> dt.date:
    """Today's date on the Pacific wall clock (DST-aware: 2026-11-02 07:30 UTC is still Nov 1 in Los Angeles)."""
    now = now_utc or dt.datetime.now(dt.timezone.utc)
    return now.astimezone(PT).date()


def _d(x: dt.date, year: bool = False) -> str:
    return f"{MONTHS[x.month - 1]} {x.day}" + (f", {x.year}" if year else "")


def _range_text(a: dt.date, b: dt.date) -> str:
    if a == b:
        return _d(a)
    cross = a.year != b.year
    return f"{_d(a, cross)} – {_d(b, cross)}"


def monday(d: dt.date) -> dt.date:
    return d - dt.timedelta(days=d.weekday())


def month_start(d: dt.date) -> dt.date:
    return d.replace(day=1)


def month_end(d: dt.date) -> dt.date:
    nxt = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return nxt - dt.timedelta(days=1)


def prev_month_start(d: dt.date) -> dt.date:
    return month_start(month_start(d) - dt.timedelta(days=1))


def daterange(a: dt.date, b: dt.date):
    d = a
    while d <= b:
        yield d
        d += dt.timedelta(days=1)


# ---------------------------------------------------------------------------------------------------------------- periods

def span_of(interval: str, d: dt.date) -> tuple[dt.date, dt.date]:
    if interval == "day":
        return d, d
    if interval == "week":
        m = monday(d)
        return m, m + dt.timedelta(days=6)
    return month_start(d), month_end(d)


def previous_span(interval: str, start: dt.date) -> tuple[dt.date, dt.date]:
    if interval == "day":
        p = start - dt.timedelta(days=1)
        return p, p
    if interval == "week":
        return start - dt.timedelta(days=7), start - dt.timedelta(days=1)
    p = prev_month_start(start)
    return p, month_end(p)


def label_of(interval: str, start: dt.date, end: dt.date) -> tuple[str, str]:
    if interval == "day":
        return f"{WEEKDAYS[start.weekday()]} {_d(start)}", ""
    if interval == "week":
        return f"W{start.isocalendar()[1]:02d}", _range_text(start, end)
    return f"{MONTHS[start.month - 1]} {start.year}", _range_text(start, end)


def _like_text(interval: str, n: int) -> str:
    if interval == "week":
        return f"the same {n} weekday{'s' if n > 1 else ''} of last week ({WEEKDAYS[0]}" + (f"–{WEEKDAYS[n - 1]}" if n > 1 else "") + ")"
    return f"the same first {n} day{'s' if n > 1 else ''} of last month"


def build_periods(interval: str, today: dt.date, through: dt.date, n: int) -> list[dict]:
    """Newest first: the latest `n` periods that have loaded days, preceded by any open period newer than them that has none yet (a placeholder row).
    A period is OPEN when it ends after the last loaded day; its comparison is the same elapsed days of the previous period (like for like)."""
    out: list[dict] = []
    cur = span_of(interval, today)[0]
    real = 0
    placeholders: list[dict] = []
    while real < n:
        start, end = span_of(interval, cur)
        covered_end = min(end, through) if start <= through else None
        days_total = (end - start).days + 1
        days_cov = ((covered_end - start).days + 1) if covered_end else 0
        is_open = end > through
        p = {"start": start, "end": end, "covered_end": covered_end, "days": days_total, "days_cov": days_cov, "open": is_open,
             "state": "not_loaded" if days_cov == 0 else "open" if is_open else "closed"}
        ps, pe = previous_span(interval, start)
        cmp = {"kind": "previous", "start": ps, "end": pe, "days": (pe - ps).days + 1, "why": None, "text": None}
        if p["state"] == "not_loaded":
            cmp.update(kind="none", why="Nothing is loaded for this period yet (sources load once a day, after the Pacific day ends), so it cannot be compared yet.")
        elif is_open and interval == "day":
            cmp.update(kind="none", why="Today is not loaded yet.")
        elif is_open:
            if days_cov > cmp["days"]:
                cmp.update(kind="none", why=f"The previous period has only {cmp['days']} days, fewer than the {days_cov} elapsed here, so there is no like-for-like base.")
            else:
                cmp.update(kind="like_for_like", end=ps + dt.timedelta(days=days_cov - 1), days=days_cov, text=_like_text(interval, days_cov))
        p["compare"] = cmp
        p["label"], p["sub"] = label_of(interval, start, end)
        p["key"] = start.isoformat()
        if p["state"] == "not_loaded":
            if len(placeholders) < MAX_PLACEHOLDERS:
                placeholders.append(p)
        else:
            out.append(p)
            real += 1
        cur = start - dt.timedelta(days=1)
    rows = placeholders + out
    rows.sort(key=lambda p: p["start"], reverse=True)
    return rows


# ---------------------------------------------------------------------------------------------------------------- sources

SOURCE_LABELS = {
    "meta": "Meta spend and delivery", "meta_up": "Upstack attribution of Meta", "google": "Google", "google_up": "Upstack attribution of Google",
    "orders": "Upstack orders (new customers, revenue)", "prod_spend": "Meta spend by product", "prod_nc": "New customers by product", "reach": "Meta reach",
    "anyclick": "Upstack any-click new customers by stage",
}
SHORT = {"meta": "Meta", "google": "Google", "orders": "Orders", "meta_up": "Meta attribution", "google_up": "Google attribution", "prod_spend": "Product spend", "prod_nc": "Product customers", "reach": "Reach", "anyclick": "Stage customers"}
SOURCE_ORDER = ("meta", "google", "orders", "meta_up", "google_up", "prod_spend", "prod_nc", "anyclick", "reach")


class Cov:
    """First / last day a source holds, and (for sources that can have holes) the days it really has."""

    app_first: dt.date | None = None                 # Meta only: the first day ad_daily holds (earlier days come from the sheet-fed history)

    def __init__(self, first: dt.date | None, last: dt.date | None, present: set[dt.date] | None = None):
        self.first, self.last, self.present = first, last, present

    def why_not(self, label: str, a: dt.date, b: dt.date) -> str | None:
        """None when the source covers every day of [a, b]; otherwise the reason."""
        if self.first is None or self.last is None:
            return f"{label} is not loaded."
        if b < self.first:
            return f"{label} starts {_d(self.first, True)}."
        if a < self.first:
            return f"{label} starts {_d(self.first, True)}; this period begins earlier."
        if b > self.last:
            return f"{label} is loaded through {_d(self.last, True)}."
        if self.present is not None:
            for d in daterange(a, b):
                if d not in self.present:
                    return f"{label} has no data for {_d(d, True)}."
        return None


def coverage(conn, today: dt.date) -> dict[str, Cov]:
    """first / last day of every source (one round trip). Meta = the app's own ad_daily plus the sheet-fed history before it; Upstack orders absent days are real zeros."""
    rows = conn.execute("""SELECT 'ad_daily' AS k, min(day) AS lo, max(day) AS hi FROM ad_daily
        UNION ALL SELECT 'meta_sheet', min(day), max(day) FROM proj_feed_meta_day WHERE source = 'sheet'
        UNION ALL SELECT 'meta_up', min(day), max(day) FROM ad_daily_upstack
        UNION ALL SELECT 'google', min(day), max(day) FROM google_daily
        UNION ALL SELECT 'orders', min(day), max(day) FROM upstack_orders_daily
        UNION ALL SELECT 'prod_spend', min(day), max(day) FROM proj_feed_meta_product
        UNION ALL SELECT 'prod_nc', min(day), max(day) FROM proj_feed_nc_product
        UNION ALL SELECT 'anyclick', min(day), max(day) FROM proj_feed_anyclick
        UNION ALL SELECT 'reach', min(period_start), max(period_end) FROM proj_feed_meta_reach WHERE grain = 'day'""").fetchall()
    mm = {r["k"]: (r["lo"], r["hi"]) for r in rows}
    yday = today - dt.timedelta(days=1)
    cov: dict[str, Cov] = {}
    a_lo, a_hi = mm["ad_daily"]
    s_lo = mm["meta_sheet"][0]
    cov["meta"] = Cov(min(x for x in (a_lo, s_lo) if x) if (a_lo or s_lo) else None, a_hi)
    cov["meta"].app_first = a_lo
    cov["meta_up"] = Cov(*mm["meta_up"])
    cov["google"] = Cov(*mm["google"])
    gu = conn.execute("SELECT value FROM app_meta WHERE key = 'google_upstack_first_day'").fetchone()
    try:
        gfirst = dt.date.fromisoformat(gu["value"]) if gu else GOOGLE_UPSTACK_FIRST
    except (TypeError, ValueError):
        gfirst = GOOGLE_UPSTACK_FIRST
    glo, ghi = mm["google"]
    cov["google_up"] = Cov(max(gfirst, glo) if glo else None, ghi)
    cov["orders"] = Cov(*mm["orders"])
    cov["prod_spend"] = Cov(*mm["prod_spend"])
    lo, hi = mm["prod_nc"]
    cov["prod_nc"] = Cov(lo, min(hi, yday) if hi else None)               # today's row, if any, is partial: never read
    cov["anyclick"] = Cov(*mm["anyclick"])
    lo, hi = mm["reach"]
    cov["reach"] = Cov(lo, min(hi, yday) if hi else None)
    return cov


def through_of(cov: dict[str, Cov], today: dt.date) -> dt.date:
    """The last loaded day: the newest of the three core feeds, never today (a day is loaded after it ends)."""
    lasts = [cov[k].last for k in ("meta", "google", "orders") if cov[k].last]
    if not lasts:
        raise q.NotLoaded()
    return min(max(lasts), today - dt.timedelta(days=1))


# ---------------------------------------------------------------------------------------------------------------- the day frame

SUM_KEYS = ("ms", "mi", "mc", "mp_own", "mr_own", "mp_nc", "mr_nc", "mp_all", "mr_all", "gs", "gi", "gc", "gp_own", "gr_own", "gp_nc", "gr_nc", "gp_all", "gr_all",
            "onc_rev", "onc_ord", "orc", "otot", "ts", "ss", "tnc", "snc") + tuple(f"{k}_{s}" for k, _ in PRODUCTS for s in ("spend", "ncrev", "ncord"))


def _f(x: Any) -> float:
    return float(x) if x is not None else 0.0


def fetch_frame(conn, lo: dt.date, hi: dt.date, cov: dict[str, Cov]) -> tuple[dict[dt.date, dict[str, float]], dict[str, set[dt.date]]]:
    """Per-day sums for [lo, hi] from every source, one grouped query each. Returns ({day: {key: value}}, {source: days that have rows})."""
    fr: dict[dt.date, dict[str, float]] = {}
    present: dict[str, set[dt.date]] = {k: set() for k in ("meta", "prod_spend")}

    def put(day: dt.date, **kv: float) -> None:
        row = fr.setdefault(day, {})
        for k, v in kv.items():
            row[k] = row.get(k, 0.0) + v

    app_first = getattr(cov["meta"], "app_first", None)
    # Meta: the app's ad_daily (every page of the app reads it) ...
    for r in conn.execute("""SELECT a.day, sum(a.spend) AS s, sum(a.impressions) AS i, sum(a.link_clicks) AS c, sum(a.purchases) AS p, sum(a.conv_value) AS v,
                                    sum(a.spend) FILTER (WHERE c.stage = 'testing') AS ts, sum(a.spend) FILTER (WHERE c.stage = 'scaling') AS ss
                             FROM ad_daily a LEFT JOIN meta_campaigns c USING (campaign_id) WHERE a.day BETWEEN %s AND %s GROUP BY a.day""", (lo, hi)):
        present["meta"].add(r["day"])
        put(r["day"], ms=_f(r["s"]), mi=_f(r["i"]), mc=_f(r["c"]), mp_own=_f(r["p"]), mr_own=_f(r["v"]), ts=_f(r["ts"]), ss=_f(r["ss"]))
    # ... plus, for days before ad_daily begins, the sheet-fed campaign history (the only Meta data there is; campaign grain)
    if app_first is None or lo < app_first:
        top = (app_first - dt.timedelta(days=1)) if app_first else hi
        for r in conn.execute("""SELECT day, sum(spend) AS s, sum(impressions) AS i, sum(link_clicks) AS c, sum(purchases) AS p, sum(purchase_value) AS v,
                                        sum(spend) FILTER (WHERE stage = 'Testing') AS ts, sum(spend) FILTER (WHERE stage = 'Scaling') AS ss
                                 FROM proj_feed_meta_day WHERE source = 'sheet' AND day BETWEEN %s AND %s GROUP BY day""", (lo, min(hi, top))):
            present["meta"].add(r["day"])
            put(r["day"], ms=_f(r["s"]), mi=_f(r["i"]), mc=_f(r["c"]), mp_own=_f(r["p"]), mr_own=_f(r["v"]), ts=_f(r["ts"]), ss=_f(r["ss"]))
    for r in conn.execute("""SELECT day, sum(nc_purchases) AS np, sum(nc_conv_value) AS nv, sum(purchases) AS ap, sum(conv_value) AS av
                             FROM ad_daily_upstack WHERE day BETWEEN %s AND %s GROUP BY day""", (lo, hi)):
        put(r["day"], mp_nc=_f(r["np"]), mr_nc=_f(r["nv"]), mp_all=_f(r["ap"]), mr_all=_f(r["av"]))
    for r in conn.execute("""SELECT day, sum(spend) AS s, sum(impressions) AS i, sum(clicks) AS c, sum(purchases) AS p, sum(purchases_value) AS v, sum(nc) AS n, sum(nc_rev) AS nv,
                                    sum(orders) AS o, sum(rev) AS ov FROM google_daily WHERE day BETWEEN %s AND %s GROUP BY day""", (lo, hi)):
        put(r["day"], gs=_f(r["s"]), gi=_f(r["i"]), gc=_f(r["c"]), gp_own=_f(r["p"]), gr_own=_f(r["v"]), gp_nc=_f(r["n"]), gr_nc=_f(r["nv"]), gp_all=_f(r["o"]), gr_all=_f(r["ov"]))
    for r in conn.execute("SELECT day, nc_rev, nc_orders, rc_rev, total_rev FROM upstack_orders_daily WHERE day BETWEEN %s AND %s", (lo, hi)):
        put(r["day"], onc_rev=_f(r["nc_rev"]), onc_ord=_f(r["nc_orders"]), orc=_f(r["rc_rev"]), otot=_f(r["total_rev"]))
    # product feeds: a day is read from 'app' when it has app rows, else from 'sheet' (the same rule as analytics.meta_campaign_daily_history)
    slug = {name: key for key, name in PRODUCTS}
    for r in conn.execute("""SELECT h.day, h.product, sum(h.spend) AS s FROM proj_feed_meta_product h WHERE h.day BETWEEN %s AND %s
                             AND (h.source = 'app' OR NOT EXISTS (SELECT 1 FROM proj_feed_meta_product a WHERE a.day = h.day AND a.source = 'app'))
                             GROUP BY 1, 2""", (lo, hi)):
        present["prod_spend"].add(r["day"])
        if r["product"] in slug:
            put(r["day"], **{f"{slug[r['product']]}_spend": _f(r["s"])})
    for r in conn.execute("SELECT day, stage, nc FROM proj_feed_anyclick WHERE day BETWEEN %s AND %s", (lo, hi)):
        put(r["day"], **{("tnc" if r["stage"] == "Testing" else "snc"): _f(r["nc"])})
    for r in conn.execute("SELECT day, product, nc_rev, nc_orders FROM proj_feed_nc_product WHERE day BETWEEN %s AND %s", (lo, hi)):
        if r["product"] in slug:
            put(r["day"], **{f"{slug[r['product']]}_ncrev": _f(r["nc_rev"]), f"{slug[r['product']]}_ncord": _f(r["nc_orders"])})
    return fr, present


def sums(fr: dict[dt.date, dict[str, float]], a: dt.date, b: dt.date) -> dict[str, float]:
    out = dict.fromkeys(SUM_KEYS, 0.0)
    for d in daterange(a, b):
        row = fr.get(d)
        if row:
            for k, v in row.items():
                out[k] += v
    return out


# ---------------------------------------------------------------------------------------------------------------- metrics

def _div(a: float, b: float, mult: float = 1.0) -> float | None:
    return (a / b * mult) if b else None


BASIS_NAME = {"own": "own reporting", "nc": "Upstack new customers", "all": "Upstack all orders"}
# kind -> change type: 'rate' metrics change in percentage points, everything else in percent
KINDS = {"money": "pct", "money2": "pct", "int": "pct", "roas": "pct", "dec": "pct", "rate": "pp"}
UP, DOWN, NEUTRAL = "up_good", "down_good", "neutral"


def _purch_keys(platform_prefix: str, numbers: str) -> tuple[str, str]:
    return f"{platform_prefix}p_{'own' if numbers == 'own' else numbers}", f"{platform_prefix}r_{'own' if numbers == 'own' else numbers}"


def build_catalog(numbers: str) -> list[dict]:
    """Every metric: key, label, group, kind, polarity, the sources it needs, its help text and its formula over the period sums S (a callable, not serialised)."""
    M: list[dict] = []

    def add(key, label, group, kind, pol, needs, fn, help=""):
        M.append({"key": key, "label": label, "group": group, "kind": kind, "pol": pol, "needs": tuple(needs), "fn": fn, "help": help})

    add("total_spend", "Total spend", "totals", "money", NEUTRAL, ("meta", "google"), lambda S: S["ms"] + S["gs"], "Meta + Google spend.")
    add("meta_spend", "Meta spend", "totals", "money", NEUTRAL, ("meta",), lambda S: S["ms"], "Meta spend (both ad accounts).")
    add("google_spend", "Google spend", "totals", "money", NEUTRAL, ("google",), lambda S: S["gs"], "Google spend.")
    add("nc_rev", "NC revenue", "totals", "money", UP, ("orders",), lambda S: S["onc_rev"], "Actual first-order revenue (Upstack orders feed; tax included, before refunds).")
    add("nc_roas", "NC ROAS", "totals", "roas", UP, ("orders", "meta", "google"), lambda S: _div(S["onc_rev"], S["ms"] + S["gs"]), "Actual NC revenue / total spend, as a ratio of the period's sums.")
    add("nc_orders", "New customers", "totals", "int", UP, ("orders",), lambda S: S["onc_ord"], "Actual new customers (first orders), Upstack orders feed.")
    add("nc_cpa", "NC CPA", "totals", "money2", DOWN, ("orders", "meta", "google"), lambda S: _div(S["ms"] + S["gs"], S["onc_ord"]), "Total spend / actual new customers.")
    add("nc_aov", "NC AOV", "totals", "money2", UP, ("orders",), lambda S: _div(S["onc_rev"], S["onc_ord"]), "Actual NC revenue / actual new customers.")
    add("nc_cvr", "NC conversion", "totals", "rate", UP, ("orders", "meta", "google"), lambda S: _div(S["onc_ord"], S["mc"] + S["gc"]), "Actual new customers / (Meta link clicks + Google clicks).")
    add("rc_rev", "Returning revenue", "totals", "money", UP, ("orders",), lambda S: S["orc"], "Repeat-order revenue (Upstack orders feed).")
    add("total_rev", "Total revenue", "totals", "money", UP, ("orders",), lambda S: S["otot"], "All order revenue: new + returning (Upstack orders feed; no Amazon).")
    add("repeat_share", "Repeat share of revenue", "totals", "rate", NEUTRAL, ("orders",), lambda S: _div(S["orc"], S["otot"]), "Returning revenue / total revenue.")
    add("mer", "MER", "totals", "roas", UP, ("orders", "meta", "google"), lambda S: _div(S["otot"], S["ms"] + S["gs"]), "Total revenue / total spend (orders feed: no Amazon revenue, no Amazon or TikTok spend).")

    # Meta
    pk, rk = _purch_keys("m", numbers)
    nm = {"own": ("purchases", "revenue", "ROAS", "cost per purchase"), "nc": ("new customers", "NC revenue", "NC ROAS", "NC CPA"),
          "all": ("orders", "revenue", "ROAS", "cost per order")}[numbers]
    suf = "" if numbers == "own" else " (Upstack)"
    mneeds = ("meta",) if numbers == "own" else ("meta", "meta_up")
    add("m_impr", "Meta impressions", "meta", "int", NEUTRAL, ("meta",), lambda S: S["mi"], "Meta impressions.")
    add("m_clicks", "Meta link clicks", "meta", "int", UP, ("meta",), lambda S: S["mc"], "Meta link clicks.")
    add("m_cpm", "Meta CPM", "meta", "money2", DOWN, ("meta",), lambda S: _div(S["ms"], S["mi"], 1000), "Spend / impressions x 1,000.")
    add("m_ctr", "Meta CTR", "meta", "rate", UP, ("meta",), lambda S: _div(S["mc"], S["mi"]), "Link clicks / impressions.")
    add("m_cpc", "Meta CPC", "meta", "money2", DOWN, ("meta",), lambda S: _div(S["ms"], S["mc"]), "Spend / link clicks.")
    add("m_purch", f"Meta {nm[0]}{suf}", "meta", "int", UP, mneeds, lambda S, pk=pk: S[pk], f"Basis: {BASIS_NAME[numbers]}.")
    add("m_rev", f"Meta {nm[1]}{suf}", "meta", "money", UP, mneeds, lambda S, rk=rk: S[rk], f"Basis: {BASIS_NAME[numbers]}.")
    add("m_cvr", "Meta conversion", "meta", "rate", UP, ("meta",), lambda S: _div(S["mp_own"], S["mc"]), "Meta-reported purchases / link clicks (Meta's own count).")
    add("m_roas", f"Meta {nm[2]}{suf}", "meta", "roas", UP, mneeds, lambda S, rk=rk: _div(S[rk], S["ms"]), f"Revenue / Meta spend. Basis: {BASIS_NAME[numbers]}.")
    add("m_cpp", f"Meta {nm[3]}{suf}", "meta", "money2", DOWN, mneeds, lambda S, pk=pk: _div(S["ms"], S[pk]), f"Meta spend / {nm[0]}. Basis: {BASIS_NAME[numbers]}.")
    # Meta reach (read from the reach feed's own rows, never summed)
    for key, label, kind, pol, h in (("reach", "Meta reach", "int", NEUTRAL, "People reached, de-duplicated by Meta for exactly this period (the feed's own day / week / month row)."),
                                      ("frequency", "Meta frequency", "dec", NEUTRAL, "Impressions / reach of the same feed row."),
                                      ("first_imp", "First-time impression %", "rate", NEUTRAL, "Reach / impressions of the same feed row (the share of impressions that reached someone new)."),
                                      ("cost_1k", "Cost / 1,000 reached", "money2", DOWN, "Meta spend / reach x 1,000. Closed periods only (the open period's reach runs a day past its spend)."),
                                      ("uoc", "Unique outbound clicks", "int", UP, "Meta unique outbound clicks, de-duplicated per period (feed row)."),
                                      ("uoc_ctr", "Unique outbound CTR", "rate", UP, "Unique outbound clicks / impressions of the same feed row.")):
        add(key, label, "meta", kind, pol, ("reach",), None, h)

    # Google
    pk, rk = _purch_keys("g", numbers)
    gnm = {"own": ("purchases", "revenue", "ROAS", "cost per purchase"), "nc": ("new customers", "NC revenue", "NC ROAS", "NC CPA"), "all": ("orders", "revenue", "ROAS", "cost per order")}[numbers]
    gneeds = ("google",) if numbers == "own" else ("google", "google_up")
    add("g_impr", "Google impressions", "google", "int", NEUTRAL, ("google",), lambda S: S["gi"], "Google impressions.")
    add("g_clicks", "Google clicks", "google", "int", UP, ("google",), lambda S: S["gc"], "Google clicks.")
    add("g_ctr", "Google CTR", "google", "rate", UP, ("google",), lambda S: _div(S["gc"], S["gi"]), "Clicks / impressions.")
    add("g_cpc", "Google CPC", "google", "money2", DOWN, ("google",), lambda S: _div(S["gs"], S["gc"]), "Spend / clicks.")
    add("g_purch", f"Google {gnm[0]}{suf}", "google", "int", UP, gneeds, lambda S, pk=pk: S[pk], f"Basis: {BASIS_NAME[numbers]}. Never added to Meta's: both credit the same orders.")
    add("g_rev", f"Google {gnm[1]}{suf}", "google", "money", UP, gneeds, lambda S, rk=rk: S[rk], f"Basis: {BASIS_NAME[numbers]}.")
    add("g_cvr", "Google conversion", "google", "rate", UP, ("google",), lambda S: _div(S["gp_own"], S["gc"]), "Google-reported purchases / clicks (Google's own count).")
    add("g_roas", f"Google {gnm[2]}{suf}", "google", "roas", UP, gneeds, lambda S, rk=rk: _div(S[rk], S["gs"]), f"Revenue / Google spend. Basis: {BASIS_NAME[numbers]}.")
    add("g_cpp", f"Google {gnm[3]}{suf}", "google", "money2", DOWN, gneeds, lambda S, pk=pk: _div(S["gs"], S[pk]), f"Google spend / {gnm[0]}. Basis: {BASIS_NAME[numbers]}.")

    # per product
    for slug, name in PRODUCTS:
        sp, nr, no = f"{slug}_spend", f"{slug}_ncrev", f"{slug}_ncord"
        add(f"p_{slug}_spend", f"{name} Meta spend", slug, "money", NEUTRAL, ("prod_spend",), lambda S, sp=sp: S[sp], "Meta spend of the product, classified campaign name > ad set name > ad name (the sheet's rule).")
        add(f"p_{slug}_nc_rev", f"{name} NC revenue", slug, "money", UP, ("prod_nc",), lambda S, nr=nr: S[nr], "Actual first-order revenue split across product lines (feed nc_product).")
        add(f"p_{slug}_nc_roas", f"{name} NC ROAS", slug, "roas", UP, ("prod_nc", "prod_spend"), lambda S, nr=nr, sp=sp: _div(S[nr], S[sp]), "Product NC revenue / product Meta spend.")
        add(f"p_{slug}_nc_cpa", f"{name} NC CPA", slug, "money2", DOWN, ("prod_nc", "prod_spend"), lambda S, no=no, sp=sp: _div(S[sp], S[no]), "Product Meta spend / product new customers (orders whose largest product line is this product).")
        add(f"p_{slug}_nc_orders", f"{name} NC orders", slug, "int", UP, ("prod_nc",), lambda S, no=no: S[no], "Actual new customers whose first order was mostly this product (feed nc_product).")

    # the product bands' Total columns divide by Meta spend alone, exactly as the sheet does (the DTC band's NC ROAS / NC CPA use Meta + Google)
    add("meta_nc_roas", "NC ROAS on Meta spend", "totals", "roas", UP, ("orders", "meta"), lambda S: _div(S["onc_rev"], S["ms"]), "Actual NC revenue / Meta spend (the product bands' Total column).")
    add("meta_nc_cpa", "NC CPA on Meta spend", "totals", "money2", DOWN, ("orders", "meta"), lambda S: _div(S["ms"], S["onc_ord"]), "Meta spend / actual new customers (the product bands' Total column).")

    # testing vs scaling
    add("test_spend", "Testing spend", "stage", "money", NEUTRAL, ("meta",), lambda S: S["ts"], "Meta spend in campaigns whose name contains 'test'.")
    add("scale_spend", "Scaling spend", "stage", "money", NEUTRAL, ("meta",), lambda S: S["ss"], "Meta spend in all other campaigns.")
    add("test_share", "Testing share of spend", "stage", "rate", NEUTRAL, ("meta",), lambda S: _div(S["ts"], S["ms"]), "Testing spend / Meta spend.")
    add("test_nc", "Testing new customers", "stage", "int", UP, ("anyclick",), lambda S: S["tnc"], "New customers Upstack credits to Testing campaigns (any-click, 30-day window). Not additive to actual new customers.")
    add("scale_nc", "Scaling new customers", "stage", "int", UP, ("anyclick",), lambda S: S["snc"], "New customers Upstack credits to Scaling campaigns (any-click, 30-day window).")
    add("test_cpa", "Testing NC CPA", "stage", "money2", DOWN, ("meta", "anyclick"), lambda S: _div(S["ts"], S["tnc"]), "Testing spend / new customers credited to Testing campaigns.")
    add("scale_cpa", "Scaling NC CPA", "stage", "money2", DOWN, ("meta", "anyclick"), lambda S: _div(S["ss"], S["snc"]), "Scaling spend / new customers credited to Scaling campaigns.")
    return M


# ---------------------------------------------------------------------------------------------------------------- the layout (the sheet's bands)
# Chips (what the page lets you show or hide). 'plan' exists for the month grain only; 'change' is the sheet's WoW / MoM change block.
GROUPS = [
    {"key": "plan", "label": "Plan", "default": True, "intervals": ["month"]},
    {"key": "totals", "label": "DTC / Shopify", "default": True},
    {"key": "meta", "label": "Meta", "default": False},
    {"key": "google", "label": "Google", "default": False},
    {"key": "products", "label": "By product", "default": False},
    {"key": "stage", "label": "Testing vs scaling", "default": False},
    {"key": "change", "label": "Change", "default": True},
]

# The sheet's own columns of the 'WoW Δ' / 'MoM Δ' band that the app can supply (it has no Amazon and no EBITDA): % change of these against the previous period.
CHANGE_COLS = (("total_rev", "Total Revenue"), ("total_spend", "Total Spend"), ("nc_rev", "NC Revenue"), ("rc_rev", "Repeat Revenue"), ("nc_roas", "NC ROAS"),
               ("nc_cpa", "NC CPA"), ("nc_orders", "NC Orders"), ("m_roas", None), ("g_roas", None))
CHANGE_TITLE = {"day": "DoD Δ", "week": "WoW Δ", "month": "MoM Δ"}

# Month grain: the sheet's leading 'Projected* | Target | % Diff' block (Total Rev, Total Spend, NC Rev, EBITDA, NOI, then the EBITDA and NOI gap in dollars).
# The numbers are the Pacing page's own (app.projection.pacing.views.plan_block): this module only lays them out. (key, title, polarity)
PLAN_MEASURES = (("total_rev", "Total Rev", UP), ("total_spend", "Total Spend", DOWN), ("nc_rev", "NC Rev", UP), ("ebitda", "EBITDA", UP), ("noi", "NOI", UP))


def plan_columns() -> list[dict]:
    """The plan band's columns (values come from /api/trends/plan): per measure Projected* | Target | % Diff, then the two dollar gaps."""
    cols: list[dict] = []
    for key, title, pol in PLAN_MEASURES:
        cols += [{"key": f"plan_{key}_proj", "label": "Projected*", "kind": "money", "sub": title, "pol": NEUTRAL},
                 {"key": f"plan_{key}_goal", "label": "Target", "kind": "money", "sub": title, "pol": NEUTRAL},
                 {"key": f"plan_{key}_diff", "label": "% Diff", "kind": "diff", "sub": title, "pol": pol}]
    cols += [{"key": "plan_ebitda_gap", "label": "EBITDA", "kind": "gap", "sub": "Gap vs Target", "pol": UP},
             {"key": "plan_noi_gap", "label": "NOI", "kind": "gap", "sub": "Gap vs Target", "pol": UP}]
    return cols


def _col(key: str, label: str, **kw) -> dict:
    return {"key": key, "label": label, **kw}


def build_layout(interval: str, numbers: str, catalog: list[dict]) -> list[dict]:
    """The bands, left to right, in the order of the sheet's Rollup tabs: [Projected* | Target | % Diff (month)] DTC / SHOPIFY, META, per-product metric bands, SPEND BY STAGE,
    TESTING % OF SPEND, NC (UPSTACK ANY-CLICK), NC CPA BY STAGE, GOOGLE, then the change block. Bands the app has no data for (BUSINESS-WIDE with Amazon, AMAZON, EBITDA (LIVE P&L),
    the per-product click bands) are left out; columns inside a band the app cannot supply are left out too (docs/TRENDS.md lists them).
    Each band: key, title, group (the chip), tone (the colour family), cols [{key, label, tone?}]; a column key is a metric key of the payload (or a plan_ key)."""
    by = {m["key"]: m for m in catalog}

    def short(key: str, prefix: str) -> str:                          # 'Meta new customers (Upstack)' -> 'New customers (Upstack)'
        t = by[key]["label"]
        t = t[len(prefix):] if t.startswith(prefix) else t
        return t[:1].upper() + t[1:]

    own = numbers == "own"

    def plat(p: str, prefix: str) -> list[dict]:
        return [_col(f"{p}_purch", "Purchases" if own else short(f"{p}_purch", prefix)), _col(f"{p}_rev", "Purchase Value" if own else short(f"{p}_rev", prefix))]

    bands: list[dict] = []
    if interval == "month":
        bands.append({"key": "plan", "title": "Projected* | Target | % Diff", "group": "plan", "tone": "ink", "cols": plan_columns(), "external": True})
    bands.append({"key": "dtc", "title": "DTC / SHOPIFY", "group": "totals", "tone": "teal", "cols": [
        _col("total_rev", "Total Revenue"), _col("nc_rev", "NC Revenue"), _col("rc_rev", "Repeat Revenue"), _col("nc_orders", "NC Orders"), _col("nc_aov", "NC AOV"),
        _col("total_spend", "Ad Spend"), _col("nc_cpa", "NC CPA"), _col("nc_roas", "NC ROAS"), _col("mer", "MER"), _col("nc_cvr", "NC Conversion"), _col("repeat_share", "Repeat Share %")]})
    bands.append({"key": "meta", "title": "META", "group": "meta", "tone": "blue", "cols": [
        _col("meta_spend", "Spend"), _col("m_clicks", "Link Clicks"), _col("m_cpc", "CPC"), *plat("m", "Meta "), _col("m_cvr", "CVR"),
        _col("m_roas", "Platform ROAS" if own else short("m_roas", "Meta ")), _col("m_cpp", "Cost per Purchase" if own else short("m_cpp", "Meta ")),
        _col("m_impr", "Impressions"), _col("reach", "Reach"), _col("first_imp", "First-Time Impression %"), _col("cost_1k", "Cost per 1,000 Reached"), _col("m_cpm", "CPM"),
        _col("frequency", "Frequency"), _col("uoc", "Unique Outbound Clicks"), _col("uoc_ctr", "Unique Outbound CTR"), _col("m_ctr", "Link CTR")]})
    prod = [("vitabull", "VitaBull", "vb"), ("beefliver", "Beef Liver", "bl"), ("boneflex", "Boneflex", "bf")]
    for i, (bkey, title, per, total) in enumerate((("spend", "SPEND", "p_{}_spend", "meta_spend"), ("nc_rev", "NC REVENUE", "p_{}_nc_rev", "nc_rev"),
                                                   ("nc_roas", "NC ROAS", "p_{}_nc_roas", "meta_nc_roas"), ("nc_orders", "NC ORDERS", "p_{}_nc_orders", "nc_orders"),
                                                   ("nc_cpa", "NC CPA", "p_{}_nc_cpa", "meta_nc_cpa"))):
        bands.append({"key": "prod_" + bkey, "title": title, "group": "products", "tone": "plum" if i % 2 == 0 else "slate",
                      "cols": [_col(per.format(slug), name, tone=tone) for slug, name, tone in prod] + [_col(total, "Total", tone="tot")]})
    bands += [
        {"key": "stage_spend", "title": "SPEND BY STAGE", "group": "stage", "tone": "slate", "cols": [_col("scale_spend", "Scaling Spend"), _col("test_spend", "Testing Spend")]},
        {"key": "stage_share", "title": "TESTING % OF SPEND", "group": "stage", "tone": "plum", "cols": [_col("test_share", "Testing %")]},
        {"key": "stage_nc", "title": "NC (UPSTACK ANY-CLICK)", "group": "stage", "tone": "slate", "cols": [_col("scale_nc", "Scaling NC"), _col("test_nc", "Testing NC")]},
        {"key": "stage_cpa", "title": "NC CPA BY STAGE", "group": "stage", "tone": "plum", "cols": [_col("scale_cpa", "Scaling NC CPA"), _col("test_cpa", "Testing NC CPA")]},
        {"key": "google", "title": "GOOGLE", "group": "google", "tone": "green", "cols": [
            _col("google_spend", "Spend"), _col("g_clicks", "Clicks"), _col("g_cpc", "CPC"), *plat("g", "Google "), _col("g_cvr", "CVR"),
            _col("g_roas", "Platform ROAS" if own else short("g_roas", "Google ")), _col("g_cpp", "Cost per Purchase" if own else short("g_cpp", "Google ")),
            _col("g_impr", "Impressions"), _col("g_ctr", "CTR")]}]
    ch = []
    for key, label in CHANGE_COLS:
        if label is None:
            label = ("Meta " if key == "m_roas" else "Google ") + ("ROAS" if own else "NC ROAS")
        ch.append(_col(key, label))
    bands.append({"key": "change", "title": CHANGE_TITLE[interval], "group": "change", "tone": "grey", "cols": ch, "change": True})
    for b in bands:
        if not b.get("external"):
            for c in b["cols"]:
                assert c["key"] in by, c["key"]
    return bands


def _num(v: float | None, nd: int = 6) -> float | None:
    return None if v is None else round(v, nd)


def change_of(kind: str, cur: float | None, base: float | None) -> dict | None:
    """% change (amounts, counts, ratios) or percentage-point change (rates), as fractions; None when either side is missing or the base is zero."""
    if cur is None or base is None:
        return None
    if KINDS[kind] == "pp":
        return {"v": round(cur - base, 6), "t": "pp"}
    if base == 0:
        return None
    return {"v": round((cur - base) / base, 6), "t": "pct"}


def parse_args(args) -> dict:
    interval = (args.get("interval") or "week").strip().lower()
    if interval not in INTERVALS:
        raise q.ApiError(400, "interval must be day, week or month")
    raw = args.get("periods")
    n = DEFAULT_PERIODS[interval] if raw is None or str(raw).strip() == "" else q.parse_uint(str(raw).strip(), 2, MAX_PERIODS[interval],
                                                                                       f"periods must be a whole number from 2 to {MAX_PERIODS[interval]} for {interval}")
    numbers = pf.numbers_of(args)
    as_of = None
    if args.get("as_of"):
        try:
            as_of = dt.date.fromisoformat(str(args["as_of"]).strip())
        except ValueError:
            raise q.ApiError(400, "as_of must be a date (YYYY-MM-DD)") from None
        if not (FIRST_DAY <= as_of <= dt.date(2035, 12, 31)):
            raise q.ApiError(400, "as_of must lie between 2025-01-01 and 2035-12-31")
    return {"interval": interval, "periods": n, "numbers": numbers, "as_of": as_of}


def fetch_reach(conn, interval: str, lo: dt.date, hi: dt.date) -> dict[dt.date, dict]:
    """The reach feed's own rows of the interval's grain, keyed by period start."""
    rows = conn.execute("""SELECT period_start, period_end, impressions, reach, unique_outbound_clicks AS uoc FROM proj_feed_meta_reach
                           WHERE grain = %s AND period_start BETWEEN %s AND %s""", (interval, lo, hi)).fetchall()
    return {r["period_start"]: {"end": r["period_end"], "impr": _f(r["impressions"]), "reach": _f(r["reach"]), "uoc": _f(r["uoc"])} for r in rows}


def _reach_values(row: dict | None, spend: float | None) -> dict[str, float | None]:
    if not row or not row["reach"]:
        return {"reach": None, "frequency": None, "first_imp": None, "cost_1k": None, "uoc": None, "uoc_ctr": None}
    return {"reach": row["reach"], "frequency": _div(row["impr"], row["reach"]), "first_imp": _div(row["reach"], row["impr"]), "cost_1k": _div(spend, row["reach"], 1000) if spend is not None else None,
            "uoc": row["uoc"], "uoc_ctr": _div(row["uoc"], row["impr"])}


def trends(conn, args, *, today: dt.date | None = None) -> dict:
    """The /api/trends payload: periods newest first, each with every metric's value, its change vs the comparison base, and the reason for every dash."""
    conn.execute(f"SET LOCAL statement_timeout = {QUERY_TIMEOUT_MS}")           # a bad query answers a clear JSON error (503 'query timed out'), not a 20 s hang
    opt = parse_args(args)
    today = opt["as_of"] or today or pacific_today()
    cov = coverage(conn, today)
    through = through_of(cov, today)
    periods = build_periods(opt["interval"], today, through, opt["periods"])
    lo = min(min(p["start"], p["compare"]["start"]) for p in periods)
    lo = max(lo, FIRST_DAY - dt.timedelta(days=1))
    fr, present = fetch_frame(conn, lo, through, cov)
    cov["meta"].present = present["meta"]
    cov["prod_spend"].present = present["prod_spend"]
    reach = fetch_reach(conn, opt["interval"], lo, today)
    return assemble(opt, today, cov, through, periods, fr, reach)


def assemble(opt: dict, today: dt.date, cov: dict[str, Cov], through: dt.date, periods: list[dict], fr: dict, reach: dict) -> dict:
    """The payload from already loaded data (no database): the part the UI tests' mock server runs over made-up days."""
    interval, n, numbers = opt["interval"], opt["periods"], opt["numbers"]
    catalog = build_catalog(numbers)

    def values_for(a: dt.date, b: dt.date) -> tuple[dict[str, float | None], dict[str, str]]:
        S = sums(fr, a, b)
        vals: dict[str, float | None] = {}
        why: dict[str, str] = {}
        cache: dict[str, str | None] = {}
        for m in catalog:
            if m["fn"] is None:
                continue
            reason = None
            for src in m["needs"]:
                if src not in cache:
                    cache[src] = cov[src].why_not(SOURCE_LABELS[src], a, b)
                if cache[src]:
                    reason = cache[src]
                    break
            if reason:
                vals[m["key"]] = None
                why[m["key"]] = reason
                continue
            v = m["fn"](S)
            vals[m["key"]] = _num(v)
            if v is None:
                why[m["key"]] = "Undefined: the denominator is zero in this period."
        return vals, why

    kind_of = {m["key"]: m["kind"] for m in catalog}
    rows = []
    for p in periods:
        row = {"key": p["key"], "label": p["label"], "sub": p["sub"], "start": p["start"].isoformat(), "end": p["end"].isoformat(),
               "covered_end": p["covered_end"].isoformat() if p["covered_end"] else None, "days": p["days"], "days_cov": p["days_cov"], "open": p["open"], "state": p["state"]}
        c = p["compare"]
        row["compare"] = {"kind": c["kind"], "start": c["start"].isoformat(), "end": c["end"].isoformat(), "days": c["days"], "why": c["why"], "text": c["text"]}
        v: dict[str, float | None] = {}
        d: dict[str, dict | None] = {}
        why: dict[str, str] = {}
        if p["state"] == "not_loaded":
            for m in catalog:
                v[m["key"]] = None
            row.update(v=v, d={}, why={}, reach_through=None)
            rows.append(row)
            continue
        cur_v, cur_why = values_for(p["start"], p["covered_end"])
        v.update(cur_v)
        why.update(cur_why)
        base_v: dict[str, float | None] = {}
        base_why: dict[str, str] = {}
        if c["kind"] != "none":
            base_v, base_why = values_for(c["start"], c["end"])
        for m in catalog:
            k = m["key"]
            if m["fn"] is None:
                continue
            if c["kind"] == "none":
                d[k] = None
            else:
                d[k] = change_of(m["kind"], v.get(k), base_v.get(k))
            if d[k] is None and v.get(k) is not None and c["kind"] != "none":
                why[k + ":d"] = base_why.get(k) or ("The base period's value is zero." if base_v.get(k) == 0 else "No comparison value.")
        # reach: the feed's own row for this period (never a sum of days)
        rrow = reach.get(p["start"])
        r_why = None
        if not rrow:
            r_why = cov["reach"].why_not(SOURCE_LABELS["reach"], p["start"], p["covered_end"]) or "Meta reach has no row for this period."
        elif not p["open"] and rrow["end"] != p["end"]:
            r_why, rrow = f"Meta reach for this period is incomplete (through {_d(rrow['end'], True)}).", None
        spend_v = v.get("meta_spend")
        use_spend = spend_v if (rrow and not p["open"]) else None
        rv = _reach_values(rrow, use_spend)
        for k, val in rv.items():
            v[k] = _num(val)
            if val is None:
                why[k] = r_why or ("Cost / 1,000 reached is shown for closed periods only: the open period's reach runs past its spend." if k == "cost_1k" and p["open"]
                                   else "Meta spend is not available for this period." if k == "cost_1k" else "Meta reach is zero in this period.")
        if c["kind"] == "none":
            for k in rv:
                d[k] = None
        elif p["open"]:
            for k in rv:
                d[k] = None
                if v.get(k) is not None:
                    why[k + ":d"] = "Reach cannot be cut to the same days of the previous period (Meta de-duplicates it per period), so there is no like-for-like change yet."
        else:
            prow = reach.get(c["start"])
            if prow and prow["end"] != c["end"]:
                prow = None
            base_spend = base_v.get("meta_spend") if prow else None
            pv = _reach_values(prow, base_spend)
            for k in rv:
                d[k] = change_of(kind_of[k], v.get(k), pv.get(k))
                if d[k] is None and v.get(k) is not None:
                    why[k + ":d"] = "The previous period has no reach row." if not prow else "No comparison value."
        row.update(v=v, d={k: x for k, x in d.items() if x is not None}, why=why, reach_through=rrow["end"].isoformat() if rrow and p["open"] else None)
        rows.append(row)

    layout = build_layout(interval, numbers, catalog)
    groups = []
    for g in GROUPS:
        if g.get("intervals") and interval not in g["intervals"]:
            continue
        keys = [c["key"] for b in layout if b["group"] == g["key"] for c in b["cols"]]
        groups.append({"key": g["key"], "label": g["label"], "default": g["default"], "metrics": list(dict.fromkeys(keys))})
    yday = today - dt.timedelta(days=1)
    fresh = []
    for k in SOURCE_ORDER:
        cv = cov[k]
        behind = (yday - cv.last).days if cv.last else None
        fresh.append({"key": k, "label": SOURCE_LABELS[k], "short": SHORT[k], "first": cv.first.isoformat() if cv.first else None, "last": cv.last.isoformat() if cv.last else None,
                      "days_behind": behind, "stale": behind is None or behind >= STALE_AFTER_DAYS})
    notes = []
    if any(r["state"] == "open" for r in rows):
        notes.append("The open period is compared with the same elapsed days of the previous period, never with the full one.")
    if cov["meta"].first and cov["meta"].first < (cov["meta_up"].first or cov["meta"].first):
        notes.append(f"Meta before {_d(cov['meta_up'].first, True)} comes from the sheet-fed campaign history (campaign grain, Meta's own reporting); Upstack attribution of Meta starts {_d(cov['meta_up'].first, True)}.")
    return {"interval": interval, "periods": n, "numbers": numbers, "basis": BASIS_NAME[numbers], "today": today.isoformat(), "data_through": through.isoformat(),
            "metrics": [{k: m[k] for k in ("key", "label", "group", "kind", "pol", "help")} | {"change": KINDS[m["kind"]], "needs": list(m["needs"])} for m in catalog],
            "groups": groups, "layout": layout, "freshness": fresh, "rows": rows, "notes": notes,
            "labels": {"day": "Day over day", "week": "Week over week", "month": "Month over month"}}


# ---------------------------------------------------------------------------------------------------------------- the plan block (month grain)

PLAN_MAX_MONTHS = 36


def _pacing_setup():
    """(today, engine result, TargetBook, connection) exactly as the Pacing page builds them (scenario and as_of read from the request)."""
    from .projection.pacing import api as pacing_api
    return pacing_api._setup()


def plan_payload(args, setup: Callable | None = None) -> dict:
    """GET /api/trends/plan?from=YYYY-MM-DD&to=YYYY-MM-DD: the Pacing page's own 'Projection against the plan' numbers (projected, target, % diff) for each month in
    [from, to], via app.projection.pacing.views (plan_block). A running month projects at its month-to-date pace over the whole month; a closed month is its actual.
    The EBITDA and NOI gaps are projected minus target. Never raises for an engine problem: the page shows dashes with the reason instead."""
    try:
        frm = dt.date.fromisoformat(str(args.get("from", "")).strip()).replace(day=1)
        to = dt.date.fromisoformat(str(args.get("to", "")).strip()).replace(day=1)
    except ValueError:
        raise q.ApiError(400, "from and to must be dates (YYYY-MM-DD)") from None
    if frm > to or (to.year - frm.year) * 12 + to.month - frm.month + 1 > PLAN_MAX_MONTHS:
        raise q.ApiError(400, f"from must not be after to, and at most {PLAN_MAX_MONTHS} months")
    try:
        from .projection.pacing import views as pacing_views
        today, _res, book, conn = (setup or _pacing_setup)()
        out = pacing_views.rollup_payload(conn, "month", frm, to, today, book, "rollup", ["projection against the plan"])
    except Exception as e:                                                                    # noqa: BLE001  (the page must still render without the plan block)
        msg = getattr(e, "message", None) or str(e) or e.__class__.__name__
        return {"available": False, "reason": f"The plan figures could not be loaded ({msg}).", "months": [], "values": {}}
    rows = {r["key"]: r for r in out["rows"] if r.get("values") is not None}
    values: dict[str, list] = {}
    for key, _title, _pol in PLAN_MEASURES:
        for suf, src in (("proj", "proj"), ("goal", "goal"), ("diff", "diff")):
            r = rows.get(f"plan_{key}_{src}")
            values[f"plan_{key}_{suf}"] = [_num(v) for v in r["values"]] if r else [None] * len(out["months"])
    for key in ("ebitda", "noi"):
        values[f"plan_{key}_gap"] = [_num(p - g) if p is not None and g is not None else None for p, g in zip(values[f"plan_{key}_proj"], values[f"plan_{key}_goal"])]
    return {"available": True, "reason": None, "months": out["months"], "status": out["status"], "values": values, "data_through": out["data_through"],
            "caption": "Same figures as the Pacing page: a running month is projected at its month-to-date pace over the whole month, a closed month is its actual; targets are the plan of record's P&L. Business-wide, so Amazon is included here, unlike the columns to the right."}
