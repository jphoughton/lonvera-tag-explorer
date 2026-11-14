# The `why` investigation

`why` (brain MCP tool, `brain/why.py`) walks the metric tree, then runs an investigation (`brain/why_investigate.py`, statistics in `brain/why_stats_inv.py`). Deterministic SQL and arithmetic, no model call, so a question stays cheap. Own budget: 24 extra queries and 45 seconds on top of `max_steps`.

## What it returns (read in this order)
- `answer`: the cause with its confidence word, or "inside noise at every grain tested". `confidence`: supported by control / consistent with / inside noise / unexplained.
- `chain`: why -> why -> why with numbers: L0 the move and its grain, L1 spend vs new-customer revenue, L2 hypotheses still standing, L3 product and ad sets carrying it, L4 the change someone made with its control.
- `ruled_out`, `untestable` (names the missing data), `next_check` (the one test or the next day's value that settles it), `action` only when a control supports it, `depth_stopped_at`.
- `noise_gate`: the move at 1, 3 and 7 days against the same weekdays of the prior 4 weeks (noise band = 2 x the larger of the Poisson band on new customers (or clicks for CPM / CTR / CPC) and the empirical spread of those baseline windows), what one more day would need to show, and the hour-of-day curve (orders_hourly, cumulative new-customer revenue vs the same weekday).
- `ledger`: one verdict for every hypothesis: H1 attribution lag, H2 spend level, H3 mix by product and stage, H4 CPM / CTR / CPC, H5 new vs old ad families, H6 funnel steps by product (binomial z), H7 AOV decomposed exactly into pack size, product mix, price point and discount (order lines, new customers, product net, shipping and tax excluded), H8 returning-buyer contamination, H9 country mix, H10 breakdown segments, H11 email / SMS, H12 stockouts, H13 feed breaks; then E1.. one per material Meta change event with its control, and C.. coincident page / Klaviyo / price / ad events (before / after only).
- Verdict words: `supported by control` (edited vs unedited objects differ beyond noise, parallel pre-trend, at least 5% of spend; still not randomized), `consistent with`, `ruled out`, `inside noise`, `untestable`.
- `priors_seen_before`: earlier runs from `analytics.why_findings` (a lead, never proof).

## The edited-vs-unedited control
For a Meta ad set / campaign event: the affected ids vs every other ad set on `meta_ad_daily`, 3 days before vs from the event day to the end of the window: spend, CPM, CPC, conversion (Upstack new customers per link click), NC ROAS, CPA per group, pre-trend of each group, difference in differences. "Consistent with not the cause" is the wording when the unedited group moved the same way. Time-stamped events also get a before / after split of the event day from `orders_hourly` (account level only; ad-set hourly is not stored).

## Tables and views
`analytics.change_timeline` (migration 261): Meta activity-log edits with bulk edits grouped (same event type, same actor, within 90 minutes -> one row, ids in `object_ids`), landing page copy / price changes, Klaviyo flows and campaigns, new ad families, first-seen discount codes and price points, stockouts, failed ETL runs. `public.why_findings` / `analytics.why_findings`: written by the hosted brain after each run with a fixed parameterized INSERT through `WHY_FINDINGS_URL` (else `DATABASE_URL`); not written (and the answer says so) when neither is set.

## Not available, so untestable
Payment declines and method, split-test visitor assignment, ad-set hourly delivery (Phase 3, not built), Upstack new-customer attribution by placement / device / age / country, email attributed to new customers.
