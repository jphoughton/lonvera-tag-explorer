# UI system (Tag Explorer shell)

Written for the people and agents who edit one page of the app. Read this before touching any page. The shell (navigation, page header, filter bars, tokens, shared components, CSS and test layout) is owned by this document; a page edits its own files only.

## 1. Information architecture

Top row: **Lonvera** (home = Overview) | **Performance** | **Creative** | **Plan** | tag search | status, account.
Second row: the pages of the active section as tabs. The active section and tab are highlighted (`aria-current`).

| Section | Tab (nav label) | URL | Page function | Notes |
|---|---|---|---|---|
| Performance | Overview | `/` (platform=all, the default) | `pages/platforms.js` | every platform side by side |
| Performance | Meta | `/?platform=meta`, `/p/<slug>`, `/ad/<id>` | `pages/products.js`, `product.js`, `ad.js` | products, tags, creatives |
| Performance | Google | `/?platform=google` (`/google` redirects here) | `pages/google.js` | |
| Performance | Campaigns | `/campaigns` (`?platform=all` is the nav link) | `all_campaigns.js`, `campaigns.js`, `google.js` | one Campaigns tab, platform filter inside |
| Performance | Trends | `/trends` | `pages/trends.js` | |
| Performance | Email | `/email` | `pages/email.js` | Klaviyo email and text messages by flow, campaign and over time; `docs/EMAIL.md` |
| Creative | Priorities | `/priorities`, `?view=<token>`, `/priorities/v/<token>` | `pages/priorities*.js` | |
| Creative | Production | `/priorities/production` | `pages/priorities_production.js` | vendor page `/priorities/vendor/<token>` is public and has no app navigation |
| Creative | Test to scale | `/promotions` | `promotions.js`, `scale_views.js` | |
| Plan | Projections | `/projection[/<module>]` | separate page (`projection.html`) | same top row; its second row is the module tabs and the scenario control |
| (search box) | Tag lookup | `/lookup?q=...` | `pages/lookup.js` | the box is in the top bar on every page; `/lookup` still works on its own |

Every old URL keeps working. Pages that are not tabs (ad, product, campaign detail, lookup, saved views) light the tab of the section they belong to (`resolveNav` in `js/nav.js`) and take their Back target from `js/pageconfig.js`.

### Naming (one name per concept)

Overview, Meta, Google, Campaigns, Trends, Email, Priorities, Production, Test to scale, Projections, Tag lookup. The nav labels, page titles (`h1`), `document.title` (`<Page> · Lonvera`), breadcrumbs and in-page links all use these names now. Retired names, which must not come back in page text:

| Retired | Use |
|---|---|
| All platforms | Overview |
| All campaigns | Campaigns |
| Creative priorities, Back to Creative priorities | Priorities, Back to Priorities |
| Production board, Production history (link and block heading) | Production |
| Marketing metrics (title, crumb root, login heading) | Lonvera |

Legacy names survive only where a saved view or URL stores them (priorities saved views store the old Action names; the legacy-label mapping stays), in developer comments, and in the Python docstrings.

### Copy rules

No em dashes in UI text (an empty value shows an en dash or the Projections placeholder glyph). Abbreviations are spelled out where first used on a page or in a tooltip: new customer (NC), return on ad spend (ROAS), cost per acquisition (CPA), ideal customer profile (ICP). Tile labels may stay short (`NC ROAS`) when the tile carries a `title` that expands them (`kpis.js` `labelTitle`). Performance pages keep the chart's Grain choice in view and tuck Bars, Line, Swap, Split and Numbers into the closed "Chart options" disclosure (`tuckChartOptions` in `platforms.js`; tests call `open_chart_options` from `tests/ui/ui_helpers.py`).

The app shell is forced light (`<html data-theme="light">` in `index.html`); only the Projections page follows the OS dark mode.

## 2. Where things live

