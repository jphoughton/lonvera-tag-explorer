import { h, enc, fmt, stageSet, href, apiWindow, sparkParams, globalParams, navigate, confBadge, empty, thumb, stageBadge, liveDot, pick, setParams, getRowMode, setRowMode, setViewStateProvider, cmpView, onCmpRefresh, metricLabel } from '../util.js';
import { api, AuthError } from '../api.js';
import { createTable } from '../table.js';
import { metricColumns, fixTotal, FLAT_KEYS, trendColumn, pinColumn } from '../columns.js';
import { setRangeInfo } from '../controls.js';
import { breadcrumbs, legendLine, creativeMeta, columnsChips, filterBar, saveFilterToUrl, makeFilter, adsTable, familyAdsNode, unmatchedPanel, saveSortToUrl, sortFromUrl, campaignHrefFor, coverageBar, untestedPanel } from '../components.js';
import { stageChart } from '../charts.js';
import { createTree } from '../tree.js';
import { createTrendPanel, createPins } from '../trend.js';
import { tagBadge } from './products.js';

const CONF_RANK = { low: 1, medium: 2, high: 3 };
const winInfo = (d) => d.window_info || (d.window && typeof d.window === 'object' ? d.window : null);
export const hasUnclassified = (m) => !!(m && m.stage && m.stage.unclassified && m.stage.unclassified.spend > 0);
function report(p, d, unc = false) { setRangeInfo(p, winInfo(d), d.compare_info || null, { unclassified: unc }); }

// product list cache (name / tag label for endpoints that do not return the product)
let productsCache = null;
async function getProduct(slug, signal) {
  if (!productsCache) productsCache = api('/api/products', null, { signal }).then((d) => (Array.isArray(d) ? d : d.products || []));
  let list;
  try { list = await productsCache; } catch (e) { productsCache = null; throw e; }
  return list.find((x) => x.slug === slug) || { slug, name: slug, tag_set: 'tracker', tag_label: '' };
}

export async function productPage(ctx) {
  const slug = decodeURIComponent(ctx.match[1]);
  const v = ctx.params.view;
  if (v === 'campaigns') return campaignsView(ctx, slug);
  if (v === 'ads') return adsView(ctx, slug);
  if (ctx.params.all) return familiesFlatView(ctx, slug);
  return tagsView(ctx, slug);
}

const pagePath = (slug) => '/p/' + enc(slug);

function pageHead(product, slug, p, active) {
  const G = globalParams(p);
  const mk = (label, key, to) => h('a', { class: 'seg', href: to, 'data-view': key, 'aria-current': active === key ? 'page' : null, 'aria-pressed': String(active === key) }, label);
  return h('div', { class: 'pagehead' },
    h('div', { class: 'titleline' },
      h('h1', product.name),
      tagBadge(product),
      h('span', { class: 'spacer' }),
      h('div', { class: 'ctl' },
        h('span', { class: 'ctl-name' }, 'View by'),
        h('span', { class: 'segs' }, mk('Tags', 'tags', href(pagePath(slug), G)), mk('Campaigns', 'campaigns', href(pagePath(slug), { ...G, view: 'campaigns' }))))),
    product.tag_set !== 'linemap' ? h('div', { class: 'note-line' }, 'Tracker tags — not line-mapped yet. Cluster / ICP / pain point come from the Creative Tracker columns.') : null);
}

function tagCrumbs(slug, p, product, extra = []) {
  const G = globalParams(p);
  const path = pagePath(slug);
  const crumbs = [{ label: 'Products', href: href('/', G) }, { label: product.name, href: href(path, G) }];
  if (p.cluster) crumbs.push({ label: p.cluster, href: href(path, { ...G, cluster: p.cluster }) });
  if (p.icp) crumbs.push({ label: p.icp, href: href(path, { ...G, cluster: p.cluster, icp: p.icp }) });
  if (p.pain) crumbs.push({ label: p.pain, href: href(path, { ...G, cluster: p.cluster, icp: p.icp, pain: p.pain }) });
  crumbs.push(...extra);
  if (crumbs.length === 2) crumbs[1].href = null;
  return crumbs;
}

