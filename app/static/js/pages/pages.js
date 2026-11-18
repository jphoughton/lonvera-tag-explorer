// By page (platform Meta): the Lonvera Facebook Page vs every other page (handle) the ads run from, for any window, product and stage.
// Numbers: Meta-reported spend / clicks / purchases next to Upstack new-customer ROAS and CPA; ratios of sums computed by the server (app/meta_pages.py); a figure with nothing behind it reads blank.
import { h, fmt, isNum, href, apiWindow, globalParams, platformUrl } from '../util.js';
import { api } from '../api.js';
import { createTable } from '../table.js';
import { setRangeInfo } from '../controls.js';
import { breadcrumbs, viewSwitch } from '../components.js';
import { pageHeader } from '../ui/pagehead.js';
import { filterRow } from '../ui/pagefilters.js';

const money = (v) => fmt.money(v);
const money2 = (v) => fmt.money2(v);
const times = (v) => (isNum(v) ? fmt.roas(v) : '–');
const pct1 = (v) => (isNum(v) ? fmt.share(v) : '–');
const rate = (v) => (isNum(v) ? fmt.rate(v) : '–');
const int = (v) => (isNum(v) ? fmt.int(v) : '–');

function col(key, label, get, cell, o = {}) {
  return { key, label, num: o.num !== false, sort: get, cell: (r) => cell(get(r), r), total: (t) => cell(get(t), t), title: o.title, sticky: o.sticky, cls: o.cls };
}

const COLUMNS = [
  col('page', 'Page', (r) => r.page, (v, r) => h('span', { title: v }, v, r && r.is_lonvera ? h('span', { class: 'badge muted-badge', style: 'margin-left:6px' }, 'Lonvera') : null), { num: false, sticky: true, cls: 'labelcol' }),
  col('ads', 'Ads with spend', (r) => r.ads, int, { title: 'Distinct ads with spend in the window' }),
  col('spend', 'Meta spend', (r) => r.spend, money, { title: 'Meta spend in the window' }),
  col('share', 'Share of spend', (r) => r.share, pct1, { title: 'This page’s spend divided by total Meta spend in the filtered window' }),
  col('nc_roas', 'NC ROAS', (r) => r.nc_roas, times, { title: 'Upstack new-customer (NC) revenue divided by Meta spend (return on ad spend), over the days Upstack data exists' }),
  col('nc_cpa', 'NC CPA', (r) => r.nc_cpa, money2, { title: 'Meta spend divided by Upstack new customers (cost per acquisition)' }),
  col('meta_roas', 'Meta-reported ROAS', (r) => r.meta_roas, times, { title: 'Purchase value Meta reports divided by spend. Side column: counts returning buyers and Meta’s own attribution windows' }),
  col('cost_per_purchase', 'Cost per purchase', (r) => r.cost_per_purchase, money2, { title: 'Meta spend divided by Meta-reported purchases' }),
  col('aov', 'Average order value', (r) => r.aov, money2, { title: 'Meta-reported purchase value divided by Meta-reported purchases' }),
  col('cpm', 'CPM', (r) => r.cpm, money2, { title: 'Meta spend per 1,000 impressions (cost per mille)' }),
  col('ctr', 'CTR', (r) => r.ctr, rate, { title: 'Link clicks divided by impressions (click-through rate)' }),
  col('cpc', 'Cost per link click', (r) => r.cpc, money2, { title: 'Meta spend divided by link clicks' }),
  col('conv_rate', 'Conversion rate', (r) => r.conv_rate, rate, { title: 'Meta-reported purchases divided by link clicks' }),
];

function emptyState(message) {
  return h('div', { class: 'empty-range', role: 'status', 'data-testid': 'pages-empty' }, h('strong', message || 'Page identity has not loaded yet.'));
}

export async function pagesPage(ctx) {
  const { params: p, signal } = ctx;
  const G = globalParams(p);
  const product = p.product || null;
  document.title = 'By page · Lonvera';
  const d = await api('/api/meta/pages', { ...apiWindow(p), ...(product ? { product } : {}) }, { signal });
  if (!ctx.alive()) return;
  setRangeInfo(p, d.window_info || null, null, { unclassified: false });

  const head = pageHeader({ title: 'By page', badge: 'Meta', actions: viewSwitch('pages', G, 'meta'),
    description: 'The Lonvera Facebook Page against every other page (handle) the ads run from. NC means new customer; ROAS means return on ad spend; CPA means cost per acquisition; CPM is cost per 1,000 impressions; CTR is click-through rate.' });
  const crumbs = h('div', { class: 'crumbrow' }, breadcrumbs([{ label: 'Meta', href: platformUrl('/', 'meta', G) }, { label: 'By page' }]));
  if (!d.loaded) { ctx.mount(h('div', { class: 'page', 'data-level': 'pages' }, head, crumbs, emptyState(d.message))); return; }

  const chip = (label, slug) => h('a', { class: 'seg', href: href('/pages', { ...G, product: slug }), 'data-product': slug || 'all', 'aria-pressed': String((product || null) === slug) }, label);
  const productCtl = h('div', { class: 'ctl', id: 'product-ctl' }, h('span', { class: 'ctl-name' }, 'Product'),
    h('span', { class: 'segs', role: 'group', 'aria-label': 'Product', 'data-testid': 'product-filter' }, chip('All', null), (d.products || []).map((x) => chip(x.name, x.slug))));

  const mk = (rows, total, testid, emptyText) => createTable({ columns: COLUMNS, rows, total, rowKey: (r) => r.key || r.page_id || r.page, tableClass: 'compact', testid, emptyText });
  const sum = mk(d.groups, d.total, 'pages-summary', 'No spend in this window.');
  const t = mk(d.rows, d.total, 'pages-table', 'No pages in this window.');
  const cov = d.coverage || {};
  ctx.mount(h('div', { class: 'page', 'data-level': 'pages' }, head, crumbs, filterRow(productCtl),
    h('div', { class: 'note-line', 'data-testid': 'pages-range' }, `Showing ${fmt.day(d.range.from)} to ${fmt.day(d.range.to)}, ${String(d.range.to).slice(0, 4)} (${fmt.int(d.range.days)} days, Pacific).`),
    h('div', { class: 'note-line', 'data-testid': 'pages-likeforlike' }, d.like_for_like),
    h('div', { class: 'tablehead' }, h('h2', { id: 'level-title' }, 'Lonvera page vs other pages')), sum.el,
    h('div', { class: 'tablehead' }, h('h2', null, 'By page')), t.el,
    h('div', { class: 'note-line', 'data-testid': 'pages-caption' }, d.basis + ' Page identity covers ' + fmt.int(cov.with_page) + ' of ' + fmt.int(cov.ads) + ' ads; creator / partnership ads with no Page are the “(Instagram-only handle)” row and ads with no identity at all the “(unknown page)” row. Meta ad-level data starts ' + fmt.day(d.window_info.daily_start) + ', ' + String(d.window_info.daily_start).slice(0, 4) + '.')));
}
