// Retention-curve picker: which curve drives repeat revenue in this scenario, and what each choice does to the P&L.
// Options: Current (the latest cohorts), Goal (the saved target curve) and As of month (the current method applied to the cohort table as it stood at
// the end of an earlier month, with 3 or 6 cohorts averaged). The choice is saved in the scenario through PUT /cohorts/levers (audited, versioned,
// restorable from History). Used by the P&L page (mountCurvePicker) and the Cohorts page (curveControls, activeCurveLine).
// Colors come from brand.css tokens only (docs/projection/DESIGN.md).
import { events } from './lib/api.js';

const STYLE_ID = 'cp-style';
const CSS = `
.cp-controls { display: flex; flex-wrap: wrap; align-items: flex-end; gap: var(--lv-sp-3) var(--lv-sp-5); }
.cp-field { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
.cp-field > .lbl { font-size: var(--lv-fs-sm); font-weight: var(--lv-fw-semi); color: var(--lv-ink-2); }
.cp-field select { min-width: 150px; }
.cp-note { font-size: var(--lv-fs-xs); color: var(--lv-muted); }
.cp-body { display: grid; grid-template-columns: minmax(0, 5fr) minmax(0, 7fr); gap: var(--lv-sp-4); margin-top: var(--lv-sp-3); align-items: start; }
.cp-scroll { overflow-x: auto; }
.cp-table { width: 100%; min-width: 600px; border-collapse: collapse; font-size: var(--lv-fs-sm); font-variant-numeric: tabular-nums; }
.cp-table th, .cp-table td { padding: 7px 10px; text-align: right; border-bottom: 1px solid var(--lv-line-soft); vertical-align: top; }
.cp-table th:first-child, .cp-table td:first-child { text-align: left; white-space: normal; }
.cp-table thead th { font-size: var(--lv-fs-2xs); text-transform: uppercase; letter-spacing: var(--lv-track-kicker); color: var(--lv-muted); font-weight: var(--lv-fw-bold); white-space: normal; }
.cp-table tbody tr { cursor: pointer; }
.cp-table tbody tr:hover { background: var(--lv-hover); }
.cp-table tbody tr:focus-visible { outline: 2px solid var(--lv-focus); outline-offset: -2px; }
.cp-table tbody tr.active { background: var(--lv-hl-bg); }
.cp-table tbody tr.picked { box-shadow: inset 0 0 0 2px var(--lv-ink); }
.cp-table td small { display: block; color: var(--lv-muted); font-size: var(--lv-fs-xs); font-weight: var(--lv-fw-regular); white-space: nowrap; }
.cp-table .name { font-weight: var(--lv-fw-semi); display: flex; flex-wrap: wrap; gap: 4px var(--lv-sp-2); align-items: center; }
.cp-table td.good { color: var(--lv-good); font-weight: var(--lv-fw-semi); }
.cp-table td.bad { color: var(--lv-neg); font-weight: var(--lv-fw-semi); }
.cp-table td.muted { color: var(--lv-muted); }
.cp-foot { display: flex; flex-wrap: wrap; align-items: center; gap: var(--lv-sp-2) var(--lv-sp-4); margin-top: var(--lv-sp-3); }
.cp-foot .where { font-size: var(--lv-fs-sm); color: var(--lv-muted); }
.cp-foot .state.err { color: var(--lv-bad); font-weight: var(--lv-fw-semi); }
.cp-active { margin-top: var(--lv-sp-2); font-size: var(--lv-fs-sm); color: var(--lv-on-inverse-2); }
.cp-active b { color: var(--lv-on-inverse); font-weight: var(--lv-fw-bold); }
@media (max-width: 1100px) { .cp-body { grid-template-columns: minmax(0, 1fr); } }
`;

function ensureStyle() {
  if (document.getElementById(STYLE_ID)) return;
  const s = document.createElement('style');
  s.id = STYLE_ID;
  s.textContent = CSS;
  document.head.append(s);
}

const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const monthText = (iso) => { const [y, m] = String(iso).split('-'); return `${MON[Number(m) - 1]} ${y}`; };
const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

// The sentence both pages show under the headline: which curve drives repeat revenue. Best effort: the page header may be redrawn by its page.
export function activeCurveLine(ctx, label, root = document) {
  ensureStyle();
  const { h } = ctx;
  const line = root.querySelector('#view .pg-tldr-line') || root.querySelector('.pg-tldr-line');
  if (!line) return;
  line.querySelectorAll('.cp-active').forEach((n) => n.remove());
  line.append(h('div', { class: 'cp-active' }, 'Repeat revenue uses: ', h('b', label)));
}

