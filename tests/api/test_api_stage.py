"""v1.3: stage = comma-separated include list (testing, scaling, unclassified)."""
import datetime as dt
import itertools

import pytest

from app import queries as q

from . import fixture_data as F
from .helpers import THROUGH, WINDOWS, check_m, get_ok, qs, rows_by_product
from .test_api_compare import PAIRS, check_cmp, n_days

D = dt.date
META = q.DataMeta(F.D0, THROUGH)

COMBOS = [
    ("stage=testing,scaling", ("testing", "scaling")),
    ("stage=unclassified", ("unclassified",)),
    ("stage=scaling,unclassified", ("scaling", "unclassified")),
    ("stage=unclassified,testing", ("testing", "unclassified")),
    ("stage=testing,scaling,unclassified", "all"),
    ("stage=unclassified,scaling,testing", "all"),
    ("stage=all", "all"),
    ("stage=TESTING,%20Scaling", ("testing", "scaling")),
    ("stage=testing", ("testing",)),
]
KEY_WINDOWS = [w for w in WINDOWS if w[0] in ("window=launch", "window=7d", "window=30d", "window=180d", "from=2026-10-10&to=2026-10-20", "window=mtd")]


def parse(**kw):
    return q.parse_window(kw, META)


# ------------------------------------------------------------------------------------------------------------------ parsing
def test_parse_stage_values():
    assert parse().stage == "all" and parse().stages == ["testing", "scaling", "unclassified"]
    assert parse(stage="all").stage == "all"
    assert parse(stage="testing,scaling,unclassified").stage == "all"          # all three listed == all
    assert parse(stage="scaling,testing").stage == "testing,scaling"           # canonical order
    assert parse(stage=" Unclassified , TESTING ").stages == ["testing", "unclassified"]
    assert parse(stage="testing,testing").stages == ["testing"]
    assert parse(stage="all,testing").stage == "all"
    info = parse(window="7d", stage="scaling,unclassified").info(META)
    assert info["stage"] == "scaling,unclassified" and info["stages"] == ["scaling", "unclassified"]


@pytest.mark.parametrize("bad", ["", ",", " , ", "bogus", "testing,bogus", "ALL;DROP", "testing;", "both", "none", "test"])
def test_parse_stage_rejects(bad):
    with pytest.raises(q.ApiError) as e:
        parse(stage=bad)
    assert e.value.status == 400


def test_launch_note_depends_on_unclassified():
    assert "classified days" in parse(stage="testing").note
    assert "classified days" in parse(stage="testing,scaling").note
    assert parse(stage="testing,unclassified").note is None and parse(stage="unclassified").note is None and parse().note is None
    assert parse(window="7d", stage="testing").note is None


@pytest.mark.parametrize("url", [
    "/api/products", "/api/products/vita-bull/rollup", "/api/products/vita-bull/families", "/api/products/vita-bull/ads",
    "/api/products/vita-bull/campaigns", "/api/products/vita-bull/unmatched", "/api/products/vita-bull/tree", "/api/ads/1000",
    "/api/products/vita-bull/stage-daily"])
def test_http_stage_400_everywhere(client, url):
    for bad in ("", ",", "bogus", "testing,bogus", "testing%3BDROP%20TABLE%20x"):
        r = client.get(f"{url}?stage={bad}")
        assert r.status_code == 400 and "error" in r.get_json(), (url, bad)
    for ok in ("testing,scaling", "unclassified", "all", "scaling"):
        assert client.get(f"{url}?stage={ok}").status_code == 200, (url, ok)
    assert client.get(url).status_code == 200


def test_window_info_carries_canonical_stage(client):
    d = get_ok(client, "/api/products/vita-bull/rollup?window=7d&stage=scaling,testing")
    assert d["window_info"]["stage"] == "testing,scaling" and d["window_info"]["stages"] == ["testing", "scaling"]
    assert get_ok(client, "/api/products/vita-bull/rollup?window=7d")["window_info"]["stage"] == "all"


# ------------------------------------------------------------------------------------------------------------------ numbers
CASES = [pytest.param(w, wo, s, so, id=f"{w or 'default'}|{s}") for (w, wo), (s, so) in itertools.product(KEY_WINDOWS, COMBOS)]


@pytest.mark.parametrize("w,wo,s,so", CASES)
def test_products_rollup_families_with_stage_lists(client, fx, w, wo, s, so):
    prods = {p["slug"]: p for p in get_ok(client, "/api/products?" + qs(w, s))}
    for slug in ("vita-bull", "beef-liver"):
        check_m(prods[slug]["m"], F.aggregate(fx, rows_by_product(fx, slug), wo, so), f"{slug} {w} {s}")
    d = get_ok(client, "/api/products/vita-bull/rollup?" + qs("level=cluster", w, s))
    for row in d["rows"]:
        rows = [r for r in rows_by_product(fx, "vita-bull") if F.tag_labels(r)[0] == row["label"]]
        check_m(row["m"], F.aggregate(fx, rows, wo, so), f"{row['label']} {w} {s}")
    check_m(d["total"]["m"], F.aggregate(fx, rows_by_product(fx, "vita-bull"), wo, so), "total")
    d = get_ok(client, "/api/products/vita-bull/families?" + qs(w, s))
    fams = {}
    for r in rows_by_product(fx, "vita-bull"):
        fams.setdefault(r["family_key"], []).append(r)
    for row in d["rows"]:
        agg = F.aggregate(fx, fams[row["family_key"]], wo, so)
        agg["unique"] = 1
        check_m(row["m"], agg, f"{row['family_key']} {w} {s}")


