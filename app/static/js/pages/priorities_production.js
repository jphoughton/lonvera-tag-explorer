// Production (/priorities/production) and the vendor page (/priorities/vendor/<token>). One plain table of assignments (a concept given to a vendor to make for a week), links edited in place.
// Everything the user typed or the API returned goes in through textContent / h(); links are built with h('a', {href, target: '_blank', rel: 'noopener noreferrer'}), never innerHTML.
// Class names and test ids start with `prod-` (never ad-, ads-, sponsor, banner, promo: ad blockers hide those).
import { h, fmt, isNum, href, navigate } from '../util.js';
import { api } from '../api.js';
import { showControls } from '../controls.js';
import { expander } from './priorities_panel.js';

const STAGES = { assigned: 'Assigned', brief_sent: 'Brief sent', creative_in: 'Creative delivered', ready: 'Ready to launch', launched: 'Launched', dropped: 'Dropped' };
const VENDOR_FALLBACK = ['Madre', 'Victoria', 'TrybeDCShop', 'TrybeOrion', '10xROAS', 'Internal', 'PuredMedia', 'CreateMastery', 'OpenProgram'];
const PRODUCT_NAMES = { 'vita-bull': 'Vita Bull', 'beef-liver': 'Beef Liver', boneflex: 'Boneflex' };

const md = (s) => { const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(s || ''); return m ? `${+m[2]}/${+m[3]}` : (s || ''); };
const weekLabel = (s) => 'Week of ' + md(s);
const productName = (p) => PRODUCT_NAMES[p] || p || '';
const stageLabel = (r) => r.stage_label || STAGES[r.stage] || r.stage || '';
const today = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; };
const mondayOf = (iso) => { const [y, m, d] = iso.split('-').map(Number); const t = new Date(Date.UTC(y, m - 1, d)); t.setUTCDate(t.getUTCDate() - ((t.getUTCDay() + 6) % 7)); return t.toISOString().slice(0, 10); };
const addDays = (iso, n) => { const [y, m, d] = iso.split('-').map(Number); const t = new Date(Date.UTC(y, m - 1, d + n)); return t.toISOString().slice(0, 10); };
const vendorLinkUrl = (tok) => `${location.origin}/priorities/vendor/${tok}`;
export { mondayOf, addDays, today, VENDOR_FALLBACK };                       // used by priorities_assign.js (the Assign to vendor dialog on /priorities)

// "Launched 11/3, $1,240 spent, 1.80 return" from the linked ads (earliest launch date, total spend, spend-weighted return), or '' when nothing is launched
export function resultText(r) {
  const ads = (r.ads || []).filter((a) => a.launch_date);
  if (!ads.length) return '';
  const first = ads.map((a) => a.launch_date).sort()[0];
  const spend = ads.reduce((s, a) => s + (isNum(a.spend) ? a.spend : 0), 0);
  const val = ads.reduce((s, a) => s + (isNum(a.spend) && isNum(a.roas) ? a.spend * a.roas : 0), 0);
  const ret = spend > 0 ? val / spend : null;
  return `Launched ${md(first)}, ${fmt.money(spend)} spent` + (ret !== null ? `, ${fmt.roas(ret)} return` : '');
}