// Segmented source control, month select and cohorts-averaged control. state = {source, asof, n}; options = GET /cohorts/curve-options.
// onChange(state, what) fires after every change ('source' | 'asof' | 'n'). Returns {el, setState, setOptions}.
export function curveControls(ctx, { options, state, showN = true, onChange }) {
  ensureStyle();
  const { h } = ctx;
  let opts = options;
  const st = { ...state };
  const el = h('div', { class: 'cp-controls' });
  const months = () => (opts.asof_months || []).filter((m) => m.computable);

  function draw() {
    const ok = months();
    const sourceBtns = opts.sources.map((s) => {
      const disabled = s.key === 'asof' && !ok.length;
      return h('button', { type: 'button', class: 'seg', 'aria-pressed': String(st.source === s.key), disabled,
        title: disabled ? 'There is not enough cohort history to build an as-of curve yet.' : null,
        onclick: () => { if (st.source !== s.key) { st.source = s.key; draw(); onChange({ ...st }, 'source'); } } }, s.label);
    });
    const select = h('select', { id: 'cp-asof', 'aria-label': 'Month the curve is taken at the end of' },
      ok.map((m) => h('option', { value: m.month, selected: m.month === st.asof }, m.is_latest ? `${m.label} (latest)` : m.label)));
    select.addEventListener('change', () => { st.asof = select.value; onChange({ ...st }, 'asof'); });
    const nBtns = opts.n_choices.map((n) => h('button', { type: 'button', class: 'seg', 'aria-pressed': String(st.n === n),
      onclick: () => { if (st.n !== n) { st.n = n; draw(); onChange({ ...st }, 'n'); } } }, String(n)));
    el.replaceChildren(...[
      h('div', { class: 'cp-field' }, h('span', { class: 'lbl', id: 'cp-src-lbl' }, 'Curve'), h('div', { class: 'segs', role: 'group', 'aria-labelledby': 'cp-src-lbl' }, sourceBtns)),
      st.source === 'asof' ? h('div', { class: 'cp-field' }, h('label', { class: 'lbl', for: 'cp-asof' }, 'As of the end of'), select) : null,
      showN && st.source !== 'goal' ? h('div', { class: 'cp-field' }, h('span', { class: 'lbl', id: 'cp-n-lbl' }, 'Cohorts averaged'), h('div', { class: 'segs', role: 'group', 'aria-labelledby': 'cp-n-lbl' }, nBtns)) : null,
      st.source === 'asof' ? h('div', { class: 'cp-note', style: { alignSelf: 'center', maxWidth: '34ch' } }, `Uses only data through the end of ${monthText(st.asof)}. ${opts.needs}.`) : null,
    ].filter(Boolean));
  }
  draw();
  return {
    el,
    state: () => ({ ...st }),
    setState(next) { Object.assign(st, next); draw(); },
    setOptions(next) { opts = next; if (!months().some((m) => m.month === st.asof)) st.asof = next.default_asof || (months()[0] || {}).month || null; draw(); },
  };
}

// levers to save for a picked option
export const leversFor = ({ source, asof, n }) => (source === 'goal' ? { curve_source: 'goal' }
  : source === 'asof' ? { curve_source: 'asof', asof_month: asof, n_cohorts: n } : { curve_source: 'live', n_cohorts: n });

