import json
import os
import sys
from pathlib import Path

import psycopg
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

TEST_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://localhost/tagexp_test")
DATA_TABLES = ("ad_tags, ad_metrics, tracker_rows, taxonomy_lines, line_ingredients, ad_daily, meta_campaigns, ad_lifetime, meta_family_map, meta_ads, "
               "meta_ad_lifetime, family_content, creative_assets, unmatched_meta, app_meta, etl_runs, family_daily")


@pytest.fixture(scope="session")
def dburl():
    os.environ["DATABASE_URL"] = TEST_URL
    with psycopg.connect(TEST_URL, autocommit=True) as c:
        c.execute("DROP SCHEMA public CASCADE")
        c.execute("CREATE SCHEMA public")
    from etl import migrate
    with psycopg.connect(TEST_URL) as c:
        migrate.migrate(c)
    return TEST_URL


@pytest.fixture
def conn(dburl):
    c = psycopg.connect(dburl)
    c.execute(f"TRUNCATE {DATA_TABLES} RESTART IDENTITY CASCADE")
    c.commit()
    yield c
    c.rollback()
    c.close()


# ---- tiny tracker / sheet fixtures ----
TRACKER_HDR = ["Brief Date", "Asset URL", "Concept ID", "Concept Name", "Status", "Launch Date", "Type", "Pain Point",
               "ICP - Target Avatar (Who are we trying to talk to?) ", "Product", "Style", "Creative Fidelity", "Vendor", "Proof Mechanism",
               "Name", "Primary Text", "Ad Family", "Transcript", "Primary Text 2", "Primary Text 3", "Ad Headline 1", "Ad Headline 2", "Ad Headline 3",
               "Ad Description", "ICP (legacy)", "Emotion", "Market Cluster", "Lead Ingredient", "Short Description"]


def trow(concept, family, product, launch="", status="8 - Launched", pain="", icp="", cluster="", **kw):
    d = {"Concept ID": concept, "Ad Family": family, "Product": product, "Launch Date": launch, "Status": status,
         "Pain Point": pain, "ICP - Target Avatar (Who are we trying to talk to?) ": icp, "Market Cluster": cluster}
    d.update(kw)
    return [d.get(h, "") for h in TRACKER_HDR]


def serial(iso):
    import datetime as dt
    return (dt.date.fromisoformat(iso) - dt.date(1899, 12, 30)).days


def write_snapshot(tmp_path: Path, tracker_rows, daily, lifetime=(), windows=(), overview=None):
    """daily: (iso_day, family, spend, purchases, conv, clicks); lifetime: (tracker_family, meta_name, ad_id, spend, purchases, conv);
    windows: (meta_family, tracker_family)."""
    (tmp_path / "creative_tracker.json").write_text(json.dumps([TRACKER_HDR] + list(tracker_rows)))
    (tmp_path / "meta_daily.json").write_text(json.dumps(
        [["cap"], ["Date", "Ad Family", "Spend", "Purchases", "Conversion Value", "Ads", "Link Clicks"]] +
        [[serial(d), f, s, p, c, 1, cl] for d, f, s, p, c, cl in daily]))
    (tmp_path / "meta_lifetime.json").write_text(json.dumps(
        [["cap"], ["Ad Family", "Meta Ad Name", "Ad ID", "Ad Account", "Created", "Ad Status", "Spend", "Purchases", "Conversion Value"]] +
        [[f, n, i, "Lonvera Main", serial("2026-01-01"), "ACTIVE", s, p, c] for f, n, i, s, p, c in lifetime]))
    (tmp_path / "meta_windows.json").write_text(json.dumps(
        [["cap"], ["Ad Family", "Tracker Family"] + ["x"] * 26] + [[a, b] + [0] * 26 for a, b in windows]))
    if overview is not None:
        (tmp_path / "communication_overview.json").write_text(json.dumps(overview))
    return tmp_path
