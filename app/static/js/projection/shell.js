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

const GROUP_NAMES = { P1: 'P1 · Engine', P2: 'P2 · Supply and cash', P3: 'P3 · Pacing' };
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
const homeHref = () => '/projection' + qs({ scenario: scenarios.slug });

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

// ------------------------------------------------------------------ top bar, scenario bar, nav
function buildTopbar() {
  $('#topbar').replaceChildren(h('div', { class: 'topbar-in' },
    h('a', { class: 'brand', href: '/' }, 'Tag Explorer'),
    h('a', { class: 'navlink', href: '/' }, 'Products'),
    h('a', { class: 'navlink pg-brandnow', href: '/projection', 'data-pg': '' }, 'Projections'),
    h('span', { class: 'spacer' })));
}

const bar = $('#pg-bar');
function buildBar() {
  const cur = scenarios.current;
  const sel = h('select', { id: 'pg-scenario', 'aria-label': 'Scenario' },
    scenarios.list.map((s) => h('option', { value: s.slug, selected: s.slug === scenarios.slug }, s.name + (s.is_default ? ' (default)' : ''))));
  sel.addEventListener('change', () => scenarios.select(sel.value));
  const btn = (act, text, extra = {}) => h('button', { type: 'button', class: 'btn small', 'data-act': act, onclick: () => actions[act](), ...extra }, text);
  const isDefault = !!cur && (cur.is_default || cur.slug === 'current');
  bar.replaceChildren(
    h('span', { class: 'ctl-name' }, 'Scenario'),
    sel,
    h('span', { class: 'pg-ver', id: 'pg-version', title: cur && cur.updated_at ? 'Updated ' + F.dateTime(cur.updated_at) : null }, cur ? 'v' + cur.version : ''),
    btn('new', 'New'),
    btn('clone', 'Clone', { disabled: !cur }),
    btn('rename', 'Rename', { disabled: !cur }),
    btn('delete', 'Delete', { disabled: !cur || isDefault, hidden: isDefault, title: 'Delete this scenario' }),
    h('span', { class: 'spacer' }),
    btn('history', 'History', { disabled: !cur, title: 'Versions of this scenario' }));
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
      onConfirm: () => scenarios.remove(cur.slug, cur.slug),
    });
    if (ok) toast(`Deleted ${cur.name}`, 'ok');
  },
  history: () => openHistory(),
};

const nav = $('#pg-nav');
function buildNav() {
  const { seg } = parseLoc();
  const groups = [];
  for (const m of modules) {
    let g = groups.find((x) => x.name === m.group);
    if (!g) { g = { name: m.group, items: [] }; groups.push(g); }
    g.items.push(m);
  }
  groups.sort((a, b) => String(a.name).localeCompare(String(b.name)));
  setChildren(nav,
    h('div', { class: 'pg-ngroup' }, h('a', { class: 'pg-tab', href: homeHref(), 'data-pg': '', 'data-key': '', 'aria-current': !seg ? 'page' : null }, 'Overview')),
    groups.map((g) => h('div', { class: 'pg-ngroup', 'data-group': g.name },
      h('span', { class: 'pg-ghead' }, GROUP_NAMES[g.name] || g.name),
      g.items.map((m) => h('a', {
        class: 'pg-tab', href: moduleHref(m), 'data-pg': '', 'data-key': m.key, 'data-built': String(!!m.built),
        'aria-current': seg && dash(m.key) === dash(seg) ? 'page' : null, title: m.built ? null : 'Not built yet',
      }, m.label, m.built ? null : h('span', { class: 'soon' }, 'soon'))))));
  nav.hidden = false;
}
function markNav() {
  const { seg } = parseLoc();
  for (const a of nav.querySelectorAll('a.pg-tab')) {
    const on = seg ? dash(a.dataset.key) === dash(seg) : a.dataset.key === '';
    if (on) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
    if (a.dataset.key !== undefined) a.href = a.dataset.key === '' ? homeHref() : moduleHref({ key: a.dataset.key });
  }
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
      if (vals.length >= 2) items.push({ label: 'Implied EV', value: `${F.compact(Math.min(...vals))} to ${F.compact(Math.max(...vals))}`, fmt: 'text' });
    }
    if (typeof raw.lifetime_multiple === 'number') { done.add('lifetime_multiple'); items.push({ label: 'Lifetime repeat', value: raw.lifetime_multiple, fmt: 'x' }); }
    const low = raw.lowest_ending_cash;
    if (low && typeof low.value === 'number') {
      done.add('lowest_ending_cash');
      items.push({ label: 'Lowest ending cash' + (low.month ? ' · ' + F.monthLabel(low.month) : ''), value: low.value, fmt: 'usd0' });
    }
    for (const [k, v] of Object.entries(raw)) if (!done.has(k) && typeof v === 'number') items.push({ label: title(k), value: v, fmt: guessFmt(k, v) });
  }
  return items.filter((x) => x.value != null).slice(0, 6);
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
  if (!through && !items.length) { strip.hidden = true; strip.replaceChildren(); return; }
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
    pageHead('Projections', cur ? `Scenario: ${cur.name}` : null),
    groups.map((g) => h('section', { 'data-group': g.name },
      h('div', { class: 'pg-group-head' }, GROUP_NAMES[g.name] || g.name),
      h('div', { class: 'pg-cards' }, g.items.map((m) => {
        const kids = [h('span', { class: 't' }, m.label), h('span', { class: 's' }, m.built ? 'Open' : 'Not built yet')];
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
  const { seg, params } = parseLoc();
  if (params.scenario && scenarios.has(params.scenario) && params.scenario !== scenarios.slug) scenarios.select(params.scenario, { silent: true });
  buildBar();
  syncUrlScenario();
  markNav();
  if (scroll) window.scrollTo(0, 0);

  if (!seg) { document.title = 'Projections · Tag Explorer'; renderHome(); document.body.dataset.pgState = 'ready'; return; }
  const mod = findModule(seg, params);
  if (!mod) { renderNotFound(seg); document.body.dataset.pgState = 'ready'; return; }
  document.title = `${mod.label} · Projections`;
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
  buildTopbar();
  try {
    const [m] = await Promise.all([api.get('/modules'), scenarios.load(parseLoc().params.scenario)]);
    modules = (m && m.modules) || [];
  } catch (e) {
    if (e && e.name === 'AuthError') return;                    // redirected to the Tag Explorer login
    view.replaceChildren(h('div', { class: 'errorbox', role: 'alert' }, h('strong', null, 'Could not load Projections. '), h('span', null, e.message || String(e)),
      h('div', null, h('button', { type: 'button', class: 'btn', onclick: () => location.reload() }, 'Retry'))));
    document.body.dataset.pgState = 'error';
    return;
  }
  buildNav();
  scenarios.on('list', () => buildBar());
  scenarios.on('version', () => { updateVersionChip(); refreshHeadlineSoon(); });
  scenarios.on('change', () => { syncUrlScenario(); buildBar(); route({ scroll: false }); refreshHeadline(); });
  refreshHeadline();
  await route();
}
boot();
