"""Calendar helpers and period building: month boundaries, ISO weeks, the pacing date."""
from datetime import date

import pytest

from app.projection.pacing import periods
from app.projection.pacing.rollup import make_periods


def test_data_through_is_yesterday_and_default_month_follows_it():
    assert periods.data_through(date(2026, 11, 3)) == date(2026, 11, 2)
    assert periods.default_month(date(2026, 11, 3)) == date(2026, 11, 1)
    assert periods.default_month(date(2026, 11, 1)) == date(2026, 10, 1)          # the 1st shows the month that just ended
    assert periods.default_month(date(2027, 1, 1)) == date(2026, 12, 1)          # across a year


def test_days_elapsed_running_past_and_future_month():
    today = date(2026, 11, 3)
    assert periods.days_elapsed(date(2026, 11, 1), today) == 2
    assert periods.days_elapsed(date(2026, 10, 1), today) == 31
    assert periods.days_elapsed(date(2026, 12, 1), today) == 0
    assert periods.days_elapsed(date(2026, 11, 1), date(2026, 11, 1)) == 0
    assert periods.days_elapsed(date(2026, 11, 1), date(2026, 12, 1)) == 30      # the month is complete on the 1st of the next


def test_leap_february_and_month_math():
    assert periods.days_in_month(date(2028, 2, 10)) == 29
    assert periods.days_in_month(date(2027, 2, 10)) == 28
    assert periods.add_months(date(2026, 11, 1), 3) == date(2027, 2, 1)
    assert periods.add_months(date(2026, 1, 1), -1) == date(2025, 12, 1)


def test_iso_week_keys_across_year_boundaries():
    assert periods.iso_week_key(date(2025, 1, 1)) == "2025-W01"
    assert periods.iso_week_key(date(2024, 12, 30)) == "2025-W01"                # Monday of 2025-W01 is in 2024
    assert periods.iso_week_key(date(2026, 1, 1)) == "2026-W01"
    assert periods.iso_week_key(date(2027, 1, 3)) == "2026-W53"
    assert periods.week_start(date(2026, 11, 3)) == date(2026, 11, 2)            # Tuesday -> Monday


def test_as_of_override_env_and_default(monkeypatch):
    monkeypatch.setenv(periods.ENV_AS_OF, "2026-05-17")
    assert periods.as_of() == date(2026, 5, 17)
    assert periods.as_of("2026-06-01") == date(2026, 6, 1)
    assert periods.as_of(date(2026, 7, 4)) == date(2026, 7, 4)
    monkeypatch.delenv(periods.ENV_AS_OF)
    assert isinstance(periods.as_of(), date)
    with pytest.raises(ValueError):
        periods.as_of("not-a-date")


def test_make_periods_month_partial_flag_and_days():
    ps = make_periods("month", date(2026, 9, 1), date(2026, 11, 30), date(2026, 11, 2))
    assert [p.key for p in ps] == ["2026-09", "2026-10", "2026-11"]
    assert [p.partial for p in ps] == [False, False, True]
    assert [p.days for p in ps] == [30, 31, 2]
    assert ps[-1].days_in_period == 30


def test_make_periods_weeks_start_on_monday_and_first_week_is_short():
    ps = make_periods("week", date(2024, 12, 30), date(2025, 1, 12), date(2026, 1, 1))
    assert [p.key for p in ps] == ["2025-W01", "2025-W02"]
    assert ps[0].start == date(2024, 12, 30) and ps[0].days == 5                 # data starts 2025-01-01
    assert ps[1].days == 7 and not ps[1].partial


def test_make_periods_day_grain_and_empty_range():
    ps = make_periods("day", date(2026, 11, 1), date(2026, 11, 9), date(2026, 11, 2))
    assert [p.key for p in ps] == ["2026-11-01", "2026-11-02"]                   # clipped to data_through
    assert make_periods("day", date(2026, 11, 5), date(2026, 11, 9), date(2026, 11, 2)) == []
    with pytest.raises(ValueError):
        make_periods("year", date(2026, 1, 1), date(2026, 2, 1), date(2026, 2, 1))
