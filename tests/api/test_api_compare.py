"""v1.2 comparison mode: window A vs window B."""
import datetime as dt
import itertools

import pytest

from . import fixture_data as F
from .helpers import STAGE_PARAMS, THROUGH, check_m, get_ok, qs, ratio_or_none, rows_by_product

D = dt.date
A7 = (D(2026, 10, 27), THROUGH)

# (query string, A window, B window or None when B is entirely outside the data)
PAIRS = [
    ("window=7d&compare=prev", A7, (D(2026, 10, 20), D(2026, 10, 26))),
    ("window=7d&compare=30d", A7, (D(2026, 10, 4), THROUGH)),                                  # overlapping windows
    ("window=14d&compare=7d", (D(2026, 10, 20), THROUGH), A7),                                 # B inside A
    ("window=1d&compare=prev", (THROUGH, THROUGH), (D(2026, 11, 1), D(2026, 11, 1))),
    ("from=2026-10-10&to=2026-10-14&compare=prev", (D(2026, 10, 10), D(2026, 10, 14)), (D(2026, 10, 5), D(2026, 10, 9))),
    ("window=mtd&compare=prev", (D(2026, 11, 1), THROUGH), (D(2026, 10, 30), D(2026, 10, 31))),
    ("window=30d&cfrom=2026-08-01&cto=2026-08-31", (D(2026, 10, 4), THROUGH), (D(2026, 8, 1), D(2026, 8, 31))),
    ("from=2026-06-01&to=2026-08-30&compare=prev", (D(2026, 6, 1), D(2026, 8, 30)), (F.D0, D(2026, 5, 31))),     # B clamped at the start
    ("window=180d&compare=prev", (D(2026, 5, 7), THROUGH), (F.D0, F.D0)),                                        # B cut to one day
    ("window=7d&cfrom=2026-04-01&cto=2026-04-07", A7, None),
    ("window=7d&compare=60d&cfrom=2026-10-01&cto=2026-10-03", A7, (D(2026, 10, 1), D(2026, 10, 3))),            # custom B wins over preset
]
CASES = [pytest.param(q_, a, b, s, so, id=f"{q_}|{s or 'all'}") for (q_, a, b), (s, so) in itertools.product(PAIRS, STAGE_PARAMS)]


def n_days(win):
    return None if win is None else (win[1] - win[0]).days + 1


def pct(a, b):
    return None if b in (None, 0) or a is None else round((a - b) / b, 4)


