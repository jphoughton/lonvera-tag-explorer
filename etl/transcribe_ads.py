"""Nightly step: transcribe the video ads on Production cards that are Ready to launch, from the real audio, into creative_ad_transcripts.

What it does: for every card with status 'ready' (not dropped) it reads the variation links from the card's story ("Variations" section, "Original: <link>", "Hook 2: <link>") and the card's
creative link, keeps the Google Drive links, skips any ad that already has a transcript, downloads each remaining file through the Drive service account (read-only, etl.drive_io), and runs
faster-whisper (base.en, CPU, int8) on the first 180 seconds of audio. One row per ad (concept_id, the tracker Concept ID of the variation); a stored transcript is never overwritten. A
silent video is stored as [no speech]; an image is skipped (nothing to transcribe); a link that is not a Drive file (Frame.io and others need a login this job does not have) is counted and skipped.
At most MAX_PER_RUN ads and TIME_BUDGET_S seconds per run, so a backlog clears over several nights. Nothing is written to Google Sheets, the Creative Tracker or Meta.
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
    cur = conn.execute("""INSERT INTO creative_ad_transcripts (concept_id, assignment_id, label, source_url, file_id, language, duration_s, engine, model, transcript, created_by)
                          VALUES (%s,%s,%s,%s,%s,%s,%s,'faster-whisper',%s,%s,%s) ON CONFLICT (concept_id) DO NOTHING""",
                       (it["concept_id"], it["assignment_id"], it["label"], it["url"], it["file_id"], r["language"], r["duration_s"], MODEL, r["text"], CREATED_BY))
    return cur.rowcount


def run(conn, dry_run: bool = False, limit: int | None = None, log=db.log, fetch_fn=fetch, transcribe_fn=transcribe, budget_s: int | None = None) -> dict:
    stats = {"dry_run": dry_run, "table_missing": False, "stored": 0, "no_speech": 0, "images_skipped": 0, "errors": [], "deferred": 0}
    try:
        todo, cs = candidates(conn)
    except (psycopg.errors.UndefinedTable, psycopg.errors.UndefinedColumn):
        conn.rollback()
        stats["table_missing"] = True
        log("  transcribe_ads: migration 249 or the board columns do not exist yet, nothing to do")
        return stats
    stats.update(cs)
    stats["to_do"] = len(todo)
    cap = MAX_PER_RUN if limit is None else limit
    budget = TIME_BUDGET_S if budget_s is None else budget_s
    if dry_run:
        log(f"  transcribe_ads: {len(todo)} ads waiting ({cs['already']} already done, {cs['not_drive']} not on Drive); dry run, nothing downloaded")
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
                kind = fetch_fn(it["file_id"], path)
                if kind != "video":
                    stats["images_skipped"] += 1
                    continue
                r = transcribe_fn(path)
            stats["stored"] += store(conn, it, r)
            conn.commit()
            done += 1
            if r["text"] == NO_SPEECH:
                stats["no_speech"] += 1
            log(f"  transcribe_ads: {it['concept_id']} ({r['duration_s']}s) stored")
        except Exception as e:                                         # noqa: BLE001 - one bad file must not stop the rest
            conn.rollback()
            stats["errors"].append({"concept_id": it["concept_id"], "why": db.scrub(e)[:160]})
    log(f"  transcribe_ads: {stats['stored']} stored, {stats['images_skipped']} images skipped, {len(stats['errors'])} errors, {stats['deferred']} left for the next run")
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
