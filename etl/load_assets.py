"""Load creative assets + per-family content from the Vita Bull tagging cycle folder.
  creative_assets: thumbs (media/NNNN.jpg, max 640px wide, q82) and video frames (frames/{video_id}_{k}.jpg and frames/drive_{n}_{k}.jpg, max 480px, q75),
                   keyed by tracker family_key via media_index.json (+ drive_index.json). Video families with no thumbnail get a fallback thumb cut from frame 1.
  family_content:  Meta copy (meta_copy.json), transcript (tracker transcript, else whisper), story_url / is_video / ad_id / video_id (media_index.json).
Other products' content lives on the tracker rows themselves. Usage: python -m etl.load_assets --cycle-dir C [--only thumbs|frames|content]"""
from __future__ import annotations

import argparse
import glob
import io
import json
from pathlib import Path

from . import db

DEFAULT_CYCLE = Path("/Users/joshhoughton/Desktop/Google Sheets/lonvera_creative_key/cycles/c2026-11-03-vb-linemap")
SOURCE = "cycle-2026-11-03"


def shrink(path: Path, max_w: int, quality: int):
    from PIL import Image
    with Image.open(path) as im:
        im = im.convert("RGB")
        if im.width > max_w:
            im = im.resize((max_w, max(1, round(im.height * max_w / im.width))), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=quality, optimize=True)
        return buf.getvalue(), im.width, im.height


def family_key_for(key: str, family_keys: set[str]) -> str | None:
    """media_index keys are lower(ad_family or concept_id); blank-family rows are 'id:'+concept in the DB."""
    if key in family_keys:
        return key
    if "id:" + key in family_keys:
        return "id:" + key
    return None


def upsert_asset(conn, fk, kind, idx, data, w, h, source=SOURCE):
    conn.execute("""INSERT INTO creative_assets (family_key, kind, idx, mime, width, height, bytes, source) VALUES (%s,%s,%s,'image/jpeg',%s,%s,%s,%s)
                    ON CONFLICT (family_key, kind, idx) DO UPDATE SET mime=EXCLUDED.mime, width=EXCLUDED.width, height=EXCLUDED.height,
                    bytes=EXCLUDED.bytes, source=EXCLUDED.source, created_at=now()""", (fk, kind, idx, w, h, data, source))


