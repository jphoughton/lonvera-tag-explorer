# STATUS_E — Beef Liver line-map load (Agent E, 2026-11-03/04)

Loaded into `tagexp_dev` from `c2026-11-04-bl-linemap`; `products.tag_set` for beef-liver is now `linemap`. Nothing committed.

**Counts (verified in the DB and through the API on :5097, server stopped)**
- Tags: 1,441 of 1,441 BL tracker rows tagged (`linemap`, version `linemap-v2-2026-11-04`), 1,408 unique families, 0 untagged, 56 of 66 lines used; rows per line identical to the tag file. Confidence 740 medium / 443 high / 258 low. Method 598 video+Whisper / 557 image / 186 video+tracker transcript / 100 "no picture (copy only)". Re-run = identical rows (md5 equal).
- Matching: 1,441 = 1,441 by (concept_id, family) + ordinal. 27 blank-family rows: the cycle export writes the concept id into blank `Ad Family`, the tracker keeps it blank (family_key `id:...`); the loader treats both the same. 2 rows differ only in status (file "7 - Approved", tracker now "8 - Launched"). Types and sheet rows all equal.
- Taxonomy: BL lines carry the tagging catalog's codes B01-B63; the 3 lines added to the sheet after the cycle (GLP-1 Hair Shedder ICP) got B64-B66 and sit at sort_order 13-15. All 56 (cluster, icp, pain) keys in the tags map to a line (0 missing, 0 code mismatches). Vita Bull L01-L37 and Boneflex L01-L53 untouched.
- Assets: 1,311 families with a thumb, 780 video families with 4 frames (3,120 frame images), 97 families with no image (= the text-only tags), content rows for all 1,408 (849 video, 762 transcripts, 670 post links, 660 with Meta copy). creative_assets is now 315 MB (BL +146 MB); tagexp_dev 422 MB. Other products' assets untouched except one byte-identical thumb (shared family_key, see below).
- API: cluster rollup total = sum of rows (1,408 ads, $5,481,818, 80,122 purchases); every ICP and pain level sums to its parent (0 mismatches); ad detail shows seen/why/runner-up/line story/ingredients, 4 frames, thumb, transcript; /media serves them. `tests/api/test_real_data.py` 31 passed.

**Reliability (app_meta `reliability:beef-liver` / `reliability:vita-bull`)**: BL blind re-tag, 120 units (60 image + 60 video) vs the final first tags: line 82%, ICP 83%, cluster 90% (pain 93%); high 100% (n=40), medium 73% (n=70), low 70% (n=10). The cycle's own qa_compare.py prints 82.5 / 84.2 / 90.8 because it reads results/ in unsorted glob order and sometimes compares against a superseded first tag (15 QA units have 2-3 result files); I used last-file-wins, which is what tagmap_rows.json holds. VB unchanged (79/84/88, high 93, low 52). `reliability_note` now reads the product's numbers (VB falls back to its old numbers when the key is missing, others get no percentages) and also detects "copy only".

**Decide / know**
1. Codes: tags use the 63-line catalog; `catalog_live.json` renumbers B13-B66 (+3) so its codes must not be applied to this tag file. I kept the tag codes and appended B64-B66; alternative is renumbering to sheet order (then the cycle files and the app disagree).
2. BL method strings kept verbatim (UI badge regex already matches "copy only"; "thumbnail only" never fires for BL). BL `copy_only_line` is null (single vision pass).
3. Tracker data quality: 4 Beef Liver rows carry another product's name (Lonvera-BoneFlex-Static-2.5, Lonvera-VitalBull-Static-2.6, Bull-474-Hook 4, Boneflex-67_bumper_postId) and 6 family_keys exist in two products (assets/content are keyed by family_key, so they are shared).
4. BL frames are 360x640 tiles cut from the cycle's 2x2 contact sheets (VB frames are 480 wide).
5. docs/CONTRACT.md still describes the hard-coded reliability text; update it. Production: run the loaders locally against the Railway DATABASE_URL (assets ~150 MB) or dump/restore the tables.
6. Tests: tests/etl 48 passed (14 new in test_load_tags.py / test_load_tags_assets.py); tests/api 577 passed.

Files: etl/load_tags.py, etl/load_taxonomy.py, etl/load_assets.py, app/queries.py (reliability note only), tests/etl/test_load_tags.py, tests/etl/test_load_tags_assets.py.
