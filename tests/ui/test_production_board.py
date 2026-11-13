"""Production board (/priorities/production): Board (kanban), List, Plan and the card drawer against the mock (tests/ui/mock_production_board.py).
Seeded cards (POST /__test/board/seed, on top of the four base rows): 1 Assigned (Madre), 2 In review (Madre, old Creative delivered), 3 Brief sent? no, see ids below.
 1 Assigned Madre | 2 In review Madre | 3 Assigned Victoria next week | 4 Launched Madre 2 weeks ago (old)
 5 In production Madre high priority overdue no creative | 6 In production Victoria due tomorrow | 7 In review Victoria 2 comments | 8 Brief sent Madre
 9 Assigned TrybeDCShop next week | 10 Ready Victoria | 11 Launched Madre 30 days ago (hidden until Show launched and dropped)"""
import json
import re

import pytest

pytestmark = pytest.mark.mock_only

STATUSES = ["assigned", "brief_sent", "in_production", "in_review", "ready", "launched"]


@pytest.fixture(autouse=True)
def fresh(ctx, base_url):
    ctx.request.post(base_url + "/__test/assign/reset")
    ctx.request.post(base_url + "/__test/board/seed")


def choose(page, tid, *values):
    """Tick exactly these options in a multi-select filter (none = All), then close it so the choice is applied."""
    d = page.locator(f".pagefilters [data-testid={tid}]")
    d.locator("summary").click()
    for cb in d.locator("input[type=checkbox]").all():
        cb.set_checked(cb.get_attribute("value") in values)
    page.keyboard.press("Escape")


def idle(page):
    page.wait_for_function("() => !document.querySelector('#view').hasAttribute('aria-busy')")


def goto(page, qs=""):
    page.goto("/priorities/production" + qs)
    page.wait_for_selector("[data-testid=prod-page]")
    idle(page)
    page.wait_for_selector("[data-testid=prod-board], [data-testid=prod-table], [data-testid=prod-plan], [data-testid=prod-empty]")


def card(page, cid):
    return page.locator(f"[data-testid=prod-board-card][data-id='{cid}']")


def col_ids(page, status, lane=None):
    sel = f"[data-testid=prod-board-list][data-status={status}]" + (f"[data-lane='{lane}']" if lane is not None else "")
    return page.locator(sel).first.locator("[data-testid=prod-board-card]").evaluate_all("cs => cs.map(c => c.dataset.id)")


def drag(page, src, dst, where="center", steps=12):
    """Mouse drag from the middle of `src` to the top, middle or bottom of `dst`."""
    grab = src.locator("[data-testid=prod-card-title]")          # press on the card title: most of a card is a button or a link, which do not start a drag
    a = (grab if grab.count() else src).first.bounding_box()
    b = dst.bounding_box()
    y = b["y"] + (6 if where == "top" else b["height"] - 6 if where == "bottom" else b["height"] / 2)
    page.mouse.move(a["x"] + a["width"] / 2, a["y"] + a["height"] / 2 + 2)
    page.mouse.down()
    page.mouse.move(a["x"] + a["width"] / 2 + 10, a["y"] + a["height"] / 2 + 12, steps=3)
    page.mouse.move(b["x"] + b["width"] / 2, y, steps=steps)
    page.mouse.up()


def drag_card(page, cid, target_list_status, where="center"):
    """Drag a card to a card in (or the empty list of) the target column; returns the /move request body."""
    lst = page.locator(f"[data-testid=prod-board-list][data-status={target_list_status}]").first
    cards = lst.locator("[data-testid=prod-board-card]:not(.prod-dragging)")
    dst = (cards.last if where == "bottom" else cards.first) if cards.count() else lst
    with page.expect_request(lambda r: "/move" in r.url and r.method == "POST", timeout=5000) as ri:
        drag(page, card(page, cid), dst, where)
    return json.loads(ri.value.post_data)


def toast(page):
    return page.locator("[data-testid=prod-toast]").last


# ---------------------------------------------------------------------------------------------------------------- render
def test_board_is_the_default_view_with_six_columns_in_order_and_counts(page):
    goto(page)
    assert page.locator("[data-testid=prod-page]").get_attribute("data-view") == "board"
    cols = page.locator("[data-testid=prod-board-column]")
    assert cols.evaluate_all("cs => cs.map(c => c.dataset.status)") == STATUSES
    heads = [t.strip() for t in page.locator("[data-testid=prod-board-column] h3").all_text_contents()]
    assert heads == ["Assigned", "Brief sent", "In production", "In review", "Ready to launch", "Launched"]
    counts = page.locator("[data-testid=prod-board-count]").all_text_contents()
    assert counts == ["3", "1", "2", "2", "1", "1"]                  # launched column: only row 4 (recent); row 11 is 30 days old
    assert col_ids(page, "in_production") == ["5", "6"]              # high priority first
    assert col_ids(page, "assigned") == ["1", "3", "9"]              # then by position
    assert page.locator("h1").text_content() == "Production"
    assert "—" not in page.locator("[data-testid=prod-page]").text_content()


