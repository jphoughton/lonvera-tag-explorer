"""Overview, Google, Campaigns, totals strip and platform trends.

Split out of tests/ui/test_ui.py with no assertion changes. Shared helpers live in ui_helpers.py. Run: see the docstring of conftest.py.
"""
import re
from datetime import date, timedelta
from urllib.parse import parse_qs, urlencode, urlparse
import pytest
from playwright.sync_api import expect
from conftest import IS_MOCK, PASSWORD, SHOTS, USERNAME
from ui_helpers import *  # noqa: F401,F403

def open_chart_options(page):
    """Bars, Line, Swap, Split bars by and Numbers sit in the closed 'Chart options' disclosure; open it (no-op when already open)."""
    det = page.locator("[data-testid=chart-options]").first
    if det.get_attribute("open") is None:
        det.locator("summary").click()
    expect(det).to_have_attribute("open", "")


@pytest.mark.mock_only
def test_platform_picker_google_page_and_back(page):
    goto_product(page, "?window=14d&stage=scaling")
    expect(page.locator("#platform-ctl .ctl-name")).to_have_text("Platform")
    assert page.evaluate("() => document.querySelector('#controls .ctl-row .ctl').id") == "platform-ctl"          # first control of the bar
    assert [b.get_attribute("data-platform") for b in page.locator("#platform-ctl .seg").all()] == ["all", "meta", "google"]
    expect(platform_btn(page, "meta")).to_have_attribute("aria-pressed", "true")
    expect(page.locator("#stage-ctl")).to_be_visible()
    platform_btn(page, "google").click()
    expect(page).to_have_url(re.compile(r"platform=google"))
    q = qparams(page)
    assert q["window"] == "14d" and "stage" not in q and page.evaluate("() => location.pathname") == "/", q            # the range carries over, Meta-only controls are dropped
    expect(page.locator("h1")).to_have_text("Google")
    expect(platform_btn(page, "google")).to_have_attribute("aria-pressed", "true")
    expect(page.locator("#stage-ctl")).to_be_hidden()
    expect(page.locator("#tieout-btn")).to_be_hidden()
    expect(page.locator("#source-ctl [data-source=meta]")).to_have_text("Google")
    expect(page.locator("#source-ctl-inline [data-src-inline=meta]")).to_have_text("Google")
    types = page.locator("[data-testid=google-types] tbody tr[data-key]")
    expect(types.first).to_be_visible()
    assert types.count() >= 4
    expect(page.locator("[data-testid=google-types] th[data-col=revenue]")).to_contain_text("Conversion value")
    expect(page.locator("[data-testid=google-campaigns] tbody tr[data-key]").first).to_be_visible()
    expect(page.locator("[data-testid=google-trend] canvas")).to_be_visible()
    expect(page.locator("[data-testid=src-badge]").first).to_have_text("Numbers: Google")
    total_google = num(foot_value(page, "spend", "google-types"))
    page.locator("#source-ctl [data-source=upstack]").click()                                          # Numbers from Upstack: labels change, spend does not
    expect(page).to_have_url(re.compile(r"source=upstack"))
    expect(page.locator("[data-testid=google-types] th[data-col=purchases]")).to_contain_text("New customers")
    assert abs(num(foot_value(page, "spend", "google-types")) - total_google) <= 1
    expect(page.locator("#source-banner")).to_contain_text("Upstack")
    expect(page.locator("[data-testid=src-badge]").first).to_have_text("Numbers: Upstack · new customers")
    page.locator("[data-testid=google-types] tbody tr[data-key]").first.click()                        # a type row filters the campaigns table
    expect(page).to_have_url(re.compile(r"type="))
    expect(page.locator("#campaigns-title")).to_contain_text("Campaigns ·")
    assert qparams(page)["source"] == "upstack" and qparams(page)["platform"] == "google"
    platform_btn(page, "meta").click()                                                                  # back to Meta: range and numbers choice carry over, platform and type leave
    page.wait_for_function("() => new URL(location.href).searchParams.get('platform') === 'meta'")     # Meta's Products landing is explicit now: a bare / is All
    expect(page.locator("h1")).to_have_text("Meta")
    expect(platform_btn(page, "meta")).to_have_attribute("aria-pressed", "true")
    assert qparams(page)["window"] == "14d" and qparams(page)["source"] == "upstack" and "type" not in qparams(page)
    shot(page, "11_google")


@pytest.mark.mock_only
def test_first_google_url_still_works(page, base_url):
    page.goto(f"{base_url}/google?window=14d")
    expect(page).to_have_url(re.compile(r"/\?(?=.*platform=google)(?=.*window=14d)"))
    expect(page.locator("h1")).to_have_text("Google")


