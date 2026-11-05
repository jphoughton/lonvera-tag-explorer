// Marketing metrics, platform All: one overview of every platform Upstack serves. Per platform three rows: the platform's own reporting, then Upstack's
// any-click attribution on new customers and on all orders. Spend, clicks and impressions are the platform's in every row.
import { h, fmt, href, apiWindow, globalParams, empty, platformUrl } from '../util.js';
import { api } from '../api.js';
import { createTable } from '../table.js';
import { metricColumns } from '../columns.js';
import { setRangeInfo, setPendingNote } from '../controls.js';
import { breadcrumbs, viewSwitch } from '../components.js';
import { createTrendPanel } from '../trend.js';
import { totalsStrip } from '../kpis.js';

const KEYS = ['spend', 'impressions', 'link_clicks', 'ctr', 'cpc', 'purchases', 'revenue', 'roas', 'cpa'];
// one neutral header per column: what a row counts is said by its own Numbers cell (new customers, orders, or the platform's purchases)
const NEUTRAL = { purchases: 'Purchases / orders', revenue: 'Revenue', roas: 'ROAS', cpa: 'Cost per purchase', link_clicks: 'Clicks' };
const NEUTRAL_TITLE = {
  purchases: 'What the row counts: purchases as the platform attributes them, new customers (first orders) or all orders credited by Upstack',
  revenue: 'The conversion value or order totals of what the row counts', roas: 'Revenue / spend', cpa: 'Spend / purchases (or new customers, or orders)',
  link_clicks: 'Meta link clicks; Google all clicks',
};

// the trend source of the All pages: spend bars split by platform, one line per platform. Bars are limited to what may be added across platforms
// (spend, impressions, clicks). `basis` adds the chart's own Numbers choice (the overview shows all three bases in its table, so the chart needs one).
export function platformTrendSource(p, wp, filt = {}, { basis = false } = {}) {
  const unfiltered = !filt.only && !filt.product && !filt.type;
  return {
    splits: [['platform', 'Platform']], defaultSplit: 'platform', alwaysSplit: true, barsMetrics: ['spend', 'impressions', 'link_clicks'],
    total: true, totalLine: unfiltered,                    // total spend in the tooltip and over the bars; 'NC ROAS (total)' is a Line choice (and the default) when no filter hides it
    basis: basis ? { param: 'nb', options: [['own', 'Own reporting'], ['nc', 'Upstack · new customers'], ['all', 'Upstack · all orders']], default: 'own' } : null,
    fetch: ({ grain, basis: nb, signal }) => api('/api/platforms/trend', { ...wp, grain, ...(basis ? { numbers: nb } : {}), ...filt }, { signal }),
  };
}

export async function allPlatformsPage(ctx) {
  const { params: p, signal } = ctx;
  const G = globalParams(p);
  document.title = 'All platforms · Marketing metrics';
  const d = await api('/api/platforms/overview', apiWindow(p), { signal });
  if (!ctx.alive()) return;
  if (p.compare || p.cfrom) setPendingNote('Comparison shows in the totals strip only; the chart, the tables and the Stage / Active only filters do not apply to All platforms.');
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
  ctx.mount(h('div', { class: 'page', 'data-level': 'platforms' },
    h('div', { class: 'pagehead' }, h('div', { class: 'titleline' }, h('h1', 'All platforms'), h('span', { class: 'badge muted-badge' }, 'Meta and Google'), h('span', { class: 'spacer' }), viewSwitch('home', G, 'all')),
      h('div', { class: 'note-line' }, 'Each platform’s own reporting, then Upstack’s attribution of the same spend on new customers and on all orders.')),
    h('div', { class: 'crumbrow' }, breadcrumbs([{ label: 'Marketing metrics', href: href('/', G) }, { label: 'All platforms' }])),
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
