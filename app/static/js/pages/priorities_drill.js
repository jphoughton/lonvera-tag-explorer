// Click-in drill-downs for the /priorities row panel. Every block of the panel (then vs now tiles, timeline steps, hooks, former winning ads, Performance / Testing / Live tiles) gets a "See more"
// button; one shared region sits under each group of buttons and is filled on first open from GET /api/priorities/lines/<line_id>/insights (read-only, Postgres only).
// Class names and test ids start with `pri-` (never ad-, ads-, sponsor, banner, promo).
import { h, fmt, isNum, href } from '../util.js';
import { api } from '../api.js';

const WINDOWS = [['7', 'Last 7 days'], ['30', 'Last 30 days'], ['90', 'Last 90 days'], ['all', 'All time']];
const roasX = (v) => (isNum(v) ? fmt.roas(v) + 'x' : '–');
const WEEKS_SHOWN = 12;
let seq = 0;

// the data table of ads: name linked to /ad/<id>, spend, ROAS, purchases, launch date, hook
function adsTable(ads) {
  if (!ads.length) return h('p', { class: 'pri-hookn' }, 'No ads with spend in this window.');
  return h('table', { class: 'pri-dt', 'data-testid': 'pri-drill-ads' }, h('thead', null, h('tr', null, ['Ad', 'Spend', 'ROAS', 'Purchases', 'Launched', 'Hook'].map((t) => h('th', { scope: 'col' }, t)))),
    h('tbody', null, ads.map((a) => h('tr', null,
      h('td', { 'data-label': 'Ad' }, h('a', { href: href('/ad/' + encodeURIComponent(a.ad_id), {}) }, a.name)),
      h('td', { 'data-label': 'Spend' }, fmt.money(a.spend)), h('td', { 'data-label': 'ROAS' }, roasX(a.roas)), h('td', { 'data-label': 'Purchases' }, fmt.int(a.purchases)),
      h('td', { 'data-label': 'Launched' }, a.launch_date || '–'), h('td', { 'data-label': 'Hook', class: 'pri-dt-hook' }, a.hook || '–')))));
}

function hooksTable(hooks) {
  if (!hooks.length) return h('p', { class: 'pri-hookn' }, 'No hooks found for the ads on this line yet.');
  return h('table', { class: 'pri-dt', 'data-testid': 'pri-drill-hooks' }, h('thead', null, h('tr', null, ['Hook', 'Spend', 'ROAS'].map((t) => h('th', { scope: 'col' }, t)))),
    h('tbody', null, hooks.map((k) => h('tr', null, h('td', { 'data-label': 'Hook' }, h('a', { href: href('/ad/' + encodeURIComponent(k.ad_id), {}), title: k.ad }, k.hook)),
      h('td', { 'data-label': 'Spend' }, fmt.money(k.spend)), h('td', { 'data-label': 'ROAS' }, roasX(k.roas))))));
}

// weekly trend, newest last; the last 12 weeks, the rest behind a toggle
function trendTable(weekly) {
  if (!weekly.length) return h('p', { class: 'pri-hookn' }, 'No weekly data in this window.');
  const rows = weekly.map((w, i) => h('tr', { hidden: i < weekly.length - WEEKS_SHOWN }, h('td', { 'data-label': 'Week of' }, w.week), h('td', { 'data-label': 'Spend' }, fmt.money(w.spend)),
    h('td', { 'data-label': 'ROAS' }, roasX(w.roas)), h('td', { 'data-label': 'Purchases' }, fmt.int(w.purchases))));
  const out = [h('table', { class: 'pri-dt', 'data-testid': 'pri-drill-trend' }, h('thead', null, h('tr', null, ['Week of', 'Spend', 'ROAS', 'Purchases'].map((t) => h('th', { scope: 'col' }, t)))), h('tbody', null, rows))];
  if (weekly.length > WEEKS_SHOWN) {
    const btn = h('button', { type: 'button', class: 'pri-dbtn', 'aria-expanded': 'false' }, `Show all ${weekly.length} weeks`);
    btn.addEventListener('click', () => {
      const open = btn.getAttribute('aria-expanded') !== 'true';
      btn.setAttribute('aria-expanded', String(open));
      rows.forEach((tr, i) => { tr.hidden = !open && i < weekly.length - WEEKS_SHOWN; });
      btn.textContent = open ? 'Show fewer weeks' : `Show all ${weekly.length} weeks`;
    });
    out.push(btn);
  }
  return out;
}

