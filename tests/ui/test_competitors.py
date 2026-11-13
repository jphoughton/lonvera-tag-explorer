"""Competitors page (/competitors) against the mock (tests/ui/mock_competitors.py): pulse strip, "Worth a look" rail, lightbox, brand wall with in-place detail gallery, angle chart, the old sortable table
(?view=table), week picker, the All / Video / Image toggle kept in the URL, typographic cards when no thumbnail is stored, first-snapshot week, the optional news and Reddit cards, no sideways scroll at 375 px, no em dashes."""
import pytest

pytestmark = pytest.mark.mock_only


def idle(page):
    page.wait_for_function("() => !document.querySelector('#view').hasAttribute('aria-busy')")


def goto(page, qs=""):
    page.goto("/competitors" + qs)
    page.wait_for_selector("[data-testid=comp-page]")
    idle(page)


def brands(page):
    return page.locator("[data-testid=comp-row]").evaluate_all("rs => rs.map(r => r.dataset.brand)")


def cell(page, brand, col):
    return page.locator(f"[data-testid=comp-row][data-brand='{brand}'] [data-col={col}]").text_content().strip()


def test_table_facts_and_no_em_dash(page):
    goto(page, "?view=table")
    assert page.locator("h1").text_content() == "Competitors"
    assert set(brands(page)) == {"Acme", "Bolt & Co", "Cedar", "Dune Labs", "Ember Health", "Fig & Root", "Gild"}
    assert cell(page, "Acme", "active_ads") == "3" and cell(page, "Acme", "new_ads") == "2" and cell(page, "Acme", "stopped_ads") == "2"
    assert "Stale" in page.locator("[data-testid=comp-row][data-brand='Bolt & Co'] [data-testid=comp-status]").text_content()
    facts = page.locator("[data-testid=comp-fact]").all_text_contents()
    assert facts and any("Marrow for joints" in f for f in facts) and any("could not be read" in f for f in facts)
    assert "—" not in page.locator("[data-testid=comp-page]").text_content()
    assert "carousels count as image" in page.locator("[data-testid=comp-note]").text_content()


def test_sort_by_column(page):
    goto(page, "?view=table")
    assert brands(page)[0] == "Ember Health"                             # default: most active ads first
    page.click("[data-testid=comp-sort-brand]")
    assert brands(page)[:3] == ["Acme", "Bolt & Co", "Cedar"]
    page.click("[data-testid=comp-sort-brand]")
    assert brands(page)[:2] == ["Gild", "Fig & Root"] and brands(page)[-1] == "Acme"
    page.click("[data-testid=comp-sort-max_days_running]")
    assert brands(page)[0] == "Dune Labs"


def test_row_opens_detail_with_links(page):
    goto(page, "?view=table")
    page.click("[data-testid=comp-row][data-brand=Acme] [data-testid=comp-name]")
    page.wait_for_selector("[data-brand=Acme] [data-testid=comp-longest]")
    panel = page.locator("tr.comp-detailrow[data-brand=Acme]")
    assert panel.is_visible()
    hrefs = panel.locator("[data-testid=comp-adlink]").evaluate_all("as => as.map(a => a.href)")
    assert "https://www.facebook.com/ads/library/?id=a1" in hrefs
    assert panel.locator("[data-testid=comp-adlink]").first.get_attribute("rel") == "noopener noreferrer"
    assert "Marrow for joints" in panel.locator("[data-testid=comp-angles]").text_content()
    assert "acme.com/new" in panel.locator("[data-testid=comp-pages]").text_content()
    assert panel.locator("[data-testid=comp-new] tbody tr").count() == 2
    assert "q=acme" in panel.locator("[data-testid=comp-brandlink]").get_attribute("href")
    page.click("[data-testid=comp-row][data-brand=Acme] [data-testid=comp-name]")
    assert not panel.is_visible()


