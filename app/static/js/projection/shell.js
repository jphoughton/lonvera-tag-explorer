// Projection shell: top bar, scenario picker + history drawer, module nav, headline strip, routing, module loader.
// URL: /projection  (overview)  |  /projection/<module>?scenario=<slug>  (a module page). Deep links work; history.pushState navigation.
// A module page is /static/js/projection/<ui> (ui comes from GET /api/projection/modules) exporting
//   async function render(el, ctx)  ->  may return a cleanup function. See docs/projection/MODULE_ui_shell.md.
import { h, $, qs, dash, debounce, toast, askText, confirmBox, confirmTyped, setChildren, ui } from './lib/util.js';
import * as api from './lib/api.js';
import { scenarios } from './lib/scenario.js';
import { Grid, createGrid } from './lib/grid.js';
import * as F from './lib/format.js';
import * as charts from './lib/charts.js';

const GROUP_NAMES = { P1: 'Plan', P2: 'Supply and cash', P3: 'Track' };
const PURPOSE = {
  cohorts: 'Repeat-purchase curves and repeat revenue by month.',
  drivers: 'Ad spend plan, new-customer revenue and Amazon.',
  pnl: 'Profit and loss, margins and implied value.',
  inventory_demand: 'Units shipped per day and the demand forecast.',
  inventory_supply: 'Purchase orders, stock burn and when to reorder.',
  financing: 'Debt, cash after inventory, payback and payouts.',
  pacing: 'This month against plan, and Meta spend by product.',
};
const DEMO = { key: '_demo', label: 'Library demo', group: 'Demo', built: true, ui: '_demo.js' };
const view = $('#view');

let modules = [];
let token = 0;
let cleanup = null;
let ctxOff = [];          // listeners registered through ctx.on*, removed at teardown
let headlineGen = 0;

// ------------------------------------------------------------------ location helpers
function parseLoc() {
  const m = /^\/projection(?:\/([^/]+))?(?:\/.*)?$/.exec(location.pathname);
  return { seg: m && m[1] ? decodeURIComponent(m[1]) : null, params: Object.fromEntries(new URLSearchParams(location.search)) };
}
const findModule = (seg, params) => {
  if (seg === '_demo') return params.demo === '1' ? DEMO : null;
  return modules.find((m) => dash(m.key) === dash(seg)) || null;
};
const moduleHref = (m, extra) => `/projection/${dash(m.key)}` + qs({ scenario: scenarios.slug, ...(extra || {}) });
const homeHref = () => '/projection/overview' + qs({ scenario: scenarios.slug });     // the Overview page; /projection itself lands on Pacing
const curSeg = () => { const { seg } = parseLoc(); return seg === 'overview' ? null : seg; };   // null = Overview

function go(url, { replace = false } = {}) {
  if (replace) history.replaceState(history.state, '', url); else history.pushState({}, '', url);
  return route();
}
function syncUrlScenario() {
  if (!scenarios.slug) return;
  const u = new URL(location.href);
  if (u.searchParams.get('scenario') === scenarios.slug) return;
  u.searchParams.set('scenario', scenarios.slug);
  history.replaceState(history.state, '', u.pathname + u.search);
}

// ------------------------------------------------------------------ one bar: Lonvera | Tags | Projections | module tabs | scenario control
// The static part of the bar (brand, Tags, Projections) is in projection.html and mirrors the Tags pages. This file fills the module tabs and the scenario control.

