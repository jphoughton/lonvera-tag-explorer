"""Eval-only holdout (BRAIN_HOLDOUT_FILE): held-out ledger answers are invisible to past_questions and search; with the variable unset nothing changes."""
from __future__ import annotations

import json

import pytest

from brain import knowledge as K
from brain import tools as T

ANSWER = "Billing reminder flow, not timing. Consistent with the Sep 14 scale-up."


@pytest.fixture()
def root(fake_root):
    q = fake_root / "docs" / "questions"
    items = [json.loads(ln) for ln in (q / "lonvera_items.jsonl").read_text().splitlines()]
    items.append({"id": 9, "date": "2026-10-24", "verbatim": "Same thing asked again", "answer": ANSWER, "status": "answered"})   # near-duplicate of item 7
    items.append({"id": 10, "date": "2026-10-25", "verbatim": "Unrelated pelican question", "answer": "Pelicans are fine.", "status": "answered"})
    (q / "lonvera_items.jsonl").write_text("\n".join(json.dumps(i) for i in items) + "\n")
    (q / "ledger_marketing.md").write_text("# Ledger\n\nBilling reminder flow is the driver of repeat revenue.\n")
    (fake_root / "docs" / "GUIDE.md").write_text("# Guide\n\n## Rules\n\nName the basis of every ratio.\nThe cause: Billing reminder flow, not timing. Consistent with the Sep 14 scale-up.\nZebra pelican line stays.\n")
    return fake_root


def _brain(root, monkeypatch, tmp_path, ids=None):
    if ids is None:
        monkeypatch.delenv(K.ENV_HOLDOUT, raising=False)
    else:
        f = tmp_path / "holdout.json"
        f.write_text(json.dumps(ids))
        monkeypatch.setenv(K.ENV_HOLDOUT, str(f))
    return T.Brain(root=root)


def test_unset_changes_nothing(root, monkeypatch, tmp_path):
    b = _brain(root, monkeypatch, tmp_path)
    assert {m["id"] for m in b.past_questions("billing reminder repeat revenue")["matches"]} >= {7, 9}
    paths = {h.get("path") for h in b.search("billing reminder flow")["hits"]}
    assert "docs/questions/ledger_marketing.md" in paths and "docs/GUIDE.md" in paths


def test_holdout_drops_items_duplicates_and_leaking_docs(root, monkeypatch, tmp_path):
    b = _brain(root, monkeypatch, tmp_path, ids=[7])
    ids = {m["id"] for m in b.past_questions("billing reminder repeat revenue")["matches"]}
    assert 7 not in ids and 9 not in ids                      # 9 repeats item 7's answer
    assert 10 in {m["id"] for m in b.past_questions("pelican")["matches"]}   # others stay
    hits = b.search("billing reminder flow timing")["hits"]
    assert not [h for h in hits if str(h.get("path", "")).startswith("docs/questions/") and h["kind"] == "doc"]
    assert not [h for h in hits if "Sep 14 scale-up" in h["snippet"]]
    assert [h for h in b.search("zebra pelican")["hits"] if h.get("path") == "docs/GUIDE.md"]   # the clean lines of the same doc remain
    assert [h for h in b.search("name the basis ratio")["hits"] if h.get("path") == "docs/GUIDE.md"]


def test_holdout_file_formats(tmp_path):
    f = tmp_path / "ids.txt"
    f.write_text("# comment\nT_1-2\nL2-9\n")
    assert K.read_holdout_ids(f) == {"T_1-2", "L2-9"}
    g = tmp_path / "ids.json"
    g.write_text(json.dumps({"source_item_ids": ["a", 3]}))
    assert K.read_holdout_ids(g) == {"a", "3"}
