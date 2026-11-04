import { h, enc, fmt, href, apiWindow, globalParams, badge, empty, isNum, onCmpRefresh } from '../util.js';
import { api } from '../api.js';
import { createTable } from '../table.js';
import { columnsChips, legendLine } from '../components.js';
import { createTrendPanel, createPins } from '../trend.js';
import { metricColumns, trendColumn, pinColumn, flatTotal } from '../columns.js';
import { setRangeInfo, ensureStatus, windowInfoFallback } from '../controls.js';

export function tagBadge(product) {
  const mapped = product.tag_set === 'linemap';
  return badge(product.tag_label || (mapped ? 'Line-mapped tags' : 'Tracker tags — not line-mapped yet'), mapped ? 'tag-mapped' : 'tag-tracker', 'Which tags drive the hierarchy');
}

// % of spend across products when the API did not send it: relative to the Meta account total (total_all) when there is one
function addShares(list, ta) {
  const tot = ta ? ta.spend : list.reduce((a, r) => a + (r.m && r.m.spend || 0), 0);
  const totB = ta ? (ta.cmp && ta.cmp.spend) : list.reduce((a, r) => a + (r.m && r.m.cmp && r.m.cmp.spend || 0), 0);
  for (const r of list) {
    const m = r.m;
    if (!m) continue;
    // with an account total the share is always relative to it (the API's own share is relative to the tracker products only)
    if (ta || m.spend_share == null) m.spend_share = tot > 0 ? m.spend / tot : null;
    if (m.cmp) {
      if (ta || m.cmp.spend_share == null) m.cmp.spend_share = totB > 0 ? m.cmp.spend / totB : null;
      m.delta = m.delta || {};
      if (ta || m.delta.spend_share_pp == null) m.delta.spend_share_pp = isNum(m.spend_share) && isNum(m.cmp.spend_share) ? (m.spend_share - m.cmp.spend_share) * 100 : null;
    }
  }
}

export async function productsPage(ctx) {
  const { params: p, signal } = ctx;
  const G = globalParams(p);
  document.title = 'Products · Tag Explorer';
  const [data, st] = await Promise.all([api('/api/products', { ...apiWindow(p), envelope: 1 }, { signal }), ensureStatus(signal)]);
  if (!ctx.alive()) return;
  const list = Array.isArray(data) ? data : (data.rows || data.products || []);
  const um = !Array.isArray(data) && data.unmatched && data.unmatched.m;
  const ta = !Array.isArray(data) && data.total_all && data.total_all.m;
  addShares(list, ta);
  // /api/products is a bare array (no window_info): derive the range labels client-side
  let a = !Array.isArray(data) && data.window_info, b = !Array.isArray(data) && data.compare_info;
  if (!a) { const fb = windowInfoFallback(p, st); a = fb && fb.a; b = fb && fb.b; }
  const unc = list.some((r) => r.m && r.m.stage && r.m.stage.unclassified && r.m.stage.unclassified.spend > 0);
  setRangeInfo(p, a, b, { unclassified: unc });
  // Total row: the API's total.m when it sends one, else summed here with ratios recomputed from the sums
  const days = a && a.days, bdays = b && b.days;
  const sumStage = (st) => list.reduce((acc, r) => acc + (r.m && r.m.stage && r.m.stage[st] ? r.m.stage[st].spend || 0 : 0), 0);
  const testingSum = sumStage('testing'), scalingSum = sumStage('scaling'), uncSum = sumStage('unclassified');
  const tm = (!Array.isArray(data) && data.total && data.total.m) || {
    ...flatTotal(list.map((r) => r.m).filter(Boolean), { days, bdays }),
    unique_ads: list.reduce((acc, r) => acc + ((r.m && r.m.unique_ads) || 0), 0),
    live_now: list.reduce((acc, r) => acc + ((r.m && r.m.live_now) || 0), 0),
    stage: { testing: { spend: testingSum }, scaling: { spend: scalingSum }, unclassified: { spend: uncSum } },
    testing_pct: testingSum + scalingSum > 0 ? testingSum / (testingSum + scalingSum) : null,
  };
  if (ta && ta.spend > 0) {  // the tracker total is a share of the whole Meta account
    tm.spend_share = tm.spend / ta.spend;
    if (tm.cmp && ta.cmp && ta.cmp.spend > 0) {
      tm.cmp.spend_share = tm.cmp.spend / ta.cmp.spend;
      tm.delta = { ...(tm.delta || {}), spend_share_pp: (tm.spend_share - tm.cmp.spend_share) * 100 };
    }
  }
  const total = { m: tm, rows: list.reduce((acc, r) => acc + (r.rows || 0), 0) };
  const firstSlug = list[0] && list[0].slug;
  const extraTotals = [];
  if (um) {
    extraTotals.push({
      key: 'untracked', cls: 'extra untracked', total: { m: um, rows: null },
      label: h('span', null, 'Not on the tracker', h('a', { class: 'note-link', href: firstSlug ? href('/p/' + enc(firstSlug), G) : '#', title: 'Each product page lists its own unmatched Meta spend' }, 'see each product’s Not on the tracker panel')),
    });
  }
  if (ta) extraTotals.push({ key: 'all-spend', cls: 'extra all', total: { m: ta, rows: null }, label: 'All spend (Meta account total)' });
  const pins = createPins();
  let t = null;
  const panel = createTrendPanel({
    slug: null, p, pins, mode: 'landing', scopeLabel: 'All products', products: list, signal,
    onSparks: (map) => { for (const r of list) r.spark = map[r.slug]; if (t) t.refresh(); },
  });
  const cols = [
    {
      key: 'name', label: 'Product', sticky: true, sort: (r) => r.name,
      cell: (r) => h('div', { class: 'cell-prod' }, h('a', { href: href('/p/' + enc(r.slug), G), class: 'prodname' }, r.name), tagBadge(r)),
      total: () => (um || ta ? 'Total (tracker products)' : 'Total'),
    },
    pinColumn({ pins, keyOf: (r) => r.slug, labelOf: (r) => r.name, scopeOf: (r) => ({ slug: r.slug }) }),
    { key: '_ads', label: '', nosort: true, header: h('span', { class: 'muted small' }, 'Ads'), cell: (r) => h('a', { href: href('/p/' + enc(r.slug), { ...G, view: 'ads' }), class: 'btnlink small' }, 'Ads') },
    trendColumn(),
    { key: 'rows', label: 'Tracker rows', num: true, sort: (r) => r.rows, cell: (r) => fmt.int(r.rows), total: (tt) => (tt.rows == null ? '' : fmt.int(tt.rows)), title: 'Creative Tracker rows (every status)' },
    ...metricColumns(),
  ];
  t = createTable({
    columns: cols, rows: list, total, extraTotals, totalLabel: um || ta ? 'Total (tracker products)' : 'Total',
    rowKey: (r) => r.slug,
    rowHref: (r) => href('/p/' + enc(r.slug), G),
    emptyText: 'No products.',
    testid: 'products-table',
  });
  onCmpRefresh(() => t.refresh());
  ctx.mount(h('div', { class: 'page' },
    h('div', { class: 'pagehead' }, h('h1', 'Products')),
    legendLine(),
    panel.el,
    h('div', { class: 'tablehead' }, columnsChips()),
    list.length ? t.el : empty('No products with tracker rows or Meta spend.'),
  ));
  if (panel.state.collapsed) panel.sparksOnly(); else panel.load();
}
