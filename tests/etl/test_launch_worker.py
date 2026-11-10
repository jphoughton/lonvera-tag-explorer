"""etl.launch_worker with fake Meta and Drive clients. No test here touches the network: the clients are injected, the real factories refuse to run under pytest, and an autouse
fixture makes any attempt to send an HTTP request fail the test."""
import datetime as dt
import os

import pytest

from app import priorities_launch as pl
from etl import db
from etl import launch_worker as lw
from etl import meta_write as mw

MONDAY = dt.date(2026, 11, 9)
TOKEN = "test-token-1234567890-not-real"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    import requests

    def boom(*a, **k):
        raise AssertionError("a test tried to send an HTTP request")
    monkeypatch.setattr(requests.Session, "send", boom)
    monkeypatch.setenv("META_LAUNCH_ENABLED", "1")
    monkeypatch.setenv("META_LAUNCH_TOKEN", TOKEN)
    monkeypatch.setenv("META_LAUNCH_DRY_RUN", "0")
    monkeypatch.delenv("META_LAUNCH_MAX_ADS_PER_DAY", raising=False)


LAUNCH_TABLES = ("creative_launch_audit", "creative_launch_ads", "creative_launch_jobs", "creative_launch_drafts", "creative_launch_adsets", "creative_launch_settings",
                 "creative_launch_pages", "creative_priorities_assignments")


def _wipe(conn):
    conn.rollback()
    for t in LAUNCH_TABLES:
        conn.execute(f"DELETE FROM {t}")
    conn.commit()


@pytest.fixture(autouse=True)
def clean(conn):
    _wipe(conn)
    conn.execute("ALTER SEQUENCE creative_priorities_assignments_id_seq RESTART WITH 12")
    conn.commit()
    yield
    _wipe(conn)                                  # other etl tests TRUNCATE the assignments table, which the draft foreign key forbids while rows remain


# ---------------------------------------------------------------------------------------------------------------- fakes
class FakeDrive:
    def __init__(self, files=(), skipped=()):
        self.files, self.skipped = list(files), list(skipped)
        self.listed, self.downloads = [], []

    def list_media(self, url, max_files):
        self.listed.append((url, max_files))
        return {"files": list(self.files), "skipped": list(self.skipped)}

    def download(self, file_id, dest):
        self.downloads.append(file_id)
        data = b"x" * 16
        with open(dest, "wb") as fh:
            fh.write(data)
        return len(data)


class FakeMeta:
    def __init__(self):
        self.calls, self.existing, self.fail, self.usage_pct = [], {}, {}, 0.0
        self.n = 0

    def _rec(self, name, key=None):
        self.calls.append((name, key))
        left = self.fail.get((name, key))
        if left:                                                  # fail this many more times
            if left > 1:
                self.fail[(name, key)] = left - 1
            else:
                del self.fail[(name, key)]
            raise mw.MetaWriteError(f"{name} failed for {key} access_token={TOKEN}")

    def count(self, name):
        return sum(1 for c in self.calls if c[0] == name)

    def _id(self, p):
        self.n += 1
        return f"{p}{self.n}"

    def find_ad_by_name(self, account_id, name):
        self._rec("find_ad_by_name", name)
        return self.existing.get(name)

    def clone_adset_paused(self, template, name, budget, bid):
        self._rec("clone_adset_paused", name)
        return self._id("adset")

    def upload_image(self, account_id, data, filename):
        self._rec("upload_image", filename)
        return self._id("hash")

    def upload_video(self, account_id, path, size, filename):
        self._rec("upload_video", filename)
        return self._id("vid")

    def wait_video_ready(self, vid):
        self._rec("wait_video_ready", vid)
        return True

    def video_thumbnail(self, vid):
        self._rec("video_thumbnail", vid)
        return "https://thumb.invalid/t.jpg"

    def create_creative(self, account_id, spec):
        self._rec("create_creative", spec["name"])
        return self._id("creative")

    def create_paused_ad(self, account_id, adset_id, creative_id, name):
        self._rec("create_paused_ad", name)
        return self._id("ad")


