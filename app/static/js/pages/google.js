// Marketing metrics, platform Google: campaign types and campaigns from Upstack's Google feeds. No creatives, so no tag tree: two tables and a trend chart.
// Two views of one page: the overview ('/?platform=google': trend, campaign types, campaigns) and Campaigns ('/campaigns?platform=google': trend and the campaigns table first, the types as a filter).
// Numbers: Google's own reporting (default) or Upstack's attribution (Numbers from switch, same as the Meta pages). See app/google.py.
import { h, fmt, href, apiWindow, globalParams, isNum, metricLabel, colPref, setColPref, refreshCmp } from '../util.js';
import { api } from '../api.js';
import { createTable } from '../table.js';
import { metricColumns } from '../columns.js';
import { setRangeInfo } from '../controls.js';
import { breadcrumbs, viewSwitch, srcBadge } from '../components.js';
import { trendChart, BAR_COLOR, LINE_COLOR } from '../charts.js';

const TYPES = ['Shopping', 'Search', 'Performance Max', 'Demand Gen', 'Other'];
const KEYS = ['spend', 'spend_share', 'impressions', 'link_clicks', 'ctr', 'cpc', 'cpm', 'conv_rate', 'purchases', 'revenue', 'roas', 'cpa'];

const BARS = [['spend', () => 'Spend', 'money'], ['purchases', () => metricLabel('purchases', 'Purchases'), 'int'], ['revenue', () => metricLabel('revenue', 'Revenue'), 'money'],
  ['link_clicks', () => 'Clicks', 'int'], ['impressions', () => 'Impressions', 'int']];
const LINES = [['roas', () => metricLabel('roas', 'ROAS'), 'roas'], ['cpa', () => metricLabel('cpa', 'CPA'), 'money'], ['ctr', () => 'CTR', 'rate'], ['cpc', () => 'CPC', 'money2'],
  ['conv_rate', () => 'Conv rate', 'rate']];

// Google's own words for the columns: clicks are all clicks, the numbers are Google's whichever source is chosen for purchases / revenue
function googleColumns(info) {
  const own = !info || !info.source || info.source.key !== 'upstack';
  const basis = info && info.source && info.source.basis;
  const t = {
    spend: 'Google spend in the selected range', impressions: 'Google impressions', link_clicks: 'Google clicks (all clicks)', ctr: 'CTR = clicks / impressions', cpc: 'Cost per click = spend / clicks',
    cpm: 'Cost per 1,000 impressions', conv_rate: own ? 'Purchases / clicks' : (basis === 'nc' ? 'New customers / clicks' : 'Orders / clicks'),
    purchases: own ? 'Purchases as Google counts them' : (basis === 'nc' ? 'Upstack new customers credited to the campaign (first orders; any-click, 30-day window)' : 'Upstack orders credited to the campaign (new and returning customers; any-click, 30-day window)'),
    revenue: own ? 'Conversion value as Google reports it' : (basis === 'nc' ? 'Upstack new-customer revenue (order totals)' : 'Upstack revenue of the credited orders (order totals)'),
    roas: 'Revenue / spend', cpa: 'Spend / purchases',
  };
  return metricColumns({ keys: KEYS }).map((c) => {
    if (c.key === 'link_clicks') c.label = () => 'Clicks';
    if (c.key === 'revenue') c.label = () => metricLabel('revenue', 'Revenue');
    if (t[c.key]) c.title = t[c.key];
    return c;
  });
}

function colChips() {
  const mk = (g, label, title) => {
    const b = h('button', { type: 'button', class: 'cchip', 'data-cols': g, 'aria-pressed': String(colPref(g)), title }, label);
    b.addEventListener('click', () => {
      const on = !colPref(g);
      setColPref(g, on);
      document.querySelectorAll(`.cchip[data-cols=${g}]`).forEach((x) => x.setAttribute('aria-pressed', String(on)));
      refreshCmp();
    });
    return b;
  };
  return h('div', { class: 'ctl colchips' }, srcBadge(), h('span', { class: 'ctl-name' }, 'Columns'),
    h('span', { class: 'chips', role: 'group', 'aria-label': 'Columns' }, mk('traffic', 'Traffic', 'Impressions, Clicks, CTR, CPC'), mk('advanced', 'Advanced', 'CPM, Conv rate')));
}

function typeBadge(t) { return h('span', { class: 'badge muted-badge', title: 'Campaign type, read from the campaign name' }, t); }

const basePath = () => (location.pathname === '/campaigns' ? '/campaigns' : '/');

