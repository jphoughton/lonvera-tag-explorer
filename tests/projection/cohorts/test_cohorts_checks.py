"""Upload plausibility checks (blocking errors, warnings, before/after multiples), the QA probes, and undoing an upload exactly."""
from __future__ import annotations

import io
import sys
from datetime import date
from pathlib import Path

import pytest
from conftest import build_history, seed_cohorts

from app.projection import rwdb
from app.projection.cohorts import checks, engine as eng, store
from app.projection.cohorts import model as cohorts_model

D = date
TODAY = D(2026, 11, 3)
P = "/api/projection"
C = f"{P}/cohorts"
RATIOS = (0.2, 0.3, 0.2, 0.1, 0.05, 0.04, 0.03, 0.03, 0.02, 0.02, 0.02, 0.02, 0.01, 0.01)
HEADER = "Cohort Month,New Customers,90-Day Repeat Rate,CAC,Payback Period (Months),First Order (Realized),First Order," + ",".join(f"M{i}" for i in range(1, 37))
BANDS = eng.Bands()
LEVERS = eng.Levers()


def table() -> list[eng.Cohort]:
    return build_history(first=D(2024, 8, 1), last=D(2026, 10, 1), ratios=RATIOS)


def row_from(c: eng.Cohort, **over) -> store.CohortRow:
    cum = {a: v for a, v in enumerate(c.cum, start=1) if v is not None}
    kw = dict(month=c.month, new_customers=c.new_customers, first_order=c.first_order, first_order_realized=c.first_order, repeat_90d=c.repeat_90d, cac=c.cac, cum=cum)
    kw.update(over)
    return store.CohortRow(**kw)


def by_month(cohorts, month):
    return next(c for c in cohorts if c.month == month)


# ---------------------------------------------------------------------------------------------------------------- unit: the checks

def test_the_stored_table_itself_is_inside_every_band():
    life, twelve = checks.multiples(table(), LEVERS)
    assert BANDS.lifetime_min < life < BANDS.lifetime_max and BANDS.twelve_min < twelve < BANDS.twelve_max


def test_a_clean_refresh_has_no_errors_and_reports_before_and_after():
    t = table()
    rep = checks.check([row_from(c) for c in t], t, BANDS, LEVERS)
    assert rep.errors == [] and rep.warnings == []
    assert rep.impact["lifetime"]["before"] == pytest.approx(rep.impact["lifetime"]["after"]) and rep.impact["lifetime"]["min"] == 0.5 and rep.impact["twelve"]["max"] == 3.0


def test_qa_probe_a_first_order_of_99999_is_blocked():
    t = table()
    octo = by_month(t, D(2026, 10, 1))
    rep = checks.check([row_from(octo, first_order=99999.0)], t, BANDS, LEVERS)
    assert any("first order per customer is $99,999" in e and "outside the allowed $5 to $500" in e for e in rep.errors)
    assert rep.impact["lifetime"]["after"] == pytest.approx(rep.impact["lifetime"]["before"])       # the newest cohort has no repeat yet, so the curve is untouched


def test_qa_probe_a_negative_first_order_is_blocked_and_says_so():
    t = table()
    rep = checks.check([row_from(by_month(t, D(2026, 10, 1)), first_order=-5.0)], t, BANDS, LEVERS)
    assert any("first order per customer is negative ($-5.00)" in e or "is negative" in e for e in rep.errors)
    assert any(i.code == "negative_first_order" for i in rep.issues)


def test_qa_probe_a_negative_first_order_on_a_cohort_with_repeat_would_flip_the_multiple_negative_and_is_blocked_twice():
    t = table()
    rep = checks.check([row_from(by_month(t, D(2026, 9, 1)), first_order=-5.0)], t, BANDS, LEVERS)       # Sep-26 has a first month: its increment ratio turns hugely negative
    assert rep.impact["lifetime"]["after"] < 0 < rep.impact["lifetime"]["before"]
    assert any("is negative" in e for e in rep.errors) and any("over 60 months would be -" in e for e in rep.errors)


