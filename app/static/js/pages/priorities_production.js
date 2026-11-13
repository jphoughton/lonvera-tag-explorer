// Production (/priorities/production): Board (kanban), List (the table of assignments, links edited in place) and Plan (four-week vendor grid) over the same assignments, plus the card drawer. The public vendor page is in priorities_vendor.js.
// Everything the user typed or the API returned goes in through textContent / h(); links are built with h('a', {href, target: '_blank', rel: 'noopener noreferrer'}), never innerHTML.
// Class names and test ids start with `prod-` (never ad-, ads-, sponsor, banner, promo: ad blockers hide those).
import { h, fmt, isNum, href, navigate } from '../util.js';
import { api } from '../api.js';
import { showControls } from '../controls.js';
import { expander } from './priorities_panel.js';
import { pageHeader } from '../ui/pagehead.js';
import { filterRow, seg, selectCtl, checkCtl, searchCtl } from '../ui/pagefilters.js';

import { eventLine, STAGES, STATUSES, STATUS_LABEL, statusOf, VENDOR_FALLBACK, md, weekLabel, productName, stageLabel, today, mondayOf, addDays, formatLabel, rowFormat, resultText, link, short, askDialog, toast, dueInfo } from './production_common.js';
import { board as boardView, loadBars } from './production_board.js';
import { plan as planView } from './production_plan.js';
import { openDrawer } from './production_drawer.js';
const vendorLinkUrl = (tok) => `${location.origin}/priorities/vendor/${tok}`;
export { mondayOf, addDays, today, VENDOR_FALLBACK, formatLabel, resultText };           // used by priorities_assign.js (the Assign to vendor dialog on /priorities)

function historyCell(r) {
  const ev = r.history || [];
  if (!ev.length) return null;
  return h('details', { class: 'prod-hist', 'data-testid': 'prod-history' }, h('summary', null, `History (${ev.length})`),
    h('ol', { class: 'prod-histlist' }, ev.map((e) => { const l = eventLine(e); return h('li', { 'data-testid': 'prod-event', 'data-kind': e.kind },
      h('span', { class: 'prod-hwhen' }, l.when), ' ', h('strong', null, l.who), ': ', l.what, l.move ? h('span', { class: 'pri-sub' }, ' · ' + l.move) : null); })));
}


