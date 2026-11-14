"""Competitors learning layer (app/competitors_learn.py): model-read tags and transcripts, tag filters, the share charts against Lonvera's own spend, brand tag mix, and tagged landing pages.
The tag / transcript / landing-page tables are real migration tables (261, 262) that other tests share, so the fixture swaps them for contract-column versions per test (or drops them to test the absent case) and puts the real tables back afterwards from the migration files; with them absent the payloads must be unchanged."""
import glob
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REAL = ("competitor_ad_tags", "competitor_ad_transcript", "competitor_landing_pages", "competitor_lp_media", "competitor_ad_lp")


def restore_real_tables(pg):
    """Drop whatever this test left and re-run the migrations that own the competitor learning tables (idempotent CREATE ... IF NOT EXISTS)."""
    for t in REAL:
        pg.execute(f"DROP TABLE IF EXISTS {t}")
    for name in ("263_competitor_ad_tags.sql", "264_competitor_landing_pages.sql"):
        for path in glob.glob(os.path.join(ROOT, "migrations", name)):
            with open(path) as f:
                pg.execute(f.read())
    pg.commit()

W1, W2 = "2026-11-09", "2026-11-16"

DDL = {
    "competitor_ad_transcript": "CREATE TABLE IF NOT EXISTS competitor_ad_transcript (library_id text PRIMARY KEY, transcript text NOT NULL, language text, duration_s numeric, model text, created_at timestamptz DEFAULT now())",
    "competitor_ad_tags": """CREATE TABLE IF NOT EXISTS competitor_ad_tags (library_id text PRIMARY KEY, concept_key text, tag_version text, method text, confidence text, line_code text, cluster text, icp text, pain_point text,
        emotional_trigger text, is_new_angle boolean, new_angle_label text, style text, fidelity text, proof_mechanism text, emotion text, hook_type text, offer_type text, device text, cta text, hook_text text,
        why_it_works text, seen_on text, said_on text, model text, tagged_at timestamptz DEFAULT now())""",
    "competitor_landing_pages": """CREATE TABLE IF NOT EXISTS competitor_landing_pages (week_start date, lp_id text, url_key text, final_url text, brand text, http_status int, fetched_at timestamptz, ad_count int, page_type text,
        key_theme text, hero_headline text, selling_points text[], offer_summary text, price_text text, guarantee text, proof_elements text[], urgency_devices text[], objections text[], cta text, cluster text, icp text,
        pain_point text, why_it_works text, method text, confidence text, content_hash text, changed_from_prior boolean, change_note text, fetch_error text, model text, tagged_at timestamptz, PRIMARY KEY (week_start, lp_id))""",
    "competitor_lp_media": "CREATE TABLE IF NOT EXISTS competitor_lp_media (week_start date, lp_id text, content_type text, width int, height int, bytes bytea, fetched_at timestamptz DEFAULT now(), PRIMARY KEY (week_start, lp_id))",
    "competitor_ad_lp": "CREATE TABLE IF NOT EXISTS competitor_ad_lp (week_start date, library_id text, lp_id text, PRIMARY KEY (week_start, library_id))",
}


def ads_rows():
    # (week, lid, brand, days, fmt, concept_key)
    out = []
    for i in range(1, 11):                       # Acme: 10 ads
        out.append((W2, f"a{i}", "Acme", 10 + i, "video" if i % 2 else "image", f"Acme:a{i}"))
    for i in range(1, 6):                        # Bolt: 5 ads
        out.append((W2, f"b{i}", "Bolt", 5 + i, "image", f"Bolt:b{i}"))
    out.append((W1, "a1", "Acme", 3, "video", "Acme:a1"))
    return out


