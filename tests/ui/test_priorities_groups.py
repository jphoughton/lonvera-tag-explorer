"""Creative priorities (/priorities): the grouped hierarchy (product > market cluster > ICP > pain point as merged cells) and the sticky selection bar + column header.

Against the mock server (tests/ui/mock_priorities.py + priorities_fixture.json): 30 concepts, the fixture carries `sheet_pos` (the Communication Overview position of each line) in an order that differs from the
Action order on purpose, plus off-sheet rows (new ideas) inside an on-sheet ICP, inside an on-sheet cluster and in a cluster of their own.
"""
import pytest

pytestmark = pytest.mark.mock_only

ROW = "[data-testid=pri-card]"
PICK = "[data-testid=pri-body] [data-testid=pri-pick]"
TOOLS = "[data-testid=pri-seltools]"
TABLE = "[data-testid=pri-table]"
TOPBAR_H = 76          # the sticky app header is two rows: top bar 42 + section tabs 34 (--header-h)

# the sheet order of the fixture: product > cluster > ICP; off-sheet rows come after the on-sheet rows of their ICP / cluster / product
EXPECTED = {
    "vita-bull": [("TRT & Men's Clinics", ["Natural Testosterone Support", "Peptide Clinic Shopper"]), ("ED / Performance", ["Bedroom Upgrade Seeker"]), ("Muscle & Strength", ["Physique / Body Composition", "Cutting Gym Regular"]),
                  ("Prostate / Urinary", ["The Night Waker"]), ("GLP-1 Companion", ["Men on GLP-1"])],
    "beef-liver": [("Energy & Multivitamin", ["The Wired-Tired", "AG1 Defector"]), ("Ancestral Nutrition", ["Primal Living", "Curious Beginner", "Label Reader"]), ("Collagen", ["One-Scoop Everything Buyer"]),
                   ("Alcohol & Liver Support", ["BL1 Weekend Face"]), ("Weight Loss & Belly Fat", ["BL2 The Stubborn Gut"]), ("Thyroid", ["Thyroid Suspect"]), ("Fertility & Postpartum", ["Mom Feeding Kids Whole Foods"])],
    "boneflex": [("Glucosamine & Chondroitin", ["BF3 Tried Everything", "Horse-Pill Quitter", "Label Reader", "Women's Bone-Marrow Joint Buyer"]), ("Pain-Relief Alternatives", ["Pills Off The Table"]),
                 ("Pickleball & Active Aging", ["BF1 Won't Sit It Out", "Ex-Runner Longevity"]), ("Bone Broth & Ancestral Nutrition", ["Primal Living"]), ("GLP-1 Companion", ["Spanish-speaking joint buyer"])],
}


def idle(page):
    page.wait_for_function("() => !document.querySelector('#view').hasAttribute('aria-busy')")


def goto(page, qs=""):
    page.goto("/priorities" + qs)
    page.wait_for_selector("[data-testid=pri-page], [data-testid=pri-empty]")
    idle(page)


def tuples(page):
    return page.locator("[data-testid=pri-body] tr.pri-row").evaluate_all("rows => rows.map(r => [r.dataset.product, r.querySelector('.pri-c-cluster').textContent.trim(), r.querySelector('.pri-c-icp').textContent.trim()])")


def is_grouped(page):
    return page.locator(TABLE).evaluate("e => e.classList.contains('pri-grouped')")


def group_geometry(page):
    """For every merged cell on screen: [level, top offset from its first row, bottom offset from its last row, rowSpan, rows really covered (visible rows incl. open detail rows)]."""
    return page.evaluate("""() => {
      const trs = [...document.querySelector('[data-testid=pri-body]').children].filter((t) => getComputedStyle(t).display !== 'none' && !t.classList.contains('pri-prodhead'));      // product heading rows are not part of any merged cell
      const lv = { cluster: ['pri-c-cluster', ['pri-g-prod', 'pri-g-cl']], icp: ['pri-c-icp', ['pri-g-prod', 'pri-g-cl', 'pri-g-icp']] };
      const out = [];
      for (const [name, [c, stop]] of Object.entries(lv)) {
        trs.forEach((tr, i) => {
          const td = tr.querySelector('td.' + c + '.pri-merged');
          if (!td || getComputedStyle(td).display === 'none') return;
          let j = i + 1; while (j < trs.length && !stop.some((s) => trs[j].classList.contains(s))) j++;
          const r = td.getBoundingClientRect(), f = tr.getBoundingClientRect(), l = trs[j - 1].getBoundingClientRect();
          out.push([name, Math.round(r.top - f.top), Math.round(r.bottom - l.bottom), td.rowSpan, j - i]);
        });
      }
      return out;
    }""")


