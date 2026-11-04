"""The inventory_supply engine: stock burn per location x product, reorder status, inventory value, purchase-order cash outlay, warnings.

Pure functions over `Inputs` and a `DemandResult`: no database, no clock (the reference day is passed in). Mirrors the sheet's tabs
`Warehance/Amazon/Intl/Total Inventory Burn`, `Warehance/Intl Reorder`, `Inventory Value`, `Inventory Cash Outlay` and the computed columns of
`Purchase Orders`.
"""
from __future__ import annotations

import calendar
import statistics
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from ..interfaces import AXIS_END, AXIS_START, MONTHS, DemandResult, add_months, month_floor
from .model import (DEFAULT_COMBOS, LOCATIONS, PO, PRODUCTS, REORDER_LOCATIONS, Inputs, ReorderParam, Settings, Stock, SupplyResult,
                    Transfer, location_name, num, product_name)

EPOCH = date(1899, 12, 30)
LANDED_OR_CANCELLED = ("landed", "cancelled")


def serial(d: date) -> int:
    """Google Sheets serial number of a date (days since 1899-12-30)."""
    return (d - EPOCH).days


def days_in_month(d: date) -> int:
    return calendar.monthrange(d.year, d.month)[1]


def from_serial(x: float) -> date:
    return EPOCH + timedelta(days=int(x // 1))


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


# ---------------------------------------------------------------------------------------------------------------- context

@dataclass
class Ctx:
    months: list[date]
    today: date
    start: date
    horizon: int
    settings: Settings
    demand: dict[tuple[str, str], list[float]]
    opening: dict[tuple[str, str], float]
    combos: list[tuple[str, str]]
    mtd: dict[tuple[str, str], float] = field(default_factory=dict)
    demand_found: bool = False

    def index(self, d: date | None) -> int | None:
        if d is None:
            return None
        i = (d.year - self.start.year) * 12 + d.month - self.start.month
        return i if 0 <= i < len(self.months) else None

    def dim(self, i: int) -> int:
        return days_in_month(self.months[i])


def month_list(start: date) -> list[date]:
    out, m = [], start
    while m <= AXIS_END:
        out.append(m)
        m = add_months(m, 1)
    return out


def _sorted_combos(keys) -> list[tuple[str, str]]:
    return sorted(set(keys), key=lambda k: (LOCATIONS.index(k[0]) if k[0] in LOCATIONS else 99,
                                           PRODUCTS.index(k[1]) if k[1] in PRODUCTS else 99))


def build_ctx(inputs: Inputs, demand: DemandResult | None, today: date, settings: Settings,
              pos: list[PO] | None = None, transfers: list[Transfer] | None = None) -> Ctx:
    start = month_floor(max(today, AXIS_START))
    months = month_list(start)
    units = (demand.units if demand is not None else {}) or {}
    combos = {(loc, p) for loc, ps in DEFAULT_COMBOS.items() for p in ps}
    combos |= set(inputs.stock)
    for po in (inputs.pos if pos is None else pos):
        combos.add((po.warehouse, po.product))
    for t in (inputs.transfers if transfers is None else transfers):
        combos |= {("revamp", t.product), ("amazon", t.product)}
    for (loc, p), s in units.items():
        if loc in LOCATIONS and any(v for v in s.values()):
            combos.add((loc, p))
    combos_l = _sorted_combos(combos)
    dem: dict[tuple[str, str], list[float]] = {}
    mtd: dict[tuple[str, str], float] = {}
    for key in combos_l:
        s = units.get(key) or {}
        arr = [num(s.get(m), 0.0) or 0.0 for m in months]
        m0 = num((units.get((f"{key[0]}_mtd", key[1])) or {}).get(start), 0.0) or 0.0
        if arr:
            arr[0] = max(0.0, arr[0] - m0)
        dem[key] = arr
        mtd[key] = m0
    opening = {k: inputs.stock[k].opening if k in inputs.stock else 0.0 for k in combos_l}
    return Ctx(months=months, today=today, start=start, horizon=min(settings.horizon_months, len(months)), settings=settings,
               demand=dem, opening=opening, combos=combos_l, mtd=mtd, demand_found=bool(units))


# ---------------------------------------------------------------------------------------------------------------- burn

@dataclass
class Flow:
    opening: list[float]
    demand: list[float]
    inbound: list[float]
    out: list[float]
    closing: list[float]
    cover: list[float | None]
    to_send: list[float] | None = None


def _cover(closing: list[float], demand: list[float]) -> list[float | None]:
    n = len(closing)
    out: list[float | None] = []
    for i in range(n):
        d = demand[i + 1] if i + 1 < n else demand[i]
        out.append(closing[i] / d if d else None)
    return out


def simulate(ctx: Ctx, pos: list[PO], transfers: list[Transfer]) -> dict[tuple[str, str], Flow]:
    """Burn per (location, product): opening + inbound - demand - transfers out = closing, for every month from the start month."""
    n = len(ctx.months)
    inbound = {k: [0.0] * n for k in ctx.combos}
    out = {k: [0.0] * n for k in ctx.combos}
    for po in pos:
        if po.status in LANDED_OR_CANCELLED:
            continue
        i, key = ctx.index(po.land_date), (po.warehouse, po.product)
        if i is not None and key in inbound:
            inbound[key][i] += po.units
    for t in transfers:
        if t.status in ("received", "cancelled"):
            continue
        j = ctx.index(t.receive_date)
        if j is not None and ("amazon", t.product) in inbound:
            inbound[("amazon", t.product)][j] += t.units
        i = ctx.index(t.ship_date)
        if t.status == "planned" and i is not None and ("revamp", t.product) in out:
            out[("revamp", t.product)][i] += t.units
    flows: dict[tuple[str, str], Flow] = {}
    for key in ctx.combos:
        loc, _ = key
        op, close, opens = ctx.opening.get(key, 0.0), [], []
        dem = ctx.demand[key]
        for i in range(n):
            opens.append(op)
            c = op + inbound[key][i] - dem[i] - out[key][i]
            close.append(c)
            op = c
        cover = _cover(close, dem)
        to_send = None
        if loc == "amazon":
            tgt = ctx.settings.fba_target_months
            to_send = [max(0.0, tgt * (dem[i + 1] if i + 1 < n else dem[i]) - close[i]) for i in range(n)]
        flows[key] = Flow(opening=opens, demand=list(dem), inbound=inbound[key], out=out[key], closing=close, cover=cover, to_send=to_send)
    return flows


# ---------------------------------------------------------------------------------------------------------------- reorder

def reorder_param(inputs: Inputs, settings: Settings, loc: str, prod: str) -> ReorderParam:
    p = inputs.reorder.get((loc, prod))
    if p is None:
        lead = settings.default_lead_days_revamp if loc == "revamp" else settings.default_lead_days_intl
        p = ReorderParam(location=loc, product=prod, lead_days=int(round(lead)), safety_days=int(round(settings.default_safety_days)), moq_units=0.0)
    if settings.safety_days is not None:
        p = ReorderParam(p.location, p.product, p.lead_days, int(settings.safety_days), p.moq_units)
    return p


def reorder_row(ctx: Ctx, flow: Flow, loc: str, prod: str, param: ReorderParam) -> dict:
    H = ctx.horizon
    first_out = next((i for i in range(H) if flow.closing[i] < ctx.settings.out_below_units), None)
    avg = statistics.fmean(flow.demand[:H]) if H else 0.0
    suggested = max(param.moq_units, ctx.settings.reorder_cover_months * avg)
    row: dict[str, Any] = {"location": loc, "product": prod, "lead_days": param.lead_days, "safety_days": param.safety_days,
                           "moq_units": param.moq_units, "avg_monthly_demand": avg, "suggested_po_units": suggested,
                           "stockout_month": None, "opening_that_month": None, "demand_that_month": None, "days_in_month": None,
                           "est_stockout": None, "must_land_by": None, "order_by": None, "days_until_order": None,
                           "status": "OK", "no_stockout": first_out is None, "serial": {}}
    if first_out is None:
        row["status_text"] = "OK (no stockout in window)"
        return row
    i = first_out
    m0, dim = ctx.months[i], ctx.dim(i)
    d_here = flow.demand[i]
    est = serial(m0) + (flow.opening[i] / d_here * dim if d_here else 0.0)
    must_land = est - param.safety_days
    order_by = must_land - param.lead_days
    days_until = order_by - serial(ctx.today)
    if days_until < 0:
        status = "OVERDUE"
    elif days_until < ctx.settings.order_soon_days:
        status = "ORDER SOON"
    else:
        status = "OK"
    row.update({"stockout_month": _iso(m0), "opening_that_month": flow.opening[i], "demand_that_month": d_here, "days_in_month": dim,
                "est_stockout": _iso(from_serial(est)), "must_land_by": _iso(from_serial(must_land)), "order_by": _iso(from_serial(order_by)),
                "days_until_order": days_until, "status": status, "status_text": status,
                "serial": {"stockout_month": serial(m0), "est_stockout": est, "must_land_by": must_land, "order_by": order_by,
                           "days_until_order": days_until}})
    return row


_URGENCY = {"OVERDUE": 0, "ORDER SOON": 1, "OK": 2}


def sort_reorder(rows: list[dict]) -> list[dict]:
    return sorted(rows, key=lambda r: (_URGENCY.get(r["status"], 3) if not r["no_stockout"] else 4,
                                       r["days_until_order"] if r["days_until_order"] is not None else 1e9, r["location"], r["product"]))


# ---------------------------------------------------------------------------------------------------------------- purchase orders

def vendor_lead_days(inputs: Inputs, vendor: str) -> float | None:
    v = inputs.vendors.get(vendor)
    return v.lead_weeks * 7 if v else None


def po_view(po: PO, inputs: Inputs) -> dict:
    """Computed columns of a purchase order: order date, unit cost, value, down payment, balance."""
    lead = vendor_lead_days(inputs, po.vendor)
    derived = po.order_date is None
    order_eff = po.order_date or (po.land_date - timedelta(days=lead) if lead is not None else None)
    vc = inputs.vendor_costs.get((po.vendor, po.product))
    cost = po.unit_cost if po.unit_cost is not None else (vc.unit_cost if vc else None)
    value = po.units * cost if cost is not None else None
    dep_pct = po.deposit_pct if po.deposit_pct is not None else (vc.deposit_pct if vc else None)
    down = value * dep_pct if value is not None and dep_pct is not None else (0.0 if value is not None else None)
    return {"id": po.id, "ref": po.ref or str(po.id), "overridden": list(po.overridden), "scenario_only": bool(po.ref and po.ref.startswith("new:")),
            "sheet_row": po.sheet_row, "po_number": po.po_number, "product": po.product, "vendor": po.vendor,
            "warehouse": po.warehouse, "units": po.units, "status": po.status, "order_date": _iso(order_eff),
            "order_date_derived": derived and order_eff is not None, "land_date": _iso(po.land_date), "lead_days": lead,
            "unit_cost": cost, "unit_cost_override": po.unit_cost, "po_value": value, "deposit_pct": dep_pct, "deposit_pct_override": po.deposit_pct,
            "down_payment": down,
            "balance": (value - down) if value is not None and down is not None else None,
            "deposit_paid": po.deposit_paid, "shipping_paid": po.shipping_paid, "balance_paid": po.balance_paid, "link": po.link,
            "note": po.note, "source": po.source, "_order_eff": order_eff, "flags": []}


def cash_schedule(views: list[dict], months: list[date] = MONTHS, detail: dict | None = None) -> tuple[dict, dict, dict]:
    """(deposits by order month, balances by land month, international total by month) over the axis; cancelled orders carry no cash.
    `detail` (optional dict) receives `landing_value` and `landing_deposits` by landing month."""
    idx = {m: i for i, m in enumerate(months)}
    dep, bal, intl = {m: 0.0 for m in months}, {m: 0.0 for m in months}, {m: 0.0 for m in months}
    lv, ld = {m: 0.0 for m in months}, {m: 0.0 for m in months}
    if detail is not None:
        detail["landing_value"], detail["landing_deposits"] = lv, ld
    for v in views:
        if v["status"] == "cancelled" or v["po_value"] is None:
            continue
        order = v["_order_eff"] or date.fromisoformat(v["land_date"])
        land = date.fromisoformat(v["land_date"])
        om, lm = month_floor(order), month_floor(land)
        down = v["down_payment"] or 0.0
        if om in idx:
            dep[om] += down
            if v["warehouse"] == "intl":
                intl[om] += down
        if lm in idx:
            lv[lm] += v["po_value"]
            ld[lm] += down
            bal[lm] += v["po_value"] - down
            if v["warehouse"] == "intl":
                intl[lm] += v["po_value"] - down
    return dep, bal, intl


# ---------------------------------------------------------------------------------------------------------------- warnings

def _w(code: str, level: str, title: str, detail: str, refs: list[dict] | None = None) -> dict:
    return {"code": code, "level": level, "title": title, "detail": detail, "refs": refs or []}


def _ref(kind: str, obj) -> dict:
    return {"kind": kind, "id": obj.id, "sheet_row": obj.sheet_row}


def _d(d: date) -> str:
    return f"{d:%b} {d.day}, {d.year}"


def _n(count: int, noun: str, plural: str | None = None) -> str:
    return f"{count:,} {noun if count == 1 else (plural or noun + 's')}"


_WH_LABEL = {"revamp": "Revamp Utah", "intl": "the International 3PL"}


def build_warnings(inputs: Inputs, ctx: Ctx, views: list[dict], demand_given: bool) -> list[dict]:
    """Data checks, in plain words: what is off, how many rows, what to do. `warn` = fix it; `info` = worth knowing."""
    out: list[dict] = []
    today, start = ctx.today, ctx.start
    live = [p for p in inputs.pos if p.status != "cancelled"]
    by_id = {v["id"]: v for v in views}
    ref = lambda rows: ", ".join(str(r.sheet_row or r.id) for r in rows)          # noqa: E731
    # duplicates: same product, vendor, warehouse and size landing within 21 days of each other
    groups: dict[tuple, list[PO]] = {}
    for p in live:
        groups.setdefault((p.product, p.vendor, p.warehouse, round(p.units, 2)), []).append(p)
    for (prod, vendor, wh, units), rows in groups.items():
        rows = sorted(rows, key=lambda r: r.land_date)
        cluster: list[PO] = []
        clusters: list[list[PO]] = []
        for r in rows:
            if cluster and (r.land_date - cluster[-1].land_date).days > ctx.settings.duplicate_window_days:
                clusters.append(cluster)
                cluster = []
            cluster.append(r)
        clusters.append(cluster)
        for cl in clusters:
            if len(cl) >= 2:
                out.append(_w("duplicate_po", "warn", "Possible duplicate purchase orders",
                              f"{len(cl)} orders of {int(units):,} {product_name(prod)} from {vendor} to {_WH_LABEL.get(wh, wh)} land within {ctx.settings.duplicate_window_days:g} days "
                              f"of each other (rows {ref(sorted(cl, key=lambda r: r.sheet_row or r.id))}). Each counts as supply. Cancel or delete the extra one.",
                              [_ref("po", r) for r in cl]))
    # planned but order date already passed
    late = [p for p in live if p.status == "planned" and by_id[p.id]["_order_eff"] and by_id[p.id]["_order_eff"] < today and p.land_date >= today]
    if late:
        out.append(_w("planned_order_in_past", "warn", "Planned orders with an order date in the past",
                      f"{_n(len(late), 'planned order')} (rows {ref(late)}) should already have been placed to land on schedule: the order date is before {_d(today)}. "
                      "Mark them ordered or move the land date.", [_ref("po", p) for p in late]))
    # inbound the stock count may already include
    near = [p for p in live if p.status in ("planned", "ordered", "in transit") and ctx.index(p.land_date) == 0 and p.land_date <= today]
    if near:
        out.append(_w("landed_still_inbound", "warn", "Orders past their land date still counted as inbound",
                      f"{_n(len(near), 'order')} (rows {ref(near)}) landed on or before {_d(today)} but {'is' if len(near) == 1 else 'are'} not marked landed, so "
                      f"{int(sum(p.units for p in near)):,} units count as inbound this month and may already be in the stock count. Mark them landed or move the date.",
                      [_ref("po", p) for p in near]))
    gone = [p for p in live if p.status in ("planned", "ordered", "in transit") and month_floor(p.land_date) < start]
    if gone:
        out.append(_w("past_land_not_landed", "warn", "Orders with a land date before this month, not marked landed",
                      f"{_n(len(gone), 'order')} (rows {ref(gone)}), {int(sum(p.units for p in gone)):,} units, {'is' if len(gone) == 1 else 'are'} in neither the stock count "
                      "nor the inbound. Mark them landed once counted, or set a new land date.", [_ref("po", p) for p in gone]))
    # transfers
    bad = [t for t in inputs.transfers if t.status != "cancelled" and t.receive_date < t.ship_date]
    if bad:
        out.append(_w("transfer_receive_before_ship", "warn", "Transfers that arrive before they ship",
                      f"{_n(len(bad), 'transfer')} (rows {ref(bad)}) {'has' if len(bad) == 1 else 'have'} a receive date before the ship date: the units leave Revamp "
                      "but never reach Amazon FBA stock. Fix the dates.", [_ref("transfer", t) for t in bad]))
    cancelled = [p for p in inputs.pos if p.status == "cancelled"]
    cancelled_t = [t for t in inputs.transfers if t.status == "cancelled"]
    if cancelled or cancelled_t:
        out.append(_w("cancelled_excluded", "info", "Cancelled rows carry no stock and no cash",
                      f"{_n(len(cancelled), 'cancelled order')} and {_n(len(cancelled_t), 'cancelled transfer')} add no inbound stock and no cash.",
                      [_ref("po", p) for p in cancelled] + [_ref("transfer", t) for t in cancelled_t]))
    # price list gaps
    no_cost = [p for p in live if by_id[p.id]["po_value"] is None]
    if no_cost:
        out.append(_w("missing_unit_cost", "warn", "Orders with no unit cost",
                      f"{_n(len(no_cost), 'order')} (rows {ref(no_cost)}) {'has' if len(no_cost) == 1 else 'have'} no price list entry for the vendor and product, "
                      "so no cash is counted. Add the price or type a unit cost.", [_ref("po", p) for p in no_cost]))
    no_dep = [p for p in live if by_id[p.id]["po_value"] is not None and by_id[p.id]["deposit_pct"] is None]
    if no_dep:
        out.append(_w("missing_deposit_pct", "info", "Orders with no deposit percentage",
                      f"{_n(len(no_dep), 'order')} {'has' if len(no_dep) == 1 else 'have'} no deposit percentage, so the whole value is paid when it lands.",
                      [_ref("po", p) for p in no_dep]))
    no_lead = [p for p in live if p.order_date is None and by_id[p.id]["lead_days"] is None]
    if no_lead:
        out.append(_w("missing_vendor_lead", "warn", "Orders with no vendor lead time",
                      f"{_n(len(no_lead), 'order')} (rows {ref(no_lead)}) {'has' if len(no_lead) == 1 else 'have'} no order date and the vendor has no lead time, "
                      "so the deposit is dated at landing. Add the vendor's lead time.", [_ref("po", p) for p in no_lead]))
    for prod, pc in sorted(inputs.product_costs.items()):
        stocked = any(inputs.stock[k].on_hand > 0 for k in inputs.stock if k[1] == prod)
        if pc.china_unit_cost == 0 and stocked:
            out.append(_w("zero_product_cost", "warn", f"{product_name(prod)} has no unit cost",
                          f"Stock of {product_name(prod)} is valued at $0 until a landed unit cost is entered.", []))
    # dates outside the model window
    outside = [p for p in live if not (AXIS_START <= month_floor(p.land_date) <= AXIS_END)
               or (by_id[p.id]["_order_eff"] and not (AXIS_START <= month_floor(by_id[p.id]["_order_eff"]) <= AXIS_END))]
    if outside:
        out.append(_w("outside_axis", "warn", "Orders dated outside the model window",
                      f"{_n(len(outside), 'order')} (rows {ref(outside)}) {'has' if len(outside) == 1 else 'have'} an order or land date outside Jan 2025 to Dec 2029, "
                      "so no cash is counted.", [_ref("po", p) for p in outside]))
    # stock freshness
    for loc in LOCATIONS:
        rows = [s for k, s in inputs.stock.items() if k[0] == loc]
        if not rows:
            out.append(_w("stock_missing", "warn", f"No stock count for {location_name(loc)}", "The stock table starts from zero units until a count is entered.", []))
            continue
        asof = max(s.as_of for s in rows)
        age = (today - asof).days
        est = any(s.estimate for s in rows if s.as_of == asof)
        if age > ctx.settings.stale_stock_days or est:
            out.append(_w("stock_stale", "warn" if age > ctx.settings.stale_stock_days else "info",
                          f"{location_name(loc)} stock count is {_n(age, 'day')} old" + (" and an estimate" if est else ""),
                          f"Counted {_d(asof)}" + ("; this is an estimate, not a count from the warehouse portal" if est else "") + ". Enter a fresh count below.", []))
    intl_pending = [s for k, s in inputs.stock.items() if k[0] == "intl" and s.allocated]
    if intl_pending:
        out.append(_w("intl_opening_basis", "info", "International 3PL starts from units on hand",
                      f"{int(sum(s.allocated for s in intl_pending)):,} units pending fulfillment are not subtracted from the starting stock.", []))
    if inputs.notes:
        out.append(_w("scenario_change_ignored", "warn", "Scenario changes that no longer fit",
                      f"{_n(len(inputs.notes), 'change')} in this scenario could not be applied: " + "; ".join(inputs.notes[:4]) + ("; ..." if len(inputs.notes) > 4 else "")
                      + ". Revert them or restore the rows.", []))
    if not demand_given:
        out.append(_w("demand_missing", "warn", "No demand forecast", "Inventory demand returned nothing, so every location runs at zero demand.", []))
    return out


# ---------------------------------------------------------------------------------------------------------------- compute

def _series(ctx: Ctx, arr: list[float | None]) -> dict[date, float]:
    return {m: v for m, v in zip(ctx.months, arr) if v is not None}


def compute_supply(inputs: Inputs, demand: DemandResult | None, today: date, settings: Settings | None = None) -> SupplyResult:
    settings = settings or Settings()
    ctx = build_ctx(inputs, demand, today, settings)
    flows = simulate(ctx, inputs.pos, inputs.transfers)
    views = [po_view(p, inputs) for p in inputs.pos]
    detail: dict = {}
    dep, bal, intl = cash_schedule(views, detail=detail)
    outlay = {m: dep[m] + bal[m] for m in MONTHS}
    res = SupplyResult(outlay=outlay, start=ctx.start, months=ctx.months, horizon=ctx.horizon, today=today, combos=list(ctx.combos))
    res.deposits, res.balances, res.intl_outlay = dep, bal, intl
    res.landing_value, res.landing_deposits = detail["landing_value"], detail["landing_deposits"]
    run = 0.0
    for m in MONTHS:
        run += outlay[m]
        res.outlay_cumulative[m] = run

    burn: dict[tuple, dict] = {}
    for (loc, prod), f in flows.items():
        burn[(loc, prod, "opening")] = _series(ctx, f.opening)
        burn[(loc, prod, "demand")] = _series(ctx, f.demand)
        burn[(loc, prod, "inbound")] = _series(ctx, f.inbound)
        burn[(loc, prod, "out")] = _series(ctx, f.out)
        burn[(loc, prod, "closing")] = _series(ctx, f.closing)
        burn[(loc, prod, "cover")] = _series(ctx, f.cover)
        if f.to_send is not None:
            burn[(loc, prod, "to_send")] = _series(ctx, f.to_send)
        res.status_at[(loc, prod)] = {m: ("OUT" if c < settings.out_below_units else ("LOW" if cv is not None and cv < settings.low_cover_months else ""))
                                      for m, c, cv in zip(ctx.months, f.closing, f.cover)}

    # all-locations roll-up per product
    n = len(ctx.months)
    prods = sorted({p for _, p in ctx.combos}, key=lambda p: PRODUCTS.index(p) if p in PRODUCTS else 99)
    for prod in prods:
        mine = [(k, f) for k, f in flows.items() if k[1] == prod]
        op = [sum(f.opening[i] for _, f in mine) for i in range(n)]
        dem = [sum(f.demand[i] for _, f in mine) for i in range(n)]
        inb = [sum(f.inbound[i] for k, f in mine if k[0] != "amazon") for i in range(n)]
        clo = [sum(f.closing[i] for _, f in mine) for i in range(n)]
        cov = _cover(clo, dem)
        chk = [clo[i] - (op[i] + inb[i] - dem[i]) for i in range(n)]
        for line, arr in (("opening", op), ("demand", dem), ("inbound", inb), ("closing", clo), ("cover", cov), ("check", chk)):
            burn[("total", prod, line)] = _series(ctx, arr)
        res.status_at[("total", prod)] = {m: ("OUT" if c < settings.out_below_units else ("LOW" if cv is not None and cv < settings.low_cover_months else ""))
                                          for m, c, cv in zip(ctx.months, clo, cov)}
    res.check = {m: sum(burn[("total", p, "check")].get(m, 0.0) for p in prods) for m in ctx.months}
    res.burn = burn

    # reorder
    reorder_rows = []
    for (loc, prod), f in flows.items():
        if loc not in REORDER_LOCATIONS:
            continue
        r = reorder_row(ctx, f, loc, prod, reorder_param(inputs, settings, loc, prod))
        reorder_rows.append(r)
        key = f"reorder.{loc}.{prod}"
        for fld in ("lead_days", "safety_days", "avg_monthly_demand", "suggested_po_units"):
            res.scalars[f"{key}.{fld}"] = float(r[fld])
        for fld, v in r["serial"].items():
            res.scalars[f"{key}.{fld}"] = float(v)
        for fld in ("opening_that_month", "demand_that_month", "days_in_month"):
            if r[fld] is not None:
                res.scalars[f"{key}.{fld}"] = float(r[fld])
    res.reorder = sort_reorder(reorder_rows)

    # stats (sheet: "Available today / Avg monthly demand / Months of cover today / Months stocked out")
    H = ctx.horizon
    for (loc, prod), f in flows.items():
        avg = statistics.fmean(f.demand[:H]) if H else 0.0
        avail = f.opening[0] if f.opening else 0.0
        res.stats.append({"location": loc, "product": prod, "available_today": avail, "avg_monthly_demand": avg,
                          "months_cover_today": (avail / avg) if avg else None, "months_out": sum(1 for c in f.closing[:H] if c < settings.out_below_units),
                          "as_of": _iso(inputs.stock[(loc, prod)].as_of) if (loc, prod) in inputs.stock else None})
        key = f"stats.{loc}.{prod}"
        res.scalars[f"{key}.available_today"] = avail
        res.scalars[f"{key}.avg_monthly_demand"] = avg
        res.scalars[f"{key}.months_out"] = float(sum(1 for c in f.closing[:H] if c < settings.out_below_units))
        if avg:
            res.scalars[f"{key}.months_cover_today"] = avail / avg
    for prod in prods:
        t_open = res.burn[("total", prod, "opening")].get(ctx.start, 0.0)
        t_dem = [res.burn[("total", prod, "demand")].get(m, 0.0) for m in ctx.months[:H]]
        avg = statistics.fmean(t_dem) if t_dem else 0.0
        res.scalars[f"stats.total.{prod}.available_today"] = t_open
        res.scalars[f"stats.total.{prod}.avg_monthly_demand"] = avg
        res.scalars[f"stats.total.{prod}.months_out"] = float(sum(1 for m in ctx.months[:H] if res.burn[("total", prod, "closing")].get(m, 0) < settings.out_below_units))
        if avg:
            res.scalars[f"stats.total.{prod}.months_cover_today"] = t_open / avg

    # value
    unit = {p: (inputs.product_costs[p].china_unit_cost if p in inputs.product_costs else 0.0) for p in prods}
    value: dict[date, float] = {m: 0.0 for m in ctx.months}
    for (loc, prod), f in flows.items():
        v = {m: c * unit.get(prod, 0.0) for m, c in zip(ctx.months, f.closing)}
        res.value_by_location[(loc, prod)] = v
        for m, x in v.items():
            value[m] += x
    res.value = value
    for prod in prods:
        burn[("total", prod, "value")] = {m: sum(res.value_by_location.get((l, prod), {}).get(m, 0.0) for l in LOCATIONS) for m in ctx.months}
    for loc in LOCATIONS:
        tot = 0.0
        for (l, prod), f in flows.items():
            if l == loc:
                tot += (inputs.stock[(l, prod)].on_hand if (l, prod) in inputs.stock else 0.0) * unit.get(prod, 0.0)
        res.scalars[f"value_now.{loc}"] = tot

    # purchase orders
    res.po_rows = [{k: v for k, v in pv.items() if k != "_order_eff"} for pv in views]
    maxrow = max([p.sheet_row for p in inputs.pos if p.sheet_row] or [1])
    cells: dict[str, list] = {k: [None] * max(0, maxrow - 1) for k in ("po_value", "down_payment", "balance", "order_date", "unit_cost")}
    for pv in views:
        r = pv["sheet_row"]
        if r and r >= 2:
            cells["po_value"][r - 2] = pv["po_value"]
            cells["down_payment"][r - 2] = pv["down_payment"]
            cells["balance"][r - 2] = pv["balance"]
            cells["unit_cost"][r - 2] = pv["unit_cost"]
            cells["order_date"][r - 2] = serial(pv["_order_eff"]) if pv["_order_eff"] else None
    res.po_cells = cells
    live_views = [v for v in views if v["status"] != "cancelled"]
    res.scalars["po.total_value"] = sum(v["po_value"] or 0.0 for v in live_views)
    res.scalars["po.total_deposits"] = sum(v["down_payment"] or 0.0 for v in live_views)
    res.scalars["po.total_balances"] = res.scalars["po.total_value"] - res.scalars["po.total_deposits"]
    res.scalars["outlay.total_scheduled"] = sum(outlay.values())

    # transfers
    res.transfer_rows = [{"id": t.id, "ref": t.ref or str(t.id), "overridden": list(t.overridden), "scenario_only": bool(t.ref and t.ref.startswith("new:")),
                          "sheet_row": t.sheet_row, "transfer_no": t.transfer_no, "product": t.product, "units": t.units,
                          "ship_date": _iso(t.ship_date), "receive_date": _iso(t.receive_date), "status": t.status, "note": t.note,
                          "source": t.source, "in_transit_days": (t.receive_date - t.ship_date).days} for t in inputs.transfers]

    res.warnings = build_warnings(inputs, ctx, views, demand_given=ctx.demand_found)
    res.tldr = build_tldr(res, inputs, ctx, views)
    return res


# ---------------------------------------------------------------------------------------------------------------- headline

def cash_next_days(views: list[dict], today: date, days: int = 90) -> float:
    """Cash falling due from today through today + days: deposits at the order date, balances at the land date (cancelled excluded)."""
    end = today + timedelta(days=days)
    total = 0.0
    for v in views:
        if v["status"] == "cancelled" or v["po_value"] is None:
            continue
        down = v["down_payment"] or 0.0
        order = v["_order_eff"] or date.fromisoformat(v["land_date"])
        land = date.fromisoformat(v["land_date"])
        if today <= order <= end:
            total += down
        if today <= land <= end:
            total += v["po_value"] - down
    return total


def build_tldr(res: SupplyResult, inputs: Inputs, ctx: Ctx, views: list[dict]) -> dict:
    stockouts = [r for r in res.reorder if not r["no_stockout"]]
    first = min(stockouts, key=lambda r: r["serial"]["est_stockout"]) if stockouts else None
    nxt = min(stockouts, key=lambda r: r["serial"]["order_by"]) if stockouts else None
    oldest = None
    if inputs.stock:
        by_loc: dict[str, date] = {}
        for (loc, _), s in inputs.stock.items():
            by_loc[loc] = max(by_loc.get(loc, s.as_of), s.as_of)
        loc, d = min(by_loc.items(), key=lambda kv: kv[1])
        oldest = {"location": loc, "as_of": d.isoformat(), "age_days": (ctx.today - d).days}
    amz_out = None
    for key in ctx.combos:
        if key[0] == "amazon":
            months_out = [m for m, s in res.status_at[key].items() if s == "OUT"]
            if months_out and (amz_out is None or months_out[0] < amz_out["month"]):
                amz_out = {"product": key[1], "month": months_out[0]}
    return {
        "first_stockout": ({"product": first["product"], "location": first["location"], "est_stockout": first["est_stockout"],
                            "stockout_month": first["stockout_month"], "order_by": first["order_by"], "status": first["status"]} if first else None),
        "next_order_by": ({"product": nxt["product"], "location": nxt["location"], "order_by": nxt["order_by"], "status": nxt["status"],
                           "days_until_order": nxt["days_until_order"], "est_stockout": nxt["est_stockout"]} if nxt else None),
        "cash_next_90_days": cash_next_days(views, ctx.today, int(ctx.settings.cash_window_days)),
        "cash_window_days": int(ctx.settings.cash_window_days),
        "inventory_value_now": sum(res.scalars.get(f"value_now.{loc}", 0.0) for loc in LOCATIONS),
        "warning_count": sum(1 for w in res.warnings if w["level"] == "warn"),
        "overdue_count": sum(1 for r in res.reorder if r["status"] == "OVERDUE"),
        "oldest_stock": oldest,
        "amazon_first_out": ({"product": amz_out["product"], "month": amz_out["month"].isoformat()} if amz_out else None),
        "months_out_total": sum(1 for r in res.stats if r["months_out"]),
    }
