// Projection page: P&L.
// What it shows: revenue down to net operating income by month (QuickBooks months, then the projection), the margin trend, what the business is worth
// at the chosen multiples of full-year net operating income against the offer, scenarios side by side, and the driver behind every projected cost line.
// Edits: a projected cell (a monthly override, audited through PUT /series), a cost driver (PUT /pnl/drivers), settings (PUT /pnl/settings), and the
// monthly QuickBooks export (POST /pnl/upload: preview, then commit).
import { events } from './lib/api.js';
import { confirmBox } from './lib/util.js';
import { mountCurvePicker } from './cohorts_curve_picker.js';

const ID = 'pn-style';
const CSS = `
.pn-two { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: var(--lv-sp-4); }
.pn-card-head { display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: var(--lv-sp-2) var(--lv-sp-4); margin-bottom: var(--lv-sp-2); }
.pn-card-head h2, .pn-card-head h3 { margin: 0; }
.pn-table { width: 100%; min-width: 520px; border-collapse: collapse; font-size: var(--lv-fs-sm); font-variant-numeric: tabular-nums; }
.pn-table th, .pn-table td { padding: 7px 10px; text-align: right; border-bottom: 1px solid var(--lv-line-soft); white-space: nowrap; vertical-align: top; }
.pn-table th:first-child, .pn-table td:first-child { text-align: left; white-space: normal; }
.pn-table thead th { font-size: var(--lv-fs-2xs); text-transform: uppercase; letter-spacing: var(--lv-track-kicker); color: var(--lv-muted); font-weight: var(--lv-fw-bold); }
.pn-table td small { display: block; color: var(--lv-muted); font-size: var(--lv-fs-xs); font-weight: var(--lv-fw-regular); }
.pn-table td.good, .pn-table td small.good { color: var(--lv-good); }
.pn-table td.bad, .pn-table td small.bad { color: var(--lv-bad); }
.pn-table td.text, .pn-table th.text { text-align: left; white-space: normal; }
.pn-table .num { font-weight: var(--lv-fw-semi); }
.pn-scroll { overflow-x: auto; }
.pn-sku input { max-width: 120px; text-align: right; }
.pn-table.drivers { table-layout: fixed; min-width: 600px; }
.pn-table.drivers td, .pn-table.drivers th { overflow-wrap: anywhere; }
.pn-offer { font-weight: var(--lv-fw-bold); }
.pn-fields { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: var(--lv-sp-3) var(--lv-sp-5); align-items: start; }
.pn-field { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
.pn-field > label { font-size: var(--lv-fs-sm); font-weight: var(--lv-fw-semi); color: var(--lv-ink-2); }
.pn-field input, .pn-field select { width: 100%; }
.pn-field .hint { font-size: var(--lv-fs-xs); color: var(--lv-muted); }
.pn-actions { display: flex; flex-wrap: wrap; align-items: center; gap: var(--lv-sp-2); margin-top: var(--lv-sp-3); }
.pn-actions .status { font-size: var(--lv-fs-sm); color: var(--lv-muted); }
.pn-actions .status.err { color: var(--lv-bad); font-weight: var(--lv-fw-semi); }
.pn-group-title { margin: var(--lv-sp-4) 0 4px; font-size: var(--lv-fs-2xs); text-transform: uppercase; letter-spacing: var(--lv-track-kicker); color: var(--lv-muted); font-weight: var(--lv-fw-bold); }
.pn-group-title:first-child { margin-top: 0; }
.pn-edited { color: var(--lv-ink); font-weight: var(--lv-fw-semi); }
.pn-details > summary { cursor: pointer; font-size: var(--lv-fs-md); font-weight: var(--lv-fw-heavy); padding: var(--lv-sp-2) 0; }
.pn-dlg { width: min(900px, calc(100vw - 24px)); max-height: calc(100vh - 32px); overflow: auto; }
.pn-dlg .pg-dialog-body { max-height: none; }
.pn-steps { display: flex; flex-direction: column; gap: var(--lv-sp-2); }
.pn-step { display: grid; grid-template-columns: 150px minmax(0, 1fr) auto; gap: var(--lv-sp-2); align-items: end; padding: var(--lv-sp-2); background: var(--lv-surface-2); border-radius: var(--lv-r-sm); }
.pn-step .params { display: flex; flex-wrap: wrap; gap: var(--lv-sp-2); align-items: end; }
.pn-step label { display: flex; flex-direction: column; gap: 2px; font-size: var(--lv-fs-xs); color: var(--lv-muted); font-weight: var(--lv-fw-semi); }
.pn-drop { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 4px; padding: var(--lv-sp-5); border: 2px dashed var(--lv-line-strong); border-radius: var(--lv-r-md); background: var(--lv-surface-2); text-align: center; cursor: pointer; }
.pn-drop:focus-within, .pn-drop:hover { border-color: var(--lv-action); }
.pn-drop input { max-width: 100%; }
.pn-up { display: flex; flex-direction: column; gap: var(--lv-sp-3); }
.pn-up .counts { display: grid; grid-template-columns: repeat(auto-fill, minmax(120px, 1fr)); gap: var(--lv-sp-2); }
.pn-up .counts div { background: var(--lv-surface-2); border-radius: var(--lv-r-sm); padding: 8px 10px; }
.pn-up .counts b { display: block; font-size: var(--lv-fs-lg); font-weight: var(--lv-fw-heavy); }
.pn-up .counts span { font-size: var(--lv-fs-xs); color: var(--lv-muted); }
.pn-up ul { margin: 0; padding-left: 18px; color: var(--lv-ink-2); font-size: var(--lv-fs-sm); }
.pn-legend { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 14px; margin: 0 0 var(--lv-sp-2); font-size: var(--lv-fs-sm); color: var(--lv-ink-2); }
.pn-legend .grp { display: inline-flex; align-items: center; gap: 6px; }
.pn-review { padding: var(--lv-sp-2) var(--lv-sp-3); background: var(--lv-warn-bg); border-radius: var(--lv-r-sm); }
.pn-block { padding: var(--lv-sp-2) var(--lv-sp-3); background: var(--lv-bad-bg); border-radius: var(--lv-r-sm); color: var(--lv-bad); font-weight: var(--lv-fw-semi); }
.pn-block ul, .pn-review ul { margin: 4px 0 0; }
.pn-check { display: inline-flex; align-items: center; gap: 6px; font-size: var(--lv-fs-sm); }
.pn-step-label { font-size: var(--lv-fs-2xs); text-transform: uppercase; letter-spacing: var(--lv-track-kicker); color: var(--lv-muted); font-weight: var(--lv-fw-bold); margin: 0; }
@media (max-width: 960px) { .pn-two { grid-template-columns: 1fr; } .pn-step { grid-template-columns: 1fr; } }
`;

function ensureStyle() {
  if (document.getElementById(ID)) return;
  const s = document.createElement('style');
  s.id = ID;
  s.textContent = CSS;
  document.head.append(s);
}

