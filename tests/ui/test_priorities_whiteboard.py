"""Creative priorities Board (/priorities/board) against the mock (tests/ui/mock_whiteboard.py): render, real-mouse drag and drop (backlog to a lane, reorder, other lane, remove with Undo and Restore),
the lifted copy moving by transform only, keyboard pick up / move / drop, saving and reload, filters, a failed save with Retry, other people's changes, and a frame-time probe over 250 concepts."""
import json
import re
import statistics

import pytest

pytestmark = pytest.mark.mock_only

WEEK = "2026-11-23"
LANE_IDS = ["must", "if_time", "parked"]


@pytest.fixture(autouse=True)
def fresh(ctx, base_url):
    ctx.request.post(base_url + "/__test/whiteboard/reset")
    ctx.request.post(base_url + "/__test/assign/reset")


def idle(page):
    page.wait_for_function("() => !document.querySelector('#view').hasAttribute('aria-busy')")


def goto(page, qs=""):
    page.goto(f"/priorities/board?week={WEEK}{qs}")
    page.wait_for_selector("[data-testid=pri-wb-page]")
    idle(page)
    page.wait_for_selector("[data-testid=pri-wb-card]")
    page.wait_for_function("() => !document.querySelector('.wb-skel')")
    page.wait_for_function("() => document.querySelector('[data-testid=pri-wb-save]').dataset.state === 'saved'")


def lane(page, name):
    return page.locator(f"[data-testid=pri-wb-list][data-lane={name}]")


def keys(page, name):
    return lane(page, name).locator(".wb-bcard").evaluate_all("cs => cs.map(c => c.dataset.key)")


def titles(page, name):
    return lane(page, name).locator("[data-testid=pri-wb-btitle]").all_inner_texts()


def card(page, i):
    return page.locator("[data-testid=pri-wb-card]:visible").nth(i)


def settle(page):
    """the save went out and came back"""
    page.wait_for_timeout(420)                                                  # the lifted copy settling (180 ms), FLIP, the ring flash and the numbers rolling (150 ms)
    page.wait_for_function("() => document.querySelector('[data-testid=pri-wb-save]').dataset.state === 'saved'")
    page.wait_for_timeout(60)


def server_state(ctx, base_url):
    return ctx.request.get(f"{base_url}/api/priorities/whiteboard?week={WEEK}").json()


def drag(page, src, dst, where="center", steps=14, hold=False):
    """A real mouse drag (down, many moves, up) from the middle of `src` to the middle of `dst` (or its top / bottom edge)."""
    a = src.bounding_box()
    b = dst.bounding_box()
    y = b["y"] + (4 if where == "top" else b["height"] - 4 if where == "bottom" else b["height"] / 2)
    page.mouse.move(a["x"] + a["width"] / 2, a["y"] + a["height"] / 2)
    page.mouse.down()
    page.mouse.move(a["x"] + a["width"] / 2 + 8, a["y"] + a["height"] / 2 + 8, steps=3)
    page.mouse.move(b["x"] + b["width"] / 2, y, steps=steps)
    if not hold:
        page.mouse.up()


def add(page, i, lane_name="must"):
    """put the i-th visible backlog card on the board the quick way (double click appends to Must do; keys 1 to 3 go to the top of a lane)"""
    c = card(page, i)
    if lane_name == "must":
        c.dblclick()
    else:
        c.focus()
        page.keyboard.press({"must": "1", "if_time": "2", "parked": "3"}[lane_name])
    settle(page)


