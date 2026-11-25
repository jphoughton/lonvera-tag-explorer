"""Nightly step: transcribe the video ads on Production cards that are Ready to launch, from the real audio, into creative_ad_transcripts.

What it does: for every card with status 'ready' (not dropped) it reads the variation links from the card's story ("Variations" section, "Original: <link>", "Hook 2: <link>") and the card's
creative link, keeps the Google Drive links, skips any ad that already has a transcript, downloads each remaining file through the Drive service account (read-only, etl.drive_io), and runs
faster-whisper (base.en, CPU, int8) on the first 180 seconds of audio. One row per ad (concept_id, the tracker Concept ID of the variation); a stored transcript is never overwritten. A
silent video is stored as [no speech]; an image is skipped (nothing to transcribe); a link that is not a Drive file (Frame.io and others need a login this job does not have) is counted and skipped.
Second source (Meta): every ad family with Meta spend since META_SINCE (default 2026-11-16) and no transcript yet, biggest spend first, is fetched the way etl.own_ad_tags does (yt-dlp on the
facebook.com video, else the ad-preview iframe) and transcribed; statics are skipped and counted. Stored under the family's tracker Concept ID (else its family key) and in ad_transcript
(source 'whisper') so the tagger finds it. At most MAX_PER_RUN ads and TIME_BUDGET_S seconds per run (Drive first, then Meta), so a backlog clears over several nights. Nothing is written to Google Sheets, the Creative Tracker or Meta.
Usage: python -m etl.transcribe_ads [--dry-run] [--limit N]"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

import psycopg
from psycopg.rows import dict_row

from . import db, drive_io

MODEL = os.environ.get("ADS_WHISPER_MODEL", "base.en")
MAX_SECONDS = 180
MAX_PER_RUN = int(os.environ.get("TRANSCRIBE_MAX_PER_RUN", "20"))
TIME_BUDGET_S = int(os.environ.get("TRANSCRIBE_TIME_BUDGET_S", "1500"))
META_SINCE = os.environ.get("TRANSCRIBE_META_SINCE", "2026-11-16")
NO_SPEECH = "[no speech]"
CREATED_BY = "etl.transcribe_ads"

_model = None


def variations(rec: dict) -> list[dict]:
    """[{concept_id, label, url}] for one card: every 'label: link' line under the story's Variations heading, each tied to the card's tracker Concept ID that ends in the same label / hook number
    (else <base>-hook<n>, else <base>.<label>); the base ID (no -hookN) is the Original. A card with no variation lines but a creative link gives one Original."""
    tids = list(rec.get("tracker_concept_ids") or [])
    base = next((t for t in tids if not re.search(r"-hook\d+$", t, re.I)), tids[0] if tids else None)
    story = rec.get("story") or ""
    section = story.split("Variations", 1)[1] if "Variations" in story else ""
    found: dict[str, str] = {}
    for ln in section.splitlines():
        m = re.match(r"\s*([^:\s][^:]*?):\s*(https?://\S+)", ln)
        if m:
            found[m.group(1).strip()] = m.group(2)
    out = []
    for label, url in found.items():
        mh = re.match(r"hook\s*(\d+)$", label, re.I)
        n = mh.group(1) if mh else None
        cands = [t for t in tids if t == label or t.endswith("-" + label) or t.endswith("." + label) or (n and re.search(rf"-hook{n}$", t, re.I))]
        if re.fullmatch(r"original", label, re.I) and base:
            cid = base
        elif cands:
            cid = cands[0]
        elif n and base:
            cid = f"{base}-hook{n}"
        elif base and label.isdigit():
            cid = f"{base}.{label}"
        else:
            cid = label
        hm = re.search(r"hook(\d+)", cid, re.I)
        out.append({"concept_id": cid, "label": "Original" if cid == base else (f"Hook {hm.group(1)}" if hm else cid), "url": url})
    if not out and base and rec.get("creative_url"):
        out.append({"concept_id": base, "label": "Original", "url": rec["creative_url"]})
    return out


def candidates(conn) -> tuple[list[dict], dict]:
    """(to_do, stats): Drive-hosted variations of Ready cards that have no transcript yet. Other hosts are counted, not attempted."""
    stats = {"cards": 0, "variations": 0, "already": 0, "not_drive": 0}
    recs = conn.execute("SELECT id, tracker_concept_ids, creative_url, story FROM creative_priorities_assignments WHERE status = 'ready' AND dropped_at IS NULL ORDER BY id").fetchall()
    have = {r["concept_id"] for r in conn.execute("SELECT concept_id FROM creative_ad_transcripts").fetchall()}
    todo, seen = [], set()
    for rec in recs:
        stats["cards"] += 1
        for v in variations(rec):
            stats["variations"] += 1
            if v["concept_id"] in have or v["concept_id"] in seen:
                stats["already"] += 1
                continue
            try:
                kind, fid = drive_io.parse_link(v["url"])
            except drive_io.DriveError:
                stats["not_drive"] += 1
                continue
            if kind != "file":
                stats["not_drive"] += 1
                continue
            seen.add(v["concept_id"])
            todo.append({**v, "assignment_id": rec["id"], "file_id": fid})
    return todo, stats


META_SQL = """
WITH sp AS (SELECT d.ad_id, sum(d.spend) AS spend FROM ad_daily d WHERE d.day >= %(since)s AND d.ad_id NOT LIKE 'fam:%%' GROUP BY d.ad_id HAVING sum(d.spend) > 0),
fam AS (SELECT COALESCE(m.tracker_family_key, a.meta_family_key) AS family_key, sum(s.spend) AS spend, (array_agg(a.ad_id ORDER BY s.spend DESC, a.ad_id))[1] AS top_ad_id
        FROM sp s JOIN meta_ads a ON a.ad_id = s.ad_id LEFT JOIN meta_family_map m ON m.meta_family_key = a.meta_family_key
        WHERE COALESCE(m.tracker_family_key, a.meta_family_key) IS NOT NULL AND COALESCE(m.tracker_family_key, a.meta_family_key) <> ''
        GROUP BY 1)
