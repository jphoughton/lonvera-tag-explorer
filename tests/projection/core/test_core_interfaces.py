from __future__ import annotations

import math
from datetime import date

import pytest

from app.projection import interfaces as I


def test_month_axis():
    assert len(I.MONTHS) == 60 and I.MONTHS[0] == date(2025, 1, 1) and I.MONTHS[-1] == date(2029, 12, 1)
    assert all(m.day == 1 for m in I.MONTHS) and list(I.MONTHS) == sorted(set(I.MONTHS))
    assert I.month_index(date(2025, 1, 1)) == 0 and I.month_index(date(2026, 11, 1)) == 22 and I.month_index(date(2029, 12, 1)) == 59


@pytest.mark.parametrize("bad", [date(2026, 11, 2), date(2024, 12, 1), date(2030, 1, 1), "2026-11-01", None])
def test_month_index_rejects(bad):
    with pytest.raises(ValueError):
        I.month_index(bad)


def test_parse_month_and_helpers():
    assert I.parse_month("2026-11-01") == date(2026, 11, 1)
    for bad in ("2026-11-15", "2026-11", "nope", 20261101, "2031-01-01"):
        with pytest.raises(ValueError):
            I.parse_month(bad)
    assert I.add_months(date(2026, 11, 17), 3) == date(2027, 2, 1) and I.add_months(date(2026, 1, 1), -2) == date(2025, 11, 1)
    assert I.month_floor(date(2026, 3, 19)) == date(2026, 3, 1)


def test_is_actual_and_status():
    at = date(2026, 6, 1)
    assert I.is_actual(date(2026, 6, 1), at) and not I.is_actual(date(2026, 7, 1), at) and not I.is_actual(date(2026, 6, 1), None)
    st = I.status_for(at)
    assert len(st) == 60 and st[:18] == ["actual"] * 18 and st[18] == "projected"


def test_table_payload_shape_and_nan_to_null():
    t = I.Table.for_axis(date(2025, 3, 1))
    t.add("rev", "Revenue", {date(2025, 1, 1): 10.0, date(2025, 2, 1): math.nan}, kind="total", fmt="money")
    t.add("spend", "Spend", {date(2025, 1, 1): 5}, input=True, missing=None, fmt="money")
    t.add("hdr", "Section", kind="header")
    t.captions.append("Source: test")
    p = t.to_payload()
    assert set(p) == {"months", "status", "rows", "captions"}
    assert p["months"][0] == "2025-01-01" and len(p["months"]) == 60 and p["status"][:3] == ["actual"] * 3 and p["status"][3] == "projected"
    r0, r1, r2 = p["rows"]
    assert set(r0) == {"key", "label", "kind", "fmt", "values", "input"}
    assert r0["values"][0] == 10.0 and r0["values"][1] is None and r0["values"][2] == 0.0 and r0["input"] is False
    assert r1["input"] is True and r1["values"][1] is None
    assert r2["values"] == [None] * 60 and r2["kind"] == "header"
    assert p["captions"] == ["Source: test"]


def test_row_validation_and_length_check():
    with pytest.raises(ValueError):
        I.Row("k", "l", [], kind="bold")
    with pytest.raises(ValueError):
        I.Row("k", "l", [], fmt="euros")
    with pytest.raises(ValueError):
        I.Table().add("k", "l", [1.0, 2.0])


def test_dataclasses_default_empty_and_module_keywords():
    d = I.DriverResult()
    assert d.nc_rev == {} and d.actual_through is None
    assert I.DemandResult().get("revamp", "x") == {}
    m = I.Module(key="x")
    assert m.compute is None and m.blueprint is None and m.ui is None
    s = I.Scenario(1, "current", "Current plan", {})
    assert s.base_slug is None and s.version == 1
    from app.projection import engine
    assert set(engine.ORDER) <= set(I.MODULE_CATALOG)                                          # every engine step is a catalog module (the catalog also lists page-only modules)
    assert all(isinstance(v, tuple) and len(v) == 2 for v in I.MODULE_CATALOG.values())
