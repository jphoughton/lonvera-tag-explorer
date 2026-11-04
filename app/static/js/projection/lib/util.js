// Projection UI: DOM helper, small emitter, toast, dialogs, loading / error / empty boxes.
// No innerHTML anywhere: API data is untrusted and goes in through textContent / text nodes.

export function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  if (attrs != null && (typeof attrs !== 'object' || attrs instanceof Node || Array.isArray(attrs))) {
    children.unshift(attrs);
    attrs = null;
  }
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k === 'class') el.className = v;
      else if (k === 'dataset') Object.assign(el.dataset, v);
      else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
      else if (k.length > 2 && k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2).toLowerCase(), v);
      else if (v === true) el.setAttribute(k, '');
      else el.setAttribute(k, String(v));
    }
  }
  for (const c of children.flat(Infinity)) {
    if (c == null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

export const $ = (sel, root = document) => root.querySelector(sel);
// el.replaceChildren that accepts nested arrays and skips null / false (replaceChildren(array) would stringify the array)
export function setChildren(el, ...kids) {
  el.replaceChildren(...kids.flat(Infinity).filter((c) => c != null && c !== false).map((c) => (c instanceof Node ? c : document.createTextNode(String(c)))));
  return el;
}
export const isNum = (v) => typeof v === 'number' && Number.isFinite(v);

export function debounce(fn, ms = 200) {
  let t;
  const d = (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
  d.cancel = () => clearTimeout(t);
  return d;
}

export function qs(params) {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params || {})) {
    if (v == null || v === '' || v === false) continue;
    sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? '?' + s : '';
}

// module keys use underscores (inventory_demand); URLs and API prefixes use dashes (inventory-demand)
export const dash = (key) => String(key || '').replace(/_/g, '-');

export function createEmitter() {
  const map = new Map();
  return {
    on(evt, fn) {
      if (!map.has(evt)) map.set(evt, new Set());
      map.get(evt).add(fn);
      return () => map.get(evt).delete(fn);
    },
    emit(evt, ...args) {
      for (const fn of [...(map.get(evt) || [])]) {
        try { fn(...args); } catch (e) { console.warn('listener failed', evt, e); }
      }
    },
  };
}

// ---------- toast ----------
let toastHost = null;
export function toast(message, opts = {}) {
  if (typeof opts === 'string') opts = { kind: opts };
  const kind = opts.kind || 'info';
  const ms = opts.ms ?? (kind === 'error' ? 6500 : 2800);
  if (!toastHost || !toastHost.isConnected) {
    toastHost = h('div', { class: 'pg-toasts', role: 'status', 'aria-live': 'polite' });
    document.body.append(toastHost);
  }
  const el = h('div', { class: 'pg-toast ' + kind }, String(message));
  toastHost.append(el);
  while (toastHost.children.length > 4) toastHost.firstChild.remove();
  setTimeout(() => el.remove(), ms);
  return el;
}

// ---------- dialogs (native <dialog>) ----------
function openDialog(build) {
  return new Promise((resolve) => {
    const dlg = h('dialog', { class: 'pg-dialog' });
    let done = false;
    const close = (val) => {
      if (done) return;
      done = true;
      if (dlg.open) dlg.close();
      dlg.remove();
      resolve(val);
    };
    dlg.addEventListener('cancel', (e) => { e.preventDefault(); close(null); });
    dlg.addEventListener('click', (e) => { if (e.target === dlg) close(null); });
    build(dlg, close);
    document.body.append(dlg);
    dlg.showModal();
  });
}

// askText({title, label, value, confirmText, onSubmit(value) -> may throw}) -> string | null (cancelled)
export function askText({ title, label, value = '', confirmText = 'Save', onSubmit = null, maxLength = 80 }) {
  return openDialog((dlg, close) => {
    const input = h('input', { type: 'text', class: 'pg-input', value, maxlength: maxLength, autocomplete: 'off', 'aria-label': label || title });
    const err = h('div', { class: 'err pg-dialog-err', role: 'alert' });
    const ok = h('button', { type: 'submit', class: 'btn primary' }, confirmText);
    const cancel = h('button', { type: 'button', class: 'btn', onclick: () => close(null) }, 'Cancel');
    const form = h('form', { class: 'pg-dialog-body' },
      h('h2', title),
      label ? h('label', null, label) : null,
      input, err,
      h('div', { class: 'pg-dialog-actions' }, cancel, ok));
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const v = input.value.trim();
      if (!v) { err.textContent = 'Enter a name.'; return; }
      err.textContent = '';
      ok.disabled = true;
      try {
        if (onSubmit) await onSubmit(v);
        close(v);
      } catch (ex) {
        err.textContent = ex && ex.message ? ex.message : String(ex);
        ok.disabled = false;
      }
    });
    dlg.append(form);
    setTimeout(() => { input.focus(); input.select(); }, 0);
  });
}

