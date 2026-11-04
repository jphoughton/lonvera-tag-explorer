"""Payloads for the page: the monthly units grid, the inputs card and the daily chart series. Pure functions over a DemandDetail.

The grid payload uses the shared grid's vocabulary (kind: calc | subtotal | total | header | ratio; fmt: num | pct)."""
from __future__ import annotations

from datetime import date, timedelta

from ..interfaces import MONTHS, Series, add_months, status_for
from . import levers as lv
from .constants import LOCATION_LABELS, LOCATION_SHORT, LOCATIONS, PRODUCT_ORDER, STALE_AFTER_DAYS, product_label
from .engine import DemandDetail, days_in


def _vals(s: Series | None, missing: float | None = 0.0) -> list[float | None]:
    s = s or {}
    return [s.get(m, missing) for m in MONTHS]


def _row(key: str, label: str, values, *, kind: str = "calc", fmt: str = "num", agg: str | None = None, digits: int | None = None,
         note: str | None = None) -> dict:
    r = {"key": key, "label": label, "kind": kind, "fmt": fmt, "values": [None] * len(MONTHS) if values is None else values, "input": False}
    if agg:
        r["agg"] = agg
    if digits is not None:
        r["digits"] = digits
    if note:
        r["note"] = note
    return r


def _products(d: DemandDetail) -> list[str]:
    seen = {p for (loc, p) in d.units if loc in LOCATIONS}
    return [p for p in PRODUCT_ORDER if p in seen] + sorted(p for p in seen if p not in PRODUCT_ORDER)


def _short(d: date) -> str:
    return f"{d.strftime('%b')} {d.day}"


def units_table(d: DemandDetail) -> dict:
    """Units by location and product, month by month (months before the as-of month are actuals)."""
    m0 = d.current_month
    through_month = add_months(m0, -1) if m0 else None
    rows: list[dict] = []
    prods = _products(d)

    rows.append(_row("hdr.total", "All locations", None, kind="header"))
    rows.append(_row("total", "Total units", _vals(d.total), kind="total"))
    for loc in LOCATIONS:
        if loc in d.location_total:
            rows.append(_row(f"loc.{loc}", LOCATION_SHORT[loc], _vals(d.location_total[loc])))
    for loc in LOCATIONS:
        if loc in d.share:
            rows.append(_row(f"share.{loc}", f"{LOCATION_SHORT[loc]} share of units", _vals(d.share[loc]), kind="ratio", fmt="pct", agg="none"))

    rows.append(_row("hdr.product", "By product, all locations", None, kind="header"))
    for p in prods:
        rows.append(_row(f"prod.{p}", product_label(p), _vals(d.product_total.get(p))))

    for loc in LOCATIONS:
        if loc not in d.location_total:
            continue
        rows.append(_row(f"hdr.{loc}", LOCATION_LABELS[loc], None, kind="header"))
        for p in prods:
            if (loc, p) in d.units:
                rows.append(_row(f"cell.{loc}.{p}", product_label(p), _vals(d.units[(loc, p)])))
        rows.append(_row(f"locsum.{loc}", "Location total", _vals(d.location_total[loc]), kind="subtotal"))

    rows.append(_row("hdr.perday", "Units per day", None, kind="header"))
    for loc in LOCATIONS:
        if loc in d.location_total:
            rows.append(_row(f"perday.{loc}", LOCATION_SHORT[loc], [None if v is None else v / days_in(m) for m, v in zip(MONTHS, _vals(d.location_total[loc]))],
                             kind="ratio", agg="avg", digits=1))
    rows.append(_row("perday.total", "All locations", [None if v is None else v / days_in(m) for m, v in zip(MONTHS, _vals(d.total))],
                     kind="ratio", agg="avg", digits=1))

    names = {"revamp": "Warehance", "amazon": "Amazon", "intl": "international"}
    through = [f"{names[loc]} through {_short(d.last_full_day[loc])}" for loc in LOCATIONS if d.last_full_day.get(loc)]
    caps = [("Units from " + ", ".join(through) + ".") if through else "No units are loaded yet."]
    if m0:
        caps.append(f"{m0.strftime('%b %Y')}* is units so far plus the forecast for the remaining days.")
    return {"months": [m.isoformat() for m in MONTHS], "status": status_for(through_month), "rows": rows, "captions": caps}