@pytest.fixture()
def world(pg):
    def build(tables=(), tags=(), transcripts=(), lps=(), ad_lp=(), media=(), lonvera=None, classes=()):
        for t in ("competitor_ad_tags", "competitor_ad_transcript", "competitor_landing_pages", "competitor_lp_media", "competitor_ad_lp"):
            pg.execute(f"DROP TABLE IF EXISTS {t}")
        for t in tables:
            pg.execute(DDL[t])
        pg.execute("DELETE FROM competitor_ads"); pg.execute("DELETE FROM competitor_concept_class"); pg.execute("DELETE FROM competitor_brands")
        for b in ("Acme", "Bolt"):
            pg.execute("INSERT INTO competitor_brands (brand, product_type, search_phrases, name_match, last_ok_at) VALUES (%s,'x',%s,%s, now())", (b, [b.lower()], b))
        for (wk, lid, brand, days, fmt, ck) in ads_rows():
            pg.execute("INSERT INTO competitor_ads (week_start, library_id, brand, start_date, days_running, variant_count, format, primary_text, hook, landing_url, concept_key, seen_on) VALUES (%s,%s,%s,%s::date - %s,%s,1,%s,%s,%s,%s,%s,%s)",
                       (wk, lid, brand, wk, days, days, fmt, f"text {lid}", f"hook {lid}", "https://acme.com/p", ck, wk))
        for ck, brand, cluster, pain, new, label, prod in classes:
            pg.execute("INSERT INTO competitor_concept_class (concept_key, brand, mapped_product, cluster, pain_point, is_new_angle, new_angle_label) VALUES (%s,%s,%s,%s,%s,%s,%s)", (ck, brand, prod, cluster, pain, new, label))
        cols = ("library_id", "style", "hook_type", "device", "offer_type", "proof_mechanism", "emotion", "cluster", "pain_point", "is_new_angle", "new_angle_label", "why_it_works", "hook_text", "seen_on", "said_on")
        for t in tags:
            d = dict(zip(cols, t))
            pg.execute(f"INSERT INTO competitor_ad_tags ({','.join(d)}) VALUES ({','.join(['%s'] * len(d))})", tuple(d.values()))
        for lid, text in transcripts:
            pg.execute("INSERT INTO competitor_ad_transcript (library_id, transcript) VALUES (%s,%s)", (lid, text))
        for r in lps:
            d = dict(week_start=W2, **r)
            pg.execute(f"INSERT INTO competitor_landing_pages ({','.join(d)}) VALUES ({','.join(['%s'] * len(d))})", tuple(d.values()))
        for lid, lp in ad_lp:
            pg.execute("INSERT INTO competitor_ad_lp (week_start, library_id, lp_id) VALUES (%s,%s,%s)", (W2, lid, lp))
        for lp in media:
            pg.execute("INSERT INTO competitor_lp_media (week_start, lp_id, content_type, bytes) VALUES (%s,%s,'image/png',%s)", (W2, lp, b"x"))
        pg.commit()
    yield build
    pg.rollback()
    for t in ("competitor_ads", "competitor_concept_class", "competitor_brands"):
        pg.execute(f"DELETE FROM {t}")
    pg.commit()
    restore_real_tables(pg)


def tag(lid, style=None, hook=None, device=None, offer=None, proof=None, emotion=None, cluster=None, pain=None, new=False, label=None, why=None, quote=None, seen=None, said=None):
    return (lid, style, hook, device, offer, proof, emotion, cluster, pain, new, label, why, quote, seen, said)


def many(style_counts, hook="Question", device="Talking head"):
    """Tags for Acme/Bolt ads of W2 with the given style counts, in order."""
    ids = [f"a{i}" for i in range(1, 11)] + [f"b{i}" for i in range(1, 6)]
    out, i = [], 0
    for st, n in style_counts:
        for _ in range(n):
            out.append(tag(ids[i], style=st, hook=hook, device=device))
            i += 1
    return out


def test_all_tables_absent_payload_has_no_new_keys(client, world):
    world()
    d = client.get("/api/competitors?style=UGC").get_json()
    assert not {"tags", "learn", "lp_patterns"} & set(d)
    assert len(d["brands"]) == 2 and sum(b["active_ads"] for b in d["brands"]) == 15          # the filter is ignored without tags
    det = client.get("/api/competitors/Acme").get_json()
    assert "tag_mix" not in det and all(set(p) == {"page", "ads"} for p in det["landing_pages"])
    assert all("tag_chips" not in c and "landing_page" not in c for c in d["spotlight"])


def test_empty_tables_degrade_to_the_empty_message_flag(client, world):
    world(tables=("competitor_ad_tags", "competitor_ad_transcript", "competitor_landing_pages", "competitor_lp_media", "competitor_ad_lp"))
    d = client.get("/api/competitors").get_json()
    assert d["tags"]["empty"] is True and d["tags"]["options"] == {} and "learn" not in d and "lp_patterns" not in d
    assert sum(b["active_ads"] for b in d["brands"]) == 15 and "tag_mix" not in client.get("/api/competitors/Acme").get_json()


