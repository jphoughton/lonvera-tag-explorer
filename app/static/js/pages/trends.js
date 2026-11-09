// Marketing metrics, Trends (/trends): the key metrics by Day, Week or Month laid out like the Google Sheet 'Lonvera Projections' rollup tabs: periods as rows, oldest first and
// the newest (open) period last, labelled bands of columns left to right, the change against the previous period as one block at the right (the sheet's WoW / MoM block).
// The open (partial) period is flagged 'so far' and compared with the same elapsed days of the previous one. Everything comes from /api/trends (the month grain's plan block
// from /api/trends/plan, the Pacing page's own numbers).
// Class names and test ids start with `trn-` (never ad-, ads-, sponsor, banner, promo: ad blockers hide those).
import { h, fmt, isNum, href, setParams } from '../util.js';
import { api } from '../api.js';
import { trendChart, BAR_COLOR, LINE_COLOR } from '../charts.js';
import { showControls } from '../controls.js';

const INTERVALS = [['day', 'Day', 'Day over day (DoD)'], ['week', 'Week', 'Week over week (WoW)'], ['month', 'Month', 'Month over month (MoM)']];
const NUMBERS = [['own', 'Own reporting', "Meta's and Google's own purchases and revenue"], ['nc', 'Upstack new customers', "Upstack any-click attribution, new customers"], ['all', 'Upstack all orders', 'Upstack any-click attribution, all orders']];
const DEFAULT_PERIODS = { day: 14, week: 12, month: 12 };
const MAX_PERIODS = { day: 120, week: 104, month: 36 };
const SLOW_MS = 15000;
const CHART_KIND = { money: 'money', money2: 'money2', int: 'int', roas: 'roas', dec: 'roas', rate: 'rate' };
const DEFAULT_LINE = 'nc_roas';

const intervalOf = (p) => (['day', 'week', 'month'].includes(p.interval) ? p.interval : 'week');
const numbersOf = (p) => (['own', 'nc', 'all'].includes(p.numbers) ? p.numbers : 'own');

function apiParams(p) {
  const iv = intervalOf(p);
  const o = { interval: iv, numbers: numbersOf(p) };
  if (p.periods) o.periods = p.periods;
  if (p.as_of) o.as_of = p.as_of;
  return o;
}

// One call with a patience limit: a request that hangs or fails ends in the page's error box with Retry (app.js), never in an endless 'Loading…'.
// `trn_timeout_ms` in the URL shortens the limit (used by the tests).
async function loadTrends(p, signal) {
  const limit = Number(p.trn_timeout_ms) > 0 ? Number(p.trn_timeout_ms) : SLOW_MS;
  const ctl = new AbortController();
  let slow = false;
  const timer = setTimeout(() => { slow = true; ctl.abort(); }, limit);
  const relay = () => ctl.abort();
  signal.addEventListener('abort', relay);
  try {
    return await api('/api/trends', apiParams(p), { signal: ctl.signal });
  } catch (e) {
    if (slow) throw new Error(`Trends took longer than ${Math.round(limit / 1000)} seconds to answer. The server may be busy or the data is being rebuilt: press Retry.`);
    throw e;
  } finally {
    clearTimeout(timer);
    signal.removeEventListener('abort', relay);
  }
}

const fmtValue = (kind, v) => {
  if (!isNum(v)) return '–';
  switch (kind) {
    case 'money': return fmt.money(v);
    case 'money2': return fmt.money2(v);
    case 'roas': return fmt.roas(v) + 'x';
    case 'dec': return fmt.roas(v);
    case 'rate': return fmt.rate(v);
    default: return fmt.int(v);
  }
};
const signed = (v, f) => (v > 0 ? '+' : v < 0 ? '-' : '') + f(Math.abs(v));
const fmtPctDiff = (v) => (isNum(v) ? (Math.abs(v) < 0.0005 ? '0.0%' : signed(v, (x) => (x * 100).toFixed(1) + '%')) : '–');
const fmtGap = (v) => (isNum(v) ? (Math.round(v) === 0 ? '$0' : signed(v, (x) => fmt.money(x))) : '–');

