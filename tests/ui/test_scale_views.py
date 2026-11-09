"""Test to scale, the two simple views (What should be scaled, Performance in scaling) in the browser, against the mock server (tests/ui/mock_promotions.py: the real
payload builders app.scale_views.build_should_scale / build_in_scaling over made-up creatives).

Mock facts used below. View 1 (Meta basis): scaling averages Vita Bull 0.80x / $60 and Beef Liver 0.80x / $60, Boneflex none; seven creatives in testing spent today:
Bull-901 ($1,500, 60 purchases, 1.40x: high), Bull-902 ($800, 30, 1.25x: medium), Liver-903 ($600, 8, 1.25x: low) qualify, in that order; Liver-904 (0.78x, CPA $75) is a
near miss, Bull-907 (0.60x) a miss, Bull-905 ($120) is under the spend minimum, Boneflex-906 has no scaling average; Liver-908 stopped five days ago and is not listed.
View 2: the page's 56 moves of the last 30 days (45 of them one bulk day).
"""
import re
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.sync_api import expect

from conftest import SHOTS

pytestmark = pytest.mark.mock_only

TABS = "[data-testid=sv-tabs]"
TABLE = "[data-testid=sv-table]"
MISSES = "[data-testid=sv-misses]"
TOTALS = "[data-testid=sv-totals]"


# ------------------------------------------------------------------ helpers
def idle(page):
    page.wait_for_function("() => !document.querySelector('#view').hasAttribute('aria-busy')")


def goto(page, qs="", view=None):
    q = [x for x in (qs.lstrip("?"), f"view={view}" if view else "") if x]
    page.goto("/promotions" + ("?" + "&".join(q) if q else ""))
    page.wait_for_selector(TABS)
    page.wait_for_selector("[data-testid=sv-summary]")
    idle(page)


def params(page):
    return {k: v[0] for k, v in parse_qs(urlparse(page.url).query).items()}


def table(page, root):
    return page.evaluate(
        """(root) => {
          const t = document.querySelector(`${root} table`);
          if (!t) return {heads: [], rows: [], keys: [], foot: []};
          const heads = [...t.querySelectorAll(':scope > thead th')].map(th => th.dataset.col || '');
          const trs = [...t.querySelectorAll(':scope > tbody > tr[data-key]:not(.expandrow)')];
          const foot = [...t.querySelectorAll(':scope > tfoot td')].map(td => td.innerText.trim());
          return {heads, rows: trs.map(tr => [...tr.children].map(td => td.innerText.trim())), keys: trs.map(tr => tr.dataset.key), foot,
                  empty: !!t.querySelector(':scope > tbody > tr.emptyrow')};
        }""",
        root,
    )


def column(page, root, key):
    d = table(page, root)
    i = d["heads"].index(key)
    return [r[i] for r in d["rows"]]


def num(s):
    s = (s or "").strip()
    if s in ("", "–", "-"):
        return None
    return float(re.sub(r"[^0-9.\-]", "", s.replace("≥", "")) or "nan")


def api(page, qs):
    r = page.context.request.get("/api/promotions/scale" + qs)
    assert r.status == 200, r.status
    return r.json()


def shot(page, name):
    page.screenshot(path=str(SHOTS / f"{name}.png"), full_page=True)


# ------------------------------------------------------------------ the tabs
def test_the_page_opens_on_what_should_be_scaled_and_has_three_tabs(page):
    goto(page)
    assert params(page).get("window") == "30d"
    labels = page.locator(f"{TABS} a").all_inner_texts()
    assert labels == ["What should be scaled", "Performance in scaling", "All moves, with detail"]
    expect(page.locator("[data-testid=sv-tab-should_scale]")).to_have_attribute("aria-pressed", "true")
    expect(page.locator("[data-testid=sv-tab-should_scale]")).to_have_attribute("aria-current", "page")
    expect(page.locator("h1")).to_have_text("Test to scale")
    expect(page.locator("#level-title")).to_have_text("What should be scaled")
    expect(page).to_have_title(re.compile("What should be scaled"))
    page.locator("[data-testid=sv-tab-in_scaling]").click()
    page.wait_for_selector("[data-testid=sv-caption]")
    idle(page)
    assert params(page)["view"] == "in_scaling"
    expect(page.locator("[data-testid=sv-tab-in_scaling]")).to_have_attribute("aria-pressed", "true")
    expect(page).to_have_title(re.compile("Performance in scaling"))
    page.locator("[data-testid=sv-tab-detail]").click()
    page.wait_for_selector("[data-testid=tts-strip]")
    assert params(page)["view"] == "detail" and page.locator(f"{TABS} a[aria-pressed=true]").inner_text() == "All moves, with detail"
    page.locator("[data-testid=sv-tab-should_scale]").click()
    page.wait_for_selector("[data-testid=sv-rule]")
    assert page.locator("[data-testid=sv-tab-should_scale]").get_attribute("aria-pressed") == "true"


