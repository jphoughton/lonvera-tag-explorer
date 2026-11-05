"""Trends (/trends) in the browser, against the mock server (tests/ui/mock_trends.py: the real payload builder over made-up days).

Mock facts used below: data 2025-01-01 .. 2026-11-04, "today" = Thu 2026-11-05 Pacific, so Nov 4 is the last loaded day: the open week W45 (Mon Nov 2 - Wed Nov 4,
3 of 7 days) is compared with Oct 26-28, the open month Nov (4 days) with Oct 1-4, and the Day view starts with a not-loaded placeholder for today.
"""
import re
from urllib.parse import parse_qs, urlparse

import pytest

from conftest import SHOTS

pytestmark = pytest.mark.mock_only

TABLE = "[data-testid=trn-table]"


def idle(page):
    page.wait_for_function("() => !document.querySelector('#view').hasAttribute('aria-busy')")


def pressed(page, testid):
    page.wait_for_function("(id) => { const e = document.querySelector('[data-testid=' + id + ']'); return !!e && e.getAttribute('aria-pressed') === 'true'; }", arg=testid)
    idle(page)


def goto(page, qs=""):
    page.goto("/trends" + qs)
    page.wait_for_selector("[data-testid=trn-page]")
    idle(page)
    page.wait_for_selector(TABLE + " tbody tr[data-key]")


def params(page):
    return {k: v[0] for k, v in parse_qs(urlparse(page.url).query).items()}


def rows(page):
    return page.evaluate("""() => [...document.querySelectorAll('[data-testid=trn-table] tbody tr[data-key]')].map(tr => ({
        key: tr.dataset.key, cls: tr.className, cells: [...tr.children].map(td => td.innerText.trim()) }))""")


def heads(page):
    return page.evaluate("() => [...document.querySelectorAll('[data-testid=trn-table] thead th')].map(th => th.dataset.col || '')")


def test_nav_link_and_default_week_view(page):
    page.goto("/promotions")
    page.wait_for_selector("[data-testid=nav-trends]")
    page.locator("[data-testid=nav-trends]").click()
    page.wait_for_selector("[data-testid=trn-page]")
    idle(page)
    assert page.url.endswith("/trends")
    page.wait_for_selector(TABLE + " tbody tr[data-key]")
    r = rows(page)
    assert len(r) == 12
    assert page.locator("[data-testid=trn-interval-week]").get_attribute("aria-pressed") == "true"
    assert page.locator("#controls").is_hidden()                       # the global range bar does not apply
    first = r[0]
    assert first["cells"][0].startswith("W45") and "so far" in first["cells"][0] and "3 of 7 days" in first["cells"][0]
    assert "Oct 26 – Oct 28" in first["cells"][0] and "same 3 days" in first["cells"][0]
    assert "trn-open" in first["cls"]
    assert r[1]["cells"][0].startswith("W44") and "so far" not in r[1]["cells"][0] and "vs Oct 19 – Oct 25" in r[1]["cells"][0]
    h = heads(page)
    assert h[:5] == ["period", "total_spend", "total_spend:d", "meta_spend", "meta_spend:d"]


