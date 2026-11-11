"""Ad (creative) page, thumbnails, lightbox, live preview, hook card.

Split out of tests/ui/test_ui.py with no assertion changes. Shared helpers live in ui_helpers.py. Run: see the docstring of conftest.py.
"""
import re
from datetime import date, timedelta
from urllib.parse import parse_qs, urlencode, urlparse
import pytest
from playwright.sync_api import expect
from conftest import IS_MOCK, PASSWORD, SHOTS, USERNAME
from ui_helpers import *  # noqa: F401,F403

def test_lightbox_opens_from_any_thumbnail_and_closes_three_ways(page):
    page.goto(f"/p/{SLUG}?view=ads")
    wait_rows(page, "ads-table")
    thumb = page.locator("[data-testid=ads-table] tbody img.thumb.zoomable").first
    name_alt = thumb.get_attribute("alt")
    url = page.url
    thumb.click()
    dlg = page.locator(LB)
    expect(dlg).to_be_visible()
    assert dlg.get_attribute("aria-modal") == "true" and dlg.get_attribute("aria-label")
    assert page.url == url  # a thumbnail click does not follow the row link
    img = dlg.locator("img.lb-img")
    expect(img).to_be_visible()
    assert img.get_attribute("src").startswith("/media/") and img.get_attribute("alt")  # alt text
    assert page.evaluate("() => document.querySelector('.lightbox .lb-img').naturalWidth") > 0
    expect(dlg.locator("#lb-open")).to_have_text("Open ad page")
    expect(dlg.locator("#lb-live")).to_have_text("See live ad")
    assert dlg.locator(".modal-title").inner_text().strip()  # the ad name
    shot(page, "38_lightbox")
    page.keyboard.press("Escape")  # 1. Esc
    expect(dlg).to_have_count(0)
    assert page.evaluate("() => document.activeElement && document.activeElement.classList.contains('zoomable')")  # focus restored to the thumbnail
    thumb.click()
    expect(dlg).to_be_visible()
    page.mouse.click(5, 5)  # 2. click outside
    expect(dlg).to_have_count(0)
    thumb.click()
    dlg.locator(".modal-close").click()  # 3. the x button
    expect(dlg).to_have_count(0)
    # keyboard: the thumbnail is a focusable button (Enter opens)
    thumb.focus()
    page.keyboard.press("Enter")
    expect(dlg).to_be_visible()
    page.keyboard.press("Escape")
    assert name_alt


def test_lightbox_from_tree_rows_does_not_drop_down_the_row_and_opens_ad_page(page):
    drill_to_families(page)
    row = first_row(page)
    caret = row.locator("button.caret")
    expect(caret).to_have_attribute("aria-expanded", "false")
    row.locator("img.thumb.zoomable").click()
    expect(page.locator(LB)).to_be_visible()
    expect(page.locator(f"{BODY_ROWS} button.caret").first).to_have_attribute("aria-expanded", "false")  # the row stayed closed
    page.locator("#lb-open").click()  # Open ad page
    expect(page).to_have_url(re.compile(r"/ad/\d+"))
    expect(page.locator(LB)).to_have_count(0)
    expect(page.locator("#ad-title")).to_be_visible()