// small popup menu (role=menu): click or Enter opens, arrows move, Esc and outside click close
let openMenu = null;
function closeMenu(restoreFocus = false) {
  if (!openMenu) return;
  const { btn, pop } = openMenu;
  pop.hidden = true;
  btn.setAttribute('aria-expanded', 'false');
  openMenu = null;
  if (restoreFocus) btn.focus();
}
function wireMenu(btn, pop) {
  btn.setAttribute('aria-haspopup', 'menu');
  btn.setAttribute('aria-expanded', 'false');
  pop.setAttribute('role', 'menu');
  pop.hidden = true;
  const items = () => [...pop.querySelectorAll('[role="menuitem"]')].filter((x) => !x.hidden && !x.disabled);
  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    if (openMenu && openMenu.btn === btn) { closeMenu(true); return; }
    closeMenu();
    pop.hidden = false;
    btn.setAttribute('aria-expanded', 'true');
    openMenu = { btn, pop };
    const cur = pop.querySelector('[aria-current="page"]') || items()[0];
    if (cur) cur.focus();
  });
  pop.addEventListener('click', (e) => { if (e.target.closest('[role="menuitem"]')) closeMenu(); });
  pop.addEventListener('keydown', (e) => {
    const list = items();
    const i = list.indexOf(document.activeElement);
    if (e.key === 'ArrowDown') { e.preventDefault(); (list[i + 1] || list[0]).focus(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); (list[i - 1] || list[list.length - 1]).focus(); }
    else if (e.key === 'Home') { e.preventDefault(); list[0].focus(); }
    else if (e.key === 'End') { e.preventDefault(); list[list.length - 1].focus(); }
    else if (e.key === 'Escape') { e.preventDefault(); closeMenu(true); }
    else if (e.key === 'Tab') closeMenu();
  });
}
document.addEventListener('click', (e) => { if (openMenu && !openMenu.pop.contains(e.target) && !openMenu.btn.contains(e.target)) closeMenu(); });

const bar = $('#pg-bar');
function buildBar() {
  closeMenu();
  const cur = scenarios.current;
  const sel = h('select', { id: 'pg-scenario', 'aria-label': 'Scenario' },
    scenarios.list.map((s) => h('option', { value: s.slug, selected: s.slug === scenarios.slug }, s.name)));
  sel.addEventListener('change', () => scenarios.select(sel.value));
  const isDefault = !!cur && (cur.is_default || cur.slug === 'current');
  const item = (act, text, extra = {}) => h('button', { type: 'button', role: 'menuitem', class: 'pg-mitem' + (extra.danger ? ' danger' : ''), 'data-act': act, onclick: () => actions[act](), disabled: extra.disabled, hidden: extra.hidden, title: extra.title }, text);
  const pop = h('div', { class: 'pg-menu-pop', id: 'pg-menu-pop', 'aria-label': 'Scenario actions' },
    item('new', 'New scenario'),
    item('clone', 'Clone this scenario', { disabled: !cur }),
    item('rename', 'Rename', { disabled: !cur }),
    item('history', 'History and restore', { disabled: !cur, title: 'Versions of this scenario' }),
    isDefault ? null : h('div', { class: 'pg-msep', role: 'separator' }),
    isDefault ? null : item('delete', 'Delete this scenario', { danger: true, disabled: !cur, title: 'Delete this scenario' }));
  const mbtn = h('button', { type: 'button', class: 'btn small pg-menubtn', id: 'pg-menu-btn', 'aria-label': 'Scenario actions', title: 'New, clone, rename, history' },
    h('span', { 'aria-hidden': 'true' }, '⋯'));
  wireMenu(mbtn, pop);
  bar.replaceChildren(
    sel,
    h('span', { class: 'pg-ver', id: 'pg-version', title: cur && cur.updated_at ? 'Updated ' + F.dateTime(cur.updated_at) : null }, cur ? 'v' + cur.version : ''),
    h('span', { class: 'pg-mode', id: 'pg-mode', title: 'You are in a copy. Edits here do not change the plan of record.' }, 'Copy'),
    h('div', { class: 'pg-menu' }, mbtn, pop));
  document.body.dataset.pgScenario = isDefault ? 'default' : 'copy';
  bar.hidden = false;
}
function updateVersionChip() {
  const el = $('#pg-version');
  const cur = scenarios.current;
  if (el && cur) { el.textContent = 'v' + cur.version; el.title = cur.updated_at ? 'Updated ' + F.dateTime(cur.updated_at) : ''; }
}

