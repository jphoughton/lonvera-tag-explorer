// Tag Explorer product page: performance by our own ad learning tags (hook type, device, offer type), one small table per cut, sorted by spend.
// Data: GET /api/products/<slug>/learning-tags (same window, stage, source and cluster / ICP / pain point scope as the tag tree). Hidden until the learning table holds rows.
// Everything from the API goes in through textContent / h().
import { h, enc, fmt, isNum, apiWindow } from '../util.js';
import { api } from '../api.js';

const rate = (v) => (isNum(v) ? (v * 100).toFixed(2) + '%' : '–');
const cash2 = (v) => (isNum(v) ? fmt.money2(v) : '–');

// [header, tooltip, cell(row, side)]: side = the block the Numbers switch picks (nc = Upstack new customers, meta = Meta's own reporting)
function columns(useNc) {
  const pre = useNc ? 'NC ' : '';
  const side = (r) => (useNc ? r.nc : r.meta) || {};
  return [
    ['Ad families', 'Unique ad families carrying this tag value, counted once each', (r) => fmt.int(r.unique_ads)],
    ['Spend', 'Meta spend in the window', (r) => fmt.money(r.spend)],
    ['% of spend', 'Share of the product’s spend in the window', (r) => fmt.share(r.spend_share)],
    ['NC ROAS', 'Upstack new customer revenue divided by spend (return on ad spend)', (r) => fmt.roas(r.nc && r.nc.roas), 'nc-roas'],
    ['Meta ROAS', 'Meta’s own reported revenue divided by spend', (r) => fmt.roas(r.meta && r.meta.roas), 'meta-roas'],
    [pre + 'CPA', useNc ? 'Spend divided by new customers (Upstack)' : 'Spend divided by purchases (Meta)', (r) => cash2(side(r).cpa)],
    ['Cost per link click', 'Spend divided by link clicks', (r) => cash2((r.meta || r.nc || {}).cplc)],
    ['CTR', 'Link clicks divided by impressions', (r) => rate((r.meta || r.nc || {}).ctr)],
    ['CPM', 'Cost per 1,000 impressions', (r) => cash2((r.meta || r.nc || {}).cpm)],
    [pre + 'Conversion rate', (useNc ? 'New customers' : 'Purchases') + ' divided by link clicks', (r) => rate(side(r).conv_rate)],
    [pre + 'AOV', 'Average order value: revenue divided by ' + (useNc ? 'new customers' : 'purchases'), (r) => cash2(side(r).aov)],
  ];
}

function cutCard(cut, cols, hasNc) {
  const head = h('tr', null, h('th', { scope: 'col' }, cut.title), cols.map(([t, tip]) => h('th', { scope: 'col', class: 'num', title: tip }, t)));
  const body = cut.rows.map((r) => h('tr', { 'data-value': r.value, 'data-testid': 'own-cut-row' },
    h('th', { scope: 'row', class: 'own-val' }, r.value), cols.map(([, , cell, cls]) => h('td', { class: 'num' + (cls ? ' ' + cls : '') }, cell(r)))));
  return h('article', { class: 'own-cut', 'data-testid': 'own-cut', 'data-key': cut.key },
    h('h3', null, cut.title), h('p', { class: 'pri-sub own-cut-note', 'data-testid': 'own-cut-note' }, cut.note + (hasNc ? '' : '. Upstack numbers are not loaded, so only Meta’s own numbers show.')),
    h('p', { class: 'pri-sub own-hint' }, 'Swipe the table sideways for the other columns.'),
    h('div', { class: 'own-scroll' }, h('table', { class: 'mini own-table' }, h('thead', null, head), h('tbody', null, body))));
}

/** The section element: an empty placeholder that fills itself from the API and removes itself when there is nothing to show or the call fails. */
export function learningSection(slug, p, scope, signal) {
  const host = h('section', { class: 'pgsec own-learn', id: 'sec-learning', 'data-testid': 'own-learn', hidden: true });
  const useNc = p.source === 'upstack';
  api(`/api/products/${enc(slug)}/learning-tags`, { ...scope, ...apiWindow(p) }, { signal }).then((d) => {
    if (!d || !d.available || !(d.cuts || []).length) return;
    const cols = columns(useNc && d.has_nc);
    host.append(h('div', { class: 'tablehead' }, h('h2', null, 'By hook type, device and offer type')),
      h('p', { class: 'pri-sub', 'data-testid': 'own-learn-note' }, `${d.window_info && d.window_info.label ? d.window_info.label + '. ' : ''}${d.note}`),
      h('div', { class: 'own-cuts' }, d.cuts.map((c) => cutCard(c, cols, d.has_nc))));
    host.hidden = false;
  }).catch(() => { /* an optional section: stay hidden */ });
  return host;
}
