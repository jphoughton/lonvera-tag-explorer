// Projection UI: fetch layer. Module pages never handle auth, rate limits or batching themselves.
//   - 401 (only when the app's own login is on) -> Tag Explorer /login?next=<this page>, like app/static/js/api.js.
//   - 429 {error:"rate_limited"} -> ApiError with a plain message (err.code === 'rate_limited').
//   - Mutations always send Content-Type: application/json.
//   - saveSeries() batches and debounces PUT /api/projection/series (<= 500 items per request, one request in flight).
import { qs, createEmitter } from './util.js';

const BASE = '/api/projection';
export const events = createEmitter();          // 'saved' -> {scenario, version}

// A write guard sees every mutating request before it is sent (lib/guard.js uses it to protect the plan of record).
// guard({method, path, params, body}) -> null to go ahead, {scenario, after?} to send the request to another scenario, or it throws (EditCancelled).
let writeGuard = null;
export function setWriteGuard(fn) { writeGuard = fn; }
export class EditCancelled extends Error {
  constructor(message = 'No change made. The plan of record is unchanged.') { super(message); this.name = 'EditCancelled'; }
}
// Versioned writes: series PUT, scenario rename and restore carry `expected_version`, so a stale tab cannot silently overwrite another tab's edit.
// The server answers 409 "version conflict: the scenario is at version N"; the conflict handler (lib/guard.js) asks Reload or Overwrite.
//   setVersionSource(async (slug, fresh) => version)  where the known version comes from (fresh = ask the server first)
//   setConflictHandler(async ({slug, mine, theirs}) => 'overwrite' | 'reload')
let versionSource = null;
let conflictHandler = null;
export function setVersionSource(fn) { versionSource = fn; }
export function setConflictHandler(fn) { conflictHandler = fn; }
const staleVersion = new Set();                  // scenarios written through endpoints that do not report a version: refresh before the next versioned write
function versionedSlug({ method, path, body }) {
  const p = '/' + String(path || '').replace(/^\/?(api\/projection\/)?/, '').replace(/\?.*$/, '');
  if (method === 'PUT' && p === '/series') return body && typeof body.scenario === 'string' ? body.scenario : null;
  let m = /^\/scenarios\/([^/]+)$/.exec(p);
  if (m && method === 'PATCH') return decodeURIComponent(m[1]);
  m = /^\/scenarios\/([^/]+)\/(?:restore|detach)$/.exec(p);
  return m && method === 'POST' ? decodeURIComponent(m[1]) : null;
}
const conflictVersion = (err) => { const m = /version (\d+)/.exec((err && (err.code || err.message)) || ''); return m ? Number(m[1]) : null; };

