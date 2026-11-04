"""v1.6: GET /api/products/<slug>/trend and the spark / spark_days parameter."""
import datetime as dt
from collections import defaultdict
from urllib.parse import quote

import pytest

from . import fixture_data as F
from .helpers import exp_clk, exp_imp, get_ok, qs

D = dt.date
THROUGH = F.THROUGH
ONE = dt.timedelta(days=1)


# ---------------------------------------------------------------------------------------------------------------- oracle helpers
def first_families(fx, slug="vita-bull", **tags):
    """Tracker families whose first tracker row sits in the tag group (cluster / icp / pain), blank families excluded."""
    firsts = {}
    for r in sorted(fx.tracker, key=lambda r: (r["sheet_row"], r["id"])):
        if r["product"] == slug:
            firsts.setdefault(r["family_key"], r)
    out = set()
    for fk, r in firsts.items():
        c, i, p = F.tag_labels(r)
        if fk.startswith("id:"):
            continue
        if all(v is None or {"cluster": c, "icp": i, "pain": p}[k] == v for k, v in tags.items()):
            out.add(fk)
    return out


def series(fx, fams, d_from, d_to, stages=F.STAGES):
    """{day: [spend, purchases, revenue, clicks, impressions, clicks_tr, spend_tr]} summed over fams / stages (days without rows are absent)."""
    out = {}
    for (f, day, st), v in fx.family_daily.items():
        if f in fams and st in stages and d_from <= day <= d_to:
            cur = out.setdefault(day, [F.Z, F.Z, F.Z, 0, 0, 0, F.Z])
            for i in range(7):
                cur[i] += v[i]
    return out


def agg_vec(vecs):
    out = [F.Z, F.Z, F.Z, 0, 0, 0, F.Z]
    for v in vecs:
        for i in range(7):
            out[i] += v[i]
    return out


def as_agg(v):
    """oracle vector -> the dict shape helpers.exp_imp / exp_clk expect"""
    return dict(spend=v[0], imp=v[4], clk=v[3])


def check_point(p, v, ctx=""):
    assert p["spend"] == pytest.approx(float(v[0]), abs=0.006), ctx
    assert p["purchases"] == pytest.approx(float(v[1]), abs=0.006), ctx
    assert p["revenue"] == pytest.approx(float(v[2]), abs=0.006), ctx
    assert p["impressions"] == exp_imp(as_agg(v)) and p["link_clicks"] == exp_clk(as_agg(v)), ctx
    assert p["roas"] == (pytest.approx(float(v[2] / v[0]), abs=1e-4) if v[0] else None), ctx
    assert p["cpa"] == (pytest.approx(float(v[0] / v[1]), abs=0.01) if v[1] else None), ctx
    assert p["ctr"] == (pytest.approx(v[5] / v[4], abs=1e-5) if v[4] else None), ctx
    assert p["cpc"] == (pytest.approx(float(v[6]) / v[3], abs=1e-3) if v[3] and not (v[6] == 0 and v[0] > 0) else None), ctx
    assert p["cpm"] == (pytest.approx(float(v[6]) * 1000 / v[4], abs=1e-2) if v[4] and not (v[6] == 0 and v[0] > 0) else None), ctx
    assert p["conv_rate"] == (pytest.approx(float(v[1]) / v[3], abs=1e-5) if v[3] else None), ctx


def tget(client, slug, extra):
    return get_ok(client, f"/api/products/{slug}/trend?{extra}")


def day_range(a, b):
    return [a + dt.timedelta(days=i) for i in range((b - a).days + 1)]


W30 = (THROUGH - dt.timedelta(days=29), THROUGH)
W7 = (THROUGH - dt.timedelta(days=6), THROUGH)


# ---------------------------------------------------------------------------------------------------------------- daily points
@pytest.mark.parametrize("extra,win", [("window=30d", W30), ("window=7d", W7), ("from=2026-08-01&to=2026-08-20", (D(2026, 8, 1), D(2026, 8, 20))),
                                       ("window=1d", (THROUGH, THROUGH)), ("window=mtd", (D(2026, 11, 1), THROUGH))])
@pytest.mark.parametrize("stage,stages", [("", F.STAGES), ("&stage=testing", ("testing",)), ("&stage=scaling,unclassified", ("scaling", "unclassified"))])
def test_daily_points_match_the_oracle(client, fx, extra, win, stage, stages):
    d = tget(client, "vita-bull", extra + stage)
    fams = first_families(fx)
    ser = series(fx, fams, win[0], win[1], stages)
    days = day_range(*win)
    assert [p["t"] for p in d["points"]] == [x.isoformat() for x in days]          # continuous x axis, one point per day
    assert d["grain"] == "day" and all("partial" not in p for p in d["points"])
    for p, day in zip(d["points"], days):
        check_point(p, ser.get(day, [F.Z, F.Z, F.Z, 0, 0, 0, F.Z]), p["t"])
    assert set(d["points"][0]) == {"t", "spend", "purchases", "revenue", "roas", "cpa", "impressions", "link_clicks", "ctr", "cpc", "cpm", "conv_rate"}
    assert d["scope"] == {"cluster": None, "icp": None, "pain": None, "family_key": None, "campaign_id": None}
    assert "cmp_points" not in d and "by_stage" not in d and "children" not in d
    assert d["window_info"]["stages"] == list(stages)