def test_tabs_keep_the_numbers_from_choice_and_the_product(page):
    goto(page, "?source=upstack&basis=all&product=vita-bull", "should_scale")
    page.locator("[data-testid=sv-tab-in_scaling]").click()
    page.wait_for_selector("[data-testid=sv-caption]")
    p = params(page)
    assert p["source"] == "upstack" and p["basis"] == "all" and p["product"] == "vita-bull" and p["view"] == "in_scaling"
    assert page.locator("[data-testid=src-badge]").first.inner_text() == "Numbers: Upstack · all orders"
    assert {r for r in column(page, TABLE, "product")} == {"Vita Bull"}


# ------------------------------------------------------------------ view 1: what should be scaled
def test_what_should_be_scaled_lists_the_three_that_qualify_in_rank_order(page):
    goto(page)
    rule = page.locator("[data-testid=sv-rule]").inner_text()
    assert "in testing" in rule and "have not moved" in rule and "$500" in rule and "5 purchases" in rule and "scaling average" in rule and "last 3 days" in rule
    assert "screen, not a verdict" in page.locator("[data-testid=sv-screen]").inner_text() and "50 purchases" in page.locator("[data-testid=sv-screen]").inner_text()
    d = table(page, TABLE)
    assert d["heads"] == ["rank", "display", "product", "days", "spend", "purchases", "roas", "cpa", "vs", "last7", "conf"]
    names = column(page, TABLE, "display")
    assert [n.split("\n")[0] for n in names] == ["Bull-901-Hook1", "Bull-902-Hook2", "Liver-903-Hook1"]
    assert column(page, TABLE, "rank") == ["1", "2", "3"]
    assert column(page, TABLE, "product") == ["Vita Bull", "Vita Bull", "Beef Liver"]
    assert column(page, TABLE, "spend") == ["$1,500", "$800", "$600"] and column(page, TABLE, "purchases") == ["60", "30", "8"]
    assert [num(x) for x in column(page, TABLE, "roas")] == [1.40, 1.25, 1.25] and [num(x) for x in column(page, TABLE, "cpa")] == [25, 27, 75]
    assert column(page, TABLE, "vs") == ["above", "above", "above"]
    assert [num(x) for x in column(page, TABLE, "last7")] == [1.40, 1.25, 1.25]
    assert column(page, TABLE, "conf") == ["High", "Medium", "Low"]
    assert all(num(x) > 0 for x in column(page, TABLE, "days"))
    assert page.locator("[data-testid=sv-count]").inner_text() == "3 of 7 creatives in testing qualify"
    for n in ("Bull-905", "Liver-904", "Bull-907", "Liver-908", "Boneflex-906"):
        assert n not in " ".join(names)
    # every name links to the creative page
    links = page.locator(f"{TABLE} [data-testid=sv-creative-link]")
    assert links.count() == 3 and re.fullmatch(r"/ad/\d+.*", links.first.get_attribute("href"))
    shot(page, "sv_01_should_scale")


def test_scaling_averages_are_shown_next_to_the_table_and_tie_to_the_api(page):
    goto(page)
    d = api(page, "?view=should_scale")
    bars = {b["slug"]: b for b in d["bars"]}
    assert set(bars) == {"vita-bull", "beef-liver"}
    t = page.locator("[data-testid=sv-bar-vita-bull]").inner_text()
    assert t.startswith("Scaling average for Vita Bull: 0.80") and "$60" in t
    assert page.locator("[data-testid=sv-bar-beef-liver]").inner_text().startswith("Scaling average for Beef Liver: 0.80")
    assert page.locator("[data-testid=sv-bar-boneflex]").count() == 0
    assert bars["vita-bull"]["roas"] == pytest.approx(0.8) and bars["vita-bull"]["cpa"] == pytest.approx(60.06, abs=0.01)