def load_transcripts(cd: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for f in ["video_transcripts.json"] + sorted(Path(p).name for p in glob.glob(str(cd / "video_transcripts_*.json"))):
        p = cd / f
        if p.exists():
            for k, v in json.loads(p.read_text()).items():
                if v.get("text"):
                    out.setdefault(k.lower(), v["text"])
    return out


def load(conn, cd: Path, only: str | None = None) -> dict:
    fkeys = {r[0] for r in conn.execute("SELECT DISTINCT family_key FROM tracker_rows").fetchall()}
    mi = json.loads((cd / "media_index.json").read_text())
    di = json.loads((cd / "drive_index.json").read_text()) if (cd / "drive_index.json").exists() else {}
    stats = {"families": len(mi), "unmatched_families": [], "thumbs": 0, "frames": 0, "fallback_thumbs": 0, "no_image_families": 0,
             "bytes_thumbs": 0, "bytes_frames": 0, "content": 0}
    resolved = {}
    for k in mi:
        fk = family_key_for(k, fkeys)
        if fk is None:
            stats["unmatched_families"].append(k)
        else:
            resolved[k] = fk
    for k in di:
        if k not in resolved:
            fk = family_key_for(k, fkeys)
            if fk:
                resolved[k] = fk
    has_thumb = set()
    if only in (None, "thumbs"):
        for k, rec in mi.items():
            fk = resolved.get(k)
            if not fk or not rec.get("thumb"):
                continue
            p = cd / rec["thumb"]
            if not p.exists():
                continue
            data, w, h = shrink(p, 640, 82)
            upsert_asset(conn, fk, "thumb", 0, data, w, h)
            has_thumb.add(fk)
            stats["thumbs"] += 1; stats["bytes_thumbs"] += len(data)
    if only in (None, "frames"):
        for k, fk in resolved.items():
            paths = []
            vid = (mi.get(k) or {}).get("video_id")
            if vid:
                paths += [cd / "frames" / f"{vid}_{i}.jpg" for i in range(4)]
            if k in di and di[k].get("frames"):
                paths += [cd / f for f in di[k]["frames"]]
            paths = [p for p in paths if p.exists()][:4]
            for i, p in enumerate(paths):
                data, w, h = shrink(p, 480, 75)
                upsert_asset(conn, fk, "frame", i, data, w, h)
                stats["frames"] += 1; stats["bytes_frames"] += len(data)
            # fallback thumbnail for video families whose creative has no thumbnail
            if paths and not (mi.get(k) or {}).get("thumb") and only is None:
                data, w, h = shrink(paths[min(1, len(paths) - 1)], 640, 82)
                upsert_asset(conn, fk, "thumb", 0, data, w, h, source=SOURCE + "-frame")
                stats["fallback_thumbs"] += 1
    if only in (None, "content"):
        meta_copy = {k.lower(): v for k, v in json.loads((cd / "meta_copy.json").read_text()).items()}
        whisper = load_transcripts(cd)
        drive_t = {k.lower(): v.get("text") for k, v in (json.loads((cd / "video_transcripts_drive.json").read_text()).items()
                                                         if (cd / "video_transcripts_drive.json").exists() else [])}
        tracker_tr = {}
        for fk, tr in conn.execute("SELECT family_key, transcript FROM tracker_rows WHERE transcript IS NOT NULL AND transcript <> '' ORDER BY sheet_row, id").fetchall():
            tracker_tr.setdefault(fk, tr)
        for k, fk in resolved.items():
            m = mi.get(k) or {}
            c = meta_copy.get(k) or {}
            tr, src = None, None
            if fk in tracker_tr:
                tr, src = tracker_tr[fk], "tracker"
            elif whisper.get(k):
                tr, src = whisper[k], "whisper"
            elif drive_t.get(k):
                tr, src = drive_t[k], "whisper"
            conn.execute("""INSERT INTO family_content (family_key, meta_body, meta_title, meta_desc, transcript, transcript_seconds, story_url, is_video, ad_id, video_id, source)
                            VALUES (%s,%s,%s,%s,%s,NULL,%s,%s,%s,%s,%s)
                            ON CONFLICT (family_key) DO UPDATE SET meta_body=EXCLUDED.meta_body, meta_title=EXCLUDED.meta_title, meta_desc=EXCLUDED.meta_desc,
                            transcript=EXCLUDED.transcript, story_url=EXCLUDED.story_url, is_video=EXCLUDED.is_video, ad_id=EXCLUDED.ad_id,
                            video_id=EXCLUDED.video_id, source=EXCLUDED.source""",
                         (fk, c.get("body"), c.get("title"), c.get("desc"), tr, m.get("story") or None, bool(m.get("video") or c.get("video") or m.get("video_id")),
                          m.get("ad_id") or c.get("ad_id") or None, m.get("video_id") or None, f"{SOURCE}:{src}" if src else SOURCE))
            stats["content"] += 1
    n_img = conn.execute("SELECT count(DISTINCT family_key) FROM creative_assets").fetchone()[0]
    stats["families_with_any_asset"] = n_img
    stats["families_with_thumb"] = conn.execute("SELECT count(*) FROM creative_assets WHERE kind='thumb'").fetchone()[0]
    stats["families_with_frames"] = conn.execute("SELECT count(DISTINCT family_key) FROM creative_assets WHERE kind='frame'").fetchone()[0]
    stats["no_image_families"] = len(resolved) - conn.execute(
        "SELECT count(DISTINCT family_key) FROM creative_assets WHERE family_key = ANY(%s)", (list(resolved.values()),)).fetchone()[0]
    stats["unmatched_families"] = stats["unmatched_families"][:10]
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cycle-dir", default=str(DEFAULT_CYCLE))
    ap.add_argument("--only", choices=["thumbs", "frames", "content"])
    a = ap.parse_args()
    with db.JobRun("load_assets") as run, db.connect() as conn:
        stats = load(conn, Path(a.cycle_dir), a.only)
        conn.commit()
        run.stats = stats
        run.rows_written = stats["thumbs"] + stats["frames"] + stats["fallback_thumbs"] + stats["content"]
        db.log(f"  {stats}")


if __name__ == "__main__":
    main()
