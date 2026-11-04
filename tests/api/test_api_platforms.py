"""Platform level (Meta): the all-products Campaigns view (/api/campaigns) and the All platforms overview (/api/platforms/overview).
The Campaigns view is the per-product Campaigns view summed over products, so every row and the total must tie to those and to the Products table."""
import pytest

from .helpers import THROUGH, days_ago, get_ok, qs

DATE_WINDOWS = ["window=7d", "window=14d", "window=30d", "from=2026-10-10&to=2026-10-20"]
STAGES = ["", "stage=testing", "stage=scaling"]
KEYS = ("spend", "purchases", "revenue", "impressions", "link_clicks")


def products(client, extra=""):
    return get_ok(client, "/api/products?" + qs("envelope=1", extra))


def product_slugs(client):
    return [p["slug"] for p in products(client, "window=30d")["rows"]]


@pytest.mark.parametrize("w", DATE_WINDOWS)
@pytest.mark.parametrize("s", STAGES)
def test_total_ties_to_the_products_table_all_spend(client, w, s):
    d = get_ok(client, "/api/campaigns?" + qs(w, s))
    t = d["total"]
    all_ = products(client, qs(w, s))["total_all"]["m"]
    assert t["spend"] == pytest.approx(all_["spend"], abs=0.011)
    assert t["purchases"] == pytest.approx(all_["purchases"], abs=0.011)
    assert t["revenue"] == pytest.approx(all_["revenue"], abs=0.011)
    assert t["spend"] == pytest.approx(sum(r["spend"] for r in d["rows"]), abs=0.011)
    assert d["platform"] == "meta" and d["scope"] == "tracker+unmatched" and d["product"] is None


@pytest.mark.parametrize("w", ["window=7d", "window=30d"])
def test_rows_equal_the_per_product_views_summed(client, w):
    d = get_ok(client, "/api/campaigns?" + w)
    summed: dict[str, dict] = {}
    for slug in product_slugs(client):
        per = get_ok(client, f"/api/products/{slug}/campaigns?{w}")
        for r in per["rows"]:
            acc = summed.setdefault(r["campaign_id"], {k: 0.0 for k in KEYS} | {"ads": 0, "name": r["name"], "stage": r["stage"], "account": r["account"]})
            for k in KEYS:
                acc[k] += r[k] or 0.0                                                       # None = no traffic data for that row
            acc["ads"] += r["ads"]
    got = {r["campaign_id"]: r for r in d["rows"]}
    assert set(got) == set(summed)
    for cid, want in summed.items():
        g = got[cid]
        for k in KEYS:
            assert (g[k] or 0.0) == pytest.approx(want[k], abs=0.011), (cid, k)
        assert g["ads"] == want["ads"] and g["name"] == want["name"] and g["stage"] == want["stage"] and g["account"] == want["account"]


def test_product_filter_is_that_products_campaigns_view(client):
    for slug in product_slugs(client):
        per = get_ok(client, f"/api/products/{slug}/campaigns?window=30d")
        d = get_ok(client, f"/api/campaigns?window=30d&product={slug}")
        assert d["product"]["slug"] == slug
        assert {r["campaign_id"] for r in d["rows"]} == {r["campaign_id"] for r in per["rows"]}
        assert d["total"]["spend"] == pytest.approx(per["total"]["spend"], abs=0.011)
        by = {r["campaign_id"]: r for r in per["rows"]}
        for r in d["rows"]:
            assert r["spend"] == pytest.approx(by[r["campaign_id"]]["spend"], abs=0.011)
            assert r["product_slug"] == slug and r["product"] != "Mixed"            # one product's ads only: the row belongs to that product


def test_product_column_names_the_product_or_says_mixed(client):
    d = get_ok(client, "/api/campaigns?window=30d")
    assert d["rows"], "fixture has campaigns"
    mixed = [r for r in d["rows"] if r["product"] == "Mixed"]
    single = [r for r in d["rows"] if r["product"] != "Mixed"]
    assert mixed and single                                                           # the fixture has shared campaigns (C100 / C200 carry a second product's ads) and single-product ones
    for r in d["rows"]:
        assert r["products"] and round(sum(p["share"] for p in r["products"]), 3) == pytest.approx(1.0, abs=0.002)
        assert sum(p["spend"] for p in r["products"]) == pytest.approx(r["spend"], abs=0.011)
        if r["product"] == "Mixed":
            assert len(r["products"]) > 1 and r["product_slug"] is None
        else:
            assert len(r["products"]) == 1 and r["products"][0]["name"] == r["product"]