def f(i, name=None, kind="image"):
    return {"id": f"drive{i}", "name": name or f"{i}.png", "mime": "image/png" if kind == "image" else "video/mp4", "size": 16, "kind": kind}


# ------------------------------------------------------------------------------------------------------------------ seed
def seed(conn, creative_url="https://drive.google.com/drive/folders/abcdefghij", settings=True, draft=True):
    if settings:
        conn.execute("""INSERT INTO creative_launch_settings (product, account_id, campaign_id, template_adset_id, landing_url, primary_texts, headlines)
                        VALUES ('boneflex','act_1','c1','t1','https://lonvera.com/p', ARRAY['Text A','Text B'], ARRAY['Head A'])""")
    conn.execute("INSERT INTO creative_launch_pages (page_id, name, instagram_user_id, name_token) VALUES ('p1','Lonvera','ig1','lonvera')")
    aid = conn.execute("""INSERT INTO creative_priorities_assignments (concept_key, text_key, product, vendor, pain_point, icp, section_at_assign, for_week, creative_url, ready_at)
                          VALUES ('k','t','boneflex','Madre','Joint Pain','Weekend Warrior','iterate',%s,%s, now()) RETURNING id""", (MONDAY, creative_url)).fetchone()[0]
    if draft:
        conn.execute("INSERT INTO creative_launch_drafts (assignment_id, fields) VALUES (%s, %s)",
                     (aid, db.jsonb({"style": "UGC", "fidelity": "Low", "proof": "Doctor", "page": "p1", "type": "Iteration"})))
    conn.commit()
    return aid


def queue(conn, aid, kind, plan=None, h=None, dry=False):
    jid = conn.execute("INSERT INTO creative_launch_jobs (assignment_id, kind, plan, plan_hash, dry_run) VALUES (%s,%s,%s,%s,%s) RETURNING id",
                       (aid, kind, db.jsonb(plan) if plan is not None else None, h, dry)).fetchone()[0]
    conn.commit()
    return jid


def job(conn, jid):
    r = conn.execute("SELECT state, error, plan, plan_hash FROM creative_launch_jobs WHERE id = %s", (jid,)).fetchone()
    return {"state": r[0], "error": r[1], "plan": r[2], "plan_hash": r[3]}


def prepared(conn, files, skipped=()):
    """Seed + run a prepare job; returns (assignment id, prepare job id, plan, hash)."""
    aid = seed(conn)
    jid = queue(conn, aid, "prepare")
    assert lw.run_once(conn, FakeMeta, lambda: FakeDrive(files, skipped)) == "prepare"
    j = job(conn, jid)
    assert j["state"] == "done", j
    return aid, jid, j["plan"], j["plan_hash"]


def launch(conn, aid, plan, h, meta, drive, dry=False, **kw):
    jid = queue(conn, aid, "launch", plan, h, dry)
    assert lw.run_once(conn, lambda: meta, lambda: drive, sleep=lambda s: None, **kw) == "launch"
    return jid


def ads(conn, aid):
    return conn.execute("SELECT file_name, state, meta_ad_id, meta_creative_id, meta_image_hash, meta_adset_id, error, concept_id FROM creative_launch_ads "
                        "WHERE assignment_id = %s ORDER BY id", (aid,)).fetchall()


def tracker_ids(conn, aid):
    return conn.execute("SELECT tracker_concept_ids FROM creative_priorities_assignments WHERE id=%s", (aid,)).fetchone()[0]


