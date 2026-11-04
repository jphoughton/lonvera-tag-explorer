"""The Variables page in a real browser (headless Chromium) against the real Flask app on a local port."""
from __future__ import annotations

import threading

import pytest
from werkzeug.serving import make_server

from conftest import make_app

sync_api = pytest.importorskip("playwright.sync_api")


@pytest.fixture()
def server(vdb):
    app = make_app(vdb, AUTH_DISABLED=True)
    srv = make_server("127.0.0.1", 0, app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()
    app.extensions["pool"].close()


@pytest.fixture()
def browser():
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:
            pytest.skip(f"Chromium not available: {e}")
        yield b
        b.close()


def open_page(browser, base, width=1440, height=900, scheme="light", allow_plan_edits=True):
    page = browser.new_context(viewport={"width": width, "height": height}, color_scheme=scheme).new_page()
    if allow_plan_edits:                                                    # the shell asks before the first edit of the plan of record; these tests have said yes
        page.add_init_script("try { sessionStorage.setItem('projection.por.edit', '1'); } catch (e) {}")
    msgs: list[str] = []
    page.on("console", lambda m: msgs.append(f"{m.type}: {m.text}") if m.type in ("error", "warning") else None)
    page.on("pageerror", lambda e: msgs.append(f"pageerror: {e}"))
    page.goto(f"{base}/projection/variables", wait_until="networkidle")
    page.wait_for_selector("body[data-pg-state=ready], body[data-pg-state=error]", timeout=20000)
    return page, msgs


def test_page_renders_header_tldr_sections_and_the_ranking(browser, server):
    page, msgs = open_page(browser, server)
    assert page.evaluate("document.body.dataset.pgState") == "ready"
    assert page.locator("h1.pg-h1").inner_text() == "Variables"
    page.wait_for_selector(".va-bar", timeout=30000)
    line = page.locator(".pg-tldr-line").inner_text()
    assert line.startswith("Every variable matches the sheet.") and len(line.split()) <= 18 and "FY27 net operating income" in line
    assert page.locator(".pg-tldr .pg-kpi").count() == 5
    mods = page.locator("details.va-mod")
    assert mods.count() == 9                                                         # eight modules and the items the model does not carry
    assert page.locator("#va-cohorts tbody tr.row").count() == 17
    assert page.locator(".va-sens .c.name").count() == 12
    assert msgs == [], msgs


def test_editing_a_number_saves_flags_it_and_reset_goes_back(browser, server, restore):
    page, msgs = open_page(browser, server)
    cell = page.locator("tr[data-id='cohorts.tail_decay'] input")
    assert cell.input_value() == "95"
    cell.fill("90")
    cell.press("Tab")
    page.wait_for_selector("tr[data-id='cohorts.tail_decay'].chg", timeout=15000)
    assert page.locator("tr[data-id='cohorts.tail_decay'] input").input_value() == "90"
    assert "1 variable differs from the sheet." in page.locator(".pg-tldr-line").inner_text()
    page.locator("tr[data-id='cohorts.tail_decay'] .sheet").get_by_text("95.0%").wait_for()
    page.get_by_label("Changed only").check()
    assert page.locator("tr.row:visible").count() == 1
    page.locator("tr[data-id='cohorts.tail_decay'] button", has_text="Reset").click()
    page.wait_for_selector("tr[data-id='cohorts.tail_decay']:not(.chg)", state="attached", timeout=15000)
    page.get_by_label("Changed only").uncheck()
    assert page.locator("tr[data-id='cohorts.tail_decay'] input").input_value() == "95"
    assert "Every variable matches the sheet." in page.locator(".pg-tldr-line").inner_text()
    assert msgs == [], msgs


def test_a_bad_value_shows_the_reason_and_keeps_the_old_one(browser, server, restore):
    page, msgs = open_page(browser, server)
    cell = page.locator("tr[data-id='cohorts.tail_decay'] input")
    cell.fill("400")
    cell.press("Tab")
    err = page.locator("tr[data-id='cohorts.tail_decay'] .va-err")
    err.wait_for(timeout=10000)
    assert "at most" in err.inner_text()
    assert page.locator("tr[data-id='cohorts.tail_decay'] input").input_value() == "95"
    assert not [m for m in msgs if "pageerror" in m]


def test_search_and_series_rows_link_to_their_page(browser, server):
    page, _ = open_page(browser, server)
    page.get_by_label("Search variables").fill("sales-tax")
    page.wait_for_timeout(200)
    assert page.locator("tr.row:visible").count() == 1
    page.get_by_label("Search variables").fill("typed months")
    row = page.locator("tr[data-id='drivers.spend_total']")
    assert row.locator("td.val").inner_text().startswith("41 typed months")
    assert row.locator("a", has_text="Edit in Spend plan").get_attribute("href").startswith("/projection/drivers")


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_phone_width_has_no_sideways_scroll_and_no_console_errors(browser, server, scheme):
    page, msgs = open_page(browser, server, width=390, height=844, scheme=scheme)
    page.wait_for_selector(".va-bar, .va-sens-wrap", timeout=30000)
    assert page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth") <= 1
    assert page.locator("tr[data-id='cohorts.tail_decay'] input").is_visible()
    assert msgs == [], msgs


def test_the_plan_of_record_guard_asks_first_and_cancel_sends_nothing(browser, server, restore):
    page, msgs = open_page(browser, server, allow_plan_edits=False)
    cell = page.locator("tr[data-id='cohorts.tail_decay'] input")
    cell.fill("90")
    cell.press("Tab")
    page.get_by_role("button", name="Cancel").click()
    page.wait_for_timeout(500)
    assert page.locator("tr[data-id='cohorts.tail_decay'] input").input_value() == "95"
    assert page.locator("tr[data-id='cohorts.tail_decay'].chg").count() == 0 and page.locator("tr[data-id='cohorts.tail_decay'] .va-err").count() == 0
    cell.fill("90")
    cell.press("Tab")
    page.get_by_role("button", name="Edit the plan of record").click()
    page.wait_for_selector("tr[data-id='cohorts.tail_decay'].chg", timeout=15000)
    assert msgs == [], msgs