const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const mon = (iso) => { const [y, m] = String(iso).split('-'); return `${MON[Number(m) - 1]} ${y}`; };
const BASES = { amazon_rev: 'Amazon revenue', dtc_rev: 'DTC revenue', total_revenue: 'Total company revenue' };
const MODES = { fixed: 'Fixed amount a month', carry: 'Same as last month', avg: 'Average of previous months', rate: 'Share of revenue', headcount: 'Headcount formula' };
const RATE_KINDS = { window: 'Trailing actual months', ratio_avg: 'Mean of monthly ratios', typed: 'Typed percent', cogs_model: 'COGS model' };

// multipart upload: the shared fetch layer is JSON only
async function postUpload(file, { commit = false, token = '', confirm = false } = {}) {
  const fd = new FormData();
  fd.append('file', file, file.name);
  if (commit) fd.append('diff_sha256', token);
  if (commit && confirm) fd.append('confirm_review', '1');
  const res = await fetch('/api/projection/pnl/upload' + (commit ? '?commit=1' : ''), { method: 'POST', body: fd, credentials: 'same-origin' });
  if (res.status === 401) { location.assign('/login?next=' + encodeURIComponent(location.pathname + location.search)); throw new Error('Sign in required'); }
  let data = null;
  try { data = await res.json(); } catch { /* not JSON */ }
  if (!res.ok) {
    const msg = res.status === 429 ? 'Too many uploads in a short time. Wait a bit and try again.' : (data && data.error) || `The server answered ${res.status}`;
    const err = new Error(msg);
    err.status = res.status;
    throw err;
  }
  return data;
}

