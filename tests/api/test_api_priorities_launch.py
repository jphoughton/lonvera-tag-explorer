"""Launch API (app/priorities_launch_api.py): the three gates, settings, the launch form, and the prepare / confirm / cancel queue. DARK: nothing here reaches Meta or Google; the routes only
read and write the migration 203 tables."""
import pytest
from psycopg.types.json import Jsonb

from app import api as api_mod
from app import priorities_launch as pl

from .conftest import PASSWORD
from .test_api_priorities import _key
from .test_api_priorities_assign import LINE_ROW, one, snaps  # noqa: F401  (snaps is a fixture)

TABLES = ("creative_launch_audit", "creative_launch_ads", "creative_launch_jobs", "creative_launch_drafts", "creative_launch_adsets", "creative_launch_settings", "creative_launch_pages",
          "creative_priorities_assignments", "creative_priorities_vendor_links", "creative_priorities_manual")


def _wipe(pg):
    for t in TABLES:
        pg.execute(f"DELETE FROM {t}")
    pg.commit()


@pytest.fixture(autouse=True)
def clean(pg, app):
    _wipe(pg)
    api_mod._assign_limiter = None
    api_mod._launch_limiter = None
    yield
    _wipe(pg)


@pytest.fixture()
def on(monkeypatch):
    monkeypatch.setenv("META_LAUNCH_ENABLED", "1")
    monkeypatch.setenv("META_LAUNCH_USERS", "joshhoughton")
    monkeypatch.delenv("META_LAUNCH_DRY_RUN", raising=False)


@pytest.fixture()
def ready(client, snaps, on):
    """An assignment at stage 'ready' (creative link set, marked ready)."""
    a = one(client)
    r = client.patch(f"/api/priorities/assignments/{a['id']}", json={"creative_url": "https://drive.google.com/drive/folders/abc", "ready": True})
    assert r.status_code == 200 and r.get_json()["stage"] == "ready"
    return a["id"]


def url(aid, tail=""):
    return f"/api/priorities/assignments/{aid}/launch{tail}"


def done_prepare(pg, aid, plan=None):
    plan = plan or {"assignment_id": aid, "status": "PAUSED", "ads": [{"file_name": "1.png"}]}
    h = pl.plan_hash(plan)
    jid = pg.execute("INSERT INTO creative_launch_jobs (assignment_id, kind, state, plan, plan_hash, dry_run, finished_at) VALUES (%s, 'prepare', 'done', %s, %s, true, now()) RETURNING id",
                     (aid, Jsonb(plan), h)).fetchone()[0]
    pg.commit()
    return jid, h


def audit(pg):
    return [r[0] for r in pg.execute("SELECT action FROM creative_launch_audit ORDER BY id").fetchall()]


ROUTES = [("get", "/api/priorities/launch/settings"), ("put", "/api/priorities/launch/settings/vita-bull"), ("get", "/api/priorities/assignments/1/launch"),
          ("put", "/api/priorities/assignments/1/launch/draft"), ("post", "/api/priorities/assignments/1/launch/prepare"), ("post", "/api/priorities/assignments/1/launch/confirm"),
          ("post", "/api/priorities/assignments/1/launch/cancel")]


# ------------------------------------------------------------------------------------------------------------------ gates
def test_flag_off_is_404_everywhere_and_discovery_says_disabled(client, monkeypatch):
    monkeypatch.delenv("META_LAUNCH_ENABLED", raising=False)
    monkeypatch.setenv("META_LAUNCH_USERS", "joshhoughton")
    for m, u in ROUTES:
        r = getattr(client, m)(u, json={})
        assert r.status_code == 404 and r.get_json() == {"error": "not found"}, (m, u)
    d = client.get("/api/priorities/assignments").get_json()["launch"]
    assert d == {"enabled": False, "can_launch": False, "dry_run": True}


def test_auth_disabled_is_403(app_factory, monkeypatch):
    monkeypatch.setenv("META_LAUNCH_ENABLED", "1")
    monkeypatch.setenv("META_LAUNCH_USERS", "guest")
    c = app_factory(AUTH_DISABLED=True).test_client()
    for m, u in ROUTES:
        r = getattr(c, m)(u, json={})
        assert r.status_code == 403 and r.get_json() == {"error": "Launching needs sign-in to be on."}, (m, u)
    assert c.get("/api/priorities/assignments").get_json()["launch"]["can_launch"] is False


