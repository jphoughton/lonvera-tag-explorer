// App header (Back, brand, data-through, logout) and the global range / stage / compare controls. State lives in the URL.
import { h, navigate, setParams, fmt, isNum, currentParams, stageSet, stageParam, STAGES, hasDateWindow, compareOn, cmpView, applyCompareState, refreshCmp, qs, cleanParams } from './util.js';
import { api, AuthError } from './api.js';

const PRESETS = [['launch', 'Since launch'], ['1d', '1d'], ['7d', '7d'], ['14d', '14d'], ['30d', '30d'], ['60d', '60d'], ['90d', '90d'], ['180d', '180d'], ['mtd', 'MTD']];
const COMPARES = [['off', 'Off'], ['prev', 'Previous period'], ['7d', '7d'], ['14d', '14d'], ['30d', '30d'], ['60d', '60d'], ['90d', '90d'], ['custom', 'Custom range']];
const STAGE_LABELS = { testing: 'Testing', scaling: 'Scaling', unclassified: 'Unclassified' };

let topbar, controls, statusEl, userEl, labelEl, noteEl, fromEl, toEl, errEl, backBtn;
let cmpSel, cfromEl, ctoEl, cmpCustomBox, perDayWrap, perDayCb, cerrEl;
let presetBtns = [], stageBtns = {};
let activeBtn, activeCountEl;
let status = null;
let uncVisible = false;
let pendingNote = null;
let authDisabled = false;
let stageNote = '';
export const getStageNote = () => stageNote;
let controlsOn = false;
export const isAuthDisabled = () => authDisabled;
// Log out + user name only exist when the deployment has logins (status.auth !== 'disabled')
function applyAuthUi() {
  const lo = topbar && topbar.querySelector('#logout');
  if (lo) lo.hidden = !controlsOn || authDisabled;
  if (userEl) userEl.hidden = !controlsOn || authDisabled || !userEl.textContent;
}

export function setPendingNote(t) { pendingNote = t; }

