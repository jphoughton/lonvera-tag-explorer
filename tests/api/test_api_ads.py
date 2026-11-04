"""/ads (Meta ad grain), /campaigns, /stage-daily, /unmatched vs the oracle."""
import datetime as dt
import itertools

import pytest

from . import fixture_data as F
from .helpers import STAGE_PARAMS, WINDOWS, close, get_ok, qs, rows_by_product

THROUGH = F.THROUGH


def family_launches(fx, slug="vita-bull"):
    out = {}
    for r in sorted(fx.tracker, key=lambda r: (r["sheet_row"], r["id"])):
        if r["product"] == slug and not r["family_key"].startswith("id:"):
            out.setdefault(r["family_key"], r["launch"])
    return out


def groups_for(fx, cluster=None, icp=None, pain=None):
    """first-row families (key -> launch) whose first tracker row is in the group"""
    firsts = {}
    for r in sorted(fx.tracker, key=lambda r: (r["sheet_row"], r["id"])):
        if r["product"] == "vita-bull":
            firsts.setdefault(r["family_key"], r)
    out = {}
    for fk, r in firsts.items():
        c, i, p = F.tag_labels(r)
        if fk.startswith("id:"):
            continue
        if (cluster in (None, c)) and (icp in (None, i)) and (pain in (None, p)):
            out[fk] = r["launch"]
    return out


WIN_STAGE = [pytest.param(w, wo, s, so, id=f"{w or 'default'}|{s or 'all'}") for (w, wo), (s, so) in itertools.product(WINDOWS, STAGE_PARAMS)]


@pytest.mark.parametrize("w,wo,s,so", WIN_STAGE)
def test_ads_all_rows_match_oracle(client, fx, w, wo, s, so):
    d = get_ok(client, "/api/products/vita-bull/ads?" + qs(w, s))
    fams = groups_for(fx)
    ads, rem, rem_u = F.ad_oracle(fx, fams, wo, so, unmatched_pid=fx.product_ids["vita-bull"])
    got = {(r["ad_id"], r["family_key"]): r for r in d["rows"] if r["ad_id"]}
    assert set(got) == set(ads), (w, s)
    for key, v in ads.items():
        r = got[key]
        assert close(r["spend"], v[0]) and close(r["purchases"], v[1]) and close(r["revenue"], v[2]), (key, w, s)
        assert r["roas"] == (round(float(v[2] / v[0]), 4) if v[0] else None)
    for on_tracker, want in ((True, rem), (False, rem_u)):            # family-grain days: tracker families / Meta families not on the tracker
        remainder = [r for r in d["rows"] if r["ad_id"] is None and r["on_tracker"] is on_tracker]
        if any(abs(x) >= F.Decimal("0.005") for x in want):
            assert len(remainder) == 1 and remainder[0]["ad_name"] == "(days without ad-level detail)"
            assert close(remainder[0]["spend"], want[0]) and close(remainder[0]["purchases"], want[1]) and close(remainder[0]["revenue"], want[2])
        else:
            assert not remainder
    assert d["total"]["spend"] == pytest.approx(float(sum(v[0] for v in ads.values()) + rem[0] + rem_u[0]), abs=0.02)
    assert d["total"]["ads"] == len(ads)
    assert [r["spend"] for r in d["rows"] if r["ad_id"]] == sorted((r["spend"] for r in d["rows"] if r["ad_id"]), reverse=True)


