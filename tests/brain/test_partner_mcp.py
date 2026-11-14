"""The brain MCP (/mcp) for partner keys: a partner_read_all key may use it (same guards: SELECT only on the analytics reader, no writes); a partner_push key and no key may not."""
import json

import pytest

from etl import api_keys as cli

from brain_helpers import DB_URL
from test_brain_mcp_http import bearer, rpc


@pytest.fixture()
def keys(pg):
    pg.execute("DELETE FROM api_keys WHERE name IN ('pt-read-all', 'pt-push')")
    pg.commit()
    ra, _ = cli.create_key(pg, "pt-read-all", "pytest", "partner_read_all", "Studio Flow")
    push, _ = cli.create_key(pg, "pt-push", "pytest", "partner_push", "Madre")
    yield ra, push
    pg.rollback()
    pg.execute("DELETE FROM api_keys WHERE name IN ('pt-read-all', 'pt-push')")
    pg.commit()


def tool(anon, k, name, args=None):
    r = rpc(anon, "tools/call", {"name": name, "arguments": args or {}}, headers=bearer(k))
    assert r.status_code == 200, r.get_data(as_text=True)
    res = r.get_json()["result"]
    return res["isError"], (res["content"][0]["text"] if res["isError"] else json.loads(res["content"][0]["text"]))


def test_a_read_all_key_can_use_the_brain(anon, keys, pg):
    ra, _ = keys
    r = rpc(anon, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}, headers=bearer(ra))
    assert r.status_code == 200 and r.get_json()["result"]["serverInfo"]["name"]
    tl = rpc(anon, "tools/list", headers=bearer(ra))
    assert tl.status_code == 200 and {"catalog", "describe", "sql", "app_map"} <= {t["name"] for t in tl.get_json()["result"]["tools"]}
    err, cat = tool(anon, ra, "catalog")
    assert not err and cat["domains"]
    assert "api_keys" not in json.dumps(cat) and "key_hash" not in json.dumps(cat)
    err, d = tool(anon, ra, "describe", {"name": "meta_ad_daily"})
    assert not err and d["columns"]
    row = pg.execute("SELECT actor FROM brain_audit ORDER BY id DESC LIMIT 1").fetchone()
    assert row[0].startswith("key:pt-read-all")


def test_sql_through_a_partner_key_is_select_only_and_cannot_see_keys(anon, keys, monkeypatch, pg):
    ra, _ = keys
    monkeypatch.setenv("ANALYTICS_READER_URL", DB_URL)
    err, r = tool(anon, ra, "sql", {"query": "select 41 + 1 as answer"})
    assert not err and r["rows"] == [[42]]
    for q in ("insert into app_meta (key, value) values ('x', 'y')", "delete from api_keys", "update api_keys set scope = 'read_assign'", "drop table api_keys", "select 1; select 2"):
        err, text = tool(anon, ra, "sql", {"query": q})
        assert err, q
        assert "key_hash" not in text
    # reads: the reader role of the hosted brain (etl.reader_role) is granted the analytics schema only, and no analytics view is built on api_keys
    n = pg.execute("SELECT count(*) FROM information_schema.view_table_usage WHERE table_name = 'api_keys' AND view_schema = 'analytics'").fetchone()[0]
    assert n == 0


def test_a_push_key_and_no_key_are_refused(anon, keys):
    ra, push = keys
    r = rpc(anon, "initialize", headers=bearer(push))
    assert r.status_code == 403
    assert rpc(anon, "tools/list", headers=bearer(push)).status_code == 403
    assert rpc(anon, "initialize").status_code == 401
    assert rpc(anon, "initialize", headers=bearer("lvk_" + "Q" * 40)).status_code == 401


def test_a_read_all_key_cannot_mint_brain_keys(anon, keys):
    ra, _ = keys
    assert anon.post("/api/brain/key", json={}, headers=bearer(ra)).status_code == 403
    assert anon.get("/api/brain/key", headers=bearer(ra)).status_code == 403