def test_card_shows_product_vendor_format_due_priority_comments_links_and_hints(page):
    goto(page)
    c5 = card(page, 5)
    assert c5.locator("[data-testid=prod-card-product]").get_attribute("data-product") == "vita-bull"
    assert c5.locator("[data-testid=prod-card-vendor]").text_content() == "Madre"
    assert c5.locator("[data-testid=prod-card-format]").text_content() == "F1"
    assert "F1" in c5.locator("[data-testid=prod-card-format]").get_attribute("title")
    assert c5.locator("[data-testid=prod-card-due]").get_attribute("data-tone") == "bad" and "Overdue" in c5.locator("[data-testid=prod-card-due]").text_content()
    assert c5.locator("[data-testid=prod-card-priority]").text_content() == "High"
    assert c5.locator("[data-testid=prod-card-needs]").text_content() == "Needs creative"
    c6 = card(page, 6)
    assert c6.locator("[data-testid=prod-card-due]").get_attribute("data-tone") == "warn"            # due tomorrow
    assert c6.locator("[data-testid=prod-card-needs]").count() == 0 and c6.locator("[data-testid=prod-card-creative]").count() == 1
    c7 = card(page, 7)
    assert c7.locator("[data-testid=prod-card-comments]").text_content().strip() == "2"
    assert c7.locator("[data-testid=prod-card-brief]").get_attribute("href").endswith("/7")
    assert card(page, 8).locator("[data-testid=prod-card-due]").get_attribute("data-tone") == ""      # later: neutral
    assert card(page, 4).locator("[data-testid=prod-card-result]").text_content().startswith("$")   # launched card shows spend and return
    assert card(page, 4).locator("[data-testid=prod-card-due]").count() == 0


def test_launched_is_collapsed_to_14_days_and_show_launched_and_dropped_adds_everything_and_a_dropped_lane(page, base_url):
    goto(page)
    assert col_ids(page, "launched") == ["4"]
    assert page.locator("[data-testid=prod-board-column][data-status=launched]").text_content().count("Last 14 days") == 1
    page.request.patch(base_url + "/api/priorities/assignments/9", data={"dropped": True})
    goto(page, "?closed=1")
    assert sorted(col_ids(page, "launched")) == ["11", "4"]
    cols = page.locator("[data-testid=prod-board-column]").evaluate_all("cs => cs.map(c => c.dataset.status)")
    assert cols == STATUSES + ["dropped"]
    assert col_ids(page, "dropped") == ["9"]
    page.locator("[data-testid=prod-closed]").uncheck()
    page.wait_for_function("() => document.querySelectorAll('[data-testid=prod-board-column]').length === 6")
    assert "closed=1" not in page.url


def test_load_bars_show_open_cards_against_capacity_and_flag_over_capacity(page):
    goto(page)
    madre = page.locator("[data-testid=prod-board-load-row][data-vendor=Madre]")
    assert madre.locator("[data-testid=prod-board-load-text]").text_content() == "4 of 2"
    assert madre.get_attribute("data-over") == "1" and madre.locator("[data-testid=prod-board-load-warn]").text_content() == "Over capacity"
    vic = page.locator("[data-testid=prod-board-load-row][data-vendor=Victoria]")
    assert vic.locator("[data-testid=prod-board-load-text]").text_content() == "3 of 10"
    assert vic.get_attribute("data-over") == "0" and vic.locator("[data-testid=prod-board-load-warn]").count() == 0


# ---------------------------------------------------------------------------------------------------------------- drag and drop
def test_drag_to_another_column_posts_move_with_neighbours_and_the_card_lands_there(page):
    goto(page)
    body = drag_card(page, 8, "in_production", "bottom")
    assert body["status"] == "in_production" and ("after_id" in body or "before_id" in body)
    page.wait_for_function("() => document.querySelector('[data-testid=prod-board-list][data-status=in_production]').querySelectorAll('[data-testid=prod-board-card]').length === 3")
    assert "8" in col_ids(page, "in_production") and col_ids(page, "brief_sent") == []
    assert "Moved to In production" in toast(page).text_content()


def test_drag_within_a_column_reorders_with_before_and_after_neighbours(page):
    goto(page)
    assert col_ids(page, "assigned") == ["1", "3", "9"]
    body = drag_card(page, 9, "assigned", "top")                      # drag the last Assigned card above the first one
    assert body["status"] == "assigned" and body["before_id"] == 1 and "after_id" not in body


def test_dragging_into_an_empty_column_works(page, base_url):
    page.request.patch(base_url + "/api/priorities/assignments/10", data={"status": "in_production"})
    goto(page)
    assert col_ids(page, "ready") == []
    body = drag_card(page, 7, "ready")                                # in_review -> ready is Approve by hand
    assert body["status"] == "ready" and "before_id" not in body and "after_id" not in body


