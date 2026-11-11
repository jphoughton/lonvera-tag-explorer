"""The generated app map is current (like the schema doc), and it describes the brain endpoint's auth correctly."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import gen_app_map as G                      # noqa: E402


@pytest.fixture(scope="module")
def built():
    return G.build()


def test_the_committed_map_is_not_stale(built):
    assert G.stale(built) == []


def test_the_map_is_deterministic(built):
    assert G.render_json(G.build()) == G.render_json(built)


def test_a_stale_map_is_detected(built, tmp_path, monkeypatch):
    monkeypatch.setattr(G, "JSON_PATH", tmp_path / "app_map.json")
    monkeypatch.setattr(G, "MD_PATH", tmp_path / "APP_MAP.md")
    assert len(G.stale(built)) == 2                                        # missing files
    (tmp_path / "app_map.json").write_text(G.render_json(built))
    (tmp_path / "APP_MAP.md").write_text(G.render_md(built))
    assert G.stale(built) == []
    (tmp_path / "app_map.json").write_text(G.render_json({**built, "services": []}))
    assert len(G.stale(built)) == 1 and "app_map.json is stale" in G.stale(built)[0]


def test_services_schedules_and_jobs_come_from_the_deploy_scripts(built):
    sv = {s["name"]: s for s in built["services"]}
    assert sv["etl"]["schedule"] == "0 13 * * *" and sv["feeds"]["schedule"] == "0 21 * * *" and sv["alerts"]["schedule"] == "0 * * * *" and sv["backup"]["schedule"] == "30 8 * * *"
    assert sv["web"]["healthcheck"] == "/healthz"
    etl = [j["module"] for j in built["etl_jobs"] if j["service"] == "etl"]
    assert etl[:2] == ["etl.run_daily", "etl.upstack_daily"] and etl[-1] == "etl.auto_tag"
    assert next(j for j in built["etl_jobs"] if j["module"] == "etl.auto_tag")["fatal"] is False
    assert any(m["module"] == "etl.api_keys" for m in built["etl_modules"])


def test_routes_flag_what_skips_the_login_check(built):
    r = {(x["rule"], tuple(x["methods"])): x for x in built["routes"]}
    mcp = next(x for x in built["routes"] if x["rule"] == "/mcp")
    assert mcp["auth_exempt"] is False and "never vendor tokens" in mcp["auth"] and "POST" in mcp["methods"]
    assert r[("/api/priorities/vendor/<token>", ("GET",))]["auth_exempt"] is True
    assert r[("/api/login", ("POST",))]["auth_exempt"] is True
    assert r[("/api/products", ("GET",))]["auth_exempt"] is False and r[("/priorities", ("GET",))]["auth_exempt"] is True
    assert ("/api/brain/key", ("POST",)) in r and any(p["url_pattern"] == "/connect-agent" for p in built["pages"])
    assert json.loads((ROOT / "docs" / "app_map.json").read_text())["routes"] == built["routes"]
