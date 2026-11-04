"""Module discovery: every sub-package of app.projection that exports MODULE is a projection module.

A missing, broken or malformed module never takes the app down: it is logged and listed as built=False (the shell shows a "not built yet" card,
the engine uses a fixture stub for it). A module's blueprint is mounted at /api/projection/<key with dashes>.
"""
from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil
from dataclasses import dataclass
from typing import Any

from .interfaces import MODULE_CATALOG, Module

log = logging.getLogger("tagexp.projection")

PACKAGE = "app.projection"                       # tests point this at a fake package
API_PREFIX = "/api/projection"

_cache: dict[str, dict[str, "ModuleInfo"]] = {}


@dataclass
class ModuleInfo:
    key: str
    module: Module | None = None
    error: str | None = None

    @property
    def built(self) -> bool:
        return self.module is not None


def _load(package: str, name: str) -> ModuleInfo:
    info = ModuleInfo(key=name)
    try:
        mod = importlib.import_module(f"{package}.{name}")
        m: Any = getattr(mod, "MODULE", None)
        if m is None:
            info.error = "package does not export MODULE"
        elif getattr(m, "key", None) != name:
            info.error = f"MODULE.key {getattr(m, 'key', None)!r} does not match the package name {name!r}"
        elif callable(getattr(m, "label", None)) or callable(getattr(m, "group", None)):
            info.error = "Module(...) fields are out of order: pass keyword arguments (key=, load_inputs=, compute=, blueprint=, ui=)"
        else:
            info.module = m
    except Exception as e:                       # a syntax error or a failing import in a module must not break the app
        info.error = f"{type(e).__name__}: {e}"
    if info.error:
        log.warning("projection module %s not available: %s", name, info.error)
    return info


def discover(package: str | None = None, refresh: bool = False) -> dict[str, ModuleInfo]:
    """{key: ModuleInfo} for every sub-package of `package` (default: app.projection). Cached per package."""
    package = package or PACKAGE
    if not refresh and package in _cache:
        return _cache[package]
    found: dict[str, ModuleInfo] = {}
    try:
        pkg = importlib.import_module(package)
        for item in pkgutil.iter_modules(getattr(pkg, "__path__", [])):
            if item.ispkg and not item.name.startswith("_"):
                found[item.name] = _load(package, item.name)
    except Exception as e:
        log.warning("projection module discovery failed for %s: %s", package, e)
    _cache[package] = found
    return found


def get_module(key: str, package: str | None = None) -> Module | None:
    info = discover(package).get(key)
    return info.module if info else None


def listing(package: str | None = None) -> list[dict]:
    """[{key,label,group,built,ui}] for the seven catalog modules, in nav order."""
    found = discover(package)
    out = []
    for key, (label, group) in MODULE_CATALOG.items():
        info = found.get(key)
        built = bool(info and info.built)
        out.append({"key": key, "label": label, "group": group, "built": built, "ui": (info.module.ui or None) if built else None})
    return out


def register_modules(app, package: str | None = None) -> list[str]:
    """Mount every built module's blueprint. Returns the keys mounted."""
    mounted = []
    for key, info in discover(package).items():
        bp = info.module.blueprint if info.built else None
        if bp is None:
            continue
        try:
            app.register_blueprint(bp, url_prefix=f"{API_PREFIX}/{key.replace('_', '-')}")
            mounted.append(key)
        except Exception as e:
            info.error = f"blueprint not registered: {type(e).__name__}: {e}"
            log.warning("projection module %s: %s", key, info.error)
    return mounted


# ---------------------------------------------------------------------------------------------------------------- module hooks

def hook(key: str, name: str, package: str | None = None):
    """A module's optional hook function: an attribute of its MODULE, else of its package (app.projection.<key>) or its params / validation submodule, else None."""
    package = package or PACKAGE
    info = discover(package).get(key)
    if info is None or not info.built:
        return None
    fn = getattr(info.module, name, None)
    for where in ("", ".params", ".validation"):    # the MODULE, the package, then the module's params / validation submodule
        if callable(fn):
            break
        try:
            fn = getattr(importlib.import_module(f"{package}.{key}{where}"), name, None)
        except Exception:                           # a missing submodule, or an import problem discovery already logged
            fn = None
    return fn if callable(fn) else None


def hooks(name: str, package: str | None = None) -> list[tuple[str, Any]]:
    """[(module key, function)] for every built module that defines the hook."""
    return [(k, fn) for k in discover(package) if (fn := hook(k, name, package)) is not None]


def call_hook(fn, **ctx):
    """Call a module hook with the context values its parameters are named for (conn, scenario, namespace, params, items, slug, ...), so a module may
    declare only what it needs, in any order: (params, conn=None), (conn, scenario, items), (conn, slug, to_version, new_version, actor=None)."""
    ps = inspect.signature(fn).parameters
    kwargs = {}
    for n, p in ps.items():
        if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            continue
        if n in ctx:
            kwargs[n] = ctx[n]
        elif p.default is p.empty:
            raise TypeError(f"module hook {getattr(fn, '__module__', '?')}.{getattr(fn, '__name__', '?')} needs {n!r}; the core can pass {sorted(ctx)}")
    return fn(**kwargs)


def run_hook(fn, **ctx):
    """call_hook inside a savepoint when a connection is given: a module whose tables are not installed in this database (a partial schema, as in per-module
    test databases) is skipped with a note instead of aborting the caller's transaction. Any other error propagates and rolls the whole write back."""
    import psycopg
    conn = ctx.get("conn")
    try:
        if conn is None:
            return call_hook(fn, **ctx)
        with conn.transaction():
            return call_hook(fn, **ctx)
    except psycopg.errors.UndefinedTable:
        log.warning("module hook %s skipped: its tables are not installed in this database", getattr(fn, "__module__", "?"))
        return {"skipped": "the module's tables are not installed in this database"}
