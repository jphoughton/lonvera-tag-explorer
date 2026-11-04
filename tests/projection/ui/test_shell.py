"""Shell: nav, routing, scenario flows, headline, history drawer, 401 / 429 handling, mobile layout."""
import re

import pytest
from playwright.sync_api import expect

from conftest import SHOTS, cell, hook, mock_state, open_module

BUILT = {"cohorts", "drivers"}
ALL = ["cohorts", "drivers", "pnl", "inventory_demand", "inventory_supply", "financing", "pacing"]



def act(page, name):
    """Scenario actions live in the "..." menu at the right end of the bar: open it, then pick the item."""
    page.click("#pg-menu-btn")
    page.click(f'#pg-menu-pop [data-act="{name}"]')


def test_nav_renders_seven_modules_with_built_state(page):
    page.goto("/projection")
    page.wait_for_selector("a.pg-tab[data-key]")
    tabs = page.locator("a.pg-tab[data-key]:not([data-key=''])")
    assert tabs.count() == 7
    for key in ALL:
        t = page.locator(f'a.pg-tab[data-key="{key}"]')
        assert t.get_attribute("data-built") == ("true" if key in BUILT else "false"), key
    # grouped P1 / P2 / P3
    assert [g.get_attribute("data-group") for g in page.locator(".pg-ngroup[data-group]").all()] == ["P1", "P2", "P3"]
    assert page.locator("a.pg-tab[data-built=false] .soon").count() == 5
    # overview cards: 2 open, 5 not built
    assert page.locator('a.pg-mcard[data-built="true"]').count() == 2
    assert page.locator('div.pg-mcard[data-built="false"]').count() == 5
    assert page.locator('meta[name="robots"]').get_attribute("content") == "noindex, nofollow"
    page.screenshot(path=str(SHOTS / "overview.png"))


def test_nav_link_pushes_url_and_keeps_scenario(page):
    page.goto("/projection")
    page.wait_for_selector("a.pg-tab[data-key='drivers']")
    page.click("a.pg-tab[data-key='drivers']")
    page.wait_for_selector(".pg-grid .pg-table")
    assert page.url.endswith("/projection/drivers?scenario=current")
    page.go_back()
    page.wait_for_selector(".pg-mcard")
    assert "/projection" in page.url and "/drivers" not in page.url
    page.go_forward()
    page.wait_for_selector(".pg-grid .pg-table")


def test_deep_links_and_not_built_and_not_found(page):
    open_module(page, "drivers", "?scenario=current")
    assert page.locator("h1").inner_text() == "Drivers"
    page.goto("/projection/inventory-demand")
    expect(page.locator('[data-state="not-built"]')).to_contain_text("Not built yet")
    page.goto("/projection/inventory_demand")                     # underscore spelling resolves too
    expect(page.locator('[data-state="not-built"]')).to_be_visible()
    page.goto("/projection/nope")
    expect(page.locator("h1")).to_have_text("Not found")
    page.goto("/projection/_demo")                               # demo only with ?demo=1
    expect(page.locator("h1")).to_have_text("Not found")
    page.goto("/projection/_demo?demo=1")
    page.wait_for_selector('[data-testid="demo"] .pg-table')
    page.wait_for_function("document.body.dataset.pgState === 'ready'")
    assert page.locator("#demo-chart").count() == 1


def test_headline_strip(page):
    open_module(page, "drivers")
    expect(page.locator("#pg-actual-through")).to_have_text("Actual through Oct 2026")
    labels = page.locator(".pg-kpi .k").all_inner_texts()
    assert [x.upper() for x in labels] == ["FY27 REVENUE", "FY27 NOI", "IMPLIED EV", "LIFETIME REPEAT", "LOWEST ENDING CASH · MAR 2027"]
    values = page.locator(".pg-kpi .v").all_inner_texts()
    assert values[3] == "1.86x" and values[4] == "$154K" and " to " in values[2]


