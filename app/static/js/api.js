import { qs } from './util.js';

export class AuthError extends Error {}
export class ApiError extends Error {
  constructor(message, status) { super(message); this.status = status; }
}

let onUnauthorized = () => {};
export function setUnauthorizedHandler(fn) { onUnauthorized = fn; }

export async function api(path, params, { signal, method = 'GET', body } = {}) {
  const res = await fetch(path + qs(params || {}), {
    method,
    credentials: 'include',
    signal,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (res.status === 401 && path !== '/api/login') {
    onUnauthorized();
    throw new AuthError('unauthorized');
  }
  if (res.status === 204) return null;
  let data = null;
  try { data = await res.json(); } catch { /* non-JSON error page */ }
  if (!res.ok) throw new ApiError((data && data.error) || `HTTP ${res.status}`, res.status);
  return data;
}
