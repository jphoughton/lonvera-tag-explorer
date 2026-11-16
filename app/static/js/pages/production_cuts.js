// Creative Pipeline "By cut" view: production concepts counted by status next to the spend and return behind the same cuts, for a chosen date window (GET /api/priorities/cuts).
// A cut is any ordered subset of Product, Market cluster, ICP (ideal customer profile) and Pain point. Drag the chips between "Available" and "Group by", and left or right inside "Group by", to rebuild the table.
// Without a mouse: Enter or Space on a chip moves it between the two boxes, the left and right arrows reorder it inside Group by.
// Everything from the API goes in through textContent / h(). Class names and test ids start with `prod-` (never ad-, ads-, sponsor, banner, promo: ad blockers hide those).
import { h, fmt, isNum } from '../util.js';
import { api } from '../api.js';
import { today, addDays } from './production_common.js';

export const DIMS = [['product', 'Product'], ['cluster', 'Market cluster'], ['icp', 'ICP'], ['pain_point', 'Pain point']];
const DIM_LABEL = Object.fromEntries(DIMS);
const STAGE_FALLBACK = [['assigned', 'Assigned'], ['brief_sent', 'Brief sent'], ['in_production', 'In production'], ['in_review', 'In review'], ['ready', 'Ready to launch'], ['launched', 'Launched']];
const PRESETS = [['7', 'Last 7 days'], ['14', 'Last 14 days'], ['30', 'Last 30 days'], ['90', 'Last 90 days'], ['mtd', 'This month'], ['lastmonth', 'Last month']];
const METRICS = [
  ['spend', 'Spend', fmt.money, 'Meta spend on ads tagged to the cut'], ['nc_revenue', 'NC revenue', fmt.money, 'Upstack new-customer revenue (first orders only)'],
  ['nc_roas', 'NC ROAS', (v) => (isNum(v) ? fmt.roas(v) + 'x' : '–'), 'New-customer revenue / spend (Upstack)'], ['nc_cpa', 'NC CPA', fmt.money2, 'Spend per new customer (Upstack)'],
  ['meta_roas', 'Meta ROAS', (v) => (isNum(v) ? fmt.roas(v) + 'x' : '–'), 'Meta-reported revenue / spend; counts returning customers'], ['cpm', 'CPM', fmt.money2, 'Spend per 1,000 impressions'],
  ['ctr', 'CTR', (v) => (isNum(v) ? (v * 100).toFixed(2) + '%' : '–'), 'Link clicks / impressions'], ['cplc', 'Cost per link click', fmt.money2, 'Spend / link clicks'],
  ['conv_rate', 'Conv rate', (v) => (isNum(v) ? (v * 100).toFixed(2) + '%' : '–'), 'New customers / link clicks'], ['aov', 'AOV', fmt.money2, 'Average order value of new customers'],
];
const iso = (d) => (typeof d === 'string' ? d : d.toISOString().slice(0, 10));
const dateOf = (s) => new Date(s + 'T12:00:00Z');

export function presetRange(key, now = today()) {
  const yest = addDays(iso(now), -1);
  if (key === 'mtd') { const first = yest.slice(0, 8) + '01'; return { from: first, to: yest }; }
  if (key === 'lastmonth') {
    const d = dateOf(yest.slice(0, 8) + '01'); d.setUTCDate(0);
    const last = iso(d); return { from: last.slice(0, 8) + '01', to: last };
  }
  const n = Number(key) || 30;
  return { from: addDays(yest, -(n - 1)), to: yest };
}

