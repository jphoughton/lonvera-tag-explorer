"""Best-effort: fetch creative thumbnails from the Meta Graph API for tracker families that have no asset yet.
Family -> candidate ad ids (meta_family_map -> meta_ads, ACTIVE and most recent first) -> /?ids=...&fields=creative{thumbnail_url,image_url,
effective_object_story_id,video_id}. Resumable (families already tried and found empty are skipped unless --retry), rate-limit aware, small batches.
Usage: python -m etl.assets_pull [--limit N] [--product slug] [--batch 10] [--sleep 2] [--retry] [--dry-run]"""
from __future__ import annotations

import argparse
import io
import time

from . import db, meta_api

THUMB_FIELDS = ("id,name,creative.thumbnail_width(640).thumbnail_height(640){id,video_id,thumbnail_url,image_url,effective_object_story_id}")
EMPTY_SOURCE = "assets_noimg:"            # app_meta key prefix: families tried with no usable image (skipped unless --retry)


def candidates(conn, product: str | None, limit: int, retry: bool) -> list[tuple[str, list[str]]]:
    """Tracker families (non-blank) with no thumb asset, highest since-launch spend first, with up to 3 candidate ad ids each."""
    sql = """
    SELECT m.family_key, max(m.spend_launch) AS sp,
           (SELECT array_agg(ad_id) FROM (
              SELECT a.ad_id FROM meta_family_map f JOIN meta_ads a ON a.meta_family_key = f.meta_family_key
              WHERE f.tracker_family_key = m.family_key AND a.ad_id NOT LIKE 'fam:%%'
              ORDER BY (a.status = 'ACTIVE') DESC NULLS LAST, a.last_day DESC NULLS LAST, a.ad_id DESC LIMIT 3) x) AS ad_ids
    FROM ad_metrics m JOIN tracker_rows t ON t.id = m.tracker_row_id JOIN products p ON p.id = t.product_id
    WHERE m.family_key NOT LIKE 'id:%%' AND (%(product)s::text IS NULL OR p.slug = %(product)s)
      AND NOT EXISTS (SELECT 1 FROM creative_assets c WHERE c.family_key = m.family_key AND c.kind = 'thumb')
      AND (%(retry)s OR NOT EXISTS (SELECT 1 FROM app_meta am WHERE am.key = %(empty)s || m.family_key))
    GROUP BY m.family_key ORDER BY sp DESC
    """
    rows = conn.execute(sql, {"product": product, "retry": retry, "empty": EMPTY_SOURCE}).fetchall()
    return [(r[0], r[2]) for r in rows if r[2]][:limit]


def download_thumb(url: str):
    import requests
    from PIL import Image
    for _ in range(3):
        try:
            r = requests.get(url, timeout=40)
            if r.status_code == 200 and len(r.content) > 500:
                with Image.open(io.BytesIO(r.content)) as im:
                    im = im.convert("RGB")
                    if im.width > 640:
                        im = im.resize((640, max(1, round(im.height * 640 / im.width))))
                    buf = io.BytesIO()
                    im.save(buf, "JPEG", quality=82, optimize=True)
                    return buf.getvalue(), im.width, im.height
        except Exception:                                                     # noqa: BLE001
            time.sleep(2)
    return None


def run(conn, limit=50, product=None, batch=10, pace=2.0, retry=False, dry_run=False, tok=None) -> dict:
    tok = tok or meta_api.get_token()
    cands = candidates(conn, product, limit, retry)
    stats = {"candidates": len(cands), "saved": 0, "no_image": 0, "no_ad_found": 0, "stopped_early": None, "bytes": 0}
    all_ids = sorted({i for _, ids in cands for i in ids})
    meta: dict = {}
    for i in range(0, len(all_ids), batch):
        chunk = all_ids[i:i + batch]
        try:
            j = meta_api.graph_get(f"{meta_api.G}/", {"ids": ",".join(chunk), "fields": THUMB_FIELDS, "access_token": tok}, "assets", max_retries=4)
            meta.update({k: v for k, v in j.items() if isinstance(v, dict)})
        except meta_api.GraphError as e:
            stats["stopped_early"] = db.scrub(e)[:160]
            db.log(f"  stopped fetching metadata at {i}/{len(all_ids)}: {stats['stopped_early']}")
            break
        time.sleep(pace)
    for fk, ids in cands:
        if stats["stopped_early"] and not any(i in meta for i in ids):
            continue                                                         # not fetched (rate limit): try again next run
        got = None
        for ad in ids:
            cr = (meta.get(ad) or {}).get("creative") or {}
            url = cr.get("thumbnail_url") or cr.get("image_url")
            if url:
                got = (ad, cr, download_thumb(url))
                if got[2]:
                    break
        if got and got[2]:
            ad, cr, (data, w, h) = got
            stats["saved"] += 1; stats["bytes"] += len(data)
            if not dry_run:
                conn.execute("""INSERT INTO creative_assets (family_key, kind, idx, mime, width, height, bytes, source) VALUES (%s,'thumb',0,'image/jpeg',%s,%s,%s,'meta-api')
                                ON CONFLICT (family_key, kind, idx) DO NOTHING""", (fk, w, h, data))
                story = cr.get("effective_object_story_id") or ""
                story_url = "https://www.facebook.com/%s/posts/%s/" % tuple(story.split("_", 1)) if "_" in story else None
                conn.execute("""INSERT INTO family_content (family_key, story_url, is_video, ad_id, video_id, source) VALUES (%s,%s,%s,%s,%s,'assets_pull')
                                ON CONFLICT (family_key) DO UPDATE SET story_url=coalesce(family_content.story_url, EXCLUDED.story_url),
                                is_video=coalesce(family_content.is_video, EXCLUDED.is_video), ad_id=coalesce(family_content.ad_id, EXCLUDED.ad_id),
                                video_id=coalesce(family_content.video_id, EXCLUDED.video_id)""",
                             (fk, story_url, bool(cr.get("video_id")), ad, cr.get("video_id") or None))
        else:
            stats["no_image"] += 1
            if not dry_run:
                db.set_meta(conn, EMPTY_SOURCE + fk, "tried")
    if not dry_run:
        conn.commit()
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--product")
    ap.add_argument("--batch", type=int, default=10)
    ap.add_argument("--sleep", type=float, default=2.0)
    ap.add_argument("--retry", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    with db.JobRun("assets_pull") as run_, db.connect() as conn:
        run_.stats = run(conn, a.limit, a.product, a.batch, a.sleep, a.retry, a.dry_run)
        run_.rows_written = run_.stats["saved"]
        db.log(f"  {run_.stats}")


if __name__ == "__main__":
    main()