function body(d, spec) {
  const t = d.total || {};
  const sum = h('p', { class: 'pri-hookn', 'data-testid': 'pri-drill-total' }, `${d.ads_total} ${d.ads_total === 1 ? 'ad' : 'ads'} with spend on this line in this window: ${fmt.money(t.spend)} spent, ${roasX(t.roas)} return, ${fmt.int(t.purchases)} purchases. Meta numbers.`);
  if (spec.ad_id != null) {
    const a = d.ads[0];
    return [h('p', null, h('a', { href: href('/ad/' + encodeURIComponent(a.ad_id), {}) }, a.name), ` launched ${a.launch_date || 'date unknown'}: ${fmt.money(a.spend)} spent, ${roasX(a.roas)} return, ${fmt.int(a.purchases)} purchases.`),
      a.hook ? h('p', null, h('span', { class: 'pri-label' }, 'Hook '), h('q', { class: 'pri-quote' }, a.hook)) : h('p', { class: 'pri-hookn' }, 'No hook found for this ad yet.'),
      h('h6', { class: 'pri-label' }, 'Weekly trend'), trendTable(d.weekly)];
  }
  const parts = [sum];
  if (spec.block === 'hooks') parts.push(h('h6', { class: 'pri-label' }, 'Hooks, biggest spend first'), hooksTable(d.hooks));
  parts.push(h('h6', { class: 'pri-label' }, spec.block === 'testing' ? 'Newest ads on this line (up to 10)' : 'Top ads on this line (up to 10)'), adsTable(d.ads));
  parts.push(h('h6', { class: 'pri-label' }, 'Weekly trend'), trendTable(d.weekly));
  return parts;
}

// One shared region for a group of buttons. `region.open(btn, spec)` loads lazily (answers cached per block + window + ad); opening another button of the group switches the content.
export function drillRegion(lineId) {
  const id = 'pri-drill-' + (++seq);
  const title = h('h5', { class: 'pri-drill-h', id: id + '-t' });
  const winRow = h('div', { class: 'pri-drill-win', role: 'group', 'aria-label': 'Time window' });
  const content = h('div', { class: 'pri-drill-body', 'aria-live': 'polite' });
  const close = h('button', { type: 'button', class: 'pri-dbtn', 'data-testid': 'pri-drill-close' }, 'Close');
  const el = h('section', { class: 'pri-drill', id, hidden: true, 'data-testid': 'pri-drill', 'aria-labelledby': id + '-t' }, h('div', { class: 'pri-drill-top' }, title, close), winRow, content);
  const cache = new Map(); const btns = new Set();
  let cur = null; let curBtn = null; let token = 0;
  const sync = () => btns.forEach((b) => b.setAttribute('aria-expanded', String(!el.hidden && b === curBtn)));
  async function load() {
    const my = ++token; const spec = cur;
    title.textContent = spec.title;
    const hadFocus = winRow.contains(document.activeElement);                       // the window buttons are rebuilt on every load: keep the keyboard where it was
    winRow.replaceChildren(...(spec.ad_id != null ? [] : WINDOWS.map(([k, label]) => h('button', { type: 'button', class: 'pri-win' + (k === spec.window ? ' pri-win-on' : ''), 'aria-pressed': String(k === spec.window), 'data-window': k, onclick: () => { cur = { ...spec, window: k }; load(); } }, label))));
    if (hadFocus) { const nb = winRow.querySelector(`[data-window="${spec.window}"]`); if (nb) nb.focus(); }
    const key = [spec.block, spec.window, spec.ad_id].join('|');
    if (!cache.has(key)) {
      content.replaceChildren(h('p', { class: 'pri-hookn' }, 'Loading...'));
      try { cache.set(key, await api(`/api/priorities/lines/${lineId}/insights`, { block: spec.block, window: spec.window, ad_id: spec.ad_id })); } catch (e) {
        if (my === token) content.replaceChildren(h('p', { class: 'pri-hookn', role: 'alert' }, e.status === 404 ? 'No data for this line in the database any more (the numbers on this row are a saved copy).' : 'Could not load this. Try again in a moment.'));
        return;
      }
    }
    if (my === token) content.replaceChildren(...[body(cache.get(key), spec)].flat(Infinity));
  }
  function hide() { el.hidden = true; sync(); if (curBtn) curBtn.focus(); }
  close.addEventListener('click', hide);
  el.addEventListener('keydown', (e) => { if (e.key === 'Escape') { e.stopPropagation(); hide(); } });
  el.open = (btn, spec) => {
    if (!el.hidden && curBtn === btn) { hide(); return; }
    cur = { window: 'all', ...spec }; curBtn = btn; el.hidden = false; sync(); load();
  };
  el.register = (b) => btns.add(b);
  return el;
}

// a "See more" button for one block / tile / step / hook / winning ad; click opens `region` on this spec
export function drillBtn(region, spec, what) {
  const b = h('button', { type: 'button', class: 'pri-dbtn pri-seemore', 'aria-expanded': 'false', 'aria-controls': region.id, 'aria-label': `See more: ${what}`, 'data-testid': 'pri-seemore' }, 'See more');
  b.addEventListener('click', (e) => { e.stopPropagation(); region.open(b, { title: what, ...spec }); });
  region.register(b);
  return b;
}

// null when the row has no line in the database (manual concepts, the unmapped row, competitor drafts)
export const canDrill = (r) => r.line_id != null && !r.manual;
