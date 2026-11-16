// Countries (platform Meta): one row per country with Meta spend next to the new customers and new-customer revenue Shopify recorded for that ship-to country.
// Spend comes from Meta's own country breakdown (so it starts when that breakdown was first loaded); new customers are each customer's first Shopify order, every channel.
// Ratios are ratios of sums computed by the server (app/countries.py); a figure with nothing behind it reads blank, never zero.
import { h, fmt, isNum, href, apiWindow, globalParams, platformUrl } from '../util.js';
import { api } from '../api.js';
import { createTable } from '../table.js';
import { setRangeInfo } from '../controls.js';
import { breadcrumbs, viewSwitch } from '../components.js';
import { pageHeader } from '../ui/pagehead.js';

const money = (v) => fmt.money(v);
const money2 = (v) => fmt.money2(v);
const times = (v) => (isNum(v) ? fmt.roas(v) : '–');
const pct1 = (v) => (isNum(v) ? fmt.share(v) : '–');
const rate = (v) => (isNum(v) ? fmt.rate(v) : '–');

function col(key, label, get, cell, o = {}) {
  return { key, label, num: o.num !== false, sort: get, cell: (r) => cell(get(r), r), total: (t) => cell(get(t), t), title: o.title, sticky: o.sticky, cls: o.cls };
}

const COLUMNS = [
  col('country', 'Country', (r) => r.country, (v) => h('span', { title: v }, v), { num: false, sticky: true, cls: 'labelcol' }),
  col('spend', 'Meta spend', (r) => r.spend, money, { title: 'Meta spend in the window, from Meta’s country breakdown' }),
  col('share', 'Share of spend', (r) => r.share, pct1, { title: 'This country’s spend divided by total Meta spend' }),
  col('new_customers', 'New customers', (r) => r.new_customers, (v) => (isNum(v) ? fmt.int(v) : '–'), { title: 'Customers whose first Shopify order ever fell in the window, by ship-to country (every channel)' }),
  col('nc_revenue', 'NC revenue', (r) => r.nc_revenue, money, { title: 'New-customer (NC) revenue: the order total of those first orders, in dollars' }),
  col('nc_roas', 'NC ROAS', (r) => r.nc_roas, times, { title: 'New-customer revenue divided by Meta spend (return on ad spend)' }),
  col('nc_cpa', 'NC CPA', (r) => r.nc_cpa, money2, { title: 'Meta spend divided by new customers (cost per acquisition)' }),
  col('aov', 'Average order value', (r) => r.aov, money2, { title: 'New-customer revenue divided by new customers' }),
  col('cpm', 'CPM', (r) => r.cpm, money2, { title: 'Meta spend per 1,000 impressions (cost per mille)' }),
  col('ctr', 'CTR', (r) => r.ctr, rate, { title: 'Link clicks divided by impressions (click-through rate)' }),
  col('cpc', 'Cost per link click', (r) => r.cpc, money2, { title: 'Meta spend divided by link clicks' }),
  col('conv_rate', 'Conversion rate', (r) => r.conv_rate, rate, { title: 'New customers divided by link clicks' }),
  col('meta_roas', 'Meta-reported ROAS', (r) => r.meta_roas, times, { title: 'Purchase value Meta reports divided by spend. Side column: Meta counts returning buyers and its own attribution windows' }),
];

function emptyState(message) {
  return h('div', { class: 'empty-range', role: 'status', 'data-testid': 'countries-empty' }, h('strong', message || 'Country data has not loaded yet.'));
}

export async function countriesPage(ctx) {
  const { params: p, signal } = ctx;
  const G = globalParams(p);
  document.title = 'Countries · Lonvera';
  const d = await api('/api/meta/countries', apiWindow(p), { signal });
  if (!ctx.alive()) return;
  setRangeInfo(p, d.window_info || null, null, { unclassified: false });

  const head = pageHeader({ title: 'Countries', badge: 'Meta', actions: viewSwitch('countries', G, 'meta'),
    description: 'Meta spend and new customers by country. NC means new customer; ROAS means return on ad spend; CPA means cost per acquisition; CPM is cost per 1,000 impressions; CTR is click-through rate.' });
  const crumbs = h('div', { class: 'crumbrow' }, breadcrumbs([{ label: 'Meta', href: platformUrl('/', 'meta', G) }, { label: 'Countries' }]));
  if (!d.loaded) { ctx.mount(h('div', { class: 'page', 'data-level': 'countries' }, head, crumbs, emptyState(d.message))); return; }

  const t = createTable({ columns: COLUMNS, rows: d.rows, total: d.total, rowKey: (r) => r.code || r.country, tableClass: 'compact', testid: 'countries-table', emptyText: 'No countries in this window.' });
  const first = d.coverage.spend_first;
  const caption = 'NC revenue = each customer’s first Shopify order by ship country (all channels, so includes Google and organic buyers); spend from Meta’s country breakdown, from ' + fmt.day(first) + ', ' + String(first).slice(0, 4) + '.';
  const notes = (d.notes || []).map((n) => h('div', { class: 'note-line', 'data-testid': 'countries-note' }, n));
  ctx.mount(h('div', { class: 'page', 'data-level': 'countries' }, head, crumbs,
    h('div', { class: 'note-line', 'data-testid': 'countries-range' }, `Showing ${fmt.day(d.range.from)} to ${fmt.day(d.range.to)}, ${String(d.range.to).slice(0, 4)} (${fmt.int(d.range.days)} days, Pacific).`),
    ...notes,
    h('div', { class: 'tablehead' }, h('h2', { id: 'level-title' }, 'By country')),
    t.el,
    h('div', { class: 'note-line', 'data-testid': 'countries-caption' }, caption)));
}