def assert_merges_correct(page):
    g = group_geometry(page)
    assert g, "no merged cells"
    for lvl, top, bottom, span, rows in g:
        assert top == 0 and abs(bottom) <= 1, (lvl, top, bottom)                  # the merged cell starts at its first row and ends at the bottom of its last row
        assert span == rows, (lvl, span, rows)
    # a visible, un-merged Cluster / ICP cell never exists in grouped mode, and no Product cell is visible at all (the product is a heading row)
    stray = page.evaluate("""() => [...document.querySelectorAll('[data-testid=pri-body] tr.pri-row td.pri-c-product, [data-testid=pri-body] tr.pri-row td.pri-c-cluster, [data-testid=pri-body] tr.pri-row td.pri-c-icp')]
      .filter((td) => getComputedStyle(td).display !== 'none' && !td.classList.contains('pri-merged')).length""")
    assert stray == 0
    assert page.evaluate("() => [...document.querySelectorAll('[data-testid=pri-body] td.pri-c-product')].every((td) => getComputedStyle(td).display === 'none' && !td.classList.contains('pri-merged'))")
    # every visible cell lies inside the table (a detail cell in the wrong grid slot would push past its right edge)
    assert page.evaluate("""() => { const t = document.querySelector('[data-testid=pri-table]'), r = t.getBoundingClientRect().right + 1;
      return [...t.querySelectorAll('tbody td')].filter((td) => getComputedStyle(td).display !== 'none').every((td) => td.getBoundingClientRect().right <= r); }""")
    return g


# ------------------------------------------------------------------------------------------------------------------------------------------ grouped order and merged cells


def test_default_is_grouped_in_sheet_order_with_off_sheet_rows_after_their_group(page):
    goto(page)
    assert is_grouped(page) and page.locator("[data-testid=pri-group-btn]").get_attribute("aria-pressed") == "true"
    t = tuples(page)
    assert len(t) == 30
    for slug, clusters in EXPECTED.items():
        mine = [(c, i) for p, c, i in t if p == slug]
        want = [(c, i) for c, icps in clusters for i in icps]
        seq = [x for n, x in enumerate(mine) if n == 0 or mine[n - 1] != x]                          # collapse repeats: rows of one ICP sit together
        assert seq == want, (slug, seq)
    assert [p for p, _, _ in t] == sorted([p for p, _, _ in t], key=["vita-bull", "beef-liver", "boneflex"].index)
    # inside one pain point group the Action order is kept; inside Peptide Clinic Shopper the off-sheet new idea is last
    peptide = page.locator("[data-testid=pri-body] tr.pri-row").evaluate_all(
        "rows => rows.filter((r) => r.dataset.product === 'vita-bull' && r.querySelector('.pri-c-icp').textContent.trim() === 'Peptide Clinic Shopper').map((r) => [r.querySelector('.pri-c-pain').textContent.trim(), r.querySelector('[data-testid=pri-source]') ? 1 : 0])")
    assert [s for _, s in peptide] == [0, 0, 0, 1] and peptide[-1][0] == "Weight Gain / Stubborn Belly Fat"


