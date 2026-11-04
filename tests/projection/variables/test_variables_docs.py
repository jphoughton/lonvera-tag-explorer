"""The documents match the code: VARIABLES.md is generated from the registry, every ticket a spec names exists, every tool runs."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from app.projection.variables import registry as R

ROOT = Path(__file__).resolve().parents[3]
DOCS = ROOT / "docs" / "projection"


def test_variables_md_is_up_to_date():
    p = subprocess.run([sys.executable, str(Path(__file__).parent / "tools" / "render_registry.py"), "--check"], cwd=ROOT, env={"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin"})
    assert p.returncode == 0, "run: python tests/projection/variables/tools/render_registry.py"


def test_every_ticket_a_spec_names_has_a_heading():
    for s in R.all_specs():
        if not s.ticket:
            continue
        module, slug = s.ticket.split("#")
        f = DOCS / f"TICKETS_{module}.md"
        assert f.exists(), s.id
        assert re.search(rf"^### {re.escape(slug)}:", f.read_text(), re.M), f"{s.id}: {s.ticket}"


def test_every_ticket_heading_is_used_or_cross_referenced():
    named = {s.ticket for s in R.all_specs() if s.ticket}
    from app.projection.variables.spec import MODULES
    for f in DOCS.glob("TICKETS_*.md"):
        mod = f.stem.removeprefix("TICKETS_")
        if mod not in MODULES:
            continue                                         # another module's or agent's tickets
        for slug in re.findall(r"^### ([a-z0-9-]+):", f.read_text(), re.M):
            same_slug = any(t.split("#")[1] == slug for t in named)
            assert f"{mod}#{slug}" in named or same_slug or slug in ("unused-defaults-today", "view-only-levers", "reorder-default-literal", "tax-payment-months", "channel-share-sum"), (mod, slug)


def test_module_doc_exists_and_mentions_the_endpoints():
    t = (DOCS / "MODULE_variables.md").read_text()
    for needle in ("/api/projection/variables", "sensitivity", "VARIABLES.md", "specs_<module>.py"):
        assert needle in t


def test_the_code_audit_tool_runs_on_every_module():
    p = subprocess.run([sys.executable, str(Path(__file__).parent / "tools" / "code_literals.py")], cwd=ROOT, capture_output=True, text=True)
    assert p.returncode == 0 and "cohorts/engine.py" in p.stdout and "financing/params.py" in p.stdout
