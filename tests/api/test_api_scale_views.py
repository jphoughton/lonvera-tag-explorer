"""Test to scale, the two simple views (/api/promotions/scale; app/scale_views.py, table scaling_benchmarks).

Own throwaway database (not the shared API fixture): a small hand-built account is loaded through the real ETL builders. The expected numbers come from a
plain-Python oracle that works from the DAILY ROWS defined below (never from the precomputed tables), so eligibility, ranking, ratios and status words are
checked against an independent computation, in every Numbers basis.
"""
from __future__ import annotations

import datetime as dt

import psycopg
import pytest

from app import scale_views as sv
from etl import build_metrics, stage_moves

from . import seed_fixture as S
from .conftest import DB_NAME, PG_BASE, PASSWORD, make_app
from .helpers import get_ok

D = dt.date
D0, THROUGH = D(2026, 5, 6), D(2026, 11, 2)
SV_DB = f"{DB_NAME}_sv"
SV_URL = f"{PG_BASE}/{SV_DB}"
CAMPAIGNS = {"c-vb-test": "VB_Test_Hooks", "c-bl-test": "BL_Test_Hooks", "c-bf-test": "BF_Test_Hooks", "c-vb-s1": "VB_Scaling_One", "c-bl-s1": "BL_Scaling_One"}
UP_ALL, UP_NC = 2, 0.5                       # Upstack twins: all orders = 2x Meta's purchases and revenue, new customers = 0.5x (spend stays Meta's)
BASIS_MULT = {"": 1.0, "upstack_all": UP_ALL, "upstack_nc": UP_NC}
BASIS_QS = {"meta": "", "upstack_all": "source=upstack&basis=all", "upstack_nc": "source=upstack&basis=nc"}


def rng(a, b):
    d = a
    while d <= b:
        yield d
        d += dt.timedelta(days=1)


def cr(fam, prod, *segs):
    """One creative: segs = (campaign id, first day, last day, spend per day, purchases per day, revenue per day)."""
    return fam, prod, list(segs)


VB_T, BL_T, BF_T, VB_S, BL_S = "c-vb-test", "c-bl-test", "c-bf-test", "c-vb-s1", "c-bl-s1"
CREATIVES = [
    # ---- scaling that sets the product averages (30-day window = Oct 4 .. Nov 2)
    cr("vb-sc-a", "vita-bull", (VB_S, D(2026, 10, 10), THROUGH, 100, 1, 80)),
    cr("vb-old-sc", "vita-bull", (VB_S, D(2026, 9, 1), D(2026, 9, 10), 1000, 1, 100)),             # before the window: not in the average
    cr("bl-sc-a", "beef-liver", (BL_S, D(2026, 10, 5), THROUGH, 100, 1, 50)),
    # ---- testing, never scaled, still spending (view 1 candidates)
    cr("vb-t-edge", "vita-bull", (VB_T, D(2026, 10, 23), D(2026, 10, 31), 80, 1, 130)),             # last spend Oct 31 = 3rd last day: still recent
    cr("vb-t-big", "vita-bull", (VB_T, D(2026, 10, 20), THROUGH, 100, 2, 160)),                      # 28 purchases: medium
    cr("vb-t-high", "vita-bull", (VB_T, D(2026, 10, 20), THROUGH, 60, 4, 130)),                      # 56 purchases: high
    cr("vb-t-near", "vita-bull", (VB_T, D(2026, 10, 31), THROUGH, 200, 2, 200)),                     # 1.0 ROAS: near the bar, a miss
    cr("vb-t-roas", "vita-bull", (VB_T, D(2026, 10, 31), THROUGH, 200, 2, 400)),                    # ROAS 2.0 but CPA $100 above the Vita Bull CPA: beats on ROAS only
    cr("vb-t-poor", "vita-bull", (VB_T, D(2026, 10, 25), THROUGH, 100, 1, 20)),                      # far below the bar
    cr("vb-t-cheap", "vita-bull", (VB_T, D(2026, 10, 30), D(2026, 10, 30), 100, 9, 500)),            # $100 spend only (below the spend floor), last spend Oct 30 (also stale)
    cr("vb-t-few", "vita-bull", (VB_T, D(2026, 11, 1), THROUGH, 300, 1, 750)),                       # 2 purchases (below the purchase floor)
    cr("vb-t-dead", "vita-bull", (VB_T, D(2026, 10, 20), D(2026, 10, 29), 100, 2, 250)),             # great numbers but stopped Oct 29: dead
    cr("bl-t-cpa", "beef-liver", (BL_T, D(2026, 10, 27), THROUGH, 100, 2, 38)),                      # 6 days: ROAS 0.38 < 0.5 but CPA $50 < $100: beats on CPA only
    cr("bl-t-both", "beef-liver", (BL_T, D(2026, 10, 28), THROUGH, 100, 2, 90)),                     # beats on both
    cr("bf-t-nobar", "boneflex", (BF_T, D(2026, 10, 26), THROUGH, 100, 2, 250)),                     # Boneflex has no scaling spend: no bar
    # ---- moved to scaling (view 2); tests before, scaling after
    cr("vb-mv-a", "vita-bull", (VB_T, D(2026, 10, 10), D(2026, 10, 14), 100, 1, 90), (VB_S, D(2026, 10, 20), THROUGH, 200, 3, 270)),              # better, holding
    cr("vb-mv-fade", "vita-bull", (VB_T, D(2026, 10, 8), D(2026, 10, 12), 100, 1, 100), (VB_S, D(2026, 10, 14), D(2026, 10, 26), 200, 2, 300),
       (VB_S, D(2026, 10, 27), THROUGH, 200, 1, 100)),                                                                                           # last 7 days fall apart
    cr("vb-mv-stop", "vita-bull", (VB_T, D(2026, 10, 5), D(2026, 10, 7), 100, 1, 100), (VB_S, D(2026, 10, 9), D(2026, 10, 20), 100, 1, 70)),   # worse, stopped
    cr("vb-mv-early", "vita-bull", (VB_T, D(2026, 10, 27), D(2026, 10, 28), 100, 1, 90), (VB_S, D(2026, 10, 31), THROUGH, 50, 1, 60)),           # $150, 3 days: too early (spend)
    cr("bl-mv-c", "beef-liver", (BL_T, D(2026, 10, 14), D(2026, 10, 16), 50, 1, 40), (BL_S, D(2026, 10, 23), THROUGH, 40, 1, 45)),
    cr("bl-mv-old", "beef-liver", (BL_T, D(2026, 9, 20), D(2026, 9, 25), 80, 1, 70), (BL_S, D(2026, 9, 28), D(2026, 10, 3), 100, 1, 100)),     # moved before the 30-day window
    cr("bf-mv-zero", "boneflex", (BF_T, D(2026, 9, 15), D(2026, 9, 17), 50, 0, 0), (BL_S, D(2026, 9, 20), D(2026, 9, 27), 40, 1, 50)),         # moved before the window, its testing earned nothing: no baseline
    # ---- never in a rule: scaling only, same-day start, scaling before testing
    cr("vb-scaling-only", "vita-bull", (VB_S, D(2026, 10, 12), THROUGH, 30, 0, 0)),
    cr("bl-same-day", "beef-liver", (BL_T, D(2026, 10, 18), THROUGH, 10, 0, 0), (BL_S, D(2026, 10, 18), D(2026, 10, 19), 10, 0, 0)),
]
for i in range(1, 46):        # a mass relaunch: 45 creatives tested Oct 13-14 and all entered scaling on Oct 22
    CREATIVES.append(cr(f"vb-bulk-{i:02d}", "vita-bull", (VB_T, D(2026, 10, 13), D(2026, 10, 14), 30, 0, 0), (VB_S, D(2026, 10, 22), D(2026, 10, 24), 20, 1, 25)))
