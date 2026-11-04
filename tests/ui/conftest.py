import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
SHOTS = HERE / "shots"
SHOTS.mkdir(exist_ok=True)
IS_MOCK = "UI_BASE_URL" not in os.environ
PASSWORD = os.environ.get("UI_PASSWORD", "test")


def pytest_configure(config):
    config.addinivalue_line("markers", "allow_errors: do not fail on console errors (e.g. deliberate 401s)")
    config.addinivalue_line("markers", "mock_only: needs the mock server's canned data")


def pytest_collection_modifyitems(config, items):
    if not IS_MOCK:
        skip = pytest.mark.skip(reason="mock-only test (UI_BASE_URL is set)")
        for it in items:
            if "mock_only" in it.keywords:
                it.add_marker(skip)


def _up(url):
    try:
        with urllib.request.urlopen(url + "/healthz", timeout=1) as r:
            return r.status == 200
    except Exception:
        return False


@pytest.fixture(scope="session")
def base_url():
    env = os.environ.get("UI_BASE_URL")
    if env:
        yield env.rstrip("/")
        return
    port = 5099
    url = f"http://127.0.0.1:{port}"
    proc = None
    if not _up(url):
        proc = subprocess.Popen([sys.executable, str(HERE / "mock_server.py"), str(port)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(60):
            if _up(url):
                break
            time.sleep(0.25)
        else:
            proc.terminate()
            raise RuntimeError("mock server did not start")
    yield url
    if proc:
        proc.terminate()


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        yield b
        b.close()


@pytest.fixture
def ctx(browser, base_url):
    c = browser.new_context(viewport={"width": 1440, "height": 900}, base_url=base_url)
    yield c
    c.close()


@pytest.fixture
def anon_page(ctx, request):
    """a page with console-error collection and no session"""
    page = ctx.new_page()
    page.errors = []
    page.on("console", lambda m: page.errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: page.errors.append("pageerror: " + str(e)))
    yield page
    if request.node.get_closest_marker("allow_errors") is None:
        allow = os.environ.get("UI_ALLOW_RESOURCE_ERRORS") == "1"
        errs = [e for e in page.errors if not (allow and "Failed to load resource" in e)]
        assert not errs, f"console errors: {errs}"


@pytest.fixture
def page(ctx, anon_page, base_url):
    r = ctx.request.post(base_url + "/api/login", data={"password": PASSWORD})
    assert r.status == 204, f"login failed: {r.status}"
    return anon_page
