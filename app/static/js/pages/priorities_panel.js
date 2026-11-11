// The creative-priorities detail panel, shared by /priorities (priorities.js), the Production page and the vendor page (priorities_production.js). One concept's full panel: who it is for,
// story, verdict, hooks, then vs now, former winners, competitor badges, number tiles. Pure rendering from one snapshot row `r` and the payload `d` ({baselines}); no fetching here.
// Class names and test ids start with `pri-` (never ad-, ads-, sponsor, banner, promo).
import { h, fmt, isNum, href } from '../util.js';
import { dayOf, manualPanel } from './priorities_manual.js';

export const SECTIONS = [['revive', 'Revive'], ['make_more', 'Make more'], ['test', 'Test'], ['stop', 'Stop'], ['hold', 'Hold']];
let DEFS = {};                                                                      // the rules from the API payload (real thresholds), set by the page with setDefs
export const setDefs = (x) => { DEFS = x || {}; };
export const actionRule = (k) => ((DEFS.actions || []).find((a) => a.key === k) || {}).rule || '';
const statusRule = (l) => ((DEFS.statuses || []).find((x) => x.label === l) || {}).rule || '';
export const STATUS_CLASS = { 'Working': 'ok', 'Rising': 'up', 'Fading': 'fade', 'Not working': 'bad', 'Average': 'flat', 'Too early': 'flat', 'Untested': 'new', 'No line': 'flat', 'MANUAL': 'manual' };
export const STATUS_ORDER = ['Working', 'Rising', 'Fading', 'Not working', 'Average', 'Too early', 'Untested'];
export const roasX = (v) => (isNum(v) ? fmt.roas(v) + 'x' : '–');
export const pct = (v) => (isNum(v) ? fmt.pct(v) : '–');
// share of n in d as text, one decimal under 10% ("2.2%"), none above ("54%"); a dash when there is no denominator
export const share = (n, dn) => { if (!dn) return '\u2013'; const v = 100 * n / dn; return (v > 0 && v < 10 ? v.toFixed(1) : Math.round(v)) + '%'; };
export const compact$ = (v) => (v >= 1e6 ? '$' + (v / 1e6).toFixed(2) + 'M' : v >= 1e4 ? '$' + Math.round(v / 1e3) + 'K' : fmt.money(v));

// stat cards for the detail panel: [[group, [[label, value, sub?], ...]], ...]; labels and values are the same facts the old inline row showed
export function numbers(r) {
  const m = r.metrics || {};
  if (r.section === 'hold' && r.line_id == null && !r.manual) {                       // ads not mapped to a line: plain numbers
    return [['All time', [['Spend', fmt.money(m.spend)], ['ROAS (all time)', roasX(m.roas)], ['CPA (all time)', fmt.money(m.cpa)], ['Purchases', fmt.int(m.purchases)], ['Ads with spend', fmt.int(m.ads)]]],
      ['Last 30 days', [['Spend', fmt.money(m.spend_30d)], ['ROAS', roasX(m.roas_30d)], ['CPA', fmt.money(m.cpa_30d)], ['Purchases', fmt.int(m.purchases_30d)]]],
      ['Last 14 days', [['Spend', fmt.money(m.spend_14d)], ['ROAS', roasX(m.roas_14d)], ['Purchases', fmt.int(m.purchases_14d)]]]];
  }
  if (r.line_id != null && r.section !== 'test') {            // every line row with results, Hold included, shows the same number tiles
    return [['Performance', [['Spend', fmt.money(m.spend)], ['ROAS (all time)', roasX(m.roas)], ['CPA (all time)', fmt.money(m.cpa)], ['Upstack new-customer ROAS', roasX(m.nc_roas)], ['ROAS, strong tags only', roasX(m.roas_strong_tags)]]],
      ['Testing', m.funnel
        ? [['Ads launched', fmt.int(m.funnel.launched)], ['Hit rate ($10K+ spend)', share(m.funnel.top, m.funnel.launched), `(${fmt.int(m.funnel.top)} of ${fmt.int(m.funnel.launched)} launched ads)`], ['New ads, 30 days', fmt.int(m.launches_30d)]]
        : [['Ads tested ($100+)', fmt.int(m.judged)], ['Ads that reached $1,000', `${m.hits} of ${m.mature}`, `(${pct(m.hit_rate)} of ads that spent $100+ and launched 14+ days ago)`], ['New ads, 30 days', fmt.int(m.launches_30d)]]],
      ['Live', [...(isNum(m.spend_14d) ? [['Last 14 days', roasX(m.roas_14d), `on ${fmt.money(m.spend_14d)}${isNum(m.roas_14d) ? '' : ' (under $100: no ratio)'}`]] : []), ['Last 30 days', roasX(m.roas_30d), `on ${fmt.money(m.spend_30d)}`], ['Prior 30 days', roasX(m.roas_prior30), `on ${fmt.money(m.spend_prior30)}`], ['Live ads', fmt.int(m.live_ads)]].concat(m.funnel && m.funnel.active_30d != null ? [['Launched in last 30 days', fmt.int(m.funnel.launched_30d != null ? m.funnel.launched_30d : m.funnel.active_30d)], ['Of those, $10K+ so far', fmt.int(m.funnel.top_launched_30d != null ? m.funnel.top_launched_30d : m.funnel.top_30d)]] : [])]];
  }
  if (r.source) return [];                                                           // a new idea (not on the sheet yet) has no numbers of ours
  return [['Performance', [['Ads tagged', fmt.int(m.tagged_ads)], ['Spend so far', fmt.money(m.spend)]]]];
}

