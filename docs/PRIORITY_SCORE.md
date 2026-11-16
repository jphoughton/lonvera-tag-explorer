# Creative priority score

**A high score means: make more creative for this cut now.** New ads in this cut have tended to get budget and hit, the cut already carries spend that can absorb them, it is short of fresh ads for what it carries, and nothing in the pipeline already covers it.

Code: `app/priority_score.py` (pure formula), `app/priority_signals.py` (per-cut inputs from the database), backtest `analysis/priority_score_backtest.py`. Tests: `tests/api/test_priority_score_pure.py`, `tests/api/test_priority_signals.py`.

## What a cut is

Any ordered subset of market cluster, ICP (ideal customer profile), pain point, vendor and media (image / video). Product can filter the scope but is never a level the score ranks: `parse_score_group` refuses `group=product`. Ads are tagged exactly as in the By cut view (`priorities_cuts.CUT_CTE`: the Communication Overview line tag, else the tracker's own columns), one tracker family = one ad.

Every cut is scored from **its own rolled-up sums** (spend, revenue, ads), never by averaging the scores of its children. The score is a pure function of the cut's signal dict, so the same numbers give the same score at any level, and adding or splitting sibling cuts does not change a cut's score.

## Bases and windows

* Spend is Meta's. Return is **Upstack new customers** (NC: first orders, any-click 30 days). Meta-reported purchases are not used. No ratio mixes bases.
* Windows end on the as-of day (default: the last loaded day): last 7, last 30, days 31 to 60. All ratios are ratios of sums.
* Live = the ad spent on the as-of day or the day before (the By cut view's "last two loaded days").
* Launch = an ad family's first day of Meta spend (not the tracker's Launch Date, which undercounts). Ads already spending on the first day of data (2026-05-06) are treated as launched before the data starts: old, outside the hit cohort.

## Signals that are scored

| Component | Weight | Definition | Normalised to 0..1 | Why it earns its place |
|---|---|---|---|---|
| Hit rate | 0.40 | The app's **scoring** hit definition (`etl/creative_priorities.py`, `metric hit_rate`): ads 14 to 180 days old that spent $100+ ("tested"); a hit reached **$1,000** spend. Shrunk toward the cut's product-mix hit rate with 4 pretend ads (`SHRINK_ADS`), read as an index. | clamp((index - 0.75) / 1.25), the same mapping as the weekly priorities | The strongest single predictor in the backtest: cuts where past new ads hit keep producing new ads that hit. |
| Supply gap | 0.25 | 0.6 x under-launching (1 - the cut's share of new ads in 60 days / its share of 30-day spend) + 0.4 x staleness (days since the last launch / 45). Multiplied by spend / (spend + $2,500). | already 0..1 | A cut that carries spend but gets few new ads is where fresh creative has room. The evidence weight stops a $300 cut from claiming a gap. |
| Scale | 0.25 | 30-day spend on a log scale: $1,000 = 0, $50,000 = 1. | log position | New ads only matter if the cut can give them budget. |
| New-customer return | 0.10 | 30-day NC ROAS, shrunk toward what the cut's **product mix** gets (k = $2,500 of pretend spend, `SHRINK_SPEND`), as an index. | clamp((index - 0.80) / 0.50) | A guard, not a driver: the backtest found past ROAS does not predict how new ads do (see below). It keeps a money-losing cut from reading as strong, together with the cap. |
| Momentum | 0.00 | 30-day NC ROAS index / prior-30 index (both windows $1,000+). | 0.5 + (ratio - 1) | Computed and shown, weight 0: it ranked cuts slightly **backwards** in the backtest. |

**Product-mix expectation.** Products have very different return levels, and product is not a level. So each cut is compared with what its own product mix gets anyway: expected NC ROAS = sum over products of (cut's 30-day spend in the product x the product's NC ROAS) / cut spend; expected hit rate is the same, weighted by the cut's tested ads per product. When the cut has no spend, the scope's blended number is used.

**Shrinkage.** Small samples are pulled toward their prior, empirical-Bayes style: ROAS index = (revenue + 2,500 x prior ROAS) / (spend + 2,500) / prior ROAS; hit rate = (hits + 4 x prior rate) / (tested + 4). A $300 cut at 3.0x against a 0.5x mix (raw index 6.0) shrinks to index 1.54. No spend at all gives index 1.00 exactly.

## Formula

```
base  = 0.40 hit_rate + 0.25 supply_gap + 0.25 scale + 0.10 economics + 0.00 momentum          (each 0..1)
score = 100 x base x (1 - 0.5 x coverage)       coverage = clamp(open tickets / max(3, launches in last 60 days / 2))
cap   : 30-day spend >= $10,000 and NC ROAS index < 0.85  ->  score = min(score, 44)
```

**Pipeline.** Tickets that are assigned, brief sent, in production, in review or ready (not launched, not dropped) count against the cut's own launch pace. One full month of pace already in production halves the score. Untested in the backtest (no ticket history before November); it is a judgment call.

## Tiers

| Score | Tier |
|---|---|
| 55+ | Make more now |
| 45 to 54.9 | Make more |
| 35 to 44.9 | Keep steady |
| under 35 | Low priority |
| under $100 in 30 days and no tested ads | Untested (sorts after every tested cut, whatever the score) |

Floors were set on the 2026-11-14 spread (top ICP 59.6, most tested ICPs 20 to 45). A **low-confidence** cut (under $1,000 in 30 days or under 3 tested ads) never reads above Keep steady, whatever its score. Confidence: high = $5,000+ in 30 days and 8+ tested ads; medium = $1,000+ and 3+; else low.

Sort: score, then confidence, then 30-day spend, then labels A to Z.

## Why this score (printed per cut)

`drivers` lists every weighted component with its points (points add up to the score; the pipeline and the cap carry negative points) and one sentence each. `why` joins the top three plus any pipeline or cap line, e.g. for Beer Drinker on 2026-11-14:

> 17 of 35 tested ads reached $1,000 (49%) vs 26% for its product mix; $13,719 spent in the last 30 days; Carries 5% of spend but got 2% of new ads in 60 days; last new ad 8 days ago

Flags: untested; big and below its product mix (capped); one ad carries 60%+ of spend; revive candidate (under $1,000 now but old ads hit); pipeline covers the pace; 80%+ of spend on ads older than 60 days; low confidence.

## Shown, not scored

7-day spend and NC ROAS, NC CPA, live ads, ads with spend, launches in 30 / 60 days, last launch day, top-ad share, old-ad spend share, oldest and median ticket age, tagged ads, the date-window totals. Cost per click, CPM, CTR and AOV (average order value) stay in the By cut table; none of them added anything the scored signals do not already carry, and they are funnel diagnostics, not "make more" evidence.

## Backtest (2026-11-15, `analysis/priority_score_backtest.py`)

**Question.** Does a high score on day T predict that ads launched in that cut in the next 28 days do well?

* As-of days: every 7 days, 2026-07-06 to 2026-09-14 (11 days; outcomes need 56 days after T and data ends 2026-11-14). The windows overlap, so the 11 days are not independent.
* Features: `build_signals` on data up to T only, the same code the app runs; pipeline = 0.
* Outcomes: ads first spending in (T, T+28]. **Hit rate** = reached $1,000 by T+56 / reached $100. **New-ad ROAS index** = their NC revenue / spend over (T, T+56] divided by what the same products got over the same days. Only cuts with 3+ tested new ads count: about 11 cuts per day.
* rho = Spearman rank correlation, averaged over the 11 days. Top / bottom = pooled outcome of the top and bottom third of cuts.

| Level | Ranker | rho, hit | rho, ROAS index | Top-third hit | Bottom-third hit | Top-third ROAS index | Bottom-third ROAS index |
|---|---|---|---|---|---|---|---|
| ICP | **score** | 0.25 | 0.19 | 49.6% (n 224) | 38.8% (n 178) | 0.99 | 0.96 |
| ICP | 30-day spend | 0.17 | 0.07 | 46.4% | 42.7% | 0.98 | 0.98 |
| ICP | 30-day NC ROAS | 0.07 | -0.06 | 38.6% | 40.2% | 0.91 | 1.05 |
| Cluster | **score** | 0.23 | 0.42 | 43.4% | 27.3% | 0.99 | 0.86 |
| Cluster | 30-day spend | 0.27 | 0.37 | 43.2% | 34.0% | 0.98 | 0.78 |
| Cluster | 30-day NC ROAS | 0.07 | 0.23 | 37.7% | 39.8% | 0.97 | 0.95 |
| Pain point | **score** | 0.06 | -0.06 | 34.9% | 36.6% | 1.03 | 1.00 |
| Pain point | 30-day spend | 0.27 | -0.26 | 41.0% | 27.6% | 0.85 | 1.10 |
| Cluster + ICP | **score** | 0.26 | 0.18 | 48.6% | 38.4% | 0.98 | 0.97 |
| ICP + pain point | **score** | 0.44 | 0.25 | 51.1% (n 174) | 25.5% (n 153) | 1.02 | 0.95 |
| ICP + pain point | 30-day spend | 0.35 | 0.15 | 50.8% | 32.0% | 0.95 | 0.99 |
| ICP + pain point | 30-day NC ROAS | -0.02 | 0.02 | 37.2% | 40.9% | 0.87 | 0.98 |
| ICP + media | **score** | 0.15 | -0.01 | 44.0% | 29.7% | 0.96 | 0.95 |
| ICP + media | 30-day spend | -0.07 | 0.05 | 41.6% | 37.6% | 1.00 | 1.00 |

(Cluster + ICP + pain point gives the same table as ICP + pain point: ICP names do not repeat across clusters.)

Permutation test (scores shuffled within each day, 500 times): score vs hit rate p = 0.010 (ICP), 0.010 (cluster), 0.256 (pain point), 0.008 (cluster + ICP), < 0.002 (ICP + pain point), 0.086 (ICP + media).

**Verdict.**
1. **Ranking by past NC ROAS does not work.** Its correlation with how new ads do is about zero at every level, and its top third did worse than its bottom third on new-ad ROAS at 4 of 5 levels. This is why economics carries only 0.10 and momentum 0.
2. **The score beats rank-by-ROAS everywhere and edges rank-by-spend at ICP, cluster + ICP, ICP + pain point and ICP + media; it is mixed at cluster (worse on hits, better on ROAS index) and loses at pain point alone.** Averaged over five levels (ICP, cluster, pain point, ICP + pain point, ICP + media): score rho 0.22 (hit) and 0.16 (ROAS index); spend 0.20 and 0.08. Split in half: early days (07-06 to 08-10) score 0.21 vs spend 0.21 on hit; late days (08-17 to 09-14) 0.25 vs 0.19. The edge over spend is real on the ROAS index and small on hits.
3. **The weights were chosen on this same history** (first draft 0.40 economics / 0.20 hit / 0.15 gap / 0.15 scale / 0.10 momentum scored rho 0.11 hit, 0.14 ROAS index; component-by-component runs showed hit, gap and scale help, ROAS and momentum do not). The permutation p-values do not account for that choice and the 11 days overlap, so read them as optimistic.
4. **Where it fails:** pain point alone (score rho 0.06; pain points span products and ICPs, so a pain-point roll-up mixes unlike audiences: use it with ICP). At ICP + media the ROAS-index edge disappears.

**What the backtest cannot show.**
* The hit outcome is spend-based, and buyers decide spend. Part of "new ads hit here" is "buyers fund this cut". Scale and supply gap partly predict allocation, not creative quality. The new-ad ROAS index is the quality read, and every ranker's edge on it is small (the score's top-vs-bottom-third gap is 0.01 to 0.13 of index).
* Outcomes exist only where the team chose to launch. Cuts never tried have no outcome, so the score says nothing about Untested cuts.
* The pipeline factor, the loss cap and the low-confidence tier hold are judgment calls with no history to test them. The cap only changed cuts at 30-day spend >= $10,000 with index < 0.85 and moved the ROAS-index rho up slightly.
* Tags are today's tags. They describe the creative, not its results, so they do not leak outcomes, but tagging improved over time.
* Four months of ad-level data (from 2026-05-06), one season, one account structure.

## Adversarial cases (all in `tests/api/test_priority_score_pure.py`)

| Case | What happens | Why |
|---|---|---|
| Tiny cut, $300 at 3.0x, one ad | Low priority / Keep steady, low confidence, ranks below a proven cut | ROAS index shrinks 6.0 -> 1.54, hit rate shrinks, supply gap x 0.11 evidence weight, scale 0, tier held |
| One-ad cut with real spend | Scored normally, flagged "one ad carries N% of the cut's spend" | Concentration is information, not a penalty |
| Huge spend, bad ROAS ($60K at 0.30x vs 0.50x mix) | Capped at 44 (Keep steady at most), flagged | Guard against hit rate + scale alone shouting "more" while losing money |
| Dead cut with a great history (no spend, 5 of 6 old ads hit) | Keep steady at most, revive flag | Hit rate history counts, but no spend and low confidence keep it out of Make more |
| Lots already in the pipeline | Score x (1 - 0.5 x coverage), negative pipeline driver, flag | A full month of pace in production halves it |
| Proposed cut, no ads | Untested, score under 20, sorts last | No evidence; the score is not a test-selection tool |
| Missing / NaN / text in a field | Read as 0 / None, never raises | `_n`, `_opt` |

## Known limits

* It ranks cuts for **more** creative. Choosing which untested idea to test is the weekly priorities' Test section (`etl/creative_priorities.py`), not this score.
* Hit = $1,000 spend, the app's scoring threshold. The displayed hit rate on /priorities uses $10,000; with ~11 judged new ads per cut per month, $10,000 hits are too rare to rank on. Name which one you quote.
* Spend share and launch share are relative to the scope you filter (e.g. Vita Bull only vs all products): filtering changes supply gap, by design.
* Weekly priorities score on Meta-reported numbers; this score uses Upstack new customers. Rankings can differ between bases.
* The score is a prioritisation aid, not a causal claim: "new ads here have tended to hit" is consistent with the creative angle working and with buyers funding the cut.

## How the main session calls it

```python
from app import priority_signals, priority_score
sig = priority_signals.signals(conn, group, frm=None, to=None, filters=None, week=None, vendor=None)
#   group: list or comma string of cluster / icp / pain_point / vendor / media (product refused)
#   filters: {"products": [slug], "cluster": [normalised], "icp": [...], "pain_point": [...], "vendor": [...], "media": [...]}  ('' = untagged)
#   -> {group, as_of, from, rows: [signal dict], warning, basis}
scored = priority_score.score_cuts(sig["rows"], config=None)
#   -> [{labels, score, tier, drivers: [{signal, label, value, points, plain_text}], why, confidence, flags, components, signals}]
```

`build_signals(families, daily, tickets, group, as_of, frm, data_start, filters)` is the pure core used by both `signals` and the backtest.
