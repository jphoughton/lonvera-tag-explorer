import { h, enc, fmt, href, apiWindow, globalParams, badge, empty } from '../util.js';
import { api } from '../api.js';
import { createTable } from '../table.js';
import { metricColumns } from '../columns.js';
import { setWindowInfo, ensureStatus, windowInfoFallback } from '../controls.js';

export function tagBadge(product) {
  const mapped = product.tag_set === 'linemap';
  return badge(product.tag_label || (mapped ? 'Line-mapped tags' : 'Tracker tags — not line-mapped yet'), mapped ? 'tag-mapped' : 'tag-tracker', 'Which tags drive the hierarchy');
}

export async function productsPage(ctx) {
  const { params: p, signal } = ctx;
  const G = globalParams(p);
  document.title = 'Products · Tag Explorer';
  const [data, st] = await Promise.all([api('/api/products', apiWindow(p), { signal }), ensureStatus(signal)]);
  if (!ctx.alive()) return;
  const list = Array.isArray(data) ? data : (data.products || []);
  // /api/products is a bare array (no window_info): derive the range label client-side
  setWindowInfo((!Array.isArray(data) && data.window_info) || windowInfoFallback(p, st));
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
  ctx.mount(h('div', { class: 'page' },
    h('div', { class: 'pagehead' }, h('h1', 'Products')),
    list.length ? t.el : empty('No products with tracker rows or Meta spend.'),
  ));
}
