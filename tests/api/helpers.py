"""Shared test helpers: window table, comparisons of API output against the oracle."""
from __future__ import annotations

import datetime as dt
from collections import defaultdict

import pytest

from . import fixture_data as F

THROUGH = F.THROUGH


def days_ago(n: int) -> dt.date:
    return THROUGH - dt.timedelta(days=n)


# (query string, oracle window): 'launch' or (from, to) after clamping to the data span
WINDOWS = [
    ("window=launch", "launch"),
    ("", "launch"),
    ("window=1d", (THROUGH, THROUGH)),
    ("window=7d", (days_ago(6), THROUGH)),
    ("window=14d", (days_ago(13), THROUGH)),
    ("window=30d", (days_ago(29), THROUGH)),
    ("window=60d", (days_ago(59), THROUGH)),
    ("window=90d", (days_ago(89), THROUGH)),
    ("window=180d", (days_ago(179), THROUGH)),
    ("window=mtd", (dt.date(2026, 11, 1), THROUGH)),
    ("from=2026-10-10&to=2026-10-20", (dt.date(2026, 10, 10), dt.date(2026, 10, 20))),
    ("from=2026-10-05&to=2026-10-05", (dt.date(2026, 10, 5), dt.date(2026, 10, 5))),
    ("from=2026-04-01&to=2026-05-20", (F.D0, dt.date(2026, 5, 20))),          # clamped at the start
    ("from=2026-10-25&to=2027-01-01", (dt.date(2026, 10, 25), THROUGH)),     # clamped at the end
]
STAGE_PARAMS = [("", "all"), ("stage=testing", "testing"), ("stage=scaling", "scaling")]


def qs(*parts: str) -> str:
    return "&".join(p for p in parts if p)


def get_ok(client, url: str) -> dict | list:
    r = client.get(url)
    assert r.status_code == 200, f"{url} -> {r.status_code} {r.get_data(as_text=True)[:300]}"
    return r.get_json()


def close(a, b, tol=0.006) -> bool:
    return abs(float(a) - float(b)) <= tol


def ratio(a, b, nd):
    return None if float(b) == 0 else round(float(a) / float(b), nd)


def check_m(m: dict, agg: dict, ctx: str = "") -> None:
    """API `m` object vs oracle aggregate (F.aggregate)."""
    def eq(name, got, want, tol=0.006):
        assert got is not None and close(got, want, tol), f"{ctx} {name}: api {got} != oracle {want}"

    assert m["unique_ads"] == agg["unique"], f"{ctx} unique_ads {m['unique_ads']} != {agg['unique']}"
    assert m["live_now"] == agg["live"], f"{ctx} live_now {m['live_now']} != {agg['live']}"
    assert m["active_ads"] == agg["active"], f"{ctx} active_ads {m['active_ads']} != {agg['active']}"
    eq("spend", m["spend"], agg["spend"])
    eq("purchases", m["purchases"], agg["purchases"])
    eq("revenue", m["revenue"], agg["revenue"])
    eq("spend_7d", m["spend_7d"], agg["s7"])
    eq("purchases_7d", m["purchases_7d"], agg["p7"])
    assert m["roas"] == pytest.approx(ratio(agg["revenue"], agg["spend"], 4), abs=1e-4) if agg["spend"] else m["roas"] is None, ctx
    assert (m["cpa"] is None) == (agg["purchases"] == 0), ctx
    if agg["purchases"]:
        assert m["cpa"] == pytest.approx(float(agg["spend"] / agg["purchases"]), abs=0.006), ctx
    assert (m["cpa_7d"] is None) == (agg["p7"] == 0), ctx
    for key, st in (("t", "testing"), ("s", "scaling")):
        sm = m["stage"][st]
        eq(f"{st}.spend", sm["spend"], agg[key][0])
        eq(f"{st}.purchases", sm["purchases"], agg[key][1])
        eq(f"{st}.revenue", sm["revenue"], agg[key][2])
    um = m["stage"]["unclassified"]
    eq("unclassified.spend", um["spend"], agg["u"][0])
    eq("unclassified.purchases", um["purchases"], agg["u"][1])
    eq("unclassified.revenue", um["revenue"], agg["u"][2])
    cls = float(agg["t"][0] + agg["s"][0])
    if cls:
        assert m["testing_pct"] == pytest.approx(float(agg["t"][0]) / cls, abs=1e-4), ctx
    else:
        assert m["testing_pct"] is None, ctx


def rows_by_product(fx, slug):
    return [r for r in fx.tracker if r["product"] == slug]


def group_rows(rows, level_cols):
    """tracker rows -> {label tuple: [rows]} using the rows' (cluster, icp, pain) tag labels."""
    out = defaultdict(list)
    for r in rows:
        lab = F.tag_labels(r)
        out[tuple(lab[: len(level_cols)])].append(r)
    return out
