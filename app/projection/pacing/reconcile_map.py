"""Reconcile map for the pacing module (scripts/projection/reconcile.py --module pacing).

Run at the snapshot date and with the full history loaded:

    PROJECTION_AS_OF=2026-11-03 PROJECTION_PACING_FULL=1 DATABASE_URL=... PYTHONPATH=. python scripts/projection/reconcile.py --module pacing

`pacing.automated.<col>` is the month pacing table in the layout of the sheet's Automated Pacing tab (list index = sheet row - 4); column B of the two
cost-per-customer rows is compared as goal / projection (the sheet's convention). `pacing.snapshot.<col>` is the Month Snapshot (rows 7..28).
With PROJECTION_PACING_FULL the result also carries the last closed month, last complete ISO week and yesterday of the rollups: `pacing.rollup_month|rollup_week|rollup_day.<metric key>`,
compared with the sheet's rollup tabs cell by cell. The snapshot fixture holds only the last 400 rows of the bids and any-click feeds, so those metrics are compared on
the day grain only (every other key on all three grains).
"""
from datetime import date

MONTHLY = {
    "biz_total_rev": "W", "biz_total_spend": "X", "biz_mer": "Y", "biz_nc_rev": "Z", "biz_repeat_rev": "AA", "biz_nc_orders": "AB",
    "biz_nc_cpa": "AC", "biz_nc_roas": "AD", "biz_nc_aov": "AE", "biz_nc_cvr": "AF", "biz_repeat_share": "AG", "dtc_total_rev": "AH",
    "dtc_nc_rev": "AI", "dtc_repeat_rev": "AJ", "dtc_total_orders": "AK", "dtc_nc_orders": "AL", "dtc_aov": "AN", "dtc_nc_aov": "AO",
    "dtc_ad_spend": "AP", "dtc_nc_cpa": "AQ", "dtc_nc_roas": "AR", "dtc_mer": "AS", "dtc_nc_cvr": "AT", "dtc_repeat_share": "AU",
    "dtc_new_visitors": "AV", "dtc_cost_per_new_visitor": "AW", "dtc_cost_per_uoc": "AX", "meta_spend": "AY", "meta_link_clicks": "AZ",
    "meta_cpc": "BA", "meta_purchases": "BC", "meta_purchase_value": "BD", "meta_cvr": "BE", "meta_aov": "BF", "meta_nc_orders": "BG",
    "meta_nc_rev": "BH", "meta_nc_roas": "BI", "meta_nc_cpa": "BJ", "meta_nc_aov": "BK", "meta_roas": "BL", "meta_impressions": "BM",
    "meta_reach": "BN", "meta_first_impression_pct": "BO", "meta_cost_per_1k_reach": "BP", "meta_cpm": "BQ", "meta_frequency": "BR",
    "meta_unique_outbound_clicks": "BS", "meta_unique_outbound_ctr": "BT", "meta_avg_cost_cap": "BU", "meta_avg_roas_floor": "BV",
    "meta_capped_spend_pct": "BW", "meta_pct_cost_cap": "BX", "meta_pct_troas_floor": "BY", "meta_spend_vitabull": "BZ", "meta_spend_beefliver": "CA",
    "meta_spend_boneflex": "CB", "prod_spend_total": "CC", "meta_ncrev_vitabull": "CD", "meta_ncrev_beefliver": "CE", "meta_ncrev_boneflex": "CF",
    "prod_ncrev_total": "CG", "meta_ncroas_vitabull": "CH", "meta_ncroas_beefliver": "CI", "meta_ncroas_boneflex": "CJ", "prod_nc_roas_total": "CK",
    "meta_nco_vitabull": "CL", "meta_nco_beefliver": "CM", "meta_nco_boneflex": "CN", "prod_nco_total": "CO", "meta_nccpa_vitabull": "CP",
    "meta_nccpa_beefliver": "CQ", "meta_nccpa_boneflex": "CR", "prod_nc_cpa_total": "CS", "meta_uoc_vitabull": "CT", "meta_uoc_beefliver": "CU",
    "meta_uoc_boneflex": "CV", "prod_uoc_total": "CW", "meta_cvr_vitabull": "CX", "meta_cvr_beefliver": "CY", "meta_cvr_boneflex": "CZ",
    "prod_cvr_total": "DA", "meta_rpc_vitabull": "DB", "meta_rpc_beefliver": "DC", "meta_rpc_boneflex": "DD", "prod_rpc_total": "DE",
    "meta_cpuoc_vitabull": "DF", "meta_cpuoc_beefliver": "DG", "meta_cpuoc_boneflex": "DH", "prod_cpuoc_total": "DI", "stage_spend_scaling": "DJ",
    "stage_spend_testing": "DK", "stage_spend_total": "DL", "stage_testing_pct": "DM", "stage_nc_scaling": "DN", "stage_nc_testing": "DO",
    "stage_nc_total": "DP", "stage_cpa_scaling": "DQ", "stage_cpa_testing": "DR", "stage_cpa_total": "DS", "stage_cpa_test_vs_scale": "DT",
    "google_spend": "DU", "google_clicks": "DV", "google_cpc": "DW", "google_purchases": "DX", "google_purchase_value": "DY", "google_cvr": "DZ",
    "google_aov": "EA", "google_nc_orders": "EB", "google_nc_rev": "EC", "google_nc_roas": "EE", "google_nc_cpa": "EF", "google_nc_aov": "EG",
    "google_roas": "EH", "amz_ad_spend": "EI", "amz_clicks": "EJ", "amz_impressions": "EK", "amz_cpc": "EL", "amz_revenue": "EM", "amz_orders": "EN",
    "amz_units": "EO", "amz_aov": "EP", "amz_nc_orders": "EQ", "amz_nc_rev": "ER", "amz_nc_roas": "ES", "amz_nc_cpa": "ET",
    "amz_attributed_sales_14d": "EU", "amz_ad_roas": "EV", "amz_tacos": "EW", "amz_cvr": "EX", "noi_target": "EY", "ebitda_target": "EZ",
    "ebitda_revenue": "FA", "ebitda_cogs": "FB", "ebitda_ad_spend": "FC", "ebitda_opex_total": "FD", "ebitda_est": "FE", "ebitda_gap": "FF",
    "ebitda_margin": "FG", "noi_est": "FH", "noi_gap": "FI",
}
WEEKLY = {
    "biz_total_rev": "F", "biz_total_spend": "G", "biz_mer": "H", "biz_nc_rev": "I", "biz_repeat_rev": "J", "biz_nc_orders": "K", "biz_nc_cpa": "L",
    "biz_nc_roas": "M", "biz_nc_aov": "N", "biz_nc_cvr": "O", "biz_repeat_share": "P", "dtc_total_rev": "Q", "dtc_nc_rev": "R", "dtc_repeat_rev": "S",
    "dtc_total_orders": "T", "dtc_nc_orders": "U", "dtc_aov": "W", "dtc_nc_aov": "X", "dtc_ad_spend": "Y", "dtc_nc_cpa": "Z", "dtc_nc_roas": "AA",
    "dtc_mer": "AB", "dtc_nc_cvr": "AC", "dtc_repeat_share": "AD", "dtc_new_visitors": "AE", "dtc_cost_per_new_visitor": "AF",
    "dtc_cost_per_uoc": "AG", "meta_spend": "AH", "meta_link_clicks": "AI", "meta_cpc": "AJ", "meta_purchases": "AK", "meta_purchase_value": "AL",
    "meta_cvr": "AM", "meta_aov": "AN", "meta_nc_orders": "AO", "meta_nc_rev": "AP", "meta_nc_roas": "AQ", "meta_nc_cpa": "AR", "meta_nc_aov": "AS",
    "meta_roas": "AT", "meta_impressions": "AU", "meta_reach": "AV", "meta_first_impression_pct": "AW", "meta_cost_per_1k_reach": "AX",
    "meta_cpm": "AY", "meta_frequency": "AZ", "meta_unique_outbound_clicks": "BA", "meta_unique_outbound_ctr": "BB", "meta_avg_cost_cap": "BC",
    "meta_avg_roas_floor": "BD", "meta_capped_spend_pct": "BE", "meta_pct_cost_cap": "BF", "meta_pct_troas_floor": "BG", "meta_spend_vitabull": "BH",
    "meta_spend_beefliver": "BI", "meta_spend_boneflex": "BJ", "prod_spend_total": "BK", "meta_ncrev_vitabull": "BL", "meta_ncrev_beefliver": "BM",
    "meta_ncrev_boneflex": "BN", "prod_ncrev_total": "BO", "meta_ncroas_vitabull": "BP", "meta_ncroas_beefliver": "BQ", "meta_ncroas_boneflex": "BR",
    "prod_nc_roas_total": "BS", "meta_nco_vitabull": "BT", "meta_nco_beefliver": "BU", "meta_nco_boneflex": "BV", "prod_nco_total": "BW",
    "meta_nccpa_vitabull": "BX", "meta_nccpa_beefliver": "BY", "meta_nccpa_boneflex": "BZ", "prod_nc_cpa_total": "CA", "meta_uoc_vitabull": "CB",
    "meta_uoc_beefliver": "CC", "meta_uoc_boneflex": "CD", "prod_uoc_total": "CE", "meta_cvr_vitabull": "CF", "meta_cvr_beefliver": "CG",
    "meta_cvr_boneflex": "CH", "prod_cvr_total": "CI", "meta_rpc_vitabull": "CJ", "meta_rpc_beefliver": "CK", "meta_rpc_boneflex": "CL",
    "prod_rpc_total": "CM", "meta_cpuoc_vitabull": "CN", "meta_cpuoc_beefliver": "CO", "meta_cpuoc_boneflex": "CP", "prod_cpuoc_total": "CQ",
    "stage_spend_scaling": "CR", "stage_spend_testing": "CS", "stage_spend_total": "CT", "stage_testing_pct": "CU", "stage_nc_scaling": "CV",
    "stage_nc_testing": "CW", "stage_nc_total": "CX", "stage_cpa_scaling": "CY", "stage_cpa_testing": "CZ", "stage_cpa_total": "DA",
    "stage_cpa_test_vs_scale": "DB", "google_spend": "DC", "google_clicks": "DD", "google_cpc": "DE", "google_purchases": "DF",
    "google_purchase_value": "DG", "google_cvr": "DH", "google_aov": "DI", "google_nc_orders": "DJ", "google_nc_rev": "DK", "google_nc_roas": "DL",
    "google_nc_cpa": "DM", "google_nc_aov": "DN", "google_roas": "DO", "amz_ad_spend": "DP", "amz_clicks": "DQ", "amz_impressions": "DR",
    "amz_cpc": "DS", "amz_revenue": "DT", "amz_orders": "DU", "amz_units": "DV", "amz_aov": "DW", "amz_nc_orders": "DX", "amz_nc_rev": "DY",
    "amz_nc_roas": "DZ", "amz_nc_cpa": "EA", "amz_attributed_sales_14d": "EB", "amz_ad_roas": "EC", "amz_tacos": "ED", "amz_cvr": "EE",
    "noi_target": "EF", "ebitda_target": "EG", "ebitda_revenue": "EH", "ebitda_cogs": "EI", "ebitda_ad_spend": "EJ", "ebitda_opex_total": "EK",
    "ebitda_est": "EL", "ebitda_gap": "EM", "ebitda_margin": "EN", "noi_est": "EO", "noi_gap": "EP",
}
DAILY = {
    "biz_total_rev": "E", "biz_total_spend": "F", "biz_nc_rev": "G", "biz_nc_roas": "H", "biz_nc_cvr": "I", "biz_nc_cpa": "J", "biz_nc_aov": "K",
    "biz_nc_orders": "L", "biz_mer": "M", "biz_repeat_rev": "N", "biz_repeat_share": "O", "dtc_total_rev": "P", "dtc_nc_rev": "Q",
    "dtc_repeat_rev": "R", "dtc_total_orders": "S", "dtc_nc_orders": "T", "dtc_aov": "V", "dtc_nc_aov": "W", "dtc_ad_spend": "X", "dtc_nc_cpa": "Y",
    "dtc_nc_roas": "Z", "dtc_mer": "AA", "dtc_nc_cvr": "AB", "dtc_repeat_share": "AC", "dtc_new_visitors": "AD", "dtc_cost_per_new_visitor": "AE",
    "dtc_cost_per_uoc": "AF", "meta_spend": "AG", "meta_link_clicks": "AH", "meta_cpc": "AI", "meta_purchases": "AJ", "meta_purchase_value": "AK",
    "meta_cvr": "AL", "meta_aov": "AM", "meta_nc_orders": "AN", "meta_nc_rev": "AO", "meta_nc_roas": "AP", "meta_nc_cpa": "AQ", "meta_nc_aov": "AR",
    "meta_roas": "AS", "meta_impressions": "AT", "meta_reach": "AU", "meta_first_impression_pct": "AV", "meta_cost_per_1k_reach": "AW",
    "meta_cpm": "AX", "meta_frequency": "AY", "meta_unique_outbound_clicks": "AZ", "meta_unique_outbound_ctr": "BA", "meta_avg_cost_cap": "BB",
    "meta_avg_roas_floor": "BC", "meta_capped_spend_pct": "BD", "meta_pct_cost_cap": "BE", "meta_pct_troas_floor": "BF", "meta_spend_vitabull": "BG",
    "meta_spend_beefliver": "BH", "meta_spend_boneflex": "BI", "prod_spend_total": "BJ", "meta_ncrev_vitabull": "BK", "meta_ncrev_beefliver": "BL",
    "meta_ncrev_boneflex": "BM", "prod_ncrev_total": "BN", "meta_ncroas_vitabull": "BO", "meta_ncroas_beefliver": "BP", "meta_ncroas_boneflex": "BQ",
    "prod_nc_roas_total": "BR", "meta_nco_vitabull": "BS", "meta_nco_beefliver": "BT", "meta_nco_boneflex": "BU", "prod_nco_total": "BV",
    "meta_nccpa_vitabull": "BW", "meta_nccpa_beefliver": "BX", "meta_nccpa_boneflex": "BY", "prod_nc_cpa_total": "BZ", "meta_uoc_vitabull": "CA",
    "meta_uoc_beefliver": "CB", "meta_uoc_boneflex": "CC", "prod_uoc_total": "CD", "meta_cvr_vitabull": "CE", "meta_cvr_beefliver": "CF",
    "meta_cvr_boneflex": "CG", "prod_cvr_total": "CH", "meta_rpc_vitabull": "CI", "meta_rpc_beefliver": "CJ", "meta_rpc_boneflex": "CK",
    "prod_rpc_total": "CL", "meta_cpuoc_vitabull": "CM", "meta_cpuoc_beefliver": "CN", "meta_cpuoc_boneflex": "CO", "prod_cpuoc_total": "CP",
    "stage_spend_scaling": "CQ", "stage_spend_testing": "CR", "stage_spend_total": "CS", "stage_testing_pct": "CT", "stage_nc_scaling": "CU",
    "stage_nc_testing": "CV", "stage_nc_total": "CW", "stage_cpa_scaling": "CX", "stage_cpa_testing": "CY", "stage_cpa_total": "CZ",
    "stage_cpa_test_vs_scale": "DA", "google_spend": "DB", "google_clicks": "DC", "google_cpc": "DD", "google_purchases": "DE",
    "google_purchase_value": "DF", "google_cvr": "DG", "google_aov": "DH", "google_nc_orders": "DI", "google_nc_rev": "DJ", "google_nc_roas": "DK",
    "google_nc_cpa": "DL", "google_nc_aov": "DM", "google_roas": "DN", "amz_ad_spend": "DO", "amz_clicks": "DP", "amz_impressions": "DQ",
    "amz_cpc": "DR", "amz_revenue": "DS", "amz_orders": "DT", "amz_units": "DU", "amz_aov": "DV", "amz_nc_orders": "DW", "amz_nc_rev": "DX",
    "amz_nc_roas": "DY", "amz_nc_cpa": "DZ", "amz_attributed_sales_14d": "EA", "amz_ad_roas": "EB", "amz_tacos": "EC", "amz_cvr": "ED",
    "noi_target": "EE", "ebitda_revenue": "EF", "ebitda_cogs": "EG", "ebitda_ad_spend": "EH", "ebitda_variable_opex": "EI", "ebitda_fixed_opex": "EJ",
    "below_ebitda_per_day": "EK", "ebitda_target": "EL", "ebitda_est": "EM", "ebitda_gap": "EN", "ebitda_margin": "EO",
    "ebitda_contribution_margin": "EP", "noi_est": "ET", "noi_gap": "EU",
}
# metrics whose feed rows the snapshot holds only in part (last 400 rows): checked on the day grain only
PARTIAL_FEEDS = {"meta_avg_cost_cap", "meta_avg_roas_floor", "meta_capped_spend_pct", "meta_pct_cost_cap", "meta_pct_troas_floor", "stage_nc_scaling", "stage_nc_testing",
                 "stage_nc_total", "stage_cpa_scaling", "stage_cpa_testing", "stage_cpa_total", "stage_cpa_test_vs_scale"}


