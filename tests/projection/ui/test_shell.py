"""Shell: nav, routing, scenario flows, headline, history drawer, 401 / 429 handling, mobile layout."""
import re
from pathlib import Path

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
    expect(page.locator("#pg-actual-through")).to_have_text("Books through Oct 2026 \u00b7 Feeds through Nov 2, 2026")
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
    assert "Edited inputs" in items.first.inner_text() and "Spend, Dec 2026: $350,000 to $400,000" in items.first.inner_text()   # audit row attached to its version
    assert items.nth(1).inner_text().count("Seeded") == 1
    assert page.locator("#pg-hist li").first.locator(".vchip.now").count() == 1       # newest = current version
    assert page.locator("#pg-hist li").first.locator("button").count() == 0
    page.screenshot(path=str(SHOTS / "history.png"))
    # restore v1 behind a confirm dialog
    items.nth(1).locator("button:has-text('Restore')").click()
    expect(page.locator("dialog h2")).to_contain_text("Restore version 1")
    with page.expect_request(lambda r: r.method == "POST" and r.url.endswith("/scenarios/current/restore")) as req:
        page.click("dialog button.primary")
    body = req.value.post_data_json
    assert body["version"] == 1 and "expected_version" in body                    # restore carries the version it was made against
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


@pytest.mark.allow_errors
def test_error_codes_are_shown_as_plain_words(page):
    page.route("**/api/projection/drivers/table*", lambda r: r.fulfill(status=500, content_type="application/json", body='{"error":"internal_error"}'))
    page.goto("/projection/drivers")
    box = page.locator(".errorbox")
    expect(box).to_contain_text("The server hit a problem.")
    expect(box).to_contain_text("Your edits are safe.")
    assert "internal_error" not in box.inner_text()


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
    expect(page.locator("#pg-mode")).to_contain_text("Plan of record")             # calm chip on the plan of record
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


# ---------------------------------------------------------------- Pacing is the default page when it is built
def test_default_lands_on_pacing_when_built(page, base_url):
    hook(base_url, "built", {"keys": ["pacing"]})
    page.goto("/projection")
    page.wait_for_function("location.pathname === '/projection/pacing'")
    page.wait_for_function("document.body.dataset.pgState === 'ready'")
    expect(page.locator("a.pg-tab[aria-current=page]")).to_have_text("Pacing")
    assert page.title() == "Pacing · Projections · Lonvera"
    # the scenario query survives the redirect
    page.goto("/projection?scenario=current")
    page.wait_for_function("location.pathname === '/projection/pacing'")
    assert "scenario=current" in page.url


def test_overview_lives_at_projection_overview(page, base_url):
    hook(base_url, "built", {"keys": ["pacing"]})
    page.goto("/projection/overview")
    page.wait_for_selector(".pg-mcard")
    assert page.url.split("?")[0].endswith("/projection/overview")
    assert page.title() == "Projections · Lonvera"
    expect(page.locator("a.pg-tab[aria-current=page]")).to_have_text("Overview")
    # a click on the Pacing tab and back keeps the two apart
    page.click("a.pg-tab[data-key='pacing']")
    page.wait_for_function("location.pathname === '/projection/pacing'")
    page.click("a.pg-tab[data-key='']")
    page.wait_for_function("location.pathname === '/projection/overview'")
    page.wait_for_selector(".pg-mcard")


def test_tab_order_pacing_overview_then_the_rest(page, base_url):
    hook(base_url, "built", {"keys": ["pacing"]})
    page.goto("/projection/overview")
    page.wait_for_selector("a.pg-tab[data-key]")
    keys = page.locator("#pg-nav a.pg-tab").evaluate_all("els => els.map(e => e.dataset.key)")
    assert keys == ["pacing", "", "cohorts", "drivers", "pnl", "inventory_demand", "inventory_supply", "financing"]
    # the narrow-screen menu lists the same order
    menu = page.locator("#pg-nav-pop a.pg-mitem").evaluate_all("els => els.map(e => e.dataset.key)")
    assert menu == keys
    # Pacing is not marked "soon", and not a duplicate of the P3 group
    assert page.locator("#pg-nav a.pg-tab[data-key='pacing'] .soon").count() == 0
    assert page.locator("#pg-nav a.pg-tab[data-key='pacing']").count() == 1


