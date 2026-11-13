// The Production board (kanban): six columns, cards, drag and drop, "Move to" menu, swimlanes by vendor, and the weekly load bars. Pure drawing plus events: the page (priorities_production.js) owns the data and saves moves.
// Test ids: prod-board-*, prod-card-*. Class names start with prod- (never ad-, ads-, sponsor, banner, promo: ad blockers hide those).
import { h, fmt, isNum } from '../util.js';
import { STATUSES, STATUS_LABEL, statusOf, md, productName, weekLabel, formatLabel, dueInfo, link, short, nextActions } from './production_common.js';
import { enableDrag, gripEl } from './production_dnd.js';

const LAUNCHED_DAYS = 14;

export function resultShort(r) {
  const ads = (r.ads || []).filter((a) => a.launch_date);
  if (!ads.length) return '';
  const spend = ads.reduce((s, a) => s + (isNum(a.spend) ? a.spend : 0), 0);
  const val = ads.reduce((s, a) => s + (isNum(a.spend) && isNum(a.roas) ? a.spend * a.roas : 0), 0);
  return `${fmt.money(spend)} spent` + (spend > 0 ? `, ${fmt.roas(val / spend)} return` : '');
}

const chip = (cls, text, attrs) => h('span', { class: 'prod-chip ' + cls, ...(attrs || {}) }, text);

// One card. `ops` = { open(card), run(card, action), moveTo(card, status) }.
export function cardEl(r, ops, { compact = false } = {}) {
  const status = statusOf(r);
  const due = dueInfo(r);
  const title = r.pain_point || r.cluster || 'Concept';
  const acts = nextActions(r);
  const primary = acts[0];
  const hint = !r.creative_url && status === 'in_production' ? 'Needs creative' : '';
  const moveSel = h('select', { class: 'prod-card-moveto', 'data-testid': 'prod-card-moveto', 'aria-label': `Move ${title} to`, onchange: (e) => { const v = e.target.value; e.target.value = ''; if (v) ops.moveTo(r, v); } },
    h('option', { value: '' }, 'Move to…'), STATUSES.filter(([k]) => k !== status && k !== 'launched').map(([k, l]) => h('option', { value: k, 'data-testid': 'prod-card-moveto-' + k }, l)));
  const res = status === 'launched' ? resultShort(r) : '';
  const el = h('article', { class: 'prod-board-card' + (r.overdue ? ' prod-overdue' : '') + (r.priority === 'high' ? ' prod-high' : ''), 'data-testid': 'prod-board-card', 'data-id': r.id, 'data-status': status, 'data-title': title, tabindex: '0',
    role: 'group', 'aria-roledescription': 'draggable card', 'aria-label': `${title}, ${r.vendor}, ${STATUS_LABEL[status] || status}`,
    onclick: (e) => { if (!e.target.closest('a, button, select, input, textarea')) ops.open(r); } },
    h('div', { class: 'prod-card-top' },
      h('span', { class: 'prod-dot prod-dot-' + (r.product || 'none'), title: productName(r.product), 'data-testid': 'prod-card-product', 'data-product': r.product || '' }),
      h('button', { type: 'button', class: 'prod-card-title', 'data-testid': 'prod-card-title', 'data-dragok': '', onclick: () => ops.open(r) }, title),
      r.priority === 'high' ? chip('prod-chip-high', 'High', { 'data-testid': 'prod-card-priority', title: 'High priority' }) : null,
      gripEl()),
    compact ? null : h('p', { class: 'prod-card-sub' }, [r.cluster, r.icp].filter(Boolean).join(' / ')),
    h('div', { class: 'prod-card-chips' },
      chip('prod-chip-vendor', r.vendor, { 'data-testid': 'prod-card-vendor' }),
      r.format_code ? chip('prod-chip-fmt', r.format_code, { 'data-testid': 'prod-card-format', title: formatLabel({ code: r.format_code, name: r.format_name || '', length: r.format_length || '' }) }) : null,
      due ? chip('prod-chip-due' + (due.tone ? ' prod-due-' + due.tone : ''), due.text, { 'data-testid': 'prod-card-due', 'data-tone': due.tone }) : null,
      hint ? chip('prod-chip-hint', hint, { 'data-testid': 'prod-card-needs' }) : null),
    h('div', { class: 'prod-card-meta' },
      h('button', { type: 'button', class: 'prod-card-comments' + (r.comment_count ? '' : ' prod-quiet'), 'data-testid': 'prod-card-comments', 'aria-label': `${r.comment_count || 0} comments, open`, onclick: () => ops.open(r, 'comments') },
        h('span', { class: 'prod-ic-comment', 'aria-hidden': 'true' }), ' ', String(r.comment_count || 0)),
      r.brief_url ? link(r.brief_url, 'Brief', { 'data-testid': 'prod-card-brief', class: 'prod-card-link' }) : null,
      r.creative_url ? link(r.creative_url, 'Creative', { 'data-testid': 'prod-card-creative', class: 'prod-card-link' }) : null,
      res ? h('span', { class: 'prod-card-result', 'data-testid': 'prod-card-result' }, res) : null),
    compact ? null : h('div', { class: 'prod-card-foot' },
      primary ? h('button', { type: 'button', class: 'btn prod-card-action' + (primary.primary ? ' primary' : ''), 'data-testid': 'prod-card-action', 'data-action': primary.key, onclick: () => ops.run(r, primary) }, primary.label) : null,
      status === 'launched' ? null : moveSel));
  return el;
}