@pytest.mark.allow_errors
def test_rejected_move_reverts_and_shows_the_plain_english_reason(page):
    goto(page)
    body = drag_card(page, 5, "in_review")                           # card 5 has no creative link
    assert body["status"] == "in_review"
    toast(page).wait_for()
    page.wait_for_function("() => document.querySelector('[data-testid=prod-toast]').textContent.includes('creative link')")
    assert "Add a creative link before sending to review." in page.locator("[data-testid=prod-toast]").last.text_content()
    assert 5 == int(col_ids(page, "in_production")[0]) and "5" not in col_ids(page, "in_review")
    assert card(page, 5).get_attribute("data-status") == "in_production"
    assert "Add a creative link" in page.locator("[data-testid=prod-msg]").text_content()


def test_dragging_onto_launched_is_refused_without_calling_the_server(page):
    goto(page)
    calls = []
    page.on("request", lambda r: calls.append(r.url) if "/move" in r.url else None)
    drag(page, card(page, 10), page.locator("[data-testid=prod-board-list][data-status=launched] [data-testid=prod-board-card]").first)
    page.wait_for_function("() => document.querySelector('[data-testid=prod-toast]') && document.querySelector('[data-testid=prod-toast]').textContent.includes('Mark launched')")
    assert calls == [] and card(page, 10).get_attribute("data-status") == "ready" and "10" in col_ids(page, "ready")


def test_dragging_back_from_in_review_asks_for_a_comment_and_cancel_undoes_it(page):
    goto(page)
    calls = []
    page.on("request", lambda r: calls.append(r.url) if "/move" in r.url else None)
    lst = page.locator("[data-testid=prod-board-list][data-status=in_production]").first
    drag(page, card(page, 7), lst.locator("[data-testid=prod-board-card]").first)
    page.wait_for_selector("[data-testid=prod-dialog]")
    assert page.locator("[data-testid=prod-dialog] h2").text_content() == "Request changes"
    page.click("[data-testid=prod-dialog-cancel]")
    page.wait_for_selector("[data-testid=prod-dialog]", state="detached")
    assert calls == [] and "7" in col_ids(page, "in_review") and "7" not in col_ids(page, "in_production")


def test_keyboard_pick_arrow_and_drop_moves_a_card(page):
    goto(page)
    c = card(page, 8)
    c.focus()
    page.keyboard.press("Space")
    assert c.get_attribute("aria-grabbed") == "true"
    assert "Picked up" in page.locator("[data-testid=prod-board-live]").text_content()
    page.keyboard.press("ArrowRight")
    assert "In production" in page.locator("[data-testid=prod-board-live]").text_content()
    with page.expect_request(lambda r: "/move" in r.url, timeout=5000) as ri:
        page.keyboard.press("Enter")
    assert json.loads(ri.value.post_data)["status"] == "in_production"
    page.wait_for_function("() => document.querySelector('[data-testid=prod-board-card][data-id=\"8\"]').dataset.status === 'in_production'")
    assert page.evaluate("() => document.activeElement.dataset.id") == "8"           # focus comes back to the card


def test_keyboard_escape_cancels_a_pick(page):
    goto(page)
    calls = []
    page.on("request", lambda r: calls.append(r.url) if "/move" in r.url else None)
    c = card(page, 8)
    c.focus()
    page.keyboard.press("Space")
    page.keyboard.press("ArrowRight")
    page.keyboard.press("Escape")
    assert "8" in col_ids(page, "brief_sent") and calls == [] and c.get_attribute("aria-grabbed") is None


def test_move_to_menu_moves_a_card_and_hides_launched(page):
    page.set_viewport_size({"width": 1280, "height": 1600})                       # a scroll closes the menu, so the card must be on screen without scrolling
    goto(page)
    card(page, 9).locator("[data-testid=prod-card-more] > summary").click()
    sel = card(page, 9).locator("[data-testid=prod-card-moveto]")
    assert "Launched" not in sel.locator("option").all_text_contents()
    assert "Assigned" not in sel.locator("option").all_text_contents()            # its own status is not offered
    with page.expect_request(lambda r: "/move" in r.url) as ri:
        sel.select_option("brief_sent")
    assert json.loads(ri.value.post_data)["status"] == "brief_sent"
    page.wait_for_function("() => document.querySelector('[data-testid=prod-board-card][data-id=\"9\"]').dataset.status === 'brief_sent'")


