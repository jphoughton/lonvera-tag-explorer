"""Launched ads on a ticket (the live Meta ad of each variation, and the concept's own list) in the card drawer, against the mock (tests/ui/mock_priorities_assign.py).
Internal only: the vendor page never carries them."""
import json

import pytest

pytestmark = pytest.mark.mock_only

KNOWN = "123456789012"
VARS = [{"label": "Original", "url": "", "concept_id": ""}, {"label": "Hook A", "url": "", "concept_id": ""}, {"label": "Hook B", "url": "", "concept_id": ""}]


@pytest.fixture(autouse=True)
def fresh(ctx, base_url):
    ctx.request.post(base_url + "/__test/assign/reset")
    ctx.request.post(base_url + "/__test/board/seed")


def patch(page, aid, body):
    return page.evaluate("async ([a, b]) => { const r = await fetch('/api/priorities/assignments/' + a, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(b) }); return [r.status, await r.json()]; }", [aid, body])


def open_card(page, aid=1, variations=True):
    page.goto("/priorities/production")
    page.wait_for_selector("[data-testid=prod-page]")
    if variations:
        assert patch(page, aid, {"variations": VARS})[0] == 200
    page.goto(f"/priorities/production?card={aid}")
    page.wait_for_selector("[data-testid=prod-drawer-details]")


def get_row(page, aid=1):
    rows = page.evaluate("async () => (await (await fetch('/api/priorities/assignments?include_closed=1')).json()).rows")
    return next(r for r in rows if r["id"] == aid)


def test_the_drawer_shows_an_input_per_variation_and_a_zero_summary(page):
    open_card(page)
    assert page.locator("[data-testid=prod-live-input]").count() == 3
    assert page.locator("[data-testid=prod-drawer-launched]").inner_text() == "Launched 0 of 3 ads"
    assert page.locator("[data-testid=prod-ticket-live-none]").is_visible()
    assert page.locator("[data-testid=prod-live-input]").first.get_attribute("placeholder") == "Paste Meta ad ID or Ads Manager link"


def test_saving_an_ad_id_on_blur_shows_the_match_and_updates_the_summary(page):
    open_card(page)
    inp = page.locator("[data-testid=prod-live-input]").first
    inp.fill(f"https://adsmanager.facebook.com/adsmanager/manage/ads?act=1&selected_ad_ids={KNOWN}")
    inp.blur()
    page.wait_for_function("() => document.querySelector('[data-testid=prod-live-info]')")
    assert "Lonvera-VitalBull-Static-5.4 - Copy 2" in page.locator("[data-testid=prod-live-info]").inner_text()
    assert "$1,240" in page.locator("[data-testid=prod-live-info]").inner_text() and "Meta-reported" in page.locator("[data-testid=prod-live-info]").inner_text()
    assert page.locator("[data-testid=prod-live-input]").first.input_value() == KNOWN
    assert page.locator("[data-testid=prod-drawer-launched]").inner_text() == "Launched 1 of 3 ads"
    row = get_row(page)
    assert row["variations"][0]["ad_id"] == KNOWN and [v["label"] for v in row["variations"]] == ["Original", "Hook A", "Hook B"]


def test_an_unknown_ad_says_not_found_and_clear_removes_it_and_enter_saves(page):
    open_card(page)
    inp = page.locator("[data-testid=prod-live-input]").nth(1)
    inp.fill("99999999999999")
    inp.press("Enter")
    page.wait_for_selector("[data-testid=prod-live-info]")
    assert page.locator("[data-testid=prod-live-info]").inner_text() == "Not found in Meta data yet"
    assert page.locator("[data-testid=prod-drawer-launched]").inner_text() == "Launched 1 of 3 ads"
    page.locator("[data-testid=prod-live-clear]:visible").click()
    page.wait_for_function("() => !document.querySelector('[data-testid=prod-live-info]')")
    assert page.locator("[data-testid=prod-drawer-launched]").inner_text() == "Launched 0 of 3 ads"
    assert all("ad_id" not in v for v in get_row(page)["variations"])


@pytest.mark.allow_errors
def test_a_post_link_is_refused_with_a_plain_message(page):
    open_card(page)
    inp = page.locator("[data-testid=prod-live-input]").first
    inp.fill("https://www.facebook.com/lonvera/posts/123456789")
    inp.blur()
    page.wait_for_function("() => /no ad ID/.test(document.querySelector('[data-testid=prod-drawer-msg]').textContent)")
    assert "ad_id" not in get_row(page)["variations"][0]


