"""Polish round of the Production board (2026-11-12): calm cards with a "..." menu, one slim Team row, one Stage pill and short link labels in the List, phone column chips, Plan with a collapsed
'nothing planned' group and a Saved cue, a tighter vendor page and a Clear button on the due date. Same mocks and seed as test_production_board.py (see its docstring for the card ids)."""
import json

import pytest

pytestmark = pytest.mark.mock_only
MADRE = "tok-b3-madre-00000000000000000"


@pytest.fixture(autouse=True)
def fresh(ctx, base_url):
    ctx.request.post(base_url + "/__test/assign/reset")
    ctx.request.post(base_url + "/__test/board/seed")
    ctx.request.post(base_url + "/__test/vendorb3/reset")


def idle(page):
    page.wait_for_function("() => !document.querySelector('#view').hasAttribute('aria-busy')")


def goto(page, qs=""):
    page.goto("/priorities/production" + qs)
    page.wait_for_selector("[data-testid=prod-page]")
    idle(page)
    page.wait_for_selector("[data-testid=prod-board-card], [data-testid=prod-table], [data-testid=prod-plan]")


def card(page, cid):
    return page.locator(f"[data-testid=prod-board-card][data-id='{cid}']")


# ---- 1. cards
def test_card_is_clean_menu_holds_move_open_and_alternatives(page):
    goto(page)
    c = card(page, 7)                                                    # In review: Approve is the main button, Request changes sits in the menu
    assert not c.locator("[data-testid=prod-card-moveto]").is_visible()
    assert c.locator("[data-testid=prod-card-action]").text_content() == "Approve"
    c.locator("[data-testid=prod-card-more-btn]").click()
    c.locator("[data-testid=prod-card-moveto]").wait_for(state="visible")
    assert c.locator("[data-testid=prod-card-moveto]").is_visible() and c.locator("[data-testid=prod-card-open]").is_visible()
    assert c.locator("[data-testid=prod-card-alt]").all_text_contents() == ["Request changes"]
    c.locator("[data-testid=prod-card-open-comments]").click()
    page.wait_for_selector("[data-testid=prod-drawer]")
    assert page.locator("[data-testid=prod-drawer-tab-comments]").get_attribute("aria-selected") == "true"


def test_comment_count_is_hidden_at_zero_and_chips_stay_on_one_line(page):
    goto(page)
    assert card(page, 1).locator("[data-testid=prod-card-comments]").count() == 0
    assert card(page, 7).locator("[data-testid=prod-card-comments]").count() == 1
    boxes = card(page, 5).locator(".prod-card-chips .prod-chip").evaluate_all("cs => cs.map(c => Math.round(c.getBoundingClientRect().top))")
    assert len(boxes) >= 2 and len(set(boxes)) == 1
    assert card(page, 5).locator("[data-testid=prod-card-needs]").text_content() == "Needs creative"


def test_empty_column_says_drop_a_card_here_and_headers_are_sticky(page):
    goto(page)
    page.goto("/priorities/production?q=zzzznomatch")
    page.wait_for_selector("[data-testid=prod-board]")
    idle(page)
    assert page.locator("[data-testid=prod-board-column][data-status=assigned] [data-testid=prod-board-empty]").text_content().strip() == "Drop a card here"
    assert page.locator("[data-testid=prod-board-column][data-status=launched] [data-testid=prod-board-empty]").text_content().strip() == "Nothing here yet"
    assert page.locator(".prod-col-head").first.evaluate("e => getComputedStyle(e).position") == "sticky"


# ---- 2. header
def test_team_row_is_slim_and_the_board_starts_high(page):
    page.set_viewport_size({"width": 1440, "height": 900})
    goto(page)
    team = page.locator("[data-testid=prod-team]")
    assert team.locator("[data-testid=prod-board-load-row]").count() == 2 and team.locator("[data-testid=prod-links]").count() == 1
    assert team.bounding_box()["height"] < 50
    head = page.locator("[data-testid=prod-head]").bounding_box()
    first = page.locator("[data-testid=prod-board-card]").first.bounding_box()
    assert first["y"] - head["y"] <= 320, (first["y"], head["y"])


