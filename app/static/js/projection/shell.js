// Projection shell: top bar, scenario picker + history drawer, module nav, headline strip, routing, module loader.
// URL: /projection  (overview)  |  /projection/<module>?scenario=<slug>  (a module page). Deep links work; history.pushState navigation.
// A module page is /static/js/projection/<ui> (ui comes from GET /api/projection/modules) exporting
//   async function render(el, ctx)  ->  may return a cleanup function. See docs/projection/MODULE_ui_shell.md.
import { h, $, qs, dash, debounce, toast, askText, confirmBox, confirmTyped, setChildren, ui } from './lib/util.js';
import * as api from './lib/api.js';
import { scenarios } from './lib/scenario.js';
import { installGuard } from './lib/guard.js';
import { Grid, createGrid } from './lib/grid.js';
import * as F from './lib/format.js';
import * as charts from './lib/charts.js';
import { renderNav } from '../nav.js';

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
// The static part of the bar (brand, Lonvera, Projections) is in projection.html and mirrors the Lonvera pages. This file fills the module tabs and the scenario control.

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

// the plan of record chip: calm, always there, says what it drives and when it last changed
const ago = (iso) => { const t = Date.parse(iso); if (!Number.isFinite(t)) return null; const m = Math.max(0, Math.round((Date.now() - t) / 60000)); return m < 1 ? 'just now' : m < 60 ? `${m} min ago` : m < 1440 ? `${Math.round(m / 60)} h ago` : `${Math.round(m / 1440)} days ago`; };
const porAge = (c) => (c && c.version > 1 ? `changed ${ago(c.updated_at) || 'earlier'}` : 'no edits yet');
const porTitle = (c) => `The plan of record drives the pacing goals and the default view of every page.${c && c.version > 1 && ago(c.updated_at) ? ` Last changed ${ago(c.updated_at)}.` : ''}`;
function refreshModeChip() {
  const el = $('#pg-mode');
  const c = scenarios.current;
  if (!el || !el.classList.contains('por') || !c) return;
  const age = el.querySelector('.age');
  if (age) age.textContent = ' · ' + porAge(c);
  el.title = porTitle(c);
}
setInterval(refreshModeChip, 30000);