// the good / bad colour of a change: spend up is neutral, CPA up is bad, ROAS up is good; below half a percent it reads 0 and carries no colour
function polClass(pol, v, flat) {
  if (pol === 'neutral' || flat || !isNum(v)) return 'trn-flat';
  return (pol === 'up_good') === (v > 0) ? 'trn-good' : 'trn-bad';
}

// '▲12%' / '▲1.2pp' next to a metric (the 'Change after every column' switch)
function changeNode(m, ch) {
  if (!ch) return h('span', { class: 'trn-none' }, '–');
  const flat = Math.abs(ch.v) < (ch.t === 'pp' ? 0.0005 : 0.005);
  const txt = flat ? (ch.t === 'pp' ? '0pp' : '0%') : ch.t === 'pp' ? fmt.pp(ch.v * 100, 1) : fmt.pctChange(ch.v);
  return h('span', { class: 'trn-chg ' + polClass(m.pol, ch.v, flat), 'data-dir': flat ? 'flat' : ch.v > 0 ? 'up' : 'down' }, txt);
}

// the sheet's change block: one signed percent per metric, the cell tinted green or red
function changeCell(m, ch) {
  if (!ch) return { text: '–', cls: 'trn-none' };
  const flat = Math.abs(ch.v) < (ch.t === 'pp' ? 0.0005 : 0.005);
  const text = ch.t === 'pp' ? (flat ? '0.0pp' : signed(ch.v * 100, (x) => x.toFixed(1) + 'pp')) : fmtPctDiff(flat ? 0 : ch.v);
  return { text, cls: polClass(m.pol, ch.v, flat), dir: flat ? 'flat' : ch.v > 0 ? 'up' : 'down' };
}

const vsText = (r) => (r.compare.kind === 'like_for_like' ? `vs ${fmt.day(r.compare.start)}${r.compare.end !== r.compare.start ? ' – ' + fmt.day(r.compare.end) : ''} (same ${r.compare.days} day${r.compare.days === 1 ? '' : 's'})`
  : r.compare.kind === 'previous' ? `vs ${fmt.day(r.compare.start)}${r.compare.end !== r.compare.start ? ' – ' + fmt.day(r.compare.end) : ''}` : '');

function periodCell(r) {
  const badge = r.state === 'open' ? h('span', { class: 'badge trn-sofar', 'data-testid': 'trn-so-far' }, 'so far')
    : r.state === 'not_loaded' ? h('span', { class: 'badge muted-badge trn-pending-badge', 'data-testid': 'trn-not-loaded' }, 'not loaded yet') : null;
  const sub = r.state === 'open' ? [r.sub, `${r.days_cov} of ${r.days} days`].filter(Boolean).join(' · ') : r.sub;
  return h('div', { class: 'trn-period' },
    h('div', { class: 'trn-pname' }, h('strong', { 'data-testid': 'trn-label' }, r.label), badge),
    sub ? h('div', { class: 'meta trn-psub' }, sub) : null);
}

const periodTitle = (r) => (r.state === 'open' ? `Open period: ${r.days_cov} of ${r.days} days so far (through ${fmt.day(r.covered_end)}). ${r.compare.text ? 'Its change is against ' + r.compare.text + '.' : (r.compare.why || '')}`
  : r.state === 'not_loaded' ? (r.compare.why || 'Not loaded yet') : (vsText(r) ? `Its change is ${vsText(r)}` : null));

