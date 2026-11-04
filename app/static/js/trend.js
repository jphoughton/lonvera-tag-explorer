// Trend panel (Chart.js), sparklines and the "pin rows into the chart" store.
import { h, enc, isNum, apiWindow, currentParams, navigate, cleanParams, qs, cmpView, onCmpRefresh, colPref, trendCollapsed, setTrendCollapsed, sparkMetric } from './util.js';
import { api, AuthError } from './api.js';
import { trendChart, fmtMetric, BAR_COLOR, LINE_COLOR } from './charts.js';

export const METRICS = [
  { key: 'spend', label: 'Spend', kind: 'money', additive: true },
  { key: 'purchases', label: 'Purchases', kind: 'int', additive: true },
  { key: 'revenue', label: 'Revenue', kind: 'money', additive: true },
  { key: 'roas', label: 'ROAS', kind: 'roas' },
  { key: 'cpa', label: 'CPA', kind: 'money' },
  { key: 'impressions', label: 'Impressions', kind: 'int', additive: true },
  { key: 'link_clicks', label: 'Link clicks', kind: 'int', additive: true },
  { key: 'ctr', label: 'CTR', kind: 'rate' },
  { key: 'cpc', label: 'CPC', kind: 'money2' },
  { key: 'cpm', label: 'CPM', kind: 'money2', advanced: true },
  { key: 'conv_rate', label: 'Conv rate', kind: 'rate', advanced: true },
];
const METRIC = Object.fromEntries(METRICS.map((m) => [m.key, m]));
export const metricInfo = (key) => METRIC[key] || METRIC.spend;

const PALETTE = ['#1a5fb4', '#d9822b', '#1d8a56', '#a64dc4', '#c2410c', '#0b7a75', '#b8860b', '#c0392b'];
const STAGE_COLOR = { testing: '#1b9a96', scaling: '#5a4fd6', unclassified: '#b4bcc8' };
const STAGE_NAME = { testing: 'Testing', scaling: 'Scaling', unclassified: 'Unclassified' };
const SVGNS = 'http://www.w3.org/2000/svg';
export const MAX_PINS = 8;

// ---------- sparklines ----------
export function sparkSvg(vals, { w = 70, h: hh = 20, title = '' } = {}) {
  const svg = document.createElementNS(SVGNS, 'svg');
  svg.setAttribute('class', 'spark');
  svg.setAttribute('width', String(w));
  svg.setAttribute('height', String(hh));
  svg.setAttribute('viewBox', `0 0 ${w} ${hh}`);
  svg.setAttribute('role', 'img');
  if (title) {
    svg.setAttribute('aria-label', title);
    const t = document.createElementNS(SVGNS, 'title');
    t.textContent = title;
    svg.append(t);
  }
  const nums = vals.filter(isNum);
  const pad = 2;
  if (!nums.length || nums.every((v) => v === 0)) {
    const l = document.createElementNS(SVGNS, 'line');
    l.setAttribute('x1', String(pad)); l.setAttribute('x2', String(w - pad));
    l.setAttribute('y1', String(hh - pad)); l.setAttribute('y2', String(hh - pad));
    l.setAttribute('class', 'spark-empty');
    svg.append(l);
    return svg;
  }
  let min = Math.min(...nums), max = Math.max(...nums);
  if (min === max) { min -= 1; max += 1; }
  const n = vals.length;
  const x = (i) => pad + (i * (w - 2 * pad)) / Math.max(1, n - 1);
  const y = (v) => hh - pad - ((v - min) / (max - min)) * (hh - 2 * pad);
  let d = '';
  let pen = false;
  let last = null;
  vals.forEach((v, i) => {
    if (!isNum(v)) { pen = false; return; }
    d += `${pen ? 'L' : 'M'}${x(i).toFixed(1)} ${y(v).toFixed(1)}`;
    pen = true;
    last = [x(i), y(v)];
  });
  const path = document.createElementNS(SVGNS, 'path');
  path.setAttribute('d', d);
  path.setAttribute('class', 'spark-line');
  svg.append(path);
  if (last) {
    const c = document.createElementNS(SVGNS, 'circle');
    c.setAttribute('cx', last[0].toFixed(1)); c.setAttribute('cy', last[1].toFixed(1)); c.setAttribute('r', '1.7');
    c.setAttribute('class', 'spark-dot');
    svg.append(c);
  }
  return svg;
}
export function sparkTitle(vals) {
  const m = metricInfo(sparkMetric(currentParams()));
  const nums = vals.filter(isNum);
  if (!nums.length) return `${m.label}, last ${vals.length} days: no data`;
  return `${m.label}, last ${vals.length} days: ${fmtMetric(m.kind, Math.min(...nums))} – ${fmtMetric(m.kind, Math.max(...nums))}`;
}

