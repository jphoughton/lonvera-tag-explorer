"""Production page (/priorities/production) and the vendor page (/priorities/vendor/<token>) against the mock (tests/ui/mock_priorities_assign.py): one plain table of assignments, links edited in
place, stage derived from the links, Ready to launch only with a creative link, filters, vendor links, and a vendor page that shows only the vendor's own rows."""
import pytest

pytestmark = pytest.mark.mock_only

MADRE = "tok-madre-0000000000000000"
VICTORIA = "tok-victoria-00000000000000"


@pytest.fixture(autouse=True)
def fresh(ctx, base_url):
    ctx.request.post(base_url + "/__test/assign/reset")


def idle(page):
    page.wait_for_function("() => !document.querySelector('#view').hasAttribute('aria-busy')")


def goto(page, qs=""):
    page.goto("/priorities/production" + qs)
    page.wait_for_selector("[data-testid=prod-page]")
    idle(page)


def row(page, rid):
    return page.locator(f"[data-testid=prod-row][data-id='{rid}']")


def stage(page, rid):
    return row(page, rid).locator("[data-testid=prod-stage]").text_content().strip()


def fill_dialog(page, value, field="url"):
    page.fill(f"[data-testid=prod-dialog] [data-testid=prod-field-{field}]", value)
    page.click("[data-testid=prod-dialog-ok]")
    page.wait_for_selector("[data-testid=prod-dialog]", state="detached")


def test_table_renders_with_plain_columns_and_open_rows_only(page):
    goto(page)
    heads = [t.strip() for t in page.locator("[data-testid=prod-table] thead th").all_text_contents()]
    assert heads == ["Week of", "Vendor", "Product", "Concept", "Brief", "Creative", "Stage", "Ads", "Result", ""]
    ids = page.locator("[data-testid=prod-row]").evaluate_all("rs => rs.map(r => r.dataset.id)")
    assert ids == ["3", "2", "1"]                                    # newest week first; the launched row 4 is hidden until Show launched and dropped
    assert stage(page, 1) == "Assigned" and stage(page, 2) == "Creative delivered"
    assert "Hair Loss" in row(page, 2).locator("[data-testid=prod-concept]").text_content()
    assert page.locator("h1").text_content() == "Production"
    assert "—" not in page.locator("[data-testid=prod-page]").text_content()          # no em dashes in UI text


def test_adding_a_brief_link_moves_the_stage_to_brief_sent(page):
    goto(page)
    assert stage(page, 1) == "Assigned"
    row(page, 1).locator("[data-testid=prod-edit-brief_url]").click()
    fill_dialog(page, "https://docs.google.com/document/d/abc")
    page.wait_for_function("() => document.querySelector(\"[data-testid=prod-row][data-id='1'] [data-testid=prod-stage]\").textContent.trim() === 'Brief sent'")
    a = row(page, 1).locator("[data-testid=prod-brief_url]")
    assert a.get_attribute("href") == "https://docs.google.com/document/d/abc" and a.get_attribute("target") == "_blank" and "noopener" in a.get_attribute("rel")


@pytest.mark.allow_errors
def test_a_bad_link_is_refused_with_a_message(page):
    goto(page)
    row(page, 1).locator("[data-testid=prod-edit-brief_url]").click()
    fill_dialog(page, "javascript:alert(1)")
    page.wait_for_function("() => document.querySelector('[data-testid=prod-msg]').textContent.length > 0")
    assert "http" in page.locator("[data-testid=prod-msg]").text_content()
    assert stage(page, 1) == "Assigned"


def test_ready_button_only_with_a_creative_link_and_then_stage_is_ready(page):
    goto(page)
    assert row(page, 1).locator("[data-testid=prod-ready]").count() == 0          # no creative link
    assert row(page, 2).locator("[data-testid=prod-ready]").count() == 1
    row(page, 2).locator("[data-testid=prod-ready]").click()
    page.wait_for_function("() => document.querySelector(\"[data-testid=prod-row][data-id='2'] [data-testid=prod-stage]\").textContent.trim() === 'Ready to launch'")
    assert row(page, 2).locator("[data-testid=prod-ready]").count() == 0