def test_format_toggle_filters_everything_and_lives_in_the_url(page):
    goto(page, "?view=table")
    page.click("[data-testid=comp-format-video]")
    page.wait_for_function("() => document.querySelector(\"[data-testid=comp-row][data-brand=Acme] [data-col=active_ads]\").textContent.trim() === '2'")
    assert "format=video" in page.url
    assert cell(page, "Bolt & Co", "active_ads") == "0" and page.locator("[data-testid=comp-format-video]").get_attribute("aria-pressed") == "true"
    page.click("[data-testid=comp-row][data-brand=Acme] [data-testid=comp-name]")
    page.wait_for_selector("[data-brand=Acme] [data-testid=comp-longest]")
    fmts = page.locator("tr.comp-detailrow[data-brand=Acme] [data-testid=comp-longest] tbody tr td:nth-child(3)").all_text_contents()
    assert fmts and set(fmts) == {"video"}
    goto(page, "?view=table&format=image")                               # reload from the URL
    assert page.locator("[data-testid=comp-format-image]").get_attribute("aria-pressed") == "true" and cell(page, "Acme", "active_ads") == "1"
    page.click("[data-testid=comp-format-all]")
    page.wait_for_function("() => document.querySelector(\"[data-testid=comp-row][data-brand=Acme] [data-col=active_ads]\").textContent.trim() === '3'")
    assert "format=video" not in page.url and "format=image" not in page.url


def test_first_snapshot_week(page):
    goto(page, "?view=table")
    page.select_option("[data-testid=comp-week]", "2026-11-09")
    page.wait_for_selector("[data-testid=comp-notice]")
    assert "First snapshot" in page.locator("[data-testid=comp-notice]").text_content()
    assert cell(page, "Acme", "change") == "–" and cell(page, "Acme", "new_ads") == "–"
    page.click("[data-testid=comp-row][data-brand=Acme] [data-testid=comp-name]")
    page.wait_for_selector("[data-testid=comp-first]")
    assert "week=2026-11-09" in page.url


def test_chatter_only_when_present(page, ctx, base_url):
    ctx.request.post(base_url + "/__test/competitors/chatter?on=0")
    goto(page)
    assert page.locator("[data-testid=comp-chatter-sec]").count() == 0
    ctx.request.post(base_url + "/__test/competitors/chatter?on=1")
    try:
        goto(page)
        assert "Is Acme worth it?" in page.locator("[data-testid=comp-chatter]").text_content()
        card = page.locator("[data-testid=comp-newscard]").first
        assert "Reddit" in card.text_content() and "Acme" in card.text_content() and "3 days ago" in card.text_content()
        assert card.locator("a").get_attribute("href") == "https://www.reddit.com/r/x/1"
        assert page.locator("[data-testid=comp-chatter-sec] h2").text_content() == "News and Reddit"
    finally:
        ctx.request.post(base_url + "/__test/competitors/chatter?on=0")


def test_table_scrolls_inside_its_box_on_a_phone(page):
    page.set_viewport_size({"width": 375, "height": 800})
    goto(page, "?view=table")
    page.click("[data-testid=comp-row][data-brand=Acme] [data-testid=comp-name]")
    page.wait_for_selector("[data-brand=Acme] [data-testid=comp-longest]")
    assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")
    assert page.evaluate("() => { const s = document.querySelector('[data-testid=comp-scroll]'); return s.scrollWidth > s.clientWidth; }")      # the wide table scrolls inside its own box


def test_nav_has_competitors_tab(page):
    goto(page)
    assert page.locator("[data-testid=nav-competitors]").count() >= 1


# ---------- the visual page ----------
def media(ctx, base_url, on):
    ctx.request.post(base_url + f"/__test/competitors/media?on={1 if on else 0}")


def video(ctx, base_url, on):
    ctx.request.post(base_url + f"/__test/competitors/video?on={1 if on else 0}")


def test_pulse_strip_and_first_snapshot_notice(page):
    goto(page)
    nums = {k: page.locator(f"[data-testid=comp-pulse-{k}] strong").text_content().strip() for k in ("brands", "active", "new", "longest")}
    assert nums == {"brands": "7", "active": "16", "new": "6", "longest": "317 days"}
    assert "Dune Labs" in page.locator("[data-testid=comp-pulse-longest]").text_content()
    page.select_option("[data-testid=comp-week]", "2026-11-09")
    page.wait_for_selector("[data-testid=comp-notice]")
    assert page.locator("[data-testid=comp-pulse-new] strong").text_content().strip() == "–" and "First snapshot" in page.locator("[data-testid=comp-notice]").text_content()


