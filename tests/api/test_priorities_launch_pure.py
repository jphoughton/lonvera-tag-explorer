"""app/priorities_launch.py (pure) and migration 203."""
import copy as _copy
import datetime as dt

import psycopg
import pytest

from app import priorities_launch as pl

ASSIGN = {"id": 12, "product": "beef-liver", "vendor": "Madre", "for_week": dt.date(2026, 11, 9), "pain_point": "Joint Pain & Mobility Loss",
          "icp": "BF2 Can't Get Down on the Floor", "section_at_assign": "make_more"}
SETTINGS = {"account_id": "act_1", "campaign_id": "c1", "template_adset_id": "t1", "daily_budget_cents": 10000, "bid_amount_cents": 6800, "landing_url": "https://x.test/p",
            "url_tags": None, "cta_image": "LEARN_MORE", "cta_video": "SHOP_NOW", "pixel_id": "px", "description": None}
PAGE = {"page_id": "p1", "name": "Lonvera", "instagram_user_id": "ig1", "name_token": "lonvera"}
FIELDS = {"style": "Photo-Hero Benefit Ad", "fidelity": "Lo-Fi", "proof": "No Proof / Claim Only", "type": "Iteration"}


def _plan(files=None, ids=None, **kw):
    files = files or [{"id": "f1", "name": "1.png", "kind": "image"}, {"id": "f2", "name": "2.mp4", "kind": "video"}]
    ids = ids or ["Lonvera-BeefLiver-Static-A12-v1", "Lonvera-BeefLiver-Video-A12-v2"]
    return pl.build_plan(kw.get("assignment", ASSIGN), SETTINGS, PAGE, files, ids, FIELDS, {"primary_texts": ["a"], "headlines": ["h"], "source": "draft"}, kw.get("adset"))


# ---- ad name: mirrors the tracker Name formula (only spaces removed)
def test_ad_name_matches_the_real_tracker_row():
    got = pl.build_ad_name("Lonvera-BoneFlex-Static-7.0.8", "image", "Joint Pain & Mobility Loss", "BF2 Can't Get Down on the Floor", "Boneflex", "Photo-Hero Benefit Ad", "Lo-Fi",
                           "Madre", "New-Concept", "No Proof / Claim Only", "lonvera")
    assert got == "Lonvera-BoneFlex-Static-7.0.8_IMG_JointPain&MobilityLoss_BF2Can'tGetDownontheFloor_Boneflex_Photo-HeroBenefitAd_Lo-Fi_Madre_New-Concept_NoProof/ClaimOnly_lonvera"


def test_ad_name_keeps_parentheses_slashes_and_at_signs():
    got = pl.build_ad_name("Trybe-X-beefliver-1", "video", "Nutrient Deficiency (Iron / B12 / Vitamin A)", "BL3 Thinning and Drained", "BeefLiver", "UGC Talking Head", "Lo-Fi",
                           "TrybeOrion", "New-Concept", "Named Testimonial Quote", "@livinfreewithlee")
    assert got == "Trybe-X-beefliver-1_VID_NutrientDeficiency(Iron/B12/VitaminA)_BL3ThinningandDrained_BeefLiver_UGCTalkingHead_Lo-Fi_TrybeOrion_New-Concept_NamedTestimonialQuote_@livinfreewithlee"


def test_tracker_row_uses_the_same_inputs_as_the_name():
    args = ("Victoria-BeefLiver-Static-A12", "image", "Hair Loss", "BL3 Thinning and Drained", "BeefLiver", "Static Quote", "Hi-Fi", "Victoria", "Iteration", "Review Count", "victoria")
    v = pl.tracker_row_values(*args, asset_url="u", brief_url="b")
    assert v["Concept ID"] == args[0] and v["Type"] == "IMG" and v["New Concept / Iteration"] == "Iteration" and v["Handle"] == "victoria" and v["Status"].startswith("5")
    row = pl.tracker_row_tsv(v).split("\t")
    assert len(row) == len(pl.TRACKER_HEADERS) and row[2] == args[0] and row[0] == "b" and row[1] == "u"
    assert pl.tracker_row_tsv({"Concept ID": "x\ty\nz"}, ["Concept ID", "Nope"]) == "x y z\t"


# ---- ids
def test_mint_single_file():
    assert pl.mint_concept_ids("Lonvera", "beef-liver", ["image"], 12, set()) == ["Lonvera-BeefLiver-Static-A12"]
    assert pl.mint_concept_ids("Victoria", "vita-bull", ["video"], 3) == ["Victoria-VitalBull-Video-A3"]
    assert pl.mint_concept_ids("Internal", "boneflex", ["image"], 7) == ["Internal-BoneFlex-Static-A7"]


