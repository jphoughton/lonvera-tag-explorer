"""Fix wave: the hourly curve loads itself and degrades to an estimate, payback is shown two ways, cost-per-customer context, degraded plan, month states, clock."""
import datetime as dt

import pytest

from app.projection.pacing import feeds, levers as lv, month as M, periods
from app.projection.pacing.targets import TargetBook
from etl import projection_pacing as ETL
from pacing_support import SNAP, SNAPSHOT_DAY
from snapshot_pnl import ROW_MAP

BASE = "/api/projection/pacing"


def nov(conn, pnl, cohorts, params=None, actual_through=None):
    fr = feeds.load_frame(conn, dt.date(2026, 10, 1), dt.date(2026, 11, 2))
    book = TargetBook(pnl, None, cohorts, row_map=ROW_MAP, levers=lv.parse(params or {}))
    ref = feeds.cpa_reference(conn, params or {}, lv.parse(params or {}).cpa_window_days, SNAPSHOT_DAY)
    return M.pacing_month(fr, dt.date(2026, 11, 1), SNAPSHOT_DAY, book, cohorts, params or {}, actual_through, ref), book


# ---------------------------------------------------------------------------------------------------------------- payback two ways

def test_payback_on_product_margin_and_after_all_variable_costs(conn, pnl, cohorts):
    res, book = nov(conn, pnl, cohorts)
    pb = res["payback"]
    assert pb["months"]["mtd"] == 10 and pb["months"]["goal"] == 5                                          # product margin: the sheet's Payback tab
    ld = pb["loaded"]
    assert ld["months"]["mtd"] == 18 and ld["months"]["goal"] == 8                                          # financing's loaded payback (the skeptic reads 19, the curve rounds)
    assert ld["variable_cost_rate"] == pytest.approx(0.3240, abs=1e-3) and ld["source"] == "P&L rates"
    assert [p["key"] for p in ld["parts"]] == ["china_cogs", "fulfillment_services", "inbound_shipping", "payment_processing", "chargeback_management"]
    assert ld["first_order_margin"]["mtd"] == pytest.approx(pb["aov"]["mtd"] * (1 - ld["variable_cost_rate"]))
    assert ld["months"]["mtd"] > pb["months"]["mtd"] and ld["months"]["goal"] > pb["months"]["goal"]


def test_pacing_uses_the_financing_modules_payback_functions(conn, pnl, cohorts):
    from app.projection.financing import calc as fin_calc
    res, book = nov(conn, pnl, cohorts)
    pb = res["payback"]
    curve = list(cohorts.curve_live)
    want = fin_calc.payback(pb["aov"]["mtd"], pb["cpa"]["mtd"], 0.1726, 5.53, curve, 36)
    assert pb["months"]["mtd"] == want.months and pb["first_order_margin"]["mtd"] == pytest.approx(want.cm_first)
    wl = fin_calc.payback(pb["aov"]["mtd"], pb["cpa"]["mtd"], 0.1726, 5.53, curve, 36, fully_loaded=True, loaded_rate=pb["loaded"]["variable_cost_rate"])
    assert pb["loaded"]["months"]["mtd"] == wl.months and pb["loaded"]["first_order_margin"]["mtd"] == pytest.approx(wl.cm_first)


def test_loaded_payback_follows_financings_levers(conn, pnl, cohorts):
    base, _ = nov(conn, pnl, cohorts)
    typed, _ = nov(conn, pnl, cohorts, {"financing": {"payback": {"loaded_rate": 0.20}}})
    assert typed["payback"]["loaded"]["source"] == "typed" and typed["payback"]["loaded"]["variable_cost_rate"] == 0.20
    assert typed["payback"]["loaded"]["months"]["mtd"] < base["payback"]["loaded"]["months"]["mtd"]
    fewer, _ = nov(conn, pnl, cohorts, {"financing": {"payback": {"loaded_lines": ["china_cogs", "fulfillment_services"]}}})
    assert fewer["payback"]["loaded"]["variable_cost_rate"] < base["payback"]["loaded"]["variable_cost_rate"]
    assert fewer["payback"]["months"]["mtd"] == base["payback"]["months"]["mtd"]                             # product margin is unchanged


