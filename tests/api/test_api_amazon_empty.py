"""Amazon pages before the backfill has run: every table is empty, so every route answers 200 with loaded=false (the page shows an empty state), never an error."""
import psycopg
import pytest

from .conftest import DB_URL
from .helpers import get_ok
from .test_api_amazon import TABLES

ROUTES = ["overview", "trend", "campaigns", "campaign-trend?campaign_id=c1", "products", "search-terms", "search-terms/history?term=beef+liver", "fees"]


@pytest.fixture(scope="module")
def empty(fx):
    with psycopg.connect(DB_URL) as c:
        for t in TABLES:
            c.execute(f"TRUNCATE {t}")
        c.commit()


@pytest.mark.parametrize("route", ROUTES)
def test_empty_tables_give_an_empty_state_not_an_error(empty, client, route):
    sep = "&" if "?" in route else "?"
    d = get_ok(client, f"/api/amazon/{route}{sep}window=30d")
    assert d["loaded"] is False and d["message"] == "Amazon data has not loaded yet" and d["window_info"] is None and d["channel"] == "amazon"


def test_status_says_amazon_is_not_available_yet(empty, client):
    s = get_ok(client, "/api/status")["sources"]
    assert s["amazon"] == {"available": False, "through": None, "first_day": None}
