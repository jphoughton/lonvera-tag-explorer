-- 249: transcripts of ads, from the real audio. One row per ad (the tracker Concept ID of a variation, for example Lonvera-BoneFlex-Video-8.8-hook2), written by the transcription job
-- (lonvera_creative_key/cycles/c2026-11-13-transcribe-ready/transcribe_ready.py) for every video on a Production card that is Ready to launch, and shown on the card's ticket under the matching Hook.
-- assignment_id says which Production card the ad was made for; it is not a foreign key (a card may be deleted, the transcript belongs to the ad). Additive and idempotent.
CREATE TABLE IF NOT EXISTS creative_ad_transcripts (
  id            bigserial    PRIMARY KEY,
  concept_id    text         NOT NULL UNIQUE,
  assignment_id bigint,
  label         text         NOT NULL DEFAULT '',
  source_url    text,
  file_id       text,
  language      text,
  duration_s    numeric,
  engine        text,
  model         text,
  transcript    text         NOT NULL,
  created_at    timestamptz  NOT NULL DEFAULT now(),
  created_by    text
);
CREATE INDEX IF NOT EXISTS creative_ad_transcripts_assignment_idx ON creative_ad_transcripts (assignment_id);

-- ---------------------------------------------------------------- comments
COMMENT ON TABLE public.creative_ad_transcripts IS 'Transcripts of ads taken from the real audio: one row per ad variation (its tracker Concept ID), made for the videos on Production cards that are Ready to launch and shown on the card''s ticket under the matching Hook. Grain: one row per ad (concept_id is unique). Source: the transcription job (lonvera_creative_key/cycles/c2026-11-13-transcribe-ready/transcribe_ready.py: downloads the video from Google Drive and runs faster-whisper on this Mac). Refresh: when the job is run (on demand, or on a schedule once one is set); a transcript is written once per ad and never overwritten. Coverage: videos of Ready to launch Production cards from 2026-11-13; images and silent videos have no speech to transcribe (a silent video is stored as [no speech]). Caveats: Speech to text as heard: brand and ingredient names can be misspelled (heme iron may come out as Heam iron). | Audio is cut at 180 seconds. | assignment_id is the Production card the ad was made for; it is not a foreign key.';
COMMENT ON COLUMN public.creative_ad_transcripts.id IS 'Row number (primary key).';
COMMENT ON COLUMN public.creative_ad_transcripts.concept_id IS 'Tracker Concept ID of the ad variation, for example Lonvera-BoneFlex-Video-8.8-hook2; unique.';
COMMENT ON COLUMN public.creative_ad_transcripts.assignment_id IS 'creative_priorities_assignments.id of the Production card the ad belongs to; NULL when unknown. Not a foreign key.';
COMMENT ON COLUMN public.creative_ad_transcripts.label IS 'Short name of the variation on the card: Original, Hook 2 ...';
COMMENT ON COLUMN public.creative_ad_transcripts.source_url IS 'Link to the video file the transcript was made from (Google Drive).';
COMMENT ON COLUMN public.creative_ad_transcripts.file_id IS 'The Google Drive file id inside source_url.';
COMMENT ON COLUMN public.creative_ad_transcripts.language IS 'Language the speech was recognised as (en).';
COMMENT ON COLUMN public.creative_ad_transcripts.duration_s IS 'Length of the audio in seconds, before the 180 second cut.';
COMMENT ON COLUMN public.creative_ad_transcripts.engine IS 'Software that made it (faster-whisper).';
COMMENT ON COLUMN public.creative_ad_transcripts.model IS 'Model size used (base.en).';
COMMENT ON COLUMN public.creative_ad_transcripts.transcript IS 'The words spoken, as heard; [no speech] for a silent video.';
COMMENT ON COLUMN public.creative_ad_transcripts.created_at IS 'When the transcript was stored.';
COMMENT ON COLUMN public.creative_ad_transcripts.created_by IS 'Who or what stored it.';
