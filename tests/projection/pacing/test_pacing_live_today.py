"""Live today: GET /live-today (latest hourly Upstack snapshot with age and stale flag) and the two intraday cards on Projections > Pacing that fill
themselves from it. The pacing tests pin today to 2026-11-03; the server clock is real, so snapshots are dated relative to the real now."""
import datetime as dt

import psycopg
import pytest

from test_pacing_nc_today import nc                                         # noqa: F401  (fixture: a deterministic hourly curve for the 30 closed days)
from test_pacing_page import base_url, browser, open_page                   # noqa: F401  (fixtures + helper of the pacing page tests)

BASE = "/api/projection/pacing"
PINNED = dt.date(2026, 11, 3)


def utcnow():
    return dt.datetime.now(dt.timezone.utc)


def put(pacing_db, minutes_ago=10, day=PINNED, ptime="12:30", meta=7528.0, google=310.5, nc_rev=450.0, nc_orders=7):
    with psycopg.connect(pacing_db) as c:
        c.execute("INSERT INTO proj_live_today_snapshots (pulled_at, day, pacific_time, meta_spend, google_spend, total_spend, nc_rev, nc_orders) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                  (utcnow().replace(second=0, microsecond=0) - dt.timedelta(minutes=minutes_ago), day, ptime, meta, google, meta + google, nc_rev, nc_orders))
        c.commit()


@pytest.fixture()
def snaps(pacing_db):
    def clear():
        with psycopg.connect(pacing_db) as c:
            c.execute("DELETE FROM proj_live_today_snapshots")
            c.commit()
    clear()
    yield pacing_db
    clear()


# ---------------------------------------------------------------------------------------------------------------- endpoint

def test_empty_table_says_so(api, snaps):
    j = api.get(f"{BASE}/live-today").get_json()
    assert j["loaded"] is False and j["snapshot"] is None and j["stale"] is False and j["age_minutes"] is None and "No live pull yet" in j["message"]


def test_fresh_snapshot_of_today(api, snaps):
    put(snaps, minutes_ago=12)
    j = api.get(f"{BASE}/live-today").get_json()
    assert j["loaded"] and j["stale"] is False and j["stale_reason"] is None and j["message"] is None and j["as_of"] == "2026-11-03"
    s = j["snapshot"]
    assert (s["day"], s["pacific_time"], s["meta_spend"], s["google_spend"], s["total_spend"], s["nc_rev"], s["nc_orders"]) == ("2026-11-03", "12:30", 7528.0, 310.5, 7838.5, 450.0, 7)
    assert 12 <= j["age_minutes"] <= 13 and s["pulled_at"].endswith("Z")


def test_the_newest_pull_wins(api, snaps):
    put(snaps, minutes_ago=130, meta=1000.0, ptime="10:00")
    put(snaps, minutes_ago=70, meta=2000.0, ptime="11:00")
    put(snaps, minutes_ago=5, meta=3000.0, ptime="12:05")
    j = api.get(f"{BASE}/live-today").get_json()
    assert j["snapshot"]["meta_spend"] == 3000.0 and j["snapshot"]["pacific_time"] == "12:05"


def test_older_than_150_minutes_is_stale_but_still_returned(api, snaps):
    put(snaps, minutes_ago=151)
    j = api.get(f"{BASE}/live-today").get_json()
    assert j["loaded"] and j["stale"] is True and j["stale_reason"] == "age" and "minutes old" in j["message"] and j["age_minutes"] >= 151
    put(snaps, minutes_ago=149)
    assert api.get(f"{BASE}/live-today").get_json()["stale"] is False                  # the newer row wins and is inside the limit


def test_a_snapshot_from_a_previous_pacific_day_is_stale_whatever_its_age(api, snaps):
    put(snaps, minutes_ago=3, day=PINNED - dt.timedelta(days=1), ptime="23:55")
    j = api.get(f"{BASE}/live-today").get_json()
    assert j["loaded"] and j["stale"] is True and j["stale_reason"] == "previous_day" and "Nov 2" in j["message"] and "not today" in j["message"]


def test_the_live_endpoint_needs_login_and_is_read_only(api_app, api, snaps):
    assert api_app.test_client().get(f"{BASE}/live-today").status_code == 401
    assert api.post(f"{BASE}/live-today", json={}).status_code in (404, 405)


def test_endpoint_survives_the_table_not_being_migrated_yet(api, snaps):
    from app import db as appdb                                              # noqa: F401
    with psycopg.connect(snaps) as c:
        c.execute("ALTER TABLE proj_live_today_snapshots RENAME TO proj_live_today_snapshots_x")
        c.commit()
    try:
        j = api.get(f"{BASE}/live-today").get_json()
        assert j["loaded"] is False and "No live pull yet" in j["message"]
    finally:
        with psycopg.connect(snaps) as c:
            c.execute("ALTER TABLE proj_live_today_snapshots_x RENAME TO proj_live_today_snapshots")
            c.commit()


# ---------------------------------------------------------------------------------------------------------------- the cards

META = "[data-box='intraday']"
NC = "[data-box='intraday-nc']"


def note(page, box):
    return page.locator(f"{box} .pacing-note[role='status']:not(.pacing-live)").first.inner_text()


def live_line(page, box):
    return page.locator(f"{box} .pacing-live").inner_text()


def test_both_cards_fill_themselves_from_the_latest_pull(browser, base_url, snaps, nc):          # noqa: F811
    put(snaps, minutes_ago=12)
    ctx, page, errors = open_page(browser, base_url)
    page.wait_for_function(f"document.querySelector(\"{META} input[type='number']\")?.value === '7528'")
    assert page.input_value(f"{META} input[type='time']") == "12:30"
    assert page.input_value(f"{NC} input[type='number']") == "450" and page.input_value(f"{NC} input[type='time']") == "12:30"
    page.wait_for_function(f"document.querySelector(\"{NC} .pacing-note[role='status']:not(.pacing-live)\")?.innerText.includes('Projected end-of-day')")
    assert "Projected end-of-day NC revenue $1,000" in note(page, NC)                   # 450 spent at 12:30, the fixture curve is 45% in by then
    page.wait_for_function(f"document.querySelector(\"{META} .pacing-note[role='status']:not(.pacing-live)\")?.innerText.includes('Projected end-of-day')")
    for box in (META, NC):
        assert "Live from Upstack, pulled 12:30 PT (1" in live_line(page, box) and "min ago)" in live_line(page, box)
        assert "Stale" not in live_line(page, box) and page.locator(f"{box} .pacing-live button").is_hidden()
    assert errors == []
    ctx.close()


def test_a_typed_value_overrides_and_use_live_or_clearing_restores(browser, base_url, snaps, nc):  # noqa: F811
    put(snaps, minutes_ago=5)
    ctx, page, errors = open_page(browser, base_url)
    num = f"{META} input[type='number']"
    page.wait_for_function(f"document.querySelector(\"{num}\")?.value === '7528'")
    page.fill(num, "9000")
    assert "Your typed value is in use" in live_line(page, META)
    btn = page.locator(f"{META} .pacing-live button")
    assert btn.is_visible()
    assert page.input_value(f"{NC} input[type='number']") == "450"                       # the other card is untouched
    btn.click()
    assert page.input_value(num) == "7528" and page.input_value(f"{META} input[type='time']") == "12:30" and btn.is_hidden()
    page.fill(num, "8100")
    page.fill(num, "")                                                                  # clearing the field returns to live
    assert page.input_value(num) == "7528" and "typed value" not in live_line(page, META)
    page.fill(f"{META} input[type='time']", "18:00")                                    # changing the time is an override too
    assert "Your typed value is in use" in live_line(page, META)
    assert errors == []
    ctx.close()


def test_the_card_follows_the_next_hourly_pull_but_never_overwrites_a_typed_value(browser, base_url, snaps, nc):  # noqa: F811
    put(snaps, minutes_ago=5)
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    page.clock.install()
    page.goto(base_url + "/projection/pacing")
    page.wait_for_selector("body[data-pg-state='ready']")
    meta, ncv = f"{META} input[type='number']", f"{NC} input[type='number']"
    page.wait_for_function(f"document.querySelector(\"{meta}\")?.value === '7528'")
    page.fill(ncv, "777")                                                                # typed on the NC card only
    put(snaps, minutes_ago=0, meta=8000.0, nc_rev=520.0, ptime="12:45")                  # the next hourly pull lands
    page.clock.run_for(5 * 60 * 1000 + 1000)                                             # the page re-reads the live pull every 5 minutes
    page.wait_for_function(f"document.querySelector(\"{meta}\")?.value === '8000'")
    assert page.input_value(f"{META} input[type='time']") == "12:45" and page.input_value(ncv) == "777"
    ctx.close()


def test_an_old_pull_still_fills_but_warns(browser, base_url, snaps, nc):                    # noqa: F811
    put(snaps, minutes_ago=200, ptime="09:10")
    ctx, page, errors = open_page(browser, base_url)
    page.wait_for_function(f"document.querySelector(\"{META} input[type='number']\")?.value === '7528'")
    for box in (META, NC):
        t = live_line(page, box)
        assert "Stale data" in t and "pulled 09:10 PT (3 h 2" in t and "over two and a half hours" in t
    assert errors == []
    ctx.close()


def test_a_pull_from_yesterday_is_not_used(browser, base_url, snaps, nc):                     # noqa: F811
    put(snaps, minutes_ago=4, day=PINNED - dt.timedelta(days=1), ptime="23:55")
    ctx, page, errors = open_page(browser, base_url)
    page.wait_for_function(f"document.querySelector(\"{META} .pacing-live\")?.innerText.includes('not today')")
    assert page.input_value(f"{META} input[type='number']") == "" and page.input_value(f"{NC} input[type='number']") == ""
    assert "Stale data" in live_line(page, NC) and page.locator(f"{NC} .pacing-live button").is_hidden()
    ctx.close()


def test_no_pull_yet_typed_inputs_work_as_before(browser, base_url, snaps, nc):               # noqa: F811
    ctx, page, errors = open_page(browser, base_url)
    page.wait_for_function(f"document.querySelector(\"{META} .pacing-live\")?.innerText.includes('No live pull yet')")
    page.fill(f"{NC} input[type='number']", "300")
    page.fill(f"{NC} input[type='time']", "12:00")
    page.wait_for_function(f"document.querySelector(\"{NC} .pacing-note[role='status']:not(.pacing-live)\")?.innerText.includes('Projected end-of-day NC revenue $1,000')")
    assert errors == []
    ctx.close()


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_phone_width_375_has_no_sideways_scroll_with_the_live_lines(browser, base_url, snaps, nc, scheme):    # noqa: F811
    put(snaps, minutes_ago=200)
    ctx, page, errors = open_page(browser, base_url, (375, 812), scheme)
    page.wait_for_function(f"document.querySelector(\"{META} input[type='number']\")?.value === '7528'")
    page.fill(f"{META} input[type='number']", "9000")                                    # the longest state: stale warning + typed note + Use live button
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    for box in (META, NC):
        assert page.evaluate("(sel) => { const e = document.querySelector(sel + ' .pacing-live'); return e.scrollWidth <= e.clientWidth + 1; }", box)
        assert page.evaluate("(sel) => parseFloat(getComputedStyle(document.querySelector(sel + ' .pacing-live')).fontSize) >= 12", box)
    assert errors == []
    ctx.close()