const actions = {
  async new() {
    await askText({ title: 'New scenario', label: 'Name', confirmText: 'Create', onSubmit: (name) => scenarios.create(name) });
  },
  async clone() {
    const cur = scenarios.current;
    if (!cur) return;
    await askText({ title: 'Clone scenario', label: 'Name for the copy', value: `${cur.name} copy`, confirmText: 'Clone', onSubmit: (name) => scenarios.create(name, cur.slug) });
  },
  async rename() {
    const cur = scenarios.current;
    if (!cur) return;
    await askText({ title: 'Rename scenario', label: 'Name', value: cur.name, confirmText: 'Rename', onSubmit: (name) => scenarios.rename(cur.slug, name) });
  },
  async delete() {
    const cur = scenarios.current;
    if (!cur || cur.is_default || cur.slug === 'current') return;
    const ok = await confirmTyped({
      title: `Delete "${cur.name}"?`,
      message: 'This removes the scenario and all of its inputs. Other scenarios are not affected.',
      expect: cur.slug,
      confirmText: 'Delete scenario',
      onConfirm: () => { refreshHeadlineSoon.cancel(); headlineGen++; return scenarios.remove(cur.slug, cur.slug); },   // no headline request for a scenario that is going away
    });
    if (ok) toast(`Deleted ${cur.name}`, 'ok');
  },
  history: () => openHistory(),
};

const nav = $('#pg-nav');
const navMenu = $('#pg-navmenu');
function buildNav() {
  const seg = curSeg();
  const pacing = modules.find((m) => dash(m.key) === 'pacing' && m.built);       // Pacing is the first tab and the default page
  const groups = [];
  for (const m of modules) {
    if (m === pacing) continue;
    let g = groups.find((x) => x.name === m.group);
    if (!g) { g = { name: m.group, items: [] }; groups.push(g); }
    g.items.push(m);
  }
  groups.sort((a, b) => String(a.name).localeCompare(String(b.name)));
  // wide screens: tabs inline in the bar (groups are thin dividers, no labels)
  setChildren(nav,
    pacing ? h('div', { class: 'pg-ngroup', 'data-group': 'pacing' }, h('a', { class: 'pg-tab', href: moduleHref(pacing), 'data-pg': '', 'data-key': pacing.key, 'data-built': 'true', 'aria-current': seg && dash(pacing.key) === dash(seg) ? 'page' : null }, pacing.label)) : null,
    h('div', { class: 'pg-ngroup' }, h('a', { class: 'pg-tab', href: homeHref(), 'data-pg': '', 'data-key': '', 'aria-current': !seg ? 'page' : null }, 'Overview')),
    groups.map((g) => h('div', { class: 'pg-ngroup', 'data-group': g.name },
      g.items.map((m) => h('a', {
        class: 'pg-tab', href: moduleHref(m), 'data-pg': '', 'data-key': m.key, 'data-built': String(!!m.built),
        'aria-current': seg && dash(m.key) === dash(seg) ? 'page' : null, title: m.built ? null : 'Not built yet',
      }, m.label, m.built ? null : h('span', { class: 'soon' }, 'soon'))))));
  nav.hidden = false;
  // narrow screens: one "Projections" menu in the bar lists the same pages, grouped
  const pop = h('div', { class: 'pg-menu-pop pg-nav-pop', id: 'pg-nav-pop', 'aria-label': 'Projection pages' },
    pacing ? h('a', { class: 'pg-mitem', role: 'menuitem', href: moduleHref(pacing), 'data-pg': '', 'data-key': pacing.key }, pacing.label) : null,
    h('a', { class: 'pg-mitem', role: 'menuitem', href: homeHref(), 'data-pg': '', 'data-key': '' }, 'Overview'),
    groups.map((g) => [
      h('div', { class: 'pg-mhead', role: 'presentation' }, GROUP_NAMES[g.name] || g.name),
      g.items.map((m) => h('a', { class: 'pg-mitem' + (m.built ? '' : ' quiet'), role: 'menuitem', href: moduleHref(m), 'data-pg': '', 'data-key': m.key }, m.label, m.built ? null : h('span', { class: 'soon' }, 'soon'))),
    ]));
  const btn = h('button', { type: 'button', class: 'btn small pg-navbtn', id: 'pg-navbtn', 'aria-label': 'Projection pages' },
    h('span', { class: 'pg-navbtn-label', id: 'pg-navbtn-label' }, 'Projections'), h('span', { 'aria-hidden': 'true', class: 'pg-chev' }, '▾'));
  wireMenu(btn, pop);
  setChildren(navMenu, btn, pop);
  navMenu.hidden = false;
}
function markNav() {
  const seg = curSeg();
  let label = 'Overview';
  for (const a of document.querySelectorAll('#pg-nav a[data-key], #pg-navmenu a[data-key]')) {
    const on = seg ? dash(a.dataset.key) === dash(seg) : a.dataset.key === '';
    if (on) { a.setAttribute('aria-current', 'page'); label = a.dataset.key === '' ? 'Overview' : (modules.find((m) => dash(m.key) === dash(a.dataset.key)) || {}).label || label; }
    else a.removeAttribute('aria-current');
    if (a.dataset.key !== undefined) a.href = a.dataset.key === '' ? homeHref() : moduleHref({ key: a.dataset.key });
  }
  const nb = $('#pg-navbtn');
  if (nb) nb.title = `Projections: ${label}`;
}

