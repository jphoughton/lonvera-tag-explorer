// Projection page: Inventory demand.
// What it shows: units sold or shipped each month by warehouse (US direct, Amazon, international) and product, from the trailing average of
// complete days. Assumptions per warehouse are edited in place (baseline days, monthly growth, seasonality, typed units per day); the monthly
// grid, charts and headline follow. International shipments can be uploaded (preview, then commit).
import { events } from './lib/api.js';

const ID = 'id-style';
const CSS = `
.id-feeds { display: flex; flex-wrap: wrap; gap: var(--lv-sp-2); align-items: center; }
.id-locs { display: grid; grid-template-columns: repeat(auto-fit, minmax(310px, 1fr)); gap: var(--lv-sp-3); align-items: start; }
.id-loc { display: flex; flex-direction: column; gap: var(--lv-sp-3); }
.id-loc-head { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: baseline; gap: 2px var(--lv-sp-3); }
.id-loc-head h3 { margin: 0; font-size: var(--lv-fs-md); font-weight: var(--lv-fw-heavy); }
.id-loc-sub { margin: 0; font-size: var(--lv-fs-xs); color: var(--lv-muted); }
.id-rate { display: flex; align-items: baseline; gap: var(--lv-sp-2); }
.id-rate .n { font-size: var(--lv-fs-2xl); font-weight: var(--lv-fw-heavy); letter-spacing: -0.02em; font-variant-numeric: tabular-nums; line-height: 1.1; }
.id-rate .u { color: var(--lv-muted); font-size: var(--lv-fs-sm); }
.id-prod { width: 100%; border-collapse: collapse; font-size: var(--lv-fs-sm); font-variant-numeric: tabular-nums; }
.id-prod th, .id-prod td { padding: 3px 6px; text-align: right; border-bottom: 1px solid var(--lv-line-soft); white-space: nowrap; }
.id-prod th:first-child, .id-prod td:first-child { text-align: left; padding-left: 0; }
.id-prod thead th { font-size: var(--lv-fs-2xs); text-transform: uppercase; letter-spacing: var(--lv-track-kicker); color: var(--lv-muted); font-weight: var(--lv-fw-bold); }
.id-prod td.in { width: 92px; }
.id-fields { display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: var(--lv-sp-2) var(--lv-sp-3); }
.id-field { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
.id-field > label { font-size: var(--lv-fs-xs); font-weight: var(--lv-fw-semi); color: var(--lv-ink-2); }
.id-in, .pg-body input.id-in { width: 100%; min-height: 30px; padding: 0 8px; text-align: right; font-variant-numeric: tabular-nums; font-weight: var(--lv-fw-semi); background: var(--lv-input-bg); border: 1px solid var(--lv-input-edge); border-radius: var(--lv-r-xs); color: var(--lv-ink); }
.id-in:hover { background: var(--lv-input-hover); }
.id-in:focus-visible { outline: 2px solid var(--lv-focus); outline-offset: 1px; background: var(--lv-surface); }
.id-in.bad { border-color: var(--lv-bad); outline: 2px solid var(--lv-bad); }
.id-check { display: flex; align-items: center; gap: var(--lv-sp-2); font-size: var(--lv-fs-sm); color: var(--lv-ink-2); min-height: 30px; }
.id-season summary { list-style: none; cursor: pointer; font-size: var(--lv-fs-sm); font-weight: var(--lv-fw-semi); color: var(--lv-ink-2); min-height: 28px; display: flex; align-items: center; }
.id-season summary::before { content: ""; width: 8px; height: 8px; margin-right: 8px; border-right: 2px solid var(--lv-ink-2); border-bottom: 2px solid var(--lv-ink-2); transform: rotate(-45deg); transition: transform var(--lv-dur) var(--lv-ease); }
.id-season[open] summary::before { transform: rotate(45deg); }
.id-season-grid { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: var(--lv-sp-2); margin-top: var(--lv-sp-2); }
.id-season-grid .id-field > label { text-align: center; color: var(--lv-muted); }
.id-status { min-height: 1.2em; font-size: var(--lv-fs-xs); color: var(--lv-muted); }
.id-status.err { color: var(--lv-bad); font-weight: var(--lv-fw-semi); }
.id-two { display: grid; grid-template-columns: minmax(0, 1fr); gap: var(--lv-sp-4); }
.id-chart-head { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: var(--lv-sp-2) var(--lv-sp-4); margin-bottom: var(--lv-sp-2); }
.id-chart-head h2 { margin: 0; font-size: var(--lv-fs-md); font-weight: var(--lv-fw-heavy); }
.id-pick { display: flex; flex-wrap: wrap; gap: var(--lv-sp-2); align-items: center; }
.id-pick select { min-width: 140px; }
.id-country { width: 100%; border-collapse: collapse; font-size: var(--lv-fs-sm); font-variant-numeric: tabular-nums; }
.id-country th, .id-country td { padding: 4px 8px; text-align: right; border-bottom: 1px solid var(--lv-line-soft); white-space: nowrap; }
.id-country th:first-child, .id-country td:first-child { text-align: left; }
.id-country thead th { font-size: var(--lv-fs-2xs); text-transform: uppercase; letter-spacing: var(--lv-track-kicker); color: var(--lv-muted); font-weight: var(--lv-fw-bold); }
.id-scroll { overflow-x: auto; }
.id-up { display: flex; flex-direction: column; gap: var(--lv-sp-3); }
.id-up table { width: 100%; border-collapse: collapse; font-size: var(--lv-fs-sm); font-variant-numeric: tabular-nums; }
.id-up th, .id-up td { text-align: right; padding: 4px 8px; border-bottom: 1px solid var(--lv-line-soft); }
.id-up th:first-child, .id-up td:first-child, .id-up th:nth-child(2), .id-up td:nth-child(2) { text-align: left; }
.id-up thead th { font-size: var(--lv-fs-2xs); text-transform: uppercase; letter-spacing: var(--lv-track-kicker); color: var(--lv-muted); }
.id-up .counts { display: flex; flex-wrap: wrap; gap: var(--lv-sp-2); }
.id-up .counts div { background: var(--lv-surface-2); border-radius: var(--lv-r-sm); padding: 8px 12px; min-width: 110px; }
.id-up .counts b { display: block; font-size: var(--lv-fs-lg); font-weight: var(--lv-fw-heavy); }
.id-up .counts span { font-size: var(--lv-fs-xs); color: var(--lv-muted); }
.id-up ul { margin: 0; padding-left: 18px; color: var(--lv-bad); font-size: var(--lv-fs-sm); }
.id-drop { display: flex; flex-direction: column; gap: 4px; align-items: flex-start; border: 2px dashed var(--lv-line-strong); border-radius: var(--lv-r-sm); padding: var(--lv-sp-4); cursor: pointer; }
.id-drop:focus-within { outline: 2px solid var(--lv-focus); outline-offset: 2px; }
.id-drop .t { font-weight: var(--lv-fw-semi); }
.id-drop .s { font-size: var(--lv-fs-xs); color: var(--lv-muted); }
.id-dlg { width: min(720px, calc(100vw - 24px)); max-height: calc(100vh - 32px); overflow: auto; }
@media (min-width: 1100px) { .id-two { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 640px) { .id-season-grid { grid-template-columns: repeat(4, minmax(0, 1fr)); } .id-in, .pg-body input.id-in { min-height: 44px; } }
`;