def test_table_ties_to_the_api(page):
    goto(page)
    d = api(page, "?view=should_scale")
    assert [r["family_key"] for r in d["rows"]] == [k for k in table(page, TABLE)["keys"]]
    assert [round(r["test"]["roas"], 2) for r in d["rows"]] == [num(x) for x in column(page, TABLE, "roas")]
    assert [r["confidence"]["level"].capitalize() for r in d["rows"]] == column(page, TABLE, "conf")


def test_confidence_chips_say_what_they_rest_on(page):
    goto(page)
    chips = page.locator(f"{TABLE} .sv-conf")
    assert [chips.nth(i).get_attribute("data-conf") for i in range(3)] == ["high", "medium", "low"]
    assert "more than 50 purchases" in chips.nth(0).get_attribute("title") and "20 to 50" in chips.nth(1).get_attribute("title") and "under 20" in chips.nth(2).get_attribute("title")


def test_the_three_rule_inputs_default_edit_and_live_in_the_url(page):
    goto(page)
    assert page.locator("#sv-min-spend").input_value() == "500" and page.locator("#sv-min-purchases").input_value() == "5" and page.locator("#sv-bar-pct").input_value() == "100"
    assert page.locator("[data-testid=sv-reset]").count() == 0
    page.locator("#sv-min-spend").fill("1000")
    page.locator("#sv-min-spend").press("Enter")
    page.wait_for_function("() => document.querySelectorAll('[data-testid=sv-table] tbody tr[data-key]').length === 1")
    assert params(page)["min_test_spend"] == "1000" and column(page, TABLE, "spend") == ["$1,500"]
    assert "$1,000" in page.locator("[data-testid=sv-rule]").inner_text()
    assert page.locator("[data-testid=sv-reset]").count() == 1
    page.locator("#sv-min-spend").fill("0")
    page.locator("#sv-min-spend").press("Enter")
    page.wait_for_function("() => document.querySelectorAll('[data-testid=sv-table] tbody tr[data-key]').length === 4")          # Bull-905 ($120, 6 purchases) joins
    assert params(page)["min_test_spend"] == "0"
    page.locator("[data-testid=sv-reset]").click()
    page.wait_for_function("() => document.querySelectorAll('[data-testid=sv-table] tbody tr[data-key]').length === 3")
    assert "min_test_spend" not in params(page) and page.locator("#sv-min-spend").input_value() == "500"
    page.locator("#sv-min-purchases").fill("40")
    page.locator("#sv-min-purchases").press("Enter")
    page.wait_for_function("() => document.querySelectorAll('[data-testid=sv-table] tbody tr[data-key]').length === 1")
    assert params(page)["min_purchases"] == "40" and column(page, TABLE, "purchases") == ["60"]
    page.locator("[data-testid=sv-reset]").click()
    page.wait_for_function("() => document.querySelectorAll('[data-testid=sv-table] tbody tr[data-key]').length === 3")
    page.locator("#sv-min-purchases").fill("0")
    page.locator("#sv-min-purchases").press("Enter")
    page.wait_for_function("() => document.querySelectorAll('[data-testid=sv-table] tbody tr[data-key]').length === 3")           # nothing else has under 5 purchases and $500


def test_return_bar_is_a_percentage_and_cost_per_purchase_also_counts(page):
    goto(page, "?bar_pct=200")
    assert params(page)["bar_pct"] == "200" and page.locator("#sv-bar-pct").input_value() == "200"
    # the bar is 1.60x: Bull-901 (1.40x) and Bull-902 (1.25x) are under it on ROAS but their CPA ($25, $27) is inside the $30 bar: they qualify on cost per purchase only
    # ranked by revenue above the bar x spend: both are under the bar on revenue, so the one that is less under (Bull-902, -$280 against -$300) is first
    assert [n.split("\n")[0] for n in column(page, TABLE, "display")] == ["Bull-902-Hook2", "Bull-901-Hook1"]
    assert all("cost per purchase only" in v for v in column(page, TABLE, "vs"))
    goto(page, "?bar_pct=50")
    names = [n.split("\n")[0] for n in column(page, TABLE, "display")]
    assert "Liver-904-Hook3" in names and "Bull-907-Hook2" in names                       # bar 0.40x: the 0.78x and 0.60x creatives qualify
    assert "50% of" in page.locator("[data-testid=sv-rule]").inner_text()


