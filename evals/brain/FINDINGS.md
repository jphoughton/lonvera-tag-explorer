# Brain eval: findings (2026-11-10)

Held-out Q&A eval for the lonvera-brain MCP tools. 70 cases from the question ledger, answers hidden from the brain, graded by a separate model.

## How to run

```bash
PYTHONPATH=. python evals/brain/build_cases.py            # rebuilds cases.jsonl + holdout.json; type (c) keys are recomputed from the read-only database
PYTHONPATH=. python evals/brain/run.py --split tune --run rN --jobs 3   # needs claude on PATH and a venv with the mcp package (EVAL_PYTHON)
PYTHONPATH=. python evals/brain/run.py --split test --run test_final    # the test split: once, at the end
```

Agent = `claude -p --model claude-sonnet-5-5` in an empty temp dir with all built-in tools off, CLAUDE.md and auto-memory disabled, only `mcp__lonvera-brain__*` allowed, talking to a local stdio brain started with `BRAIN_HOLDOUT_FILE`. It cannot read the repo, the ledger or memory files. Grader = a second tool-less Sonnet call (verdict correct / partial / wrong, score 2 / 1 / 0, missing, errors, guardrail violations). Results: `results/<run>.jsonl` (answers, tool calls, grades) and `<run>.summary.json`.

## Cases

70 cases: a (where is X) 14, b (definitions and traps) 13, c (numbers from the database) 21, d (why / causal) 10, e (how the app works) 12. Tune 45 (a9 b8 c14 d6 e8), test 25 (a5 b5 c7 d4 e4); fixed seed 20261110, stratified by type. 74 ledger items are held out. Type (c) keys are recomputed from `analytics.*` (e.g. October Total NC ROAS 0.64 = 249,489.24 / 387,470.52); the ledger answer only confirmed what was asked. Counts are a little under "about 70 / 45 / 25" because ledger items with a checkable, database-covered answer are scarce (the ledger is mostly build instructions and pushback).

## Scores (percent of the maximum 2 points per case)

| Run | Brain | Tune n | Score | correct / partial / wrong | a | b | c | d | e |
|---|---|---|---|---|---|---|---|---|---|
| r0 first pass | build/v1 + holdout | 45 | 90.0 | 36 / 9 / 0 | | | | | |
| r0 baseline (4 keys corrected, see below) | build/v1 + holdout | 45 | 94.4 | 40 / 5 / 0 | 100 | 93.8 | 100 | 66.7 | 100 |
| r0b baseline rerun (noise check, same code) | same | 45 | 91.1 | 37 / 8 / 0 | 100 | 93.8 | 96.4 | 58.3 | 93.8 |
| r1 | instructions, tool descriptions, synonyms, data-guide examples | 45 | 93.3 | 39 / 6 / 0 | 100 | 93.8 | 100 | 66.7 | 93.8 |
| r2 | dense snippets, 1,000 character chunks | 45 | 92.2 | 38 / 7 / 0 | 100 | 93.8 | 100 | 66.7 | 87.5 |
| r3 | app_map match, error hint, instruction 8 | 45 | 95.6 | 41 / 4 / 0 | 100 | 93.8 | 100 | 75.0 | 100 |
| **test, once** | r3 brain | 25 | **96.0** | 23 / 2 / 0 | 100 | 100 | 100 | 75.0 | 100 |

Read this honestly: the brain was already near the ceiling. Two baseline samples of identical code differ by 3.3 points, so the tune movement (baseline mean 92.8, final 95.6) is about one noise width. What did move measurably: average tool calls per case fell from 5.1 / 5.0 to 3.9 (test 3.8), and the same four why-cases and the held-out-decision case are the only failures that survive. No case was ever "wrong" (score 0): the failure mode is partial answers, not invented numbers. Guardrail violations (grader count) 4 / 3 baseline, 2 final tune, 1 test.

## Failure buckets (tune split, baseline and later rounds)

