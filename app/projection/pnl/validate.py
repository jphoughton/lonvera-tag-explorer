"""Write rules for the pnl module, called by the core on every series write and on every scenario params patch (docs/projection/TICKETS_validation.md).

Series: only lines with a driver (or the marketing spend and its channels) can be typed over; a month that is already books cannot (upload a corrected
QuickBooks export instead); amounts are not negative, except the uncategorized line. Params: the same checks the Settings and Cost drivers endpoints make,
so a scenario patch that does not go through them is held to the same limits.
"""
from __future__ import annotations

from datetime import date

from ...queries import ApiError
from . import charts
from . import lines as L
from . import store
from .model import validate_segments

OVERRIDABLE = frozenset(L.DRIVER_KEYS + L.DRIVEN_PARENTS + L.CHANNEL_KEYS + ("marketing_spend",))
MAY_BE_NEGATIVE = frozenset(("uncategorized",))
QB_MAY_BE_NEGATIVE = frozenset(("refunds_discounts",))
MONTH_NAMES = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
QB_SERIES_PREFIX = charts.get(charts.QB2026).series_prefix            # 'pnl.qb26.'


def validate_series(conn, scenario, items) -> None:
    """Typed months. Sheet-chart keys are 'pnl.<line>', new-chart keys 'pnl.qb26.<line>'; each is accepted only for a scenario that shows that chart, and
    only for lines that chart projects."""
    scn_chart = charts.chart_of(((getattr(scenario, "params", None) or {}).get("pnl")))
    through: dict[str, date] = {}
    for it in items:
        if not it.line_key.startswith(L.ACTUALS_PREFIX):
            continue
        qb = it.line_key.startswith(QB_SERIES_PREFIX)
        chart = charts.QB2026 if qb else charts.SHEET
        cd = charts.get(chart)
        key = it.line_key[len(cd.series_prefix):]
        if it.value is None:
            continue                                            # clearing a typed month is always allowed
        label = f"{it.line_key} {MONTH_NAMES[it.month.month - 1]} {it.month.year}"
        if chart != scn_chart:
            raise ApiError(400, f"{label}: this scenario shows the {'new' if scn_chart == charts.QB2026 else 'sheet'} chart, so it cannot carry typed months for the "
                                f"{'new' if qb else 'sheet'} chart. Choose the chart in Settings first")
        if key not in cd.overridable:
            why = "is calculated from other lines" if key in cd.line_keys else "is not a P&L line"
            raise ApiError(400, f"{label}: {key} {why}, so it cannot be typed over. Change its driver or the lines it adds up")
        if chart not in through:
            through[chart] = store.data_through(conn, store.today(), chart) or date.min
        if it.month <= through[chart]:
            raise ApiError(400, f"{label}: that month is in the books. Upload a corrected QuickBooks export to change it")
        if float(it.value) < 0 and key not in (QB_MAY_BE_NEGATIVE if qb else MAY_BE_NEGATIVE):
            raise ApiError(400, f"{label}: an amount cannot be negative")


def validate_params(params) -> None:
    from .api import check_settings
    own = {k: v for k, v in (params or {}).items() if k not in ("drivers", "qb26")}
    if own:
        check_settings(own)
    for key, v in ((params or {}).get("drivers") or {}).items():
        try:
            validate_segments(key, v.get("segments") if isinstance(v, dict) else v)
        except ValueError as e:
            raise ApiError(400, f"pnl.drivers.{key}: {e}") from None
    qb26 = (params or {}).get("qb26")
    if qb26 is not None:
        if not isinstance(qb26, dict) or set(qb26) - {"drivers"}:
            raise ApiError(400, "pnl.qb26 may only hold drivers")
        for key, v in (qb26.get("drivers") or {}).items():
            try:
                validate_segments(key, v.get("segments") if isinstance(v, dict) else v, charts.QB2026)
            except ValueError as e:
                raise ApiError(400, f"pnl.qb26.drivers.{key}: {e}") from None
