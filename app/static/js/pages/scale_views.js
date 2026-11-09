// Marketing metrics, Test to scale, the two simple views: "What should be scaled" (testing creatives that look good enough to move) and "Performance in scaling"
// (creatives that moved and how they do since). Plain ranked tables from /api/promotions/scale (precomputed tables, one call). Meta only. Class names and test ids
// start with `sv-` (never ad-, ads-, sponsor, banner, promo: ad blockers hide those).
import { h, enc, fmt, isNum, href, globalParams, apiWindow, setParams, metricLabel, empty } from '../util.js';
import { api } from '../api.js';
import { createTable } from '../table.js';
import { setRangeInfo } from '../controls.js';
import { breadcrumbs } from '../components.js';

export const DEFAULT_VIEW = 'should_scale';
const DEFAULTS = { min_test_spend: 500, min_purchases: 5, bar_pct: 100 };
const TABS = [['should_scale', 'What should be scaled'], ['in_scaling', 'Performance in scaling'], ['detail', 'All moves, with detail']];
const money = (v) => fmt.money(v);
const roasX = (v) => (isNum(v) ? fmt.roas(v) + 'x' : '–');
const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);

export const currentView = (p) => (TABS.some(([k]) => k === p.view) ? p.view : DEFAULT_VIEW);

// the three tabs; they carry the range bar and Numbers settings (G) and the product, nothing else (each view has its own inputs)
export function viewTabs(active, p) {
  const G = globalParams(p);
  return h('nav', { class: 'segs sv-tabs', 'aria-label': 'Test to scale views', 'data-testid': 'sv-tabs' },
    TABS.map(([k, label]) => h('a', { class: 'seg', href: href('/promotions', { ...G, ...(p.product ? { product: p.product } : {}), view: k }), 'data-view': k, 'data-testid': 'sv-tab-' + k,
      'aria-pressed': String(active === k), ...(active === k ? { 'aria-current': 'page' } : {}) }, label)));
}

function apiParams(view, p) {
  const o = {};
  const w = apiWindow(p);
  if (w.source) { o.source = w.source; if (w.basis) o.basis = w.basis; }
  if (p.from && p.to) { o.from = p.from; o.to = p.to; } else o.window = p.window || '30d';
  if (p.product) o.product = p.product;
  o.view = view;
  if (view === 'should_scale') {
    for (const k of Object.keys(DEFAULTS)) if (p[k] != null && p[k] !== '') o[k] = p[k];
  } else {
    if (p.hide_bulk === '1') o.hide_bulk = 1;
    if (p.bulk_min) o.bulk_min = p.bulk_min;
  }
  return o;
}

const SLOW_MS = 15000;
// One call with a patience limit: a request that hangs or fails ends in the page's error box with Retry (app.js), never in an endless 'Loading…'.
// `tts_timeout_ms` in the URL shortens the limit (used by the tests, like the detail view).
async function load(view, p, signal) {
  const limit = Number(p.tts_timeout_ms) > 0 ? Number(p.tts_timeout_ms) : SLOW_MS;
  const ctl = new AbortController();
  let slow = false;
  const timer = setTimeout(() => { slow = true; ctl.abort(); }, limit);
  const relay = () => ctl.abort();
  signal.addEventListener('abort', relay);
  try {
    return await api('/api/promotions/scale', apiParams(view, p), { signal: ctl.signal });
  } catch (e) {
    if (slow) throw new Error(`Test to scale took longer than ${Math.round(limit / 1000)} seconds to answer. The server may be busy or the data is being rebuilt: press Retry.`);
    throw e;
  } finally {
    clearTimeout(timer);
    signal.removeEventListener('abort', relay);
  }
}

function creativeCell(r, G, flags = []) {
  const hf = r.primary_id != null ? href('/ad/' + enc(r.primary_id), G) : null;
  return h('div', { class: 'sv-name' },
    hf ? h('a', { href: hf, class: 'rowlink rowname', title: r.display, 'data-testid': 'sv-creative-link' }, r.display) : h('span', { class: 'rowname' }, r.display),
    ...flags);
}

