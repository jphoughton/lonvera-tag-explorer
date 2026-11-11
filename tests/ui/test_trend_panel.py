"""Trend panel, sparklines and pins.

Split out of tests/ui/test_ui.py with no assertion changes. Shared helpers live in ui_helpers.py. Run: see the docstring of conftest.py.
"""
import re
from datetime import date, timedelta
from urllib.parse import parse_qs, urlencode, urlparse
import pytest
from playwright.sync_api import expect
from conftest import IS_MOCK, PASSWORD, SHOTS, USERNAME
from ui_helpers import *  # noqa: F401,F403

def test_ad_trend_panel_selectors_include_traffic(page):
    open_first_ad(page, "window=14d")
    opts = page.locator("#trend-bars option").all_inner_texts()
    assert opts == ["Spend", "Purchases", "Revenue", "ROAS", "CPA", "Impressions", "Link clicks", "CTR", "CPC", "None"], opts
    pick_metric(page, "bars", "impressions")
    expect(page).to_have_url(re.compile(r"tb=impressions"))
    wait_trend(page)
    assert page.input_value("#trend-bars") == "impressions"
    if IS_MOCK:  # days without impressions are gaps (null), never zero
        assert bars_ds(trend_info(page))[0]["data"][0] is None
    shot(page, "27_ad_trend_impressions")


# ------------------------------------------------------------------ trend panel, sparklines, pins (v1.6)
def test_trend_panel_renders_at_every_level(page):
    cluster, icp, pain = drill_to_families(page)
    urls = ["/?platform=meta", f"/p/{SLUG}", purl(cluster), purl(cluster, icp), purl(cluster, icp, pain), f"/p/{SLUG}?all=1",
            f"/p/{SLUG}?view=ads", f"/p/{SLUG}?view=campaigns", f"/p/{SLUG}?window=14d&compare=prev"]
    for u in urls:
        page.goto(u)
        page.wait_for_selector(".tablebox")
        wait_trend(page)
        info = trend_info(page)
        assert info["n"] >= 2 and len(info["labels"]) >= 5, (u, info)
        expect(page.locator("#trend-panel")).to_be_visible()
        # defaults: Bars = Spend (left axis), Line = ROAS (right axis), both axes labelled
        assert page.input_value("#trend-bars") == "spend" and page.input_value("#trend-line") == "roas"
        assert bars_ds(info) and line_ds(info)
        assert info["axes"]["left"]["display"] and info["axes"]["right"]["display"]
        assert "Spend" in info["axes"]["left"]["title"] and "ROAS" in info["axes"]["right"]["title"]
    shot(page, "28_trend_every_level")
    # both selectors offer every metric (CPM / Conv rate only with the Advanced chip) and None
    page.goto(f"/p/{SLUG}")
    wait_trend(page)
    opts = page.locator("#trend-bars option").all_inner_texts()
    assert opts == ["Spend", "Purchases", "Revenue", "ROAS", "CPA", "Impressions", "Link clicks", "CTR", "CPC", "None"], opts
    assert page.locator("#trend-line option").all_inner_texts() == opts
    page.locator(".cchip[data-cols=advanced]").click()
    assert page.locator("#trend-bars option").all_inner_texts()[-3:] == ["CPM", "Conv rate", "None"]