// ============================ Tags view: dropdown tree ============================
async function tagsView(ctx, slug) {
  const { params: p, signal } = ctx;
  const G = globalParams(p);
  const path = pagePath(slug);
  const rootLevel = !p.cluster ? 0 : !p.icp ? 1 : !p.pain ? 2 : 3;
  const rootName = ['cluster', 'icp', 'pain', 'family'][rootLevel];
  const filters = {};
  if (p.cluster) filters.cluster = p.cluster;
  if (p.icp) filters.icp = p.icp;
  if (p.pain) filters.pain = p.pain;
  const wp = apiWindow(p);
  const base = `/api/products/${enc(slug)}/`;
  const [data, product, um, cov] = await Promise.all([
    api(base + 'tree', { root: rootName, ...filters, depth: 0, ...wp, ...sparkParams(p) }, { signal }),
    getProduct(slug, signal),
    api(base + 'unmatched', wp, { signal }).catch(() => null),
    api(base + 'coverage', wp, { signal }).catch(() => null),
  ]);
  if (!ctx.alive()) return;
  report(p, data, hasUnclassified(data.total && data.total.m));
  document.title = `${product.name} · Tag Explorer`;

  const first = ['Market cluster', 'ICP', 'Pain point', 'Creative'][rootLevel];
  const title = rootLevel === 0 ? 'Market clusters' : rootLevel === 1 ? `ICPs in ${p.cluster}` : rootLevel === 2 ? `Pain points in ${p.icp}` : `Creatives in ${p.pain}`;
  const untestedOn = p.untested === '1' && rootLevel === 0 && !!(cov && cov.available);     // "Not tested only": the flat list of ICPs / pain points with no ads or no spend
  const pins = createPins(ctx.restore && ctx.restore.pins);
  // the panel first: it locks / unlocks the Chart checkboxes before the table renders them
  const panel = createTrendPanel({ slug, p, scope: filters, scopeLabel: [p.pain, p.icp, p.cluster].find(Boolean) || product.name, pins, mode: 'scope', signal });
  const tree = createTree({ slug, p, signal, rootLevel, filters, first, restore: ctx.restore, pins, coverage: cov && cov.available ? cov.keys : null });
  await tree.init(data.rows, data.total);
  if (!ctx.alive()) return;

  ctx.mount(h('div', { class: 'page', 'data-level': ['cluster', 'icp', 'pain', 'families'][rootLevel] },
    pageHead(product, slug, p, 'tags'),
    h('div', { class: 'crumbrow' }, breadcrumbs(tagCrumbs(slug, p, product)), h('span', { class: 'spacer' }),
      h('a', { class: 'btnlink', id: 'toggle-all', href: href(path, { ...G, all: 1 }) }, 'All creatives')),
    rootLevel === 0 ? coverageBar(cov, { path, G, on: untestedOn, windowLabel: data.window_info && data.window_info.label, sourceLabel: data.window_info && data.window_info.source && data.window_info.source.label }) : null,
    untestedOn ? null : panel.el,
    h('div', { class: 'tablehead' }, h('h2', { id: 'level-title' }, untestedOn ? 'Not tested' : title)),
    untestedOn ? null : legendLine(),
    untestedOn ? untestedPanel(cov, { path, G, windowLabel: data.window_info && data.window_info.label }) : tree.el,
    unmatchedPanel(slug, p, um, signal),
  ));
  if (!untestedOn) { panel.load(); tree.afterMount(ctx.restore); }
}