def test_user_not_listed_is_403(client, monkeypatch):
    monkeypatch.setenv("META_LAUNCH_ENABLED", "1")
    for users in ("", "somebody-else"):
        monkeypatch.setenv("META_LAUNCH_USERS", users)
        for m, u in ROUTES:
            r = getattr(client, m)(u, json={})
            assert r.status_code == 403 and r.get_json() == {"error": "Your login is not allowed to launch ads."}, (users, m, u)
        d = client.get("/api/priorities/assignments").get_json()["launch"]
        assert d == {"enabled": True, "can_launch": False, "dry_run": True}


def test_listed_user_discovery_and_user_match_ignores_case(client, on):
    assert client.get("/api/priorities/assignments").get_json()["launch"] == {"enabled": True, "can_launch": True, "dry_run": True}


def test_not_signed_in_is_401(anon, on):
    for m, u in ROUTES:
        assert getattr(anon, m)(u, json={}).status_code == 401


def test_vendor_token_path_and_public_pattern_do_not_expose_launch(anon, on):
    from app import auth
    tok = "A" * 30
    for p in (f"/api/priorities/vendor/{tok}/launch", f"/api/priorities/vendor/{tok}/1/launch", "/api/priorities/launch/settings", "/api/priorities/assignments/1/launch"):
        assert not auth.PUBLIC_API_RE.fullmatch(p), p
    assert anon.get("/api/priorities/launch/settings").status_code == 401                       # still behind the login
    assert anon.get(f"/api/priorities/vendor/{tok}/1/launch").status_code in (401, 404)
    assert anon.get(f"/api/priorities/vendor/{tok}/launch").status_code in (401, 404)


# --------------------------------------------------------------------------------------------------------------- settings
def test_settings_get_put_roundtrip(client, pg, on):
    r = client.get("/api/priorities/launch/settings")
    assert r.status_code == 200
    j = r.get_json()
    assert j["settings"] == [] and {"vendor": "Madre", "prefix": "Lonvera"} in j["vendor_prefixes"] and j["pages"] == []
    pg.execute("INSERT INTO creative_launch_pages (page_id, name, instagram_user_id, name_token) VALUES ('p1', 'Lonvera', 'ig1', 'lonvera')")
    pg.commit()
    body = {"account_id": "act_123", "campaign_id": "c1", "template_adset_id": "t1", "page_id": "p1", "link_url": "https://x.test/p", "daily_budget_cents": 10000, "cost_cap_cents": 6800,
            "primary_texts": ["a", "b", "a"], "headlines": ["h"]}
    r = client.put("/api/priorities/launch/settings/vita-bull", json=body)
    assert r.status_code == 200, r.get_data(as_text=True)
    s = r.get_json()
    assert s["product"] == "vita-bull" and s["campaign_id"] == "c1" and s["page_id"] == "p1" and s["instagram_actor_id"] == "ig1" and s["cost_cap_cents"] == 6800
    assert s["primary_texts"] == ["a", "b"] and s["updated_by"] == "JoshHoughton" and s["link_url"] == "https://x.test/p"
    r2 = client.put("/api/priorities/launch/settings/vita-bull", json={"campaign_id": "", "product": "ignored", "updated_at": "x"})       # blank clears, read-only keys ignored
    assert r2.get_json()["campaign_id"] is None and r2.get_json()["account_id"] == "act_123"
    assert client.get("/api/priorities/launch/settings").get_json()["pages"] == [{"page_id": "p1", "name": "Lonvera", "instagram_actor_id": "ig1"}]
    assert audit(pg) == ["settings_saved", "settings_saved"]


@pytest.mark.parametrize("product,body", [("nope", {"campaign_id": "c"}), ("vita-bull", {}), ("vita-bull", {"bogus": 1}), ("vita-bull", {"daily_budget_cents": 5}),
                                          ("vita-bull", {"daily_budget_cents": "10000"}), ("vita-bull", {"link_url": "ftp://x"}), ("vita-bull", {"campaign_id": "a b"}),
                                          ("vita-bull", {"page_id": "missing"}), ("vita-bull", {"headlines": "x"}), ("vita-bull", {"headlines": ["a", "b", "c", "d"]}),
                                          ("vita-bull", {"cta_image": "learn more"})])
