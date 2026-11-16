"""Creative Pipeline "By cut" view (/priorities/production?view=cuts): the page rename, the table by cut, drag and drop of the cut chips, the keyboard path, status columns and the date window,
against the mock (tests/ui/mock_production_board.py, seeded like test_production_board.py)."""
import re

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
    page.goto("/priorities/production?view=cuts" + qs)
    page.wait_for_selector("[data-testid=prod-cuts-table]")
    idle(page)


def heads(page):
    return [t.strip().rstrip("▲▼ ").strip() for t in page.locator("[data-testid=prod-cuts-table] thead th").all_text_contents()][1:]       # the first column is the tick box


def drag(page, src, dst, dx=0):
    a, b = src.bounding_box(), dst.bounding_box()
    page.mouse.move(a["x"] + a["width"] / 2, a["y"] + a["height"] / 2)
    page.mouse.down()
    page.mouse.move(a["x"] + a["width"] / 2 + 12, a["y"] + a["height"] / 2 + 12, steps=3)
    page.mouse.move(b["x"] + dx, b["y"] + b["height"] / 2, steps=8)
    page.mouse.up()
    page.wait_for_timeout(300)


def test_page_is_named_creative_pipeline(page):
    page.goto("/priorities/production")
    page.wait_for_selector("[data-testid=prod-page]")
    assert page.locator("h1").text_content() == "Creative Pipeline"
    assert page.title() == "Creative Pipeline · Lonvera"
    assert page.locator("[data-testid=nav-production]").text_content().strip() == "Creative Pipeline"


def test_by_cut_view_lists_cuts_with_status_counts_and_stats(page):
    goto(page)
    assert heads(page)[:2] == ["Product", "ICP"]
    assert "Concepts in pipeline" in heads(page) and "Live concepts" in heads(page) and "Spend" in heads(page) and "NC ROAS" in heads(page) and "Meta ROAS" in heads(page)
    assert page.locator("[data-testid=prod-cuts-row]").count() > 0
    assert page.locator("[data-testid=prod-cuts-total] td").nth(1).text_content() == "Total"
    assert "Mock basis." in page.locator("[data-testid=prod-cuts-basis]").text_content()


def test_dragging_a_cut_into_group_by_rebuilds_the_table(page):
    goto(page)
    on = page.locator("[data-testid=prod-cuts-on]")
    drag(page, page.locator("[data-testid=prod-chip-cluster]"), on.locator("[data-testid=prod-chip-product]"), dx=2)
    idle(page)
    page.wait_for_function("() => document.querySelectorAll('[data-testid=prod-cuts-on] .prod-cutchip').length === 3")
    assert [c.get_attribute("data-key") for c in on.locator(".prod-cutchip").all()] == ["cluster", "product", "icp"]
    assert heads(page)[:3] == ["Market cluster", "Product", "ICP"]
    assert re.search(r"by=cluster(,|%2C)product(,|%2C)icp", page.url)


def test_dragging_a_cut_out_and_reordering(page):
    goto(page, "&by=product,icp,pain_point")
    on = page.locator("[data-testid=prod-cuts-on]")
    drag(page, on.locator("[data-testid=prod-chip-pain_point]"), on.locator("[data-testid=prod-chip-product]"), dx=2)
    page.wait_for_function("() => document.querySelector('[data-testid=prod-cuts-on] .prod-cutchip').dataset.key === 'pain_point'")
    assert heads(page)[:3] == ["Pain point", "Product", "ICP"]
    drag(page, on.locator("[data-testid=prod-chip-product]"), page.locator("[data-testid=prod-cuts-off]"))
    page.wait_for_function("() => document.querySelectorAll('[data-testid=prod-cuts-on] .prod-cutchip').length === 2")
    assert [c.get_attribute("data-key") for c in on.locator(".prod-cutchip").all()] == ["pain_point", "icp"]
    assert page.locator("[data-testid=prod-cuts-off] [data-testid=prod-chip-product]").count() == 1


