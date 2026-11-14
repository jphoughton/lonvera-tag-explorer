"""Competitors page, our own side (app/competitors_learn.py): Lonvera's share of spend by hook type / device / offer type from ad_learning_tags, the 'what is working for us' lines (top two values by NC ROAS with a
$1,000 floor), and our own landing pages (competitor_landing_pages.is_own, ad_family_lp) next to the competitor pages, which must never include them.
Tables of other jobs are created here with their contract columns and dropped afterwards."""
import pytest

from .helpers import restore_own_tables

from .test_api_competitors_learn import W1, W2, tag, world  # noqa: F401  (world is a fixture)

DAY = "2099-01-01"                  # later than any fixture day, so the 30-day window holds only these rows
LT = """CREATE TABLE IF NOT EXISTS ad_learning_tags (family_key text PRIMARY KEY, tag_version text, method text, confidence text, hook_type text, offer_type text, device text, cta text,
        hook_text text, why_it_works text, seen_on text, said_on text, model text, tagged_at timestamptz DEFAULT now())"""
OWN_ID, OWN_ID2 = "a" * 16, "b" * 16


def full():
    """All 15 competitor ads tagged: UGC demo, Question, Talking head, Discount."""
    ids = [f"a{i}" for i in range(1, 11)] + [f"b{i}" for i in range(1, 6)]
    return [tag(i, style="UGC demo", hook="Question", device="Talking head", offer="Discount") for i in ids]


@pytest.fixture(autouse=True)
def _real_own_tables_back(pg):
    """Tests here replace or drop the real own-tag tables; put them back from the migrations afterwards (other files share the database)."""
    yield
    restore_own_tables(pg)


def pid(pg):
    return pg.execute("SELECT id FROM products ORDER BY id LIMIT 1").fetchone()[0]


def clean(pg):
    pg.execute("DELETE FROM family_daily WHERE family_key LIKE 'zzo-%'"); pg.execute("DELETE FROM family_daily_upstack_nc WHERE family_key LIKE 'zzo-%'")
    pg.execute("DELETE FROM tracker_rows WHERE row_key LIKE 'zzo-%'")
    pg.execute("DROP TABLE IF EXISTS ad_learning_tags"); pg.execute("DROP TABLE IF EXISTS ad_family_lp")
    pg.commit()


@pytest.fixture()
def lonvera(pg):
    """Six Lonvera families in the far-future window. spend / NC revenue: Question zzo-0 600 / 600 + zzo-2 700 / 350 (1,300 on 0.73), Story zzo-1 500 / 900 (under the $1,000 floor),
    Statement zzo-3..5 3,000 / 3,300 each (9,000 on 1.10, three families so not thin)."""
    clean(pg)
    pg.execute(LT)
    P = pid(pg)
    spec = [("zzo-0", "Question", "Talking head", "Discount", 600, 600), ("zzo-1", "Story", "Voiceover", "Bundle", 500, 900), ("zzo-2", "Question", "Voiceover", "Discount", 700, 350),
            ("zzo-3", "Statement", "Text on screen", None, 3000, 3300), ("zzo-4", "Statement", "Text on screen", None, 3000, 3300), ("zzo-5", "Statement", "Text on screen", None, 3000, 3300)]
    for i, (fk, hook, dev, offer, sp, rev) in enumerate(spec):
        pg.execute("INSERT INTO tracker_rows (product_id, row_key, concept_id, ad_family, family_key, sheet_row, style) VALUES (%s,%s,%s,%s,%s,%s,'UGC demo')", (P, fk, fk, f"Family {i}", fk, i))
        pg.execute("INSERT INTO ad_learning_tags (family_key, hook_type, device, offer_type) VALUES (%s,%s,%s,%s)", (fk, hook, dev, offer))
        pg.execute("INSERT INTO family_daily (family_key, day, stage, spend, revenue) VALUES (%s,%s,'scaling',%s,%s)", (fk, DAY, sp, rev * 2))            # Meta reports twice what Upstack counts
        pg.execute("INSERT INTO family_daily_upstack_nc (family_key, day, stage, spend, revenue) VALUES (%s,%s,'scaling',%s,%s)", (fk, DAY, sp, rev))
    pg.commit()
    yield
    clean(pg)


def charts(d):
    return {c["key"]: c for c in d["learn"]["charts"]}


# ------------------------------------------------------------------------------------------------ share of spend and 'what is working'

def test_our_share_replaces_no_equivalent_for_hook_device_offer(client, world, lonvera):
    world(tables=("competitor_ad_tags",), tags=full())
    d = client.get("/api/competitors").get_json()
    ch = charts(d)
    for k in ("hook_type", "device", "offer_type"):
        assert ch[k]["has_lonvera"] is True
    rows = {r["value"]: r for r in ch["hook_type"]["rows"]}
    assert rows["Question"]["lonvera_share"] == pytest.approx(1300 / 10800, abs=1e-3) and rows["Statement"]["lonvera_share"] == pytest.approx(9000 / 10800, abs=1e-3)
    assert "Story" not in rows                                                                  # 4.6% of our spend and no competitor ad: under the 5% cut for values only we use
    assert rows["Question"]["share"] == 1.0 and rows["Question"]["hl"] is True              # every competitor ad is a question, we are at 12%
    dev = {r["value"]: r for r in ch["device"]["rows"]}
    assert dev["Talking head"]["share"] == 1.0 and dev["Talking head"]["lonvera_share"] == pytest.approx(600 / 10800, abs=1e-3)
    off = {r["value"]: r for r in ch["offer_type"]["rows"]}
    assert off["Discount"]["lonvera_share"] == pytest.approx(1300 / 1800, abs=1e-3)           # the base is the spend of families that carry an offer value (the three Statement families have none)
    assert ch["style"]["has_lonvera"] is True                                                 # tracker style is untouched


