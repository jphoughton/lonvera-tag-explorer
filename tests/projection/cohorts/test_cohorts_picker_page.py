"""The retention-curve picker on the real P&L and Cohorts pages, in headless Chromium against a live server on a fully seeded database.

Database tagexp_proj_cohorts_page (every migration, cohorts / spend plan / P&L seeded from the sheet snapshot). Skipped when Playwright or its browser is
missing. Set PICKER_SHOTS_DIR to keep the screenshots (dark and light, desktop and 390 px) to look at.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import psycopg
import pytest

ROOT = Path(__file__).resolve().parents[3]
PG_BASE = os.environ.get("TEST_PG_BASE", "postgresql://localhost")
DB_NAME = os.environ.get("TEST_PROJ_COHORTS_PAGE_DB", "tagexp_proj_cohorts_page")
DB_URL = f"{PG_BASE}/{DB_NAME}"

pw = pytest.importorskip("playwright.sync_api")
SNAP = Path(os.environ.get("PROJECTION_SNAPSHOT_DIR", "~/lonvera-tag-explorer-data/projection-snapshot-2026-11-03")).expanduser()
if not SNAP.exists():
    pytest.skip("sheet snapshot not available", allow_module_level=True)


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    try:
        with psycopg.connect(f"{PG_BASE}/postgres", autocommit=True) as c:
            c.execute(f'DROP DATABASE IF EXISTS "{DB_NAME}" WITH (FORCE)')
            c.execute(f'CREATE DATABASE "{DB_NAME}"')
            c.execute(f'ALTER DATABASE "{DB_NAME}" SET synchronous_commit = off')
    except psycopg.OperationalError as e:
        pytest.skip(f"Postgres not available: {e}")
    d = tmp_path_factory.mktemp("allmigrations")
    for f in (ROOT / "migrations").glob("*.sql"):
        shutil.copy(f, d / f.name)
    env = {**os.environ, "DATABASE_URL": DB_URL, "PYTHONPATH": str(ROOT), "MIGRATIONS_DIR": str(d)}
    py = sys.executable
    for args in (["-m", "etl.migrate"], ["-m", "etl.projection_cohorts"], ["-m", "etl.projection_drivers", "seed"], ["-m", "etl.projection_pnl", "seed"]):
        r = subprocess.run([py, *args], cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
        if r.returncode:
            pytest.skip(f"could not prepare the page database ({' '.join(args)}): {r.stderr[-300:]}")
    from werkzeug.serving import make_server
    from app import create_app
    from app.projection import rwdb
    app = create_app({"DATABASE_URL": DB_URL, "AUTH_DISABLED": True, "SECRET_KEY": "test-secret-key", "DEV_INSECURE_COOKIES": True, "DB_POOL_MAX": 4,
                      "PROJ_RATE_MUTATIONS_PER_MIN": 100000, "PROJ_RATE_UPLOADS_PER_HOUR": 100000})
    srv = make_server("127.0.0.1", 0, app, threaded=True)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()
    app.extensions["pool"].close()
    rwdb.close_all()


@pytest.fixture(scope="module")
def browser():
    with pw.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:                                  # browser not installed
            pytest.skip(f"Chromium not available: {e}")
        yield b
        b.close()


@pytest.fixture()
def page(browser, server):
    """A fresh page per test with the console watched; the scenario's curve choice is reset before and after."""
    ctx = browser.new_context(viewport={"width": 1440, "height": 1000}, color_scheme="dark")
    pg = ctx.new_page()
    pg.problems = []
    pg.on("console", lambda m: pg.problems.append((m.type, m.text)) if m.type in ("error", "warning") else None)
    pg.on("pageerror", lambda e: pg.problems.append(("pageerror", str(e))))
    pg.on("response", lambda r: pg.problems.append((f"http{r.status}", r.url)) if r.status >= 400 else None)
    pg.base = server
    reset(pg)
    yield pg
    reset(pg)
    ctx.close()


def reset(pg, scenario="current"):
    pg.request.put(f"{pg.base}/api/projection/cohorts/levers", data={"scenario": scenario, "levers": {"curve_source": None, "asof_month": None, "n_cohorts": None}},
                   headers={"Content-Type": "application/json"})


def open_pnl(pg, scenario=None):
    pg.goto(f"{pg.base}/projection/pnl" + (f"?scenario={scenario}" if scenario else ""), wait_until="networkidle")
    pg.wait_for_selector(".cp-table")


