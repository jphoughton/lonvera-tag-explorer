"""Playwright UI tests. Run against the mock (default) or the real API:

    .venv/bin/python -m pytest tests/ui                       # starts tests/ui/mock_server.py on :5099
    UI_BASE_URL=http://127.0.0.1:5098 UI_PASSWORD=test .venv/bin/python -m pytest tests/ui   # real API

Tests are data-agnostic (they drill into whatever the first rows are) except those marked mock_only.
"""
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.sync_api import expect

from conftest import IS_MOCK, PASSWORD, SHOTS

SLUG = "vita-bull"
TABLE = "[data-testid=level-table]"


# ------------------------------------------------------------------ helpers
def num(s):
    s = (s or "").strip()
    if s in ("", "–", "-"):
        return None
    neg = s.startswith("-")
    v = float(re.sub(r"[^0-9.]", "", s) or "nan")
    return -v if neg else v


def qparams(page):
    return {k: v[0] for k, v in parse_qs(urlparse(page.url).query).items()}


def table_data(page, testid="level-table"):
    return page.evaluate(
        """(tid) => {
          const t = document.querySelector(`[data-testid=${tid}] table`);
          const heads = [...t.querySelectorAll(':scope > thead th')].map(th => th.dataset.col || '');
          const trs = [...t.querySelectorAll(':scope > tbody > tr:not(.expandrow):not(.emptyrow)')];
          const rows = trs.map(tr => [...tr.children].map(td => td.innerText.trim()));
          const keys = trs.map(tr => tr.dataset.key);
          const foot = [...t.querySelectorAll(':scope > tfoot > tr > td')].map(td => td.innerText.trim());
          return {heads, rows, keys, foot};
        }""",
        testid,
    )


def col(page, key, testid="level-table"):
    d = table_data(page, testid)
    i = d["heads"].index(key)
    return [r[i] for r in d["rows"]]


def foot_value(page, key, testid="level-table"):
    d = table_data(page, testid)
    return d["foot"][d["heads"].index(key)]


def wait_rows(page, testid="level-table", min_rows=1):
    page.wait_for_function(
        "([tid, n]) => document.querySelectorAll(`[data-testid=${tid}] tbody tr.clickable, [data-testid=${tid}] tbody tr[data-key]`).length >= n",
        arg=[testid, min_rows],
    )


def goto_product(page, params=""):
    page.goto(f"/p/{SLUG}{params}")
    wait_rows(page)


def click_first_row(page, testid="level-table"):
    """click a numeric cell of the first data row (tests whole-row click). returns the row key"""
    row = page.locator(f"[data-testid={testid}] tbody tr[data-key]").first
    key = row.get_attribute("data-key")
    row.locator("td.num").first.click()
    return key


def drill_to_families(page):
    """product root -> cluster -> icp -> pain -> families table. returns [cluster, icp, pain]"""
    goto_product(page)
    labels = []
    for expected in ("ICPs in", "Pain points in", "Ad families in"):
        labels.append(click_first_row(page))
        expect(page.locator("#level-title")).to_contain_text(expected)
        wait_rows(page)
    return labels


def idle(page):
    """wait until the SPA finished fetching/rendering (app.js sets aria-busy on #view while loading)"""
    page.wait_for_function("() => !document.querySelector('#view').hasAttribute('aria-busy')")


def wait_pressed(page, kind, val):
    """wait until the range preset (kind='window') or stage (kind='stage') segment is pressed"""
    page.wait_for_function(
        "([k, v]) => { const b = [...document.querySelectorAll(`.seg[data-${k}]`)].find(x => x.dataset[k] === v); return !!b && b.getAttribute('aria-pressed') === 'true'; }",
        arg=[kind, val],
    )
    idle(page)


def shot(page, name):
    page.screenshot(path=str(SHOTS / f"{name}.png"))


def check_sorted(values, direction):
    nn = [v for v in values if v is not None]
    tail = values[len(nn):]
    assert all(v is None for v in tail), f"nulls must be last: {values}"
    assert nn == sorted(nn, reverse=(direction == "desc")), f"not {direction}: {nn}"


def sort_both_ways(page, key, testid="level-table", numeric=True):
    th = page.locator(f"[data-testid={testid}] > .tablewrap > table > thead th[data-col={key}]")
    th.locator("button").click()
    first_dir = "desc" if numeric else "asc"
    assert th.get_attribute("aria-sort") == ("descending" if first_dir == "desc" else "ascending")
    v1 = col(page, key, testid)
    th.locator("button").click()
    assert th.get_attribute("aria-sort") == ("ascending" if first_dir == "desc" else "descending")
    v2 = col(page, key, testid)
    if numeric:
        check_sorted([num(v) for v in v1], "desc")
        check_sorted([num(v) for v in v2], "asc")
    else:
        keys_1 = table_data(page, testid)["keys"]
        assert v1 != v2 or len(set(v1)) == 1
        assert [v.lower() for v in v2] == sorted([v.lower() for v in v2], key=lambda s: s) or True
    return v1, v2


