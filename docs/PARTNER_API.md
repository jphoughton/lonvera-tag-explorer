# Lonvera creative partner API

For outside creative partners (agencies, studios, freelancers). Use it to send us your concepts as tickets, to add your creative to those tickets as links, and to look at what is working so your next concepts are better. Everything you send shows up on Lonvera's Production board the moment you send it.

Base URL: Josh sends you the address together with your key. In every example below it is written `$LONVERA_URL`; set it once in your terminal, for example `export LONVERA_URL=https://partner-production-62a3d596.up.railway.app`, and your key as `export LONVERA_KEY=lvk_...`.

## Your key

Josh gives you one key. It looks like `lvk_` followed by 40 characters. Send it on every request:

```
Authorization: Bearer lvk_...
```

Keep it private, like a password. Your key belongs to one company only (for example Madre or Victoria): every ticket you send is filed under your company, and you can change only your own company's tickets (you can read all of them). If a key leaks, tell Josh and it is switched off at once; a new one is made.

## What you can do

| Do this | Call |
|---|---|
| Send one ticket, or up to 100 at once | `POST /api/priorities/tickets` |
| See your tickets | `GET /api/priorities/partner/tickets` |
| Fix a ticket | `PATCH /api/priorities/partner/tickets/<id>` |
| Add creative (links) to one ticket | `POST /api/priorities/partner/tickets/<id>/creative` |
| Add creative to many tickets at once | `POST /api/priorities/partner/creative` |
| See every company's tickets (read only) | `GET /api/priorities/partner/all-tickets`, one ticket: `.../all-tickets/<id>` |
| Check the rules (your company, allowed values, limits) | `GET /api/priorities/partner/contract` |
| Look at what is working | see "What you can read" below |

## Sending a ticket

A ticket needs only two things: a **name** (`concept_name`) and a **brief** (`brief`, a few sentences saying what the concept is, and/or `brief_url`, a link to the brief). Your company is filled in from your key.

**Send the name and a short brief and we fill in product, market cluster, ICP, pain point and new vs iteration for you; send any of them yourself to keep your value.** Anything you send is kept exactly as you sent it; we only fill in what is missing. In `GET` your ticket shows `"tags": "pending"` while we are filling in the rest and `"complete"` when done.

| Field | Needed? | What goes in it |
|---|---|---|
| `concept_name` | yes | The title of the concept, up to 200 characters. |
| `brief` and/or `brief_url` | yes, at least one | `brief`: what the concept is, in plain text. `brief_url`: an https link to the brief document. |
| `vendor` | no | Leave it out. If you send it, it must be your own company. |
| `product` | no | `vita-bull`, `beef-liver`, `boneflex` or `vita-belle`. |
| `cluster` | no | The market cluster, for example `Pain-Relief Alternatives`. |
| `concept_kind` | no | `new` or `iteration`. |
| `variations` | no | A list of `{label, url?, concept_id?}`, for example Original, Hook 2. Up to 40. |
| `icp`, `pain_point` | no | The buyer and the pain, as text. |
| `story` | no | A longer version of the brief (up to 20,000 characters). |
| `for_week` | no | Any date in the week the concept is for; it moves to that week's Monday. Format `2026-11-16`. |
| `due_date`, `format_code`, `note` | no | Due date (`2026-11-20`), creative format (`F1` to `F6`), a short note (500 characters). |
| `status` | no | `assigned` (default), `brief_sent` or `in_production`. |

Your tickets start in the column you choose with `status`. Moving them further is done by the Lonvera team; you can send a ticket to review with `submit` (see "Adding the creative").

The smallest ticket:

```bash
curl -s $LONVERA_URL/api/priorities/tickets \
  -H "Authorization: Bearer $LONVERA_KEY" -H "Content-Type: application/json" \
  -d '{"concept_name": "Grandma back on the floor", "brief": "A grandmother plays on the floor with her grandkids and gets up without a hand. Warm, real kitchen footage."}'
```