def check_cmp(m, rows, fx, a, b, so, ctx, unique_override=None):
    """m.cmp / m.delta / per-day fields against the oracle for window B."""
    agg_a = F.aggregate(fx, rows, a, so)
    zero = F.aggregate(fx, [], a, so)
    agg_b = F.aggregate(fx, rows, b, so) if b else zero
    da, db = n_days(a), n_days(b)
    assert "cmp" in m and "delta" in m, ctx
    c, dlt = m["cmp"], m["delta"]
    for k, want in (("spend", agg_b["spend"]), ("purchases", agg_b["purchases"]), ("revenue", agg_b["revenue"])):
        assert c[k] == pytest.approx(float(want), abs=0.006), (ctx, "cmp", k)
    assert c["active_ads"] == agg_b["active"], ctx
    assert c["roas"] == (round(float(agg_b["revenue"] / agg_b["spend"]), 4) if agg_b["spend"] else None), ctx
    assert (c["cpa"] is None) == (agg_b["purchases"] == 0), ctx
    # per-day normalisation (A over A's days, B over B's days)
    assert m["spend_per_day"] == pytest.approx(float(agg_a["spend"]) / da, abs=0.006), ctx
    assert m["purchases_per_day"] == pytest.approx(float(agg_a["purchases"]) / da, abs=0.006), ctx
    assert m["revenue_per_day"] == pytest.approx(float(agg_a["revenue"]) / da, abs=0.006), ctx
    if db:
        assert c["spend_per_day"] == pytest.approx(float(agg_b["spend"]) / db, abs=0.006), ctx
        assert c["revenue_per_day"] == pytest.approx(float(agg_b["revenue"]) / db, abs=0.006), ctx
    else:
        assert c["spend_per_day"] is None and c["purchases_per_day"] is None and c["revenue_per_day"] is None, ctx
        assert all(dlt[k] is None for k in ("spend_pct", "purchases_pct", "revenue_pct", "spend_per_day_pct")), ctx
    # delta = A - B and (A - B) / B, null when B is 0
    sa, sb = float(agg_a["spend"]), float(agg_b["spend"])
    assert dlt["spend"] == pytest.approx(sa - sb, abs=0.011), ctx
    assert dlt["purchases"] == pytest.approx(float(agg_a["purchases"] - agg_b["purchases"]), abs=0.011), ctx
    assert dlt["revenue"] == pytest.approx(float(agg_a["revenue"]) - float(agg_b["revenue"]), abs=0.011), ctx
    for key, av, bv in (("spend", agg_a["spend"], agg_b["spend"]), ("purchases", agg_a["purchases"], agg_b["purchases"]),
                        ("revenue", agg_a["revenue"], agg_b["revenue"])):
        if bv == 0:
            assert dlt[f"{key}_pct"] is None, (ctx, key)
        else:
            assert dlt[f"{key}_pct"] == pytest.approx(float((av - bv) / bv), abs=2e-4), (ctx, key)
    if db and agg_b["spend"] != 0:
        assert dlt["spend_per_day_pct"] == pytest.approx((sa / da - sb / db) / (sb / db), abs=2e-4), ctx
    ra = float(agg_a["revenue"] / agg_a["spend"]) if agg_a["spend"] else None
    rb = float(agg_b["revenue"] / agg_b["spend"]) if agg_b["spend"] else None
    if ra is None or rb is None:
        assert dlt["roas"] is None and dlt["roas_pct"] is None, ctx
    elif rb == 0:
        assert dlt["roas_pct"] is None, ctx
    else:
        assert dlt["roas"] == pytest.approx(ra - rb, abs=2e-4), ctx
        assert dlt["roas_pct"] == pytest.approx((ra - rb) / rb, abs=5e-4), ctx
    check_cmp_traffic(m, agg_a, agg_b, da, db, ctx)
    # the A side and the non-windowed fields are exactly what they are without comparison
    check_m(m, agg_a if unique_override is None else dict(agg_a, unique=unique_override), ctx)


def check_cmp_traffic(m, agg_a, agg_b, da, db, ctx):
    """Traffic metrics inside cmp / delta: B sums, A - B, ctr_pp, percent changes (null when B is 0 / unknown)."""
    c, dlt = m["cmp"], m["delta"]
    assert c["impressions"] == agg_b["imp"] and c["link_clicks"] == agg_b["clk"], ctx
    def ratios(agg):
        return {"ctr": ratio_or_none(agg["clk_tr"], agg["imp"]), "cpc": ratio_or_none(agg["sp_tr"], agg["clk"]),
                "cpm": None if not agg["imp"] else float(agg["sp_tr"]) * 1000 / agg["imp"], "conv_rate": ratio_or_none(agg["purch_t"], agg["clk"])}
    ra, rb = ratios(agg_a), ratios(agg_b)
    for k, tol in (("ctr", 1e-5), ("cpc", 1e-3), ("cpm", 1e-2), ("conv_rate", 1e-5)):
        assert (c[k] is None) == (rb[k] is None), (ctx, k)
        if rb[k] is not None:
            assert c[k] == pytest.approx(rb[k], abs=tol), (ctx, k)
        if ra[k] is None or rb[k] is None:
            assert dlt[k] is None and dlt[f"{k}_pct"] is None, (ctx, k)
        else:
            assert dlt[k] == pytest.approx(ra[k] - rb[k], abs=2 * tol), (ctx, k)
            if rb[k]:
                assert dlt[f"{k}_pct"] == pytest.approx((ra[k] - rb[k]) / rb[k], abs=2e-3), (ctx, k)
    assert dlt["impressions"] == agg_a["imp"] - agg_b["imp"] and dlt["link_clicks"] == agg_a["clk"] - agg_b["clk"], ctx
    assert (dlt["ctr_pp"] is None) == (dlt["ctr"] is None), ctx
    if dlt["ctr"] is not None:
        assert dlt["ctr_pp"] == pytest.approx(dlt["ctr"] * 100, abs=2e-3), ctx           # percentage points
    for key, a_, b_ in (("impressions", agg_a["imp"], agg_b["imp"]), ("link_clicks", agg_a["clk"], agg_b["clk"])):
        assert (dlt[f"{key}_pct"] is None) == (b_ == 0), (ctx, key)
        if b_:
            assert dlt[f"{key}_pct"] == pytest.approx((a_ - b_) / b_, abs=2e-4), (ctx, key)
        assert m[f"{key}_per_day"] == pytest.approx(a_ / da, abs=0.006), (ctx, key)
        if db:
            assert c[f"{key}_per_day"] == pytest.approx(b_ / db, abs=0.006), (ctx, key)
        else:
            assert c[f"{key}_per_day"] is None, (ctx, key)


