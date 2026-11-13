-- 246: creative thumbnails of watched competitor ads for the /competitors page. Filled by etl.competitor_ads (scrape-time capture, or --media-backfill for ads already stored); served by GET /api/competitors/media/<library_id>.
--   competitor_ad_media  one row per Meta Ad Library ID: the creative image (or the poster frame of a video) as a downscaled JPEG, <= 480 px wide, <= 150 KB.
-- Ad Library image links are signed and expire within days, so the bytes are stored, never the URL. Only a curated slice per brand is kept (see docs/COMPETITOR_ADS.md).
CREATE TABLE IF NOT EXISTS competitor_ad_media (
  library_id    text        PRIMARY KEY,
  kind          text        NOT NULL CHECK (kind IN ('image', 'video_poster')),
  content_type  text        NOT NULL,
  width         integer,
  height        integer,
  bytes         bytea       NOT NULL,
  fetched_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS competitor_ad_media_fetched ON competitor_ad_media (fetched_at);

-- comments (data dictionary): copied from docs/schema/descriptions.py via scripts/gen_schema_doc.py --comments all (competitor_ad_media lines only)
COMMENT ON TABLE public.competitor_ad_media IS 'Creative thumbnails of the watched competitors'' Meta ads (the image, or the poster frame of a video) so the /competitors page can show what the ads look like. Bytes are stored because the Ad Library image links are signed and expire within days. Grain: one row per Meta Ad Library ID (joins to competitor_ads.library_id). Source: etl.competitor_ads: downloaded from the public Ad Library at scrape time for a curated slice per brand (top 12 longest-running distinct concepts + up to 8 new this week, at most 400 per run), or by --media-backfill for ads already stored; re-encoded to JPEG, at most 480 px wide and 150 KB. Refresh: weekly with etl.competitor_ads (Sundays Pacific); python -m etl.competitor_ads --media-backfill fills ads already stored; rows older than 60 days that are not in the latest week are deleted. Coverage: from 2026-11-12; only the curated slice per brand, not every ad in competitor_ads. Caveats: Not every competitor_ads row has media: only the curated slice, and only when the card showed an image and the download worked. No row = no thumbnail, not a missing ad. | Served by GET /api/competitors/media/<library_id> (login required); do not read bytes through SQL for display. | Carousels store the first image only; videos store the poster frame, never the video.';
COMMENT ON COLUMN public.competitor_ad_media.library_id IS 'Meta Ad Library ID (primary key).';
COMMENT ON COLUMN public.competitor_ad_media.kind IS '''image'' or ''video_poster''.';
COMMENT ON COLUMN public.competitor_ad_media.content_type IS 'MIME type of bytes (image/jpeg).';
COMMENT ON COLUMN public.competitor_ad_media.width IS 'Pixel width after re-encoding.';
COMMENT ON COLUMN public.competitor_ad_media.height IS 'Pixel height after re-encoding.';
COMMENT ON COLUMN public.competitor_ad_media.bytes IS 'The JPEG bytes.';
COMMENT ON COLUMN public.competitor_ad_media.fetched_at IS 'When the image was downloaded.';