def test_loaded_payback_is_unavailable_not_lower_when_a_cost_line_is_missing(conn, pnl, cohorts):
    import copy
    from app.projection.interfaces import PnlResult
    lines = {k: v for k, v in pnl.lines.items() if k != "payment_processing"}
    partial = PnlResult(lines=lines)
    partial.actual_through = pnl.actual_through
    fr = feeds.load_frame(conn, dt.date(2026, 10, 1), dt.date(2026, 11, 2))
    book = TargetBook(partial, None, cohorts, row_map=ROW_MAP)
    res = M.pacing_month(fr, dt.date(2026, 11, 1), SNAPSHOT_DAY, book, cohorts, {}, None)
    assert "loaded" not in res["payback"] and "payment_processing" in res["payback"]["loaded_unavailable"]
    assert res["payback"]["months"]["mtd"] == 10                                                             # the product-margin payback still shows
    bare = M.pacing_month(fr, dt.date(2026, 11, 1), SNAPSHOT_DAY, TargetBook(None, None, cohorts, row_map={}), cohorts, {}, None)
    assert "loaded" not in bare["payback"]
def test_payback_edges_follow_financings_calculator():
    curve = [0.5] * 36
    assert M.payback_months(100, 50, 0.0, 0.0, curve)["months"] == 0                                         # first order already pays for the customer
    assert M.payback_months(None, 50, 0.1, 5, curve)["label"] == "n/a" and M.payback_months(100, None, 0.1, 5, curve)["label"] == "n/a"
    assert M.payback_months(100, 1e6, 0.1, 5, curve, 4) == {"first_order_margin": 85.0, "months": None, "label": "4+"}
    loaded = M.fin_payback(100, 170, 0.1, 5, curve, 36, 0.30)                                                 # margin 70; cumulative 70 x (1 + 0.5 k) = 105, 140, 175
    assert loaded["first_order_margin"] == 70 and loaded["months"] == 3
    assert M.fin_payback(100, 170, 0.1, 5, curve, 36)["months"] == 2                                          # product margin 85; cumulative 85 x (1 + 0.5 k) = 127.5, 170


# ---------------------------------------------------------------------------------------------------------------- cost-per-customer context

@pytest.fixture()
def fed(clean_conn):
    """ad_daily filled from the snapshot's Meta days (the Spend plan page reads Meta spend there); rolled back after the test."""
    clean_conn.execute("INSERT INTO ad_daily (ad_id, day, meta_family_key, meta_family, campaign_id, spend, source) "
                       "SELECT 'seed-' || day, day, 'seed', 'seed', '', sum(spend), 'meta_api' FROM proj_feed_meta_day GROUP BY day")
    return clean_conn


def _window_cpa(conn, first, last):
    """Independent read of the same window: Meta (ad_daily) + Google spend over new customers in the orders feed."""
    meta = conn.execute("SELECT sum(spend) AS v FROM ad_daily WHERE day BETWEEN %s AND %s", (first, last)).fetchone()["v"]
    google = conn.execute("SELECT sum(spend) AS v FROM proj_feed_platform WHERE platform = 'Google Ads' AND day BETWEEN %s AND %s", (first, last)).fetchone()["v"]
    nc = conn.execute("SELECT sum(nc_orders) AS v FROM proj_feed_orders WHERE day BETWEEN %s AND %s", (first, last)).fetchone()["v"]
    return float(meta + google) / float(nc)