def test_tags_preferred_over_text_class_and_card_fields(client, world):
    world(tables=("competitor_ad_tags", "competitor_ad_transcript"),
          classes=[("Acme:a1", "Acme", "Energy", "Low T", False, None, "vita-bull")],
          tags=[tag("a1", style="Testimonial", hook="Question", device="Talking head", offer="Discount", proof="Reviews", emotion="Relief", cluster="Joints", pain="Knee pain", new=True, label="Marrow for joints",
                    why="Names the pain in the first second.", quote="Do your knees hurt?", seen="A man on a couch", said="Do your knees hurt")],
          transcripts=[("a1", "Do your knees hurt on the stairs")])
    d = client.get("/api/competitors").get_json()
    card = next(c for c in d["spotlight"] if c["library_id"] == "a1")
    assert card["angle"] == "Joints / Knee pain" and card["is_new_angle"] is True and card["new_angle_label"] == "Marrow for joints"      # tag beats the text-only class (Energy / Low T)
    assert card["why_it_works"] == "Names the pain in the first second." and card["hook_quote"] == "Do your knees hurt?" and card["transcript"].startswith("Do your knees")
    assert card["seen_on"] == "A man on a couch" and card["said_on"] == "Do your knees hurt"
    chips = {c["key"]: c for c in card["tag_chips"]}
    assert chips["style"]["filter"] == "style" and chips["hook_type"]["filter"] == "hook" and chips["device"]["filter"] == "device" and chips["offer_type"]["filter"] == "offer"
    assert chips["proof_mechanism"]["filter"] is None and chips["emotion"]["value"] == "Relief"
    other = next(c for c in d["spotlight"] if c["library_id"] != "a1")
    assert "tag_chips" not in other and "transcript" not in other


def test_each_filter_narrows_everything(client, world):
    world(tables=("competitor_ad_tags",), tags=[tag("a1", "UGC", "Question", "Talking head", "Discount"), tag("a2", "UGC", "Story", "Split screen", "Bundle"), tag("a3", "Studio", "Question", "Talking head", "Discount"),
                                               tag("b1", "UGC", "Question", "Split screen", None), tag("b2", "Studio", "Stat", "Text on screen", "Bundle")])
    base = client.get("/api/competitors").get_json()
    assert base["tags"]["options"]["style"] == [{"value": "UGC", "count": 3}, {"value": "Studio", "count": 2}]
    for qs, total in (("style=UGC", 3), ("style=ugc", 3), ("hook=Question", 3), ("device=Split+screen", 2), ("offer=Discount", 2), ("style=UGC&hook=Question", 2), ("style=UGC&device=Text+on+screen", 0)):
        d = client.get("/api/competitors?" + qs).get_json()
        assert sum(b["active_ads"] for b in d["brands"]) == total, qs
        assert d["pulse"]["active_ads"] == total
        assert d["tags"]["options"] == base["tags"]["options"]          # options come from the whole week, so a filter can be changed
    d = client.get("/api/competitors?style=UGC").get_json()
    assert d["tags"]["active"] == {"style": "UGC"} and d["learn"]["tagged_ads"] == 3
    assert all(c["library_id"] in {"a1", "a2", "b1"} for c in d["spotlight"])
    assert sum(g["ads"] for g in client.get("/api/competitors/Acme?style=UGC").get_json()["angles"]) == 2
    assert client.get("/api/competitors?style=" + "x" * 90).status_code == 400
    fmt = client.get("/api/competitors?format=video&style=UGC").get_json()
    assert sum(b["active_ads"] for b in fmt["brands"]) == 1                    # video ads are the odd-numbered ones: a1 (UGC) and a3 (Studio) in the filter set, so only a1