def test_lightbox_frames_arrows_keyboard_and_focus_trap(page):
    ad_id = first_video_ad_id(page)
    page.goto(f"/ad/{ad_id}")
    expect(page.locator("#ad-title")).to_be_visible()
    frames = page.locator("#media .frames button.media-link")
    assert frames.count() >= 2
    frames.nth(1).click()  # opens on the clicked frame
    dlg = page.locator(LB)
    expect(dlg).to_be_visible()
    n = page.locator(".lb-strip .lb-frame").count()
    assert n >= 3
    counter = dlg.locator(".lb-counter")
    assert re.fullmatch(rf"\d+ / {n}", counter.inner_text())
    start = int(counter.inner_text().split(" / ")[0])
    page.keyboard.press("ArrowRight")
    assert int(counter.inner_text().split(" / ")[0]) == start % n + 1
    page.keyboard.press("ArrowLeft")
    page.keyboard.press("ArrowLeft")
    assert int(counter.inner_text().split(" / ")[0]) == (start - 2) % n + 1
    src_before = dlg.locator("img.lb-img").get_attribute("src")
    dlg.locator(".lb-nav.next").click()
    assert dlg.locator("img.lb-img").get_attribute("src") != src_before
    dlg.locator(".lb-nav.prev").click()
    assert dlg.locator("img.lb-img").get_attribute("src") == src_before
    dlg.locator(".lb-strip .lb-frame").nth(n - 1).click()  # click a frame in the strip
    assert counter.inner_text() == f"{n} / {n}"
    page.keyboard.press("ArrowRight")  # wraps around
    assert counter.inner_text() == f"1 / {n}"
    alt = dlg.locator("img.lb-img").get_attribute("alt")
    assert re.search(r"frame \d+ of \d+", alt)
    # detail: ad name, tag and a transcript snippet
    expect(dlg.locator(".lb-tag")).to_contain_text("Tag:")
    expect(dlg.locator(".lb-transcript")).to_contain_text("Transcript:")
    assert len(dlg.locator(".lb-transcript").inner_text()) <= 320
    # focus stays inside the dialog (trap), forwards and backwards
    for _ in range(14):
        page.keyboard.press("Tab")
        assert page.evaluate("() => document.querySelector('.lightbox').contains(document.activeElement)")
    for _ in range(14):
        page.keyboard.press("Shift+Tab")
        assert page.evaluate("() => document.querySelector('.lightbox').contains(document.activeElement)")
    shot(page, "39_lightbox_frames")
    page.keyboard.press("Escape")
    expect(dlg).to_have_count(0)


@pytest.mark.mock_only
def test_see_the_ad_section_formats_iframe_and_sizes(page):
    ad_id = first_video_ad_id(page)
    reqs = []
    page.on("request", lambda r: reqs.append(r.url) if "/preview" in r.url and "/api/" in r.url else None)
    page.goto(f"/ad/{ad_id}")
    section = page.locator("#see-ad")
    expect(section).to_be_visible()
    expect(section.locator("h2")).to_have_text("See the ad")
    fr = section.locator("iframe.preview-frame")
    expect(fr).to_be_visible()
    assert fr.get_attribute("sandbox") == "allow-scripts allow-same-origin allow-popups" and fr.get_attribute("loading") == "lazy"
    assert "/mock/preview" in fr.get_attribute("src") and fr.get_attribute("title")
    assert fr.evaluate("e => [e.clientWidth, e.clientHeight]") == [400, 700]  # mobile feed
    page.wait_for_function("() => !document.querySelector('#see-ad .preview-msg.overlay')")  # spinner goes away once loaded
    assert [b.inner_text() for b in section.locator(".preview-formats button").all()] == ["Mobile feed", "Desktop feed", "Reels", "Stories"]
    for key, size in (("desktop_feed", [540, 600]), ("reels", [400, 700]), ("stories", [400, 700]), ("mobile_feed", [400, 700])):
        section.locator(f"button[data-format={key}]").click()
        page.wait_for_function("(k) => { const f = document.querySelector('#see-ad iframe.preview-frame'); return !!f && f.getAttribute('src').includes('format=' + k); }", arg=key)
        assert section.locator("iframe.preview-frame").evaluate("e => [e.clientWidth, e.clientHeight]") == size, key
        assert any(f"format={key}" in u for u in reqs)
    # the preview frame shows the mock ad
    assert "Mock Meta preview" in page.frame_locator("#see-ad iframe.preview-frame").locator("#mock-ad").inner_text()
    shot(page, "40_see_the_ad")