def test_28_day_cost_per_customer_sits_beside_the_months(fed, pnl, cohorts):
    res, _ = nov(fed, pnl, cohorts)
    by = {r["key"]: r for r in res["rows"]}
    c = by["nc_cpa"]["context"]
    first, last = dt.date(2026, 10, 5), dt.date(2026, 11, 1)                                                  # 28 complete days ending the day before the newest orders day
    assert c["label"] == "Last 28 days" and c["days"] == 28 and (c["from"], c["to"]) == (first.isoformat(), last.isoformat())
    assert c["value"] == pytest.approx(_window_cpa(fed, first, last), rel=1e-6) and c["status"] in ("bad", "warn", "good")
    assert c["pacing"] == pytest.approx(c["value"] / by["nc_cpa"]["goal"]) and by["nc_cpa2"]["context"] == c
    assert "context" not in by["nc_roas"] and by["nc_cpa"]["mtd"] != pytest.approx(c["value"])                  # the month's own figure is untouched


def test_28_day_cost_per_customer_is_the_spend_plan_pages_number(fed, pnl, cohorts):
    from app.projection.drivers import model as dm, reference as dref
    ref = dref.load_reference(fed, dm.parse_levers({}).ad_channels, 28, SNAPSHOT_DAY, dm.parse_levers({}))
    res, _ = nov(fed, pnl, cohorts)
    assert {r["key"]: r for r in res["rows"]}["nc_cpa"]["context"]["value"] == ref.cpa_window["cpa"]


def test_28_day_cost_is_withheld_with_the_reason_when_a_spend_feed_has_gaps(fed, pnl, cohorts):
    fed.execute("DELETE FROM ad_daily WHERE day BETWEEN '2026-10-10' AND '2026-10-18'")                         # 9 of the 28 days gone
    res, _ = nov(fed, pnl, cohorts)
    by = {r["key"]: r for r in res["rows"]}
    c = by["nc_cpa"]["context"]
    assert c["value"] is None and c["pacing"] is None and c["unavailable"] == "Meta spend feed has 19 of 28 days"
    assert by["nc_cpa"]["mtd"] and by["nc_cpa"]["goal"]                                                         # the month's own figure and goal still show


def test_28_day_cost_gap_tolerance_is_the_drivers_lever(fed, pnl, cohorts):
    fed.execute("DELETE FROM ad_daily WHERE day BETWEEN '2026-10-10' AND '2026-10-11'")                         # 2 missing: inside the default tolerance
    assert {r["key"]: r for r in nov(fed, pnl, cohorts)[0]["rows"]}["nc_cpa"]["context"]["value"] is not None
    fed.execute("DELETE FROM ad_daily WHERE day = '2026-10-12'")                                                # 3 missing: outside it
    assert {r["key"]: r for r in nov(fed, pnl, cohorts)[0]["rows"]}["nc_cpa"]["context"]["unavailable"] == "Meta spend feed has 25 of 28 days"
    wide = nov(fed, pnl, cohorts, {"drivers": {"reference_gap_days": 4}})[0]
    assert {r["key"]: r for r in wide["rows"]}["nc_cpa"]["context"]["value"] is not None


def test_28_day_cost_outside_the_believable_range_is_withheld(fed, pnl, cohorts):
    res, _ = nov(fed, pnl, cohorts, {"drivers": {"reference_cpa_min": 20, "reference_cpa_max": 60}})
    c = {r["key"]: r for r in res["rows"]}["nc_cpa"]["context"]
    assert c["value"] is None and c["unavailable"].startswith("Realized CPA $") and c["unavailable"].endswith("is outside the $20 to $60 a real feed gives")


def test_28_day_cost_needs_the_new_customer_feed_and_a_read_reference(fed, pnl, cohorts):
    fed.execute("DELETE FROM proj_feed_orders WHERE day BETWEEN '2026-10-20' AND '2026-10-30'")
    c = {r["key"]: r for r in nov(fed, pnl, cohorts)[0]["rows"]}["nc_cpa"]["context"]
    assert c["value"] is None and c["unavailable"].startswith("Shopify new-customer feed has ") and c["unavailable"].endswith(" of 28 days")
    fr = feeds.load_frame(fed, dt.date(2026, 10, 1), dt.date(2026, 11, 2))
    book = TargetBook(pnl, None, cohorts, row_map=ROW_MAP)
    unread = M.pacing_month(fr, dt.date(2026, 11, 1), SNAPSHOT_DAY, book, cohorts, {}, None, None)
    assert all("context" not in r for r in unread["rows"])                                                    # no reference, no figure: it is never shown unguarded


