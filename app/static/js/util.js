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
export const GLOBAL_KEYS = ['window', 'from', 'to', 'stage'];

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
// params for the API: custom range wins over window; stage only when not "all"
export function apiWindow(p) {
  const o = {};
  if (p.from && p.to) { o.from = p.from; o.to = p.to; }
  else if (p.window && p.window !== 'launch') o.window = p.window;
  if (p.stage && p.stage !== 'all') o.stage = p.stage;
  return o;
}
// drop defaults so URLs stay clean
export function cleanParams(p) {
  const o = { ...p };
  if (o.stage === 'all') delete o.stage;
  if (o.window === 'launch') delete o.window;
  if (o.from && o.to) delete o.window;
  for (const k of Object.keys(o)) if (o[k] == null || o[k] === '' || o[k] === false) delete o[k];
  return o;
}
export function href(path, params) { return path + qs(cleanParams(params || {})); }

let renderer = () => {};
export function setRenderer(fn) { renderer = fn; }
export function rerender() { renderer(); }
export function navigate(url, { replace = false, render = true, scroll = true } = {}) {
  if (replace) history.replaceState(null, '', url);
  else history.pushState(null, '', url);
  if (render) renderer({ scroll });
}
// merge into the current query string
export function setParams(changes, opts = {}) {
  const p = cleanParams({ ...currentParams(), ...changes });
  navigate(location.pathname + qs(p), { scroll: false, ...opts });
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