function typesTable(rows, total, G, info) {
  const labelCol = { key: 'label', label: 'Campaign type', sticky: true, sort: (r) => r.label, total: () => 'Total',
    cell: (r) => h('a', { href: href(basePath(), { ...G, type: r.key }), class: 'rowlink' }, r.label) };
  const countCol = { key: 'active', label: 'Campaigns with spend', num: true, sort: (r) => r.m.active_campaigns, cell: (r) => fmt.int(r.m.active_campaigns), total: (t) => fmt.int(t.m.active_campaigns),
    title: 'Campaigns that spent in the selected range' };
  return createTable({ columns: [labelCol, countCol, ...googleColumns(info)], rows, total: { m: total }, rowKey: (r) => r.key, rowHref: (r) => href(basePath(), { ...G, type: r.key }),
    tableClass: 'compact', testid: 'google-types', sortKey: 'spend', sortDir: 'desc' });
}

function campaignsTable(rows, total, G, info) {
  const labelCol = { key: 'label', label: 'Campaign', sticky: true, sort: (r) => r.label, total: () => 'Total',
    cell: (r) => h('div', { class: 'cell-label-text' },
      h('div', { class: 'nm' }, h('span', { class: 'rowname', title: r.label }, r.label)),
      h('div', { class: 'sub' }, typeBadge(r.campaign_type), r.in_feed ? null : h('span', { class: 'badge muted-badge' }, 'not in the Google feed'),
        r.status && r.status !== 'ENABLED' ? h('span', { class: 'muted small' }, ` ${r.status.toLowerCase()}`) : null,
        r.status === 'ENABLED' ? h('span', { class: 'muted small' }, ' enabled') : null)) };
  return createTable({ columns: [labelCol, ...googleColumns(info)], rows, total: { m: total }, rowKey: (r) => r.key, tableClass: 'compact', testid: 'google-campaigns', sortKey: 'spend', sortDir: 'desc',
    emptyText: 'No campaigns in this range.' });
}

// trend: Bars + Line pickers and Day | Week, drawn with the shared trendChart
function trendPanel(p, scope, signal) {
  const st = { bars: 'spend', line: 'roas', grain: 'day' };
  const canvas = h('canvas', { 'aria-label': 'Google trend' });
  const note = h('div', { class: 'trend-note' });
  let chart = null, seq = 0;
  const sel = (id, opts, key) => {
    const el = h('select', { id, 'aria-label': id }, opts.map(([k, l]) => h('option', { value: k }, l())));
    el.value = st[key];
    el.addEventListener('change', () => { st[key] = el.value; load(); });
    return el;
  };
  const barSel = sel('g-bars', BARS, 'bars'), lineSel = sel('g-line', LINES, 'line');
  const grainBtns = ['day', 'week'].map((g) => {
    const b = h('button', { type: 'button', class: 'seg', 'aria-pressed': String(g === st.grain), 'data-grain': g }, g === 'day' ? 'Day' : 'Week');
    b.addEventListener('click', () => { st.grain = g; grainBtns.forEach((x) => x.setAttribute('aria-pressed', String(x.dataset.grain === g))); load(); });
    return b;
  });
  async function load() {
    const my = ++seq;
    let d;
    try { d = await api('/api/google/trend', { ...apiWindow(p), grain: st.grain, ...scope }, { signal }); } catch (e) { if (e && e.name === 'AbortError') return; note.textContent = 'Trend unavailable: ' + e.message; return; }
    if (my !== seq || !canvas.isConnected) return;
    const pts = d.points;
    const b = BARS.find((x) => x[0] === st.bars), l = LINES.find((x) => x[0] === st.line);
    const cmp = d.cmp_points || null;
    const ds = [{ axis: 'left', type: 'bar', kind: b[2], label: b[1](), data: pts.map((x) => (isNum(x[b[0]]) ? x[b[0]] : null)), color: BAR_COLOR },
      { axis: 'right', type: 'line', kind: l[2], label: l[1](), data: pts.map((x) => (isNum(x[l[0]]) ? x[l[0]] : null)), color: LINE_COLOR, width: 2.4, partialDash: true }];
    if (cmp) {
      const pad = (arr) => pts.map((_, i) => (arr[i] && isNum(arr[i][b[0]]) ? arr[i][b[0]] : null));
      ds.push({ axis: 'left', type: 'bar', kind: b[2], label: `B ${b[1]()}`, data: pad(cmp), color: '#6b7684', outline: true, dash: [3, 3], order: 3, cmpDates: pts.map((_, i) => (cmp[i] ? cmp[i].t : null)) });
    }
    if (chart) chart.destroy();
    chart = trendChart(canvas, { labels: pts.map((x) => x.t), grain: st.grain, datasets: ds, left: { kind: b[2], title: `${b[1]()} (bars, left)` }, right: { kind: l[2], title: `${l[1]()} (line, right)` },
      partial: pts.map((x) => !!x.partial) });
    note.textContent = (d.window_info && d.window_info.note) || '';
  }
  const el = h('div', { class: 'card trendcard', 'data-testid': 'google-trend' },
    h('div', { class: 'card-head' }, h('h3', 'Trend')),
    h('div', { class: 'ctl-row' },
      h('div', { class: 'ctl' }, h('span', { class: 'ctl-name' }, 'Bars'), barSel),
      h('div', { class: 'ctl' }, h('span', { class: 'ctl-name' }, 'Line'), lineSel),
      h('div', { class: 'ctl' }, h('span', { class: 'ctl-name' }, 'Grain'), h('span', { class: 'segs' }, grainBtns))),
    h('div', { class: 'chartbox' }, canvas), note);
  return { el, load };
}

