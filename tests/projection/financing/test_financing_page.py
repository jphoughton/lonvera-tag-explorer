"""The Cash & debt page in a real browser (headless Chromium via Playwright) against the real Flask app on a local port."""
from __future__ import annotations

import threading

import pytest
from werkzeug.serving import make_server

from app.projection import registry

sync_api = pytest.importorskip("playwright.sync_api")


@pytest.fixture(autouse=True)
def only_financing(monkeypatch):
    real = registry.discover
    monkeypatch.setattr(registry, "discover", lambda package=None, refresh=False: {k: v for k, v in real(package, refresh).items() if k == "financing"})


@pytest.fixture()
def server(app_factory):
    app = app_factory(AUTH_DISABLED=True)
    srv = make_server("127.0.0.1", 0, app, threaded=True)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


@pytest.fixture()
def browser():
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:                                     # browsers not installed on this machine
            pytest.skip(f"Chromium not available: {e}")
        yield b
        b.close()


def open_page(browser, base, width=1440, height=900):
    page = browser.new_context(viewport={"width": width, "height": height}).new_page()
    msgs: list[str] = []
    page.on("console", lambda m: msgs.append(f"{m.type}: {m.text}") if m.type in ("error", "warning") else None)
    page.on("pageerror", lambda e: msgs.append(f"pageerror: {e}"))
    page.goto(f"{base}/projection/financing", wait_until="networkidle")
    page.wait_for_selector("body[data-pg-state=ready], body[data-pg-state=error]", timeout=20000)
    return page, msgs


def principal_cell(page, index):
    return page.locator(".pg-table").first.locator("tr", has_text="Term loan principal").first.locator("td").nth(index)


def test_page_renders_with_header_headline_and_every_section(browser, server):
    page, msgs = open_page(browser, server)
    assert page.evaluate("document.body.dataset.pgState") == "ready"
    assert page.locator("h1.pg-h1").inner_text() == "Cash & debt"
    assert page.locator(".pg-purpose").count() == 1
    kpis = page.locator(".pg-tldr .pg-kpi")
    assert 3 <= kpis.count() <= 5
    labels = " ".join(kpis.locator(".k").all_inner_texts()).lower()
    assert "lowest ending cash" in labels and "payback" in labels and "term loan" in labels
    line = page.locator(".pg-tldr-line").inner_text()
    assert line.startswith("Cash ") and len(line.split()) <= 18 and "*" in line                 # the finding first, short, projected months asterisked
    assert [t.lower() for t in page.locator("section.pg-section > h2").all_inner_texts()] == [
        "cash by month", "debt", "new-customer payback", "spend and return", "media-buyer payout"]
    assert page.locator(".pg-table").count() == 2                     # cash grid and term loan grid
    assert page.locator("canvas").count() == 2                        # cash chart and payback chart
    assert page.locator(".pg-section").nth(3).locator("table").first.locator("tbody tr").count() == 19      # spend rows of the grid
    assert page.locator(".pg-section").nth(4).locator("table").first.locator("tbody tr").count() == 10      # payout scale tiers
    assert [m for m in msgs if "favicon" not in m] == [], msgs


def test_editing_a_principal_cell_saves_audits_and_redraws(browser, server, pg):
    page, msgs = open_page(browser, server)
    cell = principal_cell(page, 3)                                    # Dec 2026
    assert cell.inner_text().startswith("$50,000")
    cell.click()
    page.keyboard.type("75000")
    page.keyboard.press("Enter")
    page.wait_for_function("() => [...document.querySelectorAll('.pg-table')][0] && document.querySelectorAll('.pg-table')[0].innerText.includes('$2,595,000')", timeout=8000)      # the balance row is recalculated by the server
    assert principal_cell(page, 3).inner_text().startswith("$75,000")
    bal = page.locator(".pg-table").first.locator("tr", has_text="Term loan balance").first.locator("td").nth(3)
    assert bal.inner_text().startswith("$2,595,000")                  # 3,000,000 - 180,000 - 150,000 - 75,000
    row = pg.execute("SELECT old, new, actor FROM proj_audit WHERE action = 'series.update' AND line_key = 'financing.principal_paid'").fetchone()
    assert row["old"]["value"] == 50_000 and row["new"]["value"] == 75_000
    assert [m for m in msgs if "favicon" not in m] == [], msgs


