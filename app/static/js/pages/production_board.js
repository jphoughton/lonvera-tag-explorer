// The Production board (kanban): six columns, cards, drag and drop, "Move to" menu, swimlanes by vendor, and the weekly load bars. Pure drawing plus events: the page (priorities_production.js) owns the data and saves moves.
// Test ids: prod-board-*, prod-card-*. Class names start with prod- (never ad-, ads-, sponsor, banner, promo: ad blockers hide those).
import { h, fmt, isNum } from '../util.js';
import { STATUSES, STATUS_LABEL, statusOf, md, ticketTitle, adCount, mediaOf, conceptName, factsEl, productName, weekLabel, formatLabel, dueInfo, link, short, nextActions } from './production_common.js';
import { enableDrag, gripEl } from './production_dnd.js';

const LAUNCHED_DAYS = 14;

export function resultShort(r) {
  const ads = (r.ads || []).filter((a) => a.launch_date);
  if (!ads.length) return '';
  const spend = ads.reduce((s, a) => s + (isNum(a.spend) ? a.spend : 0), 0);
  const val = ads.reduce((s, a) => s + (isNum(a.spend) && isNum(a.roas) ? a.spend * a.roas : 0), 0);
  return `${fmt.money(spend)} spent` + (spend > 0 ? `, ${fmt.roas(val / spend)} return` : '');
}

// The "..." menu is position: fixed so no scroll area can clip it: it opens below its button, flips above when there is no room, and is kept inside the viewport.
// Only one is open at a time; scrolling, resizing, Escape or a click elsewhere closes it.
let openMenu = null;
function closeMenu(except) { for (const d of document.querySelectorAll('.prod-card-more[open]')) if (d !== except) d.open = false; if (!except) openMenu = null; }
function placeMenu(d) {
  closeMenu(d);
  openMenu = d;
  const m = d.querySelector('.prod-card-menu'); const b = d.querySelector('summary').getBoundingClientRect();
  const w = Math.min(220, window.innerWidth - 16); m.style.width = w + 'px';
  const hgt = m.getBoundingClientRect().height; const vh = window.innerHeight;
  let top = b.bottom + 4; if (top + hgt > vh - 8) top = Math.max(8, b.top - 4 - hgt);
  m.style.top = top + 'px'; m.style.left = Math.max(8, Math.min(b.right - w, window.innerWidth - w - 8)) + 'px';
  d.dataset.placed = '1';
}
if (typeof document !== 'undefined' && !window.__prodMenuHooks) {
  window.__prodMenuHooks = true;
  document.addEventListener('click', (e) => { const d = e.target.closest && e.target.closest('.prod-card-more'); closeMenu(d || undefined); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeMenu(); });
  window.addEventListener('resize', closeMenu);
  window.addEventListener('scroll', closeMenu, true);
}

const chip = (cls, text, attrs) => h('span', { class: 'prod-chip ' + cls, ...(attrs || {}) }, text);

