"""Offline unit tests for scripts/projection/snapshot.py (no network, no snapshot needed)."""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts" / "projection"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import snapshot as snap  # noqa: E402


def test_slugify():
    assert snap.slugify("P&L") == "p-l"
    assert snap.slugify("Automated Pacing ") == "automated-pacing"
    assert snap.slugify("Pacing - Daily (180d)") == "pacing-daily-180d"
    assert snap.slugify("Orders - Daily (NC-RC Rev)") == "orders-daily-nc-rc-rev"


def test_trim_and_pad_grid():
    grid = [["a", "", ""], ["", "b", None, ""], ["", "", ""], []]
    trimmed = snap.trim_grid(grid)
    assert trimmed == [["a", ""], ["", "b"]]
    assert snap.pad_grid(trimmed, 3, 3) == [["a", "", ""], ["", "b", ""], ["", "", ""]]
    assert snap.trim_grid([[], [""]]) == []
    assert snap.shape([]) == (0, 0)
    assert snap.a1_end(1179, 86) == "A1:CH1179"


def test_find_errors():
    n, samples = snap.find_errors([[1, "#REF! (Unresolved sheet name 'X'.)"], ["#N/A", "ok"], ["#hashtag", 3]])
    assert n == 2 and samples[0].startswith("B1=#REF!") and samples[1] == "A2=#N/A"


def test_plan_modes_matches_titles_ignoring_whitespace():
    tabs = [{"gid": 1, "title": "Automated Pacing "}, {"gid": 2, "title": "Meta Bids - Daily"},
            {"gid": 3, "title": "Stuck Orders"}, {"gid": 4, "title": "Brand New Tab"}]
    modes = snap.plan_modes(tabs)
    assert modes[1][0] == "full" and modes[2][0] == "head_tail"
    assert modes[3][0] == "skipped" and modes[4] == ("skipped", "unlisted in snapshot plan")


def test_tab_plan_has_no_overlaps_and_covers_73_tabs():
    full = {t.strip() for t in snap.FULL_TABS}
    ht = {t.strip() for t in snap.HEAD_TAIL_TABS}
    skip = {t.strip() for t in snap.SKIPPED_TABS}
    assert not (full & ht) and not (full & skip) and not (ht & skip)
    assert (len(full), len(ht), len(skip)) == (45, 17, 11)
    assert len(snap.SKIPPED_TABS) == 11 and sum(t.startswith("Klaviyo") for t in skip) == 8


def test_quote_escapes_apostrophes():
    assert snap._quote("P&L") == "'P&L'"
    assert snap._quote("Bob's tab") == "'Bob''s tab'"


def test_script_never_calls_a_write_endpoint():
    src = (SCRIPTS / "snapshot.py").read_text(encoding="utf-8")
    for forbidden in (r"batchUpdate", r"\.update\(\s*spreadsheetId", r"\.clear\(", r"\.append\(\s*spreadsheetId",
                      r"batchClear", r"copyTo", r"\.create\(\s*body"):
        assert not re.search(forbidden, src), forbidden
    assert "values().batchGet(" in src and "spreadsheets().get(" in src


def test_cli_dry_run_flags_exist():
    import argparse  # noqa: F401 - parser is built inside _cli; just make sure --help parses
    with pytest.raises(SystemExit) as exc:
        snap._cli(["--help"])
    assert exc.value.code == 0
