-- 254: playable copies of watched competitor video ads for the /competitors lightbox. Filled by etl.competitor_ads (scrape-time capture, or --video-backfill for ads already stored);
-- served by GET /api/competitors/video/<library_id> (HTTP Range).
--   competitor_ad_video  one row per Meta Ad Library ID: the first 30 seconds of the SD mp4 (H.264 / AAC, moov first), at most 6 MB.
-- Ad Library video links are signed and expire in about 4 days, so the bytes are stored, never the URL. Only the curated video slice is kept (see docs/COMPETITOR_ADS.md).
CREATE TABLE IF NOT EXISTS competitor_ad_video (
  library_id    text        PRIMARY KEY,
  content_type  text        NOT NULL,
  duration_s    numeric,
  width         integer,
  height        integer,
  bytes         bytea       NOT NULL,
  size_bytes    integer     NOT NULL,
  fetched_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS competitor_ad_video_fetched ON competitor_ad_video (fetched_at);

-- comments (data dictionary): copied from docs/schema/descriptions.py via scripts/gen_schema_doc.py --comments all (competitor_ad_video lines only)
COMMENT ON TABLE public.competitor_ad_video IS 'A small playable copy of the watched competitors'' video ads (first 30 seconds, H.264 / AAC mp4) so the /competitors lightbox can play them in the app. Bytes are stored because the Ad Library video links are signed and expire in about 4 days. Grain: one row per Meta Ad Library ID (joins to competitor_ads.library_id and competitor_ad_media.library_id). Source: etl.competitor_ads: the SD mp4 link on the public Ad Library card (scrape time or --video-backfill), downloaded with a plain GET, trimmed to 30 seconds with ffmpeg stream copy (re-encoded only when the source is not H.264 / AAC or is over the cap), at most 6 MB each; selection = the same curated slice as the thumbnails, videos only. Refresh: weekly with etl.competitor_ads (Sundays Pacific); python -m etl.competitor_ads --video-backfill fills ads already stored; rows older than 21 days that are not in the latest week are deleted, and the table is held under --max-video-mb (default 300). Coverage: from 2026-11-13; only the curated video slice, not every video in competitor_ads. Caveats: No row = no saved video (never captured, over the size cap, or the link was gone), not a missing ad. The lightbox then shows the poster and a link to the Ad Library. | Served by GET /api/competitors/video/<library_id> (login required, HTTP Range supported); do not read bytes through SQL for display. | Only the first 30 seconds are kept (duration_s is the stored length, not the ad''s length). Quality is Meta''s SD rendition (360 px wide).';
COMMENT ON COLUMN public.competitor_ad_video.library_id IS 'Meta Ad Library ID (primary key).';
COMMENT ON COLUMN public.competitor_ad_video.content_type IS 'MIME type of bytes (video/mp4).';
COMMENT ON COLUMN public.competitor_ad_video.duration_s IS 'Stored length in seconds (at most 30); NULL when ffmpeg was not available to read it.';
COMMENT ON COLUMN public.competitor_ad_video.width IS 'Pixel width of the stored video, NULL when unknown.';
COMMENT ON COLUMN public.competitor_ad_video.height IS 'Pixel height of the stored video, NULL when unknown.';
COMMENT ON COLUMN public.competitor_ad_video.bytes IS 'The mp4 bytes (moov atom first, so it streams and seeks).';
COMMENT ON COLUMN public.competitor_ad_video.size_bytes IS 'Length of bytes (kept as a column so the size cap never reads the bytes).';
COMMENT ON COLUMN public.competitor_ad_video.fetched_at IS 'When the video was downloaded.';