function productChips(d, p, view) {
  const G = globalParams(p);
  const keep = view === 'should_scale' ? Object.fromEntries(Object.keys(DEFAULTS).filter((k) => p[k] != null && p[k] !== '').map((k) => [k, p[k]])) : { ...(p.hide_bulk ? { hide_bulk: p.hide_bulk } : {}), ...(p.bulk_min ? { bulk_min: p.bulk_min } : {}) };
  const chip = (label, slug, n) => h('a', { class: 'seg', href: href('/promotions', { ...G, ...keep, view, product: slug }), 'data-testid': 'sv-product-' + (slug || 'all'), 'aria-pressed': String((p.product || null) === slug),
    title: n != null ? `${fmt.int(n)} creative${n === 1 ? '' : 's'}` : null }, label);
  return h('div', { class: 'ctl', 'data-testid': 'sv-product-filter' }, h('span', { class: 'ctl-name' }, 'Product'),
    h('span', { class: 'segs', role: 'group', 'aria-label': 'Product' }, chip('All', null, null), (d.products || []).map((x) => chip(x.name, x.slug, x.n))));
}

function numberInput(id, label, value, min, suffixPre, onApply, extra = {}) {
  const el = h('input', { type: 'number', id, 'data-testid': id, min: String(min), step: '1', value: String(value), 'aria-label': label, class: 'tts-num sv-num', ...extra });
  const go = () => onApply(el.value.trim());
  el.addEventListener('change', go);
  el.addEventListener('keydown', (e) => { if (e.key === 'Enter') go(); });
  return h('label', { class: 'ctl', for: id }, h('span', { class: 'ctl-name' }, label), suffixPre ? h('span', { class: 'muted' }, suffixPre) : null, el);
}

const confText = { high: 'High', medium: 'Medium', low: 'Low' };
function confChip(c, d) {
  return h('span', { class: 'badge sv-conf sv-conf-' + c.level, 'data-conf': c.level, title: `${confText[c.level]} confidence: ${d.definition.confidence[c.level]} in testing` }, confText[c.level]);
}
function vsChip(v) {
  const how = v.beats === 'cpa' ? ' (on cost per purchase only)' : '';
  const t = `Return bar for this product: ROAS ${roasX(v.bar_roas)}${v.bar_cpa != null ? `, CPA ${money(v.bar_cpa)}` : ''}${v.ratio != null ? `. This creative's testing ROAS is ${Math.round(v.ratio * 100)}% of the bar.` : ''}`;
  return h('span', { class: 'badge sv-vs sv-vs-' + v.word, 'data-vs': v.word, title: t }, v.word + how);
}

function shouldColumns(d, G) {
  const L = d.labels || {};
  return [
    { key: 'rank', label: '#', num: true, sort: (r) => r._rank, cell: (r) => String(r._rank), title: 'Rank' },
    { key: 'display', label: 'Creative', sticky: true, cls: 'labelcol', sort: (r) => r.display, cell: (r) => creativeCell(r, G) },
    { key: 'product', label: 'Product', sort: (r) => r.product.name, cell: (r) => h('span', { class: 'tts-product', 'data-product': r.product.slug }, r.product.name) },
    { key: 'days', label: 'Days in testing', num: true, sort: (r) => r.days_in_testing, title: 'Days from its first spend to the latest day of data (at least that many when it was already running on the first day of data)',
      cell: (r) => (r.launched_before_data_start ? '≥ ' : '') + fmt.int(r.days_in_testing) },
    { key: 'spend', label: 'Testing spend', num: true, sort: (r) => r.test.spend, cell: (r) => money(r.test.spend) },
    { key: 'purchases', label: () => metricLabel('purchases', cap((L.purchases || 'Purchases').toLowerCase())), num: true, sort: (r) => r.test.purchases, cell: (r) => fmt.int(r.test.purchases) },
    { key: 'roas', label: () => metricLabel('roas', 'ROAS'), num: true, sort: (r) => r.test.roas, cell: (r) => roasX(r.test.roas) },
    { key: 'cpa', label: () => metricLabel('cpa', 'CPA'), num: true, sort: (r) => r.test.cpa, cell: (r) => money(r.test.cpa) },
    { key: 'vs', label: 'Vs scaling average', sort: (r) => (r.vs_scaling.ratio == null ? -1 : r.vs_scaling.ratio), title: 'Above, near or below the average of the same product in scaling over the last 30 days (hover for the numbers)', cell: (r) => vsChip(r.vs_scaling) },
    { key: 'last7', label: () => `Last 7 days ${metricLabel('roas', 'ROAS')}`, num: true, sort: (r) => r.last7.roas, title: 'Testing ROAS over the last 7 days of data', cell: (r) => roasX(r.last7.roas) },
    { key: 'conf', label: 'Confidence', sort: (r) => r.confidence.purchases, title: 'How much evidence the testing numbers rest on: purchases in testing', cell: (r) => confChip(r.confidence, d) },
  ];
}

