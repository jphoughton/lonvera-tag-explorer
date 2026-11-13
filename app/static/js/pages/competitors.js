// Competitors (/competitors): a weekly view of what watched competitors run on Meta. One plain sortable table, one row per brand; click a row to open its detail underneath.
// Everything from the API (ad copy, brand names, landing pages, post titles) goes in through textContent / h(); links are built with h('a'), never innerHTML. Ad Library links are built by the API from the Library ID.
// Class names and test ids start with `comp-` (never ad-, ads-, sponsor, banner, promo: ad blockers hide those).
import { h, fmt, isNum, href, navigate } from '../util.js';
import { api } from '../api.js';
import { showControls } from '../controls.js';
import { pageHeader } from '../ui/pagehead.js';
import { filterRow, seg, selectCtl } from '../ui/pagefilters.js';

const md = (s) => { const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(s || ''); return m ? `${+m[2]}/${+m[3]}` : (s || ''); };
const weekLabel = (s) => 'Week of ' + md(s);
const num = (v) => (isNum(v) ? fmt.int ? fmt.int(v) : String(v) : '–');
const signed = (v) => (isNum(v) ? (v > 0 ? '+' : '') + v : '–');
const pct = (v) => (isNum(v) ? Math.round(v * 100) + '%' : '–');
const FORMAT_NAMES = { video: 'Video', image: 'Image' };

function link(url, text, attrs) { return h('a', { href: url, target: '_blank', rel: 'noopener noreferrer', ...(attrs || {}) }, text); }
const safeHttp = (u) => { try { const x = new URL(u); return x.protocol === 'https:' || x.protocol === 'http:' ? u : null; } catch (e) { return null; } };

const COLS = [
  ['brand', 'Brand', 'Brand name', (r) => r.brand, false],
  ['product_type', 'Sells', 'What the brand sells', (r) => r.product_type || '', false],
  ['active_ads', 'Active ads', 'Ads running now in the snapshot', (r) => r.active_ads, true],
  ['change', 'Change', 'Active ads now minus active ads the week before', (r) => r.change, true],
  ['new_ads', 'New', 'Ads running now that were not running the week before', (r) => r.new_ads, true],
  ['stopped_ads', 'Stopped', 'Ads that ran the week before and are gone now', (r) => r.stopped_ads, true],
  ['avg_days_running', 'Avg days running', 'Average days since the ads started', (r) => r.avg_days_running, true],
  ['max_days_running', 'Longest days running', 'Days since the oldest running ad started', (r) => r.max_days_running, true],
  ['video_share', 'Video share', 'Share of the ads that are video', (r) => r.video_share, true],
  ['top_landing_domain', 'Top landing site', 'Site most ads send people to', (r) => r.top_landing_domain || '', false],
  ['is_stale', 'Status', 'Stale means the brand could not be read this week and the numbers are from an earlier week', (r) => (r.is_stale ? 1 : 0), false],
];
const CELL = {
  active_ads: (r) => num(r.active_ads), change: (r) => signed(r.change), new_ads: (r) => num(r.new_ads), stopped_ads: (r) => num(r.stopped_ads),
  avg_days_running: (r) => (isNum(r.avg_days_running) ? String(Math.round(r.avg_days_running)) : '–'), max_days_running: (r) => num(r.max_days_running), video_share: (r) => pct(r.video_share),
};

function plainTable(heads, rows, testid) {
  return h('div', { class: 'comp-scroll' }, h('table', { class: 'pri-table comp-sub', 'data-testid': testid },
    h('thead', null, h('tr', null, heads.map((x) => h('th', { scope: 'col' }, x)))), h('tbody', null, rows)));
}

