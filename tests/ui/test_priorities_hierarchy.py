"""Creative priorities (/priorities): the grouped hierarchy and the pinned bars, against the mock server.

No column sort = the grouped view: rows ordered product > Market Cluster > ICP > Pain Point in the Communication Overview order (row.sheet_pos), with Product, Market Cluster and ICP as real merged
cells (rowspan), colour keyed by the cluster name. A column sort = the flat view. The selection bar and the column header are pinned; at phone width only the bar is, and the group labels become heading bars.
"""
import json
import re

import pytest

from conftest import SHOTS

pytestmark = pytest.mark.mock_only

ROW = "[data-testid=pri-body] tr.pri-row"
PICK = "[data-testid=pri-body] [data-testid=pri-pick]"
TOGGLE = "[data-testid=pri-body] [data-testid=pri-toggle]"
TOOLS = "[data-testid=pri-seltools]"
TABLE = "[data-testid=pri-table]"
PRODUCTS = ["vita-bull", "beef-liver", "boneflex"]
FIXTURE = json.load(open(__import__("pathlib").Path(__file__).resolve().parent / "priorities_fixture.json"))


def idle(page):
    page.wait_for_function("() => !document.querySelector('#view').hasAttribute('aria-busy')")


def goto(page, qs=""):
    page.goto("/priorities" + qs)
    page.wait_for_selector("[data-testid=pri-page]")
    idle(page)


def is_grouped(page):
    return page.evaluate("() => document.querySelector('[data-testid=pri-table]').classList.contains('pri-grouped')")


def rows_info(page):
    """One dict per row in table order: product, cluster / icp / pain text (read from the cells, merged or not), whether each of the three merged cells is the group's visible cell, and its rowspan."""
    return page.evaluate("""() => [...document.querySelectorAll('[data-testid=pri-body] tr.pri-row')].map(r => {
      const c = (k) => r.querySelector('.pri-c-' + k);
      const m = (k) => ({ text: c(k).textContent.trim(), merged: c(k).classList.contains('pri-merged'), span: c(k).rowSpan, hid: getComputedStyle(c(k)).display === 'none' });
      return { product: r.dataset.product, section: r.dataset.section, cluster: m('cluster'), icp: m('icp'), prod: m('product'), pain: c('pain').textContent.trim(), open: r.classList.contains('pri-open') };
    })""")


GRID_JS = """() => {
  const rows = [...document.querySelectorAll('[data-testid=pri-body] > tr')].filter(r => getComputedStyle(r).display !== 'none');
  let occ = {}; const widths = [];
  for (const tr of rows) {
    let col = 0;
    for (const c of [...tr.children].filter(c => getComputedStyle(c).display !== 'none')) {
      while ((occ[col] || 0) > 0) col++;
      const cs = c.colSpan || 1, rs = c.rowSpan || 1;
      for (let k = 0; k < cs; k++) if (rs > 1) occ[col + k] = rs;
      col += cs;
    }
    while ((occ[col] || 0) > 0) col++;
    widths.push(col);
    for (const k of Object.keys(occ)) { occ[k] -= 1; if (occ[k] <= 0) delete occ[k]; }
  }
  return { widths, leftover: Object.keys(occ).length, n: rows.length };
}"""


def assert_grid_valid(page):
    """Every rendered row fills the same number of columns (merged cells counted in the rows they span) and no merged cell runs past the last row."""
    g = page.evaluate(GRID_JS)
    assert g["n"] > 0 and len(set(g["widths"])) == 1, g
    assert g["leftover"] == 0, g


def assert_geometry(page):
    """Each merged cell exactly covers the rows of its group on screen: its box runs from its first row's top to the bottom of its last row (the open detail row included)."""
    bad = page.evaluate("""() => {
      const rows = [...document.querySelectorAll('[data-testid=pri-body] > tr')].filter(r => getComputedStyle(r).display !== 'none');
      const bad = [];
      for (const td of document.querySelectorAll('[data-testid=pri-body] td.pri-merged')) {
        const first = td.parentElement, i = rows.indexOf(first);
        const last = rows[i + td.rowSpan - 1];
        const a = td.getBoundingClientRect(), f = first.getBoundingClientRect(), l = last.getBoundingClientRect();
        if (Math.abs(a.top - f.top) > 1.5 || Math.abs(a.bottom - l.bottom) > 1.5) bad.push([td.textContent.trim().slice(0, 30), a.top - f.top, a.bottom - l.bottom]);
      }
      return bad;
    }""")
    assert bad == [], bad