export async function mountCurvePicker(host, ctx) {
  ensureStyle();
  const { h, fmt, ui, toast } = ctx;
  const slug = ctx.scenario;
  let chart = null;
  let dead = false;
  const card = h('section', { class: 'pg-card', 'aria-label': 'Retention curve' });
  host.replaceChildren(card);
  ui.loading(card, 'Loading the retention curve');

  let options;
  try {
    options = await ctx.api.get('cohorts/curve-options');
  } catch (e) { ui.error(card, e, ctx.reload); return () => {}; }
  if (!ctx.alive()) return () => {};
  if (options.available === false) { host.remove(); return () => {}; }              // no cohort tables in this database: nothing to pick

  const saved = options.saved;
  const state = { source: saved.curve_source === 'asof' || saved.curve_source === 'goal' ? saved.curve_source : 'live', asof: saved.asof_month || options.default_asof, n: options.n };
  let data = null;
  const keyOf = (source) => (source === 'live' ? 'current' : source);           // the controls say live, the rows say current
  const sourceOf = (key) => (key === 'current' ? 'live' : key === 'asof2' ? 'asof' : key);
  let picked = keyOf(state.source);                           // the row that is highlighted (what Use this curve would save)
  const tableHost = h('div', { class: 'cp-scroll' });
  const chartCanvas = h('canvas', { role: 'img', 'aria-label': 'Repeat per first-order dollar by month after the first order: current, goal and as-of curves' });
  const stateEl = h('span', { class: 'state', role: 'status' });
  const useBtn = h('button', { type: 'button', class: 'btn primary', disabled: true, onclick: () => apply(picked) }, 'Use this curve');
  const controls = curveControls(ctx, { options, state, onChange: (s, what) => onControls(s, what) });
  const where = ctx.scenarioInfo && ctx.scenarioInfo.is_default
    ? 'Saved in the plan of record.' : `Saved in ${ctx.scenarioInfo ? ctx.scenarioInfo.name : 'this scenario'} only; the plan of record keeps its own curve.`;

  card.replaceChildren(
    h('div', { class: 'pn-card-head', style: { display: 'flex', flexWrap: 'wrap', alignItems: 'baseline', justifyContent: 'space-between', gap: 'var(--lv-sp-2) var(--lv-sp-4)', marginBottom: 'var(--lv-sp-2)' } },
      h('h2', { style: { margin: 0 } }, 'Retention curve'), h('span', { class: 'cp-note' }, 'The repeat revenue each cohort keeps producing, as a share of its first order, by month after that order.')),
    controls.el,
    h('div', { class: 'cp-body' }, h('div', null, h('div', { class: 'pg-chartbox' }, chartCanvas)), tableHost),
    h('div', { class: 'cp-foot' }, useBtn, h('span', { class: 'where' }, where), stateEl),
    ui.source('Cohort exports through the latest closed month. Figures marked * are projected; each row runs the whole model with that curve.'));

  const sel = () => picked;

  async function load() {
    const s = controls.state();
    stateEl.className = 'state';
    stateEl.textContent = '';
    try {
      const params = { n: s.n };
      if (s.asof) params.asof = s.asof;
      data = await ctx.api.get('cohorts/curve-compare', params);
    } catch (e) {
      stateEl.className = 'state err';
      stateEl.textContent = e.message;
      if (!data) tableHost.replaceChildren(h('div', { class: 'empty' }, e.message));
      return;
    }
    if (dead || !ctx.alive()) return;
    drawTable();
    drawChart();
    refreshUse();
  }

  async function onControls(s, what) {
    picked = keyOf(s.source);
    if (what === 'source' && data) { drawTable(); drawChart(); refreshUse(); return; }           // the rows already hold every option
    if (what === 'n') {
      try {
        const next = await ctx.api.get('cohorts/curve-options', { n: s.n });
        controls.setOptions(next);
        options = next;
        const adjusted = controls.state();
        if (adjusted.asof !== s.asof) stateEl.textContent = `With ${s.n} cohorts the earliest month is ${monthText(next.asof_months.find((m) => m.computable).month)}.`;
      } catch (e) { stateEl.className = 'state err'; stateEl.textContent = e.message; return; }
    }
    await load();
  }

  const optFor = (key) => (data ? data.options.find((o) => o.key === key) : null);

  function refreshUse() {
    const o = optFor(sel());
    useBtn.disabled = !o || o.active;
    useBtn.textContent = o && o.active ? 'In use' : 'Use this curve';
  }

  async function apply(key) {
    const s = controls.state();
    const levers = leversFor({ source: sourceOf(key), asof: s.asof, n: s.n });
    useBtn.disabled = true;
    stateEl.className = 'state';
    stateEl.textContent = 'Saving';
    try {
      const res = await ctx.api.put('cohorts/levers', { scenario: slug, levers });
      events.emit('saved', { scenario: res.scenario || slug, version: res.version });
      const label = (optFor(key) || {}).label || res.levers.curve_source;
      toast(`Repeat revenue now uses: ${label}`, 'ok');
      ctx.reload();
    } catch (e) {
      const cancelled = e && e.name === 'EditCancelled';                                   // the plan-of-record prompt was cancelled: nothing was sent
      stateEl.className = cancelled ? 'state' : 'state err';
      stateEl.textContent = e.message;
      refreshUse();
    }
  }

  const short = (v) => { const a = Math.abs(v); return a >= 1e6 ? `$${(a / 1e6).toFixed(1)}M` : a >= 1e3 ? `$${Math.round(a / 1e3)}K` : `$${Math.round(a)}`; };       // one decimal in millions, so $6.0M lines up with $6.9M
  const money = (v) => (Number.isFinite(v) ? (v < 0 ? '-' : '') + short(v) : '');
  const signedMoney = (v) => (Number.isFinite(v) ? (v === 0 ? '$0' : `${v > 0 ? '+' : '-'}${short(v)}`) : '');
  const yr = () => `FY${String(data.year).slice(2)}`;

  function drawTable() {
    const y = yr();
    const rows = data.options.map((o) => {
      const d = o.vs_current;
      const isCur = o.key === 'current';
      const tone = !Number.isFinite(d.noi) || d.noi === 0 ? '' : d.noi > 0 ? 'good' : 'bad';
      const ev = o.fy.ev || {};
      const lo = ev['3x'], hi = ev['8x'];
      const pick = () => { picked = o.key; controls.setState({ source: sourceOf(o.key) }); apply(o.key); };
      return h('tr', {
        class: (o.active ? 'active ' : '') + (o.key === sel() && !o.active ? 'picked' : ''), tabindex: 0, role: 'button', 'aria-pressed': String(o.active),
        'aria-label': `Use ${o.label}`, onclick: pick, onkeydown: (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick(); } },
      },
      h('td', null, h('div', { class: 'name' }, o.label, o.active ? ui.chip('In use', 'good') : null)),
      h('td', fmt.x(o.lifetime, 2)),
      h('td', fmt.x(o.twelve, 2)),
      h('td', money(o.fy.repeat_rev) ? money(o.fy.repeat_rev) + '*' : ''),
      h('td', money(o.fy.total_revenue) ? money(o.fy.total_revenue) + '*' : ''),
      h('td', null, money(o.fy.noi) ? money(o.fy.noi) + '*' : '', Number.isFinite(lo) && Number.isFinite(hi) ? h('small', `Worth ${money(lo)} to ${money(hi)}*`) : null),
      isCur ? h('td', { class: 'muted' }, 'Baseline') : h('td', { class: tone }, signedMoney(d.noi) ? signedMoney(d.noi) + '*' : '', Number.isFinite(d.lifetime) ? h('small', `${d.lifetime >= 0 ? '+' : '-'}${Math.abs(d.lifetime).toFixed(2)}x repeat`) : null));
    });
    tableHost.replaceChildren(h('table', { class: 'cp-table' },
      h('thead', null, h('tr', null, h('th', 'Curve'), h('th', 'Repeat per first-order dollar'), h('th', 'First 12 months'), h('th', `${y} repeat revenue`), h('th', `${y} revenue`),
        h('th', `${y} net operating income`), h('th', 'Change against Current'))),
      h('tbody', null, rows)));
  }

  function drawChart() {
    if (chart) { chart.destroy(); chart = null; }
    if (typeof Chart === 'undefined' || !data) return;
    const ctxCharts = ctx.charts;
    const pal = ctxCharts.PALETTE;
    const muted = cssVar('--lv-viz-axis');
    Chart.defaults.font.family = cssVar('--lv-font');
    Chart.defaults.font.size = 12;
    Chart.defaults.color = muted;
    const ages = data.ages.slice(0, 36);
    const key = sel();
    const style = (o, color, dash) => ({ label: o.short, data: o.curve.slice(0, 36), borderColor: color, backgroundColor: color, pointRadius: 0, tension: 0.15,
      borderWidth: o.key === key ? 3 : 1.75, borderDash: dash });
    const sets = [];
    const cur = optFor('current'), goal = optFor('goal'), asof = optFor('asof'), asof2 = optFor('asof2');
    if (cur) sets.push(style(cur, cssVar('--lv-viz-ink'), undefined));
    if (goal) sets.push(style(goal, pal[1], undefined));
    if (asof) sets.push(style(asof, pal[3], [6, 4]));
    if (asof2) sets.push(style(asof2, pal[5], [2, 3]));
    chart = new Chart(chartCanvas, {
      type: 'line', data: { labels: ages, datasets: sets },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false, interaction: { mode: 'index', intersect: false },
        scales: {
          x: { grid: { display: false }, title: { display: true, text: 'Months after the first order', color: muted }, ticks: { color: muted, maxRotation: 0, autoSkip: true, maxTicksLimit: 12 } },
          y: { beginAtZero: true, grid: { color: cssVar('--lv-viz-grid') }, title: { display: true, text: 'Repeat per first-order dollar', color: muted }, ticks: { color: muted, callback: (v) => fmt.pct(v, 0) } },
        },
        plugins: {
          legend: { position: 'top', align: 'end', labels: { boxWidth: 12, boxHeight: 2, color: muted } },
          tooltip: { callbacks: { title: (items) => `Month ${items[0].label} after the first order`, label: (c) => `${c.dataset.label}: ${fmt.pct(c.parsed.y, 1)}` } },
        },
      },
    });
  }

  activeCurveLine(ctx, saved.label, host.closest('.pg-page') || document);
  await load();
  return () => { dead = true; if (chart) { chart.destroy(); chart = null; } };
}