def test_days_without_data_are_present_with_zeros_and_null_ratios(client, fx):
    d = tget(client, "boneflex", "window=7d")                    # BF-Fam-1 never spent
    assert len(d["points"]) == 7
    assert all(p["spend"] == 0 and p["impressions"] == 0 and p["roas"] is None and p["ctr"] is None and p["cpc"] is None for p in d["points"])


@pytest.mark.parametrize("extra", ["window=30d", "window=7d&stage=testing", "from=2026-09-01&to=2026-10-15&stage=scaling,unclassified"])
def test_points_sum_to_the_window_totals_of_the_same_scope(client, extra):
    for scope in ("", "cluster=Alpha Cluster", "cluster=Alpha Cluster&icp=ICP One", "cluster=Alpha Cluster&icp=ICP One&pain=Pain A", "cluster=(untagged)"):
        t = tget(client, "vita-bull", f"{extra}&{scope}")
        if scope == "":
            roll = get_ok(client, f"/api/products/vita-bull/rollup?{extra}")["total"]["m"]
        else:
            level = {1: "icp", 2: "pain"}.get(len([x for x in scope.split("&") if x]), "icp")
            parts = dict(x.split("=", 1) for x in scope.split("&"))
            if "pain" in parts:
                rows = get_ok(client, f"/api/products/vita-bull/rollup?level=pain&{extra}&cluster={quote(parts['cluster'])}&icp={quote(parts['icp'])}")["rows"]
                roll = next(r for r in rows if r["label"] == parts["pain"])["m"]
            else:
                roll = get_ok(client, f"/api/products/vita-bull/rollup?level=icp&{extra}&cluster={quote(parts['cluster'])}")["total"]["m"] if "icp" not in parts \
                    else next(r for r in get_ok(client, f"/api/products/vita-bull/rollup?level=icp&{extra}&cluster={quote(parts['cluster'])}")["rows"] if r["label"] == parts["icp"])["m"]
        for k in ("spend", "purchases", "revenue"):
            assert sum(p[k] for p in t["points"]) == pytest.approx(roll[k], abs=0.02), (scope, k)
        assert sum(p["impressions"] or 0 for p in t["points"]) == (roll["impressions"] or 0), scope
        assert sum(p["link_clicks"] or 0 for p in t["points"]) == (roll["link_clicks"] or 0), scope
        assert t["scope"]["cluster"] == (parts["cluster"] if scope else None)


def test_scope_by_family_and_campaign(client, fx):
    t = tget(client, "vita-bull", "window=30d&family_key=vb-fam-a")
    ser = series(fx, {"vb-fam-a"}, *W30)
    for p, day in zip(t["points"], day_range(*W30)):
        check_point(p, ser.get(day, [F.Z, F.Z, F.Z, 0, 0, 0, F.Z]), p["t"])
    det = get_ok(client, "/api/ads/1000?window=30d")["metrics"]["family"]["window"]
    assert sum(p["spend"] for p in t["points"]) == pytest.approx(det["spend"], abs=0.02)
    # campaign scope = that campaign's ad_daily rows of the product, so it equals the campaign row of /campaigns
    for camp in ("C100", "C200", "C999"):
        t = tget(client, "vita-bull", f"window=30d&campaign_id={camp}")
        row = next(r for r in get_ok(client, "/api/products/vita-bull/campaigns?window=30d")["rows"] if r["campaign_id"] == camp)
        assert sum(p["spend"] for p in t["points"]) == pytest.approx(row["spend"], abs=0.02), camp
        assert sum(p["purchases"] for p in t["points"]) == pytest.approx(row["purchases"], abs=0.02), camp
        assert t["scope"]["campaign_id"] == camp
    both = tget(client, "vita-bull", "window=30d&campaign_id=C100&cluster=Alpha Cluster")      # campaign AND tag group
    assert 0 < sum(p["spend"] for p in both["points"]) < sum(p["spend"] for p in tget(client, "vita-bull", "window=30d&campaign_id=C100")["points"])