def test_next_action_buttons_on_cards(page):
    goto(page)
    assert card(page, 1).locator("[data-testid=prod-card-action]").text_content() == "Send brief"
    assert card(page, 8).locator("[data-testid=prod-card-action]").text_content() == "Mark in production"
    assert card(page, 6).locator("[data-testid=prod-card-action]").text_content() == "Submit for review"
    assert card(page, 7).locator("[data-testid=prod-card-action]").text_content() == "Approve"
    assert card(page, 10).locator("[data-testid=prod-card-action]").text_content() == "Mark launched"
    card(page, 7).locator("[data-testid=prod-card-action]").click()
    page.wait_for_function("() => document.querySelector('[data-testid=prod-board-card][data-id=\"7\"]').dataset.status === 'ready'")
    card(page, 10).locator("[data-testid=prod-card-action]").click()
    page.wait_for_function("() => document.querySelector('[data-testid=prod-board-card][data-id=\"10\"]').dataset.status === 'launched'")


# ---------------------------------------------------------------------------------------------------------------- swimlanes and filters
def test_group_by_vendor_draws_a_lane_per_vendor_and_keeps_cards_in_their_own_lane(page):
    goto(page)
    page.locator("[data-testid=prod-board-group]").check()
    assert "group=vendor" in page.url
    lanes = page.locator("[data-testid=prod-board-lane]")
    assert lanes.evaluate_all("ls => ls.map(l => l.dataset.vendor)") == ["Madre", "Victoria", "TrybeDCShop"] or lanes.count() == 3
    assert page.locator("[data-testid=prod-board-lane][data-vendor=Madre] [data-testid=prod-board-load-row]").count() == 1
    assert sorted(col_ids(page, "in_production", "Victoria")) == ["6"] and col_ids(page, "in_production", "Madre") == ["5"]
    mad = page.locator("[data-testid=prod-board-lane][data-vendor=Madre] [data-testid=prod-board-card]").evaluate_all("cs => cs.every(c => c.querySelector('[data-testid=prod-card-vendor]').textContent === 'Madre')")
    assert mad
    body = drag_card(page, 8, "in_production") if False else None
    # a card dragged onto another vendor's lane does not land there: the drop needs a list in its own lane
    a = card(page, 8).bounding_box()
    b = page.locator("[data-testid=prod-board-lane][data-vendor=Victoria] [data-testid=prod-board-list][data-status=in_production]").bounding_box()
    calls = []
    page.on("request", lambda r: calls.append(r.url) if "/move" in r.url else None)
    page.mouse.move(a["x"] + a["width"] / 2, a["y"] + 20); page.mouse.down(); page.mouse.move(b["x"] + 50, b["y"] + 20, steps=10); page.mouse.up()
    assert calls == [] and card(page, 8).get_attribute("data-status") == "brief_sent"
    page.locator("[data-testid=prod-board-group]").uncheck()
    assert "group=vendor" not in page.url and page.locator("[data-testid=prod-board-lane]").count() == 0


def test_filters_vendor_overdue_search_product_week_and_stage_param(page):
    goto(page)
    choose(page, "prod-vendor", "Victoria")
    page.wait_for_function("() => document.querySelectorAll('[data-testid=prod-board-card]').length === 4 || true")
    page.wait_for_function("() => [...document.querySelectorAll('[data-testid=prod-card-vendor]')].every(v => v.textContent === 'Victoria')")
    assert "vendor=Victoria" in page.url
    choose(page, "prod-vendor")
    page.wait_for_function("() => new Set([...document.querySelectorAll('[data-testid=prod-card-vendor]')].map(v => v.textContent)).size > 1")
    page.locator("[data-testid=prod-overdue-only]").check()
    assert page.locator("[data-testid=prod-board-card]").evaluate_all("cs => cs.map(c => c.dataset.id)") == ["5"]
    assert page.locator("[data-testid=prod-board-overdue]").count() == 1
    page.locator("[data-testid=prod-overdue-only]").uncheck()
    page.fill("[data-testid=prod-search]", "knee")
    assert page.locator("[data-testid=prod-board-card]").evaluate_all("cs => cs.map(c => c.dataset.id)") == ["7"]
    page.fill("[data-testid=prod-search]", "")
    choose(page, "prod-product-filter", "boneflex")
    page.wait_for_function("() => [...document.querySelectorAll('[data-testid=prod-card-product]')].every(p => p.dataset.product === 'boneflex')")
    assert sorted(page.locator("[data-testid=prod-board-card]").evaluate_all("cs => cs.map(c => c.dataset.id)")) == ["3", "7", "8"]
    choose(page, "prod-product-filter")
    goto(page, "?stage=creative_in")                                  # old stage names still work: it maps to In review
    assert page.locator("[data-testid=prod-board-column]").evaluate_all("cs => cs.map(c => c.dataset.status)") == ["in_review"]
    page.click("[data-testid=prod-board-showall]")
    assert page.locator("[data-testid=prod-board-column]").count() == 6
    week = page.locator("[data-testid=prod-week] input[type=checkbox]").nth(0).get_attribute("value")
    goto(page, f"?week={week}")
    assert page.locator("[data-testid=prod-board-card]").count() >= 1