@pytest.mark.mock_only
def test_google_campaigns_view_has_the_campaigns_first_and_types_as_a_filter(page):
    goto_product(page, "?window=14d")
    platform_btn(page, "google").click()
    page.locator("#view-switch [data-platform-view=campaigns]").click()
    expect(page).to_have_url(re.compile(r"/campaigns\?"))
    expect(page.locator("h1")).to_have_text("Campaigns")
    assert qparams(page)["platform"] == "google"
    assert page.locator("[data-testid=google-types]").count() == 0                                      # the types are the filter row here, not a table
    rows = page.locator("[data-testid=google-campaigns] tbody tr[data-key]")
    expect(rows.first).to_be_visible()
    n_all = rows.count()
    chips = page.locator("[data-testid=type-filter] [data-type]")
    assert chips.count() >= 3 and page.locator("[data-testid=type-filter] [data-type=all]").get_attribute("aria-pressed") == "true"
    chips.nth(1).click()
    expect(page).to_have_url(re.compile(r"type="))
    idle(page)
    expect(page.locator("[data-testid=google-campaigns] tbody tr[data-key]").first).to_be_visible()
    assert 0 < page.locator("[data-testid=google-campaigns] tbody tr[data-key]").count() < n_all
    platform_btn(page, "meta").click()                                                                  # the switch keeps you on Campaigns
    expect(page).to_have_url(re.compile(r"/campaigns"))
    assert "platform" not in qparams(page) and "type" not in qparams(page)
    expect(page.locator("[data-testid=platform-campaigns]")).to_be_visible()


@pytest.mark.mock_only
def test_all_platforms_overview(page):
    goto_product(page, "?window=14d&stage=scaling&compare=prev")
    platform_btn(page, "all").click()
    expect(page.locator("h1")).to_have_text("Overview")
    assert "stage" not in qparams(page) and "platform" not in qparams(page)                             # All is the default of the bare root
    expect(page.locator("#stage-ctl")).to_be_hidden()
    expect(page.locator("#source-ctl")).to_be_hidden()                                                  # each platform's own numbers and Upstack are side by side: no switch
    expect(page.locator("#source-ctl-inline")).to_be_hidden()
    expect(page.locator("#tieout-btn")).to_be_hidden()
    rows = page.locator("[data-testid=platform-overview] tbody tr")
    expect(rows).to_have_count(6)                                                                       # per platform: its own reporting, Upstack new customers, Upstack all orders
    text = [r.inner_text() for r in rows.all()]
    assert "Meta's own reporting" in text[0] and "Upstack · new customers" in text[1] and "Upstack · all orders" in text[2]
    assert "Google's own reporting" in text[3] and "Upstack · all orders" in text[5]
    spend = [num(x) for x in col(page, "spend", "platform-overview")]
    assert spend[0] == spend[1] == spend[2] and spend[3] == spend[4] == spend[5] and spend[0] > 0 and spend[3] > 0    # spend is the platform's in every row
    expect(page.locator("[data-testid=platform-overview] th[data-col=purchases]")).to_contain_text("Purchases / orders")
    expect(page.locator("#range-note")).to_contain_text("do not apply to Overview")
    page.locator("[data-testid=platform-overview] a", has_text="Campaigns →").first.click()              # Meta's campaigns
    expect(page).to_have_url(re.compile(r"/campaigns"))
    expect(page.locator("[data-testid=platform-campaigns]")).to_be_visible()
    shot(page, "12_all_platforms")


@pytest.mark.mock_only
def test_platform_campaigns_view_product_column_and_filter(page):
    goto_home(page, "?window=30d")
    page.locator("#view-switch [data-platform-view=campaigns]").click()
    expect(page).to_have_url(re.compile(r"/campaigns\?"))
    expect(page.locator("h1")).to_have_text("Campaigns")
    t = "[data-testid=platform-campaigns]"
    rows = page.locator(f"{t} tbody tr[data-key]")
    expect(rows.first).to_be_visible()
    n_all = rows.count()
    assert n_all >= 6
    spend_sum = sum(num(x) for x in col(page, "spend", "platform-campaigns"))
    assert abs(spend_sum - num(foot_value(page, "spend", "platform-campaigns"))) <= max(2.0, 0.001 * spend_sum)      # the Total row is the sum of the rows
    products_col = col(page, "product", "platform-campaigns")
    assert any(c.startswith("Mixed") for c in products_col), products_col                                      # a campaign that runs several products says so
    names = {c.split("\n")[0].strip() for c in products_col}
    assert {"Vita Bull", "Beef Liver", "Boneflex"} <= names
    chips = page.locator("[data-testid=product-filter] [data-product]")
    assert [c.get_attribute("data-product") for c in chips.all()][:2] == ["all", "vita-bull"]
    chips.nth(1).click()                                                                                # Vita Bull only
    expect(page).to_have_url(re.compile(r"product=vita-bull"))
    expect(page.locator("[data-testid=product-filter] [data-product=vita-bull]")).to_have_attribute("aria-pressed", "true")
    idle(page)
    expect(page.locator(f"{t} tbody tr[data-key]").first).to_be_visible()
    n_vb = page.locator(f"{t} tbody tr[data-key]").count()
    assert 0 < n_vb < n_all
    expect(page.locator("#level-title")).to_contain_text("Vita Bull")
    assert qparams(page)["window"] == "30d"
    page.locator(f"{t} tbody tr[data-key]").first.locator("a.rowlink").click()                          # a campaign opens its ads in that product, as in the product view
    expect(page).to_have_url(re.compile(r"/p/vita-bull\?.*view=ads.*campaign_id="))
    expect(page.locator("h1")).to_contain_text("Vita Bull")
    page.go_back()
    page.locator("[data-testid=product-filter] [data-product=all]").click()
    page.wait_for_function("() => !new URL(location.href).searchParams.has('product')")
    idle(page)
    expect(page.locator(f"{t} tbody tr[data-key]").first).to_be_visible()
    assert page.locator(f"{t} tbody tr[data-key]").count() == n_all
    page.locator("#source-ctl [data-source=upstack]").click()                                           # the Numbers-from flip applies like everywhere
    expect(page.locator(f"{t} th[data-col=purchases]")).to_contain_text("New customers")
    shot(page, "13_platform_campaigns")


