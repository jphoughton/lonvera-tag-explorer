"""Cohort math: retention curves and the repeat-revenue waterfall. Pure functions, no database.

Sheet equivalents (Lonvera Projections): `Cohort Data - Unified` rows 2.. (cohort table) and row 36 (weighted average of the most recent cohorts
that reached each age), `Curves` (Live / Goal curve, tail decay) and `Hist Waterfall - Last 4 Months Curve` (layers 1-5).

Definitions
    cum[a]      cumulative $ per cohort customer at month `a` after acquisition, a = 1..36, first order included (sheet columns M1..M36)
    first_order $ per customer of the first order (sheet column G, "First Order"); cohort revenue = new_customers x first_order
    increment   cum[a] - cum[a-1] with cum[0] = first_order; blank when cum[a] is not observed (blank or zero)
    live curve  age a = weighted average (weights: new customers) of increment / first_order over the last `n_cohorts` cohorts that have an
                increment at age a; ages from `tail_from_age` on = previous age x `tail_decay`
    repeat      cohort revenue x curve[age] in every month at that age (age 0 = acquisition month = first order, no repeat)
    as-of curve the Live curve re-derived from only what was observable at the end of month M: cohort C counts when C <= M and its age-a cell
                counts when C + a <= M (calendar months fully closed by M). The Aug cohort "as of Aug" has a first order and no repeat yet.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date
from typing import Iterable, Sequence

from ..interfaces import MONTHS, Series, add_months

COHORT_AGES = 36          # M1..M36 stored per cohort
CURVE_AGES = 60           # curve length (ages 1..60)

CURVE_CHOICES_HELP = "live | goal | asof | blend:<w> (w = weight on the Goal curve, 0..1)"
SOURCES = ("live", "goal", "asof")
N_COHORT_CHOICES = (3, 6)           # the two averages the page offers: the sheet's literal 3 and the 6 the deal notes mention
MIN_COMPUTABLE_AGES = 12            # an as-of month is computable when ages 1..12 each have n_cohorts cohorts behind them

MONTH_ABBR = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def month_text(d: date | None) -> str:
    return f"{MONTH_ABBR[d.month - 1]} {d.year}" if d else ""


# ---------------------------------------------------------------------------------------------------------------- levers

@dataclass(frozen=True)
class Levers:
    """Every lever of the module. `curve_source` replaces the earlier `curve` name (still accepted when reading a scenario)."""
    n_cohorts: int = 3                 # sheet row 36 "Last 3 Cohorts Avg"
    min_cohorts_per_age: int = 1       # sheet: MIN(3, count) - an age with a single cohort still reads that cohort
    tail_decay: float = 0.95           # Curves!C2
    tail_from_age: int = 28            # Curves rows 4-30 read the table (ages 1-27), row 31 on = decay
    max_age: int = 60                  # sheet: age > 60 returns 0
    curve_source: str = "live"         # live | goal | asof | blend:<w>; sheet P&L!A2 "Repeat Case" (Last 4 Months Curve)
    asof_month: date | None = None     # first of month; the month an 'asof' curve is taken at the end of (no sheet cell: new)

    @property
    def curve(self) -> str:             # the earlier name
        return self.curve_source

    @property
    def blend_weight(self) -> float | None:
        """Weight on the Goal curve for curve_source='blend:<w>', else None."""
        if self.curve_source.startswith("blend:"):
            return float(self.curve_source.split(":", 1)[1])
        return None

    def to_dict(self) -> dict:
        return {"n_cohorts": self.n_cohorts, "min_cohorts_per_age": self.min_cohorts_per_age, "tail_decay": self.tail_decay,
                "tail_from_age": self.tail_from_age, "max_age": self.max_age, "curve_source": self.curve_source,
                "asof_month": self.asof_month.isoformat() if self.asof_month else None}


DEFAULT_LEVERS = Levers()
LEVER_BOUNDS = {
    "n_cohorts": {"min": 1, "max": 36, "type": "int"},
    "min_cohorts_per_age": {"min": 1, "max": 36, "type": "int"},
    "tail_decay": {"min": 0.5, "max": 1.0, "type": "float"},
    "tail_from_age": {"min": 2, "max": CURVE_AGES, "type": "int"},
    "max_age": {"min": 12, "max": CURVE_AGES, "type": "int"},
    "curve_source": {"type": "text", "help": CURVE_CHOICES_HELP},
    "asof_month": {"type": "month", "help": "first of a month, e.g. 2026-08-01 (required when curve_source is asof)"},
}
LEGACY_NAMES = {"curve": "curve_source"}


# Upload plausibility bands (docs MODULE_cohorts.md, Variables). They only gate what an upload may load; the engine never reads them.
# name -> (default, lowest, highest, plain label)
BAND_BOUNDS: dict[str, tuple[float, float, float, str]] = {
    "upload_lifetime_min": (0.5, 0.0, 10.0, "Lowest allowed repeat per first-order dollar over 60 months"),
    "upload_lifetime_max": (5.0, 0.5, 50.0, "Highest allowed repeat per first-order dollar over 60 months"),
    "upload_twelve_min": (0.2, 0.0, 10.0, "Lowest allowed repeat per first-order dollar over the first 12 months"),
    "upload_twelve_max": (3.0, 0.2, 20.0, "Highest allowed repeat per first-order dollar over the first 12 months"),
    "upload_first_order_min": (5.0, 0.0, 10000.0, "Lowest allowed first order per customer"),
    "upload_first_order_max": (500.0, 1.0, 100000.0, "Highest allowed first order per customer"),
    "upload_first_order_tol": (0.40, 0.05, 5.0, "How far a first order may sit from its neighbouring cohorts"),
    "upload_customers_tol": (0.60, 0.05, 10.0, "How far a cohort's customer count may change from its stored value or its neighbours"),
}


@dataclass(frozen=True)
class Bands:
    lifetime_min: float = 0.5
    lifetime_max: float = 5.0
    twelve_min: float = 0.2
    twelve_max: float = 3.0
    first_order_min: float = 5.0
    first_order_max: float = 500.0
    first_order_tol: float = 0.40
    customers_tol: float = 0.60


def parse_bands(raw: dict | None, *, strict: bool = False) -> Bands:
    """Upload bands from a params['cohorts'] dict; anything missing or out of range takes the default (strict=True raises instead)."""
    raw = raw or {}
    kw: dict = {}
    for name, (default, lo, hi, label) in BAND_BOUNDS.items():
        if name not in raw or raw[name] is None:
            continue
        try:
            kw[name.removeprefix("upload_")] = float(_num(raw[name], name, "float", lo, hi))
        except ValueError:
            if strict:
                raise
    b = Bands(**kw)
    for lo_name, hi_name in (("lifetime_min", "lifetime_max"), ("twelve_min", "twelve_max"), ("first_order_min", "first_order_max")):
        if getattr(b, lo_name) >= getattr(b, hi_name):
            if strict:
                raise ValueError(f"upload_{lo_name} must be lower than upload_{hi_name}")
            b = replace(b, **{lo_name: getattr(Bands(), lo_name), hi_name: getattr(Bands(), hi_name)})
    return b


def parse_curve_choice(v: object) -> str:
    """Normalise 'live' | 'goal' | 'asof' | 'blend:0.4'. ValueError otherwise."""
    if not isinstance(v, str):
        raise ValueError(f"curve_source must be text: {CURVE_CHOICES_HELP}")
    s = v.strip().lower()
    if s in SOURCES:
        return s
    if s.startswith("blend:"):
        try:
            w = float(s.split(":", 1)[1])
        except ValueError:
            raise ValueError(f"curve_source {v!r}: the blend weight must be a number") from None
        if not 0.0 <= w <= 1.0:
            raise ValueError("curve blend weight must be between 0 and 1")
        return f"blend:{w:g}"
    raise ValueError(f"curve_source must be {CURVE_CHOICES_HELP}")


def parse_month_value(v: object) -> date:
    """'2026-08', '2026-08-01' or a date at the first of a month -> date. ValueError otherwise (any other day of the month is rejected)."""
    if isinstance(v, date):
        d = v
    elif isinstance(v, str):
        s = v.strip()
        try:
            d = date.fromisoformat(s + "-01") if len(s) == 7 else date.fromisoformat(s)
        except ValueError:
            raise ValueError("asof_month must be a month like 2026-08") from None
    else:
        raise ValueError("asof_month must be a month like 2026-08")
    if d.day != 1:
        raise ValueError("asof_month must be the first of a month, like 2026-08-01")
    return d


def _num(v: object, name: str, kind: str, lo: float, hi: float) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError(f"{name} must be a number")
    if v != v or v in (float("inf"), float("-inf")):
        raise ValueError(f"{name} must be finite")
    if kind == "int" and float(v) != int(v):
        raise ValueError(f"{name} must be a whole number")
    if not lo <= v <= hi:
        raise ValueError(f"{name} must be between {lo:g} and {hi:g}")
    return int(v) if kind == "int" else float(v)


def parse_levers(raw: dict | None, *, strict: bool = True) -> Levers:
    """Levers from a params['cohorts'] dict; missing keys take the defaults. The earlier name `curve` is read as `curve_source` (the new name wins
    when both are present). strict=True rejects unknown keys, out-of-range values and an as-of curve without a month (ValueError); strict=False
    ignores them (used when reading a scenario somebody else wrote, so a bad cell never stops the engine)."""
    raw = dict(raw or {})
    for old, new in LEGACY_NAMES.items():
        if old in raw:
            legacy = raw.pop(old)
            if raw.get(new) is None:
                raw[new] = legacy
    kw: dict = {}
    for name, spec in LEVER_BOUNDS.items():
        if name not in raw or raw[name] is None:
            continue
        try:
            if name == "curve_source":
                kw[name] = parse_curve_choice(raw[name])
            elif name == "asof_month":
                kw[name] = parse_month_value(raw[name])
            else:
                kw[name] = _num(raw[name], name, spec["type"], spec["min"], spec["max"])
        except ValueError:
            if strict:
                raise
    unknown = set(raw) - set(LEVER_BOUNDS) - set(BAND_BOUNDS)
    if unknown and strict:
        raise ValueError(f"unknown cohort lever {sorted(unknown)[0]!r}")
    if strict:
        parse_bands(raw, strict=True)                           # the upload bands: range and order checks (they are not part of Levers)
    lv = Levers(**kw)
    if lv.min_cohorts_per_age > lv.n_cohorts:
        if strict:
            raise ValueError("min_cohorts_per_age cannot exceed n_cohorts")
        lv = replace(lv, min_cohorts_per_age=lv.n_cohorts)
    if lv.curve_source == "asof" and lv.asof_month is None:
        if strict:
            raise ValueError("choose a month for the as-of curve (asof_month)")
        lv = replace(lv, curve_source="live")
    return lv


# ---------------------------------------------------------------------------------------------------------------- cohort table

@dataclass(frozen=True)
class Cohort:
    month: date
    new_customers: float
    first_order: float | None = None                 # $/customer, sheet column G
    first_order_realized: float | None = None
    repeat_90d: float | None = None
    cac: float | None = None
    payback_months: float | None = None
    cum: tuple[float | None, ...] = ()               # ages 1..36; None = not observed
    nc_rev_reference: float | None = None            # sheet's typed Hist Waterfall NC-Rev (tie-out only)

    def cum_at(self, age: int) -> float | None:
        if 1 <= age <= len(self.cum):
            v = self.cum[age - 1]
            return None if v is None or v == 0 else v
        return None

    @property
    def first_order_rev(self) -> float:
        """Cohort new-customer revenue = new customers x first-order $ per customer."""
        return self.new_customers * self.first_order if self.first_order is not None else 0.0

    def increment(self, age: int) -> float | None:
        """$ per customer earned in month `age` (cum[age] - cum[age-1]); None when not observed. Sheet: IF(OR(M="",M=0),"",M-prev)."""
        cur = self.cum_at(age)
        if cur is None:
            return None
        prev = self.first_order if age == 1 else self.cum_at(age - 1)
        return None if prev is None else cur - prev

    def increment_ratio(self, age: int) -> float | None:
        """Increment as a multiple of the first order (sheet columns CD..DM)."""
        inc = self.increment(age)
        if inc is None or not self.first_order:
            return None
        return inc / self.first_order

    def last_observed_age(self) -> int:
        return max((a for a in range(1, COHORT_AGES + 1) if self.cum_at(a) is not None), default=0)


def sort_cohorts(cohorts: Iterable[Cohort]) -> list[Cohort]:
    return sorted(cohorts, key=lambda c: c.month)


def months_between(start: date, end: date) -> int:
    """Whole months from start to end (both first-of-month): the sheet's DATEDIF(start, end, "M")."""
    return (end.year - start.year) * 12 + end.month - start.month


