"""v1.9 "Active ads only": active=1 on every metrics endpoint vs an independent oracle (fixture_data.build_active), plus partition checks
(active + inactive == all), the creative / unique_ads semantics, ad lists, compare x stage combinations, window_info.note, and a guard that
no active=1 query still reads an all-ads table."""
import copy
import datetime as dt
import itertools
import re

import pytest

from app import queries as q

from . import fixture_data as F
from .helpers import STAGE_PARAMS, THROUGH, WINDOWS, check_m, close, get_ok, group_rows, qs, rows_by_product

ACTIVE_ADS = {a for a, v in F.ADS.items() if v[3] == "ACTIVE"}
WIN_STAGE = [pytest.param(w, wo, s, so, id=f"{w or 'default'}|{s or 'all'}") for (w, wo), (s, so) in itertools.product(WINDOWS, STAGE_PARAMS)]
WIN_ONLY = [pytest.param(w, wo, id=w or "default") for w, wo in WINDOWS]


def fa(fx):
    return fx.active_view


def act_rows(fx, slug):
    """tracker rows of a product whose creative has at least one ACTIVE Meta ad"""
    return [r for r in rows_by_product(fx, slug) if r["family_key"] in fa(fx).n_active]


def vb_fams(fx):
    return {r["family_key"] for r in act_rows(fx, "vita-bull") if not r["family_key"].startswith("id:")}


# ------------------------------------------------------------------------------------------------------------------ parameter + note
def test_active_param_parsing(client):
    base = get_ok(client, "/api/products/vita-bull/rollup?window=7d")
    assert base["window_info"]["active"] is False and "Active ads only" not in (base["window_info"]["note"] or "")
    for off in ("0", "false", "no", "off", ""):
        d = get_ok(client, f"/api/products/vita-bull/rollup?window=7d&active={off}")
        assert d == base, off
    for on in ("1", "true", "yes", "on", "TRUE"):
        d = get_ok(client, f"/api/products/vita-bull/rollup?window=7d&active={on}")
        assert d["window_info"]["active"] is True, on
    for bad in ("2", "maybe", "1;drop", "-1"):
        r = client.get(f"/api/products/vita-bull/rollup?active={bad}")
        assert r.status_code == 400 and "active must be 1 or 0" in r.get_json()["error"], bad


def test_active_note_is_explicit(client):
    d = get_ok(client, "/api/products/vita-bull/rollup?window=7d&active=1&compare=prev")
    note = d["window_info"]["note"]
    assert "Active ads only" in note and "at least one ACTIVE Meta ad" in note and "whole period" in note and "totals and shares are within this set" in note
    assert "Meta status as last recorded" in note                       # the fixture records no status refresh time
    assert d["compare_info"]["active"] is False and "Active ads only" not in (d["compare_info"]["note"] or "")
    launch = get_ok(client, "/api/products/vita-bull/rollup?active=1")["window_info"]["note"]
    assert "since-launch figures are the active ads' lifetime" in launch


def test_active_note_shows_status_time(pg, client):
    pg.execute("INSERT INTO app_meta (key, value) VALUES ('ad_status_as_of', '2026-11-03T08:00:00+00:00')")
    pg.commit()
    try:
        note = client.get("/api/products/vita-bull/rollup?window=7d&active=1").get_json()["window_info"]["note"]
        assert "status as of 2026-11-03" in note and "refresh time is not stored" not in note
        assert client.get("/api/status").get_json()["active"]["status_as_of"] == "2026-11-03T08:00:00+00:00"
    finally:
        pg.execute("DELETE FROM app_meta WHERE key = 'ad_status_as_of'")
        pg.commit()


def test_status_active_counts(client, fx):
    a = get_ok(client, "/api/status")["active"]
    d1 = THROUGH - dt.timedelta(days=1)
    live = {r["ad_id"] for r in fx.ad_daily if r["ad_id"] in ACTIVE_ADS and r["spend"] > 0 and r["day"] >= d1}
    assert a["available"] is True and a["ads"] == len(ACTIVE_ADS) and a["ads_live"] == len(live) and a["creatives"] == len(fa(fx).n_active)
    assert a["source"] == "Meta effective status" and a["status_as_of"] is None


