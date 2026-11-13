// Production (/priorities/production) and the vendor page (/priorities/vendor/<token>). One plain table of assignments (a concept given to a vendor to make for a week), links edited in place.
// Everything the user typed or the API returned goes in through textContent / h(); links are built with h('a', {href, target: '_blank', rel: 'noopener noreferrer'}), never innerHTML.
// Class names and test ids start with `prod-` (never ad-, ads-, sponsor, banner, promo: ad blockers hide those).
import { h, fmt, isNum, href, navigate } from '../util.js';
import { api } from '../api.js';
import { showControls } from '../controls.js';
import { expander } from './priorities_panel.js';
import { pageHeader } from '../ui/pagehead.js';
import { filterRow, seg, selectCtl, checkCtl } from '../ui/pagefilters.js';

import { STAGES, VENDOR_FALLBACK, md, weekLabel, productName, stageLabel, today, mondayOf, addDays, formatLabel, rowFormat, resultText, link, short } from './production_common.js';
const vendorLinkUrl = (tok) => `${location.origin}/priorities/vendor/${tok}`;
export { mondayOf, addDays, today, VENDOR_FALLBACK, formatLabel, resultText };           // used by priorities_assign.js (the Assign to vendor dialog on /priorities)

// History of an assignment: "11/10 2:14 PM  Josh: Brief sent to Creative delivered". Who did it comes from the sign-in name; a vendor link page shows as "Madre (vendor link)"; older rows have no name.
const EVENT_TEXT = { assigned: 'Assigned to production', brief_added: 'Brief link added', brief_removed: 'Brief link removed', creative_added: 'Creative link added', creative_removed: 'Creative link removed', ready: 'Marked ready to launch', ready_cleared: 'Ready to launch removed',
  launched: 'Marked launched', launched_cleared: 'Launched removed', dropped: 'Dropped', restored: 'Restored', vendor: 'Vendor changed', week: 'Week changed', format: 'Format changed' };
const actorName = (a) => (!a ? 'Not recorded' : a.startsWith('vendor:') ? a.slice(7) + ' (vendor link)' : a);
const whenText = (iso) => { const d = new Date(iso); return isNaN(d) ? '' : `${d.getMonth() + 1}/${d.getDate()} ${d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}`; };
export function eventLine(e) {
  const what = EVENT_TEXT[e.kind] || e.kind;
  const move = e.stage_before && e.stage_after && e.stage_before !== e.stage_after ? `Stage: ${STAGES[e.stage_before] || e.stage_before} to ${STAGES[e.stage_after] || e.stage_after}` : '';
  return { when: whenText(e.at), who: actorName(e.actor), what: what + (e.detail ? ` (${e.detail})` : ''), move };
}
function historyCell(r) {
  const ev = r.history || [];
  if (!ev.length) return null;
  return h('details', { class: 'prod-hist', 'data-testid': 'prod-history' }, h('summary', null, `History (${ev.length})`),
    h('ol', { class: 'prod-histlist' }, ev.map((e) => { const l = eventLine(e); return h('li', { 'data-testid': 'prod-event', 'data-kind': e.kind },
      h('span', { class: 'prod-hwhen' }, l.when), ' ', h('strong', null, l.who), ': ', l.what, l.move ? h('span', { class: 'pri-sub' }, ' · ' + l.move) : null); })));
}


