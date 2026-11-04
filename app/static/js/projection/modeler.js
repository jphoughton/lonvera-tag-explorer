// Projection page: Scenario modeler.
// A what-if workbench: change monthly ad spend, order value (AOV) and cost per new customer (CPA), pick the retention model, and see yearly revenue and
// EBITDA move as you type. The three plan rows are the Spend plan page's own series, so both pages always agree. Every edit is previewed on the server
// without writing (POST /modeler/preview), then saved through the normal audited path (PUT /series, PUT /cohorts/levers).
import { events, flushSeries } from './lib/api.js';
import { scenarios } from './lib/scenario.js';
import { askText, confirmBox, debounce } from './lib/util.js';

const STYLE_ID = 'mo-style';
const CSS = `
.mo-page { gap: var(--lv-sp-3); }
.mo-cue { display: flex; flex-wrap: wrap; align-items: center; gap: var(--lv-sp-2) var(--lv-sp-3); padding: var(--lv-sp-2) var(--lv-sp-3); border: 1px solid var(--lv-line); border-radius: var(--lv-r-sm); background: var(--lv-surface); font-size: var(--lv-fs-sm); color: var(--lv-ink-2); }
.mo-cue.copy { border-color: var(--lv-warn); background: var(--lv-warn-bg); color: var(--lv-ink); }
.mo-cue .who { font-weight: var(--lv-fw-heavy); color: var(--lv-ink); }
.mo-cue .grow { flex: 1 1 260px; min-width: 0; }
.mo-layout { display: grid; grid-template-columns: minmax(0, 1fr) 500px; gap: var(--lv-sp-4); align-items: start; }
.mo-main { display: flex; flex-direction: column; gap: var(--lv-sp-4); min-width: 0; }
.mo-outputs { position: sticky; top: 54px; align-self: start; display: flex; flex-direction: column; gap: var(--lv-sp-3); max-height: calc(100vh - 66px); overflow-y: auto; scrollbar-width: thin; }
.mo-card h2 { margin: 0 0 var(--lv-sp-2); font-size: var(--lv-fs-md); font-weight: var(--lv-fw-heavy); }
.mo-card-head { display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: var(--lv-sp-1) var(--lv-sp-3); margin-bottom: var(--lv-sp-2); }
.mo-card-head h2 { margin: 0; }
.mo-state { font-size: var(--lv-fs-xs); font-weight: var(--lv-fw-semi); color: var(--lv-muted); }
.mo-state.err { color: var(--lv-bad); }
.mo-state.busy { color: var(--lv-ink-2); }
.mo-years-wrap { overflow-x: auto; }
.mo-years { width: 100%; border-collapse: collapse; font-size: var(--lv-fs-sm); font-variant-numeric: tabular-nums; }
.mo-years th, .mo-years td { padding: 5px 8px; text-align: right; border-bottom: 1px solid var(--lv-line-soft); white-space: nowrap; }
.mo-years th:first-child, .mo-years td:first-child { text-align: left; white-space: normal; }
.mo-years thead th { font-size: var(--lv-fs-xs); font-weight: var(--lv-fw-bold); color: var(--lv-ink); vertical-align: bottom; }
.mo-years thead th small { display: block; font-weight: var(--lv-fw-regular); color: var(--lv-muted); font-size: var(--lv-fs-xs); white-space: normal; margin-left: auto; line-height: 1.25; }
.mo-years tbody th { font-weight: var(--lv-fw-medium); color: var(--lv-ink-2); }
.mo-years tr.mo-sub th { padding-left: 18px; font-weight: var(--lv-fw-regular); color: var(--lv-muted); }
.mo-years tr.mo-sub td { color: var(--lv-ink-2); }
.mo-years tr.mo-strong th, .mo-years tr.mo-strong td { font-weight: var(--lv-fw-heavy); color: var(--lv-ink); }
.mo-years tr.mo-strong td, .mo-years tr.mo-strong th { border-top: 1px solid var(--lv-line-strong); }
.mo-years tr.mo-vs th, .mo-years tr.mo-vs td { font-size: var(--lv-fs-xs); color: var(--lv-muted); }
.mo-years td.neg { color: var(--lv-neg); }
.mo-years td.up { color: var(--lv-good); font-weight: var(--lv-fw-semi); }
.mo-years td.down { color: var(--lv-neg); font-weight: var(--lv-fw-semi); }
.mo-years td.proj, .mo-years th.proj { background: var(--lv-proj-cell); }
.mo-memo { margin: var(--lv-sp-2) 0 0; font-size: var(--lv-fs-sm); color: var(--lv-ink-2); }
.mo-memo b { color: var(--lv-ink); font-weight: var(--lv-fw-heavy); }
.mo-memo .neg { color: var(--lv-neg); font-weight: var(--lv-fw-heavy); }
.mo-ebitda { display: flex; flex-wrap: wrap; align-items: center; gap: var(--lv-sp-1) var(--lv-sp-2); margin-top: var(--lv-sp-2); font-size: var(--lv-fs-xs); color: var(--lv-muted); }
.mo-ebitda select { min-height: 26px; font-size: var(--lv-fs-xs); }
.mo-chart { position: relative; height: 170px; margin-top: var(--lv-sp-3); }
.mo-sum { display: none; margin: 0; font-size: var(--lv-fs-sm); font-weight: var(--lv-fw-semi); }
.mo-sum b { font-weight: var(--lv-fw-heavy); }
.mo-body > summary { display: none; }
.mo-tool { display: flex; flex-direction: column; gap: var(--lv-sp-2); margin-bottom: var(--lv-sp-3); padding: var(--lv-sp-3); background: var(--lv-surface-2); border-radius: var(--lv-r-sm); }
.mo-tool-row { display: flex; flex-wrap: wrap; align-items: flex-end; gap: var(--lv-sp-2) var(--lv-sp-3); }
.mo-field { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
.mo-field > label, .mo-field > .lbl { font-size: var(--lv-fs-xs); font-weight: var(--lv-fw-semi); color: var(--lv-ink-2); }
.mo-field select, .mo-field input { min-width: 0; }
.mo-field input[type="text"] { width: 120px; }
.mo-field.sel select { max-width: 190px; }
.mo-tool-text { margin: 0; font-size: var(--lv-fs-sm); color: var(--lv-ink-2); min-height: 20px; }
.mo-tool-text.err { color: var(--lv-bad); font-weight: var(--lv-fw-semi); }
.mo-hint { margin: 0; font-size: var(--lv-fs-xs); color: var(--lv-muted); }
.mo-legend { display: flex; flex-wrap: wrap; gap: 4px var(--lv-sp-4); align-items: center; font-size: var(--lv-fs-xs); color: var(--lv-muted); margin-top: var(--lv-sp-2); }
.mo-page .pg-cell.pg-in.tone-locked { background: var(--lv-surface-2); color: var(--lv-muted); font-weight: var(--lv-fw-regular); cursor: default; border-right-color: var(--lv-line-soft); border-bottom-color: var(--lv-line-soft); }
.mo-page .pg-cell.tone-locked::before { content: none; }
.mo-opts { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: var(--lv-sp-3); }
.mo-opt { display: flex; flex-direction: column; background: var(--lv-surface); border: 1px solid var(--lv-line-strong); border-radius: var(--lv-r-sm); }
.mo-opt:hover { background: var(--lv-hover); }
.mo-opt[data-active="true"] { border-color: var(--lv-ink); box-shadow: inset 0 0 0 1px var(--lv-ink); background: var(--lv-hl-bg); }
.mo-opt-pick { display: flex; flex-direction: column; gap: var(--lv-sp-1); width: 100%; padding: var(--lv-sp-3); text-align: left; font: inherit; color: var(--lv-ink); background: transparent; border: 0; border-radius: var(--lv-r-sm); cursor: pointer; }
.mo-opt-pick .t { display: flex; flex-wrap: wrap; align-items: center; gap: 4px var(--lv-sp-2); font-weight: var(--lv-fw-heavy); font-size: var(--lv-fs-md); }
.mo-opt-pick .m { font-size: var(--lv-fs-sm); color: var(--lv-ink-2); font-variant-numeric: tabular-nums; }
.mo-opt-pick .d { font-size: var(--lv-fs-xs); color: var(--lv-muted); }
.mo-opt-ctl { display: flex; flex-wrap: wrap; gap: var(--lv-sp-2); align-items: center; padding: 0 var(--lv-sp-3) var(--lv-sp-3); margin-top: calc(-1 * var(--lv-sp-1)); }
.mo-opt-ctl select { min-height: 28px; font-size: var(--lv-fs-sm); max-width: 100%; }
.mo-cmp-pick { display: flex; flex-wrap: wrap; gap: var(--lv-sp-1) var(--lv-sp-4); margin-bottom: var(--lv-sp-3); }
.mo-check { display: inline-flex; align-items: center; gap: 6px; font-size: var(--lv-fs-sm); min-height: var(--lv-hit, 28px); }
.mo-check input { width: auto; min-height: 0; margin: 0; }
.mo-check.off { color: var(--lv-muted); }
.mo-cmp { width: 100%; border-collapse: collapse; font-size: var(--lv-fs-sm); font-variant-numeric: tabular-nums; }
.mo-cmp th, .mo-cmp td { padding: 5px 8px; text-align: right; border-bottom: 1px solid var(--lv-line-soft); white-space: nowrap; }
.mo-cmp th:first-child, .mo-cmp td:first-child { text-align: left; white-space: normal; }
.mo-cmp thead th { font-size: var(--lv-fs-xs); font-weight: var(--lv-fw-bold); }
.mo-cmp tr.grp th { text-align: left; font-size: var(--lv-fs-2xs); text-transform: uppercase; letter-spacing: var(--lv-track-kicker); color: var(--lv-muted); background: var(--lv-band-bg); }
.mo-cmp tr.me th, .mo-cmp tr.me td { font-weight: var(--lv-fw-heavy); background: var(--lv-hl-bg); }
.mo-cmp td small { display: block; font-size: var(--lv-fs-xs); font-weight: var(--lv-fw-semi); }
.mo-cmp td small.up { color: var(--lv-good); }
.mo-cmp td small.down { color: var(--lv-neg); }
.mo-cmp td.neg { color: var(--lv-neg); }
.mo-scroll { overflow-x: auto; }
.mo-open { padding: var(--lv-sp-5); }
@media (max-width: 1179px) {
  .mo-layout { grid-template-columns: minmax(0, 1fr); }
  .mo-outputs { order: -1; position: sticky; top: 42px; z-index: 6; max-height: none; overflow: visible; }
  .mo-outputs > .pg-card { padding: var(--lv-sp-2) var(--lv-sp-3); box-shadow: var(--lv-shadow-2); }
  .mo-sum { display: block; }
  .mo-body > summary { display: list-item; cursor: pointer; font-size: var(--lv-fs-xs); font-weight: var(--lv-fw-semi); color: var(--lv-link); padding: 4px 0; min-height: var(--lv-hit, 28px); }
  .mo-body[open] { max-height: min(70vh, 640px); overflow-y: auto; }
  .mo-card-head h2 { font-size: var(--lv-fs-sm); }
}
@media (max-width: 900px) { .mo-opts { grid-template-columns: minmax(0, 1fr); } }
@media (max-width: 640px) { .mo-field input[type="text"] { width: 100px; } .mo-tool { padding: var(--lv-sp-2); } .mo-years th, .mo-years td { padding: 5px 4px; } .mo-years th:first-child { max-width: 104px; } .mo-years thead th small { max-width: 9ch; } }
`;