# ---------------------------------------------------------------------------------------------------------------- order and merged cells
def test_default_is_the_grouped_view_in_product_cluster_icp_pain_order(page):
    goto(page)
    assert is_grouped(page)
    assert page.locator("[data-testid=pri-group-btn]").get_attribute("aria-pressed") == "true"
    info = rows_info(page)
    assert len(info) == 30
    # products first, in the page's product order, one run each
    prods = [r["product"] for r in info]
    assert [p for i, p in enumerate(prods) if i == 0 or prods[i - 1] != p] == PRODUCTS
    # inside a product the clusters are one run each, in sheet order (the line's sheet position): on-sheet clusters by their first line, clusters with no sheet line (new ideas) after them
    pos = {(r["product"], r["line_id"]): r["line_id"] for r in FIXTURE["rows"] if r.get("line_id") is not None}
    by_row = {(r["product"], r["cluster"], r["icp"], r["pain_point"]): r.get("line_id") for r in FIXTURE["rows"]}
    for prod in PRODUCTS:
        mine = [r for r in info if r["product"] == prod]
        clusters = [r["cluster"]["text"] for r in mine]
        runs = [c for i, c in enumerate(clusters) if i == 0 or clusters[i - 1] != c]
        assert len(runs) == len(set(runs)), (prod, runs)                                             # no cluster appears in two places
        first_pos = {}
        for r in mine:
            lid = by_row[(prod, r["cluster"]["text"], r["icp"]["text"], r["pain"])]
            first_pos[r["cluster"]["text"]] = min(first_pos.get(r["cluster"]["text"], 1e9), lid if lid is not None else 1e9)
        assert [first_pos[c] for c in runs] == sorted(first_pos[c] for c in runs), (prod, [(c, first_pos[c]) for c in runs])
        for cl in runs:                                                                              # the same inside a cluster: one run per ICP, in sheet order
            inner = [r for r in mine if r["cluster"]["text"] == cl]
            icps = [r["icp"]["text"] for r in inner]
            iruns = [c for i, c in enumerate(icps) if i == 0 or icps[i - 1] != c]
            assert len(iruns) == len(set(iruns)), (prod, cl, iruns)
            ip = {}
            for r in inner:
                lid = by_row[(prod, cl, r["icp"]["text"], r["pain"])]
                ip[r["icp"]["text"]] = min(ip.get(r["icp"]["text"], 1e9), lid if lid is not None else 1e9)
            assert [ip[c] for c in iruns] == sorted(ip[c] for c in iruns)
    # rows inside one ICP keep the sheet order of their lines
    for prod in PRODUCTS:
        for cl in {r["cluster"]["text"] for r in info if r["product"] == prod}:
            for ic in {r["icp"]["text"] for r in info if r["product"] == prod and r["cluster"]["text"] == cl}:
                ids = [by_row[(prod, cl, ic, r["pain"])] for r in info if r["product"] == prod and r["cluster"]["text"] == cl and r["icp"]["text"] == ic]
                ids = [i if i is not None else 1e9 for i in ids]
                assert ids == sorted(ids)
    assert pos                                                                                         # the fixture carries line ids (the mock's stand-in for the sheet position)


def test_new_ideas_not_on_the_sheet_group_under_their_own_cluster_after_the_sheet_rows(page):
    goto(page)
    info = rows_info(page)
    for prod in PRODUCTS:
        mine = [r for r in info if r["product"] == prod]
        drafts = [i for i, r in enumerate(mine) if r["section"] == "test" and r["cluster"]["text"] in ("Chest Fat (cortisol / estrogen)", "GLP-1 Companion (men)", "Thyroid", "Fertility & Postpartum", "Menopause", "Spanish-Language Joint Buyer")]
        assert drafts and drafts == list(range(len(mine) - len(drafts), len(mine))), (prod, drafts, len(mine))     # last, each under its own merged cluster cell
        assert all(mine[i]["cluster"]["merged"] for i in drafts if i == 0 or mine[i - 1]["cluster"]["text"] != mine[i]["cluster"]["text"])