def test_cpa_window_lever_and_closed_months(fed, pnl, cohorts):
    short, _ = nov(fed, pnl, cohorts, {"pacing": {"cpa_window_days": 14}})
    c14 = {r["key"]: r for r in short["rows"]}["nc_cpa"]["context"]
    assert c14["label"] == "Last 14 days" and c14["value"] != pytest.approx(_window_cpa(fed, dt.date(2026, 10, 5), dt.date(2026, 11, 1)), abs=0.01)
    assert c14["value"] == pytest.approx(_window_cpa(fed, dt.date(2026, 10, 19), dt.date(2026, 11, 1)), rel=1e-6)
    fr = feeds.load_frame(fed, dt.date(2026, 10, 1), dt.date(2026, 10, 31))
    book = TargetBook(pnl, None, cohorts, row_map=ROW_MAP)
    ref = feeds.cpa_reference(fed, {}, 28, SNAPSHOT_DAY)
    closed = M.pacing_month(fr, dt.date(2026, 10, 1), SNAPSHOT_DAY, book, cohorts, {}, None, ref)
    assert all("context" not in r for r in closed["rows"])                                                    # "last 28 days" is about now, not about a closed month


# ---------------------------------------------------------------------------------------------------------------- month state

@pytest.mark.parametrize("month,through,elapsed,dim,want", [
    (dt.date(2026, 6, 1), dt.date(2026, 7, 1), 30, 30, "books"), (dt.date(2026, 7, 1), dt.date(2026, 7, 1), 31, 31, "books"),
    (dt.date(2026, 10, 1), dt.date(2026, 7, 1), 31, 31, "feeds"), (dt.date(2026, 11, 1), dt.date(2026, 7, 1), 2, 30, "projected"),
    (dt.date(2026, 12, 1), None, 0, 31, "projected"), (dt.date(2026, 10, 1), None, 31, 31, "feeds")])
def test_month_state_books_feeds_projected(month, through, elapsed, dim, want):
    assert M.month_state(month, through, elapsed, dim) == want


def test_state_is_in_the_month_and_snapshot_payloads(conn, pnl, cohorts):
    assert nov(conn, pnl, cohorts, None, dt.date(2026, 7, 1))[0]["state"] == "projected"
    fr = feeds.load_frame(conn, dt.date(2026, 10, 1), dt.date(2026, 10, 31))
    book = TargetBook(pnl, None, cohorts, row_map=ROW_MAP)
    assert M.snapshot_month(fr, dt.date(2026, 10, 1), SNAPSHOT_DAY, book, cohorts, {}, dt.date(2026, 7, 1))["state"] == "feeds"
    assert M.snapshot_month(fr, dt.date(2026, 10, 1), SNAPSHOT_DAY, book, cohorts, {}, dt.date(2026, 10, 1))["state"] == "books"


# ---------------------------------------------------------------------------------------------------------------- hourly curve