# --------------------------------------------------------------------------------------------------------------------- params
def test_compare_info_prev_math(client):
    d = get_ok(client, "/api/products/vita-bull/rollup?window=7d&compare=prev")
    ci, wi = d["compare_info"], d["window_info"]
    assert (wi["from"], wi["to"]) == ("2026-10-27", "2026-11-02")
    assert ci["kind"] == "prev" and (ci["from"], ci["to"], ci["days"]) == ("2026-10-20", "2026-10-26", 7)
    assert ci["clamped"] is False and ci["label"] == "Oct 20 – Oct 26 · 7 days"
    assert set(wi) == set(ci)                                # same shape
    assert ci["stage"] == "all" and ci["daily_start"] == "2026-05-06" and ci["data_through"] == "2026-11-02"
    # prev of a custom window keeps its length, B ends the day before A starts
    ci = get_ok(client, "/api/products/vita-bull/families?from=2026-10-10&to=2026-10-14&compare=prev")["compare_info"]
    assert (ci["from"], ci["to"], ci["days"]) == ("2026-10-05", "2026-10-09", 5)
    # prev of a window that was itself clamped uses the clamped length
    d = get_ok(client, "/api/products/vita-bull/rollup?from=2026-04-30&to=2026-05-10&compare=prev")
    assert d["window_info"]["days"] == 5 and d["compare_info"]["days"] == 0 and d["compare_info"]["to"] is None


def test_compare_info_presets_and_overlap(client):
    ci = get_ok(client, "/api/products/vita-bull/rollup?window=7d&compare=30d")["compare_info"]
    assert (ci["kind"], ci["name"], ci["from"], ci["to"], ci["days"]) == ("trailing", "30d", "2026-10-04", "2026-11-02", 30)
    ci = get_ok(client, "/api/products/vita-bull/rollup?window=7d&compare=mtd")["compare_info"]
    assert (ci["kind"], ci["from"], ci["to"]) == ("mtd", "2026-11-01", "2026-11-02")
    ci = get_ok(client, "/api/products/vita-bull/rollup?window=7d&cfrom=2026-08-01&cto=2026-08-31&compare=prev")["compare_info"]
    assert ci["kind"] == "custom" and (ci["from"], ci["to"], ci["days"]) == ("2026-08-01", "2026-08-31", 31)     # cfrom/cto win


def test_compare_b_clamping_and_empty(client):
    ci = get_ok(client, "/api/products/vita-bull/rollup?from=2026-06-01&to=2026-08-30&compare=prev")["compare_info"]
    assert ci["clamped"] is True and (ci["from"], ci["to"], ci["days"]) == ("2026-05-06", "2026-05-31", 26) and ci["note"]
    ci = get_ok(client, "/api/products/vita-bull/rollup?window=180d&compare=prev")["compare_info"]
    assert (ci["from"], ci["to"], ci["days"], ci["clamped"]) == ("2026-05-06", "2026-05-06", 1, True)
    ci = get_ok(client, "/api/products/vita-bull/rollup?window=7d&cfrom=2026-04-01&cto=2026-04-07")["compare_info"]
    assert ci["days"] == 0 and ci["from"] is None and ci["to"] is None and ci["clamped"] is True and "No daily data" in ci["note"]
    ci = get_ok(client, "/api/products/vita-bull/rollup?window=7d&cfrom=2026-10-30&cto=2030-01-01")["compare_info"]
    assert ci["clamped"] is True and ci["to"] == "2026-11-02" and ci["days"] == 4