def test_product_cluster_and_icp_are_real_merged_cells_centered_with_cluster_colours(page):
    goto(page)
    g = assert_merges_correct(page)
    by = {lvl: [x for x in g if x[0] == lvl] for lvl in ("cluster", "icp")}
    assert len(by["cluster"]) == sum(len(v) for v in EXPECTED.values()) and len(by["icp"]) == sum(len(i) for v in EXPECTED.values() for _, i in v)
    assert any(x[3] > 1 for x in by["cluster"]) and any(x[3] > 1 for x in by["icp"])      # rowspan > 1 where a group has several rows
    cell = page.locator("[data-testid=pri-body] td.pri-c-cluster.pri-merged").first
    st = cell.evaluate("e => { const s = getComputedStyle(e); return [s.textAlign, s.verticalAlign]; }")
    assert st == ["center", "top"]      # the label itself is centred by a margin and then sticks (see the sticky-label test)
    # no blank cells; the tbody holds row, detail and product-heading rows only; headings are not rows (no checkbox, not counted)
    assert page.locator("[data-testid=pri-body] > tr").evaluate_all("trs => trs.every((t) => t.classList.contains('pri-row') || t.classList.contains('pri-detail') || t.classList.contains('pri-prodhead'))")
    assert page.locator("[data-testid=pri-card]").count() == 30 and page.locator(PICK).count() == 30
    assert page.locator("[data-testid=pri-body] td.pri-merged:empty").count() == 0
    # fills: cluster cell strongest, ICP lighter, ordinary cell faintest (lightness sum of the rgb channels)
    lum = page.evaluate("""() => {
      const p = (e) => getComputedStyle(e).backgroundColor.match(/[\\d.]+/g).slice(0, 3).map(Number).reduce((a, b) => a + b, 0);
      const tr = document.querySelector('[data-testid=pri-body] tr.pri-row.pri-g-icp.pri-g-cl');
      return [p(tr.querySelector('td.pri-c-cluster')), p(tr.querySelector('td.pri-c-icp')), p(tr)];      // the ordinary cells are transparent on a white row
    }""")
    assert lum[0] < lum[1] < lum[2]
    # separators: heavier between clusters than between ICPs
    sh = page.evaluate("""() => { const f = (s) => getComputedStyle(document.querySelector('[data-testid=pri-body] tr.pri-row.' + s + ' td.pri-c-pain')).boxShadow;
      return [f('pri-g-cl:not(.pri-g-prod)'), f('pri-g-icp:not(.pri-g-cl)')]; }""")
    assert "3px" in sh[0] and "1px" in sh[1]


def test_same_cluster_name_has_the_same_colour_under_every_product(page):
    goto(page)
    cols = page.evaluate("""() => { const o = {}; document.querySelectorAll('[data-testid=pri-body] td.pri-c-cluster.pri-merged').forEach((td) => {
        const tr = td.parentElement; (o[td.textContent.trim()] = o[td.textContent.trim()] || []).push([tr.dataset.product, getComputedStyle(td).backgroundColor, tr.dataset.clusterHue]); }); return o; }""")
    glp = cols["GLP-1 Companion"]
    assert {p for p, _, _ in glp} == {"vita-bull", "boneflex"} and len({c for _, c, _ in glp}) == 1 and len({h for _, _, h in glp}) == 1
    assert len({v[0][1] for v in cols.values()}) >= 8                                      # many different clusters get different colours (16 hues; hash collisions are possible, not constant colour)
    goto(page)                                                                             # and stable across visits
    again = page.evaluate("() => [...document.querySelectorAll('[data-testid=pri-body] td.pri-c-cluster.pri-merged')].map((td) => getComputedStyle(td).backgroundColor)")
    first = [c for v in cols.values() for _, c, _ in v]
    assert sorted(again) == sorted(first)


def test_merges_stay_correct_after_search_filter_selected_only_and_expand(page):
    goto(page)
    assert_merges_correct(page)
    # search: the merged cells are recomputed over the visible rows
    page.locator("[data-testid=pri-search]").fill("Label")
    assert is_grouped(page) and page.locator(PICK).count() == 2
    g = assert_merges_correct(page)
    assert [x for x in g if x[0] == "cluster"] and all(x[3] == x[4] for x in g)
    page.locator("[data-testid=pri-search]").fill("")
    # a product chip (filter): only boneflex, still grouped, merges recomputed
    page.locator("[data-testid=pri-f-product-boneflex]").click()
    assert is_grouped(page) and page.locator(PICK).count() == 10 and page.locator("[data-testid=pri-prodhead]").count() == 1 and assert_merges_correct(page)
    page.locator("[data-testid=pri-clear-filters]").click()
    # expand one row in the middle of a cluster: its detail sits inside the merged cells, which grow with it
    toggles = page.locator("[data-testid=pri-body] [data-testid=pri-toggle]")
    before = {x[0] + str(i): x[3] for i, x in enumerate(group_geometry(page))}
    toggles.nth(2).click()
    g = assert_merges_correct(page)
    assert sum(x[3] for x in g if x[0] == "cluster") == sum(v for k, v in before.items() if k.startswith("cluster")) + 1
    d = page.locator("[data-testid=pri-body] [data-testid=pri-detail]:visible").first
    assert d.is_visible() and d.locator(".pri-panel").count() == 1
    box = d.locator("xpath=./td[not(contains(@class,'pri-d-sp')) and not(@hidden)]").bounding_box()
    pain = page.locator("[data-testid=pri-body] tr.pri-row .pri-c-pain").nth(2).bounding_box()
    assert abs(box["x"] - pain["x"]) <= 1                                                  # the panel starts at the Pain Point column, right of the merged cells
    toggles.nth(2).click()
    assert_merges_correct(page)
    # Expand all / Collapse all
    page.locator("[data-testid=pri-all-expand]").click()
    assert page.locator("[data-testid=pri-body] [data-testid=pri-detail]:visible").count() == 30
    assert_merges_correct(page)
    page.locator("[data-testid=pri-all-collapse]").click()
    assert page.locator("[data-testid=pri-body] [data-testid=pri-detail]:visible").count() == 0
    assert_merges_correct(page)
    # Show selected only: three ticked rows from different groups
    for i in (0, 9, 20):
        page.locator(PICK).nth(i).check()
    page.locator("[data-testid=pri-sel-only]").click()
    assert page.locator(PICK).count() == 3 and is_grouped(page)
    assert_merges_correct(page)
    page.locator("[data-testid=pri-body] [data-testid=pri-toggle]").nth(1).click()          # expanding inside the selected-only list
    assert_merges_correct(page)
    page.locator("[data-testid=pri-sel-only]").click()
    assert page.locator(PICK).count() == 30 and is_grouped(page)
    assert_merges_correct(page)