@pytest.mark.allow_errors
def test_preview_off_and_error_states(page):
    ad_id = first_video_ad_id(page)
    # preview switched off: a clear message, the links only, no iframe
    page.route("**/api/ads/*/preview*", lambda route: route.fulfill(status=200, content_type="application/json", body='{"enabled": false, "src": null, "format": "mobile_feed"}'))
    page.goto(f"/ad/{ad_id}")
    expect(page.locator("#see-ad")).to_contain_text("Live preview off")
    assert page.locator("#see-ad iframe").count() == 0 and page.locator("#see-ad .preview-formats").is_hidden()
    assert page.locator("#see-ad .preview-msg").get_attribute("data-state") == "off"
    # enabled but Meta returned an error
    page.unroute("**/api/ads/*/preview*")
    page.route("**/api/ads/*/preview*", lambda route: route.fulfill(status=200, content_type="application/json", body='{"enabled": true, "src": null, "error": "Meta rejected the preview request"}'))
    page.goto(f"/ad/{ad_id}")
    expect(page.locator("#see-ad")).to_contain_text("Preview unavailable")
    expect(page.locator("#see-ad")).to_contain_text("Meta rejected the preview request")
    assert page.locator("#see-ad iframe").count() == 0
    # an unsafe src is never framed
    page.unroute("**/api/ads/*/preview*")
    page.route("**/api/ads/*/preview*", lambda route: route.fulfill(status=200, content_type="application/json", body='{"enabled": true, "src": "javascript:alert(1)", "format": "mobile_feed"}'))
    page.goto(f"/ad/{ad_id}")
    expect(page.locator("#see-ad")).to_contain_text("Preview unavailable")
    assert page.locator("#see-ad iframe").count() == 0
    # HTTP failure
    page.unroute("**/api/ads/*/preview*")
    page.route("**/api/ads/*/preview*", lambda route: route.fulfill(status=502, content_type="application/json", body='{"error": "bad gateway"}'))
    page.goto(f"/ad/{ad_id}")
    expect(page.locator("#see-ad")).to_contain_text("Preview unavailable")
    shot(page, "41_preview_unavailable")


@pytest.mark.allow_errors
def test_eye_icon_and_lightbox_open_the_preview_modal(page):
    page.goto(f"/p/{SLUG}?view=ads")
    wait_rows(page, "ads-table")
    reqs = []
    page.on("request", lambda r: reqs.append(r.url) if "/preview" in r.url and "/api/" in r.url else None)
    eye = page.locator("[data-testid=ads-table] tbody button.eye").first
    expect(eye).to_be_visible()
    assert eye.get_attribute("title") == "See live ad"
    eye.click()
    modal = page.locator(".preview-modal[role=dialog]")
    expect(modal).to_be_visible()
    page.wait_for_function("() => !!document.querySelector('.preview-modal .preview-msg') || !!document.querySelector('.preview-modal iframe')")
    assert any("/api/meta-ads/" in u and u.split("?")[0].endswith("/preview") for u in reqs), reqs  # the per-Meta-ad endpoint
    page.keyboard.press("Escape")
    expect(modal).to_have_count(0)
    # lightbox -> See live ad: the preview stacks above it; Esc closes the top one first
    page.locator("[data-testid=ads-table] tbody img.thumb.zoomable").first.click()
    expect(page.locator(LB)).to_be_visible()
    page.locator("#lb-live").click()
    expect(modal).to_be_visible()
    assert page.locator(LB).count() == 1
    page.keyboard.press("Escape")
    expect(modal).to_have_count(0)
    expect(page.locator(LB)).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.locator(LB)).to_have_count(0)
    # the eye on a tree ad row
    drill_to_families(page)
    first_row(page).locator("button.caret").click()
    page.wait_for_selector(f"{TABLE} tr.lv4", timeout=8000)
    page.locator(f"{TABLE} tr.lv4 button.eye").first.click()
    expect(modal).to_be_visible()
    page.keyboard.press("Escape")


