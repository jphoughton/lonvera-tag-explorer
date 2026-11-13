"""Launch controls on the Production page against the mock (tests/ui/mock_priorities_launch.py). Dark by default: with the flag off the page is exactly today's page. With it on, a Prepare launch
button sits on rows that are ready and have a creative link; it shows the exact plan, saves edited text through the draft route, and confirms with job id + plan hash. Nothing here calls Meta."""
import pytest

pytestmark = pytest.mark.mock_only

LAUNCH_STRINGS = ["Prepare launch", "Create paused ads in Meta", "Test mode", "Created in Meta", "Copy tracker row", "Retry failed", "Every ad is created paused"]


@pytest.fixture(autouse=True)
def fresh(ctx, base_url):
    ctx.request.post(base_url + "/__test/assign/reset")
    ctx.request.post(base_url + "/__test/launch/reset")


@pytest.fixture
def cfg(ctx, base_url):
    def set_(**kw):
        ctx.request.post(base_url + "/__test/launch/config", data=kw)
    return set_


@pytest.fixture
def calls(ctx, base_url):
    return lambda: ctx.request.get(base_url + "/__test/launch/calls").json()


def idle(page):
    page.wait_for_function("() => !document.querySelector('#view').hasAttribute('aria-busy')")


def goto(page, ctx, base_url, ready=True):
    if ready:
        ctx.request.patch(base_url + "/api/priorities/assignments/2", data={"ready": True})      # row 2 has a creative link: becomes Ready to launch
    page.add_init_script("window.LAUNCH_POLL_MS = 120")
    page.goto("/priorities/production?view=list")
    page.wait_for_selector("[data-testid=prod-page]")
    idle(page)


def row(page, rid):
    return page.locator(f"[data-testid=prod-row][data-id='{rid}']")


def open_plan(page):
    row(page, 2).locator("[data-testid=prod-launch-open]").click()
    page.wait_for_selector("[data-testid=prod-launch-plan]")


def state(page):
    return page.locator("[data-testid=prod-launch-state]").text_content().strip()


def test_flag_off_page_is_today_s_page(page, ctx, base_url):
    goto(page, ctx, base_url)
    text = page.locator("[data-testid=prod-page]").inner_text()
    for s in LAUNCH_STRINGS:
        assert s not in text
    assert page.locator("[data-testid^=prod-launch-]").count() == 0
    acts = row(page, 2).locator("td").last.locator("button").evaluate_all("bs => bs.map(b => b.dataset.testid)")
    assert acts == ["prod-open-card", "prod-launched", "prod-change", "prod-drop"]


def test_enabled_but_not_allowed_shows_nothing(page, ctx, base_url, cfg):
    cfg(enabled=True, can_launch=False)
    goto(page, ctx, base_url)
    assert page.locator("[data-testid^=prod-launch-]").count() == 0
    assert "Prepare launch" not in page.locator("[data-testid=prod-page]").inner_text()


def test_prepare_button_only_on_ready_rows_with_a_creative_link(page, ctx, base_url, cfg):
    cfg(enabled=True)
    goto(page, ctx, base_url)
    assert row(page, 2).locator("[data-testid=prod-launch-open]").count() == 1
    assert row(page, 1).locator("[data-testid=prod-launch-open]").count() == 0          # no creative link
    assert row(page, 3).locator("[data-testid=prod-launch-open]").count() == 0
    assert page.locator("[data-testid=prod-launch-open]").count() == 1