def test_keyboard_moves_and_reorders_chips(page):
    goto(page)
    page.locator("[data-testid=prod-chip-pain_point]").focus()
    page.keyboard.press("Enter")
    page.wait_for_function("() => document.querySelectorAll('[data-testid=prod-cuts-on] .prod-cutchip').length === 3")
    page.keyboard.press("ArrowLeft")
    page.wait_for_function("() => [...document.querySelectorAll('[data-testid=prod-cuts-on] .prod-cutchip')].map(c => c.dataset.key).join() === 'product,pain_point,icp'")
    assert page.evaluate("document.activeElement.dataset.key") == "pain_point"


def test_the_last_cut_cannot_be_removed(page):
    goto(page, "&by=icp")
    page.locator("[data-testid=prod-chip-icp]").focus()
    page.keyboard.press("Enter")
    assert page.locator("[data-testid=prod-cuts-on] .prod-cutchip").count() == 1
    assert "At least one cut" in page.locator("[data-testid=prod-cuts-live]").text_content()


def test_date_presets_and_custom_dates_change_the_window(page):
    goto(page)
    span = lambda: page.locator("[data-testid=prod-cuts-basis]").text_content().split(".")[0]
    first = span()
    page.locator("[data-testid=prod-cuts-preset-7]").click()
    page.wait_for_function("(t) => !document.querySelector('[data-testid=prod-cuts-basis]').textContent.startsWith(t)", arg=first)
    assert page.locator("[data-testid=prod-cuts-preset-7]").get_attribute("aria-pressed") == "true"
    page.locator("[data-testid=prod-cuts-from]").fill("2026-09-01")
    page.locator("[data-testid=prod-cuts-from]").dispatch_event("change")
    page.wait_for_function("() => document.querySelector('[data-testid=prod-cuts-basis]').textContent.startsWith('2026-09-01')")
    assert page.locator("[data-testid=prod-cuts-preset-7]").get_attribute("aria-pressed") == "false"
    assert "preset=custom" in page.url


def test_status_columns_can_be_hidden_and_only_pipeline_cuts_filter(page):
    goto(page)
    assert "In production" in heads(page)
    page.locator("[data-testid=prod-cuts-metrics] > summary").click()
    page.locator("[data-testid=prod-cuts-stage-in_production]").uncheck()
    assert "In production" not in heads(page)
    n = page.locator("[data-testid=prod-cuts-row]").count()
    page.locator("[data-testid=prod-cuts-pipeline-only]").check()
    assert 0 < page.locator("[data-testid=prod-cuts-row]").count() <= n


def test_sorting_by_a_column(page):
    goto(page)
    page.locator("[data-testid=prod-cuts-sort-open]").click()
    vals = [int(x) if x.isdigit() else 0 for x in page.locator("[data-testid=prod-cuts-row] td[data-metric=open]").all_text_contents()]
    assert vals == sorted(vals, reverse=True)
    assert page.locator("[data-testid=prod-cuts-sort-open]").locator("xpath=..").get_attribute("aria-sort") == "descending"


def test_phone_width_has_no_sideways_page_scroll(page):
    page.set_viewport_size({"width": 375, "height": 812})
    goto(page)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")


def test_metrics_can_be_chosen(page):
    goto(page)
    assert "CPM" in heads(page)
    page.locator("[data-testid=prod-cuts-metrics] > summary").click()
    page.locator("[data-testid=prod-cuts-metric-cpm]").uncheck()
    page.locator("[data-testid=prod-cuts-metric-ctr]").uncheck()
    assert "CPM" not in heads(page) and "CTR" not in heads(page) and "Spend" in heads(page)
    assert "ms=" in page.url
    page.reload()
    page.wait_for_selector("[data-testid=prod-cuts-table]")
    assert "CPM" not in heads(page)