def test_the_concept_box_adds_and_removes_ticket_level_ads_even_without_variations(page):
    open_card(page, 2, variations=False)
    assert page.locator("[data-testid=prod-live-input]").count() == 0
    box = page.locator("[data-testid=prod-ticket-live-input]")
    box.fill(KNOWN)
    page.locator("[data-testid=prod-ticket-live-add]").click()
    page.wait_for_selector("[data-testid=prod-ticket-live-row]")
    assert KNOWN in page.locator("[data-testid=prod-ticket-live-row]").inner_text()
    assert page.locator("[data-testid=prod-drawer-launched]").inner_text() == "Launched 1 ad"
    page.locator("[data-testid=prod-ticket-live-remove]").click()
    page.wait_for_selector("[data-testid=prod-ticket-live-none]")
    assert get_row(page, 2)["launched_ad_ids"] == []


def test_the_board_card_and_list_row_show_the_compact_count(page):
    page.goto("/priorities/production")
    page.wait_for_selector("[data-testid=prod-page]")
    assert patch(page, 1, {"variations": [{**v, **({"ad_id": KNOWN} if i == 0 else {})} for i, v in enumerate(VARS)]})[0] == 200
    page.goto("/priorities/production")
    page.wait_for_selector("[data-testid=prod-board]")
    assert page.locator("[data-testid=prod-board-card][data-id='1'] [data-testid=prod-card-launched]").inner_text() == "1/3 launched"
    page.goto("/priorities/production?view=list")
    page.wait_for_selector("[data-testid=prod-table]")
    assert page.locator("[data-testid=prod-row][data-id='1'] [data-testid=prod-launched-count]").inner_text() == "1/3 launched"


def test_a_vendor_page_never_carries_launched_ads(page):
    page.goto("/priorities/production")
    page.wait_for_selector("[data-testid=prod-page]")
    res = patch(page, 1, {"variations": [{**v, "ad_id": KNOWN} for v in VARS], "launched_ad_ids": ["555555555"]})
    assert res[0] == 200, res
    d = page.evaluate("async () => (await fetch('/api/priorities/vendor/tok-madre-0000000000000000')).json()")
    row = next(r for r in d["rows"] if r["id"] == 1)
    assert len(row["variations"]) == 3
    assert not any("ad_id" in v for v in row["variations"])
    assert not {"launched_ad_ids", "launched_ads", "ads_total", "ads_linked"} & set(row)
    page.goto("/priorities/vendor/tok-madre-0000000000000000")
    page.wait_for_selector("[data-testid=prod-vendor-page]")
    body = page.inner_text("body")
    assert KNOWN not in body and "Launched ad" not in body
    assert page.locator("[data-testid=prod-live-input], [data-testid=prod-drawer-launched]").count() == 0


def test_phone_layout_has_no_sideways_scroll_and_no_null_text(browser, base_url):
    c = browser.new_context(viewport={"width": 375, "height": 812}, base_url=base_url)
    try:
        c.request.post(base_url + "/__test/assign/reset")
        c.request.post(base_url + "/__test/board/seed")
        c.request.post(base_url + "/api/login", data={"username": "JoshHoughton", "password": "test"})
        pg = c.new_page()
        errs = []
        pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto("/priorities/production")
        pg.wait_for_selector("[data-testid=prod-page]")
        assert patch(pg, 1, {"variations": VARS, "launched_ad_ids": [KNOWN]})[0] == 200
        pg.goto("/priorities/production?card=1")
        pg.wait_for_selector("[data-testid=prod-live]")
        pg.locator("[data-testid=prod-live-input]").first.fill(KNOWN)
        pg.locator("[data-testid=prod-live-input]").first.blur()
        pg.wait_for_selector("[data-testid=prod-live-info]")
        assert pg.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth + 1 && document.body.scrollWidth <= window.innerWidth + 1")
        assert pg.evaluate("() => { const d = document.querySelector('[data-testid=prod-drawer]'); return d.scrollWidth <= d.clientWidth + 1; }")
        text = pg.inner_text("[data-testid=prod-drawer]")
        assert "undefined" not in text and "null" not in text and "NaN" not in text
        assert not errs, errs
    finally:
        c.close()
