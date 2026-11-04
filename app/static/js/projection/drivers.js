// Projection page: Spend plan.
// What it shows: how much is spent each month, what a new customer costs (CPA) and spends on a first order (AOV), the new-customer revenue that
// produces, and the revenue stack on top (repeat, Amazon, TikTok, sales tax). Everything is one monthly grid; the yellow rows are the plan.
// Edits: cells in the grid (core PUT /series), "Fill a range" (POST /drivers/patterns, previewed first), plan settings (PUT /drivers/levers).
import { events } from './lib/api.js';
import { toast as notify } from './lib/util.js';

const ID = 'dr-style';
const CSS = `
.dr-cue { display: flex; flex-wrap: wrap; align-items: center; gap: var(--lv-sp-2) var(--lv-sp-3); padding: var(--lv-sp-2) var(--lv-sp-3); border: 1px solid var(--lv-line); border-radius: var(--lv-r-sm); background: var(--lv-surface); font-size: var(--lv-fs-sm); color: var(--lv-ink-2); }
.dr-cue.copy { border-color: var(--lv-warn); background: var(--lv-warn-bg); }
.dr-cue .who { font-weight: var(--lv-fw-heavy); color: var(--lv-ink); }
.dr-cue .grow { flex: 1 1 260px; min-width: 0; }
.dr-status { display: flex; flex-wrap: wrap; gap: var(--lv-sp-2); }
.dr-status:empty { display: none; }
.dr-tool { display: flex; flex-direction: column; gap: var(--lv-sp-3); }
.dr-tool[hidden] { display: none; }
.dr-settings, .dr-compare { display: flex; flex-direction: column; gap: var(--lv-sp-3); }
.dr-tool h2, .dr-settings h2, .dr-compare h2 { margin: 0; font-size: var(--lv-fs-md); font-weight: var(--lv-fw-heavy); }
.dr-fields { display: grid; grid-template-columns: repeat(auto-fill, minmax(170px, 1fr)); gap: var(--lv-sp-3) var(--lv-sp-4); align-items: end; }
.dr-field { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
.dr-field > label, .dr-field > .lbl { font-size: var(--lv-fs-sm); font-weight: var(--lv-fw-semi); color: var(--lv-ink-2); }
.dr-field input:not([type="checkbox"]), .dr-field select { width: 100%; }
.dr-field.wide { grid-column: span 2; }
.dr-field .segs { width: fit-content; max-width: 100%; }
.dr-field .hint { font-size: var(--lv-fs-xs); color: var(--lv-muted); }
.dr-row { display: flex; flex-wrap: wrap; gap: var(--lv-sp-2); align-items: center; }
.dr-row .status { font-size: var(--lv-fs-sm); color: var(--lv-muted); }
.dr-row .status.err { color: var(--lv-bad); font-weight: var(--lv-fw-semi); }
.dr-preview { width: 100%; border-collapse: collapse; font-size: var(--lv-fs-sm); font-variant-numeric: tabular-nums; }
.dr-preview th, .dr-preview td { padding: 5px 8px; text-align: right; border-bottom: 1px solid var(--lv-line-soft); white-space: nowrap; }
.dr-preview th:first-child, .dr-preview td:first-child { text-align: left; white-space: normal; }
.dr-preview thead th { font-size: var(--lv-fs-2xs); text-transform: uppercase; letter-spacing: var(--lv-track-kicker); color: var(--lv-muted); font-weight: var(--lv-fw-bold); }
.dr-preview td.delta.up { color: var(--lv-good); font-weight: var(--lv-fw-semi); }
.dr-preview td.delta.down { color: var(--lv-neg); font-weight: var(--lv-fw-semi); }
.dr-box { background: var(--lv-surface-2); border-radius: var(--lv-r-sm); padding: var(--lv-sp-3); overflow-x: auto; max-width: 100%; }
.dr-refs { display: flex; flex-direction: column; gap: var(--lv-sp-2); }
.dr-refs:empty { display: none; }
.dr-ref { display: flex; flex-wrap: wrap; gap: var(--lv-sp-2); align-items: center; }
.dr-legend { display: flex; flex-wrap: wrap; gap: 4px var(--lv-sp-4); align-items: center; font-size: var(--lv-fs-xs); color: var(--lv-muted); }
.dr-total { font-size: var(--lv-fs-sm); font-weight: var(--lv-fw-semi); }
.dr-total.bad { color: var(--lv-bad); }
.dr-compare-chips { flex-wrap: wrap; width: fit-content; max-width: 100%; }
.dr-fy { width: 100%; border-collapse: collapse; font-size: var(--lv-fs-sm); font-variant-numeric: tabular-nums; }
.dr-fy th, .dr-fy td { padding: 6px 8px; text-align: right; border-bottom: 1px solid var(--lv-line-soft); white-space: nowrap; }
.dr-fy th:first-child, .dr-fy td:first-child { text-align: left; }
.dr-fy thead th { font-size: var(--lv-fs-2xs); text-transform: uppercase; letter-spacing: var(--lv-track-kicker); color: var(--lv-muted); font-weight: var(--lv-fw-bold); }
.dr-fy td.neg { color: var(--lv-neg); }
.dr-scroll { overflow-x: auto; }
.dr-check { display: inline-flex; align-items: center; gap: 6px; font-size: var(--lv-fs-sm); min-height: var(--lv-hit, 28px); white-space: nowrap; margin-right: var(--lv-sp-3); }
.dr-check input { width: auto; min-height: 0; margin: 0; }
@media (max-width: 640px) { .dr-fields { grid-template-columns: 1fr 1fr; } }
`;

function ensureStyle() {
  if (document.getElementById(ID)) return;
  const s = document.createElement('style');
  s.id = ID;
  s.textContent = CSS;
  document.head.append(s);
}

