-- 203: launching approved creative into Meta from /priorities production. Josh picks a Drive folder or file link for an assignment, the app prepares a plan (ad names, Concept IDs, copy, destination) and, only for a signed-in launch user, creates PAUSED ads.
-- Settings per product (account, campaign, template ad set, budget, bid, page, pixel, landing page, default copy), the Pages the ads can run as, the Concept ID prefix per vendor, one editable draft per assignment, the jobs (prepare / launch) with the plan they were
-- approved against, one row per ad with the Meta object ids it produced, the ad set made for a product x vendor x week, and an audit log. No real settings rows are seeded: the account and campaign ids are entered later in the app. Additive and idempotent.
-- Written by the launch module (app/priorities_launch.py) and the launch worker through their own read-write pool; nothing here is read by the nightly jobs.
CREATE TABLE IF NOT EXISTS creative_launch_settings (
  product             text        PRIMARY KEY,
  account_id          text,
  campaign_id         text,
  template_adset_id   text,
  daily_budget_cents  integer     NOT NULL DEFAULT 10000,
  bid_amount_cents    integer     NOT NULL DEFAULT 6800,
  default_page_id     text,
  pixel_id            text,
  landing_url         text,
  url_tags            text,
  cta_image           text        NOT NULL DEFAULT 'LEARN_MORE',
  cta_video           text        NOT NULL DEFAULT 'SHOP_NOW',
  primary_texts       text[]      NOT NULL DEFAULT '{}',
  headlines           text[]      NOT NULL DEFAULT '{}',
  description         text,
  updated_at          timestamptz NOT NULL DEFAULT now(),
  updated_by          text
);

CREATE TABLE IF NOT EXISTS creative_launch_pages (
  page_id            text    PRIMARY KEY,
  name               text    NOT NULL,
  instagram_user_id  text,
  name_token         text,
  active             boolean NOT NULL DEFAULT true
);

CREATE TABLE IF NOT EXISTS creative_launch_vendor_prefixes (
  vendor  text PRIMARY KEY,
  prefix  text NOT NULL
);
INSERT INTO creative_launch_vendor_prefixes (vendor, prefix) VALUES ('Madre', 'Lonvera'), ('Victoria', 'Victoria'), ('10xROAS', '10x'), ('Internal', 'Internal') ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS creative_launch_drafts (
  assignment_id  bigint      PRIMARY KEY REFERENCES creative_priorities_assignments(id),
  fields         jsonb       NOT NULL DEFAULT '{}',
  updated_at     timestamptz NOT NULL DEFAULT now(),
  updated_by     text
);

