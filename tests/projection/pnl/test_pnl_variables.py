"""Every variable the P&L exposes: a change moves the outputs, one test per class (see "Variables" in docs/projection/MODULE_pnl.md).

Classes: rate window, inflation, growth-average window, headcount formula, COGS model (price, US share, mix, unit costs), implied value (year,
multiples, offer), per-line drivers (every mode), monthly overrides, loaded QuickBooks values, freshness threshold. Plus a guard that no business
number is written into module code outside the one starting-values table.
"""
from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.projection.interfaces import MONTHS                    # noqa: E402
from app.projection.pnl import model as M                        # noqa: E402
from test_pnl_model import base_actuals, drivers_default, months_to, run, THROUGH   # noqa: E402

AUG, SEP, DEC27 = date(2026, 8, 1), date(2026, 9, 1), date(2027, 12, 1)
SKUS = [{"sku": "A", "label": "A", "china_unit_cost": 2.0, "us_unit_cost": 4.0, "sales_mix": 0.5},
        {"sku": "B", "label": "B", "china_unit_cost": 3.0, "us_unit_cost": 5.0, "sales_mix": 0.5}]
COGS_DRV = {"china_cogs": [{"from": None, "mode": "rate", "base": "total_revenue", "rate_kind": "cogs_model"}]}


def drv_with(**extra):
    d = drivers_default()
    d.update(extra)
    return d


# ------------------------------------------------------------------------------------------------------------ engine settings

def test_rate_window_months_moves_every_trailing_rate():
    a = base_actuals()
    a["amazon_fba_fees"][THROUGH] = 80_000.0
    short = run(actuals=a, params={"engine": {"rate_window_months": 1}}).lines["amazon_fba_fees"][AUG]
    long = run(actuals=a, params={"engine": {"rate_window_months": 12}}).lines["amazon_fba_fees"][AUG]
    assert short > long > 0


@pytest.mark.parametrize("group,line", [("opex_inflation", "accounting"), ("cogs_inflation", "china_cogs")])
def test_inflation_levers_move_their_lines_only(group, line):
    drv = drv_with(**COGS_DRV)
    base = run(drv=drv, cogs_sku=SKUS, settings={"cogs": {"us_share": 0.5, "price_per_bottle": 30.0}})
    infl = run(drv=drv, cogs_sku=SKUS, settings={"cogs": {"us_share": 0.5, "price_per_bottle": 30.0}}, params={"engine": {group: 0.2}})
    assert infl.lines[line][DEC27] > base.lines[line][DEC27]
    other = "accounting" if line == "china_cogs" else "china_cogs"
    assert infl.lines[other][DEC27] == pytest.approx(base.lines[other][DEC27])


def test_growth_average_window_is_a_setting():
    six = run(params={"trend": {"avg_window": 6}}).lines["dtc_rev_mom_6m"]
    three = run(params={"trend": {"avg_window": 3}}).lines["dtc_rev_mom_6m"]
    assert MONTHS[3] in three and MONTHS[3] not in six                       # a 3-month average starts earlier
    m = MONTHS[10]
    mom = run().lines["dtc_rev_mom"]
    assert three[m] == pytest.approx(sum(mom[x] for x in MONTHS[8:11]) / 3)


@pytest.mark.parametrize("key,value", [("base", 40_000.0), ("increment", 50_000.0)])
def test_headcount_base_and_increment(key, value):
    base = run(up={"nc": 2_000_000.0, "repeat": 1_000_000.0}, settings={"headcount": {"start": "2026-09-01"}}).lines["headcount"][DEC27]
    moved = run(up={"nc": 2_000_000.0, "repeat": 1_000_000.0}, settings={"headcount": {"start": "2026-09-01"}}, params={"headcount": {key: value}}).lines["headcount"][DEC27]
    assert moved > base


@pytest.mark.parametrize("key,value", [("threshold", 40_000_000.0), ("step", 1_000_000.0)])
def test_headcount_threshold_and_step(key, value):
    up = {"nc": 2_000_000.0, "repeat": 1_000_000.0}
    base = run(up=up).lines["headcount"][DEC27]
    moved = run(up=up, params={"headcount": {key: value}}).lines["headcount"][DEC27]
    assert moved != base


def test_headcount_start_month_moves_when_it_begins():
    early = run(params={"headcount": {"start": "2026-08-01"}}).lines["headcount"][AUG]
    late = run(params={"headcount": {"start": "2026-10-01"}}).lines["headcount"][AUG]
    assert early > 0 == late


# ------------------------------------------------------------------------------------------------------------ COGS model

@pytest.mark.parametrize("patch", [{"price_per_bottle": 15.0}, {"us_share": 0.9}, {"mix": {"A": 1.0, "B": 0.0}}, {"china_cost": {"A": 9.0}}, {"us_cost": {"B": 9.0}}])
def test_cogs_model_variables_move_the_rate_and_the_line(patch):
    s = {"cogs": {"us_share": 0.5, "price_per_bottle": 30.0}}
    base = run(drv=drv_with(**COGS_DRV), cogs_sku=SKUS, settings=s)
    moved = run(drv=drv_with(**COGS_DRV), cogs_sku=SKUS, settings=s, params={"cogs": patch})
    assert moved.cogs_model["rate"] != pytest.approx(base.cogs_model["rate"])
    assert moved.lines["china_cogs"][SEP] != pytest.approx(base.lines["china_cogs"][SEP])