def test_renders_backlog_lanes_and_totals(page, ctx, base_url):
    goto(page)
    rows = [r for r in ctx.request.get(base_url + "/api/priorities").json()["rows"] if r.get("line_code") != "no line" and not (r.get("manual") and r.get("done"))]
    assert page.locator("[data-testid=pri-wb-card]").count() == len(rows)
    assert page.locator("[data-testid=pri-wb-count]").inner_text() == f"{len(rows)} of {len(rows)}"
    assert page.locator("[data-testid=pri-wb-lane]").count() == 3
    assert page.locator("[data-testid=pri-wb-lane-empty]").count() == 3
    assert page.locator("[data-testid=pri-wb-total-n]").inner_text() == "0"
    assert page.locator("[data-testid=pri-wb-week-label]").inner_text() == "Week of Nov 23"
    assert page.locator("[data-testid=pri-wb-cap-text]").inner_text() == "0 of 8"
    assert "Table" in page.locator("[data-testid=pri-wb-mode-table]").inner_text()
    # one screen: the page itself does not scroll
    assert page.evaluate("document.documentElement.scrollHeight <= window.innerHeight + 2")
    # each backlog card is compact and carries the cluster colour bar
    box = card(page, 0).bounding_box()
    assert 80 <= box["height"] <= 84
    assert card(page, 0).locator("[data-testid=pri-wb-story]").is_visible()


def cluster_group(page, i=0):
    return page.locator("[data-testid=pri-wb-cgroup]:visible").nth(i)


def test_backlog_nests_cluster_icp_and_pain_point_with_stories(page):
    goto(page)
    assert page.locator("[data-testid=pri-wb-group]").get_attribute("aria-pressed") == "true"
    cg = cluster_group(page)
    head = cg.locator(".wb-ch")
    assert head.locator("[data-testid=pri-wb-chead]").inner_text().strip()
    assert int(head.locator("[data-testid=pri-wb-chead-count]").inner_text()) == cg.locator("[data-testid=pri-wb-card]:visible").count()
    assert cg.locator("[data-testid=pri-wb-ihead]").count() >= 1
    ig = cg.locator("[data-testid=pri-wb-igroup]").first
    assert ig.locator("[data-testid=pri-wb-ihead]").count() == 1 and ig.locator("[data-testid=pri-wb-card]").count() >= 1
    c = ig.locator("[data-testid=pri-wb-card]").first
    assert c.locator("[data-testid=pri-wb-story]").is_visible()                                      # the story is on the card
    assert ">" not in c.locator(".wb-cl").text_content()                                              # cluster and ICP live in the headers when grouped
    bx, ix = cg.bounding_box(), ig.bounding_box()
    assert ix["x"] > bx["x"] + 8                                                                     # ICP group is indented under the cluster
    assert 80 <= c.bounding_box()["height"] <= 84
    # a board card carries the cluster as a tag
    c.dblclick()
    settle(page)
    assert page.locator("[data-testid=pri-wb-bcard] [data-testid=pri-wb-ctag]").first.inner_text().strip()


def test_add_all_and_add_icp_one_save_one_undo(page, ctx, base_url):
    goto(page)
    cg = cluster_group(page)
    n = int(cg.locator("[data-testid=pri-wb-chead-count]").inner_text())
    btn = cg.locator("[data-testid=pri-wb-addall]")
    assert btn.inner_text() == f"+ All {n}"
    keys_in = cg.locator("[data-testid=pri-wb-card]").evaluate_all("cs => cs.map(c => c.dataset.key)")
    before = len(server_state(ctx, base_url)["picks"])
    btn.click()
    settle(page)
    assert keys(page, "must") == keys_in                                                              # appended to the bottom, in the backlog's order
    assert page.locator("[data-testid=pri-wb-toast]").inner_text().startswith(f"Added {n} from")
    assert before == 0 and len(server_state(ctx, base_url)["picks"]) == n
    assert btn.is_disabled() and btn.inner_text() == "+ All 0"
    page.locator("body").click(position={"x": 5, "y": 5})
    page.keyboard.press("Control+z")                                                                  # one undo step takes all of them back
    settle(page)
    assert keys(page, "must") == [] and btn.inner_text() == f"+ All {n}"
    # + ICP adds only that profile
    ig = cg.locator("[data-testid=pri-wb-igroup]").first
    ni = ig.locator("[data-testid=pri-wb-card]").count()
    ig.locator("[data-testid=pri-wb-addicp]").click()
    settle(page)
    assert len(keys(page, "must")) == ni and page.locator("[data-testid=pri-wb-toast]").inner_text().startswith(f"Added {ni} from")


