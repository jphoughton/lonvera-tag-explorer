"""Engine: run the modules in dependency order for one scenario and collect their results.

    drivers -> cohorts -> pnl -> inventory_demand -> inventory_supply -> financing -> pacing

A module that is not built is replaced by its fixture stub (stubs.py) when the sheet snapshot is readable (development), so every module can be developed
against a complete upstream; with no snapshot an unbuilt module's result is None. A module that is built but fails (raises, returns nothing, or lacks an
upstream result it requires) is DEGRADED: its result is None, the reason is in `errors` / `degraded` and in the `/run` summary, and nothing stands in for
it (no stub, no zeros that look like data). `strict=True` raises instead. Calling convention for a real module (see docs/projection/MODULE_core.md):

    inputs = MODULE.load_inputs(conn, scenario)        # or load_inputs(conn); conn rows are dicts; read-only use
    result = MODULE.compute(scenario, inputs, **upstream)   # only the upstream results the function names are passed
"""
from __future__ import annotations

import inspect
import logging
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable

from flask import g, has_request_context

from . import clock, registry, scenarios, series as series_mod, stubs
from .degraded import DEPENDS
from .interfaces import MONTHS, Scenario

log = logging.getLogger("tagexp.projection")

ORDER = ("drivers", "cohorts", "pnl", "inventory_demand", "inventory_supply", "financing", "pacing")
RESULT_NAME = {"drivers": "drivers", "cohorts": "cohorts", "pnl": "pnl", "inventory_demand": "demand", "inventory_supply": "inventory",
               "financing": "financing", "pacing": "pacing"}
ALIAS = {v: k for k, v in RESULT_NAME.items()}


@dataclass
class EngineResult:
    scenario: Scenario
    actual_through: date | None
    results: dict[str, Any] = field(default_factory=dict)        # module key -> result dataclass (None for an unbuilt pacing)
    ms: dict[str, float] = field(default_factory=dict)
    built: dict[str, bool] = field(default_factory=dict)         # a real module package is present
    stubbed: dict[str, bool] = field(default_factory=dict)       # the result came from a fixture stub
    errors: dict[str, str] = field(default_factory=dict)         # module key -> why it is degraded (its result is None)

    @property
    def degraded(self) -> list[dict]:
        return [{"module": k, "error": self.errors[k]} for k in ORDER if k in self.errors]

    # convenient attribute access by result name
    def __getattr__(self, name: str):
        key = ALIAS.get(name)
        if key is not None and key in self.__dict__.get("results", {}):
            return self.results[key]
        raise AttributeError(name)

    def resolve(self, path: str) -> Any:
        """'drivers.nc_rev', 'pnl.lines.noi', 'demand.units.revamp|bull' -> the value. Dict keys may contain dots; 'a|b' addresses a tuple key."""
        head, *rest = path.split(".")
        key = ALIAS.get(head, head)
        if key not in self.results:
            raise KeyError(f"no engine result {head!r}")
        obj = self.results[key]
        while rest:
            if isinstance(obj, dict):
                for n in range(len(rest), 0, -1):
                    joined = ".".join(rest[:n])
                    cands = [joined] + ([tuple(joined.split("|"))] if "|" in joined else [])
                    hit = next((c for c in cands if c in obj), None)
                    if hit is not None:
                        obj, rest = obj[hit], rest[n:]
                        break
                else:
                    raise KeyError(f"{path}: no key {'.'.join(rest)!r}")
            else:
                if not hasattr(obj, rest[0]):
                    raise KeyError(f"{path}: no attribute {rest[0]!r}")
                obj, rest = getattr(obj, rest[0]), rest[1:]
        return obj

    def summary(self) -> dict:
        mods = {}
        for key in ORDER:
            m = {"built": bool(self.built.get(key)), "stub": bool(self.stubbed.get(key)), "ms": self.ms.get(key, 0.0)}
            if key in self.errors:
                m["error"] = self.errors[key]
            mods[key] = m
        out = {"scenario": self.scenario.slug, "version": self.scenario.version,
               "actual_through": self.actual_through.isoformat() if self.actual_through else None,
               "modules": mods, "degraded": self.degraded, "headline": _headline(self)}
        out.update(self._month_status())
        return out

    def _month_status(self) -> dict:
        """books_through / feeds_through / month_status (60 words) from whichever of drivers, pnl reports them; absent keys are omitted."""
        st: dict[str, Any] = {}
        for res in (self.results.get("pnl"), self.results.get("drivers")):
            for name in ("books_through", "feeds_through"):
                v = getattr(res, name, None)
                if v is not None and name not in st:
                    st[name] = v.isoformat() if hasattr(v, "isoformat") else v
            ms = getattr(res, "month_status", None)
            if ms and "month_status" not in st:
                st["month_status"] = [ms.get(m, "projected") for m in MONTHS]
        return st


# ---------------------------------------------------------------------------------------------------------------- calling modules

def _accepts_second_arg(fn: Callable) -> bool:
    try:
        ps = [p for p in inspect.signature(fn).parameters.values()]
    except (TypeError, ValueError):
        return True
    if any(p.kind is p.VAR_POSITIONAL for p in ps):
        return True
    return len([p for p in ps if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]) >= 2


