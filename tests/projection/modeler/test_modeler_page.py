"""The Scenario modeler page in a real browser (headless Chromium) against the real Flask app on a local port."""
from __future__ import annotations

import json
import re
import threading
import urllib.request
from pathlib import Path

import pytest
from conftest import AOV, CPA, P, SPEND, copy_of, fy_months, make_app, put_cells
from werkzeug.serving import make_server

sync_api = pytest.importorskip("playwright.sync_api")
ROOT = Path(__file__).resolve().parents[3]
AUDIT_JS = (ROOT / "tests/projection/design/tools/audit_readability.js").read_text()
SHOTS = Path(__file__).resolve().parent / "shots"
MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


@pytest.fixture()
def server(mdb):
    app = make_app(mdb, AUTH_DISABLED=True)
    srv = make_server("127.0.0.1", 0, app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()
    app.extensions["pool"].close()


@pytest.fixture()
def browser():
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:
            pytest.skip(f"Chromium not available: {e}")
        yield b
        b.close()


def api(base, path, body=None, method=None):
    req = urllib.request.Request(f"{base}{P}{path}", data=None if body is None else json.dumps(body).encode(), method=method or ("POST" if body is not None else "GET"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read().decode())


def open_page(browser, base, path="/projection/modeler?scenario=modeler", width=1440, height=900, scheme="light"):
    ctx = browser.new_context(viewport={"width": width, "height": height}, color_scheme=scheme)
    page = ctx.new_page()
    msgs: list[str] = []
    page.on("console", lambda m: msgs.append(f"{m.type}: {m.text}") if m.type in ("error", "warning") else None)
    page.on("pageerror", lambda e: msgs.append(f"pageerror: {e}"))
    if base.endswith("/"):
        base = base[:-1]
    if "scenario=modeler" in path:
        api(base, "/modeler/workspace", {})                           # the working copy exists, as it does after the first visit
    page.goto(f"{base}{path}", wait_until="networkidle")
    page.wait_for_selector("body[data-pg-state=ready], body[data-pg-state=error]", timeout=20000)
    page.wait_for_selector("#mo-cue", timeout=20000)
    page.wait_for_selector(".mo-opt", timeout=20000)
    return page, msgs


def label(iso):
    return f"{MON[int(iso[5:7]) - 1]} {iso[:4]}"


def cell(page, key, iso):
    col = page.locator(f"th.pg-mh[title^='{label(iso)}']").first.get_attribute("data-c")
    return page.locator(f"tr[data-key='{key}'] td[data-c='{col}']")


def cell_text(page, key, iso):
    return cell(page, key, iso).inner_text().replace("*", "").strip()


def wait_saved(page):
    page.wait_for_function("document.querySelector('#mo-status') && document.querySelector('#mo-status').textContent.trim() === 'Saved'", timeout=15000)


def wait_cell(page, key, iso, text):
    col = page.locator(f"th.pg-mh[title^='{label(iso)}']").first.get_attribute("data-c")
    page.wait_for_function("([k, c, t]) => { const td = document.querySelector(`tr[data-key='${k}'] td[data-c='${c}']`); return td && td.textContent.replace('*', '').trim() === t; }", arg=[key, col, text], timeout=10000)
    wait_saved(page)


def type_cell(page, key, iso, text):
    cell(page, key, iso).click()
    page.keyboard.type(text)
    page.keyboard.press("Enter")


def money(s):
    s = s.replace(",", "").replace("$", "").strip()
    neg = s.startswith("-") or s.startswith("(")
    v = float(re.sub(r"[^\d.]", "", s))
    return -v if neg else v


def years_table(page):
    return page.evaluate("""() => { const out = {}; document.querySelectorAll('table.mo-years tbody tr').forEach(tr => {
        out[tr.querySelector('th').textContent.trim()] = [...tr.querySelectorAll('td')].map(td => td.title || td.textContent.trim()); }); return out; }""")


def saved_cell(base, key, iso, slug="modeler", expect=None, tries=60):
    """The saved value of a plan cell; with `expect`, polls until the save lands (the grid shows an edit before the save is sent)."""
    import time
    for _ in range(tries):
        s = api(base, f"/modeler/state?scenario={slug}")
        v = next(r for r in s["rows"] if r["key"] == key)["values"][s["months"].index(iso)]
        if expect is None or v == expect:
            return v
        time.sleep(0.1)
    return v


def saved_years(base, slug="modeler"):
    return {y["year"]: y for y in api(base, f"/modeler/state?scenario={slug}")["outputs"]["years"]}


def tldr(page):
    return page.locator(".pg-tldr-line").inner_text()


def wait_tldr_change(page, before):
    page.wait_for_function("(b) => document.querySelector('.pg-tldr-line').innerText !== b", arg=before, timeout=8000)


def run_tool(page, row, op, frm, to, **fields):
    page.select_option("#mo-row", row)
    page.select_option("#mo-op", op)
    page.select_option("#mo-from", frm)
    page.select_option("#mo-to", to)
    for k, v in fields.items():
        page.fill(f"#mo-f-{k}", str(v))
    page.wait_for_function("() => { const b = document.querySelector('#mo-apply'); return b && !b.disabled; }", timeout=8000)
    text = page.locator("#mo-tool-text").inner_text()
    page.click("#mo-apply")
    return text


# ------------------------------------------------------------------------------------------------------------------ the working copy

def test_arriving_on_the_plan_of_record_opens_the_working_copy_and_says_so(restore, browser, server):
    por_before = api(server, "/scenarios")["scenarios"][0]["version"]
    page, msgs = open_page(browser, server, path="/projection/modeler")
    page.wait_for_url(re.compile(r"scenario=modeler"), timeout=15000)
    assert "Working copy" in page.locator("#mo-cue").inner_text() and "Modeling on: Modeler" in page.locator("#mo-cue").inner_text()
    assert "Created \"Modeler\", a copy of the plan of record" in page.locator(".pg-toasts").inner_text()
    assert page.locator("h1.pg-h1").inner_text() == "Scenario modeler" and page.title().startswith("Scenario modeler")
    assert [s for s in api(server, "/scenarios")["scenarios"] if s["is_default"]][0]["version"] == por_before
    assert page.evaluate("document.body.dataset.pgScenario") == "copy"
    page.reload(wait_until="networkidle")
    page.wait_for_selector("#mo-cue")
    assert page.url.count("scenario=modeler") == 1 and not [m for m in msgs if "pageerror" in m]


def test_choosing_the_plan_of_record_on_purpose_is_kept_and_edits_still_ask_first(restore, browser, server):
    page, msgs = open_page(browser, server)
    page.select_option("#pg-scenario", label="Current plan")
    page.wait_for_function("document.querySelector('#mo-cue') && document.querySelector('#mo-cue').innerText.includes('Plan of record')", timeout=10000)
    assert "Your first change asks whether to work on a copy instead." in page.locator("#mo-cue").inner_text()
    before = api(server, "/modeler/state?scenario=current")["outputs"]["years"]
    type_cell(page, SPEND, "2027-03-01", "900000")
    page.wait_for_selector(".pg-por-ask", timeout=8000)
    page.click(".pg-por-ask button[data-choice=cancel]")
    page.wait_for_timeout(600)
    assert api(server, "/modeler/state?scenario=current")["outputs"]["years"] == before


# ------------------------------------------------------------------------------------------------------------------ editing

def test_editing_spend_aov_and_cpa_cells_moves_the_outputs_and_saves(restore, browser, server):
    page, msgs = open_page(browser, server)
    base_line = tldr(page)
    assert base_line.startswith("At $350K a month, $95 CPA and $69 AOV, FY27 revenue is ")
    rev0 = money(years_table(page)["Total revenue"][1])
    type_cell(page, SPEND, "2027-01-01", "600000")
    wait_tldr_change(page, base_line)
    assert tldr(page).startswith("On average $371K a month")
    wait_saved(page)
    rev1 = money(years_table(page)["Total revenue"][1])
    assert rev1 > rev0 and rev1 == pytest.approx(saved_years(server)[2027]["total_revenue"], abs=0.5)
    line = tldr(page)
    type_cell(page, CPA, "2027-02-01", "60")
    wait_tldr_change(page, line)
    wait_saved(page)
    assert money(years_table(page)["Total revenue"][1]) > rev1
    line = tldr(page)
    type_cell(page, AOV, "2027-02-01", "80")
    wait_tldr_change(page, line)
    wait_saved(page)
    table = years_table(page)
    for y, text in zip((2026, 2027, 2028, 2029), table["Total revenue"]):
        assert money(text) == pytest.approx(saved_years(server)[y]["total_revenue"], abs=0.5)
    for y, text in zip((2026, 2027, 2028, 2029), table["EBITDA"]):
        assert money(text) == pytest.approx(saved_years(server)[y]["ebitda"], abs=0.5)
    assert cell_text(page, SPEND, "2027-01-01") == "$600,000" and cell_text(page, CPA, "2027-02-01") == "$60.00"
    kpis = page.locator(".pg-tldr .pg-kpi").all_inner_texts()
    assert any("vs plan" in k for k in kpis)
    assert [m for m in msgs if "pageerror" in m] == []


def test_the_outputs_answer_within_a_third_of_a_second_of_the_edit(restore, browser, server):
    page, _ = open_page(browser, server)
    line = tldr(page)
    page.evaluate("window.__t0 = 0; const orig = window.fetch; window.fetch = (...a) => { if (String(a[0]).includes('/modeler/preview')) window.__t0 = window.__t0 || performance.now(); return orig(...a).then(r => { if (String(a[0]).includes('/modeler/preview')) window.__t1 = performance.now(); return r; }); };")
    type_cell(page, SPEND, "2027-05-01", "480000")
    wait_tldr_change(page, line)
    ms = page.evaluate("window.__t1 - window.__t0")
    assert ms < 300, ms


def test_a_typed_zero_cpa_is_refused_with_the_reason_and_the_cell_goes_back(restore, browser, server):
    page, msgs = open_page(browser, server)
    type_cell(page, CPA, "2027-03-01", "0")
    page.wait_for_function("document.body.innerText.includes('must be above $0.00')", timeout=8000)
    assert cell_text(page, CPA, "2027-03-01") == "$95.00"
    assert api(server, "/modeler/state?scenario=modeler")["outputs"]["averages"]["cpa"] == 95.0
    assert [m for m in msgs if "pageerror" in m] == []


def test_a_closed_month_is_locked_and_nothing_is_sent(restore, browser, server):
    page, msgs = open_page(browser, server)
    before = cell_text(page, CPA, "2026-08-01")
    calls = []
    page.on("request", lambda r: calls.append(r.url) if "/modeler/preview" in r.url or r.method == "PUT" else None)
    type_cell(page, CPA, "2026-08-01", "50")
    page.wait_for_function("document.body.innerText.includes('holds loaded figures')", timeout=8000)
    assert cell_text(page, CPA, "2026-08-01") == before and calls == []
    assert "locked" in (cell(page, CPA, "2026-08-01").get_attribute("class") or "") or "tone-locked" in (cell(page, CPA, "2026-08-01").get_attribute("class") or "")
    assert msgs == [], msgs


def test_pasting_a_row_from_sheets_fills_the_months(restore, browser, server):
    page, msgs = open_page(browser, server)
    cell(page, SPEND, "2027-06-01").click()
    page.evaluate("""() => { const dt = new DataTransfer(); dt.setData('text/plain', '400000\\t410000\\t420000\\t430000');
        document.activeElement.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true })); }""")
    wait_saved(page)
    assert [cell_text(page, SPEND, m) for m in ("2027-06-01", "2027-07-01", "2027-08-01", "2027-09-01")] == ["$400,000", "$410,000", "$420,000", "$430,000"]
    s = api(server, "/modeler/state?scenario=modeler")
    spend = next(r for r in s["rows"] if r["key"] == SPEND)["values"]
    assert [spend[s["months"].index(m)] for m in ("2027-06-01", "2027-09-01")] == [400000.0, 430000.0]
    assert msgs == [], msgs


# ------------------------------------------------------------------------------------------------------------------ quick edits

def test_the_quick_edit_tool_sets_a_range_ramps_changes_by_a_percent_and_undoes(restore, browser, server):
    page, msgs = open_page(browser, server)
    text = run_tool(page, "spend_total", "fill", "2027-01-01", "2027-06-01", value=550000)
    assert text == "Set ad spend to $550,000 for Jan 2027 to Jun 2027 (6 months)."
    wait_cell(page, SPEND, "2027-06-01", "$550,000")
    assert cell_text(page, SPEND, "2027-07-01") == "$350,000"
    assert "Set ad spend to $550,000" in page.locator(".pg-toasts").inner_text()
    run_tool(page, "nc_cpa", "ramp", "2027-01-01", "2027-05-01", end=75)
    wait_cell(page, CPA, "2027-05-01", "$75.00")
    assert [cell_text(page, CPA, m) for m in ("2027-01-01", "2027-03-01", "2027-05-01")] == ["$95.00", "$85.00", "$75.00"]
    run_tool(page, "nc_aov", "percent", "2027-01-01", "2027-12-01", pct="10")
    wait_cell(page, AOV, "2027-04-01", "$75.90")
    assert cell_text(page, AOV, "2027-04-01") == "$75.90" and "+10%" in page.locator("#mo-tool-text").inner_text()
    assert saved_cell(server, AOV, "2027-04-01", expect=75.9) == 75.9
    page.click(".pg-toolbar button:has-text('Undo')")                            # undoes the percent change
    wait_cell(page, AOV, "2027-04-01", "$69.00")
    assert saved_cell(server, AOV, "2027-04-01", expect=69.0) == 69.0 and cell_text(page, AOV, "2027-04-01") == "$69.00"
    assert [cell_text(page, CPA, m) for m in ("2027-03-01",)] == ["$85.00"]                                  # the ramp is still in
    assert msgs == [], msgs


def test_the_tool_repeats_a_month_forward_and_puts_a_row_back_to_the_plan_of_record(restore, browser, server):
    page, msgs = open_page(browser, server)
    type_cell(page, SPEND, "2027-01-01", "480000")
    wait_cell(page, SPEND, "2027-01-01", "$480,000")
    text = run_tool(page, "spend_total", "copy", "2027-01-01", "2027-04-01")
    assert text.startswith("Repeat ad spend from Jan 2027 ($480,000) through Apr 2027 (3 months).")
    wait_cell(page, SPEND, "2027-04-01", "$480,000")
    assert cell_text(page, SPEND, "2027-05-01") == "$350,000"
    run_tool(page, "spend_total", "reset", "2027-01-01", "2027-04-01")
    wait_cell(page, SPEND, "2027-04-01", "$350,000")
    assert cell_text(page, SPEND, "2027-01-01") == "$350,000"
    assert saved_cell(server, SPEND, "2027-02-01", expect=350000.0) == 350000.0
    assert msgs == [], msgs


def test_the_tool_describes_the_change_and_shows_the_outputs_before_it_is_applied(restore, browser, server):
    page, _ = open_page(browser, server)
    line = tldr(page)
    page.select_option("#mo-row", "spend_total")
    page.select_option("#mo-from", "2027-01-01")
    page.select_option("#mo-to", "2027-12-01")
    page.fill("#mo-f-value", "700000")
    page.wait_for_function("document.querySelector('#mo-tool-text').innerText.startsWith('Set ad spend to $700,000')", timeout=8000)
    assert "Previewing the change below" in page.locator("#mo-status").inner_text()
    assert tldr(page) != line and cell_text(page, SPEND, "2027-01-01") == "$350,000"                         # outputs preview, the grid and the saved plan wait for Apply
    assert saved_years(server)[2027]["ad_spend"] == 4200000.0
    page.fill("#mo-f-value", "")
    page.wait_for_function("(b) => document.querySelector('.pg-tldr-line').innerText === b", arg=line, timeout=8000)


def test_a_range_over_closed_months_skips_them_and_the_grid_selection_fills_the_form(restore, browser, server):
    page, _ = open_page(browser, server)
    page.select_option("#mo-row", "nc_cpa")
    page.select_option("#mo-from", "2026-11-01")
    cell(page, CPA, "2027-02-01").click()
    cell(page, CPA, "2027-04-01").click(modifiers=["Shift"])
    page.wait_for_function("document.querySelector('#mo-from').value === '2027-02-01' && document.querySelector('#mo-to').value === '2027-04-01'", timeout=5000)
    assert page.locator("#mo-row").input_value() == "nc_cpa"


# ------------------------------------------------------------------------------------------------------------------ retention

def test_the_retention_selector_shows_each_option_and_writes_the_scenario(restore, browser, server):
    page, msgs = open_page(browser, server)
    cards = {k: page.locator(f".mo-opt[data-key={k}] .mo-opt-pick") for k in ("current", "goal", "asof")}
    boxes = {k: page.locator(f".mo-opt[data-key={k}]") for k in cards}
    assert cards["current"].get_attribute("aria-checked") == "true" and "In use" in boxes["current"].inner_text()
    for k in cards:
        assert re.search(r"\d\.\d\dx lifetime . \d\.\d\dx first 12 months", boxes[k].inner_text()), boxes[k].inner_text()
    line = tldr(page)
    cards["goal"].click()
    page.wait_for_function("document.querySelector('.mo-opt[data-key=goal] .mo-opt-pick').getAttribute('aria-checked') === 'true'", timeout=10000)
    wait_saved(page)
    assert tldr(page) != line and "Goal" in page.locator("#mo-retention-line").inner_text()
    lev = api(server, "/cohorts/levers?scenario=modeler")["levers"]
    assert lev["curve_source"] == "goal"
    assert api(server, "/cohorts/levers?scenario=current")["levers"]["curve_source"] == "live"
    assert money(years_table(page)["Repeat revenue"][1]) == pytest.approx(next(y for y in api(server, "/modeler/state?scenario=modeler")["outputs"]["years"] if y["year"] == 2027)["repeat_rev"], abs=0.5)
    page.locator(".mo-opt[data-key=asof] .seg", has_text="6 cohorts").click()
    page.wait_for_function("document.querySelector('.mo-opt[data-key=asof] .mo-opt-pick').getAttribute('aria-checked') === 'false'", timeout=5000)
    cards["asof"].click()
    page.wait_for_function("document.querySelector('.mo-opt[data-key=asof] .mo-opt-pick').getAttribute('aria-checked') === 'true'", timeout=10000)
    wait_saved(page)
    lev = api(server, "/cohorts/levers?scenario=modeler")["levers"]
    assert lev["curve_source"] == "asof" and lev["n_cohorts"] == 6 and lev["asof_month"]
    page.reload(wait_until="networkidle")
    page.wait_for_selector(".mo-opt")
    assert page.locator(".mo-opt[data-key=asof] .mo-opt-pick").get_attribute("aria-checked") == "true" and "6 cohorts" in page.locator("#mo-retention-line").inner_text()
    assert [m for m in msgs if "pageerror" in m] == []


def test_changing_the_cohorts_averaged_on_the_current_curve_is_saved(restore, browser, server):
    page, _ = open_page(browser, server)
    line = page.locator("#mo-retention-line").inner_text()
    page.locator(".mo-opt[data-key=current] .seg", has_text="6 cohorts").click()
    page.wait_for_function("(b) => document.querySelector('#mo-retention-line').innerText !== b", arg=line, timeout=10000)
    assert "last 6 cohorts" in page.locator("#mo-retention-line").inner_text()
    page.wait_for_function("document.querySelector('#mo-status').textContent.trim() === 'Saved'", timeout=10000)
    assert api(server, "/cohorts/levers?scenario=modeler")["levers"]["n_cohorts"] == 6


def test_switching_scenario_in_the_bar_moves_the_page_to_that_scenario(restore, browser, server, client):
    other = copy_of(client, "Elsewhere")
    page, _ = open_page(browser, server)
    page.select_option("#pg-scenario", value=other)
    page.wait_for_function("document.querySelector('#mo-cue') && document.querySelector('#mo-cue').innerText.includes('Modeling on: Elsewhere')", timeout=10000)
    assert "Working copy" in page.locator("#mo-cue").inner_text()


# ------------------------------------------------------------------------------------------------------------------ compare, save as, reset

def test_compare_shows_the_plan_of_record_and_up_to_three_others_with_differences(restore, browser, server, client):
    others = [copy_of(client, f"Other {i}") for i in range(4)]
    put_cells(client, others[0], [(SPEND, m, 700000.0) for m in fy_months("2027-01-01", 12)])
    page, msgs = open_page(browser, server)
    assert page.locator("#mo-compare input[data-slug=current]").is_checked()
    type_cell(page, SPEND, "2027-01-01", "800000")
    wait_saved(page)
    page.locator(f"#mo-compare input[data-slug={others[0]}]").check()
    page.wait_for_function("document.querySelector('table.mo-cmp') && document.querySelector('table.mo-cmp').innerText.includes('Other 0')", timeout=10000)
    rows = page.locator("table.mo-cmp tbody tr").all_inner_texts()
    assert any("Modeler (editing)" in r for r in rows) and any("Other 0" in r for r in rows) and any("plan of record" in r for r in rows)
    assert "vs Modeler" in page.locator("table.mo-cmp").inner_text()
    page.locator(f"#mo-compare input[data-slug={others[1]}]").check()
    page.wait_for_function("document.querySelectorAll('#mo-compare input:checked').length === 3")
    page.wait_for_function("(s) => document.querySelector(`#mo-compare input[data-slug=${s}]`).disabled", arg=others[2], timeout=10000)
    page.evaluate("window.__copied = null; navigator.clipboard.writeText = async (t) => { window.__copied = t; };")
    page.click("#mo-cmp-copy")
    page.wait_for_function("window.__copied !== null")
    lines = page.evaluate("window.__copied").split("\n")
    assert lines[0].split("\t") == ["Scenario", "Measure", "FY2026", "FY2027", "FY2028", "FY2029"]
    assert any(l.startswith("Modeler\tTotal revenue\t") for l in lines) and any("minus Modeler\tEBITDA" in l for l in lines)
    mine = next(l for l in lines if l.startswith("Modeler\tTotal revenue\t")).split("\t")
    assert int(mine[3]) == pytest.approx(saved_years(server)[2027]["total_revenue"], abs=1)
    assert [m for m in msgs if "pageerror" in m] == []


def test_save_as_keeps_the_numbers_and_reset_goes_back_to_the_plan_of_record(restore, browser, server):
    page, msgs = open_page(browser, server)
    type_cell(page, SPEND, "2027-01-01", "900000")
    wait_saved(page)
    changed = saved_years(server)
    page.click("#mo-save-as")
    page.fill("dialog input[type=text]", "Josh nine hundred K")
    page.click("dialog button[type=submit]")
    page.wait_for_function("document.querySelector('.pg-toasts') && document.querySelector('.pg-toasts').innerText.includes('Saved as')", timeout=8000)
    slug = next(s["slug"] for s in api(server, "/scenarios")["scenarios"] if s["name"] == "Josh nine hundred K")
    assert saved_years(server, slug)[2027]["total_revenue"] == pytest.approx(changed[2027]["total_revenue"])
    page.click("#mo-reset")
    page.click("dialog button:has-text('Reset')")
    page.wait_for_function("document.querySelector('.pg-tldr .pg-kpi .n') && document.querySelector('.pg-tldr .pg-kpi .n').innerText.includes('same as plan')", timeout=15000)
    assert saved_years(server)[2027]["total_revenue"] == pytest.approx(saved_years(server, "current")[2027]["total_revenue"])
    assert saved_years(server, slug)[2027]["total_revenue"] == pytest.approx(changed[2027]["total_revenue"])    # the saved copy did not move
    assert [m for m in msgs if "pageerror" in m] == []


def test_the_ebitda_definition_can_follow_pacing(restore, browser, server):
    page, _ = open_page(browser, server)
    assert page.locator("#mo-ebitda-text").inner_text() == "EBITDA is net operating income before interest expense."
    first = years_table(page)["EBITDA"][0]
    page.select_option("#mo-ebitda-def", "pacing")
    page.wait_for_function("document.querySelector('#mo-ebitda-text').innerText.includes('marketing influencers')", timeout=8000)
    assert years_table(page)["EBITDA"][0] != first


def test_a_reload_keeps_the_edits_the_retention_model_and_the_scroll_start(restore, browser, server):
    page, _ = open_page(browser, server)
    type_cell(page, SPEND, "2027-08-01", "445000")
    wait_saved(page)
    line = tldr(page)
    page.reload(wait_until="networkidle")
    page.wait_for_selector(".mo-opt")
    assert cell_text(page, SPEND, "2027-08-01") == "$445,000" and tldr(page) == line


# ------------------------------------------------------------------------------------------------------------------ phone, readability

def test_phone_width_has_no_sideways_scroll_and_the_results_stay_in_view(restore, browser, server):
    page, msgs = open_page(browser, server, width=390, height=844)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    page.evaluate("window.scrollTo(0, 1400)")
    page.wait_for_timeout(300)
    top = page.evaluate("document.querySelector('.mo-outputs').getBoundingClientRect().top")
    assert 0 <= top < 120 and page.locator("#mo-sum").is_visible() and "revenue" in page.locator("#mo-sum").inner_text()
    page.click(".mo-body > summary")
    assert page.locator("table.mo-years").is_visible() and page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    assert msgs == [], msgs


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("width,height", [(1440, 900), (390, 844)])
def test_text_is_readable_in_light_and_dark_and_the_page_is_looked_at(restore, browser, server, scheme, width, height):
    page, msgs = open_page(browser, server, width=width, height=height, scheme=scheme)
    page.wait_for_timeout(800)
    res = page.evaluate(AUDIT_JS)
    bad = [v for v in res["violations"] if v["kind"] not in ("disabled-note",)]
    assert bad == [] and res["pageOverflow"] is False, bad
    SHOTS.mkdir(exist_ok=True)
    page.screenshot(path=str(SHOTS / f"modeler-{width}-{scheme}.png"), full_page=True)
    assert msgs == [], msgs
