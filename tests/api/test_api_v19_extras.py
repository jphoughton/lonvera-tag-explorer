"""v1.9 additions on top of "active only": removed tracker rows (deleted_at), /api/status stale, the products envelope's unmatched + total_all,
since-launch unmatched = Meta lifetime, window_info.stage_note, and the pre-migration fallbacks."""
import datetime as dt
import time

import pytest

from app import queries as q

from . import fixture_data as F
from .helpers import THROUGH, WINDOWS, get_ok, qs, rows_by_product

STAGE_NOTE = ("Stage follows each campaign's current name: 'test' in the name = Testing, everything else = Scaling; "
              "renaming a campaign reclassifies its history.")
ACTIVE_ADS = {a for a, v in F.ADS.items() if v[3] == "ACTIVE"}


# ------------------------------------------------------------------------------------------------------------------ deleted tracker rows
def test_deleted_rows_exist_in_the_table(pg):
    assert pg.execute("SELECT count(*) FROM tracker_rows WHERE deleted_at IS NOT NULL").fetchone()[0] == len(F.DELETED_TRACKER)


@pytest.mark.parametrize("row_id", [r["id"] for r in F.DELETED_TRACKER])
def test_deleted_row_is_gone(client, row_id):
    assert client.get(f"/api/ads/{row_id}").status_code == 404
    assert client.get(f"/api/ads/{row_id}?active=1&window=7d").status_code == 404


def test_deleted_rows_never_appear_anywhere(client, fx):
    live_ids = {r["id"] for r in fx.tracker}
    d = get_ok(client, "/api/ads/1000")
    assert {x["id"] for x in d["siblings"]} <= live_ids and 2000 not in {x["id"] for x in d["siblings"]}
    for active in ("", "active=1"):
        fams = get_ok(client, "/api/products/vita-bull/families?" + active)
        assert 2000 not in [i for r in fams["rows"] for i in r["ad_ids"]] and 2001 not in [i for r in fams["rows"] for i in r["ad_ids"]]
        assert "vb-fam-deleted" not in {r["family_key"] for r in fams["rows"]}
        fa_row = next(r for r in fams["rows"] if r["family_key"] == "vb-fam-a")
        assert fa_row["ad_ids"] == [1000, 1001] and fa_row["primary_id"] == 1000        # the deleted row sat before it in sheet order
        tree = get_ok(client, "/api/products/vita-bull/tree?depth=3&" + active)
        assert 2000 not in [r["primary_id"] for r in tree["rows"]] and "vb-fam-deleted" not in {r["family_key"] for r in tree["rows"]}
    rows = get_ok(client, "/api/products")
    by = {p["slug"]: p for p in rows}
    assert by["vita-bull"]["rows"] == len(rows_by_product(fx, "vita-bull")) and by["beef-liver"]["rows"] == len(rows_by_product(fx, "beef-liver"))
    st = get_ok(client, "/api/status")
    assert st["products"] >= 3


# ------------------------------------------------------------------------------------------------------------------ stale
def set_meta(pg, **kv):
    for k, v in kv.items():
        pg.execute("DELETE FROM app_meta WHERE key = %s", (k,))
        if v is not None:
            pg.execute("INSERT INTO app_meta (key, value) VALUES (%s, %s)", (k, v))
    pg.commit()


@pytest.fixture()
def clean_meta(pg):
    yield
    set_meta(pg, last_ok_at=None, refresh_status=None)


def freeze(monkeypatch, y, m, d, hh, mm=0):
    monkeypatch.setattr(q, "_now", lambda: dt.datetime(y, m, d, hh, mm, tzinfo=dt.timezone.utc))


def stale(client):
    return get_ok(client, "/api/status")["stale"]


def test_stale_fresh(client, pg, monkeypatch, clean_meta):
    freeze(monkeypatch, 2026, 11, 3, 20)
    set_meta(pg, last_ok_at="2026-11-03T18:00:00+00:00", refresh_status="ok")
    s = stale(client)
    assert s == {"is_stale": False, "reason": None, "last_ok_at": "2026-11-03T18:00:00+00:00", "data_through": "2026-11-02", "age_hours": 2.0,
                 "refresh_status": "ok"}