function ensureStyle() {
  if (document.getElementById(ID)) return;
  const s = document.createElement('style');
  s.id = ID;
  s.textContent = CSS;
  document.head.append(s);
}

const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const SEASON_LABELS = MON;
const dayLabel = (iso) => { const [, m, d] = String(iso).split('-'); return `${MON[Number(m) - 1]} ${Number(d)}`; };
const monLabel = (iso) => { const [y, m] = String(iso).split('-'); return `${MON[Number(m) - 1]} ${y}`; };
const PRODUCT_NAME = { bull: 'Vital Bull', liver: 'Beef Liver', boneflex: 'Boneflex', belle: 'Vital Belle', flow: 'Vital Flow' };
const PRODUCT_SLOT = { bull: 0, boneflex: 1, liver: 2, belle: 3, flow: 4 };       // palette slots 1-3 are Vital Bull, Boneflex, Beef Liver
const LOC_COLOR_SLOT = { revamp: 3, amazon: 4, intl: 5 };                          // locations use slots 4-6 so a product keeps its own color

// ---------- multipart upload (the shared fetch layer is JSON only) ----------
async function postUpload(file, { commit = false, digest = '' } = {}) {
  const fd = new FormData();
  fd.append('file', file, file.name);
  if (commit) fd.append('digest', digest);
  const res = await fetch('/api/projection/inventory-demand/upload-intl' + (commit ? '?commit=1' : ''), { method: 'POST', body: fd, credentials: 'same-origin' });
  if (res.status === 401) { location.assign('/login?next=' + encodeURIComponent(location.pathname + location.search)); throw new Error('Sign in required'); }
  let data = null;
  try { data = await res.json(); } catch { /* not JSON */ }
  if (!res.ok) {
    const code = data && data.error;
    throw new Error(res.status === 429 ? 'Too many uploads in a short time. Wait a bit and try again.' : (code || `HTTP ${res.status}`));
  }
  return data;
}

