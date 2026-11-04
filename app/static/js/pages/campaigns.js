// Marketing metrics, platform Meta, Campaigns view: every Meta campaign across all products in one table (one row per campaign).
// The same numbers as the product Campaigns view, summed over products; a Product column and a Product filter say which product's ads a row counts.
import { h, enc, fmt, href, apiWindow, sparkParams, globalParams, stageBadge, liveDot, onCmpRefresh } from '../util.js';
import { api } from '../api.js';
import { createTable } from '../table.js';
import { metricColumns, fixTotal, FLAT_KEYS, trendColumn } from '../columns.js';
import { setRangeInfo } from '../controls.js';
import { breadcrumbs, columnsChips, filterBar, saveFilterToUrl, makeFilter, saveSortToUrl, sortFromUrl, viewSwitch } from '../components.js';
import { createTrendPanel } from '../trend.js';

const NONE = 'none';

// the product whose ads view the row opens: the single product, else the one that spends most in the campaign
function openSlug(r) {
  if (r.product_slug) return r.product_slug;
  const p = (r.products || []).find((x) => x.slug && x.slug !== NONE);
  return p ? p.slug : null;
}
const adsHref = (G, r) => { const s = openSlug(r); return s ? href('/p/' + enc(s), { ...G, view: 'ads', campaign_id: r.campaign_id }) : null; };

function productCell(r, G) {
  if (r.product !== 'Mixed') {
    const slug = r.product_slug;
    return slug && slug !== NONE ? h('a', { href: href('/p/' + enc(slug), G), class: 'plain' }, r.product) : h('span', null, r.product);
  }
  const split = (r.products || []).map((x) => `${x.name} ${fmt.pct(x.share)}`).join(' · ');
  return h('div', { class: 'cell-label-text' }, h('div', { class: 'nm' }, h('span', { class: 'badge muted-badge', title: 'This campaign runs ads of several products: ' + split }, 'Mixed')),
    h('div', { class: 'sub', title: split }, (r.products || []).map((x, i) => [i ? ' · ' : null,
      x.slug && x.slug !== NONE ? h('a', { href: href('/p/' + enc(x.slug), { ...G, view: 'ads', campaign_id: r.campaign_id }), class: 'plain', title: `${x.name}'s ads in this campaign` }, x.name) : x.name,
      ` ${fmt.pct(x.share)}`])));
}

