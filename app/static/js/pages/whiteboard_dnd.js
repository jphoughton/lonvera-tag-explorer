// Drag and drop for the Creative priorities Board (/priorities/board). Pointer events only (no HTML5 drag and drop), hand rolled.
// How it stays fluid: ONE lifted copy of the card (position: fixed, moved only with transform, in a single requestAnimationFrame loop that reads the latest pointer position);
// every rectangle is measured ONCE when the drag starts (in content coordinates of the scroller, so scrolling needs no new measuring) and the loop only does arithmetic and writes transforms;
// neighbours open a gap with translateY (no DOM move until the drop); on drop the copy glides into the slot, then the page reorders the DOM once and a FLIP pass smooths any difference.
// Keyboard: Space picks a card up, arrows move it (Up/Down inside a lane, Left/Right between the backlog and the lanes), Enter drops, Escape cancels.
// The page owns the data: this module only reports the drop through onDrop({ key, from, to, via }). Class names start with wb-.

const THRESHOLD = 4;            // pixels the mouse must move before a drag starts
const LONG_PRESS = 350;         // touch: hold this long on a card to lift it
const HYST = 8;                 // pixels past a card's midpoint before the slot moves (stops flicker)
const EDGE = 56;                // auto-scroll zone at the top and bottom of a pane
const MAX_SCROLL = 14;          // pixels per frame at the very edge
const GLIDE = 180;              // ms: the lifted copy settling into its slot
const SKIP = 'input, textarea, select, a, button, summary, [contenteditable]';

const reduced = () => window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
const T = (y) => (y ? `translate3d(0,${Math.round(y * 10) / 10}px,0)` : '');

