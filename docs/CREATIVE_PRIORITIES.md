# Creative priorities (`/priorities`, `python -m etl.creative_priorities`)

A weekly one-sheeter: per product (Vita Bull, Beef Liver, Boneflex) which **Market Cluster -> ICP -> Pain Point -> Story** lines to produce ads for next. Every row reads as a brief: the four tags first (full text, each with a one-sentence plain description built from the Communication Overview text), then a **status chip**, a then-vs-now line, the number of new concepts, the numbers, what competitors run, the reason and hooks. A legend at the top says what each tag is.
Sections per product: **Iterate** (WORKING NOW / RISING), **Revive or rework** (FADING / WAS WORKING, with the old winners' hooks and a suggested approach), **New and untested clusters**, **Competitor angles we have not made**, **Stop or deprioritise** (NOT WORKING). It is prioritisation ("consistent with the numbers"), not a causal claim.

## Status of every line (rates, same Meta-reported basis)
Index = the line's shrunk ROAS / its product's ROAS in the same window. Windows: lifetime (`ad_metrics`), last 30 days, and "then" = days 31 to 90 before the last day of data (`family_daily`). Constants at the top of `etl/creative_priorities.py`.
* UNTESTED / UNDER-TESTED: under $1,000 spend and at most 1 ad that reached $100. TOO EARLY: some spend but under $5,000, 40 purchases or 4 tested ads (not judged).
* WORKING NOW: 30-day index >= 1.0 and lifetime index >= 1.05 (or then >= 1.05 and now >= 1.05). RISING: 30-day index >= 1.05 and >= 1.15x the then index, or well above its lifetime.
* FADING / WAS WORKING: strong earlier (lifetime index >= 1.05 or then >= 1.05) and now below 0.90 or at most 0.85x its then index, judged only with >= $250 spend in 30 days; or idle (no spend in 30 days) with a lifetime index >= 1.05 (a retired winner). Rows say how much ROAS moved and the last rolling 4-week window in which the line was at or above its product's average.
* NOT WORKING: enough spend, lifetime index under 0.95 and 30-day index under 0.90 (or idle). AVERAGE: everything in between.
Revive rows get a suggested approach worded as "consistent with": few new ads against the line's share of spend (fatigue / no fresh iterations), new ads also below average (the whole line weakening), or mixed; plus the old winners' hooks.

## Where it lives
* Page `/priorities` (top nav **Creative priorities**): week selector, **Download CSV** (`GET /api/priorities.csv?week=`; first columns market_cluster, icp, pain_point, story, then their descriptions, then status), **Print / save as PDF** (print CSS: landscape, one product per page, controls hidden). No server-side PDF.
* API `GET /api/priorities?week=YYYY-MM-DD` (any day: the Sunday on or before it; default the latest) returns the snapshot, the stored weeks, the tag legend, the competitor as-of date and the brands whose last pull failed.
* Table `creative_priorities_snapshots` (migration 195): one row per week, `week_start` = the Sunday (Pacific), `rows` / `thresholds` / `baselines` / `stats` as JSON. Re-running a week replaces it.
* Job: the nightly etl (`scripts/deploy/run_etl.sh`) runs, on Pacific-time Sundays only and non-fatally, `python -m etl.creative_priorities --if-sunday`, after `etl.amazon_mtd` and (when it exists) the competitor step, before `etl.auto_tag`.
* By hand: `python -m etl.creative_priorities [--week YYYY-MM-DD] [--dry-run] [--out DIR] [--competitors-file FILE]`. `--dry-run` opens a read-only session and stores nothing; `--out` writes `priorities_<week>.csv`, `.md`, `.json`.

## Competitor signal ("what others are running")
Read-only input: the view `competitor_concepts` (written by the separate competitor job, which pulls the Meta Ad Library weekly and maps concepts to our taxonomy; this module neither scrapes nor stores competitor data). Contract columns: week_start, brand, concept_key, first_ad_start_date, days_running, variant_count, format, hook, primary_text_excerpt, mapped_product, line_code, cluster, icp, pain_point, confidence, is_new_angle, new_angle_label, as_of, is_stale. The snapshot reads the latest `week_start`; the Sunday step runs after the competitor step.
* If the view is absent, lacks a contract column, is empty, or errors, the signal and the competitor section are hidden (nothing breaks); the sheet then says no competitor ads are loaded.
* Each row shows "N competitors, N concepts, N running 60+ days (brands)". Untested lines that competitors run rank up in **New**; concepts that map to no line of ours (is_new_angle / no line) form **Competitor angles we have not made**. The sheet shows the as-of date and any stale brands (`is_stale`: the last pull failed, last good data kept).
* Running a long time is a signal, **not** proven performance; the sheet says so.
* Dry run with a file instead: `--dry-run --competitors-file FILE [--as-of DATE]` (a competitor CSV, or a .json of normalised rows).

## Proposed new market clusters (drafts)
The sheet is not limited to existing lines. `etl/creative_priorities_proposals.json` holds drafts in the Communication Overview shape (product, market cluster, ICP, pain point, a story of 15 words or fewer in the existing voice, one-line descriptions, flag **NEW CLUSTER** or **NEW LINE inside <cluster>**, adjacent lines of ours as evidence, and match terms for competitor evidence). Seeded from the competitor pull's NEW lines and the ten angles Lonvera is not running (men on GLP-1, women's cycle and hormones, chest fat as cortisol / estrogen, "not beer, a tired liver" for men 55+, wild vs farmed, bone marrow for menopause joints, premature grays / copper, postpartum and kids' nutrition, stack consolidation, Spanish-language joints). Edit the file to add or retire drafts; a draft whose ICP already exists on the sheet for that product drops out by itself, so approving one (adding it to the Communication Overview) removes it.
They appear as status PROPOSED (draft), top 5 per product, 1 concept each, ranked 50% by the adjacent lines' results (economics x size, working lines only) and 50% by competitor activity (long-running concepts and brands matching the draft). Evidence is competitor activity and our adjacent results only: **no performance numbers are claimed for a proposal.** Drafts are for Josh's approval; they are stored in the weekly snapshot and the CSV (section `proposal`) like every other row.

## Iterate: who qualifies and how it is ranked (all constants at the top of `etl/creative_priorities.py`)
Qualifies when its status is WORKING NOW or RISING (see above; this already requires spend >= $5,000, >= 40 purchases and >= 4 ads that reached $100, a shrunk ROAS index so luck on a small sample cannot qualify, and a 30-day index at or above the product's).

Score (0 to 100) = 35% economics + 20% hit rate + 20% room to iterate + 15% momentum + 10% size, times (1 - 0.35 x share of the line's spend on weak tags):
* economics: blended index (half since-launch, half last 30 days) mapped 0.95 -> 0, 1.40 -> 1;
* hit rate: shrunk share of mature ads (>= 14 days old, judged) that reached $1,000, as an index to the product's, mapped 0.75 -> 0, 2.0 -> 1;
* room: 1 - (line's share of the product's new ads in 30 days / line's share of the product's spend), floored at 0: lots of spend, few recent launches = room;
* momentum: 0.5 + (last-30-day ROAS / prior-30-day ROAS - 1), clamped, 0.5 when either window is under $1,000;
* size: log scale from $5,000 (0) to $25,000 (1).
Top 8 per product. **Concepts** = 10 per product (4 more for Revive, 6 for New: 20 in all) split by score with largest remainder, 1 to 5 per row.

## New: who qualifies and how it is ranked
A taxonomy line with status UNTESTED / UNDER-TESTED and at most 3 tagged ads in the tracker (more means it is already in the pipeline). Lines added to the sheet after the ads were tagged (`added_after_tagging`) are skipped: they read 0 ads by construction.
Score = 50% adjacent winner + 20% cross-product + 20% Josh's cluster order + 10% has a story; with competitor ads loaded: 40% adjacent + 15% cross-product + 25% competitors (long-running concepts and brands) + 12% cluster order + 8% story. Adjacent = a *proven* line (>= $5,000, index >= 1.05) on the same product with the same ICP (weight 1.0) or only the same cluster (0.7); cross-product = a proven line on another product with the same pain point (1.0) or cluster name (0.6); each valued by its economics x size x (1 - weak-tag penalty). Lines with no evidence at all are shown only to fill a product to 3 rows, flagged "no performance evidence". Top 6 per product, 6 concepts split 1 to 2 per row. No numbers are invented: an untested line shows no ROAS.
**Hooks** for a new line are copied only from the same product's adjacent winner (another product's hooks do not transfer); for an iterate line they are the line's own best ads (>= $1,000 spend, ROAS at or above the product's, by revenue above that bar, duplicates collapsed).

## Confidence and flags
`thin sample` (< $10,000 or < 100 purchases), `weak tags` (> 35% of the line's spend on low-confidence, copy-only or auto tags; auto tags are `tagger auto-...`), `few ads` (< 8 judged), `Upstack new-customer ROAS below product average` (index < 0.85 on >= $3,000), `dormant`, `fading vs prior 30 days` (< 75%). Confidence: low with 2+ risk flags, high with none and >= $25,000, else medium. Each iterate row also shows ROAS on strong tags only.

## Known limits
* Meta-reported ROAS runs above actual new-customer ROAS; the Upstack figure beside it is the cross-check.
* Offer / sale lines (Offer / Price-Led, "50% off" ads) carry generic copy, so their ICP tags are often low confidence: they top the list on economics but with weak tags (penalised and flagged).
* "New" is judged on the tag, not on a text search of ad copy.
* Recent ads (< 14 days) are left out of the hit rate; mass relaunch days count as launches.