def test_working_lines_top_two_by_nc_roas_with_floor_and_thin_mark(client, world, lonvera):
    world(tables=("competitor_ad_tags",), tags=full())
    ch = charts(client.get("/api/competitors").get_json())
    w = ch["hook_type"]["working"]
    assert [i["value"] for i in w["items"]] == ["Statement", "Question"]                       # Story has $500: under the floor
    s, q = w["items"]
    assert s["nc_roas"] == pytest.approx(1.1, abs=1e-3) and s["spend"] == 9000 and s["ads"] == 3 and s["thin"] is False and s["competitor_share"] == 0.0
    assert q["nc_roas"] == pytest.approx(950 / 1300, abs=1e-3) and q["spend"] == 1300 and q["ads"] == 2 and q["thin"] is True and q["competitor_share"] == 1.0
    assert w["sentence"] == ("Our best hook type on NC ROAS is statement at 1.10 on $9.0K, competitors use it 0% of the time. "
                             "Next: question at 0.73 on $1.3K (thin sample), competitors 100%.")
    assert "working" in ch["style"] and ch["style"]["working"]["items"][0]["value"] == "UGC demo"        # every dimension with data gets the line
    d = client.get("/api/competitors").get_json()["learn"]
    assert d["best_through"] == DAY


def test_working_needs_the_floor_and_the_upstack_rows(client, world, lonvera, pg):
    world(tables=("competitor_ad_tags",), tags=full())
    pg.execute("UPDATE family_daily_upstack_nc SET spend = 100 WHERE family_key LIKE 'zzo-%' AND family_key <> 'zzo-3' AND family_key <> 'zzo-4' AND family_key <> 'zzo-5'"); pg.commit()
    w = charts(client.get("/api/competitors").get_json())["hook_type"]["working"]
    assert [i["value"] for i in w["items"]] == ["Statement"] and w["sentence"].startswith("Our best hook type on NC ROAS is statement") and "Next:" not in w["sentence"]
    pg.execute("DELETE FROM family_daily_upstack_nc WHERE family_key LIKE 'zzo-%'"); pg.commit()
    ch = charts(client.get("/api/competitors").get_json())
    assert "working" not in ch["hook_type"] and ch["hook_type"]["has_lonvera"] is True              # shares still show without Upstack rows


def test_without_the_learning_table_hook_device_offer_stay_competitor_only(client, world, pg):
    clean(pg)
    world(tables=("competitor_ad_tags",), tags=full())
    ch = charts(client.get("/api/competitors").get_json())
    assert all(ch[k]["has_lonvera"] is False and "working" not in ch[k] for k in ("hook_type", "device", "offer_type"))


def test_empty_learning_table_is_like_absent(client, world, pg):
    clean(pg)
    pg.execute(LT); pg.commit()
    world(tables=("competitor_ad_tags",), tags=full())
    try:
        assert charts(client.get("/api/competitors").get_json())["hook_type"]["has_lonvera"] is False
    finally:
        clean(pg)


# ------------------------------------------------------------------------------------------------ landing pages

COMP = [dict(lp_id="1" * 16, brand="Acme", page_type="Advertorial", key_theme="t1", proof_elements=["Reviews", "Doctor quote"], guarantee="60-day money back", ad_count=2),
        dict(lp_id="2" * 16, brand="Acme", page_type="Product page", key_theme="t2", proof_elements=["Reviews"], guarantee="30 day guarantee", ad_count=1),
        dict(lp_id="3" * 16, brand="Bolt", page_type="Advertorial", key_theme="t3", proof_elements=["Reviews", "Before and after"], guarantee="30-day", ad_count=1),
        dict(lp_id="4" * 16, brand="Bolt", page_type="Product page", key_theme="t4", proof_elements=[], guarantee=None, ad_count=1)]
LP_TABLES = ("competitor_landing_pages", "competitor_lp_media", "competitor_ad_lp")


def own_page(pg, lp_id=OWN_ID, week=W2, **kw):
    d = dict(week_start=week, lp_id=lp_id, brand="Lonvera", final_url="https://lonvera.com/try-bl", page_type="Advertorial", key_theme="Your liver is a vitamin warehouse", hero_headline="Why your face shows last night",
             selling_points=["Grass-fed liver", "90 day guarantee"], offer_summary="Buy 2 get 1", guarantee="90-day money back", proof_elements=["Reviews"], urgency_devices=[], ad_count=2, is_own=True)
    d.update(kw)
    pg.execute(f"INSERT INTO competitor_landing_pages ({','.join(d)}) VALUES ({','.join(['%s'] * len(d))})", tuple(d.values()))


