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
const tierClass = (t) => 'prod-prio-' + String(t || 'none').toLowerCase().replace(/[^a-z]+/g, '-');
const OPEN = ['assigned', 'brief_sent', 'in_production', 'in_review', 'ready'];
export const openCount = (r) => OPEN.reduce((n, k) => n + (r.stages[k] || 0), 0);
const working = (r, base) => (r.stats.spend || 0) >= MIN_SPEND && isNum(r.stats.nc_roas) && isNum(base) && r.stats.nc_roas >= base;
const QV_COLOR = { prio: 'pri-cc-sec-competitor', stuck: 'pri-cc-sec-faded', gap: 'pri-cc-sec-iterate', scale: 'pri-cc-sec-new', ready: 'pri-cc-sec-proposal', dark: 'pri-cc-sec-stop' };
export const QUICK = [
  ['', 'All cuts', 'Every cut', () => true],
  ['prio', 'Make more now', 'Priority score of 55 or more: the cuts the score says need new creative first (needs Market cluster, ICP or Pain point in Group by)', (r) => !!r.priority && r.priority.tier === 'Make more now'],
  ['stuck', 'Lots in pipeline, not working', `3 or more concepts in the pipeline, at least $${MIN_SPEND.toLocaleString('en-US')} spent, and NC ROAS below the blended NC ROAS of all cuts in the window`, (r, base) => openCount(r) >= 3 && (r.stats.spend || 0) >= MIN_SPEND && isNum(r.stats.nc_roas) && isNum(base) && r.stats.nc_roas < base],
  ['gap', 'Working, nothing in pipeline', 'NC ROAS at or above blended with at least $1,000 spent, and no concepts in the pipeline', (r, base) => working(r, base) && openCount(r) === 0],
  ['scale', 'Working, 2 or fewer live ads', 'NC ROAS at or above blended with at least $1,000 spent, and 2 or fewer unique live ads', (r, base) => working(r, base) && (r.stats.live_ads || 0) <= 2],
  ['ready', 'Ready to launch, none live', 'At least one concept is ready to launch and no unique ad is live', (r) => (r.stages.ready || 0) > 0 && !(r.stats.live_ads > 0)],
  ['dark', 'Winners gone dark', 'NC ROAS at or above blended with at least $1,000 spent in the window, but no unique ad live now', (r, base) => working(r, base) && !(r.stats.live_ads > 0)],
];
const PRODUCT_SLUG = { 'Vita Bull': 'vita-bull', 'Beef Liver': 'beef-liver', Boneflex: 'boneflex', 'Vita Belle': 'vita-belle' };
export const ADS_MIN_SPEND = 250;
// Colour grading: each graded metric is compared with the Total row (the blended number of every cut in the window); a cut needs at least GRADE_MIN_SPEND spent to be coloured, so a tiny sample never looks like a winner.
// dir 1 = higher is better, -1 = lower is better. ratio = value / total, flipped for lower-is-better; 5 steps from strong red to strong green.
export const GRADE_MIN_SPEND = 250;
export const GRADED = { nc_roas: 1, meta_roas: 1, nc_cpa: -1, cpm: -1, cplc: -1, ctr: 1, conv_rate: 1, aov: 1 };
export function gradeOf(key, value, base, spend) {
  const dir = GRADED[key];
  if (!dir || !isNum(value) || !isNum(base) || base <= 0 || !(spend >= GRADE_MIN_SPEND)) return '';
  const r = dir === 1 ? value / base : (value > 0 ? base / value : 0);
  return r >= 1.25 ? 'g2' : r >= 1.05 ? 'g1' : r >= 0.95 ? 'g0' : r >= 0.75 ? 'r1' : 'r2';
}
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

// Drag a row of a vertical list by its grip (pointer events: mouse and touch), or press the up and down arrows on the grip. keysOf() gives the current order, onMove(newKeys) gets the new one.
function enableReorder(list, keysOf, onMove) {
  const rows = () => [...list.querySelectorAll('.prod-ordrow')];
  list.addEventListener('keydown', (e) => {
    const grip = e.target.closest('.prod-ordgrip');
    if (!grip || (e.key !== 'ArrowUp' && e.key !== 'ArrowDown')) return;
    e.preventDefault();
    const key = grip.closest('.prod-ordrow').dataset.key, keys = keysOf(), i = keys.indexOf(key), j = i + (e.key === 'ArrowUp' ? -1 : 1);
    if (j < 0 || j >= keys.length) return;
    [keys[i], keys[j]] = [keys[j], keys[i]];
    onMove(keys);
    requestAnimationFrame(() => document.querySelector(`.prod-ordgrip[data-testid="prod-cuts-metric-grip-${key}"]`)?.focus());
  });
  list.addEventListener('pointerdown', (e) => {
    const grip = e.target.closest('.prod-ordgrip');
    if (!grip || e.button !== 0) return;
    e.preventDefault();
    const row = grip.closest('.prod-ordrow'), key = row.dataset.key;
    row.classList.add('prod-ordrow-drag');
    const at = (y) => { const others = rows().filter((r) => r !== row); let n = 0; for (const r of others) { const b = r.getBoundingClientRect(); if (y > b.top + b.height / 2) n++; } return n; };
    const bar = h('li', { class: 'prod-orddrop', 'aria-hidden': 'true' });
    const move = (ev) => { const others = rows().filter((r) => r !== row), n = at(ev.clientY); bar.remove(); if (others[n]) list.insertBefore(bar, others[n]); else list.append(bar); };
    const end = (ev, commit) => {
      document.removeEventListener('pointermove', move); document.removeEventListener('pointerup', up); document.removeEventListener('pointercancel', cancel);
      bar.remove(); row.classList.remove('prod-ordrow-drag');
      if (!commit) return;
      const rest = keysOf().filter((k) => k !== key), n = at(ev.clientY);
      rest.splice(n, 0, key);
      if (rest.join() !== keysOf().join()) onMove(rest);
    };
    const up = (ev) => end(ev, true), cancel = (ev) => end(ev, false);
    document.addEventListener('pointermove', move); document.addEventListener('pointerup', up); document.addEventListener('pointercancel', cancel);
  });
}