const bar = $('#pg-bar');
function buildBar() {
  closeMenu();
  const cur = scenarios.current;
  const sel = h('select', { id: 'pg-scenario', 'aria-label': 'Scenario' },
    scenarios.list.map((s) => h('option', { value: s.slug, selected: s.slug === scenarios.slug }, s.name)));
  sel.addEventListener('change', () => scenarios.select(sel.value));
  const isDefault = !!cur && (cur.is_default || cur.slug === 'current');
  const item = (act, text, extra = {}) => h('button', { type: 'button', role: 'menuitem', class: 'pg-mitem' + (extra.danger ? ' danger' : ''), 'data-act': act, onclick: () => actions[act](), disabled: extra.disabled, hidden: extra.hidden, title: extra.title }, text);
  const following = !!(cur && cur.frozen === false && cur.base_slug && !isDefault);            // a clone made before copies were frozen still reads its base
  const baseName = following ? ((scenarios.list.find((x) => x.slug === cur.base_slug) || {}).name || cur.base_slug) : '';
  const pop = h('div', { class: 'pg-menu-pop', id: 'pg-menu-pop', 'aria-label': 'Scenario actions' },
    item('new', 'New scenario'),
    item('clone', 'Clone this scenario', { disabled: !cur }),
    item('rename', 'Rename', { disabled: !cur }),
    following ? item('detach', `Detach from ${baseName}`, { title: `This scenario still follows ${baseName}. Detach keeps its numbers and stops following.` }) : null,
    item('history', 'History and restore', { disabled: !cur, title: 'Versions of this scenario, and deleted scenarios' }),
    isDefault ? null : h('div', { class: 'pg-msep', role: 'separator' }),
    isDefault ? null : item('delete', 'Delete this scenario', { danger: true, disabled: !cur, title: 'Delete this scenario' }));
  const mbtn = h('button', { type: 'button', class: 'btn small pg-menubtn', id: 'pg-menu-btn', 'aria-label': 'Scenario actions', title: 'New, clone, rename, history' },
    h('span', { 'aria-hidden': 'true' }, '⋯'));
  wireMenu(mbtn, pop);
  bar.replaceChildren(
    sel,
    h('span', { class: 'pg-ver', id: 'pg-version', title: cur && cur.updated_at ? 'Updated ' + F.dateTime(cur.updated_at) : null }, cur ? 'v' + cur.version : ''),
    isDefault
      ? h('span', { class: 'pg-mode por', id: 'pg-mode', title: porTitle(cur) }, 'Plan of record', h('span', { class: 'age' }, ' · ' + porAge(cur)))
      : h('span', { class: 'pg-mode', id: 'pg-mode', title: 'You are in a copy. Edits here do not change the plan of record.' }, 'Copy'),
    h('div', { class: 'pg-menu' }, mbtn, pop));
  document.body.dataset.pgScenario = isDefault ? 'default' : 'copy';
  bar.hidden = false;
}
function updateVersionChip() {
  const el = $('#pg-version');
  const cur = scenarios.current;
  if (el && cur) { el.textContent = 'v' + cur.version; el.title = cur.updated_at ? 'Updated ' + F.dateTime(cur.updated_at) : ''; }
  refreshModeChip();
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
  async detach() {
    const cur = scenarios.current;
    if (!cur || cur.frozen !== false || !cur.base_slug) return;
    const baseName = (scenarios.list.find((x) => x.slug === cur.base_slug) || {}).name || cur.base_slug;
    const ok = await confirmBox({
      title: `Detach "${cur.name}" from ${baseName}?`,
      message: `Right now this scenario still follows ${baseName}: anything it does not hold itself comes from there. Detaching keeps today's numbers as its own and stops following. Nothing it holds changes.`,
      confirmText: 'Detach', danger: false,
      onConfirm: () => scenarios.detach(cur.slug),
    });
    if (ok) toast(`${cur.name} now stands on its own`, 'ok');
  },
  async delete() {
    const cur = scenarios.current;
    if (!cur || cur.is_default || cur.slug === 'current') return;
    const ok = await confirmTyped({
      title: `Delete "${cur.name}"?`,
      message: 'This removes the scenario and all of its inputs. Other scenarios are not affected. You can bring it back from History for 30 days.',
      expect: cur.slug,
      confirmText: 'Delete scenario',
      onConfirm: () => { refreshHeadlineSoon.cancel(); headlineGen++; return scenarios.remove(cur.slug, cur.slug); },   // no headline request for a scenario that is going away
    });
    if (ok) toast(`Deleted ${cur.name}`, { kind: 'ok', action: { label: 'Undo', onClick: async () => { try { await scenarios.undelete(cur.slug); toast(`Restored ${cur.name}`, 'ok'); } catch (e) { toast(e.message || 'Could not restore it', 'error'); } } } });
  },
  history: () => openHistory(),
};

// the top row: the same bar as every other page (js/nav.js), with the Plan section lit; the module tabs below it are this section's second row
{
  const shared = renderNav({ dataPg: true });
  $('#topbar').replaceChildren(h('div', { class: 'topbar-in' }, shared.brand, shared.groups, shared.search));
  shared.setCurrent('/projection', {});
}
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
function revealActiveTab() {
  const act = nav.querySelector('a.pg-tab[aria-current="page"]');
  if (!act || !nav.clientWidth) return;
  const nr = nav.getBoundingClientRect(), ar = act.getBoundingClientRect();
  if (ar.left < nr.left + 4 || ar.right > nr.right - 36) nav.scrollLeft += (ar.left + ar.width / 2) - (nr.left + nr.width / 2);
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
  // keep the current tab fully visible when the tabs scroll (1100px and below); again once fonts settle, because widths change then
  requestAnimationFrame(revealActiveTab);
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => requestAnimationFrame(revealActiveTab));
}

