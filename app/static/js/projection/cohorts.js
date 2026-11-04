// Projection page: Cohorts and curves.
// What it shows: the repeat revenue the cohorts produce month by month, the retention curve behind it (Live from the cohort export, Goal as a saved
// target) and the cohort table the curve is read from.
// Edits: curve settings (scenario levers, PUT /cohorts/levers, saved as you change them) and the monthly cohort export (POST /cohorts/upload:
// choose file, review changes, commit). Layout, copy and colors follow docs/projection/DESIGN.md; every color comes from brand.css tokens.
import { events } from './lib/api.js';
import { curveControls, leversFor, activeCurveLine } from './cohorts_curve_picker.js';
import { confirmBox } from './lib/util.js';

const STYLE_ID = 'co-style';
const CSS = `
.co-settings { display: grid; grid-template-columns: repeat(auto-fill, minmax(190px, 1fr)); gap: var(--lv-sp-3) var(--lv-sp-5); align-items: start; }
.co-field { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
.co-field > label, .co-field > .lbl { font-size: var(--lv-fs-sm); font-weight: var(--lv-fw-semi); color: var(--lv-ink-2); }
.co-field input[type="number"] { width: 100%; }
.co-field .hint { font-size: var(--lv-fs-xs); color: var(--lv-muted); }
.co-bar { display: flex; flex-wrap: wrap; align-items: center; gap: var(--lv-sp-2) var(--lv-sp-3); margin-top: var(--lv-sp-3); font-size: var(--lv-fs-sm); color: var(--lv-muted); }
.co-bar .state.err { color: var(--lv-bad); font-weight: var(--lv-fw-semi); }
.co-two { display: grid; grid-template-columns: minmax(0, 1fr) 320px; gap: var(--lv-sp-4); align-items: start; }
.co-two > * { min-width: 0; }
.co-compare-wrap { overflow-x: auto; }
.co-goal-tbl { table-layout: fixed; }
.co-goal-tbl th, .co-goal-tbl td { white-space: normal; }
.co-goal-tbl th:first-child, .co-goal-tbl td:first-child { width: 38%; padding-right: var(--lv-sp-3); overflow-wrap: anywhere; }
.co-goal-tbl th:not(:first-child), .co-goal-tbl td:not(:first-child) { width: 31%; }
.co-compare { width: 100%; border-collapse: collapse; font-size: var(--lv-fs-sm); font-variant-numeric: tabular-nums; }
.co-compare th, .co-compare td { padding: 6px 8px; text-align: right; border-bottom: 1px solid var(--lv-line-soft); white-space: nowrap; }
.co-compare thead th { white-space: normal; }
.co-compare th:first-child, .co-compare td:first-child { text-align: left; }
.co-compare thead th { font-size: var(--lv-fs-2xs); text-transform: uppercase; letter-spacing: var(--lv-track-kicker); color: var(--lv-muted); font-weight: var(--lv-fw-bold); }
.co-compare td.good { color: var(--lv-good); font-weight: var(--lv-fw-semi); }
.co-compare td.bad { color: var(--lv-neg); font-weight: var(--lv-fw-semi); }
.co-head { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: var(--lv-sp-2) var(--lv-sp-4); margin-bottom: var(--lv-sp-2); }
.co-head h2 { margin: 0; }
.co-legend { font-size: var(--lv-fs-xs); color: var(--lv-muted); display: flex; flex-wrap: wrap; gap: 4px var(--lv-sp-4); align-items: center; }
.co-legend .sw { display: inline-block; width: 12px; height: 12px; border-radius: 3px; vertical-align: -2px; margin-right: 5px; background: var(--lv-surface); border: 1px solid var(--lv-line-strong); }
.co-legend .sw.feed { box-shadow: inset 0 0 0 2px var(--lv-ink); }
.co-heat { overflow: auto; max-height: 560px; border: 1px solid var(--lv-line); border-radius: var(--lv-r-sm); background: var(--lv-surface); }
.co-heat table { border-collapse: separate; border-spacing: 0; font-size: var(--lv-fs-xs); font-variant-numeric: tabular-nums; }
.co-heat th, .co-heat td { padding: 0 6px; height: 24px; text-align: right; white-space: nowrap; border-bottom: 1px solid var(--lv-line-soft); }
.co-heat thead th { position: sticky; z-index: 2; background: var(--lv-surface-2); color: var(--lv-muted); font-weight: var(--lv-fw-bold); border-bottom: 1px solid var(--lv-line); }
.co-heat thead tr:first-child th { top: 0; }
.co-heat thead tr:nth-child(2) th { top: 24px; }
.co-heat thead th.tail { background: var(--lv-surface-3); }
.co-heat thead th.span { text-align: left; }
.co-heat tbody th { position: sticky; left: 0; z-index: 1; background: var(--lv-surface); text-align: left; font-weight: var(--lv-fw-semi); min-width: 84px; border-right: 1px solid var(--lv-line); }
.co-heat thead th.corner { left: 0; z-index: 3; text-align: left; }
.co-heat td.cust, .co-heat td.fo { color: var(--lv-ink-2); }
.co-heat td.cell { min-width: 46px; color: var(--lv-ink); }
.co-heat td.cell.feed { box-shadow: inset 0 0 0 2px var(--lv-ink); font-weight: var(--lv-fw-bold); }
.co-heat td.cell.tail { opacity: 0.85; }
.co-steps { display: flex; flex-wrap: wrap; gap: var(--lv-sp-2) var(--lv-sp-5); margin: 0 0 var(--lv-sp-3); padding: 0; list-style: none; font-size: var(--lv-fs-sm); color: var(--lv-muted); }
.co-steps li { display: flex; align-items: center; gap: var(--lv-sp-2); }
.co-steps .n { display: inline-flex; align-items: center; justify-content: center; width: 22px; height: 22px; border-radius: var(--lv-r-pill); border: 1px solid var(--lv-line-strong); font-weight: var(--lv-fw-bold); font-size: var(--lv-fs-xs); }
.co-steps li[aria-current="step"] { color: var(--lv-ink); font-weight: var(--lv-fw-heavy); }
.co-steps li[aria-current="step"] .n { background: var(--lv-inverse); color: var(--lv-on-inverse); border-color: var(--lv-inverse); }
.co-drop { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 4px; padding: var(--lv-sp-6) var(--lv-sp-4); border: 2px dashed var(--lv-line-strong); border-radius: var(--lv-r-md); background: var(--lv-surface); color: var(--lv-ink-2); text-align: center; cursor: pointer; position: relative; }
.co-drop:hover, .co-drop.over { background: var(--lv-hover); border-color: var(--lv-ink-2); }
.co-drop:focus-within { outline: 2px solid var(--lv-focus); outline-offset: 2px; }
.co-drop input { position: absolute; inset: 0; opacity: 0; cursor: pointer; width: 100%; height: 100%; }
.co-drop strong { font-size: var(--lv-fs-md); }
.co-drop span { font-size: var(--lv-fs-sm); color: var(--lv-muted); }
.co-review { display: flex; flex-direction: column; gap: var(--lv-sp-3); }
.co-chips { display: flex; flex-wrap: wrap; gap: var(--lv-sp-2); }
.co-review table { width: 100%; border-collapse: collapse; font-size: var(--lv-fs-sm); font-variant-numeric: tabular-nums; }
.co-review th, .co-review td { text-align: right; padding: 5px 8px; border-bottom: 1px solid var(--lv-line-soft); }
.co-review th:first-child, .co-review td:first-child { text-align: left; }
.co-review thead th { font-size: var(--lv-fs-2xs); text-transform: uppercase; letter-spacing: var(--lv-track-kicker); color: var(--lv-muted); }
.co-review td.new { background: var(--lv-hl-bg); font-weight: var(--lv-fw-semi); }
.co-review ul { margin: 0; padding: 0; list-style: none; display: flex; flex-direction: column; gap: 4px; font-size: var(--lv-fs-sm); color: var(--lv-ink-2); }
.co-review li { display: flex; align-items: center; flex-wrap: wrap; gap: var(--lv-sp-2); }
.co-actions { display: flex; flex-wrap: wrap; align-items: center; gap: var(--lv-sp-2); }
.co-err { color: var(--lv-bad); font-weight: var(--lv-fw-semi); font-size: var(--lv-fs-sm); min-height: 1em; }
@media (max-width: 960px) { .co-two { grid-template-columns: 1fr; } }
`;

