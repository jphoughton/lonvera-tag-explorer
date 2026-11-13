"""Email (/email) in the browser, against the mock server (tests/ui/mock_email.py: the real payload builders over made-up Klaviyo days).

Mock facts: loaded days 2026-07-13 .. 2026-11-09 (a Monday), so the default 30 days are Oct 11 - Nov 9 compared with Sep 11 - Oct 10; the week of Nov 9 and the month
of November are partial; three flows (F1 Billing Reminder Flow, F2 Welcome Series, F3 Welcome Text Flow, a text message flow) and twelve campaigns (the newest, "Last Call Text
Campaign", sent Oct 25); flow message fm2 has no copy.
"""
import re
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.sync_api import expect

from conftest import SHOTS

pytestmark = pytest.mark.mock_only


def idle(page):
    page.wait_for_function("() => !document.querySelector('#view').hasAttribute('aria-busy')")


def goto(page, qs=""):
    page.goto("/email" + qs)
    page.wait_for_selector("[data-testid=eml-page]")
    idle(page)
    page.wait_for_selector("[data-testid=eml-flows-table], [data-testid=eml-campaigns-table]")


def params(page):
    return {k: v[0] for k, v in parse_qs(urlparse(page.url).query).items()}


def tile(page, key):
    return page.locator(f"[data-testid=eml-tile-{key}-value]").inner_text()


def api(page, path):
    return page.evaluate("async (u) => (await fetch(u, { credentials: 'include' })).json()", path)


def pressed(page, testid):
    page.wait_for_function("(id) => { const e = document.querySelector('[data-testid=' + id + ']'); return !!e && e.getAttribute('aria-pressed') === 'true'; }", arg=testid)
    idle(page)


def body_rows(page, table):
    return page.evaluate("""(t) => [...document.querySelectorAll(`[data-testid=${t}] tbody > tr[data-key]`)].map(tr => ({ key: tr.dataset.key, cells: [...tr.children].map(td => td.innerText.trim()) }))""", table)


def no_sideways_scroll(page):
    return page.evaluate("() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1")


def test_nav_page_renders_with_tiles_and_both_tables(page):
    page.goto("/trends")
    page.wait_for_selector("[data-testid=nav-email]")
    page.locator("[data-testid=nav-email]").click()
    page.wait_for_selector("[data-testid=eml-page]")
    idle(page)
    assert page.url.endswith("/email")
    expect(page.locator("#subnav a.navtab[aria-current=page]")).to_have_text("Email")
    assert page.title() == "Email · Lonvera"
    expect(page.locator("[data-testid=eml-head] h1")).to_have_text("Email")
    tiles = page.locator("[data-testid=eml-totals] .kpi")
    assert tiles.count() == 8
    assert [t.split("\n")[0] for t in tiles.all_inner_texts()] == ["SENDS", "DELIVERED", "OPEN RATE", "CLICK RATE", "ORDERS", "REVENUE", "REVENUE PER DELIVERED", "UNSUBSCRIBE RATE"]
    expect(page.locator("[data-testid=eml-window]")).to_contain_text("Oct 11 to Nov 9 (30 days), compared with Sep 11 to Oct 10")
    expect(page.locator("[data-testid=eml-flows-table] tbody tr[data-key]")).to_have_count(3)
    expect(page.locator("[data-testid=eml-campaigns-table] tbody tr[data-key]")).to_have_count(page.evaluate("async () => (await (await fetch('/api/email/campaigns?window=30d', { credentials: 'include' })).json()).rows.length"))
    # the revenue and open-rate caveats are on the page, in plain words
    note = page.locator("[data-testid=eml-notes]").inner_text()
    assert "Klaviyo-attributed" in note and "Apple Mail Privacy Protection" in note and "—" not in page.locator("body").inner_text()
    assert page.locator("[data-testid=eml-tile-revenue]").get_attribute("title").startswith("Klaviyo-attributed revenue")
    page.screenshot(path=str(SHOTS / "email_01_desktop.png"), full_page=True)


