// Priorities (/priorities): the weekly one-sheeter as one table. Every concept has exactly one Action (Revive / Make more / Test / Stop / Hold; JSON field `section`) and one Status
// (Working / Rising / Fading / Not working / Average / Too early / Untested); the rules are printed on the page from the API's `definitions`. One row per concept, ALL COLLAPSED on load (nothing is remembered). A row shows Market Cluster | ICP | Pain Point | Story (full text) | Status | Concepts | one key number; the chevron button (or a click on
// the row) opens a detail panel directly under it with everything else. Print CSS expands every panel. Class names and test ids start with `pri-` (never ad-, ads-, sponsor, banner, promo).
import { h, fmt, isNum, href, navigate, qs } from '../util.js';
import { api } from '../api.js';
import { pageHeader } from '../ui/pagehead.js';
import { filterRow, selectCtl, searchCtl } from '../ui/pagefilters.js';
import { showControls } from '../controls.js';
import { dayOf, manualPanel, addManualNotes, manualStrip, manualControls } from './priorities_manual.js';
import { assignControls } from './priorities_assign.js';

import { SECTIONS, SECTION_LABEL, setDefs, actionRule, STATUS_CLASS, STATUS_ORDER, roasX, pct, share, compact$, numbers, keyNumber, toneOf, statCards, tile, detail } from './priorities_panel.js';
const PRODUCT_ORDER = ['vita-bull', 'beef-liver', 'boneflex'];
const value_keys = ['product', 'section', 'cluster', 'icp', 'pain', 'story', 'status', 'concepts', 'key', 'spend14', 'roas14'];

// ---------- saved views: helpers ----------
const LAST_VIEW = 'pri-last-view';
const FLASH = 'pri-flash';
const lastView = { get() { try { return localStorage.getItem(LAST_VIEW) || ''; } catch (e) { return ''; } }, set(t) { try { if (t) localStorage.setItem(LAST_VIEW, t); else localStorage.removeItem(LAST_VIEW); } catch (e) { /* blocked */ } } };
const flash = { set(t) { try { sessionStorage.setItem(FLASH, t); } catch (e) { /* blocked */ } }, take() { try { const t = sessionStorage.getItem(FLASH); sessionStorage.removeItem(FLASH); return t; } catch (e) { return null; } } };
const defaultViewName = () => { const t = new Date(); return `Priorities - ${t.getMonth() + 1}/${t.getDate()}/${String(t.getFullYear() % 100).padStart(2, '0')}`; };
const viewUrl = (tok, extra) => href('/priorities', { view: tok, ...(extra || {}) });

// One small modal for naming a view (Save view, Rename): focused, prefilled and selected input, Enter saves, Esc cancels, 120 characters, empty -> the default name.
// `kinds` = [[value, label, disabled?], ...] adds a radio group (what to save). Resolves {name, kind} or null.
function nameDialog({ title, initial, okLabel, kinds = null, kind = null, note = null, placeholder = 'Madre - 11/10/26 Priorities', field = 'Name', max = 120, emptyOk = false }) {
  return new Promise((resolve) => {
    const input = h('input', { type: 'text', class: 'pri-name', maxlength: String(max), 'aria-label': field === 'Name' ? 'View name' : field, 'data-testid': 'pri-view-name', placeholder, value: initial || '' });
    let chosen = kind || (kinds && (kinds.find((k) => !k[2]) || kinds[0])[0]);
    const radios = kinds ? h('fieldset', { class: 'pri-kinds' }, h('legend', { class: 'pri-k' }, 'What to save'), kinds.map(([v, label, dis]) => h('label', { class: 'pri-kind' },
      h('input', { type: 'radio', name: 'pri-kind', value: v, checked: v === chosen, disabled: !!dis, 'data-testid': 'pri-kind-' + v, onchange: () => { chosen = v; } }), ' ', label))) : null;
    const cancel = h('button', { type: 'button', class: 'btn', 'data-testid': 'pri-dialog-cancel' }, 'Cancel');
    const form = h('form', { method: 'dialog', class: 'pri-dform', onsubmit: (e) => { e.preventDefault(); done({ name: input.value.trim() || (emptyOk ? '' : defaultViewName()), kind: chosen }); } },
      h('h2', { class: 'pri-dtitle', id: 'pri-dtitle' }, title), note ? h('p', { class: 'pri-dnote', 'data-testid': 'pri-dialog-note' }, note) : null, radios,
      h('label', { class: 'pri-dlabel' }, h('span', { class: 'pri-k' }, field), input),
      h('div', { class: 'pri-dbtns' }, cancel, h('button', { type: 'submit', class: 'btn primary', 'data-testid': 'pri-dialog-save' }, okLabel || 'Save')));
    const dlg = h('dialog', { class: 'pri-dialog', 'aria-labelledby': 'pri-dtitle', 'data-testid': 'pri-name-dialog' }, form);
    let finished = false;
    function done(v) { if (finished) return; finished = true; try { dlg.close(); } catch (e) { /* already closed */ } dlg.remove(); resolve(v); }
    cancel.addEventListener('click', () => done(null));
    dlg.addEventListener('cancel', (e) => { e.preventDefault(); done(null); });                       // Esc
    dlg.addEventListener('click', (e) => { if (e.target === dlg) done(null); });                      // click on the backdrop
    document.body.appendChild(dlg);
    if (typeof dlg.showModal === 'function') dlg.showModal(); else dlg.setAttribute('open', '');
    input.focus(); input.select();
  });
}

// the panel under a row: manual rows have their own render function; an auto row gets the notes of any manual concept with the same four tags appended
const unmappedPanel = (r, d) => h('div', { class: 'pri-panel pri-tone-' + toneOf(r), 'data-testid': 'pri-rest-panel' },
  h('div', { class: 'pri-chips' }, h('strong', { class: 'pri-chip-prod' }, r.product_name), h('span', { class: 'pri-sec pri-sec-hold' }, 'Hold'), h('span', { class: 'pri-status pri-st-' + toneOf(r) }, r.status)),
  r.story ? h('p', { class: 'pri-story' }, r.story) : null, r.why ? h('p', null, r.why) : null, statCards(r),
  (r.unmapped_ads || []).length ? h('div', { class: 'pri-card', 'data-testid': 'pri-unmapped' }, h('div', { class: 'pri-h' }, 'Biggest ads without a line yet'), h('ul', null, r.unmapped_ads.map((a) => h('li', null, `${a.ad}: ${fmt.money(a.spend)} since launch, ${fmt.money(a.spend_14d)} last 14 days`)))) : null);
// every row gets the same panel (hooks, winning ads, trend, number tiles, competitor badges where present), Hold included; only the "ads not mapped to a line" row has its own simple one
const withProd = (p, r, actl) => { if (actl) p.append(actl.history(r)); return p; };                           // production history under every concept's panel (priorities_assign.js); none on the public view page
const panelFor = (r, d, mctl, actl) => { if (r.manual) return withProd(manualPanel(r, mctl), r, actl); if (r.section === 'hold' && r.line_id == null) return unmappedPanel(r, d); const p = detail(r, d); addManualNotes(p, r, d); return withProd(p, r, actl); };

const NEW_IDEA = { 'Competitor': 'New idea, from a competitor', 'Our draft': 'New idea, our draft' };
const SECTION_ORDER = SECTIONS.map(([k]) => k);
const COLS = [['product', 'Product'], ['cluster', 'Market Cluster'], ['icp', 'ICP'], ['pain', 'Pain Point'], ['section', 'Action'], ['story', 'Story'], ['status', 'Status'], ['concepts', 'Concepts'], ['key', 'Key number'], ['spend14', 'Spend \u00b7 14d'], ['roas14', 'ROAS \u00b7 14d']];

// last 14 days (Meta-reported, raw purchase value / spend, not shrunk): spend always shown when the row has data, ROAS only with $100+ spend in the window (else a dash)
const has14 = (r) => !r.manual && r.metrics && isNum(r.metrics.spend_14d);
const roas14Of = (r) => (has14(r) && isNum(r.metrics.roas_14d) ? r.metrics.roas_14d : null);
function roas14Cell(r, d) {
  const v = roas14Of(r);
  if (v === null) return has14(r) ? h('span', { class: 'pri-k', title: 'Under $100 spend in the last 14 days: no ratio shown' }, '\u2013') : '\u2013';
  const base = ((d.baselines || {})[r.product] || {}).roas_30d;
  const rel = isNum(base) && base ? v / base - 1 : 0;
  const tone = rel >= 0.05 ? 'up' : rel <= -0.05 ? 'down' : 'flat';
  return h('span', { class: 'pri-r14 pri-r14-' + tone, 'data-testid': 'pri-roas14', title: `${roasX(v)} vs ${roasX(base)} product average over the last 30 days` }, roasX(v), h('span', { class: 'pri-r14-a', 'aria-hidden': 'true' }, tone === 'up' ? ' \u25b2' : tone === 'down' ? ' \u25bc' : ''),
    h('span', { class: 'pri-vh' }, tone === 'up' ? ' above the product average' : tone === 'down' ? ' below the product average' : ' near the product average'));
}
// sums over a list of rows that have last-14-day data: spend, purchase value and the spend-weighted ROAS (total value / total spend, never an average of the cells); dash under $100 spend
function sum14(rows) {
  let spend = 0, value = 0, purchases = 0, n = 0;
  rows.forEach((r) => { if (!has14(r)) return; const m = r.metrics; n += 1; spend += m.spend_14d; purchases += m.purchases_14d || 0; value += isNum(m.value_14d) ? m.value_14d : (isNum(m.roas_14d) ? m.roas_14d * m.spend_14d : 0); });
  return { n, spend, value, purchases, roas: spend >= 100 ? value / spend : null };
}
const keyOf = (r) => r.key || [r.product, r.section, r.cluster, r.icp, r.pain_point].join('|');      // stable row key: unique per row in a snapshot; the server uses the same join for ?id=
const storeKey = (week, tok) => 'pri-selected-' + week + (tok ? '-' + tok : '');
function loadSel(week, valid, tok) {
  try { return new Set((JSON.parse(sessionStorage.getItem(storeKey(week, tok))) || []).filter((k) => valid.has(k))); } catch (e) { return new Set(); }
}
function saveSel(week, sel, tok) { try { sessionStorage.setItem(storeKey(week, tok), JSON.stringify([...sel])); } catch (e) { /* storage blocked: selection just is not remembered */ } }

// Cluster colours: one fixed palette of soft hues, picked from a hash of the cluster NAME, so the same cluster name has the same colour under every product and on every visit.
// The hues are stepped (index * 7 mod 16) so neighbouring palette slots are far apart on the colour wheel. The CSS turns the hue into a fill (cluster), a lighter tint (ICP) and a faint tint (other cells).
const HUES = Array.from({ length: 16 }, (_, i) => (((i * 7) % 16) * 22.5 + 8) % 360);
export const hueOf = (name) => { let x = 5381; const t = String(name || '').trim().toLowerCase(); for (let i = 0; i < t.length; i++) x = ((x * 33) ^ t.charCodeAt(i)) >>> 0; return Math.round(HUES[x % HUES.length]); };

