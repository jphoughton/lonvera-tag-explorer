"""Daily story (/daily) in the browser, against the mock server (tests/ui/mock_daily.py: the real facts builder over a made-up panel, newest day Wed 2026-11-11)."""
import re
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.sync_api import expect

from conftest import SHOTS

pytestmark = pytest.mark.mock_only


def idle(page):
    page.wait_for_function("() => !document.querySelector('#view').hasAttribute('aria-busy')")


def goto(page, qs=""):
    page.goto("/daily" + qs)
    page.wait_for_selector("[data-testid=dly-page]")
    idle(page)


def test_default_shows_the_newest_day_tldr_first_and_the_tables(page):
    goto(page)
    expect(page.locator("h1")).to_have_text("Daily story")
    expect(page.locator("#subnav a.navtab[aria-current=page]")).to_have_text("Daily story")
    assert "Wed Nov 11, 2026" in page.locator("[data-testid=dly-day]").inner_text()
    tldr = page.locator("[data-testid=dly-tldr]").inner_text()
    assert tldr.startswith("TLDR:") and "NC ROAS" in tldr
    assert "Template" in page.locator("[data-testid=dly-source]").inner_text()
    # headline row and the date-window columns
    heads = page.locator("[data-testid=dly-metrics] thead th").all_inner_texts()
    assert heads[:6] == ["Metric", "Day", "Same weekday, median of previous 4 weeks", "Change", "Previous 7 days", "z-score"]
    roas = float(page.locator("[data-testid=dly-m-nc_roas]").inner_text())
    assert 0.5 < roas < 1.5
    # the decomposition ends with a total row equal to the sum of the terms
    pts = [float(x) for x in page.locator("[data-testid=dly-decomp] tbody tr:not(.dly-sumrow) td:nth-child(4)").all_inner_texts()]
    total = float(page.locator("[data-testid=dly-term-total]").inner_text())
    assert abs(sum(pts) - total) < 0.15 * len(pts)                               # each term is rounded to 0.1 points
    # cuts, ledger, sources, scoreboard
    for tid in ("dly-cuttable-product", "dly-cuttable-structure", "dly-cuttable-cluster", "dly-ledger", "dly-klaviyo", "dly-google", "dly-amazon", "dly-scoreboard", "dly-movers"):
        assert page.locator(f"[data-testid={tid}]").count() == 1, tid
    assert "Hair Thinning" in page.locator("[data-testid=dly-cuttable-cluster]").inner_text()
    assert "Scaling / tROAS / 7-day click" in page.locator("[data-testid=dly-cuttable-structure]").inner_text()
    assert "correlation, not cause" in page.locator("[data-testid=dly-score-note]").inner_text().lower()
    assert page.locator("[data-testid=dly-cannot] li").count() >= 3
    page.screenshot(path=str(SHOTS / "daily_desktop.png"), full_page=True)


def test_date_picker_and_previous_day(page):
    goto(page)
    page.locator("[data-testid=dly-prev]").click()
    page.wait_for_function("() => location.search.includes('day=2026-11-10')")
    idle(page)
    assert "Tue Nov 10, 2026" in page.locator("[data-testid=dly-day]").inner_text()
    page.locator("[data-testid=dly-date]").fill("2026-11-04")
    page.wait_for_function("() => location.search.includes('day=2026-11-04')")
    idle(page)
    assert "Wed Nov 4, 2026" in page.locator("[data-testid=dly-day]").inner_text()
    page.locator("[data-testid=dly-next]").click()
    idle(page)
    assert "Thu Nov 5, 2026" in page.locator("[data-testid=dly-day]").inner_text()


@pytest.mark.allow_errors
def test_unknown_day_shows_the_error_box_with_retry(page):
    page.goto("/daily?day=2026-01-01")
    page.wait_for_selector("text=no daily story for 2026-01-01")


def test_phone_width_never_scrolls_sideways(browser, base_url):
    from conftest import PASSWORD, USERNAME
    c = browser.new_context(viewport={"width": 375, "height": 812}, base_url=base_url)
    r = c.request.post(base_url + "/api/login", data={"username": USERNAME, "password": PASSWORD})
    assert r.status == 204
    p = c.new_page()
    errs = []
    p.on("pageerror", lambda e: errs.append(str(e)))
    p.goto("/daily")
    p.wait_for_selector("[data-testid=dly-page]")
    p.wait_for_function("() => !document.querySelector('#view').hasAttribute('aria-busy')")
    assert p.evaluate("() => document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    assert p.evaluate("() => document.body.scrollWidth <= window.innerWidth")
    p.screenshot(path=str(SHOTS / "daily_375.png"), full_page=True)
    c.close()
    assert not errs