function link(url, text, attrs) {
  return h('a', { href: url, target: '_blank', rel: 'noopener noreferrer', ...(attrs || {}) }, text);
}
const short = (u) => { try { const x = new URL(u); return (x.host + x.pathname).replace(/\/$/, '').slice(0, 38) + ((x.host + x.pathname).length > 38 ? '…' : ''); } catch (e) { return u; } };

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
  const st = { week: ctx.params.week || '', vendor: ctx.params.vendor || '', closed: ctx.params.closed === '1' };
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

  const linkCell = (r, field, label) => h('td', { class: 'prod-c' },
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
    b.push(h('button', { type: 'button', class: 'btn prod-small', 'data-testid': 'prod-change', onclick: () => change(r) }, 'Change vendor or week'));
    b.push(h('button', { type: 'button', class: 'btn prod-small', 'data-testid': 'prod-drop', onclick: () => drop(r) }, 'Drop'));
    return h('td', { class: 'prod-c prod-acts' }, b);
  }

  function row(r) {
    const host = h('div', { class: 'prod-xd', 'data-testid': 'prod-detail' });
    const wrap = h('tr', { class: 'prod-detailrow', 'data-testid': 'prod-detail-row', 'data-id': r.id }, h('td', { colspan: '10', class: 'prod-xcell' }, host));
    const x = expander({ key: 'a' + r.id, fetchDetail: () => api(`/api/priorities/assignments/${r.id}/detail`, {}, { signal: ctx.signal }), host, wrap, startOpen: openIds.has(r.id), onToggle: (o) => { if (o) openIds.add(r.id); else openIds.delete(r.id); } });
    const main = h('tr', { class: 'prod-row', 'data-testid': 'prod-row', 'data-id': r.id, 'data-stage': r.stage },
      h('td', { class: 'prod-c prod-nowrap', 'data-testid': 'prod-week' }, weekLabel(r.for_week).replace('Week of ', '')),
      h('td', { class: 'prod-c', 'data-testid': 'prod-vendor' }, r.vendor),
      h('td', { class: 'prod-c', 'data-testid': 'prod-product' }, productName(r.product)),
      h('td', { class: 'prod-c prod-concept', 'data-testid': 'prod-concept' }, h('strong', null, x.nameButton(r.pain_point || r.cluster)), h('div', { class: 'pri-sub' }, [r.cluster, r.icp].filter(Boolean).join(' / ')), r.note ? h('div', { class: 'pri-sub' }, 'Note: ' + r.note) : null, r.vendor_note ? h('div', { class: 'pri-sub', 'data-testid': 'prod-vnote' }, 'Vendor note: ' + r.vendor_note) : null, h('div', null, x.button())),
      linkCell(r, 'brief_url', 'Brief'), linkCell(r, 'creative_url', 'Creative'),
      h('td', { class: 'prod-c prod-nowrap', 'data-testid': 'prod-stage' }, stageLabel(r)),
      adsCell(r),
      h('td', { class: 'prod-c', 'data-testid': 'prod-result' }, resultText(r)),
      actions(r));
    return [main, wrap];
  }

  function draw() {
    const weeks = [...new Set([...(data.weeks || []), ...(st.week ? [st.week] : [])])].sort().reverse();
    const sel = (label, key, opts, val, onchange) => h('label', { class: 'prod-ctl' }, h('span', { class: 'pri-flabel' }, label),
      h('select', { class: 'pri-fselect', 'data-testid': 'prod-' + key, onchange: (e) => onchange(e.target.value) }, opts.map(([v, l]) => h('option', { value: v, selected: v === val }, l))));
    const refresh = () => { navigate(href('/priorities/production', { week: st.week, vendor: st.vendor, closed: st.closed ? 1 : '' }), { replace: true, render: false }); load().catch((e) => { if (!(e && e.name === 'AbortError')) say(e.message || 'Could not load.', true); }); };
    const controls = h('div', { class: 'prod-controls' },
      sel('Week', 'week', [['', 'All weeks'], ...weeks.map((w) => [w, weekLabel(w)])], st.week, (v) => { st.week = v; refresh(); }),
      sel('Vendor', 'vendor', [['', 'All vendors'], ...vendorNames().map((v) => [v, v])], st.vendor, (v) => { st.vendor = v; refresh(); }),
      h('label', { class: 'prod-check' }, h('input', { type: 'checkbox', 'data-testid': 'prod-closed', checked: st.closed, onchange: (e) => { st.closed = e.target.checked; refresh(); } }), ' Show launched and dropped'));
    const links = h('div', { class: 'prod-links', 'data-testid': 'prod-links' }, h('div', { class: 'pri-flabel' }, 'Vendor links'),
      h('div', { class: 'prod-linkrow' }, vendorNames().map((v) => h('span', { class: 'prod-vl', 'data-vendor': v },
        h('button', { type: 'button', class: 'btn prod-small', 'data-testid': 'prod-copy-link', onclick: () => vendorLink(v, false) }, 'Copy link for ' + v), ' ',
        h('button', { type: 'button', class: 'btn prod-small', 'data-testid': 'prod-reset-link', 'aria-label': 'Reset link for ' + v, onclick: () => vendorLink(v, true) }, 'Reset link')))));
    const heads = ['Week of', 'Vendor', 'Product', 'Concept', 'Brief', 'Creative', 'Stage', 'Ads', 'Result', ''];
    const table = (data.rows || []).length
      ? h('div', { class: 'prod-scroll', 'data-testid': 'prod-scroll' }, h('table', { class: 'pri-table prod-table', 'data-testid': 'prod-table' },
        h('thead', null, h('tr', null, heads.map((x) => h('th', { scope: 'col' }, x)))), h('tbody', null, data.rows.flatMap(row))))
      : h('p', { 'data-testid': 'prod-empty' }, 'Nothing here yet. On Creative priorities, tick concepts and click Assign to vendor.');
    body.replaceChildren(h('div', { class: 'pri-titlerow' }, h('div', { class: 'pri-titlecol' }, h('h1', null, 'Production'), h('p', { class: 'pri-sub' }, 'Concepts given to vendors to make, by week. Status of launched ads comes from the Creative Tracker.')),
      h('a', { href: href('/priorities', {}), 'data-testid': 'prod-back' }, 'Back to Creative priorities')), controls, links, msg, table);
  }
  root.append(body);
  await load();
  ctx.mount(root);
  document.title = 'Production · Creative priorities · Marketing metrics';
}

