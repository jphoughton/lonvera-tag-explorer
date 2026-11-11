// Marketing metrics, Test to scale (/promotions): creatives that moved from TESTING to SCALING in the chosen range, and what happened around the move. Meta only.
// A move = a creative spent in a Testing campaign and its first spend in a Scaling campaign came on a later day (the buyer duplicates the creative into a scaling
// campaign; Meta cannot move an ad). Everything comes from /api/promotions in one call. Class names and test ids start with `tts-` (never ad-, ads-, sponsor, banner,
// promo: ad blockers hide those).
import { h, fmt, isNum, href, globalParams, apiWindow, setParams, metricLabel, liveDot, debounce, empty, navigate } from '../util.js';
import { api } from '../api.js';
import { createTable } from '../table.js';
import { setRangeInfo } from '../controls.js';
import { saveSortToUrl, sortFromUrl } from '../components.js';
import { trendChart } from '../charts.js';
import { currentView, scaleViewsPage, pageTop, productFilter, numberInput, bulkCheck, creativeCell as sharedCreativeCell } from './scale_views.js';
import { searchCtl } from '../ui/pagefilters.js';

const HELD_TEXT = { held: 'Held up', softer: 'Softer', fell: 'Fell', too_early: 'Too early', no_baseline: 'No baseline' };
const HELD_TITLE = {
  held: 'Scaling ROAS since the move is at least 90% of its testing ROAS', softer: 'Scaling ROAS is 70% to 90% of its testing ROAS', fell: 'Scaling ROAS is below 70% of its testing ROAS',
  too_early: 'Not enough scaling yet to call it: less than $200 spent or fewer than 3 days in scaling', no_baseline: 'Its testing earned nothing, so there is no testing ROAS to compare with',
};
const PRODUCT_NONE = 'none';
const money = (v) => fmt.money(v);
const roasX = (v) => (isNum(v) ? fmt.roas(v) + 'x' : '–');
const dayRange = (a, b) => (a === b ? fmt.day(a) : `${fmt.day(a)} – ${fmt.day(b)}`);
const sumOf = (rows, f) => rows.reduce((a, r) => a + (f(r) || 0), 0);
const ratio = (n, d) => (d ? n / d : null);

// API parameters of the page: the range bar's window (default the last 30 days; Since launch is every day held), Numbers from, and the page's own filters
function apiParams(p) {
  const o = {};
  const w = apiWindow(p);                                    // only source / basis are taken from it: stage, Active only and comparison do not apply here
  if (w.source) { o.source = w.source; if (w.basis) o.basis = w.basis; }
  if (p.from && p.to) { o.from = p.from; o.to = p.to; } else o.window = p.window || '30d';
  if (p.product) o.product = p.product;
  if (p.hide_bulk === '1') o.hide_bulk = 1;
  if (p.bulk_min) o.bulk_min = p.bulk_min;
  if (p.min_test_spend) o.min_test_spend = p.min_test_spend;
  if (p.grain === 'day' || p.grain === 'week') o.grain = p.grain;
  return o;
}

function kpi(id, label, value, exact, sub, extra = []) {
  return h('div', { class: 'kpi', 'data-testid': 'tts-kpi-' + id },
    h('div', { class: 'kpi-label' }, label),
    h('div', { class: 'kpi-value', 'data-testid': 'tts-kpi-' + id + '-value', ...(exact != null ? { 'data-value': String(exact) } : {}) }, value),
    sub ? h('div', { class: 'kpi-sub' }, sub) : null,
    ...extra);
}

function heldBadge(m) {
  const pct = m.ratio != null ? ` · ${Math.round(m.ratio * 100)}%` : '';
  return h('span', { class: 'badge tts-held tts-held-' + m.state, 'data-held': m.state, title: HELD_TITLE[m.state] + (m.ratio != null ? ` (scaling ROAS is ${Math.round(m.ratio * 100)}% of testing ROAS)` : '') }, HELD_TEXT[m.state] + pct);
}

// the creative cell shared by both tables here (and by every other tab): thumbnail, name (opens the creative page), product, tags and the flags
const creativeCell = (r, G, flags = []) => sharedCreativeCell(r, G, { flags, testid: 'tts-creative-link', extra: true });

