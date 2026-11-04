"""Playwright UI tests. Run against the mock (default) or the real API:

    .venv/bin/python -m pytest tests/ui                       # starts tests/ui/mock_server.py on :5099
    UI_BASE_URL=http://127.0.0.1:5098 UI_PASSWORD=test .venv/bin/python -m pytest tests/ui   # real API

Tests are data-agnostic (they drill into whatever the first rows are) except those marked mock_only.
"""
import re
from datetime import date, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.sync_api import expect

from conftest import IS_MOCK, PASSWORD, SHOTS

SLUG = "vita-bull"
TABLE = "[data-testid=level-table]"
BODY_ROWS = f"{TABLE} > .tablewrap > table > tbody > tr[data-key]:not(.pseudo)"
HEAD = f"{TABLE} > .tablewrap > table > thead"
STAGE_COLS = ["testing_spend", "scaling_spend", "testing_pct"]
METRIC_COLS = ["unique_ads", "live_now", "spend", "spend_share", "purchases", "revenue", "roas", "cpa"]


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
          const foot = [...t.querySelectorAll(':scope > tfoot > tr > td')].map(td => td.innerText.trim());
          return {heads, rows, keys, labels, levels, foot};
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


def goto_product(page, params=""):
    page.goto(f"/p/{SLUG}{params}")
    wait_rows(page)


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
    for expected in ("ICPs in", "Pain points in", "Ad families in"):
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


def chip(page, stage):
    return page.locator(f".schip[data-stage={stage}]")


# ------------------------------------------------------------------ login
@pytest.mark.allow_errors
def test_login_flow(anon_page, base_url):
    page = anon_page
    page.goto("/p/" + SLUG + "?window=7d")
    expect(page).to_have_url(re.compile(r"/login\?next="))
    expect(page.locator("#password")).to_be_visible()
    page.fill("#password", "definitely-wrong")
    page.click("button[type=submit]")
    expect(page.locator("#login-error")).to_contain_text("Wrong password")
    expect(page).to_have_url(re.compile(r"/login"))
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
    shares = [num(v) for v in col(page, "spend_share", "products-table")]
    assert abs(sum(s or 0 for s in shares) - 100) <= 1, shares
    assert page.locator("[data-testid=products-table] .badge").count() >= 1
    for gone in ("spend_7d", "cpa_7d"):
        assert not has_col(page, gone, "products-table")
    shot(page, "02_products")
    # the stage chips and compare control sit in the header here too; row click -> product page, window carried
    expect(chip(page, "testing")).to_be_visible()
    expect(page.locator("#cmp")).to_be_visible()
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
    expect(page.locator("#level-title")).to_contain_text("Ad families in")
    wait_rows(page)
    q = qparams(page)
    assert (q["cluster"], q["icp"], q["pain"]) == (cluster, icp, pain)
    crumbs = page.locator(".crumbs li").all_inner_texts()
    assert crumbs[0] == "Products" and crumbs[2:5] == [cluster, icp, pain], crumbs
    shot(page, "03_families")
    first_row(page).locator("a.row-open").click()
    expect(page).to_have_url(re.compile(r"/ad/\d+"))
    expect(page.locator("#ad-title")).to_be_visible()
    crumbs = page.locator(".crumbs li").all_inner_texts()
    assert crumbs[0] == "Products" and crumbs[-1] != "" and len(crumbs) >= 3
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
    labels = [t.strip().replace("\n", " ") for t in page.locator(f"{HEAD} th").all_inner_texts()]
    assert not any("7d" in x for x in labels), labels
    for lbl in ["Unique ads", "Live now", "Spend", "% of spend", "Purchases", "Revenue", "ROAS", "CPA", "Testing $", "Scaling $", "Testing %"]:
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
    expect(page.locator("#all-ads")).to_have_text("All ads in view")
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
        assert abs(sum(r[3] or 0 for r in rows if r[0] == lv) - 100) <= 1.5, lv
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
    page.evaluate("() => window.scrollTo(0, 260)")
    page.wait_for_timeout(100)
    y = page.evaluate("() => window.scrollY")
    assert y > 100
    url = page.url
    page.locator(f"{TABLE} tr.lv3 a.row-open").first.click()
    expect(page.locator("#ad-title")).to_be_visible()
    expect(page.locator("#back")).to_be_enabled()
    page.click("#back")
    wait_rows(page)
    assert page.url == url
    page.wait_for_selector(f"{TABLE} tr.lv3")
    after = table_data(page)
    assert after["keys"] == before["keys"], "expanded rows / order not restored"
    expect(page.locator(f"{HEAD} th[data-col=spend]")).to_have_attribute("aria-sort", "ascending")
    page.wait_for_timeout(200)
    assert abs(page.evaluate("() => window.scrollY") - y) <= 8
    shot(page, "20_back_restored")