# ---------------------------------------------------------------------------------------------------------------- curves

@dataclass
class AgePoint:
    age: int
    value: float
    source: str                                      # 'data' (cohort average) | 'tail' (decay from the previous age) | 'none'
    cohorts_used: list[date] = field(default_factory=list)

    @property
    def n_used(self) -> int:
        return len(self.cohorts_used)


def live_curve(cohorts: Sequence[Cohort], levers: Levers = DEFAULT_LEVERS) -> list[AgePoint]:
    """Live curve, ages 1..CURVE_AGES. At each age before `tail_from_age`: weighted average of the increment ratio over the most recent
    `n_cohorts` cohorts (by month) that have a non-blank, non-zero increment at that age; when fewer than `min_cohorts_per_age` exist the age
    falls back to the previous age x tail_decay (0 for age 1). From `tail_from_age` on every age is previous x tail_decay."""
    ordered = sort_cohorts(cohorts)
    out: list[AgePoint] = []
    for age in range(1, CURVE_AGES + 1):
        prev = out[-1].value if out else 0.0
        if age >= levers.tail_from_age or age > COHORT_AGES:
            out.append(AgePoint(age, prev * levers.tail_decay, "tail"))
            continue
        usable = [(c, r) for c in ordered if (r := c.increment_ratio(age)) not in (None, 0) and c.new_customers > 0]
        picked = usable[-levers.n_cohorts:]
        if len(picked) < levers.min_cohorts_per_age or not picked:
            out.append(AgePoint(age, prev * levers.tail_decay, "tail" if out else "none"))
            continue
        den = sum(c.new_customers for c, _ in picked)
        out.append(AgePoint(age, sum(c.new_customers * r for c, r in picked) / den, "data", [c.month for c, _ in picked]))
    return out


