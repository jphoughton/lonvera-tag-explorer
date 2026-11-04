"""Projection module `cohorts`: cohort table, retention curves (Live and Goal) and the repeat-revenue waterfall.

Replaces the sheet tabs `Cohort Data - Unified`, `Curves` and `Hist Waterfall - Last 4 Months Curve`. Output: CohortResult.repeat_rev feeds the
P&L's repeat revenue line. See docs/projection/MODULE_cohorts.md.
"""
from ..interfaces import Module
from .api import bp
from .model import compute, load_inputs

MODULE = Module(key="cohorts", load_inputs=load_inputs, compute=compute, blueprint=bp, ui="cohorts.js")
