import { h, enc, fmt, href, apiWindow, globalParams, navigate, colorChip, confBadge, badge, empty, thumb, stageBadge, liveDot, pick, setParams, safeColor } from '../util.js';
import { api, ApiError } from '../api.js';
import { createTable } from '../table.js';
import { metricColumns } from '../columns.js';
import { setWindowInfo } from '../controls.js';
import { breadcrumbs, filterBar, saveFilterToUrl, makeFilter, adsTable, familyAdsNode, unmatchedPanel } from '../components.js';
import { stageChart } from '../charts.js';
import { tagBadge } from './products.js';

const CONF_RANK = { low: 1, medium: 2, high: 3 };
const winInfo = (d) => d.window_info || (d.window && typeof d.window === 'object' ? d.window : null);
const validDir = (d) => (d === 'asc' || d === 'desc' ? d : null);

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
  return tagsView(ctx, slug);
}

function pagePath(slug) { return '/p/' + enc(slug); }

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

// ============================ Tags view ============================
async function tagsView(ctx, slug) {
  const { params: p, signal } = ctx;
  const G = globalParams(p);
  const path = pagePath(slug);
  const familiesMode = !!p.all || !!(p.cluster && p.icp && p.pain);
  const level = familiesMode ? 'families' : !p.cluster ? 'cluster' : !p.icp ? 'icp' : 'pain';
  const wp = apiWindow(p);
  const base = `/api/products/${enc(slug)}/`;
  const data = familiesMode
    ? await api(base + 'families', { cluster: p.cluster, icp: p.icp, pain: p.pain, ...wp }, { signal })
    : await api(base + 'rollup', { level, cluster: p.cluster, icp: p.icp, ...wp }, { signal });
  if (!ctx.alive()) return;
  setWindowInfo(winInfo(data));
  const product = data.product || (await getProduct(slug, signal));
  document.title = `${product.name} · Tag Explorer`;

  // breadcrumb
  const crumbs = [{ label: 'Products', href: href('/', G) }, { label: product.name, href: href(path, G) }];
  if (p.cluster) crumbs.push({ label: p.cluster, href: href(path, { ...G, cluster: p.cluster }) });
  if (p.icp) crumbs.push({ label: p.icp, href: href(path, { ...G, cluster: p.cluster, icp: p.icp }) });
  if (p.pain) crumbs.push({ label: p.pain, href: href(path, { ...G, cluster: p.cluster, icp: p.icp, pain: p.pain }) });
  if (p.all) crumbs.push({ label: 'All ad families' });
  if (crumbs.length === 2) crumbs[1].href = null; // current

  const sortKey = p.sort || null;
  const sortDir = validDir(p.dir);
  const onSort = (k, d) => { const u = new URL(location.href); u.searchParams.set('sort', k); u.searchParams.set('dir', d); navigate(u.pathname + u.search, { replace: true, render: false }); };

  let table, filterUi = null, title;
  const allAdsHref = href(path, { ...G, view: 'ads', cluster: p.cluster, icp: p.icp, pain: p.pain });
  const allAdsHeader = h('a', { href: allAdsHref, class: 'btnlink small', id: 'all-ads', title: 'Open the Meta ads table for everything in view' }, 'All ads in view');

  if (!familiesMode) {
    title = level === 'cluster' ? 'Market clusters' : level === 'icp' ? `ICPs in ${p.cluster}` : `Pain points in ${p.icp}`;
    const rowParams = (r) => ({
      cluster: level === 'cluster' ? r.label : (p.cluster || r.cluster),
      icp: level === 'icp' ? r.label : level === 'pain' ? (p.icp || r.icp) : undefined,
      pain: level === 'pain' ? r.label : undefined,
    });
    const childWord = level === 'cluster' ? 'ICP' : level === 'icp' ? 'pain point' : 'ad';
    const cols = [
      {
        key: 'label', label: level === 'cluster' ? 'Market cluster' : level === 'icp' ? 'ICP' : 'Pain point', sticky: true, cls: 'labelcol',
        sort: (r) => r.label,
        total: () => 'Total',
        cell: (r) => {
          const rp = rowParams(r);
          return h('div', { class: 'cell-label' },
            level === 'cluster' || r.color ? colorChip(r.color) : null,
            h('div', { class: 'cell-label-text' },
              h('div', null,
                r.line_code ? h('span', { class: 'linecode' }, r.line_code) : null,
                h('a', { href: href(path, { ...G, ...rp }), class: 'rowlink' }, r.label),
                r.child_count != null ? h('span', { class: 'muted small childcount' }, ` ${fmt.int(r.child_count)} ${childWord}${r.child_count === 1 ? '' : 's'}`) : null),
              r.trigger ? h('div', { class: 'sub', title: r.trigger }, r.trigger) : null));
        },
      },
      { key: '_ads', label: '', nosort: true, cls: 'actioncol', header: allAdsHeader, cell: (r) => h('a', { href: href(path, { ...G, view: 'ads', ...rowParams(r) }), class: 'btnlink small row-ads' }, 'Ads') },
      ...metricColumns(),
    ];
    table = createTable({
      columns: cols, rows: data.rows, total: data.total, sortKey, sortDir, onSort,
      rowKey: (r) => r.label,
      rowHref: (r) => href(path, { ...G, ...rowParams(r) }),
      emptyText: 'No rows at this level.',
      testid: 'level-table',
    });
  } else {
    title = p.all && !p.pain ? 'All ad families' : `Ad families in ${p.pain || p.icp || p.cluster || 'product'}`;
    const linemap = product.tag_set === 'linemap';
    const rkey = (r) => r.family_key + '|' + (r.ad_ids || []).join(',');
    const cols = [
      {
        key: 'display', label: 'Ad family', sticky: true, cls: 'labelcol', sort: (r) => r.display, total: () => 'Total',
        cell: (r) => {
          const ids = (r.concept_ids || []).slice(0, 2).join(', ') + ((r.concept_ids || []).length > 2 ? ` +${r.concept_ids.length - 2}` : '');
          const hf = r.primary_id != null ? href('/ad/' + enc(r.primary_id), G) : null;
          return h('div', { class: 'cell-ad' },
            thumb(r.thumb_asset_id, 34),
            h('div', { class: 'cell-ad-text' },
              hf ? h('a', { href: hf, class: 'rowlink adname', title: r.display }, r.display) : h('span', { class: 'adname' }, r.display),
              h('div', { class: 'sub' },
                ids,
                (r.n_rows || 0) > 1 ? ` · ${r.n_rows} rows` : '',
                r.counted_here === false ? ' · ' : '',
                r.counted_here === false ? h('span', { class: 'badge muted-badge', title: 'This family’s metrics are counted on its first tracker row, which sits in another group' }, 'counted elsewhere') : null)));
        },
      },
      {
        key: '_ads', label: '', nosort: true, cls: 'actioncol', header: allAdsHeader,
        cell: (r) => h('a', { href: href(path, { ...G, view: 'ads', family_key: r.family_key, cluster: p.cluster, icp: p.icp, pain: p.pain }), class: 'btnlink small row-ads' }, 'Ads'),
      },
      { key: 'ad_type', label: 'Type', sort: (r) => r.ad_type, cell: (r) => fmt.text(r.ad_type) },
      { key: 'status', label: 'Status', sort: (r) => r.status, cell: (r) => fmt.text(r.status) },
      { key: 'launch_date', label: 'Launch', sort: (r) => r.launch_date, cell: (r) => fmt.text(r.launch_date) },
      ...(linemap ? [{
        key: 'confidence', label: 'Conf.', title: 'Tag confidence (lowest among this family’s rows in the group)',
        sort: (r) => CONF_RANK[r.confidence] || null,
        cell: (r) => (r.confidence ? h('span', { title: r.method || null }, confBadge(r.confidence)) : ''),
      }] : []),
      ...metricColumns(),
    ];
    table = createTable({
      columns: cols, rows: data.rows, total: data.total, sortKey, sortDir, onSort,
      rowKey: rkey,
      rowHref: (r) => (r.primary_id != null ? href('/ad/' + enc(r.primary_id), G) : null),
      rowClass: (r) => (r.counted_here === false ? 'not-counted' : ''),
      expand: { render: (r) => familyAdsNode(slug, p, r.family_key, signal) },
      emptyText: 'No ad families here.',
      testid: 'level-table',
      onCount: (n, tot) => filterUi && filterUi.setCount(n, tot),
    });
    const apply = (st) => table.setFilter(makeFilter(st, (r) => r.m && r.m.spend, (r) => [r.display, ...(r.concept_ids || []), r.ad_type, r.status]));
    filterUi = filterBar({
      spend: p.spend === '1', q: p.q, placeholder: 'Search ad family, concept id…',
      onChange: (st) => { saveFilterToUrl(st); apply(st); },
    });
    if (p.spend === '1' || p.q) apply({ spend: p.spend === '1', q: p.q });
    else filterUi.setCount(data.rows.length, data.rows.length);
  }

  const toggleAll = p.all
    ? h('a', { class: 'btnlink', id: 'toggle-all', href: href(path, G) }, '← Back to hierarchy')
    : h('a', { class: 'btnlink', id: 'toggle-all', href: href(path, { ...G, all: 1 }) }, 'All ad families');

  ctx.mount(h('div', { class: 'page', 'data-level': level },
    pageHead(product, slug, p, 'tags'),
    h('div', { class: 'crumbrow' }, breadcrumbs(crumbs), h('span', { class: 'spacer' }), toggleAll),
    h('div', { class: 'tablehead' }, h('h2', { id: 'level-title' }, title), filterUi ? filterUi.el : null),
    table.el,
    unmatchedPanel(slug, p, data.unmatched || null, signal),
  ));
}