const rank = (rows) => rows.map((r, i) => ({ ...r, _rank: i + 1 }));

function barLine(d) {
  const L = d.labels || {};
  if (!d.bars.length) return null;
  return h('ul', { class: 'sv-bars', 'data-testid': 'sv-bars', 'aria-label': 'Scaling average of each product over the last 30 days' },
    d.bars.map((b) => h('li', { 'data-testid': 'sv-bar-' + b.slug, 'data-roas': String(b.roas) },
      h('strong', null, `Scaling average for ${b.name}: ${fmt.roas(b.roas)}`), ` ${L.roas || 'ROAS'}${b.cpa != null ? `, ${money(b.cpa)} ${L.cpa || 'CPA'}` : ''} · ${money(b.spend)} spent in scaling, ${fmt.day(b.day_from)} – ${fmt.day(b.day_to)}`)));
}

function renderShould(ctx, d, p) {
  const G = globalParams(p);
  const f = d.filters;
  const set = (k, v, dflt) => setParams({ [k]: v === '' || Number(v) === dflt || !(Number(v) >= 0) ? null : String(Math.floor(Number(v))) });
  const minSpend = numberInput('sv-min-spend', 'Min testing spend', f.min_test_spend, 0, '$', (v) => set('min_test_spend', v, DEFAULTS.min_test_spend));
  const minPur = numberInput('sv-min-purchases', 'Min ' + (d.labels.purchases || 'purchases').toLowerCase(), f.min_purchases, 0, '', (v) => set('min_purchases', v, DEFAULTS.min_purchases));
  const barPct = numberInput('sv-bar-pct', 'Return bar', f.bar_pct, 1, '', (v) => set('bar_pct', v === '0' ? '' : v, DEFAULTS.bar_pct));
  barPct.append(h('span', { class: 'muted' }, '% of the scaling average'));
  const changed = Object.keys(DEFAULTS).some((k) => Number(f[k]) !== DEFAULTS[k]);
  const reset = changed ? h('a', { href: '#', class: 'linkbtn', 'data-testid': 'sv-reset', onclick: (e) => { e.preventDefault(); setParams({ min_test_spend: null, min_purchases: null, bar_pct: null }); } }, 'Reset to the defaults') : null;
  const controls = h('div', { class: 'ctl-row tts-filters sv-controls', 'data-testid': 'sv-controls' }, productChips(d, p, 'should_scale'), minSpend, minPur, barPct, reset);
  const s = d.summary;
  const summary = h('div', { class: 'sv-summary', 'data-testid': 'sv-summary' },
    h('strong', { 'data-testid': 'sv-count', 'data-n': String(s.qualify) }, `${fmt.int(s.qualify)} of ${fmt.int(s.testing_live)} creatives in testing qualify`),
    s.qualify ? ` (${fmt.int(s.confidence.high)} high, ${fmt.int(s.confidence.medium)} medium, ${fmt.int(s.confidence.low)} low confidence).` : '.',
    ' Testing creatives that have not moved to scaling and spent in the last 3 days.');
  const cols = shouldColumns(d, G);
  const mk = (rows, testid, emptyText) => createTable({ columns: cols, rows: rank(rows), rowKey: (r) => r.family_key, tableClass: 'compact', emptyText, testid });
  const main = mk(d.rows, 'sv-table', 'Nothing qualifies with these rules.');
  const missTable = !d.rows.length && d.near_misses.length ? mk(d.near_misses, 'sv-misses', '') : null;
  const body = d.rows.length ? main.el : [
    empty('Nothing in testing qualifies right now: no creative that meets the spend and purchase minimums also beats its product\'s scaling average. Lower a minimum or the return bar above to see more.'),
    missTable ? h('div', { class: 'tablehead' }, h('h2', null, 'Nearest misses')) : null,
    missTable ? h('div', { class: 'muted small sv-foot' }, 'These meet the spend, purchase and recent-spend minimums but fall short of the return bar. Closest first.') : null,
    missTable ? missTable.el : null];
  const foot = [...(d.notes || [])].map((n) => h('div', { class: 'muted small', 'data-testid': 'sv-note' }, n));
  return [
    h('p', { class: 'sv-lead', 'data-testid': 'sv-rule' }, d.definition.rule),
    h('p', { class: 'sv-lead muted', 'data-testid': 'sv-screen' }, d.definition.screen),
    controls, barLine(d), summary,
    h('div', { class: 'tablehead' }, h('h2', { id: 'level-title' }, 'What should be scaled'), h('span', { class: 'src-badge', 'data-testid': 'src-badge' }, 'Numbers: ' + (d.numbers || 'Meta'))),
    ...[].concat(body), ...foot];
}

