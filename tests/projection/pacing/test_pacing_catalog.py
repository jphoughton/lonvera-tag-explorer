"""The metric catalog: unique keys, ratios recomputed from sums, blanks on zero denominators, product bands."""
from collections import defaultdict

from app.projection.pacing import catalog
from app.projection.pacing.catalog import div
from etl.projection_pacing import classify_campaign_product, classify_product_by_level, classify_stage


def bases(**kw):
    b = defaultdict(float)
    b.update(kw)
    return b


def test_keys_are_unique_and_match_the_sheets_machine_keys():
    keys = [m.key for m in catalog.CATALOG]
    assert len(keys) == len(set(keys))
    for must in ("biz_total_rev", "dtc_nc_roas", "meta_spend", "meta_spend_vitabull", "prod_spend_total", "stage_testing_pct", "google_nc_cpa",
                 "amz_tacos", "ebitda_est", "noi_gap", "all_spend", "bone_roas", "test_cpa"):
        assert must in keys, must
    assert len(catalog.bands_for("rollup")) == 8 and len(catalog.bands_for("meta")) == 6


def test_div_blank_on_zero_or_missing():
    assert div(10, 4) == 2.5 and div(10, 0) is None and div(None, 3) is None and div(3, None) is None


def test_ratios_are_computed_from_sums_not_averaged():
    # two days with different CPAs: the period CPA is total spend / total new customers
    d1, d2 = bases(m_spend=100, o_nc_orders=1), bases(m_spend=300, o_nc_orders=9)
    total = bases(m_spend=d1["m_spend"] + d2["m_spend"], o_nc_orders=10)
    v = catalog.evaluate(total)
    assert v["dtc_nc_cpa"] == 40.0
    assert (div(100, 1) + div(300, 9)) / 2 != v["dtc_nc_cpa"]


def test_zero_denominators_give_blank_not_errors():
    v = catalog.evaluate(bases())
    assert v["dtc_nc_cpa"] is None and v["meta_roas"] is None and v["biz_mer"] is None and v["ebitda_margin"] is None
    assert v["biz_total_rev"] == 0.0 and v["stage_testing_pct"] is None


def test_business_wide_and_ebitda_formulas():
    b = bases(o_total_rev=1000, o_tax=50, o_refunds=30, a_revenue=200, o_nc_rev=400, a_nc_rev=50, o_nc_orders=10, a_nc_orders=2, m_spend=300, g_spend=20, tt_spend=5,
              pl_nonmeta_spend=25, a_ad_spend=15, e_cogs=300, e_var_opex=40, e_fixed_opex=100, e_below=10, e_noi_target=60, e_target_days=1, e_tiktok_plan=5, x_tax_est=70)
    v = catalog.evaluate(b)
    assert v["biz_total_rev"] == 1200 and v["biz_nc_rev"] == 450 and v["biz_nc_orders"] == 12
    assert v["biz_total_spend"] == 340                              # non-Meta platforms + Meta + Amazon ads
    assert v["dtc_ad_spend"] == 325
    assert v["ebitda_revenue"] == 1120 and v["biz_total_rev_plan"] == 1130
    assert v["ebitda_ad_spend"] == 345
    assert v["ebitda_est"] == 1120 - 300 - 345 - 40 - 100 == 335
    assert v["noi_est"] == 325 and v["ebitda_target"] == 70 and v["ebitda_gap"] == 265 and v["noi_gap"] == 265


def test_reach_measures_are_not_additive_and_blank_when_missing():
    b = bases(m_spend=1000)
    b["r_impressions"] = b["r_reach"] = b["r_uoc"] = None
    v = catalog.evaluate(b)
    assert v["meta_impressions"] is None and v["meta_cpm"] is None and v["meta_frequency"] is None
    b.update({"r_impressions": 400_000, "r_reach": 100_000, "r_uoc": 8_000})
    v = catalog.evaluate(b)
    assert v["meta_cpm"] == 2.5 and v["meta_frequency"] == 4.0 and v["meta_cost_per_1k_reach"] == 10.0 and v["meta_unique_outbound_ctr"] == 0.02
    assert not catalog.BY_KEY["meta_reach"].additive and catalog.BY_KEY["meta_spend"].additive


def test_product_bands_and_total_row():
    b = bases(ps_vitabull=100, ps_beefliver=200, ps_boneflex=300, m_spend=620, pn_rev_vitabull=90, pn_ord_vitabull=1, o_nc_rev=500, o_nc_orders=5)
    b["pu_vitabull"] = 50
    b["r_uoc"] = 400
    v = catalog.evaluate(b)
    assert v["meta_ncroas_vitabull"] == 0.9 and v["meta_nccpa_vitabull"] == 100 and v["meta_cvr_vitabull"] == 0.02
    assert v["meta_uoc_beefliver"] is None                                   # no per-product clicks: blank, not zero
    assert v["prod_spend_total"] == 620 and v["prod_nc_roas_total"] == 500 / 620 and v["prod_cvr_total"] == 5 / 400


def test_stage_premium_and_meta_groups():
    b = bases(m_scaling=900, m_testing=100, ac_scaling=10, ac_testing=2, ac_total=12, mg_all_spend=1000, mg_all_value=800, mg_bull_spend=300, mg_bull_clicks=150)
    v = catalog.evaluate(b, "rollup")
    assert v["stage_testing_pct"] == 0.1 and v["stage_cpa_scaling"] == 90 and v["stage_cpa_testing"] == 50 and v["stage_cpa_test_vs_scale"] == 50 / 90 - 1
    m = catalog.evaluate(b, "meta")
    assert m["all_roas"] == 0.8 and m["bull_cpc"] == 2.0 and m["bone_roas"] is None


def test_classifiers_match_the_sheets_rules():
    assert classify_campaign_product("Beef Liver ABO") == "Beef Liver"
    assert classify_campaign_product("VitaBull_WW_Scaling_CostCaps_CBO") == "VitaBull"
    assert classify_campaign_product("BoneFlex_WW_Testing") == "Boneflex"
    assert classify_campaign_product("All_WW_Testing_CostCaps_ABO_1DC") == "Unclassified"
    assert classify_campaign_product("Beef Liver Boneflex mix") == "Unclassified"      # more than one product
    assert classify_stage("Bull 7DC") == "Scaling" and classify_stage("Beef Liver Testing") == "Testing" and classify_stage("X_TEST_Y") == "Testing"
    assert classify_stage("Bull Tester") == "Testing"                                    # the rule is 'test' anywhere in the name
    assert classify_product_by_level("All_WW_Scaling", "Boneflex 1,2 (WW)") == "Boneflex"        # ad set name when the campaign is mixed
    assert classify_product_by_level("All_WW", "All") == "Other"
    assert classify_product_by_level("Vital Belle ASC", None) == "Other"
    assert classify_product_by_level("Bull Tester", "Liver stuff") == "VitaBull"                  # campaign name wins