def test_current_price_only_changes_the_comparison_not_the_projection():
    s = {"cogs": {"us_share": 0.5, "price_per_bottle": 30.0, "current_price_per_bottle": 25.0}}
    base = run(drv=drv_with(**COGS_DRV), cogs_sku=SKUS, settings=s)
    moved = run(drv=drv_with(**COGS_DRV), cogs_sku=SKUS, settings=s, params={"cogs": {"current_price_per_bottle": 20.0}})
    assert moved.cogs_model["rate_at_current_price"] > base.cogs_model["rate_at_current_price"]
    assert moved.lines["china_cogs"][SEP] == pytest.approx(base.lines["china_cogs"][SEP])


# ------------------------------------------------------------------------------------------------------------ implied value

def test_ev_year_multiples_and_offer():
    base = run()
    other_year = run(params={"ev": {"year": 2028}})
    assert other_year.ev["basis_year"] == 2028 and other_year.ev["basis_noi"] == pytest.approx(other_year.fy[2028]["noi"])
    assert other_year.ev["3x"] == pytest.approx(other_year.fy[2028]["noi"] * 3) and base.ev["basis_year"] == 2027
    mult = run(params={"ev": {"multiples": [2, 10]}})
    assert [k for k in mult.ev if k.endswith("x")] == ["2x", "10x"] and mult.ev["10x"] == pytest.approx(base.fy[2027]["noi"] * 10)
    assert run(params={"ev": {"loi": 1.0}}).ev["loi"] == 1.0


# ------------------------------------------------------------------------------------------------------------ drivers, overrides, loaded values

@pytest.mark.parametrize("segment,expect", [
    ({"from": None, "mode": "fixed", "value": 777.0}, lambda out: out.lines["accounting"][SEP] == 777.0),
    ({"from": None, "mode": "carry"}, lambda out: out.lines["accounting"][SEP] == 2_000.0),
    ({"from": None, "mode": "avg", "window": 2}, lambda out: out.lines["accounting"][SEP] == pytest.approx(2_000.0)),
    ({"from": None, "mode": "rate", "base": "dtc_rev", "rate_kind": "typed", "rate": 0.01}, lambda out: out.lines["accounting"][SEP] == pytest.approx(0.01 * out.lines["dtc_rev"][SEP])),
    ({"from": None, "mode": "rate", "base": "dtc_rev", "rate_kind": "window"}, lambda out: out.lines["accounting"][SEP] == pytest.approx(2_000.0 / 800_000.0 * out.lines["dtc_rev"][SEP])),
    ({"from": None, "mode": "rate", "base": "dtc_rev", "rate_kind": "ratio_avg", "window": 2}, lambda out: out.lines["accounting"][SEP] == pytest.approx(2_000.0 / 800_000.0 * out.lines["dtc_rev"][SEP])),
])
def test_every_driver_mode_is_editable_per_scenario(segment, expect):
    out = run(params={"drivers": {"accounting": {"segments": [segment]}}})
    assert expect(out)
    assert out.lines["accounting"][SEP] != run().lines["accounting"][SEP] or segment["mode"] in ("carry", "avg")


def test_a_step_changes_a_line_from_its_month_only():
    out = run(params={"drivers": {"accounting": {"segments": [{"from": None, "mode": "fixed", "value": 1.0}, {"from": "2027-01-01", "mode": "fixed", "value": 2.0}]}}})
    assert out.lines["accounting"][date(2026, 12, 1)] == 1.0 and out.lines["accounting"][date(2027, 1, 1)] == 2.0


def test_monthly_override_and_loaded_actual_both_move_the_outputs():
    base = run().lines["noi"][SEP]
    assert run(overrides={"accounting": {SEP: 50_000.0}}).lines["noi"][SEP] < base
    a = base_actuals()
    a["accounting"] = {m: 50_000.0 for m in months_to(THROUGH, 12)}                      # a different loaded history: the actual months AND the fixed-line trail move
    changed = run(actuals=a)
    assert changed.lines["noi"][date(2026, 3, 1)] < run().lines["noi"][date(2026, 3, 1)]


# ------------------------------------------------------------------------------------------------------------ the guard

NUMBERS = [r"\b0\.07\b", r"\b29\.22\b", r"\b21\.62\b", r"\b2027\b", r"7_?500_?000", r"20_?000_?000", r"\b15_?000\b", r"5_?000_?000"]
SOURCES = ["model.py", "views.py", "store.py", "api.py", "qb.py", "xlsx_read.py", "__init__.py"]


def _code_without_defaults(text: str) -> str:
    start = text.index("DEFAULT_SETTINGS: dict")
    end = text.index("\n}\n", start) + 3
    return text[:start] + text[end:]


def test_no_business_number_is_written_into_module_code_outside_the_starting_values():
    base = ROOT / "app" / "projection" / "pnl"
    for name in SOURCES:
        text = (base / name).read_text()
        if name == "model.py":
            text = _code_without_defaults(text)
        text = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith(("#", '"""')) and '"""' not in l)
        for pat in NUMBERS:
            assert not re.search(pat, text), f"{name}: business number {pat} written in code"
    js = (ROOT / "app" / "static" / "js" / "projection" / "pnl.js").read_text()
    for pat in (r"\b29\.22\b", r"\b21\.62\b", r"\b2027\b", r"\b0\.07\b", r"7_?500_?000"):
        assert not re.search(pat, "\n".join(l for l in js.splitlines() if not l.lstrip().startswith("//"))), pat
