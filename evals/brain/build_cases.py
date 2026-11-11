"""Build evals/brain/cases.jsonl, holdout.json and split.json.

Type (c) keys are recomputed here from the read-only analytics database (never copied from the ledger); the other types are checked against docs/ and the code.
Split: fixed seed, stratified by type; the test split is for one final run only.
    PYTHONPATH=. python evals/brain/build_cases.py
"""
from __future__ import annotations

import json
import random
from pathlib import Path

from brain import rundb

HERE = Path(__file__).resolve().parent
AS_OF = "2026-11-10"
SEED = 20261110
TEST_SHARE = 0.36


def q1(sql: str):
    r = rundb.run_select(sql, 50, allow_file=True)
    return [dict(zip(r["columns"], row)) for row in r["rows"]]


def f(x: float, n: int = 2) -> str:
    return f"{x:,.{n}f}"


C: list[dict] = []


def case(type_, ids, question, expected, must=(), must_not=(), notes="", split=None):
    C.append({"split": split, "type": type_, "source_item_ids": list(ids), "question": question, "as_of": AS_OF, "expected_answer": expected, "must_mention": list(must), "must_not": list(must_not), "grading_notes": notes})


# ------------------------------------------------------------------------------------------------- (a) where is X
case("a", ["L2-311"], "Which database view holds Meta spend, clicks and purchases for each individual ad on each day, and from which date does it start?",
     "analytics.meta_ad_daily (one row per ad per day); starts 2026-05-06. Campaign-level only before that.", ["meta_ad_daily", "2026-05-06"], [], "Ad set / campaign roll-ups exist but the ad grain is meta_ad_daily.")
case("a", ["L2-268", "T_20-25"], "Where in the database can I get Meta reach and frequency, and may I add up the daily rows to get a monthly figure?",
     "analytics.meta_reach, stored at day / week / month grain; reach is de-duplicated per period so it must never be summed across days or weeks; read the month row.", ["meta_reach", "not summed", "month"], ["sum the daily reach"], "Account level only (both ad accounts); nothing below account level.")
case("a", ["S_secondary-184"], "I need Meta campaign spend for March 2026. Which view has it?",
     "analytics.meta_campaign_daily_history: campaign x day back to 2024-12-31, the only Meta data before 2026-05-06 (campaign grain, no ad sets or ads). meta_campaign_daily starts 2026-05-06.", ["meta_campaign_daily_history"], ["meta_ad_daily has it"], "The date is context only; the view name is the answer.")
case("a", ["L2-231"], "Where do I find Google's own spend and conversions by campaign, and Upstack's attributed new customers for Google? How far back does each go?",
     "analytics.google_campaign_daily; Google's own numbers from 2024-03-25, Upstack columns from 2025-05-13 (NULL before). No ad group, ad or keyword grain exists.", ["google_campaign_daily", "2025-05-13", "2024-03-25"])
case("a", ["T_26-46"], "Which view gives the business's ACTUAL new-customer revenue per day, as opposed to what Meta or Google report, and which column is it?",
     "analytics.orders_daily, column new_customer_revenue (Upstack order-level, Pacific days, counted once; gross of tax and refunds). It is the numerator of Total NC ROAS.", ["orders_daily", "new_customer_revenue"])
case("a", ["T_18-48", "T_18-73"], "How do I check, before quoting a number, which data sources are loaded through which day?",
     "Query analytics.data_coverage (source, first_day, last_day, loaded_at) or call the freshness tool; the last loaded day is yesterday (Pacific).", ["data_coverage or the freshness tool"], [], "Either the data_coverage view or the freshness tool is a full answer; yesterday as the last loaded day is a bonus.")
case("a", ["L2-292", "L2-291"], "Which view tells me whether a pipeline job failed or ran late, and since when does it have history?",
     "analytics.etl_runs: one row per job run with status, data_through, error; history since the first production run on 2026-11-04 (UTC).", ["etl_runs", "2026-11-04"])
case("a", ["L2-300", "L2-295"], "Where in the database is the hook (opening line) of each ad stored, and which view should I use to ask which hook, cluster or ICP performs?",
     "analytics.creative_tracker: hook and hook_source columns, with cluster / icp / pain_point / emotional_trigger and since-launch metrics; one row per tracker row, metrics on the first row of each ad family.", ["creative_tracker", "hook"])
case("a", ["T_11-84"], "Which view lists every ad with its status, product and lifetime totals, and how do I count unique ads rather than duplicate copies?",
     "analytics.meta_ads (one row per Meta ad_id with status, is_active, product, lifetime_*). Count unique ads by family (count(distinct family_key)); the same creative is duplicated across ad sets so ad_id counts are inflated.", ["meta_ads", "family_key"])
