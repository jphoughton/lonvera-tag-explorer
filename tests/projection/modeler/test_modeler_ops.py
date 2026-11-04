"""Quick-edit math and validation (pure, no database): typed cells, the draft overlay, and every instruction the page can give."""
from __future__ import annotations

from datetime import date

import pytest

from app.projection.interfaces import MONTHS
from app.projection.modeler import ops, overlay
from app.projection.modeler.ops import OpContext
from app.queries import ApiError

SPEND, AOV, CPA = "drivers.spend_total", "drivers.nc_aov", "drivers.nc_cpa"


def d(s: str) -> date:
    return date.fromisoformat(s)


def context(*, locked: tuple[str, ...] = (), por: bool = False, cpa: float = 95.0, aov: float = 69.0, spend: float = 350000.0) -> OpContext:
    cur = {"spend_total": {m: spend for m in MONTHS}, "nc_aov": {m: aov for m in MONTHS}, "nc_cpa": {m: cpa for m in MONTHS}}
    codes = {k: {m.isoformat(): ("actual" if m.isoformat() in locked else "typed") for m in MONTHS} for k in (SPEND, AOV, CPA)}
    theirs = {"spend_total": {m: 400000.0 for m in MONTHS if m < d("2028-01-01")}, "nc_aov": {m: 70.0 for m in MONTHS}, "nc_cpa": {m: 90.0 for m in MONTHS}}
    return OpContext(current=cur, codes=codes, por=theirs if por else None)


def cells(exp: ops.Expansion) -> dict[str, float | None]:
    return {m.isoformat(): v for (_k, m), v in sorted(exp.edits.items(), key=lambda kv: kv[0][1])}


# ------------------------------------------------------------------------------------------------------------------ typed cells

def test_parse_edits_reads_the_three_plan_rows_and_a_later_entry_wins():
    got = ops.parse_edits([{"line_key": SPEND, "month": "2027-01-01", "value": 1}, {"line_key": SPEND, "month": "2027-01-01", "value": 2},
                           {"line_key": CPA, "month": "2027-02-01", "value": None}])
    assert got == {(SPEND, d("2027-01-01")): 2.0, (CPA, d("2027-02-01")): None}
    assert ops.parse_edits(None) == {} and ops.parse_edits([]) == {}


@pytest.mark.parametrize("raw,fragment", [
    ("x", "must be a list"), ([1], "must be an object"), ([{"line_key": "drivers.amazon_rev", "month": "2027-01-01", "value": 1}], "line_key must be one of"),
    ([{"line_key": SPEND, "month": "2027-01-15", "value": 1}], "first of a month"), ([{"line_key": SPEND, "month": "2031-01-01", "value": 1}], "between Jan 2025 and Dec 2029"),
    ([{"line_key": SPEND, "month": "2027-01-01", "value": "5"}], "must be a number"), ([{"line_key": SPEND, "month": "2027-01-01", "value": True}], "must be a number"),
    ([{"line_key": SPEND, "month": "2027-01-01", "value": float("inf")}], "must be a number"),
])
def test_parse_edits_rejects_bad_input_with_a_plain_message(raw, fragment):
    with pytest.raises(ApiError) as e:
        ops.parse_edits(raw)
    assert e.value.status == 400 and fragment in e.value.message


def test_parse_edits_caps_the_batch():
    with pytest.raises(ApiError) as e:
        ops.parse_edits([{"line_key": SPEND, "month": "2027-01-01", "value": 1}] * (ops.MAX_EDITS + 1))
    assert "at most" in e.value.message


def test_a_zero_cost_per_customer_is_refused_but_zero_spend_is_allowed():
    top_cpa, top_spend = ops.limits("nc_cpa")[1], ops.limits("spend_total")[1]
    ops.check_values({(SPEND, d("2027-01-01")): 0.0, (CPA, d("2027-01-01")): 0.01, (AOV, d("2027-01-01")): ops.limits("nc_aov")[1], (SPEND, d("2027-02-01")): top_spend})
    for key, v, label in ((CPA, 0.0, "Cost per new customer"), (CPA, -5.0, "Cost per new customer"), (AOV, 0.0, "Average order value"), (SPEND, -1.0, "Ad spend"),
                          (SPEND, top_spend + 1, "Ad spend"), (CPA, top_cpa + 1, "Cost per new customer")):
        with pytest.raises(ApiError) as e:
            ops.check_values({(key, d("2027-01-01")): v})
        assert e.value.status == 400 and label in e.value.message and "Jan 2027" in e.value.message


