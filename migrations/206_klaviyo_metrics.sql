-- 206: Klaviyo flow and campaign metrics. Five tables and one view: the campaign and flow lists (klaviyo_campaigns, klaviyo_flows, klaviyo_flow_messages) and one row per Pacific day x campaign/flow x message x channel (klaviyo_daily), the copy of every message (klaviyo_message_content) and a per-message performance view (klaviyo_message_performance).
-- Written by python -m etl.klaviyo (etl/klaviyo.py): it replaces the pulled window of klaviyo_daily and upserts the lists. Additive and idempotent.
CREATE TABLE IF NOT EXISTS klaviyo_campaigns (
  id           text        PRIMARY KEY,
  name         text,
  channel      text        NOT NULL CHECK (channel IN ('email', 'sms')),
  status       text,
  send_time    timestamptz,
  subject      text,
  created_at   timestamptz,
  updated_at   timestamptz,
  raw          jsonb,
  loaded_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS klaviyo_flows (
  id            text        PRIMARY KEY,
  name          text,
  status        text,
  trigger_type  text,
  created_at    timestamptz,
  updated_at    timestamptz,
  raw           jsonb,
  loaded_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS klaviyo_flow_messages (
  id         text        PRIMARY KEY,
  flow_id    text        NOT NULL,
  name       text,
  channel    text,
  subject    text,
  raw        jsonb,
  loaded_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS klaviyo_flow_messages_flow_idx ON klaviyo_flow_messages (flow_id);

CREATE TABLE IF NOT EXISTS klaviyo_daily (
  day              date        NOT NULL,
  source           text        NOT NULL CHECK (source IN ('campaign', 'flow')),
  parent_id        text        NOT NULL,
  message_id       text        NOT NULL DEFAULT '',
  channel          text        NOT NULL CHECK (channel IN ('email', 'sms')),
  parent_name      text,
  sends            bigint      NOT NULL DEFAULT 0,
  delivered        bigint      NOT NULL DEFAULT 0,
  bounced          bigint      NOT NULL DEFAULT 0,
  opens            bigint      NOT NULL DEFAULT 0,
  unique_opens     bigint      NOT NULL DEFAULT 0,
  clicks           bigint      NOT NULL DEFAULT 0,
  unique_clicks    bigint      NOT NULL DEFAULT 0,
  unsubscribes     bigint      NOT NULL DEFAULT 0,
  spam_complaints  bigint      NOT NULL DEFAULT 0,
  orders           bigint      NOT NULL DEFAULT 0,
  revenue          numeric(14,2) NOT NULL DEFAULT 0,
  loaded_at        timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (day, source, parent_id, message_id, channel)
);
CREATE INDEX IF NOT EXISTS klaviyo_daily_parent_idx ON klaviyo_daily (source, parent_id, day);


CREATE TABLE IF NOT EXISTS klaviyo_message_content (
  message_id    text        PRIMARY KEY,
  source        text        NOT NULL CHECK (source IN ('campaign', 'flow')),
  parent_id     text        NOT NULL,
  channel       text        NOT NULL CHECK (channel IN ('email', 'sms')),
  subject       text,
  preview_text  text,
  from_name     text,
  from_email    text,
  template_id   text,
  editor_type   text,
  html          text,
  body_text     text,
  image_count   integer,
  link_urls     text[],
  content_hash  text,
  fetched_at    timestamptz NOT NULL DEFAULT now(),
  raw           jsonb
);
CREATE INDEX IF NOT EXISTS klaviyo_message_content_parent_idx ON klaviyo_message_content (source, parent_id);

-- One row per message with its copy and its totals over everything loaded in klaviyo_daily. Uniques are SUMS of per-day uniques (a person who opens on two days counts twice), so the two rates built on them read a little high.
CREATE OR REPLACE VIEW klaviyo_message_performance AS
SELECT d.message_id, d.source, d.parent_id, d.channel,
       max(d.parent_name) AS parent_name,
       c.subject, c.preview_text, c.body_text, c.image_count, c.editor_type,
       min(d.day) FILTER (WHERE d.sends > 0) AS first_send_day,
       max(d.day) FILTER (WHERE d.sends > 0) AS last_send_day,
       sum(d.sends)::bigint AS sends, sum(d.delivered)::bigint AS delivered, sum(d.bounced)::bigint AS bounced,
       sum(d.opens)::bigint AS opens, sum(d.unique_opens)::bigint AS unique_opens_daily_sum,
       sum(d.clicks)::bigint AS clicks, sum(d.unique_clicks)::bigint AS unique_clicks_daily_sum,
       sum(d.unsubscribes)::bigint AS unsubscribes, sum(d.spam_complaints)::bigint AS spam_complaints,
       sum(d.orders)::bigint AS orders, sum(d.revenue)::numeric(14,2) AS revenue,
       round(sum(d.unique_opens)::numeric / NULLIF(sum(d.delivered), 0), 4) AS open_rate_daily_uniques,
       round(sum(d.unique_clicks)::numeric / NULLIF(sum(d.delivered), 0), 4) AS click_rate_daily_uniques,
       round(sum(d.orders)::numeric / NULLIF(sum(d.delivered), 0), 5) AS orders_per_delivered,
       round(sum(d.revenue) / NULLIF(sum(d.delivered), 0), 4) AS revenue_per_delivered,
       (c.message_id IS NOT NULL AND c.body_text IS NOT NULL) AS has_copy
FROM klaviyo_daily d
LEFT JOIN klaviyo_message_content c ON c.message_id = d.message_id
WHERE d.message_id <> ''
GROUP BY d.message_id, d.source, d.parent_id, d.channel, c.message_id, c.subject, c.preview_text, c.body_text, c.image_count, c.editor_type;

-- ---------------------------------------------------------------- comments
COMMENT ON TABLE public.klaviyo_campaigns IS 'Dimension: one row per Klaviyo campaign (email and SMS) with its name, status, send time and subject. Names the parent ids of klaviyo_daily. Grain: one row per campaign (id is the primary key; Klaviyo campaign id). Source: Klaviyo API GET /campaigns/ with campaign-messages, both channels, via python -m etl.klaviyo (etl/klaviyo.py). Refresh: nightly with the daily ETL (whole list re-upserted every run). Coverage: every campaign Klaviyo lists, regardless of the metric window. Caveats: raw holds the Klaviyo attributes and the campaign''s messages (id, label, subject); an A/B campaign has several messages and subject is the first one''s. | A campaign that sent in the window but is no longer listed shows in klaviyo_daily as ''(unknown campaign message ...)''.';
COMMENT ON COLUMN public.klaviyo_campaigns.id IS 'Klaviyo campaign id (primary key).';
COMMENT ON COLUMN public.klaviyo_campaigns.name IS 'Campaign name.';
COMMENT ON COLUMN public.klaviyo_campaigns.channel IS '''email'' or ''sms''.';
COMMENT ON COLUMN public.klaviyo_campaigns.status IS 'Klaviyo status text, for example Sent, Draft, Queued without Recipients.';
COMMENT ON COLUMN public.klaviyo_campaigns.send_time IS 'Scheduled send time (UTC); NULL when not scheduled.';
COMMENT ON COLUMN public.klaviyo_campaigns.subject IS 'Subject line of the first message; NULL for SMS and drafts without one.';
COMMENT ON COLUMN public.klaviyo_campaigns.created_at IS 'When the campaign was created in Klaviyo.';
COMMENT ON COLUMN public.klaviyo_campaigns.updated_at IS 'When the campaign was last edited in Klaviyo.';
COMMENT ON COLUMN public.klaviyo_campaigns.raw IS 'Klaviyo attributes plus the campaign''s messages, as JSON.';
COMMENT ON COLUMN public.klaviyo_campaigns.loaded_at IS 'When this row was last written.';
COMMENT ON TABLE public.klaviyo_flows IS 'Dimension: one row per Klaviyo flow with its name, status and trigger type. Names the parent ids of klaviyo_daily. Grain: one row per flow (id is the primary key; Klaviyo flow id). Source: Klaviyo API GET /flows/ via python -m etl.klaviyo (etl/klaviyo.py). Refresh: nightly with the daily ETL (whole list re-upserted every run). Coverage: every flow Klaviyo lists (draft, manual and live), regardless of the metric window. Caveats: raw is the Klaviyo attribute object as returned.';
COMMENT ON COLUMN public.klaviyo_flows.id IS 'Klaviyo flow id (primary key).';
COMMENT ON COLUMN public.klaviyo_flows.name IS 'Flow name.';
COMMENT ON COLUMN public.klaviyo_flows.status IS 'draft, manual or live.';
COMMENT ON COLUMN public.klaviyo_flows.trigger_type IS 'What starts the flow, for example Metric, List, Segment, Date.';
COMMENT ON COLUMN public.klaviyo_flows.created_at IS 'When the flow was created.';
COMMENT ON COLUMN public.klaviyo_flows.updated_at IS 'When the flow was last edited.';
COMMENT ON COLUMN public.klaviyo_flows.raw IS 'Klaviyo attributes as JSON.';
COMMENT ON COLUMN public.klaviyo_flows.loaded_at IS 'When this row was last written.';
COMMENT ON TABLE public.klaviyo_flow_messages IS 'Dimension: one row per message (email or SMS step) inside a Klaviyo flow, with its name and subject. Names the message_id of klaviyo_daily flow rows. Grain: one row per flow message (id is the primary key; Klaviyo flow message id). Source: Klaviyo API GET /flows/{id}/flow-actions/ then /flow-actions/{id}/flow-messages/ via python -m etl.klaviyo (etl/klaviyo.py). Refresh: nightly with the daily ETL, for live flows and flows that had activity in the pulled window. Coverage: messages of every live flow and of flows with sends, opens, clicks or orders in any pulled window. Caveats: Messages of draft or manual flows that were silent in every pulled window are not fetched, so they are missing here. | Messages deleted from a flow are not listed; their old metrics in klaviyo_daily keep the message_id with no name.';
COMMENT ON COLUMN public.klaviyo_flow_messages.id IS 'Klaviyo flow message id (primary key).';
COMMENT ON COLUMN public.klaviyo_flow_messages.flow_id IS 'Parent flow (klaviyo_flows.id). No foreign key.';
COMMENT ON COLUMN public.klaviyo_flow_messages.name IS 'Message name in the flow builder.';
COMMENT ON COLUMN public.klaviyo_flow_messages.channel IS '''email'' or ''sms'' as Klaviyo reports it, lowercased.';
COMMENT ON COLUMN public.klaviyo_flow_messages.subject IS 'Email subject line; NULL for SMS.';
COMMENT ON COLUMN public.klaviyo_flow_messages.raw IS 'Klaviyo attributes plus the flow action id, as JSON.';
COMMENT ON COLUMN public.klaviyo_flow_messages.loaded_at IS 'When this row was last written.';
COMMENT ON TABLE public.klaviyo_daily IS 'Klaviyo email and SMS performance by Pacific day, campaign or flow, message and channel: sends, delivered, opens, clicks, unsubscribes and Klaviyo-attributed orders and revenue. Grain: one row per day (Pacific) x source (''campaign'' | ''flow'') x parent_id x message_id x channel (that is the primary key; message_id is '''' where Klaviyo gives none). Source: Klaviyo metric-aggregates (Received / Bounced / Opened / Clicked / Marked as Spam / Unsubscribed events by $flow and $message, SMS equivalents, and Placed Order - Upstackified by $attributed_flow / $attributed_message) via python -m etl.klaviyo (etl/klaviyo.py). Refresh: nightly with the daily ETL; the trailing 14 complete Pacific days are replaced each run so late opens, clicks and orders land. Coverage: complete Pacific days through yesterday; history from the first backfill (120 days at launch). Caveats: unique_opens and unique_clicks are per-day uniques: do not add them across days or messages; sums run 20-55% above Klaviyo''s single-window uniques. | orders and revenue are Klaviyo-attributed conversions of the metric ''Placed Order - Upstackified'' (S6xLbC, the CheckoutChamp / Upstack server feed), dated by the day the order happened, not the day the message was sent; Shopify''s own Placed Order metric reads about 10% lower. | Clicks exclude clicks Klaviyo flags as bots. sends = delivered + bounced. | Klaviyo does not expose SMS unsubscribes per flow, and campaign SMS unsubscribes are matched by campaign name with no message (message_id ''''); about 2K keyword-STOP unsubscribes have no campaign and are not in this table. | SMS rows have no opens, unique_opens or spam_complaints (0). | Sends, delivered and revenue reconcile within about 3% of Klaviyo''s own campaign and flow value reports.';
COMMENT ON COLUMN public.klaviyo_daily.day IS 'Calendar day in America/Los_Angeles (event date).';
COMMENT ON COLUMN public.klaviyo_daily.source IS '''campaign'' or ''flow''.';
COMMENT ON COLUMN public.klaviyo_daily.parent_id IS 'Klaviyo campaign id or flow id (klaviyo_campaigns.id / klaviyo_flows.id). No foreign key.';
COMMENT ON COLUMN public.klaviyo_daily.message_id IS 'Klaviyo message id (campaign message or flow message); '''' when the row has none (SMS unsubscribes by campaign name).';
COMMENT ON COLUMN public.klaviyo_daily.channel IS '''email'' or ''sms''.';
COMMENT ON COLUMN public.klaviyo_daily.parent_name IS 'Name of the campaign or flow when loaded (denormalised); ''(unknown ...)'' when Klaviyo no longer lists it.';
COMMENT ON COLUMN public.klaviyo_daily.sends IS 'Messages Klaviyo tried to send that day (delivered plus bounced).';
COMMENT ON COLUMN public.klaviyo_daily.delivered IS 'Messages delivered.';
COMMENT ON COLUMN public.klaviyo_daily.bounced IS 'Emails bounced or SMS that failed to deliver.';
COMMENT ON COLUMN public.klaviyo_daily.opens IS 'Email opens (includes Apple Mail Privacy Protection machine opens); 0 for SMS.';
COMMENT ON COLUMN public.klaviyo_daily.unique_opens IS 'Distinct recipients who opened that day (per-day unique, not additive); 0 for SMS.';
COMMENT ON COLUMN public.klaviyo_daily.clicks IS 'Clicks, bot clicks excluded.';
COMMENT ON COLUMN public.klaviyo_daily.unique_clicks IS 'Distinct recipients who clicked that day (per-day unique, not additive).';
COMMENT ON COLUMN public.klaviyo_daily.unsubscribes IS 'Unsubscribes counted that day (email: clicked-to-unsubscribe events; SMS: campaign-level only).';
COMMENT ON COLUMN public.klaviyo_daily.spam_complaints IS 'Emails marked as spam; 0 for SMS.';
COMMENT ON COLUMN public.klaviyo_daily.orders IS 'Orders Klaviyo attributes to the message, dated by order day.';
COMMENT ON COLUMN public.klaviyo_daily.revenue IS 'Revenue of those orders in dollars (Placed Order - Upstackified value), dated by order day.';
COMMENT ON COLUMN public.klaviyo_daily.loaded_at IS 'When this row was last written.';
COMMENT ON TABLE public.klaviyo_message_content IS 'The copy of every Klaviyo message (campaign message or flow message) that had activity in a pulled window, or that sits in a live flow: subject, preview text, full email HTML, readable body text and the message links. Joins to klaviyo_daily on message_id. Grain: one row per message (message_id is the primary key; Klaviyo campaign message id or flow message id). Source: Klaviyo API: GET /campaign-messages/{id}/template/ and /flow-messages/{id}/template/ (email HTML), the message definitions from GET /campaigns/ and /flow-actions/{id}/flow-messages/ (subject, preview, sender, SMS body), via python -m etl.klaviyo (etl/klaviyo.py). Refresh: nightly with the daily ETL, for messages with activity in the trailing 14 days and every message of a live flow. Coverage: messages with activity in any pulled window (120 days at launch) plus live-flow messages; not every message Klaviyo has ever held. Caveats: Only the latest copy is kept: when a message is edited the row is overwritten and fetched_at moves (content_hash changes); the history of edits is not kept, so old performance reads against the current copy. | Messages whose template Klaviyo no longer returns (deleted campaigns or templates) have a row with NULL html and body_text and raw.fetch_error set. | body_text is extracted from the HTML (head, scripts and styles dropped, link text kept, whitespace collapsed); image-only emails have little or no text, and image_count says how many real images the email carries. For SMS body_text is the message body and html is NULL. | image_count excludes tracking pixels and 1-pixel images.';
COMMENT ON COLUMN public.klaviyo_message_content.message_id IS 'Klaviyo message id (primary key); equals klaviyo_daily.message_id.';
COMMENT ON COLUMN public.klaviyo_message_content.source IS '''campaign'' or ''flow''.';
COMMENT ON COLUMN public.klaviyo_message_content.parent_id IS 'Campaign id or flow id of the message. No foreign key.';
COMMENT ON COLUMN public.klaviyo_message_content.channel IS '''email'' or ''sms''.';
COMMENT ON COLUMN public.klaviyo_message_content.subject IS 'Email subject line; NULL for SMS.';
COMMENT ON COLUMN public.klaviyo_message_content.preview_text IS 'Email preview text; NULL for SMS or when none is set.';
COMMENT ON COLUMN public.klaviyo_message_content.from_name IS 'Sender name of the email.';
COMMENT ON COLUMN public.klaviyo_message_content.from_email IS 'Sender address of the email.';
COMMENT ON COLUMN public.klaviyo_message_content.template_id IS 'Klaviyo template id the email renders from; NULL for SMS or when the template was not returned.';
COMMENT ON COLUMN public.klaviyo_message_content.editor_type IS 'Klaviyo editor of the template: SIMPLE (plain text email), CODE, DRAG_AND_DROP, USER_DRAFTED; NULL for SMS.';
COMMENT ON COLUMN public.klaviyo_message_content.html IS 'Full template HTML of the email; NULL for SMS or when not returned.';
COMMENT ON COLUMN public.klaviyo_message_content.body_text IS 'Readable plain text of the email (or the SMS body); NULL when nothing could be fetched or the email is images only.';
COMMENT ON COLUMN public.klaviyo_message_content.image_count IS 'Real images in the email (tracking pixels excluded); 1 for an SMS with a picture.';
COMMENT ON COLUMN public.klaviyo_message_content.link_urls IS 'Distinct http(s) links in the message, in order of appearance.';
COMMENT ON COLUMN public.klaviyo_message_content.content_hash IS 'sha256 of the HTML (email) or body (SMS); changes when the message is edited.';
COMMENT ON COLUMN public.klaviyo_message_content.fetched_at IS 'When the content last changed in our copy (moves only when content_hash changes).';
COMMENT ON COLUMN public.klaviyo_message_content.raw IS 'The message definition as Klaviyo returns it (without the body / html), plus fetch_error when the template could not be fetched.';
COMMENT ON VIEW public.klaviyo_message_performance IS 'One row per Klaviyo message with its copy (subject, preview text, body text) and its totals and rates over everything in klaviyo_daily: the table to read which copy earns. Grain: one row per message_id (campaign or flow message with at least one klaviyo_daily row). Source: klaviyo_daily (grouped by message_id) joined to klaviyo_message_content. Refresh: live (a view over klaviyo_daily; moves with the nightly Klaviyo load). Coverage: the days loaded in klaviyo_daily (120 days at launch, then trailing); first_send_day is the first loaded day with sends, not the message''s real first send. Caveats: unique_opens_daily_sum and unique_clicks_daily_sum are sums of per-day uniques, not window uniques: a person who opens on two days counts twice. open_rate_daily_uniques and click_rate_daily_uniques are built on them and read a little high; use them to compare messages, not as Klaviyo''s reported rates. | Orders and revenue are Klaviyo-attributed conversions of ''Placed Order - Upstackified'', dated by the order day, so a message can show revenue with no sends when it sent before the loaded window. | Rates are NULL when delivered is 0. SMS messages have no opens. Messages without fetched copy have NULL subject / body_text and has_copy false. | SMS campaign unsubscribes are matched to the campaign, not the message, so they are not in this view.';
COMMENT ON COLUMN public.klaviyo_message_performance.message_id IS 'Klaviyo message id.';
COMMENT ON COLUMN public.klaviyo_message_performance.source IS '''campaign'' or ''flow''.';
COMMENT ON COLUMN public.klaviyo_message_performance.parent_id IS 'Campaign id or flow id.';
COMMENT ON COLUMN public.klaviyo_message_performance.channel IS '''email'' or ''sms''.';
COMMENT ON COLUMN public.klaviyo_message_performance.parent_name IS 'Campaign or flow name.';
COMMENT ON COLUMN public.klaviyo_message_performance.subject IS 'Email subject (current copy); NULL for SMS or when not fetched.';
COMMENT ON COLUMN public.klaviyo_message_performance.preview_text IS 'Email preview text (current copy).';
COMMENT ON COLUMN public.klaviyo_message_performance.body_text IS 'Readable body text (current copy).';
COMMENT ON COLUMN public.klaviyo_message_performance.image_count IS 'Real images in the email.';
COMMENT ON COLUMN public.klaviyo_message_performance.editor_type IS 'Klaviyo template editor type.';
COMMENT ON COLUMN public.klaviyo_message_performance.first_send_day IS 'First loaded day with sends.';
COMMENT ON COLUMN public.klaviyo_message_performance.last_send_day IS 'Last loaded day with sends.';
COMMENT ON COLUMN public.klaviyo_message_performance.sends IS 'Sum of sends.';
COMMENT ON COLUMN public.klaviyo_message_performance.delivered IS 'Sum of delivered.';
COMMENT ON COLUMN public.klaviyo_message_performance.bounced IS 'Sum of bounced or failed.';
COMMENT ON COLUMN public.klaviyo_message_performance.opens IS 'Sum of opens (includes machine opens).';
COMMENT ON COLUMN public.klaviyo_message_performance.unique_opens_daily_sum IS 'Sum of per-day unique opens (not a window unique).';
COMMENT ON COLUMN public.klaviyo_message_performance.clicks IS 'Sum of clicks, bots excluded.';
COMMENT ON COLUMN public.klaviyo_message_performance.unique_clicks_daily_sum IS 'Sum of per-day unique clicks (not a window unique).';
COMMENT ON COLUMN public.klaviyo_message_performance.unsubscribes IS 'Sum of unsubscribes.';
COMMENT ON COLUMN public.klaviyo_message_performance.spam_complaints IS 'Sum of spam complaints.';
COMMENT ON COLUMN public.klaviyo_message_performance.orders IS 'Sum of attributed orders.';
COMMENT ON COLUMN public.klaviyo_message_performance.revenue IS 'Sum of attributed revenue in dollars.';
COMMENT ON COLUMN public.klaviyo_message_performance.open_rate_daily_uniques IS 'unique_opens_daily_sum / delivered; NULL when delivered is 0.';
COMMENT ON COLUMN public.klaviyo_message_performance.click_rate_daily_uniques IS 'unique_clicks_daily_sum / delivered; NULL when delivered is 0.';
COMMENT ON COLUMN public.klaviyo_message_performance.orders_per_delivered IS 'orders / delivered; NULL when delivered is 0.';
COMMENT ON COLUMN public.klaviyo_message_performance.revenue_per_delivered IS 'revenue / delivered in dollars; NULL when delivered is 0.';
COMMENT ON COLUMN public.klaviyo_message_performance.has_copy IS 'True when body_text was fetched for the message.';
