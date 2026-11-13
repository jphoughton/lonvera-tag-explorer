// The card drawer: opens from a board card or a list row. Right-hand panel on desktop, full-screen sheet on a phone.
// Header (title, status, stepper, next actions), then tabs: Details (edit fields), Comments (thread and composer), History (who changed what; app users only, from the assignment detail).
// Test ids: prod-drawer-*, prod-comment-*. The page passes `ops` (save, run an action, reload) and a `getCard()` that returns the freshest copy of the card.
import { h } from '../util.js';
import { api } from '../api.js';
import { assignmentPanel } from './priorities_panel.js';
import { STATUSES, STATUS_LABEL, statusOf, md, weekLabel, productName, formatLabel, dueInfo, nextActions, eventLine, link, resultText, mondayOf, addDays, today, askDialog } from './production_common.js';

const when = (iso) => { const d = new Date(iso); return isNaN(d) ? '' : `${d.getMonth() + 1}/${d.getDate()} ${d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}`; };

export function openDrawer({ id, tab = 'details', ops, getCard, onClose, signal }) {
  let cur = tab;
  let comments = null;            // null = not loaded yet
  let history = null;
  let detail = null;
  let dirty = false;
  const opener = document.activeElement;
  const scrim = h('div', { class: 'prod-drawer-scrim', 'data-testid': 'prod-drawer-scrim', onclick: () => close() });
  const box = h('aside', { class: 'prod-drawer', 'data-testid': 'prod-drawer', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'prod-drawer-title', 'data-id': id });
  const head = h('div', { class: 'prod-drawer-head' });
  const tabsEl = h('div', { class: 'prod-drawer-tabs', role: 'tablist' });
  const pane = h('div', { class: 'prod-drawer-pane', 'data-testid': 'prod-drawer-pane' });
  box.append(head, tabsEl, pane);
  const msg = h('p', { class: 'prod-drawer-msg', role: 'status', 'data-testid': 'prod-drawer-msg' });
  const say = (t, bad) => { msg.textContent = t || ''; msg.classList.toggle('pri-err', !!bad); };

  function close() {
    document.removeEventListener('keydown', onKey, true);
    scrim.remove(); box.remove(); document.body.classList.remove('prod-drawer-open');
    if (opener && opener.isConnected) opener.focus();
    if (onClose) onClose();
  }
  function onKey(e) {
    if (e.key === 'Escape' && !document.querySelector('dialog[open]')) { e.preventDefault(); close(); return; }
    if (e.key === 'Tab') {                                 // keep focus inside the drawer
      const f = [...box.querySelectorAll('a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex="0"]')].filter((n) => n.offsetParent !== null);
      if (!f.length) return;
      if (e.shiftKey && document.activeElement === f[0]) { e.preventDefault(); f[f.length - 1].focus(); } else if (!e.shiftKey && document.activeElement === f[f.length - 1]) { e.preventDefault(); f[0].focus(); }
    }
  }

  function drawHead(r) {
    const status = statusOf(r);
    const idx = STATUSES.findIndex(([k]) => k === status);
    const due = dueInfo(r);
    head.replaceChildren(
      h('div', { class: 'prod-drawer-titlerow' },
        h('h2', { id: 'prod-drawer-title', 'data-testid': 'prod-drawer-title' }, r.pain_point || r.cluster || 'Concept'),
        h('button', { type: 'button', class: 'btn prod-drawer-close', 'data-testid': 'prod-drawer-close', 'aria-label': 'Close', onclick: close }, 'Close')),
      h('p', { class: 'pri-sub prod-drawer-sub' }, [productName(r.product), r.cluster, r.icp].filter(Boolean).join(' / ')),
      r.stage === 'dropped' ? h('p', { class: 'prod-pill prod-st-dropped', 'data-testid': 'prod-drawer-status' }, 'Dropped')
        : h('ol', { class: 'prod-stepper', 'data-testid': 'prod-drawer-stepper', 'aria-label': 'Status' }, STATUSES.map(([k, l], i) => h('li', { class: 'prod-step' + (i < idx ? ' prod-step-done' : '') + (i === idx ? ' prod-step-now' : ''), 'data-status': k, 'aria-current': i === idx ? 'step' : null, 'data-testid': 'prod-drawer-step' }, h('span', { class: 'prod-step-dot' }), h('span', { class: 'prod-step-label' }, l)))),
      h('div', { class: 'prod-drawer-meta' },
        h('span', { class: 'prod-pill prod-ps-' + status, 'data-testid': 'prod-drawer-status' }, STATUS_LABEL[status] || status),
        h('span', { class: 'prod-chip prod-chip-vendor' }, r.vendor), h('span', { class: 'pri-sub' }, weekLabel(r.for_week)),
        due ? h('span', { class: 'prod-chip prod-chip-due' + (due.tone ? ' prod-due-' + due.tone : ''), 'data-testid': 'prod-drawer-due-chip' }, due.text) : null),
      h('div', { class: 'prod-drawer-actions', 'data-testid': 'prod-drawer-actions' }, nextActions(r).map((a) => h('button', { type: 'button', class: 'btn' + (a.primary ? ' primary' : ''), 'data-testid': 'prod-drawer-action-' + a.key, onclick: () => act(r, a) }, a.label))),
      msg);
  }
  async function act(r, a) {
    say('');
    const ok = await ops.run(getCard(), a, { say });
    if (ok !== false) { comments = null; history = null; await refresh(); }
  }

  function drawTabs(r) {
    const defs = [['details', 'Details'], ['comments', `Comments (${r.comment_count || 0})`], ['history', 'History']];
    tabsEl.replaceChildren(...defs.map(([k, l]) => h('button', { type: 'button', role: 'tab', class: 'prod-drawer-tab', 'aria-selected': String(k === cur), 'data-testid': 'prod-drawer-tab-' + k, onclick: () => { cur = k; draw(); } }, l)));
  }

  // ---- details
  function fieldInput(label, el, hint) { return h('label', { class: 'prod-field' }, h('span', { class: 'pri-k' }, label), el, hint ? h('span', { class: 'pri-sub' }, hint) : null); }
  function drawDetails(r) {
    const mon = mondayOf(today());
    const weeks = [-7, 0, 7, 14, 21, 28].map((n) => addDays(mon, n));
    if (!weeks.includes(r.for_week)) weeks.push(r.for_week);
    weeks.sort();
    const fmts = (ops.formats() || []).slice();
    if (r.format_code && !fmts.some((f) => f.code === r.format_code)) fmts.push({ code: r.format_code, name: r.format_name || '', length: r.format_length || '' });
    const mk = (tid, el) => { el.setAttribute('data-testid', tid); el.addEventListener('input', () => { dirty = true; }); el.addEventListener('change', () => { dirty = true; }); return el; };
    const vendor = mk('prod-drawer-vendor', h('select', { class: 'pri-name' }, ops.vendors().map((v) => h('option', { value: v, selected: v === r.vendor }, v))));
    const week = mk('prod-drawer-week', h('select', { class: 'pri-name' }, weeks.map((w) => h('option', { value: w, selected: w === r.for_week }, weekLabel(w)))));
    const dueIn = mk('prod-drawer-due', h('input', { type: 'date', class: 'pri-name', value: r.due_date || '' }));
    const prio = mk('prod-drawer-priority', h('select', { class: 'pri-name' }, [['normal', 'Normal'], ['high', 'High']].map(([v, l]) => h('option', { value: v, selected: (r.priority || 'normal') === v }, l))));
    const fmt = mk('prod-drawer-format', h('select', { class: 'pri-name' }, h('option', { value: '', selected: !r.format_code }, 'No format chosen'), fmts.map((f) => h('option', { value: f.code, selected: f.code === r.format_code }, formatLabel(f)))));
    const brief = mk('prod-drawer-brief', h('input', { type: 'text', class: 'pri-name', placeholder: 'https://', value: r.brief_url || '', maxlength: '2000' }));
    const creative = mk('prod-drawer-creative', h('input', { type: 'text', class: 'pri-name', placeholder: 'https://', value: r.creative_url || '', maxlength: '2000' }));
    const note = mk('prod-drawer-note', h('textarea', { class: 'pri-name', rows: '3', maxlength: '500', placeholder: 'Internal note (the vendor never sees this)' }, r.note || ''));
    const save = h('button', { type: 'button', class: 'btn primary', 'data-testid': 'prod-drawer-save', onclick: async () => {
      const body = {};
      if (vendor.value !== r.vendor) body.vendor = vendor.value;
      if (week.value !== r.for_week) body.for_week = week.value;
      if ((dueIn.value || null) !== (r.due_date || null)) body.due_date = dueIn.value || null;
      if (prio.value !== (r.priority || 'normal')) body.priority = prio.value;
      if ((fmt.value || null) !== (r.format_code || null)) body.format_code = fmt.value || null;
      if ((brief.value.trim() || null) !== (r.brief_url || null)) body.brief_url = brief.value.trim() || null;
      if ((creative.value.trim() || null) !== (r.creative_url || null)) body.creative_url = creative.value.trim() || null;
      if (note.value.trim() !== (r.note || '').trim()) body.note = note.value.trim();
      if (!Object.keys(body).length) { say('Nothing changed.'); return; }
      save.disabled = true;
      const ok = await ops.save(r, body);
      save.disabled = false;
      if (ok) { dirty = false; history = null; say('Saved.'); await refresh(); } else say(ok === false ? ops.lastError() : '', true);
    } }, 'Save changes');
    const ads = (r.tracker_concept_ids || []);
    const resText = resultText(r);
    const more = h('details', { class: 'prod-drawer-concept', 'data-testid': 'prod-drawer-concept' }, h('summary', null, 'Concept details'), h('div', { class: 'prod-drawer-concept-body' }, h('p', { class: 'pri-sub', 'data-testid': 'prod-drawer-concept-loading' }, 'Open to load.')));
    more.addEventListener('toggle', async () => {
      if (!more.open || more.dataset.loaded) return;
      const body = more.querySelector('.prod-drawer-concept-body');
      body.replaceChildren(h('p', { class: 'pri-sub' }, 'Loading details…'));
      try { const d = await loadDetail(); body.replaceChildren(assignmentPanel(d)); more.dataset.loaded = '1'; } catch (e) { body.replaceChildren(h('p', { class: 'pri-err', role: 'alert' }, 'Could not load the details: ' + ((e && e.message) || 'error'))); }
    });
    pane.replaceChildren(h('div', { class: 'prod-drawer-form', 'data-testid': 'prod-drawer-details' },
      fieldInput('Vendor', vendor), fieldInput('Week of', week), fieldInput('Due date', dueIn), fieldInput('Priority', prio), fieldInput('Format', fmt),
      fieldInput('Brief link', brief), fieldInput('Creative link', creative), fieldInput('Note', note),
      r.vendor_note ? h('div', { class: 'prod-field' }, h('span', { class: 'pri-k' }, 'Vendor note'), h('p', { class: 'prod-drawer-vnote', 'data-testid': 'prod-drawer-vnote' }, r.vendor_note)) : null,
      ads.length || resText ? h('div', { class: 'prod-field' }, h('span', { class: 'pri-k' }, 'Ads'), h('p', { class: 'prod-drawer-vnote' }, [ads.join(', '), resText].filter(Boolean).join('. '))) : null,
      h('div', { class: 'prod-drawer-btns' }, save,
        r.stage === 'dropped' ? null : h('button', { type: 'button', class: 'btn', 'data-testid': 'prod-drawer-drop', onclick: () => dropIt(r) }, 'Drop')),
      more));
  }
  async function dropIt(r) {
    const res = await askDialog({ title: 'Drop this assignment?', note: `${r.vendor}, ${weekLabel(r.for_week).toLowerCase()}: ${r.cluster} / ${r.icp} / ${r.pain_point}. It moves to Dropped (shown under Show launched and dropped).`, okLabel: 'Drop', danger: true });
    if (!res) return;
    if (await ops.save(r, { dropped: true })) close();
  }
  async function loadDetail() {
    if (!detail) detail = await api(`/api/priorities/assignments/${id}/detail`, {}, { signal });
    return detail;
  }

  // ---- comments
  async function loadComments() {
    try { const d = await api(`/api/priorities/assignments/${id}/comments`, {}, { signal }); comments = d.comments || []; } catch (e) { if (e && e.name === 'AbortError') return; comments = []; say(e.message || 'Could not load comments.', true); }
  }
  async function drawComments(r) {
    if (comments === null) { pane.replaceChildren(h('p', { class: 'pri-sub', role: 'status' }, 'Loading comments…')); await loadComments(); if (cur !== 'comments') return; }
    const input = h('textarea', { class: 'pri-name prod-comment-input', rows: '3', maxlength: '2000', placeholder: 'Write a comment', 'aria-label': 'Comment', 'data-testid': 'prod-comment-input' });
    const post = h('button', { type: 'button', class: 'btn primary', 'data-testid': 'prod-comment-post', onclick: async () => {
      const body = input.value.trim();
      if (!body) { say('Write a comment first.', true); input.focus(); return; }
      post.disabled = true;
      const res = await ops.comment(r, body);
      post.disabled = false;
      if (res) { input.value = ''; comments = null; history = null; say(''); await refresh(); } else say(ops.lastError(), true);
    } }, 'Post comment');
    pane.replaceChildren(h('div', { class: 'prod-comments', 'data-testid': 'prod-comments' },
      comments.length ? h('ul', { class: 'prod-comment-list' }, comments.map((c) => h('li', { class: 'prod-comment prod-comment-' + c.author_kind, 'data-testid': 'prod-comment', 'data-kind': c.author_kind },
        h('div', { class: 'prod-comment-head' }, h('strong', { 'data-testid': 'prod-comment-author' }, c.author_name || 'Team'), c.author_kind === 'vendor' ? h('span', { class: 'prod-chip prod-chip-vendor' }, 'Vendor') : null, h('span', { class: 'pri-sub', 'data-testid': 'prod-comment-time' }, when(c.created_at))),
        h('p', { class: 'prod-comment-body', 'data-testid': 'prod-comment-body' }, c.body))))
        : h('p', { class: 'pri-sub', 'data-testid': 'prod-comment-none' }, 'No comments yet.'),
      h('div', { class: 'prod-comment-new' }, input, h('div', { class: 'prod-drawer-btns' }, post))));
  }

  // ---- history
  async function drawHistory() {
    if (history === null) {
      pane.replaceChildren(h('p', { class: 'pri-sub', role: 'status' }, 'Loading history…'));
      try { detail = null; const d = await loadDetail(); history = d.history || (d.assignment && d.assignment.history) || []; } catch (e) { if (e && e.name === 'AbortError') return; history = []; say(e.message || 'Could not load history.', true); }
      if (cur !== 'history') return;
    }
    pane.replaceChildren(history.length
      ? h('ol', { class: 'prod-histlist prod-drawer-history', 'data-testid': 'prod-drawer-history' }, history.slice().reverse().map((e) => { const l = eventLine(e); return h('li', { 'data-testid': 'prod-drawer-event', 'data-kind': e.kind }, h('span', { class: 'prod-hwhen' }, l.when), ' ', h('strong', null, l.who), ': ', l.what, l.move ? h('span', { class: 'pri-sub' }, ' · ' + l.move) : null); }))
      : h('p', { class: 'pri-sub', 'data-testid': 'prod-drawer-history-none' }, 'Nothing recorded yet.'));
  }

  function draw() {
    const r = getCard();
    if (!r) { close(); return; }
    drawHead(r); drawTabs(r);
    if (cur === 'comments') drawComments(r); else if (cur === 'history') drawHistory(); else drawDetails(r);
  }
  async function refresh() {
    if (!(await ops.reload())) return;
    draw();
  }

  document.body.append(scrim, box);
  document.body.classList.add('prod-drawer-open');
  document.addEventListener('keydown', onKey, true);
  draw();
  const first = box.querySelector('[data-testid=prod-drawer-close]');
  if (first) first.focus();
  return { close, refresh: () => { if (!dirty || cur !== 'details') draw(); else { const r = getCard(); if (r) { drawHead(r); drawTabs(r); } } }, id: () => id };
}
