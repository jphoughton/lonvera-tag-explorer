import { h, navigate, currentParams } from '../util.js';
import { api, ApiError } from '../api.js';
import { showControls, clearStatus } from '../controls.js';

export async function loginPage(ctx) {
  showControls(false);
  clearStatus();
  document.title = 'Sign in · Tag Explorer';
  const err = h('div', { class: 'err', id: 'login-error', role: 'alert' });
  const pw = h('input', { type: 'password', id: 'password', name: 'password', autocomplete: 'current-password', required: true, autofocus: true });
  const btn = h('button', { type: 'submit', class: 'btn primary' }, 'Sign in');
  const form = h('form', { class: 'login-card' },
    h('h1', 'Tag Explorer'),
    h('label', { for: 'password' }, 'Password'),
    pw, btn, err);
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    err.textContent = '';
    btn.disabled = true;
    try {
      await api('/api/login', null, { method: 'POST', body: { password: pw.value } });
      const next = currentParams().next;
      navigate(next && next.startsWith('/') && !next.startsWith('//') && !next.startsWith('/login') ? next : '/', { replace: true });
    } catch (e2) {
      err.textContent = e2 instanceof ApiError && e2.status === 401 ? 'Wrong password.'
        : e2 instanceof ApiError && e2.status === 429 ? 'Too many attempts. Wait a minute and retry.'
        : 'Sign-in failed: ' + (e2.message || e2);
      pw.select();
    } finally {
      btn.disabled = false;
    }
  });
  ctx.mount(h('div', { class: 'login-wrap' }, form));
  pw.focus();
}
