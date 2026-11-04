"""Seed: the Current plan equals the sheet's typed cells, migration 160 is that seed, and re-running it never overwrites an edit."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import fin_helpers  # noqa: F401  (puts the repo and scripts/projection on sys.path)
from etl import projection_financing as seed
from app.projection.financing import params as P

ROOT = Path(__file__).resolve().parents[3]


def test_seed_params_equal_the_code_defaults_except_aov_and_cac():
    prm = seed.seed_params()
    want = P.deep_merge(P.DEFAULTS, {})
    want["payback"]["aov"] = pytest.approx(69.15600858369098)
    want["payback"]["cac"] = pytest.approx(105.4916738197425)
    assert prm == want


def test_seed_reads_the_sheets_terms():
    prm = seed.seed_params()
    assert prm["debt"] == {"principal": 3_000_000.0, "rate": 0.15, "mode": "typed", "fixed_payment": 125_000.0}
    assert prm["tax"]["rate"] == 0.21
    assert [(p["month"], p["from"], p["to"]) for p in prm["tax"]["payments"]] == [
        ("2027-09-01", "2026-09-01", "2026-12-01"), ("2028-06-01", "2027-01-01", "2027-12-01"), ("2029-09-01", "2028-01-01", "2028-12-01")]
    cols = {c["payback"]: c for c in prm["payout"]["columns"]}
    assert cols[4] == {"payback": 4, "first_scale": 850_000.0, "first_bonus": 2_000.0, "step_up": 0.15, "step_above": 0.10}
    assert cols[3]["step_up"] == 0.16 and cols[3]["step_above"] == 0.12 and cols[2]["first_bonus"] == 3_500.0
    assert prm["payout"]["scales"][0] == 800_000 and prm["payout"]["scales"][-1] == 1_500_000
    assert prm["matrix"]["spends"][0] == 200_000 and prm["matrix"]["spends"][-1] == 2_000_000 and len(prm["matrix"]["roas"]) == 13


def test_seed_series_are_the_sheets_typed_cells():
    cells = seed.seed_series()
    principal = {c["month"]: c["value"] for c in cells if c["line_key"] == P.S_PRINCIPAL}
    assert len(principal) == 21 and sum(principal.values()) == 2_999_000          # the typed schedule leaves $1,000 of the $3.0M outstanding
    assert principal["2026-10-01"] == 180_000 and "2026-09-01" not in principal and principal["2028-12-01"] == 140_000
    other = {(c["line_key"], c["month"]): c for c in cells if c["line_key"] != P.S_PRINCIPAL}
    assert other[(P.S_NOI_BI, "2026-09-01")]["value"] == 150_000 and other[(P.S_NOI_BI, "2026-09-01")]["kind"] == "override"
    assert other[(P.S_OPENING, "2026-10-01")]["value"] == 0
    assert all(c["note"] for c in cells)                                         # every typed value carries its source


def test_migration_160_is_exactly_the_seed():
    on_disk = (ROOT / "migrations" / "160_proj_financing.sql").read_text()
    assert on_disk.strip() == seed.build_sql().strip(), "regenerate: python -m etl.projection_financing --sql > migrations/160_proj_financing.sql"
    for secret in ("password", "api_key", "secret", "token"):
        assert secret not in on_disk.lower()


def test_migration_seeded_the_current_plan(pg):
    row = pg.execute("SELECT params FROM proj_scenarios WHERE slug = 'current'").fetchone()
    assert row["params"]["financing"]["debt"]["principal"] == 3_000_000
    n = pg.execute("SELECT count(*) AS n FROM proj_series WHERE line_key LIKE 'financing.%'").fetchone()["n"]
    assert n == 23


def test_reseeding_is_idempotent_and_never_overwrites_an_edit(dconn, pg):
    pg.execute("UPDATE proj_scenarios SET params = jsonb_set(params, '{financing,debt,rate}', '0.2') WHERE slug = 'current'")
    pg.execute("DELETE FROM proj_series WHERE line_key = 'financing.principal_paid' AND month = '2026-10-01'")
    pg.execute("UPDATE proj_series SET value = 1 WHERE line_key = 'financing.principal_paid' AND month = '2026-11-01'")
    seed.apply_seed(dconn)
    dconn.commit()
    row = pg.execute("SELECT params FROM proj_scenarios WHERE slug = 'current'").fetchone()
    assert row["params"]["financing"]["debt"]["rate"] == 0.2                      # the edit survives
    cells = {r["month"].isoformat(): r["value"] for r in pg.execute("SELECT month, value FROM proj_series WHERE line_key = 'financing.principal_paid'")}
    assert "2026-10-01" not in cells and cells["2026-11-01"] == 1                 # a deleted or edited cell is not put back
    assert pg.execute("SELECT count(*) AS n FROM proj_series WHERE line_key LIKE 'financing.%'").fetchone()["n"] == 22


def test_seeding_an_empty_scenario_table(bare, dconn, pg):
    seed.apply_seed(dconn)
    dconn.commit()
    assert pg.execute("SELECT count(*) AS n FROM proj_series WHERE line_key LIKE 'financing.%'").fetchone()["n"] == 23
    assert json.loads(json.dumps(pg.execute("SELECT params FROM proj_scenarios").fetchone()["params"]))["financing"]["close_month"] == "2026-09-01"
