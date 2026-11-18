// Theme previews (/theme-previews): make a preview copy of the store theme, pick a file, edit it, save it to the preview. Previews only: this page cannot go live.
// Routes: /api/theme/* (app/theme_api.py). Everything shown is set with textContent or input values, never HTML. Class names and test ids start with `tp-`.
import { h } from '../util.js';
import { api, ApiError } from '../api.js';
import { showControls } from '../controls.js';
import { pageHeader } from '../ui/pagehead.js';

const NOTE = 'Changes here go to a preview only. Going live is done in Shopify admin by Josh.';
const say = (e) => (e instanceof ApiError ? e.message : 'Something went wrong. Try again.');

export async function themePreviewsPage(ctx) {
  showControls(false);
  document.title = 'Theme previews · Lonvera';
  const { signal } = ctx;
  const header = pageHeader({ title: 'Theme previews', testid: 'tp-head', description: 'Edit the store theme in a copy that only you can see by link. The live theme is never touched.' });
  const note = h('p', { class: 'note-line tp-note', 'data-testid': 'tp-note' }, NOTE);
  const msg = h('div', { class: 'tp-msg', role: 'status', 'data-testid': 'tp-msg' });
  const setMsg = (t, bad) => { msg.textContent = t || ''; msg.className = 'tp-msg' + (bad ? ' errorbox' : ''); };

  const all = await api('/api/status', null, { signal });                 // its `theme` key says enabled / can_edit without a 404 while the feature is off
  const st = all.theme || { enabled: false };
  if (!ctx.alive()) return;
  if (!st.enabled || !st.can_edit) {
    ctx.mount(h('div', { class: 'page tp-page', 'data-testid': 'tp-page' }, header, h('p', { class: 'tl-prose', 'data-testid': 'tp-off' }, 'Theme previews are not available to you.')));
    return;
  }

  const listBox = h('div', { 'data-testid': 'tp-list' });
  const filesBox = h('div', { 'data-testid': 'tp-filebox' });
  const editBox = h('div', { 'data-testid': 'tp-editbox' });
  const nameIn = h('input', { type: 'text', maxlength: '80', placeholder: 'Name, for example Bigger buy button', 'aria-label': 'Preview name', 'data-testid': 'tp-name' });
  const newBtn = h('button', { type: 'button', class: 'btn primary', 'data-testid': 'tp-new' }, 'New preview');
  let current = null;

  async function loadList() {
    const d = await api('/api/theme/themes', null, { signal });
    if (!ctx.alive()) return;
    const rows = d.previews.map((p) => h('tr', { 'data-id': p.id },
      h('td', null, p.name), h('td', null, p.created_by), h('td', null, String(p.created_at).slice(0, 10)),
      h('td', null, h('a', { href: p.preview_url, target: '_blank', rel: 'noopener', 'data-testid': 'tp-open-' + p.id }, 'Open preview')),
      h('td', null, h('button', { type: 'button', class: 'btn small', 'data-testid': 'tp-pick-' + p.id, onclick: () => pick(p) }, 'Edit files'))));
    listBox.replaceChildren(
      h('p', { class: 'muted', 'data-testid': 'tp-live' }, `Live theme (read only): ${d.live.name}`),
      d.previews.length
        ? h('div', { class: 'tablebox' }, h('div', { class: 'tablewrap' }, h('table', { class: 'dt' },
          h('thead', null, h('tr', null, ['Preview', 'Made by', 'Made on', 'Link', ''].map((t) => h('th', null, t)))), h('tbody', null, rows))))
        : h('p', { class: 'tl-prose', 'data-testid': 'tp-empty' }, 'No previews yet. Make one above.'));
  }

  async function pick(p) {
    current = p; editBox.replaceChildren(); setMsg('Loading files...');
    try {
      const d = await api(`/api/theme/previews/${p.id}/files`, null, { signal });
      if (!ctx.alive()) return;
      setMsg('');
      filesBox.replaceChildren(h('h2', { class: 'tp-h2' }, `Files in ${p.name}`),
        h('div', { class: 'tablebox tp-files' }, h('div', { class: 'tablewrap' }, h('table', { class: 'dt', 'data-testid': 'tp-files' }, h('thead', null, h('tr', null, h('th', null, 'File'))),
          h('tbody', null, d.files.map((f) => h('tr', null, h('td', null, h('button', { type: 'button', class: 'tp-link', 'data-key': f.key, onclick: () => openFile(f.key) }, f.key)))))))));
    } catch (e) { if (e && e.name !== 'AbortError') setMsg(say(e), true); }
  }

  async function openFile(key) {
    setMsg('Loading file...');
    try {
      const d = await api(`/api/theme/previews/${current.id}/files`, { key }, { signal });
      if (!ctx.alive()) return;
      setMsg('');
      const ta = h('textarea', { class: 'tp-editor', spellcheck: 'false', 'aria-label': 'File text', 'data-testid': 'tp-text' });
      ta.value = d.content;
      const save = h('button', { type: 'button', class: 'btn primary', 'data-testid': 'tp-save' }, 'Save to preview');
      save.addEventListener('click', async () => {
        save.disabled = true; setMsg('Saving...');
        try { await api(`/api/theme/previews/${current.id}/files`, null, { method: 'PUT', body: { key, content: ta.value } }); setMsg(`Saved ${key} to the preview.`); } catch (e) { setMsg(say(e), true); }
        save.disabled = false;
      });
      editBox.replaceChildren(h('h2', { class: 'tp-h2' }, key), ta, h('p', null, save));
    } catch (e) { if (e && e.name !== 'AbortError') setMsg(say(e), true); }
  }

  newBtn.addEventListener('click', async () => {
    if (!nameIn.value.trim()) { setMsg('Give the preview a name first.', true); return; }
    newBtn.disabled = true; setMsg('Making the preview. This can take a minute...');
    try { await api('/api/theme/previews', null, { method: 'POST', body: { name: nameIn.value.trim() } }); nameIn.value = ''; setMsg('Preview made.'); await loadList(); } catch (e) { setMsg(say(e), true); }
    newBtn.disabled = false;
  });

  ctx.mount(h('div', { class: 'page tp-page', 'data-testid': 'tp-page' }, header, note,
    h('div', { class: 'tp-new' }, nameIn, newBtn), msg, listBox, filesBox, editBox));
  try { await loadList(); } catch (e) { if (e && e.name !== 'AbortError') setMsg(say(e), true); }
}
