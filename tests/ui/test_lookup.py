"""Tag lookup (/lookup) in the browser, against the mock server (tests/ui/mock_lookup.py: the real matching code over a few made-up rows).

Mock facts used below: 'Liver-208-Copy3_VID_WeightGain_Madre' is a platform ad whose family has two tracker rows with different new tags (B45 and B07);
'S-Lonvera-BeefLiver-Static-9.5_IMG_Offer' reaches row 103 only through the tracker name underneath; 'Lonvera-Boneflex-Video-2.1' has a text-only tag;
'Bull-777-Hook1' is a tracker Concept ID with no new tags; 'Bull-526-Hook2 - Copy' and 'Liver-209-Hook1' are platform ads with no tracker row.
"""
from urllib.parse import parse_qs, urlparse

import pytest

from conftest import SHOTS

pytestmark = pytest.mark.mock_only

CARD = "[data-testid=tl-match]"


def idle(page):
    page.wait_for_function("() => !document.querySelector('#view').hasAttribute('aria-busy')")


def search(page, text):
    page.locator("[data-testid=tl-input]").fill(text)
    page.locator("[data-testid=tl-input]").press("Enter")
    page.wait_for_function("() => !!document.querySelector('[data-testid=tl-results], [data-testid=tl-nomatch], [data-testid=tl-error]')")
    idle(page)


def goto(page, q=None):
    page.goto("/lookup" + (f"?q={q}" if q else ""))
    page.wait_for_selector("[data-testid=tl-page]")
    idle(page)


def qparam(page):
    return parse_qs(urlparse(page.url).query).get("q", [None])[0]


def test_nav_link_opens_an_empty_search_with_label_example_and_no_range_bar(page):
    page.goto("/trends")
    page.locator("[data-testid=nav-lookup-go]").click()                                                  # the top-bar tag search (empty submit opens the page)
    page.wait_for_selector("[data-testid=tl-page]")
    idle(page)
    assert page.url.endswith("/lookup")
    assert page.locator(".tl-label").text_content() == "Look up an ad by its platform name"
    assert "for example" in page.locator("[data-testid=tl-input]").get_attribute("placeholder")
    assert page.locator("[data-testid=tl-empty]").is_visible() and page.locator("#controls").is_hidden()
    assert page.locator("[data-testid=tl-clear]").is_hidden()
    assert page.evaluate("() => document.activeElement && document.activeElement.id") == "tl-q"           # ready to paste
    page.locator("[data-testid=tl-input]").press("Enter")                                                  # nothing typed: nothing happens
    assert page.url.endswith("/lookup")


def test_exact_name_shows_the_new_tags_and_the_link(page):
    goto(page)
    search(page, "Lonvera-Boneflex-Video-2.1")
    assert qparam(page) == "Lonvera-Boneflex-Video-2.1"
    s = page.locator("[data-testid=tl-summary]")
    assert s.get_attribute("data-kind") == "exact" and "1 tracker row" in s.inner_text()
    card = page.locator(CARD)
    assert card.count() == 1
    assert card.locator("[data-testid=tl-line-code]").inner_text() == "BF11"
    assert "Build-My-Stack" in card.locator("[data-testid=tl-line-name]").inner_text()
    txt = card.text_content()
    for want in ("Glucosamine & Chondroitin", "Joint Pain & Mobility Loss", "Desire", "Glucosamine", "Cartilage wear", "What was seen", "Why this line", "Runner-up line"):
        assert want in txt, want
    assert card.locator("[data-testid=tl-caveat]").is_visible()                                            # text-only tag
    assert card.locator("[data-testid=tl-open-page]").get_attribute("href") == "/ad/104"
    assert page.locator("[data-testid=tl-clear]").is_visible()
    page.screenshot(path=str(SHOTS / "lookup_exact.png"), full_page=True)



def test_a_name_with_several_tracker_rows_shows_every_row_and_says_the_tags_differ(page):
    goto(page, "Liver-208-Copy3_VID_WeightGain_Madre")
    page.wait_for_selector(CARD)
    assert page.locator(CARD).count() == 2
    assert page.locator("[data-testid=tl-line-code]").all_inner_texts() == ["B45", "B07"]
    assert "tags differ" in page.locator("[data-testid=tl-note]").first.inner_text()
    assert page.locator("[data-testid=tl-platform]").is_visible()
    assert page.locator("[data-testid=tl-summary]").get_attribute("data-kind") == "exact"


