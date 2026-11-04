"""Locations, products and defaults for the inventory demand module."""
from __future__ import annotations

LOCATIONS = ("revamp", "amazon", "intl")

# Labels read as a finished document: no acronyms (3PL, FBA) in anything the page shows.
LOCATION_LABELS = {
    "revamp": "US direct to consumer (Revamp, Utah)",
    "amazon": "Amazon (Fulfilled by Amazon)",
    "intl": "International (overseas warehouse)",
}
LOCATION_SHORT = {"revamp": "US direct", "amazon": "Amazon", "intl": "International"}

PRODUCT_LABELS = {
    "bull": "Vital Bull",
    "liver": "Beef Liver",
    "boneflex": "Boneflex",
    "belle": "Vital Belle",
    "flow": "Vital Flow",
}
PRODUCT_ORDER = tuple(PRODUCT_LABELS)
# Products each location carries in the sheet (Vital Belle has no international volume, Vital Flow is international only).
LOCATION_PRODUCTS = {
    "revamp": ("bull", "liver", "boneflex", "belle"),
    "amazon": ("bull", "liver", "boneflex", "belle"),
    "intl": ("bull", "liver", "boneflex", "flow"),
}

# Column headers in the sheet's daily tabs -> product slug.
HEADER_PRODUCT = {"vital bull": "bull", "beef liver": "liver", "boneflex": "boneflex", "vital belle": "belle", "vital flow": "flow"}
# The international warehouse's item codes (monthly tracking export) -> product slug.
SKU_PRODUCT = {"DPCD1-GN178001": "bull", "DPCD1-GN180001": "liver", "DPCD1-GN187001": "boneflex", "DPCD1-GN179001": "flow"}

# Sheet tab holding each location's daily units. Amazon is read from the per-product daily tab the sheet refreshes every day; the older
# `Amazon Daily Units` tab (last refreshed by hand) is only the fallback.
DAILY_TABS = {"revamp": "Warehance Daily", "amazon": "Amazon - Product Daily", "intl": "Intl Daily Units"}
AMAZON_FALLBACK_TAB = "Amazon Daily Units"
# Days at the end of each feed that are not final yet: Warehance's current day, Amazon's current day (and, in the fallback tab, two
# reporting-lag days). Loaders keep these out of the database, so everything stored is a complete day.
PARTIAL_TAIL_DAYS = {"revamp": 1, "amazon": 1, "intl": 0}
FALLBACK_PARTIAL_TAIL_DAYS = 2

# Baseline window in days (trailing average of complete days), as in the sheet's Fcst Inputs tabs.
DEFAULT_BASELINE_DAYS = {"revamp": 28, "amazon": 30, "intl": 30}

# Feed freshness: days after which the freshness check calls the feed stale.
STALE_AFTER_DAYS = {"revamp": 2, "amazon": 2, "intl": 4}
FEED_LABELS = {
    "revamp": "Warehance shipments (US direct to consumer)",
    "amazon": "Amazon units sold",
    "intl": "International units shipped (estimated from Warehance non-US orders)",
}

MODULE_KEY = "inventory_demand"
SERIES_PREFIX = "inventory_demand."


def product_label(slug: str) -> str:
    return PRODUCT_LABELS.get(slug) or slug.replace("_", " ").title()


def location_products(location: str, seen: set[str] | None = None) -> tuple[str, ...]:
    """The location's configured products, then any other product that holds data there."""
    base = list(LOCATION_PRODUCTS.get(location, ()))
    extra = sorted(p for p in (seen or ()) if p not in base)
    return tuple(base + extra)