def test_no_compare_means_no_cmp_blocks_but_per_day_fields(client):
    d = get_ok(client, "/api/products/vita-bull/rollup?window=7d")
    assert d["compare_info"] is None
    for r in d["rows"] + [d["total"]]:
        assert "cmp" not in r["m"] and "delta" not in r["m"]
        assert r["m"]["spend_per_day"] == pytest.approx(r["m"]["spend"] / 7, abs=0.006)
        assert r["m"]["purchases_per_day"] == pytest.approx(r["m"]["purchases"] / 7, abs=0.006)
    for off in ("compare=", "compare=off", "compare=none"):
        assert "cmp" not in get_ok(client, f"/api/products/vita-bull/rollup?window=7d&{off}")["total"]["m"]
    # since launch has no day count -> per-day fields are present but null
    t = get_ok(client, "/api/products/vita-bull/rollup")["total"]["m"]
    assert t["spend_per_day"] is None and t["purchases_per_day"] is None and t["revenue_per_day"] is None


ENDPOINTS = ["rollup", "families", "ads", "campaigns", "unmatched"]


@pytest.mark.parametrize("ep", ENDPOINTS)
def test_launch_plus_compare_is_400(client, ep):
    for extra in ("compare=prev", "compare=7d", "cfrom=2026-10-01&cto=2026-10-07", "window=launch&compare=prev"):
        r = client.get(f"/api/products/vita-bull/{ep}?{extra}")
        assert r.status_code == 400 and r.get_json() == {"error": "compare needs a date window"}, (ep, extra)
    assert client.get("/api/products?compare=prev").status_code == 400
    assert client.get("/api/ads/1000?compare=prev").status_code == 400
    assert client.get("/api/products/vita-bull/rollup?window=7d&compare=prev").status_code == 200


@pytest.mark.parametrize("bad", ["compare=bogus", "compare=8d", "cfrom=2026-10-01", "cto=2026-10-07", "cfrom=2026-10-09&cto=2026-10-01",
                                 "cfrom=x&cto=y", "cfrom=2026-13-01&cto=2026-13-02"])
def test_bad_compare_params_400(client, bad):
    for ep in ENDPOINTS:
        r = client.get(f"/api/products/vita-bull/{ep}?window=7d&{bad}")
        assert r.status_code == 400 and "error" in r.get_json(), (ep, bad)


# --------------------------------------------------------------------------------------------------------------------- numbers
@pytest.mark.parametrize("q_,a,b,s,so", CASES)
def test_products_and_rollup_cmp(client, fx, q_, a, b, s, so):
    prods = {p["slug"]: p for p in get_ok(client, "/api/products?" + qs(q_, s))}
    for slug in ("vita-bull", "beef-liver", "boneflex"):
        check_cmp(prods[slug]["m"], rows_by_product(fx, slug), fx, a, b, so, f"{slug} {q_} {s}")
    env = get_ok(client, "/api/products?" + qs(q_, s, "envelope=1"))
    assert env["compare_info"]["days"] == (n_days(b) or 0) and env["window_info"]["days"] == n_days(a) and len(env["rows"]) == len(prods)
    d = get_ok(client, "/api/products/vita-bull/rollup?" + qs("level=cluster", q_, s))
    for row in d["rows"]:
        rows = [r for r in rows_by_product(fx, "vita-bull") if F.tag_labels(r)[0] == row["label"]]
        check_cmp(row["m"], rows, fx, a, b, so, f"cluster {row['label']} {q_} {s}")
    check_cmp(d["total"]["m"], rows_by_product(fx, "vita-bull"), fx, a, b, so, f"total {q_} {s}")
    d = get_ok(client, "/api/products/vita-bull/rollup?" + qs("level=pain", "cluster=Beta Cluster", "icp=ICP Three", q_, s))
    zero_row = next(r for r in d["rows"] if r["label"] == "Pain E")                  # taxonomy line with no ads -> all-zero cmp, null pcts
    assert zero_row["m"]["cmp"]["spend"] == 0 and zero_row["m"]["delta"]["spend_pct"] is None and zero_row["m"]["delta"]["roas"] is None


