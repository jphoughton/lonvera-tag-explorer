"""Lever validation and merging."""
from __future__ import annotations

import pytest

from app.projection.financing import params as P
from app.queries import ApiError


def test_effective_overlays_the_scenario_on_the_defaults():
    e = P.effective({"financing": {"debt": {"rate": 0.1}}, "other": {"x": 1}})
    assert e["debt"]["rate"] == 0.1 and e["debt"]["principal"] == 3_000_000 and "other" not in e
    assert P.effective(None) == P.DEFAULTS and P.effective({"financing": "junk"}) == P.DEFAULTS
    e["debt"]["rate"] = 9
    assert P.DEFAULTS["debt"]["rate"] == 0.15                                  # never mutates the defaults


def test_lists_replace_rather_than_merge():
    e = P.effective({"financing": {"tax": {"payments": [{"month": "2027-09-01", "from": "2027-01-01", "to": "2027-06-01"}]}}})
    assert len(e["tax"]["payments"]) == 1


@pytest.mark.parametrize("patch", [
    {"debt": {"principal": 1_000_000, "rate": 0.12, "mode": "fixed_payment", "fixed_payment": 90_000}},
    {"seller_note": {"enabled": True, "installments": 5, "ebitda_floor": 1_000_000}},
    {"seller_note": {"ebitda_floor": None}},
    {"tax": {"rate": 0.25, "deduct_interest": True, "payments": []}},
    {"close_month": "2026-10-01"},
    {"payback": {"aov": 70, "cac": 0, "curve": "goal"}},
    {"matrix": {"spends": [1, 2, 3], "roas": [0.5], "basis": "loaded"}},
    {"payout": {"repeat_source": "typed", "repeat_typed": [0.2, 0.1], "columns": [{"payback": 3, "first_scale": 1, "first_bonus": 2, "step_up": 0.1, "step_above": 0.1}]}},
    {"debt": None},
])
def test_valid_patches_pass(patch):
    assert P.validate_patch(patch) is patch


@pytest.mark.parametrize("patch,needle", [
    ({"debt": {"principal": "1"}}, "debt.principal"),
    ({"debt": {"principal": True}}, "debt.principal"),
    ({"debt": {"rate": 1.2}}, "debt.rate"),
    ({"debt": {"rate": float("nan")}}, "debt.rate"),
    ({"seller_note": {"installments": 2.5}}, "seller_note.installments"),
    ({"seller_note": {"first_payment": "2027-09-02"}}, "seller_note.first_payment"),
    ({"tax": {"payments": [{"month": "2027-09-01", "from": "2027-01-01", "to": "2027-12-01", "x": 1}]}}, "tax.payments[0]"),
    ({"tax": {"payments": [{"month": "2027-09-01", "from": "2027-01-01"}]}}, "to is required"),
    ({"matrix": {"spends": list(range(1, 60))}}, "matrix.spends"),
    ({"payout": {"scales": [3, 2]}}, "ascending"),
    ({"payout": {"columns": []}}, "payout.columns"),
    ({"payout": {"columns": [{"payback": 3, "first_scale": 1, "first_bonus": 2, "step_up": 0.1, "step_above": 0.1}] * 2}}, "distinct"),
    ({"nope": 1}, "nope"),
    ({"debt": {"nope": 1}}, "debt.nope"),
])
def test_invalid_patches_name_the_problem(patch, needle):
    with pytest.raises(ApiError) as e:
        P.validate_patch(patch)
    assert e.value.status == 400 and needle in e.value.message


def test_non_object_patch():
    with pytest.raises(ApiError):
        P.validate_patch([1])
    with pytest.raises(ApiError):
        P.validate_patch({"debt": 1})


def test_readers_are_tolerant():
    assert P.num("x", 3) == 3 and P.num(None) == 0.0 and P.num(float("inf"), 7) == 7 and P.num(True, 2) == 2 and P.num(5) == 5.0
    assert P.opt_num("x") is None and P.opt_num(2) == 2.0
    assert P.to_month("2026-09-01").month == 9 and P.to_month("2026-09-02") is None and P.to_month(5, "d") == "d"