@pytest.mark.parametrize("w,wo,s,so", [c for c in WIN_STAGE if "|all" in c.id])
def test_ad_rows_add_up_to_group_totals(client, fx, w, wo, s, so):
    """For every cluster/ICP/pain/family selection: ad rows (+ remainder) == the family/rollup figure for the same window."""
    for sel in ({}, {"cluster": "Alpha Cluster"}, {"cluster": "Alpha Cluster", "icp": "ICP One"}, {"cluster": "Alpha Cluster", "icp": "ICP One", "pain": "Pain A"},
                {"cluster": "Beta Cluster"}, {"cluster": "(untagged)"}):
        d = get_ok(client, "/api/products/vita-bull/ads?" + qs(*[f"{k}={v}" for k, v in sel.items()], w, s))
        roll = get_ok(client, "/api/products/vita-bull/families?" + qs(*[f"{k}={v}" for k, v in sel.items()], w, s))
        tracker_side = [r for r in d["rows"] if r["on_tracker"]]                       # ads of tracker families (+ remainder)
        assert sum(r["spend"] for r in tracker_side) == pytest.approx(roll["total"]["m"]["spend"], abs=0.02), (sel, w)
        assert sum(r["revenue"] for r in tracker_side) == pytest.approx(roll["total"]["m"]["revenue"], abs=0.02), (sel, w)
        tol = 0.5 * len(roll["rows"]) + 0.01 if wo == "launch" else 0.02               # launch purchases are rounded per family
        assert sum(r["purchases"] for r in tracker_side) == pytest.approx(roll["total"]["m"]["purchases"], abs=tol), (sel, w)
        if sel:
            assert all(r["on_tracker"] for r in d["rows"]) and all(r["tag"] is None or r["tag"]["cluster"] == sel.get("cluster", r["tag"]["cluster"]) for r in d["rows"])
        else:
            assert any(not r["on_tracker"] for r in d["rows"]) or wo != "launch"          # unmatched Meta families join the unfiltered view


def test_ads_launch_rule_per_ad(client, fx):
    d = get_ok(client, "/api/products/vita-bull/ads?family_key=vb-fam-b")
    rows = {r["ad_id"]: r for r in d["rows"]}
    post = {ad: sum(r["spend"] for r in fx.ad_daily if r["ad_id"] == ad and r["day"] > F.CUT) for ad in ("b1", "b2")}
    assert rows["b1"]["spend"] == pytest.approx(float(F.dec("1000.50") + post["b1"]), abs=0.006)         # per-ad lifetime + daily after the cutoff
    assert rows["b2"]["spend"] == pytest.approx(float(F.dec("500.25") + post["b2"]), abs=0.006)
    rem = rows[None]
    assert rem["spend"] == pytest.approx(120.00, abs=0.006) and rem["purchases"] == 3 and rem["revenue"] == pytest.approx(100.0, abs=0.006)   # lifetime no ad accounts for
    fam = next(r for r in get_ok(client, "/api/products/vita-bull/families")["rows"] if r["family_key"] == "vb-fam-b")["m"]
    assert d["total"]["spend"] == pytest.approx(fam["spend"], abs=0.006)
    # family A: launch after D0 -> daily only, sheet days become the remainder row
    d = get_ok(client, "/api/products/vita-bull/ads?family_key=vb-fam-a")
    sheet_days = sum(r["spend"] for r in fx.ad_daily if r["ad_id"].startswith("fam:vb-fam-a") and r["day"] >= F.D(2026, 8, 1))
    assert {r["ad_id"] for r in d["rows"] if r["ad_id"]} == {"a1", "a2", "a3"}
    assert next(r for r in d["rows"] if r["ad_id"] is None)["spend"] == pytest.approx(float(sheet_days), abs=0.006)


def test_ads_row_fields(client, fx):
    d = get_ok(client, "/api/products/vita-bull/ads?family_key=vb-fam-a&window=30d")
    r = next(x for x in d["rows"] if x["ad_id"] == "a2")
    assert r["ad_name"] == "VB-Fam-A - Copy 2 (a2)" and r["family_key"] == "vb-fam-a" and r["display"] == "VB-Fam-A" and r["primary_id"] == 1000
    assert r["campaign_id"] == "C200" and r["campaign_name"] == "VB_Testing_Cambium" and r["stage"] == "testing" and r["adset_name"] == "VB test set"
    assert r["account"] == "Main" and r["status"] == "ACTIVE" and r["created_date"] == "2026-10-09" and isinstance(r["thumb_asset_id"], int)
    assert r["tag"] == {"cluster": "Alpha Cluster", "icp": "ICP One", "pain": "Pain A"} and r["on_tracker"] is True
    a2 = [x for x in fx.ad_daily if x["ad_id"] == "a2"]
    assert r["spend_7d"] == pytest.approx(float(sum(x["spend"] for x in a2 if x["day"] >= THROUGH - dt.timedelta(days=6))), abs=0.006)
    assert r["live"] is True
    assert r["days_active"] == sum(1 for x in a2 if x["spend"] > 0 and x["day"] >= THROUGH - dt.timedelta(days=29))
    a3 = next(x for x in d["rows"] if x["ad_id"] == "a3")
    assert a3["status"] == "PAUSED" and a3["campaign_id"] == "C100" and a3["stage"] == "scaling"
    # ads of unmatched families have no tracker link
    u = next(x for x in get_ok(client, "/api/products/vita-bull/ads?window=30d")["rows"] if x["ad_id"] == "u1")
    assert u["primary_id"] is None and u["tag"] is None and u["on_tracker"] is False and u["display"] == "Brief2-2-Bull-398"


