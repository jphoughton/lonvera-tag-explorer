"""All SQL for the Tag Explorer API, plus the small pure helpers that shape results.

Conventions
- Every function takes an open psycopg connection (dict rows) and returns plain Python (dicts / lists / floats), never Decimals.
- All values reach SQL as bound parameters. The only strings ever interpolated into SQL are the static fragments in this file
  and the group-by column lists chosen from the GROUP_COLS whitelist.
- Metric semantics follow docs/CONTRACT.md "Metric rules" (first-row attribution, since-launch rule, windows from family_daily,
  fixed 7d/live from ad_metrics, stage split). See _rm() for the central per-tracker-row CTE.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterable

# --------------------------------------------------------------------------------------------------------------------
# errors / constants
# --------------------------------------------------------------------------------------------------------------------


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class NotLoaded(ApiError):
    def __init__(self):
        super().__init__(503, "data not loaded yet")


STAGES = ("testing", "scaling", "unclassified")
WINDOW_NAMES = ("launch", "1d", "7d", "14d", "30d", "60d", "90d", "180d", "mtd")
TRAILING_DAYS = {"1d": 1, "7d": 7, "14d": 14, "30d": 30, "60d": 60, "90d": 90, "180d": 180}
LEVELS = ("cluster", "icp", "pain")
GROUP_COLS = {"cluster": ("cluster",), "icp": ("cluster", "icp"), "pain": ("cluster", "icp", "pain")}   # whitelist for GROUP BY
UNTAGGED = "(untagged)"
TAG_LABELS = {"linemap": "Line-mapped tags", "tracker": "Tracker tags — not line-mapped yet"}
CONF_NAME = {1: "low", 2: "medium", 3: "high"}
# Blind re-tag agreement per product lives in app_meta `reliability:<slug>` (JSON written by etl.load_tags). Vita Bull falls back to its
# original numbers when the key is missing; any other product without numbers gets a note with no percentages.
_VB_RELIABILITY = {"overall": {"line": 0.79, "icp": 0.84, "cluster": 0.88}, "by_confidence": {"high": {"line": 0.93}, "low": {"line": 0.52}}}

# --------------------------------------------------------------------------------------------------------------------
# small pure helpers
# --------------------------------------------------------------------------------------------------------------------


def _f(x: Any) -> float:
    return float(x) if x is not None else 0.0


def f2(x: Any) -> float:
    """Money: float dollars, 2 dp."""
    return round(_f(x), 2)


def num(x: Any) -> int | float:
    """Counts / purchases: int when whole, else 2 dp."""
    v = round(_f(x), 2)
    return int(v) if v == int(v) else v


def ratio(a: Any, b: Any, nd: int) -> float | None:
    d = _f(b)
    if d == 0:
        return None
    return round(_f(a) / d, nd)


def tag_label(tag_set: str) -> str:
    return TAG_LABELS.get(tag_set, TAG_LABELS["tracker"])


def rows_of(conn, sql: str, params: Any = None) -> list[dict]:
    return conn.execute(sql, params).fetchall()


def row_of(conn, sql: str, params: Any = None) -> dict | None:
    return conn.execute(sql, params).fetchone()


# --------------------------------------------------------------------------------------------------------------------
# data meta + window parsing
# --------------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class DataMeta:
    daily_start: dt.date
    data_through: dt.date
    lifetime_cutoff: dt.date | None = None        # legacy (sheet-era) value, unused since v1.5
    impressions_from: dt.date | None = None      # first day with impression data (None = none loaded yet)


def load_meta(conn) -> DataMeta:
    kv = {r["key"]: r["value"] for r in rows_of(
        conn, "SELECT key, value FROM app_meta WHERE key = ANY(%s)", (["daily_start", "lifetime_cutoff", "data_through"],))}
    through = kv.get("data_through")
    start = kv.get("daily_start")
    if not through or not start:
        mm = row_of(conn, "SELECT min(day) AS lo, max(day) AS hi FROM ad_daily")
        if not mm or mm["hi"] is None:
            raise NotLoaded()
        through = through or mm["hi"].isoformat()
        start = start or mm["lo"].isoformat()
    first_imp = row_of(conn, "SELECT min(day) AS d FROM family_daily WHERE impressions > 0")
    return DataMeta(
        daily_start=dt.date.fromisoformat(start),
        lifetime_cutoff=dt.date.fromisoformat(kv["lifetime_cutoff"]) if kv.get("lifetime_cutoff") else None,
        data_through=dt.date.fromisoformat(through),
        impressions_from=first_imp["d"] if first_imp else None,
    )


def _fmt_day(d: dt.date, with_year: bool) -> str:
    return f"{d:%b} {d.day}" + (f", {d.year}" if with_year else "")


def _plural(n: int, word: str = "day") -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


@dataclass
class WindowSpec:
    kind: str                     # launch | trailing | mtd | custom
    name: str                     # launch | 7d | mtd | custom ...
    date_from: dt.date | None
    date_to: dt.date | None
    days: int | None
    clamped: bool
    note: str | None
    label: str
    stage: str = "all"            # 'all' or the included stages, comma-separated in canonical order (see parse_stage)
    cmp: "WindowSpec | None" = None   # comparison window B (None = comparison off); A is always a date window then

    @property
    def is_launch(self) -> bool:
        return self.kind == "launch"

    @property
    def is_empty(self) -> bool:
        return self.days == 0

    @property
    def stages(self) -> list[str]:
        return list(STAGES) if self.stage == "all" else self.stage.split(",")

    def traffic_note(self, meta: DataMeta) -> str | None:
        """Why impressions / clicks / CTR / CPC / CPM may cover less than spend does."""
        if self.is_launch or self.is_empty or self.date_from is None:
            return None                           # since-launch figures are API lifetime sums: impressions and clicks included
        if meta.impressions_from is None:
            return "No impressions are loaded yet."
        if meta.impressions_from > self.date_from:
            return f"Impressions only from {_fmt_day(meta.impressions_from, False)}."
        return None

    def info(self, meta: DataMeta) -> dict:
        note = " ".join(x for x in (self.note, self.traffic_note(meta)) if x) or None
        return {
            "kind": self.kind, "name": self.name, "label": self.label,
            "from": self.date_from.isoformat() if self.date_from else None,
            "to": self.date_to.isoformat() if self.date_to else None,
            "days": self.days, "clamped": self.clamped, "note": note, "stage": self.stage, "stages": self.stages,
            "daily_start": meta.daily_start.isoformat(), "data_through": meta.data_through.isoformat(),
        }

    def compare_info(self, meta: DataMeta) -> dict | None:
        return self.cmp.info(meta) if self.cmp is not None else None

    def span(self, meta: DataMeta) -> tuple[dt.date, dt.date]:
        """Day range for endpoints that have no 'since launch' (campaigns, unmatched, stage-daily): launch = every day held."""
        if self.is_launch:
            return meta.daily_start, meta.data_through
        return self.date_from, self.date_to        # type: ignore[return-value]


def stage_list(stage: str) -> list[str]:
    return list(STAGES) if stage == "all" else stage.split(",")


def parse_stage(args) -> str:
    """stage = comma-separated include list from testing,scaling,unclassified (omitted or 'all' = all three). Returns the canonical
    string: 'all' or the included stages in canonical order joined by commas. Unknown or empty -> 400."""
    raw = args.get("stage")
    if raw is None:
        return "all"
    parts = [p.strip().lower() for p in str(raw).split(",") if p.strip()]
    if not parts:
        raise ApiError(400, "stage must include at least one of testing, scaling, unclassified (or all)")
    for p in parts:
        if p != "all" and p not in STAGES:
            raise ApiError(400, f"unknown stage {p[:40]!r}: use a comma-separated list from testing, scaling, unclassified (or all)")
    if "all" in parts:
        return "all"
    inc = [st for st in STAGES if st in parts]
    return "all" if len(inc) == len(STAGES) else ",".join(inc)


def _parse_day(s: str, field: str) -> dt.date:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s or ""):
        raise ApiError(400, f"{field} must be YYYY-MM-DD")
    try:
        return dt.date.fromisoformat(s)
    except ValueError:
        raise ApiError(400, f"{field} is not a valid date")


def _label_for(df: dt.date, dtt: dt.date, meta: DataMeta, suffix: str = "") -> str:
    with_year = df.year != meta.data_through.year or dtt.year != meta.data_through.year
    n = (dtt - df).days + 1
    core = _fmt_day(df, with_year) if df == dtt else f"{_fmt_day(df, with_year)} – {_fmt_day(dtt, with_year)}"
    return f"{core} · {_plural(n)}{suffix}"


def parse_window(args, meta: DataMeta) -> WindowSpec:
    """window=launch|1d|7d|14d|30d|60d|90d|180d|mtd  or  from=YYYY-MM-DD&to=YYYY-MM-DD (custom wins). Plus stage=all|testing|scaling,
    and the optional comparison window B: compare=prev|1d|...|mtd or cfrom/cto."""
    a = _parse_a(args, meta)
    a.cmp = parse_compare(args, meta, a)
    return a


COMPARE_NAMES = ("prev",) + tuple(TRAILING_DAYS) + ("mtd",)


def parse_compare(args, meta: DataMeta, a: WindowSpec) -> WindowSpec | None:
    """Window B. prev = same length immediately before A; presets end at data_through like A's; cfrom/cto = custom (wins).
    B is clamped to the daily data span; if nothing of B is available it comes back empty (days = 0) rather than failing."""
    cf, ct = (args.get("cfrom") or "").strip(), (args.get("cto") or "").strip()
    cname = (args.get("compare") or "").strip().lower()
    if not (cf or ct) and cname in ("", "off", "none"):
        return None
    if a.is_launch:
        raise ApiError(400, "compare needs a date window")
    ds, dthru = meta.daily_start, meta.data_through
    if cf or ct:
        if not (cf and ct):
            raise ApiError(400, "cfrom and cto are both required for a custom comparison range")
        f, t = _parse_day(cf, "cfrom"), _parse_day(ct, "cto")
        if f > t:
            raise ApiError(400, "cfrom must not be after cto")
        kind = name = "custom"
    elif cname == "prev":
        n = a.days or 1
        t = a.date_from - dt.timedelta(days=1)
        f = t - dt.timedelta(days=n - 1)
        kind = name = "prev"
    elif cname == "mtd":
        f, t = dthru.replace(day=1), dthru
        kind = name = "mtd"
    elif cname in TRAILING_DAYS:
        f, t = dthru - dt.timedelta(days=TRAILING_DAYS[cname] - 1), dthru
        kind, name = "trailing", cname
    else:
        raise ApiError(400, f"compare must be one of {', '.join(COMPARE_NAMES)} (or cfrom/cto)")
    nf, nt = max(f, ds), min(t, dthru)
    requested = _label_for(f, t, meta, "")
    if nf > nt:
        return WindowSpec(kind, name, None, None, 0, True,
                          f"No daily data for {requested}; data covers {_fmt_day(ds, True)} – {_fmt_day(dthru, True)}.",
                          requested + " (no data)", a.stage)
    clamped = (nf, nt) != (f, t)
    note = (f"Daily data covers {_fmt_day(ds, True)} – {_fmt_day(dthru, True)}; comparison window cut to "
            f"{_fmt_day(nf, True)} – {_fmt_day(nt, True)}." if clamped else None)
    suffix = " · MTD" if kind == "mtd" else ""
    return WindowSpec(kind, name, nf, nt, (nt - nf).days + 1, clamped, note, _label_for(nf, nt, meta, suffix), a.stage)


def _parse_a(args, meta: DataMeta) -> WindowSpec:
    stage = parse_stage(args)
    raw_from, raw_to = (args.get("from") or "").strip(), (args.get("to") or "").strip()
    name = (args.get("window") or "launch").strip().lower()
    ds, dthru = meta.daily_start, meta.data_through

    if raw_from or raw_to:
        if not (raw_from and raw_to):
            raise ApiError(400, "from and to are both required for a custom range")
        f, t = _parse_day(raw_from, "from"), _parse_day(raw_to, "to")
        if f > t:
            raise ApiError(400, "from must not be after to")
        nf, nt = max(f, ds), min(t, dthru)
        if nf > nt:
            raise ApiError(400, f"no daily data in that range (data covers {_fmt_day(ds, True)} – {_fmt_day(dthru, True)})")
        clamped = (nf, nt) != (f, t)
        note = (f"Daily data covers {_fmt_day(ds, True)} – {_fmt_day(dthru, True)}; showing {_fmt_day(nf, True)} – {_fmt_day(nt, True)}"
                if clamped else None)
        return WindowSpec("custom", "custom", nf, nt, (nt - nf).days + 1, clamped, note, _label_for(nf, nt, meta), stage)

    if name == "launch":
        note = None
        if stage != "all" and "unclassified" not in stage.split(","):
            note = ("Since launch with this stage filter counts only days with a campaign (classified days); "
                    "history before the daily range and days without campaign data are 'unclassified' and excluded.")
        return WindowSpec("launch", "launch", None, None, None, False, note, "Since launch", stage)

    if name == "mtd":
        f, t = dthru.replace(day=1), dthru
        clamped = f < ds
        if clamped:
            f = ds
        note = f"Daily data starts {_fmt_day(ds, True)}." if clamped else None
        return WindowSpec("mtd", "mtd", f, t, (t - f).days + 1, clamped, note, _label_for(f, t, meta, " · MTD"), stage)

    if name in TRAILING_DAYS:
        n = TRAILING_DAYS[name]
        f, t = dthru - dt.timedelta(days=n - 1), dthru
        clamped = f < ds
        if clamped:
            f = ds
        have = (t - f).days + 1
        note = (f"Daily data starts {_fmt_day(ds, True)}; showing {_plural(have)} instead of {n}." if clamped else None)
        return WindowSpec("trailing", name, f, t, have, clamped, note, _label_for(f, t, meta), stage)

    raise ApiError(400, f"window must be one of {', '.join(WINDOW_NAMES)} (or from/to)")


# --------------------------------------------------------------------------------------------------------------------
# metrics shaping
# --------------------------------------------------------------------------------------------------------------------

AGG_COLS = """
  count(DISTINCT family_key)                         AS unique_ads,
  count(*) FILTER (WHERE live)                       AS live_now,
  count(*) FILTER (WHERE spend > 0)                  AS active_ads,
  COALESCE(sum(spend), 0)                            AS spend,
  COALESCE(sum(purchases), 0)                        AS purchases,
  COALESCE(sum(revenue), 0)                          AS revenue,
  COALESCE(sum(spend_7d), 0)                         AS spend_7d,
  COALESCE(sum(purchases_7d), 0)                     AS purchases_7d,
  COALESCE(sum(t_spend), 0) AS t_spend, COALESCE(sum(t_purch), 0) AS t_purch, COALESCE(sum(t_rev), 0) AS t_rev,
  COALESCE(sum(s_spend), 0) AS s_spend, COALESCE(sum(s_purch), 0) AS s_purch, COALESCE(sum(s_rev), 0) AS s_rev,
  COALESCE(sum(u_spend), 0) AS u_spend, COALESCE(sum(u_purch), 0) AS u_purch, COALESCE(sum(u_rev), 0) AS u_rev,
  COALESCE(sum(c_spend), 0) AS c_spend, COALESCE(sum(c_purch), 0) AS c_purch, COALESCE(sum(c_rev), 0) AS c_rev,
  count(*) FILTER (WHERE c_spend > 0)                AS c_active,
  COALESCE(sum(imp), 0) AS imp, COALESCE(sum(clk), 0) AS clk, COALESCE(sum(clk_tr), 0) AS clk_tr, COALESCE(sum(sp_tr), 0) AS sp_tr,
  COALESCE(sum(purch_t), 0) AS purch_t,
  COALESCE(sum(c_imp), 0) AS c_imp, COALESCE(sum(c_clk), 0) AS c_clk, COALESCE(sum(c_clk_tr), 0) AS c_clk_tr, COALESCE(sum(c_sp_tr), 0) AS c_sp_tr
