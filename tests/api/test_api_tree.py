"""v1.3: GET /api/products/<slug>/tree - same numbers as rollup / families, depth-first, one response."""
import copy
import itertools

import pytest

from .helpers import WINDOWS, get_ok, qs

EXTRAS = [
    "", "window=7d", "window=30d&stage=testing,scaling", "window=14d&compare=prev", "window=launch&stage=scaling,unclassified",
    "from=2026-10-10&to=2026-10-20&compare=30d&stage=testing,unclassified", "window=mtd&stage=unclassified",
]


def strip_share(m):
    m = copy.deepcopy(m)
    m.pop("spend_share", None)
    if "cmp" in m:
        m["cmp"].pop("spend_share", None)
        m["delta"].pop("spend_share_pp", None)
    return m


def by_key(rows):
    return {r["key"]: r for r in rows}


def children_of(rows, key):
    return [r for r in rows if r["parent_key"] == key]


# ------------------------------------------------------------------------------------------------------------------ structure
def test_shape_and_depth_first_order(client):
    d = get_ok(client, "/api/products/vita-bull/tree?root=cluster&depth=3")
    assert set(d) >= {"product", "root", "depth", "filters", "window_info", "compare_info", "rows", "total"}
    assert d["root"] == "cluster" and d["depth"] == 3 and d["compare_info"] is None
    fields = {"key", "parent_key", "level", "label", "cluster", "icp", "pain", "family_key", "primary_id", "color", "line_code", "trigger",
              "confidence", "ad_type", "status", "thumb_asset_id", "has_children", "child_count", "counted_here", "ads_manager_url", "story_url", "m"}
    fields_family = fields | {"ads_count", "ads_live"}
    for r in d["rows"]:
        assert set(r) == (fields_family if r["level"] == 3 else fields), r["key"]
    keys = [r["key"] for r in d["rows"]]
    assert len(keys) == len(set(keys))
    seen = {}
    for i, r in enumerate(d["rows"]):                      # depth-first: a parent precedes its children and its subtree is contiguous
        if r["parent_key"] is not None:
            assert r["parent_key"] in seen and seen[r["parent_key"]] < i and r["level"] == d["rows"][seen[r["parent_key"]]]["level"] + 1
        seen[r["key"]] = i
    stack = []
    for r in d["rows"]:
        while stack and stack[-1]["level"] >= r["level"]:
            stack.pop()
        assert (stack[-1]["key"] if stack else None) == r["parent_key"]
        stack.append(r)
    assert [r["label"] for r in d["rows"] if r["level"] == 0] == ["Alpha Cluster", "Beta Cluster", "(untagged)"]
    first = d["rows"][0]
    assert first["key"] == "Alpha Cluster" and first["level"] == 0 and first["parent_key"] is None and first["color"] == "#aa3333"
    assert d["rows"][1]["key"] == "Alpha Cluster|ICP One" and d["rows"][1]["parent_key"] == "Alpha Cluster"
    pain_a = by_key(d["rows"])["Alpha Cluster|ICP One|Pain A"]
    assert pain_a["line_code"] == "L01" and pain_a["trigger"] == "Fear" and pain_a["level"] == 2
    fam = by_key(d["rows"])["Alpha Cluster|ICP One|Pain A|vb-fam-a"]
    assert fam["level"] == 3 and fam["family_key"] == "vb-fam-a" and fam["label"] == "VB-Fam-A" and fam["primary_id"] == 1000
    assert fam["ad_type"] == "VID" and fam["status"] == "8 - Launched" and fam["confidence"] == "high" and fam["counted_here"] is True
    assert isinstance(fam["thumb_asset_id"], int) and fam["pain"] == "Pain A" and fam["cluster"] == "Alpha Cluster"
    assert first["family_key"] is None and first["primary_id"] is None and first["counted_here"] is None and first["confidence"] is None


def test_zero_line_untagged_and_ordering(client):
    d = get_ok(client, "/api/products/vita-bull/tree?root=cluster&depth=3")
    rows = by_key(d["rows"])
    l05 = rows["Beta Cluster|ICP Three|Pain E"]
    assert l05["m"]["unique_ads"] == 0 and l05["has_children"] is False and l05["child_count"] == 0 and l05["line_code"] == "L05"
    labels = [r["label"] for r in children_of(d["rows"], "Beta Cluster|ICP Three")]
    assert labels == ["Pain D", "Pain E"]                                            # taxonomy order, not spend order
    chain = [r["key"] for r in d["rows"] if r["cluster"] == "(untagged)"]
    assert chain == ["(untagged)", "(untagged)|(untagged)", "(untagged)|(untagged)|(untagged)", "(untagged)|(untagged)|(untagged)|vb-fam-e"]
    fams = children_of(d["rows"], "Alpha Cluster|ICP Two|Pain C")
    spends = [r["m"]["spend"] for r in fams]
    assert spends == sorted(spends, reverse=True) and {r["family_key"] for r in fams} == {"id:0510", "id:510", "vb-fam-f"}


