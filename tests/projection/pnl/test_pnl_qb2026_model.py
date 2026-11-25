"""The qb2026 chart engine: no database. Subtotals against the books layout, old-format months tie to the sheet chart, trailing-window rates and fixed
amounts re-derive when another month is loaded, typed steps and months, revenue is exactly what the sheet chart gets, the sheet chart never reads qb data."""
from __future__ import annotations

import copy
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.projection.interfaces import MONTHS, add_months                      # noqa: E402
from app.projection.pnl import charts                                         # noqa: E402
from app.projection.pnl import model as M                                      # noqa: E402
from app.projection.pnl.charts import qb2026 as Q                              # noqa: E402
from pnl_helpers import QB26_ACCOUNTS, qb26_expected                           # noqa: E402

TODAY = date(2026, 11, 24)
OCT, SEP, AUG = date(2026, 10, 1), date(2026, 9, 1), date(2026, 8, 1)
CODE_TO_KEY = {code: key for key, code in Q.ACCOUNTS.items()}


def qb_books(factors: dict[date, float]) -> dict[str, dict[date, float]]:
    """{line key: {month: value}} loaded under the new chart for each month, amounts scaled by the month's factor."""
    out: dict[str, dict[date, float]] = {}
    for name, base in QB26_ACCOUNTS:
        key = CODE_TO_KEY[name.split(" ", 1)[0]]
        for m, f in factors.items():
            out.setdefault(key, {})[m] = round(base * f, 2)
    return out


def sheet_actuals(through=date(2026, 7, 1), n=19):
    months = [add_months(through, -i) for i in range(n - 1, -1, -1)]
    vals = dict(dtc_rev=800_000, amazon_rev=200_000, tiktok_rev=1_000, amazon_fba_fees=20_000, amazon_seller_fees=15_000, china_cogs=100_000,
                fulfillment_services=100_000, inbound_shipping=10_000, shopify_subscriptions=9_000, seller_fees=30_000, mkt_facebook_ads=300_000,
                mkt_google_ads=10_000, mkt_bing_ads=500, marketing_agency=10_000, marketing_services=5_000, accounting=2_000, marketing_software=3_000,
                memberships=8_000, bank_fees=400, miscellaneous=600, interest_expense=25_000, headcount=15_000, insurance=1_000, office_admin=600)
    return {k: {m: float(v) for m in months} for k, v in vals.items()}


def upstream(start=date(2026, 8, 1)):
    drivers = SimpleNamespace(nc_rev={m: 300_000.0 for m in MONTHS if m >= start}, amazon_rev={m: 100_000.0 for m in MONTHS if m >= start}, tiktok_rev={},
                              sales_tax={m: -0.07 * 800_000 for m in MONTHS if m >= start}, spend_total={m: 250_000.0 for m in MONTHS},
                              marketing_spend={m: 250_000.0 for m in MONTHS}, spend_amazon_ads={}, spend_meta={m: 225_000.0 for m in MONTHS},
                              spend_google={m: 25_000.0 for m in MONTHS}, spend_tiktok={}, spend_bing={}, feeds_through=date(2026, 10, 1))
    cohorts = SimpleNamespace(repeat_rev={m: 500_000.0 for m in MONTHS})
    return drivers, cohorts


SHEET_DRIVERS = {
    "amazon_fba_fees": [{"from": None, "mode": "rate", "base": "amazon_rev", "rate_kind": "window"}],
    "china_cogs": [{"from": None, "mode": "rate", "base": "total_revenue", "rate_kind": "typed", "rate": 0.1}],
    "fulfillment_services": [{"from": None, "mode": "rate", "base": "dtc_rev", "rate_kind": "window"}],
    "marketing_agency": [{"from": None, "mode": "carry"}], "accounting": [{"from": None, "mode": "fixed", "value": 2500.0}],
    "other_business_expenses": [{"from": None, "mode": "rate", "base": "total_revenue", "rate_kind": "window"}],
    "headcount": [{"from": None, "mode": "headcount"}],
}