def test_filters_shrink_the_add_all_count(page):
    goto(page)
    page.locator("[data-testid=pri-wb-f-sec-test]").click()
    for i in range(page.locator("[data-testid=pri-wb-cgroup]:visible").count()):
        g = cluster_group(page, i)
        shown = g.locator("[data-testid=pri-wb-card]:visible").count()
        assert g.locator("[data-testid=pri-wb-addall]").inner_text() == f"+ All {shown}"
    g = cluster_group(page)
    shown = g.locator("[data-testid=pri-wb-card]:visible").count()
    g.locator("[data-testid=pri-wb-addall]").click()
    settle(page)
    assert len(keys(page, "must")) == shown
    assert page.locator("[data-testid=pri-wb-card][data-section=test]:visible").evaluate_all("cs => true")
    assert all(k in keys(page, "must") for k in g.locator("[data-testid=pri-wb-card]:visible").evaluate_all("cs => cs.map(c => c.dataset.key)"))


def test_table_and_board_link_each_other(page):
    page.goto("/priorities")
    page.wait_for_selector("[data-testid=pri-title]")
    idle(page)
    page.locator("[data-testid=pri-wb-mode-board]").click()
    page.wait_for_selector("[data-testid=pri-wb-page]")
    assert page.url.endswith("/priorities/board")
    page.locator("[data-testid=pri-wb-mode-table]").click()
    page.wait_for_selector("[data-testid=pri-title]")
    assert re.search(r"/priorities/?$", page.url)


def test_drag_backlog_to_must(page, ctx, base_url):
    goto(page)
    src = card(page, 0)
    key = src.get_attribute("data-key")
    pain = src.locator("[data-testid=pri-wb-pain]").inner_text()
    drag(page, src, lane(page, "must"))
    settle(page)
    assert keys(page, "must") == [key]
    assert titles(page, "must") == [pain]
    # the backlog card stays as a faded copy tagged with its lane and cannot be dragged again
    ghost = page.locator(f"[data-testid=pri-wb-card][data-key='{key}']")
    assert "wb-onboard" in ghost.get_attribute("class")
    assert ghost.locator("[data-testid=pri-wb-wk]").inner_text() == "Wk · Must"
    assert page.locator("[data-testid=pri-wb-total-n]").inner_text() == "1"
    assert page.locator("[data-testid=pri-wb-rank]").first.inner_text() == "1"
    assert [p["concept_key"] for p in server_state(ctx, base_url)["picks"]] == [key]
    # nothing is left behind: no lifted copy, no transforms, body cursor back
    assert page.locator(".wb-ghost-drag").count() == 0
    assert page.evaluate("document.querySelectorAll('.wb-bcard').length && [...document.querySelectorAll('.wb-bcard')].every(c => !c.style.transform)")
    assert not page.evaluate("document.body.classList.contains('wb-grabbing')")


def test_reorder_inside_a_lane(page, ctx, base_url):
    goto(page)
    for i in range(3):
        add(page, i)
    before = keys(page, "must")
    assert len(before) == 3
    cards = lane(page, "must").locator(".wb-bcard")
    drag(page, cards.nth(2), cards.nth(0), where="top")
    settle(page)
    assert keys(page, "must") == [before[2], before[0], before[1]]
    assert page.locator("[data-testid=pri-wb-rank]").all_inner_texts() == ["1", "2", "3"]
    assert [p["concept_key"] for p in server_state(ctx, base_url)["picks"]] == [before[2], before[0], before[1]]


def test_drag_to_another_lane_and_total_split(page):
    goto(page)
    add(page, 0)
    add(page, 1)
    moved = keys(page, "must")[0]
    drag(page, lane(page, "must").locator(".wb-bcard").first, lane(page, "if_time"))
    settle(page)
    assert keys(page, "if_time") == [moved] and moved not in keys(page, "must")
    assert page.locator("[data-testid=pri-wb-total-split]").inner_text() == "Must 1 · If time 1"
    # a parked card does not count in the totals, and the rank numbers run across all three lanes
    drag(page, lane(page, "if_time").locator(".wb-bcard").first, lane(page, "parked"))
    settle(page)
    assert keys(page, "parked") == [moved]
    assert page.locator("[data-testid=pri-wb-total-n]").inner_text() == "1"
    assert page.locator("[data-testid=pri-wb-rank]").all_inner_texts() == ["1", "2"]


