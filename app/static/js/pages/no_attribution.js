// Lonvera, Meta, view "No attribution": per product, Meta spend beside the business's actual new-customer revenue (every new customer's first order, all channels).
// Nothing here is attributed to an ad: no Meta or Upstack purchases, revenue, ROAS or CPA. See app/no_attribution.py.
import { h, enc, fmt, href, isNum, globalParams } from '../util.js';
import { api } from '../api.js';
import { createTable } from '../table.js';
import { setRangeInfo, ensureStatus } from '../controls.js';
import { pageHeader } from '../ui/pagehead.js';
import { viewSwitch } from '../components.js';

const NOTE = 'Spend is Meta spend by product. Revenue is every new customer’s first order for that product, from all channels, not just Meta.';
const CLASSIFY = 'Spend is classified by campaign name, then ad set name, so it includes ads that are not on the tracker and adds up to the whole Meta account. Revenue includes tax and is before refunds.';
const times = (v) => (isNum(v) ? fmt.roas(v) + 'x' : '–');
const col = (key, label, cell, title) => ({ key, label, num: true, sort: (r) => r[key], cell: (r) => cell(r[key]), total: (t) => cell(t[key]), title });

// how far the products' revenue is from the business's total new-customer revenue over the same days (the footer line itself is set by controls.js)
function tieLine(d) {
  const c = d.check;
  if (!c || !isNum(c.diff_pct)) return null;
  return h('div', { class: 'note-line muted small', 'data-testid': 'noattr-tie' }, `The products add up to ${fmt.money(c.products_nc_revenue)} of ${fmt.money(c.orders_daily_nc_revenue)} total new-customer revenue over these days (${(c.diff_pct * 100).toFixed(1)}%).`);
}

export async function noAttributionPage(ctx) {
  const { params: p, signal } = ctx;
  const G = globalParams(p);
  document.title = 'No attribution · Lonvera';
  const win = p.from && p.to ? { from: p.from, to: p.to } : p.window && p.window !== 'launch' ? { window: p.window } : {};
  const [d] = await Promise.all([api('/api/no-attribution/products', win, { signal }), ensureStatus(signal)]);          // the status load rewrites the footer, so wait for it before setting ours
  if (!ctx.alive()) return;
  const head = pageHeader({ title: 'Meta', badge: 'No attribution', actions: viewSwitch('noattr', G, 'meta'),
    description: 'Total Meta spend and total new-customer revenue for each product. ROAS means return on ad spend; CPA means cost per acquisition; AOV means average order value.', testid: 'noattr-head' });
  if (d.loaded === false) {
    setRangeInfo(p, null, null);
    ctx.mount(h('div', { class: 'page', 'data-level': 'noattr' }, head, h('div', { class: 'empty-range', role: 'status', 'data-testid': 'noattr-empty' }, d.message)));
    return;
  }
  setRangeInfo(p, d.window_info, null, { unclassified: false });
  const cols = [
    { key: 'name', label: 'Product', sticky: true, sort: (r) => r.name, total: () => 'Total',
      cell: (r) => (r.slug ? h('a', { href: href('/p/' + enc(r.slug), G), class: 'prodname' }, r.name) : h('span', { class: 'prodname' }, r.name)) },
    col('spend', 'Spend', fmt.money, 'Meta spend on this product'),
    col('spend_share', '% of spend', fmt.share),
    col('nc_revenue', 'New-customer revenue', fmt.money, 'First-order revenue of new customers, all channels'),
    col('nc_revenue_share', '% of revenue', fmt.share),
    col('nc_orders', 'New customers', fmt.int, 'New customers whose first order was mostly this product'),
    col('nc_roas', 'New-customer ROAS', times, 'New-customer revenue / Meta spend'),
    col('nc_cpa', 'New-customer CPA', fmt.money2, 'Meta spend / new customers'),
    col('aov', 'AOV', fmt.money2, 'New-customer revenue / new customers'),
    col('link_clicks', 'Link clicks', fmt.int, 'Meta link clicks'),
    col('cpc', 'CPC', fmt.money2, 'Spend / link clicks'),
    col('conv_rate', 'Conv rate', fmt.rate, 'New customers / link clicks'),
  ];
  const t = createTable({ columns: cols, rows: d.rows, total: d.total, rowKey: (r) => r.key, tableClass: 'compact', testid: 'noattr-table' });
  ctx.mount(h('div', { class: 'page', 'data-level': 'noattr' }, head,
    h('div', { class: 'note-line', 'data-testid': 'noattr-note' }, NOTE),
    h('div', { class: 'note-line muted small', 'data-testid': 'noattr-classify' }, CLASSIFY),
    t.el, tieLine(d)));
}
