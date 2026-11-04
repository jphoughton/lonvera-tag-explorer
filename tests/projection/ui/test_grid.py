"""Shared grid (lib/grid.js) on the mock drivers page: projected styling, editing, paste, undo, keyboard, groups, FY, copy."""
import json

import pytest
from playwright.sync_api import expect

from conftest import SHOTS, cell, hook, mock_state, open_module

PROJ = 23          # Dec 2026: first projected month is Nov 2026 (index 22)
ACT = 5            # Jun 2025


def put_requests(base_url):
    return mock_state(base_url)["puts"]


def wait_puts(page, base_url, n, timeout=5000):
    waited = 0
    while len(put_requests(base_url)) < n:
        assert waited < timeout, f"expected {n} PUT requests, saw {len(put_requests(base_url))}"
        page.wait_for_timeout(50)
        waited += 50


def paste(page, selector, text):
    page.evaluate(
        """([sel, text]) => { const el = document.querySelector(sel); const dt = new DataTransfer(); dt.setData('text/plain', text);
             el.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true })); }""",
        [selector, text])


def test_projected_asterisk_and_header_tint(page):
    open_module(page)
    proj_txt = cell(page, "spend", PROJ).inner_text()
    act_txt = cell(page, "spend", ACT).inner_text()
    assert proj_txt.endswith("*") and proj_txt == "$350,000*"
    assert not act_txt.endswith("*")
    assert cell(page, "noi_margin", PROJ).inner_text().endswith("*")
    heads = page.locator("thead tr.pg-monthrow th.pg-mh")
    assert heads.count() == 60
    assert "s-projected" in heads.nth(PROJ).get_attribute("class") and "s-actual" in heads.nth(ACT).get_attribute("class")
    bg = lambda i: heads.nth(i).evaluate("e => getComputedStyle(e).backgroundColor")
    assert bg(PROJ) != bg(ACT)
    assert "pg-bound" in heads.nth(22).get_attribute("class")                   # boundary marker on the first projected month
    assert heads.nth(PROJ).get_attribute("title").endswith("(projected)")
    # inputs yellow, calc not
    yellow = lambda key: cell(page, key, ACT).evaluate("e => getComputedStyle(e).backgroundColor")
    assert yellow("spend") != yellow("nc_orders")
    assert "pg-in" in cell(page, "spend", ACT).get_attribute("class") and "pg-in" not in cell(page, "nc_orders", ACT).get_attribute("class")
    # row kinds
    assert page.locator('tr[data-key="total_rev"]').get_attribute("class").endswith("kind-total")
    assert page.locator('tr[data-key="noi_margin"] th.pg-label').evaluate("e => getComputedStyle(e).fontStyle") == "italic"
    # caption + year band
    expect(page.locator(".pg-caption")).to_have_text("Source: mock model (spend x CPA x AOV).")
    assert page.locator("tr.pg-yearrow th.pg-yh").count() == 5
    page.screenshot(path=str(SHOTS / "drivers-desktop.png"))


def test_edit_commit_sends_put_and_recomputes(page, base_url):
    open_module(page)
    before = cell(page, "nc_rev", PROJ).inner_text()
    cell(page, "spend", PROJ).click()
    editor = page.locator("td.pg-editing input.pg-editor")
    expect(editor).to_be_focused()
    assert editor.input_value() == "350000"
    page.keyboard.press("ControlOrMeta+a")
    page.keyboard.type("$400k")
    with page.expect_request(lambda r: r.method == "PUT" and r.url.endswith("/api/projection/series")) as req:
        page.keyboard.press("Enter")
    body = req.value.post_data_json
    assert body == {"scenario": "current", "items": [{"line_key": "spend", "month": "2026-12-01", "value": 400000, "kind": "input"}]}
    assert req.value.headers["content-type"] == "application/json"
    expect(page.locator(".pg-gstatus")).to_have_text("Saved 1 cell")
    assert cell(page, "spend", PROJ).inner_text() == "$400,000*"
    assert cell(page, "nc_rev", PROJ).inner_text() != before            # calculated row came back from the server
    expect(page.locator("#pg-version")).to_have_text("v2")
    # selection moved down one row (nc_cpa) after Enter
    expect(cell(page, "nc_cpa", PROJ)).to_have_class(__import__("re").compile(r"pg-sel"))