export function buildShell({ onBack }) {
  topbar = document.getElementById('topbar');
  controls = document.getElementById('controls');

  statusEl = h('span', { class: 'status', id: 'status' });
  userEl = h('span', { class: 'whoami', id: 'whoami', hidden: true });
  backBtn = h('button', { type: 'button', class: 'btn back', id: 'back', title: 'Back to the previous view', onclick: onBack }, '← Back');
  topbar.replaceChildren(h('div', { class: 'topbar-in' },
    backBtn,
    h('a', { class: 'brand', href: '/' }, 'Tag Explorer'),
    h('a', { class: 'navlink', href: '/' }, 'Products'),
    h('span', { class: 'spacer' }),
    statusEl,
    userEl,
    h('button', { type: 'button', class: 'btn small', id: 'logout', onclick: logout }, 'Log out'),
  ));

  presetBtns = PRESETS.map(([val, text]) => {
    const b = h('button', { type: 'button', class: 'seg', 'data-window': val, 'aria-pressed': 'false' }, text);
    b.addEventListener('click', () => {
      const p = currentParams();
      if (val === 'launch' && compareOn(p)) {
        pendingNote = 'Comparison turned off: Since launch has no date range to compare.';
        setParams({ window: 'launch', from: null, to: null, compare: null, cfrom: null, cto: null, perday: null });
      } else setParams({ window: val, from: null, to: null });
    });
    return b;
  });
  fromEl = h('input', { type: 'date', id: 'range-from', 'aria-label': 'From date' });
  toEl = h('input', { type: 'date', id: 'range-to', 'aria-label': 'To date' });
  errEl = h('span', { class: 'err', id: 'range-err' });
  const apply = h('button', { type: 'button', class: 'btn small primary', id: 'range-apply' }, 'Apply');
  apply.addEventListener('click', applyCustom);
  for (const el of [fromEl, toEl]) el.addEventListener('keydown', (e) => { if (e.key === 'Enter') applyCustom(); });

  stageBtns = {};
  for (const st of STAGES) {
    const b = h('button', { type: 'button', class: 'schip stage-' + st, 'data-stage': st, 'aria-pressed': 'true' }, STAGE_LABELS[st]);
    b.addEventListener('click', () => toggleStage(st));
    stageBtns[st] = b;
  }

  activeBtn = h('button', { type: 'button', class: 'schip active-chip', id: 'active-chip', 'aria-pressed': 'false', title: 'Only creatives with active ads; numbers are for their active ads' }, 'Active only');
  activeBtn.addEventListener('click', () => setParams({ active: currentParams().active === '1' ? null : '1' }));
  activeCountEl = h('span', { class: 'muted small', id: 'active-count' });
  cmpSel = h('select', { id: 'cmp', 'aria-label': 'Compare to' }, COMPARES.map(([v, t]) => h('option', { value: v }, t)));
  cmpSel.addEventListener('change', onCompareChange);
  cfromEl = h('input', { type: 'date', id: 'cmp-from', 'aria-label': 'Compare from date' });
  ctoEl = h('input', { type: 'date', id: 'cmp-to', 'aria-label': 'Compare to date' });
  cerrEl = h('span', { class: 'err', id: 'cmp-err' });
  const capply = h('button', { type: 'button', class: 'btn small primary', id: 'cmp-apply' }, 'Apply');
  capply.addEventListener('click', applyCustomCompare);
  for (const el of [cfromEl, ctoEl]) el.addEventListener('keydown', (e) => { if (e.key === 'Enter') applyCustomCompare(); });
  cmpCustomBox = h('span', { class: 'ctl', id: 'cmp-custom', hidden: true }, cfromEl, h('span', { class: 'muted' }, '–'), ctoEl, capply, cerrEl);
  perDayCb = h('input', { type: 'checkbox', id: 'perday' });
  perDayCb.addEventListener('change', onPerDay);
  perDayWrap = h('label', { class: 'check', id: 'perday-wrap', hidden: true }, perDayCb, ' Per day');

  labelEl = h('strong', { class: 'range-label', id: 'range-label' });
  noteEl = h('div', { class: 'range-note', id: 'range-note' });

  controls.replaceChildren(h('div', { class: 'controls-in' },
    h('div', { class: 'ctl-row' },
      h('div', { class: 'ctl' }, h('span', { class: 'ctl-name' }, 'Range'), h('span', { class: 'segs', role: 'group', 'aria-label': 'Date range' }, presetBtns)),
      h('div', { class: 'ctl' }, h('span', { class: 'ctl-name' }, 'Custom'), fromEl, h('span', { class: 'muted' }, '–'), toEl, apply, errEl),
      h('div', { class: 'ctl' }, h('span', { class: 'ctl-name' }, 'Stage'), h('span', { class: 'chips', role: 'group', 'aria-label': 'Stage' }, Object.values(stageBtns), activeBtn), activeCountEl),
      h('div', { class: 'ctl' }, h('span', { class: 'ctl-name' }, 'Compare to'), cmpSel, cmpCustomBox, perDayWrap),
    ),
    h('div', { class: 'ctl-row second' }, labelEl),
    noteEl,
  ));
}

// ---------- handlers ----------
function applyCustom() {
  errEl.textContent = '';
  let from = fromEl.value, to = toEl.value;
  if (!from && !to) { errEl.textContent = 'Pick a date'; return; }
  if (!from) from = to;
  if (!to) to = from; // single day = same date twice
  if (from > to) { errEl.textContent = 'From is after To'; return; }
  setParams({ from, to, window: null });
}

function toggleStage(st) {
  const p = currentParams();
  const next = new Set(stageSet(p));
  if (next.has(st)) next.delete(st); else next.add(st);
  const considered = uncVisible ? STAGES : ['testing', 'scaling'];
  if (!considered.some((x) => next.has(x))) return; // at least one stays on
  setParams({ stage: stageParam(next, uncVisible) });
}

function aIsLaunch(p) { return !hasDateWindow(p); }

function changeCompare(extra) {
  const p = currentParams();
  const changes = { ...extra, perday: null };
  if (aIsLaunch(p)) {
    changes.window = '7d'; changes.from = null; changes.to = null;
    pendingNote = 'Window A switched to 7d: comparison needs a date window.';
  }
  setParams(changes);
}
function onCompareChange() {
  const v = cmpSel.value;
  cerrEl.textContent = '';
  if (v === 'custom') { cmpCustomBox.hidden = false; return; }
  cmpCustomBox.hidden = true;
  if (v === 'off') setParams({ compare: null, cfrom: null, cto: null, perday: null });
  else changeCompare({ compare: v, cfrom: null, cto: null });
}
function applyCustomCompare() {
  cerrEl.textContent = '';
  let from = cfromEl.value, to = ctoEl.value;
  if (!from && !to) { cerrEl.textContent = 'Pick a date'; return; }
  if (!from) from = to;
  if (!to) to = from;
  if (from > to) { cerrEl.textContent = 'From is after To'; return; }
  changeCompare({ compare: null, cfrom: from, cto: to });
}
function onPerDay() {
  cmpView.perDay = perDayCb.checked;
  const u = new URL(location.href);
  u.searchParams.set('perday', cmpView.perDay ? '1' : '0');
  navigate(u.pathname + u.search, { replace: true, render: false });
  refreshCmp();
}