def test_tiles_tie_to_the_api_and_show_the_change(page):
    goto(page)
    d = api(page, "/api/email/summary?window=30d")
    assert tile(page, "sends") == f"{d['totals']['sends']:,}" and tile(page, "orders") == f"{d['totals']['orders']:,}"
    assert tile(page, "revenue") == "${:,}".format(round(d["totals"]["revenue"]))                  # no cents on a total
    assert tile(page, "rev_per_delivered") == "${:.2f}".format(d["totals"]["rev_per_delivered"])
    assert tile(page, "click_rate") == "{:.2f}%".format(d["totals"]["click_rate"] * 100)
    ch = page.locator("[data-testid=eml-change-revenue]")
    assert ch.get_attribute("data-dir") in ("up", "down", "flat") and "vs prior" in ch.inner_text()
    ur = d["change"]["unsub_rate"]
    assert "points" in page.locator("[data-testid=eml-change-unsub_rate]").inner_text() and ur["t"] == "pp"       # a change in a rate is in percentage points
    assert page.locator("[data-testid=eml-tile-open_rate]").inner_text().lower().count("approximate") == 1


def test_window_choice_and_no_prior_period_for_all_loaded_days(page):
    goto(page)
    before = tile(page, "sends")
    page.locator("[data-testid=eml-window-7d]").click()
    page.wait_for_function("() => new URLSearchParams(location.search).get('window') === '7d'")
    idle(page)
    pressed(page, "eml-window-7d")
    assert tile(page, "sends") != before
    expect(page.locator("[data-testid=eml-window]")).to_contain_text("Nov 3 to Nov 9 (7 days), compared with Oct 27 to Nov 2")
    page.locator("[data-testid=eml-window-90d]").click()
    page.wait_for_function("() => new URLSearchParams(location.search).get('window') === '90d'")
    idle(page)
    # 90 days needs 180 days of data and 120 are loaded: no comparison, said in words, and no change line on the tiles
    expect(page.locator("[data-testid=eml-window]")).to_contain_text("No earlier period to compare with")
    assert page.locator("[data-testid=eml-nochange]").count() == 8 and page.locator("[data-testid=eml-change-revenue]").count() == 0
    page.locator("[data-testid=eml-window-all]").click()
    page.wait_for_function("() => new URLSearchParams(location.search).get('window') === 'all'")
    idle(page)
    expect(page.locator("[data-testid=eml-window]")).to_contain_text("Jul 13 to Nov 9 (120 days)")
    page.locator("[data-testid=eml-window-30d]").click()
    page.wait_for_function("() => !new URLSearchParams(location.search).has('window')")           # 30 days is the default: a clean URL


def test_flows_campaigns_all_switch(page):
    goto(page)
    page.locator("[data-testid=eml-kind-flows]").click()
    page.wait_for_function("() => new URLSearchParams(location.search).get('kind') === 'flows'")
    idle(page)
    pressed(page, "eml-kind-flows")
    assert page.locator("[data-testid=eml-flows]").count() == 1 and page.locator("[data-testid=eml-campaigns]").count() == 0
    flows_sends = tile(page, "sends")
    assert flows_sends == f"{api(page, '/api/email/summary?window=30d&kind=flows')['totals']['sends']:,}"
    page.locator("[data-testid=eml-kind-campaigns]").click()
    page.wait_for_function("() => new URLSearchParams(location.search).get('kind') === 'campaigns'")
    idle(page)
    assert page.locator("[data-testid=eml-flows]").count() == 0 and page.locator("[data-testid=eml-campaigns]").count() == 1
    assert tile(page, "sends") != flows_sends
    expect(page.locator("[data-testid=eml-split]")).to_be_visible()
    assert page.locator("[data-testid=eml-split]").is_disabled()                              # splitting flows from campaigns needs All
    page.locator("[data-testid=eml-kind-all]").click()
    page.wait_for_function("() => !new URLSearchParams(location.search).has('kind')")
    idle(page)
    assert page.locator("[data-testid=eml-flows]").count() == 1 and page.locator("[data-testid=eml-campaigns]").count() == 1


