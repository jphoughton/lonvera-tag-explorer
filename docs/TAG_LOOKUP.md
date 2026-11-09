# Tag lookup

Page `/lookup` (nav link "Tag lookup"), API `GET /api/tag-lookup?q=<text>`. Paste an ad name exactly as Ads Manager shows it and read the new (line-mapped) tags of that ad. Read-only, 8 second statement timeout. Code: `app/lookup.py`, `app/static/js/pages/lookup.js`.

## What it accepts
A platform ad name, a Meta ad id (digits, or an Ads Manager link that carries `selected_ad_ids=`), a tracker Concept ID, a tracker Ad Family.

## How it matches (first rule that reaches a tracker row wins; `match_kind`)
1. `id`: the digits are a Meta ad id; its name leads to tracker rows through the ETL's Meta family map.
2. `exact`: the text is a platform ad name character for character.
3. `normalized`: same name ignoring case, spacing, quotes, trailing ` - Copy N` and ` | xx-merge-date` tags (the ETL's family key).
4. `concept`: a tracker Concept ID (or the tracker's "Name" column).
5. `family`: a tracker Ad Family, or a platform name that is a tracker name with an `S-` prefix and/or a `_VID_...` / `_IMG_...` encoded suffix (the ETL resolver does not handle the `S-` names; this does, by the name underneath).
6. `none`: nothing reached a tracker row. The answer says so, shows the platform ad when it exists, and never shows a tag.

Several tracker rows can answer to one name (copies, iterations, the same family in two products). All are shown (up to 10) with a note when their tags differ. Rows removed from the tracker are never returned.

## Limits
- Only line-mapped tags (Vita Bull, Beef Liver, Boneflex). Other products and untagged rows say "not tagged yet".
- A text-only tag (no picture was available) is shown with a caveat.
- Old `Bull-###` style ads with no tracker row cannot be matched.

## Later (left out of the first version)
- The tracker's own tags beside the new ones, with an agrees / differs marker per field.
- A 30-day and since-launch performance line on the app's Numbers basis.
- "Closest names" suggestions for a name that matches nothing, and debounced suggestions while typing.
- The ad's hook, vendor, launch date and ad type on the card; a search box on the Meta products page.
