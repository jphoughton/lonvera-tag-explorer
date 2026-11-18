// The "Proposed Concept" lane: the first lane of the Creative Pipeline board. It holds concepts that were proposed (added by hand here, from By cut, from a competitor ad, or from the weekly review)
// and are not yet given to a partner. Each card can be opened and edited, and assigned to a partner, which creates the board ticket in the Assigned lane (POST /api/priorities/backlog/assign).
// Everything from the API goes in through textContent / h(). Class names and test ids start with `prod-` (never ad-, ads-, sponsor, banner, promo: ad blockers hide those).
import { h } from '../util.js';
import { api } from '../api.js';
import { today, mondayOf, addDays, weekLabel, toast, PRODUCT_NAMES, productName } from './production_common.js';
import { openModal } from '../modal.js';
import { renderEvidence } from './production_cuts.js';

export const nextMondays = (n = 6) => { const m = mondayOf(today()); return Array.from({ length: n }, (_, i) => addDays(m, 7 * i)); };
const MEDIA = [['', 'Choose'], ['video', 'Video'], ['image', 'Image']];
const MEDIA_LABEL = { video: 'Video', image: 'Image' };
const weekSelect = (value, testid, onChange) => h('select', { 'data-testid': testid, 'aria-label': 'Week', onchange: (e) => onChange(e.target.value) },
  [...new Set([...nextMondays(), value].filter(Boolean))].sort().map((w) => h('option', { value: w, selected: w === value }, weekLabel(w))));
const field = (label, control) => h('label', { class: 'ctl prod-prop-field' }, h('span', { class: 'ctl-label' }, label), control);
const text = (value, testid, onInput, max = 200) => h('input', { type: 'text', maxlength: max, value: value || '', 'data-testid': testid, oninput: (e) => onInput(e.target.value) });
const select = (opts, value, testid, onChange) => h('select', { 'data-testid': testid, onchange: (e) => onChange(e.target.value) }, opts.map(([v, l]) => h('option', { value: v, selected: v === (value || '') }, l)));
const productOpts = [['', 'Choose'], ...Object.entries(PRODUCT_NAMES)];

// ctx = { signal, vendors: () => [names], reload: async () => void }
export function openAddConcept(ctx) {
  const f = { product: '', media_type: '', cluster: '', icp: '', pain_point: '', story: '', for_week: nextMondays()[0] };
  const err = h('p', { class: 'pri-err', role: 'alert', 'data-testid': 'prod-prop-add-err' });
  return openModal({ label: 'Add a proposed concept', className: 'prod-prop-modal', build(box, modal) {
    box.append(h('h3', { class: 'prod-cutads-title', 'data-testid': 'prod-prop-add-title' }, 'Add a proposed concept'),
      h('p', { class: 'pri-sub' }, 'It appears in the Proposed Concept lane until you assign it to a partner.'),
      h('div', { class: 'prod-prop-form' },
        field('Product (required)', select(productOpts, '', 'prod-prop-add-product', (v) => { f.product = v; })),
        field('Image or video (required)', select(MEDIA, '', 'prod-prop-add-media', (v) => { f.media_type = v; })),
        field('Market cluster (required)', text('', 'prod-prop-add-cluster', (v) => { f.cluster = v; })),
        field('Customer profile, ICP (required)', text('', 'prod-prop-add-icp', (v) => { f.icp = v; })),
        field('Pain point (required)', text('', 'prod-prop-add-pain', (v) => { f.pain_point = v; })),
        field('Story (15 words or fewer)', text('', 'prod-prop-add-story', (v) => { f.story = v; }, 300)),
        field('Week', weekSelect(f.for_week, 'prod-prop-add-week', (v) => { f.for_week = v; }))),
      h('div', { class: 'prod-bl-openbar' },
        h('button', { type: 'button', class: 'btn primary small', 'data-testid': 'prod-prop-add-go', onclick: async () => {
          err.textContent = '';
          try {
            const r = await api('/api/priorities/backlog', {}, { method: 'POST', body: { items: [{ product: f.product, cluster: f.cluster, icp: f.icp, pain_point: f.pain_point, ...(f.media_type ? { media_type: f.media_type } : {}), ...(f.story ? { story: f.story } : {}) }], for_week: f.for_week } });
            toast(r.created.length ? 'Added to Proposed Concept.' : 'That concept is already in Proposed Concept for that week.');
            modal.close(); await ctx.reload();
          } catch (e) { if (e && e.name === 'AbortError') throw e; err.textContent = e.message || 'Could not add.'; } } }, 'Add concept'),
        h('button', { type: 'button', class: 'btn small', onclick: () => modal.close() }, 'Cancel')), err);
  } });
}