def test_product_is_a_heading_row_with_a_count_not_a_merged_cell(page):
    goto(page)
    heads = page.locator("[data-testid=pri-prodhead]")
    assert heads.count() == 3 and page.locator("td.pri-c-product.pri-merged").count() == 0
    per = page.locator("[data-testid=pri-card]").evaluate_all("rows => rows.reduce((o, r) => { o[r.dataset.product] = (o[r.dataset.product] || 0) + 1; return o; }, {})")
    texts = heads.evaluate_all("hs => hs.map((h) => [h.dataset.product, h.textContent.trim(), h.querySelectorAll('input, button').length, h.nextElementSibling.classList.contains('pri-row')])")
    assert [t[0] for t in texts] == ["vita-bull", "beef-liver", "boneflex"]
    for slug, text, controls, next_is_row in texts:
        assert text.endswith(f"· {per[slug]} concepts") and controls == 0 and next_is_row, (slug, text)
    # full table width, heaviest rule above, plain background, bold name
    st = heads.first.evaluate("""h => { const t = h.closest('table').getBoundingClientRect(), td = h.firstElementChild.getBoundingClientRect(), s = getComputedStyle(h.firstElementChild);
      return [Math.abs(td.left - t.left) <= 3 && Math.abs(td.right - t.right) <= 3, s.borderTopWidth, s.backgroundColor, getComputedStyle(h.querySelector('strong')).fontWeight]; }""")
    assert st[0] and st[1] == "4px" and st[2] == "rgba(0, 0, 0, 0)" and int(st[3]) >= 700, st
    # no Product column header in the grouped view; a filter changes the count
    assert page.locator("th.pri-th-product").evaluate("e => getComputedStyle(e).display") == "none"
    page.locator("[data-testid=pri-search]").fill("Label")
    assert page.locator("[data-testid=pri-prodhead]").evaluate_all("hs => hs.map((h) => h.textContent.trim())") == ["Beef Liver · 1 concept", "Boneflex · 1 concept"]
    page.locator("[data-testid=pri-search]").fill("")
    # white rows: the checkbox cell and the cells from Pain Point on carry no cluster tint, only the cluster and ICP cells are coloured
    plain = page.evaluate("""() => [...document.querySelectorAll('[data-testid=pri-body] tr.pri-row')].every((tr) => {
      const bg = (e) => getComputedStyle(e).backgroundColor, t = tr.querySelector('.pri-c-tog');
      return (bg(t) === 'rgba(0, 0, 0, 0)' || bg(t) === bg(tr)) && bg(tr) === 'rgb(255, 255, 255)' && bg(tr.querySelector('.pri-c-pain')) === 'rgba(0, 0, 0, 0)'; })""")
    assert plain
    # flat (sorted) view: no headings, the Product column is a normal column again
    page.locator("[data-testid=pri-sort-pain]").click()
    assert page.locator("[data-testid=pri-prodhead]").count() == 0
    assert page.locator("th.pri-th-product").evaluate("e => getComputedStyle(e).display") != "none"
    assert page.locator("[data-testid=pri-card] .pri-c-product").evaluate_all("cs => cs.length > 0 && cs.every((c) => getComputedStyle(c).display !== 'none' && c.textContent.trim())")


