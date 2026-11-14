"""Our own ad learning tags in the UI, on the mock (tests/ui/mock_own_tags.py, mock_competitors.py): performance by hook type / device / offer type on the Tag Explorer product page, the learning card on the ad page,
the 'what is working for us' lines and our own landing pages on the Competitors page. Hidden when the data is absent, 375 px without sideways scroll, no em dashes."""
import re

import pytest
from playwright.sync_api import expect

from ui_helpers import SLUG, shot, wait_rows

pytestmark = pytest.mark.mock_only
EM = "—"


@pytest.fixture()
def own(ctx, base_url):
    def set_mode(mode="on"):
        ctx.request.post(base_url + f"/__test/own_tags?mode={mode}")
    yield set_mode
    ctx.request.post(base_url + "/__test/own_tags?mode=off")


@pytest.fixture()
def comp(ctx, base_url):
    def set_mode(tags="on", lp=True, own=True):
        ctx.request.post(base_url + f"/__test/competitors/tags?mode={tags}")
        ctx.request.post(base_url + f"/__test/competitors/lp?on={'1' if lp else '0'}")
        ctx.request.post(base_url + f"/__test/competitors/own?on={'1' if own else '0'}")
    yield set_mode
    for u in ("tags?mode=off", "lp?on=0", "own?on=0"):
        ctx.request.post(base_url + "/__test/competitors/" + u)


def no_sideways(page):
    assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth + 1"), "the page scrolls sideways"


def goto_tags(page, qs=""):
    page.goto(f"/p/{SLUG}{qs}")
    wait_rows(page)


def table_rows(page, key):
    t = page.locator(f"[data-testid=own-cut][data-key={key}] tbody tr")
    return [[c.inner_text().strip() for c in t.nth(i).locator("th, td").all()] for i in range(t.count())]


def headers(page, key):
    return [c.inner_text().strip() for c in page.locator(f"[data-testid=own-cut][data-key={key}] thead th").all()]


# ------------------------------------------------------------------------------------------------ performance cuts

def test_section_absent_until_the_learning_table_has_rows(page, own):
    own("off")
    goto_tags(page)
    page.wait_for_timeout(300)
    assert not page.locator("[data-testid=own-learn]").is_visible() and page.locator("[data-testid=own-cut]").count() == 0


def test_cuts_render_with_the_existing_metrics_sorted_by_spend(page, own):
    own("on")
    goto_tags(page)
    expect(page.locator("[data-testid=own-learn]")).to_be_visible()
    assert page.locator("[data-testid=own-cut]").count() == 3
    assert [page.locator("[data-testid=own-cut] h3").nth(i).inner_text() for i in range(3)] == ["Hook type", "Device", "Offer type"]
    h = headers(page, "hook_type")
    assert h == ["Hook type", "Ad families", "Spend", "% of spend", "NC ROAS", "Meta ROAS", "CPA", "Cost per link click", "CTR", "CPM", "Conversion rate", "AOV"]
    rows = table_rows(page, "hook_type")
    assert [r[0] for r in rows] == ["Question", "Statement", "Story", "Offer"]
    q = dict(zip(h, rows[0]))
    assert q["Ad families"] == "6" and q["Spend"] == "$9,000" and q["% of spend"] == "45.0%" and q["NC ROAS"] == "0.80" and q["Meta ROAS"] == "1.05"
    assert q["Cost per link click"] == "$0.82" and q["CTR"] == "1.57%" and q["CPM"] == "$12.86"
    note = page.locator("[data-testid=own-cut][data-key=hook_type] [data-testid=own-cut-note]").inner_text()
    assert note == "14 of 40 ad families tagged"
    assert "NC ROAS is Upstack new customer revenue divided by spend" in page.locator("[data-testid=own-learn-note]").inner_text()
    assert [r[0] for r in table_rows(page, "device")] == ["Talking head", "Voiceover", "Split screen", "Text on screen"] and [r[0] for r in table_rows(page, "offer_type")] == ["Discount", "Bundle"]
    page.locator("[data-testid=own-learn]").scroll_into_view_if_needed()
    shot(page, "own_01_cuts_desktop")


def test_numbers_switch_picks_the_basis_of_cpa_conversion_and_aov(page, own):
    own("on")
    goto_tags(page)
    expect(page.locator("[data-testid=own-learn]")).to_be_visible()
    meta = dict(zip(headers(page, "hook_type"), table_rows(page, "hook_type")[0]))
    assert {"CPA", "Conversion rate", "AOV"} <= set(meta) and meta["CPA"] == "$50.00" and meta["Conversion rate"] == "1.64%" and meta["AOV"] == "$52.50"      # Meta: 180 purchases on $9,000 and 11,000 link clicks
    goto_tags(page, "?source=upstack")
    expect(page.locator("[data-testid=own-learn]")).to_be_visible()
    nc = dict(zip(headers(page, "hook_type"), table_rows(page, "hook_type")[0]))
    assert {"NC CPA", "NC Conversion rate", "NC AOV"} <= set(nc) and nc["NC CPA"] == "$100.00" and nc["NC AOV"] == "$80.00" and nc["NC ROAS"] == "0.80" and nc["Meta ROAS"] == "1.05"