def test_settings_bad_input_is_400(client, pg, on, product, body):
    r = client.put(f"/api/priorities/launch/settings/{product}", json=body)
    assert r.status_code == 400 and r.get_json()["error"]
    assert audit(pg) == []


# ----------------------------------------------------------------------------------------------------------------- get / draft
def test_launch_get_ready_and_not_ready(client, snaps, on):
    a = one(client)
    j = client.get(url(a["id"])).get_json()
    assert j["ready"] is False and j["not_ready_reason"] and j["draft"] is None and j["job"] is None and j["ads"] == [] and j["tracker_tsv"] is None and j["dry_run"] is True
    assert j["assignment"]["id"] == a["id"] and j["assignment"]["product"] == "vita-bull" and j["assignment"]["concept_label"]
    client.patch(f"/api/priorities/assignments/{a['id']}", json={"creative_url": "https://drive.google.com/x", "ready": True})
    j = client.get(url(a["id"])).get_json()
    assert j["ready"] is True and j["not_ready_reason"] is None and j["assignment"]["stage"] == "ready"


def test_unknown_assignment_is_404(client, on):
    for m, tail in (("get", ""), ("put", "/draft"), ("post", "/prepare"), ("post", "/confirm"), ("post", "/cancel")):
        r = getattr(client, m)(url(999999, tail), json={"job_id": 1, "plan_hash": "x"})
        assert r.status_code == 404, (m, tail)


def test_draft_defaults_from_settings_then_saved_edits(client, pg, ready):
    pg.execute("INSERT INTO creative_launch_pages (page_id, name, name_token) VALUES ('p1', 'Lonvera', 'lonvera')")
    pg.execute("INSERT INTO creative_launch_settings (product, default_page_id, landing_url, primary_texts, headlines) VALUES ('vita-bull', 'p1', 'https://x.test/p', %s, %s)", (["t1", "t2", "t3"], ["h1"]))
    pg.commit()
    d = client.get(url(ready)).get_json()["draft"]
    assert d["page"] == "p1" and d["link"] == "https://x.test/p" and d["primary_texts"] == ["t1", "t2", "t3"] and d["headlines"] == ["h1"] and d["type"] in pl_types()
    r = client.put(url(ready, "/draft"), json={"type": "Iteration", "headlines": ["x", "y"], "link": "https://x.test/q"})
    assert r.status_code == 200
    d = r.get_json()["draft"]
    assert d["type"] == "Iteration" and d["headlines"] == ["x", "y"] and d["link"] == "https://x.test/q" and d["primary_texts"] == ["t1", "t2", "t3"]
    r = client.put(url(ready, "/draft"), json={"type": ""})                                                    # blank clears back to the default
    assert r.get_json()["draft"]["type"] in pl_types()
    assert audit(pg)[-2:] == ["draft_saved", "draft_saved"]


def pl_types():
    return ["New-Concept", "Iteration"]


def test_draft_validation_and_open_job_conflict(client, pg, ready):
    for body in ({"type": "Nope"}, {"style": "not a real style"}, {"page": "nope"}, {"bogus": 1}, {"headlines": ["a", "b", "c", "d"]}, {"link": "javascript:x"}, {"type": 5}):
        r = client.put(url(ready, "/draft"), json=body)
        assert r.status_code == 400, body
    assert client.put(url(ready, "/draft"), json={"type": "Iteration"}).status_code == 200
    assert client.post(url(ready, "/prepare")).status_code == 202
    r = client.put(url(ready, "/draft"), json={"type": "New-Concept"})
    assert r.status_code == 409 and r.get_json() == {"error": "A launch is already running for this row."}


