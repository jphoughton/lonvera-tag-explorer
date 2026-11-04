"""products / rollup / families vs the independent oracle, for every window x stage combination."""
import itertools

import pytest

from . import fixture_data as F
from .helpers import STAGE_PARAMS, WINDOWS, check_m, get_ok, group_rows, qs, rows_by_product

WIN_STAGE = [pytest.param(w, wo, s, so, id=f"{(w or 'default')}|{s or 'all'}") for (w, wo), (s, so) in itertools.product(WINDOWS, STAGE_PARAMS)]


# --------------------------------------------------------------------------------------------------------------------- products
@pytest.mark.parametrize("w,wo,s,so", WIN_STAGE)
def test_products(client, fx, w, wo, s, so):
    data = get_ok(client, "/api/products?" + qs(w, s))
    assert [p["slug"] for p in data] == ["vita-bull", "beef-liver", "boneflex", "vital-flow", "unassigned"]      # sort_order; empty ones only with Meta spend
    by = {p["slug"]: p for p in data}
    for slug in ("vita-bull", "beef-liver", "boneflex"):
        rows = rows_by_product(fx, slug)
        assert by[slug]["rows"] == len(rows)
        check_m(by[slug]["m"], F.aggregate(fx, rows, wo, so), f"{slug} {w} {s}")
    assert by["vita-bull"]["tag_label"] == "Line-mapped tags" and by["vita-bull"]["tag_set"] == "linemap"
    assert by["beef-liver"]["tag_label"] == "Tracker tags — not line-mapped yet"
    assert by["vital-flow"]["rows"] == 0 and by["vital-flow"]["m"]["spend"] == 0       # no tracker rows, Meta spend only -> listed
    assert "vita-belle" not in by                                                         # no rows, no spend -> excluded


# --------------------------------------------------------------------------------------------------------------------- rollup
def expected_rollup_rows(fx, slug, level, cluster, icp):
    """(label, tracker rows) in the order the API must return them."""
    rows = rows_by_product(fx, slug)
    cols = {"cluster": 1, "icp": 2, "pain": 3}[level]
    rows = [r for r in rows if (cluster is None or F.tag_labels(r)[0] == cluster) and (icp is None or F.tag_labels(r)[1] == icp)]
    groups = group_rows(rows, range(cols))
    out = []
    if slug == "vita-bull":                                      # taxonomy skeleton first (incl. the line with no ads), then the rest
        seen = set()
        for ln in F.TAXONOMY:
            if cluster is not None and ln["cluster"] != cluster:
                continue
            if icp is not None and ln["icp"] != icp:
                continue
            key = (ln["cluster"], ln["icp"], ln["pain"])[:cols]
            if key not in seen:
                seen.add(key)
                out.append((key, groups.get(key, [])))
        extra = [k for k in groups if k not in seen]
    else:
        extra = list(groups)
    return out, extra