def test_headline_accepts_array_shape(page):
    body = '{"actual_through":"2026-10-01","headline":[{"key":"orders","label":"Orders","value":1234,"fmt":"num"}]}'
    page.route("**/api/projection/run*", lambda r: r.fulfill(status=200, content_type="application/json", body=body))
    open_module(page, "drivers")
    expect(page.locator("#pg-actual-through")).to_have_text("Actual through Oct 2026")
    assert page.locator(".pg-kpi .v").all_inner_texts() == ["1,234"]


def test_headline_hidden_when_run_has_nothing(page):
    page.route("**/api/projection/run*", lambda r: r.fulfill(status=200, content_type="application/json", body="{}"))
    open_module(page, "drivers")
    assert page.locator("#pg-headline").is_hidden()


def test_scenario_new_clone_rename_delete(page, base_url):
    open_module(page, "drivers")
    sel = page.locator("#pg-scenario")
    assert sel.input_value() == "current"
    expect(page.locator("#pg-version")).to_have_text("v1")
    assert page.locator('[data-act="delete"]').is_hidden()          # default scenario cannot be deleted

    # New
    act(page, "new")
    page.fill("dialog input", "Bear case")
    page.click("dialog button[type=submit]")
    page.wait_for_function("document.querySelector('#pg-scenario').value === 'bear-case'")
    assert "scenario=bear-case" in page.url
    page.wait_for_selector(".pg-grid .pg-table")
    expect(page.locator(".pg-title .sub")).to_have_text("Bear case")

    # edit in the new scenario goes to its slug
    cell(page, "spend", 23).click()
    page.keyboard.type("123k")
    with page.expect_request(lambda r: r.method == "PUT" and "/series" in r.url) as req:
        page.keyboard.press("Enter")
    assert req.value.post_data_json["scenario"] == "bear-case"
    expect(page.locator(".pg-gstatus")).to_contain_text("Saved 1 cell")

    # Clone
    act(page, "clone")
    assert page.locator("dialog input").input_value() == "Bear case copy"
    page.fill("dialog input", "Bear case B")
    page.click("dialog button[type=submit]")
    page.wait_for_function("document.querySelector('#pg-scenario').value === 'bear-case-b'")
    page.wait_for_selector(".pg-grid .pg-table")
    assert page.locator('tr[data-key="spend"] td[data-c="23"]').inner_text().startswith("$123,000")   # clone carries the edit
    scn = {s["slug"]: s for s in mock_state(base_url)["scenarios"]}
    assert scn["bear-case-b"]["base_slug"] == "bear-case"

    # Rename
    act(page, "rename")
    page.fill("dialog input", "Bear case B2")
    page.click("dialog button[type=submit]")
    page.wait_for_function("[...document.querySelectorAll('#pg-scenario option')].some(o => o.textContent === 'Bear case B2')")

    # Delete needs the slug typed back
    act(page, "delete")
    ok = page.locator("dialog button.danger")
    assert ok.is_disabled()
    page.fill("dialog input", "wrong")
    assert ok.is_disabled()
    page.fill("dialog input", "bear-case-b")
    assert ok.is_enabled()
    with page.expect_request(lambda r: r.method == "DELETE") as req:
        ok.click()
    assert req.value.post_data_json == {"confirm": "bear-case-b"}
    page.wait_for_function("document.querySelector('#pg-scenario').value === 'current'")
    assert "bear-case-b" not in [s["slug"] for s in mock_state(base_url)["scenarios"]]
    assert "scenario=current" in page.url


@pytest.mark.allow_errors
def test_scenario_duplicate_name_error_stays_in_dialog(page):
    open_module(page, "drivers")
    act(page, "new")
    page.fill("dialog input", "current plan")
    page.click("dialog button[type=submit]")
    expect(page.locator("dialog .pg-dialog-err")).to_contain_text("already exists")
    assert page.locator("dialog").count() == 1
    page.click("dialog button:has-text('Cancel')")
    assert page.locator("dialog").count() == 0


