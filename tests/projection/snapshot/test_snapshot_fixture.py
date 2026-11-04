"""Sanity checks on the frozen Lonvera Projections sheet snapshot (no database needed).

The snapshot lives outside the repo (default ~/lonvera-tag-explorer-data/projection-snapshot-2026-11-03,
override with PROJECTION_SNAPSHOT_DIR) and is produced by scripts/projection/snapshot.py.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts" / "projection"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import snapshot_read as sr  # noqa: E402

pytestmark = pytest.mark.skipif(
    not (sr.snapshot_dir() / "_manifest.json").exists(),
    reason="projection snapshot not present (run scripts/projection/snapshot.py)",
)

# Sep-2026 Total Company Revenue (P&L!W22) read live on 2026-11-03.
LIVE_W22_2026_11_03 = 1_286_956.0


def test_manifest_lists_every_tab_and_files_parse():
    m = sr.manifest()
    assert m["sheet_id"] == "1qA0iRJkWp1fbY5b6N860eM88Vs0JRe_8VfPQciMaLKI"
    assert m["captured_utc"] and m["tab_count"] == len(m["tabs"])
    modes = {e["mode"] for e in m["tabs"]}
    assert modes <= {"full", "head_tail", "skipped"}
    counts = {k: sum(1 for e in m["tabs"] if e["mode"] == k) for k in modes}
    assert counts == {"full": 45, "head_tail": 17, "skipped": 11}
    for e in m["tabs"]:
        assert {"gid", "title", "hidden", "rowCount", "columnCount", "mode", "snapshot_utc"} <= set(e)
        if e["mode"] == "skipped":
            assert e["files"] == [] and e["reason"]
            continue
        assert len(e["files"]) == 2
        for kind in ("formulas", "values"):
            path = sr.snapshot_dir() / f"{e['stem']}.{kind}.json"
            assert path.exists(), path
            obj = json.loads(path.read_text(encoding="utf-8"))
            assert obj["gid"] == e["gid"] and obj["title"] == e["title"]
            assert obj["dateTimeRenderOption"] == "SERIAL_NUMBER"
            assert obj["mode"] == e["mode"]
            if e["mode"] == "full":
                assert len(obj["values"]) == obj["rows"] == e["used_rows"]
                assert all(len(r) == obj["cols"] for r in obj["values"])
                assert obj["range"].startswith("A1:")
            else:
                assert len(obj["head"]) == obj["head_rows"] <= 6
                assert obj["tail_start_row"] + len(obj["tail"]) - 1 <= obj["rows"]
                assert len(obj["tail"]) <= 400
                assert all(len(r) == obj["cols"] for r in obj["head"] + obj["tail"])


def test_formulas_and_values_share_a_shape():
    for title in ("P&L", "Curves", "Cohort Data - Unified", "Purchase Orders"):
        f, v = sr.load(title, "formulas"), sr.load(title, "values")
        assert (f["rows"], f["cols"]) == (v["rows"], v["cols"])


def test_pnl_month_header_runs_jan_2025_to_dec_2029():
    serials = sr.row("P&L", 8, "C", "BJ")
    assert len(serials) == 60
    months = [sr.serial_to_date(s) for s in serials]
    assert months[0] == date(2025, 1, 1) and months[-1] == date(2029, 12, 1)
    assert all(d.day == 1 for d in months)
    assert [(d.year * 12 + d.month) for d in months] == list(range(2025 * 12 + 1, 2025 * 12 + 61))
    assert sr.cell("P&L", "A8") == "Month"


def test_pnl_months_file_matches_sheet_header():
    data = json.loads((sr.snapshot_dir() / "P&L.months.json").read_text(encoding="utf-8"))
    assert data["month_count"] == 60 and len(data["months"]) == 60
    assert data["months"][0]["col"] == "C" and data["months"][-1]["col"] == "BJ"
    assert [m["date"] for m in data["months"]] == [sr.serial_to_date(s).isoformat() for s in sr.row("P&L", 8, "C", "BJ")]
    assert [(f["col"], f["year"]) for f in data["fy_rollups"]] == [
        ("BL", 2025), ("BM", 2026), ("BN", 2027), ("BO", 2028), ("BP", 2029)]


def test_pnl_rowmap_covers_rows_1_to_215():
    data = json.loads((sr.snapshot_dir() / "P&L.rowmap.json").read_text(encoding="utf-8"))
    rows = data["rows"]
    assert [r["row"] for r in rows] == list(range(1, 216))
    assert {r["kind"] for r in rows} <= {"input", "formula", "blank"}
    by_row = {r["row"]: r for r in rows}
    assert by_row[22]["label"] == "Total Company Revenue" and by_row[22]["kind"] == "formula"
    assert by_row[22]["w_formula"].startswith("=")
    assert by_row[12]["label"].startswith("NC-Rev") and by_row[12]["kind"] == "input"
    assert by_row[30]["kind"] == "blank"


def test_pnl_w22_total_company_revenue_matches_live_reading():
    w22 = sr.cell("P&L", "W22")
    assert w22 == pytest.approx(LIVE_W22_2026_11_03, rel=0.005)
    # and it is the sum of its parts: DTC + Amazon + TikTok + sales tax (negative)
    parts = sum(sr.cell("P&L", f"W{r}") or 0 for r in (18, 19, 20, 21))
    assert w22 == pytest.approx(parts, rel=1e-9)
    assert sr.cell("P&L", "W22", "formulas").startswith("=")


def test_curves_lifetime_sum_sanity():
    ages = sr.col("Curves", "A", 4, 63)
    assert ages == list(range(1, 61))
    live = sr.col("Curves", "C", 4, 63)
    assert all(isinstance(x, (int, float)) and x >= 0 for x in live)
    lifetime = sr.cell("Curves", "C65")
    assert lifetime == pytest.approx(sum(live), rel=1e-9)
    assert 1.0 < lifetime < 3.0
    assert sr.cell("Curves", "C66") == pytest.approx(sum(live[:12]), rel=1e-9)
    assert 0.5 < sr.cell("Curves", "C66") < lifetime
    assert sr.cell("Curves", "C65", "formulas") == "=SUM(C4:C63)"


def test_tab_resolution_handles_trailing_space_gid_and_slug():
    by_title = sr.resolve("Automated Pacing ")
    assert sr.resolve("Automated Pacing")["gid"] == by_title["gid"] == 1058761071
    assert sr.resolve(1058761071)["title"] == "Automated Pacing "
    assert sr.resolve("p-l")["title"] == "P&L"
    with pytest.raises(KeyError):
        sr.resolve("No Such Tab")


def test_skipped_tabs_are_listed_but_not_loadable():
    skipped = [e["title"] for e in sr.tabs() if e["mode"] == "skipped"]
    assert len(skipped) == 11 and "Stuck Orders" in skipped and "Klaviyo - Daily" in skipped
    with pytest.raises(KeyError):
        sr.load("Stuck Orders")


def test_head_tail_tab_access():
    obj = sr.load("Ad Spend - Daily")
    assert obj["mode"] == "head_tail" and obj["rows"] > 400
    assert sr.row("Ad Spend - Daily", 1, 1, 3)[0] is not None  # header row captured
    last = obj["rows"]
    assert any(sr.row("Ad Spend - Daily", last, 1, 3))
    with pytest.raises(KeyError):
        sr.row("Ad Spend - Daily", obj["head_rows"] + 5)  # between head and tail
    assert sr.cell("Ad Spend - Daily", "A1") == obj["head"][0][0]


def test_known_errors_are_recorded_in_manifest():
    errors = {e["title"]: e["error_cells"] for e in sr.tabs() if e.get("error_cells")}
    assert errors.get("P&L", 0) > 0  # B94 and row 128 are #REF! in the live sheet
    assert sr.cell("P&L", "C128").startswith("#REF!")


def test_cell_helpers():
    assert sr.col_letter(1) == "A" and sr.col_letter(26) == "Z" and sr.col_letter(27) == "AA"
    assert sr.col_letter(62) == "BJ" and sr.col_index("BJ") == 62 and sr.col_index("aa") == 27
    for i in (1, 25, 26, 27, 52, 53, 62, 160, 702, 703):
        assert sr.col_index(sr.col_letter(i)) == i
    assert sr.parse_ref("W17") == (17, 23) and sr.parse_ref("$BJ$8") == (8, 62)
    assert sr.serial_to_date(45658) == date(2025, 1, 1)
    assert sr.serial_to_date(45658.75) == date(2025, 1, 1)
    assert sr.cell("P&L", "ZZ9999") is None  # outside the used range
    assert sr.row("P&L", 8, 3, 5) == sr.row("P&L", 8, "C", "E")


def test_env_override_points_at_another_directory(tmp_path, monkeypatch):
    monkeypatch.setenv(sr.ENV_VAR, str(tmp_path))
    assert sr.snapshot_dir() == tmp_path
    with pytest.raises(FileNotFoundError):
        sr.manifest()
