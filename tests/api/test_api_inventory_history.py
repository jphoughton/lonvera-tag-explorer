"""GET /api/inventory/history: one past day per product x location, the daily trend per location, estimate marks and gap flags.

Rows are written straight into proj_demand_snapshot (and removed again), so the numbers asserted here are the ones written below."""
from __future__ import annotations

import datetime as dt

import pytest

from app import inventory_history as IH

D = dt.date
URL = "/api/inventory/history"


@pytest.fixture()
def snaps(pg, fx):
    """Revamp Nov 3..8 (real), Amazon Nov 3..5 and Nov 7..8 (Nov 6 missing), international from Nov 5 only (count day real, later days estimates)."""
    pg.execute("DELETE FROM proj_demand_snapshot")
    rows = []
    for i in range(6):
        d = D(2026, 11, 3 + i)
        rows += [("revamp", "bull", d, 100 + i, 10 + i, "warehance"), ("revamp", "liver", d, 200 + i, 0, "warehance")]
    for d in (D(2026, 11, 3), D(2026, 11, 4), D(2026, 11, 5), D(2026, 11, 7), D(2026, 11, 8)):
        rows.append(("amazon", "bull", d, 50 - (d.day - 3), 0, "estimate_from_count"))
    rows += [("intl", "bull", D(2026, 11, 5), 1000, 4, "typed_count"), ("intl", "bull", D(2026, 11, 6), 990, 0, "estimate_from_count"),
             ("intl", "bull", D(2026, 11, 7), 980, 0, "estimate_from_count"), ("intl", "bull", D(2026, 11, 8), 970, 0, "estimate_from_count")]
    pg.cursor().executemany("INSERT INTO proj_demand_snapshot (location, product, as_of, on_hand, allocated, source) VALUES (%s,%s,%s,%s,%s,%s)", rows)
    pg.commit()
    yield
    pg.execute("DELETE FROM proj_demand_snapshot")
    pg.commit()


def get(client, qs=""):
    r = client.get(URL + qs)
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()


def test_requires_sign_in(anon, snaps):
    assert anon.get(URL).status_code == 401


def test_default_is_the_newest_day_with_30_days_of_trend(client, snaps):
    d = get(client)
    assert d["date"] == "2026-11-08" and d["newest"] == "2026-11-08" and d["days"] == 30 and d["from"] == "2026-10-10"
    rows = {(r["location"], r["product"]): r for r in d["snapshot"]}
    assert rows[("revamp", "bull")]["on_hand"] == 105 and rows[("revamp", "bull")]["allocated"] == 15 and rows[("revamp", "bull")]["estimate"] is False
    assert rows[("amazon", "bull")]["on_hand"] == 45 and rows[("amazon", "bull")]["estimate"] is True
    assert rows[("intl", "bull")]["estimate"] is True
    assert [r["location"] for r in d["snapshot"]] == ["revamp", "revamp", "amazon", "intl"]          # location order, then product order
    assert [p["key"] for p in d["products"]][:3] == ["bull", "liver", "boneflex"]


def test_a_past_day_and_the_count_day_is_not_an_estimate(client, snaps):
    d = get(client, "?date=2026-11-05")
    rows = {(r["location"], r["product"]): r for r in d["snapshot"]}
    assert rows[("intl", "bull")]["on_hand"] == 1000 and rows[("intl", "bull")]["estimate"] is False and rows[("intl", "bull")]["source"] == "typed_count"
    assert d["missing_on_date"] == []


def test_gap_inside_a_series_is_flagged_and_days_before_the_series_are_not(client, snaps):
    d = get(client, "?date=2026-11-08&days=6")
    tr = {t["location"]: t for t in d["trend"]}
    assert tr["amazon"]["gaps"] == ["2026-11-06"]
    assert tr["intl"]["gaps"] == [] and tr["intl"]["first_day"] == "2026-11-05"             # Nov 3 and 4 precede the international series
    assert tr["revamp"]["gaps"] == []
    s = {x["day"]: x for x in tr["intl"]["series"]}
    assert s["2026-11-03"]["on_hand"] is None and s["2026-11-06"]["on_hand"] == 990 and s["2026-11-06"]["estimate"] is True and s["2026-11-05"]["estimate"] is False
    am = {x["day"]: x for x in tr["amazon"]["series"]}
    assert am["2026-11-06"]["on_hand"] is None
    rev = {x["day"]: x for x in tr["revamp"]["series"]}
    assert rev["2026-11-04"]["on_hand"] == 101 + 201 and rev["2026-11-04"]["allocated"] == 11          # summed over products
    assert len(tr["revamp"]["series"]) == 6


def test_a_location_with_no_row_on_the_picked_day_is_listed(client, snaps):
    d = get(client, "?date=2026-11-06")
    assert d["missing_on_date"] == ["amazon"]
    assert {r["location"] for r in d["snapshot"]} == {"revamp", "intl"}


def test_bad_parameters_are_400(client, snaps):
    for qs in ("?date=yesterday", "?date=2026-13-01", "?days=0", "?days=181", "?days=abc"):
        assert client.get(URL + qs).status_code == 400, qs


def test_empty_table_answers_an_empty_page_not_an_error(client, pg, fx):
    pg.execute("DELETE FROM proj_demand_snapshot")
    pg.commit()
    d = get(client)
    assert d["date"] is None and d["snapshot"] == [] and d["trend"] == []


def test_estimate_rule():
    assert IH.is_estimate("estimate_from_count") and IH.is_estimate("estimate") and not IH.is_estimate("warehance") and not IH.is_estimate("typed_count") and not IH.is_estimate(None)


def test_route_is_get_only(client, snaps):
    assert client.post(URL, json={}).status_code in (404, 405)