def kpi(pg, label):
    return pg.locator(".pg-tldr .pg-kpi", has_text=re.compile(label, re.I)).first.locator(".v").inner_text()


def uses(pg):
    return pg.locator(".pg-tldr .cp-active").first.inner_text().replace("\n", " ")


def wait_for_uses(pg, text):
    pg.wait_for_function("t => { const e = document.querySelector('.pg-tldr .cp-active'); return e && e.innerText.includes(t); }", arg=text, timeout=15000)
    pg.wait_for_selector(".cp-table")


def allow_plan_edit(pg):
    """The first edit of a tab session on the plan of record asks whether to edit it or make a copy; choose to edit it."""
    ask = pg.locator(".pg-por-ask [data-choice=edit]")
    ask.wait_for(state="visible", timeout=5000)
    ask.click()


def shoot(pg, name):
    keep = os.environ.get("PICKER_SHOTS_DIR")
    if keep:
        Path(keep).mkdir(parents=True, exist_ok=True)
        pg.screenshot(path=str(Path(keep) / name), full_page=True)


def money_m(text):
    return float(re.search(r"\$([\d.]+)M", text).group(1))


# ---------------------------------------------------------------------------------------------------------------- the toggle

def test_each_option_changes_the_pnl_and_the_headline_says_which_curve_is_used(page):
    open_pnl(page)
    assert uses(page) == "Repeat revenue uses: Current, last 3 cohorts"
    base_noi = money_m(kpi(page, "net operating income"))
    base_rev = money_m(kpi(page, "FY27 revenue"))
    shoot(page, "pnl_dark_current.png")

    page.locator(".cp-controls .seg", has_text="Goal").click()
    page.get_by_role("button", name="Use this curve").click()
    allow_plan_edit(page)
    wait_for_uses(page, "Goal, saved Sep 2026")
    goal_noi, goal_rev = money_m(kpi(page, "net operating income")), money_m(kpi(page, "FY27 revenue"))
    assert goal_noi > base_noi and goal_rev > base_rev
    assert page.locator(".cp-table tbody tr.active").inner_text().startswith("Goal")
    shoot(page, "pnl_dark_goal.png")

    page.locator(".cp-controls .seg", has_text="As of month").click()
    assert page.locator("#cp-asof").input_value() == "2026-08-01"                      # the default month
    page.get_by_role("button", name="Use this curve").click()
    wait_for_uses(page, "As of Aug 2026, last 3 cohorts")
    asof_noi = money_m(kpi(page, "net operating income"))
    assert base_noi < asof_noi < goal_noi
    shoot(page, "pnl_dark_asof.png")

    page.locator(".cp-controls .seg", has_text="Current").click()
    page.get_by_role("button", name="Use this curve").click()
    wait_for_uses(page, "Current, last 3 cohorts")
    assert money_m(kpi(page, "net operating income")) == base_noi
    assert not page.problems, page.problems


def test_clicking_a_row_applies_it_and_six_cohorts_changes_the_figures(page):
    open_pnl(page)
    base = money_m(kpi(page, "net operating income"))
    page.locator(".cp-table tbody tr", has_text="As of Aug 2026").click()
    allow_plan_edit(page)
    wait_for_uses(page, "As of Aug 2026, last 3 cohorts")
    three = money_m(kpi(page, "net operating income"))
    page.locator(".cp-controls .segs", has=page.locator(".seg", has_text="6")).locator(".seg", has_text="6").click()
    page.wait_for_selector(".cp-table")
    page.locator(".cp-table tbody tr", has_text="As of Aug 2026").click()
    wait_for_uses(page, "As of Aug 2026, last 6 cohorts")
    six = money_m(kpi(page, "net operating income"))
    assert three != six and three != base
    assert not page.problems, page.problems


