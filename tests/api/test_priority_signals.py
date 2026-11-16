"""Per-cut signals for the priority score (app/priority_signals.py) on the fixture database: window sums on the Meta spend / Upstack new-customer basis, the scoring hit rate
(14 to 180 days old, $100 tested, $1,000 hit), launches and days since the last launch, live ads, concentration, the open pipeline, the product-mix expectation, product refused as
a level, and the hand-off to priority_score.score_cuts."""
import datetime as dt

import psycopg
import pytest
from psycopg.rows import dict_row

from app import priority_score as ps, priority_signals as sig, queries as q

from .conftest import DB_URL
from .test_api_priorities_assign import LINE_ROW, clean, one, snaps          # noqa: F401  (fixtures: snaps, clean)

AS_OF = dt.date(2026, 10, 31)
FAMS = {  # family: (tracker row id, icp, ad type, [(day, spend, nc purchases, nc revenue)])
    "zz-ps-a": (9401, "Given Up Guy", "VID", [(dt.date(2026, 8, 1), 600, 3, 300), (dt.date(2026, 9, 20), 500, 2, 250), (dt.date(2026, 10, 30), 400, 2, 220)]),
    "zz-ps-b": (9402, "Given Up Guy", "IMG", [(dt.date(2026, 10, 10), 150, 1, 60)]),
    "zz-ps-c": (9403, "ZZ Other Guy", "VID", [(dt.date(2026, 10, 20), 50, 0, 0)]),
}
FILTERS = {"icp": ["given up guy", "zz other guy"]}


@pytest.fixture()
def ads(pg):
    pid = pg.execute("SELECT id FROM products WHERE slug = 'vita-bull'").fetchone()[0]
    for fam, (rid, icp, typ, days) in FAMS.items():
        pg.execute("INSERT INTO tracker_rows (id, product_id, row_key, concept_id, ad_family, family_key, sheet_row, status, ad_type, raw) VALUES (%s,%s,%s,%s,%s,%s,%s,'8 - Launched',%s,'{}')",
                   (rid, pid, f"ZZ-PS-{rid}#1", f"ZZ-PS-{rid}", fam, fam, 90000 + rid, typ))
        pg.execute("INSERT INTO ad_tags (tracker_row_id, tag_set, version, is_current, cluster, icp, pain_point) VALUES (%s,'linemap','zz',true,'Energy',%s,'Low T')", (rid, icp))
        for day, sp, ncp, ncr in days:
            pg.execute("INSERT INTO family_daily (family_key, day, stage, spend, purchases, revenue) VALUES (%s,%s,'testing',%s,%s,%s)", (fam, day, sp, ncp + 1, ncr + 50))
            pg.execute("INSERT INTO family_daily_upstack_nc (family_key, day, stage, spend, purchases, revenue) VALUES (%s,%s,'testing',%s,%s,%s)", (fam, day, sp, ncp, ncr))
    pg.commit()
    yield
    for t in ("family_daily", "family_daily_upstack_nc"):
        pg.execute(f"DELETE FROM {t} WHERE family_key = ANY(%s)", (list(FAMS),))
    pg.execute("DELETE FROM tracker_rows WHERE id = ANY(%s)", ([v[0] for v in FAMS.values()],))
    pg.commit()


@pytest.fixture()
def dconn(fx):
    """A dict-row connection, as the app's pool hands out."""
    with psycopg.connect(DB_URL, row_factory=dict_row) as c:
        yield c


def run(conn, group, **kw):
    return sig.signals(conn, group, to=AS_OF, filters=FILTERS, **kw)


def by_icp(out, icp):
    return next(r for r in out["rows"] if r["labels"].get("icp") == icp)


def test_window_sums_hits_launches_and_live(dconn, ads):
    out = run(dconn, "icp")
    g = by_icp(out, "Given Up Guy")
    assert g["spend_30d"] == 550 and g["nc_revenue_30d"] == 280 and g["nc_purchases_30d"] == 3     # Oct 2 .. Oct 31: b 150 + a 400 (Upstack NC basis, not the Meta revenue)
    assert g["spend_prior30"] == 500 and g["nc_revenue_prior30"] == 250                             # Sep 2 .. Oct 1
    assert g["spend_7d"] == 400 and g["nc_roas_30d"] == pytest.approx(280 / 550)
    assert g["judged_ads"] == 2 and g["hit_ads"] == 1                                               # a: $1,500 to date (hit); b: $150, 21 days old (tested, no hit)
    assert g["launches_60d"] == 1 and g["launches_30d"] == 1 and g["days_since_launch"] == 21 and g["last_launch_day"] == "2026-10-10"
    assert g["live_ads"] == 1 and g["ads_spending_30d"] == 2                                       # a spent on Oct 30 (the day before as-of)
    assert g["top_ad_share_30d"] == pytest.approx(400 / 550) and g["old_spend_share_30d"] == pytest.approx(400 / 550)   # a is 91 days old
    c = by_icp(out, "ZZ Other Guy")
    assert c["judged_ads"] == 0 and c["spend_30d"] == 50
    assert g["spend_share_30d"] == pytest.approx(550 / 600) and g["launch_share_60d"] == pytest.approx(1 / 2)
    # one product in scope -> the product-mix expectation is that product's own 30-day NC ROAS
    assert g["expected_nc_roas_30d"] == pytest.approx(280 / 600) and g["expected_hit_rate"] == pytest.approx(1 / 2)


def test_days_after_as_of_are_ignored(dconn, ads):
    early = sig.signals(dconn, "icp", to=dt.date(2026, 9, 30), filters=FILTERS)
    g = by_icp(early, "Given Up Guy")
    assert g["spend_30d"] == 500 and g["launches_60d"] == 0 and g["days_since_launch"] == 60
    assert all(r["labels"]["icp"] != "ZZ Other Guy" or r["spend_30d"] == 0 for r in early["rows"])


def test_open_pipeline_joins_the_same_cut(dconn, ads, client, snaps):
    one(client)                                                                                    # one ticket on Energy / Given Up Guy / Low T (LINE_ROW)
    g = by_icp(run(dconn, "icp"), "Given Up Guy")
    assert g["pipeline_open"] == 1 and g["oldest_ticket_days"] is not None
    deep = run(dconn, ["cluster", "icp", "pain_point"])
    assert by_icp(deep, "Given Up Guy")["labels"] == {"cluster": "Energy", "icp": "Given Up Guy", "pain_point": "Low T"}


def test_media_level_splits_image_and_video(dconn, ads):
    out = run(dconn, "icp,media")
    vid = next(r for r in out["rows"] if r["labels"] == {"icp": "Given Up Guy", "media": "Video"})
    img = next(r for r in out["rows"] if r["labels"] == {"icp": "Given Up Guy", "media": "Image"})
    assert vid["spend_30d"] == 400 and img["spend_30d"] == 150


def test_product_is_not_a_scoring_level():
    with pytest.raises(q.ApiError):
        sig.parse_score_group("product")
    assert sig.parse_score_group("product,icp") == ["icp"]


def test_signals_feed_the_score(dconn, ads):
    scored = ps.score_cuts(run(dconn, "icp")["rows"])
    assert {o["labels"]["icp"] for o in scored} >= {"Given Up Guy", "ZZ Other Guy"}
    g = next(o for o in scored if o["labels"]["icp"] == "Given Up Guy")
    assert 0 <= g["score"] <= 100 and g["confidence"] == "low" and g["why"]
    assert scored[-1]["labels"]["icp"] == "ZZ Other Guy" and scored[-1]["tier"] == "Untested"