```
app/static/
  index.html            the SPA shell (links app.css, then projection/brand.css)
  projection.html       the Projections page (same CSS, same top row)
  app.js                router: PAGE_FNS + js/pageconfig.js; render(); Back; sign-in redirect
  app.css               MANIFEST ONLY: @import list of css/*.css (the order is the cascade; do not reorder)
  css/                  one file per page or component (section 4)
  js/nav.js             NAV CONFIG (sections, tabs, tag search, which tab a URL lights) and the nav renderer
  js/pageconfig.js      PAGE CONFIG (URL pattern, Back target, filters, default range) for every page
  js/controls.js        builds the shell: top bar, global filter bar, status, stale banner
  js/ui/pagehead.js     pageHeader() component
  js/ui/pagefilters.js  seg / chips / selectCtl / searchCtl / numberCtl / checkCtl / filterRow
  js/pages/*.js         one file per page (page owners edit these)
  projection/brand.css  brand palette (--lv-*), light and dark; maps the app tokens onto it
```

### Navigation config: `js/nav.js`

Edit `SECTIONS` to add, rename or move a menu entry. Nothing else defines navigation: the SPA shell (`controls.js` `buildShell`) and the Projections page (`js/projection/shell.js`) both call `renderNav()`. `SEARCH` configures the tag search box.

```js
{ key: 'priorities', label: 'Priorities', href: '/priorities', testid: 'nav-priorities', hint: 'What to make next' }
```
Tab testids (immutable): `nav-overview nav-meta nav-google nav-campaigns nav-trends nav-email nav-priorities nav-production nav-test-to-scale nav-projections`; section links `nav-section-<key>`; search form `nav-lookup`, input `nav-lookup-input`, button `nav-lookup-go`; sub-nav container `subnav`.
`resolveNav(path, params)` maps a URL to `{ section, page }`. Add a row to `RESOLVE` when you add a route that is not a tab.

### Page config: `js/pageconfig.js`

One row per page in `PAGES`:

| Field | Meaning |
|---|---|
| `id`, `re` | key in `PAGE_FNS` (app.js) and the URL pattern |
| `public`, `bare` | no login / no app navigation (vendor links) |
| `filters` | which parts of the global filter bar the page uses: `platform`, `source` (Numbers from and Count), `range`, `stage`, `compare`. `[]` hides the bar. |
| `parent({ path, p, G })` | where Back goes with no in-app history (`p` = URL params, `G` = the global filter params to carry along) |
| `defaults`, `skipDefaultsWhen(p)` | default URL params (Test to scale opens on `window=30d`) |

A page that draws its own controls (Trends, Priorities, Production, Lookup) declares `filters: []` and builds them with `js/ui/pagefilters.js`.

## 3. Page header and filter bar

### `pageHeader` (`js/ui/pagehead.js`)

```js
import { pageHeader } from '../ui/pagehead.js';
pageHeader({
  title: 'Trends',
  description: 'Key numbers by day, week or month, with the plan beside them.',
  badge: 'Meta and Google',            // optional
  actions: [h('button', { class: 'btn small', type: 'button' }, 'Download CSV')],   // right-hand slot
  testid: 'trn-head',
});
```
Output is `.pagehead > .titleline > h1 + .badge + .pagehead-actions`, then `.note-line` (the one-line description). It is the markup the Meta, Trends and Test to scale pages already use, so adopting it changes nothing visually. Priorities and Production use `.pri-titlerow`; migrate them when you do their page.

### Filter bar

Two layers, one look:
1. **Global bar** (`#controls`, built by `controls.js`): Platform, Numbers from (+ Count), Range (presets and custom), Stage chips, Compare to, Per day. A page picks its parts with `filters` in `pageconfig.js`. Do not rebuild these on a page.
2. **Page controls** (`js/ui/pagefilters.js`): anything else a page filters on.

```js
import { filterRow, seg, chips, selectCtl, searchCtl, numberCtl, checkCtl } from '../ui/pagefilters.js';
filterRow(
  seg({ name: 'Interval', options: [['day', 'Day'], ['week', 'Week'], ['month', 'Month']], value: iv, onChange: setInterval, testid: 'trn-interval' }),
  chips({ name: 'Bands', options: bands, values: on, onToggle: toggleBand }),
  selectCtl({ name: 'Week', options: weeks, value: wk, onChange: setWeek }),
  searchCtl({ placeholder: 'Search creatives', onInput: filter, testid: 'tts-moved-search' }),
  numberCtl({ name: 'Min spend', value: 500, min: 0, step: 50, onChange: setMin }),
  checkCtl({ label: 'Hide bulk relaunches', checked: hide, onChange: setHide }));
```
`seg` takes `href(value)` to render links (so a choice lives in the URL) instead of buttons. Put every filter in the URL (`setParams`), so Back, reload and shared links keep it.