def test_a_typed_cell_in_a_closed_month_is_refused_and_names_the_months():
    codes = {SPEND: {"2026-07-01": "actual", "2026-08-01": "actual", "2026-11-01": "typed"}}
    ops.check_unlocked({(SPEND, d("2026-11-01")): 1.0}, codes)
    with pytest.raises(ApiError) as e:
        ops.check_unlocked({(SPEND, d("2026-07-01")): 1.0, (SPEND, d("2026-08-01")): 2.0}, codes)
    assert "Jul 2026, Aug 2026" in e.value.message and "loaded figures" in e.value.message


# ------------------------------------------------------------------------------------------------------------------ instructions

def test_fill_sets_every_planned_month_and_says_how_many():
    exp = ops.expand({"type": "fill", "row": "spend_total", "from": "2027-01-01", "to": "2027-03-01", "value": 550000}, context())
    assert cells(exp) == {"2027-01-01": 550000.0, "2027-02-01": 550000.0, "2027-03-01": 550000.0}
    assert exp.count == 3 and exp.skipped == [] and "Set ad spend to $550,000 for Jan 2027 to Mar 2027 (3 months)." == exp.text


def test_a_range_with_closed_months_skips_them_and_reports_it():
    locked = ("2026-08-01", "2026-09-01", "2026-10-01")
    exp = ops.expand({"type": "fill", "row": "nc_cpa", "from": "2026-08-01", "to": "2026-12-01", "value": 90}, context(locked=locked))
    assert sorted(cells(exp)) == ["2026-11-01", "2026-12-01"] and [m.isoformat() for m in exp.skipped] == list(locked)
    assert "Skipped Aug 2026, Sep 2026, Oct 2026: loaded figures." in exp.text


def test_a_range_of_only_closed_months_is_an_error():
    with pytest.raises(ApiError) as e:
        ops.expand({"type": "fill", "row": "nc_cpa", "from": "2026-08-01", "to": "2026-09-01", "value": 90}, context(locked=("2026-08-01", "2026-09-01")))
    assert "Pick planned months" in e.value.message


def test_a_range_defaults_to_the_end_of_the_plan_and_must_not_run_backwards():
    exp = ops.expand({"type": "fill", "row": "nc_aov", "from": "2029-10-01", "value": 72}, context())
    assert sorted(cells(exp)) == ["2029-10-01", "2029-11-01", "2029-12-01"]
    with pytest.raises(ApiError) as e:
        ops.expand({"type": "fill", "row": "nc_aov", "from": "2027-05-01", "to": "2027-01-01", "value": 72}, context())
    assert "must not be before" in e.value.message


def test_percent_changes_each_months_own_value_and_rounds_to_cents():
    ctx = context()
    ctx.current["spend_total"][d("2027-02-01")] = 100001.0
    exp = ops.expand({"type": "percent", "row": "spend_total", "from": "2027-01-01", "to": "2027-02-01", "pct": 10}, ctx)
    assert cells(exp) == {"2027-01-01": 385000.0, "2027-02-01": 110001.1}
    cut = ops.expand({"type": "percent", "row": "nc_cpa", "from": "2027-01-01", "to": "2027-01-01", "pct": -15}, ctx)
    assert cells(cut) == {"2027-01-01": 80.75} and "-15%" in cut.text


@pytest.mark.parametrize("pct", [-100, -150, 1001, "10"])
def test_percent_outside_its_limits_is_refused(pct):
    with pytest.raises(ApiError):
        ops.expand({"type": "percent", "row": "spend_total", "from": "2027-01-01", "to": "2027-02-01", "pct": pct}, context())


def test_a_percent_cut_that_would_break_a_limit_names_the_month():
    ctx = context(cpa=0.01)
    with pytest.raises(ApiError) as e:
        ops.expand({"type": "percent", "row": "nc_cpa", "from": "2027-01-01", "to": "2027-01-01", "pct": -90}, ctx)
    assert "Cost per new customer for Jan 2027" in e.value.message


