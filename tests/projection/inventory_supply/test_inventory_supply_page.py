"""The page in a real browser (headless Chromium against a live server on the test database): renders the headline, tables and grid, edits a cell in
place, console stays clean."""
from __future__ import annotations

import threading

import pytest

sync_api = pytest.importorskip("playwright.sync_api")
from werkzeug.serving import make_server                       # noqa: E402


@pytest.fixture()
def live(app_factory, seeded, with_demand):
    app = app_factory(AUTH_DISABLED=True, APP_USERS={})
    srv = make_server("127.0.0.1", 0, app, threaded=True)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_page_renders_edits_in_place_and_logs_no_errors(live, pg):
    try:
        pw = sync_api.sync_playwright().start()
        browser = pw.chromium.launch()
    except Exception as e:                                     # browser not installed on this machine
        pytest.skip(f"Chromium not available: {e}")
    try:
        page = browser.new_context(viewport={"width": 1440, "height": 900}).new_page()
        errors: list[str] = []
        page.on("console", lambda m: errors.append(m.text) if m.type in ("error", "warning") else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(live + "/projection/inventory-supply")
        page.wait_for_selector("body[data-pg-state='ready']", timeout=20000)
        assert page.locator("h1").first.inner_text() == "Inventory supply"
        tldr = page.locator(".pg-tldr-line").inner_text()
        assert "Vital Bull" in tldr and "runs out" in tldr and "the order was due" in tldr
        assert page.locator(".pg-tldr .pg-kpi").count() == 5
        assert page.locator(".pg-chip", has_text="OVERDUE").count() >= 2                       # What to order
        assert page.locator("[aria-label='Data checks'] .is-check").count() >= 8
        page.wait_for_selector("[aria-label='Stock by month'] .pg-grid, [aria-label='Stock by month'] table", timeout=10000)
        assert page.locator("[aria-label='Stock by month'] canvas").count() == 1
        # edit units of PO 7 in place; the change is saved and the headline refreshes
        box = page.locator("tr[data-id='7'] input[aria-label='Units']")
        box.fill("21,000")
        box.press("Enter")
        page.wait_for_function("document.querySelector(\"#is-po .is-status\")?.textContent === 'Saved'", timeout=10000)
        assert float(pg.execute("SELECT units FROM proj_po WHERE id = 7").fetchone()["units"]) == 21000.0
        assert pg.execute("SELECT count(*) AS n FROM proj_audit WHERE action = 'inventory_supply.po.update' AND line_key = 'inventory_supply.po:7'").fetchone()["n"] == 1
        # location tabs switch the grid
        page.get_by_role("button", name="Amazon FBA", exact=True).click()
        page.wait_for_function("document.body.innerText.includes('Units to send to reach target cover')", timeout=10000)
        # the plan opens as a comparison and writes nothing until accepted
        n_before = pg.execute("SELECT count(*) AS n FROM proj_po").fetchone()["n"]
        page.get_by_role("button", name="Suggest plan").click()
        page.wait_for_selector("[aria-label='Suggested plan'] .is-plan-cards", timeout=15000)
        assert pg.execute("SELECT count(*) AS n FROM proj_po").fetchone()["n"] == n_before
        assert not page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth")
        assert errors == [], errors
        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_timeout(300)
        assert not page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth")           # no sideways page scroll on a phone
    finally:
        browser.close()
        pw.stop()