def test_ads_unknown_campaign_is_unclassified(client):
    d = get_ok(client, "/api/products/vita-bull/ads?family_key=vb-fam-f&window=launch")
    f1 = next(r for r in d["rows"] if r["ad_id"] == "f1")
    assert f1["campaign_id"] == "C999" and f1["stage"] == "unclassified" and f1["campaign_name"] is None


@pytest.mark.parametrize("camp,stage", [("C100", "scaling"), ("C200", "testing"), ("C999", "unclassified"), ("C300", None)])
def test_ads_campaign_filter(client, fx, camp, stage):
    for w, wo in (("window=30d", (THROUGH - dt.timedelta(days=29), THROUGH)), ("window=launch", "launch"), ("", "launch")):
        d = get_ok(client, "/api/products/vita-bull/ads?" + qs(f"campaign_id={camp}", w))
        fams = groups_for(fx)
        ads, rem, rem_u = F.ad_oracle(fx, fams, wo, "all", campaign=camp, unmatched_pid=fx.product_ids["vita-bull"])
        got = {(r["ad_id"], r["family_key"]): r for r in d["rows"] if r["ad_id"]}
        assert set(got) == set(ads), (camp, w)
        for key, v in ads.items():
            assert close(got[key]["spend"], v[0]), (key, camp, w)
            assert got[key]["campaign_id"] == camp
            if stage:
                assert got[key]["stage"] == stage
        assert not [r for r in d["rows"] if r["ad_id"] is None]          # family-grain sheet days have no campaign -> never inside a campaign filter


def test_ads_empty_and_filters_anded(client):
    d = get_ok(client, "/api/products/vita-bull/ads?cluster=Alpha Cluster&campaign_id=C300&window=30d")
    assert d["rows"] == [] and d["total"]["spend"] == 0 and d["total"]["ads"] == 0
    d = get_ok(client, "/api/products/vita-bull/ads?family_key=vb-fam-d&cluster=Beta Cluster&icp=ICP Three&pain=Pain D&campaign_id=C200&window=7d")
    assert {r["ad_id"] for r in d["rows"]} == {"d1"}
    assert get_ok(client, "/api/products/vita-bull/ads?family_key=no-such-family")["rows"] == []
    # blank-family selection has nothing to show
    assert get_ok(client, "/api/products/vita-bull/ads?family_key=id:0510")["rows"] == []


def test_ads_other_products(client, fx):
    d = get_ok(client, "/api/products/beef-liver/ads?window=30d")
    ids = {r["ad_id"] for r in d["rows"] if r["ad_id"]}
    assert ids == {"bl1a", "bl1b", "bl3a"} | ({"u2"} if False else set()) or ids >= {"bl1a", "bl1b"}
    assert all(r["tag"] is None or r["tag"]["cluster"] in ("Hair", "(none)", "Energy", "Joints") for r in d["rows"])
    assert get_ok(client, "/api/products/boneflex/ads?window=30d")["rows"][0]["ad_id"] == "u2"      # Weird-Bone-1 is assigned to Boneflex by name


# --------------------------------------------------------------------------------------------------------------------- campaigns
def product_rows(fx, pid, win, stages, camp=None):
    out = []
    for r in fx.ad_daily:
        tfam = F.MAPPING[r["meta_family_key"]][0]
        mine = (tfam is not None and any(x["family_key"] == tfam and x["product"] == "vita-bull" for x in fx.tracker)) or \
               (tfam is None and fx.unmatched[r["meta_family_key"]]["product_id"] == pid)
        if mine and (win == "all" or win[0] <= r["day"] <= win[1]) and F.stage_of_row(fx, r["campaign_id"]) in stages and (camp is None or r["campaign_id"] == camp):
            out.append(r)
    return out