// ============================ All creatives (flat, no tree root) ============================
async function familiesFlatView(ctx, slug) {
  const { params: p, signal } = ctx;
  const G = globalParams(p);
  const path = pagePath(slug);
  const wp = apiWindow(p);
  const base = `/api/products/${enc(slug)}/`;
  const [data, product, um] = await Promise.all([
    api(base + 'families', { cluster: p.cluster, icp: p.icp, pain: p.pain, ...wp, ...sparkParams(p) }, { signal }),
    getProduct(slug, signal),
    api(base + 'unmatched', wp, { signal }).catch(() => null),
  ]);
  if (!ctx.alive()) return;
  report(p, data, hasUnclassified(data.total && data.total.m));
  document.title = `${product.name} · Tag Explorer`;
  const linemap = product.tag_set === 'linemap';
  const rkey = (r) => r.family_key + '|' + (r.ad_ids || []).join(',');
  let filterUi = null;
  let rowMode = getRowMode();
  const flatPins = createPins(ctx.restore && ctx.restore.pins);
  const flatPanel = createTrendPanel({ slug, p, scope: pick(p, ['cluster', 'icp', 'pain']), scopeLabel: product.name, pins: flatPins, mode: 'scope', signal });
  const allAdsHeader = h('a', { href: href(path, { ...G, view: 'ads', cluster: p.cluster, icp: p.icp, pain: p.pain }), class: 'btnlink small', id: 'all-ads', title: 'Open the ads table for everything in view' }, 'All creatives in view');
  const cols = [
    {
      key: 'display', label: 'Creative', sticky: true, cls: 'labelcol', sort: (r) => r.display, total: () => 'Total',
      cell: (r) => {
        const hf = r.primary_id != null ? href('/ad/' + enc(r.primary_id), G) : null;
        return h('div', { class: 'cell-ad' },
          thumb(r.thumb_asset_id, 34, { name: r.display, primaryId: r.primary_id }),
          h('div', { class: 'cell-ad-text' },
            h('div', { class: 'nm' }, hf ? h('a', { href: hf, class: 'rowlink rowname', title: r.display }, r.display) : h('span', { class: 'rowname' }, r.display)),
            h('div', { class: 'meta' }, linemap && r.confidence ? h('span', { title: r.method || null }, confBadge(r.confidence)) : null,
              hf ? h('a', { href: hf, class: 'opn row-open' }, 'Open ›') : null,
              creativeMeta({ adType: r.ad_type, status: r.status, launch: r.launch_date, adsCount: r.ads_count, adsLive: r.ads_live }),
              (r.n_rows || 0) > 1 ? h('span', { class: 'muted' }, `${r.n_rows} tracker rows`) : null,
              r.counted_here === false ? h('span', { class: 'badge muted-badge', title: 'This creative’s metrics are counted on its first tracker row, which sits in another group' }, 'counted elsewhere') : null)));
      },
    },
    {
      key: 'hook', label: 'Hook', cls: 'hookcol', sort: (r) => r.hook, total: () => '',
      title: 'The ad’s first spoken line and on-image headline, boiled down to one line',
      cellTitle: (r) => r.hook || null,
      cell: (r) => (r.hook ? h('span', { class: 'hooktext' }, r.hook) : ''),
    },
    pinColumn({ pins: flatPins, keyOf: rkey, labelOf: (r) => r.display, scopeOf: (r) => ({ family_key: r.family_key, ...pick(p, ['cluster', 'icp', 'pain']) }) }),
    {
      key: '_ads', label: '', nosort: true, cls: 'actioncol', header: allAdsHeader,
      cell: (r) => h('a', { href: href(path, { ...G, view: 'ads', family_key: r.family_key, cluster: p.cluster, icp: p.icp, pain: p.pain }), class: 'btnlink small row-ads' }, 'Ads'),
    },
    trendColumn(),
    ...metricColumns(),
  ];
  const table = createTable({
    columns: cols, rows: data.rows, total: data.total, ...sortFromUrl(p), onSort: saveSortToUrl,
    rowKey: rkey,
    rowHref: (r) => (r.primary_id != null ? href('/ad/' + enc(r.primary_id), G) : null),
    onRowClick: (r, e, c) => { if (rowMode === 'expand') c.toggle(); else if (c.href) navigate(c.href); },
    rowClass: (r) => (r.counted_here === false ? 'not-counted' : ''),
    expand: { render: (r) => familyAdsNode(slug, p, r.family_key, signal, pick(p, ['cluster', 'icp', 'pain'])) },
    emptyText: 'No creatives here.',
    testid: 'level-table',
    onCount: (n, tot) => filterUi && filterUi.setCount(n, tot),
  });
  onCmpRefresh(() => table.refresh());
  const apply = (st) => table.setFilter(makeFilter(st, (r) => r.m && r.m.spend, (r) => [r.display, ...(r.concept_ids || []), r.ad_type, r.status, r.hook]));
  filterUi = filterBar({
    spend: p.spend === '1', q: p.q, placeholder: 'Search creative, concept id, hook…',
    onChange: (st) => { saveFilterToUrl(st); apply(st); },
  });
  if (p.spend === '1' || p.q) apply({ spend: p.spend === '1', q: p.q });
  else filterUi.setCount(data.rows.length, data.rows.length);
  const modeBtns = [['expand', 'Drops down'], ['open', 'Opens']].map(([v, t]) => {
    const b = h('button', { type: 'button', class: 'seg', 'data-rm': v, 'aria-pressed': String(rowMode === v) }, t);
    b.addEventListener('click', () => { rowMode = v; setRowMode(v); modeBtns.forEach((x) => x.setAttribute('aria-pressed', String(x.dataset.rm === v))); });
    return b;
  });

  setViewStateProvider(() => ({ pins: flatPins.list(), tscroll: { top: table.wrap.scrollTop, left: table.wrap.scrollLeft } }));
  ctx.mount(h('div', { class: 'page', 'data-level': 'families' },
    pageHead(product, slug, p, 'tags'),
    h('div', { class: 'crumbrow' }, breadcrumbs(tagCrumbs(slug, p, product, [{ label: 'All creatives' }])), h('span', { class: 'spacer' }),
      h('a', { class: 'btnlink', id: 'toggle-all', href: href(path, { ...G, cluster: p.cluster, icp: p.icp, pain: p.pain }) }, '← Back to hierarchy')),
    flatPanel.el,
    h('div', { class: 'tablehead' }, h('h2', { id: 'level-title' }, 'All creatives'),
      h('div', { class: 'ctl' }, h('span', { class: 'ctl-name' }, 'Clicking a row'), h('span', { class: 'segs', id: 'row-mode' }, modeBtns)), columnsChips(), filterUi.el),
    legendLine(),
    table.el,
    unmatchedPanel(slug, p, um, signal),
  ));
  flatPanel.load();
  const vs = ctx.restore;
  if (vs) { window.scrollTo(0, vs.scrollY || 0); if (vs.tscroll) { table.wrap.scrollTop = vs.tscroll.top || 0; table.wrap.scrollLeft = vs.tscroll.left || 0; } }
}