def test_back_walks_the_drills_and_is_disabled_on_products(page):
    page.goto("/")
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
    page.goto(f"/p/{SLUG}?cluster={cluster}&icp={icp}&pain={pain}")  # fresh entry: no in-app history
    wait_rows(page)
    expect(page.locator("#back")).to_be_enabled()
    page.click("#back")
    expect(page.locator("#level-title")).to_contain_text("Pain points in")
    assert "pain" not in qparams(page) and qparams(page)["icp"] == icp
    wait_rows(page)
    page.goto(ad_href)
    expect(page.locator("#ad-title")).to_be_visible()
    page.click("#back")  # ad detail -> its pain-point (families) page
    expect(page.locator("#level-title")).to_contain_text("Ad families in")


def test_filter_changes_replace_history_so_back_is_not_an_undo(page):
    page.goto("/")
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
        expect(page.locator("#level-title")).to_contain_text("Ad families in")
    for key in ["spend", "spend_share", "roas", "cpa", "unique_ads", "testing_pct"]:
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
    goto_product(page)
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
    assert not any(has_col(page, c) for c in STAGE_COLS), "Testing $ / Scaling $ / Testing % hide with one stage"
    if IS_MOCK:
        assert abs(num(foot_value(page, "spend")) - scaling_all) <= 2
    open_first_row(page)  # carried across a drill
    expect(page.locator("#level-title")).to_contain_text("ICPs in")
    assert qparams(page)["stage"] == "scaling"
    expect(chip(page, "testing")).to_have_attribute("aria-pressed", "false")
    url = page.url  # at least one stays on: clicking the only active chip does nothing
    chip(page, "scaling").click()
    page.wait_for_timeout(200)
    assert page.url == url
    expect(chip(page, "scaling")).to_have_attribute("aria-pressed", "true")
    chip(page, "testing").click()  # both on again = all = param omitted
    wait_pressed(page, "stage", "testing")
    assert "stage" not in qparams(page)
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
    page.goto("/?stage=scaling")
    page.wait_for_selector("[data-testid=products-table] tbody tr[data-key]")
    assert not any(has_col(page, c, "products-table") for c in STAGE_COLS)
    page.goto("/?stage=testing")
    page.wait_for_selector("[data-testid=products-table] tbody tr[data-key]")
    assert not any(has_col(page, c, "products-table") for c in STAGE_COLS)
    page.goto("/")
    page.wait_for_selector("[data-testid=products-table] tbody tr[data-key]")
    assert all(has_col(page, c, "products-table") for c in STAGE_COLS)
    cluster, icp, pain = drill_to_families(page)
    for params, both in (("", True), ("&stage=testing", False), ("&stage=scaling", False)):
        page.goto(f"/p/{SLUG}?cluster={cluster}&icp={icp}&pain={pain}{params}")
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
    page.wait_for_selector("[data-testid=campaign-table] tbody tr[data-key]")
    assert page.locator(".stagecard").count() == 2
    page.goto(f"/p/{SLUG}?view=campaigns&stage=scaling")
    page.wait_for_selector("[data-testid=campaign-table] tbody tr[data-key]")
    assert page.locator(".stagecard").count() == 1
    assert page.locator(".stagecard[data-stage=scaling]").count() == 1


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
    assert {"ad_name", "campaign_name", "stage", "adset_name", "status", "created_date", "spend", "spend_share", "purchases", "revenue", "roas", "cpa", "live"} <= set(d["heads"])
    assert "spend_7d" not in d["heads"] and "cpa_7d" not in d["heads"]
    total = num(foot_value(page, "spend", "ads-table"))
    assert abs(total - spend) <= max(2, 0.001 * spend), (total, spend)
    assert page.locator("[data-testid=ads-table] .badge").count() >= 1
    crumbs = page.locator(".crumbs li").all_inner_texts()
    assert crumbs[-1] == "Ads" and key in crumbs
    sort_both_ways(page, "spend", "ads-table")
    sh = [num(v) for v in col(page, "spend_share", "ads-table")]
    assert abs(sum(s or 0 for s in sh) - 100) <= 1.5 or spend == 0
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
    expect(page.locator("[data-testid=ads-table] > .tablewrap > table > thead th[data-col=spend_share]")).to_contain_text("% of family")


