// Shared page components: breadcrumbs, filter bar, Meta-ads table, unmatched panel.
import { h, enc, fmt, isNum, currentParams, href, apiWindow, globalParams, debounce, navigate, stageBadge, liveDot, thumb, empty, colPref, setColPref, refreshCmp, srcBadgeText, platformUrl } from './util.js';
import { api, AuthError } from './api.js';
import { createTable } from './table.js';
import { metricColumns, FLAT_KEYS, fixTotal, trendColumn, bothStagesIncluded } from './columns.js';
import { adActions } from './preview.js';
import { getStatus, getStageNote } from './controls.js';

export function breadcrumbs(items) {
  const last = items.length - 1;
  return h('nav', { class: 'crumbs', 'aria-label': 'Breadcrumb' },
    h('ol', items.map((it, i) =>
      h('li', { class: i === last ? 'current' : '', 'aria-current': i === last ? 'page' : null },
        it.href && i !== last ? h('a', { href: it.href }, it.chip || null, it.label) : h('span', it.chip || null, it.label)))));
}

// "View: Products | Campaigns" (Meta) or "Overview | Campaigns" (Google): the two views of a platform; the range, stage and numbers choice carry over (platform stays in G)
export function viewSwitch(active, G, platform = 'meta') {
  const first = platform === 'meta' ? 'Products' : platform === 'all' ? 'Platforms' : 'Overview';
  const mk = (label, key, to) => h('a', { class: 'seg', href: to, 'data-platform-view': key, 'aria-current': active === key ? 'page' : null, 'aria-pressed': String(active === key) }, label);
  const g = { ...G };
  delete g.type; delete g.product; delete g.only; delete g.chart; delete g.platform;
  return h('div', { class: 'ctl', id: 'view-switch' }, h('span', { class: 'ctl-name' }, 'View by'),
    h('span', { class: 'segs', role: 'group', 'aria-label': 'View by' }, mk(first, 'home', platformUrl('/', platform, g)), mk('Campaigns', 'campaigns', platformUrl('/campaigns', platform, g))));
}

// "Numbers: Upstack · new customers": which numbers the table beside it shows; text kept current by controls.js (refreshSrcBadges) on every flip
export function srcBadge() {
  return h('span', { class: 'src-badge', 'data-testid': 'src-badge', title: 'Which numbers this table shows. Change it with Numbers from, in the bar above the page.' }, srcBadgeText());
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
    srcBadge(),
    h('span', { class: 'ctl-name' }, 'Columns'),
    h('span', { class: 'chips', role: 'group', 'aria-label': 'Columns' }, mk('traffic', 'Traffic', 'Impressions, Link clicks, CTR, CPC'), mk('advanced', 'Advanced', 'CPM, Conv rate'), mk('trend', 'Trend', 'Sparkline per row')));
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

// ---------- creative / ad vocabulary ----------
export const LEGEND_TEXT = 'Creative = one ad concept and all its copies. Ad = one copy running in a campaign and ad set.';
export const LEGEND_TIP = 'Creative: one ad concept (for example Bull-393-Hook2) with all its copies and relaunches; tags attach to creatives. Ad: one individual copy of a creative running in a specific campaign and ad set.';
export function legendLine() {
  const on = currentParams().active === '1';
  const st = getStatus();
  const asOf = st && st.last_refresh && st.last_refresh.finished_at ? fmt.dateTime(st.last_refresh.finished_at) : null;
  return h('p', { class: 'legend-line', 'data-testid': 'legend' }, LEGEND_TEXT, ' ',
    h('span', { class: 'info-i', tabindex: '0', role: 'img', 'aria-label': LEGEND_TIP, title: LEGEND_TIP }, 'ⓘ'),
    getStageNote() && !bothStagesIncluded() ? h('span', { class: 'legend-stage', 'data-testid': 'legend-stage' }, h('br'), 'Stage filter: ' + getStageNote()) : null,
    on ? h('span', { class: 'legend-active', 'data-testid': 'legend-active' }, h('br'), `Showing creatives with active ads; numbers are for their active ads only${asOf ? ` (status as of ${asOf})` : ''}.`) : null);
}
export const TYPE_NAME = { VID: 'Video', IMG: 'Image' };
// second line under a creative name: type chip, status, launch date, "<n> ads · <m> live"
export function creativeMeta({ adType, status, launch, adsCount, adsLive }) {
  const bits = [];
  if (adType) bits.push(h('span', { class: 'chip-type ' + String(adType).toLowerCase(), title: 'Creative type' }, TYPE_NAME[adType] || adType));
  if (status) bits.push(h('span', { class: 'meta-status' }, status));
  if (launch) bits.push(h('span', { class: 'meta-launch' }, launch));
  if (isNum(adsCount)) bits.push(h('span', { class: 'meta-ads', title: 'Ads (copies) of this creative' }, `${fmt.int(adsCount)} ad${adsCount === 1 ? '' : 's'}` + (isNum(adsLive) ? ` · ${fmt.int(adsLive)} live` : '')));
  return bits;
}

