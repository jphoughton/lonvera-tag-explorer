"""Our own ad learning tags in the Tag Explorer (app/own_tags.py): GET /api/products/<slug>/learning-tags and the `learning` block of GET /api/ads/<id>.
The learning table belongs to another job, so the fixtures create it here (contract columns) and drop it afterwards; with it absent or empty nothing changes. The Upstack (NC) side is a scaled copy of the
fixture's own family rows, so every number is checked against plain SQL over those tables, not against the code under test."""
import datetime as dt

import pytest

from . import fixture_data as F
from .helpers import THROUGH, restore_own_tables

DDL = """CREATE TABLE IF NOT EXISTS ad_learning_tags (family_key text PRIMARY KEY, tag_version text, method text, confidence text, hook_type text, offer_type text, device text, cta text,
         hook_text text, why_it_works text, seen_on text, said_on text, model text, tagged_at timestamptz DEFAULT now())"""
D30 = (THROUGH - dt.timedelta(days=29), THROUGH)
FACTOR = 0.5                      # Upstack new customers = half of Meta's purchases and revenue in this fixture


def fkey(pg, concept):
    return pg.execute("SELECT family_key FROM tracker_rows WHERE concept_id = %s AND deleted_at IS NULL", (concept,)).fetchone()[0]


def tag_row(pg, family_key, hook=None, device=None, offer=None, **kw):
    d = dict(family_key=family_key, hook_type=hook, device=device, offer_type=offer, **kw)
    pg.execute(f"INSERT INTO ad_learning_tags ({','.join(d)}) VALUES ({','.join(['%s'] * len(d))}) ON CONFLICT (family_key) DO UPDATE SET " + ",".join(f"{k}=EXCLUDED.{k}" for k in d if k != "family_key"), tuple(d.values()))


@pytest.fixture()
def world(pg):
    def build(rows=()):
        pg.execute("DROP TABLE IF EXISTS ad_learning_tags")
        if rows is not None:
            pg.execute(DDL)
        for fam, hook, device, offer in rows or ():
            tag_row(pg, fkey(pg, fam), hook, device, offer)
        pg.commit()
    yield build
    restore_own_tables(pg)


