"""The P&L page in a real browser (headless Chromium): renders, states the QuickBooks status, edits a driver, previews an upload, console clean.

Runs the Flask app in a thread against the test database with the fake upstream modules. Skipped when Playwright or Chromium is not installed.
"""
from __future__ import annotations

import io
import sys
import threading
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

pw = pytest.importorskip("playwright.sync_api")

from tests._servers import loopback, make_server, origin  # noqa: E402

from app.projection.pnl import store                            # noqa: E402
from pnl_support import TODAY, seed_basic                           # noqa: E402
from pnl_helpers import qb_rows, to_csv                          # noqa: E402
from test_pnl_api import DRIVERS, REQUIRED                       # noqa: E402
from psycopg.types.json import Jsonb                            # noqa: E402


@pytest.fixture()
def server(app, upstream_pkg, monkeypatch, pg):
    from app.projection import registry
    monkeypatch.setattr(registry, "PACKAGE", upstream_pkg)
    monkeypatch.setattr(store, "today", lambda: TODAY)
    seed_basic(pg)
    store.upsert_driver_defaults(pg, [(k, s, "n", "test") for k, s in DRIVERS])
    store.upsert_qb_map(pg, [("shopify sales", "dtc_rev", None), ("amazon sales", "amazon_rev", None), ("amazon fba fees", "amazon_fba_fees", None),
                             ("china cogs", "china_cogs", None), ("other business expenses", "other_direct", None), ("bank fees", "bank_fees", None),
                             ("accounting", "accounting", None), ("fulfillment services", "fulfillment_services", None), ("facebook ads", "mkt_facebook_ads", None)])
    pg.execute("INSERT INTO proj_pnl_settings (key, value) VALUES ('upload', %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
               (Jsonb({"low_ratio": 0.001, "high_ratio": 1000.0, "required_families": REQUIRED}),))
    srv = make_server(loopback(), 0, app, threaded=True)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield origin(srv.server_port)
    srv.shutdown()


@pytest.fixture()
def page(server, tmp_path):
    with pw.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:                                    # browser not installed
            pytest.skip(f"Chromium not available: {e}")
        ctx = b.new_context(viewport={"width": 1440, "height": 900})
        pg = ctx.new_page()
        pg.console_errors = []
        pg.on("console", lambda m: pg.console_errors.append(m.text) if m.type in ("error", "warning") else None)
        pg.on("pageerror", lambda e: pg.console_errors.append(str(e)))
        r = pg.request.post(server + "/api/login", data='{"username": "JoshHoughton", "password": "throwaway-test-password"}',
                            headers={"Content-Type": "application/json"})        # the app's login is on: sign in through the API first
        assert r.status == 204, r.status
        yield pg
        ctx.close()
        b.close()


def allow_plan_edit(page):
    """The shell asks once per tab before the first edit of the plan of record (lib/guard.js); choose "Edit the plan of record"."""
    try:
        page.wait_for_selector('[data-choice="edit"]', timeout=4000)
        page.click('[data-choice="edit"]')
    except Exception:                                              # already allowed in this session
        pass


def open_page(page, server):
    page.goto(server + "/projection/pnl")
    try:
        page.wait_for_selector("body[data-pg-state=ready]", timeout=20000)
        page.wait_for_selector(".pg-tldr", timeout=20000)
    except Exception:
        raise AssertionError("page did not render: " + page.inner_text("body")[:600] + " | console: " + repr(page.console_errors)) from None


def test_page_renders_with_tldr_grid_and_clean_console(page, server):
    open_page(page, server)
    tldr = page.inner_text(".pg-tldr")
    low = tldr.lower()                                              # kickers are upper-cased by CSS
    assert "fy27 net operating income" in low and "quickbooks" in low and "books through jul 2026" in low and "feeds aug-oct 2026" in low
    assert "Books through Jul 2026. Feeds only, no books yet: Aug-Oct 2026. Projected from Nov 2026." in page.inner_text(".pg-source")
    legend = page.inner_text(".pn-legend").lower()
    assert "books" in legend and "feeds" in legend and "projected" in legend
    assert page.locator(".pg-kpi").count() >= 5
    assert page.locator("table").first.is_visible()
    assert "Net operating income" in page.inner_text("main")
    assert page.locator("canvas").count() == 2
    assert page.locator("h2:has-text('Implied value')").count() == 1 and page.locator("h2:has-text('Cost drivers')").count() == 1
    assert page.console_errors == []


