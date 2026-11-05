// Marketing metrics, All views: the totals strip above the trend chart. Meta and Google are the only spend platforms, so
//   Total spend      = Meta spend + Google spend,
//   Total NC revenue = the business's ACTUAL new-customer revenue (Upstack orders, each order once), over the days Meta, Google and the orders feed all cover,
//   Total NC ROAS    = Total NC revenue / spend of those same days.
// The platforms' own attributed new-customer revenue is shown beside the actual with the gap (overlap between platforms and orders no platform claims net out in it).
// With a Product, Campaign type or Platform filter the ratio is hidden (Upstack orders have no product or platform dimension) and the strip says so; spend stays.
import { h, fmt, isNum, apiWindow } from './util.js';
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

function deltaLine(cur, prev, kind) {
  if (!isNum(cur) || !isNum(prev)) return null;
  const txt = kind === 'roas' ? fmt.roas(prev) + 'x' : money(prev);
  return h('div', { class: 'kpi-cmp muted small', 'data-testid': 'kpi-compare' }, `Window B: ${txt} · `, h('strong', null, prev ? fmt.pctChange((cur - prev) / prev) : '–'));
}

// filt = { only, product, type }: the same filters as the chart and the table below it
export function totalsStrip({ p, signal, filt = {} }) {
  const el = h('section', { class: 'kpis', 'data-testid': 'totals-strip', 'aria-label': 'Total spend and total NC ROAS' }, h('div', { class: 'kpi-loading muted small' }, 'Loading totals…'));

  function paint(d) {
    const t = d.total;
    const nc = t.nc || {};
    const cmp = d.compare || null;
    const parts = [];
    const platSub = [t.meta_spend ? `Meta ${money(t.meta_spend)} · ${fmt.share(t.meta_share)}` : null, t.google_spend ? `Google ${money(t.google_spend)} · ${fmt.share(t.google_share)}` : null].filter(Boolean).join('   ');
    parts.push(tile('spend', 'Total spend', money(t.spend), t.spend, platSub || 'No spend in this window', [deltaLine(t.spend, cmp && cmp.spend, 'money')]));
    if (nc.available) {
      const a = nc.attributed || {};
      const gapTxt = a.gap != null ? `gap ${signed(a.gap)} (${signedPct(a.gap_pct)})` : null;
      parts.push(tile('nc-rev', 'Total NC revenue', money(nc.rev), nc.rev,
        `Actual new-customer orders, each counted once · ${dayRange(nc.from, nc.to)}`,
        [a.sum != null ? h('div', { class: 'kpi-attr small', 'data-testid': 'kpi-attributed',
          title: 'Meta and Google each credit the orders they touched, so the same order can be credited twice (overlap), and some orders are credited to neither (unattributed). The gap is the net of both.' },
          h('span', { class: 'muted' }, 'Platforms attribute '), h('strong', null, money(a.sum)), h('span', { class: 'muted' }, ` (Meta ${money(a.meta)} + Google ${money(a.google)}) · ${gapTxt}, overlap net of unattributed`)) : null,
        deltaLine(nc.rev, cmp && cmp.nc && cmp.nc.rev, 'money')]));
      parts.push(tile('nc-roas', 'Total NC ROAS', fmt.roas(nc.roas) + 'x', nc.roas,
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
