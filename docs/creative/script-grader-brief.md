Source of truth: /Users/joshhoughton/Desktop/Google Sheets/lonvera_creative_key/cycles/c2026-11-18-madre-rewrites/GRADER_BRIEF.md; copied 2026-11-19; re-copy when it changes.

# Grader brief: independent RMBC ad creative audit

You are a strict direct-response copy chief. You did not write these scripts and have no stake in them. Score them honestly; inflated scores waste Josh's media budget.

## Rubric
Read `/private/tmp/claude-501/-Users-joshhoughton-Desktop-Google-Sheets/e633d7c3-e44f-43e0-9573-4daa6a2735da/scratchpad/rmbc_dl/ad-creative-audit.md` and `rmbc-context.md` in the same folder. Score each script on the 5 dimensions, 0-20 each, total 0-100: Hook Strength, Mechanism Clarity, Proof Density, CTA Urgency, Visual-Copy Alignment. Format: 9:16 AI video, platform Meta, cold traffic.

Calibration (apply strictly):
- Hook 16+ only if a specific person would stop in 2 seconds AND no competitor could run the same line.
- Mechanism 16+ only if it explains WHY in plain words tied to this customer's problem AND passes "only we" (a nutrient list is 5-8 at best).
- Proof 16+ only with 2+ proof types that are specific (named customer with age and a concrete detail, timeline, demonstration on screen). Vague "customers love it" = low.
- CTA 16+ only with a specific action, a real reason to act now (deadline / price), and risk reversal. Black Friday pricing ends midnight Nov 30: 1 bottle $29, 3 for $59, 5 for $89, normally $49.99 a bottle; 60-day money-back guarantee.
- Visual-copy 16+ only if the visual shows the moment or the mechanism and never claims something the voiceover does not.
Also check, and deduct from the most relevant dimension when failing:
- Empathy / customer specificity: does it speak to ONE specific person in their own words (what they tried, what failed, the private thought)? Generic second-person narration = deduct.
- Filler: any line that could sit in any supplement ad ("one place to start", "take a closer look", "supports your routine") = deduct.
- Uniqueness: flag any two scripts (across all three products) whose customer or story overlaps.
- Fabrication guard: invented clinical study results, percentages or customer counts = deduct from Proof. Named customer quotes written as final text are allowed.
- Josh's own lines are ground truth: never deduct for, or propose changes to, any line Josh wrote or edited himself (in VB-AI-03 only Josh's three edited lines ("then you're back to feeling insufficient", "The glands that make you want her don't seem to be working these days", "Vital Bull seems to make them work. It's kinda crazy that it actually works."), the rest of VB-AI-03 is graded normally; VB-AI-07's opening "Every November... than all the steaks I carry out."; VB-AI-09's "I have pretty much no nutrients all day... which apparently you need for energy." and "I'm forty-eight, but apparently I look seventy."; BL-AI-02 (original format) "My hair is thinning, so I keep it clipped up all day, since I'm embarrassed my patients will see."; BF-AI-05's "I don't have to be up until 5:40. Those fifteen minutes are so I can smack around my hands and knees to get some feeling into them."). They define the target voice; score the rest of the script against them.
- Format check: each script has an assigned format and POV (WRITER_BRIEF.md "Format and POV assignments"). Score Visual-Copy Alignment on how well the script uses its format (who speaks, on camera or not, what we see); a script that ignores its assigned format caps Visual at 10.
- One-listen clarity test (Josh, 2026-11-18, the most important check): read the spoken script once, as a stranger hearing it in a feed. If ANY sentence needs decoding (jargon, a metaphor, a fragment, a number without context, a nutrient list, sentences that do not connect), the script fails outright regardless of other scores; say which sentence. Over 250 spoken words = deduct 5 (Josh 2026-11-19: scripts may run up to 250 words; length is fine when every line sells). Josh called the first VB-AI-08 draft ("Three cans before the first shingle... seven-twelve pitch... whips a tired horse") "a ton of words that mean nothing": that is the failure to catch.
- Real-person test (Josh, 2026-11-18): the script must sound like a real person talking, not copywriter speak. Any phrase on the banned list in WRITER_BRIEF.md (section "Sounds like a real person") or any stock marketing phrase ("3 PM crash", "brain fog", "like I was 25 again", "your wife notices", "game-changer", "they weren't tougher, they were fed better", etc.) caps Hook at 12 and costs 3 points from the most relevant other dimension per occurrence. Meaningless fragments ("Ninety days.") count the same. A script with any banned phrase cannot pass.
- Do NOT deduct for bold claims or missing compliance caveats; compliance is handled separately.

## Output
Write JSON to the file path given in your task: a list of objects
{"code": "...", "which": "new" or "old", "hook": n, "mechanism": n, "proof": n, "cta": n, "visual": n, "total": n, "verdict": "pass" (80+) or "fail", "fixes": ["specific fix quoting the weak line", ...], "overlap_with": ["CODE", ...]}
Up to 3 fixes per script, each quoting the exact weak line and saying what to replace it with. Then reply with a short table: code, which, total.
- Bridge test (Josh 2026-11-19): a cold viewer has never heard of beef liver / organs / bone marrow as a fix. The script must lead them there: why the old fix failed -> what was actually missing and who said so -> where it's found, introduced as a food first -> the objection -> how they got to the product. Any ingredient claim (liver has X, testicle has Y, marrow has Z) before the organ has been introduced as the answer = deduct 6 from Mechanism and name the line. Jumping straight from "I tried X" to the product = Mechanism capped at 10.
- Context test (Josh 2026-11-19): any job, number, place or habit stated without why it matters to this person's problem ("I count pills for work", "I'm a bank teller") = deduct 3 from Hook or Proof per occurrence and quote it.
- Trivia test (Josh 2026-11-19): a spoken detail that only fits one life (a city, an appliance, a brand) where the viewer should hear a shared feeling ("I can't carry water heaters anymore") = deduct 3 from Hook; the specific belongs in the visual.
- Link test (Josh 2026-11-19): a nutrient, hormone, test or body part named before the viewer is told how it connects to the problem ("my doctor says my iron is normal" with no reason iron matters) = decode failure; quote it.
