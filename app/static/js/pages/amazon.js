// Lonvera, platform Amazon: sales, advertising and organic search of the Amazon account, from Amazon through ExpandFi (see app/amazon.py, docs/MARKETING_METRICS_AMAZON.md).
// One page, eight views chosen with the View switch (?view=): Overview, Campaigns, Products, Keywords, Ad search terms, Negative keywords (the last three are Sponsored Products keyword data loaded by hand), Search terms, Fees. Amazon is its own channel: nothing here is added to the
// All platforms totals, and there is no Numbers from switch (only Amazon's own reporting exists). The range control of the bar above applies to every view.
import { h, fmt, href, isNum, navigate, currentParams, qs, cleanParams, debounce } from '../util.js';
import { api } from '../api.js';
import { createTable } from '../table.js';
import { setRangeInfo } from '../controls.js';
import { breadcrumbs } from '../components.js';
import { trendChart, BAR_COLOR, LINE_COLOR } from '../charts.js';
import { pageHeader } from '../ui/pagehead.js';
import { filterRow } from '../ui/pagefilters.js';

const VIEWS = [['overview', 'Overview'], ['campaigns', 'Campaigns'], ['products', 'Products'], ['keywords', 'Keywords'], ['adsearch', 'Ad search terms'], ['negatives', 'Negative keywords'], ['search', 'Search terms'], ['fees', 'Fees']];
const AD_TYPES = [['sp', 'Sponsored Products'], ['sb', 'Sponsored Brands'], ['sbv', 'Sponsored Brands video'], ['sd', 'Sponsored Display']];
const PRODUCTS = [['vita-bull', 'Vita Bull'], ['beef-liver', 'Beef Liver'], ['boneflex', 'Boneflex'], ['vita-belle', 'Vital Belle']];
const PRODUCT_COLORS = { all: '#1f3a5f', 'vita-bull': '#5a4fd6', 'beef-liver': '#d9822b', boneflex: '#1d8a56', 'vita-belle': '#c2410c' };
const NO_SWITCH = 'These pages show Amazon’s own reporting only, so there is no Numbers from switch. Amazon is kept apart from Meta and Google and is never added to the All platforms totals.';

const money2 = (v) => fmt.money2(v);
const times = (v) => (isNum(v) ? fmt.roas(v) + 'x' : '–');
const pct1 = (v) => (isNum(v) ? v.toFixed(1) + '%' : '–');                 // shares from the API are already percent (0 to 100)
const pct2 = (v) => (isNum(v) ? v.toFixed(2) + '%' : '–');
const ppCell = (v) => (isNum(v) ? fmt.pp(v, 2) : '');
const pctChange = (v) => (isNum(v) ? fmt.pctChange(v) : '');

const winParams = (p) => (p.from && p.to ? { from: p.from, to: p.to } : p.window && p.window !== 'launch' ? { window: p.window } : {});
const view = (p) => (VIEWS.some(([k]) => k === p.view) ? p.view : 'overview');
const amzHref = (p, extra) => href('/amazon', { ...winParams(p), ...extra });

// ---------------------------------------------------------------------------------------------------------------- shared pieces
function col(key, label, get, cell, o = {}) {
  return { key, label, num: o.num !== false, sort: get, cell: (r) => cell(get(r), r), total: o.total ? (t) => cell((o.total === true ? get : o.total)(t), t) : undefined, title: o.title, sticky: o.sticky, cls: o.cls };
}

function emptyState(testid = 'amz-empty') {
  return h('div', { class: 'empty-range', role: 'status', 'data-testid': testid },
    h('strong', 'Amazon data has not loaded yet.'),
    h('span', ' The Amazon tables fill every night from ExpandFi, and the first load also needs the one-time backfill. Check back after the next nightly run.'));
}

function viewSwitch(p) {
  const cur = view(p);
  return h('div', { class: 'ctl', id: 'amz-view-switch', 'data-testid': 'amz-views' }, h('span', { class: 'ctl-name' }, 'View'),
    h('span', { class: 'segs', role: 'group', 'aria-label': 'Amazon view' },
      VIEWS.map(([k, label]) => h('a', { class: 'seg', href: amzHref(p, { view: k === 'overview' ? null : k }), 'data-amz-view': k, 'aria-pressed': String(cur === k), 'aria-current': cur === k ? 'page' : null }, label))));
}

function chips(name, options, value, hrefFor, testid) {
  return h('div', { class: 'ctl', 'data-testid': testid },
    h('span', { class: 'ctl-name' }, name),
    h('span', { class: 'segs', role: 'group', 'aria-label': name },
      options.map(([k, label]) => h('a', { class: 'seg', href: hrefFor(k), 'data-value': k, 'aria-pressed': String(String(value || 'all') === k) }, label))));
}

function tile(id, label, value, sub, title) {
  return h('div', { class: 'kpi', 'data-testid': 'amz-kpi-' + id },
    h('div', { class: 'kpi-label', title: title || null }, label),
    h('div', { class: 'kpi-value', 'data-testid': 'amz-kpi-' + id + '-value' }, value),
    sub ? h('div', { class: 'kpi-sub' }, sub) : null);
}

function noteLine(text, testid) {
  return h('div', { class: 'note-line az-note', 'data-testid': testid || null }, text);
}

function csvButton(filename, headers, rowsOf) {
  const b = h('button', { type: 'button', class: 'btn small', 'data-testid': 'amz-csv', title: 'Download the rows now shown as a CSV file' }, 'Download CSV');
  b.addEventListener('click', async () => {
    const esc = (v) => { const s = v == null ? '' : String(v); return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s; };
    const text = [headers, ...(await rowsOf())].map((r) => r.map(esc).join(',')).join('\n') + '\n';        // rowsOf may be async (the negatives file is fetched whole)
    const a = h('a', { href: URL.createObjectURL(new Blob([text], { type: 'text/csv' })), download: filename });
    document.body.append(a);
    a.click();
    a.remove();
  });
  return b;
}

function rangeLabel(info) {
  return info && info.label ? info.label : '';
}

const sourceLine = (d) => `Source: Amazon through ExpandFi (orders, traffic, advertising, Brand Analytics and settlements)${d && d.window_info ? `, data through ${fmt.day(d.window_info.data_through)}` : ''}.`;
function setFooter(d) {
  const el = document.getElementById('source');
  if (el) { el.hidden = false; el.textContent = sourceLine(d); }
}

// ---------------------------------------------------------------------------------------------------------------- overview
const BARS = [['revenue', 'Revenue', 'money'], ['nc_revenue', 'New-customer revenue', 'money'], ['orders', 'Orders', 'int'], ['sessions', 'Sessions', 'int']];
const LINES = [['ad_spend', 'Ad spend', 'money'], ['ad_return', 'Ad return', 'roas'], ['ad_share_of_revenue', 'Ad spend share of revenue', 'rate'], ['ad_sales', 'Ad-reported sales', 'money']];

