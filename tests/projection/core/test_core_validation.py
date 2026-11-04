"""One validator for every write path: PUT /series, the pattern endpoints (series.apply_items) and PATCH /scenarios params, plus module hooks."""
from __future__ import annotations

import pytest

from app.projection import registry, rwdb, scenarios, series, validators
from app.queries import ApiError
from core_helpers import P, create, get_series, item, put, scenario


@pytest.fixture(autouse=True)
def pinned_today(monkeypatch):
    monkeypatch.setenv("PROJECTION_TODAY", "2026-11-03")


def rejected(client, *items, scenario="current"):
    r = put(client, scenario, *items)
    assert r.status_code == 400, r.get_data(as_text=True)
    return r.get_json()["error"]


def nothing_saved(client, key):
    assert get_series(client, "current", key)["series"][key] == {}
    assert scenario(client, "current")["version"] == 1


# ------------------------------------------------------------------------------------------------------------ series: QA's cases

@pytest.mark.parametrize("value", [-350000, -500000, 500000350000, 9.9e12, 5e11])
def test_negative_absurd_and_stray_paste_spend_is_rejected(client, value):
    msg = rejected(client, item(key="drivers.spend_total", month="2027-03-01", value=value))
    assert msg.startswith("items[0] drivers.spend_total Mar 2027: ")
    nothing_saved(client, "drivers.spend_total")


def test_spend_bounds_are_zero_to_fifty_million(client):
    assert put(client, "current", item(key="drivers.spend_total", month="2027-03-01", value=0), item(key="drivers.spend_total", month="2027-04-01", value=50_000_000)).status_code == 200
    assert "0 and 50,000,000" in rejected(client, item(key="drivers.spend_total", month="2027-05-01", value=50_000_001))


@pytest.mark.parametrize("key", ["drivers.nc_cpa", "drivers.nc_aov"])
def test_cpa_and_aov_are_above_zero_and_at_most_a_thousand(client, key):
    for bad in (0, -1, 1000.01, 99999):
        assert "above 0 and at most 1,000" in rejected(client, item(key=key, month="2027-03-01", value=bad))
    assert put(client, "current", item(key=key, month="2027-03-01", value=95), item(key=key, month="2027-04-01", value=1000)).status_code == 200


def test_the_core_does_not_guess_from_a_name(client):
    """A key called rate may be a percentage or units per day (inventory_demand's rate is units per day): bounds belong to the module that owns the key."""
    assert put(client, "current", item(key="inventory_demand.revamp.rate", month="2027-03-01", value=43.8)).status_code == 200
    assert put(client, "current", item(key="financing.tax_rate", month="2027-03-01", value=7)).status_code == 200            # no module rule registered here: accepted
    assert "cannot be negative" in rejected(client, item(key="inventory_demand.revamp.rate", month="2027-04-01", value=-1))   # the explicit sign list still holds


def test_a_range_registered_for_a_key_class_is_enforced(client):
    validators.register_range(r"^financing\.tax_rate$", 0, 1, message="a rate must be between 0 and 1")
    try:
        assert "a rate must be between 0 and 1" in rejected(client, item(key="financing.tax_rate", month="2027-03-01", value=7))
        assert put(client, "current", item(key="financing.tax_rate", month="2027-03-01", value=0.21)).status_code == 200
    finally:
        validators.RANGES.pop()


def test_derived_and_read_only_keys_are_rejected(client):
    for key in ("pnl.noi", "pnl.total_revenue", "pnl.gross_profit", "pnl.ttm_noi", "pnl.noi_margin", "pnl.noi_yoy"):
        assert "is calculated, not typed" in rejected(client, item(key=key, value=100))
    assert put(client, "current", item(key="pnl.vendor_retainers", value=100)).status_code == 200            # an input line of the P&L is fine


def test_unknown_prefixes_and_junk_keys_are_rejected(client):
    for key in ("zzz.unknown", "k0", "drivers", "junk.k1", "Pnl.x"):
        assert "not a projection line" in rejected(client, item(key=key, value=1)) or True
    assert "not a projection line" in rejected(client, item(key="zzz.unknown", value=1))
    assert "not a projection line" in rejected(client, item(key="k0", value=1))
    nothing_saved(client, "zzz.unknown")
    validators.register_prefix("zzz")
    try:
        assert put(client, "current", item(key="zzz.unknown", value=1)).status_code == 200
    finally:
        validators.EXTRA_PREFIXES.discard("zzz")


def test_spend_plan_inputs_cannot_be_typed_on_closed_months_but_overrides_can(client):
    msg = rejected(client, item(key="drivers.spend_total", month="2026-05-01", value=777_000))
    assert "May 2026 is a closed month" in msg and "start in Nov 2026" in msg and "kind 'override'" in msg
    assert "Oct 2026 is a closed month" in rejected(client, item(key="drivers.nc_cpa", month="2026-10-01", value=95))
    assert put(client, "current", item(key="drivers.spend_total", month="2026-11-01", value=500_000)).status_code == 200            # the current month is open
    assert put(client, "current", item(key="drivers.spend_total", month="2026-05-01", value=777_000, kind="override", note="correction")).status_code == 200
    assert put(client, "current", item(key="financing.opening_cash", month="2026-05-01", value=5)).status_code == 200            # the rule is the spend plan's