case("a", ["L2-132", "L2-151"], "Where do I get Meta spend split by product (Vita Bull, Beef Liver, Boneflex)?",
     "analytics.meta_ad_daily (and meta_ads) carry product_slug: vita-bull, beef-liver, boneflex; roll up spend by product_slug.", ["product_slug", "meta_ad_daily"])
case("a", ["T_25-41", "T_25-43"], "Can I get Meta spend broken out by placement, device or country from the database?",
     "No. Placement, device, age / gender and country breakdowns are not in the database (listed under what is not in the database); say so instead of guessing.", ["not in the database"], ["Facebook Reels share", "country mix explains"], "Guardrail: a correct answer says the data is absent and does not invent a breakdown.")
case("a", ["T_small-16", "S_secondary-91"], "Does the analytics database have email or SMS performance, margins, COGS or order-level customer data?",
     "No. Email and SMS, margins / COGS / P&L, and customer- or order-level data are not in the analytics views.", ["not in the database"], [], "Guardrail: absence stated, no numbers invented.")
case("a", ["L2-314"], "Which page in the app shows creatives that moved from Testing to Scaling, and which API endpoint feeds it?",
     "Page /promotions (top nav 'Test to scale'), endpoint GET /api/promotions.", ["/promotions", "/api/promotions"])
case("a", ["L2-304"], "Is there one view that shows Meta and Google spend side by side per day?",
     "Yes: analytics.platform_daily (one row per day and platform 'meta' or 'google'); spend, impressions and clicks add across platforms, purchases and revenue do not.", ["platform_daily"])

# ------------------------------------------------------------------------------------------------- (b) metric definitions and traps
case("b", ["T_26-46", "L2-302"], "How is Total NC ROAS defined for Lonvera, and which tables does each side come from?",
     "Total NC ROAS = actual new-customer revenue (analytics.orders_daily.new_customer_revenue) / total spend (Meta + Google, analytics.platform_daily) over the same days, as a ratio of sums.", ["new_customer_revenue", "Meta", "Google", "ratio of sums"], ["Meta-reported revenue"])
case("b", ["L2-304", "L2-231"], "Can I add Meta's purchases and Google's purchases to get total purchases or revenue? What can be added across platforms?",
     "No. Meta and Google credit the same orders, so purchases and revenue are never added across platforms. Spend (and impressions, clicks) does add.", ["spend", "not"], ["add the purchases"])
case("b", ["L2-288", "T_18-64"], "Is revenue in the Lonvera views net or gross of tax and refunds, and why does that matter when comparing with the P&L?",
     "Gross: includes tax / shipping and is before refunds. Josh decided pacing revenue stays gross (refunds were ~9-11% of DTC revenue); compare with the P&L accordingly (net runs lower).", ["gross", "refund"])
case("b", ["T_11-52"], "What spend threshold defines a 'hit' for an ad, and is it the same everywhere in the app?",
     "Not one number. The creative priorities score uses HIT_SPEND = $1,000 (share of mature ads, at least 14 days old, that reached $1,000); the product tiles' hit-rate strip counts ads that reached $10K+. Always quote the threshold.", ["$1,000", "$10K"], [], "Either numbers plus the warning to name the threshold is correct.")
case("b", ["L2-314"], "On the Test to scale page, what exactly counts as a 'move' to scaling, and what is a bulk move day?",
     "A creative (tracker family) moved when it had spend in a Testing campaign and its first Scaling-campaign spend came on a later day. A bulk move day is a day with 40 or more moves (a mass relaunch, not test winners); Hide bulk moves removes them from every number.", ["later day", "40"])
case("b", ["L2-314"], "How is '% reached scaling' computed on the Test to scale page and why do recent weeks read low?",
     "(testers that have moved) / (testers); testers = launched creatives that began in testing. It is a cohort rate, so creatives launched in the last days have had no time to scale and recent weeks read low.", ["testers", "no time"])
case("b", ["T_18-48"], "In the analytics views, what does 'last 30 days' mean and what is the latest day I can trust?",
     "The 30 days ending at data_coverage.last_day, which is yesterday (Pacific); today is not loaded. The last 3 to 7 days still move because Meta restates conversions.", ["yesterday", "last_day"])
case("b", ["T_20-55", "T_20-57"], "Why are the upstack_* columns NULL on early Google days in platform_daily / google_campaign_daily, and what does NULL mean for sums and ratios?",
     "Upstack was not pulled for Google before 2025-05-13, so upstack_* is NULL there (meta_ad_daily itself starts 2026-05-06, when Upstack Meta data starts, so it has no such NULLs). NULL is not zero: do not treat it as a zero-conversion day or divide by it.", ["2025-05-13", "not zero"])
case("b", ["T_11-84", "T_11-85"], "In creative_tracker, how do I sum spend or count ads without double counting?",
     "Since-launch metrics sit on the FIRST row of each ad family (is_first_row): sum rows, and count unique ads with count(distinct family_key).", ["first row", "family_key"])