def test_trend_bars_line_selectors_swap_axes_and_url(page):
    goto_product(page)
    wait_trend(page)
    info = trend_info(page)
    assert [d["type"] for d in info["ds"]] == ["bar", "line"]
    assert bars_ds(info)[0]["axis"] == "left" and line_ds(info)[0]["axis"] == "right"
    assert "tb" not in qparams(page) and "tl" not in qparams(page)  # defaults are not in the URL
    # axes: labelled per metric, tinted like their series (bars accent blue, line orange), tick formats per metric
    assert info["axes"]["left"]["tick"] != info["axes"]["right"]["tick"]
    assert re.fullmatch(r"\$[\d.,]+[KM]?", info["axes"]["left"]["ticks"][1]), info["axes"]["left"]["ticks"]  # money
    assert re.fullmatch(r"\d+\.\d\d", info["axes"]["right"]["ticks"][1]), info["axes"]["right"]["ticks"]  # ROAS
    reqs = []
    page.on("request", lambda r: reqs.append(r.url) if "/api/products/" in r.url else None)
    # the bars metric also drives the sparkline column: the page re-fetches (trend + tables with spark=<bars>)
    pick_metric(page, "bars", "revenue")
    expect(page).to_have_url(re.compile(r"tb=revenue"))
    wait_rows(page)
    wait_trend(page)
    assert any("/trend" in u for u in reqs) and any("/tree" in u and "spark=revenue" in u for u in reqs)
    info = trend_info(page)
    assert "Revenue" in info["axes"]["left"]["title"] and bars_ds(info)[0]["label"] == "Revenue"
    assert "Revenue" in page.locator(f"{BODY_ROWS} svg.spark title").first.text_content()
    # the line metric is a client-side change: no request, URL tl=..., right axis re-formatted
    n = len(reqs)
    pick_metric(page, "line", "cpc")
    expect(page).to_have_url(re.compile(r"tl=cpc"))
    page.wait_for_timeout(200)
    info = trend_info(page)
    assert line_ds(info)[0]["label"] == "CPC" and "CPC" in info["axes"]["right"]["title"] and re.fullmatch(r"\$\d+\.\d\d", info["axes"]["right"]["ticks"][1])
    assert len(reqs) == n, "changing the line metric must not refetch"
    # Swap flips them (Bars <-> Line)
    page.click("#trend-swap")
    wait_rows(page)
    wait_trend(page)
    assert qparams(page)["tb"] == "cpc" and qparams(page)["tl"] == "revenue"
    info = trend_info(page)
    assert bars_ds(info)[0]["label"] == "CPC" and line_ds(info)[0]["label"] == "Revenue"
    assert "CPC" in info["axes"]["left"]["title"] and "Revenue" in info["axes"]["right"]["title"]
    assert "CPC" in page.locator(f"{BODY_ROWS} svg.spark title").first.text_content()  # sparklines follow the bars
    # None for one of them: that axis disappears
    pick_metric(page, "line", "none")
    page.wait_for_timeout(200)
    info = trend_info(page)
    assert line_ds(info) == [] and not info["axes"]["right"]["display"] and info["axes"]["left"]["display"]
    pick_metric(page, "bars", "none")
    wait_rows(page)
    trend_idle(page)
    assert qparams(page)["tb"] == "none" and qparams(page)["tl"] == "none"
    assert trend_info(page)["n"] == 0 and "Pick a metric" in page.locator("#trend-note").inner_text()
    pick_metric(page, "line", "ctr")
    wait_rows(page)
    wait_trend(page)
    info = trend_info(page)
    assert bars_ds(info) == [] and line_ds(info)[0]["label"] == "CTR" and not info["axes"]["left"]["display"] and info["axes"]["right"]["display"]
    assert "CTR" in page.locator(f"{BODY_ROWS} svg.spark title").first.text_content()  # bars None -> sparklines follow the line
    # survives reload, carried into the next level
    page.reload()
    wait_rows(page)
    wait_trend(page)
    assert page.input_value("#trend-bars") == "none" and page.input_value("#trend-line") == "ctr"
    open_first_row(page)
    wait_rows(page)
    assert qparams(page)["tb"] == "none" and qparams(page)["tl"] == "ctr"
    shot(page, "29_trend_dual_axis")