// Drag a metric column by its header to a new place in the table itself. The column follows the pointer as a label, the column being moved fades, a bar shows where it will land; on drop the columns slide into
// place and the moved one flashes. keysOf() = the full metric order, onMove(newKeys, movedKey) saves it and redraws. A plain click still sorts (the click after a drag is swallowed).
function enableHeaderDrag(table, keysOf, onMove) {
  const heads = () => [...table.querySelectorAll('thead th[data-mkey]')];
  table.querySelector('thead').addEventListener('pointerdown', (e) => {
    const th = e.target.closest('th[data-mkey]');
    if (!th || e.button !== 0) return;
    const key = th.dataset.mkey, x0 = e.clientX;
    let ghost = null, moved = false;
    const cols = () => [th, ...table.querySelectorAll(`td[data-metric="${key}"]`)];
    const target = (x) => { const others = heads().filter((c) => c !== th); let n = 0; for (const c of others) { const b = c.getBoundingClientRect(); if (x > b.left + b.width / 2) n++; } return { n, others }; };
    const mark = (x) => { const { n, others } = target(x); heads().forEach((c) => c.classList.remove('prod-th-before', 'prod-th-end')); if (others[n]) others[n].classList.add('prod-th-before'); else if (others.length) others[others.length - 1].classList.add('prod-th-end'); };
    const move = (ev) => {
      if (!moved && Math.abs(ev.clientX - x0) < 6) return;
      if (!moved) { moved = true; cols().forEach((c) => c.classList.add('prod-col-drag')); ghost = h('div', { class: 'prod-th-ghost', 'aria-hidden': 'true' }, th.textContent.trim()); document.body.append(ghost); document.body.classList.add('prod-dragging-col'); }
      ev.preventDefault();
      ghost.style.left = ev.clientX + 10 + 'px'; ghost.style.top = (th.getBoundingClientRect().top + 4) + 'px';
      mark(ev.clientX);
    };
    const end = (ev, commit) => {
      document.removeEventListener('pointermove', move); document.removeEventListener('pointerup', up); document.removeEventListener('pointercancel', cancel);
      if (!moved) return;
      document.body.classList.remove('prod-dragging-col');
      ghost.remove(); cols().forEach((c) => c.classList.remove('prod-col-drag')); heads().forEach((c) => c.classList.remove('prod-th-before', 'prod-th-end'));
      table.addEventListener('click', (c) => { c.stopPropagation(); c.preventDefault(); }, { once: true, capture: true });
      if (!commit) return;
      const { n, others } = target(ev.clientX), full = keysOf().filter((k) => k !== key);
      const at = others[n] ? full.indexOf(others[n].dataset.mkey) : full.indexOf(others[others.length - 1].dataset.mkey) + 1;
      full.splice(at, 0, key);
      if (full.join() !== keysOf().join()) onMove(full, key);
    };
    const up = (ev) => end(ev, true), cancel = (ev) => end(ev, false);
    document.addEventListener('pointermove', move, { passive: false }); document.addEventListener('pointerup', up); document.addEventListener('pointercancel', cancel);
  });
}

// After a redraw: slide every metric column from where it was to where it is, and flash the one that moved (FLIP).
function animateColumns(table, before, movedKey) {
  const reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  for (const th of table.querySelectorAll('thead th[data-mkey]')) {
    const key = th.dataset.mkey, dx = before[key] === undefined ? 0 : before[key] - th.getBoundingClientRect().left;
    const cells = [th, ...table.querySelectorAll(`td[data-metric="${key}"]`)];
    if (key === movedKey) cells.forEach((c) => c.classList.add('prod-col-landed'));
    if (!dx || reduce) continue;
    cells.forEach((c) => { c.style.transition = 'none'; c.style.transform = `translateX(${dx}px)`; });
    requestAnimationFrame(() => requestAnimationFrame(() => cells.forEach((c) => { c.style.transition = 'transform 280ms cubic-bezier(.2,.8,.2,1)'; c.style.transform = ''; })));
  }
  setTimeout(() => table.querySelectorAll('.prod-col-landed').forEach((c) => c.classList.remove('prod-col-landed')), 1100);
}