// ------------------------------------------------------------------ headline strip
const strip = $('#pg-headline');
const title = (k) => String(k).replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());
function guessFmt(key, v) {
  const k = String(key || '').toLowerCase();
  if (/pct|rate|margin|share|growth|percent/.test(k)) return 'pct';
  if (/roas|multiple|ratio|_x$/.test(k)) return 'x';
  if (/orders|units|customers|count/.test(k)) return 'num';
  return Number.isFinite(v) ? 'usd0' : 'text';
}
function pickYear(years, through) {
  const ys = years.map(Number).filter(Number.isFinite).sort((x, y) => x - y);
  if (!ys.length) return null;
  const base = through ? F.yearOf(through) : new Date().getFullYear();
  return ys.includes(base + 1) ? base + 1 : ys[ys.length - 1];
}
// GET /run -> headline: {fy:{'2027':{revenue,noi}}, ev:{...}, lifetime_multiple, lowest_ending_cash:{month,value}}
// also accepts an array of {key,label,value,fmt} or a flat object of numbers
function headlineItems(r) {
  const raw = r && (r.headline ?? r.headlines ?? r.summary ?? r.kpis);
  let items = [];
  if (Array.isArray(raw)) {
    items = raw.map((x) => ({ label: x.label || title(x.key), value: x.value, fmt: x.fmt || guessFmt(x.key, x.value) }));
  } else if (raw && typeof raw === 'object') {
    const done = new Set();
    if (raw.fy && typeof raw.fy === 'object') {
      done.add('fy');
      const y = pickYear(Object.keys(raw.fy), r.actual_through);
      const f = y != null ? raw.fy[String(y)] : null;
      const yy = 'FY' + String(y).slice(-2);
      if (f && typeof f.revenue === 'number') items.push({ label: `${yy} revenue`, value: f.revenue, fmt: 'usd0' });
      if (f && typeof f.noi === 'number') items.push({ label: `${yy} NOI`, value: f.noi, fmt: 'usd0' });
    }
    if (raw.ev && typeof raw.ev === 'object') {
      done.add('ev');
      const vals = Object.entries(raw.ev).filter(([k, v]) => typeof v === 'number' && /^\d+(\.\d+)?x?$/i.test(k)).map(([, v]) => v);
      if (vals.length >= 2) items.push({ label: 'Implied EV', value: `${F.compact(Math.min(...vals))} to ${F.compact(Math.max(...vals))}`, fmt: 'text', zero: Math.min(...vals) === 0 && Math.max(...vals) === 0 });
    }
    if (typeof raw.lifetime_multiple === 'number') { done.add('lifetime_multiple'); items.push({ label: 'Lifetime repeat', value: raw.lifetime_multiple, fmt: 'x' }); }
    const low = raw.lowest_ending_cash;
    if (low && typeof low.value === 'number') {
      done.add('lowest_ending_cash');
      items.push({ label: 'Lowest ending cash' + (low.month ? ' · ' + F.monthLabel(low.month) : ''), value: low.value, fmt: 'usd0' });
    }
    for (const [k, v] of Object.entries(raw)) if (!done.has(k) && typeof v === 'number') items.push({ label: title(k), value: v, fmt: guessFmt(k, v) });
  }
  return items.filter((x) => x.value != null).map((x) => ({ ...x, zero: x.zero ?? x.value === 0 })).slice(0, 6);
}
const kpiText = (it) => (typeof it.value !== 'number' ? String(it.value) : (it.fmt === 'usd0' || it.fmt === 'usd') ? F.compact(it.value) : F.display(it.value, it.fmt).text);
async function refreshHeadline() {
  const gen = ++headlineGen;
  if (!scenarios.slug) { strip.hidden = true; return; }
  let r = null;
  try { r = await api.get('/run', { scenario: scenarios.slug }); } catch { r = null; }
  if (gen !== headlineGen) return;
  const through = r && (r.actual_through || r.actualThrough);
  const items = headlineItems(r);
  // nothing real to show (no module built, or every figure is zero): hide the strip rather than print $0
  if (!modules.some((m) => m.built) || !items.length || items.every((it) => it.zero)) { strip.hidden = true; strip.replaceChildren(); return; }
  setChildren(strip,
    through ? h('span', { class: 'pg-actual', id: 'pg-actual-through' }, 'Actual through ' + F.monthLabel(through)) : null,
    items.map((it) => h('div', { class: 'pg-kpi' }, h('span', { class: 'k' }, it.label), h('span', { class: 'v' + (it.value < 0 ? ' neg' : '') }, kpiText(it)))));
  strip.hidden = false;
}
const refreshHeadlineSoon = debounce(refreshHeadline, 400);

