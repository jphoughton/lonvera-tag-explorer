"""Competitors learning layer on the mock (tests/ui/mock_competitors.py): tag chips, why-it-works, transcript disclosures and landing page line in the lightbox, the Tags filter row kept in the URL, the
'What competitors are doing' charts and highlight, landing page cards and lightbox, brand tag mix, hidden when there are no tags, 375 px, no em dashes."""
import pytest

pytestmark = pytest.mark.mock_only


@pytest.fixture()
def mode(ctx, base_url):
    def set_mode(tags="on", lp=True):
        ctx.request.post(base_url + f"/__test/competitors/tags?mode={tags}")
        ctx.request.post(base_url + f"/__test/competitors/lp?on={'1' if lp else '0'}")
    yield set_mode
    ctx.request.post(base_url + "/__test/competitors/tags?mode=off")
    ctx.request.post(base_url + "/__test/competitors/lp?on=0")


def goto(page, qs=""):
    page.goto("/competitors" + qs)
    page.wait_for_selector("[data-testid=comp-page]")
    page.wait_for_function("() => !document.querySelector('#view').hasAttribute('aria-busy')")


def wait_active(page, n):
    page.wait_for_function("(n) => { const e = document.querySelector('[data-testid=comp-pulse-active] strong'); return !!e && e.textContent.trim() === n; }", arg=n)


def open_ad(page, hook_part):
    page.locator(f"[data-testid=comp-spot]:has-text('{hook_part}') [data-testid=comp-card-open]").first.click()
    page.wait_for_selector("[data-testid=comp-lb]", state="visible")


def test_without_tags_nothing_new_is_drawn(page, mode):
    mode("off", False)
    goto(page)
    for t in ("comp-tags", "comp-tags-empty", "comp-learn"):
        assert page.locator(f"[data-testid={t}]").count() == 0
    open_ad(page, "vitamin warehouse")
    for t in ("comp-tchips", "comp-why", "comp-disc-transcript", "comp-lb-lp"):
        assert page.locator(f"[data-testid={t}]").count() == 0


def test_empty_tags_table_shows_only_the_one_message(page, mode):
    mode("empty", False)
    goto(page)
    assert page.locator("[data-testid=comp-tags-empty]").text_content() == "No tags yet. They fill in after the next tagging run."
    assert page.locator("[data-testid=comp-tags]").count() == 0 and page.locator("[data-testid=comp-learn]").count() == 0


def test_lightbox_shows_chips_why_quote_and_disclosures(page, mode):
    mode("on", True)
    goto(page)
    open_ad(page, "vitamin warehouse")
    lb = page.locator("[data-testid=comp-lb]")
    chips = lb.locator("[data-testid=comp-tchip]").all_text_contents()
    assert chips == ["Style: Testimonial", "Hook type: Question", "Device: Talking head", "Proof: Reviews", "Emotion: Relief", "Offer: Discount"]
    assert lb.locator("a[data-testid=comp-tchip]").count() == 4 and lb.locator("span[data-testid=comp-tchip]").count() == 2
    assert "Names the reader" in lb.locator("[data-testid=comp-why]").text_content()
    assert lb.locator("[data-testid=comp-quote]").text_content() == "Your liver is not a filter"
    d = lb.locator("[data-testid=comp-disc-transcript]")
    assert d.get_attribute("open") is None and not d.locator("p").is_visible()
    d.locator("summary").click()
    assert d.get_attribute("open") is not None and "vitamin warehouse" in d.locator("p").text_content()
    assert lb.locator("[data-testid=comp-disc-seen]").count() == 1 and lb.locator("[data-testid=comp-disc-said]").count() == 1
    assert "Advertorial" in lb.locator("[data-testid=comp-lb-lp]").text_content()


def test_ads_without_tag_parts_render_no_empty_bits(page, mode):
    mode("on", True)
    goto(page)
    page.keyboard.press("Escape")
    page.locator("[data-testid=comp-spot]:has-text('Your knees are asking') [data-testid=comp-card-open]").first.click()
    lb = page.locator("[data-testid=comp-lb]")
    assert lb.locator("[data-testid=comp-tchips]").count() == 1
    for t in ("comp-why", "comp-quote", "comp-disc-transcript", "comp-disc-seen", "comp-disc-said"):
        assert lb.locator(f"[data-testid={t}]").count() == 0


def test_chip_applies_the_filter_and_it_lives_in_the_url(page, mode):
    mode("on", False)
    goto(page)
    open_ad(page, "vitamin warehouse")
    page.locator("[data-testid=comp-lb] a[data-testid=comp-tchip][data-key=style]").click()
    wait_active(page, "7")
    assert "style=Testimonial" in page.url and not page.locator("[data-testid=comp-lb]").is_visible()
    assert page.locator("[data-testid=comp-tag-style] option:checked").text_content().startswith("Testimonial")
    goto(page, "?style=Testimonial&hook=Story")                         # reload from the URL
    wait_active(page, "4")
    assert page.locator("[data-testid=comp-tag-hook] option:checked").text_content().startswith("Story")


