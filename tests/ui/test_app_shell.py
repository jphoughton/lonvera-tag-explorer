"""App shell: stale banner, global range / stage / compare / Numbers-from controls, Back, deep links, 401, page layout, default landing and top navigation.

Split out of tests/ui/test_ui.py with no assertion changes. Shared helpers live in ui_helpers.py. Run: see the docstring of conftest.py.
"""
import re
from datetime import date, timedelta
from urllib.parse import parse_qs, urlencode, urlparse
import pytest
from playwright.sync_api import expect
from conftest import IS_MOCK, PASSWORD, SHOTS, USERNAME
from ui_helpers import *  # noqa: F401,F403

@pytest.mark.mock_only
def test_a_late_status_answer_does_not_wipe_the_range_label_note_and_active_count(page):
    """/api/status can come back AFTER the page's own data (a slow machine, a slow first request). The label, note and active-ads count that data set must stay:
    the re-sync that runs when the status arrives used to clear them and nothing set them again (seen as an empty #range-note / #range-label in about 1 run in 4)."""
    held = []
    page.route("**/api/status", lambda route: held.append(route))                   # the status request is held back until the data is on the page
    page.goto(f"/p/{SLUG}?from=2026-05-06&to=2026-06-10&active=1")
    wait_rows(page)
    expect(page.locator("#range-note")).to_contain_text("Impressions are available from Jun 20")
    expect(page.locator("#active-count")).to_contain_text("active ads")
    label = page.locator("#range-label").inner_text()
    assert label.strip()
    page.wait_for_function("() => true")
    for _ in range(100):
        if held:
            break
        page.wait_for_timeout(50)
    assert held, "the page never asked for /api/status"
    held[0].continue_()
    page.wait_for_selector("#data-through")                                          # the status arrived: syncControls ran in the same task
    page.evaluate("() => new Promise((r) => setTimeout(r, 0))")
    expect(page.locator("#range-note")).to_contain_text("Impressions are available from Jun 20")
    expect(page.locator("#active-count")).to_contain_text("active ads")
    assert page.locator("#range-label").inner_text() == label


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
    expect(page.locator("#level-title")).to_contain_text("Ideal customer profiles in")
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
        page.wait_for_function("() => /\\d/.test(document.querySelector('#range-label')?.textContent)")
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
    page.wait_for_function("() => /3 days/.test(document.querySelector('#range-label')?.textContent)")
    assert any(f"from={start.isoformat()}" in u and f"to={end.isoformat()}" in u for u in reqs)
    assert page.locator(".seg[aria-pressed=true][data-window]").count() == 0
    page.fill("#range-from", end.isoformat())
    page.fill("#range-to", end.isoformat())
    page.click("#range-apply")
    page.wait_for_function("() => /1 day/.test(document.querySelector('#range-label')?.textContent)")
    idle(page)
    open_first_row(page)
    expect(page.locator("#level-title")).to_contain_text("Ideal customer profiles in")
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
    expect(page.locator("#level-title")).to_contain_text("Ideal customer profiles in")
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


@pytest.mark.parametrize("width", [375, 390, 768, 1024, 1440, 2100])
def test_pages_never_scroll_horizontally_at_page_level(page, width):
    """phone to desktop: only tables may scroll sideways inside their own box, never the page (/, a product, Campaigns, Ads, compare, the ad page)"""
    page.set_viewport_size({"width": 1440, "height": 900})
    ad_path = ad_page_url(page)
    page.set_viewport_size({"width": width, "height": 800})
    paths = ["/", "/?platform=meta", f"/p/{SLUG}", f"/p/{SLUG}?view=campaigns&window=30d", f"/p/{SLUG}?view=ads&window=30d", f"/p/{SLUG}?window=7d&compare=prev&stage=testing",
             f"/p/{SLUG}?all=1&window=30d", f"{ad_path}?window=30d&compare=prev", "/?platform=meta&window=7d&compare=30d", "/?window=30d",
             "/campaigns?window=30d", "/?platform=all&window=30d", "/?platform=google&window=30d", "/campaigns?platform=google&window=30d",
             "/campaigns?platform=all&window=30d", "/campaigns?window=30d&compare=prev", "/?window=30d&compare=prev"]
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


