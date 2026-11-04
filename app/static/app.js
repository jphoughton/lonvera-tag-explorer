import { $, h, setRenderer, navigate, currentParams, qs } from './js/util.js';
import { setUnauthorizedHandler, AuthError } from './js/api.js';
import { buildShell, showControls, syncControls, loadStatus, setWindowInfo, getStatus } from './js/controls.js';
import { destroyCharts } from './js/charts.js';
import { loginPage } from './js/pages/login.js';
import { productsPage } from './js/pages/products.js';
import { productPage } from './js/pages/product.js';
import { adPage } from './js/pages/ad.js';

const routes = [
  { re: /^\/login\/?$/, page: loginPage, public: true },
  { re: /^\/?$/, page: productsPage },
  { re: /^\/p\/([^/]+)\/?$/, page: productPage },
  { re: /^\/ad\/([^/]+)\/?$/, page: adPage },
];

let token = 0;
let abort = null;
const view = () => $('#view');

setUnauthorizedHandler(() => {
  if (location.pathname.startsWith('/login')) return;
  const next = location.pathname + location.search;
  navigate('/login' + qs({ next }), { replace: true, scroll: false });
});

async function render(opts = {}) {
  const my = ++token;
  if (abort) abort.abort();
  abort = new AbortController();
  destroyCharts();
  const path = location.pathname;
  const params = currentParams();
  const route = routes.find((r) => r.re.test(path));
  const root = view();
  if (!route) {
    showControls(false);
    root.replaceChildren(h('div', { class: 'page' }, h('h1', 'Not found'), h('p', null, 'No such page. '), h('a', { href: '/' }, 'Back to products')));
    return;
  }
  const ctx = {
    params,
    match: path.match(route.re),
    signal: abort.signal,
    alive: () => token === my,
    mount(node) { if (token === my) { root.replaceChildren(node); root.removeAttribute('aria-busy'); } },
  };
  root.setAttribute('aria-busy', 'true');
  if (!route.public) {
    showControls(true);
    syncControls(params);
    if (!getStatus()) loadStatus(abort.signal).then(() => { if (token === my) syncControls(params); }).catch(() => {});
    root.replaceChildren(h('div', { class: 'loading' }, 'Loading…'));
  }
  if (opts.scroll !== false) window.scrollTo(0, 0);
  try {
    await route.page(ctx);
  } catch (e) {
    if (e instanceof AuthError || (e && e.name === 'AbortError') || token !== my) return;
    console.warn('render failed', e);
    setWindowInfo(null);
    root.removeAttribute('aria-busy');
    root.replaceChildren(h('div', { class: 'page' },
      h('div', { class: 'errorbox', role: 'alert' },
        h('strong', 'Could not load this page. '),
        h('span', null, e && e.message ? e.message : String(e)),
        h('div', null, h('button', { type: 'button', class: 'btn', onclick: () => render() }, 'Retry')))));
  }
}

setRenderer(render);
window.addEventListener('popstate', () => render({ scroll: false }));

// in-app links navigate without a reload
document.addEventListener('click', (e) => {
  if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
  const a = e.target.closest('a[href]');
  if (!a || a.target || a.hasAttribute('download')) return;
  const u = new URL(a.href, location.href);
  if (u.origin !== location.origin) return;
  if (/^\/(static|media|api)\//.test(u.pathname)) return;
  e.preventDefault();
  navigate(u.pathname + u.search);
});

buildShell();
render();
