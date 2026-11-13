// The Plan view: vendors as rows, four weeks as columns, one cell per vendor and week with that vendor's cards and a load bar. Drag a card to another cell to reassign vendor and week; the capacity number in the row header is editable.
// Test ids: prod-plan-*. Class names start with prod- (never ad-, ads-, sponsor, banner, promo: ad blockers hide those).
import { h } from '../util.js';
import { statusOf, weekLabel, addDays, mondayOf, today } from './production_common.js';
import { cardEl } from './production_board.js';
import { enableDrag } from './production_dnd.js';

export const planWeeks = (start) => { const s = mondayOf(start || today()); return [0, 7, 14, 21].map((n) => addDays(s, n)); };

// plan({cards, vendors, capacity, start, ops, onReassign, onCapacity}) -> {el}
// capacity = {vendor: per_week}; cards = every card to place (launched ones show but do not count toward load)
export function plan({ cards, vendors, capacity, start, ops, onReassign, onCapacity, onShift }) {
  const weeks = planWeeks(start);
  const open = (v, w) => cards.filter((c) => c.vendor === v && c.for_week === w && statusOf(c) !== 'launched' && c.stage !== 'dropped').length;
  const live = h('p', { class: 'prod-sr', 'aria-live': 'assertive', role: 'status', 'data-testid': 'prod-plan-live' });
  const nav = h('div', { class: 'prod-plan-nav' },
    h('button', { type: 'button', class: 'btn', 'data-testid': 'prod-plan-prev', onclick: () => onShift(addDays(weeks[0], -7)) }, 'Earlier'),
    h('span', { class: 'prod-plan-range', 'data-testid': 'prod-plan-range' }, `${weekLabel(weeks[0])} to ${weekLabel(weeks[3]).replace('Week of ', '')}`),
    h('button', { type: 'button', class: 'btn', 'data-testid': 'prod-plan-next', onclick: () => onShift(addDays(weeks[0], 7)) }, 'Later'),
    h('button', { type: 'button', class: 'btn', 'data-testid': 'prod-plan-today', onclick: () => onShift('') }, 'This week'));
  const head = h('div', { class: 'prod-plan-row prod-plan-headrow' }, h('div', { class: 'prod-plan-vh', role: 'columnheader' }, 'Vendor'), weeks.map((w) => h('div', { class: 'prod-plan-wh', role: 'columnheader', 'data-testid': 'prod-plan-weekhead', 'data-week': w }, weekLabel(w))));
  const rows = vendors.map((v) => {
    const per = capacity[v];
    const capIn = h('input', { type: 'number', min: '0', max: '200', step: '1', class: 'prod-plan-capin', value: per == null ? '' : per, 'aria-label': `Weekly capacity for ${v}`, 'data-testid': 'prod-plan-capacity', 'data-vendor': v,
      onchange: (e) => { const n = Number(e.target.value); if (!Number.isInteger(n) || n < 0) { e.target.value = per == null ? '' : per; return; } onCapacity(v, n); } });
    return h('div', { class: 'prod-plan-row', role: 'row', 'data-testid': 'prod-plan-row', 'data-vendor': v },
      h('div', { class: 'prod-plan-vh', role: 'rowheader' }, h('strong', null, v), h('label', { class: 'prod-plan-cap' }, h('span', { class: 'pri-sub' }, 'Cards per week'), capIn)),
      weeks.map((w) => {
        const n = open(v, w); const over = per != null && n > per; const full = per != null && n === per;
        const mine = cards.filter((c) => c.vendor === v && c.for_week === w);
        const pct = per ? Math.min(100, Math.round(100 * n / per)) : 0;
        return h('div', { class: 'prod-plan-cell' + (over ? ' prod-plan-over' : full ? ' prod-plan-full' : ''), role: 'cell', 'data-testid': 'prod-plan-cell', 'data-vendor': v, 'data-week': w, 'data-open': String(n), 'data-over': over ? '1' : '0' },
          h('div', { class: 'prod-plan-load' }, h('span', { class: 'prod-loadtrack', 'aria-hidden': 'true' }, h('span', { class: 'prod-loadfill', style: { width: pct + '%' } })), h('span', { class: 'prod-loadnum', 'data-testid': 'prod-plan-load' }, per != null ? `${n} of ${per}` : String(n)), over ? h('span', { class: 'prod-loadwarn' }, 'Over capacity') : null),
          h('div', { class: 'prod-plan-list', 'data-testid': 'prod-plan-list', 'data-vendor': v, 'data-week': w, 'data-label': `${v}, ${weekLabel(w)}`, 'aria-label': `${v}, ${weekLabel(w)}`, role: 'list' }, mine.map((c) => cardEl(c, ops, { compact: true })), mine.length ? null : h('p', { class: 'prod-board-empty' }, 'Open')));
      }));
  });
  const scroll = h('div', { class: 'prod-plan-scroll', 'data-testid': 'prod-plan-scroll' }, h('div', { class: 'prod-plan-grid', role: 'table', 'aria-label': 'Four week plan' }, head, rows));
  const el = h('div', { class: 'prod-plan', 'data-testid': 'prod-plan' }, nav, live, scroll);
  enableDrag({ root: el, cardSel: '.prod-board-card', gripSel: '.prod-grip', listSel: '.prod-plan-list', laneOf: () => '', live, onDrop: ({ card, list }) => onReassign(card, list.dataset.vendor, list.dataset.week) });
  return { el, weeks };
}
