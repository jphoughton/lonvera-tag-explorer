"""The new P&L chart (qb2026) on the whole seeded stack (every module, the frozen sheet snapshot):

* the default chart is untouched: every response a sheet-chart scenario gets is identical with and without new-chart books, driver rows, account map rows,
  typed months and driver edits in the database (the isolation golden test);
* a scenario that selects the new chart still feeds pacing, cash and debt (financing), the scenario modeler, the run summary and the variables page, through
  the legacy view (the old line keys built from the new lines).
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest
from psycopg.types.json import Jsonb

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "projection" / "pnl"))

from pnl_helpers import QB26_ACCOUNTS, qb26_expected                       # noqa: E402

M = "/api/projection"
CODE_KEY = {"4010": "amazon_sales", "4020": "dtc_sales", "4030": "tiktok_sales", "4200": "shipping_income", "4400": "refunds_discounts", "5010": "cogs_amazon",
            "5020": "cogs_shopify", "5030": "cogs_tiktok", "5310": "threepl_services", "5320": "fba_fees", "5410": "adyen_fees", "5420": "amazon_fees",
            "5430": "paypal_fees", "5440": "shopify_fees", "5450": "tiktok_fees", "6011": "creative_agency", "6012": "email_agency", "6031": "amazon_ads",
            "6032": "facebook_ads", "6033": "google_ads", "6035": "tiktok_ads", "6110": "accounting", "6115": "chargeback_management", "6120": "consulting",
            "6125": "customer_service", "6135": "legal", "6145": "office_supplies", "6166": "software_marketing", "6167": "software_operations",
            "6170": "bank_fees", "7000": "other_income", "8000": "interest_expense"}
NEW_MONTHS = [date(2026, m, 1) for m in range(1, 11)]
ENDPOINTS = ("pnl/table", "pnl/drivers", "pnl/summary", "pnl/metrics", "pnl/ev", "pnl/compare", "pnl/checks", "pnl/qb-map", "pnl/freshness", "run",
             "pacing/month", "pacing/levers", "financing/table", "financing/cash", "financing/payback", "financing/debt", "modeler/state", "modeler/compare",
             "variables/", "drivers/levers", "freshness")


@pytest.fixture()
def clean_qb(vdb, restore):
    """Removes every new-chart row a test adds (books, typed months); scenarios, series and audit come back through `restore`."""
    import psycopg
    yield
    with psycopg.connect(vdb) as c:
        c.execute("DELETE FROM proj_actuals WHERE left(line_key, 5) = 'qb26.'")
        c.execute("DELETE FROM proj_upload_history WHERE chart = 'qb2026'")


def load_books(vdb, months=NEW_MONTHS):
    import psycopg
    with psycopg.connect(vdb) as c, c.cursor() as cur:
        for i, m in enumerate(months):
            f = 0.8 + 0.04 * i
            for name, base in QB26_ACCOUNTS:
                cur.execute("INSERT INTO proj_actuals (line_key, month, value, source) VALUES (%s,%s,%s,'test qb2026 books') ON CONFLICT (line_key, month) DO UPDATE SET value = EXCLUDED.value",
                            ("qb26." + CODE_KEY[name.split(" ", 1)[0]], m, round(base * f, 2)))


def snapshot(client, slug="current"):
    out = {}
    for ep in ENDPOINTS:
        r = client.get(f"{M}/{ep}", query_string={"scenarios": slug} if ep == "modeler/compare" else {"scenario": slug})
        out[ep] = (r.status_code, r.get_json())
    return out


def strip_clock(x):
    """Wall-clock timings are not part of the answer."""
    if isinstance(x, dict):
        return {k: strip_clock(v) for k, v in x.items() if k not in ("ms", "elapsed_ms", "generated_at", "computed_at")}
    if isinstance(x, list):
        return [strip_clock(v) for v in x]
    return x


def test_default_chart_responses_are_identical_with_and_without_new_chart_data(client, vdb, clean_qb):
    import psycopg
    before = snapshot(client)
    assert before["pnl/table"][0] == 200 and "chart" not in before["pnl/table"][1]
    load_books(vdb)
    with psycopg.connect(vdb) as c:
        sid = c.execute("SELECT id FROM proj_scenarios WHERE slug = 'current'").fetchone()[0]
        c.execute("INSERT INTO proj_series (scenario_id, line_key, month, value, kind) VALUES (%s, 'pnl.qb26.facebook_ads', '2026-12-01', 1, 'override') "
                  "ON CONFLICT DO NOTHING", (sid,))
        c.execute("UPDATE proj_pnl_drivers SET segments = %s WHERE chart = 'qb2026' AND line_key = 'consulting'", (Jsonb([{"from": None, "mode": "fixed", "value": 1.0}]),))
        c.execute("INSERT INTO proj_pnl_qb_map (chart, account, line_key, note) VALUES ('qb2026', '9999 junk', 'legal', 't') ON CONFLICT DO NOTHING")
        c.execute("UPDATE proj_scenarios SET params = jsonb_set(params, '{pnl,qb26}', %s::jsonb, true) WHERE slug = 'current'"
                  if (c.execute("SELECT params ? 'pnl' FROM proj_scenarios WHERE slug = 'current'").fetchone()[0]) else
                  "UPDATE proj_scenarios SET params = params || jsonb_build_object('pnl', jsonb_build_object('qb26', %s::jsonb)) WHERE slug = 'current'",
                  (Jsonb({"drivers": {"legal": {"segments": [{"from": None, "mode": "fixed", "value": 5}]}}}),))
    after = snapshot(client)
    for ep in ENDPOINTS:
        assert after[ep][0] == before[ep][0], ep
        assert json.dumps(strip_clock(after[ep][1]), sort_keys=True, default=str) == json.dumps(strip_clock(before[ep][1]), sort_keys=True, default=str), ep


def to_new_chart(client, name="New chart copy"):
    slug = client.post(f"{M}/scenarios", json={"name": name, "clone_from": "current"}).get_json()["scenario"]["slug"]
    assert client.put(f"{M}/pnl/settings", json={"scenario": slug, "settings": {"chart": "qb2026"}}).status_code == 200
    return slug


def engine_pnl(vdb, slug):
    import psycopg
    from psycopg.rows import dict_row
    from app.projection import engine
    with psycopg.connect(vdb, row_factory=dict_row) as c:
        res = engine.run(slug, c)
    assert not res.errors and not res.summary()["degraded"]
    return res, res.results["pnl"]


def test_a_new_chart_scenario_feeds_every_other_module(client, vdb, clean_qb):
    load_books(vdb)
    slug = to_new_chart(client)
    snap = snapshot(client, slug)
    for ep, (status, body) in snap.items():
        assert status == 200, (ep, body)
    res, pnl = engine_pnl(vdb, slug)
    nov = date(2026, 11, 1)
    table = snap["pnl/table"][1]
    assert table["chart"] == "qb2026"
    ms = dict(zip(table["months"], table["month_status"]))
    assert ms["2026-10-01"] == "books" and ms["2025-06-01"] == "old" and ms["2026-11-01"] == "projected"
    rows = {r["key"]: r for r in table["rows"]}
    idx = table["months"].index("2026-10-01")
    ex = qb26_expected(0.8 + 0.04 * 9)
    for key, want in (("total_income", ex["total_income"]), ("noi", ex["noi"]), ("net_income", ex["net_income"]), ("gross_profit", ex["gross_profit"])):
        assert rows[key]["values"][idx] == pytest.approx(want, abs=0.02), key

    # run summary: the headline reads total revenue and net operating income by their old names; they are the new chart's
    fy27 = snap["run"][1]["headline"]["fy"]["2027"]
    yi = table["fy"]["years"].index(2027)
    assert fy27["noi"] == pytest.approx(table["fy"]["rows"]["noi"][yi], rel=1e-9) and fy27["revenue"] == pytest.approx(table["fy"]["rows"]["total_income"][yi], rel=1e-9)

    # pacing: the month's revenue and net operating income goals are the new chart's
    pm = {r["key"]: r for r in snap["pacing/month"][1]["rows"]}
    assert pm["total_rev"]["goal"] == pytest.approx(pnl.lines["total_income"][nov]) and pm["noi"]["goal"] == pytest.approx(pnl.lines["noi"][nov])
    assert pm["sales_tax"]["label"] == "Shipping income, refunds and discounts"
    assert next(r for r in client.get(f"{M}/pacing/month", query_string={"scenario": "current"}).get_json()["rows"] if r["key"] == "sales_tax")["label"] == "Sales tax (est.)"
    assert pm["cogs_direct"]["goal"] == pytest.approx(pnl.lines["cogs_product"][nov]) and pm["inbound"]["goal"] == 0.0

    # financing: operating cash before inventory = net operating income + the product cost the inventory buys replace
    fin = {r["key"]: r for r in snap["financing/table"][1]["rows"]}
    fi = snap["financing/table"][1]["months"].index("2026-11-01")
    assert fin["financing.noi_before_inventory"]["values"][fi] == pytest.approx(pnl.lines["noi"][nov] + pnl.lines["cogs_product"][nov])

    # modeler: yearly figures are the P&L's; interest expense is NOT added back to a net operating income that is already before it
    years = {y["year"]: y for y in snap["modeler/state"][1]["outputs"]["years"]}
    assert years[2027]["noi"] == pytest.approx(pnl.fy[2027]["noi"]) and years[2027]["total_revenue"] == pytest.approx(pnl.fy[2027]["total_income"])
    assert years[2027]["addbacks"]["interest_expense"] == 0.0 and years[2027]["ebitda"] == pytest.approx(pnl.fy[2027]["noi"])
    sheet_y = {y["year"]: y for y in client.get(f"{M}/modeler/state", query_string={"scenario": "current"}).get_json()["outputs"]["years"]}
    assert sheet_y[2026]["ebitda"] == pytest.approx(sheet_y[2026]["noi"] + sheet_y[2026]["addbacks"]["interest_expense"])      # the sheet chart still adds it back


def test_old_keys_other_modules_read_reconcile_with_the_new_lines(client, vdb, clean_qb):
    from app.projection.financing import model as fin_model, params as fin_params
    from app.projection.pacing import targets
    load_books(vdb)
    slug = to_new_chart(client)
    _, pnl = engine_pnl(vdb, slug)
    nov = date(2026, 11, 1)
    L_ = pnl.lines
    assert pnl.chart == "qb2026"
    assert L_["total_revenue"][nov] == L_["total_income"][nov] and L_["dtc_rev"][nov] == L_["dtc_sales"][nov] and L_["amazon_rev"][nov] == L_["amazon_sales"][nov]
    assert L_["sales_tax"][nov] == pytest.approx(L_["shipping_income"][nov] + L_["refunds_discounts"][nov])
    assert L_["china_cogs"][nov] == L_["cogs_product"][nov] and L_["fulfillment_services"][nov] == L_["threepl_services"][nov]
    assert L_["seller_fees"][nov] == pytest.approx(L_["adyen_fees"][nov] + L_["paypal_fees"][nov]) and L_["shopify_subscriptions"][nov] == L_["shopify_fees"][nov]
    assert L_["amazon_fba_fees"][nov] == L_["fba_fees"][nov] and L_["amazon_seller_fees"][nov] == pytest.approx(L_["amazon_fees"][nov] + L_["tiktok_fees"][nov])
    assert L_["marketing_agency"][nov] == pytest.approx(L_["creative_agency"][nov] + L_["email_agency"][nov])
    assert L_["marketing_spend"][nov] == L_["marketing_media_total"][nov] and L_["mkt_facebook_ads"][nov] == L_["facebook_ads"][nov]
    assert L_["marketing_software"][nov] == L_["software_marketing"][nov] and L_["software_apps"][nov] == L_["software_operations"][nov]
    assert L_["office_admin"][nov] == L_["office_supplies"][nov]
    # the legacy cost groups add up to the new totals: nothing counted twice, nothing dropped (interest and other income stay below the line)
    legacy_exp = L_["marketing_spend"][nov] + L_["marketing"][nov] + L_["other_business_expenses"][nov] + L_["overhead"][nov] + L_["uncategorized"][nov]
    assert legacy_exp == pytest.approx(L_["total_expenses"][nov])
    legacy_cogs = (L_["payment_processing"][nov] + L_["inbound_shipping"][nov] + L_["fulfillment_services"][nov] + L_["cogs"][nov]
                   + L_["amazon_fba_fees"][nov] + L_["amazon_seller_fees"][nov])
    assert legacy_cogs == pytest.approx(L_["total_cogs"][nov])
    assert L_["noi"][nov] == pytest.approx(L_["gross_profit"][nov] - L_["total_expenses"][nov])
    # pacing reads rows through the sheet's row map: every row its cost lines use is a series on the new chart's lines
    row_map = targets.default_row_map()
    used = {row_map[r] for c in targets.COST_LINES for r in c.rows if r in row_map}
    assert used and used <= set(L_)
    # financing: the loaded variable-cost rate resolves from the legacy lines
    info = fin_model.loaded_rate_info(fin_params.effective({}), pnl)
    assert info["rate"] is not None and not info["missing"] and info["parts"]
