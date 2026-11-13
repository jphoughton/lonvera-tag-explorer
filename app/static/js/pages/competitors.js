// Competitors (/competitors): a weekly, visual view of what watched competitors run on Meta: a pulse strip, a "Worth a look" rail of ads, a wall of brand tiles (or the old sortable table), an angle chart and compact chatter cards.
// A brand opens in place (gallery, angles, landing pages). Ads show their stored thumbnail, or the hook set large on a tinted card when none is stored.
// Everything from the API (ad copy, brand names, landing pages, post titles) goes in through textContent / h(); links are built with h('a'), never innerHTML. Ad Library links are built by the API from the Library ID.
// Class names and test ids start with `comp-` (never ad-, ads-, sponsor, banner, promo: ad blockers hide those).
import { h, fmt, isNum, href, navigate } from '../util.js';
import { api } from '../api.js';
import { showControls } from '../controls.js';
import { pageHeader } from '../ui/pagehead.js';
import { filterRow, seg, selectCtl } from '../ui/pagefilters.js';
import { adCard, lightbox, bars, link, safeHttp, tint } from './competitors_cards.js';
import { TAG_PARAMS, tagFilters, learning, lpChanges, lpCards, lpLightbox } from './competitors_learn.js';

const md = (s) => { const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(s || ''); return m ? `${+m[2]}/${+m[3]}` : (s || ''); };
const weekLabel = (s) => 'Week of ' + md(s);
const num = (v) => (isNum(v) ? fmt.int ? fmt.int(v) : String(v) : '–');
const signed = (v) => (isNum(v) ? (v > 0 ? '+' : '') + v : '–');
const pct = (v) => (isNum(v) ? Math.round(v * 100) + '%' : '–');
const FORMAT_NAMES = { video: 'Video', image: 'Image' };


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
    d.landing_pages.length && d.landing_pages[0].lp_id ? lpCards(d.landing_pages, st.openLp) : d.landing_pages.length ? plainTable(['Page', 'Ads'], d.landing_pages.map((p) => h('tr', null, h('td', null, p.page), h('td', { class: 'comp-n' }, String(p.ads)))), 'comp-pages') : h('p', { class: 'pri-sub' }, 'No landing pages read.'), 'comp-sec-pages'));
  if (d.chatter && d.chatter.length) parts.push(section('Chatter about them', null, chatterCards(d.chatter, 'comp-bchatter'), 'comp-sec-bchatter'));
  return h('div', { class: 'comp-detail' }, parts);
}

const ageText = (iso) => {
  if (!iso) return '';
  const d = Math.floor((Date.now() - new Date(iso).getTime()) / 86400000);
  return d <= 0 ? 'today' : d === 1 ? '1 day ago' : d + ' days ago';
};
function chatterCards(rows, testid) {
  return h('ul', { class: 'comp-news', 'data-testid': testid || 'comp-chatter' }, rows.map((p) => {
    const u = safeHttp(p.url);
    return h('li', { class: 'comp-newscard', 'data-testid': 'comp-newscard' },
      h('span', { class: 'comp-news-src' }, [p.source === 'reddit' ? 'Reddit' : (p.source || ''), p.subreddit].filter(Boolean).join(' / ')),
      u ? link(u, p.title || u, { class: 'comp-news-title' }) : h('span', { class: 'comp-news-title' }, p.title || ''),
      h('span', { class: 'comp-news-foot' }, p.brand ? h('span', { class: 'comp-tag' }, p.brand) : null, ageText(p.posted_at)));
  }));
}

const TILE_SORTS = [['active_ads', 'Most ads'], ['new_ads', 'Most new this week'], ['max_days_running', 'Longest running'], ['video_share', 'Most video']];
const initials = (s) => String(s || '?').trim().slice(0, 1).toUpperCase();

