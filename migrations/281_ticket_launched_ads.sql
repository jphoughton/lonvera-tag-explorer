-- 281: the live Meta ads of a ticket. variations[].ad_id (inside the existing variations jsonb, no column) holds the Meta ad behind each variation; this column holds the ticket's own list of launched
-- Meta ad IDs (concept level: tickets with no variations, and ads launched outside the listed variations). Additive and idempotent; internal only (never sent to a vendor or partner).
ALTER TABLE creative_priorities_assignments ADD COLUMN IF NOT EXISTS launched_ad_ids jsonb NOT NULL DEFAULT '[]'::jsonb;

COMMENT ON COLUMN public.creative_priorities_assignments.launched_ad_ids IS 'Meta ad IDs (digit strings, up to 200, no duplicates, order kept) of the live ads for the concept as a whole, entered by an app user on the ticket. The per-variation ad is variations[].ad_id. Internal only: vendor link pages and partner keys never see it. Matched read-only against meta_ads / ad_daily for name, status, spend and return.';