**Numbers from** has one implementation (`numbersFrom` in `controls.js`) mounted twice: in the bar (`#source-ctl`, `#basis-ctl`, `data-source`, `data-basis`) and beside the source explanation (`#source-ctl-inline`, `#basis-ctl-inline`, `data-src-inline`, `data-basis-inline`). Both ids stay because the tests address each mount; they share one state. Trends has its own Own | Upstack choice (`trn-numbers-*`); fold it into the global one when you do that page.

## 4. Tokens (`css/tokens.css`, `projection/brand.css`)

One layer. Names the app CSS reads:

| Token | Meaning |
|---|---|
| `--bg --panel --line --line-soft --head-bg` | page, card, borders, table head |
| `--text --muted --accent --accent-bg` | text, secondary text, links and selected states |
| `--good --warn --bad` (+ `-bg`) | status |
| `--testing --scaling --unc` (+ `-bg`) | creative stage colours |
| `--font --mono` | type |
| `--topbar-h` (42px) `--subnav-h` (34px) | the two header rows |
| `--header-h` | their sum; the offset for anything that sticks under the header (filter bar, table heads). Do not hard-code 42px. |

Values come from the brand tokens (`--lv-*`, warm bone / ink / ember, light and dark) in `projection/brand.css`, which is linked after `app.css` and maps these names onto the brand palette. Use `--lv-*` directly in new work. Page-scoped tokens live at the top of the page's own CSS file (priorities: `--lv-pri-*` in `css/priorities.css`); never add page tokens to `tokens.css`. The two dark blocks in `brand.css` must stay identical (a test checks).

## 5. Components

The small set. Old names stay as aliases, so page files keep working; new code uses the canonical name.

| Component | Canonical class | Aliases (same look) | Use |
|---|---|---|---|
| Button | `btn`, `btn primary`, `btn small` | `btnlink` (a link that looks like a button), `.btn.back` | actions: Apply, Save, Download |
| Link button | `linkbtn` | | an inline action inside a sentence |
| Segmented control | `segs > seg` with `aria-pressed` | `pri-seg > pri-segbtn` | choose one of a few |
| Filter chip | `fchip` with `aria-pressed` | `cchip`, `mchip`, `schip` (stage colours), `pri-chipbtn` | turn a filter on or off, several can be on |
| Tab | `navtab` (section pages), page tabs `sv-tab-*` | | switch the page's view |

Segmented controls and chips always carry `aria-pressed="true|false"`. Buttons that only look like links are still `<button>`s. Other shared pieces: `createTable` (`js/table.js`), `createTrendPanel` (`js/trend.js`), `kpis.js` totals strip, `components.js` (`breadcrumbs`, `viewSwitch`, `srcBadge`, `columnsChips`, `section`, `adsTable`), `modal.js`, `lightbox.js`. Build DOM with `h()` from `js/util.js` only; API text goes in as text, never HTML.

## 6. CSS layout (`app.css` is a manifest)

| File | Owner | Holds |
|---|---|---|
| `tokens.css` | shell | tokens, base elements |
| `shell.css` | shell | top bar, section tabs, tag search, global filter bar, page header, buttons, segmented control |
| `components.css` | shell | badges, cards, stage cards |
| `tables.css`, `columns.css` | shell | tables; compare cells, column chips, first column, hooks |
| `responsive.css` | shell | phone rules for the shell, tables, cards |
| `charts.css`, `media.css`, `sources.css` | shell | trend panel; thumbnails, lightbox, preview; stale and source banners |
| `login.css` | Login page | sign-in |
| `product.css` | Product and Ad pages | tag tree, coverage bar |
| `performance.css` | Overview, Meta, Google, Campaigns | totals strip, KPI tiles |
| `test_to_scale.css` | Test to scale | `tts-`, `sv-`, `kv-` |
| `trends.css` | Trends | `trn-` |
| `email.css` | Email | `eml-` |
| `lookup.css` | Tag lookup | `tl-` (page and result cards) |
| `priorities.css`, `priorities_groups.css` | Priorities | `pri-`, saved views, detail panel, grouped table, sticky bars |
| `production.css` | Production | `prod-`, vendor page, launch dialog |