def test_toggle_day_week_month_changes_the_rows_and_url(page):
    goto(page)
    page.locator("[data-testid=trn-interval-day]").click()
    page.wait_for_function("() => document.querySelectorAll('[data-testid=trn-table] tbody tr[data-key]').length === 15")
    idle(page)
    assert params(page).get("interval") == "day"
    d = rows(page)
    assert "not loaded yet" in d[0]["cells"][0] and d[0]["cells"][1] == "–" and "trn-pending" in d[0]["cls"]       # today: nothing loaded, no misleading change
    assert d[1]["cells"][0].startswith("Wed Nov 4") and "vs Nov 3" in d[1]["cells"][0]
    assert re.search(r"[▲▼]\d", d[1]["cells"][2]), d[1]["cells"][:4]                                               # yesterday is closed: it has a change
    page.locator("[data-testid=trn-interval-month]").click()
    pressed(page, "trn-interval-month")
    page.wait_for_function("() => document.querySelectorAll('[data-testid=trn-table] tbody tr[data-key]').length === 12")
    m = rows(page)
    assert m[0]["cells"][0].startswith("Nov 2026") and "so far" in m[0]["cells"][0] and "4 of 30 days" in m[0]["cells"][0] and "vs Oct 1 – Oct 4" in m[0]["cells"][0]
    assert m[1]["cells"][0].startswith("Oct 2026") and "so far" not in m[1]["cells"][0]
    page.locator("[data-testid=trn-interval-week]").click()
    pressed(page, "trn-interval-week")
    assert "interval" not in params(page)                              # week is the default: a clean URL


def test_periods_control_changes_the_span(page):
    goto(page, "?interval=week")
    page.locator("[data-testid=trn-periods]").fill("5")
    page.locator("[data-testid=trn-periods]").press("Enter")
    page.wait_for_function("() => document.querySelectorAll('[data-testid=trn-table] tbody tr[data-key]').length === 5")
    assert params(page)["periods"] == "5"
    page.goto("/trends?interval=month&periods=3")
    page.wait_for_selector(TABLE + " tbody tr[data-key]")
    assert len(rows(page)) == 3


def test_change_colours_follow_polarity(page):
    goto(page, "?groups=totals")
    # spend changes are neutral (grey), CPA down is good, ROAS up is good
    res = page.evaluate("""() => {
        const out = {};
        for (const tr of document.querySelectorAll('[data-testid=trn-table] tbody tr[data-key]')) {
          for (const k of ['total_spend', 'nc_cpa', 'nc_roas']) {
            const i = [...document.querySelectorAll('[data-testid=trn-table] thead th')].findIndex(th => th.dataset.col === k + ':d');
            const el = tr.children[i].querySelector('.trn-chg');
            if (el) (out[k] = out[k] || []).push([el.dataset.dir, el.className.replace('trn-chg ', '')]);
          }
        }
        return out;
    }""")
    assert res["total_spend"] and all(c == "trn-flat" for _, c in res["total_spend"])
    for d, c in res["nc_cpa"]:
        assert (c == "trn-good") == (d == "down") or c == "trn-flat", (d, c)
    for d, c in res["nc_roas"]:
        assert (c == "trn-good") == (d == "up") or c == "trn-flat", (d, c)
    assert any(c == "trn-bad" for _, c in res["nc_roas"]) and any(c == "trn-good" for _, c in res["nc_roas"])


def test_column_groups_and_chart_line_pick(page):
    goto(page)
    assert page.locator("[data-testid=trn-group-totals]").get_attribute("aria-pressed") == "true"
    assert page.locator("[data-testid=trn-group-meta]").get_attribute("aria-pressed") == "false"
    page.locator("[data-testid=trn-group-meta]").click()
    pressed(page, "trn-group-meta")
    h = heads(page)
    for k in ("m_impr", "m_cpm", "m_ctr", "m_cpc", "m_roas", "reach", "frequency", "cost_1k", "uoc"):
        assert k in h, k
    assert params(page)["groups"] == "totals,meta"
    page.locator("[data-testid=trn-group-vitabull]").click()
    pressed(page, "trn-group-vitabull")
    assert "p_vitabull_nc_roas" in heads(page)
    # the last group cannot be switched off
    page.goto("/trends?groups=stage")
    page.wait_for_selector(TABLE + " tbody tr[data-key]")
    page.locator("[data-testid=trn-group-stage]").click()
    assert "test_share" in heads(page)
    # clicking a column heading draws that metric as the chart line
    page.goto("/trends")
    page.wait_for_selector(TABLE + " tbody tr[data-key]")
    assert "NC ROAS" in page.locator("[data-testid=trn-chart-note]").inner_text()
    page.locator("[data-testid=trn-pick-nc_cpa]").click()
    pressed(page, "trn-pick-nc_cpa")
    assert params(page)["line"] == "nc_cpa"
    assert "NC CPA" in page.locator("[data-testid=trn-chart-note]").inner_text()
    line_label = page.evaluate("() => Chart.getChart(document.getElementById('trn-chart')).data.datasets[1].label")
    assert line_label == "NC CPA"
    nbars = page.evaluate("() => Chart.getChart(document.getElementById('trn-chart')).data.labels.length")
    assert nbars == 12