# ------------------------------------------------------------------------------------------------------------------ products / rollup / families
@pytest.mark.parametrize("w,wo,s,so", WIN_STAGE)
def test_products_active(client, fx, w, wo, s, so):
    data = get_ok(client, "/api/products?" + qs("active=1", w, s))
    by = {p["slug"]: p for p in data}
    pid2slug = {v: k for k, v in fx.product_ids.items()}
    unmatched_spend = {}
    for u in fa(fx).unmatched.values():
        unmatched_spend[pid2slug[u["product_id"]]] = unmatched_spend.get(pid2slug[u["product_id"]], 0) + u["spend_all"]
    expect = [slug for slug in ("vita-bull", "beef-liver", "boneflex", "vita-belle", "vital-flow", "unassigned")
              if act_rows(fx, slug) or unmatched_spend.get(slug, 0) > 0]
    assert [p["slug"] for p in data] == expect
    for slug in ("vita-bull", "beef-liver"):
        rows = act_rows(fx, slug)
        assert by[slug]["rows"] == len(rows)
        check_m(by[slug]["m"], F.aggregate(fa(fx), rows, wo, so), f"{slug} {w} {s} active")
    if "boneflex" in by:
        assert by["boneflex"]["rows"] == 0 and by["boneflex"]["m"]["unique_ads"] == 0        # its only tracker creative has no Meta ad at all
    # envelope total: shares are within the filtered set
    env = get_ok(client, "/api/products?" + qs("active=1", "envelope=1", w, s))
    assert env["window_info"]["active"] is True and [p["slug"] for p in env["rows"]] == expect
    assert env["total"]["m"]["unique_ads"] == sum(p["m"]["unique_ads"] for p in env["rows"])
    assert env["total"]["m"]["spend"] == pytest.approx(sum(p["m"]["spend"] for p in env["rows"]), abs=0.02)
    if env["total"]["m"]["spend"]:
        assert sum(p["m"]["spend_share"] for p in env["rows"]) == pytest.approx(1.0, abs=1e-4)


@pytest.mark.parametrize("w,wo,s,so", WIN_STAGE)
def test_rollup_active(client, fx, w, wo, s, so):
    d = get_ok(client, "/api/products/vita-bull/rollup?" + qs("level=cluster", "active=1", w, s))
    rows = act_rows(fx, "vita-bull")
    groups = group_rows(rows, range(1))
    by = {r["label"]: r for r in d["rows"]}
    assert [r["label"] for r in d["rows"]][:2] == ["Alpha Cluster", "Beta Cluster"]          # the taxonomy skeleton always shows
    for label, row in by.items():
        check_m(row["m"], F.aggregate(fa(fx), groups.get((label,), []), wo, so), f"cluster {label}")
    check_m(d["total"]["m"], F.aggregate(fa(fx), rows, wo, so), "total")
    assert d["total"]["m"]["unique_ads"] == len(vb_fams(fx))
    assert {k[0] for k in groups} <= set(by)
    sp = d["total"]["m"]["spend"]
    if sp:
        assert sum(r["m"]["spend_share"] or 0 for r in d["rows"]) == pytest.approx(1.0, abs=1e-4)      # shares recomputed within the filtered set
    # unmatched summary follows the filter
    um = {k: v for k, v in fa(fx).unmatched.items() if v["product_id"] == fx.product_ids["vita-bull"]}
    assert d["unmatched"]["families_all"] == sum(1 for v in um.values() if max(v["spend_all"], v["spend_launch"]) > 0)
    assert d["unmatched"]["spend_30d"] == pytest.approx(float(sum(v["spend_30d"] for v in um.values())), abs=0.011)