function flagsOf(r) {
  const out = [];
  if (r.bulk) out.push(h('span', { class: 'badge tts-bulk', 'data-testid': 'tts-bulk-chip', title: `${fmt.int(r.moves_that_day)} creatives entered scaling on ${fmt.day(r.moved)}: a mass relaunch, not a test winner` }, `Bulk move · ${fmt.int(r.moves_that_day)} that day`));
  if (r.launched_before_data_start) out.push(h('span', { class: 'badge muted-badge', 'data-testid': 'tts-before-start', title: 'Already running on the first day of data, so its real launch day (and the time it spent in testing) is earlier than shown' }, 'Launched before data start'));
  return out;
}

const testBlock = (r) => r.test || {};
const scaleBlock = (r) => r.scale || {};

function placementsNode(r) {
  const pl = r.placements;
  if (!pl || !pl.top.length) return h('div', { class: 'muted small' }, 'No scaling ad set found for this creative.');
  const cols = [
    { key: 'campaign', label: 'Scaling campaign', sort: (x) => x.campaign, cell: (x) => h('span', { title: x.campaign }, x.campaign) },
    { key: 'adset', label: 'Ad set', sort: (x) => x.adset, cell: (x) => h('span', { title: x.adset || '' }, x.adset || '–') },
    { key: 'ads', label: 'Ads', num: true, sort: (x) => x.ads, cell: (x) => fmt.int(x.ads) },
    { key: 'spend', label: 'Spend', num: true, sort: (x) => x.spend, cell: (x) => money(x.spend) },
    { key: 'purchases', label: () => metricLabel('purchases', 'Purchases'), num: true, sort: (x) => x.purchases, cell: (x) => fmt.int(x.purchases) },
    { key: 'roas', label: () => metricLabel('roas', 'ROAS'), title: 'Return on ad spend (ROAS): revenue divided by ad spend', num: true, sort: (x) => x.roas, cell: (x) => fmt.roas(x.roas) },
    { key: 'first', label: 'First day', sort: (x) => x.first_day, cell: (x) => fmt.day(x.first_day) },
    { key: 'last', label: 'Last day', sort: (x) => x.last_day, cell: (x) => fmt.day(x.last_day) },
  ];
  const t = createTable({ columns: cols, rows: pl.top, rowKey: (x) => x.campaign_id + '|' + x.adset_id, tableClass: 'compact', testid: 'tts-placements' });
  return h('div', null,
    h('div', { class: 'note-line' }, `Scaling since the move (${fmt.day(r.moved)}): ${fmt.int(pl.campaigns)} campaign${pl.campaigns === 1 ? '' : 's'}, ${fmt.int(pl.adsets)} ad set${pl.adsets === 1 ? '' : 's'}, ${fmt.int(pl.ads)} ad${pl.ads === 1 ? '' : 's'}, ${money(pl.spend)}.`),
    t.el,
    pl.more ? h('div', { class: 'muted small' }, `+ ${fmt.int(pl.more)} more ad set${pl.more === 1 ? '' : 's'} not listed`) : null);
}

