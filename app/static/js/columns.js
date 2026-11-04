// Shared metric columns. The same metrics appear at every level: Spend, % of spend, traffic (impressions, link clicks, CTR, CPC), purchases, revenue, ROAS, CPA.
// With comparison on, Spend / % of spend / Purchases / Revenue / ROAS / CPA cells get a second line "vs <B> ▲12%".
import { h, fmt, isNum, cmpView, stageSet, currentParams, colPref } from './util.js';
import { sparkSvg, sparkTitle, MAX_PINS, PIN_HINT } from './trend.js';

// Testing $ / Scaling $ / Testing % only make sense when both stages are included in the stage filter
const STAGE_COLS = new Set(['testing_spend', 'scaling_spend', 'testing_pct']);
export function bothStagesIncluded() {
  const s = stageSet(currentParams());
  return s.has('testing') && s.has('scaling');
}

const PERDAY = new Set(['spend', 'purchases', 'revenue']);
const KINDS = { int: fmt.int, money: fmt.money, money2: fmt.money2, roas: fmt.roas, pct: fmt.pct, share: fmt.share, rate: fmt.rate };
// good direction of a change in A vs B
const DIR = {
  spend: 'neutral', spend_share: 'neutral', impressions: 'neutral', link_clicks: 'neutral', ctr: 'up', cpc: 'down', cpm: 'down', conv_rate: 'up',
  purchases: 'up', revenue: 'up', roas: 'up', cpa: 'down',
};
// ratios whose change is shown in percentage points (the API sends *_pp, or A - B as a fraction)
const PP_KEYS = new Set(['spend_share', 'ctr', 'conv_rate']);

const DEFS = [
  ['unique_ads', 'Creatives', 'int', 'Distinct creatives in this group (every tracker status)', 'sep'],
  ['live_now', 'Live now', 'int', 'Creatives that spent in the last 2 days'],
  ['spend', 'Spend', 'money', 'Meta-reported spend in the selected window'],
  ['spend_share', '% of spend', 'share', 'Share of the total spend of this table (a child’s share adds up to its parent’s)'],
  ['impressions', 'Impressions', 'int', 'Meta impressions in the selected window (blank where Meta data is not available)', '', 'traffic'],
  ['link_clicks', 'Link clicks', 'int', 'Meta link clicks in the selected window', '', 'traffic'],
  ['ctr', 'CTR', 'rate', 'Link CTR = link clicks / impressions', '', 'traffic'],
  ['cpc', 'CPC', 'money2', 'Cost per link click = spend / link clicks', '', 'traffic'],
  ['cpm', 'CPM', 'money2', 'Cost per 1,000 impressions', '', 'advanced'],
  ['conv_rate', 'Conv rate', 'rate', 'Purchases / link clicks', '', 'advanced'],
  ['purchases', 'Purchases', 'int', 'Meta-reported purchases in the selected window'],
  ['revenue', 'Revenue', 'money', 'Meta-reported conversion value in the selected window'],
  ['roas', 'ROAS', 'roas', 'Revenue / spend'],
  ['cpa', 'CPA', 'money', 'Spend / purchases'],
  ['testing_spend', 'Testing $', 'money', 'Spend in Testing campaigns (selected window)', 'sep'],
  ['scaling_spend', 'Scaling $', 'money', 'Spend in Scaling campaigns (selected window)'],
  ['testing_pct', 'Testing %', 'pct', 'Testing share of classified spend'],
];

function get(m, key) {
  if (!m) return null;
  switch (key) {
    case 'testing_spend': return m.stage && m.stage.testing ? m.stage.testing.spend : null;
    case 'scaling_spend': return m.stage && m.stage.scaling ? m.stage.scaling.spend : null;
    default: return m[key];
  }
}

// key actually shown/sorted: per-day variants for spend, purchases, revenue when "Per day" is on
const valueKey = (key) => (cmpView.perDay && PERDAY.has(key) ? key + '_per_day' : key);

function fmtFor(kind, key) {
  if (key === 'purchases_per_day') return fmt.dec1;
  return KINDS[kind];
}

export function deltaPct(m, key) {
  if (!m || !m.delta) return null;
  if (key === 'spend_share') return m.delta.spend_share_pp;
  if (PP_KEYS.has(key)) {
    if (isNum(m.delta[key + '_pp'])) return m.delta[key + '_pp'];
    return isNum(m[key]) && m.cmp && isNum(m.cmp[key]) ? (m[key] - m.cmp[key]) * 100 : null;
  }
  const v = m.delta[valueKey(key) + '_pct'];
  if (v != null) return v;
  const a = m[valueKey(key)], b = m.cmp ? m.cmp[valueKey(key)] : null;
  return isNum(a) && isNum(b) && b !== 0 ? (a - b) / b : null;
}