"""

ZERO_AGG = {k: Decimal(0) for k in (
    "unique_ads", "live_now", "active_ads", "spend", "purchases", "revenue", "spend_7d", "purchases_7d",
    "t_spend", "t_purch", "t_rev", "s_spend", "s_purch", "s_rev", "u_spend", "u_purch", "u_rev",
    "c_spend", "c_purch", "c_rev", "c_active", "imp", "clk", "clk_tr", "sp_tr", "purch_t", "c_imp", "c_clk", "c_clk_tr", "c_sp_tr")}


# ---- window A / window B (comparison) helpers -----------------------------------------------------------------------


def _pd(x: Any, days: int | None) -> float | None:
    return None if not days else round(_f(x) / days, 2)


def _pct(a: Any, b: Any) -> float | None:
    """(A - B) / B as a fraction; null when B is 0/missing or A is missing."""
    if a is None or b is None or _f(b) == 0:
        return None
    return round((_f(a) - _f(b)) / _f(b), 4)


def side_a(r: dict) -> dict:
    """Window A numbers of an aggregate / row dict (keys spend, purchases, revenue, imp, clk, clk_tr, sp_tr[, purch_t])."""
    return {"spend": r["spend"], "purchases": r["purchases"], "revenue": r["revenue"], "imp": r["imp"], "clk": r["clk"],
            "clk_tr": r["clk_tr"], "sp_tr": r["sp_tr"], "purch_t": r.get("purch_t", r["purchases"])}


def side_b(r: dict) -> dict:
    """Window B numbers (the c_* columns)."""
    return {"spend": r["c_spend"], "purchases": r["c_purch"], "revenue": r["c_rev"], "imp": r["c_imp"], "clk": r["c_clk"],
            "clk_tr": r["c_clk_tr"], "sp_tr": r["c_sp_tr"], "purch_t": r["c_purch"]}


ZERO_SIDE = {"spend": 0, "purchases": 0, "revenue": 0, "imp": 0, "clk": 0, "clk_tr": 0, "sp_tr": 0, "purch_t": 0}

# keys of a metric side in the order they appear, with their rounding for the delta
_DELTA_ND = {"spend": 2, "purchases": 2, "revenue": 2, "roas": 4, "cpa": 2, "impressions": 0, "link_clicks": 0, "ctr": 5, "cpc": 4, "cpm": 4,
             "conv_rate": 5}


def side_fields(side: dict, days: int | None) -> dict:
    """spend / purchases / revenue / roas / cpa, the traffic metrics (impressions, link_clicks, ctr, cpc, cpm, conv_rate) and per-day
    values for one window.

    Traffic bases: CTR = link clicks / impressions over rows with impressions; CPC = spend_tr / link clicks and CPM = spend_tr * 1000 /
    impressions, where spend_tr is the spend of rows with traffic data (so lifetime or unknown-traffic spend never pollutes them);
    conv rate = purchases / link clicks.

    UNKNOWN is null, never 0: impressions is null when there is activity (spend or link clicks) but no impression data; link_clicks is null
    when there is spend but neither impressions nor clicks; every ratio is null when its denominator is 0 or its numerator base is missing
    (e.g. spend_tr = 0 while spend > 0, which means 'traffic spend not recorded', not a CPC of $0)."""
    sp, pu, rv, imp, clk = side["spend"], side["purchases"], side["revenue"], side["imp"], side["clk"]
    f_sp, f_imp, f_clk = _f(sp), _f(imp), _f(clk)
    imp_v = None if f_imp == 0 and (f_clk > 0 or f_sp > 0) else num(imp)
    clk_v = None if f_clk == 0 and f_imp == 0 and f_sp > 0 else num(clk)
    base_missing = _f(side["sp_tr"]) == 0 and f_sp > 0
    return {
        "spend": f2(sp), "purchases": num(pu), "revenue": f2(rv), "roas": ratio(rv, sp, 4), "cpa": ratio(sp, pu, 2),
        "impressions": imp_v, "link_clicks": clk_v,
        "ctr": ratio(side["clk_tr"], imp, 5), "cpc": None if base_missing else ratio(side["sp_tr"], clk, 4),
        "cpm": None if f_imp == 0 or base_missing else round(_f(side["sp_tr"]) * 1000 / f_imp, 4),
        "conv_rate": ratio(side["purch_t"], clk, 5),
        "spend_per_day": _pd(sp, days), "purchases_per_day": _pd(pu, days), "revenue_per_day": _pd(rv, days),
        "impressions_per_day": None if imp_v is None else _pd(imp, days), "link_clicks_per_day": None if clk_v is None else _pd(clk, days),
    }


def _raw_ratios(side: dict) -> dict:
    """Unrounded ratios of a side (None when the denominator is 0), so differences and percent changes are not distorted by display rounding."""
    def r(n, d, k=1.0):
        return None if _f(d) == 0 else _f(n) * k / _f(d)
    base_missing = _f(side["sp_tr"]) == 0 and _f(side["spend"]) > 0
    return {"roas": r(side["revenue"], side["spend"]), "cpa": r(side["spend"], side["purchases"]),
            "ctr": r(side["clk_tr"], side["imp"]), "cpc": None if base_missing else r(side["sp_tr"], side["clk"]),
            "cpm": None if base_missing else r(side["sp_tr"], side["imp"], 1000.0), "conv_rate": r(side["purch_t"], side["clk"])}


def cmp_blocks(a: dict, w: "WindowSpec", b: dict, b_active: Any = None) -> tuple[dict, dict]:
    """(cmp, delta) for window A vs window B sides. Everything is recomputed from the sums, never averaged."""
    da, db = w.days, (w.cmp.days if w.cmp is not None else None)
    fa, fb = side_fields(a, da), side_fields(b, db)
    cmp = dict(fb)
    if b_active is not None:
        cmp["active_ads"] = int(b_active)
    ra, rb = _raw_ratios(a), _raw_ratios(b)
    additive = {"spend": "spend", "purchases": "purchases", "revenue": "revenue", "impressions": "imp", "link_clicks": "clk"}
    delta = {}
    for k, nd in _DELTA_ND.items():
        if k in additive:
            x, y = _f(a[additive[k]]), _f(b[additive[k]])
            if fa[k] is None or fb[k] is None:                       # unknown on either side (impressions / link clicks): no delta
                delta[k] = delta[f"{k}_pct"] = None
                continue
            delta[k] = num(x - y) if k == "purchases" else round(x - y, nd)
            delta[f"{k}_pct"] = _pct(x, y)
        else:
            x, y = ra[k], rb[k]
            delta[k] = None if x is None or y is None else round(x - y, nd)
            delta[f"{k}_pct"] = _pct(x, y)
    # percentage points (0.5 = 0.5 pp), not fractions: ctr_pp, conv_rate_pp (and spend_share_pp, added by apply_shares)
    delta["ctr_pp"] = None if delta["ctr"] is None else round((ra["ctr"] - rb["ctr"]) * 100, 3)
    delta["conv_rate_pp"] = None if delta["conv_rate"] is None else round((ra["conv_rate"] - rb["conv_rate"]) * 100, 3)
    for k, key in (("spend", "spend"), ("purchases", "purchases"), ("revenue", "revenue"), ("impressions", "imp"), ("link_clicks", "clk")):
        if fa[k] is None or fb[k] is None:
            delta[f"{k}_per_day_pct"] = None
            continue
        ax, by = (None if not da else _f(a[key]) / da), (None if not db else _f(b[key]) / db)
        delta[f"{k}_per_day_pct"] = _pct(ax, by)
    return cmp, delta


def window_block(a: dict, w: "WindowSpec | None", b: dict | None = None) -> dict:
    """Row-level metric block for window A (+ cmp and delta when comparison is on). a / b are 'sides' (see side_a / side_b)."""
    out = side_fields(a, w.days if w is not None else None)
    if w is not None and w.cmp is not None:
        out["cmp"], out["delta"] = cmp_blocks(a, w, b or ZERO_SIDE)
    return out


def share(x: Any, total: Any) -> float | None:
    return None if total is None or _f(total) == 0 else round(_f(x) / _f(total), 6)


def apply_shares(blocks: Iterable[dict], total: dict) -> None:
    """spend_share on every block (a metric dict or a row with spend/cmp/delta keys) relative to `total`'s spend. With comparison on,
    also cmp.spend_share (relative to total.cmp.spend) and delta.spend_share_pp (A - B in percentage points)."""
    ta = total["spend"]
    tb = total["cmp"]["spend"] if "cmp" in total else None
    for b in blocks:
        b["spend_share"] = share(b["spend"], ta)
        if "cmp" in b:
            sb = share(b["cmp"]["spend"], tb)
            b["cmp"]["spend_share"] = sb
            b["delta"]["spend_share_pp"] = None if b["spend_share"] is None or sb is None else round((b["spend_share"] - sb) * 100, 4)


def shape_m(a: dict, w: "WindowSpec | None" = None) -> dict:
    """aggregate row -> the contract's `m` object (ratios always recomputed from sums)."""
    t_sp, s_sp = _f(a["t_spend"]), _f(a["s_spend"])
    out = {
        "unique_ads": int(a["unique_ads"]),
        "live_now": int(a["live_now"]),
        **side_fields(side_a(a), w.days if w is not None else None),
        "spend_7d": f2(a["spend_7d"]),
        "purchases_7d": num(a["purchases_7d"]),
        "cpa_7d": ratio(a["spend_7d"], a["purchases_7d"], 2),
        "active_ads": int(a["active_ads"]),
        "testing_pct": ratio(t_sp, t_sp + s_sp, 4),
        "stage": {
            "testing": {"spend": f2(a["t_spend"]), "purchases": num(a["t_purch"]), "revenue": f2(a["t_rev"]),
                        "roas": ratio(a["t_rev"], a["t_spend"], 4), "cpa": ratio(a["t_spend"], a["t_purch"], 2)},
            "scaling": {"spend": f2(a["s_spend"]), "purchases": num(a["s_purch"]), "revenue": f2(a["s_rev"]),
                        "roas": ratio(a["s_rev"], a["s_spend"], 4), "cpa": ratio(a["s_spend"], a["s_purch"], 2)},
            "unclassified": {"spend": f2(a["u_spend"]), "purchases": num(a["u_purch"]), "revenue": f2(a["u_rev"])},
        },
    }
    if w is not None and w.cmp is not None:
        out["cmp"], out["delta"] = cmp_blocks(side_a(a), w, side_b(a), b_active=a["c_active"])
    return out


def sum_aggs(rows: Iterable[dict], unique_ads: int) -> dict:
    out = dict(ZERO_AGG)
    for r in rows:
        for k in out:
            if k != "unique_ads":
                out[k] += r[k]
    out["unique_ads"] = unique_ads
    return out


def simple_metrics(spend: Any, purchases: Any, revenue: Any) -> dict:
    return {"spend": f2(spend), "purchases": num(purchases), "revenue": f2(revenue),
            "roas": ratio(revenue, spend, 4), "cpa": ratio(spend, purchases, 2)}


# --------------------------------------------------------------------------------------------------------------------
# the central CTE: one row per tracker row with its tag group and its (first-row attributed) metrics
# --------------------------------------------------------------------------------------------------------------------

_TR = """
tr AS (
  SELECT r.id, r.product_id, r.concept_id, r.ad_family, r.family_key, r.sheet_row, r.status, r.ad_type,
         COALESCE(NULLIF(btrim(r.ad_family), ''), r.concept_id) AS display,
         (t.id IS NOT NULL) AS tagged,
         CASE WHEN t.id IS NULL THEN '(untagged)' ELSE COALESCE(NULLIF(btrim(COALESCE(t.cluster, tl.cluster)), ''), '(none)') END AS cluster,
         CASE WHEN t.id IS NULL THEN '(untagged)' ELSE COALESCE(NULLIF(btrim(COALESCE(t.icp, tl.icp)), ''), '(none)') END AS icp,
         CASE WHEN t.id IS NULL THEN '(untagged)' ELSE COALESCE(NULLIF(btrim(COALESCE(t.pain_point, tl.pain_point)), ''), '(none)') END AS pain,
         tl.line_code, COALESCE(t.emotional_trigger, tl.emotional_trigger) AS trig, tl.cluster_color AS color,
         t.confidence, t.method,
         CASE t.confidence WHEN 'low' THEN 1 WHEN 'medium' THEN 2 WHEN 'high' THEN 3 END AS conf_rank
  FROM tracker_rows r
  JOIN products p ON p.id = r.product_id
  LEFT JOIN ad_tags t ON t.tracker_row_id = r.id AND t.is_current AND t.tag_set = p.tag_set
  LEFT JOIN taxonomy_lines tl ON tl.id = t.line_id
  %(where)s
)
"""


def _tr_cte(product_id: int | None, family_key: str | None, params: dict) -> str:
    where = []
    if product_id is not None:
        where.append("r.product_id = %(pid)s")
        params["pid"] = product_id
    if family_key is not None:
        where.append("r.family_key = %(fk)s")
        params["fk"] = family_key
    return _TR % {"where": ("WHERE " + " AND ".join(where)) if where else ""}


def _stage_sum_cols(prefix: str, day_pred: str = "", with_cmp: bool = False, launch: bool = False) -> str:
    """Column list for the family_daily scan.
    f_* (selected stages) and t_/s_/u_* (stage buckets) for spend, purchases, revenue; f_imp / f_clk / f_clk_tr / f_sp_tr / f_purch_t = the traffic
    sums of the selected stages; all restricted by day_pred. with_cmp adds c_* = the selected stages over window B's days (same scan);
    launch adds d_* = the same quantities over every stage (the daily part of the since-launch total)."""
    extra = f" AND {day_pred}" if day_pred else ""
    stage_in = f"{prefix}stage = ANY(%(stages)s)"
    cols = []
    for short, col in (("spend", "spend"), ("purch", "purchases"), ("rev", "revenue")):
        cols.append(f"COALESCE(sum({prefix}{col}) FILTER (WHERE {stage_in}{extra}), 0) AS f_{short}")
    for letter, stage in (("t", "testing"), ("s", "scaling"), ("u", "unclassified")):
        for short, col in (("spend", "spend"), ("purch", "purchases"), ("rev", "revenue")):
            cols.append(f"COALESCE(sum({prefix}{col}) FILTER (WHERE {prefix}stage = '{stage}'{extra}), 0) AS {letter}_{short}")
    traffic = (("imp", "impressions"), ("clk", "link_clicks"), ("clk_tr", "clicks_tr"), ("sp_tr", "spend_tr"), ("purch_t", "purchases"))
    for short, col in traffic:
        cols.append(f"COALESCE(sum({prefix}{col}) FILTER (WHERE {stage_in}{extra}), 0) AS f_{short}")
    if with_cmp:
        cpred = f"{prefix}day BETWEEN %(cf)s AND %(ct)s"
        for short, col in (("spend", "spend"), ("purch", "purchases"), ("rev", "revenue"), ("imp", "impressions"), ("clk", "link_clicks"),
                           ("clk_tr", "clicks_tr"), ("sp_tr", "spend_tr")):
            cols.append(f"COALESCE(sum({prefix}{col}) FILTER (WHERE {stage_in} AND {cpred}), 0) AS c_{short}")
    if launch:
        for short, col in (("spend", "spend"), ("purch", "purchases"), ("rev", "revenue"), ("imp", "impressions"), ("clk", "link_clicks"),
                           ("clk_tr", "clicks_tr"), ("sp_tr", "spend_tr")):
            cols.append(f"COALESCE(sum({prefix}{col}), 0) AS d_{short}")
    return ",\n    ".join(cols)


