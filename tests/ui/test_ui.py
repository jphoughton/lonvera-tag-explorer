"""Playwright UI tests. Run against the mock (default) or the real API:

    .venv/bin/python -m pytest tests/ui                       # starts tests/ui/mock_server.py on :5099
    UI_BASE_URL=http://127.0.0.1:5098 UI_USERNAME=JoshHoughton UI_PASSWORD=test .venv/bin/python -m pytest tests/ui   # real API

Tests are data-agnostic (they drill into whatever the first rows are) except those marked mock_only.
"""
import re
from datetime import date, timedelta
from urllib.parse import parse_qs, urlencode, urlparse

import pytest
from playwright.sync_api import expect

from conftest import IS_MOCK, PASSWORD, SHOTS, USERNAME

SLUG = "vita-bull"
TABLE = "[data-testid=level-table]"
BODY_ROWS = f"{TABLE} > .tablewrap > table > tbody > tr[data-key]:not(.pseudo)"
HEAD = f"{TABLE} > .tablewrap > table > thead"
STAGE_COLS = ["testing_spend", "scaling_spend", "testing_pct"]
TRAFFIC_COLS = ["impressions", "link_clicks", "ctr", "cpc"]
ADVANCED_COLS = ["cpm", "conv_rate"]
METRIC_COLS = ["unique_ads", "live_now", "spend", "spend_share", *TRAFFIC_COLS, "purchases", "revenue", "roas", "cpa"]