function ensureStyle() {
  if (document.getElementById(STYLE_ID)) return;
  const s = document.createElement('style');
  s.id = STYLE_ID;
  s.textContent = CSS;
  document.head.append(s);
}

// A scenario chosen on purpose from the bar (a change event in the last few seconds) is kept; arriving on this page from anywhere else on the plan of record opens the working copy.
let lastPick = 0;
scenarios.on('change', () => { lastPick = Date.now(); });
const LS_COMPARE = 'projection.modeler.compare';
const lsGet = (k) => { try { return localStorage.getItem(k); } catch { return null; } };
const lsSet = (k, v) => { try { localStorage.setItem(k, v); } catch { /* private window */ } };

const ROWS = [
  { id: 'spend_total', key: 'drivers.spend_total', label: 'Ad spend', fmt: 'usd0' },
  { id: 'nc_aov', key: 'drivers.nc_aov', label: 'Average order value', fmt: 'usd' },
  { id: 'nc_cpa', key: 'drivers.nc_cpa', label: 'Cost per new customer', fmt: 'usd' },
];
const ROW_BY_KEY = Object.fromEntries(ROWS.map((r) => [r.key, r]));
const OPS = [
  ['fill', 'Set to a value'], ['percent', 'Change by a percent'], ['add', 'Raise or cut by an amount'],
  ['ramp', 'Ramp to a value'], ['copy', "Repeat one month's value"], ['reset', 'Back to the plan of record'],
];
const SOURCE_NAME = { current: 'Current', goal: 'Goal', asof: 'As of month' };
const SOURCE_DESC = {
  current: 'The curve the latest cohorts are on.',
  goal: 'The saved target curve.',
  asof: 'The current method on the cohort table as it stood at the end of an earlier month.',
};

