// The Plan view: vendors as rows, four weeks as columns, one cell per vendor and week with that vendor's cards and a load bar. Drag a card to another cell to reassign vendor and week; the capacity number in the row header is editable.
// Test ids: prod-plan-*. Class names start with prod- (never ad-, ads-, sponsor, banner, promo: ad blockers hide those).
import { h } from '../util.js';
import { statusOf, weekLabel, addDays, mondayOf, today } from './production_common.js';
import { cardEl } from './production_board.js';
import { enableDrag } from './production_dnd.js';

export const planWeeks = (start) => { const s = mondayOf(start || today()); return [0, 7, 14, 21].map((n) => addDays(s, n)); };

// plan({cards, vendors, capacity, start, ops, onReassign, onCapacity}) -> {el}
// capacity = {vendor: per_week}; cards = every card to place (launched ones show but do not count toward load)
let idleOpen = false;                                  // the 'nothing planned' group stays open across redraws
// Vendors folded up to one line (so a vendor with 75 cards does not push the rest of the plan off screen). Kept in this browser only.
const FOLD_KEY = 'prodPlanFolded';
const folded = new Set((() => { try { return JSON.parse(localStorage.getItem(FOLD_KEY) || '[]'); } catch (e) { return []; } })());
const saveFolded = () => { try { localStorage.setItem(FOLD_KEY, JSON.stringify([...folded])); } catch (e) { /* storage blocked: folding still works until the page is left */ } };
export function plan({ cards, vendors, capacity, start, ops, onReassign, onCapacity, onShift, saved }) {
  const weeks = planWeeks(start);
  const open = (v, w) => cards.filter((c) => c.vendor === v && c.for_week === w && statusOf(c) !== 'launched' && c.stage !== 'dropped').length;
  const setAll = (on) => { for (const v of vendors) { if (on) folded.add(v); else folded.delete(v); } saveFolded(); for (const row of el.querySelectorAll('.prod-plan-row[data-vendor]')) { const v = row.dataset.vendor; row.classList.toggle('prod-plan-folded', on); const b = row.querySelector('.prod-plan-fold'); if (b) { b.setAttribute('aria-expanded', String(!on)); b.setAttribute('aria-label', `${on ? 'Expand' : 'Collapse'} ${v}`); } } };
  const live = h('p', { class: 'prod-sr', 'aria-live': 'assertive', role: 'status', 'data-testid': 'prod-plan-live' });
  const nav = h('div', { class: 'prod-plan-nav' },
    h('button', { type: 'button', class: 'btn', 'data-testid': 'prod-plan-prev', onclick: () => onShift(addDays(weeks[0], -7)) }, 'Earlier'),
    h('span', { class: 'prod-plan-range', 'data-testid': 'prod-plan-range' }, `${weekLabel(weeks[0])} to ${weekLabel(weeks[3]).replace('Week of ', '')}`),
    h('button', { type: 'button', class: 'btn', 'data-testid': 'prod-plan-next', onclick: () => onShift(addDays(weeks[0], 7)) }, 'Later'),
    h('button', { type: 'button', class: 'btn', 'data-testid': 'prod-plan-today', onclick: () => onShift('') }, 'This week'),
    h('button', { type: 'button', class: 'btn', 'data-testid': 'prod-plan-fold-all', onclick: () => setAll(true) }, 'Roll up all vendors'),
    h('button', { type: 'button', class: 'btn', 'data-testid': 'prod-plan-unfold-all', onclick: () => setAll(false) }, 'Show all vendors'));
  const head = h('div', { class: 'prod-plan-row prod-plan-headrow' }, h('div', { class: 'prod-plan-vh', role: 'columnheader' }, 'Vendor'), weeks.map((w) => h('div', { class: 'prod-plan-wh', role: 'columnheader', 'data-testid': 'prod-plan-weekhead', 'data-week': w }, weekLabel(w))));
  const rows = vendors.map((v) => {
    const per = capacity[v];
    const capIn = h('input', { type: 'number', min: '0', max: '200', step: '1', class: 'prod-plan-capin', value: per == null ? '' : per, 'aria-label': `Weekly capacity for ${v}`, 'data-testid': 'prod-plan-capacity', 'data-vendor': v,
      onchange: (e) => { const n = Number(e.target.value); if (!Number.isInteger(n) || n < 0) { e.target.value = per == null ? '' : per; return; } onCapacity(v, n); } });
    capIn.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); capIn.blur(); } });
    const cue = v === saved ? h('span', { class: 'prod-plan-saved', role: 'status', 'data-testid': 'prod-plan-capsaved' }, 'Saved') : null;
    if (cue) setTimeout(() => cue.remove(), 2500);
    const total = weeks.reduce((n, w) => n + open(v, w), 0);
    const fold = h('button', { type: 'button', class: 'prod-plan-fold', 'data-testid': 'prod-plan-collapse', 'data-vendor': v, 'aria-expanded': String(!folded.has(v)), 'aria-label': `${folded.has(v) ? 'Expand' : 'Collapse'} ${v}`, title: folded.has(v) ? 'Show this vendor\'s cards' : 'Roll this vendor up to one line',
      onclick: () => { const row = fold.closest('.prod-plan-row'); const on = !folded.has(v); if (on) folded.add(v); else folded.delete(v); saveFolded(); row.classList.toggle('prod-plan-folded', on); fold.setAttribute('aria-expanded', String(!on)); fold.setAttribute('aria-label', `${on ? 'Expand' : 'Collapse'} ${v}`); fold.title = on ? 'Show this vendor\'s cards' : 'Roll this vendor up to one line'; } });
    return h('div', { class: 'prod-plan-row' + (folded.has(v) ? ' prod-plan-folded' : ''), role: 'row', 'data-testid': 'prod-plan-row', 'data-vendor': v },
      h('div', { class: 'prod-plan-vh', role: 'rowheader' }, h('div', { class: 'prod-plan-vname' }, fold, h('strong', null, v), h('span', { class: 'pri-sub prod-plan-foldn', 'data-testid': 'prod-plan-foldn' }, `${total} open`)), h('label', { class: 'prod-plan-cap' }, h('span', { class: 'pri-sub' }, 'Cards per week'), h('span', { class: 'prod-plan-capwrap' }, capIn, cue))),
      weeks.map((w) => {
        const n = open(v, w); const over = per != null && n > per; const full = per != null && n === per;
        const mine = cards.filter((c) => c.vendor === v && c.for_week === w);
        const pct = per ? Math.min(100, Math.round(100 * n / per)) : 0;
        return h('div', { class: 'prod-plan-cell' + (over ? ' prod-plan-over' : full ? ' prod-plan-full' : ''), role: 'cell', 'data-testid': 'prod-plan-cell', 'data-vendor': v, 'data-week': w, 'data-open': String(n), 'data-over': over ? '1' : '0' },
          h('div', { class: 'prod-plan-load' }, h('span', { class: 'prod-loadtrack', 'aria-hidden': 'true' }, h('span', { class: 'prod-loadfill', style: { width: pct + '%' } })), h('span', { class: 'prod-loadnum', 'data-testid': 'prod-plan-load' }, per != null ? `${n} of ${per}` : String(n)), over ? h('span', { class: 'prod-loadwarn' }, 'Over capacity') : null),
          h('div', { class: 'prod-plan-list', 'data-testid': 'prod-plan-list', 'data-vendor': v, 'data-week': w, 'data-label': `${v}, ${weekLabel(w)}`, 'aria-label': `${v}, ${weekLabel(w)}`, role: 'list' }, mine.map((c) => cardEl(c, ops, { compact: true })), mine.length ? null : h('p', { class: 'prod-board-empty' }, 'Free')));
      }));
  });
  const planned = (v) => cards.some((c) => c.vendor === v && weeks.includes(c.for_week));
  const busy = rows.filter((_, i) => planned(vendors[i]));
  const idle = rows.filter((_, i) => !planned(vendors[i]));
  const idleBox = idle.length ? h('details', { class: 'prod-plan-idle', 'data-testid': 'prod-plan-idle', open: idleOpen, ontoggle: (e) => { idleOpen = e.target.open; } },
    h('summary', null, `${idle.length} ${idle.length === 1 ? 'vendor has' : 'vendors have'} nothing planned in these weeks`, h('span', { class: 'prod-plan-idle-hint' }, ' · set their capacity or drop a card on them')), idle) : null;
  const scroll = h('div', { class: 'prod-plan-scroll', 'data-testid': 'prod-plan-scroll' }, h('div', { class: 'prod-plan-grid', role: 'table', 'aria-label': 'Four week plan' }, head, busy, idleBox));
  const el = h('div', { class: 'prod-plan', 'data-testid': 'prod-plan' }, nav, live, scroll);
  enableDrag({ root: el, cardSel: '.prod-board-card', gripSel: '.prod-grip', listSel: '.prod-plan-list', laneOf: () => '', live, onDrop: ({ card, list }) => onReassign(card, list.dataset.vendor, list.dataset.week) });
  return { el, weeks };
}