// point a request at another scenario: body.scenario, params.scenario and a ?scenario= in the path
function retarget({ path, params, body }, to) {
  const out = { path: path.replace(/([?&]scenario=)[^&#]*/, `$1${encodeURIComponent(to)}`), params, body };
  if (params && 'scenario' in params) out.params = { ...params, scenario: to };
  if (body && typeof body === 'object' && 'scenario' in body) out.body = { ...body, scenario: to };
  return out;
}

export class ApiError extends Error {
  constructor(message, status, code, data) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code || null;
    this.data = data || null;
  }
}
export class AuthError extends ApiError {
  constructor() { super('Sign in required', 401, 'unauthorized'); this.name = 'AuthError'; }
}

const FRIENDLY = {
  rate_limited: 'Too many changes in a short time. Wait a minute and try again.',
  unauthorized: 'Sign in required',
  confirm_required: 'Type the scenario slug to confirm.',
};

let onUnauthorized = () => {
  const next = location.pathname + location.search;
  location.assign('/login?next=' + encodeURIComponent(next));
};
export function setUnauthorizedHandler(fn) { onUnauthorized = fn; }

const url = (path, params) => (path.startsWith('/api/') ? path : BASE + (path.startsWith('/') ? path : '/' + path)) + qs(params || {});

export async function fetchJSON(path0, { method = 'GET', params: params0, body: body0, signal } = {}) {
  const mutating = method !== 'GET' && method !== 'HEAD';
  let path = path0, params = params0, body = body0, after = null;
  if (mutating && writeGuard) {
    const g = await writeGuard({ method, path, params, body });
    if (g && g.scenario) { ({ path, params, body } = retarget({ path, params, body }, g.scenario)); after = g.after || null; }
  }
  const vslug = mutating ? versionedSlug({ method, path, body }) : null;
  try {
    if (vslug && body && typeof body === 'object' && body.expected_version === undefined && versionSource) {
      const v = await versionSource(vslug, staleVersion.delete(vslug));
      if (typeof v === 'number') body = { ...body, expected_version: v };
    }
    let data;
    try {
      data = await send(path, { method, params, body, signal, mutating });
    } catch (e) {
      const theirs = vslug && e && e.status === 409 ? conflictVersion(e) : null;
      if (theirs == null || !conflictHandler || !body || body.expected_version === undefined) throw e;
      const choice = await conflictHandler({ slug: vslug, mine: body.expected_version, theirs });
      if (choice !== 'overwrite') throw new EditCancelled('This plan changed in another tab or window. Reload to see it.');
      data = await send(path, { method, params, body: { ...body, expected_version: theirs }, signal, mutating });
    }
    if (mutating && data && typeof data === 'object') {
      const slug = vslug || (body && typeof body.scenario === 'string' ? body.scenario : null);
      const v = typeof data.version === 'number' ? data.version : (data.scenario && typeof data.scenario.version === 'number' ? data.scenario.version : null);
      if (slug && v != null) events.emit('saved', { scenario: slug, version: v });
      else if (slug) staleVersion.add(slug);
    }
    return data;
  } finally { if (after) { try { after(); } catch { /* the UI refresh must not mask the result */ } } }
}

async function send(path, { method, params, body, signal, mutating }) {
  const headers = { Accept: 'application/json' };
  let payload;
  if (mutating) {
    headers['Content-Type'] = 'application/json';
    if (body !== undefined) payload = JSON.stringify(body);
    else if (method !== 'DELETE') payload = '{}';
  }
  let res;
  try {
    res = await fetch(url(path, params), { method, headers, body: payload, credentials: 'same-origin', signal });
  } catch (e) {
    if (e && e.name === 'AbortError') throw e;
    throw new ApiError('Network error. Check the connection and retry.', 0, 'network');
  }
  if (res.status === 401) {
    onUnauthorized();
    throw new AuthError();
  }
  if (res.status === 204) return null;
  let data = null;
  try { data = await res.json(); } catch { /* non-JSON error page */ }
  if (!res.ok) {
    const code = data && typeof data.error === 'string' ? data.error : null;
    if (res.status === 503 && code === 'upstream_degraded') {            // a module this endpoint depends on failed: tell the shell, then fail with plain words
      const fallback = (data && (data.detail || data.message)) || '';
      const mods = data && Array.isArray(data.modules) && data.modules.length ? data.modules : [data && data.module ? data.module : null];   // [{module, error}] (or bare names)
      for (const d of mods) events.emit('degraded', { module: (d && typeof d === 'object' ? d.module : d) || null, error: (d && typeof d === 'object' ? (d.error || d.detail || d.message) : '') || fallback });
      throw new ApiError('These numbers are not shown because a module they depend on failed to load.', 503, code, data);
    }
    const msg = res.status === 429 ? (FRIENDLY[code] || FRIENDLY.rate_limited)
      : (code && FRIENDLY[code]) || code || `HTTP ${res.status}`;
    throw new ApiError(msg, res.status, code, data);
  }
  return data;
}

export const get = (path, params, opts = {}) => fetchJSON(path, { ...opts, params });
export const post = (path, body, opts = {}) => fetchJSON(path, { ...opts, method: 'POST', body: body ?? {} });
export const put = (path, body, opts = {}) => fetchJSON(path, { ...opts, method: 'PUT', body: body ?? {} });
export const patch = (path, body, opts = {}) => fetchJSON(path, { ...opts, method: 'PATCH', body: body ?? {} });
export const del = (path, body, opts = {}) => fetchJSON(path, { ...opts, method: 'DELETE', body });

// ---------- series writes: batch + debounce ----------
export const MAX_ITEMS = 500;                   // server cap per PUT
const DELAY = 250;
const pending = new Map();                      // scenario -> {items: Map, waiters: [], timer}
let chain = Promise.resolve();

function normalize(it) {
  if (!it || !it.line_key || !it.month) throw new Error('series item needs line_key and month');
  const v = it.value;
  const out = {
    line_key: it.line_key,
    month: it.month,
    value: v == null || v === '' ? null : (typeof v === 'number' ? v : (Number.isFinite(Number(v)) ? Number(v) : v)),
    kind: it.kind || 'input',
  };
  if (it.note !== undefined) out.note = it.note;
  return out;
}

// saveSeries(scenario, items[{line_key, month, value|null, kind?, note?}], {delay}) -> Promise<{version}>
// Calls made within `delay` ms are merged into one PUT; the same (line_key, month) keeps the last value.
export function saveSeries(scenario, items, { delay = DELAY } = {}) {
  return new Promise((resolve, reject) => {
    let b = pending.get(scenario);
    if (!b) { b = { items: new Map(), waiters: [], timer: null }; pending.set(scenario, b); }
    try {
      for (const it of items) { const n = normalize(it); b.items.set(n.line_key + '|' + n.month, n); }
    } catch (e) { reject(e); return; }
    b.waiters.push({ resolve, reject });
    clearTimeout(b.timer);
    b.timer = setTimeout(() => flush(scenario), delay);
  });
}

function flush(scenario) {
  const b = pending.get(scenario);
  if (!b) return chain;
  pending.delete(scenario);
  clearTimeout(b.timer);
  chain = chain.then(async () => {
    try {
      const all = [...b.items.values()];
      let res = null;
      for (let i = 0; i < all.length; i += MAX_ITEMS) {
        res = await put('/series', { scenario, items: all.slice(i, i + MAX_ITEMS) });
      }
      for (const w of b.waiters) w.resolve(res);
      if (res && res.version != null) events.emit('saved', { scenario, version: res.version });
    } catch (e) {
      for (const w of b.waiters) w.reject(e);
    }
  });
  return chain;
}
// send everything queued right now (used before navigating away)
export function flushSeries() {
  for (const s of [...pending.keys()]) flush(s);
  return chain;
}

// ---------- convenience wrappers used by ctx.api ----------
// table('drivers', {extra}) -> GET /api/projection/drivers/table?scenario=<current>&extra
export function makeContextApi(getScenario) {
  const dashed = (m) => String(m).replace(/_/g, '-');
  return {
    get: (path, params, opts) => get(path, { scenario: getScenario(), ...(params || {}) }, opts),
    post, put, patch, del,
    table: (module, params, opts) => get(`/${dashed(module)}/table`, { scenario: getScenario(), ...(params || {}) }, opts),
    saveSeries: (items, opts) => saveSeries(getScenario(), items, opts),
    run: (opts) => get('/run', { scenario: getScenario() }, opts),
    fetchJSON,
  };
}
