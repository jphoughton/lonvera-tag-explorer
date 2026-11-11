-- 207: landing page copy store and ad to landing page map. etl/landing_pages.py finds the page each Meta ad sends people to (the ad's destination link), reads every such page with a plain public
-- web request, stores the page copy as versions (a new version only when the text changes), and keeps one row per ad and page. View landing_page_top_ads ranks the ads that drive each page by
-- spend over the last 7 and 30 days, using public.ad_daily (Meta-reported purchases and revenue). Additive and idempotent. Not yet scheduled: see docs/LANDING_PAGES.md.
CREATE TABLE IF NOT EXISTS landing_pages (
  id               bigserial   PRIMARY KEY,
  url              text        NOT NULL UNIQUE,
  host             text        NOT NULL,
  path             text        NOT NULL,
  first_seen_at    timestamptz NOT NULL DEFAULT now(),
  last_seen_at     timestamptz NOT NULL DEFAULT now(),
  last_fetched_at  timestamptz,
  last_status      int,
  last_error       text,
  current_version_id bigint,
  active           boolean     NOT NULL DEFAULT true
);
CREATE INDEX IF NOT EXISTS landing_pages_host ON landing_pages (host);

CREATE TABLE IF NOT EXISTS landing_page_versions (
  id               bigserial   PRIMARY KEY,
  landing_page_id  bigint      NOT NULL REFERENCES landing_pages(id) ON DELETE CASCADE,
  content_hash     text        NOT NULL,
  fetched_at       timestamptz NOT NULL DEFAULT now(),
  http_status      int,
  final_url        text,
  title            text,
  meta_description text,
  rendered_in_browser boolean  NOT NULL DEFAULT false,
  word_count       int         NOT NULL DEFAULT 0,
  blocks           jsonb       NOT NULL DEFAULT '[]'::jsonb,
  ctas             jsonb       NOT NULL DEFAULT '[]'::jsonb,
  prices           jsonb       NOT NULL DEFAULT '[]'::jsonb,
  guarantees       jsonb       NOT NULL DEFAULT '[]'::jsonb,
  reviews          jsonb       NOT NULL DEFAULT '[]'::jsonb,
  images           jsonb       NOT NULL DEFAULT '[]'::jsonb,
  full_text        text        NOT NULL DEFAULT '',
  UNIQUE (landing_page_id, content_hash)
);
CREATE INDEX IF NOT EXISTS landing_page_versions_page ON landing_page_versions (landing_page_id, fetched_at DESC);

CREATE TABLE IF NOT EXISTS landing_page_ads (
  ad_id            text        NOT NULL,
  landing_page_id  bigint      NOT NULL REFERENCES landing_pages(id) ON DELETE CASCADE,
  raw_url          text        NOT NULL,
  source           text        NOT NULL,
  first_seen_date  date        NOT NULL DEFAULT current_date,
  last_seen_date   date        NOT NULL DEFAULT current_date,
  PRIMARY KEY (ad_id, landing_page_id)
);
CREATE INDEX IF NOT EXISTS landing_page_ads_page ON landing_page_ads (landing_page_id);

-- Ads already looked at, so an ad with no readable link is not asked about again every night. result: 'linked' | 'no_link' | 'error'.
CREATE TABLE IF NOT EXISTS landing_page_ad_checks (
  ad_id       text        PRIMARY KEY,
  checked_at  timestamptz NOT NULL DEFAULT now(),
  result      text        NOT NULL,
  detail      text
);

-- Per landing page, per window (7 or 30 days back from today in Pacific time, matching the Pacific dates stored in ad_daily), per ad: spend, Meta-reported purchases and revenue, ROAS, and the ad's rank by spend inside the page.
-- An ad whose creative lists more than one link appears under each page with its FULL spend, so do not add spend across pages.
CREATE OR REPLACE VIEW landing_page_top_ads AS
SELECT lp.id AS landing_page_id, lp.url, w.window_days, a.ad_id, ma.ad_name, ma.campaign_id,
       s.spend, s.purchases, s.conv_value AS revenue,
       CASE WHEN s.spend > 0 THEN round(s.conv_value / s.spend, 3) END AS roas,
       row_number() OVER (PARTITION BY lp.id, w.window_days ORDER BY s.spend DESC, a.ad_id) AS rank_by_spend
FROM landing_pages lp
JOIN landing_page_ads a ON a.landing_page_id = lp.id
CROSS JOIN (VALUES (7), (30)) AS w(window_days)
JOIN LATERAL (SELECT sum(d.spend) AS spend, sum(d.purchases) AS purchases, sum(d.conv_value) AS conv_value
              FROM ad_daily d WHERE d.ad_id = a.ad_id AND d.day > (now() AT TIME ZONE 'America/Los_Angeles')::date - w.window_days) s ON s.spend > 0
LEFT JOIN meta_ads ma ON ma.ad_id = a.ad_id;

-- ---------------------------------------------------------------- comments
COMMENT ON TABLE public.landing_pages IS 'Every landing page (sales page, advertorial, product page, funnel step) that at least one Meta ad sends people to, with when we last read it. Grain: one row per normalised page address (id is the primary key; url is unique). Source: etl/landing_pages.py: the destination links found on Meta ad creatives. Refresh: each run of python -m etl.landing_pages (not scheduled yet; see docs/LANDING_PAGES.md). Coverage: pages of ads that had spend in the window of the first run onward. Caveats: The address is normalised: https, lower-case host, tracking parameters and the part after # removed, no trailing slash. Parameters that change which page loads are kept. | current_version_id points at the newest landing_page_versions row whose copy matches the last read (the copy can go back to an older version if the page is changed back).';
COMMENT ON COLUMN public.landing_pages.id IS 'Primary key (bigserial).';
COMMENT ON COLUMN public.landing_pages.url IS 'Normalised page address (unique).';
COMMENT ON COLUMN public.landing_pages.host IS 'Host name of the page, lower case.';
COMMENT ON COLUMN public.landing_pages.path IS 'Path and any kept query string of the page.';
COMMENT ON COLUMN public.landing_pages.first_seen_at IS 'When an ad was first found pointing at this page.';
COMMENT ON COLUMN public.landing_pages.last_seen_at IS 'When an ad was last found pointing at this page.';
COMMENT ON COLUMN public.landing_pages.last_fetched_at IS 'When the page was last read (successfully or not); NULL until the first read.';
COMMENT ON COLUMN public.landing_pages.last_status IS 'HTTP status of the last successful read; NULL before the first.';
COMMENT ON COLUMN public.landing_pages.last_error IS 'Short scrubbed error text when the last read failed; NULL after a good read.';
COMMENT ON COLUMN public.landing_pages.current_version_id IS 'landing_page_versions.id of the copy seen on the last good read.';
COMMENT ON COLUMN public.landing_pages.active IS 'False stops the page being read again; set by hand, nothing sets it.';
COMMENT ON TABLE public.landing_page_versions IS 'The copy of a landing page as it was on a given read: title, description, ordered text blocks with heading level, buttons, prices, guarantee sentences, review snippets and image alt text. Grain: one row per (landing page, content hash): a new row exists only when the copy changed (id is the primary key). Source: etl/landing_pages.py: a plain public web request per page, parsed with the standard library (headless Chromium for pages built in the browser). Refresh: each run of python -m etl.landing_pages; a row is added only when the text hash changes. Coverage: from the first read of each page. Caveats: content_hash covers the text only (title, description, text blocks, button text, image alt text), not image addresses or scripts, so changing a picture without changing its alt text is not a new version. | Pages built in the browser have little text in the server HTML; those are read again with Chromium and rendered_in_browser is true. Text shown only after a click or inside an iframe is not captured. | fetched_at is when this exact copy was first seen, not when it was last confirmed (landing_pages.last_fetched_at has that).';
COMMENT ON COLUMN public.landing_page_versions.id IS 'Primary key (bigserial).';
COMMENT ON COLUMN public.landing_page_versions.landing_page_id IS 'Page this copy belongs to (landing_pages.id, foreign key).';
COMMENT ON COLUMN public.landing_page_versions.content_hash IS 'sha256 of the page text (see caveats); unique per page.';
COMMENT ON COLUMN public.landing_page_versions.fetched_at IS 'When this copy was first seen.';
COMMENT ON COLUMN public.landing_page_versions.http_status IS 'HTTP status of the read that produced this copy.';
COMMENT ON COLUMN public.landing_page_versions.final_url IS 'Address after redirects.';
COMMENT ON COLUMN public.landing_page_versions.title IS 'Page title (the title tag, else the Open Graph title).';
COMMENT ON COLUMN public.landing_page_versions.meta_description IS 'Meta description (else the Open Graph description).';
COMMENT ON COLUMN public.landing_page_versions.rendered_in_browser IS 'True when the copy came from headless Chromium because the server HTML held almost no text.';
COMMENT ON COLUMN public.landing_page_versions.word_count IS 'Words in the page text.';
COMMENT ON COLUMN public.landing_page_versions.blocks IS 'Ordered text blocks as JSON: [{i, tag, level, text}], level is the heading level 1 to 6 or null, tag is the nearest label tag (h1..h6, p, li, blockquote, button, a, text).';
COMMENT ON COLUMN public.landing_page_versions.ctas IS 'Button and call to action text as JSON: [{text, href, tag}].';
COMMENT ON COLUMN public.landing_page_versions.prices IS 'Dollar amounts found in the text as JSON: [{price, context}].';
COMMENT ON COLUMN public.landing_page_versions.guarantees IS 'Sentences that mention a guarantee, refund, money back or risk free, as a JSON array of strings.';
COMMENT ON COLUMN public.landing_page_versions.reviews IS 'Review or testimonial snippets (quote blocks, star lines, structured data), as a JSON array of strings.';
COMMENT ON COLUMN public.landing_page_versions.images IS 'Image alt text as JSON: [{alt, src}].';
COMMENT ON COLUMN public.landing_page_versions.full_text IS 'All text blocks joined by new lines, for searching.';
COMMENT ON TABLE public.landing_page_ads IS 'Which Meta ad sends people to which landing page, and how that link was found. Grain: one row per (ad, landing page) (ad_id + landing_page_id is the primary key). Source: etl/landing_pages.py: the Meta ad creative, the Page post an ad reuses, or the ad preview. Refresh: each run of python -m etl.landing_pages (new ads with spend only; --refresh-ads looks at all again). Coverage: ads with spend in the 30 days before each run, from the first run onward. Caveats: An ad whose creative lists several links has one row per page. Its spend counts in full under each page in landing_page_top_ads, so spend must not be added across pages. | ad_id is not a foreign key to meta_ads because ads can be deleted in Meta.';
COMMENT ON COLUMN public.landing_page_ads.ad_id IS 'Meta ad id.';
COMMENT ON COLUMN public.landing_page_ads.landing_page_id IS 'landing_pages.id (foreign key).';
COMMENT ON COLUMN public.landing_page_ads.raw_url IS 'The link exactly as Meta returned it (with tracking parameters).';
COMMENT ON COLUMN public.landing_page_ads.source IS 'How the link was found: ''creative'' (the ad''s own creative), ''post'' (the Page post the ad reuses), or ''preview'' (the ad preview).';
COMMENT ON COLUMN public.landing_page_ads.first_seen_date IS 'Date the ad was first found with this link.';
COMMENT ON COLUMN public.landing_page_ads.last_seen_date IS 'Date the link was last confirmed.';
COMMENT ON TABLE public.landing_page_ad_checks IS 'Ads that etl/landing_pages.py has already looked at, so an ad with no readable link is not asked about every night. Grain: one row per ad (ad_id is the primary key). Source: etl/landing_pages.py. Refresh: each run of python -m etl.landing_pages. Coverage: ads with spend in the window of the runs. Caveats: An ad with result no_link or error is looked at again 14 days after checked_at.';
COMMENT ON COLUMN public.landing_page_ad_checks.ad_id IS 'Meta ad id (primary key).';
COMMENT ON COLUMN public.landing_page_ad_checks.checked_at IS 'When the ad was last looked at.';
COMMENT ON COLUMN public.landing_page_ad_checks.result IS '''linked'' (a landing page was found), ''no_link'' (none could be read) or ''error'' (Meta returned an error).';
COMMENT ON COLUMN public.landing_page_ad_checks.detail IS 'Short reason for no_link or error; NULL when linked.';
COMMENT ON VIEW public.landing_page_top_ads IS 'The ads that drive each landing page, ranked by spend over the last 7 and the last 30 days, with Meta-reported purchases, revenue and ROAS (revenue divided by spend). Grain: one row per (landing page, window_days, ad): window_days is 7 or 30; only ads with spend in the window appear. Source: public.landing_pages, public.landing_page_ads, public.ad_daily (Meta-reported) and public.meta_ads (name). Refresh: live view; the ad to page map moves with each run of python -m etl.landing_pages, spend with the nightly Meta pull. Coverage: ads mapped to a page by the landing page job. Caveats: Purchases and revenue are what Meta reports (all customers), not Upstack new-customer numbers; join analytics.meta_ad_daily on ad_id when you need those. | An ad that lists several links appears under each page with its full spend: do not add spend across pages. | The window is the last N Pacific calendar days including today, which is a partial day.';
COMMENT ON COLUMN public.landing_page_top_ads.landing_page_id IS 'landing_pages.id.';
COMMENT ON COLUMN public.landing_page_top_ads.url IS 'Normalised page address.';
COMMENT ON COLUMN public.landing_page_top_ads.window_days IS '7 or 30.';
COMMENT ON COLUMN public.landing_page_top_ads.ad_id IS 'Meta ad id.';
COMMENT ON COLUMN public.landing_page_top_ads.ad_name IS 'Ad name from meta_ads; NULL when the ad is not there.';
COMMENT ON COLUMN public.landing_page_top_ads.campaign_id IS 'Campaign id from meta_ads; NULL when the ad is not there.';
COMMENT ON COLUMN public.landing_page_top_ads.spend IS 'Spend in the window, dollars (Meta).';
COMMENT ON COLUMN public.landing_page_top_ads.purchases IS 'Meta-reported purchases in the window.';
COMMENT ON COLUMN public.landing_page_top_ads.revenue IS 'Meta-reported purchase revenue in the window, dollars.';
COMMENT ON COLUMN public.landing_page_top_ads.roas IS 'revenue divided by spend, 3 decimals.';
COMMENT ON COLUMN public.landing_page_top_ads.rank_by_spend IS '1 = biggest spender for this page and window.';