def test_spotlight_rail_cards_and_badges(page):
    goto(page)
    cards = page.locator("[data-testid=comp-spot]")
    assert 8 <= cards.count() <= 12
    badges = page.locator("[data-testid=comp-spot] .comp-badge").all_text_contents()
    assert any(b.endswith("days running") for b in badges) and "Angle Lonvera does not run" in badges and "New this week" in badges
    assert page.evaluate("() => getComputedStyle(document.querySelector('[data-testid=comp-rail]')).scrollSnapType").startswith("x")
    assert page.evaluate("() => { const r = document.querySelector('[data-testid=comp-rail]'); return r.scrollWidth > r.clientWidth; }")
    first = cards.first
    assert first.locator("[data-testid=comp-adlink]").get_attribute("href").startswith("https://www.facebook.com/ads/library/?id=")
    assert page.locator("[data-testid=comp-spot]").first.locator("[data-testid=comp-thumb]").count() == 1          # a stored thumbnail shows as a picture
    page.wait_for_function("() => { const i = document.querySelector('[data-testid=comp-thumb] img'); return i && i.complete && i.naturalWidth > 0; }")
    assert page.locator("[data-testid=comp-typo]").count() >= 1                                                    # ads without one show the hook on a tinted card


def test_lightbox_open_arrows_keys_swipe_and_close(page):
    goto(page)
    page.locator("[data-testid=comp-spot] [data-testid=comp-card-open]").first.click()
    lb = page.locator("[data-testid=comp-lb]")
    assert lb.is_visible()
    n = page.locator("[data-testid=comp-spot]").count()
    assert page.locator("[data-testid=comp-lb-count]").text_content() == f"1 / {n}"
    assert "I stopped the shedding" in lb.text_content() and "See why thousands switched" in page.locator("[data-testid=comp-lb-text]").text_content()
    assert page.locator("[data-testid=comp-lb-landing]").get_attribute("href") == "https://dune.co/hair" and page.locator("[data-testid=comp-lb-adlink]").get_attribute("href").endswith("?id=d1")
    page.click("[data-testid=comp-lb-next]")
    assert page.locator("[data-testid=comp-lb-count]").text_content() == f"2 / {n}"
    page.keyboard.press("ArrowRight")
    assert page.locator("[data-testid=comp-lb-count]").text_content() == f"3 / {n}"
    page.keyboard.press("ArrowLeft"); page.keyboard.press("ArrowLeft")
    assert page.locator("[data-testid=comp-lb-count]").text_content() == f"1 / {n}"
    page.click("[data-testid=comp-lb-prev]")                                    # wraps around
    assert page.locator("[data-testid=comp-lb-count]").text_content() == f"{n} / {n}"
    box = page.locator("[data-testid=comp-lb] .comp-lb-body").bounding_box()
    page.mouse.move(box["x"] + 200, box["y"] + 60); page.mouse.down(); page.mouse.move(box["x"] + 40, box["y"] + 60, steps=5); page.mouse.up()      # swipe left = next
    assert page.locator("[data-testid=comp-lb-count]").text_content() == f"1 / {n}"
    page.keyboard.press("Escape")
    assert not lb.is_visible()
    page.locator("[data-testid=comp-spot] [data-testid=comp-card-open]").first.click()
    page.click("[data-testid=comp-lb-close]")
    assert not lb.is_visible()


