-- 231: analytics views for tables that live on open pull requests, so they exist only when their source does.
--   Klaviyo (feat/klaviyo-metrics, migration 206): klaviyo_daily, klaviyo_campaigns, klaviyo_flows, klaviyo_flow_messages, klaviyo_message_content, klaviyo_message_performance      (domain email)
--   Landing pages (feat/lp-copy-store, migration 207): landing_pages, landing_page_versions, landing_page_ads, landing_page_ad_checks, landing_page_top_ads                         (domain landing_pages)
--   Shopify orders (feat/orders-table, migration 210): shopify_orders, shopify_order_lines                                                                                         (domain orders)
-- Each view is `analytics.<same name>` with the same column names and the same COMMENTs as its source, minus what an agent must not read (below). Each is created only when
-- to_regclass says its source exists AND the source still has every column the view selects; otherwise that block is a no-op, so this file is safe before those PRs merge, safe to re-run
-- (CREATE OR REPLACE VIEW, COMMENT ON) and is meant to be run again after they merge (delete its row from schema_migrations, or apply the file by hand). It never references a missing table.
-- analytics.catalog (migration 230) reads the live schema, so these views appear in it by themselves (domain from descriptions.py); each block GRANTs SELECT to analytics_reader when that role exists.
--
-- Left out of the views:
--   shopify_orders.customer_id and .tags (an identifier that links to a person, and free text that can hold names); the table has no email, name, phone, address or IP at all.
--   Kept: order id and number, dates, totals, discounts, discount codes, refunds, status, source, country and region, new vs returning, rebill / test / gift flags; lines with SKU, quantity, price.
--   raw (jsonb) of klaviyo_campaigns / klaviyo_flows / klaviyo_flow_messages / klaviyo_message_content: the API payload as received.
--   landing_page_versions.blocks and .images (raw page structure), landing_page_ads.raw_url (the ad's full destination link with its tracking query string).

CREATE SCHEMA IF NOT EXISTS analytics;

-- klaviyo_daily
DO $v$
BEGIN
  IF to_regclass('public.klaviyo_daily') IS NOT NULL
     AND (SELECT count(*) FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'klaviyo_daily' AND column_name = ANY(ARRAY['day', 'source', 'parent_id', 'message_id', 'channel', 'parent_name', 'sends', 'delivered', 'bounced', 'opens', 'unique_opens', 'clicks', 'unique_clicks', 'unsubscribes', 'spam_complaints', 'orders', 'revenue', 'loaded_at'])) = 18 THEN
    EXECUTE $q$CREATE OR REPLACE VIEW analytics.klaviyo_daily AS SELECT day, source, parent_id, message_id, channel, parent_name, sends, delivered, bounced, opens, unique_opens, clicks, unique_clicks, unsubscribes, spam_complaints, orders, revenue, loaded_at FROM public.klaviyo_daily$q$;
    EXECUTE $q$COMMENT ON VIEW analytics.klaviyo_daily IS 'Klaviyo email and SMS performance by Pacific day, campaign or flow, message and channel: sends, delivered, opens, clicks, unsubscribes and Klaviyo-attributed orders and revenue. Grain: one row per day (Pacific) x source (''campaign'' | ''flow'') x parent_id x message_id x channel (that is the primary key; message_id is '''' where Klaviyo gives none). Source: Klaviyo metric-aggregates (Received / Bounced / Opened / Clicked / Marked as Spam / Unsubscribed events by $flow and $message, SMS equivalents, and Placed Order - Upstackified by $attributed_flow / $attributed_message) via python -m etl.klaviyo (etl/klaviyo.py). Refresh: nightly with the daily ETL; the trailing 14 complete Pacific days are replaced each run so late opens, clicks and orders land. Coverage: complete Pacific days through yesterday; history from the first backfill (120 days at launch). Caveats: unique_opens and unique_clicks are per-day uniques: do not add them across days or messages; sums run 20-55% above Klaviyo''s single-window uniques. | orders and revenue are Klaviyo-attributed conversions of the metric ''Placed Order - Upstackified'' (S6xLbC, the CheckoutChamp / Upstack server feed), dated by the day the order happened, not the day the message was sent; Shopify''s own Placed Order metric reads about 10% lower. | Clicks exclude clicks Klaviyo flags as bots. sends = delivered + bounced. | Klaviyo does not expose SMS unsubscribes per flow, and campaign SMS unsubscribes are matched by campaign name with no message (message_id ''''); about 2K keyword-STOP unsubscribes have no campaign and are not in this table. | SMS rows have no opens, unique_opens or spam_complaints (0). | Sends, delivered and revenue reconcile within about 3% of Klaviyo''s own campaign and flow value reports.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_daily.day IS 'Calendar day in America/Los_Angeles (event date).'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_daily.source IS '''campaign'' or ''flow''.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_daily.parent_id IS 'Klaviyo campaign id or flow id (klaviyo_campaigns.id / klaviyo_flows.id). No foreign key.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_daily.message_id IS 'Klaviyo message id (campaign message or flow message); '''' when the row has none (SMS unsubscribes by campaign name).'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_daily.channel IS '''email'' or ''sms''.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_daily.parent_name IS 'Name of the campaign or flow when loaded (denormalised); ''(unknown ...)'' when Klaviyo no longer lists it.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_daily.sends IS 'Messages Klaviyo tried to send that day (delivered plus bounced).'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_daily.delivered IS 'Messages delivered.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_daily.bounced IS 'Emails bounced or SMS that failed to deliver.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_daily.opens IS 'Email opens (includes Apple Mail Privacy Protection machine opens); 0 for SMS.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_daily.unique_opens IS 'Distinct recipients who opened that day (per-day unique, not additive); 0 for SMS.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_daily.clicks IS 'Clicks, bot clicks excluded.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_daily.unique_clicks IS 'Distinct recipients who clicked that day (per-day unique, not additive).'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_daily.unsubscribes IS 'Unsubscribes counted that day (email: clicked-to-unsubscribe events; SMS: campaign-level only).'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_daily.spam_complaints IS 'Emails marked as spam; 0 for SMS.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_daily.orders IS 'Orders Klaviyo attributes to the message, dated by order day.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_daily.revenue IS 'Revenue of those orders in dollars (Placed Order - Upstackified value), dated by order day.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_daily.loaded_at IS 'When this row was last written.'$q$;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'analytics_reader') THEN
      EXECUTE 'GRANT SELECT ON analytics.klaviyo_daily TO analytics_reader';
    END IF;
  END IF;
END
$v$;

-- klaviyo_campaigns
DO $v$
BEGIN
  IF to_regclass('public.klaviyo_campaigns') IS NOT NULL
     AND (SELECT count(*) FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'klaviyo_campaigns' AND column_name = ANY(ARRAY['id', 'name', 'channel', 'status', 'send_time', 'subject', 'created_at', 'updated_at', 'loaded_at'])) = 9 THEN
    EXECUTE $q$CREATE OR REPLACE VIEW analytics.klaviyo_campaigns AS SELECT id, name, channel, status, send_time, subject, created_at, updated_at, loaded_at FROM public.klaviyo_campaigns$q$;
    EXECUTE $q$COMMENT ON VIEW analytics.klaviyo_campaigns IS 'Dimension: one row per Klaviyo campaign (email and SMS) with its name, status, send time and subject. Names the parent ids of klaviyo_daily. Grain: one row per campaign (id is the primary key; Klaviyo campaign id). Source: Klaviyo API GET /campaigns/ with campaign-messages, both channels, via python -m etl.klaviyo (etl/klaviyo.py). Refresh: nightly with the daily ETL (whole list re-upserted every run). Coverage: every campaign Klaviyo lists, regardless of the metric window. Caveats: raw holds the Klaviyo attributes and the campaign''s messages (id, label, subject); an A/B campaign has several messages and subject is the first one''s. | A campaign that sent in the window but is no longer listed shows in klaviyo_daily as ''(unknown campaign message ...)''.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_campaigns.id IS 'Klaviyo campaign id (primary key).'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_campaigns.name IS 'Campaign name.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_campaigns.channel IS '''email'' or ''sms''.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_campaigns.status IS 'Klaviyo status text, for example Sent, Draft, Queued without Recipients.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_campaigns.send_time IS 'Scheduled send time (UTC); NULL when not scheduled.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_campaigns.subject IS 'Subject line of the first message; NULL for SMS and drafts without one.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_campaigns.created_at IS 'When the campaign was created in Klaviyo.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_campaigns.updated_at IS 'When the campaign was last edited in Klaviyo.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_campaigns.loaded_at IS 'When this row was last written.'$q$;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'analytics_reader') THEN
      EXECUTE 'GRANT SELECT ON analytics.klaviyo_campaigns TO analytics_reader';
    END IF;
  END IF;
END
$v$;

-- klaviyo_flows
DO $v$
BEGIN
  IF to_regclass('public.klaviyo_flows') IS NOT NULL
     AND (SELECT count(*) FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'klaviyo_flows' AND column_name = ANY(ARRAY['id', 'name', 'status', 'trigger_type', 'created_at', 'updated_at', 'loaded_at'])) = 7 THEN
    EXECUTE $q$CREATE OR REPLACE VIEW analytics.klaviyo_flows AS SELECT id, name, status, trigger_type, created_at, updated_at, loaded_at FROM public.klaviyo_flows$q$;
    EXECUTE $q$COMMENT ON VIEW analytics.klaviyo_flows IS 'Dimension: one row per Klaviyo flow with its name, status and trigger type. Names the parent ids of klaviyo_daily. Grain: one row per flow (id is the primary key; Klaviyo flow id). Source: Klaviyo API GET /flows/ via python -m etl.klaviyo (etl/klaviyo.py). Refresh: nightly with the daily ETL (whole list re-upserted every run). Coverage: every flow Klaviyo lists (draft, manual and live), regardless of the metric window. Caveats: raw is the Klaviyo attribute object as returned.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_flows.id IS 'Klaviyo flow id (primary key).'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_flows.name IS 'Flow name.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_flows.status IS 'draft, manual or live.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_flows.trigger_type IS 'What starts the flow, for example Metric, List, Segment, Date.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_flows.created_at IS 'When the flow was created.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_flows.updated_at IS 'When the flow was last edited.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_flows.loaded_at IS 'When this row was last written.'$q$;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'analytics_reader') THEN
      EXECUTE 'GRANT SELECT ON analytics.klaviyo_flows TO analytics_reader';
    END IF;
  END IF;
END
$v$;

-- klaviyo_flow_messages
DO $v$
BEGIN
  IF to_regclass('public.klaviyo_flow_messages') IS NOT NULL
     AND (SELECT count(*) FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'klaviyo_flow_messages' AND column_name = ANY(ARRAY['id', 'flow_id', 'name', 'channel', 'subject', 'loaded_at'])) = 6 THEN
    EXECUTE $q$CREATE OR REPLACE VIEW analytics.klaviyo_flow_messages AS SELECT id, flow_id, name, channel, subject, loaded_at FROM public.klaviyo_flow_messages$q$;
    EXECUTE $q$COMMENT ON VIEW analytics.klaviyo_flow_messages IS 'Dimension: one row per message (email or SMS step) inside a Klaviyo flow, with its name and subject. Names the message_id of klaviyo_daily flow rows. Grain: one row per flow message (id is the primary key; Klaviyo flow message id). Source: Klaviyo API GET /flows/{id}/flow-actions/ then /flow-actions/{id}/flow-messages/ via python -m etl.klaviyo (etl/klaviyo.py). Refresh: nightly with the daily ETL, for live flows and flows that had activity in the pulled window. Coverage: messages of every live flow and of flows with sends, opens, clicks or orders in any pulled window. Caveats: Messages of draft or manual flows that were silent in every pulled window are not fetched, so they are missing here. | Messages deleted from a flow are not listed; their old metrics in klaviyo_daily keep the message_id with no name.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_flow_messages.id IS 'Klaviyo flow message id (primary key).'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_flow_messages.flow_id IS 'Parent flow (klaviyo_flows.id). No foreign key.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_flow_messages.name IS 'Message name in the flow builder.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_flow_messages.channel IS '''email'' or ''sms'' as Klaviyo reports it, lowercased.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_flow_messages.subject IS 'Email subject line; NULL for SMS.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_flow_messages.loaded_at IS 'When this row was last written.'$q$;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'analytics_reader') THEN
      EXECUTE 'GRANT SELECT ON analytics.klaviyo_flow_messages TO analytics_reader';
    END IF;
  END IF;
END
$v$;

-- klaviyo_message_content
DO $v$
BEGIN
  IF to_regclass('public.klaviyo_message_content') IS NOT NULL
     AND (SELECT count(*) FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'klaviyo_message_content' AND column_name = ANY(ARRAY['message_id', 'source', 'parent_id', 'channel', 'subject', 'preview_text', 'from_name', 'from_email', 'template_id', 'editor_type', 'html', 'body_text', 'image_count', 'link_urls', 'content_hash', 'fetched_at'])) = 16 THEN
    EXECUTE $q$CREATE OR REPLACE VIEW analytics.klaviyo_message_content AS SELECT message_id, source, parent_id, channel, subject, preview_text, from_name, from_email, template_id, editor_type, html, body_text, image_count, link_urls, content_hash, fetched_at FROM public.klaviyo_message_content$q$;
    EXECUTE $q$COMMENT ON VIEW analytics.klaviyo_message_content IS 'The copy of every Klaviyo message (campaign message or flow message) that had activity in a pulled window, or that sits in a live flow: subject, preview text, full email HTML, readable body text and the message links. Joins to klaviyo_daily on message_id. Grain: one row per message (message_id is the primary key; Klaviyo campaign message id or flow message id). Source: Klaviyo API: GET /campaign-messages/{id}/template/ and /flow-messages/{id}/template/ (email HTML), the message definitions from GET /campaigns/ and /flow-actions/{id}/flow-messages/ (subject, preview, sender, SMS body), via python -m etl.klaviyo (etl/klaviyo.py). Refresh: nightly with the daily ETL, for messages with activity in the trailing 14 days and every message of a live flow. Coverage: messages with activity in any pulled window (120 days at launch) plus live-flow messages; not every message Klaviyo has ever held. Caveats: Only the latest copy is kept: when a message is edited the row is overwritten and fetched_at moves (content_hash changes); the history of edits is not kept, so old performance reads against the current copy. | Messages whose template Klaviyo no longer returns (deleted campaigns or templates) have a row with NULL html and body_text and raw.fetch_error set. | body_text is extracted from the HTML (head, scripts and styles dropped, link text kept, whitespace collapsed); image-only emails have little or no text, and image_count says how many real images the email carries. For SMS body_text is the message body and html is NULL. | image_count excludes tracking pixels and 1-pixel images.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_message_content.message_id IS 'Klaviyo message id (primary key); equals klaviyo_daily.message_id.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_message_content.source IS '''campaign'' or ''flow''.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_message_content.parent_id IS 'Campaign id or flow id of the message. No foreign key.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_message_content.channel IS '''email'' or ''sms''.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_message_content.subject IS 'Email subject line; NULL for SMS.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_message_content.preview_text IS 'Email preview text; NULL for SMS or when none is set.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_message_content.from_name IS 'Sender name of the email.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_message_content.from_email IS 'Sender address of the email.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_message_content.template_id IS 'Klaviyo template id the email renders from; NULL for SMS or when the template was not returned.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_message_content.editor_type IS 'Klaviyo editor of the template: SIMPLE (plain text email), CODE, DRAG_AND_DROP, USER_DRAFTED; NULL for SMS.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_message_content.html IS 'Full template HTML of the email; NULL for SMS or when not returned.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_message_content.body_text IS 'Readable plain text of the email (or the SMS body); NULL when nothing could be fetched or the email is images only.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_message_content.image_count IS 'Real images in the email (tracking pixels excluded); 1 for an SMS with a picture.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_message_content.link_urls IS 'Distinct http(s) links in the message, in order of appearance.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_message_content.content_hash IS 'sha256 of the HTML (email) or body (SMS); changes when the message is edited.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_message_content.fetched_at IS 'When the content last changed in our copy (moves only when content_hash changes).'$q$;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'analytics_reader') THEN
      EXECUTE 'GRANT SELECT ON analytics.klaviyo_message_content TO analytics_reader';
    END IF;
  END IF;
END
$v$;

-- klaviyo_message_performance
DO $v$
BEGIN
  IF to_regclass('public.klaviyo_message_performance') IS NOT NULL
     AND (SELECT count(*) FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'klaviyo_message_performance' AND column_name = ANY(ARRAY['message_id', 'source', 'parent_id', 'channel', 'parent_name', 'subject', 'preview_text', 'body_text', 'image_count', 'editor_type', 'first_send_day', 'last_send_day', 'sends', 'delivered', 'bounced', 'opens', 'unique_opens_daily_sum', 'clicks', 'unique_clicks_daily_sum', 'unsubscribes', 'spam_complaints', 'orders', 'revenue', 'open_rate_daily_uniques', 'click_rate_daily_uniques', 'orders_per_delivered', 'revenue_per_delivered', 'has_copy'])) = 28 THEN
    EXECUTE $q$CREATE OR REPLACE VIEW analytics.klaviyo_message_performance AS SELECT message_id, source, parent_id, channel, parent_name, subject, preview_text, body_text, image_count, editor_type, first_send_day, last_send_day, sends, delivered, bounced, opens, unique_opens_daily_sum, clicks, unique_clicks_daily_sum, unsubscribes, spam_complaints, orders, revenue, open_rate_daily_uniques, click_rate_daily_uniques, orders_per_delivered, revenue_per_delivered, has_copy FROM public.klaviyo_message_performance$q$;
    EXECUTE $q$COMMENT ON VIEW analytics.klaviyo_message_performance IS 'One row per Klaviyo message with its copy (subject, preview text, body text) and its totals and rates over everything in klaviyo_daily: the table to read which copy earns. Grain: one row per message_id (campaign or flow message with at least one klaviyo_daily row). Source: klaviyo_daily (grouped by message_id) joined to klaviyo_message_content. Refresh: live (a view over klaviyo_daily; moves with the nightly Klaviyo load). Coverage: the days loaded in klaviyo_daily (120 days at launch, then trailing); first_send_day is the first loaded day with sends, not the message''s real first send. Caveats: unique_opens_daily_sum and unique_clicks_daily_sum are sums of per-day uniques, not window uniques: a person who opens on two days counts twice. open_rate_daily_uniques and click_rate_daily_uniques are built on them and read a little high; use them to compare messages, not as Klaviyo''s reported rates. | Orders and revenue are Klaviyo-attributed conversions of ''Placed Order - Upstackified'', dated by the order day, so a message can show revenue with no sends when it sent before the loaded window. | Rates are NULL when delivered is 0. SMS messages have no opens. Messages without fetched copy have NULL subject / body_text and has_copy false. | SMS campaign unsubscribes are matched to the campaign, not the message, so they are not in this view.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_message_performance.message_id IS 'Klaviyo message id.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_message_performance.source IS '''campaign'' or ''flow''.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_message_performance.parent_id IS 'Campaign id or flow id.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_message_performance.channel IS '''email'' or ''sms''.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_message_performance.parent_name IS 'Campaign or flow name.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_message_performance.subject IS 'Email subject (current copy); NULL for SMS or when not fetched.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_message_performance.preview_text IS 'Email preview text (current copy).'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_message_performance.body_text IS 'Readable body text (current copy).'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_message_performance.image_count IS 'Real images in the email.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_message_performance.editor_type IS 'Klaviyo template editor type.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_message_performance.first_send_day IS 'First loaded day with sends.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_message_performance.last_send_day IS 'Last loaded day with sends.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_message_performance.sends IS 'Sum of sends.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_message_performance.delivered IS 'Sum of delivered.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_message_performance.bounced IS 'Sum of bounced or failed.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_message_performance.opens IS 'Sum of opens (includes machine opens).'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_message_performance.unique_opens_daily_sum IS 'Sum of per-day unique opens (not a window unique).'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_message_performance.clicks IS 'Sum of clicks, bots excluded.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_message_performance.unique_clicks_daily_sum IS 'Sum of per-day unique clicks (not a window unique).'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_message_performance.unsubscribes IS 'Sum of unsubscribes.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_message_performance.spam_complaints IS 'Sum of spam complaints.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_message_performance.orders IS 'Sum of attributed orders.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_message_performance.revenue IS 'Sum of attributed revenue in dollars.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_message_performance.open_rate_daily_uniques IS 'unique_opens_daily_sum / delivered; NULL when delivered is 0.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_message_performance.click_rate_daily_uniques IS 'unique_clicks_daily_sum / delivered; NULL when delivered is 0.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_message_performance.orders_per_delivered IS 'orders / delivered; NULL when delivered is 0.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_message_performance.revenue_per_delivered IS 'revenue / delivered in dollars; NULL when delivered is 0.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.klaviyo_message_performance.has_copy IS 'True when body_text was fetched for the message.'$q$;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'analytics_reader') THEN
      EXECUTE 'GRANT SELECT ON analytics.klaviyo_message_performance TO analytics_reader';
    END IF;
  END IF;
