"""Plan proposal: purchase orders and FBA transfers that keep stock above the safety line. It only PROPOSES rows; `store.accept_plan` writes them.

Rules (the 2026-10-13 rebalance of the sheet, generalised from a flat daily rate to the demand series):
  1. FBA transfers: for each product and month, when FBA closing stock is below `fba_target_months` of demand, ship from Revamp `transfer_lag_months`
     earlier and receive in this month, sized to restore `fba_restore_months` of cover, rounded up to `transfer_round_units` (at least `transfer_min_units`).
  2. Revamp buys (the vendor priced for Revamp) and 3. international 3PL buys (the vendor priced for it): at the first month whose closing stock falls
     below the safety line (safety days of that month's demand), land a purchase order in that month, or on today + vendor lead time when that is later,
     sized to the shortfall plus `plan_cover_months` of forward demand, rounded up to `po_round_units` and not below the vendor minimum order.
All of these are variables (model.SETTING_SPECS, scenario params `inventory_supply`) with the sheet / rebalance values as defaults.
A month that cannot be fixed in time (earlier than any order could land) is reported as a shortfall instead of being bought again.
"""
from __future__ import annotations

import math
from dataclasses import replace
from datetime import date, timedelta
from typing import Any

from ..interfaces import DemandResult
from .calc import Ctx, build_ctx, cash_schedule, compute_supply, po_view, simulate
from .model import PO, Inputs, Settings, Transfer, product_name

MAX_ROUNDS = 60                          # safety stop for the search loop, not a business input


def ceil_to(x: float, step: float) -> int:
    step = max(float(step), 1.0)
    return int(math.ceil(max(x, 0.0) / step) * step)


class _Rates:
    def __init__(self, ctx: Ctx):
        self.ctx = ctx

    def rate_day(self, key, i: int) -> float:
        d = self.ctx.demand[key]
        n = len(d)
        j = 1 if i == 0 and n > 1 else i
        return d[j] / self.ctx.dim(j) if n else 0.0

    def monthly(self, key, j: int) -> float:
        j = min(j, len(self.ctx.months) - 1)
        return self.rate_day(key, j) * self.ctx.dim(j)

    def forward(self, key, k: int, months: float) -> float:
        left, tot, m = months, 0.0, 0
        while left > 1e-9:
            tot += min(1.0, left) * self.monthly(key, k + m)
            left -= 1.0
            m += 1
        return tot


def _safety(inputs: Inputs, settings: Settings, key: tuple[str, str]) -> float:
    if settings.plan_safety_days is not None:
        return settings.plan_safety_days
    if settings.safety_days is not None:
        return settings.safety_days
    p = inputs.reorder.get(key)
    return float(p.safety_days) if p else float(settings.default_safety_days)


def pick_vendor(inputs: Inputs, loc: str, prod: str) -> str | None:
    cands = [(vc.unit_cost, v) for (v, p), vc in inputs.vendor_costs.items()
             if p == prod and v in inputs.vendors and inputs.vendors[v].default_warehouse == loc]
    return min(cands)[1] if cands else None


def _fmt_note(today: date, months: float) -> str:
    return f"Auto-plan {today.isoformat()}: {months:g}-month cover at current run rate"


def suggest(inputs: Inputs, demand: DemandResult | None, today: date, settings: Settings | None = None, *, replace_auto: bool = True) -> dict:
    settings = settings or Settings()
    base_pos = [p for p in inputs.pos if not (replace_auto and p.source == "auto-plan")]
    base_tr = [t for t in inputs.transfers if not (replace_auto and t.source == "auto-plan")]
    removed_pos = [p for p in inputs.pos if replace_auto and p.source == "auto-plan"]
    removed_tr = [t for t in inputs.transfers if replace_auto and t.source == "auto-plan"]
    ctx = build_ctx(inputs, demand, today, settings, pos=base_pos, transfers=base_tr)
    rates = _Rates(ctx)
    H = ctx.horizon
    new_pos: list[PO] = []
    new_tr: list[Transfer] = []
    shortfalls: list[dict] = []
    tmp = [0]

    def next_id() -> int:
        tmp[0] -= 1
        return tmp[0]

    def run():
        return simulate(ctx, base_pos + new_pos, base_tr + new_tr)

    # 1) FBA transfers
    lag = int(settings.transfer_lag_months)
    for prod in sorted({p for loc, p in ctx.combos if loc == "amazon"}):
        key = ("amazon", prod)
        if not any(ctx.demand[key][:H]):
            continue
        for i in range(H):
            close = run()[key].closing[i]
            monthly = rates.rate_day(key, i) * settings.days_per_month
            if close >= settings.fba_target_months * monthly - 1e-6:
                continue
            if i < max(lag, 1):
                shortfalls.append({"location": "amazon", "product": prod, "month": ctx.months[i].isoformat(), "earliest_land": None,
                                   "reason": "FBA stock is below target this month; a transfer cannot arrive before "
                                             f"{ctx.months[max(lag, 1)]:%b %Y}."})
                continue
            size = max(settings.transfer_min_units, ceil_to(settings.fba_restore_months * monthly - close, settings.transfer_round_units))
            ship = max(ctx.months[i - lag], today)
            new_tr.append(Transfer(id=next_id(), product=prod, units=float(size), ship_date=ship, receive_date=ctx.months[i], status="planned",
                                   note=f"Auto-plan {today.isoformat()}: {settings.fba_target_months:g}-month FBA cover at current run rate", source="auto-plan"))

    # 2) Revamp buys, 3) international 3PL buys
    for loc in ("revamp", "intl"):
        for prod in sorted({p for l, p in ctx.combos if l == loc}):
            key = (loc, prod)
            if not any(ctx.demand[key][:H]):
                continue
            vendor = pick_vendor(inputs, loc, prod)
            if vendor is None:
                shortfalls.append({"location": loc, "product": prod, "month": None, "earliest_land": None,
                                   "reason": f"No vendor price list entry for {product_name(prod)} at this location; nothing to propose."})
                continue
            v = inputs.vendors[vendor]
            vc = inputs.vendor_costs[(vendor, prod)]
            lead = int(round(v.lead_weeks * 7))
            floor_i, guard = 0, 0
            while guard < MAX_ROUNDS:
                guard += 1
                f = run()[key]
                sd = _safety(inputs, settings, key)
                bad = next((i for i in range(floor_i, H) if f.closing[i] < sd * rates.rate_day(key, i) - 1e-6), None)
                if bad is None:
                    break
                earliest = today + timedelta(days=lead)
                land = max(ctx.months[bad], earliest)
                k = ctx.index(land)
                if k is None or k >= H:
                    shortfalls.append({"location": loc, "product": prod, "month": ctx.months[bad].isoformat(), "earliest_land": land.isoformat(),
                                       "reason": "The earliest possible landing is past the planning window."})
                    break
                safety_k = sd * rates.rate_day(key, k)
                short = safety_k - f.closing[k]
                units = max(v.moq_units, ceil_to(short + rates.forward(key, k, settings.plan_cover_months), settings.po_round_units))
                late = k > bad
                new_pos.append(PO(id=next_id(), product=prod, vendor=vendor, warehouse=loc, units=float(units), land_date=land, status="planned",
                                  deposit_pct=vc.deposit_pct, note=_fmt_note(today, settings.plan_cover_months), source="auto-plan"))
                if late:
                    shortfalls.append({"location": loc, "product": prod, "month": ctx.months[bad].isoformat(), "earliest_land": land.isoformat(),
                                       "reason": f"Stock is below the safety line from {ctx.months[bad]:%b %Y}, but the earliest an order can land is "
                                                 f"{land:%b} {land.day}, {land.year} (today plus {lead} days)."})
                    floor_i = k
    return _package(inputs, demand, today, settings, base_pos, base_tr, new_pos, new_tr, removed_pos, removed_tr, shortfalls, replace_auto)