def test_mint_folder_numbers_in_given_order():
    assert pl.mint_concept_ids("Lonvera", "boneflex", ["image", "video", "image"], 5) == ["Lonvera-BoneFlex-Static-A5-v1", "Lonvera-BoneFlex-Video-A5-v2", "Lonvera-BoneFlex-Static-A5-v3"]


def test_mint_collision_is_case_insensitive_and_advances_the_letter():
    taken = {"lonvera-beefliver-static-a12"}
    assert pl.mint_concept_ids("Lonvera", "beef-liver", ["image"], 12, taken) == ["Lonvera-BeefLiver-Static-A12b"]
    taken |= {"LONVERA-BEEFLIVER-STATIC-A12B"}
    assert pl.mint_concept_ids("Lonvera", "beef-liver", ["image"], 12, taken) == ["Lonvera-BeefLiver-Static-A12c"]
    out = pl.mint_concept_ids("Lonvera", "beef-liver", ["image", "image"], 12, {"Lonvera-BeefLiver-Static-A12-v2"})
    assert out == ["Lonvera-BeefLiver-Static-A12b-v1", "Lonvera-BeefLiver-Static-A12b-v2"] and len({x.lower() for x in out}) == 2


def test_vendor_prefix_fallback():
    assert pl.vendor_prefix("Madre", {"Madre": "Lonvera"}) == "Lonvera"
    assert pl.vendor_prefix("Trybe DC-Shop!", {}) == "TrybeDCShop"
    assert pl.vendor_prefix("Madre", {"Madre": "  "}) == "Madre"


def test_default_type_and_words():
    assert pl.default_type("iterate") == "Iteration" and pl.default_type("make_more") == "Iteration"
    assert pl.default_type("revive") == "New-Concept" and pl.default_type("") == "New-Concept" and pl.default_type(None) == "New-Concept"
    assert (pl.media_kind_word("image"), pl.media_kind_word("video"), pl.media_token("image"), pl.media_token("video")) == ("Static", "Video", "IMG", "VID")


# ---- ordering and copy
def test_folder_numbering_follows_natural_name_order():
    files = [{"id": "a", "name": "10.png"}, {"id": "b", "name": "2.png"}, {"id": "c", "name": "B.png"}, {"id": "d", "name": "a.png"}, {"id": "e", "name": "1.png"}]
    assert [f["name"] for f in pl.order_files(files)] == ["1.png", "2.png", "10.png", "a.png", "B.png"]
    ordered = pl.order_files([{"id": "x", "name": "10.png", "kind": "image"}, {"id": "y", "name": "2.png", "kind": "image"}])
    ids = pl.mint_concept_ids("Lonvera", "beef-liver", [f["kind"] for f in ordered], 4)
    assert dict(zip([f["name"] for f in ordered], ids)) == {"2.png": "Lonvera-BeefLiver-Static-A4-v1", "10.png": "Lonvera-BeefLiver-Static-A4-v2"}
    stable = pl.order_files([{"id": "1", "name": "a.png"}, {"id": "2", "name": "A.png"}])
    assert [f["id"] for f in stable] == ["1", "2"]


def test_copy_precedence_and_trimming():
    d = {"primary_texts": ["", " d1 ", "d2", "d3", "d4"], "headlines": []}
    b = {"primary_texts": ["b1"], "headlines": ["bh"]}
    df = {"primary_texts": ["p"], "headlines": ["dh"]}
    r = pl.resolve_copy(d, b, df)
    assert r == {"primary_texts": ["d1", "d2", "d3"], "headlines": ["bh"], "source": "draft"}
    assert pl.resolve_copy({}, b, df)["source"] == "best_ad"
    assert pl.resolve_copy(None, None, df) == {"primary_texts": ["p"], "headlines": ["dh"], "source": "default"}
    assert pl.resolve_copy(None, None, None) == {"primary_texts": [], "headlines": [], "source": "default"}


# ---- plan + hash
def test_plan_shape_and_paused():
    p = _plan()
    assert p["status"] == "PAUSED" and p["destination"]["new_adset"] is True and p["destination"]["adset_name"] == "BeefLiver_Madre_2026-11-09_Production"
    assert [a["cta"] for a in p["ads"]] == ["LEARN_MORE", "SHOP_NOW"]
    assert p["ads"][0]["ad_name"].startswith("Lonvera-BeefLiver-Static-A12-v1_IMG_JointPain&MobilityLoss_")
    assert p["ads"][0]["ad_name"].endswith("_Madre_Iteration_NoProof/ClaimOnly_lonvera")
    assert _plan(adset={"meta_adset_id": "999"})["destination"]["adset_id"] == "999"


