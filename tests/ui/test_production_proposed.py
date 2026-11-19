"""The Proposed Concept lane: the first lane of the Creative Pipeline Board (/priorities/production), which replaced the Needs brief view. Against the mock (tests/ui/mock_production_board.py).
Covers: the Needs brief tab is gone and its old URLs fall back to the Board, the lane is first and shows concepts, Add concept works from the lane, assigning from the lane moves the card to Assigned,
and the lane works at 375 px."""
import pytest

pytestmark = pytest.mark.mock_only


@pytest.fixture(autouse=True)
def fresh(ctx, base_url):
    ctx.request.post(base_url + "/__test/assign/reset")
    ctx.request.post(base_url + "/__test/board/seed")
    ctx.request.post(base_url + "/__test/backlog/reset")


def idle(page):
    page.wait_for_function("() => !document.querySelector('#view').hasAttribute('aria-busy')")


def goto(page, qs=""):
    page.goto("/priorities/production" + qs)
    page.wait_for_selector("[data-testid=prod-page]")
    idle(page)
    page.wait_for_selector("[data-testid=prod-board]")


def seed(ctx, base_url, **kw):
    item = {"product": "vita-bull", "cluster": "Energy & Multivitamin", "icp": "Given Up Guy", "pain_point": "Declining Testosterone", "media_type": "video", "story": "He decided feeling worn out is just turning 50."}
    item.update(kw)
    r = ctx.request.post(base_url + "/api/priorities/backlog", data={"items": [item]})
    assert r.status == 201


def test_the_needs_brief_tab_is_gone_and_the_views_are_board_list_plan_by_cut(page):
    goto(page)
    labels = [t.strip() for t in page.locator("[data-testid=prod-view] button, [data-testid=prod-view] label").all_text_contents() if t.strip()]
    assert labels[:4] == ["Board", "List", "Plan", "By cut"], labels
    assert page.locator("[data-testid=prod-view-backlog]").count() == 0
    assert "Needs brief" not in page.locator("[data-testid=prod-page]").text_content()


@pytest.mark.parametrize("qs", ["?view=backlog", "?view=needs-brief"])
def test_old_needs_brief_urls_open_the_board(page, qs):
    goto(page, qs)
    assert page.locator("[data-testid=prod-prop-col]").count() == 1
    assert page.locator("[data-testid=prod-page]").get_attribute("data-view") == "board"
    page.wait_for_function("() => !location.search.includes('backlog') && !location.search.includes('needs-brief')")


def test_old_needs_brief_hash_opens_the_board(page):
    page.goto("/priorities/production#needs-brief")
    page.wait_for_selector("[data-testid=prod-board]")
    assert page.locator("[data-testid=prod-prop-col]").count() == 1
    assert "needs-brief" not in page.url


def test_proposed_concept_is_the_first_lane_and_the_other_lanes_are_unchanged(page, ctx, base_url):
    seed(ctx, base_url)
    goto(page)
    heads = page.locator("[data-testid=prod-board] .prod-board-col .prod-col-head h3").all_text_contents()
    assert heads == ["Proposed Concept", "Assigned", "Brief sent", "In production", "In review", "Ready to launch", "Launched"]
    first = page.locator("[data-testid=prod-board] .prod-board-col").first
    assert first.get_attribute("data-status") == "proposed"
    assert page.locator("[data-testid=prod-board-column]").count() == 6                                       # the six existing lanes are still there
    assert page.locator("[data-testid=prod-prop-count]").text_content() == "1"


def test_the_lane_shows_manual_concepts_with_their_fields(page, ctx, base_url):
    seed(ctx, base_url)
    seed(ctx, base_url, product="boneflex", icp="Won't Sit It Out", pain_point="Knee Pain", media_type="image", story="")
    goto(page)
    cards = page.locator("[data-testid=prod-prop-card]")
    assert cards.count() == 2
    text = cards.first.text_content()
    for needle in ("Energy & Multivitamin", "Given Up Guy", "Declining Testosterone", "He decided", "Video", "Week of"):
        assert needle in text, needle
    assert "Image" in cards.nth(1).text_content()


def test_empty_lane_says_so_and_still_offers_add_concept(page):
    goto(page)
    assert "No proposed concepts" in page.locator("[data-testid=prod-prop-empty]").text_content()
    assert page.locator("[data-testid=prod-prop-add]").is_visible()


@pytest.mark.allow_errors            # the empty submit is a deliberate 400 the browser logs
def test_add_concept_from_the_lane_checks_the_required_tags(page):
    goto(page)
    page.locator("[data-testid=prod-prop-add]").click()
    page.locator("[data-testid=prod-prop-add-go]").click()
    err = page.locator("[data-testid=prod-prop-add-err]")
    page.wait_for_function("() => document.querySelector('[data-testid=prod-prop-add-err]').textContent.length > 0")
    assert "add" in err.text_content().lower() and "before this goes to Proposed Concept" in err.text_content()
    page.select_option("[data-testid=prod-prop-add-product]", "vita-bull")
    page.select_option("[data-testid=prod-prop-add-media]", "video")
    page.fill("[data-testid=prod-prop-add-cluster]", "Energy & Multivitamin")
    page.fill("[data-testid=prod-prop-add-icp]", "Given Up Guy")
    page.fill("[data-testid=prod-prop-add-pain]", "Declining Testosterone")
    page.locator("[data-testid=prod-prop-add-go]").click()
    page.wait_for_selector("[data-testid=prod-prop-add-title]", state="detached")
    assert page.locator("[data-testid=prod-prop-card]").count() == 1
    assert page.locator("[data-testid=prod-prop-count]").text_content() == "1"