def test_the_choice_survives_a_reload_and_each_scenario_keeps_its_own(page):
    page.request.post(f"{page.base}/api/projection/scenarios", data={"name": "Picker copy", "clone_from": "current"}, headers={"Content-Type": "application/json"})
    page.request.put(f"{page.base}/api/projection/cohorts/levers", data={"scenario": "picker-copy", "levers": {"curve_source": "live"}},
                     headers={"Content-Type": "application/json"})                       # the copy saves its own choice (a scenario that never did follows its base)
    open_pnl(page)
    page.locator(".cp-table tbody tr", has_text="Goal").click()
    allow_plan_edit(page)
    wait_for_uses(page, "Goal, saved Sep 2026")
    page.reload(wait_until="networkidle")
    page.wait_for_selector(".cp-table")
    assert uses(page).endswith("Goal, saved Sep 2026")
    assert page.locator(".cp-controls .seg[aria-pressed=true]").inner_text() == "Goal"
    open_pnl(page, "picker-copy")
    assert uses(page).endswith("Current, last 3 cohorts") and "Picker copy" in page.locator(".cp-foot .where").inner_text()
    page.locator(".cp-table tbody tr", has_text="As of Aug 2026").click()
    wait_for_uses(page, "As of Aug 2026")
    open_pnl(page, "current")
    assert uses(page).endswith("Goal, saved Sep 2026")                                  # the plan of record kept its own choice
    page.request.delete(f"{page.base}/api/projection/scenarios/picker-copy", data={"confirm": "picker-copy"}, headers={"Content-Type": "application/json"})
    assert not page.problems, page.problems


def test_the_saved_choice_is_audited_and_the_compare_numbers_equal_the_pnl_after_saving(page):
    open_pnl(page)
    cmp_before = page.request.get(f"{page.base}/api/projection/cohorts/curve-compare?asof=2026-08&n=3").json()
    asof = cmp_before["options"][2]
    page.locator(".cp-table tbody tr", has_text="As of Aug 2026").click()
    allow_plan_edit(page)
    wait_for_uses(page, "As of Aug 2026")
    run = page.request.get(f"{page.base}/api/projection/run").json()
    assert run["headline"]["fy"]["2027"]["noi"] == pytest.approx(asof["fy"]["noi"], rel=1e-9)
    assert run["headline"]["lifetime_multiple"] == pytest.approx(asof["lifetime"], rel=1e-9)
    audit = page.request.get(f"{page.base}/api/projection/audit?scenario=current&limit=5").json()["audit"]
    assert any(a["action"] == "scenario.params" and a["new"]["params"]["cohorts"].get("asof_month") == "2026-08-01" for a in audit)
    assert not page.problems, page.problems


def test_the_default_curve_leaves_the_pnl_exactly_as_it_was(page):
    """Current with 3 cohorts is the model's own default: its figures are the headline's, so nothing moves until a choice is saved."""
    cmp = page.request.get(f"{page.base}/api/projection/cohorts/curve-compare?asof=2026-08&n=3").json()
    run = page.request.get(f"{page.base}/api/projection/run").json()
    cur = cmp["options"][0]
    assert cur["fy"]["noi"] == pytest.approx(run["headline"]["fy"]["2027"]["noi"], rel=1e-12)
    assert cur["fy"]["total_revenue"] == pytest.approx(run["headline"]["fy"]["2027"]["revenue"], rel=1e-12)
    assert cur["lifetime"] == pytest.approx(run["headline"]["lifetime_multiple"], rel=1e-12)
    assert cur["fy"]["ev"]["3x"] == pytest.approx(run["headline"]["ev"]["3x"], rel=1e-12)


def test_a_choice_that_cannot_be_built_is_refused_on_the_page_with_the_reason(page):
    open_pnl(page)
    r = page.request.put(f"{page.base}/api/projection/cohorts/levers", data={"levers": {"curve_source": "asof", "asof_month": "2025-01"}},
                         headers={"Content-Type": "application/json"})
    assert r.status == 400 and "choose a month from" in r.json()["error"]
    page.problems.clear()                                                                 # the 400 above was on purpose


# ---------------------------------------------------------------------------------------------------------------- Cohorts page, layout, themes

def test_the_cohorts_page_shows_the_same_line_and_its_controls_save(page):
    page.goto(f"{page.base}/projection/cohorts", wait_until="networkidle")
    page.wait_for_selector(".pg-tldr")
    assert page.locator(".pg-tldr .cp-active").inner_text().replace("\n", " ") == "Repeat revenue uses: Current, last 3 cohorts"
    page.locator(".co-settings .cp-controls .seg", has_text="Goal").click()
    allow_plan_edit(page)
    page.wait_for_function("() => { const e = document.querySelector('.pg-tldr .cp-active'); return !!e && e.innerText.includes('Goal'); }", timeout=15000)
    assert "Goal, saved Sep 2026" in page.locator(".pg-tldr .cp-active").inner_text()
    shoot(page, "cohorts_dark_goal.png")
    assert not page.problems, page.problems


