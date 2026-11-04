"""International shipments CSV: parsing, preview-then-commit with a digest, estimates replaced, audited, closed days only."""
from __future__ import annotations

import io
from datetime import date

import pytest

from app.projection.inventory_demand import upload
from etl import projection_inventory_demand as etl
from idemand_helpers import FakeReader, P, daily_tab, days, product_daily_tab, stock_tab

AS_OF = date(2026, 11, 3)
WIDE = "Date,Vital Bull,Beef Liver,Boneflex,Vital Flow\n2026-10-10,100,50,40,0\n2026-10-11,110,55,45,2\n"


def post(client, text, commit=False, digest=None, name="intl.csv", **kw):
    data = {"file": (io.BytesIO(text.encode()), name)}
    if digest:
        data["digest"] = digest
    return client.post(f"{P}/upload-intl" + ("?commit=1" if commit else ""), data=data, content_type="multipart/form-data", **kw)


@pytest.fixture()
def feed(conn, monkeypatch):
    monkeypatch.setenv("PROJECTION_TODAY", "2026-11-03")
    rows = {d: (5, 6, 7, 1) for d in days(date(2026, 9, 1), 42)}                              # through Oct 12
    etl.refresh(conn, days=60, reader=FakeReader({
        "Warehance Daily": daily_tab("revamp", {d: (1, 1, 1, 1) for d in days(date(2026, 10, 1), 33)}, "2026-11-03 is a partial day."),
        "Amazon - Product Daily": product_daily_tab({d: (1, 1, 1, 0) for d in days(date(2026, 9, 1), 64)}),
        "Intl Daily Units": daily_tab("intl", rows, "Rows from 2026-10-08 (grey italics) are estimates."),
        "Warehance Fcst Inputs": stock_tab(AS_OF)}))


# ------------------------------------------------------------------------------------------------------------- parser

def test_wide_layout_with_product_names():
    p = upload.parse_csv(WIDE.encode(), AS_OF)
    assert p.errors == [] and p.cells[("bull", date(2026, 10, 11))] == 110 and p.cells[("flow", date(2026, 10, 11))] == 2 and len(p.cells) == 8


def test_long_layout_adds_up_lines_and_knows_the_warehouse_item_codes():
    text = "Ship Date,SKU,Qty\n10/10/2026,DPCD1-GN178001,60\n10/10/2026,DPCD1-GN178001,40\n10/10/2026,DPCD1-GN187001,7\n2026-10-11,Beef Liver,3\n"
    p = upload.parse_csv(text.encode(), AS_OF)
    assert p.errors == [] and p.cells == {("bull", date(2026, 10, 10)): 100, ("boneflex", date(2026, 10, 10)): 7, ("liver", date(2026, 10, 11)): 3}


def test_tab_separated_and_byte_order_mark_are_fine():
    p = upload.parse_csv("﻿date\tbull\tliver\n2026-10-10\t5\t6\n".encode("utf-8"), AS_OF)
    assert p.errors == [] and len(p.cells) == 2


def test_blank_cells_mean_not_reported_and_are_not_zero():
    p = upload.parse_csv(b"date,bull,liver\n2026-10-10,5,\n", AS_OF)
    assert p.cells == {("bull", date(2026, 10, 10)): 5}


def test_problems_are_listed_not_raised():
    text = "date,bull\n2026-10-10,abc\n2026-10-11,-4\nsoon,3\n2026-11-03,9\n2026-11-09,9\n2026-10-12,2\n"
    p = upload.parse_csv(text.encode(), AS_OF)
    assert len(p.errors) == 5 and p.cells == {("bull", date(2026, 10, 12)): 2}
    assert any("unreadable date" in e for e in p.errors) and any("not a complete day" in e for e in p.errors)


@pytest.mark.parametrize("data,fragment", [(b"", "empty"), (b"\xff\xfe\x00", "UTF-8"), (b"a,b\n1,2\n", "no date column"), (b"date,x,y\n2026-10-01,1,2\n", "no product columns"),
                                           (b"date,product,units\n", "no usable rows")])
def test_unusable_files_are_rejected(data, fragment):
    from app.queries import ApiError
    try:
        p = upload.parse_csv(data, AS_OF)
    except ApiError as e:
        assert fragment in e.message
    else:
        assert fragment in p.errors[0]


def test_a_file_spanning_over_a_year_is_flagged():
    p = upload.parse_csv(b"date,bull\n2024-01-01,1\n2026-10-10,1\n", AS_OF)
    assert any("spans more than" in e for e in p.errors)


def test_digest_depends_on_the_content_only():
    a = upload.parse_csv(WIDE.encode(), AS_OF)
    b = upload.parse_csv(WIDE.replace("100", "101").encode(), AS_OF)
    assert a.digest == upload.parse_csv(WIDE.encode(), AS_OF).digest and a.digest != b.digest


# ------------------------------------------------------------------------------------------------------------- endpoint