@pytest.mark.parametrize("width", [1440, 1100])
def test_merged_cells_span_exactly_their_group_with_one_cell_per_group(browser, base_url, width):
    c = browser.new_context(viewport={"width": width, "height": 900}, base_url=base_url)
    c.request.post(base_url + "/api/login", data={"username": "JoshHoughton", "password": "test"})
    p = c.new_page()
    try:
        goto(p)
        info = rows_info(p)
        for level, key in (("prod", lambda r: (r["product"],)), ("cluster", lambda r: (r["product"], r["cluster"]["text"])), ("icp", lambda r: (r["product"], r["cluster"]["text"], r["icp"]["text"]))):
            groups = {}
            for i, r in enumerate(info):
                groups.setdefault(key(r), []).append(i)
            for k, idx in groups.items():
                assert idx == list(range(idx[0], idx[-1] + 1)), (level, k)                          # the group is contiguous
                merged = [i for i in idx if info[i][level]["merged"]]
                assert merged == [idx[0]], (level, k, merged)                                       # exactly one visible cell for the group, on its first row
                assert info[idx[0]][level]["span"] == len(idx), (level, k, info[idx[0]][level]["span"], len(idx))      # its rowspan = the group's rows (all panels closed)
                assert all(info[i][level]["hid"] for i in idx[1:]), (level, k)                      # the rest of the group's cells are not drawn
        assert_grid_valid(p)
        assert_geometry(p)
        # text centred both ways in the merged cells
        cs = p.evaluate("() => [...document.querySelectorAll('[data-testid=pri-body] td.pri-merged')].map(t => { const s = getComputedStyle(t); return [s.textAlign, s.verticalAlign]; })")
        assert cs and all(a == "center" and v == "middle" for a, v in cs), cs[:3]
    finally:
        c.close()


def test_pain_point_stays_one_cell_per_row_and_the_checkbox_toggle_and_story_stay_per_row(page):
    goto(page)
    assert page.locator("[data-testid=pri-body] td.pri-c-pain.pri-merged").count() == 0
    assert page.locator(ROW).count() == page.locator(PICK).count() == page.locator(TOGGLE).count() == 30
    assert all(rowspan == 1 for rowspan in page.evaluate("() => [...document.querySelectorAll('[data-testid=pri-body] td.pri-c-pain, [data-testid=pri-body] td.pri-c-tog')].map(t => t.rowSpan)"))


# ---------------------------------------------------------------------------------------------------------------- colour
def test_same_cluster_name_has_the_same_colour_under_every_product_and_icp_is_lighter(page):
    """Cluster colour is keyed by the NAME: one cluster name used under all three products gets one fill; the ICP cell is a lighter tint; different names differ."""
    def same_name(route):
        r = route.fetch()
        d = r.json()
        seen = {}
        for row in d["rows"]:
            if row["product"] in PRODUCTS and row.get("line_id") is not None and row["product"] not in seen:
                seen[row["product"]] = True
                row["cluster"] = "Energy & Multivitamin"                                             # the same cluster name under each product
        route.fulfill(response=r, json=d)
    page.route("**/api/priorities?*", same_name)
    page.route("**/api/priorities", same_name)
    goto(page)
    fills = page.evaluate("""() => [...document.querySelectorAll('[data-testid=pri-body] tr.pri-row')].filter(r => r.querySelector('.pri-c-cluster.pri-merged')).map(r => {
      const cl = r.querySelector('.pri-c-cluster'), ic = r.querySelector('.pri-c-icp'), co = r.querySelector('.pri-c-pain');
      const bg = (e) => getComputedStyle(e).backgroundColor;
      return { product: r.dataset.product, name: cl.textContent.trim(), cluster: bg(cl), icp: ic.classList.contains('pri-merged') ? bg(ic) : null, cell: bg(co) };
    })""")
    shared = [f for f in fills if f["name"] == "Energy & Multivitamin"]
    assert {f["product"] for f in shared} == set(PRODUCTS)
    assert len({f["cluster"] for f in shared}) == 1                                                   # same name, same colour, across products
    assert len({f["cluster"] for f in fills}) > 4                                                     # different names get different colours (a fixed palette of 16 hues)

    def lightness(rgb):
        r, g, b = [int(x) for x in re.findall(r"\d+", rgb)[:3]]
        return 0.2126 * r + 0.7152 * g + 0.0722 * b
    for f in fills:
        if f["icp"]:
            assert lightness(f["icp"]) > lightness(f["cluster"]), f                                   # ICP: a lighter tint of the cluster colour
        assert lightness(f["cell"]) > lightness(f["icp"] or f["cluster"]) - 1, f                      # the rows: the faintest tint