// the one number on the collapsed row: plain English, the product named, both numbers labelled
export function keyNumber(r, d) {
  if (r.manual) return r.why_now || `Added ${dayOf(r.created_at)}`;
  const m = r.metrics || {};
  const b = (d.baselines || {})[r.product] || {};
  const pn = b.product_name || r.product_name || 'Product';
  const avg = (v) => `${pn} average ${roasX(v)}.`;
  if (r.section === 'revive') {
    const now = m.spend_30d >= 250 ? roasX(m.roas_30d) + ' now.' : 'Off now.';
    return r.stretch ? `${roasX(r.stretch.roas)} at peak. ${now}` : `${roasX(m.roas)} since launch. ${now}`;
  }
  if (r.section === 'make_more') return `${roasX(m.roas_30d)} last 30 days. ${avg(b.roas_30d)}`;
  if (r.source) return r.competitor ? r.competitor.text : 'Draft';
  if (r.section === 'test') return m.spend > 0 ? `${fmt.money(m.spend)} spent so far` : 'No spend yet';
  if (m.spend >= 100) return `${roasX(m.roas)} since launch. ${avg(b.roas)}`;                       // stop and hold
  return m.spend > 0 ? `${fmt.money(m.spend)} spent so far` : 'No spend yet';
}

// ---------- detail panel (the expanded row) ----------
// Reading order: identity strip -> verdict -> the thing to act on (revive play / hooks) -> then vs now -> timeline -> former winners -> competitors -> stat cards -> footer pills.
// Every fact the panel always carried is still here; long definitions sit behind keyboard-operable "?" disclosures and long lists behind "Show all".
export const toneOf = (r) => STATUS_CLASS[r.status] || 'flat';
const TAG_HELP = { 'Market Cluster': 'cluster_description', 'ICP': 'icp_description', 'Pain Point': 'pain_description' };

export function tile(label, value, desc) {
  return h('div', { class: 'pri-tile' },
    h('div', { class: 'pri-tile-h' }, h('span', { class: 'pri-label' }, label),
      desc ? h('details', { class: 'pri-help' }, h('summary', { 'aria-label': `What is ${label}?`, title: `What is ${label}?` }, '?'), h('p', { class: 'pri-helptext' }, desc)) : null),
    h('div', { class: 'pri-tname' }, value || '–'));
}