def test_child_counts_and_has_children(client):
    d = get_ok(client, "/api/products/vita-bull/tree?root=cluster&depth=3")
    rows = by_key(d["rows"])
    assert rows["Alpha Cluster"]["child_count"] == 2 and rows["Alpha Cluster|ICP One"]["child_count"] == 2
    assert rows["Alpha Cluster|ICP One|Pain A"]["child_count"] == rows["Alpha Cluster|ICP One|Pain A"]["m"]["unique_ads"] == 2
    # family rows: Meta-ad children only where the family's first tracker row sits (the /ads rows for that group)
    assert rows["Alpha Cluster|ICP One|Pain A|vb-fam-a"]["child_count"] == 3 and rows["Alpha Cluster|ICP One|Pain A|vb-fam-a"]["has_children"] is True
    second = rows["Alpha Cluster|ICP One|Pain B|vb-fam-a"]
    assert second["counted_here"] is False and second["has_children"] is False and second["child_count"] == 0
    assert rows["Alpha Cluster|ICP Two|Pain C|id:0510"]["has_children"] is False                     # blank family: nothing to expand
    ads = get_ok(client, "/api/products/vita-bull/ads?family_key=vb-fam-a&cluster=Alpha Cluster&icp=ICP One&pain=Pain A")
    assert len([a for a in ads["rows"] if a["ad_id"]]) == 3


@pytest.mark.parametrize("depth,levels", [(0, {0}), (1, {0, 1}), (2, {0, 1, 2}), (3, {0, 1, 2, 3})])
def test_depth_param(client, depth, levels):
    d = get_ok(client, f"/api/products/vita-bull/tree?root=cluster&depth={depth}")
    assert {r["level"] for r in d["rows"]} == levels
    if depth == 0:                                       # root level only, but still knows about its children
        assert all(r["has_children"] for r in d["rows"] if r["m"]["unique_ads"])


def test_roots_and_filters(client):
    d = get_ok(client, "/api/products/vita-bull/tree?root=icp&cluster=Alpha Cluster&depth=1")
    assert [r["label"] for r in d["rows"] if r["level"] == 1] == ["ICP One", "ICP Two"] and d["rows"][0]["parent_key"] is None
    assert {r["cluster"] for r in d["rows"]} == {"Alpha Cluster"}
    d = get_ok(client, "/api/products/vita-bull/tree?root=pain&cluster=Alpha Cluster&icp=ICP One&depth=3")
    assert [r["level"] for r in d["rows"]] == [2, 3, 3, 2, 3]                    # depth capped at the families level (Pain A: 2 families, Pain B: 1)
    assert d["rows"][0]["key"] == "Alpha Cluster|ICP One|Pain A" and d["rows"][0]["parent_key"] is None
    d = get_ok(client, "/api/products/vita-bull/tree?root=family&cluster=Alpha Cluster&icp=ICP One&pain=Pain A&depth=2")
    assert {r["level"] for r in d["rows"]} == {3} and {r["family_key"] for r in d["rows"]} == {"vb-fam-a", "vb-fam-b"}
    assert get_ok(client, "/api/products/vita-bull/tree?root=cluster&cluster=Beta Cluster")["rows"][0]["label"] == "Alpha Cluster"   # extra filter ignored at root=cluster


def test_validation(client):
    bad = ["root=bogus", "root=icp", "root=pain&cluster=x", "root=family&cluster=x&icp=y", "root=pain&icp=y", "depth=4", "depth=-1", "depth=abc",
           "depth=1.5", "depth=", "root=cluster&depth=99"]
    for b in bad:
        r = client.get(f"/api/products/vita-bull/tree?{b}")
        if b == "depth=":
            assert r.status_code == 200          # blank = default depth 0
            continue
        assert r.status_code == 400 and "error" in r.get_json(), b
    assert client.get("/api/products/nope/tree").status_code == 404
    assert client.get("/api/products/vita-bull/tree?window=nope").status_code == 400
    assert client.get("/api/products/vita-bull/tree?compare=prev").status_code == 400