def test_qa_probe_customers_falling_from_thousands_to_12_is_blocked():
    t = table()
    octo = by_month(t, D(2026, 10, 1))
    rep = checks.check([row_from(octo, new_customers=12.0)], t, BANDS, LEVERS)
    assert any("new customers change from" in e and "to 12" in e and "60%" in e for e in rep.errors)


def test_a_new_cohort_far_from_its_neighbours_in_size_is_only_a_warning():
    t = table()[:-1]                                                              # Oct-26 not loaded yet
    octo = by_month(table(), D(2026, 10, 1))
    rep = checks.check([row_from(octo, new_customers=9000.0)], t, BANDS, LEVERS)
    assert rep.errors == [] and any("new customers is" in w and "around it" in w for w in rep.warnings)


def test_first_order_far_from_the_cohorts_around_it_is_blocked_for_large_cohorts_only():
    t = table()
    sep = by_month(t, D(2026, 9, 1))                                              # first order ~69, neighbours ~68
    rep = checks.check([row_from(sep, first_order=25.0)], t, BANDS, LEVERS)
    assert any("below" in e and "around it" in e and "limit 40%" in e for e in rep.errors)
    small = checks.check([row_from(sep, first_order=25.0, new_customers=300.0)], t, BANDS, LEVERS)
    assert not any(i.code == "first_order_neighbors" for i in small.issues)       # too small to be judged against neighbours


def test_cumulative_dollars_that_fall_block_when_large_warn_when_small_and_pass_when_tiny():
    t = table()
    c = by_month(t, D(2026, 3, 1))
    base = list(c.cum)

    def with_dip(factor):
        cum = {a: v for a, v in enumerate(base, start=1) if v is not None}
        cum[4] = cum[3] * factor
        return checks.check([row_from(c, cum=cum)], t, BANDS, LEVERS)

    assert any("fall from" in e and "at month 4" in e for e in with_dip(0.90).errors)            # 10% fall
    small = with_dip(0.98)                                                                         # 2% fall
    assert any(i.code == "cumulative_falls" and i.level == "warning" for i in small.issues) and small.errors == []
    tiny = with_dip(0.999)                                                                         # refund-sized noise
    assert not any(i.code == "cumulative_falls" for i in tiny.issues) and tiny.errors == []


def test_a_negative_cumulative_amount_is_blocked():
    t = table()
    c = by_month(t, D(2026, 3, 1))
    cum = {a: v for a, v in enumerate(c.cum, start=1) if v is not None}
    cum[2] = -3.0
    rep = checks.check([row_from(c, cum=cum)], t, BANDS, LEVERS)
    assert any("holds a negative amount" in e for e in rep.errors)


def test_the_multiple_band_blocks_and_the_bands_can_be_widened():
    t = table()
    c = by_month(t, D(2026, 9, 1))                                                                  # the newest cohort with repeat sets the curve at month 1
    cum = {1: c.first_order * 30.0}                                                                 # a first month 30 times the first order
    rep = checks.check([row_from(c, cum=cum)], t, BANDS, LEVERS)
    assert any("over 60 months would be" in e and "outside the allowed 0.5x to 5x" in e and "(now " in e for e in rep.errors)
    wide = eng.parse_bands({"upload_lifetime_max": 50.0, "upload_twelve_max": 20.0})
    assert checks.check([row_from(c, cum=cum)], t, wide, LEVERS).errors == []
    assert rep.impact["lifetime"]["after"] > 5 and rep.impact["lifetime"]["before"] == pytest.approx(checks.multiples(t, LEVERS)[0])


def test_a_multiple_that_moves_a_lot_inside_its_band_is_a_warning():
    t = table()
    cohorts = [eng.Cohort(**{**c.__dict__, "cum": tuple((c.first_order + (v - c.first_order) * 1.5) if v else v for v in c.cum)}) if c.month >= D(2026, 6, 1) else c for c in t]
    rep = checks.check([row_from(c) for c in cohorts if c.month >= D(2026, 6, 1)], t, BANDS, LEVERS)
    assert rep.errors == [] and any("moves from" in w for w in rep.warnings)