case("b", ["L2-231", "L2-232"], "Meta shows more purchases and a higher ROAS than Upstack's new-customer numbers. Which basis counts returning customers and which one is the 'NC' in NC ROAS?",
     "Meta-reported (meta_purchases / meta_revenue) counts returning customers; upstack_new_customer* counts first orders only and is the NC basis; upstack_order* counts all orders Upstack credits. Never mix bases in one ratio.", ["returning", "first order"])
case("b", ["T_20-25", "L2-268"], "Can I sum reach across days or weeks to get monthly reach?",
     "No. Reach is de-duplicated by Meta per period, so it is stored at day, week and month grain and must never be summed across periods; use the month row.", ["month", "never"])
case("b", ["L2-204"], "How does the app decide whether a Meta campaign is Testing or Scaling?",
     "By name: Testing when the campaign name contains 'test', otherwise Scaling (stage on meta_ad_daily / meta_campaign_daily; renaming a campaign reclassifies its history).", ["contains 'test'"], [], "Accept 'name contains test'.")
case("b", ["T_26-49", "T_26-50"], "How do I compute a blended ROAS or CPA over several days without bias?",
     "Ratio of sums: sum(revenue) / sum(spend) or sum(spend) / sum(purchases), never an average of daily ratios.", ["ratio of sums"])

# ------------------------------------------------------------------------------------------------- (c) numbers from the database
wk = q1("select sum(spend) s from analytics.platform_daily where platform='meta' and day between '2026-10-05' and '2026-10-11'")[0]["s"]
case("c", ["T_20-55"], "As of 2026-11-10: how much did Meta spend in the week Monday 2026-10-05 to Sunday 2026-10-11?",
     f"${f(wk)} Meta spend (analytics.platform_daily, platform = 'meta').", [f(wk, 0)], [], "Meta only, not Google. Within 2% = correct.")
sep = q1("select sum(new_customer_revenue) r, sum(new_customers) n from analytics.orders_daily where day between '2026-09-01' and '2026-09-30'")[0]
case("c", ["T_06-61"], "As of 2026-11-10: what was actual new-customer revenue for September 2026 (Pacific days), and how many new customers?",
     f"${f(sep['r'])} new-customer revenue from {int(sep['n']):,} new customers (analytics.orders_daily). Gross.", [f(sep['r'], 0), f"{int(sep['n']):,}"])
octo = q1("select sum(new_customer_revenue) r, sum(new_customers) n, sum(total_revenue) t, sum(orders) o from analytics.orders_daily where day between '2026-10-01' and '2026-10-31'")[0]
plat = {r["platform"]: r for r in q1("select platform, sum(spend) sp, sum(own_purchases) p, sum(own_revenue) r, sum(upstack_new_customers) unc, sum(upstack_new_customer_revenue) uncr from analytics.platform_daily where day between '2026-10-01' and '2026-10-31' group by 1")}
tot_sp = plat["meta"]["sp"] + plat["google"]["sp"]
case("c", ["L2-319"], "As of 2026-11-10: what was total ad spend (Meta plus Google) for October 2026, as the app's data has it?",
     f"${f(tot_sp)} = Meta ${f(plat['meta']['sp'])} + Google ${f(plat['google']['sp'])} (analytics.platform_daily).", [f(tot_sp, 0)], [], "Within 2% correct. Amazon ads are not in the views.")
case("c", ["L2-232", "T_26-46"], "As of 2026-11-10: what was Total NC ROAS for October 2026?",
     f"{octo['r'] / tot_sp:.2f}x = actual new-customer revenue ${f(octo['r'])} / Meta + Google spend ${f(tot_sp)} (ratio of sums, Pacific days).", [f"{octo['r'] / tot_sp:.2f}"], ["Meta-reported ROAS is the answer"], "0.64 expected; must be the orders_daily numerator, not Meta revenue.")
m_roas = plat["meta"]["r"] / plat["meta"]["sp"]
m_nc_roas = plat["meta"]["uncr"] / plat["meta"]["sp"]
case("c", ["L2-231", "L2-232"], "As of 2026-11-10: for October 2026, what was Meta's own reported ROAS and what was Meta's ROAS counting only Upstack-attributed new-customer revenue? Why do they differ?",
     f"Meta-reported ROAS {m_roas:.2f} (${f(plat['meta']['r'])} / ${f(plat['meta']['sp'])}); Upstack new-customer-attributed ROAS {m_nc_roas:.2f} (${f(plat['meta']['uncr'])}). Different bases: Meta counts returning customers, Upstack first orders only.", [f"{m_roas:.2f}", f"{m_nc_roas:.2f}", "returning"])