def test_trend_split_stage_only_for_additive_bars(page):
    goto_product(page, "?window=60d")
    wait_trend(page)
    reqs = trend_requests(page)
    # grain switch re-fetches; weeks are ISO weeks (60 days = about 9-10 buckets)
    day_labels = len(trend_info(page)["labels"])
    page.locator("#trend-grain [data-grain=week]").click()
    expect(page).to_have_url(re.compile(r"tg=week"))
    wait_trend(page)
    assert reqs and reqs[-1].get("grain") == "week"
    assert 8 <= len(trend_info(page)["labels"]) <= 11 and len(trend_info(page)["labels"]) < day_labels
    page.locator("#trend-grain [data-grain=day]").click()
    wait_trend(page)
    assert reqs[-1].get("grain") == "day" and "tg" not in qparams(page)
    # additive bars (Spend): the split control is enabled; Stage stacks Testing (teal) and Scaling (indigo) bars
    assert not page.locator("#trend-split [data-split=stage]").is_disabled()
    n = len(reqs)
    page.locator("#trend-split [data-split=stage]").click()
    expect(page).to_have_url(re.compile(r"ts=stage"))
    wait_trend(page)
    assert len(reqs) == n + 1 and reqs[-1].get("split") == "stage"
    st = trend_info(page)
    stage_bars = [d for d in bars_ds(st) if d["label"] in ("Testing", "Scaling")]
    assert {d["label"] for d in stage_bars} == {"Testing", "Scaling"} and all(d["stack"] == "stage" for d in stage_bars)
    assert st["axes"]["left"]["stacked"] is True
    assert len({d["color"] for d in stage_bars}) == 2
    assert [d["label"] for d in line_ds(st)] == ["ROAS"] or all("ROAS" in d["label"] for d in line_ds(st))
    # ROAS is a ratio: with Split = Stage the line becomes one line per stage on the right axis
    assert {d["label"] for d in line_ds(st)} >= {"ROAS · Testing", "ROAS · Scaling"} and all(d["axis"] == "right" for d in line_ds(st))
    # a metric on the line that is additive keeps the total line
    pick_metric(page, "line", "purchases")
    page.wait_for_timeout(200)
    assert [d["label"] for d in line_ds(trend_info(page))] == ["Purchases"]
    shot(page, "30_trend_split_stage")
    # ratio bars (ROAS): the split is not available (disabled + explanation) and nothing is split
    pick_metric(page, "bars", "roas")
    wait_rows(page)
    wait_trend(page)
    assert page.locator("#trend-split [data-split=stage]").is_disabled() and page.locator("#trend-split [data-split=none]").is_disabled()
    assert page.locator("#trend-split-hint").inner_text().strip() != ""
    info = trend_info(page)
    assert not any(d["label"] in ("Testing", "Scaling") for d in info["ds"]) and len(bars_ds(info)) == 1
    assert reqs[-1].get("split") is None  # the stage split is not requested
    # additive again: the (still stored) split applies again
    pick_metric(page, "bars", "impressions")
    wait_rows(page)
    wait_trend(page)
    assert not page.locator("#trend-split [data-split=stage]").is_disabled() and reqs[-1].get("split") == "stage"
    # the state survives a reload and a drill
    page.reload()
    wait_trend(page)
    expect(page.locator("#trend-split [data-split=stage]")).to_have_attribute("aria-pressed", "true")
    open_first_row(page)
    wait_rows(page)
    wait_trend(page)
    assert qparams(page)["ts"] == "stage" and qparams(page)["tb"] == "impressions"
    # there is no "Top rows" split any more
    assert page.locator("#trend-split [data-split=children]").count() == 0


