"""Competitors (/api/competitors, /api/competitors/<brand>, app/competitors.py): two synthetic weeks, one week only, a stale brand, the format filter, and the optional competitor_posts table."""
import pytest

W1, W2 = "2026-11-09", "2026-11-16"


def ad(week, lid, brand, days=10, fmt="video", copy=None, url="https://www.acme.com/p1?utm=x", variants=1, carried=None):
    return (week, lid, brand, days, fmt, copy or f"hook {lid}", url, f"{brand}:{copy or lid}", variants, carried)


@pytest.fixture()
def seeded(pg):
    def run(rows, brands=(("Acme", "capsules"), ("Bolt", "joint")), classes=()):
        pg.execute("DELETE FROM competitor_ads"); pg.execute("DELETE FROM competitor_concept_class"); pg.execute("DELETE FROM competitor_brands")
        for b, t in brands:
            pg.execute("INSERT INTO competitor_brands (brand, product_type, search_phrases, name_match, last_ok_at) VALUES (%s,%s,%s,%s, now())", (b, t, [b.lower()], b))
        for (wk, lid, brand, days, fmt, copy, url, ckey, var, carried) in rows:
            pg.execute("INSERT INTO competitor_ads (week_start, library_id, brand, start_date, days_running, variant_count, format, primary_text, hook, landing_url, concept_key, seen_on, carried_from) "
                       "VALUES (%s,%s,%s,%s::date - %s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", (wk, lid, brand, wk, days, days, var, fmt, copy, copy, url, ckey, wk, carried))
        for ck, brand, cluster, pain, new, label, prod in classes:
            pg.execute("INSERT INTO competitor_concept_class (concept_key, brand, mapped_product, cluster, pain_point, is_new_angle, new_angle_label) VALUES (%s,%s,%s,%s,%s,%s,%s)", (ck, brand, prod, cluster, pain, new, label))
        pg.commit()
    yield run
    for t in ("competitor_ads", "competitor_concept_class", "competitor_brands"):
        pg.execute(f"DELETE FROM {t}")
    pg.commit()


TWO = [ad(W1, "a1", "Acme", 200), ad(W1, "a2", "Acme", 5, "image"), ad(W1, "a3", "Acme", 3), ad(W1, "b1", "Bolt", 30), ad(W1, "b2", "Bolt", 2, "image"),
       ad(W2, "a1", "Acme", 207), ad(W2, "a4", "Acme", 1, "video", url="acme.com/new"), ad(W2, "a5", "Acme", 1, "image", url="acme.com/new"), ad(W2, "b1", "Bolt", 37)]
CLS = [("Acme:a4", "Acme", "Joints", "Knee pain", True, "Marrow for joints", "boneflex"), ("Acme:a1", "Acme", "Energy", "Low T", False, None, "vita-bull")]


def by(d):
    return {b["brand"]: b for b in d["brands"]}


def test_two_weeks_summary_and_changes(client, seeded):
    seeded(TWO, classes=CLS)
    d = client.get("/api/competitors").get_json()
    assert d["weeks"] == [W2, W1] and d["week"] == W2 and d["prior_week"] == W1 and d["first_snapshot"] is False and d["notice"] is None
    a, b = by(d)["Acme"], by(d)["Bolt"]
    assert (a["active_ads"], a["change"], a["new_ads"], a["stopped_ads"]) == (3, 0, 2, 2)
    assert (b["active_ads"], b["change"], b["new_ads"], b["stopped_ads"]) == (1, -1, 0, 1)
    assert a["max_days_running"] == 207 and a["top_landing_domain"] == "acme.com" and a["video_share"] == pytest.approx(2 / 3, abs=0.001)
    assert a["is_stale"] is False
    texts = " ".join(c["text"] for c in d["changes"])
    assert "Marrow for joints" in texts and "207 days" in texts and len(d["changes"]) <= 8
    older = client.get(f"/api/competitors?week={W1}").get_json()
    assert older["first_snapshot"] is True and by(older)["Acme"]["change"] is None and older["notice"]


def test_one_week_only_degrades(client, seeded):
    seeded([r for r in TWO if r[0] == W1])
    d = client.get("/api/competitors").get_json()
    assert d["first_snapshot"] is True and "First snapshot" in d["notice"]
    assert all(b["change"] is None and b["new_ads"] is None and b["stopped_ads"] is None for b in d["brands"])
    det = client.get("/api/competitors/Acme").get_json()
    assert det["first_snapshot"] is True and det["new_this_week"] == [] and len(det["longest_running"]) == 3


def test_empty_database_and_bad_args(client, seeded):
    seeded([])
    d = client.get("/api/competitors").get_json()
    assert d["weeks"] == [] and d["week"] is None and d["brands"] == [] and d["notice"]
    seeded(TWO)
    assert client.get("/api/competitors?week=nope").status_code == 400
    assert client.get("/api/competitors?week=2030-01-06").status_code == 404
    assert client.get("/api/competitors?format=gif").status_code == 400
    assert client.get("/api/competitors/Nobody").status_code == 404


def test_stale_brand_flagged_and_carried_rows_make_no_changes(client, seeded, pg):
    rows = [ad(W1, "a1", "Acme", 20), ad(W1, "b1", "Bolt", 20), ad(W2, "a1", "Acme", 27), ad(W2, "b1", "Bolt", 27, carried=W1)]
    seeded(rows)
    pg.execute("UPDATE competitor_brands SET last_error = 'blocked' WHERE brand = 'Bolt'"); pg.commit()
    d = client.get("/api/competitors").get_json()
    b = by(d)["Bolt"]
    assert b["is_stale"] is True and b["new_ads"] == 0 and b["stopped_ads"] == 0 and b["last_error"] == "blocked" and by(d)["Acme"]["is_stale"] is False
    assert any(c["kind"] == "stale" and c["brand"] == "Bolt" for c in d["changes"])