CONTRAST_JS = """() => {
  const lum = (c) => { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); };
  const parse = (s) => { const m = s.match(/[\\d.]+/g).map(Number); return [m[0], m[1], m[2], m.length > 3 ? m[3] : 1]; };
  const over = (top, bot) => [top[0] * top[3] + bot[0] * (1 - top[3]), top[1] * top[3] + bot[1] * (1 - top[3]), top[2] * top[3] + bot[2] * (1 - top[3]), 1];
  const bgOf = (el) => { const stack = []; for (let e = el; e; e = e.parentElement) { const b = parse(getComputedStyle(e).backgroundColor); if (b[3] > 0) stack.push(b); if (b[3] === 1) break; } let acc = [255, 255, 255, 1]; for (const b of stack.reverse()) acc = over(b, acc); return acc; };
  const opacityOf = (el) => { let o = 1; for (let e = el; e && e.tagName !== 'BODY'; e = e.parentElement) o *= parseFloat(getComputedStyle(e).opacity); return o; };
  const out = [];
  for (const el of document.querySelectorAll('[data-testid=pri-body] tr.pri-row *')) {
    if (getComputedStyle(el).display === 'none' || !el.offsetParent) continue;
    if (![...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim())) continue;
    if (el.closest('[hidden]') || el.closest('.pri-hid')) continue;
    const bg = bgOf(el), fg = parse(getComputedStyle(el).color);
    const a = fg[3] * opacityOf(el);
    const eff = over([fg[0], fg[1], fg[2], a], bg);
    const L1 = lum(eff), L2 = lum(bg);
    const ratio = (Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05);
    out.push({ text: el.textContent.trim().slice(0, 24), cls: el.className.toString().slice(0, 30) || el.tagName, ratio: Math.round(ratio * 100) / 100 });
  }
  return out;
}"""


def test_text_keeps_4_5_to_1_contrast_on_every_fill_in_the_grouped_view(page):
    goto(page)
    res = page.evaluate(CONTRAST_JS)
    assert len(res) > 150
    low = [r for r in res if r["ratio"] < 4.5]
    assert low == [], low[:8]
    merged = page.evaluate("""() => [...document.querySelectorAll('[data-testid=pri-body] td.pri-merged')].length""")
    assert merged > 20


# ---------------------------------------------------------------------------------------------------------------- flat view and back
def test_a_column_sort_switches_to_the_flat_view_and_group_by_cluster_restores_it(page):
    goto(page)
    btn = page.locator("[data-testid=pri-group-btn]")
    assert btn.is_visible() and btn.text_content().strip() == "Group by cluster"
    page.locator("[data-testid=pri-sort-pain]").click()
    assert not is_grouped(page) and btn.get_attribute("aria-pressed") == "false"
    assert page.locator("[data-testid=pri-body] td.pri-merged").count() == 0                          # flat: no merged cells
    assert all(rowspan == 1 for rowspan in page.evaluate("() => [...document.querySelectorAll('[data-testid=pri-body] tr.pri-row td')].map(t => t.rowSpan)"))
    assert page.evaluate("() => [...document.querySelectorAll('[data-testid=pri-body] tr.pri-row')].every(r => ['product','cluster','icp'].every(k => getComputedStyle(r.querySelector('.pri-c-' + k)).display !== 'none' && r.querySelector('.pri-c-' + k).textContent.trim()))")
    pains = page.locator("[data-testid=pri-body] tr.pri-row .pri-c-pain").all_text_contents()
    assert pains == sorted(pains, key=str.lower)
    assert page.evaluate("() => getComputedStyle(document.querySelector('[data-testid=pri-body] .pri-c-cluster'), '::before').content") != "none"      # the cluster keeps its colour chip
    assert_grid_valid(page)
    page.screenshot(path=str(SHOTS / "priorities_flat.png"))
    btn.click()
    assert is_grouped(page) and btn.get_attribute("aria-pressed") == "true"
    assert page.locator("[data-testid=pri-body] td.pri-merged").count() > 20
    assert all(th.get_attribute("aria-sort") in (None, "none") for th in page.locator("[data-testid=pri-table] thead th").all())          # the column sort is cleared
    assert_grid_valid(page)
    page.screenshot(path=str(SHOTS / "priorities_grouped.png"))