// ---------------------------------------------------------------------------------------------------------------- view 2
const statusTitle = {
  holding: 'The last 7 days return within 10% of, or more than, the ROAS since the move',
  fading: 'The last 7 days return more than 10% less than the ROAS since the move',
  stopped: 'No scaling spend in the last 7 days',
  'too early': 'Under $200 spent or fewer than 3 days in scaling: not enough to call it',
};
const slug = (s) => String(s).replace(/\s+/g, '-');
function statusChip(st) { return h('span', { class: 'badge sv-status sv-status-' + slug(st), 'data-status': st, title: statusTitle[st] }, st); }
function vsTestingChip(v, r) {
  const t = v.ratio != null ? `Scaling ROAS since the move is ${Math.round(v.ratio * 100)}% of its testing ROAS (testing ${roasX(r.test.roas)}, scaling ${roasX(r.scale.roas)})` : 'Not enough scaling yet, or its testing earned nothing, to compare';
  return h('span', { class: 'badge sv-vs sv-vt-' + slug(v.word), 'data-vs': v.word, title: t }, v.word);
}

function scalingColumns(d, G) {
  const L = d.labels || {};
  const flags = (r) => (r.bulk ? [h('span', { class: 'badge tts-bulk', 'data-testid': 'sv-bulk-chip', title: `${fmt.int(r.moves_that_day)} creatives entered scaling on ${fmt.day(r.moved)}: a mass relaunch, not a test winner` }, `Bulk move · ${fmt.int(r.moves_that_day)} that day`)] : []);
  return [
    { key: 'display', label: 'Creative', sticky: true, cls: 'labelcol', sort: (r) => r.display, cell: (r) => creativeCell(r, G, flags(r)) },
    { key: 'product', label: 'Product', sort: (r) => r.product.name, cell: (r) => h('span', { class: 'tts-product', 'data-product': r.product.slug }, r.product.name) },
    { key: 'moved', label: 'Moved', sort: (r) => r.moved, title: 'First day with spend in a Scaling campaign', cell: (r) => fmt.day(r.moved) },
    { key: 'since', label: 'Days since move', num: true, sort: (r) => r.days_since_move, title: 'From the move day to the latest day of data', cell: (r) => fmt.int(r.days_since_move) },
    { key: 'spend', label: 'Scaling spend', num: true, sort: (r) => r.scale.spend, title: 'Spend in Scaling campaigns since the move', cell: (r) => money(r.scale.spend) },
    { key: 'purchases', label: () => metricLabel('purchases', cap((L.purchases || 'Purchases').toLowerCase())), num: true, sort: (r) => r.scale.purchases, cell: (r) => fmt.int(r.scale.purchases) },
    { key: 'roas', label: () => `${metricLabel('roas', 'ROAS')} since move`, num: true, sort: (r) => r.scale.roas, cell: (r) => roasX(r.scale.roas) },
    { key: 'cpa', label: () => `${metricLabel('cpa', 'CPA')} since move`, num: true, sort: (r) => r.scale.cpa, cell: (r) => money(r.scale.cpa) },
    { key: 'l7roas', label: () => `Last 7 days ${metricLabel('roas', 'ROAS')}`, num: true, sort: (r) => r.last7.roas, cell: (r) => roasX(r.last7.roas) },
    { key: 'l7cpa', label: () => `Last 7 days ${metricLabel('cpa', 'CPA')}`, num: true, sort: (r) => r.last7.cpa, cell: (r) => money(r.last7.cpa) },
    { key: 'vs', label: 'Vs its testing', sort: (r) => (r.vs_testing.ratio == null ? -1 : r.vs_testing.ratio), title: 'Scaling ROAS since the move against its testing ROAS: better, about the same or worse (hover for the numbers)', cell: (r) => vsTestingChip(r.vs_testing, r) },
    { key: 'status', label: 'Status', sort: (r) => r.status, title: 'Holding, fading, stopped or too early (hover a status for the rule)', cell: (r) => statusChip(r.status) },
  ];
}

