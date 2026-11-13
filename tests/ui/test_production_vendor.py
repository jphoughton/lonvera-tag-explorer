"""Vendor page "My tasks" (/priorities/vendor/<token>) against tests/ui/mock_production_vendor.py: sections, due chips, the three vendor transitions, Submit blocked without a creative link, comment thread,
the Changes requested banner, phone layout and what a vendor never sees."""
import json
import re

import pytest

pytestmark = pytest.mark.mock_only

MADRE = "tok-b3-madre-00000000000000000"
VICTORIA = "tok-b3-victoria-000000000000000"


@pytest.fixture(autouse=True)
def fresh(ctx, base_url):
    ctx.request.post(base_url + "/__test/vendorb3/reset")


def open_page(page, token=MADRE):
    page.goto(f"/priorities/vendor/{token}")
    page.wait_for_selector("[data-testid=prod-vendor-page]")
    page.wait_for_function("() => !document.querySelector('#view').hasAttribute('aria-busy')")
    return page


def card(page, i):
    return page.locator(f"[data-testid=prod-card][data-id='{i}']")


def section_ids(page):
    return page.evaluate("""() => Object.fromEntries([...document.querySelectorAll('[data-testid=prod-v-section]')].map(s =>
        [s.dataset.section, [...s.querySelectorAll('[data-testid=prod-card]')].map(c => c.dataset.id)]))""")


def record(page):
    reqs = []
    page.on("request", lambda r: reqs.append((r.method, r.url.split("/api/priorities/vendor/")[-1].split("/", 1)[-1], r.post_data)) if "/api/priorities/vendor/" in r.url and r.method != "GET" else None)
    return reqs


def test_sections_render_with_counts_and_a_plain_count_line(anon_page):
    page = open_page(anon_page)
    assert page.locator("h1").text_content() == "Madre: concepts to make"
    assert not page.locator("#topbar").is_visible()
    ids = section_ids(page)
    assert sorted(ids["todo"]) == ["1", "2"] and sorted(ids["prod"]) == ["3", "4"] and ids["review"] == ["5"] and sorted(ids["done"]) == ["6", "7"]
    assert page.locator("[data-testid=prod-vhelp]").text_content().strip() == "2 to start, 2 in production, 1 in review, 2 done."
    titles = page.locator("[data-testid=prod-v-section] h2").evaluate_all("hs => hs.map(h => h.firstChild.textContent)")
    assert titles == ["To start", "In production", "In review", "Done"]
    assert "waiting for the team" in page.locator("[data-section=review]").text_content().lower()
    assert page.locator("[data-testid=prod-weekgroup]").count() >= 4
    assert "—" not in page.locator("[data-testid=prod-vendor-page]").text_content()


def test_card_shows_product_format_due_chip_and_brief_button(anon_page):
    page = open_page(anon_page)
    c = card(page, 1)
    assert c.locator("[data-testid=prod-v-format]").text_content().strip() == "F1"
    assert "Beef Liver" in c.locator("[data-testid=prod-v-product]").text_content()
    assert c.locator("[data-testid=prod-v-due]").text_content().strip() == "Due tomorrow" and c.locator("[data-testid=prod-v-due]").get_attribute("data-state") == "soon"
    brief = c.locator("[data-testid=prod-v-brief]")
    assert brief.get_attribute("href").endswith("brief1") and brief.get_attribute("target") == "_blank" and "noopener" in brief.get_attribute("rel")
    assert card(page, 3).locator("[data-testid=prod-v-due]").text_content().strip() == "Overdue by 2 days"
    assert card(page, 3).locator("[data-testid=prod-v-due]").get_attribute("data-state") == "overdue"
    assert card(page, 2).locator("[data-testid=prod-v-due]").get_attribute("data-state") == "ok"
    assert card(page, 6).locator("[data-testid=prod-v-due]").get_attribute("data-state") == "ok"      # done work is never overdue
    assert card(page, 1).locator("[data-testid=prod-format-text]").text_content().startswith("F1 Long animated story")
    assert card(page, 1).locator("[data-testid=prod-story]").count() == 1