def test_share_math_gap_highlight_and_sentence(client, world, pg):
    # 20 tagged ads would be ideal; use 15: Testimonial x10 (67%), Studio x3, Demo x2
    world(tables=("competitor_ad_tags",), tags=many([("Testimonial", 10), ("Studio", 3), ("Demo", 2)]))
    pg.execute("DELETE FROM family_daily WHERE family_key LIKE 'zzl-%'"); pg.execute("DELETE FROM tracker_rows WHERE row_key LIKE 'zzl-%'")
    pid = pg.execute("SELECT id FROM products ORDER BY id LIMIT 1").fetchone()
    if pid is None:
        pid = pg.execute("INSERT INTO products (slug, name) VALUES ('zzl-test', 'Zzl') RETURNING id").fetchone()
    for i, (st, spend) in enumerate((("Testimonial", 10), ("Studio", 70), ("Demo", 20))):
        pg.execute("INSERT INTO tracker_rows (product_id, row_key, concept_id, family_key, sheet_row, style, proof, tracker_emotion) VALUES (%s,%s,%s,%s,%s,%s,'Reviews','Relief')", (pid[0], f"zzl-{i}", f"zzl-{i}", f"zzl-{i}", i, st))
        pg.execute("INSERT INTO family_daily (family_key, day, stage, spend) VALUES (%s, '2099-01-01', 'scaling', %s)", (f"zzl-{i}", spend))
    pg.commit()
    try:
        d = client.get("/api/competitors").get_json()
        ch = {c["key"]: c for c in d["learn"]["charts"]}
        style = ch["style"]
        rows = {r["value"]: r for r in style["rows"]}
        assert style["n"] == 15 and style["has_lonvera"] is True
        assert rows["Testimonial"]["share"] == pytest.approx(10 / 15, abs=1e-3) and rows["Testimonial"]["lonvera_share"] == pytest.approx(0.1, abs=1e-3)
        assert rows["Testimonial"]["hl"] is True and rows["Studio"]["hl"] is False and rows["Demo"]["hl"] is False
        assert style["sentence"] == "Competitors lean on testimonial 67% of the time, Lonvera 10% of spend." and d["learn"]["lonvera_through"]
        assert "emotion" not in ch                                                        # no competitor emotion tags in this fixture
        assert ch["hook_type"]["has_lonvera"] is False and ch["hook_type"]["rows"][0]["lonvera_share"] is None and ch["hook_type"]["sentence"].endswith("of the time.")
        assert not any(r["hl"] for r in ch["hook_type"]["rows"])
    finally:
        pg.execute("DELETE FROM family_daily WHERE family_key LIKE 'zzl-%'"); pg.execute("DELETE FROM tracker_rows WHERE row_key LIKE 'zzl-%'"); pg.commit()


def test_gap_rule_needs_15_points_and_5_ads(client, world, pg):
    pg.execute("DELETE FROM family_daily WHERE family_key LIKE 'zzl-%'"); pg.execute("DELETE FROM tracker_rows WHERE row_key LIKE 'zzl-%'")
    pid = pg.execute("SELECT id FROM products ORDER BY id LIMIT 1").fetchone() or pg.execute("INSERT INTO products (slug, name) VALUES ('zzl-test', 'Zzl') RETURNING id").fetchone()
    # Lonvera: Testimonial 50%, Studio 50% of spend. Competitors: Testimonial 7/15 = 46.7% (below), Studio 3/15, Demo 5/15 = 33.3% vs 0 (gap 33 and 5 ads -> highlighted), Other 0
    for i, st in enumerate(("Testimonial", "Studio")):
        pg.execute("INSERT INTO tracker_rows (product_id, row_key, concept_id, family_key, sheet_row, style) VALUES (%s,%s,%s,%s,%s,%s)", (pid[0], f"zzl-{i}", f"zzl-{i}", f"zzl-{i}", i, st))
        pg.execute("INSERT INTO family_daily (family_key, day, stage, spend) VALUES (%s, '2099-01-01', 'scaling', 50)", (f"zzl-{i}",))
    pg.commit()
    world(tables=("competitor_ad_tags",), tags=many([("Testimonial", 7), ("Studio", 3), ("Demo", 5)]))
    try:
        rows = {r["value"]: r for r in next(c for c in client.get("/api/competitors").get_json()["learn"]["charts"] if c["key"] == "style")["rows"]}
        assert rows["Demo"]["hl"] is True and rows["Demo"]["lonvera_share"] == 0.0           # 33 points, 5 ads
        assert rows["Testimonial"]["hl"] is False                                              # 46.7 vs 50: competitors lean less
        world(tables=("competitor_ad_tags",), tags=many([("Testimonial", 5), ("Studio", 5), ("Demo", 4)]))
        rows = {r["value"]: r for r in next(c for c in client.get("/api/competitors").get_json()["learn"]["charts"] if c["key"] == "style")["rows"]}
        assert rows["Demo"]["hl"] is False                                                     # 4 ads: below the minimum even though the gap is 28 points
        world(tables=("competitor_ad_tags",), tags=many([("Testimonial", 5), ("Studio", 5), ("Demo", 5)]))
        pg.execute("UPDATE family_daily SET spend = 100 WHERE family_key = 'zzl-0'"); pg.execute("UPDATE family_daily SET spend = 0 WHERE family_key = 'zzl-1'"); pg.commit()
        rows = {r["value"]: r for r in next(c for c in client.get("/api/competitors").get_json()["learn"]["charts"] if c["key"] == "style")["rows"]}
        assert rows["Studio"]["hl"] is True and rows["Studio"]["share"] == pytest.approx(1 / 3, abs=1e-3)   # 33.3 vs 0, 5 ads
        assert rows["Testimonial"]["hl"] is False                                              # 33.3 vs 100
    finally:
        pg.execute("DELETE FROM family_daily WHERE family_key LIKE 'zzl-%'"); pg.execute("DELETE FROM tracker_rows WHERE row_key LIKE 'zzl-%'"); pg.commit()


