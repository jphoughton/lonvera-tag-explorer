import { h, navigate, currentParams } from '../util.js';
import { api, ApiError, AuthError } from '../api.js';
import { showControls, clearStatus } from '../controls.js';

export async function loginPage(ctx) {
  showControls(false);
  clearStatus();
  document.title = 'Sign in · Marketing metrics';
  // no logins on this deployment (AUTH_DISABLED): there is no login page, go to the app
  try {
    const st = await api('/api/status', null, { signal: ctx.signal });
    if (st && st.auth === 'disabled') { navigate('/', { replace: true }); return; }
  } catch (e) {
    if (!(e instanceof AuthError) && !(e && e.name === 'AbortError') && !(e instanceof ApiError)) throw e;
  }
  if (!ctx.alive()) return;
  const err = h('div', { class: 'err', id: 'login-error', role: 'alert' });
  const user = h('input', { type: 'text', id: 'username', name: 'username', autocomplete: 'username', required: true, autofocus: true, autocapitalize: 'none', spellcheck: 'false' });
  const pw = h('input', { type: 'password', id: 'password', name: 'password', autocomplete: 'current-password', required: true, autofocus: true });
  const btn = h('button', { type: 'submit', class: 'btn primary' }, 'Sign in');
  const form = h('form', { class: 'login-card' },
    h('h1', 'Marketing metrics'),
    h('label', { for: 'username' }, 'Username'),
    user,
    h('label', { for: 'password' }, 'Password'),
    pw, btn, err);
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    err.textContent = '';
    btn.disabled = true;
    try {
      await api('/api/login', null, { method: 'POST', body: { username: user.value.trim(), password: pw.value } });
      const next = currentParams().next;
      navigate(next && next.startsWith('/') && !next.startsWith('//') && !next.startsWith('/login') ? next : '/', { replace: true });
    } catch (e2) {
      err.textContent = e2 instanceof ApiError && e2.status === 401 ? (e2.message && e2.message !== 'HTTP 401' ? e2.message : 'Invalid username or password.')
        : e2 instanceof ApiError && e2.status === 429 ? 'Too many attempts. Try again in a few minutes.'
        : 'Sign-in failed: ' + (e2.message || e2);
      pw.select();
    } finally {
      btn.disabled = false;
    }
  });
  ctx.mount(h('div', { class: 'login-wrap' }, form));
  user.focus();
}
