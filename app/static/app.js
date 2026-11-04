import { $, h, setRenderer, navigate, currentParams, qs, href, globalParams, hasDateWindow, compareOn, cleanParams, setViewStateProvider, clearCmpRefreshers, loadViewState, navIndex, getParentHint, setParentHint } from './js/util.js';
import { setUnauthorizedHandler, AuthError } from './js/api.js';
import { buildShell, showControls, syncControls, loadStatus, setRangeInfo, getStatus, setBackDisabled, setPendingNote, isAuthDisabled } from './js/controls.js';
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
  if (location.pathname.startsWith('/login') || isAuthDisabled()) return;
  const next = location.pathname + location.search;
  navigate('/login' + qs({ next }), { replace: true, scroll: false });
});

// the parent level of the current URL (used by Back when there is no in-app history)
function parentUrl() {
  const path = location.pathname;
  const p = currentParams();
  const G = globalParams(p);
  const m = path.match(/^\/p\/([^/]+)/);
  if (m) {
    const base = '/p/' + m[1];
    if (p.view === 'ads') {
      if (p.campaign_id) return href(base, { ...G, view: 'campaigns' });
      return href(base, { ...G, cluster: p.cluster, icp: p.icp, pain: p.pain, all: p.family_key && !p.pain ? 1 : null });
    }
    if (p.view === 'campaigns') return href(base, G);
    if (p.all) return href(base, { ...G, cluster: p.cluster, icp: p.icp, pain: p.pain });
    if (p.pain) return href(base, { ...G, cluster: p.cluster, icp: p.icp });
    if (p.icp) return href(base, { ...G, cluster: p.cluster });
    if (p.cluster) return href(base, G);
    return href('/', G);
  }
  if (/^\/ad\//.test(path)) return getParentHint() || href('/', G);
  return null;
}

function goBack() {
  if (navIndex() > 0) { history.back(); return; }
  const u = parentUrl();
  if (u) navigate(u);
}

function updateBack() {
  const onProducts = /^\/?$/.test(location.pathname);
  setBackDisabled(location.pathname.startsWith('/login') || (onProducts && navIndex() === 0));
}

// comparison needs a date window for A: a link with compare + Since launch switches A to 7d
function normalize(params) {
  if (compareOn(params) && !hasDateWindow(params)) {
    const fixed = cleanParams({ ...params, window: '7d' });
    setPendingNote('Window A switched to 7d: comparison needs a date window.');
    history.replaceState(history.state, '', location.pathname + qs(fixed));
    return fixed;
  }
  return params;
}

async function render(opts = {}) {
  const my = ++token;
  if (abort) abort.abort();
  abort = new AbortController();
  destroyCharts();
  clearCmpRefreshers();
  setViewStateProvider(null);
  setParentHint(null);
  const path = location.pathname;
  const route = routes.find((r) => r.re.test(path));
  const root = view();
  const params = route && !route.public ? normalize(currentParams()) : currentParams();
  updateBack();
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
    restore: opts.restore ? loadViewState() : null,
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
    setRangeInfo(params, null, null);
    root.removeAttribute('aria-busy');
    root.replaceChildren(h('div', { class: 'page' },
      h('div', { class: 'errorbox', role: 'alert' },
        h('strong', 'Could not load this page. '),
        h('span', null, e && e.message ? e.message : String(e)),
        h('div', null, h('button', { type: 'button', class: 'btn', onclick: () => render({ restore: true }) }, 'Retry')))));
  }
  if (token === my) updateBack();
}

setRenderer(render);
window.addEventListener('popstate', () => render({ scroll: false, restore: true }));

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

buildShell({ onBack: goBack });
document.querySelector('#topbar .navlink').after(h('a', { class: 'navlink', href: '/projection', target: '_self' }, 'Projections'));
render({ restore: true });