def _load_inputs(mod, conn, scenario: Scenario) -> Any:
    fn = mod.load_inputs
    if fn is None:
        return None
    return fn(conn, scenario) if _accepts_second_arg(fn) else fn(conn)


def _compute(mod, scenario: Scenario, inputs: Any, upstream: dict[str, Any]) -> Any:
    fn = mod.compute
    ps = inspect.signature(fn).parameters
    if any(p.kind is p.VAR_KEYWORD for p in ps.values()):
        kw = dict(upstream)
    else:
        kw = {k: v for k, v in upstream.items() if k in ps}
    return fn(scenario, inputs, **kw)


UPSTREAM_NAMES = frozenset(RESULT_NAME.values()) - {"pacing"}


def _required_upstream(fn: Callable) -> list[str]:
    """Upstream results a compute function cannot run without: parameters named like an upstream result and given no default."""
    try:
        ps = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return []
    need = {("drivers" if n == "nc_rev" else n) for n, p in ps.items() if n in UPSTREAM_NAMES | {"nc_rev"} and p.default is p.empty}
    return sorted(need)


# ---------------------------------------------------------------------------------------------------------------- run

def run(scn: Scenario | str | None, conn, *, strict: bool = False, today: date | None = None, package: str | None = None) -> EngineResult:
    """Run every module for the scenario (Scenario, slug, or None = the default scenario)."""
    today = today or clock.today_pt()
    scenario = scn if isinstance(scn, Scenario) else scenarios.get_scenario(conn, scn)
    found = registry.discover(package)
    out = EngineResult(scenario=scenario, actual_through=None)
    upstream: dict[str, Any] = {}
    for key in ORDER:
        t0 = time.perf_counter()
        info = found.get(key)
        mod = info.module if info is not None and info.built else None
        out.built[key] = mod is not None
        result = None
        bad = [d for d in DEPENDS.get(key, ()) if d in out.errors]
        if bad:                                             # something it reads is degraded: its numbers would rest on nothing, so it is degraded too
            out.errors[key] = "upstream degraded: " + ", ".join(bad)
            if strict:
                raise RuntimeError(f"{key}: {out.errors[key]}")
        elif mod is not None and mod.compute is not None:
            missing = [n for n in _required_upstream(mod.compute) if n not in upstream]
            if missing:
                out.errors[key] = "upstream unavailable: " + ", ".join(missing)
                if strict:
                    raise RuntimeError(f"{key}: {out.errors[key]}")
            else:
                try:
                    result = _compute(mod, scenario, _load_inputs(mod, conn, scenario), upstream)
                    if result is None:
                        out.errors[key] = "the module returned no result"
                except Exception as e:
                    if strict:
                        raise
                    out.errors[key] = f"{type(e).__name__}: {e}"
                    log.exception("projection module %s failed; it is degraded (no result)", key)
                    try:
                        conn.rollback()                     # a failed query leaves the transaction aborted; the modules after it need a clean one
                    except Exception:
                        pass
        elif key in stubs.STUBS:                            # not built (or built without a compute step): the fixture stub, when there is a snapshot
            result = stubs.stub_for(key, conn, scenario, today, **upstream)
            if result is not None:
                out.stubbed[key] = True
        out.results[key] = result
        if result is not None:
            upstream[RESULT_NAME[key]] = result
            if key == "drivers":
                upstream["nc_rev"] = getattr(result, "nc_rev", {})
        out.ms[key] = round((time.perf_counter() - t0) * 1000, 2)
    drivers = out.results.get("drivers")
    out.actual_through = getattr(drivers, "actual_through", None) or series_mod.actual_through(conn, today)
    if has_request_context():                               # the response guard (api.install_guards) turns a degraded dependency into a 503
        g.setdefault("proj_degraded", {}).update(out.errors)
    return out


# ---------------------------------------------------------------------------------------------------------------- headline

_REVENUE_KEYS = ("total_revenue", "total_company_revenue", "revenue_total", "revenue")
_NOI_KEYS = ("noi", "net_operating_income")


def _pick(d: dict, keys: tuple[str, ...]):
    return next((d[k] for k in keys if k in d), None)


def _fy_value(pnl, year: int, keys: tuple[str, ...]):
    v = _pick(pnl.fy.get(year) or {}, keys)
    if v is not None:
        return v
    line = _pick(pnl.lines, keys)
    return sum(line.get(m, 0.0) for m in MONTHS if m.year == year) if line is not None else None


def _headline(r: EngineResult) -> dict:
    h: dict[str, Any] = {}
    try:
        pnl = r.results.get("pnl")
        if pnl is not None:
            h["fy"] = {str(y): {"revenue": _fy_value(pnl, y, _REVENUE_KEYS), "noi": _fy_value(pnl, y, _NOI_KEYS)}
                       for y in sorted({m.year for m in MONTHS})}
            h["ev"] = pnl.ev or {}
        cohorts = r.results.get("cohorts")
        if cohorts is not None:
            h["lifetime_multiple"] = cohorts.lifetime_multiple
        fin = r.results.get("financing")
        cash = fin.lines.get("ending_cash") if fin is not None else None
        if cash:
            month, value = min(cash.items(), key=lambda kv: kv[1])
            h["lowest_ending_cash"] = {"month": month.isoformat(), "value": value}
    except Exception:                                      # a module with an unexpected result shape must not break the summary
        log.exception("headline summary failed")
    return h
