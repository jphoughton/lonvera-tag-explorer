"""Tag lookup (GET /api/tag-lookup): a hand-built set of tracker rows, tags and Meta ads (ids and names all start TLK / 9xxx, removed again
after the module), and plain-Python oracles for every answer: which rule matched and which tags came back. Nothing is guessed: an unmatched ad never carries a tag."""
import datetime as dt
import json
import re

import pytest

from app import lookup as L

from .conftest import PASSWORD

URL = "/api/tag-lookup"
D = dt.date
ACCOUNT = "Lonvera Main"

LINES = {   # beef-liver product, codes that no other fixture uses
    "TLB1": dict(cluster="Hair Thinning", icp="Weekend Face", pain="Hair Loss", trig="Fear", story="story one", ing=[("Heme Iron", "Low ferritin", "Hair growth"), ("Copper", "Iron release", "Follicles")]),
    "TLB2": dict(cluster="Alcohol & Liver Support", icp="Beer Drinker", pain="Liver Health / Detox", trig="Shame", story="story two", ing=[("Choline", "Fatty liver", "Liver")]),
}
# id, product, concept, ad_family, status, sheet_row, tracker tags (cluster, icp, pain, emotion, ingredient), line, confidence, method, deleted
ROWS = [
    (9001, "beef-liver", "TLK-Alpha", "TLK-Alpha", "8 - Launched", 9001, (None, "BL1 Weekend Face", "Hair Loss", "Fear", "Heme Iron"), "TLB1", "high", "image", False),
    (9002, "beef-liver", "TLK-Alpha - Copy 2", "TLK-Alpha", "8 - Launched", 9002, (None, "BL2 The Stubborn Gut", "Weight Gain", "Desire", None), "TLB2", "medium", "video: 4 frames + Whisper transcript", False),
    (9003, "boneflex", "TLK-Beta", "TLK-Beta", "8 - Launched", 9003, (None, "BF1 Won't Sit It Out", "Joint Pain", "Fear", None), "TLB1", "low", "text only", False),
    (9004, "vita-bull", "TLK-Gamma", "TLK-Gamma", "5 - Briefed", 9004, (None, "VB1 Not Just Getting Older", "Low Energy", "Frustration", None), None, None, None, False),
    (9005, "beef-liver", "Victoria-TLK-Static-9.5", "Lonvera-TLK-Static-9.5", "8 - Launched", 9005, (None, "Mechanism", "Hair Loss", "Fear", None), "TLB1", "medium", "image", False),
    (9006, "beef-liver", "Internal-TLK-Static-4.2", "Internal-TLK-Static-4.2", "8 - Launched", 9006, (None, "Generic", "Liver Health / Detox", "Desire", "Choline"), "TLB2", "high", "image", False),
    (9007, "beef-liver", "TLK-Shared-1", "TLK-Shared", "8 - Launched", 9007, (None, "BL1 Weekend Face", "Hair Loss", "Fear", None), "TLB1", "high", "image", False),
    (9008, "boneflex", "TLK-Shared-2", "TLK-Shared", "8 - Launched", 9008, (None, "BF1 Won't Sit It Out", "Joint Pain", "Fear", None), None, None, None, False),
    (9009, "beef-liver", "TLK-Deleted", "TLK-Deleted", "8 - Launched", 9009, (None, None, None, None, None), "TLB1", "high", "image", True),
    (9010, "beef-liver", "TLK-Blank", "", "5 - Briefed", 9010, (None, None, None, None, None), "TLB2", "low", "image", False),
] + [(9100 + i, "beef-liver", f"TLK-Many-{i}", "TLK-Many", "8 - Launched", 9100 + i, (None, None, None, None, None), "TLB2", "high", "image", False) for i in range(12)]