def blend(live: Sequence[float], goal: Sequence[float], weight_on_goal: float) -> list[float]:
    return [weight_on_goal * g + (1.0 - weight_on_goal) * v for v, g in zip(live, goal)]


def choose_curve(live: Sequence[float], goal: Sequence[float], levers: Levers, asof: Sequence[float] | None = None) -> tuple[list[float], str | None]:
    """The curve that drives repeat revenue, capped at max_age (ages beyond are 0). Returns (curve, warning).
    A Goal or blended choice with no Goal curve loaded falls back to Live and says so; so does an as-of choice with no as-of curve."""
    warning = None
    source = levers.curve_source
    if source == "live":
        used = list(live)
    elif source == "asof":
        if asof is None or not any(asof):
            used, warning = list(live), "There is no data for the chosen as-of month, so the current curve is used."
        else:
            used = list(asof)
    elif not any(goal):
        used, warning = list(live), "No Goal curve is loaded, so the current curve is used."
    elif source == "goal":
        used = list(goal)
    else:
        used = blend(live, goal, levers.blend_weight or 0.0)
    return [v if a < levers.max_age + 1 else 0.0 for a, v in enumerate(used, start=1)], warning


def curve_label(levers: Levers, goal_snapshot: date | None = None) -> str:
    """Plain-English name of the curve that drives repeat revenue, e.g. 'As of Aug 2026, last 3 cohorts'."""
    n = f"last {levers.n_cohorts} cohort{'s' if levers.n_cohorts != 1 else ''}"
    src = levers.curve_source
    if src == "live":
        return f"Current, {n}"
    if src == "goal":
        return f"Goal, saved {month_text(goal_snapshot)}" if goal_snapshot else "Goal"
    if src == "asof":
        return f"As of {month_text(levers.asof_month)}, {n}"
    w = levers.blend_weight or 0.0
    return f"Blend: {w:.0%} Goal, {1 - w:.0%} current"