def test_prepare_shows_states_then_the_exact_plan(page, ctx, base_url, cfg, calls):
    cfg(enabled=True)
    goto(page, ctx, base_url)
    row(page, 2).locator("[data-testid=prod-launch-open]").click()
    page.wait_for_selector("[data-testid=prod-launch-plan]")
    assert ["POST", "prepare", 2] in calls()
    d = page.locator("[data-testid=prod-launch-dialog]")
    assert state(page) == "Plan ready"
    t = d.inner_text()
    assert "BeefLiver_BL1_Scaling_11-10-26" in t and "will be created" in t
    assert page.locator("[data-testid=prod-launch-adrow]").evaluate_all("rs => rs.map(r => r.innerText.replace(/\\s+/g, ' ').trim())") == ["BeefLiver-9.1-a 9.1-a.png", "BeefLiver-9.2-b 9.2-b.png"]
    assert "notes.txt" in page.locator("[data-testid=prod-launch-skipped]").inner_text() and "Not an image or video" in t
    assert page.locator("[data-testid=prod-launch-budget]").text_content() == "$150.00" and page.locator("[data-testid=prod-launch-cap]").text_content() == "$95.00"
    assert page.locator("[data-testid=prod-launch-primary-3]").input_value() == "Primary three" and page.locator("[data-testid=prod-launch-headline-1]").input_value() == "Headline one"
    assert "Every ad is created paused. Turning ads on happens in Ads Manager." in t
    assert "—" not in t
    assert page.locator("[data-testid=prod-launch-testmode]").count() == 0
    assert page.locator("[data-testid=prod-launch-confirm]").text_content().strip() == "Create paused ads in Meta"
    assert page.locator("[data-testid=prod-launch-cancel]").text_content().strip() == "Cancel"
    assert page.locator("[data-testid=prod-launch-link]").get_attribute("href") == "https://lonvera.com/pages/beef-liver"


def test_dry_run_banner(page, ctx, base_url, cfg):
    cfg(enabled=True, dry_run=True)
    goto(page, ctx, base_url)
    open_plan(page)
    assert page.locator("[data-testid=prod-launch-testmode]").text_content().strip() == "Test mode: nothing will be created in Meta."


def test_editing_text_saves_a_draft_and_requires_prepare_again(page, ctx, base_url, cfg, calls):
    cfg(enabled=True)
    goto(page, ctx, base_url)
    open_plan(page)
    page.fill("[data-testid=prod-launch-primary-1]", "A brand new primary text")
    assert page.locator("[data-testid=prod-launch-confirm]").is_disabled()            # unsaved edits: cannot create
    page.click("[data-testid=prod-launch-save-text]")
    page.wait_for_selector("[data-testid=prod-launch-notice]")
    put = [c for c in calls() if c[0] == "PUT"][0]
    assert put[3]["primary_texts"][0] == "A brand new primary text" and put[3]["headlines"] == ["Headline one", "Headline two", "Headline three"]
    assert page.locator("[data-testid=prod-launch-confirm]").is_disabled()            # saved, but the plan is old: Prepare again first
    page.click("[data-testid=prod-launch-prepare]")
    page.wait_for_function("() => { const i = document.querySelector('[data-testid=prod-launch-primary-1]'); const c = document.querySelector('[data-testid=prod-launch-confirm]'); return i && i.value === 'A brand new primary text' && c && !c.disabled; }")


def test_confirm_sends_job_id_and_plan_hash_then_shows_created_and_failed_ads(page, ctx, base_url, cfg, calls):
    cfg(enabled=True)
    goto(page, ctx, base_url)
    open_plan(page)
    page.click("[data-testid=prod-launch-confirm]")
    page.wait_for_selector("[data-testid=prod-launch-adstate]")
    conf = [c for c in calls() if c[1] == "confirm"][0]
    assert isinstance(conf[3]["job_id"], int) and len(conf[3]["plan_hash"]) == 16
    assert state(page) == "Created in Meta, paused"
    ads = page.locator("[data-testid=prod-launch-ad]")
    assert ads.count() == 2
    assert ads.nth(0).get_attribute("data-state") == "created" and ads.nth(1).get_attribute("data-state") == "failed"
    a = ads.nth(0).locator("[data-testid=prod-launch-amlink]")
    assert a.get_attribute("href").startswith("https://adsmanager.facebook.com/") and a.get_attribute("target") == "_blank" and "noopener" in a.get_attribute("rel")
    assert "Meta refused the image: too small." in ads.nth(1).inner_text()
    assert page.locator("[data-testid=prod-launch-retry]").count() == 1
    assert "never writes the Google Sheet" in page.locator("[data-testid=prod-launch-dialog]").inner_text()
    # Retry failed re-runs prepare
    n = len([c for c in calls() if c[1] == "prepare"])
    page.click("[data-testid=prod-launch-retry]")
    page.wait_for_selector("[data-testid=prod-launch-plan]")
    assert len([c for c in calls() if c[1] == "prepare"]) == n + 1