# ------------------------------------------------------------------------------------------------------------------ inputs card

def feed_status(as_of: date, last_day: date | None, location: str, estimate_days: int, loaded_at=None, source: str | None = None) -> dict:
    """Freshness chip data for one location."""
    stale_after = STALE_AFTER_DAYS[location]
    if last_day is None:
        return {"status": "none", "data_through": None, "age_days": None, "stale_after_days": stale_after, "estimate_days": 0,
                "source": source, "loaded_at": loaded_at.isoformat() if loaded_at else None}
    age = max(0, (as_of - last_day).days)
    return {"status": "stale" if age > stale_after else "ok", "data_through": last_day.isoformat(), "age_days": age, "stale_after_days": stale_after,
            "estimate_days": estimate_days, "source": source, "loaded_at": loaded_at.isoformat() if loaded_at else None}


def inputs_payload(d: DemandDetail, params: dict, inputs_loads: dict[str, dict], pinned: bool) -> dict:
    levers = lv.parse(params)
    locs = []
    for loc in LOCATIONS:
        L = levers.loc(loc)
        prods = sorted({p for (l, p) in d.rates if l == loc}, key=lambda p: PRODUCT_ORDER.index(p) if p in PRODUCT_ORDER else 99)
        wtotal = sum(d.rates.get((loc, p), 0.0) for p in prods)
        plist = []
        for p in prods:
            base = d.window_units.get((loc, p), 0.0) / L.baseline_days
            ov = L.rate_override(p)
            plist.append({"key": p, "label": product_label(p), "window_units": d.window_units.get((loc, p), 0.0), "baseline_rate": base,
                          "rate_override": ov, "effective_rate": d.rates.get((loc, p), 0.0),
                          "mix": (d.rates.get((loc, p), 0.0) / wtotal) if wtotal else 0.0,
                          "monthly_growth": L.products[p].monthly_growth if p in L.products else None})
        load = inputs_loads.get(loc) or {}
        feed = feed_status(d.as_of, d.last_full_day.get(loc), loc, d.estimate_days_in_window.get(loc, 0), load.get("at"), load.get("source"))
        w = d.window.get(loc)
        locs.append({"key": loc, "label": LOCATION_LABELS[loc], "short": LOCATION_SHORT[loc], "levers": lv.to_dict(L),
                     "products": plist, "window": {"start": w[0].isoformat(), "end": w[1].isoformat()} if w else None,
                     "total_rate": sum(p["effective_rate"] for p in plist), "feed": feed,
                     "mtd_days": d.actual_days.get(loc, 0)})
    return {"as_of": d.as_of.isoformat(), "as_of_pinned": pinned, "current_month": d.current_month.isoformat(), "locations": locs,
            "warnings": list(d.warnings)}


# ------------------------------------------------------------------------------------------------------------------ daily chart

def daily_payload(rows: list[dict], start: date, end: date, rate: float, window: tuple[date, date] | None, location: str, product: str | None) -> dict:
    by_day = {r["day"]: r for r in rows}
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    units = [float(by_day[d]["units"]) if d in by_day else 0.0 for d in days]
    est = [bool(by_day[d]["is_estimate"]) if d in by_day else False for d in days]
    loaded = [d in by_day for d in days]
    ma7 = []
    for i in range(len(days)):
        lo = max(0, i - 6)
        ma7.append(sum(units[lo:i + 1]) / (i + 1 - lo) if i >= 6 else None)
    baseline = [rate if window and window[0] <= d <= window[1] else None for d in days]
    return {"location": location, "product": product or "all", "label": product_label(product) if product else "All products",
            "days": [d.isoformat() for d in days], "units": units, "estimate": est, "loaded": loaded, "ma7": ma7, "baseline": baseline,
            "rate": rate, "window": {"start": window[0].isoformat(), "end": window[1].isoformat()} if window else None}
