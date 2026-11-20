"""Launched ads on a ticket (migration 281): variations[].ad_id (the live Meta ad of each variation) and launched_ad_ids (the ticket's own list). Parsing, validation, row output with the app's own
Meta data, history, and the rule that vendors and partners never see or change any of it."""
import datetime as dt
import json

import pytest

from app import priorities_assign as pa

from .test_api_keys import bearer, fresh_throttle, mint, key, post_assign      # noqa: F401  (fixtures)
from .test_api_priorities_assign import MIN, MONDAY, clean, link, one, snaps    # noqa: F401
from .test_partner_keys import create, pkey, pk, ticket                          # noqa: F401  (fixtures)

AD = "120212345678901234"
VARS = [{"label": "Original", "url": "https://drive.google.com/file/d/AAA/view", "concept_id": "Lonvera-BeefLiver-Video-1.1"}, {"label": "Hook 2"}, {"label": "Hook 3"}]


def mk(client, **kw):
    r = client.post("/api/priorities/tickets", json={**MIN, "variations": VARS, **kw})
    assert r.status_code == 201, r.get_data(as_text=True)
    return r.get_json()


def patch(client, tid, body):
    return client.patch(f"/api/priorities/assignments/{tid}", json=body)


def stored(pg, tid):
    pg.rollback()
    return pg.execute("SELECT variations, launched_ad_ids FROM creative_priorities_assignments WHERE id = %s", (tid,)).fetchone()


@pytest.fixture()
def meta(pg):
    pg.execute("INSERT INTO meta_ads (ad_id, ad_name, status) VALUES (%s, 'Lonvera-BeefLiver-Video-1.1 - Copy 2', 'ACTIVE') ON CONFLICT (ad_id) DO NOTHING", (AD,))
    for i, (sp, cv) in enumerate([(100.0, 150.0), (50.0, 30.0)]):
        pg.execute("INSERT INTO ad_daily (ad_id, day, meta_family_key, meta_family, spend, purchases, conv_value, source) VALUES (%s,%s,'zz','ZZ',%s,2,%s,'meta_api') ON CONFLICT DO NOTHING", (AD, dt.date(2026, 11, 1 + i), sp, cv))
    pg.commit()
    yield
    pg.execute("DELETE FROM ad_daily WHERE ad_id = %s", (AD,))
    pg.execute("DELETE FROM meta_ads WHERE ad_id = %s", (AD,))
    pg.commit()


# ------------------------------------------------------------------------------------------------------------ parsing
@pytest.mark.parametrize("raw,want", [
    ("12345", "12345"), (" 120212345678901234 ", "120212345678901234"), (120212345678901234, "120212345678901234"),
    ("https://adsmanager.facebook.com/adsmanager/manage/ads?act=111&selected_ad_ids=120212345678901234", "120212345678901234"),
    ("https://adsmanager.facebook.com/adsmanager/manage/ads/edit?act=111&business_id=2&ad_id=120212345678901234", "120212345678901234"),
    ("https://business.facebook.com/adsmanager/manage/ads?selected_ad_ids=555555555,666666666&act=1", "555555555"),
    ("https://adsmanager.facebook.com/adsmanager/manage/ads#?selected_ad_ids=777777777", "777777777"),
])
def test_parse_ad_id_accepts_ids_and_ads_manager_links(raw, want):
    assert pa.parse_ad_id(raw) == want


@pytest.mark.parametrize("raw", ["", "abc", "1234", "1" * 26, "12 345 678", "https://www.facebook.com/lonvera/posts/123456789012", "https://adsmanager.facebook.com/adsmanager/manage/ads?act=111", True, None, ["1"]])
def test_parse_ad_id_rejects_everything_else(raw):
    with pytest.raises(Exception) as e:
        pa.parse_ad_id(raw)
    assert getattr(e.value, "status", 400) == 400


def test_a_post_link_says_to_paste_the_ad_id():
    with pytest.raises(Exception) as e:
        pa.parse_ad_id("https://www.facebook.com/lonvera/posts/pfbid0abc")
    assert "no ad ID" in e.value.message and "Ads Manager link" in e.value.message


# ------------------------------------------------------------------------------------------------------------ variations
def test_a_variation_ad_id_saves_and_keeps_every_other_key(client, snaps, pg, meta):
    t = mk(client)
    assert t["ads_total"] == 3 and t["ads_linked"] == 0 and t["launched_ads"] == [] and t["launched_ad_ids"] == []
    new = [{**v} for v in t["variations"]]
    new[0]["ad_id"] = f"https://adsmanager.facebook.com/adsmanager/manage/ads?selected_ad_ids={AD}"
    r = patch(client, t["id"], {"variations": new})
    assert r.status_code == 200, r.get_data(as_text=True)
    j = r.get_json()
    assert j["variations"][0] == {"label": "Original", "url": "https://drive.google.com/file/d/AAA/view", "concept_id": "Lonvera-BeefLiver-Video-1.1", "ad_id": AD}
    assert "ad_id" not in j["variations"][1] and [v["label"] for v in j["variations"]] == ["Original", "Hook 2", "Hook 3"]
    assert j["ads_total"] == 3 and j["ads_linked"] == 1
    assert j["launched_ads"] == [{"ad_id": AD, "source": "variation", "label": "Original", "variation_index": 0,
                                  "meta": {"name": "Lonvera-BeefLiver-Video-1.1 - Copy 2", "status": "ACTIVE", "spend": 150.0, "purchases": 4.0, "roas": 1.2}}]
    assert stored(pg, t["id"])[0][0]["ad_id"] == AD