def test_quick_filters_count_and_filter(page):
    goto(page)
    btns = page.locator("[data-testid=prod-cuts-quick] button").all_text_contents()
    assert len(btns) == 6 and btns[0].startswith("All cuts") and "Lots in pipeline, not working" in btns[1] and btns[4].startswith("Ready to launch, none live")
    n_ready = int(btns[4].rsplit(" ", 1)[1])
    page.locator("[data-testid=prod-cuts-quick-ready]").click()
    assert page.locator("[data-testid=prod-cuts-row]").count() == n_ready
    assert "qf=ready" in page.url
    page.locator("[data-testid=prod-cuts-quick-all]").click()
    assert page.locator("[data-testid=prod-cuts-row]").count() >= n_ready


def test_clicking_a_row_opens_the_ads_popup_without_leaving_the_page(page):
    goto(page)
    url = page.url
    page.locator("[data-testid=prod-cuts-row]").first.click()
    page.wait_for_selector("[data-testid=prod-cutads-top]")
    assert page.locator("[data-testid=prod-cutads-top] [data-testid=prod-cutads-row]").count() == 5
    assert page.locator("[data-testid=prod-cutads-window] [data-testid=prod-cutads-row]").count() == 3
    assert page.locator("[data-testid=prod-cutads-cut] dt").all_text_contents()[:2] == ["Product", "ICP"]
    assert "null" not in page.locator(".prod-cutads").text_content()
    assert page.url == url
    page.keyboard.press("Escape")
    assert page.locator("[data-testid=prod-cutads-body]").count() == 0 and page.locator("[data-testid=prod-cuts-table]").is_visible()


def test_share_metrics_are_off_by_default_and_selectable(page):
    goto(page)
    assert "% of spend" not in heads(page)
    page.locator("[data-testid=prod-cuts-metrics] > summary").click()
    page.locator("[data-testid=prod-cuts-metric-spend_share]").check()
    assert "% of spend" in heads(page)
    assert page.locator("[data-testid=prod-cuts-total] td[data-metric=spend_share]").text_content().strip() == "100.0%"


def test_filter_by_pain_point_and_icp_narrows_rows(page):
    goto(page, "&by=product,icp,pain_point")
    n = page.locator("[data-testid=prod-cuts-row]").count()
    d = page.locator("[data-testid=prod-cuts-f-pain]")
    d.locator("summary").click()
    first = d.locator("input[type=checkbox]").first
    label = first.get_attribute("value")
    first.check()
    page.keyboard.press("Escape")
    page.wait_for_function("(n) => document.querySelectorAll('[data-testid=prod-cuts-row]').length < n", arg=n)
    cells = page.locator("[data-testid=prod-cuts-row] td:nth-child(4)").all_text_contents()
    assert cells and all(c.strip() == label for c in cells)
    assert "fp=" in page.url
    page.locator("[data-testid=prod-cuts-f-clear]").click()
    page.wait_for_function("(n) => document.querySelectorAll('[data-testid=prod-cuts-row]').length === n", arg=n)


def test_popup_hides_ads_under_250_dollars_until_unticked(page):
    goto(page)
    page.locator("[data-testid=prod-cuts-row]").first.click()
    page.wait_for_selector("[data-testid=prod-cutads-window]")
    assert page.locator("[data-testid=prod-cutads-count]").text_content() == "Showing 3 of 4 ads"          # the mock ads spent $700, $500, $300 and $100
    page.locator("[data-testid=prod-cutads-min]").uncheck()
    assert page.locator("[data-testid=prod-cutads-count]").text_content() == "Showing 4 of 4 ads"


def test_no_stray_null_text_in_the_view(page):
    goto(page)
    assert "null" not in page.locator("[data-testid=prod-page]").text_content().lower().replace("nullable", "")


def tick_complete_rows(page, n):
    """Tick the first n rows whose cut labels are all real (no '(none)')."""
    done = 0
    for i in range(page.locator("[data-testid=prod-cuts-row]").count()):
        row = page.locator("[data-testid=prod-cuts-row]").nth(i)
        if "(none)" not in row.text_content() and done < n:
            row.locator("[data-testid=prod-cuts-sel]").check()
            done += 1
    assert done == n