def _package(inputs, demand, today, settings, base_pos, base_tr, new_pos, new_tr, removed_pos, removed_tr, shortfalls, replace_auto) -> dict:
    before = compute_supply(inputs, demand, today, settings)
    after_inputs = replace(inputs, pos=base_pos + new_pos, transfers=base_tr + new_tr)
    after = compute_supply(after_inputs, demand, today, settings)
    views_new = [po_view(p, after_inputs) for p in new_pos]
    views_old = [po_view(p, inputs) for p in removed_pos]

    def out_months(res) -> dict[str, list[str]]:
        o: dict[str, list[str]] = {}
        for (loc, prod), st in res.status_at.items():
            if loc == "total":
                continue
            ms = [m.isoformat() for m, s in st.items() if s == "OUT" and m in res.months[:res.horizon]]
            if ms:
                o[f"{loc}|{prod}"] = ms
        return o

    delta = {m.isoformat(): after.outlay.get(m, 0.0) - before.outlay.get(m, 0.0) for m in sorted(before.outlay)
             if abs(after.outlay.get(m, 0.0) - before.outlay.get(m, 0.0)) > 0.005}
    pos_out = []
    for p, v in zip(new_pos, views_new):
        pos_out.append({"temp_id": p.id, "product": p.product, "vendor": p.vendor, "warehouse": p.warehouse, "units": p.units,
                        "land_date": p.land_date.isoformat(), "order_date": v["order_date"], "status": "planned", "deposit_pct": v["deposit_pct"],
                        "unit_cost": v["unit_cost"], "po_value": v["po_value"], "down_payment": v["down_payment"], "note": p.note, "source": "auto-plan"})
    tr_out = [{"temp_id": t.id, "product": t.product, "units": t.units, "ship_date": t.ship_date.isoformat(), "receive_date": t.receive_date.isoformat(),
               "status": "planned", "note": t.note, "source": "auto-plan"} for t in new_tr]
    return {
        "replace_auto": replace_auto,
        "today": today.isoformat(),
        "pos": pos_out, "transfers": tr_out, "shortfalls": shortfalls,
        "removed": {"pos": [{"id": p.id, "sheet_row": p.sheet_row, "product": p.product, "units": p.units, "land_date": p.land_date.isoformat(),
                             "po_value": v["po_value"]} for p, v in zip(removed_pos, views_old)],
                    "transfers": [{"id": t.id, "sheet_row": t.sheet_row, "product": t.product, "units": t.units, "ship_date": t.ship_date.isoformat(),
                                   "receive_date": t.receive_date.isoformat()} for t in removed_tr]},
        "summary": {"added_pos": len(pos_out), "added_units": sum(p["units"] for p in pos_out),
                    "added_value": sum(p["po_value"] or 0.0 for p in pos_out), "added_transfers": len(tr_out),
                    "removed_pos": len(removed_pos), "removed_transfers": len(removed_tr),
                    "removed_value": sum(v["po_value"] or 0.0 for v in views_old),
                    "outlay_total_before": sum(before.outlay.values()), "outlay_total_after": sum(after.outlay.values()),
                    "stockouts_before": out_months(before), "stockouts_after": out_months(after), "cash_delta_by_month": delta},
    }
