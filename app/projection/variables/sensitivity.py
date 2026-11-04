"""What moves the plan: every scalar number moved down and up by 10% (one at a time), the whole engine rerun, and the change in FY27 net operating income,
FY27 revenue and the lowest month-end cash read off the headline. Results are cached per scenario version and day."""
from __future__ import annotations

import re
import time
from datetime import date
from typing import Any

from .. import engine
from . import access as A, registry

YEAR = "2027"
STEP = 0.10
CAP_SECONDS = 15.0
TTL_SECONDS = 600
_cache: dict[tuple, tuple[float, dict]] = {}


def short_label(label: str) -> str:
    """The part of a label before the colon, without a closing parenthesis and without a trailing 'for the ...' clause: the name the headline sentence uses."""
    t = label.split(":")[0]
    t = re.sub(r"\s*\([^)]*\)\s*$", "", t)
    t = re.split(r"\s+(?:for the|in the|per month)\s+", t)[0]
    return t.strip()


def _headline(run) -> dict:
    h = run.summary().get("headline") or {}
    fy = (h.get("fy") or {}).get(YEAR) or {}
    cash = (h.get("lowest_ending_cash") or {}).get("value")
    return {"noi": fy.get("noi"), "revenue": fy.get("revenue"), "cash": cash}


def candidates(ctx: A.Context) -> list[tuple[Any, Any]]:
    out = []
    for s in registry.all_specs():
        if not (s.sens and s.numeric and s.kind == "scalar" and s.binding in ("param", "supply", "pnl_driver") and s.status != "MISSING" and s.probe == "engine"):
            continue
        if (s.extra or {}).get("readonly"):
            continue
        r = A.resolve(s, ctx)
        v = r.value
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v == 0:
            continue
        out.append((s, float(v)))
    return out


def _sides(spec, v: float) -> tuple[float | None, float | None]:
    dn, up = v * (1 - STEP), v * (1 + STEP)
    if spec.value_type == "integer":
        iv = int(v)
        dn, up = min(int(round(dn)), iv - 1), max(int(round(up)), iv + 1)
    lo, hi = spec.lo, spec.hi
    if lo is not None and dn < lo:
        dn = None
    if hi is not None and up is not None and up > hi:
        up = None
    return dn, up


def _delta(a: dict, b: dict) -> dict:
    return {k: (None if a.get(k) is None or b.get(k) is None else b[k] - a[k]) for k in ("noi", "revenue", "cash")}


def run(conn, slug: str, *, cap: float = CAP_SECONDS, use_cache: bool = True) -> dict:
    ctx = A.Context(conn, slug)
    key = (slug, ctx.scn.version, date.today().isoformat())
    hit = _cache.get(key)
    if use_cache and hit and time.time() - hit[0] < TTL_SECONDS:
        return hit[1]
    t0 = time.time()
    base = _headline(ctx.run)
    rows, tested, truncated = [], 0, False
    for spec, v in candidates(ctx):
        if time.time() - t0 > cap:
            truncated = True
            break
        dn, up = _sides(spec, v)
        entry: dict[str, Any] = {"id": spec.id, "label": spec.label, "short": short_label(spec.label), "module": spec.module, "unit": spec.unit, "value_type": spec.value_type,
                                 "value": v, "down": None, "up": None}
        for side, val in (("down", dn), ("up", up)):
            if val is None:
                continue
            try:
                scn2 = A.scenario_with(ctx, spec, val)
                entry[side] = {"value": val, **_delta(base, _headline(engine.run(scn2, conn)))}
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
        tested += 1
        swing = max([abs(entry[s][k]) for s in ("down", "up") if entry[s] for k in ("noi", "revenue", "cash") if entry[s].get(k) is not None] or [0.0])
        noi_swing = max([abs(entry[s]["noi"]) for s in ("down", "up") if entry[s] and entry[s].get("noi") is not None] or [0.0])
        if swing > 0.5:
            entry["noi_swing"], entry["swing"] = noi_swing, swing
            rows.append(entry)
    rows.sort(key=lambda r: (-r["noi_swing"], -r["swing"]))
    out = {"scenario": slug, "version": ctx.scn.version, "year": int(YEAR), "step": STEP, "base": base, "tested": tested, "moving": len(rows), "truncated": truncated,
           "seconds": round(time.time() - t0, 2), "rows": rows}
    _cache[key] = (time.time(), out)
    return out


def clear() -> None:
    _cache.clear()