// st: { by: ['product', ...], preset, from, to, pipelineOnly, stages: [status...], sort: {key, dir} }  (plain object kept by the page so it survives a redraw)
export function cutsView({ host, st, filters, signal, onState, say }) {
  let out = null;
  let seq = 0;
  const builder = h('div', { class: 'prod-cuts-builder', 'data-testid': 'prod-cuts-builder' });
  const when = h('div', { class: 'prod-cuts-when', 'data-testid': 'prod-cuts-when' });
  const tableHost = h('div', { class: 'prod-cuts-out', 'data-testid': 'prod-cuts-out' });
  const live = h('p', { class: 'pri-sub prod-cuts-live', role: 'status', 'data-testid': 'prod-cuts-live' });
  host.replaceChildren(builder, when, tableHost, live);

  const changed = () => { if (onState) onState(); };
  const rebuild = (by) => { st.by = by.length ? by : st.by; drawBuilder(); changed(); fetchNow(); };

  // ---- drag and drop of the cut chips
  function chip(key, zone) {
    const idx = st.by.indexOf(key);
    const el = h('button', { type: 'button', class: 'prod-cutchip', 'data-key': key, 'data-zone': zone, 'data-testid': 'prod-chip-' + key, draggable: 'false',
      title: zone === 'on' ? 'Drag left or right to reorder, or drag to Available to remove. Enter removes it.' : 'Drag into Group by to add it. Enter adds it.',
      'aria-label': zone === 'on' ? `${DIM_LABEL[key]}, grouping level ${idx + 1} of ${st.by.length}. Left and right arrows reorder, Enter removes.` : `${DIM_LABEL[key]}. Enter adds it to Group by.` },
      zone === 'on' ? h('span', { class: 'prod-cutchip-n', 'aria-hidden': 'true' }, String(idx + 1)) : null, h('span', { class: 'prod-grip', 'aria-hidden': 'true' }), DIM_LABEL[key]);
    el.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(key); return; }
      if (zone === 'on' && (e.key === 'ArrowLeft' || e.key === 'ArrowRight')) {
        e.preventDefault();
        const i = st.by.indexOf(key), j = i + (e.key === 'ArrowLeft' ? -1 : 1);
        if (j < 0 || j >= st.by.length) return;
        const by = st.by.slice(); [by[i], by[j]] = [by[j], by[i]];
        rebuild(by); builder.querySelector(`.prod-cutchip[data-key="${key}"]`)?.focus();
        live.textContent = `${DIM_LABEL[key]} is now level ${j + 1}.`;
      }
    });
    el.addEventListener('pointerdown', (e) => startDrag(e, el, key, zone));
    return el;
  }
  function toggle(key) {
    if (st.by.includes(key)) { if (st.by.length > 1) rebuild(st.by.filter((k) => k !== key)); else live.textContent = 'At least one cut must stay in Group by.'; }
    else rebuild([...st.by, key]);
    builder.querySelector(`.prod-cutchip[data-key="${key}"]`)?.focus();
  }
  function startDrag(e, el, key, zone) {
    if (e.button !== 0) return;
    const x0 = e.clientX, y0 = e.clientY;
    let ghost = null, moved = false;
    const onZone = () => builder.querySelector('[data-zone-box="on"]'), offZone = () => builder.querySelector('[data-zone-box="off"]');
    const hit = (x, y) => { for (const z of [onZone(), offZone()]) { const r = z.getBoundingClientRect(); if (x >= r.left && x <= r.right && y >= r.top - 8 && y <= r.bottom + 8) return z.dataset.zoneBox; } return ''; };
    const insertAt = (x) => { const chips = [...onZone().querySelectorAll('.prod-cutchip')].filter((c) => c !== el); let n = 0; for (const c of chips) { const r = c.getBoundingClientRect(); if (x > r.left + r.width / 2) n++; } return n; };
    const move = (ev) => {
      if (!moved && Math.hypot(ev.clientX - x0, ev.clientY - y0) < 6) return;
      if (!moved) {
        moved = true; el.classList.add('prod-cutchip-drag');
        ghost = el.cloneNode(true); ghost.classList.add('prod-cutchip-ghost'); ghost.removeAttribute('data-testid'); ghost.setAttribute('aria-hidden', 'true'); document.body.append(ghost);
      }
      ev.preventDefault();
      ghost.style.left = ev.clientX + 8 + 'px'; ghost.style.top = ev.clientY + 8 + 'px';
      const z = hit(ev.clientX, ev.clientY);
      onZone().classList.toggle('prod-over', z === 'on'); offZone().classList.toggle('prod-over', z === 'off');
      const mark = onZone().querySelectorAll('.prod-cutchip-drop'); mark.forEach((m) => m.remove());
      if (z === 'on') {
        const chips = [...onZone().querySelectorAll('.prod-cutchip')].filter((c) => c !== el), n = insertAt(ev.clientX);
        const bar = h('span', { class: 'prod-cutchip-drop', 'aria-hidden': 'true' });
        if (chips[n]) onZone().insertBefore(bar, chips[n]); else onZone().append(bar);
      }
    };
    const end = (ev, commit) => {
      document.removeEventListener('pointermove', move); document.removeEventListener('pointerup', up); document.removeEventListener('pointercancel', cancel);
      if (ghost) ghost.remove();
      onZone().classList.remove('prod-over'); offZone().classList.remove('prod-over');
      el.classList.remove('prod-cutchip-drag');
      if (!moved) return;                                                       // a plain click falls through to the keyboard path below
      if (el._suppress) el._suppress = false;
      if (!commit) { drawBuilder(); return; }
      const z = hit(ev.clientX, ev.clientY);
      const rest = st.by.filter((k) => k !== key);
      if (z === 'on') { const n = insertAt(ev.clientX); rest.splice(n, 0, key); rebuild(rest); live.textContent = `${DIM_LABEL[key]} is now level ${rest.indexOf(key) + 1} of ${rest.length}.`; }
      else if (z === 'off') { if (!rest.length) { live.textContent = 'At least one cut must stay in Group by.'; drawBuilder(); } else { rebuild(rest); live.textContent = `${DIM_LABEL[key]} removed from Group by.`; } }
      else drawBuilder();
    };
    const up = (ev) => end(ev, true), cancel = (ev) => end(ev, false);
    document.addEventListener('pointermove', move, { passive: false }); document.addEventListener('pointerup', up); document.addEventListener('pointercancel', cancel);
    el.addEventListener('click', (ev) => { if (moved) { ev.preventDefault(); ev.stopImmediatePropagation(); } }, { once: true, capture: true });
  }
  function drawBuilder() {
    const off = DIMS.map(([k]) => k).filter((k) => !st.by.includes(k));
    builder.replaceChildren(
      h('div', { class: 'prod-cuts-zonewrap' }, h('span', { class: 'prod-cuts-zonelabel', id: 'prod-cuts-on-l' }, 'Group by'),
        h('div', { class: 'prod-cuts-zone prod-cuts-on', 'data-zone-box': 'on', 'data-testid': 'prod-cuts-on', role: 'group', 'aria-labelledby': 'prod-cuts-on-l' }, st.by.map((k) => chip(k, 'on')))),
      h('div', { class: 'prod-cuts-zonewrap' }, h('span', { class: 'prod-cuts-zonelabel', id: 'prod-cuts-off-l' }, 'Available'),
        h('div', { class: 'prod-cuts-zone prod-cuts-off', 'data-zone-box': 'off', 'data-testid': 'prod-cuts-off', role: 'group', 'aria-labelledby': 'prod-cuts-off-l' },
          off.length ? off.map((k) => chip(k, 'off')) : h('span', { class: 'pri-sub' }, 'Every cut is in use'))),
      h('p', { class: 'pri-sub prod-cuts-hint' }, 'Drag a cut into Group by (or out of it) and left or right to change the order. Rows are grouped by the first cut, then the second, and so on.'));
  }

  // ---- dates
  function drawWhen() {
    const presetBtns = PRESETS.map(([k, t]) => h('button', { type: 'button', class: 'seg', 'aria-pressed': String(st.preset === k), 'data-testid': 'prod-cuts-preset-' + k, onclick: () => { Object.assign(st, { preset: k }, presetRange(k)); drawWhen(); changed(); fetchNow(); } }, t));
    const date = (name, key, tid) => h('label', { class: 'ctl prod-cuts-date' }, h('span', { class: 'ctl-label' }, name),
      h('input', { type: 'date', value: st[key], max: iso(addDays(iso(today()), -0)), 'data-testid': tid, onchange: (e) => { if (!e.target.value) return; st[key] = e.target.value; st.preset = ''; if (st.from > st.to) { if (key === 'from') st.to = st.from; else st.from = st.to; } drawWhen(); changed(); fetchNow(); } }));
    when.replaceChildren(h('div', { class: 'ctl' }, h('span', { class: 'ctl-label' }, 'Performance dates'), h('span', { class: 'segs', role: 'group', 'aria-label': 'Performance dates' }, presetBtns)), date('From', 'from', 'prod-cuts-from'), date('To', 'to', 'prod-cuts-to'),
      h('label', { class: 'check prod-cuts-only' }, h('input', { type: 'checkbox', checked: st.pipelineOnly, 'data-testid': 'prod-cuts-pipeline-only', onchange: (e) => { st.pipelineOnly = e.target.checked; changed(); drawTable(); } }), ' Only cuts with concepts in the pipeline'));
  }

  // ---- data and table
  async function fetchNow() {
    const mine = ++seq;
    tableHost.classList.add('prod-cuts-busy');
    try {
      const d = await api('/api/priorities/cuts', { group: st.by.join(','), from: st.from, to: st.to, week: filters().week, vendor: filters().vendor, product: filters().product }, { signal });
      if (mine !== seq) return;
      out = d; say('');
      drawTable();
    } catch (e) {
      if (e && e.name === 'AbortError') throw e;
      if (mine !== seq) return;
      say(e.message || 'Could not load the cuts.', true);
    } finally { if (mine === seq) tableHost.classList.remove('prod-cuts-busy'); }
  }
  const stageList = () => (out && out.stages ? out.stages.map((s) => [s.status, s.label]) : STAGE_FALLBACK);
  const val = (row, key) => (key.startsWith('label:') ? (row.labels[key.slice(6)] || '') : key.startsWith('stage:') ? row.stages[key.slice(6)] : key === 'concepts' ? row.concepts : row.stats[key]);
  function sorted(rows) {
    const { key, dir } = st.sort;
    if (!key) return rows;
    const m = dir === 'asc' ? 1 : -1;
    return rows.slice().sort((a, b) => {
      const x = val(a, key), y = val(b, key);
      if (typeof x === 'string' || typeof y === 'string') return m * String(x ?? '').localeCompare(String(y ?? ''));
      if (!isNum(x) && !isNum(y)) return 0;
      if (!isNum(x)) return 1;                                                 // empty numbers always last
      if (!isNum(y)) return -1;
      return m * (x - y);
    });
  }
  function drawTable() {
    if (!out) { tableHost.replaceChildren(); return; }
    const stages = stageList();
    const shown = stages.filter(([k]) => st.stages.includes(k));
    const rows = sorted(out.rows.filter((r) => !st.pipelineOnly || r.concepts > 0));
    const sortBtn = (key, text, title, right) => {
      const on = st.sort.key === key;
      return h('th', { scope: 'col', class: right ? 'prod-num' : null, title: title || null, 'aria-sort': on ? (st.sort.dir === 'asc' ? 'ascending' : 'descending') : 'none' },
        h('button', { type: 'button', class: 'prod-sortbtn', 'data-testid': 'prod-cuts-sort-' + key.replace(':', '-'), onclick: () => { st.sort = on ? { key, dir: st.sort.dir === 'desc' ? 'asc' : 'desc' } : { key, dir: typeof val(rows[0] || { labels: {}, stages: {}, stats: {} }, key) === 'string' ? 'asc' : 'desc' }; changed(); drawTable(); } }, text, on ? (st.sort.dir === 'asc' ? ' ▲' : ' ▼') : ''));
    };
    const heads = [...out.group.map((d) => sortBtn('label:' + d, DIM_LABEL[d])), ...shown.map(([k, t]) => sortBtn('stage:' + k, t, 'Concepts at this status (not dropped)', true)), sortBtn('concepts', 'Concepts', 'All concepts in the pipeline for this cut', true), ...METRICS.map(([k, t, , tip]) => sortBtn(k, t, tip, true))];
    const cells = (r, total) => [...out.group.map((d, i) => h('td', { class: 'prod-c prod-cut-label' + (r.labels[d] === '(none)' ? ' prod-cut-none' : '') }, total ? (i === 0 ? 'Total' : '') : r.labels[d])),
      ...shown.map(([k]) => h('td', { class: 'prod-c prod-num' + (r.stages[k] ? ' prod-cut-has' : ' prod-cut-zero'), 'data-stage': k }, r.stages[k] ? String(r.stages[k]) : '–')),
      h('td', { class: 'prod-c prod-num' }, r.concepts ? String(r.concepts) : '–'), ...METRICS.map(([k, , f]) => h('td', { class: 'prod-c prod-num', 'data-metric': k }, f(r.stats[k])))];
    const tot = { labels: {}, stages: out.totals.stages, concepts: out.totals.concepts, stats: out.totals.stats };
    tableHost.replaceChildren(
      h('div', { class: 'prod-cuts-stages', 'data-testid': 'prod-cuts-stages' }, h('span', { class: 'ctl-label' }, 'Status columns'),
        stages.map(([k, t]) => h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: st.stages.includes(k), 'data-testid': 'prod-cuts-stage-' + k, onchange: (e) => { st.stages = e.target.checked ? stages.map(([x]) => x).filter((x) => x === k || st.stages.includes(x)) : st.stages.filter((x) => x !== k); changed(); drawTable(); } }), ' ' + t))),
      rows.length
        ? h('div', { class: 'prod-scroll prod-cuts-scroll', 'data-testid': 'prod-cuts-scroll' }, h('table', { class: 'pri-table prod-table prod-cuts-table', 'data-testid': 'prod-cuts-table' },
          h('thead', null, h('tr', null, heads)), h('tbody', null, h('tr', { class: 'prod-cuts-total', 'data-testid': 'prod-cuts-total' }, cells(tot, true)), rows.map((r) => h('tr', { 'data-testid': 'prod-cuts-row' }, cells(r, false))))))
        : h('p', { 'data-testid': 'prod-cuts-empty' }, 'No cuts match these filters and dates.'),
      h('p', { class: 'pri-sub prod-cuts-basis', 'data-testid': 'prod-cuts-basis' }, `${out.from} to ${out.to}. ${out.basis} Status columns count production concepts (dropped ones left out) for the chosen week, vendor and product filters; they do not depend on the dates. A concept with no ICP or pain point yet shows under (none). `
        + (out.warning || '')));
  }

  drawBuilder();
  drawWhen();
  return { refresh: fetchNow, init: fetchNow };
}