| Bucket | Cases | Outcome |
|---|---|---|
| Why-reasoning gap: the mechanism lives only in the ledger / why files, not in docs or the database | d02 (CPM: neither placement nor country mix), d03 (caps cost volume, not price), d04 (frequency = ad-set overlap, not exhaustion), d08 (Skio run-off, billing-reminder flow, PayPal failures) | Not fixable on the brain side without leaking the answer. Behaviour is right (says the data is absent, no invented number, no "timing"), but the verified mechanism is missing. Deferred to the why tree (`feat/brain-why`). |
| Decision held only in the ledger | b03 (pacing revenue stays gross by Josh's call) | Same: a decision log, not a doc. Deferred. |
| Wrong basis / wrong population | d10 (launch volume counted from tracker rows; should be ad families by first spend day) | Fixed: instruction 5 plus a worked query in `docs/AGENT_DATA_GUIDE.md`. d10 passed r1 and r3, flaked in r2 (week-start convention). |
| Definition not quoted from the docs | b06 (% reached scaling) | Search found `TEST_TO_SCALE.md` but the 260-character snippet cut off the definition and the agent rebuilt the metric. Fixed with densest-window snippets and 1,000 character chunks; passes r3. |
| Tool output too big to use | e11 (/mcp auth), e02, e05 (service steps) | `app_map` returned all 216 routes. Added `match=`; instruction 8 says to filter and quote schedules in full. e11 passes from r3. |
| Search ranking / shorthand | NC, CPA, CPM, ROAS, plural forms | Added query-side synonyms (weight 0.5) and singularising. Unit-tested; no tune case turned on it, but fewer searches were needed. |
| Tool description unclear | a06 (freshness reads which table?) | Descriptions now name `analytics.data_coverage` / `etl_runs`, say `queryable_by_agents=false` means sql cannot read it, and `sql` errors on a public table now point to `catalog`. |
| Wrong key (my error) | a03, a06, b08, e01 | Corrected before any tuning: a03 and a06 demanded facts the question did not ask for; e01 required `etl.migrate` (a start-script step); **b08 had a false premise**: `meta_ad_daily` has no NULL rows (it starts the day Upstack Meta data starts), the NULLs are Google rows before 2025-05-13 in `platform_daily` / `google_campaign_daily`. The brain was right and the key was wrong. Re-ran those four. |

## What changed in the brain (all in this PR)

- `brain/instructions.py`: one set of standing instructions, sent on connect by both the stdio server and `/mcp` (they had drifted apart). An agent with only the MCP tools gets no CLAUDE.md, so the repeat-mistake rules now travel with the server: name the basis, spend adds but purchases do not, ratios from sums, creatives once, last day is yesterday, quote doc definitions, launch volume rule, a not-in-the-database list, the why-question rules, filter `app_map`.
- `brain/knowledge.py`: eval-only holdout (`BRAIN_HOLDOUT_FILE`); synonyms and singularising for search; densest-window snippets; 1,000 character doc chunks.
- `brain/tools.py`: sharper descriptions; `app_map(match=)`; `metric` falls back to doc hits when the glossary has no match; `sql` hint for non-analytics relations.
- `docs/AGENT_DATA_GUIDE.md`: three worked queries (new ad families per week, ROAS by product on both bases, testing vs scaling) and the CPM / reach pointers.
- Tests: `tests/brain/test_brain_holdout.py` (holdout on and off) and additions to `test_brain_tools.py`.

## Holdout design and its limits

`BRAIN_HOLDOUT_FILE` (ids as JSON list, `{"source_item_ids": [...]}` or one per line) hides those items from `past_questions` and `search`; `search` also skips every `docs/questions/**` file and blanks any doc line sharing a 6-word run with a held-out answer; ledger items whose answer repeats a held-out answer (the ledger has near-duplicates) are hidden too. The hosted server never sets it; unset, behaviour is unchanged. Limits: 2,861 other ledger items stay visible, so a "why" case can still pick up sibling facts from other ledger items; the holdout hides the ledger, not the database, which is the point of type (c).

Overlap between tuning and the test split, so the test number is not a clean generalisation score: instruction 5 (launch volume) was added after tune case d10, and test case c15 asks for the same count; the not-in-the-database list added Warehance / Shopify / repeat data after tune case d08, and test case d09 is the Warehance cancel claim. Both are genuine brain guidance (the launch-volume trap is in the project notes), but read the test score with that in mind.

## Deferred to #99 (migrations, `docs/schema/descriptions.py`, generated schema docs)

Description fixes I would make:
1. `meta_ad_daily.upstack_new_customers` and siblings: "no NULL rows in this view (it starts 2026-05-06); NULL exists only in `platform_daily` / `google_campaign_daily` Upstack columns before 2025-05-13; 0 means Upstack credited none".
2. `meta_ads.family_key`: "use this for unique ads and launch volume; ad_id counts copies".
3. `creative_tracker.launch_date`: "undercounts old ads; for launch volume use first spend day per family_key".
4. `meta_ad_daily.stage` and the roll-ups: "testing when the campaign name contains 'test', else scaling; renaming reclassifies history".
5. `orders_daily.new_customers` / `orders`: say that `new_customers` is first orders and that `orders` includes returning ones; state gross of tax and refunds on every revenue column.
6. `meta_campaign_daily_history.product` vs `meta_ad_daily.product_slug`: different spellings of the same thing; say so on both.
7. Examples (the `examples` array that `describe` returns): new ad families per week, ROAS by product on both bases, testing vs scaling split, monthly CPM, month-grain reach.
8. `meta_reach`: "never sum across periods; use the month row" on `reach` and `frequency`.

The metric glossary (#99) would fix: every case that starts with `metric` currently gets "glossary not loaded"; it would turn b06-type lookups (reached scaling, hit rate threshold, stage) into one call, give `metric` something to match for NC ROAS / NC CPA / basis, and remove the wasted call from most b, c and d cases. It cannot fix the d02 / d03 / d04 / d08 / b03 gaps: those are verified findings and decisions.

Deferred to the why tree: the four mechanisms above (mix does not explain CPM; caps cost volume not price; overlap not exhaustion; Skio run-off, billing-reminder flow and PayPal failures), and the "pacing revenue stays gross" decision; plus a rule for citing old past_questions answers (several tune answers repeated a stale figure from an earlier, later-retracted analysis, e.g. 56/84-day billing and price-cut explanations in d08).

## Grader caveats

Sonnet grading against a key is lenient on wording and strict on listed facts; some partials are the key asking for more than the question (a03, a06, e01, fixed) and some pass because an extra claim is hedged (d02). Numbers within 2% count. The grader sees tool names but not tool results.

## Round 2: making agents use `why` (branch `feat/brain-eval-2`)

Tune only; the test split ran once at the end.

| Run | Brain | Tune score | d | `why` calls on cause questions |
|---|---|---|---|---|
| m1 (merged build/v1, d cases only) | why tools, old instructions | d only: 25.0 | 25.0 | 3 of 10 (tune + test d) |
| w1 (d only) | routing words, compact answer, parent / gap known causes, Meta history | d only | 66.7 | 5 of 6 |
| w2 (full tune, round 1) | same | 94.4 | 66.7 | 4 of 5 cause questions |
| w3 (full tune, round 2) | known causes first + `how_to_answer`, hypothesis questions route to `why` | **97.8** | **83.3** | 5 of 5 cause questions (d10 is a count question) |
| **test, once** | w3 brain | **92.0** (earlier 96.0) | 75.0 | 1 of 4 (d01; d06, d07 and d09 are not cause-of-a-move questions in the agent's reading) |

Test fell 4 points although d held at 75.0: the two extra partials are e08 and e12 (`etl` non-fatal steps, `feeds` shadow vs direct wording), which no change here touches, and d09 / d01 as before. With 25 cases one partial is 2 points, so this is inside the noise measured earlier (two identical baselines differed by 3.3), but it is not an improvement and should be read that way.

Gap classification of what `why` returned on the tune d cases against the expected answers:

| Class | Count | Cases and fix |
|---|---|---|
| (a) data absent, left | 2 | d03 (capped vs uncapped CPM evidence: bids are not in the database and no known cause carries it); d04's overlap numbers (46 ad sets vs about 27 supportable: the known cause says "same-day overlap, not exposure" but not the counts) |
| (b) node exists, wrong routing | 2 | `metric="repeat revenue"` (space) was rejected: now plain words and phrases resolve (`resolve_metric`: "spend cliff", "frequency up", "renewals down", "CPM jump"); a Meta window before 2026-05-06 read $0 spend (platform_daily starts then), so CPM showed "n/a" for April: Meta spend now falls back to `meta_campaign_daily_history` (CPM +99%, $57.05 vs $28.68) |
| (c) known causes not surfaced | 3 | known causes on gap nodes (`bids`) never matched because they need an assessed node: now matched on dated overlap; causes on a parent (`spend` for `meta_spend`) were skipped: ancestors are scanned; d08's causes now appear |
| (d) too long or unclear | 3 | the tool answer was 82 KB: now a compact view (about 8-15 KB, `detail=true` for the full tree); known causes were buried behind other nodes' causes: the asked metric's own come first, refuted last, with a `how_to_answer` line; agents summarised around them (d02) |

No held-out answer text was added to code or `known_causes`; the changes are routing, matching and presentation. Tests added in `tests/brain/test_why.py` for phrase routing, ancestors, gap-node matching, the compact view, known-cause ordering and the Meta history fallback.
