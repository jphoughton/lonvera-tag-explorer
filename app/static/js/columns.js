// Shared metric columns. The same 9 sheet metrics + % of spend + active ads + stage split appear at every level.
// With comparison on, Spend / % of spend / Purchases / Revenue / ROAS / CPA cells get a second line "vs <B> ▲12%".
import { h, fmt, isNum, cmpView, stageSet, currentParams } from './util.js';

// Testing $ / Scaling $ / Testing % only make sense when both stages are included in the stage filter
const STAGE_COLS = new Set(['testing_spend', 'scaling_spend', 'testing_pct']);
export function bothStagesIncluded() {
  const s = stageSet(currentParams());
  return s.has('testing') && s.has('scaling');
}

const PERDAY = new Set(['spend', 'purchases', 'revenue']);
const KINDS = { int: fmt.int, money: fmt.money, roas: fmt.roas, pct: fmt.pct, share: fmt.share };
// good direction of a change in A vs B
const DIR = { spend: 'neutral', spend_share: 'neutral', purchases: 'up', revenue: 'up', roas: 'up', cpa: 'down' };

const DEFS = [
  ['unique_ads', 'Unique ads', 'int', 'Distinct ad families in this group (every tracker status)', 'sep'],
  ['live_now', 'Live now', 'int', 'Families with spend on the last or second-to-last data day'],
  ['spend', 'Spend', 'money', 'Meta-reported spend in the selected window'],
  ['spend_share', '% of spend', 'share', 'Share of the total spend of this table (a child’s share adds up to its parent’s)'],
  ['purchases', 'Purchases', 'int', 'Meta-reported purchases in the selected window'],
  ['revenue', 'Revenue', 'money', 'Meta-reported conversion value in the selected window'],
  ['roas', 'ROAS', 'roas', 'Revenue / spend'],
  ['cpa', 'CPA', 'money', 'Spend / purchases'],
  ['spend_7d', 'Spend 7d', 'money', 'Spend over the last 7 days of data (ignores the window)'],
  ['cpa_7d', 'CPA 7d', 'money', 'CPA over the last 7 days of data (ignores the window)'],
  ['active_ads', 'Active ads', 'int', 'Families with spend > 0 in the selected window'],
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
  const v = m.delta[valueKey(key) + '_pct'];
  return v == null ? null : v;
}

function deltaEl(key, pct) {
  if (!isNum(pct)) return null;
  const text = key === 'spend_share' ? fmt.pp(pct) : fmt.pctChange(pct);
  const dir = DIR[key] || 'neutral';
  let cls = 'neutral';
  if (pct !== 0 && dir !== 'neutral') {
    const up = pct > 0;
    cls = (dir === 'up') === up ? 'good' : 'bad';
  }
  return h('span', { class: 'delta ' + cls }, text);
}

export function metricCell(m, key, kind) {
  if (!m) return '';
  const vk = key === 'spend_share' ? key : valueKey(key);
  const f = fmtFor(kind, vk);
  const a = f(get(m, vk));
  if (!cmpView.on || !m.cmp || !(key in DIR)) return a;
  const bVal = m.cmp[vk];
  return h('div', { class: 'cmpcell' },
    h('div', { class: 'a' }, a),
    h('div', { class: 'b' }, 'vs ', f(bVal), ' ', deltaEl(key, deltaPct(m, key))));
}

// opts.getM: row -> metrics object (default r.m); opts.getTotalM: total -> metrics object (default t.m)
// opts.keys: subset of metric keys; opts.shareLabel: header text for the share column
export function metricColumns({ getM = (r) => r.m, getTotalM = (t) => t && t.m, keys = null, shareLabel = '% of spend' } = {}) {
  return DEFS.filter((d) => !keys || keys.includes(d[0])).map(([key, label, kind, title, cls]) => {
    const comparable = key in DIR;
    const val = (r) => { const m = getM(r); return m ? get(m, key === 'spend_share' ? key : valueKey(key)) : null; };
    return {
      key,
      label: () => {
        const base = key === 'spend_share' ? shareLabel : label;
        return cmpView.perDay && PERDAY.has(key) ? base + '/day' : base;
      },
      num: true, title, cls: cls || '',
      hidden: STAGE_COLS.has(key) ? () => !bothStagesIncluded() : null,
      sort: val,
      deltaSort: comparable ? (r) => deltaPct(getM(r), key) : null,
      cell: (r) => metricCell(getM(r), key, kind),
      cellTitle: kind === 'money' ? (r) => { const v = val(r); return isNum(v) ? fmt.money2(v) : null; } : null,
      total: (t) => metricCell(getTotalM(t), key, kind),
    };
  });
}
export const FLAT_KEYS = ['spend', 'spend_share', 'purchases', 'revenue', 'roas', 'cpa', 'spend_7d'];

// ---- helpers for client-side totals of flat rows ----
const sumK = (rows, f) => rows.reduce((a, r) => a + (f(r) || 0), 0);
export function pctDelta(a, b) { return isNum(a) && isNum(b) && b !== 0 ? (a - b) / b : null; }
// totals (with compare) from flat metric rows, recomputed from sums
export function flatTotal(rows, { days, bdays } = {}) {
  const t = { spend: sumK(rows, (r) => r.spend), purchases: sumK(rows, (r) => r.purchases), revenue: sumK(rows, (r) => r.revenue), spend_7d: sumK(rows, (r) => r.spend_7d) };
  t.roas = t.spend > 0 ? t.revenue / t.spend : null;
  t.cpa = t.purchases > 0 ? t.spend / t.purchases : null;
  t.spend_share = 1;
  if (days) { t.spend_per_day = t.spend / days; t.purchases_per_day = t.purchases / days; t.revenue_per_day = t.revenue / days; }
  if (rows.length && rows.every((r) => r.cmp)) {
    const b = { spend: sumK(rows, (r) => r.cmp.spend), purchases: sumK(rows, (r) => r.cmp.purchases), revenue: sumK(rows, (r) => r.cmp.revenue) };
    b.roas = b.spend > 0 ? b.revenue / b.spend : null;
    b.cpa = b.purchases > 0 ? b.spend / b.purchases : null;
    b.spend_share = 1;
    const d = {};
    for (const k of ['spend', 'purchases', 'revenue', 'roas', 'cpa']) {
      d[k] = isNum(t[k]) && isNum(b[k]) ? t[k] - b[k] : null;
      d[k + '_pct'] = pctDelta(t[k], b[k]);
    }
    d.spend_share_pp = 0;
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