def test_escape_cancels_and_invalid_keeps_editor(page, base_url):
    open_module(page)
    cell(page, "spend", PROJ).click()
    page.keyboard.press("ControlOrMeta+a")
    page.keyboard.type("999")
    page.keyboard.press("Escape")
    assert cell(page, "spend", PROJ).inner_text() == "$350,000*"
    assert put_requests(base_url) == []
    cell(page, "spend", PROJ).click()
    page.keyboard.press("ControlOrMeta+a")
    page.keyboard.type("abc")
    page.keyboard.press("Enter")
    editor = page.locator("td.pg-editing input.pg-editor")
    expect(editor).to_have_class(__import__("re").compile("invalid"))
    expect(page.locator(".pg-gstatus")).to_have_text("Not a number")
    assert put_requests(base_url) == []
    page.keyboard.press("Escape")
    assert page.locator("td.pg-editing").count() == 0


def test_tab_commits_and_moves_right_and_blur_commits(page, base_url):
    open_module(page)
    cell(page, "spend", PROJ).click()
    page.keyboard.press("ControlOrMeta+a")
    page.keyboard.type("410000")
    page.keyboard.press("Tab")
    expect(cell(page, "spend", PROJ + 1)).to_have_class(__import__("re").compile("pg-sel"))
    wait_puts(page, base_url, 1)
    # clicking another cell commits the edit in progress
    cell(page, "spend", PROJ + 1).click()
    page.keyboard.press("ControlOrMeta+a")
    page.keyboard.type("420000")
    cell(page, "nc_cpa", PROJ + 2).click()
    wait_puts(page, base_url, 2)
    assert put_requests(base_url)[1]["items"][0]["value"] == 420000


def test_typing_starts_edit_and_delete_clears(page, base_url):
    open_module(page)
    cell(page, "nc_orders", PROJ).click()                         # calc cell: select only
    assert page.locator("td.pg-editing").count() == 0
    page.keyboard.press("ArrowUp")                                # nc_aov
    page.keyboard.press("ArrowUp")                                # nc_cpa
    page.keyboard.press("ArrowUp")                                # spend
    assert "pg-sel" in cell(page, "spend", PROJ).get_attribute("class")
    page.keyboard.type("5")
    editor = page.locator("td.pg-editing input.pg-editor")
    assert editor.input_value() == "5"
    page.keyboard.press("Escape")
    with page.expect_request(lambda r: r.method == "PUT") as req:
        page.keyboard.press("Delete")
    assert req.value.post_data_json["items"] == [{"line_key": "spend", "month": "2026-12-01", "value": None, "kind": "input"}]


def test_parse_variants_in_cells(page, base_url):
    open_module(page)
    for text, expect_v in [("1,234.5", 1234.5), ("(50)", -50), ("2m", 2000000), ("-7.25", -7.25)]:
        cell(page, "amazon_rev", PROJ).click()
        page.keyboard.press("ControlOrMeta+a")
        page.keyboard.type(text)
        with page.expect_request(lambda r: r.method == "PUT") as req:
            page.keyboard.press("Enter")
        assert req.value.post_data_json["items"][0]["value"] == pytest.approx(expect_v)
        page.wait_for_function("document.querySelector('.pg-gstatus').textContent.startsWith('Saved')")


def test_paste_row_of_values_across_months(page, base_url):
    open_module(page)
    cell(page, "spend", PROJ).click()
    page.keyboard.press("Escape")                                  # keep the selection, drop the editor
    with page.expect_request(lambda r: r.method == "PUT") as req:
        paste(page, "table.pg-table", "$100k\t$110k\t$120k\t$130k")
    items = req.value.post_data_json["items"]
    assert [(i["month"], i["value"]) for i in items] == [("2026-12-01", 100000), ("2027-01-01", 110000), ("2027-02-01", 120000), ("2027-03-01", 130000)]
    assert len(put_requests(base_url)) == 1                          # one batched request
    page.wait_for_function("document.querySelector('.pg-gstatus').textContent === 'Saved 4 cells'")
    assert cell(page, "spend", PROJ + 3).inner_text() == "$130,000*"