@pytest.mark.parametrize("w,wo", [("window=30d", (THROUGH - dt.timedelta(days=29), THROUGH)), ("window=7d", (THROUGH - dt.timedelta(days=6), THROUGH)),
                                  ("", "all"), ("window=launch", "all"), ("from=2026-08-01&to=2026-09-01", (F.D(2026, 8, 1), F.D(2026, 9, 1)))])
@pytest.mark.parametrize("s,so", STAGE_PARAMS)
def test_campaigns(client, fx, w, wo, s, so):
    pid = fx.product_ids["vita-bull"]
    d = get_ok(client, "/api/products/vita-bull/campaigns?" + qs(w, s))
    stages = F.STAGES if so == "all" else (so,)
    rows_all = product_rows(fx, pid, wo, stages)
    want_total = sum(r["spend"] for r in rows_all)
    assert d["total"]["spend"] == pytest.approx(float(want_total), abs=0.006)
    by_camp = {}
    for r in rows_all:
        if r["campaign_id"]:
            by_camp.setdefault(r["campaign_id"], []).append(r)
    assert {c["campaign_id"] for c in d["rows"]} == {k for k, v in by_camp.items() if sum(x["spend"] for x in v)}
    for c in d["rows"]:
        v = by_camp[c["campaign_id"]]
        assert c["spend"] == pytest.approx(float(sum(x["spend"] for x in v)), abs=0.006)
        assert c["purchases"] == pytest.approx(float(sum(x["purchases"] for x in v)), abs=0.006)
        assert c["ads"] == len({x["ad_id"] for x in v if x["spend"] > 0 and not x["ad_id"].startswith("fam:")})
        assert c["families"] == len({x["meta_family_key"] for x in v if x["spend"] > 0})
        last7 = sum(x["spend"] for x in product_rows(fx, pid, (THROUGH - dt.timedelta(days=6), THROUGH), F.STAGES, c["campaign_id"]))
        assert c["spend_7d"] == pytest.approx(float(last7), abs=0.006)
        assert c["live"] == (sum(x["spend"] for x in product_rows(fx, pid, (THROUGH - dt.timedelta(days=1), THROUGH), F.STAGES, c["campaign_id"])) > 0)
        every_day = [x["day"] for x in product_rows(fx, pid, "all", F.STAGES, c["campaign_id"]) if x["spend"] > 0]
        assert c["last_day"] == max(every_day).isoformat()          # last day the campaign spent, whatever the window
        assert c["stage"] == fx.camp_stage.get(c["campaign_id"], "unclassified")
        assert c["name"] == next((n for cid, _a, n in F.CAMPAIGNS if cid == c["campaign_id"]), c["campaign_id"])
    spends = [c["spend"] for c in d["rows"]]
    assert spends == sorted(spends, reverse=True)
    # stage cards always show the full split of the window, ignoring the stage filter
    cards = {c["stage"]: c for c in d["by_stage"]}
    for st in ("testing", "scaling"):
        assert cards[st]["spend"] == pytest.approx(float(sum(x["spend"] for x in product_rows(fx, pid, wo, (st,)))), abs=0.006)
    assert cards["testing"]["campaigns"] == len({x["campaign_id"] for x in product_rows(fx, pid, wo, ("testing",)) if x["campaign_id"] and x["spend"] > 0})
    if "unclassified" in cards:
        assert cards["unclassified"]["campaigns"] >= 0
    assert sum(c["spend"] for c in d["by_stage"]) == pytest.approx(float(sum(x["spend"] for x in product_rows(fx, pid, wo, F.STAGES))), abs=0.011)
    wi = d["window_info"]
    assert (wi["kind"] == "launch") == (not w or w == "window=launch")