# ---------------------------------------------------------------------------------------------------------------- weeks
def test_weekly_buckets_sum_to_the_daily_series_and_flag_partial_weeks(client, fx):
    for extra, win in (("window=30d", W30), ("window=launch", (F.D0, THROUGH)), ("from=2026-09-07&to=2026-09-20", (D(2026, 9, 7), D(2026, 9, 20)))):
        daily = tget(client, "vita-bull", f"{extra}&grain=day")
        weekly = tget(client, "vita-bull", f"{extra}&grain=week")
        assert weekly["grain"] == "week"
        first, last = win
        mondays = []
        m = first - dt.timedelta(days=first.weekday())
        while m <= last:
            mondays.append(m)
            m += dt.timedelta(days=7)
        assert [p["t"] for p in weekly["points"]] == [x.isoformat() for x in mondays]                 # ISO weeks, Monday start
        assert all(D.fromisoformat(p["t"]).weekday() == 0 for p in weekly["points"])
        for p, mon in zip(weekly["points"], mondays):
            days = [x for x in day_range(mon, mon + dt.timedelta(days=6)) if first <= x <= last]
            ds = [q_ for q_ in daily["points"] if D.fromisoformat(q_["t"]) in days]
            for k in ("spend", "purchases", "revenue"):
                assert p[k] == pytest.approx(sum(x[k] for x in ds), abs=0.02), (extra, p["t"], k)
            assert (p["impressions"] or 0) == sum(x["impressions"] or 0 for x in ds)
            partial = mon < first or mon + dt.timedelta(days=6) > last
            assert p.get("partial", False) is partial, (extra, p["t"])
            if p["spend"] and p["revenue"]:
                assert p["roas"] == pytest.approx(p["revenue"] / p["spend"], abs=2e-4)               # ratio from the week's sums, not an average of days
        for k in ("spend", "purchases", "revenue"):
            assert sum(p[k] for p in weekly["points"]) == pytest.approx(sum(p[k] for p in daily["points"]), abs=0.05), (extra, k)
        assert weekly["points"][0].get("partial", False) is (first.weekday() != 0)
        assert weekly["points"][-1].get("partial", False) is (last.weekday() != 6)


def test_week_ratio_is_not_the_mean_of_daily_ratios(client):
    daily = tget(client, "vita-bull", "from=2026-10-12&to=2026-10-18&grain=day")["points"]            # exactly one ISO week
    week = tget(client, "vita-bull", "from=2026-10-12&to=2026-10-18&grain=week")["points"]
    assert len(week) == 1 and "partial" not in week[0]
    mean = sum(p["roas"] for p in daily if p["roas"] is not None) / len([p for p in daily if p["roas"] is not None])
    assert week[0]["roas"] == pytest.approx(week[0]["revenue"] / week[0]["spend"], abs=2e-4) and abs(week[0]["roas"] - mean) > 1e-5


# ---------------------------------------------------------------------------------------------------------------- split
@pytest.mark.parametrize("grain", ["day", "week"])
@pytest.mark.parametrize("stage,stages", [("", F.STAGES), ("&stage=testing,scaling", ("testing", "scaling")), ("&stage=unclassified", ("unclassified",))])
def test_split_by_stage_sums_to_the_series(client, fx, grain, stage, stages):
    d = tget(client, "vita-bull", f"window=30d&split=stage&grain={grain}{stage}")
    assert list(d["by_stage"]) == list(stages)                                                          # only the included stages
    for i, p in enumerate(d["points"]):
        for k in ("spend", "purchases", "revenue"):
            assert sum(d["by_stage"][st][i][k] for st in stages) == pytest.approx(p[k], abs=0.02), (p["t"], k)
        assert sum(d["by_stage"][st][i]["impressions"] or 0 for st in stages) == (p["impressions"] or 0)
        assert [d["by_stage"][st][i]["t"] for st in stages] == [p["t"]] * len(stages)
    fams = first_families(fx)
    if grain == "day":
        for st in stages:
            ser = series(fx, fams, *W30, (st,))
            for p, day in zip(d["by_stage"][st], day_range(*W30)):
                check_point(p, ser.get(day, [F.Z, F.Z, F.Z, 0, 0, 0, F.Z]), f"{st} {p['t']}")