export async function render(el, ctx) {
  ensureStyle();
  const { h, fmt, ui, charts, toast } = ctx;
  const slug = ctx.scenario;
  const root = h('div', { class: 'pg-page' });
  el.replaceChildren(root);
  ui.loading(root, 'Loading the P&L');

  let summary, table, metrics, drivers, ev, comp, checks, uploads;
  try {
    [summary, table, metrics, drivers, ev, comp, checks, uploads] = await Promise.all([
      ctx.api.get('pnl/summary'), ctx.api.get('pnl/table'), ctx.api.get('pnl/metrics'), ctx.api.get('pnl/drivers'),
      ctx.api.get('pnl/ev'), ctx.api.get('pnl/compare'), ctx.api.get('pnl/checks'), ctx.api.get('pnl/uploads'),
    ]);
  } catch (e) { ui.error(root, e, ctx.reload); return undefined; }
  if (!ctx.alive()) return undefined;

  const cleanups = [];
  const money = (v) => (Number.isFinite(v) ? fmt.compact(v) : 'none');

  // ---------------------------------------------------------------- header and TLDR
  function tldrFor(s) {
    const projected = s.actual_year == null || s.basis_year > s.actual_year ? '*' : '';
    const fy = `FY${String(s.basis_year).slice(2)}`;
    const fresh = s.freshness || {};
    const freshTone = fresh.status === 'fresh' ? 'good' : fresh.status === 'stale' ? 'bad' : 'warn';
    let line;
    if (s.noi == null) line = 'There is no profit and loss to project yet.';
    else if (s.noi < 0) line = `${fy} net operating income is ${money(s.noi)}${projected}, so a multiple of earnings gives no value.`;
    else line = `${fy} net operating income is ${money(s.noi)}${projected}: ${money(s.ev_low)} to ${money(s.ev_high)}${projected} at ${s.multiple_range}, against the ${money(s.loi)} offer.`;
    let evTone = '';
    if (s.ev_low != null && s.loi) evTone = s.ev_low >= s.loi ? 'good' : s.ev_high < s.loi ? 'bad' : 'warn';
    const through = s.actual_through ? mon(s.actual_through) : 'none loaded';
    const qbNote = s.status && s.status.feeds_span ? `Feeds ${s.status.feeds_span}` : '';
    return {
      line,
      kpis: [
        { label: `${fy} revenue`, value: `${money(s.revenue)}${projected}` },
        { label: `${fy} net operating income`, value: `${money(s.noi)}${projected}`, note: s.noi_margin != null ? `${fmt.pct(s.noi_margin, 1)} margin` : '' },
        { label: `Implied value, ${s.multiple_range}`, value: s.ev_low == null ? 'none' : `${money(s.ev_low)} to ${money(s.ev_high)}${projected}`, note: `Offer ${money(s.loi)}`, tone: evTone },
        { label: `${fy} gross margin`, value: s.gross_profit_pct != null ? `${fmt.pct(s.gross_profit_pct, 1)}${projected}` : 'none' },
        { label: 'QuickBooks', value: s.actual_through ? `Books through ${through}` : 'No books loaded', note: qbNote, tone: freshTone },
      ],
    };
  }

  const uploadBtn = h('button', { type: 'button', class: 'btn primary', onclick: () => openUpload() }, 'Upload QuickBooks export');
  let headerEl = null;
  const sourceP = h('p', { class: 'pg-source' });
  function drawHeader() {
    const next = ui.pageHeader({
      title: 'P&L',
      purpose: 'Profit and loss from revenue down to net operating income (NOI, profit before tax and financing), with the driver behind every projected cost line.',
      actions: [uploadBtn], tldr: tldrFor(summary),
    });
    if (headerEl) headerEl.replaceWith(next);
    headerEl = next;
    const f = summary.freshness;
    const due = summary.next_due;
    const late = f && f.status === 'stale' && due ? ` The ${new Date(due.month + 'T00:00:00').toLocaleString('en-US', { month: 'long' })} books were due ${fmt.date(due.due_by)}.` : '';
    sourceP.textContent = [summary.quickbooks_text + late, ...(summary.warnings || [])].join(' ');
  }
  drawHeader();

  // ---------------------------------------------------------------- charts
  const revCanvas = h('canvas', { 'aria-label': 'Total company revenue and net operating income by month' });
  const marginCanvas = h('canvas', { 'aria-label': 'Gross margin and net operating income margin by month' });
  const rowOf = (t, key) => (t.rows.find((r) => r.key === key) || { values: [], label: key });
  function drawCharts() {
    charts.destroyAll();
    const rev = rowOf(table, 'total_revenue'), noi = rowOf(table, 'noi'), gp = rowOf(table, 'gross_profit_pct'), nm = rowOf(table, 'noi_margin');
    charts.comboChart(revCanvas, { months: table.months, status: table.status, yFmt: 'usd0',
      series: [{ label: rev.label, values: rev.values, type: 'bar', axis: 'y' }, { label: noi.label, values: noi.values, type: 'line', axis: 'y' }] });
    charts.lineChart(marginCanvas, { months: table.months, status: table.status, yFmt: 'pct',
      series: [{ label: gp.label, values: gp.values, fmt: 'pct' }, { label: nm.label, values: nm.values, fmt: 'pct' }] });
  }
  const chartRow = h('div', { class: 'pn-two' },
    h('div', { class: 'pg-card' }, h('div', { class: 'pn-card-head' }, h('h2', 'Revenue and net operating income')), h('div', { class: 'pg-chartbox' }, revCanvas)),
    h('div', { class: 'pg-card' }, h('div', { class: 'pn-card-head' }, h('h2', 'Margins')), h('div', { class: 'pg-chartbox' }, marginCanvas)));

  // ---------------------------------------------------------------- the grid
  const gridHost = h('div');
  const collapsedAtStart = table.rows.filter((r) => r.kind === 'header').map((r) => r.key);
  const grid = ctx.grid(gridHost, { payload: table, title: 'P&L', fy: true, groupCollapse: collapsedAtStart, readOnly: true });
  cleanups.push(() => grid.destroy());
  const ss = table.status_summary || {};
  const legend = h('div', { class: 'pn-legend', 'aria-label': 'What each month is based on' },
    h('span', { class: 'grp' }, ui.chip('BOOKS', 'good'), ss.books_through ? `QuickBooks, through ${mon(ss.books_through)}` : 'No QuickBooks loaded'),
    ss.feeds_span ? h('span', { class: 'grp' }, ui.chip('FEEDS', 'info'), `${ss.feeds_span}: revenue from the cohort and ad feeds, costs projected`) : null,
    h('span', { class: 'grp' }, ui.chip('PROJECTED', 'info'), ss.projected_from ? `From ${mon(ss.projected_from)}` : 'No projected months'));
  const gridCard = h('div', { class: 'pg-card' },
    h('div', { class: 'pn-card-head' }, h('h2', 'Profit and loss by month')),
    legend, gridHost);

  // ---------------------------------------------------------------- trends
  const trendHost = h('div');
  let trendGrid = null;
  const trendDetails = h('details', { class: 'pg-card pn-details' }, h('summary', 'Trailing twelve months, run-rate and growth'), trendHost);
  trendDetails.addEventListener('toggle', () => {
    if (trendDetails.open && !trendGrid) {
      trendGrid = ctx.grid(trendHost, { payload: metrics, readOnly: true, groupCollapse: false, fy: false, title: 'Trends' });
      cleanups.push(() => trendGrid && trendGrid.destroy());
    }
  });

  // ---------------------------------------------------------------- value (implied enterprise value)
  function evSection() {
    const mults = ev.multiples.map((m) => `${m}x`);
    const diffTxt = (v) => (v == null ? '' : `${v >= 0 ? '+' : '-'}${fmt.compact(Math.abs(v))} vs offer`);
    const rows = ev.rows.map((r) => h('tr', null,
      h('td', null, r.name, r.slug === slug ? h('small', 'This scenario') : null),
      h('td', null, money(r.basis_noi)),
      mults.map((k) => {
        const v = r.values[k];
        const d = r.vs_loi[k];
        return h('td', { class: v == null ? '' : d >= 0 ? 'good' : 'bad' }, h('span', { class: 'num' }, v == null ? 'none' : money(v)), h('small', { class: d == null ? '' : d >= 0 ? 'good' : 'bad' }, diffTxt(d)));
      }),
      (ev.offer_years || []).map((y) => h('td', null, h('span', { class: 'num' }, r.offer_multiple[y] == null ? 'none' : fmt.x(r.offer_multiple[y], 1))))));
    return h('div', { class: 'pg-section' }, h('div', { class: 'pg-card' },
      h('div', { class: 'pn-card-head' }, h('h2', 'Implied value'), h('span', { class: 'pg-source' }, `Offer ${money(ev.loi)}`)),
      h('div', { class: 'pn-scroll' }, h('table', { class: 'pn-table' },
        h('thead', null, h('tr', null, h('th', 'Scenario'), h('th', `FY${String(ev.year).slice(2)} net operating income`), mults.map((k) => h('th', k)), (ev.offer_years || []).map((y) => h('th', `Offer / FY${String(y).slice(2)}`)))),
        h('tbody', null, rows))),
      ui.source(ev.caption + ' The last columns show the offer as a multiple of each year\'s net operating income. Projected values are marked *.')));
  }

  // ---------------------------------------------------------------- data checks (typed figures next to the cohort figures)
  function checksSection() {
    if (!checks.rows || !checks.rows.length) return null;
    const cell = (r, v) => h('td', null, r.fmt === 'pct' ? (v == null ? '' : h('span', { class: v >= 0 ? 'num' : 'num bad' }, `${v >= 0 ? '+' : '-'}${Math.abs(v * 100).toFixed(1)}%`)) : (v == null ? '' : h('span', { class: 'num' }, fmt.money(v))));
    return h('div', { class: 'pg-section' }, h('div', { class: 'pg-card' },
      h('div', { class: 'pn-card-head' }, h('h2', 'Data checks'), h('span', { class: 'pg-source' }, 'Typed figures against the cohort data')),
      h('div', { class: 'pn-scroll' }, h('table', { class: 'pn-table' },
        h('thead', null, h('tr', null, h('th', 'Source'), checks.months.map((m) => h('th', mon(m))))),
        h('tbody', null, checks.rows.map((r) => h('tr', null, h('td', null, r.label), r.values.map((v) => cell(r, v))))))),
      ui.source(checks.note)));
  }

  // ---------------------------------------------------------------- QuickBooks uploads (history and undo)
  function uploadsSection() {
    const list = (uploads && uploads.uploads) || [];
    if (!list.length) return null;
    const fmtAt = (iso) => new Date(iso).toLocaleString('en-US', { month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit' });
    const rows = list.map((u) => h('tr', null,
      h('td', null, fmtAt(u.at)), h('td', { class: 'text' }, u.filename), h('td', null, u.actor || ''),
      h('td', { class: 'text' }, (u.months || []).map(mon).join(', ')),
      h('td', null, `${u.new_cells} added, ${u.changed_cells} changed`),
      h('td', null, u.status === 'undone' ? ui.chip('UNDONE', 'neutral') : ui.chip('APPLIED', 'good')),
      h('td', null, u.can_undo ? h('button', { type: 'button', class: 'btn small', onclick: () => undoUpload(u) }, 'Undo last upload') : '')));
    return h('div', { class: 'pg-section' }, h('div', { class: 'pg-card' },
      h('div', { class: 'pn-card-head' }, h('h2', 'QuickBooks uploads')),
      h('div', { class: 'pn-scroll' }, h('table', { class: 'pn-table' },
        h('thead', null, h('tr', null, h('th', 'When'), h('th', { class: 'text' }, 'File'), h('th', 'Who'), h('th', { class: 'text' }, 'Months'), h('th', 'Cells'), h('th', 'Status'), h('th', ''))),
        h('tbody', null, rows))),
      ui.source('Undo puts back exactly the values the latest upload replaced and removes the ones it added.')));
  }
  async function undoUpload(u) {
    await confirmBox({
      title: 'Undo the last upload', message: `This puts back the ${u.changed_cells} values ${u.filename} replaced and removes the ${u.new_cells} it added. The projection returns to how it was before the upload.`,
      confirmText: 'Undo upload', danger: false,
      onConfirm: async () => { await ctx.api.post('pnl/upload/undo', { upload_id: u.id }); events.emit('saved', { scenario: slug }); toast('Upload undone', 'ok'); ctx.refreshHeadline(); ctx.reload(); },
    });
  }

  // ---------------------------------------------------------------- scenario compare
  function compareSection() {
    if (comp.scenarios.length < 2) return null;
    const base = comp.scenarios.find((s) => s.slug === 'current') || comp.scenarios[0];
    const block = (key, title) => {
      const body = comp.scenarios.map((s) => h('tr', null,
        h('td', null, s.name),
        comp.years.map((y, i) => {
          const v = s[key][i];
          const d = s === base || v == null || base[key][i] == null ? null : v - base[key][i];
          return h('td', null, h('span', { class: 'num' }, money(v)), d == null ? null : h('small', { class: d >= 0 ? 'good' : 'bad' }, `${d >= 0 ? '+' : '-'}${fmt.compact(Math.abs(d))}`));
        })));
      return h('div', null, h('div', { class: 'pn-group-title' }, title),
        h('div', { class: 'pn-scroll' }, h('table', { class: 'pn-table' }, h('thead', null, h('tr', null, h('th', 'Scenario'), comp.years.map((y) => h('th', `FY${String(y).slice(2)}`)))), h('tbody', null, body))));
    };
    return h('div', { class: 'pg-section' }, h('div', { class: 'pg-card' },
      h('div', { class: 'pn-card-head' }, h('h2', 'Scenarios side by side'), h('span', { class: 'pg-source' }, `Difference is against ${base.name}`)),
      block('noi', 'Net operating income'), block('total_revenue', 'Total company revenue'), block('gross_profit', 'Gross profit')));
  }

  // ---------------------------------------------------------------- cost drivers
  const own = drivers.own_settings || {};
  function driverSection() {
    const groups = drivers.groups.map((g) => h('div', null,
      h('div', { class: 'pn-group-title' }, g.title),
      h('div', { class: 'pn-scroll' }, h('table', { class: 'pn-table drivers' },
        h('colgroup', null, h('col', { style: { width: '27%' } }), h('col', { style: { width: '47%' } }), h('col', { style: { width: '14%' } }), h('col', { style: { width: '12%' } })),
        h('thead', null, h('tr', null, h('th', 'Line'), h('th', { class: 'text' }, 'How it is projected'), h('th', 'Now'), h('th', ''))),
        h('tbody', null, g.items.map((it) => {
          const now = it.rate != null ? fmt.pct(it.rate, 2) : it.value != null ? fmt.money(it.value, { digits: it.value < 100 && it.value ? 2 : 0 }) : '';
          const basis = it.window_text ? `Based on ${it.window_text}` : '';
          return h('tr', null,
            h('td', null, h('span', { class: it.overridden ? 'pn-edited' : '' }, it.label), it.overridden ? h('small', 'Changed in this scenario') : null),
            h('td', { class: 'text' }, it.text, basis ? h('small', basis) : null, it.segments.length > 1 ? h('small', `${it.segments.length} steps over time`) : null,
            it.overrides && it.overrides.length ? h('small', `${it.overrides.length} month${it.overrides.length === 1 ? '' : 's'} typed over`) : null),
            h('td', null, h('span', { class: 'num' }, now)),
            h('td', null, h('button', { type: 'button', class: 'btn small', 'aria-label': `Change how ${it.label} is projected`, onclick: () => openDriverEditor(it) }, 'Change')));
        }))))));
    return h('div', { class: 'pg-section' }, h('div', { class: 'pg-card' },
      h('div', { class: 'pn-card-head' }, h('h2', 'Cost drivers'),
        h('span', { class: 'pg-source' }, drivers.window_text ? `Trailing rates read ${drivers.window_text}` : 'No QuickBooks months loaded')),
      groups,
      ui.source('Each projected month follows these drivers. Change one here, or type over a single projected month from its Change dialog.')));
  }

  function openDriverEditor(item) {
    const dlg = h('dialog', { class: 'pg-dialog pn-dlg' });
    const err = h('div', { class: 'pg-dialog-err', role: 'alert' });
    let steps = JSON.parse(JSON.stringify(item.segments.length ? item.segments : [{ from: null, mode: 'fixed', value: 0 }]));
    const list = h('div', { class: 'pn-steps' });
    const allowed = item.key === 'headcount' ? ['headcount', 'fixed'] : ['fixed', 'carry', 'avg', 'rate'];
    const select = (opts, value, onchange, label) => {
      const s = h('select', { class: 'pg-input', 'aria-label': label }, Object.entries(opts).map(([k, v]) => h('option', { value: k, selected: k === value }, v)));
      s.addEventListener('change', () => onchange(s.value));
      return s;
    };
    const num = (value, onchange, { step = 'any', min, max, label }) => {
      const i = h('input', { type: 'number', class: 'pg-input', step, min, max, value: value == null ? '' : String(value), inputmode: 'decimal', 'aria-label': label });
      i.addEventListener('input', () => onchange(i.value === '' ? null : Number(i.value)));
      return i;
    };
    function stepRow(s, i) {
      const fromCell = i === 0 ? h('div', { class: 'muted' }, 'From the first month')
        : h('label', null, 'From', (() => {
          const m = h('input', { type: 'month', class: 'pg-input', value: s.from ? s.from.slice(0, 7) : '', 'aria-label': `Step ${i + 1} starts` });
          m.addEventListener('input', () => { s.from = m.value ? m.value + '-01' : null; });
          return m;
        })());
      const params = h('div', { class: 'params' });
      params.append(h('label', null, 'How', select(Object.fromEntries(allowed.map((k) => [k, MODES[k]])), s.mode, (v) => { s.mode = v; Object.assign(s, defaultsFor(v, s)); renderSteps(); }, `Step ${i + 1} method`)));
      if (s.mode === 'fixed') params.append(h('label', null, 'Dollars a month', num(s.value, (v) => { s.value = v; }, { label: 'Dollars a month' })));
      if (s.mode === 'avg') params.append(h('label', null, 'Previous months', num(s.window, (v) => { s.window = v; }, { step: 1, min: 1, max: 36, label: 'Previous months' })));
      if (s.mode === 'rate') {
        params.append(h('label', null, 'Of', select(BASES, s.base, (v) => { s.base = v; }, 'Revenue base')));
        params.append(h('label', null, 'Rate from', select(RATE_KINDS, s.rate_kind || 'window', (v) => { s.rate_kind = v; renderSteps(); }, 'Rate source')));
        if (['window', 'ratio_avg'].includes(s.rate_kind || 'window')) params.append(h('label', null, 'Months (blank = setting)', num(s.window, (v) => { s.window = v; }, { step: 1, min: 1, max: 36, label: 'Months' })));
        if (s.rate_kind === 'typed') params.append(h('label', null, 'Percent', num(s.rate == null ? null : Math.round(s.rate * 1e6) / 1e4, (v) => { s.rate = v == null ? null : v / 100; }, { label: 'Percent' })));
      }
      const del = i === 0 ? h('span') : h('button', { type: 'button', class: 'btn small', 'aria-label': `Remove step ${i + 1}`, onclick: () => { steps.splice(i, 1); renderSteps(); } }, 'Remove');
      return h('div', { class: 'pn-step' }, fromCell, params, del);
    }
    function defaultsFor(mode, s) {
      if (mode === 'fixed') return { value: s.value ?? 0 };
      if (mode === 'avg') return { window: s.window ?? drivers.settings.engine.rate_window_months };
      if (mode === 'rate') return { base: s.base || 'total_revenue', rate_kind: s.rate_kind || 'window' };
      return {};
    }
    function renderSteps() { list.replaceChildren(...steps.map(stepRow)); }
    renderSteps();
    const clean = () => steps.map((s) => {
      const o = { from: s.from || null, mode: s.mode };
      if (s.mode === 'fixed') o.value = s.value == null ? NaN : s.value;
      if (s.mode === 'avg') o.window = s.window == null ? drivers.settings.engine.rate_window_months : s.window;
      if (s.mode === 'rate') {
        o.base = s.base; o.rate_kind = s.rate_kind || 'window';
        if (s.window != null && ['window', 'ratio_avg'].includes(o.rate_kind)) o.window = s.window;
        if (o.rate_kind === 'typed') o.rate = s.rate == null ? NaN : s.rate;
      }
      return o;
    });
    let ovChanged = false;
    const close = () => { dlg.close(); dlg.remove(); if (ovChanged) ctx.reload(); };
    // typed months: a one-off amount for a single projected month (saved as it is changed; audited like any scenario edit)
    const overrides = (item.overrides || []).map((o) => ({ ...o }));
    const ovList = h('div', { class: 'pn-steps' });
    const monthMin = drivers.first_projected ? drivers.first_projected.slice(0, 7) : '2025-01';
    // The shell asks inline before the first edit of the plan of record; a modal dialog would hide that question, so the dialog steps aside during a save.
    async function whileAside(fn) {
      dlg.close();
      try { return await fn(); } finally { if (dlg.isConnected) dlg.showModal(); }
    }
    async function putOverride(month, value) {
      err.textContent = '';
      try {
        await whileAside(() => ctx.api.saveSeries([{ line_key: 'pnl.' + item.key, month, value, kind: 'override' }]));
        events.emit('saved', { scenario: slug });
        ovChanged = true;
        return true;
      } catch (e) { err.textContent = e.name === 'EditCancelled' ? '' : e.message; return false; }
    }
    function renderOverrides() {
      const rows = overrides.map((o, i) => {
        const v = h('input', { type: 'number', class: 'pg-input', step: 'any', value: String(o.value), 'aria-label': `Amount for ${mon(o.month)}` });
        v.addEventListener('change', async () => { const n = Number(v.value); if (v.value !== '' && Number.isFinite(n) && await putOverride(o.month, n)) o.value = n; });
        return h('div', { class: 'pn-step' }, h('strong', mon(o.month)), v,
          h('button', { type: 'button', class: 'btn small', 'aria-label': `Remove the amount for ${mon(o.month)}`, onclick: async () => { if (await putOverride(o.month, null)) { overrides.splice(i, 1); renderOverrides(); } } }, 'Remove'));
      });
      const m = h('input', { type: 'month', class: 'pg-input', min: monthMin, max: '2029-12', 'aria-label': 'Month to type over' });
      const amt = h('input', { type: 'number', class: 'pg-input', step: 'any', placeholder: 'Dollars', 'aria-label': 'Amount for that month' });
      const add = h('button', { type: 'button', class: 'btn small', onclick: async () => {
        const month = m.value ? m.value + '-01' : '';
        const n = Number(amt.value);
        if (!month || m.value < monthMin) { err.textContent = `Pick a projected month, ${mon(monthMin + '-01')} or later. Earlier months come from QuickBooks.`; return; }
        if (amt.value === '' || !Number.isFinite(n)) { err.textContent = 'Enter the amount for that month.'; return; }
        if (await putOverride(month, n)) {
          const at = overrides.findIndex((o) => o.month === month);
          if (at >= 0) overrides[at].value = n; else overrides.push({ month, value: n });
          overrides.sort((a, b) => a.month.localeCompare(b.month));
          renderOverrides();
        }
      } }, 'Type over this month');
      ovList.replaceChildren(...rows, h('div', { class: 'pn-step' }, m, amt, add));
    }
    renderOverrides();
    async function save(segments) {
      err.textContent = '';
      try {
        await whileAside(() => ctx.api.put('pnl/drivers', { scenario: slug, key: item.key, segments }));
        events.emit('saved', { scenario: slug });
        toast(segments ? `${item.label} updated` : `${item.label} back to its default`, 'ok');
        ovChanged = true;
        close();
      } catch (e) { err.textContent = e.name === 'EditCancelled' ? '' : e.message; }
    }
    const saveBtn = h('button', { type: 'button', class: 'btn primary', onclick: () => {
      const seg = clean();
      if (seg.some((s) => Number.isNaN(s.value) || Number.isNaN(s.rate))) { err.textContent = 'Enter a number for every step.'; return; }
      save(seg);
    } }, 'Save');
    dlg.addEventListener('cancel', (e) => { e.preventDefault(); close(); });
    dlg.append(h('div', { class: 'pg-dialog-body' },
      h('h2', item.label),
      h('p', { class: 'muted', style: { margin: 0 } }, 'Each step applies from its month until the next step. This changes the current scenario only.'),
      list,
      h('div', null, h('button', { type: 'button', class: 'btn small', onclick: () => { steps.push({ from: null, mode: 'fixed', value: 0 }); renderSteps(); } }, 'Add a step')),
      h('p', { class: 'pn-group-title' }, 'Single months typed over'),
      h('p', { class: 'muted', style: { margin: 0 } }, 'A typed amount replaces the driver for that month only. Remove it to go back to the driver.'),
      ovList,
      err,
      h('div', { class: 'pg-dialog-actions' },
        item.overridden ? h('button', { type: 'button', class: 'btn', onclick: () => save(null) }, 'Back to default') : null,
        h('button', { type: 'button', class: 'btn', onclick: close }, 'Cancel'), saveBtn)));
    document.body.append(dlg);
    dlg.showModal();
  }

  // ---------------------------------------------------------------- settings
  // Every variable the projection uses is a field here or in the driver table above; each starts at the sheet's value and changes the current scenario only.
  function settingsSection() {
    const S = drivers.settings;
    const cm = drivers.cogs_model;
    const fields = [];
    const inputs = [];
    const field = (group, key, label, value, { scale = 1, step = 'any', min, max, hint, type = 'number', sku = null, into = fields } = {}) => {
      const input = h('input', { id: `pn-${group}-${key}${sku ? '-' + sku : ''}`, type, class: 'pg-input', step, min, max, inputmode: type === 'number' ? 'decimal' : undefined,
        'aria-label': label, value: value == null ? '' : type === 'number' ? String(Math.round(value * scale * 1e6) / 1e6) : String(value) });
      input.addEventListener('input', markDirty);
      inputs.push({ group, key, sku, input, scale, initial: input.value, type });
      if (into === fields) into.push(h('div', { class: 'pn-field' }, h('label', { for: input.id }, label), input, hint ? h('span', { class: 'hint' }, hint) : null));
      return input;
    };
    field('engine', 'rate_window_months', 'Months behind trailing rates', S.engine.rate_window_months, { step: 1, min: 1, max: 36, hint: drivers.window_text || 'Last actual months' });
    field('engine', 'opex_inflation', 'Operating cost inflation a year (%)', S.engine.opex_inflation, { scale: 100, step: 0.5, min: -50, max: 100, hint: 'Applied to projected months' });
    field('engine', 'cogs_inflation', 'Product cost inflation a year (%)', S.engine.cogs_inflation, { scale: 100, step: 0.5, min: -50, max: 100 });
    field('trend', 'avg_window', 'Months in growth averages', S.trend.avg_window, { step: 1, min: 1, max: 36, hint: 'Growth rows that show an average' });
    field('cogs', 'price_per_bottle', 'Average price per bottle ($)', S.cogs.price_per_bottle, { step: 0.01, min: 0.01,
      hint: cm.rate_at_current_price ? `Cost of goods is ${fmt.pct(cm.rate, 1)} of revenue at this price; ${fmt.pct(cm.rate_at_current_price, 1)} at the current price` : '' });
    field('cogs', 'current_price_per_bottle', 'Current price per bottle ($)', S.cogs.current_price_per_bottle, { step: 0.01, min: 0.01, hint: 'Shown as a comparison; it does not change the projection' });
    field('cogs', 'us_share', 'Bottles sourced in the US (%)', S.cogs.us_share, { scale: 100, step: 5, min: 0, max: 100, hint: `Blended landed cost ${fmt.money(cm.cost_per_bottle, { digits: 2 })} per bottle` });
    field('ev', 'year', 'Value year', S.ev.year, { step: 1, min: 2025, max: 2029 });
    field('ev', 'loi', 'Offer ($)', S.ev.loi, { step: 50000, min: 0 });
    field('ev', 'multiples', 'Multiples (comma separated)', (S.ev.multiples || []).join(', '), { type: 'text' });
    field('headcount', 'base', 'Headcount base a month ($)', S.headcount.base, { step: 500, min: 0 });
    field('headcount', 'threshold', 'Annual revenue before headcount steps up ($)', S.headcount.threshold, { step: 1000000, min: 1 });
    field('headcount', 'step', 'Revenue per step ($)', S.headcount.step, { step: 1000000, min: 1 });
    field('headcount', 'increment', 'Added per step a month ($)', S.headcount.increment, { step: 500, min: 0 });
    field('headcount', 'start', 'Headcount starts', S.headcount.start ? S.headcount.start.slice(0, 7) : '', { type: 'month' });
    // the sales tax deduction belongs to the spend plan; it is edited here through the same lever
    const tax = drivers.sales_tax || {};
    const taxInput = h('input', { id: 'pn-tax', type: 'number', class: 'pg-input', step: 0.5, min: 0, max: 50, 'aria-label': 'Sales tax deducted from DTC revenue (%)',
      value: tax.rate == null ? '' : String(Math.round(tax.rate * 1e6) / 1e4) });
    taxInput.addEventListener('input', markDirty);
    fields.push(h('div', { class: 'pn-field' }, h('label', { for: 'pn-tax' }, 'Sales tax deducted from DTC revenue (%)'), taxInput, h('span', { class: 'hint' }, tax.text || '')));
    const upc = drivers.upload_checks || {};
    const lowInput = h('input', { id: 'pn-up-low', type: 'number', class: 'pg-input', step: 5, min: 1, max: 100, 'aria-label': 'Flag a QuickBooks month below (% of neighbours)', value: String(Math.round((upc.low_ratio || 0) * 100)) });
    const highInput = h('input', { id: 'pn-up-high', type: 'number', class: 'pg-input', step: 10, min: 100, max: 10000, 'aria-label': 'Flag a QuickBooks month above (% of neighbours)', value: String(Math.round((upc.high_ratio || 0) * 100)) });
    [lowInput, highInput].forEach((i) => i.addEventListener('input', markDirty));
    fields.push(h('div', { class: 'pn-field' }, h('label', { for: 'pn-up-low' }, 'Flag an uploaded month below (% of its neighbours)'), lowInput, h('span', { class: 'hint' }, 'Income or cost, against the months around it and the plan. A flagged month needs a confirmation.')));
    fields.push(h('div', { class: 'pn-field' }, h('label', { for: 'pn-up-high' }, 'Flag an uploaded month above (% of its neighbours)'), highInput));
    const FAMILY_NAMES = { revenue: 'Revenue', cost_of_goods: 'Cost of goods', fulfillment: 'Fulfillment and shipping', marketing_spend: 'Ad spend', operating_expenses: 'Operating expenses' };
    const famBoxes = Object.entries(FAMILY_NAMES).map(([k, label]) => {
      const box = h('input', { type: 'checkbox', checked: (upc.required_families || []).includes(k), disabled: k === 'revenue', 'aria-label': `${label} accounts required` });
      box.addEventListener('change', markDirty);
      return { key: k, box, node: h('label', { class: 'pn-check' }, box, label) };
    });
    fields.push(h('div', { class: 'pn-field' }, h('span', { class: 'lbl', style: { fontSize: 'var(--lv-fs-sm)', fontWeight: 'var(--lv-fw-semi)' } }, 'A month needs these accounts to become books'),
      h('div', { style: { display: 'flex', flexDirection: 'column', gap: '4px' } }, famBoxes.map((f) => f.node)), h('span', { class: 'hint' }, 'Applies to every scenario.')));
    const initialFams = (upc.required_families || []).slice().sort().join(',');
    const upChanged = () => lowInput.value !== String(Math.round((upc.low_ratio || 0) * 100)) || highInput.value !== String(Math.round((upc.high_ratio || 0) * 100))
      || famBoxes.filter((f) => f.box.checked).map((f) => f.key).sort().join(',') !== initialFams;
    const taxChanged = () => taxInput.value !== (tax.rate == null ? '' : String(Math.round(tax.rate * 1e6) / 1e4));
    const lateInput = h('input', { id: 'pn-late', type: 'number', class: 'pg-input', step: 1, min: 1, max: 365, 'aria-label': 'Days before QuickBooks is late', value: String(drivers.late_after_days) });
    lateInput.addEventListener('input', markDirty);
    fields.push(h('div', { class: 'pn-field' }, h('label', { for: 'pn-late' }, 'Days before QuickBooks is late'), lateInput, h('span', { class: 'hint' }, 'Counted from the close of the first month not loaded. Applies to every scenario.')));
    const status = h('span', { class: 'status', role: 'status' }, Object.keys(own).length ? 'Custom settings in this scenario.' : 'Defaults.');
    const saveBtn = h('button', { type: 'button', class: 'btn primary', disabled: true, onclick: () => save() }, 'Save settings');
    const resetBtn = h('button', { type: 'button', class: 'btn', disabled: !Object.keys(own).filter((k) => k !== 'drivers').length, onclick: () => reset() }, 'Reset to defaults');
    function changed() { return inputs.filter((x) => x.input.value !== x.initial); }
    const lateChanged = () => lateInput.value !== String(drivers.late_after_days);
    function markDirty() {
      const n = changed().length + (lateChanged() ? 1 : 0) + (taxChanged() ? 1 : 0) + (upChanged() ? 1 : 0);
      saveBtn.disabled = n === 0;
      status.className = 'status';
      status.textContent = n ? 'Unsaved changes.' : (Object.keys(own).length ? 'Custom settings in this scenario.' : 'Defaults.');
    }
    async function put(settings) {
      status.className = 'status';
      status.textContent = 'Saving';
      try {
        if (lateChanged()) await ctx.api.put('pnl/freshness', { stale_after_days: Number(lateInput.value) });
        if (taxChanged()) await ctx.api.put('drivers/levers', { scenario: slug, levers: { sales_tax_rate: Number(taxInput.value) / 100 } });
        if (upChanged()) await ctx.api.put('pnl/upload-checks', { low_ratio: Number(lowInput.value) / 100, high_ratio: Number(highInput.value) / 100,
          required_families: famBoxes.filter((f) => f.box.checked).map((f) => f.key) });
        if (settings && Object.keys(settings).length) await ctx.api.put('pnl/settings', { scenario: slug, settings });
        events.emit('saved', { scenario: slug });
        toast('Settings saved', 'ok');
        ctx.reload();
      } catch (e) { status.className = 'status err'; status.textContent = e.message; saveBtn.disabled = false; }
    }
    function save() {
      const patch = {};
      for (const x of changed()) {
        let v;
        if (x.type === 'month') v = x.input.value ? x.input.value + '-01' : null;
        else if (x.key === 'multiples') v = x.input.value.split(',').map((t) => Number(t.trim())).filter((n) => Number.isFinite(n) && n > 0);
        else v = x.input.value === '' ? null : Number(x.input.value) / x.scale;
        const g = (patch[x.group] = patch[x.group] || {});
        if (x.sku) (g[x.key] = g[x.key] || {})[x.sku] = v; else g[x.key] = v;
      }
      saveBtn.disabled = true;
      put(patch);
    }
    function reset() {
      const patch = {};
      for (const g of Object.keys(own)) if (g !== 'drivers') patch[g] = null;
      put(patch);
    }
    const skuRows = cm.all_skus.map((r) => h('tr', null, h('td', null, r.label || r.sku),
      h('td', null, field('cogs', 'mix', `Sales mix for ${r.label || r.sku} (%)`, r.mix, { scale: 100, step: 5, min: 0, max: 100, sku: r.sku, into: [] })),
      h('td', null, field('cogs', 'china_cost', `China cost per unit for ${r.label || r.sku}`, r.china, { step: 0.01, min: 0, sku: r.sku, into: [] })),
      h('td', null, field('cogs', 'us_cost', `US cost per unit for ${r.label || r.sku}`, r.us, { step: 0.01, min: 0, sku: r.sku, into: [] }))));
    const vendors = drivers.vendors || [];
    return h('div', { class: 'pg-section' }, h('div', { class: 'pg-card' },
      h('div', { class: 'pn-card-head' }, h('h2', 'Settings')),
      h('div', { class: 'pn-fields' }, fields),
      h('div', { class: 'pn-group-title' }, 'Cost of goods sold model'),
      h('div', { class: 'pn-scroll' }, h('table', { class: 'pn-table pn-sku' },
        h('thead', null, h('tr', null, h('th', 'Product'), h('th', 'Sales mix (%)'), h('th', 'China cost per unit ($)'), h('th', 'US cost per unit ($)'))),
        h('tbody', null, skuRows))),
      ui.source(`Blended cost per bottle ${fmt.money(cm.cost_per_bottle, { digits: 2 })} divided by ${fmt.money(cm.price_per_bottle, { digits: 2 })} per bottle gives ${fmt.pct(cm.rate, 2)} of revenue.`),
      h('div', { class: 'pn-actions' }, saveBtn, resetBtn, status),
      vendors.length ? h('div', null,
        h('div', { class: 'pn-group-title' }, 'Vendor retainers a month'),
        h('div', { class: 'pn-scroll' }, h('table', { class: 'pn-table' },
          h('thead', null, h('tr', null, h('th', 'Vendor'), h('th', 'Current'), h('th', 'Proposed'))),
          h('tbody', null, vendors.map((v) => h('tr', null, h('td', null, v.vendor), h('td', null, v.current == null ? '' : fmt.money(v.current)), h('td', null, v.proposed == null ? '' : fmt.money(v.proposed))))))),
        ui.source('Reference only. These retainers are not part of the projection.')) : null));
  }

  // ---------------------------------------------------------------- QuickBooks upload (choose file, review changes, commit)
  function openUpload() {
    const dlg = h('dialog', { class: 'pg-dialog pn-dlg' });
    const errBox = h('div', { class: 'pg-dialog-err', role: 'alert' });
    const preview = h('div', { class: 'pn-up' });
    const file = h('input', { type: 'file', accept: '.xlsx,.csv,text/csv', 'aria-label': 'QuickBooks Profit and Loss by Month file' });
    let current = null;
    let confirmed = false;
    let confirmBoxEl = null;
    const commitBtn = h('button', { type: 'button', class: 'btn primary', disabled: true }, 'Commit');
    const close = () => { dlg.close(); dlg.remove(); };
    dlg.addEventListener('cancel', (e) => { e.preventDefault(); close(); });
    async function read() {
      const f = (current && current.file) || (file.files && file.files[0]);
      errBox.textContent = '';
      commitBtn.disabled = true;
      if (!f) return;
      preview.replaceChildren(h('div', { class: 'loading' }, 'Reading the file'));
      try {
        const res = await postUpload(f);
        current = { file: f, res };
        show(res);
      } catch (e) { preview.replaceChildren(); errBox.textContent = e.message; }
    }
    file.addEventListener('change', () => { current = null; read(); });
    function show(res) {
      const c = res.cells;
      const count = (n, label) => h('div', null, h('b', String(n)), h('span', label));
      const parts = [
        h('p', { class: 'pn-step-label' }, '2  Review changes'),
        h('p', { style: { margin: 0 } }, `Read ${res.file.rows} rows from ${res.file.name}. Only months that are over are loaded. Nothing is saved until you commit.`),
        h('div', { class: 'counts' }, count(c.new, 'cells added'), count(c.changed, 'cells changed'), count(c.same, 'unchanged'), count(res.unmapped.length, 'accounts not mapped')),
      ];
      if (res.unmapped.length) {
        parts.push(h('div', null, h('div', { class: 'pn-group-title' }, ui.chip('NOT MAPPED', 'warn'), ' Choose a line for each account'),
          h('table', { class: 'pn-table' }, h('thead', null, h('tr', null, h('th', 'Account'), h('th', 'Total'), h('th', 'Line'))),
            h('tbody', null, res.unmapped.map((u) => mapRow(u))))));
      }
      if (res.blockers.length) {
        parts.push(h('div', { class: 'pn-block', role: 'alert' }, ui.chip('CANNOT COMMIT', 'bad'), h('ul', null, res.blockers.map((b) => h('li', b)))));
      }
      if (res.months.length) {
        parts.push(h('div', { class: 'pn-scroll' }, h('table', { class: 'pn-table' },
          h('thead', null, h('tr', null, h('th', 'Month'), h('th', 'Status'), h('th', 'Cells'), h('th', 'Income'), h('th', 'Cost'), h('th', 'Net operating income'), h('th', 'Plan today'), h('th', 'Accounts'))),
          h('tbody', null, res.months.map((m) => h('tr', null, h('td', null, mon(m.month)),
            h('td', { class: 'text' }, ui.chip(m.status === 'new' ? 'NEW' : m.status === 'changed' ? 'CHANGED' : 'UNCHANGED', m.status === 'changed' ? 'warn' : m.status === 'new' ? 'info' : 'neutral')),
            h('td', null, `${m.cells}${m.changed ? `, ${m.changed} changed` : ''}`),
            h('td', null, fmt.compact(m.income)), h('td', null, fmt.compact(m.cost)), h('td', null, fmt.compact(m.noi)),
            h('td', null, m.model_noi == null ? '' : fmt.compact(m.model_noi)),
            h('td', { class: 'text' }, m.missing.length ? ui.chip('MISSING', 'bad') : ui.chip('COMPLETE', 'good'), m.missing.length ? h('small', `Missing: ${m.missing.join(', ')}`) : null)))))));
      }
      confirmBoxEl = null;
      if (res.needs_confirmation) {
        const box = h('input', { type: 'checkbox', 'aria-label': 'I checked the flagged months' });
        box.addEventListener('change', () => { confirmed = box.checked; commitBtn.disabled = !(res.committable && c.new + c.changed && confirmed); });
        confirmed = false;
        confirmBoxEl = h('div', { class: 'pn-review' }, ui.chip('REVIEW', 'warn'), ' These months look unusual next to their neighbours and the plan:',
          h('ul', null, res.review.map((r) => h('li', r))), h('label', { class: 'pn-check' }, box, 'I checked these months and they are right'));
        parts.push(confirmBoxEl);
      }
      if (res.changes.length) {
        parts.push(h('div', null, h('div', { class: 'pn-group-title' }, `Values that would be replaced (${res.changes.length}${res.changes_truncated ? ` of ${res.changes.length + res.changes_truncated}` : ''})`),
          h('table', { class: 'pn-table' }, h('thead', null, h('tr', null, h('th', 'Line'), h('th', 'Month'), h('th', 'Loaded'), h('th', 'File'))),
            h('tbody', null, res.changes.slice(0, 20).map((x) => h('tr', null, h('td', null, labelOf(x.line_key)), h('td', null, mon(x.month)), h('td', null, fmt.money(x.old, { digits: 2 })), h('td', null, fmt.money(x.new, { digits: 2 }))))))));
      }
      const left = [];
      for (const s of res.skipped_months) left.push(h('li', null, `${mon(s.month)}: ${s.reason}`));
      if (res.ignored.length) left.push(h('li', null, `${res.ignored.length} total and subtotal rows are calculated here, not loaded`));
      if (left.length) parts.push(h('div', null, h('div', { class: 'pn-group-title' }, ui.chip('SKIPPED', 'info'), ' Left out'), h('ul', null, left)));
      if (res.notes.length) parts.push(h('div', null, h('div', { class: 'pn-group-title' }, 'Check'), h('ul', null, res.notes.map((n) => h('li', n)))));
      parts.push(h('p', { class: 'pn-step-label' }, '3  Commit'));
      preview.replaceChildren(...parts);
      commitBtn.disabled = !res.committable || !(c.new + c.changed) || (res.needs_confirmation && !confirmed);
      commitBtn.textContent = c.new + c.changed ? `Commit ${c.new + c.changed} cell${c.new + c.changed === 1 ? '' : 's'}` : 'Nothing new to commit';
    }
    const lineLabels = {};
    function labelOf(k) { return lineLabels[k] || k; }
    function mapRow(u) {
      const sel = h('select', { class: 'pg-input', 'aria-label': `Line for ${u.account}` }, h('option', { value: '' }, 'Choose a line'));
      ensureLines().then((lines) => { lines.forEach((l) => sel.append(h('option', { value: l.key }, l.label))); });
      const btn = h('button', { type: 'button', class: 'btn small', disabled: true, onclick: async () => {
        btn.disabled = true;
        try { await ctx.api.put('pnl/qb-map', { account: u.account, line_key: sel.value }); await read(); } catch (e) { errBox.textContent = e.message; btn.disabled = false; }
      } }, 'Map');
      sel.addEventListener('change', () => { btn.disabled = !sel.value; });
      return h('tr', null, h('td', null, u.account, u.error ? h('small', u.error) : null), h('td', null, fmt.money(u.total, { digits: 2 })), h('td', { class: 'text' }, sel, ' ', btn));
    }
    let linesPromise = null;
    function ensureLines() {
      if (!linesPromise) linesPromise = ctx.api.get('pnl/qb-map').then((m) => { m.lines.forEach((l) => { lineLabels[l.key] = l.label; }); return m.lines; });
      return linesPromise;
    }
    ensureLines();
    commitBtn.addEventListener('click', async () => {
      if (!current) return;
      commitBtn.disabled = true;
      errBox.textContent = '';
      try {
        const res = await postUpload(current.file, { commit: true, token: current.res.diff_sha256, confirm: confirmed });
        toast(`Saved ${res.cells_written} cells to ${res.months.length} month${res.months.length === 1 ? '' : 's'}`, 'ok');
        close();
        ctx.refreshHeadline();
        ctx.reload();
      } catch (e) {
        errBox.textContent = e.status === 409 ? 'The loaded data changed since the preview. Choose the file again to review the changes.' : e.message;
        commitBtn.disabled = e.status === 409;
        if (e.status === 409) read();
      }
    });
    dlg.append(h('div', { class: 'pg-dialog-body' },
      h('h2', 'Upload QuickBooks export'),
      h('p', { class: 'pn-step-label' }, '1  Choose file'),
      h('label', { class: 'pn-drop' }, h('strong', 'QuickBooks Profit and Loss by Month'), h('span', { class: 'muted' }, '.xlsx or .csv, one column per month'), file),
      errBox, preview,
      h('div', { class: 'pg-dialog-actions' }, h('button', { type: 'button', class: 'btn', onclick: close }, 'Close'), commitBtn)));
    document.body.append(dlg);
    dlg.showModal();
  }

  // ---------------------------------------------------------------- assemble
  const curveHost = h('div', { class: 'pg-section' });          // retention-curve picker (cohorts_curve_picker.js)
  mountCurvePicker(curveHost, ctx).then((stop) => cleanups.push(stop)).catch(() => {});
  root.replaceChildren(...[headerEl, sourceP, curveHost, chartRow, h('div', { class: 'pg-section' }, gridCard), h('div', { class: 'pg-section' }, trendDetails),
    checksSection(), evSection(), compareSection(), driverSection(), uploadsSection(), settingsSection()].filter(Boolean));
  drawCharts();
  return () => { cleanups.forEach((fn) => { try { fn(); } catch { /* already gone */ } }); charts.destroyAll(); };
}
