"""Seeding from the sheet snapshot and reconciliation against it. Skipped when the snapshot is not on this machine."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts" / "projection"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.projection.interfaces import MONTHS                      # noqa: E402
from app.projection.pnl import lines as L, store                   # noqa: E402
from pnl_support import DB_URL, TODAY                                  # noqa: E402

try:
    import snapshot_read as sr
    sr.cell("P&L", "A1")
    HAVE_SNAPSHOT = True
except Exception:                                                  # no snapshot on this machine
    HAVE_SNAPSHOT = False

pytestmark = pytest.mark.skipif(not HAVE_SNAPSHOT, reason="sheet snapshot not available")


@pytest.fixture()
def seeded(pg):
    from etl import projection_pnl
    return projection_pnl.seed(pg, sr), pg


def test_seed_counts_and_idempotence(seeded):
    counts, pg = seeded
    from etl import projection_pnl
    n = pg.execute("SELECT count(*) AS n FROM proj_actuals").fetchone()["n"]
    again = projection_pnl.seed(pg, sr)
    assert again == counts
    assert pg.execute("SELECT count(*) AS n FROM proj_actuals").fetchone()["n"] == n
    assert counts["drivers"] >= 30 and counts["cogs_sku"] == 12 and counts["vendors"] == 10 and counts["settings"] == 6


def test_loaded_history_equals_the_sheet_cell_for_cell(seeded):
    _, pg = seeded
    act = store.load_actuals(pg)
    checked = 0
    for key in L.QB_KEYS:
        ln = L.LINE_KEYS[key]
        if ln.row is None:
            continue
        for i, v in enumerate(sr.row("P&L", ln.row, "C", "U")):          # Jan-25 .. Jul-26
            if isinstance(v, (int, float)):
                assert act[key][MONTHS[i]] == pytest.approx(v, abs=1e-4), (key, MONTHS[i])
                checked += 1
            else:
                assert MONTHS[i] not in act.get(key, {}), (key, MONTHS[i])            # blank stays blank
    assert checked > 600
    assert act["inventory_shrinkage"] == {date(2025, 2, 1): 22631.4}


def test_jul_26_is_flagged_as_typed_not_a_close(seeded):
    _, pg = seeded
    r = pg.execute("SELECT DISTINCT source FROM proj_actuals WHERE month = '2026-07-01' AND line_key LIKE 'pnl.%'").fetchall()
    assert len(r) == 1 and "not a QuickBooks close" in r[0]["source"]
    r = pg.execute("SELECT DISTINCT source FROM proj_actuals WHERE month = '2026-06-01' AND line_key LIKE 'pnl.%'").fetchall()
    assert len(r) == 1 and "QuickBooks" in r[0]["source"]


def test_seeded_drivers_and_settings_read_the_sheet(seeded):
    _, pg = seeded
    d = store.load_driver_defaults(pg)
    assert d["consulting"][1] == {"from": "2026-10-01", "mode": "fixed", "value": 8000.0}
    assert d["insurance"][0]["value"] == 59.0 and d["insurance"][1]["value"] == 1200.0
    assert d["outside_services"][0] == {"from": None, "mode": "avg", "window": 5} and d["outside_services"][1]["value"] == pytest.approx(5584.3008)
    assert d["china_cogs"][1]["rate_kind"] == "ratio_avg" and d["marketing_agency"] == [{"from": None, "mode": "carry"}]
    s = store.load_settings(pg)
    assert s["headcount"] == {"base": 15000.0, "threshold": 20_000_000.0, "step": 5_000_000.0, "increment": 15000.0, "start": "2026-09-01"}
    assert s["cogs"]["us_share"] == 0.7 and s["cogs"]["price_per_bottle"] == 29.22 and s["cogs"]["current_price_per_bottle"] == 21.62
    assert s["engine"]["rate_window_months"] == 4 and s["trend"] == {"avg_window": 6} and s["freshness"] == {"stale_after_days": 45} and s["trend"] == {"avg_window": 6} and s["freshness"] == {"stale_after_days": 45}


def test_qb_map_has_sheet_labels_and_spellings(seeded):
    _, pg = seeded
    m = store.load_qb_map(pg)
    assert m["amazon fba fees"] == "amazon_fba_fees" and m["facebook adds"] == "mkt_facebook_ads" and m["facebook ads"] == "mkt_facebook_ads"
    assert m["other business expenses"] == "other_direct" and m["china cogs"] == "china_cogs"


def test_cogs_model_reproduces_the_sheet_rate(seeded):
    _, pg = seeded
    from app.projection.pnl.model import cogs_model, effective_settings
    cm = cogs_model(store.load_cogs_sku(pg), effective_settings(store.load_settings(pg), {}))
    assert cm["rate"] == pytest.approx(sr.cell("COGS", "L44"), abs=1e-9)
    assert cm["cost_per_bottle"] == pytest.approx(sr.cell("COGS", "M25"), abs=1e-9)
    assert cm["rate_at_current_price"] == pytest.approx(3.5297 / 21.62, rel=1e-3)               # at today's $21.62 bottle the rate is 16.3%


def test_engine_reproduces_the_sheet_from_its_own_typed_inputs(seeded, monkeypatch):
    """Every pnl line, every month, with the upstream modules replaced by the sheet's own values (fixture stubs)."""
    _, pg = seeded
    import reconcile_stub_run as rs
    from app.projection import registry
    monkeypatch.setattr(store, "today", lambda: TODAY)
    reports, result = rs.run(DB_URL)
    registry._cache.clear()
    bad = [(r.key, r.max_abs, r.max_rel, r.status) for r in reports if r.status != "ok"]
    assert not bad, bad
    assert len(reports) >= 80
    by = {r.key: r for r in reports}
    for k in ("total_cogs", "gross_profit", "total_expenses", "noi", "ttm_noi"):
        assert by[k].max_rel < 0.001                                              # the one explained difference moves net operating income by < 0.1%
    for k in ("amazon_fees", "cogs", "fulfillment_services", "payment_processing", "headcount", "overhead", "total_revenue"):
        assert by[k].max_abs < 0.5, k
    assert result.results["pnl"].actual_through == date(2026, 7, 1)