def test_without_upstack_rows_only_meta_numbers_show(page, own):
    own("nc_off")
    goto_tags(page)
    expect(page.locator("[data-testid=own-learn]")).to_be_visible()
    row = dict(zip(headers(page, "hook_type"), table_rows(page, "hook_type")[0]))
    assert row["NC ROAS"] == "–" and row["Meta ROAS"] == "1.05"
    assert "Upstack numbers are not loaded" in page.locator("[data-testid=own-cut-note]").first.inner_text()


def test_other_products_without_tags_show_nothing(page, own):
    own("on")
    page.goto("/p/beef-liver")
    wait_rows(page)
    page.wait_for_timeout(300)
    assert not page.locator("[data-testid=own-learn]").is_visible()


def test_cuts_phone_width_and_no_em_dash(page, own):
    own("on")
    page.set_viewport_size({"width": 375, "height": 812})
    goto_tags(page)
    expect(page.locator("[data-testid=own-learn]")).to_be_visible()
    no_sideways(page)
    assert EM not in page.locator("[data-testid=own-learn]").inner_text()
    page.locator("[data-testid=own-learn]").scroll_into_view_if_needed()
    shot(page, "own_02_cuts_375")


# ------------------------------------------------------------------------------------------------ the ad page

def ad_ids(ctx, base_url):
    return ctx.request.get(base_url + "/__test/own_tags/ids").json()


def test_ad_page_chips_quote_and_collapsed_parts(page, own, ctx, base_url):
    own("on")
    ids = ad_ids(ctx, base_url)
    page.goto(f"/ad/{ids['full']}")
    card = page.locator("[data-testid=learning-block]")
    expect(card).to_be_visible()
    assert card.locator("[data-testid=learn-chips] .learn-chip").all_inner_texts() == ["Hook type Question", "Device Talking head", "Offer type Discount", "CTA Shop now"]
    assert card.locator("[data-testid=learn-quote]").inner_text() == "Your liver is not a filter, it is a vitamin warehouse"
    assert "Names the pain" in card.inner_text()
    for ident in ("learn-seen", "learn-said", "learn-transcript"):
        d = card.locator(f"#{ident}")
        assert d.get_attribute("open") is None and not d.locator(".copyblock").is_visible()
    card.locator("#learn-seen summary").click()
    assert "bright kitchen" in card.locator("#learn-seen .copyblock").inner_text()
    assert EM not in card.inner_text()
    card.scroll_into_view_if_needed()
    shot(page, "own_03_ad_desktop")
    page.set_viewport_size({"width": 375, "height": 812})
    page.goto(f"/ad/{ids['full']}")
    expect(page.locator("[data-testid=learning-block]")).to_be_visible()
    no_sideways(page)
    page.locator("[data-testid=learning-block]").scroll_into_view_if_needed()
    shot(page, "own_04_ad_375")


def test_ad_page_shows_only_what_is_present(page, own, ctx, base_url):
    own("on")
    ids = ad_ids(ctx, base_url)
    page.goto(f"/ad/{ids['chip']}")
    card = page.locator("[data-testid=learning-block]")
    expect(card).to_be_visible()
    assert card.locator(".learn-chip").all_inner_texts() == ["Hook type Story"]
    assert card.locator("[data-testid=learn-quote]").count() == 0 and card.locator("details").count() == 0 and "Why it works" not in card.inner_text()
    page.goto(f"/ad/{ids['none']}")
    page.wait_for_selector("#ad-title")
    assert page.locator("[data-testid=learning-block]").count() == 0


def test_ad_page_without_the_table_is_unchanged(page, own, ctx, base_url):
    own("on")
    ids = ad_ids(ctx, base_url)
    own("off")
    page.goto(f"/ad/{ids['full']}")
    page.wait_for_selector("#ad-title")
    assert page.locator("[data-testid=learning-block]").count() == 0


# ------------------------------------------------------------------------------------------------ competitors page

def goto_comp(page):
    page.goto("/competitors")
    page.wait_for_selector("[data-testid=comp-page]")
    page.wait_for_function("() => !document.querySelector('#view').hasAttribute('aria-busy')")
    page.wait_for_selector("[data-testid=comp-learn]")


def chart(page, key):
    return page.locator(f"[data-testid=comp-lchart][data-key={key}]")


def test_without_our_data_hook_device_offer_keep_no_equivalent(page, comp):
    comp("on", True, False)
    goto_comp(page)
    for k in ("hook_type", "device", "offer_type"):
        assert "No Lonvera equivalent" in chart(page, k).inner_text()
    assert page.locator("[data-testid=comp-lwork]").count() == 0 and page.locator("[data-testid=comp-own]").count() == 0