function recentLaunched(r) {
  const t = Date.parse(r.status_changed_at || '');
  return isNaN(t) || Date.now() - t <= LAUNCHED_DAYS * 86400000;
}

// load bars: open cards for the week against the vendor's weekly capacity (capacity rows come from the board payload, one per vendor and week)
export function loadBars(capacity, vendors, week, cards) {
  const rows = (vendors || []).map((v) => {
    const c = (capacity || []).filter((x) => x.vendor === v && (!week || x.week === week));
    const per = c.length ? c[0].per_week : null;
    const open = c.length ? c.reduce((s, x) => s + (x.open || 0), 0) : cards.filter((x) => x.vendor === v && (!week || x.for_week === week) && statusOf(x) !== 'launched').length;
    return { vendor: v, per, open };
  }).filter((x) => x.per != null || x.open > 0);
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
  const filt = (r) => (!st.overdue || r.overdue) && (!st.q || [r.pain_point, r.cluster, r.icp, r.vendor, r.note, r.vendor_note].filter(Boolean).join(' ').toLowerCase().includes(st.q.toLowerCase()));
  const byStatus = new Map(columns.map((c) => [c.status, c.cards || []]));
  let defs = STATUSES.map(([k, l]) => ({ key: k, label: l, cards: (byStatus.get(k) || []).filter(filt) }));
  defs = defs.map((d) => (d.key === 'launched' && !st.closed ? { ...d, cards: d.cards.filter(recentLaunched), note: `Last ${LAUNCHED_DAYS} days` } : d));
  if (st.closed) defs.push({ key: 'dropped', label: 'Dropped', cards: (dropped || []).filter(filt), lane: true });
  if (st.stage) { const k = st.stage === 'creative_in' ? 'in_review' : st.stage; defs = defs.filter((d) => d.key === k); }
  const live = h('p', { class: 'prod-sr', 'aria-live': 'assertive', role: 'status', 'data-testid': 'prod-board-live' });
  const lanes = st.group === 'vendor' ? [...new Set([...vendors, ...defs.flatMap((d) => d.cards.map((c) => c.vendor))])].filter((v) => defs.some((d) => d.cards.some((c) => c.vendor === v))) : [null];

  const cellFor = (d, lane) => {
    const cards = lane ? d.cards.filter((c) => c.vendor === lane) : d.cards;
    const dropOk = d.key !== 'dropped';
    const list = h('div', { class: 'prod-board-list' + (dropOk ? '' : ' prod-nodrop'), 'data-testid': 'prod-board-list', 'data-status': d.key, 'data-lane': lane || '', 'data-label': d.label, 'aria-label': d.label, role: 'list' },
      cards.map((c) => cardEl(c, ops)), cards.length ? null : h('p', { class: 'prod-board-empty', 'data-testid': 'prod-board-empty' }, d.key === 'launched' ? 'Nothing launched lately' : 'Nothing here'));
    return { cards, list };
  };

  const cols = (lane) => defs.map((d) => {
    const { cards, list } = cellFor(d, lane);
    return h('section', { class: 'prod-board-col prod-col-' + d.key, 'data-testid': 'prod-board-column', 'data-status': d.key, 'data-droptarget': '', 'aria-label': d.label }, colHead(d.label, cards, d.note), list);
  });

  const scroll = h('div', { class: 'prod-board-scroll', 'data-testid': 'prod-board-scroll' },
    lanes[0] === null
      ? h('div', { class: 'prod-board-cols', style: { '--cols': defs.length } }, cols(null))
      : lanes.map((v) => { const mine = defs.flatMap((d) => d.cards).filter((c) => c.vendor === v); const bar = loadBars(capacity, [v], st.week, allCards || mine);
        return h('div', { class: 'prod-lane', 'data-testid': 'prod-board-lane', 'data-vendor': v }, h('div', { class: 'prod-lane-head' }, h('strong', null, v), h('span', { class: 'prod-lane-n' }, `${mine.length} cards`), bar),
          h('div', { class: 'prod-board-cols', style: { '--cols': defs.length } }, cols(v))); }));
  if (lanes.length === 0) scroll.append(h('p', { 'data-testid': 'prod-board-nolanes' }, 'No cards match.'));

  const el = h('div', { class: 'prod-board', 'data-testid': 'prod-board' }, live, scroll);
  enableDrag({ root: el, cardSel: '.prod-board-card', gripSel: '.prod-grip', listSel: '.prod-board-list:not(.prod-nodrop)', laneOf: (n) => (n.closest('[data-lane]') || n).dataset.lane || '', onDrop, live });
  return { el };
}