function adRow(a) {
  const u = safeHttp(a.ad_library_url);
  return h('tr', null,
    h('td', { class: 'comp-hook' }, a.hook || '(no text)'),
    h('td', { class: 'comp-n' }, isNum(a.days_running) ? String(a.days_running) : '–'),
    h('td', null, a.format || ''),
    h('td', { class: 'comp-n' }, String(a.variant_count || 1)),
    h('td', null, [a.cluster, a.pain_point].filter(Boolean).join(' / ') || '–'),
    h('td', null, a.landing_domain || '–'),
    h('td', null, u ? link(u, 'Open in Ad Library', { 'data-testid': 'comp-adlink' }) : ''));
}
const AD_HEADS = ['Ad text', 'Days running', 'Format', 'Ads sharing it', 'Angle', 'Lands on', 'Link'];

function section(title, note, body, testid) {
  return h('section', { class: 'comp-sec', 'data-testid': testid }, h('h3', null, title), note ? h('p', { class: 'pri-sub' }, note) : null, body);
}

function detailPanel(d, st) {
  const parts = [];
  const bl = safeHttp(d.brand_ad_library_url);
  parts.push(h('p', { class: 'comp-top' }, bl ? link(bl, `Search ${d.brand} in the Ad Library`, { 'data-testid': 'comp-brandlink' }) : null,
    d.format !== 'all' ? h('span', { class: 'pri-sub' }, ` Showing ${d.format} ads only.`) : null));
  parts.push(section('Longest running ads', 'The ads kept on longest are the best sign of what works for them. Start dates reset when an ad is re-uploaded, so these ages can be too low.',
    d.longest_running.length ? plainTable(AD_HEADS, d.longest_running.map(adRow), 'comp-longest') : h('p', { class: 'pri-sub' }, 'No ads in this view.'), 'comp-sec-longest'));
  parts.push(section('New this week', null,
    d.first_snapshot ? h('p', { class: 'pri-sub', 'data-testid': 'comp-first' }, 'First snapshot: new ads show from next week.')
      : d.new_this_week.length ? plainTable(AD_HEADS, d.new_this_week.map(adRow), 'comp-new') : h('p', { class: 'pri-sub' }, 'No new ads this week.'), 'comp-sec-new'));
  parts.push(section('Angles they push', 'Which message each ad leads with, matched to our market cluster and pain point lists. "New angle" means we have no line for it.',
    d.angles.length ? plainTable(['Market cluster', 'Pain point', 'Ads', 'Concepts', 'New angle'], d.angles.map((g) => h('tr', null,
      h('td', null, g.cluster || '–'), h('td', null, g.pain_point || '–'), h('td', { class: 'comp-n' }, String(g.ads)), h('td', { class: 'comp-n' }, String(g.concepts)),
      h('td', null, g.is_new_angle ? (g.new_angle_label || 'Yes') : ''))), 'comp-angles') : h('p', { class: 'pri-sub' }, 'No ads in this view.'), 'comp-sec-angles'));
  parts.push(section('Landing pages', null,
    d.landing_pages.length ? plainTable(['Page', 'Ads'], d.landing_pages.map((p) => h('tr', null, h('td', null, p.page), h('td', { class: 'comp-n' }, String(p.ads)))), 'comp-pages') : h('p', { class: 'pri-sub' }, 'No landing pages read.'), 'comp-sec-pages'));
  if (d.chatter && d.chatter.length) parts.push(section('Chatter about them', null, chatterTable(d.chatter), 'comp-sec-bchatter'));
  return h('div', { class: 'comp-detail' }, parts);
}

function chatterTable(rows) {
  return plainTable(['Post', 'Source', 'Brand', 'Score', 'Comments'], rows.map((p) => {
    const u = safeHttp(p.url);
    return h('tr', null, h('td', null, u ? link(u, p.title || u) : (p.title || '')), h('td', null, [p.source, p.subreddit].filter(Boolean).join(' / ')), h('td', null, p.brand || '–'),
      h('td', { class: 'comp-n' }, num(p.score)), h('td', { class: 'comp-n' }, num(p.num_comments)));
  }), 'comp-chatter');
}