// first `n` items always shown, the rest behind a toggle (the hidden items stay in the DOM)
function limited(items, n, what) {
  if (items.length <= n) return items;
  const extra = items.slice(n);
  extra.forEach((el) => el.setAttribute('hidden', ''));
  const btn = h('button', { type: 'button', class: 'pri-more', 'aria-expanded': 'false' }, `Show all ${items.length} ${what}`);
  btn.addEventListener('click', () => {
    const open = btn.getAttribute('aria-expanded') !== 'true';
    btn.setAttribute('aria-expanded', String(open));
    extra.forEach((el) => { if (open) el.removeAttribute('hidden'); else el.setAttribute('hidden', ''); });
    btn.textContent = open ? 'Show fewer' : `Show all ${items.length} ${what}`;
  });
  return [...items, btn];
}

function roasPill(v, ref) {
  const cls = !isNum(v) || !isNum(ref) ? 'flat' : v >= ref ? 'ok' : 'fade';
  return h('span', { class: 'pri-pill pri-pill-' + cls }, roasX(v));
}

// one then/now tile: label, then value -> now value, delta chip (good = green, bad = red, tiny change = grey)
function tnTile(label, then, now, fmtv, lowerIsBetter, nowNote) {
  let delta = null; let tone = 'flat';
  if (isNum(then) && isNum(now) && then !== 0) {
    const ch = (now - then) / then;
    const good = lowerIsBetter ? ch < 0 : ch > 0;
    tone = Math.abs(ch) < 0.03 ? 'flat' : good ? 'ok' : 'bad';
    delta = `${ch > 0 ? '▲' : ch < 0 ? '▼' : '▬'} ${Math.abs(Math.round(ch * 100))}%`;
  }
  return h('div', { class: 'pri-tn' }, h('div', { class: 'pri-label' }, label),
    h('div', { class: 'pri-tn-vals' }, h('span', { class: 'pri-tn-then' }, fmtv(then)), h('span', { class: 'pri-arrow-r', 'aria-hidden': 'true' }, '→'), h('strong', { class: 'pri-tn-now' }, nowNote || fmtv(now))),
    delta ? h('span', { class: 'pri-delta pri-delta-' + tone }, delta) : null);
}

// ROAS bars: peak / then / now with a tick for the product average over the same weeks
function roasBars(r, base) {
  const m = r.metrics || {};
  const rows = [];
  if (r.stretch && isNum(r.stretch.roas)) rows.push(['Peak', r.stretch.roas, r.stretch.product_roas, `Peak weeks ${r.stretch.start} to ${r.stretch.end}`]);
  if (isNum(m.roas_then)) rows.push(['Then', m.roas_then, base.roas_then, 'Days 31-90']);
  if (isNum(m.roas_30d)) rows.push(['Now', m.roas_30d, base.roas_30d, 'Last 30 days']);
  if (rows.length < 2) return null;
  const max = Math.max(...rows.flatMap((x) => [x[1], x[2]].filter(isNum))) * 1.12;
  const w = (v) => Math.max(0, Math.min(100, (v / max) * 100)).toFixed(1) + '%';
  return h('div', { class: 'pri-bars', role: 'img', 'aria-label': 'ROAS ' + rows.map((x) => `${x[0].toLowerCase()} ${roasX(x[1])}${isNum(x[2]) ? ` against product ${roasX(x[2])}` : ''}`).join(', ') },
    h('div', { class: 'pri-bars-h pri-label' }, 'ROAS vs product average (tick)'),
    rows.map(([k, v, ref, tip]) => h('div', { class: 'pri-bar-row', title: tip },
      h('span', { class: 'pri-bar-k' }, k),
      h('span', { class: 'pri-bar-track' }, h('span', { class: 'pri-bar-fill pri-bar-' + (isNum(ref) && v >= ref ? 'ok' : 'bad'), style: { width: w(v) } }), isNum(ref) ? h('span', { class: 'pri-bar-tick', style: { left: w(ref) } }) : null),
      h('span', { class: 'pri-bar-v' }, roasX(v)))));
}