def test_empty_state_in_plain_words_with_the_nearest_misses(page):
    goto(page, "?bar_pct=1000")
    assert table(page, TABLE)["keys"] == []
    assert page.locator("[data-testid=sv-count]").inner_text() == "0 of 7 creatives in testing qualify"
    empty = page.locator("#view .empty").first.inner_text()
    assert "Nothing in testing qualifies right now" in empty and "Lower a minimum or the return bar" in empty
    expect(page.get_by_role("heading", name="Nearest misses")).to_be_visible()
    d = table(page, MISSES)
    assert [n.split("\n")[0] for n in column(page, MISSES, "display")] == ["Bull-901-Hook1", "Bull-902-Hook2", "Liver-903-Hook1", "Liver-904-Hook3", "Bull-907-Hook2"]
    assert len(d["keys"]) == 5 and set(column(page, MISSES, "vs")) <= {"near", "below"}
    shot(page, "sv_02_empty")


def test_a_near_miss_is_worded_near_and_a_far_one_below(page):
    d = api(page, "?view=should_scale&bar_pct=1000")
    by = {r["family_key"]: r["vs_scaling"] for r in d["near_misses"]}
    assert by["liver-904-hook3"]["ratio"] == pytest.approx(0.778 / 8, abs=1e-3)
    d0 = api(page, "?view=should_scale&bar_pct=100&min_test_spend=0")
    assert d0["summary"]["qualify"] == 4


def test_product_with_no_scaling_average_says_so(page):
    goto(page, "?product=boneflex")
    assert table(page, TABLE)["keys"] == [] and "Nothing in testing qualifies" in page.locator("#view .empty").first.inner_text()
    assert "Boneflex has no scaling spend in the last 30 days" in page.locator("[data-testid=sv-note]").first.inner_text()


def test_product_chips_filter_the_table(page):
    goto(page)
    page.locator("[data-testid=sv-product-beef-liver]").click()
    page.wait_for_function("() => document.querySelectorAll('[data-testid=sv-table] tbody tr[data-key]').length === 1")
    assert column(page, TABLE, "product") == ["Beef Liver"] and params(page)["product"] == "beef-liver"
    page.locator("[data-testid=sv-product-all]").click()
    page.wait_for_function("() => document.querySelectorAll('[data-testid=sv-table] tbody tr[data-key]').length === 3")


def test_numbers_basis_changes_purchases_revenue_labels_and_the_bar(page):
    goto(page, "?source=upstack&basis=nc")
    page.wait_for_function("() => document.querySelectorAll('[data-testid=sv-table] tbody tr[data-key]').length === 2")      # Liver-903 drops under 5 new customers (8 x 0.55 = 4.4)
    assert [n.split("\n")[0] for n in column(page, TABLE, "display")] == ["Bull-901-Hook1", "Bull-902-Hook2"]
    heads = page.locator(f"{TABLE} thead th").all_inner_texts()
    assert any("New customers" in h for h in heads) and any("NC ROAS" in h for h in heads)
    assert column(page, TABLE, "spend") == ["$1,500", "$800"] and column(page, TABLE, "purchases") == ["33", "17"]
    assert page.locator("[data-testid=src-badge]").first.inner_text() == "Numbers: Upstack · new customers"
    d = api(page, "?view=should_scale&source=upstack&basis=nc")
    assert [r["family_key"] for r in d["rows"]] == table(page, TABLE)["keys"]
    assert {b["slug"]: b["roas"] for b in d["bars"]}["vita-bull"] == pytest.approx(0.44, abs=0.01)
    goto(page, "?source=upstack&basis=all")
    assert [num(x) for x in column(page, TABLE, "roas")][0] == pytest.approx(1.40 * 1.7, abs=0.01)