def test_preview_shows_the_diff_and_writes_nothing(client, feed, pg):
    before = pg.execute("SELECT count(*) AS n, sum(units) AS u FROM proj_units_daily WHERE location = 'intl'").fetchone()
    r = post(client, WIDE)
    body = r.get_json()
    assert r.status_code == 200 and body["preview"] is True and body["errors"] == [] and len(body["digest"]) == 16
    d = body["diff"]
    assert d["rows"] == 8 and d["days"] == 2 and d["replaces_estimate"] == 8 and d["new"] == 0 and d["changes_loaded"] == 0
    assert d["first_day"] == "2026-10-10" and d["last_day"] == "2026-10-11" and d["moves"][0]["kind"] == "replaces estimate"
    assert pg.execute("SELECT count(*) AS n, sum(units) AS u FROM proj_units_daily WHERE location = 'intl'").fetchone() == before
    assert pg.execute("SELECT count(*) AS n FROM proj_audit WHERE action = 'inventory_demand.upload_intl'").fetchone()["n"] == 0


def test_commit_needs_the_digest_of_the_previewed_file(client, feed, pg):
    digest = post(client, WIDE).get_json()["digest"]
    for bad in (None, "0" * 16):
        r = post(client, WIDE, commit=True, digest=bad)
        assert r.status_code == 400 and "preview this exact file" in r.get_json()["error"]
    assert post(client, WIDE.replace("100", "999"), commit=True, digest=digest).status_code == 400          # a different file than the one previewed
    assert pg.execute("SELECT count(*) AS n FROM proj_units_daily WHERE source = 'upload'").fetchone()["n"] == 0


def test_commit_replaces_estimates_audits_and_logs_the_load(client, feed, pg):
    digest = post(client, WIDE).get_json()["digest"]
    r = post(client, WIDE, commit=True, digest=digest)
    body = r.get_json()
    assert r.status_code == 200 and body["committed"] is True and body["rows_written"] == 8
    got = pg.execute("SELECT product, day, units, source, is_estimate FROM proj_units_daily WHERE source = 'upload' ORDER BY day, product").fetchall()
    assert len(got) == 8 and not any(g["is_estimate"] for g in got)
    assert pg.execute("SELECT units FROM proj_units_daily WHERE location='intl' AND product='bull' AND day='2026-10-11'").fetchone()["units"] == 110
    a = pg.execute("SELECT actor, new, note FROM proj_audit WHERE action = 'inventory_demand.upload_intl'").fetchall()
    assert len(a) == 1 and a[0]["actor"] == "JoshHoughton" and a[0]["new"]["replaces_estimate"] == 8 and a[0]["note"] == "intl.csv"
    load = pg.execute("SELECT source, rows, first_day, last_day FROM proj_units_loads WHERE source = 'upload'").fetchone()
    assert load["rows"] == 8 and load["last_day"] == date(2026, 10, 11)
    # the same file again changes nothing (idempotent) and the next refresh does not put the estimates back
    assert post(client, WIDE, commit=True, digest=digest).get_json()["rows_written"] == 0
    assert post(client, WIDE).get_json()["diff"]["unchanged"] == 8
    assert pg.execute("SELECT units FROM proj_units_daily WHERE location='intl' AND product='bull' AND day='2026-10-11'").fetchone()["units"] == 110


def test_estimates_left_in_the_forecast_window_drop_after_the_upload(client, feed):
    before = client.get(f"{P}/inputs").get_json()["locations"][2]["feed"]["estimate_days"]
    digest = post(client, WIDE).get_json()["digest"]
    post(client, WIDE, commit=True, digest=digest)
    after = client.get(f"{P}/inputs").get_json()["locations"][2]["feed"]["estimate_days"]
    assert before == 5 and after == 3                                                              # Oct 8, 9 and 12 remain estimates


def test_a_file_with_problems_previews_them_and_cannot_be_committed(client, feed, pg):
    text = "date,bull\n2026-10-10,5\nbad,6\n"
    body = post(client, text).get_json()
    assert len(body["errors"]) == 1 and body["diff"]["rows"] == 1
    r = post(client, text, commit=True, digest=body["digest"])
    assert r.status_code == 400 and "fix 1 problem" in r.get_json()["error"]
    assert pg.execute("SELECT count(*) AS n FROM proj_units_daily WHERE source = 'upload'").fetchone()["n"] == 0


def test_missing_file_and_non_multipart_are_400_415(client, feed):
    assert client.post(f"{P}/upload-intl", data={}, content_type="multipart/form-data").status_code == 400
    assert client.post(f"{P}/upload-intl", json={"a": 1}).status_code == 400


def test_uploads_are_rate_limited_separately(app_factory, feed):
    c = app_factory(AUTH_DISABLED=True, PROJ_RATE_UPLOADS_PER_HOUR=2).test_client()
    codes = [post(c, WIDE).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