@pytest.mark.parametrize("w,wo,s,so", WIN_STAGE)
def test_rollup_vita_bull_levels(client, fx, w, wo, s, so):
    base = "/api/products/vita-bull/rollup?"
    # cluster level
    d = get_ok(client, base + qs("level=cluster", w, s))
    assert d["product"] == {"slug": "vita-bull", "name": "Vita Bull", "tag_set": "linemap", "tag_label": "Line-mapped tags"}
    assert d["level"] == "cluster" and d["filters"] == {"cluster": None, "icp": None}
    sk, extra = expected_rollup_rows(fx, "vita-bull", "cluster", None, None)
    assert [r["label"] for r in d["rows"]] == [k[0] for k, _ in sk] + [e[0] for e in extra]
    assert d["rows"][-1]["label"] == "(untagged)"
    for row, (key, rows) in zip(d["rows"], sk):
        check_m(row["m"], F.aggregate(fx, rows, wo, so), f"cluster {key}")
        assert row["cluster"] == key[0] and row["icp"] is None and row["color"] in ("#aa3333", "#3355aa")
    assert [r["child_count"] for r in d["rows"][:2]] == [2, 1]               # Alpha: ICP One+Two; Beta: ICP Three
    total_rows = rows_by_product(fx, "vita-bull")
    check_m(d["total"]["m"], F.aggregate(fx, total_rows, wo, so), "total")
    # total == sum of rows (ratios are recomputed, unique ads is a distinct count)
    for k in ("spend", "purchases", "revenue", "spend_7d", "live_now", "active_ads"):
        assert d["total"]["m"][k] == pytest.approx(sum(r["m"][k] for r in d["rows"]), abs=0.011)
    assert d["total"]["m"]["unique_ads"] == 8
    # icp level
    d = get_ok(client, base + qs("level=icp", "cluster=Alpha Cluster", w, s))
    sk, _ = expected_rollup_rows(fx, "vita-bull", "icp", "Alpha Cluster", None)
    assert [r["label"] for r in d["rows"]] == ["ICP One", "ICP Two"]
    for row, (key, rows) in zip(d["rows"], sk):
        check_m(row["m"], F.aggregate(fx, rows, wo, so), f"icp {key}")
        assert row["cluster"] == "Alpha Cluster" and row["icp"] == key[1]
    assert [r["child_count"] for r in d["rows"]] == [2, 1]
    check_m(d["total"]["m"], F.aggregate(fx, [r for _, rows in sk for r in rows], wo, so), "icp total")
    # pain level
    d = get_ok(client, base + qs("level=pain", "cluster=Beta Cluster", "icp=ICP Three", w, s))
    sk, _ = expected_rollup_rows(fx, "vita-bull", "pain", "Beta Cluster", "ICP Three")
    assert [r["label"] for r in d["rows"]] == ["Pain D", "Pain E"]
    assert [(r["line_code"], r["trigger"]) for r in d["rows"]] == [("L04", "Desire"), ("L05", "Pride")]
    for row, (key, rows) in zip(d["rows"], sk):
        check_m(row["m"], F.aggregate(fx, rows, wo, so), f"pain {key}")
    zero = d["rows"][1]["m"]                       # taxonomy line with no ads still listed, all zero
    assert zero["unique_ads"] == 0 and zero["spend"] == 0 and zero["roas"] is None and zero["cpa"] is None


def test_rollup_untagged_chain(client, fx):
    d = get_ok(client, "/api/products/vita-bull/rollup?level=icp&cluster=(untagged)")
    assert [r["label"] for r in d["rows"]] == ["(untagged)"]
    d = get_ok(client, "/api/products/vita-bull/rollup?level=pain&cluster=(untagged)&icp=(untagged)")
    assert [r["label"] for r in d["rows"]] == ["(untagged)"] and d["rows"][0]["m"]["unique_ads"] == 1


@pytest.mark.parametrize("w,wo,s,so", [p for p in WIN_STAGE if "launch" in p.id or "7d" in p.id or "from=2026-10-10" in p.id])
def test_rollup_tracker_tag_product(client, fx, w, wo, s, so):
    d = get_ok(client, "/api/products/beef-liver/rollup?" + qs("level=cluster", w, s))
    assert d["product"]["tag_label"] == "Tracker tags — not line-mapped yet"
    _, extra = expected_rollup_rows(fx, "beef-liver", "cluster", None, None)
    rows = rows_by_product(fx, "beef-liver")
    groups = group_rows(rows, range(1))
    aggs = {k: F.aggregate(fx, v, wo, so) for k, v in groups.items()}
    want = sorted(groups, key=lambda k: (k[0] == "(untagged)", -float(aggs[k]["spend"]), k))
    assert [r["label"] for r in d["rows"]] == [k[0] for k in want]                 # spend desc (ties by label), no taxonomy
    assert "(none)" in [r["label"] for r in d["rows"]]                               # blank tracker value -> literal (none)
    for row in d["rows"]:
        check_m(row["m"], aggs[(row["label"],)], f"BL cluster {row['label']}")
        assert row["color"] is None and row["line_code"] is None
    d = get_ok(client, "/api/products/beef-liver/rollup?level=pain&cluster=Hair&icp=BL1 Weekend Face")
    assert [r["label"] for r in d["rows"]] == ["Hair Loss"] and d["rows"][0]["child_count"] == 2      # BL1 + BL3 families
    assert d["rows"][0]["line_code"] is None