def test_numbers_basis_switch_changes_meta_and_google_columns(page):
    goto(page, "?groups=meta,google")
    h = heads(page)
    i = h.index("m_roas")
    own = rows(page)[1]["cells"][i]
    assert re.fullmatch(r"\d\.\d\d", own), own
    page.locator("[data-testid=trn-numbers-nc]").click()
    pressed(page, "trn-numbers-nc")
    assert params(page)["numbers"] == "nc"
    page.wait_for_selector("[data-testid=trn-pick-m_roas]")
    assert "NC ROAS (Upstack)" in page.locator("[data-testid=trn-pick-m_roas]").inner_text()
    assert rows(page)[1]["cells"][i] != own
    assert "Own" not in page.locator("[data-testid=trn-pick-m_roas]").inner_text()


def test_missing_source_days_are_dashes_with_a_reason(page):
    goto(page, "?interval=month&periods=24&groups=meta")
    r = rows(page)
    h = heads(page)
    i = h.index("m_roas")
    oldest = r[-1]
    assert oldest["cells"][0].startswith("Dec 2024") or oldest["cells"][0].startswith("Jan 2025") or oldest["cells"][i] in ("–", "")
    page.goto("/trends?interval=month&periods=24&numbers=nc&groups=meta")
    page.wait_for_selector(TABLE + " tbody tr[data-key]")
    r = rows(page)
    h = heads(page)
    i = h.index("m_roas")
    assert r[-1]["cells"][i] == "–" and r[0]["cells"][i] != "–"        # Upstack attribution of Meta only starts 2026-05-06
    tip = page.evaluate("(i) => document.querySelector('[data-testid=trn-table] tbody tr:last-child').children[i].title", i)
    assert "starts" in tip


def test_reach_has_no_change_while_the_period_is_open(page):
    goto(page, "?groups=meta")
    h = heads(page)
    i = h.index("reach")
    r = rows(page)
    assert re.search(r"\d", r[0]["cells"][i]) and r[0]["cells"][i + 1] == "–"        # open week: value from the feed row, no change
    assert re.search(r"[▲▼]", r[1]["cells"][i + 1])                                    # closed week: change vs the previous week's own row
    tip = page.evaluate("(i) => document.querySelector('[data-testid=trn-table] tbody tr:first-child').children[i + 1].title", i)
    assert "like-for-like" in tip.lower() or "same days" in tip.lower()


def test_freshness_chips_and_help(page):
    goto(page)
    chips = page.locator("[data-testid=trn-fresh] .badge")
    assert chips.count() == 9
    assert chips.first.inner_text().startswith("Meta") and "Nov 4" in chips.first.inner_text()
    assert page.locator("[data-testid=trn-fresh] [data-stale=true]").count() == 0
    assert "so far" in page.locator("[data-testid=trn-help-short]").inner_text()
    page.locator("[data-testid=trn-more] summary").click()
    assert "ISO week" in page.locator("[data-testid=trn-more]").inner_text()


def test_stale_feed_is_flagged(page):
    goto(page, "?as_of=2026-11-09")
    assert page.locator("[data-testid=trn-fresh] [data-stale=true]").count() >= 1