def test_a_file_with_one_cohort_is_checked_against_the_table_and_noted():
    t = table()
    sep = by_month(t, D(2026, 9, 1))
    ok = checks.check([row_from(sep)], t, BANDS, LEVERS)
    assert ok.errors == [] and any("holds 1 cohort" in w and "the table holds" in w for w in ok.warnings)
    bad = checks.check([row_from(sep, first_order=99999.0)], t, BANDS, LEVERS)
    assert bad.errors and any("holds 1 cohort" in w for w in bad.warnings)


def test_the_first_load_of_an_empty_table_has_no_before_value():
    t = table()
    rep = checks.check([row_from(c) for c in t], [], BANDS, LEVERS)
    assert rep.errors == [] and rep.impact["lifetime"]["before"] is None and rep.impact["lifetime"]["after"] > 0


def test_merged_state_keeps_stored_cells_and_overlays_the_rows():
    t = table()
    c = by_month(t, D(2026, 3, 1))
    merged = by_month(checks.merged_state(t, [store.CohortRow(month=c.month, new_customers=c.new_customers, cum={1: 99.0})]), c.month)
    assert merged.cum[0] == 99.0 and merged.cum[1] == c.cum[1] and merged.first_order == c.first_order           # first order kept (row gave none)


def test_the_real_sheet_data_passes_every_check_as_a_first_load_and_as_a_refresh(monkeypatch):
    monkeypatch.delenv("PROJECTION_SNAPSHOT_DIR", raising=False)                  # the module-wide fixture points it at an empty folder
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts" / "projection"))
    try:
        from etl import projection_cohorts as seeder
        rows, goal, snap = seeder.read_snapshot()
    except Exception as e:
        pytest.skip(f"sheet snapshot not available: {e}")
    stored = [eng.Cohort(month=r.month, new_customers=r.new_customers, first_order=r.first_order, cum=tuple(r.cum.get(a) for a in range(1, 37)), cac=r.cac,
                         repeat_90d=r.repeat_90d) for r in rows]
    first_load = checks.check(rows, [], BANDS, LEVERS)
    refresh = checks.check(rows, stored, BANDS, LEVERS)
    assert first_load.errors == [] and refresh.errors == [] and refresh.warnings == []
    assert first_load.impact["lifetime"]["after"] == pytest.approx(1.5788, abs=1e-3) and refresh.impact["lifetime"]["before"] == pytest.approx(first_load.impact["lifetime"]["after"])


# ---------------------------------------------------------------------------------------------------------------- API: QA probes

@pytest.fixture(autouse=True)
def _frozen(monkeypatch, tmp_path):
    monkeypatch.setattr(cohorts_model, "today", lambda: TODAY)
    monkeypatch.setenv("PROJECTION_SNAPSHOT_DIR", str(tmp_path))


@pytest.fixture()
def loaded(cohorts_db):
    with rwdb.get_rw(cohorts_db) as conn:
        seed_cohorts(conn, table(), goal=[0.2, 0.1] + [0.01] * 58)


def csv_from(cohorts: list[eng.Cohort], **over_by_month) -> bytes:
    lines = [HEADER]
    for c in cohorts:
        o = over_by_month.get(c.month.isoformat()[:7], {})
        cum = {a: v for a, v in enumerate(c.cum, start=1) if v is not None}
        cum.update(o.get("cum", {}))
        cells = [str(cum[a]) if a in cum else "0" for a in range(1, 37)]
        lines.append(",".join([c.month.isoformat()[:7], str(o.get("n", c.new_customers)), "0.5", "80", "", str(o.get("fo", c.first_order)), str(o.get("fo", c.first_order))] + cells))
    return ("\n".join(lines) + "\n").encode()


def post(client, raw: bytes, commit: bool = False, sha: str = "", name: str = "cohorts.csv"):
    data = {"file": (io.BytesIO(raw), name)}
    if commit:
        data["expect_sha256"] = sha
    return client.post(f"{C}/upload" + ("?commit=1" if commit else ""), data=data, content_type="multipart/form-data")


