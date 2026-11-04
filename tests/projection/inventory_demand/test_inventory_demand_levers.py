"""Lever parsing (tolerant) and validation of PUT /inputs bodies (strict)."""
from __future__ import annotations

from datetime import date

import pytest

from app.projection.inventory_demand import levers as lv
from app.queries import ApiError


def test_defaults_match_the_sheet():
    L = lv.parse({})
    assert L.as_of is None
    assert (L.loc("revamp").baseline_days, L.loc("amazon").baseline_days, L.loc("intl").baseline_days) == (28, 30, 30)
    assert L.loc("revamp").seasonality == (1.0,) * 12 and L.loc("revamp").monthly_growth == 0.0 and L.loc("revamp").lag_days == 0
    assert not L.loc("intl").tie_to_dtc_revenue


def test_parse_reads_the_namespace_or_the_whole_params():
    ns = {"as_of": "2026-11-03", "locations": {"intl": {"baseline_days": 14, "monthly_growth": 0.02, "tie_to_dtc_revenue": True,
                                                         "products": {"bull": {"rate": 12.5}}}}}
    for params in ({"inventory_demand": ns}, ns):
        L = lv.parse(params)
        assert L.as_of == date(2026, 11, 3)
        assert L.loc("intl").baseline_days == 14 and L.loc("intl").monthly_growth == 0.02 and L.loc("intl").tie_to_dtc_revenue
        assert L.loc("intl").rate_override("bull") == 12.5 and L.loc("intl").rate_override("liver") is None
        assert L.loc("revamp").baseline_days == 28


@pytest.mark.parametrize("bad", [None, "text", 3, [], {"locations": 5}, {"locations": {"revamp": "x"}}])
def test_parse_never_raises(bad):
    assert lv.parse({"inventory_demand": bad}).loc("revamp").baseline_days == 28


def test_round_trip_through_to_dict():
    L = lv.parse({"inventory_demand": {"locations": {"revamp": {"baseline_days": 21, "seasonality": [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1.3, 1.5]}}}})
    d = lv.to_dict(L.loc("revamp"))
    assert d["baseline_days"] == 21 and d["seasonality"][11] == 1.5 and d["products"] == {}


def test_validate_patch_builds_a_merge_patch():
    patch = lv.validate_patch({"scenario": "current", "expected_version": 3, "as_of": None,
                               "locations": {"revamp": {"baseline_days": 14, "monthly_growth": -0.02, "seasonality": [1.0] * 12,
                                                        "tie_to_dtc_revenue": True, "products": {"bull": {"rate": None, "monthly_growth": 0.01}}}}})
    assert patch["as_of"] is None
    assert patch["locations"]["revamp"]["baseline_days"] == 14
    assert patch["locations"]["revamp"]["products"]["bull"] == {"rate": None, "monthly_growth": 0.01}
    assert lv.validate_patch({"as_of": "2026-11-03"})["as_of"] == "2026-11-03"


@pytest.mark.parametrize("body,fragment", [
    ({}, "nothing to change"),
    ({"bogus": 1}, "unknown field"),
    ({"as_of": "yesterday"}, "ISO date"),
    ({"as_of": "2031-01-01"}, "between"),
    ({"locations": {}}, "non-empty"),
    ({"locations": {"mars": {}}}, "unknown location"),
    ({"locations": {"revamp": 3}}, "must be an object"),
    ({"locations": {"revamp": {"colour": 1}}}, "unknown field"),
    ({"locations": {"revamp": {"baseline_days": 0}}}, "between"),
    ({"locations": {"revamp": {"baseline_days": 28.5}}}, "whole number"),
    ({"locations": {"revamp": {"baseline_days": True}}}, "must be a number"),
    ({"locations": {"revamp": {"baseline_days": "28"}}}, "must be a number"),
    ({"locations": {"revamp": {"lag_days": -1}}}, "between"),
    ({"locations": {"revamp": {"monthly_growth": 2}}}, "between"),
    ({"locations": {"revamp": {"monthly_growth": float("nan")}}}, "must be a number"),
    ({"locations": {"revamp": {"seasonality": [1.0] * 11}}}, "12 numbers"),
    ({"locations": {"revamp": {"seasonality": [1.0] * 11 + [-1]}}}, "between"),
    ({"locations": {"revamp": {"tie_to_dtc_revenue": "yes"}}}, "true or false"),
    ({"locations": {"revamp": {"products": {"Bad Key": {}}}}}, "bad product key"),
    ({"locations": {"revamp": {"products": {"bull": {"rate": -1}}}}}, "between"),
    ({"locations": {"revamp": {"products": {"bull": {"nope": 1}}}}}, "unknown field"),
])
def test_validate_patch_rejects_bad_input_naming_the_field(body, fragment):
    with pytest.raises(ApiError) as e:
        lv.validate_patch(body)
    assert e.value.status == 400 and fragment in e.value.message
