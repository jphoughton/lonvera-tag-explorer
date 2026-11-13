"""Production page (/priorities/production) and the vendor page (/priorities/vendor/<token>) against the mock (tests/ui/mock_priorities_assign.py): one plain table of assignments, links edited in
place, stage derived from the links, Ready to launch only with a creative link, filters, vendor links, and a vendor page that shows only the vendor's own rows."""
import pytest

pytestmark = pytest.mark.mock_only

MADRE = "tok-madre-0000000000000000"
VICTORIA = "tok-victoria-00000000000000"


@pytest.fixture(autouse=True)
def fresh(ctx, base_url):
    ctx.request.post(base_url + "/__test/assign/reset")


def choose(page, tid, *values):
    """Tick exactly these options in a multi-select filter (none = All). Each tick applies at once and the list stays open, so every click re-finds its box; Escape closes it."""
    d = page.locator(f".pagefilters [data-testid={tid}]")
    if d.get_attribute("open") is None:
        d.locator("summary").click()
    opts = [cb.get_attribute("value") for cb in d.locator("input[type=checkbox]").all()]
    for v in opts:
        box = page.locator(f".pagefilters [data-testid={tid}] input[type=checkbox][value='{v}']")
        want = v in values
        if box.is_checked() != want:
            box.set_checked(want)
            page.wait_for_timeout(150)
    page.keyboard.press("Escape")


def idle(page):
    page.wait_for_function("() => !document.querySelector('#view').hasAttribute('aria-busy')")


def goto(page, qs=""):
    page.goto("/priorities/production" + (qs + "&view=list" if qs else "?view=list"))
    page.wait_for_selector("[data-testid=prod-table], [data-testid=prod-empty]")
    idle(page)


def row(page, rid):
    return page.locator(f"[data-testid=prod-row][data-id='{rid}']")


def stage(page, rid):
    return row(page, rid).locator("[data-testid=prod-stage]").text_content().strip()


def more(page, rid):
    """Change vendor or week and Drop sit behind a per-row More disclosure."""
    d = row(page, rid).locator("[data-testid=prod-more]")
    if d.get_attribute("open") is None:
        d.locator("summary").click()


def fill_dialog(page, value, field="url"):
    page.fill(f"[data-testid=prod-dialog] [data-testid=prod-field-{field}]", value)
    page.click("[data-testid=prod-dialog-ok]")
    page.wait_for_selector("[data-testid=prod-dialog]", state="detached")


def test_table_renders_with_plain_columns_and_open_rows_only(page):
    goto(page)
    heads = [t.strip() for t in page.locator("[data-testid=prod-table] thead th").all_text_contents()]
    assert heads == ["Week of", "Vendor", "Product", "Concept", "Brief", "Creative", "Format", "Stage", "Due", "Priority", "Ready", "Ads", "Spend", "Return", ""]
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
    page.wait_for_function("() => document.querySelector(\"[data-testid=prod-row][data-id='1'] [data-testid=prod-stage]\")?.textContent.trim() === 'Brief sent'")
    a = row(page, 1).locator("[data-testid=prod-brief_url]")
    assert a.get_attribute("href") == "https://docs.google.com/document/d/abc" and a.get_attribute("target") == "_blank" and "noopener" in a.get_attribute("rel")


@pytest.mark.allow_errors
def test_a_bad_link_is_refused_with_a_message(page):
    goto(page)
    row(page, 1).locator("[data-testid=prod-edit-brief_url]").click()
    fill_dialog(page, "javascript:alert(1)")
    page.wait_for_function("() => document.querySelector('[data-testid=prod-msg]')?.textContent.length > 0")
    assert "http" in page.locator("[data-testid=prod-msg]").text_content()
    assert stage(page, 1) == "Assigned"


def test_ready_button_only_with_a_creative_link_and_then_stage_is_ready(page):
    goto(page)
    assert row(page, 1).locator("[data-testid=prod-ready]").count() == 0          # no creative link
    assert row(page, 2).locator("[data-testid=prod-ready]").count() == 1
    row(page, 2).locator("[data-testid=prod-ready]").click()
    page.wait_for_function("() => document.querySelector(\"[data-testid=prod-row][data-id='2'] [data-testid=prod-stage]\")?.textContent.trim() === 'Ready to launch'")
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
    page.wait_for_function("() => document.querySelector(\"[data-testid=prod-row][data-id='1'] [data-testid=prod-stage]\")?.textContent.trim() === 'Launched'")
    row(page, 3).locator("[data-testid=prod-suggest]").click()
    page.wait_for_function("() => document.querySelector(\"[data-testid=prod-row][data-id='3'] [data-testid=prod-cid]\")")
    assert row(page, 3).locator("[data-testid=prod-suggest]").count() == 0


