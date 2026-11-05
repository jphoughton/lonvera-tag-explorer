"""GET /intraday-nc: the 'NC revenue today' card's endpoint. Fixture data in proj_feed_nc_hourly (a deterministic daily shape), the pinned date of the pacing
tests (2026-11-03, so the window is Oct 4 .. Nov 2 and Nov 3 itself is never read)."""
import datetime as dt

import psycopg
import pytest

BASE = "/api/projection/pacing"
TODAY = dt.date(2026, 11, 3)                          # Tuesday
SHAPE = {7: 10.0, 9: 20.0, 12: 30.0, 18: 30.0, 21: 10.0}      # percent of a 1,000 dollar day


@pytest.fixture()
def nc(pacing_db):
    with psycopg.connect(pacing_db) as c:
        c.execute("DELETE FROM proj_feed_nc_hourly")
        for i in range(1, 36):
            d = TODAY - dt.timedelta(days=i)
            for h, pct in SHAPE.items():
                c.execute("INSERT INTO proj_feed_nc_hourly (day, hour, nc_rev, nc_orders) VALUES (%s, %s, %s, %s)", (d, h, pct * 10, 1))
        c.execute("INSERT INTO proj_feed_nc_hourly (day, hour, nc_rev, nc_orders) VALUES (%s, 3, 999999, 99)", (TODAY,))      # today's partial: never in the curve
        c.commit()
    yield
    with psycopg.connect(pacing_db) as c:
        c.execute("DELETE FROM proj_feed_nc_hourly")
        c.commit()


def test_projection_from_the_curve_and_the_note_fields(api, nc):
    j = api.get(f"{BASE}/intraday-nc?spent=300&time=12:00").get_json()
    assert j["loaded"] and j["share_of_day"] == pytest.approx(0.30)           # hours 7 and 9 are in: 10 + 20 percent; hour 12 has not started
    assert j["projected_eod"] == pytest.approx(1000.0) and j["to_go"] == pytest.approx(700.0) and j["message"] is None
    mid = api.get(f"{BASE}/intraday-nc?spent=450&time=12:30").get_json()
    assert mid["share_of_day"] == pytest.approx(0.45) and mid["projected_eod"] == pytest.approx(1000.0)      # linear inside the hour
    w = j["window"]
    assert (w["from"], w["to"], w["days_used"], w["days_with_data"]) == ("2026-10-04", "2026-11-02", 30, 30) and w["total_nc_rev"] == pytest.approx(30000.0)
    assert j["basis"] == "30 closed days, 2026-10-04 to 2026-11-02" and j["as_of"] == "2026-11-03"
    assert len(j["curve"]) == 24 and j["curve"][23]["cum_end"] == pytest.approx(1.0) and j["curve"][9]["cum_end"] == pytest.approx(0.30)
    assert j["stale"] == {"newest_closed_day": "2026-11-02", "age_days": 0, "stale_after_days": 2, "is_stale": False}
    assert j["weekday"] == "Tuesday" and j["dow_check"]["differs"] is False


def test_no_spend_returns_the_share_and_the_curve_only(api, nc):
    j = api.get(f"{BASE}/intraday-nc?time=18:00").get_json()
    assert j["projected_eod"] is None and j["share_of_day"] == pytest.approx(0.60) and j["loaded"] and j["message"] is None


def test_same_weekday_switch_changes_the_basis_not_the_endpoint(api, nc):
    j = api.get(f"{BASE}/intraday-nc?spent=1&time=12:00&dow=same").get_json()
    assert j["dow"] == "same" and 4 <= j["window"]["days_used"] <= 5 and j["share_of_day"] == pytest.approx(0.30)


def test_validation(api, nc):
    for q in ("spent=abc", "spent=-5", "spent=1e12", "spent=nan&time=12:00", "time=noon", "time=25:30", "spent=10&dow=weekend"):
        assert api.get(f"{BASE}/intraday-nc?{q}").status_code == 400, q
    assert api.get(f"{BASE}/intraday-nc?spent=0&time=12:00").get_json()["projected_eod"] == pytest.approx(0.0)


def test_empty_table_says_so_and_never_assumes_an_even_day(api, pacing_db):
    with psycopg.connect(pacing_db) as c:
        c.execute("DELETE FROM proj_feed_nc_hourly")
        c.commit()
    j = api.get(f"{BASE}/intraday-nc?spent=500&time=12:00").get_json()
    assert j["loaded"] is False and j["projected_eod"] is None and j["curve"] == [] and "No closed day" in j["message"] and j["stale"]["is_stale"] is True


def test_stale_curve_is_flagged(api, nc):
    j = api.get(f"{BASE}/intraday-nc?spent=1&time=12:00&as_of=2026-11-07").get_json()          # the fixture's newest row is Nov 3 (a closed day from Nov 7's point of view) against yesterday Nov 6
    assert j["stale"]["is_stale"] is True and j["stale"]["age_days"] == 3 and j["stale"]["newest_closed_day"] == "2026-11-03"


def test_the_endpoint_needs_a_login_and_is_read_only(api_app, api, nc):
    assert api_app.test_client().get(f"{BASE}/intraday-nc").status_code == 401
    assert api.post(f"{BASE}/intraday-nc", json={}).status_code in (404, 405)


def test_the_freshness_chip_appears_once_the_feed_has_loaded(api, nc, pacing_db):
    chips = {x["feed"]: x for x in api.get(f"{BASE}/freshness").get_json()["feeds"]}
    assert chips["nc_hourly"]["data_through"] == "2026-11-03" and chips["nc_hourly"]["status"] == "good"      # newest row is today's partial: the chip reads the table, the curve ignores it
    with psycopg.connect(pacing_db) as c:
        c.execute("DELETE FROM proj_feed_nc_hourly")
        c.commit()
    assert "nc_hourly" not in {x["feed"] for x in api.get(f"{BASE}/freshness").get_json()["feeds"]}
