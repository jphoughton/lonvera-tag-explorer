"""Read helpers for the frozen Lonvera Projections sheet snapshot.

The snapshot is written by ``scripts/projection/snapshot.py`` into
``~/lonvera-tag-explorer-data/projection-snapshot-2026-11-03/`` (override with the
``PROJECTION_SNAPSHOT_DIR`` environment variable). Every tab has two files:

    <gid>__<slug>.formulas.json   valueRenderOption=FORMULA
    <gid>__<slug>.values.json     valueRenderOption=UNFORMATTED_VALUE

Dates are Sheets serial numbers (use ``serial_to_date``). Rows and columns are
1-based everywhere in this module, matching A1 notation. Empty cells come back as
``None`` from ``cell`` / ``row``.

Tabs captured in ``head_tail`` mode only hold the first rows (``head``) and the last
rows (``tail``); asking for a row that was not captured raises ``KeyError``.

Usage::

    from snapshot_read import cell, row, load, col_letter, col_index, serial_to_date
    cell("P&L", "W22")                    # Sep-2026 total revenue
    row("P&L", 8, "C", "BJ")              # month header serials
    serial_to_date(row("P&L", 8, "C")[0])  # datetime.date(2025, 1, 1)
"""
from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

DEFAULT_DIR = Path("~/lonvera-tag-explorer-data/projection-snapshot-2026-11-03").expanduser()
ENV_VAR = "PROJECTION_SNAPSHOT_DIR"
KINDS = ("values", "formulas")
_EPOCH = date(1899, 12, 30)
_REF_RE = re.compile(r"^\$?([A-Za-z]{1,3})\$?(\d+)$")


def snapshot_dir() -> Path:
    """Directory holding the snapshot (env PROJECTION_SNAPSHOT_DIR overrides the default)."""
    override = os.environ.get(ENV_VAR)
    return Path(override).expanduser() if override else DEFAULT_DIR


# ---------------------------------------------------------------- A1 helpers

def col_letter(i: int) -> str:
    """1-based column index -> letters (1 -> 'A', 27 -> 'AA', 62 -> 'BJ')."""
    if i < 1:
        raise ValueError(f"column index must be >= 1, got {i}")
    out = ""
    while i:
        i, rem = divmod(i - 1, 26)
        out = chr(65 + rem) + out
    return out


def col_index(letters: str) -> int:
    """Column letters -> 1-based index ('A' -> 1, 'BJ' -> 62)."""
    letters = letters.strip().upper().replace("$", "")
    if not letters or not letters.isalpha():
        raise ValueError(f"bad column letters: {letters!r}")
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n


def parse_ref(ref: str) -> tuple[int, int]:
    """'W17' -> (row=17, col=23)."""
    m = _REF_RE.match(ref.strip())
    if not m:
        raise ValueError(f"bad A1 reference: {ref!r}")
    return int(m.group(2)), col_index(m.group(1))


def _col(c: int | str) -> int:
    return col_index(c) if isinstance(c, str) else int(c)


