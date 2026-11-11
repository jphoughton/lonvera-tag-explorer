"""The Marketing metrics Trend panel at the top of the Pacing page (headless Chromium): default state, controls, Hide persistence, error state, 375px."""
import datetime as dt
import json
import pathlib
import threading

import pytest

playwright = pytest.importorskip("playwright.sync_api")
from tests._servers import make_server                                   # noqa: E402

from pacing_support import make_app                                         # noqa: E402

SHOTS = pathlib.Path(__file__).resolve().parents[2] / "ui" / "shots"
SHOTS.mkdir(exist_ok=True)


@pytest.fixture()
def base_url(api_app, pacing_db):
    app = make_app(pacing_db, AUTH_DISABLED=True)
    srv = make_server("127.0.0.1", 0, app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()
    app.extensions["pool"].close()


@pytest.fixture()
def browser():
    with playwright.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:                                                # noqa: BLE001
            pytest.skip(f"Chromium not available: {e}")
        yield b
        b.close()


def series(grain, n=60):
    start = dt.date(2026, 9, 1)
    step = {"day": 1, "week": 7, "month": 30}[grain]
    n = n if grain == "day" else (9 if grain == "week" else 3)
    mp, gp, tp = [], [], []
    for i in range(n):
        t = (start + dt.timedelta(days=step * i)).isoformat()
        m, g = 9000 + 100 * i, 600 + 5 * i
        mk = lambda s: dict(t=t, spend=s, purchases=s / 100, revenue=s * 0.8, roas=0.8, cpa=100.0, impressions=s * 40, link_clicks=s / 2, ctr=0.01, cpc=2.0, cpm=25.0, conv_rate=0.03)  # noqa: E731
        mp.append(mk(m)); gp.append(mk(g))
        tp.append(dict(t=t, spend=m + g, meta_spend=m, google_spend=g, nc_roas=0.6 + (i % 7) / 100, nc_days=1))
    labels = [pt["t"] for pt in mp]
    comb = [dict(t=a["t"], spend=a["spend"] + b["spend"], impressions=a["impressions"] + b["impressions"], link_clicks=a["link_clicks"] + b["link_clicks"]) for a, b in zip(mp, gp)]
    return dict(window_info=dict(note="Since launch here means the days every platform has data: May 6, 2026 – Nov 8, 2026."), compare_info=None, grain=grain, labels=labels, points=comb,
                groups=[dict(key="meta", label="Meta", color="#1a5fb4", points=mp), dict(key="google", label="Google", color="#d9822b", points=gp)],
                total=dict(available=True, orders_first=labels[0], points=tp), notes=[])


def open_page(browser, base_url, size=(1440, 900), mode="mock", init=None):
    ctx = browser.new_context(viewport={"width": size[0], "height": size[1]}, color_scheme="light")
    if init:
        ctx.add_init_script(init)
    page = ctx.new_page()
    errors, reqs = [], []
    page.on("console", lambda m: errors.append(m.text) if m.type in ("error", "warning") else None)
    page.on("pageerror", lambda e: errors.append("PAGEERROR " + str(e)))

    def trend(route):
        url = route.request.url
        reqs.append(dict(p.split("=") for p in url.split("?", 1)[1].split("&")))
        if mode == "fail":
            return route.fulfill(status=500, content_type="application/json", body=json.dumps({"error": "boom"}))
        g = reqs[-1].get("grain", "day")
        return route.fulfill(status=200, content_type="application/json", body=json.dumps(series(g)))
    page.route("**/api/platforms/trend*", trend)
    page.goto(base_url + "/projection/pacing")
    page.wait_for_selector("body[data-pg-state='ready']")
    page.wait_for_selector(".pacing-trend-scroll table")
    return ctx, page, errors, reqs


def pressed(page, sel):
    return page.locator(f"{sel} .seg[aria-pressed='true']").inner_text()


def test_panel_sits_under_the_header_above_the_cards_with_the_default_state(browser, base_url):
    ctx, page, errors, reqs = open_page(browser, base_url)
    page.wait_for_function("document.querySelector('#trend-status').textContent === '' && document.querySelector('#trend-note').textContent.includes('Since launch')")
    kids = page.evaluate("[...document.querySelector('.pg-page').children].map(c => c.id || c.className).slice(0, 3)")
    assert kids[1] == "trend-panel pg-trendpanel" or "trend" in kids[1]
    assert page.evaluate("document.querySelector('.pg-page').children[0].querySelector('h1.pg-h1') !== null")
    assert page.locator("#trend-panel h2").inner_text() == "Trend"
    assert page.locator("#trend-bars").input_value() == "spend"
    assert page.locator("#trend-line").input_value() == "nc_roas_total"
    assert pressed(page, "#trend-grain") == "Day"
    assert pressed(page, "#trend-split") == "Total"
    assert pressed(page, "#trend-numbers") == "Own reporting"
    assert page.locator("#trend-toggle").inner_text() == "Hide"
    assert page.locator("#trend-chart").is_visible()
    assert reqs[0].get("grain") == "day" and reqs[0].get("numbers") == "own" and "window" not in reqs[0]    # since launch = no window param
    assert page.locator(".pacing-card").count() == 6
    # the page URL is untouched by the panel
    assert "tg=" not in page.url and "nb=" not in page.url
    page.screenshot(path=str(SHOTS / "pacing_trend_desktop.png"), full_page=False)
    assert errors == []
    ctx.close()


def test_controls_change_the_request_and_the_chart(browser, base_url):
    ctx, page, errors, reqs = open_page(browser, base_url)
    page.wait_for_selector("#trend-chart")
    page.click("#trend-grain .seg[data-grain='week']")
    page.wait_for_function("document.querySelector('#trend-grain .seg[aria-pressed=true]').dataset.grain === 'week'")
    assert reqs[-1]["grain"] == "week"
    page.click("#trend-split .seg[data-split='platform']")
    page.wait_for_timeout(300)
    assert pressed(page, "#trend-split") == "Platform"
    page.click("#trend-numbers .seg[data-numbers='nc']")
    page.wait_for_timeout(300)
    assert reqs[-1]["numbers"] == "nc"
    # Bars metric changes locally, the chart re-fetches and the bar label follows
    page.select_option("#trend-bars", "impressions")
    page.wait_for_timeout(300)
    assert page.locator("#trend-bars").input_value() == "impressions"
    # Swap needs a real line metric: pick ROAS as the line first, then swap
    page.select_option("#trend-line", "roas")
    page.wait_for_timeout(200)
    page.click("#trend-swap")
    page.wait_for_timeout(300)
    # the All views only allow additive bars: ROAS cannot become the bars, so the bars fall back to Spend and the old bars metric becomes the line (as on the Marketing page)
    assert page.locator("#trend-bars").input_value() == "spend" and page.locator("#trend-line").input_value() == "impressions"
    assert page.locator("#trend-chart").is_visible()
    assert "tg=" not in page.url and "tb=" not in page.url and "nb=" not in page.url
    assert errors == []
    ctx.close()


def test_hide_persists_across_a_reload_and_does_not_touch_the_marketing_key(browser, base_url):
    ctx, page, errors, reqs = open_page(browser, base_url)
    page.click("#trend-toggle")
    assert page.locator("#trend-toggle").inner_text() == "Show"
    assert not page.locator("#trend-chart").is_visible()
    assert page.evaluate("localStorage.getItem('tagexp.trend.collapsed')") is None
    page.reload()
    page.wait_for_selector("body[data-pg-state='ready']")
    page.wait_for_selector("#trend-toggle")
    assert page.locator("#trend-toggle").inner_text() == "Show"
    assert not page.locator("#trend-chart").is_visible()
    page.click("#trend-toggle")
    page.wait_for_selector("#trend-chart", state="visible")
    assert page.locator("#trend-toggle").inner_text() == "Hide"
    assert errors == []
    ctx.close()


def test_api_failure_shows_the_error_line_not_a_blank_panel(browser, base_url):
    ctx, page, errors, reqs = open_page(browser, base_url, mode="fail")
    page.wait_for_function("document.querySelector('#trend-note').textContent.includes('Trend failed to load')")
    assert "boom" in page.locator("#trend-note").inner_text()
    assert page.locator(".pacing-card").count() == 6                  # the rest of the page is unaffected
    ctx.close()


def test_phone_width_has_no_sideways_scroll_and_the_chart_fits(browser, base_url):
    ctx, page, errors, reqs = open_page(browser, base_url, size=(375, 812))
    page.wait_for_function("document.querySelector('#trend-status').textContent === ''")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    box = page.locator("#trend-panel").bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= 375 + 1
    assert page.locator("#trend-chart").bounding_box()["width"] <= 375
    page.screenshot(path=str(SHOTS / "pacing_trend_375.png"))
    assert errors == []
    ctx.close()
