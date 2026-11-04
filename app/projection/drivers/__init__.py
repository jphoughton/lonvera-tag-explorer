"""Projection module `drivers` (page "Spend plan"): the levers that make the plan a growth or harvest case.

Replaces P&L rows 11-16, 19-20 and 51-56 (spend, new-customer revenue, orders, CPA, AOV, ROAS, Amazon and TikTok revenue, channel split);
revenue.revenue_lines() produces rows 17, 18, 21, 22 once the cohorts module has run. See docs/projection/MODULE_drivers.md.
"""
from ..interfaces import Module
from . import loaders, model
from .api import bp
from .model import DriversResult
from .revenue import RevenueLines, revenue_lines


def load_inputs(conn, scn):
    return loaders.load_inputs(conn, scn)


def compute(scn, inputs) -> DriversResult:
    return model.compute_drivers(scn.params, inputs)


MODULE = Module(key="drivers", label="Spend plan", group="P1", load_inputs=load_inputs, compute=compute, blueprint=bp, ui="drivers.js")

__all__ = ["MODULE", "DriversResult", "RevenueLines", "revenue_lines"]