export async function campaignsPage(ctx) {
  const { params: p, signal } = ctx;
  const G = globalParams(p);
  const product = p.product || null;
  const wp = apiWindow(p);
  document.title = 'Campaigns · Marketing metrics';
  const d = await api('/api/campaigns', { ...wp, ...sparkParams(p), ...(product ? { product } : {}) }, { signal });
  if (!ctx.alive()) return;
  setRangeInfo(p, d.window_info, d.compare_info || null, { unclassified: false });

  const flat = metricColumns({ getM: (r) => r, getTotalM: (t) => fixTotal(t), keys: FLAT_KEYS });
  const cols = [
    { key: 'name', label: 'Campaign', sticky: true, cls: 'labelcol', sort: (r) => r.name, total: () => 'Total',
      cell: (r) => { const to = adsHref(G, r); return to ? h('a', { href: to, class: 'rowlink', title: r.name }, r.name) : h('span', { title: r.name }, r.name); } },
    { key: 'chart', label: '', nosort: true, header: h('span', { class: 'muted small' }, 'Chart'), total: () => '',
      cell: (r) => h('a', { href: href('/campaigns', { ...G, product, chart: r.campaign_id }), class: 'btnlink small', 'data-testid': 'chart-row', title: 'Show only this campaign in the chart' }, 'Chart') },
    trendColumn(),
    { key: 'product', label: 'Product', sort: (r) => r.product, cell: (r) => productCell(r, G), total: () => '' },
    { key: 'account', label: 'Account', sort: (r) => r.account, cell: (r) => fmt.text(r.account) },
    { key: 'stage', label: 'Stage', sort: (r) => r.stage, cell: (r) => stageBadge(r.stage) },
    { key: 'ads', label: 'Ads', num: true, sort: (r) => r.ads, cell: (r) => fmt.int(r.ads), total: (t) => (t && t.ads != null ? fmt.int(t.ads) : '') },
    { key: 'families', label: 'Creatives', num: true, sort: (r) => r.families, cell: (r) => fmt.int(r.families), total: () => '' },
    ...flat,
    { key: 'live', label: 'Live', sort: (r) => (r.live ? 1 : 0), cell: (r) => liveDot(r.live), total: () => '', cls: 'center' },
    { key: 'last_day', label: 'Last day', sort: (r) => r.last_day, cell: (r) => fmt.text(r.last_day), total: () => '' },
  ];
  let filterUi;
  const t = createTable({
    columns: cols, rows: d.rows, total: d.total, ...sortFromUrl(p), onSort: saveSortToUrl,
    rowKey: (r) => r.campaign_id,
    rowHref: (r) => adsHref(G, r),
    emptyText: product ? 'No campaigns of this product in this window.' : 'No campaigns in this window.',
    testid: 'platform-campaigns',
    onCount: (n, tot) => filterUi && filterUi.setCount(n, tot),
  });
  onCmpRefresh(() => t.refresh());
  const apply = (st) => t.setFilter(makeFilter(st, (r) => r.spend, (r) => [r.name, r.account, r.stage, r.campaign_id, r.product]));
  filterUi = filterBar({ spend: p.spend === '1', q: p.q, placeholder: 'Search campaign…', onChange: (st) => { saveFilterToUrl(st); apply(st); } });
  if (p.spend === '1' || p.q) apply({ spend: p.spend === '1', q: p.q });
  else filterUi.setCount(d.rows.length, d.rows.length);

  // Product filter: All, every product that spent in the window (the chosen one always), and 'No product' when some families belong to none
  const chips = (d.products || []).filter((x) => x.spend > 0 || x.slug === product);
  const chip = (label, slug, n) => h('a', { class: 'seg', href: href('/campaigns', { ...G, product: slug }), 'data-product': slug || 'all', 'aria-pressed': String((product || null) === slug), title: n != null ? `${fmt.int(n)} campaigns` : null }, label);
  const chartId = p.chart || null;
  const chartRow = chartId ? d.rows.find((r) => r.campaign_id === chartId) : null;
  const panel = createTrendPanel({ p, mode: 'source', signal, source: {
    splits: [['none', 'None'], ['stage', 'Stage'], ['product', 'Product']],
    fetch: ({ grain, split, signal: sig }) => api('/api/campaigns/trend', { ...wp, grain, ...(split !== 'none' ? { split } : {}), ...(product ? { product } : {}), ...(chartId ? { campaign_id: chartId } : {}) }, { signal: sig }),
  } });
  const productCtl = h('div', { class: 'ctl', id: 'product-ctl' }, h('span', { class: 'ctl-name' }, 'Product'),
    h('span', { class: 'segs', role: 'group', 'aria-label': 'Product', 'data-testid': 'product-filter' }, chip('All', null, null), chips.map((x) => chip(x.name, x.slug, x.campaigns))));

  ctx.mount(h('div', { class: 'page', 'data-level': 'platform-campaigns' },
    h('div', { class: 'pagehead' }, h('div', { class: 'titleline' }, h('h1', 'Campaigns'), h('span', { class: 'badge muted-badge' }, 'Meta · all products'), h('span', { class: 'spacer' }), viewSwitch('campaigns', G, 'meta')),
      h('div', { class: 'note-line' }, 'One row per Meta campaign. A campaign that runs ads of several products reads Mixed, with each product’s share; the Product filter keeps one product’s ads.')),
    h('div', { class: 'crumbrow' }, breadcrumbs([{ label: 'Marketing metrics', href: href('/', G) }, { label: 'Campaigns' }])),
    h('div', { class: 'ctl-row' }, productCtl),
    chartId ? h('div', { class: 'note-line', id: 'chart-scope', 'data-testid': 'chart-scope' }, 'Chart shows only ', h('strong', null, chartRow ? chartRow.name : chartId), ' · ', h('a', { href: href('/campaigns', { ...G, product }), class: 'btnlink small' }, 'Show all campaigns')) : null,
    panel.el,
    h('div', { class: 'tablehead' }, h('h2', { id: 'level-title' }, product && d.product ? `Campaigns · ${d.product.name}` : 'Campaigns'), columnsChips(), filterUi.el),
    t.el,
  ));
  panel.load();
}