def curve_sum(curve: Sequence[float], ages: int = CURVE_AGES) -> float:
    return float(sum(curve[:ages]))


# ---------------------------------------------------------------------------------------------------------------- waterfall

@dataclass
class WaterfallCohort:
    month: date
    nc_rev: float
    source: str                                      # 'table' (new customers x first order) | 'drivers' (forward month)


def cohort_inputs(cohorts: Sequence[Cohort], nc_rev: Series | None, months: Sequence[date] = MONTHS) -> list[WaterfallCohort]:
    """Every cohort that contributes revenue: the loaded table rows, then each remaining axis month at the drivers' new-customer revenue.
    A month that has a table row always uses the table (new customers x first order)."""
    nc_rev = nc_rev or {}
    out: list[WaterfallCohort] = []
    have = set()
    for c in sort_cohorts(cohorts):
        have.add(c.month)
        if c.first_order is not None:
            out.append(WaterfallCohort(c.month, c.first_order_rev, "table"))
        else:
            out.append(WaterfallCohort(c.month, float(nc_rev.get(c.month, 0.0) or 0.0), "drivers"))
    for m in months:
        if m not in have:
            out.append(WaterfallCohort(m, float(nc_rev.get(m, 0.0) or 0.0), "drivers"))
    return sorted(out, key=lambda w: w.month)