def test_tab_order_when_pacing_is_not_built(page):
    page.goto("/projection")
    page.wait_for_selector("a.pg-tab[data-key]")
    keys = page.locator("#pg-nav a.pg-tab").evaluate_all("els => els.map(e => e.dataset.key)")
    assert keys[0] == "" and keys[-1] == "pacing"          # no redirect, Overview is first, Pacing sits in its group as "soon"
    assert page.url.split("?")[0].endswith("/projection")


# ---------------------------------------------------------------- the headline says what changed
def test_headline_shows_was_after_an_edit_and_difference_in_a_copy(page):
    open_module(page, "drivers")
    page.wait_for_selector("#pg-headline .pg-kpi")
    assert page.locator("#pg-headline .pg-kpi .d").count() == 0                     # nothing moved yet
    cell(page, "spend", 28).click()
    page.keyboard.type("900k")
    page.keyboard.press("Enter")
    page.wait_for_selector("#pg-headline .pg-kpi .d", timeout=8000)
    assert "was " in page.locator("#pg-headline .pg-kpi .d").first.inner_text()      # plan of record: what it was before the edit
    act(page, "clone")
    page.fill("dialog input", "Copy for delta")
    page.click("dialog button[type=submit]")
    page.wait_for_function("document.body.dataset.pgScenario === 'copy'")
    page.wait_for_selector("#pg-headline .pg-kpi")
    page.wait_for_function("[...document.querySelectorAll('#pg-headline .pg-kpi .d')].every(e => /same as Current plan/.test(e.textContent))")   # a fresh clone equals its base
    cell(page, "spend", 28).click()
    page.keyboard.type("100k")
    page.keyboard.press("Enter")
    page.wait_for_function("[...document.querySelectorAll('#pg-headline .pg-kpi .d')].some(e => /[+-].* vs Current plan/.test(e.textContent))", timeout=8000)


def test_active_tab_is_scrolled_into_view_in_a_narrow_bar(browser, base_url):
    ctx = browser.new_context(viewport={"width": 1000, "height": 800}, base_url=base_url)
    p = ctx.new_page()
    try:
        hook(base_url, "built", {"keys": ["pacing"]})
        p.goto("/projection/financing")                                  # a tab far to the right
        p.wait_for_selector("#pg-nav a.pg-tab[aria-current=page]")
        p.wait_for_timeout(300)
        box = p.locator("#pg-nav a.pg-tab[aria-current=page]").bounding_box()
        nav = p.locator("#pg-nav").bounding_box()
        assert box["x"] >= nav["x"] - 1 and box["x"] + box["width"] <= nav["x"] + nav["width"] + 1, "the active tab is cut off"
    finally:
        ctx.close()


def test_toast_with_an_action_button(page):
    open_module(page, "drivers")
    page.evaluate("""async () => {
      const { toast } = await import('/static/js/projection/lib/util.js');
      window.__undone = 0;
      toast('Saved 36 changes to Marketing spend', { kind: 'ok', action: { label: 'Undo', onClick: () => { window.__undone += 1; } } });
    }""")
    expect(page.locator(".pg-toast .pg-toast-act")).to_have_text("Undo")
    page.click(".pg-toast .pg-toast-act")
    assert page.evaluate("window.__undone") == 1
    expect(page.locator(".pg-toast")).to_have_count(0)


# ---------------------------------------------------------------- the plan of record: chip, strip, first-edit choice, quick restore
ASK = ".pg-por-ask"


def edit_cell(page, row="spend", col=28, text="123k"):
    cell(page, row, col).click()
    page.keyboard.type(text)
    page.keyboard.press("Enter")


def put_series(base_url):
    return [p for p in mock_state(base_url)["puts"] if "items" in p]


