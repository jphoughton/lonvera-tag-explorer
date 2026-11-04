"""A read-only draft on top of proj_series, for what-if runs that must write nothing.

    with overlay.active(Draft(slug, base_slug, {(line_key, month): (value | None, kind)})):
        res = engine.run(scn, conn)            # every module that reads the scenario's series through series.fetch_cells / get_series sees the draft

The engine and its modules are untouched: `series.fetch_cells` (which `series.get_series` calls) is wrapped once, and the wrapper is the identity unless a
draft is active in the CURRENT context (a ContextVar, so another request thread never sees it). A draft cell with a value replaces the scenario's cell
for that month; a draft cell with value None deletes the scenario's OWN cell, so the month falls back to the base scenario's cell, exactly as the real
`PUT /series` with value null does. Nothing is written: the run works on the app's read-only connection.
"""
from __future__ import annotations

import contextlib
import contextvars
import functools
import threading
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable

from .. import scenarios, series
from ..interfaces import Scenario

Cell = tuple[float | None, str]                       # (value or None = delete the scenario's own cell, kind)


@dataclass(frozen=True)
class Draft:
    slug: str                                         # the scenario the draft applies to
    base_slug: str | None = None                      # where a deleted cell falls back to
    cells: dict[tuple[str, date], Cell] = field(default_factory=dict)


_DRAFT: contextvars.ContextVar[Draft | None] = contextvars.ContextVar("modeler_draft", default=None)
_lock = threading.Lock()
_original = None
NOTE = "draft"


def _slug_of(conn, scn: Any) -> str | None:
    if isinstance(scn, Scenario):
        return scn.slug
    if isinstance(scn, str):
        return scn
    if scn is None:
        return scenarios.default_slug(conn)
    return None                                       # a numeric id: not used by the engine's modules


def _wanted(line_key: str, keys: set[str] | None, prefix: str | None) -> bool:
    if keys is not None:
        return line_key in keys
    return prefix is None or line_key.startswith(prefix)


def apply_draft(conn, draft: Draft, cells: list[dict], keys, prefix, from_, to) -> list[dict]:
    """The winning cells of `draft.slug` after the draft: `cells` is what the unwrapped fetch returned for the same filters."""
    by = {(c["line_key"], c["month"]): c for c in cells}
    keyset = set(keys) if keys is not None else None
    for (line_key, month), (value, kind) in draft.cells.items():
        if not _wanted(line_key, keyset, prefix) or (from_ is not None and month < from_) or (to is not None and month > to):
            continue
        if value is not None:
            by[(line_key, month)] = {"line_key": line_key, "month": month, "value": float(value), "kind": kind, "note": NOTE, "origin": draft.slug}
            continue
        held = by.get((line_key, month))
        if held is None or held["origin"] != draft.slug:
            continue                                  # the scenario holds no cell of its own there: deleting changes nothing
        fallback = _original(conn, draft.base_slug, [line_key], from_=month, to=month) if draft.base_slug else []
        if fallback:
            by[(line_key, month)] = fallback[0]
        else:
            del by[(line_key, month)]
    return list(by.values())


def _patched(conn, scn, keys: Iterable[str] | None = None, *, prefix: str | None = None, from_: date | None = None, to: date | None = None) -> list[dict]:
    keys = list(keys) if keys is not None else None
    cells = _original(conn, scn, keys, prefix=prefix, from_=from_, to=to)
    draft = _DRAFT.get()
    if draft is None or not draft.cells or _slug_of(conn, scn) != draft.slug:
        return cells
    return apply_draft(conn, draft, cells, keys, prefix, from_, to)


def install() -> None:
    """Wrap series.fetch_cells once (idempotent). The wrapper does nothing unless a draft is active."""
    global _original
    with _lock:
        if _original is not None and series.fetch_cells is _patched:
            return
        _original = getattr(series.fetch_cells, "_modeler_original", series.fetch_cells)
        functools.update_wrapper(_patched, _original)
        _patched._modeler_original = _original        # type: ignore[attr-defined]
        series.fetch_cells = _patched                 # type: ignore[assignment]


@contextlib.contextmanager
def active(draft: Draft):
    """Run the block with `draft` visible to the engine, in this context only."""
    install()
    token = _DRAFT.set(draft)
    try:
        yield draft
    finally:
        _DRAFT.reset(token)


def current() -> Draft | None:
    return _DRAFT.get()