# ---------------------------------------------------------------------------------------------------------------- prepare
def test_prepare_plan_and_hash_accepted_by_confirm(conn):
    aid, jid, plan, h = prepared(conn, [f(2, "2.png"), f(10, "10.png"), f(1, "1.png", "video")])
    assert h == pl.plan_hash(plan)                                  # the API re-hashes the stored plan with this function
    assert [a["file_name"] for a in plan["ads"]] == ["1.png", "2.png", "10.png"]
    assert [a["concept_id"] for a in plan["ads"]] == ["Lonvera-BoneFlex-Video-A12-v1", "Lonvera-BoneFlex-Static-A12-v2", "Lonvera-BoneFlex-Static-A12-v3"]
    assert plan["ads"][0]["ad_name"].startswith("Lonvera-BoneFlex-Video-A12-v1_VID_JointPain_WeekendWarrior_Boneflex_UGC_Low_Madre_Iteration_Doctor_lonvera")
    assert plan["status"] == "PAUSED" and plan["adset_name"] == "Boneflex_Madre_2026-11-09_Production" and plan["adset_exists"] is False
    assert plan["primary_texts"] == ["Text A", "Text B"] and plan["headlines"] == ["Head A"] and plan["skipped"] == []
    assert plan["link_url"] == "https://lonvera.com/p" and plan["daily_budget_cents"] == 10000 and plan["cost_cap_cents"] == 6800
    stored = job(conn, jid)
    assert pl.plan_hash(stored["plan"]) == stored["plan_hash"]
    assert conn.execute("SELECT count(*) FROM creative_launch_ads").fetchone()[0] == 0          # prepare reserves nothing


def test_prepare_folder_over_ten_files_and_skipped_types(conn):
    files = [f(i, f"{i}.png") for i in range(1, 13)]
    aid, jid, plan, h = prepared(conn, files, skipped=[{"name": "notes.pdf", "reason": "not an image or video"}])
    assert [a["file_name"] for a in plan["ads"]] == [f"{i}.png" for i in range(1, 11)]
    assert plan["skipped"][0] == {"file_name": "notes.pdf", "reason": "not an image or video"}
    assert [s["file_name"] for s in plan["skipped"][1:]] == ["11.png", "12.png"] and "limit of 10" in plan["skipped"][1]["reason"]


def test_prepare_uses_draft_copy_and_link(conn):
    aid = seed(conn)
    conn.execute("UPDATE creative_launch_drafts SET fields = fields || %s WHERE assignment_id = %s",
                 (db.jsonb({"primary_texts": ["Mine"], "headlines": ["H1", "H2"], "link": "https://lonvera.com/q"}), aid))
    conn.commit()
    jid = queue(conn, aid, "prepare")
    lw.run_once(conn, FakeMeta, lambda: FakeDrive([f(1, "solo.mp4", "video")]))
    p = job(conn, jid)["plan"]
    assert p["primary_texts"] == ["Mine"] and p["headlines"] == ["H1", "H2"] and p["link_url"] == "https://lonvera.com/q"
    assert p["ads"][0]["concept_id"] == "Lonvera-BoneFlex-Video-A12" and p["ads"][0]["cta"] == "SHOP_NOW"


@pytest.mark.parametrize("what,expect", [("settings", "settings"), ("files", "No images or videos"), ("fields", "Pick")])
def test_prepare_fails_with_plain_text(conn, what, expect):
    aid = seed(conn, settings=(what != "settings"), draft=(what != "fields"))
    jid = queue(conn, aid, "prepare")
    lw.run_once(conn, FakeMeta, lambda: FakeDrive([]))
    j = job(conn, jid)
    assert j["state"] == "failed" and expect in j["error"] and j["plan"] is None