def test_scenario_switch_rerenders_module(page):
    open_module(page, "drivers")
    cell(page, "spend", 23).click()
    page.keyboard.type("500k")
    page.keyboard.press("Enter")
    expect(page.locator(".pg-gstatus")).to_contain_text("Saved")
    act(page, "clone")
    page.click("dialog button[type=submit]")
    page.wait_for_function("document.querySelector('#pg-scenario').value === 'current-plan-copy'")
    page.wait_for_selector(".pg-grid .pg-table")
    page.select_option("#pg-scenario", "current")
    page.wait_for_function("document.querySelector('.pg-title .sub').textContent === 'Current plan'")
    assert cell(page, "spend", 23).inner_text().startswith("$500,000")


def test_history_drawer_and_restore(page, base_url):
    open_module(page, "drivers")
    cell(page, "spend", 23).click()
    page.keyboard.type("400k")
    page.keyboard.press("Enter")
    expect(page.locator("#pg-version")).to_have_text("v2")
    act(page, "history")
    page.wait_for_selector("#pg-hist li")
    items = page.locator("#pg-hist li")
    assert items.count() == 2
    assert "Edited inputs" in items.first.inner_text() and "spend Dec 2026: 350,000 → 400,000" in items.first.inner_text()   # audit row attached to its version
    assert items.nth(1).inner_text().count("Seeded") == 1
    assert page.locator("#pg-hist li").first.locator(".vchip.now").count() == 1       # newest = current version
    assert page.locator("#pg-hist li").first.locator("button").count() == 0
    page.screenshot(path=str(SHOTS / "history.png"))
    # restore v1 behind a confirm dialog
    items.nth(1).locator("button:has-text('Restore')").click()
    expect(page.locator("dialog h2")).to_contain_text("Restore version 1")
    with page.expect_request(lambda r: r.method == "POST" and r.url.endswith("/scenarios/current/restore")) as req:
        page.click("dialog button.primary")
    assert req.value.post_data_json == {"version": 1}
    expect(page.locator("#pg-version")).to_have_text("v3")
    page.wait_for_selector(".pg-grid .pg-table")
    assert cell(page, "spend", 23).inner_text().startswith("$350,000")                 # grid re-rendered with restored values
    assert page.locator("#pg-hist li").count() == 3
    page.keyboard.press("Escape")
    assert page.locator("#pg-drawer").is_hidden()


def test_history_restore_cancel_sends_nothing(page, base_url):
    open_module(page, "drivers")
    cell(page, "spend", 23).click()
    page.keyboard.type("400k")
    page.keyboard.press("Enter")
    expect(page.locator("#pg-version")).to_have_text("v2")
    act(page, "history")
    page.locator("#pg-hist li").nth(1).locator("button:has-text('Restore')").click()
    page.click("dialog button:has-text('Cancel')")
    expect(page.locator("#pg-version")).to_have_text("v2")


@pytest.mark.allow_errors
def test_rate_limited_message(page, base_url):
    open_module(page, "drivers")
    hook(base_url, "rate_limit", {"n": 1})
    cell(page, "spend", 23).click()
    page.keyboard.type("400k")
    page.keyboard.press("Enter")
    expect(page.locator(".pg-gstatus")).to_contain_text("Too many changes in a short time")
    expect(page.locator(".pg-toast.error")).to_contain_text("Wait a minute")
    assert cell(page, "spend", 23).inner_text().startswith("$350,000")             # reverted
    assert "pg-error" in cell(page, "spend", 23).get_attribute("class")


