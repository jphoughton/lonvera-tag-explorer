// Manual concepts on /priorities: the "Add concept" form (a native <dialog>), the detail panel of a manual row, the manual-note block inside a matching auto row, and the row actions
// (edit / mark done / delete). Kept apart from priorities.js so the table code and the detail-panel restyle stay untouched. Everything is built with h() (text nodes, never innerHTML).
// Class names and test ids start with `pri-`.
import { h } from '../util.js';
import { api } from '../api.js';

const NEW = '__new__';
export const dayOf = (iso) => {
  const t = iso ? new Date(iso) : null;
  return t && !Number.isNaN(t.getTime()) ? t.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }) : '';
};

// ---------------------------------------------------------------------------------------------------------------- panels
const field = (label, value) => h('div', { class: 'pri-tag' }, h('div', { class: 'pri-label' }, label), h('div', { class: 'pri-dval' }, value || '–'));
const block = (label, text, testid) => (text ? h('div', { class: 'pri-mblock' }, h('div', { class: 'pri-label' }, label), h('div', { class: 'pri-mtext', 'data-testid': testid }, text)) : null);

// the expanded panel of a manual row (its own render function: the auto-row panel in priorities.js is not touched)
export function manualPanel(r, mctl) {
  const meta = [`Added ${dayOf(r.created_at)}`, r.updated_at && r.updated_at !== r.created_at ? `edited ${dayOf(r.updated_at)}` : null, r.done ? `marked done ${dayOf(r.done_at)}` : null].filter(Boolean).join(', ');
  return h('div', { class: 'pri-panel pri-mpanel', 'data-testid': 'pri-manual-panel' },
    h('div', { class: 'pri-mhead' }, h('span', { class: 'pri-mbadge' }, 'Manual concept'), h('span', { class: 'pri-k' }, ' Added by hand; stays on the sheet every week until it is marked done or deleted.')),
    h('div', { class: 'pri-descs pri-mfields' }, field('Market Cluster', r.cluster), field('ICP', r.icp), field('Pain Point', r.pain_point), field('Story', r.story)),
    r.new_values && r.new_values.length ? h('p', { class: 'pri-mnew', 'data-testid': 'pri-manual-new' }, `New, not in the Communication Overview yet: ${r.new_values.join(', ')}.`) : null,
    block('Notes', r.notes, 'pri-manual-notes'),
    block('Why now / what to test', r.why_now, 'pri-manual-why'),
    h('p', { class: 'pri-k', 'data-testid': 'pri-manual-meta' }, meta + '.'),
    mctl && !mctl.readonly ? h('div', { class: 'pri-mactions' },
      h('button', { type: 'button', class: 'btn', 'data-testid': 'pri-manual-edit', onclick: () => mctl.edit(r) }, 'Edit'),
      h('button', { type: 'button', class: 'btn', 'data-testid': 'pri-manual-done', onclick: () => mctl.setDone(r, !r.done) }, r.done ? 'Reopen' : 'Mark done'),
      h('button', { type: 'button', class: 'btn pri-mdel', 'data-testid': 'pri-manual-delete', onclick: () => mctl.remove(r) }, 'Delete')) : null);
}

// an auto row whose product + cluster + ICP + pain point match open manual concepts shows their notes inside its own panel (the manual rows stay in the table too)
const norm = (t) => String(t || '').trim().toLowerCase();
export function addManualNotes(panel, r, d) {
  const same = d.rows.filter((m) => m.manual && !m.done && m.product === r.product && norm(m.cluster) === norm(r.cluster) && norm(m.icp) === norm(r.icp) && norm(m.pain_point) === norm(r.pain_point));
  if (!same.length) return;
  panel.append(h('div', { class: 'pri-mnote', 'data-testid': 'pri-manual-note' }, h('span', { class: 'pri-mbadge' }, 'Manual note'),
    same.map((m) => h('div', { class: 'pri-mnote-item' }, h('span', { class: 'pri-k' }, `Added ${dayOf(m.created_at)}: `), [m.notes, m.why_now && `Why now: ${m.why_now}`].filter(Boolean).join(' — ') || '(no notes)'))));
}

// the strip under a manual row in the print layout, and the notes block of Copy as text (max 0 = full text)
export function manualStrip(r, max = 0) {
  const clip = (t) => { t = t.replace(/\s+/g, ' ').trim(); return max && t.length > max ? t.slice(0, max).replace(/\s+\S*$/, '') + '…' : t; };
  const full = !max;
  const out = [r.notes && (full ? r.notes : clip(r.notes)), r.why_now && `Why now: ${r.why_now}`].filter(Boolean);
  return out.length ? out : ['Manual concept (no notes).'];
}

// ---------------------------------------------------------------------------------------------------------------- the form
let optionsPromise = null;
const loadOptions = () => { if (!optionsPromise) optionsPromise = api('/api/priorities/manual/options').catch((e) => { optionsPromise = null; throw e; }); return optionsPromise; };