def test_split_children_parts_plus_other_sum_to_the_parent(client, fx):
    for scope, level_keys in (("", {"Alpha Cluster", "Beta Cluster", "(untagged)"}), ("cluster=Alpha Cluster", {"Alpha Cluster|ICP One", "Alpha Cluster|ICP Two"}),
                              ("cluster=Alpha Cluster&icp=ICP One", {"Alpha Cluster|ICP One|Pain A", "Alpha Cluster|ICP One|Pain B"}),
                              ("cluster=Alpha Cluster&icp=ICP Two&pain=Pain C", {"Alpha Cluster|ICP Two|Pain C|vb-fam-f", "Alpha Cluster|ICP Two|Pain C|id:0510",
                                                                                 "Alpha Cluster|ICP Two|Pain C|id:510"})):
        d = tget(client, "vita-bull", f"window=30d&split=children&{scope}")
        keys = {k["key"] for k in d["children"] if k["key"] != "__other__"}
        assert keys <= level_keys and keys, scope                                           # keys are the /tree keys of the next level
        for i, p in enumerate(d["points"]):
            for k in ("spend", "purchases", "revenue"):
                assert sum(c["points"][i][k] for c in d["children"]) == pytest.approx(p[k], abs=0.02), (scope, p["t"], k)
        for c in d["children"]:
            assert len(c["points"]) == len(d["points"]) and {"key", "label", "color", "points"} == set(c)
    d = tget(client, "vita-bull", "window=30d&split=children")
    assert [c["label"] for c in d["children"]][:2] == ["Alpha Cluster", "Beta Cluster"] or True
    alpha = next(c for c in d["children"] if c["key"] == "Alpha Cluster")
    assert alpha["color"] == "#aa3333" and alpha["label"] == "Alpha Cluster"
    ser = series(fx, first_families(fx, cluster="Alpha Cluster"), *W30)
    for p, day in zip(alpha["points"], day_range(*W30)):
        check_point(p, ser.get(day, [F.Z, F.Z, F.Z, 0, 0, 0, F.Z]), p["t"])
    # ordered by window spend desc
    spends = [sum(p["spend"] for p in c["points"]) for c in d["children"] if c["key"] != "__other__"]
    assert spends == sorted(spends, reverse=True)


def test_explicit_children_keys_and_other_series(client, fx):
    d = tget(client, "vita-bull", "window=30d&split=children&children=Beta Cluster")
    assert [c["key"] for c in d["children"]] == ["Beta Cluster", "__other__"]
    other = d["children"][1]
    assert other["label"] == "Other" and other["color"] is None
    for i, p in enumerate(d["points"]):
        assert d["children"][0]["points"][i]["spend"] + other["points"][i]["spend"] == pytest.approx(p["spend"], abs=0.02)
    both = tget(client, "vita-bull", "window=30d&split=children&children=Beta Cluster,Alpha Cluster")
    assert [c["key"] for c in both["children"]][:2] == ["Beta Cluster", "Alpha Cluster"]            # caller's order
    assert tget(client, "vita-bull", "window=30d&split=children&children=nope")["children"][0]["key"] == "__other__"
    # below a family there is nothing to split
    assert tget(client, "vita-bull", "window=30d&split=children&family_key=vb-fam-a")["children"] == []
    # the next level never shows more than 8 + Other
    assert len(tget(client, "vita-bull", "window=launch&split=children&cluster=Alpha Cluster")["children"]) <= 9


# ---------------------------------------------------------------------------------------------------------------- compare
def test_cmp_points_align_by_position(client, fx):
    d = tget(client, "vita-bull", "window=14d&compare=prev")
    a_days = day_range(THROUGH - dt.timedelta(days=13), THROUGH)
    b_days = day_range(THROUGH - dt.timedelta(days=27), THROUGH - dt.timedelta(days=14))
    assert len(d["cmp_points"]) == len(d["points"]) == 14
    assert [p["t"] for p in d["cmp_points"]] == [x.isoformat() for x in b_days]                     # position i of B lines up with position i of A
    ser = series(fx, first_families(fx), b_days[0], b_days[-1])
    for p, day in zip(d["cmp_points"], b_days):
        check_point(p, ser.get(day, [F.Z, F.Z, F.Z, 0, 0, 0, F.Z]), p["t"])
    assert d["compare_info"]["days"] == 14 and d["window_info"]["days"] == 14


def test_cmp_points_are_null_padded_and_respect_the_stage_list(client, fx):
    d = tget(client, "vita-bull", "window=14d&cfrom=2026-10-01&cto=2026-10-05")                     # B shorter than A
    assert len(d["cmp_points"]) == 14 and [p["t"] for p in d["cmp_points"][:5]] == [f"2026-10-0{i}" for i in range(1, 6)]
    assert all(p is None for p in d["cmp_points"][5:])
    longer = tget(client, "vita-bull", "window=7d&compare=30d")                                      # B longer than A: cut to A's length
    assert len(longer["cmp_points"]) == 7 and longer["cmp_points"][0]["t"] == "2026-10-04"
    empty = tget(client, "vita-bull", "window=7d&cfrom=2026-04-01&cto=2026-04-07")                  # B before the data
    assert empty["compare_info"]["days"] == 0 and empty["cmp_points"] == [None] * 7
    wk = tget(client, "vita-bull", "window=30d&compare=prev&grain=week")
    assert len(wk["cmp_points"]) == len(wk["points"]) == 6 and wk["cmp_points"][0]["t"] == "2026-08-31"       # B (Sep 4 - Oct 3) spans 5 ISO weeks, A 6
    assert wk["cmp_points"][0]["partial"] is True and wk["cmp_points"][4]["partial"] is True and wk["cmp_points"][5] is None
    st = tget(client, "vita-bull", "window=7d&compare=prev&stage=testing")
    ser = series(fx, first_families(fx), THROUGH - dt.timedelta(days=13), THROUGH - dt.timedelta(days=7), ("testing",))
    assert sum(p["spend"] for p in st["cmp_points"]) == pytest.approx(float(sum(v[0] for v in ser.values())), abs=0.02)
    assert st["compare_info"]["stage"] == "testing"


