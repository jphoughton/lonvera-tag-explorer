"""Constants and helpers shared by the pnl tests (a uniquely named module: `conftest` names collide across projection test directories)."""
from __future__ import annotations

import os
from datetime import date

PG_BASE = os.environ.get("TEST_PG_BASE", "postgresql://localhost")
DB_NAME = os.environ.get("TEST_PROJ_PNL_DB", "tagexp_proj_pnl_test")
DB_URL = f"{PG_BASE}/{DB_NAME}"
PASSWORD = "throwaway-test-password"
TODAY = date(2026, 11, 3)


def login(client, username: str = "JoshHoughton"):
    r = client.post("/api/login", json={"username": username, "password": PASSWORD})
    assert r.status_code == 204
    return client


def seed_basic(pg, through: date = date(2026, 7, 1)):
    """A small, round data set: 12 months of loaded actuals ending at `through`, no drivers, no COGS SKUs."""
    from app.projection import series
    from app.projection.interfaces import add_months
    months = [add_months(through, -i) for i in range(11, -1, -1)]
    rows = []
    for m in months:
        rows += [("pnl.dtc_rev", m, 800_000.0), ("pnl.amazon_rev", m, 200_000.0), ("pnl.tiktok_rev", m, 0.0),
                 ("pnl.amazon_fba_fees", m, 20_000.0), ("pnl.china_cogs", m, 100_000.0), ("pnl.fulfillment_services", m, 100_000.0),
                 ("pnl.mkt_facebook_ads", m, 300_000.0), ("pnl.marketing_agency", m, 10_000.0), ("pnl.accounting", m, 2_000.0),
                 ("pnl.marketing_software", m, 3_000.0)]
    series.upsert_actuals(pg, rows, "test")
    return months