def test_open_period_and_placeholder_rows_when_the_week_starts(page):
    goto(page, "?as_of=2026-11-02&interval=week")
    r = rows(page)
    assert "not loaded yet" in r[0]["cells"][0] and r[0]["cells"][1] == "–"
    assert r[1]["cells"][0].startswith("W44") and "so far" not in r[1]["cells"][0]


@pytest.mark.allow_errors
def test_error_box_with_retry(page):
    page.route("**/api/trends*", lambda route: route.fulfill(status=503, content_type="application/json", body='{"error":"query timed out"}'))
    page.goto("/trends")
    box = page.locator(".errorbox")
    box.wait_for()
    assert "query timed out" in box.inner_text()
    page.unroute("**/api/trends*")
    box.get_by_role("button", name="Retry").click()
    page.wait_for_selector(TABLE + " tbody tr[data-key]")
    assert len(rows(page)) == 12


@pytest.mark.allow_errors
def test_slow_answer_ends_in_the_error_box(page):
    def slow(route):
        import time
        time.sleep(1.5)
        route.continue_()
    page.route("**/api/trends*", slow)
    page.goto("/trends?trn_timeout_ms=300")
    page.locator(".errorbox").wait_for()
    assert "took longer than" in page.locator(".errorbox").inner_text()


def test_screenshots_desktop(page):
    goto(page, "?groups=totals,meta")
    page.screenshot(path=str(SHOTS / "trends_01_week_desktop.png"), full_page=True)
    goto(page, "?interval=day&groups=totals,vitabull")
    page.screenshot(path=str(SHOTS / "trends_02_day_desktop.png"), full_page=True)
    goto(page, "?interval=month&groups=totals,stage")
    page.screenshot(path=str(SHOTS / "trends_03_month_desktop.png"), full_page=True)


def test_phone_width_has_no_sideways_scroll(browser, base_url):
    c = browser.new_context(viewport={"width": 375, "height": 812}, base_url=base_url)
    try:
        assert c.request.post(base_url + "/api/login", data={"username": "JoshHoughton", "password": "test"}).status == 204
        pg = c.new_page()
        errs = []
        pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: errs.append(str(e)))
        for qs in ("", "?interval=day", "?interval=month&groups=totals,meta,google,vitabull,beefliver,boneflex,stage", "?groups=meta&numbers=nc"):
            pg.goto("/trends" + qs)
            pg.wait_for_selector(TABLE + " tbody tr[data-key]")
            idle(pg)
            w = pg.evaluate("[document.documentElement.scrollWidth, document.documentElement.clientWidth, document.body.scrollWidth]")
            assert w[0] <= w[1] and w[2] <= w[1], (qs, w)
            over = pg.evaluate("""() => [...document.querySelectorAll('#view *')].filter(e => { const r = e.getBoundingClientRect();
                return r.width > 0 && r.right > document.documentElement.clientWidth + 1 && !e.closest('.tablewrap'); }).map(e => e.className || e.tagName).slice(0, 5)""")
            assert over == [], (qs, over)
        pg.goto("/trends?groups=totals,meta")
        pg.wait_for_selector(TABLE + " tbody tr[data-key]")
        idle(pg)
        pg.screenshot(path=str(SHOTS / "trends_04_week_phone.png"), full_page=True)
        pg.goto("/trends?interval=day")
        pg.wait_for_selector(TABLE + " tbody tr[data-key]")
        idle(pg)
        pg.screenshot(path=str(SHOTS / "trends_05_day_phone.png"), full_page=True)
        assert errs == []
    finally:
        c.close()


def test_no_class_or_id_an_ad_blocker_would_hide(page):
    goto(page, "?groups=totals,meta,google,vitabull,stage")
    bad = page.evaluate("""() => [...document.querySelectorAll('#view *')].flatMap(e => [...e.classList, e.id].filter(Boolean))
        .filter(t => /^(ads?|ad-.*|ads-.*|adname|advert.*|sponsor.*)$/.test(t) || /promo|banner|sponsor|advert/i.test(t))""")
    assert bad == []