def test_start_moves_a_card_from_to_start_to_in_production(anon_page):
    page = open_page(anon_page)
    reqs = record(page)
    card(page, 1).locator("[data-testid=prod-v-start]").click()
    page.wait_for_selector("[data-section=prod] [data-testid=prod-card][data-id='1']")
    assert ("PATCH", "1", '{"status":"in_production"}') in [(m, p, d.replace(" ", "")) for m, p, d in reqs]
    assert "1" not in section_ids(page)["todo"]
    assert card(page, 1).locator("[data-testid=prod-stage]").text_content().strip() == "In production"
    assert card(page, 2).locator("[data-testid=prod-v-start]").count() == 1                 # brief_sent can start too
    assert card(page, 3).locator("[data-testid=prod-v-start]").count() == 0


def test_submit_for_review_is_blocked_without_a_creative_link_then_sends_it(anon_page):
    page = open_page(anon_page)
    reqs = record(page)
    c = card(page, 3)
    sub = c.locator("[data-testid=prod-v-submit]")
    assert sub.is_disabled() and c.locator("[data-testid=prod-vready]").count() == 0
    assert "Add the link to your creative" in c.locator("[data-testid=prod-v-hint]").text_content() and c.locator("[data-testid=prod-v-hint]").is_visible()
    c.locator("[data-testid=prod-in-creative]").fill("https://drive.google.com/drive/folders/new3")
    ready = c.locator("[data-testid=prod-vready]")
    assert ready.count() == 1 and ready.is_enabled() and not c.locator("[data-testid=prod-v-hint]").is_visible()
    ready.click()
    page.wait_for_selector("[data-section=review] [data-testid=prod-card][data-id='3']")
    sent = [json.loads(d) for m, p, d in reqs if p == "3"]
    assert sent == [{"creative_url": "https://drive.google.com/drive/folders/new3", "vendor_note": "", "status": "in_review"}]
    assert card(page, 3).locator("[data-testid=prod-stage]").text_content().strip() == "In review"
    # clearing the link again disables the button on a card that has none saved
    c2 = card(page, 4)
    c2.locator("[data-testid=prod-in-creative]").fill("")
    assert c2.locator("[data-testid=prod-v-submit]").is_disabled()


def test_a_card_with_a_saved_link_can_be_submitted_at_once(anon_page):
    page = open_page(anon_page)
    assert card(page, 4).locator("[data-testid=prod-vready]").is_enabled()
    card(page, 4).locator("[data-testid=prod-vready]").click()
    page.wait_for_selector("[data-section=review] [data-testid=prod-card][data-id='4']")


def test_pull_back_moves_a_review_card_to_production(anon_page):
    page = open_page(anon_page)
    reqs = record(page)
    assert card(page, 5).locator("[data-testid=prod-vready]").count() == 0 and card(page, 5).locator("[data-testid=prod-v-start]").count() == 0
    card(page, 5).locator("[data-testid=prod-v-pullback]").click()
    page.wait_for_selector("[data-section=prod] [data-testid=prod-card][data-id='5']")
    assert [json.loads(d) for m, p, d in reqs if p == "5"] == [{"status": "in_production"}]


def test_done_cards_have_no_status_buttons_and_launched_ones_show_spend_and_return(anon_page):
    page = open_page(anon_page)
    for i in (6, 7):
        for t in ("prod-v-start", "prod-v-submit", "prod-vready", "prod-v-pullback", "prod-vunready"):
            assert card(page, i).locator(f"[data-testid={t}]").count() == 0
    assert "$1,240 spent" in card(page, 7).locator("[data-testid=prod-result]").text_content()
    assert card(page, 6).locator("[data-testid=prod-stage]").text_content().strip() == "Ready to launch"