// ============================ Ads view (flat table, one ad per row) ============================
async function adsView(ctx, slug) {
  const { params: p, signal } = ctx;
  const G = globalParams(p);
  const path = pagePath(slug);
  const base = `/api/products/${enc(slug)}/`;
  const [d, product] = await Promise.all([
    api(base + 'ads', { ...pick(p, ['cluster', 'icp', 'pain', 'family_key', 'campaign_id']), ...apiWindow(p), ...sparkParams(p) }, { signal }),
    getProduct(slug, signal),
  ]);
  if (!ctx.alive()) return;
  report(p, d, false);
  document.title = `Ads · ${product.name} · Tag Explorer`;

  const crumbs = [{ label: 'Products', href: href('/', G) }, { label: product.name, href: href(path, G) }];
  const firstRow = (d.rows || []).find((r) => r.ad_id != null);
  if (p.campaign_id) {
    crumbs.push({ label: 'Campaigns', href: href(path, { ...G, view: 'campaigns' }) });
    crumbs.push({ label: (firstRow && firstRow.campaign_name) || p.campaign_id, href: null });
  } else {
    if (p.cluster) crumbs.push({ label: p.cluster, href: href(path, { ...G, cluster: p.cluster }) });
    if (p.icp) crumbs.push({ label: p.icp, href: href(path, { ...G, cluster: p.cluster, icp: p.icp }) });
    if (p.pain) crumbs.push({ label: p.pain, href: href(path, { ...G, cluster: p.cluster, icp: p.icp, pain: p.pain }) });
    if (p.family_key) {
      const famHref = p.pain ? href(path, { ...G, cluster: p.cluster, icp: p.icp, pain: p.pain }) : href(path, { ...G, all: 1, cluster: p.cluster, icp: p.icp });
      crumbs.push({ label: (firstRow && firstRow.display) || p.family_key, href: famHref });
    }
  }
  crumbs.push({ label: 'Ads' });

  const adsTitle = p.family_key ? `Ads running ${(firstRow && firstRow.display) || p.family_key}` : 'Ads';
  let filterUi;
  const t = adsTable(p, d, {
    ...sortFromUrl(p), onSort: saveSortToUrl,
    onCount: (n, tot) => filterUi && filterUi.setCount(n, tot),
    showFamily: !p.family_key,
    shareLabel: p.family_key ? '% of creative' : '% of spend',
    campaignHref: (r) => href(path, { ...G, view: 'ads', campaign_id: r.campaign_id }),
  });
  onCmpRefresh(() => t.refresh());
  const apply = (st) => t.setFilter(makeFilter(st, (r) => r.spend, (r) => [r.ad_name, r.campaign_name, r.adset_name, r.display, r.status]));
  filterUi = filterBar({
    spend: p.spend === '1', q: p.q, placeholder: 'Search ad, campaign, ad set…',
    onChange: (st) => { saveFilterToUrl(st); apply(st); },
  });
  if (p.spend === '1' || p.q) apply({ spend: p.spend === '1', q: p.q });
  filterUi.setCount(d.rows.length, d.rows.length);

  const adsPanel = createTrendPanel({ slug, p, scope: pick(p, ['cluster', 'icp', 'pain', 'family_key', 'campaign_id']), scopeLabel: (firstRow && (p.family_key ? firstRow.display : p.campaign_id ? firstRow.campaign_name : null)) || p.pain || p.icp || p.cluster || product.name, mode: 'scope', signal });
  setViewStateProvider(() => ({ tscroll: { top: t.wrap.scrollTop, left: t.wrap.scrollLeft } }));
  ctx.mount(h('div', { class: 'page', 'data-level': 'ads' },
    pageHead(product, slug, p, p.campaign_id ? 'campaigns' : 'tags'),
    h('div', { class: 'crumbrow' }, breadcrumbs(crumbs)),
    adsPanel.el,
    h('div', { class: 'tablehead' }, h('h2', { id: 'level-title' }, adsTitle), columnsChips(), filterUi.el),
    legendLine(),
    t.el,
  ));
  adsPanel.load();
  const vs = ctx.restore;
  if (vs) { window.scrollTo(0, vs.scrollY || 0); if (vs.tscroll) { t.wrap.scrollTop = vs.tscroll.top || 0; t.wrap.scrollLeft = vs.tscroll.left || 0; } }
}