def test_our_share_and_what_is_working_lines(page, comp):
    comp("on", True, True)
    goto_comp(page)
    for k in ("hook_type", "device", "offer_type"):
        c = chart(page, k)
        assert "No Lonvera equivalent" not in c.inner_text() and c.locator(".comp-dbar-l").count() >= 1
    assert page.locator("[data-testid=comp-lwork]").count() == 4
    line = chart(page, "hook_type").locator("[data-testid=comp-lwork]").inner_text()
    m = re.fullmatch(r"What is working for us\. Our best hook type on NC ROAS is question at 0\.81 on \$24K, competitors use it (\d+)% of the time\. Next: story at 0\.70 on \$2\.4K \(thin sample\), competitors (\d+)%\.", line)
    assert m, line
    q_bar = chart(page, "hook_type").locator("[data-testid=comp-lbars-hook_type] li[data-value=Question] .comp-bar-n").inner_text().split("\n")[0].strip()
    assert q_bar == m.group(1) + "%"                                                           # the sentence and the bar are the same number
    assert chart(page, "hook_type").locator("[data-testid=comp-lwork]").get_attribute("data-thin") == "1"
    assert chart(page, "device").locator("[data-testid=comp-lwork]").get_attribute("data-thin") == "0"
    assert chart(page, "proof_mechanism").locator("[data-testid=comp-lwork]").count() == 0    # no data for that dimension: no line
    assert "NC ROAS is Upstack new customer revenue divided by spend" in page.locator("[data-testid=comp-lwnote]").inner_text()
    assert EM not in page.locator("[data-testid=comp-learn]").inner_text()
    chart(page, "hook_type").scroll_into_view_if_needed()
    shot(page, "own_05_learning_desktop")


def test_own_landing_page_panel(page, comp):
    comp("on", True, True)
    goto_comp(page)
    panel = page.locator("[data-testid=comp-own]")
    expect(panel).to_be_visible()
    cards = panel.locator("[data-testid=comp-own-card]")
    assert cards.count() == 2
    first = cards.first
    assert first.locator("[data-testid=comp-lptype]").inner_text() == "Advertorial" and first.locator("[data-testid=comp-lpthumb]").count() == 1
    assert first.locator(".comp-own-nums").inner_text() == "Last 30 days: $6,900 spend, NC ROAS 0.64, Meta ROAS 0.94"
    assert first.locator("[data-testid=comp-own-fams] li").all_inner_texts() == ["Bull-33-B3-Hook 2 $4,200", "cedFat5 $1,800", "Liver-12-A $900"]
    assert "3 ad families point here" == first.locator("[data-testid=comp-lpads]").inner_text()
    assert "No ad family is linked" in cards.nth(1).inner_text() and cards.nth(1).locator("[data-testid=comp-lptypo]").count() == 1
    assert panel.locator("[data-testid=comp-own-compare] li").all_inner_texts() == [
        "Page type: ours are advertorial and product page. Competitors: 50% product page, 25% advertorial.",
        "Proof: Competitors: 100% use reviews (we do); 25% use before and after (we do not); 25% use doctor quote (we do not).",
        "Guarantee: ours is 90 days. Competitors: the most common is 30 days (50% of pages that state one)."]
    assert EM not in panel.inner_text()
    first.locator("[data-testid=comp-lpopen]").click()
    lb = page.locator("[data-testid=comp-lplb]")
    expect(lb).to_be_visible()
    assert "3 ad families point to this page." in lb.inner_text() and "Lonvera" in lb.inner_text()
    page.keyboard.press("Escape")
    panel.scroll_into_view_if_needed()
    shot(page, "own_06_own_lp_desktop")


def test_competitor_page_patterns_do_not_count_ours(page, comp):
    comp("on", True, True)
    goto_comp(page)
    assert page.locator("[data-testid=comp-lpat]").inner_text().count("Lonvera") == 0
    assert "4 landing pages read this week" in page.locator("[data-testid=comp-lpat]").inner_text()           # the four competitor pages; ours are two more rows in the same table
    counts = page.locator("[data-testid=comp-lpchart][data-key=page_type] .comp-bar-n").all_inner_texts()
    assert sum(int(c.split()[0]) for c in counts) == 4


def test_own_panel_hidden_without_own_pages(page, comp):
    comp("on", True, False)
    goto_comp(page)
    assert page.locator("[data-testid=comp-own]").count() == 0


def test_competitors_own_side_phone_width(page, comp):
    comp("on", True, True)
    page.set_viewport_size({"width": 375, "height": 812})
    goto_comp(page)
    expect(page.locator("[data-testid=comp-own]")).to_be_visible()
    no_sideways(page)
    chart(page, "hook_type").scroll_into_view_if_needed()
    shot(page, "own_07_learning_375")
    page.locator("[data-testid=comp-own]").scroll_into_view_if_needed()
    shot(page, "own_08_own_lp_375")