@pytest.mark.parametrize("w,wo,s,so", WIN_STAGE)
def test_families_active(client, fx, w, wo, s, so):
    d = get_ok(client, "/api/products/vita-bull/families?" + qs("active=1", w, s))
    rows = act_rows(fx, "vita-bull")
    fams = {}
    for r in rows:
        fams.setdefault(r["family_key"], []).append(r)
    assert {r["family_key"] for r in d["rows"]} == set(fams)                  # only creatives with an ACTIVE ad: vb-fam-f / blank families drop out
    assert "vb-fam-f" not in {r["family_key"] for r in d["rows"]} and not any(r["family_key"].startswith("id:") for r in d["rows"])
    for r in d["rows"]:
        check_m(r["m"], F.aggregate(fa(fx), fams[r["family_key"]], wo, so), r["family_key"])
        assert r["m"]["unique_ads"] == 1
    check_m(d["total"]["m"], F.aggregate(fa(fx), rows, wo, so), "families total")
    assert d["total"]["m"]["unique_ads"] == len(d["rows"])
    assert [r["m"]["spend"] for r in d["rows"]] == sorted((r["m"]["spend"] for r in d["rows"]), reverse=True)


def test_families_ads_count_and_live_are_active_ads(client, fx):
    d1 = THROUGH - dt.timedelta(days=1)
    live = lambda fam: {r["ad_id"] for r in fa(fx).ad_daily if F.MAPPING[r["meta_family_key"]][0] == fam and r["spend"] > 0 and r["day"] >= d1}   # noqa: E731
    for w, rng in (("window=launch", None), ("window=30d", (THROUGH - dt.timedelta(days=29), THROUGH))):
        d = get_ok(client, "/api/products/vita-bull/families?" + qs("active=1", w))
        for r in d["rows"]:
            fam = r["family_key"]
            assert r["ads_live"] == len(live(fam)), (fam, w)
            if rng is None:
                assert r["ads_count"] == fa(fx).n_active[fam], (fam, w)            # every ACTIVE ad of the creative (c1 has no lifetime row but is in meta_ads)
            else:
                want = {x["ad_id"] for x in fa(fx).ad_daily if F.MAPPING[x["meta_family_key"]][0] == fam and x["spend"] > 0 and rng[0] <= x["day"] <= rng[1]}
                assert r["ads_count"] == len(want), (fam, w)
    # all-ads mode counts the paused ads too
    allm = {r["family_key"]: r for r in get_ok(client, "/api/products/vita-bull/families")["rows"]}
    assert allm["vb-fam-a"]["ads_count"] == 3 and next(r for r in get_ok(client, "/api/products/vita-bull/families?active=1")["rows"] if r["family_key"] == "vb-fam-a")["ads_count"] == 2


EXTRAS = ["window=7d", "window=30d&stage=testing,scaling", "window=14d&compare=prev", "window=launch&stage=scaling,unclassified",
          "from=2026-10-10&to=2026-10-20&compare=30d&stage=testing,unclassified", "window=mtd&stage=unclassified&compare=7d"]


def strip_share(m):
    m = copy.deepcopy(m)
    m.pop("spend_share", None)
    if "cmp" in m:
        m["cmp"].pop("spend_share", None)
        m["delta"].pop("spend_share_pp", None)
    return m


@pytest.mark.parametrize("extra", EXTRAS)
def test_tree_active_matches_rollup_and_families(client, extra):
    t = get_ok(client, "/api/products/vita-bull/tree?" + qs("root=cluster", "depth=3", "active=1", extra))
    assert t["window_info"]["active"] is True
    roll = {r["label"]: r for r in get_ok(client, "/api/products/vita-bull/rollup?" + qs("level=cluster", "active=1", extra))["rows"]}
    for r in t["rows"]:
        if r["level"] == 0:
            assert strip_share(r["m"]) == strip_share(roll[r["label"]]["m"]), r["key"]
    fam = {r["family_key"]: r for r in get_ok(client, "/api/products/vita-bull/families?" + qs("active=1", extra))["rows"]}
    tree_fams = {r["family_key"]: r for r in t["rows"] if r["level"] == 3 and r["counted_here"]}      # a creative under two tags shows twice, counted once
    assert set(tree_fams) == set(fam) and {r["family_key"] for r in t["rows"] if r["level"] == 3} == set(fam)
    for k, r in tree_fams.items():
        assert strip_share(r["m"]) == strip_share(fam[k]["m"]), k
        assert r["ads_count"] == fam[k]["ads_count"] and r["ads_live"] == fam[k]["ads_live"]
    # the representative Meta ad of a creative is an ACTIVE one (a3, the paused relaunch, can never stand for vb-fam-a)
    links = get_ok(client, "/api/ads/1000?active=1")["links"]
    assert links["ad_id"] in ("a1", "a2")