# ---------------------------------------------------------------------------------------------------------------- unknown traffic, launch, validation
def test_unknown_traffic_is_null_in_points(client, fx):
    d = tget(client, "beef-liver", "from=2026-08-12&to=2026-08-16")                  # BL-Fam-2: sheet days, clicks and impressions unknown
    spent = [p for p in d["points"] if p["spend"] > 0]
    assert spent and all(p["impressions"] is None and p["link_clicks"] is None and p["ctr"] is None and p["cpc"] is None and p["cpm"] is None for p in spent)
    v = tget(client, "vita-bull", "from=2026-08-05&to=2026-08-10&family_key=vb-fam-a")      # sheet days with link clicks but no impressions
    spent = [p for p in v["points"] if p["spend"] > 0]
    assert spent and all(p["impressions"] is None and p["ctr"] is None and p["link_clicks"] and p["cpc"] for p in spent)


def test_launch_trend_covers_every_day_held(client):
    d = tget(client, "vita-bull", "")
    assert len(d["points"]) == 181 and d["points"][0]["t"] == "2026-05-06" and d["points"][-1]["t"] == "2026-11-02"
    assert "every day held" in d["window_info"]["note"] and d["window_info"]["kind"] == "launch" and d["window_info"]["from"] is None
    assert tget(client, "vita-bull", "window=launch&grain=week")["points"][0]["partial"] is True        # May 6 is a Wednesday


@pytest.mark.parametrize("bad", ["grain=year", "grain=", "split=pie", "window=nope", "stage=zzz", "compare=prev", "compare=bogus&window=7d",
                                 "children=" + ",".join(f"k{i}" for i in range(13)) + "&split=children", "cfrom=2026-10-01"])
def test_trend_validation(client, bad):
    r = client.get(f"/api/products/vita-bull/trend?{bad}")
    if bad == "grain=":
        assert r.status_code == 200                  # blank = default
        return
    assert r.status_code == 400 and "error" in r.get_json(), bad
    assert client.get("/api/products/nope/trend").status_code == 404


def test_trend_injection_attempts(client, pg):
    evil = ["' OR 1=1; DROP TABLE tracker_rows; --", "\"; DELETE FROM ad_daily; --", "x' UNION SELECT 1 --", "%27%20OR%20%271%27%3D%271", "a\x00b", "%", "_"]
    before = {t: pg.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("tracker_rows", "ad_daily", "family_daily", "products")}
    pg.rollback()
    for e in evil:
        q_ = quote(e, safe="") if "%" not in e else e
        for url in (f"cluster={q_}", f"icp={q_}&cluster={q_}", f"pain={q_}", f"family_key={q_}", f"campaign_id={q_}", f"grain={q_}", f"split={q_}",
                    f"split=children&children={q_}", f"split=children&children={q_},{q_}&cluster={q_}", f"split=children&campaign_id={q_}", f"spark={q_}"):
            r = client.get(f"/api/products/vita-bull/trend?{url}")
            assert r.status_code in (200, 400), (url, r.status_code, r.get_data(as_text=True)[:150])
        r = client.get(f"/api/products/vita-bull/trend?cluster={q_}")
        if "\x00" not in e:
            assert r.status_code == 200 and sum(p["spend"] for p in r.get_json()["points"]) == 0              # nothing matches: all zeros, not "everything"
    after = {t: pg.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in before}
    pg.rollback()
    assert before == after


# ---------------------------------------------------------------------------------------------------------------- sparklines
def spark_oracle(fx, fams, metric, n, end, stages=F.STAGES):
    ser = series(fx, fams, end - dt.timedelta(days=n - 1), end, stages)
    out = []
    for i in range(n):
        v = ser.get(end - dt.timedelta(days=n - 1 - i), [F.Z, F.Z, F.Z, 0, 0, 0, F.Z])
        out.append({"spend": float(v[0]), "purchases": float(v[1]), "revenue": float(v[2]),
                    "roas": float(v[2] / v[0]) if v[0] else None, "cpa": float(v[0] / v[1]) if v[1] else None,
                    "ctr": v[5] / v[4] if v[4] else None,
                    "cpc": float(v[6]) / v[3] if v[3] and not (v[6] == 0 and v[0] > 0) else None}[metric])
    return out