def _rm(meta: DataMeta, w: WindowSpec, product_id: int | None = None, family_key: str | None = None) -> tuple[str, dict]:
    """Returns ('WITH tr AS (...), ..., rm AS (...) ', params). `rm` has one row per tracker row:
         id, product_id, concept_id, ad_family, display, family_key, sheet_row, status, ad_type, tagged, cluster, icp, pain,
         line_code, trig, color, confidence, method, conf_rank, counted (metrics are attributed to this row), is_first, live,
         spend, purchases, revenue (selected window + stages; 0 unless counted), imp, clk, clk_tr, sp_tr, purch_t (traffic sums of the same
         window), spend_7d, purchases_7d (fixed; first row only), t_*/s_*/u_* (stage buckets of the window),
         c_spend/c_purch/c_rev/c_imp/c_clk/c_clk_tr/c_sp_tr (comparison window B, same stages; 0 when comparison is off).

    Since launch (v1.5) = the API lifetime totals stored on ad_metrics (spend_launch ... clicks_launch). Lifetime carries no stage, so for a
    stage list the family's daily rows of the included stages are used and everything the daily rows do not explain (lifetime minus all
    daily rows: history before the daily range, restatements) counts as 'unclassified'. All three stages together therefore add up to the
    lifetime total."""
    params: dict[str, Any] = {"d0": meta.daily_start, "dt": meta.data_through, "stages": w.stages}
    sql = "WITH " + _tr_cte(product_id, family_key, params)
    first_row = "(COALESCE(am.is_first_row, false) AND NOT starts_with(tr.family_key, 'id:'))"
    cw = w.cmp if (w.cmp is not None and not w.cmp.is_empty) else None      # comparison window B with at least one day of data
    zero = "0"
    c_imp = c_clk = c_clk_tr = c_sp_tr = zero

    if not w.is_launch:
        lo, hi = w.date_from, w.date_to
        if cw is not None:
            lo, hi = min(lo, cw.date_from), max(hi, cw.date_to)
            params.update(cf=cw.date_from, ct=cw.date_to)
        params.update(wf=w.date_from, wt=w.date_to, lo=lo, hi=hi)
        cmp_cols = ", w.c_spend, w.c_purch, w.c_rev, w.c_imp, w.c_clk, w.c_clk_tr, w.c_sp_tr" if cw is not None else ""
        sql += f""",
win AS (
  SELECT family_key,
    {_stage_sum_cols('', 'day BETWEEN %(wf)s AND %(wt)s', cw is not None)}
  FROM family_daily
  WHERE day BETWEEN %(lo)s AND %(hi)s AND family_key IN (SELECT family_key FROM tr)
  GROUP BY family_key
),
b AS (
  SELECT tr.*, {first_row} AS counted, COALESCE(am.is_first_row, false) AS is_first, COALESCE(am.live, false) AS am_live,
         COALESCE(am.spend_7d, 0) AS s7, COALESCE(am.purchases_7d, 0) AS p7,
         w.f_spend, w.f_purch, w.f_rev, w.t_spend, w.t_purch, w.t_rev, w.s_spend, w.s_purch, w.s_rev, w.u_spend, w.u_purch, w.u_rev,
         w.f_imp, w.f_clk, w.f_clk_tr, w.f_sp_tr, w.f_purch_t {cmp_cols}
  FROM tr
  LEFT JOIN ad_metrics am ON am.tracker_row_id = tr.id
  LEFT JOIN win w ON w.family_key = tr.family_key
)"""
        spend, purch, rev = ("COALESCE(b.f_spend, 0)", "COALESCE(b.f_purch, 0)", "COALESCE(b.f_rev, 0)")
        imp, clk, clk_tr, sp_tr, purch_t = ("COALESCE(b.f_imp, 0)", "COALESCE(b.f_clk, 0)", "COALESCE(b.f_clk_tr, 0)",
                                            "COALESCE(b.f_sp_tr, 0)", "COALESCE(b.f_purch_t, 0)")
        u_extra = ("", "", "")
        if cw is not None:
            c_imp, c_clk, c_clk_tr, c_sp_tr = ("COALESCE(b.c_imp, 0)", "COALESCE(b.c_clk, 0)", "COALESCE(b.c_clk_tr, 0)", "COALESCE(b.c_sp_tr, 0)")
    else:
        sql += f""",
fr AS (
  SELECT am.tracker_row_id, am.family_key
  FROM ad_metrics am
  WHERE am.is_first_row AND NOT starts_with(am.family_key, 'id:') AND am.tracker_row_id IN (SELECT id FROM tr)
),
win AS (
  SELECT fr.tracker_row_id,
    {_stage_sum_cols('fd.', '', False, launch=True)}
  FROM fr JOIN family_daily fd ON fd.family_key = fr.family_key AND fd.day <= %(dt)s
  GROUP BY fr.tracker_row_id
),
b AS (
  SELECT tr.*, {first_row} AS counted, COALESCE(am.is_first_row, false) AS is_first, COALESCE(am.live, false) AS am_live,
         COALESCE(am.spend_7d, 0) AS s7, COALESCE(am.purchases_7d, 0) AS p7,
         am.spend_launch AS l_spend, am.purchases_launch AS l_purch, am.revenue_launch AS l_rev,
         am.impressions_launch AS l_imp, am.clicks_launch AS l_clk,
         w.f_spend, w.f_purch, w.f_rev, w.t_spend, w.t_purch, w.t_rev, w.s_spend, w.s_purch, w.s_rev, w.u_spend, w.u_purch, w.u_rev,
         w.f_imp, w.f_clk, w.f_clk_tr, w.f_sp_tr, w.f_purch_t,
         w.d_spend, w.d_purch, w.d_rev, w.d_imp, w.d_clk, w.d_clk_tr, w.d_sp_tr
  FROM tr
  LEFT JOIN ad_metrics am ON am.tracker_row_id = tr.id
  LEFT JOIN win w ON w.tracker_row_id = tr.id
)"""
        # residual = lifetime - every daily row we hold (history before the daily range, restatements): belongs to 'unclassified'
        res = {"spend": "(COALESCE(b.l_spend, 0) - COALESCE(b.d_spend, 0))", "purch": "(COALESCE(b.l_purch, 0) - COALESCE(b.d_purch, 0))",
               "rev": "(COALESCE(b.l_rev, 0) - COALESCE(b.d_rev, 0))", "imp": "(COALESCE(b.l_imp, 0) - COALESCE(b.d_imp, 0))",
               "clk": "(COALESCE(b.l_clk, 0) - COALESCE(b.d_clk, 0))", "clk_tr": "(COALESCE(b.l_clk, 0) - COALESCE(b.d_clk_tr, 0))",
               "sp_tr": "(COALESCE(b.l_spend, 0) - COALESCE(b.d_sp_tr, 0))", "purch_t": "(COALESCE(b.l_purch, 0) - COALESCE(b.d_purch, 0))"}
        if w.stage == "all":      # the lifetime totals ETL stored (per-family rounding already applied)
            spend, purch, rev = "COALESCE(b.l_spend, 0)", "COALESCE(b.l_purch, 0)", "COALESCE(b.l_rev, 0)"
            imp, clk, clk_tr, sp_tr, purch_t = ("COALESCE(b.l_imp, 0)", "COALESCE(b.l_clk, 0)", "COALESCE(b.l_clk, 0)",
                                                "COALESCE(b.l_spend, 0)", "COALESCE(b.l_purch, 0)")
        else:                      # stage list: daily rows of the included stages (+ the residual when 'unclassified' is included)
            inc = "unclassified" in w.stages
            add = lambda col, key: f"COALESCE(b.f_{col}, 0)" + (f" + {res[key]}" if inc else "")      # noqa: E731
            spend, rev = f"round({add('spend', 'spend')}, 2)", f"round({add('rev', 'rev')}, 2)"
            purch = f"round({add('purch', 'purch')}, 0)"
            imp, clk, clk_tr, sp_tr, purch_t = add("imp", "imp"), add("clk", "clk"), add("clk_tr", "clk_tr"), add("sp_tr", "sp_tr"), add("purch_t", "purch_t")
        u_extra = (f" + {res['spend']}", f" + {res['purch']}", f" + {res['rev']}")      # the residual is part of the unclassified bucket

    def only(expr: str) -> str:
        return f"CASE WHEN b.counted THEN {expr} ELSE 0 END"

    c_spend, c_purch, c_rev = (("COALESCE(b.c_spend, 0)", "COALESCE(b.c_purch, 0)", "COALESCE(b.c_rev, 0)") if cw is not None
                               else ("0", "0", "0"))
    sql += f""",
rm AS (
  SELECT b.id, b.product_id, b.concept_id, b.ad_family, b.display, b.family_key, b.sheet_row, b.status, b.ad_type, b.tagged,
         b.cluster, b.icp, b.pain, b.line_code, b.trig, b.color, b.confidence, b.method, b.conf_rank, b.counted, b.is_first,
         (b.counted AND b.am_live) AS live,
         {only(spend)} AS spend, {only(purch)} AS purchases, {only(rev)} AS revenue,
         {only(imp)} AS imp, {only(clk)} AS clk, {only(clk_tr)} AS clk_tr, {only(sp_tr)} AS sp_tr, {only(purch_t)} AS purch_t,
         {only('b.s7')} AS spend_7d, {only('b.p7')} AS purchases_7d,
         {only('COALESCE(b.t_spend, 0)')} AS t_spend, {only('COALESCE(b.t_purch, 0)')} AS t_purch, {only('COALESCE(b.t_rev, 0)')} AS t_rev,
         {only('COALESCE(b.s_spend, 0)')} AS s_spend, {only('COALESCE(b.s_purch, 0)')} AS s_purch, {only('COALESCE(b.s_rev, 0)')} AS s_rev,
         {only('COALESCE(b.u_spend, 0)' + u_extra[0])} AS u_spend, {only('COALESCE(b.u_purch, 0)' + u_extra[1])} AS u_purch,
         {only('COALESCE(b.u_rev, 0)' + u_extra[2])} AS u_rev,
         {only(c_spend)} AS c_spend, {only(c_purch)} AS c_purch, {only(c_rev)} AS c_rev,
         {only(c_imp)} AS c_imp, {only(c_clk)} AS c_clk, {only(c_clk_tr)} AS c_clk_tr, {only(c_sp_tr)} AS c_sp_tr
  FROM b
)
"""
    return sql, params


def _tag_where(cluster: str | None, icp: str | None, pain: str | None, params: dict, prefix: str = "") -> str:
    """' AND <prefix>cluster = %(cluster)s ...' for the filters that are set (column names are fixed literals)."""
    out = ""
    for col, val in (("cluster", cluster), ("icp", icp), ("pain", pain)):
        if val is not None:
            out += f" AND {prefix}{col} = %({col})s"
            params[col] = val
    return out


# --------------------------------------------------------------------------------------------------------------------
# products
# --------------------------------------------------------------------------------------------------------------------


def get_product(conn, slug: str) -> dict:
    p = row_of(conn, "SELECT id, slug, name, tag_set, sort_order FROM products WHERE slug = %s AND active", (slug,))
    if not p:
        raise ApiError(404, "product not found")
    p["tag_label"] = tag_label(p["tag_set"])
    return p


def product_brief(p: dict) -> dict:
    return {"slug": p["slug"], "name": p["name"], "tag_set": p["tag_set"], "tag_label": p["tag_label"]}


def products_list(conn, meta: DataMeta, w: WindowSpec) -> list[dict]:
    sql, params = _rm(meta, w)
    aggs = {r["product_id"]: r for r in rows_of(conn, sql + "SELECT product_id, count(*) AS n_rows, " + AGG_COLS + " FROM rm GROUP BY product_id", params)}
    meta_spend = {r["product_id"]: r["spend_all"] for r in rows_of(
        conn, "SELECT product_id, COALESCE(sum(spend_all), 0) AS spend_all FROM unmatched_meta GROUP BY product_id")}
    out = []
    for p in rows_of(conn, "SELECT id, slug, name, tag_set, sort_order FROM products WHERE active ORDER BY sort_order, id"):
        a = aggs.get(p["id"])
        n_rows = a["n_rows"] if a else 0
        if n_rows == 0 and _f(meta_spend.get(p["id"])) <= 0:
            continue
        out.append({"slug": p["slug"], "name": p["name"], "tag_set": p["tag_set"], "tag_label": tag_label(p["tag_set"]),
                    "rows": n_rows, "m": shape_m(a or ZERO_AGG, w)})
    total = {"spend": round(sum(o["m"]["spend"] for o in out), 2)}
    if w.cmp is not None:
        total["cmp"] = {"spend": round(sum(o["m"]["cmp"]["spend"] for o in out), 2)}
    apply_shares([o["m"] for o in out], total)           # share of all listed products' spend
    return out


def unmatched_summary(conn, product_id: int, meta: DataMeta | None = None, w: WindowSpec | None = None) -> dict:
    """Meta spend that is not on the tracker. spend_7d / spend_30d / live_families / families* are fixed windows (from unmatched_meta);
    window_spend / window_families follow the selected window + stage filter (since launch = every day held)."""
    r = row_of(conn, """
        SELECT COALESCE(sum(spend_7d), 0) AS spend_7d, COALESCE(sum(spend_30d), 0) AS spend_30d,
               count(*) FILTER (WHERE live) AS live_families,
               count(*) FILTER (WHERE spend_7d > 0) AS families,
               count(*) FILTER (WHERE spend_30d > 0) AS families_30d,
               count(*) FILTER (WHERE spend_all > 0) AS families_all
        FROM unmatched_meta WHERE product_id = %s""", (product_id,))
    out = {"spend_7d": f2(r["spend_7d"]), "spend_30d": f2(r["spend_30d"]), "live_families": int(r["live_families"]),
           "families": int(r["families"]), "families_30d": int(r["families_30d"]), "families_all": int(r["families_all"])}
    if meta is not None and w is not None:
        wf, wt = w.span(meta)
        x = row_of(conn, """
            SELECT COALESCE(sum(ad.spend), 0) AS spend, count(DISTINCT ad.meta_family_key) FILTER (WHERE ad.spend > 0) AS families
            FROM unmatched_meta um JOIN ad_daily ad ON ad.meta_family_key = um.meta_family_key
            LEFT JOIN meta_campaigns mc ON mc.campaign_id = ad.campaign_id
            WHERE um.product_id = %(pid)s AND ad.day BETWEEN %(wf)s AND %(wt)s
              AND (CASE WHEN ad.campaign_id = '' THEN 'unclassified' ELSE COALESCE(mc.stage, 'unclassified') END) = ANY(%(stages)s)""",
                   {"pid": product_id, "wf": wf, "wt": wt, "stages": w.stages})
        out.update(window_spend=f2(x["spend"]), window_families=int(x["families"]))
    return out


# --------------------------------------------------------------------------------------------------------------------
# rollup (cluster -> icp -> pain)
# --------------------------------------------------------------------------------------------------------------------


def _taxonomy(conn, product_id: int) -> list[dict]:
    return rows_of(conn, """SELECT line_code, cluster, icp, pain_point, emotional_trigger, cluster_color, sort_order
                            FROM taxonomy_lines WHERE product_id = %s ORDER BY sort_order, id""", (product_id,))


