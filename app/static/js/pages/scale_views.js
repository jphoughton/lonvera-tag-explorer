// Marketing metrics, Test to scale, the two simple views: "What should be scaled" (testing creatives that look good enough to move) and "Performance in scaling"
// (creatives that moved and how they do since). Plain ranked tables from /api/promotions/scale (precomputed tables, one call). Meta only. Class names and test ids
// start with `sv-` (never ad-, ads-, sponsor, banner, promo: ad blockers hide those).
import { h, enc, fmt, isNum, href, globalParams, apiWindow, setParams, metricLabel, empty, thumb } from '../util.js';
import { api } from '../api.js';
import { createTable } from '../table.js';
import { setRangeInfo } from '../controls.js';
import { breadcrumbs } from '../components.js';
import { pageHeader } from '../ui/pagehead.js';
import { seg } from '../ui/pagefilters.js';

export const DEFAULT_VIEW = 'should_scale';
const DEFAULTS = { min_test_spend: 500, min_purchases: 5, bar_pct: 100 };
const KILL_KEYS = ['kill_min_spend', 'kill_roas_pct'];
const TABS = [['should_scale', 'What should be scaled'], ['should_kill', 'What should be killed'], ['in_scaling', 'Performance in scaling'], ['detail', 'All moves, with detail']];
const money = (v) => fmt.money(v);
const PTITLE = (x) => (x.n != null ? `${fmt.int(x.n)} creative${x.n === 1 ? '' : 's'}` : null);
const roasX = (v) => (isNum(v) ? fmt.roas(v) + 'x' : '–');
const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);

export const currentView = (p) => (TABS.some(([k]) => k === p.view) ? p.view : DEFAULT_VIEW);

// the four tabs; they carry the range bar and Numbers settings (G) and the product, nothing else (each view has its own inputs)
const TAB_HINT = {
  should_scale: 'Testing creatives that look good enough to move into scaling',
  should_kill: 'Testing creatives that have spent enough and fall clearly below scaling',
  in_scaling: 'Creatives that moved into scaling and how they do since',
  detail: 'Every move in the range, with the numbers around it',
};
export function viewTabs(active, p) {
  const G = globalParams(p);
  return h('nav', { class: 'segs sv-tabs', 'aria-label': 'Test to scale views', 'data-testid': 'sv-tabs' },
    TABS.map(([k, label]) => h('a', { class: 'seg', href: href('/promotions', { ...G, ...(p.product ? { product: p.product } : {}), view: k }), 'data-view': k, 'data-testid': 'sv-tab-' + k, title: TAB_HINT[k],
      'aria-pressed': String(active === k), ...(active === k ? { 'aria-current': 'page' } : {}) }, label)));
}

export const VIEW_TITLE = { should_scale: 'What should be scaled', should_kill: 'What should be killed', in_scaling: 'Performance in scaling', detail: 'All moves, with detail' };
const DESCRIPTION = {
  should_scale: 'Creatives still in testing that look good enough to move into scaling, best first.',
  should_kill: 'Creatives in testing that have spent enough and are clearly below scaling, so you can turn them off.',
  in_scaling: 'Creatives that moved from testing to scaling in the range, and whether they are holding up since the move.',
  detail: 'Creatives that moved from Testing to Scaling in the range above, and what happened around the move.',
};

// One vocabulary for the whole page, written once and shown on every tab.
export function wordsHelp() {
  const p = (strong, text) => h('p', null, h('strong', null, strong + ' '), text);
  return h('details', { class: 'sv-defs sv-words', 'data-testid': 'sv-words' }, h('summary', null, 'What the words on this page mean'),
    p('Testing and scaling.', 'Testing campaigns try a new creative out. Scaling campaigns spend behind creatives that proved themselves. A move is a creative that spent in testing and later spent in scaling.'),
    p('Too early.', 'Not enough spend or days yet to judge, so no verdict is given. It is the one word used for this everywhere on the page: in the held up verdict, the status in scaling and the comparison chips.'),
    p('Not scaled.', 'A creative that began in testing and has no scaling spend yet. It is either still testing (spent on the latest day of data or the day before) or stopped.'),
    p('Straight into scaling.', 'A creative that never had testing spend. This page does not call these "untested".'),
    p('Return on ad spend (ROAS).', 'Revenue divided by ad spend. A column labelled ROAS shows it as a multiple, so 1.40x means $1.40 back for each $1 spent.'),
    p('Cost per acquisition (CPA).', 'Ad spend divided by purchases. With Numbers from set to Upstack, purchases are new customers (NC, short for new customer), so NC ROAS and NC CPA are the return and the cost for a first-time buyer.'));
}