// The plain-English explanation of the priority score, from docs/PRIORITY_SCORE.md. Opens in a popup so nobody leaves the page.
function openScoreHelp() {
  const row = (a, b, c, d) => h('tr', null, h('td', { class: 'prod-c prod-sh-part' }, a), h('td', { class: 'prod-c prod-num' }, b), h('td', { class: 'prod-c' }, c), h('td', { class: 'prod-c' }, d));
  openModal({ label: 'How the priority score works', className: 'prod-cutads prod-scorehelp', build(box) {
    box.append(h('h3', { class: 'prod-cutads-title', 'data-testid': 'prod-scorehelp-title' }, 'How the priority score works'),
      h('p', null, h('strong', null, 'One line: '), 'a number from 0 to 100 for how much a cut (any mix of market cluster, ICP, pain point, vendor and image / video) needs new creative now. Use it to order the list. It is not a verdict.'),
      h('h4', { class: 'pri-h' }, 'What goes in'),
      h('div', { class: 'prod-scroll' }, h('table', { class: 'pri-table prod-table prod-cuts-table' }, h('thead', null, h('tr', null, ['Part', 'Weight', 'What it measures', 'Why it is there'].map((x, i) => h('th', { scope: 'col', class: i === 1 ? 'prod-num' : null }, x)))),
        h('tbody', null,
          row('Hit rate', '40%', 'Of the ads in the cut that spent $100 or more and are 14 to 180 days old, the share that reached $1,000 of spend. Pulled toward the product average by 4 pretend ads, so a tiny sample cannot win.', 'Tells you whether this kind of creative finds winners at all.'),
          row('Supply gap', '25%', 'Are new ads going out in step with the money the cut carries? 60%: share of new ads in the last 60 days against share of 30-day spend. 40%: days since the last launch, out of 45. Counts less when spend is small.', 'A cut with lots of spend and few new ads is running on old creative.'),
          row('Scale', '25%', '30-day spend on a log scale ($1,000 = 0, $50,000 = 1).', 'Winning creative here moves more money.'),
          row('Return', '10%', '30-day new-customer ROAS against what the cut\'s product mix gets, pulled toward 1 by $2,500 of pretend spend.', 'Small weight on purpose: in past data a cut\'s old ROAS did not predict how its new ads did.')))),
      h('h4', { class: 'pri-h' }, 'Then it is adjusted'),
      h('ul', null,
        h('li', null, h('strong', null, 'Pipeline discount: '), 'the score is cut by up to half when briefs and tickets already cover the need (open tickets divided by the larger of 3 and half the launches of the last 60 days).'),
        h('li', null, h('strong', null, 'Loss cap: '), 'a cut with $10,000 or more of 30-day spend and new-customer ROAS under 0.85 of its product average cannot score above 44.'),
        h('li', null, h('strong', null, 'Tiers: '), '55 and up Make more now, 45 Make more, 35 Keep steady, under that Low priority. Under $100 spent and no tested ads reads Untested and sorts last. A low-confidence cut never reads above Keep steady.')),
      h('h4', { class: 'pri-h' }, 'Not enough data'),
      h('p', { 'data-testid': 'prod-scorehelp-untested' }, 'The score only ranks a cut that has at least $1,000 spent in the last 30 days and 3 tested ads (ads that spent $100 or more). Anything thinner shows "Not enough data" instead of a number, sorts after every scored cut, and its tooltip says what is missing and gives the model\'s indicative guess. That includes cuts nobody has tested at all: the score has no evidence about them, so it cannot say whether to test them. Which untested idea to try next is the Test section of the weekly Priorities page, not this score. The cut shows a number as soon as it clears the bar.'),
      h('h4', { class: 'pri-h' }, 'Which time windows it looks at'),
      h('p', { 'data-testid': 'prod-scorehelp-windows' }, 'Everything is measured up to the end of the dates you picked: the last 7 days, the last 30 days and the 30 days before that, plus how old each ad is (hit rate looks at ads 14 to 180 days old). "Used to do great, now does not" is handled in three ways: the loss cap (big spend with weak return cannot score high), a "revive candidate" note when a cut has almost no spend now but its past new ads hit, and a note when 80% of spend sits on ads older than 60 days. The trend itself (last 30 days against the 30 before) is calculated and shown in the reasons but carries no weight in the score, because in past data it did not predict how new ads did.'),
      h('h4', { class: 'pri-h' }, 'Levels'),
      h('p', null, 'It works for any cut made of market cluster, ICP, pain point, vendor and image / video, scored from that cut\'s own numbers (never an average of smaller cuts). Product is a filter, not a level: with Product grouped, each product is scored against its own average.'),
      h('h4', { class: 'pri-h' }, 'Does it work? Tested on past data'),
      h('ul', null,
        h('li', null, 'Eleven past dates (Jul 6 to Sep 14), cuts with 3 or more tested new ads. Score at that date against how ads launched in the next weeks did.'),
        h('li', null, 'It ranks a little better than ranking by spend (rank correlation with new-ad hit rate 0.22 against 0.20; with new-ad return 0.16 against 0.08). Ranking by past ROAS predicted nothing, which is why return only weighs 10%.'),
        h('li', null, 'Best at ICP + pain point: the top third of cuts had a 51% hit rate on new ads, the bottom third 26% (ranking by spend: 51% against 32%). Weak at pain point alone; mixed at market cluster.'),
        h('li', null, 'Honest limits: the weights were picked on this same history, so it looks better than it will; hit is judged on spend, and buyers decide spend; the pipeline discount and tier floors are judgement calls with no ticket history to test them.')),
      h('p', { class: 'pri-sub' }, 'Basis: spend is Meta\'s; return is Upstack new customers (first orders). Full spec and backtest: docs/PRIORITY_SCORE.md.'));
  } });
}

