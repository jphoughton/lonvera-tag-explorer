"""Saved views of the By cut page (app/priorities_cut_views.py, /api/priorities/cut-views)."""
import pytest

STATE = {"by": ["cluster", "icp"], "preset": "30", "f": {"cluster": ["Energy"], "icp": [], "pain_point": []}, "metrics": ["spend", "nc_roas"], "order": ["nc_roas", "spend"], "stages": ["in_production", "ready"],
         "quick": "gap", "sort": {"key": "spend", "dir": "desc"}, "pipelineOnly": True, "product": "vita-bull"}


@pytest.fixture(autouse=True)
def empty(pg):
    pg.execute("DELETE FROM creative_cut_views")
    pg.commit()
    yield
    pg.execute("DELETE FROM creative_cut_views")
    pg.commit()


def save(client, name="My view", state=None):
    r = client.post("/api/priorities/cut-views", json={"name": name, "state": state or STATE})
    assert r.status_code == 201, r.get_data(as_text=True)
    return r.get_json()


def test_save_list_replace_and_delete(client):
    a = save(client)
    assert a["state"]["by"] == ["cluster", "icp"] and a["state"]["order"] == ["nc_roas", "spend"]
    b = save(client, "my VIEW", {**STATE, "by": ["icp"]})                                    # same name, any case: replaced
    assert b["id"] == a["id"] and b["name"] == "my VIEW" and b["state"]["by"] == ["icp"]
    save(client, "Another")
    assert [v["name"] for v in client.get("/api/priorities/cut-views").get_json()["views"]] == ["Another", "my VIEW"]
    assert client.delete(f"/api/priorities/cut-views/{a['id']}").get_json() == {"removed": 1}
    assert client.delete(f"/api/priorities/cut-views/{a['id']}").status_code == 404
    assert len(client.get("/api/priorities/cut-views").get_json()["views"]) == 1


def test_unknown_keys_are_dropped_and_bad_state_is_a_400(client):
    v = save(client, "x", {**STATE, "evil": "<script>"})
    assert "evil" not in v["state"]
    for state in ({"by": []}, {"by": ["colour"]}, {"by": ["icp", "icp"]}, {"preset": "5"}, {"from": "nope"}, {"stages": ["nope"]}, {"sort": {"key": "x", "dir": "up"}}, {"metrics": "spend"}, {"f": []}, "text"):
        assert client.post("/api/priorities/cut-views", json={"name": "bad", "state": state}).status_code == 400, state
    assert client.post("/api/priorities/cut-views", json={"name": " ", "state": STATE}).status_code == 400
    assert client.post("/api/priorities/cut-views", json={"name": "big", "state": {"metrics": ["m" * 39] * 40, "order": ["m" * 39] * 40, "f": {"cluster": ["c" * 199] * 200, "icp": [], "pain_point": []}}}).status_code == 400