# ------------------------------------------------------------------------------------------------------------------ ads
@pytest.mark.parametrize("w,wo,s,so", WIN_STAGE)
def test_ads_active_rows_match_oracle(client, fx, w, wo, s, so):
    d = get_ok(client, "/api/products/vita-bull/ads?" + qs("active=1", w, s))
    ads, rem, rem_u = F.ad_oracle(fa(fx), vb_fams(fx), wo, so, unmatched_pid=fx.product_ids["vita-bull"])
    got = {(r["ad_id"], r["family_key"]): r for r in d["rows"] if r["ad_id"]}
    assert set(got) == set(ads), (w, s)
    assert all(a in ACTIVE_ADS for a, _ in got), "ad lists contain only ACTIVE ads"
    assert all(r["status"] == "ACTIVE" for r in d["rows"] if r["ad_id"])
    for key, v in ads.items():
        r = got[key]
        assert close(r["spend"], v["spend"]) and close(r["purchases"], v["purchases"]) and close(r["revenue"], v["revenue"]), (key, w, s)
        assert r["impressions"] == v["imp"] and r["link_clicks"] == v["clk"], (key, w, s)
    for on_tracker, want in ((True, rem), (False, rem_u)):
        remainder = [r for r in d["rows"] if r["ad_id"] is None and r["on_tracker"] is on_tracker]
        if any(abs(want[k]) >= F.Decimal("0.005") for k in ("spend", "purchases", "revenue")):
            assert len(remainder) == 1 and close(remainder[0]["spend"], want["spend"])
        else:
            assert not remainder, (w, s, remainder)
    assert d["total"]["spend"] == pytest.approx(float(sum(v["spend"] for v in ads.values()) + rem["spend"] + rem_u["spend"]), abs=0.02)
    assert d["total"]["ads"] == len(ads)


@pytest.mark.parametrize("w,wo", WIN_ONLY)
def test_active_plus_inactive_equals_all(client, fx, w, wo):
    """Additive metrics partition by ad status: Σ(rows of ACTIVE ads) == the active=1 total, and all == active + (everything not ACTIVE)."""
    a = get_ok(client, "/api/products/vita-bull/ads?" + qs(w))
    b = get_ok(client, "/api/products/vita-bull/ads?" + qs("active=1", w))
    for k in ("spend", "revenue", "impressions", "link_clicks"):
        act_sum = sum((r[k] or 0) for r in a["rows"] if r["ad_id"] and r["status"] == "ACTIVE")
        rest = sum((r[k] or 0) for r in a["rows"] if not (r["ad_id"] and r["status"] == "ACTIVE"))
        assert act_sum == pytest.approx(b["total"][k] or 0, abs=0.02), (k, w)
        assert act_sum + rest == pytest.approx(a["total"][k] or 0, abs=0.02), (k, w)
        assert (b["total"][k] or 0) <= (a["total"][k] or 0) + 0.02
    # families too: all - active == the family's non-ACTIVE ad rows (+ its remainder)
    fam_all = {r["family_key"]: r for r in get_ok(client, "/api/products/vita-bull/families?" + qs(w))["rows"]}
    fam_act = {r["family_key"]: r for r in get_ok(client, "/api/products/vita-bull/families?" + qs("active=1", w))["rows"]}
    assert set(fam_act) < set(fam_all)
    for fk, r in fam_all.items():
        mine = get_ok(client, f"/api/products/vita-bull/ads?family_key={fk}&" + qs(w))
        non_active = sum(x["spend"] for x in mine["rows"] if not (x["ad_id"] and x["status"] == "ACTIVE"))
        a_spend = fam_act[fk]["m"]["spend"] if fk in fam_act else 0
        assert r["m"]["spend"] - a_spend == pytest.approx(non_active, abs=0.02), (fk, w)
    # per product: the active figure never exceeds the all-ads figure
    pa = {p["slug"]: p for p in get_ok(client, "/api/products?" + qs(w))}
    pb = {p["slug"]: p for p in get_ok(client, "/api/products?" + qs("active=1", w))}
    for slug, p in pb.items():
        assert p["m"]["spend"] <= pa[slug]["m"]["spend"] + 0.02 and p["m"]["unique_ads"] <= pa[slug]["m"]["unique_ads"]


