"""Spend-plan patterns (pure)."""
from __future__ import annotations

from datetime import date

import pytest

from app.projection.drivers import patterns
from app.projection.drivers.model import month_range


def M(y, m):
    return date(y, m, 1)


RANGE = month_range(M(2026, 12), M(2027, 5))          # six months


def test_flat():
    assert set(patterns.generate({"type": "flat", "value": 350000}, RANGE).values()) == {350000.0}


def test_step_switches_at_the_step_month():
    out = patterns.generate({"type": "step", "value": 300000, "then_value": 500000, "step_month": "2027-03-01"}, RANGE)
    assert out[M(2027, 2)] == 300000 and out[M(2027, 3)] == 500000 and out[M(2027, 5)] == 500000


def test_growth_compounds_and_caps():
    out = patterns.generate({"type": "growth", "value": 100000, "rate": 0.10}, RANGE)
    assert out[M(2026, 12)] == 100000 and out[M(2027, 1)] == 110000 and out[M(2027, 5)] == pytest.approx(161051, abs=1)
    capped = patterns.generate({"type": "growth", "value": 100000, "rate": 0.10, "cap": 120000}, RANGE)
    assert max(capped.values()) == 120000
    falling = patterns.generate({"type": "growth", "value": 100000, "rate": -0.10, "cap": 70000}, RANGE)
    assert min(falling.values()) == 70000                       # for a falling plan the cap is a floor


def test_ramp_is_a_straight_line_end_to_end():
    out = patterns.generate({"type": "ramp", "value": 100000, "end_value": 600000}, RANGE)
    vals = [out[m] for m in RANGE]
    assert vals[0] == 100000 and vals[-1] == 600000
    steps = {round(b - a, 2) for a, b in zip(vals, vals[1:])}
    assert steps == {100000.0}
    assert patterns.generate({"type": "ramp", "value": 5, "end_value": 9}, RANGE[:1]) == {RANGE[0]: 5.0}


@pytest.mark.parametrize("spec,msg", [
    (None, "object"), ({"type": "wiggle", "value": 1}, "type"), ({"type": "flat"}, "value is required"), ({"type": "flat", "value": "5"}, "number"),
    ({"type": "flat", "value": float("inf")}, "number"), ({"type": "flat", "value": True}, "number"),
    ({"type": "growth", "value": 1, "rate": 3}, "rate"), ({"type": "step", "value": 1, "then_value": 2, "step_month": "2027-03-15"}, "step_month"),
    ({"type": "ramp", "value": 1}, "end_value"),
])
def test_bad_specs_name_the_problem(spec, msg):
    with pytest.raises(patterns.PatternError, match=msg):
        patterns.generate(spec, RANGE)


def test_empty_and_oversize_ranges():
    with pytest.raises(patterns.PatternError, match="no months"):
        patterns.generate({"type": "flat", "value": 1}, [])
    with pytest.raises(patterns.PatternError, match="at most"):
        patterns.generate({"type": "flat", "value": 1}, RANGE * 11)


def test_bounds_by_target():
    patterns.check_bounds("spend_total", {M(2027, 1): 0.0})
    with pytest.raises(patterns.PatternError, match="Marketing spend"):
        patterns.check_bounds("spend_total", {M(2027, 1): -1.0})
    with pytest.raises(patterns.PatternError, match="CPA"):
        patterns.check_bounds("nc_cpa", {M(2027, 1): 0.0})                  # a CPA of zero would divide
    with pytest.raises(patterns.PatternError, match="target"):
        patterns.check_bounds("rent", {})


def test_describe_is_short_and_dated():
    assert patterns.describe("spend_total", {"type": "flat", "value": 350000}, M(2026, 12), M(2027, 5)) == "flat 350,000.00, Dec 2026 - May 2027"
    assert "growth 3.0% a month" in patterns.describe("spend_total", {"type": "growth", "value": 1, "rate": 0.03}, M(2026, 12), M(2027, 5))