@pytest.mark.parametrize("path", ["/projection/pnl", "/projection/cohorts"])
def test_390px_has_no_sideways_scroll_and_a_clean_console_dark_and_light(browser, server, path):
    for scheme in ("dark", "light"):
        ctx = browser.new_context(viewport={"width": 390, "height": 844}, color_scheme=scheme)
        pg = ctx.new_page()
        problems = []
        pg.on("console", lambda m: problems.append((m.type, m.text)) if m.type in ("error", "warning") else None)
        pg.on("pageerror", lambda e: problems.append(("pageerror", str(e))))
        pg.on("response", lambda r: problems.append((f"http{r.status}", r.url)) if r.status >= 400 else None)
        pg.goto(server + path, wait_until="networkidle")
        pg.wait_for_selector(".cp-controls")
        pg.wait_for_timeout(500)
        wide = pg.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
        keep = os.environ.get("PICKER_SHOTS_DIR")
        if keep:
            Path(keep).mkdir(parents=True, exist_ok=True)
            pg.screenshot(path=str(Path(keep) / f"{path.strip('/').replace('/', '_')}_390_{scheme}.png"), full_page=True)
        ctx.close()
        assert wide <= 0, f"{path} {scheme}: the page is {wide}px wider than the screen"
        assert not problems, problems


# ---------------------------------------------------------------------------------------------------------------- upload checks and undo in the browser

COLS = "Cohort Month,New Customers,90-Day Repeat Rate,CAC,Payback Period (Months),First Order (Realized),First Order," + ",".join(f"M{i}" for i in range(1, 37))


def stored_cohort_csv(pg, month: str, **over) -> dict:
    """A one-cohort file in the sheet layout built from what the server holds for that month, with overrides: n (customers), fo (first order), m1 (month 1)."""
    rows = pg.request.get(f"{pg.base}/api/projection/cohorts/cohorts").json()["cohorts"]
    c = next(r for r in rows if r["month"] == f"{month}-01")
    cum = [("" if v is None else repr(v)) for v in c["cum"]]
    if "m1" in over:
        cum[0] = repr(over["m1"])
    fo = over.get("fo", c["first_order"])
    line = ",".join([month, str(over.get("n", c["new_customers"])), "0.5", "80", "", str(fo), str(fo)] + [x or "0" for x in cum])
    return {"name": "probe.csv", "mimeType": "text/csv", "buffer": (COLS + "\n" + line + "\n").encode()}


def cohort_state(pg):
    return pg.request.get(f"{pg.base}/api/projection/cohorts/cohorts").json()["cohorts"]


def open_cohorts(pg):
    pg.goto(f"{pg.base}/projection/cohorts", wait_until="networkidle")
    pg.wait_for_selector(".pg-tldr")


@pytest.mark.parametrize("label,over,fragment", [("99,999", {"fo": 99999}, "outside the allowed $5 to $500"), ("-5", {"fo": -5}, "negative"),
                                                  ("customers to 12", {"n": 12}, "new customers change from")])
def test_a_bad_file_shows_why_it_cannot_be_loaded_and_the_commit_stays_off(page, label, over, fragment):
    open_cohorts(page)
    before = cohort_state(page)
    page.get_by_role("button", name="Upload cohort export").click()
    page.set_input_files(".co-drop input", stored_cohort_csv(page, "2026-10", **over))
    page.wait_for_selector(".co-review")
    assert page.locator(".co-review .pg-chip.bad", has_text="Cannot be loaded").count() == 1
    assert fragment in page.locator(".co-review").inner_text()
    assert page.locator(".co-review .btn.primary").is_disabled() and page.locator(".co-review .btn.primary").inner_text() == "Cannot be loaded"
    assert "now," in page.locator(".co-review").inner_text() or "Repeat per first-order dollar" in page.locator(".co-review").inner_text()
    shoot(page, f"cohorts_dark_blocked_{label.replace(',', '').replace(' ', '_')}.png")
    assert cohort_state(page) == before
    assert not page.problems, page.problems