function thenNow(r, base) {
  const m = r.metrics || {};
  if (!r.then_now) return null;
  const idle = isNum(m.spend_30d) && m.spend_30d < 250;
  const any = [m.roas_then, m.roas_30d, m.cpa_then, m.cpa_30d].some(isNum);
  return h('section', { class: 'pri-card pri-thennow', 'data-testid': 'pri-thennow', 'aria-label': 'Then versus now' },
    h('h4', { class: 'pri-h' }, 'Then vs Now ', h('span', { class: 'pri-hnote' }, 'days 31-90 vs last 30 days')),
    any ? h('div', { class: 'pri-tns' },
      tnTile('ROAS', m.roas_then, m.roas_30d, roasX, false, idle ? 'idle' : null),
      tnTile('CPA', m.cpa_then, m.cpa_30d, fmt.money, true, idle ? 'idle' : null),
      tnTile('Share of product spend', m.share_then, m.share_now, pct, false)) : null,
    any ? roasBars(r, base) : null,
    h('p', { class: 'pri-tn-text' }, r.then_now));
}

function step(n, label, text, cls) {
  return h('li', { class: 'pri-step ' + (cls || '') }, h('span', { class: 'pri-step-n', 'aria-hidden': 'true' }, n), h('div', null, h('h5', { class: 'pri-step-h' }, label), h('p', null, text)));
}

function hookList(r, title, cls) {
  if (!(r.hooks && r.hooks.length)) return null;
  const items = r.hooks.map((k) => h('li', { class: 'pri-hook' },
    h('q', { class: 'pri-quote' }, k.ad_id != null ? h('a', { href: href('/ad/' + encodeURIComponent(k.ad_id), {}), title: k.ad }, k.hook) : k.hook),
    k.spend != null ? h('span', { class: 'pri-hookn' }, ' ', fmt.money(k.spend), ' at ', roasPill(k.roas, null)) : h('span', { class: 'pri-hookn' }, ` (${k.line_code})`)));
  return h('section', { class: 'pri-card pri-hooks ' + (cls || ''), 'data-testid': 'pri-hooks' }, h('h4', { class: 'pri-h' }, title), h('ul', { class: 'pri-list' }, limited(items, 3, 'hooks')));
}

function winnersTable(r, base) {
  const ws = r.former_winners;
  if (!(ws && ws.length)) return null;
  const rows = ws.map((w) => h('tr', { class: 'pri-w' },
    h('td', { class: 'pri-w-name' }, w.ad_id != null ? h('a', { href: href('/ad/' + encodeURIComponent(w.ad_id), {}) }, w.ad) : w.ad),
    h('td', { class: 'pri-w-num', 'data-label': 'Spend' }, fmt.money(w.spend)),
    h('td', { 'data-label': 'ROAS' }, roasPill(w.roas, base.roas)),
    h('td', { class: 'pri-w-peak', 'data-label': 'Peak week of' }, w.peak_week || '–'),
    h('td', { 'data-label': 'Status' }, h('span', { class: 'pri-badge pri-badge-' + (w.kind === 'stopped' ? 'bad' : 'fade') }, w.kind === 'stopped' && w.stopped ? 'stopped ' + w.stopped : w.kind), w.kind !== 'stopped' && w.stopped ? h('span', { class: 'pri-hookn' }, ' stopped ' + w.stopped) : null)));
  const shown = limited(rows, 3, 'ads');
  const trs = shown.filter((x) => x.tagName === 'TR'); const btn = shown.find((x) => x.tagName === 'BUTTON');
  const ws2 = r.winners_summary;
  return h('section', { class: 'pri-card pri-winners', 'data-testid': 'pri-winners' }, h('h4', { class: 'pri-h' }, 'Former winning ads'),
    h('table', { class: 'pri-wt' }, h('tbody', null, h('tr', { class: 'pri-wth' }, ['Ad', 'Spend', 'ROAS', 'Peak week of', 'Status'].map((t) => h('th', { scope: 'col' }, t))), trs)), btn || null,
    ws2 ? h('p', { class: 'pri-hookn pri-wsum' }, `${ws2.still_working} of ${ws2.winners} winning ads still working; new ads in the last 30 days: ${ws2.launches_last_30d}${ws2.launches_in_peak != null ? ' (' + ws2.launches_in_peak + ' launched during the working stretch)' : ''}.`) : null);
}