SELECT f.family_key, f.spend, f.top_ad_id, t.concept_id, t.ad_type, c.is_video, c.video_id, c.ad_id AS content_ad_id
FROM fam f
LEFT JOIN LATERAL (SELECT r.concept_id, r.ad_type FROM tracker_rows r WHERE r.family_key = f.family_key AND r.deleted_at IS NULL ORDER BY r.sheet_row, r.id LIMIT 1) t ON true
LEFT JOIN family_content c ON c.family_key = f.family_key
ORDER BY f.spend DESC, f.family_key
"""


def meta_candidates(conn, have: set[str], since: str | None = None) -> tuple[list[dict], dict]:
    """(to_do, stats): ad families with Meta spend since `since` and no transcript yet (compared case-insensitively on the stored concept_id), highest spend first. A family known to be a static
    (tracker type IMG, or family_content says not a video) is counted in meta_static and left out. concept_id = the family's tracker Concept ID, else its family key."""
    stats = {"meta_candidates": 0, "meta_already": 0, "meta_static": 0}
    todo = []
    for r in conn.execute(META_SQL, {"since": since or META_SINCE}).fetchall():
        cid = r["concept_id"] or r["family_key"]
        if cid.lower() in have or r["family_key"].lower() in have:
            stats["meta_already"] += 1
            continue
        if (r["ad_type"] or "").upper() == "IMG" or r["is_video"] is False:
            stats["meta_static"] += 1
            continue
        stats["meta_candidates"] += 1
        todo.append({"meta": True, "concept_id": cid, "family_key": r["family_key"], "label": "Meta", "url": None, "assignment_id": None, "file_id": None,
                     "top_ad_id": r["top_ad_id"], "video_id": r["video_id"], "content_ad_id": r["content_ad_id"], "spend": float(r["spend"] or 0)})
    return todo, stats


