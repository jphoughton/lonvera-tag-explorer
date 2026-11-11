"""The page in a real browser (headless Chromium against a live server on the test database): renders the headline, tables and grid, edits a cell in
place, console stays clean."""
from __future__ import annotations

import threading

import pytest

sync_api = pytest.importorskip("playwright.sync_api")
from tests._servers import make_server                       # noqa: E402


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
        assert "null" not in page.locator("#is-po").inner_text().lower().split()                    # no literal null above the table
        # the edit is in the history panel and can be reverted from it
        page.wait_for_selector("[aria-label='History of orders and stock'] tbody tr", timeout=10000)
        assert "Purchase order 7" in page.locator("[aria-label='History of orders and stock']").inner_text()
        page.locator("[aria-label='History of orders and stock'] button", has_text="Revert").first.click()
        page.get_by_role("dialog").get_by_role("button", name="Revert").click()
        page.wait_for_function("document.body.innerText.includes('REVERTED')", timeout=10000)
        assert float(pg.execute("SELECT units FROM proj_po WHERE id = 7").fetchone()["units"]) == 20000.0
        assert page.locator("tr[data-id='7'] input[aria-label='Units']").input_value() == "20,000"
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


def _open(live):
    try:
        pw = sync_api.sync_playwright().start()
        browser = pw.chromium.launch()
    except Exception as e:
        pytest.skip(f"Chromium not available: {e}")
    return pw, browser


def test_reorder_table_is_the_first_thing_under_the_summary_and_checks_are_collapsed(live):
    pw, browser = _open(live)
    try:
        for size in ({"width": 1440, "height": 900}, {"width": 768, "height": 1024}):
            page = browser.new_context(viewport=size).new_page()
            page.goto(live + "/projection/inventory-supply")
            page.wait_for_selector("body[data-pg-state='ready']", timeout=20000)
            y = page.locator("[aria-label='What to order'] thead").bounding_box()["y"]
            assert y < size["height"] - 300, (size, y)                                        # visible without scrolling
            details = page.locator("[aria-label='Data checks'] details")
            assert details.count() == 1 and details.get_attribute("open") is None          # collapsed strip
            assert page.locator("[aria-label='Data checks']").bounding_box()["y"] > y        # below the reorder list
    finally:
        browser.close()
        pw.stop()


def test_on_a_phone_orders_and_transfers_are_cards_with_details_on_tap(live, pg):
    pw, browser = _open(live)
    try:
        page = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True).new_page()
        errors: list[str] = []
        page.on("console", lambda m: errors.append(m.text) if m.type in ("error", "warning") else None)
        page.goto(live + "/projection/inventory-supply")
        page.wait_for_selector("body[data-pg-state='ready']", timeout=20000)
        assert page.locator("#is-po table").count() == 0 and page.locator("[aria-label='FBA transfers'] table").count() == 0
        n_open = pg.execute("SELECT count(*) AS n FROM proj_po WHERE status NOT IN ('landed', 'cancelled')").fetchone()["n"]
        assert page.locator("#is-po details.is-poc").count() == n_open + 1                                      # one card per open order plus the add card
        assert page.locator("input[type='date']").count() <= 6                                                  # was about 240: fields are built on tap
        assert page.locator("#is-po input, #is-po select").count() < 15
        card = page.locator("#is-po details.is-poc[data-id='7']")
        assert "Vital Bull" in card.inner_text() and "20,000 units" in card.inner_text() and "IN TRANSIT" in card.inner_text()
        card.locator("summary").click()
        page.wait_for_selector("#is-po details[data-id='7'] input[aria-label='Units']")
        box = card.locator("input[aria-label='Units']")
        box.fill("21,500")
        box.press("Enter")
        page.wait_for_function("document.querySelector(\"#is-po .is-status\")?.textContent === 'Saved'", timeout=10000)
        assert float(pg.execute("SELECT units FROM proj_po WHERE id = 7").fetchone()["units"]) == 21500.0
        assert page.locator("#is-po details[data-id='7']").get_attribute("open") is not None                    # stays open after saving
        assert "21,500 units" in page.locator("#is-po details[data-id='7'] summary").inner_text()
        # add card and transfer cards
        page.locator("#is-po-add summary").click()
        assert page.locator("#is-po-add input[type='date']").count() == 2
        page.locator("[aria-label='FBA transfers'] details.is-poc").nth(1).locator("summary").click()
        page.wait_for_selector("[aria-label='FBA transfers'] details.is-poc[open] input[type='date']")
        assert page.locator("[aria-label='FBA transfers'] details.is-poc[open] input[type='date']").count() == 2
        assert not page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth")
        assert errors == [], errors
        page.set_viewport_size({"width": 1100, "height": 800})                                                  # back to the table when the window widens
        page.wait_for_selector("#is-po table")
    finally:
        browser.close()
        pw.stop()


def test_stock_valuation_shows_where_each_cost_comes_from_and_editing_it_writes_the_pnl_key(live, pg):
    pw, browser = _open(live)
    try:
        page = browser.new_context(viewport={"width": 1440, "height": 900}).new_page()
        errors: list[str] = []
        page.on("console", lambda m: errors.append(m.text) if m.type in ("error", "warning") else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(live + "/projection/inventory-supply")
        page.wait_for_selector("body[data-pg-state='ready']", timeout=20000)
        page.locator("[aria-label='Lead times and prices'] summary", has_text="Lead times").click()
        row = page.locator("[aria-label='Lead times and prices'] tr", has=page.locator("input[aria-label='Vital Bull landed unit cost']"))
        assert row.locator("td").last.inner_text() == "table"
        box = row.locator("input[aria-label='Vital Bull landed unit cost']")
        box.fill("3.50")
        box.press("Enter")
        page.wait_for_function("document.querySelector(\"[aria-label='Lead times and prices'] .is-status\")?.textContent === 'Saved'", timeout=10000)
        params = pg.execute("SELECT params FROM proj_scenarios WHERE is_default").fetchone()["params"]
        assert params["pnl"]["cogs"]["china_cost"] == {"LV-VBULL": 3.5}
        page.wait_for_function("[...document.querySelectorAll(\"[aria-label='Lead times and prices'] tr\")].some(r => r.querySelector(\"input[aria-label='Vital Bull landed unit cost']\") && r.lastElementChild.textContent === 'P&L cost model')", timeout=10000)
        assert page.locator("[aria-label='Lead times and prices'] details").first.get_attribute("open") is not None        # stays open after saving
        assert errors == [], errors
    finally:
        browser.close()
        pw.stop()