BULK = {f"vb-bulk-{i:02d}" for i in range(1, 46)}
STAGE = {cid: ("testing" if "test" in name.lower() else "scaling") for cid, name in CAMPAIGNS.items()}
NAMES = {"vita-bull": "Vita Bull", "beef-liver": "Beef Liver", "boneflex": "Boneflex"}


# ----------------------------------------------------------------------------------------------------------------- the database
@pytest.fixture(scope="module")
def vdb():
    try:
        S.recreate_db(f"{PG_BASE}/postgres", SV_DB)
        conn = psycopg.connect(SV_URL)
    except psycopg.OperationalError as e:
        pytest.skip(f"Postgres not available for API tests: {e}")
    with conn:
        S.apply_migrations(conn)
        pids = {slug: pid for pid, slug in conn.execute("SELECT id, slug FROM products").fetchall()}
        for cid, name in CAMPAIGNS.items():
            conn.execute("INSERT INTO meta_campaigns (campaign_id, account, name, stage) VALUES (%s,'Lonvera Main',%s,'scaling')", (cid, name))
        for n, (fam, prod, segs) in enumerate(CREATIVES, 1):
            conn.execute("""INSERT INTO tracker_rows (product_id, row_key, concept_id, ad_family, family_key, sheet_row, status, ad_type, launch_date, raw)
                            VALUES (%s,%s,%s,%s,%s,%s,'8 - Launched','VID',%s,'{}')""", (pids[prod], f"{fam}#1", f"C-{fam}", fam.upper(), fam, n, D(2026, 8, 1)))
            conn.execute("INSERT INTO meta_family_map (meta_family_key, meta_family, tracker_family_key, rule) VALUES (%s,%s,%s,'exact')", (fam, fam.upper(), fam))
            for k, (camp, d1, d2, sp, pu, rv) in enumerate(segs):
                ad = f"{fam}-{k}"
                conn.execute("""INSERT INTO meta_ads (ad_id, account, ad_name, meta_family_key, campaign_id, adset_id, adset_name, created_date, status, family_name)
                                VALUES (%s,'Lonvera Main',%s,%s,%s,'set:a','a',%s,'ACTIVE',%s)""", (ad, fam.upper(), fam, camp, D(2026, 8, 1), fam.upper()))
                for d in rng(d1, d2):
                    conn.execute("""INSERT INTO ad_daily (ad_id, day, meta_family_key, meta_family, campaign_id, spend, purchases, conv_value, link_clicks, impressions, source)
                                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'meta_api')""", (ad, d, fam, fam.upper(), camp, sp, pu, rv, sp // 10, sp * 20))
        conn.execute("INSERT INTO app_meta (key, value) VALUES ('daily_start', %s)", (D0.isoformat(),))
        build_metrics.rebuild(conn, commit=False)
        conn.execute("""INSERT INTO ad_daily_upstack (ad_id, day, meta_family_key, meta_family, campaign_id, spend, purchases, conv_value, nc_purchases, nc_conv_value, link_clicks, impressions)
                        SELECT ad_id, day, meta_family_key, meta_family, campaign_id, spend, purchases * %s, conv_value * %s, purchases * %s, conv_value * %s, link_clicks, impressions FROM ad_daily""",
                     (UP_ALL, UP_ALL, UP_NC, UP_NC))
        for b in ("nc", "all"):
            build_metrics.rebuild_source(conn, b)
        stage_moves.refresh(conn)
        for k, v in (("upstack_through", THROUGH.isoformat()), ("upstack_first_day", D0.isoformat()), ("upstack_built_at", "2026-11-03T00:00:00+00:00")):
            conn.execute("INSERT INTO app_meta (key, value) VALUES (%s,%s)", (k, v))
        conn.execute("INSERT INTO etl_runs (job, started_at, finished_at, status, data_through) VALUES ('build_metrics', now() - interval '1 hour', now() - interval '59 minutes', 'ok', %s)", (THROUGH,))
        conn.commit()
    return pids


@pytest.fixture(scope="module")
def vapp(vdb):
    a = make_app(DATABASE_URL=SV_URL)
    yield a
    a.extensions["pool"].close()


@pytest.fixture()
def vc(vapp):
    c = vapp.test_client()
    assert c.post("/api/login", json={"username": "JoshHoughton", "password": PASSWORD}).status_code == 204
    return c


@pytest.fixture()
def vconn(vdb):
    with psycopg.connect(SV_URL, row_factory=psycopg.rows.dict_row) as c:
        yield c


# ----------------------------------------------------------------------------------------------------------------- the oracle (daily rows only)
def daily(mult=1.0):
    """(family, product, stage, day, spend, purchases, revenue) for every day of every segment; purchases and revenue scaled by the basis."""
    out = []
    for fam, prod, segs in CREATIVES:
        for camp, d1, d2, sp, pu, rv in segs:
            for d in rng(d1, d2):
                out.append((fam, prod, STAGE[camp], d, sp, pu * mult, rv * mult))
    return out


def facts(mult=1.0):
    """Per creative: path, test / scale / last7 sums, first and last spend day, first scaling day, test days."""
    rows = daily(mult)
    out = {}
    for fam in dict.fromkeys(r[0] for r in rows):
        mine = [r for r in rows if r[0] == fam]
        prod = mine[0][1]
        tdays = [r[3] for r in mine if r[2] == "testing" and r[4] > 0]
        sdays = [r[3] for r in mine if r[2] == "scaling" and r[4] > 0]
        t0, s0 = (min(tdays) if tdays else None), (min(sdays) if sdays else None)
        path = "testing_only" if t0 and not s0 else "scaling_only" if s0 and not t0 else "moved" if t0 < s0 else "same_day" if t0 == s0 else "scaling_first"
        before = lambda r: s0 is None or r[3] < s0         # noqa: E731
        t = [r for r in mine if r[2] == "testing" and before(r)]
        s = [r for r in mine if r[2] == "scaling"]
        l7 = [r for r in mine if r[3] > THROUGH - dt.timedelta(days=7)]
        sums = lambda rs: tuple(sum(r[i] for r in rs) for i in (4, 5, 6))      # noqa: E731
        out[fam] = {"product": prod, "path": path, "first": min(r[3] for r in mine if r[4] > 0), "last": max(r[3] for r in mine if r[4] > 0), "move": s0,
                    "test": sums(t), "scale": sums(s), "scale_days": len(sdays), "last7": sums(l7), "last7_scale": sums([r for r in l7 if r[2] == "scaling"])}
    return out


def bench(mult=1.0):
    """Scaling spend / purchases / revenue per product over the 30 days ending on THROUGH."""
    b = {}
    for fam, prod, stage, d, sp, pu, rv in daily(mult):
        if stage == "scaling" and d > THROUGH - dt.timedelta(days=30):
            x = b.setdefault(prod, [0.0, 0.0, 0.0])
            x[0] += sp
            x[1] += pu
            x[2] += rv
    return b


def ratio(n, d):
    return n / d if d else None


def should_oracle(mult=1.0, min_spend=500.0, min_pur=5, pct=100, product=None):
    """(qualifying family keys in rank order, near-miss keys in order, dict of the numbers)"""
    f, b, frac = facts(mult), bench(mult), pct / 100
    qual, miss = [], []
    for fam, x in f.items():
        if x["path"] != "testing_only" or (THROUGH - x["last"]).days >= 3 or (product and x["product"] != product):
            continue
        sp, pu, rv = x["test"]
        bp = b.get(x["product"])
        if sp < min_spend or pu < min_pur or not bp or bp[0] <= 0 or bp[2] <= 0:
            continue
        bar_roas = bp[2] / bp[0] * frac
        bar_cpa = bp[0] / bp[1] / frac if bp[1] > 0 else None
        roas, cpa = rv / sp, ratio(sp, pu)
        ok = roas >= bar_roas or (cpa is not None and bar_cpa is not None and cpa <= bar_cpa)
        (qual if ok else miss).append((fam, rv - bar_roas * sp, sp, roas / bar_roas))
    qual.sort(key=lambda t: (-t[1], -t[2], t[0]))
    miss.sort(key=lambda t: (-t[3], -t[2], t[0]))
    return [t[0] for t in qual], [t[0] for t in miss[:5]], {t[0]: t for t in qual + miss}


def get(vc, qs="", view="should_scale"):
    return get_ok(vc, f"/api/promotions/scale?view={view}" + ("&" + qs if qs else ""))


def keys(d, field="rows"):
    return [r["family_key"] for r in d[field]]


# ----------------------------------------------------------------------------------------------------------------- the benchmark table
@pytest.mark.parametrize("basis", ["meta", "upstack_all", "upstack_nc"])
def test_benchmarks_equal_the_oracle_in_every_basis(vconn, basis):
    got = {r["product_slug"]: r for r in vconn.execute("SELECT * FROM scaling_benchmarks WHERE basis = %s", (basis,)).fetchall()}
    exp = bench(BASIS_MULT["" if basis == "meta" else basis])
    assert set(got) == set(exp) == {"vita-bull", "beef-liver"}
    for p, (sp, pu, rv) in exp.items():
        assert float(got[p]["spend"]) == pytest.approx(sp) and float(got[p]["purchases"]) == pytest.approx(pu) and float(got[p]["revenue"]) == pytest.approx(rv)
        assert got[p]["day_to"] == THROUGH and got[p]["day_from"] == THROUGH - dt.timedelta(days=29)
    n_vb = len({r[0] for r in daily() if r[1] == "vita-bull" and r[2] == "scaling" and r[3] > THROUGH - dt.timedelta(days=30)})
    assert got["vita-bull"]["creatives"] == n_vb


def test_benchmark_window_is_30_days_and_spend_ties_to_family_daily(vconn):
    total = vconn.execute("SELECT sum(spend) FROM family_daily WHERE stage = 'scaling' AND day > %s", (THROUGH - dt.timedelta(days=30),)).fetchone()["sum"]
    assert float(vconn.execute("SELECT sum(spend) FROM scaling_benchmarks WHERE basis = 'meta'").fetchone()["sum"]) == pytest.approx(float(total))
    vb = bench()["vita-bull"]
    assert vb[0] == pytest.approx(float(vconn.execute("SELECT spend FROM scaling_benchmarks WHERE basis='meta' AND product_slug='vita-bull'").fetchone()["spend"]))
    assert 1000 * 10 not in [vb[0]]                                        # the Sep 1-10 scaling spend ($10,000) is outside the window


def test_refresh_is_idempotent_and_runs_in_the_nightly_step(vconn):
    before = vconn.execute("SELECT * FROM scaling_benchmarks ORDER BY basis, product_slug").fetchall()
    with psycopg.connect(SV_URL) as plain:
        stats = stage_moves.refresh(plain)
        plain.rollback()
    assert stats["scaling_benchmarks"] == len(before) == 6
    assert "scaling_benchmarks" in stage_moves.TABLES


# ----------------------------------------------------------------------------------------------------------------- view 1: what should be scaled
def test_default_rules_rank_and_exclusions(vc):
    d = get(vc)
    want, misses, info = should_oracle()
    assert keys(d) == want and want[0] == "vb-t-high" and len(want) == 6
    assert d["filters"] == {"product": None, "min_test_spend": 500.0, "min_purchases": 5, "bar_pct": 100}
    assert d["defaults"] == {"min_test_spend": 500.0, "min_purchases": 5, "bar_pct": 100, "recent_days": 3}
    for fam in ("vb-mv-a", "vb-mv-fade", "bl-mv-c", "vb-scaling-only", "bl-same-day", "vb-t-dead", "vb-t-cheap", "vb-t-few", "vb-t-poor", "vb-t-near", "bf-t-nobar"):
        assert fam not in keys(d), fam
    assert {"vb-t-edge", "vb-t-big", "vb-t-high", "vb-t-roas", "bl-t-cpa", "bl-t-both"} <= set(keys(d))          # the edge creative (last spend Oct 31) is still recent
    assert d["rows"] == sorted(d["rows"], key=lambda r: (-r["extra_revenue"], -r["test"]["spend"], r["family_key"]))
    s = d["summary"]
    assert s["qualify"] == len(want) and s["meets_floors"] == len(info) + s["no_bar"] and s["testing_live"] == 10
    assert s["no_bar"] == 1                                                                         # bf-t-nobar met the floors but Boneflex has no scaling average


def test_row_numbers_match_the_daily_rows(vc):
    d = get(vc)
    f, b = facts(), bench()
    for r in d["rows"]:
        x = f[r["family_key"]]
        sp, pu, rv = x["test"]
        assert r["test"]["spend"] == pytest.approx(sp) and r["test"]["purchases"] == pytest.approx(pu) and r["test"]["revenue"] == pytest.approx(rv)
        assert r["test"]["roas"] == pytest.approx(rv / sp, abs=1e-4) and r["test"]["cpa"] == pytest.approx(sp / pu, abs=0.01)
        l7s, l7p, l7r = x["last7"]
        assert r["last7"]["spend"] == pytest.approx(l7s) and r["last7"]["roas"] == pytest.approx(ratio(l7r, l7s), abs=1e-4)
        assert r["days_in_testing"] == (THROUGH - x["first"]).days + 1
        assert r["product"]["slug"] == x["product"] and r["product"]["name"] == NAMES[x["product"]]
        assert r["primary_id"] is not None and r["vs_scaling"]["word"] == "above"
        bsp, bpu, brv = b[x["product"]]
        assert r["vs_scaling"]["bar_roas"] == pytest.approx(brv / bsp, abs=1e-4) and r["vs_scaling"]["bar_cpa"] == pytest.approx(bsp / bpu, abs=0.01)
        assert r["extra_revenue"] == pytest.approx(rv - brv / bsp * sp, abs=0.01)
    bars = {x["slug"]: x for x in d["bars"]}
    assert set(bars) == {"vita-bull", "beef-liver"}
    for p, (sp, pu, rv) in b.items():
        assert bars[p]["roas"] == pytest.approx(rv / sp, abs=1e-4) and bars[p]["cpa"] == pytest.approx(sp / pu, abs=0.01) and bars[p]["spend"] == pytest.approx(sp)
        assert bars[p]["day_to"] == THROUGH.isoformat() and bars[p]["name"] == NAMES[p]


def test_beats_words_and_ratio_of_sums(vc):
    d = get(vc)
    by = {r["family_key"]: r for r in d["rows"]}
    assert by["bl-t-cpa"]["vs_scaling"]["beats"] == "cpa" and by["bl-t-cpa"]["test"]["roas"] < by["bl-t-cpa"]["vs_scaling"]["bar_roas"]
    assert by["bl-t-both"]["vs_scaling"]["beats"] == "both"
    assert by["vb-t-roas"]["vs_scaling"]["beats"] == "roas" and by["vb-t-roas"]["test"]["cpa"] > by["vb-t-roas"]["vs_scaling"]["bar_cpa"]
    assert by["vb-t-big"]["vs_scaling"]["beats"] == "both"
    # one creative's ROAS is the ratio of its sums, not a mean of daily ratios (the daily rows here are identical, so also check the CPA direction)
    assert by["vb-t-big"]["test"]["roas"] == pytest.approx(160 / 100, abs=1e-4) and by["vb-t-big"]["test"]["cpa"] == pytest.approx(50.0)


def test_confidence_chip_bands(vc):
    d = get(vc)
    by = {r["family_key"]: r["confidence"] for r in d["rows"]}
    assert by["vb-t-high"] == {"level": "high", "purchases": 56}
    assert by["vb-t-big"] == {"level": "medium", "purchases": 28}
    assert by["vb-t-edge"]["level"] == "low" and by["bl-t-cpa"]["level"] == "low"
    for p, lvl in ((51, "high"), (50, "medium"), (50.01, "high"), (20, "medium"), (19.99, "low"), (0, "low"), (21, "medium")):
        assert sv.confidence(p) == lvl, p
    assert d["summary"]["confidence"] == {k: sum(1 for r in d["rows"] if r["confidence"]["level"] == k) for k in ("high", "medium", "low")}
    assert "50" in d["definition"]["confidence"]["high"] and "20" in d["definition"]["confidence"]["medium"] and "screen" in d["definition"]["screen"].lower()


@pytest.mark.parametrize("qs,kw", [
    ("min_test_spend=0&min_purchases=0", dict(min_spend=0, min_pur=0)),
    ("min_test_spend=50", dict(min_spend=50)),
    ("min_test_spend=1000", dict(min_spend=1000)),
    ("min_purchases=20", dict(min_pur=20)),
    ("min_purchases=60", dict(min_pur=60)),
    ("bar_pct=50", dict(pct=50)),
    ("bar_pct=150", dict(pct=150)),
    ("bar_pct=90&min_purchases=1", dict(pct=90, min_pur=1)),
    ("product=vita-bull", dict(product="vita-bull")),
    ("product=beef-liver&min_test_spend=400", dict(product="beef-liver", min_spend=400)),
])
def test_thresholds_are_editable_and_equal_the_oracle(vc, qs, kw):
    d = get(vc, qs)
    want, misses, _ = should_oracle(**kw)
    assert keys(d) == want, (qs, keys(d), want)
    assert keys(d, "near_misses") == misses


def test_lowering_the_floors_adds_the_small_creatives_and_raising_them_removes(vc):
    base = set(keys(get(vc)))
    assert "vb-t-few" not in base and "vb-t-cheap" not in base
    low = set(keys(get(vc, "min_test_spend=0&min_purchases=0")))
    assert "vb-t-few" in low and "vb-t-cheap" not in low           # cheap is stale (last spend Oct 30): the recency rule stays
    assert base <= low and "vb-t-dead" not in low
    assert set(keys(get(vc, "min_test_spend=800"))) < base


def test_bar_percent_moves_the_bar_both_ways(vc):
    easy, hard = get(vc, "bar_pct=50"), get(vc, "bar_pct=200")
    assert "vb-t-near" in keys(easy) and "vb-t-near" not in keys(get(vc))
    assert len(keys(hard)) < len(keys(get(vc))) < len(keys(easy))
    b = {x["slug"]: x for x in get(vc)["bars"]}["vita-bull"]
    assert {x["slug"]: x for x in easy["bars"]}["vita-bull"]["bar_roas"] == pytest.approx(0.5 * b["roas"], abs=1e-4) and {x["slug"]: x for x in hard["bars"]}["vita-bull"]["bar_roas"] == pytest.approx(2 * b["roas"], abs=1e-4)
    assert "50%" in easy["definition"]["rule"]


def test_empty_state_lists_the_nearest_misses_best_first(vc):
    d = get(vc, "bar_pct=1000&min_purchases=0&min_test_spend=0")
    assert d["rows"] == [] and d["summary"]["qualify"] == 0
    want_q, want_m, info = should_oracle(min_spend=0, min_pur=0, pct=1000)
    assert want_q == [] and keys(d, "near_misses") == want_m and len(want_m) == 5
    ratios = [r["vs_scaling"]["ratio"] for r in d["near_misses"]]
    assert ratios == sorted(ratios, reverse=True) and all(r["vs_scaling"]["word"] in ("near", "below") for r in d["near_misses"])
    # default rules: the near miss is the 1.0-ROAS creative, worded 'near'
    d0 = get(vc)
    nm = {r["family_key"]: r for r in d0["near_misses"]}
    assert nm["vb-t-near"]["vs_scaling"]["word"] == "near" and nm["vb-t-poor"]["vs_scaling"]["word"] == "below"
    assert "vb-t-dead" not in nm and "vb-t-cheap" not in nm and "vb-t-few" not in nm


def test_a_product_with_no_scaling_spend_has_no_bar_and_says_so(vc):
    d = get(vc, "product=boneflex")
    assert d["rows"] == [] and d["near_misses"] == [] and d["bars"] == []
    assert any("Boneflex has no scaling spend in the last 30 days" in n for n in d["notes"])
    assert d["summary"]["no_bar"] == 1 and d["summary"]["testing_live"] == 1


def test_product_chips_count_live_testing_creatives(vc):
    d = get(vc)
    assert {c["slug"]: c["n"] for c in d["products"]} == {"vita-bull": 7, "beef-liver": 2, "boneflex": 1}
    f = facts()
    live = [k for k, x in f.items() if x["path"] == "testing_only" and (THROUGH - x["last"]).days < 3]
    assert sum(c["n"] for c in d["products"]) == len(live)
    assert [c["slug"] for c in d["products"]] == ["vita-bull", "beef-liver", "boneflex"]


@pytest.mark.parametrize("basis", ["upstack_all", "upstack_nc"])
def test_each_numbers_basis_uses_its_own_purchases_revenue_and_bar(vc, basis):
    mult = BASIS_MULT[basis]
    d = get(vc, BASIS_QS[basis])
    want, misses, _ = should_oracle(mult=mult)
    assert keys(d) == want and keys(d, "near_misses") == misses
    assert d["labels"]["purchases"] == ("Orders" if basis == "upstack_all" else "New customers")
    by = {r["family_key"]: r for r in d["rows"]}
    f = facts(mult)
    for k, r in by.items():
        assert r["test"]["purchases"] == pytest.approx(f[k]["test"][1]) and r["test"]["revenue"] == pytest.approx(f[k]["test"][2])
        assert r["test"]["spend"] == pytest.approx(f[k]["test"][0])                       # spend is Meta's in every basis
    b = bench(mult)
    assert {x["slug"]: x["roas"] for x in d["bars"]}["vita-bull"] == pytest.approx(b["vita-bull"][2] / b["vita-bull"][0], abs=1e-4)
    # min purchases is in the chosen basis: with new customers at half of Meta's purchases the default floor of 5 drops creatives it kept before
    if basis == "upstack_nc":
        assert set(keys(d)) != set(keys(get(vc)))


def test_meta_basis_is_the_default_and_source_meta_is_the_same(vc):
    assert keys(get(vc, "source=meta")) == keys(get(vc))


def test_every_row_links_to_its_creative_page(vc, vdb):
    with psycopg.connect(SV_URL) as c:
        ids = dict(c.execute("SELECT family_key, id FROM tracker_rows").fetchall())
    for r in get(vc)["rows"]:
        assert r["primary_id"] == ids[r["family_key"]]


# ----------------------------------------------------------------------------------------------------------------- view 2: performance in scaling
def in_scaling_oracle(mult=1.0, lo=THROUGH - dt.timedelta(days=29), hide_bulk=False, product=None, bulk_min=40):
    f = facts(mult)
    moved = {k: x for k, x in f.items() if x["path"] == "moved" and lo <= x["move"] <= THROUGH}
    per_day = {}
    for x in moved.values():
        per_day[x["move"]] = per_day.get(x["move"], 0) + 1
    out = {}
    for k, x in moved.items():
        bulk = per_day[x["move"]] >= bulk_min
        if hide_bulk and bulk or (product and x["product"] != product):
            continue
        out[k] = {**x, "bulk": bulk}
    return out


def status_of(x):
    sp, pu, rv = x["scale"]
    l7s, l7p, l7r = x["last7_scale"]
    if sp < 200 or x["scale_days"] < 3:
        return "too early"
    if l7s <= 0:
        return "stopped"
    return "holding" if l7r / l7s >= 0.9 * (rv / sp) else "fading"


def vs_testing_of(x):
    sp, pu, rv = x["scale"]
    tsp, tpu, trv = x["test"]
    if sp < 200 or x["scale_days"] < 3:
        return "too early"
    if tsp <= 0 or trv <= 0:
        return "no baseline"
    r = (rv / sp) / (trv / tsp)
    return "better" if r >= 1.10 else "about the same" if r >= 0.90 else "worse"


def test_in_scaling_default_window_rows_and_order(vc):
    d = get(vc, view="in_scaling")
    want = in_scaling_oracle()
    assert set(keys(d)) == set(want)
    assert "bl-mv-old" not in keys(d) and "vb-t-big" not in keys(d) and "vb-scaling-only" not in keys(d) and "bl-same-day" not in keys(d)
    spends = [r["scale"]["spend"] for r in d["rows"]]
    assert spends == sorted(spends, reverse=True)
    assert d["summary"]["n"] == len(want) == len(d["rows"])
    assert d["filters"] == {"product": None, "hide_bulk": False, "bulk_min": 40}


def test_in_scaling_numbers_status_words_and_days(vc):
    d = get(vc, view="in_scaling")
    want = in_scaling_oracle()
    for r in d["rows"]:
        x = want[r["family_key"]]
        sp, pu, rv = x["scale"]
        assert r["scale"]["spend"] == pytest.approx(sp) and r["scale"]["purchases"] == pytest.approx(pu) and r["scale"]["revenue"] == pytest.approx(rv)
        assert r["scale"]["roas"] == pytest.approx(rv / sp, abs=1e-4)
        assert r["scale"]["cpa"] == (pytest.approx(sp / pu, abs=0.01) if pu else None)
        l7s, l7p, l7r = x["last7_scale"]
        assert r["last7"]["spend"] == pytest.approx(l7s) and r["last7"]["roas"] == (pytest.approx(l7r / l7s, abs=1e-4) if l7s else None)
        assert r["last7"]["cpa"] == (pytest.approx(l7s / l7p, abs=0.01) if l7p else None)
        assert r["moved"] == x["move"].isoformat() and r["days_since_move"] == (THROUGH - x["move"]).days
        assert r["status"] == status_of(x), r["family_key"]
        assert r["vs_testing"]["word"] == vs_testing_of(x), r["family_key"]
        assert r["bulk"] == x["bulk"]
    by = {r["family_key"]: r for r in d["rows"]}
    assert by["vb-mv-a"]["status"] == "holding" and by["vb-mv-a"]["vs_testing"]["word"] == "better"
    assert by["vb-mv-fade"]["status"] == "fading" and by["vb-mv-fade"]["vs_testing"]["word"] == "better"           # better than testing since the move, but the last week fell apart
    assert by["vb-mv-stop"]["status"] == "stopped" and by["vb-mv-stop"]["vs_testing"]["word"] == "worse" and by["vb-mv-stop"]["last7"]["spend"] == 0
    assert by["vb-mv-early"]["status"] == "too early" and by["vb-mv-early"]["vs_testing"]["word"] == "too early"
    assert by["bl-mv-c"]["status"] == "holding"
    assert by["vb-mv-fade"]["last7"]["roas"] == pytest.approx(0.5) and by["vb-mv-fade"]["scale"]["roas"] == pytest.approx(4600 / 4000)
    assert by["vb-mv-a"]["vs_testing"]["ratio"] == pytest.approx((3780 / 2800) / (450 / 500), abs=1e-3)
    assert by["vb-mv-a"]["test"]["roas"] == pytest.approx(0.9)


def test_held_up_logic_is_the_pages_own(vc):
    """vs testing uses promotions.held_up: its too-early floor and its 90% line; no second definition."""
    from app import promotions as P
    d = get(vc, view="in_scaling")
    want = in_scaling_oracle()
    for r in d["rows"]:
        x = want[r["family_key"]]
        sp, pu, rv = x["scale"]
        tsp, tpu, trv = x["test"]
        held = P.held_up({"scale_spend": sp, "scale_revenue": rv, "scale_days": x["scale_days"], "test_spend": tsp, "test_revenue": trv})
        if held["state"] in ("held", "softer", "fell"):
            assert (r["vs_testing"]["word"] in ("better", "about the same")) == (held["state"] == "held")
            assert r["vs_testing"]["ratio"] == held["ratio"]
        else:
            assert r["vs_testing"]["word"] == held["state"].replace("_", " ")
    assert "90%" in d["definition"]["vs_testing"] and "$200" in d["definition"]["status"]


def test_total_row_per_product_is_ratio_of_sums(vc):
    d = get(vc, view="in_scaling")
    want = in_scaling_oracle()
    by_p = {}
    for x in want.values():
        e = by_p.setdefault(x["product"], [0, 0.0, 0.0, 0.0, 0.0, 0.0])
        e[0] += 1
        e[1] += x["scale"][0]; e[2] += x["scale"][2]; e[3] += x["scale"][1]; e[4] += x["last7_scale"][0]; e[5] += x["last7_scale"][2]
    assert [t["slug"] for t in d["totals"]] == [p for p in ("vita-bull", "beef-liver", "boneflex") if p in by_p]
    for t in d["totals"]:
        n, sp, rv, pu, l7s, l7r = by_p[t["slug"]]
        assert t["creatives"] == n and t["scale"]["spend"] == pytest.approx(sp) and t["scale"]["roas"] == pytest.approx(rv / sp, abs=1e-4)
        assert t["scale"]["cpa"] == pytest.approx(sp / pu, abs=0.01) and t["last7"]["roas"] == (pytest.approx(l7r / l7s, abs=1e-4) if l7s else None)
        rows = [r for r in d["rows"] if r["product"]["slug"] == t["slug"]]
        mean_of_ratios = sum(r["scale"]["roas"] for r in rows) / len(rows)
        if len(rows) > 3:
            assert t["scale"]["roas"] != pytest.approx(mean_of_ratios, abs=1e-6)
    allsp = sum(v[1] for v in by_p.values())
    assert d["total_all"]["scale"]["spend"] == pytest.approx(allsp) and d["total_all"]["creatives"] == len(want)


def test_bulk_moves_are_flagged_and_hide_bulk_removes_them_everywhere(vc):
    d = get(vc, view="in_scaling")
    assert {r["family_key"] for r in d["rows"] if r["bulk"]} == BULK and all(r["moves_that_day"] == 45 for r in d["rows"] if r["bulk"])
    assert d["bulk_days"] == [{"day": "2026-10-22", "moves": 45}] and d["summary"]["bulk"] == 45
    h = get(vc, "hide_bulk=1", view="in_scaling")
    assert BULK.isdisjoint(keys(h)) and h["hidden_bulk"] == 45 and set(keys(h)) == set(in_scaling_oracle(hide_bulk=True))
    assert h["summary"]["n"] == d["summary"]["n"] - 45
    assert sum(t["creatives"] for t in h["totals"]) == h["summary"]["n"]
    assert any("45 creatives that moved on bulk days" in n for n in h["notes"])
    hi = get(vc, "bulk_min=46", view="in_scaling")                              # raise the line above the 45-creative day: no bulk day
    assert hi["bulk_days"] == [] and not any(r["bulk"] for r in hi["rows"])
    lo = get(vc, "bulk_min=1&hide_bulk=1", view="in_scaling")
    assert lo["rows"] == [] and lo["total_all"] is None


def test_window_and_product_filters(vc):
    full = get(vc, "window=launch", view="in_scaling")
    assert "bl-mv-old" in keys(full) and set(keys(full)) == set(in_scaling_oracle(lo=D0))
    z = next(r for r in full["rows"] if r["family_key"] == "bf-mv-zero")
    assert z["vs_testing"] == {"word": "no baseline", "ratio": None} and z["status"] == "stopped"
    w7 = get(vc, "days=7", view="in_scaling")
    assert set(keys(w7)) == set(in_scaling_oracle(lo=THROUGH - dt.timedelta(days=6)))
    one = get(vc, "product=beef-liver", view="in_scaling")
    assert set(keys(one)) == set(in_scaling_oracle(product="beef-liver")) and {t["slug"] for t in one["totals"]} == {"beef-liver"}
    assert {c["slug"] for c in get(vc, view="in_scaling")["products"]} == {"vita-bull", "beef-liver"}
    assert {c["slug"] for c in full["products"]} == {"vita-bull", "beef-liver", "boneflex"}


@pytest.mark.parametrize("basis", ["upstack_all", "upstack_nc"])
def test_in_scaling_in_each_basis(vc, basis):
    mult = BASIS_MULT[basis]
    d = get(vc, BASIS_QS[basis], view="in_scaling")
    want = in_scaling_oracle(mult=mult)
    assert set(keys(d)) == set(want)
    for r in d["rows"]:
        x = want[r["family_key"]]
        assert r["scale"]["spend"] == pytest.approx(x["scale"][0]) and r["scale"]["revenue"] == pytest.approx(x["scale"][2]) and r["status"] == status_of(x)
        assert r["vs_testing"]["word"] == vs_testing_of(x)
    assert d["labels"]["roas"] == ("ROAS" if basis == "upstack_all" else "NC ROAS")


def test_in_scaling_empty_window_is_empty_not_an_error(vc):
    d = get(vc, "from=2026-05-06&to=2026-05-10", view="in_scaling")
    assert d["rows"] == [] and d["totals"] == [] and d["total_all"] is None and d["summary"]["n"] == 0


# ----------------------------------------------------------------------------------------------------------------- errors, limits, guards
def test_bad_parameters_are_400s_and_unknown_product_404(vc):
    for qs in ("view=nope", "view=should_scale&min_purchases=-1", "view=should_scale&min_purchases=x", "view=should_scale&bar_pct=0", "view=should_scale&bar_pct=1001",
               "view=should_scale&min_test_spend=abc", "view=in_scaling&bulk_min=0", "view=in_scaling&hide_bulk=maybe", "view=in_scaling&window=bogus", "source=bogus"):
        r = vc.get("/api/promotions/scale?" + qs)
        assert r.status_code == 400 and "error" in r.get_json(), (qs, r.status_code)
    assert vc.get("/api/promotions/scale?view=should_scale&product=nope").status_code == 404
    assert vc.get("/api/promotions/scale?view=in_scaling&product=none").status_code == 200


def test_endpoint_needs_a_login_and_is_cacheable(vapp, vc):
    from app import respcache
    assert vapp.test_client().get("/api/promotions/scale").status_code == 401
    assert respcache.CACHEABLE.match("/api/promotions/scale") and respcache.CACHEABLE.match("/api/promotions") and not respcache.CACHEABLE.match("/api/promotions/other")
    assert vc.get("/api/promotions/scale").status_code == 200                          # no view = should_scale


def test_response_is_json_safe_and_free_of_sql_text(vc):
    for view in sv.VIEWS:
        text = vc.get(f"/api/promotions/scale?view={view}").get_data(as_text=True)
        for bad in ("SELECT ", "family_daily", "creative_stage_moves", "NaN", "Infinity"):
            assert bad not in text, (view, bad)


def test_not_built_yet_is_a_clear_503(vc, vconn):
    saved = vconn.execute("SELECT value FROM app_meta WHERE key = 'stage_moves_built_at'").fetchone()["value"]
    vconn.execute("DELETE FROM app_meta WHERE key = 'stage_moves_built_at'")
    vconn.commit()
    try:
        for view in sv.VIEWS:
            r = vc.get(f"/api/promotions/scale?view={view}")
            assert r.status_code == 503 and "etl.stage_moves" in r.get_json()["error"]
    finally:
        vconn.execute("INSERT INTO app_meta (key, value) VALUES ('stage_moves_built_at', %s)", (saved,))
        vconn.commit()


@pytest.mark.parametrize("view,fn", [("should_scale", "_fetch_testing"), ("in_scaling", "_fetch_moved")])
def test_a_slow_query_answers_a_json_error_not_a_hang(vc, monkeypatch, view, fn):
    import time
    from app import promotions as P

    def slow(conn, *a):
        conn.execute("SELECT pg_sleep(3)")
        return []
    monkeypatch.setattr(sv, fn, slow)
    monkeypatch.setattr(P, "QUERY_TIMEOUT_MS", 150)
    t = time.time()
    r = vc.get(f"/api/promotions/scale?view={view}")
    assert r.status_code == 503 and r.get_json() == {"error": "query timed out"} and time.time() - t < 2.5


def test_stale_tables_add_a_note(vc, vconn):
    saved = vconn.execute("SELECT value FROM app_meta WHERE key = 'stage_moves_data_through'").fetchone()["value"]
    vconn.execute("UPDATE app_meta SET value = '2026-10-30' WHERE key = 'stage_moves_data_through'")
    vconn.commit()
    try:
        for view in sv.VIEWS:
            assert any("older than the latest day of data" in n for n in get(vc, view=view)["notes"])
    finally:
        vconn.execute("UPDATE app_meta SET value = %s WHERE key = 'stage_moves_data_through'", (saved,))
        vconn.commit()
