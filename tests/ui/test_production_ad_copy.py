"""Ad copy in the card drawer (a tab per variation, primary texts and headlines as plain text with a Copy button, editable and saved on its own), against the mock."""
import pytest

pytestmark = pytest.mark.mock_only

VARS = [{"label": "Execution A", "url": "", "concept_id": ""}, {"label": "Execution B", "url": "", "concept_id": ""}]


@pytest.fixture(autouse=True)
def fresh(ctx, base_url):
    ctx.request.post(base_url + "/__test/assign/reset")
    ctx.request.post(base_url + "/__test/board/seed")


def open_card(page, aid=1):
    page.goto("/priorities/production")
    page.wait_for_selector("[data-testid=prod-page]")
    assert page.evaluate("async ([a, v]) => (await fetch('/api/priorities/assignments/' + a, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ variations: v }) })).status", [aid, VARS]) == 200
    page.goto(f"/priorities/production?card={aid}")
    page.wait_for_selector("[data-testid=prod-copy]")


def stored(page, aid=1):
    rows = page.evaluate("async () => (await (await fetch('/api/priorities/assignments?include_closed=1')).json()).rows")
    return next(r for r in rows if r["id"] == aid)["ad_copy"]


def test_empty_copy_shows_tabs_and_nothing_yet(page):
    open_card(page)
    assert [t.inner_text() for t in page.locator("[data-testid=prod-copy-tab]").all()] == ["Execution A (none)", "Execution B (none)"]
    assert page.locator("[data-testid=prod-copy-none-primary]").is_visible() and page.locator("[data-testid=prod-copy-none-head]").is_visible()


def test_add_edit_save_and_it_comes_back_per_variation(page):
    open_card(page)
    page.locator("[data-testid=prod-copy-add-primary]").click()
    page.locator("[data-testid=prod-copy-primary]").first.fill("Primary one\nline two")
    page.locator("[data-testid=prod-copy-add-head]").click()
    page.locator("[data-testid=prod-copy-head]").first.fill("Headline one")
    page.locator("[data-testid=prod-copy-save]").click()
    page.wait_for_function("() => document.querySelector('[data-testid=prod-copy-tab]').textContent === 'Execution A'")
    assert stored(page) == {"Execution A": {"primary_texts": ["Primary one\nline two"], "headlines": ["Headline one"]}}
    page.locator("[data-testid=prod-copy-tab]").nth(1).click()
    assert page.locator("[data-testid=prod-copy-primary]").count() == 0
    page.locator("[data-testid=prod-copy-tab]").first.click()
    assert page.locator("[data-testid=prod-copy-primary]").first.input_value() == "Primary one\nline two"
    assert page.locator("[data-testid=prod-copy-head]").first.input_value() == "Headline one"


def test_copy_button_puts_the_text_on_the_clipboard(page, ctx):
    ctx.grant_permissions(["clipboard-read", "clipboard-write"])
    open_card(page)
    page.locator("[data-testid=prod-copy-add-head]").click()
    page.locator("[data-testid=prod-copy-head]").first.fill("Copy me")
    page.locator("[data-testid=prod-copy-btn]").first.click()
    page.wait_for_function("() => document.querySelector('[data-testid=prod-copy-btn]').textContent === 'Copied'")
    assert page.evaluate("navigator.clipboard.readText()") == "Copy me"


def test_remove_and_the_five_item_limit(page):
    open_card(page)
    for _ in range(5):
        page.locator("[data-testid=prod-copy-add-primary]").click()
    assert page.locator("[data-testid=prod-copy-primary]").count() == 5
    assert page.locator("[data-testid=prod-copy-add-primary]").is_disabled()
    page.locator("[data-testid=prod-copy-remove]").first.click()
    assert page.locator("[data-testid=prod-copy-primary]").count() == 4


def test_it_fits_a_phone(page):
    page.set_viewport_size({"width": 375, "height": 800})
    open_card(page)
    page.locator("[data-testid=prod-copy-add-primary]").click()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
