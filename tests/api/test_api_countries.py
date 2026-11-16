"""Countries view: GET /api/meta/countries (app/countries.py) over analytics.meta_breakdown_daily (breakdown = country) and nc_by_country_daily.

The numbers are small round ones so every ratio can be checked by hand. Rules under test: ratios of sums (never an average of daily ratios), the window cut to the days both
tables hold (days before coverage are blank, not zero), total NC ROAS uses the new-customer revenue of ALL countries (including a country with no spend), only the country
breakdown is summed, both ad accounts are included, and the empty states."""
import datetime as dt

import pytest

D = dt.date
MAIN, BACKUP = "act_1671338033619223", "act_1543579384022317"


def ins(conn, day, acct, country, spend, imps, link, val, bset="country", level="ad", eid="a1"):
    conn.execute("INSERT INTO meta_breakdown_daily (day, account_id, level, entity_id, campaign_id, adset_id, breakdown_set, dim1, dims_key, spend, impressions, inline_link_clicks, "
                 "purchase_value) VALUES (%s,%s,%s,%s,'c1','s1',%s,%s,%s,%s,%s,%s,%s)", (day, acct, level, eid, bset, country, f"{bset}={country}", spend, imps, link, val))


def nc(conn, day, cc, n, rev):
    conn.execute("INSERT INTO nc_by_country_daily (day, country_code, new_customers, new_customer_revenue) VALUES (%s,%s,%s,%s)", (day, cc, n, rev))


@pytest.fixture()
def cty(pg, fx):
    def wipe():
        pg.rollback()
        pg.execute("DELETE FROM meta_breakdown_daily")
        pg.execute("DELETE FROM nc_by_country_daily")
        pg.commit()
    wipe()
    yield pg
    wipe()


def seed(pg):
    """Spend Oct 14-16 (three days), new customers Oct 12-16. Oct 14 = 1,000 spend in the US, Oct 15 and 16 = 3,000 each (unequal days on purpose)."""
    for day, us in ((D(2026, 10, 14), 1000), (D(2026, 10, 15), 3000), (D(2026, 10, 16), 3000)):
        ins(pg, day, MAIN, "US", us * 0.75, us * 40, us * 0.6, us * 0.8)
        ins(pg, day, BACKUP, "US", us * 0.25, us * 10, us * 0.2, us * 0.2, eid="a2")             # the second ad account counts too
        ins(pg, day, MAIN, "GB", 100, 3000, 50, 90)
        ins(pg, day, MAIN, "GB", 999, 99999, 999, 999, bset="region", eid="a3")                   # another breakdown: never summed into the country table
    for day in (D(2026, 10, 12), D(2026, 10, 13)):                                                # before the spend coverage
        nc(pg, day, "US", 99, 9999)
    for day, n, rev in ((D(2026, 10, 14), 10, 700), (D(2026, 10, 15), 30, 2100), (D(2026, 10, 16), 30, 2100)):
        nc(pg, day, "US", n, rev)
        nc(pg, day, "GB", 2, 120)
        nc(pg, day, "ES", 1, 50)                                                                  # a country with new customers and no spend
        nc(pg, day, "unknown", 1, 40)
    pg.commit()


def get(client, qs="from=2026-10-12&to=2026-10-16"):
    r = client.get("/api/meta/countries?" + qs)
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def test_requires_login(anon):
    assert anon.get("/api/meta/countries").status_code in (401, 403)