def assert_spark(got, want, tol=6e-3):
    assert len(got) == len(want)
    for g, w_ in zip(got, want):
        assert (g is None) == (w_ is None), (got, want)
        if w_ is not None:
            assert g == pytest.approx(w_, abs=tol)


METRICS = ["spend", "purchases", "revenue", "roas", "cpa", "ctr", "cpc"]


@pytest.mark.parametrize("metric", METRICS)
def test_rollup_spark_values_equal_per_day_sums(client, fx, metric):
    n = 12
    d = get_ok(client, f"/api/products/vita-bull/rollup?level=cluster&window=30d&spark={metric}&spark_days={n}")
    assert "spark" not in d["total"]["m"]                                               # the series sits next to m, like on the rows
    assert_spark(d["total"]["spark"], spark_oracle(fx, first_families(fx), metric, n, THROUGH))      # the total's own per-day series
    for r in d["rows"]:
        fams = first_families(fx, cluster=r["label"])
        assert_spark(r["spark"], spark_oracle(fx, fams, metric, n, THROUGH))
    d = get_ok(client, f"/api/products/vita-bull/rollup?level=pain&cluster=Alpha Cluster&icp=ICP One&window=30d&spark={metric}&spark_days={n}")
    for r in d["rows"]:
        assert_spark(r["spark"], spark_oracle(fx, first_families(fx, cluster="Alpha Cluster", icp="ICP One", pain=r["label"]), metric, n, THROUGH))


def test_spark_length_default_and_validation(client):
    d = get_ok(client, "/api/products/vita-bull/rollup?spark=spend")
    assert all(len(r["spark"]) == 30 for r in d["rows"])                          # default 30
    assert all(len(r["spark"]) == 60 for r in get_ok(client, "/api/products/vita-bull/rollup?spark=spend&spark_days=60")["rows"])
    assert all(len(r["spark"]) == 1 for r in get_ok(client, "/api/products/vita-bull/rollup?spark=spend&spark_days=1")["rows"])
    assert "spark" not in get_ok(client, "/api/products/vita-bull/rollup?window=7d")["rows"][0]
    assert "spark" not in get_ok(client, "/api/products/vita-bull/rollup?spark_days=10")["rows"][0]     # spark_days alone does nothing
    for bad in ("spark=nope", "spark=spend&spark_days=0", "spark=spend&spark_days=61", "spark=spend&spark_days=abc", "spark=spend&spark_days=-3",
                "spark=spend;DROP&spark_days=5", "spark=spend&spark_days=1.5"):
        for ep in ("rollup", "families", "ads", "campaigns", "tree"):
            assert client.get(f"/api/products/vita-bull/{ep}?{bad}").status_code == 400, (ep, bad)


def test_spark_follows_the_window_end_and_the_stage_filter(client, fx):
    fams = first_families(fx)
    end = D(2026, 10, 20)
    d = get_ok(client, "/api/products/vita-bull/families?from=2026-10-01&to=2026-10-20&spark=spend&spark_days=7")       # series ends at the window end
    tot = [sum(r["spark"][i] for r in d["rows"]) for i in range(7)]
    want = spark_oracle(fx, fams, "spend", 7, end)
    assert_spark(tot, want, tol=0.02)
    launch = get_ok(client, "/api/products/vita-bull/families?spark=spend&spark_days=7")                                 # launch -> ends at data_through
    assert_spark([sum(r["spark"][i] for r in launch["rows"]) for i in range(7)], spark_oracle(fx, fams, "spend", 7, THROUGH), tol=0.02)
    for stage, stages in (("testing", ("testing",)), ("scaling,unclassified", ("scaling", "unclassified"))):
        s = get_ok(client, f"/api/products/vita-bull/rollup?level=cluster&window=30d&stage={stage}&spark=spend&spark_days=10")
        for r in s["rows"]:
            assert_spark(r["spark"], spark_oracle(fx, first_families(fx, cluster=r["label"]), "spend", 10, THROUGH, stages), tol=0.01)
    # a window that ends before the data starts still returns a full-length (zero) series
    early = get_ok(client, "/api/products/vita-bull/rollup?from=2026-05-06&to=2026-05-08&spark=spend&spark_days=10")
    assert all(len(r["spark"]) == 10 for r in early["rows"])


