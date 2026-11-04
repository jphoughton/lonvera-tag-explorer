import { h, enc, fmt, href, apiWindow, globalParams, badge, empty, isNum, onCmpRefresh } from '../util.js';
import { api } from '../api.js';
import { createTable } from '../table.js';
import { columnsChips } from '../components.js';
import { metricColumns } from '../columns.js';
import { setRangeInfo, ensureStatus, windowInfoFallback } from '../controls.js';

export function tagBadge(product) {
  const mapped = product.tag_set === 'linemap';
  return badge(product.tag_label || (mapped ? 'Line-mapped tags' : 'Tracker tags — not line-mapped yet'), mapped ? 'tag-mapped' : 'tag-tracker', 'Which tags drive the hierarchy');
}

// % of spend across products when the API did not send it (the list has no total row)
function addShares(list) {
  const tot = list.reduce((a, r) => a + (r.m && r.m.spend || 0), 0);
  const totB = list.reduce((a, r) => a + (r.m && r.m.cmp && r.m.cmp.spend || 0), 0);
  for (const r of list) {
    const m = r.m;
    if (!m) continue;
    if (m.spend_share == null) m.spend_share = tot > 0 ? m.spend / tot : null;
    if (m.cmp) {
      if (m.cmp.spend_share == null) m.cmp.spend_share = totB > 0 ? m.cmp.spend / totB : null;
      m.delta = m.delta || {};
      if (m.delta.spend_share_pp == null) m.delta.spend_share_pp = isNum(m.spend_share) && isNum(m.cmp.spend_share) ? (m.spend_share - m.cmp.spend_share) * 100 : null;
    }
  }
}

export async function productsPage(ctx) {
  const { params: p, signal } = ctx;
  const G = globalParams(p);
  document.title = 'Products · Tag Explorer';
  const [data, st] = await Promise.all([api('/api/products', apiWindow(p), { signal }), ensureStatus(signal)]);
  if (!ctx.alive()) return;
  const list = Array.isArray(data) ? data : (data.products || []);
  addShares(list);
  // /api/products is a bare array (no window_info): derive the range labels client-side
  let a = !Array.isArray(data) && data.window_info, b = !Array.isArray(data) && data.compare_info;
  if (!a) { const fb = windowInfoFallback(p, st); a = fb && fb.a; b = fb && fb.b; }
  const unc = list.some((r) => r.m && r.m.stage && r.m.stage.unclassified && r.m.stage.unclassified.spend > 0);
  setRangeInfo(p, a, b, { unclassified: unc });
  const cols = [
    {
      key: 'name', label: 'Product', sticky: true, sort: (r) => r.name,
      cell: (r) => h('div', { class: 'cell-prod' }, h('a', { href: href('/p/' + enc(r.slug), G), class: 'prodname' }, r.name), tagBadge(r)),
      total: () => '',
    },
    { key: '_ads', label: '', nosort: true, header: h('span', { class: 'muted small' }, 'Ads'), cell: (r) => h('a', { href: href('/p/' + enc(r.slug), { ...G, view: 'ads' }), class: 'btnlink small' }, 'Ads') },
    { key: 'rows', label: 'Tracker rows', num: true, sort: (r) => r.rows, cell: (r) => fmt.int(r.rows), title: 'Creative Tracker rows (every status)' },
    ...metricColumns(),
  ];
  const t = createTable({
    columns: cols, rows: list,
    rowKey: (r) => r.slug,
    rowHref: (r) => href('/p/' + enc(r.slug), G),
    emptyText: 'No products.',
    testid: 'products-table',
  });
  onCmpRefresh(() => t.refresh());
  ctx.mount(h('div', { class: 'page' },
    h('div', { class: 'pagehead' }, h('h1', 'Products')),
    h('div', { class: 'tablehead' }, columnsChips()),
    list.length ? t.el : empty('No products with tracker rows or Meta spend.'),
  ));
}