def test_detail_sections(client, seeded, monkeypatch):
    monkeypatch.setattr("app.competitors._chatter_table", lambda conn: False)
    seeded(TWO + [ad(W2, "a6", "Acme", 207, copy="a1")], classes=CLS)       # a6 repeats a1's copy: one concept, longest-running listed once
    det = client.get("/api/competitors/Acme").get_json()
    assert [x["library_id"] for x in det["longest_running"]][0] == "a1" and len({x["library_id"] for x in det["longest_running"]}) == len(det["longest_running"])
    assert all(x["ad_library_url"] == f"https://www.facebook.com/ads/library/?id={x['library_id']}" for x in det["longest_running"])
    assert "a6" not in {x["library_id"] for x in det["longest_running"]}                    # same concept as a1
    assert {x["library_id"] for x in det["new_this_week"]} == {"a4", "a5", "a6"} and det["new_count"] == 3
    angles = {(g["cluster"], g["pain_point"]): g for g in det["angles"]}
    assert angles[("Joints", "Knee pain")]["is_new_angle"] is True and angles[("Joints", "Knee pain")]["new_angle_label"] == "Marrow for joints"
    assert any(g["cluster"] == "Not classified yet" for g in det["angles"])
    assert {"page": "acme.com/new", "ads": 2} in det["landing_pages"] and {"page": "acme.com/p1", "ads": 2} in det["landing_pages"]
    assert det["brand_ad_library_url"].startswith("https://www.facebook.com/ads/library/?active_status=active") and "q=acme" in det["brand_ad_library_url"]
    assert len(det["longest_running"]) <= 8 and len(det["new_this_week"]) <= 8
    assert "chatter" not in det


def test_format_filter_applies_everywhere(client, seeded):
    seeded(TWO, classes=CLS)
    allf = client.get("/api/competitors").get_json()
    v = client.get("/api/competitors?format=video").get_json()
    i = client.get("/api/competitors?format=image").get_json()
    assert v["format"] == "video" and i["format"] == "image" and allf["format"] == "all"
    assert (by(v)["Acme"]["active_ads"], by(v)["Acme"]["new_ads"], by(v)["Acme"]["stopped_ads"]) == (2, 1, 1)      # video: a1,a4 now; a1,a3 before
    assert (by(i)["Acme"]["active_ads"], by(i)["Acme"]["new_ads"], by(i)["Acme"]["stopped_ads"]) == (1, 1, 1)
    assert by(v)["Acme"]["video_share"] == 1.0 and by(i)["Acme"]["video_share"] == 0.0
    assert by(i)["Bolt"]["active_ads"] == 0 and by(v)["Bolt"]["active_ads"] == 1
    dv = client.get("/api/competitors/Acme?format=video").get_json()
    di = client.get("/api/competitors/Acme?format=image").get_json()
    assert {x["format"] for x in dv["longest_running"]} == {"video"} and {x["format"] for x in di["longest_running"]} == {"image"}
    assert {x["library_id"] for x in dv["new_this_week"]} == {"a4"} and {x["library_id"] for x in di["new_this_week"]} == {"a5"}
    assert sum(p["ads"] for p in dv["landing_pages"]) == 2 and sum(p["ads"] for p in di["landing_pages"]) == 1
    assert sum(g["ads"] for g in dv["angles"]) == 2 and sum(g["ads"] for g in di["angles"]) == 1
    assert all("Image" not in c["text"] for c in v["changes"])
    assert client.get("/api/competitors?format=all").get_json()["format"] == "all"


def test_chatter_included_only_when_table_exists(client, seeded, pg, monkeypatch):
    seeded(TWO)
    monkeypatch.setattr("app.competitors._chatter_table", lambda conn: False)
    assert "chatter" not in client.get("/api/competitors").get_json()
    monkeypatch.undo()
    pg.execute("DELETE FROM competitor_posts"); pg.commit()
    for n in range(12):
        pg.execute("INSERT INTO competitor_posts (week_start, source, brand, title, url, subreddit, score, num_comments, posted_at, seen_on) VALUES (%s,'reddit',%s,%s,%s,'r/x',%s,3,now(),%s)",
                   (W2, "Acme" if n % 2 == 0 else None, f"post {n}", f"https://reddit.com/{n}", n, W2))
    pg.execute("INSERT INTO competitor_posts (week_start, source, brand, title, url, subreddit, score, num_comments, posted_at, seen_on) VALUES (%s,'reddit',NULL,'old','https://reddit.com/o','r/x',999,1,now(),%s)", (W1, W1))
    pg.commit()
    try:
        d = client.get("/api/competitors").get_json()
        assert [p["title"] for p in d["chatter"]][:2] == ["post 11", "post 10"] and len(d["chatter"]) == 10 and d["chatter"][0]["brand"] is None
        det = client.get("/api/competitors/Acme").get_json()
        assert all(p["brand"] == "Acme" for p in det["chatter"]) and det["chatter"][0]["title"] == "post 10"
    finally:
        pg.execute("DELETE FROM competitor_posts"); pg.commit()


def test_login_required(anon):
    assert anon.get("/api/competitors").status_code == 401
    assert anon.get("/api/competitors/Acme").status_code == 401
