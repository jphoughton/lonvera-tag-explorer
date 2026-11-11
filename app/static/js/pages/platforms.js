// Overview (platform All): one overview of every platform Upstack serves. Per platform three rows: the platform's own reporting, then Upstack's
// any-click attribution on new customers and on all orders. Spend, clicks and impressions are the platform's in every row.
import { h, fmt, href, apiWindow, globalParams, empty, platformUrl } from '../util.js';
import { api } from '../api.js';
import { createTable } from '../table.js';
import { metricColumns } from '../columns.js';
import { setRangeInfo, setPendingNote } from '../controls.js';
import { breadcrumbs, viewSwitch } from '../components.js';
import { createTrendPanel, platformTrendSource } from '../trend.js';
import { totalsStrip } from '../kpis.js';
import { pageHeader } from '../ui/pagehead.js';
import { filterRow } from '../ui/pagefilters.js';

const KEYS = ['spend', 'impressions', 'link_clicks', 'ctr', 'cpc', 'purchases', 'revenue', 'roas', 'cpa'];
// one neutral header per column: what a row counts is said by its own Numbers cell (new customers, orders, or the platform's purchases)
const NEUTRAL = { purchases: 'Purchases / orders', revenue: 'Revenue', roas: 'ROAS', cpa: 'Cost per purchase', link_clicks: 'Clicks' };
// header tooltips spell out the short column names (ROAS, CTR, CPC)
const NEUTRAL_TITLE = {
  purchases: 'What the row counts: purchases as the platform attributes them, new customers (first orders) or all orders credited by Upstack',
  revenue: 'The conversion value or order totals of what the row counts', roas: 'Return on ad spend (ROAS): revenue / spend',
  ctr: 'Click-through rate (CTR): clicks / impressions', cpc: 'Cost per click (CPC): spend / clicks', cpa: 'Spend / purchases (or new customers, or orders)',
  link_clicks: 'Meta link clicks; Google all clicks',
};

export { platformTrendSource };

// Chart options: the chart keeps Grain (Day, Week, Month) in view; Bars, Line, Swap, Split bars by, Numbers and Compare overlay sit in a closed "Chart options" disclosure.
// The shared trend panel builds these controls; this only moves them (same ids, same behavior). The disclosure opens by itself when the URL already holds a chart choice.
export function tuckChartOptions(panel, { open = false } = {}) {
  const ctl = panel && panel.el && panel.el.querySelector('.trend-controls');
  if (!ctl || ctl.querySelector('.chart-options')) return;
  const grain = ctl.querySelector('#trend-grain');
  const keep = grain ? grain.closest('.ctl') : null;
  const stay = new Set(['trend-pins', 'trend-pin-hint', 'trend-status']);
  const moved = [...ctl.children].filter((c) => c !== keep && !stay.has(c.id));
  if (!moved.length) return;
  const q = new URLSearchParams(location.search);
  const chosen = open || ['tb', 'tl', 'ts', 'nb'].some((k) => q.has(k));
  const det = h('details', { class: 'chart-options', 'data-testid': 'chart-options' }, h('summary', { title: 'Choose the bars, the line, how bars are split and which numbers the chart uses' }, 'Chart options'),
    h('div', { class: 'chart-options-body' }, moved));
  if (chosen) det.open = true;
  ctl.insertBefore(det, keep ? keep.nextSibling : ctl.firstChild);
}   // moved to trend.js so the Projections page can use it without loading this page

export async function allPlatformsPage(ctx) {
  const { params: p, signal } = ctx;
  const G = globalParams(p);
  document.title = 'Overview · Lonvera';
  const d = await api('/api/platforms/overview', apiWindow(p), { signal });
  if (!ctx.alive()) return;
  if (p.compare || p.cfrom) setPendingNote('Comparison shows in the totals strip only; the chart, the tables and the Stage / Active only filters do not apply to Overview.');
  setRangeInfo(p, d.window_info, null, { unclassified: false });

  const rows = [];
  for (const pf of d.platforms) pf.rows.forEach((r, i) => rows.push({ key: pf.key + ':' + r.basis, platform: pf, first: i === 0, ...r }));
  const flat = metricColumns({ getM: (r) => r.m, keys: KEYS }).map((c) => {
    const o = { ...c, nosort: true, total: undefined };
    if (NEUTRAL[c.key]) o.label = () => NEUTRAL[c.key];
    if (NEUTRAL_TITLE[c.key]) o.title = NEUTRAL_TITLE[c.key];
    return o;
  });
  const cols = [
    { key: 'platform', label: 'Platform', sticky: true, nosort: true,
      cell: (r) => (r.first ? h('div', { class: 'cell-label-text' }, h('a', { href: platformUrl('/', r.platform.key, G), class: 'prodname' }, r.platform.name),
        h('div', { class: 'sub' }, h('a', { href: platformUrl('/campaigns', r.platform.key, G), class: 'plain' }, 'Campaigns →'))) : '') },
    { key: 'basis', label: 'Numbers', nosort: true, cls: 'labelcol',
      cell: (r) => h('div', { class: 'cell-label-text' }, h('div', { class: 'nm' }, r.label),
        r.window && r.window.clamped && r.window.from ? h('div', { class: 'sub', title: r.window.note || '' }, `${fmt.day(r.window.from)} – ${fmt.day(r.window.to)}`) : null) },
    ...flat,
  ];
  const t = createTable({ columns: cols, rows, rowKey: (r) => r.key, emptyText: 'No platform data in this window.', testid: 'platform-overview' });
  const panel = createTrendPanel({ p, mode: 'source', signal, source: platformTrendSource(p, apiWindow(p), {}, { basis: true }) });
  const strip = totalsStrip({ p, signal });
  const notes = d.platforms.filter((x) => x.note).map((x) => h('div', { class: 'muted small', 'data-testid': 'platform-note' }, `${x.name}: ${x.note}`));
  tuckChartOptions(panel);
  ctx.mount(h('div', { class: 'page', 'data-level': 'platforms' },
    pageHeader({ title: 'Overview', badge: 'Meta and Google', actions: viewSwitch('home', G, 'all'),
      description: 'Each platform’s own reporting, then Upstack’s attribution of the same spend on new customers (NC) and on all orders. ROAS means return on ad spend.' }),
    h('div', { class: 'crumbrow' }, breadcrumbs([{ label: 'Lonvera', href: href('/', G) }, { label: 'Overview' }])),
    strip.el,
    panel.el,
    h('div', { class: 'tablehead' }, h('h2', { id: 'level-title' }, 'Platforms'), h('span', { class: 'src-badge', 'data-testid': 'src-badge' }, 'Numbers: each platform’s own reporting and Upstack')),
    rows.length ? t.el : empty('No platform data in this window.'),
    h('div', { class: 'muted small total-note' }, 'Not added up: Meta and Google each credit an order to the ads or campaigns clicked, so purchases overlap between platforms. Upstack rows start when Upstack’s history does; the date range of a clamped row is shown under its name.'),
    ...notes,
  ));
  strip.load();
  panel.load();
}