def test_filters_by_week_vendor_and_closed(page):
    goto(page)
    choose(page, "prod-vendor", "Victoria")
    page.wait_for_function("() => document.querySelectorAll('[data-testid=prod-row]').length === 1")
    assert page.locator("[data-testid=prod-row]").get_attribute("data-id") == "3"
    choose(page, "prod-vendor")
    page.wait_for_function("() => document.querySelectorAll('[data-testid=prod-row]').length === 3")
    wk = [cb.get_attribute("value") for cb in page.locator("[data-testid=prod-week] input[type=checkbox]").all()]
    assert len(wk) == 3
    choose(page, "prod-week", wk[1])
    page.wait_for_function("() => document.querySelectorAll('[data-testid=prod-row]').length === 2")
    choose(page, "prod-week")
    page.check("[data-testid=prod-closed]")
    page.wait_for_function("() => document.querySelectorAll('[data-testid=prod-row]').length === 4")
    assert "closed=1" in page.url


def test_change_vendor_and_drop_with_confirm(page):
    goto(page)
    more(page, 1)
    row(page, 1).locator("[data-testid=prod-change]").click()
    page.select_option("[data-testid=prod-dialog] [data-testid=prod-field-vendor]", "Victoria")
    page.click("[data-testid=prod-dialog-ok]")
    page.wait_for_function("() => document.querySelector(\"[data-testid=prod-row][data-id='1'] [data-testid=prod-vendor]\")?.textContent === 'Victoria'")
    more(page, 1)
    row(page, 1).locator("[data-testid=prod-drop]").click()
    page.click("[data-testid=prod-dialog-cancel]")                                 # cancel keeps it
    assert row(page, 1).count() == 1
    more(page, 1)
    row(page, 1).locator("[data-testid=prod-drop]").click()
    page.click("[data-testid=prod-dialog-ok]")
    page.wait_for_function("() => !document.querySelector(\"[data-testid=prod-row][data-id='1']\")")


def test_vendor_links_copy_and_reset_with_confirm(page, ctx):
    ctx.grant_permissions(["clipboard-read", "clipboard-write"])
    goto(page)
    assert page.locator("[data-testid=prod-copy-link]").count() == 9
    assert not page.locator("[data-testid=prod-copy-link]").first.is_visible()          # collapsed until opened
    page.locator("[data-testid=prod-links] > summary").click()
    page.locator("[data-vendor=Madre] [data-testid=prod-copy-link]").click()
    page.wait_for_function("() => document.querySelector('[data-testid=prod-vendor-url]')")
    assert page.locator("[data-testid=prod-vendor-url]").text_content().endswith("/priorities/vendor/" + MADRE)
    page.locator("[data-vendor=Madre] [data-testid=prod-reset-link]").click()
    page.click("[data-testid=prod-dialog-cancel]")                                 # cancel: link unchanged
    page.locator("[data-vendor=Madre] [data-testid=prod-copy-link]").click()
    page.wait_for_function(f"() => document.querySelector('[data-testid=prod-vendor-url]')?.textContent.endsWith('{MADRE}')")
    page.locator("[data-vendor=Madre] [data-testid=prod-reset-link]").click()
    page.click("[data-testid=prod-dialog-ok]")
    page.wait_for_function(f"() => {{ const e = document.querySelector('[data-testid=prod-vendor-url]'); return !!e && !e.textContent.endsWith('{MADRE}'); }}")