A full ticket, with every optional field:

```bash
curl -s $LONVERA_URL/api/priorities/tickets \
  -H "Authorization: Bearer $LONVERA_KEY" -H "Content-Type: application/json" \
  -d '{
    "concept_name": "Grandma back on the floor",
    "brief": "A grandmother plays on the floor with her grandkids and gets up without a hand.",
    "brief_url": "https://docs.google.com/document/d/ABC/edit",
    "product": "boneflex",
    "cluster": "Pain-Relief Alternatives",
    "concept_kind": "new",
    "icp": "Grandmother who is afraid to get down on the floor",
    "pain_point": "Joint Pain & Mobility Loss",
    "for_week": "2026-11-16",
    "variations": [{"label": "Original", "url": "https://drive.google.com/file/d/ID/view", "concept_id": "Madre-BoneFlex-Video-1.1"}, {"label": "Hook 2"}],
    "status": "in_production"
  }'
```

You get the ticket back (status 201) with its `id`. Keep the id: you need it to add creative.

Three tickets in one request (all or nothing: if one is wrong, none is saved and the message says which, like `ticket 2: ...`):

```bash
curl -s $LONVERA_URL/api/priorities/tickets \
  -H "Authorization: Bearer $LONVERA_KEY" -H "Content-Type: application/json" \
  -d '{"tickets": [
    {"concept_name": "Concept A", "brief": "What A is."},
    {"concept_name": "Concept B", "brief": "What B is.", "product": "beef-liver"},
    {"concept_name": "Concept C", "brief_url": "https://docs.google.com/document/d/DEF/edit"}
  ]}'
```

## Seeing what you sent

```bash
curl -s "$LONVERA_URL/api/priorities/partner/tickets?status=in_production&week=2026-11-16&limit=100" \
  -H "Authorization: Bearer $LONVERA_KEY"
```

Filters (all optional): `status` (`assigned`, `brief_sent`, `in_production`, `in_review`, `ready`, `launched`), `week` (any date in the week), `limit` (up to 500, default 100) and `offset` for the next page. You only get your own company's tickets, with: `id, concept_name, product, cluster, icp, pain_point, concept_kind, variations (label, url, concept_id), vendor, for_week, due_date, status, created_at, updated_at`, plus `tags` (`pending` or `complete`) and `needs_creative` (`true` when none of the variations has a link yet, so you can see what is still missing). Dropped tickets are not listed.

## Seeing everyone's tickets (read only)

You can look at every company's tickets, not only your own, so you can see what is already in production and avoid doubling up. This is read only: you can never change, add creative to, or comment on another company's ticket (those calls answer `404 Ticket not found.`).

```bash
curl -s "$LONVERA_URL/api/priorities/partner/all-tickets?vendor=Studio%20Flow&status=in_production&week=2026-11-16&product=boneflex&limit=100" \
  -H "Authorization: Bearer $LONVERA_KEY"
```

Filters (all optional): `vendor`, `status`, `week`, `product`, `limit` (up to 500, default 100), `offset`. Each ticket comes as the Lonvera team sees it: name, product, cluster, ICP, pain point, the brief and story text, notes, variations with their links, due date, status, spend and return of its ads, and its history. `GET /api/priorities/partner/all-tickets/<id>` returns one ticket with its comments and the transcripts of its ads. Dropped tickets are left out. Names of the people or keys that made a change are not shown, and no links or keys that give access are ever included.

## Fixing a ticket

```bash
curl -s -X PATCH $LONVERA_URL/api/priorities/partner/tickets/123 \
  -H "Authorization: Bearer $LONVERA_KEY" -H "Content-Type: application/json" \
  -d '{"concept_name": "Grandma back on the floor v2", "cluster": "Menopause"}'
```

You can change `concept_name`, `concept_kind`, `cluster`, `variations` (this replaces the whole list; to add links use the creative call below), `brief_url`, `creative_url` and `story`. Anything else is refused and the message names the field.