// One card. `ops` = { open(card), run(card, action), moveTo(card, status) }.
export function cardEl(r, ops, { compact = false } = {}) {
  const status = statusOf(r);
  const due = dueInfo(r);
  const title = ticketTitle(r);
  const acts = nextActions(r);
  const primary = acts[0];
  const hint = !r.creative_url && status === 'in_production' ? 'Needs creative' : '';
  const moveSel = h('select', { class: 'prod-card-moveto', 'data-testid': 'prod-card-moveto', 'aria-label': `Move ${title} to`, onchange: (e) => { const v = e.target.value; e.target.value = ''; if (v) ops.moveTo(r, v); } },
    h('option', { value: '' }, 'Move to…'), STATUSES.filter(([k]) => k !== status && k !== 'launched').map(([k, l]) => h('option', { value: k, 'data-testid': 'prod-card-moveto-' + k }, l)));
  // the "..." menu: everything that is not the one main button. A details element so it works with a keyboard and on a phone without any script; it opens inline under the card text.
  const menuBtn = (testid, label, fn, extra) => h('button', { type: 'button', class: 'btn prod-small prod-menu-item', 'data-testid': testid, onclick: (e) => { const d = e.target.closest('details'); if (d) d.open = false; fn(); }, ...(extra || {}) }, label);
  const more = h('details', { class: 'prod-card-more', 'data-testid': 'prod-card-more', ontoggle: (e) => { if (e.target.open) placeMenu(e.target); else { delete e.target.dataset.placed; if (openMenu === e.target) openMenu = null; } } },
    h('summary', { class: 'prod-card-more-btn', 'aria-label': `More actions for ${title}`, title: 'More actions', 'data-testid': 'prod-card-more-btn' }, h('span', { class: 'prod-ellipsis', 'aria-hidden': 'true' })),
    h('div', { class: 'prod-card-menu', 'data-testid': 'prod-card-menu' },
      status === 'launched' ? null : moveSel,
      menuBtn('prod-card-open', 'Open card', () => ops.open(r)),
      menuBtn('prod-card-open-comments', 'Comments', () => ops.open(r, 'comments')),
      acts.slice(1).map((a) => menuBtn('prod-card-alt', a.label, () => ops.run(r, a), { 'data-action': a.key }))));
  const res = status === 'launched' ? resultShort(r) : '';
  const el = h('article', { class: 'prod-board-card' + (r.overdue ? ' prod-overdue' : '') + (r.priority === 'high' ? ' prod-high' : ''), 'data-testid': 'prod-board-card', 'data-id': r.id, 'data-status': status, 'data-title': title, tabindex: '0',
    role: 'group', 'aria-roledescription': 'draggable card', 'aria-label': `${title}, ${r.vendor}, ${STATUS_LABEL[status] || status}`,
    onclick: (e) => { if (!e.target.closest('a, button, select, input, textarea, summary, details')) ops.open(r); } },
    h('div', { class: 'prod-card-top' },
      h('span', { class: 'prod-dot prod-dot-' + (r.product || 'none'), title: productName(r.product), 'data-testid': 'prod-card-product', 'data-product': r.product || '' }),
      h('button', { type: 'button', class: 'prod-card-title', 'data-testid': 'prod-card-title', 'data-dragok': '', onclick: () => ops.open(r) }, conceptName(r)),
      r.priority === 'high' ? chip('prod-chip-high', 'High', { 'data-testid': 'prod-card-priority', title: 'High priority' }) : null,
      gripEl()),
    compact ? null : factsEl(r, 'prod-facts prod-card-facts', { Vendor: 'prod-card-vendor' }),
    h('div', { class: 'prod-card-chips' },
      mediaOf(r) ? chip('prod-chip-media prod-media-' + mediaOf(r).toLowerCase().replace(/\W+/g, '-'), mediaOf(r), { 'data-testid': 'prod-card-media' }) : null,
      r.format_code ? chip('prod-chip-fmt', r.format_code, { 'data-testid': 'prod-card-format', title: formatLabel({ code: r.format_code, name: r.format_name || '', length: r.format_length || '' }) }) : null,
      due ? chip('prod-chip-due' + (due.tone ? ' prod-due-' + due.tone : ''), due.text, { 'data-testid': 'prod-card-due', 'data-tone': due.tone }) : null),
    h('div', { class: 'prod-card-meta' },
      r.comment_count ? h('button', { type: 'button', class: 'prod-card-comments', 'data-testid': 'prod-card-comments', 'aria-label': `${r.comment_count} comments, open`, onclick: () => ops.open(r, 'comments') },
        h('span', { class: 'prod-ic-comment', 'aria-hidden': 'true' }), ' ', String(r.comment_count)) : null,
      hint ? h('span', { class: 'prod-card-needs', 'data-testid': 'prod-card-needs' }, hint) : null,
      r.brief_url ? link(r.brief_url, 'Brief', { 'data-testid': 'prod-card-brief', class: 'prod-card-link prod-ext', title: r.brief_url }) : null,
      r.creative_url ? link(r.creative_url, 'Creative', { 'data-testid': 'prod-card-creative', class: 'prod-card-link prod-ext', title: r.creative_url }) : null,
      res ? h('span', { class: 'prod-card-result', 'data-testid': 'prod-card-result' }, res) : null),
    compact ? null : h('div', { class: 'prod-card-foot' },
      primary ? h('button', { type: 'button', class: 'btn prod-card-action' + (primary.primary ? ' primary' : ''), 'data-testid': 'prod-card-action', 'data-action': primary.key, onclick: () => ops.run(r, primary) }, primary.label) : null,
      more));
  return el;
}

