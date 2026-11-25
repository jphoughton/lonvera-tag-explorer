"""The P&L page on the new chart of accounts in a real browser: rows, old-format marking, driver table with mode / base / resolved value / window, the
chart setting, the new-chart upload, a driver edit; 375 px and desktop; console clean. Skipped when Playwright or Chromium is not installed."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

pw = pytest.importorskip("playwright.sync_api")

from test_pnl_page import allow_plan_edit, page, server           # noqa: E402,F401  (fixtures: the app thread, the browser page)
from pnl_helpers import qb26_expected, qb26_rows, make_xlsx, seed_qb2026   # noqa: E402
from app.projection import series                                   # noqa: E402

P = "/api/projection/pnl"


def load_books(pg, months=(date(2026, 8, 1), date(2026, 9, 1), date(2026, 10, 1)), factors=(0.9, 1.0, 1.1)):
    from pnl_helpers import QB26_ACCOUNTS
    from app.projection.pnl.charts import qb2026 as Q
    code_key = {c: k for k, c in Q.ACCOUNTS.items()}
    rows = [("qb26." + code_key[n.split(" ", 1)[0]], m, round(base * f, 2)) for n, base in QB26_ACCOUNTS for m, f in zip(months, factors)]
    series.upsert_actuals(pg, rows, "test new chart")


@pytest.fixture()
def qbserver(server, pg, page):
    seed_qb2026(pg)
    load_books(pg)
    r = page.request.put(server + P + "/settings", data='{"scenario": "current", "settings": {"chart": "qb2026"}}', headers={"Content-Type": "application/json"})
    if r.status == 409 or r.status == 400:
        pytest.fail(r.text())
    return server


def open_page(page, server):
    page.goto(server + "/projection/pnl")
    page.wait_for_selector("body[data-pg-state=ready]", timeout=20000)
    page.wait_for_selector(".pg-tldr", timeout=20000)


def test_new_chart_page_renders_old_format_marking_rows_and_drivers(page, qbserver):
    open_page(page, qbserver)
    legend = page.inner_text(".pn-legend").lower()
    assert "old format" in legend and "books" in legend and "projected" in legend
    assert "Books through Oct 2026" in page.inner_text(".pg-source") and "Old format" in page.inner_text(".pg-source")
    page.wait_for_selector("text=Below the line")
    main = page.inner_text("main").lower()                                      # grid section headers are upper-cased by CSS
    for label in ("income", "cost of goods sold", "gross profit", "marketing", "overhead", "net operating income", "below the line"):      # the sections of the new chart
        assert label in main, label
    labels = page.locator(".pg-lt").all_inner_texts()                           # row labels are in the grid even while a section is collapsed
    for label in ("4020 DTC sales", "Total income (net revenue)", "Old-format cost of goods (no account in the new books)", "Total for 6165 Software and apps",
                  "8000 Interest expense", "Net income"):
        assert label in labels, label
    assert page.locator("canvas").count() == 2
    drivers = page.inner_text("h2:has-text('Cost drivers') >> xpath=ancestor::div[contains(@class,'pg-card')]")
    assert "5020 Shopify cost of goods" in drivers and "Mean of the trailing months of books" in drivers and "6032 Facebook ads and 6033 Google ads follow the spend plan" in drivers
    assert "Based on Aug to Oct 2026" in drivers
    t = page.evaluate("fetch('/api/projection/pnl/table').then(r => r.json())")
    total = next(r for r in t["rows"] if r["key"] == "total_income")["values"][t["months"].index("2026-10-01")]
    assert total == pytest.approx(qb26_expected(1.1)["total_income"], abs=0.01)
    assert "Cost of goods sold model" not in main and "Sales tax deducted" not in main
    assert page.console_errors == []


def test_chart_selector_in_settings_switches_back_and_forth(page, qbserver):
    open_page(page, qbserver)
    sel = page.locator("#pn-chart")
    assert sel.input_value() == "qb2026"
    sel.select_option("sheet")
    page.click("text=Save settings")
    allow_plan_edit(page)
    page.wait_for_selector("text=Sales tax deducted from DTC revenue", timeout=15000)
    assert page.locator("#pn-chart").input_value() == "sheet" and "below the line" not in page.inner_text("main").lower()
    page.locator("#pn-chart").select_option("qb2026")
    page.click("text=Save settings")
    page.wait_for_selector("text=Below the line", timeout=15000)
    assert page.console_errors == []


def test_editing_a_new_chart_driver_saves_a_fixed_step_for_that_line(page, qbserver, pg):
    open_page(page, qbserver)
    page.click('button[aria-label="Change how 6120 Consulting is projected"]')
    page.wait_for_selector("dialog[open]")
    page.select_option('dialog select[aria-label="Step 1 method"]', "fixed")
    page.fill('dialog input[aria-label="Dollars a month"]', "4321")
    page.click("dialog >> text=Save")
    allow_plan_edit(page)
    page.wait_for_selector("dialog", state="detached", timeout=8000)
    page.wait_for_selector("text=Changed in this scenario")
    t = page.evaluate("fetch('/api/projection/pnl/table').then(r => r.json())")
    row = next(r for r in t["rows"] if r["key"] == "consulting")
    assert row["values"][t["months"].index("2026-12-01")] == 4321.0
    params = pg.execute("SELECT params FROM proj_scenarios WHERE slug = 'current'").fetchone()["params"]["pnl"]
    assert params["qb26"]["drivers"]["consulting"]["segments"][0] == {"from": None, "mode": "fixed", "value": 4321.0}
    assert page.console_errors == []


def test_typing_over_a_new_chart_month_uses_the_new_chart_series_key(page, qbserver, pg):
    open_page(page, qbserver)
    page.click('button[aria-label="Change how 6120 Consulting is projected"]')
    page.wait_for_selector("dialog[open]")
    page.fill('dialog input[aria-label="Month to type over"]', "2027-02")
    page.fill('dialog input[aria-label="Amount for that month"]', "777")
    page.click("dialog >> text=Type over this month")
    allow_plan_edit(page)
    page.wait_for_selector('dialog input[aria-label="Amount for Feb 2027"]', timeout=8000)
    assert pg.execute("SELECT value FROM proj_series WHERE line_key = 'pnl.qb26.consulting' AND month = '2027-02-01'").fetchone()["value"] == 777


def test_uploading_a_new_chart_file_shows_the_chart_and_commits(page, qbserver, pg, tmp_path):
    f = tmp_path / "oct.xlsx"
    f.write_bytes(make_xlsx(qb26_rows(["Oct 2026"], [1.3])))
    open_page(page, qbserver)
    page.click("text=Upload QuickBooks export")
    page.wait_for_selector("dialog[open]")
    page.set_input_files("dialog input[type=file]", str(f))
    page.wait_for_selector("dialog >> text=NEW CHART")
    body = page.inner_text("dialog")
    assert "Numbered QuickBooks accounts" in body and "32" in body and "cells changed" in body
    before_sheet = pg.execute("SELECT count(*) AS n FROM proj_actuals WHERE left(line_key, 4) = 'pnl.'").fetchone()["n"]
    page.wait_for_selector('dialog button:text-is("Commit 32 cells")', timeout=10000)
    page.click('dialog button:has-text("Commit")')
    page.wait_for_selector("dialog", state="detached", timeout=10000)
    assert pg.execute("SELECT value FROM proj_actuals WHERE line_key = 'qb26.dtc_sales' AND month = '2026-10-01'").fetchone()["value"] == 1_040_000
    assert pg.execute("SELECT count(*) AS n FROM proj_actuals WHERE left(line_key, 4) = 'pnl.'").fetchone()["n"] == before_sheet
    page.wait_for_selector("text=New chart")                                    # the history row names the chart
    assert page.console_errors == []


def test_no_horizontal_scroll_at_375_and_desktop(page, qbserver):
    for w in (375, 1440):
        page.set_viewport_size({"width": w, "height": 844})
        open_page(page, qbserver)
        page.wait_for_selector("text=Below the line")
        assert page.evaluate("document.documentElement.scrollWidth") <= w, w