@pytest.mark.parametrize("extra", EXTRAS)
def test_ads_active_with_compare_and_stage(client, fx, extra):
    a = get_ok(client, "/api/products/vita-bull/ads?" + qs("active=1", extra))
    assert a["window_info"]["active"] is True
    assert all(r["status"] == "ACTIVE" for r in a["rows"] if r["ad_id"])
    fam = get_ok(client, "/api/products/vita-bull/families?" + qs("active=1", extra))
    tracker_side = [r for r in a["rows"] if r["on_tracker"]]
    assert sum(r["spend"] for r in tracker_side) == pytest.approx(fam["total"]["m"]["spend"], abs=0.03)
    if "compare" in extra or "cfrom" in extra:
        assert a["compare_info"] is not None
        assert sum(r["cmp"]["spend"] for r in tracker_side) == pytest.approx(fam["total"]["m"]["cmp"]["spend"], abs=0.03)


# ------------------------------------------------------------------------------------------------------------------ campaigns / stage-daily / unmatched / trend / spark
@pytest.mark.parametrize("w,wo", [c for c in WIN_ONLY if c.values[1] != "launch"])
def test_campaigns_active_add_up_to_ads(client, fx, w, wo):
    for s in ("", "stage=testing", "stage=scaling,unclassified"):
        c = get_ok(client, "/api/products/vita-bull/campaigns?" + qs("active=1", w, s))
        ads = get_ok(client, "/api/products/vita-bull/ads?" + qs("active=1", w, s))
        assert c["window_info"]["active"] is True
        assert c["total"]["spend"] == pytest.approx(ads["total"]["spend"], abs=0.03), (w, s)
        per_campaign = {}
        for r in ads["rows"]:
            per_campaign[r["campaign_id"] or ""] = per_campaign.get(r["campaign_id"] or "", 0) + r["spend"]
        for row in c["rows"]:
            assert row["spend"] == pytest.approx(per_campaign.get(row["campaign_id"], 0), abs=0.03), (row["campaign_id"], w, s)
        if not s:
            assert sum(r["spend"] for r in c["by_stage"]) == pytest.approx(c["total"]["spend"], abs=0.03)
        # never more than the all-ads view
        allc = get_ok(client, "/api/products/vita-bull/campaigns?" + qs(w, s))
        assert c["total"]["spend"] <= allc["total"]["spend"] + 0.02 and c["total"]["ads"] <= allc["total"]["ads"]


def test_stage_daily_active_matches_oracle(client, fx):
    pid = fx.product_ids["vita-bull"]
    fams = vb_fams(fx)
    want = {}
    for r in fa(fx).ad_daily:
        tfam = F.MAPPING[r["meta_family_key"]][0]
        if (tfam in fams) or (tfam is None and fa(fx).unmatched[r["meta_family_key"]]["product_id"] == pid):
            key = (r["day"], F.stage_of_row(fa(fx), r["campaign_id"]))
            want[key] = want.get(key, F.Z) + r["spend"]
    d = get_ok(client, "/api/products/vita-bull/stage-daily?active=1&days=90")
    got = {(dt.date.fromisoformat(x["day"]), st): x[st] for x in d for st in F.STAGES}
    first_day = min(day for day, _ in got)
    want = {k: v for k, v in want.items() if k[0] >= first_day}
    assert first_day == THROUGH - dt.timedelta(days=89) and len(d) == 90
    for (day, st), v in want.items():
        assert close(got[(day, st)], v), (day, st)
    assert sum(got.values()) == pytest.approx(float(sum(want.values())), abs=0.05)
    allv = get_ok(client, "/api/products/vita-bull/stage-daily?days=90")
    assert sum(sum(x[st] for st in F.STAGES) for x in allv) > sum(got.values())           # the paused ads' days are gone
    # stage list + window params both honoured
    t = get_ok(client, "/api/products/vita-bull/stage-daily?active=1&window=14d&stage=testing")
    assert all(x["scaling"] == 0 and x["unclassified"] == 0 for x in t) and len(t) == 14


