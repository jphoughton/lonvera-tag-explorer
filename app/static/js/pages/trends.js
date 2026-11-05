// Marketing metrics, Trends (/trends): the key metrics by Day, Week or Month, newest period first, each value followed by its change against the previous period.
// The open (partial) period is flagged 'so far' and compared with the same elapsed days of the previous one. Everything comes from /api/trends in one call.
// Class names and test ids start with `trn-` (never ad-, ads-, sponsor, banner, promo: ad blockers hide those).
import { h, fmt, isNum, href, setParams } from '../util.js';
import { api } from '../api.js';
import { createTable } from '../table.js';
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
    case 'roas': return fmt.roas(v);
    case 'dec': return fmt.roas(v);
    case 'rate': return fmt.rate(v);
    default: return fmt.int(v);
  }
};

// change cell: '▲12%' for amounts, '▲1.2pp' for rates, coloured by polarity (spend up is neutral, CPA up is bad, ROAS up is good)
function changeNode(m, ch) {
  if (!ch) return h('span', { class: 'trn-none' }, '–');
  const flat = Math.abs(ch.v) < (ch.t === 'pp' ? 0.0005 : 0.005);        // below half a percent (or 0.05 points) it reads 0% and carries no colour
  const txt = flat ? (ch.t === 'pp' ? '0pp' : '0%') : ch.t === 'pp' ? fmt.pp(ch.v * 100, 1) : fmt.pctChange(ch.v);
  let cls = 'trn-flat';
  if (m.pol !== 'neutral' && !flat) {
    const good = (m.pol === 'up_good') === (ch.v > 0);
    cls = good ? 'trn-good' : 'trn-bad';
  }
  return h('span', { class: 'trn-chg ' + cls, 'data-dir': flat ? 'flat' : ch.v > 0 ? 'up' : 'down' }, txt);
}

function periodCell(r) {
  const badge = r.state === 'open' ? h('span', { class: 'badge trn-sofar', 'data-testid': 'trn-so-far', title: `Open period: ${r.days_cov} of ${r.days} days so far (through ${fmt.day(r.covered_end)}). ${r.compare.text ? 'Its change is against ' + r.compare.text + '.' : (r.compare.why || '')}` }, 'so far')
    : r.state === 'not_loaded' ? h('span', { class: 'badge muted-badge trn-pending-badge', 'data-testid': 'trn-not-loaded', title: r.compare.why || 'Not loaded yet' }, 'not loaded yet') : null;
  const vs = r.compare.kind === 'like_for_like' ? `vs ${fmt.day(r.compare.start)}${r.compare.end !== r.compare.start ? ' – ' + fmt.day(r.compare.end) : ''} (same ${r.compare.days} day${r.compare.days === 1 ? '' : 's'})`
    : r.compare.kind === 'previous' ? `vs ${fmt.day(r.compare.start)}${r.compare.end !== r.compare.start ? ' – ' + fmt.day(r.compare.end) : ''}` : '';
  return h('div', { class: 'trn-period' },
    h('div', { class: 'trn-pname' }, h('strong', { 'data-testid': 'trn-label' }, r.label), badge),
    r.sub ? h('div', { class: 'meta' }, r.sub) : null,
    r.state === 'open' ? h('div', { class: 'meta trn-vs', 'data-testid': 'trn-vs' }, `${r.days_cov} of ${r.days} days · ${vs}`) : vs ? h('div', { class: 'meta trn-vs' }, vs) : null);
}

