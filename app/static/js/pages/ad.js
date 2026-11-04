import { h, enc, fmt, href, apiWindow, globalParams, badge, confBadge, stageBadge, liveDot, methodFlags, safeUrl, empty, isNum, colorChip, safeColor, cmpView, onCmpRefresh, setParentHint } from '../util.js';
import { api } from '../api.js';
import { createTable } from '../table.js';
import { setRangeInfo } from '../controls.js';
import { flatTotal } from '../columns.js';
import { breadcrumbs, section, adsTable } from '../components.js';
import { dailyChart } from '../charts.js';

function kv(label, value, cls = '') {
  if (value == null || value === '' || (Array.isArray(value) && !value.length)) return null;
  return h('div', { class: 'kv ' + cls }, h('div', { class: 'k' }, label), h('div', { class: 'v' }, value));
}
function extLink(label, url) {
  const u = safeUrl(url);
  return u ? h('a', { href: u, target: '_blank', rel: 'noopener noreferrer', class: 'ext' }, label + ' ↗') : null;
}
function textBlock(t) { return h('div', { class: 'copyblock' }, t); }

function pctSpan(pct, dir) {
  if (!isNum(pct)) return '–';
  let cls = 'neutral';
  if (pct !== 0 && dir !== 'neutral') cls = (dir === 'up') === (pct > 0) ? 'good' : 'bad';
  return h('span', { class: 'delta ' + cls }, fmt.pctChange(pct));
}

// window A | window B | delta table (only when comparing)
function compareTable(d, wi) {
  const f = d.metrics && d.metrics.family;
  const ci = d.compare_info;
  if (!f || !f.compare || !ci || !cmpView.on) return null;
  const perDay = cmpView.perDay;
  const da = wi && wi.days, db = ci.days;
  const val = (m, k, days) => (perDay && ['spend', 'purchases', 'revenue'].includes(k) && days ? m[k] / days : m[k]);
  const rows = [
    ['spend', perDay ? 'Spend / day' : 'Spend', fmt.money, 'neutral'],
    ['purchases', perDay ? 'Purchases / day' : 'Purchases', perDay ? fmt.dec1 : fmt.int, 'up'],
    ['revenue', perDay ? 'Revenue / day' : 'Revenue', fmt.money, 'up'],
    ['roas', 'ROAS', fmt.roas, 'up'],
    ['cpa', 'CPA', fmt.money, 'down'],
  ];
  const tr = rows.map(([k, label, ff, dir]) => {
    const a = val(f.window, k, da), b = val(f.compare, k, db);
    let pct = f.delta ? f.delta[(perDay && ['spend', 'purchases', 'revenue'].includes(k) ? k + '_per_day' : k) + '_pct'] : null;
    if (pct == null && isNum(a) && isNum(b) && b !== 0) pct = (a - b) / b;
    const diff = isNum(a) && isNum(b) ? a - b : null;
    return h('tr', { 'data-metric': k },
      h('th', { scope: 'row' }, label),
      h('td', { class: 'num a' }, ff(a)),
      h('td', { class: 'num b' }, ff(b)),
      h('td', { class: 'num d' }, diff == null ? '–' : (diff > 0 ? '+' : diff < 0 ? '-' : '') + ff(Math.abs(diff))),
      h('td', { class: 'num p' }, pctSpan(pct, dir)));
  });
  return h('div', { class: 'cmp-table', id: 'compare-table' },
    h('div', { class: 'subtle-head' }, 'Window A vs B'),
    h('table', { class: 'mini' },
      h('thead', h('tr', h('th'), h('th', { class: 'num' }, 'A · ' + (wi && wi.label || '')), h('th', { class: 'num' }, 'B · ' + (ci.label || '')), h('th', { class: 'num' }, 'Δ'), h('th', { class: 'num' }, 'Δ %'))),
      h('tbody', tr)));
}

