Source of truth: /Users/joshhoughton/.claude/skills/lonvera-brief-prep/SKILL.md; copied 2026-11-20; re-copy when it changes.

---
name: lonvera-brief-prep
description: Mandatory evidence pass before writing ANY Lonvera creative brief, ad concept, script, hook list or static brief (Vita Bull, Beef Liver, Boneflex, Vital Belle). Fires on "build a brief", "write briefs", "new concepts", "write ads", "script", "hooks", "static briefs", "creative round". Reviews every tag, transcript, hook, our CTR/CPC/CPM/conversion data, what clicked well but converted poorly, key value props, every competitor ad and competitor landing page, and our own landing pages BEFORE a single concept is written.
---

# lonvera-brief-prep: read everything, then write

Josh's rule (2026-11-13): nobody writes a brief from instinct. The evidence pack comes first, the brief cites it.
Fable (session model) decides what the pack means and writes the brief. Sonnet subagents do every pull, transcript read and page fetch (more than 5 tool calls = delegate). Never fabricate a number: if a figure is not in the data, say so.

Hard stops:
- No concept is written until the pack in section 9 exists and has been read end to end.
- Every concept is labelled one of two kinds. Evidence-backed: names the evidence it stands on (ad family or Concept ID, spend, the metrics, the tag line it maps to, or the competitor ad id). Net-new swing: an idea with no data behind it on purpose (new audience, angle, mechanism, format, or a competitor gap). Net-new swings are encouraged, not penalized: the evidence pass exists so they are informed and not duplicates, never to limit the brief to what has already worked. A net-new swing states the hypothesis (who it speaks to, why it should work), the nearest adjacent evidence if any (a similar tag, a competitor concept, a customer quote), and what result would tell us it worked. Josh's goal for a round is often broad new swings, so keep a real share of them; do not filter ideas out for lacking data.
- Any script, hook or voiceover: read `reference/script-voice.md` first and meet it (Josh 2026-11-18: one-listen clarity, one unique customer per script, real-person language with a banned-phrase list, mechanism as a reason not a nutrient list, his own line edits as the target voice, independent grader at 80+).
- Do NOT add compliance, claims or policy warnings to brief or ad-prompt text (Josh owns the claims pass; execution rule 11). Write the buyer, the pain, the product, the voice.
- ROAS means Upstack new-customer ROAS. Platform ROAS is a side data point only. Always show cost per link click, click-through rate, CPM, conversion rate and AOV, plus link click to add to cart, page view to add to cart, add to cart to purchase, cut by product and stage. Tables use date windows as column headers, one column per data point, numbers only. TLDR first, no unexpanded acronyms.

## 0. Pin the job
Product(s), persona / line(s) (`tag_taxonomy_lines`: cluster, ICP, pain point, ingredient), stage (testing or scaling), format (video, static), count, vendor, deadline. If a line is named, read its tag-map row and ingredient mapping (`tag_line_ingredients`).

## 1. Our tags (all of them)
Pull the full tag set for the product from `analytics.tracker_tags` and `tag_taxonomy_lines` joined to `creative_tracker`: Type, Pain Point, ICP / persona, Style, Creative Fidelity, Vendor, Proof Mechanism, Emotion, Lead Ingredient, Hook (`tracker_hooks`). Report per tag value: ads launched, spend, share of spend, Upstack NC ROAS, NC cost per customer, AOV, and which tag values have zero or near-zero ads (white space). Tags with fewer than 5 ads or under $2K spend are labelled thin; never rank on thin data.

## 2. Our performance (Upstack new-customer basis first)
Source: `creative_family_metrics` (filter `basis = 'upstack_new_customers'`; Meta-reported only as a side column), `meta_ad_daily`, `creative_family_daily`, `creative_scaling_benchmarks`. Count unique ads with `family_key` / `is_first_row`, never rows or `ad_id`. Use windows 7d, 30d, launch-to-date; recompute ratios from sums. Exclude the partial current day. Hit-rate definitions come from the docs (search the brain `search` tool), quote the threshold with the rate.
For every tag cut also show: cost per link click, click-through rate, CPM, conversion rate (new customers / link clicks), AOV, funnel rates.