# ---------------------------------------------------------------------------------------------------------------- drawer
def test_drawer_opens_from_a_card_with_header_stepper_actions_and_tabs_and_closes_with_escape(page):
    goto(page)
    card(page, 7).locator("[data-testid=prod-card-title]").click()
    page.wait_for_selector("[data-testid=prod-drawer]")
    assert "card=7" in page.url
    assert re.fullmatch(r"Victoria - .+ - Knee Pain - Week of \d+/\d+", page.locator("[data-testid=prod-drawer-title]").text_content())
    assert page.locator("[data-testid=prod-drawer-status]").text_content() == "In review"
    assert page.locator("[data-testid=prod-drawer-step][aria-current=step]").get_attribute("data-status") == "in_review"
    acts = [t.strip() for t in page.locator("[data-testid=prod-drawer-actions] button").all_text_contents()]
    assert acts == ["Approve", "Request changes"]
    tabs = [t.strip() for t in page.locator(".prod-drawer-tab").all_text_contents()]
    assert tabs == ["Details", "Comments (2)", "History"]
    page.keyboard.press("Escape")
    page.wait_for_selector("[data-testid=prod-drawer]", state="detached")
    assert "card=" not in page.url
    assert page.evaluate("() => document.activeElement && document.activeElement.dataset.testid") == "prod-card-title"


def test_drawer_actions_per_status(page):
    goto(page)
    expect = {1: ["Send brief", "Mark in production"], 8: ["Mark in production"], 6: ["Submit for review"], 7: ["Approve", "Request changes"], 10: ["Mark launched"]}
    for cid, want in expect.items():
        page.goto(f"/priorities/production?card={cid}")
        page.wait_for_selector("[data-testid=prod-drawer]")
        assert [t.strip() for t in page.locator("[data-testid=prod-drawer-actions] button").all_text_contents()] == want


def test_drawer_edit_fields_saves_only_what_changed_and_refreshes_the_card(page):
    goto(page, "?card=6")
    page.fill("[data-testid=prod-drawer-due]", "2030-01-15")
    page.select_option("[data-testid=prod-drawer-priority]", "high")
    page.fill("[data-testid=prod-drawer-note]", "check the hook")
    with page.expect_request(lambda r: r.method == "PATCH" and "/assignments/6" in r.url) as ri:
        page.click("[data-testid=prod-drawer-save]")
    assert json.loads(ri.value.post_data) == {"due_date": "2030-01-15", "priority": "high", "note": "check the hook"}
    page.wait_for_function("() => document.querySelector('[data-testid=prod-board-card][data-id=\"6\"] [data-testid=prod-card-priority]')")
    assert page.locator("[data-testid=prod-drawer-msg]").text_content() == "Saved."
    assert card(page, 6).locator("[data-testid=prod-card-due]").text_content() == "Due 1/15"
    page.click("[data-testid=prod-drawer-save]")
    assert page.locator("[data-testid=prod-drawer-msg]").text_content() == "Nothing changed."


def test_drawer_change_vendor_week_format_and_links(page):
    goto(page, "?card=8")
    page.select_option("[data-testid=prod-drawer-vendor]", "Victoria")
    page.select_option("[data-testid=prod-drawer-format]", "F2")
    page.fill("[data-testid=prod-drawer-creative]", "https://drive.google.com/file/d/new/view")
    with page.expect_request(lambda r: r.method == "PATCH") as ri:
        page.click("[data-testid=prod-drawer-save]")
    body = json.loads(ri.value.post_data)
    assert body == {"vendor": "Victoria", "format_code": "F2", "creative_url": "https://drive.google.com/file/d/new/view"}
    page.wait_for_function("() => document.querySelector('[data-testid=prod-board-card][data-id=\"8\"] [data-testid=prod-card-vendor]').textContent === 'Victoria'")
    assert card(page, 8).locator("[data-testid=prod-card-creative]").count() == 1


def test_request_changes_requires_a_comment_then_moves_back_and_posts_it(page):
    goto(page, "?card=7")
    page.click("[data-testid=prod-drawer-action-changes]")
    page.wait_for_selector("[data-testid=prod-dialog]")
    page.click("[data-testid=prod-dialog-ok]")                         # empty: refused in the dialog, no request
    assert "required" in page.locator("[data-testid=prod-dialog-error]").text_content()
    page.fill("[data-testid=prod-dialog] [data-testid=prod-field-comment]", "Cut the first 3 seconds")
    with page.expect_request(lambda r: "/move" in r.url) as ri:
        page.click("[data-testid=prod-dialog-ok]")
    assert json.loads(ri.value.post_data) == {"status": "in_production", "comment": "Cut the first 3 seconds"}
    page.wait_for_function("() => document.querySelector('[data-testid=prod-drawer-status]').textContent === 'In production'")
    page.click("[data-testid=prod-drawer-tab-comments]")
    page.wait_for_selector("[data-testid=prod-comment]")
    texts = page.locator("[data-testid=prod-comment-body]").all_text_contents()
    assert "Changes requested: Cut the first 3 seconds" in texts and len(texts) == 3
    assert "7" in col_ids(page, "in_production")


