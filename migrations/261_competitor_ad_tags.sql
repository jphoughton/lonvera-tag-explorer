-- 261: transcripts and tags of the curated competitor ads (the ones with a saved picture or video). Filled by `python -m etl.competitor_tags`; read by the /competitors page.
--   competitor_ad_transcript  one row per Meta Ad Library ID: what is said in the saved video (faster-whisper)
--   competitor_ad_tags        one row per Meta Ad Library ID: Lonvera's taxonomy (line / cluster / ICP / pain point / trigger), the Creative Tracker's style / fidelity / proof / emotion
--                             lists, and the learning tags (hook type, offer type, device, call to action, hook text, why it works, what was seen / said), made by headless Claude
--                             looking at the picture (video: cover + 4 frames) plus transcript plus copy. competitor_concept_class (text-only) is unchanged.
CREATE TABLE IF NOT EXISTS competitor_ad_transcript (
  library_id  text        PRIMARY KEY,
  transcript  text        NOT NULL,
  language    text,
  duration_s  numeric,
  model       text        NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS competitor_ad_tags (
  library_id        text        PRIMARY KEY,
  concept_key       text,
  tag_version       text        NOT NULL,
  method            text        NOT NULL CHECK (method IN ('vision_transcript', 'vision', 'text')),
  confidence        text        CHECK (confidence IN ('high', 'medium', 'low')),
  line_code         text,
  cluster           text,
  icp               text,
  pain_point        text,
  emotional_trigger text,
  is_new_angle      boolean     NOT NULL DEFAULT false,
  new_angle_label   text,
  style             text,
  fidelity          text,
  proof_mechanism   text,
  emotion           text,
  hook_type         text,
  offer_type        text,
  device            text,
  cta               text,
  hook_text         text,
  why_it_works      text,
  seen_on           text,
  said_on           text,
  model             text        NOT NULL,
  tagged_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS competitor_ad_tags_concept ON competitor_ad_tags (concept_key);

-- comments (data dictionary): copied from docs/schema/descriptions.py via scripts/gen_schema_doc.py --comments all (competitor_ad_transcript and competitor_ad_tags lines only)
COMMENT ON TABLE public.competitor_ad_transcript IS 'What is said in the saved competitor video ads: a speech-to-text transcript of the first 30 seconds of each video in competitor_ad_video, so the ads can be read and tagged, not only watched. Grain: one row per Meta Ad Library ID (joins to competitor_ads.library_id and competitor_ad_video.library_id). Source: etl.competitor_tags: faster-whisper (model base, int8, CPU, voice-activity filter) on the stored mp4 audio. Refresh: with etl.competitor_tags (by hand: python -m etl.competitor_tags; ads already transcribed are skipped, --retag redoes them). Coverage: from 2026-11-13; only videos that have a row in competitor_ad_video. Caveats: transcript is an empty string when the video has no speech (music only or silent); no row = not transcribed (faster-whisper not installed on that machine, or the transcription failed), not a silent ad. | Only the first 30 seconds (the stored copy), in Meta''s SD audio; names and product terms can be misheard.';
COMMENT ON COLUMN public.competitor_ad_transcript.library_id IS 'Meta Ad Library ID (primary key).';
COMMENT ON COLUMN public.competitor_ad_transcript.transcript IS 'Speech-to-text of the saved video; '''' = no speech.';
COMMENT ON COLUMN public.competitor_ad_transcript.language IS 'Detected language code (en ...).';
COMMENT ON COLUMN public.competitor_ad_transcript.duration_s IS 'Audio length in seconds that was read.';
COMMENT ON COLUMN public.competitor_ad_transcript.model IS 'Transcription model, for example faster-whisper-base-int8.';
COMMENT ON COLUMN public.competitor_ad_transcript.created_at IS 'When it was transcribed.';
COMMENT ON TABLE public.competitor_ad_tags IS 'Tags of each curated competitor ad (one with a saved picture or video), made by headless Claude LOOKING at the picture (video: cover plus 4 frames), the transcript and the copy: Lonvera''s own taxonomy (line, cluster, ICP, pain point, emotional trigger), the Creative Tracker''s style / fidelity / proof mechanism / emotion values (so competitor and Lonvera ads compare 1:1) and learning tags (hook type, offer type, device, call to action, hook text, why it works, what was seen and said). Grain: one row per Meta Ad Library ID (joins to competitor_ads.library_id; concept_key joins to competitor_concept_class). Source: etl.competitor_tags: claude -p (Sonnet, Read tool only) on batches of 6 ads, validated against taxonomy_lines and the distinct Creative Tracker values. Refresh: with etl.competitor_tags (python -m etl.competitor_tags; ads already tagged at the current tag_version are skipped, --retag replaces the row). Coverage: from 2026-11-13; only the curated slice that has a saved picture or video, not every ad in competitor_ads. Caveats: method says what the tagger had: vision_transcript (picture and speech), vision (picture, no speech or not transcribed) or text (no picture could be read, confidence capped at medium). The tags are the model''s reading, not a measured result: no spend or performance exists for competitor ads. | line_code is NULL for a new angle (is_new_angle, new_angle_label; cluster / icp / pain_point hold the suggestion) and for out-of-category ads (cluster NULL too). style / fidelity / proof_mechanism / emotion hold a Creative Tracker value, or ''PROPOSE: <value>'' when none fitted. | competitor_concept_class stays the text-only, per-concept classification; the two can disagree, and this table (which saw the picture) is the better read for a single ad.';
COMMENT ON COLUMN public.competitor_ad_tags.library_id IS 'Meta Ad Library ID (primary key).';
COMMENT ON COLUMN public.competitor_ad_tags.concept_key IS 'competitor_ads.concept_key of the ad.';
COMMENT ON COLUMN public.competitor_ad_tags.tag_version IS 'Prompt / list version that made the row (ctags-v1-2026-11-13).';
COMMENT ON COLUMN public.competitor_ad_tags.method IS 'vision_transcript | vision | text: what the tagger saw.';
COMMENT ON COLUMN public.competitor_ad_tags.confidence IS 'high | medium | low (the tagger''s own).';
COMMENT ON COLUMN public.competitor_ad_tags.line_code IS 'Lonvera line (L.. Vital Bull, B.. Beef Liver, BF.. Boneflex) or NULL.';
COMMENT ON COLUMN public.competitor_ad_tags.cluster IS 'Market cluster (taxonomy value, or suggested for a new angle).';
COMMENT ON COLUMN public.competitor_ad_tags.icp IS 'ICP (taxonomy value, or suggested).';
COMMENT ON COLUMN public.competitor_ad_tags.pain_point IS 'Pain point (taxonomy value, or suggested).';
COMMENT ON COLUMN public.competitor_ad_tags.emotional_trigger IS 'The line''s emotional trigger (or suggested for a new angle).';
COMMENT ON COLUMN public.competitor_ad_tags.is_new_angle IS 'True when no Lonvera line covers the angle.';
COMMENT ON COLUMN public.competitor_ad_tags.new_angle_label IS '4-10 word label of the new angle.';
COMMENT ON COLUMN public.competitor_ad_tags.style IS 'Creative Tracker Style value, or ''PROPOSE: x''.';
COMMENT ON COLUMN public.competitor_ad_tags.fidelity IS 'Creative Tracker Creative Fidelity value, or ''PROPOSE: x''.';
COMMENT ON COLUMN public.competitor_ad_tags.proof_mechanism IS 'Creative Tracker Proof Mechanism value, or ''PROPOSE: x''.';
COMMENT ON COLUMN public.competitor_ad_tags.emotion IS 'Creative Tracker Emotion value, or ''PROPOSE: x''.';
COMMENT ON COLUMN public.competitor_ad_tags.hook_type IS 'question | stat or number | story | call-out | comparison | authority or doctor | myth-bust | curiosity | social proof | offer-led | other.';
COMMENT ON COLUMN public.competitor_ad_tags.offer_type IS 'discount | bundle or multi-pack | free shipping | guarantee | free gift | subscription | none shown | other.';
COMMENT ON COLUMN public.competitor_ad_tags.device IS 'testimonial | listicle or ranked list | before and after | founder or brand story | doctor or expert | podcast clip | demo or unboxing | news-style | comparison chart | text-on-image | other.';
COMMENT ON COLUMN public.competitor_ad_tags.cta IS 'Call to action, 8 words or fewer.';
COMMENT ON COLUMN public.competitor_ad_tags.hook_text IS 'First line seen or said, quoted, 20 words or fewer.';
COMMENT ON COLUMN public.competitor_ad_tags.why_it_works IS 'One plain sentence, 25 words or fewer: what Lonvera could learn from it.';
COMMENT ON COLUMN public.competitor_ad_tags.seen_on IS 'What the tagger saw on screen (the evidence).';
COMMENT ON COLUMN public.competitor_ad_tags.said_on IS 'What was said (the evidence), or ''no speech''.';
COMMENT ON COLUMN public.competitor_ad_tags.model IS 'Model id used.';
COMMENT ON COLUMN public.competitor_ad_tags.tagged_at IS 'When the row was written.';