@pytest.mark.mock_only
def test_numbers_from_switch_sits_beside_the_explanation_on_a_wide_window(page):
    page.set_viewport_size({"width": 2100, "height": 1000})                                             # Josh's 4K window
    goto_product(page, "?window=14d")
    box = lambda sel: page.locator(sel).bounding_box()                                                  # noqa: E731
    bar, tie, inline = box("#controls .controls-in"), box("#tieout-btn"), box("#source-ctl-inline")
    assert bar["width"] <= 1601, bar                                                                    # the bar is capped, not 2,000 px wide
    assert abs(inline["x"] + inline["width"] - tie["x"]) < 260, (inline, tie)                           # the switch is right next to the tie-out button
    assert page.evaluate("() => document.querySelector('#view').getBoundingClientRect().width") <= 1601
    assert page.evaluate("() => document.documentElement.scrollWidth <= innerWidth + 1")
    expect(page.locator("[data-testid=src-badge]").first).to_have_text("Numbers: Meta")
    page.locator("[data-testid=switch-source]").click()                                                 # the words "Switch to Upstack" in the note are a button
    expect(page).to_have_url(re.compile(r"source=upstack"))
    expect(page.locator("[data-testid=src-badge]").first).to_have_text("Numbers: Upstack · new customers")
    expect(page.locator("#source-ctl-inline [data-src-inline=upstack]")).to_have_attribute("aria-pressed", "true")
    expect(page.locator("#source-ctl [data-source=upstack]")).to_have_attribute("aria-pressed", "true")      # one state, two places
    page.locator("#basis-ctl-inline [data-basis-inline=all]").click()
    expect(page).to_have_url(re.compile(r"basis=all"))
    expect(page.locator("[data-testid=src-badge]").first).to_have_text("Numbers: Upstack · all orders")
    open_first_row(page)                                                                                # product page: same switch in view, same state
    expect(page.locator("#source-ctl-inline")).to_be_visible()
    expect(page.locator("[data-testid=src-badge]").first).to_have_text("Numbers: Upstack · all orders")
    page.locator("[data-testid=switch-source]").click()                                                 # "Switch to Meta's own numbers"
    page.wait_for_function("() => !new URL(location.href).searchParams.has('source')")
    expect(page.locator("[data-testid=src-badge]").first).to_have_text("Numbers: Meta")
    page.set_viewport_size({"width": 1280, "height": 900})


@pytest.mark.mock_only
def test_platform_campaigns_chart_split_grain_and_campaign_narrowing(page):
    goto_home(page, "?window=30d")
    page.locator("#view-switch [data-platform-view=campaigns]").click()
    wait_trend(page)
    open_chart_options(page)
    info = trend_info(page)
    assert [d["label"] for d in bars_ds(info)] == ["Spend"] and line_ds(info)[0]["label"].startswith("ROAS")
    table_total = num(foot_value(page, "spend", "platform-campaigns"))
    assert abs(chart_sum(info) - table_total) <= max(2.0, 0.002 * table_total)                      # the chart adds up to the table's total
    assert [b.get_attribute("data-split") for b in page.locator("#trend-split .seg").all()] == ["none", "stage", "product"]
    assert [b.get_attribute("data-grain") for b in page.locator("#trend-grain .seg").all()] == ["day", "week", "month"]
    page.locator("#trend-split [data-split=product]").click()
    page.wait_for_function("() => Chart.getChart('trend-chart').data.datasets.filter(d => d.type === 'bar').length > 1")
    info = trend_info(page)
    names = {d["label"] for d in bars_ds(info)}
    assert {"Vita Bull", "Beef Liver", "Boneflex"} <= names and all(d["stack"] for d in bars_ds(info))     # stacked bars, one per product
    assert abs(chart_sum(info) - table_total) <= max(2.0, 0.002 * table_total)
    assert qparams(page)["ts"] == "product"
    page.locator("#trend-split [data-split=stage]").click()
    page.wait_for_function("() => Chart.getChart('trend-chart').data.datasets.some(d => d.label === 'Scaling')")
    page.locator("#trend-grain [data-grain=week]").click()
    page.wait_for_function("() => new URL(location.href).searchParams.get('tg') === 'week'")
    wait_trend(page)
    info = trend_info(page)
    assert len(info["labels"]) < 8 and abs(chart_sum(info) - table_total) <= max(2.0, 0.002 * table_total)
    page.locator("#trend-grain [data-grain=day]").click()
    page.locator("#trend-split [data-split=none]").click()
    # a campaign row's Chart button narrows the chart (not the table)
    n_rows = page.locator("[data-testid=platform-campaigns] tbody tr[data-key]").count()
    first = page.locator("[data-testid=platform-campaigns] tbody tr[data-key]").nth(2)
    row_spend = num(first.locator("td").nth(col_index(page, "spend", "platform-campaigns")).inner_text())
    first.locator("[data-testid=chart-row]").click()
    expect(page.locator("[data-testid=chart-scope]")).to_be_visible()
    assert "chart" in qparams(page)
    idle(page)
    wait_trend(page)
    info = trend_info(page)
    assert abs(chart_sum(info) - row_spend) <= max(2.0, 0.002 * row_spend)
    assert page.locator("[data-testid=platform-campaigns] tbody tr[data-key]").count() == n_rows
    page.locator("[data-testid=chart-scope] a").click()
    page.wait_for_function("() => !new URL(location.href).searchParams.has('chart')")
    shot(page, "14_platform_campaigns_chart")


