-- 264: landing pages of the watched competitors' ads, opened weekly and tagged (key theme, selling points, offer, proof). Filled by etl.competitor_landing_pages.
--   competitor_landing_pages  one row per (week, distinct LP): what the page says (tags) + fetch status + week-over-week change flag.
--   competitor_lp_media       one above-the-fold screenshot per (week, LP), small JPEG; served by GET /api/competitors/lp-media/<lp_id>.
--   competitor_ad_lp          links each ad of a week's competitor_ads to its LP.
-- lp_id = first 16 hex chars of sha1(url_key); url_key = lowercase host without www + path without trailing slash, tracking params dropped (see docs/COMPETITOR_ADS.md).
CREATE TABLE IF NOT EXISTS competitor_landing_pages (
  week_start         date        NOT NULL,
  lp_id              text        NOT NULL,
  url_key            text        NOT NULL,
  final_url          text,
  brand              text,
  http_status        integer,
  fetched_at         timestamptz,
  ad_count           integer     NOT NULL DEFAULT 0,
  page_type          text,
  key_theme          text,
  hero_headline      text,
  selling_points     text[],
  offer_summary      text,
  price_text         text,
  guarantee          text,
  proof_elements     text[],
  urgency_devices    text[],
  objections         text[],
  cta                text,
  cluster            text,
  icp                text,
  pain_point         text,
  why_it_works       text,
  method             text        CHECK (method IN ('vision_text', 'text')),
  confidence         text        CHECK (confidence IN ('high', 'medium', 'low')),
  content_hash       text,
  changed_from_prior boolean     NOT NULL DEFAULT false,
  change_note        text,
  fetch_error        text,
  model              text,
  tagged_at          timestamptz,
  PRIMARY KEY (week_start, lp_id)
);
CREATE INDEX IF NOT EXISTS competitor_landing_pages_lp ON competitor_landing_pages (lp_id, week_start);
CREATE INDEX IF NOT EXISTS competitor_landing_pages_brand ON competitor_landing_pages (week_start, brand);

CREATE TABLE IF NOT EXISTS competitor_lp_media (
  week_start    date        NOT NULL,
  lp_id         text        NOT NULL,
  content_type  text        NOT NULL,
  width         integer,
  height        integer,
  bytes         bytea       NOT NULL,
  fetched_at    timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (week_start, lp_id)
);
CREATE INDEX IF NOT EXISTS competitor_lp_media_lp ON competitor_lp_media (lp_id, week_start);

CREATE TABLE IF NOT EXISTS competitor_ad_lp (
  week_start  date NOT NULL,
  library_id  text NOT NULL,
  lp_id       text NOT NULL,
  PRIMARY KEY (week_start, library_id)
);
CREATE INDEX IF NOT EXISTS competitor_ad_lp_lp ON competitor_ad_lp (week_start, lp_id);