def test_drag_to_tray_undo_and_restore(page, ctx, base_url):
    goto(page)
    add(page, 0)
    add(page, 1)
    first = keys(page, "must")[0]
    pain = titles(page, "must")[0]
    tray = page.locator("[data-testid=pri-wb-tray-toggle]")
    assert tray.bounding_box()["height"] == 36
    drag(page, lane(page, "must").locator(".wb-bcard").first, tray, hold=True)
    page.wait_for_timeout(250)
    assert 70 <= tray.bounding_box()["height"] <= 74                      # the bar grew to 72px while a card is over the board
    page.mouse.up()
    page.wait_for_timeout(450)
    assert first not in keys(page, "must") and len(keys(page, "must")) == 1
    toast = page.locator("[data-testid=pri-wb-toast]")
    assert toast.inner_text().startswith(f'Removed "{pain}"')
    assert page.locator("[data-testid=pri-wb-tray-n]").inner_text() == "1"
    # undo from the toast: back in its first place
    page.locator("[data-testid=pri-wb-undo]").click()
    settle(page)
    assert keys(page, "must")[0] == first and len(keys(page, "must")) == 2
    assert page.locator("[data-testid=pri-wb-toast]").count() == 0
    # remove again with the keyboard, then Ctrl+Z
    page.locator(f".wb-bcard[data-key='{first}']").focus()
    page.keyboard.press("Delete")
    settle(page)
    assert first not in keys(page, "must")
    page.locator("[data-testid=pri-wb-tray-toggle]").click()
    assert page.locator("[data-testid=pri-wb-dchip]").count() == 1
    assert [p["concept_key"] for p in server_state(ctx, base_url)["dropped"]] == [first]
    # restore from the tray: it leaves the tray and is a normal backlog card again
    page.locator("[data-testid=pri-wb-restore]").click()
    settle(page)
    assert page.locator("[data-testid=pri-wb-dchip]").count() == 0
    assert "wb-onboard" not in page.locator(f"[data-testid=pri-wb-card][data-key='{first}']").get_attribute("class")
    assert server_state(ctx, base_url)["dropped"] == []
    page.keyboard.press("Control+z")                                       # undo the restore: the chip is back
    settle(page)
    assert page.locator("[data-testid=pri-wb-tray-n]").inner_text() == "1"


def test_lifted_copy_moves_by_transform_only(page):
    goto(page)
    src = card(page, 0)
    drag(page, src, lane(page, "must"), hold=True)
    g = page.locator(".wb-ghost-drag")
    assert g.count() == 1
    st = g.evaluate("e => ({left: e.style.left, top: e.style.top, tf: e.style.transform, pos: getComputedStyle(e).position, pe: getComputedStyle(e).pointerEvents})")
    assert st["left"] == "" and st["top"] == "" and st["pos"] == "fixed" and st["pe"] == "none"
    assert "translate3d" in st["tf"] and "rotate(1.5deg)" in st["tf"]
    t1 = st["tf"]
    page.mouse.move(700, 400, steps=6)
    page.wait_for_timeout(60)
    assert g.evaluate("e => e.style.transform") != t1 and g.evaluate("e => e.style.left") == ""
    # the source stays as a dashed placeholder; the lane under the pointer is outlined
    assert "wb-placeholder" in src.get_attribute("class")
    assert page.locator("[data-testid=pri-wb-lane].wb-lane-over").count() <= 1
    page.mouse.move(*[(lane(page, "must").bounding_box()[k]) for k in ("x",)] + [lane(page, "must").bounding_box()["y"] + 30], steps=4)
    page.mouse.move(lane(page, "must").bounding_box()["x"] + 200, lane(page, "must").bounding_box()["y"] + 30, steps=4)
    page.wait_for_timeout(80)
    assert page.locator("[data-testid=pri-wb-lane][data-lane=must].wb-lane-over").count() == 1
    page.keyboard.press("Escape")                                          # cancel: nothing is added
    page.wait_for_timeout(300)
    assert page.locator(".wb-ghost-drag").count() == 0 and keys(page, "must") == []
    assert "wb-placeholder" not in src.get_attribute("class")