END
$v$;

-- landing_pages
DO $v$
BEGIN
  IF to_regclass('public.landing_pages') IS NOT NULL
     AND (SELECT count(*) FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'landing_pages' AND column_name = ANY(ARRAY['id', 'url', 'host', 'path', 'first_seen_at', 'last_seen_at', 'last_fetched_at', 'last_status', 'last_error', 'current_version_id', 'active'])) = 11 THEN
    EXECUTE $q$CREATE OR REPLACE VIEW analytics.landing_pages AS SELECT id, url, host, path, first_seen_at, last_seen_at, last_fetched_at, last_status, last_error, current_version_id, active FROM public.landing_pages$q$;
    EXECUTE $q$COMMENT ON VIEW analytics.landing_pages IS 'Every landing page (sales page, advertorial, product page, funnel step) that at least one Meta ad sends people to, with when we last read it. Grain: one row per normalised page address (id is the primary key; url is unique). Source: etl/landing_pages.py: the destination links found on Meta ad creatives. Refresh: each run of python -m etl.landing_pages (not scheduled yet; see docs/LANDING_PAGES.md). Coverage: pages of ads that had spend in the window of the first run onward. Caveats: The address is normalised: https, lower-case host, tracking parameters and the part after # removed, no trailing slash. Parameters that change which page loads are kept. | current_version_id points at the newest landing_page_versions row whose copy matches the last read (the copy can go back to an older version if the page is changed back).'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.landing_pages.id IS 'Primary key (bigserial).'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.landing_pages.url IS 'Normalised page address (unique).'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.landing_pages.host IS 'Host name of the page, lower case.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.landing_pages.path IS 'Path and any kept query string of the page.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.landing_pages.first_seen_at IS 'When an ad was first found pointing at this page.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.landing_pages.last_seen_at IS 'When an ad was last found pointing at this page.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.landing_pages.last_fetched_at IS 'When the page was last read (successfully or not); NULL until the first read.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.landing_pages.last_status IS 'HTTP status of the last successful read; NULL before the first.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.landing_pages.last_error IS 'Short scrubbed error text when the last read failed; NULL after a good read.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.landing_pages.current_version_id IS 'landing_page_versions.id of the copy seen on the last good read.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.landing_pages.active IS 'False stops the page being read again; set by hand, nothing sets it.'$q$;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'analytics_reader') THEN
      EXECUTE 'GRANT SELECT ON analytics.landing_pages TO analytics_reader';
    END IF;
  END IF;
