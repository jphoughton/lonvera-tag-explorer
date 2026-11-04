"""Levers of the demand forecast: scenario params under `inventory_demand`.

    inventory_demand = {
      "as_of": "2026-11-03",                       # optional: pin the forecast date (blank = today, Pacific time)
      "locations": {
        "revamp": {"baseline_days": 28,            # trailing complete days averaged into the baseline units per day
                   "lag_days": 0,                  # extra most-recent days of loaded data to ignore
                   "monthly_growth": 0.0,          # fraction per month, compounding from the as-of month (0.02 = 2%)
                   "seasonality": [1.0] * 12,      # index by calendar month, 1.00 = no adjustment
                   "tie_to_dtc_revenue": False,    # scale forward units with projected new-customer + repeat revenue per day
                   "products": {"bull": {"rate": None, "monthly_growth": None, "seasonality": None}}}}}   # per-product overrides

`parse` is tolerant (a bad value falls back to the default: the engine never fails on params); `validate_patch` is strict and builds the
merge patch for PATCH /scenarios.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ...queries import ApiError
from ..interfaces import AXIS_END, AXIS_START
from .constants import DEFAULT_BASELINE_DAYS, LOCATIONS, MODULE_KEY

MAX_BASELINE_DAYS = 365
MAX_LAG_DAYS = 60
GROWTH_RANGE = (-0.5, 1.0)
SEASON_RANGE = (0.0, 10.0)
MAX_RATE = 100_000.0
_SLUG_OK = lambda s: isinstance(s, str) and 0 < len(s) <= 40 and all(c.islower() or c.isdigit() or c == "_" for c in s)   # noqa: E731


@dataclass(frozen=True)
class ProductLevers:
    rate: float | None = None                          # typed units per day; replaces the trailing baseline
    monthly_growth: float | None = None
    seasonality: tuple[float, ...] | None = None


@dataclass(frozen=True)
class LocationLevers:
    baseline_days: int = 30
    lag_days: int = 0
    monthly_growth: float = 0.0
    seasonality: tuple[float, ...] = (1.0,) * 12
    tie_to_dtc_revenue: bool = False
    products: dict[str, ProductLevers] = field(default_factory=dict)

    def growth(self, product: str) -> float:
        p = self.products.get(product)
        return p.monthly_growth if p is not None and p.monthly_growth is not None else self.monthly_growth

    def season(self, product: str) -> tuple[float, ...]:
        p = self.products.get(product)
        return p.seasonality if p is not None and p.seasonality is not None else self.seasonality

    def rate_override(self, product: str) -> float | None:
        p = self.products.get(product)
        return p.rate if p is not None else None


@dataclass(frozen=True)
class Levers:
    as_of: date | None = None
    locations: dict[str, LocationLevers] = field(default_factory=dict)

    def loc(self, location: str) -> LocationLevers:
        return self.locations.get(location) or LocationLevers(baseline_days=DEFAULT_BASELINE_DAYS.get(location, 30))


# ------------------------------------------------------------------------------------------------------------------ tolerant parse

def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def _int_in(v: Any, lo: int, hi: int, default: int) -> int:
    n = _num(v)
    return int(n) if n is not None and n == int(n) and lo <= n <= hi else default


def _float_in(v: Any, lo: float, hi: float, default: float | None) -> float | None:
    n = _num(v)
    return n if n is not None and lo <= n <= hi else default


def _season(v: Any, default: tuple[float, ...] | None) -> tuple[float, ...] | None:
    if isinstance(v, (list, tuple)) and len(v) == 12:
        vals = [_float_in(x, *SEASON_RANGE, None) for x in v]
        if all(x is not None for x in vals):
            return tuple(vals)           # type: ignore[arg-type]
    return default


def parse_date(v: Any) -> date | None:
    if isinstance(v, date):
        return v
    if isinstance(v, str):
        try:
            return date.fromisoformat(v[:10])
        except ValueError:
            return None
    return None


def parse(params: dict | None) -> Levers:
    """Levers from a scenario's effective params (`scn.params`) or from the `inventory_demand` namespace itself."""
    ns = params or {}
    ns = ns.get(MODULE_KEY, ns) if isinstance(ns, dict) else {}
    if not isinstance(ns, dict):
        ns = {}
    as_of = parse_date(ns.get("as_of"))
    raw_locs = ns.get("locations") if isinstance(ns.get("locations"), dict) else {}
    locs: dict[str, LocationLevers] = {}
    for loc in LOCATIONS:
        d = raw_locs.get(loc) if isinstance(raw_locs.get(loc), dict) else {}
        prods: dict[str, ProductLevers] = {}
        for slug, pd in (d.get("products") or {}).items() if isinstance(d.get("products"), dict) else []:
            if not _SLUG_OK(slug) or not isinstance(pd, dict):
                continue
            prods[slug] = ProductLevers(rate=_float_in(pd.get("rate"), 0.0, MAX_RATE, None),
                                        monthly_growth=_float_in(pd.get("monthly_growth"), *GROWTH_RANGE, None),
                                        seasonality=_season(pd.get("seasonality"), None))
        locs[loc] = LocationLevers(
            baseline_days=_int_in(d.get("baseline_days"), 1, MAX_BASELINE_DAYS, DEFAULT_BASELINE_DAYS[loc]),
            lag_days=_int_in(d.get("lag_days"), 0, MAX_LAG_DAYS, 0),
            monthly_growth=_float_in(d.get("monthly_growth"), *GROWTH_RANGE, 0.0) or 0.0,
            seasonality=_season(d.get("seasonality"), (1.0,) * 12) or (1.0,) * 12,
            tie_to_dtc_revenue=d.get("tie_to_dtc_revenue") is True,
            products=prods)
    return Levers(as_of=as_of, locations=locs)