def test_sorting_a_column_reorders_the_rows(page):
    goto(page)
    page.locator(f"{TABLE} thead th[data-col=spend] button").click()
    first = [num(x) for x in column(page, TABLE, "spend")]
    assert first == sorted(first) or first == sorted(first, reverse=True)
    page.locator(f"{TABLE} thead th[data-col=spend] button").click()
    second = [num(x) for x in column(page, TABLE, "spend")]
    assert second == list(reversed(first))


# ------------------------------------------------------------------ view 2: performance in scaling
def test_performance_in_scaling_lists_the_moved_creatives_biggest_spend_first(page):
    goto(page, "", "in_scaling")
    d = table(page, TABLE)
    assert d["heads"] == ["display", "product", "moved", "since", "spend", "purchases", "roas", "cpa", "l7roas", "l7cpa", "vs", "status"]
    assert len(d["keys"]) == 56 and page.locator("[data-testid=sv-count]").inner_text().startswith("56 creatives moved to scaling")
    spends = [num(x) for x in column(page, TABLE, "spend")]
    assert spends == sorted(spends, reverse=True)
    assert set(column(page, TABLE, "status")) <= {"holding", "fading", "stopped", "too early"}
    assert set(column(page, TABLE, "vs")) <= {"better", "about the same", "worse", "too early", "no baseline"}
    assert "Creatives that moved from testing to scaling" in page.locator("[data-testid=sv-caption]").inner_text()
    assert page.locator("[data-testid=sv-bulk-chip]").count() == 45 and "Oct 22 (45)" in page.locator("[data-testid=sv-bulk-note]").inner_text()
    shot(page, "sv_03_in_scaling")


def test_in_scaling_rows_tie_to_the_api_and_status_words_follow_the_rules(page):
    goto(page, "", "in_scaling")
    d = api(page, "?view=in_scaling&window=30d")
    assert [r["family_key"] for r in d["rows"]] == table(page, TABLE)["keys"]
    assert column(page, TABLE, "status") == [r["status"] for r in d["rows"]]
    for r in d["rows"]:
        l7 = r["last7"]["roas"]
        since = r["scale"]["roas"]
        if r["status"] == "too early":
            assert r["scale"]["spend"] < 200 or r["scale"]["days"] < 3
        elif r["status"] == "stopped":
            assert r["last7"]["spend"] == 0
        elif r["status"] == "fading":
            assert l7 < 0.9 * since
        else:
            assert l7 >= 0.9 * since
        if r["vs_testing"]["ratio"] is not None:
            w = r["vs_testing"]["ratio"]
            assert r["vs_testing"]["word"] == ("better" if w >= 1.10 else "about the same" if w >= 0.90 else "worse")
    assert {r["status"] for r in d["rows"]} >= {"holding", "too early"}


def test_a_total_row_per_product_with_spend_weighted_roas(page):
    goto(page, "", "in_scaling")
    t = table(page, TOTALS)
    d = api(page, "?view=in_scaling&window=30d")
    assert t["keys"] == [x["slug"] for x in d["totals"]] and len(t["keys"]) == 3
    for x, row in zip(d["totals"], t["rows"]):
        rows = [r for r in d["rows"] if r["product"]["slug"] == x["slug"]]
        sp, rv = sum(r["scale"]["spend"] for r in rows), sum(r["scale"]["revenue"] for r in rows)
        assert num(row[2]) == pytest.approx(sp, abs=1) and num(row[4]) == pytest.approx(rv / sp, abs=0.006)
        assert row[1] == str(len(rows))
    assert t["foot"][0] == "All products" and num(t["foot"][1]) == 56
    assert num(t["foot"][2]) == pytest.approx(sum(r["scale"]["spend"] for r in d["rows"]), abs=1)


def test_hide_bulk_moves_removes_them_from_the_rows_and_the_totals(page):
    goto(page, "", "in_scaling")
    page.locator("[data-testid=sv-hide-bulk]").check()
    page.wait_for_function("() => document.querySelectorAll('[data-testid=sv-table] tbody tr[data-key]').length === 11")
    assert params(page)["hide_bulk"] == "1" and page.locator("[data-testid=sv-bulk-chip]").count() == 0
    assert "Bulk moves hidden" in page.locator("[data-testid=sv-bulk-note]").inner_text()
    assert num(table(page, TOTALS)["foot"][1]) == 11
    page.locator("[data-testid=sv-hide-bulk]").uncheck()
    page.wait_for_function("() => document.querySelectorAll('[data-testid=sv-table] tbody tr[data-key]').length === 56")


