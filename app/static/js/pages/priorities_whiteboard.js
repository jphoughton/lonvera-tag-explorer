// Board (/priorities/board): the Creative priorities whiteboard. Left: the backlog of concepts (one compact card each, filters and sort on top). Right: This Week in three lanes (Must do, If time, Parked).
// Drag a concept from the backlog into a lane, reorder inside a lane, move between lanes, or drop on the bar at the bottom to remove it (Undo for 6 seconds, or Ctrl/Cmd+Z). Everyone who opens the same week sees the same board.
// The store below is the source of truth; every change is applied at once, then the whole week is saved (PUT /api/priorities/whiteboard) through one promise chain, and a poll every few seconds picks up other people's changes.
// Everything the API returned goes in through h() / textContent, never innerHTML. Class names start with `wb-` and test ids with `pri-wb-` (never ad-, ads-, sponsor, banner, promo: ad blockers hide those).
import { h, fmt, isNum, href, navigate } from '../util.js';
import { api } from '../api.js';
import { pageHeader } from '../ui/pagehead.js';
import { showControls } from '../controls.js';
import { hueOf } from './priorities.js';
import { SECTIONS, SECTION_LABEL, STATUS_CLASS, STATUS_ORDER, roasX, compact$ } from './priorities_panel.js';
import { mondayOf, addDays, today } from './priorities_production.js';
import { createDnd } from './whiteboard_dnd.js';

const LANES = [['must', 'Must do'], ['if_time', 'If time'], ['parked', 'Parked']];
const LANE_NAME = Object.fromEntries(LANES);
const LANE_SHORT = { must: 'Must', if_time: 'If time', parked: 'Parked' };
const PRODUCTS = ['vita-bull', 'beef-liver', 'boneflex'];
const SEC_ORDER = SECTIONS.map(([k]) => k);
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const weekLabel = (iso) => { const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso || ''); return m ? `Week of ${MONTHS[+m[2] - 1]} ${+m[3]}` : iso; };
const norm = (v) => String(v == null ? '' : v).replace(/\s+/g, ' ').trim().toLowerCase();
const textKeyOf = (r) => 'text:' + [r.product, r.cluster, r.icp, r.pain_point].map(norm).join('|');   // same as app/priorities_assign.text_key
const isOpen = (a) => a.stage !== 'launched' && a.stage !== 'dropped';
const reduced = () => window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
const lsGet = (k) => { try { return localStorage.getItem(k); } catch (e) { return null; } };
const ssGet = (k) => { try { return sessionStorage.getItem(k); } catch (e) { return null; } };
const ssSet = (k, v) => { try { sessionStorage.setItem(k, v); } catch (e) { /* not remembered */ } };
const lsSet = (k, v) => { try { localStorage.setItem(k, v); } catch (e) { /* blocked: the board still works, it just is not remembered here */ } };
const copyObj = (o) => JSON.parse(JSON.stringify(o));
const SAVE_MS = 4000;

// last 14 days (Meta-reported), the same fields and rules as the Priorities table (priorities.js sum14): ROAS = total value / total spend, a dash under $100 of spend
const has14 = (r) => !r.manual && r.metrics && isNum(r.metrics.spend_14d);
const valueOf = (r) => { const m = r.metrics; return isNum(m.value_14d) ? m.value_14d : (isNum(m.roas_14d) ? m.roas_14d * m.spend_14d : 0); };
function sum(items) {
  let spend = 0, value = 0, n = 0;
  items.forEach((it) => { if (it.r && has14(it.r)) { n += 1; spend += it.r.metrics.spend_14d; value += valueOf(it.r); } });
  return { n, spend, value, roas: spend >= 100 ? value / spend : null };
}

function roll(el, to, f) {                                                         // a number that rolls to its new value in 150 ms
  const from = el._v === undefined ? to : el._v;
  el._v = to; el.dataset.v = String(to);
  cancelAnimationFrame(el._r);
  if (from === to || reduced()) { el.textContent = f(to); return; }
  const t0 = performance.now();
  const step = (now) => { const k = Math.min(1, (now - t0) / 150); el.textContent = f(from + (to - from) * k); if (k < 1) el._r = requestAnimationFrame(step); else el.textContent = f(to); };
  el._r = requestAnimationFrame(step);
}