// One small modal: optional note, fields [{key, label, type: 'text'|'select', value, options: [[value, label]], placeholder}], resolves {key: value} or null. No fields = a plain confirm.
function askDialog({ title, note = null, fields = [], okLabel = 'Save', danger = false }) {
  return new Promise((resolve) => {
    const inputs = fields.map((f) => {
      const el = f.type === 'select'
        ? h('select', { class: 'pri-name', 'data-testid': 'prod-field-' + f.key, 'aria-label': f.label }, f.options.map(([v, l]) => h('option', { value: v, selected: v === f.value }, l)))
        : h('input', { type: 'text', class: 'pri-name', 'data-testid': 'prod-field-' + f.key, 'aria-label': f.label, placeholder: f.placeholder || '', value: f.value || '', maxlength: '2000' });
      return [f, el];
    });
    const cancel = h('button', { type: 'button', class: 'btn', 'data-testid': 'prod-dialog-cancel' }, 'Cancel');
    const form = h('form', { method: 'dialog', class: 'pri-dform', onsubmit: (e) => { e.preventDefault(); done(Object.fromEntries(inputs.map(([f, el]) => [f.key, el.value.trim()]))); } },
      h('h2', { class: 'pri-dtitle', id: 'prod-dtitle' }, title), note ? h('p', { class: 'pri-dnote', 'data-testid': 'prod-dialog-note' }, note) : null,
      inputs.map(([f, el]) => h('label', { class: 'pri-dlabel' }, h('span', { class: 'pri-k' }, f.label), el)),
      h('div', { class: 'pri-dbtns' }, cancel, h('button', { type: 'submit', class: 'btn primary' + (danger ? ' prod-danger' : ''), 'data-testid': 'prod-dialog-ok' }, okLabel)));
    const dlg = h('dialog', { class: 'pri-dialog', 'aria-labelledby': 'prod-dtitle', 'data-testid': 'prod-dialog' }, form);
    let finished = false;
    function done(v) { if (finished) return; finished = true; try { dlg.close(); } catch (e) { /* already closed */ } dlg.remove(); resolve(v); }
    cancel.addEventListener('click', () => done(null));
    dlg.addEventListener('cancel', (e) => { e.preventDefault(); done(null); });
    dlg.addEventListener('click', (e) => { if (e.target === dlg) done(null); });
    document.body.appendChild(dlg);
    if (typeof dlg.showModal === 'function') dlg.showModal(); else dlg.setAttribute('open', '');
    const first = inputs[0] && inputs[0][1];
    if (first) { first.focus(); if (first.select) first.select(); }
  });
}