END
$v$;

-- landing_page_versions
DO $v$
BEGIN
  IF to_regclass('public.landing_page_versions') IS NOT NULL
     AND (SELECT count(*) FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'landing_page_versions' AND column_name = ANY(ARRAY['id', 'landing_page_id', 'content_hash', 'fetched_at', 'http_status', 'final_url', 'title', 'meta_description', 'rendered_in_browser', 'word_count', 'ctas', 'prices', 'guarantees', 'reviews', 'full_text'])) = 15 THEN
    EXECUTE $q$CREATE OR REPLACE VIEW analytics.landing_page_versions AS SELECT id, landing_page_id, content_hash, fetched_at, http_status, final_url, title, meta_description, rendered_in_browser, word_count, ctas, prices, guarantees, reviews, full_text FROM public.landing_page_versions$q$;
    EXECUTE $q$COMMENT ON VIEW analytics.landing_page_versions IS 'The copy of a landing page as it was on a given read: title, description, ordered text blocks with heading level, buttons, prices, guarantee sentences, review snippets and image alt text. Grain: one row per (landing page, content hash): a new row exists only when the copy changed (id is the primary key). Source: etl/landing_pages.py: a plain public web request per page, parsed with the standard library (headless Chromium for pages built in the browser). Refresh: each run of python -m etl.landing_pages; a row is added only when the text hash changes. Coverage: from the first read of each page. Caveats: content_hash covers the text only (title, description, text blocks, button text, image alt text), not image addresses or scripts, so changing a picture without changing its alt text is not a new version. | Pages built in the browser have little text in the server HTML; those are read again with Chromium and rendered_in_browser is true. Text shown only after a click or inside an iframe is not captured. | fetched_at is when this exact copy was first seen, not when it was last confirmed (landing_pages.last_fetched_at has that).'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.landing_page_versions.id IS 'Primary key (bigserial).'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.landing_page_versions.landing_page_id IS 'Page this copy belongs to (landing_pages.id, foreign key).'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.landing_page_versions.content_hash IS 'sha256 of the page text (see caveats); unique per page.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.landing_page_versions.fetched_at IS 'When this copy was first seen.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.landing_page_versions.http_status IS 'HTTP status of the read that produced this copy.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.landing_page_versions.final_url IS 'Address after redirects.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.landing_page_versions.title IS 'Page title (the title tag, else the Open Graph title).'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.landing_page_versions.meta_description IS 'Meta description (else the Open Graph description).'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.landing_page_versions.rendered_in_browser IS 'True when the copy came from headless Chromium because the server HTML held almost no text.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.landing_page_versions.word_count IS 'Words in the page text.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.landing_page_versions.ctas IS 'Button and call to action text as JSON: [{text, href, tag}].'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.landing_page_versions.prices IS 'Dollar amounts found in the text as JSON: [{price, context}].'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.landing_page_versions.guarantees IS 'Sentences that mention a guarantee, refund, money back or risk free, as a JSON array of strings.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.landing_page_versions.reviews IS 'Review or testimonial snippets (quote blocks, star lines, structured data), as a JSON array of strings.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.landing_page_versions.full_text IS 'All text blocks joined by new lines, for searching.'$q$;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'analytics_reader') THEN
      EXECUTE 'GRANT SELECT ON analytics.landing_page_versions TO analytics_reader';
    END IF;
  END IF;
