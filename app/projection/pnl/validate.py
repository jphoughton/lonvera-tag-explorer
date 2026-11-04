"""Write rules for the pnl module, called by the core on every series write and on every scenario params patch (docs/projection/TICKETS_validation.md).

Series: only lines with a driver (or the marketing spend and its channels) can be typed over; a month that is already books cannot (upload a corrected
QuickBooks export instead); amounts are not negative, except the uncategorized line. Params: the same checks the Settings and Cost drivers endpoints make,
so a scenario patch that does not go through them is held to the same limits.
"""
from __future__ import annotations

from datetime import date

from ...queries import ApiError
from . import lines as L
from . import store
from .model import validate_segments

OVERRIDABLE = frozenset(L.DRIVER_KEYS + L.DRIVEN_PARENTS + L.CHANNEL_KEYS + ("marketing_spend",))
MAY_BE_NEGATIVE = frozenset(("uncategorized",))
MONTH_NAMES = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def validate_series(conn, scenario, items) -> None:
    books_through: date | None = None
    for it in items:
        if not it.line_key.startswith(L.ACTUALS_PREFIX):
            continue
        key = it.line_key[len(L.ACTUALS_PREFIX):]
        if it.value is None:
            continue                                            # clearing a typed month is always allowed
        label = f"{it.line_key} {MONTH_NAMES[it.month.month - 1]} {it.month.year}"
        if key not in OVERRIDABLE:
            why = "is calculated from other lines" if key in L.LINE_KEYS else "is not a P&L line"
            raise ApiError(400, f"{label}: {key} {why}, so it cannot be typed over. Change its driver or the lines it adds up")
        if books_through is None:
            books_through = store.data_through(conn, store.today()) or date.min
        if it.month <= books_through:
            raise ApiError(400, f"{label}: that month is in the books. Upload a corrected QuickBooks export to change it")
        if float(it.value) < 0 and key not in MAY_BE_NEGATIVE:
            raise ApiError(400, f"{label}: an amount cannot be negative")


def validate_params(params) -> None:
    from .api import check_settings
    own = {k: v for k, v in (params or {}).items() if k != "drivers"}
    if own:
        check_settings(own)
    for key, v in ((params or {}).get("drivers") or {}).items():
        try:
            validate_segments(key, v.get("segments") if isinstance(v, dict) else v)
        except ValueError as e:
            raise ApiError(400, f"pnl.drivers.{key}: {e}") from None