def test_concept_id_links_ads_and_shows_the_result(page):
    goto(page, "?closed=1")
    assert page.locator("[data-testid=prod-closed]").is_checked()
    assert stage(page, 4) == "Launched"
    res = row(page, 4).locator("[data-testid=prod-result]").text_content()
    assert res.startswith("Launched ") and "$1,240 spent" in res and "1.80 return" in res
    assert row(page, 4).locator("[data-testid=prod-cid]").text_content() == "Lonvera-VitalBull-Static-5.4"
    # add a Concept ID by hand to row 1, and use the suggestion on row 3
    row(page, 1).locator("[data-testid=prod-add-cid]").click()
    fill_dialog(page, "Lonvera-VitalBull-Static-5.4", "cid")
    page.wait_for_function("() => document.querySelector(\"[data-testid=prod-row][data-id='1'] [data-testid=prod-stage]\").textContent.trim() === 'Launched'")
    row(page, 3).locator("[data-testid=prod-suggest]").click()
    page.wait_for_function("() => document.querySelector(\"[data-testid=prod-row][data-id='3'] [data-testid=prod-cid]\")")
    assert row(page, 3).locator("[data-testid=prod-suggest]").count() == 0


def test_filters_by_week_vendor_and_closed(page):
    goto(page)
    page.select_option("[data-testid=prod-vendor]", "Victoria")
    page.wait_for_function("() => document.querySelectorAll('[data-testid=prod-row]').length === 1")
    assert page.locator("[data-testid=prod-row]").get_attribute("data-id") == "3"
    page.select_option("[data-testid=prod-vendor]", "")
    page.wait_for_function("() => document.querySelectorAll('[data-testid=prod-row]').length === 3")
    weeks = page.locator("[data-testid=prod-week] option").all_text_contents()
    assert weeks[0] == "All weeks" and len(weeks) == 4
    page.select_option("[data-testid=prod-week]", index=2)
    page.wait_for_function("() => document.querySelectorAll('[data-testid=prod-row]').length === 2")
    page.select_option("[data-testid=prod-week]", "")
    page.check("[data-testid=prod-closed]")
    page.wait_for_function("() => document.querySelectorAll('[data-testid=prod-row]').length === 4")
    assert "closed=1" in page.url


def test_change_vendor_and_drop_with_confirm(page):
    goto(page)
    row(page, 1).locator("[data-testid=prod-change]").click()
    page.select_option("[data-testid=prod-dialog] [data-testid=prod-field-vendor]", "Victoria")
    page.click("[data-testid=prod-dialog-ok]")
    page.wait_for_function("() => document.querySelector(\"[data-testid=prod-row][data-id='1'] [data-testid=prod-vendor]\").textContent === 'Victoria'")
    row(page, 1).locator("[data-testid=prod-drop]").click()
    page.click("[data-testid=prod-dialog-cancel]")                                 # cancel keeps it
    assert row(page, 1).count() == 1
    row(page, 1).locator("[data-testid=prod-drop]").click()
    page.click("[data-testid=prod-dialog-ok]")
    page.wait_for_function("() => !document.querySelector(\"[data-testid=prod-row][data-id='1']\")")


def test_vendor_links_copy_and_reset_with_confirm(page, ctx):
    ctx.grant_permissions(["clipboard-read", "clipboard-write"])
    goto(page)
    assert page.locator("[data-testid=prod-copy-link]").count() == 9
    page.locator("[data-vendor=Madre] [data-testid=prod-copy-link]").click()
    page.wait_for_function("() => document.querySelector('[data-testid=prod-vendor-url]')")
    assert page.locator("[data-testid=prod-vendor-url]").text_content().endswith("/priorities/vendor/" + MADRE)
    page.locator("[data-vendor=Madre] [data-testid=prod-reset-link]").click()
    page.click("[data-testid=prod-dialog-cancel]")                                 # cancel: link unchanged
    page.locator("[data-vendor=Madre] [data-testid=prod-copy-link]").click()
    page.wait_for_function(f"() => document.querySelector('[data-testid=prod-vendor-url]').textContent.endsWith('{MADRE}')")
    page.locator("[data-vendor=Madre] [data-testid=prod-reset-link]").click()
    page.click("[data-testid=prod-dialog-ok]")
    page.wait_for_function(f"() => !document.querySelector('[data-testid=prod-vendor-url]').textContent.endsWith('{MADRE}')")


