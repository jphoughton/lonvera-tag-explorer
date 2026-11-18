# Theme previews

A page in the app (`/theme-previews`) where a named person, for example the media buyer, edits Shopify theme files and saves them to a PREVIEW theme. It can never publish and never touches the live theme.

**Status: built dark and fully mocked. No real Shopify call has been made. The token does not exist yet.**

## What it does

- Make a preview: copies the live theme into a new unpublished theme named `preview-<login>-<yyyymmdd>-<slug>` and records it in `theme_previews`. The page shows an "Open preview" link (`https://<store>/?preview_theme_id=<id>`).
- Pick a preview, pick a file, edit the text, "Save to preview".
- Every create and save writes a row to `theme_edit_audit` (who, which preview, which file, sha256, size, time). The text itself is not stored in the app.

## Switches (environment variables, all off or empty by default)

| Variable | Meaning |
|---|---|
| `THEME_PREVIEW_ENABLED` | Exactly `1` turns the feature on. Anything else: every route answers 404 and the nav link is hidden. |
| `THEME_EDITORS` | Comma list of logins allowed to use it (case ignored). Empty means nobody. |
| `SHOPIFY_STORE_DOMAIN` | For example `your-store.myshopify.com`. |
| `SHOPIFY_THEME_TOKEN` | Admin API access token of a custom app. Never logged, stored, or returned; errors are scrubbed of it. |

Optional: `THEME_WRITES_PER_MIN` (default 30 per IP).

Gates on every route, in order: flag off gives 404; sign-in off gives 403 "Theme editing needs sign-in to be on."; login not in `THEME_EDITORS` gives 403 "Your login is not allowed to edit themes." No route is public, and an API key is never an editor. `GET /api/theme/status` (login only) and the `theme` key on `/api/status` return `{enabled, can_edit}` so the UI shows the nav link only when both are true.

## Required token scopes

`read_themes` and `write_themes`. Shopify's docs also say modifying theme files needs a separate exemption from Shopify for the app (the docs for `themeFilesUpsert` and `themeDuplicate` say so). Without it the calls will fail with a Shopify error that the page shows in plain words.

## Safety rules (enforced on the server on every write)

1. The theme's role is re-read from Shopify each time. A `MAIN` (live) theme is refused with 409 "That is the live theme. Live changes are made in Shopify admin."
2. The theme must be in our own `theme_previews` table (made through this page). Any other theme is refused with 409.
3. Files: only `templates/`, `sections/`, `snippets/`, `blocks/`, `layout/`, `assets/`, ending in `.css`, `.js`, `.liquid` or `.json`; no `..`, no leading slash; at most 1 MB.
4. `app/shopify_theme.py` has no publish call and no delete call (theme or file). A test fails if `themePublish`, `themeDelete` or `themeFilesDelete` appear in the Shopify module, the routes or the page.

Note: `config/settings_data.json` and `locales/` are not editable here on purpose (theme settings are not in the allowed folders).

## Where edits live

Edits exist only inside the preview theme in Shopify. They are NOT in git. Per the Shopify rules in CLAUDE.md, anything worth keeping must be copied into the theme repo and go through a pull request before Josh publishes in Shopify admin. Previews are never deleted by the app; clean them up in Shopify admin.

## Routes

`GET /api/theme/status`, `GET /api/theme/themes`, `POST /api/theme/previews {name}`, `GET /api/theme/previews/<id>/files` (list) and `?key=` (read one), `PUT /api/theme/previews/<id>/files {key, content}`. Tables: migration `276_theme_previews.sql`.

## Unproven until a real token exists

- Every GraphQL call (API version `2026-10`): `theme(id)`, `themes(first, roles: [MAIN])` (the `roles` argument was not confirmed in the docs), `themeDuplicate`, `theme { files }` listing and reading (the `OnlineStoreThemeFileBodyText` fragment), and `themeFilesUpsert`.
- Whether the Shopify exemption for theme edits applies to this app.
- `themeFilesUpsert` is asynchronous (it returns a job); the page does not wait for the job, so a saved file may take a moment to show in the preview.
- How a duplicate of a large theme behaves (the page waits up to 25 seconds for Shopify's answer).
- Unit tests use a fake Shopify class at one seam (`app.shopify_theme.client`).