def test_history_months_match_the_sheet_exactly(seeded, monkeypatch):
    _, pg = seeded
    import reconcile_stub_run as rs
    from app.projection import registry
    monkeypatch.setattr(store, "today", lambda: TODAY)
    _, result = rs.run(DB_URL)
    registry._cache.clear()
    out = result.results["pnl"]
    for key in ("total_revenue", "gross_profit", "total_expenses", "noi", "total_cogs", "marketing_spend", "overhead"):
        ln = L.LINE_KEYS[key]
        for i, v in enumerate(sr.row("P&L", ln.row, "C", "U")):                  # Jan-25 .. Jul-26
            assert out.lines[key].get(MONTHS[i], 0.0) == pytest.approx(v or 0.0, abs=0.01), (key, MONTHS[i])


def test_the_one_known_difference_is_marketing_software(seeded, monkeypatch):
    """The sheet divides Apr-Jul software cost by Feb-May revenue; the engine uses Apr-Jul for both."""
    _, pg = seeded
    import reconcile_stub_run as rs
    from app.projection import registry
    monkeypatch.setattr(store, "today", lambda: TODAY)
    _, result = rs.run(DB_URL)
    registry._cache.clear()
    out = result.results["pnl"]
    aug = date(2026, 8, 1)
    sheet = sr.cell("P&L", "V63")
    assert out.lines["marketing_software"][aug] != pytest.approx(sheet, rel=0.005)
    assert out.lines["marketing_software"][aug] == pytest.approx(sheet * 1.0207, rel=0.001)
    sw = [out.lines["marketing_software"][m] for m in out.window]
    assert sw and out.driver_info["marketing_software"]["rate"] == pytest.approx(
        sum(out.lines["marketing_software"][m] for m in out.window) / sum(out.lines["total_revenue"][m] for m in out.window))