def test_rows_are_white_in_every_state_open_hover_selected(page):
    goto(page)
    for i in (0, 4, 9):
        page.locator(PICK).nth(i).check()
    page.locator("[data-testid=pri-body] [data-testid=pri-toggle]").nth(5).click()
    row = page.locator("[data-testid=pri-body] tr.pri-row").nth(5)
    row.hover()
    bgs = page.evaluate("""() => [...document.querySelectorAll('[data-testid=pri-body] tr.pri-row')].map((tr) => [tr.classList.contains('pri-open'), getComputedStyle(tr).backgroundColor,
      [...tr.children].filter((td) => !td.classList.contains('pri-merged') && getComputedStyle(td).display !== 'none').map((td) => getComputedStyle(td).backgroundColor)])""")
    assert any(b[0] for b in bgs)
    for is_open, tr_bg, cells in bgs:
        assert tr_bg == "rgb(255, 255, 255)" and all(c in ("rgba(0, 0, 0, 0)", "rgb(255, 255, 255)") or "gradient" in c for c in cells), (is_open, tr_bg, cells)
    # the open row is still marked, neutrally (a rule beside its checkbox, no cluster colour)
    assert "gradient" in page.locator("[data-testid=pri-body] tr.pri-row.pri-open td.pri-c-tog").first.evaluate("e => getComputedStyle(e).backgroundImage")


@pytest.mark.parametrize("w", [1280, 1440, 1700, 1920])
def test_selection_bar_header_and_table_share_left_and_right_edges(browser, base_url, w):
    c = browser.new_context(viewport={"width": w, "height": 900}, base_url=base_url)
    c.request.post(base_url + "/api/login", data={"username": "JoshHoughton", "password": "test"})
    p = c.new_page()
    p.goto("/priorities")
    p.wait_for_selector("[data-testid=pri-page]")
    idle(p)
    p.locator(PICK).nth(0).check()
    e = p.evaluate("""() => { const r = (s) => { const b = document.querySelector(s).getBoundingClientRect(); return [b.left, b.right]; };
      return [r('[data-testid=pri-seltools]'), r('[data-testid=pri-table]'), r('[data-testid=pri-table] thead tr')]; }""")
    for a in e[1:]:
        assert abs(a[0] - e[0][0]) <= 2 and abs(a[1] - e[0][1]) <= 2, (w, e)
    c.close()


def test_merged_labels_are_sticky_and_stay_in_view_inside_a_tall_group(page):
    goto(page)
    page.locator("[data-testid=pri-all-expand]").click()                                      # every group is now tall
    lbl = page.locator("[data-testid=pri-body] td.pri-c-cluster.pri-merged > .pri-lbl").first
    assert lbl.evaluate("e => getComputedStyle(e).position") == "sticky"
    assert page.locator("[data-testid=pri-body] td.pri-c-icp.pri-merged > .pri-lbl").first.evaluate("e => getComputedStyle(e).position") == "sticky"
    # a short group keeps its label centred in the cell
    page.locator("[data-testid=pri-all-collapse]").click()
    cen = page.evaluate("""() => { const td = [...document.querySelectorAll('[data-testid=pri-body] td.pri-c-cluster.pri-merged')].find((t) => t.rowSpan > 1); const l = td.firstElementChild, a = td.getBoundingClientRect(), b = l.getBoundingClientRect();
      return Math.abs((b.top + b.bottom) / 2 - (a.top + a.bottom) / 2); }""")
    assert cen <= 4, cen
    page.locator("[data-testid=pri-all-expand]").click()
    page.evaluate("() => document.querySelector('[data-testid=pri-table]').scrollIntoView()")
    for dy in (250, 500, 900):
        page.evaluate("(d) => window.scrollBy(0, d)", dy)
        res = page.evaluate("""() => { const hb = document.querySelector('[data-testid=pri-table] thead tr').getBoundingClientRect().bottom;
          return [...document.querySelectorAll('[data-testid=pri-body] td.pri-c-cluster.pri-merged')].map((td) => { const a = td.getBoundingClientRect(), b = td.firstElementChild.getBoundingClientRect();
            const shown = a.bottom > hb + 40 && a.top < innerHeight - 40;                        // a cell that is well on screen
            return [shown, b.top >= hb - 1 && b.bottom <= innerHeight + 1 && b.bottom <= a.bottom + 1]; }); }""")
        assert all(ok for shown, ok in res if shown), (dy, res)
        assert any(shown for shown, _ in res)