case("c", ["T_08-35", "L2-218"], "As of 2026-11-10: what was the new-customer CPA for October 2026 on the business basis (actual new customers vs Meta plus Google spend)?",
     f"${tot_sp / octo['n']:.2f} = ${f(tot_sp)} / {int(octo['n']):,} new customers (analytics.orders_daily.new_customers).", [f"{tot_sp / octo['n']:.0f}"], [], "About $108. Meta-only Upstack-attributed CPA would be ~$127, a different basis; the business basis is requested.")
prod = {r["product_slug"]: r for r in q1("select product_slug, sum(spend) sp, sum(upstack_new_customer_revenue) ncr from analytics.meta_ad_daily where day between '2026-10-01' and '2026-10-31' group by 1")}
vb = prod["vita-bull"]["sp"]
case("c", ["T_19-26"], "As of 2026-11-10: how much did Meta spend on Vita Bull in October 2026, and what share of Meta spend was that?",
     f"${f(vb)}, {vb / plat['meta']['sp'] * 100:.1f}% of Meta's ${f(plat['meta']['sp'])} (meta_ad_daily, product_slug = 'vita-bull').", [f(vb, 0), f"{vb / plat['meta']['sp'] * 100:.0f}%"])
cpm = {r["m"]: r for r in q1("select to_char(date_trunc('month',day),'YYYY-MM') m, sum(spend)/sum(impressions)*1000 cpm from analytics.meta_campaign_daily_history where day>='2026-04-01' group by 1")}
case("c", ["T_25-42", "T_10-22"], "As of 2026-11-10: what was Meta's CPM (cost per 1,000 impressions) in April 2026 and in September 2026?",
     f"April ${cpm['2026-04']['cpm']:.2f}, September ${cpm['2026-09']['cpm']:.2f} (sum of spend / sum of impressions x 1000, meta_campaign_daily_history).", [f"{cpm['2026-04']['cpm']:.0f}", f"{cpm['2026-09']['cpm']:.0f}"])
rr = {r["period_key"]: r for r in q1("select period_key, reach, frequency, impressions from analytics.meta_reach where grain='month' and period_key in ('2026-04','2026-09')")}
case("c", ["T_17-41", "S_secondary-88"], "As of 2026-11-10: what was Meta's account-level frequency in September 2026 and in April 2026, and how many unique people were reached in September?",
     f"September frequency {rr['2026-09']['frequency']:.2f} (reach {int(rr['2026-09']['reach']):,}); April {rr['2026-04']['frequency']:.2f} (analytics.meta_reach, month grain).", [f"{rr['2026-09']['frequency']:.1f}", f"{int(rr['2026-09']['reach']):,}"], ["sum of daily reach"])
nc2 = q1("select sum(new_customers) filter (where day between '2026-07-06' and '2026-07-12') jul, sum(new_customers) filter (where day between '2026-10-12' and '2026-10-18') oct from analytics.orders_daily")[0]
case("c", ["T_47-29"], "As of 2026-11-10: how many actual new customers did the business get in the week Mon 2026-10-12 to Sun 2026-10-18, versus the week Mon 2026-07-06 to Sun 2026-07-12?",
     f"{int(nc2['oct']):,} versus {int(nc2['jul']):,} (analytics.orders_daily.new_customers), down {100 * (1 - nc2['oct'] / nc2['jul']):.0f}%.", [f"{int(nc2['oct']):,}", f"{int(nc2['jul']):,}"])
stg = {r["stage"]: r for r in q1("select stage, sum(spend) sp, sum(upstack_new_customers) nc from analytics.meta_campaign_daily where day between '2026-10-01' and '2026-10-31' group by 1")}
tsh = stg["testing"]["sp"] / (stg["testing"]["sp"] + stg["scaling"]["sp"])
case("c", ["L2-204"], "As of 2026-11-10: what share of Meta spend in October 2026 went to Testing campaigns, and how much was it in dollars?",
     f"{tsh * 100:.1f}% = ${f(stg['testing']['sp'])} testing of ${f(stg['testing']['sp'] + stg['scaling']['sp'])} (stage = 'testing' on meta_campaign_daily).", [f"{tsh * 100:.0f}%", f(stg["testing"]["sp"], 0)])
g = plat["google"]
case("c", ["L2-231"], "As of 2026-11-10: what was Google's spend in October 2026 and its own reported ROAS (Google-reported revenue / spend)?",
     f"${f(g['sp'])} spend; Google-reported ROAS {g['r'] / g['sp']:.2f} (${f(g['r'])} own revenue; analytics.platform_daily platform = 'google').", [f(g["sp"], 0), f"{g['r'] / g['sp']:.2f}"])