// ------------------------------------------------------------------ degraded modules: one plain banner on every page
// Sources: GET /run -> degraded:[{module, error}] and any 503 upstream_degraded from a module endpoint (lib/api.js emits 'degraded').
const degradedEl = $('#pg-degraded');
let degradedRun = [];
let degradedSeen = [];
const modLabel = (key) => { const m = modules.find((x) => dash(x.key) === dash(String(key || ''))); return m ? m.label : (key ? String(key).replace(/_/g, ' ') : null); };
const listWords = (a) => (a.length < 2 ? a.join('') : a.slice(0, -1).join(', ') + ' and ' + a[a.length - 1]);
function drawDegraded() {
  const all = [...degradedRun, ...degradedSeen];
  if (!all.length) { degradedEl.hidden = true; degradedEl.replaceChildren(); return; }
  const labels = [...new Set(all.map((d) => modLabel(d.module)).filter(Boolean))];
  const detail = (all.find((d) => d.error) || {}).error;
  setChildren(degradedEl,
    h('span', { class: 'pg-degraded-t' }, h('strong', null, `${labels.length ? listWords(labels) : 'A module'} failed to load`), `, so numbers that depend on ${labels.length > 1 ? 'them' : 'it'} are not shown.`),
    detail ? h('span', { class: 'pg-degraded-d' }, String(detail).slice(0, 160)) : null,
    h('button', { type: 'button', class: 'btn small', onclick: () => { refreshHeadline(); route({ scroll: false }); } }, 'Try again'));
  degradedEl.hidden = false;
}
api.events.on('degraded', (d) => { degradedSeen = [...degradedSeen.filter((x) => x.module !== d.module), d]; drawDegraded(); });

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
      const y = pickYear(Object.keys(raw.fy), r.actual_through || r.books_through);
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
// What changed? In a copy every headline number shows its difference from the scenario it was cloned from; in the plan of record a number that just moved shows what it was.
const lastShown = new Map();      // slug -> {label: item} from the previous refresh in this session
const byLabel = (items) => Object.fromEntries(items.map((it) => [it.label, it]));
function deltaText(it, ref, refName) {
  if (ref && typeof it.value === 'string' && typeof ref.value === 'string') return it.value === ref.value ? `same as ${refName}` : `${refName}: ${ref.value}`;      // ranges such as the implied value
  if (!ref || typeof it.value !== 'number' || typeof ref.value !== 'number') return null;
  const d = it.value - ref.value;
  if (Math.abs(d) < 1e-9) return refName ? `same as ${refName}` : null;
  const sign = d < 0 ? '-' : '+';
  const body = (it.fmt === 'usd0' || it.fmt === 'usd') ? F.compact(Math.abs(d)).replace(/^-/, '') : F.display(Math.abs(d), it.fmt).text;
  return `${sign}${body} vs ${refName}`;
}
async function refreshHeadline() {
  const gen = ++headlineGen;
  if (!scenarios.slug) { strip.hidden = true; return; }
  const cur = scenarios.current;
  const baseInfo = cur && cur.base_slug && !(cur.is_default || cur.slug === 'current') ? scenarios.list.find((x) => x.slug === cur.base_slug) : null;
  let r = null, rb = null;
  try { [r, rb] = await Promise.all([api.get('/run', { scenario: scenarios.slug }), baseInfo ? api.get('/run', { scenario: baseInfo.slug }).catch(() => null) : null]); } catch { r = null; }
  if (gen !== headlineGen) return;
  degradedRun = r && Array.isArray(r.degraded) ? r.degraded : [];
  drawDegraded();
  const through = r && (r.actual_through || r.actualThrough);
  const books = r && r.books_through, feeds = r && r.feeds_through;
  const thruLabel = books ? `Books through ${F.monthLabel(books)}` + (feeds ? ` · Feeds through ${F.date(feeds)}` : '') : (through ? 'Actual through ' + F.monthLabel(through) : null);
  const items = headlineItems(r);
  // nothing real to show (no module built, or every figure is zero): hide the strip rather than print $0
  if (!modules.some((m) => m.built) || !items.length || items.every((it) => it.zero)) { strip.hidden = true; strip.replaceChildren(); return; }
  const prev = lastShown.get(scenarios.slug);
  lastShown.set(scenarios.slug, byLabel(items));
  const refs = baseInfo && rb ? byLabel(headlineItems(rb)) : null;
  setChildren(strip,
    thruLabel ? h('span', { class: 'pg-actual', id: 'pg-actual-through' }, thruLabel) : null,
    items.map((it) => {
      let note = null;
      if (refs) note = deltaText(it, refs[it.label], baseInfo.name);
      else if (prev && prev[it.label] && typeof it.value === 'number' && typeof prev[it.label].value === 'number' && Math.abs(it.value - prev[it.label].value) > 1e-9) note = `was ${kpiText(prev[it.label])}`;
      else if (prev && prev[it.label] && typeof it.value === 'string' && typeof prev[it.label].value === 'string' && it.value !== prev[it.label].value) note = `was ${prev[it.label].value}`;
      return h('div', { class: 'pg-kpi' }, h('span', { class: 'k' }, it.label), h('span', { class: 'v' + (it.value < 0 ? ' neg' : '') }, kpiText(it)), note ? h('span', { class: 'd' }, note) : null);
    }));
  strip.hidden = false;
}
const refreshHeadlineSoon = debounce(refreshHeadline, 400);