function competitorBadges(c) {
  const b = (n, t, cls) => h('span', { class: 'pri-badge ' + (cls || 'pri-badge-info') }, h('strong', null, String(n)), ' ' + t);
  return h('section', { class: 'pri-card pri-others', 'data-testid': 'pri-others', 'aria-label': 'What others are running: ' + c.text },
    h('h4', { class: 'pri-h' }, 'What others are running'),
    h('div', { class: 'pri-badges' }, b(c.competitors, c.competitors === 1 ? 'competitor' : 'competitors'), b(c.concepts, c.concepts === 1 ? 'concept' : 'concepts'), b(c.long_running, 'running 60+ days', c.long_running ? 'pri-badge-ok' : 'pri-badge-flat'),
      (c.brands || []).length ? h('span', { class: 'pri-hookn' }, (c.brands || []).join(', ')) : null),
    h('p', { class: 'pri-hookn' }, '(running a long time, not proven performance)'));
}

export function statCards(r) {
  const groups = numbers(r);
  if (!groups.length) return null;
  return h('section', { class: 'pri-card pri-statsec', 'aria-label': 'Numbers' }, groups.map(([g, items]) => h('div', { class: 'pri-sg' }, h('h4', { class: 'pri-label' }, g),
    h('dl', { class: 'pri-cards' }, items.map(([k, v, sub]) => h('div', { class: 'pri-sc' }, h('dt', { class: 'pri-label' }, k), h('dd', null, h('strong', null, v), sub ? ' ' : null, sub ? h('span', { class: 'pri-hookn' }, sub) : null)))))));
}

// two columns of cards; a column with nothing in it is left out so the other one uses the full width
function cols(...groups) {
  const cs = groups.map((g) => g.filter(Boolean)).filter((g) => g.length).map((g) => h('div', { class: 'pri-col' }, g));
  return cs.length ? h('div', { class: 'pri-cols' }, cs) : null;
}

export function detail(r, d) {
  const base = ((d && d.baselines) || {})[r.product] || {};
  const tone = toneOf(r);
  const faded = !!r.when_it_worked;
  // the summary sentence of a faded row is "<STATUS prefix> <when it worked> <how it stopped>": show only the prefix, the timeline below carries the rest word for word
  let why = r.why;
  if (faded && why && why.endsWith(`${r.when_it_worked} ${r.how_it_stopped}`)) why = why.slice(0, why.length - `${r.when_it_worked} ${r.how_it_stopped}`.length).trim();
  const flagPills = (r.flags || []).map((f) => h('span', { class: 'pri-pill pri-pill-fade' }, f));
  const play = faded ? h('section', { class: 'pri-cta', 'data-testid': 'pri-play' }, h('h4', { class: 'pri-cta-h' }, h('span', { 'aria-hidden': 'true' }, '→ '), 'Revive play: '), h('p', null, r.revive_play))
    : hookList(r, r.source ? 'Competitor hooks seen' : r.section === 'test' ? 'Hooks to borrow (same product, adjacent winner)' : r.section === 'make_more' ? 'Proven hooks to iterate from' : 'Hooks from the best ads on this line', 'pri-cta');
  const hooksLow = faded ? hookList(r, 'Hooks from the working period') : null;
  return h('div', { class: 'pri-panel pri-tone-' + tone },
    h('div', { class: 'pri-id' },
      h('div', { class: 'pri-chips' }, h('span', { class: 'pri-chip-prod' }, (base.product_name || r.product_name || r.product)), h('span', { class: 'pri-sec pri-sec-' + r.section, title: actionRule(r.section) }, SECTION_LABEL[r.section]), r.section === 'add' && r.source ? h('span', { class: 'pri-pill pri-pill-flat', 'data-testid': 'pri-source' }, r.source) : null, h('span', { class: 'pri-status pri-st-' + tone, title: statusRule(r.status) }, r.status)),
      h('div', { class: 'pri-tiles' }, tile('Market Cluster', r.cluster, r.cluster_description), tile('ICP', r.icp, r.icp_description), tile('Pain Point', r.pain_point, r.pain_description),
        r.story ? h('div', { class: 'pri-tile pri-tile-story' }, h('div', { class: 'pri-tile-h' }, h('span', { class: 'pri-label' }, 'Story')), h('div', { class: 'pri-story' }, r.story)) : null)),
    h('div', { class: 'pri-verdict' }, h('div', { class: 'pri-verdict-k' }, keyNumber(r, d || { baselines: {} })), why ? h('p', { class: 'pri-verdict-why', 'data-testid': 'pri-why' }, why) : null),
    play,
    cols([thenNow(r, base), faded ? h('section', { class: 'pri-card', 'data-testid': 'pri-faded', 'aria-label': 'How it played out' }, h('h4', { class: 'pri-h' }, 'Timeline'),
          h('ol', { class: 'pri-steps' }, step('1', 'When it worked: ', r.when_it_worked, 'pri-step-ok'), step('2', 'When and how it stopped: ', r.how_it_stopped, 'pri-step-bad'), step('3', 'Diagnosis (consistent with): ', r.diagnosis, 'pri-step-flat'))) : null],
      [winnersTable(r, base), r.competitor ? competitorBadges(r.competitor) : null]),
    statCards(r),
    hooksLow,
    h('div', { class: 'pri-foot' }, flagPills.length ? h('span', { class: 'pri-foot-g' }, h('span', { class: 'pri-k' }, 'Flags'), flagPills) : null,
      h('span', { class: 'pri-pill pri-pill-flat' }, 'Line ' + r.line_code), h('span', { class: 'pri-pill pri-pill-flat' }, 'Confidence: ' + r.confidence), h('span', { class: 'pri-pill pri-pill-flat' }, 'Score ' + r.score)));
}
export const SECTION_LABEL = Object.fromEntries(SECTIONS);

