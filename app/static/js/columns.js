// Shared metric columns. The same 9 sheet metrics + active ads + stage split appear at every level.
import { fmt, isNum } from './util.js';

const KINDS = { int: fmt.int, money: fmt.money, roas: fmt.roas, pct: fmt.pct };

const DEFS = [
  ['unique_ads', 'Unique ads', (m) => m.unique_ads, 'int', 'Distinct ad families in this group (every tracker status)', 'sep'],
  ['live_now', 'Live now', (m) => m.live_now, 'int', 'Families with spend on the last or second-to-last data day'],
  ['spend', 'Spend', (m) => m.spend, 'money', 'Meta-reported spend in the selected window'],
  ['purchases', 'Purchases', (m) => m.purchases, 'int', 'Meta-reported purchases in the selected window'],
  ['revenue', 'Revenue', (m) => m.revenue, 'money', 'Meta-reported conversion value in the selected window'],
  ['roas', 'ROAS', (m) => m.roas, 'roas', 'Revenue / spend'],
  ['cpa', 'CPA', (m) => m.cpa, 'money', 'Spend / purchases'],
  ['spend_7d', 'Spend 7d', (m) => m.spend_7d, 'money', 'Spend over the last 7 days of data (ignores the window)'],
  ['cpa_7d', 'CPA 7d', (m) => m.cpa_7d, 'money', 'CPA over the last 7 days of data (ignores the window)'],
  ['active_ads', 'Active ads', (m) => m.active_ads, 'int', 'Families with spend > 0 in the selected window'],
  ['testing_spend', 'Testing $', (m) => m.stage && m.stage.testing ? m.stage.testing.spend : null, 'money', 'Spend in Testing campaigns (selected window)', 'sep'],
  ['scaling_spend', 'Scaling $', (m) => m.stage && m.stage.scaling ? m.stage.scaling.spend : null, 'money', 'Spend in Scaling campaigns (selected window)'],
  ['testing_pct', 'Testing %', (m) => m.testing_pct, 'pct', 'Testing share of classified spend'],
];

// opts.getM: row -> metrics object (default r.m); opts.getTotalM: total -> metrics object (default t.m)
// opts.keys: subset of metric keys
export function metricColumns({ getM = (r) => r.m, getTotalM = (t) => t && t.m, keys = null } = {}) {
  return DEFS.filter((d) => !keys || keys.includes(d[0])).map(([key, label, get, kind, title, cls]) => {
    const f = KINDS[kind];
    const val = (r) => { const m = getM(r); return m ? get(m) : null; };
    return {
      key, label, num: true, title, cls: cls || '',
      sort: val,
      cell: (r) => f(val(r)),
      cellTitle: kind === 'money' ? (r) => { const v = val(r); return isNum(v) ? fmt.money2(v) : null; } : null,
      total: (t) => { const m = getTotalM(t); return f(m ? get(m) : null); },
    };
  });
}
export const FLAT_KEYS = ['spend', 'purchases', 'revenue', 'roas', 'cpa', 'spend_7d'];