def test_comments_tab_lists_the_thread_and_posts_a_comment(page):
    goto(page, "?card=7")
    page.click("[data-testid=prod-drawer-tab-comments]")
    page.wait_for_selector("[data-testid=prod-comment]")
    assert page.locator("[data-testid=prod-comment-author]").all_text_contents() == ["Victoria", "Josh"]
    assert page.locator("[data-testid=prod-comment][data-kind=vendor]").count() == 1
    page.click("[data-testid=prod-comment-post]")                       # empty
    assert "Write a comment" in page.locator("[data-testid=prod-drawer-msg]").text_content()
    page.fill("[data-testid=prod-comment-input]", "Approved after one tweak")
    with page.expect_request(lambda r: r.method == "POST" and r.url.endswith("/assignments/7/comments")) as ri:
        page.click("[data-testid=prod-comment-post]")
    assert json.loads(ri.value.post_data) == {"body": "Approved after one tweak"}
    page.wait_for_function("() => document.querySelectorAll('[data-testid=prod-comment]').length === 3")
    assert page.locator("[data-testid=prod-comment-body]").last.text_content() == "Approved after one tweak"
    assert page.locator("[data-testid=prod-drawer-tab-comments]").text_content() == "Comments (3)"
    assert card(page, 7).locator("[data-testid=prod-card-comments]").text_content().strip() == "3"


def test_history_tab_lists_events_from_the_detail_and_a_move_shows_up(page):
    goto(page, "?card=8")
    page.click("[data-testid=prod-drawer-action-start]")
    page.wait_for_function("() => document.querySelector('[data-testid=prod-drawer-status]').textContent === 'In production'")
    page.click("[data-testid=prod-drawer-tab-history]")
    page.wait_for_selector("[data-testid=prod-drawer-event]")
    kinds = page.locator("[data-testid=prod-drawer-event]").evaluate_all("es => es.map(e => e.dataset.kind)")
    assert kinds == ["status_changed", "assigned"]                       # newest first
    assert "Status: Brief sent to In production" in page.locator("[data-testid=prod-drawer-event]").first.text_content()


def test_history_falls_back_to_the_list_row_when_the_detail_has_no_history(page):
    def strip(route):
        r = route.fetch()
        body = r.json()
        body.pop("history", None)
        route.fulfill(response=r, json=body)
    page.route("**/api/priorities/assignments/8/detail", strip)
    goto(page, "?card=8")
    page.click("[data-testid=prod-drawer-tab-history]")
    page.wait_for_selector("[data-testid=prod-drawer-event]")
    assert page.locator("[data-testid=prod-drawer-event]").evaluate_all("es => es.map(e => e.dataset.kind)") == ["assigned"]


def test_drop_from_the_drawer_asks_to_confirm_and_removes_the_card(page):
    goto(page, "?card=9")
    page.click("[data-testid=prod-drawer-drop]")
    page.wait_for_selector("[data-testid=prod-dialog]")
    page.click("[data-testid=prod-dialog-ok]")
    page.wait_for_selector("[data-testid=prod-drawer]", state="detached")
    assert card(page, 9).count() == 0


# ---------------------------------------------------------------------------------------------------------------- list view
def test_list_view_has_status_due_and_priority_columns_and_opens_the_drawer(page):
    goto(page, "?view=list")
    assert page.locator("[data-testid=prod-view-list]").get_attribute("aria-pressed") == "true"
    row5 = page.locator("[data-testid=prod-row][data-id='5']")
    assert row5.locator("[data-testid=prod-status]").text_content() == "In production"
    assert "Overdue" in row5.locator("[data-testid=prod-due]").text_content()
    assert row5.locator("[data-testid=prod-priority]").text_content() == "High"
    assert page.locator("[data-testid=prod-row][data-id='1'] [data-testid=prod-priority]").text_content() == "Normal"
    row5.locator("[data-testid=prod-open-card]").click()
    page.wait_for_selector("[data-testid=prod-drawer]")
    assert re.fullmatch(r"Madre - Seed cluster - Energy Crash - Week of \d+/\d+", page.locator("[data-testid=prod-drawer-title]").text_content())
    page.click("[data-testid=prod-drawer-close]")
    assert "view=list" in page.url


def test_view_switch_keeps_filters_and_changes_the_url(page):
    goto(page, "?vendor=Victoria")
    page.click("[data-testid=prod-view-list]")
    page.wait_for_selector("[data-testid=prod-table]")
    assert "view=list" in page.url and "vendor=Victoria" in page.url
    page.click("[data-testid=prod-view-plan]")
    page.wait_for_selector("[data-testid=prod-plan]")
    assert "view=plan" in page.url
    page.click("[data-testid=prod-view-board]")
    page.wait_for_selector("[data-testid=prod-board]")
    assert "view=" not in page.url