// Which bands show: the URL's groups, else the defaults of this interval ('plan' only for month); legacy single-product values map to By product, 'all' is every chip.
const LEGACY = { vitabull: 'products', beefliver: 'products', boneflex: 'products' };
function groupsOn(p, d) {
  const keys = d.groups.map((g) => g.key);
  const dflt = d.groups.filter((g) => g.default).map((g) => g.key);
  if (!p.groups) return new Set(dflt);
  const asked = String(p.groups).split(',').map((x) => (x === 'all' ? 'all' : LEGACY[x] || x));
  const on = asked.includes('all') ? keys : keys.filter((k) => asked.includes(k));
  return new Set(on.length ? on : dflt);
}

// The table: two header rows (band titles, column labels), the period column frozen on the left, the headers frozen on top.
function buildTable({ d, plan, byKey, on, lineKey, onLine, delta }) {
  const rows = d.rows.slice().reverse();                                  // the sheet's order: oldest first, the open period last
  const bands = d.layout.filter((b) => on.has(b.group));
  const monthIdx = plan && plan.available ? new Map(plan.months.map((m, i) => [m, i])) : new Map();
  const seenPick = new Set();
  const band1 = [h('th', { class: 'trn-corner', rowspan: '2', 'data-col': 'period' }, 'Period')];
  const band2 = [];
  const cols = [];                                                        // {band, key, kind, pol, cell(r) -> {text, cls, title, dir, v}}
  for (const b of bands) {
    let span = 0;
    b.cols.forEach((c, i) => {
      const m = byKey[c.key];
      const first = i === 0;
      const tone = c.tone ? 'trn-t-' + c.tone : '';
      const head = h('th', { class: `num trn-colhead trn-b-${b.tone} ${tone}${first ? ' trn-bfirst' : ''}`, 'data-col': b.change ? 'chg:' + c.key : c.key, 'data-band': b.key });
      if (c.sub) head.append(h('span', { class: 'trn-sub' }, c.sub));
      if (m && !seenPick.has(c.key)) {
        seenPick.add(c.key);
        const btn = h('button', { type: 'button', class: 'thbtn trn-pick' + (c.key === lineKey ? ' active' : ''), 'data-testid': 'trn-pick-' + c.key, 'aria-pressed': String(c.key === lineKey),
          title: `${m.help || m.label} Click to draw it as the chart line.` }, h('span', null, c.label));
        btn.addEventListener('click', () => onLine(c.key));
        head.append(btn);
      } else head.append(h('span', null, c.label));
      band2.push(head);
      span += 1;
      if (b.external) {
        const idxOf = (r) => monthIdx.get(r.start);
        cols.push({ band: b, first, key: c.key, cell: (r) => {
          const i = idxOf(r);
          const v = plan && plan.available && i != null ? plan.values[c.key][i] : null;
          if (!isNum(v)) return { text: '–', cls: 'trn-none', title: plan && !plan.available ? plan.reason : r.state === 'not_loaded' ? null : 'No plan figure for this month.' };
          if (c.kind === 'diff') return { text: fmtPctDiff(v), cls: polClass(c.pol, v, Math.abs(v) < 0.0005), tint: true, v };
          if (c.kind === 'gap') return { text: fmtGap(v), cls: polClass(c.pol, v, Math.round(v) === 0), tint: true, v };
          return { text: fmt.money(v), cls: '', v };
        } });
        return;
      }
      if (b.change) {
        cols.push({ band: b, first, key: 'chg:' + c.key, cell: (r) => { const x = changeCell(m, r.d[c.key]); return { ...x, tint: x.cls !== 'trn-none' && x.cls !== 'trn-flat', title: r.d[c.key] ? (r.compare.text ? `Against ${r.compare.text}` : `Against ${fmt.day(r.compare.start)}${r.compare.end !== r.compare.start ? ' – ' + fmt.day(r.compare.end) : ''}`) : (r.why[c.key + ':d'] || (isNum(r.v[c.key]) ? r.compare.why : null) || null) }; } });
        return;
      }
      cols.push({ band: b, first, key: c.key, m, num: true, cell: (r) => ({ text: fmtValue(m.kind, r.v[c.key]), v: r.v[c.key],
        title: isNum(r.v[c.key]) ? (r.reach_through && ['reach', 'frequency', 'uoc', 'uoc_ctr'].includes(c.key) ? `Reach covers ${fmt.day(r.start)} to ${fmt.day(r.reach_through)} (it runs a day past the loaded spend).` : null) : (r.why[c.key] || null) }) });
      if (delta) {
        const dh = h('th', { class: `num trn-colhead trn-dhead-cell trn-b-${b.tone} ${tone}`, 'data-col': c.key + ':d' }, h('span', { class: 'trn-dhead', title: `Change in ${c.label} against the previous period (like for like for the open one)` }, 'Δ'));
        band2.push(dh);
        span += 1;
        cols.push({ band: b, key: c.key + ':d', delta: true, cell: (r) => ({ node: changeNode(m, r.d[c.key]),
          title: r.d[c.key] ? (r.compare.text ? `Against ${r.compare.text}` : `Against ${fmt.day(r.compare.start)}${r.compare.end !== r.compare.start ? ' – ' + fmt.day(r.compare.end) : ''}`) : (r.why[c.key + ':d'] || (isNum(r.v[c.key]) ? r.compare.why : null) || null) }) });
      }
    });
    band1.push(h('th', { class: `trn-band trn-b-${b.tone}`, colspan: String(span), 'data-band': b.key, 'data-testid': 'trn-band-' + b.key }, h('span', { class: 'trn-bandtitle' }, b.title)));
  }
  const thead = h('thead', h('tr', { class: 'trn-bandrow' }, band1), h('tr', { class: 'trn-colrow' }, band2));
  const tbody = h('tbody');
  for (const r of rows) {
    const tr = h('tr', { 'data-key': r.key, class: r.state === 'open' ? 'trn-open' : r.state === 'not_loaded' ? 'trn-pending' : '' },
      h('td', { class: 'trn-pcol', title: periodTitle(r) }, periodCell(r)));
    for (const c of cols) {
      const x = r.state === 'not_loaded' ? { text: '–', cls: 'trn-none' } : c.cell(r);
      const td = h('td', { class: ['num', c.first ? 'trn-bfirst' : '', x.tint ? 'trn-tint ' + x.cls : ''].join(' ').trim(), title: x.title || null }, x.node || h('span', { class: 'trn-val' + (x.tint ? ' ' + x.cls : ''), 'data-v': isNum(x.v) ? String(x.v) : '', 'data-dir': x.dir || null }, x.text));
      tr.append(td);
    }
    tbody.append(tr);
  }
  if (!rows.length) tbody.append(h('tr', null, h('td', { colspan: String(cols.length + 1), class: 'trn-empty' }, 'No periods to show.')));
  const box = h('div', { class: 'tablewrap trn-box', 'data-testid': 'trn-table' }, h('table', { class: 'dt trn-grid' }, thead, tbody));
  return { box, rows };
}