def repeat_for(cohort_month: date, nc_rev: float, curve: Sequence[float], month: date, max_age: int = CURVE_AGES) -> float:
    """Repeat $ from one cohort in one calendar month (0 in the acquisition month and before). Sheet: MAX(0, NC-Rev x curve(age)), age > 60 -> 0."""
    age = months_between(cohort_month, month)
    if age < 1 or age > min(max_age, CURVE_AGES, len(curve)):
        return 0.0
    return max(0.0, nc_rev * curve[age - 1])


def waterfall(inputs: Sequence[WaterfallCohort], curve: Sequence[float], months: Sequence[date] = MONTHS, max_age: int = CURVE_AGES
              ) -> tuple[Series, dict[int, Series], dict[date, Series]]:
    """Repeat revenue by calendar month: (total, by cohort year, by cohort month). Series cover `months` only; cohorts earlier than the axis
    still contribute to the months they repeat in."""
    total: Series = {m: 0.0 for m in months}
    by_year: dict[int, Series] = {}
    by_cohort: dict[date, Series] = {}
    for w in inputs:
        row: Series = {}
        for m in months:
            v = repeat_for(w.month, w.nc_rev, curve, m, max_age)
            if v:
                row[m] = v
                total[m] += v
                y = by_year.setdefault(w.month.year, {})
                y[m] = y.get(m, 0.0) + v
        by_cohort[w.month] = row
    return total, by_year, by_cohort