function movedColumns(G, daily) {
  const dpad = (r) => (r.launched_before_data_start ? '≥ ' : '');
  const rl = (txt, key, dflt) => () => `${txt} ${metricLabel(key, dflt)}`;
  return [
    { key: 'display', label: 'Creative', sticky: true, cls: 'labelcol', sort: (r) => r.display, total: () => 'Total', cell: (r) => creativeCell(r, G, flagsOf(r)) },
    { key: 'hook', label: 'Hook', cls: 'hookcol', sort: (r) => r.hook, total: () => '', title: 'The first spoken line and on-image headline, boiled down to one line', cellTitle: (r) => r.hook || null,
      cell: (r) => (r.hook ? h('span', { class: 'hooktext' }, r.hook) : '') },
    { key: 'launched', label: 'Launched', sort: (r) => r.launched, total: () => '', title: 'First day with spend in any campaign', cellTitle: (r) => (r.launched_before_data_start ? `Already running on ${fmt.day(daily)}, the first day of data` : null),
      cell: (r) => fmt.day(r.launched) + (r.launched_before_data_start ? '*' : '') },
    { key: 'moved', label: 'Moved', sort: (r) => r.moved, total: () => '', title: 'First day with spend in a Scaling campaign', cell: (r) => fmt.day(r.moved) },
    { key: 'days_in_test', label: 'Days in test', num: true, sort: (r) => r.days_in_test, total: () => '', title: 'Days from the first testing spend to the first scaling spend (at least that many when the creative was already running on the first day of data)',
      cell: (r) => dpad(r) + fmt.int(r.days_in_test) },
    { key: 'test_spend', label: 'Test spend', num: true, cls: 'sep', sort: (r) => testBlock(r).spend, title: 'Spend in Testing campaigns before the first scaling day', cell: (r) => money(testBlock(r).spend), total: (t) => money(t.test.spend) },
    { key: 'test_purchases', label: rl('Test', 'purchases', 'Purchases'), num: true, sort: (r) => testBlock(r).purchases, cell: (r) => fmt.int(testBlock(r).purchases), total: (t) => fmt.int(t.test.purchases) },
    { key: 'test_roas', label: rl('Test', 'roas', 'ROAS'), title: 'Return on ad spend (ROAS): revenue divided by ad spend', num: true, sort: (r) => testBlock(r).roas, cell: (r) => fmt.roas(testBlock(r).roas), total: (t) => fmt.roas(t.test.roas) },
    { key: 'test_cpa', label: rl('Test', 'cpa', 'CPA'), title: 'Cost per acquisition (CPA): ad spend divided by purchases', num: true, sort: (r) => testBlock(r).cpa, cell: (r) => money(testBlock(r).cpa), total: (t) => money(t.test.cpa) },
    { key: 'scale_spend', label: 'Scaling spend', num: true, cls: 'sep', sort: (r) => scaleBlock(r).spend, title: 'Spend in Scaling campaigns since the move, through the latest day of data', cell: (r) => money(scaleBlock(r).spend), total: (t) => money(t.scale.spend) },
    { key: 'scale_purchases', label: rl('Scaling', 'purchases', 'Purchases'), num: true, sort: (r) => scaleBlock(r).purchases, cell: (r) => fmt.int(scaleBlock(r).purchases), total: (t) => fmt.int(t.scale.purchases) },
    { key: 'scale_roas', label: rl('Scaling', 'roas', 'ROAS'), title: 'Return on ad spend (ROAS): revenue divided by ad spend', num: true, sort: (r) => scaleBlock(r).roas, cell: (r) => fmt.roas(scaleBlock(r).roas), total: (t) => fmt.roas(t.scale.roas) },
    { key: 'scale_cpa', label: rl('Scaling', 'cpa', 'CPA'), title: 'Cost per acquisition (CPA): ad spend divided by purchases', num: true, sort: (r) => scaleBlock(r).cpa, cell: (r) => money(scaleBlock(r).cpa), total: (t) => money(t.scale.cpa) },
    { key: 'held', label: 'Held up?', cls: 'sep', sort: (r) => (r.held.ratio == null ? -1 : r.held.ratio), total: () => '', title: 'Scaling return on ad spend (ROAS) since the move against testing ROAS before it (hover a badge for the rule)', cell: (r) => heldBadge(r.held) },
    { key: 'last7_spend', label: 'Last 7 days spend', num: true, sort: (r) => r.last7.spend, title: 'Scaling spend in the last 7 days of data', cell: (r) => money(r.last7.spend), total: (t) => money(t.last7.spend) },
    { key: 'last7_roas', label: rl('Last 7 days', 'roas', 'ROAS'), title: 'Return on ad spend (ROAS): revenue divided by ad spend', num: true, sort: (r) => r.last7.roas, cell: (r) => fmt.roas(r.last7.roas), total: (t) => fmt.roas(t.last7.roas) },
    { key: 'live', label: 'Live', cls: 'center', sort: (r) => (r.live_scaling ? 1 : 0), total: () => '', title: 'Spent in a Scaling campaign on the latest day of data or the day before', cell: (r) => liveDot(r.live_scaling) },
    { key: 'placements', label: 'Scaled in', sort: (r) => r.placements.adsets, total: () => '', title: 'Scaling campaigns and ad sets this creative has spent in since the move (open the row for the list)',
      cell: (r) => {
        const pl = r.placements;
        const names = pl.top.map((x) => `${x.campaign} › ${x.adset || '(no ad set)'} · ${money(x.spend)}`).join('\n') + (pl.more ? `\n+ ${pl.more} more` : '');
        return h('span', { title: names, 'data-testid': 'tts-placement-count' }, `${fmt.int(pl.campaigns)} campaign${pl.campaigns === 1 ? '' : 's'} · ${fmt.int(pl.adsets)} ad set${pl.adsets === 1 ? '' : 's'}`);
      } },
  ];
}