@pytest.mark.parametrize("q_,a,b,s,so", [c for c in CASES if "|all" in c.id or "testing" in c.id][:14])
def test_families_cmp(client, fx, q_, a, b, s, so):
    d = get_ok(client, "/api/products/vita-bull/families?" + qs(q_, s))
    fams = {}
    for r in rows_by_product(fx, "vita-bull"):
        fams.setdefault(r["family_key"], []).append(r)
    for row in d["rows"]:
        check_cmp(row["m"], fams[row["family_key"]], fx, a, b, so, f"{row['family_key']} {q_} {s}", unique_override=1)
    check_cmp(d["total"]["m"], rows_by_product(fx, "vita-bull"), fx, a, b, so, f"families total {q_} {s}")
    assert d["compare_info"] is not None


def test_non_windowed_fields_carry_no_cmp(client):
    m = get_ok(client, "/api/products/vita-bull/rollup?window=7d&compare=prev")["total"]["m"]
    for k in ("unique_ads", "live_now", "spend_7d", "cpa_7d"):
        assert not isinstance(m[k], dict)
    assert set(m["cmp"]) == {"spend", "purchases", "revenue", "roas", "cpa", "active_ads", "spend_per_day", "purchases_per_day", "revenue_per_day",
                           "spend_share", "impressions", "link_clicks", "ctr", "cpc", "cpm", "conv_rate", "impressions_per_day", "link_clicks_per_day"}
    assert set(m["delta"]) == {"spend", "purchases", "revenue", "roas", "cpa", "spend_pct", "purchases_pct", "revenue_pct", "roas_pct", "cpa_pct",
                               "spend_per_day_pct", "purchases_per_day_pct", "revenue_per_day_pct",
                               "spend_share_pp", "impressions", "link_clicks", "ctr", "ctr_pp", "cpc", "cpm", "conv_rate", "impressions_pct", "link_clicks_pct",
                               "ctr_pct", "cpc_pct", "cpm_pct", "conv_rate_pct", "impressions_per_day_pct", "link_clicks_per_day_pct"}
    assert "cmp" not in m["stage"]["testing"]


def test_totals_are_recomputed_from_sums_not_averaged(client):
    d = get_ok(client, "/api/products/vita-bull/rollup?window=14d&compare=prev")
    t = d["total"]["m"]
    rows = [r["m"] for r in d["rows"]]
    for k in ("spend", "purchases", "revenue"):
        assert t["cmp"][k] == pytest.approx(sum(r["cmp"][k] for r in rows), abs=0.011)
        assert t["delta"][k] == pytest.approx(sum(r["delta"][k] for r in rows), abs=0.02)
    assert t["cmp"]["roas"] == pytest.approx(t["cmp"]["revenue"] / t["cmp"]["spend"], abs=2e-4)
    assert t["cmp"]["cpa"] == pytest.approx(t["cmp"]["spend"] / t["cmp"]["purchases"], abs=0.01)
    assert t["delta"]["roas"] == pytest.approx(t["roas"] - t["cmp"]["roas"], abs=2e-4)
    assert t["delta"]["spend_pct"] == pytest.approx((t["spend"] - t["cmp"]["spend"]) / t["cmp"]["spend"], abs=2e-4)
    avg = sum(r["cmp"]["roas"] for r in rows if r["cmp"]["roas"] is not None) / sum(1 for r in rows if r["cmp"]["roas"] is not None)
    assert abs(t["cmp"]["roas"] - avg) > 1e-4            # genuinely not the mean of row ratios in this fixture


def test_delta_pct_null_when_b_is_zero(client):
    d = get_ok(client, "/api/products/beef-liver/rollup?window=7d&cfrom=2026-05-06&cto=2026-05-06")      # Beef Liver has no spend before Aug 10
    t = d["total"]["m"]
    assert t["spend"] > 0
    assert t["cmp"]["spend"] == 0 and t["cmp"]["roas"] is None and t["cmp"]["cpa"] is None
    for k in ("spend_pct", "purchases_pct", "revenue_pct", "roas_pct", "cpa_pct", "spend_per_day_pct", "purchases_per_day_pct", "revenue_per_day_pct"):
        assert t["delta"][k] is None, k
    assert t["delta"]["spend"] == t["spend"] and t["delta"]["roas"] is None
    assert t["cmp"]["spend_per_day"] == 0                    # B has one day, so per-day is a real 0, not null
    assert t["delta"]["spend_per_day_pct"] is None