async function logout() {
  try { await api('/api/logout', null, { method: 'POST' }); } catch { /* ignore */ }
  status = null;
  navigate('/login', { replace: true });
}

// ---------- state ----------
// yellow strip under the header when the data is stale (not dismissible, hidden when fresh)
function renderStaleBanner(st) {
  const el = document.getElementById('stale-banner');
  if (!el) return;
  const stale = st && st.stale && st.stale.is_stale;
  if (!stale || !controlsOn) { el.hidden = true; el.textContent = ''; return; }
  const when = st.last_ok_at ? fmt.dateTime(st.last_ok_at) : 'unknown';
  el.textContent = `Data may be out of date: ${st.stale.reason || 'the last refresh did not complete'}. Last successful refresh ${when}; data through ${fmt.day(st.data_through)}.`;
  el.hidden = false;
}

export function showControls(on) {
  controls.hidden = !on;
  const src = document.getElementById('source');
  if (src && !on) src.hidden = true;
  document.body.classList.toggle('no-controls', !on);
  controlsOn = on;
  applyAuthUi();
  renderStaleBanner(status);
}
export function setBackDisabled(disabled) {
  if (backBtn) backBtn.disabled = !!disabled;
}

function syncStageChips(p) {
  const set = stageSet(p);
  const explicit = !!(p.stage && p.stage !== 'all');
  for (const st of STAGES) stageBtns[st].setAttribute('aria-pressed', String(set.has(st)));
  // Unclassified chip: only when the window has unclassified spend, or the URL already names it
  stageBtns.unclassified.hidden = !(uncVisible || (explicit && set.has('unclassified')));
}

// reflect URL state in the controls immediately; label/notice arrive with the response (setRangeInfo)
export function syncControls(p) {
  const custom = !!(p.from && p.to);
  const win = custom ? null : (p.window || 'launch');
  presetBtns.forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.window === win)));
  syncStageChips(p);
  activeBtn.setAttribute('aria-pressed', String(p.active === '1'));
  activeCountEl.textContent = '';
  fromEl.value = custom ? p.from : '';
  toEl.value = custom ? p.to : '';
  errEl.textContent = '';
  cerrEl.textContent = '';
  // compare
  const ccustom = !!(p.cfrom && p.cto);
  cmpSel.value = ccustom ? 'custom' : (COMPARES.some(([v]) => v === p.compare) ? p.compare : (p.compare ? 'off' : 'off'));
  cmpCustomBox.hidden = !(ccustom || cmpSel.value === 'custom');
  cfromEl.value = ccustom ? p.cfrom : cfromEl.value;
  ctoEl.value = ccustom ? p.cto : ctoEl.value;
  perDayWrap.hidden = !compareOn(p);
  labelEl.textContent = '';
  noteEl.replaceChildren();
  noteEl.className = 'range-note';
  if (status) {
    for (const el of [fromEl, toEl, cfromEl, ctoEl]) { el.min = status.daily_start || ''; el.max = status.data_through || ''; }
  }
}

function rangeText(info) {
  if (!info) return '';
  if (info.kind === 'launch' || !info.from) return info.label || '';
  const n = info.days;
  const span = info.from === info.to ? fmt.day(info.from) : `${fmt.day(info.from)} – ${fmt.day(info.to)}`;
  return `${span} (${n} day${n === 1 ? '' : 's'})`;
}