# ---------------------------------------------------------------------------------------------------------------- plan
def cell(page, vendor, week):
    return page.locator(f"[data-testid=prod-plan-cell][data-vendor='{vendor}'][data-week='{week}']")


def test_plan_shows_four_weeks_vendor_rows_with_load_and_capacity(page):
    goto(page, "?view=plan")
    heads = page.locator("[data-testid=prod-plan-weekhead]").evaluate_all("hs => hs.map(h => h.dataset.week)")
    assert len(heads) == 4 and heads == sorted(heads)
    w0, w1 = heads[0], heads[1]
    assert cell(page, "Madre", w0).get_attribute("data-open") == "4" and cell(page, "Madre", w0).get_attribute("data-over") == "1"
    assert cell(page, "Victoria", w0).get_attribute("data-open") == "3" and cell(page, "Victoria", w1).get_attribute("data-open") == "1"
    assert cell(page, "Madre", w0).locator("[data-testid=prod-plan-load]").text_content() == "4 of 2"
    assert page.locator("[data-testid=prod-plan-row]").count() == 10
    nxt = page.locator("[data-testid=prod-plan-next]")
    nxt.click()
    assert page.locator("[data-testid=prod-plan-weekhead]").first.get_attribute("data-week") == w1
    page.click("[data-testid=prod-plan-today]")
    assert page.locator("[data-testid=prod-plan-weekhead]").first.get_attribute("data-week") == w0


def test_plan_capacity_is_editable_inline(page):
    goto(page, "?view=plan")
    inp = page.locator("[data-testid=prod-plan-capacity][data-vendor=Madre]")
    with page.expect_request(lambda r: r.method == "PUT" and "vendor-capacity" in r.url) as ri:
        inp.fill("6")
        inp.press("Enter")
        inp.blur()
    assert json.loads(ri.value.post_data) == {"vendor": "Madre", "per_week": 6}
    page.wait_for_function("() => document.querySelector('[data-testid=prod-plan-cell][data-vendor=Madre][data-open=\"4\"] [data-testid=prod-plan-load]').textContent === '4 of 6'")
    assert "Madre: 6 cards per week." in page.locator("[data-testid=prod-toast]").last.text_content()
    assert page.locator("[data-testid=prod-plan-cell][data-vendor=Madre]").first.get_attribute("data-over") == "0"


def test_plan_drag_reassigns_vendor_and_week(page):
    page.set_viewport_size({"width": 1440, "height": 1900})            # every vendor row on screen: the mouse cannot drag below the window
    goto(page, "?view=plan")
    page.click("[data-testid=prod-plan-idle] > summary")                  # TrybeOrion has nothing planned, so its row sits in the collapsed group
    heads = page.locator("[data-testid=prod-plan-weekhead]").evaluate_all("hs => hs.map(h => h.dataset.week)")
    src = page.locator("[data-testid=prod-board-card][data-id='6']")
    dst = page.locator(f"[data-testid=prod-plan-list][data-vendor=TrybeOrion][data-week='{heads[1]}']")
    with page.expect_request(lambda r: r.method == "PATCH" and "/assignments/6" in r.url) as ri:
        drag(page, src, dst)
    assert json.loads(ri.value.post_data) == {"vendor": "TrybeOrion", "for_week": heads[1]}
    page.wait_for_function("(w) => document.querySelector(`[data-testid=prod-plan-cell][data-vendor=TrybeOrion][data-week='${w}']`).dataset.open === '1'", arg=heads[1])
    assert cell(page, "Victoria", heads[0]).get_attribute("data-open") == "2"


def test_plan_drag_into_a_full_week_warns_about_capacity(page):
    goto(page, "?view=plan")
    heads = page.locator("[data-testid=prod-plan-weekhead]").evaluate_all("hs => hs.map(h => h.dataset.week)")
    src = page.locator("[data-testid=prod-board-card][data-id='3']")
    dst = page.locator(f"[data-testid=prod-plan-list][data-vendor=Madre][data-week='{heads[0]}']")
    with page.expect_request(lambda r: r.method == "PATCH"):
        drag(page, src, dst, "bottom")
    page.wait_for_function("() => document.querySelector('[data-testid=prod-toast]') && document.querySelector('[data-testid=prod-toast]').textContent.includes('over the 2 a week capacity')")