def test_channel_switch_text_messages_have_no_open_rate(page):
    goto(page, "?channel=sms")
    pressed(page, "eml-channel-sms")
    assert tile(page, "open_rate") == "–"                                                           # a dash, not 0.00%
    assert page.locator("[data-testid=eml-note-sms]").count() == 1 and "incomplete" in page.locator("[data-testid=eml-note-sms]").inner_text()
    names = [r["cells"][1] for r in body_rows(page, "eml-flows-table")]
    assert [n.startswith("Welcome Text Flow") for n in names] == [True] and len(names) == 1
    assert [r["cells"][1] for r in body_rows(page, "eml-campaigns-table")] and all("Text" in r["cells"][3] or r["cells"][3] == "Text message" for r in body_rows(page, "eml-campaigns-table"))
    page.locator("[data-testid=eml-channel-email]").click()
    page.wait_for_function("() => new URLSearchParams(location.search).get('channel') === 'email'")
    idle(page)
    assert tile(page, "open_rate") != "–" and page.locator("[data-testid=eml-note-sms]").count() == 0
    assert not any(r["cells"][1].startswith("Welcome Text Flow") for r in body_rows(page, "eml-flows-table"))


def test_chart_grain_metric_and_partial_marking(page):
    goto(page)
    expect(page.locator("[data-testid=eml-chart-title]")).to_have_text("Revenue by day")
    assert page.locator("[data-testid=eml-chart-note]").inner_text().startswith("Every period shown is complete")
    page.locator("[data-testid=eml-grain-week]").click()
    pressed(page, "eml-grain-week")
    expect(page.locator("[data-testid=eml-chart-title]")).to_have_text("Revenue by week")
    expect(page.locator("[data-testid=eml-chart-note]")).to_contain_text("partial")                  # the week of Nov 9 has one loaded day and the first week starts before Oct 11
    assert params(page).get("grain") == "week"
    page.locator("[data-testid=eml-grain-month]").click()
    pressed(page, "eml-grain-month")
    expect(page.locator("[data-testid=eml-chart-note]")).to_contain_text("Nov 2026")
    page.locator("[data-testid=eml-metric]").select_option("open_rate")
    expect(page.locator("[data-testid=eml-chart-title]")).to_have_text("Open rate by month")
    page.locator("[data-testid=eml-metric]").select_option("opens")
    expect(page.locator("[data-testid=eml-chart-title]")).to_have_text("Opens (views) by month")
    assert {"grain": "month", "metric": "opens"}.items() <= params(page).items()
    labels = page.locator("[data-testid=eml-metric] option").all_inner_texts()
    assert labels == ["Revenue", "Sends", "Delivered", "Opens (views)", "Open rate", "Click rate", "Orders", "Revenue per delivered", "Click to open", "Orders per delivered", "Unsubscribe rate"]
    page.locator("[data-testid=eml-split]").check()
    page.wait_for_function("() => new URLSearchParams(location.search).get('split') === '1'")
    idle(page)
    expect(page.locator("[data-testid=eml-chart-panel] canvas")).to_be_visible()
    # the chart really holds the plotted series (Flows and Campaigns)
    page.wait_for_function("() => Chart.getChart(document.getElementById('eml-chart')).data.datasets.map(d => d.label).join() === 'Flows,Campaigns'")
    page.reload()
    page.wait_for_selector("[data-testid=eml-page]")
    idle(page)
    expect(page.locator("[data-testid=eml-chart-title]")).to_have_text("Opens (views) by month")        # the choices live in the URL