def test_families_flat_view_filters_and_deep_link(page):
    goto_product(page, "?all=1")
    expect(page.locator("#level-title")).to_have_text("All ad families")
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
    expect(page.locator("#level-title")).to_have_text("All ad families")
    wait_rows(page)
    assert qparams(page).get("all") == "1"
    assert len(table_data(page)["rows"]) >= 1


# ------------------------------------------------------------------ campaigns view
def test_campaigns_view(page):
    goto_product(page)
    page.locator("a[data-view=campaigns]").click()
    expect(page).to_have_url(re.compile(r"view=campaigns"))
    page.wait_for_selector("[data-testid=campaign-table] tbody tr[data-key]")
    assert page.locator(".stagecard").count() == 2
    assert "7d" not in page.locator(".stagecards").inner_text()
    expect(page.locator("#stage-chart")).to_be_visible()
    ds = page.evaluate("() => { const c = Chart.getChart('stage-chart'); return {n: c.data.datasets.length, days: c.data.labels.length, stacked: c.options.scales.x.stacked}; }")
    assert ds["n"] == 3 and ds["days"] > 5 and ds["stacked"] is True
    assert not has_col(page, "spend_7d", "campaign-table")
    sort_both_ways(page, "spend", "campaign-table")
    sort_both_ways(page, "roas", "campaign-table")
    sh = [num(v) for v in col(page, "spend_share", "campaign-table")]
    assert abs(sum(s or 0 for s in sh) - 100) <= 1.5
    shot(page, "12_campaigns")
    chip(page, "scaling").click()  # the global stage chips filter the campaign table
    wait_pressed(page, "stage", "testing")
    page.wait_for_selector("[data-testid=campaign-table] tbody tr[data-key], [data-testid=campaign-table] tr.emptyrow")
    assert all(s == "testing" for s in col(page, "stage", "campaign-table"))
    chip(page, "scaling").click()
    idle(page)
    assert "stage" not in qparams(page)
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
    page.wait_for_selector("#daily-chart")


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
        expect(page.locator("#meta-ads th[data-col=spend_share]")).to_contain_text("% of family")
    info = page.evaluate("""() => { const c = Chart.getChart('daily-chart');
        return {n: c.data.datasets.length, days: c.data.labels.length, shades: c.options.plugins.windowShade.shades, first: c.data.labels[0], last: c.data.labels[c.data.labels.length-1]}; }""")
    assert info["n"] == 3 and info["days"] >= 100
    sh = info["shades"]
    assert len(sh) == 1 and sh[0]["i1"] == info["days"] - 1 and sh[0]["i1"] - sh[0]["i0"] == 6, info
    if IS_MOCK:
        assert info["days"] == 181 and info["first"] == "2026-05-06" and info["last"] == "2026-11-02"
    page.uncheck("#toggle-revenue")
    assert page.evaluate("() => Chart.getChart('daily-chart').isDatasetVisible(1)") is False
    page.check("#toggle-revenue")
    assert page.evaluate("() => Chart.getChart('daily-chart').isDatasetVisible(1)") is True
    expect(page.locator("#family-metrics")).to_contain_text("days")