export async function googlePage(ctx) {
  const { params: p, signal } = ctx;
  const G = { ...globalParams(p), platform: 'google' };
  const wp = apiWindow(p);
  const type = TYPES.includes(p.type) ? p.type : null;
  const campaignsView = location.pathname === '/campaigns';
  const base = basePath();
  document.title = (campaignsView ? 'Google campaigns' : 'Google') + ' · Marketing metrics';
  const [types, camps] = await Promise.all([
    api('/api/google/rollup', { group: 'type', ...wp }, { signal }),
    api('/api/google/rollup', { group: 'campaign', ...(type ? { type } : {}), ...wp }, { signal }),
  ]);
  if (!ctx.alive()) return;
  setRangeInfo(p, types.window_info, types.compare_info || null, { unclassified: false });
  const panel = trendPanel(p, type ? { type } : {}, signal);
  const root = { label: 'Marketing metrics', href: href('/', { ...G, platform: null }) };
  const crumbs = [root, { label: campaignsView ? 'Google campaigns' : 'Google', href: type ? href(base, G) : null }, ...(type ? [{ label: type }] : [])];
  if (crumbs.length === 2) crumbs[1].href = null;
  const tTypes = typesTable(types.rows, types.total.m, G, types.window_info);
  const tCamps = campaignsTable(camps.rows, camps.total.m, G, types.window_info);
  // Campaigns view: the types are a filter row above the campaigns table (the overview shows them as a table of their own)
  const typeChips = h('div', { class: 'ctl', id: 'type-ctl' }, h('span', { class: 'ctl-name' }, 'Campaign type'),
    h('span', { class: 'segs', role: 'group', 'aria-label': 'Campaign type', 'data-testid': 'type-filter' },
      h('a', { class: 'seg', href: href(base, G), 'data-type': 'all', 'aria-pressed': String(!type) }, 'All'),
      types.rows.filter((r) => (r.m.spend || 0) > 0 || r.key === type).map((r) => h('a', { class: 'seg', href: href(base, { ...G, type: r.key }), 'data-type': r.key, 'aria-pressed': String(type === r.key) }, r.label))));
  const campHead = h('div', { class: 'tablehead' }, h('h2', { id: 'campaigns-title' }, type ? `Campaigns · ${type}` : 'Campaigns'),
    campaignsView ? colChips() : null, type && !campaignsView ? h('a', { class: 'btnlink', href: href(base, G) }, 'All types') : null);
  ctx.mount(h('div', { class: 'page', 'data-level': campaignsView ? 'google-campaigns' : 'google' },
    h('div', { class: 'pagehead' }, h('div', { class: 'titleline' }, h('h1', campaignsView ? 'Campaigns' : 'Google'), h('span', { class: 'badge muted-badge', title: 'Read from Upstack: its Google ad-platform feed and its attribution' }, 'Google · Upstack-fed · campaign level'),
      h('span', { class: 'spacer' }), viewSwitch(campaignsView ? 'campaigns' : 'home', G, 'google'))),
    h('div', { class: 'crumbrow' }, breadcrumbs(crumbs)),
    panel.el,
    campaignsView ? h('div', { class: 'ctl-row' }, typeChips) : null,
    campaignsView ? null : h('div', { class: 'tablehead' }, h('h2', { id: 'level-title' }, 'Campaign types'), colChips()),
    campaignsView ? null : h('div', { class: 'note-line' }, 'Upstack exposes no Google campaign type, so it is read from the campaign name (Shopping, Search, Performance Max, Demand Gen; a name that says none of them is Other).'),
    campaignsView ? null : tTypes.el,
    campHead,
    tCamps.el));
  panel.load();
}