def _skeleton(lines: list[dict], level: str, cluster: str | None, icp: str | None) -> list[dict]:
    """Ordered rows the Communication Overview defines for this level/filters (so lines with no ads still show)."""
    out: list[dict] = []
    seen: set = set()
    for ln in lines:
        if cluster is not None and ln["cluster"] != cluster:
            continue
        if icp is not None and ln["icp"] != icp:
            continue
        if level == "cluster":
            key = (ln["cluster"],)
            label = ln["cluster"]
        elif level == "icp":
            key = (ln["cluster"], ln["icp"])
            label = ln["icp"]
        else:
            key = (ln["cluster"], ln["icp"], ln["pain_point"])
            label = ln["pain_point"]
        if key in seen:
            continue
        seen.add(key)
        out.append({"key": key, "label": label, "cluster": ln["cluster"], "icp": ln["icp"] if level != "cluster" else None,
                    "pain": ln["pain_point"] if level == "pain" else None, "color": ln["cluster_color"],
                    "line_code": ln["line_code"] if level == "pain" else None,
                    "trigger": ln["emotional_trigger"] if level == "pain" else None})
    # child counts straight from the taxonomy
    for s in out:
        if level == "cluster":
            s["child_count"] = len({(l["icp"]) for l in lines if l["cluster"] == s["cluster"]})
        elif level == "icp":
            s["child_count"] = len({l["pain_point"] for l in lines if l["cluster"] == s["cluster"] and l["icp"] == s["label"]})
        else:
            s["child_count"] = None
    return out


def rollup(conn, meta: DataMeta, w: WindowSpec, product: dict, level: str, cluster: str | None, icp: str | None,
           spark: tuple[str, int] | None = None) -> dict:
    if level not in LEVELS:
        raise ApiError(400, f"level must be one of {', '.join(LEVELS)}")
    if level in ("icp", "pain") and cluster is None:
        raise ApiError(400, f"level={level} requires cluster")
    if level == "pain" and icp is None:
        raise ApiError(400, "level=pain requires cluster and icp")
    cols = GROUP_COLS[level]
    collist = ", ".join(cols)                 # from the whitelist
    sql, params = _rm(meta, w, product_id=product["id"])
    where = "WHERE TRUE" + _tag_where(cluster if level != "cluster" else None, icp if level == "pain" else None, None, params)
    sql += (f"SELECT {collist}, GROUPING(cluster) AS is_total, max(line_code) AS line_code, max(trig) AS trig, max(color) AS color,\n"
            f"       count(DISTINCT icp) AS n_icp, count(DISTINCT pain) AS n_pain,\n{AGG_COLS}\n"
            f"FROM rm {where} GROUP BY GROUPING SETS (({collist}), ())")
    data_rows = rows_of(conn, sql, params)
    total_row = next((r for r in data_rows if r["is_total"]), None) or dict(ZERO_AGG, n_icp=0, n_pain=0)
    data = {tuple(r[c] for c in cols): r for r in data_rows if not r["is_total"]}

    rows: list[dict] = []
    used: set = set()

    def make_row(label, key, d, sk):
        m = shape_m(d or ZERO_AGG, w)
        if level == "cluster":
            child = sk["child_count"] if sk and sk["child_count"] is not None else (int(d["n_icp"]) if d else 0)
        elif level == "icp":
            child = sk["child_count"] if sk and sk["child_count"] is not None else (int(d["n_pain"]) if d else 0)
        else:
            child = m["unique_ads"]
        return {"label": label, "cluster": key[0], "icp": key[1] if level != "cluster" else None,
                "pain": key[2] if level == "pain" else None,
                "color": (sk["color"] if sk else None) or (d["color"] if d else None),
                "line_code": (sk["line_code"] if sk else None) or (d["line_code"] if d and level == "pain" else None),
                "trigger": (sk["trigger"] if sk else None) or (d["trig"] if d and level == "pain" else None),
                "child_count": child, "m": m}

    if product["tag_set"] == "linemap":
        for sk in _skeleton(_taxonomy(conn, product["id"]), level, cluster, icp):
            rows.append(make_row(sk["label"], sk["key"], data.get(sk["key"]), sk))
            used.add(sk["key"])
    # groups the taxonomy does not know about (or every group, for tracker-tag products): spend desc, (untagged) last
    extras = sorted((k for k in data if k not in used), key=lambda k: (k[-1] == UNTAGGED, -_f(data[k]["spend"]), k))
    for k in extras:
        rows.append(make_row(k[-1], k, data[k], None))
    rows.sort(key=lambda r: r["label"] == UNTAGGED)          # stable: (untagged) rows last
    total_m = shape_m(total_row, w)
    apply_shares([r["m"] for r in rows] + [total_m], total_m)
    if spark:
        attach_spark_rollup(conn, meta, w, product, level, cluster, icp, rows, spark)
    return {"rows": rows, "total": {"m": total_m}, "unmatched": unmatched_summary(conn, product["id"], meta, w)}


# --------------------------------------------------------------------------------------------------------------------
# families
# --------------------------------------------------------------------------------------------------------------------


def families(conn, meta: DataMeta, w: WindowSpec, product: dict, cluster: str | None, icp: str | None, pain: str | None,
             spark: tuple[str, int] | None = None) -> dict:
    sql, params = _rm(meta, w, product_id=product["id"])
    where = "WHERE TRUE" + _tag_where(cluster, icp, pain, params)
    order = "ORDER BY rm.sheet_row NULLS LAST, rm.id"
    sql += f"""
, thumbs AS (
  SELECT DISTINCT ON (family_key) family_key, id AS thumb_id FROM creative_assets WHERE kind = 'thumb' ORDER BY family_key, idx
), fl AS (
  SELECT family_key, launch_date FROM ad_metrics WHERE product_id = %(pid)s AND is_first_row
), g AS (
  SELECT rm.family_key,
    array_agg(rm.id {order}) AS ad_ids,
    array_agg(rm.concept_id {order}) AS concept_ids,
    (array_agg(rm.display {order}))[1] AS display,
    (array_agg(rm.ad_type {order}))[1] AS ad_type,
    (array_agg(rm.status {order}))[1] AS status,
    (array_agg(rm.cluster {order}))[1] AS cluster,
    (array_agg(rm.icp {order}))[1] AS icp,
    (array_agg(rm.pain {order}))[1] AS pain,
    (array_agg(rm.line_code {order}))[1] AS line_code,
    min(rm.conf_rank) AS conf_rank,
    (array_agg(rm.method ORDER BY rm.conf_rank NULLS LAST, rm.sheet_row NULLS LAST, rm.id))[1] AS method,
    bool_or(rm.is_first) AS counted_here,
    {AGG_COLS}
  FROM rm {where}
  GROUP BY rm.family_key
)
SELECT g.*, th.thumb_id, fl.launch_date FROM g LEFT JOIN thumbs th ON th.family_key = g.family_key
LEFT JOIN fl ON fl.family_key = g.family_key
ORDER BY g.spend DESC, g.display COLLATE "C", g.family_key COLLATE "C"      -- same order as the tree (codepoint order)
"""
    raw = rows_of(conn, sql, params)
    out = []
    for r in raw:
        out.append({
            "family_key": r["family_key"], "display": r["display"], "ad_ids": list(r["ad_ids"]), "primary_id": r["ad_ids"][0],
            "concept_ids": list(r["concept_ids"]), "n_rows": len(r["ad_ids"]), "ad_type": r["ad_type"], "status": r["status"],
            "launch_date": r["launch_date"].isoformat() if r["launch_date"] else None,
            "thumb_asset_id": r["thumb_id"], "confidence": CONF_NAME.get(r["conf_rank"]), "method": r["method"],
            "counted_here": bool(r["counted_here"]),
            "cluster": r["cluster"], "icp": r["icp"], "pain": r["pain"], "line_code": r["line_code"],
            "m": shape_m(r, w),
        })
    total_m = shape_m(sum_aggs(raw, unique_ads=len(raw)), w)
    apply_shares([r["m"] for r in out] + [total_m], total_m)
    if spark:
        attach_spark_families(conn, meta, w, product, cluster, icp, pain, out, spark)
    return {"rows": out, "total": {"m": total_m}}


# --------------------------------------------------------------------------------------------------------------------
# tree (dropdown table): cluster -> icp -> pain -> family in ONE query, depth-first rows
# --------------------------------------------------------------------------------------------------------------------

TREE_ROOTS = ("cluster", "icp", "pain", "family")
_TREE_COLS = (("cluster",), ("cluster", "icp"), ("cluster", "icp", "pain"), ("cluster", "icp", "pain", "family_key"))   # whitelist
_ALL_TREE_COLS = ("cluster", "icp", "pain", "family_key")


def _tax_children(lines: list[dict]) -> tuple[dict, dict]:
    """Taxonomy skeleton: parent key tuple -> ordered child key tuples, and key tuple -> {color, line_code, trigger}."""
    children: dict[tuple, list] = {}
    info: dict[tuple, dict] = {}
    seen: set = set()
    for ln in lines:
        k0, k1, k2 = (ln["cluster"],), (ln["cluster"], ln["icp"]), (ln["cluster"], ln["icp"], ln["pain_point"])
        for parent, key in (((), k0), (k0, k1), (k1, k2)):
            if key not in seen:
                seen.add(key)
                children.setdefault(parent, []).append(key)
                info[key] = {"color": ln["cluster_color"], "line_code": ln["line_code"] if len(key) == 3 else None,
                             "trigger": ln["emotional_trigger"] if len(key) == 3 else None}
    return children, info


def tree(conn, meta: DataMeta, w: WindowSpec, product: dict, root: str, cluster: str | None, icp: str | None, pain: str | None,
         depth: int, spark: tuple[str, int] | None = None) -> dict:
    if root not in TREE_ROOTS:
        raise ApiError(400, f"root must be one of {', '.join(TREE_ROOTS)}")
    if not 0 <= depth <= 3:
        raise ApiError(400, "depth must be 0, 1, 2 or 3")
    root_level = TREE_ROOTS.index(root)
    for need, name, val in ((1, "cluster", cluster), (2, "icp", icp), (3, "pain", pain)):
        if root_level >= need and val is None:
            raise ApiError(400, f"root={root} requires " + ", ".join(n for lvl, n, _v in ((1, "cluster", cluster), (2, "icp", icp), (3, "pain", pain)) if lvl <= root_level))
    last = min(3, root_level + depth)
    sets_sql = ", ".join("(" + ", ".join(_TREE_COLS[lv]) + ")" for lv in range(root_level, last + 1)) + ", ()"
    deepest = _TREE_COLS[last]
    select_cols = ", ".join(c if c in deepest else f"NULL::text AS {c}" for c in _ALL_TREE_COLS)
    sql, params = _rm(meta, w, product_id=product["id"])
    where = "WHERE TRUE" + _tag_where(cluster if root_level >= 1 else None, icp if root_level >= 2 else None,
                                       pain if root_level >= 3 else None, params)
    order = "ORDER BY sheet_row NULLS LAST, id"
    sql += f"""
SELECT {select_cols},
  count(DISTINCT icp) AS n_icp, count(DISTINCT pain) AS n_pain, max(line_code) AS line_code, max(trig) AS trig, max(color) AS color,
  (array_agg(id {order}))[1] AS primary_id, (array_agg(display {order}))[1] AS display,
  (array_agg(ad_type {order}))[1] AS ad_type, (array_agg(status {order}))[1] AS status,
  min(conf_rank) AS conf_rank, bool_or(is_first) AS counted_here,
  {AGG_COLS}
FROM rm {where}
GROUP BY GROUPING SETS ({sets_sql})
"""
    raw = rows_of(conn, sql, params)

    groups: list[dict[tuple, dict]] = [{}, {}, {}, {}]
    total_row = dict(ZERO_AGG, n_icp=0, n_pain=0)
    for r in raw:
        if r["cluster"] is None and r["family_key"] is None:
            total_row = r
            continue
        lv = 3 if r["family_key"] is not None else 2 if r["pain"] is not None else 1 if r["icp"] is not None else 0
        key = tuple(r[c] for c in _TREE_COLS[lv])
        groups[lv][key] = r
    by_parent: list[dict[tuple, list]] = [{}, {}, {}, {}]
    for lv in range(4):
        for key in groups[lv]:
            by_parent[lv].setdefault(key[:-1], []).append(key)

    sk_children, sk_info = _tax_children(_taxonomy(conn, product["id"])) if product["tag_set"] == "linemap" else ({}, {})

    def ordered(lv: int, parent: tuple) -> list[tuple]:
        data_keys = by_parent[lv].get(parent, [])
        if lv == 3:
            return sorted(data_keys, key=lambda k: (-_f(groups[3][k]["spend"]), groups[3][k]["display"] or "", k[-1]))
        sk = sk_children.get(parent, [])
        skset = set(sk)
        extras = sorted((k for k in data_keys if k not in skset), key=lambda k: (k[-1] == UNTAGGED, -_f(groups[lv][k]["spend"]), k))
        out = sk + extras
        out.sort(key=lambda k: k[-1] == UNTAGGED)                      # stable: (untagged) last
        return out

    # root rows = the root level under the parent the filters imply: () for clusters, (cluster,) for icps, (cluster, icp) for pains ...
    root_parent = tuple([cluster, icp, pain][:root_level])

    fam_keys = [k[-1] for k in groups[3]]
    thumbs, ad_counts = {}, {}
    if fam_keys:
        thumbs = {r["family_key"]: r["id"] for r in rows_of(conn, """
            SELECT DISTINCT ON (family_key) family_key, id FROM creative_assets
            WHERE kind = 'thumb' AND family_key = ANY(%s) ORDER BY family_key, idx""", (fam_keys,))}
        ad_counts = {r["fk"]: int(r["n"]) for r in rows_of(conn, """
            SELECT m.tracker_family_key AS fk, count(DISTINCT ma.ad_id) AS n
            FROM meta_ads ma JOIN meta_family_map m ON m.meta_family_key = ma.meta_family_key
            WHERE m.tracker_family_key = ANY(%s) GROUP BY 1""", (fam_keys,))}

    out: list[dict] = []

    def key_str(key: tuple) -> str:
        return "|".join(key)

    def emit(lv: int, key: tuple, parent_key: str | None) -> None:
        r = groups[lv].get(key)
        d = r if r is not None else dict(ZERO_AGG, n_icp=0, n_pain=0)
        m = shape_m(d, w)
        info = sk_info.get(key) if lv < 3 else None
        if lv == 0:
            child = len(sk_children.get(key, [])) if key in sk_info else int(d["n_icp"])
        elif lv == 1:
            child = len(sk_children.get(key, [])) if key in sk_info else int(d["n_pain"])
        elif lv == 2:
            child = m["unique_ads"]
        else:
            child = ad_counts.get(key[-1], 0) if (r is not None and r["counted_here"]) else 0
        row = {
            "key": key_str(key), "parent_key": parent_key, "level": lv,
            "label": (r["display"] if lv == 3 and r is not None else key[-1]),
            "cluster": key[0], "icp": key[1] if lv >= 1 else None, "pain": key[2] if lv >= 2 else None,
            "family_key": key[3] if lv == 3 else None,
            "primary_id": r["primary_id"] if lv == 3 and r is not None else None,
            "color": (info or {}).get("color") or (r["color"] if r is not None and lv < 3 else None),
            "line_code": ((info or {}).get("line_code") or (r["line_code"] if r is not None else None)) if lv == 2 else None,
            "trigger": ((info or {}).get("trigger") or (r["trig"] if r is not None else None)) if lv == 2 else None,
            "confidence": CONF_NAME.get(r["conf_rank"]) if lv == 3 and r is not None else None,
            "ad_type": r["ad_type"] if lv == 3 and r is not None else None,
            "status": r["status"] if lv == 3 and r is not None else None,
            "thumb_asset_id": thumbs.get(key[3]) if lv == 3 else None,
            "has_children": child > 0, "child_count": child,
            "counted_here": bool(r["counted_here"]) if lv == 3 and r is not None else None,
            "m": m,
        }
        out.append(row)
        if lv < last:
            for ck in ordered(lv + 1, key):
                emit(lv + 1, ck, row["key"])

    for rk in ordered(root_level, root_parent):
        emit(root_level, rk, None)

    total_m = shape_m(total_row, w)
    apply_shares([r["m"] for r in out] + [total_m], total_m)         # every row relative to the root total of this response
    if spark:
        attach_spark_tree(conn, meta, w, product, root_level, last, cluster, icp, pain, out, spark)
    return {"rows": out, "total": {"m": total_m}}