// ============================ Ads view (flat Meta ads) ============================
async function adsView(ctx, slug) {
  const { params: p, signal } = ctx;
  const G = globalParams(p);
  const path = pagePath(slug);
  const base = `/api/products/${enc(slug)}/`;
  const [d, product] = await Promise.all([
    api(base + 'ads', { ...pick(p, ['cluster', 'icp', 'pain', 'family_key', 'campaign_id']), ...apiWindow(p) }, { signal }),
    getProduct(slug, signal),
  ]);
  if (!ctx.alive()) return;
  setWindowInfo(winInfo(d));
  document.title = `Meta ads · ${product.name} · Tag Explorer`;

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

  const sortKey = p.sort || null;
  const sortDir = validDir(p.dir);
  let filterUi;
  const t = adsTable(p, d, {
    sortKey, sortDir, onCount: (n, tot) => filterUi && filterUi.setCount(n, tot),
    onSort: (k, dr) => { const u = new URL(location.href); u.searchParams.set('sort', k); u.searchParams.set('dir', dr); navigate(u.pathname + u.search, { replace: true, render: false }); },
    showFamily: !p.family_key,
    campaignHref: (r) => href(path, { ...G, view: 'ads', campaign_id: r.campaign_id }),
  });
  const apply = (st) => t.setFilter(makeFilter(st, (r) => r.spend, (r) => [r.ad_name, r.campaign_name, r.adset_name, r.display, r.status]));
  filterUi = filterBar({
    spend: p.spend === '1', q: p.q, placeholder: 'Search ad, campaign, ad set…',
    onChange: (st) => { saveFilterToUrl(st); apply(st); },
  });
  if (p.spend === '1' || p.q) apply({ spend: p.spend === '1', q: p.q });
  filterUi.setCount(d.rows.length, d.rows.length);

  ctx.mount(h('div', { class: 'page', 'data-level': 'ads' },
    pageHead(product, slug, p, p.campaign_id ? 'campaigns' : 'tags'),
    h('div', { class: 'crumbrow' }, breadcrumbs(crumbs)),
    h('div', { class: 'tablehead' }, h('h2', { id: 'level-title' }, 'Meta ads'), filterUi.el),
    t.el,
  ));
}