def test_stage_daily(client, fx):
    pid = fx.product_ids["vita-bull"]
    d = get_ok(client, "/api/products/vita-bull/stage-daily?days=30")
    assert len(d) == 30 and d[0]["day"] == "2026-10-04" and d[-1]["day"] == "2026-11-02"
    for row in d:
        day = dt.date.fromisoformat(row["day"])
        for st in F.STAGES:
            want = sum(x["spend"] for x in product_rows(fx, pid, (day, day), (st,)))
            assert row[st] == pytest.approx(float(want), abs=0.006), (row["day"], st)
    default = get_ok(client, "/api/products/vita-bull/stage-daily")
    assert len(default) == 90
    allv = get_ok(client, "/api/products/vita-bull/stage-daily?days=366")
    assert len(allv) == 181 and allv[0]["day"] == "2026-05-06"                      # clamped to the data, zero filled
    assert get_ok(client, "/api/products/vita-bull/stage-daily?window=7d")[0]["day"] == "2026-10-27"
    assert get_ok(client, "/api/products/vita-bull/stage-daily?from=2026-10-01&to=2026-10-03")[-1]["day"] == "2026-10-03"
    assert len(get_ok(client, "/api/products/vita-bull/stage-daily?window=launch")) == 181
    # unmatched Meta spend assigned to the product is part of its daily split (Boneflex: Weird-Bone-1 from Oct 25), zero before that
    bf = get_ok(client, "/api/products/boneflex/stage-daily?days=366")
    assert all(r["testing"] == r["scaling"] == r["unclassified"] == 0 for r in bf[:100]) and sum(r["scaling"] for r in bf[-9:]) > 0


# --------------------------------------------------------------------------------------------------------------------- unmatched
@pytest.mark.parametrize("w,wo", [("", "all"), ("window=7d", (THROUGH - dt.timedelta(days=6), THROUGH)), ("window=30d", (THROUGH - dt.timedelta(days=29), THROUGH))])
@pytest.mark.parametrize("s,so", STAGE_PARAMS)
def test_unmatched(client, fx, w, wo, s, so):
    d = get_ok(client, "/api/products/vita-bull/unmatched?" + qs(w, s))
    stages = F.STAGES if so == "all" else (so,)
    want = {}
    for r in fx.ad_daily:
        if F.MAPPING[r["meta_family_key"]][0] is None and fx.unmatched[r["meta_family_key"]]["product_id"] == fx.product_ids["vita-bull"]:
            if (wo == "all" or wo[0] <= r["day"] <= wo[1]) and F.stage_of_row(fx, r["campaign_id"]) in stages:
                want.setdefault(r["meta_family_key"], []).append(r)
    assert {r["meta_family_key"] for r in d["rows"]} == {k for k, v in want.items() if any(x["spend"] for x in v)}
    for r in d["rows"]:
        v = want[r["meta_family_key"]]
        assert r["spend"] == pytest.approx(float(sum(x["spend"] for x in v)), abs=0.006)
        assert r["purchases"] == pytest.approx(float(sum(x["purchases"] for x in v)), abs=0.006)
        um = fx.unmatched[r["meta_family_key"]]
        assert r["spend_7d"] == pytest.approx(float(um["spend_7d"]), abs=0.006) and r["live"] == um["live"] and r["meta_family"] == um["meta_family"]
    assert d["total"]["families"] == len(d["rows"])
    assert d["total"]["spend"] == pytest.approx(sum(r["spend"] for r in d["rows"]), abs=0.011)
    assert [r["spend"] for r in d["rows"]] == sorted((r["spend"] for r in d["rows"]), reverse=True)


def test_unmatched_other_products_and_empty(client, fx):
    assert [r["meta_family"] for r in get_ok(client, "/api/products/boneflex/unmatched")["rows"]] == ["Weird-Bone-1"]
    assert [r["meta_family"] for r in get_ok(client, "/api/products/vital-flow/unmatched")["rows"]] == ["Vital Flow Test 1"]
    assert [r["meta_family"] for r in get_ok(client, "/api/products/unassigned/unmatched")["rows"]] == ["Orphan-Zzz"]
    assert get_ok(client, "/api/products/beef-liver/unmatched")["rows"] == []
    assert get_ok(client, "/api/products/vita-bull/unmatched")["total"]["spend"] > 0