def serial_to_date(n: float | int) -> date:
    """Sheets serial number -> date (fractions of a day are dropped)."""
    return _EPOCH + timedelta(days=int(n // 1))


def serial_to_datetime(n: float | int) -> datetime:
    """Sheets serial number -> datetime (keeps the time of day)."""
    return datetime(1899, 12, 30) + timedelta(days=float(n))


# ---------------------------------------------------------------- loading

def _read_json(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


@lru_cache(maxsize=8)
def _manifest(dir_str: str) -> dict:
    return _read_json(Path(dir_str) / "_manifest.json")


def manifest() -> dict:
    """The parsed ``_manifest.json`` of the active snapshot."""
    return _manifest(str(snapshot_dir()))


def tabs() -> list[dict]:
    """Manifest entries for every tab (including skipped ones)."""
    return manifest()["tabs"]


def resolve(title: str | int) -> dict:
    """Manifest entry for a tab, matched by gid, exact title, stripped/case-insensitive
    title, slug or file stem."""
    entries = tabs()
    if isinstance(title, int) or (isinstance(title, str) and title.isdigit()):
        for e in entries:
            if e["gid"] == int(title):
                return e
    for e in entries:
        if e["title"] == title:
            return e
    key = str(title).strip().casefold()
    for e in entries:
        if e["title"].strip().casefold() == key or e.get("slug") == key or e.get("stem") == title:
            return e
    raise KeyError(f"tab not in snapshot manifest: {title!r}")


@lru_cache(maxsize=64)
def _load(dir_str: str, stem: str, kind: str) -> dict:
    return _read_json(Path(dir_str) / f"{stem}.{kind}.json")


def load(title: str | int, kind: str = "values") -> dict:
    """Parsed snapshot file for a tab: ``{"title","gid","range","rows","cols","values"}``
    (``head``/``tail``/``tail_start_row`` instead of ``values`` for head_tail tabs).
    The returned dict is shared (cached); do not mutate it."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}, got {kind!r}")
    entry = resolve(title)
    if entry["mode"] == "skipped":
        raise KeyError(f"tab {entry['title']!r} was skipped in the snapshot ({entry.get('reason')})")
    return _load(str(snapshot_dir()), entry["stem"], kind)


def _clean(v: Any) -> Any:
    return None if v == "" else v


def _grid_row(obj: dict, r: int) -> list | None:
    """Stored row r (1-based) of a loaded tab, or None when the row is outside the
    used range. Raises KeyError for a head_tail row that was not captured."""
    if "values" in obj:
        vals = obj["values"]
        return vals[r - 1] if 1 <= r <= len(vals) else None
    head, tail, tail_start = obj["head"], obj["tail"], obj["tail_start_row"]
    if 1 <= r <= len(head):
        return head[r - 1]
    if tail and tail_start <= r < tail_start + len(tail):
        return tail[r - tail_start]
    if r > obj["rows"]:
        return None
    raise KeyError(f"row {r} of {obj['title']!r} is not in the head/tail capture "
                   f"(head rows 1-{len(head)}, tail rows {tail_start}-{tail_start + len(tail) - 1})")


def cell(title: str | int, ref: str, kind: str = "values") -> Any:
    """Value of one cell ('W17'); None when the cell is empty or outside the used range."""
    r, c = parse_ref(ref)
    grid_row = _grid_row(load(title, kind), r)
    if grid_row is None or c > len(grid_row):
        return None
    return _clean(grid_row[c - 1])


def row(title: str | int, r: int, c0: int | str = 1, c1: int | str | None = None,
        kind: str = "values") -> list:
    """Values of row r from column c0 through c1 inclusive (letters or 1-based ints).
    ``c1=None`` runs to the end of the tab's used width. Empty cells are None."""
    obj = load(title, kind)
    lo = _col(c0)
    hi = _col(c1) if c1 is not None else obj["cols"]
    grid_row = _grid_row(obj, r) or []
    out = []
    for c in range(lo, hi + 1):
        out.append(_clean(grid_row[c - 1]) if c <= len(grid_row) else None)
    return out


def col(title: str | int, c: int | str, r0: int = 1, r1: int | None = None,
        kind: str = "values") -> list:
    """Values of column c from row r0 through r1 inclusive (``r1=None`` = last used row)."""
    obj = load(title, kind)
    ci = _col(c)
    hi = r1 if r1 is not None else obj["rows"]
    out = []
    for r in range(r0, hi + 1):
        grid_row = _grid_row(obj, r) or []
        out.append(_clean(grid_row[ci - 1]) if ci <= len(grid_row) else None)
    return out


def block(title: str | int, r0: int, r1: int, c0: int | str = 1, c1: int | str | None = None,
          kind: str = "values") -> list[list]:
    """Rectangular block of cells (rows r0..r1, columns c0..c1, all inclusive)."""
    return [row(title, r, c0, c1, kind) for r in range(r0, r1 + 1)]