// ---------- pins: rows pinned into the chart ----------
export const PIN_HINT = 'Rows can be charted when Bars is None (the line then shows one row per line)';
export function createPins(initial = [], max = MAX_PINS) {
  const map = new Map((initial || []).map((x) => [x.key, x]));
  const listeners = [];
  let locked = false;
  const notify = () => {
    document.querySelectorAll('.pin-cb').forEach((cb) => {
      cb.checked = map.has(cb.dataset.key);
      cb.disabled = locked || (!cb.checked && map.size >= max);
      cb.title = locked ? PIN_HINT : (cb.disabled ? `Max ${max} rows in the chart` : 'Show in the trend chart');
    });
    listeners.forEach((f) => f());
  };
  return {
    max,
    locked: () => locked,
    setLocked(v) { locked = !!v; notify(); },
    has: (k) => map.has(k),
    size: () => map.size,
    full: () => map.size >= max,
    list: () => [...map.values()],
    toggle(key, label, scope) {
      if (map.has(key)) map.delete(key);
      else { if (map.size >= max) return false; map.set(key, { key, label, scope }); }
      notify();
      return true;
    },
    clear() { map.clear(); notify(); },
    onChange(fn) { listeners.push(fn); },
  };
}

// ---------- week / sum helpers (landing page aggregates per-product series on the client) ----------
const sumN = (pts, k) => pts.reduce((a, p) => a + (isNum(p[k]) ? p[k] : 0), 0);
export function combine(pts, t, partial = false) {
  const spend = sumN(pts, 'spend'), purchases = sumN(pts, 'purchases'), revenue = sumN(pts, 'revenue');
  const clicks = sumN(pts, 'link_clicks');
  const known = pts.filter((p) => isNum(p.impressions));
  const imp = sumN(known, 'impressions');
  const ctrNum = known.reduce((a, p) => a + (p.ctr || 0) * p.impressions, 0);
  const cpmNum = known.reduce((a, p) => a + (p.cpm || 0) * p.impressions, 0);
  const o = {
    t, spend, purchases, revenue,
    roas: spend ? revenue / spend : null, cpa: purchases ? spend / purchases : null,
    impressions: known.length ? imp : null, link_clicks: clicks,
    ctr: imp ? ctrNum / imp : null, cpc: clicks ? spend / clicks : null,
    cpm: imp ? cpmNum / imp : null, conv_rate: clicks ? purchases / clicks : null,
  };
  if (partial) o.partial = true;
  return o;
}
const mondayOf = (iso) => {
  const [y, m, d] = iso.split('-').map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d));
  dt.setUTCDate(dt.getUTCDate() - ((dt.getUTCDay() + 6) % 7));
  return dt.toISOString().slice(0, 10);
};
const daysInMonth = (iso) => { const [y, m] = iso.split('-').map(Number); return new Date(Date.UTC(y, m, 0)).getUTCDate(); };
// day points -> ISO weeks (Monday) or calendar months ('YYYY-MM-01'); buckets missing days of the window are partial
export function rebucket(points, grain) {
  const by = new Map();
  for (const pt of points) {
    if (!pt) continue;
    const k = grain === 'month' ? pt.t.slice(0, 8) + '01' : mondayOf(pt.t);
    if (!by.has(k)) by.set(k, []);
    by.get(k).push(pt);
  }
  return [...by.entries()].sort().map(([k, list]) => combine(list, k, list.length < (grain === 'month' ? daysInMonth(k) : 7)));
}
export const rebucketWeeks = (points) => rebucket(points, 'week');