function movedTotal(rows) {
  const blk = (get) => {
    const sp = sumOf(rows, (r) => get(r).spend), pu = sumOf(rows, (r) => get(r).purchases), rv = sumOf(rows, (r) => get(r).revenue);
    return { spend: sp, purchases: pu, roas: ratio(rv, sp), cpa: ratio(sp, pu) };
  };
  return { test: blk(testBlock), scale: blk(scaleBlock), last7: blk((r) => r.last7) };
}

function notScaledColumns(G, through) {
  const rl = (key, dflt) => () => `Test ${metricLabel(key, dflt)}`;
  return [
    { key: 'display', label: 'Creative', sticky: true, cls: 'labelcol', sort: (r) => r.display, cell: (r) => creativeCell(r, G, flagsOf({ ...r, bulk: false })) },
    { key: 'hook', label: 'Hook', cls: 'hookcol', sort: (r) => r.hook, cellTitle: (r) => r.hook || null, cell: (r) => (r.hook ? h('span', { class: 'hooktext' }, r.hook) : '') },
    { key: 'launched', label: 'Launched', sort: (r) => r.launched, cell: (r) => fmt.day(r.launched) },
    { key: 'age_days', label: 'Days since launch', num: true, sort: (r) => r.age_days, title: `Days from the first spend to ${fmt.day(through)}, the latest day of data`, cell: (r) => fmt.int(r.age_days) },
    { key: 'state', label: 'Status', sort: (r) => r.state, title: 'Still testing = spent on the latest day of data or the day before; Stopped = no spend since', cellTitle: (r) => (r.state === 'stopped' ? `Last spend ${fmt.day(r.last_spend_day)}` : null),
      cell: (r) => h('span', { class: 'badge tts-state tts-state-' + r.state, 'data-state': r.state }, r.state === 'still_testing' ? 'Still testing' : `Stopped · last spend ${fmt.day(r.last_spend_day)}`) },
    { key: 'test_spend', label: 'Test spend', num: true, cls: 'sep', sort: (r) => r.test.spend, cell: (r) => money(r.test.spend) },
    { key: 'test_purchases', label: rl('purchases', 'Purchases'), num: true, sort: (r) => r.test.purchases, cell: (r) => fmt.int(r.test.purchases) },
    { key: 'test_roas', label: rl('roas', 'ROAS'), title: 'Return on ad spend (ROAS): revenue divided by ad spend', num: true, sort: (r) => r.test.roas, cell: (r) => fmt.roas(r.test.roas) },
    { key: 'test_cpa', label: rl('cpa', 'CPA'), title: 'Cost per acquisition (CPA): ad spend divided by purchases', num: true, sort: (r) => r.test.cpa, cell: (r) => money(r.test.cpa) },
    { key: 'last7_spend', label: 'Last 7 days spend', num: true, sort: (r) => r.last7.spend, cell: (r) => money(r.last7.spend) },
  ];
}

function cohortColumns() {
  const wk = (r) => `Week of ${fmt.day(r.week)}`;
  return [
    { key: 'week', label: 'Launch week', sticky: true, sort: (r) => r.week, total: () => 'Total', title: 'Week (Monday) of the first spend', cellTitle: (r) => (r.partial ? 'Only part of this week is inside the range' : null),
      cell: (r) => h('span', null, wk(r), r.partial ? h('span', { class: 'muted small' }, ' (partial)') : null) },
    { key: 'launched', label: 'Began in testing', num: true, sort: (r) => r.launched, cell: (r) => fmt.int(r.launched), total: (t) => fmt.int(t.launched), title: 'Creatives whose first spend was in a Testing campaign' },
    { key: 'moved', label: 'Reached scaling', num: true, sort: (r) => r.moved, cell: (r) => fmt.int(r.moved), total: (t) => fmt.int(t.moved), title: 'Of those, how many later spent in a Scaling campaign' },
    { key: 'pct', label: '% reached scaling', num: true, sort: (r) => r.pct_reached_scaling, cell: (r) => fmt.pct(r.pct_reached_scaling), total: (t) => fmt.pct(t.pct_reached_scaling), title: 'Reached scaling / began in testing. Recent weeks are still maturing: the typical creative takes days to weeks to move.' },
    { key: 'median', label: 'Median days to scale', num: true, sort: (r) => r.median_days_to_scale, cell: (r) => fmt.dec1(r.median_days_to_scale), total: (t) => fmt.dec1(t.median_days_to_scale) },
    { key: 'still', label: 'Still testing', num: true, sort: (r) => r.still_testing, cell: (r) => fmt.int(r.still_testing), total: (t) => fmt.int(t.still_testing) },
    { key: 'stopped', label: 'Stopped', num: true, sort: (r) => r.stopped, cell: (r) => fmt.int(r.stopped), total: (t) => fmt.int(t.stopped), title: 'Not scaled and no spend on the latest day of data or the day before' },
    { key: 'no_test', label: 'Straight into scaling', num: true, sort: (r) => r.no_test, cell: (r) => fmt.int(r.no_test), total: (t) => fmt.int(t.no_test), title: 'Launched without testing (or scaling first, or both on the same day): not part of a test-to-scale rate' },
  ];
}