def chart_info(page):
    return page.evaluate("""() => { const c = Chart.getChart(document.getElementById('eml-chart'));
      const shown = (c.legend.legendItems || []).map(i => i.text);
      const line = c.data.datasets.find(d => d.type === 'line' && !d._halo);
      return { legend: shown, y1: !!c.scales.y1 && c.scales.y1.options.display !== false, y1title: c.scales.y1 && c.scales.y1.options.title && c.scales.y1.options.title.text,
        y1ticks: c.scales.y1 ? c.scales.y1.ticks.map(t => t.label) : [], line: line ? { axis: line.yAxisID, data: line.data, label: line.label } : null,
        bars: c.data.datasets.filter(d => d.type === 'bar').map(d => d.label), n: c.data.labels.length }; }""")


def test_second_metric_line_on_a_right_axis(page):
    msgs = []
    page.on("console", lambda m: msgs.append(m.text) if m.type == "error" else None)
    goto(page, "?kind=all")
    # default: no line, no right axis
    assert chart_info(page)["line"] is None and not chart_info(page)["y1"]
    assert page.locator("[data-testid=eml-line] option").all_inner_texts()[0] == "None"
    page.locator("[data-testid=eml-metric]").select_option("sends")
    page.locator("[data-testid=eml-line]").select_option("click_rate")
    expect(page.locator("[data-testid=eml-chart-title]")).to_have_text("Sends (bars) and Click rate (line) by day")
    assert {"metric": "sends", "line": "click_rate"}.items() <= params(page).items()
    info = chart_info(page)
    assert info["y1"] and info["y1title"].startswith("Click rate")
    assert info["line"]["axis"] == "y1" and info["line"]["label"] == "Click rate (line, right)"
    assert "Sends (bars, left)" in info["legend"] and "Click rate (line, right)" in info["legend"] and not any("outline" in t for t in info["legend"])
    assert any(t.endswith("%") for t in info["y1ticks"])
    ts = api(page, "/api/email/timeseries?window=30d&grain=day")
    assert info["n"] == len(ts["buckets"]) == len(info["line"]["data"]) and all(isinstance(v, float) for v in info["line"]["data"])
    assert [round(v, 9) for v in info["line"]["data"]] == [round(b["v"]["click_rate"], 9) for b in ts["buckets"]]
    # both values on hover
    box = page.locator("#eml-chart").bounding_box()
    page.mouse.move(box["x"] + box["width"] * 0.5, box["y"] + box["height"] * 0.5)
    tip = page.evaluate("() => { const c = Chart.getChart(document.getElementById('eml-chart')); const a = c.getActiveElements(); return a.map(e => c.data.datasets[e.datasetIndex].label); }")
    assert "Sends (bars, left)" in tip and "Click rate (line, right)" in tip
    # week and month: partial bucket is an open marker with a dashed segment, the note says so, line has a point per bucket
    page.locator("[data-testid=eml-grain-week]").click()
    pressed(page, "eml-grain-week")
    expect(page.locator("[data-testid=eml-chart-title]")).to_have_text("Sends (bars) and Click rate (line) by week")
    note = page.locator("[data-testid=eml-chart-note]").inner_text()
    assert "partial" in note and "open marker" in note and "right axis" in note
    wk = api(page, "/api/email/timeseries?window=30d&grain=week")
    assert chart_info(page)["n"] == len(wk["buckets"])
    page.locator("[data-testid=eml-grain-month]").click()
    pressed(page, "eml-grain-month")
    assert chart_info(page)["line"]["data"] and all(v is not None for v in chart_info(page)["line"]["data"])
    # split: bars stay split, the line is the combined total, and the note says so
    page.locator("[data-testid=eml-split]").check()
    page.wait_for_function("() => new URLSearchParams(location.search).get('split') === '1'")
    idle(page)
    page.wait_for_function("() => Chart.getChart(document.getElementById('eml-chart')).data.datasets.some(d => d.label.startsWith('Flows: Sends'))")
    info = chart_info(page)
    assert any(b.startswith("Flows: Sends") for b in info["bars"]) and any(b.startswith("Campaigns: Sends") for b in info["bars"]) and info["line"]["axis"] == "y1"
    assert "combined total of flows and campaigns" in page.locator("[data-testid=eml-chart-note]").inner_text()
    page.locator("[data-testid=eml-split]").uncheck()
    idle(page)
    # reload restores
    page.reload()
    page.wait_for_selector("[data-testid=eml-page]")
    idle(page)
    assert params(page).get("line") == "click_rate" and page.locator("[data-testid=eml-line]").input_value() == "click_rate"
    assert chart_info(page)["y1"] and chart_info(page)["line"]["label"] == "Click rate (line, right)"
    page.screenshot(path=str(SHOTS / "email_06_bar_and_line.png"), full_page=True)
    # a rate as the bar measure still draws bars when a line is chosen; same metric twice is allowed
    page.locator("[data-testid=eml-metric]").select_option("open_rate")
    page.locator("[data-testid=eml-line]").select_option("open_rate")
    assert chart_info(page)["bars"] and chart_info(page)["y1"]
    # None removes the line and the right axis; an invalid value falls back to None
    page.locator("[data-testid=eml-line]").select_option("")
    info = chart_info(page)
    assert not info["y1"] and (info["line"] is None or info["line"]["axis"] == "y") and "line" not in params(page)        # a rate alone is its own single-axis line, as before
    expect(page.locator("[data-testid=eml-chart-title]")).to_have_text("Open rate by month")
    goto(page, "?line=bogus")
    assert page.locator("[data-testid=eml-line]").input_value() == "" and not chart_info(page)["y1"]
    text = page.locator("[data-testid=eml-chart-panel]").inner_text()
    assert "null" not in text and "undefined" not in text and "\u2014" not in text
    assert not msgs, msgs