function trendCard(p, signal) {
  const st = { bars: 'revenue', line: 'ad_spend', grain: 'day' };
  const canvas = h('canvas', { id: 'amz-trend-chart', 'aria-label': 'Amazon trend' });
  const note = h('div', { class: 'trend-note' });
  let chart = null, seq = 0;
  const sel = (id, opts, key) => {
    const el = h('select', { id, 'aria-label': id }, opts.map(([k, l]) => h('option', { value: k }, l)));
    el.value = st[key];
    el.addEventListener('change', () => { st[key] = el.value; draw(); });
    return el;
  };
  const barSel = sel('amz-bars', BARS, 'bars'), lineSel = sel('amz-line', LINES, 'line');
  const grains = [['day', 'Day'], ['week', 'Week'], ['month', 'Month']].map(([g, l]) => {
    const b = h('button', { type: 'button', class: 'seg', 'aria-pressed': String(g === st.grain), 'data-grain': g }, l);
    b.addEventListener('click', () => { st.grain = g; grains.forEach((x) => x.setAttribute('aria-pressed', String(x.dataset.grain === g))); load(); });
    return b;
  });
  let data = null;
  function draw() {
    if (!data || !canvas.isConnected) return;
    const pts = data.points;
    const b = BARS.find((x) => x[0] === st.bars), l = LINES.find((x) => x[0] === st.line);
    if (chart) chart.destroy();
    chart = trendChart(canvas, { labels: pts.map((x) => x.t), grain: st.grain, partial: pts.map((x) => !!x.partial),
      datasets: [{ axis: 'left', type: 'bar', kind: b[2], label: b[1], data: pts.map((x) => (isNum(x[b[0]]) ? x[b[0]] : null)), color: BAR_COLOR },
        { axis: 'right', type: 'line', kind: l[2], label: l[1], data: pts.map((x) => (isNum(x[l[0]]) ? x[l[0]] : null)), color: LINE_COLOR, width: 2.4, partialDash: true }],
      left: { kind: b[2], title: `${b[1]} (bars, left)` }, right: { kind: l[2], title: `${l[1]} (line, right)` } });
  }
  async function load() {
    const my = ++seq;
    let d;
    try { d = await api('/api/amazon/trend', { ...winParams(p), grain: st.grain }, { signal }); } catch (e) { if (e && e.name === 'AbortError') return; note.textContent = 'Trend unavailable: ' + e.message; return; }
    if (my !== seq) return;
    if (d.loaded === false) { note.textContent = d.message; return; }
    data = d;
    note.textContent = 'A dashed line is a period that is not over yet. Ad figures start when the ads data starts; days before that are blank, not zero.';
    draw();
  }
  const el = h('div', { class: 'card trendcard', 'data-testid': 'amz-trend' },
    h('div', { class: 'card-head' }, h('h3', 'Trend')),
    h('div', { class: 'ctl-row' },
      h('div', { class: 'ctl' }, h('span', { class: 'ctl-name' }, 'Grain'), h('span', { class: 'segs' }, grains)),
      h('details', { class: 'chart-options', 'data-testid': 'chart-options' }, h('summary', { title: 'Choose the bars and the line' }, 'Chart options'),
        h('div', { class: 'chart-options-body' }, h('div', { class: 'ctl' }, h('span', { class: 'ctl-name' }, 'Bars'), barSel), h('div', { class: 'ctl' }, h('span', { class: 'ctl-name' }, 'Line'), lineSel)))),
    h('div', { class: 'chartbox' }, canvas), note);
  return { el, load };
}

function adCols(total = false) {
  return [
    col('spend', 'Ad spend', (r) => r.spend, fmt.money2, { total, title: 'Amazon advertising spend (Ads report), range total' }),
    col('impressions', 'Impressions', (r) => r.impressions, fmt.int, { total }),
    col('clicks', 'Clicks', (r) => r.clicks, fmt.int, { total }),
    col('ctr', 'Click-through rate', (r) => r.ctr, fmt.rate, { total, title: 'Clicks / impressions' }),
    col('cpc', 'Cost per click', (r) => r.cpc, money2, { total, title: 'Spend / clicks' }),
    col('ad_orders', 'Ad-reported orders', (r) => r.ad_orders, fmt.int, { total, title: 'Orders Amazon credits to the ads (Sponsored Products 14-day)' }),
    col('ad_sales', 'Ad-reported sales', (r) => r.ad_sales, fmt.money, { total, title: 'Sales Amazon credits to the ads (Sponsored Products 14-day; Brands and Display as Amazon reports)' }),
    col('ad_return', 'Ad return', (r) => r.ad_return, times, { total, title: 'Ad-reported sales / ad spend' }),
    col('cost_per_order', 'Cost per order', (r) => r.cost_per_order, money2, { total, title: 'Ad spend / ad-reported orders' }),
  ];
}

async function overviewView(ctx, p, mount) {
  const d = await api('/api/amazon/overview', winParams(p), { signal: ctx.signal });
  if (!ctx.alive()) return;
  if (d.loaded === false) { setRangeInfo(p, null, null); mount(emptyState()); return; }
  setRangeInfo(p, d.window_info, null, { unclassified: false });
  setFooter(d);
  const t = d.tiles;
  const tiles = h('section', { class: 'kpis', 'data-testid': 'amz-tiles', 'aria-label': 'Amazon totals' }, h('div', { class: 'kpi-row' },
    tile('revenue', 'Amazon revenue', fmt.money(t.revenue), 'Seller Central sales, Amazon.com and business orders', 'Revenue of orders placed in the range (tax excluded as Amazon reports it)'),
    tile('orders', 'Orders', fmt.int(t.orders), 'Counted per product: an order of two of our products counts for each', 'Orders are counted per ASIN, so a basket with two of our products counts twice'),
    tile('units', 'Units', fmt.int(t.units)),
    tile('nc-revenue', 'New-customer revenue', fmt.money(t.nc_revenue), `${fmt.int(t.nc_orders)} new-customer orders`, 'Revenue of orders on the customer’s first purchase day'),
    tile('ad-spend', 'Ad spend', fmt.money(t.ad_spend), 'Amazon Ads report', 'Sponsored Products, Brands, Brands video and Display'),
    tile('ad-sales', 'Ad-reported sales', fmt.money(t.ad_sales), 'As Amazon credits them to ads', 'Sponsored Products 14-day; Brands and Display as Amazon reports them'),
    tile('ad-return', 'Ad return', times(t.ad_return), 'Ad-reported sales / ad spend', 'Not comparable with new-customer return on ad spend of Meta or Google'),
    tile('ad-share', 'Ad spend share of revenue', isNum(t.ad_share_of_revenue) ? fmt.rate(t.ad_share_of_revenue) : '–', 'Ad spend / Amazon revenue, same days', 'Uses only days the ads data covers'),
    tile('sessions', 'Sessions', fmt.int(t.sessions), 'Visits to our product pages', 'Account-level sessions from Amazon Business Reports'),
    tile('conversion', 'Conversion', isNum(t.conversion) ? fmt.rate(t.conversion) : '–', 'Orders / sessions, same days', 'Orders counted per product divided by sessions'),
  ));
  const trend = trendCard(p, ctx.signal);
  const prodCols = [
    col('label', 'Product', (r) => r.label, (v) => v, { num: false, sticky: true, total: () => 'Total' }),
    col('revenue', 'Revenue', (r) => r.revenue, fmt.money, { total: (r) => r.revenue }),
    col('orders', 'Orders', (r) => r.orders, fmt.int, { total: (r) => r.orders }),
    col('units', 'Units', (r) => r.units, fmt.int, { total: (r) => r.units }),
    col('nc_revenue', 'New-customer revenue', (r) => r.nc_revenue, fmt.money, { total: (r) => r.nc_revenue }),
    col('spend', 'Ad spend', (r) => r.spend, fmt.money, { total: (r) => r.spend }),
    col('ad_sales', 'Ad-reported sales', (r) => r.ad_sales, fmt.money, { total: (r) => r.ad_sales }),
    col('ad_return', 'Ad return', (r) => r.ad_return, times, { total: (r) => r.ad_return }),
    col('ad_share_of_revenue', 'Ad spend share of revenue', (r) => r.ad_share_of_revenue, (v) => (isNum(v) ? fmt.rate(v) : '–'), { total: (r) => r.ad_share_of_revenue }),
  ];
  const tProd = createTable({ columns: prodCols, rows: d.by_product, total: d.by_product_total, rowKey: (r) => r.key, tableClass: 'compact', testid: 'amz-by-product', sortKey: 'revenue', sortDir: 'desc' });
  const typeCols = [
    col('label', 'Ad type', (r) => r.label, (v, r) => h('div', { class: 'cell-label-text' }, h('div', { class: 'nm' }, v), r.attribution ? h('div', { class: 'sub' }, r.attribution) : null), { num: false, sticky: true, total: () => 'Total' }),
    ...adCols(true),
  ];
  const tType = createTable({ columns: typeCols, rows: d.by_ad_type, total: d.by_ad_type_total, rowKey: (r) => r.key, tableClass: 'compact', testid: 'amz-by-type', sortKey: 'spend', sortDir: 'desc' });
  mount(h('div', null,
    tiles, ...d.notes.map((n) => noteLine(n, 'amz-range-note')),
    trend.el,
    h('div', { class: 'tablehead' }, h('h2', { id: 'amz-products-title' }, 'By product'), csvButton('amazon-by-product.csv', ['Product', 'Revenue', 'Orders', 'Units', 'New-customer revenue', 'Ad spend', 'Ad-reported sales', 'Ad return'],
      () => d.by_product.map((r) => [r.label, r.revenue, r.orders, r.units, r.nc_revenue, r.spend, r.ad_sales, r.ad_return]))),
    noteLine('Ad spend that sits in no single product (campaigns for all products, Sponsored Brands) is the "No single product" row.'),
    tProd.el,
    h('div', { class: 'tablehead' }, h('h2', { id: 'amz-types-title' }, 'By ad type')),
    noteLine('Sponsored Products results are 14-day; Sponsored Brands, Brands video and Sponsored Display are as Amazon reports them.'),
    tType.el));
  trend.load();
}