def test_stage_split_is_unfiltered_and_launch_tieout(client):
    """m.stage / testing_pct stay the unfiltered split; the three single-stage totals add up to the all-stage total."""
    base = get_ok(client, "/api/products/vita-bull/rollup?window=30d")["total"]["m"]
    for s in ("stage=testing", "stage=scaling,unclassified", "stage=unclassified"):
        m = get_ok(client, "/api/products/vita-bull/rollup?window=30d&" + s)["total"]["m"]
        assert m["stage"] == base["stage"] and m["testing_pct"] == base["testing_pct"]
    for window in ("window=30d", "window=launch", "from=2026-06-01&to=2026-09-01"):
        parts = [get_ok(client, f"/api/products/vita-bull/rollup?{window}&stage={s}")["total"]["m"] for s in ("testing", "scaling", "unclassified")]
        whole = get_ok(client, f"/api/products/vita-bull/rollup?{window}")["total"]["m"]
        assert sum(p["spend"] for p in parts) == pytest.approx(whole["spend"], abs=0.011), window
        assert sum(p["revenue"] for p in parts) == pytest.approx(whole["revenue"], abs=0.011), window
        assert whole["spend"] > 0
        all3 = get_ok(client, f"/api/products/vita-bull/rollup?{window}&stage=testing,scaling,unclassified")["total"]["m"]
        assert all3["spend"] == whole["spend"] and all3["purchases"] == whole["purchases"] and all3["revenue"] == whole["revenue"]


def test_launch_stage_list_partitions_the_api_lifetime(client, fx):
    """Since launch is the API lifetime. A stage list takes the daily rows of the included stages; what the daily rows do not explain
    (history before the daily range, restatements, sheet-only days) is 'unclassified'. All three stages add up to the lifetime exactly."""
    def fam(stage):
        rows = get_ok(client, f"/api/products/vita-bull/families?window=launch&stage={stage}")["rows"]
        return next(r for r in rows if r["family_key"] == "vb-fam-b")["m"]
    lt = F.family_lifetime(fx, "vb-fam-b")
    allm = fam("all")
    assert allm["spend"] == pytest.approx(float(lt["spend"]), abs=0.006) and allm["impressions"] == lt["imp"] and allm["link_clicks"] == lt["clk"]
    daily = F.daily_vec(fx, "vb-fam-b", None, None, ("testing", "scaling"))
    assert fam("testing,scaling")["spend"] == pytest.approx(float(daily[0]), abs=0.006)             # no lifetime without 'unclassified'
    parts = [fam(s)["spend"] for s in ("testing", "scaling", "unclassified")]
    assert sum(parts) == pytest.approx(allm["spend"], abs=0.011)
    assert fam("unclassified")["spend"] > 1000                                                       # b1 + b2 history before the daily range
    assert fam("scaling,unclassified")["spend"] + fam("testing")["spend"] == pytest.approx(allm["spend"], abs=0.011)


def test_launch_traffic_with_stage_lists(client, fx):
    rows = lambda st: {r["family_key"]: r["m"] for r in get_ok(client, f"/api/products/vita-bull/families?window=launch&stage={st}")["rows"]}   # noqa: E731
    a, t, s_, u = rows("all"), rows("testing"), rows("scaling"), rows("unclassified")
    for k in ("vb-fam-a", "vb-fam-b", "vb-fam-d"):
        for field in ("impressions", "link_clicks"):
            assert t[k][field] + s_[k][field] + u[k][field] == a[k][field], (k, field)


@pytest.mark.parametrize("w,wo,s,so", [c for c in CASES if "launch" in c.id or "7d" in c.id][:12])
def test_ads_with_stage_lists(client, fx, w, wo, s, so):
    d = get_ok(client, "/api/products/vita-bull/ads?" + qs(w, s))
    fams = {r["family_key"] for r in fx.tracker if r["product"] == "vita-bull" and not r["family_key"].startswith("id:")}
    ads, rem, rem_u = F.ad_oracle(fx, fams, wo, so, unmatched_pid=fx.product_ids["vita-bull"])
    got = {(r["ad_id"], r["family_key"]): r for r in d["rows"] if r["ad_id"]}
    assert set(got) == set(ads)
    for key, v in ads.items():
        assert got[key]["spend"] == pytest.approx(float(v["spend"]), abs=0.006) and got[key]["revenue"] == pytest.approx(float(v["revenue"]), abs=0.006)
        assert got[key]["impressions"] == v["imp"] and got[key]["link_clicks"] == v["clk"], key
    remainder = next((r for r in d["rows"] if r["ad_id"] is None and r["on_tracker"]), None)
    if any(abs(x) >= F.Decimal("0.005") for x in (rem["spend"], rem["purchases"], rem["revenue"])):
        assert remainder is not None and remainder["spend"] == pytest.approx(float(rem["spend"]), abs=0.006)
    assert d["window_info"]["stage"] == q.parse_stage({"stage": s.split("=")[1].replace("%20", "")}) if s else True


