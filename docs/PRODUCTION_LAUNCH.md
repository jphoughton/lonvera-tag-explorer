# Launching ads from the Production page (dark by default)

## What it does

When a creative on the Creative priorities Production page is marked Ready and has its creative link (a Google Drive file or folder), you can turn it into real Meta ads without opening Ads Manager. Everything it creates is PAUSED. Nothing spends a dollar until you switch ads on yourself in Ads Manager. It never edits the Google Sheet; it only gives you the Concept IDs it minted so you can paste them into the tracker.

## The flow

1. Ready: the row is marked Ready with its creative link and the launch form filled in (style, fidelity, proof, Page, ad copy).
2. Prepare launch: the worker lists the files behind the link (one file is one ad; a folder gives one ad per image or video, at most 10), mints one Concept ID per ad and writes a plan. Nothing is sent to Meta.
3. The plan: you read the exact ad names, the ad set (reused if one already exists for that product, vendor and week; otherwise a new PAUSED copy of the product's template ad set), the ad copy, the link and anything skipped. The plan carries a fingerprint (plan hash). If anything changes after you read it, Create paused ads refuses and you prepare again.
4. Create paused ads: the worker uploads each file, builds the creative, then creates the ad, one ad at a time. Each step is saved, so a crash or a retry picks up where it stopped and never makes a duplicate (an ad already in Meta under the exact name is adopted, not recreated). One ad failing does not stop the others; the failed ones keep a plain-English error and Retry failed re-runs only those.
5. Ads Manager: each created ad shows a link to it in Ads Manager, paused. You review and switch on. The new Concept IDs are added to the assignment's tracker list.

Safety rails: every ad and ad set is PAUSED; at most `META_LAUNCH_MAX_ADS_PER_DAY` ads a day (default 50); new work waits while Meta usage is at 75 percent or higher; Cancel works only while a job is still waiting to start; tokens never appear in logs, errors or the audit table; every step writes an audit row.

## Where it stands today

The feature is built and tested with fake Meta and Drive clients. It is dark: the flag is off, there is no launch token, no Railway `launch` service, and the worker is not scheduled anywhere. No real Meta or Drive call has ever been made by it.

## Checklist to switch it on for real

1. Create a system user in Meta Business settings for launching and generate a token with the scopes `ads_management`, `pages_read_engagement` and `pages_manage_ads`. Assign it the ad account(s), the Pages and the Instagram accounts it will use. It is separate from the nightly read token, which is never used for launching. Keep the token in Railway only, never in a file or in chat.
2. In Railway create a service named `launch` from this repo with start command `scripts/deploy/run_launch_worker.sh`. Give it the database URL, the Google service account variables (same as the other services) and `META_LAUNCH_TOKEN`.
3. Set `META_LAUNCH_ENABLED=1` and `META_LAUNCH_USERS=<comma list of sign-in names>` on the web service (the launch buttons and routes) and `META_LAUNCH_ENABLED=1` on the `launch` service. Leave `META_LAUNCH_DRY_RUN` unset at first: a dry run builds plans and never writes to Meta. Set it to `0` on both services only when you want real PAUSED ads.
4. Fill in the per-product settings through the settings API (`PUT /api/priorities/launch/settings/<product>`): ad account, campaign, template ad set (the existing ad set a new weekly ad set is cloned from), daily budget and bid for new ad sets, landing page, URL tags, call to action, default Page and default ad copy. Add the Pages (Page id, Instagram account, the name token used in ad names) and the vendor name prefixes used in Concept IDs.
5. Drive access: the service account must be able to read each folder or file that will be linked. As of 2026-11-10 a read-only check showed the service account can see the shared drive `Image Ads` (id `0AORg_VVGftwAUk9PVA`) and list its top-level folders. Sharing was not changed. Any Drive folder outside that shared drive needs to be shared with the service account as Viewer.
6. One validate-only proof before the first real launch: with the real token, call `create_creative` and `create_paused_ad` in `etl/meta_write.py` with `validate_only=True` for one real ad account, Page and uploaded image. Meta checks the request and creates nothing. Only when both come back clean, switch `META_LAUNCH_DRY_RUN` to `0`.
7. First real launch: use one file, confirm it appears paused in Ads Manager under the right ad set with the right name, then launch normally.

Turning it back off: unset `META_LAUNCH_ENABLED` (routes return 404 and the worker exits). Anything already created stays paused in Meta.