function deltaEl(key, pct) {
  if (!isNum(pct)) return null;
  const text = PP_KEYS.has(key) ? fmt.pp(pct, key === 'spend_share' ? 1 : 2) : fmt.pctChange(pct);
  const dir = DIR[key] || 'neutral';
  let cls = 'neutral';
  if (pct !== 0 && dir !== 'neutral') {
    const up = pct > 0;
    cls = (dir === 'up') === up ? 'good' : 'bad';
  }
  return h('span', { class: 'delta ' + cls }, text);
}

// Meta has no impressions for some days (they stay unknown): 0 impressions next to spend / clicks means "not available", not zero
const impressionsUnknown = (o) => !!o && o.impressions === 0 && ((o.spend || 0) > 0 || (o.link_clicks || 0) > 0);

export function metricCell(m, key, kind) {
  if (!m) return '';
  const vk = key === 'spend_share' ? key : valueKey(key);
  const f = fmtFor(kind, vk);
  const show = (o) => (key === 'impressions' && impressionsUnknown(o) ? '–' : f(get(o, vk)));
  const a = show(m);
  if (!cmpView.on || !m.cmp || !(key in DIR)) return a;
  return h('div', { class: 'cmpcell' },
    h('div', { class: 'a' }, a),
    h('div', { class: 'b' }, 'vs ', key === 'impressions' && impressionsUnknown(m.cmp) ? '–' : f(m.cmp[vk]), ' ', deltaEl(key, deltaPct(m, key))));
}

// opts.getM: row -> metrics object (default r.m); opts.getTotalM: total -> metrics object (default t.m)
// opts.keys: subset of metric keys; opts.shareLabel: header text for the share column
export function metricColumns({ getM = (r) => r.m, getTotalM = (t) => t && t.m, keys = null, shareLabel = '% of spend' } = {}) {
  return DEFS.filter((d) => !keys || keys.includes(d[0])).map(([key, label, kind, title, cls, group]) => {
    const comparable = key in DIR;
    const val = (r) => { const m = getM(r); return m ? get(m, key === 'spend_share' ? key : valueKey(key)) : null; };
    return {
      key,
      label: () => {
        const base = key === 'spend_share' ? shareLabel : label;
        return cmpView.perDay && PERDAY.has(key) ? base + '/day' : base;
      },
      num: true, title, cls: cls || '',
      hidden: STAGE_COLS.has(key) ? () => !bothStagesIncluded() : (group ? () => !colPref(group) : null),
      sort: val,
      deltaSort: comparable ? (r) => deltaPct(getM(r), key) : null,
      cell: (r) => metricCell(getM(r), key, kind),
      cellTitle: (kind === 'money' || kind === 'money2') ? (r) => { const v = val(r); return isNum(v) ? fmt.money2(v) : null; } : null,
      total: (t) => metricCell(getTotalM(t), key, kind),
    };
  });
}
export const FLAT_KEYS = ['spend', 'spend_share', 'impressions', 'link_clicks', 'ctr', 'cpc', 'cpm', 'conv_rate', 'purchases', 'revenue', 'roas', 'cpa'];

// ---- helpers for client-side totals of flat rows ----
const sumK = (rows, f) => rows.reduce((a, r) => a + (f(r) || 0), 0);
export function pctDelta(a, b) { return isNum(a) && isNum(b) && b !== 0 ? (a - b) / b : null; }
function trafficTotals(rows, get, spend, purchases) {
  const imp = sumK(rows, (r) => get(r).impressions);
  const clk = sumK(rows, (r) => get(r).link_clicks);
  const clkTr = sumK(rows, (r) => (get(r).ctr || 0) * (get(r).impressions || 0));
  const spTr = sumK(rows, (r) => (get(r).cpm || 0) * (get(r).impressions || 0) / 1000);
  return {
    impressions: imp || null, link_clicks: clk,
    ctr: imp ? clkTr / imp : null, cpc: clk ? spend / clk : null,
    cpm: imp ? (spTr / imp) * 1000 : null, conv_rate: clk ? purchases / clk : null,
  };
}