def test_intraday_falls_back_to_an_even_day_and_says_how_to_fix_it(api, meta_sandbox):
    c = meta_sandbox
    saved = c.execute("SELECT hour, spend_30d, days_in_curve, note FROM proj_pacing_hourly").fetchall()
    c.execute("DELETE FROM proj_pacing_hourly")
    c.commit()
    try:
        j = api.get(f"{BASE}/intraday?spent=6000&time=12:00").get_json()
        assert j["loaded"] is False and j["estimate"] is True and j["share_of_day"] == pytest.approx(0.5)
        assert j["projected_eod"] == pytest.approx(12000) and j["to_go"] == pytest.approx(6000)
        assert "seed-hourly" in j["message"] and "even through the day" in j["message"]
        assert api.get(f"{BASE}/intraday?time=12:00").get_json()["projected_eod"] is None
        chips = {f["feed"]: f for f in api.get(f"{BASE}/freshness").get_json()["feeds"]}
        assert chips["hourly_curve"]["status"] == "bad" and chips["hourly_curve"]["data_through"] is None            # the missing curve is flagged
    finally:
        for r in saved:
            c.execute("INSERT INTO proj_pacing_hourly (hour, spend_30d, days_in_curve, note) VALUES (%s, %s, %s, %s)", (r["hour"], r["spend_30d"], r["days_in_curve"], r["note"]))
        c.commit()


def test_refresh_loads_the_hourly_curve_when_the_table_is_empty(meta_sandbox):
    c = meta_sandbox
    c.execute("DELETE FROM proj_pacing_hourly")
    c.commit()
    out = ETL.refresh(c, days=3, snapshot_dir=SNAP)
    assert out["feeds"]["hourly_curve"]["rows"] == 24
    assert c.execute("SELECT count(*) AS n FROM proj_pacing_hourly").fetchone()["n"] == 24
    again = ETL.refresh(c, days=3, snapshot_dir=SNAP)
    assert "hourly_curve" not in again["feeds"]                                                                   # loaded once, then left alone


def test_a_missing_curve_tab_warns_but_does_not_stop_the_daily_feeds(meta_sandbox, monkeypatch):
    c = meta_sandbox
    c.execute("DELETE FROM proj_pacing_hourly")
    c.commit()
    real = ETL.read_grid

    def reader(tab, snap=None):
        if tab == "Automated Pacing ":
            raise ETL.FeedError("Automated Pacing : cannot read the sheet tab")
        return real(tab, snap)
    with monkeypatch.context() as m:                                                                              # undone before the sandbox reloads the feeds
        m.setattr(ETL, "read_grid", reader)
        out = ETL.refresh(c, days=3, snapshot_dir=SNAP)
    assert out["rows"] > 0 and "cannot read" in out["feeds"]["hourly_curve"]["warning"]


# ---------------------------------------------------------------------------------------------------------------- degraded plan

class NoPlan:
    """An engine result whose P&L module is degraded."""

    def __init__(self, scenario, cohorts):
        self.results = {"pnl": None, "cohorts": cohorts, "drivers": None}
        self.scenario = scenario
        self.errors = {"pnl": "OperationalError: connection refused"}
        self.actual_through = None

    def summary(self):
        return {"scenario": self.scenario.slug, "version": 1, "actual_through": None, "modules": {}, "headline": {}}


@pytest.fixture()
def degraded_api(api, monkeypatch, cohorts):
    from app.projection import scenarios
    from app.projection.pacing import api as pacing_api
    monkeypatch.setattr(pacing_api.engine, "run", lambda slug, conn, **kw: NoPlan(scenarios.get_scenario(conn, slug), cohorts))
    return api


def test_a_degraded_plan_is_a_503_upstream_degraded_never_goals_of_zero(degraded_api):
    for path in ("month", "snapshot", "rollup?grain=month&from=2026-09-01"):
        r = degraded_api.get(f"{BASE}/{path}")
        assert r.status_code == 503, path
        j = r.get_json()
        assert j["error"] == "upstream_degraded" and j["modules"][0]["module"] == "pnl" and "connection refused" in j["modules"][0]["error"]


def test_a_healthy_plan_answers_200(api):
    assert api.get(f"{BASE}/rollup?grain=month&from=2026-10-01").status_code == 200 and api.get(f"{BASE}/month").status_code == 200


def test_pacing_needs_the_pnl_in_its_compute_signature():
    import inspect
    from app.projection.engine import _required_upstream
    from app.projection.pacing.model import compute
    assert _required_upstream(compute) == ["pnl"]


