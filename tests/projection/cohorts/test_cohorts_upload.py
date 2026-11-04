"""Cohort upload: layouts, cell parsing, the closed-months-only rules, and the diff shown before anything is written."""
from __future__ import annotations

from datetime import date

import pytest
from conftest import make_cohort

from app.projection.cohorts import upload as up
from app.projection.cohorts.engine import Cohort

D = date
HEADER = "Cohort Month,New Customers,90-Day Repeat Rate,CAC,Payback Period (Months),First Order (Realized),First Order," + ",".join(f"M{i}" for i in range(1, 37))


def wide_line(month: str, n, first, cum: list, repeat="0.5", cac="80", pay="", realized=None) -> str:
    cells = [str(x) for x in cum] + ["0"] * (36 - len(cum))
    return ",".join([month, str(n), repeat, cac, pay, str(realized if realized is not None else first), str(first)] + cells)


def wide(*lines: str) -> bytes:
    return ("\n".join([HEADER, *lines]) + "\n").encode()


# ---------------------------------------------------------------------------------------------------------------- cells

@pytest.mark.parametrize("raw,expected", [("2026-10", D(2026, 10, 1)), ("2026-10-17", D(2026, 10, 1)), ("Oct 2026", D(2026, 10, 1)),
                                          ("October 2026", D(2026, 10, 1)), ("Oct-26", D(2026, 10, 1)), ("10/1/2026", D(2026, 10, 1)),
                                          ("46296", D(2026, 10, 1)), ("2024-02", D(2024, 2, 1))])
def test_month_cells(raw, expected):
    assert up.parse_month_cell(raw) == expected


@pytest.mark.parametrize("raw", ["", "soon", "2026-13", "13/1/2026", "Foo 2026"])
def test_bad_month_cells_are_rejected(raw):
    with pytest.raises(up.UploadError):
        up.parse_month_cell(raw)


def test_number_cells():
    assert up.parse_number("$1,234.50") == 1234.5 and up.parse_number("(12)") == -12 and up.parse_number("12%") == pytest.approx(0.12)
    assert up.parse_number("") is None and up.parse_number("-") is None and up.parse_number(None) is None
    with pytest.raises(up.UploadError):
        up.parse_number("abc")
    with pytest.raises(up.UploadError):
        up.parse_number("nan")


# ---------------------------------------------------------------------------------------------------------------- layouts

def test_wide_layout_of_the_sheet_tab():
    p = up.parse_csv(wide(wide_line("2026-08", 8417, 63.47, [76.43, 93.86]), wide_line("2026-09", 5641, 71.02, [78.91])))
    assert p.layout == "wide" and [r.month for r in p.rows] == [D(2026, 8, 1), D(2026, 9, 1)]
    r = p.rows[0]
    assert r.new_customers == 8417 and r.first_order == pytest.approx(63.47) and r.cum[1] == pytest.approx(76.43) and r.cum[3] == 0.0
    assert r.repeat_90d == 0.5 and r.cac == 80 and r.payback_months is None and len(p.sha256) == 64


def test_long_layout_of_the_shopify_cohort_export():
    csv = ("Month,Months since first purchase,Customers,Amount spent per customer,Customers in cohort\n"
           "2026-08,0,100,60,100\n2026-08,1,40,75,100\n2026-08,2,30,85,100\n2026-09,0,50,70,50\n2026-09,1,10,72,50\n")
    p = up.parse_csv(csv.encode())
    assert p.layout == "long"
    aug, sep = sorted(p.rows, key=lambda r: r.month)
    assert aug.new_customers == 100 and aug.first_order == 60 and aug.cum == {1: 75, 2: 85}
    assert sep.new_customers == 50 and sep.cum == {1: 72}