// totals (with compare) from flat metric rows, recomputed from sums
export function flatTotal(rows, { days, bdays } = {}) {
  const t = { spend: sumK(rows, (r) => r.spend), purchases: sumK(rows, (r) => r.purchases), revenue: sumK(rows, (r) => r.revenue) };
  t.roas = t.spend > 0 ? t.revenue / t.spend : null;
  t.cpa = t.purchases > 0 ? t.spend / t.purchases : null;
  Object.assign(t, trafficTotals(rows, (r) => r, t.spend, t.purchases));
  t.spend_share = 1;
  if (days) { t.spend_per_day = t.spend / days; t.purchases_per_day = t.purchases / days; t.revenue_per_day = t.revenue / days; }
  if (rows.length && rows.every((r) => r.cmp)) {
    const b = { spend: sumK(rows, (r) => r.cmp.spend), purchases: sumK(rows, (r) => r.cmp.purchases), revenue: sumK(rows, (r) => r.cmp.revenue) };
    b.roas = b.spend > 0 ? b.revenue / b.spend : null;
    b.cpa = b.purchases > 0 ? b.spend / b.purchases : null;
    Object.assign(b, trafficTotals(rows, (r) => r.cmp, b.spend, b.purchases));
    b.spend_share = 1;
    const d = {};
    for (const k of ['spend', 'purchases', 'revenue', 'roas', 'cpa']) {
      d[k] = isNum(t[k]) && isNum(b[k]) ? t[k] - b[k] : null;
      d[k + '_pct'] = pctDelta(t[k], b[k]);
    }
    d.spend_share_pp = 0;
    for (const k of ['impressions', 'link_clicks', 'ctr', 'cpc', 'cpm', 'conv_rate']) {
      d[k] = isNum(t[k]) && isNum(b[k]) ? t[k] - b[k] : null;
      d[k + '_pct'] = pctDelta(t[k], b[k]);
    }
    d.ctr_pp = d.ctr == null ? null : d.ctr * 100;
    d.conv_rate_pp = d.conv_rate == null ? null : d.conv_rate * 100;
    if (days && bdays) {
      for (const k of ['spend', 'purchases', 'revenue']) {
        b[k + '_per_day'] = b[k] / bdays;
        d[k + '_per_day_pct'] = pctDelta(t[k] / days, b[k] / bdays);
      }
    }
    t.cmp = b; t.delta = d;
  }
  return t;
}

// flat totals from the API may omit the share fields: a total is 100% of itself
export function fixTotal(t) {
  if (!t) return t;
  const o = { ...t };
  if (o.spend_share == null) o.spend_share = 1;
  if (o.cmp) { o.cmp = { ...o.cmp }; if (o.cmp.spend_share == null) o.cmp.spend_share = 1; }
  if (o.delta) { o.delta = { ...o.delta }; if (o.delta.spend_share_pp == null) o.delta.spend_share_pp = 0; }
  return o;
}

// ---- Trend sparkline column (follows the trend panel metric) and the "Chart" pin column ----
export function trendColumn({ getSpark = (r) => r.spark } = {}) {
  return {
    key: 'trend', label: 'Trend', nosort: true, cls: 'trendcol', total: () => '',
    title: 'Last 30 days of the metric selected in the trend panel',
    hidden: () => !colPref('trend'),
    cell: (r) => {
      const sp = getSpark(r);
      if (!Array.isArray(sp)) return '';
      return sparkSvg(sp, { title: sparkTitle(sp) });
    },
  };
}

export function pinColumn({ pins, canPin = () => true, keyOf, labelOf, scopeOf }) {
  return {
    key: '_chart', label: 'Chart', nosort: true, cls: 'pincol', total: () => '',
    title: `Add this row to the trend chart (max ${MAX_PINS})`,
    cell: (r) => {
      if (!canPin(r)) return '';
      const key = keyOf(r);
      const on = pins.has(key);
      const dis = pins.locked() || (!on && pins.full());
      const cb = h('input', { type: 'checkbox', class: 'pin-cb', 'data-key': key, checked: on, disabled: dis, 'aria-label': 'Show ' + labelOf(r) + ' in the chart', title: pins.locked() ? PIN_HINT : (dis ? `Max ${MAX_PINS} rows in the chart` : 'Show in the trend chart') });
      cb.addEventListener('change', () => { if (!pins.toggle(key, labelOf(r), scopeOf(r))) cb.checked = false; });
      return cb;
    },
  };
}