def test_plan_of_record_chip_and_grid_strip(page):
    open_module(page, "drivers")
    chip = page.locator("#pg-mode")
    expect(chip).to_contain_text("Plan of record")
    assert "pacing goals" in chip.get_attribute("title")
    expect(chip.locator(".age")).to_have_text(" · no edits yet")
    strip = page.locator(".pg-por")
    expect(strip).to_be_visible()
    assert "pacing goals" in strip.inner_text() and "default view" in strip.inner_text()
    # the strip sits right above the grid toolbar
    order = page.evaluate("[...document.querySelectorAll('.pg-grid > *')].map(e => e.className.split(' ')[0])")
    assert order[:2] == ["pg-por", "pg-toolbar"]
    # an edit updates the age in the chip
    edit_cell(page)
    page.wait_for_function("document.querySelector('#pg-mode .age').textContent.includes('just now')")
    # a copy has no plan-of-record chip or strip
    act(page, "clone")
    page.fill("dialog input", "Strip test")
    page.click("dialog button[type=submit]")
    page.wait_for_function("document.body.dataset.pgScenario === 'copy'")
    page.wait_for_selector(".pg-grid .pg-table")
    expect(page.locator("#pg-mode")).to_have_text("Copy")
    assert page.locator(".pg-por").count() == 0


def test_first_edit_asks_then_remembers(fresh_page, base_url):
    p = fresh_page
    open_module(p, "drivers")
    edit_cell(p)
    ask = p.locator(ASK)
    expect(ask).to_be_visible()
    assert ask.get_attribute("role") == "alertdialog"
    expect(ask.locator("button.primary")).to_have_text("Make a copy and edit it")
    expect(ask.locator("[data-choice=edit]")).to_have_text("Edit the plan of record")
    assert p.evaluate("document.activeElement.dataset.choice") == "copy"            # the safe choice has focus
    assert put_series(base_url) == []                                                # nothing is sent until the choice is made
    ask.locator("[data-choice=edit]").click()
    expect(p.locator(".pg-gstatus")).to_contain_text("Saved 1 cell")
    assert put_series(base_url)[-1]["scenario"] == "current"
    assert p.locator(ASK).count() == 0
    edit_cell(p, col=29, text="200k")                                                 # remembered: no second question
    expect(p.locator(".pg-gstatus")).to_contain_text("Saved 1 cell")
    assert p.locator(ASK).count() == 0
    p.reload()                                                                        # and it survives a reload of the same tab
    p.wait_for_selector(".pg-grid .pg-table")
    edit_cell(p, col=30, text="210k")
    expect(p.locator(".pg-gstatus")).to_contain_text("Saved 1 cell")
    assert p.locator(ASK).count() == 0


def test_first_edit_make_a_copy_applies_the_edit_there(fresh_page, base_url):
    p = fresh_page
    open_module(p, "drivers")
    old = cell(p, "spend", 28).inner_text()
    edit_cell(p, text="777k")
    p.locator(f"{ASK} [data-choice=copy]").click()
    p.wait_for_function("document.body.dataset.pgScenario === 'copy'")
    p.wait_for_selector(".pg-grid .pg-table")
    p.wait_for_function("document.querySelector('tr[data-key=\"spend\"] td[data-c=\"28\"]').innerText.startsWith('$777,000')")
    state = mock_state(base_url)
    copies = [s for s in state["scenarios"] if s["slug"] != "current"]
    assert len(copies) == 1 and copies[0]["name"].startswith("What-if") and copies[0]["base_slug"] == "current"
    assert put_series(base_url)[-1]["scenario"] == copies[0]["slug"]                  # the edit went to the copy, not to the plan of record
    assert not [x for x in put_series(base_url) if x["scenario"] == "current"]
    expect(p.locator(".pg-toast").first).to_contain_text("applied your edit there")
    p.select_option("#pg-scenario", "current")                                        # the plan of record is untouched
    p.wait_for_function("document.body.dataset.pgScenario === 'default'")
    p.wait_for_selector(".pg-grid .pg-table")
    assert cell(p, "spend", 28).inner_text() == old


def test_cancelling_the_choice_changes_nothing(fresh_page, base_url):
    p = fresh_page
    open_module(p, "drivers")
    old = cell(p, "spend", 28).inner_text()
    edit_cell(p)
    expect(p.locator(ASK)).to_be_visible()
    p.keyboard.press("Escape")
    expect(p.locator(ASK)).to_have_count(0)
    p.wait_for_function(f"document.querySelector('tr[data-key=\"spend\"] td[data-c=\"28\"]').innerText === {old!r}")
    assert put_series(base_url) == []
    expect(p.locator(".pg-gstatus")).to_have_text("No change made")
    assert p.locator(".pg-toast.error").count() == 0                                   # quiet: no error toast