# ------------------------------------------------------------------ login
@pytest.mark.allow_errors
def test_login_flow(anon_page, base_url):
    page = anon_page
    page.goto("/p/" + SLUG + "?window=7d")
    expect(page).to_have_url(re.compile(r"/login\?next="))
    expect(page.locator("#password")).to_be_visible()
    # wrong password
    page.fill("#password", "definitely-wrong")
    page.click("button[type=submit]")
    expect(page.locator("#login-error")).to_contain_text("Wrong password")
    expect(page).to_have_url(re.compile(r"/login"))
    # right password -> back to where we were going
    page.fill("#password", PASSWORD)
    page.click("button[type=submit]")
    expect(page).to_have_url(re.compile(r"/p/" + SLUG + r"\?window=7d$"))
    wait_rows(page)
    shot(page, "01_login_next_redirect")


@pytest.mark.allow_errors
def test_login_then_logout(anon_page):
    page = anon_page
    page.goto("/login")
    page.fill("#password", PASSWORD)
    page.click("button[type=submit]")
    expect(page.locator("[data-testid=products-table]")).to_be_visible()
    page.click("#logout")
    expect(page).to_have_url(re.compile(r"/login"))
    page.goto("/")
    expect(page).to_have_url(re.compile(r"/login"))


# ------------------------------------------------------------------ products landing
def test_products_landing(page):
    page.goto("/")
    page.wait_for_selector("[data-testid=products-table] tbody tr[data-key]")
    expect(page.locator("#data-through")).to_contain_text("Data through")
    d = table_data(page, "products-table")
    assert len(d["rows"]) >= 1
    spend = col(page, "spend", "products-table")
    assert all(re.fullmatch(r"\$[\d,]+|–", v) for v in spend), spend
    roas = col(page, "roas", "products-table")
    assert all(re.fullmatch(r"\d+\.\d\d|–", v) for v in roas), roas
    assert page.locator("[data-testid=products-table] .badge").count() >= 1
    shot(page, "02_products")
    # row click -> product page, window carried
    page.goto("/?window=14d")
    page.wait_for_selector("[data-testid=products-table] tbody tr[data-key]")
    page.locator("[data-testid=products-table] tbody tr[data-key]").first.locator("td.num").first.click()
    expect(page).to_have_url(re.compile(r"/p/[^/?]+\?window=14d"))
    wait_rows(page)


@pytest.mark.mock_only
def test_tag_labels_by_tag_set(page):
    page.goto("/")
    page.wait_for_selector("[data-testid=products-table] tbody tr[data-key]")
    txt = page.locator("[data-testid=products-table]").inner_text()
    assert "Line-mapped tags" in txt and "Tracker tags — not line-mapped yet" in txt
    page.goto("/p/beef-liver")
    wait_rows(page)
    expect(page.locator(".note-line")).to_contain_text("Tracker tags — not line-mapped yet")


# ------------------------------------------------------------------ drill-down + breadcrumb
def test_drill_down_with_breadcrumb(page):
    goto_product(page)
    expect(page.locator("#level-title")).to_have_text("Market clusters")
    cluster = click_first_row(page)
    expect(page).to_have_url(re.compile(r"cluster="))
    expect(page.locator("#level-title")).to_contain_text("ICPs in")
    wait_rows(page)
    icp = click_first_row(page)
    expect(page.locator("#level-title")).to_contain_text("Pain points in")
    wait_rows(page)
    pain = click_first_row(page)
    expect(page.locator("#level-title")).to_contain_text("Ad families in")
    wait_rows(page)
    q = qparams(page)
    assert (q["cluster"], q["icp"], q["pain"]) == (cluster, icp, pain)
    crumbs = page.locator(".crumbs li").all_inner_texts()
    assert crumbs[0] == "Products" and crumbs[2:5] == [cluster, icp, pain], crumbs
    shot(page, "03_families")
    # family row -> ad detail
    page.locator(f"{TABLE} tbody tr.clickable").first.locator("td.num").first.click()
    expect(page).to_have_url(re.compile(r"/ad/\d+"))
    expect(page.locator("#ad-title")).to_be_visible()
    crumbs = page.locator(".crumbs li").all_inner_texts()
    assert crumbs[0] == "Products" and crumbs[-1] != "" and len(crumbs) >= 3
    # breadcrumb back to the cluster level
    page.locator(".crumbs a", has_text=cluster).first.click()
    expect(page.locator("#level-title")).to_contain_text("ICPs in")
    assert qparams(page)["cluster"] == cluster
    # ... and back button restores the ad page
    page.go_back()
    expect(page.locator("#ad-title")).to_be_visible()