# ------------------------------------------------------------------------------------------------------------------ same numbers
@pytest.mark.parametrize("extra", EXTRAS)
def test_tree_equals_rollup_and_families(client, extra):
    for slug, first_cluster in (("vita-bull", None), ("beef-liver", None)):
        t = get_ok(client, f"/api/products/{slug}/tree?" + qs("root=cluster", "depth=3", extra))
        rows = t["rows"]
        roll = get_ok(client, f"/api/products/{slug}/rollup?" + qs("level=cluster", extra))
        assert t["total"]["m"] == roll["total"]["m"]
        lvl0 = [r for r in rows if r["level"] == 0]
        assert [r["label"] for r in lvl0] == [r["label"] for r in roll["rows"]]
        for tr, rr in zip(lvl0, roll["rows"]):
            assert tr["m"] == rr["m"], (slug, tr["label"], extra)                      # root level: identical, including spend_share
            assert tr["child_count"] == rr["child_count"] and tr["color"] == rr["color"]
            icp_roll = get_ok(client, f"/api/products/{slug}/rollup?" + qs("level=icp", f"cluster={tr['cluster']}", extra))
            lvl1 = [r for r in children_of(rows, tr["key"])]
            assert [r["label"] for r in lvl1] == [r["label"] for r in icp_roll["rows"]]
            for t1, r1 in zip(lvl1, icp_roll["rows"]):
                assert strip_share(t1["m"]) == strip_share(r1["m"]), (slug, t1["key"], extra)
                assert t1["child_count"] == r1["child_count"]
                pain_roll = get_ok(client, f"/api/products/{slug}/rollup?" + qs("level=pain", f"cluster={tr['cluster']}", f"icp={t1['icp']}", extra))
                lvl2 = children_of(rows, t1["key"])
                assert [r["label"] for r in lvl2] == [r["label"] for r in pain_roll["rows"]]
                for t2, r2 in zip(lvl2, pain_roll["rows"]):
                    assert strip_share(t2["m"]) == strip_share(r2["m"]), (slug, t2["key"], extra)
                    assert (t2["line_code"], t2["trigger"], t2["child_count"]) == (r2["line_code"], r2["trigger"], r2["child_count"])
                    fam_roll = get_ok(client, f"/api/products/{slug}/families?" + qs(f"cluster={tr['cluster']}", f"icp={t1['icp']}", f"pain={t2['pain']}", extra))
                    lvl3 = children_of(rows, t2["key"])
                    assert [r["family_key"] for r in lvl3] == [r["family_key"] for r in fam_roll["rows"]]
                    for t3, r3 in zip(lvl3, fam_roll["rows"]):
                        assert strip_share(t3["m"]) == strip_share(r3["m"]), (slug, t3["key"], extra)
                        assert (t3["primary_id"], t3["counted_here"], t3["confidence"], t3["ad_type"], t3["status"], t3["thumb_asset_id"], t3["label"]) == \
                               (r3["primary_id"], r3["counted_here"], r3["confidence"], r3["ad_type"], r3["status"], r3["thumb_asset_id"], r3["display"])
                    if t2["m"]["unique_ads"]:                                         # root = pain returns the same pain-level rows
                        sub = get_ok(client, f"/api/products/{slug}/tree?" + qs("root=family", f"cluster={tr['cluster']}", f"icp={t1['icp']}", f"pain={t2['pain']}", extra))
                        assert [r["m"] for r in sub["rows"]] == [r["m"] for r in fam_roll["rows"]]
                        assert sub["total"]["m"] == fam_roll["total"]["m"]


@pytest.mark.parametrize("extra", EXTRAS[:5])
def test_tree_roots_match_page_level_endpoints(client, extra):
    t = get_ok(client, "/api/products/vita-bull/tree?" + qs("root=icp", "cluster=Alpha Cluster", "depth=0", extra))
    roll = get_ok(client, "/api/products/vita-bull/rollup?" + qs("level=icp", "cluster=Alpha Cluster", extra))
    assert [r["m"] for r in t["rows"]] == [r["m"] for r in roll["rows"]] and t["total"]["m"] == roll["total"]["m"]
    t = get_ok(client, "/api/products/vita-bull/tree?" + qs("root=pain", "cluster=Alpha Cluster", "icp=ICP One", "depth=0", extra))
    roll = get_ok(client, "/api/products/vita-bull/rollup?" + qs("level=pain", "cluster=Alpha Cluster", "icp=ICP One", extra))
    assert [r["m"] for r in t["rows"]] == [r["m"] for r in roll["rows"]]