def test_exact_15_point_gap_is_a_highlight(client, world, pg):
    pg.execute("DELETE FROM family_daily WHERE family_key LIKE 'zzl-%'"); pg.execute("DELETE FROM tracker_rows WHERE row_key LIKE 'zzl-%'")
    pid = pg.execute("SELECT id FROM products ORDER BY id LIMIT 1").fetchone() or pg.execute("INSERT INTO products (slug, name) VALUES ('zzl-test', 'Zzl') RETURNING id").fetchone()
    for i, (st, sp) in enumerate((("Testimonial", 25), ("Studio", 75))):                  # Lonvera: 25% / 75%
        pg.execute("INSERT INTO tracker_rows (product_id, row_key, concept_id, family_key, sheet_row, style) VALUES (%s,%s,%s,%s,%s,%s)", (pid[0], f"zzl-{i}", f"zzl-{i}", f"zzl-{i}", i, st))
        pg.execute("INSERT INTO family_daily (family_key, day, stage, spend) VALUES (%s, '2099-01-01', 'scaling', %s)", (f"zzl-{i}", sp))
    pg.commit()
    world(tables=("competitor_ad_tags",), tags=many([("Testimonial", 6), ("Studio", 9)]))        # 6 of 15 = 40%, exactly 15 points above 25%
    try:
        rows = {r["value"]: r for r in next(c for c in client.get("/api/competitors").get_json()["learn"]["charts"] if c["key"] == "style")["rows"]}
        assert rows["Testimonial"]["hl"] is True and rows["Studio"]["hl"] is False
    finally:
        pg.execute("DELETE FROM family_daily WHERE family_key LIKE 'zzl-%'"); pg.execute("DELETE FROM tracker_rows WHERE row_key LIKE 'zzl-%'"); pg.commit()


def test_without_lonvera_data_competitors_only(client, world, monkeypatch):
    world(tables=("competitor_ad_tags",), tags=many([("Testimonial", 8), ("Studio", 7)]))
    monkeypatch.setattr("app.competitors_learn.lonvera_shares", lambda conn: None)
    d = client.get("/api/competitors").get_json()
    st = next(c for c in d["learn"]["charts"] if c["key"] == "style")
    assert st["has_lonvera"] is False and all(r["lonvera_share"] is None and r["hl"] is False for r in st["rows"]) and "Lonvera" not in st["sentence"] and d["learn"]["lonvera_through"] is None


def test_combos_top_five_with_example(client, world):
    tg = [tag(f"a{i}", "UGC", "Question", "Talking head") for i in range(1, 5)] + [tag("a5", "UGC", "Story", "Split screen"), tag("b1", "UGC", "Story", "Split screen"), tag("b2", "UGC", "Stat", "Text on screen")]
    world(tables=("competitor_ad_tags",), tags=tg)
    c = client.get("/api/competitors").get_json()["learn"]["combos"]
    assert [(x["hook_type"], x["device"], x["ads"], x["brands"]) for x in c][:3] == [("Question", "Talking head", 4, 1), ("Story", "Split screen", 2, 2), ("Stat", "Text on screen", 1, 1)]
    assert c[0]["example"]["library_id"] == "a4" and c[0]["example"]["ad_library_url"].endswith("?id=a4")          # longest-running of the four


def test_brand_tag_mix(client, world):
    world(tables=("competitor_ad_tags",), tags=[tag("a1", "UGC", "Question", "Talking head"), tag("a2", "UGC", "Story", "Talking head"), tag("a3", "Studio", "Story", "Split screen"), tag("b1", "Demo", "Stat", "Text on screen")])
    mix = client.get("/api/competitors/Acme").get_json()["tag_mix"]
    assert mix["style"] == [{"value": "UGC", "count": 2}, {"value": "Studio", "count": 1}] and mix["hook_type"][0] == {"value": "Story", "count": 2} and mix["device"][0] == {"value": "Talking head", "count": 2}
    assert client.get("/api/competitors/Bolt").get_json()["tag_mix"]["style"] == [{"value": "Demo", "count": 1}]


