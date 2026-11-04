// DOM helpers, formatting, URL/state helpers. No innerHTML anywhere: API data is untrusted and goes in via textContent.

export function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  if (attrs != null && (typeof attrs !== 'object' || attrs instanceof Node || Array.isArray(attrs))) {
    children.unshift(attrs);
    attrs = null;
  }
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k === 'class') el.className = v;
      else if (k === 'dataset') Object.assign(el.dataset, v);
      else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
      else if (k.length > 2 && k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2).toLowerCase(), v);
      else if (v === true) el.setAttribute(k, '');
      else el.setAttribute(k, String(v));
    }
  }
  for (const c of children.flat(Infinity)) {
    if (c == null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

export const $ = (sel, root = document) => root.querySelector(sel);
export const enc = encodeURIComponent;

// ---------- formatting ----------
const nf0 = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 });
const nf2 = new Intl.NumberFormat('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
export const isNum = (v) => typeof v === 'number' && Number.isFinite(v);
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

export const fmt = {
  money(v) {
    if (!isNum(v)) return '–';
    const r = Math.round(v);
    return (r < 0 ? '-' : '') + '$' + nf0.format(Math.abs(r));
  },
  money2(v) {
    if (!isNum(v)) return '–';
    return (v < 0 ? '-' : '') + '$' + nf2.format(Math.abs(v));
  },
  roas(v) { return isNum(v) ? nf2.format(v) : '–'; },
  int(v) { return isNum(v) ? nf0.format(Math.round(v)) : '–'; },
  pct(v) { return isNum(v) ? nf0.format(v * 100) + '%' : '–'; },
  dec1(v) { return isNum(v) ? (Math.round(v * 10) / 10).toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 1 }) : '–'; },
  // (A - B) / B as '▲12%' / '▼3%' / '0%'
  pctChange(v) {
    if (!isNum(v)) return '';
    const p = Math.abs(v) * 100;
    const r = p >= 10 ? Math.round(p) : Math.round(p * 10) / 10;
    if (r === 0) return '0%';
    return (v > 0 ? '▲' : '▼') + nf0.format(r) + '%';
  },
  share(v) { return isNum(v) ? (v * 100).toFixed(1) + '%' : '–'; },
  // percentage-point change, value already in points: '▲1.2pp' (digits = decimals)
  pp(v, digits = 1) {
    if (!isNum(v)) return '';
    const f = 10 ** digits;
    const r = Math.round(Math.abs(v) * f) / f;
    return r === 0 ? '0pp' : (v > 0 ? '▲' : '▼') + r.toFixed(digits) + 'pp';
  },
  rate(v) { return isNum(v) ? (v * 100).toFixed(2) + '%' : '–'; }, // CTR 1.32%, conv rate 2.57%
  text(v) { return v == null || v === '' ? '–' : String(v); },
  // 'YYYY-MM-DD' -> 'Nov 2' (no Date object: avoids timezone drift)
  day(s) {
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(s || '');
    return m ? `${MONTHS[+m[2] - 1]} ${+m[3]}` : fmt.text(s);
  },
  dateTime(s) {
    if (!s) return '–';
    const d = new Date(s);
    if (isNaN(d)) return String(s);
    return d.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
  },
};

export function safeUrl(u) {
  try {
    const x = new URL(String(u), location.href);
    return x.protocol === 'http:' || x.protocol === 'https:' ? x.href : null;
  } catch { return null; }
}
export function safeColor(c) {
  return typeof c === 'string' && /^#[0-9a-fA-F]{3,8}$/.test(c) ? c : null;
}
export function debounce(fn, ms = 200) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

// ---------- URL state ----------
export const GLOBAL_KEYS = ['window', 'from', 'to', 'stage', 'compare', 'cfrom', 'cto', 'perday', 'tm', 'tg', 'ts'];

export function qs(params) {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params || {})) {
    if (v == null || v === '' || v === false) continue;
    sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? '?' + s : '';
}
export function currentParams() {
  return Object.fromEntries(new URL(location.href).searchParams);
}
export function pick(obj, keys) {
  const o = {};
  for (const k of keys) if (obj[k] != null && obj[k] !== '') o[k] = obj[k];
  return o;
}
// window/from/to/stage carried into every link
export function globalParams(p) {
  return pick(p, GLOBAL_KEYS);
}
// ---------- stage include list ----------
export const STAGES = ['testing', 'scaling', 'unclassified'];
export function stageSet(p) {
  const v = p.stage;
  if (!v || v === 'all') return new Set(STAGES);
  const set = new Set(String(v).split(',').map((x) => x.trim()).filter((x) => STAGES.includes(x)));
  return set.size ? set : new Set(STAGES);
}
// serialise the chip state; hidden Unclassified chip never matters, so Testing + Scaling = "all"
export function stageParam(set, uncVisible) {
  if (uncVisible) {
    if (STAGES.every((x) => set.has(x))) return null;
    return STAGES.filter((x) => set.has(x)).join(',');
  }
  if (set.has('testing') && set.has('scaling')) return null;
  return ['testing', 'scaling'].filter((x) => set.has(x)).join(',') || null;
}

