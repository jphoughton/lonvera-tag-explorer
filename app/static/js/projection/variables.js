// Projection page: Variables.
// What it shows: every number that drives the plan, grouped by module, with the Lonvera Projections sheet's value beside the current value. Numbers are edited in place
// (PUT /variables, saved as you leave the cell); monthly series and tables show a short summary and a link to the page that edits them.
// A "what matters most" table reruns the model with each number moved down and up by 10% (GET /variables/sensitivity).
// Layout, copy and colors follow docs/projection/DESIGN.md; every color comes from brand.css tokens.
import { events } from './lib/api.js';
import { confirmBox } from './lib/util.js';

const STYLE_ID = 'va-style';
const CSS = `
.va-controls { display: flex; flex-wrap: wrap; align-items: center; gap: var(--lv-sp-2) var(--lv-sp-4); }
.va-controls .grow { flex: 1 1 240px; min-width: 200px; }
.va-controls input[type="search"] { width: 100%; }
.va-controls label.check { display: inline-flex; align-items: center; gap: 6px; font-size: var(--lv-fs-sm); color: var(--lv-ink-2); min-height: var(--lv-hit); cursor: pointer; }
.va-jump { display: flex; flex-wrap: wrap; gap: 6px; margin-top: var(--lv-sp-2); }
.va-jump a { font-size: var(--lv-fs-xs); font-weight: var(--lv-fw-bold); padding: 4px 10px; border-radius: var(--lv-r-pill); background: var(--lv-surface-2); color: var(--lv-ink-2); text-decoration: none; border: 1px solid var(--lv-line); }
.va-jump a:hover { background: var(--lv-hover); }
.va-note { font-size: var(--lv-fs-sm); color: var(--lv-muted); margin: var(--lv-sp-2) 0 0; }
.va-mod { border: 1px solid var(--lv-line); border-radius: var(--lv-r-md); background: var(--lv-surface); overflow: clip; }
.va-mod + .va-mod { margin-top: var(--lv-sp-3); }
.va-mod > summary { list-style: none; display: flex; flex-wrap: wrap; align-items: center; gap: var(--lv-sp-2) var(--lv-sp-3); padding: var(--lv-sp-3) var(--lv-sp-4); cursor: pointer; background: var(--lv-surface-2); min-height: var(--lv-hit); }
.va-mod > summary::-webkit-details-marker { display: none; }
.va-mod > summary::before { content: ""; width: 8px; height: 8px; border-right: 2px solid var(--lv-ink-2); border-bottom: 2px solid var(--lv-ink-2); transform: rotate(-45deg); transition: transform var(--lv-dur) var(--lv-ease); flex: 0 0 auto; }
.va-mod[open] > summary::before { transform: rotate(45deg); }
.va-mod > summary h2 { margin: 0; font-size: var(--lv-fs-md); font-weight: var(--lv-fw-heavy); }
.va-mod > summary .meta { font-size: var(--lv-fs-xs); color: var(--lv-muted); }
.va-mod > summary .sp { flex: 1 1 auto; }
.va-mod > summary a { font-size: var(--lv-fs-sm); font-weight: var(--lv-fw-bold); color: var(--lv-link); }
.va-scroll { overflow-x: auto; }
.va-table { width: 100%; border-collapse: collapse; font-size: var(--lv-fs-base); }
.va-table th { text-align: left; font-size: var(--lv-fs-2xs); text-transform: uppercase; letter-spacing: var(--lv-track-kicker); color: var(--lv-muted); font-weight: var(--lv-fw-bold); padding: 8px 10px; border-bottom: 1px solid var(--lv-line); background: var(--lv-surface); position: sticky; top: 0; }
.va-table td { padding: 6px 10px; border-bottom: 1px solid var(--lv-line-soft); vertical-align: top; }
.va-table tr.grp th { background: var(--lv-band-bg); color: var(--lv-ink-2); padding: 6px 10px; }
.va-table td.name { min-width: 240px; font-weight: var(--lv-fw-semi); color: var(--lv-ink); }
.va-table td.val { min-width: 190px; }
.va-table td.sheet { min-width: 130px; color: var(--lv-ink-2); font-variant-numeric: tabular-nums; }
.va-table td.unit { min-width: 120px; color: var(--lv-muted); font-size: var(--lv-fs-sm); }
.va-table td.used { min-width: 260px; color: var(--lv-ink-2); font-size: var(--lv-fs-sm); }
.va-table td.src { min-width: 190px; color: var(--lv-muted); font-size: var(--lv-fs-xs); }
.va-table tr.chg td.name { box-shadow: inset 3px 0 0 var(--lv-info); }
.va-table tr.miss td { color: var(--lv-muted); }
.va-table tr.miss td.name { font-weight: var(--lv-fw-medium); }
.va-in { display: flex; align-items: center; gap: 6px; }
.va-in input, .va-in select { width: 100%; max-width: 190px; font: inherit; font-variant-numeric: tabular-nums; min-height: 32px; padding: 0 8px; border: 1px solid var(--lv-input-edge); border-radius: var(--lv-r-sm); background: var(--lv-input-bg); color: var(--lv-input-ink); font-weight: var(--lv-fw-semi); }
.va-in input:hover, .va-in select:hover { background: var(--lv-input-hover); }
.va-in input:focus, .va-in select:focus { background: var(--lv-surface); outline: 2px solid var(--lv-focus); outline-offset: 1px; }
.va-in input.wide { max-width: 260px; }
.va-in .suffix { color: var(--lv-muted); font-size: var(--lv-fs-sm); }
.va-in.saving { opacity: 0.6; }
.va-in.bad input, .va-in.bad select { border-color: var(--lv-bad); box-shadow: inset 0 0 0 1px var(--lv-bad); }
.va-err { color: var(--lv-bad); font-size: var(--lv-fs-xs); font-weight: var(--lv-fw-semi); margin-top: 2px; }
.va-hint { color: var(--lv-muted); font-size: var(--lv-fs-xs); margin-top: 2px; }
.va-flash td.val { animation: va-flash 900ms var(--lv-ease); }
@keyframes va-flash { 0% { box-shadow: inset 0 0 0 2px var(--lv-good); } 100% { box-shadow: inset 0 0 0 2px transparent; } }
.va-btn { font: inherit; font-size: var(--lv-fs-xs); font-weight: var(--lv-fw-bold); border: 0; background: none; color: var(--lv-link); cursor: pointer; padding: 2px 4px; min-height: 24px; }
.va-btn:hover { text-decoration: underline; }
.va-chips { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 4px; }
.va-sum { color: var(--lv-ink-2); font-size: var(--lv-fs-sm); }
.va-sum a { font-weight: var(--lv-fw-bold); }
.va-sens { display: grid; grid-template-columns: minmax(180px, 1.4fr) minmax(0, 2.6fr) 90px 90px; gap: 0; font-size: var(--lv-fs-sm); font-variant-numeric: tabular-nums; }
.va-sens .h { font-size: var(--lv-fs-2xs); text-transform: uppercase; letter-spacing: var(--lv-track-kicker); color: var(--lv-muted); font-weight: var(--lv-fw-bold); padding: 6px 8px; border-bottom: 1px solid var(--lv-line); }
.va-sens .c { padding: 6px 8px; border-bottom: 1px solid var(--lv-line-soft); display: flex; align-items: center; min-width: 0; }
.va-sens .c.r { justify-content: flex-end; }
.va-sens .c.name { flex-direction: column; align-items: flex-start; justify-content: center; }
.va-sens .c.name small { color: var(--lv-muted); font-size: var(--lv-fs-xs); }
.va-bars { position: relative; width: 100%; height: 22px; }
.va-bars::before { content: ""; position: absolute; left: 50%; top: 0; bottom: 0; border-left: 1px solid var(--lv-line-strong); }
.va-bar { position: absolute; top: 3px; height: 16px; border-radius: 2px; }
.va-bar.dn { background: var(--lv-viz-2); }
.va-bar.up { background: var(--lv-viz-1); }
.va-bar.neg { right: 50%; }
.va-bar.pos { left: 50%; }
.va-sens .c.name small.txt { display: none; }
.va-bar-lab { position: absolute; top: 2px; font-size: var(--lv-fs-2xs); color: var(--lv-ink-2); white-space: nowrap; }
.va-legend { display: flex; flex-wrap: wrap; gap: var(--lv-sp-3); font-size: var(--lv-fs-xs); color: var(--lv-muted); margin: 0 0 var(--lv-sp-2); }
.va-legend i { display: inline-block; width: 12px; height: 12px; border-radius: 2px; margin-right: 5px; vertical-align: -2px; }
.va-legend i.dn { background: var(--lv-viz-2); }
.va-legend i.up { background: var(--lv-viz-1); }
.va-sens-wait { display: flex; align-items: center; gap: 8px; color: var(--lv-muted); font-size: var(--lv-fs-sm); padding: var(--lv-sp-3) 0; }
@media (max-width: 760px) {
  .va-table thead { display: none; }
  .va-table, .va-table tbody, .va-table tr, .va-table td { display: block; width: 100%; }
  .va-table tr.row { padding: 8px 0; border-bottom: 1px solid var(--lv-line); }
  .va-table td { border: 0; padding: 2px 12px; min-width: 0 !important; }
  .va-table td[data-label]::before { content: attr(data-label); display: block; font-size: var(--lv-fs-2xs); text-transform: uppercase; letter-spacing: var(--lv-track-kicker); color: var(--lv-muted); font-weight: var(--lv-fw-bold); }
  .va-table td.unit { display: none; }
  .va-in input, .va-in select { max-width: none; min-height: var(--lv-hit); }
  .va-sens { grid-template-columns: minmax(120px, 1.4fr) minmax(0, 2fr); }
  .va-sens .h.hide, .va-sens .c.hide { display: none; }
  .va-bar-lab { display: none; }
  .va-sens .c.name small.txt { display: block; }
}
`;

