"""QuickBooks upload: parsing (csv and xlsx), account mapping, closed-month rule, diff and the diff-hash handshake. No database."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.projection.pnl import qb                                   # noqa: E402
from app.projection.pnl.xlsx_read import XlsxError, read_first_sheet     # noqa: E402
from pnl_helpers import make_xlsx, qb_rows, to_csv                        # noqa: E402

TODAY = date(2026, 11, 3)
MAP = {"shopify sales": "dtc_rev", "amazon sales": "amazon_rev", "amazon fba fees": "amazon_fba_fees", "china cogs": "china_cogs",
       "other business expenses": "other_direct", "bank fees": "bank_fees", "accounting": "accounting", "fulfillment services": "fulfillment_services",
       "facebook ads": "mkt_facebook_ads"}


def test_csv_and_xlsx_parse_to_the_same_accounts():
    rows = qb_rows()
    a = qb.parse(to_csv(rows), "x.csv")
    b = qb.parse(make_xlsx(rows), "x.xlsx")
    assert [x.name for x in a.accounts] == [x.name for x in b.accounts]
    assert a.months == b.months == [date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1)]
    assert a.header_row == 5
    assert a.accounts[0].values[date(2026, 2, 1)] == 2000.0


def test_totals_and_subtotals_are_listed_as_ignored_not_loaded():
    p = qb.parse(to_csv(qb_rows()), "x.csv")
    names = {x["account"] for x in p.ignored}
    assert {"Total Income", "Gross Profit", "Net Operating Income", "Total for Expenses", "Total for Other Business Expenses",
            "Total Cost of Goods Sold"} <= names
    assert not any(a.name.lower().startswith("total") for a in p.accounts)


def test_section_headers_without_numbers_are_skipped_and_parent_name_with_numbers_is_one_account():
    p = qb.parse(to_csv(qb_rows()), "x.csv")
    obe = [a for a in p.accounts if a.name == "Other Business Expenses"]
    assert len(obe) == 1 and obe[0].values[date(2026, 1, 1)] == 5.0


def test_amount_formats():
    rows = [["", "Jan 2026", "Feb 2026", "Mar 2026"], ["Accounting", "$1,234.50", "(200.00)", "-"], ["Legal", "-$10", "", "12"]]
    p = qb.parse(to_csv(rows), "x.csv")
    acc = {a.name: a.values for a in p.accounts}
    assert acc["Accounting"] == {date(2026, 1, 1): 1234.5, date(2026, 2, 1): -200.0}
    assert acc["Legal"] == {date(2026, 1, 1): -10.0, date(2026, 3, 1): 12.0}


@pytest.mark.parametrize("labels", [["Jan 2026", "Feb 2026", "Mar 2026"], ["January 2026", "February 2026", "March 2026"], ["Jan-26", "Feb-26", "Mar-26"],
                                    ["2026-01", "2026-02", "2026-03"], ["1/1/2026", "2/1/2026", "3/1/2026"]])
def test_month_header_formats(labels):
    p = qb.parse(to_csv([["", *labels, "Total"], ["Accounting", 1, 2, 3, 6]]), "x.csv")
    assert p.months == [date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1)]


def test_excel_serial_dates_in_the_header():
    rows = [["", 46023.0, 46054.0, 46082.0], ["Accounting", 1.0, 2.0, 3.0]]          # 2026-01-01, 2026-02-01, 2026-03-01
    p = qb.parse(make_xlsx(rows), "x.xlsx")
    assert p.months == [date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1)]


@pytest.mark.parametrize("data,frag", [(b"", "empty"), (b"just,some,text\n1,2,3\n", "no month header"),
                                       (to_csv([["", "Jan 2026", "Feb 2026", "Mar 2026"], ["Expenses"]]), "no account rows"),
                                       (to_csv([["", "Jan 2026", "Jan 2026", "Mar 2026"], ["Accounting", 1, 2, 3]]), "repeats a month")])
def test_bad_files_get_a_plain_message(data, frag):
    with pytest.raises(qb.UploadError) as e:
        qb.parse(data, "x.csv")
    assert frag in str(e.value)


def test_xlsx_with_a_doctype_is_refused_and_a_non_zip_is_not_a_workbook():
    with pytest.raises(qb.UploadError):
        qb.parse(make_xlsx(qb_rows(), doctype=True), "x.xlsx")
    with pytest.raises(XlsxError):
        read_first_sheet(b"PK\x03\x04 not really a zip")


def test_oversized_file_is_refused():
    with pytest.raises(qb.UploadError):
        qb.parse(b"a" * (qb.MAX_BYTES + 1), "x.csv")


def test_preview_maps_sums_and_lists_unmapped():
    p = qb.parse(to_csv(qb_rows()), "x.csv")
    pv, cells = qb.preview(p, {k: v for k, v in MAP.items() if k != "bank fees"}, {}, TODAY)
    assert cells[("dtc_rev", date(2026, 2, 1))] == 2000.0
    assert [u["account"] for u in pv["unmapped"]] == ["Bank Fees"]
    assert pv["cells"] == {"total": len(cells), "new": len(cells), "changed": 0, "same": 0}
    assert pv["committable"] and len(pv["diff_sha256"]) == 64


def test_two_accounts_on_one_line_add():
    m = dict(MAP)
    m["amazon sales"] = "dtc_rev"
    pv, cells = qb.preview(qb.parse(to_csv(qb_rows()), "x.csv"), m, {}, TODAY)
    assert cells[("dtc_rev", date(2026, 1, 1))] == 1400.0


def test_open_and_out_of_axis_months_are_skipped_with_a_reason():
    rows = [["", "Dec 2024", "Oct 2026", "Nov 2026", "Dec 2026"], ["Accounting", 5, 6, 7, 8]]
    pv, cells = qb.preview(qb.parse(to_csv(rows), "x.csv"), MAP, {}, TODAY)
    assert sorted(m.isoformat() for (_, m) in cells) == ["2026-10-01"]
    reasons = {s["month"]: s["reason"] for s in pv["skipped_months"]}
    assert "not closed" in reasons["2026-11-01"] and "not closed" in reasons["2026-12-01"] and "Outside" in reasons["2024-12-01"]


def test_diff_new_changed_same_and_hash_changes_with_content():
    p = qb.parse(to_csv(qb_rows()), "x.csv")
    existing = {"accounting": {date(2026, 1, 1): 50.0, date(2026, 2, 1): 999.0}}
    pv, cells = qb.preview(p, MAP, existing, TODAY)
    assert pv["cells"]["changed"] == 1 and pv["cells"]["same"] == 1
    assert pv["changes"] == [{"line_key": "accounting", "month": "2026-02-01", "old": 999.0, "new": 60.0}]
    assert {m["month"]: m["status"] for m in pv["months"]}["2026-02-01"] == "changed"
    pv2, _ = qb.preview(p, MAP, {}, TODAY)
    assert pv["diff_sha256"] == pv2["diff_sha256"]                                 # the hash covers what would be written, not what exists
    other = to_csv(qb_rows([ "Jan 2026", "Feb 2026", "Mar 2026"])).replace(b"60.0", b"61.0")
    pv3, _ = qb.preview(qb.parse(other, "y.csv"), MAP, {}, TODAY)
    assert pv3["diff_sha256"] != pv["diff_sha256"]


def test_no_revenue_mapping_is_called_out():
    m = {k: v for k, v in MAP.items() if v not in ("dtc_rev", "amazon_rev")}
    pv, _ = qb.preview(qb.parse(to_csv(qb_rows()), "x.csv"), m, {}, TODAY)
    assert any("No revenue account" in n for n in pv["notes"])