// does window A have a date range (comparison needs one)?
export function hasDateWindow(p) {
  return !!((p.from && p.to) || (p.window && p.window !== 'launch'));
}
export function compareOn(p) {
  return !!(p.compare || (p.cfrom && p.cto));
}
// params for the API: custom range wins over window; stage only when not "all"; compare only with a date window A
export function apiWindow(p) {
  const o = {};
  if (p.from && p.to) { o.from = p.from; o.to = p.to; }
  else if (p.window && p.window !== 'launch') o.window = p.window;
  if (p.stage && p.stage !== 'all') {
    const set = stageSet(p);
    if (!STAGES.every((x) => set.has(x))) o.stage = STAGES.filter((x) => set.has(x)).join(',');
  }
  if (hasDateWindow(p)) {
    if (p.cfrom && p.cto) { o.cfrom = p.cfrom; o.cto = p.cto; }
    else if (p.compare) o.compare = p.compare;
  }
  return o;
}

// comparison view state shared by tables/cards (set by each page from the response)
export const cmpView = { on: false, perDay: false, a: null, b: null };
let refreshers = [];
export function onCmpRefresh(fn) { refreshers.push(fn); }
export function clearCmpRefreshers() { refreshers = []; }
export function refreshCmp() { refreshers.forEach((f) => f()); }
export function applyCompareState(p, a, b) {
  cmpView.on = !!(a && b);
  cmpView.a = a || null;
  cmpView.b = b || null;
  cmpView.perDay = cmpView.on && (p.perday != null && p.perday !== '' ? p.perday === '1' : (a.days !== b.days));
}
// drop defaults so URLs stay clean
export function cleanParams(p) {
  const o = { ...p };
  if (o.stage === 'all' || o.stage === STAGES.join(',')) delete o.stage;
  if (o.window === 'launch') delete o.window;
  if (o.from && o.to) delete o.window;
  if (o.tm === 'spend') delete o.tm;
  if (o.tg === 'day') delete o.tg;
  if (o.ts === 'none') delete o.ts;
  if (o.cfrom && o.cto) delete o.compare;
  if (!o.compare && !(o.cfrom && o.cto)) { delete o.perday; delete o.cfrom; delete o.cto; }
  for (const k of Object.keys(o)) if (o[k] == null || o[k] === '' || o[k] === false) delete o[k];
  return o;
}
export function href(path, params) { return path + qs(cleanParams(params || {})); }

let renderer = () => {};
export function setRenderer(fn) { renderer = fn; }
export function rerender() { renderer({ restore: true }); }

// ----- view state (expanded rows, sort, scroll) kept in history.state with a sessionStorage fallback -----
let vsProvider = null;
export function setViewStateProvider(fn) { vsProvider = fn; }
const vsKey = (url) => 'tagexp.vs:' + url;
export function saveViewState() {
  let vs = null;
  try { vs = vsProvider ? vsProvider() : null; } catch { vs = null; }
  if (!vs) return;
  vs.scrollY = window.scrollY;
  try { history.replaceState({ ...(history.state || {}), vs }, ''); } catch { /* ignore */ }
  try { sessionStorage.setItem(vsKey(location.pathname + location.search), JSON.stringify(vs)); } catch { /* ignore */ }
}
export function loadViewState() {
  const st = history.state && history.state.vs;
  if (st) return st;
  try {
    const raw = sessionStorage.getItem(vsKey(location.pathname + location.search));
    return raw ? JSON.parse(raw) : null;
  } catch { return null; }
}
export function navIndex() { return (history.state && history.state.idx) || 0; }