// ---------------------------------------------------------------------------------------------------------------- campaigns
function campaignTrendCard(p, id, name, signal) {
  const st = { grain: 'day' };
  const canvas = h('canvas', { id: 'amz-campaign-chart', 'aria-label': 'Campaign trend' });
  const note = h('div', { class: 'trend-note' });
  let chart = null, seq = 0;
  const grains = [['day', 'Day'], ['week', 'Week']].map(([g, l]) => {
    const b = h('button', { type: 'button', class: 'seg', 'aria-pressed': String(g === st.grain), 'data-grain': g }, l);
    b.addEventListener('click', () => { st.grain = g; grains.forEach((x) => x.setAttribute('aria-pressed', String(x.dataset.grain === g))); load(); });
    return b;
  });
  async function load() {
    const my = ++seq;
    let d;
    try { d = await api('/api/amazon/campaign-trend', { ...winParams(p), campaign_id: id, grain: st.grain }, { signal }); } catch (e) { if (e && e.name === 'AbortError') return; note.textContent = 'Trend unavailable: ' + e.message; return; }
    if (my !== seq || !canvas.isConnected || d.loaded === false) return;
    const pts = d.points;
    if (chart) chart.destroy();
    chart = trendChart(canvas, { labels: pts.map((x) => x.t), grain: st.grain, partial: pts.map((x) => !!x.partial),
      datasets: [{ axis: 'left', type: 'bar', kind: 'money', label: 'Ad spend', data: pts.map((x) => (isNum(x.spend) ? x.spend : null)), color: BAR_COLOR },
        { axis: 'right', type: 'line', kind: 'roas', label: 'Ad return', data: pts.map((x) => (isNum(x.ad_return) ? x.ad_return : null)), color: LINE_COLOR, width: 2.4, partialDash: true }],
      left: { kind: 'money', title: 'Ad spend (bars, left)' }, right: { kind: 'roas', title: 'Ad return (line, right)' } });
    note.textContent = 'Recent days read low while 14-day sales mature.';
  }
  const close = h('a', { class: 'btnlink', href: amzHref(p, { view: 'campaigns', ad_type: p.ad_type, product: p.product, q: p.q }), 'data-testid': 'amz-campaign-close' }, 'Close');
  const el = h('div', { class: 'card trendcard', 'data-testid': 'amz-campaign-trend' },
    h('div', { class: 'card-head' }, h('h3', null, `${name} · daily trend`), close),
    h('div', { class: 'ctl-row' }, h('div', { class: 'ctl' }, h('span', { class: 'ctl-name' }, 'Grain'), h('span', { class: 'segs' }, grains))),
    h('div', { class: 'chartbox' }, canvas), note);
  return { el, load };
}

function searchBox(value, onInput, testid, label) {
  const input = h('input', { type: 'search', class: 'az-search', placeholder: label, 'aria-label': label, value: value || '', 'data-testid': testid, autocomplete: 'off' });
  input.addEventListener('input', debounce(() => onInput(input.value.trim()), 300));
  return h('div', { class: 'ctl' }, h('span', { class: 'ctl-name' }, 'Search'), input);
}

function replaceParam(key, value) {
  navigate(location.pathname + qs(cleanParams({ ...currentParams(), [key]: value || null })), { replace: true, render: false });
}

async function campaignsView(ctx, p, mount) {
  const args = { ...winParams(p), ...(p.ad_type ? { ad_type: p.ad_type } : {}), ...(p.product ? { product: p.product } : {}) };
  const d = await api('/api/amazon/campaigns', args, { signal: ctx.signal });
  if (!ctx.alive()) return;
  if (d.loaded === false) { setRangeInfo(p, null, null); mount(emptyState()); return; }
  setRangeInfo(p, d.window_info, null, { unclassified: false });
  setFooter(d);
  const typeBadge = (r) => h('span', { class: 'badge muted-badge', title: 'Attribution: ' + r.attribution }, r.ad_type_label);
  const cols = [
    { key: 'name', label: 'Campaign', sticky: true, sort: (r) => r.name, total: () => 'Total',
      cell: (r) => h('div', { class: 'cell-label-text' },
        h('div', { class: 'nm' }, h('a', { class: 'rowlink', href: amzHref(p, { view: 'campaigns', ad_type: p.ad_type, product: p.product, campaign: r.campaign_id }), title: r.name }, r.name)),
        h('div', { class: 'sub' }, typeBadge(r), ' ', h('span', { class: 'muted small' }, r.product_label), r.status ? h('span', { class: 'muted small' }, ' · ' + r.status) : null)) },
    col('attribution', 'Attribution', (r) => r.attribution, (v) => v, { num: false, title: 'How Amazon counts sales and orders for this ad type' }),
    ...adCols().map((c) => ({ ...c, sort: (r) => r.m[c.key], cell: (r) => c.cell(r.m), total: (tt) => c.cell(tt.m) })),
  ];
  const t = createTable({ columns: cols, rows: d.rows, total: d.total, rowKey: (r) => r.key, tableClass: 'compact', testid: 'amz-campaigns', sortKey: 'spend', sortDir: 'desc', emptyText: 'No campaigns match.' });
  const filt = (q) => (q ? (r) => (r.name + ' ' + r.campaign_id).toLowerCase().includes(q.toLowerCase()) : null);
  t.setFilter(filt(p.q));
  const box = searchBox(p.q, (q) => { replaceParam('q', q); t.setFilter(filt(q)); }, 'amz-campaign-search', 'Campaign name');
  const sel = p.campaign ? d.rows.find((r) => r.campaign_id === p.campaign) : null;
  const trend = p.campaign ? campaignTrendCard(p, p.campaign, sel ? sel.name : 'Campaign ' + p.campaign, ctx.signal) : null;
  const typeOpts = [['all', 'All'], ...d.facets.ad_types.map((x) => [x.key, x.label])];
  const prodOpts = [['all', 'All'], ...d.facets.products.map((x) => [x.key, x.label])];
  mount(h('div', null,
    noteLine(d.note),
    trend ? trend.el : null,
    filterRow(chips('Ad type', typeOpts, p.ad_type, (k) => amzHref(p, { view: 'campaigns', ad_type: k === 'all' ? null : k, product: p.product }), 'amz-type-filter'),
      chips('Product', prodOpts, p.product, (k) => amzHref(p, { view: 'campaigns', ad_type: p.ad_type, product: k === 'all' ? null : k }), 'amz-product-filter'), box),
    h('div', { class: 'tablehead' }, h('h2', { id: 'amz-campaigns-title' }, 'Campaigns'),
      csvButton('amazon-campaigns.csv', ['Campaign', 'Ad type', 'Product', 'Status', 'Ad spend', 'Impressions', 'Clicks', 'Click-through rate', 'Cost per click', 'Ad-reported orders', 'Ad-reported sales', 'Ad return', 'Cost per order'],
        () => { const f = filt(box.querySelector('input').value.trim()); return d.rows.filter((r) => !f || f(r))
          .map((r) => [r.name, r.ad_type_label, r.product_label, r.status, r.m.spend, r.m.impressions, r.m.clicks, r.m.ctr, r.m.cpc, r.m.ad_orders, r.m.ad_sales, r.m.ad_return, r.m.cost_per_order]); })),
    t.el));
  if (trend) trend.load();
}

