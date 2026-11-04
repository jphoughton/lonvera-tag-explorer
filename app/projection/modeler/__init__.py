"""Module `modeler` (page Scenario modeler): a what-if workbench on top of the engine.

Change monthly ad spend, order value, cost per new customer and the retention model, and see yearly revenue and EBITDA move as you type. It has no engine
step of its own: it edits the Spend plan rows and the cohorts levers (so the Spend plan page and this page always agree) and runs the engine on a draft
that is never written (overlay.py). See docs/projection/MODULE_modeler.md.
"""
from __future__ import annotations

from ..interfaces import Module
from . import overlay
from .api import bp

overlay.install()

MODULE = Module(key="modeler", label="Scenario modeler", group="P1", blueprint=bp, ui="modeler.js")