def qb_drivers() -> dict[str, list[dict]]:
    rates = {"shipping_income": "dtc_sales", "refunds_discounts": "gross_sales", "cogs_amazon": "amazon_sales", "cogs_shopify": "dtc_sales",
             "cogs_tiktok": "tiktok_sales", "threepl_services": "dtc_sales", "fba_fees": "amazon_sales", "adyen_fees": "dtc_sales",
             "paypal_fees": "dtc_sales", "shopify_fees": "dtc_sales", "amazon_fees": "amazon_sales", "tiktok_fees": "tiktok_sales",
             "amazon_ads": "amazon_sales", "tiktok_ads": "tiktok_sales"}
    out = {k: [{"from": None, "mode": "rate", "base": b, "rate_kind": "window"}] for k, b in rates.items()}
    for k in ("creative_agency", "email_agency", "accounting", "chargeback_management", "consulting", "customer_service", "legal", "office_supplies",
              "software_marketing", "software_operations", "bank_fees", "other_income", "interest_expense"):
        out[k] = [{"from": None, "mode": "books_mean"}]
    return out


def both(qb: dict | None = None, *, params=None, qb_overrides=None, qb_drv=None, settings=None):
    """(sheet output, qb2026 output) for the same inputs."""
    drivers, cohorts = upstream()
    inp = M.PnlInputs(actuals=sheet_actuals(), drivers=copy.deepcopy(SHEET_DRIVERS), settings=settings or {}, today=TODAY,
                      qb_actuals=qb or {}, qb_overrides=qb_overrides or {}, qb_drivers=qb_drv if qb_drv is not None else qb_drivers())
    params = params or {}
    old = M.compute_pnl(inp, drivers, cohorts, params)
    return old, Q.compute(inp, drivers, cohorts, params, copy.copy(old))


def val(out, key, m):
    return out.lines[key].get(m)


# ---------------------------------------------------------------------------------------------------------------- books months

def test_october_like_month_reproduces_every_subtotal():
    _, q = both(qb_books({OCT: 1.0}))
    ex = qb26_expected(1.0)
    for key, want in ex.items():
        assert val(q, key, OCT) == pytest.approx(want, abs=0.005), key
    assert q.month_status[OCT] == "books"
    assert q.books_through == OCT and q.chart == "qb2026"


def test_leaves_are_the_loaded_values_and_missing_stays_blank():
    books = qb_books({OCT: 1.0})
    del books["tiktok_fees"]
    _, q = both(books)
    assert val(q, "tiktok_fees", OCT) is None
    assert val(q, "adyen_fees", OCT) == 25_000.0
    assert val(q, "seller_fees_total", OCT) == pytest.approx(25_000 + 18_000 + 12_000 + 9_000)


def test_legacy_rollups_are_zero_in_new_format_months_and_hold_history_before():
    _, q = both(qb_books({OCT: 1.0}))
    for k in ("legacy_income_adj", "legacy_cogs_other", "legacy_expenses_other"):
        assert val(q, k, OCT) == 0.0 and val(q, k, add_months(OCT, 1)) == 0.0
    assert val(q, "legacy_cogs_other", date(2026, 3, 1)) == 10_000.0                 # inbound shipping, which has no account in the new books
    assert val(q, "legacy_expenses_other", date(2026, 3, 1)) == pytest.approx(500 + 5_000 + 15_000 + 1_000 + 600 + 8_000)


# ---------------------------------------------------------------------------------------------------------------- old-format months

@pytest.mark.parametrize("with_books", [True, False])
def test_old_format_months_tie_to_the_sheet_chart(with_books):
    old, q = both(qb_books({OCT: 1.0}) if with_books else {})
    checked = 0
    for m in MONTHS:
        if q.month_status[m] != "old":
            continue
        assert val(q, "total_income", m) == pytest.approx(old.lines["total_revenue"][m], abs=0.005), m
        assert val(q, "total_cogs", m) == pytest.approx(old.lines["total_cogs"][m], abs=0.005), m
        assert val(q, "net_income", m) == pytest.approx(old.lines["noi"][m], abs=0.005), m
        checked += 1
    assert checked == (len([m for m in MONTHS if m < OCT]) if with_books else len(MONTHS))


def test_old_months_are_marked_and_the_status_words():
    _, q = both(qb_books({OCT: 1.0}))
    assert q.month_status[date(2026, 3, 1)] == "old" and q.month_status[OCT] == "books"
    assert q.month_status[date(2026, 11, 1)] == "projected" and q.old_format_through == date(2026, 9, 1)
    _, q0 = both({})
    assert set(q0.month_status.values()) == {"old"} and any("No new-format books" in w for w in q0.warnings)


def test_a_gap_month_is_old_format_and_the_window_skips_it():
    _, q = both(qb_books({AUG: 1.0, OCT: 2.0}))
    assert q.month_status[SEP] == "old" and q.month_status[AUG] == "books" and q.month_status[OCT] == "books"
    assert q.window == [AUG, OCT]