def test_vendor_page_shows_only_its_rows_saves_a_creative_link_and_has_no_app_navigation(anon_page, base_url):
    page = anon_page
    page.goto(f"/priorities/vendor/{MADRE}")
    page.wait_for_selector("[data-testid=prod-vendor-page]")
    idle(page)
    assert page.locator("h1").text_content() == "Madre: concepts to make"
    assert not page.locator("#topbar").is_visible()
    cards = page.locator("[data-testid=prod-card]")
    assert cards.count() == 3 and cards.evaluate_all("cs => cs.map(c => c.dataset.id)") == ["1", "2", "4"]    # Madre's open rows plus the one launched 2 weeks ago; not Victoria's row 3
    text = page.locator("[data-testid=prod-vendor-page]").text_content()
    assert "Hair Thinning" in text and "Joint Pain" not in text and "Boneflex" not in text
    card = page.locator("[data-testid=prod-card][data-id='1']")
    assert card.locator("[data-testid=prod-vready]").count() == 0                   # no creative link yet
    card.locator("[data-testid=prod-in-creative]").fill("https://drive.google.com/drive/folders/new1")
    card.locator("[data-testid=prod-in-note]").fill("Two cuts attached")
    card.locator("[data-testid=prod-save]").click()
    page.wait_for_function("() => document.querySelector(\"[data-id='1'] [data-testid=prod-saved]\")?.textContent === 'Saved'")
    assert page.locator("[data-testid=prod-card][data-id='1'] [data-testid=prod-stage]").text_content() == "Creative delivered"
    assert page.locator("[data-testid=prod-card][data-id='1'] [data-testid=prod-vready]").count() == 1
    page.locator("[data-testid=prod-card][data-id='1'] [data-testid=prod-vready]").click()
    page.wait_for_function("() => document.querySelector(\"[data-id='1'] [data-testid=prod-stage]\")?.textContent === 'Ready to launch'")
    page.locator("[data-testid=prod-card][data-id='1'] [data-testid=prod-vunready]").click()
    page.wait_for_function("() => document.querySelector(\"[data-id='1'] [data-testid=prod-stage]\")?.textContent === 'Creative delivered'")


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


@pytest.mark.parametrize("path", ["/priorities/production?view=list", f"/priorities/vendor/{MADRE}"])
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


def fmt(page, rid):
    return row(page, rid).locator("[data-testid=prod-format]")


def test_format_column_shows_the_choice_and_internal_users_change_it_inline(page):
    goto(page)
    assert fmt(page, 2).input_value() == "F3" and fmt(page, 1).input_value() == ""
    opts = fmt(page, 1).locator("option").all_text_contents()
    assert opts[0] == "No format chosen" and opts[1] == "F1 Long animated story, openly AI, told by the character (90s to 3 min)" and len(opts) == 7
    fmt(page, 1).select_option("F5")
    page.wait_for_function("() => document.querySelector(\"[data-testid=prod-row][data-id='1'] [data-testid=prod-format]\")?.value === 'F5'")
    page.wait_for_function("() => document.querySelector('[data-testid=prod-msg]')?.textContent === 'Format saved.'")
    page.reload()
    page.wait_for_selector("[data-testid=prod-page]")
    idle(page)
    assert fmt(page, 1).input_value() == "F5"                                                  # it was saved, not just drawn
    fmt(page, 1).select_option("")
    page.wait_for_function("() => document.querySelector(\"[data-testid=prod-row][data-id='1'] [data-testid=prod-format]\")?.value === ''")
    page.wait_for_function("() => document.querySelector('[data-testid=prod-msg]')?.textContent === 'Format removed.'")
    assert stage(page, 1) == "Assigned"                                                         # the other cells are unchanged
    assert "—" not in page.locator("[data-testid=prod-page]").text_content()


def test_vendor_page_shows_the_format_read_only(anon_page, base_url):
    page = anon_page
    page.goto(f"/priorities/vendor/{MADRE}")
    page.wait_for_selector("[data-testid=prod-vendor-page]")
    idle(page)
    assert page.locator("[data-testid=prod-card][data-id='2'] [data-testid=prod-format-text]").text_content() == "F3 Short acted scene with two people (30 to 60s)"
    assert page.locator("[data-testid=prod-card][data-id='1'] [data-testid=prod-format-text]").text_content() == "Not chosen"
    assert page.locator("[data-testid=prod-card] select").count() == 0 and page.locator("[data-testid=prod-card] [data-testid=prod-format]").count() == 0       # nothing to change it with
    r = page.request.patch(f"{base_url}/api/priorities/vendor/{MADRE}/2", data={"format_code": "F1"})
    assert r.status == 400
    page.reload()
    page.wait_for_selector("[data-testid=prod-vendor-page]")
    idle(page)
    assert "F3" in page.locator("[data-testid=prod-card][data-id='2'] [data-testid=prod-format-text]").text_content()