def test_second_metric_chart_on_a_phone(page):
    page.set_viewport_size({"width": 375, "height": 812})
    goto(page, "?metric=sends&line=click_rate&grain=week")
    assert no_sideways_scroll(page)
    info = chart_info(page)
    assert info["y1"] and info["line"]["label"] == "Click rate (line)" and "Sends (bars)" in info["legend"]
    assert len(info["y1ticks"]) <= 6
    assert page.evaluate("() => document.getElementById('eml-chart').getBoundingClientRect().width") <= 375
    assert no_sideways_scroll(page)
    page.screenshot(path=str(SHOTS / "email_07_bar_and_line_phone.png"), full_page=True)


def test_flows_table_sorts_and_opens_the_flows_messages(page):
    goto(page, "?kind=flows")
    rev = lambda: [float(re.sub(r"[^0-9.]", "", r["cells"][8])) for r in body_rows(page, "eml-flows-table")]
    r0 = rev()
    assert r0 == sorted(r0, reverse=True)                                                           # revenue first by default
    assert page.locator("[data-testid=eml-flows-table] th[data-col=revenue]").get_attribute("aria-sort") == "descending"
    page.locator("[data-testid=eml-flows-table] th[data-col=sends] button.thbtn").click()
    sends = [int(r["cells"][3].replace(",", "")) for r in body_rows(page, "eml-flows-table")]
    assert sends == sorted(sends, reverse=True)
    page.locator("[data-testid=eml-flows-table] th[data-col=sends] button.thbtn").click()
    sends = [int(r["cells"][3].replace(",", "")) for r in body_rows(page, "eml-flows-table")]
    assert sends == sorted(sends)
    page.locator("[data-testid=eml-flows-table] th[data-col=name] button.thbtn").click()
    names = [r["cells"][1] for r in body_rows(page, "eml-flows-table")]
    assert names == sorted(names, key=str.lower)
    # shares add to 100 percent and the total row ties to the summary
    shares = [float(r["cells"][10].rstrip("%")) for r in body_rows(page, "eml-flows-table")]
    assert abs(sum(shares) - 100) < 0.2
    total = page.locator("[data-testid=eml-flows-table] tfoot tr").inner_text()
    assert f"{api(page, '/api/email/summary?window=30d&kind=flows')['totals']['sends']:,}" in total
    # a click on the row opens its messages
    page.locator("[data-testid=eml-flows-table] tbody tr[data-key=F1]").click()
    page.wait_for_selector("[data-testid=eml-msg-table]")
    msgs = page.locator("[data-testid=eml-msg-row]")
    assert msgs.count() == 2
    assert "copy not available" in page.locator("[data-testid=eml-msg-row][data-message=fm2]").inner_text()
    assert "Your plan renews soon" in page.locator("[data-testid=eml-msg-row][data-message=fm1]").inner_text()
    page.screenshot(path=str(SHOTS / "email_03_flow_messages.png"), full_page=True)