def test_brand_wall_tiles_sort_and_thumbs(page):
    goto(page)
    tiles = page.locator("[data-testid=comp-tile]")
    assert tiles.count() == 7 and page.locator("[data-testid=comp-view-wall]").get_attribute("aria-pressed") == "true"
    order = lambda: page.locator("[data-testid=comp-tile]").evaluate_all("ts => ts.map(t => t.dataset.brand)")
    assert order()[0] == "Ember Health"                                         # most ads (6)
    t = page.locator("[data-testid=comp-tile][data-brand=Acme]")
    assert t.locator("[data-testid=comp-tile-ads]").text_content() == "3" and "0 vs last week" in t.locator("[data-testid=comp-tile-change]").text_content()
    assert "+3 vs last week" in page.locator("[data-testid=comp-tile][data-brand='Ember Health'] [data-testid=comp-tile-change]").text_content()
    assert t.locator(".comp-mini").count() == 3 and t.locator("[data-testid=comp-mini-img]").count() == 2 and t.locator("[data-testid=comp-mini-typo]").count() == 1
    assert "Knee pain" in t.locator("[data-testid=comp-tile-angle]").text_content() and "comp-chip-hl" in t.locator("[data-testid=comp-tile-angle]").get_attribute("class")
    assert t.locator(".comp-split").get_attribute("title") == "67% video, 33% image"
    page.select_option("[data-testid=comp-tilesort]", "max_days_running")
    assert order()[0] == "Dune Labs"
    page.select_option("[data-testid=comp-tilesort]", "new_ads")
    assert order()[0] == "Ember Health"
    page.select_option("[data-testid=comp-tilesort]", "video_share")
    assert order()[0] in ("Cedar", "Dune Labs") and order()[-1] in ("Bolt & Co", "Gild")


def test_brand_detail_gallery_angles_pages_and_lightbox(page):
    goto(page)
    page.click("[data-testid=comp-tile][data-brand='Ember Health'] [data-testid=comp-tile-open]")
    page.wait_for_selector("[data-testid=comp-bpanel] [data-testid=comp-gallery]")
    panel = page.locator("[data-testid=comp-bpanel]")
    assert panel.locator("[data-testid=comp-gcard]").count() == 6
    assert panel.locator("[data-testid=comp-gcard] .comp-badge", has_text="New this week").count() == 4 and panel.locator("[data-testid=comp-gcard] .comp-badge", has_text="97 days running").count() == 1
    assert "q=ember" in panel.locator("[data-testid=comp-brandlink]").get_attribute("href")
    assert "Knee pain" in panel.locator("[data-testid=comp-bangles]").text_content() and panel.locator("[data-testid=comp-bangles] .comp-bar-hl").count() == 1
    assert "ember.com/new" in panel.locator("[data-testid=comp-bpages]").text_content()
    panel.locator("[data-testid=comp-gcard] [data-testid=comp-card-open]").nth(2).click()
    assert page.locator("[data-testid=comp-lb-count]").text_content() == "3 / 6"
    page.keyboard.press("Escape")
    page.click("[data-testid=comp-bclose]")
    assert page.locator("[data-testid=comp-bpanel]").count() == 0


def test_angle_chart_highlights_angles_we_do_not_run(page):
    goto(page)
    rows = page.locator("[data-testid=comp-anglechart] .comp-bar")
    assert 2 <= rows.count() <= 10
    hl = page.locator("[data-testid=comp-anglechart] .comp-bar-hl")
    assert hl.count() >= 1 and "Knee pain" in hl.first.text_content() and "Lonvera does not run" in hl.first.text_content()
    assert "does not run" in page.locator("[data-testid=comp-anglechart-sec] > p").text_content()
    first = page.locator("[data-testid=comp-anglechart] .comp-bar-fill").first.bounding_box()["width"]
    last = page.locator("[data-testid=comp-anglechart] .comp-bar-fill").last.bounding_box()["width"]
    assert first > last                                                          # bars scale with the number of ads


def test_table_toggle_and_format_toggle_on_the_wall(page):
    goto(page)
    assert page.locator("[data-testid=comp-table]").count() == 0
    page.click("[data-testid=comp-view-table]")
    page.wait_for_selector("[data-testid=comp-table]")
    assert "view=table" in page.url and page.locator("[data-testid=comp-rail]").count() == 0
    page.click("[data-testid=comp-view-wall]")
    page.wait_for_selector("[data-testid=comp-wall]")
    assert "view=table" not in page.url
    page.click("[data-testid=comp-format-image]")
    page.wait_for_function("() => document.querySelector('[data-testid=comp-pulse-active] strong').textContent.trim() === '8'")
    assert "format=image" in page.url
    assert set(page.locator("[data-testid=comp-spot] .comp-fmt").all_text_contents()) == {"Image"}
    assert page.locator("[data-testid=comp-tile][data-brand=Acme] [data-testid=comp-tile-ads]").text_content() == "1"