# ---------------------------------------------------------------------------------------------------------------- projected months

NOV = date(2026, 11, 1)


def test_projected_revenue_is_exactly_what_the_sheet_chart_gets_and_no_tax_is_deducted():
    old, q = both(qb_books({OCT: 1.0}))
    for m in (NOV, date(2027, 6, 1)):
        assert val(q, "dtc_sales", m) == old.lines["dtc_rev"][m] == 800_000.0
        assert val(q, "amazon_sales", m) == old.lines["amazon_rev"][m]
        assert val(q, "gross_sales", m) == old.lines["dtc_rev"][m] + old.lines["amazon_rev"][m] + old.lines["tiktok_rev"][m]
        assert old.lines["sales_tax"][m] != 0 and val(q, "legacy_income_adj", m) == 0.0
        assert val(q, "total_income", m) == pytest.approx(val(q, "gross_sales", m) + val(q, "shipping_income", m) + val(q, "refunds_discounts", m))


def test_one_month_window_rates_and_fixed_amounts_are_that_month():
    _, q = both(qb_books({OCT: 1.0}))
    assert q.window == [OCT]
    ex_dtc = 800_000.0
    assert val(q, "cogs_shopify", NOV) == pytest.approx(80_000 / ex_dtc * 800_000)
    assert val(q, "refunds_discounts", NOV) == pytest.approx(-150_000 / 901_000 * val(q, "gross_sales", NOV))      # negative: reduces income
    assert val(q, "refunds_discounts", NOV) < 0
    assert val(q, "fba_fees", NOV) == pytest.approx(12_000 / 100_000 * val(q, "amazon_sales", NOV))
    assert val(q, "accounting", NOV) == 2_500.0 and val(q, "interest_expense", NOV) == 30_000.0 and val(q, "other_income", NOV) == 300.0
    assert val(q, "facebook_ads", NOV) == 225_000.0 and val(q, "google_ads", NOV) == 25_000.0                # the spend plan's typed spend
    info = q.driver_info
    assert info["cogs_shopify"]["rate"] == pytest.approx(0.1) and info["cogs_shopify"]["base"] == "dtc_sales" and info["cogs_shopify"]["window"] == [OCT.isoformat()]
    assert info["accounting"]["value"] == 2_500.0 and info["accounting"]["mode"] == "books_mean"


def test_loading_more_months_re_derives_every_rate_and_fixed_amount():
    _, one = both(qb_books({OCT: 1.0}))
    _, two = both(qb_books({OCT: 1.0, SEP: 0.5}))
    _, three = both(qb_books({OCT: 1.0, SEP: 0.5, AUG: 2.0}))
    # rates are sums over sums, so a bigger earlier month weighs more; here every line scales together, so the rate is unchanged by construction
    assert val(two, "cogs_shopify", NOV) == pytest.approx(val(one, "cogs_shopify", NOV))
    # a fixed amount is the mean of the books months
    assert val(one, "consulting", NOV) == 12_000.0
    assert val(two, "consulting", NOV) == pytest.approx((12_000 + 6_000) / 2)
    assert val(three, "consulting", NOV) == pytest.approx((12_000 + 6_000 + 24_000) / 3)
    assert three.window == [AUG, SEP, OCT]
    # a rate that is not proportional across months: change only the line
    books = qb_books({OCT: 1.0, SEP: 1.0})
    books["adyen_fees"][SEP] = 50_000.0
    _, r = both(books)
    assert r.driver_info["adyen_fees"]["rate"] == pytest.approx((25_000 + 50_000) / (800_000 + 800_000))
    assert val(r, "adyen_fees", NOV) == pytest.approx(75_000 / 1_600_000 * 800_000)


def test_the_window_setting_limits_months_and_only_books_months_count():
    books = qb_books({OCT: 1.0, SEP: 1.0, AUG: 1.0})
    books["consulting"][AUG] = 90_000.0
    _, wide = both(books)
    _, narrow = both(books, params={"engine": {"rate_window_months": 2}})
    assert narrow.window == [SEP, OCT] and val(narrow, "consulting", NOV) == 12_000.0
    assert val(wide, "consulting", NOV) == pytest.approx((90_000 + 12_000 + 12_000) / 3)
    # projected months never feed the window: Dec reads the same books months as Nov
    assert val(wide, "consulting", date(2026, 12, 1)) == val(wide, "consulting", NOV)


