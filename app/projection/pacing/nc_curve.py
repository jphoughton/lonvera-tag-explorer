"""Hourly share curve of new-customer (NC) revenue and the end-of-day projection of the "NC revenue today" card.

Pure functions, no database. Input rows are (day, hour, nc_rev, nc_orders) as the feed `nc_hourly` stores them: Pacific day, Pacific wall-clock hour 0-23.

Rules (the same as the Meta spend card, plus the ones Josh set for revenue):
- Only closed days enter the curve: the window is the `window_days` days ending yesterday (Pacific). Today is never read, even if the table holds a row for it.
- Ratio of sums: share of hour h = sum of NC revenue in hour h over the window / sum of all NC revenue in the window. Never an average of daily ratios
  (a thin day would weigh as much as a big one). `mean_of_ratios` is returned next to it only so the two can be compared.
- Interpolation inside the hour is linear: at 08:48 the share is the cumulative share through 07:59 plus 0.8 of hour 8's share.
- Clock hours: on the 25-hour fall-back day both 1 a.m. hours land in hour 1, on the 23-hour spring-forward day hour 2 is empty. The card's time is the
  Pacific wall clock, so the curve is in wall-clock hours too.
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from typing import Iterable, Sequence

WINDOW_DAYS = 30
DOW_GAP_POINTS = 0.03          # weekday vs weekend share at the current hour differing by more than this shows the 'same weekday only' toggle
LOW_SHARE = 0.05               # below this share of the day the projection swings: say so
STALE_AFTER_DAYS = 2           # newest closed day older than this (against yesterday) is a stale curve
MIN_DAYS = 3                   # fewer days than this: no curve, a projection from it would be noise


def window_dates(today: dt.date, window_days: int = WINDOW_DAYS) -> tuple[dt.date, dt.date]:
    """First and last day of the window: the `window_days` closed days ending yesterday."""
    last = today - dt.timedelta(days=1)
    return last - dt.timedelta(days=window_days - 1), last


def _by_day(rows: Iterable[Sequence], first: dt.date, last: dt.date) -> dict[dt.date, list[list[float]]]:
    """{day: [[rev, orders] x 24]} for days in [first, last]; rows outside the window (today included) are dropped."""
    days: dict[dt.date, list[list[float]]] = {}
    for day, hour, rev, orders in rows:
        if not first <= day <= last:
            continue
        h = int(hour)
        if not 0 <= h <= 23:
            raise ValueError(f"hour {h} outside 0-23 on {day}")
        cells = days.setdefault(day, [[0.0, 0.0] for _ in range(24)])
        cells[h][0] += float(rev or 0)
        cells[h][1] += float(orders or 0)
    return days


def share_curve(cells_by_day: dict[dt.date, list[list[float]]], dates: Sequence[dt.date] | None = None) -> dict:
    """Ratio-of-sums curve over `dates` (default: every day given). cum[h] = share of the day's NC revenue in hours 0..h-1 (cum[0] = 0, cum[24] = 1)."""
    use = sorted(d for d in cells_by_day if dates is None or d in dates)
    hour_rev = [sum(cells_by_day[d][h][0] for d in use) for h in range(24)]
    hour_orders = [sum(cells_by_day[d][h][1] for d in use) for h in range(24)]
    total = sum(hour_rev)
    if not use or total <= 0:
        return {"days": use, "total": 0.0, "orders": 0, "share": [0.0] * 24, "cum": [0.0] * 25, "mean_of_ratios": None}
    share = [x / total for x in hour_rev]
    cum = [0.0]
    for s in share:
        cum.append(cum[-1] + s)
    cum[24] = 1.0
    ratios = []                                                        # the per-day curves, averaged: shown for comparison only
    for d in use:
        t = sum(c[0] for c in cells_by_day[d])
        if t > 0:
            run, row = 0.0, [0.0]
            for c in cells_by_day[d]:
                run += c[0] / t
                row.append(run)
            ratios.append(row)
    mor = [sum(r[i] for r in ratios) / len(ratios) for i in range(25)] if ratios else None
    return {"days": use, "total": total, "orders": int(sum(hour_orders)), "share": share, "cum": cum, "mean_of_ratios": mor, "hour_rev": hour_rev}


def share_at(cum: Sequence[float], share: Sequence[float], hours: float) -> float:
    """Cumulative share by `hours` (0-24, Pacific wall clock), interpolated linearly inside the hour."""
    hours = max(0.0, min(24.0, hours))
    f = min(int(hours), 23)
    return cum[f] + share[f] * (hours - f)


def project(curve: dict, spent: float, hours: float) -> dict:
    """End-of-day NC revenue = revenue so far / share of a typical day already in by `hours`."""
    if not curve["days"] or curve["total"] <= 0:
        return {"share": None, "projected_eod": None, "to_go": None, "reason": "No closed day of new-customer revenue by hour is loaded."}
    sh = share_at(curve["cum"], curve["share"], hours)
    if sh <= 0:
        return {"share": 0.0, "projected_eod": None, "to_go": None, "reason": "Too early in the day to project: no new-customer revenue has landed by this time on a typical day."}
    eod = spent / sh
    return {"share": sh, "projected_eod": eod, "to_go": eod - spent}


def _group(cells_by_day, pred) -> dict:
    return share_curve(cells_by_day, [d for d in cells_by_day if pred(d)])


