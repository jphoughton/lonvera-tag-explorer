// The public vendor page (/priorities/vendor/<token>): login-exempt, no app navigation. "My tasks": To start, In production, In review, Done.
// Everything the user typed or the API returned goes in through textContent / h(); links are built with h('a', {href, target: '_blank', rel: 'noopener noreferrer'}), never innerHTML.
// A row without a `status` field (the older payload) still renders with the older buttons (Ready to launch / Not ready yet), so the page works against either server.
import { h } from '../util.js';
import { api } from '../api.js';
import { showControls } from '../controls.js';
import { expander } from './priorities_panel.js';
import { weekLabel, ticketTitle, variationGroups, variationLabel, productName, stageLabel, formatLabel, rowFormat, resultText, link, short } from './production_common.js';

// The one place the vendor routes are spelled: PATCH and detail are `${base}/<id>`, comments are `${base}/<id>/comments`.
const vendorBase = (tok) => '/api/priorities/vendor/' + tok;

const SECTIONS = [
  { key: 'todo', title: 'To start', count: 'to start', empty: 'Nothing waiting to start.' },
  { key: 'prod', title: 'In production', count: 'in production', empty: 'Nothing in production.' },
  { key: 'review', title: 'In review', count: 'in review', empty: 'Nothing waiting for review.', sub: 'Waiting for the team to look at these.' },
  { key: 'done', title: 'Done', count: 'done', empty: 'Nothing done yet.' },
];
const STATUS_LABEL = { assigned: 'Assigned', brief_sent: 'Brief sent', in_production: 'In production', in_review: 'In review', ready: 'Ready to launch', launched: 'Launched' };
const SECTION_OF = { assigned: 'todo', brief_sent: 'todo', in_production: 'prod', in_review: 'review', ready: 'done', launched: 'done' };
// older rows carry only the derived stage
const STATUS_OF_STAGE = { ideation: 'assigned', assigned: 'assigned', brief: 'brief_sent', brief_sent: 'brief_sent', creative_in: 'in_production', ready: 'ready', launched: 'launched' };
export const statusOf = (r) => r.status || STATUS_OF_STAGE[r.stage] || 'assigned';

const pad = (n) => String(n).padStart(2, '0');
function dayDiff(iso) {                                              // whole days from today (local) to a YYYY-MM-DD date; negative = past
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso || '');
  if (!m) return null;
  const t = new Date(); const a = Date.UTC(t.getFullYear(), t.getMonth(), t.getDate()); const b = Date.UTC(+m[1], +m[2] - 1, +m[3]);
  return Math.round((b - a) / 86400000);
}
export function dueChip(r) {
  if (!r.due_date) return null;
  const st = statusOf(r);
  const d = dayDiff(r.due_date);
  const open = st !== 'ready' && st !== 'launched';
  const late = open && (r.overdue === true || (r.overdue == null && d != null && d < 0));
  const soon = open && !late && d != null && d <= 2;
  const dt = new Date(r.due_date.slice(0, 10) + 'T12:00:00');
  const day = isNaN(dt) ? r.due_date : dt.toLocaleDateString(undefined, { weekday: 'short', month: 'numeric', day: 'numeric' });
  let text = 'Due ' + day;
  if (late && d != null) text = `Overdue by ${-d} day${d === -1 ? '' : 's'}`;
  else if (open && d === 0) text = 'Due today';
  else if (open && d === 1) text = 'Due tomorrow';
  return h('span', { class: 'prod-v-chip prod-v-due' + (late ? ' is-late' : soon ? ' is-soon' : ''), 'data-testid': 'prod-v-due', 'data-state': late ? 'overdue' : soon ? 'soon' : 'ok' }, text);
}
const when = (iso) => { const d = new Date(iso); return isNaN(d) ? '' : d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }); };
const isChanges = (c) => c && c.author_kind === 'app' && /^\s*changes requested/i.test(c.body || '');