# ------------------------------------------------------------------------------------------------------------------ launch
def test_launch_happy_path_paused_and_tracker_ids(conn):
    aid, _, plan, h = prepared(conn, [f(1, "a.png"), f(2, "b.mp4", "video")])
    meta, drive = FakeMeta(), FakeDrive()
    jid = launch(conn, aid, plan, h, meta, drive)
    assert job(conn, jid)["state"] == "done" and job(conn, jid)["error"] is None
    rows = ads(conn, aid)
    assert [r[1] for r in rows] == ["created", "created"] and all(r[2] and r[3] for r in rows)
    assert meta.count("clone_adset_paused") == 1 and meta.count("create_paused_ad") == 2 and meta.count("wait_video_ready") == 1
    adset = conn.execute("SELECT meta_adset_id FROM creative_launch_adsets WHERE product='boneflex' AND vendor='Madre' AND for_week=%s", (MONDAY,)).fetchone()[0]
    assert {r[5] for r in rows} == {adset}
    assert tracker_ids(conn, aid) == [a["concept_id"] for a in plan["ads"]]
    assert conn.execute("SELECT launched_at FROM creative_priorities_assignments WHERE id=%s", (aid,)).fetchone()[0] is None      # stage untouched
    actions = [r[0] for r in conn.execute("SELECT action FROM creative_launch_audit ORDER BY id").fetchall()]
    assert actions.count("ad_created") == 2 and "adset_created" in actions and "launch_finished" in actions
    assert drive.downloads == ["drive1", "drive2"]


def test_tracker_concept_ids_append_without_duplicates(conn):
    aid, _, plan, h = prepared(conn, [f(1), f(2)])
    conn.execute("UPDATE creative_priorities_assignments SET tracker_concept_ids = %s WHERE id=%s", (["Hand-Entered-1", plan["ads"][0]["concept_id"]], aid))
    conn.commit()
    launch(conn, aid, plan, h, FakeMeta(), FakeDrive())
    assert tracker_ids(conn, aid) == ["Hand-Entered-1", plan["ads"][0]["concept_id"], plan["ads"][1]["concept_id"]]


def test_dry_run_makes_no_calls_at_all(conn):
    aid, _, plan, h = prepared(conn, [f(1), f(2)])

    def nope():
        raise AssertionError("a dry run must not even build a client")
    for env_dry, job_dry, flag in [("0", True, False), (None, False, False), ("0", False, True)]:
        conn.execute("DELETE FROM creative_launch_jobs WHERE kind='launch'")
        conn.execute("UPDATE creative_priorities_assignments SET tracker_concept_ids = '{}' WHERE id=%s", (aid,))
        conn.commit()
        if env_dry is None:
            os.environ.pop("META_LAUNCH_DRY_RUN", None)
        else:
            os.environ["META_LAUNCH_DRY_RUN"] = env_dry
        jid = queue(conn, aid, "launch", plan, h, job_dry)
        assert lw.run_once(conn, nope, nope, dry_run=flag) == "launch"
        assert job(conn, jid)["state"] == "done"
        assert conn.execute("SELECT count(*) FROM creative_launch_ads").fetchone()[0] == 0
        assert tracker_ids(conn, aid) == [a["concept_id"] for a in plan["ads"]]
    d = conn.execute("SELECT detail FROM creative_launch_audit WHERE action='launch_dry_run' ORDER BY id DESC LIMIT 1").fetchone()[0]
    assert d["would_create_adset"] is True and len(d["ads"]) == 2


def test_launch_rejects_tampered_plan(conn):
    aid, _, plan, h = prepared(conn, [f(1)])
    plan["ads"][0]["ad_name"] = "something else"
    jid = launch(conn, aid, plan, h, FakeMeta(), FakeDrive())
    assert job(conn, jid)["state"] == "failed" and "Prepare again" in job(conn, jid)["error"]
    assert conn.execute("SELECT count(*) FROM creative_launch_ads").fetchone()[0] == 0