def test_unrecognised_layout_oversize_and_empty_files_are_rejected_with_a_message():
    with pytest.raises(up.UploadError, match="unrecognised layout"):
        up.parse_csv(b"a,b,c\n1,2,3\n")
    with pytest.raises(up.UploadError, match="no data rows"):
        up.parse_csv((HEADER + "\n").encode())
    with pytest.raises(up.UploadError, match="too large"):
        up.parse_csv(b"x" * (up.MAX_BYTES + 1))
    with pytest.raises(up.UploadError, match="UTF-8"):
        up.parse_csv(b"\xff\xfe\x00bad")


def test_duplicate_month_and_bad_cell_name_the_line():
    with pytest.raises(up.UploadError, match="line 3.*twice"):
        up.parse_csv(wide(wide_line("2026-08", 100, 60, [70]), wide_line("2026-08", 100, 60, [70])))
    with pytest.raises(up.UploadError, match="line 2"):
        up.parse_csv(wide(wide_line("2026-08", "many", 60, [70])))


def test_tab_and_semicolon_delimiters_are_detected():
    tsv = wide(wide_line("2026-08", 100, 60, [70])).decode().replace(",", "\t").encode()
    assert up.parse_csv(tsv).rows[0].new_customers == 100


# ---------------------------------------------------------------------------------------------------------------- rules

def test_default_through_is_the_last_closed_month_and_the_filename_end_date_can_pull_it_back():
    today = D(2026, 11, 3)
    assert up.default_through(today) == D(2026, 10, 1)
    assert up.default_through(today, "cohorts-revenue-2023-11-02-to-2026-11-02.csv") == D(2026, 10, 1)       # Nov 2 is mid-month: Oct was the last complete
    assert up.default_through(today, "cohorts-2023-11-01-to-2026-10-31.csv") == D(2026, 10, 1)               # Oct 31 is a month end: Oct counts
    assert up.default_through(today, "cohorts-to-2026-09-15.csv") == D(2026, 8, 1)
    assert up.default_through(today, "x.csv", through=D(2026, 8, 17)) == D(2026, 8, 1)
    assert up.default_through(today, "x.csv", through=D(2027, 1, 1)) == D(2026, 10, 1)                         # never later than the last closed month
    assert up.default_through(D(2026, 1, 5), "to-2026-01-04.csv") == D(2025, 12, 1)                             # across a year boundary


def test_rules_skip_open_and_tiny_cohorts_blank_open_month_cells_and_drop_zeros():
    csv = wide(
        wide_line("2026-08", 8417, 63.47, [76.43, 93.86, 99.0, 0, 120.0]),     # M3 lands Nov-26 (open), M4 is a zero, M5 lands Jan-27
        wide_line("2026-10", 3614, 68.32, [69.92, 69.95]),                      # Oct cohort: M1 lands Nov-26 (open)
        wide_line("2026-11", 234, 69.1, [70.3]),                                # in-progress cohort
        wide_line("2023-12", 1, 43.89, []),                                     # one customer
    )
    a = up.apply_rules(up.parse_csv(csv), D(2026, 10, 1), min_customers=10)
    assert [r.month for r in a.rows] == [D(2026, 8, 1), D(2026, 10, 1)]
    aug = a.rows[0]
    assert aug.cum == {1: 76.43, 2: 93.86}                                       # Sep and Oct only
    assert a.rows[1].cum == {}
    reasons = {s["month"]: s["reason"] for s in a.skipped}
    assert reasons["2026-11-01"] == "month not over" and "fewer than" in reasons["2023-12-01"]
    assert a.blanked_open_month == 4                                             # Aug M3 (Nov-26) and M5 (Jan-27); Oct M1 and M2
    assert a.zeros_dropped >= 1                                                  # the explicit zero at Aug M4, plus the file's padding zeros


def test_cumulative_that_falls_is_a_warning_not_an_error():
    a = up.apply_rules(up.parse_csv(wide(wide_line("2026-01", 500, 60, [70, 65]))), D(2026, 10, 1))
    assert a.rows[0].cum == {1: 70, 2: 65} and any("falls at month 2" in w for w in a.warnings)