@pytest.mark.mock_only
def test_all_platforms_chart_platform_series_numbers_and_links(page):
    page.goto("/?platform=all&window=30d")
    wait_trend(page)
    open_chart_options(page)
    info = trend_info(page)
    assert [d["label"] for d in bars_ds(info)] == ["Spend"]                                            # the default is the total: one bar per day = Meta + Google spend, not broken out
    assert [b.get_attribute("data-split") for b in page.locator("#trend-split .seg").all()] == ["none", "platform"]
    expect(page.locator("#trend-split [data-split=none]")).to_have_attribute("aria-pressed", "true")
    assert "ts" not in qparams(page)                                                                   # the total is the default: nothing in the URL
    assert [d["label"] for d in line_ds(info)] == ["NC ROAS (total)"]                                  # the default Line is the total NC ROAS (actual new-customer revenue / total spend)
    total_bars = chart_sum(info)
    shot(page, "17_all_platforms_total_chart")
    page.locator("#trend-split [data-split=platform]").click()                                         # Split bars by Platform stacks the same total into Meta + Google
    page.wait_for_function("() => new URL(location.href).searchParams.get('ts') === 'platform'")
    wait_trend(page)
    info = trend_info(page)
    assert {d["label"] for d in bars_ds(info)} == {"Meta", "Google"} and all(d["stack"] for d in bars_ds(info))
    assert abs(chart_sum(info) - total_bars) <= 0.02
    pick_metric(page, "line", "roas")
    wait_trend(page)
    info = trend_info(page)
    assert {d["label"] for d in line_ds(info)} == {"ROAS · Meta", "ROAS · Google", "NC ROAS (total)"}  # one ROAS line per platform, and the actual total next to them
    opts = page.locator("#trend-bars option").all_inner_texts()
    assert opts == ["Spend", "Impressions", "Link clicks", "None"], opts                               # only what may be added across platforms
    assert [b.get_attribute("data-numbers") for b in page.locator("#trend-numbers .seg").all()] == ["own", "nc", "all"]
    own_spend = chart_sum(info, "Meta")
    page.locator("#trend-numbers [data-numbers=nc]").click()
    page.wait_for_function("() => new URL(location.href).searchParams.get('nb') === 'nc'")
    wait_trend(page)
    assert "Upstack attribution is drawn only where Upstack has it" in page.locator("#trend-note").inner_text()
    assert abs(chart_sum(trend_info(page), "Meta") - own_spend) <= 1                                   # spend is the platform's on every basis
    expect(page.locator("#view-switch [data-platform-view=campaigns]")).to_have_text("Campaigns")
    page.locator("#view-switch [data-platform-view=campaigns]").click()                                # Overview -> Campaigns
    expect(page).to_have_url(re.compile(r"/campaigns\?"))
    assert qparams(page)["platform"] == "all"
    expect(page.locator("h1")).to_have_text("Campaigns")
    page.locator("#view-switch [data-platform-view=home]").click()                                     # and back
    expect(page.locator("h1")).to_have_text("Overview")
    shot(page, "15_all_platforms_chart")


