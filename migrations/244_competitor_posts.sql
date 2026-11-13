-- 244: competitor / category chatter (non-Meta signal): public posts and news that mention a watched competitor brand or a category term. Filled by `python -m etl.competitor_chatter` (weekly-ish, additive).
--   competitor_posts  one row per (source, url): Reddit posts via public subreddit RSS, news via Google News RSS. No model calls, no login, no paid scrapers.
-- Read by the /competitors page; the table is idempotent (upsert on (source, url)); rows older than the 14-day window are kept as history.
CREATE TABLE IF NOT EXISTS competitor_posts (
  week_start    date        NOT NULL,
  source        text        NOT NULL,
  brand         text,
  title         text        NOT NULL,
  url           text        NOT NULL,
  subreddit     text,
  score         integer     NOT NULL DEFAULT 0,
  num_comments  integer     NOT NULL DEFAULT 0,
  posted_at     timestamptz,
  summary       text,
  seen_on       date        NOT NULL,
  PRIMARY KEY (source, url)
);
CREATE INDEX IF NOT EXISTS competitor_posts_week_brand ON competitor_posts (week_start, brand);
CREATE INDEX IF NOT EXISTS competitor_posts_posted ON competitor_posts (posted_at DESC);

-- comments (data dictionary): copied from docs/schema/descriptions.py via scripts/gen_schema_doc.py --comments all (competitor_posts lines only)
COMMENT ON TABLE public.competitor_posts IS 'Non-Meta chatter about watched competitors and the category: Reddit posts (public subreddit RSS) and news headlines (Google News RSS) that mention a competitor_brands brand or a category term (beef organ supplement, bone marrow supplement ...). Read by the /competitors page next to the Meta ad snapshots. Grain: one row per (source, url). Source: etl.competitor_chatter: Google News RSS (one query per active brand plus category terms) and public subreddit RSS (/r/<sub>/new/.rss), filtered to brand / category mentions, spam dropped, last 14 days per run. Refresh: by hand / when scheduled: python -m etl.competitor_chatter [--days 14]; idempotent upsert, older rows are kept as history. Coverage: from 2026-11-12; only what the public feeds still show (Reddit RSS lists the newest 100 posts per subreddit). Caveats: Reddit search is blocked for logged-out clients, so Reddit rows come only from the newest posts of a short subreddit list that mention a brand or category term; a quiet week or a Reddit block (it blocks datacenter IPs and allows about 1 request a minute) shows few or no rows. | score and num_comments are 0 for every row: the RSS feeds carry neither. Do not read 0 as no engagement. | google_news summary holds the publisher name; the headline link is a Google News redirect. Brand is the brand searched or mentioned in the headline; NULL = category-level hit.';
COMMENT ON COLUMN public.competitor_posts.week_start IS 'Monday (Pacific) of the week the item was posted.';
COMMENT ON COLUMN public.competitor_posts.source IS '''google_news'' | ''reddit''.';
COMMENT ON COLUMN public.competitor_posts.brand IS 'competitor_brands.brand the item mentions, NULL for a category-term hit.';
COMMENT ON COLUMN public.competitor_posts.title IS 'Post title / headline (publisher suffix removed).';
COMMENT ON COLUMN public.competitor_posts.url IS 'Link (primary key with source); Google News links are redirects.';
COMMENT ON COLUMN public.competitor_posts.subreddit IS 'Subreddit name for reddit rows, else NULL.';
COMMENT ON COLUMN public.competitor_posts.score IS 'Upvotes: always 0 (not in the RSS feeds).';
COMMENT ON COLUMN public.competitor_posts.num_comments IS 'Comment count: always 0 (not in the RSS feeds).';
COMMENT ON COLUMN public.competitor_posts.posted_at IS 'When the item was posted / published.';
COMMENT ON COLUMN public.competitor_posts.summary IS 'Reddit: first 400 characters of the post text; Google News: the publisher name.';
COMMENT ON COLUMN public.competitor_posts.seen_on IS 'Date (Pacific) of the run that last saw the item.';
