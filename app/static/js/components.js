// Shared page components: breadcrumbs, filter bar, Meta-ads table, unmatched panel.
import { h, enc, fmt, isNum, href, apiWindow, globalParams, debounce, navigate, stageBadge, liveDot, thumb, empty, colPref, setColPref, refreshCmp } from './util.js';
import { api, AuthError } from './api.js';
import { createTable } from './table.js';
import { metricColumns, FLAT_KEYS, fixTotal } from './columns.js';

export function breadcrumbs(items) {
  const last = items.length - 1;
  return h('nav', { class: 'crumbs', 'aria-label': 'Breadcrumb' },
    h('ol', items.map((it, i) =>
      h('li', { class: i === last ? 'current' : '', 'aria-current': i === last ? 'page' : null },
        it.href && i !== last ? h('a', { href: it.href }, it.chip || null, it.label) : h('span', it.chip || null, it.label)))));
}

// "Columns" chip group: Traffic (Impressions, Link clicks, CTR, CPC; default on) and Advanced (CPM, Conv rate; default off)
export function columnsChips() {
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
  return h('div', { class: 'ctl colchips' },
    h('span', { class: 'ctl-name' }, 'Columns'),
    h('span', { class: 'chips', role: 'group', 'aria-label': 'Columns' }, mk('traffic', 'Traffic', 'Impressions, Link clicks, CTR, CPC'), mk('advanced', 'Advanced', 'CPM, Conv rate')));
}

export function section(title, body, { id, aside } = {}) {
  return h('section', { class: 'section', id: id || null },
    h('div', { class: 'section-head' }, h('h2', title), aside || null),
    body);
}

// sort state in the URL (replaceState: sorting never adds history entries)
export function saveSortToUrl(k, d, by) {
  const u = new URL(location.href);
  u.searchParams.set('sort', k); u.searchParams.set('dir', d);
  if (by === 'delta') u.searchParams.set('by', 'delta'); else u.searchParams.delete('by');
  navigate(u.pathname + u.search, { replace: true, render: false });
}
export const sortFromUrl = (p) => ({ sortKey: p.sort || null, sortDir: p.dir === 'asc' || p.dir === 'desc' ? p.dir : null, sortBy: p.by === 'delta' ? 'delta' : 'value' });

// "With spend only" + search. onChange({spend,q}) is called on every change.
export function filterBar({ spend, q, placeholder = 'Search…', onChange }) {
  const cb = h('input', { type: 'checkbox', id: 'flt-spend', checked: !!spend });
  const input = h('input', { type: 'search', id: 'flt-q', value: q || '', placeholder, 'aria-label': 'Search' });
  const count = h('span', { class: 'muted', id: 'flt-count' });
  const change = () => onChange({ spend: cb.checked, q: input.value.trim() });
  cb.addEventListener('change', change);
  input.addEventListener('input', debounce(change, 120));
  return {
    el: h('div', { class: 'filterbar' }, h('label', { class: 'check' }, cb, ' With spend only'), input, count),
    setCount(n, total) { count.textContent = n === total ? `${fmt.int(total)} rows` : `${fmt.int(n)} of ${fmt.int(total)} rows`; },
  };
}
// persist the filter in the URL without re-rendering
export function saveFilterToUrl({ spend, q }) {
  const u = new URL(location.href);
  if (spend) u.searchParams.set('spend', '1'); else u.searchParams.delete('spend');
  if (q) u.searchParams.set('q', q); else u.searchParams.delete('q');
  navigate(u.pathname + u.search, { replace: true, render: false });
}
export function makeFilter({ spend, q }, spendOf, fieldsOf) {
  const needle = (q || '').toLowerCase();
  return (r) => {
    if (spend && !(spendOf(r) > 0)) return false;
    if (!needle) return true;
    return fieldsOf(r).some((f) => f != null && String(f).toLowerCase().includes(needle));
  };
}

// ---------- Meta ads table (ad_id grain) ----------
export function adsColumns(p, { showFamily = true, campaignHref = null, linkAd = true, shareLabel = '% of spend' } = {}) {
  const G = globalParams(p);
  const adHref = (r) => (linkAd && r.primary_id != null ? href('/ad/' + enc(r.primary_id), G) : null);
  const tagPath = (r) => (r.tag ? [r.tag.cluster, r.tag.icp, r.tag.pain].filter(Boolean).join(' › ') : '');
  const flat = metricColumns({ getM: (r) => r, getTotalM: (t) => fixTotal(t), keys: FLAT_KEYS, shareLabel });
  const nameCol = {
    key: 'ad_name', label: 'Ad', sticky: true, cls: 'adcol',
    sort: (r) => r.ad_name,
    total: () => 'Total',
    cell: (r) => {
      if (r.ad_id == null) return h('span', { class: 'muted ital' }, r.ad_name || '(days without ad-level detail)');
      const hf = adHref(r);
      const sub = [showFamily && r.display && r.display !== r.ad_name ? r.display : null, tagPath(r)].filter(Boolean).join(' · ');
      return h('div', { class: 'cell-ad' },
        thumb(r.thumb_asset_id, 32),
        h('div', { class: 'cell-ad-text' },
          hf ? h('a', { href: hf, class: 'adname', title: r.ad_name }, r.ad_name) : h('span', { class: 'adname', title: r.ad_name }, r.ad_name),
          sub ? h('div', { class: 'sub', title: sub }, sub) : null));
    },
  };
  return [
    nameCol,
    {
      key: 'campaign_name', label: 'Campaign', sort: (r) => r.campaign_name,
      cell: (r) => {
        if (!r.campaign_name) return '';
        const ch = campaignHref && r.campaign_id ? campaignHref(r) : null;
        return ch
          ? h('a', { href: ch, class: 'plain', title: r.campaign_name }, r.campaign_name)
          : h('span', { title: r.campaign_name }, r.campaign_name);
      },
    },
    { key: 'stage', label: 'Stage', sort: (r) => r.stage, cell: (r) => (r.stage ? stageBadge(r.stage) : '') },
    { key: 'adset_name', label: 'Ad set', sort: (r) => r.adset_name, cell: (r) => h('span', { title: r.adset_name || null }, r.adset_name || '') },
    { key: 'status', label: 'Status', sort: (r) => r.status },
    { key: 'created_date', label: 'Created', sort: (r) => r.created_date, cell: (r) => fmt.text(r.created_date) },
    ...flat,
    { key: 'live', label: 'Live', sort: (r) => (r.live ? 1 : 0), cell: (r) => (r.ad_id == null ? '' : liveDot(r.live)), total: () => '', cls: 'center' },
    { key: 'days_active', label: 'Days', num: true, sort: (r) => r.days_active, cell: (r) => fmt.int(r.days_active), total: () => '', title: 'Days with spend in the window' },
  ];
}

