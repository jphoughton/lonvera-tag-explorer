import { $, h, fmt, setRenderer, navigate, currentParams, qs, globalParams, hasDateWindow, compareOn, cleanParams, setViewStateProvider, clearCmpRefreshers, loadViewState, navIndex, setParentHint, currentPlatform } from './js/util.js';
import { setUnauthorizedHandler, AuthError, ApiError } from './js/api.js';
import { buildShell, showControls, syncControls, syncNav, setNavVisible, loadStatus, setRangeInfo, getStatus, setBackDisabled, setPendingNote, isAuthDisabled } from './js/controls.js';
import { PAGES, pageFor } from './js/pageconfig.js';
import { destroyCharts } from './js/charts.js';
import './js/lightbox.js';
import { closeAllModals } from './js/modal.js';
import { loginPage } from './js/pages/login.js';
import { productsPage } from './js/pages/products.js';
import { productPage } from './js/pages/product.js';
import { adPage } from './js/pages/ad.js';
import { googlePage } from './js/pages/google.js';
import { campaignsPage } from './js/pages/campaigns.js';
import { allPlatformsPage } from './js/pages/platforms.js';
import { allCampaignsPage } from './js/pages/all_campaigns.js';
import { promotionsPage } from './js/pages/promotions.js';
import { trendsPage } from './js/pages/trends.js';
import { prioritiesPage, prioritiesViewPage } from './js/pages/priorities.js';
import { productionPage, vendorPage } from './js/pages/priorities_production.js';
import { competitorsPage } from './js/pages/competitors.js';
import { lookupPage } from './js/pages/lookup.js';
import { connectAgentPage } from './js/pages/connect_agent.js';

// Platform pages: '/' is the home of the chosen platform (platform=all overview, google, else Meta's products) and '/campaigns' its Campaigns view.
// Google's campaigns are part of its one page, so its Campaigns view is the same page with the campaigns table first.
const homePage = (ctx) => { const pl = currentPlatform(); return pl === 'all' ? allPlatformsPage(ctx) : pl === 'google' ? googlePage(ctx) : productsPage(ctx); };
const campaignsRoute = (ctx) => { const pl = currentPlatform(); return pl === 'google' ? googlePage(ctx) : pl === 'all' ? allCampaignsPage(ctx) : campaignsPage(ctx); };
// page functions by id; the URL patterns, Back targets, filters and defaults of each page are in js/pageconfig.js (PAGES)
const PAGE_FNS = {
  login: loginPage, home: homePage, campaigns: campaignsRoute, promotions: promotionsPage, trends: trendsPage, priorities: prioritiesPage, 'priorities-view': prioritiesViewPage,
  production: productionPage, competitors: competitorsPage, vendor: vendorPage, lookup: lookupPage, 'connect-agent': connectAgentPage, product: productPage, ad: adPage,
};
const routes = PAGES.map((r) => ({ ...r, page: PAGE_FNS[r.id] }));

let token = 0;
let abort = null;
const view = () => $('#view');

setUnauthorizedHandler(() => {
  if (location.pathname.startsWith('/login') || isAuthDisabled()) return;
  const next = location.pathname + location.search;
  navigate('/login' + qs({ next }), { replace: true, scroll: false });
});