def state(pg) -> list:
    heads = pg.execute("SELECT cohort_month, new_customers::text, first_order_per_customer::text, first_order_realized::text, repeat_90d::text, cac::text, "
                       "payback_months::text, nc_rev_reference::text, source FROM proj_cohorts ORDER BY 1").fetchall()
    cells = pg.execute("SELECT cohort_month, age, cum_per_customer::text FROM proj_cohort_cum ORDER BY 1, 2").fetchall()
    return [heads, cells]


@pytest.mark.parametrize("label,over,fragment", [
    ("99,999", {"fo": 99999.0}, "outside the allowed $5 to $500"),
    ("-5", {"fo": -5.0}, "is negative"),
    ("customers 3,614 to 12", {"n": 12}, "new customers change from"),
])
def test_qa_probes_are_not_committable_and_say_why(client, loaded, pg, label, over, fragment):
    cohorts = table()
    raw = csv_from([c for c in cohorts if c.month == D(2026, 10, 1)], **{"2026-10": over})
    before = state(pg)
    r = post(client, raw)
    assert r.status_code == 200
    out = r.get_json()
    assert out["committable"] is False and out["errors"] and any(fragment in e for e in out["errors"]), (label, out["errors"])
    assert out["impact"]["lifetime"]["before"] is not None and out["impact"]["lifetime"]["after"] is not None
    c = post(client, raw, commit=True, sha=out["sha256"])
    assert c.status_code == 400 and "cannot be loaded" in c.get_json()["error"]
    assert state(pg) == before                                                       # nothing was written


def test_a_file_of_only_one_cohort_is_committable_when_sound_and_noted(client, loaded):
    raw = csv_from([c for c in table() if c.month == D(2026, 9, 1)])
    out = post(client, raw).get_json()
    assert out["committable"] is True and out["errors"] == [] and any("holds 1 cohort" in w for w in out["warnings"])
    assert out["summary"]["cells_changed"] == 0 and out["summary"]["cells_added"] == 0


def test_a_sound_refresh_shows_the_multiples_before_and_after_and_is_committable(client, loaded):
    out = post(client, csv_from(table())).get_json()
    assert out["committable"] is True and out["errors"] == [] and out["issues"] == []
    life = out["impact"]["lifetime"]
    assert life["before"] == pytest.approx(life["after"]) and 0.5 < life["after"] < 5
    assert out["summary"]["lifetime_before"] == pytest.approx(life["before"])


def test_the_bands_come_from_the_plan_of_record_and_can_be_widened(client, loaded):
    raw = csv_from([c for c in table() if c.month == D(2026, 10, 1)], **{"2026-10": {"fo": 800.0}})
    assert post(client, raw).get_json()["committable"] is False
    r = client.put(f"{C}/levers", json={"levers": {"upload_first_order_max": 1000.0, "upload_lifetime_min": 0.0, "upload_first_order_tol": 5.0}})
    assert r.status_code == 200 and r.get_json()["own"]["upload_first_order_max"] == 1000.0
    out = post(client, raw).get_json()
    assert not any(i["code"] == "first_order_band" for i in out["issues"])
    bad = client.put(f"{C}/levers", json={"levers": {"upload_lifetime_min": 6.0}})
    assert bad.status_code == 400 and "lower than" in bad.get_json()["error"]


# ---------------------------------------------------------------------------------------------------------------- undo