// A copy of the header row that follows the page scroll: while the table is on screen but its own header has scrolled up under the top bars, a fixed copy sits just below them, lined up with the real columns and
// scrolled sideways with them. Clicking a copied header presses the real one (so sorting works); dragging a column is done on the real header. Removed as soon as the table leaves the page.
let stickyCleanup = null;
function enableStickyHeader(frame, table) {
  if (stickyCleanup) stickyCleanup();
  const wrap = h('div', { class: 'prod-page prod-stickyhead', hidden: true, 'data-testid': 'prod-stickyhead' });
  let inner = null, last = '';
  document.body.append(wrap);
  const offset = () => Math.max(0, ...['#topbar', '#subnav'].map((s) => { const el = document.querySelector(s); return el ? el.getBoundingClientRect().bottom : 0; }));
  const build = () => {
    const real = table.querySelector('thead');
    const clone = table.cloneNode(false); clone.removeAttribute('data-testid'); clone.style.width = table.getBoundingClientRect().width + 'px'; clone.style.tableLayout = 'fixed';
    const head = real.cloneNode(true);
    [...head.querySelectorAll('th')].forEach((th, i) => { const w = real.querySelectorAll('th')[i].getBoundingClientRect().width; th.style.width = th.style.minWidth = th.style.maxWidth = w + 'px'; th.removeAttribute('data-testid'); th.querySelectorAll('[data-testid]').forEach((e) => e.removeAttribute('data-testid')); });
    clone.append(head); inner = h('div', { class: 'prod-stickyhead-in' }, clone); wrap.replaceChildren(inner);
    head.addEventListener('click', (e) => { const th = e.target.closest('th'); if (!th) return; const i = [...head.querySelectorAll('th')].indexOf(th); real.querySelectorAll('th')[i]?.querySelector('button')?.click(); });
  };
  const update = () => {
    if (!table.isConnected) { cleanup(); return; }
    const off = offset(), fr = frame.getBoundingClientRect(), tr = table.getBoundingClientRect(), hh = table.querySelector('thead').getBoundingClientRect().height;
    const show = fr.top < off && tr.bottom - hh > off && fr.bottom > off;
    wrap.hidden = !show;
    if (!show) return;
    const sig = Math.round(tr.width) + ':' + table.querySelectorAll('thead th').length + ':' + [...table.querySelectorAll('thead th')].map((t) => Math.round(t.getBoundingClientRect().width)).join(',');
    if (sig !== last || !inner) { last = sig; build(); }
    wrap.style.top = off + 'px'; wrap.style.left = fr.left + 'px'; wrap.style.width = fr.width + 'px';
    inner.style.transform = `translateX(${-frame.scrollLeft}px)`;
  };
  let pending = false;
  const tick = () => { if (pending) return; pending = true; const run = () => { if (!pending) return; pending = false; update(); }; requestAnimationFrame(run); setTimeout(run, 60); };     // a frame, or 60 ms if the page is in a background tab and frames are paused
  window.addEventListener('scroll', tick, { passive: true }); window.addEventListener('resize', tick); frame.addEventListener('scroll', tick, { passive: true });
  const gone = new MutationObserver(() => { if (!table.isConnected) cleanup(); });                                  // the table left the page (another view, another page): remove the copy at once
  gone.observe(document.body, { childList: true, subtree: true });
  let done = false;
  const cleanup = () => { if (done) return; done = true; gone.disconnect(); window.removeEventListener('scroll', tick); window.removeEventListener('resize', tick); frame.removeEventListener('scroll', tick); wrap.remove(); if (stickyCleanup === cleanup) stickyCleanup = null; };    // each copy cleans only itself
  stickyCleanup = cleanup;
  tick();
}