def test_per_ad_failure_isolated_then_resume_without_duplicates(conn):
    aid, _, plan, h = prepared(conn, [f(1, "a.png"), f(2, "b.png"), f(3, "c.png")])
    meta, drive = FakeMeta(), FakeDrive()
    meta.fail[("upload_image", "b.png")] = 1
    j1 = launch(conn, aid, plan, h, meta, drive)
    assert job(conn, j1)["state"] == "failed" and "1 of 3" in job(conn, j1)["error"]
    rows = {r[0]: r for r in ads(conn, aid)}
    assert [rows[n][1] for n in ("a.png", "b.png", "c.png")] == ["created", "failed", "created"]
    assert TOKEN not in rows["b.png"][6] and "***" in rows["b.png"][6]                # error is scrubbed
    assert TOKEN not in (job(conn, j1)["error"] or "")
    j2 = launch(conn, aid, plan, h, meta, drive)
    assert job(conn, j2)["state"] == "done"
    assert [r[1] for r in ads(conn, aid)] == ["created"] * 3
    assert meta.count("create_paused_ad") == 3 and meta.count("create_creative") == 3 and meta.count("clone_adset_paused") == 1
    assert meta.count("find_ad_by_name") == 4                                         # the two finished ads are not looked up again
    assert conn.execute("SELECT count(*) FROM creative_launch_ads WHERE error IS NOT NULL").fetchone()[0] == 0


def test_resume_from_media_uploaded_does_not_reupload(conn):
    aid, _, plan, h = prepared(conn, [f(1, "a.png")])
    meta, drive = FakeMeta(), FakeDrive()
    meta.fail[("create_creative", plan["ads"][0]["ad_name"][:90])] = 1       # the creative name is cut to 90 characters
    launch(conn, aid, plan, h, meta, drive)
    r = ads(conn, aid)[0]
    assert r[1] == "failed" and r[4] and not r[3]
    launch(conn, aid, plan, h, meta, drive)
    assert ads(conn, aid)[0][1] == "created"
    assert meta.count("upload_image") == 1 and drive.downloads == ["drive1"] and meta.count("create_paused_ad") == 1


def test_orphan_ad_adopted_by_exact_name(conn):
    aid, _, plan, h = prepared(conn, [f(1, "a.png"), f(2, "b.png")])
    meta = FakeMeta()
    name = plan["ads"][0]["ad_name"]
    meta.existing[name] = {"id": "999", "name": name, "effective_status": "PAUSED"}
    launch(conn, aid, plan, h, meta, FakeDrive())
    rows = ads(conn, aid)
    assert rows[0][2] == "999" and rows[0][1] == "created" and rows[1][1] == "created"
    assert meta.count("create_paused_ad") == 1 and meta.count("upload_image") == 1 and meta.count("create_creative") == 1
    assert "ad_adopted" in [r[0] for r in conn.execute("SELECT action FROM creative_launch_audit").fetchall()]
    assert conn.execute("SELECT count(*) FROM creative_launch_audit WHERE action='ad_created'").fetchone()[0] == 1


def test_existing_adset_is_reused_not_cloned(conn):
    aid, _, plan, h = prepared(conn, [f(1)])
    conn.execute("INSERT INTO creative_launch_adsets (product, vendor, for_week, meta_adset_id) VALUES ('boneflex','Madre',%s,'adsetOLD')", (MONDAY,))
    conn.commit()
    meta = FakeMeta()
    launch(conn, aid, plan, h, meta, FakeDrive())
    assert meta.count("clone_adset_paused") == 0 and ads(conn, aid)[0][5] == "adsetOLD"


def test_daily_cap(conn, monkeypatch):
    monkeypatch.setenv("META_LAUNCH_MAX_ADS_PER_DAY", "2")
    aid, _, plan, h = prepared(conn, [f(1), f(2), f(3)])
    meta = FakeMeta()
    jid = launch(conn, aid, plan, h, meta, FakeDrive())
    assert job(conn, jid)["state"] == "failed" and "daily limit of 2" in job(conn, jid)["error"]
    assert meta.count("create_paused_ad") == 2 and conn.execute("SELECT count(*) FROM creative_launch_ads WHERE state='created'").fetchone()[0] == 2
    assert tracker_ids(conn, aid) == [a["concept_id"] for a in plan["ads"][:2]]
    jid2 = launch(conn, aid, plan, h, meta, FakeDrive())                       # same day, still capped: nothing new is started
    assert job(conn, jid2)["state"] == "failed" and meta.count("create_paused_ad") == 2