d13, d15 = (q1(f"select sum(spend) s from analytics.platform_daily where platform='meta' and day = '{d}'")[0]["s"] for d in ("2026-09-13", "2026-09-15"))
case("c", ["T_08-48", "T_17-9"], "As of 2026-11-10: how much did Meta spend per day on 2026-09-13 and on 2026-09-15?",
     f"2026-09-13 ${f(d13)}; 2026-09-15 ${f(d15)} (platform_daily, platform = 'meta'), a {100 * (1 - d15 / d13):.0f}% drop in two days.", [f(d13, 0), f(d15, 0)])
share = plat["meta"]["unc"] / octo["n"]
case("c", ["T_08-38"], "As of 2026-11-10: in October 2026 how many new customers did Upstack attribute to Meta, and what share of the business's actual new customers is that?",
     f"{int(plat['meta']['unc']):,} Meta-attributed new customers of {int(octo['n']):,} actual = {share * 100:.1f}% (platform_daily upstack_new_customers vs orders_daily.new_customers).", [f"{int(plat['meta']['unc']):,}", f"{share * 100:.0f}%"])
fam = q1("select count(*) c from (select family_key, min(day) fd from analytics.meta_ad_daily d join analytics.meta_ads a using(ad_id) where d.spend > 0 and a.family_key is not null group by 1) t where fd between '2026-10-12' and '2026-10-18'")[0]["c"]
case("c", ["L2-241", "T_17-36"], "As of 2026-11-10: how many new ad families (family_key) had their first day of Meta spend in the week Mon 2026-10-12 to Sun 2026-10-18?",
     f"{fam} ad families (first spend day per family_key from meta_ad_daily joined to meta_ads, spend > 0). The older '~80 a week' figure is a September number, not current.", [str(fam)], ["about 80 per week"], "Within 2%. Counting ad_id instead of family_key gives a larger number and is wrong.")
bf = prod["boneflex"]
case("c", ["T_24-6"], "As of 2026-11-10: for October 2026, what were Boneflex's Meta spend and its Upstack new-customer revenue, and the resulting new-customer ROAS on Meta spend?",
     f"Spend ${f(bf['sp'])}; Upstack-attributed new-customer revenue ${f(bf['ncr'])}; ROAS {bf['ncr'] / bf['sp']:.2f} (meta_ad_daily, product_slug = 'boneflex').", [f(bf["sp"], 0), f"{bf['ncr'] / bf['sp']:.2f}"])
case("c", ["T_26-33"], "As of 2026-11-10: what was total revenue (all orders) for October 2026 and what share of it was new-customer revenue?",
     f"Total revenue ${f(octo['t'])} from {int(octo['o']):,} orders; new-customer revenue ${f(octo['r'])} = {octo['r'] / octo['t'] * 100:.1f}% (analytics.orders_daily; gross).", [f(octo["t"], 0), f"{octo['r'] / octo['t'] * 100:.0f}%"])

# added before the baseline run finished (split fixed explicitly so the earlier assignments do not move)
wk2 = q1("select sum(new_customer_revenue) filter (where day between '2026-10-19' and '2026-10-25') r2, sum(new_customer_revenue) filter (where day between '2026-10-12' and '2026-10-18') r1 from analytics.orders_daily")[0]
sp2 = q1("select sum(spend) filter (where day between '2026-10-19' and '2026-10-25') s2, sum(spend) filter (where day between '2026-10-12' and '2026-10-18') s1 from analytics.platform_daily")[0]
case("c", ["T_20-58"], "As of 2026-11-10: what was Total NC ROAS in the week Mon 2026-10-19 to Sun 2026-10-25, and in the week before it (Oct 12-18)?",
     f"Oct 19-25: {wk2['r2'] / sp2['s2']:.2f}x (${f(wk2['r2'])} / ${f(sp2['s2'])}); Oct 12-18: {wk2['r1'] / sp2['s1']:.2f}x (${f(wk2['r1'])} / ${f(sp2['s1'])}). Actual new-customer revenue over Meta + Google spend, ratio of sums.",
     [f"{wk2['r2'] / sp2['s2']:.2f}", f"{wk2['r1'] / sp2['s1']:.2f}"], [], "Within 2%.", split="tune")
bl = prod["beef-liver"]
bl_nc = q1("select sum(upstack_new_customers) n from analytics.meta_ad_daily where product_slug='beef-liver' and day between '2026-10-01' and '2026-10-31'")[0]["n"]
case("c", ["T_23-1"], "As of 2026-11-10: what was Beef Liver's new-customer CPA on Meta spend in October 2026, using Upstack-attributed new customers?",
     f"${bl['sp'] / bl_nc:.2f} = ${f(bl['sp'])} Meta spend / {int(bl_nc):,} Upstack-attributed new customers (meta_ad_daily, product_slug = 'beef-liver').", [f"{bl['sp'] / bl_nc:.0f}"], [], "Within 2%. Meta-reported CPA is a different basis.", split="tune")