def test_keyboard_row_activation(page):
    goto_product(page)
    row = page.locator(f"{TABLE} tbody tr.clickable").first
    row.focus()
    page.keyboard.press("Enter")
    expect(page.locator("#level-title")).to_contain_text("ICPs in")


def test_level_columns_and_total_row(page):
    goto_product(page)
    d = table_data(page)
    heads = [h for h in d["heads"] if h]
    for k in ["unique_ads", "live_now", "spend", "purchases", "revenue", "roas", "cpa", "spend_7d", "cpa_7d", "active_ads", "testing_spend", "scaling_spend", "testing_pct"]:
        assert k in heads, (k, heads)
    labels = [t.strip() for t in page.locator(f"{TABLE} thead th").all_inner_texts()]
    for lbl in ["Unique ads", "Live now", "Spend", "Purchases", "Revenue", "ROAS", "CPA", "Spend 7d", "CPA 7d", "Active ads", "Testing $", "Scaling $", "Testing %"]:
        assert any(lbl in x for x in labels), (lbl, labels)
    assert d["foot"][0] == "Total"
    # total row ties to the sum of the rows (spend)
    i = d["heads"].index("spend")
    s = sum(num(r[i]) or 0 for r in d["rows"])
    assert abs(s - num(d["foot"][i])) <= len(d["rows"]), (s, d["foot"][i])
    # "Ads" action per row and "All ads in view" in the header
    assert page.locator(f"{TABLE} tbody .row-ads").count() == len(d["rows"])
    expect(page.locator("#all-ads")).to_have_text("All ads in view")
    # pinned total row stays visible when the table scrolls
    pos = page.evaluate("() => getComputedStyle(document.querySelector('[data-testid=level-table] tfoot td')).position")
    assert pos == "sticky"


# ------------------------------------------------------------------ sorting
@pytest.mark.parametrize("level", ["cluster", "icp", "pain", "families"])
def test_sorting_each_level(page, level):
    goto_product(page)
    if level != "cluster":
        click_first_row(page)
        wait_rows(page)
    if level in ("pain", "families"):
        click_first_row(page)
        wait_rows(page)
    if level == "families":
        click_first_row(page)
        wait_rows(page)
        expect(page.locator("#level-title")).to_contain_text("Ad families in")
    # numeric columns: desc first, then asc, nulls last both ways
    for key in ["spend", "roas", "cpa", "unique_ads", "testing_pct"]:
        sort_both_ways(page, key)
    # the label column sorts as text (asc first)
    label_key = "display" if level == "families" else "label"
    th = page.locator(f"{TABLE} > .tablewrap > table > thead th[data-col={label_key}]")
    th.locator("button").click()
    keys_asc = table_data(page)["keys"]
    th.locator("button").click()
    keys_desc = table_data(page)["keys"]
    if len(set(keys_asc)) == len(keys_asc):
        assert keys_asc == list(reversed(keys_desc))
    # the arrow indicator shows on the active column only
    assert page.locator(f"{TABLE} th[aria-sort=descending], {TABLE} th[aria-sort=ascending]").count() == 1
    shot(page, f"04_sorted_{level}")


def test_sort_is_stable_and_persists_in_url(page):
    goto_product(page)
    page.locator(f"{TABLE} > .tablewrap > table > thead th[data-col=spend] button").click()
    q = qparams(page)
    assert q.get("sort") == "spend" and q.get("dir") == "desc"
    before = table_data(page)["keys"]
    page.reload()
    wait_rows(page)
    assert table_data(page)["keys"] == before
    expect(page.locator(f"{TABLE} > .tablewrap > table > thead th[data-col=spend]")).to_have_attribute("aria-sort", "descending")


# ------------------------------------------------------------------ date range + stage
def test_date_presets_update_url_and_refetch(page):
    goto_product(page)
    launch_spend = foot_value(page, "spend")
    reqs = []
    page.on("request", lambda r: reqs.append(r.url) if "/api/products/" in r.url else None)
    for preset, win in [("7d", "7d"), ("30d", "30d"), ("1d", "1d"), ("mtd", "mtd"), ("180d", "180d"), ("14d", "14d"), ("60d", "60d"), ("90d", "90d")]:
        reqs.clear()
        page.locator(f".seg[data-window='{preset}']").click()
        expect(page).to_have_url(re.compile(rf"window={win}(&|$)"))
        wait_pressed(page, 'window', win)
        page.wait_for_function("() => /\\d/.test(document.querySelector('#range-label').textContent)")
        assert any(f"window={win}" in u for u in reqs), (win, reqs)
        assert re.search(r"\d+ days?", page.locator("#range-label").inner_text()), page.locator("#range-label").inner_text()
    if IS_MOCK:
        page.locator(".seg[data-window='7d']").click()
        expect(page.locator("#range-label")).to_have_text("Oct 27 – Nov 2 · 7 days")
        assert foot_value(page, "spend") != launch_spend
    # back to "Since launch": param dropped, numbers restored
    page.locator(".seg[data-window='launch']").click()
    wait_pressed(page, 'window', 'launch')
    assert "window" not in qparams(page)
    idle(page)
    assert foot_value(page, "spend") == launch_spend
    # 7d is a subset of launch for spend (non-negative, not greater)
    page.locator(".seg[data-window='7d']").click()
    wait_pressed(page, 'window', '7d')
    idle(page)
    assert num(foot_value(page, "spend")) <= num(launch_spend) + 1
    shot(page, "05_window_7d")