def test_percent_needs_a_value_to_start_from():
    ctx = context()
    del ctx.current["spend_total"][d("2027-02-01")]
    with pytest.raises(ApiError) as e:
        ops.expand({"type": "percent", "row": "spend_total", "from": "2027-01-01", "to": "2027-03-01", "pct": 5}, ctx)
    assert "no value in Feb 2027" in e.value.message


def test_add_raises_or_cuts_by_an_amount():
    up = ops.expand({"type": "add", "row": "spend_total", "from": "2027-01-01", "to": "2027-02-01", "amount": 50000}, context())
    assert cells(up) == {"2027-01-01": 400000.0, "2027-02-01": 400000.0} and up.text.startswith("Raise ad spend by $50,000")
    down = ops.expand({"type": "add", "row": "nc_cpa", "from": "2027-01-01", "to": "2027-01-01", "amount": -5}, context())
    assert cells(down) == {"2027-01-01": 90.0} and down.text.startswith("Cut cost per new customer by $5.00")


def test_ramp_is_a_straight_line_from_the_current_value_to_the_end_value():
    exp = ops.expand({"type": "ramp", "row": "spend_total", "from": "2027-01-01", "to": "2027-05-01", "end_value": 550000}, context())
    got = [v for _m, v in sorted(cells(exp).items())]
    assert got == [350000.0, 400000.0, 450000.0, 500000.0, 550000.0]
    steps = {round(b - a, 2) for a, b in zip(got, got[1:])}
    assert steps == {50000.0}


def test_ramp_can_start_from_a_given_value_and_one_month_ramp_is_the_start_value():
    exp = ops.expand({"type": "ramp", "row": "nc_cpa", "from": "2027-01-01", "to": "2027-03-01", "start_value": 100, "end_value": 80}, context())
    assert [v for _m, v in sorted(cells(exp).items())] == [100.0, 90.0, 80.0]
    one = ops.expand({"type": "ramp", "row": "nc_cpa", "from": "2027-01-01", "to": "2027-01-01", "start_value": 100, "end_value": 80}, context())
    assert list(cells(one).values()) == [100.0]


def test_ramp_runs_over_the_planned_months_only():
    locked = ("2026-08-01", "2026-09-01")
    exp = ops.expand({"type": "ramp", "row": "nc_aov", "from": "2026-08-01", "to": "2026-11-01", "end_value": 72}, context(locked=locked))
    assert [m.isoformat() for m in exp.skipped] == list(locked)
    assert [v for _m, v in sorted(cells(exp).items())] == [69.0, 72.0]


def test_step_and_growth_use_the_spend_plan_patterns():
    step = ops.expand({"type": "step", "row": "spend_total", "from": "2027-01-01", "to": "2027-04-01", "value": 300000, "then_value": 500000, "step_month": "2027-03-01"}, context())
    assert [v for _m, v in sorted(cells(step).items())] == [300000.0, 300000.0, 500000.0, 500000.0]
    grow = ops.expand({"type": "growth", "row": "spend_total", "from": "2027-01-01", "to": "2027-03-01", "value": 100000, "rate_pct": 10}, context())
    assert [v for _m, v in sorted(cells(grow).items())] == [100000.0, 110000.0, 121000.0]
    with pytest.raises(ApiError):
        ops.expand({"type": "growth", "row": "spend_total", "from": "2027-01-01", "to": "2027-03-01", "value": 100000, "rate_pct": 400}, context())


def test_copy_repeats_one_months_value_forward_through_the_last_month():
    ctx = context()
    ctx.current["spend_total"][d("2027-01-01")] = 480000.0
    exp = ops.expand({"type": "copy", "row": "spend_total", "month": "2027-01-01", "to": "2027-04-01"}, ctx)
    assert sorted(cells(exp)) == ["2027-02-01", "2027-03-01", "2027-04-01"] and set(cells(exp).values()) == {480000.0}
    with pytest.raises(ApiError) as e:
        ops.expand({"type": "copy", "row": "spend_total", "month": "2027-04-01", "to": "2027-04-01"}, ctx)
    assert "after the month you copy from" in e.value.message
    del ctx.current["spend_total"][d("2027-06-01")]
    with pytest.raises(ApiError) as e:
        ops.expand({"type": "copy", "row": "spend_total", "month": "2027-06-01", "to": "2027-08-01"}, ctx)
    assert "no value in Jun 2027" in e.value.message