# ---------------------------------------------------------------------------------------------------------------- details panel (the same panel as /priorities, plus up to 10 example ads)
def test_production_row_expands_to_the_priorities_panel_with_ten_examples(page):
    reqs = []
    page.on("request", lambda r: reqs.append(r.url) if "/detail" in r.url else None)
    goto(page)
    assert reqs == []                                                                  # lazy: nothing is fetched until a row is opened
    btn = row(page, 1).locator("[data-testid=pri-x-toggle]")
    assert btn.get_attribute("aria-expanded") == "false" and btn.text_content() == "Details"
    page.locator("[data-testid=prod-detail-row][data-id='1']").evaluate("e => { if (!e.hidden) throw new Error('open'); }")
    btn.focus()
    page.keyboard.press("Enter")                                                       # keyboard operable
    panel = page.locator("[data-testid=prod-detail-row][data-id='1'] .pri-panel")
    panel.wait_for()
    assert btn.get_attribute("aria-expanded") == "true" and btn.text_content() == "Hide details"
    assert len([u for u in reqs if "/assignments/1/detail" in u]) == 1
    text = panel.text_content()
    assert "ED / Performance" in text and "Bedroom Upgrade Seeker" in text and "General Vitality / Aging Well" in text      # who it is for
    assert panel.locator(".pri-tile-story").count() == 1 and panel.locator("[data-testid=pri-hooks]").count() == 1          # story and hooks, as on /priorities
    assert panel.locator(".pri-sc").count() > 0                                        # the number tiles
    ex = panel.locator("[data-testid=pri-example]")
    assert ex.count() == 10
    spends = ex.evaluate_all("rs => rs.map(r => parseFloat(r.querySelector('.pri-w-num').textContent.replace(/[$,]/g, '')))")
    assert spends == sorted(spends, reverse=True)
    assert panel.locator("[data-testid=pri-example-best]").count() == 1
    links = panel.locator("[data-testid=pri-example-link]")
    assert links.count() == 10 and links.first.get_attribute("href").startswith("/ad/")
    row(page, 1).locator("[data-testid=pri-x-toggle]").click()                         # collapse
    assert btn.get_attribute("aria-expanded") == "false"
    page.wait_for_function("() => document.querySelector(\"[data-testid=prod-detail-row][data-id='1']\")?.hidden")
    row(page, 1).locator("[data-testid=pri-x-name]").click()                           # the concept name opens it again, without a second request
    page.wait_for_function("() => { const e = document.querySelector(\"[data-testid=prod-detail-row][data-id='1']\"); return !!e && !e.hidden; }")
    assert btn.get_attribute("aria-expanded") == "true" and len([u for u in reqs if "/assignments/1/detail" in u]) == 1
    assert "—" not in page.locator("[data-testid=prod-detail-row][data-id='1']").text_content()


def test_production_row_with_no_current_concept_says_so(page):
    goto(page)
    row(page, 2).locator("[data-testid=pri-x-toggle]").click()
    box = page.locator("[data-testid=prod-detail-row][data-id='2']")
    box.locator("[data-testid=pri-no-numbers]").wait_for()
    assert box.locator("[data-testid=pri-no-numbers]").text_content() == "No current numbers for this concept."
    assert "Thinning and Drained" in box.text_content() and box.locator("[data-testid=pri-example]").count() == 0


def test_an_open_row_stays_open_after_a_save(page):
    goto(page)
    row(page, 1).locator("[data-testid=pri-x-toggle]").click()
    page.locator("[data-testid=prod-detail-row][data-id='1'] .pri-panel").wait_for()
    row(page, 1).locator("[data-testid=prod-edit-brief_url]").click()
    fill_dialog(page, "https://docs.google.com/document/d/abc")
    page.wait_for_function("() => document.querySelector(\"[data-testid=prod-row][data-id='1'] [data-testid=prod-stage]\")?.textContent.trim() === 'Brief sent'")
    page.locator("[data-testid=prod-detail-row][data-id='1'] .pri-panel").wait_for()
    assert row(page, 1).locator("[data-testid=pri-x-toggle]").get_attribute("aria-expanded") == "true"