def test_paste_block_skips_calculated_rows_and_pastes_into_editor(page, base_url):
    open_module(page)
    cell(page, "spend", PROJ).click()
    page.keyboard.press("Escape")
    with page.expect_request(lambda r: r.method == "PUT") as req:
        paste(page, "table.pg-table", "1\t2\n3\t4\n5\t6")           # rows: spend, nc_cpa, nc_aov
    items = req.value.post_data_json["items"]
    assert sorted((i["line_key"], i["month"], i["value"]) for i in items) == sorted([
        ("spend", "2026-12-01", 1), ("spend", "2027-01-01", 2), ("nc_cpa", "2026-12-01", 3), ("nc_cpa", "2027-01-01", 4), ("nc_aov", "2026-12-01", 5), ("nc_aov", "2027-01-01", 6)])
    page.wait_for_function("document.querySelector('.pg-gstatus').textContent.startsWith('Saved')")
    # a multi-cell paste while the editor is open pastes into the grid instead
    cell(page, "amazon_rev", PROJ).click()
    with page.expect_request(lambda r: r.method == "PUT") as req2:
        paste(page, "td.pg-editing input.pg-editor", "10\t20")
    assert [i["value"] for i in req2.value.post_data_json["items"]] == [10, 20]


def test_paste_single_value_fills_range(page, base_url):
    open_module(page)
    cell(page, "spend", PROJ).click()
    page.keyboard.press("Escape")
    page.keyboard.press("Shift+ArrowRight")
    page.keyboard.press("Shift+ArrowRight")
    assert page.locator("td.pg-range").count() >= 2
    with page.expect_request(lambda r: r.method == "PUT") as req:
        paste(page, "table.pg-table", "250k")
    assert [i["value"] for i in req.value.post_data_json["items"]] == [250000, 250000, 250000]


def test_undo_restores_previous_value(page, base_url):
    open_module(page)
    assert page.locator("button:has-text('Undo')").is_disabled()
    cell(page, "spend", PROJ).click()
    page.keyboard.press("ControlOrMeta+a")
    page.keyboard.type("999k")
    page.keyboard.press("Enter")
    page.wait_for_function("document.querySelector('.pg-gstatus').textContent === 'Saved 1 cell'")
    assert cell(page, "spend", PROJ).inner_text() == "$999,000*"
    with page.expect_request(lambda r: r.method == "PUT") as req:
        page.click("button:has-text('Undo')")
    assert req.value.post_data_json["items"][0]["value"] == 350000
    expect(page.locator(".pg-gstatus")).to_have_text("Undone")
    assert cell(page, "spend", PROJ).inner_text() == "$350,000*"
    assert page.locator("button:has-text('Undo')").is_disabled()


@pytest.mark.allow_errors
def test_save_error_reverts_cell_and_shows_state(page, base_url):
    open_module(page)
    hook(base_url, "fail_next", {"n": 1})
    cell(page, "spend", PROJ).click()
    page.keyboard.press("ControlOrMeta+a")
    page.keyboard.type("777k")
    page.keyboard.press("Enter")
    expect(page.locator(".pg-gstatus")).to_have_text("Not saved: Series write failed")
    assert cell(page, "spend", PROJ).inner_text() == "$350,000*"
    assert "pg-error" in cell(page, "spend", PROJ).get_attribute("class")
    assert cell(page, "spend", PROJ).get_attribute("title") == "Series write failed"
    assert page.locator("button:has-text('Undo')").is_disabled()