def test_campaigns_unmatched_stage_lists(client, fx):
    d = get_ok(client, "/api/products/vita-bull/campaigns?window=30d&stage=testing,unclassified")
    assert all(r["stage"] in ("testing", "unclassified") for r in d["rows"])
    assert {c["stage"] for c in d["by_stage"]} >= {"testing", "scaling"}          # cards ignore the filter
    full = get_ok(client, "/api/products/vita-bull/campaigns?window=30d")
    assert {c["stage"]: c["spend"] for c in d["by_stage"]} == {c["stage"]: c["spend"] for c in full["by_stage"]}
    assert d["total"]["spend"] == pytest.approx(sum(c["spend"] for c in d["by_stage"] if c["stage"] in ("testing", "unclassified")), abs=0.011)
    u = get_ok(client, "/api/products/vita-bull/unmatched?window=30d&stage=scaling")
    ufull = get_ok(client, "/api/products/vita-bull/unmatched?window=30d")
    assert u["total"]["spend"] <= ufull["total"]["spend"]
    sheet_days = sum(r["spend"] for r in fx.ad_daily if r["ad_id"] == "fam:brief2-2-bull-398" and r["day"] >= THROUGH - dt.timedelta(days=29))
    assert sheet_days > 0
    got = get_ok(client, "/api/products/vita-bull/unmatched?window=30d&stage=testing,unclassified")["total"]["spend"]
    assert got == pytest.approx(float(sheet_days), abs=0.006)         # u1 runs in a scaling campaign; only its sheet-grain (unclassified) days remain


def test_detail_daily_chart_honors_stage(client, fx):
    def daily(stage):
        d = get_ok(client, "/api/ads/1000?" + stage)["daily"]
        assert len(d) == 181
        return d
    full = daily("")
    for st, names in (("stage=testing", ("testing",)), ("stage=scaling,unclassified", ("scaling", "unclassified")), ("stage=unclassified", ("unclassified",))):
        got = daily(st)
        for day in got:
            want = sum(v[0] for (f, d, s_), v in fx.family_daily.items() if f == "vb-fam-a" and d.isoformat() == day["day"] and s_ in names)
            assert day["spend"] == pytest.approx(float(want), abs=0.006), (st, day["day"])
    assert sum(d["spend"] for d in full) > sum(d["spend"] for d in daily("stage=testing"))


def test_stage_daily_zeroes_excluded_stages(client):
    full = get_ok(client, "/api/products/vita-bull/stage-daily?days=60")
    cut = get_ok(client, "/api/products/vita-bull/stage-daily?days=60&stage=testing,unclassified")
    assert len(full) == len(cut) == 60
    for a, b in zip(full, cut):
        assert b["scaling"] == 0 and b["testing"] == a["testing"] and b["unclassified"] == a["unclassified"]
    assert sum(r["scaling"] for r in full) > 0


# ------------------------------------------------------------------------------------------------------------------ stage + compare
STAGE_CMP = [pytest.param(q_, a, b, s, so, id=f"{q_}|{s}") for (q_, a, b), (s, so) in itertools.product(
    [PAIRS[0], PAIRS[1], PAIRS[6], PAIRS[7]], [COMBOS[0], COMBOS[1], COMBOS[2], COMBOS[8]])]


@pytest.mark.parametrize("q_,a,b,s,so", STAGE_CMP)
def test_compare_and_stage_together(client, fx, q_, a, b, s, so):
    d = get_ok(client, "/api/products/vita-bull/rollup?" + qs("level=cluster", q_, s))
    check_cmp(d["total"]["m"], rows_by_product(fx, "vita-bull"), fx, a, b, so, f"total {q_} {s}")
    for row in d["rows"]:
        rows = [r for r in rows_by_product(fx, "vita-bull") if F.tag_labels(r)[0] == row["label"]]
        check_cmp(row["m"], rows, fx, a, b, so, f"{row['label']} {q_} {s}")
    assert d["compare_info"]["stage"] == d["window_info"]["stage"] == q.parse_stage({"stage": s.split("=")[1].replace("%20", "")})
    prods = {p["slug"]: p for p in get_ok(client, "/api/products?" + qs(q_, s))}
    check_cmp(prods["vita-bull"]["m"], rows_by_product(fx, "vita-bull"), fx, a, b, so, f"product {q_} {s}")