// Header, breadcrumbs, tab bar and the shared word list: the same top of page on every tab.
export function pageTop(view, p) {
  const G = globalParams(p);
  return [
    pageHeader({ title: 'Test to scale', badge: 'Meta only · creatives', description: DESCRIPTION[view] }),
    h('div', { class: 'crumbrow' }, breadcrumbs([{ label: 'Lonvera', href: href('/', G) }, { label: 'Test to scale' }])),
    viewTabs(view, p),
    wordsHelp()];
}

// Product filter, one control on every tab. `prefix` keeps each tab's own test ids (sv-product-* or tts-product-*); `keep` = the params the tab carries along.
export function productFilter({ products, p, view, keep, prefix, titleOf }) {
  const G = globalParams(p);
  const active = p.product || '';
  const opts = [['', 'All', null, prefix + '-product-all'], ...(products || []).map((x) => [x.slug, x.name, titleOf(x), prefix + '-product-' + x.slug])];
  return seg({ name: 'Product', options: opts, value: active, testid: prefix + '-product-filter', href: (v) => href('/promotions', { ...G, ...keep, view, product: v || null }) });
}

// The creative cell on every tab: thumbnail, name (opens the creative page) and flags. The detail tab adds product, type and tags under the name.
export function creativeCell(r, G, { flags = [], testid, extra = false } = {}) {
  const hf = r.primary_id != null ? href('/ad/' + enc(r.primary_id), G) : null;
  const tags = extra ? [r.cluster, r.icp, r.pain].filter((x) => x && x !== '(untagged)' && x !== '(none)').join(' › ') : '';
  const metaBits = [
    ...(extra ? [h('span', { class: 'tts-product', 'data-product': r.product.slug }, r.product.name),
      r.ad_type ? h('span', { class: 'chip-type ' + String(r.ad_type).toLowerCase(), title: 'Creative type' }, r.ad_type === 'VID' ? 'Video' : r.ad_type === 'IMG' ? 'Image' : r.ad_type) : null] : []),
    ...flags].filter(Boolean);
  return h('div', { class: 'cell-ad' },
    thumb(r.thumb_asset_id, 34, { name: r.display, primaryId: r.primary_id }),
    h('div', { class: 'cell-ad-text' },
      h('div', { class: 'nm' }, hf ? h('a', { href: hf, class: 'rowlink rowname', title: r.display, 'data-testid': testid }, r.display) : h('span', { class: 'rowname' }, r.display)),
      metaBits.length ? h('div', { class: 'meta' }, metaBits) : null,
      tags ? h('div', { class: 'meta sub', title: tags }, tags) : null));
}