# --------------------------------------------------------------------------------------------------------------------
# Meta ads (ad_id grain)
# --------------------------------------------------------------------------------------------------------------------

REMAINDER_NAME = "(days without ad-level detail)"
NO_CAMPAIGN_NAME = "(no campaign data)"


def _cmp_window(w: WindowSpec) -> WindowSpec | None:
    """Window B when comparison is on and B has at least one day of data (else treated as zeros)."""
    return w.cmp if (w.cmp is not None and not w.cmp.is_empty) else None


def _window_params(meta: DataMeta, w: WindowSpec, params: dict) -> WindowSpec | None:
    """Adds wf/wt (A), cf/ct (B) and lo/hi (the one day range that covers both) for non-launch windows; returns the usable B."""
    cw = _cmp_window(w)
    if not w.is_launch:
        lo, hi = w.date_from, w.date_to
        if cw is not None:
            lo, hi = min(lo, cw.date_from), max(hi, cw.date_to)
            params.update(cf=cw.date_from, ct=cw.date_to)
        params.update(wf=w.date_from, wt=w.date_to, lo=lo, hi=hi)
    return cw


# numeric columns every `parts` select carries, in this order: window A, then window B, then days_active
_A_KEYS = ("spend", "purchases", "revenue", "imp", "clk", "clk_tr", "sp_tr", "purch_t")
_B_KEYS = ("c_spend", "c_purch", "c_rev", "c_imp", "c_clk", "c_clk_tr", "c_sp_tr")
_ALL_KEYS = _A_KEYS + _B_KEYS


def _row_exprs(alias: str) -> dict:
    """What each metric sums over on ad_daily rows. Traffic bases follow family_daily: CTR uses rows with impressions, CPC/CPM use the
    spend of rows with traffic data (impressions known, or link clicks > 0)."""
    return {
        "spend": f"{alias}.spend", "purchases": f"{alias}.purchases", "revenue": f"{alias}.conv_value",
        "imp": f"{alias}.impressions", "clk": f"{alias}.link_clicks",
        "clk_tr": f"CASE WHEN {alias}.impressions IS NOT NULL THEN {alias}.link_clicks END",
        "sp_tr": f"CASE WHEN {alias}.impressions IS NOT NULL OR {alias}.link_clicks > 0 THEN {alias}.spend END",
        "purch_t": f"{alias}.purchases",
    }


def _pre_cols(w: WindowSpec, cw: WindowSpec | None) -> str:
    """First pass over ad_daily rows (alias ad), already grouped per (ad, Meta family, campaign): window A sums (all days for since-launch),
    window B sums, days_active. Stage logic is applied afterwards on the few groups instead of on every daily row."""
    e = _row_exprs("ad")
    fa = "" if w.is_launch else f" FILTER (WHERE ad.day BETWEEN %(wf)s AND %(wt)s)"
    cols = [f"COALESCE(sum({e[k]}){fa}, 0) AS {k}" for k in _A_KEYS]
    b_src = dict(zip(_B_KEYS, ("spend", "purchases", "revenue", "imp", "clk", "clk_tr", "sp_tr")))
    for k in _B_KEYS:
        cols.append(f"COALESCE(sum({e[b_src[k]]}) FILTER (WHERE ad.day BETWEEN %(cf)s AND %(ct)s), 0) AS {k}"
                    if cw is not None else f"0::numeric AS {k}")
    act = "ad.spend > 0" + ("" if w.is_launch else " AND ad.day BETWEEN %(wf)s AND %(wt)s")
    cols.append(f"count(*) FILTER (WHERE {act}) AS days_active")
    return ",\n         ".join(cols)


def _post_cols(w: WindowSpec, signed_stage: str | None = None) -> str:
    """Second pass over the pre-aggregated groups `da`. signed_stage (since launch with a stage list that includes 'unclassified'):
    sum(included stages) - sum(every stage), i.e. the daily rows count for their stage and are taken out of the lifetime row that is added
    separately (so the residual lands in 'unclassified')."""
    if signed_stage:
        cols = [f"COALESCE(sum(da.{k}) FILTER (WHERE {signed_stage}), 0) - COALESCE(sum(da.{k}), 0) AS {k}" for k in _A_KEYS]
        cols += [f"0::numeric AS {k}" for k in _B_KEYS]
    else:
        cols = [f"COALESCE(sum(da.{k}), 0) AS {k}" for k in _ALL_KEYS]
    cols.append("0::bigint AS days_active" if w.is_launch else "COALESCE(sum(da.days_active), 0)::bigint AS days_active")      # since launch: see dact
    return ",\n         ".join(cols)


def meta_ads(conn, meta: DataMeta, w: WindowSpec, product: dict, *, cluster: str | None = None, icp: str | None = None,
             pain: str | None = None, family_key: str | None = None, campaign_id: str | None = None,
             spark: tuple[str, int] | None = None) -> dict:
    """Individual Meta ads that make up the selection. Families are included where their first tracker row is in the selection, so the ad
    rows (plus the remainder rows) sum to the same totals the rollup / families tables show.

    Since launch (v1.5) per ad = that ad's API lifetime row. With a stage list the ad's daily rows of the included stages are used, and
    (lifetime - all daily rows of the ad) is added when 'unclassified' is included - the same partition the family totals use."""
    params: dict[str, Any] = {"d0": meta.daily_start, "dt": meta.data_through, "stages": w.stages, "campaign": campaign_id,
                              "d7": meta.data_through - dt.timedelta(days=6), "d1": meta.data_through - dt.timedelta(days=1)}
    sql = "WITH " + _tr_cte(product["id"], family_key, params)
    tagw = _tag_where(cluster, icp, pain, params, prefix="tr.")
    have_tag_filter = any(v is not None for v in (cluster, icp, pain, family_key))
    cw = _window_params(meta, w, params)
    launch = w.is_launch
    incl_uncl = "unclassified" in w.stages
    sql += f""",
sel AS (
  SELECT DISTINCT ON (tr.family_key) tr.family_key, tr.id AS primary_id, tr.display, tr.cluster, tr.icp, tr.pain,
         am.spend_launch AS l_spend, am.purchases_launch AS l_purch, am.revenue_launch AS l_rev,
         am.impressions_launch AS l_imp, am.clicks_launch AS l_clk
  FROM tr JOIN ad_metrics am ON am.tracker_row_id = tr.id AND am.is_first_row
  WHERE NOT starts_with(tr.family_key, 'id:') {tagw}
  ORDER BY tr.family_key, tr.sheet_row NULLS LAST, tr.id
),
thumbs AS (
  SELECT DISTINCT ON (family_key) family_key, id AS thumb_id FROM creative_assets WHERE kind = 'thumb' ORDER BY family_key, idx
),
ad7 AS (
  SELECT ad_id, sum(spend) FILTER (WHERE day >= %(d7)s) AS s7, sum(spend) FILTER (WHERE day >= %(d1)s) AS s1
  FROM ad_daily WHERE day >= %(d7)s GROUP BY ad_id
),
"""
    camp_pred = "(%(campaign)s::text IS NULL OR ad.campaign_id = %(campaign)s)"
    stage_expr = "CASE WHEN da.campaign_id = '' THEN 'unclassified' ELSE COALESCE(mc.stage, 'unclassified') END"
    stage_in = f"{stage_expr} = ANY(%(stages)s)"
    day_pred = "ad.day <= %(dt)s" if launch else "ad.day BETWEEN %(lo)s AND %(hi)s"
    aid_expr = "CASE WHEN starts_with(da.ad_id, 'fam:') THEN NULL ELSE da.ad_id END"
    # one pass over ad_daily for tracker families AND (when nothing is selected by tag/family) Meta families that are not on the tracker
    um_join = "" if have_tag_filter else "LEFT JOIN unmatched_meta um ON um.meta_family_key = da.meta_family_key AND um.product_id = %(pid)s"
    fk_expr = "s.family_key" if have_tag_filter else "COALESCE(s.family_key, um.meta_family_key)"
    member = "s.family_key IS NOT NULL" if have_tag_filter else "(s.family_key IS NOT NULL OR um.meta_family_key IS NOT NULL)"
    # since launch: the lifetime row carries the all-stage total, so with every stage included the daily rows only matter for days_active
    names = ", ".join(f"0::numeric AS {k}" for k in _ALL_KEYS)
    parts: list[str] = [f"""
  SELECT NULL::text AS aid, NULL::text AS fk, NULL::boolean AS matched, NULL::text AS mf, NULL::text AS camp, {names}, 0::bigint AS days_active
  WHERE FALSE"""]                       # names the columns of the UNION
    if not launch or not (incl_uncl and w.stage == "all"):
        signed = stage_in if (launch and incl_uncl) else None
        where_stage = "" if signed else f"AND {stage_in}"
        sql += f"""
da AS (   -- one pass over ad_daily, grouped to (ad, Meta family, campaign): a few thousand groups instead of tens of thousands of days
  SELECT ad.ad_id COLLATE "C" AS ad_id, ad.meta_family_key COLLATE "C" AS meta_family_key, ad.campaign_id COLLATE "C" AS campaign_id,
         max(ad.meta_family) AS mf, {_pre_cols(w, cw)}
  FROM ad_daily ad
  WHERE {day_pred} AND {camp_pred}
  GROUP BY 1, 2, 3
),
"""
        parts.append(f"""
  SELECT {aid_expr} AS aid, {fk_expr} AS fk, (s.family_key IS NOT NULL) AS matched, max(da.mf) AS mf, max(NULLIF(da.campaign_id, '')) AS camp,
         {_post_cols(w, signed)}
  FROM da
  LEFT JOIN meta_family_map m ON m.meta_family_key = da.meta_family_key
  LEFT JOIN sel s ON s.family_key = m.tracker_family_key
  {um_join}
  LEFT JOIN meta_campaigns mc ON mc.campaign_id = da.campaign_id
  WHERE {member} {where_stage}
  GROUP BY 1, 2, 3""")
    if launch and incl_uncl:
        sql += """
mlf AS (   -- API lifetime per Meta ad of the selected families (no cutoff, no launch-date gating)
  SELECT ml.ad_id, m.tracker_family_key AS fk, ml.spend, ml.purchases, ml.conv_value, ml.impressions, ml.link_clicks, ma.campaign_id AS camp
  FROM meta_ad_lifetime ml LEFT JOIN meta_ads ma ON ma.ad_id = ml.ad_id
  JOIN meta_family_map m ON m.meta_family_key = COALESCE(ma.meta_family_key, ml.meta_family_key)
  WHERE m.tracker_family_key IN (SELECT family_key FROM sel)
),
"""
        parts.append("""
  SELECT mlf.ad_id, mlf.fk, TRUE, NULL::text, NULL::text,
         mlf.spend, mlf.purchases, mlf.conv_value, COALESCE(mlf.impressions, 0), COALESCE(mlf.link_clicks, 0), COALESCE(mlf.link_clicks, 0),
         mlf.spend, mlf.purchases, 0::numeric, 0::numeric, 0::numeric, 0::numeric, 0::numeric, 0::numeric, 0::numeric, 0::bigint
  FROM mlf WHERE (%(campaign)s::text IS NULL OR mlf.camp = %(campaign)s)""")
        if campaign_id is None:      # what the family total holds beyond its ads' lifetime rows goes to the remainder row
            parts.append("""
  SELECT NULL::text, s.family_key, TRUE, NULL::text, NULL::text,
         COALESCE(s.l_spend, 0) - COALESCE(x.spend, 0), COALESCE(s.l_purch, 0) - COALESCE(x.purchases, 0), COALESCE(s.l_rev, 0) - COALESCE(x.revenue, 0),
         COALESCE(s.l_imp, 0) - COALESCE(x.imp, 0), COALESCE(s.l_clk, 0) - COALESCE(x.clk, 0), COALESCE(s.l_clk, 0) - COALESCE(x.clk, 0),
         COALESCE(s.l_spend, 0) - COALESCE(x.spend, 0), COALESCE(s.l_purch, 0) - COALESCE(x.purchases, 0),
         0::numeric, 0::numeric, 0::numeric, 0::numeric, 0::numeric, 0::numeric, 0::numeric, 0::bigint
  FROM sel s LEFT JOIN (SELECT fk, sum(spend) AS spend, sum(purchases) AS purchases, sum(conv_value) AS revenue,
                               sum(COALESCE(impressions, 0)) AS imp, sum(COALESCE(link_clicks, 0)) AS clk FROM mlf GROUP BY fk) x ON x.fk = s.family_key""")
    if not have_tag_filter:       # no tag/family selected: "every ad of the product" also covers Meta ads not on the tracker
        if launch and incl_uncl:
            parts.append("""
  SELECT ml.ad_id, um.meta_family_key, FALSE, max(um.meta_family), NULL::text,
         ml.spend, ml.purchases, ml.conv_value, COALESCE(ml.impressions, 0), COALESCE(ml.link_clicks, 0), COALESCE(ml.link_clicks, 0),
         ml.spend, ml.purchases, 0::numeric, 0::numeric, 0::numeric, 0::numeric, 0::numeric, 0::numeric, 0::numeric, 0::bigint
  FROM meta_ad_lifetime ml LEFT JOIN meta_ads ma ON ma.ad_id = ml.ad_id
  JOIN unmatched_meta um ON um.meta_family_key = COALESCE(ma.meta_family_key, ml.meta_family_key) AND um.product_id = %(pid)s
  WHERE (%(campaign)s::text IS NULL OR ma.campaign_id = %(campaign)s)
  GROUP BY ml.ad_id, um.meta_family_key, ml.spend, ml.purchases, ml.conv_value, ml.impressions, ml.link_clicks""")
    sums = ", ".join(f"sum({k}) AS {k}" for k in _ALL_KEYS)
    nonzero = " OR ".join(f"o.{k} <> 0" for k in _ALL_KEYS)
    # days_active since launch = days with spend among the daily rows we hold (one cheap indexed count for just these ads)
    dact_cte = (""",
dact AS (SELECT ad_id, count(*) FILTER (WHERE spend > 0) AS n FROM ad_daily WHERE ad_id IN (SELECT aid FROM o WHERE aid IS NOT NULL) GROUP BY ad_id)"""
                if launch else "")
    days_expr = "COALESCE(dact.n, 0)" if launch else "o.days_active"
    dact_join = "LEFT JOIN dact ON dact.ad_id = o.aid" if launch else ""
    sql += f"""
parts AS ({" UNION ALL ".join(parts)}),
o AS (
  SELECT aid, fk, bool_or(matched) AS matched, max(mf) AS mf, max(camp) AS camp, {sums}, sum(days_active) AS days_active
  FROM parts GROUP BY aid, fk
){dact_cte}
SELECT o.*, {days_expr} AS days_active_final,
       ma.ad_name, ma.campaign_id AS ma_campaign, ma.adset_name, ma.account, ma.status AS ad_status, ma.created_date,
       mc.name AS campaign_name, mc.account AS campaign_account, COALESCE(mc.stage, 'unclassified') AS camp_stage,
       COALESCE(a7.s7, 0) AS spend_7d, COALESCE(a7.s1, 0) > 0 AS live,
       s.primary_id, s.display, s.cluster, s.icp, s.pain, th.thumb_id
FROM o
{dact_join}
LEFT JOIN meta_ads ma ON ma.ad_id = o.aid
LEFT JOIN meta_campaigns mc ON mc.campaign_id = COALESCE(NULLIF(ma.campaign_id, ''), o.camp)
LEFT JOIN ad7 a7 ON a7.ad_id = o.aid
LEFT JOIN sel s ON s.family_key = o.fk AND o.matched
LEFT JOIN thumbs th ON th.family_key = o.fk AND o.matched
WHERE ({nonzero})
ORDER BY o.spend DESC, o.c_spend DESC, o.aid NULLS LAST
"""
    raw = rows_of(conn, sql, params)

    zero = lambda: {k: Decimal(0) for k in _ALL_KEYS}   # noqa: E731
    rem = {True: zero(), False: zero()}          # family-grain (no ad id) days: tracker families / Meta families not on the tracker
    ads: list[dict] = []
    tot = zero()
    for r in raw:
        for k in tot:
            tot[k] += r[k]
        if r["aid"] is None:
            for k in tot:
                rem[bool(r["matched"])][k] += r[k]
            continue
        campaign = r["ma_campaign"] or r["camp"]
        ads.append({
            "ad_id": r["aid"],
            "ad_name": r["ad_name"] or r["mf"] or r["aid"],
            "family_key": r["fk"], "display": (r["display"] if r["matched"] else None) or r["mf"] or r["fk"],
            "primary_id": r["primary_id"] if r["matched"] else None,
            "thumb_asset_id": r["thumb_id"] if r["matched"] else None,
            "on_tracker": bool(r["matched"]),
            "campaign_id": campaign or None, "campaign_name": r["campaign_name"], "stage": r["camp_stage"] if campaign else "unclassified",
            "adset_name": r["adset_name"], "account": r["account"] or r["campaign_account"], "status": r["ad_status"],
            "created_date": r["created_date"].isoformat() if r["created_date"] else None,
            "tag": {"cluster": r["cluster"], "icp": r["icp"], "pain": r["pain"]} if r["matched"] else None,
            **window_block(side_a(r), w, side_b(r)),
            "spend_7d": f2(r["spend_7d"]), "live": bool(r["live"]), "days_active": int(r["days_active_final"] or 0),
        })

    for on_tracker in (True, False):             # the remainder keeps tracker and non-tracker days apart so each side still adds up
        rm_ = rem[on_tracker]
        # purchases are rounded per family in the totals but fractional per ad, so a remainder of <1 purchase alone is rounding dust, not a row
        if (any(abs(_f(rm_[k])) >= 0.005 for k in ("spend", "revenue", "c_spend", "c_rev")) or any(abs(_f(rm_[k])) >= 1 for k in ("purchases", "c_purch"))
                or any(_f(rm_[k]) != 0 for k in ("imp", "clk", "c_imp", "c_clk"))):
            ads.append({
                "ad_id": None, "ad_name": REMAINDER_NAME, "family_key": family_key,
                "display": REMAINDER_NAME if on_tracker else REMAINDER_NAME[:-1] + ", not on the tracker)",
                "primary_id": None, "thumb_asset_id": None, "on_tracker": on_tracker, "campaign_id": None, "campaign_name": None,
                "stage": "unclassified", "adset_name": None, "account": None, "status": None, "created_date": None, "tag": None,
                **window_block(side_a(rm_), w, side_b(rm_)),
                "spend_7d": 0.0, "live": False, "days_active": 0,
            })

    total = {"ads": sum(1 for a in ads if a["ad_id"]), **window_block(side_a(tot), w, side_b(tot)),
             "spend_7d": f2(sum(Decimal(str(a["spend_7d"])) for a in ads)), "live": sum(1 for a in ads if a["live"])}
    apply_shares(ads + [total], total)
    if spark:
        attach_spark_ads(conn, meta, w, product, ads, spark)
    return {"rows": ads, "total": total}


