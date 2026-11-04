"""Shared setup for a combined run of every projection test directory (`pytest tests/projection`).

Three things only work in one process because of this file:

1. `from conftest import X`. Several test directories keep their helpers in their own conftest.py and import them as `conftest`; in one run the
   name `conftest` can only be one module. A router takes the name over and resolves every `conftest` import to the conftest.py of the importing
   file's own directory, so each directory sees its own helpers whatever ran before it. (A single directory run is unaffected: the router answers
   with that directory's conftest.)
2. Run order: the UI tests (one session-wide Playwright session) run last; see pytest_collection_modifyitems.
3. The projection registry. Some directories point `registry.PACKAGE` at a fake package; the registry is process-wide, so a patch that outlives
   its directory (a session-scoped fixture lives until the end of the run) made later directories build their apps without the real modules. Every
   test starts on the real package unless it opts in (the hook below).
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_BY_DIR: dict[Path, types.ModuleType] = {}          # directory -> its loaded conftest module


class _ConftestRouter(types.ModuleType):
    """Stands in for sys.modules['conftest']; attribute access is answered by the importing file's own directory conftest."""

    def __getattr__(self, name: str):
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        return getattr(self._target(), name)

    def _target(self) -> types.ModuleType:
        frame = sys._getframe(2)
        while frame is not None:                    # skip the import machinery's own frames
            mod = frame.f_globals.get("__name__", "")
            if not (mod.startswith("importlib") or mod.startswith("_frozen_importlib")) and frame.f_code.co_filename != __file__:
                break
            frame = frame.f_back
        path = Path(frame.f_code.co_filename).resolve() if frame is not None else _HERE
        for d in [path.parent, *path.parent.parents]:
            if d in _BY_DIR:
                return _BY_DIR[d]
        raise AttributeError(f"no conftest.py registered above {path}")


_ROUTER = _ConftestRouter("conftest")


def _install_router() -> None:
    sys.modules["conftest"] = _ROUTER


def pytest_plugin_registered(plugin) -> None:
    """Called after pytest imports each conftest.py: remember it by directory and keep the router in place of `conftest`."""
    path = getattr(plugin, "__file__", None)
    if isinstance(plugin, types.ModuleType) and path and Path(path).name == "conftest.py" and plugin is not _ROUTER:
        _BY_DIR[Path(path).resolve().parent] = plugin
        _install_router()


_REAL = "app.projection"
_OPTED_IN: dict[str, str] = {}                      # package a session fixture named `only_this_module` pointed the registry at


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item) -> None:
    """Runs before ANY fixture of the test is set up (session-scoped ones included): the app fixtures of a directory are created lazily at its
    first test, and must see the real projection package. A directory that needs a fake package patches registry.PACKAGE itself, per test
    (monkeypatch) or for its session through a fixture named `only_this_module`; tests that use that fixture get that package back."""
    from app.projection import registry
    if "only_this_module" in item.fixturenames:
        if "pkg" in _OPTED_IN:
            registry.PACKAGE = _OPTED_IN["pkg"]
    elif registry.PACKAGE != _REAL:
        registry.PACKAGE = _REAL


@pytest.hookimpl(trylast=True)
def pytest_runtest_call(item) -> None:
    from app.projection import registry
    if "only_this_module" in item.fixturenames and registry.PACKAGE != _REAL:
        _OPTED_IN["pkg"] = registry.PACKAGE


def pytest_collection_modifyitems(config, items) -> None:
    """The UI tests keep ONE Playwright session open from their first test to the end of the run (ui/conftest.py: a session-scoped `browser`), and Playwright's
    sync API cannot start a second session in the same thread while one is open. Every other directory starts its own, so the UI tests run last."""
    ui = _HERE / "ui"
    items.sort(key=lambda it: 1 if ui in Path(str(it.path)).resolve().parents else 0)          # a stable sort: the order inside each group is unchanged