# ---- 3. list
def test_list_has_one_stage_pill_short_link_labels_and_fits_1440(page):
    page.set_viewport_size({"width": 1440, "height": 900})
    goto(page, "?view=list")
    assert [t.strip() for t in page.locator("[data-testid=prod-table] thead th").all_text_contents()].count("Status") == 0
    r = page.locator("[data-testid=prod-row][data-id='2']")
    cell = r.locator("td.prod-c-stage")
    assert cell.locator("[data-testid=prod-status]").is_visible() and cell.locator("[data-testid=prod-stage]").count() == 1
    a = r.locator("[data-testid=prod-brief_url]")
    assert a.text_content().strip() == "Brief" and a.get_attribute("href").startswith("https://") and a.get_attribute("title") == a.get_attribute("href")
    assert r.locator("[data-testid=prod-creative_url]").text_content().strip() == "Creative"
    assert page.evaluate("() => { const s = document.querySelector('[data-testid=prod-scroll]'); return s.scrollWidth <= s.clientWidth + 1 }")
    for tid in ("prod-spend", "prod-return"):
        b = r.locator(f"[data-testid={tid}]").bounding_box()
        assert b["x"] + b["width"] <= 1440


def test_list_columns_toggle_shows_priority_and_ready(page):
    goto(page, "?view=list")
    assert not page.locator("[data-testid=prod-row][data-id='5'] [data-testid=prod-priority]").is_visible()
    page.locator("[data-testid=prod-cols] > summary").click()
    page.check("[data-testid=prod-cols-priority]")
    assert page.locator("[data-testid=prod-row][data-id='5'] [data-testid=prod-priority]").is_visible()
    page.check("[data-testid=prod-cols-ready]")
    assert page.locator("[data-testid=prod-row][data-id='5'] [data-testid=prod-ready-state]").is_visible()


# ---- 4. phone chips
def test_phone_jump_chips_scroll_to_the_column_and_highlight_it(browser, base_url):
    c = browser.new_context(viewport={"width": 375, "height": 812}, base_url=base_url)
    try:
        c.request.post(base_url + "/__test/assign/reset")
        c.request.post(base_url + "/__test/board/seed")
        c.request.post(base_url + "/api/login", data={"username": "JoshHoughton", "password": "test"})
        pg = c.new_page()
        goto(pg)
        chips = pg.locator("[data-testid=prod-board-jump] .prod-jump-chip")
        assert chips.count() == 6 and chips.first.is_visible() and chips.first.get_attribute("aria-current") == "true"
        assert chips.first.bounding_box()["height"] >= 44
        assert pg.locator("[data-testid=prod-board-jump-in_review]").text_content().strip().startswith("In review")
        pg.click("[data-testid=prod-board-jump-in_review]")
        pg.wait_for_function("() => document.querySelector('[data-testid=prod-board-jump-in_review]').getAttribute('aria-current') === 'true'")
        pg.wait_for_function("() => { const s = document.querySelector('[data-testid=prod-board-scroll]'); const c = document.querySelector('.prod-board-col[data-status=in_review]'); return Math.abs(c.offsetLeft - s.scrollLeft) < 30 }")
        assert pg.locator("[data-testid=prod-board-jump-assigned]").get_attribute("aria-current") is None
        assert pg.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth + 1")
    finally:
        c.close()


def test_jump_chips_are_hidden_on_a_wide_screen(page):
    page.set_viewport_size({"width": 1440, "height": 900})
    goto(page)
    assert not page.locator("[data-testid=prod-board-jump]").is_visible()


# ---- 5. plan
def test_plan_collapses_idle_vendors_and_expands_with_capacity_inputs(page):
    goto(page, "?view=plan")
    idle_box = page.locator("[data-testid=prod-plan-idle]")
    assert "vendors have nothing planned" in idle_box.locator("> summary").text_content()
    assert not page.locator("[data-testid=prod-plan-capacity][data-vendor=TrybeOrion]").is_visible()
    assert page.locator("[data-testid=prod-plan-capacity][data-vendor=Madre]").is_visible()
    idle_box.locator("> summary").click()
    assert page.locator("[data-testid=prod-plan-capacity][data-vendor=TrybeOrion]").is_visible()
    assert page.locator("[data-testid=prod-plan-row]").count() == 9


def test_plan_capacity_saves_on_enter_with_a_saved_cue_and_no_spinner(page):
    goto(page, "?view=plan")
    inp = page.locator("[data-testid=prod-plan-capacity][data-vendor=Victoria]")
    assert inp.evaluate("e => getComputedStyle(e).appearance") in ("textfield", "none")
    with page.expect_request(lambda r: r.method == "PUT" and "vendor-capacity" in r.url) as ri:
        inp.fill("7")
        inp.press("Enter")
    assert json.loads(ri.value.post_data) == {"vendor": "Victoria", "per_week": 7}
    page.wait_for_selector("[data-testid=prod-plan-capsaved]")
    assert page.locator("[data-testid=prod-plan-capsaved]").text_content() == "Saved"