// search box for one table (the moved table keeps its text in the URL)
function searchBox(table, fieldsOf, { q = '', persist = false, testid, placeholder }) {
  const count = h('span', { class: 'muted', 'data-testid': testid + '-count' });
  const searchEl = searchCtl({ placeholder, value: q, testid, onInput: null });
  const input = searchEl.querySelector('input');
  const apply = () => {
    const needle = input.value.trim().toLowerCase();
    table.setFilter(needle ? (r) => fieldsOf(r).some((f) => f != null && String(f).toLowerCase().includes(needle)) : null);
    if (persist) {
      const u = new URL(location.href);
      if (needle) u.searchParams.set('q', input.value.trim()); else u.searchParams.delete('q');
      navigate(u.pathname + u.search, { replace: true, render: false });
    }
  };
  input.addEventListener('input', debounce(apply, 120));
  if (q) apply();
  return { el: h('div', { class: 'ctl-row pagefilters' }, searchEl, count), setCount(n, total) { count.textContent = n === total ? `${fmt.int(total)} rows` : `${fmt.int(n)} of ${fmt.int(total)} rows`; } };
}

const SLOW_MS = 15000;
// One call with a patience limit: a request that hangs or fails ends in the page's error box with Retry (app.js), never in an endless 'Loading…'.
// `tts_timeout_ms` in the URL shortens the limit (used by the tests).
async function loadPromotions(p, signal) {
  const limit = Number(p.tts_timeout_ms) > 0 ? Number(p.tts_timeout_ms) : SLOW_MS;
  const ctl = new AbortController();
  let slow = false;
  const timer = setTimeout(() => { slow = true; ctl.abort(); }, limit);
  const relay = () => ctl.abort();
  signal.addEventListener('abort', relay);
  try {
    return await api('/api/promotions', apiParams(p), { signal: ctl.signal });
  } catch (e) {
    if (slow) throw new Error(`Test to scale took longer than ${Math.round(limit / 1000)} seconds to answer. The server may be busy or the data is being rebuilt: press Retry.`);
    throw e;
  } finally {
    clearTimeout(timer);
    signal.removeEventListener('abort', relay);
  }
}

