"""Projection module `pacing`: month pacing against the P&L goal, Month Snapshot, and the day / week / month rollups.

Replaces the sheet tabs Automated Pacing, Month Snapshot, Pacing - Daily (180d), Rollup - Weekly (WoW), Rollup - Monthly (MoM) and Meta - Daily / Weekly / Monthly.
Read-only consumer of the engine (P&L goals, cohort curves) and of the daily actual feeds (etl/projection_pacing.py). See docs/projection/MODULE_pacing.md.
"""
import dataclasses

from ..interfaces import Module
from .api import bp
from .levers import validate_params
from .model import compute, load_inputs

_HOOKS = {"validate_params": validate_params} if "validate_params" in {f.name for f in dataclasses.fields(Module)} else {}
MODULE = Module(key="pacing", load_inputs=load_inputs, compute=compute, blueprint=bp, ui="pacing.js", **_HOOKS)