def test_reset_goes_back_to_the_plan_of_records_cells_or_to_none():
    exp = ops.expand({"type": "reset", "row": "spend_total", "from": "2027-12-01", "to": "2028-02-01"}, context(por=True))
    assert cells(exp) == {"2027-12-01": 400000.0, "2028-01-01": None, "2028-02-01": None}
    with pytest.raises(ApiError) as e:
        ops.expand({"type": "reset", "row": "spend_total", "from": "2027-12-01", "to": "2028-02-01"}, context(por=False))
    assert "no plan of record" in e.value.message


@pytest.mark.parametrize("op", [None, "fill", {"type": "nope", "row": "spend_total"}, {"type": "fill", "row": "amazon_rev", "from": "2027-01-01", "value": 1},
                                {"type": "fill", "row": "spend_total", "from": "2027-01-01"}, {"type": "fill", "row": "spend_total", "from": "2027-01-01", "value": "x"},
                                {"type": "fill", "row": "spend_total", "from": "2027-01-15", "value": 1}])
def test_bad_instructions_are_a_400(op):
    with pytest.raises(ApiError) as e:
        ops.expand(op, context())
    assert e.value.status == 400


def test_an_instruction_that_would_break_a_limit_is_refused_before_anything_is_written():
    with pytest.raises(ApiError) as e:
        ops.expand({"type": "fill", "row": "nc_cpa", "from": "2027-01-01", "to": "2027-02-01", "value": 0}, context())
    assert "Cost per new customer" in e.value.message


# ------------------------------------------------------------------------------------------------------------------ the draft overlay (pure part)

def cell(key, month, value, origin, kind="input"):
    return {"line_key": key, "month": d(month), "value": value, "kind": kind, "note": None, "origin": origin}


def test_overlay_replaces_inserts_and_filters(monkeypatch):
    monkeypatch.setattr(overlay, "_original", lambda *a, **k: [])
    draft = overlay.Draft("mine", "base", {(SPEND, d("2027-01-01")): (999.0, "input"), (SPEND, d("2027-03-01")): (5.0, "input"), (AOV, d("2027-01-01")): (70.0, "input")})
    held = [cell(SPEND, "2027-01-01", 1.0, "mine"), cell(SPEND, "2027-02-01", 2.0, "base")]
    out = {(c["line_key"], c["month"]): c for c in overlay.apply_draft(None, draft, held, [SPEND], None, None, None)}
    assert out[(SPEND, d("2027-01-01"))]["value"] == 999.0 and out[(SPEND, d("2027-01-01"))]["origin"] == "mine"       # replaced
    assert out[(SPEND, d("2027-02-01"))]["value"] == 2.0                                                                # untouched
    assert out[(SPEND, d("2027-03-01"))]["value"] == 5.0                                                                # inserted
    assert (AOV, d("2027-01-01")) not in out                                                                              # another key than the one asked for
    windowed = overlay.apply_draft(None, draft, held, None, "drivers.", d("2027-02-01"), d("2027-02-28"))
    assert {c["month"] for c in windowed} == {d("2027-01-01"), d("2027-02-01")}                                         # outside the window: not added


def test_overlay_taking_a_cell_away_falls_back_to_the_base_or_to_nothing(monkeypatch):
    base_cell = cell(SPEND, "2027-01-01", 777.0, "base")
    monkeypatch.setattr(overlay, "_original", lambda conn, scn, keys=None, *, prefix=None, from_=None, to=None: [base_cell] if scn == "base" else [])
    gone = {(SPEND, d("2027-01-01")): (None, "input")}
    held = [cell(SPEND, "2027-01-01", 1.0, "mine")]
    assert overlay.apply_draft(None, overlay.Draft("mine", "base", gone), list(held), [SPEND], None, None, None)[0]["value"] == 777.0
    assert overlay.apply_draft(None, overlay.Draft("mine", None, gone), list(held), [SPEND], None, None, None) == []          # a frozen copy has no fallback
    inherited = [cell(SPEND, "2027-01-01", 5.0, "base")]
    assert overlay.apply_draft(None, overlay.Draft("mine", "base", gone), list(inherited), [SPEND], None, None, None)[0]["value"] == 5.0   # nothing of its own to take away