@pytest.mark.allow_errors
def test_401_goes_to_tag_explorer_login_and_back(page, base_url):
    hook(base_url, "app_auth", {"on": True})
    page.goto("/projection/drivers?scenario=current")
    page.wait_for_url(re.compile(r"/login\?next="))
    assert "next=%2Fprojection%2Fdrivers%3Fscenario%3Dcurrent" in page.url
    page.click("#login-go")
    page.wait_for_url(re.compile(r"/projection/drivers"))
    page.wait_for_selector(".pg-grid .pg-table")


@pytest.mark.allow_errors
def test_401_mid_session(page, base_url):
    open_module(page, "drivers")
    hook(base_url, "app_auth", {"on": True})
    cell(page, "spend", 23).click()
    page.keyboard.type("400k")
    page.keyboard.press("Enter")
    page.wait_for_url(re.compile(r"/login\?next="))


@pytest.mark.allow_errors
def test_module_script_failure_shows_error_with_retry(page):
    page.route("**/js/projection/_mockpage.js", lambda r: r.fulfill(status=404, body="nope"))
    page.goto("/projection/drivers")
    expect(page.locator(".errorbox")).to_contain_text("failed to load")
    assert page.locator(".errorbox button:has-text('Retry')").count() == 1


@pytest.mark.allow_errors
def test_api_error_state_on_module_table(page):
    page.route("**/api/projection/drivers/table*", lambda r: r.fulfill(status=500, content_type="application/json", body='{"error":"engine failed"}'))
    page.goto("/projection/drivers")
    expect(page.locator(".errorbox")).to_contain_text("engine failed")


def test_mobile_390_no_page_overflow_table_scrolls_inside(mobile_page):
    p = mobile_page
    p.goto("/projection")
    p.wait_for_selector(".pg-mcard")
    assert p.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    p.screenshot(path=str(SHOTS / "mobile-overview.png"))
    p.goto("/projection/drivers")
    p.wait_for_selector(".pg-grid .pg-table")
    p.wait_for_function("document.body.dataset.pgState === 'ready'")
    assert p.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth"), "page scrolls horizontally"
    assert p.evaluate("(() => { const s = document.querySelector('.pg-scroll'); return s.scrollWidth > s.clientWidth + 200; })()")
    # sticky label column stays in view while the table scrolls
    p.evaluate("document.querySelector('.pg-scroll').scrollLeft = 900")
    left = p.evaluate("(() => { const s = document.querySelector('.pg-scroll').getBoundingClientRect().left; const l = document.querySelector('tr[data-key=spend] th.pg-label').getBoundingClientRect().left; return l - s; })()")
    assert abs(left) < 2
    p.screenshot(path=str(SHOTS / "mobile-drivers.png"))
    p.goto("/projection/_demo?demo=1")
    p.wait_for_selector(".pg-grid .pg-table")
    assert p.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")


# ---------------------------------------------------------------- one bar (Lonvera | Tags | Projections | module tabs | scenario control)
def test_single_bar_structure(page):
    open_module(page, "drivers")
    bar = page.locator("#topbar .topbar-in")
    assert page.locator("#topbar .brand").inner_text() == "Lonvera"
    assert page.locator("#topbar a.navlink").all_inner_texts() == ["Tags", "Projections"]
    # tabs and the scenario control live inside the same bar, in that order
    assert bar.locator("#pg-nav").count() == 1 and bar.locator("#pg-bar").count() == 1
    order = page.evaluate("[...document.querySelectorAll('#topbar .topbar-in > *')].map(e => e.id || e.className.split(' ')[0])")
    assert order[:3] == ["brand", "navlink", "navlink"] and order.index("pg-nav") < order.index("pg-bar")
    assert page.locator(".pg-ghead").count() == 0                                  # group labels are gone from the bar
    assert page.locator("#pg-chrome").evaluate("e => e.getBoundingClientRect().height") <= 48
    assert page.locator("#topbar").evaluate("e => getComputedStyle(e).position") in ("static", "sticky")
    assert page.locator("#pg-chrome").evaluate("e => getComputedStyle(e).position") == "sticky"
    expect(page.locator("a.pg-tab[aria-current=page]")).to_have_text("Drivers")


