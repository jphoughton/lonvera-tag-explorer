"""Load creative assets + per-family content from a tagging cycle folder (Vita Bull 2026-11-03 or Beef Liver 2026-11-04 shape).
  creative_assets: thumbs (max 640px wide, q82) and video frames (max 480px, q75), keyed by tracker family_key via media_index.json (+ drive_index.json).
      Vita Bull cycle: frames/{video_id}_{k}.jpg and frames/drive_{n}_{k}.jpg (4 separate files).
      Beef Liver cycle: one 2x2 contact sheet per video (video_index*.json -> media/NNNN_v.jpg) that is cut into 4 frames (idx 0..3);
      thumbs come from media_index.thumb, else drive_index.thumb. Video families with no thumbnail get a fallback thumb cut from frame 1.
  family_content:  Meta copy (meta_copy.json), transcript (Vita Bull: tracker transcript first, else whisper; Beef Liver: the video-index transcript the tagger
                   used, else the tracker transcript), story_url / is_video / ad_id / video_id. Existing non-null content is kept when the cycle has none.
Other products' content lives on the tracker rows themselves.
Usage: python -m etl.load_assets --cycle-dir C [--product beef-liver] [--only thumbs|frames|content]
--product restricts the load to that product's families (other products' assets are never touched)."""
from __future__ import annotations

import argparse
import glob
import io
import json
import re
from pathlib import Path

from . import db

DEFAULT_CYCLE = Path("/Users/joshhoughton/Desktop/Google Sheets/lonvera_creative_key/cycles/c2026-11-03-vb-linemap")
BL_CYCLE = Path("/Users/joshhoughton/Desktop/Google Sheets/lonvera_creative_key/cycles/c2026-11-04-bl-linemap")
SOURCE = "cycle-2026-11-03"
VIDEO_INDEX_FILES = ("video_index.json", "video_index_d2.json", "video_index_d3.json", "video_index_d4.json", "video_index_meta.json", "video_index_vw.json")


def source_for(cd: Path) -> str:
    """'cycle-YYYY-MM-DD' from a cycle dir name like c2026-11-04-bl-linemap (default: the Vita Bull label)."""
    m = re.match(r"c(\d{4}-\d{2}-\d{2})", cd.name)
    return f"cycle-{m.group(1)}" if m else SOURCE


def _truthy(v) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes")
    return bool(v)


def _prep(im, max_w: int, quality: int):
    im = im.convert("RGB")
    if im.width > max_w:
        from PIL import Image
        im = im.resize((max_w, max(1, round(im.height * max_w / im.width))), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=quality, optimize=True)
    return buf.getvalue(), im.width, im.height


def shrink(path: Path, max_w: int, quality: int):
    from PIL import Image
    with Image.open(path) as im:
        return _prep(im, max_w, quality)


def _quadrants(im):
    w2, h2 = im.width // 2, im.height // 2
    return [im.crop(box) for box in ((0, 0, w2, h2), (w2, 0, 2 * w2, h2), (0, h2, w2, 2 * h2), (w2, h2, 2 * w2, 2 * h2))]


def contact_sheet_tiles(path: Path, max_w: int = 480, quality: int = 75) -> list[tuple[bytes, int, int]]:
    """A 2x2 contact sheet (top-left, top-right, bottom-left, bottom-right) -> 4 frame images."""
    from PIL import Image
    with Image.open(path) as im:
        return [_prep(t, max_w, quality) for t in _quadrants(im)]


def contact_sheet_thumb(path: Path, idx: int = 1, max_w: int = 640, quality: int = 82):
    """One quadrant of a contact sheet as a thumbnail (same size/quality settings as a regular thumb)."""
    from PIL import Image
    with Image.open(path) as im:
        return _prep(_quadrants(im)[idx], max_w, quality)


def family_key_for(key: str, family_keys: set[str]) -> str | None:
    """media_index keys are lower(ad_family or concept_id); blank-family rows are 'id:'+concept in the DB."""
    if key in family_keys:
        return key
    if "id:" + key in family_keys:
        return "id:" + key
    return None


def family_keys_for(key: str, family_keys: set[str]) -> list[str]:
    """Every tracker family the cycle key stands for: the named family and/or the blank-family 'id:' twin (same ad name, one unit in the cycle)."""
    return [k for k in (key, "id:" + key) if k in family_keys]


def upsert_asset(conn, fk, kind, idx, data, w, h, source=SOURCE, keep_existing=False):
    """keep_existing: the family_key also belongs to another product (assets are keyed by family_key alone), so an asset that is already there stays."""
    on_conflict = "DO NOTHING" if keep_existing else ("DO UPDATE SET mime=EXCLUDED.mime, width=EXCLUDED.width, height=EXCLUDED.height, "
                                                       "bytes=EXCLUDED.bytes, source=EXCLUDED.source, created_at=now()")
    conn.execute(f"""INSERT INTO creative_assets (family_key, kind, idx, mime, width, height, bytes, source) VALUES (%s,%s,%s,'image/jpeg',%s,%s,%s,%s)
                     ON CONFLICT (family_key, kind, idx) {on_conflict}""", (fk, kind, idx, w, h, data, source))