def test_vendor_page_shows_only_its_rows_saves_a_creative_link_and_has_no_app_navigation(anon_page, base_url):
    page = anon_page
    page.goto(f"/priorities/vendor/{MADRE}")
    page.wait_for_selector("[data-testid=prod-vendor-page]")
    idle(page)
    assert page.locator("h1").text_content() == "Madre: concepts to make"
    assert not page.locator("#topbar").is_visible()
    cards = page.locator("[data-testid=prod-card]")
    assert cards.count() == 3 and cards.evaluate_all("cs => cs.map(c => c.dataset.id)") == ["2", "1", "4"]    # Madre's open rows plus the one launched 2 weeks ago; not Victoria's row 3
    text = page.locator("[data-testid=prod-vendor-page]").text_content()
    assert "Hair Thinning" in text and "Joint Pain" not in text and "Boneflex" not in text
    card = page.locator("[data-testid=prod-card][data-id='1']")
    assert card.locator("[data-testid=prod-vready]").count() == 0                   # no creative link yet
    card.locator("[data-testid=prod-in-creative]").fill("https://drive.google.com/drive/folders/new1")
    card.locator("[data-testid=prod-in-note]").fill("Two cuts attached")
    card.locator("[data-testid=prod-save]").click()
    page.wait_for_function("() => document.querySelector(\"[data-id='1'] [data-testid=prod-saved]\").textContent === 'Saved'")
    assert page.locator("[data-testid=prod-card][data-id='1'] [data-testid=prod-stage]").text_content() == "Creative delivered"
    assert page.locator("[data-testid=prod-card][data-id='1'] [data-testid=prod-vready]").count() == 1
    page.locator("[data-testid=prod-card][data-id='1'] [data-testid=prod-vready]").click()
    page.wait_for_function("() => document.querySelector(\"[data-id='1'] [data-testid=prod-stage]\").textContent === 'Ready to launch'")
    page.locator("[data-testid=prod-card][data-id='1'] [data-testid=prod-vunready]").click()
    page.wait_for_function("() => document.querySelector(\"[data-id='1'] [data-testid=prod-stage]\").textContent === 'Creative delivered'")


def test_vendor_page_shows_spend_and_return_for_its_own_launched_rows_only(anon_page):
    page = anon_page
    page.goto(f"/priorities/vendor/{MADRE}")
    page.wait_for_selector("[data-testid=prod-vendor-page]")
    idle(page)
    done = page.locator("[data-testid=prod-card][data-id='4']")
    assert "$1,240 spent" in done.locator("[data-testid=prod-result]").text_content()
    page.goto(f"/priorities/vendor/{VICTORIA}")
    page.wait_for_selector("[data-testid=prod-vendor-page]")
    idle(page)
    assert page.locator("[data-testid=prod-card]").count() == 1 and "$1,240" not in page.locator("body").text_content()
    assert "Note:" not in page.locator("body").text_content()                     # the internal note is never sent


@pytest.mark.allow_errors
def test_bad_vendor_token_says_the_link_is_not_valid(anon_page):
    page = anon_page
    page.goto("/priorities/vendor/not-a-real-token")
    page.wait_for_selector("[data-testid=prod-empty]")
    assert "not valid" in page.locator("[data-testid=prod-empty]").text_content()


@pytest.mark.parametrize("path", ["/priorities/production", f"/priorities/vendor/{MADRE}"])
def test_no_sideways_page_scroll_at_375px(browser, base_url, path):
    c = browser.new_context(viewport={"width": 375, "height": 812}, base_url=base_url)
    try:
        c.request.post(base_url + "/api/login", data={"username": "JoshHoughton", "password": "test"})
        pg = c.new_page()
        pg.goto(path)
        pg.wait_for_selector("[data-testid=prod-page], [data-testid=prod-vendor-page]")
        idle(pg)
        assert pg.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth + 1")
        assert pg.evaluate("() => document.body.scrollWidth <= window.innerWidth + 1")
        if "vendor" not in path:
            assert pg.locator("[data-testid=prod-scroll]").evaluate("e => e.scrollWidth > e.clientWidth")      # the table scrolls inside its own box
    finally:
        c.close()


def test_text_is_at_least_12px(page):
    goto(page)
    small = page.evaluate("""() => [...document.querySelectorAll('[data-testid=prod-page] *')].filter(e => e.childNodes.length && [...e.childNodes].some(n => n.nodeType === 3 && n.textContent.trim()) && parseFloat(getComputedStyle(e).fontSize) < 12).map(e => e.tagName + ':' + e.textContent.slice(0, 20))""")
    assert small == []