export async function promotionsPage(ctx) {
  const { params: p, signal } = ctx;
  if (currentView(p) !== 'detail') return scaleViewsPage(ctx);          // the two simple views (What should be scaled, Performance in scaling) are the first tabs; this page is the third
  const G = globalParams(p);
  document.title = 'All moves, with detail · Test to scale · Lonvera';
  const d = await loadPromotions(p, signal);
  if (!ctx.alive()) return;
  setRangeInfo(p, d.window_info, null, { unclassified: false });
  const s = d.summary;
  const through = d.data_through;
  const daily = d.window_info.daily_start;
  const L = d.labels || {};
  const f = d.filters;

  // ---------- what a move is (always visible) ----------
  const help = h('section', { class: 'tts-help', 'data-testid': 'tts-help' },
    h('p', null, h('strong', null, 'What counts as a move. '), d.definition.move),
    h('p', null, h('strong', null, 'Meta only. '), `Counts are creatives, not ads. Google has no creative grain, and ${(L.purchases || 'purchases').toLowerCase()} are Meta's (or Upstack's, see Numbers from above), never added across platforms. `,
      'Spend, clicks and the scaling numbers run to ', h('strong', null, fmt.day(through)), ', the latest day of data.'),
    h('p', null, h('strong', null, 'Held up? '), d.definition.held),
    d.stage_moves ? h('p', { class: 'muted', 'data-testid': 'tts-built' }, `Precomputed nightly: rebuilt ${fmt.dateTime(d.stage_moves.built_at)}, through ${d.stage_moves.data_through ? fmt.day(d.stage_moves.data_through) : '–'}.`) : null);

  // ---------- filters that change every number on the page ----------
  const keep = { ...(p.hide_bulk ? { hide_bulk: p.hide_bulk } : {}), ...(p.bulk_min ? { bulk_min: p.bulk_min } : {}), ...(p.min_test_spend ? { min_test_spend: p.min_test_spend } : {}), ...(p.grain ? { grain: p.grain } : {}) };
  const productCtl = productFilter({ products: d.products, p, view: 'detail', keep, prefix: 'tts', titleOf: (n) => `${fmt.int(n.moved)} moved, ${fmt.int(n.launched)} began in testing` });
  productCtl.id = 'tts-product-ctl';
  const bulkLabel = bulkCheck('tts-hide-bulk', f);
  const minTest = numberInput('tts-min-test', 'Min test spend', f.min_test_spend || '', 0, '$', (v) => setParams({ min_test_spend: v && Number(v) > 0 ? String(Math.floor(Number(v))) : null }), { placeholder: '0', 'aria-label': 'Minimum test spend in dollars' });
  minTest.title = 'Only creatives that spent at least this much in testing (before any move) count: in the moved table, the % reached scaling and the not-scaled list';
  const bulkMin = numberInput('tts-bulk-min', 'Bulk day from', f.bulk_min === 40 ? '' : f.bulk_min, 1, '', (v) => setParams({ bulk_min: v && Number(v) >= 1 && Number(v) !== 40 ? String(Math.floor(Number(v))) : null }), { placeholder: '40', 'aria-label': 'Bulk move day threshold' });
  bulkMin.append(h('span', { class: 'muted small' }, 'creatives'));
  bulkMin.title = 'Bulk move day: this many creatives or more entering scaling on one day';
  const filters = h('div', { class: 'ctl-row pagefilters tts-filters', 'data-testid': 'tts-filters' }, productCtl, bulkLabel, bulkMin, minTest);

  // ---------- summary strip ----------
  const lr = s.launched;
  const running = lr.already_running_at_data_start ? ` · ${fmt.int(lr.already_running_at_data_start)} already running on the first day of data are not counted` : '';
  const strip = h('section', { class: 'kpis', 'data-testid': 'tts-strip', 'aria-label': 'Test to scale summary' },
    h('div', { class: 'kpi-row' },
      kpi('launched', 'Launched in range', fmt.int(lr.n), lr.n, `${fmt.int(lr.began_in_testing)} began in testing · ${fmt.int(lr.no_test)} went straight into scaling${running}`),
      kpi('still-testing', 'Still testing', fmt.int(s.still_testing), s.still_testing, `${fmt.int(s.stopped)} stopped without scaling · of ${fmt.int(lr.began_in_testing)} that began in testing`),
      kpi('moved', 'Moved to scaling', fmt.int(s.moved.n), s.moved.n, `moved in this range, whenever they launched${s.moved.bulk ? ` · ${fmt.int(s.moved.bulk)} on bulk days` : ''}${s.moved.launched_before_data_start ? ` · ${fmt.int(s.moved.launched_before_data_start)} launched before data start` : ''}`),
      kpi('reached', '% that reached scaling', s.pct_reached_scaling == null ? '–' : fmt.pct(s.pct_reached_scaling), s.pct_reached_scaling, `${fmt.int(s.moved_from_launched)} of ${fmt.int(lr.began_in_testing)} that began in testing in this range · recent launches are still maturing`),
      kpi('median', 'Median days to scale', s.median_days_to_scale == null ? '–' : fmt.dec1(s.median_days_to_scale), s.median_days_to_scale, `first test spend to first scaling spend · ${fmt.int(s.median_days_n)} moved creatives with a known launch day`),
      kpi('test-roas', `Testing ${L.roas || 'ROAS'}`, roasX(s.testing.roas), s.testing.roas, `${money(s.testing.spend)} spent in testing before the move · ${fmt.int(s.testing.purchases)} ${(L.purchases || 'purchases').toLowerCase()} · ${s.testing.cpa != null ? money(s.testing.cpa) + ' ' + (L.cpa || 'CPA') : 'no ' + (L.cpa || 'CPA')}`),
      kpi('scale-roas', `Scaling ${L.roas || 'ROAS'} since the move`, roasX(s.scaling.roas), s.scaling.roas,
        `${money(s.scaling.spend)} spent in scaling since the move · ${fmt.int(s.scaling.purchases)} ${(L.purchases || 'purchases').toLowerCase()} · ${s.scaling.cpa != null ? money(s.scaling.cpa) + ' ' + (L.cpa || 'CPA') : 'no ' + (L.cpa || 'CPA')}`,
        [h('div', { class: 'kpi-sub', 'data-testid': 'tts-kpi-vs' }, s.scaling_vs_testing_roas != null ? `${Math.round(s.scaling_vs_testing_roas * 100)}% of testing ${L.roas || 'ROAS'} · ` : '', `${money(s.scaling_spend_last7)} in the last 7 days`)])),
    h('div', { class: 'kpi-note muted small', 'data-testid': 'tts-held-summary' }, 'Verdicts on the moved creatives: ',
      ...['held', 'softer', 'fell', 'too_early', 'no_baseline'].map((k) => h('span', { class: 'tts-held-count', 'data-held': k, title: HELD_TITLE[k] }, `${HELD_TEXT[k]} ${fmt.int(s.held[k])}`)).flatMap((e, i) => (i ? [' · ', e] : [e]))));

  // ---------- bulk move note ----------
  const bulkDays = d.bulk_days || [];
  const bulkNote = bulkDays.length || d.hidden_bulk ? h('div', { class: 'tts-bulk-note', 'data-testid': 'tts-bulk-note' },
    h('strong', null, d.hidden_bulk ? 'Bulk moves hidden: ' : 'Bulk moves in this range: '),
    bulkDays.map((b) => `${fmt.day(b.day)} (${fmt.int(b.moves)})`).join(', ') || `${fmt.int(d.hidden_bulk)} creatives`, '. ', d.definition.bulk, ' ',
    h('a', { href: '#', class: 'linkbtn', 'data-testid': 'tts-bulk-toggle', onclick: (e) => { e.preventDefault(); setParams({ hide_bulk: f.hide_bulk ? null : '1' }); } }, f.hide_bulk ? 'Show them' : 'Hide them')) : null;

  // ---------- chart ----------
  const pts = d.series.points;
  const grainBtn = (g, t) => h('a', { class: 'seg', href: href('/promotions', { ...p, grain: g }), 'data-grain': g, 'data-testid': 'tts-grain-' + g, 'aria-pressed': String(d.series.grain === g) }, t);
  const canvas = h('canvas', { id: 'tts-chart', role: 'img', 'aria-label': 'Creatives moved to scaling per ' + d.series.grain + ' and the scaling spend of those creatives' });
  const panel = h('section', { class: 'trend', 'data-testid': 'tts-chart-panel' },
    h('div', { class: 'trend-head' }, h('h2', null, 'Moves and scaling spend'),
      h('span', { class: 'ctl' }, h('span', { class: 'ctl-name' }, 'Grain'), h('span', { class: 'segs', role: 'group', 'aria-label': 'Chart grain' }, grainBtn('day', 'Day'), grainBtn('week', 'Week')))),
    h('div', { class: 'chartbox trendbox' }, canvas),
    h('div', { class: 'trend-note' }, h('span', { class: 'note' }, `Bars: creatives that moved to scaling per ${d.series.grain === 'week' ? 'week (Monday)' : 'day'}, bulk-move days in orange. Line: the daily scaling spend of the creatives that moved in this range.`)));

  // ---------- moved table ----------
  const movedRows = d.moved;
  let movedFilter;
  const movedTable = createTable({
    columns: movedColumns(G, daily), rows: movedRows, total: movedRows.length ? movedTotal(movedRows) : null, ...sortFromUrl(p), onSort: saveSortToUrl,
    rowKey: (r) => r.family_key, rowClass: (r) => (r.bulk ? 'tts-bulk-row' : ''), expand: { render: (r) => placementsNode(r) }, expandLabel: 'Show where it scaled',
    emptyText: 'No creative moved from testing to scaling in this range with these filters.', testid: 'tts-moved', onCount: (n, tot) => movedFilter && movedFilter.setCount(n, tot),
  });
  movedFilter = searchBox(movedTable, (r) => [r.display, r.family_key, r.hook, r.product.name, r.cluster, r.icp, r.pain, r.ad_type], { q: p.q || '', persist: true, testid: 'tts-moved-search', placeholder: 'Search creative, hook, tag…' });
  if (!p.q) movedFilter.setCount(movedRows.length, movedRows.length);

  // ---------- launched, not scaled ----------
  const ns = d.not_scaled;
  let nsFilter;
  const nsTable = createTable({
    columns: notScaledColumns(G, through), rows: ns.rows, sortKey: 'test_spend', sortDir: 'desc', rowKey: (r) => r.family_key,
    emptyText: 'Every creative that began in testing in this range has scaled (or nothing launched).', testid: 'tts-not-scaled', onCount: (n, tot) => nsFilter && nsFilter.setCount(n, tot),
  });
  nsFilter = searchBox(nsTable, (r) => [r.display, r.family_key, r.hook, r.product.name, r.cluster, r.icp, r.pain, r.state], { testid: 'tts-ns-search', placeholder: 'Search creative, hook, tag…' });
  nsFilter.setCount(ns.rows.length, ns.rows.length);

  // ---------- cohort funnel ----------
  const co = d.cohorts;
  const coTable = createTable({
    columns: cohortColumns(), rows: co.rows, total: co.rows.length ? co.total : null, sortKey: 'week', sortDir: 'asc', rowKey: (r) => r.week,
    emptyText: 'Nothing began in testing in this range.', testid: 'tts-cohorts',
  });

  const notes = [...(d.notes || []), ...(ns.total > ns.shown ? [`Showing the ${fmt.int(ns.shown)} not-scaled creatives with the most test spend, of ${fmt.int(ns.total)}.`] : [])];
  ctx.mount(h('div', { class: 'page', 'data-level': 'promotions' },
    ...pageTop('detail', p),
    help,
    filters,
    strip,
    bulkNote,
    panel,
    h('div', { class: 'tablehead' }, h('h2', { id: 'level-title' }, `Moved to scaling · ${fmt.int(movedRows.length)}`), h('span', { class: 'src-badge', 'data-testid': 'src-badge' }, 'Numbers: ' + (d.numbers || 'Meta')), movedFilter.el),
    h('div', { class: 'muted small tts-foot' }, `Test = Testing-campaign numbers before the first scaling day. Scaling = Scaling-campaign numbers since the move, through ${fmt.day(through)}. * Already running on the first day of data (${fmt.day(daily)}): the launch day is not known.`),
    movedTable.el,
    h('div', { class: 'tablehead' }, h('h2', null, `Launched in this range, not scaled · ${fmt.int(ns.total)}`), nsFilter.el),
    h('div', { class: 'muted small tts-foot' }, 'Creatives that began in testing in this range and have no scaling spend yet. Still testing: spent on the latest day of data or the day before. Recent launches may still scale.'),
    ns.rows.length ? nsTable.el : empty('Every creative that began in testing in this range has scaled (or nothing launched).'),
    h('div', { class: 'tablehead' }, h('h2', null, 'By launch week')),
    h('div', { class: 'muted small tts-foot' }, 'Creatives grouped by the week they first spent, among those that began in testing. Weeks near the end of the range are still maturing, so their % reached scaling will keep rising.'),
    co.rows.length ? coTable.el : empty('Nothing began in testing in this range.'),
    ...notes.map((n) => h('div', { class: 'muted small', 'data-testid': 'tts-note' }, n)),
  ));
  // the chart needs the canvas in the page
  const labels = pts.map((x) => x.t);
  trendChart(canvas, {
    labels, grain: d.series.grain, partial: pts.map((x) => !!x.partial), stackedBars: true, stackTotals: true,
    left: { kind: 'int', title: 'Creatives moved' }, right: { kind: 'money', title: 'Scaling spend' },
    datasets: [
      { axis: 'left', type: 'bar', label: 'Moved to scaling', data: pts.map((x) => x.moves), color: '#1a5fb4', kind: 'int', stack: 'moves', opaque: true },
      { axis: 'left', type: 'bar', label: 'Moved on a bulk day', data: pts.map((x) => x.bulk_moves), color: '#c2610f', kind: 'int', stack: 'moves', opaque: true },
      { axis: 'right', type: 'line', label: 'Scaling spend of these creatives', data: pts.map((x) => x.scale_spend), color: '#1d8a56', kind: 'money' },
    ],
    extra: (i) => [`Began in testing: ${fmt.int(pts[i].launched)}`, `Scaling ${L.roas || 'ROAS'}: ${isNum(pts[i].scale_roas) ? fmt.roas(pts[i].scale_roas) : '–'}`],
  });
}
