"""The pacing page in a real browser (headless Chromium): renders from the API, no console errors, no sideways page scroll at 390px, controls work."""
import threading

import pytest

playwright = pytest.importorskip("playwright.sync_api")
from tests._servers import loopback, make_server, origin  # noqa: E402

from pacing_support import make_app                                         # noqa: E402


@pytest.fixture()
def base_url(api_app, pacing_db):
    app = make_app(pacing_db, AUTH_DISABLED=True)
    srv = make_server(loopback(), 0, app, threaded=True)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield origin(srv.server_port)
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
    assert page.locator(".pacing-feed").count() == 12
    # every status carries a word, not just a color
    assert page.locator(".pacing-table .pg-chip").first.inner_text().strip() in ("On pace", "Within plan", "Behind", "Over plan", "Well behind", "Well over plan")
    assert "Last 28 days unavailable: Meta spend feed has 0 of 28 days" in page.locator("table.pacing-table[aria-label='Month pacing against the plan']").inner_text()
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
    page.get_by_role("group", name="Period").get_by_role("button", name="Week", exact=True).click()
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
    page.wait_for_function("document.querySelector(\"[data-box='intraday'] .pacing-note\")?.innerText.includes('Projected end-of-day')")
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
    assert row.input_value() == "100%" and page.locator("[data-lever]").count() == 18
    assert page.locator("[data-lever='product_cost_pct'] .pg-chip").inner_text() == "drivers"           # edited here, stored in the one payback store
    page.locator("[data-lever='pace_good']").locator("input").fill("150%")
    page.locator("[data-lever='pace_good'] input").press("Tab")
    page.locator("[data-choice='edit']").click()                                                                  # the shell asks once before the plan of record is edited
    page.wait_for_selector("[data-lever='pace_good'] button:has-text('Reset')")
    page.wait_for_function("document.querySelector(\".pacing-card[data-key='total_rev'] .pg-chip\")?.innerText.trim() === 'Behind'")        # 106% is below the new 150% bar
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
    page.locator("[data-choice='edit']").click()
    page.wait_for_selector("[data-lever='last_days'] input.bad")
    ctx.close()


# ---------------------------------------------------------------------------------------------------------------- NC revenue today card

import datetime as _dt                                                       # noqa: E402
import os                                                                    # noqa: E402

import psycopg                                                               # noqa: E402
from test_pacing_nc_today import nc                                          # noqa: E402,F401  (fixture: a deterministic hourly curve for the 30 closed days)

SHOTS = os.environ.get("NCREV_SHOTS")


def shot(page, name):
    if SHOTS:
        os.makedirs(SHOTS, exist_ok=True)
        page.locator("[data-box='intraday-nc']").screenshot(path=os.path.join(SHOTS, name))


def test_nc_revenue_card_projects_end_of_day_new_customer_revenue(browser, base_url, nc):
    ctx, page, errors = open_page(browser, base_url)
    box = "[data-box='intraday-nc']"
    assert page.locator(f"{box} h2").inner_text() == "NC revenue today"
    page.fill(f"{box} input[type='number']", "300")
    page.fill(f"{box} input[type='time']", "12:00")
    page.wait_for_function("document.querySelector(\"[data-box='intraday-nc'] .pacing-note\")?.innerText.includes('Projected end-of-day')")
    txt = page.locator(f"{box} .pacing-note[role='status']").inner_text()
    assert "Projected end-of-day NC revenue $1,000*, $700 still to go (30% of a typical day's NC revenue is in by 12:00)." in txt
    assert "last 30 closed days of hourly Shopify first orders" in page.locator(box).inner_text()
    assert page.locator(f"{box} details.pacing-curve").count() == 1 and page.locator(f"{box} details table tbody tr").count() == 24     # collapsed, 24 hours behind it
    assert not page.locator(f"{box} details").evaluate("d => d.open")
    page.locator(f"{box} summary").click()
    assert "2026-10-04 to 2026-11-02: 30 closed days used" in page.locator(f"{box} details").inner_text()
    assert page.locator(f"{box} details tr.now td").first.inner_text() == "12:00"
    assert page.locator(f"{box} label.pacing-check").is_hidden()                                          # weekdays and weekends do not differ in this fixture
    assert page.locator(f"{box} .pacing-note").filter(has_text="Stale data").count() == 0 or page.locator(f"{box} .pacing-note[hidden]").count() >= 1
    shot(page, "nc_card_desktop_open.png")
    # a time before anything has landed: a plain sentence, not a number
    page.fill(f"{box} input[type='time']", "05:00")
    page.wait_for_function("document.querySelector(\"[data-box='intraday-nc'] .pacing-note\")?.innerText.includes('Too early')")
    assert errors == []
    ctx.close()


def test_nc_revenue_card_phone_width_and_dark(browser, base_url, nc):
    for scheme in ("light", "dark"):
        ctx, page, errors = open_page(browser, base_url, (375, 800), scheme)
        box = "[data-box='intraday-nc']"
        page.fill(f"{box} input[type='number']", "300")
        page.fill(f"{box} input[type='time']", "12:00")
        page.wait_for_function("document.querySelector(\"[data-box='intraday-nc'] .pacing-note\")?.innerText.includes('Projected end-of-day')")
        page.locator(f"{box} summary").click()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
        assert page.locator(box).evaluate("el => el.scrollWidth <= el.clientWidth + 1")
        shot(page, f"nc_card_375_{scheme}.png")
        assert errors == []
        ctx.close()