def test_tree_family_ad_children_add_up_via_ads_endpoint(client):
    """The UI fetches Meta ads lazily with the same filters; they must add up to the family row in the tree."""
    for extra in ("window=launch", "window=30d", "window=14d&compare=prev&stage=testing,scaling"):
        t = get_ok(client, "/api/products/vita-bull/tree?" + qs("root=cluster", "depth=3", extra))
        for r in t["rows"]:
            if r["level"] != 3 or not r["counted_here"] or r["family_key"].startswith("id:"):
                continue
            ads = get_ok(client, "/api/products/vita-bull/ads?" + qs(f"family_key={r['family_key']}", f"cluster={r['cluster']}", f"icp={r['icp']}", f"pain={r['pain']}", extra))
            assert ads["total"]["spend"] == pytest.approx(r["m"]["spend"], abs=0.011), (r["key"], extra)
            assert ads["total"]["revenue"] == pytest.approx(r["m"]["revenue"], abs=0.011)
            if "compare" in extra:
                assert ads["total"]["cmp"]["spend"] == pytest.approx(r["m"]["cmp"]["spend"], abs=0.011)


# ------------------------------------------------------------------------------------------------------------------ shares
@pytest.mark.parametrize("extra", ["", "window=30d", "window=14d&compare=prev&stage=testing,scaling"])
def test_shares_nest(client, extra):
    for slug in ("vita-bull", "beef-liver"):
        t = get_ok(client, f"/api/products/{slug}/tree?" + qs("root=cluster", "depth=3", extra))
        rows = t["rows"]
        total = t["total"]["m"]
        assert total["spend_share"] == 1.0
        assert sum(r["m"]["spend_share"] for r in rows if r["level"] == 0) == pytest.approx(1.0, abs=1e-4)
        for r in rows:
            assert r["m"]["spend_share"] == pytest.approx(r["m"]["spend"] / total["spend"], abs=1e-5)       # relative to the root total
            kids = children_of(rows, r["key"])
            if kids and r["level"] < 3:
                assert sum(k["m"]["spend_share"] for k in kids) == pytest.approx(r["m"]["spend_share"], abs=1e-4), r["key"]
            if "compare" in extra and total["cmp"]["spend"]:
                assert r["m"]["cmp"]["spend_share"] == pytest.approx(r["m"]["cmp"]["spend"] / total["cmp"]["spend"], abs=1e-5)
                if kids and r["level"] < 3:
                    assert sum(k["m"]["cmp"]["spend_share"] for k in kids) == pytest.approx(r["m"]["cmp"]["spend_share"], abs=1e-4)
                assert r["m"]["delta"]["spend_share_pp"] == pytest.approx((r["m"]["spend_share"] - r["m"]["cmp"]["spend_share"]) * 100, abs=1e-3)


def test_share_is_relative_to_root_of_the_response(client):
    t = get_ok(client, "/api/products/vita-bull/tree?root=icp&cluster=Alpha Cluster&depth=1&window=30d")
    assert t["total"]["m"]["spend_share"] == 1.0
    assert sum(r["m"]["spend_share"] for r in t["rows"] if r["level"] == 1) == pytest.approx(1.0, abs=1e-4)
    assert t["total"]["m"]["spend"] == pytest.approx(sum(r["m"]["spend"] for r in t["rows"] if r["level"] == 1), abs=0.011)


# ------------------------------------------------------------------------------------------------------------------ misc
def test_tracker_product_tree_orders_by_spend(client):
    t = get_ok(client, "/api/products/beef-liver/tree?root=cluster&depth=2&window=30d")
    lvl0 = [r for r in t["rows"] if r["level"] == 0]
    sp = [r["m"]["spend"] for r in lvl0]
    assert sp == sorted(sp, reverse=True) and "(none)" in [r["label"] for r in lvl0]
    assert all(r["line_code"] is None and r["color"] is None for r in t["rows"])
    assert t["product"]["tag_set"] == "tracker"


def test_tree_injection_strings_are_inert(client, pg):
    before = {t: pg.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("tracker_rows", "ad_daily", "products")}
    pg.rollback()
    evil = ["' OR 1=1; DROP TABLE tracker_rows; --", "\"; DELETE FROM ad_daily; --", "%27%20OR%20%271%27%3D%271", "Robert'); DROP TABLE products;--"]
    for e in evil:
        for root, url in (("icp", f"root=icp&cluster={e}"), ("pain", f"root=pain&cluster={e}&icp={e}"),
                          ("family", f"root=family&cluster={e}&icp={e}&pain={e}")):
            r = client.get("/api/products/vita-bull/tree?" + url)
            assert r.status_code == 200 and r.get_json()["rows"] == [], url          # no such cluster -> empty, never an error
        r = client.get(f"/api/products/vita-bull/tree?root=cluster&cluster={e}&icp={e}&depth=3")
        assert r.status_code == 200 and r.get_json()["rows"]                          # filters below the root level are ignored
    after = {t: pg.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in before}
    pg.rollback()
    assert before == after