def test_plan_hash_is_stable_under_key_order_and_changes_with_any_ad_field():
    p = _plan()
    rev = {k: p[k] for k in reversed(list(p))}
    rev["ads"] = [{k: a[k] for k in reversed(list(a))} for a in p["ads"]]
    assert pl.plan_hash(p) == pl.plan_hash(rev) and len(pl.plan_hash(p)) == 64
    for field in p["ads"][0]:
        q = _copy.deepcopy(p)
        q["ads"][0][field] = "changed" if not isinstance(q["ads"][0][field], list) else ["changed"]
        assert pl.plan_hash(q) != pl.plan_hash(p), field
    q = _copy.deepcopy(p)
    q["destination"]["daily_budget_cents"] = 1
    assert pl.plan_hash(q) != pl.plan_hash(p)


# ---- fields and env
def test_validate_fields():
    allowed = {"style": ["A", "B"], "fidelity": ["Lo-Fi"], "type": ["Iteration", "New-Concept"], "proof": ["P"], "page": ["p1"]}
    clean, err = pl.validate_fields({"style": " A ", "fidelity": "Hi-Fi", "type": "", "junk": "x"}, allowed)
    assert clean == {"style": "A"} and len(err) == 1 and err[0].startswith("fidelity")
    clean, err = pl.validate_fields({"style": "A"}, allowed, require_all=True)
    assert clean == {"style": "A"} and len(err) == 4


def test_env_readers():
    assert pl.launch_enabled({}) is False and pl.launch_enabled({"META_LAUNCH_ENABLED": "true"}) is False and pl.launch_enabled({"META_LAUNCH_ENABLED": " 1"}) is False
    assert pl.launch_enabled({"META_LAUNCH_ENABLED": "1"}) is True
    assert pl.launch_users({}) == set() and pl.launch_users({"META_LAUNCH_USERS": ""}) == set() and pl.launch_users({"META_LAUNCH_USERS": " , "}) == set()
    assert pl.launch_users({"META_LAUNCH_USERS": " JoshHoughton, lazer ,"}) == {"joshhoughton", "lazer"}
    assert pl.dry_run({}) is True and pl.dry_run({"META_LAUNCH_DRY_RUN": "0"}) is False and pl.dry_run({"META_LAUNCH_DRY_RUN": "false"}) is True


# ---- migration 203
def test_migration_203_tables_constraints_and_seed(pg):
    names = {"settings", "pages", "vendor_prefixes", "drafts", "jobs", "ads", "adsets", "audit"}
    have = {r[0] for r in pg.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_name LIKE 'creative_launch_%'").fetchall()}
    assert have == {f"creative_launch_{n}" for n in names}
    assert dict(pg.execute("SELECT vendor, prefix FROM creative_launch_vendor_prefixes").fetchall()) == {"Madre": "Lonvera", "Victoria": "Victoria", "10xROAS": "10x", "Internal": "Internal"}
    assert pg.execute("SELECT count(*) FROM creative_launch_settings").fetchone()[0] == 0
    aid = pg.execute("INSERT INTO creative_priorities_assignments (concept_key, text_key, product, vendor, for_week) VALUES ('text:p203','text:p203','beef-liver','Madre','2026-11-09') RETURNING id").fetchone()[0]
    try:
        pg.commit()

        def bad(sql, params=()):
            with pytest.raises(psycopg.errors.Error):
                pg.execute(sql, params)
            pg.rollback()
        ins = "INSERT INTO creative_launch_ads (assignment_id, drive_file_id, file_name, kind, concept_id, ad_name) VALUES (%s,%s,'n','image',%s,'x')"
        pg.execute(ins, (aid, "f1", "C-1"))
        pg.commit()
        bad(ins, (aid, "f1", "C-2"))                      # same file twice for one assignment
        bad(ins, (aid, "f2", "C-1"))                      # same concept id twice
        bad("INSERT INTO creative_launch_ads (assignment_id, drive_file_id, file_name, kind, concept_id, ad_name) VALUES (%s,'f3','n','gif','C-3','x')", (aid,))
        bad("INSERT INTO creative_launch_ads (assignment_id, drive_file_id, file_name, kind, concept_id, ad_name, state) VALUES (%s,'f4','n','image','C-4','x','live')", (aid,))
        job = "INSERT INTO creative_launch_jobs (assignment_id, kind, state) VALUES (%s,'launch',%s)"
        pg.execute(job, (aid, "queued"))
        pg.commit()
        bad(job, (aid, "running"))                        # a second open job
        bad("INSERT INTO creative_launch_jobs (assignment_id, kind) VALUES (%s,'delete')", (aid,))
        pg.execute("UPDATE creative_launch_jobs SET state='done' WHERE assignment_id=%s", (aid,))
        pg.execute(job, (aid, "queued"))                  # closed jobs do not block a new one
        pg.commit()
    finally:
        pg.rollback()
        for t in ("creative_launch_ads", "creative_launch_jobs", "creative_launch_drafts"):
            pg.execute(f"DELETE FROM {t} WHERE assignment_id=%s", (aid,))
        pg.execute("DELETE FROM creative_priorities_assignments WHERE id=%s", (aid,))
        pg.commit()