// ------------------------------------------------------------------ history drawer
const drawer = $('#pg-drawer');
let drawerOpener = null;
const showVal = (x) => (x == null ? 'unset' : typeof x === 'object' ? ('value' in x ? showVal(x.value) : JSON.stringify(x).slice(0, 60)) : typeof x === 'number' ? F.num(x) : String(x));
function describe(e) {
  if (e.summary || e.detail || e.description) return e.summary || e.detail || e.description;
  if (e.action === 'scenario.rename' && e.old && e.new && typeof e.new === 'object' && 'name' in e.new) return `Renamed: ${e.old.name} → ${e.new.name}`;
  if (e.line_key) return `${e.line_key}${e.month ? ' ' + F.monthLabel(e.month) : ''}: ${showVal(e.old ?? e.old_value)} → ${showVal(e.new ?? e.new_value)}`;
  return String(e.action || e.type || 'Change').replace(/[._]/g, ' ') + (e.note ? ': ' + e.note : '');
}
const ACTION_LABEL = { series: 'Edited inputs', restore: 'Restored', create: 'Created', rename: 'Renamed', seed: 'Seeded', update: 'Changed settings' };
const ts = (x) => { const t = Date.parse(x); return Number.isFinite(t) ? t : null; };
async function openHistory() {
  const cur = scenarios.current;
  if (!cur) return;
  drawerOpener = document.activeElement;
  const body = h('div', { class: 'pg-drawer-body' }, h('div', { class: 'loading' }, 'Loading…'));
  const closeBtn = h('button', { type: 'button', class: 'btn small', id: 'pg-drawer-close', onclick: closeHistory }, 'Close');
  drawer.replaceChildren(h('div', { class: 'pg-drawer-head' }, h('h2', null, `History · ${cur.name}`), closeBtn), body);
  drawer.hidden = false;
  closeBtn.focus();
  await fillHistory(body);
}
async function fillHistory(body) {
  const cur = scenarios.current;
  let hist;
  try { hist = await scenarios.history(cur.slug); } catch (e) { ui.error(body, e, () => fillHistory(body)); return; }
  const versions = [...hist.versions].sort((a, b) => Number(b.version) - Number(a.version));
  if (!versions.length && !hist.audit.length) { ui.empty(body, 'No history yet.'); return; }
  // audit rows carry the version they produced; rows without one fall back to the version written at the same time (within 2 s)
  const claimed = new Set();
  const detail = new Map();
  for (const v of versions) {
    const t = ts(v.at);
    const rows = hist.audit.filter((a) => !claimed.has(a) && (a.version != null
      ? Number(a.version) === Number(v.version)
      : (t != null && ts(a.at) != null && Math.abs(ts(a.at) - t) < 2000)));
    rows.forEach((a) => claimed.add(a));
    detail.set(v, rows);
  }
  const loose = hist.audit.filter((a) => !claimed.has(a));
  const verItem = (v) => {
    const isNow = Number(v.version) === Number(cur.version);
    const rows = detail.get(v) || [];
    const shown = rows.slice(0, 4);
    return h('li', { 'data-version': v.version },
      h('div', { class: 'when' }, F.dateTime(v.at) + (rows[0] && rows[0].actor ? ' · ' + rows[0].actor : '')),
      h('div', { class: 'what' }, h('strong', null, ACTION_LABEL[v.action] || String(v.action || 'Change')),
        shown.map((a) => h('div', { class: 'sub' }, describe(a))),
        rows.length > shown.length ? h('div', { class: 'sub' }, `and ${rows.length - shown.length} more`) : null),
      h('div', { class: 'act' },
        h('span', { class: 'vchip' + (isNow ? ' now' : '') }, 'v' + v.version),
        isNow ? null : h('button', { type: 'button', class: 'btn small', 'data-act': 'restore', onclick: () => restoreVersion(v.version, body) }, 'Restore')));
  };
  setChildren(body,
    h('ul', { class: 'pg-hist', id: 'pg-hist' }, versions.map(verItem)),
    loose.length ? h('div', { class: 'pg-group-head' }, 'Other activity') : null,
    loose.length ? h('ul', { class: 'pg-hist' }, loose.slice(0, 50).map((a) => h('li', null, h('div', { class: 'when' }, F.dateTime(a.at) + (a.actor ? ' · ' + a.actor : '')), h('div', { class: 'what' }, describe(a))))) : null);
}
async function restoreVersion(version, body) {
  const cur = scenarios.current;
  const ok = await confirmBox({
    title: `Restore version ${version}?`,
    message: `"${cur.name}" goes back to how it was at version ${version}. The current state stays in the history, so this can be undone.`,
    confirmText: 'Restore', danger: false,
    onConfirm: () => scenarios.restore(cur.slug, version),
  });
  if (!ok) return;
  toast(`Restored to version ${version}`, 'ok');
  await fillHistory(body);
}
function closeHistory() {
  drawer.hidden = true;
  drawer.replaceChildren();
  if (drawerOpener && drawerOpener.isConnected) drawerOpener.focus();
}
document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !drawer.hidden && !document.querySelector('dialog[open]')) closeHistory(); });