export async function render(el, ctx) {
  ensureStyle();
  const { h, fmt, ui, charts, toast } = ctx;
  const slug = ctx.scenario;
  const own = [];                                  // charts created here directly (Chart.js), destroyed on teardown
  const root = h('div', { class: 'pg-page' });
  el.replaceChildren(root);
  ui.loading(root, 'Loading inventory demand');

  const load = () => Promise.all([ctx.api.table('inventory-demand'), ctx.api.get('inventory-demand/inputs'), ctx.api.get('inventory-demand/countries')]);
  let table, inputs, countries;
  try { [table, inputs, countries] = await load(); } catch (e) { ui.error(root, e, ctx.reload); return undefined; }
  if (!ctx.alive()) return undefined;

  const row = (t, key) => t.rows.find((r) => r.key === key);
  const idx = (t, iso) => t.months.indexOf(iso);
  const nextMonth = (iso) => { const [y, m] = iso.split('-').map(Number); return m === 12 ? `${y + 1}-01-01` : `${y}-${String(m + 1).padStart(2, '0')}-01`; };

  // ---------------------------------------------------------------- TLDR
  const hasData = () => inputs.locations.some((l) => l.feed.status !== 'none');
  function tldrParts() {
    const m1 = nextMonth(table.current_month);
    const i = idx(table, m1);
    const val = (key) => { const r = row(table, key); return r && i >= 0 ? (r.values[i] || 0) : 0; };
    const mon = `${monLabel(m1)}*`;
    const feeds = inputs.locations.map((l) => ({ l, age: l.feed.age_days, status: l.feed.status }));
    const worst = feeds.filter((f) => f.status !== 'none').sort((a, b) => b.age - a.age)[0];
    const tone = !worst ? 'bad' : worst.status === 'stale' ? 'bad' : worst.age > 1 ? 'warn' : 'good';
    const line = hasData()
      ? `Demand runs at ${fmt.num(val('perday.total'), 0)} units a day in ${mon}, ${fmt.pct(val('share.revamp'), 0)} of it US direct.`
      : 'No units are loaded yet. Run the daily refresh to load them.';
    const kpis = [
      { label: `Units in ${mon}`, value: fmt.num(val('total'), 0) },
      { label: 'US direct per day', value: fmt.num(val('perday.revamp'), 0), note: `${fmt.pct(val('share.revamp'), 0)} of units` },
      { label: 'Amazon per day', value: fmt.num(val('perday.amazon'), 0), note: `${fmt.pct(val('share.amazon'), 0)} of units` },
      { label: 'International per day', value: fmt.num(val('perday.intl'), 0), note: `${fmt.pct(val('share.intl'), 0)} of units` },
      { label: 'Oldest feed', value: worst ? `${worst.age} day${worst.age === 1 ? '' : 's'}` : 'none', note: worst ? worst.l.short : 'no data', tone },
    ];
    return { line, kpis };
  }
  const headHost = h('div');
  const uploadBtn = h('button', { type: 'button', class: 'btn primary', onclick: () => openUpload() }, 'Upload international shipments');
  function drawHeader() {
    headHost.replaceChildren(ui.pageHeader({
      title: 'Inventory demand',
      purpose: 'Units sold or shipped each month by warehouse and product, projected from the last few weeks. Change the assumptions to see what ordering has to cover.',
      actions: [uploadBtn],
      tldr: tldrParts(),
    }));
  }

  // ---------------------------------------------------------------- feed chips
  const feedsHost = h('div', { class: 'id-feeds', role: 'list', 'aria-label': 'Data freshness' });
  function drawFeeds() {
    const out = [];
    for (const l of inputs.locations) {
      const f = l.feed;
      if (f.status === 'none') { out.push(ui.chip(`${l.short}: nothing loaded`, 'bad')); continue; }
      const word = f.status === 'stale' ? `${f.age_days} DAYS OLD` : f.age_days > 1 ? `${f.age_days} DAYS OLD` : 'FRESH';
      const tone = f.status === 'stale' ? 'bad' : f.age_days > 1 ? 'warn' : 'good';
      out.push(ui.chip(`${l.short} through ${fmt.date(f.data_through)}: ${word}`, tone));
      if (f.estimate_days > 0) out.push(ui.chip(`${l.short}: ${f.estimate_days} estimated days in the baseline`, 'info', { estimate: true }));
    }
    for (const w of inputs.warnings || []) out.push(ui.chip(w, 'warn'));
    feedsHost.replaceChildren(...out.map((c) => h('span', { role: 'listitem' }, c)));
  }

  // ---------------------------------------------------------------- assumption cards
  const cards = new Map();                          // location -> {refs}
  const LOC_ORDER = ['revamp', 'amazon', 'intl'];

  function numberInput({ value, placeholder = '', label, parse, onCommit, format = null }) {
    const input = h('input', { type: 'text', inputmode: 'decimal', class: 'id-in', 'aria-label': label, placeholder, autocomplete: 'off' });
    input.value = value;
    input.dataset.saved = value;
    const revert = () => { input.value = input.dataset.saved; input.classList.remove('bad'); };
    const commit = async () => {
      const text = input.value.trim();
      if (text === input.dataset.saved) { input.classList.remove('bad'); return; }
      const r = parse(text);
      if (!r.ok) { input.classList.add('bad'); onCommit(null, r.error, input); return; }
      input.classList.remove('bad');
      const ok = await onCommit(r.value, null, input);
      if (ok === false) { revert(); return; }
      if (format) input.value = format(r.value);
      input.dataset.saved = input.value;
    };
    input.addEventListener('blur', commit);
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); input.blur(); }
      if (e.key === 'Escape') { e.preventDefault(); revert(); input.blur(); }
    });
    return input;
  }

  const parseNum = (min, max, whole = false, allowBlank = false) => (text) => {
    if (allowBlank && text === '') return { ok: true, value: null };
    const r = fmt.parse(text, 'num');
    if (!r.ok) return r;
    if (whole && r.value !== Math.round(r.value)) return { ok: false, error: 'Use a whole number' };
    if (r.value < min || r.value > max) return { ok: false, error: `Use a number from ${min} to ${max}` };
    return r;
  };
  const parseGrowth = (text) => {
    const r = fmt.parse(text, 'pct');
    if (!r.ok) return r;
    if (r.value < -0.5 || r.value > 1) return { ok: false, error: 'Use a growth from -50% to 100% a month' };
    return r;
  };

  async function save(loc, patch, status) {
    status.className = 'id-status';
    status.textContent = 'Saving…';
    try {
      const res = await ctx.api.put('inventory-demand/inputs', { scenario: slug, locations: { [loc]: patch } });
      events.emit('saved', { scenario: slug, version: res.version });
      await refresh();
      status.textContent = 'Saved';
      return true;
    } catch (e) {
      status.className = 'id-status err';
      status.textContent = e.message;
      toast(e.message, 'error');
      return false;
    }
  }

  function buildCard(l) {
    const status = h('div', { class: 'id-status', role: 'status' });
    const refs = { rate: h('span', { class: 'n' }), sub: h('p', { class: 'id-loc-sub' }), feed: h('span'), mix: new Map(), perDay: new Map() };
    const onErr = (msg, input) => { status.className = 'id-status err'; status.textContent = msg; input.focus(); };
    const wrap = (fn) => async (v, err, input) => { if (err) { onErr(err, input); return false; } return fn(v); };
    const lv = l.levers;

    const prodRows = l.products.map((p) => {
      const perDay = h('td', fmt.num(p.effective_rate, 1));
      const mix = h('td', fmt.pct(p.mix, 0));
      refs.perDay.set(p.key, perDay);
      refs.mix.set(p.key, mix);
      const input = numberInput({
        value: p.rate_override == null ? '' : String(p.rate_override), placeholder: fmt.num(p.baseline_rate, 1), label: `${p.label} units per day, typed`,
        parse: parseNum(0, 100000, false, true), format: (v) => (v == null ? '' : String(v)),
        onCommit: wrap((v) => save(l.key, { products: { [p.key]: { rate: v } } }, status)),
      });
      return h('tr', null, h('td', p.label), perDay, mix, h('td', { class: 'in' }, input));
    });

    const fields = [
      h('div', { class: 'id-field' }, h('label', `Baseline days`), numberInput({
        value: String(lv.baseline_days), label: `${l.short} baseline days`, parse: parseNum(1, 365, true),
        onCommit: wrap((v) => save(l.key, { baseline_days: v }, status)) })),
      h('div', { class: 'id-field' }, h('label', 'Monthly growth'), numberInput({
        value: fmt.pct(lv.monthly_growth, 1), label: `${l.short} monthly growth`, parse: parseGrowth, format: (v) => fmt.pct(v, 1),
        onCommit: wrap((v) => save(l.key, { monthly_growth: v }, status)) })),
      h('div', { class: 'id-field' }, h('label', 'Ignore latest days'), numberInput({
        value: String(lv.lag_days), label: `${l.short} latest days to ignore`, parse: parseNum(0, 60, true),
        onCommit: wrap((v) => save(l.key, { lag_days: v }, status)) })),
    ];
    const seasonInputs = lv.seasonality.map((s, i) => h('div', { class: 'id-field' }, h('label', SEASON_LABELS[i]), numberInput({
      value: String(s), label: `${l.short} seasonality ${SEASON_LABELS[i]}`, parse: parseNum(0, 10),
      onCommit: wrap((v) => { const next = [...refs.season.map((inp) => Number(inp.dataset.saved))]; next[i] = v; return save(l.key, { seasonality: next }, status); }) })));
    refs.season = seasonInputs.map((d) => d.querySelector('input'));
    const tie = h('label', { class: 'id-check' }, h('input', { type: 'checkbox', checked: lv.tie_to_dtc_revenue ? 'checked' : null, 'aria-label': `${l.short}: scale with projected revenue`,
      onchange: (e) => { save(l.key, { tie_to_dtc_revenue: e.target.checked }, status).then((ok) => { if (!ok) e.target.checked = !e.target.checked; }); } }),
      'Scale with projected revenue');

    const card = h('section', { class: 'pg-card id-loc', 'aria-label': l.label },
      h('div', { class: 'id-loc-head' }, h('h3', l.label), refs.feed),
      h('div', null, h('div', { class: 'id-rate' }, refs.rate, h('span', { class: 'u' }, 'units a day')), refs.sub),
      h('table', { class: 'id-prod' },
        h('thead', null, h('tr', null, h('th', 'Product'), h('th', 'Per day'), h('th', 'Mix'), h('th', 'Typed per day'))),
        h('tbody', null, prodRows)),
      h('p', { class: 'id-loc-sub' }, 'Type units per day to replace the average. Leave blank to use it.'),
      h('div', { class: 'id-fields' }, fields),
      l.key === 'amazon' ? null : tie,
      h('details', { class: 'id-season' }, h('summary', 'Seasonality by calendar month'),
        h('div', { class: 'id-season-grid' }, seasonInputs)),
      status);
    cards.set(l.key, refs);
    return card;
  }

  function updateCard(l) {
    const refs = cards.get(l.key);
    if (!refs) return;
    refs.rate.textContent = fmt.num(l.total_rate, 0);
    const w = l.window;
    refs.sub.textContent = w ? `Average of ${fmt.date(w.start)} to ${fmt.date(w.end)} (${l.levers.baseline_days} days)${l.feed.estimate_days ? `, ${l.feed.estimate_days} estimated` : ''}.` : 'No units loaded for this warehouse.';
    refs.feed.replaceChildren(l.feed.status === 'none' ? ui.chip('NO DATA', 'bad') : l.feed.status === 'stale' ? ui.chip(`STALE, ${l.feed.age_days} DAYS`, 'bad') : ui.chip('FRESH', 'good'));
    for (const p of l.products) {
      const a = refs.perDay.get(p.key); if (a) a.textContent = fmt.num(p.effective_rate, 1);
      const b = refs.mix.get(p.key); if (b) b.textContent = fmt.pct(p.mix, 0);
    }
  }
  const cardsHost = h('div', { class: 'id-locs' });
  function drawCards() {
    cards.clear();
    cardsHost.replaceChildren(...LOC_ORDER.map((k) => inputs.locations.find((l) => l.key === k)).filter(Boolean).map(buildCard));
    inputs.locations.forEach(updateCard);
  }

  // ---------------------------------------------------------------- grid
  const gridHost = h('div');
  let grid = null;
  function drawGrid() {
    if (grid) { grid.update(table); return; }
    grid = ctx.grid(gridHost, { payload: table, title: 'Units by month', readOnly: true, groupCollapse: ['hdr.perday'] });
    try { grid.select('total', table.current_month); } catch { /* the grid has no such cell */ }
  }

  // ---------------------------------------------------------------- monthly chart
  const monthCanvas = h('canvas', { 'aria-label': 'Units by month and warehouse' });
  let monthChart = null;
  function drawMonthChart() {
    if (monthChart) { charts.destroy(monthChart); monthChart = null; }
    const total = row(table, 'total');
    const first = Math.max(0, total.values.findIndex((v) => v), idx(table, table.current_month) - 6);
    const last = Math.min(table.months.length - 1, idx(table, table.current_month) + 12);
    const cut = (arr) => arr.slice(first, last + 1);
    const series = LOC_ORDER.map((k) => ({ label: inputs.locations.find((l) => l.key === k).short, values: cut(row(table, `loc.${k}`).values), color: charts.PALETTE[LOC_COLOR_SLOT[k]] }));
    monthChart = charts.barChart(monthCanvas, { months: cut(table.months), status: cut(table.status), series, stacked: true, yFmt: 'num' });
  }

  // ---------------------------------------------------------------- daily chart
  const dailyCanvas = h('canvas', { 'aria-label': 'Units per day with the baseline' });
  const dailyNote = h('p', { class: 'id-loc-sub' });
  const state = { loc: 'revamp', prod: 'all' };
  const locSelect = h('select', { 'aria-label': 'Warehouse', onchange: (e) => { state.loc = e.target.value; state.prod = 'all'; fillProducts(); drawDaily(); } },
    LOC_ORDER.map((k) => h('option', { value: k }, inputs.locations.find((l) => l.key === k).short)));
  const prodSelect = h('select', { 'aria-label': 'Product', onchange: (e) => { state.prod = e.target.value; drawDaily(); } });
  function fillProducts() {
    const l = inputs.locations.find((x) => x.key === state.loc);
    prodSelect.replaceChildren(h('option', { value: 'all' }, 'All products'), ...l.products.map((p) => h('option', { value: p.key }, p.label)));
    prodSelect.value = state.prod;
  }
  let dailyChart = null;
  let dailySeq = 0;
  async function drawDaily() {
    const seq = ++dailySeq;
    let d;
    try { d = await ctx.api.get('inventory-demand/daily', { location: state.loc, product: state.prod, days: 120 }); } catch (e) { dailyNote.textContent = e.message; return; }
    if (!ctx.alive() || seq !== dailySeq) return;
    if (dailyChart) { dailyChart.destroy(); own.splice(own.indexOf(dailyChart), 1); dailyChart = null; }
    if (!d.days.length) { dailyNote.textContent = 'No daily units are loaded for this warehouse.'; return; }
    const css = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
    const ink = css('--lv-viz-ink');
    const color = state.prod === 'all' ? charts.PALETTE[LOC_COLOR_SLOT[state.loc]] : charts.PALETTE[PRODUCT_SLOT[state.prod] ?? 3];
    const real = d.units.map((v, i) => (d.estimate[i] || !d.loaded[i] ? null : v));
    const est = d.units.map((v, i) => (d.estimate[i] ? v : null));
    const labels = d.days.map(dayLabel);
    if (typeof Chart === 'undefined') { dailyNote.textContent = 'The chart library did not load.'; return; }
    Chart.defaults.font.family = css('--lv-font');
    Chart.defaults.font.size = 12;
    Chart.defaults.color = css('--lv-viz-axis');
    dailyChart = new Chart(dailyCanvas, {
      data: { labels, datasets: [
        { type: 'bar', label: 'Units', data: real, backgroundColor: color, borderRadius: 3, maxBarThickness: 10, order: 3 },
        { type: 'bar', label: 'Estimated units', data: est, backgroundColor: css('--lv-surface'), borderColor: color, borderWidth: 1.5, borderRadius: 3, maxBarThickness: 10, order: 3 },
        { type: 'line', label: '7-day average', data: d.ma7, borderColor: ink, backgroundColor: ink, borderWidth: 2, pointRadius: 0, tension: 0.2, spanGaps: false, order: 1 },
        { type: 'line', label: 'Baseline', data: d.baseline, borderColor: css('--lv-ink-2'), backgroundColor: css('--lv-ink-2'), borderWidth: 2, borderDash: [6, 4], pointRadius: 0, spanGaps: false, order: 0 },
      ] },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false, interaction: { mode: 'index', intersect: false },
        scales: { x: { grid: { display: false }, ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 10 } },
          y: { beginAtZero: true, grid: { color: css('--lv-viz-grid') }, ticks: { callback: (v) => fmt.num(v, 0) } } },
        plugins: { legend: { position: 'top', align: 'end', labels: { boxWidth: 10, boxHeight: 10 } },
          tooltip: { backgroundColor: css('--lv-inverse'), titleColor: css('--lv-on-inverse'), bodyColor: css('--lv-on-inverse'), cornerRadius: 8, padding: 10,
            callbacks: { label: (c) => (c.parsed.y == null ? null : `${c.dataset.label}: ${fmt.num(c.parsed.y, c.dataset.type === 'line' ? 1 : 0)}`) } } },
      },
    });
    own.push(dailyChart);
    const w = d.window;
    dailyNote.textContent = w ? `Baseline ${fmt.num(d.rate, 1)} units a day, the average of ${dayLabel(w.start)} to ${dayLabel(w.end)}.` : '';
  }

  // ---------------------------------------------------------------- international by country
  const countryHost = h('div', { class: 'id-scroll' });
  function drawCountries() {
    if (!countries.countries.length) { countryHost.replaceChildren(h('div', { class: 'empty' }, 'No country split is loaded yet.')); return; }
    const head = h('tr', null, h('th', 'Country'), ...countries.months.map((m, i) => h('th', countries.days[i] ? `${monLabel(m)} (${countries.days[i]} days)` : monLabel(m))), h('th', 'Share'));
    const top = countries.countries.slice(0, 6);
    const rest = countries.countries.slice(6);
    const body = top.map((c) => h('tr', null, h('td', c.country), ...c.units.map((u) => h('td', fmt.num(u, 0))), h('td', fmt.pct(c.share, 0))));
    if (rest.length) {
      body.push(h('tr', null, h('td', `${rest.length} other countries`), ...countries.months.map((_, i) => h('td', fmt.num(rest.reduce((s, c) => s + c.units[i], 0), 0))),
        h('td', fmt.pct(rest.reduce((s, c) => s + c.share, 0), 0))));
    }
    countryHost.replaceChildren(h('table', { class: 'id-country' }, h('thead', null, head), h('tbody', null, body)));
  }

  // ---------------------------------------------------------------- upload dialog
  function openUpload() {
    const dlg = h('dialog', { class: 'pg-dialog id-dlg' });
    const errBox = h('div', { class: 'pg-dialog-err', role: 'alert' });
    const preview = h('div', { class: 'id-up' });
    const file = h('input', { type: 'file', accept: '.csv,.tsv,.txt,text/csv', 'aria-label': 'International shipments file' });
    const drop = h('label', { class: 'id-drop' },
      h('span', { class: 't' }, 'Choose the international shipments file'),
      h('span', { class: 's' }, 'CSV with a date column and one column per product (Vital Bull, Beef Liver, Boneflex, Vital Flow), or a product and units column. Only days that are over are used.'),
      file);
    let current = null;
    const commitBtn = h('button', { type: 'button', class: 'btn primary', disabled: true }, 'Commit changes');
    const close = () => { dlg.close(); dlg.remove(); };
    dlg.addEventListener('cancel', (e) => { e.preventDefault(); close(); });
    const count = (n, label) => h('div', null, h('b', fmt.num(n, 0)), h('span', label));
    function showPreview(res) {
      const d = res.diff;
      const parts = [
        h('p', { class: 'muted', style: { margin: 0 } }, `${fmt.num(d.rows, 0)} values over ${d.days} days, ${d.first_day ? `${fmt.date(d.first_day)} to ${fmt.date(d.last_day)}` : 'no days'}. Nothing is saved until you commit.`),
        h('div', { class: 'counts' }, count(d.new, 'new values'), count(d.replaces_estimate, 'replace an estimate'), count(d.changes_loaded, 'change a loaded count'), count(d.unchanged, 'unchanged')),
      ];
      if (res.errors.length) parts.push(h('div', null, h('div', { class: 'pg-group-head', style: { margin: '0 0 4px' } }, 'Fix these first'), h('ul', null, res.errors.map((e) => h('li', e)))));
      if (d.moves.length) {
        parts.push(h('div', null, h('div', { class: 'pg-group-head', style: { margin: '0 0 4px' } }, `Largest changes (${Math.min(10, d.moves.length)} of ${d.moves_total})`),
          h('table', null, h('thead', null, h('tr', null, h('th', 'Day'), h('th', 'Product'), h('th', 'Stored'), h('th', 'New'), h('th', 'What happens'))),
            h('tbody', null, d.moves.slice(0, 10).map((m) => h('tr', null, h('td', fmt.date(m.day)), h('td', PRODUCT_NAME[m.product] || m.product), h('td', m.old == null ? 'none' : fmt.num(m.old, 0)),
              h('td', fmt.num(m.new, 0)), h('td', m.kind)))))));
      }
      preview.replaceChildren(...parts);
      const n = d.new + d.replaces_estimate + d.changes_loaded;
      commitBtn.textContent = n ? `Commit ${fmt.num(n, 0)} changes` : 'Nothing to commit';
      commitBtn.disabled = !n || res.errors.length > 0;
    }
    file.addEventListener('change', async () => {
      const f = file.files && file.files[0];
      errBox.textContent = '';
      preview.replaceChildren();
      commitBtn.disabled = true;
      current = null;
      if (!f) return;
      preview.append(h('div', { class: 'loading' }, 'Reading the file'));
      try { const res = await postUpload(f); current = { file: f, res }; showPreview(res); } catch (e) { preview.replaceChildren(); errBox.textContent = e.message; }
    });
    commitBtn.addEventListener('click', async () => {
      if (!current) return;
      commitBtn.disabled = true;
      errBox.textContent = '';
      try {
        const res = await postUpload(current.file, { commit: true, digest: current.res.digest });
        toast(`Saved ${fmt.num(res.rows_written, 0)} values to International`, 'ok');
        close();
        await refresh();
        drawDaily();
      } catch (e) { errBox.textContent = e.message; commitBtn.disabled = false; }
    });
    dlg.append(h('div', { class: 'pg-dialog-body' },
      h('h2', 'Upload international shipments'), drop, errBox, preview,
      h('div', { class: 'pg-dialog-actions' }, h('button', { type: 'button', class: 'btn', onclick: close }, 'Close'), commitBtn)));
    document.body.append(dlg);
    dlg.showModal();
  }

  // ---------------------------------------------------------------- refresh and layout
  async function refresh() {
    [table, inputs, countries] = await load();
    if (!ctx.alive()) return;
    drawHeader();
    drawFeeds();
    inputs.locations.forEach(updateCard);
    drawGrid();
    drawMonthChart();
    drawDaily();
  }

  drawHeader();
  drawFeeds();
  drawCards();
  fillProducts();
  const asOf = `Months from ${monLabel(table.current_month)} are forecast.`;
  const body = hasData() ? [
    headHost, feedsHost,
    h('section', { 'aria-label': 'Assumptions' }, h('div', { class: 'pg-group-head', style: { marginTop: 0 } }, 'Assumptions by warehouse'), cardsHost),
    h('section', { class: 'pg-card' }, gridHost),
    h('div', { class: 'id-two' },
      h('section', { class: 'pg-card' }, h('div', { class: 'id-chart-head' }, h('h2', 'Units by month and warehouse')), h('div', { class: 'pg-chartbox' }, monthCanvas),
        ui.source(asOf)),
      h('section', { class: 'pg-card' }, h('div', { class: 'id-chart-head' }, h('h2', 'Units per day'), h('div', { class: 'id-pick' }, locSelect, prodSelect)),
        h('div', { class: 'pg-chartbox' }, dailyCanvas), dailyNote)),
    h('section', { class: 'pg-card' }, h('div', { class: 'id-chart-head' }, h('h2', 'International units by country')), countryHost,
      ui.source('Overseas warehouse tracking exports, by destination country.')),
  ] : [headHost, feedsHost, h('div', { class: 'empty' }, 'No units are loaded yet. The daily refresh loads Warehance, Amazon and international units; run it, then reload this page.')];
  root.replaceChildren(...body);
  if (hasData()) { drawGrid(); drawMonthChart(); drawDaily(); drawCountries(); }

  return () => {
    for (const c of own) c.destroy();
    if (grid) grid.destroy();
    document.querySelectorAll('dialog.id-dlg').forEach((d) => d.remove());
  };
}
