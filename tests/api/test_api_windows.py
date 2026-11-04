import datetime as dt

import pytest

from app import queries as q

from .helpers import get_ok

META = q.DataMeta(dt.date(2026, 5, 6), dt.date(2026, 11, 2))


def parse(**kw):
    return q.parse_window(kw, META)


def test_default_is_launch():
    w = parse()
    assert w.kind == "launch" and w.stage == "all" and w.date_from is None and not w.clamped
    info = w.info(META)
    assert info["from"] is None and info["to"] is None and info["label"] == "Since launch"
    assert info["daily_start"] == "2026-05-06" and info["data_through"] == "2026-11-02"


@pytest.mark.parametrize("name,n", [("1d", 1), ("7d", 7), ("14d", 14), ("30d", 30), ("60d", 60), ("90d", 90), ("180d", 180)])
def test_trailing(name, n):
    w = parse(window=name)
    assert w.kind == "trailing"
    assert w.date_to == dt.date(2026, 11, 2)
    assert w.date_from == dt.date(2026, 11, 2) - dt.timedelta(days=n - 1)
    assert w.days == n and not w.clamped and w.note is None


def test_labels():
    assert parse(window="7d").label == "Oct 27 – Nov 2 · 7 days"
    assert parse(window="1d").label == "Nov 2 · 1 day"
    assert parse(window="mtd").label == "Nov 1 – Nov 2 · 2 days · MTD"
    assert parse(**{"from": "2026-10-05", "to": "2026-10-05"}).label == "Oct 5 · 1 day"


def test_mtd():
    w = parse(window="mtd")
    assert (w.date_from, w.date_to, w.days, w.kind) == (dt.date(2026, 11, 1), dt.date(2026, 11, 2), 2, "mtd")


def test_custom_wins_over_window_and_single_day():
    w = parse(window="7d", **{"from": "2026-10-10", "to": "2026-10-12"})
    assert w.kind == "custom" and w.days == 3 and w.date_from == dt.date(2026, 10, 10)
    w = parse(**{"from": "2026-10-10", "to": "2026-10-10"})
    assert w.days == 1


def test_clamping():
    w = parse(**{"from": "2026-04-01", "to": "2026-05-10"})
    assert w.clamped and w.date_from == dt.date(2026, 5, 6) and w.date_to == dt.date(2026, 5, 10) and w.days == 5
    assert "May 6, 2026" in w.note
    w = parse(**{"from": "2026-10-30", "to": "2030-01-01"})
    assert w.clamped and w.date_to == dt.date(2026, 11, 2)
    # a trailing window longer than the data
    short = q.DataMeta(dt.date(2026, 10, 20), dt.date(2026, 11, 2))
    w = q.parse_window({"window": "30d"}, short)
    assert w.clamped and w.date_from == dt.date(2026, 10, 20) and w.days == 14 and "14 days instead of 30" in w.note


@pytest.mark.parametrize("args", [
    {"window": "bogus"}, {"window": "8d"}, {"from": "2026-10-10"}, {"to": "2026-10-10"}, {"from": "2026-10-12", "to": "2026-10-10"},
    {"from": "10/10/2026", "to": "2026-10-12"}, {"from": "2026-13-45", "to": "2026-10-12"}, {"from": "x", "to": "y"},
    {"from": "2026-01-01", "to": "2026-02-01"}, {"from": "2027-01-01", "to": "2027-02-01"}, {"stage": "both"}, {"stage": "Testing; DROP"},
])
def test_bad_input_raises_400(args):
    with pytest.raises(q.ApiError) as e:
        q.parse_window(args, META)
    assert e.value.status == 400


def test_stage_values():
    assert parse(stage="testing").stages == ["testing"]
    assert parse(stage="all").stages == ["testing", "scaling", "unclassified"]
    assert parse(window="launch", stage="scaling").note and "classified days" in parse(window="launch", stage="scaling").note


@pytest.mark.parametrize("path", ["rollup", "families", "ads", "campaigns", "unmatched"])
def test_http_400_on_bad_window(client, path):
    for bad in ("window=nope", "from=2026-10-12&to=2026-10-10", "from=2026-10-12", "stage=zzz", "from=a&to=b"):
        r = client.get(f"/api/products/vita-bull/{path}?{bad}")
        assert r.status_code == 400, (path, bad, r.status_code)
        assert "error" in r.get_json()
    assert client.get("/api/products?window=nope").status_code == 400
    assert client.get("/api/ads/1000?window=nope").status_code == 400
    assert client.get("/api/products/vita-bull/stage-daily?days=0").status_code == 400
    assert client.get("/api/products/vita-bull/stage-daily?days=abc").status_code == 400


def test_http_window_info_shape(client):
    d = get_ok(client, "/api/products/vita-bull/rollup?window=7d")
    for key in ("window", "window_info"):
        wi = d[key]
        assert wi["kind"] == "trailing" and wi["label"] == "Oct 27 – Nov 2 · 7 days"
        assert (wi["from"], wi["to"], wi["days"], wi["clamped"]) == ("2026-10-27", "2026-11-02", 7, False)
        assert wi["daily_start"] == "2026-05-06" and wi["data_through"] == "2026-11-02"
    d = get_ok(client, "/api/products/vita-bull/families?from=2026-04-01&to=2026-05-10")
    wi = d["window_info"]
    assert wi["kind"] == "custom" and wi["clamped"] is True and wi["from"] == "2026-05-06" and wi["note"]
    d = get_ok(client, "/api/products/vita-bull/ads?window=mtd&stage=testing")
    assert d["window_info"]["kind"] == "mtd" and d["window_info"]["stage"] == "testing"
    for ep in ("campaigns", "unmatched"):          # launch is treated as every day held
        d = get_ok(client, f"/api/products/vita-bull/{ep}")
        assert d["window_info"]["kind"] == "launch" and "every day held" in d["window_info"]["note"]
        assert d["window_info"]["from"] is None


def test_status(client):
    d = get_ok(client, "/api/status")
    assert d["data_through"] == "2026-11-02" and d["daily_start"] == "2026-05-06" and d["lifetime_cutoff"] is None      # no cutoff since v1.5
    assert d["last_refresh"]["job"] == "build_metrics" and d["last_refresh"]["status"] == "ok" and d["last_refresh"]["finished_at"]
    assert d["products"] == 5            # vita-bull, beef-liver, boneflex, vital-flow (Meta spend only), unassigned (Meta spend only)
