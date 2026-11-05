// Marketing metrics: the totals strip above the trend chart. Meta and Google are the only spend platforms, so
//   Total spend      = Meta spend + Google spend,
//   Total NC revenue = the business's ACTUAL new-customer revenue (Upstack orders, each order once), over the days Meta, Google and the orders feed all cover,
//   Total NC ROAS    = Total NC revenue / spend of those same days.
// The platforms' own attributed new-customer revenue is shown beside the actual with the gap (overlap between platforms and orders no platform claims net out in it).
// With a Platform filter (or a Product / Campaign type filter, which belong to one platform) the strip shows THAT platform's own figures on the Numbers basis in force
// (its own reporting, Upstack new customers or Upstack all orders): its spend, revenue and ROAS, no blended total. Same three tiles, same layout: only the numbers and
// the label ('Total' | 'Meta' | 'Google') change. The same strip is built from a page's own totals on the Meta and Google pages (platformStrip).
import { h, fmt, isNum, apiWindow, srcView } from './util.js';
import { api, AuthError } from './api.js';

const money = (v) => fmt.money(v);
const signed = (v) => (isNum(v) ? (v < 0 ? '−' : '+') + fmt.money(Math.abs(v)) : '–');
const signedPct = (v) => (isNum(v) ? (v < 0 ? '−' : '+') + (Math.abs(v) * 100).toFixed(1) + '%' : '–');
const dayRange = (a, b) => (a === b ? fmt.day(a) : `${fmt.day(a)} – ${fmt.day(b)}`);

function tile(id, label, value, exact, sub, extra = []) {
  return h('div', { class: 'kpi', 'data-testid': 'kpi-' + id },
    h('div', { class: 'kpi-label' }, label),
    h('div', { class: 'kpi-value', 'data-testid': 'kpi-' + id + '-value', ...(exact != null ? { 'data-value': String(exact) } : {}) }, value),
    sub ? h('div', { class: 'kpi-sub' }, sub) : null,
    ...extra);
}

const COUNT_WORD = { own: 'purchases', nc: 'new customers', all: 'orders' };
const revWord = (l) => (l === 'Revenue' ? 'revenue' : l);

// the three tiles of ONE platform: spend, revenue and ROAS on the basis in force. t = { name, basis, numbers, labels, spend, window, m }, cmp = same shape or null
function platformTiles(t, cmp) {
  const m = t.m || {};
  const cm = (cmp && cmp.m) || {};
  const nm = t.name;
  const days = t.window && t.window.from ? dayRange(t.window.from, t.window.to) : '';
  return [
    tile('spend', `${nm} spend`, money(t.spend), t.spend, days || (t.note || ''), [deltaLine(t.spend, cmp && cmp.spend, 'money')]),
    tile('revenue', `${nm} ${revWord(t.labels.revenue)}`, m.revenue != null ? money(m.revenue) : '–', m.revenue, `${t.basis} · ${fmt.int(m.purchases)} ${COUNT_WORD[t.numbers] || 'purchases'}`, [deltaLine(m.revenue, cm.revenue, 'money')]),
    tile('roas', `${nm} ${t.labels.roas}`, m.roas != null ? fmt.roas(m.roas) + 'x' : '–', m.roas, `Revenue / spend · ${t.basis}${m.cpa != null ? ` · cost per ${COUNT_WORD[t.numbers] === 'orders' ? 'order' : 'purchase'} ${fmt.money2(m.cpa)}` : ''}`, [deltaLine(m.roas, cm.roas, 'roas')]),
  ];
}

function deltaLine(cur, prev, kind) {
  if (!isNum(cur) || !isNum(prev)) return null;
  const txt = kind === 'roas' ? fmt.roas(prev) + 'x' : money(prev);
  return h('div', { class: 'kpi-cmp muted small', 'data-testid': 'kpi-compare' }, `Window B: ${txt} · `, h('strong', null, prev ? fmt.pctChange((cur - prev) / prev) : '–'));
}

// the strip of one platform from the page's own totals (the Meta and Google pages reached through the Platform picker): tiles built exactly as the filtered All view builds
// them, from numbers the page already holds, so they tie to its table total by construction. t = { name, basis, numbers, labels, spend, m, window? }
export function platformStrip(t, cmp = null) {
  return h('section', { class: 'kpis', 'data-testid': 'totals-strip', 'data-view': t.key || t.name.toLowerCase(), 'aria-label': `${t.name} spend and ROAS` },
    h('div', { class: 'kpi-row' }, ...platformTiles(t, cmp)),
    h('div', { class: 'kpi-note muted small', 'data-testid': 'kpi-view-note' }, `Showing ${t.name} only, on ${t.basis}. Platform All shows the blended total of Meta and Google.`));
}