// ---------- the panel of one production assignment (Production page and vendor page) ----------
// Example ads: up to p.examples_max ads of the concept, top by spend, each with its spend and return on ad spend; the best performer is marked. `ad_id` is null for a vendor (the ad page needs a login): the name links to `public_url` (new tab) when there is one and it is a https Facebook / Instagram link, else it is plain text. The server already
// sends only such links; the page checks again, so no other host (Google Drive, Ads Manager) can ever be linked for a vendor.
const PUBLIC_HOSTS = ['facebook.com', 'www.facebook.com', 'fb.watch', 'instagram.com', 'www.instagram.com'];
export function publicLink(u) {
  if (typeof u !== 'string' || !u || u.length > 2000) return null;
  try { const x = new URL(u); return x.protocol === 'https:' && !x.username && !x.password && !x.port && PUBLIC_HOSTS.includes(x.hostname) ? x.href : null; } catch (e) { return null; }
}
export function examplesSection(p) {
  const ex = p.examples || [];
  const max = p.examples_max || 10;
  const rows = ex.map((e) => h('tr', { class: 'pri-w pri-ex', 'data-testid': 'pri-example', 'data-best': e.best ? '1' : '' },
    h('td', { class: 'pri-w-name', 'data-label': 'Ad' }, e.ad_id != null ? h('a', { href: href('/ad/' + encodeURIComponent(e.ad_id), {}), 'data-testid': 'pri-example-link' }, e.ad)
      : publicLink(e.public_url) ? h('a', { href: publicLink(e.public_url), target: '_blank', rel: 'noopener noreferrer', 'data-testid': 'pri-example-public' }, e.ad, ' \u2197') : h('span', null, e.ad),
      e.best ? h('span', { class: 'pri-badge pri-badge-ok pri-ex-best', 'data-testid': 'pri-example-best' }, 'Best performer') : null),
    h('td', { class: 'pri-w-num', 'data-label': 'Spend' }, fmt.money(e.spend)),
    h('td', { 'data-label': 'Return on ad spend' }, roasX(e.roas))));
  return h('section', { class: 'pri-card pri-examples', 'data-testid': 'pri-examples', 'aria-label': 'Example ads' }, h('h4', { class: 'pri-h' }, 'Example ads ', h('span', { class: 'pri-hnote' }, `top ${max} by spend since launch`)),
    ex.length ? h('table', { class: 'pri-wt' }, h('tbody', null, h('tr', { class: 'pri-wth' }, ['Ad', 'Spend', 'Return on ad spend'].map((t) => h('th', { scope: 'col' }, t))), rows))
      : h('p', { class: 'pri-hookn', 'data-testid': 'pri-examples-none' }, 'No ads with spend are tagged to this concept yet.'));
}