@pytest.mark.parametrize("w,wo", WIN_ONLY)
def test_unmatched_active(client, fx, w, wo):
    """active=1: only families with an ACTIVE ad; date windows sum the active ads' daily rows, since launch is the active ads' lifetime."""
    pid = fx.product_ids["vita-bull"]
    d = get_ok(client, "/api/products/vita-bull/unmatched?" + qs("active=1", w))
    want = {}
    for k, um in fa(fx).unmatched.items():
        if um["product_id"] != pid:
            continue
        rows = [r for r in fa(fx).ad_daily if r["meta_family_key"] == k]
        if wo == "launch":
            want[k] = um["spend_launch"]
        else:
            want[k] = sum((r["spend"] for r in rows if wo[0] <= r["day"] <= wo[1]), F.Z)
    got = {r["meta_family_key"]: r for r in d["rows"]}
    assert set(got) == {k for k, v in want.items() if v != 0}
    for k, v in want.items():
        if v:
            assert close(got[k]["spend"], v), k
    assert d["total"]["families"] == len(got) and d["total"]["spend"] == pytest.approx(sum(r["spend"] for r in d["rows"]), abs=0.011)
    assert "bull-lifetime-paused" not in got and "u6" not in got                 # the paused lifetime-only family is not an active one
    if wo == "launch":
        assert got["bull-lifetime-only"]["spend"] == pytest.approx(123.45, abs=0.006)


@pytest.mark.parametrize("grain", ["day", "week", "month"])
@pytest.mark.parametrize("extra", ["window=30d", "window=14d&compare=prev", "window=launch", "window=30d&stage=testing,scaling"])
def test_trend_active(client, fx, grain, extra):
    fam = get_ok(client, "/api/products/vita-bull/families?" + qs("active=1", extra))
    t = get_ok(client, "/api/products/vita-bull/trend?" + qs("active=1", f"grain={grain}", extra))
    assert t["window_info"]["active"] is True
    if "launch" not in extra:
        assert sum(p["spend"] for p in t["points"]) == pytest.approx(fam["total"]["m"]["spend"], abs=0.03)
        assert sum(p["revenue"] for p in t["points"]) == pytest.approx(fam["total"]["m"]["revenue"], abs=0.03)
    allt = get_ok(client, "/api/products/vita-bull/trend?" + qs(f"grain={grain}", extra))
    assert sum(p["spend"] for p in t["points"]) < sum(p["spend"] for p in allt["points"])
    k = get_ok(client, "/api/products/vita-bull/trend?" + qs("active=1", f"grain={grain}", "split=children", extra))
    kid_spend = sum(p["spend"] for c in k["children"] for p in c["points"])
    assert kid_spend == pytest.approx(sum(p["spend"] for p in k["points"]), abs=0.03)
    assert {c["label"] for c in k["children"]} <= {"Alpha Cluster", "Beta Cluster", "(untagged)"}
    camp = get_ok(client, "/api/products/vita-bull/trend?" + qs("active=1", f"grain={grain}", "campaign_id=C100", extra))
    ads = get_ok(client, "/api/products/vita-bull/ads?" + qs("active=1", "campaign_id=C100", extra.replace("window=launch", "window=30d")))
    if "launch" not in extra and "compare" not in extra and "stage" not in extra:
        assert sum(p["spend"] for p in camp["points"]) == pytest.approx(ads["total"]["spend"], abs=0.03)


