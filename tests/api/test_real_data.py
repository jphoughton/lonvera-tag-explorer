"""Tie-out against the real data loaded by the ETL (skipped when the real DB is empty or unreachable).

Targets (CONTRACT rule 13, data through 2026-11-02) and the frozen sheet snapshot `rollups.json`:
every cluster / ICP / pain row of the sheet must equal the API's row (spend and revenue within $1, everything else exact).
"""
import json
import os
import time
from urllib.parse import quote

import psycopg
import pytest

from app import create_app

REAL_URL = os.environ.get("TAGEXP_REAL_DB", "postgresql://localhost/tagexp_dev")
SNAP = os.path.expanduser("~/lonvera-tag-explorer-data/snapshot-2026-11-03/rollups.json")
BUDGET_MS = 300


def _real_has_data() -> bool:
    try:
        with psycopg.connect(REAL_URL, connect_timeout=3) as c:
            return c.execute("SELECT count(*) FROM ad_metrics").fetchone()[0] > 0
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _real_has_data() or not os.path.exists(SNAP), reason="real data not loaded")


@pytest.fixture(scope="module")
def rc():
    app = create_app({"DATABASE_URL": REAL_URL, "APP_PASSWORD": "pw", "SECRET_KEY": "k", "DEV_INSECURE_COOKIES": True, "DB_POOL_MAX": 4})
    c = app.test_client()
    assert c.post("/api/login", json={"password": "pw"}).status_code == 204
    yield c
    app.extensions["pool"].close()


def get(rc, url, budget=BUDGET_MS):
    t = time.perf_counter()
    r = rc.get(url)
    ms = (time.perf_counter() - t) * 1000
    assert r.status_code == 200, (url, r.status_code, r.get_data(as_text=True)[:300])
    assert ms < budget, f"{url} took {ms:.0f} ms (budget {budget} ms)"
    return r.get_json()


def num(x):
    return None if x in ("", None) else float(x)


def isnum(x):
    try:
        float(x)
        return x != ""
    except (TypeError, ValueError):
        return False


def vita_bull(rc):
    return next(p for p in get(rc, "/api/products") if p["slug"] == "vita-bull")


# ------------------------------------------------------------------------------------------------------ headline targets
def test_vita_bull_headline_targets(rc):
    p = vita_bull(rc)
    m = p["m"]
    assert p["rows"] == 1464
    # sheet says 1,434 unique ads; the DB keeps the blank-family row 'Lonvera-VitalBull-Static-7.1' apart from the real family of the same
    # name ('id:' key per CONTRACT rule 6) -> 1,435 (documented in docs/QUESTIONS_A.md). Group levels agree with the sheet (see below).
    assert m["unique_ads"] in (1434, 1435)
    assert m["live_now"] == 141
    assert m["spend"] == pytest.approx(4761591.26, abs=1.0) and m["purchases"] == 54960 and m["revenue"] == pytest.approx(4262522.08, abs=1.0)
    assert m["roas"] == pytest.approx(0.8952, abs=0.001) and m["cpa"] == pytest.approx(86.64, abs=0.01)
    assert m["spend_7d"] == pytest.approx(17727.98, abs=5.0) and m["purchases_7d"] == pytest.approx(168, abs=1) and m["cpa_7d"] == pytest.approx(105.52, abs=1.5)


def test_unmatched_vita_bull_7d(rc):
    u = get(rc, "/api/products/vita-bull/rollup?level=cluster")["unmatched"]
    assert u["spend_7d"] == pytest.approx(3997.48, abs=5.0) and u["families"] == 21
    um = get(rc, "/api/products/vita-bull/unmatched?window=7d")
    assert um["total"]["spend"] == pytest.approx(u["spend_7d"], abs=1.0)
    assert any(r["meta_family"].lower().startswith("brief2-2-bull-398") for r in um["rows"])


# ------------------------------------------------------------------------------------------------------ snapshot rollups
def snapshot_tables():
    v = json.load(open(SNAP))
    cell = lambda r, i: r[i] if i < len(r) else ""
    cluster, icp, pain = {}, {}, {}
    for r in v[3:]:
        if cell(r, 0) != "" and isnum(cell(r, 1)):
            cluster[(r[0],)] = [num(cell(r, i)) for i in range(1, 10)]
        if cell(r, 11) != "" and isnum(cell(r, 13)):
            icp[(r[11], cell(r, 12))] = [num(cell(r, i)) for i in range(13, 22)]
        if cell(r, 24) != "" and isnum(cell(r, 27)):
            pain[(r[24], cell(r, 25), cell(r, 26))] = [num(cell(r, i)) for i in range(27, 36)]
    total = [num(cell(v[1], i)) for i in range(1, 10)]
    return cluster, icp, pain, total