def test_rollup_validation_and_empty(client):
    assert client.get("/api/products/vita-bull/rollup?level=icp").status_code == 400
    assert client.get("/api/products/vita-bull/rollup?level=pain&cluster=x").status_code == 400
    assert client.get("/api/products/vita-bull/rollup?level=zzz").status_code == 400
    assert client.get("/api/products/nope/rollup").status_code == 404
    d = get_ok(client, "/api/products/vita-bull/rollup?level=icp&cluster=Nonexistent")
    assert d["rows"] == [] and d["total"]["m"]["unique_ads"] == 0
    d = get_ok(client, "/api/products/boneflex/rollup")
    assert [r["label"] for r in d["rows"]] == ["Joints"] and d["rows"][0]["m"]["spend"] == 0
    d = get_ok(client, "/api/products/vital-flow/rollup")
    assert d["rows"] == [] and d["unmatched"]["families"] == 1


def test_rollup_unmatched_summary(client, fx):
    u = get_ok(client, "/api/products/vita-bull/rollup")["unmatched"]
    um = [x for x in fx.unmatched.values() if x["product_id"] == fx.product_ids["vita-bull"]]
    assert u["spend_7d"] == pytest.approx(float(sum(x["spend_7d"] for x in um)), abs=0.006)
    assert u["spend_30d"] == pytest.approx(float(sum(x["spend_30d"] for x in um)), abs=0.006)
    assert u["live_families"] == sum(1 for x in um if x["live"]) and u["families"] == sum(1 for x in um if x["spend_7d"] > 0)


# --------------------------------------------------------------------------------------------------------------------- families
def expected_families(fx, slug, cluster=None, icp=None, pain=None):
    out = {}
    for r in rows_by_product(fx, slug):
        c, i, p = F.tag_labels(r)
        if (cluster, icp, pain) != (None, None, None) and not ((cluster in (None, c)) and (icp in (None, i)) and (pain in (None, p))):
            continue
        out.setdefault(r["family_key"], []).append(r)
    return out


@pytest.mark.parametrize("w,wo,s,so", WIN_STAGE)
def test_families_all_and_filtered(client, fx, w, wo, s, so):
    for slug, flt in (("vita-bull", {}), ("vita-bull", {"cluster": "Alpha Cluster"}), ("vita-bull", {"cluster": "Alpha Cluster", "icp": "ICP One"}),
                      ("vita-bull", {"cluster": "Alpha Cluster", "icp": "ICP One", "pain": "Pain B"}), ("vita-bull", {"cluster": "(untagged)"}),
                      ("beef-liver", {}), ("beef-liver", {"cluster": "(none)"})):
        query = qs(*[f"{k}={v}" for k, v in flt.items()], w, s)
        d = get_ok(client, f"/api/products/{slug}/families?{query}")
        fams = expected_families(fx, slug, **flt)
        assert {r["family_key"] for r in d["rows"]} == set(fams), (slug, flt)
        assert d["filters"] == {"cluster": flt.get("cluster"), "icp": flt.get("icp"), "pain": flt.get("pain")}
        firsts = {}
        for r in sorted(fx.tracker, key=lambda r: (r["sheet_row"], r["id"])):
            firsts.setdefault((r["product_id"], r["family_key"]), r["id"])
        for row in d["rows"]:
            rows = fams[row["family_key"]]
            agg = F.aggregate(fx, rows, wo, so)
            agg["unique"] = 1
            check_m(row["m"], agg, f"{slug} {flt} {row['family_key']}")
            assert row["n_rows"] == len(rows) and row["ad_ids"] == [r["id"] for r in sorted(rows, key=lambda r: r["sheet_row"])]
            assert row["primary_id"] == row["ad_ids"][0] and row["concept_ids"] == [r["concept"] for r in sorted(rows, key=lambda r: r["sheet_row"])]
            counted = any(firsts[(r["product_id"], r["family_key"])] == r["id"] for r in rows)
            assert row["counted_here"] is counted
            if not counted:
                assert row["m"]["spend"] == 0 and row["m"]["purchases"] == 0 and row["m"]["live_now"] == 0 and row["m"]["unique_ads"] == 1
        agg = F.aggregate(fx, [r for rs in fams.values() for r in rs], wo, so)
        check_m(d["total"]["m"], agg, f"{slug} {flt} total")
        assert d["total"]["m"]["unique_ads"] == len(fams)
        spends = [r["m"]["spend"] for r in d["rows"]]
        assert spends == sorted(spends, reverse=True)