# ---------------------------------------------------------------------------------------------------------------- clock, validation

def test_every_date_path_uses_the_pacific_clock(monkeypatch):
    monkeypatch.delenv(periods.ENV_AS_OF, raising=False)
    import datetime as _dt
    real = periods.datetime

    class Frozen(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return _dt.datetime(2026, 11, 4, 6, 10, tzinfo=_dt.timezone.utc).astimezone(tz) if tz else _dt.datetime(2026, 11, 4, 6, 10)
    monkeypatch.setattr(periods, "datetime", Frozen)
    assert periods.as_of() == dt.date(2026, 11, 3)                                                                # 06:10 UTC on Nov 4 is still Nov 3 in Los Angeles
    assert periods.today_pacific() == dt.date(2026, 11, 3)
    monkeypatch.setattr(periods, "datetime", real)


@pytest.mark.parametrize("qs", ["as_of=2035-01-01", "as_of=2024-12-31", "grain=day&from=2026-11-02&to=2026-10-01", "bands=zzz", "view=zzz"])
def test_odd_query_values_are_refused(api, qs):
    path = "catalog" if qs.startswith("view") else ("month" if qs.startswith("as_of") else "rollup")
    assert api.get(f"{BASE}/{path}?{qs}").status_code == 400


def test_known_bands_still_filter(api):
    j = api.get(f"{BASE}/rollup?grain=month&from=2026-10-01&bands=Google,Projection against the plan").get_json()
    assert [r["label"] for r in j["rows"] if r["kind"] == "header"] == ["Projection against the plan", "Google"]


# ---------------------------------------------------------------------------------------------------------------- Pacing and Cash & debt agree

def test_the_pacing_payback_box_equals_cash_and_debts_payback(api_app, api, pnl, cohorts, monkeypatch):
    """The same scenario, the same inputs: financing's GET /payback says what the pacing box says, on both bases and for the month-to-date and the plan."""
    from app.projection import scenarios
    from app.projection.financing import api as fin_api, model as fin_model
    from app.projection.interfaces import InventoryResult
    from app.projection.pacing import api as pacing_api

    class Run:
        def __init__(self, conn, slug):
            self.scenario = scenarios.get_scenario(conn, slug)
            self.errors = {}
            self.actual_through = dt.date(2026, 7, 1)
            fin = fin_model.compute(self.scenario, None, pnl, InventoryResult(outlay={}), cohorts, None)
            self.results = {"pnl": pnl, "cohorts": cohorts, "drivers": None, "financing": fin, "inventory_supply": InventoryResult(outlay={})}

        def summary(self):
            return {"scenario": self.scenario.slug, "version": 1, "actual_through": None, "modules": {}, "headline": {}}

    fake = lambda slug, conn, **kw: Run(conn, slug)                                                                # noqa: E731
    monkeypatch.setattr(pacing_api.engine, "run", fake)
    monkeypatch.setattr(fin_api.engine, "run", fake)
    box = api.get(f"{BASE}/month").get_json()["payback"]
    assert box["available"] and box["loaded"]["months"]["mtd"] == 18
    for aov, cac, curve, product, loaded in ((box["aov"]["mtd"], box["cpa"]["mtd"], "live", box["months"]["mtd"], box["loaded"]["months"]["mtd"]),
                                             (box["aov"]["goal"], box["cpa"]["goal"], "goal", box["months"]["goal"], box["loaded"]["months"]["goal"])):
        r = api.get(f"/api/projection/financing/payback?aov={aov}&cac={cac}&curve={curve}")
        assert r.status_code == 200, r.get_data(as_text=True)[:300]
        fin = r.get_json()
        assert fin["result"]["months"] == product                                                                  # on product margin
        assert fin["loaded"]["result"]["months"] == loaded                                                         # after all variable costs
        assert fin["loaded"]["info"]["rate"] == pytest.approx(box["loaded"]["variable_cost_rate"])
        assert fin["loaded"]["result"]["cm_first"] == pytest.approx(box["loaded"]["first_order_margin"]["mtd" if curve == "live" else "goal"])
        assert fin["result"]["cm_first"] == pytest.approx(box["first_order_margin"]["mtd" if curve == "live" else "goal"])


def test_editing_the_payback_inputs_in_pacing_moves_cash_and_debt_and_the_spend_plan(api, pnl, cohorts, monkeypatch):
    """One store: params['drivers']['payback']. A pacing edit is what Cash & debt and the Spend plan page read next."""
    from app.projection import scenarios
    from app.projection.drivers import api as drv_api
    from app.projection.financing import api as fin_api, model as fin_model
    from app.projection.interfaces import InventoryResult
    from app.projection.pacing import api as pacing_api

    class Run:
        def __init__(self, conn, slug):
            self.scenario = scenarios.get_scenario(conn, slug)
            self.errors = {}
            self.actual_through = dt.date(2026, 7, 1)
            fin = fin_model.compute(self.scenario, None, pnl, InventoryResult(outlay={}), cohorts, None)
            self.results = {"pnl": pnl, "cohorts": cohorts, "drivers": None, "financing": fin, "inventory_supply": InventoryResult(outlay={})}

        def summary(self):
            return {"scenario": self.scenario.slug, "version": 1, "actual_through": None, "modules": {}, "headline": {}}

    fake = lambda slug, conn, **kw: Run(conn, slug)                                                                # noqa: E731
    monkeypatch.setattr(pacing_api.engine, "run", fake)
    monkeypatch.setattr(fin_api.engine, "run", fake)
    q = "aov=70&cac=100&curve=live"
    before = api.get(f"/api/projection/financing/payback?{q}").get_json()
    assert before["result"]["cost_pct"] == pytest.approx((0.1726 * 70 + 5.53) / 70)
    r = api.put(f"{BASE}/levers", json={"scenario": "current", "levers": {"product_cost_pct": 0.40, "fulfillment_per_order": 9.0, "payback_horizon": 24}})
    assert r.status_code == 200
    # Cash & debt: the same inputs, now 40% product cost and $9 fulfillment, a 24-month horizon
    after = api.get(f"/api/projection/financing/payback?{q}").get_json()
    assert after["result"]["cost_pct"] == pytest.approx((0.40 * 70 + 9.0) / 70) and after["result"]["cm_first"] < before["result"]["cm_first"]
    inputs = api.get("/api/projection/financing/inputs").get_json()
    assert inputs["payback_sources"]["product_cost_pct"] == "drivers"
    # the Spend plan page reads the same store
    lev = api.get("/api/projection/drivers/levers").get_json()["levers"]["payback"]
    assert lev["product_cost_pct"] == 0.40 and lev["fulfillment_per_order"] == 9.0 and lev["horizon_months"] == 24
    # and the pacing box itself follows
    box = api.get(f"{BASE}/month").get_json()["payback"]
    assert box["product_cost_pct"] == 0.40 and box["fulfillment_per_order"] == 9.0 and box["horizon_months"] == 24
    # Spend plan edits move Pacing the other way round
    api.put("/api/projection/drivers/levers", json={"scenario": "current", "levers": {"payback": {"product_cost_pct": 0.25}}})
    assert {x["key"]: x["value"] for x in api.get(f"{BASE}/levers").get_json()["levers"]}["product_cost_pct"] == 0.25
    # a write to the legacy financing copy while the drivers key is set changes nothing in Pacing
    assert api.patch("/api/projection/scenarios/current", json={"params": {"financing": {"payback": {"product_cost_pct": 0.9}}}}).status_code == 200
    assert {x["key"]: x["value"] for x in api.get(f"{BASE}/levers").get_json()["levers"]}["product_cost_pct"] == 0.25