// ------------------------------------------------------------------ history drawer
const drawer = $('#pg-drawer');
let drawerOpener = null;
// History rows read in plain words: "Marketing spend, Aug 2027: $5,000,000 to $450,000", not "drivers.spend_total ...".
// the version the plan of record was at when this tab session began: the one-click "restore to how it was when you started"
const START_KEY = 'projection.por.start';
function porStart() { try { return JSON.parse(sessionStorage.getItem(START_KEY) || 'null'); } catch { return null; } }
function noteSessionStart() {
  const d = scenarios.list.find((x) => x.is_default);
  if (!d || porStart()) return;
  try { sessionStorage.setItem(START_KEY, JSON.stringify({ slug: d.slug, version: d.version, at: new Date().toISOString() })); } catch { /* ignore */ }
}
const clockLabel = (iso) => new Date(iso).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
const LINE_LABELS = {
  'drivers.spend_total': 'Marketing spend', 'drivers.nc_cpa': 'New-customer CPA', 'drivers.nc_aov': 'New-customer AOV', 'drivers.amazon_rev': 'Amazon revenue',
  'drivers.tiktok_rev': 'TikTok revenue', 'drivers.ad_spend': 'Ad spend (typed)', 'drivers.nc_orders': 'New-customer orders (typed)', 'drivers.nc_rev': 'New-customer revenue (typed)',
  'drivers.repeat_rev': 'Repeat revenue (typed)', 'financing.noi_before_inventory': 'Net operating income before inventory', 'financing.opening_cash': 'Opening cash', 'financing.principal_paid': 'Principal paid',
};
const lineLabel = (k) => LINE_LABELS[k] || String(k).split('.').pop().replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());
const showVal = (x, key = '') => {
  if (x == null) return 'unset';
  if (typeof x === 'object') return 'value' in x ? showVal(x.value, key) : JSON.stringify(x).slice(0, 60);
  if (typeof x !== 'number') return String(x);
  if (/orders|units|customers|count/.test(key)) return F.num(x);
  if (/cpa|aov|price|per_/.test(key)) return F.money(x, { digits: 2 });
  if (/_pct|rate|share|margin/.test(key)) return F.pct(x);
  return F.money(x, { digits: 0 });
};
function describe(e) {
  if (e.summary || e.detail || e.description) return e.summary || e.detail || e.description;
  if (e.action === 'scenario.rename' && e.old && e.new && typeof e.new === 'object' && 'name' in e.new) return `Renamed: ${e.old.name} to ${e.new.name}`;
  if (e.line_key) return `${lineLabel(e.line_key)}${e.month ? ', ' + F.monthLabel(e.month) : ''}: ${showVal(e.old ?? e.old_value, e.line_key)} to ${showVal(e.new ?? e.new_value, e.line_key)}`;
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
  const start = porStart();
  const isPor = !!(cur.is_default || cur.slug === 'current');
  const quick = isPor && start && start.slug === cur.slug && Number(start.version) < Number(cur.version)
    ? h('div', { class: 'pg-por-restore', id: 'pg-por-restore' },
      h('p', null, h('strong', null, 'The plan of record has changed since you started'), ` (it was v${start.version} at ${clockLabel(start.at)}).`),
      h('button', { type: 'button', class: 'btn primary', 'data-act': 'restore-session', onclick: () => restoreVersion(start.version, body) }, `Restore plan of record to v${start.version}, ${clockLabel(start.at)}`))
    : null;
  let deleted = [];
  try { deleted = await scenarios.archived(); } catch { deleted = []; }
  const delItem = (d) => h('li', { 'data-slug': d.slug },
    h('div', { class: 'what' }, h('strong', null, d.name),
      h('div', { class: 'sub' }, `Deleted ${F.dateTime(d.archived_at)}. Can be restored for ${d.days_left} more ${d.days_left === 1 ? 'day' : 'days'}.`)),
    h('div', { class: 'act' }, h('button', { type: 'button', class: 'btn small', 'data-act': 'undelete', onclick: () => undeleteScenario(d, body) }, 'Restore')));
  setChildren(body,
    quick,
    h('ul', { class: 'pg-hist', id: 'pg-hist' }, versions.map(verItem)),
    deleted.length ? h('div', { class: 'pg-group-head', id: 'pg-deleted-head' }, 'Deleted scenarios') : null,
    deleted.length ? h('ul', { class: 'pg-hist', id: 'pg-deleted' }, deleted.map(delItem)) : null,
    loose.length ? h('div', { class: 'pg-group-head' }, 'Other activity') : null,
    loose.length ? h('ul', { class: 'pg-hist' }, loose.slice(0, 50).map((a) => h('li', null, h('div', { class: 'when' }, F.dateTime(a.at) + (a.actor ? ' · ' + a.actor : '')), h('div', { class: 'what' }, describe(a))))) : null);
}
async function undeleteScenario(d, body) {
  try { await scenarios.undelete(d.slug); } catch (e) { toast(e.message || 'Could not restore it', 'error'); return; }
  toast(`Restored ${d.name}`, 'ok');
  closeHistory();
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
  degradedSeen = [];
  drawDegraded();
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
    if (e && e.code === 'upstream_degraded') {      // plain words, not a generic error: the banner above says which module failed
      const lab = modLabel(e.data && (e.data.module || (e.data.modules && e.data.modules[0])));
      el.replaceChildren(h('div', { class: 'pg-card pg-degraded-card', role: 'status' },
        h('h2', null, 'These numbers are not shown'),
        h('p', { class: 'muted' }, lab ? `${lab} failed to load, and this page depends on it. Nothing here is guessed or shown as zero.` : 'A module this page depends on failed to load. Nothing here is guessed or shown as zero.'),
        h('button', { type: 'button', class: 'btn', onclick: () => route() }, 'Try again')));
      document.body.dataset.pgState = 'ready';
      return;
    }
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
  noteSessionStart();
  installGuard({ afterSwitch: () => { syncUrlScenario(); buildBar(); } });
  window.addEventListener('pg:scenario-action', (e) => { const fn = actions[e.detail]; if (fn) fn(); });
  buildNav();
  scenarios.on('list', () => buildBar());
  scenarios.on('version', () => { updateVersionChip(); refreshHeadlineSoon(); });
  scenarios.on('change', () => { refreshHeadlineSoon.cancel(); syncUrlScenario(); buildBar(); route({ scroll: false }); refreshHeadline(); });
  refreshHeadline();
  await route();
}
boot();