function tableColumns(metrics, byKey, lineKey, onLine) {
  const cols = [{ key: 'period', label: 'Period', sticky: true, cls: 'labelcol', nosort: true, cell: periodCell }];
  for (const key of metrics) {
    const m = byKey[key];
    if (!m) continue;
    const btn = h('button', { type: 'button', class: 'thbtn trn-pick' + (key === lineKey ? ' active' : ''), 'data-testid': 'trn-pick-' + key, 'aria-pressed': String(key === lineKey), title: `${m.help || m.label} Click to draw it as the chart line.` },
      h('span', null, m.label));
    btn.addEventListener('click', () => onLine(key));
    cols.push({ key, label: m.label, num: true, nosort: true, header: btn, cls: 'trn-vcol' + (key === lineKey ? ' trn-linecol' : ''),
      cell: (r) => h('span', { class: 'trn-val', 'data-v': isNum(r.v[key]) ? String(r.v[key]) : '' }, fmtValue(m.kind, r.v[key])),
      cellTitle: (r) => (isNum(r.v[key]) ? (r.reach_through && ['reach', 'frequency', 'uoc', 'uoc_ctr'].includes(key) ? `Reach covers ${fmt.day(r.start)} to ${fmt.day(r.reach_through)} (it runs a day past the loaded spend).` : null) : (r.why[key] || null)) });
    cols.push({ key: key + ':d', label: '', num: true, nosort: true, cls: 'trn-dcol' + (key === lineKey ? ' trn-linecol' : ''),
      header: h('span', { class: 'trn-dhead', title: `Change in ${m.label} against the previous period (like for like for the open one)` }, 'Δ'),
      cell: (r) => changeNode(m, r.d[key]),
      cellTitle: (r) => (r.d[key] ? (r.compare.text ? `Against ${r.compare.text}` : `Against ${fmt.day(r.compare.start)}${r.compare.end !== r.compare.start ? ' – ' + fmt.day(r.compare.end) : ''}`) : (r.why[key + ':d'] || (isNum(r.v[key]) ? r.compare.why : null) || null)) });
  }
  return cols;
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

export async function trendsPage(ctx) {
  const { params: p, signal } = ctx;
  showControls(false);                                  // the page has its own period controls: the global range / stage / compare bar does not apply
  document.title = 'Trends · Marketing metrics';
  const d = await loadTrends(p, signal);
  if (!ctx.alive()) return;
  const iv = d.interval;
  const byKey = Object.fromEntries(d.metrics.map((m) => [m.key, m]));
  const wanted = (p.groups ? String(p.groups).split(',') : ['totals']).filter((g) => d.groups.some((x) => x.key === g));
  const on = new Set(wanted.length ? wanted : ['totals']);
  const lineKey = byKey[p.line] ? p.line : DEFAULT_LINE;
  const metricsShown = [...new Set(d.groups.filter((g) => on.has(g.key)).flatMap((g) => g.metrics))];

  // ---------- controls ----------
  const link = (changes, text, pressed, testid, title) => h('a', { class: 'seg', href: href('/trends', { ...p, ...changes }), 'aria-pressed': String(pressed), 'data-testid': testid, title: title || null }, text);
  const ivCtl = h('div', { class: 'ctl' }, h('span', { class: 'ctl-name' }, 'Interval'),
    h('span', { class: 'segs', role: 'group', 'aria-label': 'Interval', 'data-testid': 'trn-interval' }, INTERVALS.map(([k, t, ti]) => link({ interval: k === 'week' ? null : k, periods: null }, t, iv === k, 'trn-interval-' + k, ti))));
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
  const groupChip = (g) => {
    const pressed = on.has(g.key);
    const next = pressed ? [...on].filter((x) => x !== g.key) : d.groups.map((x) => x.key).filter((x) => on.has(x) || x === g.key);
    return h('a', { class: 'seg', href: href('/trends', { ...p, groups: next.length && !(next.length === 1 && next[0] === 'totals') ? next.join(',') : null }), 'aria-pressed': String(pressed), 'data-testid': 'trn-group-' + g.key,
      title: pressed && on.size === 1 ? 'At least one group stays on' : null, onclick: pressed && on.size === 1 ? (e) => e.preventDefault() : null }, g.label);
  };
  const groupsCtl = h('div', { class: 'ctl' }, h('span', { class: 'ctl-name' }, 'Columns'), h('span', { class: 'segs trn-groups', role: 'group', 'aria-label': 'Column groups', 'data-testid': 'trn-groups' }, d.groups.map(groupChip)));
  const controls = h('div', { class: 'ctl-row trn-controls', 'data-testid': 'trn-controls' }, ivCtl, periodsCtl, numbersCtl, groupsCtl);

  // ---------- freshness ----------
  const fresh = h('div', { class: 'trn-fresh', 'data-testid': 'trn-fresh' }, h('span', { class: 'ctl-name' }, 'Data through'),
    ...d.freshness.map((f) => h('span', { class: 'badge ' + (f.stale ? 'conf-low' : 'muted-badge'), 'data-testid': 'trn-fresh-' + f.key, 'data-stale': String(f.stale),
      title: f.stale ? `${f.label} is ${f.days_behind == null ? 'not loaded' : f.days_behind + ' days behind yesterday'}.` : `${f.label}: ${f.first ? fmt.day(f.first) : '–'} to ${f.last ? fmt.day(f.last) : '–'}` }, `${f.short} · ${f.last ? fmt.day(f.last) : 'none'}`)));

  // ---------- help ----------
  const help = h('section', { class: 'trn-help', 'data-testid': 'trn-help' },
    h('p', { 'data-testid': 'trn-help-short' }, h('strong', null, 'Reading this. '), 'Newest period first, each value followed by its change against the previous period. Ratios are ratios of the period\'s sums. ',
      h('span', { class: 'badge trn-sofar' }, 'so far'), ' periods are compared with the same elapsed days of the previous one. A dash is missing data, not zero (hover for why).'),
    h('details', { class: 'trn-more', 'data-testid': 'trn-more' }, h('summary', null, 'Definitions'),
      h('p', null, `Days are Pacific days, weeks run Monday to Sunday (ISO week numbers), months are calendar months. Every ratio (ROAS, CPA, CPM, CTR, CPC) is the ratio of the period's sums, never an average of daily ratios. `,
        h('strong', null, 'NC ROAS'), ' is actual new-customer revenue over Meta + Google spend; ', h('strong', null, 'NC CPA'), ' is that spend over actual new customers.'),
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
  const table = createTable({
    columns: tableColumns(metricsShown, byKey, lineKey, pickLine), rows: d.rows, externalSort: true, rowKey: (r) => r.key, testid: 'trn-table',
    rowClass: (r) => (r.state === 'open' ? 'trn-open' : r.state === 'not_loaded' ? 'trn-pending' : ''), emptyText: 'No periods to show.',
  });

  const page = h('div', { class: 'page trn-page', 'data-testid': 'trn-page' },
    h('div', { class: 'pagehead' }, h('h1', null, 'Trends'), h('span', { class: 'muted' }, ` ${d.labels[iv]} · last ${d.periods} ${iv === 'day' ? 'days' : iv === 'week' ? 'weeks' : 'months'}`)),
    controls, fresh, help, panel, table.el);
  ctx.mount(page);
  chartFor(canvas, d, byKey, lineKey);
}