def test_the_group_by_cluster_choice_survives_a_saved_filter_view_and_old_views_still_load(page):
    goto(page)
    page.locator("[data-testid=pri-sort-cluster]").click()                                            # a sorted (flat) view is saved with its sort
    page.locator("[data-testid=pri-save-open]").click()
    page.locator("[data-testid=pri-name-dialog]").wait_for()
    page.locator("[data-testid=pri-kind-filters]").check()
    page.locator("[data-testid=pri-view-name]").fill("Flat by cluster")
    page.locator("[data-testid=pri-dialog-save]").click()
    page.wait_for_selector("[data-testid=pri-view-link]")
    tok = page.locator("[data-testid=pri-view-link]").get_attribute("href").rsplit("/", 1)[1]
    goto(page, f"?view={tok}")
    assert not is_grouped(page)                                                                        # saved with a column sort: opens flat
    # a view saved before this change (no sort = key null) opens grouped; one with a sort key opens flat; neither errors
    old = page.request.post(page.url.split("/priorities")[0] + "/api/priorities/views", data={"name": "Old view", "kind": "filters", "filters": {"products": [], "sections": [], "q": "", "key": None, "dir": 1, "status": None, "statuses": []}})
    assert old.ok
    goto(page, "?view=" + old.json()["token"])
    assert is_grouped(page) and page.locator("[data-testid=pri-body] td.pri-merged").count() > 5 and page.locator(ROW).count() == 30
    assert_grid_valid(page)
    old2 = page.request.post(page.url.split("/priorities")[0] + "/api/priorities/views", data={"name": "Old sorted view", "kind": "filters", "filters": {"products": ["boneflex"], "sections": [], "q": "", "key": "pain", "dir": -1, "status": None, "statuses": []}})
    goto(page, "?view=" + old2.json()["token"])
    assert not is_grouped(page) and page.locator(ROW).count() == 10