pm = {r["product_slug"]: r for r in q1("select product_slug, sum(spend) sp, sum(meta_revenue) mr, sum(upstack_new_customer_revenue) ncr from analytics.meta_ad_daily where day between '2026-10-01' and '2026-10-31' group by 1")}
best_meta = max(pm, key=lambda k: pm[k]["mr"] / pm[k]["sp"])
best_nc = max(pm, key=lambda k: pm[k]["ncr"] / pm[k]["sp"])
case("c", ["T_21-45", "T_24-6"], "As of 2026-11-10: which of the three products had the best ROAS on Meta spend in October 2026?",
     f"It depends on the basis, and the answer must say so. Meta-reported ROAS: {best_meta} best ({', '.join(f'{k} {pm[k]['mr'] / pm[k]['sp']:.2f}' for k in pm)}). Upstack new-customer ROAS: {best_nc} best ({', '.join(f'{k} {pm[k]['ncr'] / pm[k]['sp']:.2f}' for k in pm)}).",
     [best_meta, best_nc, "basis"], ["one product is best on every basis"], "Full credit needs both bases named with the right winner on each; a single unlabeled ranking is partial.", split="tune")
msep = q1("select sum(spend) filter (where platform='meta') m, sum(spend) filter (where platform='google') g from analytics.platform_daily where day between '2026-09-01' and '2026-09-30'")[0]
case("c", ["L2-319"], "As of 2026-11-10: what was September 2026 Meta spend, and what share of Meta plus Google spend was Meta?",
     f"Meta ${f(msep['m'])}; share {msep['m'] / (msep['m'] + msep['g']) * 100:.1f}% of ${f(msep['m'] + msep['g'])} (platform_daily).", [f(msep["m"], 0), f"{msep['m'] / (msep['m'] + msep['g']) * 100:.0f}%"], [], "Within 2%.", split="test")

# ------------------------------------------------------------------------------------------------- (d) why / causal with a verified answer
case("d", ["T_08-48", "T_17-9", "T_08-49"], "As of 2026-11-10: Meta spend fell from about $35K a day on 2026-09-13 to about $15K on 2026-09-15. What caused the cliff, and how sure are we?",
     "Consistent with the 2026-09-14 bid tightening on the scaling ad sets (cost caps cut / ROAS floors raised, e.g. Vita Bull cap $68.50 to $65 and tROAS 0.95 to 1.00): delivery is bid-limited, the cut halved delivery without improving efficiency. The change log is not in the database, so the DB shows the drop but not the cause; confidence is 'consistent with' unless a control exists.",
     ["bid", "09-14", "consistent with"], ["audience exhaustion caused"], "Full credit needs the bid / cap / floor change on Sep 14 and the honest 'DB shows the drop, change log needed'. Number-only answers are partial.")
case("d", ["T_25-42", "T_25-43", "T_10-22"], "As of 2026-11-10: Meta's CPM roughly doubled between April and September 2026. Was that placement mix or country mix, or something else?",
     "Neither explains it: placement mix is a wash (repricing September at April CPMs barely moves purchases per $1K) and country mix explains about 0% of the CPM rise (within-country +$24.6 of +$25.7); CPM itself rose in every placement and country. CPM $29 (Apr) to $57 (Sep). Placement and country breakdowns are not in the analytics database.",
     ["CPM", "not in the database"], ["mix explains most"], "Guardrail: the DB cannot show placement or country; a correct answer gives the CPM rise from the data and says the breakdown cannot be checked here.")
case("d", ["T_21-60", "T_25-47"], "As of 2026-11-10: does putting cost caps or ROAS floors on ad sets raise Meta CPM?",
     "No: uncapped ad sets paid more, not less (Boneflex US 9/29-10/2: uncapped CPM $78.65 ROAS 0.50; cost cap $60.17 ROAS 0.80; tROAS $38.44 ROAS 1.19). Caps cost volume, not price; the CPM driver was geography / overlap, not the cap. Bid settings are not in the analytics database.",
     ["volume", "not price"], ["caps raise CPM"], "Bid settings are not in the DB; partial credit for a careful 'cannot tell from here'.")
case("d", ["T_17-41", "T_17-42", "S_secondary-88"], "As of 2026-11-10: Meta frequency rose from about 4.9 (April) to about 8 (September). Is the audience exhausted?",
     "No sign of exhaustion: the extra frequency is same-day overlap between ad sets (46 ad sets vs about 27 supportable) and rising price per impression; new accounts were 62% of weekly reach vs 54-65% before. The DB confirms the frequency rise (meta_reach month grain) but has no ad-set reach, so overlap cannot be tested here.",
     ["overlap", "frequency"], ["audience exhaustion is the cause"], "Frequency numbers can come from meta_reach; the mechanism needs the overlap finding.")
