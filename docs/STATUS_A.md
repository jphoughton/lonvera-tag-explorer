# STATUS_A (ETL agent) — one line per milestone

- 2026-11-03 STAGE 1 LOADED into tagexp_dev and TIED OUT: `python -m etl.verify_tieout --snapshot-dir ~/lonvera-tag-explorer-data/snapshot-2026-11-03` -> 0 mismatches
  (headline totals, all 3 `rollups` tables, `overview_metrics`, `tag_breakout`, and all 1,464 `tag_map` rows incl. per-row spend/purchases/revenue/live/7d and tag text).
  Vita Bull: 1,464 rows, 141 live, since-launch $4,761,591.26 / 54,960 / $4,262,522.08, 7d $17,727.98 / 168, unmatched VB 7d $3,997.48 (21 families).
- One documented +1: DB `COUNT(DISTINCT family_key)` for Vita Bull = **1,435**, the sheet says 1,434. Cause: blank-family row `Lonvera-VitalBull-Static-7.1` has the same name as a real Ad Family; the sheet merges them, the contract's `id:` prefix keeps them apart. At every group level (cluster/ICP/pain) the two counts agree. See docs/QUESTIONS_A.md.
- Loaded (all in tagexp_dev): taxonomy (VB 37 lines/66 ingredient rows; Beef Liver 66/121; Boneflex 53/98), tracker_rows 3,875 (all products), ad_tags tracker 3,875 + linemap 1,464, ad_daily 83,966 (sheet grain, 2026-05-06..2026-11-02), family_daily, ad_metrics, unmatched_meta.
- Mapping mode default = `sheet`. `extended` adds 104 resolver mappings: +$6,829 since-launch spend overall (VB +$4,971, BL +$1,230, BF +$628), 7d delta $0.
- Next: unit tests (tests/etl), then Stage 2 (assets, meta_pull dry run, assets_pull, run_daily).
- 2026-11-03 17:45 unit tests green (`.venv/bin/python -m pytest tests/etl`, DB tagexp_test): 25 tests (fam/Resolver/row_key ordinals/'0510' vs '510'/launch-rule branches/first-row/blank family/idempotency/stage/Meta client retry+split/run_daily fallback).
- 2026-11-03 17:48 STAGE 2: Meta dry run (last 3 days, both accounts) reconciles to the sheet: 10-31 and 11-01 diff $0.00, 11-02 diff $0.36 (Meta restatement after the sheet's 15:30 pull); per-account ad total = account total (0.00%).
- 2026-11-03 17:50 load_assets done: 998 thumbs + 154 fallback thumbs (cut from video frame 1) + 2,480 frames + family_content for 1,434 VB families (729 with Meta copy, 693 transcripts, 996 post links). ~152 MB of bytea.
- 2026-11-03 17:51 assets_pull tested on 10 families (all 10 got a thumb, source 'meta-api'); NOT run for everything.
- 2026-11-03 17:45-now: full 181-day ad-level pull (2026-05-06..2026-11-02) running in background (13 chunks x 2 accounts); log: /private/tmp/claude-501/-Users-joshhoughton-Desktop-Google-Sheets/7412bd3e-5949-4c38-8798-d2840d4101c9/scratchpad/meta_pull_full.log. Until it finishes, ad_daily is a mix of 'sheet' (family grain) and 'meta_api' (ad grain) days; family totals are unaffected.