// ---------------------------------------------------------------------------------------------------------------- vendor page (login-exempt, no app navigation)
export async function vendorPage(ctx) {
  showControls(false);
  const tok = encodeURIComponent(ctx.match[1]);
  const base = vendorBase(tok);
  let d;
  try { d = await api(base, {}, { signal: ctx.signal }); } catch (e) {
    if (e && e.status === 404) { ctx.mount(h('div', { class: 'page pri-page prod-page' }, h('h1', null, 'Link not found'), h('p', { 'data-testid': 'prod-empty' }, 'This link is not valid any more. Ask for a new one.'))); return; }
    throw e;
  }
  const root = h('div', { class: 'page pri-page prod-page prod-vendor', 'data-testid': 'prod-vendor-page' });
  const openIds = new Set();
  const openThreads = new Set();
  const comments = new Map();                                        // assignment id -> [comment]
  const drafts = new Map();                                          // assignment id -> {creative, note, comment}: typed text survives a redraw
  const draft = (id) => { let x = drafts.get(id); if (!x) { x = {}; drafts.set(id, x); } return x; };
  const modern = (r) => r.status != null;

  async function loadComments(rows) {
    await Promise.all(rows.filter((r) => modern(r) && (r.comment_count || 0) > 0).map(async (r) => {
      try { const j = await api(`${base}/${r.id}/comments`, {}, { signal: ctx.signal }); comments.set(r.id, j.comments || []); } catch (e) { /* the card still works without its thread */ }
    }));
  }
  const reload = async () => { d = await api(base, {}); await loadComments(d.rows || []); draw(); };

  const draw = () => {
    const rows = d.rows || [];
    const title = `${d.vendor}: concepts to make`;
    document.title = title + ' · Lonvera';
    const by = Object.fromEntries(SECTIONS.map((s) => [s.key, []]));
    rows.forEach((r) => by[SECTION_OF[statusOf(r)] || 'todo'].push(r));
    const counts = SECTIONS.filter((s) => by[s.key].length).map((s) => `${by[s.key].length} ${s.count}`);
    root.replaceChildren(...[
      h('header', { class: 'prod-vhead' },
        h('p', { class: 'prod-v-kicker' }, 'My tasks'),
        h('h1', null, title),
        rows.length ? h('p', { class: 'prod-vhelp', 'data-testid': 'prod-vhelp' }, counts.join(', ') + '.') : null,
        rows.length ? h('p', { class: 'prod-v-howto' }, 'For each one: open the brief, make the creative, paste the link to your files, then send it for review.') : null),
      rows.length ? null : h('p', { 'data-testid': 'prod-empty' }, 'Nothing to make right now.'),
      ...(rows.length ? SECTIONS.map((s) => section(s, by[s.key])) : [])].filter(Boolean));
  };

  function section(s, rows) {
    const weeks = [...new Set(rows.map((r) => r.for_week))].sort().reverse();
    return h('section', { class: 'prod-vsec', 'data-testid': 'prod-v-section', 'data-section': s.key },
      h('h2', { class: 'prod-vsec-h' }, s.title, h('span', { class: 'prod-vsec-n', 'data-testid': 'prod-v-section-count' }, String(rows.length))),
      s.sub && rows.length ? h('p', { class: 'prod-v-sub' }, s.sub) : null,
      rows.length ? null : h('p', { class: 'prod-v-none' }, s.empty),
      ...weeks.map((w) => h('div', { class: 'prod-week', 'data-testid': 'prod-weekgroup' },
        weeks.length > 1 ? h('h3', { class: 'prod-wk' }, weekLabel(w)) : null,
        rows.filter((r) => r.for_week === w).map(card))));
  }

  const flash = (id, text, bad) => { const el = root.querySelector(`[data-id="${id}"] [data-testid=prod-saved]`); if (el) { el.textContent = text; el.classList.toggle('pri-err', !!bad); } };
  const patch = async (r, body, saved) => {
    try { await api(`${base}/${r.id}`, {}, { method: 'PATCH', body }); drafts.delete(r.id); await reload(); flash(r.id, saved); } catch (e) { flash(r.id, (e && e.message) || 'Could not save.', true); }
  };
  const postComment = async (r, text) => {
    try { await api(`${base}/${r.id}/comments`, {}, { method: 'POST', body: { body: text } }); draft(r.id).comment = ''; openThreads.add(r.id); await reload(); flash(r.id, 'Comment sent'); } catch (e) { flash(r.id, (e && e.message) || 'Could not send the comment.', true); }
  };

  function thread(r) {
    const list = comments.get(r.id) || [];
    const n = Math.max(list.length, r.comment_count || 0);
    const ta = h('textarea', { class: 'pri-name prod-in prod-v-ta', rows: '2', maxlength: '2000', 'aria-label': 'Write a comment', 'data-testid': 'prod-v-comment-input', placeholder: 'Write a comment for the team' });
    ta.value = draft(r.id).comment || '';
    ta.addEventListener('input', () => { draft(r.id).comment = ta.value; });
    const send = h('button', { type: 'button', class: 'btn', 'data-testid': 'prod-v-comment-post', onclick: () => { const t = ta.value.trim(); if (!t) { flash(r.id, 'Write a comment first.', true); return; } postComment(r, t); } }, 'Send comment');
    const last = [...list].reverse().find((c) => c.author_kind === 'app');
    const asked = statusOf(r) === 'in_production' && (r.changes_requested === true || isChanges(last));
    const det = h('details', { class: 'prod-v-thread', 'data-testid': 'prod-v-thread', ontoggle: (e) => { if (e.target.open) openThreads.add(r.id); else openThreads.delete(r.id); } },
      h('summary', null, `Comments (${n})`),
      list.length ? h('ul', { class: 'prod-v-clist' }, list.map((c) => h('li', { class: 'prod-v-comment ' + (c.author_kind === 'app' ? 'is-team' : 'is-you'), 'data-testid': 'prod-v-comment', 'data-kind': c.author_kind },
        h('div', { class: 'prod-v-cmeta' }, h('strong', null, c.author_kind === 'app' ? (c.author_name || 'Team') : 'You'), ' ', h('span', null, when(c.created_at))),
        h('div', { class: 'prod-v-cbody' }, c.body)))) : h('p', { class: 'prod-v-none' }, 'No comments yet.'),
      h('div', { class: 'prod-v-compose' }, ta, send));
    if (openThreads.has(r.id) || asked) det.open = true;
    return { det, asked, last };
  }

  function card(r) {
    const st = statusOf(r);
    const m = modern(r);
    const dr = draft(r.id);
    const link_ = h('input', { type: 'text', class: 'pri-name prod-in', 'aria-label': 'Creative link', 'data-testid': 'prod-in-creative', placeholder: 'https://', value: dr.creative != null ? dr.creative : (r.creative_url || ''), maxlength: '2000' });
    const note = h('input', { type: 'text', class: 'pri-name prod-in', 'aria-label': 'Note', 'data-testid': 'prod-in-note', placeholder: 'Anything we should know', value: dr.note != null ? dr.note : (r.vendor_note || ''), maxlength: '1000' });
    link_.addEventListener('input', () => { dr.creative = link_.value; syncSubmit(); });
    note.addEventListener('input', () => { dr.note = note.value; });
    const res = resultText(r);
    const dt = (l, ...v) => [h('dt', { class: 'pri-flabel' }, l), h('dd', null, ...v)];
    const host = h('div', { class: 'prod-xd prod-xd-v', 'data-testid': 'prod-detail' });
    const x = expander({ key: 'v' + r.id, fetchDetail: () => api(`${base}/${r.id}/detail`, {}, { signal: ctx.signal }), host, startOpen: openIds.has(r.id), onToggle: (o) => { if (o) openIds.add(r.id); else openIds.delete(r.id); } });
    const fmt = rowFormat(r);
    const done = st === 'ready' || st === 'launched';
    const label = r.status_label || (m ? STATUS_LABEL[st] : stageLabel(r));
    const saveBody = () => ({ creative_url: link_.value.trim() || null, vendor_note: note.value.trim() });

    // buttons: Save always; then what this status allows
    const btns = [h('button', { type: 'button', class: 'btn' + (done ? ' primary' : ' primary'), 'data-testid': 'prod-save', onclick: () => patch(r, saveBody(), 'Saved') }, 'Save')];
    let submit = null, hint = null;
    if (m) {
      if (st === 'assigned' || st === 'brief_sent') btns.unshift(h('button', { type: 'button', class: 'btn primary', 'data-testid': 'prod-v-start', onclick: () => patch(r, { status: 'in_production' }, 'Started') }, 'Start'));
      if (st === 'in_production') {
        submit = h('button', { type: 'button', class: 'btn primary', 'data-testid': 'prod-v-submit', onclick: () => {
          if (!link_.value.trim()) return;
          patch(r, { ...saveBody(), status: 'in_review' }, 'Sent for review');
        } }, 'Submit for review');
        hint = h('p', { class: 'prod-v-hint', role: 'status', 'data-testid': 'prod-v-hint' }, 'Add the link to your creative above, then you can submit it for review.');
        btns.unshift(submit);
      }
      if (st === 'in_review') btns.unshift(h('button', { type: 'button', class: 'btn', 'data-testid': 'prod-v-pullback', onclick: () => patch(r, { status: 'in_production' }, 'Pulled back to production') }, 'Pull back'));
    } else {                                                          // older payload: the older buttons
      if (r.creative_url && !done) btns.push(h('button', { type: 'button', class: 'btn', 'data-testid': 'prod-vready', onclick: () => patch(r, { ready: true }, 'Marked ready to launch') }, 'Ready to launch'));
      if (r.ready_at && st === 'ready') btns.push(h('button', { type: 'button', class: 'btn', 'data-testid': 'prod-vunready', onclick: () => patch(r, { ready: false }, 'Ready to launch removed') }, 'Not ready yet'));
    }
    function syncSubmit() {                                           // the submit button is live only with a creative link (saved or typed); it keeps the older prod-vready id while live
      if (!submit) return;
      const has = !!link_.value.trim();
      submit.disabled = !has;
      submit.setAttribute('aria-disabled', String(!has));
      submit.dataset.testid = has ? 'prod-vready' : 'prod-v-submit';
      submit.setAttribute('data-testid', has ? 'prod-vready' : 'prod-v-submit');
      if (hint) hint.hidden = has;
    }
    btns.push(h('span', { class: 'prod-saved', role: 'status', 'data-testid': 'prod-saved' }));
    queueMicrotask(syncSubmit);

    const th = m ? thread(r) : null;
    const banner = th && th.asked && th.last ? h('div', { class: 'prod-v-changes', role: 'alert', 'data-testid': 'prod-v-changes' },
      h('strong', null, 'Changes requested'), h('p', null, th.last.body.replace(/^\s*changes requested:?\s*/i, '') || th.last.body), h('span', { class: 'prod-v-cmeta' }, `${th.last.author_name || 'Team'}, ${when(th.last.created_at)}`)) : null;

    return h('article', { class: 'prod-card prod-vcard', 'data-testid': 'prod-card', 'data-id': r.id, 'data-stage': r.stage, 'data-status': st },
      h('div', { class: 'prod-ctop' }, h('h3', null, x.nameButton(ticketTitle(r))), h('span', { class: 'prod-pill prod-st-' + (r.stage || st), 'data-testid': 'prod-stage' }, label)),
      h('p', { class: 'pri-sub' }, [productName(r.product), r.cluster, r.icp].filter(Boolean).join(' / ')),
      h('div', { class: 'prod-v-chips' },
        h('span', { class: 'prod-v-chip', 'data-testid': 'prod-v-product' }, productName(r.product)),
        fmt ? h('span', { class: 'prod-v-chip', title: formatLabel(fmt), 'data-testid': 'prod-v-format' }, fmt.code) : null,
        dueChip(r)),
      banner,
      r.brief_url ? link(r.brief_url, 'Open brief', { class: 'prod-v-brief prod-ext', 'data-testid': 'prod-v-brief', title: r.brief_url }) : h('p', { class: 'prod-v-none', 'data-testid': 'prod-v-nobrief' }, 'The brief has not been added yet.'),
      h('div', null, x.button()), host,
      r.story ? h('p', { class: 'prod-story', 'data-testid': 'prod-story' }, r.story) : null,
      h('dl', { class: 'prod-dl' },
        dt('Brief', r.brief_url ? link(r.brief_url, 'Brief', { 'data-testid': 'prod-brief_url', class: 'prod-ext', title: r.brief_url }) : 'Not added yet'),
        dt('Format', h('span', { 'data-testid': 'prod-format-text' }, fmt ? formatLabel(fmt) : 'Not chosen')),
        dt('Ads', (r.ads || []).length ? variationGroups(r.ads, (a) => a.concept_id).map(({ base, list }) => {
          const one = (a, text) => h('div', { 'data-testid': 'prod-cid' }, a.ad_url ? link(a.ad_url, text) : text);
          return list.length === 1 ? one(list[0], list[0].concept_id) : h('details', { class: 'prod-var', 'data-testid': 'prod-variations' }, h('summary', null, `${base} · ${list.length} variations`), list.map((a) => one(a, variationLabel(a.concept_id, base))));
        }) : 'None yet'),
        dt('Result', h('span', { 'data-testid': 'prod-result' }, res || 'Not launched yet'))),
      h('label', { class: 'pri-dlabel' }, h('span', { class: 'pri-k' }, 'Creative link'), link_),
      h('label', { class: 'pri-dlabel' }, h('span', { class: 'pri-k' }, 'Note'), note),
      hint,
      h('div', { class: 'prod-vbtns' }, ...btns),
      th ? th.det : null);
  }
  await loadComments(d.rows || []);
  draw();
  ctx.mount(root);
}