case("d", ["T_47-29", "T_26-35"], "As of 2026-11-10: weekly new customers fell about 66% between Jul 6-12 and Oct 12-18. How much of that is spend and how much is efficiency?",
     "Spend (Meta + Google) fell 58% ($197,142 to $82,080); new customers fell 66% (2,244 to 772); so most of the fall is spend, and cost per new customer rose about 21% ($87.85 to $106.32, business basis). Attribution of the efficiency part needs a like-for-like control.",
     ["58%", "21%"], [], "Numbers within 2%; must separate spend from efficiency.")
case("d", ["T_26-49", "T_26-50"], "As of 2026-11-10: did testing campaigns convert worse than scaling campaigns in October 2026? Compare NC CPA on the Upstack new-customer basis.",
     f"Yes: testing ${f(stg['testing']['sp'])} / {int(stg['testing']['nc'])} new customers = ${stg['testing']['sp'] / stg['testing']['nc']:.0f} per new customer; scaling ${f(stg['scaling']['sp'])} / {int(stg['scaling']['nc']):,} = ${stg['scaling']['sp'] / stg['scaling']['nc']:.0f} (meta_campaign_daily, stage). Part of any blended CPA gap is testing's share of spend (mix).",
     [f"{stg['testing']['sp'] / stg['testing']['nc']:.0f}", f"{stg['scaling']['sp'] / stg['scaling']['nc']:.0f}"], [], "Within 2%.")
case("d", ["L2-231", "L2-232", "T_18-48"], "As of 2026-11-10: Meta's reported ROAS is about 0.70 for October but the business's actual new-customer ROAS is about 0.64 on Meta + Google spend. Is something broken?",
     "Not broken: different bases and denominators. Meta-reported ROAS counts returning customers; actual NC ROAS uses first-order revenue over Meta + Google spend (a larger denominator). Name the basis; never mix bases in one ratio.",
     ["basis", "returning"], ["tracking bug"])
case("d", ["T_26-29", "T_26-35", "L2-143", "T_26-45"], "As of 2026-11-10: why was Lonvera's October repeat revenue so far below the model?",
     "Several things, none decided by the analytics database: the Skio (Shopify-native) subscription base was running off (about -44% over 13 weeks, renewals per due falling after the Sep 14-15 billing-reminder flow scale-up, consistent with Josh's email finding), PayPal rebills failed Oct 12-13 (billed ~3 days late), and fewer new customers means fewer first rebills. Repeat / subscription data is not in the analytics views.",
     ["Skio", "billing-reminder", "not in"], ["timing explains it"], "Guardrail: with no rebill data in the DB the brain should say so and not blame timing alone. Partial credit for the honest gap.")
case("d", ["S_secondary-102", "T_18-68"], "As of 2026-11-10: someone says 42.5% of Shopify orders that reach the Warehance warehouse system are cancelled. Is that a fulfillment problem?",
     "No: that finding was withdrawn. Every non-US order lands in Warehance and is auto-cancelled within about 15 seconds because Shopify routes it to the international 3PL (D1); those are 92% of Warehance cancels. The real US cancel rate is 5.9%. Warehance data is not in the analytics database.",
     ["non-US", "5.9%"], ["42.5% is real"], "Guardrail: the brain cannot see Warehance; it should say so and not confirm 42.5%.")
case("d", ["L2-241", "T_17-36"], "As of 2026-11-10: is it still true that Lonvera launches only about 80 new creative concepts a week?",
     "No. 80 a week was a September figure repeated as if current. In October the weeks of Oct 5 / Oct 12 / Oct 19 each had hundreds of ad families with first spend (about 323 / 272 / 828 on the meta_ads family_key basis).",
     ["not current", "272"], ["80 per week is current"], "Accept the Oct weekly counts within a few percent.")

# ------------------------------------------------------------------------------------------------- (e) how the app works
case("e", ["L2-310", "L2-345"], "Which Railway service runs the nightly data load, at what schedule (UTC), and what are its first steps?",
     "Service `etl`, cron 0 13 * * * UTC, via scripts/deploy/run_etl.sh: etl.migrate, then etl.run_daily, etl.upstack_daily, etl.stage_moves, etl.projection_daily, etl.amazon_mtd, then non-fatal steps.", ["etl", "0 13 * * *", "run_daily"], [], "etl.migrate (a start-script step) is a bonus, not required.")
case("e", ["L2-310", "L2-313"], "When does the `feeds` service run and what does it do?",
     "Railway cron `0 21 * * *` UTC via scripts/deploy/run_feeds.sh: etl.feeds run (Upstack, Warehance, Meta, ExpandFi feeds), etl.amazon_mtd, then etl.feeds verify.", ["0 21 * * *", "feeds"])