function metricsCard(d, wi) {
  const f = d.metrics && d.metrics.family;
  if (!f) return null;
  const rows = [['Since launch', f.launch, 'launch'], ['Last 7d', f.d7, 'd7'], ['Last 30d', f.d30, 'd30']];
  if (wi && wi.kind !== 'launch' && f.window) rows.push([wi.label || 'Selected window', f.window, 'window']);
  const tr = rows.map(([label, m, key]) => h('tr', { 'data-row': key },
    h('th', { scope: 'row' }, label),
    h('td', { class: 'num', title: m ? fmt.money2(m.spend) : null }, fmt.money(m && m.spend)),
    h('td', { class: 'num' }, fmt.int(m && m.purchases)),
    h('td', { class: 'num', title: m ? fmt.money2(m.revenue) : null }, fmt.money(m && m.revenue)),
    h('td', { class: 'num' }, fmt.roas(m && m.roas)),
    h('td', { class: 'num' }, fmt.money(m && m.cpa))));
  const cmpBlock = compareTable(d, wi);
  return h('div', { class: 'card', id: 'family-metrics' },
    h('div', { class: 'card-head' }, h('h3', 'Family metrics'), h('span', { class: 'live-flag' }, liveDot(f.live), f.live ? ' Live now' : ' Not live')),
    h('table', { class: 'mini' },
      h('thead', h('tr', h('th'), ['Spend', 'Purchases', 'Revenue', 'ROAS', 'CPA'].map((x) => h('th', { class: 'num' }, x)))),
      h('tbody', tr)),
    cmpBlock,
    h('div', { class: 'muted small' }, d.metrics.counted_on_this_row
      ? 'Metrics are counted on this tracker row (first row of the ad family).'
      : 'Family-level numbers. In the tables they are counted once, on the family’s first tracker row, which is not this one.'));
}

function tagBlock(d) {
  const t = d.tag;
  const tt = d.tracker_tag || {};
  const body = [];
  if (t) {
    const fl = methodFlags(t.method);
    body.push(h('div', { class: 'tag-head' },
      t.line_code ? h('span', { class: 'linecode big' }, t.line_code) : null,
      h('span', { class: 'tag-path' }, [t.cluster, t.icp, t.pain_point].filter(Boolean).join(' › ')),
      confBadge(t.confidence),
      t.method ? badge(t.method, 'method', 'How the ad was tagged') : null,
      fl.copyOnly ? badge('copy only', 'flag-copy') : null,
      fl.thumbOnly ? badge('thumbnail only', 'flag-thumb') : null));
    if (t.reliability_note) body.push(h('div', { class: 'reliability', id: 'reliability-note' }, t.reliability_note));
    body.push(
      kv('Emotional trigger', t.emotional_trigger),
      kv('What was seen', t.seen),
      kv('Why', t.why),
      kv('Runner-up', t.runner_up),
      t.copy_only_line ? kv('Copy-only pick', `${t.copy_only_line.line_code || ''}${t.copy_only_line.label ? ' · ' + t.copy_only_line.label : ''}`) : null,
      h('div', { class: 'muted small' }, `Tag set ${t.tag_set}${t.version ? ' · ' + t.version : ''}`));
  } else {
    body.push(h('div', { class: 'muted' }, 'Not line-mapped. Tracker tags below.'));
  }
  body.push(h('div', { class: 'subtle-head' }, 'Tracker tags'),
    h('div', { class: 'kvgrid' },
      kv('Market cluster', tt.cluster), kv('ICP', tt.icp), kv('Pain point', tt.pain_point),
      kv('Emotion', tt.emotion), kv('Ingredient', tt.ingredient)));
  return h('div', { class: 'card', id: 'tag-block' }, h('div', { class: 'card-head' }, h('h3', 'Tag')), body);
}

function lineBlock(d) {
  const l = d.line;
  if (!l) return null;
  const ing = (l.ingredients || []);
  return h('div', { class: 'card', id: 'line-block' },
    h('div', { class: 'card-head' }, h('h3', 'Communication Overview'), l.line_code ? h('span', { class: 'linecode' }, l.line_code) : null),
    kv('Story', l.story),
    kv('Emotional trigger', l.emotional_trigger),
    ing.length ? h('table', { class: 'mini ingredients' },
      h('thead', h('tr', h('th', 'Ingredient'), h('th', 'What it attacks'), h('th', 'What it supports'))),
      h('tbody', ing.map((i) => h('tr', h('td', i.ingredient || ''), h('td', i.attacks || ''), h('td', i.supports || ''))))) : null);
}