# ad_id, name, meta family key, mapped tracker family (or None), rule
ADS = [
    ("910000000001", "TLK-Alpha", "tlk-alpha", "tlk-alpha", "exact"),
    ("910000000002", "TLK-Alpha - Copy 2", "tlk-alpha", "tlk-alpha", "exact"),
    ("910000000003", "TLK-Beta_VID_Offer_Generic_Lo-Fi", "tlk-beta_vid_offer_generic_lo-fi", "tlk-beta", "relaunch"),
    ("910000000004", "S-Lonvera-TLK-Static-9.5_IMG_Offer/Price-Led_Generic", "s-lonvera-tlk-static-9.5_img_offer/price-led_generic", None, None),
    ("910000000005", "TLK-Orphan", "tlk-orphan", None, None),
    ("910000000006", "S-Internal-TLK-Static-4.2_IMG_Hi-Fi_x", "s-internal-tlk-static-4.2_img_hi-fi_x", None, None),
    ("910000000007", "TLK-Shared", "tlk-shared", "tlk-shared", "exact"),
    ("910000000008", "TLK-Many", "tlk-many", "tlk-many", "exact"),
    ("910000000009", "TLK-Deleted", "tlk-deleted", "tlk-deleted", "exact"),
]
# family -> (first tracker row id, spend30, purch30, rev30, spend launch, purch launch, rev launch)
METRICS = {"tlk-alpha": (9001, 1200.0, 12, 1500.0, 5000.0, 60, 6200.0), "tlk-beta": (9003, 300.0, 0, 0.0, 450.0, 3, 380.0), "tlk-shared": (9007, 100.0, 2, 90.0, 100.0, 2, 90.0)}
UPSTACK_NC = {"tlk-alpha": (9001, 1200.0, 8, 1000.0, 5000.0, 40, 4100.0)}


