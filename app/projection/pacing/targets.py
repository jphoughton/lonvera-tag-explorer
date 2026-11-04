"""Goals for pacing: read the scenario's P&L (and driver results) by month, and turn its cost lines into daily rates.

The P&L module returns one monthly Series per sheet row. Pacing needs a goal per metric for the month being paced and, for the EBITDA estimate,
the cost structure: each cost line is either a rate on a revenue driver (Amazon revenue, DTC revenue, total revenue) or fixed dollars. The rate of a
line is its P&L amount divided by its driver in the same month, so a plan that changes a rate or a fixed amount changes pacing with no code here.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from typing import Any, Iterable

from .levers import Levers
from .periods import days_in_month

# P&L sheet rows that pacing reads (labels in docs/projection/MODULE_pacing.md)
R_NC_REV, R_NC_ORDERS, R_NC_CPA, R_NC_AOV, R_NC_ROAS = 12, 13, 14, 15, 16
R_REPEAT, R_DTC_REV, R_AMZ_REV, R_TT_REV, R_TAX, R_TOTAL_REV = 17, 18, 19, 20, 21, 22
R_AD_SPEND = 11                      # Meta + Google
R_MKT_SPEND, R_MKT_AMAZON, R_MKT_META, R_MKT_GOOGLE, R_MKT_TIKTOK = 51, 52, 53, 54, 56
R_NOI = 100
R_BELOW = (62, 94)                   # default of the lever pacing.below_ebitda_rows (marketing influencers, uncategorized)

DRIVER_ROWS = {"total_rev": R_TOTAL_REV, "dtc_rev": R_DTC_REV, "amazon_rev": R_AMZ_REV}


@dataclass(frozen=True)
class CostLine:
    key: str
    label: str
    section: str                     # cogs | opex
    group: str                       # row group on the pacing table
    rows: tuple[int, ...]            # P&L sheet rows summed
    driver: str                      # total_rev | dtc_rev | amazon_rev | fixed


COST_LINES: tuple[CostLine, ...] = (
    CostLine("amazon_fees_var", "Amazon fees (rate)", "cogs", "amazon_fees", (25, 26, 28), "amazon_rev"),
    CostLine("amazon_fees_fixed", "Amazon subscription", "cogs", "amazon_fees", (27,), "fixed"),
    CostLine("cogs_direct", "COGS (direct)", "cogs", "cogs_direct", (31,), "total_rev"),
    CostLine("fulfillment", "Fulfillment", "cogs", "fulfillment", (37,), "dtc_rev"),
    CostLine("inbound", "Inbound shipping", "cogs", "inbound", (38,), "total_rev"),
    CostLine("payment", "Payment processing", "cogs", "payment", (40,), "dtc_rev"),
    CostLine("mkt_fixed", "Marketing agency and services", "opex", "marketing", (60, 61), "fixed"),
    CostLine("mkt_software", "Marketing software", "opex", "marketing", (63,), "total_rev"),
    CostLine("other_biz", "Other business", "opex", "other_business", (66,), "total_rev"),
    CostLine("headcount", "Headcount", "opex", "headcount", (75,), "fixed"),
    CostLine("ovh_fixed", "Overhead (fixed)", "opex", "overhead", (79, 81, 82, 83, 84, 85, 87, 88, 89, 90), "fixed"),
    CostLine("ovh_dtc", "Overhead (rate on DTC revenue)", "opex", "overhead", (80, 86), "dtc_rev"),
    CostLine("ovh_total", "Overhead (rate on total revenue)", "opex", "overhead", (91,), "total_rev"),
)
LINE_BY_KEY = {c.key: c for c in COST_LINES}
# group -> display label on the pacing table
GROUP_LABELS = {"amazon_fees": "Amazon fees", "cogs_direct": "COGS (direct)", "fulfillment": "Fulfillment", "inbound": "Inbound shipping",
                "payment": "Payment processing", "marketing": "Marketing opex", "other_business": "Other business", "headcount": "Headcount",
                "overhead": "Overhead"}


# ---------------------------------------------------------------------------------------------------------------- row -> line key

def _rows_from_line_keys(line_keys: Any) -> dict[int, str]:
    out: dict[int, str] = {}
    items: Iterable
    if isinstance(line_keys, dict):
        items = line_keys.items()
    else:
        items = ((getattr(x, "key", None) or (x.get("key") if isinstance(x, dict) else None), x) for x in line_keys)
    for key, meta in items:
        row = getattr(meta, "row", None)
        if row is None and isinstance(meta, dict):
            row = meta.get("row")
        if row is None and isinstance(meta, (tuple, list)):
            row = next((m for m in meta if isinstance(m, int)), None)
        if isinstance(row, int) and key:
            out.setdefault(row, key)
    return out


def default_row_map() -> dict[int, str]:
    """P&L sheet row -> LINE_KEYS key: from the pnl module when it is built (ROW_TO_KEY or LINE_KEYS), else the foundation stub's keys."""
    m: dict[int, str] = {}
    try:
        from ..pnl import lines as L                                     # type: ignore
        rk = getattr(L, "ROW_TO_KEY", None)
        if isinstance(rk, dict):
            m.update({int(k): v for k, v in rk.items()})
        elif hasattr(L, "LINE_KEYS"):
            m.update(_rows_from_line_keys(L.LINE_KEYS))
    except Exception:                                                    # noqa: BLE001  (pnl not built yet)
        pass
    try:
        from .. import stubs
        for key, row in stubs.PNL_ROWS.items():
            m.setdefault(row, key)
    except Exception:                                                    # noqa: BLE001
        pass
    return m