function mediaCard(d) {
  const a = d.assets || {};
  const ids = [];
  if (a.thumb != null) ids.push({ id: a.thumb, label: 'Thumbnail' });
  for (const f of (a.frames || []).slice(0, 4)) ids.push({ id: f, label: 'Frame' });
  const img = (x, big) => {
    const url = '/media/' + enc(x.id);
    const im = h('img', { src: url, alt: x.label, loading: 'lazy', class: big ? 'media-main' : 'media-frame' });
    im.addEventListener('error', () => im.replaceWith(h('div', { class: 'thumb ph media-missing' }, 'no image')));
    return h('a', { href: url, target: '_blank', rel: 'noopener', class: 'media-link' }, im);
  };
  if (!ids.length) return h('div', { class: 'card media', id: 'media' }, h('div', { class: 'media-missing' }, 'No image or frames stored for this ad.'));
  const main = ids[0];
  const frames = a.thumb != null ? ids.slice(1) : ids;
  return h('div', { class: 'card media', id: 'media' },
    a.thumb != null ? img(main, true) : null,
    frames.length ? h('div', { class: 'frames' }, frames.map((x) => img(x, false))) : null);
}

function copyBlock(d) {
  const c = d.content || {};
  const parts = [];
  if (d.concept_name || d.description || d.hypothesis) {
    parts.push(h('div', { class: 'subblock' },
      kv('Concept', d.concept_name), kv('Description', d.description), kv('Hypothesis', d.hypothesis), kv('Meta ad name', d.meta_ad_name)));
  }
  if ((c.primary_text || []).length) parts.push(h('div', { class: 'subblock' }, h('div', { class: 'subtle-head' }, 'Primary text'), c.primary_text.map((t, i) => h('div', { class: 'copy-item' }, c.primary_text.length > 1 ? h('span', { class: 'idx' }, i + 1) : null, textBlock(t)))));
  if ((c.headlines || []).length) parts.push(h('div', { class: 'subblock' }, h('div', { class: 'subtle-head' }, 'Headlines'), h('ul', { class: 'headlines' }, c.headlines.map((t) => h('li', t)))));
  if (c.description) parts.push(h('div', { class: 'subblock' }, h('div', { class: 'subtle-head' }, 'Description'), textBlock(c.description)));
  const meta = [kv('Meta body', c.meta_body), kv('Meta title', c.meta_title), kv('Meta description', c.meta_desc)].filter(Boolean);
  if (meta.length) parts.push(h('div', { class: 'subblock' }, h('div', { class: 'subtle-head' }, 'As running on Meta'), meta));
  if (c.transcript) {
    parts.push(h('details', { class: 'subblock transcript', open: !!c.is_video, id: 'transcript' }, h('summary', 'Video transcript'), textBlock(c.transcript)));
  }
  if (!parts.length) parts.push(h('div', { class: 'muted' }, 'No copy stored.'));
  return h('div', { class: 'card', id: 'copy-block' }, h('div', { class: 'card-head' }, h('h3', 'Copy')), parts);
}

function siblingsTable(d, G) {
  const rows = d.siblings || [];
  if (!rows.length) return null;
  const cols = [
    { key: 'concept_id', label: 'Concept ID', sticky: true, sort: (r) => r.concept_id, total: null, cell: (r) => h('a', { href: href('/ad/' + enc(r.id), G), class: 'rowlink' }, r.concept_id) },
    { key: 'line_code', label: 'Line', sort: (r) => r.line_code, cell: (r) => (r.line_code ? h('span', { class: 'linecode' }, r.line_code) : '') },
    { key: 'cluster', label: 'Cluster', sort: (r) => r.cluster },
    { key: 'icp', label: 'ICP', sort: (r) => r.icp },
    { key: 'pain_point', label: 'Pain point', sort: (r) => r.pain_point },
    { key: 'status', label: 'Status', sort: (r) => r.status },
  ];
  return section('Other tracker rows in this family', createTable({ columns: cols, rows, rowKey: (r) => String(r.id), rowHref: (r) => href('/ad/' + enc(r.id), G), tableClass: 'compact', testid: 'siblings-table' }).el, { id: 'siblings' });
}

