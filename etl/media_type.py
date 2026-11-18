"""Image / video tag of a Production ticket: the evidence rules, the backfill and the automatic fill.

The one stored tag is creative_priorities_assignments.media_type ('image' | 'video' | NULL); migration 276 adds media_type_source ('auto' | 'manual') and media_type_basis (the evidence).
An automatic fill only ever writes a ticket whose media_type is empty and whose source is not 'manual', so a choice made in the ticket panel (including clearing it) always wins.

Evidence, strongest first (the first one that says something decides; a ticket is never tagged from its title):
  a. explicit signals already on the ticket: the note line "Format: Video|Image", the note line "Type: VID|IMG", Video / Static in the Concept IDs, a creative format F1 to F6 (all video)
  b. the files the asset sync copied for the ticket (asset_sync_files): mime type, else extension
  c. the ticket's own links (creative_url, variations[].url): the files behind a Google Drive file or folder (mime types, read only), or a Vimeo / YouTube / Loom link
When both kinds are present (several Concept IDs, a folder with both) the tag is video. Nothing decides: it stays empty.

    python -m etl.media_type [--dry-run] [--no-drive] [--report FILE]      backfill every ticket (safe to re-run)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from types import SimpleNamespace

import psycopg
from psycopg.rows import dict_row

from . import db

HOST_VIDEO = ("vimeo", "youtube", "loom")                               # link types (etl.asset_sync.classify) that hold a video by nature


# ------------------------------------------------------------------------------------------------------------------------------ evidence a: the ticket itself
def from_note(note, story="") -> tuple[str, str] | None:
    """(media, basis) from the 'Format: Video' / 'Format: Image' line, else 'Type: VID' / 'Type: IMG'. Both kinds named = video."""
    text = f"{note or ''}\n{story or ''}"
    fmt = {m.lower() for m in re.findall(r"(?mi)^\s*Format:\s*(video|image|static)\b", text)}
    if fmt:
        return ("video" if "video" in fmt else "image"), "format_line"
    typ = {m.upper() for m in re.findall(r"(?mi)\bType:\s*(VID|IMG)\b", text)}
    if typ:
        return ("video" if "VID" in typ else "image"), "type_line"
    return None


def from_concept_ids(ids) -> tuple[str, str] | None:
    s = " ".join(ids or [])
    vid, img = bool(re.search(r"video", s, re.I)), bool(re.search(r"static|image", s, re.I))
    if vid or img:
        return ("video" if vid else "image"), "concept_ids"
    return None


def from_ticket(r: dict) -> tuple[str, str] | None:
    """Evidence a, in order: note lines, Concept IDs, creative format."""
    return from_note(r.get("note"), r.get("story")) or from_concept_ids(r.get("tracker_concept_ids")) or (("video", "format_code") if r.get("format_code") else None)


# ------------------------------------------------------------------------------------------------------------------------------ evidence b / c: files
def kinds(files) -> str | None:
    """'video' when any file is a video, else 'image' when any is an image, else None. files: iterable of (mime, name)."""
    from .asset_sync import media_kind
    ks = {media_kind(m, n) for m, n in files}
    return "video" if "Videos" in ks else "image" if "Images" in ks else None


def from_asset_files(conn, aid: int) -> tuple[str, str] | None:
    rows = conn.execute("SELECT mime, source_name FROM asset_sync_files WHERE assignment_id = %s AND status IN ('synced', 'duplicate')", (aid,)).fetchall()
    k = kinds((r["mime"], r["source_name"]) for r in rows)
    return (k, "asset_files") if k else None


def ticket_links(r: dict) -> list[tuple[str, str]]:
    from .asset_sync import classify, extract_urls
    out, seen = [], set()
    texts = [r.get("creative_url")] + [v.get("url") for v in (r.get("variations") or []) if isinstance(v, dict)]
    for t in texts:
        for u in extract_urls(t):
            if u not in seen:
                seen.add(u)
                out.append((u, classify(u)))
    return out


def from_links(cx, r: dict) -> tuple[tuple[str, str] | None, str]:
    """Evidence c. Returns ((media, basis) | None, why-not). cx needs a .drive (etl.asset_sync.Drive); a link that cannot be read (private, missing, no files) is skipped."""
    from .asset_sync import LinkError, drive_candidates
    found, errs = [], []
    for url, typ in ticket_links(r):
        if typ in HOST_VIDEO:
            found.append(("video", "video_host"))
        elif typ in ("drive_file", "drive_folder") and cx is not None:
            try:
                k = kinds((f.get("mimeType", ""), f.get("name", "")) for f in drive_candidates(cx, url, typ))
            except LinkError as e:
                errs.append(str(e)[:80])
                continue
            except Exception as e:                                                       # noqa: BLE001
                errs.append(f"{type(e).__name__}: {e}"[:80])
                continue
            if k:
                found.append((k, "drive_link"))
            else:
                errs.append("no image or video files behind the Drive link")
    if any(m == "video" for m, _ in found):
        return next(f for f in found if f[0] == "video"), ""
    if found:
        return found[0], ""
    return None, "; ".join(dict.fromkeys(errs)) or ("no link to look at" if not ticket_links(r) else "links are not Drive or video-host links")


# ------------------------------------------------------------------------------------------------------------------------------ writing
def apply(conn, aid: int, media: str, basis: str) -> bool:
    """Set the tag when it is still empty and no person has ever chosen for this ticket. True when a row changed."""
    cur = conn.execute("UPDATE creative_priorities_assignments SET media_type = %s, media_type_source = 'auto', media_type_basis = %s "
                       "WHERE id = %s AND media_type IS NULL AND media_type_source IS DISTINCT FROM 'manual'", (media, basis, aid))
    return cur.rowcount == 1


def autotag(conn, aid: int | None = None) -> int:
    """The hourly fill (no Drive calls): evidence a, then the files already copied. Every ticket without a tag, or just `aid`. Returns how many were tagged."""
    where, params = ("AND id = %s", (aid,)) if aid else ("", ())
    n = 0
    for r in conn.execute("SELECT id, note, story, tracker_concept_ids, format_code FROM creative_priorities_assignments WHERE media_type IS NULL AND media_type_source IS DISTINCT FROM 'manual' " + where, params).fetchall():
        hit = from_ticket(r) or from_asset_files(conn, r["id"])
        if hit and apply(conn, r["id"], *hit):
            n += 1
    return n


# ------------------------------------------------------------------------------------------------------------------------------ backfill
def backfill(conn, drive=None, dry_run: bool = False, use_drive: bool = True) -> dict:
    """Tag every ticket that has no tag yet. Returns {tagged: {basis: n}, image, video, null: [{id, why}], conflicts: [...]}. Idempotent; never touches a manual choice."""
    cx = SimpleNamespace(drive=drive) if (use_drive and drive is not None) else None
    rows = conn.execute("SELECT id, status, note, story, tracker_concept_ids, format_code, creative_url, variations, media_type, media_type_source FROM creative_priorities_assignments ORDER BY id").fetchall()
    out = {"tickets": len(rows), "already_tagged": 0, "tagged": {}, "image": 0, "video": 0, "null": [], "conflicts": [], "dry_run": dry_run}
    for r in rows:
        if r["media_type"] is not None or r["media_type_source"] == "manual":
            out["already_tagged"] += 1
            continue
        a = from_ticket(r)
        b = from_asset_files(conn, r["id"])
        hit = a or b
        c = None
        why = ""
        if hit is None:
            c, why = from_links(cx, r)
            hit = c
        else:                                                                           # evidence further down is only looked at to report a disagreement (files are free; Drive is not called)
            if a and b and a[0] != b[0]:
                out["conflicts"].append({"id": r["id"], "chosen": a, "files": b})
        if hit is None:
            out["null"].append({"id": r["id"], "status": r["status"], "why": why or "no signal on the ticket, no synced files, no link"})
            continue
        if dry_run or apply(conn, r["id"], *hit):
            out["tagged"][hit[1]] = out["tagged"].get(hit[1], 0) + 1
            out[hit[0]] += 1
    if not dry_run:
        conn.commit()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-drive", action="store_true", help="skip the Drive look-ups (evidence c)")
    ap.add_argument("--report", help="write the result as JSON to this file")
    a = ap.parse_args()
    from .asset_sync import Drive
    with db.JobRun("media_type_backfill") as job, psycopg.connect(db.database_url(), row_factory=dict_row) as conn:
        res = backfill(conn, drive=None if a.no_drive else Drive(), dry_run=a.dry_run, use_drive=not a.no_drive)
        job.rows_written = res["image"] + res["video"] if not a.dry_run else 0
        job.stats = {k: v for k, v in res.items() if k not in ("null", "conflicts")}
        print(json.dumps(res, indent=1, default=str))
        if a.report:
            with open(a.report, "w") as f:
                json.dump(res, f, indent=1, default=str)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:                                                                # noqa: BLE001
        db.log("media_type backfill FAILED:", db.scrub(e))
        sys.exit(1)
