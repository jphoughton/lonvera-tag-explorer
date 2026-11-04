// Dropdown tree table for the product page: cluster > ICP > pain point > creative > ad.
// A caret (and, in "Drops down" mode, a click on the row) expands children in place; "Open ›" navigates into that level.
// Children load lazily from /tree (tag levels) and /ads (Meta ads under a family).
import { h, enc, fmt, href, apiWindow, globalParams, sparkParams, colorChip, thumb, confBadge, stageBadge, navigate, cmpView, onCmpRefresh, isNum, getRowMode, setRowMode, debounce, setViewStateProvider } from './util.js';
import { api, AuthError } from './api.js';
import { createTable, compare } from './table.js';
import { columnsChips, creativeMeta, coverageBadge } from './components.js';
import { adActions } from './preview.js';
import { metricColumns, trendColumn, pinColumn } from './columns.js';

const ROOT = '__root__';
const LEVEL_NAME = ['cluster', 'icp', 'pain', 'family'];
const CHILD_WORD = ['ICP', 'pain point', 'creative', 'ad'];

function scaleM(m, parentM) {
  // child response total == parent's total, so child's table share = parent share x child share
  const out = { ...m };
  if (parentM && isNum(parentM.spend_share) && isNum(m.spend_share)) out.spend_share = parentM.spend_share * m.spend_share;
  else if (parentM) out.spend_share = null;
  if (m.cmp) {
    out.cmp = { ...m.cmp };
    if (parentM && parentM.cmp && isNum(parentM.cmp.spend_share) && isNum(m.cmp.spend_share)) out.cmp.spend_share = parentM.cmp.spend_share * m.cmp.spend_share;
    else if (parentM) out.cmp.spend_share = null;
    out.delta = { ...(m.delta || {}) };
    out.delta.spend_share_pp = isNum(out.spend_share) && isNum(out.cmp.spend_share) ? (out.spend_share - out.cmp.spend_share) * 100 : null;
  }
  return out;
}

// Meta ad row (/ads) -> tree node with an `m` like every other row
function adNode(r, fam, i) {
  const share = fam && fam.m && isNum(fam.m.spend_share) && isNum(r.spend_share) ? fam.m.spend_share * r.spend_share : null;
  // copy every metric field of the /ads row (traffic metrics, per-day values, ...), then override the tree-specific ones
  const m = {
    ...r,
    unique_ads: null, live_now: r.ad_id == null ? null : (r.live ? 1 : 0),
    spend_share: share, cpa_7d: null, active_ads: r.spend > 0 ? 1 : 0,
    stage: { testing: { spend: r.stage ? (r.stage === 'testing' ? r.spend : 0) : null }, scaling: { spend: r.stage ? (r.stage === 'scaling' ? r.spend : 0) : null } },
    testing_pct: null,
  };
  delete m.cmp; delete m.delta; delete m.spark;
  if (r.cmp) {
    m.cmp = { ...r.cmp };
    const famCmpShare = fam && fam.m && fam.m.cmp ? fam.m.cmp.spend_share : null;
    m.cmp.spend_share = isNum(famCmpShare) && isNum(r.cmp.spend_share) ? famCmpShare * r.cmp.spend_share : null;
    m.delta = { ...(r.delta || {}) };
    m.delta.spend_share_pp = isNum(m.spend_share) && isNum(m.cmp.spend_share) ? (m.spend_share - m.cmp.spend_share) * 100 : null;
  }
  return {
    key: `ad:${fam.key}:${r.ad_id != null ? r.ad_id : 'rem' + i}`, parent_key: fam.key, level: 4,
    label: r.ad_name, ad: r, primary_id: r.primary_id, thumb_asset_id: fam.thumb_asset_id,
    has_children: false, m, spark: r.spark,
  };
}

