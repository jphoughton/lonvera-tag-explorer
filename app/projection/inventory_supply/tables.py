"""Grid payloads ({months, status, rows, captions}) for the shared monthly grid: burn per location, inventory value, cash outlay.

Row `fmt` / `kind` follow the UI shell (usd0 | num | text ...; calc | subtotal | total | header | ratio), not interfaces.Table.
"""
from __future__ import annotations

import math
from datetime import date
from typing import Any

from ..interfaces import MONTHS, month_floor
from .model import LOCATIONS, LOCATION_LONG, PRODUCTS, SupplyResult, product_name

BURN_CAPTION = "Source: stock counts, demand forecast, purchase orders and FBA transfers"


def _clean(v: Any) -> Any:
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return v


def _row(key: str, label: str, values: list, *, kind: str = "calc", fmt: str = "num", digits: int | None = None, note: str | None = None,
         input: bool = False) -> dict:
    r: dict[str, Any] = {"key": key, "label": label, "kind": kind, "fmt": fmt, "values": [_clean(v) for v in values], "input": input}
    if digits is not None:
        r["digits"] = digits
    if note:
        r["note"] = note
    return r


def _line(res: SupplyResult, key: tuple, months: list[date], missing: float | None = 0.0) -> list:
    s = res.burn.get(key, {})
    return [s.get(m, missing) for m in months]


def _products(res: SupplyResult, loc: str) -> list[str]:
    ps = {p for l, p in res.combos if l == loc} if loc != "total" else {p for _, p in res.combos}
    return sorted(ps, key=lambda p: PRODUCTS.index(p) if p in PRODUCTS else 99)


def _status_chip(res: SupplyResult, loc: str, prod: str, months: list[date]) -> list[str]:
    st = res.status_at.get((loc, prod), {})
    return [st.get(m, "") for m in months]


def burn_table(res: SupplyResult, loc: str) -> dict:
    months = res.months
    status = ["projected"] * len(months)
    rows: list[dict] = []
    for prod in _products(res, loc):
        base = f"{loc}.{prod}"
        rows.append({"key": f"{base}.h", "label": product_name(prod), "kind": "header", "fmt": "num", "values": [None] * len(months), "input": False})
        rows.append(_row(f"{base}.opening", "Opening stock", _line(res, (loc, prod, "opening"), months)))
        rows.append(_row(f"{base}.demand", "Demand", _line(res, (loc, prod, "demand"), months)))
        if loc == "amazon":
            rows.append(_row(f"{base}.inbound", "Inbound: FBA transfers", _line(res, (loc, prod, "inbound"), months)))
        elif loc == "total":
            rows.append(_row(f"{base}.inbound", "Inbound: purchase orders", _line(res, (loc, prod, "inbound"), months)))
        else:
            rows.append(_row(f"{base}.inbound", "Inbound: purchase orders", _line(res, (loc, prod, "inbound"), months)))
        if loc == "revamp":
            rows.append(_row(f"{base}.out", "Transfers out to FBA", _line(res, (loc, prod, "out"), months)))
        rows.append(_row(f"{base}.closing", "Closing stock", _line(res, (loc, prod, "closing"), months), kind="subtotal"))
        rows.append(_row(f"{base}.cover", "Months of cover at close", _line(res, (loc, prod, "cover"), months, None), kind="ratio", digits=1))
        if loc == "total":
            rows.append(_row(f"{base}.check", "In transit between locations", _line(res, (loc, prod, "check"), months), kind="ratio",
                             note="Closing minus (opening + purchase-order inbound - demand): units shipped to FBA in one month and received in another."))
        rows.append(_row(f"{base}.status", "Status", _status_chip(res, loc, prod, months), kind="calc", fmt="text"))
        if loc == "amazon":
            rows.append(_row(f"{base}.to_send", "Units to send to reach target cover", _line(res, (loc, prod, "to_send"), months), kind="ratio"))
    caps = [BURN_CAPTION]
    return {"months": [m.isoformat() for m in months], "status": status, "rows": rows, "captions": caps, "location": loc,
            "location_label": LOCATION_LONG.get(loc, "All locations")}


def value_table(res: SupplyResult) -> dict:
    months = res.months
    rows: list[dict] = []
    for loc in LOCATIONS:
        prods = _products(res, loc)
        if not prods:
            continue
        rows.append({"key": f"{loc}.h", "label": LOCATION_LONG[loc], "kind": "header", "fmt": "usd0", "values": [None] * len(months), "input": False})
        for prod in prods:
            v = res.value_by_location.get((loc, prod), {})
            rows.append(_row(f"{loc}.{prod}", product_name(prod), [v.get(m, 0.0) for m in months], fmt="usd0"))
        tot = [sum(res.value_by_location.get((loc, p), {}).get(m, 0.0) for p in prods) for m in months]
        rows.append(_row(f"{loc}.total", f"{LOCATION_LONG[loc].split(' (')[0]} total", tot, kind="subtotal", fmt="usd0"))
    rows.append({"key": "all.h", "label": "All locations", "kind": "header", "fmt": "usd0", "values": [None] * len(months), "input": False})
    for prod in _products(res, "total"):
        rows.append(_row(f"all.{prod}", product_name(prod),
                         [sum(res.value_by_location.get((l, prod), {}).get(m, 0.0) for l in LOCATIONS) for m in months], fmt="usd0"))
    rows.append(_row("all.total", "Total inventory value", [res.value.get(m, 0.0) for m in months], kind="total", fmt="usd0"))
    units = [sum(res.burn.get(("total", p, "closing"), {}).get(m, 0.0) for p in _products(res, "total")) for m in months]
    rows.append(_row("all.units", "Total units", units, kind="ratio"))
    rows.append(_row("all.per_unit", "Blended cost per unit", [(res.value.get(m, 0.0) / u) if u else None for m, u in zip(months, units)],
                     kind="ratio", fmt="usd", digits=2))
    return {"months": [m.isoformat() for m in months], "status": ["projected"] * len(months), "rows": rows,
            "captions": ["Source: closing stock at the China landed unit cost per product"]}


def outlay_table(res: SupplyResult) -> dict:
    months = list(MONTHS)
    start = res.start or month_floor(date.today())
    status = ["actual" if m < start else "projected" for m in months]
    intl = [res.intl_outlay.get(m, 0.0) for m in months]
    dep = [res.deposits.get(m, 0.0) for m in months]
    bal = [res.balances.get(m, 0.0) for m in months]
    tot = [d + b for d, b in zip(dep, bal)]
    cum, run = [], 0.0
    for t in tot:
        run += t
        cum.append(run)
    rows = [
        _row("deposits", "Deposits (order month)", dep, fmt="usd0"),
        _row("balances", "Balances (landing month)", bal, fmt="usd0"),
        _row("total", "Total inventory cash outlay", tot, kind="total", fmt="usd0"),
        _row("intl", "of which international 3PL orders", intl, kind="ratio", fmt="usd0"),
        _row("cumulative", "Cumulative outlay", cum, kind="ratio", fmt="usd0"),
    ]
    return {"months": [m.isoformat() for m in months], "status": status, "rows": rows,
            "captions": ["Source: purchase orders (deposit in the order month, balance in the landing month; cancelled orders excluded)"]}