def check_row(m, want, label):
    unique, live, spend, purch, rev, roas, cpa, sp7, cpa7 = want
    assert m["unique_ads"] == unique, (label, "unique", m["unique_ads"], unique)
    assert m["live_now"] == live, (label, "live")
    assert m["spend"] == pytest.approx(spend, abs=1.0), (label, "spend")
    assert m["purchases"] == purch, (label, "purchases")
    assert m["revenue"] == pytest.approx(rev, abs=1.0), (label, "revenue")
    if spend:
        assert m["roas"] == pytest.approx(roas, abs=0.006), (label, "roas")
        assert m["cpa"] == pytest.approx(cpa, abs=0.02), (label, "cpa")
    else:
        assert m["roas"] is None and m["cpa"] is None
    assert m["spend_7d"] == pytest.approx(sp7, abs=2.0), (label, "7d")
    if cpa7 is not None:
        assert m["cpa_7d"] == pytest.approx(cpa7, abs=1.5), (label, "cpa 7d")


def test_every_rollup_row_matches_the_sheet(rc):
    cluster, icp, pain, total = snapshot_tables()
    assert (len(cluster), len(icp), len(pain)) == (6, 13, 37)
    got = get(rc, "/api/products/vita-bull/rollup?level=cluster")
    api = {(r["label"],): r["m"] for r in got["rows"]}
    for k, want in cluster.items():
        assert k in api, f"cluster {k} missing from API"
        check_row(api[k], want, f"cluster {k}")
    n_icp = n_pain = 0
    for c in {k[0] for k in icp}:
        rows = get(rc, f"/api/products/vita-bull/rollup?level=icp&cluster={quote(c)}")["rows"]
        api = {(c, r["label"]): r["m"] for r in rows}
        for k, want in icp.items():
            if k[0] == c:
                assert k in api, f"icp {k} missing"
                check_row(api[k], want, f"icp {k}")
                n_icp += 1
    for (c, i) in {(k[0], k[1]) for k in pain}:
        rows = get(rc, f"/api/products/vita-bull/rollup?level=pain&cluster={quote(c)}&icp={quote(i)}")["rows"]
        api = {(c, i, r["label"]): r["m"] for r in rows}
        for k, want in pain.items():
            if k[:2] == (c, i):
                assert k in api, f"pain {k} missing"
                check_row(api[k], want, f"pain {k}")
                n_pain += 1
    assert n_icp == len(icp) and n_pain == len(pain)
    t = got["total"]["m"]
    assert t["spend"] == pytest.approx(total[2], abs=1.0) and t["purchases"] == total[3] and t["live_now"] == total[1]
    assert t["unique_ads"] in (int(total[0]), int(total[0]) + 1)
    # rows add up to the total (no family is split across clusters except by design)
    assert sum(r["m"]["spend"] for r in got["rows"]) == pytest.approx(t["spend"], abs=0.02)
    assert sum(r["m"]["spend_share"] for r in got["rows"]) == pytest.approx(1.0, abs=1e-4)


def test_snapshot_totals_row_matches(rc):
    _, _, _, total = snapshot_tables()
    m = vita_bull(rc)["m"]
    assert m["spend"] == pytest.approx(total[2], abs=1.0) and m["purchases"] == total[3] and m["live_now"] == total[1]


# ------------------------------------------------------------------------------------------------------ latency + internal consistency
URLS = [
    "/api/products", "/api/products?window=7d&compare=prev", "/api/products/vita-bull/rollup?level=cluster&window=30d&compare=prev&stage=testing,scaling",
    "/api/products/vita-bull/families", "/api/products/vita-bull/families?window=14d&compare=7d",
    "/api/products/vita-bull/ads?window=30d", "/api/products/vita-bull/campaigns?window=30d&compare=prev", "/api/products/vita-bull/stage-daily?days=90",
    "/api/products/vita-bull/unmatched?window=30d", "/api/products/vita-bull/tree?root=cluster&depth=3",
    "/api/products/vita-bull/tree?root=cluster&depth=3&window=30d&compare=prev&stage=scaling,unclassified",
    "/api/products/beef-liver/tree?root=cluster&depth=3&window=7d", "/api/products/boneflex/families", "/api/products/beef-liver/rollup?level=cluster",
]