export function adsTable(p, data, { sortKey, sortDir, sortBy, onSort, onCount, ...colOpts } = {}) {
  return createTable({
    columns: adsColumns(p, colOpts),
    rows: data.rows || [],
    total: data.total || null,
    sortKey, sortDir, sortBy, onSort, onCount,
    rowKey: (r, i) => (r.ad_id != null ? r.ad_id : 'rem' + i),
    rowHref: (r) => (colOpts.linkAd === false || r.primary_id == null || r.ad_id == null ? null : href('/ad/' + enc(r.primary_id), globalParams(p))),
    emptyText: 'No Meta ads for this selection.',
    testid: 'ads-table',
    tableClass: 'ads',
  });
}

export function campaignHrefFor(slug, p) {
  const G = globalParams(p);
  return (r) => href('/p/' + enc(slug), { ...G, view: 'ads', campaign_id: r.campaign_id });
}

// compact table of a family's Meta ads (inline expansion in the flat family table)
export async function familyAdsNode(slug, p, familyKey, signal, extra = {}) {
  const d = await api(`/api/products/${enc(slug)}/ads`, { family_key: familyKey, ...extra, ...apiWindow(p) }, { signal });
  if (!d.rows.length) return empty('No Meta ads found.');
  const t = createTable({
    columns: adsColumns(p, { showFamily: false, linkAd: false, campaignHref: campaignHrefFor(slug, p), shareLabel: '% of family' }).filter((c) => !['days_active'].includes(c.key)),
    rows: d.rows, total: d.total,
    rowKey: (r, i) => (r.ad_id != null ? r.ad_id : 'rem' + i),
    tableClass: 'ads nested', pageSize: 100,
  });
  return t.el;
}

// ---------- unmatched Meta spend ----------
export function unmatchedPanel(slug, p, preloaded, signal) {
  const summary = preloaded && preloaded.total;
  const bits = summary
    ? ` · ${fmt.money(summary.spend)} spend · ${fmt.int(summary.families)} families`
    : '';
  const body = h('div', { class: 'panel-body' });
  const details = h('details', { class: 'panel', id: 'unmatched' },
    h('summary', 'Not on the tracker', h('span', { class: 'muted' }, bits)), body);
  let loaded = false;
  details.addEventListener('toggle', async () => {
    if (!details.open || loaded) return;
    loaded = true;
    if (preloaded) { body.replaceChildren(unmatchedTable(preloaded)); return; }
    body.replaceChildren(h('span', { class: 'muted' }, 'Loading…'));
    try {
      const d = await api(`/api/products/${enc(slug)}/unmatched`, apiWindow(p), { signal });
      body.replaceChildren(unmatchedTable(d));
    } catch (e) {
      if (e instanceof AuthError || e.name === 'AbortError') return;
      loaded = false;
      body.replaceChildren(h('span', { class: 'err' }, 'Failed to load: ' + e.message));
    }
  });
  return details;
}

function unmatchedTable(d) {
  const t = d.total || {};
  const total = fixTotal({ ...t, roas: t.roas != null ? t.roas : (t.spend > 0 ? t.revenue / t.spend : null), cpa: t.cpa != null ? t.cpa : (t.purchases > 0 ? t.spend / t.purchases : null) });
  const flat = metricColumns({ getM: (r) => r, getTotalM: (x) => x, keys: FLAT_KEYS });
  const cols = [
    { key: 'meta_family', label: 'Meta family', sticky: true, total: () => `Total · ${fmt.int(t.families)} families`, cell: (r) => h('span', { title: r.meta_family }, r.meta_family) },
    ...flat,
    { key: 'live', label: 'Live', sort: (r) => (r.live ? 1 : 0), cell: (r) => liveDot(r.live), total: () => '', cls: 'center' },
  ];
  const wi = d.window_info;
  const rows = d.rows.map((r) => r);
  return h('div', null,
    columnsChips(),
    wi ? h('div', { class: 'muted small' }, 'Window: ' + (wi.kind === 'launch' ? 'all days' : wi.label)) : null,
    createTable({ columns: cols, rows, total, rowKey: (r) => r.meta_family, emptyText: 'Nothing unmatched.', testid: 'unmatched-table' }).el);
}