def test_tag_selects_narrow_wall_rail_and_charts_and_clear(page, mode):
    mode("on", False)
    goto(page)
    sel = page.locator("[data-testid=comp-tag-device]")
    assert {"Talking head (5)", "Split screen (4)", "Text on screen (4)", "Voiceover (3)"} <= set(sel.locator("option").all_text_contents())
    sel.select_option(label="Split screen (4)")
    wait_active(page, "4")
    assert "device=Split" in page.url
    assert page.locator("[data-testid=comp-tile][data-brand=Acme] [data-testid=comp-tile-ads]").text_content() == "0"
    assert page.locator("[data-testid=comp-tile][data-brand='Ember Health'] [data-testid=comp-tile-ads]").text_content() == "2"
    assert set(page.locator("[data-testid=comp-rail] [data-id]").evaluate_all("els => els.map(e => e.dataset.id)")) <= {"d2", "e1", "e7", "g1"}
    assert page.locator("[data-testid=comp-lgrid] [data-testid=comp-lchart][data-key=device] [data-testid=comp-lbars-device] li").count() == 1
    page.click("[data-testid=comp-tags-clear]")
    wait_active(page, "16")
    assert "device=" not in page.url


def test_learning_charts_highlight_and_sentences(page, mode):
    mode("on", True)
    goto(page)
    sec = page.locator("[data-testid=comp-learn]")
    assert sec.locator("h2").first.text_content() == "What competitors are doing"
    assert "tagged" in sec.locator("[data-testid=comp-lnote]").text_content() and "approximate" in sec.locator("[data-testid=comp-lnote]").text_content()
    keys = sec.locator("[data-testid=comp-lchart]").evaluate_all("els => els.map(e => e.dataset.key)")
    assert keys == ["style", "hook_type", "device", "proof_mechanism", "emotion", "offer_type"]
    style = sec.locator("[data-testid=comp-lchart][data-key=style]")
    assert style.locator("[data-testid=comp-lsent]").text_content() == "Competitors lean on testimonial 44% of the time, Lonvera 9% of spend."
    hl = style.locator("li[data-hl='1']")
    assert hl.count() == 1 and hl.get_attribute("data-value") == "Testimonial"
    assert "16 tagged ads" in style.text_content() and "No Lonvera equivalent" not in style.text_content()
    hook = sec.locator("[data-testid=comp-lchart][data-key=hook_type]")
    assert hook.locator("li[data-hl='1']").count() == 0 and "No Lonvera equivalent" in hook.text_content() and hook.locator(".comp-dbar-l").count() == 0
    assert sec.locator("[data-testid=comp-combo]").count() == 5 and "Story + Talking head" in sec.locator("[data-testid=comp-combo]").first.text_content()
    assert sec.locator("[data-testid=comp-combo-link]").first.get_attribute("href").startswith("https://www.facebook.com/ads/library/?id=")
    # the section sits between the brand wall and the angle chart
    order = page.evaluate("() => [...document.querySelectorAll('[data-testid=comp-wall-sec],[data-testid=comp-learn],[data-testid=comp-anglechart-sec]')].map(e => e.dataset.testid)")
    assert order == ["comp-wall-sec", "comp-learn", "comp-anglechart-sec"]


def test_learning_hidden_when_no_tags_and_no_landing_pages(page, mode):
    mode("off", False)
    goto(page)
    assert page.locator("[data-testid=comp-learn]").count() == 0


def test_landing_page_charts_and_changed_fact(page, mode):
    mode("on", True)
    goto(page)
    charts = page.locator("[data-testid=comp-lpchart]").evaluate_all("els => els.map(e => e.dataset.key)")
    assert charts == ["page_type", "proof", "urgency", "selling_points", "guarantee"]
    types = page.locator("[data-testid=comp-lpbars-page_type] li").all_text_contents()
    assert any(t.startswith("Product page") and t.endswith("2 pages") for t in types)
    assert page.locator("[data-testid=comp-lpbars-guarantee] li").count() == 3
    page.click("[data-testid=comp-changes] summary")
    facts = page.locator("[data-testid=comp-lpfact]").all_text_contents()
    assert any("Acme: New hero headline" in f for f in facts) and any("Ember Health: Price dropped" in f for f in facts)
    assert page.locator("[data-testid=comp-lpfactlink]").first.get_attribute("href") == "https://acme.com/join"