// the panel for a concept that is not in the latest snapshot: what the assignment stored, and a plain note
function storedPanel(r, message) {
  return h('div', { class: 'pri-panel pri-tone-flat', 'data-testid': 'pri-stored-panel' },
    h('div', { class: 'pri-id' }, h('div', { class: 'pri-chips' }, h('span', { class: 'pri-chip-prod' }, r.product_name || r.product)),
      h('div', { class: 'pri-tiles' }, tile('Market Cluster', r.cluster), tile('ICP', r.icp), tile('Pain Point', r.pain_point),
        r.story ? h('div', { class: 'pri-tile pri-tile-story' }, h('div', { class: 'pri-tile-h' }, h('span', { class: 'pri-label' }, 'Story')), h('div', { class: 'pri-story' }, r.story)) : null)),
    h('p', { class: 'pri-hookn', 'data-testid': 'pri-no-numbers' }, message));
}

// p = the detail payload of /api/priorities/assignments/<id>/detail (or the vendor version): the same panel the concept has on /priorities, plus the example ads
export function assignmentPanel(p) {
  setDefs(p.definitions);
  const r = p.row;
  const panel = !p.current ? storedPanel(r, p.message || 'No current numbers for this concept.') : r.manual ? manualPanel(r, null) : detail(r, { baselines: p.baselines || {} });
  panel.append(examplesSection(p));
  return panel;
}

// A Details button (and optionally the concept name as a second control) that opens `host` and loads the panel on first open. `wrap` is what is shown / hidden (the host itself, or the table row that
// holds it). The payload is cached by `key`, so a redraw of the page re-opens a row without another request.
const CACHE = new Map();
let xid = 0;
export function expander({ key, fetchDetail, host, wrap = host, startOpen = false, onToggle = null }) {
  host.id = host.id || 'pri-xd-' + (++xid);
  const controls = [];
  const sync = (open) => controls.forEach((c) => { c.setAttribute('aria-expanded', String(open)); if (c.dataset.label !== 'name') c.textContent = open ? 'Hide details' : 'Details'; });
  async function fill() {
    if (host.dataset.loaded === '1') return;
    host.replaceChildren(h('p', { class: 'pri-hookn', role: 'status', 'data-testid': 'pri-x-loading' }, 'Loading details…'));
    try {
      if (!CACHE.has(key)) CACHE.set(key, await fetchDetail());
      host.replaceChildren(assignmentPanel(CACHE.get(key)));
      host.dataset.loaded = '1';
    } catch (e) {
      if (e && e.name === 'AbortError') return;
      host.replaceChildren(h('p', { class: 'pri-err', role: 'alert', 'data-testid': 'pri-x-error' }, 'Could not load the details: ' + ((e && e.message) || 'error') + ' Close and open again to retry.'));
    }
  }
  const set = (open) => { wrap.hidden = !open; sync(open); if (open) fill(); if (onToggle) onToggle(open); };
  const toggle = () => set(wrap.hidden);
  const mk = (cls, text, name) => {
    const b = h('button', { type: 'button', class: cls, 'aria-expanded': 'false', 'aria-controls': host.id, 'data-testid': name ? 'pri-x-name' : 'pri-x-toggle', onclick: toggle }, text);
    if (name) b.dataset.label = 'name';
    controls.push(b);
    return b;
  };
  const api_ = { button: () => mk('btn prod-small', 'Details'), nameButton: (text) => mk('prod-namebtn', text, true) };
  api_.open = (o) => set(o);
  if (startOpen) queueMicrotask(() => set(true)); else wrap.hidden = true;
  return api_;
}