def test_family_first_row_attribution(client, fx):
    """VB-Fam-A has 2 tracker rows in different lines (L01 first, L02 second): metrics only where counted_here."""
    l01 = get_ok(client, "/api/products/vita-bull/families?cluster=Alpha Cluster&icp=ICP One&pain=Pain A")
    l02 = get_ok(client, "/api/products/vita-bull/families?cluster=Alpha Cluster&icp=ICP One&pain=Pain B")
    a1 = next(r for r in l01["rows"] if r["family_key"] == "vb-fam-a")
    a2 = next(r for r in l02["rows"] if r["family_key"] == "vb-fam-a")
    assert a1["counted_here"] is True and a1["m"]["spend"] > 0 and a1["m"]["unique_ads"] == 1
    assert a2["counted_here"] is False and a2["m"]["spend"] == 0 and a2["m"]["purchases"] == 0 and a2["m"]["unique_ads"] == 1
    assert a2["m"]["live_now"] == 0 and a2["m"]["spend_7d"] == 0 and a2["m"]["active_ads"] == 0
    assert a1["confidence"] == "high" and a2["confidence"] == "low"
    # whole-product table: the family appears once, with both rows
    allf = get_ok(client, "/api/products/vita-bull/families")["rows"]
    assert sum(1 for r in allf if r["family_key"] == "vb-fam-a") == 1
    both = next(r for r in allf if r["family_key"] == "vb-fam-a")
    assert both["n_rows"] == 2 and both["confidence"] == "low" and both["counted_here"] and both["m"]["spend"] == a1["m"]["spend"]
    # the two group totals each carry the family exactly once in unique_ads, spend only once overall
    cluster = get_ok(client, "/api/products/vita-bull/rollup?level=pain&cluster=Alpha Cluster&icp=ICP One")
    assert [r["m"]["unique_ads"] for r in cluster["rows"]] == [2, 1]
    assert cluster["rows"][1]["m"]["spend"] == 0


def test_blank_family_and_leading_zero_concepts(client, fx):
    rows = get_ok(client, "/api/products/vita-bull/families")["rows"]
    by = {r["family_key"]: r for r in rows}
    assert "id:0510" in by and "id:510" in by and by["id:0510"]["concept_ids"] == ["0510"] and by["id:510"]["concept_ids"] == ["510"]
    for k in ("id:0510", "id:510"):
        assert by[k]["m"]["unique_ads"] == 1 and by[k]["m"]["spend"] == 0 and by[k]["m"]["purchases"] == 0 and by[k]["m"]["revenue"] == 0
        assert by[k]["m"]["roas"] is None and by[k]["counted_here"] is True and by[k]["display"] in ("0510", "510")
    # "510" has a launch date but a blank family cannot be matched to spend, so it must stay at zero
    assert by["id:510"]["launch_date"] == "2026-09-01"
    pain_c = get_ok(client, "/api/products/vita-bull/rollup?level=pain&cluster=Alpha Cluster&icp=ICP Two")["rows"][0]
    assert pain_c["label"] == "Pain C" and pain_c["m"]["unique_ads"] == 3          # id:0510, id:510 and VB-Fam-F all counted as unique ads