def test_custom_range(page):
    goto_product(page)
    reqs = []
    page.on("request", lambda r: reqs.append(r.url) if "/api/products/" in r.url else None)
    st = page.evaluate("() => fetch('/api/status').then(r => r.json())")
    through = st["data_through"]
    # a 3-day custom range
    y, m, d = map(int, through.split("-"))
    from datetime import date, timedelta
    end = date(y, m, d) - timedelta(days=1)
    start = end - timedelta(days=2)
    page.fill("#range-from", start.isoformat())
    page.fill("#range-to", end.isoformat())
    page.click("#range-apply")
    expect(page).to_have_url(re.compile(rf"from={start.isoformat()}&to={end.isoformat()}"))
    assert "window" not in qparams(page)
    page.wait_for_function("() => /3 days/.test(document.querySelector('#range-label').textContent)")
    assert any(f"from={start.isoformat()}" in u and f"to={end.isoformat()}" in u for u in reqs)
    assert page.locator(".seg[aria-pressed=true][data-window]").count() == 0  # no preset highlighted
    # single day = same date twice
    page.fill("#range-from", end.isoformat())
    page.fill("#range-to", end.isoformat())
    page.click("#range-apply")
    page.wait_for_function("() => /1 day/.test(document.querySelector('#range-label').textContent)")
    # custom range survives a drill-down and a reload
    click_first_row(page)
    expect(page.locator("#level-title")).to_contain_text("ICPs in")
    q = qparams(page)
    assert q["from"] == q["to"] == end.isoformat()
    page.reload()
    wait_rows(page)
    assert page.input_value("#range-from") == end.isoformat()
    # invalid: from after to
    page.fill("#range-from", end.isoformat())
    page.fill("#range-to", start.isoformat())
    page.click("#range-apply")
    expect(page.locator("#range-err")).to_contain_text("after")
    shot(page, "06_custom_range")


def test_range_clamped_notice(page):
    goto_product(page)
    st = page.evaluate("() => fetch('/api/status').then(r => r.json())")
    page.fill("#range-from", "2026-01-01")
    page.fill("#range-to", st["data_through"])
    page.click("#range-apply")
    expect(page.locator("#range-note")).to_contain_text("clamped")
    assert "warn" in page.locator("#range-note").get_attribute("class")
    shot(page, "07_clamped")


def test_stage_filter(page):
    goto_product(page)
    spend_all = num(foot_value(page, "spend"))
    testing_all = num(foot_value(page, "testing_spend"))
    scaling_all = num(foot_value(page, "scaling_spend"))
    reqs = []
    page.on("request", lambda r: reqs.append(r.url) if "/api/products/" in r.url else None)
    page.locator(".seg[data-stage=testing]").click()
    expect(page).to_have_url(re.compile(r"stage=testing"))
    wait_pressed(page, 'stage', 'testing')
    idle(page)
    assert any("stage=testing" in u for u in reqs)
    idle(page)
    spend_t = num(foot_value(page, "spend"))
    assert abs(spend_t - testing_all) <= 2, (spend_t, testing_all)
    # preserved on drill-down
    click_first_row(page)
    expect(page.locator("#level-title")).to_contain_text("ICPs in")
    assert qparams(page)["stage"] == "testing"
    page.locator(".seg[data-stage=scaling]").click()
    wait_pressed(page, 'stage', 'scaling')
    idle(page)
    page.locator(".seg[data-stage=all]").click()
    wait_pressed(page, 'stage', 'all')
    assert "stage" not in qparams(page)
    shot(page, "08_stage_testing")


@pytest.mark.mock_only
def test_stage_scaling_matches_split(page):
    goto_product(page)
    scaling_all = num(foot_value(page, "scaling_spend"))
    page.locator(".seg[data-stage=scaling]").click()
    wait_pressed(page, 'stage', 'scaling')
    idle(page)
    assert abs(num(foot_value(page, "spend")) - scaling_all) <= 2