def test_deleting_a_cell_asks_too(fresh_page, base_url):
    p = fresh_page
    open_module(p, "drivers")
    cell(p, "spend", 28).click()
    p.keyboard.press("Escape")                                                        # a click opens the editor; Escape leaves the cell selected
    p.keyboard.press("Delete")
    expect(p.locator(ASK)).to_be_visible()
    p.locator(f"{ASK} [data-choice=cancel]").click()
    assert put_series(base_url) == []


def test_bulk_writes_ask_but_previews_and_shared_facts_do_not(fresh_page):
    p = fresh_page
    for pat in ("**/api/projection/drivers/patterns", "**/api/projection/inventory-supply/po*", "**/api/projection/cohorts/goal"):
        p.route(pat, lambda r: r.fulfill(status=200, content_type="application/json", body='{"ok":true}'))
    open_module(p, "drivers")
    call = """async (args) => { const a = await import('/static/js/projection/lib/api.js'); try { await a.post(args[0], args[1]); return 'sent'; } catch (e) { return e.name; } }"""
    assert p.evaluate(call, ["drivers/patterns", {"scenario": "current", "preview": True}]) == "sent"             # a preview writes nothing
    assert p.evaluate(call, ["inventory-supply/po?scenario=current", {"units": 1}]) == "sent"                     # purchase orders are shared by every scenario
    assert p.evaluate(call, ["cohorts/goal", {"scenario": "current", "from": "live"}]) == "sent"                  # so is the saved Goal curve
    assert p.locator(ASK).count() == 0
    p.evaluate("""() => { import('/static/js/projection/lib/api.js').then((a) => { window.__bulk = a.post('drivers/patterns', {scenario: 'current', target: 'spend', pattern: {kind: 'flat'}}).then(() => 'sent', (e) => e.name); }); }""")
    expect(p.locator(ASK)).to_be_visible()                                                                          # a bulk fill asks like any edit
    p.keyboard.press("Escape")
    p.wait_for_function("window.__bulk")
    assert p.evaluate("window.__bulk") == "EditCancelled"


def test_history_has_a_one_click_restore_for_the_plan_of_record(page):
    open_module(page, "drivers")
    v0 = cell(page, "spend", 28).inner_text()
    edit_cell(page, text="555k")
    expect(page.locator(".pg-gstatus")).to_contain_text("Saved 1 cell")
    page.wait_for_function("document.querySelector('#pg-version').textContent === 'v2'")
    act(page, "history")
    quick = page.locator("#pg-por-restore")
    expect(quick).to_be_visible()
    btn = quick.locator("button[data-act=restore-session]")
    assert btn.inner_text().startswith("Restore plan of record to v1")
    btn.click()
    page.locator("dialog[open] button", has_text="Restore").last.click()
    page.wait_for_function("document.querySelector('#pg-version').textContent === 'v3'")
    page.wait_for_function(f"document.querySelector('tr[data-key=\"spend\"] td[data-c=\"28\"]').innerText === {v0!r}")


# ---------------------------------------------------------------- versioned writes: a stale tab cannot silently overwrite
def test_writes_carry_expected_version_and_follow_the_version(page, base_url):
    open_module(page, "drivers")
    edit_cell(page, text="111k")
    expect(page.locator(".pg-gstatus")).to_contain_text("Saved 1 cell")
    edit_cell(page, col=29, text="222k")
    expect(page.locator(".pg-gstatus")).to_contain_text("Saved 1 cell")
    puts = put_series(base_url)
    assert [x["expected_version"] for x in puts] == [1, 2]                            # each write expects the version the previous one produced