def test_typographic_fallback_when_no_thumbnail_is_stored(page, ctx, base_url):
    media(ctx, base_url, False)
    video(ctx, base_url, False)
    try:
        goto(page)
        assert page.locator("[data-testid=comp-thumb]").count() == 0 and page.locator("[data-testid=comp-mini-img]").count() == 0
        assert page.locator("[data-testid=comp-spot] [data-testid=comp-typo]").count() == page.locator("[data-testid=comp-spot]").count()
        txt = page.locator("[data-testid=comp-spot] .comp-typo-text").first.text_content()
        assert "I stopped the shedding" in txt
        a = page.evaluate("() => getComputedStyle(document.querySelector('[data-testid=comp-spot] .comp-typo')).getPropertyValue('--tint-bg')")
        assert a.strip().startswith("hsl(")
        tints = page.evaluate("() => [...document.querySelectorAll('[data-testid=comp-spot] .comp-typo')].map(e => [e.closest('article').querySelector('.comp-card-brand').textContent, e.style.getPropertyValue('--tint-bg')])")
        by_brand = {}
        for b, c in tints:
            by_brand.setdefault(b, set()).add(c)
        assert all(len(v) == 1 for v in by_brand.values()) and len({next(iter(v)) for v in by_brand.values()}) > 1      # same brand, same tint; brands differ
        page.locator("[data-testid=comp-spot] [data-testid=comp-card-open]").first.click()
        assert page.locator("[data-testid=comp-lb] [data-testid=comp-typo]").count() == 1
        page.keyboard.press("Escape")
    finally:
        media(ctx, base_url, True)
        video(ctx, base_url, True)


def test_broken_thumbnail_falls_back_to_the_typographic_card(page, ctx, base_url):
    goto(page)
    page.evaluate("() => { const i = document.querySelector('[data-testid=comp-spot] [data-testid=comp-thumb] img'); i.dispatchEvent(new Event('error')); }")
    assert page.locator("[data-testid=comp-spot]").first.locator("[data-testid=comp-typo]").count() == 1


def test_visual_page_has_no_em_dash_and_phone_width_does_not_scroll(page):
    page.set_viewport_size({"width": 375, "height": 800})
    goto(page)
    page.click("[data-testid=comp-tile][data-brand=Acme] [data-testid=comp-tile-open]")
    page.wait_for_selector("[data-testid=comp-gallery]")
    assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")
    page.locator("[data-testid=comp-gcard] [data-testid=comp-card-open]").first.click()
    assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")
    box = page.locator("[data-testid=comp-lb]").bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= 375
    page.keyboard.press("Escape")
    assert "\u2014" not in page.locator("[data-testid=comp-page]").text_content()
    assert page.evaluate("() => { const r = document.querySelector('[data-testid=comp-rail]'); return r.scrollWidth > r.clientWidth && r.getBoundingClientRect().right <= window.innerWidth; }")


# ---- playable videos in the lightbox ----
def open_card(page, brand, hook_part):
    page.locator(f"[data-testid=comp-spot]:has-text('{hook_part}') [data-testid=comp-card-open]").first.click()
    page.wait_for_selector("[data-testid=comp-lb]", state="visible")


def test_saved_video_gets_a_player_in_the_lightbox_and_a_play_badge_on_the_card(page):
    goto(page)
    first = page.locator("[data-testid=comp-spot]").first
    assert first.locator("[data-testid=comp-play]").count() == 1                         # the saved copy shows a play badge on the card
    imgs = page.locator("[data-testid=comp-spot]:not(:has([data-testid=comp-play])) [data-testid=comp-play]").count()
    assert imgs == 0
    first.locator("[data-testid=comp-card-open]").click()
    v = page.locator("[data-testid=comp-lb] [data-testid=comp-video]")
    assert v.count() == 1 and v.get_attribute("src") == "/api/competitors/video/d1"
    assert v.evaluate("e => e.controls && e.hasAttribute('playsinline') && e.preload === 'metadata' && !e.autoplay && !e.muted && e.paused")
    assert (v.get_attribute("poster") or "").startswith("/api/competitors/media/d1")
    assert page.locator("[data-testid=comp-play-big]").is_visible() and page.locator("[data-testid=comp-play-big]").text_content().strip().endswith("Play")
    assert page.locator("[data-testid=comp-lb-novideo]").count() == 0
    page.keyboard.press("Escape")