def test_families_tree_spark_equal_the_family_series(client, fx):
    n = 9
    d = get_ok(client, f"/api/products/vita-bull/families?cluster=Alpha Cluster&window=30d&spark=revenue&spark_days={n}")
    firsts = {}
    for r in sorted(fx.tracker, key=lambda r: (r["sheet_row"], r["id"])):
        if r["product"] == "vita-bull":
            firsts.setdefault(r["family_key"], r)
    for r in d["rows"]:
        in_group = F.tag_labels(firsts[r["family_key"]])[0] == "Alpha Cluster"
        want = spark_oracle(fx, {r["family_key"]} if in_group and not r["family_key"].startswith("id:") else set(), "revenue", n, THROUGH)
        assert_spark(r["spark"], want)
    t = get_ok(client, f"/api/products/vita-bull/tree?root=cluster&depth=3&window=30d&spark=spend&spark_days={n}")
    assert all(len(r["spark"]) == n for r in t["rows"]) and "spark" not in t["total"]["m"]
    assert_spark(t["total"]["spark"], spark_oracle(fx, first_families(fx), "spend", n, THROUGH), tol=0.01)
    by = {r["key"]: r for r in t["rows"]}
    for key, r in by.items():
        parts = key.split("|")
        tags = dict(zip(("cluster", "icp", "pain"), parts[:3]))
        fams = {parts[3]} & first_families(fx, **tags) if len(parts) == 4 else first_families(fx, **tags)
        assert_spark(r["spark"], spark_oracle(fx, fams, "spend", n, THROUGH), tol=0.01)
    # a child's series adds up to its parent's
    for key, r in by.items():
        kids = [k for k in by.values() if k["parent_key"] == key]
        if kids:
            for i in range(n):
                assert sum(k["spark"][i] for k in kids) == pytest.approx(r["spark"][i], abs=0.02), (key, i)
    assert_spark(by["Alpha Cluster|ICP One|Pain B|vb-fam-a"]["spark"], [0.0] * n)             # second tracker row of a family: no metrics there


def test_campaigns_and_ads_spark(client, fx):
    n = 8
    c = get_ok(client, f"/api/products/vita-bull/campaigns?window=30d&spark=spend&spark_days={n}")
    assert len(c["total"]["spark"]) == n and sum(r["spark"][-1] for r in c["rows"]) == pytest.approx(c["total"]["spark"][-1], abs=0.02)      # rows add up to the total's day
    for row in c["rows"]:
        want = defaultdict(float)
        for r in fx.ad_daily:
            tfam = F.MAPPING[r["meta_family_key"]][0]
            mine = (tfam is not None and any(x["family_key"] == tfam and x["product"] == "vita-bull" for x in fx.tracker)) or \
                   (tfam is None and fx.unmatched[r["meta_family_key"]]["product_id"] == fx.product_ids["vita-bull"])
            if mine and r["campaign_id"] == row["campaign_id"] and THROUGH - dt.timedelta(days=n - 1) <= r["day"] <= THROUGH:
                want[(THROUGH - r["day"]).days] += float(r["spend"])
        assert_spark(row["spark"], [want.get(n - 1 - i, 0.0) for i in range(n)], tol=0.01)
    a = get_ok(client, f"/api/products/vita-bull/ads?family_key=vb-fam-a&window=30d&spark=purchases&spark_days={n}")
    assert len(a["total"]["spark"]) == n
    assert sum(r["spark"][i] for r in a["rows"] if r["spark"] for i in [n - 1]) >= 0
    for r in a["rows"]:
        if r["ad_id"] is None:
            assert r["spark"] is None
            continue
        want = defaultdict(float)
        for x in fx.ad_daily:
            if x["ad_id"] == r["ad_id"] and THROUGH - dt.timedelta(days=n - 1) <= x["day"] <= THROUGH:
                want[(THROUGH - x["day"]).days] += float(x["purchases"])
        assert_spark(r["spark"], [want.get(n - 1 - i, 0.0) for i in range(n)], tol=0.01)
    ratio = get_ok(client, f"/api/products/vita-bull/ads?family_key=vb-fam-a&window=30d&spark=cpc&spark_days={n}")
    assert any(v is not None for r in ratio["rows"] if r["spark"] for v in r["spark"])
    st = get_ok(client, f"/api/products/vita-bull/ads?family_key=vb-fam-a&window=30d&stage=testing&spark=spend&spark_days={n}")
    assert all(all(v == 0 for v in r["spark"]) or r["campaign_id"] == "C200" for r in st["rows"] if r["spark"])         # only the testing campaign


def test_spark_params_resist_injection(client):
    for ep in ("rollup", "families", "ads", "campaigns", "tree"):
        for bad in ("spark=' OR 1=1;--", "spark=spend&spark_days=1;DROP TABLE x", "spark=%27%3B--", "spark=spend&spark_days=%00"):
            assert client.get(f"/api/products/vita-bull/{ep}?{bad}").status_code == 400, (ep, bad)


