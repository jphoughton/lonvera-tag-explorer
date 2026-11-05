// Marketing metrics, platform All, Campaigns view: every Meta and Google campaign in one table. Each row says which platform it is, its product (Meta) or campaign
// type (Google) and the basis it is counted on. Spend, impressions and clicks are totalled; purchases and revenue are not added across platforms (both platforms credit
// the same orders), so the platform subtotals carry them. The chart above is the platform trend of the same filters.
import { h, enc, fmt, href, apiWindow, sparkParams, globalParams, stageBadge, liveDot, onCmpRefresh } from '../util.js';
import { api } from '../api.js';
import { createTable } from '../table.js';
import { metricColumns, fixTotal, FLAT_KEYS, trendColumn } from '../columns.js';
import { setRangeInfo, setPendingNote } from '../controls.js';
import { createTrendPanel } from '../trend.js';
import { breadcrumbs, columnsChips, filterBar, saveFilterToUrl, makeFilter, saveSortToUrl, sortFromUrl, viewSwitch } from '../components.js';
import { platformTrendSource } from './platforms.js';
import { totalsStrip } from '../kpis.js';

const NONE = 'none';
const NEUTRAL = { purchases: 'Purchases / orders', revenue: 'Revenue', roas: 'ROAS', cpa: 'Cost per purchase', link_clicks: 'Clicks' };
const NEUTRAL_TITLE = {
  purchases: 'What the row counts, see its Numbers column: purchases as the platform attributes them, or Upstack new customers / orders',
  revenue: 'Conversion value or order totals of what the row counts', roas: 'Revenue / spend', cpa: 'Spend / purchases (or new customers, or orders)', link_clicks: 'Meta link clicks; Google all clicks',
};

const metaAdsHref = (G, r) => {
  const slug = r.product_slug || ((r.products || []).find((x) => x.slug && x.slug !== NONE) || {}).slug;
  return slug ? href('/p/' + enc(slug), { ...G, view: 'ads', campaign_id: r.campaign_id }) : null;
};

function groupCell(r, G) {
  if (r.platform === 'google') return h('span', { class: 'badge muted-badge', title: 'Campaign type, read from the campaign name' }, r.group);
  if (r.group !== 'Mixed') return r.product_slug && r.product_slug !== NONE ? h('a', { href: href('/p/' + enc(r.product_slug), G), class: 'plain' }, r.group) : h('span', null, r.group);
  const split = (r.products || []).map((x) => `${x.name} ${fmt.pct(x.share)}`).join(' · ');
  return h('div', { class: 'cell-label-text' }, h('div', { class: 'nm' }, h('span', { class: 'badge muted-badge', title: 'This campaign runs ads of several products: ' + split }, 'Mixed')), h('div', { class: 'sub', title: split }, split));
}

