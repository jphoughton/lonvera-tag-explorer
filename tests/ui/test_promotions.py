"""Test to scale (/promotions) in the browser, against the mock server (tests/ui/mock_promotions.py: the real payload builder over made-up creatives).

Mock data facts used below: 28 ordinary moves + a 45-creative mass relaunch on Oct 22 + 1 creative already running on the first day of data = 74 moves in
all, 56 of them in the default last 30 days (45 bulk); 14 creatives began in testing in the range and did not scale; the data runs May 6 to Nov 2.
"""
import re
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.sync_api import expect

from conftest import SHOTS

pytestmark = pytest.mark.mock_only

MOVED = "[data-testid=tts-moved]"
NOT_SCALED = "[data-testid=tts-not-scaled]"
COHORTS = "[data-testid=tts-cohorts]"
STRIP = "[data-testid=tts-strip]"


# ------------------------------------------------------------------ helpers
def idle(page):
    page.wait_for_function("() => !document.querySelector('#view').hasAttribute('aria-busy')")


def detail(qs=""):
    """The detail view (the third tab: the page's original content) is opened with view=detail; a bare /promotions opens What should be scaled."""
    return "/promotions" + (qs + "&view=detail" if qs else "?view=detail")


def goto(page, qs=""):
    page.goto(detail(qs))
    page.wait_for_selector(STRIP)
    idle(page)


def params(page):
    return {k: v[0] for k, v in parse_qs(urlparse(page.url).query).items()}