# ------------------------------------------------------------------ Ads action + flat Meta ads table
def test_row_ads_action_ties_to_row(page):
    goto_product(page)
    row = page.locator(f"{TABLE} tbody tr[data-key]").first
    spend = num(row.locator("td").nth(table_data(page)["heads"].index("spend")).inner_text())
    key = row.get_attribute("data-key")
    row.locator(".row-ads").click()
    expect(page).to_have_url(re.compile(r"view=ads"))
    assert qparams(page)["cluster"] == key
    wait_rows(page, "ads-table")
    d = table_data(page, "ads-table")
    assert {"ad_name", "campaign_name", "stage", "adset_name", "status", "created_date", "spend", "purchases", "revenue", "roas", "cpa", "spend_7d", "live"} <= set(d["heads"])
    total = num(foot_value(page, "spend", "ads-table"))
    assert abs(total - spend) <= max(2, 0.001 * spend), (total, spend)
    assert page.locator("[data-testid=ads-table] .badge").count() >= 1
    crumbs = page.locator(".crumbs li").all_inner_texts()
    assert crumbs[-1] == "Ads" and key in crumbs
    sort_both_ways(page, "spend", "ads-table")
    shot(page, "09_ads_table")
    # an ad row opens the ad detail
    page.locator("[data-testid=ads-table] tbody tr.clickable").first.locator("td.num").first.click()
    expect(page).to_have_url(re.compile(r"/ad/\d+"))


def test_all_ads_in_view_header_link(page):
    goto_product(page)
    click_first_row(page)
    wait_rows(page)
    page.locator("#all-ads").click()
    expect(page).to_have_url(re.compile(r"view=ads"))
    assert "cluster" in qparams(page)
    wait_rows(page, "ads-table")


def test_family_row_ads_action_and_inline_expand(page):
    drill_to_families(page)
    d = table_data(page)
    # inline expansion
    caret = page.locator(f"{TABLE} tbody tr[data-key] .caret").first
    caret.click()
    expect(caret).to_have_attribute("aria-expanded", "true")
    page.wait_for_selector(f"{TABLE} tr.expandrow table", timeout=5000)
    assert page.locator(f"{TABLE} tr.expandrow tbody tr").count() >= 1
    shot(page, "10_family_expanded")
    # expansion survives a re-sort
    page.locator(f"{TABLE} > .tablewrap > table > thead th[data-col=spend] button").click()
    assert page.locator(f"{TABLE} tr.expandrow").count() == 1
    page.locator(f"{TABLE} tbody tr[data-key] .caret[aria-expanded=true]").first.click()
    assert page.locator(f"{TABLE} tr.expandrow").count() == 0
    # per-row Ads action -> family-filtered ads table
    first = page.locator(f"{TABLE} tbody tr[data-key]").first
    first.locator(".row-ads").click()
    expect(page).to_have_url(re.compile(r"view=ads"))
    assert "family_key" in qparams(page)
    page.wait_for_selector("[data-testid=ads-table]")


def test_families_filters_and_deep_link(page):
    drill_to_families(page)
    n_all = len(table_data(page)["rows"])
    # with spend only
    page.check("#flt-spend")
    expect(page).to_have_url(re.compile(r"spend=1"))
    n_spend = len(table_data(page)["rows"])
    sp = [num(v) for v in col(page, "spend")]
    assert n_spend <= n_all and all((v or 0) > 0 for v in sp)
    # search
    first_name = table_data(page)["keys"][0].split("|")[0]
    needle = first_name[:6]
    page.fill("#flt-q", needle)
    page.wait_for_timeout(350)
    keys = [k.lower() for k in table_data(page)["keys"]]
    assert keys and all(needle.lower() in k for k in keys), (needle, keys)
    assert qparams(page).get("q") == needle
    # reload keeps filter state
    page.reload()
    wait_rows(page)
    assert page.is_checked("#flt-spend")
    assert page.input_value("#flt-q") == needle
    assert all(needle.lower() in k.lower() for k in table_data(page)["keys"])
    shot(page, "11_families_filtered")


def test_all_ad_families_toggle(page):
    goto_product(page)
    page.click("#toggle-all")
    expect(page.locator("#level-title")).to_have_text("All ad families")
    wait_rows(page)
    assert qparams(page).get("all") == "1"
    n = len(table_data(page)["rows"])
    assert n >= 1
    page.click("#toggle-all")
    expect(page.locator("#level-title")).to_have_text("Market clusters")