# ---------------------------------------------------------------------------------------------------------------- merges stay valid under every row interaction
def test_merges_stay_valid_after_filter_expand_collapse_all_and_show_selected_only(page):
    goto(page)
    assert_grid_valid(page)
    page.locator("[data-testid=pri-body] [data-testid=pri-toggle]").nth(4).click()                    # open one panel in the middle of a group
    assert_grid_valid(page)
    assert_geometry(page)
    info = rows_info(page)
    opened = info.index(next(r for r in info if r["open"]))
    grp = [i for i, r in enumerate(info) if r["product"] == info[opened]["product"] and r["cluster"]["text"] == info[opened]["cluster"]["text"]]
    first = grp[0]
    assert info[first]["cluster"]["span"] == len(grp) + 1                                             # the cluster cell now also covers the open panel row
    page.locator("[data-testid=pri-all-expand]").click()
    assert_grid_valid(page)
    assert_geometry(page)
    first_cluster = page.locator("[data-testid=pri-body] td.pri-c-cluster.pri-merged").first
    assert int(first_cluster.get_attribute("rowspan")) >= 2 * 1 + 1
    assert page.locator("[data-testid=pri-body] tr.pri-detail:not([hidden])").count() == 30
    page.locator("[data-testid=pri-all-collapse]").click()
    assert_grid_valid(page)
    assert all(r["cluster"]["span"] == len([x for x in rows_info(page) if x["product"] == r["product"] and x["cluster"]["text"] == r["cluster"]["text"]]) for r in rows_info(page) if r["cluster"]["merged"])
    # filters shrink the groups, they do not break them (the first product click isolates that product, a second adds one)
    page.locator("[data-testid=pri-f-product-beef-liver]").click()
    assert page.locator(ROW).count() == 10 and is_grouped(page)
    assert_grid_valid(page)
    assert_geometry(page)
    page.locator("[data-testid=pri-f-product-boneflex]").click()
    assert page.locator(ROW).count() == 20
    assert_grid_valid(page)
    page.locator("[data-testid=pri-f-section-test]").click()
    assert is_grouped(page) and 0 < page.locator(ROW).count() < 20
    assert_grid_valid(page)
    assert_geometry(page)
    page.locator("[data-testid=pri-search]").fill("peptide")
    assert page.locator(ROW).count() == 0 or page.locator(ROW).count() < 10
    page.locator("[data-testid=pri-search]").fill("hair")
    assert 0 < page.locator(ROW).count() < 10
    assert_grid_valid(page)
    assert_geometry(page)
    info = rows_info(page)                                                                             # a group whose first row was filtered out gets its merged cell on its new first row
    for lvl in ("prod", "cluster", "icp"):
        assert info[0][lvl]["merged"]
    goto(page)
    # show selected only, with an open panel in the middle of a selected group
    pick = page.locator(PICK)
    for i in (1, 2, 12):
        pick.nth(i).check()
    page.locator("[data-testid=pri-sel-only]").click()
    assert page.locator(ROW).count() == 3 and is_grouped(page)
    assert_grid_valid(page)
    assert_geometry(page)
    page.locator(TOGGLE).nth(0).click()
    assert_grid_valid(page)
    assert_geometry(page)
    # the cell widths line up: every panel row starts at the Pain Point column, under the three merged cells
    page.locator("[data-testid=pri-sel-only]").click()
    assert page.locator(ROW).count() == 30
    assert_grid_valid(page)


def test_row_actions_still_work_through_merged_cells(page):
    goto(page)
    first = page.locator(ROW).first
    first.locator("td.pri-c-cluster").click()                                                         # a click on a merged group cell does not open one of its rows
    assert page.locator("[data-testid=pri-body] tr.pri-detail:not([hidden])").count() == 0
    first.locator(".pri-c-pain").click()                                                              # a click on the row itself does
    assert page.locator("[data-testid=pri-body] tr.pri-detail:not([hidden])").count() == 1
    assert_grid_valid(page)
    first.locator(TOGGLE.replace("[data-testid=pri-body] ", "")).click()
    assert page.locator("[data-testid=pri-body] tr.pri-detail:not([hidden])").count() == 0
    allbox = page.locator("[data-testid=pri-table] thead input[type=checkbox]")
    allbox.check()
    assert page.locator(PICK + ":checked").count() == 30 and page.locator("[data-testid=pri-sel-count]").text_content().strip() == "30 selected"


# ---------------------------------------------------------------------------------------------------------------- pinned bars
def _scroll_into_table(page, extra=700):
    page.evaluate("(x) => window.scrollTo(0, document.querySelector('[data-testid=pri-table]').getBoundingClientRect().top + window.scrollY + x)", extra)
    page.wait_for_timeout(250)