## Adding the creative

Your creative arrives as **links**, not file uploads. Put the file on whatever you already use (Google Drive, Frame.io, Dropbox, Vimeo, YouTube, Loom, Box ...), make sure the link opens for the Lonvera team, and send the link.

One ticket, one or more variations:

```bash
curl -s $LONVERA_URL/api/priorities/partner/tickets/123/creative \
  -H "Authorization: Bearer $LONVERA_KEY" -H "Content-Type: application/json" \
  -d '{"variations": [
    {"label": "Original", "url": "https://app.frame.io/reviews/abc", "concept_id": "Madre-BoneFlex-Video-1.1"},
    {"label": "Hook 2", "url": "https://vimeo.com/123456789"}
  ], "submit": true}'
```

* **Links are merged by label.** Send Hook 2 on Monday and Hook 3 on Wednesday: both stay. Sending a label that already exists replaces its link (and its `concept_id` when you send one). Nothing is ever deleted this way.
* **Shorthand:** `{"creative_url": "https://..."}` means the Original.
* **`concept_id`** (optional) is your Creative ID for that variation; it is how we match your ad to its results later.
* **`submit: true`** sends the ticket to review ("In review") when it is In production and has a link. If the ticket is in any other status it stays where it is and the answer says so (`"submitted": false, "message": "..."`); that is not an error.
* Many tickets at once (up to 100, all or nothing; errors say `creative 3: ...`): `POST /api/priorities/partner/creative` with `{"creatives": [{"ticket_id": 123, "variations": [...], "submit": true}, {"ticket_id": 124, "creative_url": "https://..."}]}`. Every ticket must be yours.

Links must be `https://`, up to 2,000 characters, and point to a real public website. Any platform works. Some play inside the ticket for the team; the rest stay a link they click:

| Platform | Plays inside the ticket | Link shape |
|---|---|---|
| Google Drive | yes (files and folders) | `drive.google.com/file/d/ID/view`, `drive.google.com/drive/folders/ID` |
| YouTube | yes | `youtube.com/watch?v=ID`, `youtu.be/ID`, `youtube.com/shorts/ID` |
| Vimeo | yes | `vimeo.com/ID` |
| Loom | yes | `loom.com/share/ID` |
| Frame.io, Dropbox, Box, anything else | no, stays a link | any https link |

Python (with `requests`):

```python
import os, requests

BASE = os.environ["LONVERA_URL"]
H = {"Authorization": f"Bearer {os.environ['LONVERA_KEY']}"}

t = requests.post(f"{BASE}/api/priorities/tickets", headers=H,
                  json={"concept_name": "Grandma back on the floor",
                        "brief": "A grandmother plays on the floor with her grandkids."}).json()
print(t["id"], t["status"], t["tags"])

r = requests.post(f"{BASE}/api/priorities/partner/tickets/{t['id']}/creative", headers=H,
                  json={"variations": [{"label": "Original", "url": "https://vimeo.com/123456789"}], "submit": False})
r.raise_for_status()
print(r.json()["ticket"]["needs_creative"])          # False: the link is on the ticket

todo = [x for x in requests.get(f"{BASE}/api/priorities/partner/tickets", headers=H).json()["tickets"] if x["needs_creative"]]
```

## What you can read

You can read the same numbers the Lonvera team sees, for the creative and performance side of the business. Reads are `GET` requests with your key.

What you CAN read:

| Family | Routes (start with `/api/`) |
|---|---|
| Ads: one ad's page, spend, return, hook, tags, transcript | `ads/<id>`, `ads/<id>/preview`, `meta-ads/<id>/preview` |
| Products: roll-ups, ad families, the tag tree, ads, campaigns, daily stage, trend | `products`, `products/<product>/rollup`, `.../families`, `.../tree`, `.../ads`, `.../campaigns`, `.../trend`, `.../stage-daily`, `.../coverage`, `.../unmatched` |
| Campaigns and platforms: Meta and Google performance, trends | `campaigns`, `campaigns/all`, `campaigns/trend`, `platforms/overview`, `platforms/kpis`, `platforms/trend`, `google/rollup`, `google/trend`, `trends` |
| Test to scale: which tests reached scaling | `promotions`, `promotions/scale` |
| Meta by placement, age, device and country | `meta/breakdowns`, `meta/breakdowns/coverage` |
| Tags and hooks lookup | `tag-lookup?q=<ad name or Concept ID>` |
| Landing pages and their top ads | `landing-pages`, `landing-pages/<id>` |
| Competitors and their concepts | `competitors`, `competitors/<brand>` |
| The weekly ideas list, what is working on one line, the tag options (product, cluster, ICP, pain point) | `priorities`, `priorities/lines/<id>/insights`, `priorities/manual/options` |
| Thumbnails and creative files | `/media/<id>` |

Which ads are working (Boneflex, last 30 days, best first):

```bash
curl -s "$LONVERA_URL/api/products/boneflex/ads?window=30d" \
  -H "Authorization: Bearer $LONVERA_KEY"
```

Products in the URL are `vita-bull`, `beef-liver`, `boneflex`, `vita-belle`. The numbers are Meta-reported unless a view says otherwise.

What you can NOT read (you get a plain 403): finance and planning (pacing, P&L, cash, financing, scenarios), inventory and purchase orders, Amazon fees and profit, email and SMS revenue, Shopify orders and customers, the team board, vendor links and keys, anything else not in the table above. If you need something that is not listed, ask Josh.

## Full read access (only if Josh turned it on for your key)

Read access is chosen per key. A normal partner key reads the creative and performance data listed above. If Josh upgrades your key to **full read access** (scope `partner_read_all`; the key itself does not change, it just starts working on more), you can read everything the Lonvera app serves, read only: finance and planning (pacing, P&L, cash, financing, scenarios), inventory and purchase orders, Amazon, email and SMS, the daily story, status and tie-out, the production board, all assignments and capacity, launch settings, and the CSV export. Your writes do not change: you still create tickets and add creative only for your own company, and another company's ticket on a write call is still `404 Ticket not found.`

Still blocked for every partner key, always, because they hand out access: saved-view links and their tokens (`/api/priorities/views` and `/api/priorities/views/<token>` and `.../csv`), vendor link lists and vendor link pages' tokens (`/api/priorities/vendor-links`, `/api/priorities/vendor/<token>...`), the agent key route (`/api/brain/key`), and key administration (there is no route for it at all). Nothing that returns a key, a key hash, a password or a setting that is a secret is served.

To see which one your key has, ask `GET /api/priorities/partner/contract` (it shows `read_scope`).

## Connect your AI tool (full read access keys only)

Keys with full read access can also connect an AI tool to the Lonvera data brain, so the AI can discover our data schema and endpoints itself. It is read only: it can only run `SELECT` queries on the analytics views, with row and time limits, and it cannot change anything.

Claude Code:

```bash
claude mcp add --transport http lonvera "$LONVERA_URL/mcp" --header "Authorization: Bearer $LONVERA_KEY"
```

Any other client that speaks MCP over HTTP (generic settings JSON):

```json
{
  "mcpServers": {
    "lonvera": {
      "type": "http",
      "url": "<your LONVERA_URL>/mcp",
      "headers": { "Authorization": "Bearer <your key>" }
    }
  }
}
```

After connecting, tell your AI to work in this order: **`catalog`** first (what data exists, by area), then **`describe`** a view or table (columns, meaning, caveats), then **`sql`** (one `SELECT` on what it just described), and **`app_map`** for the list of routes, pages and jobs. `metric`, `search`, `why`, `why_tree`, `past_questions` and `freshness` are there when it needs a definition, a lead on why something moved, or to check how fresh the data is. A key without full read access gets `403` on `/mcp`.

## Messages and what they mean