const OVERRIDES = new Set(['drivers.ad_spend', 'drivers.nc_orders', 'drivers.nc_rev', 'drivers.repeat_rev']);
const LINES = [
  { id: 'spend_total', label: 'Marketing spend', unit: 'usd0', row: 'drivers.spend_total' },
  { id: 'nc_cpa', label: 'New-customer CPA', unit: 'usd', row: 'drivers.nc_cpa' },
  { id: 'nc_aov', label: 'New-customer AOV', unit: 'usd', row: 'drivers.nc_aov' },
  { id: 'amazon_rev', label: 'Amazon revenue', unit: 'usd0', row: 'drivers.amazon_rev' },
  { id: 'tiktok_rev', label: 'TikTok revenue', unit: 'usd0', row: 'drivers.tiktok_rev' },
  { id: 'nc_roas', label: 'NC-ROAS slide', unit: 'x', row: 'drivers.nc_cpa' },
];
const KINDS = [['flat', 'Flat'], ['step', 'Step'], ['growth', 'Growth'], ['ramp', 'Ramp']];
const SHARE_LABELS = [['amazon', 'Amazon ads'], ['meta', 'Meta'], ['google', 'Google'], ['bing', 'Bing ads'], ['tiktok', 'TikTok ads']];
const SHARE_TOTAL_TOLERANCE = 0.0005;
const PAYBACK_NOT_REACHED = 'not within the horizon';

// the last range fill (module scope: it survives the re-render that follows Apply, so Undo works from the toast, the cue button and Ctrl+Z)
let lastFill = null;
let live = null;                                                  // the ctx of the page on screen
async function undoFill() {
  const f = lastFill;
  if (!f || !live) return;
  try {
    const res = await live.api.post('drivers/undo', { scenario: f.slug, version: f.version });
    lastFill = null;
    events.emit('saved', { scenario: f.slug, version: res.version });
    notify(res.restored ? `Undid the range fill: ${res.restored} change${res.restored === 1 ? '' : 's'} put back${res.skipped ? `, ${res.skipped} edited since and left as they are` : ''}`
      : 'Nothing to undo: those cells were edited again', 'ok');
    live.reload();
  } catch (e) { notify(e.message, 'error'); }
}

