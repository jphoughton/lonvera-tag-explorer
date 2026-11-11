"""One end-to-end pass through the real API routes and the launch worker with fake Meta and Drive clients: queue prepare -> worker prepares -> confirm with the plan hash -> worker launches.
No network: the worker gets injected fakes and the real client factories refuse to run under pytest."""
import pytest
from psycopg.types.json import Jsonb

from etl import launch_worker as lw

from .test_api_priorities_launch import audit, clean, on, ready, url  # noqa: F401  (fixtures)
from .test_api_priorities_assign import snaps  # noqa: F401


class Drive:
    def __init__(self):
        self.downloads = []

    def list_media(self, u, n):
        return {"files": [{"id": "d1", "name": "1.png", "mime": "image/png", "size": 4, "kind": "image"},
                          {"id": "d2", "name": "2.png", "mime": "image/png", "size": 4, "kind": "image"}], "skipped": []}

    def download(self, fid, dest):
        self.downloads.append(fid)
        open(dest, "wb").write(b"abcd")
        return 4


class Meta:
    usage_pct = 0.0

    def __init__(self):
        self.calls, self.n = [], 0

    def _id(self, k):
        self.n += 1
        self.calls.append(k)
        return f"{k}{self.n}"

    def find_ad_by_name(self, a, name):
        self.calls.append("find")

    def clone_adset_paused(self, *a):
        return self._id("adset")

    def upload_image(self, *a):
        return self._id("hash")

    def create_creative(self, *a):
        return self._id("creative")

    def create_paused_ad(self, *a):
        return self._id("ad")


def test_queue_prepare_confirm_launch_end_to_end(client, pg, ready, monkeypatch):
    monkeypatch.setenv("META_LAUNCH_TOKEN", "test-token-not-real")
    monkeypatch.setenv("META_LAUNCH_DRY_RUN", "0")
    pg.execute("INSERT INTO creative_launch_pages (page_id, name, instagram_user_id, name_token) VALUES ('p1','Lonvera','ig1','lonvera')")
    pg.execute("INSERT INTO creative_launch_settings (product, account_id, campaign_id, template_adset_id, landing_url, default_page_id, primary_texts, headlines) "
               "SELECT product, 'act_9','c9','t9','https://lonvera.com/p','p1', ARRAY['Text'], ARRAY['Head'] FROM creative_priorities_assignments WHERE id = %s", (ready,))
    pg.commit()
    pg.execute("INSERT INTO creative_launch_drafts (assignment_id, fields) VALUES (%s, %s)", (ready, Jsonb({"style": "UGC", "fidelity": "Low", "proof": "Doctor", "page": "p1", "type": "Iteration"})))
    pg.commit()
    assert client.post(url(ready, "/prepare")).status_code == 202
    drive, meta = Drive(), Meta()
    assert lw.run_once(pg, lambda: meta, lambda: drive) == "prepare"
    got = client.get(url(ready)).get_json()
    job = got["job"]
    assert job["kind"] == "prepare" and job["state"] == "done", job
    assert meta.calls == [] and len(job["plan"]["ads"]) == 2
    r = client.post(url(ready, "/confirm"), json={"job_id": job["id"], "plan_hash": job["plan_hash"]})
    assert r.status_code == 202, r.get_data(as_text=True)                       # the API accepts the hash the worker stored
    assert client.post(url(ready, "/confirm"), json={"job_id": job["id"], "plan_hash": "0" * 64}).status_code == 409
    assert lw.run_once(pg, lambda: meta, lambda: drive, sleep=lambda s: None) == "launch"
    got = client.get(url(ready)).get_json()
    assert got["job"]["kind"] == "launch" and got["job"]["state"] == "done"
    assert [a["state"] for a in got["ads"]] == ["created", "created"] and all(a["meta_ad_id"] for a in got["ads"])
    assert meta.calls.count("adset") == 1 and drive.downloads == ["d1", "d2"]
    assert "prepare_queued" in audit(pg) and "ad_created" in audit(pg)
