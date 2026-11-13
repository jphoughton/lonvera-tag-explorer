// Shared helpers for the Production board (priorities_production.js), the vendor page (priorities_vendor.js) and the Assign dialog (priorities_assign.js).
// Class names and test ids start with `prod-` (never ad-, ads-, sponsor, banner, promo: ad blockers hide those).
import { h, fmt, isNum } from '../util.js';

// Board columns in order, with the labels shown in the app
export const STATUSES = [['assigned', 'Assigned'], ['brief_sent', 'Brief sent'], ['in_production', 'In production'], ['in_review', 'In review'], ['ready', 'Ready to launch'], ['launched', 'Launched']];
export const STATUS_LABEL = Object.fromEntries(STATUSES.concat([['dropped', 'Dropped']]));
export const statusOf = (r) => r.status || ({ creative_in: 'in_review' }[r.stage]) || r.stage || 'assigned';
export const PRIORITY_LABEL = { normal: 'Normal', high: 'High' };

export const STAGES = { assigned: 'Assigned', brief_sent: 'Brief sent', creative_in: 'Creative delivered', ready: 'Ready to launch', launched: 'Launched', dropped: 'Dropped' };
export const VENDOR_FALLBACK = ['Madre', 'Victoria', 'TrybeDCShop', 'TrybeOrion', '10xROAS', 'PuredMedia', 'CreateMastery', 'OpenProgram', 'Studio Flow'];
export const PRODUCT_NAMES = { 'vita-bull': 'Vita Bull', 'beef-liver': 'Beef Liver', boneflex: 'Boneflex' };