def test_family_row_fields(client, fx):
    rows = {r["family_key"]: r for r in get_ok(client, "/api/products/vita-bull/families")["rows"]}
    a = rows["vb-fam-a"]
    assert a["display"] == "VB-Fam-A" and a["ad_type"] == "VID" and a["status"] == "8 - Launched" and a["launch_date"] == "2026-08-01"
    assert isinstance(a["thumb_asset_id"], int) and a["method"] == "text only"          # method of the lowest-confidence row
    assert rows["vb-fam-b"]["thumb_asset_id"] is not None and rows["vb-fam-c"]["thumb_asset_id"] is None
    assert rows["vb-fam-e"]["confidence"] is None and rows["vb-fam-e"]["method"] is None            # untagged
    assert rows["vb-fam-c"]["launch_date"] is None
    assert {"cluster", "icp", "pain", "line_code"} <= set(a)
    bl = {r["family_key"]: r for r in get_ok(client, "/api/products/beef-liver/families")["rows"]}
    assert bl["id:bl-4"]["display"] == "BL-4" and bl["bl-fam-1"]["confidence"] is None


def test_since_launch_rule_values(client, fx):
    """Spot-check rule 3 on three families: launch after D0, launch before D0 (lifetime + post-cutoff daily), no launch date."""
    rows = {r["family_key"]: r["m"] for r in get_ok(client, "/api/products/vita-bull/families")["rows"]}
    # VB-Fam-B launched 2026-04-01 (< D0): lifetime (1500.75 + 120 extra) + daily after 2026-09-17, never the daily rows before it
    post = sum(v[0] for (f, d, st), v in fx.family_daily.items() if f == "vb-fam-b" and d > F.CUT)
    pre = sum(v[0] for (f, d, st), v in fx.family_daily.items() if f == "vb-fam-b" and d <= F.CUT)
    assert pre > 0
    assert rows["vb-fam-b"]["spend"] == pytest.approx(float(F.dec("1620.75") + post), abs=0.006)
    # VB-Fam-C has no launch date: since-launch is 0 but a 30d window still shows its daily spend
    assert rows["vb-fam-c"]["spend"] == 0
    c30 = {r["family_key"]: r["m"] for r in get_ok(client, "/api/products/vita-bull/families?window=30d")["rows"]}
    assert c30["vb-fam-c"]["spend"] > 0
    # VB-Fam-A (launch 2026-08-01 >= D0): sum of mapped daily rows (sheet family rows + API ad rows) from the launch day
    allsp = sum(v[0] for (f, d, st), v in fx.family_daily.items() if f == "vb-fam-a" and d >= F.D(2026, 8, 1))
    assert rows["vb-fam-a"]["spend"] == pytest.approx(float(allsp), abs=0.006)
    # launch purchases are whole numbers per family (the sheet rounds per family)
    assert all(float(r["purchases"]).is_integer() for r in rows.values())
    # ... but windowed purchases keep decimals
    assert any(not float(r["purchases"]).is_integer() for r in c30.values())


def test_live_and_fixed_7d_do_not_follow_window(client, fx):
    a = {r["family_key"]: r["m"] for r in get_ok(client, "/api/products/vita-bull/families?window=1d&stage=testing")["rows"]}
    b = {r["family_key"]: r["m"] for r in get_ok(client, "/api/products/vita-bull/families?window=180d")["rows"]}
    for k in a:
        assert a[k]["live_now"] == b[k]["live_now"] and a[k]["spend_7d"] == b[k]["spend_7d"] and a[k]["cpa_7d"] == b[k]["cpa_7d"]
        assert a[k]["unique_ads"] == b[k]["unique_ads"] == 1
    assert a["vb-fam-d"]["live_now"] == 1 and a["vb-fam-f"]["live_now"] == 0


def test_totals_equal_sum_of_rows_everywhere(client, fx):
    for w, _ in WINDOWS[:6]:
        d = get_ok(client, "/api/products/vita-bull/families?" + w)
        for k in ("spend", "purchases", "revenue", "spend_7d", "live_now", "active_ads"):
            assert d["total"]["m"][k] == pytest.approx(sum(r["m"][k] for r in d["rows"]), abs=0.011), (w, k)
        t = d["total"]["m"]
        assert t["roas"] is None or t["roas"] == pytest.approx(t["revenue"] / t["spend"], abs=1e-3)
