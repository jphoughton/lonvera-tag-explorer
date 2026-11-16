"""Creative Pipeline "By cut" view (/priorities/production?view=cuts): the page rename, the table by cut, drag and drop of the cut chips, the keyboard path, status columns and the date window,
against the mock (tests/ui/mock_production_board.py, seeded like test_production_board.py)."""
import re

import pytest

pytestmark = pytest.mark.mock_only


@pytest.fixture(autouse=True)
def fresh(ctx, base_url):
    ctx.request.post(base_url + "/__test/assign/reset")
    ctx.request.post(base_url + "/__test/board/seed")


def idle(page):
    page.wait_for_function("() => !document.querySelector('#view').hasAttribute('aria-busy')")


def goto(page, qs=""):
    page.goto("/priorities/production?view=cuts" + qs)
    page.wait_for_selector("[data-testid=prod-cuts-table]")
    idle(page)


def heads(page):
    return [t.strip().rstrip("▲▼ ").strip() for t in page.locator("[data-testid=prod-cuts-table] thead th").all_text_contents()]


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
    assert "Concepts" in heads(page) and "Spend" in heads(page) and "NC ROAS" in heads(page) and "Meta ROAS" in heads(page)
    assert page.locator("[data-testid=prod-cuts-row]").count() > 0
    assert page.locator("[data-testid=prod-cuts-total] td").first.text_content() == "Total"
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
    page.locator("[data-testid=prod-cuts-stage-in_production]").uncheck()
    assert "In production" not in heads(page)
    n = page.locator("[data-testid=prod-cuts-row]").count()
    page.locator("[data-testid=prod-cuts-pipeline-only]").check()
    assert 0 < page.locator("[data-testid=prod-cuts-row]").count() <= n


def test_sorting_by_a_column(page):
    goto(page)
    page.locator("[data-testid=prod-cuts-sort-concepts]").click()
    vals = [int(x) if x.isdigit() else 0 for x in page.locator("[data-testid=prod-cuts-row] td:nth-child(" + str(len(heads(page)) - 10) + ")").all_text_contents()]
    assert vals == sorted(vals, reverse=True)
    assert page.locator("[data-testid=prod-cuts-sort-concepts]").locator("xpath=..").get_attribute("aria-sort") == "descending"


def test_phone_width_has_no_sideways_page_scroll(page):
    page.set_viewport_size({"width": 375, "height": 812})
    goto(page)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