function ensureStyle() {
  if (document.getElementById(STYLE_ID)) return;
  const s = document.createElement('style');
  s.id = STYLE_ID;
  s.textContent = CSS;
  document.head.append(s);
}

const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const mon = (iso) => { const [y, m] = String(iso).split('-'); return `${MON[Number(m) - 1]} ${y}`; };
const plural = (n, one, many = one + 's') => `${n} ${n === 1 ? one : many}`;
const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

// the shared fetch layer is JSON only; the cohort export goes up as multipart
async function postUpload(file, { commit = false, sha = '' } = {}) {
  const fd = new FormData();
  fd.append('file', file, file.name);
  if (commit) fd.append('expect_sha256', sha);
  const res = await fetch('/api/projection/cohorts/upload' + (commit ? '?commit=1' : ''), { method: 'POST', body: fd, credentials: 'same-origin' });
  if (res.status === 401) { location.assign('/login?next=' + encodeURIComponent(location.pathname + location.search)); throw new Error('Sign in required'); }
  let data = null;
  try { data = await res.json(); } catch { /* not JSON */ }
  if (!res.ok) {
    const msg = data && data.error;
    throw new Error(res.status === 429 ? 'Too many uploads in a short time. Wait a bit and try again.' : (msg || `The server answered ${res.status}.`));
  }
  return data;
}

