// Top bar (brand, data-through, logout) and the global range/stage controls. State lives in the URL.
import { h, navigate, setParams, fmt } from './util.js';
import { api, AuthError } from './api.js';

const PRESETS = [['launch', 'Since launch'], ['1d', '1d'], ['7d', '7d'], ['14d', '14d'], ['30d', '30d'], ['60d', '60d'], ['90d', '90d'], ['180d', '180d'], ['mtd', 'MTD']];
const STAGES = [['all', 'All'], ['testing', 'Testing'], ['scaling', 'Scaling']];

let topbar, controls, statusEl, labelEl, noteEl, fromEl, toEl, errEl;
let presetBtns = [], stageBtns = [];
let status = null;

export function buildShell() {
  topbar = document.getElementById('topbar');
  controls = document.getElementById('controls');

  statusEl = h('span', { class: 'status', id: 'status' });
  topbar.replaceChildren(h('div', { class: 'topbar-in' },
    h('a', { class: 'brand', href: '/' }, 'Tag Explorer'),
    h('a', { class: 'navlink', href: '/' }, 'Products'),
    h('span', { class: 'spacer' }),
    statusEl,
    h('button', { type: 'button', class: 'btn small', id: 'logout', onclick: logout }, 'Log out'),
  ));

  presetBtns = PRESETS.map(([val, text]) => {
    const b = h('button', { type: 'button', class: 'seg', 'data-window': val, 'aria-pressed': 'false' }, text);
    b.addEventListener('click', () => setParams({ window: val, from: null, to: null }));
    return b;
  });
  stageBtns = STAGES.map(([val, text]) => {
    const b = h('button', { type: 'button', class: 'seg', 'data-stage': val, 'aria-pressed': 'false' }, text);
    b.addEventListener('click', () => setParams({ stage: val }));
    return b;
  });
  fromEl = h('input', { type: 'date', id: 'range-from', 'aria-label': 'From date' });
  toEl = h('input', { type: 'date', id: 'range-to', 'aria-label': 'To date' });
  errEl = h('span', { class: 'err', id: 'range-err' });
  labelEl = h('strong', { class: 'range-label', id: 'range-label' });
  noteEl = h('div', { class: 'range-note', id: 'range-note' });
  const apply = h('button', { type: 'button', class: 'btn small primary', id: 'range-apply' }, 'Apply');
  apply.addEventListener('click', applyCustom);
  for (const el of [fromEl, toEl]) el.addEventListener('keydown', (e) => { if (e.key === 'Enter') applyCustom(); });

  controls.replaceChildren(h('div', { class: 'controls-in' },
    h('div', { class: 'ctl-row' },
      h('div', { class: 'ctl' }, h('span', { class: 'ctl-name' }, 'Range'), h('span', { class: 'segs', role: 'group', 'aria-label': 'Date range' }, presetBtns)),
      h('div', { class: 'ctl' }, h('span', { class: 'ctl-name' }, 'Custom'), fromEl, h('span', { class: 'muted' }, '–'), toEl, apply, errEl),
      h('div', { class: 'ctl grow' }, labelEl),
      h('div', { class: 'ctl' }, h('span', { class: 'ctl-name' }, 'Stage'), h('span', { class: 'segs', role: 'group', 'aria-label': 'Stage' }, stageBtns)),
    ),
    noteEl,
  ));
}

function applyCustom() {
  errEl.textContent = '';
  let from = fromEl.value, to = toEl.value;
  if (!from && !to) { errEl.textContent = 'Pick a date'; return; }
  if (!from) from = to;
  if (!to) to = from; // single day = same date twice
  if (from > to) { errEl.textContent = 'From is after To'; return; }
  setParams({ from, to, window: null });
}

async function logout() {
  try { await api('/api/logout', null, { method: 'POST' }); } catch { /* ignore */ }
  status = null;
  navigate('/login', { replace: true });
}

export function showControls(on) {
  controls.hidden = !on;
  document.body.classList.toggle('no-controls', !on);
  topbar.querySelector('#logout').hidden = !on;
}

// reflect URL state in the controls immediately; label/notice arrive with the response (setWindowInfo)
export function syncControls(p) {
  const custom = !!(p.from && p.to);
  const win = custom ? null : (p.window || 'launch');
  presetBtns.forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.window === win)));
  const stage = p.stage || 'all';
  stageBtns.forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.stage === stage)));
  fromEl.value = custom ? p.from : '';
  toEl.value = custom ? p.to : '';
  errEl.textContent = '';
  labelEl.textContent = '';
  noteEl.textContent = '';
  noteEl.className = 'range-note';
  if (status) {
    fromEl.min = toEl.min = status.daily_start || '';
    fromEl.max = toEl.max = status.data_through || '';
  }
}

export function setWindowInfo(info) {
  if (!info) { labelEl.textContent = ''; noteEl.textContent = ''; return; }
  labelEl.textContent = info.label || '';
  if (info.note) {
    noteEl.textContent = (info.clamped ? 'Range clamped: ' : '') + info.note;
    noteEl.className = 'range-note ' + (info.clamped ? 'warn' : 'info');
  } else {
    noteEl.textContent = '';
    noteEl.className = 'range-note';
  }
}

export async function loadStatus(signal) {
  try {
    status = await api('/api/status', null, { signal });
    const lr = status.last_refresh;
    statusEl.replaceChildren(
      h('span', { id: 'data-through' }, 'Data through ' + fmt.day(status.data_through)),
      lr && lr.finished_at ? h('span', { class: 'muted' }, ` · refreshed ${fmt.dateTime(lr.finished_at)}${lr.status && lr.status !== 'ok' && lr.status !== 'success' ? ' (' + lr.status + ')' : ''}`) : null,
    );
    fromEl.min = toEl.min = status.daily_start || '';
    fromEl.max = toEl.max = status.data_through || '';
    return status;
  } catch (e) {
    if (e instanceof AuthError) throw e;
    statusEl.textContent = '';
    return null;
  }
}
export function getStatus() { return status; }
export async function ensureStatus(signal) { return status || loadStatus(signal); }

// Client-side label for endpoints that return a bare array (no window_info), e.g. /api/products.
export function windowInfoFallback(p, st) {
  if (!st) return null;
  const parse = (s) => { const [y, m, d] = s.split('-').map(Number); return Date.UTC(y, m - 1, d); };
  const iso = (ms) => new Date(ms).toISOString().slice(0, 10);
  const through = st.data_through;
  let from, to = through, kind = 'trailing';
  if (p.from && p.to) { from = p.from; to = p.to; kind = 'custom'; }
  else {
    const w = p.window || 'launch';
    if (w === 'launch') return { kind: 'launch', label: 'Since launch', from: null, to: null };
    if (w === 'mtd') { from = through.slice(0, 8) + '01'; kind = 'mtd'; }
    else from = iso(parse(through) - (parseInt(w, 10) - 1) * 86400000);
  }
  let clamped = false;
  if (st.daily_start && from < st.daily_start) { from = st.daily_start; clamped = true; }
  const days = Math.round((parse(to) - parse(from)) / 86400000) + 1;
  const label = from === to ? `${fmt.day(from)} · 1 day` : `${fmt.day(from)} – ${fmt.day(to)} · ${days} days`;
  return { kind, label, from, to, days, clamped, note: clamped ? `Daily data starts ${st.daily_start}.` : null };
}
export function clearStatus() { status = null; if (statusEl) statusEl.textContent = ''; }