def test_an_unknown_ad_has_null_meta_and_no_error(client, snaps):
    t = mk(client)
    j = patch(client, t["id"], {"variations": [{**t["variations"][0], "ad_id": "99999999999999"}, *t["variations"][1:]]}).get_json()
    assert j["launched_ads"][0]["meta"] is None and j["ads_linked"] == 1


def test_bad_variation_ad_ids_are_a_400_naming_the_variation(client, snaps, pg):
    t = mk(client)
    for bad in ("abc", "123", "1" * 30, "https://www.facebook.com/lonvera/posts/123456789012"):
        r = patch(client, t["id"], {"variations": [t["variations"][0], {**t["variations"][1], "ad_id": bad}]})
        assert r.status_code == 400 and r.get_json()["error"].startswith("variation 2:"), bad
    assert "ad_id" not in json.dumps(stored(pg, t["id"])[0])


def test_clearing_a_variation_ad_id_removes_the_key(client, snaps, pg):
    t = mk(client)
    patch(client, t["id"], {"variations": [{**t["variations"][0], "ad_id": AD}, *t["variations"][1:]]})
    j = patch(client, t["id"], {"variations": [{**t["variations"][0], "ad_id": ""}, *t["variations"][1:]]}).get_json()
    assert all("ad_id" not in v for v in j["variations"]) and j["ads_linked"] == 0


def test_a_ticket_can_be_created_with_variation_ad_ids(client, snaps):
    t = mk(client, variations=[{"label": "Original", "ad_id": AD}])
    assert t["variations"] == [{"label": "Original", "url": "", "concept_id": "", "ad_id": AD}] and t["ads_linked"] == 1


# ------------------------------------------------------------------------------------------------------------ ticket level
def test_launched_ad_ids_dedupe_keep_order_and_parse_links(client, snaps, pg):
    t = mk(client, variations=[])
    r = patch(client, t["id"], {"launched_ad_ids": ["22222", AD, "22222", f"https://x.facebook.com/adsmanager/manage/ads?ad_id={AD}", 33333]})
    assert r.status_code == 200, r.get_data(as_text=True)
    j = r.get_json()
    assert j["launched_ad_ids"] == ["22222", AD, "33333"] and j["ads_total"] == 0 and j["ads_linked"] == 3
    assert [a["source"] for a in j["launched_ads"]] == ["ticket"] * 3
    assert stored(pg, t["id"])[1] == ["22222", AD, "33333"]


def test_launched_ad_ids_limits_and_bad_values(client, snaps):
    t = mk(client)
    assert patch(client, t["id"], {"launched_ad_ids": [str(10000 + i) for i in range(200)]}).status_code == 200
    r = patch(client, t["id"], {"launched_ad_ids": [str(10000 + i) for i in range(201)]})
    assert r.status_code == 400 and "at most 200" in r.get_json()["error"]
    for bad in ("12345", {"a": 1}, ["abc"], [None], [["12345"]], 7):
        assert patch(client, t["id"], {"launched_ad_ids": bad}).status_code == 400, bad
    assert patch(client, t["id"], {"launched_ad_ids": []}).get_json()["launched_ad_ids"] == []


def test_a_variation_ad_also_in_the_ticket_list_counts_once(client, snaps):
    t = mk(client)
    patch(client, t["id"], {"variations": [{**t["variations"][0], "ad_id": AD}, *t["variations"][1:]]})
    j = patch(client, t["id"], {"launched_ad_ids": [AD, "44444"]}).get_json()
    assert j["ads_linked"] == 2 and len(j["launched_ads"]) == 3


def test_the_list_endpoint_carries_the_same_keys(client, snaps):
    t = mk(client)
    patch(client, t["id"], {"launched_ad_ids": ["55555"]})
    row = next(r for r in client.get("/api/priorities/assignments?include_closed=1").get_json()["rows"] if r["id"] == t["id"])
    assert row["launched_ad_ids"] == ["55555"] and row["ads_total"] == 3 and row["ads_linked"] == 1 and row["launched_ads"][0]["ad_id"] == "55555"


