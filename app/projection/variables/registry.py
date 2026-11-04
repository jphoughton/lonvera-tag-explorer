"""The registry: every module's VariableSpec list, validated as a whole (unique ids, one owner per sheet value), with lookups the API and the tests use."""
from __future__ import annotations

import importlib
from functools import lru_cache

from .spec import MODULE_LABELS, MODULES, VariableSpec

_FILES = {m: f"specs_{m}" for m in MODULES}


@lru_cache(maxsize=1)
def all_specs() -> tuple[VariableSpec, ...]:
    out: list[VariableSpec] = []
    for m in MODULES:
        mod = importlib.import_module(f"{__package__}.{_FILES[m]}")
        out.extend(mod.specs())
    ids = [s.id for s in out]
    dup = {i for i in ids if ids.count(i) > 1}
    if dup:
        raise ValueError(f"duplicate variable ids: {sorted(dup)}")
    return tuple(out)


def by_id() -> dict[str, VariableSpec]:
    return {s.id: s for s in all_specs()}


def get(var_id: str) -> VariableSpec | None:
    return by_id().get(var_id)


def for_module(module: str) -> list[VariableSpec]:
    return [s for s in all_specs() if s.module == module]


def counts() -> dict:
    """{module: {status: n}, '_total': {...}} for the report and the page."""
    out: dict[str, dict[str, int]] = {m: {"IMPLEMENTED": 0, "PARTIAL": 0, "MISSING": 0} for m in MODULES}
    for s in all_specs():
        out[s.module][s.status] += 1
    out["_total"] = {k: sum(out[m][k] for m in MODULES) for k in ("IMPLEMENTED", "PARTIAL", "MISSING")}
    return out


def module_label(m: str) -> str:
    return MODULE_LABELS.get(m, m)