def test_a_sound_upload_can_be_undone_from_the_page_and_everything_goes_back(page):
    open_cohorts(page)
    before = cohort_state(page)
    assert page.get_by_role("button", name="Undo last upload").count() == 0                  # nothing to undo yet
    page.get_by_role("button", name="Upload cohort export").click()
    sep = next(r for r in before if r["month"] == "2026-09-01")
    page.set_input_files(".co-drop input", stored_cohort_csv(page, "2026-09", m1=sep["cum"][0] * 1.01))
    page.wait_for_selector(".co-review")
    assert page.locator(".co-review .pg-chip.good", has_text="Within the allowed range").count() >= 1
    assert page.locator(".co-review .btn.primary").is_enabled()
    page.locator(".co-review .btn.primary").click()
    page.wait_for_selector(".co-drop", state="detached")
    page.get_by_role("button", name="Undo last upload").wait_for()
    assert cohort_state(page) != before
    page.get_by_role("button", name="Undo last upload").click()
    page.get_by_role("button", name="Undo upload").click()
    page.get_by_role("button", name="Undo last upload").wait_for(state="detached")
    assert cohort_state(page) == before                                                      # exact
    assert not page.problems, page.problems


# ---------------------------------------------------------------------------------------------------------------- Goal curve: confirm, undo, restore

@pytest.fixture()
def goal_guard(page):
    """Leave the Goal curve as the test found it (later tests read it)."""
    orig = page.request.get(f"{page.base}/api/projection/cohorts/goal").json()["active"]
    yield orig
    now = page.request.get(f"{page.base}/api/projection/cohorts/goal").json()["active"]
    if now["id"] != orig["id"] and now.get("restored_from") != orig["id"]:
        page.request.post(f"{page.base}/api/projection/cohorts/goal/restore", data={"snapshot_id": orig["id"]}, headers={"Content-Type": "application/json"})


def goal_curve(pg):
    return pg.request.get(f"{pg.base}/api/projection/cohorts/curves").json()["goal"]


def test_saving_the_goal_asks_first_shows_what_changes_and_can_be_undone_from_the_toast(page, goal_guard):
    open_cohorts(page)
    original = goal_curve(page)
    page.get_by_role("button", name="Save current as Goal").click()
    dlg = page.locator("dialog")
    dlg.wait_for()
    text = dlg.inner_text()
    assert "Goal now, saved Sep 29, 2026" in text and "Current, becomes the Goal" in text and "1.88x" in text and "1.58x" in text and "-0.30x" in text
    assert "net operating income" in text.lower() and "-$560K*" in text and "Restore previous Goal puts it back" in text
    shoot(page, "cohorts_dark_goal_confirm.png")
    dlg.get_by_role("button", name="Cancel").click()
    assert goal_curve(page) == original                                                      # nothing happened
    page.get_by_role("button", name="Save current as Goal").click()
    page.locator("dialog").get_by_role("button", name="Replace Goal").click()
    page.locator(".pg-toast", has_text="Goal curve replaced").wait_for()
    assert goal_curve(page) != original
    page.locator(".pg-toast-act", has_text="Undo").click()
    page.locator(".pg-toast", has_text="Goal curve restored").wait_for()
    assert goal_curve(page) == pytest.approx(original)
    assert not page.problems, page.problems


def test_restore_previous_goal_follows_whether_there_is_one_and_then_puts_it_back(page, goal_guard):
    open_cohorts(page)
    has_previous = page.request.get(f"{page.base}/api/projection/cohorts/goal").json()["previous"] is not None
    assert page.get_by_role("button", name="Restore previous Goal").is_disabled() == (not has_previous)
    original = goal_curve(page)
    page.get_by_role("button", name="Save current as Goal").click()
    page.locator("dialog").get_by_role("button", name="Replace Goal").click()
    page.locator(".pg-toast", has_text="Goal curve replaced").wait_for()
    page.wait_for_selector(".pg-tldr")
    restore = page.get_by_role("button", name="Restore previous Goal")
    restore.wait_for()
    assert restore.is_enabled()
    restore.click()
    page.locator("dialog").get_by_role("button", name="Restore Goal").click()
    page.locator(".pg-toast", has_text="Goal curve restored").wait_for()
    assert goal_curve(page) == pytest.approx(original)
    audit = page.request.get(f"{page.base}/api/projection/audit?limit=10").json()["audit"]
    assert any(a["action"] == "cohorts.goal.restore" for a in audit) and any(a["action"] == "cohorts.goal.snapshot" for a in audit)
    assert not page.problems, page.problems