def test_prefixed_encoded_platform_name_is_matched_by_the_tracker_name_underneath(page):
    goto(page)
    search(page, "S-Lonvera-BeefLiver-Static-9.5_IMG_Offer")
    assert page.locator("[data-testid=tl-summary]").get_attribute("data-kind") == "family"
    assert page.locator(CARD).count() == 1 and page.locator("[data-testid=tl-line-code]").inner_text() == "B07"
    assert "prefix or an encoded suffix" in page.locator("[data-testid=tl-note]").inner_text()


def test_untagged_row_says_so_and_invents_nothing(page):
    goto(page)
    search(page, "bull-777-hook1")
    assert page.locator("[data-testid=tl-new-none]").is_visible() and "not been through" in page.locator("[data-testid=tl-new-none]").inner_text()
    assert page.locator("[data-testid=tl-line-code]").count() == 0


def test_no_match_for_a_platform_ad_says_so_and_never_shows_a_tag(page):
    goto(page)
    search(page, "Liver-209-Hook1")
    nm = page.locator("[data-testid=tl-nomatch]")
    assert nm.is_visible() and "No tags found" in nm.inner_text() and "no tracker row" in nm.inner_text()
    assert page.locator("[data-testid=tl-platform]").is_visible() and page.locator(CARD).count() == 0
    assert page.locator("[data-testid=tl-line-code]").count() == 0


def test_no_match_at_all_says_what_to_do(page):
    goto(page)
    search(page, "zzqx not an ad")
    nm = page.locator("[data-testid=tl-nomatch]")
    assert nm.is_visible() and "Check the spelling" in nm.inner_text() and page.locator("[data-testid=tl-platform]").count() == 0


def test_meta_ad_id_and_clear_button(page):
    goto(page)
    search(page, "5100000003")
    assert page.locator("[data-testid=tl-summary]").get_attribute("data-kind") == "id" and page.locator(CARD).count() == 1
    page.locator("[data-testid=tl-clear]").click()
    page.wait_for_selector("[data-testid=tl-empty]")
    idle(page)
    assert page.url.endswith("/lookup") and page.locator("[data-testid=tl-input]").input_value() == ""


@pytest.mark.allow_errors
def test_error_shows_a_box_with_retry_that_asks_again(page):
    seen = []

    def handler(route):
        seen.append(1)
        if len(seen) == 1:
            route.fulfill(status=503, content_type="application/json", body='{"error":"query timed out"}')
        else:
            route.continue_()
    page.route("**/api/tag-lookup**", handler)
    goto(page, "Lonvera-Boneflex-Video-2.1")
    page.wait_for_selector("[data-testid=tl-error]")
    assert "query timed out" in page.locator("[data-testid=tl-error]").inner_text()
    assert page.locator("[data-testid=tl-input]").is_visible()                                             # the search bar stays
    page.locator("[data-testid=tl-retry]").click()
    page.wait_for_selector(CARD)
    assert len(seen) == 2 and page.locator("[data-testid=tl-error]").count() == 0


def test_a_phone_width_has_no_sideways_scroll_and_text_is_at_least_12px(page):
    page.set_viewport_size({"width": 375, "height": 800})
    goto(page, "Liver-208-Copy3_VID_WeightGain_Madre")
    page.wait_for_selector(CARD)
    assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth + 1")
    goto(page, "zzqx_a_very_long_unbroken_name_" + "x" * 120)
    page.wait_for_selector("[data-testid=tl-nomatch]")
    assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth + 1")
    goto(page, "Lonvera-Boneflex-Video-2.1")
    page.wait_for_selector(CARD)
    small = page.evaluate("""() => [...document.querySelectorAll('.tl-page *')].filter(e => e.childNodes.length && [...e.childNodes].some(n => n.nodeType === 3 && n.textContent.trim())
        && parseFloat(getComputedStyle(e).fontSize) < 12).map(e => e.className + ':' + getComputedStyle(e).fontSize)""")
    assert small == [], small
    page.screenshot(path=str(SHOTS / "lookup_375.png"), full_page=True)

