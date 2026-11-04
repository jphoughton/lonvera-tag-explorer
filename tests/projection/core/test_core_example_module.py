"""The example module in docs/projection/MODULE_core.md must run as written (imports made absolute for the fake package)."""
from __future__ import annotations

import re

from conftest import ROOT, login
from core_helpers import P, item, put


def doc_example() -> str:
    text = (ROOT / "docs" / "projection" / "MODULE_core.md").read_text()
    block = re.search(r"## How a module plugs in.*?```python\n(.*?)```", text, re.S).group(1)
    return (block.replace("from ... import db", "from app import db").replace("from .. import engine, series", "from app.projection import engine, series")
            .replace("from ..interfaces", "from app.projection.interfaces"))


def test_documented_example_module_builds_computes_and_serves_a_grid(app_factory, pkg_factory):
    pkg_factory({"drivers/__init__.py": doc_example()})
    c = login(app_factory().test_client())
    put(c, "current", item(key="drivers.spend_total", month="2026-11-01", value=9500.0), item(key="drivers.spend_total", month="2026-12-01", value=19000.0))
    assert {m["key"]: m for m in c.get(f"{P}/modules").get_json()["modules"]}["drivers"] == {
        "key": "drivers", "label": "Spend plan", "group": "P1", "built": True, "ui": "drivers.js"}
    t = c.get(f"{P}/drivers/table?scenario=current").get_json()
    assert set(t) == {"months", "status", "rows", "captions"} and len(t["months"]) == 60
    spend, nc = t["rows"]
    assert spend["input"] is True and spend["values"][22] == 9500.0 and spend["values"][0] is None
    assert nc["kind"] == "total" and nc["values"][22] == 9500.0 / 95.0 * 69.0 and nc["values"][23] == 19000.0 / 95.0 * 69.0
    c.patch(f"{P}/scenarios/current", json={"params": {"drivers": {"nc_cpa": 190.0}}})
    assert c.get(f"{P}/drivers/table").get_json()["rows"][1]["values"][22] == 9500.0 / 190.0 * 69.0
    mod = c.get(f"{P}/run").get_json()["modules"]["drivers"]
    assert mod["built"] is True and mod["stub"] is False and "error" not in mod