def test_vendor_card_expands_with_examples_but_no_ad_links_and_no_internal_notes(anon_page):
    page = anon_page
    reqs = []
    page.on("request", lambda r: reqs.append(r.url) if "/detail" in r.url else None)
    page.goto(f"/priorities/vendor/{MADRE}")
    page.wait_for_selector("[data-testid=prod-vendor-page]")
    idle(page)
    assert reqs == []
    card = page.locator("[data-testid=prod-card][data-id='1']")
    btn = card.locator("[data-testid=pri-x-toggle]")
    btn.click()
    panel = card.locator(".pri-panel")
    panel.wait_for()
    assert btn.get_attribute("aria-expanded") == "true" and len(reqs) == 1 and f"/vendor/{MADRE}/1/detail" in reqs[0]
    assert "ED / Performance" in panel.text_content() and panel.locator("[data-testid=pri-hooks]").count() == 1
    assert panel.locator("[data-testid=pri-example]").count() == 10 and panel.locator("[data-testid=pri-example-best]").count() == 1
    assert panel.locator("a[href^='/ad/']").count() == 0 and panel.locator("a[href*='adsmanager']").count() == 0      # the ad page needs a login, Ads Manager too: neither is ever linked for a vendor
    pub = panel.locator("[data-testid=pri-example-public]")                           # a link only where the server sent a https Facebook / Instagram one; new tab, no opener
    assert pub.count() == 3
    assert pub.evaluate_all("els => els.map(a => [a.getAttribute('href'), a.target, a.rel])") == [["https://www.facebook.com/lonvera/posts/101", "_blank", "noopener noreferrer"], ["https://www.facebook.com/lonvera/posts/102", "_blank", "noopener noreferrer"], ["https://www.instagram.com/p/Cabc123/", "_blank", "noopener noreferrer"]]
    for n in (4, 5, 6):                                                                       # javascript:, Google Drive and plain http are refused by the page too: plain text
        assert panel.locator("[data-testid=pri-example]").nth(n).locator("a").count() == 0
    assert panel.locator("[data-testid=pri-example]").nth(3).locator("a").count() == 0       # no link on file: plain text
    card.locator("[data-testid=pri-x-name]").click()                                   # name collapses it
    page.wait_for_function("() => document.querySelector(\"[data-id='1'] [data-testid=prod-detail]\")?.hidden")
    assert btn.get_attribute("aria-expanded") == "false"
    page.locator("[data-testid=prod-card][data-id='2'] [data-testid=pri-x-toggle]").click()
    page.locator("[data-testid=prod-card][data-id='2'] [data-testid=pri-no-numbers]").wait_for()
    assert "Note:" not in page.locator("body").text_content()


@pytest.mark.parametrize("path", ["/priorities/production?view=list", f"/priorities/vendor/{MADRE}"])
def test_no_sideways_page_scroll_at_375px_with_a_row_open(browser, base_url, path):
    c = browser.new_context(viewport={"width": 375, "height": 812}, base_url=base_url)
    try:
        c.request.post(base_url + "/__test/assign/reset")
        c.request.post(base_url + "/api/login", data={"username": "JoshHoughton", "password": "test"})
        pg = c.new_page()
        pg.goto(path)
        pg.wait_for_selector("[data-testid=prod-page], [data-testid=prod-vendor-page]")
        idle(pg)
        pg.locator("[data-id='1'] [data-testid=pri-x-toggle]").first.click()
        pg.locator("[data-testid=pri-example]").first.wait_for()
        assert pg.locator("[data-testid=pri-example]").count() == 10
        assert pg.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth + 1")
        assert pg.evaluate("() => document.body.scrollWidth <= window.innerWidth + 1")
    finally:
        c.close()


# ---------------------------------------------------------------------------------------------------------------- shared header and filters, status board, ready / spend / return columns
def test_page_uses_the_shared_header_and_filter_row_and_new_names(page):
    goto(page)
    assert page.locator("[data-testid=prod-head].pagehead .titleline h1").text_content() == "Production"
    assert page.locator(".pagefilters [data-testid=prod-week]").count() == 1 and page.locator(".pagefilters [data-testid=prod-vendor]").count() == 1
    assert page.locator(".pagefilters [data-testid=prod-closed]").count() == 1
    assert page.locator("[data-testid=prod-back]").text_content().strip() == "Back to Priorities"
    assert page.title() == "Production · Lonvera"
    assert "Production board" not in page.locator("[data-testid=prod-page]").text_content()