@pytest.mark.mock_only
def test_all_campaigns_one_table_basis_labels_totals_and_filters(page):
    page.goto("/campaigns?platform=all&window=30d")
    t = "[data-testid=all-campaigns]"
    page.locator(f"{t} tbody tr[data-key]").first.wait_for()
    data = table_data(page, "all-campaigns")
    platform_i, basis_i, group_i = (data["heads"].index(k) for k in ("platform", "basis", "group"))
    plats = {r[platform_i] for r in data["rows"]}
    assert plats == {"Meta", "Google"}
    assert {r[basis_i] for r in data["rows"] if r[platform_i] == "Meta"} == {"Meta's own reporting"}      # each row says which basis it is counted on
    assert {r[basis_i] for r in data["rows"] if r[platform_i] == "Google"} == {"Google's own reporting"}
    assert any(r[group_i].startswith("Mixed") for r in data["rows"]) and {"Shopping", "Search"} <= {r[group_i] for r in data["rows"] if r[platform_i] == "Google"}
    spend = [num(x) for x in col(page, "spend", "all-campaigns")]
    assert spend == sorted(spend, reverse=True)                                                        # sorted by spend
    foot = data["footRows"]
    assert foot[0]["cells"][0].startswith("Total (spend, impressions, clicks)")
    head = data["heads"]
    tot = foot[0]["cells"]
    assert num(tot[head.index("spend")]) > 0 and num(tot[head.index("impressions")]) > 0 and num(tot[head.index("link_clicks")]) > 0
    for k in ("purchases", "revenue", "roas", "cpa"):
        assert num(tot[head.index(k)]) is None, k                                                      # purchases / revenue / ROAS are not added across platforms
    subs = {r["cells"][0].split(" ")[0]: r["cells"] for r in foot[1:]}
    assert set(subs) == {"Meta", "Google"} and all(num(c[head.index("purchases")]) for c in subs.values())
    assert abs(num(tot[head.index("spend")]) - (num(subs["Meta"][head.index("spend")]) + num(subs["Google"][head.index("spend")]))) <= 2
    expect(page.locator("[data-testid=all-campaigns-note]")).to_contain_text("Not added up")
    # the chart adds up to the table
    wait_trend(page)
    assert abs(chart_sum(trend_info(page)) - num(tot[head.index("spend")])) <= max(3.0, 0.002 * num(tot[head.index("spend")]))
    # Platform filter: Meta only = Meta's own Campaigns total
    page.locator("[data-testid=only-meta]").click()
    expect(page).to_have_url(re.compile(r"only=meta"))
    idle(page)
    page.locator(f"{t} tbody tr[data-key]").first.wait_for()
    assert {r[platform_i] for r in table_data(page, "all-campaigns")["rows"]} == {"Meta"}
    all_meta_total = {k: num(foot_value(page, k, "all-campaigns")) for k in ("spend", "purchases", "revenue")}
    assert page.locator(f"{t} tfoot tr").count() == 1 and all(v for v in all_meta_total.values())      # one platform in view: the Total row carries purchases and revenue
    page.goto("/campaigns?window=30d")
    page.locator("[data-testid=platform-campaigns] tbody tr[data-key]").first.wait_for()
    for k in ("spend", "purchases", "revenue"):
        assert abs(num(foot_value(page, k, "platform-campaigns")) - all_meta_total[k]) <= max(1.0, 0.001 * all_meta_total[k]), k
    # Product and Type filters
    page.goto("/campaigns?platform=all&window=30d")
    page.locator("[data-testid=product-vita-bull]").click()
    expect(page).to_have_url(re.compile(r"product=vita-bull"))
    idle(page)
    page.locator(f"{t} tbody tr[data-key]").first.wait_for()
    assert {r[platform_i] for r in table_data(page, "all-campaigns")["rows"]} == {"Meta"}
    page.locator("[data-testid=type-Search]").click()
    expect(page).to_have_url(re.compile(r"type=Search"))
    assert "product" not in qparams(page)
    idle(page)
    page.locator(f"{t} tbody tr[data-key]").first.wait_for()
    d2 = table_data(page, "all-campaigns")
    assert {r[platform_i] for r in d2["rows"]} == {"Google"} and {r[group_i] for r in d2["rows"]} == {"Search"}
    # Numbers from Upstack: the Numbers column follows per row
    page.goto("/campaigns?platform=all&window=30d")
    page.locator(f"{t} tbody tr[data-key]").first.wait_for()
    page.locator("#source-ctl [data-source=upstack]").click()
    expect(page).to_have_url(re.compile(r"source=upstack"))
    idle(page)
    d3 = table_data(page, "all-campaigns")
    assert {r[basis_i] for r in d3["rows"]} == {"Upstack · new customers"}
    expect(page.locator("[data-testid=src-badge]").first).to_have_text("Numbers: Upstack · new customers")
    page.locator("#basis-ctl [data-basis=all]").click()
    idle(page)
    assert {r[basis_i] for r in table_data(page, "all-campaigns")["rows"]} == {"Upstack · all orders"}
    # the platform picker keeps you on Campaigns
    page.locator("#platform-ctl [data-platform=google]").click()
    expect(page).to_have_url(re.compile(r"/campaigns\?"))
    assert qparams(page)["platform"] == "google"
    page.locator("#platform-ctl [data-platform=all]").click()
    expect(page).to_have_url(re.compile(r"/campaigns\?"))
    assert qparams(page)["platform"] == "all"
    shot(page, "16_all_campaigns")