def test_keyboard_pick_up_move_and_drop(page, ctx, base_url):
    goto(page)
    add(page, 0)
    add(page, 1)
    c = card(page, 2)
    key = c.get_attribute("data-key")
    c.focus()
    page.keyboard.press("Space")
    assert "Picked up" in page.locator("[data-testid=pri-wb-live]").inner_text()
    page.keyboard.press("ArrowRight")                                      # backlog to Must do (end of the lane)
    assert "Must do, position 3 of 3" in page.locator("[data-testid=pri-wb-live]").inner_text()
    page.keyboard.press("ArrowUp")
    assert "position 2 of 3" in page.locator("[data-testid=pri-wb-live]").inner_text()
    page.keyboard.press("Enter")
    settle(page)
    assert keys(page, "must")[1] == key and len(keys(page, "must")) == 3
    # the dropped card keeps focus; move it down a lane, then cancel a second pick
    page.locator(f".wb-bcard[data-key='{key}']").focus()
    page.keyboard.press("Space")
    page.keyboard.press("ArrowRight")
    page.keyboard.press("Enter")
    settle(page)
    assert keys(page, "if_time") == [key]
    page.locator(f".wb-bcard[data-key='{key}']").focus()
    page.keyboard.press("Space")
    page.keyboard.press("ArrowRight")
    page.keyboard.press("Escape")
    settle(page)
    assert keys(page, "if_time") == [key] and keys(page, "parked") == []
    # shortcuts on a board card: ] goes to the next lane, 1 to the top of Must do
    page.locator(f".wb-bcard[data-key='{key}']").focus()
    page.keyboard.press("]")
    settle(page)
    assert keys(page, "parked") == [key]
    page.locator(f".wb-bcard[data-key='{key}']").focus()
    page.keyboard.press("1")
    settle(page)
    assert keys(page, "must")[0] == key
    assert [p["concept_key"] for p in server_state(ctx, base_url)["picks"]][0] == key


def test_persists_across_reload_with_notes(page):
    goto(page)
    add(page, 0)
    add(page, 1, "if_time")
    add(page, 2, "parked")
    must, ift, parked = keys(page, "must"), keys(page, "if_time"), keys(page, "parked")
    bc = lane(page, "must").locator(".wb-bcard").first
    bc.dblclick()
    page.locator("[data-testid=pri-wb-note-input]").fill("brief the hook first")
    page.keyboard.press("Enter")
    settle(page)
    assert page.locator("[data-testid=pri-wb-note]").first.inner_text() == "brief the hook first"
    page.reload()
    page.wait_for_selector("[data-testid=pri-wb-bcard]")
    page.wait_for_function("() => !document.querySelector('.wb-skel')")
    assert (keys(page, "must"), keys(page, "if_time"), keys(page, "parked")) == (must, ift, parked)
    assert page.locator("[data-testid=pri-wb-note]:visible").first.inner_text() == "brief the hook first"
    # the next week is empty, and going back brings the board back
    page.locator("[data-testid=pri-wb-week-next]").click()
    page.wait_for_function("() => document.querySelector('[data-testid=pri-wb-week-label]').textContent === 'Week of Nov 30'")
    page.wait_for_function("() => document.querySelectorAll('[data-testid=pri-wb-bcard]').length === 0")
    page.locator("[data-testid=pri-wb-week-prev]").click()
    page.wait_for_function("() => document.querySelectorAll('[data-testid=pri-wb-bcard]').length === 3")