// ---------------------------------------------------------------------------------------------------------------- products
async function productsView(ctx, p, mount) {
  const d = await api('/api/amazon/products', winParams(p), { signal: ctx.signal });
  if (!ctx.alive()) return;
  if (d.loaded === false) { setRangeInfo(p, null, null); mount(emptyState()); return; }
  setRangeInfo(p, d.window_info, null, { unclassified: false });
  setFooter(d);
  const cols = [
    { key: 'name', label: 'Product (ASIN)', sticky: true, sort: (r) => r.name, total: () => 'Total',
      cell: (r) => h('div', { class: 'cell-label-text' }, h('div', { class: 'nm' }, h('span', { class: 'rowname', title: r.name }, r.name)), h('div', { class: 'sub' }, h('span', { class: 'muted small' }, `${r.asin} · ${r.product_label}`))) },
    col('revenue', 'Revenue', (r) => r.revenue, fmt.money, { total: (r) => r.revenue }),
    col('units', 'Units', (r) => r.units, fmt.int, { total: (r) => r.units }),
    col('nc_revenue', 'New-customer revenue', (r) => r.nc_revenue, fmt.money, { total: (r) => r.nc_revenue }),
    col('ad_spend', 'Ad spend', (r) => r.ad_spend, fmt.money2, { total: (r) => r.ad_spend, title: 'Sponsored Products and Sponsored Display by advertised ASIN' }),
    col('ad_sales', 'Ad-reported sales', (r) => r.ad_sales, fmt.money, { total: (r) => r.ad_sales }),
    col('ad_return', 'Ad return', (r) => r.ad_return, times, { total: (r) => r.ad_return }),
    col('ad_share_of_revenue', 'Ad spend share of revenue', (r) => r.ad_share_of_revenue, (v) => (isNum(v) ? fmt.rate(v) : '–'), { total: (r) => r.ad_share_of_revenue }),
  ];
  const t = createTable({ columns: cols, rows: d.rows, total: d.total, rowKey: (r) => r.key, tableClass: 'compact', testid: 'amz-products', sortKey: 'revenue', sortDir: 'desc' });
  const gap = isNum(d.spend_without_product) && d.spend_without_product > 0 ? `${fmt.money(d.spend_without_product)} of Amazon ad spend in this range is in no row here (Sponsored Brands and campaigns with no advertised ASIN).` : null;
  mount(h('div', null,
    noteLine(d.note, 'amz-products-note'), gap ? noteLine(gap, 'amz-products-gap') : null,
    h('div', { class: 'tablehead' }, h('h2', { id: 'amz-asins-title' }, 'By ASIN'), csvButton('amazon-products.csv', ['ASIN', 'Product', 'Revenue', 'Units', 'New-customer revenue', 'Ad spend', 'Ad-reported sales', 'Ad return'],
      () => d.rows.map((r) => [r.asin, r.name, r.revenue, r.units, r.nc_revenue, r.ad_spend, r.ad_sales, r.ad_return]))),
    t.el));
}

// ---------------------------------------------------------------------------------------------------------------- search terms (organic)
function historyCard(p, term, signal, onClose) {
  const st = { metric: 'purchase_share' };
  const canvas = h('canvas', { id: 'amz-history-chart', 'aria-label': 'Search term history' });
  const note = h('div', { class: 'trend-note' }, 'A gap in a line means the term was not among Amazon’s top terms for that product that week. It is not zero.');
  const title = h('h3', { 'data-testid': 'amz-history-title' }, `${term.term} · weekly history`);
  const sel = h('select', { id: 'amz-history-metric', 'aria-label': 'Share to draw' }, [['purchase_share', 'Purchase share'], ['click_share', 'Click share'], ['cart_add_share', 'Cart-add share'], ['impression_share', 'Impression share']].map(([k, l]) => h('option', { value: k }, l)));
  let chart = null, data = null;
  function draw() {
    if (!data || !canvas.isConnected) return;
    const labels = data.weeks;
    const lineLabel = sel.options[sel.selectedIndex].text;
    const lines = data.series.map((s) => ({ axis: 'right', type: 'line', kind: 'rate', label: `${s.label}: ${lineLabel.toLowerCase()}`, color: PRODUCT_COLORS[s.slug] || LINE_COLOR, width: s.slug === 'all' ? 2 : 2.4,
      dash: s.slug === 'all' ? [5, 3] : undefined, markers: true,
      data: s.points.map((x) => (x.present && isNum(x[st.metric]) ? x[st.metric] / 100 : null)) }));
    if (chart) chart.destroy();
    chart = trendChart(canvas, { labels, grain: 'week', datasets: [{ axis: 'left', type: 'bar', kind: 'int', label: 'Search volume', data: data.volume.map((x) => (isNum(x.volume) ? x.volume : null)), color: BAR_COLOR }, ...lines],
      left: { kind: 'int', title: 'Search volume (bars, left)' }, right: { kind: 'rate', title: `${lineLabel} (lines, right)` } });
  }
  sel.addEventListener('change', () => { st.metric = sel.value; draw(); });
  const close = h('button', { type: 'button', class: 'btn small', 'data-testid': 'amz-history-close' }, 'Close');
  close.addEventListener('click', onClose);
  const el = h('div', { class: 'card trendcard', 'data-testid': 'amz-history' },
    h('div', { class: 'card-head' }, title, close),
    h('div', { class: 'ctl-row' }, h('div', { class: 'ctl' }, h('span', { class: 'ctl-name' }, 'Share'), sel)),
    h('div', { class: 'chartbox' }, canvas), note);
  async function load() {
    let d;
    try { d = await api('/api/amazon/search-terms/history', { term: term.key || term.term }, { signal }); } catch (e) { if (e && e.name === 'AbortError') return; note.textContent = 'History unavailable: ' + e.message; return; }
    if (d.loaded === false) { note.textContent = d.message; return; }
    data = d;
    title.textContent = `${d.term} · weekly history`;
    draw();
  }
  return { el, load, destroy() { if (chart) chart.destroy(); } };
}

