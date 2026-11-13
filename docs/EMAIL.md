# Email (`/email`)

Klaviyo email and text message (SMS) results by flow, by campaign and over time, in the Performance section next to Trends. Code: `app/email_view.py` (all numbers), `app/static/js/pages/email.js` (page), `app/static/css/email.css` (`eml-` classes). Data: `klaviyo_daily` and its dimensions (migration 206, loaded nightly by `etl.klaviyo`). No new tables or migrations.

## Endpoints (read-only, signed-in session or API key, same behaviour as the Trends endpoints)

Common parameters: `window=7d|30d|90d|all` (default 30d), `kind=all|flows|campaigns` (default all; not `source`, which already means Meta or Upstack in this app), `channel=all|email|sms` (default all). Anything else is a 400.

| Route | Answers |
|---|---|
| `GET /api/email/summary` | totals for the window, prior-period totals and the change per metric, the window, `flags` |
| `GET /api/email/timeseries` | `grain=day\|week\|month` (default day), `split=1` (flows and campaigns separately, only with `kind=all`); one bucket per period with every metric and `partial` |
| `GET /api/email/flows` | one row per flow with activity in the window, revenue first, share of flow revenue, a total row |
| `GET /api/email/flows/<flow_id>/messages` | the flow's messages with totals, subject, `has_copy` |
| `GET /api/email/campaigns` | one row per campaign with activity in the window, newest send first; `q=` filters name and subject |
| `GET /api/email/message/<message_id>` | one message: copy (latest version only), totals, a zero-filled daily series; 404 when unknown |

An empty `klaviyo_daily` answers `{"empty": true}` (summary, timeseries, flows, campaigns) or 404 (flow messages, message), never a 500. One parameterised SELECT per request pulls the daily rows (18.5K rows for 120 days, primary key leads with the day); the arithmetic is Python. About 10 to 40 ms on production-sized data locally.

## Definitions

* **Days** are Pacific days as stored. **Weeks** are ISO weeks (Monday to Sunday), **months** calendar months. Window `Nd` = the last N loaded days; `all` = every loaded day (first loaded day is 2026-07-13, so there is no "since launch").
* **Every rate is a ratio of sums**, never an average of daily ratios.
* **Open rate** = unique opens / delivered **email** (text messages have no opens, so they stay out of the denominator; text only shows a dash). **Click rate** = unique clicks / delivered (email and text). **Click to open** = email unique clicks / unique opens. **Revenue per delivered** = revenue / delivered. **Orders per delivered** = orders / delivered. **Unsubscribe rate** = unsubscribes / delivered over everything whose unsubscribes Klaviyo reports (flow text messages are left out of numerator and denominator; flow text only shows a dash). **Opens (views)** = unique opens, summed over days.
* **Approximate rates.** `unique_opens` and `unique_clicks` are per-day uniques; summed over days they overstate the window's true uniques (about +22% campaign email, +54% flow email on unique opens). Apple Mail Privacy Protection also inflates opens. The page labels open rate, click rate and opens as approximate and tells the reader to read click rate first.
* **Revenue and orders are Klaviyo-attributed** ("Placed Order - Upstackified"), dated by order day, include open-attributed orders, and are **not additive** with Meta, Google or Upstack revenue. The page says so next to every revenue figure and never relates it to ad spend.
* **Change vs prior period**: the same number of days straight before the window, shown only when every one of those days is loaded (so 90d and all show no comparison with the current 120 loaded days). Counts and money in percent, rates in percentage points. Polarity: unsubscribe rate up is bad, the rest up is good, volumes neutral.
* **Partial buckets**: a week or month that runs past the last loaded day (yesterday, Pacific) or starts before the first loaded day carries `partial: true` and `partial_why`; the chart draws it in a second colour (bars) or dashed (lines) and names it under the chart. A partial bucket is never plotted as a plain drop.
* **Campaigns table**: numbers are what happened in the window, so a campaign sent before the window shows only its later opens, clicks and orders (`sent_in_window: false`, badge "sent earlier"). Send date = Klaviyo's scheduled send time in Pacific, else the first loaded day with sends. A campaign with several messages (A/B) opens its largest message. A campaign Klaviyo no longer lists keeps the name it was loaded with.
* **Message detail** reads all loaded days by default (a campaign sent in August would read zero in the last 30 days); a switch ties it to the page window. Copy is the latest version only (`klaviyo_message_content.fetched_at` moves only when the text changes). A message with no content row (11 deleted flow messages) shows "Copy not available"; one with a row but no text is "mostly images". No Klaviyo link is shown: there is no reliably derivable message URL.

## Known gaps (shown on the page)

* Klaviyo does not expose flow text message unsubscribes, and 1,389 campaign text message unsubscribes cannot be tied to a campaign: the page says text unsubscribe figures are incomplete whenever text messages are in view.
* The last loaded day is yesterday; the last few days keep rising because Klaviyo records events late (the nightly job reloads the trailing 14 days).

## Tests

`tests/api/test_api_email.py` (own database `<TEST_API_DB>_email`; every expected number from plain Python over the generated rows, `MemSource` proves the pure builders equal the database path), `tests/ui/test_email.py` against `tests/ui/mock_email.py` (the real builders over made-up days).

Message dialog: the Subject, Preview text and From name lines each appear only when Klaviyo returned that field; a missing field shows no line and no label (the page never prints null or undefined).
