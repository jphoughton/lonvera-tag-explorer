// Navigation, defined ONCE. Used by the app shell (js/controls.js buildShell) and by the Projections page (js/projection/shell.js).
// To add, rename or move a page in the menu, edit SECTIONS below and nothing else. Page behaviour (routes, Back targets, default filters) lives in js/pageconfig.js.
// This file imports nothing so the Projections page can load it without pulling in the rest of the app.
//
// Layout: top row = Lonvera | sections (Performance, Creative, Plan) | tag search | status and account.
//         second row = the pages of the active section as tabs. The active section and tab are highlighted (aria-current).

export const SECTIONS = [
  {
    key: 'performance', label: 'Performance', hint: 'What happened: spend, revenue and return by platform, campaign and creative',
    pages: [
      { key: 'overview', label: 'Overview', href: '/', testid: 'nav-overview', hint: 'Every platform side by side' },
      { key: 'meta', label: 'Meta', href: '/?platform=meta', testid: 'nav-meta', hint: 'Meta: products, tags and creatives' },
      { key: 'google', label: 'Google', href: '/?platform=google', testid: 'nav-google', hint: 'Google: campaign types and campaigns' },
      { key: 'campaigns', label: 'Campaigns', href: '/campaigns?platform=all', testid: 'nav-campaigns', hint: 'Every campaign, Meta and Google' },
      { key: 'trends', label: 'Trends', href: '/trends', testid: 'nav-trends', hint: 'Key numbers by day, week or month' },
      { key: 'daily', label: 'Daily story', href: '/daily', testid: 'nav-daily', hint: 'Why yesterday went the way it did' },
      { key: 'email', label: 'Email', href: '/email', testid: 'nav-email', hint: 'Klaviyo email and text messages by flow and campaign' },
    ],
  },
  {
    key: 'creative', label: 'Creative', hint: 'What to make: weekly priorities, the production board and test-to-scale decisions',
    pages: [
      { key: 'priorities', label: 'Priorities', href: '/priorities', testid: 'nav-priorities', hint: 'What to make next, by product and tag' },
      { key: 'production', label: 'Production', href: '/priorities/production', testid: 'nav-production', hint: 'Concepts assigned to vendors, and where each stands' },
      { key: 'competitors', label: 'Competitors', href: '/competitors', testid: 'nav-competitors', hint: 'What competitors run on Meta, week by week' },
      { key: 'test-to-scale', label: 'Test to scale', href: '/promotions', testid: 'nav-test-to-scale', hint: 'Which testing creatives to scale or kill' },
    ],
  },
  {
    key: 'plan', label: 'Plan', hint: 'What will happen: the projections model',
    pages: [
      { key: 'projections', label: 'Projections', href: '/projection', testid: 'nav-projections', hint: 'Pacing, scenarios, profit and loss, inventory and cash', external: true },
      { key: 'inventory-history', label: 'Inventory history', href: '/inventory/history', testid: 'nav-inventory-history', hint: 'Units on hand for any past day, every warehouse' },
    ],
  },
];

// The search box in the top bar. It is the front door of the Tag lookup page (/lookup, which keeps working on its own).
export const SEARCH = { action: '/lookup', param: 'q', label: 'Tag lookup', placeholder: 'Look up an ad name or ID', testid: 'nav-lookup' };