def _row_month(y: int, m: int) -> int:
    return 5 + (y - 2025) * 12 + (m - 1)


def _row_week(d: date) -> int:
    return 5 + (d - date(2024, 12, 30)).days // 7


def _row_day(d: date) -> int:
    return 5 + (d - date(2025, 1, 1)).days


# the snapshot date 2026-11-03: last closed month Oct 2026, last complete ISO week 2026-W44 (Oct 26 - Nov 1), last complete day Nov 2
MAP = [
    {"key": "pacing", "tab": "Automated Pacing ", "kind": "column", "c0": "B", "r0": 4, "r1": 50, "result": "pacing.automated.B"},
    {"key": "mtd_actual", "tab": "Automated Pacing ", "kind": "column", "c0": "C", "r0": 4, "r1": 50, "result": "pacing.automated.C"},
    {"key": "last_7_day_avg", "tab": "Automated Pacing ", "kind": "column", "c0": "D", "r0": 4, "r1": 50, "result": "pacing.automated.D"},
    {"key": "daily_needed", "tab": "Automated Pacing ", "kind": "column", "c0": "E", "r0": 4, "r1": 50, "result": "pacing.automated.E"},
    {"key": "mtd_should_be", "tab": "Automated Pacing ", "kind": "column", "c0": "F", "r0": 4, "r1": 50, "result": "pacing.automated.F"},
    {"key": "eom_goal", "tab": "Automated Pacing ", "kind": "column", "c0": "G", "r0": 4, "r1": 50, "result": "pacing.automated.G"},
    {"key": "eom_projection", "tab": "Automated Pacing ", "kind": "column", "c0": "H", "r0": 4, "r1": 50, "result": "pacing.automated.H"},
    {"key": "snapshot_actual", "tab": "Month Snapshot", "kind": "column", "c0": "B", "r0": 7, "r1": 28, "result": "pacing.snapshot.B"},
    {"key": "snapshot_projected", "tab": "Month Snapshot", "kind": "column", "c0": "C", "r0": 7, "r1": 28, "result": "pacing.snapshot.C"},
    {"key": "snapshot_target", "tab": "Month Snapshot", "kind": "column", "c0": "D", "r0": 7, "r1": 28, "result": "pacing.snapshot.D"},
]
MAP += [{"key": f"month_2026_10.{k}", "tab": "Rollup - Monthly (MoM)", "kind": "scalar", "row": _row_month(2026, 10), "c0": c, "result": f"pacing.rollup_month.{k}"}
        for k, c in MONTHLY.items() if k not in PARTIAL_FEEDS]
MAP += [{"key": f"week_2026_W44.{k}", "tab": "Rollup - Weekly (WoW)", "kind": "scalar", "row": _row_week(date(2026, 10, 26)), "c0": c, "result": f"pacing.rollup_week.{k}"}
        for k, c in WEEKLY.items() if k not in PARTIAL_FEEDS]
MAP += [{"key": f"day_2026_11_02.{k}", "tab": "Pacing - Daily (180d)", "kind": "scalar", "row": _row_day(date(2026, 11, 2)), "c0": c, "result": f"pacing.rollup_day.{k}"}
        for k, c in DAILY.items()]
