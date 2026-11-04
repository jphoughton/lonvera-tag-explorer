// Plan-of-record guard (no password; friction and clarity instead).
//
// The plan of record (the default scenario, "Current plan") drives the pacing goals and the default view of every page, so a trial edit there
// silently moves them. The FIRST edit of a browser tab session on the plan of record opens a small inline choice:
//     [Make a copy and edit it]   (primary: clones, switches to the copy, applies the edit there)
//     [Edit the plan of record]   (secondary: remembered for the rest of the tab session)
//     [Cancel]                    (Esc: nothing is sent, the cell goes back)
// It sits in the shared fetch layer (api.setWriteGuard), so every module page gets it with no module changes: grid cell edits and deletes,
// pasted rows, bulk fills and patterns, lever and settings saves.
//
// What counts as a scenario edit: a mutating request that carries the default scenario's slug (body.scenario, params.scenario or ?scenario=)
// and is not a preview, not scenario management (/scenarios...), and not a scenario-independent fact store (purchase orders, transfers,
// stock counts, the saved Goal curve, QuickBooks account mapping, feed rules). Those are shared by every scenario, so a copy would not isolate them.
import { h, toast } from './util.js';
import * as api from './api.js';
import { scenarios } from './scenario.js';

const KEY = 'projection.por.edit';                 // sessionStorage: '1' once the user chose "Edit the plan of record" in this tab session
const ssGet = () => { try { return sessionStorage.getItem(KEY) === '1'; } catch { return false; } };
const ssSet = () => { try { sessionStorage.setItem(KEY, '1'); } catch { /* private window: asks again next time */ } };
export const editsAllowed = ssGet;

const FACTS = [
  /\/inventory-supply\/(po|transfers|stock|plan\/accept)(?:[/?]|$)/,
  /\/cohorts\/goal(?:[/?]|$)/,
  /\/pnl\/(qb-map|freshness)(?:[/?]|$)/,
  /\/pacing\/rules(?:[/?]|$)/,
  /\/inventory-demand\/upload/,
];
const MANAGEMENT = /^\/?scenarios(?:[/?]|$)/;

function slugOf(req) {
  if (req.body && typeof req.body.scenario === 'string') return req.body.scenario;
  if (req.params && typeof req.params.scenario === 'string') return req.params.scenario;
  const m = /[?&]scenario=([^&#]*)/.exec(req.path || '');
  return m ? decodeURIComponent(m[1]) : null;
}
export function isScenarioEdit(req) {
  const p = '/' + String(req.path || '').replace(/^\/?(api\/projection\/)?/, '');      // '/drivers/patterns' however the caller spelled it
  if (MANAGEMENT.test(p) || FACTS.some((re) => re.test(p))) return false;
  if (req.body && typeof req.body === 'object' && req.body.preview) return false;
  return true;
}

// ---------------------------------------------------------------- the inline choice
let asking = null;
function ask() {
  if (asking) return asking;                       // a burst of writes asks once
  asking = new Promise((resolve) => {
    const back = document.activeElement;
    const btn = (choice, cls, text) => h('button', { type: 'button', class: cls, 'data-choice': choice, onclick: () => done(choice) }, text);
    const copyBtn = btn('copy', 'btn primary', 'Make a copy and edit it');
    const editBtn = btn('edit', 'btn', 'Edit the plan of record');
    const cancelBtn = btn('cancel', 'btn pg-por-cancel', 'Cancel');
    const el = h('div', { class: 'pg-por-ask', role: 'alertdialog', 'aria-labelledby': 'pg-por-t', 'aria-describedby': 'pg-por-d' },
      h('h2', { id: 'pg-por-t' }, 'You are editing the plan of record'),
      h('p', { id: 'pg-por-d' }, 'It drives the pacing goals and the default view of every page, so a trial change moves them. Try it on a copy first.'),
      h('div', { class: 'pg-por-actions' }, copyBtn, editBtn, cancelBtn));
    const buttons = [copyBtn, editBtn, cancelBtn];
    function onKey(e) {
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); done('cancel'); return; }
      if (e.key === 'Tab') {
        const i = buttons.indexOf(document.activeElement);
        e.preventDefault();
        buttons[(i + (e.shiftKey ? buttons.length - 1 : 1) + buttons.length) % buttons.length].focus();
      }
    }
    function done(choice) {
      document.removeEventListener('keydown', onKey, true);
      el.remove();
      asking = null;
      if (back && back.isConnected) { try { back.focus({ preventScroll: true }); } catch { /* ignore */ } }
      resolve(choice);
    }
    document.addEventListener('keydown', onKey, true);
    document.body.append(el);
    copyBtn.focus();
  });
  return asking;
}