function ensureStyle() {
  if (document.getElementById(STYLE_ID)) return;
  const s = document.createElement('style');
  s.id = STYLE_ID;
  s.textContent = CSS;
  document.head.append(s);
}

const isNum = (v) => typeof v === 'number' && Number.isFinite(v);
const plural = (n, one, many = one + 's') => `${n} ${n === 1 ? one : many}`;
const short = (r) => r.short || String(r.label).split(':')[0];
const PAGES = { cohorts: 'Cohorts and curves', drivers: 'Spend plan', pnl: 'P&L', inventory_demand: 'Inventory demand', inventory_supply: 'Inventory supply', financing: 'Cash and debt', pacing: 'Pacing', modeler: 'Scenario modeler' };

export async function render(el, ctx) {
  ensureStyle();
  const { h, fmt, ui, toast } = ctx;
  const slug = ctx.scenario;
  const root = h('div', { class: 'pg-page' });
  el.replaceChildren(root);
  ui.loading(root, 'Loading the variables');

  let data;
  try { data = await ctx.api.get('variables'); } catch (e) { ui.error(root, e, ctx.reload); return undefined; }
  if (!ctx.alive()) return undefined;

  const state = { q: ctx.params.q || '', changedOnly: ctx.params.changed === '1', sens: null };
  const rows = new Map();                     // id -> {v (entry), tr, cell}
  const sections = new Map();                 // module -> {det, body, count}

  // ------------------------------------------------------------------ formatting
  const fmtValue = (v, e) => {
    if (v == null) return '';
    if (Array.isArray(v)) {
      if (!v.length) return 'None';
      if (v.every((x) => x === v[0]) && v.length > 3) return `${isNum(v[0]) ? fmt.num(v[0], 2) : v[0]} in every month`;
      const items = v.slice(0, 6).map((x) => (isNum(x) ? fmt.num(x, Math.abs(x) >= 1000 ? 0 : 2) : String(x)));
      return items.join(', ') + (v.length > 6 ? `, and ${v.length - 6} more` : '');
    }
    if (typeof v === 'boolean') return v ? 'On' : 'Off';
    if (e.value_type === 'fraction' && isNum(v)) return fmt.pct(v, Math.abs(v) < 0.1 && v !== 0 ? 2 : 1);
    if (e.value_type === 'money' && isNum(v)) return fmt.money(v, { digits: Math.abs(v) < 100 ? 2 : 0 });
    if (isNum(v)) return fmt.num(v, e.value_type === 'integer' ? 0 : (Math.abs(v) < 10 ? 3 : 2)).replace(/(\.\d*?)0+$/, '$1').replace(/\.$/, '');
    if (e.value_type === 'month' || e.value_type === 'date') return fmt.date ? fmt.date(v) : String(v);
    return String(v);
  };
  const sheetText = (e) => {
    if (e.status === 'MISSING') return e.default == null ? 'No value' : fmtValue(e.default, e);
    if (e.kind !== 'scalar') return e.kind === 'series' ? (e.series_cells ? plural(e.series_cells, 'typed month') : 'Typed months') : 'Sheet rows';
    if (e.default == null) return e.computed != null ? 'Computed from the books' : (e.value_type === 'bool' ? 'Off' : 'Blank');
    return fmtValue(e.default, e);
  };

  // ------------------------------------------------------------------ inputs
  function makeInput(e, row) {
    const wrap = h('div', { class: 'va-in' });
    const label = e.label;
    let input;
    const t = e.value_type;
    if (t === 'bool') {
      input = h('select', { 'aria-label': label }, h('option', { value: 'false' }, 'Off'), h('option', { value: 'true' }, 'On'));
      input.value = String(e.value === true);
    } else if (t === 'choice') {
      input = h('select', { 'aria-label': label }, e.choices.map((c) => h('option', { value: c }, c.replace('_', ' '))));
      input.value = e.value ?? e.default ?? e.choices[0];
    } else if (['number', 'money', 'fraction', 'integer'].includes(t)) {
      const pct = t === 'fraction';
      const scale = pct ? 100 : 1;
      const step = e.step ? e.step * scale : (t === 'integer' ? 1 : (pct ? 0.5 : 'any'));
      input = h('input', { type: 'number', 'aria-label': label, inputmode: 'decimal', step: String(step), min: e.min != null ? String(e.min * scale) : null, max: e.max != null ? String(e.max * scale) : null,
        placeholder: e.computed != null ? (pct ? `${(e.computed * 100).toFixed(2)}` : String(Math.round(e.computed * 100) / 100)) : (e.value == null ? 'blank' : null) });
      input.value = e.value == null ? '' : String(Math.round(e.value * scale * 1e6) / 1e6);
      input.dataset.scale = String(scale);
    } else {
      const v = e.value;
      input = h('input', { type: 'text', 'aria-label': label, class: t === 'list' ? 'wide' : null, placeholder: e.value == null ? 'blank' : null });
      input.value = Array.isArray(v) ? v.join(', ') : (v ?? '');
    }
    const suffix = ['fraction'].includes(t) ? '%' : t === 'money' && e.unit.includes('per month') ? '/month' : '';
    wrap.append(input);
    if (suffix) wrap.append(h('span', { class: 'suffix' }, suffix));
    const orig = input.value;
    const readValue = () => {
      const raw = input.value.trim();
      if (t === 'bool') return raw === 'true';
      if (['choice'].includes(t)) return raw;
      if (raw === '') return null;
      if (['number', 'money', 'fraction', 'integer'].includes(t)) {
        const n = Number(raw.replace(/[$,%\s]/g, ''));
        if (!Number.isFinite(n)) throw new Error('Enter a number.');
        return Math.round((n / Number(input.dataset.scale || 1)) * 1e10) / 1e10;
      }
      return raw;
    };
    input.addEventListener('change', async () => {
      let value;
      try { value = readValue(); } catch (err) { showError(row, err.message); input.value = orig; return; }
      await save([[e.id, value]], row, input, orig);
    });
    input.addEventListener('keydown', (ev) => { if (ev.key === 'Escape') { input.value = orig; input.blur(); } });
    return wrap;
  }

  function showError(row, msg) {
    const old = row.cell.querySelector('.va-err');
    if (old) old.remove();
    row.cell.querySelector('.va-in').classList.toggle('bad', !!msg);
    if (msg) row.cell.append(h('div', { class: 'va-err', role: 'alert' }, msg));
  }

  async function save(items, row, input, orig) {
    const wrap = row.cell.querySelector('.va-in');
    showError(row, '');
    wrap.classList.add('saving');
    try {
      const res = await ctx.api.put('variables', { scenario: slug, items: items.map(([id, value]) => ({ id, value })) });
      events.emit('saved', { scenario: slug, version: res.scenario.version });
      applyUpdates(res.variables);
      row.tr.classList.add('va-flash');
      setTimeout(() => row.tr.classList.remove('va-flash'), 1000);
      refreshChrome();
      queueSensitivity();
      return true;
    } catch (err) {
      if (input) input.value = orig;
      if (err && err.name === 'EditCancelled') return false;                         // the plan-of-record guard was cancelled: nothing was sent
      showError(row, err.message || 'The change was not saved. Your other edits are safe.');
      return false;
    } finally { wrap.classList.remove('saving'); }
  }

  async function resetIds(ids, row) {
    try {
      const res = await ctx.api.post('variables/reset', { scenario: slug, ids });
      events.emit('saved', { scenario: slug, version: res.scenario.version });
      applyUpdates(res.variables);
      toast(res.reset.length ? `Reset ${plural(res.reset.length, 'variable')} to the sheet value` : 'Already at the sheet value', 'ok');
      refreshChrome();
      queueSensitivity();
    } catch (err) {
      if (err && err.name === 'EditCancelled') return;
      if (row) showError(row, err.message); else toast(err.message, 'error');
    }
  }

  // ------------------------------------------------------------------ rows
  function buildRow(e) {
    const tr = h('tr', { class: 'row' + (e.changed ? ' chg' : '') + (e.status === 'MISSING' ? ' miss' : ''), dataset: { id: e.id } });
    const row = { v: e, tr, cell: null };
    // value
    const cell = h('td', { class: 'val', 'data-label': 'Value' });
    row.cell = cell;
    if (e.status === 'MISSING') {
      cell.append(h('span', { class: 'va-sum' }, 'Not in the model'));
    } else if (e.editable_inline) {
      cell.append(makeInput(e, row));
      if (e.computed != null && e.value == null) cell.append(h('div', { class: 'va-hint' }, 'Blank uses the computed value'));
    } else if (e.kind === 'scalar') {
      cell.append(h('span', { class: 'va-sum' }, fmtValue(e.value ?? e.default, e) || 'Blank'), h('div', { class: 'va-hint' }, 'Edited on its own page'));
    } else {
      const s = e.summary || {};
      cell.append(h('span', { class: 'va-sum' }, s.text || 'Rows on the module page'));
    }
    const sheet = h('td', { class: 'sheet', 'data-label': 'Sheet value' }, sheetText(e),
      e.changed && e.editable_inline ? h('div', null, h('button', { type: 'button', class: 'va-btn', onclick: () => resetIds([e.id], row), 'aria-label': `Reset ${e.label} to the sheet value` }, 'Reset')) : null);
    const used = h('td', { class: 'used', 'data-label': 'Used by' }, e.where_used);
    const chips = h('div', { class: 'va-chips' });
    if (e.changed && e.kind !== 'scalar') chips.append(ui.chip('Changed', 'info'));
    if (e.status === 'PARTIAL') chips.append(ui.chip('Partly wired', 'warn'));
    if (e.status === 'MISSING') chips.append(ui.chip('Not in the model', 'neutral'));
    if (e.scope === 'all') chips.append(ui.chip('All scenarios', 'info'));
    if (e.scope === 'feed') chips.append(ui.chip('Applies when data loads', 'neutral'));
    if ((e.also_used_by || []).length) chips.append(ui.chip(`Also read by ${e.also_used_by.map((m) => PAGES[m] || m).join(', ')}`, 'neutral'));
    used.append(chips);
    if (e.notes) used.append(h('div', { class: 'va-hint' }, e.notes));
    if (e.kind !== 'scalar' || !e.editable_inline) {
      if (e.status !== 'MISSING' && e.page) used.append(h('div', { class: 'va-hint' }, h('a', { href: `/projection/${e.page}${slug ? '?scenario=' + encodeURIComponent(slug) : ''}`, 'data-pg': '' }, `Edit in ${PAGES[e.module] || e.module}`)));
    }
    const nameCell = h('td', { class: 'name', 'data-label': 'Variable' }, e.label, h('div', { class: 'va-hint', style: { fontWeight: 'var(--lv-fw-regular)' } }, e.id));
    tr.append(nameCell, cell, sheet, h('td', { class: 'unit', 'data-label': 'Unit' }, e.unit), used, h('td', { class: 'src', 'data-label': 'Source' }, e.sheet_ref));
    return row;
  }

  function applyUpdates(list) {
    for (const e of list || []) {
      const old = rows.get(e.id);
      if (!old) continue;
      const nu = buildRow(e);
      old.tr.replaceWith(nu.tr);
      rows.set(e.id, nu);
      const i = data.variables.findIndex((x) => x.id === e.id);
      if (i >= 0) data.variables[i] = e;
    }
    applyFilter();
  }

  // ------------------------------------------------------------------ sections
  const sectionsHost = h('div', { id: 'va-sections' });
  function buildSections() {
    sectionsHost.replaceChildren();
    sections.clear();
    rows.clear();
    for (const m of data.modules) {
      const list = data.variables.filter((v) => v.module === m.key && v.status !== 'MISSING');
      if (!list.length) continue;
      const det = h('details', { class: 'va-mod', id: `va-${m.key}`, open: true });
      const tbody = h('tbody');
      const changed = list.filter((v) => v.changed && v.kind === 'scalar').length;
      const head = h('summary', null,
        h('h2', null, m.label), h('span', { class: 'meta' }, `${plural(list.length, 'variable')}${changed ? `, ${changed} changed` : ''}`), h('span', { class: 'sp' }),
        h('a', { href: `/projection/${m.key.replace(/_/g, '-')}?scenario=${encodeURIComponent(slug)}`, 'data-pg': '' }, 'Open page'));
      const resetBtn = h('button', { type: 'button', class: 'va-btn', onclick: (ev) => { ev.preventDefault(); ev.stopPropagation(); resetModule(m, list); }, 'aria-label': `Reset every ${m.label} variable to the sheet value` }, 'Reset all to sheet');
      if (changed) head.insertBefore(resetBtn, head.querySelector('a'));
      const table = h('table', { class: 'va-table' },
        h('thead', null, h('tr', null, ['Variable', 'Value', 'Sheet value', 'Unit', 'Used by', 'Source'].map((t) => h('th', { scope: 'col' }, t)))), tbody);
      det.append(head, h('div', { class: 'va-scroll' }, table));
      sectionsHost.append(det);
      sections.set(m.key, { det, tbody, list });
      let group = null;
      for (const e of list) {
        if (e.group !== group) {
          group = e.group;
          tbody.append(h('tr', { class: 'grp' }, h('th', { colspan: 6, scope: 'colgroup' }, group)));
        }
        const row = buildRow(e);
        rows.set(e.id, row);
        tbody.append(row.tr);
      }
    }
    applyFilter();
  }

  async function resetModule(m, list) {
    const n = list.filter((v) => v.changed && v.editable_inline).length;
    if (!n) return;
    const ok = await confirmBox({ title: `Reset ${m.label} to the sheet?`, message: `${plural(n, 'variable')} in this scenario go back to the sheet's value. The change is saved in History, so it can be undone.`, confirmText: 'Reset to sheet', danger: false,
      onConfirm: async () => { const res = await ctx.api.post('variables/reset', { scenario: slug, module: m.key }); events.emit('saved', { scenario: slug, version: res.scenario.version }); } });
    if (ok) ctx.reload();
  }

  const missHost = h('div');
  function buildMissing() {
    const miss = data.variables.filter((v) => v.status === 'MISSING');
    if (!miss.length) { missHost.replaceChildren(); return; }
    const tbody = h('tbody');
    for (const m of data.modules) {
      const list = miss.filter((v) => v.module === m.key);
      if (!list.length) continue;
      tbody.append(h('tr', { class: 'grp' }, h('th', { colspan: 6, scope: 'colgroup' }, m.label)));
      for (const e of list) tbody.append(buildRow(e).tr);
    }
    const det = h('details', { class: 'va-mod', id: 'va-missing' },
      h('summary', null, h('h2', null, 'Sheet items the model does not carry'), h('span', { class: 'meta' }, `${miss.length} items; each says why and what would add it`)),
      h('div', { class: 'va-scroll' }, h('table', { class: 'va-table' }, h('thead', null, h('tr', null, ['Variable', 'Value', 'Sheet value', 'Unit', 'Used by', 'Source'].map((t) => h('th', { scope: 'col' }, t)))), tbody)));
    missHost.replaceChildren(det);
  }

  // ------------------------------------------------------------------ filter
  function applyFilter() {
    const q = state.q.trim().toLowerCase();
    let shown = 0;
    for (const { v, tr } of rows.values()) {
      const text = `${v.id} ${v.label} ${v.group} ${v.sheet_ref} ${v.where_used} ${v.notes || ''}`.toLowerCase();
      const hide = (q && !text.includes(q)) || (state.changedOnly && !v.changed);
      tr.hidden = hide;
      if (!hide) shown++;
    }
    for (const [key, s] of sections) {
      const vis = [...s.tbody.querySelectorAll('tr.row')].filter((r) => !r.hidden).length;
      for (const g of s.tbody.querySelectorAll('tr.grp')) {
        let n = g.nextElementSibling, any = false;
        while (n && !n.classList.contains('grp')) { if (!n.hidden) any = true; n = n.nextElementSibling; }
        g.hidden = !any;
      }
      s.det.hidden = (q || state.changedOnly) && !vis;
      if ((q || state.changedOnly) && vis) s.det.open = true;
    }
    empty.hidden = shown > 0 || !(q || state.changedOnly);
    ctx.setParams({ q: q || null, changed: state.changedOnly ? '1' : null });
  }

  // ------------------------------------------------------------------ header, TLDR, sensitivity
  const editable = () => data.variables.filter((v) => v.kind === 'scalar' && v.status !== 'MISSING');
  const nChanged = () => data.variables.filter((v) => v.changed && v.kind === 'scalar' && v.status !== 'MISSING').length;
  let header;
  function tldrNode() {
    const n = nChanged();
    const sens = state.sens;
    const top = sens && sens.rows ? sens.rows.slice(0, 5) : [];
    let line = n ? `${plural(n, 'variable')} ${n === 1 ? 'differs' : 'differ'} from the sheet.` : 'Every variable matches the sheet.';
    let kpis;
    if (top.length >= 3) {
      line += ` These ${top.length} move FY${String(sens.year).slice(2)} net operating income most.`;
      kpis = top.map((r) => ({ label: short(r), value: '±' + fmt.compact(r.noi_swing), note: (PAGES[r.module] || r.module).split(' ').slice(0, 3).join(' ') }));
    } else {
      if (!sens) line += ' Working out which ones matter most.';
      kpis = [
        { label: 'Changed from the sheet', value: String(n), tone: n ? 'warn' : 'good' },
        { label: 'Editable here', value: String(editable().filter((v) => v.editable_inline).length) },
        { label: 'Series and tables', value: String(data.variables.filter((v) => v.kind !== 'scalar' && v.status !== 'MISSING').length), note: 'edited on pages' },
        { label: 'Not in the model yet', value: String(data.variables.filter((v) => v.status === 'MISSING').length) },
      ];
    }
    return ui.tldr({ line, kpis });
  }
  function refreshChrome() {
    const t = header.querySelector('.pg-tldr');
    if (t) t.replaceWith(tldrNode());
    for (const [key, s] of sections) {
      const list = data.variables.filter((v) => v.module === key && v.status !== 'MISSING');
      const changed = list.filter((v) => v.changed && v.kind === 'scalar').length;
      const meta = s.det.querySelector('.meta');
      if (meta) meta.textContent = `${plural(list.length, 'variable')}${changed ? `, ${changed} changed` : ''}`;
    }
  }

  const sensHost = h('div');
  let sensTimer = null;
  let sensSeq = 0;
  function queueSensitivity() {
    state.sens = null;
    clearTimeout(sensTimer);
    sensTimer = setTimeout(loadSensitivity, 400);
    sensHost.querySelector('.va-sens-wrap')?.classList.add('stale');
  }
  async function loadSensitivity() {
    const seq = ++sensSeq;
    sensHost.replaceChildren(h('div', { class: 'va-sens-wait', role: 'status' }, 'Rerunning the model with each number moved by 10%'));
    try {
      const s = await ctx.api.get('variables/sensitivity');
      if (!ctx.alive() || seq !== sensSeq) return;
      state.sens = s;
      drawSensitivity(s, false);
      refreshChrome();
    } catch (err) {
      if (seq === sensSeq) sensHost.replaceChildren(h('p', { class: 'va-note' }, 'The comparison did not load: ' + (err.message || 'try again') + '. Your variables are unaffected.'));
    }
  }
  function drawSensitivity(s, all) {
    const list = all ? s.rows : s.rows.slice(0, 12);
    const max = Math.max(1, ...list.flatMap((r) => [r.down, r.up].filter(Boolean).map((d) => Math.abs(d.noi || 0))));
    const grid = h('div', { class: 'va-sens', role: 'table', 'aria-label': 'Effect of moving each variable by 10 percent' });
    const hd = (t, cls = '') => h('div', { class: 'h ' + cls, role: 'columnheader' }, t);
    grid.append(hd('Variable'), hd(`Change in FY${String(s.year).slice(2)} net operating income`), hd(`FY${String(s.year).slice(2)} revenue`, 'hide'), hd('Lowest cash', 'hide'));
    const dm = (d) => (d == null ? '' : (d >= 0 ? '+' : '-') + fmt.compact(Math.abs(d)).replace(/^-/, ''));
    for (const r of list) {
      const bars = h('div', { class: 'va-bars' });
      const mk = (d, dir) => {
        if (!d || !isNum(d.noi) || Math.abs(d.noi) < 0.5) return;
        const w = Math.max(1, Math.round((Math.abs(d.noi) / max) * 40));
        const left = d.noi < 0;
        bars.append(h('span', { class: `va-bar ${dir} ${left ? 'neg' : 'pos'}`, style: { width: w + '%' }, title: `Number moved ${dir === 'dn' ? 'down' : 'up'} 10%: ${dm(d.noi)} net operating income` }));
        bars.append(h('span', { class: 'va-bar-lab', style: left ? { right: `calc(50% + ${w}% + 4px)` } : { left: `calc(50% + ${w}% + 4px)` } }, dm(d.noi)));
      };
      mk(r.down, 'dn');
      mk(r.up, 'up');
      const rev = Math.max(...[r.down, r.up].filter(Boolean).map((d) => Math.abs(d.revenue || 0)), 0) * 1;
      const cash = Math.max(...[r.down, r.up].filter(Boolean).map((d) => Math.abs(d.cash || 0)), 0);
      grid.append(
        h('div', { class: 'c name', role: 'cell' }, short(r), h('small', null, `${PAGES[r.module] || r.module}, now ${fmtShort(r)}`), h('small', { class: 'txt' }, `Down 10%: ${dm(r.down && r.down.noi) || 'no change'}. Up 10%: ${dm(r.up && r.up.noi) || 'no change'}.`)),
        h('div', { class: 'c', role: 'cell' }, bars),
        h('div', { class: 'c r hide', role: 'cell' }, rev ? '±' + fmt.compact(rev) : ''),
        h('div', { class: 'c r hide', role: 'cell' }, cash ? '±' + fmt.compact(cash) : ''));
    }
    const more = s.rows.length > 12 ? h('button', { type: 'button', class: 'btn small', onclick: () => drawSensitivity(s, !all) }, all ? 'Show the top 12' : `Show all ${s.rows.length}`) : null;
    sensHost.replaceChildren(
      h('div', { class: 'va-sens-wrap' },
        h('div', { class: 'va-legend' }, h('span', null, h('i', { class: 'dn' }), 'Number moved down 10%'), h('span', null, h('i', { class: 'up' }), 'Number moved up 10%'), h('span', null, 'Bars to the left lower net operating income, to the right raise it'),
          h('span', null, `${s.moving} of ${s.tested} numbers move the plan${s.truncated ? '; the run stopped early' : ''}`)),
        grid, more ? h('div', { style: { marginTop: 'var(--lv-sp-2)' } }, more) : null));
  }
  const fmtShort = (r) => fmtValue(r.value, { value_type: r.value_type });

  // ------------------------------------------------------------------ page
  const search = h('input', { type: 'search', class: 'pg-input', placeholder: 'Search by name, sheet cell or what it drives', 'aria-label': 'Search variables', value: state.q });
  search.addEventListener('input', () => { state.q = search.value; applyFilter(); });
  const changedBox = h('input', { type: 'checkbox', checked: state.changedOnly });
  changedBox.addEventListener('change', () => { state.changedOnly = changedBox.checked; applyFilter(); });
  const empty = h('p', { class: 'va-note', role: 'status' }, 'Nothing matches. Clear the search or turn off "Changed only".');
  empty.hidden = true;

  const resetAll = h('button', { type: 'button', class: 'btn', onclick: async () => {
    const n = nChanged();
    if (!n) { toast('Every variable already matches the sheet', 'info'); return; }
    const ok = await confirmBox({ title: 'Reset every variable to the sheet?', message: `${plural(n, 'variable')} in this scenario go back to the sheet's value. Monthly series and tables are not touched. The change is saved in History, so it can be undone.`,
      confirmText: 'Reset to sheet', danger: false,
      onConfirm: async () => { const res = await ctx.api.post('variables/reset', { scenario: slug, all: true }); events.emit('saved', { scenario: slug, version: res.scenario.version }); } });
    if (ok) ctx.reload();
  } }, 'Reset all to sheet');

  header = ui.pageHeader({ title: 'Variables', purpose: 'Every number that drives the plan, next to the value in the Lonvera Projections sheet. Change one here and every page recalculates.',
    actions: [resetAll], tldr: { line: '' } });
  header.querySelector('.pg-tldr')?.replaceWith(tldrNode());

  const jump = h('nav', { class: 'va-jump', 'aria-label': 'Jump to a module' }, data.modules.map((m) => h('a', { href: `#va-${m.key}`, onclick: (ev) => { ev.preventDefault(); const d = document.getElementById(`va-${m.key}`); if (d) { d.open = true; d.scrollIntoView({ block: 'start' }); } } }, m.label)),
    h('a', { href: '#va-missing', onclick: (ev) => { ev.preventDefault(); const d = document.getElementById('va-missing'); if (d) { d.open = true; d.scrollIntoView({ block: 'start' }); } } }, 'Not in the model'));
  const scn = data.scenario;
  const controls = h('section', { class: 'pg-card', 'aria-label': 'Find a variable' },
    h('div', { class: 'va-controls' }, h('div', { class: 'grow' }, search), h('label', { class: 'check' }, changedBox, 'Changed only')),
    jump,
    h('p', { class: 'va-note' }, `You are editing ${scn.name} (version ${scn.version}). Changes save when you leave a cell and are kept in History. ${editable().filter((v) => v.editable_inline).length} numbers are edited here; ${data.variables.filter((v) => v.kind !== 'scalar' && v.status !== 'MISSING').length} series and tables are edited on their pages; ${data.variables.filter((v) => v.status === 'MISSING').length} sheet items are not in the model. ` +
      (scn.is_default ? 'This is the plan of record.' : 'This is a copy: the plan of record does not change.')));

  buildSections();
  buildMissing();
  root.replaceChildren(header, controls,
    h('section', { 'aria-labelledby': 'va-sens-h' }, h('h2', { id: 'va-sens-h', class: 'pg-h2' }, 'What matters most'),
      h('p', { class: 'va-note', style: { marginTop: 0, marginBottom: 'var(--lv-sp-2)' } }, 'Each number moved down and up by 10%, one at a time, with the whole plan recalculated. Bars show the change in net operating income.'), h('div', { class: 'pg-card' }, sensHost)),
    h('section', null, h('h2', { class: 'pg-h2' }, 'All variables'), empty, sectionsHost, h('div', { style: { marginTop: 'var(--lv-sp-3)' } }, missHost)),
    ui.source(`Sheet values from Lonvera Projections as of Nov 3, 2026. ${data.counts._total.IMPLEMENTED + data.counts._total.PARTIAL} variables in the model, ${data.counts._total.MISSING} sheet items it does not carry.`));

  loadSensitivity();
  return () => { clearTimeout(sensTimer); sensSeq += 1; };
}