def to_dict(lv: LocationLevers) -> dict:
    """The location's levers as the JSON the page edits."""
    return {"baseline_days": lv.baseline_days, "lag_days": lv.lag_days, "monthly_growth": lv.monthly_growth,
            "seasonality": list(lv.seasonality), "tie_to_dtc_revenue": lv.tie_to_dtc_revenue,
            "products": {s: {"rate": p.rate, "monthly_growth": p.monthly_growth,
                             "seasonality": list(p.seasonality) if p.seasonality else None} for s, p in lv.products.items()}}


# ------------------------------------------------------------------------------------------------------------------ strict validation

def _bad(msg: str) -> ApiError:
    return ApiError(400, msg)


def _check_num(v: Any, name: str, lo: float, hi: float, nullable: bool = False) -> float | None:
    if v is None and nullable:
        return None
    n = _num(v)
    if n is None:
        raise _bad(f"{name} must be a number")
    if not lo <= n <= hi:
        raise _bad(f"{name} must be between {lo:g} and {hi:g}")
    return n


def _check_season(v: Any, name: str, nullable: bool = False) -> list[float] | None:
    if v is None and nullable:
        return None
    if not isinstance(v, list) or len(v) != 12:
        raise _bad(f"{name} must be a list of 12 numbers (January to December)")
    return [_check_num(x, f"{name}[{i}]", *SEASON_RANGE) for i, x in enumerate(v)]


def validate_patch(body: dict) -> dict:
    """The body of PUT /inputs -> merge patch for params['inventory_demand']. Raises ApiError(400) naming the field."""
    extra = set(body) - {"scenario", "expected_version", "as_of", "locations"}
    if extra:
        raise _bad(f"unknown field {sorted(extra)[0]!r}")
    patch: dict[str, Any] = {}
    if "as_of" in body:
        v = body["as_of"]
        if v is None or v == "":
            patch["as_of"] = None
        else:
            d = parse_date(v) if isinstance(v, str) and len(v) == 10 else None
            if d is None:
                raise _bad("as_of must be an ISO date (YYYY-MM-DD) or null")
            if not AXIS_START <= d <= AXIS_END:
                raise _bad(f"as_of must fall between {AXIS_START.isoformat()} and {AXIS_END.isoformat()}")
            patch["as_of"] = d.isoformat()
    if "locations" in body:
        locs = body["locations"]
        if not isinstance(locs, dict) or not locs:
            raise _bad("locations must be a non-empty object")
        out: dict[str, Any] = {}
        for loc, d in locs.items():
            if loc not in LOCATIONS:
                raise _bad(f"unknown location {loc!r}; one of {', '.join(LOCATIONS)}")
            if not isinstance(d, dict):
                raise _bad(f"locations.{loc} must be an object")
            unknown = set(d) - {"baseline_days", "lag_days", "monthly_growth", "seasonality", "tie_to_dtc_revenue", "products"}
            if unknown:
                raise _bad(f"locations.{loc}: unknown field {sorted(unknown)[0]!r}")
            o: dict[str, Any] = {}
            if "baseline_days" in d:
                n = _check_num(d["baseline_days"], f"{loc}.baseline_days", 1, MAX_BASELINE_DAYS)
                if n != int(n):
                    raise _bad(f"{loc}.baseline_days must be a whole number of days")
                o["baseline_days"] = int(n)
            if "lag_days" in d:
                n = _check_num(d["lag_days"], f"{loc}.lag_days", 0, MAX_LAG_DAYS)
                if n != int(n):
                    raise _bad(f"{loc}.lag_days must be a whole number of days")
                o["lag_days"] = int(n)
            if "monthly_growth" in d:
                o["monthly_growth"] = _check_num(d["monthly_growth"], f"{loc}.monthly_growth", *GROWTH_RANGE)
            if "seasonality" in d:
                o["seasonality"] = _check_season(d["seasonality"], f"{loc}.seasonality")
            if "tie_to_dtc_revenue" in d:
                if not isinstance(d["tie_to_dtc_revenue"], bool):
                    raise _bad(f"{loc}.tie_to_dtc_revenue must be true or false")
                o["tie_to_dtc_revenue"] = d["tie_to_dtc_revenue"]
            if "products" in d:
                prods = d["products"]
                if not isinstance(prods, dict):
                    raise _bad(f"{loc}.products must be an object")
                po: dict[str, Any] = {}
                for slug, pd in prods.items():
                    if not _SLUG_OK(slug):
                        raise _bad(f"{loc}.products: bad product key {slug!r}")
                    if not isinstance(pd, dict):
                        raise _bad(f"{loc}.products.{slug} must be an object")
                    bad = set(pd) - {"rate", "monthly_growth", "seasonality"}
                    if bad:
                        raise _bad(f"{loc}.products.{slug}: unknown field {sorted(bad)[0]!r}")
                    q: dict[str, Any] = {}
                    if "rate" in pd:
                        q["rate"] = _check_num(pd["rate"], f"{loc}.{slug}.rate", 0, MAX_RATE, nullable=True)
                    if "monthly_growth" in pd:
                        q["monthly_growth"] = _check_num(pd["monthly_growth"], f"{loc}.{slug}.monthly_growth", *GROWTH_RANGE, nullable=True)
                    if "seasonality" in pd:
                        q["seasonality"] = _check_season(pd["seasonality"], f"{loc}.{slug}.seasonality", nullable=True)
                    po[slug] = q
                o["products"] = po
            out[loc] = o
        patch["locations"] = out
    if not patch:
        raise _bad("nothing to change: send as_of and/or locations")
    return patch
