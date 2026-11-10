# Creative priorities (`/priorities`, `python -m etl.creative_priorities`)

A weekly one-sheeter: per product (Vita Bull, Beef Liver, Boneflex) which **Market Cluster -> ICP -> Pain Point -> Story** lines to produce ads for next. Two sections:
**New to produce** (lines with no ads yet, ranked by evidence the demand exists) and **Iterate on what is working** (lines whose ads beat the product's own average, where more concepts should help).
Every row reads as a brief: the four tags first (full text, each with a one-sentence plain description built from the Communication Overview text), then the number of new concepts, the numbers, the reason, and hooks.
A legend at the top says what each tag is. It is prioritisation ("consistent with the numbers"), not a causal claim.

## Where it lives
* Page `/priorities` (top nav **Creative priorities**): week selector, **Download CSV** (`GET /api/priorities.csv?week=`), **Print / save as PDF** (print CSS: landscape, one product per page, controls hidden). No server-side PDF.
* API `GET /api/priorities?week=YYYY-MM-DD` (any day: the Sunday on or before it; default the latest) returns the snapshot, the stored weeks and the tag legend.
* Table `creative_priorities_snapshots` (migration 195): one row per week, `week_start` = the Sunday (Pacific), `rows` / `thresholds` / `baselines` / `stats` as JSON. Re-running a week replaces it.
* Job: the nightly etl (`scripts/deploy/run_etl.sh`) runs `python -m etl.creative_priorities --if-sunday` after `etl.amazon_mtd` and before `etl.auto_tag`; it does nothing unless today is a Sunday in Pacific time, is non-fatal (`etl_runs` job `creative_priorities`) and takes seconds. The Sunday snapshot uses data through Saturday.
* By hand: `python -m etl.creative_priorities [--week YYYY-MM-DD] [--dry-run] [--out DIR]`. `--dry-run` opens a read-only session and stores nothing; `--out` writes `priorities_<week>.csv`, `.md`, `.json`.
* CSV columns: market_cluster, icp, pain_point, story, then the three descriptions, then product, section, rank, concepts, week, emotional trigger, ingredients, line code, confidence, flags, score, numbers, three hooks, why.

## Basis
Scored on **Meta-reported** numbers (the app's primary basis): ROAS = purchase value / spend, CPA = spend / purchases, always ratios of sums. Upstack new-customer ROAS (since 2026-05-06, `family_daily_upstack_nc`) is shown beside it and only raises a flag.
A line is the Communication Overview line a tagger chose (`ad_tags` tag_set `linemap`, current version) on the family's first tracker row. "Since launch" = `ad_metrics` (Meta lifetime); windows (last 30 days, prior 30 days, last 7) = `family_daily`.

## Iterate: who qualifies and how it is ranked (all constants at the top of `etl/creative_priorities.py`)
Qualifies when the line has: spend >= $5,000, >= 40 purchases, >= 4 ads that reached $100 (`MIN_LINE_*`); a **shrunk** since-launch ROAS index (line / product average, shrunk toward the product with $2,500 of prior spend so luck on a small sample cannot qualify) >= 1.05; and, if it spent >= $1,000 in the last 30 days, a shrunk last-30-day index >= 0.90 (otherwise it is fading and left out). Dormant lines (under $250 in 30 days) stay in, flagged as retired winners.

Score (0 to 100) = 35% economics + 20% hit rate + 20% room to iterate + 15% momentum + 10% size, times (1 - 0.35 x share of the line's spend on weak tags):
* economics: blended index (half since-launch, half last 30 days) mapped 0.95 -> 0, 1.40 -> 1;
* hit rate: shrunk share of mature ads (>= 14 days old, judged) that reached $1,000, as an index to the product's, mapped 0.75 -> 0, 2.0 -> 1;
* room: 1 - (line's share of the product's new ads in 30 days / line's share of the product's spend), floored at 0: lots of spend, few recent launches = room;
* momentum: 0.5 + (last-30-day ROAS / prior-30-day ROAS - 1), clamped, 0.5 when either window is under $1,000;
* size: log scale from $5,000 (0) to $25,000 (1).
Top 8 per product. **Concepts** = 14 per product split by score with largest remainder, 1 to 5 per row.

## New: who qualifies and how it is ranked
A taxonomy line with at most 1 ad that reached $100, under $1,000 spend, and at most 3 tagged ads in the tracker (more means it is already in the pipeline). Lines added to the sheet after the ads were tagged (`added_after_tagging`) are skipped: they read 0 ads by construction.
Score = 50% adjacent winner + 20% cross-product + 20% Josh's cluster order + 10% has a story. Adjacent = a *proven* line (>= $5,000, index >= 1.05) on the same product with the same ICP (weight 1.0) or only the same cluster (0.7); cross-product = a proven line on another product with the same pain point (1.0) or cluster name (0.6); each valued by its economics x size x (1 - weak-tag penalty). Lines with no evidence at all are shown only to fill a product to 3 rows, flagged "no performance evidence". Top 6 per product, 6 concepts split 1 to 2 per row. No numbers are invented: an untested line shows no ROAS.
**Hooks** for a new line are copied only from the same product's adjacent winner (another product's hooks do not transfer); for an iterate line they are the line's own best ads (>= $1,000 spend, ROAS at or above the product's, by revenue above that bar, duplicates collapsed).

## Confidence and flags
`thin sample` (< $10,000 or < 100 purchases), `weak tags` (> 35% of the line's spend on low-confidence, copy-only or auto tags; auto tags are `tagger auto-...`), `few ads` (< 8 judged), `Upstack new-customer ROAS below product average` (index < 0.85 on >= $3,000), `dormant`, `fading vs prior 30 days` (< 75%). Confidence: low with 2+ risk flags, high with none and >= $25,000, else medium. Each iterate row also shows ROAS on strong tags only.

## Known limits
* Meta-reported ROAS runs above actual new-customer ROAS; the Upstack figure beside it is the cross-check.
* Offer / sale lines (Offer / Price-Led, "50% off" ads) carry generic copy, so their ICP tags are often low confidence: they top the list on economics but with weak tags (penalised and flagged).
* "New" is judged on the tag, not on a text search of ad copy.
* Recent ads (< 14 days) are left out of the hit rate; mass relaunch days count as launches.