// ---------------------------------------------------------------------------------------------------------------- Production page
export async function productionPage(ctx) {
  showControls(false);
  const st = { week: ctx.params.week || '', vendor: ctx.params.vendor || '', closed: ctx.params.closed === '1', stage: ctx.params.stage || '' };
  let data = { rows: [], vendors: [], weeks: [] };
  const msg = h('p', { class: 'prod-msg', role: 'status', 'data-testid': 'prod-msg' });
  const say = (t, bad) => { msg.textContent = t || ''; msg.classList.toggle('pri-err', !!bad); };
  const body = h('div', { class: 'prod-body' });
  const openIds = new Set();                                   // rows whose details are open: kept across redraws (the payload is cached, so no new request)
  const root = h('div', { class: 'page pri-page prod-page', 'data-testid': 'prod-page' });

  const load = async () => {
    data = await api('/api/priorities/assignments', { week: st.week, vendor: st.vendor, include_closed: st.closed ? 1 : '' }, { signal: ctx.signal });
    draw();
    launchControls();
  };
  const patch = async (id, b, done) => {
    try { await api('/api/priorities/assignments/' + id, {}, { method: 'PATCH', body: b }); await load(); if (done) say(done); } catch (e) { if (e && e.name === 'AbortError') throw e; say(e.message || 'Could not save.', true); }
  };
  // Launch to Meta controls (priorities_launch.js): only fetched and drawn when the API says launching is enabled for this login; otherwise the page is unchanged.
  let launchMod = null;
  const launchControls = async () => {
    const lf = data && data.launch;
    if (!lf || lf.enabled !== true) return;
    try { launchMod = launchMod || await import('./priorities_launch.js'); launchMod.decorate(root, lf, ctx); } catch (e) { /* the launch controls are optional: the table works without them */ }
  };
  const vendorNames = () => (data.vendors && data.vendors.length ? data.vendors : VENDOR_FALLBACK);

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
    if (r.stage === 'dropped') return h('td', { class: 'prod-c prod-acts' }, h('button', { type: 'button', class: 'btn prod-small', 'data-testid': 'prod-restore', onclick: () => patch(r.id, { dropped: false }, 'Restored.') }, 'Restore'));
    const b = [];
    if (r.creative_url && r.stage !== 'ready' && r.stage !== 'launched') b.push(h('button', { type: 'button', class: 'btn prod-small', 'data-testid': 'prod-ready', onclick: () => patch(r.id, { ready: true }, 'Marked ready to launch.') }, 'Ready to launch'));
    if (r.stage !== 'launched') b.push(h('button', { type: 'button', class: 'btn prod-small', 'data-testid': 'prod-launched', onclick: () => patch(r.id, { launched: true }, 'Marked launched.') }, 'Mark launched'));
    // Less common actions sit behind "More" so each row shows one or two buttons, not four.
    b.push(h('details', { class: 'prod-more', 'data-testid': 'prod-more' }, h('summary', null, 'More'),
      h('button', { type: 'button', class: 'btn prod-small', 'data-testid': 'prod-change', onclick: () => change(r) }, 'Change vendor or week'),
      h('button', { type: 'button', class: 'btn prod-small', 'data-testid': 'prod-drop', onclick: () => drop(r) }, 'Drop')));
    return h('td', { class: 'prod-c prod-acts' }, b);
  }

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
    const wrap = h('tr', { class: 'prod-detailrow', 'data-testid': 'prod-detail-row', 'data-id': r.id }, h('td', { colspan: '13', class: 'prod-xcell' }, host));
    const x = expander({ key: 'a' + r.id, fetchDetail: () => api(`/api/priorities/assignments/${r.id}/detail`, {}, { signal: ctx.signal }), host, wrap, startOpen: openIds.has(r.id), onToggle: (o) => { if (o) openIds.add(r.id); else openIds.delete(r.id); } });
    const main = h('tr', { class: 'prod-row', 'data-testid': 'prod-row', 'data-id': r.id, 'data-stage': r.stage },
      h('td', { class: 'prod-c prod-nowrap', 'data-testid': 'prod-week' }, weekLabel(r.for_week).replace('Week of ', '')),
      h('td', { class: 'prod-c', 'data-testid': 'prod-vendor' }, r.vendor),
      h('td', { class: 'prod-c', 'data-testid': 'prod-product' }, productName(r.product)),
      h('td', { class: 'prod-c prod-concept', 'data-testid': 'prod-concept' }, h('strong', null, x.nameButton(r.pain_point || r.cluster)), h('div', { class: 'pri-sub' }, [r.cluster, r.icp].filter(Boolean).join(' / ')), r.note ? h('div', { class: 'pri-sub' }, 'Note: ' + r.note) : null, r.vendor_note ? h('div', { class: 'pri-sub', 'data-testid': 'prod-vnote' }, 'Vendor note: ' + r.vendor_note) : null, h('div', null, x.button()), historyCell(r)),
      linkCell(r, 'brief_url', 'Brief'), linkCell(r, 'creative_url', 'Creative'), formatCell(r),
      h('td', { class: 'prod-c' }, h('span', { class: 'prod-pill prod-st-' + r.stage, 'data-testid': 'prod-stage' }, stageLabel(r)), h('div', { class: 'pri-sub prod-result', 'data-testid': 'prod-result' }, resultText(r))),
      readyCell(r),
      adsCell(r),
      h('td', { class: 'prod-c prod-num', 'data-testid': 'prod-spend' }, spendOf(r) === null ? '–' : fmt.money(spendOf(r))),
      h('td', { class: 'prod-c prod-num', 'data-testid': 'prod-return' }, returnOf(r) === null ? '–' : fmt.roas(returnOf(r))),
      actions(r));
    return [main, wrap];
  }

  function draw() {
    const weeks = [...new Set([...(data.weeks || []), ...(st.week ? [st.week] : [])])].sort().reverse();
    const refresh = () => { navigate(href('/priorities/production', { week: st.week, vendor: st.vendor, closed: st.closed ? 1 : '', stage: st.stage }), { replace: true, render: false }); load().catch((e) => { if (!(e && e.name === 'AbortError')) say(e.message || 'Could not load.', true); }); };
    const all = data.rows || [];
    const keys = Object.keys(STAGES).filter((k) => st.closed || (k !== 'launched' && k !== 'dropped'));
    const count = (k) => all.filter((r) => r.stage === k).length;
    const shown = st.stage ? all.filter((r) => r.stage === st.stage) : all;
    const controls = filterRow(
      selectCtl({ name: 'Week', options: [['', 'All weeks'], ...weeks.map((w) => [w, weekLabel(w)])], value: st.week, onChange: (v) => { st.week = v; refresh(); }, testid: 'prod-week' }),
      selectCtl({ name: 'Vendor', options: [['', 'All vendors'], ...vendorNames().map((v) => [v, v])], value: st.vendor, onChange: (v) => { st.vendor = v; refresh(); }, testid: 'prod-vendor' }),
      checkCtl({ label: 'Show launched and dropped', checked: st.closed, onChange: (v) => { st.closed = v; if (!v && (st.stage === 'launched' || st.stage === 'dropped')) st.stage = ''; refresh(); }, testid: 'prod-closed' }),
      seg({ name: 'Status', options: [['', `All ${all.length}`, 'Every concept in the list', 'prod-stagebtn-all'], ...keys.map((k) => [k, `${STAGES[k]} ${count(k)}`, `Only concepts at: ${STAGES[k]}`, 'prod-stagebtn-' + k])], value: st.stage, onChange: (v) => { st.stage = v; refresh(); }, testid: 'prod-stage-filter' }));
    const links = h('details', { class: 'prod-links', 'data-testid': 'prod-links' }, h('summary', null, `Vendor links (${vendorNames().length})`),
      h('p', { class: 'pri-sub' }, 'Each vendor gets one private page that lists only their concepts. Copy the link to send it. Reset makes the old link stop working.'),
      h('div', { class: 'prod-linkrow' }, vendorNames().map((v) => h('span', { class: 'prod-vl', 'data-vendor': v },
        h('span', { class: 'prod-vname' }, v),
        h('button', { type: 'button', class: 'btn prod-small', 'data-testid': 'prod-copy-link', 'aria-label': 'Copy link for ' + v, onclick: () => vendorLink(v, false) }, 'Copy link'),
        h('button', { type: 'button', class: 'btn prod-small', 'data-testid': 'prod-reset-link', 'aria-label': 'Reset link for ' + v, onclick: () => vendorLink(v, true) }, 'Reset link')))));
    const heads = [['Week of'], ['Vendor'], ['Product'], ['Concept'], ['Brief'], ['Creative'], ['Format', 'Optional creative format chosen for this concept'], ['Stage'], ['Ready', 'Ready to launch: creative delivered and checked'], ['Ads'], ['Spend', 'Total ad spend on the launched ads'], ['Return', 'Return on ad spend: revenue per dollar spent'], ['']];
    const table = shown.length
      ? h('div', { class: 'prod-scroll', 'data-testid': 'prod-scroll' }, h('table', { class: 'pri-table prod-table', 'data-testid': 'prod-table' },
        h('thead', null, h('tr', null, heads.map(([x, t]) => h('th', { scope: 'col', title: t || null }, x)))), h('tbody', null, shown.flatMap(row))))
      : h('p', { 'data-testid': 'prod-empty' }, all.length ? 'No concepts at this status.' : 'Nothing here yet. On Priorities, tick concepts and click Assign to vendor.');
    body.replaceChildren(
      pageHeader({ title: 'Production', description: 'Concepts given to vendors to make, by week. Spend and return come from the Creative Tracker once an ad is launched.', testid: 'prod-head',
        actions: [h('a', { class: 'btn small', href: href('/priorities', {}), 'data-testid': 'prod-back' }, 'Back to Priorities')] }),
      controls, links, msg, table);
  }
  root.append(body);
  await load();
  ctx.mount(root);
  document.title = 'Production · Lonvera';
}

export { vendorPage } from './priorities_vendor.js';
