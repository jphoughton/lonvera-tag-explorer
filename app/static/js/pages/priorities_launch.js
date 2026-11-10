// Launch controls for the Production page (/priorities/production) only. Loaded by priorities_production.js; never by the vendor page or a public saved view.
// Dark by default: with `launch.enabled` false or missing, or `can_launch` false, this does nothing at all (no buttons, columns, banners or text).
// The app never calls Meta from here. It asks the API to queue a check (prepare) and then a launch; a separate worker does the work and this page polls for the result.
// Everything the user typed or the API returned goes in through textContent / h(); links use h('a', {href, target: '_blank', rel: 'noopener noreferrer'}).
// Class names and test ids start with `prod-` (never ad-, ads-, sponsor, banner, promo: ad blockers hide those).
import { h } from '../util.js';
import { api } from '../api.js';

const POLL_MS = () => (typeof window !== 'undefined' && Number(window.LAUNCH_POLL_MS)) || 3000;
const DONE_STATES = ['done', 'failed', 'cancelled'];

const money = (cents) => (typeof cents === 'number' && isFinite(cents) ? '$' + (cents / 100).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '');
const list = (x) => (Array.isArray(x) ? x : []);
const errText = (e) => (e && e.message) || 'Something went wrong.';

// State words shown to the user. A prepare job that is done is a plan ready to confirm; a launch job that is done means the ads exist in Meta, paused.
export function jobLabel(job) {
  if (!job) return '';
  const launch = job.kind === 'launch';
  switch (job.state) {
    case 'queued': return 'Waiting to start';
    case 'running': return launch ? 'Creating paused ads' : 'Checking files';
    case 'done': return launch ? 'Created in Meta, paused' : 'Plan ready';
    case 'failed': return 'Failed';
    case 'cancelled': return 'Cancelled';
    default: return '';
  }
}
export function adLabel(ad) {
  switch (ad && ad.state) {
    case 'created': case 'done': return 'Created in Meta, paused';
    case 'failed': return 'Failed';
    case 'running': case 'creating': return 'Creating paused ads';
    case 'cancelled': return 'Cancelled';
    default: return 'Waiting to start';
  }
}

function link(url, text, attrs) { return h('a', { href: url, target: '_blank', rel: 'noopener noreferrer', ...(attrs || {}) }, text); }
const safeUrl = (u) => typeof u === 'string' && /^https?:\/\//i.test(u);

// Called by the Production page after every draw. Adds one "Prepare launch" button to each row that is ready to launch and has a creative link.
export function decorate(root, launch, ctx) {
  if (!launch || launch.enabled !== true || launch.can_launch !== true || !root) return;
  for (const tr of root.querySelectorAll('[data-testid=prod-row]')) {
    if (tr.querySelector('[data-testid=prod-launch-open]')) continue;
    if (tr.dataset.stage !== 'ready' || !tr.querySelector('[data-testid=prod-creative_url]')) continue;
    const acts = tr.lastElementChild;
    if (!acts) continue;
    const id = tr.dataset.id;
    acts.append(h('button', { type: 'button', class: 'btn prod-small', 'data-testid': 'prod-launch-open', onclick: () => openLaunch(id, launch, ctx) }, 'Prepare launch'));
  }
}

