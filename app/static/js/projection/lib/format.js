// Projection UI: number / date formats shared by the grid, charts and module pages.
// Formats (row.fmt): usd (cents), usd0, pct (value is a fraction: 0.125 -> 12.5%), num, x (1.20x), text, date (ISO).
// Dates are handled as strings (no Date objects) so there is no timezone drift.

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const LONG = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
export const FORMATS = ['usd', 'usd0', 'pct', 'num', 'x', 'text', 'date'];

const isNum = (v) => typeof v === 'number' && Number.isFinite(v);
const nfCache = new Map();
function nf(digits) {
  if (!nfCache.has(digits)) nfCache.set(digits, new Intl.NumberFormat('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits }));
  return nfCache.get(digits);
}
const isZero = (v) => Math.abs(v) < 1e-9;
const round = (v, d) => { const f = 10 ** d; return Math.round((v + Number.EPSILON * Math.sign(v)) * f) / f; };

// ---------- dates ----------
export function parseISO(iso) {
  const m = /^(\d{4})-(\d{2})(?:-(\d{2}))?/.exec(iso || '');
  return m ? { y: +m[1], m: +m[2], d: m[3] ? +m[3] : 1 } : null;
}
export const yearOf = (iso) => (parseISO(iso) || {}).y ?? null;
export const monthIndex = (iso) => { const p = parseISO(iso); return p ? p.m - 1 : null; };
// 'Nov 2026' (long: 'November 2026')
export function monthLabel(iso, long = false) {
  const p = parseISO(iso);
  return p ? `${(long ? LONG : MONTHS)[p.m - 1]} ${p.y}` : String(iso ?? '');
}
export function shortMonth(iso) { const p = parseISO(iso); return p ? MONTHS[p.m - 1] : String(iso ?? ''); }
// 'Nov 5, 2026'
export function date(iso) {
  const p = parseISO(iso);
  return p ? `${MONTHS[p.m - 1]} ${p.d}, ${p.y}` : (iso == null || iso === '' ? '' : String(iso));
}
export function dateTime(s) {
  if (!s) return '';
  const d = new Date(s);
  return isNaN(d) ? String(s) : d.toLocaleString('en-US', { month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit' });
}

// ---------- single-purpose formatters (plain strings, minus sign for negatives) ----------
export function money(v, { digits = 0, parens = false } = {}) {
  if (!isNum(v)) return '';
  const body = '$' + nf(digits).format(Math.abs(v));
  const neg = v < 0 && round(v, digits) !== 0;
  return neg ? (parens ? `(${body})` : '-' + body) : body;
}
export function pct(v, digits = 1) { return isNum(v) ? nf(digits).format(round(v * 100, digits)) + '%' : ''; }
export function x(v, digits = 2) { return isNum(v) ? nf(digits).format(v) + 'x' : ''; }
export function num(v, digits) {
  if (!isNum(v)) return '';
  const d = digits ?? (Number.isInteger(v) || Math.abs(v) >= 100 ? 0 : 2);
  return nf(d).format(v);
}
// $1.2M, $350K, -$2.1M
export function compact(v, { prefix = '$', digits = 1 } = {}) {
  if (!isNum(v)) return '';
  const a = Math.abs(v);
  let s;
  if (a >= 1e9) s = trim((a / 1e9).toFixed(digits)) + 'B';
  else if (a >= 1e6) s = trim((a / 1e6).toFixed(digits)) + 'M';
  else if (a >= 1e3) s = trim((a / 1e3).toFixed(a >= 1e5 ? 0 : digits)) + 'K';
  else s = trim(a.toFixed(a < 10 && !Number.isInteger(a) ? 1 : 0));
  return (v < 0 ? '-' : '') + prefix + s;
}
const trim = (s) => (s.includes('.') ? s.replace(/\.?0+$/, '') : s);

// ---------- display (what a grid cell shows) ----------
// display(v, fmt, {digits}) -> {text, neg, blank, zero}
//   null / undefined / NaN -> blank; 0 -> em dash; negative money / num in parentheses; negative pct / x with a minus sign.
export function display(v, fmt = 'num', { digits } = {}) {
  if (fmt === 'text') return { text: v == null ? '' : String(v), neg: false, blank: v == null || v === '', zero: false };
  if (fmt === 'date') { const t = date(v); return { text: t, neg: false, blank: !t, zero: false }; }
  if (!isNum(v)) return { text: '', neg: false, blank: true, zero: false };
  if (isZero(v)) return { text: '—', neg: false, blank: false, zero: true };
  let text;
  switch (fmt) {
    case 'usd': text = money(v, { digits: digits ?? 2, parens: true }); break;
    case 'usd0': text = money(v, { digits: digits ?? 0, parens: true }); break;
    case 'pct': text = pct(v, digits ?? 1); break;
    case 'x': text = x(v, digits ?? 2); break;
    default: {
      const t = num(Math.abs(v), digits);
      text = v < 0 && Number(t.replace(/,/g, '')) !== 0 ? `(${t})` : t;
    }
  }
  return { text, neg: v < 0 && /[1-9]/.test(text), blank: false, zero: false };
}
// plain string for tooltips / labels (no em dash for zero)
export function value(v, fmt = 'num', opts = {}) {
  const d = display(v, fmt, opts);
  return d.zero ? plainZero(fmt) : d.text;
}
function plainZero(fmt) { return fmt === 'usd' ? '$0.00' : fmt === 'usd0' ? '$0' : fmt === 'pct' ? '0.0%' : fmt === 'x' ? '0.00x' : '0'; }

// ---------- editing ----------
const trimNum = (v, d) => { const s = String(round(v, d)); return s; };
// text shown in the cell editor
export function editText(v, fmt = 'num') {
  if (v == null || v === '') return '';
  if (fmt === 'text') return String(v);
  if (fmt === 'date') return String(v);
  if (!isNum(v)) return '';
  if (fmt === 'pct') return trimNum(v * 100, 4) + '%';
  if (fmt === 'x') return trimNum(v, 6) + 'x';
  return trimNum(v, 6);
}
// text copied to the clipboard (parseable again by parse())
export const plain = editText;

const NUM_RE = /^([+-])?\$?([+-])?(\d+\.?\d*|\.\d+)(?:e([+-]?\d+))?\s*(%|x|k|m|b)?$/i;
const SUFFIX = { k: 1e3, m: 1e6, b: 1e9 };
// parse(text, fmt) -> {ok:true, value} | {ok:false, error}
//   '' / '-' / '—' -> value null (clears the cell). Accepts "$1,234.50", "(1,234)", "12%", "1.2x", "350k", "1.5m".
//   A bare number in a pct row is read as percent points ("12" -> 0.12); "12%" is always 0.12.
export function parse(text, fmt = 'num') {
  const s = String(text ?? '').replace(/[ \s]+/g, ' ').trim();
  if (s === '' || s === '-' || s === '—' || s === '–') return { ok: true, value: null };
  if (fmt === 'text') return { ok: true, value: s };
  if (fmt === 'date') {
    const d = parseDateInput(s);
    return d ? { ok: true, value: d } : { ok: false, error: 'Enter a date like 2026-11-05 or 11/5/2026' };
  }
  let t = s;
  let neg = false;
  const paren = /^\((.*)\)$/.exec(t);
  if (paren) { neg = true; t = paren[1].trim(); }
  t = t.replace(/,/g, '').replace(/−/g, '-');
  const m = NUM_RE.exec(t);
  if (!m) return { ok: false, error: 'Not a number' };
  let v = parseFloat(m[3] + (m[4] ? 'e' + m[4] : ''));
  if (m[1] === '-' || m[2] === '-') neg = !neg;
  const suf = (m[5] || '').toLowerCase();
  if (suf === '%') v /= 100;
  else if (SUFFIX[suf]) v *= SUFFIX[suf];
  else if (fmt === 'pct' && suf !== 'x') v /= 100;
  if (neg) v = -v;
  v = Math.round(v * 1e9) / 1e9;
  return Number.isFinite(v) ? { ok: true, value: v } : { ok: false, error: 'Not a number' };
}
function parseDateInput(s) {
  let m = /^(\d{4})-(\d{1,2})-(\d{1,2})$/.exec(s);
  let y, mo, d;
  if (m) { y = +m[1]; mo = +m[2]; d = +m[3]; } else {
    m = /^(\d{1,2})\/(\d{1,2})\/(\d{2}|\d{4})$/.exec(s);
    if (!m) return null;
    mo = +m[1]; d = +m[2]; y = +m[3]; if (y < 100) y += 2000;
  }
  if (mo < 1 || mo > 12 || d < 1 || d > new Date(Date.UTC(y, mo, 0)).getUTCDate()) return null;
  return `${y}-${String(mo).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
}

// ---------- status chips (text rows) ----------
// OUT / OVERDUE / NEGATIVE -> bad, ORDER SOON / LOW -> warn, OK / IN STOCK -> good
export function statusTone(text) {
  const t = String(text ?? '').trim().toUpperCase();
  if (!t) return null;
  if (/^(OUT|OVERDUE|LATE|NEGATIVE|SHORT|STOCKOUT|STOCK-OUT|FAIL|ERROR)\b/.test(t)) return 'bad';
  if (/^(ORDER SOON|LOW|WATCH|DUE|WARN|AT RISK|PENDING)\b/.test(t)) return 'warn';
  if (/^(OK|IN STOCK|ON TRACK|LANDED|PAID|DONE|PASS)\b/.test(t)) return 'good';
  return null;
}

// default FY aggregation for a row: sums for money / units, nothing for rates and text
export function defaultAgg(fmt, kind) {
  if (kind === 'ratio' || kind === 'header') return 'none';
  return fmt === 'usd' || fmt === 'usd0' || fmt === 'num' ? 'sum' : 'none';
}

export default { money, pct, x, num, compact, display, value, parse, editText, plain, monthLabel, shortMonth, date, dateTime, statusTone, defaultAgg, parseISO, yearOf, FORMATS };
