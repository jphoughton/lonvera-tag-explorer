# STATUS_A (ETL agent) — one line per milestone

- 2026-11-03 STAGE 1 LOADED into tagexp_dev and TIED OUT: `python -m etl.verify_tieout --snapshot-dir ~/lonvera-tag-explorer-data/snapshot-2026-11-03` -> 0 mismatches
  (headline totals, all 3 `rollups` tables, `overview_metrics`, `tag_breakout`, and all 1,464 `tag_map` rows incl. per-row spend/purchases/revenue/live/7d and tag text).
  Vita Bull: 1,464 rows, 141 live, since-launch $4,761,591.26 / 54,960 / $4,262,522.08, 7d $17,727.98 / 168, unmatched VB 7d $3,997.48 (21 families).
- One documented +1: DB `COUNT(DISTINCT family_key)` for Vita Bull = **1,435**, the sheet says 1,434. Cause: blank-family row `Lonvera-VitalBull-Static-7.1` has the same name as a real Ad Family; the sheet merges them, the contract's `id:` prefix keeps them apart. At every group level (cluster/ICP/pain) the two counts agree. See docs/QUESTIONS_A.md.
- Loaded (all in tagexp_dev): taxonomy (VB 37 lines/66 ingredient rows; Beef Liver 66/121; Boneflex 53/98), tracker_rows 3,875 (all products), ad_tags tracker 3,875 + linemap 1,464, ad_daily 83,966 (sheet grain, 2026-05-06..2026-11-02), family_daily, ad_metrics, unmatched_meta.
- Mapping mode default = `sheet`. `extended` adds 104 resolver mappings: +$6,829 since-launch spend overall (VB +$4,971, BL +$1,230, BF +$628), 7d delta $0.
- Next: unit tests (tests/etl), then Stage 2 (assets, meta_pull dry run, assets_pull, run_daily).