# ------------------------------------------------------------------ helpers
def num(s):
    s = ((s or "").strip().split("\n")[0]).strip()  # first line only: compare cells have a second "vs ..." line
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
          const trs = [...t.querySelectorAll(':scope > tbody > tr:not(.expandrow):not(.emptyrow):not(.pseudo)')];
          const rows = trs.map(tr => [...tr.children].map(td => td.innerText.trim()));
          const keys = trs.map(tr => tr.dataset.key);
          const labels = trs.map(tr => tr.dataset.label);
          const levels = trs.map(tr => { const m = /lv(\\d)/.exec(tr.className); return m ? +m[1] : null; });
          const frows = [...t.querySelectorAll(':scope > tfoot > tr')];
          const foot = frows.length ? [...frows[0].children].map(td => td.innerText.trim()) : [];
          const footRows = frows.map(tr => ({cls: tr.className, cells: [...tr.children].map(td => td.innerText.trim())}));
          return {heads, rows, keys, labels, levels, foot, footRows};
        }""",
        testid,
    )


def col(page, key, testid="level-table"):
    d = table_data(page, testid)
    i = d["heads"].index(key)
    return [r[i] for r in d["rows"]]


def has_col(page, key, testid="level-table"):
    return key in table_data(page, testid)["heads"]


def foot_value(page, key, testid="level-table"):
    d = table_data(page, testid)
    return d["foot"][d["heads"].index(key)]


def idle(page):
    """wait until the SPA finished fetching/rendering (app.js sets aria-busy on #view while loading)"""
    page.wait_for_function("() => !document.querySelector('#view').hasAttribute('aria-busy')")


def wait_rows(page, testid="level-table", min_rows=1):
    page.wait_for_function(
        "([tid, n]) => document.querySelectorAll(`[data-testid=${tid}] tbody tr[data-key]:not(.pseudo)`).length >= n",
        arg=[testid, min_rows],
    )
    idle(page)


def wait_pressed(page, kind, val):
    """wait until the range preset (kind='window') or stage chip (kind='stage') is pressed"""
    page.wait_for_function(
        "([k, v]) => { const b = [...document.querySelectorAll(`[data-${k}]`)].find(x => x.dataset[k] === v); return !!b && b.getAttribute('aria-pressed') === 'true'; }",
        arg=[kind, val],
    )
    idle(page)


def purl(cluster=None, icp=None, pain=None, **extra):
    """/p/<slug>?... with properly encoded labels (they contain '&')"""
    q = {k: v for k, v in dict(cluster=cluster, icp=icp, pain=pain, **extra).items() if v is not None}
    return f"/p/{SLUG}" + ("?" + urlencode(q) if q else "")


def goto_product(page, params=""):
    page.goto(f"/p/{SLUG}{params}")
    wait_rows(page)


def goto_home(page, params=""):
    """the Meta landing page (Products table) of the Marketing metrics section"""
    page.goto("/?platform=meta" + ("&" + params[1:] if params.startswith("?") else params))
    page.locator("[data-testid=products-table] tbody tr").first.wait_for()


def first_row(page):
    return page.locator(BODY_ROWS).first


def open_first_row(page):
    """click the 'Open ›' link of the first row (navigates). returns the row label"""
    row = first_row(page)
    label = row.get_attribute("data-label")
    row.locator("a.row-open").click()
    return label


def drill_to_families(page):
    """product root -> cluster -> icp -> pain -> families table (via Open ›). returns [cluster, icp, pain]"""
    goto_product(page)
    labels = []
    for expected in ("ICPs in", "Pain points in", "Creatives in"):
        labels.append(open_first_row(page))
        expect(page.locator("#level-title")).to_contain_text(expected)
        wait_rows(page)
    return labels


def shot(page, name):
    page.screenshot(path=str(SHOTS / f"{name}.png"))


def check_sorted(values, direction):
    nn = [v for v in values if v is not None]
    tail = values[len(nn):]
    assert all(v is None for v in tail), f"nulls must be last: {values}"
    assert nn == sorted(nn, reverse=(direction == "desc")), f"not {direction}: {nn}"


def sort_both_ways(page, key, testid="level-table", numeric=True):
    th = page.locator(f"[data-testid={testid}] > .tablewrap > table > thead th[data-col={key}]")
    th.locator("button.thbtn").click()
    first_dir = "desc" if numeric else "asc"
    assert th.get_attribute("aria-sort") == ("descending" if first_dir == "desc" else "ascending")
    v1 = col(page, key, testid)
    th.locator("button.thbtn").click()
    assert th.get_attribute("aria-sort") == ("ascending" if first_dir == "desc" else "descending")
    v2 = col(page, key, testid)
    if numeric:
        check_sorted([num(v) for v in v1], "desc")
        check_sorted([num(v) for v in v2], "asc")
    return v1, v2


def level_rows(page):
    """[(level, label, spend, share)] of the visible tree rows"""
    d = table_data(page)
    si, hi = d["heads"].index("spend"), d["heads"].index("spend_share")
    return [(lv, lab, num(r[si]), num(r[hi])) for lv, lab, r in zip(d["levels"], d["labels"], d["rows"])]


def delta_pct(cell_text):
    """'▲12%' / '▼3.5%' in the 2nd line of a compare cell -> signed float, None when absent"""
    m = re.search(r"([▲▼])(\d+(?:\.\d+)?)%", cell_text or "")
    if not m:
        return None
    v = float(m.group(2))
    return v if m.group(1) == "▲" else -v


def trend_info(page):
    """state of the trend chart (Chart.js instance #trend-chart): datasets with type / axis, and both y axes"""
    return page.evaluate("""() => { const c = window.Chart && Chart.getChart('trend-chart'); if (!c) return null;
        const ax = (k) => { const s = c.scales[k]; if (!s) return null; const o = s.options; return {display: o.display !== false, title: o.title && o.title.display ? o.title.text : null, color: o.title ? o.title.color : null, tick: o.ticks ? o.ticks.color : null, stacked: !!o.stacked,
            ticks: s.ticks.map(t => t.label)}; };
        return {n: c.data.datasets.length, labels: c.data.labels.slice(),
                stacked: !!c.options.scales.y.stacked,
                axes: {left: ax('y'), right: ax('y1')},
                ds: c.data.datasets.map((d, i) => ({label: d.label, type: d.type, axis: d.yAxisID === 'y1' ? 'right' : 'left', fill: !!d.fill, dash: d.borderDash || null, stack: d.stack || null, hidden: !c.isDatasetVisible(i), data: d.data.slice(), color: d.borderColor})),
                shades: c.options.plugins.windowShade.shades}; }""")


def bars_ds(info):
    return [d for d in info["ds"] if d["type"] == "bar" and not d["label"].startswith("B ")]


def line_ds(info):
    return [d for d in info["ds"] if d["type"] == "line" and not d["label"].startswith("B ")]


def pick_metric(page, which, key):
    """which = 'bars' | 'line': choose a metric in the panel's selector (the page may re-render)"""
    page.select_option(f"#trend-{which}", key)


def wait_trend(page):
    """the trend chart exists, has data and is not loading"""
    page.wait_for_function("() => { const c = window.Chart && Chart.getChart('trend-chart'); const s = document.querySelector('#trend-status'); return !!c && c.data.datasets.length > 0 && s && s.textContent === ''; }")


def trend_idle(page):
    """the trend panel finished loading (it may legitimately have no series, e.g. Bars = Line = None)"""
    page.wait_for_function("() => { const s = document.querySelector('#trend-status'); return !!s && s.textContent === '' && !!(window.Chart && Chart.getChart('trend-chart')); }")


def trend_requests(page):
    """collect /trend API requests; returns the (mutable) list of parsed query dicts"""
    reqs = []
    page.on("request", lambda r: reqs.append({k: v[0] for k, v in parse_qs(urlparse(r.url).query).items()}) if "/trend" in r.url and "/api/" in r.url else None)
    return reqs


def chip(page, stage):
    return page.locator(f".schip[data-stage={stage}]")


def unc_visible(page):
    return chip(page, "unclassified").is_visible()


def stage_all_param(page):
    """the stage= value when Testing and Scaling are both on: omitted, unless an (off) Unclassified chip is showing"""
    if unc_visible(page):
        return "testing,scaling" if chip(page, "unclassified").get_attribute("aria-pressed") == "false" else None
    return None


def only_testing_scaling(page):
    """real data can still hold unclassified days (shows its own chip): switch that chip off so Testing/Scaling behave in isolation"""
    if unc_visible(page) and chip(page, "unclassified").get_attribute("aria-pressed") == "true":
        chip(page, "unclassified").click()
        idle(page)


def cards(page):
    return page.locator(".stagecard[data-stage=testing], .stagecard[data-stage=scaling]")


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
    expect(page.locator("h1")).to_have_text("All platforms")                                          # a login lands on the default view: the All platforms roll-up
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
    expect(page.locator("h1")).to_have_text("All platforms")
    expect(page.locator("#whoami")).to_have_text(USERNAME)  # the signed-in user's name sits next to Log out
    page.click("#logout")
    expect(page).to_have_url(re.compile(r"/login"))
    page.goto("/?platform=meta")
    expect(page).to_have_url(re.compile(r"/login"))


# ------------------------------------------------------------------ stale banner, stage note, landing account rows
def fake_status(page, patch):
    """serve /api/status with fields overridden (works against the mock and the real API)"""
    def handler(route):
        resp = route.fetch()
        body = resp.json()
        body.update(patch)
        route.fulfill(response=resp, json=body)
    page.route("**/api/status", handler)


def test_stale_banner_shows_on_every_page_when_stale_and_is_not_dismissible(page):
    page.goto("/?platform=meta")
    page.wait_for_selector("[data-testid=products-table] tbody tr[data-key]")
    expect(page.locator("#stale-banner")).to_be_hidden()  # fresh: no banner
    assert "failed" not in page.locator("#status").inner_text().lower()
    fake_status(page, {"stale": {"is_stale": True, "reason": "the last Meta pull failed (token rejected)"}, "last_ok_at": "2026-11-02T22:12:00+00:00",
                       "last_refresh": {"job": "run_daily", "finished_at": "2026-11-03T06:12:00+00:00", "status": "failed"}})
    for u in ("/?platform=meta", "/", f"/p/{SLUG}", f"/p/{SLUG}?view=ads", f"/p/{SLUG}?view=campaigns"):
        page.goto(u)
        page.wait_for_selector(".tablebox")
        idle(page)
        b = page.locator("#stale-banner")
        expect(b).to_be_visible()
        txt = b.inner_text()
        assert re.fullmatch(r"Data may be out of date: the last Meta pull failed \(token rejected\)\. Last successful refresh .+; data through [A-Z][a-z]{2} \d{1,2}\.", txt), txt
        assert b.locator("button, a").count() == 0 and b.get_attribute("role") == "status"  # not dismissible
        assert "failed" not in page.locator("#status").inner_text().lower()  # the muted "(failed)" is gone from the header
        bg = page.evaluate("() => getComputedStyle(document.querySelector('#stale-banner')).backgroundColor")
        assert bg.startswith("rgb(255, 2") or bg.startswith("rgb(255, 24"), bg  # yellow
        # sits under the header
        assert page.evaluate("() => document.querySelector('#stale-banner').getBoundingClientRect().top >= document.querySelector('#topbar').getBoundingClientRect().bottom - 1")
    shot(page, "44_stale_banner")


def test_stage_note_tooltip_and_legend_only_when_not_both_stages(page):
    page.goto(f"/p/{SLUG}?stage=scaling")
    wait_rows(page)
    note = page.evaluate("() => fetch('/api/products/%s/tree?root=cluster&depth=0&stage=scaling').then(r => r.json()).then(d => (d.window_info || {}).stage_note || null)" % SLUG)
    if not note:
        pytest.skip("API sends no window_info.stage_note")
    for st in ("testing", "scaling", "unclassified"):
        loc = chip(page, st)
        if loc.is_visible():
            assert loc.get_attribute("title") == note
    expect(page.locator("[data-testid=legend-stage]")).to_contain_text("Stage filter: " + note)
    page.goto(f"/p/{SLUG}")  # both stages: no tooltip, no legend line
    wait_rows(page)
    assert chip(page, "scaling").get_attribute("title") in (None, note) and page.locator("[data-testid=legend-stage]").count() == 0


def test_landing_has_tracker_total_not_on_tracker_and_account_total_rows(page):
    page.goto("/?platform=meta")
    page.wait_for_selector("[data-testid=products-table] tbody tr[data-key]")
    idle(page)
    d = table_data(page, "products-table")
    if len(d["footRows"]) < 3:
        pytest.skip("API sends no unmatched / total_all on the landing envelope")
    labels = [r["cells"][0] if r["cells"][0] else r["cells"][1] for r in d["footRows"]]
    assert labels[0].startswith("Total") and labels[1].startswith("Not on the tracker") and labels[2].startswith("All spend (Meta account total)"), labels
    assert "extra" in d["footRows"][1]["cls"] and "untracked" in d["footRows"][1]["cls"]
    assert page.locator("[data-testid=products-table] tfoot tr.untracked a.note-link").inner_text() == "see each product’s Not on the tracker panel"
    si, hi = d["heads"].index("spend"), d["heads"].index("spend_share")
    rows_sp = sum(num(r[si]) or 0 for r in d["rows"])
    tot, unm, allsp = (num(r["cells"][si]) for r in d["footRows"][:3])
    assert abs(tot - rows_sp) <= len(d["rows"]) and tot > 0
    assert abs(allsp - (tot + unm)) <= 2 + 0.001 * allsp, (tot, unm, allsp)  # account total = tracker products + not on the tracker
    sh = [num(r["cells"][hi]) for r in d["footRows"][:3]]
    assert d["footRows"][2]["cells"][hi].split("\n")[0] == "100.0%"
    assert abs(sh[0] + sh[1] - 100) <= 0.6  # % of spend is relative to the account total
    assert abs(sum(num(r[hi]) or 0 for r in d["rows"]) - sh[0]) <= 0.5 + 0.1 * len(d["rows"])
    # all three footer rows are pinned inside the box (no scrolling to the end)
    ok = page.evaluate("""() => { const w = document.querySelector('[data-testid=products-table] .tablewrap').getBoundingClientRect();
        return [...document.querySelectorAll('[data-testid=products-table] tfoot tr')].map(tr => { const r = tr.getBoundingClientRect(); return r.top >= w.top - 1 && r.bottom <= w.bottom + 1; }); }""")
    assert all(ok) and len(ok) == 3, ok
    pos = page.evaluate("() => [...document.querySelectorAll('[data-testid=products-table] tfoot td')].map(td => getComputedStyle(td).position)")
    assert set(pos) == {"sticky"}
    # compare + per day show up in the extra rows too
    page.goto("/?platform=meta&window=7d&compare=30d")
    page.wait_for_selector("[data-testid=products-table] tbody tr[data-key]")
    idle(page)
    assert page.locator("[data-testid=products-table] tfoot tr.untracked .cmpcell").count() >= 5
    assert page.locator("[data-testid=products-table] tfoot tr.all .cmpcell").count() >= 5
    d2 = table_data(page, "products-table")
    assert abs(num(d2["footRows"][2]["cells"][si]) - (num(d2["footRows"][0]["cells"][si]) + num(d2["footRows"][1]["cells"][si]))) <= 2 + 0.01 * num(d2["footRows"][2]["cells"][si])
    shot(page, "45_landing_account_rows")


@pytest.mark.parametrize("window", ["", "?window=30d"])
def test_unmatched_panel_total_matches_the_landing_not_on_tracker_row(page, window):
    page.goto("/?platform=meta" + window.replace("?", "&"))
    page.wait_for_selector("[data-testid=products-table] tbody tr[data-key]")
    idle(page)
    d = table_data(page, "products-table")
    if len(d["footRows"]) < 3:
        pytest.skip("API sends no unmatched on the landing envelope")
    si = d["heads"].index("spend")
    landing = num(d["footRows"][1]["cells"][si])
    api = page.context.request
    products = api.get("/api/products").json()
    per_product = [api.get(f"/api/products/{p['slug']}/unmatched{window}").json()["total"]["spend"] for p in (products if isinstance(products, list) else products["rows"])]
    assert abs(sum(per_product) - landing) <= max(2.0, 0.002 * landing), (per_product, landing)
    # the panel itself: a launch window reads as lifetime
    page.goto(f"/p/{SLUG}{window}")
    wait_rows(page)
    page.locator("#unmatched summary").click()
    page.wait_for_selector("[data-testid=unmatched-table]")
    if not window:
        expect(page.locator("#unmatched-window")).to_contain_text("lifetime")


# ------------------------------------------------------------------ Active only filter (v1.9)
def api_requests(page):
    reqs = []
    page.on("request", lambda r: reqs.append(r.url) if "/api/" in r.url and "/api/login" not in r.url and "/api/status" not in r.url and "/media/" not in r.url else None)
    return reqs


def has_active_support(page):
    d = page.context.request.get(f"/api/products/{SLUG}/tree?root=cluster&depth=0&active=1").json()
    return bool((d.get("window_info") or {}).get("active_ads") is not None or "ctive" in ((d.get("window_info") or {}).get("note") or ""))


def test_active_only_chip_toggles_url_refetches_tables_and_trend(page):
    if not has_active_support(page):
        pytest.skip("API does not implement active=1 yet")
    goto_product(page, "?window=30d")
    wait_trend(page)
    chip_ = page.locator("#active-chip")
    expect(chip_).to_have_attribute("aria-pressed", "false")
    assert chip_.inner_text() == "Active only"
    assert page.evaluate("() => { const a = document.querySelector('#active-chip'), b = document.querySelector('.schip[data-stage=testing]'); return a.parentElement === b.parentElement; }")  # beside the Stage chips
    before = table_data(page)
    n0, s0 = num(foot_value(page, "unique_ads")), num(foot_value(page, "spend"))
    assert page.locator("[data-testid=legend-active]").count() == 0
    reqs = api_requests(page)
    chip_.click()
    expect(page).to_have_url(re.compile(r"active=1"))
    idle(page)
    wait_trend(page)
    expect(chip_).to_have_attribute("aria-pressed", "true")
    for ep in ("/tree", "/trend", "/unmatched"):
        assert any(ep in u and "active=1" in u for u in reqs), (ep, reqs)
    assert not any(("/tree" in u or "/trend" in u) and "active=1" not in u for u in reqs[-6:]), reqs  # every refetch after the toggle is filtered
    n1, s1 = num(foot_value(page, "unique_ads")), num(foot_value(page, "spend"))
    assert n1 <= n0 and s1 <= s0
    assert n1 < n0 or s1 < s0 or not IS_MOCK
    # legend under the table + the count next to the chip + the window note
    expect(page.locator("[data-testid=legend-active]")).to_contain_text("Showing creatives with active ads; numbers are for their active ads only")
    expect(page.locator("[data-testid=legend-active]")).to_contain_text("status as of")
    expect(page.locator("#active-count")).to_have_text(re.compile(r"\d[\d,]* active ads"))
    expect(page.locator("#range-note")).to_contain_text("Active ads only")
    shot(page, "43_active_only")
    # off again: the unfiltered numbers come back
    chip_.click()
    idle(page)
    assert "active" not in qparams(page)
    assert num(foot_value(page, "spend")) == s0 and num(foot_value(page, "unique_ads")) == n0
    assert page.locator("[data-testid=legend-active]").count() == 0 and page.locator("#active-count").inner_text() == ""


def test_active_only_persists_across_drills_back_reload_and_every_screen(page):
    if not has_active_support(page):
        pytest.skip("API does not implement active=1 yet")
    page.goto("/?platform=meta&active=1")
    page.wait_for_selector("[data-testid=products-table] tbody tr[data-key]")
    wait_trend(page)
    expect(page.locator("#active-chip")).to_have_attribute("aria-pressed", "true")  # deep link
    expect(page.locator("#active-count")).to_have_text(re.compile(r"\d[\d,]* active ads"))
    expect(page.locator("[data-testid=legend-active]")).to_be_visible()
    reqs = api_requests(page)
    page.locator("[data-testid=products-table] tbody tr[data-key] a.prodname").first.click()
    wait_rows(page)
    wait_trend(page)
    assert qparams(page)["active"] == "1"
    open_first_row(page)  # drill
    wait_rows(page)
    open_first_row(page)
    wait_rows(page)
    open_first_row(page)
    wait_rows(page)
    assert qparams(page)["active"] == "1"
    first_row(page).locator("button.caret").click()  # ads under a creative
    page.wait_for_selector(f"{TABLE} tr.lv4", timeout=8000)
    first_row(page).locator("a.row-open").click()  # ad detail: ad table, trend (full series) and preview all filtered
    expect(page.locator("#ad-title")).to_be_visible()
    wait_trend(page)
    assert qparams(page)["active"] == "1"
    expect(page.locator("#active-chip")).to_have_attribute("aria-pressed", "true")
    page.click("#back")  # Back keeps it
    wait_rows(page)
    assert qparams(page)["active"] == "1"
    page.reload()
    wait_rows(page)
    expect(page.locator("#active-chip")).to_have_attribute("aria-pressed", "true")
    # the other screens
    for u in (f"/p/{SLUG}?view=ads&active=1", f"/p/{SLUG}?view=campaigns&active=1", f"/p/{SLUG}?all=1&active=1"):
        page.goto(u)
        page.wait_for_selector(".tablebox")
        idle(page)
        expect(page.locator("#active-chip")).to_have_attribute("aria-pressed", "true")
        expect(page.locator("[data-testid=legend-active]")).to_be_visible()
    # every API call made while active=1 carried the filter (tables, sparklines, trend, unmatched, campaigns, ads, ad detail)
    unfiltered = [u for u in reqs if re.search(r"/(tree|trend|families|campaigns|unmatched|stage-daily|rollup)\?|/ads\?|/api/ads/\d+\?|/api/products\?", u) and "/preview" not in u and "active=1" not in u]
    assert not unfiltered, unfiltered
    # the chip toggles from any screen and keeps the rest of the URL
    page.goto(f"/p/{SLUG}?view=ads&window=14d&active=1")
    wait_rows(page, "ads-table")
    if IS_MOCK:
        assert set(col(page, "status", "ads-table")) <= {"ACTIVE", ""}  # ad tables list only active ads
    page.locator("#active-chip").click()
    idle(page)
    q = qparams(page)
    assert "active" not in q and q["view"] == "ads" and q["window"] == "14d"


# ------------------------------------------------------------------ creative / ad vocabulary and the first column
LEGEND = "Creative = one ad concept and all its copies. Ad = one copy running in a campaign and ad set."


def test_creative_ad_vocabulary_headings_legend_and_columns(page):
    cluster, icp, pain = drill_to_families(page)
    expect(page.locator("#level-title")).to_have_text(f"Creatives in {pain}")
    heads = [t.strip().replace("\n", " ") for t in page.locator(f"{HEAD} th").all_inner_texts()]
    assert heads[0] == "Creative" and "Creatives" in heads and "Unique ads" not in heads, heads
    live_th = page.locator(f"{HEAD} th[data-col=live_now]")
    assert live_th.get_attribute("title") == "Creatives that spent in the last 2 days"
    # legend line + (i) tooltip under the heading of every table page
    pages = ["/?platform=meta", f"/p/{SLUG}", purl(cluster, icp, pain), f"/p/{SLUG}?all=1", f"/p/{SLUG}?view=ads", f"/p/{SLUG}?view=campaigns"]
    for u in pages:
        page.goto(u)
        page.wait_for_selector(".tablebox")
        idle(page)
        leg = page.locator("[data-testid=legend]").first
        expect(leg).to_contain_text(LEGEND)
        tip = leg.locator(".info-i")
        assert tip.inner_text() == "ⓘ" and "one ad concept" in tip.get_attribute("title") and "campaign and ad set" in tip.get_attribute("title")
        text = page.locator("body").inner_text().lower()
        assert not re.search(r"ad famil|meta ads?\b|\bfamilies\b", text), (u, re.findall(r"ad famil\w*|meta ads?\b|\bfamilies\b", text))
    page.goto(f"/p/{SLUG}?all=1")
    wait_rows(page)
    expect(page.locator("#level-title")).to_have_text("All creatives")
    assert page.locator(f"{HEAD} th[data-col=display]").inner_text().strip() == "Creative"
    # the Ads view of one creative: 'Ads running <creative>', same URL params as before (view=ads, family_key)
    page.locator(f"{BODY_ROWS} .row-ads").first.click()
    expect(page).to_have_url(re.compile(r"view=ads"))
    assert "family_key" in qparams(page)
    wait_rows(page, "ads-table")
    expect(page.locator("#level-title")).to_contain_text("Ads running ")
    assert page.locator(f"[data-testid=ads-table] > .tablewrap > table > thead th[data-col=ad_name]").inner_text().strip() == "Ad"
    # a campaign's rows are Ads
    page.goto(f"/p/{SLUG}?view=campaigns")
    page.wait_for_selector("[data-testid=campaign-table]")
    idle(page)
    if page.locator("[data-testid=campaign-table] tbody tr[data-key]").count():
        page.locator("[data-testid=campaign-table] tbody tr[data-key] a.rowlink").first.click()
        wait_rows(page, "ads-table")
        expect(page.locator("#level-title")).to_have_text("Ads")
    # ad detail: 'Creative: <name>' with the ads table (copies of this creative) below
    page.goto(purl(cluster, icp, pain))
    wait_rows(page)
    first_row(page).locator("a.row-open").click()
    expect(page.locator("#ad-title")).to_contain_text("Creative: ")
    if page.locator("#meta-ads").count():
        expect(page.locator("#meta-ads")).to_contain_text("copies of this creative")
        expect(page.locator("#meta-ads [data-testid=legend]")).to_contain_text(LEGEND)
    expect(page.locator("#family-metrics")).to_contain_text("Creative metrics")


def test_creative_row_second_line_shows_type_status_launch_and_ad_counts(page):
    drill_to_families(page)
    meta = first_row(page).locator(".meta")
    expect(meta).to_be_visible()
    text = meta.inner_text()
    assert meta.locator(".chip-type").inner_text() in ("Video", "Image"), text
    if IS_MOCK:
        assert re.search(r"\d+ ads? · \d+ live", text), text
        assert page.locator(f"{BODY_ROWS} .meta-status").first.inner_text().strip()
    # the same on the flat creatives table
    page.goto(f"/p/{SLUG}?all=1")
    wait_rows(page)
    t2 = page.locator(f"{BODY_ROWS} .meta").first.inner_text()
    assert page.locator(f"{BODY_ROWS} .meta .chip-type").first.inner_text() in ("Video", "Image")
    if IS_MOCK:
        assert re.search(r"\d+ ads? · \d+ live", t2), t2
        assert re.search(r"20\d\d-\d\d-\d\d", " ".join(page.locator(f"{BODY_ROWS} .meta-launch").all_inner_texts()))  # launch date


@pytest.mark.parametrize("width,dpr", [(900, 1), (1100, 1), (1440, 1), (900, 2), (1440, 2)])
def test_first_column_name_is_always_visible_and_wraps(browser, base_url, width, dpr):
    """regression: the creative name must never collapse to zero width; name on line one, badges / Open / meta below"""
    from conftest import USERNAME
    ctx_ = browser.new_context(viewport={"width": width, "height": 900}, device_scale_factor=dpr, base_url=base_url)
    try:
        assert ctx_.request.post(base_url + "/api/login", data={"username": USERNAME, "password": PASSWORD}).status in (204, 200)
        pg = ctx_.new_page()
        cluster, icp, pain = drill_to_families(pg)
        pg.locator(f"{BODY_ROWS} button.caret").first.click()  # open a creative: its ads (lv4 rows) are checked too
        pg.wait_for_selector(f"{TABLE} tr.lv4", timeout=8000)

        def audit(selector):
            return pg.evaluate("""(sel) => [...document.querySelectorAll(sel)].slice(0, 8).map(row => {
                const nm = row.querySelector('.nm'); const meta = row.querySelector('.meta, .sub'); const td = row.querySelector('td.labelcol, td.adcol');
                if (!nm) return null;
                const r = nm.getBoundingClientRect(); const cs = getComputedStyle(nm); const mr = meta ? meta.getBoundingClientRect() : null;
                const nameEl = nm.querySelector('.rowname, .lvlname') || nm;
                const nr = nameEl.getBoundingClientRect(); const ncs = getComputedStyle(nameEl);
                return {text: nm.innerText.trim(), w: r.width, h: r.height, nameW: nr.width, clipped: nm.scrollWidth > nm.clientWidth + 1, overflow: ncs.overflow, ellipsis: ncs.textOverflow === 'ellipsis',
                        ws: cs.whiteSpace, tdW: td ? td.getBoundingClientRect().width : null, metaBelow: mr ? mr.top >= r.top + r.height - 1 : true};
            }).filter(Boolean)""", selector)

        for sel in (f"{TABLE} tbody tr.lv3", f"{TABLE} tbody tr.lv4"):
            rows = audit(sel)
            assert rows, sel
            for r in rows:
                assert r["text"], r
                assert r["w"] >= 60 and r["nameW"] >= 40 and r["h"] > 8, r  # not collapsed
                assert r["ws"] == "normal" and not r["ellipsis"] and not r["clipped"], r  # wraps instead of clipping
                assert r["metaBelow"], r  # badges / Open / meta sit on the second line
                assert r["tdW"] is None or r["tdW"] >= 270, r  # first column min-width ~280
        for u in (f"/p/{SLUG}?all=1", f"/p/{SLUG}?view=ads"):
            pg.goto(u)
            pg.wait_for_selector(".tablebox tbody tr[data-key]")
            idle(pg)
            for r in audit("[data-testid=level-table] tbody tr[data-key], [data-testid=ads-table] tbody tr[data-key]"):
                assert r["w"] >= 60 and not r["clipped"] and r["ws"] == "normal" and r["metaBelow"], (u, r)
        pg.screenshot(path=str(SHOTS / f"42_first_column_{width}_{dpr}x.png"))
    finally:
        ctx_.close()


# ------------------------------------------------------------------ thumbnails lightbox, live preview, links
LB = ".lightbox[role=dialog]"


def first_video_ad_id(page):
    api = page.context.request
    for r in api.get(f"/api/products/{SLUG}/families").json()["rows"]:
        if r.get("ad_type") == "VID" and r.get("primary_id") is not None:
            return r["primary_id"]
    pytest.skip("no video ad")


def test_lightbox_opens_from_any_thumbnail_and_closes_three_ways(page):
    page.goto(f"/p/{SLUG}?view=ads")
    wait_rows(page, "ads-table")
    thumb = page.locator("[data-testid=ads-table] tbody img.thumb.zoomable").first
    name_alt = thumb.get_attribute("alt")
    url = page.url
    thumb.click()
    dlg = page.locator(LB)
    expect(dlg).to_be_visible()
    assert dlg.get_attribute("aria-modal") == "true" and dlg.get_attribute("aria-label")
    assert page.url == url  # a thumbnail click does not follow the row link
    img = dlg.locator("img.lb-img")
    expect(img).to_be_visible()
    assert img.get_attribute("src").startswith("/media/") and img.get_attribute("alt")  # alt text
    assert page.evaluate("() => document.querySelector('.lightbox .lb-img').naturalWidth") > 0
    expect(dlg.locator("#lb-open")).to_have_text("Open ad page")
    expect(dlg.locator("#lb-live")).to_have_text("See live ad")
    assert dlg.locator(".modal-title").inner_text().strip()  # the ad name
    shot(page, "38_lightbox")
    page.keyboard.press("Escape")  # 1. Esc
    expect(dlg).to_have_count(0)
    assert page.evaluate("() => document.activeElement && document.activeElement.classList.contains('zoomable')")  # focus restored to the thumbnail
    thumb.click()
    expect(dlg).to_be_visible()
    page.mouse.click(5, 5)  # 2. click outside
    expect(dlg).to_have_count(0)
    thumb.click()
    dlg.locator(".modal-close").click()  # 3. the x button
    expect(dlg).to_have_count(0)
    # keyboard: the thumbnail is a focusable button (Enter opens)
    thumb.focus()
    page.keyboard.press("Enter")
    expect(dlg).to_be_visible()
    page.keyboard.press("Escape")
    assert name_alt


def test_lightbox_from_tree_rows_does_not_drop_down_the_row_and_opens_ad_page(page):
    drill_to_families(page)
    row = first_row(page)
    caret = row.locator("button.caret")
    expect(caret).to_have_attribute("aria-expanded", "false")
    row.locator("img.thumb.zoomable").click()
    expect(page.locator(LB)).to_be_visible()
    expect(page.locator(f"{BODY_ROWS} button.caret").first).to_have_attribute("aria-expanded", "false")  # the row stayed closed
    page.locator("#lb-open").click()  # Open ad page
    expect(page).to_have_url(re.compile(r"/ad/\d+"))
    expect(page.locator(LB)).to_have_count(0)
    expect(page.locator("#ad-title")).to_be_visible()


def test_lightbox_frames_arrows_keyboard_and_focus_trap(page):
    ad_id = first_video_ad_id(page)
    page.goto(f"/ad/{ad_id}")
    expect(page.locator("#ad-title")).to_be_visible()
    frames = page.locator("#media .frames button.media-link")
    assert frames.count() >= 2
    frames.nth(1).click()  # opens on the clicked frame
    dlg = page.locator(LB)
    expect(dlg).to_be_visible()
    n = page.locator(".lb-strip .lb-frame").count()
    assert n >= 3
    counter = dlg.locator(".lb-counter")
    assert re.fullmatch(rf"\d+ / {n}", counter.inner_text())
    start = int(counter.inner_text().split(" / ")[0])
    page.keyboard.press("ArrowRight")
    assert int(counter.inner_text().split(" / ")[0]) == start % n + 1
    page.keyboard.press("ArrowLeft")
    page.keyboard.press("ArrowLeft")
    assert int(counter.inner_text().split(" / ")[0]) == (start - 2) % n + 1
    src_before = dlg.locator("img.lb-img").get_attribute("src")
    dlg.locator(".lb-nav.next").click()
    assert dlg.locator("img.lb-img").get_attribute("src") != src_before
    dlg.locator(".lb-nav.prev").click()
    assert dlg.locator("img.lb-img").get_attribute("src") == src_before
    dlg.locator(".lb-strip .lb-frame").nth(n - 1).click()  # click a frame in the strip
    assert counter.inner_text() == f"{n} / {n}"
    page.keyboard.press("ArrowRight")  # wraps around
    assert counter.inner_text() == f"1 / {n}"
    alt = dlg.locator("img.lb-img").get_attribute("alt")
    assert re.search(r"frame \d+ of \d+", alt)
    # detail: ad name, tag and a transcript snippet
    expect(dlg.locator(".lb-tag")).to_contain_text("Tag:")
    expect(dlg.locator(".lb-transcript")).to_contain_text("Transcript:")
    assert len(dlg.locator(".lb-transcript").inner_text()) <= 320
    # focus stays inside the dialog (trap), forwards and backwards
    for _ in range(14):
        page.keyboard.press("Tab")
        assert page.evaluate("() => document.querySelector('.lightbox').contains(document.activeElement)")
    for _ in range(14):
        page.keyboard.press("Shift+Tab")
        assert page.evaluate("() => document.querySelector('.lightbox').contains(document.activeElement)")
    shot(page, "39_lightbox_frames")
    page.keyboard.press("Escape")
    expect(dlg).to_have_count(0)


@pytest.mark.mock_only
def test_see_the_ad_section_formats_iframe_and_sizes(page):
    ad_id = first_video_ad_id(page)
    reqs = []
    page.on("request", lambda r: reqs.append(r.url) if "/preview" in r.url and "/api/" in r.url else None)
    page.goto(f"/ad/{ad_id}")
    section = page.locator("#see-ad")
    expect(section).to_be_visible()
    expect(section.locator("h2")).to_have_text("See the ad")
    fr = section.locator("iframe.preview-frame")
    expect(fr).to_be_visible()
    assert fr.get_attribute("sandbox") == "allow-scripts allow-same-origin allow-popups" and fr.get_attribute("loading") == "lazy"
    assert "/mock/preview" in fr.get_attribute("src") and fr.get_attribute("title")
    assert fr.evaluate("e => [e.clientWidth, e.clientHeight]") == [400, 700]  # mobile feed
    page.wait_for_function("() => !document.querySelector('#see-ad .preview-msg.overlay')")  # spinner goes away once loaded
    assert [b.inner_text() for b in section.locator(".preview-formats button").all()] == ["Mobile feed", "Desktop feed", "Reels", "Stories"]
    for key, size in (("desktop_feed", [540, 600]), ("reels", [400, 700]), ("stories", [400, 700]), ("mobile_feed", [400, 700])):
        section.locator(f"button[data-format={key}]").click()
        page.wait_for_function("(k) => { const f = document.querySelector('#see-ad iframe.preview-frame'); return !!f && f.getAttribute('src').includes('format=' + k); }", arg=key)
        assert section.locator("iframe.preview-frame").evaluate("e => [e.clientWidth, e.clientHeight]") == size, key
        assert any(f"format={key}" in u for u in reqs)
    # the preview frame shows the mock ad
    assert "Mock Meta preview" in page.frame_locator("#see-ad iframe.preview-frame").locator("#mock-ad").inner_text()
    shot(page, "40_see_the_ad")


@pytest.mark.allow_errors
def test_preview_off_and_error_states(page):
    ad_id = first_video_ad_id(page)
    # preview switched off: a clear message, the links only, no iframe
    page.route("**/api/ads/*/preview*", lambda route: route.fulfill(status=200, content_type="application/json", body='{"enabled": false, "src": null, "format": "mobile_feed"}'))
    page.goto(f"/ad/{ad_id}")
    expect(page.locator("#see-ad")).to_contain_text("Live preview off")
    assert page.locator("#see-ad iframe").count() == 0 and page.locator("#see-ad .preview-formats").is_hidden()
    assert page.locator("#see-ad .preview-msg").get_attribute("data-state") == "off"
    # enabled but Meta returned an error
    page.unroute("**/api/ads/*/preview*")
    page.route("**/api/ads/*/preview*", lambda route: route.fulfill(status=200, content_type="application/json", body='{"enabled": true, "src": null, "error": "Meta rejected the preview request"}'))
    page.goto(f"/ad/{ad_id}")
    expect(page.locator("#see-ad")).to_contain_text("Preview unavailable")
    expect(page.locator("#see-ad")).to_contain_text("Meta rejected the preview request")
    assert page.locator("#see-ad iframe").count() == 0
    # an unsafe src is never framed
    page.unroute("**/api/ads/*/preview*")
    page.route("**/api/ads/*/preview*", lambda route: route.fulfill(status=200, content_type="application/json", body='{"enabled": true, "src": "javascript:alert(1)", "format": "mobile_feed"}'))
    page.goto(f"/ad/{ad_id}")
    expect(page.locator("#see-ad")).to_contain_text("Preview unavailable")
    assert page.locator("#see-ad iframe").count() == 0
    # HTTP failure
    page.unroute("**/api/ads/*/preview*")
    page.route("**/api/ads/*/preview*", lambda route: route.fulfill(status=502, content_type="application/json", body='{"error": "bad gateway"}'))
    page.goto(f"/ad/{ad_id}")
    expect(page.locator("#see-ad")).to_contain_text("Preview unavailable")
    shot(page, "41_preview_unavailable")


@pytest.mark.allow_errors
def test_eye_icon_and_lightbox_open_the_preview_modal(page):
    page.goto(f"/p/{SLUG}?view=ads")
    wait_rows(page, "ads-table")
    reqs = []
    page.on("request", lambda r: reqs.append(r.url) if "/preview" in r.url and "/api/" in r.url else None)
    eye = page.locator("[data-testid=ads-table] tbody button.eye").first
    expect(eye).to_be_visible()
    assert eye.get_attribute("title") == "See live ad"
    eye.click()
    modal = page.locator(".preview-modal[role=dialog]")
    expect(modal).to_be_visible()
    page.wait_for_function("() => !!document.querySelector('.preview-modal .preview-msg') || !!document.querySelector('.preview-modal iframe')")
    assert any("/api/meta-ads/" in u and u.split("?")[0].endswith("/preview") for u in reqs), reqs  # the per-Meta-ad endpoint
    page.keyboard.press("Escape")
    expect(modal).to_have_count(0)
    # lightbox -> See live ad: the preview stacks above it; Esc closes the top one first
    page.locator("[data-testid=ads-table] tbody img.thumb.zoomable").first.click()
    expect(page.locator(LB)).to_be_visible()
    page.locator("#lb-live").click()
    expect(modal).to_be_visible()
    assert page.locator(LB).count() == 1
    page.keyboard.press("Escape")
    expect(modal).to_have_count(0)
    expect(page.locator(LB)).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.locator(LB)).to_have_count(0)
    # the eye on a tree ad row
    drill_to_families(page)
    first_row(page).locator("button.caret").click()
    page.wait_for_selector(f"{TABLE} tr.lv4", timeout=8000)
    page.locator(f"{TABLE} tr.lv4 button.eye").first.click()
    expect(modal).to_be_visible()
    page.keyboard.press("Escape")