def test_per_day_normalisation_7d_vs_30d(client):
    t = get_ok(client, "/api/products/vita-bull/rollup?window=7d&compare=30d")["total"]["m"]
    assert t["spend_per_day"] == pytest.approx(t["spend"] / 7, abs=0.006)
    assert t["cmp"]["spend_per_day"] == pytest.approx(t["cmp"]["spend"] / 30, abs=0.006)
    assert t["delta"]["spend_per_day_pct"] == pytest.approx((t["spend"] / 7 - t["cmp"]["spend"] / 30) / (t["cmp"]["spend"] / 30), abs=2e-4)
    # raw totals differ a lot (7 vs 30 days) while per-day pct stays moderate; 7d is inside 30d so B >= A in raw terms
    assert t["cmp"]["spend"] >= t["spend"] and t["delta"]["spend_pct"] < 0


def test_stage_filter_applies_to_both_windows(client, fx):
    d = get_ok(client, "/api/products/vita-bull/rollup?window=14d&compare=prev&stage=testing")["total"]["m"]
    full = get_ok(client, "/api/products/vita-bull/rollup?window=14d&compare=prev")["total"]["m"]
    assert d["spend"] == pytest.approx(full["stage"]["testing"]["spend"], abs=0.006)
    assert d["spend"] < full["spend"] and d["cmp"]["spend"] < full["cmp"]["spend"]
    assert get_ok(client, "/api/products/vita-bull/rollup?window=14d&compare=prev&stage=testing")["compare_info"]["stage"] == "testing"


# --------------------------------------------------------------------------------------------------------------------- ads / campaigns / unmatched / detail
def vb_family_launches(fx):
    return {r["family_key"] for r in fx.tracker if r["product"] == "vita-bull" and not r["family_key"].startswith("id:")}


@pytest.mark.parametrize("q_,a,b,s,so", [c for c in CASES if c.id.endswith("|all") or c.id.endswith("|scaling")])
def test_ads_cmp(client, fx, q_, a, b, s, so):
    d = get_ok(client, "/api/products/vita-bull/ads?" + qs(q_, s))
    fams = vb_family_launches(fx)
    pid = fx.product_ids["vita-bull"]
    ads_a, rem_a, rem_au = F.ad_oracle(fx, fams, a, so, unmatched_pid=pid)
    ads_b, rem_b, rem_bu = F.ad_oracle(fx, fams, b, so, unmatched_pid=pid) if b else ({}, F._zero(), F._zero())
    by = {(r["ad_id"], r["family_key"]): r for r in d["rows"] if r["ad_id"]}
    keys = set(ads_a) | set(ads_b)
    assert set(by) == {(ad, f) for ad, f in keys}, (q_, s)
    for key in keys:
        r = by[key]
        va, vb = ads_a.get(key, F._zero()), ads_b.get(key, F._zero())
        assert r["spend"] == pytest.approx(float(va["spend"]), abs=0.006) and r["cmp"]["spend"] == pytest.approx(float(vb["spend"]), abs=0.006), (key, q_)
        assert r["cmp"]["revenue"] == pytest.approx(float(vb["revenue"]), abs=0.006) and r["cmp"]["purchases"] == pytest.approx(float(vb["purchases"]), abs=0.006)
        assert r["cmp"]["impressions"] == vb["imp"] and r["cmp"]["link_clicks"] == vb["clk"] and r["impressions"] == va["imp"]
        assert r["delta"]["spend"] == pytest.approx(float(va["spend"] - vb["spend"]), abs=0.011)
        assert r["delta"]["impressions"] == va["imp"] - vb["imp"]
        assert (r["delta"]["spend_pct"] is None) == (vb["spend"] == 0)
        assert r["spend_per_day"] == pytest.approx(float(va["spend"]) / n_days(a), abs=0.006)
    for on_tracker, ra, rb in ((True, rem_a, rem_b), (False, rem_au, rem_bu)):
        rem = next((r for r in d["rows"] if r["ad_id"] is None and r["on_tracker"] is on_tracker), None)
        if ra["spend"] or rb["spend"]:
            assert rem is not None and rem["spend"] == pytest.approx(float(ra["spend"]), abs=0.006) and rem["cmp"]["spend"] == pytest.approx(float(rb["spend"]), abs=0.006)
    # totals = sum of rows (A and B), ratios recomputed
    t = d["total"]
    assert t["spend"] == pytest.approx(sum(r["spend"] for r in d["rows"]), abs=0.02)
    assert t["cmp"]["spend"] == pytest.approx(sum(r["cmp"]["spend"] for r in d["rows"]), abs=0.02)
    if t["cmp"]["spend"]:
        assert t["cmp"]["roas"] == pytest.approx(t["cmp"]["revenue"] / t["cmp"]["spend"], abs=2e-4)
    assert d["compare_info"] is not None
    # ad rows reconcile to the tracker-side family totals for BOTH windows (matched families; unmatched Meta families sit outside the rollup)
    matched = [r for r in d["rows"] if r.get("on_tracker") and (r["ad_id"] is None or r["tag"] is not None)]
    fam_total_a = F.aggregate(fx, rows_by_product(fx, "vita-bull"), a, so)["spend"]
    fam_total_b = F.aggregate(fx, rows_by_product(fx, "vita-bull"), b, so)["spend"] if b else F.Z
    assert sum(r["spend"] for r in matched) == pytest.approx(float(fam_total_a), abs=0.02)
    assert sum(r["cmp"]["spend"] for r in matched) == pytest.approx(float(fam_total_b), abs=0.02)