export async function whiteboardPage(ctx) {
  showControls(false);
  const abort = ctx.signal;
  let snap;
  try { snap = await api('/api/priorities', {}, { signal: abort }); } catch (e) {
    if (e && e.name === 'AbortError') throw e;
    if (e && e.status === 404) { ctx.mount(h('div', { class: 'page pri-page wb-page' }, h('h1', null, 'Priorities'), h('p', { 'data-testid': 'pri-wb-empty' }, e.message))); return; }
    ctx.mount(h('div', { class: 'page pri-page wb-page' }, h('h1', null, 'Priorities'), h('p', { class: 'pri-err', role: 'alert', 'data-testid': 'pri-wb-error' }, 'Could not load the concepts: ' + (e.message || 'error') + ' '), h('button', { type: 'button', class: 'btn', onclick: () => navigate(location.pathname + location.search) }, 'Try again')));
    return;
  }
  let assigns = [];
  try { assigns = (await api('/api/priorities/assignments', { include_closed: 1 }, { signal: abort })).rows || []; } catch (e) { if (e && e.name === 'AbortError') throw e; assigns = []; }

  // ---------- concepts ----------
  const items = [];
  const byKey = new Map();
  (snap.rows || []).forEach((r) => {
    if (r.line_code === 'no line' || !r.ckey || (r.manual && r.done) || byKey.has(r.ckey)) return;
    const it = { key: r.ckey, r, hue: hueOf(r.cluster), pain: r.pain_point || '', cluster: r.cluster || '', icp: r.icp || '', product: r.product, section: r.section, status: r.status || '', story: r.story || '', ord: items.length };
    items.push(it); byKey.set(it.key, it);
  });
  const baseOf = (p) => (snap.baselines || {})[p] || {};
  const prodName = (p) => baseOf(p).product_name || p;
  const prodFor = (r) => {
    const rows = assigns.filter((a) => (a.concept_key && a.concept_key === r.ckey) || (a.text_key && a.text_key === textKeyOf(r))).filter(isOpen).sort((a, b) => (a.for_week < b.for_week ? 1 : -1));
    return rows.length ? `In production: ${rows[0].vendor}, ${rows[0].stage_label || rows[0].stage}` : '';
  };

  // ---------- store ----------
  const S = { week: ctx.params.week || mondayOf(today()), version: 0, lanes: { must: [], if_time: [], parked: [] }, notes: {}, dropped: [], meta: {}, removed: new Set(), undo: [], target: Number(lsGet('wb-target')) || 8,
    saved: 'saved', dirty: false, pending: 0, loading: true, loadError: '', q: '', prods: new Set(), secs: new Set(), stats: new Set(), sort: 'action', group: ssGet('wb-group') !== '0', collapsed: new Set(), pane: 'backlog', editing: false, trayOpen: false };
  const infoOf = (key) => byKey.get(key) || (() => { const m = S.meta[key] || {}; return { key, r: null, hue: hueOf(m.cluster), pain: m.pain_point || m.title || key, cluster: m.cluster || '', icp: m.icp || '', product: m.product, section: null, status: '', story: '' }; })();
  const tagOf = (key) => { const it = infoOf(key); return { product: it.product, cluster: it.cluster, icp: it.icp, pain_point: it.pain, line_code: it.r ? it.r.line_code : (S.meta[key] || {}).line_code, title: it.pain }; };
  const laneOfKey = (key) => LANES.map(([l]) => l).find((l) => S.lanes[l].includes(key)) || null;
  const onBoard = (key) => laneOfKey(key) !== null;
  const stateSnap = () => copyObj({ lanes: S.lanes, notes: S.notes, dropped: S.dropped });
  const sigOf = (st) => JSON.stringify([st.lanes.must, st.lanes.if_time, st.lanes.parked, st.dropped, Object.entries(st.notes).filter(([, v]) => v).sort()]);
  const localKey = () => 'wb-board-' + S.week;
  const persistLocal = () => lsSet(localKey(), JSON.stringify({ lanes: S.lanes, notes: S.notes, dropped: S.dropped, meta: S.meta, removed: [...S.removed], unsaved: S.saved !== 'saved' }));

  function adopt(resp) {                                                           // the server's answer becomes the local state
    const lanes = { must: [], if_time: [], parked: [] };
    const notes = {};
    (resp.picks || []).forEach((p) => {
      if (!lanes[p.lane]) return;
      lanes[p.lane].push(p.concept_key);
      if (p.note) notes[p.concept_key] = p.note;
      S.meta[p.concept_key] = { product: p.product, cluster: p.cluster, icp: p.icp, pain_point: p.pain_point, line_code: p.line_code, title: p.title };
    });
    (resp.dropped || []).forEach((p) => { S.meta[p.concept_key] = { product: p.product, cluster: p.cluster, icp: p.icp, pain_point: p.pain_point, line_code: p.line_code, title: p.title }; });
    S.lanes = lanes; S.notes = notes; S.dropped = (resp.dropped || []).map((p) => p.concept_key); S.version = resp.version || 0;
  }

  // ---------- saving: one promise chain, the latest state goes out each time ----------
  let chain = Promise.resolve();
  function setSaved(state) { S.saved = state; if (ui.save) { ui.save.dataset.state = state; ui.save.firstChild.textContent = state === 'saved' ? 'Saved' : state === 'saving' ? 'Saving…' : 'Not saved, retry'; ui.save.disabled = state !== 'error'; } persistLocal(); }
  function bodyNow() {
    const picks = [], dropped = [];
    LANES.forEach(([l]) => S.lanes[l].forEach((k) => picks.push({ concept_key: k, lane: l, note: S.notes[k] || '', ...tagOf(k) })));
    S.dropped.forEach((k) => dropped.push({ concept_key: k, ...tagOf(k) }));
    const removed = [...S.removed].filter((k) => !onBoard(k) && !S.dropped.includes(k));
    return { week: S.week, picks, dropped, removed };
  }
  function scheduleSave() {
    S.dirty = true; S.pending += 1; setSaved('saving');
    chain = chain.then(runSave).catch(() => {});
  }
  async function runSave() {
    if (!S.dirty) { S.pending = Math.max(0, S.pending - 1); return; }
    S.dirty = false;
    const body = bodyNow();
    try {
      const resp = await api('/api/priorities/whiteboard', {}, { method: 'PUT', body });
      body.removed.forEach((k) => S.removed.delete(k));
      S.version = resp.version || S.version;
      S.pending = Math.max(0, S.pending - 1);
      if (!S.dirty) {
        const mine = sigOf(stateSnap());
        setSaved('saved');
        if (!drag.isDragging() && !S.editing) { const lanes = { must: [], if_time: [], parked: [] }; (resp.picks || []).forEach((p) => lanes[p.lane] && lanes[p.lane].push(p.concept_key)); if (JSON.stringify([lanes.must, lanes.if_time, lanes.parked]) !== JSON.stringify([S.lanes.must, S.lanes.if_time, S.lanes.parked]) || (resp.dropped || []).map((p) => p.concept_key).join() !== S.dropped.join()) { adopt(resp); renderAll(); } }
        void mine;
      }
    } catch (e) {
      if (e && e.name === 'AbortError') return;
      S.pending = Math.max(0, S.pending - 1);
      S.dirty = true;                                                                // keep the local state; the Retry button saves it again
      setSaved('error');
    }
  }
  const retrySave = () => { if (S.saved === 'error') scheduleSave(); };

  // ---------- changes ----------
  function changed() { persistLocal(); scheduleSave(); renderAll(); }
  function mutate(fn) { S.undo.push(stateSnap()); if (S.undo.length > 60) S.undo.shift(); fn(); changed(); }
  const pull = (key) => { LANES.forEach(([l]) => { const i = S.lanes[l].indexOf(key); if (i >= 0) S.lanes[l].splice(i, 1); }); };
  function place(key, lane, index) {
    pull(key);
    const i = S.dropped.indexOf(key); if (i >= 0) S.dropped.splice(i, 1);
    S.removed.delete(key);
    const list = S.lanes[lane];
    list.splice(index === undefined || index > list.length ? list.length : Math.max(0, index), 0, key);
    S.meta[key] = { ...(S.meta[key] || {}), ...tagOf(key) };
  }
  const addTo = (key, lane, top) => mutate(() => place(key, lane, top ? 0 : undefined));
  function discard(key) {
    const lane = laneOfKey(key); if (!lane) return;
    const index = S.lanes[lane].indexOf(key), note = S.notes[key];
    mutate(() => { S.meta[key] = { ...(S.meta[key] || {}), ...tagOf(key) }; pull(key); S.dropped = [key, ...S.dropped.filter((k) => k !== key)].slice(0, 100); });
    toast(`Removed "${infoOf(key).pain}"`, () => mutate(() => { place(key, lane, index); if (note) S.notes[key] = note; }));
  }
  function restore(key) { mutate(() => { S.dropped = S.dropped.filter((k) => k !== key); S.removed.add(key); }); }
  function setNote(key, text) { const t = text.trim().slice(0, 500); if ((S.notes[key] || '') === t) return; mutate(() => { if (t) S.notes[key] = t; else delete S.notes[key]; }); }
  function undo() {
    const st = S.undo.pop(); if (!st) return;
    const was = LANES.flatMap(([l]) => S.lanes[l]);
    S.lanes = st.lanes; S.notes = st.notes; S.dropped = st.dropped;
    was.forEach((k) => { if (!onBoard(k) && !S.dropped.includes(k)) S.removed.add(k); });         // an undone add must come off the server's week too
    [...S.removed].forEach((k) => { if (onBoard(k) || S.dropped.includes(k)) S.removed.delete(k); });
    changed();
  }
  function onDrop(info) {
    if (info.to.tray) { discard(info.key); return; }
    mutate(() => place(info.key, info.to.lane, info.to.index));
  }
  const bottomOf = (lane) => S.lanes[lane].length;

  // ---------- toast ----------
  let toastEl = null, toastTimer = 0;
  function toast(text, onUndo) {
    clearTimeout(toastTimer);
    if (toastEl) toastEl.remove();
    toastEl = h('div', { class: 'wb-toast', role: 'status', 'data-testid': 'pri-wb-toast' }, h('span', { class: 'wb-toast-t' }, text + ' '),
      onUndo ? h('button', { type: 'button', class: 'wb-toast-u', 'data-testid': 'pri-wb-undo', onclick: () => { onUndo(); hideToast(); } }, 'Undo') : null);
    app.append(toastEl);
    toastTimer = setTimeout(hideToast, onUndo ? 6000 : 2500);
  }
  function hideToast() { clearTimeout(toastTimer); if (toastEl) { toastEl.remove(); toastEl = null; } }

  // ---------- backlog ----------
  const ui = {};
  const actionChip = (sec) => (sec ? h('span', { class: 'pri-sec pri-sec-' + sec, title: SECTION_LABEL[sec] || sec }, SECTION_LABEL[sec] || sec) : null);
  function numbers(it) {
    const r = it.r;
    if (!r || !has14(r) || r.metrics.spend_14d < 100) return { text: r && has14(r) ? '–' : '–', arrow: '' , tone: '' };
    const v = r.metrics.roas_14d;
    const base = baseOf(r.product).roas_30d;
    const rel = isNum(base) && base && isNum(v) ? v / base - 1 : 0;
    return { text: `${compact$(r.metrics.spend_14d)} · ${roasX(v)}`, arrow: rel >= 0.05 ? ' ▲' : rel <= -0.05 ? ' ▼' : '', tone: rel >= 0.05 ? 'up' : rel <= -0.05 ? 'down' : 'flat' };
  }
  function backlogCard(it) {
    const n = numbers(it);
    const tone = STATUS_CLASS[it.status] || 'flat';
    const inProd = it.r ? prodFor(it.r) : '';
    const el = h('div', { class: 'wb-card', tabindex: '0', role: 'group', 'aria-label': `${it.pain}. ${it.cluster}, ${it.icp}.`, 'data-key': it.key, 'data-testid': 'pri-wb-card', 'data-product': it.product, 'data-section': it.section || '', title: null },
      h('div', { class: 'wb-c1' },
        h('i', { class: 'wb-sdot wb-sd-' + tone, title: it.status || 'No status', 'data-testid': 'pri-wb-status' }),
        h('span', { class: 'wb-pain', 'data-testid': 'pri-wb-pain' }, it.pain),
        inProd ? h('i', { class: 'wb-inprod', title: inProd, 'data-testid': 'pri-wb-inprod' }) : null,
        h('span', { class: 'wb-wk', hidden: true, 'data-testid': 'pri-wb-wk' }),
        actionChip(it.section)),
      h('div', { class: 'wb-c2' },
        h('span', { class: 'wb-cl' }, `${it.cluster} > ${it.icp}`),
        h('i', { class: 'wb-pd wb-p-' + it.product, title: prodName(it.product) }),
        h('span', { class: 'wb-num' + (n.tone ? ' wb-num-' + n.tone : ''), title: n.text === '–' ? 'Under $100 of spend in the last 14 days: no ratio shown' : 'Spend and ROAS, last 14 days' }, n.text + n.arrow)),
      h('div', { class: 'wb-story', 'data-testid': 'pri-wb-story' }, it.story),
      h('button', { type: 'button', class: 'wb-add', 'data-testid': 'pri-wb-add', 'aria-label': 'Add to this week: ' + it.pain, onclick: (e) => { e.stopPropagation(); addTo(it.key, 'must'); } }, '+ This week'),
      h('span', { class: 'wb-grip', 'aria-hidden': 'true', title: 'Drag to move' }));
    el.style.setProperty('--h', String(it.hue));
    it.el = el; it.clEl = el.querySelector('.wb-cl');
    return it;
  }

  const visible = (it) => {
    if (S.prods.size && !S.prods.has(it.product)) return false;
    if (S.secs.size && !S.secs.has(it.section)) return false;
    if (S.stats.size && !S.stats.has(it.status)) return false;
    if (S.q) { const hay = norm([it.pain, it.cluster, it.icp, it.story, prodName(it.product), SECTION_LABEL[it.section]].join(' ')); if (!S.q.split(' ').every((w) => hay.includes(w))) return false; }
    return true;
  };
  const sorted = () => {
    const sp = (it) => (it.r && has14(it.r) ? it.r.metrics.spend_14d : -1);
    const ro = (it) => (it.r && has14(it.r) && isNum(it.r.metrics.roas_14d) && it.r.metrics.spend_14d >= 100 ? it.r.metrics.roas_14d : -1);
    const secRank = (it) => { const i = SEC_ORDER.indexOf(it.section); return i < 0 ? 99 : i; };
    const cmp = { action: (a, b) => secRank(a) - secRank(b) || sp(b) - sp(a) || a.ord - b.ord, spend: (a, b) => sp(b) - sp(a) || a.ord - b.ord, roas: (a, b) => ro(b) - ro(a) || a.ord - b.ord, cluster: (a, b) => a.cluster.localeCompare(b.cluster) || a.pain.localeCompare(b.pain) }[S.sort];
    return items.slice().sort(cmp);
  };
  const spendOf = (its) => its.reduce((t, it) => t + (it._ok && it.r && has14(it.r) ? it.r.metrics.spend_14d : 0), 0);
  function layoutBacklog() {                                                       // builds the list (cards are created once; sorting and grouping only move them)
    const frag = document.createDocumentFragment();
    const list = sorted();
    ui.groups = [];
    ui.list.classList.toggle('wb-grouped', S.group);
    if (S.group) {                                                                 // cluster > ideal customer profile (ICP) > pain point cards
      const order = []; const by = new Map();
      list.forEach((it) => {
        if (!by.has(it.cluster)) { by.set(it.cluster, new Map()); order.push(it.cluster); }
        const m = by.get(it.cluster);
        if (!m.has(it.icp)) m.set(it.icp, []);
        m.get(it.icp).push(it);
      });
      order.forEach((c) => {
        const icps = [...by.get(c).entries()];
        const all = [].concat(...icps.map(([, v]) => v));
        const open = !S.collapsed.has(c);
        const G = { cluster: c, items: all, igs: [] };
        G.toggle = h('button', { type: 'button', class: 'wb-ch-t', 'aria-expanded': String(open), 'data-cluster': c, 'data-testid': 'pri-wb-chead', onclick: () => { if (S.collapsed.has(c)) S.collapsed.delete(c); else S.collapsed.add(c); G.toggle.setAttribute('aria-expanded', String(!S.collapsed.has(c))); applyFilters(); } },
          h('span', { class: 'wb-chev', 'aria-hidden': 'true' }), h('span', { class: 'wb-ch-n' }, c || '(no cluster)'));
        G.count = h('span', { class: 'wb-ch-c', 'data-testid': 'pri-wb-chead-count', title: 'Concepts in this cluster' });
        G.spend = h('span', { class: 'wb-ch-s', title: 'Meta spend, last 14 days' });
        G.btn = h('button', { type: 'button', class: 'wb-addall', 'data-testid': 'pri-wb-addall', title: 'Add every concept of this cluster that is not on the board yet to Must do', onclick: (e) => { e.stopPropagation(); addMany(all, 'Added', c || 'this cluster'); } });
        G.head = h('div', { class: 'wb-ch' }, G.toggle, G.count, G.spend, G.btn);
        G.el = h('div', { class: 'wb-cg', 'data-cluster': c, 'data-testid': 'pri-wb-cgroup' }, G.head);
        G.el.style.setProperty('--h', String(hueOf(c)));
        icps.forEach(([icp, its]) => {
          const I = { icp, items: its };
          I.count = h('span', { class: 'wb-ih-c', 'data-testid': 'pri-wb-ihead-count' });
          I.btn = h('button', { type: 'button', class: 'wb-addicp', 'data-testid': 'pri-wb-addicp', title: 'Add every concept of this customer profile that is not on the board yet to Must do', onclick: (e) => { e.stopPropagation(); addMany(its, 'Added', icp || 'this profile'); } });
          I.head = h('div', { class: 'wb-ih', 'data-testid': 'pri-wb-ihead' }, h('span', { class: 'wb-ih-n' }, icp || '(no profile)'), I.count, I.btn);
          I.el = h('div', { class: 'wb-ig', 'data-testid': 'pri-wb-igroup' }, I.head, ...its.map((it) => it.el));
          G.igs.push(I);
          G.el.append(I.el);
        });
        ui.groups.push(G);
        frag.append(G.el);
      });
      list.forEach((it) => { it.clEl.textContent = prodName(it.product); });
    } else {
      list.forEach((it) => { it.clEl.textContent = `${it.cluster} > ${it.icp}`; });
      frag.append(...list.map((it) => it.el));
    }
    frag.append(ui.noMatch);
    ui.list.replaceChildren(frag);
    applyFilters();
  }
  const notOn = (its) => its.filter((it) => it._ok && !onBoard(it.key));
  function updateGroupBtns() {
    (ui.groups || []).forEach((G) => {
      const ng = notOn(G.items).length;
      G.btn.textContent = `+ All ${ng}`; G.btn.disabled = ng === 0;
      G.igs.forEach((I) => { const ni = notOn(I.items).length; I.btn.textContent = `+ ICP ${ni}`; I.btn.disabled = ni === 0; });
    });
  }
  function addMany(its, verb, name) {                                              // every visible concept not on the board yet goes to the bottom of Must do: one save, one undo step
    const todo = notOn(its);
    if (!todo.length) return;
    mutate(() => todo.forEach((it) => place(it.key, 'must')));
    toast(`${verb} ${todo.length} from ${name}`, undo);
  }
  function applyFilters() {
    let n = 0;
    items.forEach((it) => { const ok = visible(it); if (ok) n += 1; it._ok = ok; it.el.hidden = !ok || (S.group && S.collapsed.has(it.cluster)); });
    (ui.groups || []).forEach((G) => {
      const c = G.items.filter((it) => it._ok).length;
      G.el.hidden = c === 0; G.count.textContent = String(c);
      const sp = spendOf(G.items); G.spend.textContent = G.items.some((it) => it._ok && it.r && has14(it.r)) ? compact$(sp) : '';
      const shut = S.collapsed.has(G.cluster);
      G.igs.forEach((I) => { const k = I.items.filter((it) => it._ok).length; I.el.hidden = k === 0 || shut; I.count.textContent = String(k); });
    });
    updateGroupBtns();
    ui.count.textContent = `${n} of ${items.length}`;
    ui.noMatch.hidden = n !== 0;
    ui.list.dataset.shown = String(n);
  }
  function markGhosts() {
    updateGroupBtns();
    items.forEach((it) => {
      const lane = laneOfKey(it.key);
      const was = it.el.classList.contains('wb-onboard');
      if (!!lane !== was) it.el.classList.toggle('wb-onboard', !!lane);
      const wk = it.el.querySelector('.wb-wk');
      if (lane) { const t = 'Wk · ' + LANE_SHORT[lane]; if (wk.textContent !== t) wk.textContent = t; wk.hidden = false; } else wk.hidden = true;
      it.el.setAttribute('aria-disabled', lane ? 'true' : 'false');
    });
  }
  const pill = (label, on, fn, tid) => { const b = h('button', { type: 'button', class: 'fchip wb-pill-f', 'aria-pressed': String(on), 'data-testid': tid, onclick: () => { fn(b); } }, label); return b; };
  function toggler(set, v) { return (b) => { if (set.has(v)) set.delete(v); else set.add(v); b.setAttribute('aria-pressed', String(set.has(v))); applyFilters(); }; }

  // ---------- popover (story): hover 400 ms, click pins ----------
  let popTimer = 0, popFor = null, pinned = false;
  function showPop(card, pin) {
    const it = byKey.get(card.dataset.key); if (!it) return;
    popFor = card; pinned = !!pin;
    const r = it.r || {};
    ui.pop.replaceChildren(h('div', { class: 'wb-pop-h' }, it.pain, ' ', actionChip(it.section)), h('div', { class: 'wb-pop-m' }, `${prodName(it.product)} · ${it.cluster} > ${it.icp}`), it.story ? h('p', { class: 'wb-pop-s' }, it.story) : h('p', { class: 'wb-pop-s wb-muted' }, 'No story written for this concept.'),
      r.ingredients ? h('div', { class: 'wb-pop-m' }, 'Ingredients: ' + r.ingredients) : null, it.status ? h('div', { class: 'wb-pop-m' }, 'Status: ' + it.status) : null);
    ui.pop.hidden = false;
    const cr = card.getBoundingClientRect(), ar = app.getBoundingClientRect(), pr = ui.pop.getBoundingClientRect();
    let left = cr.right + 8; if (left + pr.width > innerWidth - 8) left = Math.max(8, cr.left - pr.width - 8);
    let top = Math.min(cr.top, innerHeight - pr.height - 8);
    ui.pop.style.left = left - ar.left + 'px'; ui.pop.style.top = Math.max(0, top - ar.top) + 'px';
  }
  function hidePop() { clearTimeout(popTimer); popTimer = 0; popFor = null; pinned = false; if (ui.pop) ui.pop.hidden = true; }

  // ---------- board ----------
  const bcards = new Map();
  function boardCard(key) {
    const it = infoOf(key);
    const n = numbers(it);
    const foot = it.r && has14(it.r) ? `${compact$(it.r.metrics.spend_14d)} · ${it.r.metrics.spend_14d >= 100 ? roasX(it.r.metrics.roas_14d) : '–'} · 14d` : '14d numbers not available';
    const el = h('div', { class: 'wb-bcard', tabindex: '0', role: 'group', 'aria-label': it.pain, 'data-key': key, 'data-testid': 'pri-wb-bcard', 'data-product': it.product },
      h('span', { class: 'wb-rank', 'data-testid': 'pri-wb-rank' }),
      h('div', { class: 'wb-bb' },
        h('div', { class: 'wb-bt', 'data-testid': 'pri-wb-btitle' }, it.pain),
        h('div', { class: 'wb-bm' }, h('span', { class: 'wb-ctag', 'data-testid': 'pri-wb-ctag', title: it.cluster }, it.cluster), h('span', { class: 'wb-cl' }, it.icp), actionChip(it.section)),
        h('div', { class: 'wb-bs' }, it.story || ''),
        h('div', { class: 'wb-bf wb-num' + (n.tone ? ' wb-num-' + n.tone : '') }, foot + (n.arrow || '')),
        h('div', { class: 'wb-bn', 'data-testid': 'pri-wb-note', hidden: true })),
      h('span', { class: 'wb-grip', 'aria-hidden': 'true', title: 'Drag to move' }),
      h('a', { class: 'wb-open', href: href('/priorities', { q: it.pain }), 'data-testid': 'pri-wb-open', title: 'Open in Priorities', 'aria-label': 'Open in Priorities: ' + it.pain, onclick: (e) => { e.preventDefault(); e.stopPropagation(); navigate(href('/priorities', { q: it.pain })); } }, '›'),
      h('button', { type: 'button', class: 'wb-x', 'data-testid': 'pri-wb-x', title: 'Remove from this week', 'aria-label': 'Remove: ' + it.pain, onclick: (e) => { e.stopPropagation(); discard(key); } }, '×'));
    el.style.setProperty('--h', String(it.hue));
    el.addEventListener('dblclick', (e) => { if (e.target.closest('button, a, input')) return; editNote(el, key); });
    bcards.set(key, el);
    return el;
  }
  function editNote(el, key) {
    if (el.querySelector('.wb-note-in')) return;
    S.editing = true;
    const box = el.querySelector('.wb-bn');
    const input = h('input', { type: 'text', class: 'wb-note-in', maxlength: '500', value: S.notes[key] || '', placeholder: 'Add a note', 'aria-label': 'Note', 'data-testid': 'pri-wb-note-input' });
    box.hidden = false; box.replaceChildren(input);
    let done = false;
    const end = (save) => { if (done) return; done = true; S.editing = false; const v = input.value; box.replaceChildren(); if (save) setNote(key, v); renderBoard(); el.focus(); };
    input.addEventListener('keydown', (e) => { e.stopPropagation(); if (e.key === 'Enter') { e.preventDefault(); end(true); } else if (e.key === 'Escape') { e.preventDefault(); end(false); } });
    input.addEventListener('blur', () => end(true));
    input.focus(); input.select();
  }
  function renderBoard() {
    let rank = 0;
    LANES.forEach(([lane]) => {
      const L = ui.lanes[lane];
      const keys = S.lanes[lane];
      const its = keys.map(infoOf);
      const s = sum(its);
      L.count.textContent = String(keys.length);
      L.spend.textContent = s.n ? compact$(s.spend) : '';
      if (L.target) L.target.textContent = `${keys.length} of ~5`;
      const kids = keys.map((k) => {
        const el = bcards.get(k) || boardCard(k);
        rank += 1;
        const rk = el.querySelector('.wb-rank'); if (rk.textContent !== String(rank)) rk.textContent = String(rank);
        el.classList.toggle('wb-parked', lane === 'parked');
        const note = S.notes[k] || '';
        const nb = el.querySelector('.wb-bn');
        if (!nb.querySelector('.wb-note-in')) { if (nb.textContent !== note) nb.textContent = note; nb.hidden = !note; }
        el.classList.toggle('wb-has-note', !!note);
        return el;
      });
      if (S.loading) L.list.replaceChildren(h('div', { class: 'wb-skel' }), h('div', { class: 'wb-skel' }));
      else if (!kids.length) L.list.replaceChildren(h('div', { class: 'wb-empty', 'data-testid': 'pri-wb-lane-empty' }, lane === 'parked' ? "Decided not now? Park it, don't lose it." : lane === 'must' ? 'Drag concepts here. These are the ones that must ship this week.' : 'Drag concepts here for if there is time.'));
      else L.list.replaceChildren(...kids);
    });
  }

  // ---------- header strip ----------
  function updateStrip() {
    const must = S.lanes.must.map(infoOf), ift = S.lanes.if_time.map(infoOf);
    const live = must.concat(ift);
    const s = sum(live);
    roll(ui.tot.n, live.length, (v) => String(Math.round(v)));
    ui.tot.split.textContent = `Must ${must.length} · If time ${ift.length}`;
    roll(ui.tot.spend, s.spend, (v) => (s.n ? compact$(v) : '–'));
    ui.tot.roas.textContent = s.roas === null ? '–' : roasX(s.roas); ui.tot.roas.dataset.v = s.roas === null ? '' : String(s.roas);
    // product mix (by spend, by count when there is no spend yet)
    const by = {}; live.forEach((it) => { const w = it.r && has14(it.r) && s.spend > 0 ? it.r.metrics.spend_14d : (s.spend > 0 ? 0 : 1); by[it.product] = (by[it.product] || 0) + w; });
    const tot = Object.values(by).reduce((a, b) => a + b, 0);
    ui.mix.replaceChildren(...PRODUCTS.filter((p) => by[p]).map((p) => h('i', { class: 'wb-p-' + p, style: { flex: String(by[p] / tot) }, title: `${prodName(p)}: ${Math.round(100 * by[p] / tot)}%` })));
    ui.mix.setAttribute('aria-label', 'Product mix');
    const secs = {}; live.forEach((it) => { secs[it.section] = (secs[it.section] || 0) + 1; });
    ui.actions.replaceChildren(...SEC_ORDER.filter((k) => secs[k]).map((k) => h('span', { class: 'pri-sec pri-sec-' + k }, `${SECTION_LABEL[k]} ${secs[k]}`)));
    const cap = live.length, pct = S.target ? cap / S.target : 0;
    ui.capBar.style.width = Math.min(100, Math.round(pct * 100)) + '%';
    ui.cap.dataset.state = pct > 1 ? 'over' : pct === 1 ? 'full' : 'ok';
    ui.capText.textContent = `${cap} of ${S.target}`;
    ui.pill.textContent = `This week (${S.lanes.must.length + S.lanes.if_time.length + S.lanes.parked.length})`;
  }
  function updateTray() {
    ui.trayN.textContent = String(S.dropped.length);
    ui.trayList.replaceChildren(...(S.dropped.length ? S.dropped.map((k) => h('span', { class: 'wb-dchip', 'data-testid': 'pri-wb-dchip', 'data-key': k }, h('span', { class: 'wb-dchip-t' }, infoOf(k).pain), h('button', { type: 'button', class: 'wb-restore', 'data-testid': 'pri-wb-restore', onclick: () => restore(k) }, 'Restore'))) : [h('span', { class: 'wb-muted', 'data-testid': 'pri-wb-tray-empty' }, 'Nothing removed this week.')]));
    ui.tray.dataset.empty = S.dropped.length ? '0' : '1';
  }
  function renderAll() { renderBoard(); updateStrip(); markGhosts(); updateTray(); }

  // ---------- copy list ----------
  function listText() {
    const out = [weekLabel(S.week)];
    [['must', 'Must do'], ['if_time', 'If time']].forEach(([l, name]) => {
      if (!S.lanes[l].length) return;
      out.push('', name + ':');
      S.lanes[l].forEach((k, i) => { const it = infoOf(k); out.push(`${i + 1}. ${it.pain} (${it.cluster} > ${it.icp})${it.section ? ' [' + SECTION_LABEL[it.section] + ']' : ''}${S.notes[k] ? ' - ' + S.notes[k] : ''}`); });
    });
    return out.join('\n');
  }
  async function copyList() {
    const t = listText();
    try { await navigator.clipboard.writeText(t); } catch (e) { const ta = h('textarea', { style: { position: 'fixed', opacity: '0' } }); ta.value = t; document.body.append(ta); ta.select(); try { document.execCommand('copy'); } catch (e2) { /* nothing more to try */ } ta.remove(); }
    toast('Copied the ranked list');
  }

  // ---------- build the page ----------
  const secChips = SEC_ORDER.map((k) => pill(SECTION_LABEL[k], false, toggler(S.secs, k), 'pri-wb-f-sec-' + k));
  const statusList = STATUS_ORDER.filter((s) => items.some((it) => it.status === s));
  const sortSel = h('select', { class: 'wb-sort', 'aria-label': 'Sort the backlog', 'data-testid': 'pri-wb-sort', onchange: () => { S.sort = sortSel.value; layoutBacklog(); } },
    [['action', 'Action order, then spend'], ['spend', 'Spend, last 14 days'], ['roas', 'ROAS, last 14 days'], ['cluster', 'Cluster A to Z']].map(([v, t]) => h('option', { value: v }, t)));
  const search = h('input', { type: 'search', class: 'wb-search', placeholder: 'Search pain point, cluster, ideal customer profile, story  ( / )', 'aria-label': 'Search the backlog', 'data-testid': 'pri-wb-search', oninput: () => { S.q = norm(search.value); applyFilters(); } });
  ui.count = h('span', { class: 'wb-count', 'data-testid': 'pri-wb-count', role: 'status' });
  ui.noMatch = h('div', { class: 'wb-nomatch', 'data-testid': 'pri-wb-nomatch', hidden: true }, 'No concepts match these filters.');
  ui.list = h('div', { class: 'wb-blist', 'data-testid': 'pri-wb-backlog', tabindex: '-1' });
  items.forEach(backlogCard);
  const groupBtn = pill('Group by cluster', S.group, (b) => { S.group = !S.group; ssSet('wb-group', S.group ? '1' : '0'); b.setAttribute('aria-pressed', String(S.group)); layoutBacklog(); }, 'pri-wb-group');
  const filters = h('div', { class: 'wb-filters', 'data-testid': 'pri-wb-filters' },
    h('div', { class: 'wb-f1' }, search, ui.count),
    h('div', { class: 'wb-f2' }, h('span', { class: 'wb-fl' }, 'Product'), ...PRODUCTS.map((p) => pill(prodName(p), false, toggler(S.prods, p), 'pri-wb-f-prod-' + p)),
      h('span', { class: 'wb-fl' }, 'Action'), ...secChips),
    h('div', { class: 'wb-f2' }, h('span', { class: 'wb-fl' }, 'Status'), ...statusList.map((s) => pill(s, false, toggler(S.stats, s), 'pri-wb-f-st-' + s.toLowerCase().replace(/\s+/g, '-'))), h('span', { class: 'wb-sp' }), sortSel, groupBtn));
  const backlog = h('section', { class: 'wb-pane wb-backlog', 'aria-label': 'Backlog', 'data-testid': 'pri-wb-backlog-pane' }, filters, ui.list);

  ui.lanes = {};
  const laneEls = LANES.map(([id, name]) => {
    const L = { count: h('span', { class: 'wb-lcount', 'data-testid': 'pri-wb-lcount' }), spend: h('span', { class: 'wb-lspend' }), target: id === 'must' ? h('span', { class: 'wb-ltarget' }) : null };
    L.list = h('div', { class: 'wb-list', 'data-lane': id, 'data-testid': 'pri-wb-list', 'aria-label': name });
    L.laneEl = h('div', { class: 'wb-lane', 'data-lane': id, 'data-testid': 'pri-wb-lane' },
      h('div', { class: 'wb-llabel' }, h('span', { class: 'wb-lname' }, name), L.count, L.target, id === 'parked' ? h('span', { class: 'wb-lhint' }, "Decided not now? Park it, don't lose it") : null, h('span', { class: 'wb-sp' }), L.spend), L.list);
    ui.lanes[id] = L;
    return L.laneEl;
  });
  ui.lanesWrap = h('div', { class: 'wb-lanes', 'data-testid': 'pri-wb-board' }, ...laneEls);
  const board = h('section', { class: 'wb-pane wb-week', 'aria-label': 'This week', 'data-testid': 'pri-wb-week-pane' }, ui.lanesWrap);

  // strip
  const weekBtn = (d, label, txt) => h('button', { type: 'button', class: 'wb-wbtn', 'aria-label': label, 'data-testid': 'pri-wb-week-' + (d < 0 ? 'prev' : 'next'), onclick: () => switchWeek(addDays(S.week, d * 7)) }, txt);
  ui.weekLabel = h('span', { class: 'wb-week-l', 'data-testid': 'pri-wb-week-label' }, weekLabel(S.week));
  ui.tot = { n: h('b', { 'data-testid': 'pri-wb-total-n' }), split: h('span', { class: 'wb-muted', 'data-testid': 'pri-wb-total-split' }), spend: h('b', { 'data-testid': 'pri-wb-total-spend' }), roas: h('b', { 'data-testid': 'pri-wb-total-roas' }) };
  ui.mix = h('span', { class: 'wb-mix', 'data-testid': 'pri-wb-mix', role: 'img' });
  ui.actions = h('span', { class: 'wb-amix', 'data-testid': 'pri-wb-actionmix' });
  ui.capBar = h('i');
  ui.capText = h('span', { class: 'wb-cap-t', 'data-testid': 'pri-wb-cap-text' });
  ui.cap = h('button', { type: 'button', class: 'wb-cap', 'data-testid': 'pri-wb-cap', title: 'Click to set how many concepts this week can hold (Must do plus If time)', onclick: () => editCap() }, h('span', { class: 'wb-cap-bar' }, ui.capBar), ui.capText);
  function editCap() {
    const input = h('input', { type: 'number', min: '1', max: '99', class: 'wb-cap-in', value: String(S.target), 'aria-label': 'Capacity target', 'data-testid': 'pri-wb-cap-input' });
    let closed = false;
    const done = (ok) => { if (closed) return; closed = true; const v = Math.round(Number(input.value)); if (ok && v >= 1 && v <= 99) { S.target = v; lsSet('wb-target', String(v)); } input.replaceWith(ui.cap); updateStrip(); };
    input.addEventListener('keydown', (e) => { e.stopPropagation(); if (e.key === 'Enter') done(true); else if (e.key === 'Escape') done(false); });
    input.addEventListener('blur', () => done(true));
    ui.cap.replaceWith(input); input.focus(); input.select();
  }
  ui.save = h('button', { type: 'button', class: 'wb-save', 'data-state': 'saved', 'data-testid': 'pri-wb-save', disabled: true, onclick: retrySave }, h('span', null, 'Saved'));
  const stat = (label, tip, ...kids) => h('span', { class: 'wb-stat', title: tip }, h('span', { class: 'wb-sl' }, label), ...kids);
  const strip = h('div', { class: 'wb-strip', 'data-testid': 'pri-wb-strip' },
    h('div', { class: 'wb-weeksel' }, weekBtn(-1, 'Previous week', '‹'), ui.weekLabel, weekBtn(1, 'Next week', '›')),
    stat('Concepts', 'Must do plus If time (Parked is not counted)', ui.tot.n, ui.tot.split), stat('Spend 14D', 'Meta spend over the last 14 days on these concepts', ui.tot.spend), stat('ROAS', 'Return on ad spend over the last 14 days: total purchase value divided by total spend (a dash under $100 of spend)', ui.tot.roas), ui.mix, ui.actions, h('span', { class: 'wb-sp' }), ui.cap,
    h('button', { type: 'button', class: 'btn wb-copy', 'data-testid': 'pri-wb-copy', onclick: copyList }, 'Copy list'), ui.save);

  // tabs and pill (narrow screens)
  const tabBtn = (id, label) => h('button', { type: 'button', class: 'wb-tab', role: 'tab', 'aria-selected': String(S.pane === id), 'data-testid': 'pri-wb-tab-' + id, onclick: () => setPane(id) }, label);
  const tabs = h('div', { class: 'wb-tabs', role: 'tablist' }, tabBtn('backlog', 'Backlog'), tabBtn('week', 'This week'));
  function setPane(id) { S.pane = id; app.dataset.pane = id; tabs.querySelectorAll('.wb-tab').forEach((b) => b.setAttribute('aria-selected', String(b.dataset.testid === 'pri-wb-tab-' + id))); }
  ui.pill = h('button', { type: 'button', class: 'wb-floatpill', 'data-testid': 'pri-wb-pill', onclick: () => setPane('week') }, 'This week (0)');

  // tray
  ui.trayN = h('b', { 'data-testid': 'pri-wb-tray-n' });
  ui.trayList = h('div', { class: 'wb-tlist' });
  ui.tray = h('div', { class: 'wb-tray', 'data-testid': 'pri-wb-tray', 'data-open': '0' },
    h('div', { class: 'wb-tpanel', 'data-testid': 'pri-wb-tray-panel' }, ui.trayList),
    h('button', { type: 'button', class: 'wb-tbar', 'aria-expanded': 'false', 'data-testid': 'pri-wb-tray-toggle', onclick: () => { S.trayOpen = !S.trayOpen; ui.tray.dataset.open = S.trayOpen ? '1' : '0'; ui.tray.querySelector('.wb-tbar').setAttribute('aria-expanded', String(S.trayOpen)); } },
      h('span', { class: 'wb-tbar-n' }, 'Dropped (', ui.trayN, ')'), h('span', { class: 'wb-tbar-drop' }, 'Drop to remove')));
  ui.live = h('div', { class: 'wb-live', 'aria-live': 'assertive', role: 'status', 'data-testid': 'pri-wb-live' });
  ui.pop = h('div', { class: 'wb-pop', hidden: true, role: 'tooltip', 'data-testid': 'pri-wb-pop' });

  const app = h('div', { class: 'wb-app', 'data-pane': 'backlog', 'data-testid': 'pri-wb-app' }, strip, tabs, backlog, board, ui.tray, ui.pill, ui.live, ui.pop);
  const head = pageHeader({
    title: 'Priorities', titleTestid: 'pri-wb-title',
    description: 'Plan the week: drag the concepts to make into Must do, If time or Parked. Everyone sees the same board.',
    actions: h('span', { class: 'wb-modes', role: 'group', 'aria-label': 'View' },
      h('a', { class: 'btn', href: href('/priorities', {}), 'data-testid': 'pri-wb-mode-table', onclick: (e) => { if (e.metaKey || e.ctrlKey || e.shiftKey) return; e.preventDefault(); navigate(href('/priorities', {})); } }, 'Table'),
      h('span', { class: 'btn primary wb-mode-on', 'aria-current': 'page', 'data-testid': 'pri-wb-mode-board' }, 'Board')),
  });
  const root = h('div', { class: 'page pri-page wb-page', 'data-testid': 'pri-wb-page' }, head, app);

  // ---------- drag and drop ----------
  const narrow = window.matchMedia('(max-width: 1099px)');
  const drag = createDnd({
    root: app, backlogScroll: ui.list, boardScroll: ui.lanesWrap, trayEl: ui.tray,
    lanes: () => LANES.map(([id]) => ({ id, laneEl: ui.lanes[id].laneEl, listEl: ui.lanes[id].list })),
    canDrag: (el) => (el.classList.contains('wb-bcard') ? !S.editing : !el.classList.contains('wb-onboard') && !narrow.matches),
    slotHeight: () => { const c = ui.lanesWrap.querySelector('.wb-bcard'); return c ? c.getBoundingClientRect().height : 84; },
    nameOf: (k) => infoOf(k).pain, laneName: (l) => LANE_NAME[l], live: ui.live, onDrop,
    onStart: () => { hidePop(); app.dataset.dragging = '1'; },
    onEnd: () => { delete app.dataset.dragging; },
  });

  // ---------- events ----------
  ui.list.addEventListener('click', (e) => {                                      // one delegated listener for every backlog card
    const el = e.target.closest('.wb-card'); if (!el || e.target.closest('button')) return;
    if (popFor === el && pinned) hidePop(); else showPop(el, true);
  });
  ui.list.addEventListener('dblclick', (e) => { const el = e.target.closest('.wb-card'); if (!el || el.classList.contains('wb-onboard') || e.target.closest('button')) return; hidePop(); addTo(el.dataset.key, 'must'); });
  ui.list.addEventListener('pointerover', (e) => {
    if (drag.isDragging() || pinned || e.pointerType !== 'mouse') return;
    const el = e.target.closest('.wb-card');
    if (!el || el === popFor || (e.relatedTarget && el.contains(e.relatedTarget))) return;
    clearTimeout(popTimer); popTimer = setTimeout(() => showPop(el, false), 400);
  });
  ui.list.addEventListener('pointerout', (e) => { if (pinned) return; const el = e.target.closest('.wb-card'); if (!el || (e.relatedTarget && el.contains(e.relatedTarget))) return; hidePop(); });
  ui.list.addEventListener('scroll', () => { if (!pinned) hidePop(); }, { passive: true });
  ui.list.addEventListener('keydown', (e) => {                                    // on a focused backlog card
    const el = e.target.closest('.wb-card'); if (!el || e.target !== el || drag.isPicking()) return;
    const k = el.dataset.key;
    if (e.key === 'Enter' && !el.classList.contains('wb-onboard')) { e.preventDefault(); addTo(k, 'must'); }
    else if (['1', '2', '3'].includes(e.key) && !el.classList.contains('wb-onboard')) { e.preventDefault(); addTo(k, LANES[Number(e.key) - 1][0], true); }
    else if (e.key === 'Escape') hidePop();
  });
  ui.lanesWrap.addEventListener('keydown', (e) => {                               // on a focused card of the board
    const el = e.target.closest('.wb-bcard'); if (!el || e.target !== el || drag.isPicking()) return;
    const k = el.dataset.key, lane = laneOfKey(k), at = LANES.findIndex(([l]) => l === lane);
    const focusBack = () => { const n = bcards.get(k); if (n) n.focus(); };
    if (['1', '2', '3'].includes(e.key)) { e.preventDefault(); mutate(() => place(k, LANES[Number(e.key) - 1][0], 0)); focusBack(); }
    else if ((e.key === ']' || e.key === '[') && at >= 0) { const to = at + (e.key === ']' ? 1 : -1); if (to < 0 || to >= LANES.length) return; e.preventDefault(); mutate(() => place(k, LANES[to][0], 0)); focusBack(); }
    else if (e.key === 'Delete' || e.key === 'Backspace') { e.preventDefault(); const sibs = [...el.parentElement.querySelectorAll('.wb-bcard')]; const next = sibs[sibs.indexOf(el) + 1] || sibs[sibs.indexOf(el) - 1]; discard(k); if (next) next.focus(); }
    else if (e.key === 'Enter') { e.preventDefault(); navigate(href('/priorities', { q: infoOf(k).pain })); }
    else if (e.key === 'F2') { e.preventDefault(); editNote(el, k); }
  });
  const onDocKey = (e) => {
    const inField = e.target.closest && e.target.closest('input, textarea, select, [contenteditable]');
    if (e.key === '/' && !inField && !e.ctrlKey && !e.metaKey) { e.preventDefault(); setPane('backlog'); search.focus(); }
    else if ((e.ctrlKey || e.metaKey) && !e.shiftKey && e.key.toLowerCase() === 'z' && !inField) { e.preventDefault(); undo(); }
  };
  document.addEventListener('keydown', onDocKey);
  document.addEventListener('pointerdown', (e) => { if (pinned && !e.target.closest('.wb-pop, .wb-card')) hidePop(); }, true);
  const fit = () => { const top = app.getBoundingClientRect().top; app.style.setProperty('--wb-h', Math.max(520, Math.floor(innerHeight - top - 12)) + 'px'); };
  window.addEventListener('resize', fit);
  document.body.classList.add('wb-fill');

  // ---------- loading a week, polling ----------
  async function loadWeek() {
    S.loading = true; S.loadError = ''; renderAll();
    let resp = null;
    try { resp = await api('/api/priorities/whiteboard', { week: S.week }, { signal: abort }); } catch (e) { if (e && e.name === 'AbortError') return; S.loadError = e.message || 'error'; }
    let local = null;
    try { local = JSON.parse(lsGet(localKey()) || 'null'); } catch (e) { local = null; }
    S.loading = false;
    if (resp && !(local && local.unsaved)) { adopt(resp); setSaved('saved'); } else if (local) {
      S.lanes = local.lanes; S.notes = local.notes || {}; S.dropped = local.dropped || []; S.meta = { ...(local.meta || {}), ...S.meta }; S.removed = new Set(local.removed || []);
      if (resp) S.version = resp.version || 0;
      if (local.unsaved) scheduleSave(); else setSaved(resp ? 'saved' : 'error');
    } else setSaved('error');
    ui.root_error.hidden = !S.loadError || !!local;
    renderAll();
  }
  ui.root_error = h('div', { class: 'wb-loaderr pri-err', role: 'alert', hidden: true, 'data-testid': 'pri-wb-loaderror' }, 'Could not load this week\'s board. ', h('button', { type: 'button', class: 'btn', onclick: () => loadWeek() }, 'Try again'));
  board.prepend(ui.root_error);
  function switchWeek(week) {
    S.week = week; S.undo = []; S.removed = new Set(); S.lanes = { must: [], if_time: [], parked: [] }; S.notes = {}; S.dropped = [];
    ui.weekLabel.textContent = weekLabel(week);
    navigate(href('/priorities/board', { week }), { replace: true, render: false });
    loadWeek();
  }
  async function poll() {
    if (document.hidden || S.loading || drag.isPicking() || S.dirty || S.pending || S.editing || S.saved === 'error') return;
    let res;
    try { res = await fetch('/api/priorities/whiteboard?week=' + encodeURIComponent(S.week), { credentials: 'include', headers: { 'If-None-Match': `W/"wb-${S.week}-${S.version}"` }, signal: abort }); } catch (e) { return; }
    if (res.status !== 200) return;
    let body; try { body = await res.json(); } catch (e) { return; }
    if (drag.isPicking() || S.dirty || S.pending || S.editing || body.week !== S.week || body.version === S.version) return;
    const before = sigOf(stateSnap());
    adopt(body);
    if (sigOf(stateSnap()) !== before) renderAll();
  }
  const timer = setInterval(poll, SAVE_MS);
  abort.addEventListener('abort', () => { clearInterval(timer); document.removeEventListener('keydown', onDocKey); window.removeEventListener('resize', fit); document.body.classList.remove('wb-fill'); drag.destroy(); }, { once: true });

  // ---------- mount ----------
  ui.save.firstChild.textContent = 'Saved';
  layoutBacklog();
  renderAll();
  ctx.mount(root);
  requestAnimationFrame(fit);
  document.title = 'Board · Priorities · Lonvera';
  await loadWeek();
}