export function createTree({ slug, p, signal, rootLevel, filters, first, restore, pins, shareLabel = '% of spend', coverage = null }) {
  const G = globalParams(p);
  const path = '/p/' + enc(slug);
  const nodes = new Map();
  const kids = new Map();      // parentKey -> [keys]
  const loaded = new Set();    // parentKeys whose children are in `kids`
  const loading = new Set();
  let total = null;
  const expanded = new Set(restore && restore.exp ? restore.exp : []);
  let sort = { key: p.sort || null, dir: p.dir === 'asc' || p.dir === 'desc' ? p.dir : null, by: p.by === 'delta' ? 'delta' : 'value' };
  let q = (p.q || '').trim();
  let spendOnly = p.spend === '1';
  let rowMode = getRowMode();
  let table = null;
  const maxTagDepth = 3 - rootLevel;
  let fullDepth = 0; // how many levels below the root are loaded everywhere (via /tree depth=N)

  const windowParams = apiWindow(p);

  // ---------- data ----------
  function ingest(rows, { parent = null, rootOfResponse = rootLevel, depth = 0 } = {}) {
    const deepest = rootOfResponse + depth;
    const parentM = parent ? parent.m : null;
    const byParent = new Map();
    for (const r of rows) {
      const node = { ...r, m: parent ? scaleM(r.m, parentM) : r.m };
      nodes.set(node.key, node);
      const pk = node.level === rootLevel ? ROOT : (parent && node.level === rootOfResponse ? parent.key : node.parent_key);
      if (!byParent.has(pk)) byParent.set(pk, []);
      byParent.get(pk).push(node.key);
      if (node.level < deepest && node.level < 3) loaded.add(node.key);
    }
    for (const [pk, list] of byParent) { kids.set(pk, list); loaded.add(pk); }
    if (parent) loaded.add(parent.key);
    if (!parent && !kids.has(ROOT)) { kids.set(ROOT, []); loaded.add(ROOT); }
  }

  async function fetchTree(rootL, extra, depth) {
    const d = await api(`/api/products/${enc(slug)}/tree`, { root: LEVEL_NAME[rootL], ...extra, depth, ...windowParams, ...sparkParams(p) }, { signal });
    return d;
  }

  async function loadChildren(node) {
    if (loaded.has(node.key) || loading.has(node.key)) return;
    loading.add(node.key);
    rebuild();
    try {
      if (node.level === 3) {
        const d = await api(`/api/products/${enc(slug)}/ads`, { family_key: node.family_key, cluster: node.cluster, icp: node.icp, pain: node.pain, ...windowParams, ...sparkParams(p) }, { signal });
        const rows = d.rows.map((r, i) => adNode(r, node, i));
        for (const n of rows) nodes.set(n.key, n);
        kids.set(node.key, rows.map((n) => n.key));
      } else {
        const childLevel = node.level + 1;
        const f = { cluster: node.cluster, icp: node.level >= 1 ? node.icp : undefined, pain: node.level >= 2 ? node.pain : undefined };
        const d = await fetchTree(childLevel, f, 0);
        ingest(d.rows, { parent: node, rootOfResponse: childLevel, depth: 0 });
      }
      loaded.add(node.key);
    } catch (e) {
      if (!(e instanceof AuthError) && !(e && e.name === 'AbortError')) { node.error = e.message; loaded.add(node.key); }
    } finally {
      loading.delete(node.key);
      rebuild();
    }
  }

  async function loadDepth(depth, mySeq) {
    const d = await fetchTree(rootLevel, filters, depth);
    if (mySeq != null && mySeq !== expandSeq) return null; // a later click superseded this one: drop the response
    // keep already-loaded Meta ads
    const keep = [...kids].filter(([k]) => nodes.get(k) && nodes.get(k).level === 3).map(([k, list]) => [k, list, list.map((x) => nodes.get(x))]);
    nodes.clear(); kids.clear(); loaded.clear();
    total = d.total;
    ingest(d.rows, { depth });
    for (const [k, list, adNodes] of keep) {
      if (!nodes.has(k)) continue;
      adNodes.forEach((a) => a && nodes.set(a.key, a));
      kids.set(k, list); loaded.add(k);
    }
    fullDepth = Math.max(fullDepth, depth);
    return d;
  }

  // ---------- ordering ----------
  const cols = [];
  function labelSort(n) { return n.label; }
  function sortedKids(parentKey) {
    const list = (kids.get(parentKey) || []).map((k) => nodes.get(k)).filter(Boolean);
    const col = sort.key ? cols.find((c) => c.key === sort.key) : null;
    if (!col || col.nosort) return list;
    const byDelta = sort.by === 'delta' && cmpView.on && col.deltaSort;
    const get = sort.key === 'label' ? labelSort : (byDelta ? col.deltaSort : col.sort);
    const dir = sort.dir || 'desc';
    const rem = list.filter((n) => n.ad && n.ad.ad_id == null);
    const rest = list.filter((n) => !(n.ad && n.ad.ad_id == null));
    return rest
      .map((n, i) => ({ n, i, v: get(n) }))
      .sort((a, b) => compare(a.v, b.v, dir) || a.i - b.i)
      .map((x) => x.n)
      .concat(rem);
  }

  // ---------- filtering ----------
  const needle = () => q.toLowerCase();
  function selfMatch(n) {
    if (spendOnly && !(n.m && n.m.spend > 0)) return false;
    const nd = needle();
    if (!nd) return true;
    return [n.label, n.line_code, n.ad_type, n.status, n.ad && n.ad.campaign_name, n.ad && n.ad.adset_name].some((f) => f != null && String(f).toLowerCase().includes(nd));
  }
  const matchCache = new Map();
  function subtreeMatch(n) {
    if (matchCache.has(n.key)) return matchCache.get(n.key);
    let ok = selfMatch(n);
    // for search / spend filters keep a node when any loaded descendant matches
    if (!ok && (q || spendOnly)) {
      for (const k of kids.get(n.key) || []) { const c = nodes.get(k); if (c && subtreeMatch(c)) { ok = true; break; } }
    }
    matchCache.set(n.key, ok);
    return ok;
  }

  // ---------- flatten to visible rows ----------
  function flatten() {
    matchCache.clear();
    const out = [];
    const filtering = !!(q || spendOnly);
    const walk = (parentKey, depth) => {
      for (const n of sortedKids(parentKey)) {
        if (filtering && !subtreeMatch(n)) continue;
        out.push(n);
        const open = expanded.has(n.key) || (q && hasMatchBelow(n));
        if (open && (n.has_children !== false || n.level === 3)) {
          if (loading.has(n.key)) out.push({ key: '~loading:' + n.key, pseudo: 'Loading…', level: n.level + 1 });
          else if (loaded.has(n.key)) {
            if (n.error) out.push({ key: '~err:' + n.key, pseudo: 'Failed to load: ' + n.error, level: n.level + 1 });
            else if (!(kids.get(n.key) || []).length) out.push({ key: '~empty:' + n.key, pseudo: n.level === 3 ? 'No ads in this window.' : 'No children.', level: n.level + 1 });
            else walk(n.key, depth + 1);
          }
        }
      }
    };
    walk(ROOT, 0);
    return out;
  }
  function hasMatchBelow(n) {
    for (const k of kids.get(n.key) || []) { const c = nodes.get(k); if (c && subtreeMatch(c)) return true; }
    return false;
  }

  function rebuild() {
    if (!table) return;
    const rows = flatten();
    table.setRows(rows, total);
    updateToolbar(rows);
  }

  // ---------- expand controls ----------
  function canExpand(n) { return n.level < 4 && (n.has_children !== false || n.level === 3); }
  async function toggle(n) {
    if (expanded.has(n.key)) { expanded.delete(n.key); rebuild(); return; }
    expanded.add(n.key);
    rebuild();
    if (!loaded.has(n.key)) await loadChildren(n);
  }
  // sequence guard: only the latest Expand / Collapse click may change the tree when its response arrives
  let expandSeq = 0;
  function collapseAll() { expandSeq++; setBusy(false); expanded.clear(); rebuild(); }
  async function expandTo(k) {
    if (k <= 0) { collapseAll(); return; }
    const mySeq = ++expandSeq;
    setBusy(true);
    try {
      if (rootLevel === 3) {
        // families -> Meta ads: one /ads call for the whole filter, grouped by family
        const d = await api(`/api/products/${enc(slug)}/ads`, { cluster: filters.cluster, icp: filters.icp, pain: filters.pain, ...windowParams, ...sparkParams(p) }, { signal });
        if (mySeq !== expandSeq) return;
        const by = new Map();
        d.rows.forEach((r, i) => { if (!by.has(r.family_key)) by.set(r.family_key, []); by.get(r.family_key).push([r, i]); });
        for (const n of nodes.values()) {
          if (n.level !== 3) continue;
          const rows = (by.get(n.family_key) || []).map(([r, i]) => adNode(r, n, i));
          for (const a of rows) nodes.set(a.key, a);
          kids.set(n.key, rows.map((a) => a.key)); loaded.add(n.key);
          expanded.add(n.key);
        }
      } else {
        if (fullDepth < k && (await loadDepth(k, mySeq)) === null) return;
        if (mySeq !== expandSeq) return;
        expanded.clear();
        for (const n of nodes.values()) if (n.level - rootLevel < k && n.level < 3) expanded.add(n.key);
      }
    } catch (e) {
      if (!(e instanceof AuthError) && !(e && e.name === 'AbortError')) console.warn('expand failed', e);
    } finally {
      if (mySeq === expandSeq) { setBusy(false); rebuild(); }
    }
  }
  function expandAll() {
    // tag levels down to ad families; Meta ads of a family load on demand (or via a pain point page)
    return expandTo(rootLevel === 3 ? 1 : maxTagDepth);
  }

  // ---------- columns ----------
  const hrefFor = (n) => {
    if (n.level === 0) return href(path, { ...G, cluster: n.label });
    if (n.level === 1) return href(path, { ...G, cluster: n.cluster || filters.cluster, icp: n.label });
    if (n.level === 2) return href(path, { ...G, cluster: n.cluster || filters.cluster, icp: n.icp || filters.icp, pain: n.label });
    return n.primary_id != null ? href('/ad/' + enc(n.primary_id), G) : null;
  };
  const adsHref = (n) => {
    if (n.level === 0) return href(path, { ...G, view: 'ads', cluster: n.label });
    if (n.level === 1) return href(path, { ...G, view: 'ads', cluster: n.cluster || filters.cluster, icp: n.label });
    if (n.level === 2) return href(path, { ...G, view: 'ads', cluster: n.cluster || filters.cluster, icp: n.icp || filters.icp, pain: n.label });
    return href(path, { ...G, view: 'ads', family_key: n.family_key || (n.ad && n.ad.family_key), cluster: n.cluster, icp: n.icp, pain: n.pain });
  };

  function labelCell(n) {
    if (n.pseudo) return h('div', { class: 'cell-label pseudo', style: { paddingLeft: ((n.level - rootLevel) * 20 + 30) + 'px' } }, h('span', { class: 'muted' }, n.pseudo));
    const indent = (n.level - rootLevel) * 20;
    const open = expanded.has(n.key) || (q && hasMatchBelow(n));
    const lead = canExpand(n)
      ? h('button', { type: 'button', class: 'caret', 'aria-expanded': String(!!open), 'aria-label': (open ? 'Collapse ' : 'Expand ') + n.label, title: open ? 'Collapse' : 'Expand', onclick: (e) => { e.stopPropagation(); toggle(n); } }, open ? '▾' : '▸')
      : h('span', { class: 'caret ph' });
    const openHref = hrefFor(n);
    const openLink = openHref ? h('a', { href: openHref, class: 'opn row-open', title: 'Open as its own page' }, 'Open ›') : null;
    let body;
    if (n.level === 4) {
      const a = n.ad;
      body = h('div', { class: 'cell-ad' },
        a.ad_id == null ? null : thumb(n.thumb_asset_id, 26, { name: n.label, primaryId: n.primary_id, metaAdId: a.ad_id, tag: a.tag, links: a.links }),
        h('div', { class: 'cell-ad-text' },
          h('div', { class: 'nm' }, h('span', { class: 'rowname' + (a.ad_id == null ? ' muted ital' : ''), title: n.label }, n.label)),
          h('div', { class: 'meta' }, a.stage ? stageBadge(a.stage) : null, openLink,
            a.ad_id == null ? null : [a.campaign_name, a.adset_name, a.status].filter(Boolean).join(' · '), a.ad_id == null ? null : adActions(a))));
    } else if (n.level === 3) {
      body = h('div', { class: 'cell-ad' },
        thumb(n.thumb_asset_id, 30, { name: n.label, primaryId: n.primary_id }),
        h('div', { class: 'cell-ad-text' },
          h('div', { class: 'nm' }, h('span', { class: 'rowname', title: n.label }, n.label)),
          h('div', { class: 'meta' }, n.confidence ? confBadge(n.confidence) : null, openLink,
            creativeMeta({ adType: n.ad_type, status: n.status, launch: n.launch_date, adsCount: n.ads_count, adsLive: n.ads_live }),
            n.counted_here === false ? h('span', { class: 'badge muted-badge', title: 'This creative’s metrics are counted on its first tracker row, which sits in another group' }, 'counted elsewhere') : null)));
    } else {
      body = h('div', { class: 'cell-label-text' },
        h('div', { class: 'nm' },
          n.line_code ? h('span', { class: 'linecode' }, n.line_code) : null,
          h('span', { class: 'lvlname' }, n.label),
          n.child_count != null ? h('span', { class: 'muted small childcount' }, ` ${fmt.int(n.child_count)} ${CHILD_WORD[n.level]}${n.child_count === 1 ? '' : 's'}`) : null,
          coverageBadge(coverage && n.level >= 1 ? coverage[n.key] : null),
          openLink),
        n.trigger ? h('div', { class: 'sub', title: n.trigger }, n.trigger) : null);
    }
    return h('div', { class: 'cell-label tl' + n.level, style: { paddingLeft: indent + 'px' } }, lead, n.level === 0 || n.color ? colorChip(n.color) : null, body);
  }

  const labelCol = {
    key: 'label', label: first, sticky: true, cls: 'labelcol', sort: labelSort, total: () => 'Total', cell: labelCell,
  };
  const allAdsLink = h('a', { href: href(path, { ...G, view: 'ads', ...filters }), class: 'btnlink small', id: 'all-ads', title: 'Open the ads table for everything in view' }, 'All creatives in view');
  const actionCol = {
    key: '_ads', label: '', nosort: true, cls: 'actioncol', header: allAdsLink,
    cell: (n) => (n.pseudo ? '' : h('a', { href: adsHref(n), class: 'btnlink small row-ads' }, 'Ads')),
  };
  const metricCols = metricColumns({ getM: (n) => n.m, getTotalM: (t) => t && t.m, shareLabel });
  const pinScope = (n) => {
    const sc = {};
    if (n.level >= 0) sc.cluster = n.level === 0 ? n.label : n.cluster;
    if (n.level >= 1) sc.icp = n.level === 1 ? n.label : n.icp;
    if (n.level >= 2) sc.pain = n.level === 2 ? n.label : n.pain;
    if (n.level >= 3) sc.family_key = n.family_key;
    return sc;
  };
  const extraCols = [];
  if (pins) extraCols.push(pinColumn({ pins, canPin: (n) => !n.pseudo && n.level < 4, keyOf: (n) => n.key, labelOf: (n) => n.label, scopeOf: pinScope }));
  cols.push(labelCol, ...extraCols, actionCol, trendColumn({ getSpark: (n) => n.spark }), ...metricCols);
  // metric columns skip pseudo rows (they carry no `m`)

  // ---------- toolbar ----------
  const depthBtns = [];
  const maxDepthBtn = rootLevel === 3 ? 1 : maxTagDepth;
  for (let k = 0; k <= maxDepthBtn; k++) {
    const b = h('button', { type: 'button', 'data-depth': String(k), class: 'seg', onclick: () => expandTo(k) }, k === 0 ? 'Top level' : `+${k} level${k > 1 ? 's' : ''}`);
    depthBtns.push(b);
  }
  const modeBtns = [['expand', 'Drops down'], ['open', 'Opens']].map(([v, t]) => {
    const b = h('button', { type: 'button', class: 'seg', 'data-rm': v, 'aria-pressed': String(rowMode === v) }, t);
    b.addEventListener('click', () => { rowMode = v; setRowMode(v); modeBtns.forEach((x) => x.setAttribute('aria-pressed', String(x.dataset.rm === v))); });
    return b;
  });
  const cb = h('input', { type: 'checkbox', id: 'flt-spend', checked: spendOnly });
  const search = h('input', { type: 'search', id: 'flt-q', value: q, placeholder: 'Search…', 'aria-label': 'Search' });
  const count = h('span', { class: 'muted', id: 'flt-count' });
  const busy = h('span', { class: 'muted', id: 'tree-busy' });
  function setBusy(on) { busy.textContent = on ? 'Loading…' : ''; }
  function saveFilter() {
    const u = new URL(location.href);
    if (spendOnly) u.searchParams.set('spend', '1'); else u.searchParams.delete('spend');
    if (q) u.searchParams.set('q', q); else u.searchParams.delete('q');
    navigate(u.pathname + u.search, { replace: true, render: false });
  }
  cb.addEventListener('change', () => { spendOnly = cb.checked; saveFilter(); rebuild(); });
  search.addEventListener('input', debounce(async () => {
    q = search.value.trim();
    saveFilter();
    if (q && fullDepth < maxTagDepth && maxTagDepth > 0) { setBusy(true); try { await loadDepth(maxTagDepth); } catch { /* keep partial */ } setBusy(false); }
    rebuild();
  }, 150));
  const expandAllBtn = h('button', { type: 'button', class: 'btn', id: 'expand-all', onclick: expandAll }, 'Expand all');
  const collapseBtn = h('button', { type: 'button', class: 'btn', id: 'collapse-all', onclick: collapseAll }, 'Collapse all');
  const toolbar = h('div', { class: 'tools', 'data-testid': 'tree-toolbar' },
    expandAllBtn, collapseBtn,
    h('span', { class: 'ctl-name' }, 'Expand to'), h('span', { class: 'segs', id: 'expand-to' }, depthBtns),
    busy,
    h('span', { class: 'spacer' }),
    h('span', { class: 'ctl-name' }, 'Clicking a row'), h('span', { class: 'segs', id: 'row-mode' }, modeBtns),
    columnsChips(),
    h('label', { class: 'check' }, cb, ' With spend only'), search, count);
  function updateToolbar(rows) {
    const n = rows.filter((r) => !r.pseudo).length;
    count.textContent = `${fmt.int(n)} rows shown`;
  }

  // ---------- row click ----------
  function onRowClick(n, e, ctl) {
    if (n.pseudo) return;
    const open = hrefFor(n);
    if (rowMode === 'expand' && canExpand(n)) { toggle(n); return; }
    if (open) navigate(open);
  }

  // ---------- table ----------
  table = createTable({
    columns: cols, rows: [], total: null,
    sortKey: sort.key, sortDir: sort.dir, sortBy: sort.by, externalSort: true, pageSize: 3000,
    onSort: (k, d, by) => {
      sort = { key: k, dir: d, by };
      const u = new URL(location.href);
      u.searchParams.set('sort', k); u.searchParams.set('dir', d);
      if (by === 'delta') u.searchParams.set('by', 'delta'); else u.searchParams.delete('by');
      navigate(u.pathname + u.search, { replace: true, render: false });
      table.setRows(flatten());
    },
    rowKey: (n) => n.key,
    rowLabel: (n) => (n.pseudo ? null : n.label),
    rowHref: (n) => (n.pseudo ? null : hrefFor(n)),
    rowClickable: (n) => !n.pseudo,
    onRowClick,
    rowClass: (n) => (n.pseudo ? 'pseudo' : 'lv' + n.level + (n.counted_here === false ? ' not-counted' : '')),
    emptyText: 'Nothing here.',
    testid: 'level-table',
  });
  onCmpRefresh(() => table.refresh());

  // view state for Back: expanded set, depth needed to rebuild it, table scroll
  setViewStateProvider(() => {
    let deepest = 0;
    for (const k of expanded) { const n = nodes.get(k); if (n && n.level < 4) deepest = Math.max(deepest, n.level - rootLevel + 1); }
    return { exp: [...expanded], pins: pins ? pins.list() : [], depth: Math.min(deepest, maxTagDepth), tscroll: { top: table.wrap.scrollTop, left: table.wrap.scrollLeft } };
  });

  // ---------- init ----------
  return {
    el: h('div', { class: 'treebox' }, toolbar, table.el),
    table,
    expandedKeys: () => [...expanded],
    afterMount(vs) {
      if (!vs) return;
      window.scrollTo(0, vs.scrollY || 0);
      if (vs.tscroll) { table.wrap.scrollTop = vs.tscroll.top || 0; table.wrap.scrollLeft = vs.tscroll.left || 0; }
    },
    async init(initialRows, initialTotal) {
      total = initialTotal;
      ingest(initialRows, { depth: 0 });
      table.setRows(flatten(), total);
      updateToolbar(flatten());
      const needDepth = restore && restore.depth ? Math.min(restore.depth, maxTagDepth) : 0;
      if (needDepth > 0 && maxTagDepth > 0) {
        try { await loadDepth(needDepth); } catch { /* keep the root rows */ }
      }
      // restore expanded Meta ads
      const famOpen = [...expanded].map((k) => nodes.get(k)).filter((n) => n && n.level === 3 && !loaded.has(n.key));
      rebuild();
      await Promise.all(famOpen.map((n) => loadChildren(n)));
      if (q) { fullDepth = 0; search.dispatchEvent(new Event('input')); }
    },
  };
}