// load bars: open cards for the week against the vendor's weekly capacity (capacity rows come from the board payload, one per vendor and week)
export function loadBars(capacity, vendors, week, cards) {
  const rows = (vendors || []).map((v) => {
    const c = (capacity || []).filter((x) => x.vendor === v && (!week || x.week === week));
    const per = c.length ? c[0].per_week : null;
    const open = c.length ? c.reduce((s, x) => s + (x.open || 0), 0) : cards.filter((x) => x.vendor === v && (!week || x.for_week === week) && statusOf(x) !== 'launched').length;
    return { vendor: v, per, open };
  }).filter((x) => x.open > 0);
  if (!rows.length) return null;
  return h('div', { class: 'prod-board-load', 'data-testid': 'prod-board-load', role: 'list', 'aria-label': 'Weekly load by vendor' },
    rows.map((x) => { const pct = x.per ? Math.min(100, Math.round(100 * x.open / x.per)) : 0; const over = x.per != null && x.open >= x.per;
      return h('div', { class: 'prod-loadrow' + (over ? ' prod-loadfull' : ''), role: 'listitem', 'data-testid': 'prod-board-load-row', 'data-vendor': x.vendor, 'data-over': over ? '1' : '0' },
        h('span', { class: 'prod-loadname' }, x.vendor), h('span', { class: 'prod-loadtrack', 'aria-hidden': 'true' }, h('span', { class: 'prod-loadfill', style: { width: pct + '%' } })),
        h('span', { class: 'prod-loadnum', 'data-testid': 'prod-board-load-text' }, x.per != null ? `${x.open} of ${x.per}` : String(x.open)), over ? h('span', { class: 'prod-loadwarn', 'data-testid': 'prod-board-load-warn' }, x.open > x.per ? 'Over capacity' : 'Full') : null);
    }));
}

function colHead(label, cards, note) {
  const overdue = cards.filter((c) => c.overdue).length;
  return h('header', { class: 'prod-col-head' }, h('h3', null, label), h('span', { class: 'prod-col-count', 'data-testid': 'prod-board-count' }, String(cards.length)),
    overdue ? h('span', { class: 'prod-col-note', 'data-testid': 'prod-board-overdue' }, `${overdue} overdue`) : null, note ? h('span', { class: 'prod-col-note' }, note) : null);
}