def test_keyboard_navigation(page):
    open_module(page)
    cell(page, "nc_orders", 3).click()
    sel = lambda: page.evaluate("(() => { const e = document.querySelector('td.pg-sel'); return [e.parentElement.dataset.key, +e.dataset.c]; })()")
    assert sel() == ["nc_orders", 3]
    page.keyboard.press("ArrowRight"); assert sel() == ["nc_orders", 4]
    page.keyboard.press("ArrowDown"); assert sel() == ["nc_rev", 4]
    page.keyboard.press("ArrowLeft"); assert sel() == ["nc_rev", 3]
    page.keyboard.press("ArrowUp"); assert sel() == ["nc_orders", 3]
    page.keyboard.press("End"); assert sel() == ["nc_orders", 59]
    page.keyboard.press("Home"); assert sel() == ["nc_orders", 0]
    page.keyboard.press("Tab"); assert sel() == ["nc_orders", 1]
    page.keyboard.press("Shift+Tab"); assert sel() == ["nc_orders", 0]
    page.keyboard.press("PageDown")
    assert sel()[0] == "noi_margin" or sel()[0] == "noi"
    # moving off screen scrolls the selection into view, clear of the sticky label column
    cell(page, "nc_orders", 0).click()
    page.keyboard.press("End")
    visible = page.evaluate("""(() => { const s = document.querySelector('.pg-scroll').getBoundingClientRect(); const c = document.querySelector('td.pg-sel').getBoundingClientRect();
        return c.right <= s.right + 1 && c.left >= s.left; })()""")
    assert visible
    assert page.locator("table.pg-table").get_attribute("aria-activedescendant")


def test_sticky_label_column_and_header(page):
    open_module(page)
    page.evaluate("(() => { const s = document.querySelector('.pg-scroll'); s.scrollLeft = 1500; s.scrollTop = 120; })()")
    pos = page.evaluate("""(() => { const s = document.querySelector('.pg-scroll').getBoundingClientRect();
        const l = document.querySelector('tr[data-key=total_rev] th.pg-label').getBoundingClientRect();
        const t = document.querySelector('tr.pg-yearrow th.pg-yh').getBoundingClientRect();
        const m = document.querySelector('tr.pg-monthrow th.pg-mh').getBoundingClientRect();
        return { labelLeft: l.left - s.left, yearTop: t.top - s.top, monthTop: m.top - s.top, yh: t.height }; })()""")
    assert abs(pos["labelLeft"]) < 2 and abs(pos["yearTop"]) < 2 and abs(pos["monthTop"] - pos["yh"]) < 2


def test_group_collapse_keeps_totals(page):
    open_module(page)
    assert page.locator('tr[data-key="nc_orders"]').is_visible()
    page.click('tr[data-key="h_rev"] .pg-caret')
    assert page.locator('tr[data-key="nc_orders"]').is_hidden()
    assert page.locator('tr[data-key="spend"]').is_hidden()
    assert page.locator('tr[data-key="total_rev"]').is_visible() and page.locator('tr[data-key="dtc_rev"]').is_visible()
    assert page.locator('tr[data-key="h_rev"] .pg-caret').get_attribute("aria-expanded") == "false"
    page.click('tr[data-key="h_rev"] .pg-caret')
    assert page.locator('tr[data-key="nc_orders"]').is_visible()
    page.click("button:has-text('Collapse all')")
    assert page.locator('tr[data-key="spend"]').is_hidden()
    page.click("button:has-text('Expand all')")
    assert page.locator('tr[data-key="spend"]').is_visible()


def test_collapse_survives_an_edit_refresh(page):
    open_module(page)
    page.click('tr[data-key="h_cost"] .pg-caret')                   # collapse Costs
    cell(page, "spend", PROJ).click()
    page.keyboard.press("ControlOrMeta+a")
    page.keyboard.type("360k")
    page.keyboard.press("Enter")
    page.wait_for_function("document.querySelector('.pg-gstatus').textContent === 'Saved 1 cell'")
    assert page.locator('tr[data-key="h_cost"] .pg-caret').get_attribute("aria-expanded") == "false"


def test_fy_totals_toggle(page):
    open_module(page)
    assert page.locator("th.pg-fy").count() == 0
    page.click("button:has-text('FY totals')")
    assert page.locator("tr.pg-monthrow th.pg-fy").count() == 5
    heads = page.locator("tr.pg-monthrow th.pg-mh").all_inner_texts()
    assert heads[12] == "FY25" and heads[-1] == "FY29"
    # FY25 is all actual (no asterisk), FY26 mixes actual and projected (asterisk), money rows sum, CPA averages
    fy25_idx = 12
    spend_months = [page.locator(f'tr[data-key="spend"] td[data-c="{i}"]').inner_text() for i in range(12)]
    total = sum(int(t.replace("$", "").replace(",", "").replace("*", "")) for t in spend_months)
    fy25 = cell(page, "spend", fy25_idx).inner_text()
    assert fy25 == f"${total:,}" and not fy25.endswith("*")
    assert cell(page, "spend", 25).inner_text().endswith("*")
    assert "$" in cell(page, "nc_cpa", fy25_idx).inner_text()
    assert cell(page, "noi_margin", fy25_idx).inner_text() == ""      # ratios are not summed
    assert "pg-in" not in (cell(page, "spend", fy25_idx).get_attribute("class") or "")   # FY cells are never editable
    # year band spans months + FY column
    assert page.locator("tr.pg-yearrow th.pg-yh").first.get_attribute("colspan") == "13"
    page.click("button:has-text('FY totals')")
    assert page.locator("th.pg-fy").count() == 0


