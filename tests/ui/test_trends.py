"""Trends (/trends) in the browser, against the mock server (tests/ui/mock_trends.py: the real payload builder over made-up days).

The table is laid out like the rollup tabs of the Lonvera Projections sheet: periods as rows, OLDEST FIRST with the open period last, labelled bands of columns, the
change against the previous period as one block at the right (the sheet's WoW / MoM block), a 'Change after every column' switch for per-metric change columns.

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
    """Column keys left to right: 'period', then one per column of the second header row (change-block columns are 'chg:<metric>', per-metric change columns '<metric>:d')."""
    return ["period"] + page.evaluate("() => [...document.querySelectorAll('[data-testid=trn-table] thead tr.trn-colrow th')].map(th => th.dataset.col || '')")


def bands(page):
    return page.evaluate("() => [...document.querySelectorAll('[data-testid=trn-table] thead tr.trn-bandrow th[data-band]')].map(th => [th.innerText.trim(), th.colSpan])")


def col(page, key):
    return heads(page).index(key)


def test_nav_link_and_default_week_view(page):
    page.goto("/promotions")
    page.locator("[data-testid=nav-section-performance]").click()                                   # Trends is a tab of the Performance section
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
    last = r[-1]                                                       # the sheet's order: oldest first, the open period last
    assert last["cells"][0].startswith("W45") and "so far" in last["cells"][0] and "3 of 7 days" in last["cells"][0] and "Nov 2 – Nov 8" in last["cells"][0]
    assert "trn-open" in last["cls"]
    assert r[0]["cells"][0].startswith("W34") and r[-2]["cells"][0].startswith("W44") and "so far" not in r[-2]["cells"][0]
    assert [x["key"] for x in r] == sorted(x["key"] for x in r)         # ascending by period start
    note = page.locator("[data-testid=trn-vs]").inner_text()
    assert "W45" in note and "the same 3 weekdays of last week" in note and "Oct 26 – Oct 28" in note
    assert "Oct 19 – Oct 25" in page.evaluate("() => document.querySelectorAll('[data-testid=trn-table] tbody tr[data-key]')[10].children[0].title")        # a closed week's own base, on hover
    h = heads(page)
    assert h[:6] == ["period", "total_rev", "nc_rev", "rc_rev", "nc_orders", "nc_aov"]
    assert h[-11:] == ["chg:total_rev", "chg:total_spend", "chg:nc_rev", "chg:rc_rev", "chg:nc_roas", "chg:nc_cpa", "chg:nc_orders", "chg:new_visitors", "chg:cost_nv", "chg:m_roas", "chg:g_roas"]
    assert [b[0] for b in bands(page)] == ["DTC / SHOPIFY", "META", "WoW Δ"]


def test_toggle_day_week_month_changes_the_rows_and_url(page):
    goto(page)
    page.locator("[data-testid=trn-interval-day]").click()
    page.wait_for_function("() => document.querySelectorAll('[data-testid=trn-table] tbody tr[data-key]').length === 15")
    idle(page)
    assert params(page).get("interval") == "day"
    d = rows(page)
    ci = col(page, "chg:total_rev")
    assert "not loaded yet" in d[-1]["cells"][0] and d[-1]["cells"][1] == "–" and "trn-pending" in d[-1]["cls"]       # today (last row): nothing loaded, no misleading change
    assert d[-1]["cells"][ci] == "–"
    assert d[-2]["cells"][0].startswith("Wed Nov 4") and "Against" in page.evaluate("() => document.querySelectorAll('[data-testid=trn-table] tbody tr[data-key]')[13].children[%d].title" % ci)
    assert re.fullmatch(r"[+-]\d+\.\d%|0\.0%", d[-2]["cells"][ci]), d[-2]["cells"][ci]                              # yesterday is closed: it has a change, signed with one decimal
    assert [b[0] for b in bands(page)][-1] == "DoD Δ"
    page.locator("[data-testid=trn-interval-month]").click()
    pressed(page, "trn-interval-month")
    page.wait_for_function("() => document.querySelectorAll('[data-testid=trn-table] tbody tr[data-key]').length === 12")
    m = rows(page)
    assert m[-1]["cells"][0].startswith("Nov 2026") and "so far" in m[-1]["cells"][0] and "4 of 30 days" in m[-1]["cells"][0]
    assert "Oct 1 – Oct 4" in page.locator("[data-testid=trn-vs]").inner_text()
    assert m[-2]["cells"][0].startswith("Oct 2026") and "so far" not in m[-2]["cells"][0]
    assert [b[0] for b in bands(page)][-1] == "MoM Δ"
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
    goto(page)
    # the change block: spend changes are neutral (grey), CPA down is good, ROAS up is good; a tinted cell is green or red
    res = page.evaluate("""() => {
        const out = {};
        const heads = [...document.querySelectorAll('[data-testid=trn-table] thead tr.trn-colrow th')].map(th => th.dataset.col);
        for (const tr of document.querySelectorAll('[data-testid=trn-table] tbody tr[data-key]')) {
          for (const k of ['total_spend', 'nc_cpa', 'nc_roas']) {
            const td = tr.children[heads.indexOf('chg:' + k) + 1];
            const el = td.querySelector('.trn-val');
            if (el && el.dataset.dir) (out[k] = out[k] || []).push([el.dataset.dir, td.className.includes('trn-good') ? 'good' : td.className.includes('trn-bad') ? 'bad' : 'plain', td.innerText.trim()]);
          }
        }
        return out;
    }""")
    assert res["total_spend"] and all(c == "plain" for _, c, _t in res["total_spend"])
    for d, c, _t in res["nc_cpa"]:
        assert (c == "good") == (d == "down") or d == "flat", (d, c)
    for d, c, _t in res["nc_roas"]:
        assert (c == "good") == (d == "up") or d == "flat", (d, c)
    assert any(c == "bad" for _, c, _t in res["nc_roas"]) and any(c == "good" for _, c, _t in res["nc_roas"])
    assert all(re.fullmatch(r"[+-]\d+\.\d%|0\.0%|–", t) for rr in res.values() for _, _c, t in rr)


def test_change_after_every_column_switch_is_off_by_default_and_adds_the_old_columns(page):
    goto(page)
    assert page.locator("[data-testid=trn-delta-off]").get_attribute("aria-pressed") == "true"
    assert not [k for k in heads(page) if k.endswith(":d")]
    page.locator("[data-testid=trn-delta-on]").click()
    pressed(page, "trn-delta-on")
    assert params(page)["delta"] == "1"
    h = heads(page)
    i = h.index("total_rev")
    assert h[i + 1] == "total_rev:d" and h[h.index("nc_roas") + 1] == "nc_roas:d" and "chg:nc_roas" in h        # the change block stays
    r = rows(page)
    assert re.search(r"[+-]\d+\.\d%|[+-]\d+\.\dpp", r[-2]["cells"][h.index("nc_cpa") + 1]), r[-2]["cells"][:6]
    tints = page.evaluate("""(i) => [...document.querySelectorAll('[data-testid=trn-table] tbody tr[data-key]')].flatMap(tr => [...tr.children[i].querySelectorAll('.trn-chg')].map(e => e.className.replace('trn-chg ', '')))""", h.index("nc_cpa") + 1)
    assert "trn-good" in tints and "trn-bad" in tints                                                          # polarity colours are kept
    page.locator("[data-testid=trn-delta-off]").click()
    pressed(page, "trn-delta-off")
    assert "delta" not in params(page)


def test_layout_is_the_sheets_bands_in_the_sheets_order_oldest_first(page):
    goto(page, "?groups=all")
    assert [b[0] for b in bands(page)] == ["DTC / SHOPIFY", "META", "SPEND", "NC REVENUE", "NC ROAS", "NC ORDERS", "NC CPA", "SPEND BY STAGE", "TESTING % OF SPEND",
                                           "NC (UPSTACK ANY-CLICK)", "NC CPA BY STAGE", "GOOGLE", "WoW Δ"]
    spans = dict(bands(page))
    assert spans["DTC / SHOPIFY"] == 13 and spans["SPEND"] == 4 and spans["TESTING % OF SPEND"] == 1 and spans["WoW Δ"] == 11
    labels = page.evaluate("() => [...document.querySelectorAll('[data-testid=trn-table] thead tr.trn-colrow th')].map(th => th.innerText.replace(/\\s+/g, ' ').trim())")
    assert labels[:5] == ["Total Revenue", "NC Revenue", "Repeat Revenue", "NC Orders", "NC AOV"]
    assert "Platform ROAS" in labels and "Purchase Value" in labels and "Cost per 1,000 Reached" in labels
    assert page.evaluate("() => [...document.querySelectorAll('[data-testid=trn-table] thead tr.trn-colrow th.trn-t-vb')].length") == 5       # the sheet's per-product tint on the VitaBull heads
    # the first column and the two header rows are frozen
    st = page.evaluate("""() => { const g = (el) => getComputedStyle(el).position;
        const t = document.querySelector('[data-testid=trn-table]');
        return [g(t.querySelector('thead tr.trn-bandrow th')), g(t.querySelector('thead tr.trn-colrow th')), g(t.querySelector('thead th.trn-corner')), g(t.querySelector('tbody td.trn-pcol'))]; }""")
    assert st == ["sticky"] * 4
    box = page.evaluate("() => { const b = document.querySelector('[data-testid=trn-table]'); b.scrollLeft = 600; return [b.scrollLeft, b.scrollWidth > b.clientWidth]; }")
    assert box[1] and box[0] > 0
    left = page.evaluate("() => document.querySelector('[data-testid=trn-table] tbody td.trn-pcol').getBoundingClientRect().left - document.querySelector('[data-testid=trn-table]').getBoundingClientRect().left")
    assert left < 3                                                                                           # the period column stays at the left edge while the bands scroll
    w = page.evaluate("[document.documentElement.scrollWidth, document.documentElement.clientWidth]")
    assert w[0] <= w[1]                                                                                       # the page itself does not scroll sideways, only the table box


def test_plan_block_on_the_month_grain(page):
    goto(page, "?interval=month")
    assert [b[0] for b in bands(page)] == ["Projected* | Target | % Diff", "DTC / SHOPIFY", "META", "MoM Δ"]
    assert dict(bands(page))["Projected* | Target | % Diff"] == 17
    h = heads(page)
    assert h[1:4] == ["plan_total_rev_proj", "plan_total_rev_goal", "plan_total_rev_diff"] and h[16:18] == ["plan_ebitda_gap", "plan_noi_gap"]
    labels = page.evaluate("() => [...document.querySelectorAll('[data-testid=trn-table] thead tr.trn-colrow th')].slice(0, 5).map(th => th.innerText.replace(/\\s+/g, ' ').trim())")
    assert labels == ["Total Rev Projected*", "Total Rev Target", "Total Rev % Diff", "Total Spend Projected*", "Total Spend Target"]
    r = rows(page)
    c = r[-1]["cells"]
    assert re.fullmatch(r"\$[\d,]+", c[1]) and re.fullmatch(r"\$[\d,]+", c[2]) and re.fullmatch(r"[+-]\d+\.\d%", c[3]), c[:4]
    assert re.fullmatch(r"[+-]\$[\d,]+", c[16]), c[16]
    assert "Same figures as the Pacing page" in page.locator("[data-testid=trn-plan-note]").inner_text()
    tinted = page.evaluate("() => [...document.querySelectorAll('[data-testid=trn-table] tbody td.trn-tint')].length")
    assert tinted > 12
    page.goto("/trends?interval=week")                                                                         # the plan block is a month-grain thing
    page.wait_for_selector(TABLE + " tbody tr[data-key]")
    assert not [k for k in heads(page) if k.startswith("plan_")] and page.locator("[data-testid=trn-group-plan]").count() == 0


@pytest.mark.allow_errors
def test_plan_block_failure_shows_dashes_with_the_reason_and_the_table_still_renders(page):
    page.route("**/api/trends/plan*", lambda route: route.fulfill(status=503, content_type="application/json", body='{"error":"upstream_degraded"}'))
    goto(page, "?interval=month")
    r = rows(page)
    assert len(r) == 12 and all(x == "–" for x in r[-1]["cells"][1:18]) and re.search(r"\d", r[-1]["cells"][18])
    assert "Plan columns are empty" in page.locator("[data-testid=trn-plan-note]").inner_text()
    assert "could not be loaded" in page.evaluate("() => document.querySelector('[data-testid=trn-table] tbody tr:last-child').children[1].title")


@pytest.mark.parametrize("interval,numbers", [("week", "own"), ("day", "nc"), ("month", "own")])
def test_rendered_cells_equal_the_values_from_before_the_layout_rebuild(page, interval, numbers):
    """Every value cell on screen equals the pre-rebuild payload value of the same period and metric (tests/fixtures/trends_golden_before_layout.json; the mock has fixed days)."""
    import json
    from pathlib import Path
    golden = json.loads((Path(__file__).resolve().parents[1] / "fixtures" / "trends_golden_before_layout.json").read_text())[f"{interval}:{numbers}"]
    goto(page, f"?interval={interval}&numbers={numbers}&periods=6&groups=all")
    h = heads(page)
    got = page.evaluate("""() => [...document.querySelectorAll('[data-testid=trn-table] tbody tr[data-key]')].map(tr => [tr.dataset.key, [...tr.children].map(td => { const e = td.querySelector('.trn-val'); return e ? e.dataset.v : null; })])""")
    checked = 0
    for key, vs in got:
        assert key in golden, key
        for name, v in zip(h, vs):
            if name == "nc_cvr":                                    # intentionally redefined 2026-11-09 (new customers / new visitors): the old click-based value is nc_cvr_clicks
                continue
            gname = "nc_cvr" if name == "nc_cvr_clicks" else name
            if name == "period" or name.startswith(("plan_", "chg:")) or name.endswith(":d") or gname not in golden[key]["v"]:
                continue
            exp = golden[key]["v"][gname]
            assert (float(v) if v else None) == exp, (key, name, v, exp)
            checked += 1
    assert checked > 300, checked


def test_band_chips_show_and_hide_bands_and_chart_line_pick(page):
    goto(page)
    assert page.locator("[data-testid=trn-group-totals]").get_attribute("aria-pressed") == "true"
    assert page.locator("[data-testid=trn-group-change]").get_attribute("aria-pressed") == "true"
    assert page.locator("[data-testid=trn-group-meta]").get_attribute("aria-pressed") == "true"                    # Meta is on by default (Josh 2026-11-09)
    h = heads(page)
    for k in ("m_impr", "m_cpm", "m_ctr", "m_cpc", "m_roas", "reach", "frequency", "cost_1k", "uoc", "meta_spend"):
        assert k in h, k
    assert h.index("nc_roas") < h.index("meta_spend") < h.index("chg:total_rev")                                 # the sheet's order: DTC, Meta, ..., the change block last
    page.locator("[data-testid=trn-group-meta]").click()                                                          # switching it off hides the Meta columns
    page.wait_for_function("() => { const e = document.querySelector('[data-testid=trn-group-meta]'); return !!e && e.getAttribute('aria-pressed') === 'false'; }")
    assert "meta_spend" not in heads(page) and params(page)["groups"] == "totals,change"
    page.locator("[data-testid=trn-group-meta]").click()
    pressed(page, "trn-group-meta")
    page.locator("[data-testid=trn-group-products]").click()
    pressed(page, "trn-group-products")
    h = heads(page)
    assert "p_vitabull_nc_roas" in h and "p_boneflex_nc_cpa" in h and "meta_nc_roas" in h
    page.locator("[data-testid=trn-group-all]").click()
    pressed(page, "trn-group-all")
    assert page.locator("[data-testid=trn-group-stage]").get_attribute("aria-pressed") == "true" and "test_share" in heads(page)
    page.locator("[data-testid=trn-group-all]").click()                                                           # All again = back to the defaults, a clean URL
    page.wait_for_function("() => !location.search.includes('groups')")
    assert "groups" not in params(page)
    # an old bookmark with a single product still works (it is the By product band now)
    page.goto("/trends?groups=vitabull")
    page.wait_for_selector(TABLE + " tbody tr[data-key]")
    assert "p_vitabull_nc_roas" in heads(page) and page.locator("[data-testid=trn-group-products]").get_attribute("aria-pressed") == "true"
    # the last band cannot be switched off
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
    own = rows(page)[-2]["cells"][i]
    assert re.fullmatch(r"\d\.\d\dx", own), own
    assert "Platform ROAS" in page.locator("[data-testid=trn-pick-m_roas]").inner_text()
    page.locator("[data-testid=trn-numbers-nc]").click()
    pressed(page, "trn-numbers-nc")
    assert params(page)["numbers"] == "nc"
    page.wait_for_selector("[data-testid=trn-pick-m_roas]")
    assert "NC ROAS (Upstack)" in page.locator("[data-testid=trn-pick-m_roas]").inner_text()
    assert rows(page)[-2]["cells"][i] != own
    assert "Own" not in page.locator("[data-testid=trn-pick-m_roas]").inner_text()


def test_missing_source_days_are_dashes_with_a_reason(page):
    goto(page, "?interval=month&periods=24&groups=meta")
    r = rows(page)
    h = heads(page)
    i = h.index("m_roas")
    oldest = r[0]
    assert oldest["cells"][0].startswith("Dec 2024") or oldest["cells"][0].startswith("Jan 2025") or oldest["cells"][i] in ("–", "")
    page.goto("/trends?interval=month&periods=24&numbers=nc&groups=meta")
    page.wait_for_selector(TABLE + " tbody tr[data-key]")
    r = rows(page)
    h = heads(page)
    i = h.index("m_roas")
    assert r[0]["cells"][i] == "–" and r[-1]["cells"][i] != "–"        # Upstack attribution of Meta only starts 2026-05-06
    tip = page.evaluate("(i) => document.querySelector('[data-testid=trn-table] tbody tr:first-child').children[i].title", i)
    assert "starts" in tip


def test_reach_has_no_change_while_the_period_is_open(page):
    goto(page, "?groups=meta&delta=1")
    h = heads(page)
    i = h.index("reach")
    r = rows(page)
    assert h[i + 1] == "reach:d"
    assert re.search(r"\d", r[-1]["cells"][i]) and r[-1]["cells"][i + 1] == "–"        # open week (last row): value from the feed row, no change
    assert re.search(r"[+-]\d", r[-2]["cells"][i + 1])                                    # closed week: change vs the previous week's own row
    tip = page.evaluate("(i) => document.querySelector('[data-testid=trn-table] tbody tr:last-child').children[i + 1].title", i)
    assert "like-for-like" in tip.lower() or "same days" in tip.lower()


def test_freshness_chips_and_help(page):
    goto(page)
    chips = page.locator("[data-testid=trn-fresh] .badge")
    assert chips.count() == 10
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
    assert "not loaded yet" in r[-1]["cells"][0] and r[-1]["cells"][1] == "–"
    assert r[-2]["cells"][0].startswith("W44") and "so far" not in r[-2]["cells"][0]


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
    goto(page, "?groups=totals,meta,change")
    page.screenshot(path=str(SHOTS / "trends_01_week_desktop.png"), full_page=True)
    goto(page, "?interval=day&groups=totals,products,change")
    page.screenshot(path=str(SHOTS / "trends_02_day_desktop.png"), full_page=True)
    goto(page, "?interval=month")
    page.screenshot(path=str(SHOTS / "trends_03_month_desktop.png"), full_page=True)
    goto(page, "?groups=all")
    page.evaluate("() => { document.querySelector('[data-testid=trn-table]').scrollLeft = 1500; }")
    page.screenshot(path=str(SHOTS / "trends_06_week_all_bands_scrolled.png"), full_page=True)
    goto(page, "?delta=1")
    page.screenshot(path=str(SHOTS / "trends_07_week_change_after_every_column.png"), full_page=True)


def test_phone_width_has_no_sideways_scroll(browser, base_url):
    c = browser.new_context(viewport={"width": 375, "height": 812}, base_url=base_url)
    try:
        assert c.request.post(base_url + "/api/login", data={"username": "JoshHoughton", "password": "test"}).status == 204
        pg = c.new_page()
        errs = []
        pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: errs.append(str(e)))
        for qs in ("", "?interval=day", "?interval=month&groups=all", "?groups=meta&numbers=nc&delta=1"):
            pg.goto("/trends" + qs)
            pg.wait_for_selector(TABLE + " tbody tr[data-key]")
            idle(pg)
            w = pg.evaluate("[document.documentElement.scrollWidth, document.documentElement.clientWidth, document.body.scrollWidth]")
            assert w[0] <= w[1] and w[2] <= w[1], (qs, w)
            over = pg.evaluate("""() => [...document.querySelectorAll('#view *')].filter(e => { const r = e.getBoundingClientRect();
                return r.width > 0 && r.right > document.documentElement.clientWidth + 1 && !e.closest('.tablewrap'); }).map(e => e.className || e.tagName).slice(0, 5)""")
            assert over == [], (qs, over)
        pg.goto("/trends?groups=totals,meta,change")
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
    goto(page, "?groups=all&delta=1")
    bad = page.evaluate("""() => [...document.querySelectorAll('#view *')].flatMap(e => [...e.classList, e.id].filter(Boolean))
        .filter(t => /^(ads?|ad-.*|ads-.*|adname|advert.*|sponsor.*)$/.test(t) || /promo|banner|sponsor|advert/i.test(t))""")
    assert bad == []


def test_new_visitors_and_cost_per_new_visitor_columns(page):
    """Both columns sit at the end of the DTC / SHOPIFY band (the sheet's position), with the sheet's labels, #,##0 and $0.00 formats, dashes with a reason before the
    traffic feed starts (2025-05-08), and a change in the change block that follows the polarity colours (more visitors good, higher cost bad)."""
    goto(page, "?interval=month&periods=24")
    h = heads(page)
    labels = page.evaluate("() => [...document.querySelectorAll('[data-testid=trn-table] thead tr.trn-colrow th')].map(th => th.innerText.replace(/\\s+/g, ' ').trim())")
    assert h.index("new_visitors") == h.index("repeat_share") + 1 and h.index("cost_nv") == h.index("new_visitors") + 1 and h.index("cost_nv") < h.index("meta_spend")
    assert labels[h.index("new_visitors") - 1] == "New Visitors" and labels[h.index("cost_nv") - 1] == "Cost per New Visitor"
    r = rows(page)
    nv_i, c_i = h.index("new_visitors"), h.index("cost_nv")
    assert r[0]["cells"][nv_i] == "–" and r[0]["cells"][c_i] == "–"                                      # Dec 2024: before the feed
    tip = page.evaluate("(i) => document.querySelector('[data-testid=trn-table] tbody tr:first-child').children[i].title", c_i)
    assert "starts May 8, 2025" in tip
    closed = r[-2]["cells"]
    assert re.fullmatch(r"\d{1,3}(,\d{3})*", closed[nv_i]), closed[nv_i]
    assert re.fullmatch(r"\$\d{1,3}(,\d{3})*\.\d\d", closed[c_i]), closed[c_i]
    for k in ("new_visitors", "cost_nv"):
        ch = r[-2]["cells"][h.index("chg:" + k)]
        assert re.fullmatch(r"[+-]\d+\.\d%|0\.0%", ch), (k, ch)
    # day and week grains render both too, and the open period carries the like-for-like 'so far' change
    for interval in ("day", "week"):
        goto(page, f"?interval={interval}")
        h2, r2 = heads(page), rows(page)
        done = [x for x in r2 if "trn-pending" not in x["cls"]][-1] if interval == "day" else r2[-2]
        assert re.fullmatch(r"\d{1,3}(,\d{3})*", done["cells"][h2.index("new_visitors")]) and done["cells"][h2.index("cost_nv")].startswith("$")
    assert "trn-open" in r2[-1]["cls"] and r2[-1]["cells"][h2.index("new_visitors")] != "–" and r2[-1]["cells"][h2.index("chg:new_visitors")] != "–"
    left_out = page.locator("[data-testid=trn-left-out]").inner_text()
    assert "new visitors" not in left_out.lower()


def test_nc_conversion_is_new_customers_over_new_visitors_with_its_label_format_and_hover(page):
    """NC Conversion (DTC band) = new customers / new visitors, shown as a percentage; a dash with the traffic-feed reason before 2025-05-08; the sheet's click-based figure is the
    last column of the Meta band as 'NC Conversion (clicks)'; the Definitions text says it differs from the sheet."""
    goto(page, "?interval=month&periods=24")
    h = heads(page)
    labels = page.evaluate("() => [...document.querySelectorAll('[data-testid=trn-table] thead tr.trn-colrow th')].map(th => th.innerText.replace(/\\s+/g, ' ').trim())")
    i, j = h.index("nc_cvr"), h.index("nc_cvr_clicks")
    assert labels[i - 1] == "NC Conversion" and labels[j - 1] == "NC Conversion (clicks)" and i < h.index("repeat_share") < h.index("new_visitors") < h.index("meta_spend") < j
    r = rows(page)
    assert r[1]["cells"][i] == "–"                                                    # Jan 2025: orders exist, the traffic feed does not yet
    tip = page.evaluate("(i) => document.querySelector('[data-testid=trn-table] tbody tr:nth-child(2)').children[i].title", i)
    assert "starts May 8, 2025" in tip
    closed = r[-2]["cells"]
    assert re.fullmatch(r"\d+\.\d+%", closed[i]) and re.fullmatch(r"\d+\.\d+%", closed[j]), (closed[i], closed[j])
    nc, nv_ = (float(closed[h.index(k)].replace(",", "")) for k in ("nc_orders", "new_visitors"))
    assert abs(float(closed[i][:-1]) - 100 * nc / nv_) < 0.06                          # the cell is NC Orders / New Visitors as printed
    assert closed[i] != closed[j]
    page.locator("[data-testid=trn-more] summary").click()
    txt = page.locator("[data-testid=trn-more]").inner_text()
    assert "new visitors" in txt and "differs from the sheet" in txt


def test_header_and_controls_use_the_shared_components(page):
    goto(page)
    assert page.locator("[data-testid=trn-head] h1").inner_text() == "Trends"
    assert page.locator("[data-testid=trn-head] .note-line").count() == 1
    assert page.title() == "Trends · Lonvera"
    assert page.locator("[data-testid=trn-controls] .ctl-row").count() == 2                     # one row for period and source, one for columns
    assert page.locator("[data-testid=trn-numbers] .ctl-name").inner_text().lower() == "numbers from"
    assert page.locator("[data-testid=trn-numbers] .segs .seg").count() == 3
    assert page.locator("[data-testid=trn-groups] .fchip").count() >= 3                          # bands are filter chips
    assert "—" not in page.locator("[data-testid=trn-page]").inner_text()


def test_change_after_every_column_matches_the_change_block_format(page):
    goto(page, "?delta=1")
    txt = page.evaluate("() => [...document.querySelectorAll('[data-testid=trn-table] .trn-chg')].map(e => e.textContent)")
    assert txt and all(re.fullmatch(r"[+-]\d+\.\d%|[+-]\d+\.\dpp|0\.0%|0\.0pp", t) for t in txt), txt[:5]