def test_product_chips_list_every_active_product(client):
    d = get_ok(client, "/api/campaigns?window=30d")
    slugs = [p["slug"] for p in d["products"]]
    assert set(product_slugs(client)) <= set(slugs)                                   # every product with spend, in the Products table's order
    assert sum(p["spend"] for p in d["products"]) == pytest.approx(d["total"]["spend"], abs=0.02)      # chips are not narrowed by the product filter
    f = get_ok(client, f"/api/campaigns?window=30d&product={slugs[0]}")
    assert [p["slug"] for p in f["products"]] == slugs


def test_a_product_called_unassigned_is_a_normal_product_and_none_is_families_of_no_product(client):
    d = get_ok(client, "/api/campaigns?window=30d&product=unassigned")                 # the fixture (and production) has a real product with this slug
    assert d["product"]["slug"] == "unassigned" and d["rows"] and all(r["product_slug"] == "unassigned" for r in d["rows"])
    n = get_ok(client, "/api/campaigns?window=30d&product=none")
    assert n["product"]["slug"] == "none" and n["rows"] == []                          # every family here belongs to an active product
    r = client.get("/api/campaigns?window=30d&product=no-such-product")
    assert r.status_code == 404


def test_compare_and_shares(client):
    d = get_ok(client, "/api/campaigns?window=7d&compare=prev")
    assert d["compare_info"] is not None and d["total"]["cmp"]["spend"] >= 0
    for r in d["rows"]:
        assert "cmp" in r and "delta" in r
    assert round(sum(r["spend_share"] for r in d["rows"]), 4) == pytest.approx(1.0, abs=0.001)
    cmp_all = products(client, "window=7d&compare=prev")["total_all"]["m"]
    assert d["total"]["cmp"]["spend"] == pytest.approx(cmp_all["cmp"]["spend"], abs=0.011)


def test_active_only_is_a_subset(client):
    full = get_ok(client, "/api/campaigns?window=30d")
    act = get_ok(client, "/api/campaigns?window=30d&active=1")
    assert act["total"]["spend"] <= full["total"]["spend"] + 0.011
    assert {r["campaign_id"] for r in act["rows"]} <= {r["campaign_id"] for r in full["rows"]}
    per_total = sum(get_ok(client, f"/api/products/{s}/campaigns?window=30d&active=1")["total"]["spend"] for s in product_slugs(client))
    unm = products(client, "window=30d&active=1")["total_all"]["m"]["spend"]
    assert act["total"]["spend"] == pytest.approx(unm, abs=0.011) or act["total"]["spend"] >= per_total - 0.011


def test_since_launch_means_every_day_held(client):
    d = get_ok(client, "/api/campaigns?window=launch")
    assert "Since launch does not apply" in d["window_info"]["note"] and d["rows"]


def test_spark_series_for_rows_and_total(client):
    d = get_ok(client, "/api/campaigns?" + qs("window=7d", "spark=spend", "spark_days=7"))
    assert d["total"]["spark"] and all(r["spark"] for r in d["rows"])
    flt = get_ok(client, f"/api/campaigns?window=7d&spark=spend&spark_days=7&product={product_slugs(client)[0]}")
    assert flt["total"]["spark"] and flt["total"]["spark"] != d["total"]["spark"]


def test_overview_meta_row_is_the_products_table_total(client):
    d = get_ok(client, "/api/platforms/overview?window=30d")
    meta = next(p for p in d["platforms"] if p["key"] == "meta")
    reported = next(r for r in meta["rows"] if r["basis"] == "reported")
    all_ = products(client, "window=30d")["total_all"]["m"]
    assert reported["m"]["spend"] == pytest.approx(all_["spend"], abs=0.011) and reported["m"]["purchases"] == pytest.approx(all_["purchases"], abs=0.011)
    assert reported["labels"]["purchases"] == "Purchases" and reported["window"]["from"] == str(days_ago(29)) and reported["window"]["to"] == str(THROUGH)
    assert d["compare_info"] is None and [p["key"] for p in d["platforms"]] == ["meta", "google"]


def test_overview_ignores_stage_active_and_compare(client):
    a = get_ok(client, "/api/platforms/overview?window=30d")
    b = get_ok(client, "/api/platforms/overview?window=30d&stage=testing&active=1&compare=prev")
    assert a == b


def test_status_lists_the_platforms(client):
    s = get_ok(client, "/api/status")
    plats = {p["key"]: p for p in s["sources"]["platforms"]}
    assert set(plats) == {"meta", "google"} and plats["meta"]["available"] is True and plats["meta"]["through"] == str(THROUGH)