# ----------------------------------------------------------------------------------------------------- prepare / confirm / cancel
def test_prepare_queues_one_job_and_a_second_is_409(client, pg, ready):
    r = client.post(url(ready, "/prepare"))
    assert r.status_code == 202
    job = r.get_json()["job"]
    assert job["kind"] == "prepare" and job["state"] == "queued" and job["dry_run"] is True and job["plan"] is None and job["finished_at"] is None
    row = pg.execute("SELECT kind, state, requested_by FROM creative_launch_jobs WHERE id = %s", (job["id"],)).fetchone()
    assert tuple(row) == ("prepare", "queued", "JoshHoughton")
    r = client.post(url(ready, "/prepare"))
    assert r.status_code == 409 and r.get_json() == {"error": "A launch is already running for this row."}
    assert pg.execute("SELECT count(*) FROM creative_launch_jobs").fetchone()[0] == 1
    assert client.get(url(ready)).get_json()["job"]["id"] == job["id"]
    assert audit(pg) == ["prepare_queued"]


def test_prepare_not_ready_is_409(client, pg, snaps, on):
    a = one(client)
    r = client.post(url(a["id"], "/prepare"))
    assert r.status_code == 409 and r.get_json()["error"]
    assert pg.execute("SELECT count(*) FROM creative_launch_jobs").fetchone()[0] == 0


def test_cancel_queued_then_nothing_open_is_404(client, pg, ready):
    jid = client.post(url(ready, "/prepare")).get_json()["job"]["id"]
    r = client.post(url(ready, "/cancel"))
    assert r.status_code == 200 and r.get_json()["job"]["id"] == jid and r.get_json()["job"]["state"] == "cancelled" and r.get_json()["job"]["finished_at"]
    assert client.post(url(ready, "/cancel")).status_code == 404
    assert client.post(url(ready, "/prepare")).status_code == 202                  # the row is free again
    assert audit(pg) == ["prepare_queued", "job_cancelled", "prepare_queued"]


def test_cancel_running_is_409(client, pg, ready):
    jid = client.post(url(ready, "/prepare")).get_json()["job"]["id"]
    pg.execute("UPDATE creative_launch_jobs SET state = 'running', started_at = now() WHERE id = %s", (jid,))
    pg.commit()
    r = client.post(url(ready, "/cancel"))
    assert r.status_code == 409 and r.get_json() == {"error": "It is already running and cannot be cancelled."}
    assert pg.execute("SELECT state FROM creative_launch_jobs WHERE id = %s", (jid,)).fetchone()[0] == "running"


def test_confirm_with_right_hash_queues_a_launch_with_dry_run_from_env(client, pg, ready, monkeypatch):
    jid, h = done_prepare(pg, ready)
    r = client.post(url(ready, "/confirm"), json={"job_id": jid, "plan_hash": h})
    assert r.status_code == 202, r.get_data(as_text=True)
    job = r.get_json()["job"]
    assert job["kind"] == "launch" and job["state"] == "queued" and job["plan_hash"] == h and job["dry_run"] is True and job["plan"]["status"] == "PAUSED"
    assert job["id"] != jid
    assert "launch_queued" in audit(pg)
    # a live setting is only ever read from the environment
    pg.execute("UPDATE creative_launch_jobs SET state = 'done' WHERE id = %s", (job["id"],))
    pg.commit()
    monkeypatch.setenv("META_LAUNCH_DRY_RUN", "0")
    r = client.post(url(ready, "/confirm"), json={"job_id": jid, "plan_hash": h})
    assert r.status_code == 202 and r.get_json()["job"]["dry_run"] is False
    assert client.get(url(ready)).get_json()["dry_run"] is False


def test_confirm_stale_or_wrong_job_is_409(client, pg, ready):
    jid, h = done_prepare(pg, ready)
    stale = {"error": "The plan changed. Prepare again."}
    r = client.post(url(ready, "/confirm"), json={"job_id": jid, "plan_hash": "0" * 64})
    assert r.status_code == 409 and r.get_json() == stale
    r = client.post(url(ready, "/confirm"), json={"job_id": 999999, "plan_hash": h})
    assert r.status_code == 409 and r.get_json() == stale
    pg.execute("UPDATE creative_launch_jobs SET state = 'failed' WHERE id = %s", (jid,))                 # not done
    pg.commit()
    assert client.post(url(ready, "/confirm"), json={"job_id": jid, "plan_hash": h}).get_json() == stale
    pg.execute("UPDATE creative_launch_jobs SET state = 'done', plan = %s WHERE id = %s", (Jsonb({"tampered": True}), jid))      # stored hash no longer matches the plan
    pg.commit()
    assert client.post(url(ready, "/confirm"), json={"job_id": jid, "plan_hash": h}).get_json() == stale
    assert pg.execute("SELECT count(*) FROM creative_launch_jobs WHERE kind = 'launch'").fetchone()[0] == 0


