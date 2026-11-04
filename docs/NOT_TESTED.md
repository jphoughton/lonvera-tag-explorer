# Not tested: ICPs and pain points with no ads or no spend

Product page (Tags view, product level): a line under the title counts what has not been tested, a **Not tested only** switch (`untested=1` in the URL) replaces the tree
with the flat list, and ICP / pain-point rows in the tree carry a badge. `GET /api/products/<slug>/coverage` (same window / stage / source params as every metrics endpoint)
serves all of it.

| State | Meaning | Depends on |
|---|---|---|
| No ads | no creative in the tracker is tagged to the line (any status, any date) | tracker only: never changes with the date range, stage filter, source or "Active only" |
| Ads, but $0 spend | creatives are tagged but spent nothing | the selected window, stages and source (Meta or Upstack) |
| Added after tagging | the line was added to the Communication Overview after the ads were tagged, so it reads 0 by construction | `taxonomy_lines.added_after_tagging` |

- The comparison skeleton is the Communication Overview (`taxonomy_lines`), so lines with no ads show up. Only line-mapped products have one; tracker-tag products answer `available: false`.
- Pain-point rows are the tree's rows: lines that share cluster + ICP + pain point and differ only by emotional trigger (Boneflex BF36 / BF38) are one row and count once.
- An ICP has no ads when none of its pain points has any. Counts are over pain points ("10 of 66 not tested") and ICPs ("3 of 28").
- `added_after_tagging`: migration 009 flags Beef Liver B64 / B65 / B66 (ICP GLP-1 Hair Shedder); `etl.load_taxonomy` flags any line it inserts for a product that already has line-map tags.
- Caveat shown on the page: each ad carries exactly one line from the tagging (blind re-tag agreement on the exact line is roughly 73 to 82% by product), so a line showing none may have ads sitting on a neighbouring line.