# ------------------------------------------------------------------------------------------------------------ history
def test_linking_and_unlinking_write_history_rows(client, snaps, pg):
    t = mk(client)
    patch(client, t["id"], {"variations": [{**t["variations"][0], "ad_id": AD}, *t["variations"][1:]]})
    patch(client, t["id"], {"launched_ad_ids": ["44444"]})
    patch(client, t["id"], {"launched_ad_ids": []})
    patch(client, t["id"], {"concept_name": "Renamed"})               # no ad change: no ad event
    pg.rollback()
    ev = pg.execute("SELECT kind, detail FROM creative_priorities_assignment_events WHERE assignment_id = %s AND kind IN ('ad_linked', 'ad_unlinked') ORDER BY id", (t["id"],)).fetchall()
    assert [tuple(e) for e in ev] == [("ad_linked", AD), ("ad_linked", "44444"), ("ad_unlinked", "44444")]
    hist = client.get("/api/priorities/assignments?include_closed=1").get_json()["rows"]
    assert [e["kind"] for e in next(r for r in hist if r["id"] == t["id"])["history"] if e["kind"].startswith("ad_")] == ["ad_linked", "ad_linked", "ad_unlinked"]


# ------------------------------------------------------------------------------------------------------------ internal only
def test_internal_only_lists_the_launched_ad_keys_and_the_vendor_patch_cannot_set_them():
    for k in ("launched_ad_ids", "launched_ads", "ads_total", "ads_linked"):
        assert k in pa.INTERNAL_ONLY and k in pa.VENDOR_HIDDEN
    assert "launched_ad_ids" in pa.PATCH_FIELDS and "launched_ad_ids" not in pa.VENDOR_PATCH_FIELDS


def test_the_vendor_link_page_never_sees_or_sets_launched_ads(client, anon, snaps, pg):
    d = link(client, "Studio Flow")
    t = mk(client)
    patch(client, t["id"], {"variations": [{**t["variations"][0], "ad_id": AD}, *t["variations"][1:]], "launched_ad_ids": ["44444"]})
    rows = anon.get("/api/priorities/vendor/" + d["token"]).get_json()["rows"]
    row = next(r for r in rows if r["id"] == t["id"])
    assert len(row["variations"]) == 3 and not any("ad_id" in v for v in row["variations"])
    assert not {"launched_ad_ids", "launched_ads", "ads_total", "ads_linked"} & set(row)
    for body in ({"launched_ad_ids": ["55555"]}, {"variations": [{"label": "x", "ad_id": "55555"}]}):
        r = anon.patch(f"/api/priorities/vendor/{d['token']}/{t['id']}", json=body)
        assert r.status_code == 400
    assert stored(pg, t["id"])[1] == ["44444"]


def test_a_partner_cannot_set_ad_ids_and_a_rewrite_keeps_the_stored_ones(anon, pk, pg, client, snaps):
    t = create(anon, pk, variations=[{"label": "Original"}, {"label": "Hook 2"}]).get_json()
    tid = t["id"]
    client.patch(f"/api/priorities/assignments/{tid}", json={"variations": [{"label": "Original", "ad_id": AD}, {"label": "Hook 2"}], "launched_ad_ids": ["44444"]})
    url = f"/api/priorities/partner/tickets/{tid}"
    r = anon.patch(url, json={"variations": [{"label": "Original", "ad_id": "55555"}]}, headers=bearer(pk))
    assert r.status_code == 400 and "ad_id" in r.get_json()["error"]
    assert anon.patch(url, json={"launched_ad_ids": ["55555"]}, headers=bearer(pk)).status_code == 400
    assert create(anon, pk, variations=[{"label": "Original", "ad_id": AD}]).status_code == 400
    r = anon.patch(url, json={"variations": [{"label": "Original", "url": "https://drive.google.com/file/d/AAA/view"}, {"label": "Hook 2"}, {"label": "Hook 3"}]}, headers=bearer(pk))
    assert r.status_code == 200 and not any("ad_id" in v for v in r.get_json()["variations"])
    v, ids = stored(pg, tid)
    assert v[0]["ad_id"] == AD and "ad_id" not in v[1] and ids == ["44444"]                    # the rewrite kept the internal link
    r = anon.post(f"/api/priorities/partner/tickets/{tid}/creative", json={"variations": [{"label": "Original", "url": "https://drive.google.com/file/d/BBB/view"}]}, headers=bearer(pk))
    assert r.status_code == 200 and stored(pg, tid)[0][0]["ad_id"] == AD                       # a creative push merges by label and keeps it too


def test_partner_reads_never_carry_launched_ads(anon, pk, pg, client, snaps):
    t = create(anon, pk, variations=[{"label": "Original"}]).get_json()
    client.patch(f"/api/priorities/assignments/{t['id']}", json={"variations": [{"label": "Original", "ad_id": AD}], "launched_ad_ids": ["44444"]})
    listed = anon.get("/api/priorities/partner/tickets", headers=bearer(pk)).get_json()
    assert AD not in json.dumps(listed) and "44444" not in json.dumps(listed)
    one_ = anon.get(f"/api/priorities/partner/tickets/{t['id']}", headers=bearer(pk))
    assert AD not in one_.get_data(as_text=True)
    allt = anon.get("/api/priorities/partner/all-tickets", headers=bearer(pk))
    assert allt.status_code == 200
    body = allt.get_data(as_text=True)
    assert AD not in body and "launched_ad" not in body and "ads_linked" not in body
    det = anon.get(f"/api/priorities/partner/all-tickets/{t['id']}", headers=bearer(pk))
    assert AD not in det.get_data(as_text=True) and "launched_ad" not in det.get_data(as_text=True)