// ---------------------------------------------------------------------------------------------------------------- vendor page (login-exempt, no app navigation)
export async function vendorPage(ctx) {
  showControls(false);
  const tok = encodeURIComponent(ctx.match[1]);
  let d;
  try { d = await api('/api/priorities/vendor/' + tok, {}, { signal: ctx.signal }); } catch (e) {
    if (e && e.status === 404) { ctx.mount(h('div', { class: 'page pri-page prod-page' }, h('h1', null, 'Link not found'), h('p', { 'data-testid': 'prod-empty' }, 'This link is not valid any more. Ask for a new one.'))); return; }
    throw e;
  }
  const root = h('div', { class: 'page pri-page prod-page prod-vendor', 'data-testid': 'prod-vendor-page' });
  const openIds = new Set();
  const draw = () => {
    const rows = d.rows || [];
    const weeks = [...new Set(rows.map((r) => r.for_week))].sort().reverse();
    const title = `${d.vendor}: concepts to make`;
    document.title = title;
    root.replaceChildren(h('h1', null, title), rows.length ? null : h('p', { 'data-testid': 'prod-empty' }, 'Nothing to make right now.'),
      ...weeks.map((w) => h('section', { class: 'prod-week', 'data-testid': 'prod-weekgroup' }, h('h2', null, weekLabel(w)), rows.filter((r) => r.for_week === w).map(card))));
  };
  const save = async (r, body, saved) => {
    try { await api(`/api/priorities/vendor/${tok}/${r.id}`, {}, { method: 'PATCH', body }); d = await api('/api/priorities/vendor/' + tok, {}); draw(); flash(r.id, saved); } catch (e) { flash(r.id, e.message || 'Could not save.', true); }
  };
  const flash = (id, text, bad) => { const el = root.querySelector(`[data-id="${id}"] [data-testid=prod-saved]`); if (el) { el.textContent = text; el.classList.toggle('pri-err', !!bad); } };
  function card(r) {
    const link_ = h('input', { type: 'text', class: 'pri-name prod-in', 'aria-label': 'Creative link', 'data-testid': 'prod-in-creative', placeholder: 'https://', value: r.creative_url || '', maxlength: '2000' });
    const note = h('input', { type: 'text', class: 'pri-name prod-in', 'aria-label': 'Note', 'data-testid': 'prod-in-note', placeholder: 'Anything we should know', value: r.vendor_note || '', maxlength: '1000' });
    const res = resultText(r);
    const dt = (l, ...v) => [h('dt', { class: 'pri-flabel' }, l), h('dd', null, ...v)];
    const host = h('div', { class: 'prod-xd prod-xd-v', 'data-testid': 'prod-detail' });
    const x = expander({ key: 'v' + r.id, fetchDetail: () => api(`/api/priorities/vendor/${tok}/${r.id}/detail`, {}, { signal: ctx.signal }), host, startOpen: openIds.has(r.id), onToggle: (o) => { if (o) openIds.add(r.id); else openIds.delete(r.id); } });
    return h('article', { class: 'prod-card', 'data-testid': 'prod-card', 'data-id': r.id, 'data-stage': r.stage },
      h('h3', null, x.nameButton(r.pain_point || r.cluster)), h('p', { class: 'pri-sub' }, [productName(r.product), r.cluster, r.icp].filter(Boolean).join(' / ')),
      h('div', null, x.button()), host,
      r.story ? h('p', { class: 'prod-story', 'data-testid': 'prod-story' }, r.story) : null,
      h('dl', { class: 'prod-dl' },
        dt('Brief', r.brief_url ? link(r.brief_url, short(r.brief_url), { 'data-testid': 'prod-brief_url' }) : 'Not added yet'),
        dt('Stage', h('span', { 'data-testid': 'prod-stage' }, stageLabel(r))),
        dt('Ads', (r.ads || []).length ? (r.ads || []).map((a) => h('div', { 'data-testid': 'prod-cid' }, a.ad_url ? link(a.ad_url, a.concept_id) : a.concept_id)) : 'None yet'),
        dt('Result', h('span', { 'data-testid': 'prod-result' }, res || 'Not launched yet'))),
      h('label', { class: 'pri-dlabel' }, h('span', { class: 'pri-k' }, 'Creative link'), link_),
      h('label', { class: 'pri-dlabel' }, h('span', { class: 'pri-k' }, 'Note'), note),
      h('div', { class: 'prod-vbtns' },
        h('button', { type: 'button', class: 'btn primary', 'data-testid': 'prod-save', onclick: () => save(r, { creative_url: link_.value.trim() || null, vendor_note: note.value.trim() }, 'Saved') }, 'Save'),
        r.creative_url && r.stage !== 'launched' && r.stage !== 'dropped' && r.stage !== 'ready' ? h('button', { type: 'button', class: 'btn', 'data-testid': 'prod-vready', onclick: () => save(r, { ready: true }, 'Marked ready to launch') }, 'Ready to launch') : null,
        r.ready_at && r.stage === 'ready' ? h('button', { type: 'button', class: 'btn', 'data-testid': 'prod-vunready', onclick: () => save(r, { ready: false }, 'Ready to launch removed') }, 'Not ready yet') : null,
        h('span', { class: 'prod-saved', role: 'status', 'data-testid': 'prod-saved' })));
  }
  draw();
  ctx.mount(root);
}