// a dropdown of catalog values with "Other / new..." that reveals a text box; `free` = no catalog values at all (text box only)
function combo(id, label, { optional = false } = {}) {
  const sel = h('select', { id: 'pri-f-' + id, 'data-testid': 'pri-f-' + id });
  const txt = h('input', { type: 'text', maxlength: '400', class: 'pri-newval', 'data-testid': 'pri-f-' + id + '-new', 'aria-label': `New ${label.toLowerCase()}`, placeholder: `Type the new ${label.toLowerCase()}` });
  let sig = '', free = false;
  const sync = () => { txt.hidden = !(free || sel.value === NEW); sel.hidden = free; };
  sel.addEventListener('change', () => { sync(); if (sel.value === NEW) txt.focus(); });
  const el = h('div', { class: 'pri-fld' }, h('label', { for: 'pri-f-' + id }, label, optional ? h('span', { class: 'pri-k' }, ' (optional)') : null), sel, txt);
  return {
    el, sel, txt,
    // list = catalog values; null = the parent is not chosen yet
    setOptions(list, sectionNote) {
      const next = JSON.stringify([list, sectionNote]);
      if (next === sig) return;
      sig = next;
      const prev = sel.value;
      free = list !== null && !list.length && optional;
      sel.replaceChildren(h('option', { value: '' }, list === null ? sectionNote : optional ? 'No story' : 'Choose…'),
        ...(list || []).map((v) => h('option', { value: v, title: v }, v.length > 90 ? v.slice(0, 88) + '…' : v)), list === null ? null : h('option', { value: NEW }, 'Other / new…'));
      sel.disabled = list === null;
      if (list !== null && !list.length && !free) sel.value = NEW;                  // nothing in the catalog under the parent: the only choice is a new value
      else sel.value = [...sel.options].some((o) => o.value === prev) ? prev : '';
      if (list === null) txt.value = '';
      sync();
    },
    pick: () => (sel.value && sel.value !== NEW && !free ? sel.value : null),     // the catalog value chosen, if any
    chosen: () => free || sel.value !== '',                                      // something decided (a catalog value or a new one): the children can open
    value: () => (free || sel.value === NEW ? txt.value.trim() : sel.value),
    set(v) { v = v || ''; if (free) { txt.value = v; return; } if ([...sel.options].some((o) => o.value === v && v)) sel.value = v; else if (v) { sel.value = NEW; txt.value = v; } sync(); },
  };
}