def test_save_keeps_the_link_and_note_without_changing_status(anon_page):
    page = open_page(anon_page)
    reqs = record(page)
    c = card(page, 1)
    c.locator("[data-testid=prod-in-creative]").fill("https://drive.google.com/drive/folders/x")
    c.locator("[data-testid=prod-in-note]").fill("Two cuts")
    c.locator("[data-testid=prod-save]").click()
    page.wait_for_function("() => document.querySelector(\"[data-id='1'] [data-testid=prod-saved]\")?.textContent === 'Saved'")
    assert [json.loads(d) for m, p, d in reqs if p == "1"] == [{"creative_url": "https://drive.google.com/drive/folders/x", "vendor_note": "Two cuts"}]
    assert "1" in section_ids(page)["todo"]


def test_changes_requested_banner_shows_the_team_comment_on_a_sent_back_card(anon_page):
    page = open_page(anon_page)
    b = card(page, 4).locator("[data-testid=prod-v-changes]")
    assert b.is_visible() and "Changes requested" in b.text_content() and "first 3 seconds need the product on screen" in b.text_content() and "Josh" in b.text_content()
    assert card(page, 3).locator("[data-testid=prod-v-changes]").count() == 0 and card(page, 5).locator("[data-testid=prod-v-changes]").count() == 0
    assert card(page, 4).locator("[data-testid=prod-v-thread]").get_attribute("open") is not None      # the thread opens by itself
    assert b.evaluate("e => getComputedStyle(e).backgroundColor") != "rgba(0, 0, 0, 0)"


def test_thread_lists_team_replies_and_posting_a_comment_adds_it(anon_page, base_url):
    page = open_page(anon_page)
    c = card(page, 5)
    c.locator("[data-testid=prod-v-thread] summary").click()
    items = c.locator("[data-testid=prod-v-comment]")
    assert items.count() == 2 and items.nth(0).get_attribute("data-kind") == "vendor" and items.nth(1).get_attribute("data-kind") == "app"
    assert "Looks good" in items.nth(1).text_content() and "Josh" in items.nth(1).text_content() and "You" in items.nth(0).text_content()
    reqs = record(page)
    c.locator("[data-testid=prod-v-comment-input]").fill("Added captions too")
    c.locator("[data-testid=prod-v-comment-post]").click()
    page.wait_for_function("() => document.querySelectorAll(\"[data-id='5'] [data-testid=prod-v-comment]\").length === 3")
    assert [json.loads(d) for m, p, d in reqs if p == "5/comments"] == [{"body": "Added captions too"}]
    c = card(page, 5)
    assert "Added captions too" in c.locator("[data-testid=prod-v-comment]").last.text_content()
    assert c.locator("[data-testid=prod-v-comment-input]").input_value() == ""
    assert "Comments (3)" in c.locator("[data-testid=prod-v-thread] summary").text_content()
    assert c.locator("[data-testid=prod-v-thread]").get_attribute("open") is not None


def test_an_empty_comment_is_refused_and_a_typed_one_survives_another_card_saving(anon_page):
    page = open_page(anon_page)
    c = card(page, 1)
    c.locator("[data-testid=prod-v-thread] summary").click()
    c.locator("[data-testid=prod-v-comment-post]").click()
    assert "Write a comment first" in c.locator("[data-testid=prod-saved]").text_content()
    c.locator("[data-testid=prod-v-comment-input]").fill("Draft question")
    card(page, 2).locator("[data-testid=prod-in-note]").fill("n")
    card(page, 2).locator("[data-testid=prod-save]").click()
    page.wait_for_function("() => document.querySelector(\"[data-id='2'] [data-testid=prod-saved]\")?.textContent === 'Saved'")
    assert card(page, 1).locator("[data-testid=prod-v-comment-input]").input_value() == "Draft question"