def test_column_hover_highlight(page):
    open_module(page)
    cell(page, "nc_rev", 7).hover()
    assert "pg-colhover" in cell(page, "spend", 7).get_attribute("class")
    assert "pg-colhover" in page.locator('tr.pg-monthrow th[data-c="7"]').get_attribute("class")
    assert "pg-colhover" not in cell(page, "spend", 8).get_attribute("class")


def test_copy_table_as_tsv(page):
    open_module(page)
    page.click("button:has-text('Copy')")
    expect(page.locator(".pg-toast")).to_contain_text("Copied table")
    text = page.evaluate("navigator.clipboard.readText()")
    lines = text.split("\n")
    assert lines[0].split("\t")[:3] == ["", "Jan 2025", "Feb 2025"]
    assert len(lines[0].split("\t")) == 61
    spend = next(l for l in lines if l.startswith("Ad spend\t")).split("\t")
    assert spend[1] == "900000" and "*" not in text and "$" not in text
    assert next(l for l in lines if l.startswith("Revenue")) == "Revenue"          # header row: label only


def test_copy_selection(page):
    open_module(page)
    cell(page, "spend", 0).click()
    page.keyboard.press("Escape")
    page.keyboard.press("Shift+ArrowRight")
    page.keyboard.press("Shift+ArrowDown")
    page.keyboard.press("ControlOrMeta+c")
    # the copy event fills the clipboard; read it back through the page
    text = page.evaluate("navigator.clipboard.readText()")
    rows = [r.split("\t") for r in text.split("\n")]
    assert len(rows) == 2 and len(rows[0]) == 2 and rows[0][0] == "900000"


def test_demo_grid_status_chips_and_local_edit(page):
    page.goto("/projection/_demo?demo=1")
    page.wait_for_selector('[data-testid="demo"] .pg-table')
    chips = page.locator('tr[data-key="flag"] .pg-chip')
    assert chips.count() > 20
    tones = set(chips.evaluate_all("els => els.map(e => e.className)"))
    assert any("bad" in t for t in tones) and any("warn" in t for t in tones) and any("good" in t for t in tones)
    before = cell(page, "nc_rev", 25).inner_text()
    cell(page, "spend", 25).click()
    page.keyboard.press("ControlOrMeta+a")
    page.keyboard.type("800000")
    page.keyboard.press("Enter")
    page.wait_for_function("document.querySelector('.pg-gstatus').textContent === 'Saved 1 cell'")
    assert cell(page, "nc_rev", 25).inner_text() != before
    # NOI below zero is highlighted by the demo's highlight() callback
    assert page.locator("tr[data-key=noi] td.tone-bad").count() > 0
    page.screenshot(path=str(SHOTS / "demo-desktop.png"), full_page=True)


def test_update_preserves_scroll_and_selection(page):
    open_module(page)
    cell(page, "spend", 45).click()                                  # Playwright scrolls the cell into view first
    scroll = "[document.querySelector('.pg-scroll').scrollLeft, document.querySelector('.pg-scroll').scrollTop]"
    page.evaluate("(() => { const s = document.querySelector('.pg-scroll'); s.scrollLeft = s.scrollLeft - 60; })()")
    before = page.evaluate(scroll)
    page.keyboard.press("ControlOrMeta+a")
    page.keyboard.type("360k")
    page.keyboard.press("Enter")                                     # saves, the module returns a fresh payload, the grid re-renders
    page.wait_for_function("document.querySelector('.pg-gstatus').textContent === 'Saved 1 cell'")
    after = page.evaluate(scroll)
    assert abs(after[0] - before[0]) <= 2 and abs(after[1] - before[1]) <= 30
    assert page.locator("td.pg-sel").count() == 1
    assert cell(page, "spend", 45).inner_text() == "$360,000*"