def test_play_starts_and_next_prev_and_close_pause_it(page):
    goto(page)
    page.locator("[data-testid=comp-spot] [data-testid=comp-card-open]").first.click()
    page.wait_for_function("() => document.querySelector('[data-testid=comp-video]').readyState >= 1")
    page.click("[data-testid=comp-play-big]")
    page.wait_for_function("() => { const v = document.querySelector('[data-testid=comp-video]'); return !v.paused && v.currentTime > 0; }")
    assert not page.locator("[data-testid=comp-play-big]").is_visible()
    page.evaluate("() => { window.__v = document.querySelector('[data-testid=comp-video]'); }")
    page.click("[data-testid=comp-lb-next]")                                             # next: the old video is paused (and gone from the stage)
    assert page.evaluate("() => window.__v.paused")
    page.click("[data-testid=comp-lb-prev]")                                             # back: a fresh player, not playing
    assert page.evaluate("() => document.querySelector('[data-testid=comp-video]').paused")
    page.click("[data-testid=comp-play-big]")
    page.wait_for_function("() => !document.querySelector('[data-testid=comp-video]').paused")
    page.keyboard.press("ArrowRight")                                                    # keyboard arrows still navigate while the video has focus
    assert page.locator("[data-testid=comp-lb-count]").text_content().startswith("2 /")
    page.keyboard.press("ArrowLeft")
    page.click("[data-testid=comp-play-big]")
    page.wait_for_function("() => !document.querySelector('[data-testid=comp-video]').paused")
    page.evaluate("() => { window.__v = document.querySelector('[data-testid=comp-video]'); }")
    page.click("[data-testid=comp-lb-close]")                                            # close pauses
    assert page.evaluate("() => window.__v.paused")


def test_video_without_a_saved_copy_keeps_the_still_and_says_so(page):
    goto(page, "?format=video")
    page.locator("[data-testid=comp-spot]:has-text('Knees that do not announce') [data-testid=comp-card-open]").first.click()
    lb = page.locator("[data-testid=comp-lb]")
    assert lb.is_visible() and page.locator("[data-testid=comp-lb] [data-testid=comp-video]").count() == 0
    assert page.locator("[data-testid=comp-lb-novideo]").text_content().strip() == "Video not saved. Open in the Ad Library to watch."
    assert page.locator("[data-testid=comp-lb] [data-testid=comp-typo], [data-testid=comp-lb] [data-testid=comp-thumb]").count() == 1
    assert page.locator("[data-testid=comp-lb-adlink]").count() == 1
    page.keyboard.press("Escape")
    img = None
    goto(page, "?format=image")                                                          # an image ad never gets the line
    page.locator("[data-testid=comp-spot] [data-testid=comp-card-open]").first.click()
    assert page.locator("[data-testid=comp-lb-novideo]").count() == 0
    page.keyboard.press("Escape")


def test_play_badge_on_brand_gallery_cards_and_video_off_removes_every_player(page, ctx, base_url):
    goto(page)
    page.click("[data-testid=comp-tile][data-brand='Dune Labs'] [data-testid=comp-tile-open]")
    page.wait_for_selector("[data-testid=comp-gallery]")
    cards = page.locator("[data-testid=comp-gcard]")
    with_badge = cards.filter(has=page.locator("[data-testid=comp-play]"))
    assert with_badge.count() == 1 and "I stopped the shedding" in with_badge.first.text_content()
    video(ctx, base_url, False)
    try:
        goto(page)
        assert page.locator("[data-testid=comp-play]").count() == 0
        page.locator("[data-testid=comp-spot] [data-testid=comp-card-open]").first.click()
        assert page.locator("[data-testid=comp-video]").count() == 0 and page.locator("[data-testid=comp-lb-novideo]").count() == 1
        page.keyboard.press("Escape")
    finally:
        video(ctx, base_url, True)