END
$v$;

-- landing_page_ads
DO $v$
BEGIN
  IF to_regclass('public.landing_page_ads') IS NOT NULL
     AND (SELECT count(*) FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'landing_page_ads' AND column_name = ANY(ARRAY['ad_id', 'landing_page_id', 'source', 'first_seen_date', 'last_seen_date'])) = 5 THEN
    EXECUTE $q$CREATE OR REPLACE VIEW analytics.landing_page_ads AS SELECT ad_id, landing_page_id, source, first_seen_date, last_seen_date FROM public.landing_page_ads$q$;
    EXECUTE $q$COMMENT ON VIEW analytics.landing_page_ads IS 'Which Meta ad sends people to which landing page, and how that link was found. Grain: one row per (ad, landing page) (ad_id + landing_page_id is the primary key). Source: etl/landing_pages.py: the Meta ad creative, the Page post an ad reuses, or the ad preview. Refresh: each run of python -m etl.landing_pages (new ads with spend only; --refresh-ads looks at all again). Coverage: ads with spend in the 30 days before each run, from the first run onward. Caveats: An ad whose creative lists several links has one row per page. Its spend counts in full under each page in landing_page_top_ads, so spend must not be added across pages. | ad_id is not a foreign key to meta_ads because ads can be deleted in Meta.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.landing_page_ads.ad_id IS 'Meta ad id.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.landing_page_ads.landing_page_id IS 'landing_pages.id (foreign key).'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.landing_page_ads.source IS 'How the link was found: ''creative'' (the ad''s own creative), ''post'' (the Page post the ad reuses), or ''preview'' (the ad preview).'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.landing_page_ads.first_seen_date IS 'Date the ad was first found with this link.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.landing_page_ads.last_seen_date IS 'Date the link was last confirmed.'$q$;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'analytics_reader') THEN
      EXECUTE 'GRANT SELECT ON analytics.landing_page_ads TO analytics_reader';
    END IF;
  END IF;