const SORT_TIP = { icp: 'Ideal customer profile: who the ad speaks to', roas14: 'Return on ad spend over the last 14 days: purchase value divided by spend', spend14: 'Meta spend over the last 14 days' };
let uid = 0;
function setOpen(btn, panelRow, open) {
  btn.setAttribute('aria-expanded', String(open));
  btn.setAttribute('aria-label', (open ? 'Collapse: ' : 'Expand: ') + btn.dataset.name);
  if (open && panelRow._build) panelRow._build();
  panelRow.hidden = !open;
  panelRow.previousElementSibling.classList.toggle('pri-open', open);
  if (panelRow._layout) panelRow._layout();                                         // merged group cells must cover the rows that are visible now
}

// one row (collapsed) + its detail row (hidden). `printed` rows have no toggle and an always-visible detail (the print-only copy).
function rowPair(r, d, printed = false, onPick = null, mctl = null, actl = null) {
  const id = 'pri-detail-' + (++uid);
  const name = `${r.cluster}, ${r.icp}, ${r.pain_point}`;
  const cell = (label, cls, ...kids) => h('td', { class: 'pri-c pri-c-' + cls, 'data-label': label }, ...kids);
  const lazy = r.section === 'hold' && !printed;                                    // the 100+ Hold panels are built on first open (the content is the same full panel)
  // the detail row: a spacer cell under the checkbox column (shown only in the grouped view, where the merged Product / Cluster / ICP cells run past this row) + the panel cell
  const dSpacer = h('td', { class: 'pri-d-sp', hidden: true, 'aria-hidden': 'true' });
  const dMain = h('td', { colspan: String(12 + (actl ? 1 : 0)) }, lazy ? null : panelFor(r, d, r._from ? null : mctl, actl));
  const detailRow = h('tr', { class: 'pri-detail', id, hidden: !printed, 'data-testid': 'pri-detail' }, dSpacer, dMain);
  detailRow._sp = dSpacer; detailRow._main = dMain;
  if (lazy) detailRow._build = () => { dMain.replaceChildren(panelFor(r, d, r._from ? null : mctl, actl)); detailRow._build = null; };
  const btn = h('button', { type: 'button', class: 'pri-toggle', 'aria-expanded': 'false', 'aria-controls': id, 'aria-label': 'Expand: ' + name, 'data-name': name, 'data-testid': 'pri-toggle' },
    h('span', { class: 'pri-chev', 'aria-hidden': 'true' }, '▸'));
  const pick = h('input', { type: 'checkbox', class: 'pri-pick', 'data-testid': 'pri-pick', 'aria-label': 'Select: ' + name, onchange: (e) => onPick && onPick(r, e.target.checked) });
  const cProduct = cell('Product', 'product', (d.baselines[r.product] || {}).product_name || r.product);
  const cCluster = cell('Market Cluster', 'cluster', r.cluster, r._from ? h('span', { class: 'pri-from', 'data-testid': 'pri-from', title: 'No longer in the current snapshot: the numbers are the frozen copy saved with the view' }, ' from ' + r._from) : null);
  const cIcp = cell('ICP', 'icp', r.icp);
  const tr = h('tr', { class: 'pri-row' + (r.manual ? ' pri-manual' + (r.done ? ' pri-done' : '') : ''), 'data-testid': 'pri-card', 'data-section': r.section, 'data-product': r.product, 'data-source': r.manual ? 'manual' : 'auto', 'data-cluster-hue': String(hueOf(r.cluster)) },
    h('td', { class: 'pri-c pri-c-tog' }, printed || !onPick ? null : pick, printed ? null : btn),
    cProduct, cCluster, cIcp, cell('Pain Point', 'pain', r.pain_point),
    cell('Action', 'section', h('span', { class: 'pri-sec pri-sec-' + r.section, title: actionRule(r.section) }, SECTION_LABEL[r.section]), r.source ? h('span', { class: 'pri-src', 'data-testid': 'pri-source', title: 'Not on the sheet yet' }, [NEW_IDEA[r.source] || r.source, h('span', { class: 'pri-src-sub', 'data-testid': 'pri-not-on-sheet' }, 'Not on the sheet yet')]) : null, r.manual ? h('span', { class: 'pri-mbadge', 'data-testid': 'pri-manual-badge' }, 'Manual') : null),
    cell('Story', 'story', r.story || '–'),
    cell('Status', 'status', h('span', { class: 'pri-status pri-st-' + (STATUS_CLASS[r.status] || 'flat'), 'data-testid': 'pri-status' }, r.manual && r.done ? 'MANUAL (done)' : r.status)),
    cell('Concepts', 'concepts', r.manual || r.section === 'hold' ? '\u2013' : r.concepts ? `${r.concepts} concept${r.concepts === 1 ? '' : 's'}` : 'None'),
    cell('Key number', 'key', keyNumber(r, d)),
    cell('Spend \u00b7 14d', 'spend14', has14(r) ? fmt.money(r.metrics.spend_14d) : '\u2013'),
    cell('ROAS \u00b7 14d', 'roas14', roas14Cell(r, d)),
    actl ? cell('In production', 'prod', actl.cell(r)) : null);
  tr.style.setProperty('--h', String(hueOf(r.cluster)));
  if (!printed) {
    const toggle = () => setOpen(btn, detailRow, btn.getAttribute('aria-expanded') !== 'true');
    btn.addEventListener('click', (e) => { e.stopPropagation(); toggle(); });
    tr.addEventListener('click', (e) => { if (!e.target.closest('a, button, input, label, .pri-merged')) toggle(); });      // a merged group cell belongs to many rows: it does not open one of them
  }
  return { tr, detailRow, r, pick, cells: { product: cProduct, cluster: cCluster, icp: cIcp } };
}