def table(page, root):
    return page.evaluate(
        """(root) => {
          const t = document.querySelector(`${root} table`);
          if (!t) return {heads: [], rows: [], keys: []};
          const heads = [...t.querySelectorAll(':scope > thead th')].map(th => th.dataset.col || '');
          const trs = [...t.querySelectorAll(':scope > tbody > tr[data-key]:not(.expandrow)')];
          return {heads, rows: trs.map(tr => [...tr.children].map(td => td.innerText.trim())), keys: trs.map(tr => tr.dataset.key),
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


def natural(s):
    return [int(t) if t.isdigit() else t.casefold() for t in re.split(r"(\d+)", s)]


def kpi(page, key):
    return page.locator(f"[data-testid=tts-kpi-{key}-value]").inner_text()


def api(page, qs=""):
    r = page.context.request.get("/api/promotions" + qs)
    assert r.status == 200, r.status
    return r.json()


def n_moved(page):
    return len(table(page, MOVED)["keys"])


def shot(page, name):
    page.screenshot(path=str(SHOTS / f"{name}.png"), full_page=True)


# ------------------------------------------------------------------ the page
def test_top_nav_link_opens_the_page(page):
    page.goto("/?platform=meta")
    page.locator("[data-testid=nav-test-to-scale]").wait_for()
    assert page.locator("[data-testid=nav-test-to-scale]").inner_text() == "Test to scale"
    page.locator("[data-testid=nav-test-to-scale]").click()
    page.wait_for_selector("[data-testid=sv-tabs]")                                    # the page opens on its first tab, What should be scaled
    assert urlparse(page.url).path == "/promotions"
    expect(page.locator("h1")).to_have_text("Test to scale")
    expect(page).to_have_title(re.compile("Test to scale"))
    expect(page.locator("[data-testid=sv-tab-should_scale]")).to_have_attribute("aria-pressed", "true")
    page.locator("[data-testid=sv-tab-detail]").click()
    page.wait_for_selector(STRIP)
    assert params(page).get("view") == "detail"


def test_page_renders_every_section_and_defaults_to_the_last_30_days(page):
    goto(page)
    assert params(page).get("window") == "30d"                                           # a bare URL is normalised to the 30-day default
    expect(page.locator("#range-label")).to_have_text("Oct 4 – Nov 2 · 30 days")
    expect(page.locator(".seg[data-window='30d']")).to_have_attribute("aria-pressed", "true")
    help_text = page.locator("[data-testid=tts-help]").inner_text()
    assert "What counts as a move" in help_text and "Testing campaign" in help_text and "first spend in a Scaling campaign" in help_text
    assert "Meta only" in help_text and "Google has no creative grain" in help_text and "never added across platforms" in help_text
    assert "Held up?" in help_text and "Nov 2" in help_text
    assert page.locator(f"{STRIP} .kpi").count() == 7
    for k in ("launched", "still-testing", "moved", "reached", "median", "test-roas", "scale-roas"):
        expect(page.locator(f"[data-testid=tts-kpi-{k}]")).to_be_visible()
    assert page.locator("[data-testid=tts-chart-panel] canvas").count() == 1
    d = table(page, MOVED)
    assert len(d["keys"]) == 56 == int(kpi(page, "moved"))
    expect(page.locator("#level-title")).to_have_text("Moved to scaling · 56")
    assert page.locator(f"{NOT_SCALED} tbody tr[data-key]").count() == 9 and page.locator(f"{COHORTS} tbody tr[data-key]").count() == 5
    assert page.locator("[data-testid=src-badge]").first.inner_text() == "Numbers: Meta"
    # the stage chips and the comparison do not apply to this page; the platform picker shows Meta
    assert page.locator("#stage-ctl").is_hidden() and page.locator("#compare-ctl").is_hidden()
    expect(page.locator("#platform-ctl .seg[data-platform='meta']")).to_have_attribute("aria-pressed", "true")
    assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    shot(page, "tts_01_desktop")


def test_every_creative_links_to_its_page_and_rows_carry_the_columns_asked_for(page):
    goto(page)
    d = table(page, MOVED)
    for k in ("display", "hook", "launched", "moved", "days_in_test", "test_spend", "test_purchases", "test_roas", "test_cpa", "scale_spend", "scale_purchases", "scale_roas",
              "scale_cpa", "held", "last7_spend", "last7_roas", "live", "placements"):
        assert k in d["heads"], k
    hrefs = page.locator(f"{MOVED} [data-testid=tts-creative-link]").evaluate_all("els => els.map(e => e.getAttribute('href'))")
    assert len(hrefs) == 56 and all(re.match(r"^/ad/\d+", h) for h in hrefs)
    assert page.locator(f"{MOVED} tbody tr[data-key] img.thumb, {MOVED} tbody tr[data-key] .thumb").count() >= 56
    first = d["rows"][0]
    assert num(first[d["heads"].index("days_in_test")]) >= 2 and re.match(r"^[A-Z][a-z]{2} \d+\*?$", first[d["heads"].index("launched")])
    assert page.locator(f"{MOVED} [data-held]").count() >= 56


def test_summary_strip_ties_to_the_api_and_the_table(page):
    goto(page)
    d = api(page, "?window=30d")
    s = d["summary"]
    assert int(kpi(page, "launched")) == s["launched"]["n"] and int(kpi(page, "still-testing")) == s["still_testing"]
    assert kpi(page, "reached") == f"{round(s['pct_reached_scaling'] * 100)}%"
    assert num(kpi(page, "median")) == pytest.approx(s["median_days_to_scale"], abs=0.05)
    assert kpi(page, "test-roas") == f"{s['testing']['roas']:.2f}x" and kpi(page, "scale-roas") == f"{s['scaling']['roas']:.2f}x"
    # the table's Total row is the sum of its rows (ratios of sums): spend and the two ROAS values tie to the strip
    foot = page.evaluate("() => [...document.querySelectorAll('[data-testid=tts-moved] tfoot tr td')].map(td => td.innerText.trim())")
    heads = table(page, MOVED)["heads"]
    assert num(foot[heads.index("scale_spend")]) == pytest.approx(round(s["scaling"]["spend"]), abs=1) and num(foot[heads.index("scale_roas")]) == pytest.approx(s["scaling"]["roas"], abs=0.01)
    assert num(foot[heads.index("test_roas")]) == pytest.approx(s["testing"]["roas"], abs=0.01)
    held = page.locator("[data-testid=tts-held-summary]").inner_text()
    assert f"Held up {s['held']['held']}" in held and f"Too early {s['held']['too_early']}" in held


def test_chart_has_moves_bulk_moves_and_scaling_spend(page):
    goto(page)
    info = page.evaluate("""() => { const c = Chart.getChart(document.getElementById('tts-chart'));
        return {labels: c.data.labels, sets: c.data.datasets.map(d => ({label: d.label, type: d.type, data: d.data}))}; }""")
    sets = {s["label"]: s for s in info["sets"]}
    assert set(sets) == {"Moved to scaling", "Moved on a bulk day", "Scaling spend of these creatives"}
    assert len(info["labels"]) == 30 and info["labels"][0] == "2026-10-04" and info["labels"][-1] == "2026-11-02"
    assert sum(sets["Moved on a bulk day"]["data"]) == 45 and max(sets["Moved on a bulk day"]["data"]) == 45
    assert sets["Moved on a bulk day"]["data"][info["labels"].index("2026-10-22")] == 45
    assert sum(sets["Moved to scaling"]["data"]) == 11 and sets["Scaling spend of these creatives"]["type"] == "line"
    page.locator("[data-testid=tts-grain-week]").click()
    page.wait_for_function("() => new URLSearchParams(location.search).get('grain') === 'week'")
    idle(page)
    wk = page.evaluate("() => { const c = Chart.getChart(document.getElementById('tts-chart')); return c.data.labels; }")
    assert wk[0] == "2026-09-28" and all(re.match(r"^2026-\d\d-\d\d$", x) for x in wk) and len(wk) == 6
    expect(page.locator("[data-testid=tts-grain-week]")).to_have_attribute("aria-pressed", "true")


# ------------------------------------------------------------------ bulk moves
def test_bulk_move_day_is_flagged_with_a_chip_and_a_note(page):
    goto(page)
    chips = page.locator(f"{MOVED} [data-testid=tts-bulk-chip]")
    assert chips.count() == 45 and "Bulk move · 45 that day" in chips.first.inner_text()
    note = page.locator("[data-testid=tts-bulk-note]").inner_text()
    assert "Oct 22 (45)" in note and "40 or more creatives" in note and "mass relaunch" in note
    assert page.locator(f"{MOVED} tr.tts-bulk-row").count() == 45
    assert not page.locator("[data-testid=tts-hide-bulk]").is_checked()


def test_hide_bulk_moves_toggle_removes_them_from_every_number(page):
    goto(page)
    before = {k: kpi(page, k) for k in ("launched", "moved", "reached")}
    page.locator("[data-testid=tts-hide-bulk]").check()
    page.wait_for_function("() => new URLSearchParams(location.search).get('hide_bulk') === '1'")
    idle(page)
    assert n_moved(page) == 11 and int(kpi(page, "moved")) == 11
    assert page.locator(f"{MOVED} [data-testid=tts-bulk-chip]").count() == 0
    assert page.locator("[data-testid=tts-hide-bulk]").is_checked()
    assert "Bulk moves hidden" in page.locator("[data-testid=tts-bulk-note]").inner_text()
    assert "45 creatives that moved on bulk days are left out" in " ".join(page.locator("[data-testid=tts-note]").all_inner_texts())
    assert int(kpi(page, "launched")) < int(before["launched"])                                    # the 45 are in neither the numerator nor the denominator
    info = page.evaluate("() => Chart.getChart(document.getElementById('tts-chart')).data.datasets.map(d => d.data.reduce((a, b) => a + b, 0))")
    assert info[1] == 0 and info[0] == 11
    # the same switch from the note, and back
    page.locator("[data-testid=tts-bulk-toggle]").click()
    page.wait_for_function("() => !new URLSearchParams(location.search).has('hide_bulk')")
    idle(page)
    assert n_moved(page) == 56 and {k: kpi(page, k) for k in before} == before


def test_bulk_threshold_and_min_test_spend_are_inputs_kept_in_the_url(page):
    goto(page)
    page.locator("[data-testid=tts-bulk-min]").fill("50")
    page.locator("[data-testid=tts-bulk-min]").press("Enter")
    page.wait_for_function("() => new URLSearchParams(location.search).get('bulk_min') === '50'")
    idle(page)
    assert page.locator(f"{MOVED} [data-testid=tts-bulk-chip]").count() == 0 and page.locator("[data-testid=tts-bulk-note]").count() == 0
    page.locator("[data-testid=tts-bulk-min]").fill("40")
    page.locator("[data-testid=tts-bulk-min]").press("Enter")
    page.wait_for_function("() => !new URLSearchParams(location.search).has('bulk_min')")
    idle(page)
    assert page.locator(f"{MOVED} [data-testid=tts-bulk-chip]").count() == 45
    page.locator("[data-testid=tts-min-test]").fill("500")
    page.locator("[data-testid=tts-min-test]").press("Enter")
    page.wait_for_function("() => new URLSearchParams(location.search).get('min_test_spend') === '500'")
    idle(page)
    spends = [num(x) for x in column(page, MOVED, "test_spend")]
    assert 0 < len(spends) < 56 and all(s >= 500 for s in spends)
    assert all(num(x) >= 500 for x in column(page, NOT_SCALED, "test_spend"))
    assert page.locator("[data-testid=tts-min-test]").input_value() == "500"


# ------------------------------------------------------------------ range, product, numbers
def test_range_presets_refetch_and_the_page_follows_the_data(page):
    goto(page)
    reqs = []
    page.on("request", lambda r: reqs.append(r.url) if "/api/promotions" in r.url else None)
    want = {}
    for win in ("7d", "14d", "30d", "60d", "launch", "1d"):
        want[win] = api(page, f"?window={win}")["summary"]["moved"]["n"]
    assert want["launch"] == 74 and want["1d"] == 0 and want["7d"] < want["30d"] < want["launch"]
    for win in ("7d", "14d", "60d", "launch", "1d", "30d"):
        reqs.clear()
        page.locator(f".seg[data-window='{win}']").click()
        page.wait_for_function("(w) => new URLSearchParams(location.search).get('window') === w", arg=win)         # Since launch stays in the URL on this page
        idle(page)
        assert any(f"window={win}" in u for u in reqs), (win, reqs)
        assert n_moved(page) == want[win], win
        expect(page.locator(f".seg[data-window='{win}']")).to_have_attribute("aria-pressed", "true")
    expect(page.locator("#range-label")).to_have_text("Oct 4 – Nov 2 · 30 days")
    page.locator(".seg[data-window='launch']").click()
    idle(page)
    expect(page.locator("#range-label")).to_contain_text("All days held")
    page.goto(detail("?window=launch"))
    page.wait_for_selector(STRIP)
    idle(page)
    assert params(page)["window"] == "launch" and n_moved(page) == 74                               # a reload keeps Since launch, it does not fall back to 30 days


def test_custom_range_counts_moves_by_their_first_scaling_day(page):
    goto(page)
    d = api(page, "?window=launch")
    days = sorted({r["moved"] for r in d["moved"]})
    day = d["bulk_days"][0]["day"]
    page.locator("#range-from").fill(day)
    page.locator("#range-to").fill(day)
    page.locator("#range-apply").click()
    page.wait_for_function("(day) => new URLSearchParams(location.search).get('from') === day", arg=day)
    idle(page)
    assert n_moved(page) == 45 and params(page)["to"] == day and "window" not in params(page)
    assert len(days) > 5
    assert "latest day of data" in " ".join(page.locator("[data-testid=tts-note]").all_inner_texts())          # scaling numbers run past the range


def test_product_filter_keeps_one_products_creatives_everywhere(page):
    goto(page)
    chips = page.locator("[data-testid=tts-product-filter] .seg")
    assert chips.all_inner_texts() == ["All", "Vita Bull", "Beef Liver", "Boneflex"]
    total = n_moved(page)
    page.locator("[data-testid=tts-product-beef-liver]").click()
    page.wait_for_function("() => new URLSearchParams(location.search).get('product') === 'beef-liver'")
    idle(page)
    assert 0 < n_moved(page) < total
    for root in (MOVED, NOT_SCALED):
        prods = page.locator(f"{root} .tts-product").evaluate_all("els => els.map(e => e.dataset.product)")
        assert prods and set(prods) == {"beef-liver"}
    expect(page.locator("[data-testid=tts-product-beef-liver]")).to_have_attribute("aria-pressed", "true")
    assert page.locator("[data-testid=tts-product-vita-bull]").get_attribute("title").startswith(("1", "2", "3", "4", "5", "6", "7", "8", "9"))     # chip counts ignore the filter
    page.locator("[data-testid=tts-product-all]").click()
    idle(page)
    assert n_moved(page) == total


def test_numbers_from_upstack_relabels_columns_and_changes_purchases_not_spend(page):
    goto(page)
    spend = column(page, MOVED, "scale_spend")
    purchases = [num(x) for x in column(page, MOVED, "scale_purchases")]
    reqs = []
    page.on("request", lambda r: reqs.append(r.url) if "/api/promotions" in r.url else None)
    page.locator("#source-ctl .seg[data-source='upstack']").click()
    page.wait_for_function("() => new URLSearchParams(location.search).get('source') === 'upstack'")
    idle(page)
    assert any("source=upstack" in u for u in reqs)
    heads = page.locator(f"{MOVED} thead th").all_inner_texts()
    assert any("Test New customers" in h for h in heads) and any("Scaling NC ROAS" in h for h in heads), heads
    assert column(page, MOVED, "scale_spend") == spend                                               # Meta's spend in every basis
    assert [num(x) for x in column(page, MOVED, "scale_purchases")] != purchases
    assert "NC ROAS" in page.locator("[data-testid=tts-kpi-scale-roas]").inner_text()
    assert page.locator("[data-testid=src-badge]").first.inner_text() == "Numbers: Upstack · new customers"
    page.locator("#basis-ctl .seg[data-basis='all']").click()
    page.wait_for_function("() => new URLSearchParams(location.search).get('basis') === 'all'")
    idle(page)
    assert any("Test Orders" in h for h in page.locator(f"{MOVED} thead th").all_inner_texts())


# ------------------------------------------------------------------ the tables
def test_sorting_the_moved_table_both_ways_and_it_persists(page):
    goto(page)
    for key, numeric in (("scale_spend", True), ("days_in_test", True), ("test_roas", True), ("display", False)):
        btn = page.locator(f"{MOVED} thead th[data-col='{key}'] button.thbtn")
        btn.click()
        first = column(page, MOVED, key)
        btn.click()
        second = column(page, MOVED, key)
        if numeric:
            va, vb = [num(x) for x in first if num(x) is not None], [num(x) for x in second if num(x) is not None]
            assert va == sorted(va, reverse=True) and vb == sorted(vb), key              # numbers sort high first, then low first
        else:                                                                            # names sort naturally (Static-9.6 before Static-11.4), case-insensitively
            a, b = [natural(x.split("\n")[0]) for x in first], [natural(x.split("\n")[0]) for x in second]
            assert a == sorted(a) and b == sorted(b, reverse=True), key
    page.locator(f"{MOVED} thead th[data-col='scale_roas'] button.thbtn").click()
    p = params(page)
    assert p["sort"] == "scale_roas" and p["dir"] == "desc"
    page.reload()
    page.wait_for_selector(STRIP)
    idle(page)
    vals = [num(x) for x in column(page, MOVED, "scale_roas") if num(x) is not None]
    assert vals == sorted(vals, reverse=True)
    sp = [num(x) for x in column(page, NOT_SCALED, "test_spend")]
    assert sp == sorted(sp, reverse=True)                                                        # the not-scaled list opens with the most test spend first


def test_search_filters_the_moved_table(page):
    goto(page)
    total = n_moved(page)
    first_name = column(page, MOVED, "display")[0].split("\n")[0]
    page.locator("[data-testid=tts-moved-search]").fill(first_name)
    page.wait_for_function("(n) => document.querySelectorAll('[data-testid=tts-moved] tbody tr[data-key]').length < n", arg=total)
    d = table(page, MOVED)
    names = column(page, MOVED, "display")
    assert 1 <= len(d["keys"]) < total and all(first_name.lower() in n.lower() for n in names)
    assert params(page)["q"] == first_name
    assert page.locator("[data-testid=tts-moved-search-count]").inner_text() == f"{len(d['keys'])} of {total} rows"
    page.locator("[data-testid=tts-moved-search]").fill("zzzz-no-such-creative")
    expect(page.locator(f"{MOVED} tr.emptyrow")).to_be_visible()


def test_expanding_a_row_lists_where_it_scaled(page):
    goto(page)
    row = page.locator(f"{MOVED} tbody tr[data-key]").first
    cell = row.locator("[data-testid=tts-placement-count]")
    assert re.match(r"^\d+ campaigns? · \d+ ad sets?$", cell.inner_text())
    assert "›" in cell.get_attribute("title") and "$" in cell.get_attribute("title")                   # campaign › ad set · spend, on hover
    row.locator("button.caret").click()
    box = page.locator(f"{MOVED} tr.expandrow [data-testid=tts-placements]")
    box.wait_for()
    assert box.locator("tbody tr").count() >= 1
    assert "Scaling since the move" in page.locator(f"{MOVED} tr.expandrow").inner_text()
    assert num(box.locator("tbody tr").first.locator("td").nth(3).inner_text()) > 0
    assert row.locator("button.caret").get_attribute("title") == "Show where it scaled"


def test_truncated_launch_is_labelled(page):
    goto(page)
    chip = page.locator(f"{MOVED} [data-testid=tts-before-start]")
    assert chip.count() == 1 and chip.inner_text() == "Launched before data start"
    row = page.locator(f"{MOVED} tbody tr[data-key]").filter(has=page.locator("[data-testid=tts-before-start]"))
    d = table(page, MOVED)
    i = [k for k, r in zip(d["keys"], d["rows"])].index(row.get_attribute("data-key"))
    assert d["rows"][i][d["heads"].index("launched")].endswith("*") and d["rows"][i][d["heads"].index("days_in_test")].startswith("≥")


def test_not_scaled_list_and_cohort_funnel(page):
    goto(page)
    states = page.locator(f"{NOT_SCALED} [data-state]").evaluate_all("els => els.map(e => e.dataset.state)")
    assert set(states) == {"still_testing", "stopped"} and len(states) == 9
    assert int(kpi(page, "still-testing")) == states.count("still_testing")
    d = table(page, COHORTS)
    assert d["heads"] == ["week", "launched", "moved", "pct", "median", "still", "stopped", "no_test"]
    rows = d["rows"]
    assert rows[0][0].startswith("Week of Sep 28") and "(partial)" in rows[0][0]
    foot = page.evaluate("() => [...document.querySelectorAll('[data-testid=tts-cohorts] tfoot td')].map(td => td.innerText.trim())")
    assert foot[1] == str(sum(int(r[1]) for r in rows)) and foot[2] == str(sum(int(r[2]) for r in rows))
    assert foot[3] == f"{round(int(foot[2]) / int(foot[1]) * 100)}%"                              # a ratio of sums, not a mean of the weeks' percentages
    assert int(foot[1]) - int(foot[2]) == int(foot[5]) + int(foot[6])                              # began in testing = reached scaling + still testing + stopped


def test_empty_state_when_nothing_moved(page):
    goto(page, "?window=1d")
    assert n_moved(page) == 0 and table(page, MOVED)["empty"]
    expect(page.locator(f"{MOVED} tr.emptyrow")).to_contain_text("No creative moved from testing to scaling in this range")
    assert kpi(page, "moved") == "0" and kpi(page, "test-roas") == "–" and kpi(page, "scale-roas") == "–" and kpi(page, "median") == "–"
    assert kpi(page, "reached") == "–"
    expect(page.locator("#level-title")).to_have_text("Moved to scaling · 0")
    assert page.locator(f"{MOVED} tfoot").count() == 0                                              # no Total row over nothing
    assert page.locator("[data-testid=tts-bulk-note]").count() == 0
    assert page.locator("[data-testid=tts-chart-panel] canvas").count() == 1
    shot(page, "tts_02_empty")


@pytest.mark.allow_errors            # the API answers 400 (no data in that range): the browser logs it
def test_out_of_range_dates_use_the_apps_empty_range_state(page):
    page.goto(detail("?from=2027-01-01&to=2027-01-05"))
    page.wait_for_selector("[data-testid=empty-range]")
    page.locator("[data-testid=reset-range]").click()
    page.wait_for_selector(STRIP)


# ------------------------------------------------------------------ phone
def test_phone_width_has_no_sideways_scroll(browser, base_url):
    c = browser.new_context(viewport={"width": 375, "height": 812}, base_url=base_url)
    try:
        assert c.request.post(base_url + "/api/login", data={"username": "JoshHoughton", "password": "test"}).status == 204
        pg = c.new_page()
        errs = []
        pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: errs.append(str(e)))
        for qs in ("", "?hide_bulk=1", "?window=launch", "?window=1d", "?source=upstack&basis=all&grain=week"):
            pg.goto(detail(qs))
            pg.wait_for_selector(STRIP)
            idle(pg)
            w = pg.evaluate("[document.documentElement.scrollWidth, document.documentElement.clientWidth, document.body.scrollWidth]")
            assert w[0] <= w[1] and w[2] <= w[1], (qs, w)
            over = pg.evaluate("""() => [...document.querySelectorAll('#view *')].filter(e => { const r = e.getBoundingClientRect();
                return r.width > 0 && r.right > document.documentElement.clientWidth + 1 && !e.closest('.tablewrap'); }).map(e => e.className || e.tagName).slice(0, 5)""")
            assert over == [], (qs, over)
        pg.goto(detail())
        pg.wait_for_selector(STRIP)
        idle(pg)
        pg.locator(f"{MOVED} tbody tr[data-key]").first.locator("button.caret").click()
        pg.locator(f"{MOVED} tr.expandrow [data-testid=tts-placements]").wait_for()
        assert pg.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
        pg.screenshot(path=str(SHOTS / "tts_03_phone.png"), full_page=True)
        assert errs == []
    finally:
        c.close()


def test_no_class_or_id_an_ad_blocker_would_hide(page):
    goto(page)
    bad = page.evaluate("""() => [...document.querySelectorAll('#view *')].flatMap(e => [...e.classList, e.id].filter(Boolean))
        .filter(t => /^(ads?|ad-.*|ads-.*|adname|advert.*|sponsor.*)$/.test(t) || /promo|banner|sponsor|advert/i.test(t))""")
    assert bad == []
    assert page.locator("main#view .page").first.is_visible()


# ------------------------------------------------------------------ failures and slowness never end in an endless "Loading…"
@pytest.mark.allow_errors            # the browser logs the failed request
def test_api_failure_shows_the_error_and_retry_works(page):
    page.route("**/api/promotions*", lambda r: r.fulfill(status=503, content_type="application/json", body='{"error": "query timed out"}'))
    page.goto(detail())
    box = page.locator(".errorbox")
    box.wait_for()
    assert "query timed out" in box.inner_text() and box.get_by_role("button", name="Retry").is_visible()
    assert page.locator(".loading").count() == 0
    page.unroute("**/api/promotions*")
    box.get_by_role("button", name="Retry").click()
    page.wait_for_selector(STRIP)
    assert n_moved(page) == 56


@pytest.mark.allow_errors
def test_a_hanging_request_ends_in_an_error_with_retry_not_loading_forever(page):
    page.route("**/api/promotions*", lambda r: None)                      # never answered
    page.goto(detail("?tts_timeout_ms=800"))
    page.locator(".errorbox").wait_for(timeout=8000)
    text = page.locator(".errorbox").inner_text()
    assert "took longer than" in text and "Retry" in text
    assert page.locator(".loading").count() == 0 and not page.evaluate("document.querySelector('#view').hasAttribute('aria-busy')")


def test_page_says_when_the_precomputed_data_was_built(page):
    goto(page)
    txt = page.locator("[data-testid=tts-built]").inner_text()
    assert "rebuilt" in txt and "through Nov 2" in txt
