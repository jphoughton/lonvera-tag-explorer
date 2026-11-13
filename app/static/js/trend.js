// Trend panel (Chart.js), sparklines and the "pin rows into the chart" store.
import { h, enc, fmt, isNum, apiWindow, currentParams, navigate, cleanParams, qs, cmpView, onCmpRefresh, colPref, trendCollapsed, setTrendCollapsed, sparkMetric, metricLabel } from './util.js';
import { api, AuthError } from './api.js';
import { trendChart, fmtMetric, BAR_COLOR, LINE_COLOR, tok } from './charts.js';

export const METRICS = [
  { key: 'spend', label: 'Spend', kind: 'money', additive: true },
  { key: 'purchases', get label() { return metricLabel('purchases', 'Purchases'); }, kind: 'int', additive: true },
  { key: 'revenue', get label() { return metricLabel('revenue', 'Revenue'); }, kind: 'money', additive: true },
  { key: 'roas', get label() { return metricLabel('roas', 'ROAS'); }, kind: 'roas' },
  { key: 'cpa', get label() { return metricLabel('cpa', 'CPA'); }, kind: 'money' },
  { key: 'impressions', label: 'Impressions', kind: 'int', additive: true },
  { key: 'link_clicks', label: 'Link clicks', kind: 'int', additive: true },
  { key: 'ctr', label: 'CTR', kind: 'rate' },
  { key: 'cpc', label: 'CPC', kind: 'money2' },
  { key: 'cpm', label: 'CPM', kind: 'money2', advanced: true },
  { key: 'conv_rate', label: 'Conv rate', kind: 'rate', advanced: true },
];
const METRIC = Object.fromEntries(METRICS.map((m) => [m.key, m]));
// chart-only line on the All views: the business's actual new-customer revenue / total spend (Meta + Google), drawn from the server's `total` series
export const NC_TOTAL = { key: 'nc_roas_total', label: 'NC ROAS (total)', kind: 'roas', virtual: true };
METRIC[NC_TOTAL.key] = NC_TOTAL;
const totalColor = () => tok('--lv-ink');
export const metricInfo = (key) => METRIC[key] || METRIC.spend;

const PALETTE = ['#1a5fb4', '#E8573A', '#1d8a56', '#a64dc4', '#E8A400', '#0b7a75', '#c2185b', '#c0392b'];
const STAGE_COLOR = { testing: '#1b9a96', scaling: '#5a4fd6', get unclassified() { return tok('--lv-line-strong'); } };
const STAGE_NAME = { testing: 'Testing', scaling: 'Scaling', unclassified: 'Unclassified' };
const SVGNS = 'http://www.w3.org/2000/svg';
export const MAX_PINS = 8;

// the trend source of the All pages: total spend bars (Split bars by Platform stacks them), Total NC ROAS as the line, each platform's own ratio as a Line choice. Bars are limited to what may be added across platforms
// (spend, impressions, clicks). `basis` adds the chart's own Numbers choice (the overview shows all three bases in its table, so the chart needs one).
export function platformTrendSource(p, wp, filt = {}, { basis = false } = {}) {
  const unfiltered = !filt.only && !filt.product && !filt.type;
  return {
    splits: [['none', 'Total'], ['platform', 'Platform']], defaultSplit: unfiltered ? 'none' : 'platform', alwaysSplit: true, barsMetrics: ['spend', 'impressions', 'link_clicks'],
    total: true, totalLine: unfiltered,                    // total spend in the tooltip and over the bars; 'NC ROAS (total)' is a Line choice (and the default) when no filter hides it
    basis: basis ? { param: 'nb', options: [['own', 'Own reporting'], ['nc', 'Upstack · new customers'], ['all', 'Upstack · all orders']], default: 'own' } : null,
    fetch: ({ grain, basis: nb, signal }) => api('/api/platforms/trend', { ...wp, grain, ...(basis ? { numbers: nb } : {}), ...filt }, { signal }),
  };
}

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
    groups: d.groups || null,                       // generic split: [{key, label, color, points}] (platform pages)
    labels: d.labels || null,                       // explicit x axis (platform pages: platforms whose series start on different days)
    total: d.total || null,                         // All views: total spend and total NC ROAS per bucket, on the same x axis
    notes: d.notes || [],
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
 opts: { slug, p, scope, scopeLabel, pins, mode: 'scope'|'ad'|'landing'|'source', products (landing), signal,
         shadeInfo: {a, b} (ad mode), onSparks(slug -> [values]) (landing),
         source (mode 'source': the platform pages, no product behind them) = {
           fetch({grain, split, basis, signal}) -> API response ({points, groups?, labels?, cmp_points?, notes?, window_info, compare_info}),
           splits: [[key, label]] (first = default unless defaultSplit), defaultSplit,
           barsMetrics: [metric keys] limits the Bars choices (metrics that may be added across the groups),
           alwaysSplit: the server always returns the groups (platform pages): ratio lines are drawn per group even when Bars is None or not split (Split bars by None = the combined total),
           total: the response carries `total` (total spend + total NC ROAS per bucket): shown in the tooltip, and as totals above the stacked bars,
           totalLine: offer 'NC ROAS (total)' as a Line choice and make it the default (off when a filter hides the ratio),
           basis: {param, options: [[key, label]], default} an extra Numbers choice kept in the URL } }