def test_a_rejected_edit_reverts_and_says_why(browser, server, pg):
    page, _ = open_page(browser, server)
    cell = principal_cell(page, 5)
    before = cell.inner_text()
    cell.click()
    page.keyboard.type("-5")
    page.keyboard.press("Enter")
    page.wait_for_selector(".pg-toast.error", timeout=8000)
    assert "principal_paid must be zero or more" in page.locator(".pg-toast.error").first.inner_text()
    assert principal_cell(page, 5).inner_text() == before
    assert pg.execute("SELECT count(*) AS n FROM proj_audit").fetchone()["n"] == 0


def test_payback_recalculation_is_a_what_if_and_save_writes_the_scenario(browser, server, pg):
    page, _ = open_page(browser, server)
    assert "Pays back in month 10" in page.locator(".pg-section").nth(2).inner_text()
    page.locator("label", has_text="Average order value (AOV)").first.locator("input").fill("100")
    page.get_by_role("button", name="Recalculate").first.click()
    page.wait_for_function("() => document.querySelectorAll('.pg-section')[2].innerText.includes('Pays back in month 2')", timeout=8000)
    assert pg.execute("SELECT count(*) AS n FROM proj_audit").fetchone()["n"] == 0        # nothing saved yet
    page.get_by_role("button", name="Save to scenario").first.click()
    page.wait_for_selector(".pg-toast.ok", timeout=8000)
    row = pg.execute("SELECT new FROM proj_audit WHERE action = 'scenario.params'").fetchone()
    assert row["new"]["params"]["financing"]["payback"]["aov"] == 100


def test_saving_loan_terms_changes_interest(browser, server):
    page, _ = open_page(browser, server)
    page.locator("label", has_text="Annual rate (%)").first.locator("input").fill("12")
    page.get_by_role("button", name="Save loan terms").click()
    page.wait_for_function("() => document.querySelectorAll('.pg-table')[0].innerText.includes('$30,000')", timeout=8000)
    interest = page.locator(".pg-table").first.locator("tr", has_text="Term loan interest").first.locator("td").nth(0)
    assert interest.inner_text().startswith("$30,000")


def test_phone_width_adds_no_page_level_horizontal_scroll_from_this_page(browser, server):
    page, _ = open_page(browser, server, 390, 844)
    wide = page.evaluate("""() => { const w = window.innerWidth; const out = [];
        const scroller = (el) => { for (let a = el.parentElement; a && a !== document.body; a = a.parentElement) { const o = getComputedStyle(a).overflowX; if (o === 'auto' || o === 'scroll' || o === 'hidden') return true; } return false; };
        for (const el of document.querySelectorAll('#view *')) { const r = el.getBoundingClientRect(); if (r.right > w + 1 && r.width > 0 && !scroller(el)) out.push(el.tagName + '.' + el.className); }
        return out; }""")
    assert wide == []


def test_unseeded_scenario_renders_and_takes_aov_and_cac_from_the_last_closed_month(browser, server, bare):
    page, msgs = open_page(browser, server)
    assert page.evaluate("document.body.dataset.pgState") == "ready"
    assert "months" in page.locator(".pg-tldr-kpis").inner_text()                      # payback computed from the drivers' last closed month
    assert page.locator("label", has_text="Average order value (AOV)").first.locator("input").input_value() != ""
    assert "term loan" in page.locator(".pg-tldr-kpis").inner_text().lower()
    assert [m for m in msgs if "favicon" not in m] == [], msgs