// ---------- data source (normalised shape) ----------
// { windowInfo, compareInfo, labels:[t], points:[pt], cmp:[pt|null]|null, byStage:{stage:[pt]}|null, children:[{key,label,color,points}]|null }
function normalize(d) {
  return {
    windowInfo: d.window_info || null,
    compareInfo: d.compare_info || null,
    points: d.points || [],
    cmp: d.cmp_points || null,
    byStage: d.by_stage || null,
    children: d.children || null,
    grain: d.grain || 'day',
  };
}

function scopeParams(scope) {
  const o = {};
  for (const k of ['cluster', 'icp', 'pain', 'family_key', 'campaign_id']) if (scope && scope[k]) o[k] = scope[k];
  return o;
}

// ---------- the panel ----------
const ADDITIVE = new Set(METRICS.filter((m) => m.additive).map((m) => m.key));
const SPLIT_HINT = 'Split by Stage applies to additive Bars metrics (Spend, Purchases, Revenue, Impressions, Link clicks).';

/*
 Dual-axis trend panel: Bars (left axis) + Line (right axis), each any metric or None. Defaults Bars = Spend, Line = ROAS.
 opts: { slug, p, scope, scopeLabel, pins, mode: 'scope'|'ad'|'landing', products (landing), signal,
         shadeInfo: {a, b} (ad mode), onSparks(slug -> [values]) (landing) }
*/
export function createTrendPanel(opts) {
  const { slug, p, scope = {}, pins, mode = 'scope', signal } = opts;
  const valid = (v, d) => (v === 'none' || METRIC[v] ? v : d);
  const state = {
    bars: valid(p.tb, 'spend'),
    line: valid(p.tl, 'roas'),
    grain: ['week', 'month'].includes(p.tg) ? p.tg : 'day',
    split: p.ts === 'stage' ? 'stage' : 'none',
    overlay: true,
    collapsed: trendCollapsed(),
  };
  let chart = null;
  let abort = null;
  let seq = 0;
  let last = null;
  const dayCache = new Map(); // landing: slug -> day-grain response
  const barsAdditive = () => state.bars !== 'none' && ADDITIVE.has(state.bars);
  const effSplit = () => (barsAdditive() && state.split === 'stage' ? 'stage' : 'none');

  // ----- DOM -----
  const optionsFor = (sel, current) => {
    sel.replaceChildren(...METRICS.filter((m) => !m.advanced || colPref('advanced') || current === m.key).map((m) => h('option', { value: m.key }, m.label)), h('option', { value: 'none' }, 'None'));
    sel.value = current;
  };
  const barsSel = h('select', { id: 'trend-bars', 'aria-label': 'Bars metric (left axis)' });
  const lineSel = h('select', { id: 'trend-line', 'aria-label': 'Line metric (right axis)' });
  optionsFor(barsSel, state.bars);
  optionsFor(lineSel, state.line);
  barsSel.addEventListener('change', () => pageNavigate({ tb: barsSel.value })); // bars also drive the sparkline column: re-render the page
  lineSel.addEventListener('change', () => {
    if (state.bars === 'none') { pageNavigate({ tl: lineSel.value }); return; } // sparklines follow the line while Bars = None: re-render
    state.line = lineSel.value;
    writeUrl('tl', state.line, 'roas');
    if (last) paint(last); else load();
  });
  const swapBtn = h('button', { type: 'button', class: 'btn small', id: 'trend-swap', title: 'Swap Bars and Line' }, 'Swap');
  swapBtn.addEventListener('click', () => pageNavigate({ tb: state.line, tl: state.bars }));
  function pageNavigate(changes) {
    const q = cleanParams({ ...currentParams(), ...changes });
    navigate(location.pathname + qs(q), { replace: true, scroll: false });
  }
  const seg = (items, cur, attr, onPick) => items.map(([v, t]) => {
    const b = h('button', { type: 'button', class: 'seg', [attr]: v, 'aria-pressed': String(cur === v) }, t);
    b.addEventListener('click', () => onPick(v));
    return b;
  });
  const grainBtns = seg([['day', 'Day'], ['week', 'Week'], ['month', 'Month']], state.grain, 'data-grain', (v) => setState({ grain: v }, 'tg'));
  const splitBtns = seg([['none', 'None'], ['stage', 'Stage']], state.split, 'data-split', (v) => setState({ split: v }, 'ts'));
  const splitHint = h('span', { class: 'muted small', id: 'trend-split-hint' });
  const overlayCb = h('input', { type: 'checkbox', id: 'trend-overlay', checked: true });
  overlayCb.addEventListener('change', () => { state.overlay = overlayCb.checked; if (last) paint(last); });
  const overlayWrap = h('label', { class: 'check', id: 'trend-overlay-wrap', hidden: true }, overlayCb, ' Compare overlay');
  const pinInfo = h('span', { class: 'trend-pins', id: 'trend-pins', hidden: true });
  const pinHint = h('span', { class: 'muted small', id: 'trend-pin-hint', hidden: true });
  const toggleBtn = h('button', { type: 'button', class: 'btn small', id: 'trend-toggle', 'aria-expanded': String(!state.collapsed), onclick: () => setCollapsed(!state.collapsed) }, state.collapsed ? 'Show' : 'Hide');
  const canvas = h('canvas', { id: 'trend-chart', role: 'img', 'aria-label': 'Trend chart' });
  const note = h('div', { class: 'trend-note', id: 'trend-note' });
  const status = h('span', { class: 'muted small', id: 'trend-status' });
  const body = h('div', { class: 'trend-body', hidden: state.collapsed }, h('div', { class: 'chartbox trendbox' }, canvas), note);
  const el = h('section', { class: 'trend', id: 'trend-panel', 'data-collapsed': String(state.collapsed), 'data-mode': mode },
    h('div', { class: 'trend-head' },
      h('h2', 'Trend'),
      h('div', { class: 'trend-controls', hidden: state.collapsed },
        h('label', { class: 'ctl tsel bars' }, h('span', { class: 'ctl-name' }, 'Bars'), barsSel),
        h('label', { class: 'ctl tsel line' }, h('span', { class: 'ctl-name' }, 'Line'), lineSel),
        swapBtn,
        h('span', { class: 'ctl' }, h('span', { class: 'ctl-name' }, 'Grain'), h('span', { class: 'segs', id: 'trend-grain' }, grainBtns)),
        h('span', { class: 'ctl' }, h('span', { class: 'ctl-name' }, 'Split bars by'), h('span', { class: 'segs', id: 'trend-split' }, splitBtns), splitHint),
        overlayWrap, pinInfo, pinHint, status),
      h('span', { class: 'spacer' }),
      toggleBtn),
    body);
  const controlsEl = el.querySelector('.trend-controls');

  function syncSplitControl() {
    const ok = barsAdditive();
    splitBtns.forEach((b) => { b.disabled = !ok; b.setAttribute('aria-pressed', String(ok && b.dataset.split === state.split)); });
    splitHint.textContent = ok ? '' : (state.bars === 'none' ? 'Split applies to the bars; Bars is None.' : 'Not available for ratio bars.');
    splitHint.title = SPLIT_HINT;
    el.querySelector('#trend-split').title = ok ? '' : SPLIT_HINT;
  }
  function syncPinLock() {
    if (!pins) return;
    const locked = state.bars !== 'none';
    pins.setLocked(locked);
    pinHint.hidden = !locked;
    pinHint.textContent = locked ? 'To chart rows, set Bars to None.' : '';
    pinHint.title = PIN_HINT;
  }
  syncSplitControl();
  syncPinLock();

  // ----- state -----
  function writeUrl(key, value, dflt) {
    const u = new URL(location.href);
    if (value === dflt) u.searchParams.delete(key); else u.searchParams.set(key, value);
    navigate(u.pathname + u.search, { replace: true, render: false });
  }
  function setState(patch, urlKey) {
    Object.assign(state, patch);
    if (urlKey === 'tg') { writeUrl('tg', state.grain, 'day'); grainBtns.forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.grain === state.grain))); }
    if (urlKey === 'ts') { writeUrl('ts', state.split, 'none'); syncSplitControl(); }
    load();
  }
  function setCollapsed(on) {
    state.collapsed = on;
    setTrendCollapsed(on);
    toggleBtn.textContent = on ? 'Show' : 'Hide';
    toggleBtn.setAttribute('aria-expanded', String(!on));
    el.dataset.collapsed = String(on);
    body.hidden = on;
    controlsEl.hidden = on;
    if (on) { if (abort) abort.abort(); seq++; if (chart) { chart.destroy(); chart = null; } } else load();
  }

  // ----- data -----
  const rowsMode = () => !!(pins && pins.size() > 0 && state.bars === 'none');
  function params(sc, { grain, split, windowed }) {
    const o = { ...scopeParams(sc), grain };
    if (split === 'stage') o.split = 'stage';
    if (windowed) Object.assign(o, apiWindow(p));
    else { const a = apiWindow(p); if (a.stage) o.stage = a.stage; }
    return o;
  }
  const trendUrl = (sl) => `/api/products/${enc(sl)}/trend`;

  async function fetchScope(sig) {
    if (rowsMode()) {
      const list = pins.list();
      const results = await Promise.all(list.map((pn) => api(trendUrl(slug), params({ ...pn.scope }, { grain: state.grain, split: 'none', windowed: mode !== 'ad' }), { signal: sig })));
      const norm = results.map(normalize);
      return { ...norm[0], pinned: list.map((pn, i) => ({ key: pn.key, label: pn.label, points: norm[i].points })), cmp: null, byStage: null, children: null };
    }
    const d = await api(trendUrl(slug), params(scope, { grain: state.grain, split: effSplit(), windowed: mode !== 'ad' }), { signal: sig });
    return normalize(d);
  }

  async function fetchLanding(sig) {
    const prods = opts.products || [];
    const need = prods.filter((pr) => !dayCache.has(pr.slug));
    const got = await Promise.all(need.map((pr) => api(trendUrl(pr.slug), { grain: 'day', split: 'stage', ...apiWindow(p) }, { signal: sig })));
    need.forEach((pr, i) => dayCache.set(pr.slug, normalize(got[i])));
    const wk = (pts) => (state.grain === 'day' ? pts : rebucket(pts, state.grain));
    const datas = prods.map((pr) => dayCache.get(pr.slug));
    const sumAt = (lists) => {
      const n = Math.max(...lists.map((l) => l.length), 0);
      const out = [];
      for (let i = 0; i < n; i++) {
        const col = lists.map((l) => l[i]).filter(Boolean);
        out.push(col.length ? combine(col, col[0].t) : null);
      }
      return out;
    };
    const total = wk(sumAt(datas.map((d) => d.points)));
    const cmpLists = datas.map((d) => d.cmp).filter(Boolean);
    const cmp = cmpLists.length ? wk(sumAt(cmpLists).filter((x) => x)) : null;
    const stages = {};
    for (const st of ['testing', 'scaling', 'unclassified']) {
      const lists = datas.map((d) => d.byStage && d.byStage[st]).filter(Boolean);
      if (lists.length) stages[st] = wk(sumAt(lists));
    }
    const res = {
      windowInfo: datas[0] && datas[0].windowInfo, compareInfo: datas[0] && datas[0].compareInfo,
      points: total, cmp, byStage: Object.keys(stages).length ? stages : null, children: null, grain: state.grain,
    };
    if (rowsMode()) {
      res.pinned = prods.filter((pr) => pins.has(pr.slug)).map((pr) => ({ key: pr.slug, label: pr.name, points: wk(dayCache.get(pr.slug).points) }));
    }
    if (opts.onSparks) {
      const key = sparkMetric(currentParams());
      const sp = {};
      for (const pr of prods) sp[pr.slug] = dayCache.get(pr.slug).points.slice(-30).map((pt) => (pt ? pt[key] : null));
      opts.onSparks(sp);
    }
    return res;
  }

  async function load() {
    if (state.collapsed) return;
    if (abort) abort.abort();
    abort = new AbortController();
    const my = ++seq;
    if (signal) signal.addEventListener('abort', () => abort && abort.abort(), { once: true });
    status.textContent = 'Loading…';
    try {
      const res = mode === 'landing' ? await fetchLanding(abort.signal) : await fetchScope(abort.signal);
      if (my !== seq) return;
      last = res;
      status.textContent = '';
      paint(res);
    } catch (e) {
      if (e instanceof AuthError || (e && e.name === 'AbortError') || my !== seq) return;
      status.textContent = '';
      note.textContent = 'Trend failed to load: ' + (e && e.message ? e.message : e);
      if (chart) { chart.destroy(); chart = null; }
    }
  }

  // ----- chart -----
  function paint(res) {
    const bm = state.bars !== 'none' ? metricInfo(state.bars) : null;
    const lm = state.line !== 'none' ? metricInfo(state.line) : null;
    const pts = res.points || [];
    const rows = rowsMode() && res.pinned && res.pinned.length;
    const base = rows ? res.pinned[0].points : pts;
    const labels = base.map((pt) => (pt ? pt.t : null));
    for (let i = 0; i < labels.length; i++) if (labels[i] == null) labels[i] = pts[i] ? pts[i].t : labels[i - 1] || '';
    const val = (list, m) => list.map((pt) => (pt && isNum(pt[m.key]) ? pt[m.key] : null));
    const partial = base.map((pt) => !!(pt && pt.partial));
    const datasets = [];
    const notes = [];
    const split = effSplit();
    const stacked = !!bm && split === 'stage' && !!res.byStage;
    const hasData = (list) => list.some((pt) => pt && ((pt.spend || 0) > 0 || (pt.purchases || 0) > 0 || (pt.link_clicks || 0) > 0));
    const order = ['testing', 'scaling', 'unclassified'].filter((s) => res.byStage && res.byStage[s] && (s !== 'unclassified' || hasData(res.byStage[s])));
    if (rows) {
      // pinned rows: one line per row on the right axis (Bars = None)
      if (lm) res.pinned.forEach((pn, i) => datasets.push({ axis: 'right', type: 'line', kind: lm.kind, label: pn.label, data: val(pn.points, lm), color: PALETTE[i % PALETTE.length], width: 2, partialDash: true }));
      notes.push(`Showing ${res.pinned.length} pinned row${res.pinned.length === 1 ? '' : 's'} on the line axis (max ${MAX_PINS}).`);
    } else {
      if (bm) {
        if (stacked) {
          order.forEach((st) => datasets.push({ axis: 'left', type: 'bar', kind: bm.kind, label: STAGE_NAME[st], data: val(res.byStage[st], bm), color: STAGE_COLOR[st], stack: 'stage', opaque: true }));
        } else {
          datasets.push({ axis: 'left', type: 'bar', kind: bm.kind, label: bm.label, data: val(pts, bm), color: BAR_COLOR });
        }
      }
      if (lm) {
        if (split === 'stage' && !lm.additive && res.byStage) {
          order.forEach((st) => datasets.push({ axis: 'right', type: 'line', kind: lm.kind, label: `${lm.label} · ${STAGE_NAME[st]}`, data: val(res.byStage[st], lm), color: STAGE_COLOR[st], width: 2, partialDash: true }));
        } else {
          datasets.push({ axis: 'right', type: 'line', kind: lm.kind, label: lm.label, data: val(pts, lm), color: LINE_COLOR, width: 2.4, partialDash: true });
        }
      }
    }
    if (!bm && !lm) notes.push('Pick a metric for Bars or Line.');
    // compare overlay: B as dashed outlined bars + a dashed gray line
    const showOverlay = cmpView.on && state.overlay && res.cmp && !rows && mode !== 'ad';
    overlayWrap.hidden = !(cmpView.on && mode !== 'ad');
    if (showOverlay) {
      const ci = res.compareInfo;
      const tag = ' · ' + (ci && ci.label ? ci.label : 'window B');
      const cmpDates = res.cmp.map((pt) => (pt ? pt.t : null));
      if (bm) datasets.push({ axis: 'left', type: 'bar', kind: bm.kind, label: `B ${bm.label}${tag}`, data: val(res.cmp, bm), color: '#6b7684', outline: true, dash: [3, 3], order: 3, cmpDates });
      if (lm) datasets.push({ axis: 'right', type: 'line', kind: lm.kind, label: `B ${lm.label}${tag}`, data: val(res.cmp, lm), color: '#8b949e', dash: [5, 4], width: 1.6, order: 0, cmpDates });
    }
    // window A / B shading (ad detail: the chart always shows the full series)
    const shades = [];
    if (mode === 'ad' && opts.shadeInfo) {
      const { a, b } = opts.shadeInfo;
      if (a && a.from && a.to) shades.push({ from: a.from, to: a.to, color: 'rgba(26,95,180,0.14)' });
      if (b && b.from && b.to && cmpView.on) shades.push({ from: b.from, to: b.to, color: 'rgba(217,130,43,0.20)' });
    }
    if (chart) chart.destroy();
    chart = trendChart(canvas, {
      labels, grain: res.grain || state.grain, partial, shades, stackedBars: stacked,
      left: bm && !rows ? { kind: bm.kind, title: `${bm.label} (bars, left)` } : null,
      right: lm ? { kind: lm.kind, title: `${lm.label} (line, right)` } : null,
      datasets,
    });
    const wi = res.windowInfo;
    if (wi && wi.note && mode !== 'ad') notes.push(wi.note);
    if (mode === 'ad' && shades.length) notes.push(shades.length > 1 ? 'Shaded: window A (blue) and window B (orange).' : 'Shaded: selected window.');
    note.replaceChildren(...notes.map((t) => h('span', { class: 'note info' }, t)));
    if (pins && pins.size() > 0) {
      pinInfo.hidden = false;
      pinInfo.replaceChildren(`Pinned ${pins.size()}/${MAX_PINS} `, h('button', { type: 'button', class: 'btn small', id: 'trend-clear', onclick: () => pins.clear() }, 'Clear'));
    } else { pinInfo.hidden = true; }
  }

  // advanced metrics appear with the Advanced columns chip; pins re-draw the chart
  onCmpRefresh(() => {
    optionsFor(barsSel, state.bars);
    optionsFor(lineSel, state.line);
    overlayWrap.hidden = !(cmpView.on && mode !== 'ad');
  });
  if (pins) pins.onChange(() => { if (!state.collapsed && state.bars === 'none') load(); else if (!state.collapsed) pinInfoOnly(); });
  function pinInfoOnly() {
    if (pins.size() > 0) {
      pinInfo.hidden = false;
      pinInfo.replaceChildren(`Pinned ${pins.size()}/${MAX_PINS} `, h('button', { type: 'button', class: 'btn small', id: 'trend-clear', onclick: () => pins.clear() }, 'Clear'));
    } else pinInfo.hidden = true;
  }

  return {
    el,
    state,
    load,
    // landing page: the Trend column needs the per-product series even while the panel is collapsed
    async sparksOnly() {
      if (mode !== 'landing') return;
      try { await fetchLanding(signal); } catch { /* sparkline cells stay empty */ }
    },
    destroy() { if (abort) abort.abort(); seq++; if (chart) { chart.destroy(); chart = null; } },
  };
}