def test_player_has_no_em_dash_and_fits_at_375_px(page):
    page.set_viewport_size({"width": 375, "height": 800})
    goto(page)
    page.locator("[data-testid=comp-spot] [data-testid=comp-card-open]").first.click()
    assert page.locator("[data-testid=comp-video]").count() == 1
    assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")
    box = page.locator("[data-testid=comp-lb]").bounding_box()
    vb = page.locator("[data-testid=comp-video]").bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= 375 and vb["x"] >= 0 and vb["x"] + vb["width"] <= 375
    assert "\u2014" not in page.locator("[data-testid=comp-lb]").text_content()
    page.keyboard.press("Escape")


def test_lightbox_controls_do_not_overlap_the_text_at_375_px(page):
    page.set_viewport_size({"width": 375, "height": 700})
    goto(page)
    page.locator("[data-testid=comp-spot] [data-testid=comp-card-open]").first.click()
    assert page.locator("[data-testid=comp-lb]").is_visible()

    def box(sel):
        return page.locator(sel).bounding_box()

    def hit(a, b):
        return a["x"] < b["x"] + b["width"] and b["x"] < a["x"] + a["width"] and a["y"] < b["y"] + b["height"] and b["y"] < a["y"] + a["height"]

    text = box("[data-testid=comp-lb] .comp-lb-body")      # the scrolling area holding the video and every line of text; the controls sit outside it
    for sel in ("[data-testid=comp-lb-prev]", "[data-testid=comp-lb-next]", "[data-testid=comp-lb-count]"):
        b = box(sel)
        assert not hit(b, text), sel
    for sel in ("[data-testid=comp-lb-prev]", "[data-testid=comp-lb-next]"):
        b = box(sel)
        assert b["width"] >= 44 and b["height"] >= 44
    dlg = box("[data-testid=comp-lb]")
    for sel in ("[data-testid=comp-lb-prev]", "[data-testid=comp-lb-next]", "[data-testid=comp-lb-count]"):
        b = box(sel)
        assert b["y"] >= dlg["y"] and b["y"] + b["height"] <= dlg["y"] + dlg["height"] + 1
    assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")
    # long text scrolls inside the body, so its last line is reachable above the controls
    page.evaluate("() => { const b = document.querySelector('.comp-lb-body'); b.scrollTop = b.scrollHeight; }")
    last = page.locator("[data-testid=comp-lb] .comp-lb-links").bounding_box()
    bar = box("[data-testid=comp-lb-prev]")
    assert last["y"] + last["height"] <= bar["y"] + 1


def test_lightbox_keys_are_real_key_presses_and_do_not_hijack_inputs(page):
    goto(page)
    page.locator("[data-testid=comp-spot] [data-testid=comp-card-open]").first.click()
    lb = page.locator("[data-testid=comp-lb]")
    n = page.locator("[data-testid=comp-spot]").count()
    page.locator("[data-testid=comp-lb-next]").focus()
    page.keyboard.press("ArrowRight")
    assert page.locator("[data-testid=comp-lb-count]").text_content() == f"2 / {n}"
    page.evaluate("() => document.activeElement.blur()")
    page.keyboard.press("ArrowLeft")
    assert page.locator("[data-testid=comp-lb-count]").text_content() == f"1 / {n}"
    page.keyboard.press("Escape")
    assert not lb.is_visible()
    # closed: arrows must do nothing and no listener lingers
    page.keyboard.press("ArrowRight")
    assert page.locator("[data-testid=comp-lb-count]").text_content() == f"1 / {n}"
    # an input inside the open dialog keeps its arrows
    page.locator("[data-testid=comp-spot] [data-testid=comp-card-open]").first.click()
    page.evaluate("() => { const i = document.createElement('input'); i.id = 'zz'; document.querySelector('[data-testid=comp-lb]').append(i); i.focus(); }")
    page.keyboard.press("ArrowRight")
    assert page.locator("[data-testid=comp-lb-count]").text_content() == f"1 / {n}"