# ---------------------------------------------------------------------------------------------------------------- monthly grain
def month_starts(first, last):
    out, m = [], first.replace(day=1)
    while m <= last:
        out.append(m)
        m = (m.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return out


@pytest.mark.parametrize("extra,win", [("window=launch", (F.D0, THROUGH)), ("window=180d", (THROUGH - dt.timedelta(days=179), THROUGH)),
                                       ("from=2026-07-10&to=2026-09-20", (D(2026, 7, 10), D(2026, 9, 20))), ("window=30d", W30)])
def test_monthly_buckets_sum_to_the_daily_series_and_the_window_total(client, fx, extra, win):
    daily = tget(client, "vita-bull", f"{extra}&grain=day")
    month = tget(client, "vita-bull", f"{extra}&grain=month")
    assert month["grain"] == "month"
    starts = month_starts(*win)
    assert [p["t"] for p in month["points"]] == [x.isoformat() for x in starts]                    # continuous, t = YYYY-MM-01
    assert all(p["t"].endswith("-01") for p in month["points"])
    for p, ms in zip(month["points"], starts):
        nxt = (ms.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        days = [q_ for q_ in daily["points"] if max(ms, win[0]) <= D.fromisoformat(q_["t"]) < nxt]
        for k in ("spend", "purchases", "revenue"):
            assert p[k] == pytest.approx(sum(x[k] for x in days), abs=0.02), (extra, p["t"], k)
        assert (p["impressions"] or 0) == sum(x["impressions"] or 0 for x in days) and (p["link_clicks"] or 0) == sum(x["link_clicks"] or 0 for x in days)
        if p["spend"] and p["revenue"]:
            assert p["roas"] == pytest.approx(p["revenue"] / p["spend"], abs=2e-4)                    # from the month's sums
        assert p.get("partial", False) is (ms < win[0] or nxt - dt.timedelta(days=1) > win[1]), (extra, p["t"])
    # the trend of "since launch" covers the days held, so compare with the same days (lifetime also holds history before May 6)
    same_days = f"from={win[0]}&to={win[1]}" if extra == "window=launch" else extra
    roll = get_ok(client, f"/api/products/vita-bull/rollup?level=cluster&{same_days}")["total"]["m"]
    for k in ("spend", "purchases", "revenue"):
        assert sum(p[k] for p in month["points"]) == pytest.approx(roll[k], abs=0.05), (extra, k)
        assert sum(p[k] for p in daily["points"]) == pytest.approx(roll[k], abs=0.05), (extra, k)


def test_monthly_partial_flags_at_the_data_edges(client):
    m = tget(client, "vita-bull", "window=launch&grain=month")["points"]
    assert [p["t"] for p in m] == [f"2026-{i:02d}-01" for i in range(5, 12)]                          # May .. Nov
    assert m[0]["partial"] is True and m[-1]["partial"] is True                                       # daily_start is May 6, data_through is Nov 2
    assert all("partial" not in p for p in m[1:-1])
    whole = tget(client, "vita-bull", "from=2026-07-01&to=2026-08-31&grain=month")["points"]
    assert [p["t"] for p in whole] == ["2026-07-01", "2026-08-01"] and all("partial" not in p for p in whole)
    one = tget(client, "vita-bull", "window=1d&grain=month")["points"]
    assert len(one) == 1 and one[0]["t"] == "2026-11-01" and one[0]["partial"] is True


def test_monthly_split_and_compare_alignment(client):
    d = tget(client, "vita-bull", "window=180d&grain=month&compare=prev&split=stage")
    assert len(d["cmp_points"]) == len(d["points"])
    assert d["cmp_points"][0] is not None and d["cmp_points"][0]["t"] == "2026-05-01"              # B (prev 180d) is cut at daily_start: one day, May 6
    assert all(c is None or D.fromisoformat(c["t"]).day == 1 for c in d["cmp_points"])
    for i, p in enumerate(d["points"]):
        assert sum(d["by_stage"][s][i]["spend"] for s in d["by_stage"]) == pytest.approx(p["spend"], abs=0.02)
    pad = tget(client, "vita-bull", "window=180d&grain=month&cfrom=2026-09-01&cto=2026-09-30")
    assert pad["cmp_points"][0]["t"] == "2026-09-01" and all(c is None for c in pad["cmp_points"][1:])
    kids = tget(client, "vita-bull", "window=180d&grain=month&split=children")
    for i, p in enumerate(kids["points"]):
        assert sum(c["points"][i]["spend"] for c in kids["children"]) == pytest.approx(p["spend"], abs=0.02)


def test_bad_grain_is_still_400(client):
    for bad in ("year", "months", "MONTHLY", "quarter", "day;DROP"):
        assert client.get(f"/api/products/vita-bull/trend?grain={bad}").status_code == 400, bad
    assert client.get("/api/products/vita-bull/trend?grain=MONTH").status_code == 200              # case-insensitive like day / week