function angleRows(list) {
  return list.map((g) => ({ label: g.pain_point || g.label || g.cluster || '–', sub: g.is_new_angle ? 'Lonvera does not run this' : (g.cluster && g.cluster !== (g.pain_point || g.label) ? g.cluster : ''), value: g.ads, hl: !!g.is_new_angle,
    title: `${g.ads} ads${g.brands ? ' from ' + g.brands + ' brand' + (g.brands === 1 ? '' : 's') : ''}` }));
}

export async function competitorsPage(ctx) {
  showControls(false);
  const st = { week: ctx.params.week || '', format: FORMAT_NAMES[ctx.params.format] ? ctx.params.format : '', view: ctx.params.view === 'table' ? 'table' : 'wall', style: ctx.params.style || '', hook: ctx.params.hook || '', device: ctx.params.device || '', offer: ctx.params.offer || '', sort: 'active_ads', dir: -1, tileSort: 'active_ads' };
  let data = { weeks: [], brands: [], changes: [] };
  const open = new Set();
  let openBrand = '';
  const cache = new Map();
  const body = h('div', { class: 'comp-body' });
  const msg = h('p', { class: 'prod-msg', role: 'status', 'data-testid': 'comp-msg' });
  const lb = lightbox();
  const lplb = lpLightbox();
  const root = h('div', { class: 'page pri-page comp-page', 'data-testid': 'comp-page' }, body, lb, lplb);
  const openAd = (list) => (ad) => lb.openAt(list, ad);
  st.openLp = (lp, list) => lplb.openAt(list || [lp], lp);
  const tagHref = (f, v) => href('/competitors', { ...params(), [f]: v });
  const setTag = (f, v) => { st[f] = v; refresh(); };
  lb.handlers = {
    tagHref,
    onTag: (f, v) => { lb.close(); setTag(f, v); },
    onLp: async (ref, ad) => { try { const d = await detailFor(ad.brand); const list = (d.landing_pages || []).filter((x) => x.lp_id); const lp = list.find((x) => x.lp_id === ref.lp_id); if (lp) { lb.close(); lplb.openAt(list, lp); } } catch (e) { /* the summary is only a shortcut */ } },
  };

  const tagArgs = () => ({ style: st.style, hook: st.hook, device: st.device, offer: st.offer });
  const params = () => ({ week: st.week, format: st.format, view: st.view === 'table' ? 'table' : '', ...tagArgs() });
  const load = async () => { data = await api('/api/competitors', { week: st.week, format: st.format, ...tagArgs() }, { signal: ctx.signal }); if (!st.week && data.week) st.week = ''; draw(); };
  const refresh = () => { open.clear(); openBrand = ''; cache.clear(); navigate(href('/competitors', params()), { replace: true, render: false }); load().catch((e) => { if (!(e && e.name === 'AbortError')) { msg.textContent = e.message || 'Could not load.'; msg.classList.add('pri-err'); } }); };

  async function detailFor(brand) {
    const k = [brand, data.week, st.format, ...TAG_PARAMS.map((x) => st[x])].join('|');
    if (!cache.has(k)) cache.set(k, api('/api/competitors/' + encodeURIComponent(brand), { week: data.week, format: st.format, ...tagArgs() }, { signal: ctx.signal }));
    return cache.get(k);
  }
  async function fillDetail(cell, brand, panel) {
    cell.replaceChildren(h('p', { class: 'pri-sub' }, 'Loading...'));
    try { const d = await detailFor(brand); cell.replaceChildren(panel(d)); } catch (e) { if (e && e.name === 'AbortError') return; cell.replaceChildren(h('p', { class: 'pri-err' }, e.message || 'Could not load this brand.')); }
  }

  // ---------- table view (the old sortable table, unchanged) ----------
  function brandRows(r) {
    const isOpen = open.has(r.brand);
    const cell = h('td', { colspan: String(COLS.length), class: 'comp-xcell', 'data-testid': 'comp-detail' });
    const detail = h('tr', { class: 'comp-detailrow', hidden: !isOpen, 'data-brand': r.brand }, cell);
    const toggle = () => {
      if (open.has(r.brand)) { open.delete(r.brand); detail.hidden = true; main.setAttribute('aria-expanded', 'false'); return; }
      open.add(r.brand); detail.hidden = false; main.setAttribute('aria-expanded', 'true'); fillDetail(cell, r.brand, (d) => detailPanel(d, st));
    };
    const main = h('tr', { class: 'comp-row', 'data-testid': 'comp-row', 'data-brand': r.brand, 'aria-expanded': String(isOpen), onclick: (e) => { if (e.target.closest('a')) return; toggle(); } },
      COLS.map(([key]) => {
        if (key === 'brand') return h('td', { class: 'comp-c' }, h('button', { type: 'button', class: 'comp-name', 'data-testid': 'comp-name', 'aria-label': 'Show details for ' + r.brand, onclick: (e) => { e.stopPropagation(); toggle(); } }, r.brand));
        if (key === 'is_stale') return h('td', { class: 'comp-c', 'data-testid': 'comp-status' }, r.is_stale ? h('span', { class: 'comp-stale', title: r.last_ok_at ? 'Last read OK ' + md(r.last_ok_at) : '' }, 'Stale' + (r.last_ok_at ? ' (read ' + md(r.last_ok_at) + ')' : '')) : 'Read');
        const f = CELL[key];
        return h('td', { class: 'comp-c' + (f ? ' comp-n' : ''), 'data-col': key }, f ? f(r) : (r[key] || '–'));
      }));
    if (isOpen) fillDetail(cell, r.brand, (d) => detailPanel(d, st));
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

  function tableView() {
    const head = h('tr', null, COLS.map(([key, label, tip]) => {
      const on = st.sort === key;
      return h('th', { scope: 'col', title: tip, 'aria-sort': on ? (st.dir > 0 ? 'ascending' : 'descending') : 'none' },
        h('button', { type: 'button', class: 'comp-sort', 'data-testid': 'comp-sort-' + key, onclick: () => { if (st.sort === key) st.dir = -st.dir; else { st.sort = key; st.dir = ['brand', 'product_type', 'top_landing_domain'].includes(key) ? 1 : -1; } draw(); } },
          label + (on ? (st.dir > 0 ? ' ▲' : ' ▼') : '')));
    }));
    return h('div', { class: 'comp-scroll', 'data-testid': 'comp-scroll' }, h('table', { class: 'pri-table comp-table', 'data-testid': 'comp-table' }, h('thead', null, head), h('tbody', null, sorted().flatMap(brandRows))));
  }

  // ---------- wall view ----------
  function pulse() {
    const p = data.pulse || {};
    const cell = (id, n, label, sub) => h('div', { class: 'comp-pcell', 'data-testid': 'comp-pulse-' + id }, h('strong', null, n), h('span', null, label), sub ? h('small', null, sub) : null);
    return h('section', { class: 'comp-pulse', 'data-testid': 'comp-pulse', 'aria-label': 'This week at a glance' },
      cell('brands', num(p.brands), 'brands watched'), cell('active', num(p.active_ads), 'ads running now'),
      cell('new', isNum(p.new_ads) ? num(p.new_ads) : '–', 'new this week', isNum(p.new_ads) ? '' : 'starts next week'),
      cell('longest', isNum(p.longest_days) ? num(p.longest_days) + ' days' : '–', 'longest-running ad', p.longest_brand || ''));
  }

  function spotlight() {
    const list = data.spotlight || [];
    if (!list.length) return null;
    const rail = h('div', { class: 'comp-rail', 'data-testid': 'comp-rail', tabindex: '0', role: 'region', 'aria-label': 'Worth a look: ads to study' }, list.map((a) => adCard(a, openAd(list), { rail: true, testid: 'comp-spot' })));
    const step = (d) => rail.scrollBy({ left: d * Math.max(240, rail.clientWidth * 0.8), behavior: 'smooth' });
    return h('section', { class: 'comp-sec comp-spotlight', 'data-testid': 'comp-spotlight' },
      h('div', { class: 'comp-sechead' }, h('h2', null, 'Worth a look'),
        h('span', { class: 'comp-railnav' },
          h('button', { type: 'button', 'aria-label': 'Scroll back', 'data-testid': 'comp-rail-prev', onclick: () => step(-1) }, '‹'),
          h('button', { type: 'button', 'aria-label': 'Scroll forward', 'data-testid': 'comp-rail-next', onclick: () => step(1) }, '›'))),
      h('p', { class: 'pri-sub' }, 'Ads kept on the longest, ads pushing an angle we do not run, and new ads from brands that just ramped up.'), rail);
  }

  function tile(r) {
    const on = openBrand === r.brand;
    const split = isNum(r.video_share) ? r.video_share : null;
    const ch = r.change;
    const thumbs = (r.thumbs || []).map((t) => {
      const box = tint(h('span', { class: 'comp-mini' + (t.thumb_url ? ' comp-has-img' : ' comp-mini-t'), 'data-testid': t.thumb_url ? 'comp-mini-img' : 'comp-mini-typo' }), r.brand);
      if (t.thumb_url) box.append(h('img', { src: t.thumb_url, alt: '', loading: 'lazy' })); else box.append(h('span', null, (t.hook || '').slice(0, 44) || initials(r.brand)));
      return box;
    });
    const ta = r.top_angle;
    return h('article', { class: 'comp-tile' + (on ? ' comp-tile-on' : '') + (r.is_stale ? ' comp-tile-stale' : ''), 'data-testid': 'comp-tile', 'data-brand': r.brand },
      h('button', { type: 'button', class: 'comp-tile-btn', 'aria-expanded': String(on), 'aria-label': 'Show ads from ' + r.brand, 'data-testid': 'comp-tile-open', onclick: () => { openBrand = on ? '' : r.brand; draw(); } },
        h('span', { class: 'comp-tile-top' }, h('span', { class: 'comp-tile-name' }, r.brand), r.is_stale ? h('span', { class: 'comp-stale', title: 'Could not be read this week; numbers are from an earlier week' }, 'Stale') : null),
        h('span', { class: 'comp-tile-sells' }, r.product_type || ' '),
        h('span', { class: 'comp-tile-count' }, h('strong', { 'data-testid': 'comp-tile-ads' }, num(r.active_ads)), h('span', null, ' ads running'),
          isNum(ch) ? h('span', { class: 'comp-delta ' + (ch > 0 ? 'up' : ch < 0 ? 'down' : 'flat'), 'data-testid': 'comp-tile-change', title: 'Against the week before' }, (ch > 0 ? '▲ ' : ch < 0 ? '▼ ' : '') + signed(ch) + ' vs last week') : null),
        thumbs.length ? h('span', { class: 'comp-minis' }, thumbs) : h('span', { class: 'comp-minis comp-minis-empty' }, 'No ads in this view'),
        split === null ? null : h('span', { class: 'comp-split', title: pct(split) + ' video, ' + pct(1 - split) + ' image' },
          h('span', { class: 'comp-split-bar' }, h('span', { class: 'comp-split-vid', style: { width: Math.round(split * 100) + '%' } })), h('small', null, pct(split) + ' video')),
        ta ? h('span', { class: 'comp-chip' + (ta.is_new_angle ? ' comp-chip-hl' : ''), 'data-testid': 'comp-tile-angle', title: ta.is_new_angle ? 'An angle Lonvera does not run' : 'Most common angle' }, ta.label) : null));
  }

  const MIX = [['style', 'style', 'Style'], ['hook_type', 'hook', 'Hook type'], ['device', 'device', 'Device']];
  function tagMix(mix) {
    if (!mix) return null;
    return h('div', { class: 'comp-mix', 'data-testid': 'comp-mix' }, MIX.filter(([k]) => (mix[k] || []).length).map(([k, p, label]) => h('p', { class: 'comp-mixrow', 'data-testid': 'comp-mixrow', 'data-key': k },
      h('span', { class: 'comp-mixlab' }, label), (mix[k] || []).map((o) => h('a', { class: 'comp-tchip', href: tagHref(p, o.value), 'data-testid': 'comp-mixchip', title: 'Show only ads with this ' + label.toLowerCase(),
        onclick: (e) => { e.preventDefault(); setTag(p, o.value); } }, o.value, h('b', null, ' ' + o.count))))));
  }

  function brandPanel(d) {
    const bl = safeHttp(d.brand_ad_library_url);
    const g = d.gallery || [];
    const second = [];
    const classified = (d.angles || []).filter((a) => a.cluster !== 'Not classified yet');
    if (classified.length) second.push(h('div', { class: 'comp-half' }, h('h4', null, 'Angles they push'),
      bars(angleRows(classified), 'comp-bangles'),
      h('p', { class: 'pri-sub' }, 'Highlighted: angles Lonvera does not run.')));
    const lpc = (d.landing_pages || []).length && d.landing_pages[0].lp_id;
    if (d.landing_pages && d.landing_pages.length && !lpc) second.push(h('div', { class: 'comp-half' }, h('h4', null, 'Landing pages'),
      h('ul', { class: 'comp-pagelist', 'data-testid': 'comp-bpages' }, d.landing_pages.map((p) => h('li', null, h('span', null, p.page), h('b', null, String(p.ads)))))));
    return h('div', { class: 'comp-bpanel', 'data-testid': 'comp-bpanel', 'data-brand': d.brand },
      h('div', { class: 'comp-bhead' }, h('h3', null, d.brand), d.product_type ? h('span', { class: 'pri-sub' }, d.product_type) : null,
        bl ? link(bl, 'Search ' + d.brand + ' in the Ad Library', { 'data-testid': 'comp-brandlink' }) : null,
        h('button', { type: 'button', class: 'comp-bclose', 'data-testid': 'comp-bclose', onclick: () => { openBrand = ''; draw(); } }, 'Close'),
        d.format !== 'all' ? h('span', { class: 'pri-sub' }, `Showing ${d.format} ads only.`) : null),
      tagMix(d.tag_mix),
      g.length ? h('div', { class: 'comp-gallery', 'data-testid': 'comp-gallery' }, g.map((a) => adCard(a, openAd(g), { testid: 'comp-gcard' }))) : h('p', { class: 'pri-sub', 'data-testid': 'comp-gempty' }, 'No ads in this view.'),
      d.first_snapshot ? h('p', { class: 'pri-sub', 'data-testid': 'comp-first' }, 'First snapshot: new ads show from next week.') : null,
      second.length ? h('div', { class: 'comp-halves' }, second) : null,
      lpc ? h('div', { 'data-testid': 'comp-blps' }, h('h4', null, 'Landing pages'), lpCards(d.landing_pages, st.openLp)) : null,
      d.chatter && d.chatter.length ? h('div', null, h('h4', null, 'Chatter about them'), chatterCards(d.chatter, 'comp-bchatter')) : null);
  }

  function wall() {
    const list = data.brands.slice().sort((a, b) => {
      const k = st.tileSort, x = a[k], y = b[k];
      if (x === null || x === undefined) return (y === null || y === undefined) ? a.brand.localeCompare(b.brand) : 1;
      if (y === null || y === undefined) return -1;
      return (y - x) || a.brand.localeCompare(b.brand);
    });
    const sel = h('select', { 'aria-label': 'Sort brands', 'data-testid': 'comp-tilesort', onchange: () => { st.tileSort = sel.value; draw(); } },
      TILE_SORTS.map(([v, t]) => h('option', { value: v, selected: v === st.tileSort }, t)));
    const items = [];
    for (const r of list) {
      items.push(tile(r));
      if (openBrand === r.brand) {
        const cell = h('section', { class: 'comp-bwrap', 'data-testid': 'comp-bwrap', 'data-brand': r.brand, 'aria-label': r.brand + ' ads' });
        items.push(cell);
        fillDetail(cell, r.brand, brandPanel);
      }
    }
    return h('section', { class: 'comp-sec', 'data-testid': 'comp-wall-sec' },
      h('div', { class: 'comp-sechead' }, h('h2', null, 'Brands'), h('label', { class: 'comp-sortlbl' }, 'Sort ', sel)),
      h('div', { class: 'comp-wall', 'data-testid': 'comp-wall' }, items));
  }

  function anglesAcross() {
    const a = data.angle_chart || [];
    if (!a.length) return null;
    return h('section', { class: 'comp-sec', 'data-testid': 'comp-anglechart-sec' }, h('h2', null, 'Angles across competitors'),
      h('p', { class: 'pri-sub' }, 'The ten messages competitors lead with most, by number of ads. Highlighted bars are angles Lonvera does not run yet.'),
      bars(angleRows(a), 'comp-anglechart', 'ads'));
  }

  function draw() {
    const weeks = data.weeks || [];
    const controls = filterRow(
      selectCtl({ name: 'Week', options: weeks.length ? weeks.map((w) => [w, weekLabel(w)]) : [['', 'No snapshots']], value: data.week || '', onChange: (v) => { st.week = v; refresh(); }, testid: 'comp-week' }),
      seg({ name: 'Format', options: [['', 'All', 'Video and image ads', 'comp-format-all'], ['video', 'Video', 'Video ads only', 'comp-format-video'], ['image', 'Image', 'Image ads only (carousels count as image)', 'comp-format-image']],
        value: st.format, onChange: (v) => { st.format = v; refresh(); }, testid: 'comp-format' }),
      seg({ name: 'View', options: [['wall', 'Brand wall', 'Tiles with pictures', 'comp-view-wall'], ['table', 'Table', 'One sortable row per brand', 'comp-view-table']], value: st.view, onChange: (v) => { st.view = v; refresh(); }, testid: 'comp-view' }));
    const lpc = lpChanges(data);
    const changes = h('details', { class: 'comp-changes', 'data-testid': 'comp-changes' }, h('summary', null, 'What changed this week'),
      data.changes && data.changes.length ? h('ul', null, data.changes.map((c) => h('li', { 'data-kind': c.kind, 'data-testid': 'comp-fact' }, c.text, ' ', c.ad_library_url && safeHttp(c.ad_library_url) ? link(c.ad_library_url, 'Open ad', { 'data-testid': 'comp-factlink' }) : null))) : (data.week && !lpc ? h('p', { class: 'pri-sub' }, 'Nothing stands out.') : null), lpc);
    const tags = tagFilters(data, st, setTag, () => { for (const k of TAG_PARAMS) st[k] = ''; refresh(); });
    const notice = data.notice ? h('p', { class: 'pri-sub', 'data-testid': 'comp-notice' }, data.notice) : null;
    let main;
    if (!data.week) main = h('p', { 'data-testid': 'comp-empty' }, data.notice || 'No snapshots yet.');
    else if (!data.brands.length) main = h('p', { 'data-testid': 'comp-empty' }, 'No brands in this view.');
    else if (st.view === 'table') main = [changes, tableView(), learning(data), anglesAcross()];
    else main = [pulse(), changes, spotlight(), wall(), learning(data), anglesAcross()];
    const chatter = data.chatter ? h('section', { class: 'comp-sec', 'data-testid': 'comp-chatter-sec' }, h('h2', null, 'News and Reddit'), data.chatter.length ? chatterCards(data.chatter, 'comp-chatter') : h('p', { class: 'pri-sub' }, 'No posts stored for this week.')) : null;
    body.replaceChildren(...[
      pageHeader({ title: 'Competitors', description: 'What competitors run on Meta, week by week, from the public Ad Library. Brands show their ads running now, not spend or results.', testid: 'comp-head' }),
      h('p', { class: 'pri-sub comp-note', 'data-testid': 'comp-note' }, 'Each brand is a top slice of its ads by reach, so new and stopped counts also move with the slice. Format is video or image only: carousels count as image.'),
      controls, tags, msg, notice, h('div', { class: 'comp-main' }, main), chatter].filter(Boolean));
  }

  await load();
  ctx.mount(root);
  document.title = 'Competitors · Lonvera';
}