// ------------------------------------------------------------------ module context
function makeCtx(mod, my) {
  const params = Object.fromEntries(new URLSearchParams(location.search));
  const on = (evt, fn) => { const off = scenarios.on(evt, fn); ctxOff.push(off); return off; };
  return {
    scenario: scenarios.slug,
    scenarioInfo: scenarios.current,
    scenarioVersion: scenarios.version,
    module: { ...mod, dash: dash(mod.key) },
    params,
    setParams(changes) {                                // replaceState, no re-render
      const u = new URL(location.href);
      for (const [k, v] of Object.entries(changes)) { if (v == null || v === '') u.searchParams.delete(k); else u.searchParams.set(k, String(v)); }
      history.replaceState(history.state, '', u.pathname + u.search);
      Object.assign(params, changes);
    },
    api: api.makeContextApi(() => scenarios.slug),
    grid: createGrid, Grid, fmt: F, charts, toast, ui, h,
    alive: () => token === my,
    reload: () => route({ scroll: false }),
    onVersion: (fn) => on('version', fn),
    refreshHeadline: refreshHeadlineSoon,
  };
}

// ------------------------------------------------------------------ routing
async function teardown() {
  const c = cleanup;
  cleanup = null;
  for (const off of ctxOff) off();
  ctxOff = [];
  if (c) { try { await c(); } catch (e) { console.warn('module cleanup failed', e); } }
  charts.destroyAll();
  try { await api.flushSeries(); } catch { /* the grid already reported it */ }
}