-- comments (data dictionary): copied from docs/schema/descriptions.py via scripts/gen_schema_doc.py --comments all (competitor_landing_pages, competitor_lp_media, competitor_ad_lp lines only)
COMMENT ON TABLE public.competitor_landing_pages IS 'The landing pages the watched competitors'' Meta ads point to, opened once a week and tagged: page type, the page''s key theme, hero headline, selling points, offer, price, guarantee, proof, urgency devices, objections handled, CTA, and Lonvera''s cluster / ICP / pain point, so we can learn from the pages and not only the ads. Grain: one row per (week_start, lp_id): one distinct landing page per week, however many ads point to it (week_start = Monday, Pacific, the competitor_ads week). Source: etl.competitor_landing_pages: distinct competitor_ads.landing_url of the week, normalised to url_key (host without www + path, tracking parameters dropped), opened with headless Chromium (Playwright; 1280x900 viewport, 3 s between pages, 20 s timeout, no login, no captcha or bot-wall bypass), visible text extracted, tagged by headless Claude (Sonnet 5.5) from the text and the screenshot in batches of 5. Refresh: by hand for now (not scheduled in the nightly etl yet); python -m etl.competitor_landing_pages [--week --brand --limit --retag --no-tag --dry-run]; a page already tagged this week is skipped. Coverage: from the first run after migration 264 (2026-11-13 build); at most 200 distinct pages per run, the most-used ones first; one row per week kept as history. Caveats: Not every ad has a page row: ads without a landing_url, on social hosts (facebook, instagram, youtube), or past the 200-page cap are unlinked in competitor_ad_lp. | http_status / fetch_error show a blocked page (403, challenge, blank): its tag columns stay NULL. Blocked is not a missing page; the Ad Library may still show the ad. | landing_url in competitor_ads is often only the display domain and path (the Ad Library card shows no query string), so the page opened is the display URL, which can differ from the exact page a click lands on (tracking redirects are followed; final_url is where it ended). | content_hash covers the extracted key text (title, headings, bullets, prices, guarantee, reviews, FAQ, CTA) with clocks and counters removed; changed_from_prior compares it with the latest earlier week of the same lp_id (false when there is none) and a changed hash can still be an A/B variant served on the visit. change_note is a model summary written only for changed pages. | Tags are the model''s reading of one visit, quoted not invented; unknown fields are NULL. method ''vision_text'' = the screenshot and the text, ''text'' = text only (no screenshot).';
COMMENT ON COLUMN public.competitor_landing_pages.week_start IS 'Monday (Pacific) of the competitor_ads week (primary key with lp_id).';
COMMENT ON COLUMN public.competitor_landing_pages.lp_id IS 'First 16 hex characters of sha1(url_key) (primary key with week_start); the same page across ads and weeks keeps one lp_id.';
COMMENT ON COLUMN public.competitor_landing_pages.url_key IS 'Normalised page key: lowercase host without www + path without trailing slash; tracking parameters dropped, only page-selecting parameters (variant, v, offer ...) kept.';
COMMENT ON COLUMN public.competitor_landing_pages.final_url IS 'Where the page ended after redirects, tracking parameters removed; NULL when it never loaded.';
COMMENT ON COLUMN public.competitor_landing_pages.brand IS 'competitor_brands.brand with the most ads pointing to this page.';
COMMENT ON COLUMN public.competitor_landing_pages.http_status IS 'HTTP status of the main document; NULL when it never answered (timeout, DNS).';
COMMENT ON COLUMN public.competitor_landing_pages.fetched_at IS 'When the page was opened.';
COMMENT ON COLUMN public.competitor_landing_pages.ad_count IS 'How many of the week''s competitor_ads rows point to this page.';
COMMENT ON COLUMN public.competitor_landing_pages.page_type IS 'product page | advertorial or article | listicle or ranked list | quiz or funnel | video sales page | homepage or collection | other.';
COMMENT ON COLUMN public.competitor_landing_pages.key_theme IS 'One plain sentence (<= 20 words): the page''s central promise or story.';
COMMENT ON COLUMN public.competitor_landing_pages.hero_headline IS 'The page''s main headline, quoted exactly.';
COMMENT ON COLUMN public.competitor_landing_pages.selling_points IS '3 to 6 short phrases in the page''s own terms (ingredients, doses, sourcing, benefits, comparisons).';
COMMENT ON COLUMN public.competitor_landing_pages.offer_summary IS 'Price, bundle, discount, free shipping, subscription as offered.';
COMMENT ON COLUMN public.competitor_landing_pages.price_text IS 'Price as shown on the page.';
COMMENT ON COLUMN public.competitor_landing_pages.guarantee IS 'Guarantee as shown (e.g. ''60 day money back'').';
COMMENT ON COLUMN public.competitor_landing_pages.proof_elements IS 'From: reviews count or stars, doctor or expert, clinical study, press logos, before and after, UGC or testimonials, certifications, founder story, other.';
COMMENT ON COLUMN public.competitor_landing_pages.urgency_devices IS 'countdown, low stock, limited offer, free gift deadline, or none.';
COMMENT ON COLUMN public.competitor_landing_pages.objections IS 'FAQ topics or objection handling the page addresses.';
COMMENT ON COLUMN public.competitor_landing_pages.cta IS 'Main call-to-action button text.';
COMMENT ON COLUMN public.competitor_landing_pages.cluster IS 'Lonvera market cluster of the page''s angle (from the taxonomy; a model suggestion for a new angle).';
COMMENT ON COLUMN public.competitor_landing_pages.icp IS 'Lonvera ICP the page speaks to.';
COMMENT ON COLUMN public.competitor_landing_pages.pain_point IS 'Lonvera pain point the page speaks to.';
COMMENT ON COLUMN public.competitor_landing_pages.why_it_works IS 'One short sentence on what the page does well for its buyer.';
COMMENT ON COLUMN public.competitor_landing_pages.method IS '''vision_text'' (screenshot + text) or ''text''.';
COMMENT ON COLUMN public.competitor_landing_pages.confidence IS 'high | medium | low: how clearly the page states its buyer and promise.';
COMMENT ON COLUMN public.competitor_landing_pages.content_hash IS 'sha1 of the normalised key text of the page (clocks and counters removed).';
COMMENT ON COLUMN public.competitor_landing_pages.changed_from_prior IS 'True when content_hash differs from the latest earlier week of the same lp_id.';
COMMENT ON COLUMN public.competitor_landing_pages.change_note IS 'One plain line on what changed, only for changed pages with a tagged earlier week.';
COMMENT ON COLUMN public.competitor_landing_pages.fetch_error IS 'Why the page could not be read (blocked, challenge, blank, timeout, DNS); NULL when read.';
COMMENT ON COLUMN public.competitor_landing_pages.model IS 'Model that tagged the page.';
COMMENT ON COLUMN public.competitor_landing_pages.tagged_at IS 'When the tags were written; NULL = captured but not tagged.';
COMMENT ON TABLE public.competitor_lp_media IS 'One above-the-fold screenshot of each competitor landing page per week, so the /competitors page can show what the page looks like. Grain: one row per (week_start, lp_id) (joins to competitor_landing_pages). Source: etl.competitor_landing_pages: viewport screenshot (1280x900) taken after the page loaded, re-encoded to JPEG, at most 640 px wide and 90 KB. Refresh: weekly with the landing page run; rows older than 60 days that are not in the latest week are deleted. Coverage: from the first run after migration 264; only pages that loaded. Caveats: No row = no screenshot (blocked, blank or failed page), not a missing page. | Served by GET /api/competitors/lp-media/<lp_id>?week=YYYY-MM-DD (login required); do not read bytes through SQL for display. | A cookie banner or popup can cover part of the shot; only an obvious Accept / Close button is clicked.';
COMMENT ON COLUMN public.competitor_lp_media.week_start IS 'Monday (Pacific) of the week (primary key with lp_id).';
COMMENT ON COLUMN public.competitor_lp_media.lp_id IS 'Landing page id (primary key with week_start).';
COMMENT ON COLUMN public.competitor_lp_media.content_type IS 'MIME type of bytes (image/jpeg).';
COMMENT ON COLUMN public.competitor_lp_media.width IS 'Pixel width after re-encoding.';
COMMENT ON COLUMN public.competitor_lp_media.height IS 'Pixel height after re-encoding.';
COMMENT ON COLUMN public.competitor_lp_media.bytes IS 'The JPEG bytes.';
COMMENT ON COLUMN public.competitor_lp_media.fetched_at IS 'When the screenshot was stored.';
COMMENT ON TABLE public.competitor_ad_lp IS 'Links each competitor ad of a week to the landing page it points to, so an ad can show its page and a page can list its ads. Grain: one row per (week_start, library_id) (joins to competitor_ads and to competitor_landing_pages on lp_id). Source: etl.competitor_landing_pages: competitor_ads.landing_url of the week normalised to lp_id. Refresh: weekly with the landing page run (rewritten for the week on every run). Coverage: from the first run after migration 264. Caveats: Only ads with a usable landing_url on a non-social host and within the 200-page cap are linked; a missing row means unlinked, not ''no landing page''. | Several ads usually share one page, so count ads with competitor_landing_pages.ad_count, not rows here joined to the pages table.';
COMMENT ON COLUMN public.competitor_ad_lp.week_start IS 'Monday (Pacific) of the competitor_ads week (primary key with library_id).';
COMMENT ON COLUMN public.competitor_ad_lp.library_id IS 'Meta Ad Library ID (primary key with week_start).';
COMMENT ON COLUMN public.competitor_ad_lp.lp_id IS 'competitor_landing_pages.lp_id of the ad''s page.';
