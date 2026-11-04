"""The registry as a whole: shape, naming, statuses, sheet tie-outs and the audit tools."""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

from app.projection.variables import registry as R
from app.projection.variables.spec import MODULES, VariableSpec

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts" / "projection"))
ID_RE = re.compile(r"^[a-z0-9_]+(\.[a-z0-9_]+)+$")


def test_registry_is_big_and_every_module_is_present():
    specs = R.all_specs()
    assert len(specs) >= 250
    for m in MODULES:
        assert R.for_module(m), m
    c = R.counts()
    assert c["_total"]["IMPLEMENTED"] > 200 and c["_total"]["PARTIAL"] >= 3 and c["_total"]["MISSING"] >= 10
    assert sum(c["_total"].values()) == len(specs)


def test_ids_are_unique_snake_case_and_start_with_the_module():
    seen = set()
    for s in R.all_specs():
        assert ID_RE.match(s.id), s.id
        assert s.id not in seen
        seen.add(s.id)
        assert s.id.startswith(s.module + ".")


def test_every_spec_is_documented():
    for s in R.all_specs():
        assert s.label and s.label[0].isupper() or s.label[0].isdigit(), s.id
        assert s.sheet_ref.strip(), s.id
        assert len(s.where_used) > 15, s.id
        assert not re.search(r"\b(TODO|TBD|memo)\b|Oct \d+|rationale", s.label + s.where_used), s.id         # finished-document wording: no build history
        if s.status == "PARTIAL":
            assert s.ticket or s.notes or s.probe == "view", f"{s.id}: PARTIAL needs a ticket or a note"
        if s.status == "MISSING":
            assert s.notes or s.ticket or s.where_used, s.id
        if s.probe == "none":
            assert s.notes, f"{s.id}: probe none needs the reason"
        if s.kind == "scalar" and s.numeric and s.editable_inline:
            assert s.lo is not None or s.hi is not None or s.value_type == "money", f"{s.id}: numeric levers carry bounds"


def test_acronyms_are_not_used_bare_in_labels():
    bare = re.compile(r"(?<!\()\b(NOI|AOV|CPA|ROAS|COGS|FBA|PO|3PL|MOQ|EBITDA|CAC|LTV|SKU|DTC)\b(?!\))")
    bad = [s.id for s in R.all_specs() if bare.search(s.label)]
    assert not bad, bad


def test_defaults_tie_to_the_sheet_snapshot():
    import snapshot_read as S
    checked = 0
    for s in R.all_specs():
        for tab, cell in s.sheet_cells:
            got = S.cell(tab, cell)
            want = s.default
            if got in (None, "") and want in (0, 0.0, None):
                continue
            assert isinstance(got, (int, float)), f"{s.id}: {tab}!{cell} = {got!r}"
            assert got * s.sheet_scale == pytest.approx(want, rel=1e-9), f"{s.id}: sheet {tab}!{cell} = {got}, default {want}"
            checked += 1
    assert checked >= 60


def test_series_keys_and_paths_are_well_formed():
    for s in R.all_specs():
        if s.binding in ("param", "supply"):
            assert s.path[0] in MODULES or s.path[0] in ("drivers", "financing"), s.id
        if s.binding == "series":
            assert re.match(r"^[a-z0-9_.]+$", s.series_key)


def test_module_paths_match_module_namespaces():
    """A spec's own params namespace is its module, except the documented cross-writes (pacing reads drivers / financing; supply table cells)."""
    for s in R.all_specs():
        if s.binding in ("param", "supply") and s.module != "pacing":
            assert s.path[0] == s.module, s.id


def test_formula_literal_tool_runs_on_the_snapshot():
    sys.path.insert(0, str(Path(__file__).parent / "tools"))
    import formula_literals as F
    pl = F.scan("P&L")
    flat = {l for g in pl for l in g["literals"]}
    assert {"0.07", "0.21", "15000", "20000000", "5000000"} <= flat
    assert F.literals('=IF(A1>0,B2*0.07+DATE(2025,1,1),"x 9")') == ["0.07"]
