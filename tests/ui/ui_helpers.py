"""Shared helpers and constants for the Marketing metrics UI tests (split out of test_ui.py; no assertions live here)."""
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
    for expected in ("Ideal customer profiles in", "Pain points in", "Creatives in"):
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


def open_chart_options(page):
    """Bars, Line, Swap, Split bars by and Numbers sit in the closed 'Chart options' disclosure on the performance pages; open it (no-op when already open)"""
    det = page.locator("[data-testid=chart-options]").first
    if det.get_attribute("open") is None:
        det.locator("summary").click()
    expect(det).to_have_attribute("open", "")


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


# ------------------------------------------------------------------ stale banner, stage note, landing account rows
def fake_status(page, patch):
    """serve /api/status with fields overridden (works against the mock and the real API)"""
    def handler(route):
        resp = route.fetch()
        body = resp.json()
        body.update(patch)
        route.fulfill(response=resp, json=body)
    page.route("**/api/status", handler)


# ------------------------------------------------------------------ Active only filter (v1.9)
def api_requests(page):
    reqs = []
    page.on("request", lambda r: reqs.append(r.url) if "/api/" in r.url and "/api/login" not in r.url and "/api/status" not in r.url and "/media/" not in r.url else None)
    return reqs


def has_active_support(page):
    d = page.context.request.get(f"/api/products/{SLUG}/tree?root=cluster&depth=0&active=1").json()
    return bool((d.get("window_info") or {}).get("active_ads") is not None or "ctive" in ((d.get("window_info") or {}).get("note") or ""))


# ------------------------------------------------------------------ creative / ad vocabulary and the first column
LEGEND = "Creative = one ad concept and all its copies. Ad = one copy running in a campaign and ad set."


# ------------------------------------------------------------------ thumbnails lightbox, live preview, links
LB = ".lightbox[role=dialog]"


def first_video_ad_id(page):
    api = page.context.request
    for r in api.get(f"/api/products/{SLUG}/families").json()["rows"]:
        if r.get("ad_type") == "VID" and r.get("primary_id") is not None:
            return r["primary_id"]
    pytest.skip("no video ad")


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


def _group_spends(d):
    si = d["heads"].index("spend")
    groups = {}
    for k, lv, r in zip(d["keys"], d["levels"], d["rows"]):
        parent = k.rsplit("|", 1)[0] if lv > 0 else "__root__"
        groups.setdefault((lv, parent), []).append(num(r[si]))
    return groups


# ------------------------------------------------------------------ ad detail
def open_first_ad(page, params=""):
    drill_to_families(page)
    href = first_row(page).get_attribute("data-href")
    page.goto(href + (("&" if "?" in href else "?") + params.lstrip("?&") if params else ""))
    expect(page.locator("#ad-title")).to_be_visible()
    wait_trend(page)


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


# ------------------------------------------------------------------ platforms: Platform picker, Google, All, platform-level Campaigns
def platform_btn(page, key):
    return page.locator(f"#platform-ctl [data-platform={key}]")


# ------------------------------------------------------------------ platform trends and All campaigns
def chart_sum(info, label_startswith=None):
    """sum of the bar series of the trend chart (nulls are zeros)"""
    return sum(sum(v or 0 for v in d["data"]) for d in bars_ds(info) if label_startswith is None or d["label"].startswith(label_startswith))


def col_index(page, key, testid):
    return table_data(page, testid)["heads"].index(key)


# ------------------------------------------------------------------ All views: Total spend and Total NC ROAS
def strip_val(page, key):
    return page.locator(f"[data-testid=totals-strip] [data-testid=kpi-{key}-value]").get_attribute("data-value")


def strip_text(page, key):
    return page.locator(f"[data-testid=totals-strip] [data-testid=kpi-{key}-value]").inner_text()


def wait_strip(page):
    page.locator("[data-testid=totals-strip] [data-testid=kpi-spend-value]").wait_for()


def api_json(page, url):
    return page.evaluate("async (u) => (await fetch(u)).json()", url)


TILES = ["kpi-spend", "kpi-revenue", "kpi-roas"]


def tile_ids(page):
    return page.locator("[data-testid=totals-strip] .kpi").evaluate_all("els => els.map(e => e.dataset.testid)")


def tile_labels(page):
    return page.locator("[data-testid=totals-strip] .kpi-label").evaluate_all("els => els.map(e => e.textContent)")           # the CSS upper-cases them on screen


def table_total(page, testid, key):
    data = table_data(page, testid)
    return num(data["footRows"][0]["cells"][data["heads"].index(key)])


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