def test_undo_restores_every_touched_cohort_exactly_and_removes_the_ones_it_added(client, loaded, pg, monkeypatch):
    t = table()
    sep = by_month(t, D(2026, 9, 1))
    new_month = D(2026, 11, 1)
    before = state(pg)
    # a revision (Sep-26 first month revised, a new month 2 added) plus a brand-new closed cohort (Nov-26, with Dec closed for the test clock)
    revised = eng.Cohort(**{**sep.__dict__, "cum": (sep.cum[0] * 1.02, sep.cum[0] * 1.02 + 3.0) + (None,) * 34})
    extra = eng.Cohort(month=new_month, new_customers=1100.0, first_order=70.0, cum=(None,) * 36)
    raw = csv_from([revised, extra])
    monkeypatch.setattr(cohorts_model, "today", lambda: D(2026, 12, 15))                       # Nov-26 is closed by then
    out = post(client, raw).get_json()
    assert out["committable"] is True, out["errors"]
    done = post(client, raw, commit=True, sha=out["sha256"])
    assert done.status_code == 200 and done.get_json()["upload_id"]
    uid = done.get_json()["upload_id"]
    assert state(pg) != before
    assert pg.execute("SELECT count(*) AS n FROM proj_cohorts WHERE cohort_month = '2026-11-01'").fetchone()["n"] == 1
    summary = client.get(f"{C}/summary").get_json()
    assert summary["last_upload"]["id"] == uid and summary["last_upload"]["undoable"] is True

    u = client.post(f"{C}/upload/undo", json={"upload_id": uid})
    assert u.status_code == 200 and u.get_json()["cohorts_removed"] == 1 and u.get_json()["cohorts_restored"] == 1
    assert state(pg) == before                                                              # exact, to the last stored digit
    (a,) = pg.execute("SELECT * FROM proj_audit WHERE action = 'cohorts.upload.undo'").fetchall()
    assert a["actor"] == "JoshHoughton" and a["new"]["upload_id"] == uid and a["note"] == "cohorts.csv"
    hist = client.get(f"{C}/upload/history").get_json()["uploads"]
    assert hist[0]["id"] == uid and hist[0]["undone"] is True and hist[0]["undoable"] is False
    assert client.get(f"{C}/summary").get_json()["last_upload"] is None


def test_undo_goes_newest_first_and_refuses_anything_else(client, loaded, pg):
    t = table()
    sep, octo = by_month(t, D(2026, 9, 1)), by_month(t, D(2026, 10, 1))
    snaps = [state(pg)]
    ids = []
    for c, bump in ((sep, 1.01), (octo, 1.02)):
        r = eng.Cohort(**{**c.__dict__, "cum": (c.first_order * bump + 1.0,) + (None,) * 35 if c is octo else (c.cum[0] * bump,) + (None,) * 35})
        raw = csv_from([r])
        out = post(client, raw).get_json()
        assert out["committable"], out["errors"]
        ids.append(post(client, raw, commit=True, sha=out["sha256"]).get_json()["upload_id"])
        snaps.append(state(pg))
    assert client.post(f"{C}/upload/undo", json={"upload_id": ids[0]}).status_code == 409                   # the older one first: refused
    assert client.post(f"{C}/upload/undo", json={"upload_id": ids[1]}).status_code == 200 and state(pg) == snaps[1]
    assert client.post(f"{C}/upload/undo", json={"upload_id": ids[1]}).status_code == 409                   # already undone
    assert client.post(f"{C}/upload/undo", json={"upload_id": ids[0]}).status_code == 200 and state(pg) == snaps[0]
    assert client.post(f"{C}/upload/undo", json={"upload_id": 987654}).status_code == 404
    assert client.post(f"{C}/upload/undo", json={}).status_code == 400 and client.post(f"{C}/upload/undo", json={"upload_id": "1"}).status_code == 400


def test_the_commit_audit_row_carries_the_upload_id_and_history_lists_it(client, loaded, pg):
    raw = csv_from([by_month(table(), D(2026, 9, 1))])
    out = post(client, raw).get_json()
    uid = post(client, raw, commit=True, sha=out["sha256"], name="oct-refresh.csv").get_json()["upload_id"]
    (a,) = pg.execute("SELECT * FROM proj_audit WHERE action = 'cohorts.upload'").fetchall()
    assert a["new"]["upload_id"] == uid
    h = client.get(f"{C}/upload/history?limit=5").get_json()["uploads"][0]
    assert h["id"] == uid and h["filename"] == "oct-refresh.csv" and h["actor"] == "JoshHoughton" and h["lifetime_before"] is not None and h["undoable"] is True
    assert client.get(f"{C}/upload/history?limit=0").status_code == 400


def test_undo_needs_the_apps_login(anon, loaded):
    assert anon.post(f"{C}/upload/undo", json={"upload_id": 1}).status_code == 401 and anon.get(f"{C}/upload/history").status_code == 401