*/
export function createTrendPanel(opts) {
  const { slug, p, scope = {}, pins, mode = 'scope', signal } = opts;
  // `local` = a host page without the Marketing router (Projections): control changes stay in the panel, nothing is written to the URL,
  // and the Hide / Show choice is kept in its own store (opts.collapseStore {get, set}) instead of the Marketing pages' one.
  const local = !!opts.local;
  const cstore = opts.collapseStore || { get: trendCollapsed, set: setTrendCollapsed };
  const src = mode === 'source' ? opts.source : null;
  const splitItems = src ? src.splits : [['none', 'None'], ['stage', 'Stage']];
  const barsOnly = src && src.barsMetrics ? src.barsMetrics : null;
  const totalLine = !!(src && src.totalLine);
  const defaultLine = totalLine ? NC_TOTAL.key : 'roas';
  const validBars = (v, d) => (v === 'none' || (METRIC[v] && !METRIC[v].virtual) ? v : d);
  const validLine = (v, d) => (v === 'none' || (METRIC[v] && (!METRIC[v].virtual || totalLine)) ? v : d);
  const state = {
    bars: validBars(p.tb, 'spend'),
    line: validLine(p.tl, defaultLine),
    grain: ['week', 'month'].includes(p.tg) ? p.tg : 'day',
    split: splitItems.some(([k]) => k === p.ts) ? p.ts : (src ? (src.defaultSplit || splitItems[0][0]) : 'none'),
    basis: src && src.basis ? (src.basis.options.some(([k]) => k === p[src.basis.param]) ? p[src.basis.param] : src.basis.default) : null,
    overlay: true,
    collapsed: cstore.get(),
  };
  if (barsOnly && state.bars !== 'none' && !barsOnly.includes(state.bars)) state.bars = barsOnly[0];
  const defaultSplit = src ? (src.defaultSplit || splitItems[0][0]) : 'none';
  let chart = null;
  let abort = null;
  let seq = 0;
  let last = null;
  const dayCache = new Map(); // landing: slug -> day-grain response
  const barsAdditive = () => state.bars !== 'none' && ADDITIVE.has(state.bars);
  const effSplit = () => (barsAdditive() && state.split !== 'none' ? state.split : 'none');

  // ----- DOM -----
  const optionsFor = (sel, current, only = null, extra = []) => {
    sel.replaceChildren(...extra.map((m) => h('option', { value: m.key }, m.label)), ...METRICS.filter((m) => (!m.advanced || colPref('advanced') || current === m.key) && (!only || only.includes(m.key))).map((m) => h('option', { value: m.key }, m.label)), h('option', { value: 'none' }, 'None'));
    sel.value = current;
  };
  const barsSel = h('select', { id: 'trend-bars', 'aria-label': 'Bars metric (left axis)' });
  const lineSel = h('select', { id: 'trend-line', 'aria-label': 'Line metric (right axis)' });
  optionsFor(barsSel, state.bars, barsOnly);
  const lineExtra = totalLine ? [NC_TOTAL] : [];
  optionsFor(lineSel, state.line, null, lineExtra);
  barsSel.addEventListener('change', () => pageNavigate({ tb: barsSel.value })); // bars also drive the sparkline column: re-render the page
  lineSel.addEventListener('change', () => {
    if (state.bars === 'none') { pageNavigate({ tl: lineSel.value }); return; } // sparklines follow the line while Bars = None: re-render
    state.line = lineSel.value;
    writeUrl('tl', state.line, defaultLine);
    syncSwap();
    if (last) paint(last); else load();
  });
  const swapBtn = h('button', { type: 'button', class: 'btn small', id: 'trend-swap', title: 'Swap Bars and Line' }, 'Swap');
  swapBtn.addEventListener('click', () => pageNavigate({ tb: state.line, tl: state.bars }));
  function syncSwap() {
    const v = !!(METRIC[state.line] && METRIC[state.line].virtual);
    swapBtn.disabled = v;
    swapBtn.title = v ? 'NC ROAS (total) is a line only: it cannot be swapped into the bars' : 'Swap Bars and Line';
  }
  function pageNavigate(changes) {
    if (local) {
      if ('tb' in changes) state.bars = validBars(changes.tb, state.bars);
      if ('tl' in changes) state.line = validLine(changes.tl, state.line);
      if (barsOnly && state.bars !== 'none' && !barsOnly.includes(state.bars)) state.bars = barsOnly[0];
      optionsFor(barsSel, state.bars, barsOnly);
      optionsFor(lineSel, state.line, null, lineExtra);
      syncSplitControl(); syncSwap(); syncPinLock();
      load();
      return;
    }
    const q = cleanParams({ ...currentParams(), ...changes });
    navigate(location.pathname + qs(q), { replace: true, scroll: false });
  }
  const seg = (items, cur, attr, onPick) => items.map(([v, t]) => {
    const b = h('button', { type: 'button', class: 'seg', [attr]: v, 'aria-pressed': String(cur === v) }, t);
    b.addEventListener('click', () => onPick(v));
    return b;
  });
  const grainBtns = seg([['day', 'Day'], ['week', 'Week'], ['month', 'Month']], state.grain, 'data-grain', (v) => setState({ grain: v }, 'tg'));
  const splitBtns = seg(splitItems, state.split, 'data-split', (v) => setState({ split: v }, 'ts'));
  const basisBtns = src && src.basis ? seg(src.basis.options, state.basis, 'data-numbers', (v) => setState({ basis: v }, 'nb')) : [];
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
        basisBtns.length ? h('span', { class: 'ctl' }, h('span', { class: 'ctl-name' }, 'Numbers'), h('span', { class: 'segs', id: 'trend-numbers' }, basisBtns)) : null,
        overlayWrap, pinInfo, pinHint, status),
      h('span', { class: 'spacer' }),
      toggleBtn),
    body);
  const controlsEl = el.querySelector('.trend-controls');

  function syncSplitControl() {
    const ok = barsAdditive();
    splitBtns.forEach((b) => { b.disabled = !ok; b.setAttribute('aria-pressed', String(ok && b.dataset.split === state.split)); });
    splitHint.textContent = ok ? '' : (state.bars === 'none' ? 'Split applies to the bars; Bars is None.' : 'Not available for ratio bars.');
    splitHint.title = src ? 'Split applies to additive Bars metrics.' : SPLIT_HINT;
    el.querySelector('#trend-split').title = ok ? '' : (src ? 'Split applies to additive Bars metrics.' : SPLIT_HINT);
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
  syncSwap();

  // ----- state -----
  function writeUrl(key, value, dflt) {
    if (local) return;
    const u = new URL(location.href);
    if (value === dflt) u.searchParams.delete(key); else u.searchParams.set(key, value);
    navigate(u.pathname + u.search, { replace: true, render: false });
  }
  function setState(patch, urlKey) {
    Object.assign(state, patch);
    if (urlKey === 'tg') { writeUrl('tg', state.grain, 'day'); grainBtns.forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.grain === state.grain))); }
    if (urlKey === 'ts') { writeUrl('ts', state.split, defaultSplit); syncSplitControl(); }
    if (urlKey === 'nb') { writeUrl(src.basis.param, state.basis, src.basis.default); basisBtns.forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.numbers === state.basis))); }
    load();
  }
  function setCollapsed(on) {
    state.collapsed = on;
    cstore.set(on);
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
    else { const a = apiWindow(p); if (a.stage) o.stage = a.stage; if (a.active) o.active = a.active; }
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

  async function fetchSource(sig) {
    const d = await src.fetch({ grain: state.grain, split: effSplit(), basis: state.basis, signal: sig });
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
      const res = mode === 'landing' ? await fetchLanding(abort.signal) : mode === 'source' ? await fetchSource(abort.signal) : await fetchScope(abort.signal);
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
    const labels = res.labels ? res.labels.slice() : base.map((pt) => (pt ? pt.t : null));
    for (let i = 0; i < labels.length; i++) if (labels[i] == null) labels[i] = pts[i] ? pts[i].t : labels[i - 1] || '';
    const val = (list, m) => list.map((pt) => (pt && isNum(pt[m.key]) ? pt[m.key] : null));
    const partial = base.map((pt) => !!(pt && pt.partial));
    const datasets = [];
    const notes = [];
    const split = effSplit();
    const hasData = (list) => list.some((pt) => pt && ((pt.spend || 0) > 0 || (pt.purchases || 0) > 0 || (pt.link_clicks || 0) > 0));
    // the split series: stage (product pages), or the groups the server sends (platform pages: stage / product / platform)
    const order = ['testing', 'scaling', 'unclassified'].filter((s) => res.byStage && res.byStage[s] && (s !== 'unclassified' || hasData(res.byStage[s])));
    const mkGroups = (list) => list.filter((g) => g.key !== 'unclassified' || hasData(g.points)).map((g, i) => ({ ...g, color: g.color || STAGE_COLOR[g.key] || PALETTE[i % PALETTE.length] }));
    let groups = null;       // stacked into the bars
    let lineGroups = null;   // one ratio line each (platform pages: always, so a platform's own ROAS can be read while the bars show the total)
    if (src && src.alwaysSplit && res.groups) {
      lineGroups = mkGroups(res.groups);
      if (state.split !== 'none') groups = lineGroups;
    } else if (split !== 'none') {
      if (res.groups) groups = mkGroups(res.groups);
      else if (split === 'stage' && res.byStage) groups = order.map((st) => ({ key: st, label: STAGE_NAME[st], color: STAGE_COLOR[st], points: res.byStage[st] }));
    }
    const stacked = !!bm && !!groups;
    // All views: the server's total series (spend of both platforms, NC ROAS as a ratio of sums per bucket), same x axis as the labels
    const tot = src && src.total && res.total ? res.total : null;
    const totOn = !!(tot && tot.available && totalLine);
    const totPts = tot ? tot.points || [] : [];
    const totLine = () => ({ axis: 'right', type: 'line', kind: NC_TOTAL.kind, label: NC_TOTAL.label, data: totPts.map((pt) => (pt && isNum(pt.nc_roas) ? pt.nc_roas : null)), color: totalColor(), width: 2.6, partialDash: true, order: -1, hideInTip: true });
    if (rows) {
      // pinned rows: one line per row on the right axis (Bars = None)
      if (lm) res.pinned.forEach((pn, i) => datasets.push({ axis: 'right', type: 'line', kind: lm.kind, label: pn.label, data: val(pn.points, lm), color: PALETTE[i % PALETTE.length], width: 2, partialDash: true }));
      notes.push(`Showing ${res.pinned.length} pinned row${res.pinned.length === 1 ? '' : 's'} on the line axis (max ${MAX_PINS}).`);
    } else {
      if (bm) {
        if (stacked) {
          groups.forEach((g) => datasets.push({ axis: 'left', type: 'bar', kind: bm.kind, label: g.label, data: val(g.points, bm), color: g.color, stack: 'stage', opaque: true }));
        } else {
          // the combined series (total spend of both platforms): one bar per bucket; a stack of one so the total is written above it on a few buckets
          datasets.push({ axis: 'left', type: 'bar', kind: bm.kind, label: bm.label, data: val(pts, bm), color: BAR_COLOR, ...(tot ? { stack: 'total' } : {}) });
        }
      }
      if (lm) {
        if (lm.virtual) {
          if (totOn) datasets.push(totLine());
        } else if ((lineGroups || groups) && !lm.additive) {
          (lineGroups || groups).forEach((g) => datasets.push({ axis: 'right', type: 'line', kind: lm.kind, label: `${lm.label} · ${g.label}`, data: val(g.points, lm), color: g.color, width: 2, partialDash: true }));
          if (totOn && lm.key === 'roas') datasets.push(totLine());               // the actual total next to each platform's own line
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
      if (bm) datasets.push({ axis: 'left', type: 'bar', kind: bm.kind, label: `B ${bm.label}${tag}`, data: val(res.cmp, bm), color: tok('--lv-muted'), outline: true, dash: [3, 3], order: 3, cmpDates });
      if (lm) datasets.push({ axis: 'right', type: 'line', kind: lm.kind, label: `B ${lm.label}${tag}`, data: val(res.cmp, lm), color: tok('--lv-line-strong'), dash: [5, 4], width: 1.6, order: 0, cmpDates });
    }
    // window A / B shading (ad detail: the chart always shows the full series)
    const shades = [];
    if (mode === 'ad' && opts.shadeInfo) {
      const { a, b } = opts.shadeInfo;
      if (a && a.from && a.to) shades.push({ from: a.from, to: a.to, color: 'rgba(26,95,180,0.14)' });
      if (b && b.from && b.to && cmpView.on) shades.push({ from: b.from, to: b.to, color: 'rgba(232,87,58,0.20)' });
    }
    if (totOn && tot.orders_first && labels.length && labels[0] < tot.orders_first) notes.push(`Total NC ROAS is available from ${fmt.day(tot.orders_first)}; earlier days are blank, not zero.`);
    // hover: both platforms' spend, the total and the total NC ROAS of the bucket
    const have = new Set((res.groups || []).map((g) => g.key));
    const extra = tot && totalLine ? (i) => {                  // a platform view keeps the plain per-series hover: its own spend and ROAS lines say it all
      const t = totPts[i];
      if (!t) return [];
      const out = [];
      if (have.has('meta')) out.push(`Meta spend: ${fmt.money2(t.meta_spend)}`);
      if (have.has('google')) out.push(`Google spend: ${fmt.money2(t.google_spend)}`);
      out.push(`Total spend: ${fmt.money2(t.spend)}`);
      if (tot.available) out.push(isNum(t.nc_roas) ? `Total NC ROAS: ${fmt.roas(t.nc_roas)}${t.nc_days != null ? ` (${t.nc_days} day${t.nc_days === 1 ? '' : 's'} with orders)` : ''}` : 'Total NC ROAS: no order data');
      return out;
    } : null;
    if (chart) chart.destroy();
    chart = trendChart(canvas, {
      labels, grain: res.grain || state.grain, partial, shades, stackedBars: stacked || (!!bm && !!tot && !rows), stackTotals: !!tot, extra,
      left: bm && !rows ? { kind: bm.kind, title: `${bm.label} (bars, left)` } : null,
      right: lm ? { kind: lm.kind, title: `${lm.label} (line, right)` } : null,
      datasets,
    });
    const wi = res.windowInfo;
    if (wi && wi.note && mode !== 'ad') notes.push(wi.note);
    for (const n of res.notes || []) notes.push(n);
    if (mode === 'ad' && shades.length) notes.push(shades.length > 1 ? 'Shaded: window A (blue) and window B (orange).' : 'Shaded: selected window.');
    note.replaceChildren(...notes.map((t) => h('span', { class: 'note info' }, t)));
    if (pins && pins.size() > 0) {
      pinInfo.hidden = false;
      pinInfo.replaceChildren(`Pinned ${pins.size()}/${MAX_PINS} `, h('button', { type: 'button', class: 'btn small', id: 'trend-clear', onclick: () => pins.clear() }, 'Clear'));
    } else { pinInfo.hidden = true; }
  }

  // advanced metrics appear with the Advanced columns chip; pins re-draw the chart
  onCmpRefresh(() => {
    optionsFor(barsSel, state.bars, barsOnly);
    optionsFor(lineSel, state.line, null, lineExtra);
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