// the parent level of the current URL (used by Back when there is no in-app history): the `parent` of the page's entry in js/pageconfig.js
function parentUrl() {
  const path = location.pathname;
  const route = pageFor(path);
  if (!route || !route.parent) return null;
  const p = currentParams();
  return route.parent({ path, p, G: globalParams(p) });
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

// default filters of the page (its `defaults` in js/pageconfig.js; Test to scale opens on the last 30 days), then: comparison needs a date window for A,
// so a link with compare + Since launch switches A to 7d
function normalize(params, route) {
  if (route && route.defaults && !(route.skipDefaultsWhen && route.skipDefaultsWhen(params))) {
    const fixed = { ...params, ...route.defaults };
    history.replaceState(history.state, '', location.pathname + qs(fixed));
    return fixed;
  }
  if (compareOn(params) && !hasDateWindow(params)) {
    const fixed = cleanParams({ ...params, window: '7d' });
    setPendingNote('Window A switched to 7d: comparison needs a date window.');
    history.replaceState(history.state, '', location.pathname + qs(fixed));
    return fixed;
  }
  return params;
}

// the window (A) of the last page that rendered; "Reset" on the empty-range state goes back to it
let lastValid = null;
const isEmptyRange = (e) => e instanceof ApiError && e.status === 400 && /no daily data in that range/i.test(e.message || '');
const fullDay = (s) => (s ? `${fmt.day(s)}, ${String(s).slice(0, 4)}` : '–');

async function showEmptyRange(params, root, my) {
  let st = getStatus();
  if (!st) { try { st = await loadStatus(abort.signal); } catch { st = getStatus(); } }
  if (token !== my) return;
  st = st || getStatus();
  setRangeInfo(params, null, null);
  root.removeAttribute('aria-busy');
  const covers = st && st.daily_start && st.data_through ? ` Data covers ${fullDay(st.daily_start)} to ${fullDay(st.data_through)}.` : '';
  const reset = () => {
    const back = { ...params, window: undefined, from: undefined, to: undefined, ...(lastValid || {}) };
    navigate(location.pathname + qs(cleanParams(back)));
  };
  root.replaceChildren(h('div', { class: 'page' },
    h('div', { class: 'empty-range', role: 'status', 'data-testid': 'empty-range' },
      h('strong', 'No data in this range.'),
      h('span', null, covers),
      h('div', null, h('button', { type: 'button', class: 'btn', 'data-testid': 'reset-range', onclick: reset },
        'Back to the last valid range')))));
}

function showBlank(root, why) {
  root.removeAttribute('aria-busy');
  root.replaceChildren(h('div', { class: 'page' },
    h('div', { class: 'errorbox', role: 'alert', 'data-testid': 'blank-page' },
      h('strong', 'This page did not render. '),
      h('span', null, why),
      h('div', null, h('button', { type: 'button', class: 'btn', onclick: () => render({ restore: true }) }, 'Retry')))));
}

async function render(opts = {}) {
  if (/^\/projection(\/|$)/.test(location.pathname)) { location.replace(location.pathname + location.search); return; }   // Projections is a separate page (e.g. returning from /login)
  if (/^\/google\/?$/.test(location.pathname)) history.replaceState(history.state, '', '/' + qs(cleanParams({ ...currentParams(), platform: 'google' })));   // the first Google URL, kept working
  const my = ++token;
  if (abort) abort.abort();
  abort = new AbortController();
  destroyCharts();
  closeAllModals();
  clearCmpRefreshers();
  setViewStateProvider(null);
  setParentHint(null);
  const path = location.pathname;
  const route = routes.find((r) => r.re.test(path));
  document.body.classList.toggle('pri-bare', !!(route && route.bare));          // vendor page: topbar hidden
  const root = view();
  const params = route && !route.public ? normalize(currentParams(), route) : currentParams();
  updateBack();
  setNavVisible(!(route && route.id === 'login'));
  syncNav(path, params);
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
    mount(node) {
      if (token !== my) return;
      root.replaceChildren(node);
      root.removeAttribute('aria-busy');
      // an extension's cosmetic filter can hide a mounted page by class name (display:none leaves no layout boxes): say so, never a blank page
      requestAnimationFrame(() => {
        if (token === my && node.isConnected && !node.getClientRects().length) showBlank(root, 'The browser is hiding it. An ad blocker or extension may be matching a class name on this page.');
      });
    },
  };
  root.setAttribute('aria-busy', 'true');
  if (!route.public) {
    showControls(true, route.filters);
    syncControls(params);
    if (!getStatus()) loadStatus(abort.signal).then(() => { if (token === my) syncControls(params, { keepRangeInfo: true }); }).catch(() => {});      // the page's data may be in already: its label and note stay
    root.replaceChildren(h('div', { class: 'loading' }, 'Loading…'));
  }
  if (opts.scroll !== false) window.scrollTo(0, 0);
  try {
    await route.page(ctx);
    if (token === my && root.getAttribute('aria-busy') === 'true') showBlank(root, 'The page finished loading but had nothing to show.');
    if (token === my && !route.public) lastValid = { window: params.window, from: params.from, to: params.to };
  } catch (e) {
    if (e instanceof AuthError || (e && e.name === 'AbortError') || token !== my) return;
    if (isEmptyRange(e)) { await showEmptyRange(params, root, my); if (token === my) updateBack(); return; }
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

buildShell({ onBack: goBack, onSearch: (text) => navigate('/lookup' + qs({ q: text || null })) });
render({ restore: true });
