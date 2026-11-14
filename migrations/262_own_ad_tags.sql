-- 262: learning tags and transcripts for EVERY Lonvera ad family, so our ads and the competitors' compare 1:1. Filled by `python -m etl.own_ad_tags`.
--   ad_learning_tags  one row per Lonvera ad FAMILY (family_key, the unit used everywhere): hook type, offer type, device, call to action, hook text, why it works, what was seen / said.
--                     Same closed lists as competitor_ad_tags (etl.competitor_tags HOOK_TYPES / OFFER_TYPES / DEVICES). Style, fidelity, proof mechanism and emotion are NOT re-tagged here: the
--                     Creative Tracker stays the source of truth for those, our cluster / ICP / pain point / trigger come from the existing tag maps.
--   ad_transcript     one row per ad family: what is said in the video (faster-whisper on the real audio from earlier tagging cycles, or on the video fetched by etl.own_ad_tags).
-- creative_ad_transcripts (migration 249) stays as it is: it is per tracker concept_id of a Production card, a different grain.
CREATE TABLE IF NOT EXISTS ad_transcript (
  family_key  text        PRIMARY KEY,
  transcript  text        NOT NULL,
  language    text,
  duration_s  numeric,
  source      text        NOT NULL CHECK (source IN ('cycle_whisper', 'whisper', 'tracker_copy')),
  model       text,
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ad_learning_tags (
  family_key  text        PRIMARY KEY,
  tag_version text        NOT NULL,
  method      text        NOT NULL CHECK (method IN ('vision_transcript', 'vision', 'text')),
  confidence  text        CHECK (confidence IN ('high', 'medium', 'low')),
  hook_type   text,
  offer_type  text,
  device      text,
  cta         text,
  hook_text   text,
  why_it_works text,
  seen_on     text,
  said_on     text,
  model       text        NOT NULL,
  tagged_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ad_learning_tags_version ON ad_learning_tags (tag_version);

-- comments (data dictionary): copied from docs/schema/descriptions.py via scripts/gen_schema_doc.py --comments all (ad_transcript and ad_learning_tags lines only)
COMMENT ON TABLE public.ad_transcript IS 'What is said in each Lonvera video ad family, from the real audio, so every ad can be tagged by what it says as well as what it shows. Grain: one row per ad family (family_key = tracker family_key, or the Meta family key for a Meta ad with no tracker row). Source: etl.own_ad_tags: transcripts already made by earlier tagging cycles (faster-whisper base on the real audio of the Vita Bull, Beef Liver and Boneflex cycles, imported once with --import-transcripts) and, for videos without one, faster-whisper on the video fetched in spend order. Refresh: by hand: python -m etl.own_ad_tags (a family that already has a transcript is never transcribed again). Coverage: from the first import after migration 262. Caveats: source says how it was made: ''cycle_whisper'' (earlier cycle, real audio), ''whisper'' (this job), ''tracker_copy'' (the Creative Tracker''s typed transcript; the tracker text is sometimes from a different video than the one running, so it is a fallback only). | transcript ''[no speech]'' = a silent or music-only video. Audio over 3 minutes is skipped, not cut; no row = not transcribed (image ad, download failed, or not reached yet). | creative_ad_transcripts (migration 249) is a separate table at concept_id grain for Production cards.';
COMMENT ON COLUMN public.ad_transcript.family_key IS 'Ad family (primary key); same key as ad_learning_tags and creative_tracker.family_key.';
COMMENT ON COLUMN public.ad_transcript.transcript IS 'The words spoken, as heard; ''[no speech]'' for a silent video.';
COMMENT ON COLUMN public.ad_transcript.language IS 'Language the speech was recognised as.';
COMMENT ON COLUMN public.ad_transcript.duration_s IS 'Audio length in seconds.';
COMMENT ON COLUMN public.ad_transcript.source IS 'cycle_whisper | whisper | tracker_copy.';
COMMENT ON COLUMN public.ad_transcript.model IS 'Transcription model, for example faster-whisper-base-int8.';
COMMENT ON COLUMN public.ad_transcript.created_at IS 'When the transcript was stored.';
COMMENT ON TABLE public.ad_learning_tags IS 'Learning tags of every Lonvera ad family, made the same way as the competitor ad tags (competitor_ad_tags) so charts can compare our ads and the competitors'' 1:1: hook type, offer type, device, call to action, hook text, why it works, what was seen and said. Grain: one row per ad family (family_key; count families, not tracker rows or ad_ids). Source: etl.own_ad_tags: claude -p (Sonnet, Read tool only) on batches of 6 families, looking at the stored thumbnail / video frames plus the transcript, the tracker copy and the hook line; values validated against the same closed lists as competitor_ad_tags. Refresh: by hand: python -m etl.own_ad_tags (highest spend first; families already tagged at the current tag_version are skipped, --retag replaces the row). Coverage: from the first run after migration 262; every ad family with Meta spend since 2026-05-06 plus the tracker families. Caveats: Style, fidelity, proof mechanism and emotion are NOT here: the Creative Tracker tags are the source of truth for those, and cluster / ICP / pain point / trigger come from ad_tags (line map). | method: ''vision_transcript'' = pictures and the real audio transcript, ''vision'' = pictures and copy only (image ad, or a video whose audio could not be had), ''text'' = copy only (no picture); a text-only tag is capped at medium confidence. | A tag is the model''s reading of the ad, quoted not invented; hook_text / seen_on / said_on can be a short phrase or ''nothing readable'' / ''no speech''.';
COMMENT ON COLUMN public.ad_learning_tags.family_key IS 'Ad family (primary key).';
COMMENT ON COLUMN public.ad_learning_tags.tag_version IS 'Run version, for example owntags-v1-2026-11-13; a newer version re-tags.';
COMMENT ON COLUMN public.ad_learning_tags.method IS 'vision_transcript | vision | text.';
COMMENT ON COLUMN public.ad_learning_tags.confidence IS 'high | medium | low.';
COMMENT ON COLUMN public.ad_learning_tags.hook_type IS 'question | stat or number | story | call-out | comparison | authority or doctor | myth-bust | curiosity | social proof | offer-led | other.';
COMMENT ON COLUMN public.ad_learning_tags.offer_type IS 'discount | bundle or multi-pack | free shipping | guarantee | free gift | subscription | none shown | other.';
COMMENT ON COLUMN public.ad_learning_tags.device IS 'testimonial | listicle or ranked list | before and after | founder or brand story | doctor or expert | podcast clip | demo or unboxing | news-style | comparison chart | text-on-image | other.';
COMMENT ON COLUMN public.ad_learning_tags.cta IS 'The call to action, 8 words or fewer.';
COMMENT ON COLUMN public.ad_learning_tags.hook_text IS 'The first line seen or said, quoted, 20 words or fewer.';
COMMENT ON COLUMN public.ad_learning_tags.why_it_works IS 'One plain sentence on what the ad does well.';
COMMENT ON COLUMN public.ad_learning_tags.seen_on IS 'What is on screen, 25 words or fewer.';
COMMENT ON COLUMN public.ad_learning_tags.said_on IS 'What is said, 25 words or fewer, or ''no speech''.';
COMMENT ON COLUMN public.ad_learning_tags.model IS 'Model that tagged the ad.';
COMMENT ON COLUMN public.ad_learning_tags.tagged_at IS 'When the tag was written.';