// Which section and tab a URL belongs to. First match wins, so the longer paths come first.
// platform: '/' defaults to the Overview (platform=all); '/campaigns' always belongs to the Campaigns tab.
const RESOLVE = [
  { re: /^\/projection(\/|$)/, section: 'plan', page: 'projections' },
  { re: /^\/competitors\/?$/, section: 'creative', page: 'competitors' },
  { re: /^\/priorities\/production\/?$/, section: 'creative', page: 'production' },
  { re: /^\/priorities\/vendor\//, section: 'creative', page: 'production' },
  { re: /^\/priorities(\/|$)/, section: 'creative', page: 'priorities' },
  { re: /^\/promotions\/?$/, section: 'creative', page: 'test-to-scale' },
  { re: /^\/lookup\/?$/, section: 'creative', page: null },
  { re: /^\/connect-agent\/?$/, section: null, page: null },
  { re: /^\/inventory\/history\/?$/, section: 'plan', page: 'inventory-history' },
  { re: /^\/trends\/?$/, section: 'performance', page: 'trends' },
  { re: /^\/daily\/?$/, section: 'performance', page: 'daily' },
  { re: /^\/email\/?$/, section: 'performance', page: 'email' },
  { re: /^\/campaigns\/?$/, section: 'performance', page: 'campaigns' },
  { re: /^\/(p|ad)\//, section: 'performance', page: 'meta' },
  { re: /^\/google\/?$/, section: 'performance', page: 'google' },
  { re: /^\/?$/, section: 'performance', page: (p) => (p.platform === 'meta' ? 'meta' : p.platform === 'google' ? 'google' : 'overview') },
];

export function resolveNav(path, params = {}) {
  for (const r of RESOLVE) {
    if (r.re.test(path)) return { section: r.section, page: typeof r.page === 'function' ? r.page(params) : r.page };
  }
  return { section: null, page: null };
}

export const sectionByKey = (key) => SECTIONS.find((s) => s.key === key) || null;
export const sectionHome = (s) => s.pages[0].href;

// ---------- rendering (plain DOM, no dependencies) ----------
function el(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') e.className = v;
    else if (k.startsWith('on') && typeof v === 'function') e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v === true ? '' : String(v));
  }
  for (const c of kids.flat()) if (c != null && c !== false) e.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return e;
}

// Builds the nav pieces. The caller places them (the app shell puts Back and the account buttons around them).
//   opts.onSearch(text)  called on submit instead of the native GET to /lookup?q=...
//   opts.dataPg          add data-pg to links that stay inside the Projections app (it routes those itself)
// Returns { brand, groups, search, subnav, setCurrent(path, params) }.
export function renderNav(opts = {}) {
  const brand = el('a', { class: 'brand', href: '/' }, 'Lonvera');
  const groups = el('nav', { class: 'navgroups', 'aria-label': 'Sections' },
    SECTIONS.map((s) => el('a', { class: 'navlink navgroup', href: sectionHome(s), 'data-section': s.key, 'data-testid': 'nav-section-' + s.key, title: s.hint, target: s.pages[0].external ? '_self' : null, 'data-pg': opts.dataPg && s.pages[0].external ? '' : null }, s.label)));
  const input = el('input', { type: 'search', name: SEARCH.param, id: 'nav-search', placeholder: SEARCH.placeholder, 'aria-label': SEARCH.label + ': paste an ad name, ad ID, Concept ID or Ad Family', autocomplete: 'off', 'data-testid': SEARCH.testid + '-input' });
  const go = el('button', { type: 'button', class: 'btn small', 'data-testid': SEARCH.testid + '-go', onclick: () => search.requestSubmit() }, 'Look up');   // not type=submit: Enter in the one-field form submits it
  const search = el('form', { class: 'navsearch', role: 'search', action: SEARCH.action, method: 'get', 'data-testid': SEARCH.testid }, input, go);
  search.addEventListener('submit', (e) => {
    const text = input.value.trim();
    if (!opts.onSearch) return;
    e.preventDefault();
    opts.onSearch(text);
  });
  const subnav = el('nav', { class: 'subnav', id: 'subnav', 'aria-label': 'Pages in this section', 'data-testid': 'subnav' });

  function setCurrent(path, params = {}) {
    const cur = resolveNav(path, params);
    for (const a of groups.querySelectorAll('a[data-section]')) {
      const on = a.dataset.section === cur.section;
      a.classList.toggle('on', on);
      if (on) a.setAttribute('aria-current', 'true'); else a.removeAttribute('aria-current');
    }
    const sec = sectionByKey(cur.section);
    subnav.replaceChildren();
    if (!sec) { subnav.hidden = true; return cur; }
    subnav.hidden = false;
    for (const pg of sec.pages) {
      const on = pg.key === cur.page;
      subnav.append(el('a', {
        class: 'navtab' + (on ? ' on' : ''), href: pg.href, title: pg.hint, 'data-testid': pg.testid, 'data-page': pg.key,
        'aria-current': on ? 'page' : null, target: pg.external ? '_self' : null, 'data-pg': opts.dataPg && pg.external ? '' : null,
      }, pg.label));
    }
    const act = subnav.querySelector('.navtab.on');            // keep the current tab in view when the row scrolls (phones)
    if (act && subnav.scrollWidth > subnav.clientWidth) subnav.scrollLeft = Math.max(0, act.offsetLeft - 24);
    return cur;
  }
  return { brand, groups, search, subnav, setCurrent };
}