CREATE TABLE IF NOT EXISTS creative_launch_jobs (
  id             bigserial   PRIMARY KEY,
  assignment_id  bigint      NOT NULL REFERENCES creative_priorities_assignments(id),
  kind           text        NOT NULL CHECK (kind IN ('prepare', 'launch')),
  state          text        NOT NULL DEFAULT 'queued' CHECK (state IN ('queued', 'running', 'done', 'failed', 'cancelled')),
  plan           jsonb,
  plan_hash      text,
  dry_run        boolean     NOT NULL DEFAULT true,
  requested_by   text,
  error          text,
  created_at     timestamptz NOT NULL DEFAULT now(),
  started_at     timestamptz,
  finished_at    timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS creative_launch_jobs_open_uq ON creative_launch_jobs (assignment_id) WHERE state IN ('queued', 'running');

CREATE TABLE IF NOT EXISTS creative_launch_ads (
  id               bigserial   PRIMARY KEY,
  assignment_id    bigint      NOT NULL REFERENCES creative_priorities_assignments(id),
  job_id           bigint      REFERENCES creative_launch_jobs(id),
  drive_file_id    text        NOT NULL,
  file_name        text        NOT NULL,
  kind             text        NOT NULL CHECK (kind IN ('image', 'video')),
  concept_id       text        NOT NULL,
  ad_name          text        NOT NULL,
  state            text        NOT NULL DEFAULT 'planned' CHECK (state IN ('planned', 'media_uploaded', 'creative_created', 'created', 'failed')),
  meta_image_hash  text,
  meta_video_id    text,
  meta_creative_id text,
  meta_ad_id       text,
  meta_adset_id    text,
  error            text,
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT creative_launch_ads_file_uq UNIQUE (assignment_id, drive_file_id),
  CONSTRAINT creative_launch_ads_concept_uq UNIQUE (concept_id)
);

CREATE TABLE IF NOT EXISTS creative_launch_adsets (
  product        text        NOT NULL,
  vendor         text        NOT NULL,
  for_week       date        NOT NULL,
  meta_adset_id  text        NOT NULL,
  created_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (product, vendor, for_week)
);

CREATE TABLE IF NOT EXISTS creative_launch_audit (
  id             bigserial   PRIMARY KEY,
  at             timestamptz NOT NULL DEFAULT now(),
  actor          text,
  action         text        NOT NULL,
  assignment_id  bigint,
  detail         jsonb
);

-- ---------------------------------------------------------------- comments
COMMENT ON TABLE public.creative_launch_settings IS 'Per-product launch settings for creating paused Meta ads from /priorities production: where the ads go (account, campaign, template ad set), the budget and bid for a new ad set, the default Page, pixel, landing page, URL tags, call to action and the default ad copy. Grain: one row per product (product is the primary key). Source: the launch module (app/priorities_launch.py) and the launch worker (settings screen). Refresh: on demand (edited by a person). Coverage: from the day the migration was deployed. Caveats: No rows are seeded: the account and campaign ids are entered in the app before anything can launch. | Amounts are stored in cents; a launch always creates ads and ad sets PAUSED.';
COMMENT ON COLUMN public.creative_launch_settings.product IS 'Product the settings apply to (primary key), spelled as in creative_priorities_assignments.product.';
COMMENT ON COLUMN public.creative_launch_settings.account_id IS 'Meta ad account id the ads are created in.';
COMMENT ON COLUMN public.creative_launch_settings.campaign_id IS 'Campaign the new ad sets go into.';
COMMENT ON COLUMN public.creative_launch_settings.template_adset_id IS 'Existing ad set that a new ad set is cloned from (targeting, optimization, attribution).';
COMMENT ON COLUMN public.creative_launch_settings.daily_budget_cents IS 'Daily budget of a new ad set, in cents (default 10000 = $100).';
COMMENT ON COLUMN public.creative_launch_settings.bid_amount_cents IS 'Bid amount of a new ad set, in cents (default 6800 = $68).';
COMMENT ON COLUMN public.creative_launch_settings.default_page_id IS 'Facebook Page the ads run as when the draft does not name one.';
COMMENT ON COLUMN public.creative_launch_settings.pixel_id IS 'Meta pixel id for the product.';
COMMENT ON COLUMN public.creative_launch_settings.landing_url IS 'Landing page link the ads point to.';
COMMENT ON COLUMN public.creative_launch_settings.url_tags IS 'URL parameters appended to the link; NULL means the built-in default tags.';
COMMENT ON COLUMN public.creative_launch_settings.cta_image IS 'Call to action for image ads (default LEARN_MORE).';
COMMENT ON COLUMN public.creative_launch_settings.cta_video IS 'Call to action for video ads (default SHOP_NOW).';
COMMENT ON COLUMN public.creative_launch_settings.primary_texts IS 'Default primary texts, used when neither the draft nor the best existing ad supplies any.';
COMMENT ON COLUMN public.creative_launch_settings.headlines IS 'Default headlines, used when neither the draft nor the best existing ad supplies any.';
COMMENT ON COLUMN public.creative_launch_settings.description IS 'Default link description; NULL when none.';
COMMENT ON COLUMN public.creative_launch_settings.updated_at IS 'When the settings were last changed.';
COMMENT ON COLUMN public.creative_launch_settings.updated_by IS 'Signed-in user who last changed them; NULL when none.';
COMMENT ON TABLE public.creative_launch_pages IS 'The Facebook Pages (with their Instagram accounts) an ad can run as when launching from /priorities production. Grain: one row per Page (page_id is the primary key). Source: the launch module (app/priorities_launch.py) and the launch worker (settings screen). Refresh: on demand (edited by a person). Coverage: from the day the migration was deployed. Caveats: name_token is the short text put at the end of the ad name for ads on this Page.';
COMMENT ON COLUMN public.creative_launch_pages.page_id IS 'Facebook Page id (primary key).';
COMMENT ON COLUMN public.creative_launch_pages.name IS 'Page name shown in the launch screen.';
COMMENT ON COLUMN public.creative_launch_pages.instagram_user_id IS 'Instagram account id linked to the Page; NULL when none.';
COMMENT ON COLUMN public.creative_launch_pages.name_token IS 'Short token added to the ad name for this Page (for example lonvera).';
COMMENT ON COLUMN public.creative_launch_pages.active IS 'True while the Page may be chosen; false hides it from the picker.';
COMMENT ON TABLE public.creative_launch_vendor_prefixes IS 'Concept ID prefix per vendor, used when the app mints Concept IDs for launched creative (for example Lonvera-BeefLiver-Static-A12). Grain: one row per vendor (vendor is the primary key). Source: the launch module (app/priorities_launch.py) and the launch worker (settings screen); seeded by migration 203. Refresh: on demand (edited by a person). Coverage: from the day the migration was deployed. Caveats: Seeded with Madre = Lonvera, Victoria = Victoria, 10xROAS = 10x, Internal = Internal; a vendor with no row falls back to its name without punctuation.';
COMMENT ON COLUMN public.creative_launch_vendor_prefixes.vendor IS 'Vendor name (primary key), spelled as in creative_priorities_assignments.vendor.';
COMMENT ON COLUMN public.creative_launch_vendor_prefixes.prefix IS 'Text put at the start of the Concept ID.';
COMMENT ON TABLE public.creative_launch_drafts IS 'The editable launch form for one assignment: the folder or file link, the tag values (Style, Fidelity, Type, Proof, Page) and the copy overrides, saved as the person types. Grain: one row per assignment (assignment_id is the primary key). Source: the launch module (app/priorities_launch.py) and the launch worker (launch screen). Refresh: on demand (saved while a person edits). Coverage: from the day the migration was deployed. Caveats: fields is free-form JSON validated on read; an unknown key is ignored.';
COMMENT ON COLUMN public.creative_launch_drafts.assignment_id IS 'creative_priorities_assignments.id this draft belongs to (primary key, foreign key).';
COMMENT ON COLUMN public.creative_launch_drafts.fields IS 'The draft as JSON (link, style, fidelity, type, proof, page, primary texts, headlines).';
COMMENT ON COLUMN public.creative_launch_drafts.updated_at IS 'When the draft was last saved.';
COMMENT ON COLUMN public.creative_launch_drafts.updated_by IS 'Signed-in user who last saved it; NULL when none.';
COMMENT ON TABLE public.creative_launch_jobs IS 'One prepare or launch run for an assignment, with the exact plan it was approved against and its outcome. Grain: one row per run (id is the primary key); at most one open (queued or running) run per assignment. Source: the launch module (app/priorities_launch.py) and the launch worker. Refresh: on demand (a person queues a run; the worker updates it). Coverage: from the day the migration was deployed. Caveats: A launch only runs the plan whose plan_hash the person approved. | dry_run true means nothing is sent to Meta beyond validation.';
COMMENT ON COLUMN public.creative_launch_jobs.id IS 'Primary key (bigserial).';
COMMENT ON COLUMN public.creative_launch_jobs.assignment_id IS 'creative_priorities_assignments.id the run is for (foreign key).';
COMMENT ON COLUMN public.creative_launch_jobs.kind IS '''prepare'' (list files, mint IDs, build the plan) or ''launch'' (create the paused ads).';
COMMENT ON COLUMN public.creative_launch_jobs.state IS '''queued'' | ''running'' | ''done'' | ''failed'' | ''cancelled''.';
COMMENT ON COLUMN public.creative_launch_jobs.plan IS 'The plan as JSON (one entry per ad plus the destination and PAUSED status).';
COMMENT ON COLUMN public.creative_launch_jobs.plan_hash IS 'sha256 of the canonical plan JSON; the launch must carry the same hash that was approved.';
COMMENT ON COLUMN public.creative_launch_jobs.dry_run IS 'True when the run only validates and sends nothing live.';
COMMENT ON COLUMN public.creative_launch_jobs.requested_by IS 'Signed-in user who queued the run.';
COMMENT ON COLUMN public.creative_launch_jobs.error IS 'Scrubbed error text when the run failed; NULL otherwise.';
COMMENT ON COLUMN public.creative_launch_jobs.created_at IS 'When the run was queued.';
COMMENT ON COLUMN public.creative_launch_jobs.started_at IS 'When the worker picked it up; NULL until then.';
COMMENT ON COLUMN public.creative_launch_jobs.finished_at IS 'When it finished; NULL until then.';
COMMENT ON TABLE public.creative_launch_ads IS 'One row per file launched as an ad, with its Concept ID, ad name and the Meta ids created step by step, so a half-finished run can be resumed without duplicating anything. Grain: one row per assignment x Drive file; Concept IDs are unique across all rows. Source: the launch module (app/priorities_launch.py) and the launch worker. Refresh: on demand (the worker updates the row after each Meta step). Coverage: from the day the migration was deployed. Caveats: Concept IDs are minted once and never reused. | state moves planned, media_uploaded, creative_created, created; failed keeps the scrubbed error.';
COMMENT ON COLUMN public.creative_launch_ads.id IS 'Primary key (bigserial).';
COMMENT ON COLUMN public.creative_launch_ads.assignment_id IS 'creative_priorities_assignments.id (foreign key).';
COMMENT ON COLUMN public.creative_launch_ads.job_id IS 'creative_launch_jobs.id of the run that last touched the row (foreign key); NULL when none.';
COMMENT ON COLUMN public.creative_launch_ads.drive_file_id IS 'Google Drive file id of the source file.';
COMMENT ON COLUMN public.creative_launch_ads.file_name IS 'Name of the source file.';
COMMENT ON COLUMN public.creative_launch_ads.kind IS '''image'' or ''video''.';
COMMENT ON COLUMN public.creative_launch_ads.concept_id IS 'Concept ID minted for the ad (unique).';
COMMENT ON COLUMN public.creative_launch_ads.ad_name IS 'Ad name sent to Meta (matches the Creative Tracker Name column format).';
COMMENT ON COLUMN public.creative_launch_ads.state IS '''planned'' | ''media_uploaded'' | ''creative_created'' | ''created'' | ''failed''.';
COMMENT ON COLUMN public.creative_launch_ads.meta_image_hash IS 'Meta image hash after upload; NULL for videos or before upload.';
COMMENT ON COLUMN public.creative_launch_ads.meta_video_id IS 'Meta video id after upload; NULL for images or before upload.';
COMMENT ON COLUMN public.creative_launch_ads.meta_creative_id IS 'Meta ad creative id; NULL until created.';
COMMENT ON COLUMN public.creative_launch_ads.meta_ad_id IS 'Meta ad id; NULL until created.';
COMMENT ON COLUMN public.creative_launch_ads.meta_adset_id IS 'Ad set the ad was created in; NULL until created.';
COMMENT ON COLUMN public.creative_launch_ads.error IS 'Scrubbed error text when the step failed; NULL otherwise.';
COMMENT ON COLUMN public.creative_launch_ads.created_at IS 'When the row was planned.';
COMMENT ON COLUMN public.creative_launch_ads.updated_at IS 'When the row last changed.';
COMMENT ON TABLE public.creative_launch_adsets IS 'The paused ad set the launch created for a product, vendor and week, so later launches of the same combination reuse it. Grain: one row per product x vendor x week (the primary key). Source: the launch module (app/priorities_launch.py) and the launch worker. Refresh: on demand (written when an ad set is first created). Coverage: from the day the migration was deployed. Caveats: Ad set name is <Product>_<Vendor>_<week>_Production.';
COMMENT ON COLUMN public.creative_launch_adsets.product IS 'Product (part of the primary key).';
COMMENT ON COLUMN public.creative_launch_adsets.vendor IS 'Vendor (part of the primary key).';
COMMENT ON COLUMN public.creative_launch_adsets.for_week IS 'Monday of the week the work is for (part of the primary key).';
COMMENT ON COLUMN public.creative_launch_adsets.meta_adset_id IS 'Meta ad set id created for the combination.';
COMMENT ON COLUMN public.creative_launch_adsets.created_at IS 'When the ad set was created.';
COMMENT ON TABLE public.creative_launch_audit IS 'Append-only log of launch actions: settings changed, plan prepared, launch requested, ads created, cancelled. Grain: one row per action (id is the primary key). Source: the launch module (app/priorities_launch.py) and the launch worker. Refresh: on demand (a row per action). Coverage: from the day the migration was deployed. Caveats: Never updated or deleted.';
COMMENT ON COLUMN public.creative_launch_audit.id IS 'Primary key (bigserial).';
COMMENT ON COLUMN public.creative_launch_audit.at IS 'When the action happened.';
COMMENT ON COLUMN public.creative_launch_audit.actor IS 'Signed-in user or worker name.';
COMMENT ON COLUMN public.creative_launch_audit.action IS 'Short action name.';
COMMENT ON COLUMN public.creative_launch_audit.assignment_id IS 'Assignment the action concerns; NULL when none. No foreign key, so the log outlives its subject.';
COMMENT ON COLUMN public.creative_launch_audit.detail IS 'Action details as JSON, never containing a token.';