export function createDnd(o) {
  const { root, backlogScroll, boardScroll, trayEl, onDrop, onStart = () => {}, onEnd = () => {}, nameOf = (k) => k, laneName = (l) => l, live } = o;
  const say = (t) => { if (live) live.textContent = t; };
  let drag = null;               // pointer drag: { el, key, kind, lane, ghost, ... }
  let pending = null;            // pointer is down, drag not started yet
  let picked = null;             // keyboard pick: { el, key, kind, lane, idx }
  let m = null;                  // measurements taken at the start of a drag or pick
  let cur = null;                // the current layout: { lane, idx } | { tray: true } | null
  let raf = 0;
  const pointer = { x: 0, y: 0 };
  const swallow = (ev) => { ev.stopPropagation(); ev.preventDefault(); };

  // ---------- measuring (once per drag) ----------
  const lanesNow = () => o.lanes();                                   // [{ id, laneEl, listEl }]
  const cardsOf = (listEl) => [...listEl.children].filter((c) => c.classList.contains('wb-bcard'));
  function measure(src) {
    const sRect = boardScroll.getBoundingClientRect();
    const bRect = backlogScroll.getBoundingClientRect();
    const sT = boardScroll.scrollTop;
    const ct = (r) => r.top - sRect.top + sT;                        // content y of a viewport rect, in the board scroller
    const lanes = lanesNow().map((L) => {
      const lr = L.laneEl.getBoundingClientRect();
      const ir = L.listEl.getBoundingClientRect();
      const cs = cardsOf(L.listEl).map((el) => { const r = el.getBoundingClientRect(); return { el, top: ct(r), h: r.height, mid: ct(r) + r.height / 2 }; });
      const pad = cs.length ? cs[0].top - ct(ir) : 8;
      const gap = cs.length > 1 ? cs[1].top - (cs[0].top + cs[0].h) : 8;
      return { id: L.id, laneEl: L.laneEl, listEl: L.listEl, top: ct(lr), bottom: ct(lr) + lr.height, listTop: ct(ir), pad, gap, left: ir.left + 8, width: ir.width, cards: cs, n: cs.length };
    });
    const srcR = src.getBoundingClientRect();
    m = { sRect, bRect, lanes, srcH: src.classList.contains('wb-bcard') ? srcR.height : (o.slotHeight ? o.slotHeight() : 84), srcTop: ct(srcR), tray: trayEl.getBoundingClientRect(), app: root.getBoundingClientRect(), idx: {} };
  }

  // ---------- layout: open a gap at (lane, index) by moving neighbours with transforms ----------
  const applied = new Map();                                            // el -> last transform written
  const put = (el, y) => { const v = T(y); if (applied.get(el) !== v) { applied.set(el, v); el.style.transform = v; } };
  function clearLayout() { applied.forEach((_, el) => { el.style.transform = ''; }); applied.clear(); gapEl().hidden = true; if (m) m.lanes.forEach((x) => x.listEl.style.removeProperty('--wb-x')); }
  const gapEl = () => (gapEl.el || (gapEl.el = (() => { const g = document.createElement('div'); g.className = 'wb-gap'; g.hidden = true; g.setAttribute('aria-hidden', 'true'); return g; })()));

  function layout(laneId, idx, src) {
    const li = m.lanes.findIndex((L) => L.id === laneId);
    const L = m.lanes[li];
    const ex = L.cards.filter((c) => c.el !== src);
    const H = m.srcH + L.gap;
    const same = L.cards.some((c) => c.el === src);
    let y = ex.length ? L.listTop + L.pad : L.listTop + L.pad;
    let gapTop = y;
    for (let k = 0; k <= ex.length; k += 1) {
      if (k === idx) { gapTop = y; y += H; }
      if (k < ex.length) { put(ex[k].el, y - ex[k].top); y += ex[k].h + L.gap; }
    }
    m.lanes.forEach((x, j) => {
      if (j !== li) x.cards.forEach((c) => { if (c.el !== src) put(c.el, 0); });
      const shift = j > li && !same && L.n > 0 ? H : 0;
      put(x.laneEl, shift);
    });
    put(L.laneEl, 0);
    m.lanes.forEach((x, j) => x.listEl.style.setProperty('--wb-x', (j === li && !same && L.n > 0 ? H : 0) + 'px'));      // the lane outline grows with the gap
    // the card's own slot: a same-lane move drags the dashed placeholder along; a move from elsewhere shows an outline in the gap
    const g = gapEl();
    if (same) { put(src, gapTop - m.srcTop); g.hidden = true; } else {
      if (src && src.classList.contains('wb-bcard')) put(src, 0);
      if (g.parentElement !== L.listEl) L.listEl.append(g);
      g.hidden = false; g.style.height = m.srcH + 'px'; g.style.transform = T(gapTop - L.listTop);
    }
    return { gapTop, li, H, same };
  }

  // the lane and slot under (x, y): arithmetic only, from the cached numbers
  function target(x, y, sT) {
    if (y >= m.tray.bottom - 72 && x > m.app.left && x < m.app.right) return { tray: true };
    const r = m.sRect;
    if (x < r.left || x > r.right || y < r.top || y > r.bottom) return null;
    const yc = y - r.top + sT;
    const curLane = cur && cur.lane ? m.lanes.findIndex((L) => L.id === cur.lane) : -1;
    const curSame = curLane >= 0 && m.lanes[curLane].cards.some((c) => c.el === drag.el);
    const shift = curLane >= 0 && !curSame && m.lanes[curLane].n > 0 ? m.srcH + m.lanes[curLane].gap : 0;
    let pick = null;
    for (let j = 0; j < m.lanes.length; j += 1) {
      const L = m.lanes[j];
      const top = L.top + (curLane >= 0 && j > curLane ? shift : 0);
      const bottom = L.bottom + (curLane >= 0 && j >= curLane ? shift : 0);
      if (yc >= top && yc <= bottom) { pick = L; break; }
    }
    if (!pick) pick = yc < m.lanes[0].top ? m.lanes[0] : m.lanes[m.lanes.length - 1];
    const ex = pick.cards.filter((c) => c.el !== drag.el);
    let i = cur && cur.lane === pick.id ? cur.idx : ex.filter((c) => yc > c.mid).length;
    while (i < ex.length && yc > ex[i].mid + HYST) i += 1;
    while (i > 0 && yc < ex[i - 1].mid - HYST) i -= 1;
    return { lane: pick.id, idx: Math.min(i, ex.length) };
  }

  // ---------- pointer ----------
  root.addEventListener('pointerdown', (e) => {
    if (e.button !== 0 || drag || pending || picked) return;
    const el = e.target.closest && e.target.closest('.wb-card, .wb-bcard');
    if (!el || !root.contains(el) || el.classList.contains('wb-ghost') || (o.canDrag && !o.canDrag(el))) return;
    const grip = e.target.closest('.wb-grip');
    if (!grip && e.target.closest(SKIP)) return;
    const touch = e.pointerType !== 'mouse';
    pending = { el, x: e.clientX, y: e.clientY, id: e.pointerId, touch, grip: !!grip, timer: 0 };
    if (touch && !grip) pending.timer = setTimeout(() => { if (pending) begin(pending.x, pending.y); }, LONG_PRESS);
    document.addEventListener('pointermove', onMove);
    document.addEventListener('pointerup', onUp);
    document.addEventListener('pointercancel', onCancel);
    document.addEventListener('keydown', onEsc, true);
  });
  function onMove(e) {
    pointer.x = e.clientX; pointer.y = e.clientY;
    if (drag) { e.preventDefault(); return; }
    if (!pending) return;
    const d = Math.hypot(e.clientX - pending.x, e.clientY - pending.y);
    if (pending.touch && !pending.grip) { if (d > 8) release(); return; }          // a finger that moves before the long press is a scroll
    if (d >= THRESHOLD) begin(e.clientX, e.clientY);
  }
  function release() { if (pending && pending.timer) clearTimeout(pending.timer); pending = null; unlisten(); }
  function unlisten() {
    document.removeEventListener('pointermove', onMove); document.removeEventListener('pointerup', onUp); document.removeEventListener('pointercancel', onCancel); document.removeEventListener('keydown', onEsc, true);
    document.removeEventListener('touchmove', noScroll);
  }
  const noScroll = (e) => e.preventDefault();

  function ghostOf(el, r) {
    const g = el.cloneNode(true);
    g.querySelectorAll('[data-testid]').forEach((n) => n.removeAttribute('data-testid'));
    ['data-testid', 'id', 'tabindex'].forEach((a) => g.removeAttribute(a));
    g.classList.remove('wb-placeholder', 'wb-flash'); g.classList.add('wb-ghost-drag'); g.setAttribute('aria-hidden', 'true');
    g.style.width = r.width + 'px'; g.style.height = r.height + 'px';
    document.body.append(g);
    return g;
  }
  function begin(x, y) {
    const p = pending; if (!p) return;
    if (p.timer) clearTimeout(p.timer);
    pending = null;
    const el = p.el;
    const r = el.getBoundingClientRect();
    measure(el);
    pointer.x = x; pointer.y = y;
    const kind = el.classList.contains('wb-bcard') ? 'board' : 'backlog';
    drag = { el, key: el.dataset.key, kind, lane: el.closest('[data-lane]') ? el.closest('[data-lane]').dataset.lane : null, ghost: ghostOf(el, r), offX: x - r.left, offY: y - r.top, w: r.width, h: r.height, last: '' };
    onStart(drag);
    try { el.setPointerCapture(p.id); } catch (err) { /* the pointer is gone already */ }
    el.classList.add('wb-placeholder');
    root.classList.add('wb-dnd');
    document.body.classList.add('wb-grabbing');
    if (p.touch) document.addEventListener('touchmove', noScroll, { passive: false });
    cur = null;
    drag.ghost.style.transform = `translate3d(${x - drag.offX}px,${y - drag.offY}px,0)`;
    raf = requestAnimationFrame(frame);
  }

  function frame() {
    if (!drag) return;
    const d = drag;
    const sT = boardScroll.scrollTop, bT = backlogScroll.scrollTop;                // reads first, writes after: layout is clean at the top of a frame
    // auto-scroll the pane under the pointer
    for (const [sc, r] of [[boardScroll, m.sRect], [backlogScroll, m.bRect]]) {
      if (pointer.x < r.left || pointer.x > r.right) continue;
      const top = pointer.y - r.top, bot = r.bottom - pointer.y;
      if (top < EDGE && top > -EDGE) sc.scrollTop -= MAX_SCROLL * ((EDGE - Math.max(top, 0)) / EDGE) ** 2;
      else if (bot < EDGE && bot > -EDGE) sc.scrollTop += MAX_SCROLL * ((EDGE - Math.max(bot, 0)) / EDGE) ** 2;
    }
    const t = target(pointer.x, pointer.y, sT);
    const sig = t ? (t.tray ? 'tray' : t.lane + ':' + t.idx) : '';
    if (sig !== (cur ? (cur.tray ? 'tray' : cur.lane + ':' + cur.idx) : '')) {
      cur = t;
      if (t && t.lane) layout(t.lane, t.idx, d.el); else clearLayout();
      root.classList.toggle('wb-over-tray', !!(t && t.tray));
      m.lanes.forEach((L) => L.laneEl.classList.toggle('wb-lane-over', !!(t && t.lane === L.id)));
    }
    const tray = cur && cur.tray;
    d.ghost.style.transform = `translate3d(${pointer.x - d.offX}px,${pointer.y - d.offY}px,0) ${reduced() ? '' : tray ? 'rotate(0deg) scale(.7)' : 'rotate(1.5deg) scale(1.03)'}`;
    raf = requestAnimationFrame(frame);
  }

  function onEsc(e) { if (e.key === 'Escape' && drag) { e.preventDefault(); e.stopPropagation(); finishDrag(false); } }
  function onUp(e) {
    pointer.x = e.clientX; pointer.y = e.clientY;
    if (pending) { release(); return; }
    if (!drag) return;
    window.addEventListener('click', swallow, true); setTimeout(() => window.removeEventListener('click', swallow, true), 0);      // the click that follows a drag must not open the card
    finishDrag(true);
  }
  function onCancel() { if (pending) { release(); return; } if (drag) finishDrag(false); }

  function glide(g, tx, ty, scale, rot, done, fade) {
    cancelAnimationFrame(raf);
    const ms = reduced() ? 0 : GLIDE;
    g.style.transition = ms ? `transform ${ms}ms cubic-bezier(.3,1.3,.5,1), opacity ${ms}ms linear` : 'none';
    g.style.transform = `translate3d(${tx}px,${ty}px,0) rotate(${rot}deg) scale(${scale})`;
    if (fade) g.style.opacity = '0';
    setTimeout(done, ms + 10);
  }

  function finishDrag(commit) {
    const d = drag; drag = null; unlisten();
    cancelAnimationFrame(raf);
    const end = (fade) => { if (fade && !reduced()) { const g = d.ghost; g.style.transition = 'opacity 140ms linear'; g.style.opacity = '0'; setTimeout(() => g.remove(), 150); } else d.ghost.remove(); d.el.classList.remove('wb-placeholder'); root.classList.remove('wb-dnd', 'wb-over-tray'); document.body.classList.remove('wb-grabbing'); m.lanes.forEach((L) => L.laneEl.classList.remove('wb-lane-over')); onEnd(); };
    const t = cur; cur = null;
    if (!commit || !t) {                                                          // back where it came from
      clearLayout();
      const r = d.el.getBoundingClientRect();
      glide(d.ghost, r.left, r.top, 1, 0, () => end());
      return;
    }
    if (t.tray) {
      const r = m.tray;
      glide(d.ghost, r.left + r.width / 2 - d.w / 2, r.top + 10 - d.h / 2, .5, 0, () => { end(); apply(d, t, null); }, true);
      return;
    }
    const L = m.lanes.find((x) => x.id === t.lane);
    const sT = boardScroll.scrollTop;
    const slot = layout(t.lane, t.idx, d.el);
    glide(d.ghost, L.left, slot.gapTop - sT + m.sRect.top, 1, 0, () => { apply(d, t, d.ghost); end(true); });
  }

  // the drop itself: remember where every board card is NOW (transforms included), clear the preview, let the page reorder the DOM once, then play the difference
  function apply(d, t, ghost) {
    flip(() => {
      clearLayout();
      onDrop({ key: d.key, kind: d.kind, from: d.lane, to: t.tray ? { tray: true } : { lane: t.lane, index: t.idx }, via: 'pointer' });
    }, t.tray ? null : d.key, t.lane);
    if (ghost) { /* the copy is removed by end() right after: the new card is already under it */ }
  }

  // ---------- FLIP: smooth any difference between where a card was and where the reorder puts it ----------
  function flip(mutate, flashKey, lane) {
    const first = new Map();
    root.querySelectorAll('.wb-bcard').forEach((el) => { const r = el.getBoundingClientRect(); first.set(el.dataset.key, r.top); });
    root.classList.add('wb-nomotion');
    mutate();
    const moved = [];
    root.querySelectorAll('.wb-bcard').forEach((el) => {
      const f = first.get(el.dataset.key);
      if (f === undefined) return;
      const dy = f - el.getBoundingClientRect().top;
      if (Math.abs(dy) > 1) { el.style.transform = T(dy); moved.push(el); }
    });
    void root.offsetHeight;
    root.classList.remove('wb-nomotion');
    if (moved.length && !reduced()) {
      moved.forEach((el) => { el.style.transition = 'transform 180ms cubic-bezier(.2,.9,.3,1)'; el.style.transform = ''; });
      setTimeout(() => moved.forEach((el) => { el.style.transition = ''; }), 220);
    } else moved.forEach((el) => { el.style.transform = ''; });
    if (flashKey) {
      const el = root.querySelector(`.wb-bcard[data-key="${CSS.escape(flashKey)}"]`);
      if (el) { el.classList.remove('wb-flash'); void el.offsetWidth; el.classList.add('wb-flash'); setTimeout(() => el.classList.remove('wb-flash'), 450); }
    }
  }

  // ---------- keyboard ----------
  const slotCount = (laneId, src) => { const L = m.lanes.find((x) => x.id === laneId); return L.cards.filter((c) => c.el !== src).length; };
  function showPick() {
    if (!picked.lane) { clearLayout(); m.lanes.forEach((L) => L.laneEl.classList.remove('wb-lane-over')); return; }
    layout(picked.lane, picked.idx, picked.el);
    m.lanes.forEach((L) => L.laneEl.classList.toggle('wb-lane-over', L.id === picked.lane));
    const g = gapEl(); const target = picked.el.classList.contains('wb-bcard') && !g.hidden ? g : picked.el;
    if (!g.hidden && target.scrollIntoView) target.scrollIntoView({ block: 'nearest' });
  }
  const announce = () => {
    if (!picked.lane) { say('Back in the backlog. Press the right arrow to choose a lane.'); return; }
    say(`${laneName(picked.lane)}, position ${picked.idx + 1} of ${slotCount(picked.lane, picked.el) + 1}.`);
  };
  function endPick(commit) {
    const p = picked; picked = null;
    p.el.classList.remove('wb-picked'); p.el.removeAttribute('aria-grabbed');
    root.classList.remove('wb-dnd');
    if (!commit || !p.lane) { clearLayout(); m.lanes.forEach((L) => L.laneEl.classList.remove('wb-lane-over')); say(commit ? 'Nothing to drop. Choose a lane first.' : 'Move cancelled.'); p.el.focus(); return; }
    const info = { key: p.key, kind: p.kind, from: p.fromLane, to: { lane: p.lane, index: p.idx }, via: 'keyboard' };
    flip(() => { clearLayout(); m.lanes.forEach((L) => L.laneEl.classList.remove('wb-lane-over')); onDrop(info); }, p.key, p.lane);
    say('Dropped.');
    const after = root.querySelector(`.wb-bcard[data-key="${CSS.escape(p.key)}"]`);
    if (after) after.focus();
  }
  root.addEventListener('keydown', (e) => {
    const el = e.target.closest && e.target.closest('.wb-card, .wb-bcard');
    if (!el || e.target !== el) return;
    if (!picked) {
      if ((e.key === ' ' || e.key === 'Spacebar') && !el.classList.contains('wb-ghost') && !drag) {
        e.preventDefault();
        measure(el);
        const board = el.classList.contains('wb-bcard');
        const lane = board ? el.closest('[data-lane]').dataset.lane : null;
        const L = lane && m.lanes.find((x) => x.id === lane);
        picked = { el, key: el.dataset.key, kind: board ? 'board' : 'backlog', lane, fromLane: lane, idx: board ? L.cards.findIndex((c) => c.el === el) : 0 };
        el.classList.add('wb-picked'); el.setAttribute('aria-grabbed', 'true');
        root.classList.add('wb-dnd');
        say(`Picked up ${nameOf(picked.key)}. ${board ? `${laneName(lane)}, position ${picked.idx + 1}. ` : ''}Use the arrow keys to move, Enter to drop, Escape to cancel.`);
        showPick();
      }
      return;
    }
    if (picked.el !== el) return;
    const ids = m.lanes.map((L) => L.id);
    const k = e.key;
    if (k === 'ArrowDown' || k === 'ArrowUp') {
      e.preventDefault();
      if (!picked.lane) return;
      const n = slotCount(picked.lane, el);
      const i = Math.max(0, Math.min(n, picked.idx + (k === 'ArrowDown' ? 1 : -1)));
      if (i === picked.idx) return;
      picked.idx = i; showPick(); announce();
    } else if (k === 'ArrowRight' || k === 'ArrowLeft') {
      e.preventDefault();
      const at = picked.lane ? ids.indexOf(picked.lane) : -1;
      const to = at + (k === 'ArrowRight' ? 1 : -1);
      if (to < -1 || to >= ids.length) return;
      if (to === -1 && picked.kind === 'board') return;                           // a card already on the board cannot go back to the backlog by keyboard
      picked.lane = to === -1 ? null : ids[to];
      if (picked.lane) picked.idx = at === -1 ? slotCount(picked.lane, el) : Math.min(picked.idx, slotCount(picked.lane, el));       // from the backlog a card enters at the bottom of the lane
      showPick(); announce();
    } else if (k === 'Enter') { e.preventDefault(); endPick(true); }
    else if (k === 'Escape') { e.preventDefault(); endPick(false); }
    else if (k === ' ' || k === 'Spacebar') { e.preventDefault(); endPick(true); }
  });
  root.addEventListener('focusout', (e) => {
    if (picked && e.target === picked.el && !root.contains(e.relatedTarget)) endPick(false);
  });

  return {
    flip,
    isPicking: () => !!picked || !!drag,
    isDragging: () => !!drag,
    destroy() { cancelAnimationFrame(raf); if (pending) release(); document.querySelectorAll('.wb-ghost-drag').forEach((g) => g.remove()); document.body.classList.remove('wb-grabbing'); unlisten(); },
  };
}

// the little grip that starts a touch drag (dots are drawn by CSS)
export const GRIP_CLASS = 'wb-grip';