const LABELS = { own: { revenue: 'Revenue', roas: 'ROAS' }, nc: { revenue: 'NC revenue', roas: 'NC ROAS' }, all: { revenue: 'Revenue', roas: 'ROAS' } };
// the Meta and Google pages: `m` is the page's own total (spend, purchases, revenue, roas, cpa) on the page's Numbers basis; `key` meta | google, `name` the label
// ('Meta', 'Meta · Vita Bull', 'Google · Search'), `win` the window_info of the page (its days are said under the spend)
export function ownStrip(key, name, m, win = null) {
  const numbers = srcView.key === 'upstack' ? (srcView.basis === 'all' ? 'all' : 'nc') : 'own';
  const plat = key === 'google' ? 'Google' : 'Meta';
  const basis = numbers === 'own' ? `${plat}'s own reporting` : numbers === 'nc' ? 'Upstack · new customers' : 'Upstack · all orders';
  return platformStrip({ key, name, basis, numbers, labels: LABELS[numbers], spend: (m && m.spend) || 0, m: m || {}, window: win && win.from && win.to ? { from: win.from, to: win.to } : null });
}

// filt = { only, product, type }: the same filters as the chart and the table below it
export function totalsStrip({ p, signal, filt = {} }) {
  const el = h('section', { class: 'kpis', 'data-testid': 'totals-strip', 'aria-label': 'Total spend and total NC ROAS' }, h('div', { class: 'kpi-loading muted small' }, 'Loading totals…'));

  function paint(d) {
    const t = d.total;
    if (d.view && d.view !== 'all') {
      el.dataset.view = d.view;
      el.replaceChildren(...[h('div', { class: 'kpi-row' }, ...platformTiles(t, d.compare || null)),
        h('div', { class: 'kpi-note muted small', 'data-testid': 'kpi-view-note' }, `Showing ${t.name} only, on ${t.basis}. Platform All shows the blended total of Meta and Google.`),
        ...(d.notes || []).map((n) => h('div', { class: 'kpi-note muted small', 'data-testid': 'kpi-note' }, n))].filter(Boolean));
      return;
    }
    el.dataset.view = 'all';
    const nc = t.nc || {};
    const cmp = d.compare || null;
    const parts = [];
    const platSub = [t.meta_spend ? `Meta ${money(t.meta_spend)} · ${fmt.share(t.meta_share)}` : null, t.google_spend ? `Google ${money(t.google_spend)} · ${fmt.share(t.google_share)}` : null].filter(Boolean).join('   ');
    parts.push(tile('spend', 'Total spend', money(t.spend), t.spend, platSub || 'No spend in this window', [deltaLine(t.spend, cmp && cmp.spend, 'money')]));
    if (nc.available) {
      const a = nc.attributed || {};
      const gapTxt = a.gap != null ? `gap ${signed(a.gap)} (${signedPct(a.gap_pct)})` : null;
      parts.push(tile('revenue', 'Total NC revenue', money(nc.rev), nc.rev,
        `Actual new-customer orders, each counted once · ${dayRange(nc.from, nc.to)}`,
        [a.sum != null ? h('div', { class: 'kpi-attr small', 'data-testid': 'kpi-attributed',
          title: 'Meta and Google each credit the orders they touched, so the same order can be credited twice (overlap), and some orders are credited to neither (unattributed). The gap is the net of both.' },
          h('span', { class: 'muted' }, 'Platforms attribute '), h('strong', null, money(a.sum)), h('span', { class: 'muted' }, ` (Meta ${money(a.meta)} + Google ${money(a.google)}) · ${gapTxt}, overlap net of unattributed`)) : null,
        deltaLine(nc.rev, cmp && cmp.nc && cmp.nc.rev, 'money')]));
      parts.push(tile('roas', 'Total NC ROAS', fmt.roas(nc.roas) + 'x', nc.roas,
        `NC revenue / spend of the same ${fmt.int(nc.days)} day${nc.days === 1 ? '' : 's'} (${money(nc.spend)})`,
        [nc.partial ? h('div', { class: 'kpi-partial muted small', 'data-testid': 'kpi-partial' }, nc.partial) : null, deltaLine(nc.roas, cmp && cmp.nc && cmp.nc.roas, 'roas')]));
    }
    el.replaceChildren(...[h('div', { class: 'kpi-row' }, ...parts),
      !nc.available && nc.reason ? h('div', { class: 'kpi-note muted small', 'data-testid': 'kpi-roas-hidden' }, nc.reason) : null,
      ...(d.notes || []).map((n) => h('div', { class: 'kpi-note muted small', 'data-testid': 'kpi-note' }, n))].filter(Boolean));
  }

  return {
    el,
    async load() {
      try {
        paint(await api('/api/platforms/kpis', { ...apiWindow(p), ...filt }, { signal }));
      } catch (e) {
        if (e instanceof AuthError || (e && e.name === 'AbortError')) return;
        el.replaceChildren(h('div', { class: 'kpi-note muted small' }, 'Totals failed to load: ' + (e && e.message ? e.message : e)));
      }
    },
  };
}