async function searchView(ctx, p, mount) {
  const product = PRODUCTS.some(([k]) => k === p.product) ? p.product : null;
  const branded = ['branded', 'nonbranded'].includes(p.branded) ? p.branded : null;
  const baseArgs = { ...winParams(p), ...(product ? { product } : {}), ...(branded ? { branded } : {}) };
  const first = await api('/api/amazon/search-terms', { ...baseArgs, ...(p.q ? { q: p.q } : {}) }, { signal: ctx.signal });
  if (!ctx.alive()) return;
  if (first.loaded === false) { setRangeInfo(p, null, null); mount(emptyState()); return; }
  setRangeInfo(p, first.window_info, null, { unclassified: false });
  setFooter(first);
  let cur = first, seq = 0, open = null;
  const slot = h('div', { 'data-testid': 'amz-history-slot' });
  const termCell = (r) => h('div', { class: 'cell-label-text' },
    h('div', { class: 'nm' }, h('button', { type: 'button', class: 'linkbtn rowlink', title: 'Show the weekly history of this term', 'data-term': r.key, onclick: () => openTerm(r) }, r.term)),
    r.branded ? h('div', { class: 'sub' }, h('span', { class: 'badge muted-badge', title: 'The term contains one of our brand names' }, 'Branded')) : null);
  const c = (key, label, get, cell, title) => col(key, label, get, cell, { title });
  const cols = [
    { key: 'term', label: 'Search term', sticky: true, sort: (r) => r.term, cell: termCell },
    c('volume_latest', 'Search volume, latest week', (r) => r.volume_latest, fmt.int, 'Searches on Amazon for the term in the latest week of the range in which it appeared (market total, not ours)'),
    c('volume_avg', 'Search volume, weekly average', (r) => r.volume_avg, (v) => (isNum(v) ? fmt.int(v) : '–'), 'Average weekly searches over the weeks the term appeared in the range'),
    c('impression_share', 'Our impression share', (r) => r.impression_share, pct2, 'Our impressions / all impressions for the term (includes our sponsored placements)'),
    c('click_share', 'Our click share', (r) => r.click_share, pct2, 'Our clicks / all clicks for the term'),
    c('cart_add_share', 'Our cart-add share', (r) => r.cart_add_share, pct2, 'Our cart adds / all cart adds for the term'),
    c('purchase_share', 'Our purchase share', (r) => r.purchase_share, pct2, 'Our purchases / all purchases for the term (the closest measure to rank)'),
    c('our_clicks', 'Our clicks', (r) => r.our_clicks, fmt.int),
    c('our_purchases', 'Our purchases', (r) => r.our_purchases, fmt.int, 'Search-origin, same-day purchases: will not tie to orders'),
    c('click_share_change', 'Click share change', (r) => r.click_share_change, ppCell, 'Change in our click share, in percentage points, vs the prior equal period (blank if the term was not listed then)'),
    c('purchase_share_change', 'Purchase share change', (r) => r.purchase_share_change, ppCell, 'Change in our purchase share, in percentage points, vs the prior equal period'),
    c('volume_change', 'Search volume change', (r) => r.volume_change, pctChange, 'Change in weekly average search volume vs the prior equal period'),
    c('weeks', 'Weeks listed', (r) => r.weeks_present / (r.weeks_in_range || 1), (_v, r) => `${r.weeks_present} of ${r.weeks_in_range}`, 'Weeks of the range in which the term was among the top terms reported'),
  ];
  const t = createTable({ columns: cols, rows: cur.rows, rowKey: (r) => r.key, tableClass: 'compact', testid: 'amz-search-terms', sortKey: 'volume_avg', sortDir: 'desc', emptyText: 'No search terms match.' });
  const summary = h('div', { class: 'note-line', 'data-testid': 'amz-search-summary' });
  const periodNote = h('div', { class: 'note-line az-note', 'data-testid': 'amz-search-period' });
  function describe() {
    const s = cur.summary;
    summary.textContent = s ? `${fmt.int(s.terms)} terms${cur.truncated ? ` (the ${fmt.int(cur.rows.length)} with most searches are shown)` : ''} · our clicks ${fmt.int(s.our_clicks)} · our purchases ${fmt.int(s.our_purchases)}` : '';
    const w = cur.weeks || [];
    periodNote.textContent = w.length ? `${w.length} full week${w.length === 1 ? '' : 's'} (${fmt.day(w[0].from)} – ${fmt.day(w[w.length - 1].to)})${cur.prior_available ? `; change columns compare with the ${w.length} week${w.length === 1 ? '' : 's'} before.` : '.'} ${cur.note || ''}`.trim() : (cur.note || '');
  }
  describe();
  async function refetch(q) {
    const my = ++seq;
    let d;
    try { d = await api('/api/amazon/search-terms', { ...baseArgs, ...(q ? { q } : {}) }, { signal: ctx.signal }); } catch (e) { if (e && e.name === 'AbortError') return; summary.textContent = 'Search failed: ' + e.message; return; }
    if (my !== seq || d.loaded === false) return;
    cur = d;
    t.setRows(d.rows);
    describe();
  }
  const box = searchBox(p.q, (q) => { replaceParam('q', q); refetch(q); }, 'amz-search-input', 'Search term');
  function openTerm(r) {
    if (open) open.destroy();
    open = historyCard(p, r, ctx.signal, closeTerm);
    slot.replaceChildren(open.el);
    replaceParam('term', r.key);
    open.load();
    slot.scrollIntoView({ block: 'nearest' });
  }
  function closeTerm() {
    if (open) open.destroy();
    open = null;
    slot.replaceChildren();
    replaceParam('term', null);
  }
  const hf = (extra) => amzHref(p, { view: 'search', product, branded, q: p.q, ...extra });
  mount(h('div', null,
    noteLine('Amazon gives no rank position for a search term. Our share of clicks and of purchases is the closest measure. Impressions include our own sponsored placements. Amazon reports only the top 100 or so terms per product per week, so a week with no figure means the term was not in that list; it is not zero. A product’s share adds up its ASINs that Amazon listed for the term.', 'amz-search-note'),
    filterRow(chips('Product', [['all', 'All products'], ...PRODUCTS], product, (k) => hf({ product: k === 'all' ? null : k, term: null }), 'amz-product-filter'),
      chips('Terms', [['all', 'All'], ['branded', 'Branded'], ['nonbranded', 'Non-branded']], branded, (k) => hf({ branded: k === 'all' ? null : k, term: null }), 'amz-branded-filter'), box),
    periodNote, summary, slot,
    h('div', { class: 'tablehead' }, h('h2', { id: 'amz-terms-title' }, 'Search terms'), csvButton('amazon-search-terms.csv',
      ['Search term', 'Branded', 'Search volume latest week', 'Search volume weekly average', 'Impression share %', 'Click share %', 'Cart-add share %', 'Purchase share %', 'Our clicks', 'Our purchases', 'Click share change pp', 'Purchase share change pp', 'Search volume change', 'Weeks listed', 'Weeks in range'],
      () => cur.rows.map((r) => [r.term, r.branded ? 'yes' : 'no', r.volume_latest, r.volume_avg, r.impression_share, r.click_share, r.cart_add_share, r.purchase_share, r.our_clicks, r.our_purchases, r.click_share_change, r.purchase_share_change, r.volume_change, r.weeks_present, r.weeks_in_range]))),
    t.el));
  if (p.term) {
    const r = cur.rows.find((x) => x.key === p.term) || { key: p.term, term: p.term };
    openTerm(r);
  }
}

// ---------------------------------------------------------------------------------------------------------------- fees
async function feesView(ctx, p, mount) {
  const d = await api('/api/amazon/fees', winParams(p), { signal: ctx.signal });
  if (!ctx.alive()) return;
  if (d.loaded === false) { setRangeInfo(p, null, null); mount(emptyState()); return; }
  setRangeInfo(p, d.window_info, null, { unclassified: false });
  setFooter(d);
  const monthLabel = (m) => { const [y, mo] = m.month.split('-'); return new Date(Date.UTC(+y, +mo - 1, 1)).toLocaleString('en-US', { month: 'short', year: 'numeric', timeZone: 'UTC' }) + (m.partial ? ' (part)' : ''); };
  const cols = [
    { key: 'label', label: 'Fee or line', sticky: true, sort: (r) => r.label, total: () => 'Total',
      cell: (r) => h('div', { class: 'cell-label-text' }, h('div', { class: 'nm' }, h('span', { class: 'rowname', title: r.label }, r.label)), r.sublabel ? h('div', { class: 'sub' }, h('span', { class: 'muted small' }, r.sublabel)) : null) },
    ...d.months.map((m) => ({ key: 'm' + m.month, label: monthLabel(m), num: true, sort: (r) => (r.months || {})[m.month], cell: (r) => money2((r.months || {})[m.month]), total: (tt) => money2(tt.months[m.month]),
      title: m.partial ? 'Only the days of the range that fall in this month' : null })),
    col('total', 'Total', (r) => r.total, money2, { total: (r) => r.total }),
  ];
  const t = createTable({ columns: cols, rows: d.rows, total: d.total, rowKey: (r) => r.key, tableClass: 'compact', testid: 'amz-fees', sortKey: 'total', sortDir: 'asc', emptyText: 'No settlement lines in this range.' });
  mount(h('div', null,
    noteLine(d.note, 'amz-fees-note'),
    h('div', { class: 'tablehead' }, h('h2', { id: 'amz-fees-title' }, 'Settlement lines by month'), csvButton('amazon-fees.csv', ['Fee or line', 'Type', ...d.months.map((m) => m.month), 'Total'],
      () => d.rows.map((r) => [r.label, r.sublabel, ...d.months.map((m) => (r.months || {})[m.month]), r.total]))),
    t.el));
}