function chartFor(canvas, d, byKey, lineKey) {
  const rows = d.rows.filter((r) => r.state !== 'not_loaded').slice().reverse();
  const lm = byKey[lineKey] || byKey[DEFAULT_LINE];
  const bar = rows.map((r) => r.v.total_spend);
  const line = rows.map((r) => (isNum(r.v[lm.key]) ? r.v[lm.key] : null));
  return trendChart(canvas, {
    labels: rows.map((r) => r.start), grain: d.interval, partial: rows.map((r) => r.open),
    left: { kind: 'money', title: 'Total spend (bars, left)' }, right: { kind: CHART_KIND[lm.kind] || 'int', title: `${lm.label} (line, right)` },
    datasets: [
      { axis: 'left', type: 'bar', label: 'Total spend', data: bar, color: BAR_COLOR, kind: 'money' },
      { axis: 'right', type: 'line', label: lm.label, data: line, color: LINE_COLOR, kind: CHART_KIND[lm.kind] || 'int', partialDash: true },
    ],
  });
}

// the month grain's Pacing figures; a failure never blocks the table (the plan columns then show a dash with the reason)
async function loadPlan(p, d, signal) {
  if (d.interval !== 'month') return null;
  const ks = d.rows.filter((r) => r.state !== 'not_loaded').map((r) => r.start).sort();
  if (!ks.length) return null;
  try {
    const o = { from: ks[0], to: ks[ks.length - 1] };
    if (p.as_of) o.as_of = p.as_of;
    return await api('/api/trends/plan', o, { signal });
  } catch (e) {
    if (signal.aborted) throw e;
    return { available: false, reason: `The plan figures could not be loaded (${e && e.message ? e.message : e}).`, months: [], values: {} };
  }
}