def test_brand_landing_page_cards_and_lightbox(page, mode):
    mode("on", True)
    goto(page)
    page.click("[data-testid=comp-tile][data-brand=Acme] [data-testid=comp-tile-open]")
    page.wait_for_selector("[data-testid=comp-lpcard]")
    card = page.locator("[data-testid=comp-lpcard][data-lp=lpA1]")
    assert card.locator("[data-testid=comp-lpthumb] img").count() == 1 and card.locator("[data-testid=comp-lptype]").text_content() == "Advertorial"
    assert "starving for marrow" in card.locator("[data-testid=comp-lptheme]").text_content()
    assert card.locator("[data-testid=comp-lppoints] li").count() == 5                       # up to five bullets
    assert "Buy 2 get 1 free" in card.locator("[data-testid=comp-lpoffer]").text_content() and "60-day money back" in card.locator("[data-testid=comp-lpoffer]").text_content()
    assert card.locator("[data-testid=comp-lpproof] span").all_text_contents() == ["Reviews", "Doctor quote"] and card.locator("[data-testid=comp-lpurgency] span").all_text_contents() == ["Countdown timer"]
    assert card.locator("[data-testid=comp-lpchg]").text_content() == "Changed this week" and "countdown timer" in card.text_content()
    assert "3 ads point here" in card.locator("[data-testid=comp-lpads]").text_content() and card.locator("[data-testid=comp-lplink]").get_attribute("href") == "https://acme.com/join"
    other = page.locator("[data-testid=comp-lpcard][data-lp=lpA2]")
    assert other.locator("[data-testid=comp-lptypo]").count() == 1 and "Beef liver, finally in a capsule" in other.locator("[data-testid=comp-lptypo]").text_content() and other.locator("[data-testid=comp-lpchg]").count() == 0
    assert page.locator("[data-testid=comp-bpages]").count() == 0                              # the plain counts are replaced
    card.locator("[data-testid=comp-lpopen]").click()
    lb = page.locator("[data-testid=comp-lplb]")
    assert lb.is_visible() and "Why your knees ache" in lb.locator("[data-testid=comp-lplb-headline]").text_content()
    txt = lb.text_content()
    assert "Does it work for me" in txt and "Start my order" in txt and "Opens with a story" in lb.locator("[data-testid=comp-lplb-why]").text_content() and "Extra point six" in txt
    page.keyboard.press("ArrowRight")
    assert "One simple bottle" in lb.locator("[data-testid=comp-lplb-theme]").text_content()
    page.keyboard.press("Escape")
    assert not lb.is_visible()


def test_ad_lightbox_landing_page_line_opens_that_card(page, mode):
    mode("on", True)
    goto(page)
    open_ad(page, "vitamin warehouse")
    page.click("[data-testid=comp-lb-lplink]")
    lb = page.locator("[data-testid=comp-lplb]")
    lb.wait_for(state="visible")
    assert "Why your knees ache" in lb.locator("[data-testid=comp-lplb-headline]").text_content() and not page.locator("[data-testid=comp-lb]").is_visible()


def test_brand_tag_mix_chips_apply_filters(page, mode):
    mode("on", False)
    goto(page)
    page.click("[data-testid=comp-tile][data-brand='Ember Health'] [data-testid=comp-tile-open]")
    page.wait_for_selector("[data-testid=comp-mix]")
    rows = page.locator("[data-testid=comp-mixrow]").evaluate_all("els => els.map(e => e.dataset.key)")
    assert rows == ["style", "hook_type", "device"]
    first = page.locator("[data-testid=comp-mixrow][data-key=style] [data-testid=comp-mixchip]").first
    assert first.text_content().strip().startswith("Studio") or first.text_content().strip().startswith("UGC demo")
    first.click()
    page.wait_for_function("() => location.search.includes('style=')")
    assert page.locator("[data-testid=comp-pulse-active] strong").count() == 1


def test_phone_width_no_sideways_scroll_and_no_em_dash(page, mode):
    mode("on", True)
    page.set_viewport_size({"width": 375, "height": 800})
    goto(page, "?style=Testimonial")
    assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")
    goto(page)
    page.locator("[data-testid=comp-learn]").scroll_into_view_if_needed()
    assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")
    page.click("[data-testid=comp-tile][data-brand=Acme] [data-testid=comp-tile-open]")
    page.wait_for_selector("[data-testid=comp-lpcard]")
    assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")
    page.locator("[data-testid=comp-lpopen]").first.click()
    box = page.locator("[data-testid=comp-lplb]").bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= 375
    page.keyboard.press("Escape")
    open_ad(page, "vitamin warehouse")
    assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")
    page.keyboard.press("Escape")
    assert "—" not in page.locator("[data-testid=comp-page]").text_content()