def test_confirm_other_assignments_job_and_bad_body(client, pg, snaps, on):
    a, b = one(client)["id"], one(client, vendor="Victoria")["id"]
    for x in (a, b):
        client.patch(f"/api/priorities/assignments/{x}", json={"creative_url": "https://drive.google.com/x", "ready": True})
    jid, h = done_prepare(pg, a)
    r = client.post(url(b, "/confirm"), json={"job_id": jid, "plan_hash": h})
    assert r.status_code == 409
    for body in ({}, {"job_id": "1", "plan_hash": h}, {"job_id": jid}, {"job_id": True, "plan_hash": h}):
        assert client.post(url(b, "/confirm"), json=body).status_code == 400, body


def test_confirm_not_ready_is_409_and_open_job_conflict(client, pg, ready):
    jid, h = done_prepare(pg, ready)
    assert client.post(url(ready, "/prepare")).status_code == 202
    r = client.post(url(ready, "/confirm"), json={"job_id": jid, "plan_hash": h})                    # a prepare is open, so the partial unique index refuses the launch
    assert r.status_code == 409 and r.get_json() == {"error": "A launch is already running for this row."}
    client.post(url(ready, "/cancel"))
    client.patch(f"/api/priorities/assignments/{ready}", json={"ready": False})
    assert client.post(url(ready, "/confirm"), json={"job_id": jid, "plan_hash": h}).status_code == 409


def test_get_shows_latest_job_ads_and_tracker_row(client, pg, ready):
    pg.execute("INSERT INTO creative_launch_pages (page_id, name, name_token) VALUES ('p1', 'Lonvera', 'lonvera')")
    pg.execute("INSERT INTO creative_launch_settings (product, account_id, default_page_id) VALUES ('vita-bull', 'act_55', 'p1')")
    jid, h = done_prepare(pg, ready)
    pg.execute("INSERT INTO creative_launch_ads (assignment_id, job_id, drive_file_id, file_name, kind, concept_id, ad_name, state, meta_ad_id) VALUES (%s, %s, 'f1', '1.png', 'image', 'Lonvera-VitalBull-Static-A1', 'N1', 'created', '777')", (ready, jid))
    pg.commit()
    j = client.get(url(ready)).get_json()
    assert j["job"]["id"] == jid and j["job"]["plan_hash"] == h and j["job"]["plan"]["status"] == "PAUSED"
    ad = j["ads"][0]
    assert ad["concept_id"] == "Lonvera-VitalBull-Static-A1" and ad["state"] == "created" and ad["meta_ad_id"] == "777" and ad["error"] is None
    assert ad["ads_manager_url"] == "https://adsmanager.facebook.com/adsmanager/manage/ads?act=55&selected_ad_ids=777"
    assert j["tracker_tsv"].split("\t")[2] == "Lonvera-VitalBull-Static-A1" and "lonvera" in j["tracker_tsv"]


def test_every_write_has_an_audit_row_with_who_and_no_secret(client, pg, ready):
    client.put("/api/priorities/launch/settings/vita-bull", json={"campaign_id": "c9"})
    client.put(url(ready, "/draft"), json={"type": "Iteration"})
    client.post(url(ready, "/prepare"))
    client.post(url(ready, "/cancel"))
    rows = pg.execute("SELECT actor, action, assignment_id, detail::text FROM creative_launch_audit ORDER BY id").fetchall()
    assert [r[1] for r in rows] == ["settings_saved", "draft_saved", "prepare_queued", "job_cancelled"]
    assert all(r[0] == "JoshHoughton" for r in rows) and rows[1][2] == ready and rows[0][2] is None
    assert not any("token" in r[3].lower() or PASSWORD in r[3] for r in rows)


def test_writes_need_same_origin(client, ready):
    r = client.post(url(ready, "/prepare"), headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