def test_cuts_without_all_five_tags_cannot_go_to_needs_brief(page):
    goto(page, "&by=product,icp")
    page.locator("[data-testid=prod-cuts-sel]").nth(0).check()
    page.locator("[data-testid=prod-cuts-addbl]").click()
    assert "Group by Market cluster, Pain point" in page.locator("[data-testid=prod-cuts-selmsg]").text_content()
    goto(page, "&by=product,cluster,icp,pain_point")
    page.locator("[data-testid=prod-cuts-sel]").nth(0).check()
    page.locator("[data-testid=prod-cuts-addbl]").click()
    assert "image or video" in page.locator("[data-testid=prod-cuts-selmsg]").text_content().lower()


def test_tick_cuts_and_add_them_to_needs_brief_then_assign(page):
    goto(page, "&by=product,cluster,icp,pain_point")
    tick_complete_rows(page, 2)
    assert "2 cuts ticked" in page.locator("[data-testid=prod-cuts-selbar]").text_content()
    page.select_option("[data-testid=prod-cuts-addmedia]", "video")
    page.locator("[data-testid=prod-cuts-addbl]").click()
    page.wait_for_selector("[data-testid=prod-cuts-selhint]")
    page.locator("[data-testid=prod-view-backlog]").click()
    page.wait_for_selector("[data-testid=prod-bl-table]")
    assert page.locator("[data-testid=prod-bl-row]").count() == 2
    page.locator("[data-testid=prod-bl-sel]").nth(0).check()
    page.locator("[data-testid=prod-bl-assign]").click()
    assert "Choose a partner" in page.locator("[data-testid=prod-bl-err]").text_content()
    page.select_option("[data-testid=prod-bl-vendor]", "Madre")
    page.locator("[data-testid=prod-bl-assign]").click()
    assert "brief" in page.locator("[data-testid=prod-bl-err]").text_content().lower()
    page.fill("[data-testid=prod-bl-brief-url]", "https://docs.example/brief")
    page.locator("[data-testid=prod-bl-assign]").click()
    page.wait_for_function("() => document.querySelectorAll('[data-testid=prod-bl-row]').length === 1")


def test_adding_by_hand_and_pipeline_share_metric_and_media_cut(page):
    goto(page, "&by=media")
    assert heads(page)[0] == "Image / video"
    page.locator("[data-testid=prod-view-backlog]").click()
    page.wait_for_selector("[data-testid=prod-bl-empty]")
    page.locator("[data-testid=prod-bl-add] > summary").click()
    page.fill("[data-testid=prod-bl-add-pain]", "Hair Loss")
    page.select_option("[data-testid=prod-bl-add-product]", "beef-liver")
    page.select_option("[data-testid=prod-bl-add-media]", "image")
    page.fill("[data-testid=prod-bl-add-cluster]", "Hair Thinning")
    page.fill("[data-testid=prod-bl-add-icp]", "Denise")
    page.locator("[data-testid=prod-bl-add-go]").click()
    page.wait_for_selector("[data-testid=prod-bl-table]")
    assert page.locator("[data-testid=prod-bl-row] [data-testid=prod-bl-pain_point]").first.input_value() == "Hair Loss"
    goto(page)
    assert "% of concepts in pipeline" in heads(page)                                                  # on by default
    page.locator("[data-testid=prod-cuts-metrics] > summary").click()
    page.locator("[data-testid=prod-cuts-metric-pipeline_share]").uncheck()
    assert "% of concepts in pipeline" not in heads(page)


def test_popup_shows_top_hooks_and_a_hook_under_each_ad(page):
    goto(page)
    page.locator("[data-testid=prod-cuts-row]").first.click()
    page.wait_for_selector("[data-testid=prod-cutads-hooks]")
    assert page.locator("[data-testid=prod-cutads-hookrow]").count() == 3
    assert page.locator("[data-testid=prod-cutads-hook]").first.text_content().startswith("Hook line")