def test_first_order_missing_warns():
    a = up.apply_rules(up.parse_csv(wide(wide_line("2026-01", 500, 0, [70]))), D(2026, 10, 1))
    assert a.rows[0].first_order is None and any("no first-order" in w for w in a.warnings)


# ---------------------------------------------------------------------------------------------------------------- diff

def existing(month=D(2026, 8, 1), n=8417, first=63.47, ratios=(0.2, 0.3)) -> Cohort:
    return make_cohort(month, n=n, first=first, ratios=list(ratios), repeat_90d=0.5, cac=80.0, first_order_realized=first)


def test_diff_counts_added_changed_unchanged_and_kept():
    have = [existing(), existing(D(2026, 7, 1), n=10138, first=61.39, ratios=(0.2,))]
    new_aug = existing()                                         # identical to what is stored
    csv = wide(
        wide_line("2026-08", new_aug.new_customers, new_aug.first_order, [round(v, 6) for v in new_aug.cum if v]),     # unchanged
        wide_line("2026-07", 10138, 61.39, [round(have[1].cum[0], 6) + 5.0, 99.0]),                                    # M1 revised, M2 new
        wide_line("2026-09", 5641, 71.02, [78.91]),                                                                       # new cohort
    )
    a = up.apply_rules(up.parse_csv(csv), D(2026, 10, 1))
    d = up.diff(a, have)
    assert d["cohorts_loaded"] == 3 and d["cohorts_added"] == 1 and d["cohorts_changed"] == 1 and d["cohorts_unchanged"] == 1
    assert d["cells_added"] == 2 and d["cells_changed"] == 1            # Jul M2 and the new Sep cohort's M1; Jul M1 revised
    top = d["changes"][0]
    assert top["month"] == "2026-07-01" and top["field"] == "M1" and top["new"] - top["old"] == pytest.approx(5.0, abs=1e-4)


def test_diff_reports_stored_cells_the_file_does_not_hold_and_never_plans_to_erase_them():
    have = [existing(ratios=(0.2, 0.3, 0.1))]
    csv = wide(wide_line("2026-08", 8417, 63.47, [round(have[0].cum[0], 6)]))          # file only has M1
    a = up.apply_rules(up.parse_csv(csv), D(2026, 10, 1))
    d = up.diff(a, have)
    assert d["cells_kept_not_in_file"] == 2 and d["cells_changed"] == 0


def test_newest_closed_cohort_keeps_no_90_day_repeat_or_cac_until_it_matures():
    csv = wide(wide_line("2026-09", 5641, 71.02, [78.91], repeat="0.31", cac="74.5"), wide_line("2026-10", 3614, 68.32, [], repeat="0.075", cac="5.26"))
    a = up.apply_rules(up.parse_csv(csv), D(2026, 10, 1))
    sep, octo = a.rows
    assert (sep.repeat_90d, sep.cac) == (0.31, 74.5)
    assert (octo.repeat_90d, octo.cac) == (None, None) and octo.first_order == pytest.approx(68.32)
    assert a.held_back == [{"month": "2026-10-01", "fields": ["repeat_90d", "cac"]}]
    d = up.diff(a, [])
    assert d["held_back"] == a.held_back


def test_rounding_of_stored_rates_is_not_a_revision():
    have = [existing()]
    have = [Cohort(month=have[0].month, new_customers=have[0].new_customers, first_order=have[0].first_order, cum=have[0].cum,
                   first_order_realized=have[0].first_order, repeat_90d=0.129032, cac=17.825060, payback_months=2.843756)]              # stored to six decimals
    csv = wide(wide_line("2026-08", 8417, 63.47, [round(have[0].cum[0], 6), round(have[0].cum[1], 6)], repeat="0.12903225806451613", cac="17.825060483870963",
                         pay="2.843755848774091"))
    d = up.diff(up.apply_rules(up.parse_csv(csv), D(2026, 10, 1)), have)
    assert d["cohorts_changed"] == 0 and d["cohorts_unchanged"] == 1 and d["changes_total"] == 0