def test_sparklines_active(client, fx):
    d = get_ok(client, "/api/products/vita-bull/families?" + qs("active=1", "window=7d", "spark=spend", "spark_days=7"))
    assert d["rows"] and len(d["total"]["spark"]) == 7
    for r in d["rows"]:
        assert sum(r["spark"]) == pytest.approx(r["m"]["spend"], abs=0.03), r["family_key"]
    assert sum(d["total"]["spark"]) == pytest.approx(d["total"]["m"]["spend"], abs=0.03)
    ads = get_ok(client, "/api/products/vita-bull/ads?" + qs("active=1", "window=7d", "spark=spend", "spark_days=7"))
    for r in ads["rows"]:
        if r["ad_id"]:
            assert sum(r["spark"]) == pytest.approx(r["spend"], abs=0.03), r["ad_id"]
    camp = get_ok(client, "/api/products/vita-bull/campaigns?" + qs("active=1", "window=7d", "spark=spend", "spark_days=7"))
    assert sum(camp["total"]["spark"]) == pytest.approx(camp["total"]["spend"], abs=0.03)
    prod = get_ok(client, "/api/products?" + qs("active=1", "window=7d", "envelope=1", "spark=spend", "spark_days=7"))
    for p in prod["rows"]:
        assert sum(p["spark"]) == pytest.approx(p["m"]["spend"], abs=0.03), p["slug"]
    roll = get_ok(client, "/api/products/vita-bull/rollup?" + qs("active=1", "window=7d", "spark=spend", "spark_days=7"))
    assert sum(roll["total"]["spark"]) == pytest.approx(roll["total"]["m"]["spend"], abs=0.03)
    tree = get_ok(client, "/api/products/vita-bull/tree?" + qs("active=1", "depth=3", "window=7d", "spark=spend", "spark_days=7"))
    for r in tree["rows"]:
        assert sum(r["spark"]) == pytest.approx(r["m"]["spend"], abs=0.03), r["key"]


# ------------------------------------------------------------------------------------------------------------------ ad detail
def test_ad_detail_active(client, fx):
    row_a = next(r for r in fx.tracker if r["family_key"] == "vb-fam-a")
    d = get_ok(client, f"/api/ads/{row_a['id']}?active=1&window=30d")
    assert d["window_info"]["active"] is True and d["in_active_set"] is True and d["creative"]["active_ads"] == 2
    assert {a["ad_id"] for a in d["meta_ads"]} == {"a1", "a2"} and all(a["status"] == "ACTIVE" for a in d["meta_ads"])      # a3 is PAUSED
    agg = F.aggregate(fa(fx), [row_a], (THROUGH - dt.timedelta(days=29), THROUGH), "all")
    assert d["metrics"]["family"]["window"]["spend"] == pytest.approx(float(agg["spend"]), abs=0.006)
    assert d["creative"]["ads_count"] <= 2 and d["ads_count"] == d["creative"]["ads_count"]
    assert {c["campaign_id"] for c in d["campaigns"]} <= {"C100", "C200"}
    lt = fa(fx).ad_metrics[row_a["id"]]
    assert d["metrics"]["family"]["launch"]["spend"] == pytest.approx(float(lt["spend_launch"]), abs=0.006)
    assert d["metrics"]["family"]["d7"]["spend"] == pytest.approx(float(lt["spend_7d"]), abs=0.006)
    # all-ads mode is unchanged: three ads and the larger lifetime
    full = get_ok(client, f"/api/ads/{row_a['id']}?window=30d")
    assert {a["ad_id"] for a in full["meta_ads"]} == {"a1", "a2", "a3"} and full["in_active_set"] is True and full["creative"]["active_ads"] == 2
    assert full["metrics"]["family"]["launch"]["spend"] > d["metrics"]["family"]["launch"]["spend"]
    daily_a = sum(x["spend"] for x in d["daily"])
    assert daily_a == pytest.approx(float(sum(r["spend"] for r in fa(fx).ad_daily if F.MAPPING[r["meta_family_key"]][0] == "vb-fam-a")), abs=0.03)
    # a creative with no ACTIVE ad: still opens, with nothing counted
    paused = next(r for r in fx.tracker if r["family_key"] == "vb-fam-f")
    z = get_ok(client, f"/api/ads/{paused['id']}?active=1&window=30d")
    assert z["in_active_set"] is False and z["creative"]["active_ads"] == 0 and z["meta_ads"] == [] and z["campaigns"] == []
    assert z["metrics"]["family"]["window"]["spend"] == 0 and z["metrics"]["family"]["launch"]["spend"] == 0 and z["metrics"]["family"]["live"] is False
    assert sum(x["spend"] for x in z["daily"]) == 0 and z["ads_count"] == 0 and z["ads_live"] == 0
    assert get_ok(client, f"/api/ads/{paused['id']}?window=30d")["in_active_set"] is False                    # the flag does not depend on the filter
    blank = next(r for r in fx.tracker if r["family_key"].startswith("id:"))
    b = get_ok(client, f"/api/ads/{blank['id']}?active=1")
    assert b["in_active_set"] is False and b["meta_ads"] == []


