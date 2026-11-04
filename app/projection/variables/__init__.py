"""Module `variables` (page Variables): every driver of the model in one place, with the Lonvera Projections sheet's value as its default.

The registry (spec.py, specs_<module>.py) names each variable; access.py reads and writes it through the owning module's own mechanism, so every edit
is audited, versioned and restorable by the core. This module has no engine step of its own: it is a settings surface over the seven calculating modules.
"""
from __future__ import annotations

from ..interfaces import Module
from .api import bp

MODULE = Module(key="variables", label="Variables", group="Settings", blueprint=bp, ui="variables.js")