function campaignsTable(d, p, slug, G) {
  const raw = d.campaigns || [];
  const totSpend = raw.reduce((a, c) => a + (c.spend || 0), 0);
  const rows = raw.map((c) => ({ ...c, roas: c.spend > 0 ? c.revenue / c.spend : null, cpa: c.purchases > 0 ? c.spend / c.purchases : null, spend_share: c.spend_share != null ? c.spend_share : (totSpend > 0 ? c.spend / totSpend : null) }));
  if (!rows.length) return null;
  const sum = (k) => rows.reduce((a, r) => a + (r[k] || 0), 0);
  const tot = { spend: sum('spend'), purchases: sum('purchases'), revenue: sum('revenue') };
  tot.roas = tot.spend > 0 ? tot.revenue / tot.spend : null;
  tot.cpa = tot.purchases > 0 ? tot.spend / tot.purchases : null;
  const path = '/p/' + enc(slug);
  const cols = [
    { key: 'name', label: 'Campaign', sticky: true, sort: (r) => r.name, total: () => 'Total', cell: (r) => h('a', { href: href(path, { ...G, view: 'ads', campaign_id: r.campaign_id }), class: 'rowlink' }, r.name) },
    { key: 'stage', label: 'Stage', sort: (r) => r.stage, cell: (r) => stageBadge(r.stage) },
    { key: 'spend', label: 'Spend', num: true, sort: (r) => r.spend, cell: (r) => fmt.money(r.spend), total: (t) => fmt.money(t.spend) },
    { key: 'spend_share', label: '% of spend', num: true, sort: (r) => r.spend_share, cell: (r) => fmt.share(r.spend_share), total: () => '100.0%' },
    { key: 'purchases', label: 'Purchases', num: true, sort: (r) => r.purchases, cell: (r) => fmt.int(r.purchases), total: (t) => fmt.int(t.purchases) },
    { key: 'revenue', label: 'Revenue', num: true, sort: (r) => r.revenue, cell: (r) => fmt.money(r.revenue), total: (t) => fmt.money(t.revenue) },
    { key: 'roas', label: 'ROAS', num: true, sort: (r) => r.roas, cell: (r) => fmt.roas(r.roas), total: (t) => fmt.roas(t.roas) },
    { key: 'cpa', label: 'CPA', num: true, sort: (r) => r.cpa, cell: (r) => fmt.money(r.cpa), total: (t) => fmt.money(t.cpa) },
  ];
  return section('Campaigns', createTable({ columns: cols, rows, total: rows.length > 1 ? tot : null, rowKey: (r) => r.campaign_id, rowHref: (r) => href(path, { ...G, view: 'ads', campaign_id: r.campaign_id }), tableClass: 'compact', testid: 'campaigns-mini-table' }).el, { id: 'campaigns' });
}