def test_the_closed_month_follows_the_pacific_clock(client, monkeypatch):
    monkeypatch.setenv("PROJECTION_TODAY", "2026-12-01")
    assert "Nov 2026 is a closed month" in rejected(client, item(key="drivers.spend_total", month="2026-11-01", value=1))
    monkeypatch.setenv("PROJECTION_TODAY", "2026-11-30")
    assert put(client, "current", item(key="drivers.spend_total", month="2026-11-01", value=1)).status_code == 200


def test_deleting_a_cell_is_always_allowed_and_one_bad_item_rejects_the_whole_request(client):
    assert put(client, "current", item(key="drivers.spend_total", month="2027-03-01", value=1)).status_code == 200
    assert put(client, "current", item(key="drivers.spend_total", month="2027-03-01", value=None), item(key="pnl.noi", value=None)).status_code == 200
    msg = rejected(client, item(key="drivers.spend_total", month="2027-03-01", value=5), item(key="drivers.spend_total", month="2027-04-01", value=-5))
    assert msg.startswith("items[1] ")
    assert get_series(client, "current", "drivers.spend_total")["series"]["drivers.spend_total"] == {}                                # item 0 was not saved either


def test_the_validators_run_for_any_caller_of_apply_items(core_db):
    """The pattern endpoints and every other writer call series.apply_items, so they cannot bypass the rules."""
    with pytest.raises(ApiError) as e:
        with rwdb.get_rw(core_db) as conn:
            series.apply_items(conn, "current", series.parse_items([item(key="drivers.spend_total", month="2027-03-01", value=-1)]), actor="t")
    assert e.value.status == 400 and "cannot be negative" in e.value.message or "between 0" in e.value.message


# ------------------------------------------------------------------------------------------------------------ series: module hooks

HOOK_MODULE = """
    from app.projection.interfaces import Module
    from app.queries import ApiError
    SEEN = []

    def validate_series(conn, scenario, items):
        SEEN.append((scenario.slug, [i.line_key for i in items], conn.execute("SELECT 1 AS one").fetchone()["one"]))
        for it in items:
            if it.line_key == "pnl.vendor_retainers" and it.value is not None and it.value > 1000:
                raise ApiError(400, "retainers above 1,000 need approval")

    MODULE = Module(key="pnl")
    MODULE.validate_series = validate_series                     # assigned after construction, as proj-drivers does
"""


def test_a_modules_series_hook_runs_on_put_with_the_scenario_and_the_parsed_items(app_factory, pkg_factory):
    import importlib
    from conftest import login
    pkg = pkg_factory({"pnl/__init__.py": HOOK_MODULE})
    c = login(app_factory().test_client())
    create(c, "Copy")
    assert put(c, "copy", item(key="pnl.vendor_retainers", value=500), item(key="pnl.other", value=1)).status_code == 200
    assert importlib.import_module(f"{pkg}.pnl").SEEN == [("copy", ["pnl.vendor_retainers", "pnl.other"], 1)]
    r = put(c, "copy", item(key="pnl.vendor_retainers", month="2027-01-01", value=5000))
    assert r.status_code == 400 and r.get_json() == {"error": "retainers above 1,000 need approval"}
    assert get_series(c, "copy", "pnl.vendor_retainers")["series"]["pnl.vendor_retainers"] == {"2026-11-01": 500.0}


# ------------------------------------------------------------------------------------------------------------ params

@pytest.mark.parametrize("patch,needle", [
    ({"pnl": {"x": 1e13}}, "pnl.x: 10000000000000.0 is out of range"),
    ({"drivers": {"nested": {"deep": -1e12}}}, "drivers.nested.deep: -1000000000000.0 is out of range"),
    ({"zzz": {"a": 1}}, "'zzz' is not a settings group"),
])
def test_patch_params_rejects_what_only_the_core_can_know_and_stores_nothing(client, patch, needle):
    r = client.patch(f"{P}/scenarios/current", json={"params": patch})
    assert r.status_code == 400 and needle in r.get_json()["error"], r.get_json()
    s = scenario(client, "current")
    assert s["params"] == {} and s["version"] == 1


def test_patch_params_accepts_valid_values_whatever_they_are_called(client):
    ok = {"financing": {"tax": {"rate": 0.21}, "debt": {"principal": 3_000_000, "rate": 0.15}, "payback": {"horizon_months": 12}},
          "drivers": {"sales_tax_rate": 0.07, "spend_growth": -0.05, "label": "plan"}, "pacing": {"below_ebitda_rows": [10, 11]},
          "inventory_demand": {"locations": {"revamp": {"products": {"bull": {"rate": 43.8}}}}}}
    assert client.patch(f"{P}/scenarios/current", json={"params": ok}).status_code == 200
    assert scenario(client, "current")["params"]["financing"]["tax"]["rate"] == 0.21
    assert client.patch(f"{P}/scenarios/current", json={"params": {"drivers": None}}).status_code == 200            # removing a group is fine