// ============================ Campaigns view ============================
async function campaignsView(ctx, slug) {
  const { params: p, signal } = ctx;
  const G = globalParams(p);
  const path = pagePath(slug);
  const base = `/api/products/${enc(slug)}/`;
  const wp = apiWindow(p);
  const dailyParams = { ...wp };
  delete dailyParams.stage;
  if (!dailyParams.window && !dailyParams.from) dailyParams.days = 90;
  const [d, daily, product] = await Promise.all([
    api(base + 'campaigns', wp, { signal }),
    api(base + 'stage-daily', dailyParams, { signal }),
    getProduct(slug, signal),
  ]);
  if (!ctx.alive()) return;
  setWindowInfo(winInfo(d));
  document.title = `Campaigns · ${product.name} · Tag Explorer`;

  // stage cards
  const totalSpend = (d.by_stage || []).reduce((a, s) => a + (s.spend || 0), 0);
  const curStage = p.stage || 'all';
  const cards = (d.by_stage || []).map((s) => {
    const clickable = s.stage === 'testing' || s.stage === 'scaling';
    const card = h('div', {
      class: 'stagecard stage-' + s.stage + (curStage === s.stage ? ' selected' : '') + (clickable ? ' clickable' : ''),
      'data-stage': s.stage, tabindex: clickable ? '0' : null, role: clickable ? 'button' : null,
      title: clickable ? 'Click to filter the page to this stage' : null,
    },
      h('div', { class: 'stagecard-head' }, stageBadge(s.stage), h('span', { class: 'muted small' }, `${fmt.int(s.campaigns)} campaigns · ${fmt.int(s.ads)} ads`)),
      h('div', { class: 'stagecard-main' }, h('span', { class: 'big' }, fmt.money(s.spend)), h('span', { class: 'muted' }, totalSpend > 0 ? ` · ${fmt.pct((s.spend || 0) / totalSpend)} of spend` : '')),
      h('div', { class: 'stagecard-sub' },
        h('span', null, 'ROAS ', h('b', null, fmt.roas(s.roas))),
        h('span', null, 'CPA ', h('b', null, fmt.money(s.cpa))),
        h('span', null, 'Purchases ', h('b', null, fmt.int(s.purchases))),
        h('span', null, 'Spend 7d ', h('b', null, fmt.money(s.spend_7d)))));
    if (clickable) {
      const go = () => setParams({ stage: curStage === s.stage ? 'all' : s.stage });
      card.addEventListener('click', go);
      card.addEventListener('keydown', (e) => { if (e.key === 'Enter') go(); });
    }
    return card;
  });

  // chart
  const canvas = h('canvas', { id: 'stage-chart', role: 'img', 'aria-label': 'Daily spend by stage' });
  const chartBox = h('div', { class: 'chartbox' }, canvas);

  // campaign table
  const flat = metricColumns({ getM: (r) => r, getTotalM: (t) => t, keys: ['spend', 'purchases', 'revenue', 'roas', 'cpa', 'spend_7d'] });
  const cols = [
    { key: 'name', label: 'Campaign', sticky: true, cls: 'labelcol', sort: (r) => r.name, total: () => 'Total', cell: (r) => h('a', { href: href(path, { ...G, view: 'ads', campaign_id: r.campaign_id }), class: 'rowlink', title: r.name }, r.name) },
    { key: 'account', label: 'Account', sort: (r) => r.account, cell: (r) => fmt.text(r.account) },
    { key: 'stage', label: 'Stage', sort: (r) => r.stage, cell: (r) => stageBadge(r.stage) },
    { key: 'ads', label: 'Ads', num: true, sort: (r) => r.ads, cell: (r) => fmt.int(r.ads), total: (t) => (t && t.ads != null ? fmt.int(t.ads) : '') },
    { key: 'families', label: 'Families', num: true, sort: (r) => r.families, cell: (r) => fmt.int(r.families), total: (t) => (t && t.families != null ? fmt.int(t.families) : '') },
    ...flat,
    { key: 'live', label: 'Live', sort: (r) => (r.live ? 1 : 0), cell: (r) => liveDot(r.live), total: () => '', cls: 'center' },
    { key: 'last_day', label: 'Last day', sort: (r) => r.last_day, cell: (r) => fmt.text(r.last_day), total: () => '' },
  ];
  let filterUi;
  const sortKey = p.sort || null;
  const t = createTable({
    columns: cols, rows: d.rows, total: d.total, sortKey, sortDir: validDir(p.dir),
    onSort: (k, dr) => { const u = new URL(location.href); u.searchParams.set('sort', k); u.searchParams.set('dir', dr); navigate(u.pathname + u.search, { replace: true, render: false }); },
    rowKey: (r) => r.campaign_id,
    rowHref: (r) => href(path, { ...G, view: 'ads', campaign_id: r.campaign_id }),
    emptyText: 'No campaigns in this window.',
    testid: 'campaign-table',
    onCount: (n, tot) => filterUi && filterUi.setCount(n, tot),
  });
  const apply = (st) => t.setFilter(makeFilter(st, (r) => r.spend, (r) => [r.name, r.account, r.stage, r.campaign_id]));
  filterUi = filterBar({ spend: p.spend === '1', q: p.q, placeholder: 'Search campaign…', onChange: (st) => { saveFilterToUrl(st); apply(st); } });
  if (p.spend === '1' || p.q) apply({ spend: p.spend === '1', q: p.q });
  else filterUi.setCount(d.rows.length, d.rows.length);

  ctx.mount(h('div', { class: 'page', 'data-level': 'campaigns' },
    pageHead(product, slug, p, 'campaigns'),
    h('div', { class: 'crumbrow' }, breadcrumbs([{ label: 'Products', href: href('/', G) }, { label: product.name, href: href(path, G) }, { label: 'Campaigns' }])),
    h('div', { class: 'stagecards' }, cards),
    h('div', { class: 'section' }, h('div', { class: 'section-head' }, h('h2', 'Daily spend by stage'), h('span', { class: 'muted small' }, daily.length ? `${fmt.day(daily[0].day)} – ${fmt.day(daily[daily.length - 1].day)}` : '')), chartBox),
    h('div', { class: 'tablehead' }, h('h2', { id: 'level-title' }, 'Campaigns'), filterUi.el),
    t.el,
    unmatchedPanel(slug, p, null, signal),
  ));
  stageChart(canvas, Array.isArray(daily) ? daily : (daily.rows || []));
}
