"""Madre review (/madre-review) in the browser, against the mock server (tests/ui/mock_madre.py: a small deck in the production payload shape)."""
import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.mock_only


def open_deck(page, suffix=""):
    page.goto("/madre-review" + suffix)
    page.wait_for_selector("[data-testid=mr-deck]")
    page.wait_for_function("() => !document.querySelector('#view').hasAttribute('aria-busy')")


def count(page):
    return page.locator("[data-testid=mr-count]").inner_text()


def test_deck_opens_on_the_cover_counts_slides_and_flips_with_keys(page):
    open_deck(page)
    assert count(page) == "1 / 8 · Overview"
    expect(page.locator(".mr-slide.mr-on h1")).to_have_text("Madre · Week of Nov 9–15")
    page.keyboard.press("ArrowRight")
    assert count(page) == "2 / 8 · Overview" and page.url.endswith("#s2")
    page.keyboard.press("ArrowLeft")
    assert count(page) == "1 / 8 · Overview"
    page.locator("[data-testid=mr-prev]").click()
    assert count(page).startswith("1 / 8")                      # does not go below the first slide


def test_section_buttons_and_anchors(page):
    open_deck(page)
    page.locator("[data-testid=mr-sec-vita-bull]").click()
    assert count(page) == "3 / 8 · Vita Bull" and page.url.endswith("#vita-bull")
    expect(page.locator("[data-testid=mr-sec-vita-bull]")).to_have_class("mr-sec mr-on")
    page.locator("[data-testid=mr-sec-notes]").click()
    assert count(page) == "8 / 8 · Notes"
    open_deck(page, "#vita-bull")
    assert count(page) == "3 / 8 · Vita Bull"
    open_deck(page, "#s4")
    assert count(page) == "4 / 8 · Vita Bull"


@pytest.mark.allow_errors
def test_ad_tile_opens_a_panel_that_plays_and_a_missing_video_falls_back(page):
    open_deck(page, "#s4")
    tiles = page.locator(".mr-on [data-testid=mr-tile]")
    assert tiles.count() == 2
    tiles.nth(0).click()
    expect(page.locator("[data-testid=mr-panel]")).to_be_visible()
    expect(page.locator("[data-testid=mr-video]")).to_have_attribute("src", "/api/madre-review/video?family=ok")
    expect(page.locator("[data-testid=mr-panel]")).to_contain_text("Hook line")
    page.keyboard.press("Escape")
    expect(page.locator("[data-testid=mr-panel]")).to_be_hidden()
    tiles.nth(1).click()
    expect(page.locator("[data-testid=mr-nomedia]")).to_be_visible()
    assert page.locator("[data-testid=mr-panel] a.mr-ext[href^='https://drive.google.com']").count() >= 1


def test_ticket_panel_and_ask_link_open_panels(page):
    open_deck(page, "#s5")
    page.locator(".mr-on [data-testid=mr-chip]").click()
    expect(page.locator("[data-testid=mr-told]")).to_contain_text("3 ads already launched in this pocket")
    page.keyboard.press("Escape")
    page.locator("[data-testid=mr-sec-vita-bull]").click()
    for _ in range(4):
        page.locator("[data-testid=mr-next]").click()
    assert count(page).startswith("7 / 8")
    page.locator(".mr-on .mr-inl").click()
    expect(page.locator("[data-testid=mr-panel]")).to_contain_text("vb-7.9.3-hook2")


def test_send_to_production_button(page):
    open_deck(page, "#s6")
    page.locator("[data-testid=mr-send-idea]").click()
    expect(page.locator(".mr-on .mr-more")).to_have_text("Added to Needs brief.")


def test_phone_width_has_no_sideways_scroll(page):
    page.set_viewport_size({"width": 375, "height": 812})
    open_deck(page, "#s2")
    assert page.evaluate("() => document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    assert page.evaluate("() => document.querySelector('.mr-deck').getBoundingClientRect().width <= window.innerWidth")