def test_trend_month_grain_labels_partial_and_refetch(page):
    goto_product(page, "?window=180d")
    wait_trend(page)
    reqs = trend_requests(page)
    expect(page.locator("#trend-grain [data-grain=month]")).to_have_text("Month")
    page.locator("#trend-grain [data-grain=month]").click()
    expect(page).to_have_url(re.compile(r"tg=month"))
    wait_trend(page)
    assert reqs and reqs[-1].get("grain") == "month"  # switching re-fetches
    info = trend_info(page)
    assert 6 <= len(info["labels"]) <= 8, info["labels"]  # 180 days = 7 calendar months
    assert all(re.fullmatch(r"\d{4}-\d{2}-01", t) for t in info["labels"])
    ticks = page.evaluate("() => Chart.getChart('trend-chart').scales.x.ticks.map(t => t.label)")
    assert all(re.fullmatch(r"[A-Z][a-z]{2} 20\d\d", t) for t in ticks), ticks  # 'Oct 2026' style
    # the cut first / last months are flagged partial by the API and shown as such in the tooltip
    pts = page.context.request.get(f"/api/products/{SLUG}/trend?grain=month&window=180d").json()["points"]
    assert pts[0].get("partial") and pts[-1].get("partial") and not all(pt.get("partial") for pt in pts)
    box = page.locator("#trend-chart").bounding_box()

    def hover_bucket(i):
        pos = page.evaluate("(i) => { const c = Chart.getChart('trend-chart'); const m = c.getDatasetMeta(0).data[i]; return {x: m.x, y: (c.chartArea.top + c.chartArea.bottom) / 2}; }", i)
        page.mouse.move(box["x"] + pos["x"], box["y"] + pos["y"])
        page.wait_for_timeout(250)
        return page.evaluate("() => Chart.getChart('trend-chart').tooltip.title")

    tip = hover_bucket(len(info["labels"]) - 1)  # last bucket
    assert tip and re.fullmatch(r"[A-Z][a-z]+ 20\d\d \(partial\)", tip[0]), tip  # 'November 2026 (partial)'
    mid = hover_bucket(len(info["labels"]) // 2)  # a full month in the middle
    assert mid and re.fullmatch(r"[A-Z][a-z]+ 20\d\d", mid[0]), mid
    shot(page, "36_trend_month")
    # compare: B is aligned to A by position (null padded)
    page.goto(f"/p/{SLUG}?window=60d&compare=prev&tg=month")
    wait_rows(page)
    wait_trend(page)
    info = trend_info(page)
    b = [d for d in info["ds"] if d["label"].startswith("B ")]
    assert len(b) == 2 and all(len(d["data"]) == len(info["labels"]) for d in b)
    # a 7 day window with month grain simply shows one or two partial months
    page.goto(f"/p/{SLUG}?window=7d&tg=month")
    wait_rows(page)
    wait_trend(page)
    labels = trend_info(page)["labels"]
    assert 1 <= len(labels) <= 2
    pts7 = page.context.request.get(f"/api/products/{SLUG}/trend?grain=month&window=7d").json()["points"]
    assert all(pt.get("partial") for pt in pts7)
    # back to Day / Week drops the param
    page.locator("#trend-grain [data-grain=day]").click()
    wait_trend(page)
    assert "tg" not in qparams(page) and len(trend_info(page)["labels"]) == 7
    # state survives a drill
    page.goto(f"/p/{SLUG}?tg=month")
    wait_rows(page)
    open_first_row(page)
    wait_rows(page)
    wait_trend(page)
    assert qparams(page)["tg"] == "month"
    expect(page.locator("#trend-grain [data-grain=month]")).to_have_attribute("aria-pressed", "true")


def test_trend_compare_overlay_bars_and_line(page):
    goto_product(page, "?window=14d")
    wait_trend(page)
    expect(page.locator("#trend-overlay-wrap")).to_be_hidden()
    assert not any(d["label"].startswith("B ") for d in trend_info(page)["ds"])
    page.select_option("#cmp", "prev")
    expect(page).to_have_url(re.compile(r"compare=prev"))
    wait_rows(page)
    wait_trend(page)
    expect(page.locator("#trend-overlay-wrap")).to_be_visible()
    info = trend_info(page)
    b = [d for d in info["ds"] if d["label"].startswith("B ")]
    kinds = {d["type"]: d for d in b}
    assert set(kinds) == {"bar", "line"}  # B as dashed outlined bars + a dashed gray line
    assert kinds["bar"]["dash"] and kinds["line"]["dash"] and kinds["bar"]["axis"] == "left" and kinds["line"]["axis"] == "right"
    assert all(len(d["data"]) == len(info["labels"]) for d in b)  # aligned to A by position
    assert kinds["line"]["color"] in ("#8b949e", "rgb(139, 148, 158)")
    reqs = trend_requests(page)
    page.uncheck("#trend-overlay")  # a client-side toggle
    assert not any(d["label"].startswith("B ") for d in trend_info(page)["ds"]) and not reqs
    page.check("#trend-overlay")
    assert sum(1 for d in trend_info(page)["ds"] if d["label"].startswith("B ")) == 2
    page.locator("#trend-grain [data-grain=week]").click()
    wait_trend(page)
    assert reqs[-1].get("compare") == "prev" and reqs[-1].get("window") == "14d"
    # with Line = None only the bars overlay is drawn
    pick_metric(page, "line", "none")
    page.wait_for_timeout(200)
    assert [d["type"] for d in trend_info(page)["ds"] if d["label"].startswith("B ")] == ["bar"]
    page.select_option("#cmp", "off")
    wait_rows(page)
    wait_trend(page)
    expect(page.locator("#trend-overlay-wrap")).to_be_hidden()
    assert not any(d["label"].startswith("B ") for d in trend_info(page)["ds"])
    shot(page, "32_trend_compare_overlay")


def test_trend_hover_tooltip_shows_both_values_and_legend_toggle(page):
    goto_product(page, "?window=30d&ts=stage")
    wait_trend(page)
    box = page.locator("#trend-chart").bounding_box()
    page.mouse.move(box["x"] + box["width"] * 0.55, box["y"] + box["height"] * 0.5)
    page.wait_for_timeout(250)
    tip = page.evaluate("""() => { const t = Chart.getChart('trend-chart').tooltip; return {n: t.getActiveElements().length, title: t.title, lines: (t.body || []).map(b => b.lines.join(' '))}; }""")
    assert tip["n"] >= 2 and tip["title"], tip
    assert re.search(r"[A-Z][a-z]{2} \d{1,2}", tip["title"][0]), tip  # the date
    assert any(re.search(r"(Testing|Scaling): \$[\d,]+", ln) for ln in tip["lines"]), tip  # the bars value, formatted like the tables
    assert any(re.search(r"ROAS.*: \d\.\d\d", ln) for ln in tip["lines"]), tip  # and the line value (both values of the date)
    shot(page, "33_trend_tooltip")
    before = trend_info(page)
    idx = next(i for i, d in enumerate(before["ds"]) if d["label"] == "Testing")
    assert before["ds"][idx]["hidden"] is False
    hb = page.evaluate("(i) => { const lg = Chart.getChart('trend-chart').legend.legendHitBoxes[i]; return {x: lg.left + lg.width / 2, y: lg.top + lg.height / 2}; }", idx)

    def click_legend():
        page.mouse.move(box["x"] + hb["x"], box["y"] + hb["y"])  # Chart.js legends need a hover before the click
        page.wait_for_timeout(150)
        page.mouse.click(box["x"] + hb["x"], box["y"] + hb["y"])
        page.wait_for_timeout(150)
    click_legend()
    assert trend_info(page)["ds"][idx]["hidden"] is True
    click_legend()
    assert trend_info(page)["ds"][idx]["hidden"] is False


def test_trend_collapse_stops_requests(page):
    goto_product(page)
    wait_trend(page)
    reqs = trend_requests(page)
    page.click("#trend-toggle")
    expect(page.locator("#trend-panel")).to_have_attribute("data-collapsed", "true")
    expect(page.locator("#trend-chart")).to_be_hidden()
    assert page.evaluate("() => localStorage.getItem('tagexp.trend.collapsed')") == "1"
    page.wait_for_timeout(200)
    n = len(reqs)
    page.locator(".seg[data-window='7d']").click()  # a global filter change re-renders the page...
    wait_rows(page)
    page.wait_for_timeout(300)
    assert len(reqs) == n, "...but a collapsed panel makes no /trend request"
    page.locator("#unmatched summary").click()
    page.reload()  # collapsed state is remembered: no /trend call on load either
    wait_rows(page)
    page.wait_for_timeout(400)
    assert len(reqs) == n
    expect(page.locator(f"{BODY_ROWS} svg.spark").first).to_be_visible()  # sparklines come with the table queries
    page.click("#trend-toggle")  # expand: requests resume
    wait_trend(page)
    assert len(reqs) > n
    assert page.evaluate("() => localStorage.getItem('tagexp.trend.collapsed')") == "0"


def test_sparkline_column_every_table_unsortable_totals_empty(page):
    goto_product(page)
    d = table_data(page)
    assert "trend" in d["heads"]
    ti = d["heads"].index("trend")
    th = page.locator(f"{HEAD} th[data-col=trend]")
    assert th.locator("button").count() == 0 and th.get_attribute("aria-sort") is None  # not sortable
    keys = table_data(page)["keys"]
    th.click()
    assert table_data(page)["keys"] == keys and "sort" not in qparams(page)
    n_rows = len(d["rows"])
    assert page.locator(f"{BODY_ROWS} td:nth-child({ti + 1}) svg.spark").count() == n_rows
    assert d["foot"][ti] == ""  # totals row has none
    w, h_ = page.evaluate("() => { const s = document.querySelector('svg.spark'); return [s.getAttribute('width'), s.getAttribute('height')]; }")
    assert (int(w), int(h_)) == (70, 20)
    # the Columns chip group has "Trend" (default on), persisted
    chip_t = page.locator(".cchip[data-cols=trend]")
    expect(chip_t).to_have_attribute("aria-pressed", "true")
    chip_t.click()
    assert not has_col(page, "trend") and page.evaluate("() => localStorage.getItem('tagexp.cols.trend')") == "0"
    page.reload()
    wait_rows(page)
    assert not has_col(page, "trend")
    page.locator(".cchip[data-cols=trend]").click()
    assert has_col(page, "trend")
    # every other table type has it too
    page.goto("/?platform=meta")
    page.wait_for_selector("[data-testid=products-table] tbody tr[data-key]")
    page.wait_for_selector("[data-testid=products-table] svg.spark")
    assert has_col(page, "trend", "products-table") and page.locator("[data-testid=products-table] tbody svg.spark").count() >= 1
    page.goto(f"/p/{SLUG}?view=ads")
    wait_rows(page, "ads-table")
    assert has_col(page, "trend", "ads-table") and page.locator("[data-testid=ads-table] tbody svg.spark").count() >= 1
    page.goto(f"/p/{SLUG}?view=campaigns")
    page.wait_for_selector("[data-testid=campaign-table]")
    idle(page)
    if page.locator("[data-testid=campaign-table] tbody tr[data-key]").count():
        assert has_col(page, "trend", "campaign-table") and page.locator("[data-testid=campaign-table] tbody svg.spark").count() >= 1
    page.goto(f"/p/{SLUG}?all=1")
    wait_rows(page)
    assert has_col(page, "trend") and page.locator(f"{BODY_ROWS} svg.spark").count() >= 1
    # sparklines inside the dropdown tree (children and Meta ads) as well
    goto_product(page)
    page.locator("#expand-to [data-depth='3']").click()
    page.wait_for_selector(f"{TABLE} tr.lv3", timeout=8000)
    idle(page)
    assert page.locator(f"{TABLE} tr.lv3 svg.spark").count() >= 1 and page.locator(f"{TABLE} tr.lv2 svg.spark").count() >= 1
    shot(page, "34_sparklines")


def test_pin_rows_into_the_chart_max_8(page):
    goto_product(page)
    wait_trend(page)
    # with Bars set, the Chart checkboxes are disabled and a hint says how to enable them
    assert has_col(page, "_chart")
    boxes = page.locator(f"{BODY_ROWS} input.pin-cb")
    assert boxes.count() == len(table_data(page)["rows"]) and all(boxes.nth(i).is_disabled() for i in range(boxes.count()))
    expect(page.locator("#trend-pin-hint")).to_contain_text("Bars to None")
    pick_metric(page, "bars", "none")
    wait_rows(page)
    wait_trend(page)
    assert all(not page.locator(f"{BODY_ROWS} input.pin-cb").nth(i).is_disabled() for i in range(boxes.count()))
    expect(page.locator("#trend-pin-hint")).to_be_hidden()
    reqs = trend_requests(page)
    rows = page.locator(BODY_ROWS)
    labels = [rows.nth(i).get_attribute("data-label") for i in range(2)]
    for i in range(2):
        rows.nth(i).locator("input.pin-cb").check()
        wait_trend(page)
    info = trend_info(page)
    assert [d["label"] for d in info["ds"]] == labels  # pinned rows appear as their own lines ...
    assert all(d["type"] == "line" and d["axis"] == "right" for d in info["ds"])  # ... on the right axis only
    assert not info["axes"]["left"]["display"]
    expect(page.locator("#trend-pins")).to_contain_text("Pinned 2/8")
    scopes = [r for r in reqs if r.get("cluster") in labels]
    assert len(scopes) >= 2, reqs  # each pinned row is a /trend request with the row's scope
    shot(page, "35_trend_pinned")
    page.locator(f"{BODY_ROWS}").nth(0).locator("input.pin-cb").uncheck()
    wait_trend(page)
    assert [d["label"] for d in trend_info(page)["ds"]] == labels[1:]
    page.click("#trend-clear")
    wait_trend(page)
    assert page.locator("input.pin-cb:checked").count() == 0 and trend_info(page)["ds"][0]["label"] not in labels
    expect(page.locator("#trend-pins")).to_be_hidden()
    # setting Bars again locks the checkboxes (pins are kept but not drawn)
    pick_metric(page, "bars", "spend")
    wait_rows(page)
    assert page.locator(f"{BODY_ROWS} input.pin-cb").first.is_disabled()
    # at most 8: the 9th checkbox is disabled
    cluster, icp, pain = drill_to_families(page)
    page.goto(purl(cluster, icp, pain, tb="none"))
    wait_rows(page)
    page.click("#toggle-all")
    wait_rows(page)
    boxes = page.locator(f"{BODY_ROWS} input.pin-cb")
    if boxes.count() >= 9:
        for i in range(8):
            boxes.nth(i).check()
        wait_trend(page)
        assert page.locator("input.pin-cb:checked").count() == 8
        assert boxes.nth(8).is_disabled()
        assert trend_info(page)["n"] == 8
        boxes.nth(0).uncheck()
        assert not boxes.nth(8).is_disabled()
    # pins survive a drill and Back
    goto_product(page, "?tb=none")
    first = first_row(page)
    lab = first.get_attribute("data-label")
    first.locator("input.pin-cb").check()
    wait_trend(page)
    first_row(page).locator("a.row-open").click()
    wait_rows(page)
    page.click("#back")
    wait_rows(page)
    wait_trend(page)
    assert page.locator(f"{BODY_ROWS} input.pin-cb:checked").count() == 1
    assert [d["label"] for d in trend_info(page)["ds"]] == [lab]


def test_landing_trend_dual_axis_pins_products_and_client_side_switches(page):
    page.goto("/?platform=meta")
    page.wait_for_selector("[data-testid=products-table] tbody tr[data-key]")
    wait_trend(page)
    info = trend_info(page)
    assert [d["type"] for d in info["ds"]] == ["bar", "line"]
    reqs = trend_requests(page)
    page.locator("#trend-grain [data-grain=week]").click()
    wait_trend(page)
    assert len(trend_info(page)["labels"]) < 40
    page.locator("#trend-split [data-split=stage]").click()
    wait_trend(page)
    assert {d["label"] for d in bars_ds(trend_info(page))} >= {"Testing", "Scaling"}
    assert not reqs, "the landing page derives grain / split / pins from the per-product series it already has"
    page.locator("#trend-grain [data-grain=month]").click()  # month bucketing is client-side here as well
    wait_trend(page)
    assert 5 <= len(trend_info(page)["labels"]) <= 8 and not reqs
    pick_metric(page, "bars", "none")
    page.wait_for_selector("[data-testid=products-table] tbody tr[data-key]")
    idle(page)
    wait_trend(page)
    page.locator("[data-testid=products-table] tbody tr[data-key]").first.locator("input.pin-cb").check()
    wait_trend(page)
    info = trend_info(page)
    assert info["n"] == 1 and info["ds"][0]["type"] == "line"  # one pinned product, a line on the right axis