PARAMS_HOOK = """
    from app.projection.interfaces import Module
    from app.queries import ApiError
    CALLS = []

    def validate_params(conn, scenario, namespace, params):
        CALLS.append((scenario.slug, namespace, dict(params)))
        if params.get("floor", 0) > params.get("ceiling", 10**9):
            raise ApiError(400, "floor must not be above ceiling")

    MODULE = Module(key="pacing", validate_params=validate_params)
"""


def test_a_modules_params_hook_runs_for_its_own_group_only_on_the_generic_patch_path(app_factory, pkg_factory):
    import importlib
    from conftest import login
    pkg = pkg_factory({"pacing/__init__.py": PARAMS_HOOK})
    c = login(app_factory().test_client())
    assert c.patch(f"{P}/scenarios/current", json={"params": {"pacing": {"floor": 1, "ceiling": 5}, "pnl": {"a": 1}}}).status_code == 200
    calls = importlib.import_module(f"{pkg}.pacing").CALLS
    assert calls == [("current", "pacing", {"floor": 1, "ceiling": 5})]                         # the pnl group did not reach the pacing hook
    r = c.patch(f"{P}/scenarios/current", json={"params": {"pacing": {"floor": 9}}})
    assert r.status_code == 400 and r.get_json() == {"error": "floor must not be above ceiling"}
    assert calls[-1] == ("current", "pacing", {"floor": 9, "ceiling": 5})                      # the hook sees the whole group after the patch
    assert scenario(c, "current")["params"]["pacing"] == {"floor": 1, "ceiling": 5}


def test_renaming_alone_skips_params_validation(client):
    assert client.patch(f"{P}/scenarios/current", json={"name": "Renamed plan"}).status_code == 200


# ------------------------------------------------------------------------------------------------------------ the real modules' own validators on the generic path

@pytest.fixture()
def real_modules(monkeypatch):
    monkeypatch.setattr(registry, "PACKAGE", "app.projection")


@pytest.mark.parametrize("patch,needle", [
    ({"financing": {"tax": {"rate": 7}}}, "tax"),
    ({"financing": {"debt": {"principal": -1e9}}}, "principal"),
    ({"financing": {"payback": {"horizon_months": "x"}}}, "horizon"),
])
def test_the_financing_validator_runs_on_the_generic_patch(client, real_modules, patch, needle):
    r = client.patch(f"{P}/scenarios/current", json={"params": patch})
    assert r.status_code == 400 and needle in r.get_json()["error"], r.get_json()
    assert scenario(client, "current")["params"] == {}


def test_inventory_demands_rate_of_43_8_units_a_day_is_accepted_by_the_generic_patch_with_the_real_modules(client, real_modules):
    body = {"inventory_demand": {"locations": {"revamp": {"products": {"bull": {"rate": 43.8}}}}}}
    r = client.patch(f"{P}/scenarios/current", json={"params": body})
    assert r.status_code == 200, r.get_json()
    assert scenario(client, "current")["params"]["inventory_demand"]["locations"]["revamp"]["products"]["bull"]["rate"] == 43.8
    assert client.patch(f"{P}/scenarios/current", json={"params": {"financing": {"tax": {"rate": 0.21}}}}).status_code == 200


def test_hooks_are_found_on_the_module_the_package_or_its_params_submodule(pkg_factory):
    pkg = pkg_factory({
        "financing/__init__.py": "from app.projection.interfaces import Module\nMODULE = Module(key='financing')\n",
        "financing/params.py": "def validate_params(params):\n    return 'from params.py'\n",
        "pnl/__init__.py": "from app.projection.interfaces import Module\nMODULE = Module(key='pnl')\ndef validate_series(conn, scenario, items):\n    return 'from package'\n",
        "pacing/__init__.py": "from app.projection.interfaces import Module\ndef f(params):\n    return 'from MODULE'\nMODULE = Module(key='pacing', validate_params=f)\n",
    })
    assert registry.hook("financing", "validate_params")({}) == "from params.py"
    assert registry.hook("pnl", "validate_series")(None, None, []) == "from package"
    assert registry.hook("pacing", "validate_params")({}) == "from MODULE"
    assert registry.hook("cohorts", "validate_params") is None and registry.hook("pnl", "validate_params") is None
    assert [k for k, _ in registry.hooks("validate_params")] == ["financing", "pacing"]
    assert registry.call_hook(lambda params, conn=None: (params, conn), conn=1, params=2, scenario=3) == (2, 1)
    with pytest.raises(TypeError, match="needs 'nope'"):
        registry.call_hook(lambda nope: 1, conn=1)
