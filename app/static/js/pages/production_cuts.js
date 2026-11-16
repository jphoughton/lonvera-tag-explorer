// Creative Pipeline "By cut" view: production concepts counted by status next to the spend and return behind the same cuts, for a chosen date window (GET /api/priorities/cuts).
// A cut is any ordered subset of Product, Market cluster, ICP (ideal customer profile), Pain point and Vendor. Drag the chips between "Available" and "Group by", and left or right inside "Group by", to rebuild the table.
// Without a mouse: Enter or Space on a chip moves it between the two boxes, the left and right arrows reorder it inside Group by.
// Everything from the API goes in through textContent / h(). Class names and test ids start with `prod-` (never ad-, ads-, sponsor, banner, promo: ad blockers hide those).
import { h, fmt, isNum, href } from '../util.js';
import { openModal } from '../modal.js';
import { multiCtl } from '../ui/pagefilters.js';
import { api } from '../api.js';
import { today, addDays, mondayOf, weekLabel, toast } from './production_common.js';

export const DIMS = [['product', 'Product'], ['cluster', 'Market cluster'], ['icp', 'ICP'], ['pain_point', 'Pain point'], ['vendor', 'Vendor'], ['media', 'Image / video']];
const DIM_LABEL = Object.fromEntries(DIMS);
const STAGE_FALLBACK = [['assigned', 'Assigned'], ['brief_sent', 'Brief sent'], ['in_production', 'In production'], ['in_review', 'In review'], ['ready', 'Ready to launch'], ['launched', 'Launched']];
const PRESETS = [['7', 'Last 7 days'], ['14', 'Last 14 days'], ['30', 'Last 30 days'], ['90', 'Last 90 days'], ['mtd', 'This month'], ['lastmonth', 'Last month']];
export const METRICS = [
  ['live_ads', 'Live concepts', fmt.int, 'Unique ads (copies counted once) that spent in the last two loaded days, whatever the dates chosen'], ['ads_spending', 'Concepts with spend', fmt.int, 'Unique ads that spent anything inside the chosen dates'],
  ['spend', 'Spend', fmt.money, 'Meta spend on ads tagged to the cut'], ['nc_revenue', 'NC revenue', fmt.money, 'Upstack new-customer revenue (first orders only)'],
  ['nc_roas', 'NC ROAS', (v) => (isNum(v) ? fmt.roas(v) + 'x' : '–'), 'New-customer revenue / spend (Upstack)'], ['nc_cpa', 'NC CPA', fmt.money2, 'Spend per new customer (Upstack)'],
  ['meta_roas', 'Meta ROAS', (v) => (isNum(v) ? fmt.roas(v) + 'x' : '–'), 'Meta-reported revenue / spend; counts returning customers'], ['cpm', 'CPM', fmt.money2, 'Spend per 1,000 impressions'],
  ['ctr', 'CTR', (v) => (isNum(v) ? (v * 100).toFixed(2) + '%' : '–'), 'Link clicks / impressions'], ['cplc', 'Cost per link click', fmt.money2, 'Spend / link clicks'],
  ['pipeline_share', '% of concepts in pipeline', (v) => (isNum(v) ? (v * 100).toFixed(1) + '%' : '–'), 'This cut\'s share of all concepts in the pipeline (assigned through ready to launch)'], ['spend_share', '% of spend', (v) => (isNum(v) ? (v * 100).toFixed(1) + '%' : '–'), 'This cut\'s share of all spend in the window (the Total row)'], ['rev_share', '% of NC revenue', (v) => (isNum(v) ? (v * 100).toFixed(1) + '%' : '–'), 'This cut\'s share of all new-customer revenue in the window'],
  ['conv_rate', 'Conv rate', (v) => (isNum(v) ? (v * 100).toFixed(2) + '%' : '–'), 'New customers / link clicks'], ['aov', 'AOV', fmt.money2, 'Average order value of new customers'],
];
// Quick filters: each is a plain rule over a cut's row. BASE is the blended NC ROAS of every cut in the window (the Total row); "working" = at or above it with at least MIN_SPEND spent.
// "In pipeline" = concepts not yet launched (assigned through ready to launch).
export const MIN_SPEND = 1000;
const OPEN = ['assigned', 'brief_sent', 'in_production', 'in_review', 'ready'];
export const openCount = (r) => OPEN.reduce((n, k) => n + (r.stages[k] || 0), 0);
const working = (r, base) => (r.stats.spend || 0) >= MIN_SPEND && isNum(r.stats.nc_roas) && isNum(base) && r.stats.nc_roas >= base;
const QV_COLOR = { stuck: 'pri-cc-sec-faded', gap: 'pri-cc-sec-iterate', scale: 'pri-cc-sec-new', ready: 'pri-cc-sec-proposal', dark: 'pri-cc-sec-stop' };
export const QUICK = [
  ['', 'All cuts', 'Every cut', () => true],
  ['stuck', 'Lots in pipeline, not working', `3 or more concepts in the pipeline, at least $${MIN_SPEND.toLocaleString('en-US')} spent, and NC ROAS below the blended NC ROAS of all cuts in the window`, (r, base) => openCount(r) >= 3 && (r.stats.spend || 0) >= MIN_SPEND && isNum(r.stats.nc_roas) && isNum(base) && r.stats.nc_roas < base],
  ['gap', 'Working, nothing in pipeline', 'NC ROAS at or above blended with at least $1,000 spent, and no concepts in the pipeline', (r, base) => working(r, base) && openCount(r) === 0],
  ['scale', 'Working, 2 or fewer live ads', 'NC ROAS at or above blended with at least $1,000 spent, and 2 or fewer unique live ads', (r, base) => working(r, base) && (r.stats.live_ads || 0) <= 2],
  ['ready', 'Ready to launch, none live', 'At least one concept is ready to launch and no unique ad is live', (r) => (r.stages.ready || 0) > 0 && !(r.stats.live_ads > 0)],
  ['dark', 'Winners gone dark', 'NC ROAS at or above blended with at least $1,000 spent in the window, but no unique ad live now', (r, base) => working(r, base) && !(r.stats.live_ads > 0)],
];
const PRODUCT_SLUG = { 'Vita Bull': 'vita-bull', 'Beef Liver': 'beef-liver', Boneflex: 'boneflex', 'Vita Belle': 'vita-belle' };
export const ADS_MIN_SPEND = 250;
const roasText = (v) => (isNum(v) ? fmt.roas(v) + 'x' : '–');
const iso = (d) => (typeof d === 'string' ? d : d.toISOString().slice(0, 10));
const dateOf = (s) => new Date(s + 'T12:00:00Z');