@pytest.mark.mock_only
def test_totals_strip_and_total_line_on_all_platforms_tie_to_the_chart_and_the_table(page):
    page.goto("/?window=30d")
    wait_strip(page)
    wait_trend(page)
    open_chart_options(page)
    info = trend_info(page)
    # chart: total spend is the stack, the default Line is the dark total NC ROAS line
    assert [d["label"] for d in line_ds(info)] == ["NC ROAS (total)"] and page.locator("#trend-line").input_value() == "nc_roas_total"
    assert page.locator("#trend-line option").first.inner_text() == "NC ROAS (total)"
    assert "nc_roas_total" not in page.locator("#trend-bars").inner_html()                             # a line only: never a Bars choice
    spend = float(strip_val(page, "spend"))
    assert abs(chart_sum(info) - spend) <= 0.011                                                       # strip = chart total, to the cent
    assert strip_text(page, "spend") == "$" + f"{round(spend):,}"
    # the table below says the same: the All campaigns table total is the strip's total spend
    page.locator("#view-switch [data-platform-view=campaigns]").click()
    page.locator("[data-testid=all-campaigns] tbody tr[data-key]").first.wait_for()
    wait_strip(page)
    data = table_data(page, "all-campaigns")
    tot = num(data["footRows"][0]["cells"][data["heads"].index("spend")])
    assert strip_text(page, "spend") == "$" + f"{round(float(strip_val(page, 'spend'))):,}" and abs(tot - float(strip_val(page, "spend"))) <= 1
    # Total NC ROAS = actual NC revenue / spend of the same days, recomputed from the daily total series (ratio of sums)
    t = api_json(page, "/api/platforms/trend?window=30d&grain=day")["total"]
    D = [p for p in t["points"] if p and p["nc_rev"] is not None]
    rev, sp = sum(p["nc_rev"] for p in D), sum(p["nc_spend"] for p in D)
    assert abs(float(strip_val(page, "revenue")) - rev) <= 0.011 and abs(float(strip_val(page, "roas")) - rev / sp) <= 0.00006
    assert strip_text(page, "roas") == f"{rev / sp:.2f}x"
    a = page.locator("[data-testid=kpi-attributed]").inner_text()                                      # the platforms' attributed sum and the gap sit next to the actual
    assert "Platforms attribute" in a and "gap" in a and "overlap" in a and "unattributed" in a
    k = api_json(page, "/api/platforms/kpis?window=30d")["total"]["nc"]["attributed"]
    assert abs(k["sum"] - (k["meta"] + k["google"])) <= 0.011 and abs(k["gap"] - (k["sum"] - rev)) <= 0.011
    # hover: both platforms' spend, the total and the total NC ROAS
    lines = page.evaluate("() => Chart.getChart('trend-chart').options.plugins.tooltip.callbacks.afterBody([{dataIndex: 12}])")
    assert [x.split(":")[0] for x in lines] == ["Meta spend", "Google spend", "Total spend", "Total NC ROAS"], lines
    # ROAS as the line: each platform's own line plus the actual total beside them
    open_chart_options(page)
    pick_metric(page, "line", "roas")
    wait_trend(page)
    assert {d["label"] for d in line_ds(trend_info(page))} == {"ROAS · Meta", "ROAS · Google", "NC ROAS (total)"}
    shot(page, "16_totals_strip_all_campaigns")


@pytest.mark.mock_only
def test_total_nc_roas_buckets_are_ratio_of_sums_and_totals_label_the_bars(page):
    page.goto("/?window=launch&tg=week")
    wait_strip(page)
    wait_trend(page)
    k = api_json(page, "/api/platforms/kpis?window=launch")["total"]["nc"]
    wk = api_json(page, "/api/platforms/trend?window=launch&grain=week")["total"]["points"]
    W = [p for p in wk if p and p["nc_rev"] is not None]
    assert abs(sum(p["nc_rev"] for p in W) / sum(p["nc_spend"] for p in W) - k["roas"]) <= 0.00006        # the weekly buckets weigh back to the strip: ratio of sums
    day = api_json(page, "/api/platforms/trend?window=launch&grain=day")["total"]["points"]
    first_with = next(i for i, p in enumerate(day) if p["nc_rev"] is not None)
    assert first_with == 3 and all(p["nc_roas"] is None and p["nc_rev"] is None for p in day[:3]) and day[0]["spend"] > 0       # blank before the orders feed starts, spend kept
    assert "of" in page.locator("[data-testid=kpi-partial]").inner_text()                              # the days without order data are said, not hidden
    note = page.locator("#trend-note").inner_text()
    assert "Total NC ROAS is available from" in note and "blank, not zero" in note
    page.locator("#trend-grain [data-grain=day]").click()
    wait_trend(page)
    line = line_ds(trend_info(page))[0]
    assert line["data"][:3] == [None, None, None] and line["data"][3] is not None                      # drawn only where it exists, never as zero
    assert page.evaluate("() => Chart.getChart('trend-chart').options.plugins.stackTotals.on") is False            # 180 daily bars: no labels
    page.locator("#trend-grain [data-grain=month]").click()
    wait_trend(page)
    assert page.evaluate("() => Chart.getChart('trend-chart').options.plugins.stackTotals.on") is True             # a few monthly bars: the total sits on each stack


