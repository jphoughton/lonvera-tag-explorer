# Questions / defaults chosen by Agent A (ETL)

1. **Vita Bull unique ads: 1,435 (DB, contract `id:` keys) vs 1,434 (sheet).** The sheet counts blank-family rows by Concept ID in the same namespace as real Ad Families;
   one blank-family row (`Lonvera-VitalBull-Static-7.1`) shares its name with a real family, so the sheet merges them. Default chosen: keep the contract's `id:` prefix
   (family_key = `id:lonvera-vitalbull-static-7.1` for that row). Effect: product-level `unique_ads` = 1,435; every cluster / ICP / pain group count matches the sheet.
   If the UI must show 1,434 exactly, the API would need to count blank-family rows without the `id:` prefix, i.e. `COUNT(DISTINCT CASE WHEN ad_family='' THEN lower(trim(concept_id)) ELSE family_key END)`.
   verify_tieout checks the sheet-semantics number (1,434) and reports the contract number as a note.
2. **`ad_tags.copy_only_line`** holds the *line code* (e.g. `L07`); the sheet's label text and the rest of the cycle's text_* fields are in `ad_tags.extra` (`text_label`, `text_confidence`, ...).
3. **Resolver `adname` rule.** The sheet's tracker_tags() looks up a column named "Ad Name" that does not exist (the tracker's column is "Name"), so the sheet's resolver never used it. The port uses "Name" (stored as `tracker_rows.meta_ad_name`); it only affects `--mapping extended`.
4. **Launch dates** given as 5-digit Sheets serials (e.g. `46329` = 2026-11-03, 10 rows of ads launched on 11-03, after data_through) are parsed as dates by the ETL; the sheet's own parser (vb_perf.py) ignores them. The tie-out still matches because those ads launched after data_through so have no daily rows (verify_tieout is the guard).
5. **Product-level `family_daily`** is keyed by tracker `family_key` only (migration 002). 17 tracker family keys appear under 2 products (e.g. `lonvera-vitalbull-static-5.4` under VitaBull and Boneflex); per rule 2 each product's first row carries the full family figures, so such a family is counted in both products (not in the Vita Bull tie-out).