export function presetRange(key, now = today()) {
  const yest = addDays(iso(now), -1);
  if (key === 'mtd') { const first = yest.slice(0, 8) + '01'; return { from: first, to: yest }; }
  if (key === 'lastmonth') {
    const d = dateOf(yest.slice(0, 8) + '01'); d.setUTCDate(0);
    const last = iso(d); return { from: last.slice(0, 8) + '01', to: last };
  }
  const n = Number(key) || 30;
  return { from: addDays(yest, -(n - 1)), to: yest };
}

// st: { by: ['product', ...], preset, from, to, pipelineOnly, stages: [status...], sort: {key, dir} }  (plain object kept by the page so it survives a redraw)
export function cutsView({ host, st, filters, signal, onState, say }) {
  let out = null;
  let metricOpen = false;
  const sel = new Map();                                         // rows ticked for Needs brief: key = the cut's labels, value = the row
  const rowKey = (r) => JSON.stringify(out.group.map((d) => r.labels[d]));
  let addWeek = mondayOf(today());
  let addMedia = '';                                            // image or video for the ticked cuts when Image / video is not one of the grouped cuts
  let seq = 0;
  const builder = h('div', { class: 'prod-cuts-builder', 'data-testid': 'prod-cuts-builder' });
  const when = h('div', { class: 'prod-cuts-when', 'data-testid': 'prod-cuts-when' });
  const filterHost = h('div', { class: 'ctl-row prod-cuts-filters', 'data-testid': 'prod-cuts-filters' });
  const tableHost = h('div', { class: 'prod-cuts-out', 'data-testid': 'prod-cuts-out' });
  const live = h('p', { class: 'pri-sub prod-cuts-live', role: 'status', 'data-testid': 'prod-cuts-live' });
  host.replaceChildren(builder, when, filterHost, tableHost, live);
  let combos = [];                                               // every (product slug, cluster, ICP, pain point) the data knows, for the filter lists

  const changed = () => { if (onState) onState(); };
  const rebuild = (by) => { st.by = by.length ? by : st.by; drawBuilder(); changed(); fetchNow(); };

  // ---- drag and drop of the cut chips
  function chip(key, zone) {
    const idx = st.by.indexOf(key);
    const el = h('button', { type: 'button', class: 'prod-cutchip', 'data-key': key, 'data-zone': zone, 'data-testid': 'prod-chip-' + key, draggable: 'false',
      title: zone === 'on' ? 'Drag left or right to reorder, or drag to Available to remove. Enter removes it.' : 'Drag into Group by to add it. Enter adds it.',
      'aria-label': zone === 'on' ? `${DIM_LABEL[key]}, grouping level ${idx + 1} of ${st.by.length}. Left and right arrows reorder, Enter removes.` : `${DIM_LABEL[key]}. Enter adds it to Group by.` },
      zone === 'on' ? h('span', { class: 'prod-cutchip-n', 'aria-hidden': 'true' }, String(idx + 1)) : null, h('span', { class: 'prod-grip', 'aria-hidden': 'true' }), DIM_LABEL[key]);
    el.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(key); return; }
      if (zone === 'on' && (e.key === 'ArrowLeft' || e.key === 'ArrowRight')) {
        e.preventDefault();
        const i = st.by.indexOf(key), j = i + (e.key === 'ArrowLeft' ? -1 : 1);
        if (j < 0 || j >= st.by.length) return;
        const by = st.by.slice(); [by[i], by[j]] = [by[j], by[i]];
        rebuild(by); builder.querySelector(`.prod-cutchip[data-key="${key}"]`)?.focus();
        live.textContent = `${DIM_LABEL[key]} is now level ${j + 1}.`;
      }
    });
    el.addEventListener('pointerdown', (e) => startDrag(e, el, key, zone));
    return el;
  }
  function toggle(key) {
    if (st.by.includes(key)) { if (st.by.length > 1) rebuild(st.by.filter((k) => k !== key)); else live.textContent = 'At least one cut must stay in Group by.'; }
    else rebuild([...st.by, key]);
    builder.querySelector(`.prod-cutchip[data-key="${key}"]`)?.focus();
  }
  function startDrag(e, el, key, zone) {
    if (e.button !== 0) return;
    const x0 = e.clientX, y0 = e.clientY;
    let ghost = null, moved = false;
    const onZone = () => builder.querySelector('[data-zone-box="on"]'), offZone = () => builder.querySelector('[data-zone-box="off"]');
    const hit = (x, y) => { for (const z of [onZone(), offZone()]) { const r = z.getBoundingClientRect(); if (x >= r.left && x <= r.right && y >= r.top - 8 && y <= r.bottom + 8) return z.dataset.zoneBox; } return ''; };
    const insertAt = (x) => { const chips = [...onZone().querySelectorAll('.prod-cutchip')].filter((c) => c !== el); let n = 0; for (const c of chips) { const r = c.getBoundingClientRect(); if (x > r.left + r.width / 2) n++; } return n; };
    const move = (ev) => {
      if (!moved && Math.hypot(ev.clientX - x0, ev.clientY - y0) < 6) return;
      if (!moved) {
        moved = true; el.classList.add('prod-cutchip-drag');
        ghost = el.cloneNode(true); ghost.classList.add('prod-cutchip-ghost'); ghost.removeAttribute('data-testid'); ghost.setAttribute('aria-hidden', 'true'); document.body.append(ghost);
      }
      ev.preventDefault();
      ghost.style.left = ev.clientX + 8 + 'px'; ghost.style.top = ev.clientY + 8 + 'px';
      const z = hit(ev.clientX, ev.clientY);
      onZone().classList.toggle('prod-over', z === 'on'); offZone().classList.toggle('prod-over', z === 'off');
      const mark = onZone().querySelectorAll('.prod-cutchip-drop'); mark.forEach((m) => m.remove());
      if (z === 'on') {
        const chips = [...onZone().querySelectorAll('.prod-cutchip')].filter((c) => c !== el), n = insertAt(ev.clientX);
        const bar = h('span', { class: 'prod-cutchip-drop', 'aria-hidden': 'true' });
        if (chips[n]) onZone().insertBefore(bar, chips[n]); else onZone().append(bar);
      }
    };
    const end = (ev, commit) => {
      document.removeEventListener('pointermove', move); document.removeEventListener('pointerup', up); document.removeEventListener('pointercancel', cancel);
      if (ghost) ghost.remove();
      onZone().classList.remove('prod-over'); offZone().classList.remove('prod-over');
      el.classList.remove('prod-cutchip-drag');
      if (!moved) return;                                                       // a plain click falls through to the keyboard path below
      if (el._suppress) el._suppress = false;
      if (!commit) { drawBuilder(); return; }
      const z = hit(ev.clientX, ev.clientY);
      const rest = st.by.filter((k) => k !== key);
      if (z === 'on') { const n = insertAt(ev.clientX); rest.splice(n, 0, key); rebuild(rest); live.textContent = `${DIM_LABEL[key]} is now level ${rest.indexOf(key) + 1} of ${rest.length}.`; }
      else if (z === 'off') { if (!rest.length) { live.textContent = 'At least one cut must stay in Group by.'; drawBuilder(); } else { rebuild(rest); live.textContent = `${DIM_LABEL[key]} removed from Group by.`; } }
      else drawBuilder();
    };
    const up = (ev) => end(ev, true), cancel = (ev) => end(ev, false);
    document.addEventListener('pointermove', move, { passive: false }); document.addEventListener('pointerup', up); document.addEventListener('pointercancel', cancel);
    el.addEventListener('click', (ev) => { if (moved) { ev.preventDefault(); ev.stopImmediatePropagation(); } }, { once: true, capture: true });
  }
  function drawBuilder() {
    const off = DIMS.map(([k]) => k).filter((k) => !st.by.includes(k));
    builder.replaceChildren(
      h('div', { class: 'prod-cuts-zonewrap' }, h('span', { class: 'prod-cuts-zonelabel', id: 'prod-cuts-on-l' }, 'Group by'),
        h('div', { class: 'prod-cuts-zone prod-cuts-on', 'data-zone-box': 'on', 'data-testid': 'prod-cuts-on', role: 'group', 'aria-labelledby': 'prod-cuts-on-l' }, st.by.map((k) => chip(k, 'on')))),
      h('div', { class: 'prod-cuts-zonewrap' }, h('span', { class: 'prod-cuts-zonelabel', id: 'prod-cuts-off-l' }, 'Available'),
        h('div', { class: 'prod-cuts-zone prod-cuts-off', 'data-zone-box': 'off', 'data-testid': 'prod-cuts-off', role: 'group', 'aria-labelledby': 'prod-cuts-off-l' },
          off.length ? off.map((k) => chip(k, 'off')) : h('span', { class: 'pri-sub' }, 'Every cut is in use'))),
      h('p', { class: 'pri-sub prod-cuts-hint' }, 'Drag a cut into Group by (or out of it) and left or right to change the order. Rows are grouped by the first cut, then the second, and so on.'));
  }

  // ---- dates
  function drawWhen() {
    const presetBtns = PRESETS.map(([k, t]) => h('button', { type: 'button', class: 'seg', 'aria-pressed': String(st.preset === k), 'data-testid': 'prod-cuts-preset-' + k, onclick: () => { Object.assign(st, { preset: k }, presetRange(k)); drawWhen(); changed(); fetchNow(); } }, t));
    const date = (name, key, tid) => h('label', { class: 'ctl prod-cuts-date' }, h('span', { class: 'ctl-label' }, name),
      h('input', { type: 'date', value: st[key], max: iso(addDays(iso(today()), -0)), 'data-testid': tid, onchange: (e) => { if (!e.target.value) return; st[key] = e.target.value; st.preset = ''; if (st.from > st.to) { if (key === 'from') st.to = st.from; else st.from = st.to; } drawWhen(); changed(); fetchNow(); } }));
    when.replaceChildren(h('div', { class: 'ctl' }, h('span', { class: 'ctl-label' }, 'Performance dates'), h('span', { class: 'segs', role: 'group', 'aria-label': 'Performance dates' }, presetBtns)), date('From', 'from', 'prod-cuts-from'), date('To', 'to', 'prod-cuts-to'),
      h('label', { class: 'check prod-cuts-only' }, h('input', { type: 'checkbox', checked: st.pipelineOnly, 'data-testid': 'prod-cuts-pipeline-only', onchange: (e) => { st.pipelineOnly = e.target.checked; changed(); drawTable(); } }), ' Only cuts with concepts in the pipeline'));
  }

  // ---- the popup: every ad behind one row for the dates, and the five biggest spenders of the cut since launch (inspiration)
  async function openAds(r) {
    const cut = {};
    const ordered = out.group.map((d) => [d, r.labels[d]]);                      // the labels object arrives key-sorted: the cut order is out.group
    for (const [d, v] of ordered) cut[d] = d === 'product' ? (PRODUCT_SLUG[v] || v) : v;
    const body = h('div', { class: 'prod-cutads-body', 'data-testid': 'prod-cutads-body' }, h('p', { class: 'pri-sub' }, 'Loading ads…'));
    const facts = ordered.flatMap(([d, v]) => [h('dt', null, DIM_LABEL[d]), h('dd', null, v)]);
    openModal({ label: 'Ads for ' + ordered.map(([, v]) => v).join(', '), className: 'prod-cutads', build(box) {
      box.append(h('h3', { class: 'prod-cutads-title', 'data-testid': 'prod-cutads-title' }, 'Ads in this cut'), h('dl', { class: 'prod-cutads-cut', 'data-testid': 'prod-cutads-cut' }, facts),
        h('p', { class: 'pri-sub' }, `Dates ${st.from} to ${st.to}`), body); } });
    let d;
    try { d = await api('/api/priorities/cuts/ads', { ...cut, from: st.from, to: st.to }, { signal }); } catch (e) { if (e && e.name === 'AbortError') return; body.replaceChildren(h('p', { class: 'pri-err' }, e.message || 'Could not load the ads.')); return; }
    const name = (a) => h('span', { class: 'prod-cutads-name' }, a.ad_id ? h('a', { href: href('/ad/' + encodeURIComponent(a.ad_id), {}), target: '_blank', rel: 'noopener noreferrer', 'data-testid': 'prod-cutads-link' }, a.name) : a.name,
      a.story_url && /^https:\/\/(www\.)?(facebook|instagram)\.com\//.test(a.story_url) ? h('a', { class: 'pri-sub', href: a.story_url, target: '_blank', rel: 'noopener noreferrer' }, 'post') : null, a.live ? h('span', { class: 'prod-chip prod-chip-live', title: 'Spent in the last two loaded days' }, 'Live') : null);
    const table = (heads, rows, tid, none) => (rows.length ? h('div', { class: 'prod-scroll' }, h('table', { class: 'pri-table prod-table prod-cuts-table', 'data-testid': tid }, h('thead', null, h('tr', null, heads.map((x, i) => h('th', { scope: 'col', class: i ? 'prod-num' : null }, x)))),
      h('tbody', null, rows))) : h('p', { class: 'pri-sub' }, none || 'None.'));
    const tr = (cells) => h('tr', { 'data-testid': 'prod-cutads-row' }, cells.map((c, i) => h('td', { class: 'prod-c' + (i ? ' prod-num' : '') }, c)));
    let minOn = true;                                                           // view filter only: the data underneath is unchanged
    const winHost = h('div', { 'data-testid': 'prod-cutads-winhost' });
    const drawWindow = () => {
      const shown = minOn ? d.window.filter((a) => a.spend >= ADS_MIN_SPEND) : d.window;
      winHost.replaceChildren(
        h('div', { class: 'prod-cutads-bar' }, h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: minOn, 'data-testid': 'prod-cutads-min', onchange: (e) => { minOn = e.target.checked; drawWindow(); } }), ` Only ads that spent $${ADS_MIN_SPEND} or more`),
          h('span', { class: 'pri-sub', 'data-testid': 'prod-cutads-count' }, `Showing ${shown.length} of ${d.window.length} ads${d.window.length >= 60 ? ' (the 60 biggest spenders)' : ''}`)),
        table(['Ad', 'Spend', 'NC revenue', 'NC ROAS', 'NC CPA', 'Meta ROAS'], shown.map((a) => tr([name(a), fmt.money(a.spend), fmt.money(a.nc_revenue), roasText(a.nc_roas), fmt.money2(a.nc_cpa), roasText(a.meta_roas)])), 'prod-cutads-window', `No ad spent $${ADS_MIN_SPEND} or more in these dates.`));
    };
    body.replaceChildren(...[d.warning ? h('p', { class: 'pri-err' }, d.warning) : null,
      h('h4', { class: 'pri-h' }, 'Top 5 spenders since launch ', h('span', { class: 'pri-hnote' }, 'all time, Meta numbers, for inspiration')),
      table(['Ad', 'Spend', 'Revenue', 'Meta ROAS', 'Purchases'], d.top.map((a) => tr([name(a), fmt.money(a.spend), fmt.money(a.revenue), roasText(a.meta_roas), fmt.int(a.purchases)])), 'prod-cutads-top'),
      h('h4', { class: 'pri-h' }, 'Ads driving this row ', h('span', { class: 'pri-hnote' }, `spent between ${d.from} and ${d.to}`)), winHost].filter(Boolean));
    drawWindow();
  }

  // ---- filters on market cluster, ICP and pain point: the lists only offer values that exist together with the other choices (and the page's Product filter)
  const NONE = '(none)';
  const COMBO_DIM = { cluster: 1, icp: 2, pain_point: 3 };
  function optionsFor(dim) {
    const prods = (filters().product || '').split(',').filter(Boolean);
    const set = new Set(st.f[dim]);
    for (const c of combos) {
      if (prods.length && !prods.includes(c[0])) continue;
      if (Object.entries(COMBO_DIM).some(([d, i]) => d !== dim && st.f[d].length && !st.f[d].includes(c[i] || NONE))) continue;
      set.add(c[COMBO_DIM[dim]] || NONE);
    }
    return [...set].sort((a, b) => (a === NONE) - (b === NONE) || a.localeCompare(b));
  }
  function drawFilters() {
    const one = (dim, name, all, tid) => multiCtl({ name, allLabel: all, options: optionsFor(dim).map((v) => [v, v]), values: st.f[dim], testid: tid, onChange: (v) => { st.f[dim] = v; changed(); fetchNow(); } });
    filterHost.replaceChildren(...[one('cluster', 'Market cluster', 'All clusters', 'prod-cuts-f-cluster'), one('icp', 'ICP', 'All ICPs', 'prod-cuts-f-icp'), one('pain_point', 'Pain point', 'All pain points', 'prod-cuts-f-pain'),
      st.f.cluster.length + st.f.icp.length + st.f.pain_point.length ? h('button', { type: 'button', class: 'btn prod-small', 'data-testid': 'prod-cuts-f-clear', onclick: () => { st.f = { cluster: [], icp: [], pain_point: [] }; changed(); fetchNow(); } }, 'Clear filters') : null].filter(Boolean));
  }

  // ---- data and table
  async function fetchNow() {
    const mine = ++seq;
    tableHost.classList.add('prod-cuts-busy');
    try {
      const d = await api('/api/priorities/cuts', { group: st.by.join(','), from: st.from, to: st.to, week: filters().week, vendor: filters().vendor, product: filters().product, cluster: st.f.cluster.join('|'), icp: st.f.icp.join('|'), pain_point: st.f.pain_point.join('|') }, { signal });
      if (mine !== seq) return;
      out = d; say('');
      combos = d.combos || [];
      drawFilters();
      const T = d.totals.stats;
      const openTotal = openCount({ stages: d.totals.stages });
      for (const r of [...d.rows, { stats: T, stages: d.totals.stages }]) { r.stats.pipeline_share = openTotal ? openCount(r) / openTotal : null; r.stats.spend_share = T.spend ? r.stats.spend / T.spend : null; r.stats.rev_share = T.nc_revenue ? r.stats.nc_revenue / T.nc_revenue : null; }
      drawTable();
    } catch (e) {
      if (e && e.name === 'AbortError') throw e;
      if (mine !== seq) return;
      say(e.message || 'Could not load the cuts.', true);
    } finally { if (mine === seq) tableHost.classList.remove('prod-cuts-busy'); }
  }
  // ---- Needs brief: add the ticked cuts to the backlog for a week
  function selBar() {
    if (!sel.size) return h('p', { class: 'pri-sub prod-cuts-selhint', 'data-testid': 'prod-cuts-selhint' }, 'Tick cuts to add them to Needs brief for a week.');
    const weeks = [...new Set([...Array.from({ length: 6 }, (_, i) => addDays(mondayOf(today()), 7 * i)), addWeek])].sort();
    const msg = h('span', { class: 'pri-err', role: 'alert', 'data-testid': 'prod-cuts-selmsg' });
    const needMedia = !out.group.includes('media');
    return h('div', { class: 'prod-cuts-selbar', 'data-testid': 'prod-cuts-selbar' }, h('strong', null, `${sel.size} cut${sel.size === 1 ? '' : 's'} ticked`),
      h('label', { class: 'ctl' }, h('span', { class: 'ctl-label' }, 'For week'), h('select', { 'data-testid': 'prod-cuts-addweek', onchange: (e) => { addWeek = e.target.value; } }, weeks.map((w) => h('option', { value: w, selected: w === addWeek }, weekLabel(w))))),
      needMedia ? h('label', { class: 'ctl' }, h('span', { class: 'ctl-label' }, 'Image or video (required)'), h('select', { 'data-testid': 'prod-cuts-addmedia', onchange: (e) => { addMedia = e.target.value; } }, [['', 'Choose'], ['video', 'Video'], ['image', 'Image']].map(([v, l]) => h('option', { value: v, selected: v === addMedia }, l)))) : null,
      h('button', { type: 'button', class: 'btn primary small', 'data-testid': 'prod-cuts-addbl', onclick: async () => {
        const items = [...sel.values()].map((r) => { const it = {}; for (const d of out.group) { const v = r.labels[d]; if (v && v !== '(none)' && ['product', 'cluster', 'icp', 'pain_point', 'media'].includes(d)) { if (d === 'media') it.media_type = v.toLowerCase(); else it[d] = d === 'product' ? (PRODUCT_SLUG[v] || v) : v; } } return it; });
        msg.textContent = '';
        const lack = ['product', 'cluster', 'icp', 'pain_point'].filter((k) => !out.group.includes(k));
        if (lack.length) { msg.textContent = `Every item needs a product, market cluster, ICP, pain point and image or video. Group by ${lack.map((k) => DIM_LABEL[k]).join(', ')} too.`; return; }
        if (needMedia && !addMedia) { msg.textContent = 'Choose image or video for these cuts.'; return; }
        if (needMedia) for (const it of items) it.media_type = addMedia;
        const bad = items.filter((it) => !['product', 'cluster', 'icp', 'pain_point', 'media_type'].every((k) => it[k]));
        if (bad.length) { msg.textContent = `${bad.length} ticked cut${bad.length === 1 ? ' has' : 's have'} no product, market cluster, ICP, pain point or image / video yet (shown as none). Untick ${bad.length === 1 ? 'it' : 'them'} or fix the tags first.`; return; }
        try {
          const r = await api('/api/priorities/backlog', {}, { method: 'POST', body: { items, for_week: addWeek } });
          toast(`${r.created.length} added to Needs brief for ${weekLabel(addWeek).toLowerCase()}${r.skipped.length ? `, ${r.skipped.length} already there` : ''}.`);
          sel.clear(); drawTable();
        } catch (e) { if (e && e.name === 'AbortError') throw e; msg.textContent = e.message || 'Could not add.'; } } }, 'Add to Needs brief'),
      h('button', { type: 'button', class: 'btn small', 'data-testid': 'prod-cuts-selclear', onclick: () => { sel.clear(); drawTable(); } }, 'Clear'), msg);
  }
  const stageList = () => (out && out.stages ? out.stages.map((s) => [s.status, s.label]) : STAGE_FALLBACK);
  const val = (row, key) => (key.startsWith('label:') ? (row.labels[key.slice(6)] || '') : key.startsWith('stage:') ? row.stages[key.slice(6)] : key === 'open' ? openCount(row) : row.stats[key]);
  function sorted(rows) {
    const { key, dir } = st.sort;
    if (!key) return rows;
    const m = dir === 'asc' ? 1 : -1;
    return rows.slice().sort((a, b) => {
      const x = val(a, key), y = val(b, key);
      if (typeof x === 'string' || typeof y === 'string') return m * String(x ?? '').localeCompare(String(y ?? ''));
      if (!isNum(x) && !isNum(y)) return 0;
      if (!isNum(x)) return 1;                                                 // empty numbers always last
      if (!isNum(y)) return -1;
      return m * (x - y);
    });
  }
  function drawTable() {
    if (!out) { tableHost.replaceChildren(); return; }
    const stages = stageList();
    const shown = stages.filter(([k]) => st.stages.includes(k));
    const base = out.totals.stats.nc_roas;
    const quick = QUICK.find(([k]) => k === st.quick) || QUICK[0];
    const inScope = out.rows.filter((r) => !st.pipelineOnly || openCount(r) > 0);
    const rows = sorted(inScope.filter((r) => quick[3](r, base)));
    const metrics = METRICS.filter(([k]) => st.metrics.includes(k));
    const sortBtn = (key, text, title, right) => {
      const on = st.sort.key === key;
      return h('th', { scope: 'col', class: right ? 'prod-num' : null, title: title || null, 'aria-sort': on ? (st.sort.dir === 'asc' ? 'ascending' : 'descending') : 'none' },
        h('button', { type: 'button', class: 'prod-sortbtn', 'data-testid': 'prod-cuts-sort-' + key.replace(':', '-'), onclick: () => { st.sort = on ? { key, dir: st.sort.dir === 'desc' ? 'asc' : 'desc' } : { key, dir: typeof val(rows[0] || { labels: {}, stages: {}, stats: {} }, key) === 'string' ? 'asc' : 'desc' }; changed(); drawTable(); } }, text, on ? (st.sort.dir === 'asc' ? ' ▲' : ' ▼') : ''));
    };
    const heads = [h('th', { scope: 'col' }, h('input', { type: 'checkbox', 'aria-label': 'Select all shown cuts', 'data-testid': 'prod-cuts-selall', checked: rows.length > 0 && rows.every((r) => sel.has(rowKey(r))), onchange: (e) => { for (const r of rows) { if (e.target.checked) sel.set(rowKey(r), r); else sel.delete(rowKey(r)); } drawTable(); } })), ...out.group.map((d) => sortBtn('label:' + d, DIM_LABEL[d])), ...shown.map(([k, t]) => sortBtn('stage:' + k, t, 'Concepts at this status (not dropped)', true)), sortBtn('open', 'Concepts in pipeline', 'Concepts given out and not yet launched (assigned through ready to launch); dropped ones left out', true), ...metrics.map(([k, t, , tip]) => sortBtn(k, t, tip, true))];
    const cells = (r, total) => [h('td', { class: 'prod-c' }, total ? null : h('input', { type: 'checkbox', 'aria-label': 'Select this cut for Needs brief', 'data-testid': 'prod-cuts-sel', checked: sel.has(rowKey(r)), onchange: (e) => { if (e.target.checked) sel.set(rowKey(r), r); else sel.delete(rowKey(r)); drawTable(); } })), ...out.group.map((d, i) => h('td', { class: 'prod-c prod-cut-label' + (r.labels[d] === '(none)' ? ' prod-cut-none' : '') }, total ? (i === 0 ? 'Total' : '') : r.labels[d])),
      ...shown.map(([k]) => h('td', { class: 'prod-c prod-num' + (r.stages[k] ? ' prod-cut-has' : ' prod-cut-zero'), 'data-stage': k }, r.stages[k] ? String(r.stages[k]) : '–')),
      h('td', { class: 'prod-c prod-num', 'data-metric': 'open' }, openCount(r) ? String(openCount(r)) : '–'), ...metrics.map(([k, , f]) => h('td', { class: 'prod-c prod-num', 'data-metric': k }, f(r.stats[k])))];
    const tot = { labels: {}, stages: out.totals.stages, concepts: out.totals.concepts, stats: out.totals.stats };
    const stageKeys = stages.map(([x]) => x), metricKeys = METRICS.map(([x]) => x);
    const group = (title, items, on, tidPrefix, setter) => h('fieldset', { class: 'prod-colgroup' }, h('legend', null, title),
      items.map(([k, t]) => h('label', { class: 'prod-cols-opt' }, h('input', { type: 'checkbox', checked: on.includes(k), 'data-testid': tidPrefix + k, onchange: (e) => setter(k, e.target.checked) }), ' ' + t)));
    const columns = h('details', { class: 'prod-cuts-columns', 'data-testid': 'prod-cuts-metrics' }, h('summary', null, `Columns · ${shown.length + metrics.length} shown`),
      h('div', { class: 'prod-colpanel' }, group('Status', stages, st.stages, 'prod-cuts-stage-', (k, on) => { st.stages = stageKeys.filter((x) => (x === k ? on : st.stages.includes(x))); changed(); drawTable(); }),
        group('Metrics', METRICS.map(([k, t]) => [k, t]), st.metrics, 'prod-cuts-metric-', (k, on) => { st.metrics = metricKeys.filter((x) => (x === k ? on : st.metrics.includes(x))); changed(); drawTable(); }),
        h('button', { type: 'button', class: 'btn prod-small', 'data-testid': 'prod-cuts-metrics-all', onclick: () => { st.stages = stageKeys.slice(); st.metrics = metricKeys.slice(); changed(); drawTable(); } }, 'Show all columns')));
    if (metricOpen) columns.setAttribute('open', '');
    columns.addEventListener('toggle', () => { metricOpen = columns.open; });
    tableHost.replaceChildren(
      h('div', { class: 'prod-cuts-toolbar' },
        h('div', { class: 'prod-qv' }, h('span', { class: 'prod-cuts-zonelabel', id: 'prod-qv-l' }, 'Quick view'),
          h('div', { class: 'prod-cuts-quick', 'data-testid': 'prod-cuts-quick', role: 'group', 'aria-labelledby': 'prod-qv-l' },
            QUICK.map(([k, t, tip, fn]) => h('button', { type: 'button', class: 'pri-chipbtn pri-cc ' + (QV_COLOR[k] || ''), 'aria-pressed': String(st.quick === k), 'data-on': String(st.quick === k && k !== ''), title: tip, 'data-testid': 'prod-cuts-quick-' + (k || 'all'),
              onclick: () => { st.quick = k; changed(); drawTable(); } }, h('span', { class: 'pri-cdot', 'aria-hidden': 'true' }), t, ' ', h('span', { class: 'prod-qf-n' }, String(inScope.filter((r) => fn(r, base)).length))))),
          h('p', { class: 'pri-sub prod-qv-desc', 'data-testid': 'prod-cuts-quick-desc' }, h('strong', null, quick[1] + ': '), quick[0] ? quick[2] + '.' : 'Every cut in the table. Pick a quick view to narrow it to the cuts that need attention.')),
        columns),
      selBar(),
      rows.length
        ? h('div', { class: 'prod-scroll prod-cuts-scroll', 'data-testid': 'prod-cuts-scroll' }, h('table', { class: 'pri-table prod-table prod-cuts-table', 'data-testid': 'prod-cuts-table' },
          h('thead', null, h('tr', null, heads)), h('tbody', null, h('tr', { class: 'prod-cuts-total', 'data-testid': 'prod-cuts-total' }, cells(tot, true)), rows.map((r) => h('tr', { 'data-testid': 'prod-cuts-row', class: 'prod-cuts-clickrow', tabindex: '0', title: 'Click to see the ads behind this row', onclick: (e) => { if (!e.target.closest('a, button, input, label')) openAds(r); }, onkeydown: (e) => { if (e.key === 'Enter' && !e.target.closest('input')) openAds(r); } }, cells(r, false))))))
        : h('p', { 'data-testid': 'prod-cuts-empty' }, 'No cuts match these filters and dates.'),
      h('p', { class: 'pri-sub prod-cuts-basis', 'data-testid': 'prod-cuts-basis' }, `${out.from} to ${out.to}. ${out.basis} Status columns count production concepts (dropped ones left out) for the chosen week, vendor and product filters; they do not depend on the dates. A concept with no ICP or pain point yet shows under (none). Quick filters: blended NC ROAS of all cuts here is ${isNum(base) ? fmt.roas(base) + 'x' : 'n/a'}; working = at least $${MIN_SPEND.toLocaleString('en-US')} spent and NC ROAS at or above it; in pipeline = concepts not yet launched. `
        + (out.warning || '')));
  }

  drawBuilder();
  drawWhen();
  return { refresh: fetchNow, init: fetchNow };
}