def test_facebook_post_and_ads_manager_links_are_present_and_safe(page):
    page.goto(f"/p/{SLUG}?view=ads")
    wait_rows(page, "ads-table")
    fb = page.locator("[data-testid=ads-table] tbody a[data-link=facebook-post]")
    am = page.locator("[data-testid=ads-table] tbody a[data-link=ads-manager]")
    if not fb.count():
        pytest.skip("API returns no links yet")
    assert fb.count() >= 1 and am.count() >= 1
    for loc in (fb, am):
        for i in range(min(loc.count(), 5)):
            a = loc.nth(i)
            assert a.get_attribute("target") == "_blank"
            rel = a.get_attribute("rel")
            assert "noopener" in rel and "noreferrer" in rel
            assert re.match(r"https?://", a.get_attribute("href"))
    assert fb.first.inner_text().startswith("Facebook post") and am.first.inner_text().startswith("Ads Manager")
    # ad detail page
    drill_to_families(page)
    first_row(page).locator("a.row-open").click()
    expect(page.locator("#ad-title")).to_be_visible()
    for sel in ("a[data-link=facebook-post]", "a[data-link=ads-manager]"):
        a = page.locator(f".pagehead {sel}").first
        if a.count():
            assert a.get_attribute("target") == "_blank" and "noopener" in a.get_attribute("rel") and "noreferrer" in a.get_attribute("rel")
    assert page.locator("a[href^='javascript:']").count() == 0


@pytest.mark.mock_only
def test_unsafe_link_urls_are_not_rendered(page):
    api = page.context.request
    rows = api.get(f"/api/products/{SLUG}/families").json()["rows"]
    x = next(r for r in rows if r["display"].startswith("XSS"))
    page.goto(f"/ad/{x['primary_id']}")
    expect(page.locator("#ad-title")).to_be_visible()
    assert page.locator("a[href^='javascript:']").count() == 0
    assert page.locator(".pagehead a[data-link=facebook-post]").count() == 0  # the javascript: story_url was dropped
    assert page.locator(".pagehead a[data-link=ads-manager]").count() == 1
    page.goto(f"/p/{SLUG}?view=ads&family_key={x['family_key']}")
    wait_rows(page, "ads-table")
    assert page.locator("a[href^='javascript:']").count() == 0


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
    expect(page.locator("h1")).to_have_text("All platforms")
    assert page.url.rstrip("/").endswith(page.url.split("/")[2]) or page.url.endswith("/")  # landed on /
    assert page.locator("#username").count() == 0 and page.locator("#password").count() == 0
    page.goto("/login?next=/p/" + SLUG)  # no login step, so no ?next hop either: just the app
    expect(page.locator("h1")).to_have_text("All platforms")
    expect(page.locator("#logout")).to_be_hidden()


# ------------------------------------------------------------------ pinned Total row on every table
def assert_total_row(page, testid, strict=True):  # the landing's first footer row is a share of the Meta account total, not 100%
    d = table_data(page, testid)
    assert d["foot"] and any(c.startswith("Total") for c in d["foot"][:2]), (testid, d["foot"][:2])  # (a caret column may come first)
    i = d["heads"].index("spend")
    shown = len(d["rows"])
    if strict and shown and shown < 400:  # (long tables render 400 rows at a time)
        s = sum(num(r[i]) or 0 for r in d["rows"])
        assert abs(s - num(d["foot"][i])) <= max(2.0, shown * 1.0), (testid, s, d["foot"][i])
    if "spend_share" in d["heads"] and testid != "products-table":
        assert d["foot"][d["heads"].index("spend_share")].split("\n")[0] == "100.0%", (testid, d["foot"])
    pos = page.evaluate("(t) => getComputedStyle(document.querySelector(`[data-testid=${t}] tfoot td`)).position", testid)
    assert pos == "sticky"
    return d


def test_total_row_on_every_table_type(page):
    page.goto("/?platform=meta")
    page.wait_for_selector("[data-testid=products-table] tbody tr[data-key]")
    d = assert_total_row(page, "products-table")
    for k in ["unique_ads", "live_now", "spend", "spend_share", *TRAFFIC_COLS, "purchases", "revenue", "roas", "cpa", *STAGE_COLS]:
        assert d["foot"][d["heads"].index(k)] not in ("",), (k, d["foot"])  # every metric column has a total
    assert d["foot"][d["heads"].index("chart" if "chart" in d["heads"] else "_chart")] == "" and d["foot"][d["heads"].index("_ads")] == ""  # Chart / Ads cells are blank
    # ratios come from the sums, not from averaging the rows
    spend, clicks, purchases, revenue = (num(d["foot"][d["heads"].index(k)]) for k in ("spend", "link_clicks", "purchases", "revenue"))
    if purchases:
        assert abs(num(d["foot"][d["heads"].index("cpa")]) - spend / purchases) <= 1.0
    if spend:
        assert abs(num(d["foot"][d["heads"].index("roas")]) - revenue / spend) <= 0.01
    shot(page, "37_total_products")
    cluster, icp, pain = drill_to_families(page)
    assert_total_row(page, "level-table")  # tree tables keep theirs
    page.goto(f"/p/{SLUG}?all=1")
    wait_rows(page)
    assert_total_row(page, "level-table")
    page.goto(f"/p/{SLUG}?view=ads")
    wait_rows(page, "ads-table")
    assert_total_row(page, "ads-table")
    page.goto(f"/p/{SLUG}?view=campaigns")
    page.wait_for_selector("[data-testid=campaign-table]")
    idle(page)
    if page.locator("[data-testid=campaign-table] tbody tr[data-key]").count():
        assert_total_row(page, "campaign-table", strict=IS_MOCK)
    page.goto(f"/p/{SLUG}")
    wait_rows(page)
    page.locator("#unmatched summary").click()
    page.wait_for_selector("[data-testid=unmatched-table]")
    assert_total_row(page, "unmatched-table", strict=IS_MOCK)
    # ad detail: Meta ads in the family + campaigns mini table
    page.goto(purl(cluster, icp, pain))
    wait_rows(page)
    href = first_row(page).get_attribute("data-href")
    page.goto(href)
    wait_trend(page)
    if page.locator("#meta-ads").count():
        assert_total_row(page, "ads-table")
    if page.locator("#campaigns").count():
        assert_total_row(page, "campaigns-mini-table")
    # the inline Meta ads of a flat family row have one too
    page.goto(f"/p/{SLUG}?all=1")
    wait_rows(page)
    page.locator(f"{BODY_ROWS} td.num").first.click()
    page.wait_for_selector(f"{TABLE} tr.expandrow table tfoot td", timeout=5000)


def test_total_row_with_compare_per_day_and_single_stage(page):
    page.goto("/?platform=meta&window=7d&compare=30d")
    page.wait_for_selector("[data-testid=products-table] tbody tr[data-key]")
    idle(page)
    assert page.is_checked("#perday")  # windows differ in length: per-day values
    d = assert_total_row(page, "products-table")
    assert page.locator("[data-testid=products-table] tfoot .cmpcell").count() >= 8  # compare sub-lines in the total row
    i = d["heads"].index("spend")
    assert "vs " in page.locator(f"[data-testid=products-table] tfoot td:nth-child({i + 1}) .cmpcell .b").first.inner_text()
    page.uncheck("#perday")  # raw totals: the total spend is the sum of the raw row spends
    d = assert_total_row(page, "products-table")
    page.goto("/?platform=meta&stage=scaling")
    page.wait_for_selector("[data-testid=products-table] tbody tr[data-key]")
    d = assert_total_row(page, "products-table")
    assert not any(c in d["heads"] for c in STAGE_COLS)  # stage columns (and their totals) only when both stages are on