def test_stale_data_too_old_uses_los_angeles_today(client, pg, monkeypatch, clean_meta):
    set_meta(pg, last_ok_at="2026-11-04T20:00:00+00:00", refresh_status="ok")
    freeze(monkeypatch, 2026, 11, 5, 3)           # 03:00 UTC = Nov 4, 19:00 in Los Angeles -> today - 2 = Nov 2 -> data through Nov 2 is still fine
    assert stale(client)["is_stale"] is False
    freeze(monkeypatch, 2026, 11, 5, 9)           # Nov 5, 01:00 in Los Angeles -> today - 2 = Nov 3 -> Nov 2 is too old
    set_meta(pg, last_ok_at="2026-11-05T08:00:00+00:00")
    s = stale(client)
    assert s["is_stale"] is True and s["age_hours"] == 1.0
    assert s["reason"] == "Data runs through Nov 2, 2026, more than 2 days behind today (Nov 5, 2026, Los Angeles)."


def test_stale_last_ok_older_than_36_hours(client, pg, monkeypatch, clean_meta):
    freeze(monkeypatch, 2026, 11, 3, 20)
    set_meta(pg, last_ok_at="2026-11-02T08:00:00+00:00", refresh_status="ok")     # 36.0 hours: not older than 36
    assert stale(client)["is_stale"] is False
    set_meta(pg, last_ok_at="2026-11-02T07:00:00+00:00")                          # 37 hours
    s = stale(client)
    assert s["is_stale"] is True and s["age_hours"] == 37.0 and s["reason"] == "The last fully successful refresh finished 37 hours ago (more than 36 hours)."


def test_stale_refresh_failed(client, pg, monkeypatch, clean_meta):
    freeze(monkeypatch, 2026, 11, 3, 20)
    set_meta(pg, last_ok_at="2026-11-03T18:00:00+00:00", refresh_status="failed")
    s = stale(client)
    assert s["is_stale"] is True and s["refresh_status"] == "failed" and "latest refresh failed" in s["reason"]


def test_stale_combines_reasons_and_falls_back_to_etl_runs(client, pg, monkeypatch, clean_meta):
    set_meta(pg, last_ok_at=None, refresh_status=None)                            # nothing recorded by the ETL yet: the last ok etl_runs row stands in
    s = stale(client)
    assert s["last_ok_at"] is not None and s["age_hours"] is not None and s["refresh_status"] is None
    # the seeded etl_runs rows are stamped with the real now(), so the frozen clock is set relative to that stamp (40 hours after it), never to a fixed date
    t = (dt.datetime.fromisoformat(s["last_ok_at"]) + dt.timedelta(hours=40)).astimezone(dt.timezone.utc)
    freeze(monkeypatch, t.year, t.month, t.day, t.hour, t.minute)
    s = stale(client)
    assert s["is_stale"] is True and "Data runs through Nov 2, 2026" in s["reason"] and "hours ago" in s["reason"]


# ------------------------------------------------------------------------------------------------------------------ products envelope
def meta_account_spend(fx, wo, stages, active):
    rows = fx.active_view.ad_daily if active else fx.ad_daily
    if wo == "launch":
        lt = fx.active_view.ad_lifetime if active else fx.ad_lifetime
        extra = F.Z if active else sum((v[0] for v in F.FAMILY_EXTRA.values()), F.Z)
        return sum((v["spend"] for v in lt.values()), F.Z) + extra
    return sum((r["spend"] for r in rows if wo[0] <= r["day"] <= wo[1] and F.stage_of_row(fx, r["campaign_id"]) in stages), F.Z)


WIN = [pytest.param(w, wo, id=w or "default") for w, wo in WINDOWS]