# ------------------------------------------------------------------ default landing = Overview
@pytest.mark.mock_only
def test_bare_root_is_the_all_platforms_roll_up_and_every_link_back_to_meta_is_explicit(page, base_url):
    page.goto(f"{base_url}/")                                                                          # a bare root, as the top-bar link points to
    expect(page.locator("h1")).to_have_text("Overview")
    assert page.evaluate("() => location.search") == ""                                                # no parameter was added
    expect(platform_btn(page, "all")).to_have_attribute("aria-pressed", "true")
    expect(page.locator("#view-switch .ctl-name")).to_have_text("View by")
    assert [b.inner_text() for b in page.locator("#view-switch .seg").all()] == ["Platforms", "Campaigns"]
    expect(page.locator("#view-switch [data-platform-view=home]")).to_have_attribute("aria-pressed", "true")
    expect(page.locator("[data-testid=platform-overview] tbody tr")).to_have_count(6)
    wait_trend(page)                                                                                   # with the total-spend chart
    assert [d["label"] for d in bars_ds(trend_info(page))] == ["Spend"]
    expect(page.locator("#topbar a.navtab", has_text="Overview")).to_have_attribute("href", "/")                           # the top-bar Overview tab is the bare root
    # View by Campaigns: every campaign across both platforms, nothing to choose first
    page.locator("#view-switch [data-platform-view=campaigns]").click()
    expect(page).to_have_url(re.compile(r"/campaigns\?platform=all"))
    page.locator("[data-testid=all-campaigns] tbody tr[data-key]").first.wait_for()
    data = table_data(page, "all-campaigns")
    assert {r[data["heads"].index("platform")] for r in data["rows"]} == {"Meta", "Google"}
    page.locator("#view-switch [data-platform-view=home]").click()
    expect(page.locator("h1")).to_have_text("Overview")
    assert page.evaluate("() => location.search").find("platform") == -1                                # All is the default of '/': no platform parameter
    # the picker: Meta is the old Products landing, one click away and explicit in the URL
    platform_btn(page, "meta").click()
    expect(page).to_have_url(re.compile(r"/\?platform=meta"))
    expect(page.locator("h1")).to_have_text("Meta")
    page.locator("[data-testid=products-table] tbody tr").first.wait_for()
    platform_btn(page, "google").click()
    expect(page).to_have_url(re.compile(r"platform=google"))
    platform_btn(page, "all").click()                                                                  # and back to the default URL
    expect(page.locator("h1")).to_have_text("Overview")
    assert page.evaluate("() => location.search").find("platform") == -1
    page.locator("#topbar a.navtab", has_text="Overview").click()                                                          # the top-bar link from any page lands on All
    page.goto(f"{base_url}/p/{SLUG}")
    page.locator("#topbar a.navtab", has_text="Overview").click()
    expect(page.locator("h1")).to_have_text("Overview")


@pytest.mark.mock_only
def test_existing_deep_links_keep_their_meaning(page, base_url):
    page.goto(f"{base_url}/campaigns?window=30d")                                                       # no platform on /campaigns = Meta's Campaigns, as before
    expect(page.locator("[data-testid=platform-campaigns]")).to_be_visible()
    expect(platform_btn(page, "meta")).to_have_attribute("aria-pressed", "true")
    page.goto(f"{base_url}/?platform=meta&window=7d&source=upstack&basis=all")                          # Meta landing with every parameter
    expect(page.locator("h1")).to_have_text("Meta")
    assert qparams(page)["source"] == "upstack" and qparams(page)["basis"] == "all" and qparams(page)["window"] == "7d"
    page.goto(f"{base_url}/?window=7d&source=upstack")                                                  # a root with parameters but no platform is the roll-up with them applied
    expect(page.locator("h1")).to_have_text("Overview")
    page.goto(f"{base_url}/p/{SLUG}?window=14d")                                                        # product pages are untouched
    expect(page.locator("h1")).not_to_have_text("Overview")
    crumbs = page.locator(".crumbs li").all_inner_texts()
    assert crumbs[0] == "Meta" and len(crumbs) == 2, crumbs
    page.locator(".crumbs a").first.click()                                                             # back up the hierarchy = Meta's Products table, not the roll-up
    expect(page.locator("h1")).to_have_text("Meta")
    page.goto(f"{base_url}/p/{SLUG}")
    page.locator("#back").click()
    expect(page.locator("h1")).to_have_text("Meta")


# ---------------------------------------------------------------- top navigation (js/nav.js): sections, page tabs, tag search
SECTION_TABS = {
    "performance": ["Overview", "Meta", "Google", "Campaigns", "Trends", "Email"],
    "creative": ["Priorities", "Production", "Test to scale"],
}


def open_idle(page, url):
    page.goto(url)
    page.wait_for_selector("#view > *")
    idle(page)