// Choose a partner and give the brief (a link and / or text): creates the board ticket for the concept's own week, in the Assigned lane.
export function openAssign(r, ctx) {
  const f = { vendor: '', brief_url: '', brief: '' };
  const err = h('p', { class: 'pri-err', role: 'alert', 'data-testid': 'prod-prop-assign-err' });
  return openModal({ label: 'Assign to a partner: ' + r.title, className: 'prod-prop-modal', build(box, modal) {
    box.append(h('h3', { class: 'prod-cutads-title', 'data-testid': 'prod-prop-assign-title' }, 'Assign to a partner'),
      h('p', { class: 'pri-sub' }, `${r.title}. It becomes a card in the Assigned lane for ${weekLabel(r.for_week).toLowerCase()}.`),
      h('div', { class: 'prod-prop-form' },
        field('Partner', select([['', 'Choose a partner'], ...ctx.vendors().map((v) => [v, v])], '', 'prod-prop-assign-vendor', (v) => { f.vendor = v; })),
        field('Brief link', h('input', { type: 'url', placeholder: 'https://docs.google.com/…', 'data-testid': 'prod-prop-assign-url', oninput: (e) => { f.brief_url = e.target.value.trim(); } })),
        field('Or brief text', h('input', { type: 'text', maxlength: 1000, placeholder: 'What to make', 'data-testid': 'prod-prop-assign-text', oninput: (e) => { f.brief = e.target.value.trim(); } }))),
      h('div', { class: 'prod-bl-openbar' },
        h('button', { type: 'button', class: 'btn primary small', 'data-testid': 'prod-prop-assign-go', onclick: async () => {
          err.textContent = '';
          if (!f.vendor) { err.textContent = 'Choose a partner.'; return; }
          if (!f.brief_url && !f.brief) { err.textContent = 'Add a brief link or some brief text: every ticket needs a brief.'; return; }
          try {
            await api('/api/priorities/backlog/assign', {}, { method: 'POST', body: { ids: [r.id], vendor: f.vendor, status: 'assigned', ...(f.brief_url ? { brief_url: f.brief_url } : {}), ...(f.brief ? { brief: f.brief } : {}) } });
            toast(`Assigned to ${f.vendor}. It is now in the Assigned lane.`);
            modal.close(); await ctx.reload();
          } catch (e) { if (e && e.name === 'AbortError') throw e; err.textContent = e.message || 'Could not assign.'; } } }, 'Assign'),
        h('button', { type: 'button', class: 'btn small', onclick: () => modal.close() }, 'Cancel')), err);
  } });
}