# --------------------------------------------------------------------------------------------------------------------
# campaigns + stage-daily (a product's ad_daily rows = mapped tracker families + unmatched families assigned by regex)
# --------------------------------------------------------------------------------------------------------------------

_PRODUCT_FAMILIES = """
pf AS (
  SELECT m.meta_family_key
  FROM meta_family_map m
  WHERE m.tracker_family_key IN (SELECT family_key FROM tracker_rows WHERE product_id = %(pid)s)
  UNION
  SELECT meta_family_key FROM unmatched_meta WHERE product_id = %(pid)s
)
"""


def _filtered_sums(alias: str, pred_a: str, pred_b: str) -> str:
    """The A_KEYS / B_KEYS sums over rows aliased `alias`, each restricted by its own predicate."""
    e = _row_exprs(alias)
    cols = [f"COALESCE(sum({e[k]}) FILTER (WHERE {pred_a}), 0) AS {k}" for k in _A_KEYS]
    src = ("spend", "purchases", "revenue", "imp", "clk", "clk_tr", "sp_tr")
    cols += [f"COALESCE(sum({e[sk]}) FILTER (WHERE {pred_b}), 0) AS {k}" for k, sk in zip(_B_KEYS, src)]
    return ",\n               ".join(cols)


def campaigns(conn, meta: DataMeta, w: WindowSpec, product: dict, spark: tuple[str, int] | None = None) -> dict:
    wf, wt = w.span(meta)
    cw = _cmp_window(w)
    d7 = meta.data_through - dt.timedelta(days=6)
    d1 = meta.data_through - dt.timedelta(days=1)
    params = {"pid": product["id"], "wf": wf, "wt": wt, "d7": d7, "d1": d1, "stages": w.stages,
              "lo": min(wf, d7, cw.date_from) if cw is not None else min(wf, d7),
              "cf": cw.date_from if cw is not None else wf, "ct": cw.date_to if cw is not None else wt}
    in_cmp = "(day BETWEEN %(cf)s AND %(ct)s)" if cw is not None else "FALSE"
    base = f"""
WITH {_PRODUCT_FAMILIES},
r AS (
  SELECT ad.ad_id, ad.meta_family_key, ad.campaign_id, ad.day, ad.spend, ad.purchases, ad.conv_value, ad.impressions, ad.link_clicks,
         CASE WHEN ad.campaign_id = '' THEN 'unclassified' ELSE COALESCE(mc.stage, 'unclassified') END AS stage,
         starts_with(ad.ad_id, 'fam:') AS is_fam
  FROM ad_daily ad JOIN pf ON pf.meta_family_key = ad.meta_family_key
  LEFT JOIN meta_campaigns mc ON mc.campaign_id = ad.campaign_id
  WHERE ad.day >= %(lo)s
),
f AS (SELECT *, (day BETWEEN %(wf)s AND %(wt)s) AS in_win, {in_cmp} AS in_cmp FROM r)
"""
    # ONE scan of the product's ad_daily rows, grouped by (campaign, stage); the stage cards, the campaign rows and the total are all derived
    # from those few groups in Python (a campaign has a single stage, so the stage filter simply keeps or drops whole groups)
    groups = rows_of(conn, base + f"""
        SELECT f.campaign_id, f.stage, mc.name, mc.account,
               count(DISTINCT f.ad_id) FILTER (WHERE f.in_win AND NOT f.is_fam AND f.spend > 0) AS ads,
               count(DISTINCT f.meta_family_key) FILTER (WHERE f.in_win AND f.spend > 0) AS families,
               {_filtered_sums('f', 'f.in_win', 'f.in_cmp')},
               COALESCE(sum(f.spend) FILTER (WHERE f.day >= %(d7)s), 0) AS spend_7d,
               COALESCE(sum(f.spend) FILTER (WHERE f.day >= %(d1)s), 0) > 0 AS live,
               max(f.day) FILTER (WHERE f.spend > 0) AS last_day
        FROM f LEFT JOIN meta_campaigns mc ON mc.campaign_id = f.campaign_id
        GROUP BY f.campaign_id, f.stage, mc.name, mc.account""", params)
    keep = set(w.stages)

    def fold(gs, with_counts=True):
        acc = {k: Decimal(0) for k in _ALL_KEYS}
        acc.update(spend_7d=Decimal(0), ads=0, campaigns=0)
        for g in gs:
            for k in _ALL_KEYS:
                acc[k] += g[k]
            acc["spend_7d"] += g["spend_7d"]
            acc["ads"] += int(g["ads"])
            if g["campaign_id"] != "" and g["spend"] > 0:
                acc["campaigns"] += 1
        return acc

    by_stage = []
    for st in STAGES:
        gs = [g for g in groups if g["stage"] == st]
        if not gs and st == "unclassified":
            continue
        acc = fold(gs)
        by_stage.append({"stage": st, "campaigns": acc["campaigns"], "ads": acc["ads"], **window_block(side_a(acc), w, side_b(acc)),
                         "spend_7d": f2(acc["spend_7d"])})

    shown = [g for g in groups if g["stage"] in keep and any(g[k] != 0 for k in ("spend", "purchases", "c_spend", "c_purch"))]
    shown.sort(key=lambda g: (-g["spend"], -g["c_spend"], g["campaign_id"]))
    rows = [{"campaign_id": g["campaign_id"], "name": NO_CAMPAIGN_NAME if g["campaign_id"] == "" else (g["name"] or g["campaign_id"]),
             "account": g["account"], "stage": "unclassified" if g["campaign_id"] == "" else g["stage"],
             "ads": int(g["ads"]), "families": int(g["families"]), **window_block(side_a(g), w, side_b(g)),
             "spend_7d": f2(g["spend_7d"]), "live": bool(g["live"]),
             "last_day": g["last_day"].isoformat() if g["last_day"] else None} for g in shown]

    tr_ = fold([g for g in groups if g["stage"] in keep])
    total = {"campaigns": tr_["campaigns"], "ads": tr_["ads"], **window_block(side_a(tr_), w, side_b(tr_)), "spend_7d": f2(tr_["spend_7d"])}
    apply_shares(rows + [total], total)
    apply_shares(by_stage, {"spend": sum(c["spend"] for c in by_stage),
                            **({"cmp": {"spend": sum(c["cmp"]["spend"] for c in by_stage)}} if w.cmp is not None else {})})
    if spark:
        attach_spark_campaigns(conn, meta, w, product, rows, spark)
    return {"by_stage": by_stage, "rows": rows, "total": total}


def stage_daily(conn, meta: DataMeta, product: dict, df: dt.date, dtt: dt.date, stages: list[str] | None = None) -> list[dict]:
    """Spend per day split by stage; stages outside the include list are reported as 0."""
    inc = set(stages or STAGES)
    got = rows_of(conn, f"""
        WITH {_PRODUCT_FAMILIES}
        SELECT ad.day,
               CASE WHEN ad.campaign_id = '' THEN 'unclassified' ELSE COALESCE(mc.stage, 'unclassified') END AS stage,
               sum(ad.spend) AS spend
        FROM ad_daily ad JOIN pf ON pf.meta_family_key = ad.meta_family_key
        LEFT JOIN meta_campaigns mc ON mc.campaign_id = ad.campaign_id
        WHERE ad.day BETWEEN %(f)s AND %(t)s GROUP BY 1, 2""", {"pid": product["id"], "f": df, "t": dtt})
    by: dict[tuple, Decimal] = {(r["day"], r["stage"]): r["spend"] for r in got}
    out, d = [], df
    while d <= dtt:
        out.append({"day": d.isoformat(), **{st: (f2(by.get((d, st))) if st in inc else 0.0) for st in STAGES}})
        d += dt.timedelta(days=1)
    return out


# --------------------------------------------------------------------------------------------------------------------
# unmatched Meta families
# --------------------------------------------------------------------------------------------------------------------


def unmatched(conn, meta: DataMeta, w: WindowSpec, product: dict) -> dict:
    """Meta families that are not on the tracker. A window (or, for since launch, every day held) of daily rows."""
    wf, wt = w.span(meta)
    cw = _cmp_window(w)
    params = {"pid": product["id"], "wf": wf, "wt": wt, "stages": w.stages,
              "lo": min(wf, cw.date_from) if cw is not None else wf, "hi": max(wt, cw.date_to) if cw is not None else wt,
              "cf": cw.date_from if cw is not None else wf, "ct": cw.date_to if cw is not None else wt}
    in_cmp = "(ad.day BETWEEN %(cf)s AND %(ct)s)" if cw is not None else "FALSE"
    raw = rows_of(conn, f"""
        SELECT um.meta_family_key, um.meta_family, um.spend_7d, um.live,
               {_filtered_sums('ad', 'ad.day BETWEEN %(wf)s AND %(wt)s', in_cmp)}
        FROM unmatched_meta um
        JOIN ad_daily ad ON ad.meta_family_key = um.meta_family_key
        LEFT JOIN meta_campaigns mc ON mc.campaign_id = ad.campaign_id
        WHERE um.product_id = %(pid)s AND ad.day BETWEEN %(lo)s AND %(hi)s
          AND (CASE WHEN ad.campaign_id = '' THEN 'unclassified' ELSE COALESCE(mc.stage, 'unclassified') END) = ANY(%(stages)s)
        GROUP BY um.meta_family_key, um.meta_family, um.spend_7d, um.live
        ORDER BY spend DESC, c_spend DESC, um.meta_family""", params)
    raw = [r for r in raw if r["spend"] != 0 or r["purchases"] != 0 or r["c_spend"] != 0 or r["c_purch"] != 0]
    rows = [{"meta_family": r["meta_family"], "meta_family_key": r["meta_family_key"], **window_block(side_a(r), w, side_b(r)),
             "spend_7d": f2(r["spend_7d"]), "live": bool(r["live"])} for r in raw]
    tot = {k: sum((r[k] for r in raw), Decimal(0)) for k in _ALL_KEYS}
    total = {**window_block(side_a(tot), w, side_b(tot)), "spend_7d": f2(sum((r["spend_7d"] for r in raw), Decimal(0))), "families": len(rows)}
    apply_shares(rows + [total], total)
    return {"rows": rows, "total": total}


# --------------------------------------------------------------------------------------------------------------------
# ad detail
# --------------------------------------------------------------------------------------------------------------------


def _whole_pct(v) -> int:
    return int(float(v) * 100 + 0.5)


def reliability_stats(conn, slug: str) -> dict | None:
    """The product's own blind re-tag agreement numbers (app_meta `reliability:<slug>`), None when there are none."""
    r = row_of(conn, "SELECT value FROM app_meta WHERE key = %s", (f"reliability:{slug}",))
    if r:
        try:
            v = json.loads(r["value"])
            if isinstance(v, dict):
                return v
        except (TypeError, ValueError):
            pass
    return _VB_RELIABILITY if slug == "vita-bull" else None


def reliability_note(method: str | None, confidence: str | None, stats: dict | None = None) -> str:
    parts = []
    m = (method or "").lower()
    if "text only" in m or "copy only" in m:
        parts.append("Tagged from copy only (no image).")
    elif "thumbnail" in m and "no audio" in m and "frames" not in m:
        parts.append("Tagged from a thumbnail only.")
    by_conf = (stats or {}).get("by_confidence") or {}
    label = {"low": "Low", "medium": "Medium", "high": "High"}.get(confidence or "")
    if label:
        line = (by_conf.get(confidence) or {}).get("line")
        parts.append(f"{label} confidence — blind re-tag agreed ~{_whole_pct(line)}%." if line is not None else f"{label} confidence.")
    ov = (stats or {}).get("overall") or {}
    if all(ov.get(k) is not None for k in ("line", "icp", "cluster")):
        parts.append(f"Blind re-tag agreement overall: line {_whole_pct(ov['line'])}%, ICP {_whole_pct(ov['icp'])}%, cluster {_whole_pct(ov['cluster'])}%.")
    return " ".join(parts)


def _copy_only(text: str | None, lines: list[dict]) -> dict | None:
    """Copy-only pick is stored as 'ICP - Pain point (Trigger)'. Resolve it to a taxonomy line when we can."""
    if not text or not text.strip():
        return None
    t = text.strip()
    best = None
    for ln in lines:
        key = f"{ln['icp']} - {ln['pain_point']}"
        if t == key or t.startswith(key + " ("):
            if best is None or len(key) > len(best[0]):
                best = (key, ln["line_code"])
    return {"line_code": best[1] if best else None, "label": t}