export async function adPage(ctx) {
  const { params: p, signal } = ctx;
  const G = globalParams(p);
  const id = ctx.match[1];
  const d = await api('/api/ads/' + enc(id), apiWindow(p), { signal });
  if (!ctx.alive()) return;
  const wi = d.window_info;
  setRangeInfo(p, wi, d.compare_info || null);
  const slug = d.product.slug;
  const path = '/p/' + enc(slug);
  const title = d.ad_family || d.concept_id;
  document.title = `${title} · Tag Explorer`;

  const tg = d.tag || {};
  const tt = d.tracker_tag || {};
  const cluster = tg.cluster || tt.cluster, icp = tg.icp || tt.icp, pain = tg.pain_point || tt.pain_point;
  const crumbs = [{ label: 'Products', href: href('/', G) }, { label: d.product.name, href: href(path, G) }];
  if (cluster) crumbs.push({ label: cluster, href: href(path, { ...G, cluster }) });
  if (cluster && icp) crumbs.push({ label: icp, href: href(path, { ...G, cluster, icp }) });
  if (cluster && icp && pain) crumbs.push({ label: pain, href: href(path, { ...G, cluster, icp, pain }) });
  crumbs.push({ label: title });

  const meta = [
    d.concept_id ? `Concept ${d.concept_id}` : null,
    d.sheet_row ? `Tracker row ${d.sheet_row}` : null,
    d.launch_date ? `Launched ${d.launch_date}` : null,
    d.style ? `Style ${d.style}` : null,
    d.vendor ? `Vendor ${d.vendor}` : null,
  ].filter(Boolean).join(' · ');

  // chart
  const daily = d.daily || [];
  const canvas = h('canvas', { id: 'daily-chart', role: 'img', 'aria-label': 'Daily spend, revenue and purchases for this ad family' });
  const shades = [];
  if (wi && wi.from && wi.to) shades.push({ from: wi.from, to: wi.to, color: 'rgba(26,95,180,0.14)', name: 'A' });
  else if (daily.length && d.launch_date && d.launch_date >= daily[0].day) shades.push({ from: d.launch_date, to: daily[daily.length - 1].day, color: 'rgba(26,95,180,0.14)', name: 'A' });
  if (d.compare_info && d.compare_info.from && cmpView.on) shades.push({ from: d.compare_info.from, to: d.compare_info.to, color: 'rgba(217,130,43,0.20)', name: 'B' });
  let chart = null;
  const toggles = ['Spend', 'Revenue', 'Purchases'].map((name, i) => {
    const cb = h('input', { type: 'checkbox', checked: true, 'data-series': name.toLowerCase(), id: 'toggle-' + name.toLowerCase() });
    cb.addEventListener('change', () => { if (chart) { chart.setDatasetVisibility(i, cb.checked); chart.update(); } });
    return h('label', { class: 'check' }, cb, ' ' + name);
  });
  const chartCard = h('div', { class: 'card', id: 'daily-card' },
    h('div', { class: 'card-head' }, h('h3', 'Daily'), h('span', { class: 'toggles' }, toggles),
      h('span', { class: 'muted small shade-note' }, shades.length ? (shades.length > 1 ? 'Shaded: A (blue) and B (orange)' : `Shaded: ${wi && wi.kind !== 'launch' && wi.label ? wi.label : 'since launch'}`) : (daily.length ? `${fmt.day(daily[0].day)} – ${fmt.day(daily[daily.length - 1].day)}` : ''))),
    daily.length ? h('div', { class: 'chartbox' }, canvas) : empty('No daily data.'));

  const metaAds = d.meta_ads || [];
  const famTotal = flatTotal(metaAds, { days: wi && wi.days, bdays: d.compare_info && d.compare_info.days });
  const adsRows = metaAds.map((r) => (r.spend_share != null ? r : { ...r, spend_share: famTotal.spend > 0 ? r.spend / famTotal.spend : null }));
  const adsT = metaAds.length ? adsTable({ ...p }, { rows: adsRows, total: metaAds.length > 1 ? famTotal : null }, {
    showFamily: false, linkAd: false, shareLabel: '% of family',
    campaignHref: (r) => href(path, { ...G, view: 'ads', campaign_id: r.campaign_id }),
  }) : null;
  if (adsT) onCmpRefresh(() => adsT.refresh());
  setParentHint(href(path, { ...G, cluster, icp, pain }));

  ctx.mount(h('div', { class: 'page ad-detail' },
    h('div', { class: 'crumbrow' }, breadcrumbs(crumbs)),
    h('div', { class: 'pagehead' },
      h('div', { class: 'titleline' },
        h('h1', { id: 'ad-title' }, title),
        d.status ? badge(d.status, 'status') : null,
        d.ad_type ? badge(d.ad_type, 'type') : null,
        h('span', { class: 'spacer' }),
        extLink('Story', d.story_url), extLink('Asset', d.asset_url),
        h('a', { class: 'btnlink', href: href(path, { ...G, view: 'ads', family_key: d.family_key }) }, 'Meta ads table')),
      h('div', { class: 'note-line' }, meta)),
    h('div', { class: 'adgrid' },
      h('div', { class: 'adcol-left' }, mediaCard(d), metricsCard(d, wi)),
      h('div', { class: 'adcol-right' }, chartCard)),
    h('div', { class: 'twocol' }, tagBlock(d), lineBlock(d)),
    copyBlock(d),
    siblingsTable(d, G),
    adsT ? section('Meta ads in this family', adsT.el, { id: 'meta-ads' }) : null,
    campaignsTable(d, p, slug, G),
  ));
  onCmpRefresh(() => {
    const old = document.getElementById('compare-table');
    const nu = compareTable(d, wi);
    if (old && nu) old.replaceWith(nu);
  });
  if (daily.length) {
    chart = dailyChart(canvas, daily, shades);
  }
}