def test_usage_pause_waits_then_continues_or_gives_up(conn):
    aid, _, plan, h = prepared(conn, [f(1)])
    meta, slept = FakeMeta(), []
    meta.usage_pct = 80.0
    jid = queue(conn, aid, "launch", plan, h, False)
    lw.run_once(conn, lambda: meta, lambda: FakeDrive(), sleep=lambda s: (slept.append(s), setattr(meta, "usage_pct", 10.0)))
    assert slept == [60] and job(conn, jid)["state"] == "done" and meta.count("create_paused_ad") == 1
    conn.execute("DELETE FROM creative_launch_ads")
    conn.commit()
    meta2 = FakeMeta()
    meta2.usage_pct = 90.0
    jid = queue(conn, aid, "launch", plan, h, False)
    lw.run_once(conn, lambda: meta2, lambda: FakeDrive(), sleep=lambda s: None)
    assert job(conn, jid)["state"] == "failed" and "usage stayed high" in job(conn, jid)["error"] and meta2.count("create_paused_ad") == 0


def test_reprepare_after_launch_keeps_concept_ids(conn):
    aid, _, plan, h = prepared(conn, [f(1, "a.png"), f(2, "b.png")])
    launch(conn, aid, plan, h, FakeMeta(), FakeDrive())
    jid = queue(conn, aid, "prepare")
    lw.run_once(conn, FakeMeta, lambda: FakeDrive([f(1, "a.png"), f(2, "b.png")]))
    again = job(conn, jid)["plan"]
    assert [a["concept_id"] for a in again["ads"]] == [a["concept_id"] for a in plan["ads"]] and again["adset_exists"] is True


# ------------------------------------------------------------------------------------------------------------------ gates
def test_refuses_when_disabled_or_no_token(monkeypatch):
    monkeypatch.setattr(db, "connect", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not connect")))
    logs = []
    monkeypatch.setattr(db, "log", lambda *a: logs.append(" ".join(map(str, a))))
    monkeypatch.delenv("META_LAUNCH_ENABLED")
    assert lw.main(["--once"]) == 0 and len(logs) == 1 and "not enabled" in logs[0]
    monkeypatch.setenv("META_LAUNCH_ENABLED", "1")
    monkeypatch.delenv("META_LAUNCH_TOKEN")
    assert lw.main(["--once"]) == 0 and len(logs) == 2 and "META_LAUNCH_TOKEN" in logs[1] and TOKEN not in logs[1]
    assert lw.enabled({"META_LAUNCH_ENABLED": "0", "META_LAUNCH_TOKEN": "x"})[0] is False


def test_stale_running_job_is_failed(conn):
    aid = seed(conn)
    jid = queue(conn, aid, "prepare")
    conn.execute("UPDATE creative_launch_jobs SET state='running', started_at = now() - interval '3 hours' WHERE id=%s", (jid,))
    conn.commit()
    assert lw.reap_stale(conn) == 1 and job(conn, jid)["state"] == "failed"


def test_empty_queue_returns_none(conn):
    assert lw.run_once(conn, FakeMeta, FakeDrive) is None


# ------------------------------------------------------------------------------------------------------------------ guard
def test_never_builds_a_real_client_under_pytest(conn):
    with pytest.raises(RuntimeError, match="Inject a fake"):
        lw.default_meta_client()
    with pytest.raises(RuntimeError, match="Inject a fake"):
        lw.default_drive_client()
    aid = seed(conn)
    jid = queue(conn, aid, "prepare")
    with pytest.raises(RuntimeError, match="Inject a fake"):
        lw.run_once(conn)                                           # no injected clients: refuses instead of reaching for Drive
    assert job(conn, jid)["state"] == "failed"