export async function competitorsPage(ctx) {
  showControls(false);
  const st = { week: ctx.params.week || '', format: FORMAT_NAMES[ctx.params.format] ? ctx.params.format : '', sort: 'active_ads', dir: -1 };
  let data = { weeks: [], brands: [], changes: [] };
  const open = new Set();
  const cache = new Map();
  const body = h('div', { class: 'comp-body' });
  const msg = h('p', { class: 'prod-msg', role: 'status', 'data-testid': 'comp-msg' });
  const root = h('div', { class: 'page pri-page comp-page', 'data-testid': 'comp-page' }, body);

  const params = () => ({ week: st.week, format: st.format });
  const load = async () => { data = await api('/api/competitors', params(), { signal: ctx.signal }); if (!st.week && data.week) st.week = ''; draw(); };
  const refresh = () => { open.clear(); cache.clear(); navigate(href('/competitors', params()), { replace: true, render: false }); load().catch((e) => { if (!(e && e.name === 'AbortError')) { msg.textContent = e.message || 'Could not load.'; msg.classList.add('pri-err'); } }); };

  async function detailFor(brand) {
    const k = [brand, data.week, st.format].join('|');
    if (!cache.has(k)) cache.set(k, api('/api/competitors/' + encodeURIComponent(brand), { week: data.week, format: st.format }, { signal: ctx.signal }));
    return cache.get(k);
  }
  async function fillDetail(cell, brand) {
    cell.replaceChildren(h('p', { class: 'pri-sub' }, 'Loading...'));
    try { cell.replaceChildren(detailPanel(await detailFor(brand), st)); } catch (e) { if (e && e.name === 'AbortError') return; cell.replaceChildren(h('p', { class: 'pri-err' }, e.message || 'Could not load this brand.')); }
  }

  function brandRows(r) {
    const isOpen = open.has(r.brand);
    const cell = h('td', { colspan: String(COLS.length), class: 'comp-xcell', 'data-testid': 'comp-detail' });
    const detail = h('tr', { class: 'comp-detailrow', hidden: !isOpen, 'data-brand': r.brand }, cell);
    const toggle = () => {
      if (open.has(r.brand)) { open.delete(r.brand); detail.hidden = true; main.setAttribute('aria-expanded', 'false'); return; }
      open.add(r.brand); detail.hidden = false; main.setAttribute('aria-expanded', 'true'); fillDetail(cell, r.brand);
    };
    const main = h('tr', { class: 'comp-row', 'data-testid': 'comp-row', 'data-brand': r.brand, 'aria-expanded': String(isOpen), onclick: (e) => { if (e.target.closest('a')) return; toggle(); } },
      COLS.map(([key]) => {
        if (key === 'brand') return h('td', { class: 'comp-c' }, h('button', { type: 'button', class: 'comp-name', 'data-testid': 'comp-name', 'aria-label': 'Show details for ' + r.brand, onclick: (e) => { e.stopPropagation(); toggle(); } }, r.brand));
        if (key === 'is_stale') return h('td', { class: 'comp-c', 'data-testid': 'comp-status' }, r.is_stale ? h('span', { class: 'comp-stale', title: r.last_ok_at ? 'Last read OK ' + md(r.last_ok_at) : '' }, 'Stale' + (r.last_ok_at ? ' (read ' + md(r.last_ok_at) + ')' : '')) : 'Read');
        const f = CELL[key];
        return h('td', { class: 'comp-c' + (f ? ' comp-n' : ''), 'data-col': key }, f ? f(r) : (r[key] || '–'));
      }));
    if (isOpen) fillDetail(cell, r.brand);
    return [main, detail];
  }

  function sorted() {
    const col = COLS.find((c) => c[0] === st.sort) || COLS[2];
    const get = col[3];
    return data.brands.slice().sort((a, b) => {
      const x = get(a), y = get(b);
      if (x === null || x === undefined) return (y === null || y === undefined) ? 0 : 1;      // blanks last whichever way it sorts
      if (y === null || y === undefined) return -1;
      const c = typeof x === 'string' ? x.localeCompare(y) : x - y;
      return (c || a.brand.localeCompare(b.brand)) * st.dir;
    });
  }

  function draw() {
    const weeks = data.weeks || [];
    const controls = filterRow(
      selectCtl({ name: 'Week', options: weeks.length ? weeks.map((w) => [w, weekLabel(w)]) : [['', 'No snapshots']], value: data.week || '', onChange: (v) => { st.week = v; refresh(); }, testid: 'comp-week' }),
      seg({ name: 'Format', options: [['', 'All', 'Video and image ads', 'comp-format-all'], ['video', 'Video', 'Video ads only', 'comp-format-video'], ['image', 'Image', 'Image ads only (carousels count as image)', 'comp-format-image']],
        value: st.format, onChange: (v) => { st.format = v; refresh(); }, testid: 'comp-format' }));
    const changes = h('section', { class: 'comp-changes', 'data-testid': 'comp-changes' }, h('h2', null, 'What changed this week'),
      data.notice ? h('p', { class: 'pri-sub', 'data-testid': 'comp-notice' }, data.notice) : null,
      data.changes && data.changes.length ? h('ul', null, data.changes.map((c) => h('li', { 'data-kind': c.kind, 'data-testid': 'comp-fact' }, c.text, ' ', c.ad_library_url && safeHttp(c.ad_library_url) ? link(c.ad_library_url, 'Open ad', { 'data-testid': 'comp-factlink' }) : null))) : (data.week ? h('p', { class: 'pri-sub' }, 'Nothing stands out.') : null));
    let table;
    if (!data.week) table = h('p', { 'data-testid': 'comp-empty' }, data.notice || 'No snapshots yet.');
    else if (!data.brands.length) table = h('p', { 'data-testid': 'comp-empty' }, 'No brands in this view.');
    else {
      const head = h('tr', null, COLS.map(([key, label, tip]) => {
        const on = st.sort === key;
        return h('th', { scope: 'col', title: tip, 'aria-sort': on ? (st.dir > 0 ? 'ascending' : 'descending') : 'none' },
          h('button', { type: 'button', class: 'comp-sort', 'data-testid': 'comp-sort-' + key, onclick: () => { if (st.sort === key) st.dir = -st.dir; else { st.sort = key; st.dir = ['brand', 'product_type', 'top_landing_domain'].includes(key) ? 1 : -1; } draw(); } },
            label + (on ? (st.dir > 0 ? ' ▲' : ' ▼') : '')));
      }));
      table = h('div', { class: 'comp-scroll', 'data-testid': 'comp-scroll' }, h('table', { class: 'pri-table comp-table', 'data-testid': 'comp-table' }, h('thead', null, head), h('tbody', null, sorted().flatMap(brandRows))));
    }
    const chatter = data.chatter ? h('section', { class: 'comp-sec', 'data-testid': 'comp-chatter-sec' }, h('h2', null, 'Chatter'), data.chatter.length ? chatterTable(data.chatter) : h('p', { class: 'pri-sub' }, 'No posts stored for this week.')) : null;
    body.replaceChildren(
      pageHeader({ title: 'Competitors', description: 'What competitors run on Meta, week by week, from the public Ad Library. Brands show their ads running now, not spend or results.', testid: 'comp-head' }),
      h('p', { class: 'pri-sub comp-note', 'data-testid': 'comp-note' }, 'Each brand is a top slice of its ads by reach, so new and stopped counts also move with the slice. Format is video or image only: carousels count as image.'),
      controls, msg, changes, table, chatter);
  }

  await load();
  ctx.mount(root);
  document.title = 'Competitors · Lonvera';
}