def test_no_horizontal_scroll_on_a_phone(server, page):
    page.set_viewport_size({"width": 390, "height": 844})
    open_page(page, server)
    assert page.evaluate("document.documentElement.scrollWidth") <= 390


def test_changing_a_driver_updates_the_projection_and_the_row(page, server, pg):
    open_page(page, server)
    page.click('button[aria-label="Change how Accounting is projected"]')
    page.wait_for_selector("dialog[open]")
    page.fill('dialog input[aria-label="Dollars a month"]', "4000")
    page.click("dialog >> text=Save")
    allow_plan_edit(page)
    try:
        page.wait_for_selector("dialog", state="detached", timeout=8000)
    except Exception:
        raise AssertionError("the dialog stayed open: " + page.inner_text("dialog .pg-dialog-err") + " | console: " + repr(page.console_errors)) from None
    page.wait_for_selector("text=Changed in this scenario")
    t = page.evaluate("fetch('/api/projection/pnl/table').then(r => r.json())")
    row = next(r for r in t["rows"] if r["key"] == "accounting")
    assert row["values"][t["months"].index("2026-09-01")] == 4000.0
    n = pg.execute("SELECT count(*) AS n FROM proj_audit WHERE action = 'scenario.params'").fetchone()["n"]
    assert n == 1
    assert page.console_errors == []


def test_typing_over_a_past_month_is_refused_with_a_reason(page, server):
    open_page(page, server)
    page.click('button[aria-label="Change how Accounting is projected"]')
    page.wait_for_selector("dialog[open]")
    page.fill('dialog input[aria-label="Month to type over"]', "2026-05")
    page.fill('dialog input[aria-label="Amount for that month"]', "5")
    page.click("dialog >> text=Type over this month")
    assert "come from QuickBooks" in page.inner_text("dialog .pg-dialog-err")


def test_upload_preview_shows_changes_and_unmapped_before_anything_is_saved(page, server, pg, tmp_path):
    pg.execute("DELETE FROM proj_pnl_qb_map WHERE account = 'shopify sales'")
    f = tmp_path / "qb.csv"
    f.write_bytes(to_csv(qb_rows()))
    open_page(page, server)
    page.click("text=Upload QuickBooks export")
    page.wait_for_selector("dialog[open]")
    page.set_input_files("dialog input[type=file]", str(f))
    page.wait_for_selector("dialog >> text=NOT MAPPED")
    body = page.inner_text("dialog")
    assert "Shopify Sales" in body and "cells added" in body and "Nothing is saved until you commit" in body
    before = pg.execute("SELECT count(*) AS n FROM proj_actuals").fetchone()["n"]
    page.select_option('dialog select[aria-label="Line for Shopify Sales"]', "dtc_rev")
    page.click('dialog button:has-text("Map")')
    # the FIRST preview (before the mapping) already has a Commit button for the 24 cells that are mapped; wait for the preview that includes the new mapping (27 cells),
    # otherwise the click can carry the old preview's hash and the server answers 409 "the loaded data changed since the preview"
    page.wait_for_selector('dialog button:text-is("Commit 27 cells")', timeout=10000)
    assert pg.execute("SELECT count(*) AS n FROM proj_actuals").fetchone()["n"] == before               # still nothing written
    page.click('dialog button:has-text("Commit")')
    page.wait_for_selector("dialog", state="detached", timeout=10000)
    assert pg.execute("SELECT count(*) AS n FROM proj_audit WHERE action = 'pnl.upload'").fetchone()["n"] == 1
    assert page.console_errors == []