def test_selection_bar_and_column_header_stay_pinned_together_while_the_table_scrolls(page):
    goto(page)
    page.locator("[data-testid=pri-all-expand]").click()
    pick = page.locator(PICK)
    for i in (0, 7, 15):
        pick.nth(i).check()
    _scroll_into_table(page, 1500)
    topbar = page.evaluate("() => document.querySelector('#topbar').getBoundingClientRect().bottom")
    bar = page.evaluate("() => { const r = document.querySelector('[data-testid=pri-seltools]').getBoundingClientRect(); return [r.top, r.bottom]; }")
    head = page.evaluate("() => { const r = document.querySelector('[data-testid=pri-table] thead th').getBoundingClientRect(); return [r.top, r.bottom]; }")
    assert abs(bar[0] - topbar) <= 2, (bar, topbar)                                                    # the bar sits right under the site header, in view
    assert abs(head[0] - bar[1]) <= 12, (head, bar)                                                    # the column header directly under the bar (the bar's own bottom margin)
    assert head[0] >= bar[1] - 1 and head[0] < bar[1] + 14
    assert page.evaluate("() => document.querySelector('[data-testid=pri-table] tbody').getBoundingClientRect().top < 0")      # and the table really did scroll under them
    # nothing of the rows shows through above or between: the element at the bar's centre is the bar, at the header's centre is the header
    assert page.evaluate("() => { const b = document.querySelector('[data-testid=pri-seltools]').getBoundingClientRect(); const e = document.elementFromPoint(b.left + 40, b.top + b.height / 2); return !!e.closest('[data-testid=pri-seltools]'); }")
    assert page.evaluate("() => { const h = document.querySelector('[data-testid=pri-table] thead th:nth-child(3)').getBoundingClientRect(); const e = document.elementFromPoint(h.left + 6, h.top + h.height / 2); return !!e.closest('thead'); }")
    page.screenshot(path=str(SHOTS / "priorities_sticky.png"))
    # the header sticks on its own when nothing is selected, directly under the site header
    page.locator("[data-testid=pri-sel-clear]").click()
    page.wait_for_timeout(200)
    assert page.locator(TOOLS).is_hidden()
    h2 = page.evaluate("() => document.querySelector('[data-testid=pri-table] thead th').getBoundingClientRect().top")
    assert abs(h2 - topbar) <= 2, (h2, topbar)


def test_count_is_large_and_bold_with_a_per_product_split_that_updates(page):
    goto(page)
    assert page.locator(TOOLS).is_hidden()                                                             # only with at least one row selected
    pick = page.locator(PICK)
    prods = page.locator(ROW).evaluate_all("rows => rows.map(r => r.dataset.product)")
    vb = [i for i, p in enumerate(prods) if p == "vita-bull"][:3]
    bl = [i for i, p in enumerate(prods) if p == "beef-liver"][:2]
    bf = [i for i, p in enumerate(prods) if p == "boneflex"][:2]
    for i in vb + bl + bf:
        pick.nth(i).check()
    count = page.locator("[data-testid=pri-sel-count]")
    assert count.text_content().strip() == "7 selected"
    assert count.evaluate("e => [parseFloat(getComputedStyle(e).fontSize), parseInt(getComputedStyle(e).fontWeight)]")[0] >= 16 and count.evaluate("e => parseInt(getComputedStyle(e).fontWeight)") >= 700
    assert page.locator("[data-testid=pri-sel-split]").text_content().strip() == "Vita Bull 3 · Beef Liver 2 · Boneflex 2"
    assert count.evaluate("e => (e.closest('[aria-live]') || e).getAttribute('aria-live')") == "polite"
    pick.nth(vb[0]).uncheck()
    pick.nth(bl[0]).uncheck()
    pick.nth(bl[1]).uncheck()
    assert count.text_content().strip() == "4 selected" and page.locator("[data-testid=pri-sel-split]").text_content().strip() == "Vita Bull 2 · Boneflex 2"      # a product with none left drops out
    page.locator("[data-testid=pri-f-product-vita-bull]").click()                                      # a filter hiding ticked rows does not un-tick them: the count and split stay
    assert count.text_content().strip() == "4 selected" and "Vita Bull 2" in page.locator("[data-testid=pri-sel-split]").text_content()
    page.locator("[data-testid=pri-f-product-vita-bull]").click()
    page.locator("[data-testid=pri-sel-clear]").click()
    assert page.locator(TOOLS).is_hidden()
    for label in ("Download CSV", "Print / PDF", "Copy as text", "Show selected only", "Save view", "Clear"):
        pick.nth(0).check()
        assert label in page.locator(TOOLS).text_content()
        pick.nth(0).uncheck()