def test_facebook_post_and_ads_manager_links_are_present_and_safe(page):
    page.goto(f"/p/{SLUG}?view=ads")
    wait_rows(page, "ads-table")
    fb = page.locator("[data-testid=ads-table] tbody a[data-link=facebook-post]")
    am = page.locator("[data-testid=ads-table] tbody a[data-link=ads-manager]")
    if not fb.count():
        pytest.skip("API returns no links yet")
    assert fb.count() >= 1 and am.count() >= 1
    for loc in (fb, am):
        for i in range(min(loc.count(), 5)):
            a = loc.nth(i)
            assert a.get_attribute("target") == "_blank"
            rel = a.get_attribute("rel")
            assert "noopener" in rel and "noreferrer" in rel
            assert re.match(r"https?://", a.get_attribute("href"))
    assert fb.first.inner_text().startswith("Facebook post") and am.first.inner_text().startswith("Ads Manager")
    # ad detail page
    drill_to_families(page)
    first_row(page).locator("a.row-open").click()
    expect(page.locator("#ad-title")).to_be_visible()
    for sel in ("a[data-link=facebook-post]", "a[data-link=ads-manager]"):
        a = page.locator(f".pagehead {sel}").first
        if a.count():
            assert a.get_attribute("target") == "_blank" and "noopener" in a.get_attribute("rel") and "noreferrer" in a.get_attribute("rel")
    assert page.locator("a[href^='javascript:']").count() == 0


@pytest.mark.mock_only
def test_unsafe_link_urls_are_not_rendered(page):
    api = page.context.request
    rows = api.get(f"/api/products/{SLUG}/families").json()["rows"]
    x = next(r for r in rows if r["display"].startswith("XSS"))
    page.goto(f"/ad/{x['primary_id']}")
    expect(page.locator("#ad-title")).to_be_visible()
    assert page.locator("a[href^='javascript:']").count() == 0
    assert page.locator(".pagehead a[data-link=facebook-post]").count() == 0  # the javascript: story_url was dropped
    assert page.locator(".pagehead a[data-link=ads-manager]").count() == 1
    page.goto(f"/p/{SLUG}?view=ads&family_key={x['family_key']}")
    wait_rows(page, "ads-table")
    assert page.locator("a[href^='javascript:']").count() == 0


def test_ad_detail_sections(page):
    open_first_ad(page, "window=7d")
    shot(page, "15_ad_detail")
    expect(page.locator("#family-metrics")).to_be_visible()
    assert page.locator("#family-metrics tbody tr[data-row]").count() >= 4
    expect(page.locator("#tag-block")).to_be_visible()
    expect(page.locator("#copy-block")).to_be_visible()
    expect(page.locator("#media")).to_be_visible()
    img_ok = page.evaluate("() => { const i = document.querySelector('#media img'); return i ? i.complete && i.naturalWidth > 0 : null; }")
    assert img_ok in (True, None)
    if page.locator("#tag-block .badge.method").count():
        assert page.locator("#tag-block .badge[class*=conf-]").count() == 1
        expect(page.locator("#reliability-note")).to_contain_text("re-tag")
        expect(page.locator("#tag-block")).to_contain_text("What was seen")
        expect(page.locator("#line-block")).to_be_visible()
        expect(page.locator("#line-block table.ingredients tbody tr").first).to_be_visible()
    if page.locator("#meta-ads").count():
        expect(page.locator("#meta-ads")).to_be_visible()
        expect(page.locator("#meta-ads th[data-col=spend_share]")).to_contain_text("% of creative")
    # trend panel on the ad page: full series since daily_start, the selected window shaded (window A)
    expect(page.locator("#trend-panel")).to_be_visible()
    info = trend_info(page)
    assert info["n"] == 2 and len(info["labels"]) >= 100 and [d["type"] for d in info["ds"]] == ["bar", "line"]  # bars (Spend) + line (ROAS)
    sh = info["shades"]
    assert len(sh) == 1 and sh[0]["i1"] == len(info["labels"]) - 1 and sh[0]["i1"] - sh[0]["i0"] == 6, sh
    if IS_MOCK:
        assert len(info["labels"]) == 181 and info["labels"][0] == "2026-05-06" and info["labels"][-1] == "2026-11-02"
    assert info["axes"]["left"]["display"] and info["axes"]["right"]["display"]
    expect(page.locator("#family-metrics")).to_contain_text("days")