@pytest.mark.mock_only
@pytest.mark.parametrize("basis", ["", "&source=upstack&basis=nc", "&source=upstack&basis=all"])
@pytest.mark.parametrize("key", ["meta", "google"])
def test_platform_filter_switches_strip_and_chart_to_that_platforms_own_figures(page, key, basis):
    name = key.capitalize()
    page.goto(f"/campaigns?platform=all&window=30d{basis}")
    wait_strip(page)
    wait_trend(page)
    assert tile_ids(page) == TILES and tile_labels(page) == ["Total spend", "Total NC revenue", "Total NC ROAS"]      # blended: Platform = All
    expect(page.locator("[data-testid=totals-strip]")).to_have_attribute("data-view", "all")
    page.locator("[data-testid=all-campaigns] tbody tr[data-key]").first.wait_for()
    subs = {r["cells"][0].split(" ")[0]: r["cells"] for r in table_data(page, "all-campaigns")["footRows"][1:]}
    head = table_data(page, "all-campaigns")["heads"]
    page.locator(f"[data-testid=only-{key}]").click()                                                              # filter Platform to Meta / Google
    expect(page).to_have_url(re.compile(rf"only={key}"))
    idle(page)
    page.locator("[data-testid=all-campaigns] tbody tr[data-key]").first.wait_for()
    wait_strip(page)
    wait_trend(page)
    expect(page.locator(f"[data-testid=totals-strip][data-view={key}]")).to_have_count(1)
    assert tile_ids(page) == TILES                                                                                # the same three tiles in the same layout
    nc = "nc" in basis
    assert tile_labels(page) == [f"{name} spend", f"{name} {'NC revenue' if nc else 'revenue'}", f"{name} {'NC ROAS' if nc else 'ROAS'}"], tile_labels(page)
    expect(page.locator("[data-testid=kpi-roas-hidden]")).to_have_count(0)                                         # no blended ratio, no 'hidden' sentence
    assert page.locator("[data-testid=kpi-view-note]").inner_text().startswith(f"Showing {name} only, on ")
    # numbers: KPI spend = the platform's subtotal in the All view = the filtered table's total row, to the dollar; revenue and ROAS likewise
    assert abs(float(strip_val(page, "spend")) - num(subs[name][head.index("spend")])) <= 1
    assert abs(float(strip_val(page, "spend")) - table_total(page, "all-campaigns", "spend")) <= 1
    assert abs(float(strip_val(page, "revenue")) - num(subs[name][head.index("revenue")])) <= 1
    assert abs(float(strip_val(page, "roas")) - num(subs[name][head.index("roas")])) <= 0.006
    assert strip_text(page, "roas") == f"{float(strip_val(page, 'roas')):.2f}x"
    # the chart is that platform's: its stack, its own ROAS line (no total line, no NC ROAS (total) choice)
    info = trend_info(page)
    assert {d["label"] for d in bars_ds(info)} == {name} and abs(chart_sum(info) - float(strip_val(page, "spend"))) <= 0.011
    assert [d["label"] for d in line_ds(info)] == [f"{'NC ROAS' if nc else 'ROAS'} · {name}"]
    assert "nc_roas_total" not in page.locator("#trend-line").inner_html()
    assert qparams(page)["only"] == key and ("platform" not in qparams(page) or qparams(page)["platform"] == "all")   # the filter lives in the URL


@pytest.mark.mock_only
def test_meta_only_and_google_only_kpi_spend_equal_the_subtotals_and_add_to_the_blended_total(page):
    page.goto("/campaigns?platform=all&window=30d")
    wait_strip(page)
    page.locator("[data-testid=all-campaigns] tbody tr[data-key]").first.wait_for()
    data = table_data(page, "all-campaigns")
    subs = {r["cells"][0].split(" ")[0]: num(r["cells"][data["heads"].index("spend")]) for r in data["footRows"][1:]}
    blended = float(strip_val(page, "spend"))
    got = {}
    for key in ("meta", "google"):
        k = api_json(page, f"/api/platforms/kpis?window=30d&only={key}")["total"]
        got[key] = k["spend"]
        assert abs(k["spend"] - subs[key.capitalize()]) <= 1, (key, k["spend"], subs)                          # = the Meta / Google subtotal row of the table
    assert abs(got["meta"] + got["google"] - blended) <= 0.011                                                    # and the two add to the blended Total spend


@pytest.mark.mock_only
@pytest.mark.parametrize("path,key,table,name", [
    ("/?platform=meta&window=30d", "meta", "products-table", "Meta"), ("/campaigns?window=30d", "meta", "platform-campaigns", "Meta"),
    ("/?platform=google&window=30d", "google", "google-campaigns", "Google"), ("/campaigns?platform=google&window=30d", "google", "google-campaigns", "Google"),
])
def test_meta_and_google_pages_carry_the_same_strip_with_their_own_figures(page, path, key, table, name):
    page.goto(path)
    wait_strip(page)
    page.locator(f"[data-testid={table}] tbody tr[data-key]").first.wait_for()
    expect(page.locator(f"[data-testid=totals-strip][data-view={key}]")).to_have_count(1)
    assert tile_ids(page) == TILES and tile_labels(page) == [f"{name} spend", f"{name} revenue", f"{name} ROAS"]
    spend = float(strip_val(page, "spend"))
    data = table_data(page, table)
    foot = [r["cells"] for r in data["footRows"]] if table == "products-table" else [data["footRows"][0]["cells"]]
    want = max(num(c[data["heads"].index("spend")]) or 0 for c in foot)                                          # the Meta landing's biggest total row is the whole account
    assert abs(spend - want) <= 1, (path, spend, foot)
    assert float(strip_val(page, "roas")) > 0 and float(strip_val(page, "revenue")) > 0
    page.goto(path + "&source=upstack&basis=nc")                                                                 # the Numbers basis in force is the strip's basis
    wait_strip(page)
    assert tile_labels(page) == [f"{name} spend", f"{name} NC revenue", f"{name} NC ROAS"]
    assert "Upstack · new customers" in page.locator("[data-testid=totals-strip] .kpi-sub").nth(1).inner_text()


@pytest.mark.mock_only
def test_totals_strip_shows_window_b_when_compare_is_on(page):
    page.goto("/?window=14d&compare=prev")
    wait_strip(page)
    expect(page.locator("[data-testid=totals-strip] [data-testid=kpi-compare]")).to_have_count(3)
    expect(page.locator("#range-note")).to_contain_text("totals strip")
    k = api_json(page, "/api/platforms/kpis?window=14d&compare=prev")
    assert k["compare"] and abs(k["delta"]["spend_pct"] - (k["total"]["spend"] - k["compare"]["spend"]) / k["compare"]["spend"]) <= 0.0002
    txt = page.locator("[data-testid=totals-strip] [data-testid=kpi-compare]").first.inner_text()
    assert txt.startswith("Window B: $") and ("▲" in txt or "▼" in txt)