def test_clicking_a_merged_cell_does_not_open_a_row_but_a_normal_cell_still_does(page):
    goto(page)
    page.locator("[data-testid=pri-body] td.pri-c-cluster.pri-merged").first.click()
    assert page.locator("[data-testid=pri-body] [data-testid=pri-detail]:visible").count() == 0
    page.locator("[data-testid=pri-body] tr.pri-row .pri-c-story").first.click()
    assert page.locator("[data-testid=pri-body] [data-testid=pri-detail]:visible").count() == 1


def test_sort_turns_merges_off_and_group_by_cluster_restores_them(page):
    goto(page)
    page.locator("[data-testid=pri-sort-pain]").click()
    assert not is_grouped(page) and page.locator("[data-testid=pri-group-btn]").get_attribute("aria-pressed") == "false"
    assert page.locator("[data-testid=pri-body] td.pri-merged").count() == 0 and page.locator("[data-testid=pri-body] td.pri-hid").count() == 0
    assert page.locator("[data-testid=pri-body] tr.pri-row").evaluate_all("rows => rows.every((r) => ['.pri-c-product', '.pri-c-cluster', '.pri-c-icp'].every((s) => r.querySelector(s).offsetParent !== null && r.querySelector(s).textContent.trim()))")
    pains = page.locator("[data-testid=pri-body] .pri-c-pain").all_text_contents()
    assert pains == sorted(pains, key=str.lower)
    page.locator("[data-testid=pri-body] [data-testid=pri-toggle]").nth(3).click()          # a detail row in plain mode spans the whole table again
    box = page.locator("[data-testid=pri-body] [data-testid=pri-detail]:visible > td:not([hidden])").bounding_box()
    assert box["width"] > page.locator(TABLE).bounding_box()["width"] * 0.9
    page.locator("[data-testid=pri-group-btn]").click()
    assert is_grouped(page) and page.locator("[data-testid=pri-group-btn]").get_attribute("aria-pressed") == "true"
    assert all(th.get_attribute("aria-sort") == "none" for th in page.locator("[data-testid=pri-table] thead th[aria-sort]").all())
    assert_merges_correct(page)
    t = tuples(page)
    assert [x[1] for x in t][:1] == ["TRT & Men's Clinics"]
    page.locator("[data-testid=pri-sort-cluster]").click()                                  # another sort and back
    assert not is_grouped(page)
    page.locator("[data-testid=pri-group-btn]").click()
    assert is_grouped(page)


def test_action_header_gives_the_old_order_without_merges(page):
    goto(page)
    page.locator("[data-testid=pri-sort-section]").click()
    secs = page.locator("[data-testid=pri-body] tr.pri-row").evaluate_all("rows => rows.map((r) => r.dataset.section)")
    assert secs[0] == "revive" and secs[-1] == "stop" and not is_grouped(page)


def _save_filters_view(page, name):
    page.locator("[data-testid=pri-save-open]").click()
    page.locator("[data-testid=pri-name-dialog]").wait_for()
    page.locator("[data-testid=pri-kind-filters]").check()
    page.locator("[data-testid=pri-view-name]").fill(name)
    page.keyboard.press("Enter")
    page.wait_for_selector("[data-testid=pri-view-link]")
    return page.locator("[data-testid=pri-view-link]").get_attribute("href").rsplit("/", 1)[1]


def test_saved_view_with_a_sort_opens_ungrouped_and_one_without_opens_grouped(page):
    goto(page)
    page.locator("[data-testid=pri-sort-pain]").click()
    sorted_tok = _save_filters_view(page, "Sorted by pain")
    goto(page)
    assert is_grouped(page)                                                                    # a fresh page is grouped again
    plain_tok = _save_filters_view(page, "Grouped view")
    goto(page, f"?view={sorted_tok}")
    assert not is_grouped(page) and page.locator("[data-testid=pri-sort-pain]").locator("xpath=ancestor::th").get_attribute("aria-sort") == "ascending"
    page.locator("[data-testid=pri-group-btn]").click()                                        # Group by cluster clears the saved sort on screen
    assert is_grouped(page)
    goto(page, f"?view={plain_tok}")
    assert is_grouped(page) and page.locator("[data-testid=pri-group-btn]").get_attribute("aria-pressed") == "true"
    assert_merges_correct(page)
    page.evaluate("() => localStorage.removeItem('pri-last-view')")


# ------------------------------------------------------------------------------------------------------------------------------------------ sticky bars and the selection count


def rect(page, sel):
    return page.locator(sel).evaluate("e => { const r = e.getBoundingClientRect(); return { top: r.top, bottom: r.bottom, left: r.left, right: r.right, h: r.height }; }")


