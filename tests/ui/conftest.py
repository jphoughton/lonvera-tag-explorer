import os
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from tests._servers import start_mock

HERE = Path(__file__).resolve().parent
SHOTS = HERE / "shots"
SHOTS.mkdir(exist_ok=True)
IS_MOCK = "UI_BASE_URL" not in os.environ
PASSWORD = os.environ.get("UI_PASSWORD", "test")
USERNAME = os.environ.get("UI_USERNAME", "JoshHoughton")


def pytest_configure(config):
    config.addinivalue_line("markers", "allow_errors: do not fail on console errors (e.g. deliberate 401s)")
    config.addinivalue_line("markers", "mock_only: needs the mock server's canned data")


def pytest_collection_modifyitems(config, items):
    if not IS_MOCK:
        skip = pytest.mark.skip(reason="mock-only test (UI_BASE_URL is set)")
        for it in items:
            if "mock_only" in it.keywords:
                it.add_marker(skip)


@pytest.fixture(scope="session")
def base_url():
    env = os.environ.get("UI_BASE_URL")
    if env:
        yield env.rstrip("/")
        return
    proc, url = start_mock(HERE / "mock_server.py", what="mock server")       # a free port of its own: never another run's or session's server
    yield url
    proc.terminate()


@pytest.fixture(scope="session")
def noauth_url():
    """a server with logins disabled (the mock started with MOCK_AUTH=disabled, or UI_NOAUTH_BASE_URL for a real AUTH_DISABLED=1 server)"""
    env = os.environ.get("UI_NOAUTH_BASE_URL")
    if env:
        yield env.rstrip("/")
        return
    if not IS_MOCK:
        pytest.skip("set UI_NOAUTH_BASE_URL to test an AUTH_DISABLED=1 server")
    proc, url = start_mock(HERE / "mock_server.py", env={"MOCK_AUTH": "disabled"}, what="no-auth mock server")
    yield url
    proc.terminate()


@pytest.fixture(scope="module")      # one Playwright per process at a time: a session-wide one blocks every later module that starts its own
def browser():
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        yield b
        b.close()


@pytest.fixture
def ctx(browser, base_url):
    c = browser.new_context(viewport={"width": 1440, "height": 900}, base_url=base_url)
    # the ticket drawer embeds a Google Drive preview iframe for the fixture's fake file ids; the real drive.google.com answers 404 for them, but only if the
    # request finishes before the test ends, so a slow or fast machine decided whether the console-error check at teardown failed. The test never talks to Google.
    c.route("https://drive.google.com/**", lambda route: route.fulfill(status=200, content_type="text/html", body="<!doctype html><title>preview</title>"))
    yield c
    c.close()


@pytest.fixture
def anon_page(ctx, request):
    """a page with console-error collection and no session"""
    page = ctx.new_page()
    page.errors = []
    page.on("console", lambda m: page.errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: page.errors.append("pageerror: " + str(e)))
    page.on("response", lambda r: page.errors.append(f"[{r.status} {r.request.method} {r.url}]") if r.status == 404 else None)      # names the URL behind a bare "Failed to load resource"
    yield page
    if request.node.get_closest_marker("allow_errors") is None:
        allow = os.environ.get("UI_ALLOW_RESOURCE_ERRORS") == "1"
        errs = [e for e in page.errors if not (allow and "Failed to load resource" in e)]
        assert not errs, f"console errors: {errs}"


@pytest.fixture
def page(ctx, anon_page, base_url):
    r = ctx.request.post(base_url + "/api/login", data={"username": USERNAME, "password": PASSWORD})
    assert r.status == 204, f"login failed: {r.status}"
    return anon_page


@pytest.fixture
def noauth_page(browser, noauth_url):
    """a visitor with no cookie on a server with logins disabled; any console error fails the test"""
    c = browser.new_context(viewport={"width": 1440, "height": 900}, base_url=noauth_url)
    page = c.new_page()
    page.errors = []
    page.on("console", lambda m: page.errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: page.errors.append("pageerror: " + str(e)))
    page.ctx = c
    yield page
    errs = page.errors
    c.close()
    assert not errs, f"console errors: {errs}"