def test_campaign_table_search_sort_and_detail_with_copy(page):
    goto(page, "?kind=campaigns&window=all")
    rows = body_rows(page, "eml-campaigns-table")
    dates = [r["cells"][0] for r in rows]
    assert rows[0]["cells"][1].startswith("Last Call Text Campaign") and len(rows) >= 5 and dates[0].startswith("Oct 25")          # newest send first
    assert page.locator("[data-testid=eml-campaigns-table] th[data-col=send_date]").get_attribute("aria-sort") == "descending"
    total = page.locator("[data-testid=eml-campaign-count]").inner_text()
    page.locator("[data-testid=eml-campaign-search]").fill("beef liver")
    expect(page.locator("[data-testid=eml-campaigns-table] tbody tr[data-key]")).to_have_count(1)
    assert "1 of" in page.locator("[data-testid=eml-campaign-count]").inner_text() and body_rows(page, "eml-campaigns-table")[0]["cells"][1].startswith("Beef Liver Restock")
    page.locator("[data-testid=eml-campaign-search]").fill("read this first")                  # subjects are searched too
    assert page.locator("[data-testid=eml-campaigns-table] tbody tr[data-key]").count() == len(rows) - 2        # all but the two text campaigns, which have no subject
    page.locator("[data-testid=eml-campaign-search]").fill("")
    assert page.locator("[data-testid=eml-campaign-count]").inner_text() == total
    page.locator("[data-testid=eml-campaigns-table] th[data-col=revenue] button.thbtn").click()
    rev = [float(re.sub(r"[^0-9.]", "", r["cells"][9])) for r in body_rows(page, "eml-campaigns-table")]
    assert rev == sorted(rev, reverse=True)
    # open an email campaign (a 90 day page window): subject, preview, copy preview and expand, link and image counts, copy date, daily chart
    goto(page, "?kind=campaigns&window=90d")
    page.locator("[data-testid=eml-campaign-search]").fill("holiday gift")
    page.locator("[data-testid=eml-campaigns-table] tbody tr[data-key]").first.click()
    page.wait_for_selector("[data-testid=eml-detail-title]")
    expect(page.locator("[data-testid=eml-detail-title]")).to_have_text("Holiday Gift Guide: read this first")
    assert params(page).get("message") == "cm09"
    expect(page.locator("[data-testid=eml-preview]")).to_contain_text("Open for the details")
    body = page.locator("[data-testid=eml-body]")
    short = body.inner_text()
    assert 590 < len(short) < 610 and short.endswith("...")                                          # about the first 600 characters
    page.locator("[data-testid=eml-body-toggle]").click()
    assert len(body.inner_text()) > len(short) and "Holiday Gift Guide copy." in body.inner_text()
    assert page.locator("[data-testid=eml-body-toggle]").inner_text() == "Show less"
    detail = page.locator("[data-testid=eml-detail]").inner_text()
    assert "2 links" in detail and "1 image" in detail and "Copy as of" in detail and "latest version only" in detail
    assert page.locator("[data-testid=eml-detail] .kpi").count() == 7 and "Klaviyo-attributed" in page.locator("[data-testid=eml-detail-window]").inner_text()
    # the numbers cover all loaded days by default; one click ties them to the page window, which is a different set of days
    expect(page.locator("[data-testid=eml-detail-window]")).to_contain_text("Jul 13 to Nov 9 (120 days)")
    sends = lambda: page.locator("[data-testid=eml-detail] .kpi").first.locator(".kpi-value").inner_text()
    assert sends() == f"{api(page, '/api/email/message/cm09?window=all')['totals']['sends']:,}"
    page.locator("[data-testid=eml-scope-page]").click()
    expect(page.locator("[data-testid=eml-detail-window]")).to_contain_text("Aug 12 to Nov 9 (90 days)")
    assert sends() == f"{api(page, '/api/email/message/cm09?window=90d')['totals']['sends']:,}"
    page.locator("[data-testid=eml-scope-all]").click()
    expect(page.locator("[data-testid=eml-detail-window]")).to_contain_text("Jul 13 to Nov 9")
    expect(page.locator("[data-testid=eml-detail] canvas")).to_be_visible()
    page.locator("[data-testid=eml-dm-revenue]").click()
    assert page.evaluate("() => Chart.getChart(document.querySelector('[data-testid=eml-detail] canvas')).data.datasets[0].label") == "Revenue (Klaviyo-attributed)"
    page.screenshot(path=str(SHOTS / "email_04_message_detail.png"))
    page.keyboard.press("Escape")
    expect(page.locator("[data-testid=eml-detail]")).to_have_count(0)
    assert "message" not in params(page)