def build(rows: Iterable[Sequence], today: dt.date, hours: float, spent: float | None = None, dow: str = "all",
          window_days: int = WINDOW_DAYS) -> dict:
    """The whole payload of the card: the projection, the curve behind it, the window, the weekday check and the stale flag.

    dow='all' uses every closed day of the window; dow='same' uses only the days with today's weekday."""
    first, last = window_dates(today, window_days)
    cells = _by_day(rows, first, last)
    all_days = sorted(cells)
    newest = max(all_days) if all_days else None
    age = (last - newest).days if newest else None
    missing = [first + dt.timedelta(days=i) for i in range(window_days) if first + dt.timedelta(days=i) not in cells and newest and first + dt.timedelta(days=i) >= min(all_days)]
    wd = today.weekday()
    full = share_curve(cells)
    weekday = _group(cells, lambda d: d.weekday() < 5)
    weekend = _group(cells, lambda d: d.weekday() >= 5)
    same = _group(cells, lambda d: d.weekday() == wd)
    gap = None
    if weekday["total"] > 0 and weekend["total"] > 0:
        gap = share_at(weekday["cum"], weekday["share"], hours) - share_at(weekend["cum"], weekend["share"], hours)
    use = same if dow == "same" else full
    out = project(use, spent, hours) if spent is not None else {"share": None, "projected_eod": None, "to_go": None}
    if spent is None:
        out["share"] = share_at(use["cum"], use["share"], hours) if use["days"] and use["total"] > 0 else None
    n_use = len(use["days"])
    loaded = n_use >= MIN_DAYS and use["total"] > 0
    if not loaded and out.get("projected_eod") is not None:
        out.update({"projected_eod": None, "to_go": None, "reason": f"Only {n_use} closed day(s) of new-customer revenue by hour are loaded; at least {MIN_DAYS} are needed."})
    out["share_of_day"] = out.get("share") if loaded else None
    if not loaded:
        out["share"] = None
    out.update({
        "loaded": loaded, "hour": round(hours, 2), "spent": spent, "dow": dow, "weekday": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"][wd],
        "low_share": bool(loaded and out["share_of_day"] is not None and 0 < out["share_of_day"] < LOW_SHARE),
        "window": {"from": first.isoformat(), "to": last.isoformat(), "requested_days": window_days, "days_with_data": len(all_days),
                   "days_used": n_use, "dates_used": [d.isoformat() for d in use["days"]], "missing_days": [d.isoformat() for d in missing],
                   "total_nc_rev": round(use["total"], 2), "total_nc_orders": use["orders"]},
        "curve": [{"hour": h, "label": f"{h:02d}:00", "nc_rev": round(use.get("hour_rev", [0.0] * 24)[h], 2), "share": use["share"][h], "cum_end": use["cum"][h + 1],
                   "cum_start": use["cum"][h]} for h in range(24)] if loaded else [],
        "mean_of_ratios_share": (share_at(use["mean_of_ratios"], [use["mean_of_ratios"][i + 1] - use["mean_of_ratios"][i] for i in range(24)], hours)
                                 if loaded and use["mean_of_ratios"] else None),
        "dow_check": {"weekday_days": len(weekday["days"]), "weekend_days": len(weekend["days"]), "same_weekday_days": len(same["days"]),
                      "weekday_share": share_at(weekday["cum"], weekday["share"], hours) if weekday["total"] > 0 else None,
                      "weekend_share": share_at(weekend["cum"], weekend["share"], hours) if weekend["total"] > 0 else None,
                      "same_weekday_share": share_at(same["cum"], same["share"], hours) if same["total"] > 0 else None,
                      "gap_points": gap, "differs": gap is not None and abs(gap) > DOW_GAP_POINTS},
        "stale": {"newest_closed_day": newest.isoformat() if newest else None, "age_days": age, "stale_after_days": STALE_AFTER_DAYS,
                  "is_stale": newest is None or age > STALE_AFTER_DAYS},
    })
    return out


def build_orders(rows: Iterable[Sequence], today: dt.date, hours: float, so_far: float | None, dow: str = "all", window_days: int = WINDOW_DAYS) -> dict:
    """End-of-day new customers (first orders), from an ORDER-COUNT share curve: share of hour h = first orders in hour h over the window / all first orders
    in the window (ratio of sums, the same 30 closed days and the same same-weekday switch as the revenue curve; it is not the revenue share).
    Returns {loaded, share, projected_eod, reason}; no curve, or zero share by this time, means no projection."""
    first, last = window_dates(today, window_days)
    cells = _by_day(rows, first, last)
    swapped = {d: [[c[1], c[0]] for c in day] for d, day in cells.items()}     # orders in the 'revenue' slot: share_curve then builds the order share
    use_days = [d for d in swapped if dow != "same" or d.weekday() == today.weekday()]
    curve = share_curve(swapped, use_days)
    out = {"loaded": False, "share": None, "projected_eod": None, "days_used": len(curve["days"]), "reason": None}
    if len(curve["days"]) < MIN_DAYS or curve["total"] <= 0:
        out["reason"] = f"Only {len(curve['days'])} closed day(s) of first orders by hour are loaded; at least {MIN_DAYS} are needed."
        return out
    out["loaded"] = True
    sh = share_at(curve["cum"], curve["share"], hours)
    out["share"] = sh
    if sh <= 0:
        out["reason"] = "Too early in the day: no first orders have landed by this time on a typical day."
    elif so_far is not None:
        out["projected_eod"] = so_far / sh
    return out