LP1 = dict(lp_id="lp1", brand="Acme", final_url="https://acme.com/join", ad_count=4, page_type="Advertorial", key_theme="Joints are a nutrient problem", hero_headline="Why your knees ache",
           selling_points=["Marrow", "No fillers", "Made in the USA", "Third party tested", "60 day guarantee", "extra six"], offer_summary="Buy 2 get 1", price_text="$49", guarantee="60-day money back",
           proof_elements=["Reviews", "Doctor quote"], urgency_devices=["Countdown timer"], objections=["Does it work"], cta="Try it", why_it_works="Story first", changed_from_prior=True, change_note="New hero headline")
LP2 = dict(lp_id="lp2", brand="Acme", final_url="https://acme.com/shop", ad_count=2, page_type="Product page", key_theme="Simple shop", selling_points=["no fillers", "Marrow"], guarantee="30 day guarantee",
           proof_elements=["reviews"], urgency_devices=["Countdown timer", "Low stock"], changed_from_prior=False)
LP3 = dict(lp_id="lp3", brand="Bolt", final_url="https://bolt.com/x", ad_count=1, page_type="Advertorial", key_theme="Bolt story", selling_points=["No fillers"], guarantee="90 days", proof_elements=["Reviews"])


def test_landing_page_cards_patterns_and_ad_links(client, world):
    world(tables=("competitor_ad_tags", "competitor_landing_pages", "competitor_lp_media", "competitor_ad_lp"), tags=[tag("a1", "UGC")], lps=[LP1, LP2, LP3], media=["lp1"],
          ad_lp=[("a1", "lp1"), ("a2", "lp1"), ("a3", "lp2"), ("b1", "lp3"), ("b2", "lp3")])
    det = client.get("/api/competitors/Acme").get_json()
    lps = det["landing_pages"]
    assert [x["lp_id"] for x in lps] == ["lp1", "lp2"]
    a = lps[0]
    assert a["lp_thumb_url"] == f"/api/competitors/lp-media/lp1?week={W2}" and lps[1]["lp_thumb_url"] is None
    assert a["changed"] is True and a["change_note"] == "New hero headline" and lps[1]["changed"] is False and lps[1]["change_note"] is None
    assert a["selling_points"][:2] == ["Marrow", "No fillers"] and a["objections"] == ["Does it work"] and a["cta"] == "Try it" and a["why_it_works"] == "Story first" and a["ad_count"] == 4
    d = client.get("/api/competitors").get_json()
    gal = {c["library_id"]: c for c in det["gallery"]}
    assert gal["a1"]["landing_page"] == {"lp_id": "lp1", "page_type": "Advertorial", "key_theme": "Joints are a nutrient problem"} and "landing_page" not in gal["a4"]
    pat = d["lp_patterns"]
    assert pat["pages"] == 3
    ch = {c["key"]: {r["label"]: r["count"] for r in c["rows"]} for c in pat["charts"]}
    assert ch["page_type"] == {"Advertorial": 2, "Product page": 1}
    assert ch["proof"]["Reviews"] == 3 and ch["proof"]["Doctor quote"] == 1                         # lowercase "reviews" groups with "Reviews"
    assert ch["urgency"] == {"Countdown timer": 2, "Low stock": 1}
    assert ch["selling_points"]["Marrow"] == 2 and ch["selling_points"]["No fillers"] == 3          # exact lowercase match groups "no fillers" / "No fillers"
    assert ch["guarantee"] == {"60 days": 1, "30 days": 1, "90 days": 1}
    assert pat["changed"] == [{"lp_id": "lp1", "brand": "Acme", "page_type": "Advertorial", "change_note": "New hero headline", "final_url": "https://acme.com/join"}]
    v = client.get("/api/competitors?format=video").get_json()["lp_patterns"]                       # video ads: a1,a3,a5,a7,a9 -> lp1, lp2 only
    assert v["pages"] == 2


def test_landing_pages_old_shape_when_no_rows_for_the_brand(client, world):
    world(tables=("competitor_ad_tags", "competitor_landing_pages"), tags=[tag("a1", "UGC")], lps=[LP3])
    det = client.get("/api/competitors/Acme").get_json()
    assert all(set(p) == {"page", "ads"} for p in det["landing_pages"]) and det["landing_pages"]
    assert "lp_patterns" in client.get("/api/competitors").get_json()