export async function allCampaignsPage(ctx) {
  const { params: p, signal } = ctx;
  const G = { ...globalParams(p), platform: 'all' };          // platform=all is explicit on /campaigns (its default there is Meta)
  const only = p.only === 'meta' || p.only === 'google' ? p.only : null;
  const product = p.product || null;
  const ctype = p.type || null;
  const wp = apiWindow(p);
  document.title = 'All campaigns · Marketing metrics';
  const filt = { ...(only ? { only } : {}), ...(product ? { product } : {}), ...(ctype ? { type: ctype } : {}) };
  const d = await api('/api/campaigns/all', { ...wp, ...sparkParams(p), ...filt }, { signal });
  if (!ctx.alive()) return;
  if (p.compare || p.cfrom) setPendingNote('Comparison shows in the totals strip only; the chart, the table and the Stage / Active only filters do not apply to All platforms.');
  setRangeInfo(p, d.window_info, null, { unclassified: false });

  const flat = metricColumns({ getM: (r) => r.m, getTotalM: (t) => fixTotal(t && t.m), keys: FLAT_KEYS }).map((c) => {
    const o = { ...c };
    if (NEUTRAL[c.key]) o.label = () => NEUTRAL[c.key];
    if (NEUTRAL_TITLE[c.key]) o.title = NEUTRAL_TITLE[c.key];
    return o;
  });
  const cols = [
    { key: 'name', label: 'Campaign', sticky: true, cls: 'labelcol', sort: (r) => r.name, total: () => 'Total',
      cell: (r) => { const to = r.platform === 'meta' ? metaAdsHref(G, r) : null; return to ? h('a', { href: to, class: 'rowlink', title: r.name }, r.name) : h('span', { title: r.name }, r.name); } },
    trendColumn(),
    { key: 'platform', label: 'Platform', sort: (r) => r.platform_name, cell: (r) => h('span', { class: 'platform-tag platform-' + r.platform }, r.platform_name), total: () => '' },
    { key: 'group', label: 'Product / type', sort: (r) => r.group, cell: (r) => groupCell(r, G), total: () => '' },
    { key: 'basis', label: 'Numbers', sort: (r) => r.basis, cell: (r) => h('span', { class: 'small', 'data-testid': 'row-basis' }, r.basis), total: () => '',
      title: 'The basis this row is counted on: its own platform, or Upstack' },
    { key: 'account', label: 'Account', sort: (r) => r.account || '', cell: (r) => fmt.text(r.account), total: () => '' },
    { key: 'stage', label: 'Stage', sort: (r) => r.stage || '', cell: (r) => (r.stage ? stageBadge(r.stage) : h('span', { class: 'muted' }, '–')), total: () => '' },
    { key: 'ads', label: 'Ads', num: true, sort: (r) => r.ads ?? -1, cell: (r) => (r.ads == null ? '–' : fmt.int(r.ads)), total: (t) => (t && t.ads != null ? fmt.int(t.ads) : '') },
    ...flat,
    { key: 'live', label: 'Live', sort: (r) => (r.live ? 1 : 0), cell: (r) => (r.live == null ? '–' : liveDot(r.live)), total: () => '', cls: 'center' },
    { key: 'last_day', label: 'Last day', sort: (r) => r.last_day || '', cell: (r) => fmt.text(r.last_day), total: () => '' },
  ];
  // totals: one platform in view = its full totals; two platforms = spend, impressions and clicks only, with a subtotal row per platform
  const sub = d.subtotals || {};
  const keys = ['meta', 'google'].filter((k) => sub[k]);
  const additive = !!(d.total && d.total.additive) && keys.length === 1;
  const totalM = additive ? { ...sub[keys[0]].m, spend_share: 1 } : { spend: d.total.spend, impressions: d.total.impressions, link_clicks: d.total.link_clicks, ctr: d.total.ctr, cpc: d.total.cpc, spend_share: d.total.spend ? 1 : null };
  const total = { m: totalM, ads: additive ? sub[keys[0]].ads : null };
  const extraTotals = additive ? [] : keys.map((k) => ({
    key: 'sub-' + k, cls: 'extra subtotal', total: { m: sub[k].m, ads: sub[k].ads },
    label: h('span', null, `${k === 'meta' ? 'Meta' : 'Google'} subtotal`, h('span', { class: 'muted small' }, ` · ${(d.basis && d.basis[k]) || ''}`)),
  }));
  let filterUi;
  const t = createTable({
    columns: cols, rows: d.rows, total, extraTotals, totalLabel: additive ? 'Total' : 'Total (spend, impressions, clicks)', ...sortFromUrl(p), onSort: saveSortToUrl,
    rowKey: (r) => r.key, rowHref: (r) => (r.platform === 'meta' ? metaAdsHref(G, r) : null),
    emptyText: 'No campaigns match these filters in this window.', testid: 'all-campaigns',
    onCount: (n, tot) => filterUi && filterUi.setCount(n, tot),
  });
  onCmpRefresh(() => t.refresh());
  const apply = (st) => t.setFilter(makeFilter(st, (r) => r.m.spend, (r) => [r.name, r.account, r.stage, r.group, r.platform_name, r.campaign_id]));
  filterUi = filterBar({ spend: p.spend === '1', q: p.q, placeholder: 'Search campaign…', onChange: (st) => { saveFilterToUrl(st); apply(st); } });
  if (p.spend === '1' || p.q) apply({ spend: p.spend === '1', q: p.q });
  else filterUi.setCount(d.rows.length, d.rows.length);

  // filters: Platform, Meta product, Google campaign type (picking one clears the others, they do not combine)
  const link = (label, extra, pressed, tid, title) => h('a', { class: 'seg', href: href('/campaigns', { ...G, ...extra }), 'data-testid': tid, 'aria-pressed': String(pressed), title: title || null }, label);
  const none = { only: null, product: null, type: null };
  const platformCtl = h('div', { class: 'ctl', id: 'only-ctl' }, h('span', { class: 'ctl-name' }, 'Platform'),
    h('span', { class: 'segs', role: 'group', 'aria-label': 'Platform filter', 'data-testid': 'platform-filter' },
      link('All', none, !only && !product && !ctype, 'only-all'),
      (d.platforms || []).map((x) => link(x.name, { ...none, only: x.key }, (only === x.key && !product && !ctype) || (x.key === 'meta' && !!product) || (x.key === 'google' && !!ctype), 'only-' + x.key, `${x.name}: $${Math.round(x.spend).toLocaleString('en-US')}`))));
  const prodChips = (d.products || []).filter((x) => x.spend > 0 || x.slug === product);
  const productCtl = prodChips.length ? h('div', { class: 'ctl', id: 'product-ctl' }, h('span', { class: 'ctl-name' }, 'Product'),
    h('span', { class: 'segs', role: 'group', 'aria-label': 'Product (Meta)', 'data-testid': 'product-filter' },
      prodChips.map((x) => link(x.name, { ...none, product: x.slug }, product === x.slug, 'product-' + x.slug, `${fmt.int(x.campaigns)} Meta campaigns`)))) : null;
  const typeChips = (d.types || []).filter((x) => x.spend > 0 || x.key === ctype);
  const typeCtl = typeChips.length ? h('div', { class: 'ctl', id: 'type-ctl' }, h('span', { class: 'ctl-name' }, 'Campaign type'),
    h('span', { class: 'segs', role: 'group', 'aria-label': 'Campaign type (Google)', 'data-testid': 'type-filter' },
      typeChips.map((x) => link(x.name, { ...none, type: x.key }, ctype === x.key, 'type-' + x.key)))) : null;

  const panel = createTrendPanel({ p, mode: 'source', signal, source: platformTrendSource(p, wp, filt) });
  const strip = totalsStrip({ p, signal, filt });
  const winLines = Object.entries(d.platform_windows || {}).map(([k, w]) => `${k === 'meta' ? 'Meta' : 'Google'}: ${w.from === w.to ? fmt.day(w.from) : `${fmt.day(w.from)} – ${fmt.day(w.to)}`}`);
  const differ = new Set(Object.values(d.platform_windows || {}).map((w) => `${w.from}|${w.to}`)).size > 1;
  ctx.mount(h('div', { class: 'page', 'data-level': 'all-campaigns' },
    h('div', { class: 'pagehead' }, h('div', { class: 'titleline' }, h('h1', 'All campaigns'), h('span', { class: 'badge muted-badge' }, 'Meta and Google'), h('span', { class: 'spacer' }), viewSwitch('campaigns', G, 'all')),
      h('div', { class: 'note-line' }, 'Every Meta and Google campaign in one table. Each row is counted on the basis in its Numbers column.')),
    h('div', { class: 'crumbrow' }, breadcrumbs([{ label: 'Marketing metrics', href: href('/', { ...G, platform: null }) }, { label: 'All campaigns' }])),
    h('div', { class: 'ctl-row' }, platformCtl, productCtl, typeCtl),
    strip.el,
    panel.el,
    h('div', { class: 'tablehead' }, h('h2', { id: 'level-title' }, 'Campaigns'), columnsChips(), filterUi.el),
    t.el,
    h('div', { class: 'muted small total-note', 'data-testid': 'all-campaigns-note' },
      additive ? `One platform in view: the Total row is that platform's total.`
        : 'Not added up: the Total row adds spend, impressions and clicks only. Both platforms credit the same orders (and under Upstack an order can be credited to a Meta ad and a Google campaign), so purchases, revenue, ROAS and cost per purchase are given per platform in the subtotal rows.'),
    differ ? h('div', { class: 'muted small', 'data-testid': 'all-campaigns-windows' }, 'Days covered: ' + winLines.join(' · ') + '.') : null,
    ...(d.notes || []).map((n) => h('div', { class: 'muted small', 'data-testid': 'platform-note' }, n)),
  ));
  strip.load();
  panel.load();
}
