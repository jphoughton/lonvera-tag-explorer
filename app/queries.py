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
GLOBAL_RELIABILITY = "Blind re-tag agreement overall: line 79%, ICP 84%, cluster 88%."

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
    lifetime_cutoff: dt.date
    data_through: dt.date


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
    return DataMeta(
        daily_start=dt.date.fromisoformat(start),
        lifetime_cutoff=dt.date.fromisoformat(kv.get("lifetime_cutoff") or "2026-09-17"),
        data_through=dt.date.fromisoformat(through),
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

    def info(self, meta: DataMeta) -> dict:
        return {
            "kind": self.kind, "name": self.name, "label": self.label,
            "from": self.date_from.isoformat() if self.date_from else None,
            "to": self.date_to.isoformat() if self.date_to else None,
            "days": self.days, "clamped": self.clamped, "note": self.note, "stage": self.stage, "stages": self.stages,
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
                    "history before the lifetime cutoff and sheet-only days have no stage and are excluded.")
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
  count(*) FILTER (WHERE c_spend > 0)                AS c_active
"""

ZERO_AGG = {k: Decimal(0) for k in (
    "unique_ads", "live_now", "active_ads", "spend", "purchases", "revenue", "spend_7d", "purchases_7d",
    "t_spend", "t_purch", "t_rev", "s_spend", "s_purch", "s_rev", "u_spend", "u_purch", "u_rev",
    "c_spend", "c_purch", "c_rev", "c_active")}


# ---- window A / window B (comparison) helpers -----------------------------------------------------------------------


def _pd(x: Any, days: int | None) -> float | None:
    return None if not days else round(_f(x) / days, 2)


def _pct(a: Any, b: Any) -> float | None:
    """(A - B) / B as a fraction; null when B is 0/missing or A is missing."""
    if a is None or b is None or _f(b) == 0:
        return None
    return round((_f(a) - _f(b)) / _f(b), 4)


def per_day_fields(spend: Any, purchases: Any, revenue: Any, days: int | None) -> dict:
    return {"spend_per_day": _pd(spend, days), "purchases_per_day": _pd(purchases, days), "revenue_per_day": _pd(revenue, days)}


def cmp_blocks(a: tuple, w: "WindowSpec", b: tuple, b_active: Any = None) -> tuple[dict, dict]:
    """(cmp, delta) for A=(spend, purchases, revenue) vs B=(...). Everything is recomputed from the sums, never averaged."""
    (a_sp, a_pu, a_rv), (b_sp, b_pu, b_rv) = a, b
    da, db = w.days, (w.cmp.days if w.cmp is not None else None)
    a_roas, b_roas = ratio(a_rv, a_sp, 4), ratio(b_rv, b_sp, 4)
    a_cpa, b_cpa = ratio(a_sp, a_pu, 2), ratio(b_sp, b_pu, 2)
    cmp = {"spend": f2(b_sp), "purchases": num(b_pu), "revenue": f2(b_rv), "roas": b_roas, "cpa": b_cpa}
    if b_active is not None:
        cmp["active_ads"] = int(b_active)
    cmp.update(per_day_fields(b_sp, b_pu, b_rv, db))

    def diff(x, y, nd):
        return None if x is None or y is None else round(x - y, nd)

    delta = {
        "spend": round(_f(a_sp) - _f(b_sp), 2), "purchases": num(_f(a_pu) - _f(b_pu)), "revenue": round(_f(a_rv) - _f(b_rv), 2),
        "roas": diff(a_roas, b_roas, 4), "cpa": diff(a_cpa, b_cpa, 2),
        "spend_pct": _pct(a_sp, b_sp), "purchases_pct": _pct(a_pu, b_pu), "revenue_pct": _pct(a_rv, b_rv),
        "roas_pct": _pct(a_roas, b_roas), "cpa_pct": _pct(a_cpa, b_cpa),
    }
    for k, (x, y) in (("spend", (a_sp, b_sp)), ("purchases", (a_pu, b_pu)), ("revenue", (a_rv, b_rv))):
        ax, by = (None if not da else _f(x) / da), (None if not db else _f(y) / db)
        delta[f"{k}_per_day_pct"] = _pct(ax, by)
    return cmp, delta


def window_block(a: tuple, w: "WindowSpec | None", b: tuple | None = None) -> dict:
    """Row-level metric block: spend/purchases/revenue/roas/cpa for A, per-day fields, and cmp + delta when comparison is on."""
    out = simple_metrics(*a)
    out.update(per_day_fields(*a, w.days if w is not None else None))
    if w is not None and w.cmp is not None:
        out["cmp"], out["delta"] = cmp_blocks(a, w, b or (0, 0, 0))
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
        "spend": f2(a["spend"]),
        "purchases": num(a["purchases"]),
        "revenue": f2(a["revenue"]),
        "roas": ratio(a["revenue"], a["spend"], 4),
        "cpa": ratio(a["spend"], a["purchases"], 2),
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
    out.update(per_day_fields(a["spend"], a["purchases"], a["revenue"], w.days if w is not None else None))
    if w is not None and w.cmp is not None:
        out["cmp"], out["delta"] = cmp_blocks((a["spend"], a["purchases"], a["revenue"]), w,
                                              (a["c_spend"], a["c_purch"], a["c_rev"]), b_active=a["c_active"])
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


def _stage_sum_cols(prefix: str, day_pred: str = "", with_cmp: bool = False) -> str:
    """f_* (selected stages) and t_/s_/u_* (stage buckets) over the columns spend, purchases, revenue; `prefix` qualifies them.
    day_pred limits the A columns to window A's days; with_cmp adds c_* = the selected stages over window B's days (same scan)."""
    extra = f" AND {day_pred}" if day_pred else ""
    cols = []
    for short, col in (("spend", "spend"), ("purch", "purchases"), ("rev", "revenue")):
        cols.append(f"COALESCE(sum({prefix}{col}) FILTER (WHERE {prefix}stage = ANY(%(stages)s){extra}), 0) AS f_{short}")
    for letter, stage in (("t", "testing"), ("s", "scaling"), ("u", "unclassified")):
        for short, col in (("spend", "spend"), ("purch", "purchases"), ("rev", "revenue")):
            cols.append(f"COALESCE(sum({prefix}{col}) FILTER (WHERE {prefix}stage = '{stage}'{extra}), 0) AS {letter}_{short}")
    if with_cmp:
        for short, col in (("spend", "spend"), ("purch", "purchases"), ("rev", "revenue")):
            cols.append(f"COALESCE(sum({prefix}{col}) FILTER (WHERE {prefix}stage = ANY(%(stages)s) "
                        f"AND {prefix}day BETWEEN %(cf)s AND %(ct)s), 0) AS c_{short}")
    return ",\n    ".join(cols)


def _rm(meta: DataMeta, w: WindowSpec, product_id: int | None = None, family_key: str | None = None) -> tuple[str, dict]:
    """Returns ('WITH tr AS (...), ..., rm AS (...) ', params). `rm` has one row per tracker row:
         id, product_id, concept_id, ad_family, display, family_key, sheet_row, status, ad_type, tagged, cluster, icp, pain,
         line_code, trig, color, confidence, method, conf_rank, counted (is the family's first row), live,
         spend, purchases, revenue (selected window + stage; 0 unless counted), spend_7d, purchases_7d (fixed; first row only),
         t_*/s_*/u_* (stage buckets of the selected window; first row only),
         c_spend/c_purch/c_rev (comparison window B, same stages; 0 when comparison is off)."""
    params: dict[str, Any] = {"d0": meta.daily_start, "cutoff": meta.lifetime_cutoff, "dt": meta.data_through, "stages": w.stages}
    sql = "WITH " + _tr_cte(product_id, family_key, params)
    first_row = "(COALESCE(am.is_first_row, false) AND NOT starts_with(tr.family_key, 'id:'))"

    cw = w.cmp if (w.cmp is not None and not w.cmp.is_empty) else None      # comparison window B with at least one day of data
    if not w.is_launch:
        lo, hi = w.date_from, w.date_to
        if cw is not None:
            lo, hi = min(lo, cw.date_from), max(hi, cw.date_to)
            params.update(cf=cw.date_from, ct=cw.date_to)
        params.update(wf=w.date_from, wt=w.date_to, lo=lo, hi=hi)
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
         w.f_spend, w.f_purch, w.f_rev, w.t_spend, w.t_purch, w.t_rev, w.s_spend, w.s_purch, w.s_rev, w.u_spend, w.u_purch, w.u_rev
         {", w.c_spend, w.c_purch, w.c_rev" if cw is not None else ""}
  FROM tr
  LEFT JOIN ad_metrics am ON am.tracker_row_id = tr.id
  LEFT JOIN win w ON w.family_key = tr.family_key
)"""
        spend, purch, rev = ("COALESCE(b.f_spend, 0)", "COALESCE(b.f_purch, 0)", "COALESCE(b.f_rev, 0)")
        u_extra = ("", "", "")
    else:
        sql += f""",
fr AS (
  SELECT am.tracker_row_id, am.family_key, am.launch_date,
         CASE WHEN am.launch_date IS NULL THEN NULL
              WHEN am.launch_date >= %(d0)s THEN am.launch_date
              ELSE %(cutoff)s::date + 1 END AS ls
  FROM ad_metrics am
  WHERE am.is_first_row AND NOT starts_with(am.family_key, 'id:') AND am.tracker_row_id IN (SELECT id FROM tr)
),
win AS (
  SELECT fr.tracker_row_id,
    {_stage_sum_cols('fd.')}
  FROM fr JOIN family_daily fd ON fd.family_key = fr.family_key AND fd.day >= fr.ls AND fd.day <= %(dt)s
  GROUP BY fr.tracker_row_id
),
lt AS (
  SELECT fr.tracker_row_id, al.spend, al.purchases, al.conv_value AS revenue
  FROM fr JOIN ad_lifetime al ON al.family_key = fr.family_key
  WHERE fr.launch_date < %(d0)s
),
b AS (
  SELECT tr.*, {first_row} AS counted, COALESCE(am.is_first_row, false) AS is_first, COALESCE(am.live, false) AS am_live,
         COALESCE(am.spend_7d, 0) AS s7, COALESCE(am.purchases_7d, 0) AS p7,
         am.launch_date AS am_launch, am.spend_launch, am.purchases_launch, am.revenue_launch,
         w.f_spend, w.f_purch, w.f_rev, w.t_spend, w.t_purch, w.t_rev, w.s_spend, w.s_purch, w.s_rev, w.u_spend, w.u_purch, w.u_rev,
         COALESCE(lt.spend, 0) AS lt_spend, COALESCE(lt.purchases, 0) AS lt_purch, COALESCE(lt.revenue, 0) AS lt_rev
  FROM tr
  LEFT JOIN ad_metrics am ON am.tracker_row_id = tr.id
  LEFT JOIN win w ON w.tracker_row_id = tr.id
  LEFT JOIN lt ON lt.tracker_row_id = tr.id
)"""
        if w.stage == "all":      # tie-out source: the totals ETL computed (per-family rounding already applied)
            spend = "CASE WHEN b.am_launch IS NOT NULL THEN b.spend_launch ELSE 0 END"
            purch = "CASE WHEN b.am_launch IS NOT NULL THEN b.purchases_launch ELSE 0 END"
            rev = "CASE WHEN b.am_launch IS NOT NULL THEN b.revenue_launch ELSE 0 END"
        else:                      # stage list: daily rows of the included stages; lifetime history is 'unclassified'; same per-family rounding as ETL
            lt = ("b.lt_spend", "b.lt_purch", "b.lt_rev") if "unclassified" in w.stages else ("0", "0", "0")
            spend = f"round(COALESCE(b.f_spend, 0) + {lt[0]}, 2)"
            purch = f"round(COALESCE(b.f_purch, 0) + {lt[1]}, 0)"
            rev = f"round(COALESCE(b.f_rev, 0) + {lt[2]}, 2)"
        u_extra = (" + b.lt_spend", " + b.lt_purch", " + b.lt_rev")    # lifetime history is unclassified

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
         {only('b.s7')} AS spend_7d, {only('b.p7')} AS purchases_7d,
         {only('COALESCE(b.t_spend, 0)')} AS t_spend, {only('COALESCE(b.t_purch, 0)')} AS t_purch, {only('COALESCE(b.t_rev, 0)')} AS t_rev,
         {only('COALESCE(b.s_spend, 0)')} AS s_spend, {only('COALESCE(b.s_purch, 0)')} AS s_purch, {only('COALESCE(b.s_rev, 0)')} AS s_rev,
         {only('COALESCE(b.u_spend, 0)' + u_extra[0])} AS u_spend, {only('COALESCE(b.u_purch, 0)' + u_extra[1])} AS u_purch,
         {only('COALESCE(b.u_rev, 0)' + u_extra[2])} AS u_rev,
         {only(c_spend)} AS c_spend, {only(c_purch)} AS c_purch, {only(c_rev)} AS c_rev
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


def unmatched_summary(conn, product_id: int) -> dict:
    r = row_of(conn, """
        SELECT COALESCE(sum(spend_7d), 0) AS spend_7d, COALESCE(sum(spend_30d), 0) AS spend_30d,
               count(*) FILTER (WHERE live) AS live_families,
               count(*) FILTER (WHERE spend_7d > 0) AS families,
               count(*) FILTER (WHERE spend_30d > 0) AS families_30d,
               count(*) FILTER (WHERE spend_all > 0) AS families_all
        FROM unmatched_meta WHERE product_id = %s""", (product_id,))
    return {"spend_7d": f2(r["spend_7d"]), "spend_30d": f2(r["spend_30d"]), "live_families": int(r["live_families"]),
            "families": int(r["families"]), "families_30d": int(r["families_30d"]), "families_all": int(r["families_all"])}


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


def rollup(conn, meta: DataMeta, w: WindowSpec, product: dict, level: str, cluster: str | None, icp: str | None) -> dict:
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
    return {"rows": rows, "total": {"m": total_m}, "unmatched": unmatched_summary(conn, product["id"])}


# --------------------------------------------------------------------------------------------------------------------
# families
# --------------------------------------------------------------------------------------------------------------------


def families(conn, meta: DataMeta, w: WindowSpec, product: dict, cluster: str | None, icp: str | None, pain: str | None) -> dict:
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
ORDER BY g.spend DESC, g.display, g.family_key
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
         depth: int) -> dict:
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
    return {"rows": out, "total": {"m": total_m}}


# --------------------------------------------------------------------------------------------------------------------
# Meta ads (ad_id grain)
# --------------------------------------------------------------------------------------------------------------------

REMAINDER_NAME = "(days without ad-level detail)"


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


def _sum_cols(alias: str, w: WindowSpec, cw: WindowSpec | None) -> str:
    """spend/purchases/revenue over window A, c_* over window B, days_active, from one pass over ad_daily rows aliased `alias`."""
    fa = "" if w.is_launch else f" FILTER (WHERE {alias}.day BETWEEN %(wf)s AND %(wt)s)"
    cols = [f"COALESCE(sum({alias}.{c}){fa}, 0) AS {n}" for c, n in (("spend", "spend"), ("purchases", "purchases"), ("conv_value", "revenue"))]
    if cw is not None:
        cols += [f"COALESCE(sum({alias}.{c}) FILTER (WHERE {alias}.day BETWEEN %(cf)s AND %(ct)s), 0) AS {n}"
                 for c, n in (("spend", "c_spend"), ("purchases", "c_purch"), ("conv_value", "c_rev"))]
    else:
        cols += ["0::numeric AS c_spend", "0::numeric AS c_purch", "0::numeric AS c_rev"]
    act = f"{alias}.spend > 0" + ("" if w.is_launch else f" AND {alias}.day BETWEEN %(wf)s AND %(wt)s")
    cols.append(f"count(*) FILTER (WHERE {act}) AS days_active")
    return ",\n         ".join(cols)


def meta_ads(conn, meta: DataMeta, w: WindowSpec, product: dict, *, cluster: str | None = None, icp: str | None = None,
             pain: str | None = None, family_key: str | None = None, campaign_id: str | None = None) -> dict:
    """Individual Meta ads that make up the selection. Families are included where their first tracker row is in the selection, so the ad
    rows (plus the remainder row) sum to the same totals the rollup / families tables show."""
    params: dict[str, Any] = {"d0": meta.daily_start, "cutoff": meta.lifetime_cutoff, "dt": meta.data_through,
                              "stages": w.stages, "campaign": campaign_id, "d7": meta.data_through - dt.timedelta(days=6),
                              "d1": meta.data_through - dt.timedelta(days=1)}
    sql = "WITH " + _tr_cte(product["id"], family_key, params)
    tagw = _tag_where(cluster, icp, pain, params, prefix="tr.")
    have_tag_filter = any(v is not None for v in (cluster, icp, pain, family_key))
    cw = _window_params(meta, w, params)
    sql += f""",
sel AS (
  SELECT DISTINCT ON (tr.family_key) tr.family_key, tr.id AS primary_id, tr.display, tr.cluster, tr.icp, tr.pain, am.launch_date,
         CASE WHEN am.launch_date IS NULL THEN NULL WHEN am.launch_date >= %(d0)s THEN am.launch_date ELSE %(cutoff)s::date + 1 END AS ls
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
    day_pred = "ad.day >= s.ls AND ad.day <= %(dt)s" if w.is_launch else "ad.day BETWEEN %(lo)s AND %(hi)s"
    zc = "0::numeric, 0::numeric, 0::numeric"           # c_spend, c_purch, c_rev for parts that carry no window B
    parts = f"""
  SELECT CASE WHEN starts_with(ad.ad_id, 'fam:') THEN NULL ELSE ad.ad_id END AS aid, s.family_key AS fk, TRUE AS matched,
         max(ad.meta_family) AS mf, max(NULLIF(ad.campaign_id, '')) AS camp,
         {_sum_cols('ad', w, cw)}
  FROM ad_daily ad
  JOIN meta_family_map m ON m.meta_family_key = ad.meta_family_key
  JOIN sel s ON s.family_key = m.tracker_family_key
  LEFT JOIN meta_campaigns mc ON mc.campaign_id = ad.campaign_id
  WHERE {day_pred} AND COALESCE(mc.stage, 'unclassified') = ANY(%(stages)s)
    AND (%(campaign)s::text IS NULL OR ad.campaign_id = %(campaign)s)
  GROUP BY 1, 2
"""
    if w.is_launch and "unclassified" in w.stages:
        parts += f"""
  UNION ALL
  SELECT ml.ad_id, s.family_key, TRUE, NULL::text, NULL::text, ml.spend, ml.purchases, ml.conv_value, {zc}, 0::bigint
  FROM meta_ad_lifetime ml
  JOIN sel s ON s.family_key = ml.tracker_family_key AND s.launch_date < %(d0)s
  LEFT JOIN meta_ads ma ON ma.ad_id = ml.ad_id
  WHERE (%(campaign)s::text IS NULL OR ma.campaign_id = %(campaign)s)
"""
        if campaign_id is None:   # lifetime history the per-ad Lifetime rows do not account for goes to the remainder row
            parts += f"""
  UNION ALL
  SELECT NULL::text, s.family_key, TRUE, NULL::text, NULL::text,
         al.spend - COALESCE(pa.spend, 0), al.purchases - COALESCE(pa.purchases, 0), al.conv_value - COALESCE(pa.revenue, 0), {zc}, 0::bigint
  FROM sel s
  JOIN ad_lifetime al ON al.family_key = s.family_key AND s.launch_date < %(d0)s
  LEFT JOIN (SELECT tracker_family_key AS fk, sum(spend) AS spend, sum(purchases) AS purchases, sum(conv_value) AS revenue
             FROM meta_ad_lifetime GROUP BY 1) pa ON pa.fk = s.family_key
"""
    if not have_tag_filter:       # no tag/family selected: "every ad of the product" also covers Meta ads not on the tracker
        u_pred = "ad.day BETWEEN %(d0)s AND %(dt)s" if w.is_launch else "ad.day BETWEEN %(lo)s AND %(hi)s"
        parts += f"""
  UNION ALL
  SELECT CASE WHEN starts_with(ad.ad_id, 'fam:') THEN NULL ELSE ad.ad_id END, um.meta_family_key, FALSE,
         max(ad.meta_family), max(NULLIF(ad.campaign_id, '')),
         {_sum_cols('ad', w, cw)}
  FROM ad_daily ad
  JOIN unmatched_meta um ON um.meta_family_key = ad.meta_family_key AND um.product_id = %(pid)s
  LEFT JOIN meta_campaigns mc ON mc.campaign_id = ad.campaign_id
  WHERE {u_pred} AND COALESCE(mc.stage, 'unclassified') = ANY(%(stages)s)
    AND (%(campaign)s::text IS NULL OR ad.campaign_id = %(campaign)s)
  GROUP BY 1, 2
"""
    sql += f"""
parts AS ({parts}),
o AS (
  SELECT aid, fk, bool_or(matched) AS matched, max(mf) AS mf, max(camp) AS camp,
         sum(spend) AS spend, sum(purchases) AS purchases, sum(revenue) AS revenue,
         sum(c_spend) AS c_spend, sum(c_purch) AS c_purch, sum(c_rev) AS c_rev, sum(days_active) AS days_active
  FROM parts GROUP BY aid, fk
)
SELECT o.aid, o.fk, o.matched, o.mf, o.camp, o.spend, o.purchases, o.revenue, o.c_spend, o.c_purch, o.c_rev, o.days_active,
       ma.ad_name, ma.campaign_id AS ma_campaign, ma.adset_name, ma.account, ma.status AS ad_status, ma.created_date,
       mc.name AS campaign_name, mc.account AS campaign_account, COALESCE(mc.stage, 'unclassified') AS camp_stage,
       COALESCE(a7.s7, 0) AS spend_7d, COALESCE(a7.s1, 0) > 0 AS live,
       s.primary_id, s.display, s.cluster, s.icp, s.pain, th.thumb_id
FROM o
LEFT JOIN meta_ads ma ON ma.ad_id = o.aid
LEFT JOIN meta_campaigns mc ON mc.campaign_id = COALESCE(NULLIF(ma.campaign_id, ''), o.camp)
LEFT JOIN ad7 a7 ON a7.ad_id = o.aid
LEFT JOIN sel s ON s.family_key = o.fk AND o.matched
LEFT JOIN thumbs th ON th.family_key = o.fk AND o.matched
WHERE (o.spend <> 0 OR o.purchases <> 0 OR o.revenue <> 0 OR o.c_spend <> 0 OR o.c_purch <> 0 OR o.c_rev <> 0)
ORDER BY o.spend DESC, o.c_spend DESC, o.aid NULLS LAST
"""
    raw = rows_of(conn, sql, params)

    zero = lambda: {k: Decimal(0) for k in ("spend", "purchases", "revenue", "c_spend", "c_purch", "c_rev")}   # noqa: E731
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
            **window_block((r["spend"], r["purchases"], r["revenue"]), w, (r["c_spend"], r["c_purch"], r["c_rev"])),
            "spend_7d": f2(r["spend_7d"]), "live": bool(r["live"]), "days_active": int(r["days_active"] or 0),
        })

    for on_tracker in (True, False):             # the remainder keeps tracker and non-tracker days apart so each side still adds up
        rm_ = rem[on_tracker]
        if any(abs(_f(v)) >= 0.005 for v in rm_.values()):
            ads.append({
                "ad_id": None, "ad_name": REMAINDER_NAME, "family_key": family_key,
                "display": REMAINDER_NAME if on_tracker else REMAINDER_NAME[:-1] + ", not on the tracker)",
                "primary_id": None, "thumb_asset_id": None, "on_tracker": on_tracker, "campaign_id": None, "campaign_name": None,
                "stage": "unclassified", "adset_name": None, "account": None, "status": None, "created_date": None, "tag": None,
                **window_block((rm_["spend"], rm_["purchases"], rm_["revenue"]), w, (rm_["c_spend"], rm_["c_purch"], rm_["c_rev"])),
                "spend_7d": 0.0, "live": False, "days_active": 0,
            })

    total = {"ads": sum(1 for a in ads if a["ad_id"]),
             **window_block((tot["spend"], tot["purchases"], tot["revenue"]), w, (tot["c_spend"], tot["c_purch"], tot["c_rev"])),
             "spend_7d": f2(sum(Decimal(str(a["spend_7d"])) for a in ads)), "live": sum(1 for a in ads if a["live"])}
    apply_shares(ads + [total], total)
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


def campaigns(conn, meta: DataMeta, w: WindowSpec, product: dict) -> dict:
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
  SELECT ad.ad_id, ad.meta_family_key, ad.campaign_id, ad.day, ad.spend, ad.purchases, ad.conv_value,
         CASE WHEN ad.campaign_id = '' THEN 'unclassified' ELSE COALESCE(mc.stage, 'unclassified') END AS stage,
         starts_with(ad.ad_id, 'fam:') AS is_fam
  FROM ad_daily ad JOIN pf ON pf.meta_family_key = ad.meta_family_key
  LEFT JOIN meta_campaigns mc ON mc.campaign_id = ad.campaign_id
  WHERE ad.day >= %(lo)s
),
f AS (SELECT *, (day BETWEEN %(wf)s AND %(wt)s) AS in_win, {in_cmp} AS in_cmp FROM r)
"""
    stage_rows = rows_of(conn, base + """
        SELECT stage,
               count(DISTINCT campaign_id) FILTER (WHERE in_win AND campaign_id <> '' AND spend > 0) AS campaigns,
               count(DISTINCT ad_id) FILTER (WHERE in_win AND NOT is_fam AND spend > 0) AS ads,
               COALESCE(sum(spend) FILTER (WHERE in_win), 0) AS spend, COALESCE(sum(purchases) FILTER (WHERE in_win), 0) AS purchases,
               COALESCE(sum(conv_value) FILTER (WHERE in_win), 0) AS revenue,
               COALESCE(sum(spend) FILTER (WHERE in_cmp), 0) AS c_spend, COALESCE(sum(purchases) FILTER (WHERE in_cmp), 0) AS c_purch,
               COALESCE(sum(conv_value) FILTER (WHERE in_cmp), 0) AS c_rev,
               COALESCE(sum(spend) FILTER (WHERE day >= %(d7)s), 0) AS spend_7d
        FROM f GROUP BY stage""", params)
    by = {r["stage"]: r for r in stage_rows}
    by_stage = []
    for st in STAGES:
        r = by.get(st)
        if r is None and st == "unclassified":
            continue
        r = r or {"campaigns": 0, "ads": 0, "spend": 0, "purchases": 0, "revenue": 0, "c_spend": 0, "c_purch": 0, "c_rev": 0, "spend_7d": 0}
        by_stage.append({"stage": st, "campaigns": int(r["campaigns"]), "ads": int(r["ads"]),
                         **window_block((r["spend"], r["purchases"], r["revenue"]), w, (r["c_spend"], r["c_purch"], r["c_rev"])),
                         "spend_7d": f2(r["spend_7d"])})

    sel = "f.stage = ANY(%(stages)s)"
    crow = rows_of(conn, base + f"""
        SELECT f.campaign_id, mc.name, mc.account, COALESCE(mc.stage, 'unclassified') AS stage,
               count(DISTINCT f.ad_id) FILTER (WHERE in_win AND NOT is_fam AND {sel} AND f.spend > 0) AS ads,
               count(DISTINCT f.meta_family_key) FILTER (WHERE in_win AND {sel} AND f.spend > 0) AS families,
               COALESCE(sum(f.spend) FILTER (WHERE in_win AND {sel}), 0) AS spend,
               COALESCE(sum(f.purchases) FILTER (WHERE in_win AND {sel}), 0) AS purchases,
               COALESCE(sum(f.conv_value) FILTER (WHERE in_win AND {sel}), 0) AS revenue,
               COALESCE(sum(f.spend) FILTER (WHERE in_cmp AND {sel}), 0) AS c_spend,
               COALESCE(sum(f.purchases) FILTER (WHERE in_cmp AND {sel}), 0) AS c_purch,
               COALESCE(sum(f.conv_value) FILTER (WHERE in_cmp AND {sel}), 0) AS c_rev,
               COALESCE(sum(f.spend) FILTER (WHERE f.day >= %(d7)s), 0) AS spend_7d,
               COALESCE(sum(f.spend) FILTER (WHERE f.day >= %(d1)s), 0) > 0 AS live,
               max(f.day) FILTER (WHERE f.spend > 0) AS last_day
        FROM f LEFT JOIN meta_campaigns mc ON mc.campaign_id = f.campaign_id
        WHERE f.campaign_id <> ''
        GROUP BY f.campaign_id, mc.name, mc.account, mc.stage
        HAVING COALESCE(sum(f.spend) FILTER (WHERE (in_win OR in_cmp) AND {sel}), 0) <> 0
            OR COALESCE(sum(f.purchases) FILTER (WHERE (in_win OR in_cmp) AND {sel}), 0) <> 0
        ORDER BY spend DESC, c_spend DESC, f.campaign_id""", params)
    rows = [{"campaign_id": r["campaign_id"], "name": r["name"] or r["campaign_id"], "account": r["account"], "stage": r["stage"],
             "ads": int(r["ads"]), "families": int(r["families"]),
             **window_block((r["spend"], r["purchases"], r["revenue"]), w, (r["c_spend"], r["c_purch"], r["c_rev"])),
             "spend_7d": f2(r["spend_7d"]), "live": bool(r["live"]),
             "last_day": r["last_day"].isoformat() if r["last_day"] else None} for r in crow]

    tr_ = row_of(conn, base + """
        SELECT count(DISTINCT campaign_id) FILTER (WHERE in_win AND campaign_id <> '' AND stage = ANY(%(stages)s) AND spend > 0) AS campaigns,
               count(DISTINCT ad_id) FILTER (WHERE in_win AND NOT is_fam AND stage = ANY(%(stages)s) AND spend > 0) AS ads,
               COALESCE(sum(spend) FILTER (WHERE in_win AND stage = ANY(%(stages)s)), 0) AS spend,
               COALESCE(sum(purchases) FILTER (WHERE in_win AND stage = ANY(%(stages)s)), 0) AS purchases,
               COALESCE(sum(conv_value) FILTER (WHERE in_win AND stage = ANY(%(stages)s)), 0) AS revenue,
               COALESCE(sum(spend) FILTER (WHERE in_cmp AND stage = ANY(%(stages)s)), 0) AS c_spend,
               COALESCE(sum(purchases) FILTER (WHERE in_cmp AND stage = ANY(%(stages)s)), 0) AS c_purch,
               COALESCE(sum(conv_value) FILTER (WHERE in_cmp AND stage = ANY(%(stages)s)), 0) AS c_rev,
               COALESCE(sum(spend) FILTER (WHERE day >= %(d7)s AND stage = ANY(%(stages)s)), 0) AS spend_7d
        FROM f""", params)
    total = {"campaigns": int(tr_["campaigns"]), "ads": int(tr_["ads"]),
             **window_block((tr_["spend"], tr_["purchases"], tr_["revenue"]), w, (tr_["c_spend"], tr_["c_purch"], tr_["c_rev"])),
             "spend_7d": f2(tr_["spend_7d"])}
    apply_shares(rows + [total], total)
    apply_shares(by_stage, {"spend": sum(c["spend"] for c in by_stage),
                            **({"cmp": {"spend": sum(c["cmp"]["spend"] for c in by_stage)}} if w.cmp is not None else {})})
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
    wf, wt = w.span(meta)
    cw = _cmp_window(w)
    params = {"pid": product["id"], "wf": wf, "wt": wt, "stages": w.stages,
              "lo": min(wf, cw.date_from) if cw is not None else wf, "hi": max(wt, cw.date_to) if cw is not None else wt,
              "cf": cw.date_from if cw is not None else wf, "ct": cw.date_to if cw is not None else wt}
    c_cols = ("COALESCE(sum(ad.spend) FILTER (WHERE ad.day BETWEEN %(cf)s AND %(ct)s), 0) AS c_spend, "
              "COALESCE(sum(ad.purchases) FILTER (WHERE ad.day BETWEEN %(cf)s AND %(ct)s), 0) AS c_purch, "
              "COALESCE(sum(ad.conv_value) FILTER (WHERE ad.day BETWEEN %(cf)s AND %(ct)s), 0) AS c_rev"
              if cw is not None else "0::numeric AS c_spend, 0::numeric AS c_purch, 0::numeric AS c_rev")
    raw = rows_of(conn, f"""
        SELECT um.meta_family_key, um.meta_family, um.spend_7d, um.live,
               COALESCE(sum(ad.spend) FILTER (WHERE ad.day BETWEEN %(wf)s AND %(wt)s), 0) AS spend,
               COALESCE(sum(ad.purchases) FILTER (WHERE ad.day BETWEEN %(wf)s AND %(wt)s), 0) AS purchases,
               COALESCE(sum(ad.conv_value) FILTER (WHERE ad.day BETWEEN %(wf)s AND %(wt)s), 0) AS revenue,
               {c_cols}
        FROM unmatched_meta um
        JOIN ad_daily ad ON ad.meta_family_key = um.meta_family_key
        LEFT JOIN meta_campaigns mc ON mc.campaign_id = ad.campaign_id
        WHERE um.product_id = %(pid)s AND ad.day BETWEEN %(lo)s AND %(hi)s
          AND (CASE WHEN ad.campaign_id = '' THEN 'unclassified' ELSE COALESCE(mc.stage, 'unclassified') END) = ANY(%(stages)s)
        GROUP BY um.meta_family_key, um.meta_family, um.spend_7d, um.live
        HAVING sum(ad.spend) <> 0 OR sum(ad.purchases) <> 0
        ORDER BY spend DESC, c_spend DESC, um.meta_family""", params)
    raw = [r for r in raw if r["spend"] != 0 or r["purchases"] != 0 or r["c_spend"] != 0 or r["c_purch"] != 0]
    rows = [{"meta_family": r["meta_family"], "meta_family_key": r["meta_family_key"],
             **window_block((r["spend"], r["purchases"], r["revenue"]), w, (r["c_spend"], r["c_purch"], r["c_rev"])),
             "spend_7d": f2(r["spend_7d"]), "live": bool(r["live"])} for r in raw]
    tot = {k: sum((r[k] for r in raw), Decimal(0)) for k in ("spend", "purchases", "revenue", "c_spend", "c_purch", "c_rev")}
    total = {**window_block((tot["spend"], tot["purchases"], tot["revenue"]), w, (tot["c_spend"], tot["c_purch"], tot["c_rev"])),
             "spend_7d": f2(sum((r["spend_7d"] for r in raw), Decimal(0))), "families": len(rows)}
    apply_shares(rows + [total], total)
    return {"rows": rows, "total": total}


# --------------------------------------------------------------------------------------------------------------------
# ad detail
# --------------------------------------------------------------------------------------------------------------------


def reliability_note(method: str | None, confidence: str | None) -> str:
    parts = []
    m = (method or "").lower()
    if "text only" in m:
        parts.append("Tagged from copy only (no image).")
    elif "thumbnail" in m and "no audio" in m and "frames" not in m:
        parts.append("Tagged from a thumbnail only.")
    if confidence == "low":
        parts.append("Low confidence — blind re-tag agreed ~52%.")
    elif confidence == "high":
        parts.append("High confidence — blind re-tag agreed ~93%.")
    elif confidence == "medium":
        parts.append("Medium confidence.")
    parts.append(GLOBAL_RELIABILITY)
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
    av = (a["spend"], a["purchases"], a["revenue"])
    block = {**simple_metrics(*av), **per_day_fields(*av, w.days)}
    if w.cmp is None:
        return block, None, None
    cmp, delta = cmp_blocks(av, w, (a["c_spend"], a["c_purch"], a["c_rev"]), b_active=a["c_active"])
    return block, {**simple_metrics(a["c_spend"], a["c_purch"], a["c_rev"]), **cmp}, delta


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
            "reliability_note": reliability_note(tg["method"], tg["confidence"]) if tg["tag_set"] == "linemap" else None,
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
            "launch": simple_metrics(first["spend_launch"], first["purchases_launch"], first["revenue_launch"]),
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
        family_metrics["window"] = {**simple_metrics(0, 0, 0), **per_day_fields(0, 0, 0, w.days)}
        cmp_block = delta_block = None
        if w.cmp is not None:
            cmp_block, delta_block = cmp_blocks((0, 0, 0), w, (0, 0, 0), b_active=0)
            cmp_block = {**simple_metrics(0, 0, 0), **cmp_block}
    if w.cmp is not None:
        family_metrics["compare"] = cmp_block
        family_metrics["delta"] = delta_block

    daily_rows = {d["day"]: d for d in rows_of(conn, """
        SELECT day, sum(spend) AS spend, sum(purchases) AS purchases, sum(revenue) AS revenue, sum(link_clicks) AS link_clicks
        FROM family_daily WHERE family_key = %s AND day BETWEEN %s AND %s AND stage = ANY(%s) GROUP BY day""",
        (fk, meta.daily_start, meta.data_through, w.stages))}
    daily, d = [], meta.daily_start
    while d <= meta.data_through:
        x = daily_rows.get(d)
        daily.append({"day": d.isoformat(), "spend": f2(x["spend"]) if x else 0.0, "purchases": num(x["purchases"]) if x else 0,
                      "revenue": f2(x["revenue"]) if x else 0.0, "link_clicks": num(x["link_clicks"]) if x else 0})
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
# status / media
# --------------------------------------------------------------------------------------------------------------------


def status(conn, meta: DataMeta, n_products: int) -> dict:
    last = row_of(conn, """SELECT job, finished_at, status FROM etl_runs WHERE finished_at IS NOT NULL ORDER BY finished_at DESC, id DESC LIMIT 1""")
    ok = row_of(conn, """SELECT finished_at FROM etl_runs WHERE finished_at IS NOT NULL AND status = 'ok' ORDER BY finished_at DESC, id DESC LIMIT 1""")
    return {
        "data_through": meta.data_through.isoformat(), "daily_start": meta.daily_start.isoformat(),
        "lifetime_cutoff": meta.lifetime_cutoff.isoformat(),
        "last_refresh": ({"job": last["job"], "finished_at": last["finished_at"].isoformat(), "status": last["status"]} if last else None),
        "last_ok_at": ok["finished_at"].isoformat() if ok else None,
        "products": n_products,
    }


def media(conn, asset_id: int) -> tuple[bytes, str] | None:
    r = row_of(conn, "SELECT bytes, mime FROM creative_assets WHERE id = %s", (asset_id,))
    return (bytes(r["bytes"]), r["mime"]) if r else None
