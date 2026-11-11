"""Meta products, tag tree, product page, ads and campaigns tables, columns, hooks, coverage.

Split out of tests/ui/test_ui.py with no assertion changes. Shared helpers live in ui_helpers.py. Run: see the docstring of conftest.py.
"""
import re
from datetime import date, timedelta
from urllib.parse import parse_qs, urlencode, urlparse
import pytest
from playwright.sync_api import expect
from conftest import IS_MOCK, PASSWORD, SHOTS, USERNAME
from ui_helpers import *  # noqa: F401,F403
from ui_helpers import _group_spends  # noqa: F401

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