// ---------- Ads table (one Meta ad per row) ----------
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
        thumb(r.thumb_asset_id, 32, { name: r.ad_name, primaryId: r.primary_id, metaAdId: r.ad_id, tag: r.tag, links: r.links }),
        h('div', { class: 'cell-ad-text' },
          h('div', { class: 'nm' }, hf ? h('a', { href: hf, class: 'rowname', title: r.ad_name }, r.ad_name) : h('span', { class: 'rowname', title: r.ad_name }, r.ad_name)),
          sub ? h('div', { class: 'meta sub', title: sub }, sub) : null));
    },
  };
  return [
    nameCol,
    trendColumn(),
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
    { key: 'links', label: 'Links', nosort: true, cls: 'linkscol', total: () => '', title: 'Facebook post, Ads Manager, and the live preview (eye)', cell: (r) => (r.ad_id == null ? '' : adActions(r) || '') },
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
    emptyText: 'No ads for this selection.',
    testid: 'ads-table',
    tableClass: 'ads',
  });
}

export function campaignHrefFor(slug, p) {
  const G = globalParams(p);
  return (r) => href('/p/' + enc(slug), { ...G, view: 'ads', campaign_id: r.campaign_id });
}

// compact table of a creative's ads (inline expansion in the flat creatives table)
export async function familyAdsNode(slug, p, familyKey, signal, extra = {}) {
  const d = await api(`/api/products/${enc(slug)}/ads`, { family_key: familyKey, ...extra, ...apiWindow(p) }, { signal });
  if (!d.rows.length) return empty('No ads found.');
  const t = createTable({
    columns: adsColumns(p, { showFamily: false, linkAd: false, campaignHref: campaignHrefFor(slug, p), shareLabel: '% of creative' }).filter((c) => !['days_active'].includes(c.key)),
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
    ? ` · ${fmt.money(summary.spend)} spend · ${fmt.int(summary.families)} creatives`
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
    { key: 'meta_family', label: 'Creative (not on tracker)', sticky: true, total: () => `Total · ${fmt.int(t.families)} creatives`, cell: (r) => h('span', { title: r.meta_family }, r.meta_family) },
    ...flat,
    { key: 'live', label: 'Live', sort: (r) => (r.live ? 1 : 0), cell: (r) => liveDot(r.live), total: () => '', cls: 'center' },
  ];
  const wi = d.window_info;
  const rows = d.rows.map((r) => r);
  return h('div', null,
    columnsChips(),
    wi ? h('div', { class: 'muted small', id: 'unmatched-window' }, 'Window: ' + (wi.kind === 'launch' ? 'lifetime (since launch)' : wi.label)) : null,
    createTable({ columns: cols, rows, total, rowKey: (r) => r.meta_family, emptyText: 'Nothing unmatched.', testid: 'unmatched-table' }).el);
}

// ---------- coverage: ideal customer profiles / pain points with no ads or no spend ("what we have not tested") ----------
// c = {state: 'no_ads'|'no_spend'|'tested', added_after_tagging} from /api/products/<slug>/coverage
export function coverageBadge(c) {
  if (!c || c.state === 'tested') return null;
  if (c.added_after_tagging) {
    return h('span', { class: 'badge cov added', title: 'Added to the Communication Overview after the ads were tagged, so it reads 0 by construction. Not a tested / untested call.' }, 'Added after tagging');
  }
  if (c.state === 'no_ads') return h('span', { class: 'badge cov no-ads', title: 'Not tested: no creative is tagged to this' }, 'Not tested · no ads');
  return h('span', { class: 'badge cov no-spend', title: 'Creatives are tagged here but spent $0 in the selected range, stages and source' }, 'Not tested · $0 spend');
}

const plural = (n, one, many) => `${fmt.int(n)} ${n === 1 ? one : many}`;
export function coverageText(cv) {
  const pp = cv.summary.pain_points, ic = cv.summary.icps;
  const parts = [`${fmt.int(pp.not_tested)} of ${fmt.int(pp.total)} pain points not tested (${fmt.int(pp.no_ads)} no ads, ${fmt.int(pp.no_spend)} ads but $0 spend)`,
    `${fmt.int(ic.not_tested)} of ${fmt.int(ic.total)} ideal customer profiles`];
  const added = pp.added_after_tagging + ic.added_after_tagging;
  if (added) parts.push(`${plural(pp.added_after_tagging, 'pain point', 'pain points')}${ic.added_after_tagging ? ` and ${plural(ic.added_after_tagging, 'ideal customer profile', 'ideal customer profiles')}` : ''} added after tagging, not counted`);
  return parts.join(' · ');
}

// one line of counts + the "Not tested only" switch (URL param untested=1), shown above the tag tree
export function coverageBar(cv, { path, G, on, windowLabel, sourceLabel }) {
  if (!cv || !cv.available) return null;
  const toggle = h('a', { class: 'btnlink' + (on ? ' on' : ''), id: 'untested-toggle', 'aria-pressed': String(!!on), href: href(path, { ...G, untested: on ? null : 1 }),
    title: 'List only the ideal customer profiles and pain points with no ads or no spend' }, on ? '✓ Not tested only' : 'Not tested only');
  return h('div', { class: 'coveragebar', 'data-testid': 'coverage-bar' },
    h('span', { class: 'cov-text', id: 'coverage-text' }, coverageText(cv)),
    h('span', { class: 'muted small' }, `Spend: ${windowLabel || 'selected range'} · ${sourceLabel || 'Meta'}`),
    toggle);
}

// the flat list behind "Not tested only": no ads at all / ads but $0 spend / added after tagging
export function untestedPanel(cv, { path, G, windowLabel }) {
  const rows = cv.rows;
  const hrefOf = (r) => href(path, r.level === 1 ? { ...G, cluster: r.cluster, icp: r.icp } : { ...G, cluster: r.cluster, icp: r.icp, pain: r.pain });
  const cols = [
    { key: 'kind', label: 'Level', sort: (r) => r.level, cell: (r) => (r.level === 1 ? 'Ideal customer profile' : 'Pain point') },
    { key: 'cluster', label: 'Market cluster', sort: (r) => r.cluster, cell: (r) => r.cluster },
    { key: 'icp', label: 'Ideal customer profile', sort: (r) => r.icp, cell: (r) => r.icp },
    { key: 'pain', label: 'Pain point', sticky: true, sort: (r) => r.pain || '', cell: (r) => (r.level === 1 ? h('span', { class: 'muted' }, 'all pain points') : r.pain) },
    { key: 'line', label: 'Line', sort: (r) => r.line_code || '', cell: (r) => (r.line_code ? h('span', { class: 'linecode' }, r.line_code) : '') },
    { key: 'trigger', label: 'Emotional trigger', sort: (r) => r.trigger || '', cell: (r) => r.trigger || '' },
    { key: 'ads', label: 'Creatives', num: true, sort: (r) => r.unique_ads, cell: (r) => fmt.int(r.unique_ads), title: 'Creatives tagged to this line in the tracker, any status' },
    { key: 'spend', label: 'Spend', num: true, sort: (r) => r.spend, cell: (r) => fmt.money(r.spend), title: `Spend in ${windowLabel || 'the selected range'}` },
  ];
  const section = (id, title, note, list) => {
    if (!list.length) return null;
    const t = createTable({ columns: cols, rows: list, rowKey: (r) => r.key, rowHref: hrefOf, tableClass: 'compact', testid: 'untested-' + id });
    return h('section', { class: 'untested-sec', id: 'untested-' + id },
      h('h3', null, `${title} `, h('span', { class: 'muted small' }, `${fmt.int(list.length)} ${list.length === 1 ? 'row' : 'rows'}`)),
      note ? h('div', { class: 'note-line' }, note) : null, t.el);
  };
  const noAds = rows.filter((r) => r.state === 'no_ads' && !r.added_after_tagging);
  const noSpend = rows.filter((r) => r.state === 'no_spend');
  const added = rows.filter((r) => r.added_after_tagging);
  const merged = rows.some((r) => r.line_code && r.line_code.includes(' / '));
  return h('div', { class: 'untested', 'data-testid': 'untested-panel' },
    section('no-ads', 'No ads at all', 'No creative in the tracker is tagged to these (any status, any date). This list does not change with the date range or the source.', noAds),
    section('no-spend', 'Ads, but $0 spend', `Creatives are tagged here but spent nothing in ${windowLabel || 'the selected range'} (follows the range, stage filter and source).`, noSpend),
    section('added', 'Added after tagging', 'Lines added to the Communication Overview after the ads were tagged. They read 0 by construction, not because they were skipped.', added),
    !rows.length ? h('div', { class: 'empty' }, 'Every ideal customer profile and pain point has spend in this range.') : null,
    h('ul', { class: 'cov-caveats' },
      h('li', null, 'Each ad carries exactly one line from the tagging (blind re-tag agreement on the exact line is roughly 73 to 82% by product), so a line showing none may have ads sitting on a neighbouring line.'),
      merged ? h('li', null, 'Lines that share a pain point and differ only by emotional trigger (Boneflex BF36 / BF38) are one row and count once.') : null));
}