@pytest.mark.parametrize("w,wo", WIN)
@pytest.mark.parametrize("active", [False, True])
def test_envelope_total_all_is_the_meta_account(client, fx, w, wo, active):
    d = get_ok(client, "/api/products?" + qs("envelope=1", "active=1" if active else "", w))
    t, u, a = d["total"]["m"], d["unmatched"]["m"], d["total_all"]["m"]
    for k in ("spend", "revenue", "purchases"):
        assert a[k] == pytest.approx(t[k] + u[k], abs=0.06), k
    assert a["unique_ads"] == t["unique_ads"] + u["unique_ads"]
    assert a["spend"] == pytest.approx(float(meta_account_spend(fx, wo, F.STAGES, active)), abs=0.06)         # = everything Meta spent (tracker + off-tracker)
    # unmatched = every Meta family that no tracker family claims, across all products
    ufams = fx.active_view.unmatched if active else fx.unmatched
    if wo == "launch":
        want = sum((v["spend_launch"] for v in ufams.values()), F.Z)
    else:
        src = fx.active_view if active else fx
        want = sum((r["spend"] for r in src.ad_daily if F.MAPPING[r["meta_family_key"]][0] is None and wo[0] <= r["day"] <= wo[1]), F.Z)
    assert u["spend"] == pytest.approx(float(want), abs=0.06)
    assert (u["unique_ads"] >= 1) == (want != 0) and (t["spend"] > 0 or wo != "launch")
    assert a["spend_share"] == pytest.approx(1.0, abs=1e-4) and u["spend_share"] == pytest.approx(u["spend"] / a["spend"], abs=1e-4)
    # the tracker total keeps its meaning
    plain = get_ok(client, "/api/products?" + qs("envelope=1", "active=1" if active else "", w))["total"]["m"]
    assert plain["spend"] == t["spend"]


@pytest.mark.parametrize("extra", ["window=30d&stage=testing", "window=30d&stage=scaling,unclassified", "window=14d&compare=prev", "window=launch&stage=testing,scaling"])
def test_envelope_unmatched_with_stage_and_compare(client, fx, extra):
    d = get_ok(client, "/api/products?" + qs("envelope=1", extra))
    t, u, a = d["total"]["m"], d["unmatched"]["m"], d["total_all"]["m"]
    for k in ("spend", "revenue", "purchases"):
        assert a[k] == pytest.approx(t[k] + u[k], abs=0.06), (extra, k)
    if "compare" in extra:
        assert a["cmp"]["spend"] == pytest.approx(t["cmp"]["spend"] + u["cmp"]["spend"], abs=0.06)
    stage = {"testing": ("testing",), "scaling": ("scaling", "unclassified")}
    if "launch" not in extra and "compare" not in extra:
        stages = stage["testing"] if "stage=testing" in extra else stage["scaling"]
        end = THROUGH
        want = sum((r["spend"] for r in fx.ad_daily if F.MAPPING[r["meta_family_key"]][0] is None and end - dt.timedelta(days=29) <= r["day"] <= end
                    and F.stage_of_row(fx, r["campaign_id"]) in stages), F.Z)
        assert u["spend"] == pytest.approx(float(want), abs=0.06)


# ------------------------------------------------------------------------------------------------------------------ unmatched since launch = lifetime
def test_unmatched_launch_is_lifetime_and_adds_up(client, fx):
    d = get_ok(client, "/api/products/vita-bull/unmatched")
    vb = {k: v for k, v in fx.unmatched.items() if v["product_id"] == fx.product_ids["vita-bull"]}
    got = {r["meta_family_key"]: r for r in d["rows"]}
    assert set(got) == set(vb)                                                    # lifetime-only families (u5, u6) are rows too
    for k, v in vb.items():
        assert got[k]["spend"] == pytest.approx(float(v["spend_launch"]), abs=0.006), k
        assert got[k]["purchases"] == pytest.approx(float(v["purchases_launch"]), abs=0.006), k
        assert got[k]["revenue"] == pytest.approx(float(v["revenue_launch"]), abs=0.006), k
    lo = got["bull-lifetime-only"]                                               # lifetime impressions and clicks come with the lifetime row (50 per click)
    assert lo["link_clicks"] == 90 and lo["impressions"] == 4500 and lo["ctr"] == pytest.approx(0.02, abs=1e-5) and lo["cpc"] == pytest.approx(123.45 / 90, abs=1e-3)
    for k, v in vb.items():
        assert got[k]["impressions"] == (int(v["impressions_launch"]) or None) and got[k]["link_clicks"] == (int(v["clicks_launch"]) or None), k
    assert d["total"]["spend"] == pytest.approx(sum(r["spend"] for r in d["rows"]), abs=0.011) and d["total"]["families"] == len(vb)
    assert d["total"]["spend"] == pytest.approx(float(sum(v["spend_launch"] for v in vb.values())), abs=0.011)
    # the panel on the rollup and the ads table tell the same story
    roll = get_ok(client, "/api/products/vita-bull/rollup")["unmatched"]
    assert roll["window_spend"] == pytest.approx(d["total"]["spend"], abs=0.011) and roll["window_families"] == len(vb)
    assert roll["families_all"] == len(vb)
    ads = get_ok(client, "/api/products/vita-bull/ads")
    off = [r for r in ads["rows"] if not r["on_tracker"]]
    assert sum(r["spend"] for r in off) == pytest.approx(d["total"]["spend"], abs=0.02)
    assert {r["ad_id"] for r in off if r["ad_id"]} >= {"u1", "u5", "u6"}
    assert d["window_info"]["note"].startswith("Since launch is Meta's lifetime")
    assert "impressions and clicks are not part of it" not in d["window_info"]["note"]