// a = window_info, b = compare_info (or null). opts.unclassified: the window has unclassified spend (shows that chip)
export function setRangeInfo(p, a, b, { unclassified = false } = {}) {
  applyCompareState(p, a, b);
  if (unclassified !== uncVisible) { uncVisible = unclassified; syncStageChips(p); }
  perDayWrap.hidden = !cmpView.on;
  perDayCb.checked = cmpView.perDay;
  stageNote = (a && a.stage_note) || '';
  for (const st of STAGES) { if (stageNote) stageBtns[st].title = stageNote; else stageBtns[st].removeAttribute('title'); }
  activeBtn.setAttribute('aria-pressed', String(p.active === '1'));
  activeCountEl.textContent = p.active === '1' && a && isNum(a.active_ads) ? `${fmt.int(a.active_ads)} active ads` : '';
  if (!a) { labelEl.textContent = ''; noteEl.replaceChildren(); return; }
  labelEl.textContent = b ? `${rangeText(a)} vs ${rangeText(b)}` : (a.label || '');
  const notes = [];
  if (pendingNote) { notes.push(['info', pendingNote]); pendingNote = null; }
  if (a.note) notes.push([a.clamped ? 'warn' : 'info', (a.clamped ? 'Range clamped: ' : '') + a.note]);
  if (b && b.note) notes.push([b.clamped ? 'warn' : 'info', (b.clamped ? 'Compare range clamped: ' : '') + b.note]);
  noteEl.replaceChildren(...notes.map(([cls, t]) => h('span', { class: 'note ' + cls }, t)));
  noteEl.className = 'range-note' + (notes.some((n) => n[0] === 'warn') ? ' warn' : (notes.length ? ' info' : ''));
}
// back-compat name used by older pages
export function setWindowInfo(info) { setRangeInfo(currentParams(), info, null); }

// B window for endpoints that return a bare array (/api/products)
export function windowInfoFallback(p, st) {
  if (!st) return null;
  const parse = (s) => { const [y, m, d] = s.split('-').map(Number); return Date.UTC(y, m - 1, d); };
  const iso = (ms) => new Date(ms).toISOString().slice(0, 10);
  const build = (from, to, kind) => {
    let clamped = false;
    if (st.daily_start && from < st.daily_start) { from = st.daily_start; clamped = true; }
    if (to > st.data_through) { to = st.data_through; clamped = true; }
    if (from > to) from = to;
    const days = Math.round((parse(to) - parse(from)) / 86400000) + 1;
    const label = from === to ? `${fmt.day(from)} · 1 day` : `${fmt.day(from)} – ${fmt.day(to)} · ${days} days`;
    return { kind, label, from, to, days, clamped, note: clamped ? `Daily data runs ${st.daily_start} to ${st.data_through}; range clamped.` : null };
  };
  const through = st.data_through;
  const trailing = (w) => (w === 'mtd' ? build(through.slice(0, 8) + '01', through, 'mtd') : build(iso(parse(through) - (parseInt(w, 10) - 1) * 86400000), through, 'trailing'));
  let a;
  if (p.from && p.to) a = build(p.from, p.to, 'custom');
  else {
    const w = p.window || 'launch';
    if (w === 'launch') return { a: { kind: 'launch', label: 'Since launch', from: null, to: null }, b: null };
    a = trailing(w);
  }
  let b = null;
  if (p.cfrom && p.cto) b = build(p.cfrom, p.cto, 'custom');
  else if (p.compare === 'prev') b = build(iso(parse(a.from) - a.days * 86400000), iso(parse(a.from) - 86400000), 'prev');
  else if (p.compare) b = trailing(p.compare);
  return { a, b };
}

export async function loadStatus(signal) {
  try {
    status = await api('/api/status', null, { signal });
    authDisabled = status.auth === 'disabled';
    if (userEl) userEl.textContent = status.user || '';
    applyAuthUi();
    const lr = status.last_refresh;
    statusEl.replaceChildren(
      h('span', { id: 'data-through' }, 'Data through ' + fmt.day(status.data_through)),
      lr && lr.finished_at ? h('span', { class: 'muted' }, ` · refreshed ${fmt.dateTime(lr.finished_at)}`) : null,
    );
    renderStaleBanner(status);
    for (const el of [fromEl, toEl, cfromEl, ctoEl]) { el.min = status.daily_start || ''; el.max = status.data_through || ''; }
    const src = document.getElementById('source');
    if (src) {
      src.textContent = 'Source: Meta Marketing API' + (lr && lr.finished_at ? ', refreshed ' + fmt.dateTime(lr.finished_at) : '') + (status.data_through ? ' · data through ' + fmt.day(status.data_through) : '');
      src.hidden = false;
    }
    return status;
  } catch (e) {
    if (e instanceof AuthError) throw e;
    statusEl.textContent = '';
    return null;
  }
}
export function getStatus() { return status; }
export async function ensureStatus(signal) { return status || loadStatus(signal); }
export function clearStatus() { status = null; renderStaleBanner(null); if (statusEl) statusEl.textContent = ''; if (userEl) { userEl.textContent = ''; userEl.hidden = true; } const src = document.getElementById('source'); if (src) src.hidden = true; }
export { cleanParams, qs };