// ---------------------------------------------------------------------------------------------------------------- keyword layer (Sponsored Products, loaded by hand)
const KW_PRODUCTS = [...PRODUCTS, ['mixed', 'Mixed products'], ['unmapped', 'No product found']];
const kwHref = (p, extra) => amzHref(p, extra);

function kwEmptyState() {
  return h('div', { class: 'empty-range', role: 'status', 'data-testid': 'amz-empty' },
    h('strong', 'Amazon keyword data has not been loaded yet.'),
    h('span', ' It is loaded by hand from Amazon Ads reports pulled through ExpandFi Data Dash; it does not refresh on its own. See docs/AMAZON_DATA.md for the load command.'));
}

function setKwFooter(d) {
  const el = document.getElementById('source');
  if (el) { el.hidden = false; el.textContent = `Source: Amazon Ads Sponsored Products reports pulled through ExpandFi Data Dash and loaded by hand${d && d.window_info ? `, data through ${fmt.day(d.window_info.data_through)}` : ''}.`; }
}

function coverageLine(d) {
  return noteLine(d.coverage_text, 'amz-kw-coverage');
}

function kwMetricCols() {
  return [
    col('impressions', 'Impressions', (r) => r.impressions, fmt.int, { total: true }),
    col('clicks', 'Clicks', (r) => r.clicks, fmt.int, { total: true }),
    col('ctr', 'Click-through rate', (r) => r.ctr, fmt.rate, { total: true, title: 'Clicks / impressions' }),
    col('cost', 'Cost', (r) => r.cost, money2, { total: true, title: 'Ad spend' }),
    col('cpc', 'Cost per click', (r) => r.cpc, money2, { total: true, title: 'Cost / clicks' }),
    col('purchases', 'Purchases', (r) => r.purchases, fmt.int, { total: true, title: 'Purchases within Amazon’s 7-day attribution window' }),
    col('conversion_rate', 'Conversion rate', (r) => r.conversion_rate, fmt.rate, { total: true, title: 'Purchases / clicks' }),
    col('sales', 'Sales', (r) => r.sales, fmt.money, { total: true, title: 'Sales within Amazon’s 7-day attribution window' }),
    col('return_on_ad_spend', 'Return on ad spend', (r) => r.return_on_ad_spend, times, { total: true, title: 'Sales / cost' }),
    col('acos', 'Ad cost as % of ad sales', (r) => r.acos, fmt.rate, { total: true, title: 'Cost / sales (known as ACOS on Amazon)' }),
  ];
}

const KW_METRIC_HEADERS = ['Impressions', 'Clicks', 'Click-through rate', 'Cost', 'Cost per click', 'Purchases', 'Conversion rate', 'Sales', 'Return on ad spend', 'Ad cost as % of ad sales'];
const kwMetricValues = (r) => [r.impressions, r.clicks, r.ctr, r.cost, r.cpc, r.purchases, r.conversion_rate, r.sales, r.return_on_ad_spend, r.acos];
const trunc = (v, title) => h('span', { class: 'rowname', title: title || v }, v);

async function keywordsView(ctx, p, mount) {
  const state = ['enabled', 'paused'].includes(p.state) ? p.state : null;
  const product = KW_PRODUCTS.some(([k]) => k === p.product) ? p.product : null;
  const all = p.all === '1';
  const baseArgs = { ...winParams(p), ...(product ? { product } : {}), ...(state ? { state } : {}), ...(all ? { all: '1' } : {}) };
  const first = await api('/api/amazon/keywords', { ...baseArgs, ...(p.q ? { q: p.q } : {}) }, { signal: ctx.signal });
  if (!ctx.alive()) return;
  if (first.loaded === false) { setRangeInfo(p, null, null); mount(kwEmptyState()); return; }
  setRangeInfo(p, first.window_info, null, { unclassified: false });
  setKwFooter(first);
  let cur = first, seq = 0;
  const cols = [
    { key: 'text', label: 'Keyword or target', sticky: true, sort: (r) => r.text, total: () => 'Total',
      cell: (r) => h('div', { class: 'cell-label-text' }, h('div', { class: 'nm' }, trunc(r.text)), h('div', { class: 'sub' }, h('span', { class: 'badge muted-badge' }, r.match_label))) },
    col('campaign', 'Campaign', (r) => r.campaign, (v) => trunc(v), { num: false }),
    col('ad_group', 'Ad group', (r) => r.ad_group, (v) => trunc(v), { num: false }),
    col('product', 'Product', (r) => r.product_label, (v) => v, { num: false }),
    col('state', 'State', (r) => r.state || 'removed', (v, r) => (r.state ? v : h('span', { title: 'Not in the latest keyword or target list: archived or removed since' }, 'Not listed')), { num: false }),
    col('bid', 'Current bid', (r) => r.bid, (v, r) => (isNum(v) ? money2(v) : r.bid_inherited ? h('span', { title: 'The target uses the ad group bid' }, 'Ad group bid') : '–'), { title: 'Bid in the latest snapshot' }),
    ...kwMetricCols(),
  ];
  const t = createTable({ columns: cols, rows: cur.rows, total: { ...cur.total }, rowKey: (r) => r.key, tableClass: 'compact', testid: 'amz-keywords', sortKey: 'cost', sortDir: 'desc', emptyText: 'No keywords match.' });
  const summary = h('div', { class: 'note-line', 'data-testid': 'amz-kw-summary' });
  function describe() {
    summary.textContent = `${fmt.int(cur.total_rows)} keywords and targets${cur.truncated ? ` (the ${fmt.int(cur.rows.length)} with most cost are shown)` : ''}.`;
  }
  describe();
  async function refetch(q) {
    const my = ++seq;
    let d;
    try { d = await api('/api/amazon/keywords', { ...baseArgs, ...(q ? { q } : {}) }, { signal: ctx.signal }); } catch (e) { if (e && e.name === 'AbortError') return; summary.textContent = 'Search failed: ' + e.message; return; }
    if (my !== seq || d.loaded === false) return;
    cur = d;
    t.setRows(d.rows, { ...d.total });
    describe();
  }
  const box = searchBox(p.q, (q) => { replaceParam('q', q); refetch(q); }, 'amz-kw-search', 'Keyword, campaign or ad group');
  const hf = (extra) => kwHref(p, { view: 'keywords', product, state, all: all ? '1' : null, q: p.q, ...extra });
  mount(h('div', null,
    h('div', { class: 'tablehead' }, h('h2', { id: 'amz-keywords-title' }, 'Keywords and targets'),
      csvButton('amazon-keywords.csv', ['Keyword or target', 'Match type', 'Campaign', 'Ad group', 'Product', 'State', 'Current bid', ...KW_METRIC_HEADERS],
        () => cur.rows.map((r) => [r.text, r.match_label, r.campaign, r.ad_group, r.product_label, r.state || 'not listed', r.bid, ...kwMetricValues(r)]))),
    coverageLine(first),
    first.bids_note ? noteLine(first.bids_note, 'amz-kw-bids') : null,
    noteLine(first.note, 'amz-kw-note'),
    filterRow(chips('Product', [['all', 'All'], ...first.facets.products.map((x) => [x.key, x.label])], product, (k) => hf({ product: k === 'all' ? null : k }), 'amz-product-filter'),
      chips('State', [['all', 'All'], ['enabled', 'Enabled'], ['paused', 'Paused']], state, (k) => hf({ state: k === 'all' ? null : k }), 'amz-state-filter'),
      chips('Show', [['imp', 'With impressions'], ['all', 'All keywords']], all ? 'all' : 'imp', (k) => hf({ all: k === 'all' ? '1' : null }), 'amz-show-filter'), box),
    summary, t.el));
}