// Open the concept: every field editable in one place, then the evidence behind its cut (priority score, top spenders, top hooks, ads).
export async function openProposed(r, ctx) {
  const f = { product: r.product, media_type: r.media_type || '', for_week: r.for_week, cluster: r.cluster, icp: r.icp, pain_point: r.pain_point, story: r.story || '', note: r.note || '' };
  const evidence = h('div', { 'data-testid': 'prod-prop-evidence' }, h('p', { class: 'pri-sub' }, 'Loading the evidence behind this cut…'));
  const msg = h('span', { class: 'pri-sub', role: 'status', 'data-testid': 'prod-prop-open-msg' });
  const area = (key, label, rows, max, testid) => h('label', { class: 'prod-bl-openlabel' }, label, h('textarea', { rows, maxlength: max, 'data-testid': testid, oninput: (e) => { f[key] = e.target.value; } }, f[key]));
  let removeArmed = false;
  const modal = openModal({ label: 'Proposed concept: ' + r.title, className: 'prod-cutads prod-bl-open prod-prop-modal', build(box, m) {
    const remove = h('button', { type: 'button', class: 'btn small', 'data-testid': 'prod-prop-remove', onclick: async () => {
      if (!removeArmed) { removeArmed = true; remove.textContent = 'Confirm: remove this concept'; return; }
      try { await api('/api/priorities/backlog/' + r.id, {}, { method: 'DELETE' }); toast('Removed.'); m.close(); await ctx.reload(); } catch (e) { if (e && e.name === 'AbortError') throw e; msg.textContent = e.message || 'Could not remove.'; } } }, 'Remove concept');
    box.append(h('h3', { class: 'prod-cutads-title', 'data-testid': 'prod-prop-open-title' }, r.title),
      h('p', { class: 'pri-sub' }, `Added ${(r.created_at || '').slice(0, 10)}${r.created_by ? ' by ' + r.created_by : ''}.`),
      h('div', { class: 'prod-prop-form', 'data-testid': 'prod-prop-open-facts' },
        field('Product', select(productOpts, f.product, 'prod-prop-open-product', (v) => { f.product = v; })),
        field('Image or video', select(MEDIA, f.media_type, 'prod-prop-open-media', (v) => { f.media_type = v; })),
        field('Week', weekSelect(f.for_week, 'prod-prop-open-week', (v) => { f.for_week = v; })),
        field('Market cluster', text(f.cluster, 'prod-prop-open-cluster', (v) => { f.cluster = v; })),
        field('Customer profile, ICP', text(f.icp, 'prod-prop-open-icp', (v) => { f.icp = v; })),
        field('Pain point', text(f.pain_point, 'prod-prop-open-pain', (v) => { f.pain_point = v; }))),
      area('story', 'Story (one sentence, 15 words or fewer)', 2, 300, 'prod-prop-open-story'),
      area('note', 'Note: why it was picked, and anything the brief needs', 4, 500, 'prod-prop-open-note'),
      h('div', { class: 'prod-bl-openbar' },
        h('button', { type: 'button', class: 'btn primary small', 'data-testid': 'prod-prop-open-save', onclick: async () => {
          const body = {};
          for (const k of Object.keys(f)) if (String(f[k] || '') !== String((k === 'media_type' ? r.media_type : r[k]) || '')) body[k] = f[k];
          if (!Object.keys(body).length) { msg.textContent = 'Nothing changed.'; return; }
          try { await api('/api/priorities/backlog/' + r.id, {}, { method: 'PATCH', body }); msg.textContent = 'Saved.'; toast('Saved.'); await ctx.reload(); m.close(); }
          catch (e) { if (e && e.name === 'AbortError') throw e; msg.textContent = e.message || 'Could not save.'; } } }, 'Save changes'),
        h('button', { type: 'button', class: 'btn small', 'data-testid': 'prod-prop-open-assign', onclick: () => { m.close(); openAssign(r, ctx); } }, 'Assign to partner'), remove, msg),
      evidence);
  } });
  try {
    const q = { product: r.product, cluster: r.cluster, icp: r.icp, pain_point: r.pain_point };
    const [d, c] = await Promise.all([api('/api/priorities/cuts/ads', q, { signal: ctx.signal }), api('/api/priorities/cuts', { ...q, group: 'cluster,icp,pain_point', score: 1 }, { signal: ctx.signal })]);
    const row = (c.rows || []).find((x) => ['cluster', 'icp', 'pain_point'].every((k) => String(x.labels[k] || '').toLowerCase() === String(r[k] || '').toLowerCase()));
    renderEvidence(evidence, d, row ? row.priority : null);
  } catch (e) { if (e && e.name === 'AbortError') return modal; evidence.replaceChildren(h('p', { class: 'pri-err' }, e.message || 'Could not load the evidence.')); }
  return modal;
}