def test_unbuilt_tabs_are_quiet_and_tagged(page):
    open_module(page, "drivers")
    built = page.locator('a.pg-tab[data-built="true"]:not([data-key=""])')
    assert built.count() == 2 and built.locator(".soon").count() == 0
    assert page.locator('a.pg-tab[data-built="false"] .soon').count() == 5


def test_scenario_menu_keyboard_and_copy_cue(page):
    open_module(page, "drivers")
    assert page.locator("body").get_attribute("data-pg-scenario") == "default"
    assert page.locator("#pg-mode").is_hidden()                                    # no cue on the plan of record
    page.focus("#pg-menu-btn")
    page.keyboard.press("Enter")
    expect(page.locator("#pg-menu-pop")).to_be_visible()
    assert page.evaluate("document.activeElement.dataset.act") == "new"           # focus moves into the menu
    page.keyboard.press("ArrowDown")
    assert page.evaluate("document.activeElement.dataset.act") == "clone"
    page.keyboard.press("Escape")
    expect(page.locator("#pg-menu-pop")).to_be_hidden()
    assert page.evaluate("document.activeElement.id") == "pg-menu-btn"             # focus returns to the button
    act(page, "new")
    page.fill(".pg-dialog input", "Cue test")
    page.click(".pg-dialog .btn.primary")
    page.wait_for_function("document.body.dataset.pgScenario === 'copy'")
    expect(page.locator("#pg-mode")).to_be_visible()
    expect(page.locator("#pg-mode")).to_have_text("Copy")


@pytest.mark.parametrize("width", [1440, 1024, 768, 390])
def test_bar_is_one_row_at_every_width(browser, base_url, width):
    ctx = browser.new_context(viewport={"width": width, "height": 800}, base_url=base_url, is_mobile=width < 500, has_touch=width < 500)
    p = ctx.new_page()
    try:
        p.goto("/projection/drivers")
        p.wait_for_selector(".pg-grid .pg-table")
        assert p.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
        h = p.locator("#pg-chrome").evaluate("e => e.getBoundingClientRect().height")
        assert h <= 48, f"bar is {h}px tall at {width}px"
        # the scenario control stays reachable and inside the viewport
        box = p.locator("#pg-menu-btn").bounding_box()
        assert box and box["x"] + box["width"] <= width and box["y"] >= 0
        sel = p.locator("#pg-scenario").bounding_box()
        assert sel and sel["x"] + sel["width"] <= width
        if width <= 900:
            assert p.locator("#pg-nav").is_hidden()
            p.click("#pg-navbtn")
            assert p.locator('#pg-nav-pop a[data-key="drivers"]').is_visible()
            p.click('#pg-nav-pop a[data-key="cohorts"]')
            p.wait_for_function("location.pathname === '/projection/cohorts'")
            assert p.locator("#pg-nav-pop").is_hidden()
        else:
            assert p.locator("#pg-nav").is_visible() and p.locator("#pg-navmenu").is_hidden()
    finally:
        ctx.close()


def test_headline_hidden_when_all_zero(page):
    body = '{"actual_through":"2026-10-01","headline":{"fy":{"2027":{"revenue":0,"noi":0}},"ev":{"3x":0,"8x":0},"lifetime_multiple":0}}'
    page.route("**/api/projection/run*", lambda r: r.fulfill(status=200, content_type="application/json", body=body))
    open_module(page, "drivers")
    page.wait_for_timeout(300)
    assert page.locator("#pg-headline").is_hidden()
    assert page.locator(".pg-kpi").count() == 0


def test_document_titles(page):
    page.goto("/projection")
    page.wait_for_selector(".pg-mcard")
    assert page.title() == "Projections · Lonvera"
    open_module(page, "drivers")
    assert page.title() == "Drivers · Projections · Lonvera"