The files load in the order listed in `app.css`; the cascade follows it. The old "contrast pass" override block of `/priorities` is folded into the rules it overrode (27 selectors); what could not move without changing the cascade against print and media rules is now ordinary rules under "priorities: cards, tiles, chips, hit rate, totals" in `priorities.css`. Computed styles were compared element by element before and after on 15 page states at 1440px and 375px: no differences.

## 7. Rules for page agents

1. **Testids and DOM ids are immutable.** Add new ones; never rename or remove. A test file you do not own may read them.
2. No em dashes in any text the user sees (an en dash is the empty-cell placeholder).
3. Plain English. Expand every acronym on first use on the page or give it a tooltip (ICP = ideal customer profile, ROAS = return on ad spend, CPA = cost per acquisition, NC = new customer, CPM = cost per thousand impressions, CTR = click-through rate). Say what the page is for in the one-line description.
4. 375px wide must not scroll sideways at page level (tables scroll inside their own wrapper). Check before you finish.
5. Class names: never start with `ad-`, `ads-`, `sponsor`, `advert`, and avoid `banner` and `promo` (ad blockers hide them; `tests/test_static_classnames.py` checks the files).
6. Edit only your page's JS, its CSS file and its test files (section 8). A change to a shared file (`controls.js`, `util.js`, `components.js`, `table.js`, `shell.css`, `tokens.css`, `nav.js`, `pageconfig.js`, `app.js`) goes through the shell owner.
7. Filters live in the URL. A page that needs the global bar says so in `pageconfig.js`, it does not call `showControls` itself.
8. Do not add a second implementation of a control that exists in section 3 or 5.
9. No `innerHTML` with data. No secrets in files.

## 8. Tests

```
tests/ui/
  conftest.py            fixtures, mock server start (UI_MOCK_PORT, UI_NOAUTH_MOCK_PORT)
  ui_helpers.py          shared helpers and constants (no assertions)
  mock_server.py         app shell: auth, status, shell routes, registers the others
  mock_app.py            the Flask app
  mock_data.py           ad-level daily table, windows, metric helpers (no routes)
  mock_performance.py    Overview, Google, Campaigns, totals, platform trend routes
  mock_product.py        products, tag tree, ads, ad page, trend, preview, media routes
  mock_promotions.py  mock_trends.py  mock_lookup.py  mock_priorities*.py  mock_priorities_launch.py   (one per page, as before)
```

| File | Page owner |
|---|---|
| `test_shell.py` | shell: stale banner, global range / stage / compare / Numbers from, Back, deep links, 401, layout, default landing, top navigation |
| `test_login.py` | Login |
| `test_performance.py`, `mock_performance.py` | Overview, Google, Campaigns |
| `test_products.py` | Meta landing and product page (tree, ads, campaigns, columns, hooks, coverage) |
| `test_ad.py` | Ad page, lightbox, preview |
| `test_trend_panel.py` | shared trend panel |
| `test_trends.py`, `mock_trends.py` | Trends |
| `test_email.py`, `mock_email.py` | Email |
| `test_promotions.py`, `test_scale_views.py`, `mock_promotions.py` | Test to scale |
| `test_priorities.py`, `test_priorities_groups.py`, `mock_priorities.py`, `priorities_fixture.json` | Priorities |
| `test_priorities_production.py`, `test_priorities_launch.py`, `mock_priorities_assign.py`, `mock_priorities_launch.py` | Production |
| `test_lookup.py`, `mock_lookup.py` | Tag lookup |

Run: `PATH=/opt/homebrew/opt/postgresql@16/bin:$PATH TEST_API_DB=<unique> UI_MOCK_PORT=<unique> UI_NOAUTH_MOCK_PORT=<unique> PYTHONPATH=. ~/lonvera-tag-explorer/.venv/bin/python -m pytest tests/ui -q -p no:cacheprovider`. Run `tests/etl/test_assignment_tags.py` in a separate command.
