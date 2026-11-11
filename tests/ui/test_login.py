"""Sign-in, sign-out and the logins-disabled deployment.

Split out of tests/ui/test_ui.py with no assertion changes. Shared helpers live in ui_helpers.py. Run: see the docstring of conftest.py.
"""
import re
from datetime import date, timedelta
from urllib.parse import parse_qs, urlencode, urlparse
import pytest
from playwright.sync_api import expect
from conftest import IS_MOCK, PASSWORD, SHOTS, USERNAME
from ui_helpers import *  # noqa: F401,F403

# ------------------------------------------------------------------ login
@pytest.mark.allow_errors
def test_login_flow(anon_page, base_url):
    page = anon_page
    page.goto("/p/" + SLUG + "?window=7d")
    expect(page).to_have_url(re.compile(r"/login\?next="))
    expect(page.locator("#password")).to_be_visible()
    expect(page.locator("#username")).to_be_focused()  # username first (autofocus)
    assert page.get_attribute("#username", "autocomplete") == "username" and page.get_attribute("#password", "autocomplete") == "current-password"
    page.fill("#username", USERNAME)
    page.fill("#password", "definitely-wrong")
    page.click("button[type=submit]")
    expect(page.locator("#login-error")).to_contain_text("nvalid username or password")  # generic: does not say which one is wrong
    expect(page).to_have_url(re.compile(r"/login"))
    page.fill("#username", "nobody")
    page.fill("#password", PASSWORD)
    page.click("button[type=submit]")
    expect(page.locator("#login-error")).to_contain_text("nvalid username or password")
    page.fill("#username", USERNAME)
    page.fill("#password", PASSWORD)
    page.click("button[type=submit]")
    expect(page).to_have_url(re.compile(r"/p/" + SLUG + r"\?window=7d$"))
    wait_rows(page)
    shot(page, "01_login_next_redirect")


@pytest.mark.mock_only
@pytest.mark.allow_errors
@pytest.mark.parametrize("name", ["JoshHoughton", "Lazer"])
def test_login_with_each_named_user_shows_the_name(anon_page, name):
    page = anon_page
    page.goto("/login")
    page.fill("#username", name)
    page.fill("#password", PASSWORD)
    page.click("button[type=submit]")
    expect(page.locator("h1")).to_have_text("Overview")                                          # a login lands on the default view: the Overview
    expect(page.locator("#whoami")).to_have_text(name)
    assert page.evaluate("() => fetch('/api/status').then(r => r.json()).then(s => s.user)") == name
    page.goto(f"/p/{SLUG}")  # the name stays in the header on every page and goes away after logging out
    wait_rows(page)
    expect(page.locator("#whoami")).to_have_text(name)
    page.click("#logout")
    expect(page).to_have_url(re.compile(r"/login"))
    expect(page.locator("#whoami")).to_be_hidden()


@pytest.mark.mock_only
@pytest.mark.allow_errors
def test_login_rejects_unknown_user_and_wrong_password_generically(anon_page):
    page = anon_page
    page.goto("/login")
    for u, pw in (("Lazer", "nope"), ("someone", PASSWORD)):
        page.fill("#username", u)
        page.fill("#password", pw)
        page.click("button[type=submit]")
        expect(page.locator("#login-error")).to_have_text("invalid username or password")
        expect(page).to_have_url(re.compile(r"/login"))


@pytest.mark.allow_errors
def test_login_throttle_message(anon_page):
    page = anon_page
    page.route("**/api/login", lambda route: route.fulfill(status=429, content_type="application/json", body='{"error":"throttled"}'))
    page.goto("/login")
    page.fill("#username", USERNAME)
    page.fill("#password", PASSWORD)
    page.click("button[type=submit]")
    expect(page.locator("#login-error")).to_have_text("Too many attempts. Try again in a few minutes.")


@pytest.mark.allow_errors
def test_login_then_logout(anon_page):
    page = anon_page
    page.goto("/login")
    page.fill("#username", USERNAME)
    page.fill("#password", PASSWORD)
    page.click("button[type=submit]")
    expect(page.locator("h1")).to_have_text("Overview")
    expect(page.locator("#whoami")).to_have_text(USERNAME)  # the signed-in user's name sits next to Log out
    page.click("#logout")
    expect(page).to_have_url(re.compile(r"/login"))
    page.goto("/?platform=meta")
    expect(page).to_have_url(re.compile(r"/login"))


# ------------------------------------------------------------------ logins disabled (AUTH_DISABLED=1)
def test_auth_disabled_first_visit_loads_without_cookie_login_or_logout(noauth_page):
    page = noauth_page
    page.goto("/p/" + SLUG)  # a first visit, no cookie
    wait_rows(page)
    assert "/login" not in page.url and page.url.endswith("/p/" + SLUG)
    expect(page.locator("#trend-panel")).to_be_visible()
    expect(page.locator("#logout")).to_be_hidden()  # nothing to log out of
    expect(page.locator("#whoami")).to_be_hidden()  # and no user name
    assert page.ctx.cookies() == []
    expect(page.locator("#data-through")).to_contain_text("Data through")
    # the whole app works: drill, ad detail, campaigns
    open_first_row(page)
    wait_rows(page)
    page.goto("/?platform=meta")
    page.wait_for_selector("[data-testid=products-table] tbody tr[data-key]")
    expect(page.locator("#logout")).to_be_hidden()
    page.goto(f"/p/{SLUG}?view=campaigns")
    page.wait_for_selector("[data-testid=campaign-table]")


def test_auth_disabled_login_page_redirects_to_the_app(noauth_page):
    page = noauth_page
    page.goto("/login")
    expect(page.locator("h1")).to_have_text("Overview")
    assert page.url.rstrip("/").endswith(page.url.split("/")[2]) or page.url.endswith("/")  # landed on /
    assert page.locator("#username").count() == 0 and page.locator("#password").count() == 0
    page.goto("/login?next=/p/" + SLUG)  # no login step, so no ?next hop either: just the app
    expect(page.locator("h1")).to_have_text("Overview")
    expect(page.locator("#logout")).to_be_hidden()


@pytest.mark.mock_only
@pytest.mark.allow_errors
def test_login_page_is_named_lonvera_and_fits_a_phone(anon_page):
    page = anon_page
    page.set_viewport_size({"width": 375, "height": 700})
    page.goto("/login")
    expect(page.locator("h1")).to_have_text("Lonvera")
    assert page.title() == "Sign in \u00b7 Lonvera"
    assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth + 1")
    assert page.evaluate("() => { const r = document.querySelector('.login-card').getBoundingClientRect(); return r.left >= 0 && r.right <= window.innerWidth; }")
