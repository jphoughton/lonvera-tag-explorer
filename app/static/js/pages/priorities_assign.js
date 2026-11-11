// "Assign to vendor" on /priorities (production hooks for the ideation page). Everything here is a small controller that priorities.js calls: the In production column, the Assign to vendor button and
// dialog, the "Production history" block in a row's detail panel and the "hide already assigned" checkbox. Assigning never removes a concept from the list. Nothing here is rendered on the public saved
// view page (/priorities/v/<token>): priorities.js does not create the controller there.
// Everything the API returned goes in through textContent / h(), links through h('a', {href, target: '_blank', rel: 'noopener noreferrer'}), never innerHTML. Test ids start with `pri-`.
import { h, href } from '../util.js';
import { api } from '../api.js';
import { mondayOf, addDays, today, VENDOR_FALLBACK, resultText, formatLabel } from './priorities_production.js';

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const monthDay = (iso) => { const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso || ''); return m ? `${MONTHS[+m[2] - 1]} ${+m[3]}` : (iso || ''); };
const weekOf = (iso) => 'week of ' + monthDay(iso);
const norm = (v) => String(v == null ? '' : v).replace(/\s+/g, ' ').trim().toLowerCase();
const textKeyOf = (r) => 'text:' + [r.product, r.cluster, r.icp, r.pain_point].map(norm).join('|');   // same as app/priorities_assign.text_key
const isOpen = (a) => a.stage !== 'launched' && a.stage !== 'dropped';
const stageText = (a) => a.stage_label || a.stage || '';
const unassignable = (r) => r.line_code === 'no line';                                                // the "ads without a line yet" row is a bucket of spend, not a concept

// "Madre, week of Nov 16: Brief sent" for the latest open assignment of a concept, plus ", +N earlier" for the other open ones; '' when nothing is open
export function inProductionText(list) {
  const open = (list || []).filter(isOpen).sort((a, b) => (a.for_week < b.for_week ? 1 : a.for_week > b.for_week ? -1 : b.id - a.id));
  if (!open.length) return '';
  const a = open[0];
  return `${a.vendor}, ${weekOf(a.for_week)}: ${stageText(a)}` + (open.length > 1 ? `, +${open.length - 1} earlier` : '');
}

function link(url, text, attrs) {
  return h('a', { href: url, target: '_blank', rel: 'noopener noreferrer', ...(attrs || {}) }, text);
}
const shortUrl = (u) => { try { const x = new URL(u); const t = (x.host + x.pathname).replace(/\/$/, ''); return t.length > 34 ? t.slice(0, 34) + '…' : t; } catch (e) { return u; } };

