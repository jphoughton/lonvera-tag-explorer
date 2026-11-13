// Page-level filter controls with one look. The global filter bar (range, stage, compare, Numbers from, Count, platform) is built by js/controls.js and a page asks for
// the controls it needs through its `filters` list in js/pageconfig.js. Anything else a page filters on (product, interval, week, search, a number) is built here, so every
// page draws a segmented control, a chip, a select, a search box and a number box the same way.
//
//   filterRow(...controls)                          wraps controls in the shared bar (.ctl-row)
//   seg({ name, options, value, onChange, href })   segmented control; options = [[value, label, title?]]; `href(value)` makes links instead of buttons
//   chips({ name, options, values, onToggle })      on/off filter chips (several can be on)
//   selectCtl({ name, options, value, onChange })   dropdown
//   multiCtl({ name, options, values, allLabel, onChange, testid })  dropdown of checkboxes (pick any number); values = [value, ...]; [] means all; onChange(values) fires on every tick
//   searchCtl({ name, value, placeholder, onInput, testid })
//   numberCtl({ name, value, min, step, onChange, testid })
//   checkCtl({ label, checked, onChange, testid })
// Every control sets aria-pressed (seg, chips) so tests and screen readers read state the same way. Pass `testid` (or per-option testids) for tests; testids never change.
import { h } from '../util.js';

const label = (name) => (name ? h('span', { class: 'ctl-name' }, name) : null);

export function filterRow(...controls) {
  return h('div', { class: 'ctl-row pagefilters' }, controls.flat().filter(Boolean));
}

export function seg({ name, options, value, onChange, href, testid } = {}) {
  const btns = options.map(([val, text, title, tid]) => {
    const on = String(val) === String(value);
    if (href) return h('a', { class: 'seg', href: href(val), 'aria-pressed': String(on), title: title || null, 'data-testid': tid || null }, text);
    return h('button', { type: 'button', class: 'seg', 'aria-pressed': String(on), title: title || null, 'data-testid': tid || null, onclick: () => onChange && onChange(val) }, text);
  });
  return h('div', { class: 'ctl', 'data-testid': testid || null }, label(name), h('span', { class: 'segs', role: 'group', 'aria-label': name || null }, btns));
}

export function chips({ name, options, values = [], onToggle, testid } = {}) {
  const set = new Set(values.map(String));
  return h('div', { class: 'ctl', 'data-testid': testid || null },
    label(name),
    h('span', { class: 'chips', role: 'group', 'aria-label': name || null },
      options.map(([val, text, title, tid]) => h('button', { type: 'button', class: 'fchip', 'aria-pressed': String(set.has(String(val))), title: title || null, 'data-testid': tid || null, onclick: () => onToggle && onToggle(val) }, text))));
}

export function selectCtl({ name, options, value, onChange, testid } = {}) {
  const sel = h('select', { 'aria-label': name || null, 'data-testid': testid || null, onchange: () => onChange && onChange(sel.value) },
    options.map(([val, text]) => h('option', { value: val, selected: String(val) === String(value) }, text)));
  return h('div', { class: 'ctl' }, label(name), sel);
}

// A dropdown where any number of options can be ticked. Nothing ticked reads as "All ..."; one tick shows its name; more show "N selected". Every tick is sent at once (onChange(values)); the page may redraw its
// controls, so the list that was open is opened again by the new control (openMf) and only closes on a click outside, Escape or the summary.
let openMf = null;
let openMfVal = null;                                   // the box that was last ticked, so keyboard focus comes back to it after a redraw
export function multiCtl({ name, options, values = [], allLabel = 'All', onChange, testid } = {}) {
  const cur = new Set(values.map(String));
  const nameOf = (v) => { const o = options.find(([x]) => String(x) === v); return o ? o[1] : v; };
  const text = () => (cur.size === 0 ? allLabel : cur.size === 1 ? nameOf([...cur][0]) : `${cur.size} selected`);
  const send = () => { sum.textContent = text(); if (onChange) onChange(options.map(([v]) => String(v)).filter((v) => cur.has(v))); };
  const sum = h('summary', { class: 'mf-btn', 'aria-label': name || null, 'data-testid': testid ? testid + '-btn' : null }, text());
  const rows = options.map(([val, txt]) => {
    const cb = h('input', { type: 'checkbox', value: String(val), checked: cur.has(String(val)), 'data-testid': testid ? `${testid}-opt-${val}` : null,
      onchange: () => { if (cb.checked) cur.add(String(val)); else cur.delete(String(val)); openMfVal = String(val); send(); } });
    return h('label', { class: 'mf-opt' }, cb, h('span', null, txt));
  });
  const clear = h('button', { type: 'button', class: 'btn prod-small', 'data-testid': testid ? testid + '-clear' : null, onclick: () => { if (!cur.size) return; cur.clear(); for (const r of rows) r.querySelector('input').checked = false; send(); } }, 'Clear');
  const d = h('details', { class: 'mf', 'data-testid': testid || null }, sum, h('div', { class: 'mf-panel' }, rows, h('div', { class: 'mf-foot' }, clear)));
  if (testid && openMf === testid) { d.open = true; const back = rows.find((r) => r.querySelector('input').value === openMfVal); if (back) requestAnimationFrame(() => { try { back.querySelector('input').focus({ preventScroll: true }); } catch (e) { /* not attached yet */ } }); }
  d.addEventListener('toggle', () => { if (d.open) openMf = testid || null; else if (openMf === (testid || null)) openMf = null; });
  const esc = (e) => { if (!d.isConnected) { document.removeEventListener('keydown', esc, true); return; } if (e.key === 'Escape' && d.open) { d.open = false; const b = d.querySelector('summary'); if (b) b.focus(); } };
  document.addEventListener('keydown', esc, true);                       // on the document: a redraw may have replaced the box that had focus
  const away = (e) => { if (!d.isConnected) { document.removeEventListener('click', away, true); return; } if (d.open && !d.contains(e.target)) d.open = false; };
  document.addEventListener('click', away, true);
  return h('div', { class: 'ctl' }, label(name), d);
}

export function searchCtl({ name, value = '', placeholder = 'Search', onInput, testid } = {}) {
  const input = h('input', { type: 'search', value, placeholder, 'aria-label': name || placeholder, 'data-testid': testid || null });
  if (onInput) input.addEventListener('input', () => onInput(input.value));
  return h('div', { class: 'ctl' }, label(name), input);
}

export function numberCtl({ name, value, min, step, onChange, testid } = {}) {
  const input = h('input', { type: 'number', value: value == null ? '' : value, min, step, 'aria-label': name || null, 'data-testid': testid || null });
  if (onChange) input.addEventListener('change', () => onChange(input.value === '' ? null : Number(input.value)));
  return h('div', { class: 'ctl' }, label(name), input);
}

export function checkCtl({ label: text, checked = false, onChange, testid } = {}) {
  const cb = h('input', { type: 'checkbox', checked, 'data-testid': testid || null });
  if (onChange) cb.addEventListener('change', () => onChange(cb.checked));
  return h('label', { class: 'check' }, cb, ' ', text);
}
