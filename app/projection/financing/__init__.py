"""Financing module ("Cash & debt"): term loan, seller note, tax, cash waterfall, payback, spend x ROAS grid, payout schedule.

Replaces the sheet tabs Term Debt Monthly Paydown, P&L rows 108-131, Payback, Spend-ROAS Matrix and the payout block P&L!E177:P206.
`validate_params(params)` refuses the values PUT /financing/inputs refuses; it is passed to the core's generic scenario PATCH hook when `Module` has one.
"""
import dataclasses

from ..interfaces import Module
from .api import bp
from .model import compute, load_inputs
from .params import validate_params

_HOOKS = {"validate_params": validate_params} if "validate_params" in {f.name for f in dataclasses.fields(Module)} else {}

MODULE = Module(key="financing", label="Cash & debt", group="P2", load_inputs=load_inputs, compute=compute, blueprint=bp, ui="financing.js", **_HOOKS)