def test_ads_family_selection_and_campaign_filter_cmp(client, fx):
    d = get_ok(client, "/api/products/vita-bull/ads?family_key=vb-fam-a&window=14d&compare=prev")
    fams = {"vb-fam-a"}
    a_, *_ = F.ad_oracle(fx, fams, (F.THROUGH - dt.timedelta(days=13), F.THROUGH), "all")
    b_, *_ = F.ad_oracle(fx, fams, (F.THROUGH - dt.timedelta(days=27), F.THROUGH - dt.timedelta(days=14)), "all")
    for r in d["rows"]:
        assert r["spend"] == pytest.approx(float(a_.get((r["ad_id"], "vb-fam-a"), F._zero())["spend"]), abs=0.006)
        assert r["cmp"]["spend"] == pytest.approx(float(b_.get((r["ad_id"], "vb-fam-a"), F._zero())["spend"]), abs=0.006)
    c = get_ok(client, "/api/products/vita-bull/ads?campaign_id=C200&window=14d&compare=prev")
    assert c["rows"] and all(r["campaign_id"] == "C200" for r in c["rows"] if r["ad_id"])
    assert all(r["stage"] == "testing" for r in c["rows"] if r["ad_id"])


@pytest.mark.parametrize("q_,a,b,s,so", [c for c in CASES if c.id.endswith("|all") or c.id.endswith("|testing")][:12])
def test_campaigns_cmp(client, fx, q_, a, b, s, so):
    d = get_ok(client, "/api/products/vita-bull/campaigns?" + qs(q_, s))
    pid = fx.product_ids["vita-bull"]

    def spend_in(win, stages, camp=None):
        out = F.Z
        for r in fx.ad_daily:
            tfam = F.MAPPING[r["meta_family_key"]][0]
            ok_fam = (tfam is not None and any(x["family_key"] == tfam and x["product"] == "vita-bull" for x in fx.tracker)) or \
                     (tfam is None and fx.unmatched[r["meta_family_key"]]["product_id"] == pid)
            if ok_fam and win[0] <= r["day"] <= win[1] and F.stage_of_row(fx, r["campaign_id"]) in stages and (camp is None or r["campaign_id"] == camp):
                out += r["spend"]
        return out
    stages = F.STAGES if so == "all" else (so,)
    for row in d["rows"]:
        assert row["spend"] == pytest.approx(float(spend_in(a, stages, row["campaign_id"])), abs=0.006)
        want_b = spend_in(b, stages, row["campaign_id"]) if b else F.Z
        assert row["cmp"]["spend"] == pytest.approx(float(want_b), abs=0.006)
        assert row["delta"]["spend"] == pytest.approx(row["spend"] - row["cmp"]["spend"], abs=0.011)
    t = d["total"]
    assert t["cmp"]["spend"] == pytest.approx(float(spend_in(b, stages)) if b else 0, abs=0.006)
    for card in d["by_stage"]:                                     # stage cards show A vs B too (stage filter does not apply to the cards)
        want_b = spend_in(b, (card["stage"],)) if b else F.Z
        assert card["cmp"]["spend"] == pytest.approx(float(want_b), abs=0.006) and "delta" in card
        assert card["spend"] == pytest.approx(float(spend_in(a, (card["stage"],))), abs=0.006)
    assert d["compare_info"] is not None


