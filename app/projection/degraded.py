"""Degraded modules must be loud: when a module fails, everything that depends on it answers 503 `upstream_degraded`, never 200 with zeros.

    DEPENDS             module -> the modules whose results it reads (engine.run degrades a module when any of them is degraded)
    closure(key)        the module and everything upstream of it
    ensure_ok(res, key) raise UpstreamDegraded (503 {error:'upstream_degraded', modules:[{module,error}]}) if `key` or anything upstream is degraded

The engine already hands dependents a missing upstream (None) and records why in `EngineResult.errors`. Module endpoints can call `ensure_ok(res, "<key>")`
after `engine.run`; independent of that, a GET under /api/projection/<module>/ is answered 503 when the engine degraded the module or anything upstream of it
during the request (see api.install_guards), so a page that forgot to check cannot show zeros.
"""
from __future__ import annotations

from typing import Any

from flask import jsonify

from ..queries import ApiError

DEPENDS: dict[str, tuple[str, ...]] = {
    "cohorts": ("drivers",),
    "pnl": ("drivers", "cohorts"),
    "inventory_supply": ("inventory_demand",),
    "financing": ("pnl", "inventory_supply"),
    "pacing": ("pnl", "drivers", "cohorts"),
}


class UpstreamDegraded(ApiError):
    def __init__(self, modules: list[dict]):
        super().__init__(503, "upstream_degraded")
        self.modules = modules


def closure(key: str) -> list[str]:
    """`key` followed by every module it reads, transitively (dependencies first-listed order, no repeats)."""
    out: list[str] = []
    todo = [key]
    while todo:
        k = todo.pop(0)
        if k not in out:
            out.append(k)
            todo.extend(DEPENDS.get(k, ()))
    return out


def degraded_in(errors: dict[str, str], key: str) -> list[dict]:
    """Degraded modules in key's closure, root causes first (the module that failed comes before the ones that only read it)."""
    return [{"module": m, "error": errors[m]} for m in reversed(closure(key)) if m in errors]


def ensure_ok(res: Any, key: str) -> None:
    """Raise UpstreamDegraded when the engine result `res` has `key` or anything it reads degraded."""
    bad = degraded_in(getattr(res, "errors", {}) or {}, key)
    if bad:
        raise UpstreamDegraded(bad)


def response(modules: list[dict]):
    resp = jsonify(error="upstream_degraded", modules=modules)
    resp.status_code = 503
    return resp


def register(app) -> None:
    app.register_error_handler(UpstreamDegraded, lambda e: response(e.modules))