def test_grid_handles_empty_and_odd_payloads(page):
    page.goto("/projection/_demo?demo=1")
    page.wait_for_selector('[data-testid="demo"] .pg-table')
    out = page.evaluate("""async () => {
      const { Grid } = await import('/static/js/projection/lib/grid.js');
      const host = document.createElement('div'); document.body.append(host);
      const res = {};
      const g = new Grid(host, { payload: { months: [], rows: [] } });
      res.empty = host.querySelectorAll('tbody tr').length;
      g.update({ months: ['2026-01-01', '2026-02-01'], rows: [
        { key: 'a', label: 'A', values: [1, null] },                                  // no kind / fmt / status
        { key: 'b', label: 'B', kind: 'weird', fmt: 'nope', values: [NaN, 'x'] },
        { key: 'h', label: 'H', kind: 'header' },
        { key: 'c', label: 'C', kind: 'calc', fmt: 'usd0', values: [1e12, -0.2], input: true },
      ] });
      res.rows = host.querySelectorAll('tbody tr').length;
      res.cellA = host.querySelector('tr[data-key=a] td[data-c="0"]').textContent;
      res.cellC = [...host.querySelectorAll('tr[data-key=c] td')].map(t => t.textContent);
      res.editable = host.querySelectorAll('td.pg-in').length;                       // no onEdit => nothing editable
      g.setOptions({ yearBands: false, density: 'comfortable' });
      res.head = [...host.querySelectorAll('thead th.pg-mh')].map(t => t.textContent);
      g.destroy();
      return res;
    }""")
    assert out["empty"] == 0 and out["rows"] == 4
    assert out["cellA"] == "1"
    assert out["cellC"] == ["$1,000,000,000,000", "$0"] and out["editable"] == 0
    assert out["head"] == ["Jan 2026", "Feb 2026"]


def test_frozen_cols_and_density_options(page):
    page.goto("/projection/_demo?demo=1")
    page.wait_for_selector('[data-testid="demo"] .pg-table')
    out = page.evaluate("""async () => {
      const { Grid } = await import('/static/js/projection/lib/grid.js');
      const payload = await fetch('/api/projection/drivers/table?scenario=current').then(r => r.json());
      const host = document.createElement('div'); host.id = 'frozen-host'; document.body.append(host);
      const g = new Grid(host, { payload, frozenCols: 3, density: 'comfortable', yearBands: false });
      const sc = host.querySelector('.pg-scroll'); sc.scrollLeft = 1500;
      await new Promise(r => setTimeout(r, 50));
      const s = sc.getBoundingClientRect();
      const left = (sel) => document.querySelector(sel).getBoundingClientRect().left - s.left;
      return { label: left('#frozen-host tr[data-key=spend] th.pg-label'), m0: left('#frozen-host tr[data-key=spend] td[data-c="0"]'), m1: left('#frozen-host tr[data-key=spend] td[data-c="1"]'),
               m2: left('#frozen-host tr[data-key=spend] td[data-c="2"]'), rowH: host.querySelector('tr[data-key=spend] td').offsetHeight, heads: host.querySelectorAll('tr.pg-yearrow').length };
    }""")
    assert abs(out["label"]) < 2
    assert 240 < out["m0"] < 262 and 330 < out["m1"] < 355        # frozen months sit right after the 250px label column
    assert out["m2"] < 0 or out["m2"] > 400                       # the third month scrolls away
    assert out["rowH"] >= 30 and out["heads"] == 0


def test_print_media_hides_chrome(page):
    open_module(page)
    page.emulate_media(media="print")
    assert page.locator(".pg-toolbar").is_hidden()
    assert page.locator("#topbar").is_hidden() and page.locator("#pg-bar").is_hidden() and page.locator("#pg-nav").is_hidden()
    assert page.locator(".pg-scroll").evaluate("e => getComputedStyle(e).overflow") == "visible"