def test_the_range_bar_applies_to_performance_in_scaling(page):
    goto(page, "?window=7d", "in_scaling")
    n7 = len(table(page, TABLE)["keys"])
    assert n7 < 56 and params(page)["window"] == "7d"
    goto(page, "?window=launch", "in_scaling")
    assert len(table(page, TABLE)["keys"]) == 74


def test_in_scaling_product_filter_and_numbers_basis(page):
    goto(page, "?product=boneflex", "in_scaling")
    rows = table(page, TABLE)["keys"]
    assert rows and set(column(page, TABLE, "product")) == {"Boneflex"} and table(page, TOTALS)["keys"] == ["boneflex"]
    goto(page, "?source=upstack&basis=nc", "in_scaling")
    heads = page.locator(f"{TABLE} thead th").all_inner_texts()
    assert any("NC ROAS since move" in h for h in heads) and any("NC CPA" in h for h in heads)


def test_the_status_rules_are_written_out_on_the_page(page):
    goto(page, "", "in_scaling")
    page.locator("[data-testid=sv-defs] summary").click()
    t = page.locator("[data-testid=sv-defs]").inner_text()
    assert "better from 110%" in t and "about the same from 90%" in t and "Holding" in t and "Fading" in t and "Stopped" in t and "$200" in t and "3 days" in t


def test_in_scaling_empty_state(page):
    goto(page, "?hide_bulk=1&bulk_min=1", "in_scaling")
    assert table(page, TABLE)["keys"] == [] and table(page, TABLE)["empty"]
    assert "No creative moved from testing to scaling" in page.locator(f"{TABLE} .emptyrow").inner_text()
    assert page.locator(f"{TOTALS} tr.emptyrow").count() == 1


# ------------------------------------------------------------------ phone, failures, ad blockers
def test_phone_width_has_no_sideways_scroll_on_either_tab(browser, base_url):
    c = browser.new_context(viewport={"width": 375, "height": 812}, base_url=base_url)
    try:
        assert c.request.post(base_url + "/api/login", data={"username": "JoshHoughton", "password": "test"}).status == 204
        pg = c.new_page()
        errs = []
        pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: errs.append(str(e)))
        for qs in ("?view=should_scale", "?view=should_scale&bar_pct=1000", "?view=should_scale&min_test_spend=0&source=upstack&basis=all", "?view=in_scaling",
                   "?view=in_scaling&hide_bulk=1", "?view=in_scaling&window=launch", "?view=detail"):
            pg.goto("/promotions" + qs)
            pg.wait_for_selector(TABS)
            pg.wait_for_selector("[data-testid=sv-summary], [data-testid=tts-strip]")
            idle(pg)
            w = pg.evaluate("[document.documentElement.scrollWidth, document.documentElement.clientWidth, document.body.scrollWidth]")
            assert w[0] <= w[1] and w[2] <= w[1], (qs, w)
            over = pg.evaluate("""() => [...document.querySelectorAll('#view *')].filter(e => { const r = e.getBoundingClientRect();
                return r.width > 0 && r.right > document.documentElement.clientWidth + 1 && !e.closest('.tablewrap'); }).map(e => e.className || e.tagName).slice(0, 5)""")
            assert over == [], (qs, over)
            small = pg.evaluate("""() => [...document.querySelectorAll('.sv-page *')].filter(e => e.children.length === 0 && e.textContent.trim() && parseFloat(getComputedStyle(e).fontSize) < 12).map(e => e.className || e.tagName).slice(0, 5)""")
            assert small == [], (qs, small)
        pg.goto("/promotions?view=should_scale")
        pg.wait_for_selector("[data-testid=sv-summary]")
        idle(pg)
        pg.screenshot(path=str(SHOTS / "sv_04_phone_should_scale.png"), full_page=True)
        pg.goto("/promotions?view=in_scaling")
        pg.wait_for_selector("[data-testid=sv-summary]")
        idle(pg)
        pg.screenshot(path=str(SHOTS / "sv_05_phone_in_scaling.png"), full_page=True)
        assert errs == []
    finally:
        c.close()