def test_filters_with_a_populated_board(page):
    goto(page)
    total = page.locator("[data-testid=pri-wb-card]").count()
    add(page, 0)
    add(page, 1)
    first_pain = keys(page, "must")[0]
    s = page.locator("[data-testid=pri-wb-search]")
    s.fill("zzzz-no-such-concept")
    assert page.locator("[data-testid=pri-wb-count]").inner_text() == f"0 of {total}"
    assert page.locator("[data-testid=pri-wb-nomatch]").is_visible()
    assert page.locator("[data-testid=pri-wb-bcard]").count() == 2         # the board is not filtered
    s.fill("")
    page.locator("[data-testid=pri-wb-f-sec-test]").click()
    shown = page.locator("[data-testid=pri-wb-card]:visible").count()
    assert 0 < shown < total
    assert page.locator("[data-testid=pri-wb-card]:visible").evaluate_all("cs => cs.every(c => c.dataset.section === 'test')")
    assert page.locator("[data-testid=pri-wb-count]").inner_text() == f"{shown} of {total}"
    page.locator("[data-testid=pri-wb-f-sec-test]").click()
    assert page.locator("[data-testid=pri-wb-group]").get_attribute("aria-pressed") == "true"      # grouped by default: sticky cluster headers that collapse
    heads = page.locator("[data-testid=pri-wb-chead]:visible")
    assert heads.count() >= 2
    n = page.locator("[data-testid=pri-wb-card]:visible").count()
    heads.first.click()
    assert page.locator("[data-testid=pri-wb-card]:visible").count() < n
    assert page.locator("[data-testid=pri-wb-ihead]:visible").count() < page.locator("[data-testid=pri-wb-ihead]").count()
    heads.first.click()
    page.locator("[data-testid=pri-wb-group]").click()                     # off: flat list, no headers, Cluster > ICP on each card
    assert page.locator("[data-testid=pri-wb-chead]").count() == 0
    assert page.locator("[data-testid=pri-wb-card]:visible").count() == total
    assert ">" in card(page, 0).locator(".wb-cl").text_content()
    page.locator("[data-testid=pri-wb-group]").click()
    # "/" jumps to the search box
    page.locator("body").click(position={"x": 5, "y": 5})
    page.keyboard.press("/")
    assert page.evaluate("document.activeElement && document.activeElement.dataset.testid") == "pri-wb-search"
    assert first_pain in keys(page, "must")


@pytest.mark.allow_errors
def test_failed_save_shows_retry_and_keeps_the_board(page, ctx, base_url):
    goto(page)
    ctx.request.post(base_url + "/__test/whiteboard/fail?n=1")
    add_key = card(page, 0).get_attribute("data-key")
    card(page, 0).dblclick()
    page.wait_for_function("() => document.querySelector('[data-testid=pri-wb-save]').dataset.state === 'error'")
    assert page.locator("[data-testid=pri-wb-save]").inner_text() == "Not saved, retry"
    assert keys(page, "must") == [add_key]                                 # the local board is kept
    assert server_state(ctx, base_url)["picks"] == []
    page.locator("[data-testid=pri-wb-save]").click()
    settle(page)
    assert [p["concept_key"] for p in server_state(ctx, base_url)["picks"]] == [add_key]


def test_other_peoples_changes_arrive_by_polling(page, ctx, base_url):
    goto(page)
    target = card(page, 3)
    key = target.get_attribute("data-key")
    r = ctx.request.put(f"{base_url}/api/priorities/whiteboard", data=json.dumps({"week": WEEK, "picks": [{"concept_key": key, "lane": "if_time", "pain_point": "from someone else", "cluster": "x", "icp": "y", "product": "vita-bull"}], "dropped": []}),
                        headers={"Content-Type": "application/json"})
    assert r.status == 200
    page.wait_for_function("(k) => document.querySelector(`[data-testid=pri-wb-list][data-lane=if_time] .wb-bcard[data-key=\"${k}\"]`)", arg=key, timeout=12000)
    assert keys(page, "if_time") == [key]