@pytest.mark.allow_errors                                                                           # the unknown message id is a deliberate 404
def test_message_without_copy_says_so_and_a_text_message_shows_its_body(page):
    goto(page, "?kind=flows")
    page.locator("[data-testid=eml-flows-table] tbody tr[data-key=F1]").click()
    page.wait_for_selector("[data-testid=eml-msg-row][data-message=fm2]")
    page.locator("[data-testid=eml-msg-row][data-message=fm2]").click()
    page.wait_for_selector("[data-testid=eml-detail-title]")
    expect(page.locator("[data-testid=eml-detail-title]")).to_have_text("Last chance to skip a shipment")
    expect(page.locator("[data-testid=eml-nocopy]")).to_contain_text("Copy not available")
    assert page.locator("[data-testid=eml-body]").count() == 0
    page.keyboard.press("Escape")
    goto(page, "?message=fm5&kind=flows")                                                           # a link straight to a text message flow message
    page.wait_for_selector("[data-testid=eml-detail-title]")
    assert "Welcome text" in page.locator("[data-testid=eml-body]").inner_text() and "Text message" in page.locator("[data-testid=eml-detail]").inner_text()
    goto(page, "?message=nope")
    page.wait_for_selector("[data-testid=eml-detail-error]")
    assert "not in the loaded data" in page.locator("[data-testid=eml-detail-error]").inner_text()


def test_definitions_are_collapsed_and_plain(page):
    goto(page)
    det = page.locator("[data-testid=eml-help]")
    assert det.get_attribute("open") is None
    det.locator("summary").click()
    text = det.inner_text()
    for needle in ("Open rate", "Click rate", "Click to open", "Revenue per delivered", "Unsubscribe rate", "ratio of sums", "Partial periods", "Recent days still move", "Copy"):
        assert needle in text, needle
    assert "—" not in text and "SMS" not in re.sub(r"\(SMS\)", "", text)


def test_no_em_dash_acronyms_are_spelled_out_and_text_is_12px_or_more(page):
    goto(page)
    page.locator("[data-testid=eml-help] summary").click()
    body = page.locator("[data-testid=eml-page]").inner_text()
    assert "—" not in body and "SMS)" in body                                                       # SMS is introduced as 'text message (SMS)'
    small = page.evaluate("""() => [...document.querySelectorAll('[data-testid=eml-page] *')].filter(e => !e.classList.contains('arrow') && e.childNodes.length && [...e.childNodes].some(n => n.nodeType === 3 && n.textContent.trim()) && parseFloat(getComputedStyle(e).fontSize) < 12).map(e => e.className + ':' + e.textContent.slice(0, 30))""")
    assert small == []