function pageHead(titleText, sub) {
  return h('div', { class: 'pg-title' }, h('h1', titleText), sub ? h('span', { class: 'sub' }, sub) : null);
}
function renderHome() {
  const groups = [];
  for (const m of modules) {
    let g = groups.find((x) => x.name === m.group);
    if (!g) { g = { name: m.group, items: [] }; groups.push(g); }
    g.items.push(m);
  }
  groups.sort((a, b) => String(a.name).localeCompare(String(b.name)));
  const cur = scenarios.current;
  view.replaceChildren(h('div', { class: 'pg-page' },
    ui.pageHeader({ title: 'Projections', purpose: cur ? `Where Lonvera lands, by scenario. You are in "${cur.name}". Pick a page to see numbers or change inputs.` : 'Where Lonvera lands, by scenario. Pick a page to see numbers or change inputs.' }),
    groups.map((g) => h('section', { 'data-group': g.name },
      h('div', { class: 'pg-group-head' }, GROUP_NAMES[g.name] || g.name),
      h('div', { class: 'pg-cards' }, g.items.map((m) => {
        const kids = [h('span', { class: 't' }, m.label), PURPOSE[m.key] ? h('span', { class: 's' }, PURPOSE[m.key]) : null, m.built ? null : h('span', { class: 's' }, 'Not built yet')];
        return m.built
          ? h('a', { class: 'pg-mcard', href: moduleHref(m), 'data-pg': '', 'data-key': m.key, 'data-built': 'true' }, kids)
          : h('div', { class: 'pg-mcard', 'data-key': m.key, 'data-built': 'false' }, kids);
      }))))));
}
function renderNotBuilt(mod) {
  view.replaceChildren(h('div', { class: 'pg-page' }, pageHead(mod.label),
    h('div', { class: 'pg-card pg-notbuilt', 'data-state': 'not-built' }, h('strong', null, 'Not built yet. '), h('span', { class: 'muted' }, `${mod.label} is on the build plan and will appear here.`)),
    h('div', null, h('a', { class: 'btnlink', href: homeHref(), 'data-pg': '' }, 'Back to overview'))));
}
function renderNotFound(seg) {
  view.replaceChildren(h('div', { class: 'pg-page' }, pageHead('Not found'),
    h('div', { class: 'empty' }, `There is no projection page called "${seg}".`),
    h('div', null, h('a', { class: 'btnlink', href: homeHref(), 'data-pg': '' }, 'Back to overview'))));
}