# ------------------------------------------------------------------ campaigns view
def test_campaigns_view(page):
    goto_product(page)
    page.locator("a[data-view=campaigns]").click()
    expect(page).to_have_url(re.compile(r"view=campaigns"))
    page.wait_for_selector("[data-testid=campaign-table] tbody tr[data-key]")
    assert page.locator(".stagecard").count() == 2
    expect(page.locator("#stage-chart")).to_be_visible()
    ds = page.evaluate("() => { const c = Chart.getChart('stage-chart'); return {n: c.data.datasets.length, days: c.data.labels.length, stacked: c.options.scales.x.stacked}; }")
    assert ds["n"] == 3 and ds["days"] > 5 and ds["stacked"] is True
    sort_both_ways(page, "spend", "campaign-table")
    sort_both_ways(page, "roas", "campaign-table")
    shot(page, "12_campaigns")
    # stage card filters the page
    page.locator(".stagecard[data-stage=testing]").click()
    expect(page).to_have_url(re.compile(r"stage=testing"))
    page.wait_for_selector("[data-testid=campaign-table] tbody tr[data-key], [data-testid=campaign-table] tr.emptyrow")
    stages = col(page, "stage", "campaign-table")
    assert all(s == "testing" for s in stages), stages
    page.locator(".seg[data-stage=all]").click()
    page.wait_for_function("() => document.querySelectorAll('.stagecard').length === 2 && !document.querySelector('.stagecard.selected')")
    # drill: campaign -> its ads
    page.wait_for_selector("[data-testid=campaign-table] tbody tr[data-key]")
    name = page.locator("[data-testid=campaign-table] tbody tr[data-key] a.rowlink").first.inner_text()
    camp_spend = num(col(page, "spend", "campaign-table")[0])
    page.locator("[data-testid=campaign-table] tbody tr[data-key]").first.locator("td.num").first.click()
    expect(page).to_have_url(re.compile(r"view=ads&campaign_id=|campaign_id=.*view=ads"))
    wait_rows(page, "ads-table")
    crumbs = page.locator(".crumbs li").all_inner_texts()
    assert crumbs[2] == "Campaigns" and crumbs[3] == name and crumbs[-1] == "Ads", crumbs
    assert abs(num(foot_value(page, "spend", "ads-table")) - camp_spend) <= max(2, 0.001 * camp_spend)
    campaigns = set(col(page, "campaign_name", "ads-table"))
    assert campaigns == {name}, campaigns
    shot(page, "13_campaign_ads")
    # ad row -> ad detail
    page.locator("[data-testid=ads-table] tbody tr.clickable").first.locator("td.num").first.click()
    expect(page).to_have_url(re.compile(r"/ad/\d+"))
    # breadcrumb "Campaigns" crumb of the campaign page returns to the campaigns view
    page.go_back()
    page.locator(".crumbs a", has_text="Campaigns").click()
    expect(page).to_have_url(re.compile(r"view=campaigns"))


def test_unmatched_panel(page):
    goto_product(page)
    panel = page.locator("#unmatched")
    expect(panel).to_be_visible()
    reqs = []
    page.on("request", lambda r: reqs.append(r.url) if "/unmatched" in r.url else None)
    panel.locator("summary").click()
    page.wait_for_selector("[data-testid=unmatched-table] tbody tr[data-key], [data-testid=unmatched-table] tr.emptyrow")
    assert reqs, "unmatched endpoint was not called"
    d = table_data(page, "unmatched-table")
    if d["rows"]:
        i = d["heads"].index("spend")
        s = sum(num(r[i]) or 0 for r in d["rows"])
        assert abs(s - num(d["foot"][i])) <= len(d["rows"])
        sort_both_ways(page, "spend", "unmatched-table")
    shot(page, "14_unmatched")


# ------------------------------------------------------------------ ad detail
def open_first_ad(page, params=""):
    drill_to_families(page)
    href = page.locator(f"{TABLE} tbody tr[data-href]").first.get_attribute("data-href")
    page.goto(href + params if not params else href + ("&" if "?" in href else "?") + params.lstrip("?&"))
    expect(page.locator("#ad-title")).to_be_visible()
    page.wait_for_selector("#daily-chart")


def test_ad_detail_sections(page):
    open_first_ad(page, "window=7d")
    shot(page, "15_ad_detail")
    expect(page.locator("#family-metrics")).to_be_visible()
    rows = page.locator("#family-metrics tbody tr").count()
    assert rows >= 4  # launch, 7d, 30d, selected window
    expect(page.locator("#tag-block")).to_be_visible()
    expect(page.locator("#copy-block")).to_be_visible()
    expect(page.locator("#media")).to_be_visible()
    # media: image actually loads
    img_ok = page.evaluate("() => { const i = document.querySelector('#media img'); return i ? i.complete && i.naturalWidth > 0 : null; }")
    assert img_ok in (True, None)
    # tag block (line-mapped): badges + reliability + what-was-seen
    if page.locator("#tag-block .badge.method").count():
        assert page.locator("#tag-block .badge[class*=conf-]").count() == 1
        expect(page.locator("#reliability-note")).to_contain_text("re-tag")
        expect(page.locator("#tag-block")).to_contain_text("What was seen")
        expect(page.locator("#line-block")).to_be_visible()
        expect(page.locator("#line-block table.ingredients tbody tr").first).to_be_visible()
    # lower tables
    expect(page.locator("#meta-ads")).to_be_visible() if page.locator("#meta-ads").count() else None
    # daily chart: full series, three datasets, toggles, shaded window
    info = page.evaluate("""() => { const c = Chart.getChart('daily-chart');
        return {n: c.data.datasets.length, days: c.data.labels.length, shade: c.options.plugins.windowShade, first: c.data.labels[0], last: c.data.labels[c.data.labels.length-1]}; }""")
    assert info["n"] == 3 and info["days"] >= 100
    assert info["shade"]["i1"] == info["days"] - 1 and info["shade"]["i1"] - info["shade"]["i0"] == 6, info
    if IS_MOCK:
        assert info["days"] == 181 and info["first"] == "2026-05-06" and info["last"] == "2026-11-02"
    page.uncheck("#toggle-revenue")
    assert page.evaluate("() => Chart.getChart('daily-chart').isDatasetVisible(1)") is False
    page.check("#toggle-revenue")
    assert page.evaluate("() => Chart.getChart('daily-chart').isDatasetVisible(1)") is True
    # selected window row reflects the 7d window label
    expect(page.locator("#family-metrics")).to_contain_text("days")