def actual_repeat(cohorts: Sequence[Cohort]) -> Series:
    """Repeat $ the cohorts really earned, by calendar month (all months, also before the axis): increment per customer x new customers.
    Sheet layer 5 (`Hist Waterfall`, TOTAL actual repeat revenue)."""
    out: Series = {}
    for c in cohorts:
        for age in range(1, COHORT_AGES + 1):
            inc = c.increment(age)
            if inc is None:
                continue
            m = add_months(c.month, age)
            out[m] = out.get(m, 0.0) + inc * c.new_customers
    return out


# ---------------------------------------------------------------------------------------------------------------- freshness helpers

def last_closed_month(today: date) -> date:
    """The most recent calendar month that is over (first of month)."""
    return add_months(today.replace(day=1), -1)


def data_through(cohorts: Sequence[Cohort], today: date) -> date | None:
    """Last fully closed cohort month that is loaded with first-order data (the cohort export's frontier)."""
    closed = last_closed_month(today)
    months = [c.month for c in cohorts if c.new_customers > 0 and c.first_order is not None and c.month <= closed]
    return max(months) if months else None


def observed_through(cohorts: Sequence[Cohort], today: date) -> date | None:
    """Last calendar month (not after the last closed month) in which any loaded cumulative cell lands."""
    closed = last_closed_month(today)
    best = None
    for c in cohorts:
        for age in range(1, COHORT_AGES + 1):
            if c.cum_at(age) is None:
                continue
            m = add_months(c.month, age)
            if m <= closed and (best is None or m > best):
                best = m
    return best


# ---------------------------------------------------------------------------------------------------------------- as-of curves

def mask_asof(cohorts: Sequence[Cohort], asof: date) -> list[Cohort]:
    """The cohort table as it was at the end of month `asof` (first of month): cohorts after it are gone and every cumulative cell that lands in a
    later month is blank. Month `asof` itself is closed, so its cells count; its cohort counts with a first order and no repeat yet."""
    out: list[Cohort] = []
    for c in sort_cohorts(cohorts):
        if c.month > asof:
            continue
        seen = months_between(c.month, asof)                     # ages 1..seen have landed
        cum = tuple(v if (age <= seen) else None for age, v in enumerate(c.cum, start=1))
        out.append(replace(c, cum=cum))
    return out


def asof_curve(cohorts: Sequence[Cohort], asof: date, levers: Levers = DEFAULT_LEVERS) -> list[AgePoint]:
    """Live-curve rule applied to the table as of the end of `asof`: ages nobody had reached yet decay from the last age that had data."""
    return live_curve(mask_asof(cohorts, asof), levers)


def cohorts_per_age(cohorts: Sequence[Cohort], ages: int = COHORT_AGES) -> list[int]:
    """How many cohorts have an increment at each age 1..ages (what the Live curve could draw on)."""
    return [sum(1 for c in cohorts if c.new_customers > 0 and c.increment_ratio(a) not in (None, 0)) for a in range(1, ages + 1)]


def asof_snapshots(cohorts: Sequence[Cohort], levers: Levers, today: date) -> list[dict]:
    """One entry per month from the first cohort to the last closed month with data: whether an as-of curve can be built then (every age 1..12 has at
    least n_cohorts cohorts behind it), the fewest cohorts at ages 1..12, the curve's 60-month and 12-month multiples. The latest month equals the
    current curve."""
    latest = observed_through(cohorts, today)
    if latest is None:
        return []
    ordered = sort_cohorts(cohorts)
    out: list[dict] = []
    m = ordered[0].month
    while m <= latest:
        masked = mask_asof(ordered, m)
        counts = cohorts_per_age(masked, MIN_COMPUTABLE_AGES)
        least = min(counts) if counts else 0
        computable = least >= levers.n_cohorts
        entry = {"month": m, "computable": computable, "fewest_cohorts": least, "is_latest": m == latest, "lifetime": None, "twelve": None}
        if computable:
            curve = choose_curve([p.value for p in live_curve(masked, levers)], [], replace(levers, curve_source="live"))[0]
            entry["lifetime"], entry["twelve"] = curve_sum(curve), curve_sum(curve, 12)
        out.append(entry)
        m = add_months(m, 1)
    return out