# ---------------------------------------------------------------------------------------------------------------- the book

class TargetBook:
    """Monthly goals by P&L sheet row for one scenario. `row(n, month)` -> float or None (not available)."""

    def __init__(self, pnl: Any = None, drivers: Any = None, cohorts: Any = None, row_map: dict[int, str] | None = None, levers: Levers | None = None):
        self.levers = levers or Levers()
        self.pnl = pnl
        self._cost_lines = tuple(replace(c, driver=self.levers.cost_drivers.get(c.key, c.driver)) for c in COST_LINES)
        self._cost_by_key = {c.key: c for c in self._cost_lines}
        self.lines: dict[str, dict] = dict(getattr(pnl, "lines", None) or {})
        self.drivers, self.cohorts = drivers, cohorts
        self.row_map = row_map if row_map is not None else default_row_map()
        self.missing: set[int] = set()

    # -- raw access
    def _driver_row(self, n: int, month: date) -> float | None:
        d, c = self.drivers, self.cohorts
        g = lambda s, name: (getattr(s, name, None) or {}).get(month) if s is not None else None      # noqa: E731
        if n == R_AD_SPEND:
            a, b = g(d, "spend_meta"), g(d, "spend_google")
            return None if a is None and b is None else (a or 0.0) + (b or 0.0)
        simple = {R_NC_REV: "nc_rev", R_NC_ORDERS: "nc_orders", R_NC_CPA: "nc_cpa", R_NC_AOV: "nc_aov", R_NC_ROAS: "nc_roas", R_AMZ_REV: "amazon_rev",
                  R_TT_REV: "tiktok_rev", R_TAX: "sales_tax", R_MKT_AMAZON: "spend_amazon_ads", R_MKT_META: "spend_meta", R_MKT_GOOGLE: "spend_google",
                  R_MKT_TIKTOK: "spend_tiktok"}
        if n in simple:
            return g(d, simple[n])
        if n == R_REPEAT:
            return g(c, "repeat_rev")
        if n == R_DTC_REV:
            nc, rp = self._driver_row(R_NC_REV, month), self._driver_row(R_REPEAT, month)
            return None if nc is None or rp is None else nc + rp
        if n == R_TOTAL_REV:
            parts = [self.row(R_DTC_REV, month), self.row(R_AMZ_REV, month), self.row(R_TT_REV, month), self.row(R_TAX, month)]
            return None if parts[0] is None or parts[1] is None else sum(p or 0.0 for p in parts)
        if n == R_MKT_SPEND:
            parts = [self._driver_row(x, month) for x in (R_MKT_AMAZON, R_MKT_META, R_MKT_GOOGLE, R_MKT_TIKTOK)]
            return None if all(p is None for p in parts) else sum(p or 0.0 for p in parts)
        return None

    def row(self, n: int, month: date) -> float | None:
        key = self.row_map.get(n)
        if key is not None and key in self.lines:
            return float(self.lines[key].get(month, 0.0) or 0.0)
        v = self._driver_row(n, month)
        if v is None:
            self.missing.add(n)
            return None
        return float(v)

    def val(self, n: int, month: date) -> float:
        """Row value, 0 when the P&L does not provide it (recorded in .missing)."""
        v = self.row(n, month)
        return 0.0 if v is None else v

    def rows_sum(self, rows: Iterable[int], month: date) -> float:
        return sum(self.val(n, month) for n in rows)

    def has(self, n: int) -> bool:
        key = self.row_map.get(n)
        return (key is not None and key in self.lines) or n in (R_AD_SPEND, R_NC_REV, R_NC_ORDERS, R_NC_CPA, R_NC_AOV, R_NC_ROAS, R_AMZ_REV)

    # -- named goals
    def goal(self, name: str, month: date) -> float | None:
        n = GOAL_ROWS[name]
        return self.row(n, month) if isinstance(n, int) else (
            None if any(self.row(x, month) is None for x in n) else sum(self.val(x, month) for x in n))

    def ebitda_goal(self, month: date) -> float | None:
        noi = self.row(R_NOI, month)
        return None if noi is None else noi + self.below_ebitda(month)

    def below_ebitda(self, month: date) -> float:
        return self.rows_sum(self.levers.below_ebitda_rows, month)

    def tax_rate(self, month: date) -> float:
        """Sales-tax estimate as a fraction of DTC revenue, negative (contra revenue): the scenario's stated rate, else P&L row 21 / row 18."""
        if self.levers.explicit_tax_rate is not None:
            return -self.levers.explicit_tax_rate
        dtc, tax = self.row(R_DTC_REV, month), self.row(R_TAX, month)
        return (tax / dtc) if dtc and tax is not None else 0.0

    # -- cost lines with the scenario's driver overrides
    def lines_(self) -> tuple[CostLine, ...]:
        return self._cost_lines

    def line(self, key: str) -> CostLine:
        return self._cost_by_key[key]

    # -- cost structure
    def driver_value(self, driver: str, month: date) -> float:
        return self.val(DRIVER_ROWS[driver], month)

    def line_goal(self, line: CostLine, month: date) -> float:
        return self.rows_sum(line.rows, month)

    def rate(self, line: CostLine, month: date) -> float:
        """Rate of a rate-type line: goal / driver in `month` (0 when the driver is empty)."""
        if line.driver == "fixed":
            return 0.0
        d = self.driver_value(line.driver, month)
        return self.line_goal(line, month) / d if d else 0.0

    def rates(self, month: date) -> dict[str, float]:
        return {c.key: self.rate(c, month) for c in self._cost_lines if c.driver != "fixed"}

    def fixed_per_day(self, month: date, keys: Iterable[str]) -> float:
        dim = days_in_month(month)
        return sum(self.line_goal(self._cost_by_key[k], month) for k in keys) / dim

    def missing_rows(self) -> list[int]:
        return sorted(self.missing)


GOAL_ROWS: dict[str, Any] = {
    "total_rev": R_TOTAL_REV, "dtc_rev": R_DTC_REV, "nc_rev": R_NC_REV, "repeat_rev": R_REPEAT, "amazon_rev": R_AMZ_REV, "tax": R_TAX,
    "nc_orders": R_NC_ORDERS, "nc_cpa": R_NC_CPA, "nc_aov": R_NC_AOV, "nc_roas": R_NC_ROAS, "dtc_ad_spend": R_AD_SPEND,
    "spend_total": R_MKT_SPEND, "spend_meta": R_MKT_META, "spend_google": R_MKT_GOOGLE, "spend_amazon": R_MKT_AMAZON, "spend_tiktok": R_MKT_TIKTOK,
    "noi": R_NOI,
}