def test_total_row_stays_visible_at_the_bottom_of_a_long_table(page):
    goto_product(page)
    page.locator("#expand-to [data-depth='3']").click()
    page.wait_for_selector(f"{TABLE} tr.lv3", timeout=8000)
    idle(page)
    wrap = f"{TABLE} .tablewrap"
    over = page.evaluate("(w) => { const e = document.querySelector(w); return e.scrollHeight - e.clientHeight; }", wrap)
    assert over > 100, "the table must be taller than its box for this check"
    for top in (0, over // 2):
        page.evaluate("([w, t]) => { document.querySelector(w).scrollTop = t; }", [wrap, top])
        rect = page.evaluate("(w) => { const e = document.querySelector(w).getBoundingClientRect(); const f = document.querySelector(w + ' tfoot td').getBoundingClientRect(); return {wt: e.top, wb: e.bottom, ft: f.top, fb: f.bottom}; }", wrap)
        assert rect["ft"] >= rect["wt"] - 1 and rect["fb"] <= rect["wb"] + 1, (top, rect)  # pinned inside the box, no scrolling to the very end


# ------------------------------------------------------------------ products landing
def test_products_landing(page):
    page.goto("/?platform=meta")
    page.wait_for_selector("[data-testid=products-table] tbody tr[data-key]")
    expect(page.locator("#data-through")).to_contain_text("Data through")
    d = table_data(page, "products-table")
    assert len(d["rows"]) >= 1
    spend = col(page, "spend", "products-table")
    assert all(re.fullmatch(r"\$[\d,]+|–", v) for v in spend), spend
    roas = col(page, "roas", "products-table")
    assert all(re.fullmatch(r"\d+\.\d\d|–", v) for v in roas), roas
    shares = [num(v) for v in col(page, "spend_share", "products-table")]
    assert 50 <= sum(s or 0 for s in shares) <= 100.5, shares  # relative to the Meta account total (the rest is "Not on the tracker")
    assert page.locator("[data-testid=products-table] .badge").count() >= 1
    for gone in ("spend_7d", "cpa_7d"):
        assert not has_col(page, gone, "products-table")
    shot(page, "02_products")
    # the stage chips and compare control sit in the header here too; row click -> product page, window carried
    expect(chip(page, "testing")).to_be_visible()
    expect(page.locator("#cmp")).to_be_visible()
    page.goto("/?platform=meta&window=14d")
    page.wait_for_selector("[data-testid=products-table] tbody tr[data-key]")
    page.locator("[data-testid=products-table] tbody tr[data-key]").first.locator("td.num").first.click()
    expect(page).to_have_url(re.compile(r"/p/[^/?]+\?window=14d"))
    wait_rows(page)


@pytest.mark.mock_only
def test_tag_labels_by_tag_set(page):
    page.goto("/?platform=meta")
    page.wait_for_selector("[data-testid=products-table] tbody tr[data-key]")
    txt = page.locator("[data-testid=products-table]").inner_text()
    assert "Line-mapped tags" in txt and "Tracker tags — not line-mapped yet" in txt
    page.goto("/p/beef-liver")
    wait_rows(page)
    expect(page.locator(".note-line")).to_contain_text("Tracker tags — not line-mapped yet")


# ------------------------------------------------------------------ drill-down (Open ›) + breadcrumb
def test_open_link_drill_down_with_breadcrumb(page):
    goto_product(page)
    expect(page.locator("#level-title")).to_have_text("Market clusters")
    cluster = open_first_row(page)
    expect(page).to_have_url(re.compile(r"cluster="))
    expect(page.locator("#level-title")).to_contain_text("ICPs in")
    wait_rows(page)
    icp = open_first_row(page)
    expect(page.locator("#level-title")).to_contain_text("Pain points in")
    wait_rows(page)
    pain = open_first_row(page)
    expect(page.locator("#level-title")).to_contain_text("Creatives in")
    wait_rows(page)
    q = qparams(page)
    assert (q["cluster"], q["icp"], q["pain"]) == (cluster, icp, pain)
    crumbs = page.locator(".crumbs li").all_inner_texts()
    assert crumbs[0] == "Meta" and crumbs[2:5] == [cluster, icp, pain], crumbs
    shot(page, "03_families")
    first_row(page).locator("a.row-open").click()
    expect(page).to_have_url(re.compile(r"/ad/\d+"))
    expect(page.locator("#ad-title")).to_be_visible()
    crumbs = page.locator(".crumbs li").all_inner_texts()
    assert crumbs[0] == "Meta" and crumbs[-1] != "" and len(crumbs) >= 3
    page.locator(".crumbs a", has_text=cluster).first.click()
    expect(page.locator("#level-title")).to_contain_text("ICPs in")
    assert qparams(page)["cluster"] == cluster
    page.go_back()
    expect(page.locator("#ad-title")).to_be_visible()


def test_level_columns_share_and_total_row(page):
    goto_product(page)
    d = table_data(page)
    heads = [h for h in d["heads"] if h]
    for k in METRIC_COLS + STAGE_COLS:
        assert k in heads, (k, heads)
    # no fixed-7d columns anywhere (the date range + Compare to replace them)
    for gone in ("spend_7d", "cpa_7d"):
        assert gone not in heads
    assert heads.index("spend_share") == heads.index("spend") + 1  # "% of spend" right after Spend
    i = heads.index("spend_share")
    assert heads[i + 1:i + 5] == TRAFFIC_COLS and heads.index("purchases") == i + 5  # then Impressions, Link clicks, CTR, CPC, Purchases
    assert not any(c in heads for c in ADVANCED_COLS), "Advanced (CPM, Conv rate) is off by default"
    labels = [t.strip().replace("\n", " ") for t in page.locator(f"{HEAD} th").all_inner_texts()]
    assert not any("7d" in x for x in labels), labels
    for lbl in ["Creatives", "Live now", "Spend", "% of spend", "Impressions", "Link clicks", "CTR", "CPC", "Purchases", "Revenue", "ROAS", "CPA", "Testing $", "Scaling $", "Testing %"]:
        assert any(lbl.replace(" ", "") in x.replace(" ", "") for x in labels), (lbl, labels)
    assert d["foot"][0] == "Total"
    i = d["heads"].index("spend")
    s = sum(num(r[i]) or 0 for r in d["rows"])
    assert abs(s - num(d["foot"][i])) <= len(d["rows"]), (s, d["foot"][i])
    sh = col(page, "spend_share")
    assert all(re.fullmatch(r"\d+\.\d%|–", v) for v in sh), sh
    assert abs(sum(num(v) or 0 for v in sh) - 100) <= 0.6, sh
    assert foot_value(page, "spend_share") == "100.0%"
    assert page.locator(f"{BODY_ROWS} a.row-ads").count() == len(d["rows"])
    expect(page.locator("#all-ads")).to_have_text("All creatives in view")
    pos = page.evaluate("() => getComputedStyle(document.querySelector('[data-testid=level-table] tfoot td')).position")
    assert pos == "sticky"


def test_share_column_sortable_and_children_add_to_parent(page):
    goto_product(page)
    sort_both_ways(page, "spend_share")
    first_row(page).locator("td.num").first.click()  # drops down (default)
    page.wait_for_selector(f"{TABLE} tr.lv1")
    rows = level_rows(page)
    parent = next(r for r in rows if r[0] == 0)
    first_kids = []
    for lv, lab, sp, sh in rows[1:]:
        if lv == 1:
            first_kids.append(sh)
        else:
            break
    assert abs(sum(x or 0 for x in first_kids) - (parent[3] or 0)) <= 0.3 * max(1, len(first_kids)), (first_kids, parent)


# ------------------------------------------------------------------ dropdown tree
def test_dropdown_expands_in_place_all_levels(page):
    goto_product(page)
    url = page.url
    expect(page.locator("#row-mode [data-rm=expand]")).to_have_attribute("aria-pressed", "true")  # default: Drops down
    row = first_row(page)
    caret = row.locator("button.caret")
    expect(caret).to_have_attribute("aria-expanded", "false")
    row.locator("td.num").first.click()  # a click anywhere on the row drops it down
    assert page.url == url  # did not navigate
    expect(row.locator("button.caret")).to_have_attribute("aria-expanded", "true")
    page.wait_for_selector(f"{TABLE} tr.lv1")
    shot(page, "16_dropdown_level1")
    pad0 = page.evaluate("() => parseFloat(getComputedStyle(document.querySelector('[data-testid=level-table] tr.lv0 .cell-label')).paddingLeft)")
    pad1 = page.evaluate("() => parseFloat(getComputedStyle(document.querySelector('[data-testid=level-table] tr.lv1 .cell-label')).paddingLeft)")
    assert pad1 > pad0
    for lv in (1, 2, 3):
        nxt = page.locator(f"{TABLE} tr.lv{lv}").first
        nxt.locator("td.num").first.click()
        page.wait_for_selector(f"{TABLE} tr.lv{lv + 1}", timeout=8000)
    assert page.url == url
    shot(page, "17_dropdown_meta_ads")
    row0 = page.locator(f"{TABLE} tr.lv0").first
    row0.locator("td.num").first.click()
    expect(page.locator(f"{TABLE} tr.lv1")).to_have_count(0)


def test_open_link_navigates_instead_of_dropping_down(page):
    goto_product(page)
    row = first_row(page)
    label = row.get_attribute("data-label")
    row.locator("a.row-open").click()
    expect(page).to_have_url(re.compile(r"cluster="))
    assert qparams(page)["cluster"] == label
    expect(page.locator("#level-title")).to_contain_text("ICPs in")


def test_row_mode_opens_navigates_and_is_remembered(page):
    goto_product(page)
    page.locator("#row-mode [data-rm=open]").click()
    assert page.evaluate("() => localStorage.getItem('tagexp.rowmode')") == "open"
    row = first_row(page)
    label = row.get_attribute("data-label")
    row.locator("td.num").first.click()  # now a plain click opens
    expect(page).to_have_url(re.compile(r"cluster="))
    assert qparams(page)["cluster"] == label
    wait_rows(page)
    expect(page.locator("#row-mode [data-rm=open]")).to_have_attribute("aria-pressed", "true")
    page.reload()
    wait_rows(page)
    expect(page.locator("#row-mode [data-rm=open]")).to_have_attribute("aria-pressed", "true")
    url = page.url
    first_row(page).locator("button.caret").click()  # the caret still expands in place
    page.wait_for_selector(f"{TABLE} tr.lv2")
    assert page.url == url
    page.locator("#row-mode [data-rm=expand]").click()
    assert page.evaluate("() => localStorage.getItem('tagexp.rowmode')") == "expand"


def test_keyboard_expand_and_space_on_caret(page):
    goto_product(page)
    row = first_row(page)
    row.focus()
    page.keyboard.press("Enter")  # row focused: Enter does the row action (drops down)
    page.wait_for_selector(f"{TABLE} tr.lv1")
    caret = first_row(page).locator("button.caret")
    expect(caret).to_have_attribute("aria-expanded", "true")
    caret.focus()
    page.keyboard.press("Space")  # the caret is a real button: Space toggles
    expect(page.locator(f"{TABLE} tr.lv1")).to_have_count(0)
    page.keyboard.press("Enter")
    page.wait_for_selector(f"{TABLE} tr.lv1")


def test_expand_all_collapse_all_and_expand_to(page):
    goto_product(page)
    n_top = len(table_data(page)["rows"])
    for depth, level in ((1, 1), (2, 2), (3, 3)):
        page.locator(f"#expand-to [data-depth='{depth}']").click()
        page.wait_for_selector(f"{TABLE} tr.lv{level}", timeout=8000)
        idle(page)
        lv = set(table_data(page)["levels"])
        assert lv == set(range(level + 1)), (depth, lv)
    shot(page, "18_expand_to_3")
    rows = level_rows(page)
    assert abs(sum(r[3] or 0 for r in rows if r[0] == 0) - 100) <= 0.6
    for lv in (1, 2, 3):  # a child's share adds up to its parent's: each level sums to 100% of the table
        n_lv = sum(1 for r in rows if r[0] == lv)
        assert abs(sum(r[3] or 0 for r in rows if r[0] == lv) - 100) <= 1.5 + 0.05 * n_lv, lv  # shares are shown rounded to 0.1%
    page.click("#collapse-all")
    assert set(table_data(page)["levels"]) == {0} and len(table_data(page)["rows"]) == n_top
    page.locator("#expand-to [data-depth='2']").click()
    page.wait_for_selector(f"{TABLE} tr.lv2", timeout=8000)
    idle(page)
    assert set(table_data(page)["levels"]) == {0, 1, 2}
    page.locator("#expand-to [data-depth='0']").click()
    assert set(table_data(page)["levels"]) == {0}
    page.click("#expand-all")
    page.wait_for_selector(f"{TABLE} tr.lv3", timeout=8000)
    idle(page)
    assert set(table_data(page)["levels"]) == {0, 1, 2, 3}


def _group_spends(d):
    si = d["heads"].index("spend")
    groups = {}
    for k, lv, r in zip(d["keys"], d["levels"], d["rows"]):
        parent = k.rsplit("|", 1)[0] if lv > 0 else "__root__"
        groups.setdefault((lv, parent), []).append(num(r[si]))
    return groups


def test_sibling_groups_sort_independently(page):
    goto_product(page)
    page.locator("#expand-to [data-depth='2']").click()
    page.wait_for_selector(f"{TABLE} tr.lv2", timeout=8000)
    idle(page)
    page.locator(f"{HEAD} th[data-col=spend] button.thbtn").click()  # spend, descending, inside every group
    groups = _group_spends(table_data(page))
    assert any(len(v) > 1 for (lv, _), v in groups.items() if lv == 1), "need a group with 2+ children"
    for (lv, parent), v in groups.items():
        assert v == sorted(v, reverse=True), (lv, parent, v)
    page.locator(f"{HEAD} th[data-col=spend] button.thbtn").click()  # ascending flips every group on its own
    for (lv, parent), v in _group_spends(table_data(page)).items():
        assert v == sorted(v), (lv, parent, v)


def test_tree_search_and_spend_filter(page):
    goto_product(page)
    n0 = len(table_data(page)["rows"])
    page.check("#flt-spend")
    page.wait_for_timeout(200)
    assert qparams(page).get("spend") == "1"
    sp = [num(v) for v in col(page, "spend")]
    assert all((v or 0) > 0 for v in sp) and len(sp) <= n0
    page.uncheck("#flt-spend")
    page.locator("#expand-to [data-depth='3']").click()
    page.wait_for_selector(f"{TABLE} tr.lv3", timeout=8000)
    idle(page)
    d = table_data(page)
    fam = next(lab for lv, lab in zip(d["levels"], d["labels"]) if lv == 3)
    page.click("#collapse-all")
    page.fill("#flt-q", fam)
    page.wait_for_selector(f'{TABLE} tr.lv3[data-label="{fam}"]', timeout=8000)  # found below the visible level, ancestors kept
    assert qparams(page).get("q") == fam
    assert page.locator(f"{TABLE} tr.lv0").count() >= 1
    shot(page, "19_tree_search")


# ------------------------------------------------------------------ Back
def test_back_button_restores_expanded_sort_and_scroll(page):
    goto_product(page)
    page.locator("#expand-to [data-depth='3']").click()
    page.wait_for_selector(f"{TABLE} tr.lv3", timeout=8000)
    idle(page)
    page.locator(f"{HEAD} th[data-col=spend] button.thbtn").click()
    page.locator(f"{HEAD} th[data-col=spend] button.thbtn").click()  # ascending
    before = table_data(page)
    wrap = f"{TABLE} .tablewrap"
    page.evaluate("(w) => { document.querySelector(w).scrollTop = 320; window.scrollTo(0, 40); }", wrap)
    page.wait_for_timeout(100)
    ty = page.evaluate("(w) => document.querySelector(w).scrollTop", wrap)
    y = page.evaluate("() => window.scrollY")
    assert ty > 100, ty
    url = page.url
    page.locator(f"{TABLE} tr.lv3 a.row-open").last.dispatch_event("click")  # no scroll-into-view side effects
    expect(page.locator("#ad-title")).to_be_visible()
    expect(page.locator("#back")).to_be_enabled()
    page.click("#back")
    wait_rows(page)
    assert page.url == url
    page.wait_for_selector(f"{TABLE} tr.lv3")
    after = table_data(page)
    assert after["keys"] == before["keys"], "expanded rows / order not restored"
    expect(page.locator(f"{HEAD} th[data-col=spend]")).to_have_attribute("aria-sort", "ascending")
    page.wait_for_timeout(250)
    assert abs(page.evaluate("(w) => document.querySelector(w).scrollTop", wrap) - ty) <= 8, "table scroll not restored"
    assert abs(page.evaluate("() => window.scrollY") - y) <= 8, "page scroll not restored"
    shot(page, "20_back_restored")


def test_back_walks_the_drills_and_is_disabled_on_products(page):
    page.goto("/?platform=meta")
    page.wait_for_selector("[data-testid=products-table] tbody tr[data-key]")
    expect(page.locator("#back")).to_be_disabled()
    page.locator("[data-testid=products-table] tbody tr[data-key] a.prodname").first.click()
    wait_rows(page)
    expect(page.locator("#back")).to_be_enabled()
    open_first_row(page)
    expect(page.locator("#level-title")).to_contain_text("ICPs in")
    wait_rows(page)
    page.click("#back")
    expect(page.locator("#level-title")).to_have_text("Market clusters")
    wait_rows(page)
    page.click("#back")
    expect(page.locator("[data-testid=products-table]")).to_be_visible()
    expect(page.locator("#back")).to_be_disabled()


def test_back_without_history_goes_to_parent_level(page):
    cluster, icp, pain = drill_to_families(page)
    ad_href = first_row(page).get_attribute("data-href")
    page.goto(purl(cluster, icp, pain))  # fresh entry: no in-app history
    wait_rows(page)
    expect(page.locator("#back")).to_be_enabled()
    page.click("#back")
    expect(page.locator("#level-title")).to_contain_text("Pain points in")
    assert "pain" not in qparams(page) and qparams(page)["icp"] == icp
    wait_rows(page)
    page.goto(ad_href)
    expect(page.locator("#ad-title")).to_be_visible()
    page.click("#back")  # ad detail -> its pain-point (families) page
    expect(page.locator("#level-title")).to_contain_text("Creatives in")


def test_filter_changes_replace_history_so_back_is_not_an_undo(page):
    page.goto("/?platform=meta")
    page.wait_for_selector("[data-testid=products-table] tbody tr[data-key]")
    page.locator("[data-testid=products-table] tbody tr[data-key] a.prodname").first.click()
    wait_rows(page)
    n_hist = page.evaluate("() => history.length")
    page.locator(".seg[data-window='7d']").click()
    wait_pressed(page, "window", "7d")
    chip(page, "testing").click()
    wait_pressed(page, "stage", "scaling")
    page.select_option("#cmp", "prev")
    idle(page)
    page.locator("#perday").check()
    page.fill("#flt-q", "zzz")
    page.wait_for_timeout(250)
    assert page.evaluate("() => history.length") == n_hist, "filters must not push history entries"
    page.click("#back")
    expect(page.locator("[data-testid=products-table]")).to_be_visible()  # straight back to the products list


# ------------------------------------------------------------------ sorting
@pytest.mark.parametrize("level", ["cluster", "icp", "pain", "families"])
def test_sorting_each_level(page, level):
    goto_product(page)
    depth = {"cluster": 0, "icp": 1, "pain": 2, "families": 3}[level]
    for _ in range(depth):
        open_first_row(page)
        wait_rows(page)
    if level == "families":
        expect(page.locator("#level-title")).to_contain_text("Creatives in")
    for key in ["spend", "spend_share", "impressions", "link_clicks", "ctr", "cpc", "roas", "cpa", "unique_ads", "testing_pct"]:
        sort_both_ways(page, key)
    th = page.locator(f"{HEAD} th[data-col=label]")
    th.locator("button.thbtn").click()
    keys_asc = table_data(page)["labels"]
    th.locator("button.thbtn").click()
    keys_desc = table_data(page)["labels"]
    if len(set(keys_asc)) == len(keys_asc):
        assert keys_asc == list(reversed(keys_desc))
    assert page.locator(f"{HEAD} th[aria-sort=descending], {HEAD} th[aria-sort=ascending]").count() == 1
    shot(page, f"04_sorted_{level}")


def test_sort_is_stable_and_persists_in_url(page):
    goto_product(page)
    page.locator(f"{HEAD} th[data-col=spend] button.thbtn").click()
    q = qparams(page)
    assert q.get("sort") == "spend" and q.get("dir") == "desc"
    before = table_data(page)["keys"]
    page.reload()
    wait_rows(page)
    assert table_data(page)["keys"] == before
    expect(page.locator(f"{HEAD} th[data-col=spend]")).to_have_attribute("aria-sort", "descending")


# ------------------------------------------------------------------ date range
def test_date_presets_update_url_and_refetch(page):
    goto_product(page)
    launch_spend = foot_value(page, "spend")
    reqs = []
    page.on("request", lambda r: reqs.append(r.url) if "/api/products/" in r.url else None)
    for win in ["7d", "30d", "1d", "mtd", "180d", "14d", "60d", "90d"]:
        reqs.clear()
        page.locator(f".seg[data-window='{win}']").click()
        expect(page).to_have_url(re.compile(rf"window={win}(&|$)"))
        wait_pressed(page, "window", win)
        page.wait_for_function("() => /\\d/.test(document.querySelector('#range-label').textContent)")
        assert any(f"window={win}" in u for u in reqs), (win, reqs)
        assert re.search(r"\d+ days?", page.locator("#range-label").inner_text()), page.locator("#range-label").inner_text()
    if IS_MOCK:
        page.locator(".seg[data-window='7d']").click()
        idle(page)
        expect(page.locator("#range-label")).to_have_text("Oct 27 – Nov 2 · 7 days")
        assert foot_value(page, "spend") != launch_spend
    page.locator(".seg[data-window='launch']").click()
    wait_pressed(page, "window", "launch")
    assert "window" not in qparams(page)
    assert foot_value(page, "spend") == launch_spend
    page.locator(".seg[data-window='7d']").click()
    wait_pressed(page, "window", "7d")
    assert num(foot_value(page, "spend")) <= num(launch_spend) + 1
    shot(page, "05_window_7d")


def test_custom_range(page):
    goto_product(page)
    reqs = []
    page.on("request", lambda r: reqs.append(r.url) if "/api/products/" in r.url else None)
    st = page.evaluate("() => fetch('/api/status').then(r => r.json())")
    y, m, d = map(int, st["data_through"].split("-"))
    end = date(y, m, d) - timedelta(days=1)
    start = end - timedelta(days=2)
    page.fill("#range-from", start.isoformat())
    page.fill("#range-to", end.isoformat())
    page.click("#range-apply")
    expect(page).to_have_url(re.compile(rf"from={start.isoformat()}&to={end.isoformat()}"))
    assert "window" not in qparams(page)
    page.wait_for_function("() => /3 days/.test(document.querySelector('#range-label').textContent)")
    assert any(f"from={start.isoformat()}" in u and f"to={end.isoformat()}" in u for u in reqs)
    assert page.locator(".seg[aria-pressed=true][data-window]").count() == 0
    page.fill("#range-from", end.isoformat())
    page.fill("#range-to", end.isoformat())
    page.click("#range-apply")
    page.wait_for_function("() => /1 day/.test(document.querySelector('#range-label').textContent)")
    idle(page)
    open_first_row(page)
    expect(page.locator("#level-title")).to_contain_text("ICPs in")
    q = qparams(page)
    assert q["from"] == q["to"] == end.isoformat()
    page.reload()
    wait_rows(page)
    assert page.input_value("#range-from") == end.isoformat()
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


# ------------------------------------------------------------------ stage chips
def test_stage_chips_toggle_both_ways(page):
    goto_product(page, "?window=14d")
    only_testing_scaling(page)
    expect(chip(page, "testing")).to_have_attribute("aria-pressed", "true")
    expect(chip(page, "scaling")).to_have_attribute("aria-pressed", "true")
    assert all(has_col(page, c) for c in STAGE_COLS)
    scaling_all = num(foot_value(page, "scaling_spend"))
    reqs = []
    page.on("request", lambda r: reqs.append(r.url) if "/api/products/" in r.url else None)
    chip(page, "testing").click()  # Testing off -> only Scaling included
    expect(page).to_have_url(re.compile(r"stage=scaling"))
    wait_pressed(page, "stage", "scaling")
    expect(chip(page, "testing")).to_have_attribute("aria-pressed", "false")
    assert any("stage=scaling" in u for u in reqs)
    assert qparams(page)["stage"] == "scaling"
    assert not any(has_col(page, c) for c in STAGE_COLS), "Testing $ / Scaling $ / Testing % hide with one stage"
    if IS_MOCK:
        assert abs(num(foot_value(page, "spend")) - scaling_all) <= 2
    open_first_row(page)  # carried across a drill
    expect(page.locator("#level-title")).to_contain_text("ICPs in")
    assert qparams(page)["stage"] == "scaling" and qparams(page)["window"] == "14d"
    expect(chip(page, "testing")).to_have_attribute("aria-pressed", "false")
    url = page.url  # at least one stays on: clicking the only active chip does nothing
    chip(page, "scaling").click()
    page.wait_for_timeout(200)
    assert page.url == url
    expect(chip(page, "scaling")).to_have_attribute("aria-pressed", "true")
    chip(page, "testing").click()  # both on again
    wait_pressed(page, "stage", "testing")
    assert qparams(page).get("stage") == stage_all_param(page)  # all = param omitted (or the explicit list while an Unclassified chip is off)
    assert all(has_col(page, c) for c in STAGE_COLS)
    chip(page, "scaling").click()  # Scaling off -> only Testing
    expect(page).to_have_url(re.compile(r"stage=testing"))
    wait_pressed(page, "stage", "testing")
    assert not any(has_col(page, c) for c in STAGE_COLS)
    chip(page, "scaling").click()
    wait_pressed(page, "stage", "scaling")
    assert all(has_col(page, c) for c in STAGE_COLS)
    shot(page, "08_stage_chips")


def test_stage_columns_hidden_with_single_stage_everywhere(page):
    """Testing $ / Scaling $ / Testing % appear only when BOTH Testing and Scaling are included"""
    page.goto("/?platform=meta&stage=scaling")
    page.wait_for_selector("[data-testid=products-table] tbody tr[data-key]")
    assert not any(has_col(page, c, "products-table") for c in STAGE_COLS)
    page.goto("/?platform=meta&stage=testing")
    page.wait_for_selector("[data-testid=products-table] tbody tr[data-key]")
    assert not any(has_col(page, c, "products-table") for c in STAGE_COLS)
    page.goto("/?platform=meta")
    page.wait_for_selector("[data-testid=products-table] tbody tr[data-key]")
    assert all(has_col(page, c, "products-table") for c in STAGE_COLS)
    cluster, icp, pain = drill_to_families(page)
    for params, both in (("", True), ("&stage=testing", False), ("&stage=scaling", False)):
        page.goto(purl(cluster, icp, pain) + params)
        wait_rows(page)
        present = [has_col(page, c) for c in STAGE_COLS]
        assert all(present) if both else not any(present), (params, present)
        d = table_data(page)
        assert len(d["rows"][0]) == len(d["heads"])  # body cells line up with the visible columns
    for params, both in (("", True), ("&stage=scaling", False)):
        page.goto(f"/p/{SLUG}?all=1{params}")
        wait_rows(page)
        present = [has_col(page, c) for c in STAGE_COLS]
        assert all(present) if both else not any(present), (params, present)
    # campaigns view: a card only for each included stage
    page.goto(f"/p/{SLUG}?view=campaigns")
    page.wait_for_selector("[data-testid=campaign-table]")
    idle(page)
    assert cards(page).count() == 2
    page.goto(f"/p/{SLUG}?view=campaigns&stage=scaling")
    page.wait_for_selector("[data-testid=campaign-table]")
    idle(page)
    assert cards(page).count() == 1
    assert page.locator(".stagecard[data-stage=scaling]").count() == 1
    assert page.locator(".stagecard[data-stage=testing]").count() == 0


@pytest.mark.mock_only
def test_unclassified_chip_only_when_present(page):
    goto_product(page)  # Since launch includes unclassified days in the mock
    expect(chip(page, "unclassified")).to_be_visible()
    total = num(foot_value(page, "spend"))
    t, s = num(foot_value(page, "testing_spend")), num(foot_value(page, "scaling_spend"))
    assert total > t + s  # the rest is unclassified
    chip(page, "unclassified").click()
    expect(page).to_have_url(re.compile(r"stage=testing%2Cscaling|stage=testing,scaling"))
    wait_pressed(page, "stage", "testing")
    assert abs(num(foot_value(page, "spend")) - (t + s)) <= 2
    assert all(has_col(page, c) for c in STAGE_COLS)  # Testing + Scaling both still included
    page.goto(f"/p/{SLUG}?window=7d")  # a window without unclassified days hides the chip
    wait_rows(page)
    expect(chip(page, "unclassified")).to_be_hidden()


@pytest.mark.mock_only
def test_stage_chip_totals_match_split(page):
    goto_product(page, "?window=90d")
    t, s = num(foot_value(page, "testing_spend")), num(foot_value(page, "scaling_spend"))
    chip(page, "scaling").click()
    wait_pressed(page, "stage", "testing")
    assert abs(num(foot_value(page, "spend")) - t) <= 2
    chip(page, "scaling").click()
    chip(page, "testing").click()
    wait_pressed(page, "stage", "scaling")
    assert abs(num(foot_value(page, "spend")) - s) <= 2


# ------------------------------------------------------------------ Ads action + flat Meta ads table
def test_row_ads_action_ties_to_row(page):
    goto_product(page)
    row = first_row(page)
    d = table_data(page)
    spend = num(row.locator("td").nth(d["heads"].index("spend")).inner_text())
    key = row.get_attribute("data-label")
    row.locator(".row-ads").click()
    expect(page).to_have_url(re.compile(r"view=ads"))
    assert qparams(page)["cluster"] == key
    wait_rows(page, "ads-table")
    d = table_data(page, "ads-table")
    assert {"ad_name", "campaign_name", "stage", "adset_name", "status", "created_date", "spend", "spend_share", *TRAFFIC_COLS, "purchases", "revenue", "roas", "cpa", "live"} <= set(d["heads"])
    assert "spend_7d" not in d["heads"] and "cpa_7d" not in d["heads"]
    total = num(foot_value(page, "spend", "ads-table"))
    assert abs(total - spend) <= max(2, 0.001 * spend), (total, spend)
    assert page.locator("[data-testid=ads-table] .badge").count() >= 1
    crumbs = page.locator(".crumbs li").all_inner_texts()
    assert crumbs[-1] == "Ads" and key in crumbs
    sort_both_ways(page, "spend", "ads-table")
    n_rows = len(table_data(page, "ads-table")["rows"])
    sh = [num(v) for v in col(page, "spend_share", "ads-table")]
    if n_rows < 400:  # (long tables render 400 rows at a time)
        assert abs(sum(s or 0 for s in sh) - 100) <= 1.5 + 0.05 * n_rows or spend == 0
    else:
        assert sum(s or 0 for s in sh) <= 100.5
    shot(page, "09_ads_table")
    page.locator("[data-testid=ads-table] tbody tr.clickable").first.locator("td.num").first.click()
    expect(page).to_have_url(re.compile(r"/ad/\d+"))


def test_all_ads_in_view_header_link(page):
    goto_product(page)
    open_first_row(page)
    wait_rows(page)
    page.locator("#all-ads").click()
    expect(page).to_have_url(re.compile(r"view=ads"))
    assert "cluster" in qparams(page)
    wait_rows(page, "ads-table")


def test_family_row_inline_meta_ads_and_ads_action(page):
    drill_to_families(page)
    row = first_row(page)
    caret = row.locator("button.caret")
    caret.click()
    expect(caret).to_have_attribute("aria-expanded", "true")
    page.wait_for_selector(f"{TABLE} tr.lv4", timeout=8000)
    n = page.locator(f"{TABLE} tr.lv4").count()
    assert n >= 1
    kid = page.locator(f"{TABLE} tr.lv4").first
    assert kid.locator(".badge[class*=stage-]").count() >= 1 or kid.get_attribute("data-label").startswith("(")
    shot(page, "10_family_expanded")
    page.locator(f"{HEAD} th[data-col=spend] button.thbtn").click()  # a re-sort keeps the expansion
    assert page.locator(f"{TABLE} tr.lv4").count() == n
    page.locator(f"{BODY_ROWS}.lv3 .row-ads").first.click()  # per-row Ads action -> family-filtered ads table
    expect(page).to_have_url(re.compile(r"view=ads"))
    assert "family_key" in qparams(page)
    wait_rows(page, "ads-table")
    expect(page.locator("[data-testid=ads-table] > .tablewrap > table > thead th[data-col=spend_share]")).to_contain_text("% of creative")


def test_families_flat_view_filters_and_deep_link(page):
    goto_product(page, "?all=1")
    expect(page.locator("#level-title")).to_have_text("All creatives")
    n_all = len(table_data(page)["rows"])
    page.check("#flt-spend")
    expect(page).to_have_url(re.compile(r"spend=1"))
    n_spend = len(table_data(page)["rows"])
    sp = [num(v) for v in col(page, "spend")]
    assert n_spend <= n_all and all((v or 0) > 0 for v in sp)
    needle = table_data(page)["keys"][0].split("|")[0][:6]
    page.fill("#flt-q", needle)
    page.wait_for_timeout(350)
    keys = [k.lower() for k in table_data(page)["keys"]]
    assert keys and all(needle.lower() in k for k in keys), (needle, keys)
    assert qparams(page).get("q") == needle
    page.reload()
    wait_rows(page)
    assert page.is_checked("#flt-spend")
    assert page.input_value("#flt-q") == needle
    shot(page, "11_families_filtered")
    page.fill("#flt-q", "")
    page.uncheck("#flt-spend")
    page.wait_for_timeout(250)
    first = page.locator(f"{TABLE} tbody tr[data-key]").first
    first.locator("td.num").first.click()  # flat table: a click drops down the Meta ads (default mode)
    page.wait_for_selector(f"{TABLE} tr.expandrow", timeout=5000)
    page.click("#toggle-all")
    expect(page.locator("#level-title")).to_have_text("Market clusters")


def test_all_ad_families_toggle(page):
    goto_product(page)
    page.click("#toggle-all")
    expect(page.locator("#level-title")).to_have_text("All creatives")
    wait_rows(page)
    assert qparams(page).get("all") == "1"
    assert len(table_data(page)["rows"]) >= 1


# ------------------------------------------------------------------ campaigns view
def test_campaigns_view(page):
    goto_product(page)
    page.locator("a[data-view=campaigns]").click()
    expect(page).to_have_url(re.compile(r"view=campaigns"))
    page.wait_for_selector("[data-testid=campaign-table]")
    idle(page)
    if not page.locator("[data-testid=campaign-table] tbody tr[data-key]").count():
        pytest.skip("no campaign-level data loaded yet")
    assert cards(page).count() == 2
    assert "7d" not in page.locator(".stagecards").inner_text()
    expect(page.locator("#stage-chart")).to_be_visible()
    ds = page.evaluate("() => { const c = Chart.getChart('stage-chart'); return {n: c.data.datasets.length, days: c.data.labels.length, stacked: c.options.scales.x.stacked}; }")
    assert ds["n"] == 3 and ds["days"] > 5 and ds["stacked"] is True
    assert not has_col(page, "spend_7d", "campaign-table")
    sort_both_ways(page, "spend", "campaign-table")
    sort_both_ways(page, "roas", "campaign-table")
    sh = [num(v) for v in col(page, "spend_share", "campaign-table")]
    if IS_MOCK:
        assert abs(sum(s or 0 for s in sh) - 100) <= 1.5 + 0.05 * len(sh)
    else:  # real data may still hold days without a campaign (they count in the total but in no campaign row)
        assert sum(s or 0 for s in sh) <= 100.5
    shot(page, "12_campaigns")
    only_testing_scaling(page)
    chip(page, "scaling").click()  # the global stage chips filter the campaign table
    wait_pressed(page, "stage", "testing")
    page.wait_for_selector("[data-testid=campaign-table] tbody tr[data-key], [data-testid=campaign-table] tr.emptyrow")
    assert all(s == "testing" for s in col(page, "stage", "campaign-table"))
    chip(page, "scaling").click()
    idle(page)
    assert qparams(page).get("stage") == stage_all_param(page)
    page.wait_for_selector("[data-testid=campaign-table] tbody tr[data-key]")
    name = page.locator("[data-testid=campaign-table] tbody tr[data-key] a.rowlink").first.inner_text()
    camp_spend = num(col(page, "spend", "campaign-table")[0])
    page.locator("[data-testid=campaign-table] tbody tr[data-key]").first.locator("td.num").first.click()
    expect(page).to_have_url(re.compile(r"view=ads&campaign_id=|campaign_id=.*view=ads"))
    wait_rows(page, "ads-table")
    crumbs = page.locator(".crumbs li").all_inner_texts()
    assert crumbs[2] == "Campaigns" and crumbs[3] == name and crumbs[-1] == "Ads", crumbs
    assert abs(num(foot_value(page, "spend", "ads-table")) - camp_spend) <= max(2, 0.001 * camp_spend)
    assert set(col(page, "campaign_name", "ads-table")) == {name}
    shot(page, "13_campaign_ads")
    page.locator("[data-testid=ads-table] tbody tr.clickable").first.locator("td.num").first.click()
    expect(page).to_have_url(re.compile(r"/ad/\d+"))
    page.go_back()
    page.locator(".crumbs a", has_text="Campaigns").click()
    expect(page).to_have_url(re.compile(r"view=campaigns"))


def test_unmatched_panel(page):
    goto_product(page)
    panel = page.locator("#unmatched")
    expect(panel).to_be_visible()
    panel.locator("summary").click()
    page.wait_for_selector("[data-testid=unmatched-table] tbody tr[data-key], [data-testid=unmatched-table] tr.emptyrow")
    d = table_data(page, "unmatched-table")
    assert "spend_7d" not in d["heads"]
    if d["rows"]:
        i = d["heads"].index("spend")
        s = sum(num(r[i]) or 0 for r in d["rows"])
        if len(d["rows"]) < 400:  # (long tables render 400 rows at a time)
            assert abs(s - num(d["foot"][i])) <= len(d["rows"])
        assert "spend_share" in d["heads"]
        sort_both_ways(page, "spend", "unmatched-table")
    shot(page, "14_unmatched")


# ------------------------------------------------------------------ ad detail
def open_first_ad(page, params=""):
    drill_to_families(page)
    href = first_row(page).get_attribute("data-href")
    page.goto(href + (("&" if "?" in href else "?") + params.lstrip("?&") if params else ""))
    expect(page.locator("#ad-title")).to_be_visible()
    wait_trend(page)


def test_ad_detail_sections(page):
    open_first_ad(page, "window=7d")
    shot(page, "15_ad_detail")
    expect(page.locator("#family-metrics")).to_be_visible()
    assert page.locator("#family-metrics tbody tr[data-row]").count() >= 4
    expect(page.locator("#tag-block")).to_be_visible()
    expect(page.locator("#copy-block")).to_be_visible()
    expect(page.locator("#media")).to_be_visible()
    img_ok = page.evaluate("() => { const i = document.querySelector('#media img'); return i ? i.complete && i.naturalWidth > 0 : null; }")
    assert img_ok in (True, None)
    if page.locator("#tag-block .badge.method").count():
        assert page.locator("#tag-block .badge[class*=conf-]").count() == 1
        expect(page.locator("#reliability-note")).to_contain_text("re-tag")
        expect(page.locator("#tag-block")).to_contain_text("What was seen")
        expect(page.locator("#line-block")).to_be_visible()
        expect(page.locator("#line-block table.ingredients tbody tr").first).to_be_visible()
    if page.locator("#meta-ads").count():
        expect(page.locator("#meta-ads")).to_be_visible()
        expect(page.locator("#meta-ads th[data-col=spend_share]")).to_contain_text("% of creative")
    # trend panel on the ad page: full series since daily_start, the selected window shaded (window A)
    expect(page.locator("#trend-panel")).to_be_visible()
    info = trend_info(page)
    assert info["n"] == 2 and len(info["labels"]) >= 100 and [d["type"] for d in info["ds"]] == ["bar", "line"]  # bars (Spend) + line (ROAS)
    sh = info["shades"]
    assert len(sh) == 1 and sh[0]["i1"] == len(info["labels"]) - 1 and sh[0]["i1"] - sh[0]["i0"] == 6, sh
    if IS_MOCK:
        assert len(info["labels"]) == 181 and info["labels"][0] == "2026-05-06" and info["labels"][-1] == "2026-11-02"
    assert info["axes"]["left"]["display"] and info["axes"]["right"]["display"]
    expect(page.locator("#family-metrics")).to_contain_text("days")


@pytest.mark.mock_only
def test_ad_detail_mock_content(page):
    api = page.context.request
    fams = api.get(f"/api/products/{SLUG}/families").json()["rows"]
    two = next(r for r in fams if r["n_rows"] > 1 and r["counted_here"])
    page.goto(f"/ad/{two['primary_id']}?window=30d")
    expect(page.locator("#ad-title")).to_have_text("Creative: " + two["display"])
    expect(page.locator("#siblings")).to_be_visible()
    assert page.locator("#siblings tbody tr").count() == two["n_rows"] - 1
    expect(page.locator("#tag-block .badge[class*=conf-]")).to_have_count(1)
    expect(page.locator("#campaigns")).to_be_visible()
    expect(page.locator("#meta-ads table")).to_be_visible()
    bl = api.get("/api/products/beef-liver/families").json()["rows"][0]
    page.goto(f"/ad/{bl['primary_id']}")
    expect(page.locator("#tag-block")).to_contain_text("Not line-mapped")
    assert page.locator("#line-block").count() == 0
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
    page.goto(f"/p/{SLUG}?all=1")
    wait_rows(page)
    assert "XSS-<b>test</b>-family" in page.locator(TABLE).inner_text()
    assert page.locator(f"{TABLE} b").count() == 0
    page.goto(f"/ad/{x['primary_id']}")
    expect(page.locator("#ad-title")).to_have_text("Creative: XSS-<b>test</b>-family")
    expect(page.locator("#copy-block")).to_contain_text("<img src=x onerror=window.__xss=1>")
    assert page.evaluate("() => window.__xss") is None
    assert page.locator("a[href^='javascript:']").count() == 0
    assert page.locator("#copy-block img, #copy-block b, #copy-block script").count() == 0
    drill = api.get(f"/api/products/{SLUG}/tree?root=cluster&depth=3").json()["rows"]  # and in the dropdown tree
    row = next(r for r in drill if r["level"] == 3 and r["label"].startswith("XSS"))
    page.goto(purl(row["cluster"], row["icp"], row["pain"]))
    wait_rows(page)
    assert page.locator(f"{TABLE} b").count() == 0
    assert page.evaluate("() => window.__xss") is None


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


# ------------------------------------------------------------------ compare mode
def test_compare_on_off_cells_and_colors(page):
    goto_product(page, "?window=7d")
    assert page.locator(f"{BODY_ROWS} .cmpcell").count() == 0
    page.select_option("#cmp", "prev")
    expect(page).to_have_url(re.compile(r"compare=prev"))
    idle(page)
    if IS_MOCK:
        expect(page.locator("#range-label")).to_have_text("Oct 27 – Nov 2 (7 days) vs Oct 20 – Oct 26 (7 days)")
    else:
        expect(page.locator("#range-label")).to_contain_text(" vs ")
    heads = table_data(page)["heads"]
    for key in ["spend", "spend_share", "impressions", "link_clicks", "ctr", "cpc", "purchases", "revenue", "roas", "cpa"]:  # A on line 1, "vs B ▲x%" on line 2
        cells = page.locator(f"{BODY_ROWS} td:nth-child({heads.index(key) + 1}) .cmpcell")
        assert cells.count() >= 1, key
        assert cells.first.locator(".b").inner_text().startswith("vs "), key
    assert page.locator(f"{TABLE} tfoot .cmpcell").count() >= 5  # totals row too
    for key, good_up in [("roas", True), ("purchases", True), ("revenue", True), ("cpa", False), ("spend", None), ("ctr", True), ("cpc", False), ("impressions", None), ("link_clicks", None)]:
        i = heads.index(key) + 1
        for el in page.locator(f"{BODY_ROWS} td:nth-child({i}) .cmpcell .delta").all():
            cls, text = el.get_attribute("class"), el.inner_text()
            if good_up is None:
                assert "neutral" in cls, (key, cls)
            elif text.startswith("▲"):
                assert ("good" in cls) == good_up and ("bad" in cls) == (not good_up), (key, text, cls)
            elif text.startswith("▼"):
                assert ("good" in cls) == (not good_up) and ("bad" in cls) == good_up, (key, text, cls)
    share_b = page.locator(f"{BODY_ROWS} td:nth-child({heads.index('spend_share') + 1}) .cmpcell .b").first.inner_text()
    assert re.search(r"vs \d+\.\d%.*pp", share_b), share_b  # % of spend: change in percentage points
    ctr_b = page.locator(f"{BODY_ROWS} td:nth-child({heads.index('ctr') + 1}) .cmpcell .b").first.inner_text()
    assert re.search(r"vs \d+\.\d\d% [▲▼]\d+\.\d\dpp|vs –", ctr_b), ctr_b  # CTR: change in percentage points
    cpc_b = page.locator(f"{BODY_ROWS} td:nth-child({heads.index('cpc') + 1}) .cmpcell .b").first.inner_text()
    assert re.search(r"vs \$\d+\.\d\d", cpc_b), cpc_b
    shot(page, "21_compare")
    page.select_option("#cmp", "off")
    expect(page).not_to_have_url(re.compile(r"compare="))
    idle(page)
    assert page.locator(f"{BODY_ROWS} .cmpcell").count() == 0


def test_compare_presets_custom_and_clamped(page):
    goto_product(page, "?window=7d")
    for v in ["7d", "14d", "30d", "60d", "90d"]:
        page.select_option("#cmp", v)
        expect(page).to_have_url(re.compile(rf"compare={v}"))
        idle(page)
        assert page.locator(f"{BODY_ROWS} .cmpcell").count() >= 1
    page.select_option("#cmp", "custom")
    expect(page.locator("#cmp-from")).to_be_visible()
    page.fill("#cmp-from", "2026-09-01")
    page.fill("#cmp-to", "2026-09-07")
    page.click("#cmp-apply")
    expect(page).to_have_url(re.compile(r"cfrom=2026-09-01&cto=2026-09-07"))
    assert "compare" not in qparams(page)
    idle(page)
    expect(page.locator("#range-label")).to_contain_text("Sep 1 – Sep 7 (7 days)")
    page.fill("#cmp-from", "2026-03-01")  # B cut to the available data -> notice
    page.fill("#cmp-to", "2026-05-10")
    page.click("#cmp-apply")
    idle(page)
    expect(page.locator("#range-note")).to_contain_text("Compare range clamped")
    assert "warn" in page.locator("#range-note").get_attribute("class")
    page.fill("#cmp-from", "2026-09-10")
    page.fill("#cmp-to", "2026-09-01")
    page.click("#cmp-apply")
    expect(page.locator("#cmp-err")).to_contain_text("after")
    shot(page, "22_compare_custom")


def test_compare_per_day_toggle(page):
    goto_product(page, "?window=7d&compare=30d")
    api = page.context.request
    assert page.is_visible("#perday") and page.is_checked("#perday")  # windows differ in length -> on by default
    heads = [t.replace("\n", " ").replace(" ", "") for t in page.locator(f"{HEAD} th").all_inner_texts()]
    assert any("Spend/day" in h for h in heads), heads
    per_day = [num(v) for v in col(page, "spend")]
    raw = api.get(f"/api/products/{SLUG}/tree?root=cluster&depth=0&window=7d").json()
    raw_spend = {r["label"]: r["m"]["spend"] for r in raw["rows"]}
    for lab, v in zip(table_data(page)["labels"], per_day):
        assert v is not None and abs(v - raw_spend[lab] / 7) <= 1.01, (lab, v, raw_spend[lab] / 7)
    b_text = page.locator(f"{BODY_ROWS} td:nth-child({table_data(page)['heads'].index('spend') + 1}) .cmpcell .b").first.inner_text()
    assert re.search(r"vs \$[\d,]+", b_text)
    reqs = []
    page.on("request", lambda r: reqs.append(r.url) if "/api/products/" in r.url else None)
    page.uncheck("#perday")  # raw totals, URL perday=0, no refetch needed
    assert qparams(page)["perday"] == "0"
    assert not reqs, "toggling Per day must not refetch"
    for lab, v in zip(table_data(page)["labels"], [num(v) for v in col(page, "spend")]):
        assert abs(v - raw_spend[lab]) <= 1.01
    heads = [t.replace("\n", " ").replace(" ", "") for t in page.locator(f"{HEAD} th").all_inner_texts()]
    assert not any("/day" in h for h in heads)
    page.check("#perday")
    assert qparams(page)["perday"] == "1"
    page.goto(f"/p/{SLUG}?window=7d&compare=prev")  # equal-length windows default to off
    wait_rows(page)
    assert page.is_visible("#perday") and not page.is_checked("#perday")
    page.check("#perday")  # and the state survives a drill-down
    open_first_row(page)
    wait_rows(page)
    assert qparams(page)["perday"] == "1" and page.is_checked("#perday")


def test_compare_sort_by_delta(page):
    goto_product(page, "?window=7d&compare=prev")
    heads = table_data(page)["heads"]
    th = page.locator(f"{HEAD} th[data-col=spend]")
    th.locator("button.thbtn").click()  # a header click sorts by A
    assert th.get_attribute("data-sortby") == "value"
    th.locator("button.deltabtn").click()  # the small Δ toggle sorts by % change
    expect(page).to_have_url(re.compile(r"by=delta"))
    assert qparams(page)["sort"] == "spend"
    assert th.get_attribute("data-sortby") == "delta"
    assert th.get_attribute("aria-sort") == "descending"
    i = heads.index("spend")
    deltas = [delta_pct(c) for c in page.locator(f"{BODY_ROWS} td:nth-child({i + 1})").all_inner_texts()]
    nn = [d for d in deltas if d is not None]
    assert nn == sorted(nn, reverse=True), deltas
    assert all(d is None for d in deltas[len(nn):]), "rows without a delta go last"
    th.locator("button.deltabtn").click()
    assert th.get_attribute("aria-sort") == "ascending"
    deltas = [delta_pct(c) for c in page.locator(f"{BODY_ROWS} td:nth-child({i + 1})").all_inner_texts()]
    nn = [d for d in deltas if d is not None]
    assert nn == sorted(nn), deltas
    th2 = page.locator(f"{HEAD} th[data-col=roas]")  # shift-click = by delta; plain click goes back to value
    th2.locator("button.thbtn").click(modifiers=["Shift"])
    assert th2.get_attribute("data-sortby") == "delta"
    th2.locator("button.thbtn").click()
    assert th2.get_attribute("data-sortby") == "value"
    assert "by" not in qparams(page)
    assert page.locator(f"{HEAD} th[data-col=unique_ads] button.deltabtn").count() == 0  # not comparable: no Δ toggle
    th.locator("button.deltabtn").click()
    page.reload()
    wait_rows(page)
    assert page.locator(f"{HEAD} th[data-col=spend]").get_attribute("data-sortby") == "delta"
    shot(page, "23_compare_sort_delta")


def test_compare_url_persistence_and_drill(page):
    goto_product(page, "?window=14d&compare=30d")
    open_first_row(page)
    wait_rows(page)
    q = qparams(page)
    assert q["window"] == "14d" and q["compare"] == "30d" and "cluster" in q
    expect(page.locator("#cmp")).to_have_value("30d")
    assert page.locator(f"{BODY_ROWS} .cmpcell").count() >= 1
    page.reload()
    wait_rows(page)
    assert qparams(page)["compare"] == "30d"
    expect(page.locator("#cmp")).to_have_value("30d")
    open_first_row(page)
    wait_rows(page)
    open_first_row(page)
    wait_rows(page)
    first_row(page).locator("a.row-open").click()
    expect(page.locator("#ad-title")).to_be_visible()
    assert qparams(page)["compare"] == "30d" and qparams(page)["window"] == "14d"
    page.locator(".crumbs a").nth(1).click()
    assert qparams(page)["compare"] == "30d"


def test_compare_with_since_launch_switches_window_and_says_so(page):
    goto_product(page)  # Since launch
    page.select_option("#cmp", "30d")
    expect(page).to_have_url(re.compile(r"compare=30d"))
    assert qparams(page)["window"] == "7d", "A switched to 7d"
    idle(page)
    expect(page.locator("#range-note")).to_contain_text("switched to 7d")
    wait_pressed(page, "window", "7d")
    page.locator(".seg[data-window='launch']").click()  # picking Since launch turns the comparison off and says so
    idle(page)
    assert "compare" not in qparams(page) and "cfrom" not in qparams(page)
    expect(page.locator("#cmp")).to_have_value("off")
    expect(page.locator("#range-note")).to_contain_text("Comparison turned off")
    assert page.locator(f"{BODY_ROWS} .cmpcell").count() == 0
    page.goto(f"/p/{SLUG}?compare=prev")  # a deep link with compare but no date window is normalised to 7d
    wait_rows(page)
    assert qparams(page)["window"] == "7d"
    assert page.locator(f"{BODY_ROWS} .cmpcell").count() >= 1


def test_compare_ad_detail_table_and_two_shades(page):
    open_first_ad(page, "window=7d&compare=prev")
    expect(page.locator("#compare-table")).to_be_visible()
    assert page.locator("#compare-table tbody tr[data-metric]").count() == 5
    for metric in ("spend", "purchases", "revenue", "roas", "cpa"):
        assert page.locator(f"#compare-table tr[data-metric={metric}] td").count() == 4  # A | B | Δ | Δ %
    head = page.locator("#compare-table thead").inner_text()
    assert "A" in head and "B" in head and "Δ" in head
    sh = trend_info(page)["shades"]
    assert len(sh) == 2 and sh[0]["i0"] > sh[1]["i0"], sh  # A (recent) and B (earlier), two tints
    expect(page.locator("#trend-note")).to_contain_text("window A")
    if page.locator("#meta-ads").count():
        assert page.locator("#meta-ads .cmpcell").count() >= 1
    page.select_option("#cmp", "30d")
    idle(page)
    assert page.is_checked("#perday")
    assert "/day" in page.locator("#compare-table").inner_text().replace(" ", "")
    page.uncheck("#perday")  # rebuilt without a reload
    assert "/day" not in page.locator("#compare-table").inner_text().replace(" ", "")
    shot(page, "24_compare_ad_detail")


def test_compare_campaign_cards_and_ads_table(page):
    page.goto(f"/p/{SLUG}?view=campaigns&window=7d&compare=prev")
    page.wait_for_selector("[data-testid=campaign-table]")
    idle(page)
    if not page.locator("[data-testid=campaign-table] tbody tr[data-key]").count():
        pytest.skip("no campaign-level data loaded yet")
    cs = cards(page)
    assert cs.count() == 2
    for i in range(2):
        assert " vs " in cs.nth(i).inner_text().replace("\n", " ")
    assert page.locator("[data-testid=campaign-table] .cmpcell").count() >= 1
    expect(page.locator("#range-label")).to_contain_text(" vs ")
    page.locator("[data-testid=campaign-table] tbody tr[data-key] a.rowlink").first.click()
    wait_rows(page, "ads-table")
    assert page.locator("[data-testid=ads-table] tfoot .cmpcell").count() >= 1
    sort_both_ways(page, "spend", "ads-table")
    shot(page, "25_compare_campaigns")


# ------------------------------------------------------------------ traffic columns (v1.4) + API-driven copy (v1.5)
def test_traffic_columns_formats_and_totals(page):
    goto_product(page, "?window=30d")
    heads = table_data(page)["heads"]
    assert all(c in heads for c in TRAFFIC_COLS)
    for key, pat in [("impressions", r"[\d,]+|–"), ("link_clicks", r"[\d,]+|–"), ("ctr", r"\d+\.\d\d%|–"), ("cpc", r"\$\d+\.\d\d|–")]:
        vals = col(page, key)
        assert all(re.fullmatch(pat, v) for v in vals), (key, vals)
        assert re.fullmatch(pat, foot_value(page, key)), (key, foot_value(page, key))
    # a 4-digit CPC keeps its cents ($2.74, not $3); totals recompute from sums (CPC = spend / clicks)
    spend, clicks = num(foot_value(page, "spend")), num(foot_value(page, "link_clicks"))
    impressions = num(foot_value(page, "impressions"))
    if IS_MOCK and clicks:
        assert abs(num(foot_value(page, "cpc")) - spend / clicks) <= 0.01 + 0.002 * spend / clicks
    if IS_MOCK and impressions:
        assert abs(num(foot_value(page, "ctr")) - 100 * clicks / impressions) <= 0.35  # CTR counts days with impressions only
    shot(page, "26_traffic_columns")


def test_columns_chip_group_toggles_and_persists(page):
    goto_product(page)
    traffic, advanced = page.locator(".cchip[data-cols=traffic]"), page.locator(".cchip[data-cols=advanced]")
    expect(page.locator(".colchips .ctl-name").first).to_have_text("Columns")
    expect(traffic).to_have_attribute("aria-pressed", "true")  # Traffic on by default
    expect(advanced).to_have_attribute("aria-pressed", "false")  # Advanced off by default
    assert all(has_col(page, c) for c in TRAFFIC_COLS) and not any(has_col(page, c) for c in ADVANCED_COLS)
    n_before = len(table_data(page)["rows"])
    advanced.click()  # CPM + Conv rate appear, no refetch
    expect(advanced).to_have_attribute("aria-pressed", "true")
    assert all(has_col(page, c) for c in ADVANCED_COLS)
    vals = col(page, "cpm") + col(page, "conv_rate")
    assert all(re.fullmatch(r"\$\d+\.\d\d|\d+\.\d\d%|–", v) for v in vals), vals
    assert len(table_data(page)["rows"]) == n_before
    traffic.click()  # Traffic off -> its four columns go away
    assert not any(has_col(page, c) for c in TRAFFIC_COLS) and all(has_col(page, c) for c in ADVANCED_COLS)
    assert page.evaluate("() => [localStorage.getItem('tagexp.cols.traffic'), localStorage.getItem('tagexp.cols.advanced')]") == ["0", "1"]
    page.reload()  # persisted in localStorage
    wait_rows(page)
    assert not any(has_col(page, c) for c in TRAFFIC_COLS) and all(has_col(page, c) for c in ADVANCED_COLS)
    expect(page.locator(".cchip[data-cols=advanced]")).to_have_attribute("aria-pressed", "true")
    # the same choice applies on every table: landing, ads, campaigns, flat families, unmatched, ad meta-ads
    page.goto("/?platform=meta")
    page.wait_for_selector("[data-testid=products-table] tbody tr[data-key]")
    assert not has_col(page, "impressions", "products-table") and has_col(page, "cpm", "products-table")
    page.goto(f"/p/{SLUG}?view=ads")
    wait_rows(page, "ads-table")
    assert has_col(page, "cpm", "ads-table") and not has_col(page, "ctr", "ads-table")
    page.goto(f"/p/{SLUG}?view=campaigns")
    page.wait_for_selector("[data-testid=campaign-table] tbody tr[data-key]")
    assert has_col(page, "conv_rate", "campaign-table") and not has_col(page, "cpc", "campaign-table")
    page.locator(".cchip[data-cols=traffic]").click()
    assert has_col(page, "cpc", "campaign-table")
    page.goto(f"/p/{SLUG}?all=1")
    wait_rows(page)
    assert has_col(page, "ctr") and has_col(page, "cpm")
    page.locator("#unmatched summary").click()
    page.wait_for_selector("[data-testid=unmatched-table]")
    assert has_col(page, "link_clicks", "unmatched-table") and has_col(page, "conv_rate", "unmatched-table")


@pytest.mark.mock_only
def test_traffic_null_days_show_dash_and_the_api_note(page):
    # days before the API pull range have link clicks but no impressions: impressions / CTR / CPM are blank, CPC still computes
    page.locator  # noqa
    page.goto(f"/p/{SLUG}?from=2026-05-06&to=2026-06-10")
    wait_rows(page)
    page.locator(".cchip[data-cols=advanced]").click()
    assert set(col(page, "impressions")) == {"–"} and set(col(page, "ctr")) == {"–"} and set(col(page, "cpm")) == {"–"}
    assert any(re.fullmatch(r"\$\d+\.\d\d", v) for v in col(page, "cpc"))
    assert any(re.fullmatch(r"[\d,]+", v) for v in col(page, "link_clicks"))
    expect(page.locator("#range-note")).to_contain_text("Impressions are available from Jun 20")  # window_info.note is shown
    # a window with impressions on every day has no note; Since launch no longer carries a "May 6" caveat
    page.goto(f"/p/{SLUG}?window=14d")
    wait_rows(page)
    expect(page.locator("#range-note")).to_have_text("")
    page.goto(f"/p/{SLUG}")
    wait_rows(page)
    assert "May 6" not in page.locator("#range-note").inner_text()
    assert "Impressions" not in page.locator("#range-note").inner_text()


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


def test_source_line_and_no_sheet_wording(page):
    for path in ("/?platform=meta", f"/p/{SLUG}", f"/p/{SLUG}?view=ads", f"/p/{SLUG}?view=campaigns", f"/p/{SLUG}?window=7d&compare=prev"):
        page.goto(path)
        page.wait_for_selector(".tablebox")
        idle(page)
        src = page.locator("#source").inner_text()
        assert src.startswith("Source: Meta Marketing API, refreshed"), src
        text = page.locator("body").inner_text().lower()
        assert not re.search(r"\bsheets?\b|\btabs?\b|ties to|google sheets?", text), re.findall(r"\bsheets?\b|\btabs?\b|ties to|google sheets?", text)   # Google is a channel now, Google Sheets is still not a source
        expect(page.locator("#data-through")).to_contain_text("Data through")  # from /api/status
    open_first_ad(page)
    text = page.locator("body").inner_text().lower()
    assert not re.search(r"\bsheets?\b|\btabs?\b|ties to|google sheets?", text)
    assert page.locator("#source").inner_text().startswith("Source: Meta Marketing API")


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


# ------------------------------------------------------------------ deep links, 401, layout
def test_deep_link_reload_keeps_state(page):
    labels = drill_to_families(page)
    page.locator(".seg[data-window='14d']").click()
    wait_pressed(page, "window", "14d")
    only_testing_scaling(page)
    chip(page, "testing").click()
    wait_pressed(page, "stage", "scaling")
    page.locator(f"{HEAD} th[data-col=spend] button.thbtn").click()
    page.locator(f"{HEAD} th[data-col=spend] button.thbtn").click()
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
    expect(chip(page, "scaling")).to_have_attribute("aria-pressed", "true")
    expect(chip(page, "testing")).to_have_attribute("aria-pressed", "false")
    expect(page.locator("#level-title")).to_contain_text("Creatives in")
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
    page.fill("#username", USERNAME)
    page.fill("#password", PASSWORD)
    page.click("button[type=submit]")
    expect(page).to_have_url(re.compile(r"/p/" + SLUG + r".*window=30d"))
    wait_rows(page)


@pytest.mark.allow_errors
def test_bad_window_error_is_shown_with_retry(page):
    page.goto(f"/p/{SLUG}?window=banana")
    expect(page.locator(".errorbox")).to_be_visible()
    expect(page.locator(".errorbox")).to_contain_text("window")
    expect(page.locator(".errorbox button")).to_have_text("Retry")
    page.goto(f"/p/{SLUG}?window=7d")
    wait_rows(page)


def test_no_console_errors_across_pages(page):
    """walk every page type; the fixture fails the test on any console error / page error"""
    page.goto("/?platform=meta")
    page.wait_for_selector("[data-testid=products-table] tbody tr[data-key]")
    drill_to_families(page)
    page.locator(f"{BODY_ROWS} button.caret").first.click()
    page.wait_for_selector(f"{TABLE} tr.lv4", timeout=8000)
    first_row(page).locator("a.row-open").click()
    wait_trend(page)
    page.go_back()
    wait_rows(page)
    page.goto(f"/p/{SLUG}?view=campaigns&window=30d&compare=prev")
    page.wait_for_selector("[data-testid=campaign-table] tbody tr")
    page.goto(f"/p/{SLUG}?view=ads&stage=testing&window=14d")
    page.wait_for_selector("[data-testid=ads-table]")
    page.goto(f"/p/{SLUG}")
    wait_rows(page)
    page.click("#expand-all")
    page.wait_for_selector(f"{TABLE} tr.lv3", timeout=8000)
    page.locator("#unmatched summary").click()
    page.wait_for_selector("[data-testid=unmatched-table]")
    page.goto(f"/p/{SLUG}?all=1&window=7d&compare=30d")
    wait_rows(page)
    page.evaluate("() => { history.pushState(null, '', '/zzz'); dispatchEvent(new PopStateEvent('popstate')); }")
    expect(page.locator("h1")).to_have_text("Not found")


@pytest.mark.parametrize("path", ["/?platform=meta", f"/p/{SLUG}", f"/p/{SLUG}?view=campaigns", f"/p/{SLUG}?all=1", f"/p/{SLUG}?window=7d&compare=prev"])
def test_no_page_level_horizontal_scroll_at_laptop_width(ctx, page, path):
    page.set_viewport_size({"width": 1280, "height": 720})
    page.goto(path)
    page.wait_for_selector(".tablebox")
    idle(page)
    page.wait_for_timeout(200)
    sw = page.evaluate("() => [document.documentElement.scrollWidth, window.innerWidth]")
    assert sw[0] <= sw[1] + 1, sw


# ------------------------------------------------------------------ QA fixes (D1-D9)
def ad_page_url(page, **extra):
    """open the first family's page from the families table and return its URL path (without query) + id"""
    drill_to_families(page)
    open_first_row(page)
    page.wait_for_selector("#family-metrics")
    idle(page)
    m = re.search(r"/ad/(\d+)", page.url)
    assert m
    return m.group(0)


def test_inline_meta_ad_rows_show_the_traffic_columns(page):
    """D1: the Meta ads under a creative (inline rows) carry impressions / link clicks / CTR / CPC like every other row"""
    drill_to_families(page)
    page.locator(".seg[data-window='30d']").click()
    wait_pressed(page, "window", "30d")
    wait_rows(page)
    d = table_data(page)
    si = d["heads"].index("spend")
    idx = next(i for i, r in enumerate(d["rows"]) if (num(r[si]) or 0) > 0)
    page.locator(BODY_ROWS).nth(idx).locator("button.caret").click()
    page.wait_for_selector(f"{TABLE} tr.lv4", timeout=8000)
    idle(page)
    d = table_data(page)
    ad_rows = [(k, r) for k, lv, r in zip(d["keys"], d["levels"], d["rows"]) if lv == 4 and ":rem" not in k and (num(r[si]) or 0) > 0]
    assert ad_rows, "no inline Meta ad with spend"
    for key in ("impressions", "link_clicks", "ctr", "cpc"):
        j = d["heads"].index(key)
        vals = [num(r[j]) for _, r in ad_rows]
        assert any(v is not None for v in vals), (key, vals)
    # and they match the ads table for the same creative
    ji, jc = d["heads"].index("impressions"), d["heads"].index("link_clicks")
    imp_inline = sum(num(r[ji]) or 0 for _, r in ad_rows)
    assert imp_inline > 0 and sum(num(r[jc]) or 0 for _, r in ad_rows) > 0


@pytest.mark.allow_errors
@pytest.mark.parametrize("a,b", [("2026-03-01", "2026-03-05"), ("2026-11-10", "2026-11-12")])
def test_custom_range_outside_the_data_shows_empty_state_and_resets(page, a, b):
    """D2: a range wholly outside the data is not an error page: a friendly empty state and a way back to the last valid window"""
    goto_product(page, "?window=14d")
    page.fill("#range-from", a)
    page.fill("#range-to", b)
    page.click("#range-apply")
    box = page.locator("[data-testid=empty-range]")
    expect(box).to_be_visible()
    expect(page.locator(".errorbox")).to_have_count(0)
    txt = box.inner_text()
    assert "No data in this range" in txt and "May 6, 2026" in txt and "Nov 2, 2026" in txt, txt
    assert box.locator("button").count() == 1 and "Retry" not in txt
    page.locator("[data-testid=reset-range]").click()
    wait_rows(page)
    q = qparams(page)
    assert q.get("window") == "14d" and "from" not in q and "to" not in q, q  # back to the last valid window
    expect(page.locator("[data-testid=empty-range]")).to_have_count(0)
    # a link straight into the empty range (no earlier window in this session): the same state; reset falls back to the default
    page.goto(f"/p/{SLUG}?from={a}&to={b}")
    expect(page.locator("[data-testid=empty-range]")).to_be_visible()
    page.locator("[data-testid=reset-range]").click()
    wait_rows(page)
    assert "from" not in qparams(page)
    # the landing page too
    page.goto(f"/?platform=meta&from={a}&to={b}")
    expect(page.locator("[data-testid=empty-range]")).to_be_visible()
    shot(page, "46_empty_range")


def test_collapse_all_after_a_slow_expand_stays_collapsed(page):
    """D3: a slow /tree response must not undo a later Collapse all (or a later, shallower Expand to)"""
    goto_product(page)
    n_top = len(table_data(page)["rows"])
    held = []

    def handler(route):
        if "depth=2" in route.request.url:
            held.append(route)
        else:
            route.continue_()

    page.route("**/api/products/*/tree*", handler)
    page.locator("#expand-to [data-depth='2']").click()
    for _ in range(40):
        if held:
            break
        page.wait_for_timeout(100)
    if not held:
        pytest.skip("the tree already holds depth 2 (nothing to race)")
    page.click("#collapse-all")
    held[0].continue_()
    page.wait_for_timeout(1500)
    idle(page)
    assert set(table_data(page)["levels"]) == {0} and len(table_data(page)["rows"]) == n_top
    assert page.locator("#tree-busy").inner_text().strip() == ""  # no stuck 'Loading…'
    page.unroute("**/api/products/*/tree*")
    # later click wins in the other order too: +2 (slow) then +1 (fast) ends at +1
    held.clear()
    page.route("**/api/products/*/tree*", handler)
    page.locator("#expand-to [data-depth='2']").click()
    for _ in range(40):
        if held:
            break
        page.wait_for_timeout(100)
    page.locator("#expand-to [data-depth='1']").click()
    page.wait_for_selector(f"{TABLE} tr.lv1", timeout=8000)
    held[0].continue_()
    page.wait_for_timeout(1500)
    idle(page)
    assert set(table_data(page)["levels"]) == {0, 1}


def test_ad_detail_fixed_rows_say_all_stages_when_the_stage_filter_is_on(page):
    """D4: the Since launch / Last 7d / Last 30d rows ignore the Stage filter: say so (the selected-window row honours it)"""
    path = ad_page_url(page)
    page.goto(f"{path}?window=30d")
    page.wait_for_selector("#family-metrics")
    idle(page)
    assert page.locator("#family-metrics .all-stages").count() == 0
    page.goto(f"{path}?window=30d&stage=testing")
    page.wait_for_selector("#family-metrics")
    idle(page)
    for r in ("launch", "d7", "d30"):
        expect(page.locator(f"#family-metrics tr[data-row={r}] th")).to_contain_text("all stages")
    expect(page.locator("#family-metrics tr[data-row=window] th")).not_to_contain_text("all stages")


def sticky_overflows(page, testid="level-table"):
    return page.evaluate("""(tid) => { const out = [];
        for (const tr of document.querySelectorAll(`[data-testid=${tid}] tbody tr[data-key]`)) {
          const td = tr.querySelector('td.sticky'); if (!td) continue;
          const c = td.getBoundingClientRect();
          for (const el of td.querySelectorAll('a, .badge, .chip-type, .thumb, button')) {
            const r = el.getBoundingClientRect();
            if (r.width && r.right > c.right + 1) { out.push([tr.dataset.label, el.className || el.tagName, Math.round(r.right - c.right)]); break; }
          }
        } return out; }""", testid)


def test_first_column_content_never_overflows_the_sticky_cell(page):
    """D5: 'Open ›' and the confidence badge wrap inside the first column instead of sliding under the next one"""
    drill_to_families(page)
    page.locator(".seg[data-window='30d']").click()
    wait_pressed(page, "window", "30d")
    wait_rows(page)
    assert sticky_overflows(page) == []
    goto_product(page, "?window=30d")
    page.click("#expand-all")
    page.wait_for_selector(f"{TABLE} tr.lv3", timeout=8000)
    idle(page)
    assert sticky_overflows(page) == []
    page.set_viewport_size({"width": 1024, "height": 800})
    page.wait_for_timeout(200)
    assert sticky_overflows(page) == []


def test_columns_beyond_the_right_edge_are_announced_and_reachable(page):
    """D6: with Traffic and Advanced on, the table scrolls sideways and says which columns are off-screen"""
    page.set_viewport_size({"width": 1180, "height": 800})
    drill_to_families(page)
    d = table_data(page)
    stage_cols = [c for c in STAGE_COLS if c in d["heads"]]
    if not stage_cols:
        pytest.skip("both stages are not included here")
    wrap = page.locator(f"{TABLE} .tablewrap")
    over = page.evaluate("() => { const w = document.querySelector('[data-testid=level-table] .tablewrap'); return w.scrollWidth - w.clientWidth; }")
    assert over > 4, "expected the table to be wider than the box at 1180px"
    hint = page.locator("[data-testid=scroll-hint]").first
    expect(hint).to_be_visible()
    assert "More columns to the right" in hint.inner_text()
    # the last column is named while it is off-screen
    last = stage_cols[-1]
    last_label = page.locator(f"{HEAD} th[data-col={last}]").inner_text().split("\n")[0].strip()
    assert last_label.rstrip(" ▲▼↑↓") in hint.inner_text(), (last_label, hint.inner_text())
    for _ in range(6):
        if not hint.is_visible():
            break
        hint.locator("button").click()
        page.wait_for_timeout(450)
    expect(hint).to_be_hidden()
    ok = page.evaluate("""(c) => { const w = document.querySelector('[data-testid=level-table] .tablewrap').getBoundingClientRect();
        const th = document.querySelector(`[data-testid=level-table] thead th[data-col=${c}]`).getBoundingClientRect(); return th.right <= w.right + 1; }""", last)
    assert ok and wrap.evaluate("(w) => w.scrollLeft") > 0
    shot(page, "47_scroll_hint")


def test_campaigns_total_note_names_the_untracked_spend_and_stage_filter_drives_the_chart(page):
    """D7 + D9"""
    page.goto(f"/p/{SLUG}?view=campaigns&window=30d")
    page.wait_for_selector("[data-testid=campaign-table]")
    idle(page)
    if not page.locator("[data-testid=campaign-table] tbody tr[data-key]").count():
        pytest.skip("no campaign-level data loaded yet")
    note = page.locator("[data-testid=campaign-total-note]")
    summary = page.locator("#unmatched summary").inner_text()
    m = re.search(r"\$[\d,]+", summary)
    if m is None:
        assert note.count() == 0  # nothing untracked: no note
    else:
        expect(note).to_be_visible()
        assert "Total includes spend not on the tracker: " + m.group(0) in note.inner_text(), (note.inner_text(), summary)
    # the Tags view has no such note
    page.goto(f"/p/{SLUG}?window=30d")
    wait_rows(page)
    assert page.locator("[data-testid=campaign-total-note]").count() == 0
    # D9: the Stage chips decide which stages the stacked chart draws
    def labels(url):
        page.goto(url)
        page.wait_for_selector("#stage-chart")
        idle(page)
        return page.evaluate("() => Chart.getChart('stage-chart').data.datasets.map(d => d.label)")
    assert labels(f"/p/{SLUG}?view=campaigns&window=30d&stage=testing") == ["Testing"]
    assert labels(f"/p/{SLUG}?view=campaigns&window=30d&stage=scaling") == ["Scaling"]
    assert labels(f"/p/{SLUG}?view=campaigns&window=30d")[:2] == ["Testing", "Scaling"]


def test_trend_chart_draws_lines_above_bars_and_lists_the_legend_in_series_order(page):
    """D8: lines sit above the (translucent) bars; legend = bars, line, then the compare series"""
    page.goto(f"/p/{SLUG}?window=14d&compare=prev")
    wait_rows(page)
    wait_trend(page)
    info = page.evaluate("""() => { const c = Chart.getChart('trend-chart');
        return {order: c.data.datasets.map(d => ({label: d.label, type: d.type, order: d.order})),
                legend: c.legend.legendItems.map(i => i.text), idx: c.data.datasets.map(d => d.label)}; }""")
    bars = [d for d in info["order"] if d["type"] == "bar"]
    lines = [d for d in info["order"] if d["type"] == "line"]
    assert bars and lines and max(l["order"] for l in lines) < min(b["order"] for b in bars), info["order"]
    assert info["legend"] == info["idx"], info  # legend in dataset order: bars, line, then B bars, B line
    assert [d["type"] for d in info["order"]][:2] == ["bar", "line"] and info["idx"][-1].startswith("B ")


def test_ad_detail_copy_only_pick_has_no_dangling_separator_and_ads_table_has_trend(page):
    """D8: a blank line code does not leave ' · L08'; the creative's Meta-ads table fills its Trend column"""
    path = ad_page_url(page)

    def patch(route):
        r = route.fetch()
        body = r.json()
        body["tag"] = body.get("tag") or {"cluster": "x", "icp": "y", "pain_point": "z", "method": "copy"}
        body["tag"]["copy_only_line"] = {"line_code": "", "label": "L08"}
        route.fulfill(response=r, json=body)

    page.route(re.compile(r"/api/ads/\d+(\?|$)"), patch)
    page.goto(f"{path}?window=30d")
    page.wait_for_selector("#tag-block")
    idle(page)
    v = page.locator("#tag-block .kv", has_text="Copy-only pick").locator(".v")
    assert v.inner_text().strip() == "L08"
    page.unroute(re.compile(r"/api/ads/\d+(\?|$)"))
    page.goto(path)  # since launch: the creative always has ads here
    page.wait_for_selector("[data-testid=ads-table] tbody tr[data-key]")
    idle(page)
    page.wait_for_function("() => document.querySelectorAll('[data-testid=ads-table] tbody tr[data-key] svg.spark').length > 0", timeout=8000)


@pytest.mark.parametrize("width", [375, 390, 768, 1024, 1440, 2100])
def test_pages_never_scroll_horizontally_at_page_level(page, width):
    """phone to desktop: only tables may scroll sideways inside their own box, never the page (/, a product, Campaigns, Ads, compare, the ad page)"""
    page.set_viewport_size({"width": 1440, "height": 900})
    ad_path = ad_page_url(page)
    page.set_viewport_size({"width": width, "height": 800})
    paths = ["/", "/?platform=meta", f"/p/{SLUG}", f"/p/{SLUG}?view=campaigns&window=30d", f"/p/{SLUG}?view=ads&window=30d", f"/p/{SLUG}?window=7d&compare=prev&stage=testing",
             f"/p/{SLUG}?all=1&window=30d", f"{ad_path}?window=30d&compare=prev", "/?platform=meta&window=7d&compare=30d", "/?window=30d",
             "/campaigns?window=30d", "/?platform=all&window=30d", "/?platform=google&window=30d", "/campaigns?platform=google&window=30d",
             "/campaigns?platform=all&window=30d", "/campaigns?window=30d&compare=prev"]
    for path in paths:
        page.goto(path)
        page.wait_for_selector(".tablebox, #family-metrics")
        idle(page)
        page.wait_for_timeout(250)
        sw = page.evaluate("() => [document.documentElement.scrollWidth, window.innerWidth]")
        if sw[0] > sw[1] + 1:  # name the culprit: widest element that is not inside a scrolling box
            culprit = page.evaluate("""() => { const W = innerWidth, out = [];
                const clipped = (el) => { for (let a = el.parentElement; a && a !== document.body; a = a.parentElement) { const o = getComputedStyle(a).overflowX; if (o === 'auto' || o === 'scroll' || o === 'hidden') return true; } return false; };
                for (const el of document.querySelectorAll('body *')) { if (clipped(el)) continue; const r = el.getBoundingClientRect(); if (r.width && r.right > W + 0.5) out.push(el.tagName + '#' + el.id + '.' + String(el.className.baseVal === undefined ? el.className : '') + ' right=' + Math.round(r.right)); }
                return out.slice(0, 5); }""")
            pytest.fail(f"{path} at {width}px: page scrollWidth {sw[0]} > {sw[1]}: {culprit}")
    shot(page, f"48_no_page_scroll_{width}")


# ------------------------------------------------------------------ source switch (Meta | Upstack)
def test_source_switch_meta_upstack(page):
    goto_product(page, "?window=14d")
    seg = lambda key: page.locator(f"#source-ctl [data-source={key}]")          # noqa: E731
    expect(seg("meta")).to_have_attribute("aria-pressed", "true")
    expect(page.locator("#basis-ctl")).to_be_hidden()                            # the Count sub-switch only exists with Upstack
    expect(page.locator("#source-banner")).to_contain_text("Meta")               # the page states its source even in Meta mode
    assert "source" not in qparams(page)
    reqs = []
    page.on("request", lambda r: reqs.append(r.url) if "/api/products/" in r.url else None)
    seg("upstack").click()
    expect(page).to_have_url(re.compile(r"source=upstack"))
    expect(seg("upstack")).to_have_attribute("aria-pressed", "true")
    expect(page.locator("#basis-ctl")).to_be_visible()
    expect(page.locator("#source-banner")).to_contain_text("Upstack")
    expect(page.locator("#source-banner")).to_contain_text("new customers")
    expect(page.locator(f"{HEAD} th[data-col=purchases]")).to_contain_text("New customers")
    expect(page.locator(f"{HEAD} th[data-col=roas]")).to_contain_text("NC ROAS")
    assert any("source=upstack" in u for u in reqs), "every metrics request carries the source"
    page.locator("#basis-ctl [data-basis=all]").click()                          # all orders
    expect(page).to_have_url(re.compile(r"basis=all"))
    expect(page.locator(f"{HEAD} th[data-col=purchases]")).to_contain_text("Orders")
    expect(page.locator(f"{HEAD} th[data-col=roas]")).not_to_contain_text("NC")
    open_first_row(page)                                                          # the choice is carried across a drill
    assert qparams(page)["source"] == "upstack" and qparams(page)["basis"] == "all" and qparams(page)["window"] == "14d"
    expect(seg("upstack")).to_have_attribute("aria-pressed", "true")
    seg("meta").click()                                                           # back to Meta: both params leave the URL, labels return
    page.wait_for_function("() => !new URL(location.href).searchParams.has('source')")
    assert "basis" not in qparams(page)
    expect(page.locator(f"{HEAD} th[data-col=purchases]")).to_contain_text("Purchases")
    shot(page, "09_source_switch")


# ------------------------------------------------------------------ coverage: what has not been tested
@pytest.mark.mock_only
def test_not_tested_bar_toggle_and_panel(page):
    goto_product(page, "?window=14d")
    bar = page.locator("[data-testid=coverage-bar]")
    expect(bar).to_be_visible()
    expect(page.locator("#coverage-text")).to_contain_text("pain points not tested")
    expect(page.locator("#coverage-text")).to_contain_text("added after tagging")           # the zero-by-construction lines are named, not hidden
    assert "untested" not in qparams(page)
    page.locator("#untested-toggle").click()
    expect(page).to_have_url(re.compile(r"untested=1"))
    panel = page.locator("[data-testid=untested-panel]")
    expect(panel).to_be_visible()
    expect(page.locator("[data-testid=untested-no-ads]")).to_be_visible()
    expect(page.locator("[data-testid=untested-no-spend]")).to_be_visible()
    expect(page.locator("[data-testid=untested-added]")).to_be_visible()
    expect(panel).to_contain_text("neighbouring line")                                       # the one-line-per-ad caveat is shown
    expect(page.locator("#untested-toggle")).to_have_attribute("aria-pressed", "true")
    expect(page.locator("#untested-no-ads tbody tr")).to_have_count(1)
    page.locator("#untested-no-ads tbody tr").first.click()                                  # a row opens that line's own page and leaves the filter
    expect(page).to_have_url(re.compile(r"pain="))
    assert "untested" not in qparams(page)
    page.go_back()
    expect(page).to_have_url(re.compile(r"untested=1"))
    page.locator("#untested-toggle").click()                                                  # off again: back to the tree
    page.wait_for_function("() => !new URL(location.href).searchParams.has('untested')")
    expect(page.locator("[data-testid=level-table]")).to_be_visible()
    shot(page, "10_not_tested")


# ------------------------------------------------------------------ platforms: Platform picker, Google, All, platform-level Campaigns
def platform_btn(page, key):
    return page.locator(f"#platform-ctl [data-platform={key}]")


@pytest.mark.mock_only
def test_platform_picker_google_page_and_back(page):
    goto_product(page, "?window=14d&stage=scaling")
    expect(page.locator("#platform-ctl .ctl-name")).to_have_text("Platform")
    assert page.evaluate("() => document.querySelector('#controls .ctl-row .ctl').id") == "platform-ctl"          # first control of the bar
    assert [b.get_attribute("data-platform") for b in page.locator("#platform-ctl .seg").all()] == ["all", "meta", "google"]
    expect(platform_btn(page, "meta")).to_have_attribute("aria-pressed", "true")
    expect(page.locator("#stage-ctl")).to_be_visible()
    platform_btn(page, "google").click()
    expect(page).to_have_url(re.compile(r"platform=google"))
    q = qparams(page)
    assert q["window"] == "14d" and "stage" not in q and page.evaluate("() => location.pathname") == "/", q            # the range carries over, Meta-only controls are dropped
    expect(page.locator("h1")).to_have_text("Google")
    expect(platform_btn(page, "google")).to_have_attribute("aria-pressed", "true")
    expect(page.locator("#stage-ctl")).to_be_hidden()
    expect(page.locator("#tieout-btn")).to_be_hidden()
    expect(page.locator("#source-ctl [data-source=meta]")).to_have_text("Google")
    expect(page.locator("#source-ctl-inline [data-src-inline=meta]")).to_have_text("Google")
    types = page.locator("[data-testid=google-types] tbody tr[data-key]")
    expect(types.first).to_be_visible()
    assert types.count() >= 4
    expect(page.locator("[data-testid=google-types] th[data-col=revenue]")).to_contain_text("Conversion value")
    expect(page.locator("[data-testid=google-campaigns] tbody tr[data-key]").first).to_be_visible()
    expect(page.locator("[data-testid=google-trend] canvas")).to_be_visible()
    expect(page.locator("[data-testid=src-badge]").first).to_have_text("Numbers: Google")
    total_google = num(foot_value(page, "spend", "google-types"))
    page.locator("#source-ctl [data-source=upstack]").click()                                          # Numbers from Upstack: labels change, spend does not
    expect(page).to_have_url(re.compile(r"source=upstack"))
    expect(page.locator("[data-testid=google-types] th[data-col=purchases]")).to_contain_text("New customers")
    assert abs(num(foot_value(page, "spend", "google-types")) - total_google) <= 1
    expect(page.locator("#source-banner")).to_contain_text("Upstack")
    expect(page.locator("[data-testid=src-badge]").first).to_have_text("Numbers: Upstack · new customers")
    page.locator("[data-testid=google-types] tbody tr[data-key]").first.click()                        # a type row filters the campaigns table
    expect(page).to_have_url(re.compile(r"type="))
    expect(page.locator("#campaigns-title")).to_contain_text("Campaigns ·")
    assert qparams(page)["source"] == "upstack" and qparams(page)["platform"] == "google"
    platform_btn(page, "meta").click()                                                                  # back to Meta: range and numbers choice carry over, platform and type leave
    page.wait_for_function("() => new URL(location.href).searchParams.get('platform') === 'meta'")     # Meta's Products landing is explicit now: a bare / is All
    expect(page.locator("h1")).to_have_text("Meta")
    expect(platform_btn(page, "meta")).to_have_attribute("aria-pressed", "true")
    assert qparams(page)["window"] == "14d" and qparams(page)["source"] == "upstack" and "type" not in qparams(page)
    shot(page, "11_google")


@pytest.mark.mock_only
def test_first_google_url_still_works(page, base_url):
    page.goto(f"{base_url}/google?window=14d")
    expect(page).to_have_url(re.compile(r"/\?(?=.*platform=google)(?=.*window=14d)"))
    expect(page.locator("h1")).to_have_text("Google")


@pytest.mark.mock_only
def test_google_campaigns_view_has_the_campaigns_first_and_types_as_a_filter(page):
    goto_product(page, "?window=14d")
    platform_btn(page, "google").click()
    page.locator("#view-switch [data-platform-view=campaigns]").click()
    expect(page).to_have_url(re.compile(r"/campaigns\?"))
    expect(page.locator("h1")).to_have_text("Campaigns")
    assert qparams(page)["platform"] == "google"
    assert page.locator("[data-testid=google-types]").count() == 0                                      # the types are the filter row here, not a table
    rows = page.locator("[data-testid=google-campaigns] tbody tr[data-key]")
    expect(rows.first).to_be_visible()
    n_all = rows.count()
    chips = page.locator("[data-testid=type-filter] [data-type]")
    assert chips.count() >= 3 and page.locator("[data-testid=type-filter] [data-type=all]").get_attribute("aria-pressed") == "true"
    chips.nth(1).click()
    expect(page).to_have_url(re.compile(r"type="))
    idle(page)
    expect(page.locator("[data-testid=google-campaigns] tbody tr[data-key]").first).to_be_visible()
    assert 0 < page.locator("[data-testid=google-campaigns] tbody tr[data-key]").count() < n_all
    platform_btn(page, "meta").click()                                                                  # the switch keeps you on Campaigns
    expect(page).to_have_url(re.compile(r"/campaigns"))
    assert "platform" not in qparams(page) and "type" not in qparams(page)
    expect(page.locator("[data-testid=platform-campaigns]")).to_be_visible()


@pytest.mark.mock_only
def test_all_platforms_overview(page):
    goto_product(page, "?window=14d&stage=scaling&compare=prev")
    platform_btn(page, "all").click()
    expect(page.locator("h1")).to_have_text("All platforms")
    assert "stage" not in qparams(page) and "platform" not in qparams(page)                             # All is the default of the bare root
    expect(page.locator("#stage-ctl")).to_be_hidden()
    expect(page.locator("#source-ctl")).to_be_hidden()                                                  # each platform's own numbers and Upstack are side by side: no switch
    expect(page.locator("#source-ctl-inline")).to_be_hidden()
    expect(page.locator("#tieout-btn")).to_be_hidden()
    rows = page.locator("[data-testid=platform-overview] tbody tr")
    expect(rows).to_have_count(6)                                                                       # per platform: its own reporting, Upstack new customers, Upstack all orders
    text = [r.inner_text() for r in rows.all()]
    assert "Meta's own reporting" in text[0] and "Upstack · new customers" in text[1] and "Upstack · all orders" in text[2]
    assert "Google's own reporting" in text[3] and "Upstack · all orders" in text[5]
    spend = [num(x) for x in col(page, "spend", "platform-overview")]
    assert spend[0] == spend[1] == spend[2] and spend[3] == spend[4] == spend[5] and spend[0] > 0 and spend[3] > 0    # spend is the platform's in every row
    expect(page.locator("[data-testid=platform-overview] th[data-col=purchases]")).to_contain_text("Purchases / orders")
    expect(page.locator("#range-note")).to_contain_text("do not apply to All platforms")
    page.locator("[data-testid=platform-overview] a", has_text="Campaigns →").first.click()              # Meta's campaigns
    expect(page).to_have_url(re.compile(r"/campaigns"))
    expect(page.locator("[data-testid=platform-campaigns]")).to_be_visible()
    shot(page, "12_all_platforms")


@pytest.mark.mock_only
def test_platform_campaigns_view_product_column_and_filter(page):
    goto_home(page, "?window=30d")
    page.locator("#view-switch [data-platform-view=campaigns]").click()
    expect(page).to_have_url(re.compile(r"/campaigns\?"))
    expect(page.locator("h1")).to_have_text("Campaigns")
    t = "[data-testid=platform-campaigns]"
    rows = page.locator(f"{t} tbody tr[data-key]")
    expect(rows.first).to_be_visible()
    n_all = rows.count()
    assert n_all >= 6
    spend_sum = sum(num(x) for x in col(page, "spend", "platform-campaigns"))
    assert abs(spend_sum - num(foot_value(page, "spend", "platform-campaigns"))) <= max(2.0, 0.001 * spend_sum)      # the Total row is the sum of the rows
    products_col = col(page, "product", "platform-campaigns")
    assert any(c.startswith("Mixed") for c in products_col), products_col                                      # a campaign that runs several products says so
    names = {c.split("\n")[0].strip() for c in products_col}
    assert {"Vita Bull", "Beef Liver", "Boneflex"} <= names
    chips = page.locator("[data-testid=product-filter] [data-product]")
    assert [c.get_attribute("data-product") for c in chips.all()][:2] == ["all", "vita-bull"]
    chips.nth(1).click()                                                                                # Vita Bull only
    expect(page).to_have_url(re.compile(r"product=vita-bull"))
    expect(page.locator("[data-testid=product-filter] [data-product=vita-bull]")).to_have_attribute("aria-pressed", "true")
    idle(page)
    expect(page.locator(f"{t} tbody tr[data-key]").first).to_be_visible()
    n_vb = page.locator(f"{t} tbody tr[data-key]").count()
    assert 0 < n_vb < n_all
    expect(page.locator("#level-title")).to_contain_text("Vita Bull")
    assert qparams(page)["window"] == "30d"
    page.locator(f"{t} tbody tr[data-key]").first.locator("a.rowlink").click()                          # a campaign opens its ads in that product, as in the product view
    expect(page).to_have_url(re.compile(r"/p/vita-bull\?.*view=ads.*campaign_id="))
    expect(page.locator("h1")).to_contain_text("Vita Bull")
    page.go_back()
    page.locator("[data-testid=product-filter] [data-product=all]").click()
    page.wait_for_function("() => !new URL(location.href).searchParams.has('product')")
    idle(page)
    expect(page.locator(f"{t} tbody tr[data-key]").first).to_be_visible()
    assert page.locator(f"{t} tbody tr[data-key]").count() == n_all
    page.locator("#source-ctl [data-source=upstack]").click()                                           # the Numbers-from flip applies like everywhere
    expect(page.locator(f"{t} th[data-col=purchases]")).to_contain_text("New customers")
    shot(page, "13_platform_campaigns")


@pytest.mark.mock_only
def test_numbers_from_switch_sits_beside_the_explanation_on_a_wide_window(page):
    page.set_viewport_size({"width": 2100, "height": 1000})                                             # Josh's 4K window
    goto_product(page, "?window=14d")
    box = lambda sel: page.locator(sel).bounding_box()                                                  # noqa: E731
    bar, tie, inline = box("#controls .controls-in"), box("#tieout-btn"), box("#source-ctl-inline")
    assert bar["width"] <= 1601, bar                                                                    # the bar is capped, not 2,000 px wide
    assert abs(inline["x"] + inline["width"] - tie["x"]) < 260, (inline, tie)                           # the switch is right next to the tie-out button
    assert page.evaluate("() => document.querySelector('#view').getBoundingClientRect().width") <= 1601
    assert page.evaluate("() => document.documentElement.scrollWidth <= innerWidth + 1")
    expect(page.locator("[data-testid=src-badge]").first).to_have_text("Numbers: Meta")
    page.locator("[data-testid=switch-source]").click()                                                 # the words "Switch to Upstack" in the note are a button
    expect(page).to_have_url(re.compile(r"source=upstack"))
    expect(page.locator("[data-testid=src-badge]").first).to_have_text("Numbers: Upstack · new customers")
    expect(page.locator("#source-ctl-inline [data-src-inline=upstack]")).to_have_attribute("aria-pressed", "true")
    expect(page.locator("#source-ctl [data-source=upstack]")).to_have_attribute("aria-pressed", "true")      # one state, two places
    page.locator("#basis-ctl-inline [data-basis-inline=all]").click()
    expect(page).to_have_url(re.compile(r"basis=all"))
    expect(page.locator("[data-testid=src-badge]").first).to_have_text("Numbers: Upstack · all orders")
    open_first_row(page)                                                                                # product page: same switch in view, same state
    expect(page.locator("#source-ctl-inline")).to_be_visible()
    expect(page.locator("[data-testid=src-badge]").first).to_have_text("Numbers: Upstack · all orders")
    page.locator("[data-testid=switch-source]").click()                                                 # "Switch to Meta's own numbers"
    page.wait_for_function("() => !new URL(location.href).searchParams.has('source')")
    expect(page.locator("[data-testid=src-badge]").first).to_have_text("Numbers: Meta")
    page.set_viewport_size({"width": 1280, "height": 900})


# ------------------------------------------------------------------ platform trends and All campaigns
def chart_sum(info, label_startswith=None):
    """sum of the bar series of the trend chart (nulls are zeros)"""
    return sum(sum(v or 0 for v in d["data"]) for d in bars_ds(info) if label_startswith is None or d["label"].startswith(label_startswith))


@pytest.mark.mock_only
def test_platform_campaigns_chart_split_grain_and_campaign_narrowing(page):
    goto_home(page, "?window=30d")
    page.locator("#view-switch [data-platform-view=campaigns]").click()
    wait_trend(page)
    info = trend_info(page)
    assert [d["label"] for d in bars_ds(info)] == ["Spend"] and line_ds(info)[0]["label"].startswith("ROAS")
    table_total = num(foot_value(page, "spend", "platform-campaigns"))
    assert abs(chart_sum(info) - table_total) <= max(2.0, 0.002 * table_total)                      # the chart adds up to the table's total
    assert [b.get_attribute("data-split") for b in page.locator("#trend-split .seg").all()] == ["none", "stage", "product"]
    assert [b.get_attribute("data-grain") for b in page.locator("#trend-grain .seg").all()] == ["day", "week", "month"]
    page.locator("#trend-split [data-split=product]").click()
    page.wait_for_function("() => Chart.getChart('trend-chart').data.datasets.filter(d => d.type === 'bar').length > 1")
    info = trend_info(page)
    names = {d["label"] for d in bars_ds(info)}
    assert {"Vita Bull", "Beef Liver", "Boneflex"} <= names and all(d["stack"] for d in bars_ds(info))     # stacked bars, one per product
    assert abs(chart_sum(info) - table_total) <= max(2.0, 0.002 * table_total)
    assert qparams(page)["ts"] == "product"
    page.locator("#trend-split [data-split=stage]").click()
    page.wait_for_function("() => Chart.getChart('trend-chart').data.datasets.some(d => d.label === 'Scaling')")
    page.locator("#trend-grain [data-grain=week]").click()
    page.wait_for_function("() => new URL(location.href).searchParams.get('tg') === 'week'")
    wait_trend(page)
    info = trend_info(page)
    assert len(info["labels"]) < 8 and abs(chart_sum(info) - table_total) <= max(2.0, 0.002 * table_total)
    page.locator("#trend-grain [data-grain=day]").click()
    page.locator("#trend-split [data-split=none]").click()
    # a campaign row's Chart button narrows the chart (not the table)
    n_rows = page.locator("[data-testid=platform-campaigns] tbody tr[data-key]").count()
    first = page.locator("[data-testid=platform-campaigns] tbody tr[data-key]").nth(2)
    row_spend = num(first.locator("td").nth(col_index(page, "spend", "platform-campaigns")).inner_text())
    first.locator("[data-testid=chart-row]").click()
    expect(page.locator("[data-testid=chart-scope]")).to_be_visible()
    assert "chart" in qparams(page)
    idle(page)
    wait_trend(page)
    info = trend_info(page)
    assert abs(chart_sum(info) - row_spend) <= max(2.0, 0.002 * row_spend)
    assert page.locator("[data-testid=platform-campaigns] tbody tr[data-key]").count() == n_rows
    page.locator("[data-testid=chart-scope] a").click()
    page.wait_for_function("() => !new URL(location.href).searchParams.has('chart')")
    shot(page, "14_platform_campaigns_chart")


def col_index(page, key, testid):
    return table_data(page, testid)["heads"].index(key)


@pytest.mark.mock_only
def test_all_platforms_chart_platform_series_numbers_and_links(page):
    page.goto("/?platform=all&window=30d")
    wait_trend(page)
    info = trend_info(page)
    assert {d["label"] for d in bars_ds(info)} == {"Meta", "Google"} and all(d["stack"] for d in bars_ds(info))
    assert {d["label"] for d in line_ds(info)} == {"ROAS · Meta", "ROAS · Google"}                     # one ROAS line per platform
    opts = page.locator("#trend-bars option").all_inner_texts()
    assert opts == ["Spend", "Impressions", "Link clicks", "None"], opts                               # only what may be added across platforms
    assert [b.get_attribute("data-numbers") for b in page.locator("#trend-numbers .seg").all()] == ["own", "nc", "all"]
    own_spend = chart_sum(info, "Meta")
    page.locator("#trend-numbers [data-numbers=nc]").click()
    page.wait_for_function("() => new URL(location.href).searchParams.get('nb') === 'nc'")
    wait_trend(page)
    assert "Upstack attribution is drawn only where Upstack has it" in page.locator("#trend-note").inner_text()
    assert abs(chart_sum(trend_info(page), "Meta") - own_spend) <= 1                                   # spend is the platform's on every basis
    expect(page.locator("#view-switch [data-platform-view=campaigns]")).to_have_text("Campaigns")
    page.locator("#view-switch [data-platform-view=campaigns]").click()                                # All platforms -> All campaigns
    expect(page).to_have_url(re.compile(r"/campaigns\?"))
    assert qparams(page)["platform"] == "all"
    expect(page.locator("h1")).to_have_text("All campaigns")
    page.locator("#view-switch [data-platform-view=home]").click()                                     # and back
    expect(page.locator("h1")).to_have_text("All platforms")
    shot(page, "15_all_platforms_chart")


@pytest.mark.mock_only
def test_all_campaigns_one_table_basis_labels_totals_and_filters(page):
    page.goto("/campaigns?platform=all&window=30d")
    t = "[data-testid=all-campaigns]"
    page.locator(f"{t} tbody tr[data-key]").first.wait_for()
    data = table_data(page, "all-campaigns")
    platform_i, basis_i, group_i = (data["heads"].index(k) for k in ("platform", "basis", "group"))
    plats = {r[platform_i] for r in data["rows"]}
    assert plats == {"Meta", "Google"}
    assert {r[basis_i] for r in data["rows"] if r[platform_i] == "Meta"} == {"Meta's own reporting"}      # each row says which basis it is counted on
    assert {r[basis_i] for r in data["rows"] if r[platform_i] == "Google"} == {"Google's own reporting"}
    assert any(r[group_i].startswith("Mixed") for r in data["rows"]) and {"Shopping", "Search"} <= {r[group_i] for r in data["rows"] if r[platform_i] == "Google"}
    spend = [num(x) for x in col(page, "spend", "all-campaigns")]
    assert spend == sorted(spend, reverse=True)                                                        # sorted by spend
    foot = data["footRows"]
    assert foot[0]["cells"][0].startswith("Total (spend, impressions, clicks)")
    head = data["heads"]
    tot = foot[0]["cells"]
    assert num(tot[head.index("spend")]) > 0 and num(tot[head.index("impressions")]) > 0 and num(tot[head.index("link_clicks")]) > 0
    for k in ("purchases", "revenue", "roas", "cpa"):
        assert num(tot[head.index(k)]) is None, k                                                      # purchases / revenue / ROAS are not added across platforms
    subs = {r["cells"][0].split(" ")[0]: r["cells"] for r in foot[1:]}
    assert set(subs) == {"Meta", "Google"} and all(num(c[head.index("purchases")]) for c in subs.values())
    assert abs(num(tot[head.index("spend")]) - (num(subs["Meta"][head.index("spend")]) + num(subs["Google"][head.index("spend")]))) <= 2
    expect(page.locator("[data-testid=all-campaigns-note]")).to_contain_text("Not added up")
    # the chart adds up to the table
    wait_trend(page)
    assert abs(chart_sum(trend_info(page)) - num(tot[head.index("spend")])) <= max(3.0, 0.002 * num(tot[head.index("spend")]))
    # Platform filter: Meta only = Meta's own Campaigns total
    page.locator("[data-testid=only-meta]").click()
    expect(page).to_have_url(re.compile(r"only=meta"))
    idle(page)
    page.locator(f"{t} tbody tr[data-key]").first.wait_for()
    assert {r[platform_i] for r in table_data(page, "all-campaigns")["rows"]} == {"Meta"}
    all_meta_total = {k: num(foot_value(page, k, "all-campaigns")) for k in ("spend", "purchases", "revenue")}
    assert page.locator(f"{t} tfoot tr").count() == 1 and all(v for v in all_meta_total.values())      # one platform in view: the Total row carries purchases and revenue
    page.goto("/campaigns?window=30d")
    page.locator("[data-testid=platform-campaigns] tbody tr[data-key]").first.wait_for()
    for k in ("spend", "purchases", "revenue"):
        assert abs(num(foot_value(page, k, "platform-campaigns")) - all_meta_total[k]) <= max(1.0, 0.001 * all_meta_total[k]), k
    # Product and Type filters
    page.goto("/campaigns?platform=all&window=30d")
    page.locator("[data-testid=product-vita-bull]").click()
    expect(page).to_have_url(re.compile(r"product=vita-bull"))
    idle(page)
    page.locator(f"{t} tbody tr[data-key]").first.wait_for()
    assert {r[platform_i] for r in table_data(page, "all-campaigns")["rows"]} == {"Meta"}
    page.locator("[data-testid=type-Search]").click()
    expect(page).to_have_url(re.compile(r"type=Search"))
    assert "product" not in qparams(page)
    idle(page)
    page.locator(f"{t} tbody tr[data-key]").first.wait_for()
    d2 = table_data(page, "all-campaigns")
    assert {r[platform_i] for r in d2["rows"]} == {"Google"} and {r[group_i] for r in d2["rows"]} == {"Search"}
    # Numbers from Upstack: the Numbers column follows per row
    page.goto("/campaigns?platform=all&window=30d")
    page.locator(f"{t} tbody tr[data-key]").first.wait_for()
    page.locator("#source-ctl [data-source=upstack]").click()
    expect(page).to_have_url(re.compile(r"source=upstack"))
    idle(page)
    d3 = table_data(page, "all-campaigns")
    assert {r[basis_i] for r in d3["rows"]} == {"Upstack · new customers"}
    expect(page.locator("[data-testid=src-badge]").first).to_have_text("Numbers: Upstack · new customers")
    page.locator("#basis-ctl [data-basis=all]").click()
    idle(page)
    assert {r[basis_i] for r in table_data(page, "all-campaigns")["rows"]} == {"Upstack · all orders"}
    # the platform picker keeps you on Campaigns
    page.locator("#platform-ctl [data-platform=google]").click()
    expect(page).to_have_url(re.compile(r"/campaigns\?"))
    assert qparams(page)["platform"] == "google"
    page.locator("#platform-ctl [data-platform=all]").click()
    expect(page).to_have_url(re.compile(r"/campaigns\?"))
    assert qparams(page)["platform"] == "all"
    shot(page, "16_all_campaigns")


# ------------------------------------------------------------------ default landing = All platforms
@pytest.mark.mock_only
def test_bare_root_is_the_all_platforms_roll_up_and_every_link_back_to_meta_is_explicit(page, base_url):
    page.goto(f"{base_url}/")                                                                          # a bare root, as the top-bar link points to
    expect(page.locator("h1")).to_have_text("All platforms")
    assert page.evaluate("() => location.search") == ""                                                # no parameter was added
    expect(platform_btn(page, "all")).to_have_attribute("aria-pressed", "true")
    expect(page.locator("#view-switch .ctl-name")).to_have_text("View by")
    assert [b.inner_text() for b in page.locator("#view-switch .seg").all()] == ["Platforms", "Campaigns"]
    expect(page.locator("#view-switch [data-platform-view=home]")).to_have_attribute("aria-pressed", "true")
    expect(page.locator("[data-testid=platform-overview] tbody tr")).to_have_count(6)
    wait_trend(page)                                                                                   # with the platform chart
    assert {d["label"] for d in bars_ds(trend_info(page))} == {"Meta", "Google"}
    expect(page.locator("#topbar a.navlink", has_text="Marketing metrics")).to_have_attribute("href", "/")                           # the top-bar Marketing metrics link is the bare root
    # View by Campaigns: every campaign across both platforms, nothing to choose first
    page.locator("#view-switch [data-platform-view=campaigns]").click()
    expect(page).to_have_url(re.compile(r"/campaigns\?platform=all"))
    page.locator("[data-testid=all-campaigns] tbody tr[data-key]").first.wait_for()
    data = table_data(page, "all-campaigns")
    assert {r[data["heads"].index("platform")] for r in data["rows"]} == {"Meta", "Google"}
    page.locator("#view-switch [data-platform-view=home]").click()
    expect(page.locator("h1")).to_have_text("All platforms")
    assert page.evaluate("() => location.search").find("platform") == -1                                # All is the default of '/': no platform parameter
    # the picker: Meta is the old Products landing, one click away and explicit in the URL
    platform_btn(page, "meta").click()
    expect(page).to_have_url(re.compile(r"/\?platform=meta"))
    expect(page.locator("h1")).to_have_text("Meta")
    page.locator("[data-testid=products-table] tbody tr").first.wait_for()
    platform_btn(page, "google").click()
    expect(page).to_have_url(re.compile(r"platform=google"))
    platform_btn(page, "all").click()                                                                  # and back to the default URL
    expect(page.locator("h1")).to_have_text("All platforms")
    assert page.evaluate("() => location.search").find("platform") == -1
    page.locator("#topbar a.navlink", has_text="Marketing metrics").click()                                                          # the top-bar link from any page lands on All
    page.goto(f"{base_url}/p/{SLUG}")
    page.locator("#topbar a.navlink", has_text="Marketing metrics").click()
    expect(page.locator("h1")).to_have_text("All platforms")


@pytest.mark.mock_only
def test_existing_deep_links_keep_their_meaning(page, base_url):
    page.goto(f"{base_url}/campaigns?window=30d")                                                       # no platform on /campaigns = Meta's Campaigns, as before
    expect(page.locator("[data-testid=platform-campaigns]")).to_be_visible()
    expect(platform_btn(page, "meta")).to_have_attribute("aria-pressed", "true")
    page.goto(f"{base_url}/?platform=meta&window=7d&source=upstack&basis=all")                          # Meta landing with every parameter
    expect(page.locator("h1")).to_have_text("Meta")
    assert qparams(page)["source"] == "upstack" and qparams(page)["basis"] == "all" and qparams(page)["window"] == "7d"
    page.goto(f"{base_url}/?window=7d&source=upstack")                                                  # a root with parameters but no platform is the roll-up with them applied
    expect(page.locator("h1")).to_have_text("All platforms")
    page.goto(f"{base_url}/p/{SLUG}?window=14d")                                                        # product pages are untouched
    expect(page.locator("h1")).not_to_have_text("All platforms")
    crumbs = page.locator(".crumbs li").all_inner_texts()
    assert crumbs[0] == "Meta" and len(crumbs) == 2, crumbs
    page.locator(".crumbs a").first.click()                                                             # back up the hierarchy = Meta's Products table, not the roll-up
    expect(page.locator("h1")).to_have_text("Meta")
    page.goto(f"{base_url}/p/{SLUG}")
    page.locator("#back").click()
    expect(page.locator("h1")).to_have_text("Meta")


# ------------------------------------------------------------------ hooks (ad_hooks): Hook column of All creatives, Hook card on the ad page
def hook_cells(page):
    """one dict per row of the All creatives table: {key, text, title, clipped, overflow} for its Hook cell"""
    return page.evaluate("""() => {
        const t = document.querySelector('[data-testid=level-table] table');
        const i = [...t.querySelectorAll(':scope > thead th')].findIndex(th => th.dataset.col === 'hook');
        return [...t.querySelectorAll(':scope > tbody > tr[data-key]')].map(tr => {
          const td = tr.children[i]; const sp = td.querySelector('.hooktext');
          return {key: tr.dataset.key, text: td.innerText.trim(), title: td.getAttribute('title'),
                  clipped: sp ? sp.scrollWidth > sp.clientWidth + 1 : false, overflow: sp ? getComputedStyle(sp).textOverflow : null};
        }); }""")


def hook_families(page):
    """{table row key: the family's hook text or None} from the API, in the shape the table keys its rows"""
    rows = page.context.request.get(f"/api/products/{SLUG}/families").json()["rows"]
    return rows, {r["family_key"] + "|" + ",".join(str(i) for i in r["ad_ids"]): r["hook"] for r in rows}


@pytest.mark.mock_only
def test_all_creatives_hook_column_text_tooltip_sort_search_and_no_total(page):
    rows, want = hook_families(page)
    assert any(want.values()) and not all(want.values())                         # the mock gives some creatives a hook and others none
    page.goto(f"/p/{SLUG}?all=1")
    wait_rows(page)
    d = table_data(page)
    assert d["heads"][1:3] == ["display", "hook"]                                # right behind the Creative column (the first th is the caret)
    assert page.locator(f"{HEAD} th[data-col=hook]").inner_text().strip() == "Hook"
    cells = hook_cells(page)
    assert {c["key"]: c["text"] for c in cells} == {k: v or "" for k, v in want.items()}
    assert all(c["title"] == (want[c["key"]] or None) for c in cells), cells     # the full text is the tooltip; an empty cell has none
    assert any(c["clipped"] and c["overflow"] == "ellipsis" for c in cells)      # the long hook is cut with an ellipsis ...
    assert any(c["text"] and not c["clipped"] for c in cells)                    # ... the short ones are not
    assert foot_value(page, "hook") == ""                                        # no total
    # sortable by text; creatives without a hook sit last in both directions
    v1, v2 = sort_both_ways(page, "hook", numeric=False)
    full = lambda v: [x for x in v if x]                                         # noqa: E731
    assert full(v1) == sorted(full(v1), key=str.lower) and full(v2) == sorted(full(v2), key=str.lower, reverse=True)
    assert v1 == full(v1) + [x for x in v1 if not x] and v2 == full(v2) + [x for x in v2 if not x]
    # the search box looks in the hook text too
    needle = "zinc levels"
    expected = {k for k, v in want.items() if needle in (v or "").lower()}
    assert expected
    page.fill("#flt-q", needle)
    expect(page.locator(BODY_ROWS)).to_have_count(len(expected))
    assert {c["key"] for c in hook_cells(page)} == expected


@pytest.mark.mock_only
def test_hook_column_keeps_the_page_from_scrolling_sideways_on_a_phone(page):
    page.set_viewport_size({"width": 390, "height": 800})
    page.goto(f"/p/{SLUG}?all=1")
    wait_rows(page)
    page.wait_for_timeout(250)
    assert page.evaluate("() => document.documentElement.scrollWidth") <= 391
    w = page.evaluate("() => Math.max(...[...document.querySelectorAll('.hooktext')].map(e => e.getBoundingClientRect().width))")
    assert 0 < w <= 221, w                                                       # capped at 220px on narrow screens; the table scrolls inside its own box


@pytest.mark.mock_only
def test_ad_page_hook_card(page):
    rows, _ = hook_families(page)
    by_src = {}
    for r in rows:
        if r["hook"] and not r["display"].startswith("XSS"):
            by_src.setdefault(r["hook_source"], r)
    assert set(by_src) == {"spoken", "on_screen", "both", "copy"}, by_src.keys()
    api = page.context.request
    both = by_src["both"]
    det = api.get(f"/api/ads/{both['primary_id']}").json()["hook"]
    page.goto(f"/ad/{both['primary_id']}")
    page.wait_for_selector("#family-metrics")
    idle(page)
    card = page.locator("#hook-block")
    expect(card).to_be_visible()
    expect(page.locator("#hook-line")).to_have_text(det["hook"])
    kv = lambda label: card.locator(".kv", has_text=label).locator(".v")         # noqa: E731
    expect(kv("Spoken")).to_have_text(det["spoken"])
    expect(kv("On screen")).to_have_text(det["on_screen"])
    expect(card.locator(".badge.method")).to_have_text("both")
    expect(card.locator(".badge.conf-high")).to_have_text("high")
    expect(card).to_contain_text("Hooks hooks-v1-2026-11-05")
    # prominent, and between the title and the creative preview
    pos = page.evaluate("""() => { const y = (s) => document.querySelector(s).getBoundingClientRect(); return {title: y('#ad-title').bottom, hook: y('#hook-block').top, line: parseFloat(getComputedStyle(document.querySelector('#hook-line')).fontSize), seeAd: y('#see-ad').top}; }""")
    assert pos["title"] <= pos["hook"] < pos["seeAd"] and pos["line"] >= 15, pos
    # a hook with no spoken line / on-screen text shows only what it has, with its source and confidence
    copy = by_src["copy"]
    page.goto(f"/ad/{copy['primary_id']}")
    page.wait_for_selector("#hook-block")
    expect(page.locator("#hook-line")).to_have_text(copy["hook"])
    assert page.locator("#hook-block .kv", has_text="Spoken").count() == 0 and page.locator("#hook-block .kv", has_text="On screen").count() == 0
    expect(page.locator("#hook-block .badge.method")).to_have_text("copy")
    expect(page.locator("#hook-block .badge.conf-low")).to_have_text("low")
    expect(page.locator("#hook-block .kv", has_text="Note").locator(".v")).to_contain_text("hook taken from the primary text")
    page.goto(f"/ad/{by_src['on_screen']['primary_id']}")
    page.wait_for_selector("#hook-block")
    expect(page.locator("#hook-block .badge.method")).to_have_text("on screen")  # the underscore is a space
    # no hook, no card
    nohook = next(r for r in rows if not r["hook"])
    page.goto(f"/ad/{nohook['primary_id']}")
    page.wait_for_selector("#family-metrics")
    idle(page)
    assert page.locator("#hook-block").count() == 0 and page.locator("#tag-block").count() == 1


@pytest.mark.mock_only
def test_hooks_are_escaped(page):
    rows, _ = hook_families(page)
    x = next(r for r in rows if r["display"].startswith("XSS"))
    assert "<img src=x onerror=window.__xss=1>" in x["hook"]
    page.goto(f"/p/{SLUG}?all=1")
    wait_rows(page)
    assert "<img src=x onerror=window.__xss=1>" in page.locator(f"{TABLE} .hooktext").first.locator("xpath=ancestor::table").inner_text()
    assert page.locator(f"{TABLE} .hooktext img, {TABLE} .hooktext b, {TABLE} .hooktext script").count() == 0
    page.goto(f"/ad/{x['primary_id']}")
    expect(page.locator("#hook-line")).to_have_text(x["hook"])
    assert page.locator("#hook-block img, #hook-block b, #hook-block script").count() == 0
    assert page.evaluate("() => window.__xss") is None