def test_status_filter_counts_and_filters_rows(page):
    goto(page)
    labels = page.locator("[data-testid=prod-stage-filter] .seg").all_text_contents()
    assert labels[0] == "All 3" and "Assigned 2" in labels and "Creative delivered 1" in labels and not any("Launched" in x for x in labels)
    page.click("[data-testid=prod-stagebtn-creative_in]")
    page.wait_for_function("() => document.querySelectorAll('[data-testid=prod-row]').length === 1")
    assert page.locator("[data-testid=prod-stagebtn-creative_in]").get_attribute("aria-pressed") == "true"
    assert "stage=creative_in" in page.url
    page.click("[data-testid=prod-stagebtn-all]")
    page.wait_for_function("() => document.querySelectorAll('[data-testid=prod-row]').length === 3")


def test_ready_spend_and_return_columns_are_visible(page):
    goto(page, "?closed=1")
    assert row(page, 2).locator("[data-testid=prod-ready-state]").get_attribute("data-ready") == "0"
    assert row(page, 1).locator("[data-testid=prod-ready-state]").text_content().strip() == "Needs creative"
    assert row(page, 4).locator("[data-testid=prod-ready-state]").get_attribute("data-ready") == "1"
    assert row(page, 4).locator("[data-testid=prod-spend]").text_content().strip() == "$1,240"
    assert row(page, 4).locator("[data-testid=prod-return]").text_content().strip() == "1.80"
    assert row(page, 1).locator("[data-testid=prod-spend]").text_content().strip() == "–"
    assert page.locator("[data-testid=prod-table] thead th[title*='Return on ad spend']").count() == 1


def test_more_hides_change_and_drop_until_opened(page):
    goto(page)
    assert not row(page, 1).locator("[data-testid=prod-drop]").is_visible()
    more(page, 1)
    assert row(page, 1).locator("[data-testid=prod-drop]").is_visible() and row(page, 1).locator("[data-testid=prod-change]").is_visible()


def test_vendor_page_is_a_single_column_with_big_buttons_at_375px(browser, base_url):
    c = browser.new_context(viewport={"width": 375, "height": 812}, base_url=base_url)
    try:
        pg = c.new_page()
        pg.goto(f"/priorities/vendor/{MADRE}")
        pg.wait_for_selector("[data-testid=prod-vendor-page]")
        assert pg.locator("[data-testid=prod-vhelp]").is_visible()
        assert pg.locator("[data-testid=prod-card] [data-testid=prod-stage]").count() == pg.locator("[data-testid=prod-card]").count()
        assert pg.locator("[data-testid=prod-save]").first.bounding_box()["height"] >= 40
        assert pg.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth + 1")
    finally:
        c.close()


def test_history_shows_who_assigned_and_each_stage_change_with_the_time(page):
    goto(page)
    r1 = row(page, 1)
    r1.locator("[data-testid=prod-history] summary").click()
    ev = r1.locator("[data-testid=prod-event]")
    assert ev.count() == 1 and "josh" in ev.first.text_content() and "Assigned to production" in ev.first.text_content()
    r1.locator("[data-testid=prod-edit-brief_url]").click()
    page.fill("[data-testid=prod-field-url]", "https://docs.google.com/document/d/newbrief")
    page.click("[data-testid=prod-dialog-ok]")
    idle(page)
    page.wait_for_selector("[data-testid=prod-row][data-id='1'] [data-testid=prod-history] summary:has-text('History (2)')")
    h = row(page, 1).locator("[data-testid=prod-history]")
    h.locator("summary").click()
    last = row(page, 1).locator("[data-testid=prod-event]").last.text_content()
    assert "Brief link added" in last and "Stage: Assigned to Brief sent" in last and "josh" in last
    # a row that was moved by a vendor and by an unknown person says so plainly (row 4 is launched, so it sits under Show launched and dropped)
    goto(page, "?closed=1")
    row(page, 4).locator("[data-testid=prod-history] summary").click()
    txt = row(page, 4).locator("[data-testid=prod-history]").text_content()
    assert "Madre (vendor link)" in txt and "Not recorded" in txt and "Marked ready to launch" in txt


def test_vendor_page_has_no_history(anon_page, base_url):
    anon_page.goto(f"{base_url}/priorities/vendor/{MADRE}")
    anon_page.wait_for_selector("[data-testid=prod-vendor-page]")
    assert anon_page.locator("[data-testid=prod-history]").count() == 0