// confirmBox({title, message, confirmText, danger, onConfirm}) -> true | false
export function confirmBox({ title, message, confirmText = 'Delete', danger = true, onConfirm = null }) {
  return openDialog((dlg, close) => {
    const err = h('div', { class: 'err pg-dialog-err', role: 'alert' });
    const ok = h('button', { type: 'button', class: 'btn ' + (danger ? 'danger' : 'primary') }, confirmText);
    ok.addEventListener('click', async () => {
      ok.disabled = true;
      try {
        if (onConfirm) await onConfirm();
        close(true);
      } catch (ex) {
        err.textContent = ex && ex.message ? ex.message : String(ex);
        ok.disabled = false;
      }
    });
    dlg.append(h('div', { class: 'pg-dialog-body' },
      h('h2', title), h('p', null, message), err,
      h('div', { class: 'pg-dialog-actions' }, h('button', { type: 'button', class: 'btn', onclick: () => close(false) }, 'Cancel'), ok)));
    setTimeout(() => ok.focus(), 0);
  }).then((v) => v === true);
}

// confirmTyped({title, message, expect, confirmText, onConfirm}) -> true | false. The confirm button stays disabled until `expect` is typed.
export function confirmTyped({ title, message, expect, confirmText = 'Delete', onConfirm = null }) {
  return openDialog((dlg, close) => {
    const input = h('input', { type: 'text', class: 'pg-input', autocomplete: 'off', spellcheck: 'false', 'aria-label': `Type ${expect} to confirm` });
    const err = h('div', { class: 'err pg-dialog-err', role: 'alert' });
    const ok = h('button', { type: 'button', class: 'btn danger', disabled: true }, confirmText);
    input.addEventListener('input', () => { ok.disabled = input.value.trim() !== expect; });
    const run = async () => {
      if (input.value.trim() !== expect) return;
      ok.disabled = true;
      try {
        if (onConfirm) await onConfirm();
        close(true);
      } catch (ex) {
        err.textContent = ex && ex.message ? ex.message : String(ex);
        ok.disabled = false;
      }
    };
    ok.addEventListener('click', run);
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); run(); } });
    dlg.append(h('div', { class: 'pg-dialog-body' },
      h('h2', title), h('p', null, message),
      h('label', null, 'Type ', h('code', null, expect), ' to confirm'), input, err,
      h('div', { class: 'pg-dialog-actions' }, h('button', { type: 'button', class: 'btn', onclick: () => close(false) }, 'Cancel'), ok)));
    setTimeout(() => input.focus(), 0);
  }).then((v) => v === true);
}

// ---------- state boxes (module pages use these for loading / error / empty) ----------
export function loading(el, msg = 'Loading…') {
  el.replaceChildren(h('div', { class: 'loading', role: 'status' }, msg));
}
export function errorBox(el, err, onRetry) {
  if (err && (err.name === 'AuthError' || err.name === 'DisabledError' || err.name === 'AbortError')) return; // the shell owns these
  el.replaceChildren(h('div', { class: 'errorbox', role: 'alert' },
    h('strong', 'Could not load this page. '),
    h('span', null, err && err.message ? err.message : String(err)),
    onRetry ? h('div', null, h('button', { type: 'button', class: 'btn', onclick: onRetry }, 'Retry')) : null));
}
export function empty(el, msg) {
  el.replaceChildren(h('div', { class: 'empty' }, msg));
}
export const ui = { loading, error: errorBox, empty };