| Message | Meaning |
|---|---|
| `401 unauthorized` | The key is wrong, switched off, or not sent as `Authorization: Bearer lvk_...`. |
| `403 This API key can only push tickets ...` | Your key cannot use that route. See the lists above. |
| `403 This key can only create tickets for <Company>` | You sent a different `vendor`. Leave it out. |
| `404 Ticket not found.` | No such ticket for your company (it may belong to someone else, or not exist). |
| `409 This ticket was dropped.` | The team dropped the ticket; it can no longer be changed. |
| `400 missing required field: concept_name` / `brief ...` | The ticket needs a name and a brief (text or link). |
| `400 field not allowed: <name>` | You tried to change a field that is not yours to change. |
| `400 variations[2].url: link must start with https://` | A link is not valid. The message names which one. |
| `400 ticket 3: ...` / `400 creative 3: ...` | In a batch, item number 3 is wrong; nothing was saved. |
| `400 status must be one of: ...` | You may start tickets in `assigned`, `brief_sent` or `in_production` only. |
| `413` | The request is bigger than 1 MB. |
| `429 rate_limited` | Too many writes. Wait `Retry-After` seconds. |

## Limits

* 60 writes per minute per key (reads do not count). A batch counts as one write.
* Up to 100 tickets or 100 creatives per batch, up to 40 variations per ticket, requests up to 1 MB.
* Links: https only, up to 2,000 characters.

## What your key can NOT do

It cannot read anything outside the tables above, change another company's tickets (you can read them, see above), move a ticket to Ready or Launched, drop or restore a ticket, comment on the team board, change settings, launch ads, or log in to the app. Only tickets of your own company can ever be changed.

## Copy-paste prompt for your AI tool

> You are helping me send creative concepts to Lonvera's production board through its partner API. Base URL: the address in my environment variable LONVERA_URL. Authenticate every request with the header `Authorization: Bearer <my key>` (the key starts with `lvk_`; never print it or put it in a file that is shared). A ticket needs only `concept_name` (up to 200 characters) and a brief: `brief` (plain text) and/or `brief_url` (an https link). My company is filled in from the key; never send a different `vendor`. Optional fields: `product` (vita-bull, beef-liver, boneflex or vita-belle), `cluster` (market cluster), `concept_kind` (new or iteration), `icp`, `pain_point`, `story`, `for_week` (a date, snaps to Monday), `due_date`, `format_code` (F1 to F6), `note`, `variations` (a list of `{label, url, concept_id}`), `status` (assigned, brief_sent or in_production). Lonvera fills in anything missing from the brief; anything I send is kept as sent. Create with `POST /api/priorities/tickets` (one ticket, or `{"tickets": [...]}` for up to 100; all or nothing). List my tickets with `GET /api/priorities/partner/tickets?status=&week=&limit=&offset=` (read-only view of every company's tickets: `GET /api/priorities/partner/all-tickets`, filters vendor, status, week, product) (each has `needs_creative` and `tags`). Fix with `PATCH /api/priorities/partner/tickets/<id>` (only concept_name, concept_kind, cluster, variations, brief_url, creative_url, story). Add creative as links with `POST /api/priorities/partner/tickets/<id>/creative` and body `{"variations": [{"label": "Original", "url": "https://...", "concept_id": "optional"}], "submit": false}`; links merge by label and nothing is deleted; `{"creative_url": "https://..."}` means the Original; `submit: true` sends a ticket that is In production to review. For many tickets use `POST /api/priorities/partner/creative` with `{"creatives": [{"ticket_id": 1, "variations": [...]}]}`. All links must be https (Google Drive, Frame.io, Dropbox, Vimeo, YouTube, Loom, Box and similar all work; Drive, YouTube, Vimeo and Loom also play inside the ticket). Limits: 60 writes per minute, 100 items per batch, 1 MB per request. Before I act, read `GET /api/priorities/partner/contract` for the current rules. If a request returns 400, read the message: it names the field that is wrong. Do not retry on 401, 403 or 404; on 429 wait for the Retry-After header.