export async function trendsPage(ctx) {
  const { params: p, signal } = ctx;
  showControls(false);                                  // the page has its own period controls: the global range / stage / compare bar does not apply
  document.title = 'Trends · Marketing metrics';
  const d = await loadTrends(p, signal);
  if (!ctx.alive()) return;
  const plan = await loadPlan(p, d, signal);
  if (!ctx.alive()) return;
  const iv = d.interval;
  const byKey = Object.fromEntries(d.metrics.map((m) => [m.key, m]));
  const on = groupsOn(p, d);
  const dflt = d.groups.filter((g) => g.default).map((g) => g.key);
  const lineKey = byKey[p.line] ? p.line : DEFAULT_LINE;
  const delta = p.delta === '1';

  // ---------- controls ----------
  const link = (changes, text, pressed, testid, title) => h('a', { class: 'seg', href: href('/trends', { ...p, ...changes }), 'aria-pressed': String(pressed), 'data-testid': testid, title: title || null }, text);
  const ivCtl = h('div', { class: 'ctl' }, h('span', { class: 'ctl-name' }, 'Interval'),
    h('span', { class: 'segs', role: 'group', 'aria-label': 'Interval', 'data-testid': 'trn-interval' }, INTERVALS.map(([k, t, ti]) => link({ interval: k === 'week' ? null : k, periods: null, groups: null }, t, iv === k, 'trn-interval-' + k, ti))));
  const periodsInput = h('input', { type: 'number', min: '2', max: String(MAX_PERIODS[iv]), step: '1', class: 'trn-num', id: 'trn-periods', 'data-testid': 'trn-periods', 'aria-label': 'Number of periods',
    value: String(d.periods), placeholder: String(DEFAULT_PERIODS[iv]) });
  const applyPeriods = () => {
    const n = Math.floor(Number(periodsInput.value));
    setParams({ periods: Number.isFinite(n) && n >= 2 && n <= MAX_PERIODS[iv] && n !== DEFAULT_PERIODS[iv] ? String(n) : null });
  };
  periodsInput.addEventListener('change', applyPeriods);
  periodsInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') applyPeriods(); });
  const periodsCtl = h('label', { class: 'ctl' }, h('span', { class: 'ctl-name' }, `Last ${iv === 'day' ? 'days' : iv === 'week' ? 'weeks' : 'months'}`), periodsInput);
  const numbersCtl = h('div', { class: 'ctl', title: "What Meta's and Google's purchases, revenue and ROAS columns count. Spend, clicks and impressions are the platform's in every case." }, h('span', { class: 'ctl-name' }, 'Numbers from'),
    h('span', { class: 'segs', role: 'group', 'aria-label': 'Numbers from', 'data-testid': 'trn-numbers' }, NUMBERS.map(([k, t, ti]) => link({ numbers: k === 'own' ? null : k }, t, d.numbers === k, 'trn-numbers-' + k, ti))));
  const deltaCtl = h('div', { class: 'ctl', title: 'Adds a Δ column after every metric (percent change against the previous period; points for rates). The change block at the right is always there.' }, h('span', { class: 'ctl-name' }, 'Change after every column'),
    h('span', { class: 'segs', role: 'group', 'aria-label': 'Change after every column', 'data-testid': 'trn-delta' }, link({ delta: null }, 'Off', !delta, 'trn-delta-off'), link({ delta: '1' }, 'On', delta, 'trn-delta-on')));
  const allKeys = d.groups.map((g) => g.key);
  const groupHref = (set) => href('/trends', { ...p, groups: set.length === dflt.length && dflt.every((k) => set.includes(k)) ? null : set.join(',') });
  const groupChip = (g) => {
    const pressed = on.has(g.key);
    const next = pressed ? allKeys.filter((x) => on.has(x) && x !== g.key) : allKeys.filter((x) => on.has(x) || x === g.key);
    return h('a', { class: 'seg', href: groupHref(next), 'aria-pressed': String(pressed), 'data-testid': 'trn-group-' + g.key,
      title: pressed && on.size === 1 ? 'At least one band stays on' : null, onclick: pressed && on.size === 1 ? (e) => e.preventDefault() : null }, g.label);
  };
  const allOn = allKeys.every((k) => on.has(k));
  const allChip = h('a', { class: 'seg', href: allOn ? groupHref(dflt) : groupHref(allKeys), 'aria-pressed': String(allOn), 'data-testid': 'trn-group-all', title: allOn ? 'Back to the default bands' : 'Show every band' }, 'All');
  const groupsCtl = h('div', { class: 'ctl' }, h('span', { class: 'ctl-name' }, 'Bands'), h('span', { class: 'segs trn-groups', role: 'group', 'aria-label': 'Bands of columns', 'data-testid': 'trn-groups' }, d.groups.map(groupChip), allChip));
  const controls = h('div', { class: 'ctl-row trn-controls', 'data-testid': 'trn-controls' }, ivCtl, periodsCtl, numbersCtl, deltaCtl, groupsCtl);

  // ---------- freshness ----------
  const fresh = h('div', { class: 'trn-fresh', 'data-testid': 'trn-fresh' }, h('span', { class: 'ctl-name' }, 'Data through'),
    ...d.freshness.map((f) => h('span', { class: 'badge ' + (f.stale ? 'conf-low' : 'muted-badge'), 'data-testid': 'trn-fresh-' + f.key, 'data-stale': String(f.stale),
      title: f.stale ? `${f.label} is ${f.days_behind == null ? 'not loaded' : f.days_behind + ' days behind yesterday'}.` : `${f.label}: ${f.first ? fmt.day(f.first) : '–'} to ${f.last ? fmt.day(f.last) : '–'}` }, `${f.short} · ${f.last ? fmt.day(f.last) : 'none'}`)));

  // ---------- help ----------
  const help = h('section', { class: 'trn-help', 'data-testid': 'trn-help' },
    h('p', { 'data-testid': 'trn-help-short' }, h('strong', null, 'Reading this. '), 'Laid out like the rollup tabs of the Lonvera Projections sheet: oldest period first, the newest last, bands of columns left to right, the change against the previous period in the block at the right. Ratios are ratios of the period\'s sums. ',
      h('span', { class: 'badge trn-sofar' }, 'so far'), ' periods are compared with the same elapsed days of the previous one. A dash is missing data, not zero (hover for why).'),
    h('details', { class: 'trn-more', 'data-testid': 'trn-more' }, h('summary', null, 'Definitions'),
      h('p', null, `Days are Pacific days, weeks run Monday to Sunday (ISO week numbers), months are calendar months. Every ratio (ROAS, CPA, CPM, CTR, CPC) is the ratio of the period's sums, never an average of daily ratios. `,
        h('strong', null, 'NC ROAS'), ' is actual new-customer revenue over Meta + Google spend; ', h('strong', null, 'NC CPA'), ' is that spend over actual new customers. In the product bands the ', h('strong', null, 'Total'), ' of NC ROAS and NC CPA divides by Meta spend alone, as the sheet does. ', h('strong', null, 'NC Conversion'), ' is Upstack new customers over Upstack new visitors (the NC Orders and New Visitors columns), which differs from the sheet; the sheet\'s link-click version is NC Conversion (clicks) in the Meta band.'),
      h('p', null, h('strong', null, 'So far. '), 'Month to date is compared with the first days of last month, week to date with the same weekdays of last week, never with the full period. ',
        `Sources load once a day, so today (${fmt.day(d.today)}) has no numbers yet: its row says so instead of showing a misleading change.`),
      h('p', null, h('strong', null, 'Dashes. '), 'The period starts before a source does, or the source has a gap. Reach is de-duplicated by Meta per period, so it is read from Meta\'s own day, week and month rows and has no like-for-like change while the period is open. ',
        d.notes.length ? d.notes.join(' ') : ''),
      h('p', { class: 'muted' }, `Counts are Upstack's orders feed (tax included, before refunds). Meta and Google purchases and revenue: ${d.basis}. Data runs through ${fmt.day(d.data_through)}; Meta restates conversions for several days, so the latest days can still move.`)));

  // ---------- chart ----------
  const canvas = h('canvas', { id: 'trn-chart', role: 'img', 'aria-label': 'Total spend per period with the chosen metric as a line' });
  const lm = byKey[lineKey];
  const panel = h('section', { class: 'trend', 'data-testid': 'trn-chart-panel' },
    h('div', { class: 'trend-head' }, h('h2', null, `${d.labels[iv]}: total spend and ${lm.label}`)),
    h('div', { class: 'chartbox trendbox' }, canvas),
    h('div', { class: 'trend-note' }, h('span', { class: 'note', 'data-testid': 'trn-chart-note' }, `Bars: total spend. Line: ${lm.label}, drawn dashed for the open period (so far). Click any column heading in the table to change the line.`)));

  // ---------- table ----------
  const pickLine = (key) => setParams({ line: key === DEFAULT_LINE ? null : key });
  const { box, rows } = buildTable({ d, plan, byKey, on, lineKey, onLine: pickLine, delta });
  const open = d.rows.find((r) => r.state === 'open');
  const notes = h('div', { class: 'trn-notes', 'data-testid': 'trn-notes' },
    open ? h('p', { 'data-testid': 'trn-vs' }, h('strong', null, `${open.label} (${open.days_cov} of ${open.days} days, so far)`), open.compare.text ? ` is compared with ${open.compare.text}: ${vsText(open)}.` : ` has no like-for-like comparison: ${open.compare.why}`) : null,
    iv === 'month' && plan ? h('p', { 'data-testid': 'trn-plan-note' }, plan.available ? h('span', null, h('strong', null, 'Projected*: '), plan.caption) : h('span', null, h('strong', null, 'Plan columns are empty. '), plan.reason)) : null,
    h('p', { class: 'muted', 'data-testid': 'trn-left-out' }, 'Left out because this app has no data for them: the sheet\'s BUSINESS-WIDE and AMAZON bands, EBITDA (LIVE P&L) and the plan block on the day and week views, TikTok spend, the per-product click bands and the bid columns.'));

  const page = h('div', { class: 'page trn-page', 'data-testid': 'trn-page' },
    h('div', { class: 'pagehead' }, h('h1', null, 'Trends'), h('span', { class: 'muted' }, ` ${d.labels[iv]} · last ${d.periods} ${iv === 'day' ? 'days' : iv === 'week' ? 'weeks' : 'months'}`)),
    controls, fresh, help, panel, box, notes);
  ctx.mount(page);
  chartFor(canvas, d, byKey, lineKey);
  if (rows.length && box.scrollHeight > box.clientHeight) box.scrollTop = box.scrollHeight;       // newest period (last row) in view, as when the sheet is scrolled to its end
}