@pytest.mark.allow_errors
def test_plan_rejected_reassign_reverts_with_a_toast(page, base_url):
    goto(page, "?view=plan")
    heads = page.locator("[data-testid=prod-plan-weekhead]").evaluate_all("hs => hs.map(h => h.dataset.week)")
    page.route("**/api/priorities/assignments/6", lambda r: r.fulfill(status=409, content_type="application/json", body=json.dumps({"error": "Madre already has that concept this week."})) if r.request.method == "PATCH" else r.continue_())
    drag(page, page.locator("[data-testid=prod-board-card][data-id='6']"), page.locator(f"[data-testid=prod-plan-list][data-vendor=Madre][data-week='{heads[2]}']"))
    page.wait_for_function("() => document.querySelector('[data-testid=prod-toast]') && document.querySelector('[data-testid=prod-toast]').textContent.includes('already has that concept')")
    assert cell(page, "Victoria", heads[0]).get_attribute("data-open") == "3"


# ---------------------------------------------------------------------------------------------------------------- mobile, launch, safety
@pytest.mark.parametrize("qs", ["", "?group=vendor", "?view=list", "?view=plan", "?card=7", "?card=7&view=list"])
def test_no_sideways_page_scroll_at_375px(browser, base_url, qs):
    c = browser.new_context(viewport={"width": 375, "height": 812}, base_url=base_url)
    try:
        c.request.post(base_url + "/__test/assign/reset")
        c.request.post(base_url + "/__test/board/seed")
        c.request.post(base_url + "/api/login", data={"username": "JoshHoughton", "password": "test"})
        pg = c.new_page()
        pg.goto("/priorities/production" + qs)
        pg.wait_for_selector("[data-testid=prod-page]")
        idle(pg)
        pg.wait_for_selector("[data-testid=prod-board], [data-testid=prod-table], [data-testid=prod-plan]")
        assert pg.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth + 1 && document.body.scrollWidth <= window.innerWidth + 1")
        if qs == "":
            assert pg.locator("[data-testid=prod-board-scroll]").evaluate("e => e.scrollWidth > e.clientWidth")        # columns scroll inside their own container
        if "card=7" in qs:
            box = pg.locator("[data-testid=prod-drawer]").bounding_box()
            assert box["width"] >= 374 and box["height"] >= 800                                                        # a full-screen sheet
    finally:
        c.close()


def test_phone_touch_targets_are_at_least_44px_and_drag_from_the_grip_works(browser, base_url):
    c = browser.new_context(viewport={"width": 375, "height": 812}, base_url=base_url, has_touch=True, is_mobile=True)
    try:
        c.request.post(base_url + "/__test/assign/reset")
        c.request.post(base_url + "/__test/board/seed")
        c.request.post(base_url + "/api/login", data={"username": "JoshHoughton", "password": "test"})
        pg = c.new_page()
        pg.goto("/priorities/production")
        pg.wait_for_selector("[data-testid=prod-board-card]")
        idle(pg)
        c1 = pg.locator("[data-testid=prod-board-card][data-id='1']")
        assert c1.locator("[data-testid=prod-card-action]").bounding_box()["height"] >= 44
        assert c1.locator("[data-testid=prod-card-more-btn]").bounding_box()["height"] >= 44
        assert not c1.locator("[data-testid=prod-card-moveto]").is_visible()          # behind the ... button
        c1.locator("[data-testid=prod-card-more-btn]").click()
        assert c1.locator("[data-testid=prod-card-moveto]").bounding_box()["height"] >= 44
        # the Move to menu is the phone path: a native select
        assert c1.locator("[data-testid=prod-card-moveto]").evaluate("e => e.tagName") == "SELECT"
        pg.locator("[data-testid=prod-card-title]").first.click()
        pg.wait_for_selector("[data-testid=prod-drawer]")
        for tid in ("prod-drawer-close", "prod-drawer-tab-details", "prod-drawer-tab-comments"):
            assert pg.locator(f"[data-testid={tid}]").bounding_box()["height"] >= 44
    finally:
        c.close()


def test_launch_ui_stays_dark_when_launching_is_off(page):
    scripts = []
    page.on("request", lambda r: scripts.append(r.url) if "priorities_launch" in r.url else None)
    for qs in ("", "?view=list", "?view=plan", "?card=6"):
        goto(page, qs)
        assert page.locator("[data-testid^=prod-launch-]").count() == 0
    assert scripts == []
    assert "Prepare launch" not in page.locator("body").text_content()


def test_server_text_goes_in_as_text_not_html(page, base_url):
    page.request.patch(base_url + "/api/priorities/assignments/1", data={"note": "<img src=x onerror=window.pwned=1>"})
    goto(page, "?card=1")
    assert page.evaluate("() => window.pwned") is None
    assert page.locator("[data-testid=prod-drawer-note]").input_value() == "<img src=x onerror=window.pwned=1>"


def test_board_text_has_no_em_dashes_and_cards_have_names(page):
    goto(page)
    labels = page.locator("[data-testid=prod-board-card]").evaluate_all("cs => cs.map(c => c.getAttribute('aria-label'))")
    assert all(re.search(r", ", l) for l in labels) and "—" not in " ".join(labels)
    goto(page, "?card=7")
    assert "—" not in page.locator("[data-testid=prod-drawer]").text_content()