def test_no_retry_button_when_nothing_failed(page, ctx, base_url, cfg):
    cfg(enabled=True, fail_ad=False)
    goto(page, ctx, base_url)
    open_plan(page)
    page.click("[data-testid=prod-launch-confirm]")
    page.wait_for_selector("[data-testid=prod-launch-adstate]")
    assert page.locator("[data-testid=prod-launch-retry]").count() == 0


def test_copy_tracker_row_puts_the_tsv_on_the_clipboard(page, ctx, base_url, cfg):
    cfg(enabled=True)
    goto(page, ctx, base_url)
    page.evaluate("() => { window.__copied = null; Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: async (t) => { window.__copied = t; } } }); }")
    open_plan(page)
    page.click("[data-testid=prod-launch-confirm]")
    page.wait_for_selector("[data-testid=prod-launch-copy]")
    page.click("[data-testid=prod-launch-copy]")
    page.wait_for_selector("[data-testid=prod-launch-copied]")
    assert page.evaluate("() => window.__copied") == "Lonvera-BeefLiver-Static-9.1\tBeefLiver-9.1-a\t7 - Approved - Ready To Launch"


@pytest.mark.allow_errors
def test_stale_plan_409_shows_the_api_message_and_offers_prepare_again(page, ctx, base_url, cfg):
    cfg(enabled=True, stale=True)
    goto(page, ctx, base_url)
    open_plan(page)
    page.click("[data-testid=prod-launch-confirm]")
    page.wait_for_selector("[data-testid=prod-launch-error]")
    assert page.locator("[data-testid=prod-launch-error]").text_content() == "The plan changed. Prepare again."
    assert page.locator("[data-testid=prod-launch-prepare]").count() == 1
    cfg(stale=False)
    page.click("[data-testid=prod-launch-prepare]")
    page.wait_for_selector("[data-testid=prod-launch-plan]")
    assert page.locator("[data-testid=prod-launch-error]").count() == 0


def test_vendor_page_never_loads_the_launch_script(anon_page, ctx, base_url, cfg):
    cfg(enabled=True)
    loaded = []
    anon_page.on("request", lambda r: loaded.append(r.url) if "priorities_launch" in r.url else None)
    anon_page.goto("/priorities/vendor/tok-madre-0000000000000000")
    anon_page.wait_for_selector("[data-testid=prod-vendor-page]")
    assert loaded == []
    assert "Prepare launch" not in anon_page.locator("[data-testid=prod-vendor-page]").inner_text()


def test_no_sideways_page_scroll_at_375px_with_the_dialog_open(browser, base_url, cfg):
    cfg(enabled=True)
    c = browser.new_context(viewport={"width": 375, "height": 812}, base_url=base_url)
    try:
        c.request.post(base_url + "/api/login", data={"username": "JoshHoughton", "password": "test"})
        c.request.patch(base_url + "/api/priorities/assignments/2", data={"ready": True})
        pg = c.new_page()
        pg.add_init_script("window.LAUNCH_POLL_MS = 120")
        pg.goto("/priorities/production?view=list")
        pg.wait_for_selector("[data-testid=prod-page]")
        idle(pg)
        assert pg.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth + 1")
        pg.locator("[data-testid=prod-row][data-id='2'] [data-testid=prod-launch-open]").click()
        pg.wait_for_selector("[data-testid=prod-launch-plan]")
        assert pg.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth + 1")
        assert pg.evaluate("() => document.body.scrollWidth <= window.innerWidth + 1")
        box = pg.locator("[data-testid=prod-launch-dialog]").bounding_box()
        assert box["x"] >= 0 and box["x"] + box["width"] <= 376
        small = pg.evaluate("""() => [...document.querySelectorAll('[data-testid=prod-launch-dialog] *')].filter(e => [...e.childNodes].some(n => n.nodeType === 3 && n.textContent.trim()) && parseFloat(getComputedStyle(e).fontSize) < 12).map(e => e.tagName + ':' + e.textContent.slice(0, 20))""")
        assert small == []
    finally:
        c.close()


def test_flag_off_never_loads_the_launch_script_or_asks_for_launch_routes(page, ctx, base_url):
    seen = []
    page.on("request", lambda r: seen.append(r.url))
    goto(page, ctx, base_url)
    assert not [u for u in seen if "priorities_launch.js" in u or "/launch" in u]
    assert page.locator("[data-testid=prod-launch-open]").count() == 0
