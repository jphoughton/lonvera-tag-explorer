"""Launch worker for /priorities production (package P5): turns queued `creative_launch_jobs` rows into a plan (prepare) or into PAUSED Meta ads (launch).

DARK BUILD. Nothing runs unless META_LAUNCH_ENABLED=1 and META_LAUNCH_TOKEN is set on the service that runs this module, and every job is a dry run unless META_LAUNCH_DRY_RUN=0 AND the job
itself was queued with dry_run false. A dry run makes no Meta calls and no Drive downloads at all. The module is not wired to any Railway service.

PREPARE job: lists the files behind the assignment's creative_url (one file = one ad; a folder = one ad per image or video that is a direct child, natural name order, at most 10, the rest
listed as skipped), mints Concept IDs, builds the plan with the pure helpers in app/priorities_launch.py and stores plan + plan_hash (computed with app.priorities_launch.plan_hash, the same
function the API's confirm route re-runs on the stored plan).
LAUNCH job: for each ad of the approved plan, one at a time, with a checkpoint row in creative_launch_ads after every Meta step (planned, media_uploaded, creative_created, created, failed):
an ad that already exists in Meta under the exact ad name is adopted, never duplicated; the PAUSED ad set is looked up or created once (creative_launch_adsets); media, creative and ad are created
through etl/meta_write.py only. One ad failing does not stop the others. A later job resumes from the checkpoints. Every ad and ad set is created PAUSED.

The Meta and Drive clients are injected (see MetaClient / DriveClient); the default factories refuse to build a real client under pytest. Nothing here prints or stores a token: every error text
goes through meta_write.scrub.
Usage: python -m etl.launch_worker [--once] [--dry-run]     (without --once it polls every LAUNCH_WORKER_POLL_SECONDS, default 15)"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time

from psycopg.rows import dict_row

from . import db
from . import drive_io
from . import meta_write as mw

ACTOR = "launch_worker"
MAX_FILES = 10
PAUSE_AT_USAGE = 75.0
DEFAULT_MAX_ADS_PER_DAY = 50
STALE_RUNNING_MINUTES = 120
REQUIRED_SETTINGS = ("account_id", "campaign_id", "template_adset_id", "landing_url")


class LaunchError(Exception):
    """A problem with plain-English text safe to show in the app (still scrubbed before it is stored)."""


class StopJob(Exception):
    """Stop the launch loop: remaining ads stay planned and the job ends failed with this text."""


def _app():
    """app.priorities_launch holds the pure helpers; imported lazily so the etl package does not import Flask-side modules until the worker runs."""
    from app import priorities_launch as pl
    return pl


# ----------------------------------------------------------------------------------------------------- injected clients
class MetaClient:
    """The Meta operations the worker uses, as methods, wrapping etl/meta_write.py (the only module that may write to Meta). A test passes any object with the same methods."""

    def __init__(self, http=None):
        self.http = http if http is not None else mw.Transport()

    @property
    def usage_pct(self) -> float:
        return mw.usage_pct(self.http)

    def find_ad_by_name(self, account_id, name):
        return mw.find_ad_by_name(account_id, name, http=self.http)

    def clone_adset_paused(self, template_adset_id, name, daily_budget, bid_amount):
        return mw.clone_adset_paused(template_adset_id, name, daily_budget, bid_amount, http=self.http)

    def upload_image(self, account_id, data, filename):
        return mw.upload_image(account_id, data, filename, http=self.http)

    def upload_video(self, account_id, path, size, filename):
        return mw.upload_video(account_id, path, size, filename, http=self.http)

    def wait_video_ready(self, video_id):
        return mw.wait_video_ready(video_id, http=self.http)

    def video_thumbnail(self, video_id):
        return mw.video_thumbnail(video_id, http=self.http)

    def create_creative(self, account_id, spec):
        return mw.create_creative(account_id, spec, http=self.http)

    def create_paused_ad(self, account_id, adset_id, creative_id, name):
        return mw.create_paused_ad(account_id, adset_id, creative_id, name, http=self.http)


class DriveClient:
    """The Drive operations the worker uses, wrapping etl/drive_io.py (read only)."""

    def __init__(self, session=None):
        self.session = session

    def list_media(self, url, max_files):
        return drive_io.list_media(url, max_files=max_files, session=self.session)

    def download(self, file_id, dest_path):
        return drive_io.download(file_id, dest_path, session=self.session)


def _refuse_real_client_in_tests(what: str):
    if os.environ.get("PYTEST_CURRENT_TEST"):
        raise RuntimeError(f"launch_worker: a real {what} client was requested under pytest. Inject a fake.")


def default_meta_client():
    _refuse_real_client_in_tests("Meta")
    return MetaClient()


def default_drive_client():
    _refuse_real_client_in_tests("Drive")
    return DriveClient()


# ------------------------------------------------------------------------------------------------------------ db helpers
def _all(conn, sql, params=()):
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _one(conn, sql, params=()):
    rows = _all(conn, sql, params)
    return rows[0] if rows else None


def _audit(conn, action, aid=None, detail=None):
    conn.execute("INSERT INTO creative_launch_audit (actor, action, assignment_id, detail) VALUES (%s, %s, %s, %s)", (ACTOR, action, aid, db.jsonb(detail or {})))


def _scrub(e) -> str:
    return mw.scrub(str(e))[:500]


def reap_stale(conn, minutes: int = STALE_RUNNING_MINUTES) -> int:
    """A job still 'running' long after it started means the worker died; fail it so the row is not blocked forever (a launch resumes from its checkpoints)."""
    rows = conn.execute("UPDATE creative_launch_jobs SET state = 'failed', finished_at = now(), error = 'The worker stopped before finishing. Run it again; nothing is repeated.' "
                        "WHERE state = 'running' AND started_at < now() - make_interval(mins => %s) RETURNING id", (minutes,)).fetchall()
    conn.commit()
    return len(rows)


def claim_job(conn):
    """Oldest queued job (FOR UPDATE SKIP LOCKED), set to running. None when the queue is empty."""
    j = _one(conn, "SELECT id, assignment_id, kind, plan, plan_hash, dry_run FROM creative_launch_jobs WHERE state = 'queued' ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1")
    if not j:
        conn.rollback()
        return None
    conn.execute("UPDATE creative_launch_jobs SET state = 'running', started_at = now() WHERE id = %s", (j["id"],))
    conn.commit()
    return j


def _finish(conn, job_id, state, error=None, plan=None, plan_hash=None):
    if plan is not None:
        conn.execute("UPDATE creative_launch_jobs SET state = %s, error = %s, finished_at = now(), plan = %s, plan_hash = %s WHERE id = %s",
                     (state, error, db.jsonb(plan), plan_hash, job_id))
    else:
        conn.execute("UPDATE creative_launch_jobs SET state = %s, error = %s, finished_at = now() WHERE id = %s", (state, error, job_id))
    conn.commit()


# ---------------------------------------------------------------------------------------------------------------- prepare
def _settings(conn, product):
    return _one(conn, "SELECT s.*, p.instagram_user_id FROM creative_launch_settings s LEFT JOIN creative_launch_pages p ON p.page_id = s.default_page_id WHERE s.product = %s", (product,))


def build_prepare_plan(conn, drive, assignment_id: int) -> dict:
    """Everything prepare does, minus the job bookkeeping. Raises LaunchError with plain text when the row is not ready to plan."""
    pl = _app()
    a = _one(conn, "SELECT id, product, vendor, pain_point, icp, section_at_assign, for_week, creative_url FROM creative_priorities_assignments WHERE id = %s",
             (assignment_id,))
    if not a:
        raise LaunchError("The assignment no longer exists.")
    if not (a["creative_url"] or "").strip():
        raise LaunchError("Add the creative link first.")
    s = _settings(conn, a["product"])
    if not s:
        raise LaunchError("Launch settings for this product are not filled in yet.")
    draft = (_one(conn, "SELECT fields FROM creative_launch_drafts WHERE assignment_id = %s", (assignment_id,)) or {}).get("fields") or {}
    link = (draft.get("link") or "").strip() or s.get("landing_url")
    s = {**s, "landing_url": link}
    missing = [k.replace("_", " ") for k in REQUIRED_SETTINGS if not s.get(k)]
    if missing:
        raise LaunchError("Launch settings are missing: " + ", ".join(missing) + ".")
    page_id = (draft.get("page") or "").strip() or s.get("default_page_id")
    page = _one(conn, "SELECT page_id, name, instagram_user_id, name_token FROM creative_launch_pages WHERE page_id = %s AND active", (page_id,)) if page_id else None
    if not page:
        raise LaunchError("Pick a Page in the launch form first.")
    fields = {k: str(draft.get(k) or "").strip() for k in ("style", "fidelity", "type", "proof")}
    blank = [k for k in ("style", "fidelity", "proof") if not fields[k]]
    if blank:
        raise LaunchError("Pick " + ", ".join(blank) + " in the launch form first.")
    fields = {k: v for k, v in fields.items() if v}
    copy = pl.resolve_copy(draft, None, {"primary_texts": s.get("primary_texts"), "headlines": s.get("headlines")})
    if not copy["primary_texts"] or not copy["headlines"]:
        raise LaunchError("Add at least one primary text and one headline.")

    listed = drive.list_media(a["creative_url"], 1000)
    files = pl.order_files(listed.get("files") or [])
    skipped = [{"file_name": x.get("name"), "reason": x.get("reason")} for x in (listed.get("skipped") or [])]
    for f in files[MAX_FILES:]:
        skipped.append({"file_name": f.get("name"), "reason": f"over the limit of {MAX_FILES} files per launch"})
    files = files[:MAX_FILES]
    if not files:
        raise LaunchError("No images or videos were found at that link." + ((" Skipped: " + "; ".join(f"{x['file_name']} ({x['reason']})" for x in skipped[:5])) if skipped else ""))

    prefixes = {r["vendor"]: r["prefix"] for r in _all(conn, "SELECT vendor, prefix FROM creative_launch_vendor_prefixes")}
    prefix = pl.vendor_prefix(a["vendor"], prefixes)
    existing = {r["drive_file_id"]: r["concept_id"] for r in _all(conn, "SELECT drive_file_id, concept_id FROM creative_launch_ads WHERE assignment_id = %s", (assignment_id,))}
    if all(f["id"] in existing for f in files):
        cids = [existing[f["id"]] for f in files]                       # a re-prepare after a launch keeps the IDs already minted for these files
    else:
        conn.execute("DELETE FROM creative_launch_ads WHERE assignment_id = %s AND state = 'planned'", (assignment_id,))      # stale reservations from an earlier prepare of a different file set
        taken = {r["concept_id"] for r in _all(conn, "SELECT concept_id FROM creative_launch_ads")}
        taken |= {r["c"] for r in _all(conn, "SELECT concept_id AS c FROM tracker_rows WHERE concept_id IS NOT NULL")}
        taken |= {c for r in _all(conn, "SELECT tracker_concept_ids AS t FROM creative_priorities_assignments") for c in (r["t"] or [])}
        cids = pl.mint_concept_ids(prefix, a["product"], [f["kind"] for f in files], a["id"], taken)

    adset = _one(conn, "SELECT meta_adset_id FROM creative_launch_adsets WHERE product = %s AND vendor = %s AND for_week = %s", (a["product"], a["vendor"], a["for_week"]))
    plan = pl.build_plan(a, s, page, files, cids, fields, copy, adset)
    d = plan["destination"]
    plan.update({"adset_name": d["adset_name"], "adset_exists": not d["new_adset"], "primary_texts": copy["primary_texts"], "headlines": copy["headlines"], "link_url": s["landing_url"],
                 "page_id": page["page_id"], "daily_budget_cents": s.get("daily_budget_cents"), "cost_cap_cents": s.get("bid_amount_cents"), "skipped": skipped})
    return plan


def run_prepare(conn, job, drive) -> None:
    try:
        plan = build_prepare_plan(conn, drive, job["assignment_id"])
    except (LaunchError, drive_io.DriveError) as e:
        conn.rollback()
        _finish(conn, job["id"], "failed", _scrub(e))
        _audit(conn, "prepare_failed", job["assignment_id"], {"job_id": job["id"], "error": _scrub(e)})
        conn.commit()
        return
    h = _app().plan_hash(plan)
    for ad in plan["ads"]:                                                               # checkpoint rows start 'planned'; an existing row for the same file is left as it is
        conn.execute("INSERT INTO creative_launch_ads (assignment_id, job_id, drive_file_id, file_name, kind, concept_id, ad_name) VALUES (%s,%s,%s,%s,%s,%s,%s) "
                     "ON CONFLICT (assignment_id, drive_file_id) DO NOTHING",
                     (job["assignment_id"], job["id"], ad["file_id"], ad["file_name"], ad["kind"], ad["concept_id"], ad["ad_name"]))
    _finish(conn, job["id"], "done", None, plan, h)
    _audit(conn, "prepare_done", job["assignment_id"], {"job_id": job["id"], "plan_hash": h, "ads": len(plan["ads"]), "skipped": len(plan["skipped"])})
    conn.commit()


# ----------------------------------------------------------------------------------------------------------------- launch
def _max_ads_per_day(env=None) -> int:
    raw = (os.environ if env is None else env).get("META_LAUNCH_MAX_ADS_PER_DAY")
    try:
        n = int(raw)
        return n if n >= 0 else DEFAULT_MAX_ADS_PER_DAY
    except (TypeError, ValueError):
        return DEFAULT_MAX_ADS_PER_DAY


def _created_today(conn) -> int:
    return _one(conn, "SELECT count(*) AS n FROM creative_launch_audit WHERE action = 'ad_created' AND at >= date_trunc('day', now())")["n"]


def _wait_for_usage(meta, sleep, rounds: int = 10, pause: int = 60):
    """Pause new work while Meta usage is at 75% or more; give up (StopJob) after `rounds` pauses so the job ends and can be run again later."""
    for _ in range(rounds):
        if float(getattr(meta, "usage_pct", 0.0) or 0.0) < PAUSE_AT_USAGE:
            return
        db.log(f"  launch: Meta usage at {meta.usage_pct:.0f}%, pausing {pause}s")
        sleep(pause)
    if float(getattr(meta, "usage_pct", 0.0) or 0.0) >= PAUSE_AT_USAGE:
        raise StopJob("Paused because Meta usage stayed high. The remaining ads were not started; run it again later.")


def _ad_row(conn, aid, job_id, ad) -> dict:
    """Get or create the checkpoint row for one planned ad. An existing row for the same file keeps its Concept ID, ad name and Meta ids."""
    conn.execute("INSERT INTO creative_launch_ads (assignment_id, job_id, drive_file_id, file_name, kind, concept_id, ad_name) VALUES (%s,%s,%s,%s,%s,%s,%s) "
                 "ON CONFLICT (assignment_id, drive_file_id) DO NOTHING", (aid, job_id, ad["file_id"], ad["file_name"], ad["kind"], ad["concept_id"], ad["ad_name"]))
    r = _one(conn, "SELECT * FROM creative_launch_ads WHERE assignment_id = %s AND drive_file_id = %s", (aid, ad["file_id"]))
    conn.execute("UPDATE creative_launch_ads SET job_id = %s WHERE id = %s", (job_id, r["id"]))
    conn.commit()
    return r


def _checkpoint(conn, row_id, state, **cols):
    sets = ", ".join(f"{k} = %s" for k in cols)
    conn.execute(f"UPDATE creative_launch_ads SET state = %s, error = NULL, updated_at = now(){', ' + sets if sets else ''} WHERE id = %s", (state, *cols.values(), row_id))
    conn.commit()


def _ensure_adset(conn, meta, plan, state: dict) -> str:
    """The PAUSED ad set for this product x vendor x week: from the plan, else the table, else cloned from the template and recorded at once. Cached per job in `state`."""
    if state.get("adset_id"):
        return state["adset_id"]
    d = plan["destination"]
    pl_product, vendor, week = plan["product"], plan["vendor"], plan["for_week"]
    row = _one(conn, "SELECT meta_adset_id FROM creative_launch_adsets WHERE product = %s AND vendor = %s AND for_week = %s", (pl_product, vendor, week))
    adset_id = (row or {}).get("meta_adset_id") or d.get("adset_id")
    if not adset_id:
        adset_id = meta.clone_adset_paused(d["template_adset_id"], d["adset_name"], int(d["daily_budget_cents"]), int(d["bid_amount_cents"]))
        _audit(conn, "adset_created", plan["assignment_id"], {"adset_id": adset_id, "name": d["adset_name"]})
    conn.execute("INSERT INTO creative_launch_adsets (product, vendor, for_week, meta_adset_id) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING", (pl_product, vendor, week, adset_id))
    conn.commit()
    state["adset_id"] = str(adset_id)
    return state["adset_id"]


def _upload_media(conn, meta, drive, account, ad, row):
    """Download from Drive and upload to Meta; returns the updated row. Skips the work when the checkpoint already holds the hash or video id."""
    if row["kind"] == "image" and row["meta_image_hash"]:
        return row
    if row["kind"] == "video" and row["meta_video_id"]:
        return row
    with tempfile.TemporaryDirectory(prefix="launch_") as tmp:
        path = os.path.join(tmp, "media")
        size = drive.download(ad["file_id"], path)
        if row["kind"] == "image":
            with open(path, "rb") as f:
                h = meta.upload_image(account, f.read(), ad["file_name"])
            _checkpoint(conn, row["id"], "media_uploaded", meta_image_hash=h)
        else:
            vid = meta.upload_video(account, path, size or os.path.getsize(path), ad["file_name"])
            _checkpoint(conn, row["id"], "media_uploaded", meta_video_id=vid)
    return _one(conn, "SELECT * FROM creative_launch_ads WHERE id = %s", (row["id"],))


def _launch_one(conn, meta, drive, plan, ad, job_id, adset_state) -> str:
    """One ad through its steps. Returns 'created' or 'adopted'. Any exception propagates to the caller, which marks the row failed."""
    aid = plan["assignment_id"]
    d = plan["destination"]
    account = d["account_id"]
    row = _ad_row(conn, aid, job_id, ad)
    name = row["ad_name"]
    if row["state"] == "created" and row["meta_ad_id"]:
        return "skipped"
    found = meta.find_ad_by_name(account, name)
    if found:
        _checkpoint(conn, row["id"], "created", meta_ad_id=found["id"])
        _audit(conn, "ad_adopted", aid, {"concept_id": row["concept_id"], "ad_id": found["id"], "ad_name": name})
        conn.commit()
        return "adopted"
    adset_id = _ensure_adset(conn, meta, plan, adset_state)
    row = _upload_media(conn, meta, drive, account, ad, row)
    if not row["meta_creative_id"]:
        thumb = None
        if row["kind"] == "video":
            meta.wait_video_ready(row["meta_video_id"])
            thumb = meta.video_thumbnail(row["meta_video_id"])
        spec = mw.build_creative_spec(name, ad["page_id"], ad.get("instagram_user_id"), ad["link"], row["kind"], ad.get("primary_texts") or [], ad.get("headlines") or [],
                                      image_hash=row["meta_image_hash"], video_id=row["meta_video_id"], thumbnail_url=thumb, cta=ad.get("cta"),
                                      url_tags=ad.get("url_tags") or mw.DEFAULT_URL_TAGS)
        cid = meta.create_creative(account, spec)
        _checkpoint(conn, row["id"], "creative_created", meta_creative_id=cid)
        row = _one(conn, "SELECT * FROM creative_launch_ads WHERE id = %s", (row["id"],))
    ad_id = meta.create_paused_ad(account, adset_id, row["meta_creative_id"], name)
    _checkpoint(conn, row["id"], "created", meta_ad_id=ad_id, meta_adset_id=adset_id)
    _audit(conn, "ad_created", aid, {"concept_id": row["concept_id"], "ad_id": ad_id, "ad_name": name, "adset_id": adset_id})
    conn.commit()
    return "created"


def _append_concept_ids(conn, aid, cids):
    cur = _one(conn, "SELECT tracker_concept_ids AS t FROM creative_priorities_assignments WHERE id = %s", (aid,))
    if cur is None:
        return
    have = list(cur["t"] or [])
    new = [c for c in dict.fromkeys(cids) if c and c not in have]
    if new:
        conn.execute("UPDATE creative_priorities_assignments SET tracker_concept_ids = %s WHERE id = %s", (have + new, aid))
        _audit(conn, "concept_ids_added", aid, {"concept_ids": new})
    conn.commit()


def run_launch(conn, job, meta_factory, drive_factory, dry: bool, sleep=time.sleep) -> None:
    pl = _app()
    jid, aid, plan = job["id"], job["assignment_id"], job["plan"]
    if not plan or not job["plan_hash"] or pl.plan_hash(plan) != job["plan_hash"]:
        _finish(conn, jid, "failed", "The plan does not match its approval. Prepare again.")
        _audit(conn, "launch_failed", aid, {"job_id": jid, "error": "plan hash mismatch"})
        conn.commit()
        return
    ads = plan.get("ads") or []
    if dry or job["dry_run"]:
        _audit(conn, "launch_dry_run", aid, {"job_id": jid, "would_create_adset": bool(plan["destination"].get("new_adset")), "adset_name": plan["destination"].get("adset_name"),
                                              "ads": [{"concept_id": x["concept_id"], "ad_name": x["ad_name"], "file_name": x["file_name"]} for x in ads]})
        _finish(conn, jid, "done")                                                       # ads stay 'planned'; no tracker IDs are written for ads that were never created
        return
    if not os.environ.get(mw.TOKEN_ENV):
        _finish(conn, jid, "failed", f"{mw.TOKEN_ENV} is not set on the worker, so nothing was created.")
        _audit(conn, "launch_failed", aid, {"job_id": jid, "error": "no token"})
        conn.commit()
        return

    meta, drive = meta_factory(), drive_factory()
    cap, adset_state = _max_ads_per_day(), {}
    created, failed, adopted, stop = [], [], 0, None
    for ad in ads:
        row_before = _one(conn, "SELECT state FROM creative_launch_ads WHERE assignment_id = %s AND drive_file_id = %s", (aid, ad["file_id"]))
        try:
            if not (row_before and row_before["state"] == "created"):
                if _created_today(conn) >= cap:
                    raise StopJob(f"The daily limit of {cap} ads was reached. The remaining ads were not started; run it again tomorrow.")
                _wait_for_usage(meta, sleep)
            res = _launch_one(conn, meta, drive, plan, ad, jid, adset_state)
            if res == "adopted":
                adopted += 1
            created.append(ad["concept_id"])
        except StopJob as e:
            conn.rollback()
            stop = _scrub(e)
            break
        except Exception as e:                                                           # noqa: BLE001 - one ad failing must not stop the others
            conn.rollback()
            msg = _scrub(e)
            r = _one(conn, "SELECT id FROM creative_launch_ads WHERE assignment_id = %s AND drive_file_id = %s", (aid, ad["file_id"]))
            if r:
                conn.execute("UPDATE creative_launch_ads SET state = 'failed', error = %s, updated_at = now() WHERE id = %s", (msg, r["id"]))
            _audit(conn, "ad_failed", aid, {"concept_id": ad["concept_id"], "error": msg})
            conn.commit()
            failed.append(ad["concept_id"])
    _append_concept_ids(conn, aid, created)
    if stop:
        _finish(conn, jid, "failed", stop)
    elif failed:
        _finish(conn, jid, "failed", f"{len(failed)} of {len(ads)} ads failed. Run it again to retry only those.")
    else:
        _finish(conn, jid, "done")
    _audit(conn, "launch_finished", aid, {"job_id": jid, "created": len(created) - adopted, "adopted": adopted, "failed": len(failed), "stopped": bool(stop)})
    conn.commit()


# ------------------------------------------------------------------------------------------------------------------- loop
def enabled(env=None) -> tuple[bool, str]:
    """(True, '') when launching is switched on and a launch token is set or META_LAUNCH_DRY_RUN=1; else (False, the one log line explaining why). The token value is never read beyond its presence."""
    e = os.environ if env is None else env
    if not _app().launch_enabled(e):
        return False, "launch_worker: launching is not enabled (META_LAUNCH_ENABLED is not 1), nothing to do"
    if not e.get(mw.TOKEN_ENV) and e.get("META_LAUNCH_DRY_RUN") != "1":
        return False, f"launch_worker: neither {mw.TOKEN_ENV} nor META_LAUNCH_DRY_RUN=1 is set, nothing to do"
    return True, ""


def run_once(conn, meta_factory=None, drive_factory=None, dry_run: bool = False, sleep=time.sleep) -> str | None:
    """Claim and process one job. Returns the job kind handled, or None when the queue was empty. Factories are called only when a job needs them (a dry-run launch needs neither)."""
    reap_stale(conn)
    job = claim_job(conn)
    if not job:
        return None
    meta_factory = meta_factory or default_meta_client
    drive_factory = drive_factory or default_drive_client
    try:
        if job["kind"] == "prepare":
            run_prepare(conn, job, drive_factory())
        else:
            run_launch(conn, job, meta_factory, drive_factory, dry_run or _app().dry_run(), sleep)
    except Exception as e:                                                               # noqa: BLE001 - last resort: never leave a job 'running'
        conn.rollback()
        _finish(conn, job["id"], "failed", _scrub(e))
        _audit(conn, "job_crashed", job["assignment_id"], {"job_id": job["id"], "error": _scrub(e)})
        conn.commit()
        if os.environ.get("PYTEST_CURRENT_TEST") and isinstance(e, RuntimeError) and "Inject a fake" in str(e):
            raise
    return job["kind"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Launch worker: prepare plans and create paused Meta ads for /priorities production.")
    ap.add_argument("--once", action="store_true", help="process the queue until it is empty, then exit")
    ap.add_argument("--dry-run", action="store_true", help="never write to Meta, whatever the job says")
    args = ap.parse_args(argv)
    ok, why = enabled()
    if not ok:
        db.log(why)
        return 0
    poll = int(os.environ.get("LAUNCH_WORKER_POLL_SECONDS", "15"))
    with db.connect() as conn:
        while True:
            handled = run_once(conn, dry_run=args.dry_run)
            if handled:
                continue
            if args.once:
                return 0
            time.sleep(poll)


if __name__ == "__main__":
    sys.exit(main())
