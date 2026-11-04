"""Plausibility checks for a cohort upload: blocking errors versus warnings, and what the file does to the retention multiples.

A file that passes the parser and the closed-months rules can still hold numbers nobody should load (a first order of $99,999, a negative amount,
3,614 customers becoming 12). The preview runs these checks on the table as it WOULD be after the upload and lists every problem in plain words.
Any error makes the upload not committable; warnings are shown and do not block.

Blocking
    - a negative amount (customers, first order, any cumulative cell)
    - a first order per customer outside the allowed band ($5 to $500 by default)
    - a first order more than 40% away from the typical first order of the cohorts around it (new or changed cohorts with at least 1,000 customers)
    - a stored cohort whose customer count changes by more than 60%
    - cumulative dollars per customer that fall by more than 5% from one month to the next (new or changed cohorts)
    - a resulting 60-month or 12-month repeat multiple outside its allowed band (0.5x to 5x and 0.2x to 3x by default)
Warning
    - a first-order of zero, a dip of 0.5% to 5%, a new cohort whose customer count sits more than 60% from its neighbours, a multiple that moves more than
      15%, a file that holds far fewer cohorts than the table
The bands are the `upload_*` variables of the cohorts module (docs/projection/MODULE_cohorts.md, Variables).
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date
from statistics import median
from typing import Sequence

from . import engine as eng
from .store import CohortRow

NEIGHBORS = 3                        # cohorts on each side that count as "the cohorts around it"
NEIGHBOR_MIN_CUSTOMERS = 1000        # smaller cohorts are too noisy to be a reference for a first order, or to be checked against one
DIP_ERROR = 0.05                     # cumulative dollars falling by more than this share of the month before block the upload
DIP_WARN = 0.005
MOVE_WARN = 0.15                     # a multiple that moves by more than this share is worth a second look
FEW_COHORTS = 0.25                   # a file with under this share of the stored cohorts gets a note


@dataclass
class Issue:
    level: str                       # error | warning
    code: str
    message: str
    month: date | None = None

    def to_dict(self) -> dict:
        return {"level": self.level, "code": self.code, "message": self.message, "month": self.month.isoformat() if self.month else None}


@dataclass
class Report:
    issues: list[Issue] = field(default_factory=list)
    impact: dict = field(default_factory=dict)

    @property
    def errors(self) -> list[str]:
        return [i.message for i in self.issues if i.level == "error"]

    @property
    def warnings(self) -> list[str]:
        return [i.message for i in self.issues if i.level == "warning"]

    def add(self, level: str, code: str, message: str, month: date | None = None) -> None:
        self.issues.append(Issue(level, code, message, month))


# ---------------------------------------------------------------------------------------------------------------- merged state

def merged_state(existing: Sequence[eng.Cohort], rows: Sequence[CohortRow]) -> list[eng.Cohort]:
    """The cohort table as it will be once `rows` are written: head fields from the row where it gives one, stored cells kept unless the row revises them."""
    by_month = {c.month: c for c in existing}
    for r in rows:
        old = by_month.get(r.month)
        cum = list(old.cum) if old else [None] * eng.COHORT_AGES
        cum += [None] * (eng.COHORT_AGES - len(cum))
        for age, v in r.cum.items():
            if 1 <= age <= eng.COHORT_AGES and v is not None:
                cum[age - 1] = v
        pick = lambda new, prev: new if new is not None else prev                 # noqa: E731
        by_month[r.month] = eng.Cohort(
            month=r.month, new_customers=r.new_customers, first_order=pick(r.first_order, old.first_order if old else None),
            first_order_realized=pick(r.first_order_realized, old.first_order_realized if old else None),
            repeat_90d=pick(r.repeat_90d, old.repeat_90d if old else None), cac=pick(r.cac, old.cac if old else None),
            payback_months=pick(r.payback_months, old.payback_months if old else None), cum=tuple(cum),
            nc_rev_reference=old.nc_rev_reference if old else None)
    return eng.sort_cohorts(by_month.values())


def _same(a: float | None, b: float | None, eps: float) -> bool:
    if a is None or b is None:
        return a is b
    return abs(a - b) <= eps * max(1.0, abs(a), abs(b))


def _touched(r: CohortRow, old: eng.Cohort | None) -> bool:
    """New, or any head field or cumulative cell differs from what is stored."""
    if old is None:
        return True
    heads = ((r.new_customers, old.new_customers), (r.first_order, old.first_order), (r.first_order_realized, old.first_order_realized),
             (r.repeat_90d, old.repeat_90d), (r.cac, old.cac), (r.payback_months, old.payback_months))
    if any(new is not None and not _same(new, prev, 1e-5) for new, prev in heads):
        return True
    return any(not _same(v, old.cum_at(a), 1e-7) for a, v in r.cum.items())


def _neighbors(table: Sequence[eng.Cohort], month: date) -> list[eng.Cohort]:
    months = [c.month for c in table]
    i = months.index(month)
    return [c for c in list(table[max(0, i - NEIGHBORS):i]) + list(table[i + 1:i + 1 + NEIGHBORS])]


# ---------------------------------------------------------------------------------------------------------------- the checks

def _money(v: float) -> str:
    return f"${v:,.2f}" if abs(v) < 1000 else f"${v:,.0f}"


def _pct(v: float) -> str:
    return f"{abs(v) * 100:.0f}%"


def multiples(cohorts: Sequence[eng.Cohort], levers: eng.Levers) -> tuple[float, float]:
    """(60-month, 12-month) repeat per first-order dollar of the current curve, the way the model reads it."""
    live = [p.value for p in eng.live_curve(cohorts, levers)]
    curve, _ = eng.choose_curve(live, [], replace(levers, curve_source="live", asof_month=None))
    return eng.curve_sum(curve), eng.curve_sum(curve, 12)


def check(rows: Sequence[CohortRow], existing: Sequence[eng.Cohort], bands: eng.Bands, levers: eng.Levers) -> Report:
    rep = Report()
    if not rows:
        return rep
    old_by = {c.month: c for c in existing}
    after = merged_state(existing, rows)
    after_by = {c.month: c for c in after}

    for r in rows:
        label = eng.month_text(r.month)
        old = old_by.get(r.month)
        touched = _touched(r, old)
        merged = after_by[r.month]

        # ---- negative amounts
        if r.new_customers < 0:
            rep.add("error", "negative_customers", f"{label}: the number of new customers is negative ({r.new_customers:,.0f}).", r.month)
        if r.first_order is not None and r.first_order < 0:
            rep.add("error", "negative_first_order", f"{label}: the first order per customer is negative ({_money(r.first_order)}).", r.month)
        neg_cells = [a for a, v in sorted(r.cum.items()) if v is not None and v < 0]
        if neg_cells:
            rep.add("error", "negative_cumulative", f"{label}: month {neg_cells[0]} holds a negative amount per customer ({_money(r.cum[neg_cells[0]])}).", r.month)

        # ---- first order: band, then neighbours
        fo = merged.first_order
        if fo is not None and fo >= 0 and not bands.first_order_min <= fo <= bands.first_order_max:
            rep.add("error", "first_order_band", f"{label}: the first order per customer is {_money(fo)}, outside the allowed ${bands.first_order_min:,.0f} to "
                                                 f"${bands.first_order_max:,.0f}.", r.month)
        elif fo is not None and fo >= 0 and touched and merged.new_customers >= NEIGHBOR_MIN_CUSTOMERS:
            refs = [n.first_order for n in _neighbors(after, r.month) if n.first_order and n.new_customers >= NEIGHBOR_MIN_CUSTOMERS]
            if refs:
                ref = median(refs)
                if ref and abs(fo / ref - 1) > bands.first_order_tol:
                    rep.add("error", "first_order_neighbors",
                            f"{label}: the first order per customer, {_money(fo)}, is {_pct(fo / ref - 1)} {'above' if fo > ref else 'below'} the {_money(ref)} of the cohorts "
                            f"around it (limit {_pct(bands.first_order_tol)}).", r.month)
        if r.first_order is None and old is None:
            rep.add("warning", "no_first_order", f"{label}: no first order per customer; this cohort cannot feed the curve.", r.month)

        # ---- customers: a stored cohort that changes a lot blocks; a new cohort far from its neighbours is a note
        if old is not None and old.new_customers > 0 and r.new_customers >= 0 and abs(r.new_customers / old.new_customers - 1) > bands.customers_tol:
            rep.add("error", "customers_changed", f"{label}: new customers change from {old.new_customers:,.0f} to {r.new_customers:,.0f} "
                                                  f"({(r.new_customers / old.new_customers - 1) * 100:+.0f}%), beyond the {_pct(bands.customers_tol)} limit.", r.month)
        elif old is None and r.new_customers > 0:
            refs = [n.new_customers for n in _neighbors(after, r.month) if n.new_customers > 0]
            if len(refs) >= 2:
                ref = median(refs)
                if ref and abs(r.new_customers / ref - 1) > bands.customers_tol:
                    rep.add("warning", "customers_neighbors", f"{label}: {r.new_customers:,.0f} new customers is {_pct(r.new_customers / ref - 1)} "
                                                              f"{'above' if r.new_customers > ref else 'below'} the {ref:,.0f} of the cohorts around it.", r.month)

        # ---- cumulative dollars per customer should not fall
        if touched:
            prev = merged.first_order
            for age in range(1, eng.COHORT_AGES + 1):
                cur = merged.cum_at(age)
                if cur is None:
                    continue
                if prev and cur >= 0 and prev - cur > DIP_WARN * prev:
                    drop = (prev - cur) / prev
                    level = "error" if drop > DIP_ERROR else "warning"
                    rep.add(level, "cumulative_falls", f"{label}: dollars per customer fall from {_money(prev)} to {_money(cur)} at month {age}"
                                                       + (f"; a running total cannot fall by more than {_pct(DIP_ERROR)}." if level == "error" else "."), r.month)
                    break
                prev = cur

    # ---- what the file does to the multiples
    life_after, twelve_after = multiples(after, levers)
    life_before = twelve_before = None
    if existing:
        life_before, twelve_before = multiples(existing, levers)
    rep.impact = {
        "lifetime": {"before": life_before, "after": life_after, "min": bands.lifetime_min, "max": bands.lifetime_max},
        "twelve": {"before": twelve_before, "after": twelve_after, "min": bands.twelve_min, "max": bands.twelve_max},
    }
    was = lambda v: f" (now {v:.2f}x)" if v is not None else ""                                   # noqa: E731
    if not bands.lifetime_min <= life_after <= bands.lifetime_max:
        rep.add("error", "lifetime_band", f"The repeat revenue per first-order dollar over 60 months would be {life_after:.2f}x, outside the allowed "
                                          f"{bands.lifetime_min:g}x to {bands.lifetime_max:g}x{was(life_before)}.")
    if not bands.twelve_min <= twelve_after <= bands.twelve_max:
        rep.add("error", "twelve_band", f"The repeat revenue per first-order dollar over the first 12 months would be {twelve_after:.2f}x, outside the allowed "
                                        f"{bands.twelve_min:g}x to {bands.twelve_max:g}x{was(twelve_before)}.")
    for name, b, a in (("60-month", life_before, life_after), ("12-month", twelve_before, twelve_after)):
        if b and abs(a / b - 1) > MOVE_WARN and bands.lifetime_min <= life_after <= bands.lifetime_max:
            rep.add("warning", "multiple_moves", f"The {name} repeat multiple moves from {b:.2f}x to {a:.2f}x ({(a / b - 1) * 100:+.0f}%).")

    if existing and len(existing) >= 8 and len(rows) < max(2, FEW_COHORTS * len(existing)):
        rep.add("warning", "few_cohorts", f"The file holds {len(rows)} cohort{'s' if len(rows) != 1 else ''}; the table holds {len(existing)}. "
                                          f"Loaded as given: the other cohorts keep their stored values.")
    return rep