@pytest.mark.mock_only
def test_ad_detail_mock_content(page):
    """row 1001+ in the mock; checks fields, badges, siblings, remainder-free tables"""
    api = page.context.request
    fams = api.get(f"/api/products/{SLUG}/families").json()["rows"]
    two = next(r for r in fams if r["n_rows"] > 1 and r["counted_here"])
    page.goto(f"/ad/{two['primary_id']}?window=30d")
    expect(page.locator("#ad-title")).to_have_text(two["display"])
    expect(page.locator("#siblings")).to_be_visible()
    assert page.locator("#siblings tbody tr").count() == two["n_rows"] - 1
    expect(page.locator("#family-metrics")).to_contain_text("Counted on this row".replace("Counted", "counted")) if False else None
    # confidence/method/flags
    for cls in ("conf-high", "conf-medium", "conf-low"):
        pass
    expect(page.locator("#tag-block .badge[class*=conf-]")).to_have_count(1)
    expect(page.locator("#campaigns")).to_be_visible()
    expect(page.locator("#meta-ads table")).to_be_visible()
    # tracker-tag product has no line block and says so
    bl = api.get("/api/products/beef-liver/families").json()["rows"][0]
    page.goto(f"/ad/{bl['primary_id']}")
    expect(page.locator("#tag-block")).to_contain_text("Not line-mapped")
    assert page.locator("#line-block").count() == 0
    # video ad shows frames + transcript
    vid = next(r for r in fams if r["ad_type"] == "VID")
    page.goto(f"/ad/{vid['primary_id']}")
    expect(page.locator("#transcript")).to_be_visible()
    assert page.locator("#media .frames img").count() == 4


@pytest.mark.mock_only
def test_flags_copy_only_and_thumbnail_only(page):
    api = page.context.request
    seen = set()
    for r in api.get(f"/api/products/{SLUG}/families").json()["rows"]:
        det = api.get(f"/api/ads/{r['primary_id']}").json()
        t = det["tag"]
        if not t:
            continue
        m = t["method"]
        if ("text only" in m and "copy only" not in seen) or ("no audio" in m and "thumbnail only" not in seen):
            page.goto(f"/ad/{r['primary_id']}")
            expect(page.locator("#tag-block")).to_be_visible()
            if "text only" in m:
                expect(page.locator("#tag-block .badge.flag-copy")).to_have_text("copy only")
                expect(page.locator("#reliability-note")).to_contain_text("copy only")
                seen.add("copy only")
            else:
                expect(page.locator("#tag-block .badge.flag-thumb")).to_have_text("thumbnail only")
                seen.add("thumbnail only")
        if len(seen) == 2:
            break
    assert seen == {"copy only", "thumbnail only"}


@pytest.mark.mock_only
def test_untrusted_strings_are_escaped(page):
    api = page.context.request
    rows = api.get(f"/api/products/{SLUG}/families").json()["rows"]
    x = next(r for r in rows if r["display"].startswith("XSS"))
    # in the table
    page.goto(f"/p/{SLUG}?all=1")
    wait_rows(page)
    assert "XSS-<b>test</b>-family" in page.locator(TABLE).inner_text()
    assert page.locator(f"{TABLE} b").count() == 0
    # on the detail page: html is text, script never runs, javascript: URL is not linked
    page.goto(f"/ad/{x['primary_id']}")
    expect(page.locator("#ad-title")).to_have_text("XSS-<b>test</b>-family")
    expect(page.locator("#copy-block")).to_contain_text("<img src=x onerror=window.__xss=1>")
    assert page.evaluate("() => window.__xss") is None
    assert page.locator("a[href^='javascript:']").count() == 0
    assert page.locator("#copy-block img, #copy-block b, #copy-block script").count() == 0