function render(d, view = null, opts = {}) {
  setDefs(d.definitions);
  const vm = opts.vm || { views: [], active: null, mode: 'latest', notice: null };
  const readonly = !!view;                                                               // a saved view: only its rows, no selection or editing, pinned to its stored snapshot
  const slugs = PRODUCT_ORDER.filter((s) => d.rows.some((r) => r.product === s));
  const sections = SECTION_ORDER.filter((k) => d.rows.some((r) => r.section === k));
  const pOrder = Object.fromEntries(PRODUCT_ORDER.map((s, i) => [s, i]));
  const sOrder = Object.fromEntries(SECTION_ORDER.map((s, i) => [s, i]));
  const act = vm.active || null;                                                           // the saved view switched on in place (/priorities?view=<token>), else null = All concepts
  const tok = act ? act.view.token : '';
  const sel = readonly ? new Set(d.rows.map(keyOf)) : loadSel(d.week_start, new Set(d.rows.map(keyOf)), tok);
  const isSel = (r) => sel.has(keyOf(r));
  const mctl = manualControls(d, readonly, opts, (r) => { sel.delete(keyOf(r)); saveSel(d.week_start, sel, tok); });
  const actl = readonly ? null : assignControls({ d, keyOf, selected: () => d.rows.filter(isSel), refresh: () => apply() });          // production hooks: none on a public saved view
  const pairs = d.rows.map((r) => rowPair(r, d, false, readonly ? null : (row, on) => { if (on) sel.add(keyOf(row)); else sel.delete(keyOf(row)); selChanged(); }, mctl, actl));
  let shownList = [];
  pairs.forEach((p) => { p.detailRow._layout = () => relayout(); });
  const carry = opts.carry || {};
  const sf = act && act.view.kind === 'filters' && act.view.filters ? act.view.filters : null;      // a filter view starts from its saved filters (applied live to whatever rows exist now)
  const pick0 = (saved, all) => { const keep = (saved || []).map((x) => (x === 'add' ? 'test' : x)).filter((x) => all.includes(x)); return new Set(keep.length ? keep : all); };
  const state = { products: pick0(sf && sf.products, slugs), sections: pick0(sf && sf.sections, sections), q: carry.q || (sf ? sf.q || '' : ''), key: 'key' in carry ? carry.key : (sf && value_keys.includes(sf.key) ? sf.key : null),        // no sort chosen (null) = the grouped view
                  dir: carry.dir || (sf && sf.dir === -1 ? -1 : 1), showDone: !!carry.showDone, onlySel: !!carry.onlySel, brief: carry.brief || 'all', statuses: carry.statuses ? new Set(carry.statuses) : new Set(sf && sf.statuses && sf.statuses.length ? sf.statuses : sf && sf.status ? [sf.status] : []) };
  const allOn = (set, all) => (all.every((x) => set.has(x)) ? [] : all.filter((x) => set.has(x)));
  const currentFilters = () => ({ products: allOn(state.products, slugs), sections: allOn(state.sections, sections), q: state.q.trim(), key: state.key, dir: state.dir, status: null, statuses: [...state.statuses] });
  if (opts.ref) opts.ref.state = state;
  const value = {
    product: (r) => pOrder[r.product], section: (r) => sOrder[r.section], cluster: (r) => r.cluster.toLowerCase(), icp: (r) => r.icp.toLowerCase(), pain: (r) => r.pain_point.toLowerCase(),
    story: (r) => (r.story || '').toLowerCase(), status: (r) => r.status.toLowerCase(), concepts: (r) => r.concepts || 0, key: (r) => keyNumber(r, d).toLowerCase(), spend14: (r) => (has14(r) ? r.metrics.spend_14d : -1), roas14: (r) => { const v = roas14Of(r); return v === null ? -1 : v; }, prod: (r) => (actl ? actl.text(r).toLowerCase() : ''), briefed: (r) => (isBriefed(r) ? 1 : 0),
  };
  // ---------- grouped view: order = product, market cluster, ICP, pain point as on the Communication Overview sheet (row.sheet_pos = the line's sheet position) ----------
  // A row with no sheet position (new idea, manual concept, "no line") goes after the on-sheet rows of its ICP / cluster; a cluster or ICP that is not on the sheet goes after the on-sheet ones of its parent, in Action / rank order.
  const INF = 1e9;
  const posOf = (r) => (isNum(r.sheet_pos) ? r.sheet_pos : INF);
  const appOf = (r) => (sOrder[r.section] || 0) * 1e6 + (r.rank || 0);                      // Action order, then rank: the order the page had before grouping
  const gmin = new Map();
  d.rows.forEach((r) => {
    [`${r.product}|${r.cluster}`, `${r.product}|${r.cluster}|${r.icp}`].forEach((k) => { const g = gmin.get(k); const pp = posOf(r), aa = appOf(r); if (!g) gmin.set(k, { pos: pp, app: aa }); else { g.pos = Math.min(g.pos, pp); g.app = Math.min(g.app, aa); } });
  });
  const cmpArr = (a, b) => { for (let i = 0; i < a.length; i++) { if (a[i] < b[i]) return -1; if (a[i] > b[i]) return 1; } return 0; };
  const gkey = (r) => { const c = gmin.get(`${r.product}|${r.cluster}`), i = gmin.get(`${r.product}|${r.cluster}|${r.icp}`);
    return [pOrder[r.product], c.pos, c.app, r.cluster.toLowerCase(), i.pos, i.app, r.icp.toLowerCase(), posOf(r), r.pain_point.toLowerCase(), appOf(r)]; };
  const gcache = new Map();
  const cmpGroup = (a, b) => { for (const p of [a, b]) if (!gcache.has(p)) gcache.set(p, gkey(p.r)); return cmpArr(gcache.get(a), gcache.get(b)); };
  let runs = [];                                                                             // the merged cells of the rows shown: { cell, members }
  const nCols = () => 12 + (actl ? 1 : 0) + (hasBriefed ? 1 : 0);
  function relayout() {                                                                      // a merged cell covers its rows plus the detail rows that are open (a closed detail row has no height)
    runs.forEach((g) => g.cell.setAttribute('rowspan', String(g.members.reduce((n, p) => n + 1 + (p.detailRow.hidden ? 0 : 1), 0))));
  }
  function applyGroups(list) {
    const on = !state.key;
    table.classList.toggle('pri-grouped', on);
    pairs.forEach((p) => {
      Object.values(p.cells).forEach((c) => { c.removeAttribute('rowspan'); c.classList.remove('pri-merged', 'pri-hid'); });
      p.tr.classList.remove('pri-g-prod', 'pri-g-cl', 'pri-g-icp');
      p.detailRow._sp.hidden = true; p.detailRow._main.colSpan = nCols();
    });
    runs = [];
    if (!on) return;
    const level = [['product', (r) => r.product, 'pri-g-prod'], ['cluster', (r) => r.product + '|' + r.cluster, 'pri-g-cl'], ['icp', (r) => r.product + '|' + r.cluster + '|' + r.icp, 'pri-g-icp']];
    level.forEach(([name, kf, cls]) => {
      let i = 0;
      while (i < list.length) {
        let j = i; const k = kf(list[i].r);
        while (j + 1 < list.length && kf(list[j + 1].r) === k) j += 1;
        const members = list.slice(i, j + 1);
        list[i].tr.classList.add(cls);
        members.forEach((p, n) => { const c = p.cells[name]; if (n === 0) c.classList.add('pri-merged'); else c.classList.add('pri-hid'); });
        runs.push({ cell: list[i].cells[name], members });
        i = j + 1;
      }
    });
    list.forEach((p) => { p.detailRow._sp.hidden = false; p.detailRow._main.colSpan = nCols() - 4; });      // under the merged cells the panel starts at the Pain Point column
    relayout();
  }
  const tbody = h('tbody', { 'data-testid': 'pri-body' });
  const count = h('span', { class: 'pri-shown', 'aria-live': 'polite', 'data-testid': 'pri-shown' });
  const sortBtns = {};
  const bTok = readonly ? view.token : tok;                                                 // briefed checkmarks live on a saved view: the share page and the in-page view switcher, never on All concepts
  const hasBriefed = !!bTok;
  const briefed = { ...(((readonly ? view : act && act.view) || {}).briefed || {}) };
  const isBriefed = (r) => !!briefed[keyOf(r)];
  const briefTxt = (r) => { const b = briefed[keyOf(r)]; return b ? `Briefed ${b.at.slice(0, 10)}${b.note ? ': ' + b.note : ''}` : 'Not briefed'; };
  const briefProgress = h('span', { class: 'pri-bprog', 'data-testid': 'pri-brief-progress', 'aria-live': 'polite' });
  // the per-row Briefed toggle (separate from the selection checkbox): saved on the server per view + row key, so everyone with the link sees the same
  const briefErr = h('span', { class: 'pri-err', 'data-testid': 'pri-brief-error', role: 'alert' });
  async function setBriefed(p, on, note) {
    const key = keyOf(p.r);
    try {
      const res = await api(`/api/priorities/views/${bTok}/briefed`, {}, { method: 'PATCH', body: { key, briefed: on, note: note === undefined ? ((briefed[key] || {}).note || '') : note } });
      if (res.briefed) briefed[key] = { at: res.at, note: res.note }; else delete briefed[key];
      briefErr.textContent = '';
      paintBrief(p); apply(); buildPrint();
    } catch (e) { briefErr.textContent = 'Could not save the briefed mark: ' + (e.message || 'error'); }
  }
  function paintBrief(p) {
    const on = isBriefed(p.r); const b = briefed[keyOf(p.r)] || {};
    p.tr.classList.toggle('pri-briefed', on);
    p.briefTd.replaceChildren(...[
      h('button', { type: 'button', class: 'pri-brief' + (on ? ' on' : ''), 'aria-pressed': String(on), 'data-testid': 'pri-brief-toggle', title: on ? `${briefTxt(p.r)}. Click to undo.` : 'Mark this concept as briefed in (sent to the team)', 'aria-label': (on ? 'Briefed, undo: ' : 'Mark briefed: ') + p.tr.querySelector('.pri-c-pain').textContent,
        onclick: () => setBriefed(p, !on) }, h('span', { class: 'pri-bmark', 'aria-hidden': 'true' }, '\u2713'), on ? 'Briefed' : 'Mark briefed'),
      on ? h('span', { class: 'pri-bwhen', 'data-testid': 'pri-brief-when' }, ' ' + b.at.slice(5, 10).replace('-', '/')) : null,
      on ? h('button', { type: 'button', class: 'pri-bnotebtn', 'data-testid': 'pri-brief-note', title: b.note ? 'Edit the briefed note' : 'Add a short note, e.g. sent to Madre 11/12', onclick: async () => {
        const dr = await nameDialog({ title: 'Briefed note', initial: b.note || '', okLabel: 'Save note', field: 'Note', placeholder: 'sent to Madre 11/12', max: 200, emptyOk: true, note: `${p.r.cluster} / ${p.r.icp} / ${p.r.pain_point}` });
        if (dr !== null) setBriefed(p, true, dr.name);
      } }, b.note || '+ note') : null].filter(Boolean));
  }
  if (hasBriefed) pairs.forEach((p) => { p.briefTd = h('td', { class: 'pri-c pri-c-brief', 'data-label': 'Briefed' }); p.tr.append(p.briefTd); paintBrief(p); });
  const viewCount = h('span', { 'data-testid': 'pri-view-count' });

  const selHint = h('span', { class: 'pri-selhint', 'data-testid': 'pri-sel-hint', 'aria-live': 'polite' });
  function apply() {
    if (state.onlySel && sel.size === 0) { state.onlySel = false; selHint.textContent = 'Nothing is selected, so all concepts are shown again.'; }     // an empty selection turns the toggle off
    else if (sel.size > 0 || !state.onlySel) selHint.textContent = '';
    const q = state.q.trim().toLowerCase();
    const eligible = pairs.filter((p) => state.showDone || !p.r.done);                               // done manual concepts stay hidden until Show done is ticked
    let list = eligible.filter((p) => state.products.has(p.r.product) && state.sections.has(p.r.section) && (!state.statuses.size || state.statuses.has(p.r.status)) && (!state.onlySel || sel.has(keyOf(p.r))) && !(actl && actl.hidden(p.r)) && (state.brief === 'all' || (state.brief === 'done') === isBriefed(p.r))
      && (!q || [p.r.cluster, p.r.icp, p.r.pain_point, p.r.story].some((t) => (t || '').toLowerCase().includes(q))));
    const def = (a, b) => (sOrder[a.r.section] - sOrder[b.r.section]) || (pOrder[a.r.product] - pOrder[b.r.product]) || (a.r.rank - b.r.rank);
    if (state.key) {                                                                         // a column sort: plain rows, no merged cells
      const f = value[state.key];
      list = list.sort((a, b) => { const x = f(a.r), y = f(b.r); return (x < y ? -1 : x > y ? 1 : 0) * state.dir || def(a, b); });
    } else list = list.sort(cmpGroup);                                                       // no sort chosen: product > market cluster > ICP > pain point in the sheet's order
    tbody.replaceChildren(...list.flatMap((p) => [p.tr, p.detailRow]));
    shownList = list;
    applyGroups(list);
    renderTotals(list);
    count.textContent = `${list.length} of ${eligible.length} concepts shown`;
    const nB = eligible.filter((p) => isBriefed(p.r)).length;
    briefProgress.textContent = `${nB} of ${eligible.length} briefed`;
    const nView = act && act.view.kind === 'filters' ? list.length : eligible.length;
    viewCount.textContent = `${nView} concept${nView === 1 ? '' : 's'}`;
    syncPicks();
    syncUI();
    for (const [k, b] of Object.entries(sortBtns)) b.closest('th').setAttribute('aria-sort', state.key === k ? (state.dir === 1 ? 'ascending' : 'descending') : 'none');
    return list;
  }
  const setAll = (open) => apply().forEach((p) => setOpen(p.tr.querySelector('.pri-toggle'), p.detailRow, open));          // Expand / Collapse all act on the filtered rows

  const uiSync = [];                                                                                // every filter control registers how to redraw itself from `state`
  const syncUI = () => uiSync.forEach((f) => f());
  // filter chip: outlined with a colour dot when off, solid in its colour when on. With nothing narrowed (every chip in the group included) all chips stay in the off style: no filter = neutral.
  const chip = (set, key, label, testid, all, cc) => {
    const on = () => set.has(key) && !all.every((x) => set.has(x));
    const b = h('button', { type: 'button', class: 'pri-chipbtn pri-cc ' + cc, 'aria-pressed': String(set.has(key)), 'data-on': String(on()), 'data-testid': testid, onclick: () => {
      if (all.every((x) => set.has(x))) { set.clear(); set.add(key); }                         // nothing selected yet (everything shown): the first click selects just that chip
      else if (set.has(key)) { set.delete(key); if (!set.size) all.forEach((x) => set.add(x)); }   // deselecting the last one goes back to no filter
      else set.add(key);
      apply();
    } }, h('i', { class: 'pri-cdot', 'aria-hidden': 'true' }), label);
    uiSync.push(() => { b.setAttribute('aria-pressed', String(set.has(key))); b.setAttribute('data-on', String(on())); });
    return b;
  };
  const statusHost = h('div', { class: 'pri-stats', 'data-testid': 'pri-statstrip', role: 'group', 'aria-label': 'Lines by status' });          // filled by buildTop (needs the counts)
  const actionsHost = h('div', { class: 'pri-actions pri-hd-screen' });                                                                 // Add concept + export, filled by buildTop
  const searchEl = readonly ? null : searchCtl({ name: 'Search', placeholder: 'Cluster, ideal customer profile, pain point or story', value: state.q, testid: 'pri-search', onInput: (v) => { state.q = v; apply(); } });
  const searchIn = searchEl ? searchEl.querySelector('input') : null;
  if (searchEl) searchEl.classList.add('pri-search', 'grow');
  uiSync.push(() => { if (searchIn && searchIn.value !== state.q) searchIn.value = state.q; });
  const nActive = () => (slugs.every((x) => state.products.has(x)) ? 0 : 1) + (sections.every((x) => state.sections.has(x)) ? 0 : 1) + (state.statuses.size ? 1 : 0) + (state.q.trim() ? 1 : 0) + (state.onlySel ? 1 : 0) + (state.brief !== 'all' ? 1 : 0) + (actl && actl.hideOn() ? 1 : 0);
  const clearBtn = h('button', { type: 'button', class: 'pri-clear', 'data-testid': 'pri-clear-filters', onclick: () => {
    slugs.forEach((x) => state.products.add(x)); sections.forEach((x) => state.sections.add(x)); state.statuses.clear(); state.q = ''; state.onlySel = false; state.brief = 'all'; if (actl) actl.setHide(false); apply();
  } }, 'Clear filters');
  const activeNote = h('span', { class: 'pri-activen', 'data-testid': 'pri-active-filters', 'aria-live': 'polite' });
  uiSync.push(() => { const n = nActive(); clearBtn.hidden = n === 0; activeNote.textContent = n ? `${n} filter${n === 1 ? '' : 's'} on` : ''; });
  const selChip = h('button', { type: 'button', class: 'pri-chipbtn pri-selchip', 'aria-pressed': 'false', 'data-testid': 'pri-f-selected', title: 'Show only the rows you ticked', onclick: () => { state.onlySel = !state.onlySel; apply(); } });
  uiSync.push(() => { selChip.textContent = `Selected (${sel.size})`; selChip.setAttribute('aria-pressed', String(state.onlySel)); selChip.disabled = sel.size === 0 && !state.onlySel; });
  const frow = (label, ...kids) => h('div', { class: 'pri-frow' }, h('span', { class: 'pri-flabel' }, label), h('div', { class: 'pri-fitems' }, ...kids));
  const inView = !!act || readonly;                                                              // inside a view the product chips carry the number of concepts per product
  const ctlg = (name, ...kids) => h('div', { class: 'ctl pri-ctlg' }, h('span', { class: 'ctl-name' }, name), ...kids);
  const prodRow = ctlg('Product', h('div', { class: 'pri-fgroup', role: 'group', 'aria-label': 'Product' }, slugs.map((x) => chip(state.products, x, d.baselines[x].product_name + (inView ? ` (${d.rows.filter((r) => r.product === x && !r.done).length})` : ''), 'pri-f-product-' + x, slugs, 'pri-cc-' + x))));
  const segBrief = (v, label) => { const b = h('button', { type: 'button', class: 'pri-segbtn', 'aria-pressed': 'false', 'data-testid': 'pri-f-brief-' + v, onclick: () => { state.brief = v; apply(); } }, label); uiSync.push(() => b.setAttribute('aria-pressed', String(state.brief === v))); return b; };
  const hideBtn = h('button', { type: 'button', class: 'pri-chipbtn', 'aria-pressed': 'false', 'data-testid': 'pri-hide-briefed', title: 'Hide the concepts already briefed in', onclick: () => { state.brief = state.brief === 'open' ? 'all' : 'open'; apply(); } }, 'Hide briefed');
  uiSync.push(() => hideBtn.setAttribute('aria-pressed', String(state.brief === 'open')));
  const briefRow = hasBriefed ? frow('Briefed', h('div', { class: 'pri-briefctl' }, h('span', { class: 'pri-seg', role: 'group', 'aria-label': 'Briefed filter' }, segBrief('all', 'All'), segBrief('open', 'Not briefed'), segBrief('done', 'Briefed')), hideBtn, briefProgress, briefErr)) : null;
  // one filter row with the common filters (product, action, search); status, selected rows, done and assigned rows sit under More filters, which opens by itself when one of them is on
  const statusRowEl = d.stats && d.stats.status_counts_total ? frow('Status', statusHost) : null;
  const showDoneEl = d.rows.some((r) => r.manual && r.done) ? h('label', { class: 'pri-showdone' }, h('input', { type: 'checkbox', 'data-testid': 'pri-show-done', checked: state.showDone,
    onchange: (e) => { state.showDone = e.target.checked; apply(); } }), ' Show done') : null;
  const moreBox = h('div', { class: 'pri-moref', id: 'pri-more-box', hidden: true, 'data-testid': 'pri-more-box' }, statusRowEl,
    h('div', { class: 'pri-frow pri-frow-inline' }, showDoneEl, readonly ? null : selChip, actl ? actl.hideLabel : null));
  const moreFilt = h('button', { type: 'button', class: 'btn small pri-morebtn', 'aria-expanded': 'false', 'aria-controls': 'pri-more-box', 'data-testid': 'pri-more-filters', onclick: () => setMoreOpen(moreBox.hidden) }, 'More filters');
  const setMoreOpen = (open) => { moreBox.hidden = !open; moreFilt.setAttribute('aria-expanded', String(open)); };
  const nMore = () => (state.statuses.size ? 1 : 0) + (state.onlySel ? 1 : 0) + (state.showDone ? 1 : 0) + (actl && actl.hideOn() ? 1 : 0);
  let prevMore = 0;
  uiSync.push(() => { const n = nMore(); moreFilt.textContent = n ? `More filters (${n})` : 'More filters'; if (n > prevMore && moreBox.hidden) setMoreOpen(true); prevMore = n; });
  const filters = h('div', { class: 'pri-fbar', role: 'group', 'aria-label': 'Filters', 'data-testid': 'pri-filters' },
    filterRow(prodRow, readonly ? null : ctlg('Action', h('div', { class: 'pri-fgroup', role: 'group', 'aria-label': 'Action' }, sections.map((k) => chip(state.sections, k, SECTION_LABEL[k], 'pri-f-section-' + k, sections, 'pri-cc-sec-' + k)))),
      searchEl, moreFilt, activeNote, clearBtn, actionsHost),
    briefRow, moreBox);

  const allBox = h('input', { type: 'checkbox', class: 'pri-pick', 'data-testid': 'pri-pick-all', 'aria-label': 'Select all shown rows', onchange: (e) => {
    shownList.forEach((p) => { if (e.target.checked) sel.add(keyOf(p.r)); else sel.delete(keyOf(p.r)); });
    selChanged();
  } });
  const head = h('thead', null, h('tr', null, h('th', { class: 'pri-th-tog' }, h('span', { class: 'pri-vh' }, 'Open'), readonly ? null : allBox), [...COLS, ...(actl ? [['prod', 'In production']] : []), ...(hasBriefed ? [['briefed', 'Briefed']] : [])].map(([k, label]) => {
    const b = h('button', { type: 'button', class: 'pri-sort', title: SORT_TIP[k] || null, 'data-testid': 'pri-sort-' + k, onclick: () => {
      if (state.key === k) state.dir = -state.dir; else { state.key = k; state.dir = 1; }
      apply();
    } }, label, h('span', { class: 'pri-arrow', 'aria-hidden': 'true' }, ' ⇅'));
    sortBtns[k] = b;
    return h('th', { scope: 'col', class: 'pri-th pri-th-' + k, 'aria-sort': 'none' }, b);
  })));
  const tfoot = h('tfoot', { class: 'pri-tfoot', 'data-testid': 'pri-totals' });
  const recon = h('div', { class: 'pri-recon', 'data-testid': 'pri-recon' });
  const table = h('table', { class: 'pri-table pri-main', 'data-testid': 'pri-table' }, h('caption', { class: 'pri-vh' }, 'Priorities, one row per concept'), head, tbody, tfoot);
  // totals of the rows shown (filters, search, selected-only and views all change them) + a per-product line when several products are shown, and an honest reconciliation with the product tiles
  function renderTotals(list) {
    const rows = list.map((p) => p.r);
    const prods = slugs.filter((x) => rows.some((r) => r.product === x && has14(r)));
    const cell = (label, cls, ...kids) => h('td', { class: 'pri-c pri-c-' + cls, 'data-label': label }, ...kids);
    const trow = (cls, label, t, slug) => h('tr', { class: 'pri-trow ' + cls, 'data-testid': slug ? 'pri-subtotal' : 'pri-total', 'data-product': slug || '' },
      h('td', { class: 'pri-c pri-c-tlabel', colspan: '10' }, h('strong', null, label), h('span', { class: 'pri-k' }, ` (${t.n} row${t.n === 1 ? '' : 's'} with data)`)),
      cell('Spend \u00b7 14d', 'spend14', h('strong', { 'data-testid': 'pri-total-spend' }, fmt.money(t.spend))),
      cell('ROAS \u00b7 14d', 'roas14', h('strong', { 'data-testid': 'pri-total-roas' }, t.roas === null ? '\u2013' : roasX(t.roas))),
      actl ? h('td', null) : null, hasBriefed ? h('td', null) : null);
    const out = [];
    if (prods.length > 1) prods.forEach((x) => out.push(trow('pri-tot-' + x, (d.baselines[x] || {}).product_name || x, sum14(rows.filter((r) => r.product === x)), x)));
    out.push(trow('pri-tot-all', prods.length > 1 ? 'Total, all products shown' : 'Total shown', sum14(rows)));
    tfoot.replaceChildren(...out);
    const lines = prods.filter((x) => d.baselines[x] && isNum(d.baselines[x].spend_14d)).map((x) => {
      const b = d.baselines[x], t = sum14(rows.filter((r) => r.product === x)), pct0 = b.spend_14d ? Math.round(100 * t.spend / b.spend_14d) : 0;
      const nol = isNum(b.spend_14d_no_line) ? b.spend_14d_no_line : 0, rest = Math.max(0, b.spend_14d - t.spend - nol);
      const nl = (isNum(b.spend_14d_no_line) ? ` ${fmt.money(nol)} is on ads with no line;` : '') + ` ${fmt.money(rest)} is on lines with no row here (average or too-early lines) or on rows hidden by filters.`;
      return h('div', { class: 'pri-recon-l', 'data-product': x }, h('strong', null, b.product_name + ': '), `shown rows = ${fmt.money(t.spend)} of the product\u2019s ${fmt.money(b.spend_14d)} 14-day spend (${pct0}%).${nl}`);
    });
    recon.replaceChildren(...(lines.length ? [...lines, h('div', { class: 'pri-k pri-recon-n' }, 'The tile counts all Meta spend for the product. The table lists concept lines only, so ads with no line, lines with no row and rows hidden by filters are not in the total; ROAS is total purchase value / total spend, not an average of the cells.')] : []));
  }

  const weekCtl = selectCtl({ name: 'Week', options: d.weeks.map((w) => [w.week_start, 'Week of ' + w.week_start]), value: d.week_start, testid: 'pri-week', onChange: (v) => navigate(href('/priorities', { week: v })) });
  weekCtl.querySelector('select').id = 'pri-week';
  const frozenMode = !!act && vm.mode === 'frozen';
  const csvBase = readonly ? `/api/priorities/views/${view.token}/csv` : frozenMode ? `/api/priorities/views/${tok}/csv` : '/api/priorities.csv' + qs({ week: d.week_start, name: act ? act.view.name : '', briefed_from: act ? tok : '' });
  const csvUrl = () => csvBase + (sel.size ? (csvBase.includes('?') ? '&' : '?') + [...sel].map((k) => 'id=' + encodeURIComponent(k)).join('&') : '');
  const csvLink = h('a', { class: 'btn', href: csvBase, download: '', 'data-testid': 'pri-csv' }, 'Download CSV');
  const printLabel = h('span', null, 'Print / save as PDF');
  // ---------- page header (built after the view switcher below exists) ----------
  const notes = () => [
    h('p', { class: 'pri-lead' }, `Week of ${d.week_start}. Data through ${d.data_through}. Numbers are Meta-reported (ROAS, return on ad spend = purchase value / spend; CPA, cost per acquisition = spend / purchases); all time = since each ad launched, last 30 days = the most recent 30 days of data. A hit is an ad that reached $10,000 spend; tested = $100+; launched = every ad with a launch date or any spend. Upstack new-customer ROAS is separate and shown in the row panels. `
      + 'This is prioritisation: consistent with the numbers, not proof that more concepts will work. ', h('span', { class: 'pri-screen-note' }, 'Every row is collapsed: open a row for the full detail.')),
    h('p', { class: 'pri-lead', 'data-testid': 'pri-comp-asof' }, d.competitors_as_of ? `Competitor ads: Meta Ad Library, as of ${d.competitors_as_of}; refreshed weekly. Running a long time is a signal, not proven performance.`
      + ((d.competitor_stale || []).length ? ` Stale (last pull failed, last good data kept): ${d.competitor_stale.join(', ')}.` : '') : 'No competitor ads loaded yet.')];
  const legend = (tid) => h('dl', { class: 'pri-legend', 'data-testid': tid }, (d.legend || []).flatMap((x) => [h('dt', null, x.tag), h('dd', null, x.meaning)]));
  const counts = d.stats && d.stats.status_counts_total ? Object.entries(d.stats.status_counts_total).sort((a, b) => (STATUS_ORDER.indexOf(a[0]) + 1 || 99) - (STATUS_ORDER.indexOf(b[0]) + 1 || 99)) : [];
  const baseLine = (b) => b.funnel
    ? ` ALL TIME: ROAS ${roasX(b.roas)}, CPA ${fmt.money(b.cpa)}. LAST 30 DAYS: ROAS ${roasX(b.roas_30d)}, CPA ${fmt.money(b.cpa_30d)}`
      + (isNum(b.spend_14d) ? `. LAST 14 DAYS: ROAS ${roasX(b.roas_14d)}, CPA ${fmt.money(b.cpa_14d)}, spend ${fmt.money(b.spend_14d)}` : '')
      + `. Hit rate (ads that reached $10K+ spend): lifetime ${share(b.funnel.top, b.funnel.launched)} (${fmt.int(b.funnel.top)} of ${fmt.int(b.funnel.launched)} launched ads)`
      + ((b.cohorts || [])[0] ? `; launched in last 30 days ${share(b.cohorts[0].top, b.cohorts[0].launched)} (${fmt.int(b.cohorts[0].top)} of ${fmt.int(b.cohorts[0].launched)})` : '')
      + (b.cohort_14d ? `; launched in last 14 days ${share(b.cohort_14d.top, b.cohort_14d.launched)} (${fmt.int(b.cohort_14d.top)} of ${fmt.int(b.cohort_14d.launched)})` : '') + '.'
    : ` average ROAS ${roasX(b.roas)} all time, ${roasX(b.roas_30d)} last 30 days, CPA ${fmt.money(b.cpa)}, hit rate ${pct(b.hit_rate)}`;
  // the rule for every Action and every Status, with the real thresholds from the API; open by default, can be collapsed
  const defsBlock = h('details', { class: 'pri-defs', open: true, 'data-testid': 'pri-defs' }, h('summary', null, 'What these mean'),
    h('div', { class: 'pri-defs-b' },
      h('div', null, h('h3', null, 'Action: what to do with a concept'), h('dl', { 'data-testid': 'pri-defs-actions' }, (d.definitions ? d.definitions.actions : []).flatMap((a) => [h('dt', null, a.label), h('dd', null, a.rule)]))),
      h('div', null, h('h3', null, 'Status: how the line is doing'), h('dl', { 'data-testid': 'pri-defs-statuses' }, (d.definitions ? d.definitions.statuses : []).flatMap((x) => [h('dt', null, x.label), h('dd', null, x.rule)])))));
  const buildTop = () => {
    const sub = `Week of ${d.week_start} · Data through ${d.data_through} · ` + (d.competitors_as_of ? `Competitor ads as of ${d.competitors_as_of}` : 'No competitor ads loaded yet');
    // status strip: the 'Lines by status' counts as chips with numbers; click = filter the table by that status
    const statTile = (k, v) => {
      const tone = STATUS_CLASS[k] || 'flat';
      const inner = [h('span', { class: 'pri-stat-n' }, String(v)), h('span', { class: 'pri-stat-k' }, k)];
      if (readonly) return h('div', { class: 'pri-stat pri-stat-' + tone, 'data-testid': 'pri-stat' }, inner);
      const b = h('button', { type: 'button', class: 'pri-stat pri-stat-' + tone, 'aria-pressed': 'false', 'data-testid': 'pri-stat', 'data-status': k, title: `${v} lines have this status in total. Click to show only the concepts with this status.`,
        onclick: () => { if (state.statuses.has(k)) state.statuses.delete(k); else state.statuses.add(k); apply(); } }, inner);
      uiSync.push(() => b.setAttribute('aria-pressed', String(state.statuses.has(k))));
      return b;
    };
    const delta = (cur, base, lowerBetter) => {                       // chip: last 30 days vs all time; green = better, red = worse
      if (!isNum(cur) || !isNum(base) || !base) return null;
      const dv = (cur - base) / base, good = lowerBetter ? dv < 0 : dv > 0, flat = Math.abs(dv) < 0.02;
      return h('span', { class: 'pri-delta ' + (flat ? 'pri-delta-flat' : good ? 'pri-delta-up' : 'pri-delta-down'), 'data-testid': 'pri-delta' }, `${dv > 0 ? '+' : dv < 0 ? '\u2212' : ''}${Math.abs(Math.round(dv * 100))}% vs all time`);
    };
    const prodTile = (x) => {
      const b = d.baselines[x], fn = b.funnel, c14 = b.cohort_14d;
      const stat = (label, v, chip) => h('div', { class: 'pri-pstat' }, h('span', { class: 'pri-label', title: label.startsWith('ROAS') ? 'Return on ad spend: purchase value divided by spend' : label.startsWith('CPA') ? 'Cost per acquisition: spend divided by purchases' : null }, label), h('strong', null, v), chip || null);
      const allBlock = h('div', { class: 'pri-pblock', 'data-testid': 'pri-pblock-all' },
        h('span', { class: 'pri-pbh' }, 'All time'),
        h('div', { class: 'pri-pstats' }, stat('ROAS (all time)', roasX(b.roas)), stat('CPA (all time)', fmt.money(b.cpa)), stat('Spend', b.spend != null ? compact$(b.spend) : '\u2013'), stat('Purchases', b.purchases != null ? fmt.int(b.purchases) : '\u2013')));
      const liveBlock = h('div', { class: 'pri-pblock pri-pblock-30', 'data-testid': 'pri-pblock-30d' },
        h('span', { class: 'pri-pbh' }, 'Last 30 days'), h('span', { class: 'pri-k pri-pbn' }, 'Activity in the window: ROAS, CPA, spend, purchases, ads live'),
        h('div', { class: 'pri-pstats' }, stat('ROAS (30 days)', roasX(b.roas_30d), delta(b.roas_30d, b.roas, false)), stat('CPA (30 days)', fmt.money(b.cpa_30d), delta(b.cpa_30d, b.cpa, true)),
          stat('Spend', b.spend_30d != null ? fmt.money(b.spend_30d) : '\u2013'), stat('Purchases', b.purchases_30d != null ? fmt.int(b.purchases_30d) : '\u2013'),
          stat('Ads live now', b.live_ads != null ? fmt.int(b.live_ads) : '\u2013'), stat('New ads launched', fmt.int(b.new_ads_30d))));
      const block14 = isNum(b.spend_14d) ? h('div', { class: 'pri-pblock pri-pblock-30 pri-pblock-14', 'data-testid': 'pri-pblock-14d' },
        h('span', { class: 'pri-pbh' }, 'Last 14 days'), h('span', { class: 'pri-k pri-pbn' }, 'Activity in the window: ROAS, CPA, spend, purchases, ads that spent'),
        h('div', { class: 'pri-pstats' }, stat('ROAS (14 days)', roasX(b.roas_14d), delta(b.roas_14d, b.roas, false)), stat('CPA (14 days)', fmt.money(b.cpa_14d), delta(b.cpa_14d, b.cpa, true)),
          stat('Spend', fmt.money(b.spend_14d)), stat('Purchases', isNum(b.purchases_14d) ? fmt.int(b.purchases_14d) : '\u2013'),
          stat('Ads live now', isNum(b.live_14d) ? fmt.int(b.live_14d) : '\u2013'), stat('New ads launched', isNum(b.new_ads_14d) ? fmt.int(b.new_ads_14d) : '\u2013'))) : null;
      const hitCell = (label, n, m, tid) => h('div', { class: 'pri-hitc', 'data-testid': tid }, h('span', { class: 'pri-label' }, label), h('strong', null, share(n, m)), h('span', { class: 'pri-k' }, `${fmt.int(n)} of ${fmt.int(m)} launched ads`));
      const c30 = (b.cohorts || [])[0];
      const hitStrip = h('div', { class: 'pri-hitstrip', 'data-testid': 'pri-hitstrip' },
        h('span', { class: 'pri-pbh' }, 'Hit rate (ads that reached $10K+ spend)'),
        fn ? h('div', { class: 'pri-hitrow' }, hitCell('Lifetime', fn.top, fn.launched, 'pri-hit-all'), c30 ? hitCell('Launched in last 30 days', c30.top, c30.launched, 'pri-hit-30') : null, c14 ? hitCell('Launched in last 14 days', c14.top, c14.launched, 'pri-hit-14') : null)
          : h('div', { class: 'pri-hitrow' }, h('div', { class: 'pri-hitc', 'data-testid': 'pri-hit-all' }, h('span', { class: 'pri-label' }, 'Ads that reached $1,000 spend'), h('strong', null, pct(b.hit_rate)), h('span', { class: 'pri-k' }, `${fmt.int(b.hits)} of ${fmt.int(b.judged)} ads that spent $100+`))));
      const inner = [h('span', { class: 'pri-pname' }, h('i', { class: 'pri-pdot', 'aria-hidden': 'true' }), b.product_name), allBlock, liveBlock, block14, hitStrip];
      const tip = `${b.product_name}: ROAS ${roasX(b.roas)} all time, ${roasX(b.roas_30d)} last 30 days; CPA ${fmt.money(b.cpa)} all time. Click to show only ${b.product_name}.`;
      if (readonly) return h('div', { class: 'pri-ptile', 'data-prod': x, 'data-testid': 'pri-ptile' }, inner);
      const el = h('button', { type: 'button', class: 'pri-ptile', 'aria-pressed': 'false', 'data-testid': 'pri-ptile', 'data-product': x, 'data-prod': x, title: tip,
        onclick: () => { const only = state.products.size === 1 && state.products.has(x); state.products.clear(); (only ? slugs : [x]).forEach((y) => state.products.add(y)); apply(); } }, inner);
      uiSync.push(() => el.setAttribute('aria-pressed', String(state.products.size === 1 && state.products.has(x))));
      return el;
    };
    const through = d.data_through;
    const metricHelp = h('details', { class: 'pri-mhelp', 'data-testid': 'pri-metric-help' },
      h('summary', { 'aria-label': 'What do these numbers mean?' }, h('span', { class: 'pri-mq', 'aria-hidden': 'true' }, '?'), ' What do these numbers mean?'),
      h('ul', null,
        h('li', null, h('strong', null, 'ROAS'), ' (return on ad spend) = Meta-reported purchase value divided by spend. ', h('strong', null, 'All time'), ' = since each ad launched; ', h('strong', null, 'last 30 days'), ` = the 30 days up to ${through}.`),
        h('li', null, h('strong', null, 'Spend \u00b7 14d / ROAS \u00b7 14d'), ' (table columns) = the 14 days up to the data-through date, raw purchase value / spend; ROAS shows a dash under $100 spend in the window. The arrow compares it with the product\u2019s last-30-day ROAS (green up, red down, 5% or more).'),
        h('li', null, h('strong', null, 'Last 14 days'), ' block: same measures over the 14 days ending on the data-through date. Ads live now = ads that spent in the window (30-day block: spent in the last 30 days; 14-day block: in the last 14). New ads launched = ads whose first spend was in the window.'),
        h('li', null, h('strong', null, 'Total row'), ' under the table = the 14-day spend and spend-weighted ROAS of the rows shown. It will not equal the tile: the tile covers every ad of the product, the table only concept lines (not ads with no line, not lines without a row, not rows hidden by filters). The note under it shows the gap per product.'),
        h('li', null, h('strong', null, 'CPA'), ' (cost per acquisition, here cost per purchase) = spend divided by purchases, same two windows.'),
        h('li', null, h('strong', null, 'Hit rate'), ' = share of ads launched in the period that have spent $10,000 or more to date; recent ads have had less time to get there. Lifetime = all launched ads; the 30-day and 14-day rates only count ads launched in that window (launch date from the tracker, else first day with spend). Launched counts every ad with a launch date or any spend, including ads that never spent $100.'),
        h('li', null, h('strong', null, 'Last 30 days / last 14 days'), ' blocks: ROAS, CPA, spend, purchases and ads live are activity inside the window.'),
        h('li', null, 'The green / red chip compares the last 30 days with all time (higher ROAS and lower CPA are green).'),
        h('li', null, 'Upstack new-customer ROAS (return on ad spend on first orders only) is a separate measure and is shown in each row panel.'),
        h('li', null, `Data through ${through}.`)));
    statusHost.replaceChildren(...counts.map(([k, v]) => statTile(k, v)));
    actionsHost.replaceChildren(
      readonly || !opts.reload ? null : h('button', { type: 'button', class: 'btn primary pri-add', 'data-testid': 'pri-add-concept', onclick: () => mctl.add() }, 'Add concept'),
      h('span', { class: 'pri-btngroup', role: 'group', 'aria-label': 'Export' }, csvLink, h('button', { type: 'button', class: 'btn', 'data-testid': 'pri-print', onclick: () => window.print() }, printLabel)));
    const prodLink = h('a', { class: 'btn pri-prodlink', href: href('/priorities/production', {}), title: 'Concepts assigned to vendors, by week', 'data-testid': 'pri-prod-link' }, 'Production board');
    if (actl) actl.onCount((n) => { prodLink.textContent = n === null ? 'Production board' : `Production board (${n})`; });
    const about = (title, tid, ...kids) => h('details', { class: 'pri-about', 'data-testid': tid }, h('summary', null, title), h('div', { class: 'pri-about-b' }, ...kids));
    const head = pageHeader({
      title: readonly ? `Priorities: ${view.name}` : act ? `Priorities: ${act.view.name}` : 'Priorities',
      description: sub,
      titleTestid: 'pri-title',
      actions: readonly ? null : [act ? null : weekCtl, viewbar, prodLink],
    });
    const subEl = head.querySelector('.note-line');
    if (subEl) { subEl.classList.add('pri-sub', 'pri-hd-screen'); subEl.setAttribute('data-testid', 'pri-sub'); }
    const actsEl = head.querySelector('.pagehead-actions');
    if (actsEl) actsEl.classList.add('pri-headctl', 'pri-hd-screen');
    // product results: closed by default (the product chips below filter the same way); the open or closed choice is remembered
    const resOpen = (() => { try { return localStorage.getItem('pri-results-open') === '1'; } catch (e) { return false; } })();
    const results = h('details', { class: 'pri-results', open: resOpen, 'data-testid': 'pri-results', ontoggle: (e) => { try { localStorage.setItem('pri-results-open', e.target.open ? '1' : '0'); } catch (err) { /* blocked */ } } },
      h('summary', null, h('span', { class: 'pri-results-t' }, 'Results by product'), h('span', { class: 'pri-results-s', 'data-testid': 'pri-results-sum' }, slugs.map((x) => h('span', { class: 'pri-rs', 'data-prod': x, title: 'Return on ad spend over the last 30 days (purchase value divided by spend)' }, h('i', { class: 'pri-pdot', 'aria-hidden': 'true' }), d.baselines[x].product_name + ' ' + roasX(d.baselines[x].roas_30d))), h('span', { class: 'pri-k' }, ' return on ad spend, last 30 days'))),
      h('div', { class: 'pri-ptiles', role: 'group', 'aria-label': 'Products' }, slugs.map(prodTile)));
    // everything that explains the numbers and tags in one closed box
    const helpBox = h('details', { class: 'pri-helpbox', 'data-testid': 'pri-help' }, h('summary', null, 'Help: what the numbers, tags, actions and statuses mean'),
      h('div', { class: 'pri-helpbox-b' }, metricHelp, h('div', { class: 'pri-abouts' }, about('What these tags mean', 'pri-about-tags', legend('pri-legend-ui')), about('About these numbers', 'pri-about-numbers', ...notes())), defsBlock));
    return h('div', { class: 'pri-top' },
      head,
      readonly ? h('p', { class: 'pri-lead', 'data-testid': 'pri-view-note' }, `Saved view of ${d.rows.length} concept${d.rows.length === 1 ? '' : 's'}, snapshot week of ${d.week_start} (frozen: it does not change when a newer snapshot arrives). `,
        h('a', { href: href('/priorities', {}), 'data-testid': 'pri-open-full' }, 'Open full priorities'), ' ',
        h('a', { href: viewUrl(view.token), 'data-testid': 'pri-open-live' }, 'Open as a view with the latest data'), ' ',
        h('button', { type: 'button', class: 'btn pri-screen-note', 'data-testid': 'pri-rename', onclick: async () => {
          const dr = await nameDialog({ title: 'Rename this view', initial: view.name, okLabel: 'Rename', note: 'The link stays the same.' });
          if (dr === null) return;
          const nn = dr.name;
          try {
            const r = await api('/api/priorities/views/' + view.token, {}, { method: 'PATCH', body: { name: nn } });
            view.name = r.name;
            document.querySelector('[data-testid=pri-title]').textContent = `Priorities: ${r.name}`;
            document.title = `${r.name} · Priorities · Lonvera`;
          } catch (e) { window.alert('Could not rename: ' + (e.message || 'error')); }
        } }, 'Rename')) : null,
      h('div', { class: 'pri-dash pri-hd-screen' }, results, helpBox),
      // print copy of the intro: every sentence, the legend and the three product lines, exactly as before (hidden on screen)
      h('div', { class: 'pri-printintro', 'data-testid': 'pri-printintro' }, ...notes(),
        counts.length ? h('p', { class: 'pri-lead', 'data-testid': 'pri-counts' }, 'Lines by status: ' + counts.map(([k, v]) => `${k} ${v}`).join(', ')) : null,
        legend('pri-legend'),
        h('div', { class: 'pri-base' }, slugs.map((x) => h('div', null, h('strong', null, d.baselines[x].product_name), baseLine(d.baselines[x]))))));
  };

  // selection toolbar (sticky, only while something is selected)
  const selCount = h('strong', { 'data-testid': 'pri-sel-count' });
  const copyNote = h('span', { class: 'pri-k', 'aria-live': 'polite', 'data-testid': 'pri-copy-note' });
  const selCsv = h('a', { class: 'btn', href: csvBase, download: '', 'data-testid': 'pri-sel-csv' }, 'Download CSV');
  const linkBox = h('div', { class: 'pri-linkbox', hidden: true, 'data-testid': 'pri-linkbox', role: 'status' });
  const abs = (u) => location.origin + u;
  const copyLinkBtn = (u, label = 'Copy link') => h('button', { type: 'button', class: 'btn', 'data-testid': 'pri-copy-link', onclick: (e) => {
    const t = abs(u);
    const ok = () => { e.target.textContent = 'Copied'; setTimeout(() => { e.target.textContent = label; }, 1500); };
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(t).then(ok, () => window.prompt('Copy this link', t)); else window.prompt('Copy this link', t);
  } }, label);
  // Save view (both kinds) opens the naming dialog; `preferKind` is what the button that opened it stands for
  async function saveViewDialog(preferKind) {
    const hasSel = sel.size > 0;
    const dr = await nameDialog({ title: 'Save view', initial: defaultViewName(), okLabel: 'Save view', note: `${sel.size} concept${sel.size === 1 ? '' : 's'} selected`, kind: preferKind === 'rows' && hasSel ? 'rows' : preferKind === 'filters' ? 'filters' : (hasSel ? 'rows' : 'filters'),
      kinds: [['rows', `Selected rows (${sel.size} selected)`, !hasSel], ['filters', 'Current filters (product, section, search, sort), applied live']] });
    if (dr === null) return;
    const body = { name: dr.name, week: d.week_start, kind: dr.kind };
    if (dr.kind === 'rows') body.ids = [...sel]; else body.filters = currentFilters();
    try {
      const v = await api('/api/priorities/views', {}, { method: 'POST', body });
      linkBox.hidden = false;
      linkBox.replaceChildren(h('span', { 'data-testid': 'pri-saved-name' }, `Saved “${v.name}” `), h('span', { class: 'pri-k' }, `${v.kind === 'filters' ? `${v.count} concept${v.count === 1 ? '' : 's'} match now; new matches appear automatically` : `${v.count} concept${v.count === 1 ? '' : 's'}${v.missing ? ` (${v.missing} no longer in this snapshot)` : ''}`}. `),
        h('a', { href: viewUrl(v.token), 'data-testid': 'pri-view-open' }, 'Open it'), ' ', h('a', { href: v.url, 'data-testid': 'pri-view-link' }, abs(v.url)), ' ', copyLinkBtn(v.url));
      vm.views.unshift({ token: v.token, name: v.name, kind: v.kind, count: v.count });
      viewSel.append(h('option', { value: v.token }, viewLabel(v)));
    } catch (e) {
      linkBox.hidden = false;
      linkBox.replaceChildren(h('span', { class: 'pri-err', 'data-testid': 'pri-view-error' }, 'Could not save the view: ' + (e.message || 'error')));
    }
  }
  const selOnlyBtn = h('button', { type: 'button', class: 'btn pri-selonly', 'aria-pressed': 'false', 'data-testid': 'pri-sel-only', onclick: () => { state.onlySel = !state.onlySel; apply(); } }, 'Show selected only');
  uiSync.push(() => { selOnlyBtn.setAttribute('aria-pressed', String(state.onlySel)); selOnlyBtn.textContent = state.onlySel ? 'Show all rows' : 'Show selected only'; });
  const selSplit = h('span', { class: 'pri-selsplit', 'data-testid': 'pri-sel-split' });
  const moreBtn = h('button', { type: 'button', class: 'btn pri-selmore', 'aria-expanded': 'false', 'data-testid': 'pri-sel-more', onclick: () => { const o = toolbar.classList.toggle('pri-more-open'); moreBtn.setAttribute('aria-expanded', String(o)); moreBtn.textContent = o ? 'Less' : 'More'; } }, 'More');
  const toolbar = h('div', { class: 'pri-seltools', role: 'region', 'aria-label': 'Selected rows', hidden: true, 'data-testid': 'pri-seltools' }, h('span', { class: 'pri-selhead' }, selCount, selSplit), moreBtn, h('span', { class: 'pri-selacts', 'data-testid': 'pri-selacts' }, selCsv,
    h('button', { type: 'button', class: 'btn', 'data-testid': 'pri-sel-print', onclick: () => window.print() }, 'Print / PDF'),
    h('button', { type: 'button', class: 'btn', 'data-testid': 'pri-sel-copy', onclick: () => copyText() }, 'Copy as text'),
    selOnlyBtn,
    h('button', { type: 'button', class: 'btn', 'data-testid': 'pri-sel-save', onclick: () => saveViewDialog('rows') }, 'Save view'),
    actl ? actl.button : null,
    h('button', { type: 'button', class: 'btn', 'data-testid': 'pri-sel-clear', onclick: () => { sel.clear(); selChanged(); } }, 'Clear'), copyNote));

  // the View switcher: All concepts + each saved view; choosing one filters this same table in place (URL ?view=<token>); Save view sits beside it
  const viewLabel = (v) => `${v.name || 'Untitled view'} (${v.kind === 'filters' ? 'filters' : `${v.count} concept${v.count === 1 ? '' : 's'}`})`;
  const viewCtl = selectCtl({ name: 'View', options: [['', 'All concepts'], ...vm.views.map((v) => [v.token, viewLabel(v)])], value: tok, testid: 'pri-view-select', onChange: (t) => {
    lastView.set(t);
    navigate(t ? viewUrl(t) : href('/priorities', {}));
  } });
  const viewSel = viewCtl.querySelector('select');
  viewSel.id = 'pri-view-select'; viewSel.classList.add('pri-viewsel');
  const goRefresh = () => navigate(location.pathname + location.search, { replace: true });
  async function activeAction(fn, okMsg) {
    try { await fn(); flash.set(okMsg); goRefresh(); } catch (e) { window.alert('Could not do that: ' + (e.message || 'error')); }
  }
  const viewTools = act ? (() => {
    const v = act.view; const kindF = v.kind === 'filters';
    const fromN = d.rows.filter((r) => r._from).length;
    return h('div', { class: 'pri-activebar', role: 'region', 'aria-label': 'Active view', 'data-testid': 'pri-activebar' },
      h('div', { class: 'pri-activeline' }, h('span', null, 'Viewing: ', h('strong', { 'data-testid': 'pri-view-name-shown' }, v.name || 'Untitled view'), ' (', viewCount, ')'),
        ' · ', h('a', { href: href('/priorities', {}), 'data-testid': 'pri-back-all', onclick: (e) => { e.preventDefault(); lastView.set(''); navigate(href('/priorities', {})); } }, 'Back to all'),
        h('span', { class: 'pri-k' }, kindF ? ' Saved filters, applied live: new matching concepts appear.' : ' A fixed set of concepts.'),
        fromN ? h('span', { class: 'pri-k', 'data-testid': 'pri-from-note' }, ` ${fromN} no longer in the current snapshot (shown from ${v.week_start}).`) : null),
      h('div', { class: 'pri-activebtns' },
        h('label', { class: 'pri-datamode' }, h('span', { class: 'pri-k' }, 'Data '), h('select', { 'data-testid': 'pri-data-mode', 'aria-label': 'Frozen as saved or latest data', onchange: (e) => navigate(viewUrl(tok, e.target.value === 'frozen' ? { data: 'frozen' } : {})) },
          h('option', { value: 'latest', selected: !frozenMode }, 'Latest data'), h('option', { value: 'frozen', selected: frozenMode }, `Frozen as saved (${v.week_start})`))),
        h('button', { type: 'button', class: 'btn', 'data-testid': 'pri-view-update', title: kindF ? 'Replace this view’s filters with the filters now on the page' : 'Replace this view’s rows with the rows now selected', onclick: () => {
          if (!kindF && !sel.size) { window.alert('Tick the rows to keep in this view, then Update this view.'); return; }
          activeAction(() => api('/api/priorities/views/' + tok, {}, { method: 'PUT', body: kindF ? { filters: currentFilters() } : { ids: [...sel] } }), `Updated “${v.name}”.`);
        } }, 'Update this view'),
        h('button', { type: 'button', class: 'btn', 'data-testid': 'pri-active-rename', onclick: async () => {
          const dr = await nameDialog({ title: 'Rename this view', initial: v.name, okLabel: 'Rename', note: 'The link stays the same.' });
          if (dr === null) return;
          activeAction(() => api('/api/priorities/views/' + tok, {}, { method: 'PATCH', body: { name: dr.name } }), `Renamed to “${dr.name}”.`);
        } }, 'Rename'),
        copyLinkBtn(`/priorities/v/${tok}`, 'Copy share link'),
        h('button', { type: 'button', class: 'btn', 'data-testid': 'pri-active-delete', onclick: async () => {
          if (!window.confirm(`Delete the view “${v.name}”? Anyone with its link will lose access.`)) return;
          try { await api('/api/priorities/views/' + tok, {}, { method: 'DELETE' }); } catch (e) { /* already gone */ }
          lastView.set(''); flash.set(`Deleted “${v.name}”.`); navigate(href('/priorities', {}));
        } }, 'Delete')));
  })() : null;
  const viewbar = h('div', { class: 'pri-viewbar', role: 'group', 'aria-label': 'Saved views', 'data-testid': 'pri-viewbar' },
    viewCtl,
    h('button', { type: 'button', class: 'btn pri-savebtn', 'data-testid': 'pri-save-open', onclick: () => saveViewDialog(null) }, 'Save view'),
    vm.notice ? h('span', { class: 'pri-notice', 'data-testid': 'pri-view-notice', role: 'status' }, vm.notice) : null);
  function syncPicks() {
    pairs.forEach((p) => { p.pick.checked = isSel(p.r); });
    const n = shownList.filter((p) => isSel(p.r)).length;
    allBox.checked = shownList.length > 0 && n === shownList.length;
    allBox.indeterminate = n > 0 && n < shownList.length;
  }
  function selChanged() {
    if (readonly) { buildPrint(); return; }
    saveSel(d.week_start, sel, tok);
    if (sel.size) selHint.textContent = '';
    if (state.onlySel) apply();                                                    // an unticked row leaves the selected-only list at once
    syncPicks();
    syncUI();
    const n = sel.size;
    toolbar.hidden = n === 0;
    selCount.textContent = `${n} selected`;
    const per = PRODUCT_ORDER.map((x) => [x, d.rows.filter((r) => r.product === x && isSel(r)).length]).filter(([, c]) => c > 0);      // selected rows are counted wherever they are: a filter hiding a ticked row does not un-tick it
    selSplit.textContent = per.map(([x, c]) => `${(d.baselines[x] || {}).product_name || x} ${c}`).join(' \u00b7 ');
    csvLink.href = selCsv.href = csvUrl();
    csvLink.textContent = n ? `Download CSV (${n} selected)` : 'Download CSV';
    printLabel.textContent = n ? `Print / save as PDF (${n} selected)` : 'Print / save as PDF';
    copyNote.textContent = '';
    buildPrint();
  }
  function copyText() {
    const rows = d.rows.filter(isSel).sort((a, b) => (pOrder[a.product] - pOrder[b.product]) || (sOrder[a.section] - sOrder[b.section]) || (a.rank - b.rank));
    const text = rows.map((r) => [`Product: ${(d.baselines[r.product] || {}).product_name || r.product}`, `Action: ${SECTION_LABEL[r.section]}${r.source ? ' (' + (NEW_IDEA[r.source] || r.source) + ', not on the sheet yet)' : ''}`,
      `Market Cluster: ${r.cluster}`, `ICP: ${r.icp}`, `Pain Point: ${r.pain_point}`, `Story: ${r.story || '\u2013'}`, `Status: ${r.status}`, `Key number: ${keyNumber(r, d)}`,
      `Concepts to brief: ${r.concepts || 0}`, ...(hasBriefed ? [`Briefed: ${isBriefed(r) ? briefTxt(r).replace('Briefed ', 'yes, ') : 'no'}`] : []), ...strip(r, true).map((t, i) => (i === 0 ? 'Notes: ' : '') + t)].join('\n')).join('\n\n');
    const done = (ok) => { copyNote.textContent = ok ? `Copied ${rows.length} row${rows.length === 1 ? '' : 's'}.` : 'Copy failed: select the text and copy by hand.'; };
    const fallback = () => {
      const ta = h('textarea', { style: { position: 'fixed', opacity: '0' } }); ta.value = text; document.body.appendChild(ta); ta.select();
      let ok = false; try { ok = document.execCommand('copy'); } catch (e) { ok = false; } ta.remove(); done(ok);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(() => done(true), fallback); else fallback();
  }

  // print copy (hidden on screen): a compact one-sheeter. Intro on its own first page; per product a fresh page with the headline columns, a 2-3 line strip under each row that has concepts to brief, the
  // Stop list as plain lines. Rows never split across pages, the header repeats on every page, fixed column widths.
  const trim = (t, n) => { t = String(t || ''); return t.length <= n ? t : t.slice(0, n).replace(/\s+\S*$/, '') + '\u2026'; };
  const strip = (r, full = false) => {
    const out0 = strip0(r, full);
    return has14(r) ? [...out0.slice(0, full ? out0.length : 2), `14d: ${fmt.money(r.metrics.spend_14d)}${roas14Of(r) === null ? '' : ' at ' + roasX(roas14Of(r))}`] : out0;
  };
  const strip0 = (r, full = false) => {
    if (r.manual) return manualStrip(r, full ? 0 : 300);
    const m = r.metrics || {};
    if (r.section === 'revive') {
      const st = r.stretch;
      const then = st ? `Worked ${st.start} to ${st.end}: ROAS ${roasX(st.roas)} on ${fmt.money(st.spend)}` : 'Strong before the weekly data began (2026-05-06)';
      const now = m.spend_30d >= 250 ? `now ${roasX(m.roas_30d)} on ${fmt.money(m.spend_30d)}${st && isNum(m.roas_30d) && st.roas ? ` (${Math.round((m.roas_30d / st.roas - 1) * 100)}%)` : ''}` : 'now idle (no spend in 30 days)';
      return [`${then} \u2192 ${now}.`, trim(r.revive_play, 230)];
    }
    if (r.section === 'make_more') return [trim(r.why, 260)];
    const out = [];
    if (r.competitor) out.push(`Others: ${r.competitor.text} (running a long time, not proven performance).`);
    const why = trim(r.why, r.competitor ? 170 : 250);
    out.push(why.charAt(0).toUpperCase() + why.slice(1));
    return out.slice(0, 2);
  };
  const PRINT_STATUS = { 'Untested': 'UNTESTED', 'Fading': 'FADING', 'Working': 'WORKING', 'Rising': 'RISING', 'Average': 'AVERAGE', 'Not working': 'NOT WORKING', 'Too early': 'TOO EARLY' };
  const printCell = (cls, ...kids) => h('td', { class: 'pri-c pri-c-' + cls }, ...kids);
  const printRow = (r) => h('tbody', { class: 'pri-rg', 'data-testid': 'pri-print-row' },
    h('tr', { class: 'pri-row' }, printCell('section', h('span', { class: 'pri-sec pri-sec-' + r.section }, SECTION_LABEL[r.section]), hasBriefed && isBriefed(r) ? h('span', { class: 'pri-bprint', 'data-testid': 'pri-print-briefed' }, ' \u2713 briefed') : null, r.manual ? h('span', { class: 'pri-mbadge' }, 'Manual') : null), printCell('cluster', r.cluster), printCell('icp', r.icp), printCell('pain', r.pain_point),
      printCell('story', r.story || '\u2013'), printCell('status', h('span', { class: 'pri-status pri-st-' + (STATUS_CLASS[r.status] || 'flat') }, PRINT_STATUS[r.status] || r.status)),
      printCell('concepts', r.manual ? '\u2013' : r.concepts ? String(r.concepts) : '\u2013'), printCell('key', keyNumber(r, d))),
    h('tr', { class: 'pri-strip' }, h('td', { colspan: '8' }, strip(r).map((t) => h('div', null, t)))));
  const widths = [10, 13, 13, 14, 22, 8, 7, 13];
  const PRINT_COLS = ['section', 'cluster', 'icp', 'pain', 'story', 'status', 'concepts', 'key'].map((k) => COLS.find(([c]) => c === k));         // the printed sheet keeps Action first
  const printHead = () => h('thead', null, h('tr', null, PRINT_COLS.map(([k, label]) => h('th', { scope: 'col' }, k === 'concepts' ? 'To brief' : label))));
  const printBlocks = h('div', { class: 'pri-print', 'data-testid': 'pri-print-copy', 'aria-hidden': 'true' });
  const buildPrint = () => printBlocks.replaceChildren(...slugs.flatMap((s) => {
    const picking = sel.size > 0;
    const mine = d.rows.filter((r) => r.product === s && (picking ? isSel(r) : (!r.done && r.section !== 'hold'))).sort((a, b) => (sOrder[a.section] - sOrder[b.section]) || (a.rank - b.rank));
    if (picking && !mine.length) return [];                                          // product sections with no selected rows are omitted
    const shown = mine.filter((r) => r.section !== 'stop' && (picking || r.concepts > 0 || r.manual));
    const stops = mine.filter((r) => r.section === 'stop');
    const b = d.baselines[s];
    const skipped = picking ? 0 : mine.length - shown.length - stops.length;
    return [h('section', { class: 'pri-product', 'data-testid': 'pri-product-' + s },
      h('h2', null, `${b.product_name}`, h('span', { class: 'pri-h2note' }, ` average ROAS ${roasX(b.roas)} all time, ${roasX(b.roas_30d)} last 30 days, CPA ${fmt.money(b.cpa)} all time`)),
      h('table', { class: 'pri-table pri-ptable' }, h('colgroup', null, widths.map((w) => h('col', { style: { width: w + '%' } }))), printHead(), shown.map(printRow)),
      (() => { const t = sum14(shown); return t.n ? h('p', { class: 'pri-pnote pri-ptotal', 'data-testid': 'pri-print-total' }, `Total last 14 days, ${t.n} row${t.n === 1 ? '' : 's'} with data: ${fmt.money(t.spend)} at ${t.roas === null ? '\u2013' : roasX(t.roas)}` + (isNum(b.spend_14d) ? ` (product: ${fmt.money(b.spend_14d)} at ${roasX(b.roas_14d)})` : '') + '.') : null; })(),
      skipped > 0 ? h('p', { class: 'pri-pnote' }, `${skipped} more line${skipped === 1 ? '' : 's'} with no concepts to brief this week are on the page, not printed.`) : null,
      stops.length ? h('div', { class: 'pri-stoplist', 'data-testid': 'pri-stoplist' }, h('h3', null, `Stop (${stops.length})`),
        h('ul', null, stops.map((r) => h('li', null, h('strong', null, `${r.cluster} / ${r.icp} / ${r.pain_point}`), `: ${roasX((r.metrics || {}).roas)} since launch vs ${roasX(b.roas)} product, ${fmt.money((r.metrics || {}).spend)} spent.`)))) : null)];
  }));

  apply();
  selChanged();
  const groupBtn = h('button', { type: 'button', class: 'btn pri-groupbtn', 'aria-pressed': 'true', 'data-testid': 'pri-group-btn', title: 'Group the rows by product, market cluster, ICP and pain point, in the sheet order (clears any column sort)', onclick: () => { state.key = null; state.dir = 1; apply(); } }, 'Group by cluster');
  uiSync.push(() => groupBtn.setAttribute('aria-pressed', String(!state.key)));
  const tablebar = h('div', { class: 'pri-tablebar' }, h('span', null, count, ' ', selHint), h('span', { class: 'pri-tbctl' }, groupBtn, h('span', { class: 'pri-seg pri-expbar', role: 'group', 'aria-label': 'Expand or collapse rows' },
    h('button', { type: 'button', class: 'pri-segbtn pri-exp', 'data-testid': 'pri-all-expand', onclick: () => setAll(true) }, 'Expand all'),
    h('button', { type: 'button', class: 'pri-segbtn pri-exp', 'data-testid': 'pri-all-collapse', onclick: () => setAll(false) }, 'Collapse all'))));
  const topEl = buildTop();
  syncUI();
  const screen = h('div', { class: 'pri-screen' }, readonly ? null : viewTools, filters, readonly ? null : toolbar, readonly ? null : linkBox, tablebar, table, recon);
  // sticky offsets: the selection bar pins under the app header, the column header pins under the bar. The bar's real height goes into a CSS variable (ResizeObserver: it wraps, and the phone layout is shorter), 0 while the bar is hidden.
  const syncBarH = () => screen.style.setProperty('--pri-bar-h', (toolbar.hidden ? 0 : Math.round(toolbar.getBoundingClientRect().height)) + 'px');
  if (!readonly && typeof ResizeObserver !== 'undefined') new ResizeObserver(syncBarH).observe(toolbar);
  syncBarH();
  return h('div', { class: 'page pri-page', 'data-testid': 'pri-page' }, topEl, screen, printBlocks);
}

// the page data for an active view: live rows where the key still exists (latest mode) else the frozen copy; a row that left the current snapshot falls back to its frozen copy, marked `_from`.
function effective(d, act, mode) {
  const v = act.view; const frozen = act.rows;
  const { view, ...frozenData } = act;
  if (mode === 'frozen') return { ...frozenData, weeks: d.weeks, competitors_as_of: act.competitors_as_of };
  const baselines = { ...(act.baselines || {}), ...(d.baselines || {}) };
  if (v.kind === 'filters') return { ...d, baselines };
  const live = new Map(d.rows.map((r) => [keyOf(r), r])); const fz = new Map(frozen.map((r) => [keyOf(r), r]));
  const rows = (v.keys.length ? v.keys : frozen.map(keyOf)).map((k) => live.get(k) || (fz.get(k) ? { ...fz.get(k), _from: v.week_start } : null)).filter(Boolean);
  return { ...d, baselines, rows };
}

export async function prioritiesPage(ctx) {
  showControls(false);
  let token = ctx.params.view || '';
  const mode = ctx.params.data === 'frozen' ? 'frozen' : 'latest';
  const fetchSnap = () => api('/api/priorities', token || !ctx.params.week ? {} : { week: ctx.params.week }, { signal: ctx.signal });
  let d;
  try {
    d = await fetchSnap();
  } catch (e) {
    if (e && e.status === 404) {
      ctx.mount(h('div', { class: 'page pri-page' }, h('h1', null, 'Priorities'), h('p', { 'data-testid': 'pri-empty' }, e.message)));
      return;
    }
    throw e;
  }
  let views = [];
  try { views = (await api('/api/priorities/views', {}, { signal: ctx.signal })).views || []; } catch (e) { if (e && e.name === 'AbortError') throw e; views = []; }
  let notice = flash.take();
  if (!token) {                                                                    // remembered view: only when the URL names none
    const last = lastView.get();
    if (last && views.some((v) => v.token === last)) { token = last; navigate(viewUrl(last, mode === 'frozen' ? { data: 'frozen' } : {}), { replace: true, render: false }); } else if (last) lastView.set('');
  }
  let act = null;
  if (token) {
    try { act = await api('/api/priorities/views/' + encodeURIComponent(token), {}, { signal: ctx.signal }); } catch (e) {
      if (e && e.status === 404) { lastView.set(''); notice = 'That saved view was deleted or does not exist, so all concepts are shown.'; navigate(href('/priorities', {}), { replace: true, render: false }); token = ''; } else throw e;
    }
  }
  const ref = {};
  const vm = { views, active: act, mode, notice };
  const reload = async () => {                                                     // after a manual concept is added / edited / done / deleted: fetch again, keep the view, search, sort and Show done
    const nd = await fetchSnap();
    ctx.mount(render(act ? effective(nd, act, mode) : nd, null, { reload, ref, carry: ref.state, vm }));
  };
  ctx.mount(render(act ? effective(d, act, mode) : d, null, { reload, ref, vm }));
  if (act) document.title = `${act.view.name} · Priorities · Lonvera`;
}

export async function prioritiesViewPage(ctx) {
  showControls(false);
  let d;
  try {
    d = await api('/api/priorities/views/' + encodeURIComponent(ctx.match[1]), {}, { signal: ctx.signal });
  } catch (e) {
    if (e && e.status === 404) {
      ctx.mount(h('div', { class: 'page pri-page' }, h('h1', null, 'Priorities'), h('p', { 'data-testid': 'pri-empty' }, 'This view was deleted (or the link is wrong): it no longer exists. '), h('a', { href: href('/priorities', {}) }, 'Open full priorities')));
      return;
    }
    throw e;
  }
  ctx.mount(render(d, d.view));
  document.title = `${d.view.name} \u00b7 Priorities \u00b7 Lonvera`;
}