@pytest.mark.mock_only
def test_ad_detail_mock_content(page):
    api = page.context.request
    fams = api.get(f"/api/products/{SLUG}/families").json()["rows"]
    two = next(r for r in fams if r["n_rows"] > 1 and r["counted_here"])
    page.goto(f"/ad/{two['primary_id']}?window=30d")
    expect(page.locator("#ad-title")).to_have_text("Creative: " + two["display"])
    expect(page.locator("#siblings")).to_be_visible()
    assert page.locator("#siblings tbody tr").count() == two["n_rows"] - 1
    expect(page.locator("#tag-block .badge[class*=conf-]")).to_have_count(1)
    expect(page.locator("#campaigns")).to_be_visible()
    expect(page.locator("#meta-ads table")).to_be_visible()
    bl = api.get("/api/products/beef-liver/families").json()["rows"][0]
    page.goto(f"/ad/{bl['primary_id']}")
    expect(page.locator("#tag-block")).to_contain_text("Not line-mapped")
    assert page.locator("#line-block").count() == 0
    vid = next(r for r in fams if r["ad_type"] == "VID")
    page.goto(f"/ad/{vid['primary_id']}")
    expect(page.locator("#transcript")).to_be_visible()
    assert page.locator("#media .frames img").count() == 4


@pytest.mark.mock_only
def test_flags_copy_only_and_thumbnail_only(page):
    api = page.context.request
    seen = set()
    for r in api.get(f"/api/products/{SLUG}/families").json()["rows"]:
        det = api.get(f"/api/ads/{r['primary_id']}").json()
        t = det["tag"]
        if not t:
            continue
        m = t["method"]
        if ("text only" in m and "copy only" not in seen) or ("no audio" in m and "thumbnail only" not in seen):
            page.goto(f"/ad/{r['primary_id']}")
            expect(page.locator("#tag-block")).to_be_visible()
            if "text only" in m:
                expect(page.locator("#tag-block .badge.flag-copy")).to_have_text("copy only")
                expect(page.locator("#reliability-note")).to_contain_text("copy only")
                seen.add("copy only")
            else:
                expect(page.locator("#tag-block .badge.flag-thumb")).to_have_text("thumbnail only")
                seen.add("thumbnail only")
        if len(seen) == 2:
            break
    assert seen == {"copy only", "thumbnail only"}


@pytest.mark.mock_only
def test_untrusted_strings_are_escaped(page):
    api = page.context.request
    rows = api.get(f"/api/products/{SLUG}/families").json()["rows"]
    x = next(r for r in rows if r["display"].startswith("XSS"))
    page.goto(f"/p/{SLUG}?all=1")
    wait_rows(page)
    assert "XSS-<b>test</b>-family" in page.locator(TABLE).inner_text()
    assert page.locator(f"{TABLE} b").count() == 0
    page.goto(f"/ad/{x['primary_id']}")
    expect(page.locator("#ad-title")).to_have_text("Creative: XSS-<b>test</b>-family")
    expect(page.locator("#copy-block")).to_contain_text("<img src=x onerror=window.__xss=1>")
    assert page.evaluate("() => window.__xss") is None
    assert page.locator("a[href^='javascript:']").count() == 0
    assert page.locator("#copy-block img, #copy-block b, #copy-block script").count() == 0
    drill = api.get(f"/api/products/{SLUG}/tree?root=cluster&depth=3").json()["rows"]  # and in the dropdown tree
    row = next(r for r in drill if r["level"] == 3 and r["label"].startswith("XSS"))
    page.goto(purl(row["cluster"], row["icp"], row["pain"]))
    wait_rows(page)
    assert page.locator(f"{TABLE} b").count() == 0
    assert page.evaluate("() => window.__xss") is None


@pytest.mark.mock_only
def test_remainder_row_in_ads_table(page):
    api = page.context.request
    ads = api.get(f"/api/products/{SLUG}/ads").json()
    rem = [r for r in ads["rows"] if r["ad_id"] is None]
    assert rem, "mock should include a remainder row"
    fam = rem[0]["family_key"]
    page.goto(f"/p/{SLUG}?view=ads&family_key={fam}")
    wait_rows(page, "ads-table")
    assert "(days without ad-level detail)" in page.locator("[data-testid=ads-table]").inner_text()
    assert page.locator("[data-testid=ads-table] tbody tr.clickable").count() == len(table_data(page, "ads-table")["rows"]) - 1