// ---- ad search terms
function matchesCard(p, term, signal, onClose) {
  const title = h('h3', { 'data-testid': 'amz-matches-title' }, `${term.term} · what matched it`);
  const slot = h('div', { 'data-testid': 'amz-matches-slot' }, h('div', { class: 'trend-note' }, 'Loading…'));
  const close = h('button', { type: 'button', class: 'btn small', 'data-testid': 'amz-matches-close' }, 'Close');
  close.addEventListener('click', onClose);
  const el = h('div', { class: 'card trendcard', 'data-testid': 'amz-matches' }, h('div', { class: 'card-head' }, title, close), slot);
  async function load() {
    let d;
    try { d = await api('/api/amazon/ad-search-terms/matches', { ...winParams(p), ...(p.product ? { product: p.product } : {}), term: term.key || term.term }, { signal }); } catch (e) { if (e && e.name === 'AbortError') return; slot.textContent = 'Unavailable: ' + e.message; return; }
    if (d.loaded === false) { slot.textContent = d.message; return; }
    const cols = [
      { key: 'text', label: 'Keyword or target', sticky: true, sort: (r) => r.text, total: () => 'Total', cell: (r) => h('div', { class: 'cell-label-text' }, h('div', { class: 'nm' }, trunc(r.text)), h('div', { class: 'sub' }, h('span', { class: 'badge muted-badge' }, r.match_label))) },
      col('campaign', 'Campaign', (r) => r.campaign, (v) => trunc(v), { num: false }),
      col('ad_group', 'Ad group', (r) => r.ad_group, (v) => trunc(v), { num: false }),
      col('product', 'Product', (r) => r.product_label, (v) => v, { num: false }),
      ...kwMetricCols(),
    ];
    const t = createTable({ columns: cols, rows: d.rows, total: { ...d.total }, rowKey: (r) => r.key, tableClass: 'compact', testid: 'amz-matches-table', sortKey: 'cost', sortDir: 'desc', emptyText: 'No matches in this range.' });
    title.textContent = `${d.term} · matched ${d.rows.length} keyword${d.rows.length === 1 ? '' : 's'} or target${d.rows.length === 1 ? '' : 's'}`;
    slot.replaceChildren(t.el);
  }
  return { el, load };
}

async function adSearchView(ctx, p, mount) {
  const product = KW_PRODUCTS.some(([k]) => k === p.product) ? p.product : null;
  const baseArgs = { ...winParams(p), ...(product ? { product } : {}) };
  const first = await api('/api/amazon/ad-search-terms', { ...baseArgs, ...(p.q ? { q: p.q } : {}) }, { signal: ctx.signal });
  if (!ctx.alive()) return;
  if (first.loaded === false) { setRangeInfo(p, null, null); mount(kwEmptyState()); return; }
  setRangeInfo(p, first.window_info, null, { unclassified: false });
  setKwFooter(first);
  let cur = first, seq = 0, open = null;
  const slot = h('div', { 'data-testid': 'amz-matches-host' });
  const termCell = (r) => h('div', { class: 'cell-label-text' }, h('div', { class: 'nm' }, h('button', { type: 'button', class: 'linkbtn rowlink', title: 'Show the keywords and campaigns that matched this term', 'data-term': r.key, onclick: () => openTerm(r) }, r.term)),
    h('div', { class: 'sub' }, h('span', { class: 'muted small' }, `${fmt.int(r.keywords)} keyword${r.keywords === 1 ? '' : 's'} · ${fmt.int(r.campaigns)} campaign${r.campaigns === 1 ? '' : 's'}`)));
  const cols = [
    { key: 'term', label: 'Customer search term', sticky: true, sort: (r) => r.term, total: () => 'Total', cell: termCell },
    ...kwMetricCols(),
    col('search_volume', 'Search volume, weekly average', (r) => r.search_volume, (v) => (isNum(v) ? fmt.int(v) : '–'), { title: 'Amazon searches for the term per week, averaged over the full weeks of the range in which Search Query Performance lists it (blank if it never does)' }),
    col('our_click_share', 'Our click share', (r) => r.our_click_share, pct2, { title: 'Our clicks / all clicks for the term, from Search Query Performance (includes our sponsored clicks)' }),
    col('our_purchase_share', 'Our purchase share', (r) => r.our_purchase_share, pct2, { title: 'Our purchases / all purchases for the term, from Search Query Performance' }),
    col('weeks_listed', 'Weeks listed', (r) => (isNum(r.weeks_listed) && r.weeks_in_range ? r.weeks_listed / r.weeks_in_range : null), (_v, r) => (isNum(r.weeks_listed) ? `${r.weeks_listed} of ${r.weeks_in_range}` : '–'), { title: 'Full weeks of the range in which Search Query Performance lists the term' }),
  ];
  const t = createTable({ columns: cols, rows: cur.rows, total: { ...cur.total }, rowKey: (r) => r.key, tableClass: 'compact', testid: 'amz-ad-search', sortKey: 'cost', sortDir: 'desc', emptyText: 'No search terms match.' });
  const summary = h('div', { class: 'note-line', 'data-testid': 'amz-ad-summary' });
  function describe() {
    summary.textContent = `${fmt.int(cur.total_terms)} search terms${cur.truncated ? ` (the ${fmt.int(cur.rows.length)} with most cost are shown)` : ''}`
      + (isNum(cur.organic_cost_share) ? ` · ${(cur.organic_cost_share * 100).toFixed(0)}% of the cost is on terms Search Query Performance lists` : '') + '.';
  }
  describe();
  async function refetch(q) {
    const my = ++seq;
    let d;
    try { d = await api('/api/amazon/ad-search-terms', { ...baseArgs, ...(q ? { q } : {}) }, { signal: ctx.signal }); } catch (e) { if (e && e.name === 'AbortError') return; summary.textContent = 'Search failed: ' + e.message; return; }
    if (my !== seq || d.loaded === false) return;
    cur = d;
    t.setRows(d.rows, { ...d.total });
    describe();
  }
  const box = searchBox(p.q, (q) => { replaceParam('q', q); refetch(q); }, 'amz-ad-search-input', 'Search term');
  function openTerm(r) {
    open = matchesCard(p, r, ctx.signal, closeTerm);
    slot.replaceChildren(open.el);
    replaceParam('term', r.key);
    open.load();
    slot.scrollIntoView({ block: 'nearest' });
  }
  function closeTerm() { open = null; slot.replaceChildren(); replaceParam('term', null); }
  const hf = (extra) => kwHref(p, { view: 'adsearch', product, q: p.q, ...extra });
  mount(h('div', null,
    h('div', { class: 'tablehead' }, h('h2', { id: 'amz-ad-search-title' }, 'Ad search terms'),
      csvButton('amazon-ad-search-terms.csv', ['Customer search term', 'Keywords matched', 'Campaigns matched', ...KW_METRIC_HEADERS, 'Search volume weekly average', 'Our click share %', 'Our purchase share %', 'Weeks listed', 'Weeks in range'],
        () => cur.rows.map((r) => [r.term, r.keywords, r.campaigns, ...kwMetricValues(r), r.search_volume, r.our_click_share, r.our_purchase_share, r.weeks_listed, r.weeks_in_range]))),
    coverageLine(first),
    noteLine(first.note, 'amz-ad-note'),
    filterRow(chips('Product', [['all', 'All'], ...first.facets.products.map((x) => [x.key, x.label])], product, (k) => hf({ product: k === 'all' ? null : k, term: null }), 'amz-product-filter'), box),
    summary, slot, t.el));
  if (p.term) openTerm(cur.rows.find((x) => x.key === p.term) || { key: p.term, term: p.term });
}

