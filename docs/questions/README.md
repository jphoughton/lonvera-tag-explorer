# Lonvera business-question ledger

Every question, hypothesis, correlation and cause-and-effect claim Josh Houghton has put to Claude about Lonvera, with what the answer was and what is still open. Agent context: read it before answering a "why did X move" question, so you do not re-ask what is settled and do not re-claim what was withdrawn.

**Coverage:** 2026-05-22 to 2026-11-09 (source records start 2026-03-01; Lonvera items begin 2026-05-22). **2,938 Lonvera items.** Last refresh: 2026-11-10, adding 361 items dated 2026-10-20 to 2026-11-09. Lonvera only; no other business is in this folder.

The standing lesson: **a causal claim needs a like-for-like control, and counts are not rates.** The October 2026 repeat-revenue miss was called "mostly timing" for two days, then the billing-reminder email Josh raised proved a larger driver. See global rule 13 and the "Standing lesson" block at the top of each ledger.

## Files

| File | What it is |
|---|---|
| `lonvera_items.jsonl` | All Lonvera items, one JSON object per line, sorted by date. Fields: id, date, source (history, transcript, secondary), session, business, domain, kind, verbatim, intent, relationship {x, y, josh_belief}, answer, numbers, status, josh_reaction. |
| `ledger_marketing.md` | Ads, creative, retention and subscription, pricing, site, checkout. Relationship ledger, recurring questions, open threads, contradictions, decisions, metrics. |
| `ledger_model_ops.md` | Projections model, cohorts, data syncs, inventory, diligence. Same six sections. |
| `open_questions.md` | Every item with status unresolved or partial among causal, correlation, hypothesis, diagnosis, decision, research, metric and pushback items. Grouped by domain, newest first: date, question verbatim, what would settle it. |

## Counts

By domain:

| domain | items |
|---|---|
| Finance-model | 1074 |
| Creative | 471 |
| Website-CRO | 345 |
| Meta ads | 309 |
| Data-infra | 242 |
| Retention-subscription | 193 |
| Diligence-M&A | 83 |
| Tooling | 68 |
| Inventory-ops | 35 |
| Fulfillment | 27 |
| Email-SMS | 23 |
| Other | 15 |
| Pricing-promo | 15 |
| Team-vendors | 9 |
| Legal-compliance | 8 |
| TikTok-affiliate | 7 |
| Product-R&D | 6 |
| Google ads | 3 |
| Amazon marketplace | 3 |
| Cross-business | 2 |

By kind:

| kind | items |
|---|---|
| build_instruction | 1291 |
| pushback | 314 |
| decision_made | 233 |
| diagnosis_ask | 217 |
| research_ask | 182 |
| metric_lookup | 165 |
| other | 162 |
| decision_ask | 133 |
| hypothesis_stated | 131 |
| causal_question | 76 |
| correlation_question | 34 |

By status:

| status | items |
|---|---|
| answered | 1536 |
| n/a | 756 |
| unresolved | 481 |
| partial | 144 |
| superseded | 21 |

By source: history 1152, secondary 124, transcript 1662.

Notes: history items are typed prompts with no answer on record (status unresolved or n/a unless a later prompt reveals the answer). `n/a` marks build instructions, decisions and acknowledgements. Numbers are as stated in the item with their period; volatile figures older than about 60 days need re-verification. Items from 2026-10-20 on carry ids `L2-n`; earlier ids are `H_...` (history), `T_...` (transcript) and `S_secondary-n`.

## How to search

```
# every item about a topic
grep -i 'billing reminder' docs/questions/lonvera_items.jsonl | jq -r '[.date,.id,.status,.verbatim[0:120]] | @tsv'

# all open causal questions on Meta, newest first
jq -r 'select(.domain=="Meta ads" and (.kind=="causal_question" or .kind=="diagnosis_ask") and (.status=="unresolved" or .status=="partial")) | [.date,.id,.verbatim[0:140]] | @tsv' docs/questions/lonvera_items.jsonl | sort -r

# what Josh believed vs what the record says for a relationship
jq -r 'select(.relationship != null and (.relationship.x|test("holdout|reminder";"i"))) | [.date,.relationship.josh_belief,.answer[0:200]] | @tsv' docs/questions/lonvera_items.jsonl

# counts by month and status
jq -r '[.date[0:7],.status] | @tsv' docs/questions/lonvera_items.jsonl | sort | uniq -c
```

Ledger rows cite item ids, so `grep '"id": "L2-211"' docs/questions/lonvera_items.jsonl` jumps from a ledger row to the verbatim and the answer.

## How it is refreshed

Source of truth lives outside the repo at `~/Desktop/Google Sheets/question_ledger/` (not committed). Pipeline: `build_corpus.py` (Claude Code `history.jsonl` plus session transcripts) then `make_batches.py`, extraction per `EXTRACT_INSTRUCTIONS.md` (batches of at most about 250 prompts; prompts that start with an injected system note go through the recovery pass in `PLAN.md`), `merge_qc.py`, `make_subsets.py`, then the ledgers per `LEDGER_INSTRUCTIONS.md`. A refresh extracts only prompts newer than the last item date, keeps Lonvera items, appends them to `synth/all_items.jsonl`, adds entries to the two ledgers without rewriting existing ones (resolved open threads are marked closed with the closing item date), then regenerates the files in this folder. Implementation chatter ("deploy", "fix the button") is not extracted unless it states a hypothesis or decision.
