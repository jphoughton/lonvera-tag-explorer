"""Engine tests: pure functions, no database."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from app.projection.interfaces import MONTHS, add_months                      # noqa: E402
from app.projection.pnl import lines as L                                      # noqa: E402
from app.projection.pnl import model as M                                      # noqa: E402

THROUGH = date(2026, 7, 1)
TODAY = date(2026, 11, 3)


def months_to(through: date, n: int = 12):
    return [add_months(through, -i) for i in range(n - 1, -1, -1)]


def actuals(through=THROUGH, n=12, **vals):
    """Every key given gets the same monthly value for the last n months up to `through`."""
    out = {}
    for k, v in vals.items():
        out[k] = {m: float(v) for m in months_to(through, n)}
    return out


def upstream(nc=300_000.0, repeat=500_000.0, amazon=100_000.0, tax_rate=0.07, spend=250_000.0, start=date(2026, 8, 1)):
    drivers = SimpleNamespace(nc_rev={m: nc for m in MONTHS if m >= start}, amazon_rev={m: amazon for m in MONTHS if m >= start},
                              tiktok_rev={}, sales_tax={m: -tax_rate * (nc + repeat) for m in MONTHS if m >= start},
                              spend_total={m: spend for m in MONTHS}, marketing_spend={m: spend for m in MONTHS},
                              spend_amazon_ads={}, spend_meta={m: spend * 0.9 for m in MONTHS}, spend_google={m: spend * 0.1 for m in MONTHS},
                              spend_tiktok={}, spend_bing={})
    cohorts = SimpleNamespace(repeat_rev={m: repeat for m in MONTHS})
    return drivers, cohorts


def base_actuals():
    a = actuals(dtc_rev=800_000, amazon_rev=200_000, tiktok_rev=0, amazon_fba_fees=20_000, china_cogs=100_000, fulfillment_services=100_000,
                inbound_shipping=10_000, mkt_facebook_ads=300_000, marketing_agency=10_000, accounting=2_000, marketing_software=3_000,
                memberships=8_000, bank_fees=400, miscellaneous=600)
    return a


def drivers_default():
    return {
        "amazon_fba_fees": [{"from": None, "mode": "rate", "base": "amazon_rev", "rate_kind": "window"}],
        "china_cogs": [{"from": None, "mode": "rate", "base": "total_revenue", "rate_kind": "typed", "rate": 0.1}],
        "fulfillment_services": [{"from": None, "mode": "rate", "base": "dtc_rev", "rate_kind": "window"}],
        "marketing_agency": [{"from": None, "mode": "carry"}],
        "accounting": [{"from": None, "mode": "fixed", "value": 2500.0}],
        "marketing_software": [{"from": None, "mode": "rate", "base": "total_revenue", "rate_kind": "window"}],
        "memberships": [{"from": None, "mode": "avg", "window": 3}],
        "other_business_expenses": [{"from": None, "mode": "rate", "base": "total_revenue", "rate_kind": "window"}],
        "headcount": [{"from": None, "mode": "headcount"}],
    }


def run(**kw):
    drivers, cohorts = upstream(**kw.pop("up", {}))
    inp = M.PnlInputs(actuals=kw.pop("actuals", base_actuals()), overrides=kw.pop("overrides", {}), drivers=kw.pop("drv", drivers_default()),
                      settings=kw.pop("settings", {}), cogs_sku=kw.pop("cogs_sku", []), today=kw.pop("today", TODAY))
    return M.compute_pnl(inp, drivers, cohorts, kw.pop("params", {}))


# ---------------------------------------------------------------------------------------------------------------- actual months

def test_actual_through_is_the_last_loaded_closed_month():
    out = run()
    assert out.actual_through == THROUGH


def test_open_month_is_never_actual():
    a = base_actuals()
    a["accounting"][date(2026, 11, 1)] = 5.0                 # November 2026 is not closed on 2026-11-03
    out = run(actuals=a)
    assert out.actual_through == THROUGH


def test_actual_month_sums_children_and_computes_noi():
    out = run()
    m = date(2026, 3, 1)
    L_ = out.lines
    assert L_["total_revenue"][m] == pytest.approx(1_000_000.0)
    assert L_["amazon_fees"][m] == 20_000.0
    assert L_["cogs"][m] == 100_000.0
    assert L_["total_cogs"][m] == pytest.approx(20_000 + 100_000 + 100_000 + 10_000)         # Amazon + COGS + fulfillment + inbound
    assert L_["gross_profit"][m] == pytest.approx(1_000_000 - 230_000)
    assert L_["marketing_spend"][m] == 300_000.0
    assert L_["other_business_expenses"][m] == pytest.approx(1_000.0)                         # sum of its sub-lines in actual months
    assert L_["total_expenses"][m] == pytest.approx(300_000 + 13_000 + 1_000 + 0 + (2_000 + 8_000))
    assert L_["noi"][m] == pytest.approx(L_["gross_profit"][m] - L_["total_expenses"][m])


def test_blank_actual_cell_is_missing_not_zero_but_counts_as_zero_in_sums():
    out = run()
    m = date(2026, 3, 1)
    assert m not in out.lines["travel"]                                  # never loaded: blank
    assert out.lines["bank_fees"][m] == 400.0
    assert out.lines["other_business_expenses"][m] == pytest.approx(1000.0)


def test_inventory_shrinkage_counts_in_total_cogs():
    a = base_actuals()
    a["inventory_shrinkage"] = {date(2026, 2, 1): 22_631.40}
    out = run(actuals=a)
    base = out.lines["total_cogs"][date(2026, 3, 1)]
    assert out.lines["total_cogs"][date(2026, 2, 1)] == pytest.approx(base + 22_631.40)


def test_no_history_every_month_is_projected_and_warns():
    out = run(actuals={})
    assert out.actual_through is None
    assert out.warnings
    assert out.lines["noi"]                                           # still computes from drivers


# ---------------------------------------------------------------------------------------------------------------- drivers

def test_rate_driver_uses_trailing_window_over_base():
    out = run(settings={"engine": {"rate_window_months": 4}})
    aug = date(2026, 8, 1)
    # Amazon FBA: 20,000 / 200,000 = 10% of Amazon revenue (100,000 projected)
    assert out.lines["amazon_fba_fees"][aug] == pytest.approx(10_000.0)
    assert out.window == months_to(THROUGH, 4)


def test_rate_window_is_a_lever():
    a = base_actuals()
    a["amazon_fba_fees"][date(2026, 7, 1)] = 60_000.0                  # July is expensive; window 1 reads only July
    out1 = run(actuals=a, params={"engine": {"rate_window_months": 1}})
    out12 = run(actuals=a, params={"engine": {"rate_window_months": 12}})
    assert out1.lines["amazon_fba_fees"][date(2026, 8, 1)] == pytest.approx(0.3 * 100_000)
    assert out12.lines["amazon_fba_fees"][date(2026, 8, 1)] == pytest.approx((11 * 20_000 + 60_000) / (12 * 200_000) * 100_000)


def test_typed_rate_applies_to_projected_total_revenue_including_sales_tax():
    out = run()
    aug = date(2026, 8, 1)
    total = out.lines["total_revenue"][aug]
    assert total == pytest.approx(300_000 + 500_000 + 100_000 - 0.07 * 800_000)
    assert out.lines["china_cogs"][aug] == pytest.approx(0.1 * total)


def test_fixed_carry_and_avg_modes():
    out = run()
    aug, sep = date(2026, 8, 1), date(2026, 9, 1)
    assert out.lines["accounting"][aug] == 2500.0
    assert out.lines["marketing_agency"][aug] == 10_000.0 and out.lines["marketing_agency"][sep] == 10_000.0     # last month carried
    assert out.lines["memberships"][aug] == pytest.approx(8_000.0)                                                # mean of the last 3 months


def test_steps_switch_mode_at_their_start_month():
    drv = drivers_default()
    drv["accounting"] = [{"from": None, "mode": "fixed", "value": 1000.0}, {"from": "2026-10-01", "mode": "fixed", "value": 4000.0}]
    out = run(drv=drv)
    assert out.lines["accounting"][date(2026, 9, 1)] == 1000.0
    assert out.lines["accounting"][date(2026, 10, 1)] == 4000.0
    assert out.lines["accounting"][date(2029, 12, 1)] == 4000.0


def test_ratio_avg_mode_is_mean_of_monthly_ratios():
    a = base_actuals()
    a["china_cogs"] = {m: 100_000.0 for m in months_to(THROUGH, 12)}
    a["china_cogs"][date(2026, 7, 1)] = 200_000.0
    drv = drivers_default()
    drv["china_cogs"] = [{"from": None, "mode": "rate", "base": "total_revenue", "rate_kind": "ratio_avg", "window": 2}]
    out = run(actuals=a, drv=drv)
    assert out.lines["china_cogs"][date(2026, 8, 1)] == pytest.approx((0.1 + 0.2) / 2 * out.lines["total_revenue"][date(2026, 8, 1)])


def test_headcount_formula_steps_with_annualised_revenue():
    out = run(settings={"headcount": {"start": "2026-09-01"}})
    assert date(2026, 8, 1) in out.lines["headcount"] and out.lines["headcount"][date(2026, 8, 1)] == 0.0           # before the start month
    assert out.lines["headcount"][date(2026, 9, 1)] == 15_000.0                                                       # 12 x ~$0.84M < $20M: base only
    big = run(up={"nc": 2_000_000.0, "repeat": 1_000_000.0})                                                          # ~$31.6M a year
    assert big.lines["headcount"][date(2026, 10, 1)] == 15_000.0 + 2 * 15_000.0                                       # two $5M steps over $20M


def test_cogs_model_rate_and_scenario_mix_override():
    rows = [{"sku": "A", "label": "A", "china_unit_cost": 2.0, "us_unit_cost": 4.0, "sales_mix": 0.5},
            {"sku": "B", "label": "B", "china_unit_cost": 3.0, "us_unit_cost": 5.0, "sales_mix": 0.5}]
    drv = drivers_default()
    drv["china_cogs"] = [{"from": None, "mode": "rate", "base": "total_revenue", "rate_kind": "cogs_model"}]
    s = {"cogs": {"us_share": 0.5, "price_per_bottle": 30.0}}
    out = run(drv=drv, cogs_sku=rows, settings=s)
    # china 2.5, us 4.5 -> 3.5 per bottle / 30 = 11.667%
    assert out.cogs_model["rate"] == pytest.approx(3.5 / 30)
    assert out.cogs_model["rate_at_current_price"] is None or out.cogs_model["rate_at_current_price"] >= 0
    out2 = run(drv=drv, cogs_sku=rows, settings=s, params={"cogs": {"mix": {"A": 1.0, "B": 0.0}}})
    assert out2.cogs_model["rate"] == pytest.approx(3.0 / 30)


def test_inflation_compounds_per_month_on_fixed_and_cogs_separately():
    drv = drivers_default()
    out = run(drv=drv, params={"engine": {"opex_inflation": 0.12, "cogs_inflation": 0.0}})
    aug, sep = date(2026, 8, 1), date(2026, 9, 1)
    assert out.lines["accounting"][aug] == pytest.approx(2500 * 1.12 ** (1 / 12))
    assert out.lines["accounting"][sep] == pytest.approx(2500 * 1.12 ** (2 / 12))
    assert out.lines["china_cogs"][aug] == pytest.approx(0.1 * out.lines["total_revenue"][aug])                      # COGS inflation is zero


def test_monthly_override_replaces_a_projected_value_only():
    ov = {"accounting": {date(2026, 10, 1): 9_999.0, date(2026, 2, 1): 1.0}}                                        # the Feb one is an actual month: ignored
    out = run(overrides=ov)
    assert out.lines["accounting"][date(2026, 10, 1)] == 9_999.0
    assert out.lines["accounting"][date(2026, 2, 1)] == 2_000.0


def test_driven_parent_other_business_expenses_is_rate_times_revenue_when_projected():
    out = run()
    aug = date(2026, 8, 1)
    rate = 1_000.0 / 1_000_000.0
    assert out.lines["other_business_expenses"][aug] == pytest.approx(rate * out.lines["total_revenue"][aug])
    assert aug not in out.lines["bank_fees"]                                                                          # sub-lines are blank when projected


def test_zero_denominator_rate_is_zero():
    a = base_actuals()
    a["amazon_rev"] = {m: 0.0 for m in months_to(THROUGH, 12)}
    out = run(actuals=a)
    assert out.lines["amazon_fba_fees"][date(2026, 8, 1)] == 0.0


def test_window_crosses_a_year_boundary():
    through = date(2026, 2, 1)
    a = actuals(through=through, dtc_rev=800_000, amazon_rev=200_000, amazon_fba_fees=20_000)
    out = run(actuals=a, today=date(2026, 6, 3), params={"engine": {"rate_window_months": 4}})
    assert out.window == [date(2025, 11, 1), date(2025, 12, 1), date(2026, 1, 1), date(2026, 2, 1)]


# ---------------------------------------------------------------------------------------------------------------- derived metrics

def test_marketing_spend_projected_comes_from_drivers_channel_split():
    out = run()
    aug = date(2026, 8, 1)
    assert out.lines["marketing_spend"][aug] == 250_000.0
    assert out.lines["mkt_facebook_ads"][aug] == pytest.approx(225_000.0)
    assert out.lines["total_expenses"][aug] >= 250_000.0


def test_fiscal_year_sums_and_recomputes_ratios():
    out = run()
    fy = out.fy[2027]
    assert fy["total_revenue"] == pytest.approx(sum(out.lines["total_revenue"][m] for m in MONTHS if m.year == 2027))
    assert fy["noi_margin"] == pytest.approx(fy["noi"] / fy["total_revenue"])
    assert fy["gross_profit_pct"] == pytest.approx(fy["gross_profit"] / fy["total_revenue"])


def test_ttm_ftm_and_growth():
    out = run()
    m = date(2026, 12, 1)
    i = MONTHS.index(m)
    assert out.lines["ttm_revenue"][m] == pytest.approx(sum(out.lines["total_revenue"][x] for x in MONTHS[i - 11: i + 1]))
    assert out.lines["ftm_noi"][m] == pytest.approx(out.lines["noi"][m] * 12)
    assert out.ttm == out.lines["ttm_noi"] and out.ftm == out.lines["ftm_noi"]
    assert MONTHS[10] not in out.lines["ttm_revenue"] and MONTHS[11] in out.lines["ttm_revenue"]                      # needs 12 months
    assert out.lines["dtc_rev_mom"][MONTHS[1]] == pytest.approx(out.lines["dtc_rev"][MONTHS[1]] / out.lines["dtc_rev"][MONTHS[0]] - 1)
    assert MONTHS[0] not in out.lines["dtc_rev_mom"]
    assert MONTHS[5] not in out.lines["dtc_rev_mom_6m"] and MONTHS[6] in out.lines["dtc_rev_mom_6m"]


def test_noi_growth_is_blank_when_the_earlier_month_is_not_positive():
    out = run()
    L_ = out.lines
    for m in MONTHS[1:]:
        prev = L_["noi"][add_months(m, -1)]
        assert (m in L_["noi_mom"]) == (prev > 0)


def test_implied_ev_is_noi_times_multiple_and_none_when_negative():
    out = run()
    noi = out.fy[2027]["noi"]
    assert out.ev["basis_year"] == 2027 and out.ev["basis_noi"] == pytest.approx(noi)
    assert out.ev["3x"] == pytest.approx(noi * 3) and out.ev["8x"] == pytest.approx(noi * 8)
    assert out.ev["multiples"] == [3, 4, 5, 6, 7, 8] and out.ev["loi"] == 7_500_000.0
    neg = run(up={"nc": 0.0, "repeat": 0.0})
    assert neg.fy[2027]["noi"] < 0 and neg.ev["3x"] is None


def test_ev_keys_match_the_shell_headline_pattern():
    import re
    out = run()
    nums = [k for k in out.ev if re.match(r"^\d+(\.\d+)?x?$", k)]
    assert nums == ["3x", "4x", "5x", "6x", "7x", "8x"]


def test_every_line_key_has_a_series_and_row_map_is_consistent():
    out = run()
    assert set(out.lines) == set(L.LINE_KEYS)
    assert L.ROW_TO_KEY[100] == "noi" and L.ROW_TO_KEY[22] == "total_revenue" and L.ROW_TO_KEY[47] == "gross_profit"
    assert len(set(L.ROW_TO_KEY.values())) == len(L.ROW_TO_KEY)


# ---------------------------------------------------------------------------------------------------------------- validation and settings

def test_validate_segments_accepts_good_and_names_the_bad():
    ok = M.validate_segments("accounting", [{"from": None, "mode": "fixed", "value": 10}, {"from": "2027-01-01", "mode": "carry"}])
    assert ok[0]["value"] == 10.0 and ok[1]["mode"] == "carry"
    bad = [([], "1-12"), ([{"mode": "fixed", "value": "x"}], "numeric"), ([{"mode": "nope"}], "mode"),
           ([{"from": "2027-01-15", "mode": "carry"}], "first of a month"),
           ([{"from": None, "mode": "carry"}, {"from": None, "mode": "carry"}], "beginning"),
           ([{"from": "2027-02-01", "mode": "carry"}, {"from": "2027-01-01", "mode": "carry"}], "order"),
           ([{"mode": "rate", "base": "bogus"}], "base"), ([{"mode": "avg", "window": 0}], "window"),
           ([{"mode": "rate", "base": "dtc_rev", "rate_kind": "typed"}], "typed")]
    for segs, frag in bad:
        with pytest.raises(ValueError) as e:
            M.validate_segments("accounting", segs)
        assert frag in str(e.value)
    with pytest.raises(ValueError):
        M.validate_segments("noi", [{"mode": "carry"}])


def test_scenario_driver_overlay_replaces_the_whole_line():
    out = run(params={"drivers": {"accounting": {"segments": [{"from": None, "mode": "fixed", "value": 123.0}]}}})
    assert out.lines["accounting"][date(2026, 9, 1)] == 123.0


def test_effective_settings_layers():
    s = M.effective_settings({"engine": {"rate_window_months": 6}}, {"engine": {"rate_window_months": 3}, "cogs": {"us_share": 0.2}})
    assert s["engine"]["rate_window_months"] == 3 and s["cogs"]["us_share"] == 0.2 and s["headcount"]["base"] == 15_000.0


def test_missing_driver_defaults_warn_instead_of_silently_projecting_zero_costs():
    out = run(drv={})
    assert any("No cost drivers" in w for w in out.warnings)
    assert out.lines["accounting"][date(2026, 9, 1)] == 0.0 and out.lines["total_revenue"][date(2026, 9, 1)] > 0