@pytest.fixture(scope="module")
def tl(pg):
    pid = {slug: i for i, slug in pg.execute("SELECT id, slug FROM products").fetchall()}
    lid = {}
    for n, (code, ln) in enumerate(LINES.items()):
        lid[code] = pg.execute("""INSERT INTO taxonomy_lines (product_id, line_code, cluster, icp, pain_point, emotional_trigger, story, sort_order)
                                  VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                               (pid["beef-liver"], code, ln["cluster"], ln["icp"], ln["pain"], ln["trig"], ln["story"], 900 + n)).fetchone()[0]
        for j, (ing, att, sup) in enumerate(ln["ing"]):
            pg.execute("INSERT INTO line_ingredients (line_id, ingredient, attacks, supports, sort_order) VALUES (%s,%s,%s,%s,%s)", (lid[code], ing, att, sup, j))
    for (rid, prod, concept, fam, status, sheet, tt, code, conf, method, deleted) in ROWS:
        fk = fam.strip().lower() or "id:" + concept.lower()
        pg.execute("""INSERT INTO tracker_rows (id, product_id, row_key, concept_id, ad_family, family_key, sheet_row, status, ad_type, vendor, launch_date, tracker_cluster,
                          tracker_icp, tracker_pain, tracker_emotion, tracker_ingredient, raw, deleted_at)
                      VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'IMG','Madre',%s,%s,%s,%s,%s,%s,%s,%s)""",
                   (rid, pid[prod], concept + "#1", concept, fam, fk, sheet, status, D(2026, 10, 1), *tt, json.dumps({}), dt.datetime(2026, 11, 1) if deleted else None))
        if code:
            pg.execute("""INSERT INTO ad_tags (tracker_row_id, tag_set, version, is_current, line_id, cluster, icp, pain_point, emotional_trigger, confidence, method, seen, why, runner_up, tagger)
                          VALUES (%s,'linemap','linemap-v2-test',true,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'test')""",
                       (rid, lid[code], LINES[code]["cluster"], LINES[code]["icp"], LINES[code]["pain"], LINES[code]["trig"], conf, method,
                        f"seen in {concept}", f"why {concept}", "TLB9 Runner Up"))
    pg.execute("""INSERT INTO ad_hooks (tracker_row_id, hook, spoken, on_screen, hook_source, confidence, version) VALUES (9001, 'Your hair is not aging, your iron is', 'spoken', 'ON SCREEN', 'both', 'high', 'hooks-test')""")
    for (ad_id, name, mfk, tfam, rule) in ADS:
        pg.execute("""INSERT INTO meta_ads (ad_id, account, ad_name, meta_family_key, campaign_id, adset_id, adset_name, status, last_day) VALUES (%s,%s,%s,%s,NULL,'S1','Set one','ACTIVE',%s)""",
                   (ad_id, ACCOUNT, name, mfk, D(2026, 11, 3)))
        if tfam:
            pg.execute("INSERT INTO meta_family_map (meta_family_key, meta_family, tracker_family_key, rule) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING", (mfk, name, tfam, rule))
    for table, data in (("ad_metrics", METRICS), ("ad_metrics_upstack_nc", UPSTACK_NC)):
        for fk, (rid, s30, p30, r30, sl, pl, rl) in data.items():
            pg.execute(f"""INSERT INTO {table} (tracker_row_id, product_id, family_key, is_first_row, launch_date, spend_launch, purchases_launch, revenue_launch,
                               spend_7d, purchases_7d, revenue_7d, spend_30d, purchases_30d, revenue_30d, live) VALUES (%s,%s,%s,true,%s,%s,%s,%s,0,0,0,%s,%s,%s,true)""",
                       (rid, pg.execute("SELECT product_id FROM tracker_rows WHERE id = %s", (rid,)).fetchone()[0], fk, D(2026, 10, 1), sl, pl, rl, s30, p30, r30))
    pg.commit()
    yield
    pg.rollback()
    pg.execute("DELETE FROM ad_metrics_upstack_nc WHERE tracker_row_id >= 9000")
    pg.execute("DELETE FROM ad_metrics WHERE tracker_row_id >= 9000")
    pg.execute("DELETE FROM tracker_rows WHERE id >= 9000")             # ad_tags, ad_hooks cascade
    pg.execute("DELETE FROM taxonomy_lines WHERE line_code IN ('TLB1', 'TLB2')")
    pg.execute("DELETE FROM meta_family_map WHERE meta_family_key LIKE 'tlk-%'")
    pg.execute("DELETE FROM meta_ads WHERE ad_id LIKE '91000000000%'")
    pg.commit()


def look(client, q, extra="", status=200):
    r = client.get(URL, query_string=({"q": q} if q is not None else {}) | ({} if not extra else dict(x.split("=") for x in extra.split("&"))))
    assert r.status_code == status, (q, r.status_code, r.get_data(as_text=True)[:300])
    return r.get_json()


def row_of(rid):
    return next(r for r in ROWS if r[0] == rid)


def oracle_new_tags(rid):
    r = row_of(rid)
    code = r[7]
    ln = LINES[code]
    return {"line_code": code, "cluster": ln["cluster"], "icp": ln["icp"], "pain_point": ln["pain"], "emotional_trigger": ln["trig"], "confidence": r[8], "method": r[9],
            "ingredients": [i[0] for i in ln["ing"]]}


def check_tags(m, rid):
    want = oracle_new_tags(rid)
    got = m["new_tags"]
    assert got is not None and m["id"] == rid
    for k in ("line_code", "cluster", "icp", "pain_point", "emotional_trigger", "confidence", "method"):
        assert got[k] == want[k], (rid, k)
    assert [i["ingredient"] for i in got["ingredients"]] == want["ingredients"]
    assert got["ingredients"][0]["attacks"] and got["ingredients"][0]["supports"]
    assert got["seen"] == f"seen in {row_of(rid)[2]}" and got["why"] == f"why {row_of(rid)[2]}" and got["runner_up"] == "TLB9 Runner Up"
    assert got["line_name"] == f"{want['icp']} · {want['pain_point']}"


def canon(s):                       # the oracle's own spelling of the comparison rule
    t = re.sub(r"[^a-z0-9]", "", (s or "").lower())
    return re.sub(r"^(vb|bl|bf)\d+", "", t)


def oracle_marker(new, old):
    a, b = canon(new), canon(old)
    if not a or not b:
        return "unknown"
    return "agrees" if (a == b or a in b or b in a) else "differs"


# ------------------------------------------------------------------------------------------------------------ matching rules


def test_exact_platform_name_finds_every_tracker_row_of_the_family_with_its_own_tags(client, tl):
    d = look(client, "TLK-Alpha")
    assert d["match_kind"] == "exact" and d["total_matches"] == 2 and not d["truncated"]
    assert [m["id"] for m in d["matches"]] == [9001, 9002]
    check_tags(d["matches"][0], 9001)
    check_tags(d["matches"][1], 9002)
    assert d["matches"][0]["new_tags"]["line_code"] != d["matches"][1]["new_tags"]["line_code"]
    assert any("tags differ" in n for n in d["notes"])                                       # several rows, different tags: all shown, and it says so
    assert d["platform"]["ad_count"] == 1 and d["platform"]["names"] == ["TLK-Alpha"]
    assert d["matches"][0]["links"]["ad_page"] == "/ad/9001"
    assert d["platform"]["ads"][0]["ads_manager_url"].endswith("selected_ad_ids=910000000001")


@pytest.mark.parametrize("typed,kind", [
    ("tlk-alpha", "normalized"), ("  TLK-ALPHA  ", "normalized"), ('"TLK-Alpha"', "exact"), ("“TLK-Alpha”", "exact"), ("TLK-Alpha - Copy 2", "exact"),
    ("TLK-Alpha - Copy 7", "normalized"), ("TLK-Alpha - Copy", "normalized"), ("TLK-Alpha - Copy - Copy 3", "normalized"), ("TLK-Alpha | vb-merge-2026-10-22", "normalized"),
    ("TLK-Alpha  ​", "exact"), ("tlk-alpha - copy 12", "normalized"),
])
def test_case_spacing_quotes_and_copy_suffix_variants_reach_the_same_rows(client, tl, typed, kind):
    d = look(client, typed)
    assert d["match_kind"] == kind, typed
    assert [m["id"] for m in d["matches"]] == [9001, 9002]


def test_relaunch_encoded_name_matches_by_exact_text_and_by_family_key(client, tl):
    name = "TLK-Beta_VID_Offer_Generic_Lo-Fi"
    d = look(client, name)
    assert d["match_kind"] == "exact" and [m["id"] for m in d["matches"]] == [9003]
    d = look(client, name.upper() + " - Copy 3")
    assert d["match_kind"] == "normalized" and [m["id"] for m in d["matches"]] == [9003]


def test_a_meta_ad_id_and_an_ads_manager_link_find_the_ad(client, tl):
    d = look(client, "910000000003")
    assert d["match_kind"] == "id" and [m["id"] for m in d["matches"]] == [9003]
    assert d["platform"]["ads"][0]["ad_id"] == "910000000003" and d["platform"]["ads"][0]["name"] == "TLK-Beta_VID_Offer_Generic_Lo-Fi"
    d = look(client, "https://adsmanager.facebook.com/adsmanager/manage/ads?act=1671338033619223&selected_ad_ids=910000000001")
    assert d["match_kind"] == "id" and [m["id"] for m in d["matches"]] == [9001, 9002]
    d = look(client, "910000000005")                                                          # an ad with no tracker row
    assert d["match_kind"] == "none" and d["matches"] == [] and d["platform"]["ads"][0]["name"] == "TLK-Orphan"
    d = look(client, "999999999999")                                                          # a number that is no ad at all
    assert d["match_kind"] == "none" and d["platform"] is None


def test_concept_id_and_ad_family_find_tracker_rows_without_a_platform_name(client, tl):
    d = look(client, "tlk-gamma")
    assert d["match_kind"] == "concept" and [m["id"] for m in d["matches"]] == [9004] and d["platform"] is None
    d = look(client, "tlk-alpha-1")                                                                # not a concept id: stays unmatched
    assert d["match_kind"] == "none"
    d = look(client, "Victoria-TLK-Static-9.5")
    assert d["match_kind"] == "concept" and [m["id"] for m in d["matches"]] == [9005]
    d = look(client, "lonvera-tlk-static-9.5")                                                # the tracker's Ad Family (a different row's concept id has another name)
    assert d["match_kind"] == "family" and [m["id"] for m in d["matches"]] == [9005]


def test_a_platform_name_that_is_the_tracker_name_plus_prefix_and_encoding_matches_by_the_base(client, tl):
    d = look(client, "S-Lonvera-TLK-Static-9.5_IMG_Offer/Price-Led_Generic")
    assert d["match_kind"] == "family" and [m["id"] for m in d["matches"]] == [9005]
    assert d["platform"]["ad_count"] == 1 and any("prefix or an encoded suffix" in n for n in d["notes"])
    d = look(client, "S-Internal-TLK-Static-4.2_IMG_Hi-Fi_x")                                  # base equals a tracker Concept ID
    assert d["match_kind"] == "family" and [m["id"] for m in d["matches"]] == [9006]
    d = look(client, "S-Internal-TLK-Static-4.2_IMG_Hi-Fi_other_ad_that_does_not_exist")       # same base, a name the platform does not have: still the tracker name underneath
    assert d["match_kind"] == "family" and [m["id"] for m in d["matches"]] == [9006]
    d = look(client, "S-Internal-TLK-Static-4.3_IMG_Hi-Fi_x")                                   # another version number: no match, never a neighbour
    assert d["match_kind"] == "none" and d["matches"] == []


def test_a_name_shared_by_two_products_shows_both_rows(client, tl):
    d = look(client, "TLK-Shared")
    assert d["match_kind"] == "exact" and sorted(m["product"]["slug"] for m in d["matches"]) == ["beef-liver", "boneflex"]
    assert any("more than one product" in n for n in d["notes"])
    tagged = [m for m in d["matches"] if m["new_tags"]]
    assert [m["id"] for m in tagged] == [9007]
    assert next(m for m in d["matches"] if m["id"] == 9008)["new_tags_reason"] == "not tagged yet"


def test_more_than_ten_rows_are_cut_and_the_count_says_so(client, tl):
    d = look(client, "TLK-Many")
    assert d["total_matches"] == 12 and len(d["matches"]) == 10 and d["truncated"] is True


# ------------------------------------------------------------------------------------------------------------ what a match carries


def test_untagged_row_has_no_new_tags_a_reason_and_still_its_tracker_tags(client, tl):
    m = look(client, "TLK-Gamma")["matches"][0]
    assert m["new_tags"] is None and m["new_tags_reason"] == "not tagged yet" and m["status"] == "5 - Briefed"


def test_text_only_tag_is_shown_with_its_caveat(client, tl):
    m = look(client, "TLK-Beta")["matches"][0]
    assert m["new_tags"]["text_only"] is True and "copy only" in m["new_tags"]["caveat"] and m["new_tags"]["confidence"] == "low"
    img = look(client, "TLK-Alpha")["matches"][0]
    assert img["new_tags"]["text_only"] is False and img["new_tags"]["caveat"] is None


def test_removed_tracker_rows_are_never_returned(client, tl):
    d = look(client, "TLK-Deleted")
    assert d["match_kind"] == "none" and d["matches"] == [] and d["platform"]["ad_count"] == 1
    assert "no tracker row" in d["reason"]






# ------------------------------------------------------------------------------------------------------------ no match, never a guess


def test_unmatched_platform_ad_is_an_honest_no_match_and_no_tags(client, tl):
    d = look(client, "TLK-Orphan")
    assert d["match_kind"] == "none" and d["matches"] == [] and d["total_matches"] == 0
    assert "no tracker row" in d["reason"] and d["platform"]["ads"][0]["ad_id"] == "910000000005"
    assert d["matches"] == [] and "suggestions" not in d



def test_hostile_text_is_just_text(client, tl):
    for q in ("'; DROP TABLE tracker_rows; --", "%_%", "\\", "TLK-Alpha' OR '1'='1", "<script>alert(1)</script>", "x" * 300):
        d = look(client, q)
        assert d["match_kind"] == "none" and d["matches"] == []
    assert client.get(URL, query_string={"q": "a\x00b"}).status_code == 400


def test_empty_and_too_long_text_is_a_400_with_a_plain_message(client, tl):
    for q in (None, "", "   ", '""', "x" * 301):
        r = client.get(URL, query_string={"q": q} if q is not None else {})
        assert r.status_code == 400 and r.get_json()["error"]
    assert "300" in client.get(URL, query_string={"q": "x" * 301}).get_json()["error"]


# ------------------------------------------------------------------------------------------------------------ plumbing


def test_unauthenticated_is_401_json(anon, tl):
    r = anon.get(URL, query_string={"q": "TLK-Alpha"})
    assert r.status_code == 401 and r.get_json() == {"error": "unauthorized"}


def test_the_lookup_page_route_serves_the_app_shell(anon):
    r = anon.get("/lookup")
    assert r.status_code == 200 and "text/html" in r.content_type


def test_a_slow_query_answers_503_query_timed_out_not_a_hang(client, tl, monkeypatch):
    monkeypatch.setattr(L, "QUERY_TIMEOUT_MS", 50)
    monkeypatch.setattr(L.DbStore, "ads_by_name", lambda self, name: self.conn.execute("SELECT pg_sleep(2)").fetchall())
    r = client.get(URL, query_string={"q": "TLK-Alpha"})
    assert r.status_code == 503 and r.get_json() == {"error": "query timed out"}


def test_lookup_is_read_only(client, pg, tl):
    before = pg.execute("SELECT (SELECT count(*) FROM tracker_rows), (SELECT count(*) FROM ad_tags), (SELECT count(*) FROM meta_ads)").fetchone()
    for q in ("TLK-Alpha", "TLK-Orphan", "nothing"):
        look(client, q)
    pg.rollback()
    assert pg.execute("SELECT (SELECT count(*) FROM tracker_rows), (SELECT count(*) FROM ad_tags), (SELECT count(*) FROM meta_ads)").fetchone() == before


def test_parse_query_unit():
    p = L.parse_query('  "TLK  Alpha - Copy 3 "  ')
    assert p["text"] == "TLK Alpha - Copy 3" and p["key"] == "tlk alpha" and p["ad_id"] is None
    assert L.parse_query("12345678901234")["ad_id"] == "12345678901234" and L.parse_query("1234567")["ad_id"] is None
    assert L.family_of("Bull-52 - Copy - Copy 2 | vb-merge-2026-10-22") == "bull-52"
    assert L.parse_query("S-Foo-1_VID_x_y")["derived"] == ["s-foo-1", "foo-1"]
