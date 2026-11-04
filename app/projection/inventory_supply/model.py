"""Plain data types and vocabulary for inventory_supply: products, locations, statuses, inputs and the result object."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ..interfaces import InventoryResult

# ---------------------------------------------------------------------------------------------------------------- vocabulary

PRODUCTS = ("bull", "liver", "boneflex", "belle", "flow")
PRODUCT_NAMES = {"bull": "Vital Bull", "liver": "Beef Liver", "boneflex": "Boneflex", "belle": "Vital Belle", "flow": "Vital Flow"}
LOCATIONS = ("revamp", "amazon", "intl")
LOCATION_NAMES = {"revamp": "Revamp Utah", "amazon": "Amazon FBA", "intl": "International 3PL"}
LOCATION_LONG = {"revamp": "Revamp Utah (US third-party logistics)", "amazon": "Amazon FBA (Fulfilled by Amazon)",
                 "intl": "International 3PL (third-party logistics)"}
WAREHOUSES = ("revamp", "intl")                          # where a purchase order lands
PO_STATUSES = ("planned", "ordered", "in transit", "landed", "cancelled")
TRANSFER_STATUSES = ("planned", "shipped", "received", "cancelled")
SOURCES = ("manual", "auto-plan")
REORDER_LOCATIONS = ("revamp", "intl")                   # FBA is fed by transfers, not purchase orders

# The sheet's burn tabs carry these products per location.
DEFAULT_COMBOS: dict[str, tuple[str, ...]] = {
    "revamp": ("bull", "liver", "boneflex", "belle"),
    "amazon": ("bull", "liver", "boneflex", "belle"),
    "intl": ("bull", "liver", "boneflex", "flow"),
}

_ALIASES = {"bull": "bull", "vitalbull": "bull", "vitabull": "bull", "liver": "liver", "beefliver": "liver", "boneflex": "boneflex",
            "belle": "belle", "vitalbelle": "belle", "vitabelle": "belle", "flow": "flow", "vitalflow": "flow", "vitaflow": "flow"}
_LOC_ALIASES = {"revamp": "revamp", "revamputah": "revamp", "warehance": "revamp", "us": "revamp", "": "revamp",
                "amazon": "amazon", "fba": "amazon", "amazonfba": "amazon",
                "intl": "intl", "international": "intl", "international3pl": "intl", "internationalthirdpartylogistics": "intl"}


def _squash(s: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def normalize_product(x: Any) -> str | None:
    """'Vital Bull' | 'vital_bull' | 'bull' -> 'bull'; None when unknown."""
    return _ALIASES.get(_squash(x))


def normalize_location(x: Any) -> str | None:
    return _LOC_ALIASES.get(_squash(x))


def product_name(p: str) -> str:
    return PRODUCT_NAMES.get(p, p)


def location_name(loc: str) -> str:
    return LOCATION_NAMES.get(loc, loc)


# ---------------------------------------------------------------------------------------------------------------- inputs

def num(v: Any, default: float | None = 0.0) -> float | None:
    if v is None:
        return default
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default


@dataclass
class PO:
    id: int
    product: str
    vendor: str
    warehouse: str
    units: float
    land_date: date
    status: str = "planned"
    order_date: date | None = None
    deposit_pct: float | None = None
    unit_cost: float | None = None
    po_number: str | None = None
    sheet_row: int | None = None
    deposit_paid: bool | None = None
    shipping_paid: bool | None = None
    balance_paid: bool | None = None
    link: str | None = None
    note: str | None = None
    source: str = "manual"
    updated_at: Any = None
    ref: str | None = None                   # "12" for a stored row, "new:3" for a row that exists only in a scenario
    overridden: tuple = ()                   # fields this scenario changed on a stored row


@dataclass
class Transfer:
    id: int
    product: str
    units: float
    ship_date: date
    receive_date: date
    status: str = "planned"
    transfer_no: str | None = None
    sheet_row: int | None = None
    note: str | None = None
    source: str = "manual"
    updated_at: Any = None
    ref: str | None = None
    overridden: tuple = ()


@dataclass
class Vendor:
    vendor: str
    lead_weeks: float
    moq_units: float = 0.0
    default_warehouse: str = "revamp"
    note: str | None = None


@dataclass
class VendorCost:
    vendor: str
    product: str
    unit_cost: float
    deposit_pct: float | None = None
    note: str | None = None


@dataclass
class ProductCost:
    product: str
    china_unit_cost: float = 0.0
    sku: str | None = None
    us_unit_cost: float | None = None


@dataclass
class ReorderParam:
    location: str
    product: str
    lead_days: int = 90
    safety_days: int = 14
    moq_units: float = 0.0


@dataclass
class Stock:
    location: str
    product: str
    as_of: date
    on_hand: float
    allocated: float = 0.0
    inbound_per_3pl: float = 0.0
    estimate: bool = False
    note: str | None = None

    @property
    def opening(self) -> float:
        """Units the burn opens with. Revamp opens on available (on hand minus allocated); FBA and the international 3PL on units on hand."""
        return self.on_hand - self.allocated if self.location == "revamp" else self.on_hand


@dataclass
class Inputs:
    pos: list[PO] = field(default_factory=list)
    transfers: list[Transfer] = field(default_factory=list)
    vendors: dict[str, Vendor] = field(default_factory=dict)
    vendor_costs: dict[tuple[str, str], VendorCost] = field(default_factory=dict)
    product_costs: dict[str, ProductCost] = field(default_factory=dict)
    reorder: dict[tuple[str, str], ReorderParam] = field(default_factory=dict)
    stock: dict[tuple[str, str], Stock] = field(default_factory=dict)          # latest snapshot per (location, product)
    last_edit: Any = None                                                      # newest updated_at over purchase orders and transfers
    notes: list = field(default_factory=list)                                  # scenario changes that could not be applied (shown as data checks)
    overridden: dict = field(default_factory=dict)                             # {("vendor", name): fields, ("price", "V|p"): fields, ...} changed by the scenario

    @property
    def empty(self) -> bool:
        return not (self.pos or self.transfers or self.stock)


@dataclass(frozen=True)
class VarSpec:
    """A named scalar variable: every business constant of the module lives here with its sheet default and source."""
    key: str                       # key under params["inventory_supply"]
    label: str
    unit: str
    default: Any
    source: str                    # sheet cell(s) or the rule it comes from
    group: str
    kind: str = "num"              # num | int | opt_num (None = not set) | date
    minimum: float | None = 0.0
    maximum: float | None = None


SETTING_SPECS: tuple[VarSpec, ...] = (
    VarSpec("as_of", "Reference day", "date", None, "Sheet TODAY() (every burn tab starts at this month)", "Timing", "date"),
    VarSpec("horizon_months", "Planning window", "months", 36, "Burn tabs: 36 month columns B:AK", "Timing", "int", 1, 120),
    VarSpec("stale_stock_days", "Stock count counts as stale after", "days", 14, "Freshness rule (no sheet cell)", "Timing", "int", 1, 365),
    VarSpec("safety_days", "Safety stock for every SKU (overrides the per-SKU days)", "days of demand", None, "Warehance Fcst Inputs C44:C47, Intl Fcst Inputs C45:C48 (14)", "Reorder", "opt_num", 0, 365),
    VarSpec("default_safety_days", "Safety stock when a SKU has no row", "days of demand", 14, "Warehance Fcst Inputs C44:C47 (14)", "Reorder", "num", 0, 365),
    VarSpec("default_lead_days_revamp", "Lead time at Revamp when a SKU has no row", "days", 90, "Warehance Fcst Inputs B44:B47 (90)", "Reorder", "num", 0, 730),
    VarSpec("default_lead_days_intl", "Lead time at the international 3PL when a SKU has no row", "days", 60, "Intl Fcst Inputs B45:B48 (60)", "Reorder", "num", 0, 730),
    VarSpec("order_soon_days", "Order soon when the order-by date is within", "days", 21, "Warehance Fcst Inputs B49, Intl Fcst Inputs B50 (21)", "Reorder", "num", 0, 365),
    VarSpec("reorder_cover_months", "Suggested order covers", "months of average demand", 3, "Warehance Fcst Inputs B48, Intl Fcst Inputs B49 (3)", "Reorder", "num", 0, 24),
    VarSpec("out_below_units", "A month is OUT when closing stock is below", "units", 0, "Burn tabs Status row: IF(closing<0,\"OUT\") (0)", "Reorder", "num", -1e7, 1e7),
    VarSpec("low_cover_months", "A month reads LOW below", "months of cover", 1.0, "No sheet cell (added)", "Reorder", "num", 0, 24),
    VarSpec("fba_target_months", "Amazon FBA target cover", "months of next month's demand", 1.5, "Amazon Fcst Inputs B42 (1.5)", "Amazon FBA", "num", 0, 12),
    VarSpec("plan_cover_months", "Plan: each new order covers", "months of forward demand", 3, "2026-10-13 rebalance (Purchase Orders notes: 3-mo cover)", "Plan", "num", 0, 24),
    VarSpec("plan_safety_days", "Plan: safety stock to keep", "days of demand", None, "2026-10-13 rebalance (14); empty = the SKU's own safety days", "Plan", "opt_num", 0, 365),
    VarSpec("po_round_units", "Plan: round orders up to a multiple of", "units", 1000, "2026-10-13 rebalance (1,000)", "Plan", "num", 1, 1e6),
    VarSpec("transfer_round_units", "Plan: round FBA transfers up to a multiple of", "units", 500, "2026-10-13 rebalance (500)", "Plan", "num", 1, 1e6),
    VarSpec("transfer_min_units", "Plan: smallest FBA transfer", "units", 500, "2026-10-13 rebalance (500)", "Plan", "num", 0, 1e6),
    VarSpec("fba_restore_months", "Plan: a transfer restores FBA to", "months of cover", 2.5, "2026-10-13 rebalance (2.5)", "Plan", "num", 0, 24),
    VarSpec("transfer_lag_months", "Plan: months from ship to FBA receive", "months", 1, "FBA Transfers: ship month = receive month - 1", "Plan", "int", 0, 6),
    VarSpec("days_per_month", "Plan: days in a month for run rates", "days", 30.4, "2026-10-13 rebalance (rate x 30.4)", "Plan", "num", 1, 31),
    VarSpec("duplicate_window_days", "Duplicate check: orders landing within", "days", 21, "No sheet cell (added)", "Data checks", "num", 0, 365),
    VarSpec("cash_window_days", "Headline cash window", "days", 90, "No sheet cell (added)", "Timing", "int", 1, 730),
)
SPEC_BY_KEY = {v.key: v for v in SETTING_SPECS}


class Settings:
    """The module's scalar variables (scenario params `inventory_supply`), each defaulting to the sheet's value. Attribute names are the spec keys."""
    horizon_months: int
    stale_stock_days: int
    safety_days: float | None
    default_safety_days: float
    default_lead_days_revamp: float
    default_lead_days_intl: float
    order_soon_days: float
    reorder_cover_months: float
    out_below_units: float
    low_cover_months: float
    fba_target_months: float
    plan_cover_months: float
    plan_safety_days: float | None
    po_round_units: float
    transfer_round_units: float
    transfer_min_units: float
    fba_restore_months: float
    transfer_lag_months: int
    days_per_month: float
    duplicate_window_days: float
    cash_window_days: int

    def __init__(self, **kw: Any) -> None:
        for spec in SETTING_SPECS:
            if spec.key != "as_of":
                setattr(self, spec.key, spec.default)
        for k, v in kw.items():
            if k not in SPEC_BY_KEY or k == "as_of":
                raise TypeError(f"unknown setting {k!r}")
            setattr(self, k, v)

    def __repr__(self) -> str:
        return "Settings(" + ", ".join(f"{s.key}={getattr(self, s.key)!r}" for s in SETTING_SPECS if s.key != "as_of") + ")"

    @classmethod
    def from_params(cls, params: dict | None) -> "Settings":
        """Settings from the effective scenario params; a value that is missing, mistyped or out of range keeps the sheet default."""
        p = (params or {}).get("inventory_supply") or {}
        s = cls()
        for spec in SETTING_SPECS:
            if spec.key == "as_of" or spec.key not in p:
                continue
            v = p[spec.key]
            if v is None:
                continue
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                continue
            if (spec.minimum is not None and v < spec.minimum) or (spec.maximum is not None and v > spec.maximum):
                continue
            setattr(s, spec.key, int(v) if spec.kind == "int" else float(v))
        return s


# ---------------------------------------------------------------------------------------------------------------- result

@dataclass
class SupplyResult(InventoryResult):
    """InventoryResult plus everything the page and the reconcile map read.

    burn:      {(location, product, line): Series}, lines opening | demand | inbound | out | closing | cover | to_send (FBA); locations
               revamp | amazon | intl; the extra location `total` carries the all-locations roll-up per product
    scalars:   {dotted key: float} reconcile scalars (reorder dates are Sheets serial numbers: days since 1899-12-30)
    """
    start: date | None = None
    months: list[date] = field(default_factory=list)
    horizon: int = 36
    today: date | None = None
    warnings: list[dict] = field(default_factory=list)
    po_rows: list[dict] = field(default_factory=list)
    transfer_rows: list[dict] = field(default_factory=list)
    stats: list[dict] = field(default_factory=list)
    scalars: dict[str, float] = field(default_factory=dict)
    po_cells: dict[str, list] = field(default_factory=dict)
    deposits: dict[date, float] = field(default_factory=dict)
    balances: dict[date, float] = field(default_factory=dict)
    landing_value: dict[date, float] = field(default_factory=dict)         # purchase-order value by landing month
    landing_deposits: dict[date, float] = field(default_factory=dict)      # deposits on the orders landing that month
    outlay_cumulative: dict[date, float] = field(default_factory=dict)
    intl_outlay: dict[date, float] = field(default_factory=dict)
    value_by_location: dict[tuple[str, str], dict[date, float]] = field(default_factory=dict)
    check: dict[date, float] = field(default_factory=dict)
    tldr: dict = field(default_factory=dict)
    status_at: dict[tuple[str, str], dict[date, str]] = field(default_factory=dict)
    combos: list[tuple[str, str]] = field(default_factory=list)
