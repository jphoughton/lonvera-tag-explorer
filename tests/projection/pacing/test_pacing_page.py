"""The pacing page in a real browser (headless Chromium): renders from the API, no console errors, no sideways page scroll at 390px, controls work."""
import threading

import pytest

playwright = pytest.importorskip("playwright.sync_api")
from werkzeug.serving import make_server                                   # noqa: E402

from pacing_support import make_app                                         # noqa: E402


@pytest.fixture()
def base_url(api_app, pacing_db):
    app = make_app(pacing_db, AUTH_DISABLED=True)
    srv = make_server("127.0.0.1", 0, app, threaded=True)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()
    app.extensions["pool"].close()


@pytest.fixture()
def browser():
    with playwright.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:                                                # noqa: BLE001
            pytest.skip(f"Chromium not available: {e}")
        yield b
        b.close()


def open_page(browser, base_url, size=(1440, 900), scheme="light"):
    ctx = browser.new_context(viewport={"width": size[0], "height": size[1]}, color_scheme=scheme)
    page = ctx.new_page()
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type in ("error", "warning") else None)
    page.on("pageerror", lambda e: errors.append("PAGEERROR " + str(e)))
    page.goto(base_url + "/projection/pacing")
    page.wait_for_selector("body[data-pg-state='ready']")
    page.wait_for_selector(".pacing-trend-scroll table")
    return ctx, page, errors


def test_page_renders_with_header_cards_tables_and_a_clean_console(browser, base_url):
    ctx, page, errors = open_page(browser, base_url)
    assert page.locator("h1.pg-h1").inner_text() == "Pacing"
    assert "Revenue is pacing to" in page.locator(".pg-tldr-line").inner_text()
    assert page.locator(".pg-tldr .pg-kpi").count() >= 4
    assert page.locator(".pacing-card").count() == 6
    assert page.locator("table.pacing-table[aria-label='Month pacing against the plan'] tbody tr").count() > 40
    assert page.locator("#pacing-month option").count() >= 22
    assert page.locator(".pacing-feed").count() == 11
    # every status carries a word, not just a color
    assert page.locator(".pacing-table .pg-chip").first.inner_text().strip() in ("On pace", "Within plan", "Behind", "Over plan", "Well behind", "Well over plan")
    assert errors == []
    ctx.close()


def test_month_picker_changes_the_table(browser, base_url):
    ctx, page, errors = open_page(browser, base_url)
    page.select_option("#pacing-month", "2026-10-01")
    page.wait_for_function("document.querySelector('#pacing-month') && document.querySelector('#pacing-month').value === '2026-10-01' && !document.body.innerText.includes('Loading')")
    assert "Early in the month" not in page.locator("body").inner_text()
    page.wait_for_selector("h2:has-text('Month snapshot, October 2026')")
    assert errors == []
    ctx.close()


def test_trend_controls_switch_grain_view_and_bands(browser, base_url):
    ctx, page, errors = open_page(browser, base_url)
    page.get_by_role("button", name="Week", exact=True).click()
    page.wait_for_function("((document.querySelector('.pacing-trend-scroll thead th:last-child') || {}).innerText || '').startsWith('W')")
    page.get_by_role("button", name="Meta campaigns").click()
    page.wait_for_function("((document.querySelector('.pacing-trend-scroll tr.band td') || {}).innerText || '').toLowerCase().includes('meta campaigns')")
    page.get_by_role("button", name="Expand all").click()
    rows_open = page.locator(".pacing-trend-scroll tbody tr").count()
    page.get_by_role("button", name="Collapse all").click()
    assert page.locator(".pacing-trend-scroll tbody tr").count() < rows_open
    assert errors == []
    ctx.close()


def test_intraday_box_projects_end_of_day_spend(browser, base_url):
    ctx, page, errors = open_page(browser, base_url)
    page.fill("[data-box='intraday'] input[type='number']", "7528")
    page.fill("[data-box='intraday'] input[type='time']", "19:52")
    page.wait_for_function("document.querySelector(\"[data-box='intraday'] .pacing-note\").innerText.includes('Projected end-of-day')")
    assert "still to go" in page.locator("[data-box='intraday'] .pacing-note").inner_text()
    assert errors == []
    ctx.close()


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_phone_width_has_no_sideways_page_scroll(browser, base_url, scheme):
    ctx, page, errors = open_page(browser, base_url, (390, 844), scheme)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    assert page.locator(".pacing-card").count() == 6
    assert errors == []
    ctx.close()


@pytest.fixture()
def clean_levers(pacing_db):
    import psycopg
    def reset():
        with psycopg.connect(pacing_db) as c:
            c.execute("UPDATE proj_scenarios SET params = '{}', version = 1 WHERE slug = 'current'")
            c.execute("DELETE FROM proj_audit")
            c.execute("DELETE FROM proj_scenario_history")
    reset()
    yield
    reset()


def test_variables_panel_edits_a_lever_and_the_page_follows(browser, base_url, clean_levers):
    ctx, page, errors = open_page(browser, base_url)
    before = page.locator("[data-key='amazon_rev'] .pg-chip, .pacing-card[data-key='total_rev'] .pg-chip").first.inner_text()
    assert page.locator("[data-box='variables']").count() == 1
    page.locator("[data-box='variables'] summary").click()
    row = page.locator("[data-lever='pace_good'] input")
    row.wait_for()
    assert row.input_value() == "100%" and page.locator("[data-lever]").count() == 17
    assert page.locator("[data-lever='product_cost_pct'] .pg-chip").inner_text() == "financing"           # edited here, stored with the owning module
    page.locator("[data-lever='pace_good']").locator("input").fill("150%")
    page.locator("[data-lever='pace_good'] input").press("Tab")
    page.wait_for_selector("[data-lever='pace_good'] button:has-text('Reset')")
    page.wait_for_function("document.querySelector(\".pacing-card[data-key='total_rev'] .pg-chip\").innerText.trim() === 'Behind'")        # 106% is below the new 150% bar
    assert before == "On pace"
    page.locator("[data-lever='pace_good'] button:has-text('Reset')").click()
    page.wait_for_function("!document.querySelector(\"[data-lever='pace_good'] button\")")
    assert page.locator("[data-lever='pace_good'] input").input_value() == "100%"
    assert errors == []
    ctx.close()


def test_a_bad_value_is_refused_in_place(browser, base_url, clean_levers):
    ctx, page, errors = open_page(browser, base_url)
    page.locator("[data-box='variables'] summary").click()
    inp = page.locator("[data-lever='last_days'] input")
    inp.wait_for()
    inp.fill("2.5")
    inp.press("Tab")
    page.wait_for_selector("[data-lever='last_days'] input.bad")
    ctx.close()