def test_rows_are_ratios_of_sums_and_sorted_by_spend(client, cty):
    seed(cty)
    d = get(client)
    assert d["loaded"] and d["nc_loaded"]
    assert [r["code"] for r in d["rows"]] == ["US", "GB", "ES", "UNKNOWN"]                      # spend desc, then countries with only new customers by revenue
    us = d["rows"][0]
    assert us["country"] == "United States" and us["spend"] == 7000 and us["impressions"] == 7000 * 50 and us["link_clicks"] == 5600
    assert us["new_customers"] == 70 and us["nc_revenue"] == 4900.0                              # Oct 12-13 are cut: the spend breakdown starts Oct 14
    assert us["nc_roas"] == 0.7 and us["nc_cpa"] == 100.0 and us["aov"] == 70.0                 # 4,900 / 7,000; 7,000 / 70; 4,900 / 70
    assert us["cpm"] == 20.0 and us["ctr"] == round(5600 / 350000, 4) and us["cpc"] == 1.25     # 7,000 / 350 thousand impressions; 7,000 / 5,600
    assert us["conv_rate"] == round(70 / 5600, 4) and us["meta_roas"] == 1.0                     # Meta-reported purchase value 7,000 (both accounts) / 7,000
    gb = d["rows"][1]
    assert gb["spend"] == 300 and gb["impressions"] == 9000 and gb["new_customers"] == 6 and gb["nc_revenue"] == 360.0 and gb["nc_roas"] == 1.2 and gb["meta_roas"] == 0.9
    es = d["rows"][2]
    assert es["spend"] is None and es["share"] is None and es["nc_roas"] is None and es["cpm"] is None and es["new_customers"] == 3 and es["nc_revenue"] == 150.0
    assert d["rows"][3]["country"] == "Unknown"


def test_total_row_uses_every_countrys_new_customer_revenue(client, cty):
    seed(cty)
    t = get(client)["total"]
    assert t["country"] == "Total" and t["spend"] == 7300 and t["share"] == 1.0
    assert t["new_customers"] == 70 + 6 + 3 + 3 and t["nc_revenue"] == 4900 + 360 + 150 + 120      # ES and Unknown have no spend but their revenue is in the total
    assert t["nc_roas"] == round(5530 / 7300, 2) and t["nc_cpa"] == round(7300 / 82, 2) and t["aov"] == round(5530 / 82, 2)
    assert t["cpm"] == round(7300 * 1000 / (350000 + 9000), 2) and t["link_clicks"] == 5600 + 150
    assert t["conv_rate"] == round(82 / 5750, 4) and t["meta_roas"] == round((7000 + 270) / 7300, 2)
    shares = [r["share"] for r in get(client)["rows"] if r["share"] is not None]
    assert round(sum(shares), 3) == 1.0


def test_window_is_cut_to_the_days_both_tables_hold(client, cty):
    seed(cty)
    d = get(client)
    assert d["range"] == {"from": "2026-10-14", "to": "2026-10-16", "days": 3} and d["requested"]["from"] == "2026-10-12"
    assert d["coverage"]["spend_first"] == "2026-10-14" and d["coverage"]["nc_first"] == "2026-10-12"
    assert any("starts Oct 14, 2026" in n and "blank, not zero" in n for n in d["notes"])
    one = get(client, "from=2026-10-16&to=2026-10-16")
    us = one["rows"][0]
    assert us["spend"] == 3000 and us["nc_revenue"] == 2100.0 and us["nc_roas"] == 0.7 and not one["notes"]
    before = get(client, "from=2026-10-01&to=2026-10-10")
    assert before["loaded"] is False and "Oct 14, 2026" in before["message"] and before["rows"] == [] and before["total"] is None   # nothing is invented for uncovered days


def test_nc_table_not_loaded_leaves_those_columns_blank(client, cty):
    for day in (D(2026, 10, 14), D(2026, 10, 15)):
        ins(cty, day, MAIN, "US", 100, 5000, 50, 80)
    cty.commit()
    d = get(client, "from=2026-10-14&to=2026-10-15")
    assert d["loaded"] and d["nc_loaded"] is False
    us = d["rows"][0]
    assert us["spend"] == 200 and us["new_customers"] is None and us["nc_revenue"] is None and us["nc_roas"] is None and us["nc_cpa"] is None and us["meta_roas"] == 0.8
    assert d["total"]["nc_revenue"] is None and any("have not loaded" in n for n in d["notes"])


def test_no_country_breakdown_at_all_is_an_empty_state(client, cty):
    nc(cty, D(2026, 10, 14), "US", 5, 300)
    cty.commit()
    d = get(client, "from=2026-10-14&to=2026-10-15")
    assert d["loaded"] is False and "not loaded" in d["message"] and d["rows"] == []


def test_default_window_and_bad_input(client, cty):
    seed(cty)
    assert client.get("/api/meta/countries").status_code == 200
    assert client.get("/api/meta/countries?window=nope").status_code == 400
    assert client.get("/api/meta/countries?from=2026-10-16&to=2026-10-14").status_code == 400