export const md = (s) => { const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(s || ''); return m ? `${+m[2]}/${+m[3]}` : (s || ''); };
export const weekLabel = (s) => 'Week of ' + md(s);
// Ticket title: "Madre - Hair Thinning - Hair Loss - 11/9" (vendor, market cluster, pain point, week of); a missing part is left out
export const ticketTitle = (r) => [r.vendor, r.cluster, r.pain_point, r.for_week ? md(r.for_week) : ''].filter(Boolean).join(' - ') || 'Concept';
// Variations of one concept share a Concept ID and differ by a suffix (-hook1 ... -hook5, -v2, - Copy 3): they are shown as ONE entry with the variations as links inside it.
export const variationBase = (id) => String(id).replace(/(?:[-_ ]+(?:hook|v|var|variation|copy)[ -]*\d+)+$/i, '') || String(id);
export function variationGroups(items, idOf = (x) => x) {
  const m = new Map();
  for (const it of items) { const b = variationBase(idOf(it)); if (!m.has(b)) m.set(b, []); m.get(b).push(it); }
  return [...m.entries()].map(([base, list]) => ({ base, list }));
}
export const variationLabel = (id, base) => String(id).slice(base.length).replace(/^[-_ ]+/, '') || String(id);
// "Label: value" lines get a bold label so a long note or brief is easy to scan; every other line stays plain. Always built with h() and text nodes (never innerHTML).
export function richLines(text) {
  const out = [];
  String(text || '').split('\n').forEach((line, i) => {
    if (i) out.push(h('br'));
    const v = /^\s*([^:]{1,40}):\s*(https?:\/\/\S+)\s*$/.exec(line);                      // "hook1: https://..." -> bold "Hook 1:" and a link
    const m = /^([A-Z][A-Za-z0-9'\/& -]{0,34}):\s+(.*)$/.exec(line);
    if (v) { const hk = /^hook[\s_-]*(\d+)$/i.exec(v[1].trim()); out.push(h('strong', null, (hk ? `Hook ${hk[1]}` : v[1].trim().replace(/^./, (c) => c.toUpperCase())) + ':'), ' ', h('a', { href: v[2], target: '_blank', rel: 'noopener noreferrer' }, 'Open')); }
    else if (m && !/^https?$/i.test(m[1])) out.push(h('strong', null, m[1] + ':'), ' ' + m[2]);
    else out.push(line);
  });
  return out;
}
// The 'Variations' section of a brief: lines "hook1: https://..." -> [{label: 'Hook 1', url}]. Anything that is not label + link is ignored.
export function parseVariations(story) {
  const lines = String(story || '').split('\n');
  const at = lines.findIndex((l) => /^variations\b/i.test(l.trim()));
  if (at < 0) return [];
  const out = [];
  for (const line of lines.slice(at + 1)) {
    const m = /^\s*([^:]{1,60}):\s*(https?:\/\/\S+)\s*$/.exec(line);
    if (!m) { if (line.trim() === '') continue; if (out.length) break; else continue; }
    const hk = /^hook[\s_-]*(\d+)$/i.exec(m[1].trim());
    out.push({ label: hk ? `Hook ${hk[1]}` : m[1].trim().replace(/^./, (c) => c.toUpperCase()), url: m[2] });
  }
  return out;
}
// A link the page may play in place: a Google Drive file (video or image) or folder. Anything else returns null and stays a plain link.
export function previewOf(url) {
  const s = String(url || '');
  let m = /^https:\/\/drive\.google\.com\/file\/d\/([\w-]+)/.exec(s) || /^https:\/\/drive\.google\.com\/open\?id=([\w-]+)/.exec(s);
  if (m) return { kind: 'file', src: `https://drive.google.com/file/d/${m[1]}/preview` };
  m = /^https:\/\/drive\.google\.com\/drive\/(?:u\/\d+\/)?folders\/([\w-]+)/.exec(s);
  if (m) return { kind: 'folder', src: `https://drive.google.com/embeddedfolderview?id=${m[1]}#grid` };
  return null;
}
export const adCount = (r) => (r.tracker_concept_ids || []).length || parseVariations(r.story).length;
export const productName = (p) => PRODUCT_NAMES[p] || p || '';
export const stageLabel = (r) => r.stage_label || STAGES[r.stage] || r.stage || '';
export const today = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; };
export const mondayOf = (iso) => { const [y, m, d] = iso.split('-').map(Number); const t = new Date(Date.UTC(y, m - 1, d)); t.setUTCDate(t.getUTCDate() - ((t.getUTCDay() + 6) % 7)); return t.toISOString().slice(0, 10); };
export const addDays = (iso, n) => { const [y, m, d] = iso.split('-').map(Number); const t = new Date(Date.UTC(y, m - 1, d + n)); return t.toISOString().slice(0, 10); };
// "F1 Long animated story, openly AI, told by the character (90s to 3 min)": a creative format as shown in the pick lists and on the vendor page
export const formatLabel = (f) => `${f.code} ${f.name}` + (f.length ? ` (${f.length})` : '');
export const rowFormat = (r) => (r.format_code ? { code: r.format_code, name: r.format_name || '', length: r.format_length || '' } : null);

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

export function link(url, text, attrs) {
  return h('a', { href: url, target: '_blank', rel: 'noopener noreferrer', ...(attrs || {}) }, text);
}
export const short = (u) => { try { const x = new URL(u); return (x.host + x.pathname).replace(/\/$/, '').slice(0, 38) + ((x.host + x.pathname).length > 38 ? '…' : ''); } catch (e) { return u; } };

// "Due 11/14", "Overdue 2 days", "Due today", "Due tomorrow": text plus a tone for the chip colour ('bad' overdue, 'warn' within 2 days, '' otherwise). Launched cards show no due chip.
export function dueInfo(r) {
  if (!r.due_date || statusOf(r) === 'launched') return null;
  const n = isNum(r.due_in_days) ? r.due_in_days : null;
  let text = 'Due ' + md(r.due_date);
  if (n !== null && n < 0) text = n === -1 ? 'Overdue 1 day' : `Overdue ${-n} days`;
  else if (n === 0) text = 'Due today';
  else if (n === 1) text = 'Due tomorrow';
  const tone = r.overdue || (n !== null && n < 0) ? 'bad' : (n !== null && n <= 2 ? 'warn' : '');
  return { text, tone };
}

// One small modal: optional note, fields [{key, label, type: 'text'|'select'|'textarea', value, options: [[value, label]], placeholder, required}], resolves {key: value} or null. No fields = a plain confirm.
export function askDialog({ title, note = null, fields = [], okLabel = 'Save', danger = false }) {
  return new Promise((resolve) => {
    const inputs = fields.map((f) => {
      const common = { class: 'pri-name', 'data-testid': 'prod-field-' + f.key, 'aria-label': f.label };
      const el = f.type === 'select'
        ? h('select', common, f.options.map(([v, l]) => h('option', { value: v, selected: v === f.value }, l)))
        : f.type === 'textarea'
          ? h('textarea', { ...common, rows: '4', placeholder: f.placeholder || '', maxlength: '2000' }, f.value || '')
          : h('input', { ...common, type: 'text', placeholder: f.placeholder || '', value: f.value || '', maxlength: '2000' });
      return [f, el];
    });
    const err = h('p', { class: 'pri-err prod-derr', role: 'alert', 'data-testid': 'prod-dialog-error' });
    const cancel = h('button', { type: 'button', class: 'btn', 'data-testid': 'prod-dialog-cancel' }, 'Cancel');
    const form = h('form', { method: 'dialog', class: 'pri-dform', onsubmit: (e) => {
      e.preventDefault();
      const missing = inputs.find(([f, el]) => f.required && !el.value.trim());
      if (missing) { err.textContent = `${missing[0].label} is required.`; missing[1].focus(); return; }
      done(Object.fromEntries(inputs.map(([f, el]) => [f.key, el.value.trim()])));
    } },
      h('h2', { class: 'pri-dtitle', id: 'prod-dtitle' }, title), note ? h('p', { class: 'pri-dnote', 'data-testid': 'prod-dialog-note' }, note) : null,
      inputs.map(([f, el]) => h('label', { class: 'pri-dlabel' }, h('span', { class: 'pri-k' }, f.label), el)), err,
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

// A short message that disappears: role=status so screen readers read it; errors stay longer
export function toast(text, bad) {
  let host = document.querySelector('[data-testid=prod-toasts]');
  if (!host) { host = h('div', { class: 'prod-toasts', 'data-testid': 'prod-toasts', role: 'status', 'aria-live': 'polite' }); document.body.appendChild(host); }
  const t = h('div', { class: 'prod-toast' + (bad ? ' prod-toast-bad' : ''), 'data-testid': 'prod-toast' }, text);
  host.appendChild(t);
  setTimeout(() => t.remove(), bad ? 6000 : 3500);
}

// What a person can do next with a card, in the order the buttons show. `to` = the status it moves to, `comment` = a comment is required, `launched` = the Mark launched call (not a move).
export function nextActions(r) {
  const s = statusOf(r);
  if (r.dropped_at || r.stage === 'dropped') return [{ key: 'restore', label: 'Restore', restore: true }];
  if (s === 'assigned') return [{ key: 'send_brief', label: 'Send brief', to: 'brief_sent', primary: true }, { key: 'start', label: 'Mark in production', to: 'in_production' }];
  if (s === 'brief_sent') return [{ key: 'start', label: 'Mark in production', to: 'in_production', primary: true }];
  if (s === 'in_production') return [{ key: 'submit', label: 'Submit for review', to: 'in_review', primary: true }];
  if (s === 'in_review') return [{ key: 'approve', label: 'Approve', to: 'ready', primary: true }, { key: 'changes', label: 'Request changes', to: 'in_production', comment: true }];
  if (s === 'ready') return [{ key: 'launched', label: 'Mark launched', launched: true, primary: true }];
  return [];
}

// History of an assignment: "11/10 2:14 PM  Josh: Brief sent to Creative delivered". Who did it comes from the sign-in name; a vendor link page shows as "Madre (vendor link)"; older rows have no name.
const EVENT_TEXT = { assigned: 'Assigned to production', brief_added: 'Brief link added', brief_removed: 'Brief link removed', creative_added: 'Creative link added', creative_removed: 'Creative link removed', ready: 'Marked ready to launch', ready_cleared: 'Ready to launch removed',
  launched: 'Marked launched', status_changed: 'Status changed', due_changed: 'Due date changed', comment_added: 'Comment added', priority_changed: 'Priority changed', capacity_changed: 'Weekly capacity changed', launched_cleared: 'Launched removed', dropped: 'Dropped', restored: 'Restored', vendor: 'Vendor changed', week: 'Week changed', format: 'Format changed' };
const actorName = (a) => (!a ? 'Not recorded' : a.startsWith('vendor:') ? a.slice(7) + ' (vendor link)' : a);
const whenText = (iso) => { const d = new Date(iso); return isNaN(d) ? '' : `${d.getMonth() + 1}/${d.getDate()} ${d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}`; };
export function eventLine(e) {
  const what = EVENT_TEXT[e.kind] || e.kind;
  const sb = e.status_before || e.stage_before; const sa = e.status_after || e.stage_after;
  const lab = (k) => STATUS_LABEL[k] || STAGES[k] || k;
  const move = sb && sa && sb !== sa ? `${e.status_after ? 'Status' : 'Stage'}: ${lab(sb)} to ${lab(sa)}` : '';
  return { when: whenText(e.at), who: actorName(e.actor), what: what + (e.detail ? ` (${e.detail})` : ''), move };
}
