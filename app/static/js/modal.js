// Accessible modal dialogs (stackable): Esc closes the top one, click outside closes, focus is trapped and restored.
import { h } from './util.js';

const stack = [];
const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

function onKey(e) {
  const top = stack[stack.length - 1];
  if (!top) return;
  if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); top.close(); return; }
  if (e.key === 'Tab') {
    const items = [...top.box.querySelectorAll(FOCUSABLE)].filter((x) => x.offsetParent !== null || x === document.activeElement);
    if (!items.length) { e.preventDefault(); top.box.focus(); return; }
    const first = items[0], last = items[items.length - 1];
    if (e.shiftKey && (document.activeElement === first || !top.box.contains(document.activeElement))) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && (document.activeElement === last || !top.box.contains(document.activeElement))) { e.preventDefault(); first.focus(); }
    return;
  }
  if (top.onKey) top.onKey(e);
}
document.addEventListener('keydown', onKey, true);

/* opts: { label, className, build(box, modal), onKey(e), onClose() } */
export function openModal(opts) {
  const prev = document.activeElement;
  const backdrop = h('div', { class: 'modal-backdrop' });
  const closeBtn = h('button', { type: 'button', class: 'modal-close', 'aria-label': 'Close' }, '×');
  const box = h('div', { class: 'modal ' + (opts.className || ''), role: 'dialog', 'aria-modal': 'true', 'aria-label': opts.label || 'Dialog', tabindex: '-1' }, closeBtn);
  backdrop.append(box);
  const modal = {
    el: backdrop, box, onKey: opts.onKey, closeBtn,
    close() {
      const i = stack.indexOf(modal);
      if (i < 0) return;
      stack.splice(i, 1);
      backdrop.remove();
      if (!stack.length) document.body.classList.remove('modal-open');
      if (opts.onClose) opts.onClose();
      if (prev && prev.isConnected && typeof prev.focus === 'function') prev.focus({ preventScroll: true });
    },
  };
  closeBtn.addEventListener('click', () => modal.close());
  backdrop.addEventListener('mousedown', (e) => { if (e.target === backdrop) modal.close(); }); // click outside
  if (opts.build) opts.build(box, modal);
  stack.push(modal);
  document.body.append(backdrop);
  document.body.classList.add('modal-open');
  closeBtn.focus({ preventScroll: true });
  return modal;
}

export function closeAllModals() {
  while (stack.length) stack[stack.length - 1].close();
}
export const modalDepth = () => stack.length;
