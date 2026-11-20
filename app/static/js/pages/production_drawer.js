// The card drawer: opens from a board card or a list row. Right-hand panel on desktop, full-screen sheet on a phone.
// Header (title, status, stepper, next actions), then tabs: Details (edit fields), Comments (thread and composer), History (who changed what; app users only, from the assignment detail).
// Test ids: prod-drawer-*, prod-comment-*. The page passes `ops` (save, run an action, reload) and a `getCard()` that returns the freshest copy of the card.
import { h } from '../util.js';
import { api } from '../api.js';
import { assignmentPanel } from './priorities_panel.js';
import { STATUSES, STATUS_LABEL, statusOf, md, weekLabel, ticketTitle, richLines, variationList, previewOf, adCount, mediaOf, conceptName, factsEl, productName, formatLabel, dueInfo, nextActions, eventLine, launchedSummary, liveAdText, link, resultText, mondayOf, addDays, today, askDialog } from './production_common.js';

const when = (iso) => { const d = new Date(iso); return isNaN(d) ? '' : `${d.getMonth() + 1}/${d.getDate()} ${d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}`; };

export function openDrawer({ id, tab = 'details', ops, getCard, onClose, signal }) {
  let cur = tab;
  let comments = null;            // null = not loaded yet
  let history = null;
  let detail = null;
  let dirty = false;
  let copySel = '';               // the variation tab open in the Copy section (kept across a redraw)
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
        h('h2', { id: 'prod-drawer-title', 'data-testid': 'prod-drawer-title' }, conceptName(r)),
        h('button', { type: 'button', class: 'btn prod-drawer-close', 'data-testid': 'prod-drawer-close', 'aria-label': 'Close', onclick: close }, 'Close')),
      factsEl(r, 'prod-facts prod-drawer-facts'),
      launchedSummary(r) ? h('p', { class: 'pri-sub prod-live-summary', 'data-testid': 'prod-drawer-launched' }, launchedSummary(r)) : null,
      r.stage === 'dropped' ? h('p', { class: 'prod-pill prod-st-dropped', 'data-testid': 'prod-drawer-status' }, 'Dropped')
        : h('ol', { class: 'prod-stepper', 'data-testid': 'prod-drawer-stepper', 'aria-label': 'Status' }, STATUSES.map(([k, l], i) => h('li', { class: 'prod-step' + (i < idx ? ' prod-step-done' : '') + (i === idx ? ' prod-step-now' : ''), 'data-status': k, 'aria-current': i === idx ? 'step' : null, 'data-testid': 'prod-drawer-step' }, h('span', { class: 'prod-step-dot' }), h('span', { class: 'prod-step-label' }, l)))),
      h('div', { class: 'prod-drawer-meta' },
        h('span', { class: 'prod-pill prod-ps-' + status, 'data-testid': 'prod-drawer-status' }, STATUS_LABEL[status] || status),
        mediaOf(r) ? h('span', { class: 'prod-chip prod-chip-media', 'data-testid': 'prod-drawer-media' }, mediaOf(r)) : null,
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
  function urlField(label, input) {
    const a = h('a', { class: 'pri-sub', target: '_blank', rel: 'noopener noreferrer', 'data-testid': 'prod-open-link' }, 'Open ↗');
    const sync = () => { const v = input.value.trim(); if (/^https?:\/\//i.test(v)) { a.href = v; a.hidden = false; } else a.hidden = true; };
    input.addEventListener('input', sync); sync();
    return h('label', { class: 'prod-field' }, h('span', { class: 'pri-k' }, label, ' ', a), input);
  }
  function fieldInput(label, el, hint) { return h('label', { class: 'prod-field' }, h('span', { class: 'pri-k' }, label), el, hint ? h('span', { class: 'pri-sub' }, hint) : null); }
  function drawDetails(r) {
    const mon = mondayOf(today());
    const weeks = [-7, 0, 7, 14, 21, 28].map((n) => addDays(mon, n));
    if (!weeks.includes(r.for_week)) weeks.push(r.for_week);
    weeks.sort();
    const fmts = (ops.formats() || []).slice();
    if (r.format_code && !fmts.some((f) => f.code === r.format_code)) fmts.push({ code: r.format_code, name: r.format_name || '', length: r.format_length || '' });
    const mk = (tid, el) => { el.setAttribute('data-testid', tid); el.addEventListener('input', () => { dirty = true; }); el.addEventListener('change', () => { dirty = true; }); return el; };
    const cname = mk('prod-drawer-concept-name', h('input', { type: 'text', class: 'pri-name', maxlength: '200', placeholder: 'Name of the concept', value: r.concept_name || '', 'aria-label': 'Concept name' }));
    const kind = mk('prod-drawer-kind', h('select', { class: 'pri-name' }, [['', 'Not set'], ['new', 'New concept'], ['iteration', 'Iteration']].map(([v, l]) => h('option', { value: v, selected: (r.concept_kind || '') === v }, l))));
    const media = mk('prod-drawer-media', h('select', { class: 'pri-name' }, [['', 'Not chosen'], ['video', 'Video'], ['image', 'Image']].map(([v, l]) => h('option', { value: v, selected: (r.media_type || '') === v }, l))));
    const vendor = mk('prod-drawer-vendor', h('select', { class: 'pri-name' }, ops.vendors().map((v) => h('option', { value: v, selected: v === r.vendor }, v))));
    const week = mk('prod-drawer-week', h('select', { class: 'pri-name' }, weeks.map((w) => h('option', { value: w, selected: w === r.for_week }, weekLabel(w)))));
    const dueIn = mk('prod-drawer-due', h('input', { type: 'date', class: 'pri-name', value: r.due_date || '', 'aria-label': 'Due date' }));
    const dueRow = h('div', { class: 'prod-date-row' }, dueIn, h('button', { type: 'button', class: 'btn prod-small', 'data-testid': 'prod-drawer-due-clear', 'aria-label': 'Clear the due date', onclick: () => { dueIn.value = ''; dirty = true; dueIn.focus(); } }, 'Clear'));
    const prio = mk('prod-drawer-priority', h('select', { class: 'pri-name' }, [['normal', 'Normal'], ['high', 'High']].map(([v, l]) => h('option', { value: v, selected: (r.priority || 'normal') === v }, l))));
    const fmt = mk('prod-drawer-format', h('select', { class: 'pri-name' }, h('option', { value: '', selected: !r.format_code }, 'No format chosen'), fmts.map((f) => h('option', { value: f.code, selected: f.code === r.format_code }, formatLabel(f)))));
    const brief = mk('prod-drawer-brief', h('input', { type: 'text', class: 'pri-name', placeholder: 'https://', value: r.brief_url || '', maxlength: '2000' }));
    const creative = mk('prod-drawer-creative', h('input', { type: 'text', class: 'pri-name', placeholder: 'https://', value: r.creative_url || '', maxlength: '2000' }));
    const note = mk('prod-drawer-note', h('textarea', { class: 'pri-name', rows: '3', maxlength: '500', placeholder: 'Internal note (the vendor never sees this)' }, r.note || ''));
    const save = h('button', { type: 'button', class: 'btn primary', 'data-testid': 'prod-drawer-save', onclick: async () => {
      const body = {};
      if (cname.value.trim() !== (r.concept_name || '')) body.concept_name = cname.value.trim();
      if ((kind.value || null) !== (r.concept_kind || null)) body.concept_kind = kind.value || null;
      if ((media.value || null) !== (r.media_type || null)) body.media_type = media.value || null;
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
    // the work itself, in one block: a tab per variation (Original, Hook 2 ...), the first one already open as a player (Google Drive files and folders), and what is said in it underneath
    const vars = variationList(r);
    if (r.creative_url && vars.length && vars.every((v) => !v.url)) vars[0] = { ...vars[0], url: r.creative_url };      // a ticket whose variations carry no link of their own (Studio Flow) shows the creative link as its Original
    const items = vars.length ? vars : (r.creative_url ? [{ label: 'Creative', url: r.creative_url }] : []);
    const tmap = new Map();                                                          // label -> transcript, filled when the transcripts arrive
    const viewer = h('div', { class: 'prod-viewer', 'data-testid': 'prod-viewer' });
    const said = h('details', { class: 'prod-said', 'data-testid': 'prod-said', hidden: true }, h('summary', null, 'Transcript'), h('p', { class: 'prod-transcript-text' }));
    const tabEls = [];
    let sel = -1;
    const showSaid = () => { const t = sel >= 0 ? tmap.get(items[sel].label) : ''; said.hidden = !t; if (t) said.querySelector('p').textContent = t; };
    const show = (i) => {
      sel = i;
      const it = items[i]; const pv = previewOf(it.url);
      tabEls.forEach((bt, j) => bt.setAttribute('aria-selected', String(j === i)));
      viewer.replaceChildren(h('div', { class: 'prod-viewer-bar' }, h('strong', null, it.label), h('a', { href: it.url, target: '_blank', rel: 'noopener noreferrer', class: 'pri-sub' }, pv && pv.kind === 'frameio' ? 'Open in Frame.io' : /youtu|vimeo|loom/.test(it.url) ? 'Open the link' : 'Open in Drive')),
        pv ? h('iframe', { class: 'prod-viewer-frame prod-viewer-' + pv.kind, src: pv.src, title: it.label, allow: 'autoplay; fullscreen', allowfullscreen: 'true', referrerpolicy: pv.src.startsWith('https://www.youtube.com/') ? 'strict-origin-when-cross-origin' : 'no-referrer', sandbox: 'allow-scripts allow-same-origin allow-presentation allow-popups' })
          : h('p', { class: 'pri-sub' }, 'This kind of link cannot be shown here (Drive, YouTube, Vimeo, Loom and Frame.io links can). Use the Open link above.'));
      showSaid();
    };
    for (const [i, it] of items.entries()) tabEls.push(h('button', { type: 'button', role: 'tab', class: 'prod-vtab', 'aria-selected': 'false', 'data-testid': 'prod-hook', onclick: () => show(i) }, it.label));
    const preview = items.length ? h('div', { class: 'prod-field prod-preview', 'data-testid': 'prod-drawer-preview' },
      h('span', { class: 'pri-k' }, items.length > 1 ? `Variations (${items.length})` : 'Creative'),
      items.length > 1 ? h('div', { class: 'prod-vtabs', role: 'tablist' }, tabEls) : null, viewer, said) : null;
    if (items.length) show(0);
    api(`/api/priorities/assignments/${r.id}/transcripts`, {}, { signal }).then((d) => {
      const list = (d && d.transcripts) || [];
      for (const t of list) tmap.set(t.label, t.transcript);
      if (items.length === 1 && list.length === 1) tmap.set(items[0].label, list[0].transcript);
      showSaid();
    }).catch(() => { /* a failed load just leaves the transcripts out */ });
    const noteView = r.note ? h('div', { class: 'prod-field' }, h('span', { class: 'pri-k' }, 'Note, as written'), h('div', { class: 'prod-richnote', 'data-testid': 'prod-drawer-note-view' }, richLines(r.note))) : null;
    pane.replaceChildren(h('div', { class: 'prod-drawer-form', 'data-testid': 'prod-drawer-details' },
      preview, adCopy(r), liveAds(r), noteView,
      fieldInput('Concept name', cname), fieldInput('New vs Iteration', kind), fieldInput('Image or video', media),
      h('dl', { class: 'prod-about', 'data-testid': 'prod-about' }, [['Vendor', r.vendor], ['Week of', weekLabel(r.for_week).replace('Week of ', '')], ['ICP', r.icp], ['Pain point', r.pain_point], ['Market Cluster', r.cluster], ['Line', r.line_code]].filter(([, v]) => v).map(([k, v]) => h('div', { class: 'prod-fact' }, h('dt', null, k + ':'), h('dd', null, v)))),
      fieldInput('Vendor', vendor), fieldInput('Week of', week), fieldInput('Due date', dueRow, 'The vendor sees this date. Clear it for no due date.'), fieldInput('Priority', prio), fieldInput('Format', fmt),
      urlField('Brief link', brief), urlField('Creative link', creative), fieldInput('Note', note),
      r.vendor_note ? h('div', { class: 'prod-field' }, h('span', { class: 'pri-k' }, 'Vendor note'), h('p', { class: 'prod-drawer-vnote', 'data-testid': 'prod-drawer-vnote' }, r.vendor_note)) : null,
      ads.length || resText ? h('div', { class: 'prod-field' }, h('span', { class: 'pri-k' }, 'Ads'), h('p', { class: 'prod-drawer-vnote' }, [ads.join(', '), resText].filter(Boolean).join('. '))) : null,
      h('div', { class: 'prod-drawer-btns' }, save,
        r.stage === 'dropped' ? null : h('button', { type: 'button', class: 'btn', 'data-testid': 'prod-drawer-drop', onclick: () => dropIt(r) }, 'Drop')),
      more));
  }

  // ---- ad copy: primary texts and headlines per variation, plain text with a Copy button on each. Saved on its own (Save copy), without touching the other fields of the form.
  function adCopy(r) {
    const host = h('div', { class: 'prod-field prod-copy', 'data-testid': 'prod-copy' });
    const MAXN = 5;
    const drafts = new Map();                                   // label -> { primary: [], heads: [] } while being edited
    const labelsOf = (c) => { const l = (Array.isArray(c.variations) ? c.variations : []).map((v) => v.label).filter(Boolean); return l.length ? l : ['Original']; };
    const saved = (c, label) => { const m = c.ad_copy || {}; const k = Object.keys(m).find((x) => x.toLowerCase() === label.toLowerCase()); return k ? m[k] : {}; };
    const draftOf = (c, label) => { if (!drafts.has(label)) { const s = saved(c, label); drafts.set(label, { primary: (s.primary_texts || []).slice(), heads: (s.headlines || []).slice() }); } return drafts.get(label); };
    async function copyText(text, btn) {
      let ok = false;
      try { await navigator.clipboard.writeText(text); ok = true; } catch (e) {
        const t = h('textarea', { style: 'position:fixed;opacity:0' }, text); document.body.append(t); t.select();
        try { ok = document.execCommand('copy'); } catch (e2) { ok = false; } t.remove();
      }
      const was = btn.textContent; btn.textContent = ok ? 'Copied' : 'Press Ctrl+C'; setTimeout(() => { btn.textContent = was; }, 1500);
    }
    function list(c, label, kind, title, max, rows) {
      const d = draftOf(c, label); const arr = kind === 'primary' ? d.primary : d.heads;
      const ul = h('ul', { class: 'prod-copy-list' }, arr.map((t, i) => {
        const ta = h('textarea', { class: 'pri-name prod-copy-text', rows: String(rows), maxlength: String(max), 'aria-label': `${title.slice(0, -1)} ${i + 1}`, 'data-testid': 'prod-copy-' + kind });
        ta.value = t;
        const n = h('span', { class: 'pri-sub prod-copy-n' }, `${t.length} of ${max}`);
        ta.addEventListener('input', () => { arr[i] = ta.value; n.textContent = `${ta.value.length} of ${max}`; });
        const cp = h('button', { type: 'button', class: 'btn prod-small', 'data-testid': 'prod-copy-btn', 'aria-label': `Copy ${title.toLowerCase().slice(0, -1)} ${i + 1}`, onclick: () => copyText(ta.value, cp) }, 'Copy');
        const rm = h('button', { type: 'button', class: 'btn prod-small', 'data-testid': 'prod-copy-remove', 'aria-label': `Remove ${title.toLowerCase().slice(0, -1)} ${i + 1}`, onclick: () => { arr.splice(i, 1); redraw(); } }, 'Remove');
        return h('li', { class: 'prod-copy-item' }, h('span', { class: 'pri-sub' }, `${title.slice(0, -1)} ${i + 1}`), ta, h('div', { class: 'prod-copy-bar' }, n, cp, rm));
      }));
      const add = h('button', { type: 'button', class: 'btn prod-small', 'data-testid': 'prod-copy-add-' + kind, disabled: arr.length >= MAXN, onclick: () => { arr.push(''); redraw(); const t = host.querySelectorAll(`[data-testid=prod-copy-${kind}]`); if (t.length) t[t.length - 1].focus(); } }, `Add ${title.toLowerCase().slice(0, -1)}`);
      return h('div', { class: 'prod-copy-group' }, h('strong', { class: 'prod-copy-title' }, `${title} (${arr.length} of ${MAXN})`), arr.length ? ul : h('p', { class: 'pri-sub', 'data-testid': 'prod-copy-none-' + kind }, 'None yet.'), add);
    }
    function redraw() { host.replaceChildren(...build(getCard() || r)); }
    function build(c) {
      const labels = labelsOf(c);
      if (!labels.some((l) => l.toLowerCase() === copySel.toLowerCase())) copySel = labels[0];
      const label = labels.find((l) => l.toLowerCase() === copySel.toLowerCase());
      const tabs = labels.length > 1 ? h('div', { class: 'prod-vtabs', role: 'tablist' }, labels.map((l) => {
        const has = (saved(c, l).primary_texts || []).length + (saved(c, l).headlines || []).length > 0;
        return h('button', { type: 'button', role: 'tab', class: 'prod-vtab', 'aria-selected': String(l === label), 'data-testid': 'prod-copy-tab', 'data-label': l, onclick: () => { copySel = l; redraw(); } }, l + (has ? '' : ' (none)'));
      })) : null;
      const status = h('span', { class: 'pri-sub', role: 'status', 'data-testid': 'prod-copy-msg' });
      const save = h('button', { type: 'button', class: 'btn primary', 'data-testid': 'prod-copy-save', onclick: async () => {
        const d = draftOf(c, label);
        const clean = (a) => a.map((t) => t.trim()).filter(Boolean);
        save.disabled = true; status.textContent = '';
        const ok = await ops.saveCopy(getCard() || c, label, { primary_texts: clean(d.primary), headlines: clean(d.heads) });
        save.disabled = false;
        if (ok) { drafts.delete(label); history = null; say('Copy saved.'); redraw(); } else { status.textContent = ops.lastError() || 'Could not save.'; status.classList.add('pri-err'); }
      } }, 'Save copy');
      return [h('span', { class: 'pri-k' }, 'Ad copy'), tabs, list(c, label, 'primary', 'Primary texts', 2000, 5), list(c, label, 'head', 'Headlines', 255, 2), h('div', { class: 'prod-drawer-btns' }, save, status)];
    }
    host.replaceChildren(...build(r));
    return host;
  }

  // ---- launched ads: the live Meta ad of each variation, and the concept's own list. Saved on their own (blur / Enter), without touching the other fields of the form.
  function liveAds(r) {
    const host = h('div', { class: 'prod-live', 'data-testid': 'prod-live' });
    const failed = (text) => say(text || ops.lastError() || 'Could not save.', true);
    async function saveAds(body) {
      say('');
      const ok = await ops.save(getCard(), body);
      if (!ok) { failed(); return false; }
      history = null;
      if (await ops.reload()) { const c = getCard(); if (c) { drawHead(c); host.replaceChildren(...build(c)); } }
      say('Saved.');
      return true;
    }
    const infoOf = (a) => h('div', { class: 'pri-sub prod-live-info', 'data-testid': 'prod-live-info' }, liveAdText(a.meta));
    function varRow(c, v, i) {
      const entry = (c.launched_ads || []).find((a) => a.source === 'variation' && a.variation_index === i);
      const input = h('input', { type: 'text', class: 'pri-name prod-live-input', 'data-testid': 'prod-live-input', 'data-index': String(i), maxlength: '2000', placeholder: 'Paste Meta ad ID or Ads Manager link', 'aria-label': `Launched ad for ${v.label}`, value: v.ad_id || '' });
      const send = async (value) => {
        const fresh = (getCard().variations || []).map((x) => ({ ...x }));
        if (!fresh[i]) return;
        if (value) fresh[i].ad_id = value; else delete fresh[i].ad_id;
        delete fresh[i].ad_url;
        await saveAds({ variations: fresh });
      };
      const commit = () => { const val = input.value.trim(); if (val !== (v.ad_id || '')) send(val); };
      input.addEventListener('blur', commit);
      input.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); input.blur(); } });
      const clear = h('button', { type: 'button', class: 'btn prod-small prod-live-clear', 'data-testid': 'prod-live-clear', 'aria-label': `Clear the launched ad for ${v.label}`, hidden: !v.ad_id, onmousedown: (e) => e.preventDefault(), onclick: () => { input.value = ''; send(''); } }, '×');
      return h('li', { class: 'prod-live-row', 'data-testid': 'prod-live-row', 'data-index': String(i) },
        h('strong', { class: 'prod-live-label' }, v.label), h('div', { class: 'prod-live-edit' }, input, clear), entry ? infoOf(entry) : null);
    }
    function ticketBox(c) {
      const mine = (c.launched_ads || []).filter((a) => a.source === 'ticket');
      const input = h('input', { type: 'text', class: 'pri-name prod-live-input', 'data-testid': 'prod-ticket-live-input', maxlength: '2000', placeholder: 'Paste Meta ad ID or Ads Manager link', 'aria-label': 'Add a launched ad for this concept' });
      const add = async () => { const val = input.value.trim(); if (!val) return; const ids = (getCard().launched_ad_ids || []).slice(); await saveAds({ launched_ad_ids: [...ids, val] }); };
      input.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); add(); } });
      return h('div', { class: 'prod-field prod-live-ticket', 'data-testid': 'prod-ticket-live' },
        h('span', { class: 'pri-k' }, 'Launched ads for this concept'),
        mine.length ? h('ul', { class: 'prod-live-list' }, mine.map((a) => h('li', { class: 'prod-live-row', 'data-testid': 'prod-ticket-live-row', 'data-ad': a.ad_id },
          h('div', { class: 'prod-live-edit' }, h('code', { class: 'prod-live-id' }, a.ad_id), h('button', { type: 'button', class: 'btn prod-small', 'data-testid': 'prod-ticket-live-remove', 'aria-label': `Remove launched ad ${a.ad_id}`, onclick: () => saveAds({ launched_ad_ids: (getCard().launched_ad_ids || []).filter((x) => x !== a.ad_id) }) }, '×')), infoOf(a))))
          : h('p', { class: 'pri-sub', 'data-testid': 'prod-ticket-live-none' }, 'None added yet.'),
        h('div', { class: 'prod-live-edit' }, input, h('button', { type: 'button', class: 'btn prod-small', 'data-testid': 'prod-ticket-live-add', onclick: add }, 'Add')));
    }
    function build(c) {
      const vs = Array.isArray(c.variations) ? c.variations : [];
      return [
        vs.length ? h('div', { class: 'prod-field', 'data-testid': 'prod-live-vars' }, h('span', { class: 'pri-k' }, 'Launched ad for each variation'), h('ul', { class: 'prod-live-list' }, vs.map((v, i) => varRow(c, v, i)))) : null,
        ticketBox(c)];
    }
    host.replaceChildren(...build(r));
    return host;
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
      try {
        detail = null;
        const d = await loadDetail();
        history = d.history || (d.assignment && d.assignment.history) || null;
        if (!Array.isArray(history)) {            // the detail payload carries no history: the List endpoint rows do (app users only), so read this card's row from it
          const r = getCard();
          const l = await api('/api/priorities/assignments', { week: r && r.for_week, vendor: r && r.vendor, include_closed: 1 }, { signal });
          const row = (l.rows || []).find((x) => x.id === Number(id));
          history = (row && row.history) || [];
        }
      } catch (e) { if (e && e.name === 'AbortError') return; history = []; say(e.message || 'Could not load history.', true); }
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
