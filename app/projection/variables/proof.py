"""Proof that a variable is wired: a stable fingerprint of every engine output, so a test (and the sensitivity run) can see what a change moved."""
from __future__ import annotations

import dataclasses
from datetime import date, datetime
from typing import Any

_MAX_ITEMS = 400


def _summ(obj: Any, depth: int = 0) -> Any:
    if obj is None or isinstance(obj, (bool, str)):
        return obj
    if isinstance(obj, (int, float)):
        return round(float(obj), 4)
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if depth > 3:
        return None
    if isinstance(obj, dict):
        vals = list(obj.values())
        if vals and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals):
            return round(sum(float(v) for v in vals), 4), len(vals)               # a monthly series or a flat map of numbers
        return {str(k): _summ(v, depth + 1) for k, v in list(obj.items())[:_MAX_ITEMS]}
    if isinstance(obj, (list, tuple)):
        if obj and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in obj):
            return round(sum(float(v) for v in obj), 4), len(obj)
        return [_summ(v, depth + 1) for v in list(obj)[:_MAX_ITEMS]]
    if dataclasses.is_dataclass(obj) or hasattr(obj, "__dict__"):
        d = {k: v for k, v in vars(obj).items() if not k.startswith("_")}
        return {k: _summ(v, depth + 1) for k, v in d.items()}
    return str(obj)


def fingerprint(run) -> dict[str, Any]:
    """{module key: summary of its result} for an EngineResult."""
    return {k: _summ(v) for k, v in run.results.items() if v is not None}


def diff(a: dict, b: dict, path: str = "") -> list[str]:
    """Paths of the outputs that differ between two fingerprints."""
    out: list[str] = []
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            out += diff(a.get(k), b.get(k), f"{path}.{k}" if path else str(k))
        return out
    if isinstance(a, list) and isinstance(b, list):
        for i, (x, y) in enumerate(zip(a, b)):
            out += diff(x, y, f"{path}[{i}]")
        if len(a) != len(b):
            out.append(path + ".len")
        return out
    if isinstance(a, tuple) and isinstance(b, tuple):
        return [path] if a != b else []
    return [path] if a != b else []