function cardEl(r, ctx) {
  const line = (k, v) => (v ? h('div', { class: 'prod-fact' }, h('dt', null, k + ':'), h('dd', null, v)) : null);
  return h('article', { class: 'prod-prop-card', 'data-testid': 'prod-prop-card', 'data-id': String(r.id), 'aria-label': r.title },
    h('div', { class: 'prod-card-top' },
      h('span', { class: 'prod-dot prod-dot-' + (r.product || 'none'), title: productName(r.product), 'data-product': r.product || '' }),
      h('button', { type: 'button', class: 'prod-card-title', 'data-testid': 'prod-prop-title', onclick: () => openProposed(r, ctx) }, r.title)),
    h('dl', { class: 'prod-facts prod-card-facts' }, line('Cluster', r.cluster), line('ICP', r.icp), line('Pain point', r.pain_point), line('Story', r.story), line('Note', r.note)),
    h('div', { class: 'prod-card-chips' },
      r.media_type ? h('span', { class: 'prod-chip prod-chip-media prod-media-' + r.media_type, 'data-testid': 'prod-prop-media' }, MEDIA_LABEL[r.media_type] || r.media_type) : null,
      h('span', { class: 'prod-chip', 'data-testid': 'prod-prop-week' }, weekLabel(r.for_week))),
    r.created_by ? h('p', { class: 'pri-sub prod-prop-by' }, 'Added by ' + r.created_by) : null,
    h('div', { class: 'prod-card-foot' },
      h('button', { type: 'button', class: 'btn primary prod-card-action', 'data-testid': 'prod-prop-assign', 'aria-label': 'Assign to partner: ' + r.title, onclick: () => openAssign(r, ctx) }, 'Assign to partner'),
      h('button', { type: 'button', class: 'btn prod-card-action', 'data-testid': 'prod-prop-open', 'aria-label': 'Open ' + r.title, onclick: () => openProposed(r, ctx) }, 'Open')));
}

// The lane itself: a column in the board grid (and the first jump chip on a phone). Not a drop target: a concept leaves it only by being assigned.
export function proposedColumn({ rows, ready = true, message = '', ctx }) {
  const head = h('header', { class: 'prod-col-head' }, h('h3', null, 'Proposed Concept'), h('span', { class: 'prod-col-count', 'data-testid': 'prod-prop-count' }, String(rows.length)));
  const add = h('button', { type: 'button', class: 'btn small prod-prop-add', 'data-testid': 'prod-prop-add', onclick: () => openAddConcept(ctx) }, 'Add concept');
  const list = h('div', { class: 'prod-board-list prod-nodrop prod-prop-list', 'data-testid': 'prod-prop-list', 'data-status': 'proposed', role: 'list', 'aria-label': 'Proposed Concept' },
    ready ? (rows.length ? rows.map((r) => h('div', { role: 'listitem' }, cardEl(r, ctx))) : h('p', { class: 'prod-board-empty', 'data-testid': 'prod-prop-empty' }, 'No proposed concepts. Add one, or send one from By cut.'))
      : h('p', { class: 'pri-err', 'data-testid': 'prod-prop-notready' }, message || 'Proposed concepts are not set up yet.'));
  return h('section', { class: 'prod-board-col prod-col-proposed', 'data-testid': 'prod-prop-col', 'data-status': 'proposed', 'aria-label': 'Proposed Concept' }, head, ready ? add : null, list);
}