@pytest.fixture()
def upstack(pg):
    """An Upstack new customer twin of the fixture: family rows and tracker-row metrics scaled by FACTOR, plus the two app_meta days. The previous contents come back afterwards."""
    for t in ("family_daily_upstack_nc", "ad_metrics_upstack_nc"):
        pg.execute(f"CREATE TEMP TABLE IF NOT EXISTS bk_{t} AS SELECT * FROM {t} WHERE false")
        pg.execute(f"DELETE FROM bk_{t}"); pg.execute(f"INSERT INTO bk_{t} SELECT * FROM {t}"); pg.execute(f"DELETE FROM {t}")
    old = {k: v for k, v in pg.execute("SELECT key, value FROM app_meta WHERE key IN ('upstack_through', 'upstack_first_day')").fetchall()}
    pg.execute("INSERT INTO family_daily_upstack_nc SELECT * FROM family_daily")
    pg.execute("UPDATE family_daily_upstack_nc SET purchases = purchases * %s, revenue = revenue * %s", (FACTOR, FACTOR))
    pg.execute("INSERT INTO ad_metrics_upstack_nc SELECT * FROM ad_metrics")
    for k, v in (("upstack_through", THROUGH.isoformat()), ("upstack_first_day", F.D0.isoformat())):
        pg.execute("INSERT INTO app_meta (key, value) VALUES (%s,%s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (k, v))
    pg.commit()
    yield
    for t in ("family_daily_upstack_nc", "ad_metrics_upstack_nc"):
        pg.execute(f"DELETE FROM {t}"); pg.execute(f"INSERT INTO {t} SELECT * FROM bk_{t}"); pg.execute(f"DROP TABLE bk_{t}")
    for k in ("upstack_through", "upstack_first_day"):
        if k in old:
            pg.execute("UPDATE app_meta SET value = %s WHERE key = %s", (old[k], k))
        else:
            pg.execute("DELETE FROM app_meta WHERE key = %s", (k,))
    pg.commit()


ROWS = [("VB-A1", "Question", "Talking head", "Discount"), ("VB-B1", "Question", "Voiceover", "Bundle"), ("VB-D1", "Story", "Talking head", None)]


def sums(pg, table, fams, days=D30, stage=None):
    r = pg.execute(f"SELECT COALESCE(sum(spend),0), COALESCE(sum(purchases),0), COALESCE(sum(revenue),0) FROM {table} WHERE family_key = ANY(%s) AND day BETWEEN %s AND %s" + (" AND stage = %s" if stage else ""),
                   (fams, *days) + ((stage,) if stage else ())).fetchone()
    return tuple(float(x) for x in r)


def cut(d, key):
    return next(c for c in d["cuts"] if c["key"] == key)


def by_value(c):
    return {r["value"]: r for r in c["rows"]}


# ------------------------------------------------------------------------------------------------ absent / empty

def test_table_absent_nothing_changes(client, world, pg):
    world(rows=None)
    d = client.get("/api/products/vita-bull/learning-tags?window=30d").get_json()
    assert d["available"] is False and "cuts" not in d
    ad = client.get("/api/ads/%d" % pg.execute("SELECT id FROM tracker_rows WHERE concept_id = 'VB-A1'").fetchone()[0]).get_json()
    assert "learning" not in ad
    assert client.get("/api/products/vita-bull/tree?root=cluster&window=30d").status_code == 200


def test_table_empty_is_unavailable(client, world, pg):
    world(rows=[])
    assert client.get("/api/products/vita-bull/learning-tags?window=30d").get_json()["available"] is False
    assert "learning" not in client.get("/api/ads/%d" % pg.execute("SELECT id FROM tracker_rows WHERE concept_id = 'VB-A1'").fetchone()[0]).get_json()


def test_tags_only_on_other_products_hide_the_section(client, world):
    world(rows=ROWS)
    assert client.get("/api/products/beef-liver/learning-tags?window=30d").get_json()["available"] is False


# ------------------------------------------------------------------------------------------------ the numbers

def test_meta_numbers_unique_families_and_note(client, world, pg):
    world(rows=ROWS)
    d = client.get("/api/products/vita-bull/learning-tags?window=30d").get_json()
    assert d["available"] is True and d["has_nc"] is False and [c["key"] for c in d["cuts"]] == ["hook_type", "device", "offer_type"]
    fams = pg.execute("SELECT count(DISTINCT family_key) FROM tracker_rows WHERE product_id = (SELECT id FROM products WHERE slug = 'vita-bull') AND deleted_at IS NULL").fetchone()[0]
    hook = cut(d, "hook_type")
    assert hook["families"] == fams and hook["tagged"] == 3 and hook["note"] == f"3 of {fams} ad families tagged"
    rows = by_value(hook)
    fa, fb, fd = fkey(pg, "VB-A1"), fkey(pg, "VB-B1"), fkey(pg, "VB-D1")
    q_sp, q_pu, q_rev = (sum(x) for x in zip(sums(pg, "family_daily", [fa]), sums(pg, "family_daily", [fb])))
    r = rows["Question"]
    assert r["unique_ads"] == 2 and r["nc"] is None                                    # family A has two tracker rows but counts once
    assert r["spend"] == pytest.approx(q_sp, abs=0.01) and r["meta"]["purchases"] == pytest.approx(q_pu, abs=0.01) and r["meta"]["revenue"] == pytest.approx(q_rev, abs=0.01)
    assert r["meta"]["roas"] == pytest.approx(q_rev / q_sp, abs=1e-3) and r["meta"]["cpa"] == pytest.approx(q_sp / q_pu, abs=0.01)
    assert r["meta"]["aov"] == pytest.approx(q_rev / q_pu, abs=0.01)
    s_sp, _, _ = sums(pg, "family_daily", [fd])
    assert rows["Story"]["unique_ads"] == 1 and rows["Story"]["spend"] == pytest.approx(s_sp, abs=0.01)
    assert r["spend_share"] == pytest.approx(q_sp / d["total"]["meta"]["spend"], abs=1e-4)
    assert [x["value"] for x in hook["rows"]] == sorted(rows, key=lambda v: (-rows[v]["spend"], v))        # sorted by spend
    offer = cut(d, "offer_type")
    assert offer["tagged"] == 2 and set(by_value(offer)) == {"Discount", "Bundle"}                  # the untagged offer of family D is not a row
    dev = by_value(cut(d, "device"))
    assert dev["Talking head"]["unique_ads"] == 2 and dev["Voiceover"]["unique_ads"] == 1


def test_traffic_ratios_come_from_the_existing_helpers(client, world, pg):
    world(rows=ROWS)
    d = client.get("/api/products/vita-bull/learning-tags?window=30d").get_json()
    fa, fb = fkey(pg, "VB-A1"), fkey(pg, "VB-B1")
    imp, clk, ctr_clk, sp_tr = pg.execute("SELECT COALESCE(sum(impressions),0), COALESCE(sum(link_clicks),0), COALESCE(sum(clicks_tr),0), COALESCE(sum(spend_tr),0) FROM family_daily "
                                         "WHERE family_key = ANY(%s) AND day BETWEEN %s AND %s", ([fa, fb], *D30)).fetchone()
    m = by_value(cut(d, "hook_type"))["Question"]["meta"]
    assert m["link_clicks"] == pytest.approx(float(clk), abs=0.01)
    if float(imp):
        assert m["ctr"] == pytest.approx(float(ctr_clk) / float(imp), abs=1e-4) and m["cpm"] == pytest.approx(float(sp_tr) * 1000 / float(imp), abs=1e-2)
    if float(clk):
        assert m["cplc"] == pytest.approx(float(sp_tr) / float(clk), abs=1e-3)


def test_nc_side_and_headline(client, world, upstack, pg):
    world(rows=ROWS)
    d = client.get("/api/products/vita-bull/learning-tags?window=30d").get_json()
    assert d["has_nc"] is True
    fams = [fkey(pg, "VB-A1"), fkey(pg, "VB-B1")]
    msp, mpu, mrev = (sum(x) for x in zip(sums(pg, "family_daily", fams[:1]), sums(pg, "family_daily", fams[1:])))
    nsp, npu, nrev = (sum(x) for x in zip(sums(pg, "family_daily_upstack_nc", fams[:1]), sums(pg, "family_daily_upstack_nc", fams[1:])))
    r = by_value(cut(d, "hook_type"))["Question"]
    assert r["nc"]["revenue"] == pytest.approx(nrev, abs=0.01) and r["nc"]["roas"] == pytest.approx(nrev / nsp, abs=1e-3) and r["nc"]["cpa"] == pytest.approx(nsp / npu, abs=0.01)
    assert r["nc"]["roas"] == pytest.approx(FACTOR * r["meta"]["roas"], abs=2e-3)
    assert r["nc"]["aov"] == pytest.approx(nrev / npu, abs=0.01)
    assert r["meta"]["roas"] == pytest.approx(mrev / msp, abs=1e-3)                          # the Meta side is untouched by the Upstack twin
    assert d["total"]["nc"]["roas"] == pytest.approx(FACTOR * d["total"]["meta"]["roas"], abs=2e-3)


def test_the_source_switch_does_not_change_the_answer(client, world, upstack):
    world(rows=ROWS)
    a = client.get("/api/products/vita-bull/learning-tags?window=30d").get_json()
    b = client.get("/api/products/vita-bull/learning-tags?window=30d&source=upstack&basis=nc").get_json()
    assert a["cuts"] == b["cuts"]


def test_stage_and_window_and_scope_filters(client, world, upstack, pg):
    world(rows=ROWS)
    fa, fb = fkey(pg, "VB-A1"), fkey(pg, "VB-B1")
    d = client.get("/api/products/vita-bull/learning-tags?window=30d&stage=testing").get_json()
    sp = sum(sums(pg, "family_daily", [f], stage="testing")[0] for f in (fa, fb))
    row = by_value(cut(d, "hook_type")).get("Question")
    assert (row["spend"] if row else 0) == pytest.approx(sp, abs=0.01)
    d7 = client.get("/api/products/vita-bull/learning-tags?window=7d").get_json()
    sp7 = sum(sums(pg, "family_daily", [f], days=(THROUGH - dt.timedelta(days=6), THROUGH))[0] for f in (fa, fb))
    assert by_value(cut(d7, "hook_type"))["Question"]["spend"] == pytest.approx(sp7, abs=0.01)
    assert client.get("/api/products/vita-bull/learning-tags?window=30d&compare=prev").status_code == 200       # a comparison on the page does not break it
    alpha = client.get("/api/products/vita-bull/learning-tags?window=30d&cluster=" + F.TAXONOMY[0]["cluster"].replace(" ", "%20")).get_json()
    assert set(by_value(cut(alpha, "hook_type"))) == {"Question"}                    # family D sits in the Beta Cluster
    beta = client.get("/api/products/vita-bull/learning-tags?window=30d&cluster=Beta%20Cluster").get_json()
    assert set(by_value(cut(beta, "hook_type"))) == {"Story"}
    assert client.get("/api/products/vita-bull/learning-tags?window=bogus").status_code == 400
    assert client.get("/api/products/nope/learning-tags").status_code == 404


def test_blank_and_padded_values_are_not_rows(client, world, pg):
    world(rows=[("VB-A1", "  Question ", "", "Discount")])
    d = client.get("/api/products/vita-bull/learning-tags?window=30d").get_json()
    assert set(by_value(cut(d, "hook_type"))) == {"Question"} and "device" not in {c["key"] for c in d["cuts"]}


# ------------------------------------------------------------------------------------------------ the ad page

def test_ad_page_learning_block(client, world, pg):
    world(rows=[])
    fam = fkey(pg, "VB-A1")
    tag_row(pg, fam, "Question", "Talking head", "Discount", cta="Shop now", hook_text="Your liver is a vitamin warehouse", why_it_works="Names the pain first.", seen_on="A kitchen", said_on="Try it for 30 days",
            method="vision_transcript", confidence="high", tag_version="own-v1")
    pg.commit()
    rid = pg.execute("SELECT id FROM tracker_rows WHERE concept_id = 'VB-A1'").fetchone()[0]
    l = client.get(f"/api/ads/{rid}").get_json()["learning"]
    assert [(c["key"], c["value"]) for c in l["chips"]] == [("hook_type", "Question"), ("device", "Talking head"), ("offer_type", "Discount"), ("cta", "Shop now")]
    assert l["hook_quote"] == "Your liver is a vitamin warehouse" and l["why_it_works"] == "Names the pain first." and l["seen_on"] == "A kitchen" and l["said_on"] == "Try it for 30 days"
    assert l["version"] == "own-v1" and l["method"] == "vision_transcript" and l["confidence"] == "high"
    other = pg.execute("SELECT id FROM tracker_rows WHERE concept_id = 'VB-B1'").fetchone()[0]
    assert "learning" not in client.get(f"/api/ads/{other}").get_json()                  # an untagged family gets no block


def test_ad_page_only_present_parts_and_transcript_sources(client, world, pg):
    world(rows=[])
    fam = fkey(pg, "VB-B1")
    tag_row(pg, fam, hook="Story")
    pg.execute("DELETE FROM family_content WHERE family_key = %s", (fam,))
    pg.execute("INSERT INTO family_content (family_key, transcript) VALUES (%s, 'from family content')", (fam,))
    pg.commit()
    rid = pg.execute("SELECT id FROM tracker_rows WHERE concept_id = 'VB-B1'").fetchone()[0]
    try:
        l = client.get(f"/api/ads/{rid}").get_json()["learning"]
        assert [c["key"] for c in l["chips"]] == ["hook_type"] and "hook_quote" not in l and "why_it_works" not in l and "seen_on" not in l
        assert l["transcript"] == "from family content"
        pg.execute("INSERT INTO ad_transcript (family_key, transcript, source) VALUES (%s, 'from ad_transcript', 'whisper')", (fam,))
        pg.commit()
        assert client.get(f"/api/ads/{rid}").get_json()["learning"]["transcript"] == "from ad_transcript"          # the new table wins when it exists
    finally:
        pg.rollback(); pg.execute("DELETE FROM ad_transcript WHERE family_key = %s", (fam,)); pg.execute("DELETE FROM family_content WHERE family_key = %s", (fam,)); pg.commit()


def test_empty_tag_row_gives_no_block(client, world, pg):
    world(rows=[])
    tag_row(pg, fkey(pg, "VB-B1"))
    pg.commit()
    rid = pg.execute("SELECT id FROM tracker_rows WHERE concept_id = 'VB-B1'").fetchone()[0]
    assert "learning" not in client.get(f"/api/ads/{rid}").get_json()