export async function render(el, ctx) {
  ensureStyle();
  live = ctx;
  const { h, fmt, ui, charts, toast } = ctx;
  const slug = ctx.scenario;
  const info = ctx.scenarioInfo;
  const isCopy = !info.is_default;
  const cleanups = [];

  const root = h('div', { class: 'pg-page' });
  el.replaceChildren(root);
  ui.loading(root, 'Loading the spend plan');

  const load = () => ctx.api.table('drivers');
  let payload;
  let allScenarios = [];
  try {
    [payload, allScenarios] = await Promise.all([load(), ctx.api.get('scenarios').then((r) => r.scenarios || []).catch(() => [])]);
  } catch (e) { ui.error(root, e, ctx.reload); return undefined; }
  if (!ctx.alive()) return undefined;
  const baseName = (allScenarios.find((x) => x.slug === info.base_slug) || {}).name || null;

  const state = { payload, tool: null };
  const mon = (iso) => fmt.monthLabel(iso);
  const rowOf = (p, key) => p.rows.find((r) => r.key === key);
  const valueAt = (p, key, iso) => { const r = rowOf(p, key); return r ? r.values[p.months.indexOf(iso)] : null; };
  const signed = (v) => (Number.isFinite(v) ? `${v >= 0 ? '+' : '-'}${Math.abs(v * 100).toFixed(0)}%` : '');

  // ---------------------------------------------------------------- header and TLDR
  const headerHost = h('div');
  const statusHost = h('div', { class: 'dr-status', role: 'note', 'aria-label': 'Month status' });
  function drawHeader() {
    const s = state.payload.summary;
    const n = s.window_months || 12;
    const empty = !s.spend_next_12 && !s.spend_prev_12;
    const dir = s.spend_change == null ? '' : s.spend_change >= 0 ? 'up' : 'down';
    const posture = { growth: 'a growth plan', harvest: 'a harvest plan', hold: 'a flat plan' }[s.posture];
    const line = empty
      ? 'No spend plan yet. Type a monthly amount in the Marketing spend row, or fill a range.'
      : `Marketing spend over the next ${n} months is ${fmt.compact(s.spend_next_12)}*${s.spend_change == null ? '' : `, ${dir} ${Math.abs(s.spend_change * 100).toFixed(0)}% on the last ${n}: ${posture}`}.`;
    const pb = s.payback_months;
    const kpis = [
      { label: `Marketing spend, next ${n} months`, value: fmt.compact(s.spend_next_12) + '*', note: s.spend_change == null ? '' : `${signed(s.spend_change)} on last ${n}` },
      { label: `New-customer revenue, next ${n} months`, value: fmt.compact(s.nc_rev_next_12) + '*' },
      { label: 'New-customer return on ad spend (NC-ROAS)', value: s.nc_roas_next_12 == null ? 'none' : fmt.x(s.nc_roas_next_12, 2) + '*', note: 'per ad dollar' },
      { label: 'Payback on a new customer', value: pb == null ? PAYBACK_NOT_REACHED : `${pb} month${pb === 1 ? '' : 's'}*`, note: s.plan_cpa ? `CPA ${fmt.money(s.plan_cpa)}` : '', tone: pb == null && s.plan_cpa ? 'bad' : '' },
      { label: `Total revenue, next ${n} months`, value: fmt.compact(s.total_revenue_next_12) + '*' },
    ];
    const sl = state.payload.status_line || {};
    const statusChips = [];
    if (sl.books_through) statusChips.push(ui.chip(`Books through ${mon(sl.books_through)}`, 'good'));
    if (sl.feeds_from) statusChips.push(ui.chip(`Feeds only, no books yet: ${sl.feeds_from === sl.feeds_to ? mon(sl.feeds_from) : `${mon(sl.feeds_from)} to ${mon(sl.feeds_to)}`}`, 'info'));
    if (sl.projected_from) statusChips.push(ui.chip(`Projected from ${mon(sl.projected_from)}*`, 'neutral'));
    statusHost.replaceChildren(...statusChips);
    headerHost.replaceChildren(ui.pageHeader({
      title: 'Spend plan',
      purpose: 'Set what you spend each month, what a new customer costs (CPA, cost per acquisition) and spends on a first order (AOV, average order value).',
      actions: [h('button', { type: 'button', class: 'btn primary', id: 'dr-open-tool', onclick: () => toggleTool(true) }, 'Fill a range')],
      tldr: { line, kpis },
    }));
  }

  // ---------------------------------------------------------------- which scenario am I in
  const cue = h('div', { class: 'dr-cue' + (isCopy ? ' copy' : ''), role: 'note' });
  const canUndoFill = () => !!lastFill && lastFill.slug === slug && lastFill.version === state.payload.scenario.version;
  function drawCue() {
    cue.replaceChildren(...[
      ui.chip(isCopy ? 'Scenario copy' : 'Plan of record', isCopy ? 'warn' : 'neutral'),
      h('span', { class: 'who' }, `${info.name} v${state.payload.scenario.version}`),
      h('span', { class: 'grow' }, isCopy ? `Editing a copy${baseName ? ` of ${baseName}` : ''}. Changes here do not touch the plan of record.` : ''),
      canUndoFill() ? h('button', { type: 'button', class: 'btn small', id: 'dr-undo-fill', onclick: () => undoFill() }, `Undo range fill (${lastFill.count} change${lastFill.count === 1 ? '' : 's'})`) : null,
      isCopy && baseName ? h('button', { type: 'button', class: 'btn small', onclick: () => { cmp.picked.add(info.base_slug); loadCompare(); compareHost.scrollIntoView({ block: 'start' }); } }, `Compare with ${baseName}`) : null,
      isCopy ? h('button', { type: 'button', class: 'btn small', onclick: () => cloneScenario() }, 'Clone this copy') : null,
    ].filter(Boolean));
  }

  async function cloneScenario() {
    const { askText } = await import('./lib/util.js');
    askText({ title: 'Clone scenario', label: 'Name', value: `${info.name} copy`, confirmText: 'Clone',
      onSubmit: async (name) => {
        const res = await ctx.api.post('scenarios', { name, clone_from: slug });
        location.assign(`/projection/drivers?scenario=${encodeURIComponent(res.scenario.slug)}`);
      } });
  }

  // ---------------------------------------------------------------- chart
  const canvas = h('canvas', { 'aria-label': 'Marketing spend, new-customer revenue and repeat revenue by month' });
  const chartCard = h('div', { class: 'pg-card' }, h('h2', { class: 'pg-h2' }, 'Marketing spend, new-customer revenue and repeat revenue'), h('div', { class: 'pg-chartbox' }, canvas));
  function drawChart() {
    charts.destroyAll();
    const p = state.payload;
    const ink = getComputedStyle(document.documentElement).getPropertyValue('--lv-viz-ink').trim() || undefined;       // the series the page is about; product slots stay with the products
    const pick = (key, label, color) => ({ label, values: rowOf(p, key).values, color });
    charts.lineChart(canvas, { months: p.months, status: p.status, yFmt: 'usd0',
      series: [pick('drivers.spend_total', 'Marketing spend', ink), pick('nc_rev', 'New-customer revenue', charts.PALETTE[3]), pick('repeat_rev', 'Repeat revenue', charts.PALETTE[4])] });
  }

  // ---------------------------------------------------------------- grid
  const gridHost = h('div');
  const legend = h('div', { class: 'dr-legend' });
  const refHost = h('div', { class: 'dr-refs' });
  function drawLegend() {
    legend.replaceChildren(ui.chip('Carried', 'info'), h('span', null, 'a value carried from the month before; type a value to set it'),
      h('span', null, 'Clear a yellow cell to remove the value you typed.'));
    const r = state.payload.reference || {};
    const kids = [];
    if (r.cpa) kids.push(h('div', { class: 'dr-ref' }, ui.chip(r.cpa.chip, r.cpa.tone), h('span', { class: 'dr-legend' }, `Realized CPA from the feeds, ${mon(r.cpa.window.from)} to ${mon(r.cpa.window.to)}; plan CPA for ${mon(r.cpa.plan_month)}.`)));
    if (r.amazon && r.amazon.note) kids.push(h('div', { class: 'dr-ref' }, ui.chip('Amazon feed', 'warn'), h('span', { class: 'dr-legend' }, r.amazon.note)));
    refHost.replaceChildren(...kids);
  }
  let grid = null;
  const cellMeta = () => state.payload.cellmeta || {};
  function highlight(cell) {
    const code = (cellMeta()[cell.key] || {})[cell.month];
    return code === 'carried' || code === 'slid' ? 'info' : null;
  }
  async function onEdit(edits) {
    const meta = cellMeta();
    const closed = edits.find((e) => (meta[e.line_key] || {})[e.month] === 'actual');
    if (closed) throw new Error(`${mon(closed.month)} is a closed month and its value comes from the books. Upload the updated books to change it.`);
    const slide = (state.payload.levers || {}).roas_slide || {};
    const inSlide = (iso) => slide.enabled && iso >= slide.from && iso <= slide.to;           // a typed CPA inside the NC-ROAS slide has to be an override to count
    const open = (state.payload.summary || {}).open_month || '';                     // a closed month is corrected with an override, a planned month takes an input
    const items = edits.map((e) => ({ line_key: e.line_key, month: e.month, value: e.value,
      kind: OVERRIDES.has(e.line_key) || (e.line_key === 'drivers.nc_cpa' && inSlide(e.month)) || e.month < open ? 'override' : 'input' }));
    for (let i = 0; i < items.length; i += 500) {                                     // saved through the Spend plan endpoint: line bounds and closed months are checked there
      try { await ctx.api.put('drivers/series', { scenario: slug, items: items.slice(i, i + 500), expected_version: state.payload.scenario.version }); }
      catch (e) { throw e && e.status === 409 ? new Error('This plan changed in another tab or window. Reload to see it.') : e; }
    }
    const fresh = await load();
    if (!ctx.alive()) return fresh;
    state.payload = fresh;
    drawHeader();
    drawChart();
    drawLegend();
    drawCue();
    return fresh;
  }
  function drawGrid() {
    grid = ctx.grid(gridHost, { payload: state.payload, onEdit, highlight, title: 'Spend plan', groupCollapse: ['h_channels', 'h_src'] });
    requestAnimationFrame(() => {                       // open on the first projected month, not on January 2025
      const sc = gridHost.querySelector('.pg-scroll');
      const bound = sc && sc.querySelector('th.pg-bound');
      if (sc && bound) sc.scrollLeft = Math.max(0, bound.offsetLeft - 330);
    });
  }

  // ---------------------------------------------------------------- fill a range
  const toolHost = h('div', { class: 'pg-card dr-tool', id: 'dr-tool', hidden: true });
  toolHost.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape') { toggleTool(false); const open = document.getElementById('dr-open-tool'); if (open) open.focus(); }
  });
  const tf = { line: 'spend_total', kind: 'flat', from: '', to: '', value: '', then: '', stepMonth: '', rate: '', cap: '', end: '', start: '' };
  const els = {};

  function planMonths(lineId) {
    const p = state.payload;
    const key = LINES.find((l) => l.id === lineId).row;
    const meta = (p.cellmeta || {})[key] || {};
    return p.months.filter((m) => meta[m] !== 'actual');
  }
  function toggleTool(open) {
    toolHost.hidden = !open;
    if (open) { buildTool(); const first = toolHost.querySelector('select, input'); if (first) first.focus(); toolHost.scrollIntoView({ block: 'nearest' }); }
  }
  function defaultsFor(lineId) {
    const months = planMonths(lineId);
    const open = (state.payload.summary || {}).open_month;
    tf.from = months.find((m) => m >= open) || months[0] || '';
    const idx = months.indexOf(tf.from);
    tf.to = months[Math.min(months.length - 1, idx + 11)] || tf.from;
    tf.stepMonth = months[Math.min(months.length - 1, idx + 6)] || tf.from;
    const line = LINES.find((l) => l.id === lineId);
    const cur = valueAt(state.payload, line.row, tf.from);
    if (lineId === 'nc_roas') {
      const aov = valueAt(state.payload, 'drivers.nc_aov', tf.from), cpa = valueAt(state.payload, 'drivers.nc_cpa', tf.from);
      const r = aov && cpa ? aov / cpa : 0.7;
      tf.start = r.toFixed(2); tf.end = (r * 0.85).toFixed(2);
    } else {
      const shown = Number.isFinite(cur) ? (line.unit === 'usd' ? cur.toFixed(2) : String(Math.round(cur))) : '';
      tf.value = shown; tf.then = shown; tf.end = shown; tf.rate = '3'; tf.cap = '';
    }
  }
  function parseNum(text, unit) {
    if (text == null || String(text).trim() === '') return { ok: false, error: 'Enter a value' };
    const r = ctx.fmt.parse(String(text), unit);
    return r.ok && r.value != null ? r : { ok: false, error: r.error || 'Enter a value' };
  }
  function buildSpec() {
    const line = LINES.find((l) => l.id === tf.line);
    const unit = line.unit;
    const n = (text, u = unit) => parseNum(text, u);
    if (tf.line === 'nc_roas') {
      const a = n(tf.start, 'x'), b = n(tf.end, 'x');
      if (!a.ok) return { error: 'Starting NC-ROAS: ' + a.error };
      if (!b.ok) return { error: 'Ending NC-ROAS: ' + b.error };
      return { pattern: { type: 'roas_slide', start: a.value, end: b.value } };
    }
    const a = n(tf.value);
    if (!a.ok) return { error: (tf.kind === 'flat' ? 'Amount: ' : 'Starting amount: ') + a.error };
    if (tf.kind === 'flat') return { pattern: { type: 'flat', value: a.value } };
    if (tf.kind === 'step') {
      const b = n(tf.then);
      if (!b.ok) return { error: 'Then amount: ' + b.error };
      return { pattern: { type: 'step', value: a.value, then_value: b.value, step_month: tf.stepMonth } };
    }
    if (tf.kind === 'growth') {
      const r = parseNum(tf.rate, 'pct');
      if (!r.ok) return { error: 'Monthly growth: ' + r.error };
      const spec = { type: 'growth', value: a.value, rate: r.value };
      if (String(tf.cap).trim() !== '') { const c = n(tf.cap); if (!c.ok) return { error: 'Cap: ' + c.error }; spec.cap = c.value; }
      return { pattern: spec };
    }
    const b = n(tf.end);
    if (!b.ok) return { error: 'Ending amount: ' + b.error };
    return { pattern: { type: 'ramp', value: a.value, end_value: b.value } };
  }
  let previewSeq = 0;
  let previewTimer = null;
  const requestBody = (spec) => ({ scenario: slug, target: tf.line, from: tf.from, to: tf.to, pattern: spec.pattern });
  function schedulePreview() {
    clearTimeout(previewTimer);
    previewTimer = setTimeout(runPreview, 280);
    els.apply.disabled = true;
  }
  async function runPreview() {
    const spec = buildSpec();
    const seq = ++previewSeq;
    if (spec.error) { showPreview(null, spec.error); return; }
    if (!tf.from || !tf.to) { showPreview(null, 'Pick the first and last month.'); return; }
    els.status.className = 'status';
    els.status.textContent = 'Working out the result';
    try {
      const res = await ctx.api.post('drivers/patterns', { ...requestBody(spec), preview: true });
      if (seq === previewSeq && ctx.alive()) showPreview(res, null);
    } catch (e) { if (seq === previewSeq && ctx.alive()) showPreview(null, e.message); }
  }
  function delta(a, b, f) {
    if (a == null || b == null) return h('td', { class: 'delta' }, '');
    const d = b - a;
    if (Math.abs(d) < 1e-9 || f(a) === f(b)) return h('td', { class: 'delta' }, 'no change');
    return h('td', { class: 'delta ' + (d > 0 ? 'up' : 'down') }, (d > 0 ? '+' : '-') + f(Math.abs(d)));
  }
  function showPreview(res, error) {
    els.preview.replaceChildren();
    els.apply.disabled = true;
    if (error || !res) {
      els.status.className = 'status err';
      els.status.textContent = error || '';
      return;
    }
    const b = res.before, a = res.after;
    const months = (n) => (n == null ? PAYBACK_NOT_REACHED : `${n} month${n === 1 ? '' : 's'}`);
    const rowFor = (label, x, y, f, d = f) => h('tr', null, h('td', null, label), h('td', null, x == null ? 'none' : f(x)), h('td', null, y == null ? 'none' : f(y)), delta(x, y, d));
    els.preview.replaceChildren(h('table', { class: 'dr-preview' },
      h('thead', null, h('tr', null, h('th', null, `${mon(res.from)} to ${mon(res.to)} (${res.months} months)`), h('th', null, 'Now'), h('th', null, 'With this'), h('th', null, 'Change'))),
      h('tbody', null,
        rowFor('Marketing spend', b.spend_total, a.spend_total, (v) => fmt.compact(v)),
        rowFor('New-customer revenue', b.nc_rev, a.nc_rev, (v) => fmt.compact(v)),
        rowFor('NC-ROAS', b.nc_roas, a.nc_roas, (v) => fmt.x(v, 2)),
        rowFor(`CPA in ${mon(res.to)}`, b.plan_cpa, a.plan_cpa, (v) => fmt.money(v, { digits: 2 })),
        h('tr', null, h('td', null, `Payback in ${mon(res.to)}`), h('td', null, months(b.payback_months)), h('td', null, months(a.payback_months)),
          delta(b.payback_months, a.payback_months, (v) => `${v} month${v === 1 ? '' : 's'}`)))));
    els.status.className = 'status';
    const changing = res.cells.filter((c) => c.before !== c.after).length;
    els.status.textContent = `${changing} of ${res.months} month${res.months === 1 ? '' : 's'} change. Nothing is saved until you apply.`;
    els.apply.disabled = false;
    els.apply.textContent = `Apply to ${res.months} month${res.months === 1 ? '' : 's'}`;
  }
  async function applyPattern() {
    const spec = buildSpec();
    if (spec.error) return;
    els.apply.disabled = true;
    els.status.className = 'status';
    els.status.textContent = 'Saving';
    try {
      const res = await ctx.api.post('drivers/patterns', { ...requestBody(spec), expected_version: state.payload.scenario.version });
      events.emit('saved', { scenario: slug, version: res.version });
      if (res.changed) lastFill = { slug, version: res.version, count: res.changed };
      toast(`Saved ${res.changed} change${res.changed === 1 ? '' : 's'} to ${LINES.find((l) => l.id === tf.line).label}`,
        res.changed ? { kind: 'ok', action: { label: 'Undo', onClick: () => undoFill() } } : 'ok');
      ctx.reload();
    } catch (e) { els.status.className = 'status err'; els.status.textContent = e.message; els.apply.disabled = false; }
  }
  const monthSelect = (id, label, value, onChange, options) => {
    const sel = h('select', { id, 'aria-label': label }, options.map((m) => h('option', { value: m, selected: m === value }, mon(m))));
    sel.addEventListener('change', () => onChange(sel.value));
    return sel;
  };
  const textField = (id, label, key, hint, { type = 'text' } = {}) => {
    const input = h('input', { type, id, value: tf[key], inputmode: 'decimal', autocomplete: 'off' });
    input.addEventListener('input', () => { tf[key] = input.value; schedulePreview(); });
    return h('div', { class: 'dr-field' }, h('label', { for: id }, label), input, hint ? h('span', { class: 'hint' }, hint) : null);
  };
  function buildTool() {
    const isSlide = tf.line === 'nc_roas';
    if (!tf.from) defaultsFor(tf.line);
    const options = planMonths(tf.line);
    const lineSel = h('select', { id: 'dr-line', 'aria-label': 'Line' }, LINES.map((l) => h('option', { value: l.id, selected: l.id === tf.line }, l.label)));
    lineSel.addEventListener('change', () => { tf.line = lineSel.value; if (tf.line === 'nc_roas') tf.kind = 'slide'; else if (tf.kind === 'slide') tf.kind = 'flat'; defaultsFor(tf.line); buildTool(); schedulePreview(); });
    const kindSegs = KINDS.map(([k, label]) => h('button', { type: 'button', class: 'seg', 'aria-pressed': String(tf.kind === k), dataset: { k },
      onclick: () => { tf.kind = k; buildTool(); schedulePreview(); } }, label));
    const endBtns = [['Next 12 months', 11], ['Next 24 months', 23]].map(([label, n]) => h('button', { type: 'button', class: 'btn small',
      onclick: () => { const i = options.indexOf(tf.from); tf.to = options[Math.min(options.length - 1, i + n)]; buildTool(); schedulePreview(); } }, label));
    endBtns.push(h('button', { type: 'button', class: 'btn small', onclick: () => { tf.to = options[options.length - 1]; buildTool(); schedulePreview(); } }, `Through ${mon(options[options.length - 1] || '')}`));
    const fields = [];
    if (isSlide) {
      fields.push(textField('dr-start', 'NC-ROAS at the start', 'start', 'Revenue per ad dollar, e.g. 0.80x', {}), textField('dr-end', 'NC-ROAS at the end', 'end', 'Held after the last month', {}));
    } else {
      fields.push(textField('dr-value', tf.kind === 'flat' ? 'Amount per month' : 'Starting amount', 'value', 'Type 350k or 1.5m'));
      if (tf.kind === 'step') fields.push(textField('dr-then', 'Then', 'then', ''), h('div', { class: 'dr-field' }, h('label', { for: 'dr-stepm' }, 'Changes in'), monthSelect('dr-stepm', 'Step month', tf.stepMonth, (v) => { tf.stepMonth = v; schedulePreview(); }, options)));
      if (tf.kind === 'growth') fields.push(textField('dr-rate', 'Growth per month (%)', 'rate', 'Compounds each month'), textField('dr-cap', 'Stop growing at', 'cap', 'Optional'));
      if (tf.kind === 'ramp') fields.push(textField('dr-ramp-end', 'Ending amount', 'end', 'Straight line to the last month'));
    }
    els.status = h('span', { class: 'status', role: 'status' }, '');
    els.preview = h('div', { class: 'dr-box', 'aria-live': 'polite' });
    els.apply = h('button', { type: 'button', class: 'btn primary', disabled: true, onclick: applyPattern }, 'Apply');
    toolHost.replaceChildren(...[
      h('div', { class: 'dr-row' }, h('h2', null, 'Fill a range'), h('span', { class: 'status' }, 'Writes the yellow cells for a run of months. Nothing is saved until you apply.')),
      h('div', { class: 'dr-fields' },
        h('div', { class: 'dr-field' }, h('label', { for: 'dr-line' }, 'Line'), lineSel),
        h('div', { class: 'dr-field' }, h('label', { for: 'dr-from' }, 'From'), monthSelect('dr-from', 'From month', tf.from, (v) => { tf.from = v; if (tf.to < v) tf.to = v; buildTool(); schedulePreview(); }, options)),
        h('div', { class: 'dr-field' }, h('label', { for: 'dr-to' }, 'To'), monthSelect('dr-to', 'To month', tf.to, (v) => { tf.to = v; schedulePreview(); }, options.filter((m) => m >= tf.from))),
        isSlide ? null : h('div', { class: 'dr-field wide' }, h('span', { class: 'lbl' }, 'Shape'), h('div', { class: 'segs', role: 'group', 'aria-label': 'Shape' }, kindSegs))),
      h('div', { class: 'dr-row' }, endBtns),
      h('div', { class: 'dr-fields' }, fields),
      els.preview,
      h('div', { class: 'dr-row' }, els.apply, h('button', { type: 'button', class: 'btn', onclick: () => toggleTool(false) }, 'Close'), els.status)].filter(Boolean));
    if (isSlide) toolHost.append(h('p', { class: 'dr-legend' }, 'The slide sets CPA from the AOV each month in the range and hides typed CPA cells there. A CPA you type inside the range still wins. Turn the slide off in Plan settings.'));
  }

  // ---------------------------------------------------------------- plan settings
  const settingsHost = h('div', { class: 'pg-card dr-settings' });
  function drawSettings() {
    const lv = state.payload.levers;
    const shareInputs = {};
    const shareRow = SHARE_LABELS.map(([k, label]) => {
      const input = h('input', { type: 'number', id: 'dr-sh-' + k, min: 0, max: 100, step: 0.1, value: String(Math.round(lv.channel_share[k] * 1000) / 10), inputmode: 'decimal' });
      shareInputs[k] = input;
      return h('div', { class: 'dr-field' }, h('label', { for: 'dr-sh-' + k }, `${label} (%)`), input);
    });
    const total = h('span', { class: 'dr-total' });
    const taxInput = h('input', { type: 'number', id: 'dr-tax', min: 0, max: 50, step: 0.1, value: String(Math.round(lv.sales_tax_rate * 1000) / 10), inputmode: 'decimal' });
    const costInput = h('input', { type: 'number', id: 'dr-cost', min: 0, max: 99, step: 0.1, value: String(Math.round(lv.payback.product_cost_pct * 1000) / 10), inputmode: 'decimal' });
    const fulInput = h('input', { type: 'number', id: 'dr-ful', min: 0, max: 1000, step: 0.01, value: String(lv.payback.fulfillment_per_order), inputmode: 'decimal' });
    const horizonInput = h('input', { type: 'number', id: 'dr-hz', min: 1, max: 60, step: 1, value: String(lv.payback.horizon_months), inputmode: 'numeric' });
    const bandInput = h('input', { type: 'number', id: 'dr-band', min: 1, max: 100, step: 1, value: String(Math.round(lv.posture_band * 100)), inputmode: 'numeric' });
    const winInput = h('input', { type: 'number', id: 'dr-win', min: 1, max: 36, step: 1, value: String(lv.summary_months), inputmode: 'numeric' });
    const refDaysInput = h('input', { type: 'number', id: 'dr-refd', min: 7, max: 90, step: 1, value: String(lv.reference_days), inputmode: 'numeric' });
    const refTolInput = h('input', { type: 'number', id: 'dr-reft', min: 1, max: 100, step: 1, value: String(Math.round(lv.reference_tolerance * 100)), inputmode: 'numeric' });
    const adBoxes = Object.fromEntries(SHARE_LABELS.map(([k, label]) => {
      const box = h('input', { type: 'checkbox', id: 'dr-ad-' + k, checked: lv.ad_spend_channels.includes(k) });
      return [k, { box, label }];
    }));
    const save = h('button', { type: 'button', class: 'btn primary', disabled: true }, 'Save settings');
    const status = h('span', { class: 'status', role: 'status' }, '');
    const shares = () => Object.fromEntries(SHARE_LABELS.map(([k]) => [k, (parseFloat(shareInputs[k].value) || 0) / 100]));
    const adSel = () => SHARE_LABELS.map(([k]) => k).filter((k) => adBoxes[k].box.checked);
    const desired = () => ({ channel_share: shares(), sales_tax_rate: (parseFloat(taxInput.value) || 0) / 100,
      payback: { product_cost_pct: (parseFloat(costInput.value) || 0) / 100, fulfillment_per_order: parseFloat(fulInput.value) || 0, horizon_months: parseInt(horizonInput.value, 10) || 0 },
      ad_spend_channels: adSel(), posture_band: (parseFloat(bandInput.value) || 0) / 100, summary_months: parseInt(winInput.value, 10) || 0,
      reference_days: parseInt(refDaysInput.value, 10) || 0, reference_tolerance: (parseFloat(refTolInput.value) || 0) / 100 });
    const differs = () => {
      const d = desired();
      return SHARE_LABELS.some(([k]) => Math.abs(d.channel_share[k] - lv.channel_share[k]) > 1e-6) || Math.abs(d.sales_tax_rate - lv.sales_tax_rate) > 1e-6
        || Math.abs(d.payback.product_cost_pct - lv.payback.product_cost_pct) > 1e-6 || Math.abs(d.payback.fulfillment_per_order - lv.payback.fulfillment_per_order) > 1e-6
        || d.payback.horizon_months !== lv.payback.horizon_months || Math.abs(d.posture_band - lv.posture_band) > 1e-6 || d.summary_months !== lv.summary_months
        || d.reference_days !== lv.reference_days || Math.abs(d.reference_tolerance - lv.reference_tolerance) > 1e-6
        || d.ad_spend_channels.join() !== SHARE_LABELS.map(([k]) => k).filter((k) => lv.ad_spend_channels.includes(k)).join();
    };
    const d0Valid = () => { const d = desired(); return d.payback.horizon_months >= 1 && d.payback.horizon_months <= 60 && d.summary_months >= 1 && d.summary_months <= 36 && d.posture_band > 0 && d.posture_band <= 1
      && d.reference_days >= 7 && d.reference_days <= 90 && d.reference_tolerance > 0 && d.reference_tolerance <= 1; };
    const refresh = () => {
      const sum = SHARE_LABELS.reduce((a, [k]) => a + shares()[k], 0);
      const ok = Math.abs(sum - 1) <= SHARE_TOTAL_TOLERANCE;
      total.className = 'dr-total' + (ok ? '' : ' bad');
      total.textContent = `Channel mix adds up to ${(sum * 100).toFixed(1)}%${ok ? '' : ', it must be 100%'}`;
      const valid = ok && adSel().length > 0 && d0Valid();
      save.disabled = !valid || !differs();
      status.textContent = '';
    };
    [...Object.values(shareInputs), taxInput, costInput, fulInput, horizonInput, bandInput, winInput, refDaysInput, refTolInput, ...Object.values(adBoxes).map((a) => a.box)].forEach((i) => i.addEventListener('input', refresh));
    save.addEventListener('click', async () => {
      save.disabled = true;
      status.className = 'status';
      status.textContent = 'Saving';
      try {
        const d = desired();
        const res = await ctx.api.put('drivers/levers', { scenario: slug, levers: d, expected_version: state.payload.scenario.version });
        events.emit('saved', { scenario: slug, version: res.scenario.version });
        toast('Plan settings saved', 'ok');
        ctx.reload();
      } catch (e) { status.className = 'status err'; status.textContent = e.message; save.disabled = false; }
    });
    const rs = lv.roas_slide;
    const slideRow = rs.enabled ? h('div', { class: 'dr-row' },
      ui.chip('NC-ROAS slide on', 'info'),
      h('span', null, `${fmt.x(rs.start, 2)} in ${mon(rs.from)} to ${fmt.x(rs.end, 2)} in ${mon(rs.to)}, then held.`),
      h('button', { type: 'button', class: 'btn small', onclick: async () => {
        try { const res = await ctx.api.put('drivers/levers', { scenario: slug, levers: { roas_slide: { enabled: false } }, expected_version: state.payload.scenario.version }); events.emit('saved', { scenario: slug, version: res.scenario.version }); ctx.reload(); }
        catch (e) { toast(e.message, 'error'); }
      } }, 'Turn off')) : null;
    settingsHost.replaceChildren(...[
      h('h2', null, 'Plan settings'),
      h('div', { class: 'dr-fields' }, shareRow),
      h('div', { class: 'dr-row' }, total),
      h('div', { class: 'dr-fields' },
        h('div', { class: 'dr-field' }, h('label', { for: 'dr-tax' }, 'Sales tax estimate (% of direct-to-consumer revenue)'), taxInput),
        h('div', { class: 'dr-field' }, h('label', { for: 'dr-cost' }, 'Product cost for payback (% of order)'), costInput),
        h('div', { class: 'dr-field' }, h('label', { for: 'dr-ful' }, 'Fulfillment per order for payback ($)'), fulInput),
        h('div', { class: 'dr-field' }, h('label', { for: 'dr-hz' }, 'Payback horizon (months)'), horizonInput),
        h('div', { class: 'dr-field' }, h('label', { for: 'dr-win' }, 'Headline window (months)'), winInput),
        h('div', { class: 'dr-field' }, h('label', { for: 'dr-band' }, 'Growth or harvest band (% change)'), bandInput, h('span', { class: 'hint' }, 'Beyond this the plan reads as growth or harvest')),
        h('div', { class: 'dr-field' }, h('label', { for: 'dr-refd' }, 'Feed reference window (days)'), refDaysInput, h('span', { class: 'hint' }, 'Realized CPA and the Amazon run-rate')),
        h('div', { class: 'dr-field' }, h('label', { for: 'dr-reft' }, 'Feed reference band (% difference)'), refTolInput, h('span', { class: 'hint' }, 'A typed plan beyond this gets a note'))),
      h('div', { class: 'dr-field' }, h('span', { class: 'lbl' }, 'Channels counted as ad spend (the new-customer cost denominator)'),
        h('div', { class: 'dr-row' }, SHARE_LABELS.map(([k, label]) => h('label', { class: 'dr-check', for: 'dr-ad-' + k }, adBoxes[k].box, ' ' + label)))),
      slideRow,
      h('div', { class: 'dr-row' }, save, status)].filter(Boolean));
    refresh();
  }

  // ---------------------------------------------------------------- compare (read-only)
  const compareHost = h('div', { class: 'pg-card dr-compare' });
  const cmp = { picked: new Set(), metric: 'spend_total', data: null };
  const METRICS = [['spend_total', 'Marketing spend', 'usd0'], ['nc_rev', 'New-customer revenue', 'usd0'], ['nc_roas', 'NC-ROAS', 'x'], ['total_revenue', 'Total revenue', 'usd0']];
  async function drawCompare() {
    const metric = METRICS.find((m) => m[0] === cmp.metric);
    const chipsBox = h('div', { class: 'segs dr-compare-chips', role: 'group', 'aria-label': 'Scenarios to compare' },
      allScenarios.filter((s) => s.slug !== slug).map((s) => h('button', { type: 'button', class: 'seg', 'aria-pressed': String(cmp.picked.has(s.slug)),
        onclick: () => { cmp.picked.has(s.slug) ? cmp.picked.delete(s.slug) : cmp.picked.add(s.slug); loadCompare(); } }, s.name)));
    const metricSegs = h('div', { class: 'segs', role: 'group', 'aria-label': 'Measure' }, METRICS.map(([k, label]) => h('button', { type: 'button', class: 'seg', 'aria-pressed': String(cmp.metric === k),
      onclick: () => { cmp.metric = k; drawCompare(); } }, label)));
    const kids = [h('div', { class: 'dr-row' }, h('h2', null, 'Compare scenarios'), h('span', { class: 'status' }, 'View only. Pick scenarios to put next to this one.')),
      allScenarios.length > 1 ? chipsBox : h('p', { class: 'dr-legend' }, 'There is only one scenario. Clone this one to try a different spend plan.')];
    if (cmp.data && cmp.picked.size) {
      const d = cmp.data;
      const base = d.scenarios.find((s) => s.slug === (d.view || slug));
      const others = d.scenarios.filter((s) => s.slug !== base.slug);
      const canvas2 = h('canvas', { 'aria-label': `${metric[1]} by month, ${base.name} compared with ${others.map((o) => o.name).join(', ')}` });
      kids.push(h('p', { class: 'dr-legend' }, `Comparing ${base.name} with ${others.map((o) => o.name).join(', ')}`), metricSegs, h('div', { class: 'pg-chartbox' }, canvas2));
      const years = Object.keys(base.fy).filter((y) => y >= '2026');
      const f = (v, u) => (v == null ? '' : u === 'x' ? fmt.x(v, 2) : fmt.compact(v));
      const body = [h('tr', null, h('td', null, `${base.name} (this one)`), years.map((y) => h('td', null, f(base.fy[y][cmp.metric], metric[2]))))];
      for (const s of others) {
        body.push(h('tr', null, h('td', null, s.name), years.map((y) => h('td', null, f(s.fy[y][cmp.metric], metric[2])))));
        body.push(h('tr', null, h('td', null, `${base.name} compared with ${s.name}`), years.map((y) => {      // this scenario minus the other: positive means this one is higher
          const dd = ((s.vs_view || {}).fy || {})[y] ? s.vs_view.fy[y][cmp.metric] : null;
          if (!dd) return h('td', null, '');
          return h('td', { class: dd.diff < 0 ? 'neg' : '' }, (dd.diff >= 0 ? '+' : '-') + f(Math.abs(dd.diff), metric[2]) + (metric[2] === 'x' || dd.pct == null ? '' : ` (${signed(dd.pct)})`));
        })));
      }
      kids.push(h('div', { class: 'dr-scroll' }, h('table', { class: 'dr-fy' }, h('thead', null, h('tr', null, h('th', null, `${metric[1]} by fiscal year`), years.map((y) => h('th', null, `FY${y.slice(2)}*`)))), h('tbody', null, body))));
      compareHost.replaceChildren(...kids);
      charts.scenarioCompare(canvas2, { months: d.months, status: d.status, baseline: { name: base.name, values: base.series[cmp.metric] },
        others: others.map((o) => ({ name: o.name, values: o.series[cmp.metric] })), fmt: metric[2] });
      return;
    }
    compareHost.replaceChildren(...kids);
  }
  async function loadCompare() {
    if (!cmp.picked.size) { cmp.data = null; drawCompare(); return; }
    try {
      cmp.data = await ctx.api.get('drivers/compare', { scenarios: [slug, ...cmp.picked].join(',') });
      if (ctx.alive()) drawCompare();
    } catch (e) { toast(e.message, 'error'); }
  }

  // ---------------------------------------------------------------- assemble
  drawHeader();
  drawGrid();
  drawLegend();
  drawSettings();
  drawCue();
  root.replaceChildren(headerHost, statusHost, cue, toolHost, chartCard, refHost, gridHost, legend, compareHost, settingsHost);
  drawChart();
  if (isCopy && info.base_slug && allScenarios.some((x) => x.slug === info.base_slug)) cmp.picked.add(info.base_slug);
  if (cmp.picked.size) loadCompare(); else drawCompare();

  const onKey = (ev) => {
    if (!(ev.ctrlKey || ev.metaKey) || ev.shiftKey || ev.key.toLowerCase() !== 'z' || !canUndoFill()) return;
    if (ev.target && ev.target.matches && ev.target.matches('input, textarea, select')) return;
    if (grid && grid.undoStack && grid.undoStack.length) return;            // a cell edit made after the fill undoes first
    ev.preventDefault();
    undoFill();
  };
  document.addEventListener('keydown', onKey);
  cleanups.push(() => document.removeEventListener('keydown', onKey));

  return () => {
    clearTimeout(previewTimer);
    previewSeq += 1;
    cleanups.forEach((fn) => { try { fn(); } catch { /* already gone */ } });
    if (grid) grid.destroy();
  };
}