@pytest.mark.mock_only
def test_ad_page_hook_card(page):
    rows, _ = hook_families(page)
    by_src = {}
    for r in rows:
        if r["hook"] and not r["display"].startswith("XSS"):
            by_src.setdefault(r["hook_source"], r)
    assert set(by_src) == {"spoken", "on_screen", "both", "copy"}, by_src.keys()
    api = page.context.request
    both = by_src["both"]
    det = api.get(f"/api/ads/{both['primary_id']}").json()["hook"]
    page.goto(f"/ad/{both['primary_id']}")
    page.wait_for_selector("#family-metrics")
    idle(page)
    card = page.locator("#hook-block")
    expect(card).to_be_visible()
    expect(page.locator("#hook-line")).to_have_text(det["hook"])
    kv = lambda label: card.locator(".kv", has_text=label).locator(".v")         # noqa: E731
    expect(kv("Spoken")).to_have_text(det["spoken"])
    expect(kv("On screen")).to_have_text(det["on_screen"])
    expect(card.locator(".badge.method")).to_have_text("both")
    expect(card.locator(".badge.conf-high")).to_have_text("high")
    expect(card).to_contain_text("Hooks hooks-v1-2026-11-05")
    # prominent, and between the title and the creative preview
    pos = page.evaluate("""() => { const y = (s) => document.querySelector(s).getBoundingClientRect(); return {title: y('#ad-title').bottom, hook: y('#hook-block').top, line: parseFloat(getComputedStyle(document.querySelector('#hook-line')).fontSize), seeAd: y('#see-ad').top}; }""")
    assert pos["title"] <= pos["hook"] < pos["seeAd"] and pos["line"] >= 15, pos
    # a hook with no spoken line / on-screen text shows only what it has, with its source and confidence
    copy = by_src["copy"]
    page.goto(f"/ad/{copy['primary_id']}")
    page.wait_for_selector("#hook-block")
    expect(page.locator("#hook-line")).to_have_text(copy["hook"])
    assert page.locator("#hook-block .kv", has_text="Spoken").count() == 0 and page.locator("#hook-block .kv", has_text="On screen").count() == 0
    expect(page.locator("#hook-block .badge.method")).to_have_text("copy")
    expect(page.locator("#hook-block .badge.conf-low")).to_have_text("low")
    expect(page.locator("#hook-block .kv", has_text="Note").locator(".v")).to_contain_text("hook taken from the primary text")
    page.goto(f"/ad/{by_src['on_screen']['primary_id']}")
    page.wait_for_selector("#hook-block")
    expect(page.locator("#hook-block .badge.method")).to_have_text("on screen")  # the underscore is a space
    # no hook, no card
    nohook = next(r for r in rows if not r["hook"])
    page.goto(f"/ad/{nohook['primary_id']}")
    page.wait_for_selector("#family-metrics")
    idle(page)
    assert page.locator("#hook-block").count() == 0 and page.locator("#tag-block").count() == 1


@pytest.mark.mock_only
def test_hooks_are_escaped(page):
    rows, _ = hook_families(page)
    x = next(r for r in rows if r["display"].startswith("XSS"))
    assert "<img src=x onerror=window.__xss=1>" in x["hook"]
    page.goto(f"/p/{SLUG}?all=1")
    wait_rows(page)
    assert "<img src=x onerror=window.__xss=1>" in page.locator(f"{TABLE} .hooktext").first.locator("xpath=ancestor::table").inner_text()
    assert page.locator(f"{TABLE} .hooktext img, {TABLE} .hooktext b, {TABLE} .hooktext script").count() == 0
    page.goto(f"/ad/{x['primary_id']}")
    expect(page.locator("#hook-line")).to_have_text(x["hook"])
    assert page.locator("#hook-block img, #hook-block b, #hook-block script").count() == 0
    assert page.evaluate("() => window.__xss") is None