def test_header_pins_under_the_app_header_and_the_selection_bar_pins_with_the_header_directly_under_it(page):
    goto(page)
    th = "[data-testid=pri-table] thead th >> nth=2"
    page.evaluate("() => document.querySelector('[data-testid=pri-table]').scrollIntoView()")
    page.evaluate("() => window.scrollBy(0, 700)")
    h0 = rect(page, th)
    assert abs(h0["top"] - TOPBAR_H) <= 1                                                       # no bar yet: the header sits right under the app header
    for i in (0, 9, 20):
        page.locator(PICK).nth(i).check()
    page.evaluate("() => window.scrollBy(0, 900)")
    assert page.evaluate("() => scrollY") > 1000
    bar, h1, top = rect(page, TOOLS), rect(page, th), rect(page, "#topbar")
    assert abs(bar["top"] - top["bottom"]) <= 1 and bar["top"] >= TOPBAR_H - 1                  # the bar is under the app header, not behind it
    assert abs(h1["top"] - bar["bottom"]) <= 1 and h1["top"] >= bar["bottom"] - 1               # the column header is directly under the bar, no overlap, no gap
    assert page.locator("[data-testid=pri-sel-count]").is_visible() and page.locator("[data-testid=pri-sel-count]").text_content() == "3 selected"
    # every header cell is pinned at the same height and the table body is under them (a row near the top of the viewport never paints over the header)
    tops = page.locator("[data-testid=pri-table] thead th").evaluate_all("els => els.filter((e) => getComputedStyle(e).display !== 'none').map((e) => Math.round(e.getBoundingClientRect().top))")
    assert len(set(tops)) == 1
    hit = page.evaluate("(y) => { const e = document.elementFromPoint(300, y); return e ? !!e.closest('thead') : false; }", h1["top"] + 5)
    assert hit
    # the pinned bar still works while scrolled far down
    page.locator("[data-testid=pri-sel-only]").click()
    assert page.locator(PICK).count() == 3
    page.locator("[data-testid=pri-sel-clear]").click()
    page.evaluate("() => window.scrollBy(0, 400)")
    assert page.locator(TOOLS).is_hidden()
    assert abs(rect(page, th)["top"] - TOPBAR_H) <= 1                                           # bar gone: the header moves up under the app header


def test_bar_follows_its_real_height_when_the_actions_wrap(browser, base_url):
    c = browser.new_context(viewport={"width": 900, "height": 900}, base_url=base_url)
    c.request.post(base_url + "/api/login", data={"username": "JoshHoughton", "password": "test"})
    p = c.new_page()
    p.goto("/priorities")
    p.wait_for_selector("[data-testid=pri-page]")
    idle(p)
    for i in (0, 9, 20):
        p.locator(PICK).nth(i).check()
    p.evaluate("() => { document.querySelector('[data-testid=pri-table]').scrollIntoView(); window.scrollBy(0, 1200); }")
    bar, h = rect(p, TOOLS), rect(p, "[data-testid=pri-table] thead th >> nth=2")
    assert bar["h"] > 50 and abs(h["top"] - bar["bottom"]) <= 1                                 # a bar that wraps to two lines pushes the header down by exactly its height
    c.close()


def test_selection_count_shows_the_per_product_split_and_updates(page):
    goto(page)
    assert page.locator(TOOLS).is_hidden()
    prods = page.locator("[data-testid=pri-body] tr.pri-row").evaluate_all("rows => rows.map((r) => r.dataset.product)")
    vb, bl, bf = [i for i, p in enumerate(prods) if p == "vita-bull"], [i for i, p in enumerate(prods) if p == "beef-liver"], [i for i, p in enumerate(prods) if p == "boneflex"]
    for i in (vb[0], vb[3], bl[2]):
        page.locator(PICK).nth(i).check()
    assert page.locator("[data-testid=pri-sel-count]").text_content() == "3 selected"
    assert page.locator("[data-testid=pri-sel-split]").text_content() == "Vita Bull 2 · Beef Liver 1"
    assert page.locator("[data-testid=pri-sel-count]").evaluate("e => getComputedStyle(e).fontWeight") in ("700", "800", "bold")
    page.locator(PICK).nth(bf[0]).check()
    page.locator(PICK).nth(bf[1]).check()
    assert page.locator("[data-testid=pri-sel-split]").text_content() == "Vita Bull 2 · Beef Liver 1 · Boneflex 2"
    assert page.locator("[data-testid=pri-sel-count]").text_content() == "5 selected"
    page.locator(PICK).nth(vb[0]).uncheck()
    assert page.locator("[data-testid=pri-sel-split]").text_content() == "Vita Bull 1 · Beef Liver 1 · Boneflex 2"
    # the existing rule: the selection persists across filters, so a ticked row hidden by a filter still counts
    page.locator("[data-testid=pri-f-product-boneflex]").click()
    assert page.locator(PICK).count() == 10 and page.locator("[data-testid=pri-sel-count]").text_content() == "4 selected"
    assert page.locator("[data-testid=pri-sel-split]").text_content() == "Vita Bull 1 · Beef Liver 1 · Boneflex 2"
    page.locator("[data-testid=pri-clear-filters]").click()
    page.locator("[data-testid=pri-sel-clear]").click()
    assert page.locator(TOOLS).is_hidden() and page.locator("[data-testid=pri-sel-split]").text_content() == ""