def _decode(path: str, run=subprocess.run):
    """(duration_s, samples or None when the file has no audio). Reads at most MAX_SECONDS of audio as mono 16 kHz float32 through ffmpeg."""
    import numpy as np
    probe = run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type:format=duration", "-of", "json", path], capture_output=True, timeout=120)
    info = json.loads(probe.stdout or "{}")
    dur = float((info.get("format") or {}).get("duration") or 0)
    if dur <= 0:
        raise ValueError("not a playable video")
    if not any(s.get("codec_type") == "audio" for s in info.get("streams") or []):
        return dur, None
    r = run(["ffmpeg", "-v", "error", "-t", str(MAX_SECONDS), "-i", path, "-vn", "-f", "f32le", "-ac", "1", "-ar", "16000", "-"], capture_output=True, timeout=300)
    if r.returncode != 0:
        raise ValueError("ffmpeg could not read the audio")
    audio = np.frombuffer(r.stdout, dtype=np.float32)
    return dur, (audio if audio.size >= 1600 else None)


def transcribe(path: str) -> dict:
    """{duration_s, language, text} for one downloaded video. Raises on an unreadable file or when faster-whisper is not installed."""
    global _model
    dur, audio = _decode(path)
    if audio is None:
        return {"duration_s": round(dur, 1), "language": None, "text": NO_SPEECH}
    if _model is None:
        from faster_whisper import WhisperModel
        _model = WhisperModel(MODEL, device="cpu", compute_type="int8", cpu_threads=int(os.environ.get("WHISPER_THREADS", "4")))
    segs, info = _model.transcribe(audio, beam_size=5, vad_filter=True, condition_on_previous_text=False)
    text = " ".join(s.text.strip() for s in segs).strip()
    text = re.sub(r"(\s*\.){3,}\s*$", "", text).strip()
    return {"duration_s": round(dur, 1), "language": getattr(info, "language", "en"), "text": text or NO_SPEECH}


def fetch(file_id: str, dest: str, session=None) -> str:
    """Download one Drive file to dest. Returns 'video', or 'image' / 'other' when there is nothing to transcribe (not downloaded). Raises drive_io.DriveError when Drive refuses."""
    s = session or drive_io._default_session()
    meta = drive_io._get_json(s, f"files/{file_id}", {"fields": drive_io.FIELDS}, "that file")
    mime = meta.get("mimeType", "")
    if mime.startswith("video/"):
        if int(meta.get("size") or 0) > drive_io.VIDEO_MAX:
            raise drive_io.DriveError("video is over 500 MB")
        drive_io.download(file_id, dest, session=s)
        return "video"
    return "image" if mime.startswith("image/") else "other"


def store(conn, it: dict, r: dict) -> int:
    if it.get("meta"):
        from . import own_ad_tags
        src = f"https://www.facebook.com/watch/?v={it['video_id']}" if it.get("video_id") else None
        own_ad_tags.store_transcript(conn, it["family_key"], r["text"], r["language"], r["duration_s"], "whisper", f"faster-whisper-{MODEL}-int8")
        it = {**it, "url": src}
    cur = conn.execute("""INSERT INTO creative_ad_transcripts (concept_id, assignment_id, label, source_url, file_id, language, duration_s, engine, model, transcript, created_by)
                          VALUES (%s,%s,%s,%s,%s,%s,%s,'faster-whisper',%s,%s,%s) ON CONFLICT (concept_id) DO NOTHING""",
                       (it["concept_id"], it["assignment_id"], it["label"], it["url"], it["file_id"], r["language"], r["duration_s"], MODEL, r["text"], CREATED_BY))
    return cur.rowcount


def fetch_meta(it: dict, dest: str) -> str:
    """Meta route of etl.own_ad_tags (yt-dlp on the facebook video, else the ad preview). -> 'video'; raises own_ad_tags.VideoSkip when nothing could be downloaded."""
    from . import own_ad_tags
    own_ad_tags.fetch_video(it, dest)
    return "video"


