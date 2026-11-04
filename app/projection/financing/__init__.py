"""Financing module ("Cash & debt"): term loan, seller note, tax, cash waterfall, payback, spend x ROAS grid, payout schedule.

Replaces the sheet tabs Term Debt Monthly Paydown, P&L rows 108-131, Payback, Spend-ROAS Matrix and the payout block P&L!E177:P206.
"""
from ..interfaces import Module
from .api import bp
from .model import compute, load_inputs

MODULE = Module(key="financing", label="Cash & debt", group="P2", load_inputs=load_inputs, compute=compute, blueprint=bp, ui="financing.js")