def _fam_window_metrics(conn, meta: DataMeta, w: WindowSpec, product_id: int, family_key: str) -> tuple[dict, dict | None, dict | None]:
    """(window A block, window B block | None, delta | None) for one family."""
    sql, params = _rm(meta, w, product_id=product_id, family_key=family_key)
    a = row_of(conn, sql + "SELECT " + AGG_COLS + " FROM rm", params)
    block = side_fields(side_a(a), w.days)
    if w.cmp is None:
        return block, None, None
    cmp, delta = cmp_blocks(side_a(a), w, side_b(a), b_active=a["c_active"])
    return block, cmp, delta


def ad_detail(conn, meta: DataMeta, w: WindowSpec, row_id: int) -> dict:
    r = row_of(conn, """
        SELECT t.*, p.slug, p.name AS product_name, p.tag_set, p.id AS pid
        FROM tracker_rows t JOIN products p ON p.id = t.product_id WHERE t.id = %s""", (row_id,))
    if not r:
        raise ApiError(404, "ad not found")
    fk, pid = r["family_key"], r["pid"]
    tags = {t["tag_set"]: t for t in rows_of(conn, """
        SELECT t.*, tl.line_code, tl.cluster AS tl_cluster, tl.icp AS tl_icp, tl.pain_point AS tl_pain, tl.emotional_trigger AS tl_trigger,
               tl.story AS tl_story, tl.id AS tl_id
        FROM ad_tags t LEFT JOIN taxonomy_lines tl ON tl.id = t.line_id
        WHERE t.tracker_row_id = %s AND t.is_current""", (row_id,))}
    tg = tags.get(r["tag_set"])
    lines = _taxonomy(conn, pid) if r["tag_set"] == "linemap" else []

    tag = None
    line = None
    if tg:
        tag = {
            "tag_set": tg["tag_set"], "version": tg["version"], "line_code": tg["line_code"],
            "cluster": tg["cluster"] or tg["tl_cluster"], "icp": tg["icp"] or tg["tl_icp"], "pain_point": tg["pain_point"] or tg["tl_pain"],
            "emotional_trigger": tg["emotional_trigger"] or tg["tl_trigger"], "confidence": tg["confidence"], "method": tg["method"],
            "seen": tg["seen"], "why": tg["why"], "runner_up": tg["runner_up"],
            "copy_only_line": _copy_only(tg["copy_only_line"], lines),
            "reliability_note": reliability_note(tg["method"], tg["confidence"], reliability_stats(conn, r["slug"])) if tg["tag_set"] == "linemap" else None,
        }
        if tg["tl_id"]:
            ing = rows_of(conn, "SELECT ingredient, attacks, supports FROM line_ingredients WHERE line_id = %s ORDER BY sort_order, id", (tg["tl_id"],))
            line = {"line_code": tg["line_code"], "story": tg["tl_story"], "emotional_trigger": tg["emotional_trigger"] or tg["tl_trigger"],
                    "ingredients": [{"ingredient": i["ingredient"], "attacks": i["attacks"], "supports": i["supports"]} for i in ing]}

    content = row_of(conn, "SELECT * FROM family_content WHERE family_key = %s", (fk,)) or {}
    assets = rows_of(conn, "SELECT id, kind, idx FROM creative_assets WHERE family_key = %s ORDER BY kind, idx", (fk,))
    thumb = next((a["id"] for a in assets if a["kind"] == "thumb"), None)
    frames = [a["id"] for a in assets if a["kind"] == "frame"]

    siblings = rows_of(conn, """
        SELECT t.id, t.concept_id, t.status, tl.line_code, COALESCE(a.cluster, tl.cluster) AS cluster,
               COALESCE(a.icp, tl.icp) AS icp, COALESCE(a.pain_point, tl.pain_point) AS pain_point
        FROM tracker_rows t
        JOIN products p ON p.id = t.product_id
        LEFT JOIN ad_tags a ON a.tracker_row_id = t.id AND a.is_current AND a.tag_set = p.tag_set
        LEFT JOIN taxonomy_lines tl ON tl.id = a.line_id
        WHERE t.product_id = %s AND t.family_key = %s AND t.id <> %s
        ORDER BY t.sheet_row NULLS LAST, t.id""", (pid, fk, row_id))

    first = row_of(conn, "SELECT * FROM ad_metrics WHERE product_id = %s AND family_key = %s AND is_first_row", (pid, fk))
    blank = fk.startswith("id:")
    if first and not blank:
        family_metrics = {
            "launch": side_fields({"spend": first["spend_launch"], "purchases": first["purchases_launch"], "revenue": first["revenue_launch"],
                                   "imp": first["impressions_launch"], "clk": first["clicks_launch"], "clk_tr": first["clicks_launch"],
                                   "sp_tr": first["spend_launch"], "purch_t": first["purchases_launch"]}, None),
            "d7": simple_metrics(first["spend_7d"], first["purchases_7d"], first["revenue_7d"]),
            "d30": simple_metrics(first["spend_30d"], first["purchases_30d"], first["revenue_30d"]),
            "live": bool(first["live"]),
        }
    else:
        z = simple_metrics(0, 0, 0)
        family_metrics = {"launch": z, "d7": z, "d30": z, "live": False}
    if not blank:
        family_metrics["window"], cmp_block, delta_block = _fam_window_metrics(conn, meta, w, pid, fk)
    else:
        family_metrics["window"] = side_fields(ZERO_SIDE, w.days)
        cmp_block = delta_block = None
        if w.cmp is not None:
            cmp_block, delta_block = cmp_blocks(ZERO_SIDE, w, ZERO_SIDE, b_active=0)
    if w.cmp is not None:
        family_metrics["compare"] = cmp_block
        family_metrics["delta"] = delta_block

    daily_rows = {d["day"]: d for d in rows_of(conn, """
        SELECT day, sum(spend) AS spend, sum(purchases) AS purchases, sum(revenue) AS revenue, sum(link_clicks) AS link_clicks,
               sum(impressions) AS impressions
        FROM family_daily WHERE family_key = %s AND day BETWEEN %s AND %s AND stage = ANY(%s) GROUP BY day""",
        (fk, meta.daily_start, meta.data_through, w.stages))}
    daily, d = [], meta.daily_start
    while d <= meta.data_through:
        x = daily_rows.get(d)
        daily.append({"day": d.isoformat(), "spend": f2(x["spend"]) if x else 0.0, "purchases": num(x["purchases"]) if x else 0,
                      "revenue": f2(x["revenue"]) if x else 0.0, "link_clicks": num(x["link_clicks"]) if x else 0,
                      "impressions": num(x["impressions"]) if x else 0})
        d += dt.timedelta(days=1)

    product = {"id": pid, "slug": r["slug"], "name": r["product_name"], "tag_set": r["tag_set"]}
    mads = meta_ads(conn, meta, w, product, family_key=fk) if not blank else {"rows": [], "total": {}}
    camp: dict[Any, dict] = {}
    for a in mads["rows"]:
        key = a["campaign_id"]
        c = camp.setdefault(key, {"campaign_id": key, "name": a["campaign_name"] or (a["ad_name"] if key is None else key),
                                  "stage": a["stage"], "spend": Decimal(0), "purchases": Decimal(0), "revenue": Decimal(0)})
        c["spend"] += Decimal(str(a["spend"]))
        c["purchases"] += Decimal(str(a["purchases"]))
        c["revenue"] += Decimal(str(a["revenue"]))
    campaigns_out = sorted(({**c, "spend": f2(c["spend"]), "purchases": num(c["purchases"]), "revenue": f2(c["revenue"])} for c in camp.values()),
                           key=lambda c: -c["spend"])
    apply_shares(campaigns_out, {"spend": round(sum(c["spend"] for c in campaigns_out), 2)})

    return {
        "id": r["id"], "product": {"slug": r["slug"], "name": r["product_name"]}, "concept_id": r["concept_id"], "ad_family": r["ad_family"],
        "family_key": fk, "sheet_row": r["sheet_row"], "status": r["status"], "ad_type": r["ad_type"], "style": r["style"], "vendor": r["vendor"],
        "launch_date": r["launch_date"].isoformat() if r["launch_date"] else None, "concept_name": r["concept_name"],
        "description": r["description"], "hypothesis": r["hypothesis"], "meta_ad_name": r["meta_ad_name"],
        "story_url": content.get("story_url"), "asset_url": r["asset_url"],
        "tag": tag,
        "tracker_tag": {"cluster": r["tracker_cluster"], "icp": r["tracker_icp"], "pain_point": r["tracker_pain"],
                        "emotion": r["tracker_emotion"], "ingredient": r["tracker_ingredient"]},
        "line": line,
        "content": {
            "primary_text": list(r["primary_text"] or []), "headlines": list(r["headlines"] or []), "description": r["ad_description"],
            "meta_body": content.get("meta_body"), "meta_title": content.get("meta_title"), "meta_desc": content.get("meta_desc"),
            "transcript": content.get("transcript") or r["transcript"],
            "is_video": content["is_video"] if content.get("is_video") is not None else ((r["ad_type"] or "").upper() == "VID"),
        },
        "assets": {"thumb": thumb, "frames": frames},
        "siblings": [{"id": s["id"], "concept_id": s["concept_id"], "line_code": s["line_code"], "cluster": s["cluster"], "icp": s["icp"],
                      "pain_point": s["pain_point"], "status": s["status"]} for s in siblings],
        "metrics": {"counted_on_this_row": bool(first and first["tracker_row_id"] == r["id"] and not blank), "family": family_metrics},
        "meta_ads": mads["rows"],
        "campaigns": campaigns_out,
        "daily": daily,
    }


# --------------------------------------------------------------------------------------------------------------------
# trend lines + sparklines (v1.6)
# --------------------------------------------------------------------------------------------------------------------

TREND_GRAINS = ("day", "week")
TREND_SPLITS = ("none", "stage", "children")
SPARK_METRICS = ("spend", "purchases", "revenue", "roas", "cpa", "impressions", "link_clicks", "ctr", "cpc", "cpm", "conv_rate")
MAX_SPARK_DAYS = 60
POINT_KEYS = ("spend", "purchases", "revenue", "roas", "cpa", "impressions", "link_clicks", "ctr", "cpc", "cpm", "conv_rate")
OTHER_KEY = "__other__"
NOT_ON_TRACKER = "(not on tracker)"
MAX_TREND_CHILDREN = 8

_FD_SUM_EXPR = {"spend": "fd.spend", "purchases": "fd.purchases", "revenue": "fd.revenue", "imp": "fd.impressions", "clk": "fd.link_clicks",
                "clk_tr": "fd.clicks_tr", "sp_tr": "fd.spend_tr", "purch_t": "fd.purchases"}


def _vec() -> dict:
    return {k: Decimal(0) for k in _A_KEYS}


def _vec_add(a: dict, b: dict) -> None:
    for k in _A_KEYS:
        a[k] += b[k]


def _vec_of(r: dict) -> dict:
    return {k: r[k] if r[k] is not None else Decimal(0) for k in _A_KEYS}


def _point(t: dt.date, vec: dict, partial: bool = False) -> dict:
    """One chart point. Ratios come from the bucket's sums (never averaged); unknown / zero denominators are null."""
    f = side_fields(vec, None)
    p: dict[str, Any] = {"t": t.isoformat()}
    if partial:
        p["partial"] = True
    p.update({k: f[k] for k in POINT_KEYS})
    return p


def _buckets(day_vecs: dict, d_from: dt.date, d_to: dt.date, grain: str) -> list[tuple[dt.date, bool, dict]]:
    """Continuous x-axis: every day (or every ISO week, Monday start) of the range, zeros where nothing happened. A week is partial when the
    range cuts it (first/last week)."""
    out: list[tuple[dt.date, bool, dict]] = []
    if grain == "day":
        d = d_from
        while d <= d_to:
            out.append((d, False, day_vecs.get(d) or _vec()))
            d += dt.timedelta(days=1)
        return out
    mon = d_from - dt.timedelta(days=d_from.weekday())
    while mon <= d_to:
        v = _vec()
        for i in range(7):
            day = mon + dt.timedelta(days=i)
            if d_from <= day <= d_to and day in day_vecs:
                _vec_add(v, day_vecs[day])
        out.append((mon, mon < d_from or mon + dt.timedelta(days=6) > d_to, v))
        mon += dt.timedelta(days=7)
    return out


def _points(day_vecs: dict, d_from: dt.date, d_to: dt.date, grain: str) -> list[dict]:
    return [_point(t, v, partial) for t, partial, v in _buckets(day_vecs, d_from, d_to, grain)]


def _child_level(cluster, icp, pain, family_key) -> int | None:
    """Level of the rows that split=children draws: clusters for the whole product, then ICPs, pain points, families; none below a family."""
    if family_key is not None:
        return None
    return 3 if pain is not None else 2 if icp is not None else 1 if cluster is not None else 0


_CKEY_SQL = {
    0: ("tr.cluster", "tr.cluster"),
    1: ("tr.cluster || '|' || tr.icp", "tr.icp"),
    2: ("tr.cluster || '|' || tr.icp || '|' || tr.pain", "tr.pain"),
    3: ("tr.cluster || '|' || tr.icp || '|' || tr.pain || '|' || tr.family_key", "tr.display"),
}