def test_week_vendor_and_product_filters_take_several_choices_at_once(page):
    goto(page, "?closed=1")
    page.wait_for_function("() => document.querySelectorAll('[data-testid=prod-row]').length === 4")
    ids = lambda: sorted(page.locator("[data-testid=prod-row]").evaluate_all("rs => rs.map(r => r.dataset.id)"))
    btn = lambda tid: page.locator(f".pagefilters [data-testid={tid}-btn]").text_content().strip()
    assert btn("prod-vendor") == "All vendors" and btn("prod-week") == "All weeks" and btn("prod-product-filter") == "All products"
    choose(page, "prod-product-filter", "vita-bull", "boneflex")                       # two products at once
    page.wait_for_function("() => document.querySelectorAll('[data-testid=prod-row]').length === 3")
    assert ids() == ["1", "3", "4"] and btn("prod-product-filter") == "2 selected"
    choose(page, "prod-vendor", "Madre", "Victoria")
    assert btn("prod-vendor") == "2 selected" and ids() == ["1", "3", "4"]
    choose(page, "prod-vendor", "Victoria")
    page.wait_for_function("() => document.querySelectorAll('[data-testid=prod-row]').length === 1")
    assert btn("prod-vendor") == "Victoria" and "vendor=Victoria" in page.url and "product=" in page.url
    page.reload()                                                                       # the choices live in the address
    page.wait_for_selector("[data-testid=prod-page]")
    idle(page)
    assert btn("prod-vendor") == "Victoria" and btn("prod-product-filter") == "2 selected"
    choose(page, "prod-vendor", "Madre", "Victoria")
    choose(page, "prod-product-filter")
    page.wait_for_function("() => document.querySelectorAll('[data-testid=prod-row]').length === 4")
    wk = [cb.get_attribute("value") for cb in page.locator("[data-testid=prod-week] input[type=checkbox]").all()]
    choose(page, "prod-week", wk[0], wk[1])                                             # two weeks at once
    assert btn("prod-week") == "2 selected"


def test_variations_of_one_concept_are_one_entry_with_the_variations_inside(page):
    goto(page)
    got = page.evaluate("""async () => { const m = await import('/static/js/pages/production_common.js');
      const ids = ['Lonvera-VitalBull-Video-3.1-hook1', 'Lonvera-VitalBull-Video-3.1-hook2', 'Lonvera-VitalBull-Video-3.1-hook3', 'Lonvera-VitalBull-Video-4.2', 'Victoria-BeefLiver-Static-C1.E1', 'Lonvera-X-9 - Copy 2', 'Lonvera-X-9'];
      return { groups: m.variationGroups(ids).map((g) => [g.base, g.list.length]), label: m.variationLabel(ids[1], m.variationBase(ids[1])), title: m.ticketTitle({ vendor: 'Studio Flow', cluster: 'Iron & B12', pain_point: 'Nutrient Deficiency', for_week: '2026-11-09' }) }; }""")
    assert got["groups"] == [["Lonvera-VitalBull-Video-3.1", 3], ["Lonvera-VitalBull-Video-4.2", 1], ["Victoria-BeefLiver-Static-C1.E1", 1], ["Lonvera-X-9", 2]]
    assert got["label"] == "hook2"
    assert got["title"] == "Studio Flow - Iron & B12 - Nutrient Deficiency - 11/9"


def test_a_filter_applies_on_every_tick_and_the_list_stays_open(page):
    goto(page)
    d = page.locator(".pagefilters [data-testid=prod-vendor]")
    d.locator("summary").click()
    page.locator(".pagefilters [data-testid=prod-vendor] input[value='Victoria']").check()
    page.wait_for_function("() => document.querySelectorAll('[data-testid=prod-row]').length === 1")      # the table already changed
    assert page.locator(".pagefilters [data-testid=prod-vendor]").get_attribute("open") is not None       # and the list is still open
    page.locator(".pagefilters [data-testid=prod-vendor] input[value='Madre']").check()
    page.wait_for_function("() => document.querySelectorAll('[data-testid=prod-row]').length === 3")
    assert page.locator(".pagefilters [data-testid=prod-vendor-btn]").text_content().strip() == "2 selected"
    page.locator(".pagefilters [data-testid=prod-vendor] input[value='Victoria']").uncheck()
    page.wait_for_function("() => document.querySelectorAll('[data-testid=prod-row]').length === 2")
    page.mouse.click(5, 400)                                                                              # a click outside closes it
    page.wait_for_function("() => !document.querySelector('.pagefilters [data-testid=prod-vendor]').hasAttribute('open')")