case("e", ["L2-320"], "How do the 'Meta spend today' and 'NC revenue today' cards on the Pacing page fill themselves, and how often?",
     "Railway cron service `live` runs etl.live_today hourly (0 * * * *): it asks Upstack for the Pacific day so far and stores one snapshot row in proj_live_today_snapshots; Upstack trails real time by 30-60 minutes, so early-day projections lean low.", ["live", "hourly", "Upstack"])
case("e", ["L2-335", "L2-346"], "How is the database backed up, when, and where do the dumps go?",
     "Railway cron service `backup` (30 8 * * *, 08:30 UTC daily) runs etl.backup: pg_dump, uploads to the Shared Drive 'Lonvera DB Backups', verifies the Drive copy and restores it into a scratch Postgres, keeps 7 days; a failure fails the run and writes an etl_runs row.", ["backup", "30 8", "Lonvera DB Backups"])
case("e", ["L2-330", "L2-332"], "What tags newly launched ads with cluster / ICP / pain point, when does it run and how many ads per run?",
     "etl.auto_tag, a non-fatal step of the nightly `etl` service (after run_daily etc.); one Sonnet call per ad family, writes linemap tags tagged 'auto', never overwrites an existing tag; cap 400 ads per run (--limit default 400).", ["auto_tag", "400"])
case("e", ["L2-304"], "Which API endpoint returns the key marketing metrics by day, week or month with the change vs the previous period, and which page uses it?",
     "GET /api/trends (the page /trends); /api/trends/plan gives the month-grain projected / target / % diff from the Pacing page.", ["/api/trends"])
case("e", ["L2-294"], "How does the web service start and what is its health check?",
     "scripts/deploy/start_web.sh: etl.migrate first, then gunicorn with 2 workers x 8 threads and a 60 s timeout; health check /healthz.", ["/healthz", "gunicorn"])
case("e", ["L2-310", "T_17-9"], "Which of the nightly etl steps are non-fatal, i.e. do not fail the run?",
     "competitor_ads (--if-sunday), creative_priorities (--if-sunday), assignment_tags and auto_tag; run_daily, upstack_daily, stage_moves, projection_daily and amazon_mtd are fatal.", ["auto_tag", "assignment_tags"])
case("e", ["L2-297"], "Is there a Railway service for the launch worker, and what schedule does it have?",
     "No: the launch worker code (etl.launch_worker, scripts/deploy/run_launch_worker.sh) exists but is wired to no Railway service; it has no schedule.", ["no", "not wired"], ["runs nightly"])
case("e", ["L2-291", "L2-292"], "Which service posts failure and staleness alerts, how often and where?",
     "Railway cron service `alerts`, hourly (0 * * * *), runs etl.alerts and posts to Slack (SLACK_ALERT_WEBHOOK_URL on that service only).", ["alerts", "hourly", "Slack"])
case("e", ["L2-278"], "How does an agent reach the brain over HTTP, and what authentication does it need?",
     "POST /mcp (stateless streamable HTTP, JSON-RPC: initialize, ping, tools/list, tools/call); needs a signed-in session or a Bearer key (a personal key made on /connect-agent), never vendor tokens.", ["/mcp", "key"])
case("e", ["L2-287", "L2-313"], "Where does the Pacing / projection model read its Upstack, Warehance and Meta feed data from now, and how do you roll a feed back?",
     "From the server-side feeds (proj_feed_* tables fed by the `feeds` service) since 14 feeds were flipped to direct mode on 2026-11-04; roll back one feed with `etl.feeds mode <feed> sheet`.", ["direct", "etl.feeds mode"])


def main() -> None:
    for i, c in enumerate(C, 1):
        c["id"] = f"{c['type']}{sum(1 for x in C[:i] if x['type'] == c['type']):02d}"
    rng = random.Random(SEED)
    split = {}
    for t in "abcde":
        ids = [c["id"] for c in C if c["type"] == t and not c["split"]]
        rng.shuffle(ids)
        n_test = round(len(ids) * TEST_SHARE)
        for j, cid in enumerate(ids):
            split[cid] = "test" if j < n_test else "tune"
    for c in C:
        c["split"] = c["split"] or split[c["id"]]
    cols = ["id", "split", "type", "source_item_ids", "question", "as_of", "expected_answer", "must_mention", "must_not", "grading_notes"]
    (HERE / "cases.jsonl").write_text("\n".join(json.dumps({k: c[k] for k in cols}, ensure_ascii=False) for c in C) + "\n")
    held = sorted({i for c in C for i in c["source_item_ids"]})
    (HERE / "holdout.json").write_text(json.dumps({"source_item_ids": held}, indent=1))
    by: dict = {}
    for c in C:
        by.setdefault(c["type"], {"tune": 0, "test": 0})[c["split"]] += 1
    print(len(C), "cases", by, len(held), "held-out items")


if __name__ == "__main__":
    main()