def run(conn, dry_run: bool = False, limit: int | None = None, log=db.log, fetch_fn=fetch, transcribe_fn=transcribe, budget_s: int | None = None, meta_fetch_fn=fetch_meta) -> dict:
    stats = {"dry_run": dry_run, "table_missing": False, "stored": 0, "no_speech": 0, "images_skipped": 0, "errors": [], "deferred": 0, "meta_candidates": 0, "meta_already": 0, "meta_static": 0, "meta_fetch_failed": 0, "meta_stored": 0}
    try:
        todo, cs = candidates(conn)
    except (psycopg.errors.UndefinedTable, psycopg.errors.UndefinedColumn):
        conn.rollback()
        stats["table_missing"] = True
        log("  transcribe_ads: migration 249 or the board columns do not exist yet, nothing to do")
        return stats
    stats.update(cs)
    have = {r["concept_id"].lower() for r in conn.execute("SELECT concept_id FROM creative_ad_transcripts").fetchall()} | {x["concept_id"].lower() for x in todo}
    mtodo, ms = meta_candidates(conn, have)
    stats.update(ms)
    todo = todo + mtodo
    stats["to_do"] = len(todo)
    cap = MAX_PER_RUN if limit is None else limit
    budget = TIME_BUDGET_S if budget_s is None else budget_s
    if dry_run:
        log(f"  transcribe_ads: {len(todo)} ads waiting ({len(todo) - len(mtodo)} Drive, {len(mtodo)} Meta; {cs['already']} already done, {cs['not_drive']} not on Drive, {ms['meta_static']} Meta statics skipped); dry run, nothing downloaded")
        return stats
    if todo and not shutil.which("ffmpeg"):
        stats["errors"].append("ffmpeg is not installed here")
        log("  transcribe_ads: ffmpeg missing, nothing done")
        return stats
    t0, done = time.time(), 0
    for it in todo:
        if done >= cap or time.time() - t0 > budget:
            stats["deferred"] += 1
            continue
        try:
            with tempfile.TemporaryDirectory() as td:
                path = os.path.join(td, "v.mp4")
                if it.get("meta"):
                    try:
                        kind = meta_fetch_fn(it, path)
                    except Exception as e:                             # noqa: BLE001 - VideoSkip (no source / download failed): counted, retried next run
                        stats["meta_fetch_failed"] += 1
                        log(f"  transcribe_ads: {it['concept_id']} not fetched ({db.scrub(e)[:80]})")
                        continue
                else:
                    kind = fetch_fn(it["file_id"], path)
                if kind != "video":
                    stats["images_skipped"] += 1
                    continue
                r = transcribe_fn(path)
            n = store(conn, it, r)
            stats["stored"] += n
            stats["meta_stored"] += n if it.get("meta") else 0
            conn.commit()
            done += 1
            if r["text"] == NO_SPEECH:
                stats["no_speech"] += 1
            log(f"  transcribe_ads: {it['concept_id']} ({r['duration_s']}s) stored")
        except Exception as e:                                         # noqa: BLE001 - one bad file must not stop the rest
            conn.rollback()
            stats["errors"].append({"concept_id": it["concept_id"], "why": db.scrub(e)[:160]})
    log(f"  transcribe_ads: {stats['stored']} stored, {stats['meta_stored']} from Meta, {stats['images_skipped']} images skipped, {stats['meta_fetch_failed']} Meta fetch failures, {len(stats['errors'])} errors, {stats['deferred']} left for the next run")
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="count the ads waiting; download and write nothing")
    ap.add_argument("--limit", type=int, help="most ads to transcribe this run (default TRANSCRIBE_MAX_PER_RUN)")
    a = ap.parse_args()
    with db.JobRun("transcribe_ads") as job, psycopg.connect(db.database_url(), row_factory=dict_row) as conn:
        st = run(conn, dry_run=a.dry_run, limit=a.limit)
        job.stats = st
        job.rows_written = st["stored"]
        if st["errors"]:
            job.note = f"{len(st['errors'])} ads failed: " + "; ".join(f"{e['concept_id']}: {e['why']}" if isinstance(e, dict) else str(e) for e in st["errors"][:5])
        db.log(f"  {json.dumps(st, default=str)[:600]}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:                                                # noqa: BLE001
        db.log("transcribe_ads FAILED:", db.scrub(e))
        sys.exit(1)