@pytest.mark.parametrize("url", URLS)
def test_latency_budget(rc, url):
    get(rc, url)                      # warm
    get(rc, url)                      # asserts < 300 ms


def test_tree_matches_rollup_on_real_data(rc):
    t = get(rc, "/api/products/vita-bull/tree?root=cluster&depth=3&window=30d&compare=prev")
    roll = get(rc, "/api/products/vita-bull/rollup?level=cluster&window=30d&compare=prev")
    assert t["total"]["m"] == roll["total"]["m"]
    assert [r["m"] for r in t["rows"] if r["level"] == 0] == [r["m"] for r in roll["rows"]]
    fams = [r for r in t["rows"] if r["level"] == 3]
    assert 1300 <= len(fams) <= 1600
    assert sum(r["m"]["spend"] for r in fams) == pytest.approx(t["total"]["m"]["spend"], abs=0.02 * len(fams) ** 0.5 + 0.1)
    assert sum(r["m"]["spend_share"] for r in t["rows"] if r["level"] == 0) == pytest.approx(1.0, abs=1e-4)


def test_stage_lists_partition_the_window(rc):
    whole = get(rc, "/api/products/vita-bull/rollup?window=30d")["total"]["m"]
    parts = [get(rc, f"/api/products/vita-bull/rollup?window=30d&stage={s}")["total"]["m"] for s in ("testing", "scaling", "unclassified")]
    assert sum(p["spend"] for p in parts) == pytest.approx(whole["spend"], abs=0.02)
    assert sum(p["revenue"] for p in parts) == pytest.approx(whole["revenue"], abs=0.02)
    assert whole["stage"]["testing"]["spend"] == pytest.approx(parts[0]["spend"], abs=0.02)
    both = get(rc, "/api/products/vita-bull/rollup?window=30d&stage=testing,scaling")["total"]["m"]
    assert both["spend"] == pytest.approx(parts[0]["spend"] + parts[1]["spend"], abs=0.02)


def test_all_products_listed_with_sane_numbers(rc):
    prods = get(rc, "/api/products")
    assert [p["slug"] for p in prods][:3] == ["vita-bull", "beef-liver", "boneflex"]
    for p in prods:
        m = p["m"]
        assert m["spend"] >= 0 and (m["roas"] is None or m["roas"] >= 0)
    assert sum(p["m"]["spend_share"] or 0 for p in prods) == pytest.approx(1.0, abs=1e-4)


@pytest.mark.parametrize("window", ["window=launch", "window=30d", "window=7d&compare=prev"])
def test_ad_rows_add_up_to_family_totals_on_real_data(rc, window):
    with psycopg.connect(REAL_URL) as c:       # /ads reads ad_daily, /families reads family_daily: they only agree once build_metrics ran after the last pull
        fd = c.execute("SELECT COALESCE(sum(spend), 0) FROM family_daily").fetchone()[0]
        ad = c.execute("""SELECT COALESCE(sum(d.spend), 0) FROM ad_daily d JOIN meta_family_map m ON m.meta_family_key = d.meta_family_key
                          WHERE m.tracker_family_key IS NOT NULL""").fetchone()[0]
    if abs(fd - ad) > 1:
        pytest.skip(f"ETL pull in progress: family_daily {fd} != mapped ad_daily {ad} (rebuild with `python -m etl.build_metrics`)")
    ads = get(rc, f"/api/products/vita-bull/ads?{window}", budget=2000)
    fams = get(rc, f"/api/products/vita-bull/families?{window}", budget=2000)
    tracker_side = [r for r in ads["rows"] if r["on_tracker"]]
    assert sum(r["spend"] for r in tracker_side) == pytest.approx(fams["total"]["m"]["spend"], abs=1.0)
    assert sum(r["revenue"] for r in tracker_side) == pytest.approx(fams["total"]["m"]["revenue"], abs=1.0)
    off = [r for r in ads["rows"] if not r["on_tracker"]]
    assert off, "Meta ads that are not on the tracker are part of the unfiltered view"
    if "compare" in window:
        assert sum(r["cmp"]["spend"] for r in tracker_side) == pytest.approx(fams["total"]["m"]["cmp"]["spend"], abs=1.0)
    # one family drilled the way the UI does it (tree row -> /ads with the same filters)
    top = max((r for r in fams["rows"] if r["counted_here"]), key=lambda r: r["m"]["spend"])
    one = get(rc, f"/api/products/vita-bull/ads?family_key={quote(top['family_key'])}&{window}")
    assert one["total"]["spend"] == pytest.approx(top["m"]["spend"], abs=0.02)