def test_text_is_at_least_12px_and_prose_is_capped(page):
    for view in ("should_scale", "in_scaling"):
        goto(page, "", view)
        small = page.evaluate("""() => [...document.querySelectorAll('.sv-page *')].filter(e => [...e.childNodes].some(n => n.nodeType === 3 && n.textContent.trim())
            && parseFloat(getComputedStyle(e).fontSize) < 12).map(e => e.className || e.tagName).slice(0, 5)""")
        assert small == [], (view, small)
        widths = page.evaluate("""() => [...document.querySelectorAll('.sv-lead, .sv-foot')].map(e => e.getBoundingClientRect().width / parseFloat(getComputedStyle(e).fontSize))""")
        assert all(w <= 56 for w in widths), widths                       # max-width 50ch is about 50 to 55 em of the proportional font


def test_contrast_of_the_chips_is_at_least_4_5(page):
    def lum(rgb):
        c = [x / 255 for x in rgb]
        c = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    for view, sel in (("should_scale", ".sv-conf, .sv-vs"), ("in_scaling", ".sv-status, .sv-vs")):
        goto(page, "?min_test_spend=0&bar_pct=1000" if view == "should_scale" else "", view)
        pairs = page.evaluate("""(sel) => [...document.querySelectorAll(sel)].map(e => { const s = getComputedStyle(e); return [s.color, s.backgroundColor]; })""", sel)
        assert pairs
        for fg, bg in pairs:
            f = [int(x) for x in re.findall(r"\d+", fg)[:3]]
            b = [int(x) for x in re.findall(r"\d+", bg)[:3]]
            a, c = sorted((lum(f), lum(b)), reverse=True)
            assert (a + 0.05) / (c + 0.05) >= 4.5, (fg, bg)


@pytest.mark.allow_errors            # the browser logs the failed request
@pytest.mark.parametrize("view", ["should_scale", "in_scaling"])
def test_api_failure_shows_the_error_and_retry_works(page, view):
    page.route("**/api/promotions/scale*", lambda r: r.fulfill(status=503, content_type="application/json", body='{"error": "query timed out"}'))
    page.goto("/promotions?view=" + view)
    box = page.locator(".errorbox")
    box.wait_for()
    assert "query timed out" in box.inner_text() and box.get_by_role("button", name="Retry").is_visible()
    assert page.locator(".loading").count() == 0
    page.unroute("**/api/promotions/scale*")
    box.get_by_role("button", name="Retry").click()
    page.wait_for_selector("[data-testid=sv-summary]")
    assert page.locator(".errorbox").count() == 0 and page.locator(TABLE).count() == 1


@pytest.mark.allow_errors
@pytest.mark.parametrize("view", ["should_scale", "in_scaling"])
def test_a_hanging_request_ends_in_an_error_with_retry_not_loading_forever(page, view):
    page.route("**/api/promotions/scale*", lambda r: None)                       # never answered
    page.goto(f"/promotions?view={view}&tts_timeout_ms=800")
    page.locator(".errorbox").wait_for(timeout=8000)
    text = page.locator(".errorbox").inner_text()
    assert "took longer than" in text and "Retry" in text


def test_no_class_or_id_an_ad_blocker_would_hide(page):
    bad = re.compile(r"^(ads?|ad-.*|ads-.*|adname|advert.*|sponsor.*)$")
    for view in ("should_scale", "in_scaling"):
        goto(page, "?bar_pct=1000" if view == "should_scale" else "", view)
        names = page.evaluate("""() => { const out = new Set(); document.querySelectorAll('#view *').forEach(e => { e.classList.forEach(c => out.add(c)); if (e.id) out.add(e.id); }); return [...out]; }""")
        assert [n for n in names if bad.match(n) or "promo" in n.lower() or "banner" in n.lower()] == []


def test_page_says_when_the_precomputed_data_was_built(page):
    for view in ("should_scale", "in_scaling"):
        goto(page, "", view)
        t = page.locator("[data-testid=sv-built]").inner_text()
        assert "Precomputed nightly" in t and "Nov 2" in t