# ---- 6. vendor page
def test_vendor_page_brief_is_a_text_link_details_is_small_and_comments_open_in_one_click(page):
    page.goto(f"/priorities/vendor/{MADRE}")
    page.wait_for_selector("[data-testid=prod-vendor-page]")
    idle(page)
    c = page.locator("[data-testid=prod-card]").first
    b = c.locator("[data-testid=prod-v-brief]")
    assert "btn" not in (b.get_attribute("class") or "").split() and b.evaluate("e => getComputedStyle(e).textDecorationLine") == "underline"
    assert b.bounding_box()["height"] >= 43.5
    t = c.locator("[data-testid=prod-v-thread]")
    assert "Comments (" in t.locator("> summary").text_content() and t.get_attribute("open") is None
    t.locator("> summary").click()
    assert t.locator("[data-testid=prod-v-comment-input]").is_visible()
    sizes = page.evaluate("() => [...document.querySelectorAll('.prod-vendor input, .prod-vendor textarea')].filter(e => e.offsetParent).map(e => parseFloat(getComputedStyle(e).fontSize))")
    assert min(sizes) >= 16


# ---- 7. dates
def test_due_date_has_a_visible_label_and_a_clear_button(page):
    goto(page, "?card=7")
    page.wait_for_selector("[data-testid=prod-drawer-details]")
    due = page.locator("[data-testid=prod-drawer-due]")
    assert due.get_attribute("type") == "date" and due.input_value() != ""
    assert "Due date" in page.locator("label.prod-field:has([data-testid=prod-drawer-due]) .pri-k").text_content()
    with page.expect_request(lambda r: r.method == "PATCH" and "/assignments/7" in r.url) as ri:
        page.click("[data-testid=prod-drawer-due-clear]")
        assert due.input_value() == ""
        page.click("[data-testid=prod-drawer-save]")
    assert json.loads(ri.value.post_data) == {"due_date": None}


# ---- the "..." popover never clipped
def _inside_viewport_and_unclipped(page, pop):
    return page.evaluate("""(sel) => {
      const m = document.querySelector(sel); const r = m.getBoundingClientRect();
      const inView = r.top >= 0 && r.left >= 0 && r.bottom <= innerHeight && r.right <= innerWidth;
      const sc = document.querySelector('[data-testid=prod-board-scroll]').getBoundingClientRect();
      // the board's scroll area must not hide it: the element at each corner/center of the popover is the popover itself
      const pts = [[r.left + 4, r.top + 4], [r.right - 4, r.top + 4], [r.left + 4, r.bottom - 4], [r.right - 4, r.bottom - 4], [(r.left + r.right) / 2, (r.top + r.bottom) / 2]];
      const hit = pts.every(([x, y]) => { const e = document.elementFromPoint(x, y); return e && m.contains(e); });
      return { inView, hit, below: r.bottom > sc.bottom };
    }""", pop)


@pytest.mark.parametrize("vp", [(1440, 900), (1440, 560), (375, 812)])
def test_more_menu_on_the_last_card_is_fully_visible_not_clipped(browser, base_url, vp):
    c = browser.new_context(viewport={"width": vp[0], "height": vp[1]}, base_url=base_url)
    try:
        c.request.post(base_url + "/__test/assign/reset")
        c.request.post(base_url + "/__test/board/seed")
        c.request.post(base_url + "/api/login", data={"username": "JoshHoughton", "password": "test"})
        pg = c.new_page()
        goto(pg)
        last = pg.locator("[data-testid=prod-board-list][data-status=assigned] [data-testid=prod-board-card]").last
        last.scroll_into_view_if_needed()
        btn = last.locator("[data-testid=prod-card-more-btn]")
        if vp[0] < 600:
            assert btn.bounding_box()["height"] >= 44
        btn.click()
        pg.wait_for_selector(".prod-card-more[open] .prod-card-menu", state="visible")
        res = _inside_viewport_and_unclipped(pg, ".prod-card-more[open] .prod-card-menu")
        assert res["inView"] and res["hit"], res
        pg.keyboard.press("Escape")
        assert not last.locator("[data-testid=prod-card-menu]").is_visible()
        btn.click()
        pg.mouse.click(5, 5)
        assert not last.locator("[data-testid=prod-card-menu]").is_visible()
    finally:
        c.close()