@pytest.mark.mock_only
def test_ad_detail_mock_content(page):
    api = page.context.request
    fams = api.get(f"/api/products/{SLUG}/families").json()["rows"]
    two = next(r for r in fams if r["n_rows"] > 1 and r["counted_here"])
    page.goto(f"/ad/{two['primary_id']}?window=30d")
    expect(page.locator("#ad-title")).to_have_text(two["display"])
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
    expect(page.locator("#ad-title")).to_have_text("XSS-<b>test</b>-family")
    expect(page.locator("#copy-block")).to_contain_text("<img src=x onerror=window.__xss=1>")
    assert page.evaluate("() => window.__xss") is None
    assert page.locator("a[href^='javascript:']").count() == 0
    assert page.locator("#copy-block img, #copy-block b, #copy-block script").count() == 0
    drill = api.get(f"/api/products/{SLUG}/tree?root=cluster&depth=3").json()["rows"]  # and in the dropdown tree
    row = next(r for r in drill if r["level"] == 3 and r["label"].startswith("XSS"))
    page.goto(f"/p/{SLUG}?cluster={row['cluster']}&icp={row['icp']}&pain={row['pain']}")
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
    for key in ["spend", "spend_share", "purchases", "revenue", "roas", "cpa"]:  # A on line 1, "vs B ▲x%" on line 2
        cells = page.locator(f"{BODY_ROWS} td:nth-child({heads.index(key) + 1}) .cmpcell")
        assert cells.count() >= 1, key
        assert cells.first.locator(".b").inner_text().startswith("vs "), key
    assert page.locator(f"{TABLE} tfoot .cmpcell").count() >= 5  # totals row too
    for key, good_up in [("roas", True), ("purchases", True), ("revenue", True), ("cpa", False), ("spend", None)]:
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
    sh = page.evaluate("() => Chart.getChart('daily-chart').options.plugins.windowShade.shades")
    assert len(sh) == 2 and sh[0]["i0"] > sh[1]["i0"], sh  # A (recent) and B (earlier), two tints
    expect(page.locator("#daily-card .shade-note")).to_contain_text("A")
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
    page.wait_for_selector("[data-testid=campaign-table] tbody tr[data-key]")
    idle(page)
    cards = page.locator(".stagecard")
    assert cards.count() == 2
    for i in range(2):
        assert " vs " in cards.nth(i).inner_text().replace("\n", " ")
    assert page.locator("[data-testid=campaign-table] .cmpcell").count() >= 1
    expect(page.locator("#range-label")).to_contain_text(" vs ")
    page.locator("[data-testid=campaign-table] tbody tr[data-key] a.rowlink").first.click()
    wait_rows(page, "ads-table")
    assert page.locator("[data-testid=ads-table] tfoot .cmpcell").count() >= 1
    sort_both_ways(page, "spend", "ads-table")
    shot(page, "25_compare_campaigns")


# ------------------------------------------------------------------ deep links, 401, layout
def test_deep_link_reload_keeps_state(page):
    labels = drill_to_families(page)
    page.locator(".seg[data-window='14d']").click()
    wait_pressed(page, "window", "14d")
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
    expect(page.locator("#level-title")).to_contain_text("Ad families in")
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
    page.goto(f"/p/{SLUG}?window=7d")
    wait_rows(page)


def test_no_console_errors_across_pages(page):
    """walk every page type; the fixture fails the test on any console error / page error"""
    page.goto("/")
    page.wait_for_selector("[data-testid=products-table] tbody tr[data-key]")
    drill_to_families(page)
    page.locator(f"{BODY_ROWS} button.caret").first.click()
    page.wait_for_selector(f"{TABLE} tr.lv4", timeout=8000)
    first_row(page).locator("a.row-open").click()
    page.wait_for_selector("#daily-chart")
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


@pytest.mark.parametrize("path", ["/", f"/p/{SLUG}", f"/p/{SLUG}?view=campaigns", f"/p/{SLUG}?all=1", f"/p/{SLUG}?window=7d&compare=prev"])
def test_no_page_level_horizontal_scroll_at_laptop_width(ctx, page, path):
    page.set_viewport_size({"width": 1280, "height": 720})
    page.goto(path)
    page.wait_for_selector(".tablebox")
    idle(page)
    page.wait_for_timeout(200)
    sw = page.evaluate("() => [document.documentElement.scrollWidth, window.innerWidth]")
    assert sw[0] <= sw[1] + 1, sw