export async function render(el, ctx) {
  ensureStyle();
  const { h, fmt, ui, charts, toast } = ctx;
  const slug = ctx.scenario;
  const mine = [];                                   // charts this page created itself
  const cleanups = [];

  const root = h('div', { class: 'pg-page' });
  el.replaceChildren(root);
  ui.loading(root, 'Loading cohorts');

  let summary, curves, table, heat, lev, copts;
  try {
    [summary, curves, table, heat, lev, copts] = await Promise.all([
      ctx.api.get('cohorts/summary'), ctx.api.get('cohorts/curves'), ctx.api.table('cohorts'), ctx.api.get('cohorts/cohorts'), ctx.api.get('cohorts/levers'),
      ctx.api.get('cohorts/curve-options'),
    ]);
  } catch (e) { ui.error(root, e, ctx.reload); return undefined; }
  if (!ctx.alive()) return undefined;

  const x = (v, d = 2) => (Number.isFinite(v) ? fmt.x(v, d) : '');
  const signed = (v) => (Number.isFinite(v) ? `${v >= 0 ? '+' : '-'}${Math.abs(v * 100).toFixed(1)}%` : '');

  // ---------------------------------------------------------------- header and TLDR
  const cs = curves.summary;
  const fresh = summary.freshness || {};
  const freshTone = !fresh.data_through || fresh.stale ? 'bad' : fresh.behind ? 'warn' : 'good';
  const usedVsGoal = cs.lifetime_goal ? summary.lifetime_multiple / cs.lifetime_goal - 1 : null;
  const vsGoal = usedVsGoal != null && cs.curve !== 'goal' ? `, ${Math.abs(usedVsGoal * 100).toFixed(0)}% ${usedVsGoal < 0 ? 'below' : 'above'} Goal` : '';
  const line = summary.cohorts_loaded
    ? `Each first-order dollar brings back ${fmt.money(summary.lifetime_multiple, { digits: 2 })} of repeat revenue over five years${vsGoal}.`
    : 'No cohorts are loaded yet. Upload a cohort export to build the retention curve.';
  const bt = summary.backtest || {};
  const gapTone = bt.gap == null ? undefined : Math.abs(bt.gap) <= 0.05 ? 'good' : Math.abs(bt.gap) <= 0.1 ? 'warn' : 'bad';
  const kpis = [
    { label: 'Repeat per first-order dollar, 60 months', value: x(summary.lifetime_multiple), note: cs.lifetime_goal ? `Goal ${x(cs.lifetime_goal)}` : '' },
    { label: 'First 12 months', value: x(summary.twelve_month_multiple), note: cs.twelve_goal ? `Goal ${x(cs.twelve_goal)}` : '' },
    { label: 'Repeat revenue, next 12 months', value: fmt.compact(summary.forward_12m_repeat) + '*', note: summary.forward_12m_from ? `${mon(summary.forward_12m_from)} to ${mon(summary.forward_12m_to)}` : '' },
    { label: 'Model against actual, last 3 months', value: signed(bt.gap), tone: gapTone, note: bt.months && bt.months.length ? `${mon(bt.months[0])} to ${mon(bt.months[bt.months.length - 1])}` : '' },
    { label: 'Cohorts loaded', value: String(summary.cohorts_loaded), note: summary.first_cohort ? `${mon(summary.first_cohort)} to ${mon(summary.last_cohort)}` : '' },
  ].filter((k) => k.value !== '');

  const uploadBtn = h('button', { type: 'button', class: 'btn primary', 'aria-expanded': 'false', onclick: () => toggleUpload() }, 'Upload cohort export');
  const last = summary.last_upload;
  const undoBtn = last && last.undoable ? h('button', { type: 'button', class: 'btn', title: `${last.filename || 'The last upload'}: ${last.cohorts_added} new and ${last.cohorts_changed} changed cohorts, loaded ${fmt.dateTime(last.at)}`,
    onclick: () => undoUpload(last) }, 'Undo last upload') : null;
  const header = ui.pageHeader({
    title: 'Cohorts and curves',
    purpose: 'How much each month of new customers keeps spending, and the retention curve that turns first orders into repeat revenue.',
    actions: [undoBtn, uploadBtn].filter(Boolean),
    tldr: { line, kpis },
  });
  const tldrLine = header.querySelector('.pg-tldr-line');
  if (tldrLine) tldrLine.append(h('div', { style: { marginTop: 'var(--lv-sp-2)' } }, ui.chip(summary.freshness_line, freshTone)));
  activeCurveLine(ctx, summary.curve_label, header);

  // ---------------------------------------------------------------- upload: choose file, review changes, commit
  const uploadCard = h('section', { class: 'pg-card', hidden: true, 'aria-label': 'Upload cohort export' });
  const stepEls = ['Choose file', 'Review changes', 'Commit'].map((t, i) => h('li', null, h('span', { class: 'n' }, String(i + 1)), t));
  const setStep = (n) => stepEls.forEach((li, i) => (i === n ? li.setAttribute('aria-current', 'step') : li.removeAttribute('aria-current')));
  const upErr = h('div', { class: 'co-err', role: 'alert' });
  const pane = h('div');
  let pending = null;                                // {file, res} of the previewed upload

  function showChoose() {
    pending = null;
    setStep(0);
    const input = h('input', { type: 'file', accept: '.csv,.tsv,.txt,text/csv', 'aria-label': 'Cohort export file' });
    const drop = h('label', { class: 'co-drop' }, input,
      h('strong', 'Drop the cohort export here or choose a file'),
      h('span', 'CSV in the sheet layout (Cohort Month, New Customers, First Order, then months 1 to 36), or a cohort export with months since first purchase.'));
    const take = (f) => { if (f) previewFile(f); };
    input.addEventListener('change', () => take(input.files && input.files[0]));
    drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.classList.add('over'); });
    drop.addEventListener('dragleave', () => drop.classList.remove('over'));
    drop.addEventListener('drop', (e) => { e.preventDefault(); drop.classList.remove('over'); take(e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0]); });
    pane.replaceChildren(drop);
    return input;
  }

  async function previewFile(file) {
    upErr.textContent = '';
    pane.replaceChildren(h('div', { class: 'loading' }, 'Reading the file'));
    try {
      const res = await postUpload(file);
      pending = { file, res };
      showReview(res);
    } catch (e) { showChoose(); upErr.textContent = e.message; }
  }

  const fieldName = (f) => ({ new_customers: 'New customers', first_order: 'First order', first_order_realized: 'First order (realized)', repeat_90d: '90-day repeat rate',
    cac: 'Cost to acquire a customer', payback_months: 'Payback months' }[f] || `Month ${String(f).replace(/^M/, '')}`);

  function showReview(res) {
    setStep(1);
    const s = res.summary;
    const affected = s.cohorts_added + s.cohorts_changed;
    const blocked = res.committable === false && (res.errors || []).length > 0;
    const nothing = affected === 0;
    const parts = [
      h('p', { style: { margin: 0, color: 'var(--lv-muted)' } }, `${res.filename}: ${plural(res.rows_read, 'row')} read as the ${res.layout === 'wide' ? 'sheet layout' : 'cohort export layout'}. Only months that are over are used, through ${mon(res.through)}.`),
      h('div', { class: 'co-chips' },
        ui.chip(`Added: ${plural(s.cohorts_added, 'cohort')}, ${plural(s.cells_added, 'cell')}`, s.cohorts_added || s.cells_added ? 'good' : 'neutral'),
        ui.chip(`Changed: ${plural(s.cohorts_changed, 'cohort')}, ${plural(s.cells_changed, 'cell')}`, s.cohorts_changed ? 'info' : 'neutral'),
        ui.chip(`Blanked: ${plural(s.cells_blanked_open_month, 'cell')} in months not over`, 'neutral')),
    ];
    if (blocked) {
      parts.push(h('div', null, ui.chip('Cannot be loaded', 'bad'), h('ul', { style: { marginTop: 'var(--lv-sp-2)' } }, res.errors.map((e) => h('li', e)))));
    }
    const imp = res.impact || {};
    const mult = (label, m) => (m && Number.isFinite(m.after)
      ? h('li', null, ui.chip(m.after < m.min || m.after > m.max ? 'Outside the allowed range' : 'Within the allowed range', m.after < m.min || m.after > m.max ? 'bad' : 'good'),
        `${label}: ${Number.isFinite(m.before) ? fmt.x(m.before, 2) + ' now, ' : ''}${fmt.x(m.after, 2)} after this file (allowed ${m.min}x to ${m.max}x)`) : null);
    const impactRows = [mult('Repeat per first-order dollar, 60 months', imp.lifetime), mult('First 12 months', imp.twelve)].filter(Boolean);
    if (impactRows.length) parts.push(h('ul', null, impactRows));
    if (s.changes.length) {
      parts.push(h('div', null, h('div', { class: 'pg-group-head', style: { margin: '0 0 4px' } }, `Largest changes (${Math.min(12, s.changes.length)} of ${s.changes_total})`),
        h('table', null,
          h('thead', null, h('tr', null, h('th', 'Cohort'), h('th', 'Field'), h('th', 'Stored'), h('th', 'New'), h('th', 'Change'))),
          h('tbody', null, s.changes.slice(0, 12).map((c) => h('tr', null, h('td', mon(c.month)), h('td', fieldName(c.field)), h('td', fmt.num(c.old, 2)),
            h('td', { class: 'new' }, fmt.num(c.new, 2)), h('td', c.rel == null ? 'added' : signed(c.rel))))))));
    }
    const notOver = (k) => k.reason === 'month not over';
    const left = [
      ...s.skipped.slice(0, 8).map((k) => h('li', null, ui.chip(notOver(k) ? 'In progress, skipped' : 'Skipped', notOver(k) ? 'info' : 'neutral'), `${mon(k.month)}${notOver(k) ? '' : ': ' + k.reason}`)),
      s.skipped.length > 8 ? h('li', `and ${s.skipped.length - 8} more months skipped`) : null,
      ...(s.held_back || []).map((k) => h('li', null, ui.chip('Held back', 'neutral'),
        `${mon(k.month)}: ${k.fields.map((f) => (f === 'cac' ? 'cost to acquire a customer' : '90-day repeat rate')).join(' and ')} wait until the month matures`)),
    ].filter(Boolean);
    if (left.length) parts.push(h('ul', null, left));
    if (res.warnings.length) parts.push(h('div', null, ui.chip('Check these', 'warn'), h('ul', { style: { marginTop: 'var(--lv-sp-2)' } }, res.warnings.map((w) => h('li', w)))));
    parts.push(h('p', { style: { margin: 0, color: 'var(--lv-muted)', fontSize: 'var(--lv-fs-sm)' } },
      blocked ? 'Fix the file and choose it again. Nothing was saved.' : nothing ? 'This file adds nothing new to the cohort table.' : 'Stored values are added or revised, never erased. You can undo this upload afterwards.'));
    const commit = h('button', { type: 'button', class: 'btn primary', disabled: nothing || blocked, onclick: () => commitUpload(commit) },
      blocked ? 'Cannot be loaded' : nothing ? 'Nothing to commit' : `Commit ${plural(affected, 'cohort')}`);
    parts.push(h('div', { class: 'co-actions' }, commit, h('button', { type: 'button', class: 'btn', onclick: () => closeUpload() }, 'Cancel'),
      h('button', { type: 'button', class: 'btn', onclick: () => { upErr.textContent = ''; showChoose().focus(); } }, 'Choose another file')));
    pane.replaceChildren(h('div', { class: 'co-review' }, parts));
  }

  async function commitUpload(btn) {
    if (!pending) return;
    const label = btn.textContent;
    btn.disabled = true;
    setStep(2);
    upErr.textContent = '';
    btn.textContent = 'Committing';
    try {
      const res = await postUpload(pending.file, { commit: true, sha: pending.res.sha256 });
      toast(`Saved ${plural(res.loaded.cohorts, 'cohort')} to ${mon(res.through)}. Undo last upload puts them back.`, 'ok');
      ctx.refreshHeadline();
      ctx.reload();
    } catch (e) { upErr.textContent = e.message; setStep(1); btn.disabled = false; btn.textContent = label; }
  }
  function closeUpload() { uploadCard.hidden = true; uploadBtn.setAttribute('aria-expanded', 'false'); upErr.textContent = ''; uploadBtn.focus(); }
  function toggleUpload() {
    if (!uploadCard.hidden) { closeUpload(); return; }
    uploadCard.hidden = false;
    uploadBtn.setAttribute('aria-expanded', 'true');
    const input = showChoose();
    uploadCard.scrollIntoView({ block: 'nearest' });
    input.focus();
  }
  uploadCard.append(h('div', { class: 'co-head' }, h('h2', 'Upload cohort export'), h('button', { type: 'button', class: 'btn small', onclick: () => closeUpload() }, 'Close')),
    h('ol', { class: 'co-steps', 'aria-label': 'Steps' }, stepEls), pane, upErr);
  setStep(0);

  // ---------------------------------------------------------------- repeat revenue: chart and grid
  const revCanvas = h('canvas', { role: 'img', 'aria-label': 'Repeat revenue by month, stacked by the year the customers were acquired' });
  const gridHost = h('div');
  const revenue = h('section', { class: 'pg-section' },
    h('div', { class: 'co-head' }, h('h2', 'Repeat revenue by month'), h('span', { class: 'co-legend' }, 'Months marked * are projected')),
    h('div', { class: 'pg-card' }, h('div', { class: 'pg-chartbox' }, revCanvas)),
    h('div', { style: { marginTop: 'var(--lv-sp-3)' } }, gridHost));

  function drawRevenue() {
    const rows = table.rows.filter((r) => /^repeat_20\d\d$/.test(r.key));
    const actual = table.rows.find((r) => r.key === 'repeat_actual');
    const shades = charts.ramp(rows.length, 2);                      // every vintage keeps 3:1 or better against the card in light and dark (design vet, 2026-11-03)
    const series = rows.map((r, i) => ({ label: r.label, values: r.values, type: 'bar', color: shades[i] }));
    const compare = actual ? [{ name: 'Actual repeat revenue', values: actual.values, color: cssVar('--lv-viz-ink') }] : [];
    charts.barChart(revCanvas, { months: table.months, status: table.status, series, stacked: true, yFmt: 'usd0', compare });
  }
  const grid = ctx.grid(gridHost, { payload: table, title: null, readOnly: true, groupCollapse: false, fy: false });
  cleanups.push(() => grid.destroy());

  // ---------------------------------------------------------------- curve settings (saved as you change them)
  const own = lev.own || {};
  const cur = { ...lev.levers };
  const state = h('span', { class: 'state', role: 'status' }, Object.keys(own).length ? 'Custom settings in this scenario.' : 'Defaults.');
  const LEVER_NAMES = { n_cohorts: 'Cohorts averaged at each month', min_cohorts_per_age: 'Fewest cohorts to trust a month', tail_from_age: 'Decay starts at month',
    tail_decay: 'Kept each month after that', max_age: 'Repeat stops after month', curve: 'Curve used for projections' };
  const plain = (text) => String(text).replace(/\b(n_cohorts|min_cohorts_per_age|tail_from_age|tail_decay|max_age|curve)\b/g, (k) => LEVER_NAMES[k]);
  const setState = (text, err = false) => { state.className = 'state' + (err ? ' err' : ''); state.textContent = plain(text); };
  async function putLevers(levers) {
    setState('Saving');
    try {
      const res = await ctx.api.put('cohorts/levers', { scenario: slug, levers });
      events.emit('saved', { scenario: res.scenario || slug, version: res.version });
      toast('Curve setting saved', 'ok');
      ctx.reload();
    } catch (e) { setState(e.message, !(e && e.name === 'EditCancelled')); throw e; }
  }
  const numField = (key, label, hint, { min, max, step = 1, scale = 1 }) => {
    const shown = Math.round(cur[key] * scale * 1000) / 1000;
    const input = h('input', { type: 'number', id: 'co-' + key, min, max, step, value: String(shown), inputmode: 'decimal' });
    input.addEventListener('change', () => {
      const v = parseFloat(input.value);
      if (!Number.isFinite(v)) { input.value = String(shown); setState('Enter a number.', true); return; }
      putLevers({ [key]: Math.round((v / scale) * 1e6) / 1e6 }).catch(() => { input.value = String(shown); });
    });
    return h('div', { class: 'co-field' }, h('label', { for: 'co-' + key }, label), input, hint ? h('span', { class: 'hint' }, hint) : null);
  };
  const curveCtl = curveControls(ctx, {
    options: copts, showN: false,
    state: { source: cur.curve_source, asof: cur.asof_month || copts.default_asof, n: cur.n_cohorts },
    onChange: (st) => { putLevers(leversFor(st)).catch(() => {}); },
  });
  const resetBtn = h('button', { type: 'button', class: 'btn small', disabled: Object.keys(own).length === 0,
    onclick: () => putLevers(Object.fromEntries(Object.keys(own).map((k) => [k, null]))).catch(() => {}) }, 'Reset to defaults');
  const settings = h('div', { class: 'co-settings' },
    h('div', { class: 'co-field', style: { gridColumn: '1 / -1' } }, h('span', { class: 'lbl' }, 'Curve used for repeat revenue'), curveCtl.el),
    numField('n_cohorts', 'Cohorts averaged at each month', 'Most recent cohorts that reached that month', { min: 1, max: 36 }),
    numField('min_cohorts_per_age', 'Fewest cohorts to trust a month', 'Fewer than this falls back to decay', { min: 1, max: 36 }),
    numField('tail_from_age', 'Decay starts at month', 'Later months decay from the one before', { min: 2, max: 60 }),
    numField('tail_decay', 'Kept each month after that (%)', 'Share of the previous month kept', { min: 50, max: 100, step: 0.5, scale: 100 }),
    numField('max_age', 'Repeat stops after month', 'No repeat revenue beyond this age', { min: 12, max: 60 }));

  // ---------------------------------------------------------------- retention curve
  let cumulative = false;
  const curveCanvas = h('canvas', { role: 'img', 'aria-label': 'Repeat revenue per first-order dollar by month after the first order, Live and Goal curves' });
  const perBtn = h('button', { type: 'button', class: 'seg', 'aria-pressed': 'true', onclick: () => setCum(false) }, 'Per month');
  const cumBtn = h('button', { type: 'button', class: 'seg', 'aria-pressed': 'false', onclick: () => setCum(true) }, 'Cumulative');
  const gl = summary.goal || {};
  const goalBtn = h('button', { type: 'button', class: 'btn', title: 'Replace the Goal curve with the current curve', onclick: () => saveGoalFlow() }, 'Save current as Goal');
  const restoreBtn = h('button', { type: 'button', class: 'btn', disabled: !gl.previous,
    title: gl.previous ? `Put back the Goal saved ${fmt.date(gl.previous.snapshot_date)} (${x(gl.previous.lifetime)})` : 'There is no earlier Goal curve', onclick: () => restoreGoalFlow() }, 'Restore previous Goal');
  const cmpTable = (() => {
    const row = (label, live, goal, gap) => h('tr', null, h('td', label), h('td', x(live)), h('td', goal ? x(goal) : ''), h('td', { class: gap == null ? '' : gap < 0 ? 'bad' : 'good' }, signed(gap)));
    return h('table', { class: 'co-compare' },
      h('thead', null, h('tr', null, h('th', 'Repeat per $1'), h('th', 'Current'), h('th', 'Goal'), h('th', 'Current vs Goal'))),
      h('tbody', null, row('First 12 months', cs.twelve_live, cs.twelve_goal, cs.twelve_live_vs_goal), row('60 months', cs.lifetime_live, cs.lifetime_goal, cs.live_vs_goal)),
      cs.curve !== 'live' ? h('tfoot', null, h('tr', null, h('td', { colspan: 4, style: { color: 'var(--lv-muted)' } }, `Repeat revenue uses: ${cs.label}, ${x(cs.lifetime_used)} over 60 months.`))) : null);
  })();
  const curveSection = h('section', { class: 'pg-section' },
    h('div', { class: 'co-head' }, h('h2', 'Retention curve'), h('div', { class: 'segs', role: 'group', 'aria-label': 'Curve view' }, perBtn, cumBtn)),
    h('div', { class: 'pg-card' }, settings, h('div', { class: 'co-bar' }, state, resetBtn)),
    h('div', { class: 'co-two', style: { marginTop: 'var(--lv-sp-3)' } },
      h('div', { class: 'pg-card' }, h('div', { class: 'pg-chartbox' }, curveCanvas)),
      h('div', { class: 'pg-card' }, h('div', { class: 'pg-group-head', style: { margin: '0 0 6px' } }, 'Current against Goal'), h('div', { class: 'co-compare-wrap' }, cmpTable),
        h('div', { class: 'co-actions', style: { marginTop: 'var(--lv-sp-3)' } }, goalBtn, restoreBtn),
        (curves.warnings || []).map((w) => h('div', { style: { marginTop: 'var(--lv-sp-3)' } }, ui.chip(w, 'warn'))))),
    ui.source(`${curves.caption} Decay starts at month ${cur.tail_from_age}, keeping ${(cur.tail_decay * 100).toFixed(0)}% each month.`));

  function drawCurve() {
    for (const c of mine) c.destroy();
    mine.length = 0;
    if (typeof Chart === 'undefined') return;
    const slot = (n) => cssVar(`--lv-viz-${n}`);
    const ink = cssVar('--lv-viz-ink');
    const muted = cssVar('--lv-viz-axis');
    const gridColor = cssVar('--lv-viz-grid');
    Chart.defaults.font.family = cssVar('--lv-font');
    Chart.defaults.font.size = 12;
    Chart.defaults.color = muted;
    const live = cumulative ? curves.cumulative.live : curves.live;
    const goal = cumulative ? curves.cumulative.goal : curves.goal;
    const used = cumulative ? curves.cumulative.used : curves.used;
    const active = cs.curve;
    const sets = [
      { label: active === 'live' ? 'Current (used)' : 'Current', data: live, borderColor: ink, backgroundColor: ink, borderWidth: active === 'live' ? 2.5 : 1.5, pointRadius: 0, tension: 0.15 },
      { label: active === 'goal' ? 'Goal (used)' : 'Goal', data: goal, borderColor: slot(2), backgroundColor: slot(2), borderWidth: active === 'goal' ? 2.5 : 1.5, pointRadius: 0, tension: 0.15 },
    ];
    if (active !== 'live' && active !== 'goal') sets.push({ label: `${cs.label} (used)`, data: used, borderColor: slot(6), backgroundColor: slot(6), borderWidth: 2.5, pointRadius: 0, tension: 0.15, borderDash: [6, 4] });
    const tailShade = {
      id: 'tailShade',
      beforeDatasetsDraw(chart) {
        const { ctx: c, chartArea, scales } = chart;
        if (!chartArea) return;
        const step = scales.x.getPixelForValue(1) - scales.x.getPixelForValue(0);
        const x0 = Math.max(chartArea.left, scales.x.getPixelForValue(cur.tail_from_age - 1) - step / 2);
        c.save();
        c.fillStyle = cssVar('--lv-surface-2');
        c.fillRect(x0, chartArea.top, chartArea.right - x0, chartArea.bottom - chartArea.top);
        c.fillStyle = muted;
        c.font = `600 11px ${cssVar('--lv-font')}`;
        c.fillText('Decay', x0 + 6, chartArea.top + 14);
        c.restore();
      },
    };
    mine.push(new Chart(curveCanvas, {
      type: 'line', data: { labels: curves.ages, datasets: sets }, plugins: [tailShade],
      options: {
        responsive: true, maintainAspectRatio: false, animation: false, interaction: { mode: 'index', intersect: false },
        scales: {
          x: { grid: { display: false }, title: { display: true, text: 'Months after the first order', color: muted }, ticks: { color: muted, maxRotation: 0, autoSkip: true, maxTicksLimit: 15 } },
          y: { beginAtZero: true, grid: { color: gridColor }, title: { display: true, text: cumulative ? 'Cumulative repeat per first-order dollar' : 'Repeat per first-order dollar', color: muted },
               ticks: { color: muted, callback: (v) => (cumulative ? fmt.x(v, 1) : fmt.pct(v, 0)) } },
        },
        plugins: {
          legend: { position: 'top', align: 'end', labels: { boxWidth: 10, boxHeight: 10, color: muted } },
          tooltip: { callbacks: { title: (items) => `Month ${items[0].label} after the first order`, label: (c) => `${c.dataset.label}: ${cumulative ? fmt.x(c.parsed.y, 2) : fmt.pct(c.parsed.y, 1)}` } },
        },
      },
    }));
  }
  function setCum(v) {
    cumulative = v;
    perBtn.setAttribute('aria-pressed', String(!v));
    cumBtn.setAttribute('aria-pressed', String(v));
    drawCurve();
  }

  // ---------------------------------------------------------------- cohort table (heatmap)
  const heatHost = h('div', { class: 'co-heat', tabindex: 0, role: 'region', 'aria-label': 'Cumulative dollars per customer by cohort and month after the first order' });
  const LAYER = 'linear-gradient(var(--lv-proj-band), var(--lv-proj-band))';          // each stacked layer adds one step of tint
  function drawHeat() {
    const tailAge = heat.tail_from_age || 28;
    const cohorts = [...heat.cohorts].reverse();
    const ages = Array.from({ length: heat.ages }, (_, i) => i + 1);
    const thead = h('thead', null,
      h('tr', null, h('th', { class: 'corner', rowspan: 2 }, 'Cohort'), h('th', { rowspan: 2 }, 'Customers'), h('th', { rowspan: 2 }, 'First order'),
        h('th', { class: 'span', colspan: ages.length }, 'Months after the first order')),
      h('tr', null, ages.map((a) => h('th', { class: a >= tailAge ? 'tail' : '', title: a >= tailAge ? `Month ${a}: the curve decays from here` : `Month ${a}` }, String(a)))));
    const rows = cohorts.map((c) => {
      const feed = new Set(c.feeds_curve);
      return h('tr', null,
        h('th', { scope: 'row' }, mon(c.month)),
        h('td', { class: 'cust' }, fmt.num(c.new_customers, 0)),
        h('td', { class: 'fo' }, c.first_order == null ? '' : fmt.money(c.first_order, { digits: 2 })),
        ages.map((a) => {
          const v = c.cum[a - 1];
          if (v == null) return h('td', { class: 'cell' });
          const repeat = c.first_order ? Math.max(0, v / c.first_order - 1) : 0;
          const layers = Math.min(8, Math.round((repeat / 2) * 8));
          const inc = c.ratio[a - 1];
          return h('td', {
            class: 'cell' + (feed.has(a) ? ' feed' : '') + (a >= tailAge ? ' tail' : ''),
            style: layers ? { backgroundImage: Array(layers).fill(LAYER).join(',') } : null,
            title: `${mon(c.month)}, month ${a}: ${fmt.money(v, { digits: 2 })} per customer in total` + (inc != null ? `; month ${a} alone is ${(inc * 100).toFixed(1)}% of the first order` : '') + (feed.has(a) ? '; feeds the current curve' : ''),
          }, fmt.money(v, { digits: 0 }));
        }));
    });
    heatHost.replaceChildren(h('table', null, thead, h('tbody', null, rows)));
  }
  const heatSection = h('section', { class: 'pg-section' },
    h('div', { class: 'co-head' }, h('h2', 'Cohort table'),
      h('span', { class: 'co-legend' },
        h('span', null, h('span', { class: 'sw feed' }), `Outlined cells feed the current curve (latest ${heat.n_cohorts} cohorts at each month)`),
        h('span', null, 'Darker means more repeat spend'))),
    heatHost, ui.source(heat.caption));

  // ---------------------------------------------------------------- undo
  async function undoUpload(u) {
    const when = fmt.dateTime(u.at);
    await confirmBox({
      title: 'Undo the last upload',
      message: `${u.filename || 'The last upload'} (${when}) added ${plural(u.cohorts_added, 'cohort')} and changed ${plural(u.cohorts_changed, 'cohort')}. Undoing puts every one of them back exactly as it was before. Nothing else changes.`,
      confirmText: 'Undo upload', danger: true,
      onConfirm: async () => {
        const res = await ctx.api.post('cohorts/upload/undo', { upload_id: u.id });
        toast(`Undid the upload: ${plural(res.cohorts_restored, 'cohort')} restored, ${plural(res.cohorts_removed, 'cohort')} removed`, 'ok');
        ctx.refreshHeadline();
        ctx.reload();
      },
    });
  }

  // ---------------------------------------------------------------- Goal curve: save, restore
  const FY = (o) => `FY${String(o.fy.year).slice(2)}`;
  const moneyM = (v) => (Number.isFinite(v) ? `${v < 0 ? '-' : ''}$${(Math.abs(v) >= 1e6 ? (Math.abs(v) / 1e6).toFixed(2) + 'M' : Math.round(Math.abs(v) / 1e3) + 'K')}` : '');
  async function restoreGoal(snapshotId) {
    const res = await ctx.api.post('cohorts/goal/restore', snapshotId ? { snapshot_id: snapshotId } : {});
    toast(`Goal curve restored to the one saved ${fmt.date(res.snapshot_date)} (${x(res.lifetime)})`, 'ok');
    ctx.refreshHeadline();
    ctx.reload();
    return res;
  }
  async function saveGoalFlow() {
    goalBtn.disabled = true;
    let cmpData = null;
    try { cmpData = await ctx.api.get('cohorts/curve-compare', { n: cur.n_cohorts }); } catch { /* no as-of month yet: the comparison still has Current and Goal below */ }
    goalBtn.disabled = false;
    const now = cmpData ? cmpData.options.find((o) => o.key === 'current') : null;
    const old = cmpData ? cmpData.options.find((o) => o.key === 'goal') : null;
    const oldGoal = gl.active;
    const rows = [['Goal now' + (oldGoal ? `, saved ${fmt.date(oldGoal.snapshot_date)}` : ''), oldGoal ? oldGoal.lifetime : null, old ? old.fy.noi : null],
                  ['Current, becomes the Goal', summary.lifetime_live, now ? now.fy.noi : null]];
    const delta = (a, b) => (Number.isFinite(a) && Number.isFinite(b) ? b - a : null);
    const dLife = delta(rows[0][1], rows[1][1]);
    const dNoi = delta(rows[0][2], rows[1][2]);
    const yr = now ? FY(now) : 'FY27';
    const sign = (v, f) => (v == null ? '' : `${v > 0 ? '+' : v < 0 ? '-' : ''}${f(Math.abs(v))}`);
    const tbl = h('table', { class: 'co-compare co-goal-tbl', style: { margin: 'var(--lv-sp-2) 0' } },
      h('thead', null, h('tr', null, h('th', ''), h('th', 'Repeat per first-order dollar'), h('th', `${yr} net operating income`))),
      h('tbody', null,
        rows.map((r) => h('tr', null, h('td', r[0]), h('td', Number.isFinite(r[1]) ? x(r[1]) : 'none'), h('td', Number.isFinite(r[2]) ? moneyM(r[2]) + '*' : ''))),
        oldGoal ? h('tr', null, h('td', 'Change'), h('td', { class: dLife < 0 ? 'bad' : 'good' }, sign(dLife, (v) => v.toFixed(2) + 'x')),
          h('td', { class: dNoi == null ? '' : dNoi < 0 ? 'bad' : 'good' }, dNoi == null ? '' : sign(dNoi, moneyM) + '*')) : null));
    const message = h('div', null,
      h('p', { style: { margin: 0 } }, oldGoal ? 'The Goal curve is replaced by the current curve. Everything that uses the Goal curve moves with it.' : 'There is no Goal curve yet. This saves the current curve as the Goal curve.'),
      tbl,
      h('p', { style: { margin: 0 } }, oldGoal ? 'The old Goal stays in history: Restore previous Goal puts it back.' : ''));
    await confirmBox({
      title: 'Replace the Goal curve?', message, confirmText: oldGoal ? 'Replace Goal' : 'Save as Goal', danger: false,
      onConfirm: async () => {
        const res = await ctx.api.post('cohorts/goal', { scenario: slug, from: 'live' });
        toast(`Goal curve replaced by the current curve (${x(res.lifetime)})`, { kind: 'ok', action: res.replaced ? { label: 'Undo', onClick: () => restoreGoal().catch((e) => toast(e.message, 'error')) } : null });
        ctx.refreshHeadline();
        ctx.reload();
      },
    });
  }
  async function restoreGoalFlow() {
    const prev = gl.previous;
    if (!prev) return;
    await confirmBox({
      title: 'Restore the previous Goal curve?',
      message: `The Goal saved ${fmt.date(prev.snapshot_date)} (${x(prev.lifetime)} over 60 months) goes back in use. The Goal in use now (${x(gl.active.lifetime)}, saved ${fmt.date(gl.active.snapshot_date)}) stays in history.`,
      confirmText: 'Restore Goal', danger: false, onConfirm: () => restoreGoal(prev.id),
    });
  }

  root.replaceChildren(header, uploadCard, revenue, curveSection, heatSection);
  drawRevenue();
  drawCurve();
  drawHeat();
  return () => {
    for (const c of mine) c.destroy();
    for (const fn of cleanups) fn();
  };
}