@pytest.mark.mock_only
@pytest.mark.parametrize("width", [375, 1024, 2100])
def test_totals_strip_is_readable_and_never_scrolls_the_page_sideways(page, width):
    page.set_viewport_size({"width": width, "height": 900})
    for path in ("/?window=30d", "/campaigns?platform=all&window=30d", "/campaigns?platform=all&window=30d&only=google", "/campaigns?window=30d"):
        page.goto(path)
        wait_strip(page)
        wait_trend(page)
        boxes = page.evaluate("() => [...document.querySelectorAll('[data-testid=totals-strip] .kpi')].map(k => { const r = k.getBoundingClientRect(); return {l: r.left, r: r.right, t: r.top, w: r.width, sw: k.scrollWidth, cw: k.clientWidth}; })")
        assert len(boxes) == 3, boxes
        assert all(b["l"] >= 0 and b["r"] <= width + 0.5 and b["sw"] <= b["cw"] + 1 for b in boxes), boxes   # nothing clipped or pushed past the edge
        assert page.evaluate("() => document.documentElement.scrollWidth <= innerWidth + 1"), (path, width)
        if width == 375:
            assert len({round(b["l"]) for b in boxes}) == 1 and boxes[1]["t"] > boxes[0]["t"]            # a phone stacks the tiles
        else:
            assert len({round(b["t"]) for b in boxes}) == 1, boxes                                       # a laptop and a 4K window show them in one row
            assert page.evaluate("() => document.querySelector('[data-testid=totals-strip]').getBoundingClientRect().width") <= 1601
        assert all(b["w"] >= 200 for b in boxes), boxes
    shot(page, f"17_totals_strip_{width}")
    page.set_viewport_size({"width": 1280, "height": 900})


# ------------------------------------------------------------------ calm first view: one header, chart options tucked away
@pytest.mark.mock_only
def test_chart_options_are_closed_by_default_keep_grain_and_open_with_a_chart_choice(page):
    page.goto("/?window=30d")
    wait_trend(page)
    det = page.locator("[data-testid=chart-options]")
    expect(det).to_have_count(1)
    assert det.get_attribute("open") is None
    expect(page.locator("#trend-grain [data-grain=week]")).to_be_visible()                              # Grain stays in view
    for sel in ("#trend-bars", "#trend-line", "#trend-split", "#trend-numbers", "#trend-swap"):
        expect(page.locator(sel)).to_be_hidden()                                                       # the rest sits behind Chart options
    expect(page.locator("#trend-panel .trend-controls > .chart-options #trend-split")).to_have_count(1)  # same ids, moved not rebuilt
    open_chart_options(page)
    for sel in ("#trend-bars", "#trend-line", "#trend-split", "#trend-numbers", "#trend-swap"):
        expect(page.locator(sel)).to_be_visible()
    page.goto("/?window=30d&ts=platform")                                                                # a chart choice in the URL opens it
    wait_trend(page)
    expect(page.locator("[data-testid=chart-options]")).to_have_attribute("open", "")


@pytest.mark.mock_only
@pytest.mark.parametrize("path,title,h1", [("/", "Overview", "Overview"), ("/?platform=google", "Google", "Google"), ("/campaigns?platform=all", "Campaigns", "Campaigns"),
                                           ("/campaigns?platform=meta", "Campaigns", "Campaigns"), ("/?platform=meta", "Meta", "Meta")])
def test_performance_pages_share_one_header_and_lonvera_title(page, path, title, h1):
    page.goto(path)
    page.wait_for_selector(".pagehead h1")
    expect(page.locator("h1")).to_have_text(h1)
    assert page.title() == f"{title} · Lonvera", page.title()
    expect(page.locator(".pagehead .pagehead-actions #view-switch")).to_have_count(1)                   # the view switch lives in the header's action slot
    assert page.locator(".pagehead .note-line").inner_text().strip()
    crumbs = page.locator(".crumbrow").all_inner_texts()
    assert not crumbs or crumbs[0].startswith("Lonvera") or crumbs[0].startswith("Meta"), crumbs
    page_text = page.locator("#view").inner_text()
    assert "\u2014" not in page_text, "no em dashes on the page"


@pytest.mark.mock_only
def test_meta_landing_tracker_badge_has_no_em_dash(page):
    page.goto("/?platform=meta")
    page.wait_for_selector("[data-testid=products-table] tbody tr[data-key]")
    txt = page.locator("[data-testid=products-table]").inner_text()
    assert "Line-mapped tags" in txt or "Tracker tags, not line-mapped yet" in txt
    assert "Tracker tags \u2014 not line-mapped yet" not in txt


@pytest.mark.mock_only
def test_google_trend_bars_and_line_are_tucked_into_chart_options(page):
    page.goto("/?platform=google")
    expect(page.locator("[data-testid=google-trend] canvas")).to_be_visible()
    det = page.locator("[data-testid=google-trend] [data-testid=chart-options]")
    assert det.get_attribute("open") is None
    expect(page.locator("#g-bars")).to_be_hidden()
    expect(page.locator("[data-testid=google-trend] [data-grain=week]")).to_be_visible()
    det.locator("summary").click()
    expect(page.locator("#g-bars")).to_be_visible()
    page.locator("#g-bars").select_option("impressions")
    expect(page.locator("[data-testid=google-trend] canvas")).to_be_visible()