def test_ad_detail_active_compare(client, fx):
    row_a = next(r for r in fx.tracker if r["family_key"] == "vb-fam-a")
    d = get_ok(client, f"/api/ads/{row_a['id']}?active=1&window=7d&compare=prev")
    b_win = (THROUGH - dt.timedelta(days=13), THROUGH - dt.timedelta(days=7))
    agg_b = F.aggregate(fa(fx), [row_a], b_win, "all")
    assert d["metrics"]["family"]["compare"]["spend"] == pytest.approx(float(agg_b["spend"]), abs=0.006)


# ------------------------------------------------------------------------------------------------------------------ guard: no active query reads an all-ads table
ALL_ADS_FAMILY_DAILY = re.compile(r"\bfamily_daily\b")
ALL_ADS_AD_DAILY = re.compile(r"\bad_daily\b(?! d_a JOIN meta_ads m_a ON m_a\.ad_id = d_a\.ad_id AND m_a\.status = 'ACTIVE')")
ENDPOINTS = [
    "/api/products?active=1&spark=spend", "/api/products?active=1&envelope=1&window=7d&compare=prev",
    "/api/products/vita-bull/rollup?active=1&level=cluster&spark=spend", "/api/products/vita-bull/rollup?active=1&level=pain&cluster=Alpha Cluster&icp=ICP One&window=30d",
    "/api/products/vita-bull/families?active=1&spark=roas&window=14d&compare=prev", "/api/products/vita-bull/tree?active=1&depth=3&spark=spend&window=launch&stage=scaling",
    "/api/products/vita-bull/ads?active=1&spark=spend&window=30d", "/api/products/vita-bull/ads?active=1&window=launch",
    "/api/products/vita-bull/ads?active=1&window=launch&stage=testing", "/api/products/vita-bull/ads?active=1&family_key=vb-fam-a&campaign_id=C100&window=7d",
    "/api/products/vita-bull/campaigns?active=1&spark=spend&window=30d&compare=prev", "/api/products/vita-bull/stage-daily?active=1&days=30",
    "/api/products/vita-bull/unmatched?active=1&window=30d", "/api/products/vita-bull/trend?active=1&grain=week&split=children",
    "/api/products/vita-bull/trend?active=1&campaign_id=C100&split=stage", "/api/ads/1000?active=1&window=30d&compare=prev", "/api/ads/1000?active=1",
]


@pytest.mark.parametrize("url", ENDPOINTS)
def test_active_queries_never_read_all_ads_tables(client, monkeypatch, url):
    seen = []
    real_rows, real_row = q.rows_of, q.row_of
    monkeypatch.setattr(q, "rows_of", lambda conn, sql, params=None: (seen.append(sql), real_rows(conn, sql, params))[1])
    monkeypatch.setattr(q, "row_of", lambda conn, sql, params=None: (seen.append(sql), real_row(conn, sql, params))[1])
    r = client.get(url)
    assert r.status_code == 200, (url, r.get_data(as_text=True)[:200])
    assert seen
    allowed = ("min(day) AS d FROM family_daily WHERE impressions > 0",)            # load_meta: where impression data starts, an all-ads fact about the data
    for sql in seen:
        if any(a in sql for a in allowed) or "FROM information_schema" in sql:
            continue
        sql = re.sub(r"--[^\n]*", "", sql)                                         # comments may name a table
        assert not ALL_ADS_FAMILY_DAILY.search(sql), f"{url} reads family_daily: {sql[:300]}"
        assert not ALL_ADS_AD_DAILY.search(sql), f"{url} reads every ad_daily row: {sql[:300]}"