@pytest.mark.parametrize("extra", ["window=7d", "window=30d&stage=testing", "window=launch&stage=testing", "window=launch&stage=unclassified"])
@pytest.mark.parametrize("active", [False, True])
def test_unmatched_panel_equals_rollup_block(client, extra, active):
    a = "active=1" if active else ""
    d = get_ok(client, "/api/products/vita-bull/unmatched?" + qs(a, extra))
    roll = get_ok(client, "/api/products/vita-bull/rollup?" + qs(a, extra))["unmatched"]
    assert roll["window_spend"] == pytest.approx(d["total"]["spend"], abs=0.011), extra
    assert roll["window_families"] == len([r for r in d["rows"] if r["spend"] > 0])
    assert d["total"]["spend"] == pytest.approx(sum(r["spend"] for r in d["rows"]), abs=0.011)


# ------------------------------------------------------------------------------------------------------------------ stage_note
ENDPOINTS = [
    "/api/products?envelope=1", "/api/products/vita-bull/rollup", "/api/products/vita-bull/families", "/api/products/vita-bull/tree?depth=1",
    "/api/products/vita-bull/ads?window=7d", "/api/products/vita-bull/campaigns?window=7d", "/api/products/vita-bull/trend?window=7d",
    "/api/products/vita-bull/unmatched?window=7d", "/api/ads/1000?window=7d&compare=prev",
]


@pytest.mark.parametrize("url", ENDPOINTS)
@pytest.mark.parametrize("active", ["", "&active=1"])
def test_stage_note_on_every_metrics_response(client, url, active):
    d = get_ok(client, url + ("&" if "?" in url else "?") + active.lstrip("&") if active else url)
    assert d["window_info"]["stage_note"] == STAGE_NOTE
    if d.get("compare_info"):
        assert d["compare_info"]["stage_note"] == STAGE_NOTE


# ------------------------------------------------------------------------------------------------------------------ before the migration
def with_caps(monkeypatch, **caps):
    monkeypatch.setattr(q, "_CAPS_AT", time.monotonic() + 3600)          # do not re-probe the schema during the test
    for k, v in caps.items():
        monkeypatch.setitem(q._CAPS, k, v)


def test_active_is_503_until_the_data_exists(client, monkeypatch):
    with_caps(monkeypatch, active=False)
    for url in ("/api/products", "/api/products/vita-bull/rollup", "/api/products/vita-bull/ads", "/api/products/vita-bull/stage-daily", "/api/ads/1000"):
        r = client.get(url + "?active=1")
        assert r.status_code == 503 and "not built" in r.get_json()["error"], url
        assert client.get(url + "?active=0").status_code == 200, url
    assert get_ok(client, "/api/status")["active"]["available"] is False and get_ok(client, "/api/status")["active"]["creatives"] is None


def test_without_deleted_at_the_api_still_answers(client, monkeypatch):
    with_caps(monkeypatch, deleted=False)
    assert client.get("/api/products/vita-bull/families").status_code == 200
    assert client.get("/api/ads/2000").status_code == 200                 # no column to filter on: the row is just a row


def test_without_unmatched_lifetime_launch_uses_the_daily_rows(client, fx, monkeypatch):
    with_caps(monkeypatch, unm_launch=False)
    d = get_ok(client, "/api/products/vita-bull/unmatched")
    assert "every day held" in d["window_info"]["note"]
    assert d["total"]["spend"] == pytest.approx(float(sum(r["spend"] for r in fx.ad_daily if F.MAPPING[r["meta_family_key"]][0] is None
                                                             and fx.unmatched[r["meta_family_key"]]["product_id"] == fx.product_ids["vita-bull"])), abs=0.02)