// ---------------------------------------------------------------- "this plan changed in another tab"
let conflicting = null;
function askConflict({ slug, mine, theirs }) {
  if (conflicting) return conflicting;
  conflicting = new Promise((resolve) => {
    const back = document.activeElement;
    const sc = scenarios.list.find((x) => x.slug === slug);
    const what = sc && (sc.is_default || sc.slug === 'current') ? 'plan' : 'scenario';
    const reload = h('button', { type: 'button', class: 'btn primary', 'data-choice': 'reload', onclick: () => done('reload') }, 'Reload');
    const over = h('button', { type: 'button', class: 'btn', 'data-choice': 'overwrite', onclick: () => done('overwrite') }, 'Overwrite');
    const el = h('div', { class: 'pg-por-ask pg-conflict', role: 'alertdialog', 'aria-labelledby': 'pg-cf-t', 'aria-describedby': 'pg-cf-d' },
      h('h2', { id: 'pg-cf-t' }, `This ${what} changed in another tab or window (v${mine} to v${theirs})`),
      h('p', { id: 'pg-cf-d' }, 'Reload to see the latest numbers. Overwrite saves your change on top of it; the other change stays in History and can be restored.'),
      h('div', { class: 'pg-por-actions' }, reload, over));
    const buttons = [reload, over];
    function onKey(e) {
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); done('reload'); return; }
      if (e.key === 'Tab') { e.preventDefault(); buttons[(buttons.indexOf(document.activeElement) + 1) % buttons.length].focus(); }
    }
    function done(choice) {
      document.removeEventListener('keydown', onKey, true);
      el.remove();
      conflicting = null;
      if (back && back.isConnected) { try { back.focus({ preventScroll: true }); } catch { /* ignore */ } }
      resolve(choice);
    }
    document.addEventListener('keydown', onKey, true);
    document.body.append(el);
    reload.focus();
  });
  return conflicting;
}

const stamp = () => new Date().toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });

// ---------------------------------------------------------------- install
// opts.afterSwitch(slug): called right after the silent switch to the copy (the shell syncs the URL and the bar).
export function installGuard({ afterSwitch = () => {} } = {}) {
  const redirects = new Map();                     // default slug -> {to, until}: follow-up writes of the same operation (chunked saves, queued edits)
  let copying = null;
  let refreshTimer = null;
  const refresh = () => {                          // one re-render once the redirected writes are in
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(async () => { try { await scenarios.refresh(); } catch { /* keep going */ } scenarios.notifyChange(); }, 120);
  };

  async function makeCopy(base) {
    const name = `What-if ${stamp()}`;
    const slug = await scenarios.create(name, base.slug, { silent: true });
    if (!slug) throw new Error('Could not make a copy. The plan of record is unchanged.');
    redirects.set(base.slug, { to: slug, until: Date.now() + 60000 });
    afterSwitch(slug);
    toast(`Made "${name}" and applied your edit there. The plan of record is unchanged.`, {
      kind: 'ok',
      action: { label: 'Rename', onClick: () => window.dispatchEvent(new CustomEvent('pg:scenario-action', { detail: 'rename' })) },
    });
    return slug;
  }

  api.setConflictHandler(async (c) => {
    const choice = await askConflict(c);
    if (choice === 'reload') { try { await scenarios.refresh(); } catch { /* keep going */ } scenarios.notifyChange(); }
    return choice;
  });

  api.setWriteGuard(async (req) => {
    const slug = slugOf(req);
    if (!slug) return null;
    const sc = scenarios.list.find((s) => s.slug === slug);
    if (!sc || !sc.is_default) return null;
    const r = redirects.get(slug);
    if (r && r.until > Date.now() && scenarios.has(r.to) && isScenarioEdit(req)) return { scenario: r.to, after: refresh };
    if (!isScenarioEdit(req) || ssGet()) return null;
    const choice = await ask();
    if (choice === 'cancel') throw new api.EditCancelled();
    if (choice === 'edit') { ssSet(); return null; }
    copying = copying || makeCopy(sc).finally(() => { copying = null; });
    const to = await copying;
    return { scenario: to, after: refresh };
  });
}