function totalsColumns(d) {
  const L = d.labels || {};
  return [
    { key: 'name', label: 'Product', sticky: true, cls: 'labelcol', sort: (t) => t.name, total: () => 'All products', cell: (t) => h('span', { class: 'tts-product', 'data-product': t.slug }, t.name) },
    { key: 'n', label: 'Creatives', num: true, sort: (t) => t.creatives, total: (t) => fmt.int(t.creatives), cell: (t) => fmt.int(t.creatives) },
    { key: 'spend', label: 'Scaling spend', num: true, sort: (t) => t.scale.spend, total: (t) => money(t.scale.spend), cell: (t) => money(t.scale.spend) },
    { key: 'purchases', label: () => metricLabel('purchases', cap((L.purchases || 'Purchases').toLowerCase())), num: true, sort: (t) => t.scale.purchases, total: (t) => fmt.int(t.scale.purchases), cell: (t) => fmt.int(t.scale.purchases) },
    { key: 'roas', label: () => `${metricLabel('roas', 'ROAS')} since move`, num: true, sort: (t) => t.scale.roas, total: (t) => roasX(t.scale.roas), cell: (t) => roasX(t.scale.roas), title: 'Total revenue divided by total spend of the product\'s creatives (spend-weighted), not an average of their ROAS' },
    { key: 'cpa', label: () => `${metricLabel('cpa', 'CPA')} since move`, num: true, sort: (t) => t.scale.cpa, total: (t) => money(t.scale.cpa), cell: (t) => money(t.scale.cpa) },
    { key: 'l7spend', label: 'Last 7 days spend', num: true, sort: (t) => t.last7.spend, total: (t) => money(t.last7.spend), cell: (t) => money(t.last7.spend) },
    { key: 'l7roas', label: () => `Last 7 days ${metricLabel('roas', 'ROAS')}`, num: true, sort: (t) => t.last7.roas, total: (t) => roasX(t.last7.roas), cell: (t) => roasX(t.last7.roas) },
  ];
}