def test_phone_width_has_no_sideways_scroll_including_the_dialog(page):
    page.set_viewport_size({"width": 375, "height": 800})
    goto(page)
    assert no_sideways_scroll(page)
    assert page.evaluate("() => document.querySelector('[data-testid=eml-flows-table] .tablewrap').scrollWidth > document.querySelector('[data-testid=eml-flows-table] .tablewrap').clientWidth")    # the table scrolls inside its own box
    page.locator("[data-testid=eml-flows-table] tbody tr[data-key=F1]").click()
    page.wait_for_selector("[data-testid=eml-msg-table]")
    assert no_sideways_scroll(page)
    page.screenshot(path=str(SHOTS / "email_02_phone.png"), full_page=True)
    page.goto("/email?kind=campaigns&message=cm03")
    page.wait_for_selector("[data-testid=eml-detail-title]")
    page.wait_for_selector("[data-testid=eml-detail] canvas")
    assert no_sideways_scroll(page)
    assert page.evaluate("() => { const m = document.querySelector('.eml-modal'); return m.scrollWidth <= m.clientWidth + 1; }")
    page.screenshot(path=str(SHOTS / "email_05_phone_detail.png"))


@pytest.mark.allow_errors
def test_a_failing_endpoint_ends_in_the_error_box_with_retry(page):
    page.route("**/api/email/summary*", lambda r: r.fulfill(status=503, content_type="application/json", body='{"error": "query timed out"}'))
    page.goto("/email")
    page.wait_for_selector(".errorbox")
    assert "query timed out" in page.locator(".errorbox").inner_text()
    page.unroute("**/api/email/summary*")
    page.locator(".errorbox button", has_text="Retry").click()
    page.wait_for_selector("[data-testid=eml-page]")


def _no_junk(text):
    return not re.search(r"\b(null|undefined|None|NaN)\b", text)


@pytest.mark.allow_errors
def test_dialogs_and_tables_never_print_null_or_undefined(page):
    # tables: every row of both lists, over all loaded days
    goto(page, "?window=all")
    for t in ("eml-flows-table", "eml-campaigns-table"):
        if page.locator(f"[data-testid={t}]").count():
            assert _no_junk(page.locator(f"[data-testid={t}]").inner_text()), t
    goto(page, "?kind=campaigns&window=all")
    assert _no_junk(page.locator("[data-testid=eml-campaigns-table]").inner_text())
    # a message with no subject, preview text or from name (the text campaign): no line and no label for the missing fields
    goto(page, "?kind=campaigns&window=all&message=cm08")
    page.wait_for_selector("[data-testid=eml-detail-title]")
    page.wait_for_selector("[data-testid=eml-detail] canvas")
    detail = page.locator("[data-testid=eml-detail]").inner_text()
    assert _no_junk(detail), detail
    assert page.locator("[data-testid=eml-subject]").count() == 0 and page.locator("[data-testid=eml-preview]").count() == 0 and page.locator("[data-testid=eml-from]").count() == 0
    assert "Subject" not in detail and "Preview text" not in detail and "From name" not in detail
    # a message with everything: labelled subject line, preview text and from name
    goto(page, "?kind=campaigns&window=all&message=cm09")
    page.wait_for_selector("[data-testid=eml-detail-title]")
    page.wait_for_selector("[data-testid=eml-detail] canvas")
    detail = page.locator("[data-testid=eml-detail]").inner_text()
    assert _no_junk(detail), detail
    assert page.locator("[data-testid=eml-subject]").inner_text() == "Subject: Holiday Gift Guide: read this first"
    assert page.locator("[data-testid=eml-preview]").inner_text() == "Preview text: Open for the details"
    assert page.locator("[data-testid=eml-from]").inner_text() == "From name: Lonvera"
    # a flow message with no copy at all (fm2) and a text flow message (fm5, no subject)
    for mid in ("fm2", "fm5"):
        goto(page, f"?kind=flows&message={mid}")
        page.wait_for_selector("[data-testid=eml-detail-title]")
        assert _no_junk(page.locator("[data-testid=eml-detail]").inner_text()), mid
