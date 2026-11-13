// Drag and drop for the Production board and the Plan grid: pointer events (mouse and touch), live reorder preview, and a keyboard path (Space picks a card up, arrows move it, Enter or Space drops, Escape cancels).
// The dragged card itself moves through the DOM while you drag, so what you see is what will be saved. On drop the caller gets the list the card ended in and its two neighbours; the caller saves it
// (and redraws from data, which also undoes the preview when a save is refused). Touch starts a drag only from the grip (touch-action: none there), so a finger can still scroll the columns.
import { h } from '../util.js';

const INTERACTIVE = 'a, button, input, select, textarea, summary, details, label';
const THRESHOLD = 6;

export function enableDrag({ root, cardSel, gripSel, listSel, laneOf = () => '', onDrop, live }) {
  const lists = () => [...root.querySelectorAll(listSel)];
  const cardsIn = (list, except) => [...list.children].filter((c) => c.matches(cardSel) && c !== except);
  const say = (t) => { if (live) live.textContent = t; };
  let drag = null;            // pointer drag in progress
  let picked = null;          // keyboard pick in progress
  let moving = false;         // true while we move the picked card ourselves (that makes the browser blur it for a moment)
  const relocate = (card, fn) => { moving = true; fn(); card.focus(); moving = false; };

  const where = (card) => { const l = card.parentElement; const sibs = cardsIn(l); return { list: l, index: sibs.indexOf(card), total: sibs.length }; };
  const neighbours = (card) => { const l = card.parentElement; const sibs = cardsIn(l); const i = sibs.indexOf(card); return { before: sibs[i + 1] || null, after: sibs[i - 1] || null }; };
  const home = (card) => ({ list: card.parentElement, next: card.nextElementSibling });
  const restore = (card, o) => { o.list.insertBefore(card, o.next); };
  const place = (card, list, y) => {
    const sibs = cardsIn(list, card);
    let before = null;
    for (const s of sibs) { const r = s.getBoundingClientRect(); if (y < r.top + r.height / 2) { before = s; break; } }
    const empty = list.querySelector('.prod-board-empty');
    list.insertBefore(card, before || null);
    if (empty) list.append(empty);
  };
  const markOver = (list) => lists().forEach((l) => l.classList.toggle('prod-over', l === list));

  function finish(card, origin, commit, via) {
    const was = origin;
    const now = where(card);
    const nb = neighbours(card);
    const same = was.list === now.list && was.next === card.nextElementSibling;
    card.classList.remove('prod-dragging', 'prod-picked');
    card.removeAttribute('aria-grabbed');
    markOver(null);
    if (!commit || same) { restore(card, was); return; }
    onDrop({ card, list: now.list, from: was.list, before: nb.before, after: nb.after, via });
  }

  // ---- pointer
  root.addEventListener('pointerdown', (e) => {
    if (e.button !== 0 || drag || picked) return;
    const card = e.target.closest(cardSel);
    if (!card || !root.contains(card)) return;
    const grip = gripSel && e.target.closest(gripSel);
    const inter = e.target.closest(INTERACTIVE);
    if (inter && !inter.hasAttribute('data-dragok') && !grip) return;
    if (e.pointerType !== 'mouse' && !grip) return;
    drag = { card, x: e.clientX, y: e.clientY, started: false, ghost: null, origin: home(card), dx: 0, dy: 0, lane: laneOf(card) };
    document.addEventListener('pointermove', move);
    document.addEventListener('pointerup', up);
    document.addEventListener('pointercancel', cancel);
    document.addEventListener('keydown', escKey, true);
  });
  function begin(e) {
    const { card } = drag;
    const r = card.getBoundingClientRect();
    const ghost = card.cloneNode(true);
    ghost.querySelectorAll('[data-testid]').forEach((n) => n.removeAttribute('data-testid'));
    ghost.removeAttribute('data-testid'); ghost.removeAttribute('tabindex'); ghost.removeAttribute('id');
    ghost.classList.add('prod-ghost');
    ghost.setAttribute('aria-hidden', 'true');
    Object.assign(ghost.style, { width: r.width + 'px', left: r.left + 'px', top: r.top + 'px' });
    document.body.append(ghost);
    drag.ghost = ghost; drag.dx = e.clientX - r.left; drag.dy = e.clientY - r.top; drag.started = true;
    card.classList.add('prod-dragging'); card.setAttribute('aria-grabbed', 'true');
    document.body.classList.add('prod-is-dragging');
  }
  function move(e) {
    if (!drag) return;
    if (!drag.started) {
      if (Math.hypot(e.clientX - drag.x, e.clientY - drag.y) < THRESHOLD) return;
      begin(e);
    }
    e.preventDefault();
    drag.ghost.style.left = e.clientX - drag.dx + 'px';
    drag.ghost.style.top = e.clientY - drag.dy + 'px';
    drag.ghost.hidden = true;
    const under = document.elementFromPoint(e.clientX, e.clientY);
    drag.ghost.hidden = false;
    let list = under && under.closest(listSel);
    if (!list) { const col = under && under.closest('[data-droptarget]'); if (col) list = col.matches(listSel) ? col : col.querySelector(listSel); }
    if (list && root.contains(list) && laneOf(list) === drag.lane) { place(drag.card, list, e.clientY); markOver(list); } else markOver(null);
    // scroll a column container / the board while dragging near an edge
    const sc = under && under.closest('.prod-board-scroll, .prod-plan-scroll');
    if (sc) { const b = sc.getBoundingClientRect(); if (e.clientX > b.right - 40) sc.scrollLeft += 14; else if (e.clientX < b.left + 40) sc.scrollLeft -= 14; }
  }
  function end() {
    document.removeEventListener('pointermove', move); document.removeEventListener('pointerup', up); document.removeEventListener('pointercancel', cancel); document.removeEventListener('keydown', escKey, true);
    if (drag && drag.ghost) drag.ghost.remove();
    document.body.classList.remove('prod-is-dragging');
  }
  function up() {
    const d = drag; drag = null; end();
    if (!d || !d.started) return;
    const swallow = (ev) => { ev.stopPropagation(); ev.preventDefault(); };       // the click that follows a drag must not open the card
    window.addEventListener('click', swallow, true); setTimeout(() => window.removeEventListener('click', swallow, true), 0);
    finish(d.card, d.origin, true, 'pointer');
  }
  function cancel() { const d = drag; drag = null; end(); if (d && d.started) finish(d.card, d.origin, false); }
  function escKey(e) { if (e.key === 'Escape' && drag && drag.started) { e.preventDefault(); cancel(); } }

  // ---- keyboard
  root.addEventListener('keydown', (e) => {
    const card = e.target.closest && e.target.closest(cardSel);
    if (!card || e.target !== card) return;
    if (!picked) {
      if (e.key === ' ' || e.key === 'Spacebar') {
        e.preventDefault();
        picked = { card, origin: home(card), lane: laneOf(card) };
        card.classList.add('prod-picked'); card.setAttribute('aria-grabbed', 'true');
        const w = where(card);
        say(`Picked up ${card.dataset.title || 'card'}. ${listName(w.list)}, position ${w.index + 1} of ${w.total}. Use the arrow keys to move, Enter to drop, Escape to cancel.`);
      }
      return;
    }
    if (picked.card !== card) return;
    const ls = lists().filter((l) => laneOf(l) === picked.lane);
    const w = where(card);
    if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
      e.preventDefault();
      const i = ls.indexOf(w.list) + (e.key === 'ArrowRight' ? 1 : -1);
      if (i < 0 || i >= ls.length) return;
      const target = ls[i];
      const sibs = cardsIn(target, card);
      relocate(card, () => target.insertBefore(card, sibs[Math.min(w.index, sibs.length)] || null));
      const n = where(card);
      say(`${listName(n.list)}, position ${n.index + 1} of ${n.total}.`);
    } else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      const sibs = cardsIn(w.list);
      const i = w.index + (e.key === 'ArrowDown' ? 1 : -1);
      if (i < 0 || i >= sibs.length) return;
      relocate(card, () => { if (e.key === 'ArrowDown') w.list.insertBefore(card, sibs[i].nextElementSibling); else w.list.insertBefore(card, sibs[i]); });
      const n = where(card);
      say(`${listName(n.list)}, position ${n.index + 1} of ${n.total}.`);
    } else if (e.key === 'Enter' || e.key === ' ' || e.key === 'Spacebar') {
      e.preventDefault();
      const p = picked; picked = null;
      say('Dropped.');
      finish(p.card, p.origin, true, 'keyboard');
    } else if (e.key === 'Escape') {
      e.preventDefault();
      const p = picked; picked = null;
      finish(p.card, p.origin, false);
      card.focus();
      say('Move cancelled.');
    }
  });
  root.addEventListener('focusout', (e) => {
    if (picked && !moving && e.target === picked.card && !root.contains(e.relatedTarget)) { const p = picked; picked = null; finish(p.card, p.origin, false); say('Move cancelled.'); }
  });
  const listName = (l) => l.getAttribute('aria-label') || l.dataset.label || 'List';
}

// The little grip that starts a touch drag (dots are drawn by CSS)
export const gripEl = () => h('span', { class: 'prod-grip', 'data-testid': 'prod-board-grip', 'aria-hidden': 'true', title: 'Drag to move' });
