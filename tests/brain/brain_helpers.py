"""Shared constants and helpers for the brain tests (a plain module: tests/brain is not a package, so it cannot use relative imports)."""
from __future__ import annotations

import glob
import json
import os
from pathlib import Path

import psycopg
from werkzeug.security import generate_password_hash

ROOT = Path(__file__).resolve().parents[2]
PG_BASE = os.environ.get("TEST_PG_BASE", "postgresql://localhost")
DB_NAME = os.environ.get("TEST_API_DB", "tagexp_apitest") + "_brain"
DB_URL = f"{PG_BASE}/{DB_NAME}"
PASSWORD = "correct horse battery"
USERS = {"JoshHoughton": generate_password_hash(PASSWORD, method="scrypt:1024:8:1"), "Lazer": generate_password_hash("staple battery horse", method="scrypt:1024:8:1")}

SCHEMA = {
    "groups": [{"key": "analytics", "title": "analytics schema"}, {"key": "meta", "title": "Meta ads"}],
    "relations": [
        {"name": "analytics.meta_ad_daily", "schema": "analytics", "relation": "meta_ad_daily", "kind": "view", "group": "analytics", "domain": "ads",
         "purpose": "One row per Meta ad per day with spend and Meta-reported purchases. Use it for ad level ROAS.", "grain": "ad x day", "source": "public.ad_daily",
         "refresh": "nightly", "coverage": "2026-05-06 onward", "caveats": ["Meta-reported counts returning customers."],
         "columns": [{"name": "day", "type": "date", "description": "Calendar day of the spend."}, {"name": "spend", "type": "numeric", "description": "Dollars spent."},
                     {"name": "meta_revenue", "type": "numeric", "description": "Meta-reported purchase revenue, gross."}],
         "examples": [{"title": "Spend by day", "sql": "select day, sum(spend) from analytics.meta_ad_daily group by 1"}]},
        {"name": "analytics.orders_daily", "schema": "analytics", "relation": "orders_daily", "kind": "view", "group": "analytics", "domain": "orders",
         "purpose": "Actual new customer revenue per day from Upstack.", "grain": "day", "source": "public.upstack_orders_daily", "refresh": "nightly", "coverage": "2025-01-01 onward",
         "caveats": [], "columns": [{"name": "day", "type": "date", "description": "Pacific day."}, {"name": "new_customer_revenue", "type": "numeric", "description": "Gross first-order revenue."}]},
        {"name": "public.ad_daily", "schema": "public", "relation": "ad_daily", "kind": "table", "group": "meta", "purpose": "Raw ad day table.", "grain": "ad x day", "source": "meta", "refresh": "nightly",
         "coverage": "all", "caveats": [], "columns": [{"name": "spend", "type": "numeric", "description": "raw spend"}]},
    ],
    "metrics": [
        {"name": "meta_roas", "label": "Meta ROAS", "formula": "sum(meta_revenue) / sum(spend)", "source": "analytics.meta_ad_daily", "default_basis": "meta", "other_bases": ["upstack_nc"],
         "traps": "Counts returning customers; never mix with Upstack revenue.", "domain": "ads"},
        {"name": "nc_roas", "label": "New customer ROAS", "formula": "orders_daily.new_customer_revenue / total spend", "source": "analytics.orders_daily", "default_basis": "upstack_nc",
         "other_bases": [], "traps": "Ratio of sums, not an average of daily ratios.", "domain": "orders"},
        {"name": "cpa", "label": "Cost per acquisition", "formula": "spend / purchases", "source": "analytics.meta_ad_daily", "default_basis": "meta", "other_bases": [], "traps": "Name the basis.", "domain": "ads"},
    ],
}



def make_app(**overrides):
    from app import create_app
    cfg = {"DATABASE_URL": DB_URL, "APP_USERS": dict(USERS), "SECRET_KEY": "test-secret-key", "DEV_INSECURE_COOKIES": True, "LOGIN_MAX_FAILS": 5, "DB_POOL_MAX": 4, "RESPONSE_CACHE_MB": 0}
    cfg.update(overrides)
    return create_app(cfg)


def login(app, user="JoshHoughton", pw=PASSWORD):
    c = app.test_client()
    assert c.post("/api/login", json={"username": user, "password": pw}).status_code == 204
    return c


