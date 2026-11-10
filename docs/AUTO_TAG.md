# Auto-tagging of new ads (`python -m etl.auto_tag`)

New ads launched after the last manual tagging cycle show as `(untagged)` for cluster / ICP / pain point / trigger. This job gives each of them a
Communication Overview line every night, on Railway, with no laptop involved.

## What it does
1. Finds ad families (tracker rows grouped by `family_key`) of Vita Bull, Beef Liver and Boneflex that have **no current `linemap` tag** and have spend
   since launch or a launch date in the last 60 days. Biggest spend first. Families that returned garbage 3 nights in a row (`app_meta autotag_fail:<product>:<family>`)
   are left alone; families with neither a picture nor any words are not sent.
2. Fetches missing thumbnails from Meta first (`etl.assets_pull`, best effort), then gathers the copy (tracker primary text / headlines / description, what ran on Meta,
   hook, start of the transcript) and the stored thumbnail (`creative_assets`).
3. One `claude -p` call per ad family (Sonnet, `--tools Read`, no Bash / network tools, nothing persisted). The cwd is a fresh temp dir holding the picture
   (`ad.jpg`); the prompt carries the product's line catalog and the tagging rules and asks for one strict JSON object
   `{line, confidence, runner_up, why, saw}`. The answer is parsed and the line code validated against the catalog (3 tries, 5 s / 20 s backoff; an
   invalid code is retried, then skipped).
4. Writes `ad_tags(tag_set='linemap', version='auto-YYYY-MM-DD', tagger='auto-claude-sonnet-5-5', extra.source='auto')` on every still-untagged row of the family.
   **It never overwrites or retires an existing tag**: only rows with no current linemap tag are filled, and a later `etl.load_tags` cycle replaces the auto tags the
   normal way (it marks other versions not-current). Re-running the same day finds nothing new; `--retag-today` redoes only that day's own auto rows.

Methods and caps: static image = `image + copy (auto)`; video = `cover + copy (auto)` (cover frame, copy, hook, transcript start; no ffmpeg / Whisper) and
confidence capped at **medium**; no picture = `text only (auto)`, capped at medium (the app already shows a "copy only" caveat for methods starting `text only`).

## Reviewing
The app already shows each tag's confidence and method (ad page, tag lookup). Auto tags are the rows with `tagger = 'auto-claude-sonnet-5-5'`
(or `extra->>'source' = 'auto'`); low-confidence ones carry `extra->>'needs_review' = 'true'`:

    SELECT t.row_key, g.confidence, g.method, tl.line_code, g.why FROM ad_tags g JOIN tracker_rows t ON t.id=g.tracker_row_id
    LEFT JOIN taxonomy_lines tl ON tl.id=g.line_id WHERE g.tag_set='linemap' AND g.is_current AND g.extra->>'source'='auto' AND g.confidence='low';

No UI change was made. A blind-agreement number for auto tags does not exist yet: the manual cycles measured 87% / 78% (Beef Liver static / video) and 79% (Vita Bull)
with the creative fully looked at; expect video ads (cover only) to be weaker.

## Setup (once)
1. On a machine where Claude Code is logged in with Josh's subscription: `claude setup-token` and copy the token it prints (starts `sk-ant-oat…`).
2. Railway > etl service > Variables: `CLAUDE_CODE_OAUTH_TOKEN=<token>`. Never commit it or paste it in chat. The job passes only a whitelist of env vars to the CLI
   (no database url, no Meta / Upstack tokens), and `db.scrub` strips the token from logs and `etl_runs`.
3. The Dockerfile installs Node 22 and `@anthropic-ai/claude-code@2.1.321` (pinned, bump deliberately). The web service shares the image, so it carries the CLI too (about +200 MB).

**Cost = plan usage, not dollars.** Every ad is one Sonnet call with a ~14K-character prompt plus one picture; roughly 7-15 s each. A 400-ad run is about 55-100 min of wall clock and counts
against Josh's plan limits. `etl_runs` job `auto_tag` stats record calls, attempts, input / output / cache tokens, duration, tagged families / rows, by product and confidence.
Backlog on 2026-11-10 (read from prod via the analytics reader): 232 qualifying untagged families (Beef Liver 130, Boneflex 50, Vita Bull 52), so the first two nights are catch-up,
then it is the week's new ads .

## Failure behaviour
* `CLAUDE_CODE_OAUTH_TOKEN` missing -> `etl_runs` row `auto_tag` = failed with a clear message, nothing tagged.
* Auth error ("Not logged in", expired token) or usage / rate limit -> the run **stops at once** (no retries, no hammering), the tags made so far are kept, the run is recorded failed.
* CLI missing from the image -> same, recorded failed.
* In `scripts/deploy/run_etl.sh` the step runs **last** and is non-fatal: it never changes the nightly exit code and never delays the data steps. Check `etl_runs` where `job='auto_tag'`.
* Hard cap per run: `--limit` (default 400 ads).

## Manual runs
    python -m etl.auto_tag --dry-run                 # counts candidates per product, shows 2 sample units per product; no calls, no token needed
    python -m etl.auto_tag --product boneflex --limit 20
    python -m etl.auto_tag --retag-today --no-assets

## Refreshing the line catalogs
`etl/autotag/catalogs/<slug>.json` (copied from the three manual cycles, no secrets) is the code list the model must choose from; the nightly job uses the committed copy.
After Josh edits the Communication Overview:
1. `python -m etl.load_taxonomy` (reads the sheet into `taxonomy_lines`; line codes stay stable).
2. `python -m etl.autotag.refresh_catalog` (dry run: prints added / removed / changed lines), then `--write`, commit, deploy.
A code the model returns that is in the catalog but not in `taxonomy_lines` is skipped and logged. Lines added to the sheet after the cycles (e.g. Beef Liver B64-B66) are not taggable until the refresh.
The tagging rules live in `etl/autotag/instructions.md`, the per-product blurbs in `etl/auto_tag.py` (`INTROS`).

## Verification done / not done
* Unit tests (`tests/etl/test_auto_tag.py`, `claude` mocked): selection, never overwriting, invalid code, idempotence, missing token, auth / limit stop, cap, usage stats, caps, env whitelist, command flags.
* Real CLI 2.1.321, one call with a generated picture, run from this Mac with Josh's own login: flags accepted, picture read, JSON envelope parsed, ~7 s, correct line (BF18).
  With the env whitelist and no credentials the real CLI answers "Not logged in", which the job turns into a clean stop.
* **Not verified:** the `CLAUDE_CODE_OAUTH_TOKEN`-only path (no token was available), the Docker build (no Docker here), a run on Railway, and quality of video tags.