// d: the page data; selected(): the selected row objects; refresh(): redraw the table (apply); label(r): a short name for a row
export function assignControls({ d, selected, keyOf, refresh }) {
  const st = { rows: null, vendors: VENDOR_FALLBACK, formats: [], hide: false, failed: false };
  const cells = new Set();
  const hists = new Set();
  const thisMonday = mondayOf(today());
  const forRow = (r) => (st.rows || []).filter((a) => (a.concept_key && a.concept_key === r.ckey) || (a.text_key && a.text_key === textKeyOf(r)));

  const paintCell = (c) => { const t = inProductionText(forRow(c.r)); c.el.textContent = st.failed ? '' : (t || '–'); c.el.title = t ? 'Open assignments for this concept. Full history is in the row detail.' : 'Not assigned to a vendor yet'; };
  function paintHist(x) {
    const list = forRow(x.r).sort((a, b) => (a.for_week < b.for_week ? 1 : a.for_week > b.for_week ? -1 : b.id - a.id));
    if (st.rows === null) { x.body.replaceChildren(h('p', { class: 'pri-k' }, st.failed ? 'Production history could not be loaded.' : 'Loading production history…')); return; }
    if (!list.length) { x.body.replaceChildren(h('p', { class: 'pri-k', 'data-testid': 'pri-prod-none' }, 'Not assigned to a vendor yet.')); return; }
    const row = (a) => {
      const ids = a.tracker_concept_ids || [];
      return h('tr', { class: 'pri-w', 'data-testid': 'pri-prod-hrow' },
        h('td', { 'data-label': 'Week of' }, monthDay(a.for_week)), h('td', { 'data-label': 'Vendor' }, a.vendor), h('td', { 'data-label': 'Stage' }, stageText(a)),
        h('td', { 'data-label': 'Brief' }, a.brief_url ? link(a.brief_url, shortUrl(a.brief_url)) : '–'),
        h('td', { 'data-label': 'Creative' }, a.creative_url ? link(a.creative_url, shortUrl(a.creative_url)) : '–'),
        h('td', { 'data-label': 'Ads' }, ids.length ? ids.join(', ') : '–'), h('td', { 'data-label': 'Result' }, resultText(a) || '–'));
    };
    x.body.replaceChildren(h('table', { class: 'pri-wt', 'data-testid': 'pri-prod-htable' }, h('tbody', null,
      h('tr', { class: 'pri-wth' }, ['Week of', 'Vendor', 'Stage', 'Brief', 'Creative', 'Ads', 'Result'].map((t) => h('th', { scope: 'col' }, t))), list.map(row))));
  }
  const repaint = () => { cells.forEach(paintCell); hists.forEach(paintHist); };

  async function load() {
    try {
      const r = await api('/api/priorities/assignments', { include_closed: 1 });
      st.rows = r.rows || [];
      if (r.vendors && r.vendors.length) st.vendors = r.vendors;
      st.formats = r.formats || st.formats;
      st.failed = false;
    } catch (e) { st.rows = st.rows || null; st.failed = true; }
    repaint();
    refresh();
  }

  const assignedNow = (r) => forRow(r).some((a) => isOpen(a) && a.for_week >= thisMonday);        // an open assignment for this week or a later one

  // ---- the dialog: vendor, week of, note; the result (created / skipped with the reason) is shown in the same dialog
  function openDialog() {
    const all = selected();
    const rows = all.filter((r) => !unassignable(r));
    if (!all.length) return;
    const vendor = h('select', { class: 'pri-name', id: 'pri-as-vendor', 'data-testid': 'pri-as-vendor', 'aria-label': 'Vendor' }, st.vendors.map((v) => h('option', { value: v }, v)));
    const nextMon = addDays(thisMonday, 7);
    const weeks = [0, 7, 14, 21, 28].map((n) => addDays(thisMonday, n));
    const week = h('select', { class: 'pri-name', id: 'pri-as-week', 'data-testid': 'pri-as-week', 'aria-label': 'Week of' }, weeks.map((w) => h('option', { value: w, selected: w === nextMon }, 'Week of ' + monthDay(w) + (w === thisMonday ? ' (this week)' : w === nextMon ? ' (next week)' : ''))));
    const format = h('select', { class: 'pri-name', id: 'pri-as-format', 'data-testid': 'pri-as-format', 'aria-label': 'Format (optional)' }, h('option', { value: '' }, 'No format chosen'), st.formats.map((f) => h('option', { value: f.code }, formatLabel(f))));
    const note = h('input', { type: 'text', class: 'pri-name', id: 'pri-as-note', 'data-testid': 'pri-as-note', 'aria-label': 'Note (optional)', maxlength: '500', placeholder: 'Optional note for the team' });
    const out = h('div', { class: 'pri-as-out', 'data-testid': 'pri-as-result', role: 'status' });
    const cancel = h('button', { type: 'button', class: 'btn', 'data-testid': 'pri-as-cancel' }, 'Cancel');
    const ok = h('button', { type: 'submit', class: 'btn primary', 'data-testid': 'pri-as-ok' }, 'Assign');
    const skippedNote = all.length > rows.length ? h('p', { class: 'pri-k' }, `${all.length - rows.length} selected row${all.length - rows.length === 1 ? ' is' : 's are'} ads without a line, not a concept, and will not be assigned.`) : null;
    const form = h('form', { method: 'dialog', class: 'pri-dform', onsubmit: (e) => { e.preventDefault(); if (finished === 'sent') done(); else submit(); } },
      h('h2', { class: 'pri-dtitle', id: 'pri-as-title' }, 'Assign to vendor'),
      h('p', { class: 'pri-dnote', 'data-testid': 'pri-as-note-count' }, `${rows.length} concept${rows.length === 1 ? '' : 's'} selected. They stay in the list; this adds each to the Production page for the vendor and week below.`), skippedNote,
      h('label', { class: 'pri-dlabel' }, h('span', { class: 'pri-k' }, 'Vendor'), vendor), h('label', { class: 'pri-dlabel' }, h('span', { class: 'pri-k' }, 'Week of'), week),
      h('label', { class: 'pri-dlabel' }, h('span', { class: 'pri-k' }, 'Format (optional)'), format),
      h('label', { class: 'pri-dlabel' }, h('span', { class: 'pri-k' }, 'Note (optional)'), note), out,
      h('div', { class: 'pri-dbtns' }, cancel, ok));
    const dlg = h('dialog', { class: 'pri-dialog', 'aria-labelledby': 'pri-as-title', 'data-testid': 'pri-as-dialog' }, form);
    let finished = false;
    function done() { try { dlg.close(); } catch (e) { /* already closed */ } dlg.remove(); }
    cancel.addEventListener('click', done);
    dlg.addEventListener('cancel', (e) => { e.preventDefault(); done(); });
    dlg.addEventListener('click', (e) => { if (e.target === dlg) done(); });
    const labelOf = (key) => { const r = rows.find((x) => keyOf(x) === key); return r ? `${r.cluster} / ${r.icp} / ${r.pain_point}` : key; };
    async function submit() {
      if (!rows.length) { out.replaceChildren(h('p', { class: 'pri-err', role: 'alert' }, 'Nothing to assign.')); return; }
      ok.disabled = true;
      out.replaceChildren(h('p', { class: 'pri-k' }, 'Assigning…'));
      try {
        const r = await api('/api/priorities/assignments', {}, { method: 'POST', body: { keys: rows.map(keyOf), snapshot_week: d.week_start, vendor: vendor.value, for_week: week.value, note: note.value.trim(), ...(format.value ? { format_code: format.value } : {}) } });
        const made = (r.created || []).length; const skipped = r.skipped || [];
        out.replaceChildren(
          h('p', { 'data-testid': 'pri-as-created' }, `${made} concept${made === 1 ? '' : 's'} assigned to ${vendor.value} for the ${weekOf(week.value)}.`),
          skipped.length ? h('div', { 'data-testid': 'pri-as-skipped' }, h('p', null, `${skipped.length} skipped:`), h('ul', null, skipped.map((s) => h('li', null, `${labelOf(s.key)}: ${s.reason}.`)))) : null,
          h('p', null, h('a', { href: href('/priorities/production', { week: week.value }), 'data-testid': 'pri-as-open-production' }, 'Open Production')));
        finished = 'sent';
        ok.textContent = 'Close'; ok.disabled = false; ok.type = 'submit'; cancel.hidden = true;
        [vendor, week, format, note].forEach((el) => { el.disabled = true; });
        await load();
      } catch (e) {
        ok.disabled = false;
        out.replaceChildren(h('p', { class: 'pri-err', role: 'alert', 'data-testid': 'pri-as-error' }, 'Could not assign: ' + (e.message || 'error')));
      }
    }
    document.body.appendChild(dlg);
    if (typeof dlg.showModal === 'function') dlg.showModal(); else dlg.setAttribute('open', '');
    vendor.focus();
  }

  const button = h('button', { type: 'button', class: 'btn', 'data-testid': 'pri-sel-assign', onclick: openDialog }, 'Assign to vendor');
  const hideBox = h('input', { type: 'checkbox', 'data-testid': 'pri-hide-assigned', onchange: (e) => { st.hide = e.target.checked; refresh(); } });
  const hideLabel = h('label', { class: 'pri-showdone', title: 'Hides concepts that have an open assignment for this week or a later week. Off by default: assigning never removes a concept from the list.' }, hideBox, ' Hide concepts already assigned this week');

  load();
  return {
    button, hideLabel,
    hideOn: () => st.hide,
    setHide: (on) => { st.hide = on; hideBox.checked = on; },
    hidden: (r) => st.hide && assignedNow(r),
    text: (r) => inProductionText(forRow(r)),
    cell(r) { const c = { r, el: h('span', { class: 'pri-inprod', 'data-testid': 'pri-inprod' }) }; cells.add(c); paintCell(c); return c.el; },
    history(r) { const x = { r, body: h('div', null) }; hists.add(x); paintHist(x); return h('section', { class: 'pri-card pri-prodhist', 'data-testid': 'pri-prod-history', 'aria-label': 'Production history' }, h('h4', { class: 'pri-h' }, 'Production history'), x.body); },
  };
}