# ---------------------------------------------------------------------------------------------------------------- phone
def test_phone_375_pins_only_a_compact_bar_and_shows_group_heading_bars_without_sideways_scroll(browser, base_url):
    c = browser.new_context(viewport={"width": 375, "height": 812}, base_url=base_url)
    c.request.post(base_url + "/api/login", data={"username": "JoshHoughton", "password": "test"})
    p = c.new_page()
    try:
        goto(p)
        assert is_grouped(p)
        assert not p.evaluate("() => document.documentElement.scrollWidth > innerWidth")
        # group labels: only the first card of a group carries them, as coloured heading bars above its checkbox; the others show only their own cells
        cards = p.evaluate("""() => [...document.querySelectorAll('[data-testid=pri-body] tr.pri-row')].slice(0, 3).map(r => ({
          shown: ['product', 'cluster', 'icp'].map(k => getComputedStyle(r.querySelector('.pri-c-' + k)).display !== 'none'),
          order: ['product', 'cluster', 'icp', 'tog'].map(k => r.querySelector('.pri-c-' + k).getBoundingClientRect().top) }))""")
        assert cards[0]["shown"] == [True, True, True] and cards[1]["shown"] == [False, False, False]
        o = cards[0]["order"]
        assert o[0] < o[1] < o[2] < o[3]                                                                  # product bar, cluster bar, ICP bar, then the card
        p.locator(PICK).nth(1).check()
        p.locator(PICK).nth(9).check()
        _scroll_into_table(p, 900)
        bar = p.evaluate("() => { const r = document.querySelector('[data-testid=pri-seltools]').getBoundingClientRect(); return [r.top, r.height]; }")
        assert bar[0] <= 2 and bar[1] < 70                                                                 # pinned at the very top (the site header scrolls away on a phone), one compact line
        assert p.locator("[data-testid=pri-sel-count]").text_content().strip() == "2 selected"
        assert p.locator("[data-testid=pri-sel-more]").is_visible() and not p.locator("[data-testid=pri-sel-print]").is_visible()
        p.locator("[data-testid=pri-sel-more]").click()                                                    # the buttons sit behind an Actions toggle
        assert p.locator("[data-testid=pri-sel-print]").is_visible() and p.locator("[data-testid=pri-sel-clear]").is_visible()
        assert not p.evaluate("() => document.documentElement.scrollWidth > innerWidth")
        p.locator("[data-testid=pri-sel-more]").click()
        # the column header is not pinned on a phone (it is not drawn)
        assert p.evaluate("() => getComputedStyle(document.querySelector('[data-testid=pri-table] thead')).position") == "absolute"
        p.screenshot(path=str(SHOTS / "priorities_375_grouped.png"))
    finally:
        c.close()


# ---------------------------------------------------------------------------------------------------------------- print
def test_print_keeps_the_flat_one_sheeter_and_drops_the_screen_table(page):
    goto(page)
    page.emulate_media(media="print")
    assert not page.locator(TABLE).is_visible()
    blocks = page.locator("[data-testid=pri-print-copy] table.pri-ptable")
    assert blocks.count() == 3
    assert page.locator("[data-testid=pri-print-copy] td.pri-merged").count() == 0 and page.locator("[data-testid=pri-print-copy] [rowspan]").count() == 0
    cl = page.locator("[data-testid=pri-print-copy] tbody.pri-rg tr.pri-row").first.locator(".pri-c-cluster").text_content()
    assert cl.strip()                                                                                  # every printed row names its own cluster: nothing relies on a merged cell
    page.emulate_media(media="screen")


def test_the_public_share_page_is_grouped_too_and_its_merges_are_valid(page):
    goto(page)
    page.locator(PICK).nth(1).check()
    page.locator(PICK).nth(2).check()
    page.locator(PICK).nth(3).check()
    page.locator(PICK).nth(10).check()
    page.locator("[data-testid=pri-sel-save]").click()
    page.locator("[data-testid=pri-name-dialog]").wait_for()
    page.locator("[data-testid=pri-view-name]").fill("Grouped share")
    page.keyboard.press("Enter")
    page.wait_for_selector("[data-testid=pri-view-link]")
    url = page.locator("[data-testid=pri-view-link]").get_attribute("href")
    page.goto(url)
    page.wait_for_selector("[data-testid=pri-body]")
    idle(page)
    assert is_grouped(page) and page.locator(ROW).count() == 4 and page.locator("[data-testid=pri-body] td.pri-merged").count() >= 3
    assert_grid_valid(page)
    assert_geometry(page)
    page.locator(TOGGLE).nth(1).click()
    assert_grid_valid(page)
    assert_geometry(page)