// board({columns, dropped, st, vendors, capacity, week, ops, onDrop}) -> {el}
export function board({ columns, dropped, st, vendors, capacity, ops, onDrop, allCards }) {
  const filt = (r) => (!st.overdue || r.overdue) && (!st.q || [r.concept_name, r.pain_point, r.cluster, r.icp, r.vendor, r.note, r.vendor_note].filter(Boolean).join(' ').toLowerCase().includes(st.q.toLowerCase()));
  const byStatus = new Map(columns.map((c) => [c.status, c.cards || []]));
  let defs = STATUSES.map(([k, l]) => ({ key: k, label: l, cards: (byStatus.get(k) || []).filter(filt), hidden: (columns.find((c) => c.status === k) || {}).hidden || 0 }));
  defs = defs.map((d) => (d.key === 'launched' && !st.closed ? { ...d, note: `Last ${LAUNCHED_DAYS} days` + (d.hidden ? `, ${d.hidden} older hidden` : '') } : d));
  if (st.closed) defs.push({ key: 'dropped', label: 'Dropped', cards: (dropped || []).filter(filt), lane: true });
  if (st.stage) { const k = st.stage === 'creative_in' ? 'in_review' : st.stage; defs = defs.filter((d) => d.key === k); }
  const live = h('p', { class: 'prod-sr', 'aria-live': 'assertive', role: 'status', 'data-testid': 'prod-board-live' });
  const lanes = st.group === 'vendor' ? [...new Set([...vendors, ...defs.flatMap((d) => d.cards.map((c) => c.vendor))])].filter((v) => defs.some((d) => d.cards.some((c) => c.vendor === v))) : [null];

  const cellFor = (d, lane) => {
    const cards = lane ? d.cards.filter((c) => c.vendor === lane) : d.cards;
    const dropOk = d.key !== 'dropped';
    const list = h('div', { class: 'prod-board-list' + (dropOk ? '' : ' prod-nodrop'), 'data-testid': 'prod-board-list', 'data-status': d.key, 'data-lane': lane || '', 'data-label': d.label, 'aria-label': d.label, role: 'list' },
      cards.map((c) => cardEl(c, ops)), cards.length ? null : h('p', { class: 'prod-board-empty', 'data-testid': 'prod-board-empty' }, d.key === 'launched' || d.key === 'dropped' ? 'Nothing here yet' : 'Drop a card here'));
    return { cards, list };
  };

  const cols = (lane) => defs.map((d) => {
    const { cards, list } = cellFor(d, lane);
    return h('section', { class: 'prod-board-col prod-col-' + d.key, 'data-testid': 'prod-board-column', 'data-status': d.key, 'data-droptarget': '', 'aria-label': d.label }, colHead(d.label, cards, d.note), list);
  });

  const scroll = h('div', { class: 'prod-board-scroll', 'data-testid': 'prod-board-scroll' },
    lanes[0] === null
      ? h('div', { class: 'prod-board-cols', style: { '--cols': defs.length } }, cols(null))
      : lanes.map((v) => { const mine = defs.flatMap((d) => d.cards).filter((c) => c.vendor === v); const bar = loadBars(capacity, [v], (st.week && !st.week.includes(',') ? st.week : ''), allCards || mine);
        return h('div', { class: 'prod-lane', 'data-testid': 'prod-board-lane', 'data-vendor': v }, h('div', { class: 'prod-lane-head' }, h('strong', null, v), h('span', { class: 'prod-lane-n' }, `${mine.length} ${mine.length === 1 ? 'card' : 'cards'}`), bar),
          h('div', { class: 'prod-board-cols', style: { '--cols': defs.length } }, cols(v))); }));
  if (lanes.length === 0) scroll.append(h('p', { 'data-testid': 'prod-board-nolanes' }, 'No cards match.'));

  // Phone: chips above the board that jump to a column; the chip of the column in view is highlighted (aria-current). Hidden on a wide screen by CSS.
  const chips = defs.map((d) => h('button', { type: 'button', class: 'prod-jump-chip', 'data-testid': 'prod-board-jump-' + d.key, 'data-status': d.key,
    onclick: () => { const col = scroll.querySelector(`.prod-board-col[data-status="${d.key}"]`); if (col) { scroll.scrollTo({ left: Math.max(0, col.offsetLeft - 12), behavior: 'smooth' }); mark(d.key); } } },
    d.label + ' ', h('span', { class: 'prod-jump-n' }, String(d.cards.length))));
  const jump = h('nav', { class: 'prod-jump', 'data-testid': 'prod-board-jump', 'aria-label': 'Jump to a column' }, chips);
  function mark(key) {
    for (const c of chips) { const on = c.dataset.status === key; if (on) c.setAttribute('aria-current', 'true'); else c.removeAttribute('aria-current'); if (on && jump.scrollWidth > jump.clientWidth) { const x = c.offsetLeft - 12; if (Math.abs(jump.scrollLeft - x) > 40) jump.scrollTo({ left: x }); } }
  }
  let tick = 0;
  scroll.addEventListener('scroll', () => {
    cancelAnimationFrame(tick);
    tick = requestAnimationFrame(() => {
      const cols = [...scroll.querySelectorAll('.prod-board-col')].filter((c, i, a) => a.findIndex((o) => o.dataset.status === c.dataset.status) === i);
      let best = null, bd = 1e9;
      for (const c of cols) { const dd = Math.abs(c.offsetLeft - 12 - scroll.scrollLeft); if (dd < bd) { bd = dd; best = c; } }
      if (best) mark(best.dataset.status);
    });
  }, { passive: true });
  if (defs.length) mark(defs[0].key);

  const el = h('div', { class: 'prod-board', 'data-testid': 'prod-board' }, live, jump, scroll);
  enableDrag({ root: el, cardSel: '.prod-board-card', gripSel: '.prod-grip', listSel: '.prod-board-list:not(.prod-nodrop)', laneOf: (n) => (n.closest('[data-lane]') || n).dataset.lane || '', onDrop, live });
  return { el };
}