// The "Hide bulk moves" check, same label and tooltip on every tab; `id` is the tab's own id and test id.
export function bulkCheck(id, f) {
  const cb = h('input', { type: 'checkbox', id, 'data-testid': id, checked: f.hide_bulk });
  cb.addEventListener('change', () => setParams({ hide_bulk: cb.checked ? '1' : null }));
  return h('div', { class: 'ctl' }, h('label', { class: 'check', title: `A day when ${f.bulk_min} or more creatives entered scaling is a mass relaunch, not a set of test winners. Hiding them removes those creatives from every number on this page.` }, cb, ' Hide bulk moves'));
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
  } else if (view === 'should_kill') {
    for (const k of KILL_KEYS) if (p[k] != null && p[k] !== '') o[k] = p[k];
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

export function numberInput(id, label, value, min, suffixPre, onApply, extra = {}) {
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
    { key: 'display', label: 'Creative', sticky: true, cls: 'labelcol', sort: (r) => r.display, cell: (r) => creativeCell(r, G, { testid: 'sv-creative-link' }) },
    { key: 'product', label: 'Product', sort: (r) => r.product.name, cell: (r) => h('span', { class: 'tts-product', 'data-product': r.product.slug }, r.product.name) },
    { key: 'days', label: 'Days in testing', num: true, sort: (r) => r.days_in_testing, title: 'Days from its first spend to the latest day of data (at least that many when it was already running on the first day of data)',
      cell: (r) => (r.launched_before_data_start ? '≥ ' : '') + fmt.int(r.days_in_testing) },
    { key: 'spend', label: 'Testing spend', num: true, sort: (r) => r.test.spend, cell: (r) => money(r.test.spend) },
    { key: 'purchases', label: () => metricLabel('purchases', cap((L.purchases || 'Purchases').toLowerCase())), num: true, sort: (r) => r.test.purchases, cell: (r) => fmt.int(r.test.purchases) },
    { key: 'roas', label: () => metricLabel('roas', 'ROAS'), title: 'Return on ad spend (ROAS): revenue divided by ad spend', num: true, sort: (r) => r.test.roas, cell: (r) => roasX(r.test.roas) },
    { key: 'cpa', label: () => metricLabel('cpa', 'CPA'), title: 'Cost per acquisition (CPA): ad spend divided by purchases', num: true, sort: (r) => r.test.cpa, cell: (r) => money(r.test.cpa) },
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
  const controls = h('div', { class: 'ctl-row pagefilters tts-filters sv-controls', 'data-testid': 'sv-controls' }, productFilter({ products: d.products, p, view: 'should_scale', keep: Object.fromEntries(Object.keys(DEFAULTS).filter((k) => p[k] != null && p[k] !== '').map((k) => [k, p[k]])), prefix: 'sv', titleOf: PTITLE }), minSpend, minPur, barPct, reset);
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

// ---------------------------------------------------------------------------------------------------------------- view 3 (what should be killed)
const KDEFAULT_PCT = 60;
function farChip(v) {
  const t = `Testing ROAS is ${Math.round(v.ratio * 100)}% of the ${roasX(v.scaling_roas)} scaling average of this product (scaling cost per purchase ${money(v.scaling_cpa)})`;
  return h('span', { class: 'badge kv-vs kv-vs-' + slug(v.word), 'data-vs': v.word, title: t }, v.word);
}
function killConf(c, d) {
  return h('span', { class: 'badge sv-conf sv-conf-' + c.level, 'data-conf': c.level, title: `${confText[c.level]} confidence: ${d.definition.confidence[c.level]}` }, confText[c.level]);
}
function killColumns(d, G, withRank) {
  const L = d.labels || {};
  const cols = [
    { key: 'display', label: 'Creative', sticky: true, cls: 'labelcol', sort: (r) => r.display, cell: (r) => creativeCell(r, G, { testid: 'sv-creative-link' }) },
    { key: 'product', label: 'Product', sort: (r) => r.product.name, cell: (r) => h('span', { class: 'tts-product', 'data-product': r.product.slug }, r.product.name) },
    { key: 'days', label: 'Days in testing', num: true, sort: (r) => r.days_in_testing, title: 'Days from its first spend to the latest day of data', cell: (r) => (r.launched_before_data_start ? '≥ ' : '') + fmt.int(r.days_in_testing) },
    { key: 'spend', label: 'Testing spend', num: true, sort: (r) => r.test.spend, cell: (r) => money(r.test.spend) },
    { key: 'purchases', label: () => metricLabel('purchases', cap((L.purchases || 'Purchases').toLowerCase())), num: true, sort: (r) => r.test.purchases, cell: (r) => fmt.int(r.test.purchases) },
    { key: 'roas', label: () => metricLabel('roas', 'ROAS'), title: 'Return on ad spend (ROAS): revenue divided by ad spend', num: true, sort: (r) => r.test.roas, cell: (r) => roasX(r.test.roas) },
    { key: 'cpa', label: () => metricLabel('cpa', 'CPA'), title: 'Cost per acquisition (CPA): ad spend divided by purchases', num: true, sort: (r) => r.test.cpa, cell: (r) => money(r.test.cpa) },
    { key: 'vs', label: 'Vs scaling average', sort: (r) => r.vs_scaling.ratio, title: 'Far below or below the average of the same product in scaling over the last 30 days (hover for the numbers)', cell: (r) => farChip(r.vs_scaling) },
    { key: 'l7spend', label: 'Last 7 days spend', num: true, sort: (r) => r.last7.spend, title: 'Testing spend over the last 7 days of data: the money that stops when it is turned off', cell: (r) => money(r.last7.spend) },
    { key: 'l7roas', label: () => `Last 7 days ${metricLabel('roas', 'ROAS')}`, title: 'Return on ad spend (ROAS): revenue divided by ad spend', num: true, sort: (r) => r.last7.roas, cell: (r) => roasX(r.last7.roas) },
    { key: 'wasted', label: 'Wasted spend', num: true, sort: (r) => r.wasted, title: d.definition.wasted, cell: (r) => money(r.wasted) },
    { key: 'reason', label: 'Why', sort: (r) => r.reason, cls: 'kv-reason', cell: (r) => h('span', { 'data-testid': 'kv-reason' }, r.reason) },
    { key: 'conf', label: 'Confidence', sort: (r) => r.confidence.spend_x, title: 'How much spend the call rests on, against the product\'s scaling cost per purchase', cell: (r) => killConf(r.confidence, d) },
  ];
  if (withRank) cols.unshift({ key: 'rank', label: '#', num: true, sort: (r) => r._rank, cell: (r) => String(r._rank), title: 'Rank' });
  return cols;
}

function renderKill(ctx, d, p) {
  const G = globalParams(p);
  const f = d.filters;
  const set = (k, v, dflt) => setParams({ [k]: v === '' || !(Number(v) > 0) || Number(v) === dflt ? null : String(Math.floor(Number(v))) });
  const spendEl = numberInput('kv-min-spend', 'Min testing spend', f.kill_min_spend == null ? '' : f.kill_min_spend, 0, '$', (v) => set('kill_min_spend', v, null), { placeholder: 'auto' });
  spendEl.append(h('span', { class: 'muted' }, 'blank = 2 times the scaling cost per purchase'));
  const pctEl = numberInput('kv-roas-pct', 'Return on ad spend (ROAS) under', f.kill_roas_pct, 1, '', (v) => set('kill_roas_pct', v, KDEFAULT_PCT));
  pctEl.append(h('span', { class: 'muted' }, '% of the scaling average'));
  const changed = f.kill_min_spend != null || Number(f.kill_roas_pct) !== KDEFAULT_PCT;
  const reset = changed ? h('a', { href: '#', class: 'linkbtn', 'data-testid': 'kv-reset', onclick: (e) => { e.preventDefault(); setParams({ kill_min_spend: null, kill_roas_pct: null }); } }, 'Reset to the defaults') : null;
  const controls = h('div', { class: 'ctl-row pagefilters tts-filters sv-controls', 'data-testid': 'sv-controls' }, productFilter({ products: d.products, p, view: 'should_kill', keep: Object.fromEntries(KILL_KEYS.filter((k) => p[k] != null && p[k] !== '').map((k) => [k, p[k]])), prefix: 'sv', titleOf: PTITLE }), spendEl, pctEl, reset);
  const lines = d.lines.length ? h('ul', { class: 'sv-bars', 'data-testid': 'kv-lines', 'aria-label': 'What enough spend and clearly below mean for each product' },
    d.lines.map((b) => h('li', { 'data-testid': 'kv-line-' + b.slug, 'data-min-spend': String(b.min_spend) },
      h('strong', null, `${b.name}: kill line ${money(b.min_spend)} of testing spend`),
      ` · scaling average ${fmt.roas(b.scaling_roas)} ${(d.labels || {}).roas || 'ROAS'} and ${money(b.scaling_cpa)} ${(d.labels || {}).cpa || 'CPA'} · clearly below = ROAS under ${fmt.roas(b.roas_floor)} and CPA over ${money(b.cpa_floor)}, or no purchases`))) : null;
  const s = d.summary, t = d.total;
  const summary = h('div', { class: 'sv-summary', 'data-testid': 'sv-summary' },
    h('strong', { 'data-testid': 'sv-count', 'data-n': String(s.kill) }, `${fmt.int(s.kill)} of ${fmt.int(s.testing_live)} creatives in testing should be killed`),
    s.kill ? `: ${money(t.testing_spend)} spent on them so far, ${money(t.last7_spend)} of it in the last 7 days.` : '.', ' Testing creatives that have not moved to scaling and spent in the last 3 days.');
  const cols = killColumns(d, G, true);
  const mk = (rows, testid) => createTable({ columns: cols, rows: rank(rows), rowKey: (r) => r.family_key, tableClass: 'compact', emptyText: 'Nothing to kill with these rules.', testid });
  const main = mk(d.rows, 'kv-table');
  const totalRow = d.rows.length ? h('div', { class: 'sv-summary kv-total', 'data-testid': 'kv-total' }, h('strong', null, 'Total'),
    ` ${fmt.int(t.creatives)} creatives · ${money(t.testing_spend)} testing spend · `, h('strong', { 'data-testid': 'kv-total-l7' }, money(t.last7_spend)), ' last 7 days spend (what stops if you turn them off) · ', money(t.wasted), ' wasted') : null;
  const copyBtn = d.rows.length ? h('button', { type: 'button', class: 'btn kv-copy', 'data-testid': 'kv-copy', title: 'Copies the creative names, one per line, to paste into Ads Manager' }, 'Copy list') : null;
  const copied = h('span', { class: 'muted small', 'data-testid': 'kv-copied', role: 'status' }, '');
  if (copyBtn) copyBtn.addEventListener('click', async () => {
    const text = d.names.join('\n');
    try { await navigator.clipboard.writeText(text); copied.textContent = `Copied ${d.names.length} names.`; } catch (e) {
      const ta = h('textarea', { 'aria-hidden': 'true', style: 'position:fixed;left:-9999px' }, text); document.body.append(ta); ta.select();
      try { document.execCommand('copy'); copied.textContent = `Copied ${d.names.length} names.`; } catch (e2) { copied.textContent = 'Could not copy: select the names in the table instead.'; }
      ta.remove();
    }
  });
  const body = d.rows.length ? [main.el] : [empty('Nothing in testing needs killing right now: no live creative has both spent enough to judge and fallen clearly below its product\'s scaling average. Lower the minimum spend or raise the ROAS percentage above to see more.')];
  const watchTable = d.watch.length ? mk(d.watch, 'kv-watch-table') : null;
  const watch = h('details', { class: 'sv-defs kv-watch', 'data-testid': 'kv-watch' },
    h('summary', null, `Watch list · ${fmt.int(d.watch_total)} not yet`),
    h('p', { class: 'muted small' }, 'Not killed yet: no purchases after spending one to two times the product\'s scaling cost per purchase, or enough spend but ROAS below 80% of the scaling average without being clearly below. Biggest spend first.' + (d.watch_total > d.watch.length ? ` Showing the biggest ${d.watch.length}.` : '')),
    watchTable ? watchTable.el : h('p', { class: 'muted' }, 'Nothing on the watch list.'));
  return [
    h('p', { class: 'sv-lead', 'data-testid': 'sv-rule' }, d.definition.rule),
    h('p', { class: 'sv-lead muted', 'data-testid': 'sv-screen' }, d.definition.screen),
    controls, lines, summary,
    h('div', { class: 'tablehead' }, h('h2', { id: 'level-title' }, 'What should be killed'), h('span', { class: 'src-badge', 'data-testid': 'src-badge' }, 'Numbers: ' + (d.numbers || 'Meta')), copyBtn, copied),
    ...body, totalRow, watch,
    ...(d.notes || []).map((n) => h('div', { class: 'muted small', 'data-testid': 'sv-note' }, n))];
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
    { key: 'display', label: 'Creative', sticky: true, cls: 'labelcol', sort: (r) => r.display, cell: (r) => creativeCell(r, G, { flags: flags(r), testid: 'sv-creative-link' }) },
    { key: 'product', label: 'Product', sort: (r) => r.product.name, cell: (r) => h('span', { class: 'tts-product', 'data-product': r.product.slug }, r.product.name) },
    { key: 'moved', label: 'Moved', sort: (r) => r.moved, title: 'First day with spend in a Scaling campaign', cell: (r) => fmt.day(r.moved) },
    { key: 'since', label: 'Days since move', num: true, sort: (r) => r.days_since_move, title: 'From the move day to the latest day of data', cell: (r) => fmt.int(r.days_since_move) },
    { key: 'spend', label: 'Scaling spend', num: true, sort: (r) => r.scale.spend, title: 'Spend in Scaling campaigns since the move', cell: (r) => money(r.scale.spend) },
    { key: 'purchases', label: () => metricLabel('purchases', cap((L.purchases || 'Purchases').toLowerCase())), num: true, sort: (r) => r.scale.purchases, cell: (r) => fmt.int(r.scale.purchases) },
    { key: 'roas', label: () => `${metricLabel('roas', 'ROAS')} since move`, title: 'Return on ad spend (ROAS): revenue divided by ad spend', num: true, sort: (r) => r.scale.roas, cell: (r) => roasX(r.scale.roas) },
    { key: 'cpa', label: () => `${metricLabel('cpa', 'CPA')} since move`, title: 'Cost per acquisition (CPA): ad spend divided by purchases', num: true, sort: (r) => r.scale.cpa, cell: (r) => money(r.scale.cpa) },
    { key: 'l7roas', label: () => `Last 7 days ${metricLabel('roas', 'ROAS')}`, title: 'Return on ad spend (ROAS): revenue divided by ad spend', num: true, sort: (r) => r.last7.roas, cell: (r) => roasX(r.last7.roas) },
    { key: 'l7cpa', label: () => `Last 7 days ${metricLabel('cpa', 'CPA')}`, title: 'Cost per acquisition (CPA): ad spend divided by purchases', num: true, sort: (r) => r.last7.cpa, cell: (r) => money(r.last7.cpa) },
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
    { key: 'cpa', label: () => `${metricLabel('cpa', 'CPA')} since move`, title: 'Cost per acquisition (CPA): ad spend divided by purchases', num: true, sort: (t) => t.scale.cpa, total: (t) => money(t.scale.cpa), cell: (t) => money(t.scale.cpa) },
    { key: 'l7spend', label: 'Last 7 days spend', num: true, sort: (t) => t.last7.spend, total: (t) => money(t.last7.spend), cell: (t) => money(t.last7.spend) },
    { key: 'l7roas', label: () => `Last 7 days ${metricLabel('roas', 'ROAS')}`, title: 'Return on ad spend (ROAS): revenue divided by ad spend', num: true, sort: (t) => t.last7.roas, total: (t) => roasX(t.last7.roas), cell: (t) => roasX(t.last7.roas) },
  ];
}

function renderScaling(ctx, d, p) {
  const G = globalParams(p);
  const f = d.filters;
  const controls = h('div', { class: 'ctl-row pagefilters tts-filters sv-controls', 'data-testid': 'sv-controls' }, productFilter({ products: d.products, p, view: 'in_scaling', keep: { ...(p.hide_bulk ? { hide_bulk: p.hide_bulk } : {}), ...(p.bulk_min ? { bulk_min: p.bulk_min } : {}) }, prefix: 'sv', titleOf: PTITLE }),
    bulkCheck('sv-hide-bulk', f));
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
  document.title = VIEW_TITLE[view] + ' · Test to scale · Lonvera';
  const d = await load(view, p, signal);
  if (!ctx.alive()) return;
  setRangeInfo(p, d.window_info, null, { unclassified: false });
  const inner = view === 'in_scaling' ? renderScaling(ctx, d, p) : view === 'should_kill' ? renderKill(ctx, d, p) : renderShould(ctx, d, p);
  ctx.mount(h('div', { class: 'page sv-page', 'data-level': 'promotions', 'data-view': view },
    ...pageTop(view, p),
    ...inner,
    h('div', { class: 'muted small sv-foot', 'data-testid': 'sv-built' }, d.stage_moves ? `Precomputed nightly: rebuilt ${fmt.dateTime(d.stage_moves.built_at)}, through ${d.stage_moves.data_through ? fmt.day(d.stage_moves.data_through) : '–'}. Spend, clicks and the move itself are Meta's; scaling numbers run to ${fmt.day(d.data_through)}, the latest day of data.` : '')));
}