def test_assigning_from_the_lane_moves_the_card_to_the_assigned_lane(page, ctx, base_url):
    seed(ctx, base_url, story="")
    goto(page)
    assigned = page.locator("[data-testid=prod-board-list][data-status=assigned]").first
    before = assigned.locator("[data-testid=prod-board-card]").count()
    page.locator("[data-testid=prod-prop-assign]").first.click()
    page.locator("[data-testid=prod-prop-assign-go]").click()
    assert "Choose a partner" in page.locator("[data-testid=prod-prop-assign-err]").text_content()
    page.select_option("[data-testid=prod-prop-assign-vendor]", "Madre")
    page.locator("[data-testid=prod-prop-assign-go]").click()
    assert "brief" in page.locator("[data-testid=prod-prop-assign-err]").text_content().lower()
    page.fill("[data-testid=prod-prop-assign-text]", "Make a 30 second video about feeling worn out at 50.")
    page.locator("[data-testid=prod-prop-assign-go]").click()
    page.wait_for_selector("[data-testid=prod-prop-assign-title]", state="detached")
    page.wait_for_selector("[data-testid=prod-prop-empty]")
    assert page.locator("[data-testid=prod-prop-count]").text_content() == "0"
    after = page.locator("[data-testid=prod-board-list][data-status=assigned]").first.locator("[data-testid=prod-board-card]")
    assert after.count() == before + 1
    assert "Declining Testosterone" in page.locator("[data-testid=prod-board-list][data-status=assigned]").first.text_content()


def test_open_edit_and_remove_a_proposed_concept(page, ctx, base_url):
    seed(ctx, base_url)
    goto(page)
    page.locator("[data-testid=prod-prop-title]").first.click()
    page.fill("[data-testid=prod-prop-open-pain]", "Low Drive")
    page.select_option("[data-testid=prod-prop-open-product]", "boneflex")
    page.locator("[data-testid=prod-prop-open-save]").click()
    page.wait_for_selector("[data-testid=prod-prop-open-title]", state="detached")
    assert "Low Drive" in page.locator("[data-testid=prod-prop-card]").first.text_content()
    page.locator("[data-testid=prod-prop-open]").first.click()
    page.locator("[data-testid=prod-prop-remove]").click()
    assert page.locator("[data-testid=prod-prop-card]").count() == 1                                          # the first click only asks for confirmation
    page.locator("[data-testid=prod-prop-remove]").click()
    page.wait_for_selector("[data-testid=prod-prop-empty]")


def test_the_lane_follows_the_product_filter_and_vendor_links_stay_above_the_board(page, ctx, base_url):
    seed(ctx, base_url)
    seed(ctx, base_url, product="boneflex", pain_point="Knee Pain")
    goto(page)
    assert page.locator("[data-testid=prod-prop-card]").count() == 2
    assert "Vendor links" in page.locator("[data-testid=prod-links] summary").text_content()
    page.goto("/priorities/production?product=boneflex")
    page.wait_for_selector("[data-testid=prod-prop-col]")
    page.wait_for_function("() => document.querySelectorAll('[data-testid=prod-prop-card]').length === 1")


def test_grouped_by_vendor_the_lane_sits_above_the_vendor_lanes(page, ctx, base_url):
    seed(ctx, base_url)
    goto(page, "?group=vendor")
    lane = page.locator("[data-testid=prod-prop-lane]")
    assert lane.count() == 1 and lane.locator("[data-testid=prod-prop-card]").count() == 1
    assert page.locator("[data-testid=prod-prop-lane], [data-testid=prod-board-lane]").first.get_attribute("data-testid") == "prod-prop-lane"


def test_375px_no_sideways_page_scroll_and_the_lane_is_reachable(browser, base_url):
    c = browser.new_context(viewport={"width": 375, "height": 812}, base_url=base_url)
    try:
        c.request.post(base_url + "/__test/assign/reset")
        c.request.post(base_url + "/__test/board/seed")
        c.request.post(base_url + "/__test/backlog/reset")
        c.request.post(base_url + "/api/login", data={"username": "JoshHoughton", "password": "test"})
        seed(c, base_url)
        page = c.new_page()
        goto(page)
        assert page.evaluate("() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1")
        chip = page.locator("[data-testid=prod-board-jump-proposed]")
        assert chip.count() == 1 and chip.get_attribute("aria-current") == "true"
        assert page.locator("[data-testid=prod-prop-add]").is_visible()
        assert page.locator("[data-testid=prod-prop-assign]").first.is_visible()
        page.locator("[data-testid=prod-prop-assign]").first.click()
        box = page.locator("[data-testid=prod-prop-assign-title]").locator("xpath=ancestor::*[@role='dialog']").bounding_box()
        assert box["x"] >= 0 and box["x"] + box["width"] <= 376
    finally:
        c.close()