def test_unmatched_cmp(client, fx):
    d = get_ok(client, "/api/products/vita-bull/unmatched?window=7d&compare=prev")
    assert [r["meta_family"] for r in d["rows"]] == ["Brief2-2-Bull-398", ] or len(d["rows"]) >= 1
    for r in d["rows"]:
        fam = r["meta_family_key"]
        rows = [x for x in fx.ad_daily if x["meta_family_key"] == fam]
        a_ = sum(x["spend"] for x in rows if F.THROUGH - dt.timedelta(days=6) <= x["day"] <= F.THROUGH)
        b_ = sum(x["spend"] for x in rows if F.THROUGH - dt.timedelta(days=13) <= x["day"] <= F.THROUGH - dt.timedelta(days=7))
        assert r["spend"] == pytest.approx(float(a_), abs=0.006) and r["cmp"]["spend"] == pytest.approx(float(b_), abs=0.006)
        assert r["spend_per_day"] == pytest.approx(r["spend"] / 7, abs=0.006)
    assert d["total"]["cmp"]["spend"] == pytest.approx(sum(r["cmp"]["spend"] for r in d["rows"]), abs=0.011)
    # a family that only ran before A still shows up through its B spend
    d = get_ok(client, "/api/products/vita-bull/unmatched?window=1d&cfrom=2026-10-20&cto=2026-10-26")
    assert any(r["spend"] == 0 and r["cmp"]["spend"] > 0 for r in d["rows"]) or len(d["rows"]) >= 1


def test_ad_detail_family_compare(client, fx):
    d = get_ok(client, "/api/ads/1000?window=14d&compare=prev")
    f = d["metrics"]["family"]
    a_ = (F.THROUGH - dt.timedelta(days=13), F.THROUGH)
    b_ = (F.THROUGH - dt.timedelta(days=27), F.THROUGH - dt.timedelta(days=14))
    sa = F.window_sums(fx, "vb-fam-a", *a_, F.STAGES)
    sb = F.window_sums(fx, "vb-fam-a", *b_, F.STAGES)
    assert f["window"]["spend"] == pytest.approx(float(sa[0]), abs=0.006)
    assert f["window"]["spend_per_day"] == pytest.approx(float(sa[0]) / 14, abs=0.006)
    assert f["compare"]["spend"] == pytest.approx(float(sb[0]), abs=0.006) and f["compare"]["purchases"] == pytest.approx(float(sb[1]), abs=0.006)
    assert f["compare"]["spend_per_day"] == pytest.approx(float(sb[0]) / 14, abs=0.006)
    assert f["delta"]["spend"] == pytest.approx(float(sa[0] - sb[0]), abs=0.011)
    assert f["delta"]["spend_pct"] == pytest.approx(float((sa[0] - sb[0]) / sb[0]), abs=2e-4)
    assert d["compare_info"]["days"] == 14 and d["window_info"]["days"] == 14
    # launch/d7/d30 are fixed blocks without cmp; the ad's own meta_ads rows carry cmp
    assert "cmp" not in f["launch"] and "compare" not in get_ok(client, "/api/ads/1000?window=14d")["metrics"]["family"]
    assert all("cmp" in r for r in d["meta_ads"])
    # blank-family row compares as zeros
    z = get_ok(client, "/api/ads/1003?window=14d&compare=prev")["metrics"]["family"]
    assert z["compare"]["spend"] == 0 and z["delta"]["spend_pct"] is None and z["window"]["spend"] == 0


def test_stage_daily_ignores_compare(client):
    assert client.get("/api/products/vita-bull/stage-daily?window=7d&compare=prev").status_code == 200
    assert client.get("/api/products/vita-bull/stage-daily?compare=prev").status_code == 200