## 3. Clicked well, converted poorly (and the reverse)
Per product, ads with at least $2K spend in the window:
- Clicked well, converted poorly: click-through rate or cost per link click in the best quartile, conversion rate or NC ROAS in the worst quartile. List each ad family with its hook, first three seconds, promise, tags, landing page and price shown. Then diagnose, in this order: ad promise vs landing page promise (`landing_page_ads`, `landing_page_top_ads`, `landing_page_versions`), price and offer shown vs cart price, audience (persona tag), funnel step where people drop (page view to add to cart, add to cart to purchase). Output: the pattern in one line per cluster, and what to do differently in the new brief (change the hook, keep the hook and fix the page, or drop the angle).
- Converted well, clicked poorly: best-quartile conversion with worst-quartile click-through rate = proven promise with a weak opener; candidates for a new hook on the same body.
Never call this cause; it is the pattern that is consistent with the data.

## 4. Transcripts that worked best
Source: `ad_transcripts` (spoken audio from the real file), `creative_content`, `tracker_hooks`. Take the top 20 ad families per product by spend-weighted NC ROAS with at least $5K spend, plus the top 20 by spend. Read the full transcript and copy of each. Extract per ad: opening line and first 3 seconds, length, structure (problem, mechanism, proof, offer, call to action), who speaks, proof type, offer and urgency, ending. Then show what the winners share that the losers (same spend band, bottom quartile) do not, as a table of feature vs share of winners vs share of losers. Quote the actual lines.

## 5. Key value props
From the winners' transcripts, copy, hooks and our review sources (Judge.me, Amazon review snippets in `lonvera_creative_key/cycles/c2026-10-12-personas/`): list each claim or mechanism (ingredient, organ, outcome, proof, price, guarantee, speed), count how many ads use it, spend behind it and NC ROAS, and mark which are carried by winners only. Separate what customers say (their words) from what we say. Flag value props that appear in many ads but never in a winner.

## 6. Competitors: every ad, every landing page
- Ads: `competitor_ads` (days_running and variant_count are survival and iteration signals, not proof of performance), `competitor_concepts` (mapped line, cluster, ICP, pain point, new-angle flags), `competitor_brands`. Read ALL ads for the brands in the product's category, not the top 10. Where the table is thin, read the Meta Ad Library directly for each brand.
- Landing pages: every distinct `landing_url` in `competitor_ads`. Fetch each (browser or WebFetch), save the page text, and record headline, offer, price, bundle ladder, guarantee, proof, review count, upsell, shipping promise. Write one row per competitor page.
- Output: what competitors run that we do not (new angles), what they run longest, the offers and prices they put in front of the same buyer, and the page structure of the longest-running ones.

## 7. Our landing pages
For each ad family in the pack, the page it sends to and the current copy (`landing_pages`, `landing_page_versions`), the price on the page vs the cart price, and the conversion of that page's ads. A new concept must name the page it will send to; if none fits, the brief says a new page is needed.

## 8. Net new check
"Net New" on the Creative Tracker means a net new STORY (the scenario, character and narrative), not a net new angle or concept (Josh 2026-11-16). Judge novelty at story level first: has this story been told in the account before, and how many briefs in the batch tell the same story. An existing angle told through a new story counts as net new; the same story in three executions counts once.
Before writing, classify each draft concept against what is running now: new to the account, already on, or ran before and stopped (tags, hook, angle). Say what share of the brief is genuinely new by audience, angle and format. Josh asked for broad new swings, not rewrites of running ads; but rewrites of winners with a better hook are fine when labelled. Use the white space from sections 1 and 6 (untouched tag lines, competitor angles we never ran, customer language not yet used) as the source of net-new swings, and give each swing the same fields as any other concept even though it has no performance data.

## 9. The evidence pack (write this file first)
Folder `~/Desktop/Google Sheets/lonvera_creative_key/cycles/c<date>-brief-<product>/EVIDENCE.md` with: TLDR (five lines: what wins, what clicks but does not convert, what the winners say, what competitors do, white space); tables from sections 1 to 7 with date windows as column headers; the raw pulls beside it (csv, transcripts, competitor page text). Cite the file in the brief.

## 10. The brief
Format follows the existing briefs (Google Doc in the Shared Drive 'Image Ads', one concept per page, model ad and reference embedded, tracker row per concept with the heading link). Each concept: its kind (evidence-backed or net-new swing), the line it maps to (cluster, ICP, pain point, ingredient) or "no line yet", the evidence it stands on or the hypothesis and nearest adjacent evidence, hook, first 3 seconds, script or layout, proof, offer, landing page, why it is not a copy of something running. Write quotes as final text, no brackets, no "fake" notes. Add tracker rows only after Josh's yes.

## 11. After
Log what shipped and which evidence each concept used, so the next round can check whether the evidence predicted the result (hit rate by "evidence strength").