export async function render(el, ctx) {
  ensureStyle();
  const { h, fmt, ui, charts, toast } = ctx;
  const root = h('div', { class: 'pg-page mo-page', 'data-testid': 'modeler' });
  el.replaceChildren(root);

  // ---------------------------------------------------------------- open on the working copy, not on the plan of record
  if (ctx.scenarioInfo.is_default && Date.now() - lastPick > 3000) {
    root.replaceChildren(h('div', { class: 'pg-card mo-open', role: 'status' }, 'Opening your working copy, "Modeler", so the plan of record stays as it is.'));
    try {
      const r = await ctx.api.post('modeler/workspace', {});
      if (!ctx.alive()) return undefined;
      await scenarios.refresh();
      if (r.created) toast('Created "Modeler", a copy of the plan of record. Changes here do not touch the plan of record.', { kind: 'ok', ms: 8000 });
      scenarios.select(r.scenario.slug);
      return undefined;
    } catch (e) { ui.error(root, e, ctx.reload); return undefined; }
  }

  ui.loading(root, 'Loading the model');
  let state;
  try { state = await ctx.api.get('modeler/state'); } catch (e) { ui.error(root, e, ctx.reload); return undefined; }
  if (!ctx.alive()) return undefined;

  const slug = ctx.scenario;
  const isPor = !!state.scenario.is_default;
  const S = {
    state, pending: new Map(), retDraft: null, retSaved: null, por: (state.por && state.por.outputs) || null,
    opts: null, cmp: null, cmpSel: [], cmpData: new Map(), tool: { row: 'spend_total', op: 'fill', from: '', to: '', value: '', pct: '', amount: '', end: '', start: '' },
    toolRes: null, toolErr: null, dirty: 0, showingDraft: false, retPrev: null,
  };
  const cleanups = [];
  const timers = new Set();
  const mon = (iso) => fmt.monthLabel(iso);
  const yy = (y) => `FY${String(y).slice(2)}`;
  const star = (yr) => (yr && yr.status !== 'actual' ? '*' : '');
  const money = (v, d = 0) => (v == null ? '' : fmt.money(v, { digits: d }));
  const compact = (v) => fmt.compact(v);
  const signed = (v) => (v == null ? '' : `${v < 0 ? '-' : '+'}${compact(Math.abs(v))}`);
  const rowOf = (p, key) => p.rows.find((r) => r.key === key);

  // ---------------------------------------------------------------- the server, one request at a time
  const stateEl = h('span', { class: 'mo-state', id: 'mo-status', role: 'status', 'aria-live': 'polite' });
  function setStatus(text, kind = '') { stateEl.textContent = text; stateEl.className = 'mo-state ' + kind; }

  function previewBody(extra = {}) {
    const edits = [...S.pending.values()].map((e) => ({ line_key: e.line_key, month: e.month, value: e.value }));
    const body = { scenario: slug, preview: true, edits, ...extra };
    const ret = extra.retention !== undefined ? extra.retention : S.retDraft;
    if (ret) body.retention = ret; else delete body.retention;
    return body;
  }
  let running = false;
  let nextBody = null;
  let waiters = [];
  function preview(body) {                                  // one preview in flight; newer requests replace the waiting one and all callers get the last answer
    return new Promise((resolve, reject) => {
      waiters.push({ resolve, reject });
      nextBody = body;
      if (!running) pump();
    });
  }
  async function pump() {
    running = true;
    while (nextBody) {
      const body = nextBody;
      nextBody = null;
      const ws = waiters;
      waiters = [];
      try { const out = await ctx.api.post('modeler/preview', body); ws.forEach((w) => w.resolve(out)); } catch (e) { ws.forEach((w) => w.reject(e)); }
    }
    running = false;
  }
  const friendly = (e) => (e && e.code === 'rate_limited' ? 'Too many changes in a short time. The numbers will update again in a minute.' : (e && e.message) || 'Something went wrong');

  // ---------------------------------------------------------------- header, cue, TLDR
  const headerHost = h('div');
  const cueHost = h('div');
  function headlineYear() {
    const o = S.state.outputs;
    return o.years.find((y) => y.year === o.headline_year) || o.years[o.years.length - 1];
  }
  function porYear(year) { return S.por && S.por.years.find((y) => y.year === year); }
  function drawHeader() {
    const o = S.state.outputs;
    const hy = headlineYear();
    const avg = o.averages;
    let line = 'No results yet. Type a monthly amount in a yellow row.';
    const kpis = [];
    if (hy) {
      const a = avg && avg.cpa != null && avg.aov != null ? `${avg.varies ? 'On average ' : 'At '}${compact(avg.spend_per_month)} a month, ${money(avg.cpa, Number.isInteger(Math.round(avg.cpa * 100) / 100) ? 0 : 2)} CPA and ${money(avg.aov, Number.isInteger(Math.round(avg.aov * 100) / 100) ? 0 : 2)} AOV, ` : '';
      line = `${a}${yy(hy.year)} revenue is ${compact(hy.total_revenue)}${star(hy)} and EBITDA ${compact(hy.ebitda)}${star(hy)}.`;
      const por = porYear(hy.year);
      const vs = (v, p) => (por && !isPor && p != null ? (Math.abs(v - p) < 0.5 ? 'same as plan' : `${signed(v - p)} vs plan`) : '');
      kpis.push({ label: `${yy(hy.year)} revenue`, value: compact(hy.total_revenue) + star(hy), note: vs(hy.total_revenue, por && por.total_revenue) });
      kpis.push({ label: `${yy(hy.year)} EBITDA`, value: compact(hy.ebitda) + star(hy), note: vs(hy.ebitda, por && por.ebitda) });
      kpis.push({ label: 'EBITDA margin', value: hy.ebitda_margin == null ? 'none' : fmt.pct(hy.ebitda_margin, 1) + star(hy) });
    }
    const lc = o.lowest_cash;
    if (lc) kpis.push({ label: 'Lowest cash balance', value: compact(lc.value) + '*', note: mon(lc.month) + '*', tone: lc.value < 0 ? 'bad' : '' });
    const r = S.state.retention;
    kpis.push({ label: 'Repeat revenue per first order', value: fmt.x(r.lifetime, 2), note: 'lifetime' });
    headerHost.replaceChildren(ui.pageHeader({
      title: 'Scenario modeler',
      purpose: 'Try a spend plan, an average order value (AOV), a cost per new customer (CPA) and a retention model, and watch yearly revenue and EBITDA (earnings before interest, taxes, depreciation and amortization) move.',
      actions: [
        h('button', { type: 'button', class: 'btn', id: 'mo-save-as', onclick: saveAs }, 'Save as scenario'),
        isPor ? null : h('button', { type: 'button', class: 'btn', id: 'mo-reset', onclick: resetToPor }, 'Reset to plan of record'),
      ],
      tldr: { line, kpis },
    }));
  }
  function drawCue() {
    const sc = S.state.scenario;
    const ret = S.state.retention;
    cueHost.replaceChildren(h('div', { class: 'mo-cue' + (isPor ? '' : ' copy'), role: 'note', id: 'mo-cue' },
      ui.chip(isPor ? 'Plan of record' : 'Working copy', isPor ? 'neutral' : 'warn'),
      h('span', { class: 'who' }, `Modeling on: ${sc.name}`),
      h('span', { class: 'muted', id: 'mo-version' }, `v${scenarios.version ?? ctx.scenarioVersion}`),
      h('span', { class: 'grow' }, isPor
        ? 'This is the plan of record. Your first change asks whether to work on a copy instead.'
        : 'Changes here save to this copy. The plan of record does not change.'),
      h('span', { id: 'mo-retention-line' }, 'Repeat revenue uses ', h('b', ret.label || 'the current curve'), '.')));
  }

  // ---------------------------------------------------------------- outputs panel
  const outHost = h('aside', { class: 'mo-outputs', 'aria-label': 'Results by year', 'data-testid': 'outputs' });
  let chart = null;
  const chartCanvas = h('canvas', { role: 'img', 'aria-label': 'Total revenue and EBITDA by year' });
  const yearsWrap = h('div', { class: 'mo-years-wrap' });
  const memoHost = h('div');
  const sumEl = h('p', { class: 'mo-sum', id: 'mo-sum' });
  const body = h('details', { class: 'mo-body', open: true }, h('summary', null, 'Yearly results'), yearsWrap, memoHost, h('div', { class: 'mo-chart' }, chartCanvas));
  const narrow = window.matchMedia('(max-width: 1179px)');
  if (narrow.matches) body.open = false;
  const tight = () => yearsWrap.clientWidth > 0 && yearsWrap.clientWidth < 470;
  const mil = (v) => `${v < 0 ? '-' : ''}$${(Math.abs(v) / 1e6).toFixed(2)}M`;
  const yearNum = (v) => (tight() ? mil(v) : fmt.money(v, { parens: true }));
  const yearTitle = (v) => fmt.money(v, { digits: 0 });

  function drawOutputs() {
    const o = S.state.outputs;
    const years = o.years;
    const por = S.por;
    const th = (y) => h('th', { scope: 'col', class: y.status === 'actual' ? '' : 'proj' }, `${y.label}${star(y)}`, y.sub && y.status === 'partial' ? h('small', null, y.sub) : null);
    const cell = (y, v, cls = '', f = yearNum) => h('td', { class: [cls, v < 0 ? 'neg' : '', y.status === 'actual' ? '' : 'proj'].filter(Boolean).join(' '), title: v == null ? null : yearTitle(v) }, v == null ? '' : f(v));
    const tr = (cls, label, pick, f) => h('tr', { class: cls }, h('th', { scope: 'row' }, label), years.map((y) => cell(y, pick(y), '', f)));
    const pctFmt = (v) => fmt.pct(v, 1);
    const rows = [
      tr('mo-strong', 'Total revenue', (y) => y.total_revenue),
      tr('mo-sub', 'New-customer revenue', (y) => y.nc_rev),
      tr('mo-sub', 'Repeat revenue', (y) => y.repeat_rev),
      tr('mo-sub', 'Amazon and TikTok, less sales tax estimate', (y) => y.other_rev),
      tr('', 'Ad spend', (y) => y.ad_spend),
      tr('mo-strong', 'EBITDA', (y) => y.ebitda),
      h('tr', { class: 'mo-sub' }, h('th', { scope: 'row' }, 'EBITDA margin'), years.map((y) => h('td', { class: y.status === 'actual' ? '' : 'proj' }, y.ebitda_margin == null ? '' : pctFmt(y.ebitda_margin)))),
    ];
    if (por && !isPor) {
      const diff = (pick) => (y) => { const p = por.years.find((q) => q.year === y.year); return p ? pick(y) - pick(p) : null; };
      const dtr = (label, pick) => h('tr', { class: 'mo-vs' }, h('th', { scope: 'row' }, label), years.map((y) => {
        const d = diff(pick)(y);
        return h('td', { class: d == null ? '' : d < -0.5 ? 'down' : d > 0.5 ? 'up' : '', title: d == null ? null : yearTitle(d) }, d == null ? '' : (Math.abs(d) < 0.5 ? '—' : `${d < 0 ? '-' : '+'}${yearNum(Math.abs(d))}`));
      }));
      rows.push(dtr('Revenue vs plan of record', (y) => y.total_revenue), dtr('EBITDA vs plan of record', (y) => y.ebitda));
    }
    yearsWrap.replaceChildren(h('table', { class: 'mo-years', 'data-testid': 'years', 'aria-label': 'Results by year' },
      h('thead', null, h('tr', null, h('th', { scope: 'col' }, ''), years.map(th))), h('tbody', null, rows)));
    const lc = o.lowest_cash;
    const addbacks = o.ebitda.addbacks;
    const choices = S.state.settings.ebitda_choices;
    const preset = addbacks.length === choices.length || (addbacks.includes('marketing_influencers') && addbacks.includes('uncategorized')) ? 'pacing' : 'interest';
    const sel = h('select', { id: 'mo-ebitda-def', 'aria-label': 'EBITDA definition', onchange: (e) => setAddbacks(e.target.value === 'pacing' ? choices : ['interest_expense']) },
      h('option', { value: 'interest', selected: preset === 'interest' }, 'Before interest expense'),
      h('option', { value: 'pacing', selected: preset === 'pacing' }, 'Same as Pacing'));
    memoHost.replaceChildren(
      h('p', { class: 'mo-memo', id: 'mo-cash' }, 'Lowest cash balance: ',
        lc ? h('b', { class: lc.value < 0 ? 'neg' : '' }, `${fmt.money(lc.value, { parens: false })}*`) : h('b', null, 'not available'),
        lc ? ` in ${mon(lc.month)}*.` : '.'),
      h('div', { class: 'mo-ebitda' }, h('span', { id: 'mo-ebitda-text' }, o.ebitda.text), sel),
      ui.source('Figures are the P&L page\'s fiscal-year columns. Projected values are marked *.'));
    const hy = headlineYear();
    sumEl.replaceChildren(hy ? h('span', null, `${yy(hy.year)}${star(hy)} revenue `, h('b', null, compact(hy.total_revenue)), ' · EBITDA ', h('b', null, compact(hy.ebitda))) : 'No results yet');
    drawChart();
  }
  function drawChart() {
    if (chart) { charts.destroy(chart); chart = null; }
    const years = S.state.outputs.years;
    if (!years.length || typeof Chart === 'undefined') return;
    const cs = getComputedStyle(document.documentElement);
    const ink = cs.getPropertyValue('--lv-viz-ink').trim();
    const months = years.map((y) => `${y.year}-12-01`);
    chart = charts.barChart(chartCanvas, {
      months, status: years.map((y) => (y.status === 'actual' ? 'actual' : 'projected')), yFmt: 'usd0', beginAtZero: true,
      series: [{ label: 'Total revenue', values: years.map((y) => y.total_revenue), color: ink || undefined }, { label: 'EBITDA', values: years.map((y) => y.ebitda), color: charts.PALETTE[3] }],
    });
    chart.data.labels = years.map((y) => `${y.label}${star(y)}`);
    chart.update('none');
  }
  outHost.append(h('div', { class: 'pg-card mo-card' },
    h('div', { class: 'mo-card-head' }, h('h2', null, 'Results by year'), stateEl),
    sumEl, body));
  let lastW = 0;
  const ro = typeof ResizeObserver === 'function' ? new ResizeObserver(debounce(() => { const w = Math.round(yearsWrap.clientWidth); if (S.state && w !== lastW) { lastW = w; drawOutputs(); } }, 120)) : null;
  if (ro) { ro.observe(yearsWrap); cleanups.push(() => ro.disconnect()); }

  // ---------------------------------------------------------------- the grid
  const gridHost = h('div');
  let grid = null;
  const slideKind = (key, month) => {
    const rs = (S.state.levers || {}).roas_slide || {};
    return key === 'drivers.nc_cpa' && rs.enabled && month >= rs.from && month <= rs.to ? 'override' : 'input';
  };
  const codeAt = (key, month) => ((S.state.cellmeta || {})[key] || {})[month];
  function highlight(cell) {
    const code = codeAt(cell.key, cell.month);
    if (!ROW_BY_KEY[cell.key]) return null;
    return code === 'actual' ? 'locked' : code === 'carried' || code === 'slid' ? 'info' : null;
  }
  function applyState(next, { keepGrid = false } = {}) {
    S.state = next;
    S.showingDraft = false;
    if (next.por) S.por = next.por.outputs;
    drawHeader();
    drawCue();
    drawOutputs();
    if (grid && !keepGrid) grid.update(next);
  }

  // save plan cells through the core path (audited, versioned, restorable); the preview already shows them
  function queueSave(edits) {
    for (const e of edits) S.pending.set(e.line_key + '|' + e.month, { line_key: e.line_key, month: e.month, value: e.value });
    setStatus('Saving…', 'busy');
    S.dirty++;
    const items = edits.map((e) => ({ line_key: e.line_key, month: e.month, value: e.value, kind: slideKind(e.line_key, e.month) }));
    return ctx.api.saveSeries(items).then(() => {
      for (const e of edits) {
        const k = e.line_key + '|' + e.month;
        const p = S.pending.get(k);
        if (p && p.value === e.value) S.pending.delete(k);
      }
      S.dirty--;
      if (!S.dirty) setStatus('Saved', '');
    }).catch(async (err) => {
      S.dirty--;
      for (const e of edits) S.pending.delete(e.line_key + '|' + e.month);
      setStatus('Not saved', 'err');
      toast(`Not saved: ${friendly(err)}`, 'error');
      try { applyState(await ctx.api.get('modeler/state')); } catch { /* the error toast already said it */ }
    });
  }

  async function onEdit(edits) {
    const locked = edits.find((e) => codeAt(e.line_key, e.month) === 'actual');
    if (locked) throw new Error(`${mon(locked.month)} holds loaded figures from the books. Only planned months can change.`);
    const body = previewBody();
    const merged = new Map(S.pending);
    for (const e of edits) merged.set(e.line_key + '|' + e.month, { line_key: e.line_key, month: e.month, value: e.value });
    body.edits = [...merged.values()];
    let out;
    try { out = await preview(body); } catch (e) { setStatus('Not applied', 'err'); throw new Error(friendly(e)); }
    queueSave(edits);
    applyState(out, { keepGrid: true });
    return out;                                          // the grid re-renders with the derived rows
  }

  function drawGrid() {
    grid = ctx.grid(gridHost, { payload: S.state, onEdit, highlight, title: 'Plan inputs by month', groupCollapse: false,
      onSelect: () => { try { fillToolFromSelection(); } catch { /* the form is optional */ } } });
    cleanups.push(() => grid && grid.destroy());
    requestAnimationFrame(() => {                       // open on the first planned month, not on January
      const sc = gridHost.querySelector('.pg-scroll');
      const bound = sc && sc.querySelector('th.pg-bound');
      if (sc && bound) sc.scrollLeft = Math.max(0, bound.offsetLeft - 330);
    });
  }

  // ---------------------------------------------------------------- quick edits
  const toolHost = h('div', { class: 'mo-tool', id: 'mo-tool', role: 'group', 'aria-label': 'Quick edits' });
  const toolText = h('p', { class: 'mo-tool-text', id: 'mo-tool-text', role: 'status', 'aria-live': 'polite' });
  const applyBtn = h('button', { type: 'button', class: 'btn primary', id: 'mo-apply', disabled: true, onclick: () => applyTool() }, 'Apply');
  const T = S.tool;
  const editableMonths = (rowId) => {
    const key = ROWS.find((r) => r.id === rowId).key;
    const codes = (S.state.cellmeta || {})[key] || {};
    return S.state.months.filter((m) => codes[m] !== 'actual');
  };
  function ensureToolMonths() {
    const ms = editableMonths(T.row);
    if (!ms.length) return;
    if (!ms.includes(T.from)) T.from = ms[0];
    if (!ms.includes(T.to)) T.to = ms[ms.length - 1];
    if (T.to < T.from) T.to = ms[ms.length - 1];
  }
  const NEEDS = {
    fill: [['value', 'Value']], percent: [['pct', 'Percent']], add: [['amount', 'Amount']], ramp: [['end', 'End value'], ['start', 'Start value (optional)']],
    copy: [], reset: [],
  };
  function toolOp() {
    const num = (s) => { const p = fmt.parse(s, 'usd0'); return p.ok ? p.value : NaN; };
    const op = { type: T.op, row: T.row, from: T.from, to: T.to };
    if (T.op === 'fill') op.value = num(T.value);
    if (T.op === 'percent') { const p = fmt.parse(String(T.pct).replace('%', ''), 'usd0'); op.pct = p.ok ? p.value : NaN; }
    if (T.op === 'add') op.amount = num(T.amount);
    if (T.op === 'ramp') { op.end_value = num(T.end); if (String(T.start).trim() !== '') op.start_value = num(T.start); }
    if (T.op === 'copy') { op.month = T.from; delete op.from; }
    for (const [k, v] of Object.entries(op)) if (typeof v === 'number' && !Number.isFinite(v)) return { error: 'Enter a number.' };
    const need = (NEEDS[T.op] || []).filter(([f]) => f !== 'start');
    if (need.some(([f]) => String(T[f]).trim() === '')) return { error: '' };
    return { op };
  }
  const dryTool = debounce(async () => {
    const { op, error } = toolOp();
    S.toolRes = null;
    if (!op) { toolText.className = 'mo-tool-text' + (error ? ' err' : ''); toolText.textContent = error || 'Choose what to change and how.'; applyBtn.disabled = true; restoreSaved(); return; }
    toolText.className = 'mo-tool-text';
    toolText.textContent = 'Checking…';
    try {
      const out = await preview(previewBody({ op }));
      if (!ctx.alive()) return;
      S.toolRes = out;
      toolText.textContent = out.applied.text;
      applyBtn.disabled = false;
      showDraft(out);
    } catch (e) {
      toolText.className = 'mo-tool-text err';
      toolText.textContent = friendly(e);
      applyBtn.disabled = true;
      restoreSaved();
    }
  }, 280);
  // outputs follow the form while it is being filled in ("if applied"); the grid keeps the saved numbers until Apply
  function showDraft(out) {
    S.state = { ...S.state, outputs: out.outputs, retention: out.retention };
    S.showingDraft = true;
    drawHeader(); drawOutputs();
    setStatus('Previewing the change below. Apply to keep it.', 'busy');
  }
  async function restoreSaved() {
    if (!S.showingDraft) return;
    try {
      const out = await preview(previewBody());
      applyState(out, { keepGrid: true });
      setStatus(S.dirty ? 'Saving…' : '', '');
    } catch { /* the form text already says what is wrong */ }
  }
  async function applyTool() {
    const { op } = toolOp();
    if (!op) return;
    applyBtn.disabled = true;
    try {
      const out = await preview(previewBody({ op }));
      const edits = out.applied.edits;
      const before = out.applied.edits.map((e) => ({ line_key: e.line_key, month: e.month, previous: valueAt(S.state, e.line_key, e.month) }));
      const prev = Object.fromEntries(before.map((b) => [b.line_key + '|' + b.month, b.previous]));
      await commitEdits(edits.map((e) => ({ line_key: e.line_key, month: e.month, value: e.value, previous: prev[e.line_key + '|' + e.month] })), out);
      const skipped = out.applied.skipped.length;
      toast(`${out.applied.text}`, { kind: 'ok', action: { label: 'Undo', onClick: () => grid && grid.undo() } });
      if (skipped) setStatus(`Skipped ${skipped} closed month${skipped === 1 ? '' : 's'}.`, '');
    } catch (e) {
      toolText.className = 'mo-tool-text err';
      toolText.textContent = friendly(e);
    } finally { applyBtn.disabled = false; dryTool(); }
  }
  const valueAt = (p, key, month) => { const r = rowOf(p, key); const i = p.months.indexOf(month); return r && i >= 0 ? r.values[i] : null; };
  // show the new numbers, record the change on the grid's undo stack, and save
  async function commitEdits(edits, out) {
    applyState(out);
    if (grid) {
      grid.undoStack.push(edits.map((e) => ({ line_key: e.line_key, month: e.month, value: e.value, previous: e.previous })));
      if (grid.undoStack.length > 50) grid.undoStack.shift();
      if (typeof grid._syncToolbar === 'function') grid._syncToolbar();
    }
    await queueSave(edits);
  }
  function fillToolFromSelection() {
    if (!grid) return;
    const sel = grid.getSelection();
    const row = sel.rows.map((k) => ROW_BY_KEY[k]).find(Boolean);
    if (!row) return;
    T.row = row.id;
    const ms = sel.months.slice().sort();
    const editable = new Set(editableMonths(T.row));
    const mine = ms.filter((m) => editable.has(m));
    if (mine.length) { T.from = mine[0]; if (mine.length > 1) T.to = mine[mine.length - 1]; }
    drawTool();
    dryTool();
  }
  function drawTool() {
    ensureToolMonths();
    const months = editableMonths(T.row);
    const monthSel = (id, label, value, onchange) => h('div', { class: 'mo-field sel' }, h('label', { for: id }, label),
      h('select', { id, onchange: (e) => { onchange(e.target.value); dryTool(); } }, months.map((m) => h('option', { value: m, selected: m === value }, mon(m)))));
    const inputs = (NEEDS[T.op] || []).map(([f, label]) => {
      const id = `mo-f-${f}`;
      const inp = h('input', { type: 'text', id, inputmode: 'decimal', autocomplete: 'off', value: T[f], 'aria-label': label,
        placeholder: f === 'pct' ? '10 or -15' : f === 'amount' ? '50000 or -5' : '', oninput: (e) => { T[f] = e.target.value; dryTool(); },
        onkeydown: (e) => { if (e.key === 'Enter') { e.preventDefault(); if (!applyBtn.disabled) applyTool(); } } });
      return h('div', { class: 'mo-field' }, h('label', { for: id }, label), inp);
    });
    const copy = T.op === 'copy';
    toolHost.replaceChildren(
      h('div', { class: 'mo-tool-row' },
        h('div', { class: 'mo-field sel' }, h('label', { for: 'mo-row' }, 'Row'),
          h('select', { id: 'mo-row', onchange: (e) => { T.row = e.target.value; drawTool(); dryTool(); } }, ROWS.map((r) => h('option', { value: r.id, selected: r.id === T.row }, r.label)))),
        h('div', { class: 'mo-field sel' }, h('label', { for: 'mo-op' }, 'Change'),
          h('select', { id: 'mo-op', onchange: (e) => { T.op = e.target.value; drawTool(); dryTool(); } }, OPS.map(([v, l]) => h('option', { value: v, selected: v === T.op }, l)))),
        monthSel('mo-from', copy ? 'Copy from' : 'From', T.from, (v) => { T.from = v; if (T.to < T.from) T.to = T.from; }),
        monthSel('mo-to', copy ? 'Through' : 'Through', T.to, (v) => { T.to = v; }),
        inputs, applyBtn),
      toolText,
      h('p', { class: 'mo-hint' }, 'Select cells in the grid to fill in the months. Shift-click or Shift-arrow selects a range. Paste a row from Sheets into a yellow cell. Closed months are skipped.'));
  }

  // ---------------------------------------------------------------- retention model
  const retHost = h('section', { class: 'pg-card mo-card', 'aria-label': 'Retention model', id: 'mo-retention' });
  async function loadOptions({ n, asof } = {}) {
    const cur = S.state.retention;
    const nUse = n || (S.cmpParams && S.cmpParams.n) || cur.n_cohorts || 3;
    try {
      const options = await ctx.api.get('cohorts/curve-options', { n: nUse });
      S.opts = options;
      const want = asof || (S.cmpParams && S.cmpParams.asof) || cur.asof_month || options.default_asof;
      const computable = new Set((options.asof_months || []).filter((m) => m.computable).map((m) => m.month));
      const asofUse = computable.has(want) ? want : (options.default_asof || null);
      const params = { n: nUse };
      if (asofUse) params.asof = asofUse;
      S.cmp = await ctx.api.get('cohorts/curve-compare', params);
      S.cmpParams = { n: nUse, asof: asofUse };
      S.cmpErr = null;
    } catch (e) { S.cmp = null; S.cmpErr = e; }
  }
  function drawRetention() {
    const cur = S.state.retention;
    const sourceKey = cur.curve_source === 'asof' ? 'asof' : cur.curve_source === 'goal' ? 'goal' : 'current';
    const byKey = {};
    if (S.cmp) for (const o of S.cmp.options) byKey[o.key === 'asof2' ? 'asof2' : o.key] = o;
    const months = (S.opts && S.opts.asof_months || []).filter((m) => m.computable);
    const nChoices = (S.opts && S.opts.n_choices) || [3, 6];
    const asofValue = (S.cmpParams && S.cmpParams.asof) || cur.asof_month || (S.opts && S.opts.default_asof) || (months[months.length - 1] || {}).month;
    const nValue = (S.cmpParams && S.cmpParams.n) || cur.n_cohorts || 3;
    const card = (key) => {
      const o = byKey[key];
      const active = sourceKey === key;
      const pick = () => chooseRetention(key);
      const ctl = key === 'asof' || key === 'current' ? h('div', { class: 'mo-opt-ctl' }, [
        key === 'asof' && months.length ? h('select', { 'aria-label': 'Month the curve is taken at the end of', id: 'mo-asof', onchange: (e) => { changeAsof(e.target.value); } }, months.map((m) => h('option', { value: m.month, selected: m.month === asofValue }, m.is_latest ? `${m.label} (latest)` : m.label))) : null,
        h('span', { class: 'segs', role: 'group', 'aria-label': `Cohorts averaged, ${SOURCE_NAME[key]}` }, nChoices.map((n) => h('button', { type: 'button', class: 'seg', 'aria-pressed': String(n === nValue), 'data-n': n,
          onclick: () => changeN(n, key) }, `${n} cohorts`))),
      ]) : null;
      return h('div', { class: 'mo-opt', 'data-key': key, 'data-active': String(active) },
        h('button', { type: 'button', class: 'mo-opt-pick', role: 'radio', 'aria-checked': String(active), 'aria-label': `${SOURCE_NAME[key]} retention model`, onclick: pick },
          h('span', { class: 't' }, SOURCE_NAME[key], active ? ui.chip('In use', 'good') : null),
          h('span', { class: 'm' }, o ? [h('b', null, fmt.x(o.lifetime, 2)), ' lifetime · ', h('b', null, fmt.x(o.twelve, 2)), ' first 12 months'] : 'Not available'),
          h('span', { class: 'd' }, key === 'goal' && S.cmp && S.cmp.options.find((x) => x.key === 'goal') ? S.cmp.options.find((x) => x.key === 'goal').label : SOURCE_DESC[key])),
        ctl);
    };
    retHost.replaceChildren(
      h('div', { class: 'mo-card-head' }, h('h2', null, 'Retention model'), h('span', { class: 'mo-state' }, 'Repeat revenue each cohort keeps producing, as a multiple of its first order.')),
      S.cmp ? h('div', { class: 'mo-opts', role: 'radiogroup', 'aria-label': 'Retention model' }, ['current', 'goal', 'asof'].map(card))
        : h('div', { class: 'empty' }, 'The retention options could not load. The model keeps using the current choice: ', h('b', null, cur.label || 'Current'), '.'),
      h('p', { class: 'mo-hint', style: { marginTop: 'var(--lv-sp-2)' } }, `Saved in ${S.state.scenario.name} only. Each choice runs the whole model, so the figures above change at once.`),
      ui.source('Cohort exports through the latest closed month. Lifetime is the sum of the curve over 60 months; first 12 months is the sum of its first 12.'));
  }
  const leversFor = (key, asof, n) => (key === 'goal' ? { curve_source: 'goal' } : key === 'asof' ? { curve_source: 'asof', asof_month: asof, n_cohorts: n } : { curve_source: 'live', n_cohorts: n });
  async function chooseRetention(key, over = {}) {
    const asof = over.asof || (S.cmpParams && S.cmpParams.asof) || S.state.retention.asof_month || (S.opts && S.opts.default_asof);
    const n = over.n || (S.cmpParams && S.cmpParams.n) || S.state.retention.n_cohorts || 3;
    if (key === 'asof' && !asof) { toast('There is not enough cohort history to build an as-of curve yet.', 'error'); return; }
    const levers = leversFor(key, asof, n);
    if (!over.undo) { const c = S.state.retention; S.retPrev = { key: c.curve_source === 'asof' ? 'asof' : c.curve_source === 'goal' ? 'goal' : 'current', asof: c.asof_month, n: c.n_cohorts }; }
    const draft = { curve_source: levers.curve_source, ...(levers.asof_month ? { asof_month: levers.asof_month } : {}), ...(levers.n_cohorts ? { n_cohorts: levers.n_cohorts } : {}) };
    setStatus('Saving…', 'busy');
    let out;
    try { out = await preview(previewBody({ retention: draft })); } catch (e) { setStatus('Not applied', 'err'); toast(friendly(e), 'error'); return; }
    S.retDraft = draft;
    applyState(out);
    drawRetention();
    try {
      await ctx.api.put('cohorts/levers', { scenario: slug, levers });
      S.retDraft = null;
      setStatus('Saved', '');
      toast(`Retention model: ${out.retention.label}.`, { kind: 'ok', action: { label: 'Undo', onClick: () => undoRetention() } });
    } catch (e) {
      S.retDraft = null;
      setStatus('Not saved', 'err');
      toast(`Not saved: ${friendly(e)}`, 'error');
    }
    try { applyState(await ctx.api.get('modeler/state')); } catch { /* keep what is shown */ }
    await loadOptions();
    drawRetention();
  }
  async function undoRetention() {
    const prev = S.retPrev;
    if (!prev) return;
    await chooseRetention(prev.key, { asof: prev.asof, n: prev.n, undo: true });
  }
  async function changeAsof(month) {
    S.cmpParams = { ...(S.cmpParams || {}), asof: month };
    if (S.state.retention.curve_source === 'asof') { await chooseRetention('asof', { asof: month }); return; }
    await loadOptions({ asof: month, n: S.cmpParams.n });
    drawRetention();
  }
  async function changeN(n, key) {
    S.cmpParams = { ...(S.cmpParams || {}), n };
    const active = S.state.retention.curve_source;
    if ((active === 'live' && key === 'current') || (active === 'asof' && key === 'asof')) { await chooseRetention(key, { n }); return; }
    await loadOptions({ n, asof: S.cmpParams.asof });
    drawRetention();
  }

  // ---------------------------------------------------------------- compare
  const cmpHost = h('section', { class: 'pg-card mo-card', 'aria-label': 'Compare scenarios', id: 'mo-compare' });
  const allScenarios = () => scenarios.list.filter((s) => s.slug !== slug);
  function loadSel() {
    let saved = [];
    try { saved = JSON.parse(lsGet(LS_COMPARE) || '[]'); } catch { saved = []; }
    const have = new Set(allScenarios().map((s) => s.slug));
    let sel = saved.filter((s) => have.has(s));
    if (!sel.length) { const por = allScenarios().find((s) => s.is_default); if (por) sel = [por.slug]; }
    return sel.slice(0, 3);
  }
  S.cmpSel = loadSel();
  async function fetchCompare() {
    const want = S.cmpSel.filter((s) => !S.cmpData.has(s));
    if (!want.length) return;
    try {
      const r = await ctx.api.get('modeler/compare', { scenarios: want.join(',') });
      for (const c of r.scenarios) S.cmpData.set(c.scenario.slug, c);
    } catch (e) { toast(`Could not load the comparison: ${friendly(e)}`, 'error'); }
  }
  function cmpRows() {
    const me = { scenario: S.state.scenario, outputs: S.state.outputs, me: true };
    return [me, ...S.cmpSel.map((s) => S.cmpData.get(s)).filter(Boolean)];
  }
  function cmpTable() {
    const rows = cmpRows();
    const years = S.state.outputs.years;
    const block = (title, pick) => {
      const mine = rows[0];
      const trs = [h('tr', { class: 'grp' }, h('th', { colspan: years.length + 1 }, title))];
      for (const r of rows) {
        trs.push(h('tr', { class: r.me ? 'me' : '' },
          h('th', { scope: 'row' }, r.me ? `${r.scenario.name} (editing)` : r.scenario.name + (r.is_plan_of_record ? ' (plan of record)' : '')),
          years.map((y) => {
            const mineY = mine.outputs.years.find((q) => q.year === y.year);
            const rowY = r.outputs.years.find((q) => q.year === y.year);
            if (!rowY) return h('td');
            const v = pick(rowY);
            const d = r.me || !mineY ? null : v - pick(mineY);
            return h('td', { class: v < 0 ? 'neg' : '', title: fmt.money(v) }, compact(v) + star(y), d == null ? null : h('small', { class: Math.abs(d) < 0.5 ? '' : d < 0 ? 'down' : 'up' }, Math.abs(d) < 0.5 ? 'same' : `${signed(d)} vs ${mine.scenario.name}`));
          })));
      }
      return trs;
    };
    return h('table', { class: 'mo-cmp', 'data-testid': 'compare' },
      h('thead', null, h('tr', null, h('th', { scope: 'col' }, 'Scenario'), years.map((y) => h('th', { scope: 'col' }, `${y.label}${star(y)}`)))),
      h('tbody', null, block('Total revenue', (y) => y.total_revenue), block('EBITDA', (y) => y.ebitda)));
  }
  function cmpTSV() {
    const rows = cmpRows();
    const years = S.state.outputs.years;
    const out = [['Scenario', 'Measure', ...years.map((y) => y.label)].join('\t')];
    for (const [m, pick] of [['Total revenue', (y) => y.total_revenue], ['EBITDA', (y) => y.ebitda]]) {
      for (const r of rows) out.push([r.scenario.name, m, ...years.map((y) => { const q = r.outputs.years.find((x) => x.year === y.year); return q ? Math.round(pick(q)) : ''; })].join('\t'));
      for (const r of rows.slice(1)) out.push([`${r.scenario.name} minus ${rows[0].scenario.name}`, m, ...years.map((y) => {
        const a = r.outputs.years.find((x) => x.year === y.year), b = rows[0].outputs.years.find((x) => x.year === y.year);
        return a && b ? Math.round(pick(a) - pick(b)) : '';
      })].join('\t'));
    }
    return out.join('\n');
  }
  async function drawCompare() {
    const list = allScenarios();
    const pick = h('div', { class: 'mo-cmp-pick', role: 'group', 'aria-label': 'Scenarios to compare' }, list.map((s) => {
      const on = S.cmpSel.includes(s.slug);
      const full = !on && S.cmpSel.length >= 3;
      return h('label', { class: 'mo-check' + (full ? ' off' : '') },
        h('input', { type: 'checkbox', checked: on, disabled: full, 'data-slug': s.slug, onchange: async (e) => {
          S.cmpSel = e.target.checked ? [...S.cmpSel, s.slug].slice(0, 3) : S.cmpSel.filter((x) => x !== s.slug);
          lsSet(LS_COMPARE, JSON.stringify(S.cmpSel));
          await fetchCompare();
          drawCompare();
        } }), s.name + (s.is_default ? ' (plan of record)' : ''));
    }));
    await fetchCompare();
    cmpHost.replaceChildren(
      h('div', { class: 'mo-card-head' }, h('h2', null, 'Compare'),
        h('span', { class: 'pg-head-actions' },
          h('button', { type: 'button', class: 'btn small', id: 'mo-cmp-refresh', onclick: async () => { S.cmpData.clear(); await drawCompare(); } }, 'Refresh'),
          h('button', { type: 'button', class: 'btn small', id: 'mo-cmp-copy', onclick: async () => {
            try { await navigator.clipboard.writeText(cmpTSV()); toast('Copied the comparison as a table.', 'ok'); } catch { toast('Copy is blocked in this browser. Select the table and copy it by hand.', 'error'); }
          } }, 'Copy as table'))),
      list.length ? pick : h('div', { class: 'empty' }, 'There is no other scenario to compare with yet. Use Save as scenario to keep this one.'),
      list.length ? h('div', { class: 'mo-scroll' }, cmpTable()) : null,
      ui.source('Other scenarios as last saved; the first row follows what you are typing. Up to 3 can be shown. Projected values are marked *.'));
  }

  // ---------------------------------------------------------------- safety actions
  async function saveAs() {
    await askText({ title: 'Save as scenario', label: 'Name', value: `${S.state.scenario.name} ${new Date().toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}`, confirmText: 'Save',
      onSubmit: async (name) => {
        await flushSeries();
        const r = await ctx.api.post('modeler/save-as', { source: slug, name });
        await scenarios.refresh();
        toast(`Saved as "${r.scenario.name}". It keeps these numbers.`, { kind: 'ok', action: { label: 'Open', onClick: () => scenarios.select(r.scenario.slug) } });
        S.cmpData.delete(r.scenario.slug);
        drawCompare();
      } });
  }
  async function resetToPor() {
    const por = S.state.plan_of_record;
    const ok = await confirmBox({
      title: 'Reset to the plan of record?',
      message: `This sets ad spend, order value, cost per new customer and the retention model in "${S.state.scenario.name}" back to the plan of record's. Other settings stay. You can undo it from History.`,
      confirmText: 'Reset', danger: false,
      onConfirm: async () => { await flushSeries(); await ctx.api.post('modeler/reset', { scenario: slug }); },
    });
    if (!ok) return;
    S.pending.clear();
    toast(`Back to ${por.name}.`, 'ok');
    ctx.reload();
  }
  async function setAddbacks(list) {
    try {
      await ctx.api.put('modeler/settings', { scenario: slug, ebitda_addbacks: list });
      applyState(await ctx.api.get('modeler/state'));
      S.cmpData.clear();
    } catch (e) { toast(`Not saved: ${friendly(e)}`, 'error'); drawOutputs(); }
  }

  // ---------------------------------------------------------------- assemble
  const legend = h('div', { class: 'mo-legend' }, ui.chip('Carried', 'info'), h('span', null, 'a value carried from the month before; type a value to set it'),
    h('span', null, 'Grey cells are closed months from the books.'));
  const inputsCard = h('section', { class: 'pg-card mo-card', 'aria-label': 'Plan inputs' },
    h('div', { class: 'mo-card-head' }, h('h2', null, 'Plan inputs and what they produce')),
    toolHost, gridHost, legend);
  drawHeader();
  drawCue();
  drawTool();
  root.replaceChildren(headerHost, cueHost,
    h('div', { class: 'mo-layout' }, h('div', { class: 'mo-main' }, inputsCard, retHost, cmpHost), outHost));
  drawOutputs();
  drawGrid();
  dryTool();
  await loadOptions();
  if (!ctx.alive()) return undefined;
  drawRetention();
  drawCompare();

  const offVersion = ctx.onVersion((v) => { const e = document.getElementById('mo-version'); if (e) e.textContent = `v${v}`; });
  const offSaved = events.on('saved', ({ scenario }) => { if (scenario === slug) S.cmpData.clear(); });
  return () => {
    for (const t of timers) clearTimeout(t);
    dryTool.cancel();
    for (const c of cleanups) { try { c(); } catch { /* already gone */ } }
    if (chart) charts.destroy(chart);
    if (typeof offVersion === 'function') offVersion();
    if (typeof offSaved === 'function') offSaved();
  };
}