@pytest.mark.allow_errors
def test_stale_tab_gets_reload_or_overwrite(page, base_url):
    open_module(page, "drivers")
    old = cell(page, "spend", 28).inner_text()
    hook(base_url, "bump", {"scenario": "current"})                                   # another tab changed the plan: the server is at v2, this tab thinks v1
    edit_cell(page, text="321k")
    ask = page.locator(".pg-conflict")
    expect(ask).to_be_visible()
    assert "changed in another tab or window (v1 to v2)" in ask.inner_text()
    assert "Reload" in ask.locator("button.primary").inner_text()
    ask.locator("[data-choice=reload]").click()
    expect(page.locator(".pg-conflict")).to_have_count(0)
    page.wait_for_function("document.querySelector('#pg-version').textContent === 'v2'")           # reloaded: the tab now sees v2
    page.wait_for_function(f"document.querySelector('tr[data-key=\"spend\"] td[data-c=\"28\"]').innerText === {old!r}")
    assert all(x.get("expected_version") != 2 or x["items"] for x in put_series(base_url))
    # a second stale edit, this time Overwrite
    hook(base_url, "bump", {"scenario": "current"})                                   # server v3, tab thinks v2
    edit_cell(page, text="654k")
    page.locator(".pg-conflict [data-choice=overwrite]").click()
    expect(page.locator(".pg-gstatus")).to_contain_text("Saved 1 cell")
    last = put_series(base_url)[-1]
    assert last["expected_version"] == 3 and last["items"][0]["value"] == 654000        # resent on top of the other tab's version
    page.wait_for_function("document.querySelector('#pg-version').textContent === 'v4'")


# ---------------------------------------------------------------- degraded modules
def test_degraded_modules_show_one_plain_banner(page, base_url):
    hook(base_url, "degraded", {"modules": [{"module": "pnl", "error": "The P&L refresh failed at 21:30"}]})
    open_module(page, "drivers")
    banner = page.locator("#pg-degraded")
    expect(banner).to_be_visible()
    text = banner.inner_text()
    assert "failed to load" in text and "numbers that depend on it are not shown" in text
    assert "The P&L refresh failed at 21:30" in text
    page.goto("/projection/overview")                                                 # on every page
    expect(page.locator("#pg-degraded")).to_be_visible()
    hook(base_url, "degraded", {"modules": []})
    page.reload()
    page.wait_for_selector(".pg-mcard")
    expect(page.locator("#pg-degraded")).to_be_hidden()


@pytest.mark.allow_errors
def test_503_upstream_degraded_becomes_the_banner_not_an_empty_page(page, base_url):
    hook(base_url, "degraded", {"modules": [], "table_503": True})
    page.goto("/projection/drivers")
    page.wait_for_function("document.body.dataset.pgState === 'ready'")
    expect(page.locator("#pg-degraded")).to_contain_text("failed to load")
    card = page.locator(".pg-degraded-card")
    expect(card).to_contain_text("These numbers are not shown")
    assert page.locator(".errorbox").count() == 0 and page.locator(".pg-grid").count() == 0     # no generic error, no zero-filled grid


# ---------------------------------------------------------------- input limits: known up front, and learned from the server's message
def test_negative_forms_are_refused_in_the_cell_before_anything_is_sent(page, base_url):
    open_module(page, "drivers")
    n = len(put_series(base_url))
    for text in ("(500000)", "-350k"):
        cell(page, "spend", 28).click()
        page.keyboard.press("ControlOrMeta+a")
        page.keyboard.type(text)
        page.keyboard.press("Enter")
        ed = page.locator("td.pg-editing input.pg-editor")
        expect(ed).to_be_visible()                                                    # the editor stays open, marked invalid, with the reason
        assert "invalid" in ed.get_attribute("class")
        expect(page.locator(".pg-gstatus")).to_have_text("Enter 0 or more. This line cannot be negative.")
        page.keyboard.press("Escape")
    cell(page, "spend", 28).click()
    page.keyboard.press("ControlOrMeta+a")
    page.keyboard.type("1e12")                                                         # above the server ceiling for any value
    page.keyboard.press("Enter")
    expect(page.locator(".pg-gstatus")).to_have_text("Enter $99,999,999,999 or less.")
    page.keyboard.press("Escape")
    assert len(put_series(base_url)) == n                                              # nothing was sent
    cell(page, "spend", 28).click()
    page.keyboard.press("ControlOrMeta+a")
    page.keyboard.type("360k")
    page.keyboard.press("Enter")
    expect(page.locator(".pg-gstatus")).to_contain_text("Saved 1 cell")