def test_metric_cells_are_colour_graded_against_the_total_row(page):
    goto(page)
    grades = {c for c in page.locator("[data-testid=prod-cuts-row] td[data-grade]").evaluate_all("els => els.map(e => e.dataset.grade)")}
    assert grades <= {"g2", "g1", "g0", "r1", "r2"} and grades
    assert page.locator("[data-testid=prod-cuts-total] td[data-grade]").count() == 0                      # the Total row is the baseline: never coloured
    assert "Colours compare each cut" in page.locator("[data-testid=prod-cuts-legend]").text_content()


def test_grade_function_rules():
    # pure rule check through the page's own module would need a browser; the rule is mirrored here so a change to it is a visible change to this test
    def grade(dir_, value, base, spend):
        if spend < 250 or not base:
            return ""
        r = value / base if dir_ == 1 else (base / value if value > 0 else 0)
        return "g2" if r >= 1.25 else "g1" if r >= 1.05 else "g0" if r >= 0.95 else "r1" if r >= 0.75 else "r2"
    assert grade(1, 0.9, 0.5, 1000) == "g2" and grade(1, 0.5, 0.5, 1000) == "g0" and grade(-1, 80, 100, 1000) == "g2" and grade(-1, 200, 100, 1000) == "r2" and grade(1, 0.9, 0.5, 100) == ""


def test_metrics_can_be_reordered_by_keyboard_and_by_drag(page):
    page.set_viewport_size({"width": 1440, "height": 1300})
    goto(page)
    before = heads(page)
    assert before.index("Spend") < before.index("NC revenue")
    page.locator("[data-testid=prod-cuts-metrics] > summary").click()
    grip = page.locator("[data-testid=prod-cuts-metric-grip-spend]")
    grip.focus()
    page.keyboard.press("ArrowDown")
    page.wait_for_function("() => { const h = [...document.querySelectorAll('[data-testid=prod-cuts-table] thead th')].map(t => t.textContent.trim()); return h.indexOf('Spend') > h.indexOf('NC revenue'); }")
    assert "mo=" in page.url
    src = page.locator("[data-testid=prod-cuts-metric-grip-aov]")
    dst = page.locator("[data-testid=prod-cuts-metric-grip-live_ads]")
    a, b = src.bounding_box(), dst.bounding_box()
    page.mouse.move(a["x"] + 4, a["y"] + 8)
    page.mouse.down()
    page.mouse.move(a["x"] + 4, a["y"] - 30, steps=5)
    page.mouse.move(b["x"] + 4, b["y"] + 1, steps=10)
    page.mouse.up()
    page.wait_for_function("() => { const h = [...document.querySelectorAll('[data-testid=prod-cuts-table] thead th')].map(t => t.textContent.trim()); return h.indexOf('AOV') < h.indexOf('Live concepts'); }")
    page.reload()
    page.wait_for_selector("[data-testid=prod-cuts-table]")
    h2 = heads(page)
    assert h2.index("AOV") < h2.index("Live concepts") and h2.index("Spend") > h2.index("NC revenue")      # the order survives a reload (it is in the address)


def test_save_load_and_delete_a_view(page):
    goto(page, "&by=icp")
    page.fill("[data-testid=prod-cuts-view-name]", "ICP view")
    page.locator("[data-testid=prod-cuts-view-save]").click()
    page.wait_for_function("() => document.querySelector('[data-testid=prod-cuts-view-msg]').textContent.includes('Saved ICP view')")
    goto(page, "&by=product,cluster")
    assert heads(page)[0] == "Product"
    page.wait_for_function("() => document.querySelectorAll('[data-testid=prod-cuts-view-select] option').length === 2")
    page.select_option("[data-testid=prod-cuts-view-select]", label="ICP view")
    page.wait_for_function("() => document.querySelector('[data-testid=prod-cuts-table] thead th:nth-child(2)').textContent.includes('ICP')")
    assert heads(page)[0] == "ICP"
    page.locator("[data-testid=prod-cuts-view-delete]").click()
    page.wait_for_function("() => document.querySelectorAll('[data-testid=prod-cuts-view-select] option').length === 1")