# ------------------------------------------------------------------------------------------------------------------------------------------ phone widths


def test_375px_cards_keep_a_coloured_cluster_label_only_the_bar_pins_and_there_is_no_sideways_scroll(browser, base_url):
    c = browser.new_context(viewport={"width": 375, "height": 812}, base_url=base_url)
    c.request.post(base_url + "/api/login", data={"username": "JoshHoughton", "password": "test"})
    p = c.new_page()
    p.goto("/priorities")
    p.wait_for_selector("[data-testid=pri-page]")
    idle(p)
    assert not p.evaluate("() => document.documentElement.scrollWidth > innerWidth")
    # the stacked layout is kept: every card shows its own cluster / ICP (the product is a simple heading above its cards, not repeated in each card), the cluster as a coloured label
    cells = p.locator("[data-testid=pri-body] tr.pri-row").evaluate_all("""rows => rows.every((r) => ['.pri-c-cluster', '.pri-c-icp'].every((s) => r.querySelector(s).offsetParent !== null)
      && getComputedStyle(r.querySelector('.pri-c-cluster')).backgroundColor !== 'rgba(0, 0, 0, 0)')""")
    assert cells
    for i in (0, 9, 20):
        p.locator(PICK).nth(i).check()
    assert not p.evaluate("() => document.documentElement.scrollWidth > innerWidth")
    p.evaluate("() => { document.querySelector('[data-testid=pri-table]').scrollIntoView(); window.scrollBy(0, 1500); }")
    bar = rect(p, TOOLS)
    assert abs(bar["top"]) <= 1 and bar["h"] < 130                                              # compact: count + split + More, pinned at the very top (the app header is not sticky on a phone)
    assert p.locator("[data-testid=pri-sel-count]").is_visible() and p.locator("[data-testid=pri-sel-split]").is_visible()
    assert p.locator("[data-testid=pri-selacts]").is_hidden() and p.locator("[data-testid=pri-sel-more]").is_visible()
    assert p.locator("[data-testid=pri-table] thead th").first.evaluate("e => getComputedStyle(e).position") == "static"        # the column header does not pin on a phone
    p.locator("[data-testid=pri-sel-more]").click()
    assert p.locator("[data-testid=pri-selacts]").is_visible() and p.locator("[data-testid=pri-sel-csv]").is_visible() and p.locator("[data-testid=pri-sel-save]").is_visible()
    assert p.locator("[data-testid=pri-sel-more]").get_attribute("aria-expanded") == "true"
    assert not p.evaluate("() => document.documentElement.scrollWidth > innerWidth")
    p.locator("[data-testid=pri-sel-more]").click()
    assert p.locator("[data-testid=pri-selacts]").is_hidden()
    c.close()


def test_760px_is_the_phone_layout_and_761px_is_the_table_with_merges(browser, base_url):
    for w, merged in ((760, False), (761, True)):
        c = browser.new_context(viewport={"width": w, "height": 900}, base_url=base_url)
        c.request.post(base_url + "/api/login", data={"username": "JoshHoughton", "password": "test"})
        p = c.new_page()
        p.goto("/priorities")
        p.wait_for_selector("[data-testid=pri-page]")
        idle(p)
        if w == 760:                                                                         # (the table has always been a little wider than the window from 761 px up; the phone layout must not scroll)
            assert not p.evaluate("() => document.documentElement.scrollWidth > innerWidth"), w
        assert (p.locator("[data-testid=pri-body] td.pri-merged").first.evaluate("e => getComputedStyle(e).display") == "table-cell") is merged
        if merged:
            assert_merges_correct(p)
        c.close()