// The evidence behind a cut: priority score and reasons, top 5 spenders since launch, top hooks, and the ads driving it (with the $250 toggle). d = GET /api/priorities/cuts/ads, pr = the row's priority or null.
export function renderEvidence(body, d, pr) {
  const name = (a) => h('span', { class: 'prod-cutads-name' }, a.ad_id ? h('a', { href: href('/ad/' + encodeURIComponent(a.ad_id), {}), target: '_blank', rel: 'noopener noreferrer', 'data-testid': 'prod-cutads-link' }, a.name) : a.name,
    a.story_url && /^https:\/\/(www\.)?(facebook|instagram)\.com\//.test(a.story_url) ? h('a', { class: 'pri-sub', href: a.story_url, target: '_blank', rel: 'noopener noreferrer' }, 'post') : null, a.live ? h('span', { class: 'prod-chip prod-chip-live', title: 'Spent in the last two loaded days' }, 'Live') : null, a.hook ? h('span', { class: 'prod-cutads-hook', 'data-testid': 'prod-cutads-hook' }, a.hook) : null);
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
  const prio = pr ? h('div', { class: 'prod-cutads-prio', 'data-testid': 'prod-cutads-prio' }, h('div', null, isNum(pr.score) ? h('span', { class: 'prod-prio prod-prio-big ' + tierClass(pr.tier) }, String(Math.round(pr.score))) : null, ' ', h('strong', null, pr.tier), h('span', { class: 'pri-sub' }, ` · ${pr.confidence} confidence`)),
    h('p', { class: 'pri-sub' }, pr.why), h('ul', { class: 'prod-cutads-drivers' }, (pr.drivers || []).map((x) => h('li', null, x.plain_text || x.label))), (pr.flags || []).length ? h('p', { class: 'pri-sub' }, 'Notes: ' + pr.flags.join(', ')) : null) : null;
  body.replaceChildren(...[prio, d.warning ? h('p', { class: 'pri-err' }, d.warning) : null,
    h('h4', { class: 'pri-h' }, 'Top 5 spenders since launch ', h('span', { class: 'pri-hnote' }, 'all time, Meta numbers, for inspiration')),
    table(['Ad', 'Spend', 'Revenue', 'Meta ROAS', 'Purchases'], d.top.map((a) => tr([name(a), fmt.money(a.spend), fmt.money(a.revenue), roasText(a.meta_roas), fmt.int(a.purchases)])), 'prod-cutads-top'),
    h('h4', { class: 'pri-h' }, 'Top hooks ', h('span', { class: 'pri-hnote' }, `best NC ROAS among ads that spent $${ADS_MIN_SPEND} or more in these dates`)),
    (d.top_hooks || []).length ? h('div', { class: 'prod-scroll' }, h('table', { class: 'pri-table prod-table prod-cuts-table', 'data-testid': 'prod-cutads-hooks' }, h('thead', null, h('tr', null, ['Hook', 'Spend', 'NC ROAS', 'NC CPA'].map((x, i) => h('th', { scope: 'col', class: i ? 'prod-num' : null }, x)))),
      h('tbody', null, d.top_hooks.map((a) => h('tr', { 'data-testid': 'prod-cutads-hookrow' }, h('td', { class: 'prod-c prod-cutads-hooktext' }, h('span', { class: 'prod-cutads-hookline' }, a.hook), h('span', { class: 'pri-sub' }, ' ', a.ad_id ? h('a', { href: href('/ad/' + encodeURIComponent(a.ad_id), {}), target: '_blank', rel: 'noopener noreferrer' }, a.name) : a.name, a.live ? ' · Live' : '')),
        h('td', { class: 'prod-c prod-num' }, fmt.money(a.spend)), h('td', { class: 'prod-c prod-num' }, roasText(a.nc_roas)), h('td', { class: 'prod-c prod-num' }, fmt.money2(a.nc_cpa))))))) : h('p', { class: 'pri-sub', 'data-testid': 'prod-cutads-nohooks' }, 'No hooks stored for the ads that spent enough in these dates.'),
    h('h4', { class: 'pri-h' }, 'Ads driving this row ', h('span', { class: 'pri-hnote' }, `spent between ${d.from} and ${d.to}`)), winHost].filter(Boolean));
  drawWindow();
}

// st: { by: ['product', ...], preset, from, to, pipelineOnly, stages: [status...], sort: {key, dir} }  (plain object kept by the page so it survives a redraw)
export function cutsView({ host, st, filters, signal, onState, say, pageFilters }) {
  let out = null;
  let metricOpen = false;
  const sel = new Map();                                         // rows ticked for Proposed Concept: key = the cut's labels, value = the row
  const rowKey = (r) => JSON.stringify(out.group.map((d) => r.labels[d]));
  let addWeek = mondayOf(today());
  let addMedia = '';                                            // image or video for the ticked cuts when Image / video is not one of the grouped cuts
  let seq = 0;
  const builder = h('div', { class: 'prod-cuts-builder', 'data-testid': 'prod-cuts-builder' });
  const when = h('div', { class: 'prod-cuts-when', 'data-testid': 'prod-cuts-when' });
  const filterHost = h('div', { class: 'ctl-row prod-cuts-filters', 'data-testid': 'prod-cuts-filters' });
  const tableHost = h('div', { class: 'prod-cuts-out', 'data-testid': 'prod-cuts-out' });
  const live = h('p', { class: 'pri-sub prod-cuts-live', role: 'status', 'data-testid': 'prod-cuts-live' });
  const viewsHost = h('div', { class: 'prod-cuts-views', 'data-testid': 'prod-cuts-views' });
  host.replaceChildren(viewsHost, builder, when, filterHost, tableHost, live);
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

  // ---- saved views: the whole page state under a name, shared with the team
  let views = [], viewId = '', viewMsg = '';
  const orderedMetrics = () => { const keys = [...st.order.filter((k) => METRICS.some(([m]) => m === k)), ...METRICS.map(([k]) => k).filter((k) => !st.order.includes(k))]; return keys.map((k) => METRICS.find(([m]) => m === k)); };
  const stateOf = () => ({ by: st.by, preset: st.preset, ...(st.preset ? {} : { from: st.from, to: st.to }), f: st.f, metrics: st.metrics, order: st.order, stages: st.stages, quick: st.quick, sort: { key: st.sort.key || '', dir: st.sort.dir }, pipelineOnly: st.pipelineOnly, ...(pageFilters ? pageFilters.get() : {}) });
  function applyState(s) {
    if (Array.isArray(s.by) && s.by.length) st.by = s.by.filter((k) => DIMS.some(([d]) => d === k));
    if (s.preset !== undefined) { st.preset = s.preset; Object.assign(st, s.preset ? presetRange(s.preset) : { from: s.from || st.from, to: s.to || st.to }); }
    if (s.f) st.f = { cluster: s.f.cluster || [], icp: s.f.icp || [], pain_point: s.f.pain_point || [] };
    if (Array.isArray(s.metrics)) st.metrics = s.metrics.filter((k) => METRICS.some(([m]) => m === k));
    if (Array.isArray(s.order)) st.order = s.order.filter((k) => METRICS.some(([m]) => m === k));
    if (Array.isArray(s.stages)) st.stages = s.stages;
    if (typeof s.quick === 'string') st.quick = QUICK.some(([k]) => k === s.quick) ? s.quick : '';
    if (s.sort) st.sort = { key: s.sort.key || '', dir: s.sort.dir === 'asc' ? 'asc' : 'desc' };
    if (s.pipelineOnly !== undefined) st.pipelineOnly = !!s.pipelineOnly;
    if (pageFilters) pageFilters.set({ product: s.product || '', vendor: s.vendor || '', week: s.week || '' });
    drawBuilder(); drawWhen(); drawFilters(); changed(); fetchNow();
  }
  async function loadViews() {
    try { const d = await api('/api/priorities/cut-views', {}, { signal }); views = d.views || []; } catch (e) { if (e && e.name === 'AbortError') throw e; views = []; }
    drawViews();
  }
  function drawViews() {
    const name = h('input', { type: 'text', maxlength: 80, placeholder: 'Name this view', 'aria-label': 'View name', 'data-testid': 'prod-cuts-view-name', value: (views.find((v) => String(v.id) === viewId) || {}).name || '' });
    const flash = h('span', { class: 'pri-sub', role: 'status', 'data-testid': 'prod-cuts-view-msg' }, viewMsg);
    viewsHost.replaceChildren(...[h('span', { class: 'prod-cuts-zonelabel' }, 'Views'),
      h('select', { 'aria-label': 'Saved views', 'data-testid': 'prod-cuts-view-select', onchange: (e) => { viewId = e.target.value; const v = views.find((x) => String(x.id) === viewId); viewMsg = v ? `Loaded ${v.name}.` : ''; if (v) applyState(v.state); drawViews(); } },
        h('option', { value: '' }, views.length ? 'Choose a saved view' : 'No saved views yet'), views.map((v) => h('option', { value: String(v.id), selected: String(v.id) === viewId }, v.name))),
      name,
      h('button', { type: 'button', class: 'btn small primary', 'data-testid': 'prod-cuts-view-save', onclick: async () => {
        const nm = name.value.trim();
        if (!nm) { flash.textContent = 'Type a name first.'; return; }
        try { const v = await api('/api/priorities/cut-views', {}, { method: 'POST', body: { name: nm, state: stateOf() } }); viewId = String(v.id); viewMsg = `Saved ${v.name}.`; toast(viewMsg); await loadViews(); }
        catch (e) { if (e && e.name === 'AbortError') throw e; flash.textContent = e.message || 'Could not save.'; } } }, 'Save view'),
      viewId ? h('button', { type: 'button', class: 'btn small', 'data-testid': 'prod-cuts-view-delete', onclick: async () => {
        try { await api('/api/priorities/cut-views/' + viewId, {}, { method: 'DELETE' }); viewId = ''; viewMsg = 'Deleted.'; await loadViews(); } catch (e) { if (e && e.name === 'AbortError') throw e; flash.textContent = e.message || 'Could not delete.'; } } }, 'Delete view') : null,
      flash].filter(Boolean));
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
    renderEvidence(body, d, r.priority);
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
      const d = await api('/api/priorities/cuts', { score: 1, group: st.by.join(','), from: st.from, to: st.to, week: filters().week, vendor: filters().vendor, product: filters().product, cluster: st.f.cluster.join('|'), icp: st.f.icp.join('|'), pain_point: st.f.pain_point.join('|') }, { signal });
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
  // ---- Proposed Concept: add the ticked cuts to the backlog for a week
  function selBar() {
    if (!sel.size) return h('p', { class: 'pri-sub prod-cuts-selhint', 'data-testid': 'prod-cuts-selhint' }, 'Tick cuts to add them to Proposed Concept for a week.');
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
          toast(`${r.created.length} added to Proposed Concept for ${weekLabel(addWeek).toLowerCase()}${r.skipped.length ? `, ${r.skipped.length} already there` : ''}.`);
          sel.clear(); drawTable();
        } catch (e) { if (e && e.name === 'AbortError') throw e; msg.textContent = e.message || 'Could not add.'; } } }, 'Add to Proposed Concept'),
      h('button', { type: 'button', class: 'btn small', 'data-testid': 'prod-cuts-selclear', onclick: () => { sel.clear(); drawTable(); } }, 'Clear'), msg);
  }
  const stageList = () => (out && out.stages ? out.stages.map((s) => [s.status, s.label]) : STAGE_FALLBACK);
  const val = (row, key) => (key.startsWith('label:') ? (row.labels[key.slice(6)] || '') : key.startsWith('stage:') ? row.stages[key.slice(6)] : key === 'open' ? openCount(row) : key === 'priority' ? (row.priority && isNum(row.priority.score) ? row.priority.score : null) : row.stats[key]);
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
    const metrics = orderedMetrics().filter(([k]) => st.metrics.includes(k));
    const sortBtn = (key, text, title, right) => {
      const on = st.sort.key === key;
      return h('th', { scope: 'col', class: (right ? 'prod-num' : '') + (METRICS.some(([m]) => m === key) ? ' prod-th-metric' : ''), 'data-mkey': METRICS.some(([m]) => m === key) ? key : null, title: (title || '') + (METRICS.some(([m]) => m === key) ? ' Drag the header to move this column.' : ''), 'aria-sort': on ? (st.sort.dir === 'asc' ? 'ascending' : 'descending') : 'none' },
        h('button', { type: 'button', class: 'prod-sortbtn', 'data-testid': 'prod-cuts-sort-' + key.replace(':', '-'), onclick: () => { st.sort = on ? { key, dir: st.sort.dir === 'desc' ? 'asc' : 'desc' } : { key, dir: typeof val(rows[0] || { labels: {}, stages: {}, stats: {} }, key) === 'string' ? 'asc' : 'desc' }; changed(); drawTable(); } }, text, on ? (st.sort.dir === 'asc' ? ' ▲' : ' ▼') : ''));
    };
    const heads = [h('th', { scope: 'col' }, h('input', { type: 'checkbox', 'aria-label': 'Select all shown cuts', 'data-testid': 'prod-cuts-selall', checked: rows.length > 0 && rows.every((r) => sel.has(rowKey(r))), onchange: (e) => { for (const r of rows) { if (e.target.checked) sel.set(rowKey(r), r); else sel.delete(rowKey(r)); } drawTable(); } })), ...out.group.map((d) => sortBtn('label:' + d, DIM_LABEL[d])), ...shown.map(([k, t]) => sortBtn('stage:' + k, t, 'Concepts at this status (not dropped)', true)), sortBtn('priority', 'Priority score', 'Creative priority, 0 to 100: how much this cut needs new creative now (hit rate, supply gap, scale, economics; discounted when briefs are already in the pipeline). Click a row for the reasons; the How the score works button explains the formula.', true), sortBtn('open', 'Concepts in pipeline', 'Concepts given out and not yet launched (assigned through ready to launch); dropped ones left out', true), ...metrics.map(([k, t, , tip]) => sortBtn(k, t, tip, true))];
    const cells = (r, total) => [h('td', { class: 'prod-c' }, total ? null : h('input', { type: 'checkbox', 'aria-label': 'Select this cut for Proposed Concept', 'data-testid': 'prod-cuts-sel', checked: sel.has(rowKey(r)), onchange: (e) => { if (e.target.checked) sel.set(rowKey(r), r); else sel.delete(rowKey(r)); drawTable(); } })), ...out.group.map((d, i) => h('td', { class: 'prod-c prod-cut-label' + (r.labels[d] === '(none)' ? ' prod-cut-none' : '') }, total ? (i === 0 ? 'Total' : '') : r.labels[d])),
      ...shown.map(([k]) => h('td', { class: 'prod-c prod-num' + (r.stages[k] ? ' prod-cut-has' : ' prod-cut-zero'), 'data-stage': k }, r.stages[k] ? String(r.stages[k]) : '–')),
      h('td', { class: 'prod-c prod-num prod-prio-cell', 'data-metric': 'priority', title: total ? null : (r.priority ? r.priority.why : (out.priority_note || 'Not scored: no scorable cut in Group by.')) },
        total || !r.priority ? (total ? null : '–') : (isNum(r.priority.score) ? [h('span', { class: 'prod-prio ' + tierClass(r.priority.tier), 'data-testid': 'prod-prio-score' }, String(Math.round(r.priority.score))), h('span', { class: 'prod-prio-tier' }, r.priority.tier)] : [h('span', { class: 'prod-prio prod-prio-nodata', 'data-testid': 'prod-prio-nodata' }, 'Not enough data')])),
      h('td', { class: 'prod-c prod-num', 'data-metric': 'open' }, openCount(r) ? String(openCount(r)) : '–'), ...metrics.map(([k, , f]) => h('td', { class: 'prod-c prod-num' + (total ? '' : (g => (g ? ' prod-heat-' + g : ''))(gradeOf(k, r.stats[k], out.totals.stats[k], r.stats.spend))), 'data-metric': k, 'data-grade': total ? null : gradeOf(k, r.stats[k], out.totals.stats[k], r.stats.spend) || null }, f(r.stats[k])))];
    const tot = { labels: {}, stages: out.totals.stages, concepts: out.totals.concepts, stats: out.totals.stats };
    const stageKeys = stages.map(([x]) => x), metricKeys = METRICS.map(([x]) => x);
    const group = (title, items, on, tidPrefix, setter) => h('fieldset', { class: 'prod-colgroup' }, h('legend', null, title),
      items.map(([k, t]) => h('label', { class: 'prod-cols-opt' }, h('input', { type: 'checkbox', checked: on.includes(k), 'data-testid': tidPrefix + k, onchange: (e) => setter(k, e.target.checked) }), ' ' + t)));
    const moveMetric = (keys, movedKey) => {
      const before = {};
      tableHost.querySelectorAll('thead th[data-mkey]').forEach((th) => { before[th.dataset.mkey] = th.getBoundingClientRect().left; });
      st.order = keys; changed(); drawTable(); animateColumns(tableHost.querySelector('table'), before, movedKey);
    };
    const metricGroup = () => {
      const list = h('ul', { class: 'prod-ordlist', 'data-testid': 'prod-cuts-metric-list' }, orderedMetrics().map(([k, t]) => h('li', { class: 'prod-ordrow', 'data-key': k },
        h('button', { type: 'button', class: 'prod-ordgrip', 'aria-label': `Move ${t}. Drag, or press the up and down arrows.`, title: 'Drag to reorder', 'data-testid': 'prod-cuts-metric-grip-' + k }, h('span', { class: 'prod-grip', 'aria-hidden': 'true' })),
        h('label', { class: 'prod-cols-opt' }, h('input', { type: 'checkbox', checked: st.metrics.includes(k), 'data-testid': 'prod-cuts-metric-' + k, onchange: (e) => { st.metrics = metricKeys.filter((x) => (x === k ? e.target.checked : st.metrics.includes(x))); changed(); drawTable(); } }), ' ' + t))));
      const move = (keys) => { st.order = keys; changed(); drawTable(); };
      enableReorder(list, () => orderedMetrics().map(([k]) => k), move);
      return h('fieldset', { class: 'prod-colgroup' }, h('legend', null, 'Metrics (drag to reorder)'), list);
    };
    const columns = h('details', { class: 'prod-cuts-columns', 'data-testid': 'prod-cuts-metrics' }, h('summary', null, `Columns · ${shown.length + metrics.length} shown`),
      h('div', { class: 'prod-colpanel' }, group('Status', stages, st.stages, 'prod-cuts-stage-', (k, on) => { st.stages = stageKeys.filter((x) => (x === k ? on : st.stages.includes(x))); changed(); drawTable(); }),
        metricGroup(),
        h('button', { type: 'button', class: 'btn prod-small', 'data-testid': 'prod-cuts-metrics-all', onclick: () => { st.stages = stageKeys.slice(); st.metrics = metricKeys.slice(); changed(); drawTable(); } }, 'Show all columns')));
    if (metricOpen) columns.setAttribute('open', '');
    columns.querySelector('summary').addEventListener('click', () => { metricOpen = !columns.open; });                       // read before the browser flips it: survives every redraw of the table
    tableHost.replaceChildren(
      h('div', { class: 'prod-cuts-toolbar' },
        h('div', { class: 'prod-qv' }, h('span', { class: 'prod-cuts-zonelabel', id: 'prod-qv-l' }, 'Quick view'),
          h('div', { class: 'prod-cuts-quick', 'data-testid': 'prod-cuts-quick', role: 'group', 'aria-labelledby': 'prod-qv-l' },
            QUICK.map(([k, t, tip, fn]) => h('button', { type: 'button', class: 'pri-chipbtn pri-cc ' + (QV_COLOR[k] || ''), 'aria-pressed': String(st.quick === k), 'data-on': String(st.quick === k && k !== ''), title: tip, 'data-testid': 'prod-cuts-quick-' + (k || 'all'),
              onclick: () => { st.quick = k; changed(); drawTable(); } }, h('span', { class: 'pri-cdot', 'aria-hidden': 'true' }), t, ' ', h('span', { class: 'prod-qf-n' }, String(inScope.filter((r) => fn(r, base)).length))))),
          h('p', { class: 'pri-sub prod-qv-desc', 'data-testid': 'prod-cuts-quick-desc' }, h('strong', null, quick[1] + ': '), quick[0] ? quick[2] + '.' : 'Every cut in the table. Pick a quick view to narrow it to the cuts that need attention.')),
        h('div', { class: 'prod-cuts-tools' }, h('button', { type: 'button', class: 'btn small', 'data-testid': 'prod-scorehelp-open', onclick: openScoreHelp }, 'How the score works'), columns)),
      selBar(),
      rows.length
        ? h('div', { class: 'prod-scroll prod-cuts-scroll', 'data-testid': 'prod-cuts-scroll' }, h('table', { class: 'pri-table prod-table prod-cuts-table', 'data-testid': 'prod-cuts-table' },
          h('thead', null, h('tr', null, heads)), h('tbody', null, h('tr', { class: 'prod-cuts-total', 'data-testid': 'prod-cuts-total' }, cells(tot, true)), rows.map((r) => h('tr', { 'data-testid': 'prod-cuts-row', class: 'prod-cuts-clickrow', tabindex: '0', title: 'Click to see the ads behind this row', onclick: (e) => { if (!e.target.closest('a, button, input, label')) openAds(r); }, onkeydown: (e) => { if (e.key === 'Enter' && !e.target.closest('input')) openAds(r); } }, cells(r, false))))))
        : h('p', { 'data-testid': 'prod-cuts-empty' }, 'No cuts match these filters and dates.'),
      out.priority_note ? h('p', { class: 'pri-sub', 'data-testid': 'prod-cuts-prionote' }, out.priority_note) : h('p', { class: 'pri-sub prod-cuts-priohow', 'data-testid': 'prod-cuts-priohow' }, 'Priority score: 0 to 100, higher = needs new creative sooner. ', h('button', { type: 'button', class: 'btn small', onclick: openScoreHelp }, 'How the score works'), ' It orders the list; it is not a verdict.'),
      h('p', { class: 'pri-sub prod-cuts-legend', 'data-testid': 'prod-cuts-legend' }, 'Colours compare each cut with the Total row: ', h('span', { class: 'prod-heat-r2 prod-legend-chip' }, 'well below'), h('span', { class: 'prod-heat-r1 prod-legend-chip' }, 'below'), h('span', { class: 'prod-heat-g0 prod-legend-chip' }, 'about the same'), h('span', { class: 'prod-heat-g1 prod-legend-chip' }, 'better'), h('span', { class: 'prod-heat-g2 prod-legend-chip' }, 'well better'), ` (lower is better for NC CPA, CPM and cost per link click; cuts under $${GRADE_MIN_SPEND} spent are not coloured).`),
      h('p', { class: 'pri-sub prod-cuts-basis', 'data-testid': 'prod-cuts-basis' }, `${out.from} to ${out.to}. ${out.basis} Status columns count production concepts (dropped ones left out) for the chosen week, vendor and product filters; they do not depend on the dates. A concept with no ICP or pain point yet shows under (none). Quick filters: blended NC ROAS of all cuts here is ${isNum(base) ? fmt.roas(base) + 'x' : 'n/a'}; working = at least $${MIN_SPEND.toLocaleString('en-US')} spent and NC ROAS at or above it; in pipeline = concepts not yet launched. `
        + (out.warning || '')));
    const tbl = tableHost.querySelector('table.prod-cuts-table');
    if (tbl) enableHeaderDrag(tbl, () => orderedMetrics().map(([k]) => k), moveMetric);
    if (tbl) enableStickyHeader(tableHost.querySelector('[data-testid=prod-cuts-scroll]'), tbl); else if (stickyCleanup) stickyCleanup();
  }

  drawBuilder();
  drawWhen();
  drawViews();
  return { refresh: fetchNow, init: async () => { loadViews().catch(() => {}); return fetchNow(); } };
}
