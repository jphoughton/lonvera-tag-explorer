"""interfaces.Table / Row speak the grid's vocabulary (usd, usd0, num, pct, x, text, date; calc/input/subtotal/total/header/ratio; note/agg)
and still accept the older names as aliases."""
from __future__ import annotations

import math
from datetime import date

import pytest

from app.projection import interfaces as I


def row(**kw):
    return I.Row("k", "Label", [1.0], **kw).to_dict()


def test_grid_names_are_emitted_unchanged():
    for fmt in ("usd", "usd0", "pct", "num", "x", "text", "date"):
        assert row(fmt=fmt)["fmt"] == fmt
    for kind in ("input", "calc", "subtotal", "total", "ratio", "header"):
        assert row(kind=kind)["kind"] == kind
    assert I.KINDS == ("input", "calc", "subtotal", "total", "ratio", "header")
    assert I.FMTS == ("usd", "usd0", "pct", "num", "x", "text", "date")
    assert I.AGGS == ("sum", "avg", "last", "none")


def test_old_names_are_aliases_that_emit_the_grid_names():
    assert row(kind="line")["kind"] == "calc" and row(kind="memo")["kind"] == "ratio"
    assert (row(fmt="money")["fmt"], row(fmt="money2")["fmt"]) == ("usd0", "usd")
    d = row(fmt="int")
    assert (d["fmt"], d["digits"]) == ("num", 0)
    d = row(fmt="num2")
    assert (d["fmt"], d["digits"]) == ("num", 2)
    d = row(fmt="pct1")
    assert (d["fmt"], d["digits"]) == ("pct", 1)
    assert row(fmt="pct1", digits=2)["digits"] == 2                              # an explicit digits wins over the alias default
    assert I.normalize_kind("line") == "calc" and I.normalize_fmt("money") == ("usd0", None) and I.normalize_fmt("int") == ("num", 0)


def test_defaults_input_rows_become_input_kind_and_optional_fields_are_emitted_only_when_set():
    assert (row()["kind"], row()["fmt"], row()["input"]) == ("calc", "usd0", False)
    assert row(input=True)["kind"] == "input" and row(input=True)["input"] is True
    assert row(kind="calc", input=True)["kind"] == "calc"                         # an explicit kind is kept
    assert set(row()) == {"key", "label", "kind", "fmt", "values", "input"}
    full = row(group="g", note="Source: sheet", agg="last", digits=1, indent=2)
    assert (full["group"], full["note"], full["agg"], full["digits"], full["indent"]) == ("g", "Source: sheet", "last", 1, 2)


@pytest.mark.parametrize("kw", [{"kind": "bold"}, {"fmt": "euros"}, {"agg": "median"}, {"digits": -1}, {"digits": 9}, {"digits": True}, {"indent": -1}, {"note": 5}, {"group": 5}])
def test_unknown_values_are_rejected(kw):
    with pytest.raises(ValueError):
        row(**kw)


def test_table_add_takes_the_grid_options_and_header_rows_carry_no_values():
    t = I.Table.for_axis(date(2025, 2, 1))
    t.add("h", "Section", kind="header")
    t.add("spend", "Ad spend", {date(2025, 1, 1): 5}, input=True, missing=None, fmt="usd", note="Source: spend plan", agg="sum")
    t.add("cum", "Balance", {date(2025, 1, 1): 1.5}, kind="subtotal", fmt="money", agg="last", group="h", digits=0, indent=1)
    t.add("old", "Old names", {}, kind="line", fmt="pct1")
    p = t.to_payload()
    h, spend, cum, old = p["rows"]
    assert h["values"] == [None] * 60 and h["kind"] == "header"
    assert spend["kind"] == "input" and spend["input"] is True and spend["fmt"] == "usd" and spend["note"] == "Source: spend plan" and spend["values"][1] is None
    assert (cum["kind"], cum["fmt"], cum["agg"], cum["group"], cum["digits"], cum["indent"]) == ("subtotal", "usd0", "last", "h", 0, 1)
    assert (old["kind"], old["fmt"], old["digits"]) == ("calc", "pct", 1)
    assert p["status"][:2] == ["actual", "actual"] and p["status"][2] == "projected"


def test_fy_block_is_emitted_when_given_and_nan_still_becomes_null():
    t = I.Table.for_axis(None)
    t.add("a", "A", [math.nan] + [1.0] * 59)
    assert "fy" not in t.to_payload()
    t.fy = {"years": [2025, 2026], "rows": {"a": [1.0, math.inf]}}
    p = t.to_payload()
    assert p["rows"][0]["values"][0] is None and p["fy"] == {"years": [2025, 2026], "rows": {"a": [1.0, None]}}


def test_a_payload_built_with_the_old_vocabulary_equals_one_built_with_the_new():
    def build(kind, fmt):
        t = I.Table.for_axis(date(2026, 1, 1))
        t.add("r", "Revenue", {date(2026, 1, 1): 10.0}, kind=kind, fmt=fmt)
        return t.to_payload()

    assert build("line", "money") == build("calc", "usd0")
    assert build("memo", "money2") == build("ratio", "usd")