def test_nc_revenue_card_weekday_switch_and_stale_warning(browser, base_url, nc, pacing_db):
    with psycopg.connect(pacing_db) as c:                                    # weekends carry their hours earlier than weekdays: the gap at 12:00 is well over 3 points
        c.execute("DELETE FROM proj_feed_nc_hourly")
        for i in range(1, 31):
            d = _dt.date(2026, 11, 3) - _dt.timedelta(days=i)
            shape = {7: 40.0, 18: 60.0} if d.weekday() < 5 else {7: 70.0, 18: 30.0}
            for h, pct in shape.items():
                c.execute("INSERT INTO proj_feed_nc_hourly (day, hour, nc_rev, nc_orders) VALUES (%s, %s, %s, 1)", (d, h, pct * 10))
        c.commit()
    ctx, page, errors = open_page(browser, base_url)
    box = "[data-box='intraday-nc']"
    page.fill(f"{box} input[type='number']", "100")
    page.fill(f"{box} input[type='time']", "12:00")
    page.wait_for_function("document.querySelector(\"[data-box='intraday-nc'] .pacing-note\")?.innerText.includes('Projected end-of-day')")
    page.wait_for_selector(f"{box} label.pacing-check", state="visible")
    assert "Projected end-of-day NC revenue $" in page.locator(f"{box} .pacing-note[role='status']").inner_text()
    before = page.locator(f"{box} .pacing-note[role='status']").inner_text()
    page.locator(f"{box} label.pacing-check input").check()                  # Tuesday only: a weekday-shaped curve (40% by noon)
    page.wait_for_function("document.querySelector(\"[data-box='intraday-nc'] .pacing-note[role='status']\")?.innerText.includes('(40% of a typical day')")
    assert page.locator(f"{box} .pacing-note[role='status']").inner_text() != before
    page.locator(f"{box} summary").click()
    assert "weekdays 40.0% in" in page.locator(f"{box} details").inner_text()
    ctx.close()
    with psycopg.connect(pacing_db) as c:                                    # the newest closed day is 5 days before yesterday
        c.execute("DELETE FROM proj_feed_nc_hourly WHERE day > '2026-10-28'")
        c.commit()
    ctx, page, errors = open_page(browser, base_url)
    page.wait_for_selector(f"{box} .pacing-note:has-text('Stale data')")
    assert "2026-10-28" in page.locator(f"{box}").inner_text()
    shot(page, "nc_card_stale.png")
    assert errors == []
    ctx.close()


def test_nc_revenue_card_without_a_curve_says_so(browser, base_url, pacing_db):
    with psycopg.connect(pacing_db) as c:
        c.execute("DELETE FROM proj_feed_nc_hourly")
        c.commit()
    ctx, page, errors = open_page(browser, base_url)
    box = "[data-box='intraday-nc']"
    page.fill(f"{box} input[type='number']", "300")
    page.wait_for_function("document.querySelector(\"[data-box='intraday-nc'] .pacing-note\")?.innerText.includes('No closed day')")
    assert "Projected" not in page.locator(f"{box} .pacing-note[role='status']").inner_text().split("No closed day")[0]
    assert errors == []
    ctx.close()


@pytest.mark.parametrize("pace,word,tone", [(1.12, "On pace", "good"), (0.95, "Behind", "warn"), (0.70, "Well behind", "bad")])
def test_dtc_nc_aov_row_renders_after_nc_cpa_with_higher_is_better_chips(browser, base_url, pace, word, tone):
    import json as _json
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()

    def patch(route):
        resp = route.fetch()
        j = resp.json()
        for r in j["rows"]:
            if r["key"] == "dtc_nc_aov":
                r["goal"] = 70.0
                r["projection"] = r["mtd"] = 70.0 * pace
                r["pacing"] = pace
                r["status"] = tone
        route.fulfill(response=resp, body=_json.dumps(j), headers={**resp.headers, "content-type": "application/json"})
    page.route("**/api/projection/pacing/month*", patch)
    page.goto(base_url + "/projection/pacing")
    page.wait_for_selector("body[data-pg-state='ready']")
    table = page.locator("table.pacing-table[aria-label='Month pacing against the plan']")
    labels = [t.strip() for t in table.locator("tbody tr th.lab").all_inner_texts()]
    i = next(k for k, t in enumerate(labels) if t.startswith("DTC NC CPA"))
    assert labels[i + 1].startswith("DTC NC AOV")
    row = table.locator("tbody tr").filter(has=page.locator("th.lab", has_text="DTC NC AOV"))
    assert row.locator(".pg-chip").first.inner_text().strip() == word
    assert row.locator(".pct").inner_text().strip() == f"{round(pace * 100)}%"
    assert "$" in row.inner_text() and row.locator("th.lab .pg-note").count() == 1                      # the little i popover carries the definition
    assert "plan's AOV" in row.locator("th.lab .pg-note").get_attribute("title")


def test_payback_card_headline_is_the_sheet_definition_with_all_costs_as_a_second_line(browser, base_url):
    """Josh 2026-11-09: the card must match the sheet's Payback tab (product cost + fulfillment only); the stricter reading is a muted second line."""
    ctx, page, errors = open_page(browser, base_url)
    card = page.locator(".pacing-card[data-key='payback']")
    if card.count() == 0:
        pytest.skip("no payback on the test month")
    assert card.locator(".t").inner_text().strip().lower() == "payback months"
    assert "product cost and fulfillment only" in card.locator(".g").inner_text()
    if card.locator(".pacing-ctx").count():
        assert "after all variable costs" in card.locator(".pacing-ctx").inner_text().lower()
    ctx.close()