@pytest.mark.allow_errors
def test_out_of_range_input_is_stopped_in_the_cell_after_the_server_says_so(page, base_url):
    open_module(page, "drivers")
    old = cell(page, "amazon_rev", 28).inner_text()
    edit_cell(page, row="amazon_rev", text="9m")                                       # a limit only the server knows: it refuses, the cell goes back, the line is learned
    expect(page.locator(".pg-gstatus")).to_contain_text("Not saved: value must be at most 5,000,000")
    page.wait_for_function(f"document.querySelector('tr[data-key=\"amazon_rev\"] td[data-c=\"28\"]').innerText === {old!r}")
    n = len(put_series(base_url))
    cell(page, "amazon_rev", 28).click()
    page.keyboard.press("ControlOrMeta+a")
    page.keyboard.type("8m")
    page.keyboard.press("Enter")
    ed = page.locator("td.pg-editing input.pg-editor")
    expect(ed).to_be_visible()
    assert "invalid" in ed.get_attribute("class")
    expect(page.locator(".pg-gstatus")).to_have_text("Enter $5,000,000 or less.")
    assert len(put_series(base_url)) == n                                              # nothing was sent the second time
    page.keyboard.press("ControlOrMeta+a")
    page.keyboard.type("360k")
    page.keyboard.press("Enter")
    expect(page.locator(".pg-gstatus")).to_contain_text("Saved 1 cell")


def test_parse_limit_reads_the_servers_wording(page):
    open_module(page, "drivers")
    res = page.evaluate("""async () => { const { parseLimit } = await import('/static/js/projection/lib/grid.js');
      return [parseLimit('value must be between 0 and 10,000,000'), parseLimit('must be a non-negative number'), parseLimit('payback.fulfillment_per_order must be between 0 and 1,000.'), parseLimit('at least 5 and at most 9'), parseLimit('unknown line_key spend_x')]; }""")
    assert res[0] == {"min": 0, "max": 10000000} and res[1] == {"min": 0} and res[2] == {"min": 0, "max": 1000}
    assert res[3] == {"min": 5, "max": 9} and res[4] is None


# ---------------------------------------------------------------- the asterisk and the three-state month header
@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_projection_asterisks_pass_contrast(browser, base_url, scheme):
    audit = (Path(__file__).resolve().parents[1] / "design" / "tools" / "audit_readability.js").read_text()
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, base_url=base_url, color_scheme=scheme)
    ctx.add_init_script("try { sessionStorage.setItem('projection.por.edit', '1'); } catch (e) {}")
    p = ctx.new_page()
    try:
        open_module(p, "drivers")
        p.wait_for_selector(".pg-ast")
        res = p.evaluate(audit)
        bad = [v for v in res["violations"] if v["kind"] == "contrast" and v.get("text") == "*"]
        assert not bad, bad[:3]
    finally:
        ctx.close()


def _three_state(route):
    resp = route.fetch()
    data = resp.json()
    n = len(data["months"])
    data["status"] = ["books" if i < 20 else "feeds" if i < 22 else "projected" for i in range(n)]
    route.fulfill(response=resp, json=data)


def test_month_header_shows_books_feeds_projected(page):
    page.route("**/api/projection/drivers/table*", _three_state)
    open_module(page, "drivers")
    chips = [c.lower() for c in page.locator(".pg-staterow .pg-stchip").all_inner_texts()]       # the chips are drawn in capitals
    assert chips == ["books", "feeds", "projected*"]
    # books and feeds months are actual months: no asterisk, no projected tint; only projected months carry both
    assert page.locator('tr[data-key="spend"] td[data-c="20"] .pg-ast').count() == 0         # a feeds month
    assert page.locator('tr[data-key="spend"] td[data-c="22"] .pg-ast').count() == 1         # the first projected month
    assert "st-feeds" in page.locator('th.pg-mh[data-c="20"]').get_attribute("class")
    # the header rows still stick: month labels sit below the state and year rows
    tops = page.evaluate("['.pg-staterow .pg-th', '.pg-yearrow .pg-th', '.pg-monthrow .pg-th'].map(s => Math.round(document.querySelector(s).getBoundingClientRect().top))")
    assert tops[0] < tops[1] < tops[2]


def test_two_state_payloads_still_work_without_a_state_row(page):
    open_module(page, "drivers")
    assert page.locator(".pg-staterow").count() == 0
    assert page.locator("th.pg-mh.s-projected").count() > 0 and page.locator("th.pg-mh.s-actual").count() > 0