def trend(conn, meta: DataMeta, w: WindowSpec, product: dict, *, cluster: str | None = None, icp: str | None = None, pain: str | None = None,
          family_key: str | None = None, campaign_id: str | None = None, grain: str = "day", split: str = "none",
          children_keys: list[str] | None = None) -> dict:
    """Time series for a scope (tag filters + family + campaign), window A and (compare) window B, in one pass over family_daily
    (or ad_daily when a campaign is selected, since campaigns only exist at ad grain)."""
    if grain not in TREND_GRAINS:
        raise ApiError(400, f"grain must be one of {', '.join(TREND_GRAINS)}")
    if split not in TREND_SPLITS:
        raise ApiError(400, f"split must be one of {', '.join(TREND_SPLITS)}")
    if children_keys is not None and len(children_keys) > 12:
        raise ApiError(400, "children takes at most 12 keys")
    df, dtt = (meta.daily_start, meta.data_through) if w.is_launch else (w.date_from, w.date_to)
    cw = _cmp_window(w)
    lo, hi = (min(df, cw.date_from), max(dtt, cw.date_to)) if cw is not None else (df, dtt)
    level = _child_level(cluster, icp, pain, family_key) if split == "children" else None
    want_children = split == "children" and level is not None

    params: dict[str, Any] = {"pid": product["id"], "stages": w.stages, "lo": lo, "hi": hi, "wf": df, "wt": dtt, "campaign": campaign_id,
                              "other": OTHER_KEY}
    sql = "WITH " + _tr_cte(product["id"], family_key, params)
    tagw = _tag_where(cluster, icp, pain, params, prefix="tr.")
    ckey_sql, label_sql = _CKEY_SQL[level] if want_children else ("''::text", "''::text")
    sql += f""",
sel AS (
  SELECT DISTINCT ON (tr.family_key) tr.family_key, {ckey_sql} AS ckey, {label_sql} AS clabel, tr.color AS color
  FROM tr JOIN ad_metrics am ON am.tracker_row_id = tr.id AND am.is_first_row
  WHERE NOT starts_with(tr.family_key, 'id:') {tagw}
  ORDER BY tr.family_key, tr.sheet_row NULLS LAST, tr.id
),"""
    if campaign_id is None:
        sums = ", ".join(f"{_FD_SUM_EXPR[k]} AS {k}" for k in _A_KEYS)
        sql += f"""
d AS (
  SELECT s.ckey, s.clabel, s.color, fd.day, fd.stage, {sums}
  FROM family_daily fd JOIN sel s ON s.family_key = fd.family_key
  WHERE fd.day BETWEEN %(lo)s AND %(hi)s AND fd.stage = ANY(%(stages)s)
),"""
    else:
        no_tags = all(v is None for v in (cluster, icp, pain, family_key))
        e = _row_exprs("ad")
        sums = ", ".join(f"{e[k]} AS {k}" for k in _A_KEYS)
        um_join = "LEFT JOIN unmatched_meta um ON um.meta_family_key = ad.meta_family_key AND um.product_id = %(pid)s" if no_tags else ""
        member = "(s.family_key IS NOT NULL OR um.meta_family_key IS NOT NULL)" if no_tags else "s.family_key IS NOT NULL"
        stage_expr = "CASE WHEN ad.campaign_id = '' THEN 'unclassified' ELSE COALESCE(mc.stage, 'unclassified') END"
        off_tracker = f"'{NOT_ON_TRACKER}'" if want_children else "''"
        sql += f"""
d AS (
  SELECT COALESCE(s.ckey, {off_tracker}) AS ckey, COALESCE(s.clabel, {off_tracker}) AS clabel, s.color, ad.day, {stage_expr} AS stage, {sums}
  FROM ad_daily ad
  LEFT JOIN meta_family_map m ON m.meta_family_key = ad.meta_family_key
  LEFT JOIN sel s ON s.family_key = m.tracker_family_key
  {um_join}
  LEFT JOIN meta_campaigns mc ON mc.campaign_id = ad.campaign_id
  WHERE ad.campaign_id = %(campaign)s AND ad.day BETWEEN %(lo)s AND %(hi)s AND {member} AND {stage_expr} = ANY(%(stages)s)
),"""
    agg = ", ".join(f"sum({k}) AS {k}" for k in _A_KEYS)
    sql += f"""
dg AS (SELECT ckey, max(clabel) AS clabel, max(color) AS color, day, stage, {agg} FROM d GROUP BY ckey, day, stage)"""
    if want_children:
        if children_keys:
            params["keys"] = children_keys
            top = "SELECT unnest(%(keys)s::text[]) AS ckey"
        else:
            top = (f"SELECT ckey FROM (SELECT ckey, sum(spend) FILTER (WHERE day BETWEEN %(wf)s AND %(wt)s) AS s FROM dg GROUP BY ckey) t "
                   f"ORDER BY s DESC NULLS LAST, ckey LIMIT {MAX_TREND_CHILDREN}")
        sql += f""",
top AS ({top})
SELECT CASE WHEN ckey IN (SELECT ckey FROM top) THEN ckey ELSE %(other)s END AS gkey, max(clabel) AS clabel, max(color) AS color, day, stage, {agg}
FROM dg GROUP BY 1, day, stage"""
    else:
        sql += f"""
SELECT ''::text AS gkey, NULL::text AS clabel, NULL::text AS color, day, stage, {agg} FROM dg GROUP BY day, stage"""
    raw = rows_of(conn, sql, params)

    total: dict[dt.date, dict] = {}
    by_stage: dict[str, dict[dt.date, dict]] = {st: {} for st in w.stages}
    by_child: dict[str, dict[dt.date, dict]] = {}
    child_meta: dict[str, tuple] = {}
    child_spend: dict[str, Decimal] = {}
    for r in raw:
        v = _vec_of(r)
        day = r["day"]
        _vec_add(total.setdefault(day, _vec()), v)
        _vec_add(by_stage.setdefault(r["stage"], {}).setdefault(day, _vec()), v)
        if want_children:
            g = r["gkey"]
            _vec_add(by_child.setdefault(g, {}).setdefault(day, _vec()), v)
            child_meta.setdefault(g, (r["clabel"], r["color"]))
            if df <= day <= dtt:
                child_spend[g] = child_spend.get(g, Decimal(0)) + v["spend"]

    out: dict[str, Any] = {"grain": grain, "scope": {"cluster": cluster, "icp": icp, "pain": pain, "family_key": family_key, "campaign_id": campaign_id},
                           "points": _points(total, df, dtt, grain)}
    if cw is not None or w.cmp is not None:
        n = len(out["points"])
        cmp_pts: list[dict | None] = _points(_day_slice(total, cw), cw.date_from, cw.date_to, grain) if cw is not None else []
        out["cmp_points"] = (cmp_pts + [None] * n)[:n]                       # aligned to A by position, null-padded
    if split == "stage":
        out["by_stage"] = {st: _points(by_stage.get(st, {}), df, dtt, grain) for st in w.stages}
    if split == "children":
        kids = []
        order = sorted((g for g in by_child if g != OTHER_KEY), key=lambda g: (-child_spend.get(g, Decimal(0)), g))
        if children_keys:                                                       # explicit keys keep the caller's order
            order = [k for k in children_keys if k in by_child and k != OTHER_KEY]
        for g in order:
            label, color = child_meta[g]
            kids.append({"key": g, "label": label, "color": color, "points": _points(by_child[g], df, dtt, grain)})
        if OTHER_KEY in by_child:
            kids.append({"key": OTHER_KEY, "label": "Other", "color": None, "points": _points(by_child[OTHER_KEY], df, dtt, grain)})
        out["children"] = kids
    return out


def _day_slice(day_vecs: dict, win: WindowSpec) -> dict:
    return {d: v for d, v in day_vecs.items() if win.date_from <= d <= win.date_to}


# ---- sparklines -----------------------------------------------------------------------------------------------------


def parse_spark(args) -> tuple[str, int] | None:
    """spark=<metric>&spark_days=N (1..60, default 30). Absent spark -> None."""
    metric = (args.get("spark") or "").strip().lower()
    if not metric:
        return None
    if metric not in SPARK_METRICS:
        raise ApiError(400, f"spark must be one of {', '.join(SPARK_METRICS)}")
    raw = (args.get("spark_days") or "30").strip()
    if not raw.isdigit() or not 1 <= int(raw) <= MAX_SPARK_DAYS:
        raise ApiError(400, f"spark_days must be an integer from 1 to {MAX_SPARK_DAYS}")
    return metric, int(raw)


def _spark_range(meta: DataMeta, w: WindowSpec, n: int) -> tuple[dt.date, dt.date]:
    end = meta.data_through if w.is_launch else w.date_to
    return end - dt.timedelta(days=n - 1), end


# metric -> SQL over the dense per-day sums (spend, purchases, revenue, imp, clk, clk_tr, sp_tr, purch_t; zeros on empty days). These mirror
# side_fields(): unknown traffic is null, ratios come from the day's own sums and are null when the denominator is 0.
_SPARK_SQL = {
    "spend": "round(spend, 2)", "purchases": "round(purchases, 2)", "revenue": "round(revenue, 2)",
    "roas": "CASE WHEN spend = 0 THEN NULL ELSE round(revenue / spend, 4) END",
    "cpa": "CASE WHEN purchases = 0 THEN NULL ELSE round(spend / purchases, 2) END",
    "impressions": "CASE WHEN imp = 0 AND (clk > 0 OR spend > 0) THEN NULL ELSE imp END",
    "link_clicks": "CASE WHEN clk = 0 AND imp = 0 AND spend > 0 THEN NULL ELSE clk END",
    "ctr": "CASE WHEN imp = 0 THEN NULL ELSE round(clk_tr / imp, 5) END",
    "cpc": "CASE WHEN clk = 0 OR (sp_tr = 0 AND spend > 0) THEN NULL ELSE round(sp_tr / clk, 4) END",
    "cpm": "CASE WHEN imp = 0 OR (sp_tr = 0 AND spend > 0) THEN NULL ELSE round(sp_tr * 1000 / imp, 4) END",
    "conv_rate": "CASE WHEN clk = 0 THEN NULL ELSE round(purch_t / clk, 5) END",
}


def _spark_tail(metric: str) -> str:
    """SQL appended after a CTE `g(gk, day, spend, ..., purch_t)` of per-group-day sums: one dense row per group with a float8[] of n days
    (zero / null filled) computed from the day's sums, all in the same statement."""
    cols = ", ".join(f"COALESCE(g.{k}, 0) AS {k}" for k in _A_KEYS)
    return f""",
keys AS (SELECT DISTINCT gk COLLATE "C" AS gk FROM g),
days AS (SELECT d::date AS day FROM generate_series(%(sf)s::date, %(st)s::date, interval '1 day') d),
dense AS (SELECT k.gk, dy.day, {cols} FROM keys k CROSS JOIN days dy LEFT JOIN g ON g.gk COLLATE "C" = k.gk AND g.day = dy.day)
SELECT gk, array_agg(({_SPARK_SQL[metric]})::float8 ORDER BY day) AS vals FROM dense GROUP BY gk"""


def _spark_arrays(conn, sql: str, params: dict, metric: str) -> dict[str, list]:
    return {r["gk"]: list(r["vals"]) for r in rows_of(conn, sql + _spark_tail(metric), params)}


def _blank_spark(n: int, metric: str) -> list:
    """What a group with no data in the range shows: zeros for additive metrics (and unknown-free traffic counts), null for ratios."""
    zero = side_fields(_vec(), None)[metric]
    return [zero] * n


_TREE_KEY_COLS = ("cluster", "icp", "pain", "family_key")


def _spark_family_arrays(conn, meta: DataMeta, w: WindowSpec, product: dict, spark: tuple[str, int], sets: list[tuple], *,
                         cluster=None, icp=None, pain=None, family_key=None) -> dict[str, list]:
    """Sparkline arrays of first-row families grouped by each of `sets` (tuples of whitelisted column names), keyed by '|'-joined key."""
    metric, n = spark
    sf, st = _spark_range(meta, w, n)
    params: dict[str, Any] = {"stages": w.stages, "sf": sf, "st": st}
    sql = "WITH " + _tr_cte(product["id"], family_key, params)
    tagw = _tag_where(cluster, icp, pain, params, prefix="tr.")
    sets_sql = ", ".join("(" + ", ".join(f"fs.{c}" for c in cols) + ", fd.day)" for cols in sets)
    used = {c for cols in sets for c in cols}                      # a column that is in no grouping set cannot be referenced at all
    gk = "concat_ws('|', " + ", ".join(f"fs.{c}" for c in _TREE_KEY_COLS if c in used) + ")"
    sums = ", ".join(f"sum({_FD_SUM_EXPR[k]}) AS {k}" for k in _A_KEYS)
    sql += f""",
fs AS (
  SELECT DISTINCT ON (tr.family_key) tr.family_key, tr.cluster, tr.icp, tr.pain
  FROM tr JOIN ad_metrics am ON am.tracker_row_id = tr.id AND am.is_first_row
  WHERE NOT starts_with(tr.family_key, 'id:') {tagw}
  ORDER BY tr.family_key, tr.sheet_row NULLS LAST, tr.id
),
g AS (
  SELECT {gk} AS gk, fd.day, {sums}
  FROM fs JOIN family_daily fd ON fd.family_key = fs.family_key
  WHERE fd.day BETWEEN %(sf)s AND %(st)s AND fd.stage = ANY(%(stages)s)
  GROUP BY GROUPING SETS ({sets_sql})
)"""
    return _spark_arrays(conn, sql, params, metric)


def attach_spark_rollup(conn, meta, w, product, level, cluster, icp, rows, spark):
    cols = GROUP_COLS[level]
    arrays = _spark_family_arrays(conn, meta, w, product, spark, [cols], cluster=cluster if level != "cluster" else None,
                                  icp=icp if level == "pain" else None)
    for r in rows:
        r["spark"] = arrays.get("|".join(r[c] for c in cols)) or _blank_spark(spark[1], spark[0])


def attach_spark_families(conn, meta, w, product, cluster, icp, pain, rows, spark):
    arrays = _spark_family_arrays(conn, meta, w, product, spark, [("family_key",)], cluster=cluster, icp=icp, pain=pain)
    for r in rows:
        r["spark"] = arrays.get(r["family_key"]) or _blank_spark(spark[1], spark[0])


def attach_spark_tree(conn, meta, w, product, root_level, last, cluster, icp, pain, rows, spark):
    sets = [_TREE_COLS[lv] for lv in range(root_level, last + 1)]
    arrays = _spark_family_arrays(conn, meta, w, product, spark, sets, cluster=cluster if root_level >= 1 else None,
                                  icp=icp if root_level >= 2 else None, pain=pain if root_level >= 3 else None)
    for r in rows:
        r["spark"] = arrays.get(r["key"]) or _blank_spark(spark[1], spark[0])


def _spark_ad_rows(conn, meta, w, product, spark, key_sql: str, where_sql: str, params: dict) -> dict[str, list]:
    """Sparkline arrays straight from ad_daily rows (campaign / ad grain). key_sql and where_sql are fixed fragments from this module."""
    metric, n = spark
    sf, st = _spark_range(meta, w, n)
    e = _row_exprs("ad")
    sums = ", ".join(f"sum({e[k]}) AS {k}" for k in _A_KEYS)
    stage_expr = "CASE WHEN ad.campaign_id = '' THEN 'unclassified' ELSE COALESCE(mc.stage, 'unclassified') END"
    sql = f"""
        WITH {_PRODUCT_FAMILIES},
        g AS (
          SELECT ({key_sql}) COLLATE "C" AS gk, ad.day, {sums}
          FROM ad_daily ad LEFT JOIN meta_campaigns mc ON mc.campaign_id = ad.campaign_id
          WHERE ad.day BETWEEN %(sf)s AND %(st)s AND {stage_expr} = ANY(%(stages)s) AND {where_sql}
          GROUP BY 1, ad.day
        )"""
    return _spark_arrays(conn, sql, {**params, "pid": product["id"], "sf": sf, "st": st, "stages": w.stages}, metric)


def attach_spark_campaigns(conn, meta, w, product, rows, spark):
    arrays = _spark_ad_rows(conn, meta, w, product, spark, "ad.campaign_id", "ad.meta_family_key IN (SELECT meta_family_key FROM pf)", {})
    for r in rows:
        r["spark"] = arrays.get(r["campaign_id"]) or _blank_spark(spark[1], spark[0])


def attach_spark_ads(conn, meta, w, product, rows, spark):
    ids = sorted({r["ad_id"] for r in rows if r["ad_id"]})
    arrays = _spark_ad_rows(conn, meta, w, product, spark, "ad.ad_id", "ad.ad_id = ANY(%(ids)s)", {"ids": ids}) if ids else {}
    for r in rows:
        r["spark"] = (arrays.get(r["ad_id"]) or _blank_spark(spark[1], spark[0])) if r["ad_id"] else None      # remainder rows have no series


# --------------------------------------------------------------------------------------------------------------------
# status / media
# --------------------------------------------------------------------------------------------------------------------


def status(conn, meta: DataMeta, n_products: int) -> dict:
    last = row_of(conn, """SELECT job, finished_at, status FROM etl_runs WHERE finished_at IS NOT NULL ORDER BY finished_at DESC, id DESC LIMIT 1""")
    ok = row_of(conn, """SELECT finished_at FROM etl_runs WHERE finished_at IS NOT NULL AND status = 'ok' ORDER BY finished_at DESC, id DESC LIMIT 1""")
    return {
        "data_through": meta.data_through.isoformat(), "daily_start": meta.daily_start.isoformat(),
        "lifetime_cutoff": meta.lifetime_cutoff.isoformat() if meta.lifetime_cutoff else None,
        "last_refresh": ({"job": last["job"], "finished_at": last["finished_at"].isoformat(), "status": last["status"]} if last else None),
        "last_ok_at": ok["finished_at"].isoformat() if ok else None,
        "products": n_products,
    }


def media(conn, asset_id: int) -> tuple[bytes, str] | None:
    r = row_of(conn, "SELECT bytes, mime FROM creative_assets WHERE id = %s", (asset_id,))
    return (bytes(r["bytes"]), r["mime"]) if r else None