export async function productionPage(ctx) {
  showControls(false);
  const VIEWS = [['board', 'Board'], ['list', 'List'], ['plan', 'Plan']];
  const st = { view: VIEWS.some(([k]) => k === ctx.params.view) ? ctx.params.view : 'board', week: ctx.params.week || '', vendor: ctx.params.vendor || '', product: ctx.params.product || '', closed: ctx.params.closed === '1',
    stage: ctx.params.stage || '', overdue: ctx.params.overdue === '1', q: ctx.params.q || '', group: ctx.params.group === 'vendor' ? 'vendor' : '', card: ctx.params.card || '' };
  let data = { rows: [], vendors: [], weeks: [] };          // the List payload (GET /api/priorities/assignments); also the source of vendors, weeks and formats for every view
  let board = null;                                           // the Board payload (GET /api/priorities/board)
  let meta = null;                                            // vendors, weeks, formats (and the launch flag) when the page started on the board
  let lastErr = '';
  let drawer = null;
  let focusId = null;                                         // card to focus again after a keyboard move redraws the board
  const msg = h('p', { class: 'prod-msg', role: 'status', 'data-testid': 'prod-msg' });
  const say = (t, bad) => { msg.textContent = t || ''; msg.classList.toggle('pri-err', !!bad); };
  const body = h('div', { class: 'prod-body' });
  const viewHost = h('div', { class: 'prod-view', 'data-testid': 'prod-view-host' });
  const openIds = new Set();                                   // rows whose details are open: kept across redraws (the payload is cached, so no new request)
  const root = h('div', { class: 'page pri-page prod-page', 'data-testid': 'prod-page', 'data-view': st.view });
  const info = () => (meta || data || {});
  const vendorNames = () => (board && board.vendors && board.vendors.length ? board.vendors : info().vendors && info().vendors.length ? info().vendors : VENDOR_FALLBACK);
  const formatsList = () => info().formats || data.formats || [];
  const weeksList = () => info().weeks || data.weeks || [];
  const abortOk = (e) => { if (e && e.name === 'AbortError') throw e; };

  // ---- data
  const boardCards = () => (board ? board.columns.flatMap((c) => c.cards || []).concat(board.dropped || []) : []);
  const findCard = (id) => { const n = Number(id); return boardCards().find((c) => c.id === n) || (data.rows || []).find((c) => c.id === n) || null; };
  const productOk = (r) => !st.product || r.product === st.product;
  async function ensureMeta() {
    if (st.view === 'list' || (meta && (meta.vendors || []).length)) return;
    try { meta = await api('/api/priorities/assignments', {}, { signal: ctx.signal }); } catch (e) { abortOk(e); meta = meta || { vendors: [], weeks: [], formats: [] }; }
  }
  async function load() {
    if (st.view === 'list') {
      data = await api('/api/priorities/assignments', { week: st.week, vendor: st.vendor, include_closed: st.closed ? 1 : '' }, { signal: ctx.signal });
      meta = null;
    } else {
      const plan = st.view === 'plan';
      board = await api('/api/priorities/board', { week: plan ? '' : st.week, vendor: plan ? '' : st.vendor, product: plan ? '' : st.product, closed: !plan && st.closed ? 1 : '' }, { signal: ctx.signal });
      await ensureMeta();
    }
  }
  async function reload() {
    try { await load(); drawView(); if (drawer) drawer.refresh(); return true; } catch (e) { abortOk(e); say(e.message || 'Could not load.', true); return false; }
  }
  const sync = () => navigate(href('/priorities/production', { view: st.view === 'board' ? '' : st.view, week: st.week, vendor: st.vendor, product: st.product, closed: st.closed ? 1 : '', stage: st.stage, overdue: st.overdue ? 1 : '', q: st.q, group: st.group, card: st.card }), { replace: true, render: false });
  const refresh = () => { sync(); drawChrome(); reload(); };

  // ---- saving
  const patch = async (id, b, done) => {
    lastErr = '';
    try { await api('/api/priorities/assignments/' + id, {}, { method: 'PATCH', body: b }); await load(); drawView(); if (drawer) drawer.refresh(); if (done) { say(done); toast(done); } return true; }
    catch (e) { abortOk(e); lastErr = e.message || 'Could not save.'; say(lastErr, true); toast(lastErr, true); return false; }
  };

  // Move a card: optimistic on the board, undone with a plain-English message when the server refuses
  function optimistic(card, to, beforeId, afterId) {
    for (const c of board.columns) c.cards = (c.cards || []).filter((x) => x.id !== card.id);
    const target = board.columns.find((c) => c.status === to);
    if (!target) return;
    card.status = to; card.status_label = STATUS_LABEL[to] || to;
    const cards = target.cards = target.cards || [];
    let at = cards.length;
    if (afterId) { const i = cards.findIndex((x) => x.id === Number(afterId)); if (i >= 0) at = i + 1; } else if (beforeId) { const i = cards.findIndex((x) => x.id === Number(beforeId)); if (i >= 0) at = i; }
    cards.splice(at, 0, card);
    for (const c of board.columns) c.count = (c.cards || []).length;
  }
  async function moveCard(card, to, o = {}) {
    lastErr = '';
    const onBoard = st.view !== 'list' && board && boardCards().some((c) => c.id === card.id);
    const snap = onBoard ? JSON.stringify({ columns: board.columns, dropped: board.dropped }) : null;
    if (onBoard) { optimistic(card, to, o.beforeId, o.afterId); drawView(); }
    try {
      await api(`/api/priorities/assignments/${card.id}/move`, {}, { method: 'POST', body: { status: to, ...(o.beforeId ? { before_id: Number(o.beforeId) } : {}), ...(o.afterId ? { after_id: Number(o.afterId) } : {}), ...(o.comment ? { comment: o.comment } : {}) } });
      await load(); drawView(); if (drawer) drawer.refresh();
      toast(`Moved to ${STATUS_LABEL[to] || to}.`);
      focusId = null;
      return true;
    } catch (e) {
      abortOk(e);
      lastErr = e.message || 'Could not move the card.';
      if (onBoard) { const s = JSON.parse(snap); board.columns = s.columns; board.dropped = s.dropped; drawView(); }
      say(lastErr, true); toast(lastErr, true);
      focusId = null;
      return false;
    }
  }
  async function askChanges(card, to) {
    const res = await askDialog({ title: 'Request changes', note: `${card.vendor} sees this comment and the card goes back to In production.`, fields: [{ key: 'comment', label: 'What needs to change', type: 'textarea', value: '', placeholder: 'For example: the hook is too slow, cut the first 3 seconds', required: true }], okLabel: 'Request changes' });
    return res ? res.comment : null;
  }
  // Run one of the card's next actions (buttons on the card and in the drawer)
  async function run(card, a) {
    if (a.restore) return patch(card.id, { dropped: false }, 'Restored.');
    if (a.launched) return patch(card.id, { launched: true }, 'Marked launched.');
    if (a.comment) { const c = await askChanges(card, a.to); if (c === null) return false; return moveCard(card, a.to, { comment: c }); }
    return moveCard(card, a.to);
  }
  async function moveTo(card, to) {
    if (statusOf(card) === 'in_review' && to === 'in_production') return run(card, { to, comment: true });
    return moveCard(card, to);
  }
  async function save(card, b) { return patch(card.id, b, 'Saved.'); }
  async function comment(card, text) {
    lastErr = '';
    try { await api(`/api/priorities/assignments/${card.id}/comments`, {}, { method: 'POST', body: { body: text } }); const c = findCard(card.id); if (c) c.comment_count = (c.comment_count || 0) + 1; await load(); drawView(); return true; }
    catch (e) { abortOk(e); lastErr = e.message || 'Could not post the comment.'; return false; }
  }
  const openCard = (card, tab) => {
    if (drawer) drawer.close();
    st.card = String(card.id); sync();
    drawer = openDrawer({ id: card.id, tab: tab || 'details', signal: ctx.signal, getCard: () => findCard(card.id), onClose: () => { drawer = null; st.card = ''; sync(); },
      ops: { run, save, comment, reload, vendors: vendorNames, formats: formatsList, lastError: () => lastErr } });
  };
  const ops = { open: openCard, run, moveTo };

  // Launch to Meta controls (priorities_launch.js): only fetched and drawn when the API says launching is enabled for this login; otherwise the page is unchanged. They decorate List rows.
  let launchMod = null;
  const launchControls = async () => {
    const lf = data && data.launch;
    if (st.view !== 'list' || !lf || lf.enabled !== true) return;
    try { launchMod = launchMod || await import('./priorities_launch.js'); launchMod.decorate(root, lf, ctx); } catch (e) { /* the launch controls are optional: the table works without them */ }
  };

  async function editLink(r, field, label) {
    const res = await askDialog({ title: `${r[field] ? 'Change' : 'Add'} ${label.toLowerCase()} link`, note: `${r.vendor}, ${weekLabel(r.for_week).toLowerCase()}: ${r.cluster} / ${r.icp} / ${r.pain_point}`,
      fields: [{ key: 'url', label: label + ' link', value: r[field] || '', placeholder: 'https://' }], okLabel: 'Save link' });
    if (res) patch(r.id, { [field]: res.url || null }, res.url ? label + ' link saved.' : label + ' link removed.');
  }
  async function addConceptId(r) {
    const res = await askDialog({ title: 'Add Concept ID', note: 'The Concept ID of the launched ad in the Creative Tracker.', fields: [{ key: 'cid', label: 'Concept ID', value: '', placeholder: 'Lonvera-VitalBull-Static-5.4' }], okLabel: 'Add' });
    if (res && res.cid) patch(r.id, { tracker_concept_ids: [...(r.tracker_concept_ids || []), res.cid] }, 'Concept ID added.');
  }
  async function change(r) {
    const mon = mondayOf(today());
    const weeks = [-7, 0, 7, 14, 21, 28].map((n) => addDays(mon, n));
    if (!weeks.includes(r.for_week)) weeks.push(r.for_week);
    weeks.sort();
    const res = await askDialog({ title: 'Change vendor or week', note: `${r.cluster} / ${r.icp} / ${r.pain_point}`, okLabel: 'Save',
      fields: [{ key: 'vendor', label: 'Vendor', type: 'select', value: r.vendor, options: vendorNames().map((v) => [v, v]) }, { key: 'for_week', label: 'Week of', type: 'select', value: r.for_week, options: weeks.map((w) => [w, weekLabel(w)]) }] });
    if (res) patch(r.id, { vendor: res.vendor, for_week: res.for_week }, 'Saved.');
  }
  // the Format cell: a select that saves on change (blank = no format chosen); a format retired since it was chosen stays listed so the cell keeps showing it
  function formatCell(r) {
    const list = (data.formats || []).slice();
    const cur = rowFormat(r);
    if (cur && !list.some((f) => f.code === cur.code)) list.push(cur);
    const sel = h('select', { class: 'pri-fselect prod-fmt', 'data-testid': 'prod-format', 'aria-label': 'Format', disabled: r.stage === 'dropped' || !list.length, onchange: async (e) => { const v = e.target.value; sel.disabled = true; await patch(r.id, { format_code: v || null }, v ? 'Format saved.' : 'Format removed.'); sel.disabled = false; sel.value = r.format_code || ''; } },
      h('option', { value: '', selected: !cur }, 'No format chosen'), list.map((f) => h('option', { value: f.code, selected: !!cur && f.code === cur.code }, formatLabel(f))));
    return h('td', { class: 'prod-c prod-c-fmt' }, sel);
  }
  async function drop(r) {
    const res = await askDialog({ title: 'Drop this assignment?', note: `${r.vendor}, ${weekLabel(r.for_week).toLowerCase()}: ${r.cluster} / ${r.icp} / ${r.pain_point}. It moves to Dropped (shown under Show launched and dropped).`, okLabel: 'Drop', danger: true });
    if (res) patch(r.id, { dropped: true }, 'Dropped.');
  }
  async function vendorLink(vendor, reset) {
    if (reset) {
      const ok = await askDialog({ title: `Reset the link for ${vendor}?`, note: 'The old link stops working. Send the new one to the vendor.', okLabel: 'Reset link', danger: true });
      if (!ok) return;
    }
    try {
      const r = await api(reset ? `/api/priorities/vendor-links/${encodeURIComponent(vendor)}/reset` : '/api/priorities/vendor-links', {}, { method: 'POST', body: reset ? {} : { vendor } });
      const url = vendorLinkUrl(r.token);
      let copied = false;
      try { await navigator.clipboard.writeText(url); copied = true; } catch (e) { /* clipboard blocked: the link is shown below to copy by hand */ }
      msg.classList.remove('pri-err');
      msg.replaceChildren(`${reset ? 'New link' : 'Link'} for ${vendor}${copied ? ' copied' : ''}: `, h('span', { class: 'prod-url', 'data-testid': 'prod-vendor-url' }, url));
    } catch (e) { if (e && e.name === 'AbortError') throw e; say(e.message || 'Could not get the link.', true); }
  }

  const linkCell = (r, field, label) => h('td', { class: 'prod-c prod-c-link' },
    r[field] ? h('div', null, link(r[field], short(r[field]), { 'data-testid': 'prod-' + field })) : null,
    h('button', { type: 'button', class: 'btn prod-small', 'data-testid': 'prod-edit-' + field, onclick: () => editLink(r, field, label) }, r[field] ? 'Change' : 'Add link'));

  function adsCell(r) {
    const ids = r.tracker_concept_ids || [];
    const byId = new Map((r.ads || []).map((a) => [a.concept_id, a]));
    return h('td', { class: 'prod-c prod-c-made', 'data-testid': 'prod-made' },
      ids.map((id) => { const a = byId.get(id); return h('div', null, a && a.ad_url ? link(a.ad_url, id, { 'data-testid': 'prod-cid' }) : h('span', { 'data-testid': 'prod-cid' }, id)); }),
      (r.suggested_concept_ids || []).filter((id) => !ids.includes(id)).map((id) => h('div', null, h('button', { type: 'button', class: 'btn prod-small', 'data-testid': 'prod-suggest', onclick: () => patch(r.id, { tracker_concept_ids: [...ids, id] }, 'Concept ID linked.') }, 'Link ' + id))),
      r.stage === 'dropped' ? null : h('button', { type: 'button', class: 'btn prod-small', 'data-testid': 'prod-add-cid', onclick: () => addConceptId(r) }, 'Add Concept ID'));
  }

  function actions(r) {
    if (r.stage === 'dropped') return h('td', { class: 'prod-c prod-acts' }, h('button', { type: 'button', class: 'btn prod-small', 'data-testid': 'prod-open-card', onclick: () => openCard(r) }, 'Open card'), h('button', { type: 'button', class: 'btn prod-small', 'data-testid': 'prod-restore', onclick: () => patch(r.id, { dropped: false }, 'Restored.') }, 'Restore'));
    const b = [h('button', { type: 'button', class: 'btn prod-small', 'data-testid': 'prod-open-card', onclick: () => openCard(r) }, 'Open card')];
    if (r.creative_url && r.stage !== 'ready' && r.stage !== 'launched') b.push(h('button', { type: 'button', class: 'btn prod-small', 'data-testid': 'prod-ready', onclick: () => patch(r.id, { ready: true }, 'Marked ready to launch.') }, 'Ready to launch'));
    if (r.stage !== 'launched') b.push(h('button', { type: 'button', class: 'btn prod-small', 'data-testid': 'prod-launched', onclick: () => patch(r.id, { launched: true }, 'Marked launched.') }, 'Mark launched'));
    // Less common actions sit behind "More" so each row shows one or two buttons, not four.
    b.push(h('details', { class: 'prod-more', 'data-testid': 'prod-more' }, h('summary', null, 'More'),
      h('button', { type: 'button', class: 'btn prod-small', 'data-testid': 'prod-change', onclick: () => change(r) }, 'Change vendor or week'),
      h('button', { type: 'button', class: 'btn prod-small', 'data-testid': 'prod-drop', onclick: () => drop(r) }, 'Drop')));
    return h('td', { class: 'prod-c prod-acts' }, b);
  }

  const dueCell = (r) => { const d = dueInfo(r); return h('td', { class: 'prod-c prod-nowrap', 'data-testid': 'prod-due' }, d ? h('span', { class: 'prod-chip prod-chip-due' + (d.tone ? ' prod-due-' + d.tone : '') }, d.text) : '–'); };
  const readyCell = (r) => {
    const on = r.stage === 'ready' || r.stage === 'launched' || !!r.ready_at;
    const t = on ? 'Ready' + (r.ready_at ? ' ' + md(r.ready_at) : '') : (r.creative_url ? 'Not yet' : 'Needs creative');
    return h('td', { class: 'prod-c prod-nowrap', 'data-testid': 'prod-ready-state', 'data-ready': on ? '1' : '0' }, on ? h('span', { class: 'prod-yes' }, t) : h('span', { class: 'prod-no' }, t));
  };
  const adsOf = (r) => (r.ads || []).filter((a) => a.launch_date);
  const spendOf = (r) => { const a = adsOf(r); return a.length ? a.reduce((s, x) => s + (isNum(x.spend) ? x.spend : 0), 0) : null; };
  const returnOf = (r) => { const a = adsOf(r); const sp = spendOf(r); if (!a.length || !(sp > 0)) return null; return a.reduce((s, x) => s + (isNum(x.spend) && isNum(x.roas) ? x.spend * x.roas : 0), 0) / sp; };

  function row(r) {
    const host = h('div', { class: 'prod-xd', 'data-testid': 'prod-detail' });
    const wrap = h('tr', { class: 'prod-detailrow', 'data-testid': 'prod-detail-row', 'data-id': r.id }, h('td', { colspan: '16', class: 'prod-xcell' }, host));
    const x = expander({ key: 'a' + r.id, fetchDetail: () => api(`/api/priorities/assignments/${r.id}/detail`, {}, { signal: ctx.signal }), host, wrap, startOpen: openIds.has(r.id), onToggle: (o) => { if (o) openIds.add(r.id); else openIds.delete(r.id); } });
    const main = h('tr', { class: 'prod-row', 'data-testid': 'prod-row', 'data-id': r.id, 'data-stage': r.stage },
      h('td', { class: 'prod-c prod-nowrap', 'data-testid': 'prod-week' }, weekLabel(r.for_week).replace('Week of ', '')),
      h('td', { class: 'prod-c', 'data-testid': 'prod-vendor' }, r.vendor),
      h('td', { class: 'prod-c', 'data-testid': 'prod-product' }, productName(r.product)),
      h('td', { class: 'prod-c prod-concept', 'data-testid': 'prod-concept' }, h('strong', null, x.nameButton(r.pain_point || r.cluster)), h('div', { class: 'pri-sub' }, [r.cluster, r.icp].filter(Boolean).join(' / ')), r.note ? h('div', { class: 'pri-sub' }, 'Note: ' + r.note) : null, r.vendor_note ? h('div', { class: 'pri-sub', 'data-testid': 'prod-vnote' }, 'Vendor note: ' + r.vendor_note) : null, h('div', null, x.button()), historyCell(r)),
      linkCell(r, 'brief_url', 'Brief'), linkCell(r, 'creative_url', 'Creative'), formatCell(r),
      h('td', { class: 'prod-c' }, h('span', { class: 'prod-pill prod-st-' + r.stage, 'data-testid': 'prod-stage' }, stageLabel(r)), h('div', { class: 'pri-sub prod-result', 'data-testid': 'prod-result' }, resultText(r))),
      h('td', { class: 'prod-c prod-nowrap' }, h('span', { class: 'prod-pill prod-ps-' + statusOf(r), 'data-testid': 'prod-status' }, STATUS_LABEL[statusOf(r)] || statusOf(r))),
      dueCell(r),
      h('td', { class: 'prod-c prod-nowrap', 'data-testid': 'prod-priority' }, r.priority === 'high' ? h('span', { class: 'prod-chip prod-chip-high' }, 'High') : 'Normal'),
      readyCell(r),
      adsCell(r),
      h('td', { class: 'prod-c prod-num', 'data-testid': 'prod-spend' }, spendOf(r) === null ? '–' : fmt.money(spendOf(r))),
      h('td', { class: 'prod-c prod-num', 'data-testid': 'prod-return' }, returnOf(r) === null ? '–' : fmt.roas(returnOf(r))),
      actions(r));
    return [main, wrap];
  }


  // ---- drawing
  function drawChrome() {
    const weeks = [...new Set([...weeksList(), ...(st.week ? [st.week] : [])])].sort().reverse();
    const list = st.view === 'list';
    const plan = st.view === 'plan';
    const all = data.rows || [];
    const keys = Object.keys(STAGES).filter((k) => st.closed || (k !== 'launched' && k !== 'dropped'));
    const count = (k) => all.filter((r) => r.stage === k).length;
    const controls = filterRow(
      seg({ name: 'View', options: VIEWS.map(([k, l]) => [k, l, `${l} view`, 'prod-view-' + k]), value: st.view, onChange: (v) => { st.view = v; root.dataset.view = v; board = null; sync(); drawChrome(); reload(); }, testid: 'prod-view' }),
      plan ? null : selectCtl({ name: 'Week', options: [['', 'All weeks'], ...weeks.map((w) => [w, weekLabel(w)])], value: st.week, onChange: (v) => { st.week = v; refresh(); }, testid: 'prod-week' }),
      plan ? null : selectCtl({ name: 'Vendor', options: [['', 'All vendors'], ...vendorNames().map((v) => [v, v])], value: st.vendor, onChange: (v) => { st.vendor = v; refresh(); }, testid: 'prod-vendor' }),
      plan ? null : selectCtl({ name: 'Product', options: [['', 'All products'], ...Object.entries({ 'vita-bull': 'Vita Bull', 'beef-liver': 'Beef Liver', boneflex: 'Boneflex' })], value: st.product, onChange: (v) => { st.product = v; sync(); if (list) drawView(); else reload(); drawChrome(); }, testid: 'prod-product-filter' }),
      plan ? null : checkCtl({ label: 'Overdue only', checked: st.overdue, onChange: (v) => { st.overdue = v; sync(); drawView(); }, testid: 'prod-overdue-only' }),
      plan ? null : searchCtl({ name: 'Search', value: st.q, placeholder: 'Search concepts', onInput: (v) => { st.q = v.trim(); sync(); drawView(); }, testid: 'prod-search' }),
      plan ? null : checkCtl({ label: 'Show launched and dropped', checked: st.closed, onChange: (v) => { st.closed = v; if (!v && (st.stage === 'launched' || st.stage === 'dropped')) st.stage = ''; refresh(); }, testid: 'prod-closed' }),
      st.view === 'board' ? checkCtl({ label: 'Group by vendor', checked: st.group === 'vendor', onChange: (v) => { st.group = v ? 'vendor' : ''; sync(); drawView(); }, testid: 'prod-board-group' }) : null,
      list ? seg({ name: 'Status', options: [['', `All ${all.length}`, 'Every concept in the list', 'prod-stagebtn-all'], ...keys.map((k) => [k, `${STAGES[k]} ${count(k)}`, `Only concepts at: ${STAGES[k]}`, 'prod-stagebtn-' + k])], value: st.stage, onChange: (v) => { st.stage = v; sync(); drawChrome(); drawView(); }, testid: 'prod-stage-filter' }) : null);
    const links = h('details', { class: 'prod-links', 'data-testid': 'prod-links' }, h('summary', null, `Vendor links (${vendorNames().length})`),
      h('p', { class: 'pri-sub' }, 'Each vendor gets one private page that lists only their concepts. Copy the link to send it. Reset makes the old link stop working.'),
      h('div', { class: 'prod-linkrow' }, vendorNames().map((v) => h('span', { class: 'prod-vl', 'data-vendor': v },
        h('span', { class: 'prod-vname' }, v),
        h('button', { type: 'button', class: 'btn prod-small', 'data-testid': 'prod-copy-link', 'aria-label': 'Copy link for ' + v, onclick: () => vendorLink(v, false) }, 'Copy link'),
        h('button', { type: 'button', class: 'btn prod-small', 'data-testid': 'prod-reset-link', 'aria-label': 'Reset link for ' + v, onclick: () => vendorLink(v, true) }, 'Reset link')))));
    body.replaceChildren(
      pageHeader({ title: 'Production', description: 'Concepts given to vendors to make, by week. Spend and return come from the Creative Tracker once an ad is launched.', testid: 'prod-head',
        actions: [h('a', { class: 'btn small', href: href('/priorities', {}), 'data-testid': 'prod-back' }, 'Back to Priorities')] }),
      controls, links, msg, viewHost);
  }

  function drawView() {
    if (st.view === 'list') drawList();
    else if (st.view === 'plan') drawPlan();
    else drawBoard();
    if (focusId) { const el = viewHost.querySelector(`.prod-board-card[data-id="${focusId}"]`); if (el) el.focus(); }
  }

  function onBoardDrop({ card, list, before, after, via }) {
    const c = findCard(card.dataset.id);
    const to = list.dataset.status;
    if (via === 'keyboard') focusId = card.dataset.id;
    if (!c) { drawView(); focusId = null; return; }
    if (to === 'launched') { toast('Cards are marked launched with the Mark launched button, not by dragging.', true); drawView(); focusId = null; return; }
    const nb = { beforeId: before ? before.dataset.id : null, afterId: after ? after.dataset.id : null };
    if (statusOf(c) === 'in_review' && to === 'in_production') {
      askChanges(c, to).then((txt) => { if (txt === null) { drawView(); focusId = null; return; } moveCard(c, to, { ...nb, comment: txt }); });
      return;
    }
    moveCard(c, to, nb);
  }

  function drawBoard() {
    if (!board) { viewHost.replaceChildren(h('p', { role: 'status' }, 'Loading…')); return; }
    const columns = board.columns.map((c) => ({ ...c, cards: (c.cards || []).filter(productOk) }));
    const dropped = (board.dropped || []).filter(productOk);
    const cards = columns.flatMap((c) => c.cards);
    const week = st.week || mondayOf(today());
    const bars = loadBars(board.capacity, vendorNames(), week, cards);
    const empty = !cards.length && !dropped.length;
    const stageNote = st.stage ? h('p', { class: 'prod-stagenote', 'data-testid': 'prod-board-stagenote' }, `Showing only ${STATUS_LABEL[st.stage === 'creative_in' ? 'in_review' : st.stage] || st.stage}. `, h('button', { type: 'button', class: 'btn prod-small', 'data-testid': 'prod-board-showall', onclick: () => { st.stage = ''; sync(); drawView(); } }, 'Show all columns')) : null;
    viewHost.replaceChildren(...[
      bars ? h('div', { class: 'prod-loadwrap' }, h('p', { class: 'prod-loadtitle' }, `Open cards per vendor, ${weekLabel(week).toLowerCase()}`), bars) : null,
      stageNote,
      empty ? h('p', { 'data-testid': 'prod-empty' }, 'Nothing here yet. On Priorities, tick concepts and click Assign to vendor.') : null,
      boardView({ columns, dropped, st, vendors: vendorNames(), capacity: board.capacity, ops, onDrop: onBoardDrop, allCards: cards }).el].filter(Boolean));
  }

  function drawPlan() {
    if (!board) { viewHost.replaceChildren(h('p', { role: 'status' }, 'Loading…')); return; }
    const cards = boardCards().filter((c) => c.stage !== 'dropped');
    const cap = {};
    for (const c of board.capacity || []) if (!(c.vendor in cap)) cap[c.vendor] = c.per_week;
    viewHost.replaceChildren(planView({ cards, vendors: vendorNames(), capacity: cap, start: st.week, ops,
      onShift: (w) => { st.week = w; sync(); drawView(); },
      onCapacity: async (v, n) => {
        try { await api('/api/priorities/vendor-capacity', {}, { method: 'PUT', body: { vendor: v, per_week: n } }); toast(`${v}: ${n} cards per week.`); await reload(); }
        catch (e) { abortOk(e); toast(e.message || 'Could not save the capacity.', true); drawView(); }
      },
      onReassign: async (card, vendor, week) => {
        const c = findCard(card.dataset.id);
        if (!c || (c.vendor === vendor && c.for_week === week)) { drawView(); return; }
        const was = { vendor: c.vendor, for_week: c.for_week };
        c.vendor = vendor; c.for_week = week; drawView();
        try {
          await api('/api/priorities/assignments/' + c.id, {}, { method: 'PATCH', body: { vendor, for_week: week } });
          await load();
          const per = (board.capacity || []).find((x) => x.vendor === vendor);
          const n = boardCards().filter((x) => x.vendor === vendor && x.for_week === week && statusOf(x) !== 'launched').length;
          drawView();
          if (per && n > per.per_week) toast(`${vendor} now has ${n} cards for ${weekLabel(week).toLowerCase()}, over the ${per.per_week} a week capacity.`);
          else toast(`Moved to ${vendor}, ${weekLabel(week).toLowerCase()}.`);
        } catch (e) { abortOk(e); Object.assign(c, was); drawView(); toast(e.message || 'Could not reassign the card.', true); }
      } }).el);
  }

  function drawList() {
    const all = (data.rows || []).filter(productOk).filter((r) => !st.overdue || r.overdue).filter((r) => !st.q || [r.pain_point, r.cluster, r.icp, r.vendor, r.note, r.vendor_note].filter(Boolean).join(' ').toLowerCase().includes(st.q.toLowerCase()));
    const shown = st.stage ? all.filter((r) => r.stage === st.stage || statusOf(r) === st.stage) : all;
    const heads = [['Week of'], ['Vendor'], ['Product'], ['Concept'], ['Brief'], ['Creative'], ['Format', 'Optional creative format chosen for this concept'], ['Stage'], ['Status', 'Where the card sits on the Board'], ['Due'], ['Priority'], ['Ready', 'Ready to launch: creative delivered and checked'], ['Ads'], ['Spend', 'Total ad spend on the launched ads'], ['Return', 'Return on ad spend: revenue per dollar spent'], ['']];
    viewHost.replaceChildren(shown.length
      ? h('div', { class: 'prod-scroll', 'data-testid': 'prod-scroll' }, h('table', { class: 'pri-table prod-table', 'data-testid': 'prod-table' },
        h('thead', null, h('tr', null, heads.map(([x, t]) => h('th', { scope: 'col', title: t || null }, x)))), h('tbody', null, shown.flatMap(row))))
      : h('p', { 'data-testid': 'prod-empty' }, (data.rows || []).length ? 'No concepts match.' : 'Nothing here yet. On Priorities, tick concepts and click Assign to vendor.'));
    launchControls();
  }

  root.append(body);
  await load();
  drawChrome();
  drawView();
  ctx.mount(root);
  document.title = 'Production · Lonvera';
  if (st.card) { const c = findCard(st.card); if (c) openCard(c); else { st.card = ''; sync(); } }
}

export { vendorPage } from './priorities_vendor.js';