# ---------------------------------------------------------------- frozen copies, detach, deleted scenarios, books and feeds
def test_headline_strip_names_books_and_feeds_dates(page, base_url):
    hook(base_url, "built", {"keys": ["drivers"]})
    open_module(page, "drivers")
    expect(page.locator("#pg-actual-through")).to_have_text("Books through Oct 2026 \u00b7 Feeds through Nov 2, 2026")


def test_detach_is_offered_only_for_a_copy_that_still_follows_its_base(page, base_url):
    hook(base_url, "legacy_clone", {"name": "Old clone"})
    hook(base_url, "built", {"keys": ["drivers"]})
    open_module(page, "drivers")
    page.select_option("#pg-scenario", label="Old clone")
    page.wait_for_function("document.querySelector('#pg-scenario').selectedOptions[0].textContent === 'Old clone'")
    page.click("#pg-menu-btn")
    item = page.locator('#pg-menu-pop [data-act="detach"]')
    expect(item).to_have_text("Detach from Current plan")
    item.click()
    expect(page.locator("dialog h2")).to_contain_text("Detach")
    with page.expect_request(lambda r: r.method == "POST" and r.url.endswith("/scenarios/old-clone/detach")) as req:
        page.click("dialog button.primary")
    assert "expected_version" in req.value.post_data_json
    expect(page.locator(".pg-toast")).to_contain_text("now stands on its own")
    page.click("#pg-menu-btn")
    assert page.locator('#pg-menu-pop [data-act="detach"]').count() == 0                # frozen now
    page.keyboard.press("Escape")
    # a normal clone and the plan of record never show it
    page.select_option("#pg-scenario", label="Current plan")
    page.click("#pg-menu-btn")
    assert page.locator('#pg-menu-pop [data-act="detach"]').count() == 0


def test_deleted_scenario_can_be_undone_from_the_toast_and_from_history(page, base_url):
    hook(base_url, "built", {"keys": ["drivers"]})
    open_module(page, "drivers")
    for name in ("Try one", "Try two"):
        act(page, "clone")
        page.fill("dialog input", name)
        page.click("dialog button.primary")
        page.wait_for_function(f"document.querySelector('#pg-scenario').selectedOptions[0].textContent === {name!r}")
    # delete "Try two" and undo it from the toast
    act(page, "delete")
    page.fill("dialog input", "try-two")
    page.click("dialog button.danger, dialog button.primary")
    page.wait_for_function("document.querySelector('#pg-scenario').selectedOptions[0].textContent === 'Current plan'")
    toast_btn = page.locator(".pg-toast .pg-toast-act")
    expect(toast_btn).to_have_text("Undo")
    toast_btn.click()
    page.wait_for_function("document.querySelector('#pg-scenario').selectedOptions[0].textContent === 'Try two'")
    # delete "Try one" and bring it back from History
    page.select_option("#pg-scenario", label="Try one")
    act(page, "delete")
    page.fill("dialog input", "try-one")
    page.click("dialog button.danger, dialog button.primary")
    page.wait_for_function("document.querySelector('#pg-scenario').selectedOptions[0].textContent === 'Current plan'")
    act(page, "history")
    page.wait_for_selector("#pg-deleted li")
    expect(page.locator("#pg-deleted-head")).to_have_text("Deleted scenarios")
    expect(page.locator("#pg-deleted li").first).to_contain_text("Try one")
    expect(page.locator("#pg-deleted li").first).to_contain_text("30 more days")
    page.click('#pg-deleted [data-act="undelete"]')
    page.wait_for_function("document.querySelector('#pg-scenario').selectedOptions[0].textContent === 'Try one'")
    assert page.locator("#pg-drawer").is_hidden() if page.locator("#pg-drawer").count() else True


@pytest.mark.allow_errors
def test_503_with_a_module_list_names_every_module(page, base_url):
    hook(base_url, "built", {"keys": ["drivers"]})
    hook(base_url, "degraded", {"modules": [], "table_503": True})
    page.goto("/projection/drivers")
    page.wait_for_function("document.body.dataset.pgState === 'ready'")
    expect(page.locator("#pg-degraded")).to_contain_text("P&L")
    expect(page.locator("#pg-degraded")).to_contain_text("The P&L refresh failed at 21:30")