def test_top_nav_has_three_sections_and_the_active_sections_tabs(page):
    open_idle(page, "/trends")
    assert page.locator("#topbar a.navgroup").all_inner_texts() == ["Performance", "Creative", "Plan"]
    expect(page.locator("#topbar a.navgroup[aria-current=true]")).to_have_text("Performance")
    assert page.locator("#subnav a.navtab").all_inner_texts() == SECTION_TABS["performance"]
    expect(page.locator("#subnav a.navtab[aria-current=page]")).to_have_text("Trends")
    page.locator("[data-testid=nav-section-creative]").click()
    page.wait_for_url(re.compile(r"/priorities$"))
    idle(page)
    expect(page.locator("#topbar a.navgroup[aria-current=true]")).to_have_text("Creative")
    assert page.locator("#subnav a.navtab").all_inner_texts() == SECTION_TABS["creative"]
    expect(page.locator("#subnav a.navtab[aria-current=page]")).to_have_text("Priorities")
    page.locator("[data-testid=nav-production]").click()
    page.wait_for_url(re.compile(r"/priorities/production$"))
    expect(page.locator("#subnav a.navtab[aria-current=page]")).to_have_text("Production")
    assert page.locator("[data-testid=nav-projections]").count() == 0                                   # Plan's own tabs are on the Projections page
    assert page.locator("a.navgroup[data-section=plan]").get_attribute("href") == "/projection"


@pytest.mark.parametrize("url,section,tab", [
    ("/", "Performance", "Overview"), ("/?platform=meta", "Performance", "Meta"), ("/?platform=google", "Performance", "Google"), ("/google", "Performance", "Google"),
    ("/campaigns", "Performance", "Campaigns"), ("/campaigns?platform=all", "Performance", "Campaigns"), ("/trends", "Performance", "Trends"), ("/email", "Performance", "Email"),
    (f"/p/{SLUG}", "Performance", "Meta"), ("/priorities", "Creative", "Priorities"), ("/priorities/production", "Creative", "Production"),
    ("/promotions", "Creative", "Test to scale"), ("/lookup", "Creative", None),
])
def test_every_page_url_still_loads_and_lights_its_section_and_tab(page, url, section, tab):
    open_idle(page, url)
    expect(page.locator("#topbar a.navgroup[aria-current=true]")).to_have_text(section)
    if tab:
        expect(page.locator("#subnav a.navtab[aria-current=page]")).to_have_text(tab)
    else:
        assert page.locator("#subnav a.navtab[aria-current=page]").count() == 0
    assert page.locator("#controls").count() == 1 and page.locator("[data-testid=blank-page]").count() == 0


def test_tag_search_box_is_on_every_page_and_opens_the_lookup_page(page):
    for url in ("/", "/trends", "/priorities", f"/p/{SLUG}"):
        open_idle(page, url)
        expect(page.locator("[data-testid=nav-lookup-input]")).to_be_visible()
    page.locator("[data-testid=nav-lookup-input]").fill("Lonvera-Boneflex-Video-2.1")
    page.locator("[data-testid=nav-lookup-input]").press("Enter")
    page.wait_for_selector("[data-testid=tl-page]")
    idle(page)
    assert parse_qs(urlparse(page.url).query)["q"] == ["Lonvera-Boneflex-Video-2.1"]
    expect(page.locator("[data-testid=tl-input]")).to_have_value("Lonvera-Boneflex-Video-2.1")
    open_idle(page, "/lookup")                                                                             # the page itself still works with the box empty
    expect(page.locator("[data-testid=tl-empty]")).to_be_visible()


@pytest.mark.parametrize("url", ["/", "/trends", "/priorities/production", "/promotions", "/lookup"])
def test_two_row_header_never_scrolls_the_page_sideways_on_a_phone(page, url):
    page.set_viewport_size({"width": 375, "height": 812})
    open_idle(page, url)
    assert page.evaluate("() => document.documentElement.scrollWidth <= document.documentElement.clientWidth"), "page scrolls sideways at 375px"
    assert page.locator("#subnav").evaluate("e => e.scrollWidth >= e.clientWidth")                         # the tab row scrolls inside itself when it is wider than the phone
    expect(page.locator("#subnav a.navtab[aria-current=page], #subnav a.navtab").first).to_be_visible()


def test_filter_bar_sits_flush_under_the_two_row_header(page):
    open_idle(page, "/?platform=meta")
    h = page.evaluate("() => ({bar: document.querySelector('#topbar').getBoundingClientRect().height, top: document.querySelector('#controls').getBoundingClientRect().top})")
    assert abs(h["bar"] - 76) <= 2 and abs(h["top"] - h["bar"]) <= 2, h