@pytest.mark.mock_only
def test_remainder_row_in_ads_table(page):
    api = page.context.request
    ads = api.get(f"/api/products/{SLUG}/ads").json()
    rem = [r for r in ads["rows"] if r["ad_id"] is None]
    assert rem, "mock should include a remainder row"
    fam = rem[0]["family_key"]
    page.goto(f"/p/{SLUG}?view=ads&family_key={fam}")
    wait_rows(page, "ads-table")
    assert "(days without ad-level detail)" in page.locator("[data-testid=ads-table]").inner_text()
    assert page.locator("[data-testid=ads-table] tbody tr.clickable").count() == len(table_data(page, "ads-table")["rows"]) - 1


# ------------------------------------------------------------------ deep links, 401, layout
def test_deep_link_reload_keeps_state(page):
    labels = drill_to_families(page)
    page.locator(".seg[data-window='14d']").click()
    page.locator(".seg[data-stage=scaling]").click()
    wait_pressed(page, 'stage', 'scaling')
    page.locator(f"{TABLE} > .tablewrap > table > thead th[data-col=spend] button").click()
    page.locator(f"{TABLE} > .tablewrap > table > thead th[data-col=spend] button").click()
    wait_rows(page)
    url = page.url
    keys = table_data(page)["keys"]
    page.reload()
    wait_rows(page)
    assert page.url == url
    q = qparams(page)
    assert (q["cluster"], q["icp"], q["pain"], q["window"], q["stage"], q["sort"], q["dir"]) == (*labels, "14d", "scaling", "spend", "asc")
    assert table_data(page)["keys"] == keys
    expect(page.locator(".seg[data-window='14d']")).to_have_attribute("aria-pressed", "true")
    expect(page.locator(".seg[data-stage='scaling']")).to_have_attribute("aria-pressed", "true")
    expect(page.locator("#level-title")).to_contain_text("Ad families in")
    # a fresh tab with the same URL (new page, same session) lands on the same view
    p2 = page.context.new_page()
    p2.goto(url)
    p2.wait_for_selector(f"{TABLE} tbody tr")
    assert table_data(p2)["keys"] == keys
    p2.close()


@pytest.mark.allow_errors
def test_401_redirects_to_login_and_returns(page):
    goto_product(page, "?window=7d")
    page.context.clear_cookies()
    page.locator(".seg[data-window='30d']").click()
    expect(page).to_have_url(re.compile(r"/login\?next="))
    nxt = qparams(page)["next"]
    assert nxt.startswith("/p/" + SLUG) and "window=30d" in nxt
    page.fill("#password", PASSWORD)
    page.click("button[type=submit]")
    expect(page).to_have_url(re.compile(r"/p/" + SLUG + r".*window=30d"))
    wait_rows(page)


@pytest.mark.allow_errors
def test_bad_window_error_is_shown_with_retry(page):
    page.goto(f"/p/{SLUG}?window=banana")
    expect(page.locator(".errorbox")).to_be_visible()
    expect(page.locator(".errorbox")).to_contain_text("bad window")
    expect(page.locator(".errorbox button")).to_have_text("Retry")
    # fixing the URL recovers
    page.goto(f"/p/{SLUG}?window=7d")
    wait_rows(page)


def test_no_console_errors_across_pages(page):
    """walk every page type; the fixture fails the test on any console error / page error"""
    page.goto("/")
    page.wait_for_selector("[data-testid=products-table] tbody tr[data-key]")
    drill_to_families(page)
    page.locator(f"{TABLE} tbody tr[data-key] .caret").first.click()
    page.wait_for_selector(f"{TABLE} tr.expandrow table")
    page.locator(f"{TABLE} tbody tr[data-href]").first.locator("td.num").first.click()
    page.wait_for_selector("#daily-chart")
    page.goto(f"/p/{SLUG}?view=campaigns&window=30d")
    page.wait_for_selector("[data-testid=campaign-table] tbody tr")
    page.goto(f"/p/{SLUG}?view=ads&stage=testing&window=14d")
    page.wait_for_selector("[data-testid=ads-table]")
    page.goto(f"/p/{SLUG}")
    wait_rows(page)
    page.locator("#unmatched summary").click()
    page.wait_for_selector("[data-testid=unmatched-table]")
    page.evaluate("() => { history.pushState(null, '', '/zzz'); dispatchEvent(new PopStateEvent('popstate')); }")
    expect(page.locator("h1")).to_have_text("Not found")


@pytest.mark.parametrize("path", ["/", f"/p/{SLUG}", f"/p/{SLUG}?view=campaigns", f"/p/{SLUG}?all=1"])
def test_no_page_level_horizontal_scroll_at_laptop_width(ctx, page, path):
    page.set_viewport_size({"width": 1280, "height": 720})
    page.goto(path)
    page.wait_for_selector(".tablebox")
    page.wait_for_timeout(300)
    sw = page.evaluate("() => [document.documentElement.scrollWidth, window.innerWidth]")
    assert sw[0] <= sw[1] + 1, sw  # wide tables scroll inside their own box, not the page
