// Creative Pipeline "Needs brief" view: the queue of cuts the team decided to work on that still need a brief (GET /api/priorities/backlog).
// Add by hand here or from the By cut view, change a week or note, remove, and select several items to assign one partner and one brief to all of them: that creates the board tickets.
// Everything from the API goes in through textContent / h(). Class names and test ids start with `prod-` (never ad-, ads-, sponsor, banner, promo: ad blockers hide those).
import { h, href } from '../util.js';
import { api } from '../api.js';
import { today, mondayOf, addDays, weekLabel, toast, PRODUCT_NAMES } from './production_common.js';

export const nextMondays = (n = 6) => { const m = mondayOf(today()); return Array.from({ length: n }, (_, i) => addDays(m, 7 * i)); };

export function backlogView({ host, signal, say, goBoard }) {
  const st = { rows: [], vendors: [], sel: new Set(), week: '', ready: true };
  const barHost = h('div', { class: 'prod-bl-bar', 'data-testid': 'prod-bl-bar' });
  const addHost = h('details', { class: 'prod-bl-add', 'data-testid': 'prod-bl-add' });
  const tableHost = h('div', { class: 'prod-bl-out', 'data-testid': 'prod-bl-out' });
  host.replaceChildren(addHost, barHost, tableHost);
  const weekSelect = (value, onChange, testid) => h('select', { 'data-testid': testid, 'aria-label': 'Week', onchange: (e) => onChange(e.target.value) },
    [...new Set([...nextMondays(), value].filter(Boolean))].sort().map((w) => h('option', { value: w, selected: w === value }, weekLabel(w))));
  const failed = (e) => { if (e && e.name === 'AbortError') throw e; say(e.message || 'Something went wrong.', true); toast(e.message || 'Something went wrong.', true); };

  async function load() {
    try {
      const d = await api('/api/priorities/backlog', {}, { signal });
      st.rows = d.rows || []; st.vendors = d.vendors || []; st.ready = d.ready !== false;
      for (const id of [...st.sel]) if (!st.rows.some((r) => r.id === id)) st.sel.delete(id);
      say(st.ready ? '' : d.message || '');
      drawAdd(); drawBar(); drawTable();
    } catch (e) { failed(e); }
  }

  function drawAdd() {
    const open = addHost.open;
    const f = { product: '', cluster: '', icp: '', pain_point: '', media_type: '', story: '', for_week: nextMondays()[0], note: '' };
    const txt = (key, label, tid) => h('label', { class: 'ctl' }, h('span', { class: 'ctl-label' }, label), h('input', { type: 'text', maxlength: 200, 'data-testid': tid, oninput: (e) => { f[key] = e.target.value; } }));
    const err = h('p', { class: 'pri-err', role: 'alert' });
    addHost.replaceChildren(h('summary', null, 'Add to Needs brief by hand'),
      h('div', { class: 'ctl-row' },
        h('label', { class: 'ctl' }, h('span', { class: 'ctl-label' }, 'Product (required)'), h('select', { 'data-testid': 'prod-bl-add-product', onchange: (e) => { f.product = e.target.value; } }, [h('option', { value: '' }, 'Choose'), ...Object.entries(PRODUCT_NAMES).map(([k, v]) => h('option', { value: k }, v))])),
        h('label', { class: 'ctl' }, h('span', { class: 'ctl-label' }, 'Image or video (required)'), h('select', { 'data-testid': 'prod-bl-add-media', onchange: (e) => { f.media_type = e.target.value; } }, [['', 'Choose'], ['video', 'Video'], ['image', 'Image']].map(([v, l]) => h('option', { value: v }, l)))),
        txt('cluster', 'Market cluster (required)', 'prod-bl-add-cluster'), txt('icp', 'ICP (required)', 'prod-bl-add-icp'), txt('pain_point', 'Pain point (required)', 'prod-bl-add-pain'), txt('story', 'Story (15 words or fewer)', 'prod-bl-add-story'),
        h('label', { class: 'ctl' }, h('span', { class: 'ctl-label' }, 'Week'), weekSelect(f.for_week, (v) => { f.for_week = v; }, 'prod-bl-add-week')),
        h('button', { type: 'button', class: 'btn primary small', 'data-testid': 'prod-bl-add-go', onclick: async () => {
          err.textContent = '';
          try { const r = await api('/api/priorities/backlog', {}, { method: 'POST', body: { items: [{ product: f.product, cluster: f.cluster, icp: f.icp, pain_point: f.pain_point, ...(f.media_type ? { media_type: f.media_type } : {}), ...(f.story ? { story: f.story } : {}) }], for_week: f.for_week } }); toast(r.created.length ? 'Added to Needs brief.' : 'Already in Needs brief for that week.'); await load(); }
          catch (e) { if (e && e.name === 'AbortError') throw e; err.textContent = e.message || 'Could not add.'; } } }, 'Add')), err);
    if (open) addHost.setAttribute('open', '');
  }

  function drawBar() {
    const n = st.sel.size;
    if (!n) { barHost.replaceChildren(h('p', { class: 'pri-sub', 'data-testid': 'prod-bl-hint' }, st.rows.length ? 'Tick the items you are briefing now, then pick a partner and add the brief.' : '')); return; }
    const f = { vendor: '', brief_url: '', brief: '' };
    const err = h('p', { class: 'pri-err', role: 'alert', 'data-testid': 'prod-bl-err' });
    barHost.replaceChildren(h('div', { class: 'prod-bl-assign' },
      h('strong', null, `${n} selected`),
      h('label', { class: 'ctl' }, h('span', { class: 'ctl-label' }, 'Partner'), h('select', { 'data-testid': 'prod-bl-vendor', onchange: (e) => { f.vendor = e.target.value; } }, [h('option', { value: '' }, 'Choose a partner'), ...st.vendors.map((v) => h('option', { value: v }, v))])),
      h('label', { class: 'ctl' }, h('span', { class: 'ctl-label' }, 'Brief link'), h('input', { type: 'url', placeholder: 'https://docs.google.com/…', 'data-testid': 'prod-bl-brief-url', oninput: (e) => { f.brief_url = e.target.value.trim(); } })),
      h('label', { class: 'ctl' }, h('span', { class: 'ctl-label' }, 'Or brief text'), h('input', { type: 'text', maxlength: 1000, placeholder: 'What to make', 'data-testid': 'prod-bl-brief-text', oninput: (e) => { f.brief = e.target.value.trim(); } })),
      h('button', { type: 'button', class: 'btn primary small', 'data-testid': 'prod-bl-assign', onclick: async () => {
        err.textContent = '';
        if (!f.vendor) { err.textContent = 'Choose a partner.'; return; }
        if (!f.brief_url && !f.brief) { err.textContent = 'Add a brief link or some brief text: every ticket needs a brief.'; return; }
        try {
          const r = await api('/api/priorities/backlog/assign', {}, { method: 'POST', body: { ids: [...st.sel], vendor: f.vendor, ...(f.brief_url ? { brief_url: f.brief_url } : {}), ...(f.brief ? { brief: f.brief } : {}) } });
          st.sel.clear(); toast(`${r.created.length} ticket${r.created.length === 1 ? '' : 's'} created for ${f.vendor}.`); await load();
          if (goBoard) say(`${r.created.length} ticket${r.created.length === 1 ? '' : 's'} created for ${f.vendor}. They are on the Board.`);
        } catch (e) { if (e && e.name === 'AbortError') throw e; err.textContent = e.message || 'Could not assign.'; } } }, `Assign ${n} to partner`),
      h('button', { type: 'button', class: 'btn small', 'data-testid': 'prod-bl-remove', onclick: async () => {
        try { for (const id of [...st.sel]) await api('/api/priorities/backlog/' + id, {}, { method: 'DELETE' }); st.sel.clear(); toast('Removed.'); await load(); } catch (e) { failed(e); } } }, 'Remove selected')), err);
  }

  // The five tags are required: a missing one shows as an empty required box on the row and is filled in right there.
  const save = async (r, k, v) => { try { await api('/api/priorities/backlog/' + r.id, {}, { method: 'PATCH', body: { [k]: v } }); toast('Saved.'); await load(); } catch (e) { failed(e); await load(); } };
  const pick = (r, k, opts) => h('select', { class: r[k] ? null : 'prod-bl-missing', 'aria-label': k, 'data-testid': 'prod-bl-' + k, onchange: (e) => { if (e.target.value) save(r, k, e.target.value); } }, opts.map(([v, l]) => h('option', { value: v, selected: v === (r[k] || '') }, l)));
  const field = (r, k) => h('input', { type: 'text', value: r[k] || '', maxlength: 200, placeholder: 'Required', class: r[k] ? null : 'prod-bl-missing', 'aria-label': k, 'data-testid': 'prod-bl-' + k, onchange: (e) => { if (e.target.value.trim()) save(r, k, e.target.value.trim()); } });
  function drawTable() {
    const weeks = [...new Set(st.rows.map((r) => r.for_week))].sort();
    const shown = st.week ? st.rows.filter((r) => r.for_week === st.week) : st.rows;
    const all = h('input', { type: 'checkbox', 'aria-label': 'Select all shown', 'data-testid': 'prod-bl-all', checked: shown.length > 0 && shown.every((r) => st.sel.has(r.id)), onchange: (e) => { for (const r of shown) (e.target.checked ? st.sel.add(r.id) : st.sel.delete(r.id)); drawBar(); drawTable(); } });
    const row = (r) => h('tr', { 'data-testid': 'prod-bl-row', 'data-id': String(r.id) },
      h('td', { class: 'prod-c' }, h('input', { type: 'checkbox', 'aria-label': 'Select ' + r.title, 'data-testid': 'prod-bl-sel', checked: st.sel.has(r.id), onchange: (e) => { if (e.target.checked) st.sel.add(r.id); else st.sel.delete(r.id); drawBar(); } })),
      h('td', { class: 'prod-c prod-nowrap' }, weekSelect(r.for_week, async (v) => { try { await api('/api/priorities/backlog/' + r.id, {}, { method: 'PATCH', body: { for_week: v } }); await load(); } catch (e) { failed(e); await load(); } }, 'prod-bl-week')),
      h('td', { class: 'prod-c' }, pick(r, 'product', [['', 'Choose'], ...Object.entries(PRODUCT_NAMES)])), h('td', { class: 'prod-c' }, pick(r, 'media_type', [['', 'Choose'], ['video', 'Video'], ['image', 'Image']])),
      h('td', { class: 'prod-c' }, field(r, 'cluster')), h('td', { class: 'prod-c' }, field(r, 'icp')), h('td', { class: 'prod-c' }, field(r, 'pain_point')),
      h('td', { class: 'prod-c prod-bl-storycell' }, h('input', { type: 'text', value: r.story || '', maxlength: 300, placeholder: 'One-sentence story', 'aria-label': 'Story', 'data-testid': 'prod-bl-story', onchange: async (e) => { try { await api('/api/priorities/backlog/' + r.id, {}, { method: 'PATCH', body: { story: e.target.value } }); toast('Saved.'); } catch (x) { failed(x); } } })),
      h('td', { class: 'prod-c' }, h('input', { type: 'text', value: r.note, maxlength: 500, 'aria-label': 'Note', 'data-testid': 'prod-bl-note', onchange: async (e) => { try { await api('/api/priorities/backlog/' + r.id, {}, { method: 'PATCH', body: { note: e.target.value } }); toast('Saved.'); } catch (x) { failed(x); } } })),
      h('td', { class: 'prod-c pri-sub' }, r.created_by || ''));
    tableHost.replaceChildren(
      ...[weeks.length > 1 ? h('div', { class: 'ctl-row' }, h('label', { class: 'ctl' }, h('span', { class: 'ctl-label' }, 'Show week'), h('select', { 'data-testid': 'prod-bl-weekfilter', onchange: (e) => { st.week = e.target.value; drawTable(); } },
        [h('option', { value: '' }, 'All weeks'), ...weeks.map((w) => h('option', { value: w, selected: w === st.week }, weekLabel(w)))]))) : null,
      shown.length ? h('div', { class: 'prod-scroll' }, h('table', { class: 'pri-table prod-table prod-bl-table', 'data-testid': 'prod-bl-table' },
        h('thead', null, h('tr', null, [h('th', { scope: 'col' }, all), ...['Week', 'Product', 'Image or video', 'Market cluster', 'ICP', 'Pain point', 'Story', 'Note', 'Added by'].map((t) => h('th', { scope: 'col' }, t))])), h('tbody', null, shown.map(row))))
        : h('p', { 'data-testid': 'prod-bl-empty' }, 'Nothing is waiting for a brief. In By cut, tick the cuts you decided to work on and add them here.'),
      goBoard ? h('p', { class: 'pri-sub' }, h('a', { href: href('/priorities/production', {}), 'data-testid': 'prod-bl-board' }, 'Open the Board')) : null].filter(Boolean));
  }

  return { init: load, refresh: load };
}