END
$v$;

-- landing_page_ad_checks
DO $v$
BEGIN
  IF to_regclass('public.landing_page_ad_checks') IS NOT NULL
     AND (SELECT count(*) FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'landing_page_ad_checks' AND column_name = ANY(ARRAY['ad_id', 'checked_at', 'result', 'detail'])) = 4 THEN
    EXECUTE $q$CREATE OR REPLACE VIEW analytics.landing_page_ad_checks AS SELECT ad_id, checked_at, result, detail FROM public.landing_page_ad_checks$q$;
    EXECUTE $q$COMMENT ON VIEW analytics.landing_page_ad_checks IS 'Ads that etl/landing_pages.py has already looked at, so an ad with no readable link is not asked about every night. Grain: one row per ad (ad_id is the primary key). Source: etl/landing_pages.py. Refresh: each run of python -m etl.landing_pages. Coverage: ads with spend in the window of the runs. Caveats: An ad with result no_link or error is looked at again 14 days after checked_at.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.landing_page_ad_checks.ad_id IS 'Meta ad id (primary key).'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.landing_page_ad_checks.checked_at IS 'When the ad was last looked at.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.landing_page_ad_checks.result IS '''linked'' (a landing page was found), ''no_link'' (none could be read) or ''error'' (Meta returned an error).'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.landing_page_ad_checks.detail IS 'Short reason for no_link or error; NULL when linked.'$q$;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'analytics_reader') THEN
      EXECUTE 'GRANT SELECT ON analytics.landing_page_ad_checks TO analytics_reader';
    END IF;
  END IF;
END
$v$;

-- landing_page_top_ads
DO $v$
BEGIN
  IF to_regclass('public.landing_page_top_ads') IS NOT NULL
     AND (SELECT count(*) FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'landing_page_top_ads' AND column_name = ANY(ARRAY['landing_page_id', 'url', 'window_days', 'ad_id', 'ad_name', 'campaign_id', 'spend', 'purchases', 'revenue', 'roas', 'rank_by_spend'])) = 11 THEN
    EXECUTE $q$CREATE OR REPLACE VIEW analytics.landing_page_top_ads AS SELECT landing_page_id, url, window_days, ad_id, ad_name, campaign_id, spend, purchases, revenue, roas, rank_by_spend FROM public.landing_page_top_ads$q$;
    EXECUTE $q$COMMENT ON VIEW analytics.landing_page_top_ads IS 'The ads that drive each landing page, ranked by spend over the last 7 and the last 30 days, with Meta-reported purchases, revenue and ROAS (revenue divided by spend). Grain: one row per (landing page, window_days, ad): window_days is 7 or 30; only ads with spend in the window appear. Source: public.landing_pages, public.landing_page_ads, public.ad_daily (Meta-reported) and public.meta_ads (name). Refresh: live view; the ad to page map moves with each run of python -m etl.landing_pages, spend with the nightly Meta pull. Coverage: ads mapped to a page by the landing page job. Caveats: Purchases and revenue are what Meta reports (all customers), not Upstack new-customer numbers; join analytics.meta_ad_daily on ad_id when you need those. | An ad that lists several links appears under each page with its full spend: do not add spend across pages. | The window is the last N Pacific calendar days including today, which is a partial day.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.landing_page_top_ads.landing_page_id IS 'landing_pages.id.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.landing_page_top_ads.url IS 'Normalised page address.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.landing_page_top_ads.window_days IS '7 or 30.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.landing_page_top_ads.ad_id IS 'Meta ad id.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.landing_page_top_ads.ad_name IS 'Ad name from meta_ads; NULL when the ad is not there.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.landing_page_top_ads.campaign_id IS 'Campaign id from meta_ads; NULL when the ad is not there.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.landing_page_top_ads.spend IS 'Spend in the window, dollars (Meta).'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.landing_page_top_ads.purchases IS 'Meta-reported purchases in the window.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.landing_page_top_ads.revenue IS 'Meta-reported purchase revenue in the window, dollars.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.landing_page_top_ads.roas IS 'revenue divided by spend, 3 decimals.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.landing_page_top_ads.rank_by_spend IS '1 = biggest spender for this page and window.'$q$;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'analytics_reader') THEN
      EXECUTE 'GRANT SELECT ON analytics.landing_page_top_ads TO analytics_reader';
    END IF;
  END IF;
END
$v$;

-- shopify_orders
DO $v$
BEGIN
  IF to_regclass('public.shopify_orders') IS NOT NULL
     AND (SELECT count(*) FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'shopify_orders' AND column_name = ANY(ARRAY['order_id', 'order_number', 'processed_at', 'order_date', 'is_new_customer', 'currency', 'subtotal_price', 'total_discounts', 'shipping_price', 'total_tax', 'total_price', 'current_total_price', 'refunded_amount', 'financial_status', 'fulfillment_status', 'source_name', 'app_id', 'discount_codes', 'ship_country', 'ship_region', 'cancelled_at', 'is_cancelled', 'is_test', 'is_rebill', 'is_trybe_gift', 'line_count', 'loaded_at'])) = 27 THEN
    EXECUTE $q$CREATE OR REPLACE VIEW analytics.shopify_orders AS SELECT order_id, order_number, processed_at, order_date, is_new_customer, currency, subtotal_price, total_discounts, shipping_price, total_tax, total_price, current_total_price, refunded_amount, financial_status, fulfillment_status, source_name, app_id, discount_codes, ship_country, ship_region, cancelled_at, is_cancelled, is_test, is_rebill, is_trybe_gift, line_count, loaded_at FROM public.shopify_orders$q$;
    EXECUTE $q$COMMENT ON VIEW analytics.shopify_orders IS 'Order-level Shopify orders of the last 120 days (rolling), one row per order, with money, status, source, discount codes and ship-to country / region. The place to answer order-level questions (refunds, rebills, gifts, discounts) that the daily roll-ups hide. Grain: one row per Shopify order (order_id is the primary key). Source: ExpandFi warehouse, merchant 1452, table shopify_orders (read-only), loaded by python -m etl.shopify_orders (etl/shopify_orders.py); fields come from the order''s raw Shopify JSON. Refresh: nightly (feeds service 21:00 UTC): the trailing 14 days are re-pulled every run, the whole 120 days every Sunday (Pacific) and on the first load; orders older than 120 days are deleted. Coverage: orders processed in the last 120 Pacific days, including today so far; the warehouse trails Shopify by minutes to hours. Caveats: No personal data is stored: no email, name, address or phone. customer_id is the Shopify customer id (NULL when the order has none); the ship-to is country and region only. | order_date is the Pacific day of processed_at. The warehouse''s own created_at is the sync time of a September backfill and is NOT used. | is_new_customer = this is the customer''s first Shopify order EVER (customer key = Shopify customer id, else the lower-cased email, as in the new-customer revenue feed), judged over the whole warehouse history, not only the 120 days. | refunded_amount = total_price - current_total_price (never below 0): Shopify''s current total already nets refunds and order edits, and the warehouse''s refund transactions are empty for many refunds. It is a net-down amount, not a refund ledger. | Orders restated after they fall out of the trailing 14 days are picked up by the Sunday full 120-day pull, so a late refund can lag up to a week. | is_rebill = source_name is subscription_contract_checkout_one. is_trybe_gift = the order carries the tag trybe (the $0 gift orders). Gift, test and cancelled orders are kept and flagged, never dropped; filter on the flags. | Revenue by product is NOT the sum of total_price by product: use shopify_order_lines.line_net for a split.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.shopify_orders.order_id IS 'Shopify order id (primary key). Not the ExpandFi row id.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.shopify_orders.order_number IS 'Human-readable order number as the store shows it.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.shopify_orders.processed_at IS 'When the order was processed (UTC instant).'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.shopify_orders.order_date IS 'Pacific calendar day of processed_at; the day to group by.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.shopify_orders.is_new_customer IS 'True when this is the customer''s first Shopify order ever.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.shopify_orders.currency IS 'Order currency code (shop currency).'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.shopify_orders.subtotal_price IS 'Order subtotal after discounts, before shipping and tax.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.shopify_orders.total_discounts IS 'Sum of all discounts on the order.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.shopify_orders.shipping_price IS 'Shipping charged (shop money).'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.shopify_orders.total_tax IS 'Tax charged.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.shopify_orders.total_price IS 'Order total at checkout: subtotal + shipping + tax.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.shopify_orders.current_total_price IS 'Order total after refunds and edits, as Shopify reports it now.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.shopify_orders.refunded_amount IS 'total_price minus current_total_price, never below 0 (see the caveat).'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.shopify_orders.financial_status IS 'Shopify financial status (paid, refunded, partially_refunded, voided, ...).'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.shopify_orders.fulfillment_status IS 'Shopify fulfillment status; NULL = unfulfilled.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.shopify_orders.source_name IS 'Shopify source name (web, subscription_contract_checkout_one for rebills, tiktok, or an app id).'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.shopify_orders.app_id IS 'Shopify app id that created the order; NULL when none.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.shopify_orders.discount_codes IS 'Discount codes used, comma separated; NULL when none.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.shopify_orders.ship_country IS 'Ship-to ISO country code; NULL when the order has no shipping address.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.shopify_orders.ship_region IS 'Ship-to province / state code; NULL when none.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.shopify_orders.cancelled_at IS 'When the order was cancelled; NULL when it was not.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.shopify_orders.is_cancelled IS 'True when cancelled_at is set.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.shopify_orders.is_test IS 'True for a Shopify test order.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.shopify_orders.is_rebill IS 'True for a subscription rebill (source_name subscription_contract_checkout_one).'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.shopify_orders.is_trybe_gift IS 'True when the order has the tag trybe (free sample / gift orders).'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.shopify_orders.line_count IS 'Number of line items stored in shopify_order_lines for this order.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.shopify_orders.loaded_at IS 'When this row was last written by the loader.'$q$;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'analytics_reader') THEN
      EXECUTE 'GRANT SELECT ON analytics.shopify_orders TO analytics_reader';
    END IF;
  END IF;
END
$v$;

-- shopify_order_lines
DO $v$
BEGIN
  IF to_regclass('public.shopify_order_lines') IS NOT NULL
     AND (SELECT count(*) FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'shopify_order_lines' AND column_name = ANY(ARRAY['order_id', 'line_no', 'sku', 'product_name', 'product', 'quantity', 'price', 'line_discount', 'line_net'])) = 9 THEN
    EXECUTE $q$CREATE OR REPLACE VIEW analytics.shopify_order_lines AS SELECT order_id, line_no, sku, product_name, product, quantity, price, line_discount, line_net FROM public.shopify_order_lines$q$;
    EXECUTE $q$COMMENT ON VIEW analytics.shopify_order_lines IS 'Line items of shopify_orders: one row per product line with SKU, product bucket, quantity, price and discount. Grain: one row per order line (order_id, line_no is the primary key; line_no counts from 1 in the order''s own line order). Source: the line_items array of the same ExpandFi shopify_orders rows, loaded together with shopify_orders by python -m etl.shopify_orders. Refresh: with shopify_orders (nightly 21:00 UTC); a re-pulled order has its lines replaced, an order that leaves the 120-day window takes its lines with it. Coverage: the same orders as shopify_orders. Caveats: product is mapped from the SKU prefix: LV-VBULL = VitaBull, LV-BLC180 and DPCD1-GN180001 = BeefLiver, DPCD1-GN187001 = Boneflex, LV-VBELLE = VitaBelle, DPCD1-GN179001 = VitaFlow, anything else = Other. | line_net = max(price x quantity - line_discount, 0); the order''s total_price also carries shipping and tax and order-level discounts, so the lines do not add up to it.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.shopify_order_lines.order_id IS 'Shopify order id; foreign key to shopify_orders.order_id (cascade delete).'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.shopify_order_lines.line_no IS 'Position of the line in the order, from 1.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.shopify_order_lines.sku IS 'SKU as sold; NULL when the line has none.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.shopify_order_lines.product_name IS 'Line title as it shows on the order.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.shopify_order_lines.product IS 'Product bucket mapped from the SKU (VitaBull, BeefLiver, Boneflex, VitaBelle, VitaFlow, Other).'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.shopify_order_lines.quantity IS 'Units on the line.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.shopify_order_lines.price IS 'Unit price before discount.'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.shopify_order_lines.line_discount IS 'Total discount on the line (all units).'$q$;
    EXECUTE $q$COMMENT ON COLUMN analytics.shopify_order_lines.line_net IS 'price x quantity minus line_discount, never below 0.'$q$;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'analytics_reader') THEN
      EXECUTE 'GRANT SELECT ON analytics.shopify_order_lines TO analytics_reader';
    END IF;
  END IF;
END
$v$;
