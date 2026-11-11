"""Playwright fixtures for the projection UI. Starts tests/projection/ui/mock_server.py on a free port.

    cd ~/lonvera-tag-explorer-projection
    ~/lonvera-tag-explorer/.venv/bin/python -m pytest tests/projection/ui
"""
import json
import urllib.request
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from tests._servers import start_mock

HERE = Path(__file__).resolve().parent
SHOTS = HERE / "shots"
SHOTS.mkdir(exist_ok=True)


def pytest_configure(config):
    config.addinivalue_line("markers", "allow_errors: do not fail on console errors (deliberate 401 / 429 / 500 responses)")


@pytest.fixture(scope="session")
def base_url():
    proc, url = start_mock(HERE / "mock_server.py", what="mock server")        # a free port of its own, waited for until /healthz answers
    yield url
    proc.terminate()


def hook(base_url, name, body=None):
    req = urllib.request.Request(f"{base_url}/__mock/{name}", data=json.dumps(body or {}).encode(), headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read() or b"{}")


def mock_state(base_url):
    with urllib.request.urlopen(f"{base_url}/__mock/state") as r:
        return json.loads(r.read())


@pytest.fixture(scope="module")      # one Playwright per process at a time: a session-wide one blocks every later module that starts its own
def browser():
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        yield b
        b.close()


@pytest.fixture(autouse=True)
def _reset(base_url):
    hook(base_url, "reset")
    yield
    hook(base_url, "app_auth", {"on": False})


@pytest.fixture
def ctx(browser, base_url):
    c = browser.new_context(viewport={"width": 1440, "height": 900}, base_url=base_url, permissions=["clipboard-read", "clipboard-write"])
    # the first edit of a tab session on the plan of record asks "make a copy or edit it"; the older tests edit the plan directly, so they start with that answered
    c.add_init_script("try { sessionStorage.setItem('projection.por.edit', '1'); } catch (e) {}")
    yield c
    c.close()


@pytest.fixture
def fresh_page(browser, base_url, request):
    """A page whose tab session has NOT answered the plan-of-record question (for the tests of that question)."""
    c = browser.new_context(viewport={"width": 1440, "height": 900}, base_url=base_url)
    p = c.new_page()
    p.errors = []
    p.on("console", lambda m: p.errors.append(m.text) if m.type == "error" else None)
    p.on("pageerror", lambda e: p.errors.append("pageerror: " + str(e)))
    yield p
    c.close()
    if request.node.get_closest_marker("allow_errors") is None:
        assert not p.errors, f"console errors: {p.errors}"


@pytest.fixture
def page(ctx, request):
    p = ctx.new_page()
    p.errors = []
    p.on("console", lambda m: p.errors.append(m.text) if m.type == "error" else None)
    p.on("pageerror", lambda e: p.errors.append("pageerror: " + str(e)))
    yield p
    if request.node.get_closest_marker("allow_errors") is None:
        assert not p.errors, f"console errors: {p.errors}"


@pytest.fixture
def mobile_page(browser, base_url, request):
    c = browser.new_context(viewport={"width": 390, "height": 844}, base_url=base_url, is_mobile=True, has_touch=True)
    p = c.new_page()
    p.errors = []
    p.on("console", lambda m: p.errors.append(m.text) if m.type == "error" else None)
    p.on("pageerror", lambda e: p.errors.append("pageerror: " + str(e)))
    yield p
    c.close()
    assert not p.errors, f"console errors: {p.errors}"


def open_module(page, name="drivers", query="", wait_grid=True):
    page.goto(f"/projection/{name}{query}")
    if wait_grid:
        page.wait_for_selector(".pg-grid .pg-table")
        page.wait_for_function("document.body.dataset.pgState === 'ready'")


def cell(page, key, c):
    return page.locator(f'tr.pg-row[data-key="{key}"] td[data-c="{c}"]')
