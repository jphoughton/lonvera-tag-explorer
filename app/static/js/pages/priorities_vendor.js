// The public vendor page (/priorities/vendor/<token>): login-exempt, no app navigation. Moved out of priorities_production.js unchanged.
// Everything the user typed or the API returned goes in through textContent / h(); links are built with h('a', {href, target: '_blank', rel: 'noopener noreferrer'}), never innerHTML.
import { h } from '../util.js';
import { api } from '../api.js';
import { showControls } from '../controls.js';
import { expander } from './priorities_panel.js';
import { weekLabel, productName, stageLabel, formatLabel, rowFormat, resultText, link, short } from './production_common.js';

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
    document.title = title + ' · Lonvera';
    const todo = rows.filter((r) => r.stage !== 'launched' && r.stage !== 'dropped' && r.stage !== 'ready').length;
    root.replaceChildren(...[h('header', { class: 'prod-vhead' }, h('h1', null, title),
      rows.length ? h('p', { class: 'prod-vhelp', 'data-testid': 'prod-vhelp' }, `${todo} to make. For each one: open the brief, make the creative, paste the link to your files, then tap Ready to launch.`) : null),
      rows.length ? null : h('p', { 'data-testid': 'prod-empty' }, 'Nothing to make right now.'),
      ...weeks.map((w) => h('section', { class: 'prod-week', 'data-testid': 'prod-weekgroup' }, h('h2', null, weekLabel(w)), rows.filter((r) => r.for_week === w).map(card)))].filter(Boolean));
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
      h('div', { class: 'prod-ctop' }, h('h3', null, x.nameButton(r.pain_point || r.cluster)), h('span', { class: 'prod-pill prod-st-' + r.stage, 'data-testid': 'prod-stage' }, stageLabel(r))),
      h('p', { class: 'pri-sub' }, [productName(r.product), r.cluster, r.icp].filter(Boolean).join(' / ')),
      h('div', null, x.button()), host,
      r.story ? h('p', { class: 'prod-story', 'data-testid': 'prod-story' }, r.story) : null,
      h('dl', { class: 'prod-dl' },
        dt('Brief', r.brief_url ? link(r.brief_url, short(r.brief_url), { 'data-testid': 'prod-brief_url' }) : 'Not added yet'),
        dt('Format', h('span', { 'data-testid': 'prod-format-text' }, rowFormat(r) ? formatLabel(rowFormat(r)) : 'Not chosen')),
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