// ---- negative keywords
async function negativesView(ctx, p, mount) {
  const match = ['phrase', 'exact'].includes(p.match) ? p.match : null;
  const state = ['enabled', 'archived'].includes(p.state) ? p.state : null;
  const product = KW_PRODUCTS.some(([k]) => k === p.product) ? p.product : null;
  const baseArgs = { ...winParams(p), ...(match ? { match } : {}), ...(state ? { state } : {}), ...(product ? { product } : {}) };
  const first = await api('/api/amazon/negatives', { ...baseArgs, ...(p.q ? { q: p.q } : {}) }, { signal: ctx.signal });
  if (!ctx.alive()) return;
  if (first.loaded === false) { setRangeInfo(p, null, null); mount(kwEmptyState()); return; }
  setRangeInfo(p, first.window_info, null, { unclassified: false });
  setKwFooter(first);
  let cur = first, seq = 0;
  const cols = [
    { key: 'text', label: 'Negative keyword', sticky: true, sort: (r) => r.text, cell: (r) => h('div', { class: 'cell-label-text' }, h('div', { class: 'nm' }, trunc(r.text))) },
    col('match_label', 'Match type', (r) => r.match_label, (v) => v, { num: false }),
    col('campaign', 'Campaign', (r) => r.campaign, (v) => trunc(v), { num: false }),
    col('ad_group', 'Ad group', (r) => r.ad_group, (v) => trunc(v), { num: false }),
    col('product', 'Product', (r) => r.product_label, (v) => v, { num: false }),
    col('state', 'State', (r) => r.state, (v) => fmt.text(v ? v.charAt(0).toUpperCase() + v.slice(1) : null), { num: false }),
  ];
  const t = createTable({ columns: cols, rows: cur.rows, rowKey: (r) => r.key, tableClass: 'compact', testid: 'amz-negatives', sortKey: 'text', sortDir: 'asc', emptyText: 'No negative keywords match.' });
  const summary = h('div', { class: 'note-line', 'data-testid': 'amz-neg-summary' });
  function describe() {
    summary.textContent = `${fmt.int(cur.matched)} of ${fmt.int(cur.total)} negative keywords match${cur.truncated ? `; the first ${fmt.int(cur.shown)} are listed, narrow the search to see the rest` : ''}.`;
  }
  describe();
  async function refetch(q) {
    const my = ++seq;
    let d;
    try { d = await api('/api/amazon/negatives', { ...baseArgs, ...(q ? { q } : {}) }, { signal: ctx.signal }); } catch (e) { if (e && e.name === 'AbortError') return; summary.textContent = 'Search failed: ' + e.message; return; }
    if (my !== seq || d.loaded === false) return;
    cur = d;
    t.setRows(d.rows);
    describe();
  }
  const box = searchBox(p.q, (q) => { replaceParam('q', q); refetch(q); }, 'amz-neg-search', 'Keyword, campaign or ad group');
  const hf = (extra) => kwHref(p, { view: 'negatives', match, state, product, q: p.q, ...extra });
  const count = (n) => (isNum(n) ? ` (${fmt.int(n)})` : '');
  const sf = first.facets;
  // the file holds every match, not just the rows on screen (the capped list is a display limit)
  const csv = csvButton('amazon-negative-keywords.csv', ['Negative keyword', 'Match type', 'Campaign', 'Ad group', 'Product', 'State'], async () => {
    const qv = box.querySelector('input').value.trim();
    const d = await api('/api/amazon/negatives', { ...baseArgs, ...(qv ? { q: qv } : {}), limit: 50000 }, { signal: ctx.signal });
    return d.rows.map((r) => [r.text, r.match_label, r.campaign, r.ad_group, r.product_label, r.state]);
  });
  mount(h('div', null,
    h('div', { class: 'tablehead' }, h('h2', { id: 'amz-negatives-title' }, 'Negative keywords'), csv),
    coverageLine(first),
    noteLine(first.note, 'amz-neg-note'),
    filterRow(chips('Match type', [['all', 'All'], ['phrase', 'Phrase' + count(sf.match.NEGATIVE_PHRASE)], ['exact', 'Exact' + count(sf.match.NEGATIVE_EXACT)]], match, (k) => hf({ match: k === 'all' ? null : k }), 'amz-match-filter'),
      chips('State', [['all', 'All'], ['enabled', 'Enabled' + count(sf.states.enabled)], ['archived', 'Archived' + count(sf.states.archived)]], state, (k) => hf({ state: k === 'all' ? null : k }), 'amz-state-filter'),
      chips('Product', [['all', 'All'], ...sf.products.map((x) => [x.key, x.label + count(x.count)])], product, (k) => hf({ product: k === 'all' ? null : k }), 'amz-product-filter'), box),
    summary, t.el));
}

// ---------------------------------------------------------------------------------------------------------------- page
const VIEW_FNS = { overview: overviewView, campaigns: campaignsView, products: productsView, keywords: keywordsView, adsearch: adSearchView, negatives: negativesView, search: searchView, fees: feesView };
const TITLES = { overview: 'Amazon', campaigns: 'Amazon campaigns', products: 'Amazon products', keywords: 'Amazon keywords', adsearch: 'Amazon ad search terms', negatives: 'Amazon negative keywords', search: 'Amazon search terms', fees: 'Amazon fees' };
const DESCRIPTIONS = {
  overview: 'Amazon sales, orders and advertising for the range, from Amazon through ExpandFi.',
  campaigns: 'Every Amazon ad campaign with spend and the results Amazon credits to it.',
  products: 'Revenue and advertising by Amazon product (ASIN).',
  keywords: 'Every Sponsored Products keyword and target with its cost, sales and current bid.',
  adsearch: 'The customer search terms your ads served on, next to how much of that search you win without paying.',
  negatives: 'The negative keywords set on your Sponsored Products ad groups.',
  search: 'Organic search: how many people search each term on Amazon and how much of it we win.',
  fees: 'What Amazon settled with us, by type of fee and month.',
};

export async function amazonPage(ctx) {
  const { params: p } = ctx;
  const v = view(p);
  document.title = TITLES[v] + ' · Lonvera';
  const body = h('div', { 'data-testid': 'amz-body' }, h('p', { class: 'muted', role: 'status', 'data-testid': 'amz-loading' }, h('span', { class: 'spinner', 'aria-hidden': 'true' }), ' Loading…'));
  const root = { label: 'Lonvera', href: href('/', winParams(p)) };
  const page = h('div', { class: 'page', 'data-level': 'amazon', 'data-amz': v },
    pageHeader({ title: v === 'overview' ? 'Amazon' : VIEWS.find(([k]) => k === v)[1], badge: 'Amazon’s own reporting', actions: viewSwitch(p), description: DESCRIPTIONS[v], testid: 'amz-head' }),
    h('div', { class: 'crumbrow' }, breadcrumbs([root, { label: 'Amazon', href: v === 'overview' ? null : amzHref(p, {}) }, ...(v === 'overview' ? [] : [{ label: VIEWS.find(([k]) => k === v)[1] }])])),
    noteLine(NO_SWITCH, 'amz-no-switch'),
    body);
  ctx.mount(page);
  await VIEW_FNS[v](ctx, p, (node) => body.replaceChildren(node));
}
