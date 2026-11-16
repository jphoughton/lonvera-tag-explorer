"""Creative Pipeline "By cut" (app/priorities_cuts.py, GET /api/priorities/cuts): production assignments counted by board status next to the Meta spend and Upstack new-customer return of ads tagged to the
same cut, for a chosen date window and any ordered subset of product / market cluster / ICP / pain point."""
import datetime as dt
import json

import pytest

from app import priorities_cuts as pc

from .test_api_priorities import _key
from .test_api_priorities_assign import LINE_ROW, MONDAY, clean, one, patch, snaps                       # noqa: F401  (fixtures: snaps, clean)
from .test_api_production_board import mv

FAM = "zz-cut-fam"
D1, D2 = dt.date(2026, 10, 5), dt.date(2026, 10, 20)


@pytest.fixture()
def ads(pg):
    """One tracker row tagged Energy / Given Up Guy / Low T (the same cut as LINE_ROW) with two days of spend: D1 $100 and D2 $50 (Meta 3 + 1 purchases, NC 2 + 0)."""
    pid = pg.execute("SELECT id FROM products WHERE slug = 'vita-bull'").fetchone()[0]
    pg.execute("INSERT INTO tracker_rows (id, product_id, row_key, concept_id, ad_family, family_key, sheet_row, status, raw) VALUES (9301,%s,'ZZ-CUT#1','ZZ-CUT','ZZ-Cut-Fam',%s,99301,'8 - Launched','{}')", (pid, FAM))
    pg.execute("INSERT INTO ad_tags (tracker_row_id, tag_set, version, is_current, cluster, icp, pain_point) VALUES (9301,'linemap','zz',true,'Energy','Given Up Guy','Low T')")
    pg.execute("INSERT INTO meta_family_map (meta_family_key, meta_family, tracker_family_key, rule) VALUES (%s,'ZZ-Cut-Fam',%s,'exact') ON CONFLICT (meta_family_key) DO UPDATE SET tracker_family_key = EXCLUDED.tracker_family_key", (FAM, FAM))
    for day, sp, clk, imp, mp, mr, ncp, ncr in ((D1, 100, 40, 4000, 3, 300, 2, 220), (D2, 50, 20, 2000, 1, 90, 0, 0)):
        pg.execute("INSERT INTO ad_daily (ad_id, day, meta_family_key, meta_family, spend, purchases, conv_value, link_clicks, impressions, source) VALUES (%s,%s,%s,'ZZ-Cut-Fam',%s,%s,%s,%s,%s,'meta_api')",
                   ("zz-ad-" + str(day), day, FAM, sp, mp, mr, clk, imp))
        pg.execute("INSERT INTO ad_daily_upstack (ad_id, day, meta_family_key, meta_family, spend, nc_purchases, nc_conv_value, link_clicks, impressions) VALUES (%s,%s,%s,'ZZ-Cut-Fam',%s,%s,%s,%s,%s)",
                   ("zz-ad-" + str(day), day, FAM, sp, ncp, ncr, clk, imp))
    pg.commit()
    yield
    pg.execute("DELETE FROM ad_daily WHERE meta_family_key = %s", (FAM,))
    pg.execute("DELETE FROM ad_daily_upstack WHERE meta_family_key = %s", (FAM,))
    pg.execute("DELETE FROM meta_family_map WHERE meta_family_key = %s", (FAM,))
    pg.execute("DELETE FROM tracker_rows WHERE id = 9301")
    pg.commit()


def get(client, **q):
    r = client.get("/api/priorities/cuts", query_string=q)
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()


def row(out, **labels):
    return next(r for r in out["rows"] if all(r["labels"].get(k) == v for k, v in labels.items()))


def test_pipeline_counts_by_status_and_cut(client, snaps):
    a = one(client)
    b = one(client, "Victoria")
    patch(client, b["id"], creative_url="https://drive.example/c")
    assert mv(client, b["id"], status="in_review").status_code == 200
    out = get(client, group="product")
    r = row(out, product="Vita Bull")
    assert r["stages"]["assigned"] == 1 and r["stages"]["in_review"] == 1 and r["concepts"] == 2
    assert out["totals"]["concepts"] == 2 and [s["status"] for s in out["stages"]][0] == "assigned"
    assert a["id"] != b["id"]


def test_dropped_concepts_are_not_counted(client, snaps):
    a = one(client)
    assert patch(client, a["id"], dropped=True).status_code == 200
    assert get(client, group="product")["totals"]["concepts"] == 0


def test_spend_and_return_behind_a_cut_for_a_window(client, snaps, ads):
    one(client)
    out = get(client, group="icp", **{"from": "2026-10-01", "to": "2026-10-31"})
    s = row(out, icp="Given Up Guy")["stats"]
    assert s["spend"] == 150 and s["nc_revenue"] == 220 and s["nc_purchases"] == 2
    assert s["nc_roas"] == pytest.approx(220 / 150) and s["nc_cpa"] == 75 and s["meta_roas"] == pytest.approx(390 / 150)
    assert s["cpm"] == pytest.approx(150 * 1000 / 6000) and s["ctr"] == pytest.approx(60 / 6000) and s["cplc"] == pytest.approx(2.5)
    assert s["conv_rate"] == pytest.approx(2 / 60) and s["aov"] == 110
    assert row(out, icp="Given Up Guy")["concepts"] == 1                                  # the pipeline side is not narrowed by the dates


def test_the_date_window_narrows_the_spend(client, snaps, ads):
    s = row(get(client, group="icp", **{"from": "2026-10-10", "to": "2026-10-31"}), icp="Given Up Guy")["stats"]
    assert s["spend"] == 50 and s["nc_roas"] == 0 and s["nc_cpa"] is None
    out = get(client, group="icp", **{"from": "2026-09-01", "to": "2026-09-30"})
    assert all(r["labels"]["icp"] != "Given Up Guy" for r in out["rows"])


def test_group_order_and_labels(client, snaps, ads):
    one(client)
    out = get(client, group="icp,product,pain_point", **{"from": "2026-10-01", "to": "2026-10-31"})
    assert out["group"] == ["icp", "product", "pain_point"]
    r = row(out, icp="Given Up Guy", product="Vita Bull", pain_point="Low T")
    assert list(r["labels"]) == ["icp", "product", "pain_point"] and r["stats"]["spend"] == 150 and r["concepts"] == 1


def test_a_product_filter_applies_to_both_sides(client, snaps, ads):
    one(client)
    out = get(client, group="product", product="boneflex", **{"from": "2026-10-01", "to": "2026-10-31"})
    assert all(r["labels"]["product"] == "Boneflex" for r in out["rows"]) and out["totals"]["concepts"] == 0
    assert row(get(client, group="icp", product="vita-bull", **{"from": "2026-10-01", "to": "2026-10-31"}), icp="Given Up Guy")["stats"]["spend"] == 150


def test_default_window_is_thirty_loaded_days(client, snaps, ads):
    out = get(client, group="product")
    frm, to = dt.date.fromisoformat(out["from"]), dt.date.fromisoformat(out["to"])
    assert (to - frm).days == 29


def test_bad_input_is_a_400(client):
    for q in ({"group": "colour"}, {"group": ","}, {"from": "nope"}, {"from": "2026-10-31", "to": "2026-10-01"}, {"from": "2020-01-01", "to": "2026-10-01"}):
        assert client.get("/api/priorities/cuts", query_string=q).status_code == 400


def test_parse_group_accepts_pain_alias_and_drops_repeats():
    assert pc.parse_group("pain,icp,pain_point,icp") == ["pain_point", "icp"]
    assert pc.parse_group(None) == ["product"]
