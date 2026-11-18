"""Theme previews page (/theme-previews) against the mock (tests/ui/mock_theme.py). Dark by default: nav link only when enabled and allowed. Nothing here reaches Shopify."""
import pytest

pytestmark = pytest.mark.mock_only


@pytest.fixture(autouse=True)
def fresh(ctx, base_url):
    ctx.request.post(base_url + "/__test/theme/reset")


@pytest.fixture
def cfg(ctx, base_url):
    return lambda **kw: ctx.request.post(base_url + "/__test/theme/config", data=kw)


def idle(page):
    page.wait_for_function("() => !document.querySelector('#view').hasAttribute('aria-busy')")


def test_nav_link_hidden_when_off_and_when_not_allowed(page, cfg):
    page.goto("/inventory/history")
    page.wait_for_selector("[data-testid=nav-inventory-history]")
    assert page.locator("[data-testid=nav-theme-previews]").count() == 0
    cfg(enabled=True, can_edit=False)
    page.goto("/inventory/history")
    page.wait_for_selector("[data-testid=nav-inventory-history]")
    assert page.locator("[data-testid=nav-theme-previews]").count() == 0


def test_page_says_not_available_when_off(page):
    page.goto("/theme-previews")
    page.wait_for_selector("[data-testid=tp-page]")
    assert "not available" in page.locator("[data-testid=tp-off]").inner_text()


def test_make_preview_edit_and_save(page, ctx, base_url, cfg):
    cfg(enabled=True, can_edit=True)
    page.goto("/inventory/history")
    page.wait_for_selector("[data-testid=nav-theme-previews]")
    page.click("[data-testid=nav-theme-previews]")
    page.wait_for_selector("[data-testid=tp-page]")
    idle(page)
    assert "Changes here go to a preview only. Going live is done in Shopify admin by Josh." in page.locator("[data-testid=tp-note]").inner_text()
    assert "Live theme (read only)" in page.locator("[data-testid=tp-live]").inner_text()
    assert page.locator("[data-testid=tp-empty]").count() == 1
    page.fill("[data-testid=tp-name]", "Bigger button")
    page.click("[data-testid=tp-new]")
    page.wait_for_selector("[data-testid=tp-open-100]")
    assert page.locator("[data-testid=tp-open-100]").get_attribute("href").endswith("?preview_theme_id=100")
    page.click("[data-testid=tp-pick-100]")
    page.wait_for_selector("[data-testid=tp-files]")
    page.click("[data-key='sections/header.liquid']")
    page.wait_for_selector("[data-testid=tp-text]")
    assert page.locator("[data-testid=tp-text]").input_value() == "<header>Hello</header>"
    page.fill("[data-testid=tp-text]", "<header><b>x</b></header>")
    page.click("[data-testid=tp-save]")
    page.wait_for_function("() => document.querySelector('[data-testid=tp-msg]').textContent.startsWith('Saved')")
    assert page.locator("[data-testid=tp-msg] b").count() == 0                       # text, not HTML
    assert ctx.request.get(base_url + "/__test/theme/calls").json() == [{"id": "100", "key": "sections/header.liquid", "content": "<header><b>x</b></header>"}]


def test_usable_at_375_px(page, cfg):
    cfg(enabled=True, can_edit=True)
    page.set_viewport_size({"width": 375, "height": 800})
    page.goto("/theme-previews")
    page.wait_for_selector("[data-testid=tp-page]")
    idle(page)
    assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth + 1")
    assert page.locator("[data-testid=tp-new]").is_visible()