def with_own_column(pg):
    pg.execute("ALTER TABLE competitor_landing_pages ADD COLUMN IF NOT EXISTS is_own boolean NOT NULL DEFAULT false"); pg.commit()


def test_column_absent_competitor_pages_unchanged_and_no_own_panel(client, world):
    world(tables=LP_TABLES, lps=COMP, ad_lp=[("a1", "1" * 16)])
    d = client.get("/api/competitors").get_json()
    assert d["lp_patterns"]["pages"] == 4 and "own_lp" not in d


def test_own_pages_never_enter_competitor_queries(client, world, pg):
    world(tables=LP_TABLES, lps=COMP, ad_lp=[("a1", "1" * 16)])
    with_own_column(pg)
    own_page(pg)
    own_page(pg, OWN_ID2, page_type="Advertorial", brand="Acme")                       # even a row that carries a competitor's brand name stays ours
    pg.commit()
    d = client.get("/api/competitors").get_json()
    assert d["lp_patterns"]["pages"] == 4
    types = {r["label"]: r["count"] for r in next(c for c in d["lp_patterns"]["charts"] if c["key"] == "page_type")["rows"]}
    assert types == {"Advertorial": 2, "Product page": 2}
    det = client.get("/api/competitors/Acme").get_json()
    assert {x["lp_id"] for x in det["landing_pages"]} == {"1" * 16, "2" * 16}
    assert all(x["brand"] != "Lonvera" for x in det["landing_pages"])


def test_own_panel_cards_performance_and_comparison(client, world, lonvera, pg):
    world(tables=LP_TABLES + ("competitor_ad_tags",), lps=COMP, ad_lp=[("a1", "1" * 16)], tags=full(), media=[OWN_ID])
    with_own_column(pg)
    own_page(pg)
    pg.execute("CREATE TABLE IF NOT EXISTS ad_family_lp (family_key text, lp_id text, week_start date, PRIMARY KEY (family_key, week_start))")
    pg.execute("INSERT INTO ad_family_lp VALUES ('zzo-3', %s, %s), ('zzo-0', %s, %s)", (OWN_ID, W2, OWN_ID, W2))
    pg.commit()
    o = client.get("/api/competitors").get_json()["own_lp"]
    assert o["week"] == W2 and o["days"] == 30 and o["competitor_pages"] == 4 and len(o["pages"]) == 1
    p = o["pages"][0]
    assert p["lp_id"] == OWN_ID and p["own"] is True and p["page_type"] == "Advertorial" and p["ad_count"] == 2 and p["lp_thumb_url"] == f"/api/competitors/lp-media/{OWN_ID}?week={W2}"
    assert p["spend"] == 3600 and p["meta_roas"] == pytest.approx((6600 + 1200) / 3600, abs=1e-3) and p["nc_roas"] == pytest.approx((3300 + 600) / 3600, abs=1e-3)
    assert [f["name"] for f in p["families"]] == ["Family 3", "Family 0"] and [f["spend"] for f in p["families"]] == [3000, 600] and p["more_families"] == 0
    assert o["compare"] == ["Page type: ours is advertorial. Competitors: 50% advertorial, 50% product page.",
                            "Proof: Competitors: 75% use reviews (we do); 25% use before and after (we do not); 25% use doctor quote (we do not).",
                            "Guarantee: ours is 90 days. Competitors: the most common is 30 days (67% of pages that state one)."]
    assert client.get(p["lp_thumb_url"]).status_code == 200                                    # our screenshot is served by the existing route


def test_own_panel_without_links_or_competitor_pages(client, world, pg):
    world(tables=LP_TABLES, lps=[], ad_lp=[])
    with_own_column(pg)
    own_page(pg)
    pg.commit()
    o = client.get("/api/competitors").get_json()["own_lp"]
    assert o["compare"] == [] and o["pages"][0]["ad_count"] == 0 and o["pages"][0]["spend"] == 0 and o["pages"][0]["families"] == [] and o["pages"][0]["nc_roas"] is None


def test_own_panel_hidden_when_no_own_rows_and_uses_latest_week_not_after_selected(client, world, pg):
    world(tables=LP_TABLES, lps=COMP, ad_lp=[("a1", "1" * 16)])
    with_own_column(pg)
    assert "own_lp" not in client.get("/api/competitors").get_json()
    own_page(pg, week=W1)
    pg.commit()
    assert client.get("/api/competitors").get_json()["own_lp"]["week"] == W1              # no row for the newest week: the latest earlier one
    assert client.get(f"/api/competitors?week={W1}").get_json()["own_lp"]["week"] == W1
    own_page(pg, OWN_ID2, week=W2)
    pg.commit()
    assert client.get("/api/competitors").get_json()["own_lp"]["week"] == W2
    assert client.get(f"/api/competitors?week={W1}").get_json()["own_lp"]["pages"][0]["lp_id"] == OWN_ID           # a past week never shows a later page