// pushState = a drill (Back returns here); replace = a filter change (Back is not an undo for filters)
export function navigate(url, { replace = false, render = true, scroll = true } = {}) {
  saveViewState();
  // "Per day" changes the URL without a re-render, so links built earlier may carry a stale value
  try {
    const u = new URL(url, location.href);
    const cur = currentParams();
    if (!replace && cur.perday != null && (u.searchParams.has('compare') || u.searchParams.has('cfrom')) && u.searchParams.get('perday') !== cur.perday) {
      u.searchParams.set('perday', cur.perday);
      url = u.pathname + u.search;
    }
  } catch { /* keep url */ }
  const idx = navIndex();
  if (replace) history.replaceState({ ...(history.state || {}), idx }, '', url);
  else history.pushState({ idx: idx + 1 }, '', url);
  if (render) renderer({ scroll: scroll && !replace, restore: replace });
}
// merge into the current query string. Filters (date range, stage, compare, per-day) replace the history entry.
export function setParams(changes, opts = {}) {
  const p = cleanParams({ ...currentParams(), ...changes });
  navigate(location.pathname + qs(p), { scroll: false, replace: true, ...opts });
}

// where Back goes when there is no in-app history (set by pages that know their parent, e.g. the ad detail page)
let parentHint = null;
export function setParentHint(u) { parentHint = u; }
export function getParentHint() { return parentHint; }

// "Columns" chip groups: Traffic (default on) and Advanced (default off), persisted in localStorage
const COLPREF = { traffic: 'tagexp.cols.traffic', advanced: 'tagexp.cols.advanced', trend: 'tagexp.cols.trend' };
export function colPref(group) {
  try {
    const v = localStorage.getItem(COLPREF[group]);
    return v == null ? group !== 'advanced' : v === '1';
  } catch { return group !== 'advanced'; }
}
export function setColPref(group, on) {
  try { localStorage.setItem(COLPREF[group], on ? '1' : '0'); } catch { /* ignore */ }
}

// trend panel metric drives the sparkline column: tables ask the API for that metric's last 30 days per row
export function sparkParams(p) {
  return { spark: p.tm || 'spend', spark_days: 30 };
}
export const TREND_METRIC_KEYS = ['spend', 'purchases', 'revenue', 'roas', 'cpa', 'impressions', 'link_clicks', 'ctr', 'cpc', 'cpm', 'conv_rate'];
const TREND_COLLAPSE_KEY = 'tagexp.trend.collapsed';
export function trendCollapsed() {
  try { return localStorage.getItem(TREND_COLLAPSE_KEY) === '1'; } catch { return false; }
}
export function setTrendCollapsed(on) {
  try { localStorage.setItem(TREND_COLLAPSE_KEY, on ? '1' : '0'); } catch { /* ignore */ }
}

// "Clicking a row": 'expand' (drops down, default) or 'open' (navigates). Persisted in localStorage.
const ROWMODE_KEY = 'tagexp.rowmode';
export function getRowMode() {
  try { return localStorage.getItem(ROWMODE_KEY) === 'open' ? 'open' : 'expand'; } catch { return 'expand'; }
}
export function setRowMode(m) {
  try { localStorage.setItem(ROWMODE_KEY, m === 'open' ? 'open' : 'expand'); } catch { /* ignore */ }
}

// ---------- small components ----------
export function badge(text, cls = '', title = '') {
  return h('span', { class: 'badge ' + cls, title: title || null }, text);
}
export function stageBadge(stage) {
  const s = stage || 'unclassified';
  return badge(s, 'stage-' + s);
}
export function confBadge(c) {
  if (!c) return badge('untagged', 'conf-none');
  return badge(c, 'conf-' + c);
}
export function liveDot(live) {
  return h('span', { class: 'live ' + (live ? 'on' : 'off'), title: live ? 'Spent in the last 2 days of data' : 'Not live' }, live ? '●' : '○');
}
export function methodFlags(method) {
  const s = (method || '').toLowerCase();
  return {
    copyOnly: /text only|copy only/.test(s),
    thumbOnly: /thumbnail/.test(s) && /(no audio|only)/.test(s),
  };
}
export function colorChip(color) {
  const c = safeColor(color);
  return h('span', { class: 'chip', style: c ? { backgroundColor: c } : null });
}
export function thumb(assetId, size = 36) {
  if (assetId == null) return h('span', { class: 'thumb ph', style: { width: size + 'px', height: size + 'px' } });
  const img = h('img', {
    class: 'thumb', loading: 'lazy', alt: '', src: '/media/' + enc(assetId),
    style: { width: size + 'px', height: size + 'px' },
  });
  img.addEventListener('error', () => img.replaceWith(h('span', { class: 'thumb ph', style: { width: size + 'px', height: size + 'px' } })));
  return img;
}
export function empty(text) { return h('div', { class: 'empty' }, text); }