def load_transcripts(cd: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for f in ["video_transcripts.json"] + sorted(Path(p).name for p in glob.glob(str(cd / "video_transcripts_*.json"))):
        p = cd / f
        if p.exists():
            for k, v in json.loads(p.read_text()).items():
                if v.get("text"):
                    out.setdefault(k.lower(), v["text"])
    return out


def load_video_index(cd: Path) -> dict:
    """Beef Liver shape: per-family video info ({frames: contact sheet path, transcript, transcript_source, dur}). Later files win; the
    video_index_fb.json entries replace an earlier one when they carry frames (same rule the tagging cycle used)."""
    vi: dict = {}
    for f in VIDEO_INDEX_FILES:
        if (cd / f).exists():
            vi.update(json.loads((cd / f).read_text()))
    if (cd / "video_index_fb.json").exists():
        for k, v in json.loads((cd / "video_index_fb.json").read_text()).items():
            if v.get("frames") or k not in vi:
                vi[k] = v
    return vi


def story_url(m: dict, c: dict) -> str | None:
    if m.get("story"):
        return m["story"]
    raw = c.get("story")                                    # meta_copy: '<page>_<post>'
    if raw and "_" in raw:
        page, post = raw.split("_", 1)
        return f"https://www.facebook.com/{page}/posts/{post}/"
    return None


def load(conn, cd: Path, only: str | None = None, product: str | None = None) -> dict:
    src = source_for(cd)
    if product:
        fkeys = {r[0] for r in conn.execute("SELECT DISTINCT t.family_key FROM tracker_rows t JOIN products p ON p.id=t.product_id WHERE p.slug=%s",
                                            (product,)).fetchall()}
    else:
        fkeys = {r[0] for r in conn.execute("SELECT DISTINCT family_key FROM tracker_rows").fetchall()}
    shared: set[str] = set()                    # family_keys that also belong to another product: existing assets / content of those are never overwritten
    if product:
        shared = {r[0] for r in conn.execute("SELECT DISTINCT t.family_key FROM tracker_rows t JOIN products p ON p.id=t.product_id "
                                             "WHERE p.slug <> %s AND t.family_key = ANY(%s)", (product, sorted(fkeys))).fetchall()}
    mi = json.loads((cd / "media_index.json").read_text())
    di = json.loads((cd / "drive_index.json").read_text()) if (cd / "drive_index.json").exists() else {}
    vi = load_video_index(cd)
    bl_shape = (cd / "video_index.json").exists()                      # contact-sheet cycle
    stats = {"source": src, "product": product, "families": len(mi), "unmatched_families": [], "thumbs": 0, "frames": 0, "fallback_thumbs": 0,
             "no_image_families": 0, "bytes_thumbs": 0, "bytes_frames": 0, "content": 0, "dual_family_keys": 0, "shared_with_other_products": len(shared)}
    resolved: dict[str, list[str]] = {}
    for k in list(mi) + [k for k in di if k not in mi]:
        fks = family_keys_for(k, fkeys)
        if not fks:
            stats["unmatched_families"].append(k)
        else:
            resolved[k] = fks
            stats["dual_family_keys"] += len(fks) > 1
    if only in (None, "thumbs"):
        for k, fks in resolved.items():
            rec, dv = mi.get(k) or {}, di.get(k) or {}
            rel = rec.get("thumb") or dv.get("thumb")
            if not rel or not (cd / rel).exists():
                continue
            data, w, h = shrink(cd / rel, 640, 82)
            for fk in fks:
                upsert_asset(conn, fk, "thumb", 0, data, w, h, src, fk in shared)
                stats["thumbs"] += 1; stats["bytes_thumbs"] += len(data)
    if only in (None, "frames"):
        for k, fks in resolved.items():
            rec, dv, vv = mi.get(k) or {}, di.get(k) or {}, vi.get(k) or {}
            tiles: list[tuple[bytes, int, int]] = []
            make_fallback = None
            if bl_shape:
                if vv.get("frames") and (cd / vv["frames"]).exists():
                    sheet = cd / vv["frames"]
                    tiles = contact_sheet_tiles(sheet)
                    make_fallback = lambda sheet=sheet: contact_sheet_thumb(sheet, 1)
            else:
                paths = []
                vid = rec.get("video_id")
                if vid:
                    paths += [cd / "frames" / f"{vid}_{i}.jpg" for i in range(4)]
                if dv.get("frames"):
                    paths += [cd / f for f in dv["frames"]]
                paths = [p for p in paths if p.exists()][:4]
                tiles = [shrink(p, 480, 75) for p in paths]
                if paths:
                    make_fallback = lambda p=paths[min(1, len(paths) - 1)]: shrink(p, 640, 82)
            for fk in fks:
                for i, (data, w, h) in enumerate(tiles):
                    upsert_asset(conn, fk, "frame", i, data, w, h, src, fk in shared)
                    stats["frames"] += 1; stats["bytes_frames"] += len(data)
            # fallback thumbnail for video families whose creative has no thumbnail
            if tiles and not (rec.get("thumb") or dv.get("thumb")) and only is None:
                data, w, h = make_fallback()
                for fk in fks:
                    upsert_asset(conn, fk, "thumb", 0, data, w, h, src + "-frame", fk in shared)
                    stats["fallback_thumbs"] += 1
    if only in (None, "content"):
        meta_copy = {k.lower(): v for k, v in json.loads((cd / "meta_copy.json").read_text()).items()}
        whisper = load_transcripts(cd)
        drive_t = {k.lower(): v.get("text") for k, v in (json.loads((cd / "video_transcripts_drive.json").read_text()).items()
                                                         if (cd / "video_transcripts_drive.json").exists() else [])}
        tracker_tr, tracker_vid = {}, set()
        for fk, tr, ty in conn.execute("SELECT family_key, transcript, ad_type FROM tracker_rows ORDER BY sheet_row, id").fetchall():
            if tr and tr.strip():
                tracker_tr.setdefault(fk, tr)
            if (ty or "").upper() == "VID":
                tracker_vid.add(fk)
        for k, fks in resolved.items():
            m = mi.get(k) or {}
            c = meta_copy.get(k) or {}
            dv, vv = di.get(k) or {}, vi.get(k) or {}
            for fk in fks:
                tr, tsrc = None, None
                if bl_shape and vv.get("transcript"):                 # the transcript the tagger actually read
                    tr, tsrc = vv["transcript"], vv.get("transcript_source") or "whisper"
                elif fk in tracker_tr:
                    tr, tsrc = tracker_tr[fk], "tracker"
                elif whisper.get(k):
                    tr, tsrc = whisper[k], "whisper"
                elif drive_t.get(k):
                    tr, tsrc = drive_t[k], "whisper"
                is_vid = _truthy(m.get("video")) or _truthy(c.get("video")) or bool(m.get("video_id"))
                if bl_shape:
                    is_vid = is_vid or fk in tracker_vid or (dv.get("mime") or "").startswith("video/") or bool(vv.get("frames"))
                on_conflict = "DO NOTHING" if fk in shared else """DO UPDATE SET
                                meta_body=COALESCE(EXCLUDED.meta_body, family_content.meta_body), meta_title=COALESCE(EXCLUDED.meta_title, family_content.meta_title),
                                meta_desc=COALESCE(EXCLUDED.meta_desc, family_content.meta_desc), transcript=COALESCE(EXCLUDED.transcript, family_content.transcript),
                                story_url=COALESCE(EXCLUDED.story_url, family_content.story_url), is_video=EXCLUDED.is_video,
                                ad_id=COALESCE(EXCLUDED.ad_id, family_content.ad_id), video_id=COALESCE(EXCLUDED.video_id, family_content.video_id),
                                source=EXCLUDED.source"""
                conn.execute(f"""INSERT INTO family_content (family_key, meta_body, meta_title, meta_desc, transcript, transcript_seconds, story_url, is_video, ad_id, video_id, source)
                                VALUES (%s,%s,%s,%s,%s,NULL,%s,%s,%s,%s,%s)
                                ON CONFLICT (family_key) {on_conflict}""",
                             (fk, c.get("body") or None, c.get("title") or None, c.get("desc") or None, tr or None, story_url(m, c), is_vid,
                              m.get("ad_id") or c.get("ad_id") or None, m.get("video_id") or c.get("video_id") or None, f"{src}:{tsrc}" if tsrc else src))
                stats["content"] += 1
    all_fks = sorted({fk for fks in resolved.values() for fk in fks})
    cov = conn.execute("""SELECT count(DISTINCT family_key), count(DISTINCT family_key) FILTER (WHERE kind='thumb'),
                                 count(DISTINCT family_key) FILTER (WHERE kind='frame') FROM creative_assets WHERE family_key = ANY(%s)""", (all_fks,)).fetchone()
    stats["families_with_any_asset"], stats["families_with_thumb"], stats["families_with_frames"] = cov
    stats["no_image_families"] = len(all_fks) - cov[0]
    stats["tracker_families_in_scope"] = len(fkeys)
    stats["tracker_families_without_asset"] = len(fkeys) - conn.execute(
        "SELECT count(DISTINCT family_key) FROM creative_assets WHERE family_key = ANY(%s)", (sorted(fkeys),)).fetchone()[0]
    stats["unmatched_families"] = stats["unmatched_families"][:10]
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cycle-dir", default=str(DEFAULT_CYCLE))
    ap.add_argument("--product", help="only this product's families (e.g. beef-liver); default: every family the cycle's index matches")
    ap.add_argument("--only", choices=["thumbs", "frames", "content"])
    a = ap.parse_args()
    with db.JobRun("load_assets") as run, db.connect() as conn:
        stats = load(conn, Path(a.cycle_dir), a.only, a.product)
        conn.commit()
        run.stats = stats
        run.rows_written = stats["thumbs"] + stats["frames"] + stats["fallback_thumbs"] + stats["content"]
        db.log(f"  {stats}")


if __name__ == "__main__":
    main()