function openLaunch(aid, flags, ctx) {
  const base = `/api/priorities/assignments/${encodeURIComponent(aid)}/launch`;
  let view = null;           // latest GET .../launch
  let error = '';            // inline error text
  let notice = '';           // inline neutral text
  let busy = false;
  let closed = false;
  let timer = null;
  let stale = false;         // draft saved since the plan was made: prepare again before creating
  let dirty = false;         // text fields edited and not saved yet
  let copied = '';
  const body = h('div', { class: 'prod-lbody' });
  const title = h('h2', { class: 'pri-dtitle', id: 'prod-ltitle' }, 'Launch to Meta');
  const dlg = h('dialog', { class: 'pri-dialog prod-ldialog', 'aria-labelledby': 'prod-ltitle', 'data-testid': 'prod-launch-dialog' }, h('div', { class: 'pri-dform' }, title, body));

  const close = () => { if (closed) return; closed = true; clearTimeout(timer); try { dlg.close(); } catch (e) { /* already closed */ } dlg.remove(); };
  dlg.addEventListener('cancel', (e) => { e.preventDefault(); close(); });
  dlg.addEventListener('click', (e) => { if (e.target === dlg) close(); });
  if (ctx && ctx.signal) ctx.signal.addEventListener('abort', close);

  const job = () => (view && view.job) || null;
  const dry = () => !!(view && view.dry_run) || !!(job() && job().dry_run) || !!flags.dry_run;

  async function refresh() {
    try { view = await api(base, {}); } catch (e) { error = errText(e); }
  }
  function schedule() {
    clearTimeout(timer);
    const j = job();
    if (closed || !j || DONE_STATES.includes(j.state)) return;
    timer = setTimeout(async () => {
      if (closed) return;
      await refresh();
      if (closed) return;
      draw();
      schedule();
    }, POLL_MS());
  }
  async function act(fn) {
    if (busy) return;
    busy = true; error = ''; notice = ''; draw();
    try { await fn(); } catch (e) { error = errText(e); if (e && e.status === 409) await refresh(); }
    busy = false;
    if (closed) return;
    draw(); schedule();
  }
  const prepare = () => act(async () => {
    await api(base + '/prepare', {}, { method: 'POST', body: {} });
    stale = false; dirty = false; copied = '';
    await refresh();
  });
  const confirm = (j) => act(async () => {
    try { await api(base + '/confirm', {}, { method: 'POST', body: { job_id: j.id, plan_hash: j.plan_hash } }); } catch (e) { if (e && e.status === 409) stale = true; throw e; }   // the plan is old: Create stays off until Prepare again
    copied = '';
    await refresh();
  });
  const cancelOpen = () => act(async () => { await api(base + '/cancel', {}, { method: 'POST', body: {} }); await refresh(); });
  async function saveDraft(inputs) {
    await act(async () => {
      await api(base + '/draft', {}, { method: 'PUT', body: { primary_texts: inputs.primary.map((i) => i.value.trim()), headlines: inputs.heads.map((i) => i.value.trim()) } });
      stale = true; dirty = false; notice = 'Text saved. Prepare launch again to check the plan with the new text.';
      await refresh();
    });
  }
  async function copyTsv(tsv) {
    try { await navigator.clipboard.writeText(tsv); copied = 'Copied. Paste it into the Creative Tracker.'; } catch (e) { copied = 'Could not copy automatically. Select the text below and copy it.'; }
    draw();
    if (copied.startsWith('Could not')) { const ta = body.querySelector('[data-testid=prod-launch-tsv]'); if (ta) { ta.hidden = false; ta.focus(); ta.select(); } }
  }

  const table = (heads, rows, tid) => h('div', { class: 'prod-lscroll' }, h('table', { class: 'prod-ltable', 'data-testid': tid },
    h('thead', null, h('tr', null, heads.map((x) => h('th', { scope: 'col' }, x)))), h('tbody', null, rows)));
  const kv = (k, v) => h('tr', null, h('th', { scope: 'row' }, k), h('td', null, v));

  function planBlock(j) {
    const p = j.plan || {};
    const inputs = { primary: [], heads: [] };
    const field = (arr, label, val, tid, i) => {
      const el = h('input', { type: 'text', class: 'pri-name prod-lin', 'aria-label': `${label} ${i + 1}`, 'data-testid': `${tid}-${i + 1}`, value: val || '', maxlength: '2000' });
      el.addEventListener('input', () => { dirty = true; paint(); });
      arr.push(el);
      return h('label', { class: 'pri-dlabel' }, h('span', { class: 'pri-k' }, `${label} ${i + 1}`), el);
    };
    const pts = list(p.primary_texts), hds = list(p.headlines);
    const n1 = Math.max(3, pts.length), n2 = Math.max(3, hds.length);
    const fields = [];
    for (let i = 0; i < n1; i++) fields.push(field(inputs.primary, 'Primary text', pts[i], 'prod-launch-primary', i));
    for (let i = 0; i < n2; i++) fields.push(field(inputs.heads, 'Headline', hds[i], 'prod-launch-headline', i));
    const saveBtn = h('button', { type: 'button', class: 'btn', 'data-testid': 'prod-launch-save-text', onclick: () => saveDraft(inputs) }, 'Save text');
    saveBtn.disabled = busy || !dirty;
    function paint() { saveBtn.disabled = busy || !dirty; const c = body.querySelector('[data-testid=prod-launch-confirm]'); if (c) c.disabled = busy || dirty || stale; }
    const ads = list(p.ads), skipped = list(p.skipped);
    return [
      table(['Plan', ''], [
        kv('Ad set', [p.adset_name || '', ' ', h('span', { class: 'pri-sub', 'data-testid': 'prod-launch-adset-exists' }, p.adset_exists ? '(already exists in Meta)' : '(will be created)')]),
        kv('Link', safeUrl(p.link_url) ? link(p.link_url, p.link_url, { 'data-testid': 'prod-launch-link' }) : (p.link_url || '')),
        kv('Daily budget', h('span', { 'data-testid': 'prod-launch-budget' }, money(p.daily_budget_cents))),
        kv('Cost cap', h('span', { 'data-testid': 'prod-launch-cap' }, money(p.cost_cap_cents))),
      ], 'prod-launch-plan'),
      h('h3', { class: 'prod-lh' }, `Ads to create (${ads.length})`),
      ads.length ? table(['Ad name', 'File'], ads.map((a) => h('tr', { 'data-testid': 'prod-launch-adrow' }, h('td', null, a.ad_name || ''), h('td', null, a.file_name || ''))), 'prod-launch-ads') : h('p', { class: 'pri-sub' }, 'No ads in this plan.'),
      skipped.length ? [h('h3', { class: 'prod-lh' }, 'Skipped files'), table(['File', 'Reason'], skipped.map((s) => h('tr', { 'data-testid': 'prod-launch-skiprow' }, h('td', null, s.file_name || ''), h('td', null, s.reason || ''))), 'prod-launch-skipped')] : null,
      h('h3', { class: 'prod-lh' }, 'Text'), fields, h('div', null, saveBtn),
      h('p', { class: 'prod-lline', 'data-testid': 'prod-launch-paused' }, 'Every ad is created paused. Turning ads on happens in Ads Manager.'),
      h('div', { class: 'pri-dbtns' },
        h('button', { type: 'button', class: 'btn', 'data-testid': 'prod-launch-cancel', onclick: close }, 'Cancel'),
        (() => { const b = h('button', { type: 'button', class: 'btn primary', 'data-testid': 'prod-launch-confirm', onclick: () => confirm(j) }, 'Create paused ads in Meta'); b.disabled = busy || dirty || stale; return b; })()),
    ];
  }

  function adsBlock(v) {
    const ads = list(v.ads);
    if (!ads.length) return null;
    const failed = ads.some((a) => a && a.state === 'failed');
    return [
      h('h3', { class: 'prod-lh' }, 'Ads'),
      table(['Ad name', 'File', 'State', 'Ads Manager'], ads.map((a) => h('tr', { 'data-testid': 'prod-launch-ad', 'data-state': a.state || '' },
        h('td', null, a.ad_name || a.concept_id || ''), h('td', null, a.file_name || ''),
        h('td', null, adLabel(a), a.error ? h('div', { class: 'pri-err', 'data-testid': 'prod-launch-aderr' }, a.error) : null),
        h('td', null, safeUrl(a.ads_manager_url) ? link(a.ads_manager_url, 'Open in Ads Manager', { 'data-testid': 'prod-launch-amlink' }) : ''))), 'prod-launch-adstate'),
      h('div', { class: 'pri-dbtns prod-lleft' },
        failed ? h('button', { type: 'button', class: 'btn', 'data-testid': 'prod-launch-retry', disabled: busy, onclick: () => prepare() }, 'Retry failed') : null,
        v.tracker_tsv ? h('button', { type: 'button', class: 'btn', 'data-testid': 'prod-launch-copy', onclick: () => copyTsv(v.tracker_tsv) }, 'Copy tracker row') : null),
      v.tracker_tsv ? [h('p', { class: 'pri-sub' }, 'This app never writes the Google Sheet. Paste the row into the Creative Tracker yourself.'),
        h('textarea', { class: 'prod-ltsv', 'data-testid': 'prod-launch-tsv', readonly: true, hidden: true, rows: '3', 'aria-label': 'Tracker row' }, v.tracker_tsv)] : null,
      copied ? h('p', { role: 'status', 'data-testid': 'prod-launch-copied' }, copied) : null,
    ];
  }

  function draw() {
    if (closed) return;
    const j = job();
    const label = jobLabel(j);
    const parts = [];
    if (dry()) parts.push(h('p', { class: 'prod-testmode', 'data-testid': 'prod-launch-testmode' }, 'Test mode: nothing will be created in Meta.'));
    const a = view && view.assignment;
    if (a) parts.push(h('p', { class: 'pri-sub' }, [a.vendor, a.concept_label].filter(Boolean).join(': ')));
    if (view && view.ready === false) parts.push(h('p', { 'data-testid': 'prod-launch-notready' }, view.not_ready_reason || 'This row is not ready to launch.'));
    if (!view && !error) parts.push(h('p', { class: 'prod-lstate', role: 'status', 'data-testid': 'prod-launch-state' }, 'Waiting to start'));
    if (label) parts.push(h('p', { class: 'prod-lstate', role: 'status', 'data-testid': 'prod-launch-state' }, label));
    if (j && j.state === 'failed' && j.error) parts.push(h('p', { class: 'pri-err', 'data-testid': 'prod-launch-joberr' }, j.error));
    if (notice) parts.push(h('p', { role: 'status', 'data-testid': 'prod-launch-notice' }, notice));
    if (error) parts.push(h('p', { class: 'pri-err', role: 'alert', 'data-testid': 'prod-launch-error' }, error));
    if (j && j.kind === 'prepare' && j.state === 'done' && !stale) parts.push(planBlock(j));
    if (j && j.kind === 'prepare' && j.state === 'done' && stale) parts.push(planBlock(j));
    const idle = !(j && j.kind === 'prepare' && j.state === 'done');
    if (view && view.ready !== false && (stale || (idle && (!j || DONE_STATES.includes(j.state))))) {
      parts.push(h('div', { class: 'pri-dbtns prod-lleft' }, h('button', { type: 'button', class: 'btn primary', 'data-testid': 'prod-launch-prepare', disabled: busy, onclick: () => prepare() }, stale ? 'Prepare launch again' : 'Prepare launch')));
    }
    if (j && j.state === 'queued') parts.push(h('div', { class: 'pri-dbtns prod-lleft' }, h('button', { type: 'button', class: 'btn', 'data-testid': 'prod-launch-cancel-job', disabled: busy, onclick: () => cancelOpen() }, 'Cancel')));
    if (view) parts.push(adsBlock(view));
    if (!(j && j.kind === 'prepare' && j.state === 'done')) parts.push(h('div', { class: 'pri-dbtns' }, h('button', { type: 'button', class: 'btn', 'data-testid': 'prod-launch-close', onclick: close }, 'Close')));
    body.replaceChildren(...parts.flat(Infinity).filter(Boolean));
  }

  document.body.appendChild(dlg);
  if (typeof dlg.showModal === 'function') dlg.showModal(); else dlg.setAttribute('open', '');
  // Opening the dialog starts a prepare right away (it only queues a check of the files; nothing is created).
  body.append(h('p', { role: 'status', 'data-testid': 'prod-launch-state' }, 'Waiting to start'));
  prepare();
}