def test_typed_fixed_step_and_typed_month_and_other_modes():
    drv = {"consulting": {"segments": [{"from": None, "mode": "books_mean"}, {"from": "2027-01-01", "mode": "fixed", "value": 9_999.0}]},
           "legal": {"segments": [{"from": None, "mode": "carry"}]},
           "cogs_shopify": {"segments": [{"from": None, "mode": "rate", "base": "dtc_sales", "rate_kind": "typed", "rate": 0.2}]}}
    _, q = both(qb_books({OCT: 1.0}), params={"qb26": {"drivers": drv}}, qb_overrides={"facebook_ads": {NOV: 777.0}, "accounting": {NOV: 1.0}})
    assert val(q, "consulting", date(2026, 12, 1)) == 12_000.0 and val(q, "consulting", date(2027, 1, 1)) == 9_999.0
    assert val(q, "legal", NOV) == 3_000.0
    assert val(q, "cogs_shopify", NOV) == pytest.approx(0.2 * 800_000)
    assert val(q, "facebook_ads", NOV) == 777.0 and val(q, "facebook_ads", date(2026, 12, 1)) == 225_000.0
    assert val(q, "accounting", NOV) == 1.0 and val(q, "accounting", date(2026, 12, 1)) == 2_500.0


def test_inflation_applies_like_the_sheet_chart_cost_lines_only():
    _, flat = both(qb_books({OCT: 1.0}))
    _, hot = both(qb_books({OCT: 1.0}), params={"engine": {"opex_inflation": 0.12, "cogs_inflation": 0.24}})
    k = (date(2027, 10, 1).year - OCT.year) * 12 + date(2027, 10, 1).month - OCT.month
    assert k == 12
    m = date(2027, 10, 1)
    assert val(hot, "consulting", m) == pytest.approx(val(flat, "consulting", m) * 1.12)
    assert val(hot, "cogs_shopify", m) == pytest.approx(val(flat, "cogs_shopify", m) * 1.24)
    assert val(hot, "refunds_discounts", m) == val(flat, "refunds_discounts", m)            # income-side lines are not inflated
    assert val(hot, "shipping_income", m) == val(flat, "shipping_income", m)


def test_totals_ratios_fiscal_year_and_value_in_the_new_chart():
    _, q = both(qb_books({OCT: 1.0}))
    m = date(2027, 3, 1)
    assert val(q, "gross_profit", m) == pytest.approx(val(q, "total_income", m) - val(q, "total_cogs", m))
    assert val(q, "noi", m) == pytest.approx(val(q, "gross_profit", m) - val(q, "total_expenses", m))
    assert val(q, "net_income", m) == pytest.approx(val(q, "noi", m) + val(q, "other_income", m) - val(q, "interest_expense", m))
    assert val(q, "noi_margin", m) == pytest.approx(val(q, "noi", m) / val(q, "total_income", m))
    assert val(q, "refunds_pct", m) == pytest.approx(val(q, "refunds_discounts", m) / val(q, "gross_sales", m))
    fy = q.fy[2027]
    assert fy["noi"] == pytest.approx(sum(val(q, "noi", x) for x in MONTHS if x.year == 2027))
    assert fy["noi_margin"] == pytest.approx(fy["noi"] / fy["total_income"])
    assert fy["total_revenue"] == pytest.approx(fy["total_income"]) and q.ev["basis_noi"] == fy["noi"]


# ---------------------------------------------------------------------------------------------------------------- the sheet chart never reads qb data

def test_the_sheet_chart_output_is_identical_with_and_without_qb_data():
    drivers, cohorts = upstream()
    base = dict(actuals=sheet_actuals(), drivers=copy.deepcopy(SHEET_DRIVERS), settings={}, today=TODAY)
    a = M.compute_pnl(M.PnlInputs(**base), drivers, cohorts, {})
    b = M.compute_pnl(M.PnlInputs(**base, qb_actuals=qb_books({OCT: 1.0, SEP: 3.0}), qb_drivers=qb_drivers(), qb_overrides={"facebook_ads": {NOV: 1.0}}), drivers, cohorts, {})
    assert a.lines == b.lines and a.fy == b.fy and a.driver_info == b.driver_info and a.month_status == b.month_status and a.chart == "sheet"


def test_chart_selection_reads_the_scenario_params():
    assert charts.chart_of(None) == "sheet" and charts.chart_of({}) == "sheet" and charts.chart_of({"chart": "sheet"}) == "sheet"
    assert charts.chart_of({"chart": "qb2026"}) == "qb2026" and charts.chart_of({"chart": "nonsense"}) == "sheet"