def test_a_new_team_reply_shows_after_the_next_load(anon_page, ctx, base_url):
    ctx.request.post(base_url + "/__test/vendorb3/team-comment", data={"id": 3, "body": "Changes requested: shorter intro please."})
    page = open_page(anon_page)
    assert "shorter intro" in card(page, 3).locator("[data-testid=prod-v-changes]").text_content()


def test_vendor_never_sees_internal_fields_other_vendors_or_team_controls(anon_page):
    page = open_page(anon_page)
    body = page.locator("body").text_content()
    assert "INTERNAL NOTE" not in body and "VICTORIA" not in body and "Victoria only concept" not in body and "secret" not in body
    assert page.locator("[data-testid=prod-card]").count() == 7
    for t in ("prod-history", "prod-event", "prod-drop", "prod-more", "prod-ready-state", "prod-made"):
        assert page.locator(f"[data-testid={t}]").count() == 0
    for word in ("Approve", "Mark launched", "Drop", "Restore"):
        assert not re.search(r"\b%s\b" % word, body), word
    api = page.request.get(f"/api/priorities/vendor/{MADRE}").json()
    assert all("note" not in r and "history" not in r for r in api["rows"])
    r = page.request.patch(f"/api/priorities/vendor/{MADRE}/1", data={"status": "ready"})
    assert r.status == 400
    r = page.request.patch(f"/api/priorities/vendor/{MADRE}/8", data={"status": "in_production"})
    assert r.status == 404                                                  # Victoria's card


def test_the_other_vendor_sees_only_own_cards(anon_page):
    page = open_page(anon_page, VICTORIA)
    assert page.locator("[data-testid=prod-card]").count() == 1 and "Victoria only concept" in page.locator("body").text_content()
    assert "Hair Loss" not in page.locator("body").text_content()


@pytest.mark.allow_errors
def test_bad_token_says_the_link_is_not_valid_and_only_token_routes_work(anon_page):
    page = anon_page
    page.goto("/priorities/vendor/not-a-real-token")
    page.wait_for_selector("[data-testid=prod-empty]")
    assert "not valid" in page.locator("[data-testid=prod-empty]").text_content()
    assert page.request.get("/api/priorities/vendor/nope/1/comments").status == 404
    assert page.request.post("/api/priorities/vendor/nope/1/comments", data={"body": "x"}).status == 404


def test_details_expander_still_opens_from_the_card_name(anon_page):
    page = open_page(anon_page)
    card(page, 1).locator("button[data-label=name], .prod-namebtn, h3 button").first.click()
    page.wait_for_selector("[data-id='1'] [data-testid=prod-detail] >> text=/./", timeout=5000)


@pytest.mark.parametrize("vp", [(375, 812), (360, 740)])
def test_phone_layout_has_no_sideways_scroll_and_big_targets(browser, base_url, vp):
    c = browser.new_context(viewport={"width": vp[0], "height": vp[1]}, base_url=base_url)
    try:
        pg = c.new_page()
        pg.goto(f"/priorities/vendor/{MADRE}")
        pg.wait_for_selector("[data-testid=prod-vendor-page]")
        pg.locator("[data-testid=prod-v-thread] summary").first.click()
        assert pg.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth + 1")
        small = pg.evaluate("""() => [...document.querySelectorAll('.prod-vendor button.btn, .prod-vendor input, .prod-vendor textarea, .prod-vendor summary, .prod-v-brief')]
            .filter(e => e.offsetParent !== null).map(e => [e.dataset.testid || e.tagName, e.getBoundingClientRect().height]).filter(x => x[1] < 43.5)""")
        assert not small, small
        fs = pg.evaluate("() => [...document.querySelectorAll('.prod-vendor input, .prod-vendor textarea')].filter(e => e.offsetParent !== null).map(e => parseFloat(getComputedStyle(e).fontSize))")
        assert fs and min(fs) >= 16
        cards = pg.evaluate("() => [...document.querySelectorAll('[data-testid=prod-card]')].map(c => c.getBoundingClientRect().width)")
        assert max(cards) <= vp[0] - 20
    finally:
        c.close()