async function route({ scroll = true } = {}) {
  const my = ++token;
  document.body.dataset.pgState = 'loading';
  await teardown();
  if (my !== token) return;
  let { seg, params } = parseLoc();
  if (!seg && modules.some((m) => dash(m.key) === 'pacing' && m.built)) {                  // /projection lands on Pacing
    history.replaceState(history.state, '', '/projection/pacing' + location.search);
    seg = 'pacing';
  }
  if (seg === 'overview') seg = null;
  if (params.scenario && scenarios.has(params.scenario) && params.scenario !== scenarios.slug) scenarios.select(params.scenario, { silent: true });
  buildBar();
  syncUrlScenario();
  markNav();
  if (scroll) window.scrollTo(0, 0);

  if (!seg) { document.title = 'Projections · Lonvera'; renderHome(); document.body.dataset.pgState = 'ready'; return; }
  const mod = findModule(seg, params);
  if (!mod) { renderNotFound(seg); document.body.dataset.pgState = 'ready'; return; }
  document.title = `${mod.label} · Projections · Lonvera`;
  if (!mod.built) { renderNotBuilt(mod); document.body.dataset.pgState = 'ready'; return; }
  if (!scenarios.slug) { view.replaceChildren(h('div', { class: 'empty' }, 'No scenario yet. Create one with New.')); document.body.dataset.pgState = 'ready'; return; }
  if (!mod.ui || !/^[A-Za-z0-9_-]+\.js$/.test(mod.ui)) {
    view.replaceChildren(h('div', { class: 'errorbox', role: 'alert' }, `${mod.label} has no page script.`));
    document.body.dataset.pgState = 'error';
    return;
  }

  const el = h('div', { class: 'pg-module', 'data-module': dash(mod.key) });
  view.replaceChildren(el);
  ui.loading(el);
  let page;
  try {
    page = await import(`/static/js/projection/${mod.ui}`);
  } catch (e) {
    if (my !== token) return;
    ui.error(el, new Error(`Page script ${mod.ui} failed to load: ${e.message}`), () => route());
    document.body.dataset.pgState = 'error';
    return;
  }
  if (my !== token) return;
  if (typeof page.render !== 'function') { ui.error(el, new Error(`${mod.ui} does not export render(el, ctx)`)); document.body.dataset.pgState = 'error'; return; }
  try {
    const out = await page.render(el, makeCtx(mod, my));
    if (my !== token) { if (typeof out === 'function') { try { out(); } catch { /* ignore */ } } return; }
    cleanup = typeof out === 'function' ? out : null;
    document.body.dataset.pgState = 'ready';
  } catch (e) {
    if (my !== token) return;
    if (e && (e.name === 'AuthError' || e.name === 'AbortError')) return;
    console.warn('module render failed', e);
    ui.error(el, e, () => route());
    document.body.dataset.pgState = 'error';
  }
}

// ------------------------------------------------------------------ boot
document.addEventListener('click', (e) => {
  if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
  const a = e.target.closest('a[data-pg]');
  if (!a || a.target) return;
  e.preventDefault();
  const u = new URL(a.href, location.href);
  go(u.pathname + u.search);
});
window.addEventListener('popstate', () => route({ scroll: false }));
window.addEventListener('pagehide', () => { api.flushSeries(); });

async function boot() {
  try {
    const [m] = await Promise.all([api.get('/modules'), scenarios.load(parseLoc().params.scenario)]);
    modules = (m && m.modules) || [];
  } catch (e) {
    if (e && e.name === 'AuthError') return;                    // redirected to the Tags login
    view.replaceChildren(h('div', { class: 'errorbox', role: 'alert' }, h('strong', null, 'Could not load Projections. '), h('span', null, e.message || String(e)),
      h('div', null, h('button', { type: 'button', class: 'btn', onclick: () => location.reload() }, 'Retry'))));
    document.body.dataset.pgState = 'error';
    return;
  }
  buildNav();
  scenarios.on('list', () => buildBar());
  scenarios.on('version', () => { updateVersionChip(); refreshHeadlineSoon(); });
  scenarios.on('change', () => { refreshHeadlineSoon.cancel(); syncUrlScenario(); buildBar(); route({ scroll: false }); refreshHeadline(); });
  refreshHeadline();
  await route();
}
boot();