function renderScaling(ctx, d, p) {
  const G = globalParams(p);
  const f = d.filters;
  const bulkCb = h('input', { type: 'checkbox', id: 'sv-hide-bulk', 'data-testid': 'sv-hide-bulk', checked: f.hide_bulk });
  bulkCb.addEventListener('change', () => setParams({ hide_bulk: bulkCb.checked ? '1' : null }));
  const controls = h('div', { class: 'ctl-row tts-filters sv-controls', 'data-testid': 'sv-controls' }, productChips(d, p, 'in_scaling'),
    h('div', { class: 'ctl' }, h('label', { class: 'check', title: `A day when ${f.bulk_min} or more creatives entered scaling is a mass relaunch, not a set of test winners. Hiding them removes those creatives from every number on this page.` }, bulkCb, ' Hide bulk moves')));
  const bulkNote = d.bulk_days.length || d.hidden_bulk ? h('div', { class: 'tts-bulk-note', 'data-testid': 'sv-bulk-note' }, h('strong', null, d.hidden_bulk ? 'Bulk moves hidden: ' : 'Bulk moves in this range: '),
    d.bulk_days.map((b) => `${fmt.day(b.day)} (${fmt.int(b.moves)})`).join(', ') || `${fmt.int(d.hidden_bulk)} creatives`, '. ', d.definition.bulk) : null;
  const s = d.summary;
  const summary = h('div', { class: 'sv-summary', 'data-testid': 'sv-summary' }, h('strong', { 'data-testid': 'sv-count', 'data-n': String(s.n) }, `${fmt.int(s.n)} creatives moved to scaling`),
    s.n ? ` · ${['holding', 'fading', 'stopped', 'too early'].map((k) => `${fmt.int(s.status[k])} ${k}`).join(' · ')}` : '');
  const totalsRows = d.totals;
  const totals = createTable({ columns: totalsColumns(d), rows: totalsRows, total: totalsRows.length > 1 && d.total_all ? d.total_all : null, rowKey: (t) => t.slug, tableClass: 'compact', emptyText: 'Nothing moved to scaling in this range.', testid: 'sv-totals' });
  const table = createTable({ columns: scalingColumns(d, G), rows: d.rows, rowKey: (r) => r.family_key, rowClass: (r) => (r.bulk ? 'tts-bulk-row' : ''), tableClass: 'compact',
    emptyText: 'No creative moved from testing to scaling in this range with these filters.', testid: 'sv-table' });
  const defs = h('details', { class: 'sv-defs', 'data-testid': 'sv-defs' }, h('summary', null, 'How "vs its testing" and the status are worked out'),
    h('p', null, d.definition.vs_testing), h('p', null, d.definition.status));
  return [
    h('p', { class: 'sv-lead', 'data-testid': 'sv-caption' }, 'Creatives that moved from testing to scaling in the range above, biggest scaling spend first, and whether they are holding up since the move.'),
    controls, bulkNote, summary,
    h('div', { class: 'tablehead' }, h('h2', null, 'By product'), h('span', { class: 'src-badge', 'data-testid': 'src-badge' }, 'Numbers: ' + (d.numbers || 'Meta'))),
    totals.el,
    h('div', { class: 'tablehead' }, h('h2', { id: 'level-title' }, `Performance in scaling · ${fmt.int(d.rows.length)}`)),
    table.el, defs,
    ...(d.notes || []).map((n) => h('div', { class: 'muted small', 'data-testid': 'sv-note' }, n))];
}

export async function scaleViewsPage(ctx) {
  const { params: p, signal } = ctx;
  const view = currentView(p);
  document.title = (view === 'in_scaling' ? 'Performance in scaling' : 'What should be scaled') + ' · Test to scale · Marketing metrics';
  const d = await load(view, p, signal);
  if (!ctx.alive()) return;
  setRangeInfo(p, d.window_info, null, { unclassified: false });
  const G = globalParams(p);
  const inner = view === 'in_scaling' ? renderScaling(ctx, d, p) : renderShould(ctx, d, p);
  ctx.mount(h('div', { class: 'page sv-page', 'data-level': 'promotions', 'data-view': view },
    h('div', { class: 'pagehead' }, h('div', { class: 'titleline' }, h('h1', 'Test to scale'), h('span', { class: 'badge muted-badge' }, 'Meta only · creatives'), h('span', { class: 'spacer' }))),
    h('div', { class: 'crumbrow' }, breadcrumbs([{ label: 'Marketing metrics', href: href('/', G) }, { label: 'Test to scale' }])),
    viewTabs(view, p),
    ...inner,
    h('div', { class: 'muted small sv-foot', 'data-testid': 'sv-built' }, d.stage_moves ? `Precomputed nightly: rebuilt ${fmt.dateTime(d.stage_moves.built_at)}, through ${d.stage_moves.data_through ? fmt.day(d.stage_moves.data_through) : '–'}. Spend, clicks and the move itself are Meta's; scaling numbers run to ${fmt.day(d.data_through)}, the latest day of data.` : '')));
}
