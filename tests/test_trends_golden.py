"""Trends: the layout rebuild changed what is shown, not what is computed.

tests/fixtures/trends_golden_before_layout.json holds every value ('v') and change ('d') of the Trends payload for the mock's made-up days, taken from the code as it was
BEFORE the sheet-layout rebuild (the numbers that had been reconciled to the cent against production), for Day / Week / Month x own / nc numbers, 6 periods each.
These tests run the payload builder again and require every one of those cells to be identical (new metrics may be added, none may change).
ONE intentional exception (2026-11-09): nc_cvr was redefined to Upstack new customers / Upstack new visitors. The fixture's nc_cvr cells are the OLD click-based definition, which now lives
on as nc_cvr_clicks: they are compared against that key, and the new nc_cvr gets its own oracle test below. Every other fixture value is untouched."""
import datetime as dt
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = json.loads((ROOT / "tests" / "fixtures" / "trends_golden_before_layout.json").read_text())


def _mock():
    spec = importlib.util.spec_from_file_location("mock_trends_golden", ROOT / "tests" / "ui" / "mock_trends.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


M = _mock()


def payload(interval, numbers, periods=6):
    T = M.T
    opt = {"interval": interval, "periods": periods, "numbers": numbers, "as_of": None}
    cov = M._cov()
    through = min(M.LAST, M.TODAY - dt.timedelta(days=1))
    for c in cov.values():
        c.last = min(c.last, through)
    return T.assemble(opt, M.TODAY, cov, through, T.build_periods(interval, M.TODAY, through, periods), M.FR, M.REACH[interval])


@pytest.mark.parametrize("key", sorted(GOLDEN))
def test_every_value_and_change_is_identical_to_before_the_layout_rebuild(key):
    interval, numbers = key.split(":")
    got = {r["key"]: r for r in payload(interval, numbers)["rows"]}
    assert set(got) == set(GOLDEN[key])
    for period, exp in GOLDEN[key].items():
        moved = {"nc_cvr": "nc_cvr_clicks"}                      # the old click-based nc_cvr values are now served under nc_cvr_clicks
        for k, v in exp["v"].items():
            assert got[period]["v"][moved.get(k, k)] == v, (key, period, k)
        for k, v in exp["d"].items():
            assert got[period]["d"].get(moved.get(k, k)) == v, (key, period, k)
        assert {moved.get(k, k) for k in exp["d"]} <= set(got[period]["d"])


def test_new_columns_only_add_to_the_old_ones():
    p = payload("week", "own")
    old = set(GOLDEN["week:own"][p["rows"][0]["key"]]["v"])
    new = {k for r in p["rows"] for k in r["v"]} - old
    assert new == {"p_vitabull_nc_orders", "p_beefliver_nc_orders", "p_boneflex_nc_orders", "meta_nc_roas", "meta_nc_cpa", "new_visitors", "cost_nv", "nc_cvr_clicks"}


def test_the_two_new_total_columns_are_the_sheets_definitions():
    """Product bands' Total of NC ROAS / NC CPA = actual NC revenue (new customers) over Meta spend alone, as in the sheet (the DTC band's use Meta + Google)."""
    T = M.T
    p = payload("week", "own")
    for r in p["rows"]:
        if r["state"] == "not_loaded":
            continue
        S = T.sums(M.FR, dt.date.fromisoformat(r["start"]), dt.date.fromisoformat(r["covered_end"]))
        assert r["v"]["meta_nc_roas"] == pytest.approx(S["onc_rev"] / S["ms"], rel=1e-5)
        assert r["v"]["meta_nc_cpa"] == pytest.approx(S["ms"] / S["onc_ord"], rel=1e-5)
        assert r["v"]["nc_roas"] == pytest.approx(S["onc_rev"] / (S["ms"] + S["gs"]), rel=1e-5)


def test_layout_columns_all_exist_and_each_metric_is_placed():
    for interval in ("day", "week", "month"):
        for numbers in ("own", "nc", "all"):
            p = payload(interval, numbers, 3)
            keys = {m["key"] for m in p["metrics"]}
            placed = {c["key"] for b in p["layout"] if not b.get("external") for c in b["cols"]}
            assert placed == keys, (interval, numbers, placed ^ keys)
            for b in p["layout"]:
                labels = [(c.get("sub"), c["label"]) for c in b["cols"]]
                assert len(labels) == len(set(labels)), (b["key"], labels)
            assert [g["key"] for g in p["groups"]][:1] == (["plan"] if interval == "month" else ["totals"])
            for g in p["groups"]:
                assert {b["group"] for b in p["layout"]} >= {g["key"]}


def test_nc_cvr_is_the_one_intentional_change_new_customers_over_new_visitors():
    """nc_cvr is now Upstack new customers / Upstack new visitors (ratio of the period's sums); the fixture's old click-based values equal nc_cvr_clicks (previous test)."""
    T = M.T
    for interval in ("day", "week", "month"):
        p = payload(interval, "own")
        checked = 0
        for r in p["rows"]:
            if r["state"] == "not_loaded":
                continue
            S = T.sums(M.FR, dt.date.fromisoformat(r["start"]), dt.date.fromisoformat(r["covered_end"]))
            if r["v"].get("nc_cvr") is None:
                continue
            assert r["v"]["nc_cvr"] == pytest.approx(S["onc_ord"] / S["nv"], abs=1e-6), (interval, r["key"])
            assert r["v"]["nc_cvr"] != pytest.approx(S["onc_ord"] / (S["mc"] + S["gc"]), rel=1e-3)
            checked += 1
        assert checked >= 1, interval
