-- 282: ad copy on a ticket. One jsonb column keyed by variation label (Execution A, Hook 2 ...): each value holds primary_texts and headlines for the launch buyer to copy into Ads Manager.
-- Additive and idempotent. Kept apart from variations so a vendor or partner rewriting the variations list can never drop it.
ALTER TABLE creative_priorities_assignments ADD COLUMN IF NOT EXISTS ad_copy jsonb NOT NULL DEFAULT '{}'::jsonb;

COMMENT ON COLUMN public.creative_priorities_assignments.ad_copy IS 'Ad copy per variation: {"<variation label>": {"primary_texts": [up to 5 strings, 1 to 2000 characters], "headlines": [up to 5 strings, 1 to 255 characters]}}. The key is the variation label as written on the ticket (matched without regard to case when written). Written only through PUT /api/priorities/assignments/<id>/copy (or the partner route); every write is a copy_changed row in the ticket history. Internal: vendor link pages never see it.';