export async function openConceptForm({ row = null, onSaved }) {
  let opt;
  try { opt = await loadOptions(); } catch (e) { window.alert('Could not load the options: ' + (e.message || 'error')); return; }
  const tree = opt.tree;
  const L = opt.limits;
  const product = h('select', { id: 'pri-f-product', 'data-testid': 'pri-f-product', required: true }, h('option', { value: '' }, 'Choose…'), opt.products.map((p) => h('option', { value: p.slug }, p.name)));
  const section = h('select', { id: 'pri-f-section', 'data-testid': 'pri-f-section' }, opt.sections.map((s) => h('option', { value: s.key }, s.label)));
  const cluster = combo('cluster', 'Market Cluster'), icp = combo('icp', 'ICP'), pain = combo('pain', 'Pain Point'), story = combo('story', 'Story', { optional: true });
  const notes = h('textarea', { id: 'pri-f-notes', 'data-testid': 'pri-f-notes', rows: '5', maxlength: String(L.notes), placeholder: 'Angle, hook ideas, references, who asked for it…' });
  const count = h('span', { class: 'pri-k pri-count', 'data-testid': 'pri-f-count' }, `0 / ${L.notes}`);
  const why = h('input', { type: 'text', id: 'pri-f-why', 'data-testid': 'pri-f-why', maxlength: String(L.why_now), placeholder: 'e.g. a competitor started running this last week' });
  const err = h('p', { class: 'pri-err pri-ferr', role: 'alert', 'data-testid': 'pri-f-error', hidden: true });
  notes.addEventListener('input', () => { count.textContent = `${notes.value.length} / ${L.notes}`; });

  // dependent options: product -> cluster -> ICP -> pain point -> story, all from the Communication Overview catalogs
  const refresh = () => {
    const t = tree[product.value] || {};
    cluster.setOptions(product.value ? Object.keys(t) : null, 'Choose a product first');
    const c = cluster.pick(), ti = (c && t[c]) || {};
    icp.setOptions(cluster.chosen() ? Object.keys(ti) : null, 'Choose a cluster first');
    const i = icp.pick(), tp = (i && ti[i]) || {};
    pain.setOptions(icp.chosen() ? Object.keys(tp) : null, 'Choose an ICP first');
    const p = pain.pick();
    story.setOptions(pain.chosen() ? ((p && tp[p]) || []) : null, 'Choose a pain point first');
  };
  [product, cluster.sel, icp.sel, pain.sel].forEach((s) => s.addEventListener('change', refresh));
  refresh();
  if (row) {                                                                    // edit: fill top-down, refreshing the options between steps
    product.value = row.product; section.value = row.section; refresh();
    cluster.set(row.cluster); refresh(); icp.set(row.icp); refresh(); pain.set(row.pain_point); refresh(); story.set(row.story);
    notes.value = row.notes || ''; why.value = row.why_now || ''; count.textContent = `${notes.value.length} / ${L.notes}`;
  }

  const dlg = h('dialog', { class: 'pri-dlg', 'aria-labelledby': 'pri-dlg-title', 'data-testid': 'pri-form' });
  const close = () => { try { dlg.close(); } catch (e) { /* already closed */ } dlg.remove(); };
  const save = h('button', { type: 'submit', class: 'btn primary', 'data-testid': 'pri-f-save' }, row ? 'Save changes' : 'Add concept');
  const form = h('form', { class: 'pri-form', novalidate: true, onsubmit: async (e) => {
    e.preventDefault();
    const body = { product: product.value, section: section.value, cluster: cluster.value(), icp: icp.value(), pain_point: pain.value(), story: story.value(), notes: notes.value, why_now: why.value };
    const missing = [['product', 'Product'], ['cluster', 'Market Cluster'], ['icp', 'ICP'], ['pain_point', 'Pain Point']].filter(([k]) => !body[k]).map(([, l]) => l);
    err.hidden = !missing.length;
    if (missing.length) { err.textContent = 'Fill in: ' + missing.join(', ') + '.'; return; }
    save.disabled = true;
    try {
      const saved = row ? await api('/api/priorities/manual/' + row.id, {}, { method: 'PATCH', body }) : await api('/api/priorities/manual', {}, { method: 'POST', body });
      close();
      await onSaved(saved);
    } catch (x) { save.disabled = false; err.hidden = false; err.textContent = 'Could not save: ' + (x.message || 'error'); }
  } },
    h('div', { class: 'pri-dhead' }, h('h2', { id: 'pri-dlg-title' }, row ? 'Edit manual concept' : 'Add concept'),
      h('button', { type: 'button', class: 'pri-dclose', 'aria-label': 'Close', 'data-testid': 'pri-f-close', onclick: close }, '×')),
    h('p', { class: 'pri-k pri-dlead' }, 'Pick from the Communication Overview, or choose “Other / new…” on any list to propose a new value. Manual concepts stay on the sheet until you mark them done or delete them.'),
    h('div', { class: 'pri-dgrid' },
      h('div', { class: 'pri-fld' }, h('label', { for: 'pri-f-product' }, 'Product'), product),
      h('div', { class: 'pri-fld' }, h('label', { for: 'pri-f-section' }, 'Action'), section),
      cluster.el, icp.el, pain.el, story.el,
      h('div', { class: 'pri-fld pri-wide' }, h('label', { for: 'pri-f-notes' }, 'Notes ', count), notes),
      h('div', { class: 'pri-fld pri-wide' }, h('label', { for: 'pri-f-why' }, 'Why now / what to test ', h('span', { class: 'pri-k' }, '(optional)')), why)),
    err,
    h('div', { class: 'pri-dfoot' }, h('button', { type: 'button', class: 'btn', 'data-testid': 'pri-f-cancel', onclick: close }, 'Cancel'), save));
  dlg.append(form);
  dlg.addEventListener('click', (e) => { if (e.target === dlg) close(); });
  dlg.addEventListener('close', () => dlg.remove());
  document.body.append(dlg);
  dlg.showModal();
  product.focus();
}

// ---------------------------------------------------------------------------------------------------------------- row actions
export function manualControls(d, readonly, opts, dropSelection) {
  const reload = async () => { if (opts.reload) await opts.reload(); };
  return {
    readonly,
    add: () => openConceptForm({ onSaved: reload }),
    edit: (r) => openConceptForm({ row: r, onSaved: reload }),
    setDone: async (r, done) => {
      try { await api('/api/priorities/manual/' + r.id, {}, { method: 'PATCH', body: { status: done ? 'done' : 'open' } }); } catch (e) { window.alert('Could not update: ' + (e.message || 'error')); return; }
      if (done) dropSelection(r);
      await reload();
    },
    remove: async (r) => {
      if (!window.confirm(`Delete this manual concept?\n\n${r.cluster} / ${r.icp} / ${r.pain_point}\n\nThis cannot be undone.`)) return;
      try { await api('/api/priorities/manual/' + r.id, {}, { method: 'DELETE' }); } catch (e) { /* already gone */ }
      dropSelection(r);
      await reload();
    },
  };
}