// ============================ Campaigns view ============================
async function campaignsView(ctx, slug) {
  const { params: p, signal } = ctx;
  const G = globalParams(p);
  const path = pagePath(slug);
  const base = `/api/products/${enc(slug)}/`;
  const wp = apiWindow(p);
  const dailyParams = { ...wp };
  delete dailyParams.stage; delete dailyParams.compare; delete dailyParams.cfrom; delete dailyParams.cto;
  if (!dailyParams.window && !dailyParams.from) dailyParams.days = 90;
  const [d, daily, product, unm] = await Promise.all([
    api(base + 'campaigns', { ...wp, ...sparkParams(p) }, { signal }),
    api(base + 'stage-daily', dailyParams, { signal }),
    getProduct(slug, signal),
    // spend that is in the campaign total but not on the tracker (the Tags view leaves it out); a failure only drops the note
    api(base + 'unmatched', wp, { signal }).catch((e) => { if (e instanceof AuthError || (e && e.name === 'AbortError')) throw e; return null; }),
  ]);
  if (!ctx.alive()) return;
  report(p, d, (daily || []).some((x) => x.unclassified > 0));
  document.title = `Campaigns · ${product.name} · Tag Explorer`;

  // stage cards (A, and B when comparing)
  const included = stageSet(p);
  const stageRows = (d.by_stage || []).filter((s) => included.has(s.stage));
  const totalSpend = stageRows.reduce((a, s) => a + (s.spend || 0), 0);
  const cards = stageRows.map((s) => {
    const c = h('div', { class: 'stagecard stage-' + s.stage, 'data-stage': s.stage },
      h('div', { class: 'stagecard-head' }, stageBadge(s.stage), h('span', { class: 'muted small' }, `${fmt.int(s.campaigns)} campaigns · ${fmt.int(s.ads)} ads`)),
      h('div', { class: 'stagecard-main' }, h('span', { class: 'big' }, fmt.money(s.spend)), h('span', { class: 'muted' }, totalSpend > 0 ? ` · ${fmt.pct((s.spend || 0) / totalSpend)} of spend` : '')),
      (s.cmp && cmpView.on) ? h('div', { class: 'stagecard-cmp' }, 'vs ', fmt.money(s.cmp.spend), ' ', deltaSpan(s.delta && s.delta.spend_pct, 'neutral')) : null,
      h('div', { class: 'stagecard-sub' },
        h('span', null, metricLabel('roas', 'ROAS') + ' ', h('b', null, fmt.roas(s.roas)), (s.cmp && cmpView.on) ? [' ', h('span', { class: 'b' }, 'vs ', fmt.roas(s.cmp.roas)), ' ', deltaSpan(s.delta && s.delta.roas_pct, 'up')] : null),
        h('span', null, metricLabel('cpa', 'CPA') + ' ', h('b', null, fmt.money(s.cpa)), (s.cmp && cmpView.on) ? [' ', h('span', { class: 'b' }, 'vs ', fmt.money(s.cmp.cpa)), ' ', deltaSpan(s.delta && s.delta.cpa_pct, 'down')] : null),
        h('span', null, metricLabel('purchases', 'Purchases') + ' ', h('b', null, fmt.int(s.purchases)), (s.cmp && cmpView.on) ? [' ', h('span', { class: 'b' }, 'vs ', fmt.int(s.cmp.purchases)), ' ', deltaSpan(s.delta && s.delta.purchases_pct, 'up')] : null)));
    return c;
  });

  const canvas = h('canvas', { id: 'stage-chart', role: 'img', 'aria-label': 'Daily spend by stage' });
  const chartBox = h('div', { class: 'chartbox' }, canvas);

  const campPins = createPins(ctx.restore && ctx.restore.pins);
  const flat = metricColumns({ getM: (r) => r, getTotalM: (t) => fixTotal(t), keys: FLAT_KEYS });
  const cols = [
    { key: 'name', label: 'Campaign', sticky: true, cls: 'labelcol', sort: (r) => r.name, total: () => 'Total', cell: (r) => h('a', { href: href(path, { ...G, view: 'ads', campaign_id: r.campaign_id }), class: 'rowlink', title: r.name }, r.name) },
    pinColumn({ pins: campPins, keyOf: (r) => r.campaign_id, labelOf: (r) => r.name, scopeOf: (r) => ({ campaign_id: r.campaign_id }) }),
    trendColumn(),
    { key: 'account', label: 'Account', sort: (r) => r.account, cell: (r) => fmt.text(r.account) },
    { key: 'stage', label: 'Stage', sort: (r) => r.stage, cell: (r) => stageBadge(r.stage) },
    { key: 'ads', label: 'Ads', num: true, sort: (r) => r.ads, cell: (r) => fmt.int(r.ads), total: (t) => (t && t.ads != null ? fmt.int(t.ads) : '') },
    { key: 'families', label: 'Creatives', num: true, sort: (r) => r.families, cell: (r) => fmt.int(r.families), total: (t) => (t && t.families != null ? fmt.int(t.families) : '') },
    ...flat,
    { key: 'live', label: 'Live', sort: (r) => (r.live ? 1 : 0), cell: (r) => liveDot(r.live), total: () => '', cls: 'center' },
    { key: 'last_day', label: 'Last day', sort: (r) => r.last_day, cell: (r) => fmt.text(r.last_day), total: () => '' },
  ];
  let filterUi;
  const campPanel = createTrendPanel({ slug, p, scope: {}, scopeLabel: product.name, pins: campPins, mode: 'scope', signal });
  const t = createTable({
    columns: cols, rows: d.rows, total: d.total, ...sortFromUrl(p), onSort: saveSortToUrl,
    rowKey: (r) => r.campaign_id,
    rowHref: (r) => href(path, { ...G, view: 'ads', campaign_id: r.campaign_id }),
    emptyText: 'No campaigns in this window.',
    testid: 'campaign-table',
    onCount: (n, tot) => filterUi && filterUi.setCount(n, tot),
  });
  onCmpRefresh(() => t.refresh());
  const apply = (st) => t.setFilter(makeFilter(st, (r) => r.spend, (r) => [r.name, r.account, r.stage, r.campaign_id]));
  filterUi = filterBar({ spend: p.spend === '1', q: p.q, placeholder: 'Search campaign…', onChange: (st) => { saveFilterToUrl(st); apply(st); } });
  if (p.spend === '1' || p.q) apply({ spend: p.spend === '1', q: p.q });
  else filterUi.setCount(d.rows.length, d.rows.length);

  setViewStateProvider(() => ({ pins: campPins.list(), tscroll: { top: t.wrap.scrollTop, left: t.wrap.scrollLeft } }));
  ctx.mount(h('div', { class: 'page', 'data-level': 'campaigns' },
    pageHead(product, slug, p, 'campaigns'),
    h('div', { class: 'crumbrow' }, breadcrumbs([{ label: 'Products', href: href('/', G) }, { label: product.name, href: href(path, G) }, { label: 'Campaigns' }])),
    campPanel.el,
    h('div', { class: 'stagecards' }, cards),
    h('div', { class: 'section' }, h('div', { class: 'section-head' }, h('h2', 'Daily spend by stage'), h('span', { class: 'muted small' }, daily.length ? `${fmt.day(daily[0].day)} – ${fmt.day(daily[daily.length - 1].day)}` : '')), chartBox),
    h('div', { class: 'tablehead' }, h('h2', { id: 'level-title' }, 'Campaigns'), columnsChips(), filterUi.el),
    legendLine(),
    t.el,
    unm && unm.total && unm.total.spend > 0
      ? h('div', { class: 'muted small total-note', 'data-testid': 'campaign-total-note' },
        `Total includes spend not on the tracker: ${fmt.money(unm.total.spend)}${unm.total.families ? ` (${fmt.int(unm.total.families)} creatives, listed under “Not on the tracker” below)` : ''}. The Tags view leaves it out.`)
      : null,
    unmatchedPanel(slug, p, unm, signal),
  ));
  stageChart(canvas, Array.isArray(daily) ? daily : (daily.rows || []), included);
  campPanel.load();
  const vs = ctx.restore;
  if (vs) { window.scrollTo(0, vs.scrollY || 0); if (vs.tscroll) { t.wrap.scrollTop = vs.tscroll.top || 0; t.wrap.scrollLeft = vs.tscroll.left || 0; } }
}

function deltaSpan(pct, dir) {
  if (pct == null || !Number.isFinite(pct)) return null;
  let cls = 'neutral';
  if (pct !== 0 && dir !== 'neutral') cls = (dir === 'up') === (pct > 0) ? 'good' : 'bad';
  return h('span', { class: 'delta ' + cls }, fmt.pctChange(pct));
}
