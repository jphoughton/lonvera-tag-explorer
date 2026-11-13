// One table for how every page of the app behaves around the page itself: its URL pattern, where Back goes when there is no in-app history,
// which global filters it shows, and any default range. app.js reads this table for routing, Back and the default window.
// Menu entries and the active tab live in js/nav.js; page content lives in js/pages/*.js.
//
// Fields
//   id       key used by app.js to find the page function (PAGE_FNS in app.js)
//   re       URL pattern (pathname)
//   public   true = no login needed, no filter bar
//   bare     true = no app navigation at all (a vendor's own link)
//   filters  global filter bar controls this page uses; [] hides the bar. Names: platform, source, range, stage, compare.
//            (Pages with their own controls, like Trends and Priorities, declare [] and draw their own with js/ui/pagefilters.js.)
//   parent   (ctx) => URL | null: where Back goes with no history. ctx = { path, p (URL params), G (global filter params) }
//   defaults { window: '30d' } applied to the URL when the page opens without a date window (unless skipDefaultsWhen(p) says so)

import { href, platformUrl, currentPlatform, getParentHint, hasDateWindow } from './util.js';

const ALL_FILTERS = ['platform', 'source', 'range', 'stage', 'compare'];
const toOverview = ({ G }) => href('/', G);

// A product page's parent is the level above it in the tag tree; the top level goes to Meta's Products table, not the All roll-up
function productParent({ path, p, G }) {
  const m = path.match(/^\/p\/([^/]+)/);
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
  return platformUrl('/', 'meta', G);
}

export const PAGES = [
  { id: 'login', re: /^\/login\/?$/, public: true, filters: [] },
  { id: 'home', re: /^\/?$/, filters: ALL_FILTERS, parent: ({ p, G }) => (p.type ? platformUrl('/', 'google', G) : null) },
  { id: 'campaigns', re: /^\/campaigns\/?$/, filters: ALL_FILTERS, parent: ({ G }) => platformUrl('/', currentPlatform(), G) },
  // Test to scale is about stages and has no comparison; it opens on the last 30 days
  { id: 'promotions', re: /^\/promotions\/?$/, filters: ['platform', 'source', 'range'], parent: toOverview, defaults: { window: '30d' }, skipDefaultsWhen: (p) => hasDateWindow(p) || p.window === 'launch' },
  { id: 'trends', re: /^\/trends\/?$/, filters: [], parent: toOverview },
  { id: 'inventory-history', re: /^\/inventory\/history\/?$/, filters: [], parent: toOverview },
  { id: 'email', re: /^\/email\/?$/, filters: [], parent: toOverview },
  { id: 'priorities', re: /^\/priorities\/?$/, filters: [], parent: toOverview },
  { id: 'priorities-view', re: /^\/priorities\/v\/([A-Za-z0-9_-]+)\/?$/, filters: [], parent: () => href('/priorities', {}) },
  { id: 'production', re: /^\/priorities\/production\/?$/, filters: [], parent: () => href('/priorities', {}) },
  { id: 'vendor', re: /^\/priorities\/vendor\/([A-Za-z0-9_-]+)\/?$/, bare: true, public: true, filters: [] },       // a vendor's own link: no login, no app navigation
  { id: 'lookup', re: /^\/lookup\/?$/, filters: [], parent: toOverview },
  { id: 'connect-agent', re: /^\/connect-agent\/?$/, filters: [], parent: toOverview },
  { id: 'product', re: /^\/p\/([^/]+)\/?$/, filters: ALL_FILTERS, parent: productParent },
  { id: 'ad', re: /^\/ad\/([^/]+)\/?$/, filters: ALL_FILTERS, parent: ({ G }) => getParentHint() || platformUrl('/', 'meta', G) },
];

export const pageFor = (path) => PAGES.find((r) => r.re.test(path)) || null;