def test_capacity_target_editable_and_copy_list(page):
    goto(page)
    add(page, 0)
    add(page, 1)
    page.locator("[data-testid=pri-wb-cap]").click()
    page.locator("[data-testid=pri-wb-cap-input]").fill("2")
    page.keyboard.press("Enter")
    assert page.locator("[data-testid=pri-wb-cap-text]").inner_text() == "2 of 2"
    assert page.locator("[data-testid=pri-wb-cap]").get_attribute("data-state") == "full"
    add(page, 2)
    assert page.locator("[data-testid=pri-wb-cap]").get_attribute("data-state") == "over"
    page.locator("[data-testid=pri-wb-copy]").click()
    assert page.locator("[data-testid=pri-wb-toast]").inner_text().startswith("Copied")


def test_narrow_screen_uses_tabs(ctx, page):
    page.set_viewport_size({"width": 700, "height": 900})
    goto(page)
    assert page.locator("[data-testid=pri-wb-week-pane]").is_hidden()
    card(page, 0).locator("[data-testid=pri-wb-add]").click()
    settle(page)
    assert page.locator("[data-testid=pri-wb-pill]").inner_text() == "This week (1)"
    page.locator("[data-testid=pri-wb-pill]").click()
    assert page.locator("[data-testid=pri-wb-backlog-pane]").is_hidden() and page.locator("[data-testid=pri-wb-bcard]").count() == 1
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def big_payload(base):
    rows = base["rows"]
    out = []
    for i in range(250):
        r = dict(rows[i % len(rows)])
        r["pain_point"] = f"{r['pain_point']} #{i}"
        r["ckey"] = f"text:perf|{i}"
        r["key"] = f"perf|{i}"
        r["line_code"] = f"P{i:03d}"
        r.pop("manual", None)
        out.append(r)
    return {**base, "rows": out}


def test_frame_time_over_250_concepts(page, ctx, base_url):
    """rAF deltas inside the page during a scripted real-mouse drag over 250 backlog cards; the numbers are printed (run with -s) and asserted against the spec: median under 17 ms, p95 under 25 ms."""
    base = ctx.request.get(base_url + "/api/priorities").json()
    body = json.dumps(big_payload(base))
    page.route(re.compile(r".*/api/priorities(\?.*)?$"), lambda route: route.fulfill(status=200, content_type="application/json", body=body))
    goto(page)
    assert page.locator("[data-testid=pri-wb-card]").count() == 250
    for i in range(4):
        add(page, i)
    src = card(page, 4).bounding_box()
    must = lane(page, "must").bounding_box()
    ift = lane(page, "if_time").bounding_box()
    page.evaluate("""() => { window.__d = []; let last = performance.now(); window.__run = true;
        const f = (t) => { if (!window.__run) return; window.__d.push(t - last); last = t; requestAnimationFrame(f); }; requestAnimationFrame(f); }""")
    page.mouse.move(src["x"] + 100, src["y"] + 20)
    page.mouse.down()
    pts = []
    for i in range(140):                                                    # down through Must do and If time, up again, over the tray edge of the board
        t = i / 139
        x = must["x"] + 120 + 160 * (0.5 - 0.5 * __import__("math").cos(t * 6.28))
        y = must["y"] + 40 + (ift["y"] + 200 - must["y"]) * (0.5 - 0.5 * __import__("math").cos(t * 3.14159 * 2))
        pts.append((x, y))
    for x, y in pts:
        page.mouse.move(x, y)
        page.wait_for_timeout(8)
    page.mouse.up()
    page.wait_for_timeout(300)
    d = page.evaluate("() => { window.__run = false; return window.__d; }")[5:]
    d.sort()
    med = statistics.median(d)
    p95 = d[int(len(d) * 0.95) - 1]
    print(f"\nframe time over 250 concepts: frames={len(d)} median={med:.1f}ms p95={p95:.1f}ms max={d[-1]:.1f}ms")
    assert len(d) > 60
    assert med < 17, f"median frame {med:.1f} ms"
    assert p95 < 25, f"p95 frame {p95:.1f} ms"
    assert len(keys(page, "must")) + len(keys(page, "if_time")) == 5
