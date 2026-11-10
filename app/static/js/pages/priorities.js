// Creative priorities (/priorities): the weekly one-sheeter as tables. Each section (Was working now isn't / Iterate / New / Proposed new clusters / Competitor angles / Stop) is a table with one
// row per concept, ALL COLLAPSED on load (nothing is remembered). A row shows Market Cluster | ICP | Pain Point | Story (full text) | Status | Concepts | one key number; the chevron button (or a click on
// the row) opens a detail panel directly under it with everything else. Print CSS expands every panel. Class names and test ids start with `pri-` (never ad-, ads-, sponsor, banner, promo).
import { h, fmt, isNum, href, navigate, qs } from '../util.js';
import { api } from '../api.js';
import { showControls } from '../controls.js';
import { dayOf, manualPanel, addManualNotes, manualStrip, manualControls } from './priorities_manual.js';

const PRODUCT_ORDER = ['vita-bull', 'beef-liver', 'boneflex'];
const value_keys = ['product', 'section', 'cluster', 'icp', 'pain', 'story', 'status', 'concepts', 'key', 'spend14', 'roas14'];
const SECTIONS = [['faded', 'Was working, now isn’t', 'Lines with a sustained strong stretch (3+ weeks above the product average) that are now below average, collapsed or idle: when it worked, how it stopped, the former winners, a hedged diagnosis and a revive play.'],
                  ['iterate', 'Iterate on what is working', 'WORKING NOW and RISING lines: their ads beat the product average on enough spend; more concepts should help.'],
                  ['new', 'New and untested clusters', 'Lines with no ads (or almost none), ranked by evidence the demand exists (our adjacent winners, other products, competitors).'],
                  ['proposal', 'Proposed new market clusters', 'Drafts in the Communication Overview shape for Josh’s approval (NEW CLUSTER or a new line inside an existing cluster). Evidence is competitor activity and our adjacent results only; no performance is claimed.'],
                  ['competitor', 'Competitor angles we have not made', 'Angles competitors run that are not on our Communication Overview. Running a long time is a signal, not proof it performs.'],
                  ['stop', 'Stop or deprioritise', 'NOT WORKING and never worked: enough spend, weak over its history.'],
                  ['other', 'Other', 'Concepts added by hand that do not fit a section above.']];
const STATUS_CLASS = { 'FADED': 'fade', 'COLLAPSED': 'bad', 'STOPPED': 'flat', 'WORKING NOW': 'ok', 'RISING': 'up', 'FADING / WAS WORKING': 'fade', 'NOT WORKING': 'bad', 'AVERAGE': 'flat', 'TOO EARLY': 'flat', 'UNTESTED / UNDER-TESTED': 'new', 'PROPOSED (draft)': 'new', 'MANUAL': 'manual' };
const roasX = (v) => (isNum(v) ? fmt.roas(v) + 'x' : '–');
const pct = (v) => (isNum(v) ? fmt.pct(v) : '–');
// share of n in d as text, one decimal under 10% ("2.2%"), none above ("54%"); a dash when there is no denominator
const share = (n, dn) => { if (!dn) return '\u2013'; const v = 100 * n / dn; return (v > 0 && v < 10 ? v.toFixed(1) : Math.round(v)) + '%'; };
const compact$ = (v) => (v >= 1e6 ? '$' + (v / 1e6).toFixed(2) + 'M' : v >= 1e4 ? '$' + Math.round(v / 1e3) + 'K' : fmt.money(v));
// the all-time funnel text for a product baseline or a row's metrics.funnel: launched, under $100, $100+, $10K+
const funnelText = (f) => `Launched ${fmt.int(f.launched)} \u00b7 under $100: ${fmt.int(f.under_tested)} (${share(f.under_tested, f.launched)}) \u00b7 $100+: ${fmt.int(f.tested)} \u00b7 $10K+: ${fmt.int(f.top)} (${share(f.top, f.launched)})`;
const funnel30Text = (f) => `${fmt.int(f.active)} ads spent \u00b7 under $100: ${fmt.int(f.under_tested)} (${share(f.under_tested, f.active)}) \u00b7 $100+: ${fmt.int(f.tested)} \u00b7 $10K+: ${fmt.int(f.top)} (${share(f.top, f.active)})`;
// small stacked bar: under $100 | $100 to $10K | $10K+ (widths are shares of the total; the $10K+ slice never drops under 3px so it stays visible)
const funnelBar = (under, tested, top, total, label) => h('div', { class: 'pri-fbar2', role: 'img', 'aria-label': label, 'data-testid': 'pri-funnel-bar' },
  h('span', { class: 'pri-fb-u', style: { flexGrow: String(under) } }), h('span', { class: 'pri-fb-m', style: { flexGrow: String(Math.max(tested - top, 0)) } }), h('span', { class: 'pri-fb-t', style: { flexGrow: String(top) } }));

// stat cards for the detail panel: [[group, [[label, value, sub?], ...]], ...]; labels and values are the same facts the old inline row showed
function numbers(r) {
  const m = r.metrics || {};
  if (r.section === 'iterate' || r.section === 'faded' || r.section === 'stop') {
    return [['Performance', [['Spend', fmt.money(m.spend)], ['ROAS (all time)', roasX(m.roas)], ['CPA (all time)', fmt.money(m.cpa)], ['Upstack new-customer ROAS', roasX(m.nc_roas)], ['ROAS, strong tags only', roasX(m.roas_strong_tags)]]],
      ['Testing', m.funnel
        ? [['Ads launched', fmt.int(m.funnel.launched)], ['Under $100 spend', fmt.int(m.funnel.under_tested), `(${share(m.funnel.under_tested, m.funnel.launched)} of launched)`], ['Ads tested ($100+)', fmt.int(m.funnel.tested)],
           ['Ads that reached $10K+', fmt.int(m.funnel.top), `(${share(m.funnel.top, m.funnel.launched)} of launched, ${share(m.funnel.top, m.funnel.tested)} of tested)`], ['New ads, 30 days', fmt.int(m.launches_30d)]]
        : [['Ads tested ($100+)', fmt.int(m.judged)], ['Hit rate ($1,000+, mature ads)', pct(m.hit_rate), `(${m.hits}/${m.mature})`], ['New ads, 30 days', fmt.int(m.launches_30d)]]],
      ['Live', [...(isNum(m.spend_14d) ? [['Last 14 days', roasX(m.roas_14d), `on ${fmt.money(m.spend_14d)}${isNum(m.roas_14d) ? '' : ' (under $100: no ratio)'}`]] : []), ['Last 30 days', roasX(m.roas_30d), `on ${fmt.money(m.spend_30d)}`], ['Prior 30 days', roasX(m.roas_prior30), `on ${fmt.money(m.spend_prior30)}`], ['Live ads', fmt.int(m.live_ads)]].concat(m.funnel && m.funnel.active_30d != null ? [['Ads that spent, 30 days', fmt.int(m.funnel.active_30d)], ['Reached $10K+ in the 30 days', fmt.int(m.funnel.top_30d)]] : [])]];
  }
  if (r.section === 'competitor' || r.section === 'proposal') return [];
  return [['Performance', [['Ads tagged', fmt.int(m.tagged_ads)], ['Spend so far', fmt.money(m.spend)]]]];
}

// the one number on the collapsed row
function keyNumber(r, d) {
  if (r.manual) return r.why_now || `Added ${dayOf(r.created_at)}`;
  const m = r.metrics || {};
  const b = (d.baselines || {})[r.product] || {};
  if (r.section === 'faded') {
    const now = m.spend_30d >= 250 ? roasX(m.roas_30d) + ' now' : 'idle now';
    return r.stretch ? `ROAS ${roasX(r.stretch.roas)} at peak \u2192 ${now}` : `No weekly peak; lifetime ${roasX(m.roas)} \u2192 ${now}`;
  }
  if (r.section === 'iterate') return `${roasX(m.roas_30d)} last 30 days vs ${roasX(b.roas_30d)} product`;
  if (r.section === 'stop') return `${roasX(m.roas)} lifetime vs ${roasX(b.roas)} product`;
  if (r.competitor) return r.competitor.text;
  if (r.section === 'new') return m.spend > 0 ? `${fmt.money(m.spend)} spent so far` : 'No spend yet';
  return 'Draft';
}

// ---------- detail panel (the expanded row) ----------
// Reading order: identity strip -> verdict -> the thing to act on (revive play / hooks) -> then vs now -> timeline -> former winners -> competitors -> stat cards -> footer pills.
// Every fact the panel always carried is still here; long definitions sit behind keyboard-operable "?" disclosures and long lists behind "Show all".
const toneOf = (r) => STATUS_CLASS[r.status] || 'flat';
const TAG_HELP = { 'Market Cluster': 'cluster_description', 'ICP': 'icp_description', 'Pain Point': 'pain_description' };

function tile(label, value, desc) {
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

function statCards(r) {
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

function detail(r, d) {
  const base = ((d && d.baselines) || {})[r.product] || {};
  const tone = toneOf(r);
  const faded = !!r.when_it_worked;
  // the summary sentence of a faded row is "<STATUS prefix> <when it worked> <how it stopped>": show only the prefix, the timeline below carries the rest word for word
  let why = r.why;
  if (faded && why && why.endsWith(`${r.when_it_worked} ${r.how_it_stopped}`)) why = why.slice(0, why.length - `${r.when_it_worked} ${r.how_it_stopped}`.length).trim();
  const flagPills = (r.flags || []).map((f) => h('span', { class: 'pri-pill pri-pill-fade' }, f));
  const play = faded ? h('section', { class: 'pri-cta', 'data-testid': 'pri-play' }, h('h4', { class: 'pri-cta-h' }, h('span', { 'aria-hidden': 'true' }, '→ '), 'Revive play: '), h('p', null, r.revive_play))
    : hookList(r, r.section === 'proposal' ? 'Competitor hooks seen' : r.section === 'new' ? 'Hooks to borrow (same product, adjacent winner)' : 'Proven hooks to iterate from', 'pri-cta');
  const hooksLow = faded ? hookList(r, 'Hooks from the working period') : null;
  return h('div', { class: 'pri-panel pri-tone-' + tone },
    h('div', { class: 'pri-id' },
      h('div', { class: 'pri-chips' }, h('span', { class: 'pri-chip-prod' }, (base.product_name || r.product_name || r.product)), h('span', { class: 'pri-sec pri-sec-' + r.section }, SECTION_LABEL[r.section]), h('span', { class: 'pri-status pri-st-' + tone }, r.status)),
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

// ---------- saved views: helpers ----------
const LAST_VIEW = 'pri-last-view';
const FLASH = 'pri-flash';
const lastView = { get() { try { return localStorage.getItem(LAST_VIEW) || ''; } catch (e) { return ''; } }, set(t) { try { if (t) localStorage.setItem(LAST_VIEW, t); else localStorage.removeItem(LAST_VIEW); } catch (e) { /* blocked */ } } };
const flash = { set(t) { try { sessionStorage.setItem(FLASH, t); } catch (e) { /* blocked */ } }, take() { try { const t = sessionStorage.getItem(FLASH); sessionStorage.removeItem(FLASH); return t; } catch (e) { return null; } } };
const defaultViewName = () => { const t = new Date(); return `Priorities - ${t.getMonth() + 1}/${t.getDate()}/${String(t.getFullYear() % 100).padStart(2, '0')}`; };
const viewUrl = (tok, extra) => href('/priorities', { view: tok, ...(extra || {}) });

// One small modal for naming a view (Save view, Rename): focused, prefilled and selected input, Enter saves, Esc cancels, 120 characters, empty -> the default name.
// `kinds` = [[value, label, disabled?], ...] adds a radio group (what to save). Resolves {name, kind} or null.
function nameDialog({ title, initial, okLabel, kinds = null, kind = null, note = null, placeholder = 'Madre - 11/10/26 Priorities', field = 'Name', max = 120, emptyOk = false }) {
  return new Promise((resolve) => {
    const input = h('input', { type: 'text', class: 'pri-name', maxlength: String(max), 'aria-label': field === 'Name' ? 'View name' : field, 'data-testid': 'pri-view-name', placeholder, value: initial || '' });
    let chosen = kind || (kinds && (kinds.find((k) => !k[2]) || kinds[0])[0]);
    const radios = kinds ? h('fieldset', { class: 'pri-kinds' }, h('legend', { class: 'pri-k' }, 'What to save'), kinds.map(([v, label, dis]) => h('label', { class: 'pri-kind' },
      h('input', { type: 'radio', name: 'pri-kind', value: v, checked: v === chosen, disabled: !!dis, 'data-testid': 'pri-kind-' + v, onchange: () => { chosen = v; } }), ' ', label))) : null;
    const cancel = h('button', { type: 'button', class: 'btn', 'data-testid': 'pri-dialog-cancel' }, 'Cancel');
    const form = h('form', { method: 'dialog', class: 'pri-dform', onsubmit: (e) => { e.preventDefault(); done({ name: input.value.trim() || (emptyOk ? '' : defaultViewName()), kind: chosen }); } },
      h('h2', { class: 'pri-dtitle', id: 'pri-dtitle' }, title), note ? h('p', { class: 'pri-dnote', 'data-testid': 'pri-dialog-note' }, note) : null, radios,
      h('label', { class: 'pri-dlabel' }, h('span', { class: 'pri-k' }, field), input),
      h('div', { class: 'pri-dbtns' }, cancel, h('button', { type: 'submit', class: 'btn primary', 'data-testid': 'pri-dialog-save' }, okLabel || 'Save')));
    const dlg = h('dialog', { class: 'pri-dialog', 'aria-labelledby': 'pri-dtitle', 'data-testid': 'pri-name-dialog' }, form);
    let finished = false;
    function done(v) { if (finished) return; finished = true; try { dlg.close(); } catch (e) { /* already closed */ } dlg.remove(); resolve(v); }
    cancel.addEventListener('click', () => done(null));
    dlg.addEventListener('cancel', (e) => { e.preventDefault(); done(null); });                       // Esc
    dlg.addEventListener('click', (e) => { if (e.target === dlg) done(null); });                      // click on the backdrop
    document.body.appendChild(dlg);
    if (typeof dlg.showModal === 'function') dlg.showModal(); else dlg.setAttribute('open', '');
    input.focus(); input.select();
  });
}

// the panel under a row: manual rows have their own render function; an auto row gets the notes of any manual concept with the same four tags appended
const panelFor = (r, d, mctl) => { if (r.manual) return manualPanel(r, mctl); const p = detail(r, d); addManualNotes(p, r, d); return p; };

const SECTION_LABEL = { faded: 'Was working, now isn’t', iterate: 'Iterate on what is working', new: 'New and untested', proposal: 'Proposed new cluster', competitor: 'Competitor angle', stop: 'Stop', other: 'Other' };
const SECTION_ORDER = SECTIONS.map(([k]) => k);
const SECTION_NOTE = Object.fromEntries(SECTIONS.map(([k, , sub]) => [k, sub]));
const COLS = [['product', 'Product'], ['section', 'Section'], ['cluster', 'Market Cluster'], ['icp', 'ICP'], ['pain', 'Pain Point'], ['story', 'Story'], ['status', 'Status'], ['concepts', 'Concepts'], ['key', 'Key number'], ['spend14', 'Spend \u00b7 14d'], ['roas14', 'ROAS \u00b7 14d']];

// last 14 days (Meta-reported, raw purchase value / spend, not shrunk): spend always shown when the row has data, ROAS only with $100+ spend in the window (else a dash)
const has14 = (r) => !r.manual && r.metrics && isNum(r.metrics.spend_14d);
const roas14Of = (r) => (has14(r) && isNum(r.metrics.roas_14d) ? r.metrics.roas_14d : null);
function roas14Cell(r, d) {
  const v = roas14Of(r);
  if (v === null) return has14(r) ? h('span', { class: 'pri-k', title: 'Under $100 spend in the last 14 days: no ratio shown' }, '\u2013') : '\u2013';
  const base = ((d.baselines || {})[r.product] || {}).roas_30d;
  const rel = isNum(base) && base ? v / base - 1 : 0;
  const tone = rel >= 0.05 ? 'up' : rel <= -0.05 ? 'down' : 'flat';
  return h('span', { class: 'pri-r14 pri-r14-' + tone, 'data-testid': 'pri-roas14', title: `${roasX(v)} vs ${roasX(base)} product average over the last 30 days` }, roasX(v), h('span', { class: 'pri-r14-a', 'aria-hidden': 'true' }, tone === 'up' ? ' \u25b2' : tone === 'down' ? ' \u25bc' : ''),
    h('span', { class: 'pri-vh' }, tone === 'up' ? ' above the product average' : tone === 'down' ? ' below the product average' : ' near the product average'));
}
const keyOf = (r) => r.key || [r.product, r.section, r.cluster, r.icp, r.pain_point].join('|');      // stable row key: unique per row in a snapshot; the server uses the same join for ?id=
const storeKey = (week, tok) => 'pri-selected-' + week + (tok ? '-' + tok : '');
function loadSel(week, valid, tok) {
  try { return new Set((JSON.parse(sessionStorage.getItem(storeKey(week, tok))) || []).filter((k) => valid.has(k))); } catch (e) { return new Set(); }
}
function saveSel(week, sel, tok) { try { sessionStorage.setItem(storeKey(week, tok), JSON.stringify([...sel])); } catch (e) { /* storage blocked: selection just is not remembered */ } }

let uid = 0;
function setOpen(btn, panelRow, open) {
  btn.setAttribute('aria-expanded', String(open));
  btn.setAttribute('aria-label', (open ? 'Collapse: ' : 'Expand: ') + btn.dataset.name);
  panelRow.hidden = !open;
  panelRow.previousElementSibling.classList.toggle('pri-open', open);
}

// one row (collapsed) + its detail row (hidden). `printed` rows have no toggle and an always-visible detail (the print-only copy).
function rowPair(r, d, printed = false, onPick = null, mctl = null) {
  const id = 'pri-detail-' + (++uid);
  const name = `${r.cluster}, ${r.icp}, ${r.pain_point}`;
  const cell = (label, cls, ...kids) => h('td', { class: 'pri-c pri-c-' + cls, 'data-label': label }, ...kids);
  const detailRow = h('tr', { class: 'pri-detail', id, hidden: !printed, 'data-testid': 'pri-detail' }, h('td', { colspan: '12' }, panelFor(r, d, r._from ? null : mctl)));
  const btn = h('button', { type: 'button', class: 'pri-toggle', 'aria-expanded': 'false', 'aria-controls': id, 'aria-label': 'Expand: ' + name, 'data-name': name, 'data-testid': 'pri-toggle' },
    h('span', { class: 'pri-chev', 'aria-hidden': 'true' }, '▸'));
  const pick = h('input', { type: 'checkbox', class: 'pri-pick', 'data-testid': 'pri-pick', 'aria-label': 'Select: ' + name, onchange: (e) => onPick && onPick(r, e.target.checked) });
  const tr = h('tr', { class: 'pri-row' + (r.manual ? ' pri-manual' + (r.done ? ' pri-done' : '') : ''), 'data-testid': 'pri-card', 'data-section': r.section, 'data-product': r.product, 'data-source': r.manual ? 'manual' : 'auto' },
    h('td', { class: 'pri-c pri-c-tog' }, printed || !onPick ? null : pick, printed ? null : btn),
    cell('Product', 'product', (d.baselines[r.product] || {}).product_name || r.product),
    cell('Section', 'section', h('span', { class: 'pri-sec pri-sec-' + r.section, title: SECTION_NOTE[r.section] }, SECTION_LABEL[r.section]), r.manual ? h('span', { class: 'pri-mbadge', 'data-testid': 'pri-manual-badge' }, 'Manual') : null),
    cell('Market Cluster', 'cluster', r.cluster, r._from ? h('span', { class: 'pri-from', 'data-testid': 'pri-from', title: 'No longer in the current snapshot: the numbers are the frozen copy saved with the view' }, ' from ' + r._from) : null), cell('ICP', 'icp', r.icp), cell('Pain Point', 'pain', r.pain_point), cell('Story', 'story', r.story || '–'),
    cell('Status', 'status', h('span', { class: 'pri-status pri-st-' + (STATUS_CLASS[r.status] || 'flat'), 'data-testid': 'pri-status' }, r.manual && r.done ? 'MANUAL (done)' : r.status)),
    cell('Concepts', 'concepts', r.manual ? '\u2013' : r.concepts ? `${r.concepts} concept${r.concepts === 1 ? '' : 's'}` : 'None'),
    cell('Key number', 'key', keyNumber(r, d)),
    cell('Spend \u00b7 14d', 'spend14', has14(r) ? fmt.money(r.metrics.spend_14d) : '\u2013'),
    cell('ROAS \u00b7 14d', 'roas14', roas14Cell(r, d)));
  if (!printed) {
    const toggle = () => setOpen(btn, detailRow, btn.getAttribute('aria-expanded') !== 'true');
    btn.addEventListener('click', (e) => { e.stopPropagation(); toggle(); });
    tr.addEventListener('click', (e) => { if (!e.target.closest('a, button, input, label')) toggle(); });
  }
  return { tr, detailRow, r, pick };
}

function render(d, view = null, opts = {}) {
  const vm = opts.vm || { views: [], active: null, mode: 'latest', notice: null };
  const readonly = !!view;                                                               // a saved view: only its rows, no selection or editing, pinned to its stored snapshot
  const slugs = PRODUCT_ORDER.filter((s) => d.rows.some((r) => r.product === s));
  const sections = SECTION_ORDER.filter((k) => d.rows.some((r) => r.section === k));
  const pOrder = Object.fromEntries(PRODUCT_ORDER.map((s, i) => [s, i]));
  const sOrder = Object.fromEntries(SECTION_ORDER.map((s, i) => [s, i]));
  const act = vm.active || null;                                                           // the saved view switched on in place (/priorities?view=<token>), else null = All concepts
  const tok = act ? act.view.token : '';
  const sel = readonly ? new Set(d.rows.map(keyOf)) : loadSel(d.week_start, new Set(d.rows.map(keyOf)), tok);
  const isSel = (r) => sel.has(keyOf(r));
  const mctl = manualControls(d, readonly, opts, (r) => { sel.delete(keyOf(r)); saveSel(d.week_start, sel, tok); });
  const pairs = d.rows.map((r) => rowPair(r, d, false, readonly ? null : (row, on) => { if (on) sel.add(keyOf(row)); else sel.delete(keyOf(row)); selChanged(); }, mctl));
  let shownList = [];
  const carry = opts.carry || {};
  const sf = act && act.view.kind === 'filters' && act.view.filters ? act.view.filters : null;      // a filter view starts from its saved filters (applied live to whatever rows exist now)
  const pick0 = (saved, all) => { const keep = (saved || []).filter((x) => all.includes(x)); return new Set(keep.length ? keep : all); };
  const state = { products: pick0(sf && sf.products, slugs), sections: pick0(sf && sf.sections, sections), q: carry.q || (sf ? sf.q || '' : ''), key: carry.key || (sf && value_keys.includes(sf.key) ? sf.key : null),
                  dir: carry.dir || (sf && sf.dir === -1 ? -1 : 1), showDone: !!carry.showDone, onlySel: !!carry.onlySel, brief: carry.brief || 'all', status: carry.status !== undefined ? carry.status : (sf && sf.status) || null };
  const allOn = (set, all) => (all.every((x) => set.has(x)) ? [] : all.filter((x) => set.has(x)));
  const currentFilters = () => ({ products: allOn(state.products, slugs), sections: allOn(state.sections, sections), q: state.q.trim(), key: state.key, dir: state.dir, status: state.status || null });
  if (opts.ref) opts.ref.state = state;
  const value = {
    product: (r) => pOrder[r.product], section: (r) => sOrder[r.section], cluster: (r) => r.cluster.toLowerCase(), icp: (r) => r.icp.toLowerCase(), pain: (r) => r.pain_point.toLowerCase(),
    story: (r) => (r.story || '').toLowerCase(), status: (r) => r.status.toLowerCase(), concepts: (r) => r.concepts || 0, key: (r) => keyNumber(r, d).toLowerCase(), spend14: (r) => (has14(r) ? r.metrics.spend_14d : -1), roas14: (r) => { const v = roas14Of(r); return v === null ? -1 : v; }, briefed: (r) => (isBriefed(r) ? 1 : 0),
  };
  const tbody = h('tbody', { 'data-testid': 'pri-body' });
  const count = h('span', { class: 'pri-shown', 'aria-live': 'polite', 'data-testid': 'pri-shown' });
  const sortBtns = {};
  const bTok = readonly ? view.token : tok;                                                 // briefed checkmarks live on a saved view: the share page and the in-page view switcher, never on All concepts
  const hasBriefed = !!bTok;
  const briefed = { ...(((readonly ? view : act && act.view) || {}).briefed || {}) };
  const isBriefed = (r) => !!briefed[keyOf(r)];
  const briefTxt = (r) => { const b = briefed[keyOf(r)]; return b ? `Briefed ${b.at.slice(0, 10)}${b.note ? ': ' + b.note : ''}` : 'Not briefed'; };
  const briefProgress = h('span', { class: 'pri-bprog', 'data-testid': 'pri-brief-progress', 'aria-live': 'polite' });
  // the per-row Briefed toggle (separate from the selection checkbox): saved on the server per view + row key, so everyone with the link sees the same
  const briefErr = h('span', { class: 'pri-err', 'data-testid': 'pri-brief-error', role: 'alert' });
  async function setBriefed(p, on, note) {
    const key = keyOf(p.r);
    try {
      const res = await api(`/api/priorities/views/${bTok}/briefed`, {}, { method: 'PATCH', body: { key, briefed: on, note: note === undefined ? ((briefed[key] || {}).note || '') : note } });
      if (res.briefed) briefed[key] = { at: res.at, note: res.note }; else delete briefed[key];
      briefErr.textContent = '';
      paintBrief(p); apply(); buildPrint();
    } catch (e) { briefErr.textContent = 'Could not save the briefed mark: ' + (e.message || 'error'); }
  }
  function paintBrief(p) {
    const on = isBriefed(p.r); const b = briefed[keyOf(p.r)] || {};
    p.tr.classList.toggle('pri-briefed', on);
    p.briefTd.replaceChildren(...[
      h('button', { type: 'button', class: 'pri-brief' + (on ? ' on' : ''), 'aria-pressed': String(on), 'data-testid': 'pri-brief-toggle', title: on ? `${briefTxt(p.r)}. Click to undo.` : 'Mark this concept as briefed in (sent to the team)', 'aria-label': (on ? 'Briefed, undo: ' : 'Mark briefed: ') + p.tr.querySelector('.pri-c-pain').textContent,
        onclick: () => setBriefed(p, !on) }, h('span', { class: 'pri-bmark', 'aria-hidden': 'true' }, '\u2713'), on ? 'Briefed' : 'Mark briefed'),
      on ? h('span', { class: 'pri-bwhen', 'data-testid': 'pri-brief-when' }, ' ' + b.at.slice(5, 10).replace('-', '/')) : null,
      on ? h('button', { type: 'button', class: 'pri-bnotebtn', 'data-testid': 'pri-brief-note', title: b.note ? 'Edit the briefed note' : 'Add a short note, e.g. sent to Madre 11/12', onclick: async () => {
        const dr = await nameDialog({ title: 'Briefed note', initial: b.note || '', okLabel: 'Save note', field: 'Note', placeholder: 'sent to Madre 11/12', max: 200, emptyOk: true, note: `${p.r.cluster} / ${p.r.icp} / ${p.r.pain_point}` });
        if (dr !== null) setBriefed(p, true, dr.name);
      } }, b.note || '+ note') : null].filter(Boolean));
  }
  if (hasBriefed) pairs.forEach((p) => { p.briefTd = h('td', { class: 'pri-c pri-c-brief', 'data-label': 'Briefed' }); p.tr.append(p.briefTd); paintBrief(p); });
  const viewCount = h('span', { 'data-testid': 'pri-view-count' });

  const selHint = h('span', { class: 'pri-selhint', 'data-testid': 'pri-sel-hint', 'aria-live': 'polite' });
  function apply() {
    if (state.onlySel && sel.size === 0) { state.onlySel = false; selHint.textContent = 'Nothing is selected, so all concepts are shown again.'; }     // an empty selection turns the toggle off
    else if (sel.size > 0 || !state.onlySel) selHint.textContent = '';
    const q = state.q.trim().toLowerCase();
    const eligible = pairs.filter((p) => state.showDone || !p.r.done);                               // done manual concepts stay hidden until Show done is ticked
    let list = eligible.filter((p) => state.products.has(p.r.product) && state.sections.has(p.r.section) && (!state.status || p.r.status === state.status) && (!state.onlySel || sel.has(keyOf(p.r))) && (state.brief === 'all' || (state.brief === 'done') === isBriefed(p.r))
      && (!q || [p.r.cluster, p.r.icp, p.r.pain_point, p.r.story].some((t) => (t || '').toLowerCase().includes(q))));
    const def = (a, b) => (sOrder[a.r.section] - sOrder[b.r.section]) || (pOrder[a.r.product] - pOrder[b.r.product]) || (a.r.rank - b.r.rank);
    if (state.key) {
      const f = value[state.key];
      list = list.sort((a, b) => { const x = f(a.r), y = f(b.r); return (x < y ? -1 : x > y ? 1 : 0) * state.dir || def(a, b); });
    } else list = list.sort(def);
    tbody.replaceChildren(...list.flatMap((p) => [p.tr, p.detailRow]));
    shownList = list;
    count.textContent = `${list.length} of ${eligible.length} concepts shown`;
    const nB = eligible.filter((p) => isBriefed(p.r)).length;
    briefProgress.textContent = `${nB} of ${eligible.length} briefed`;
    const nView = act && act.view.kind === 'filters' ? list.length : eligible.length;
    viewCount.textContent = `${nView} concept${nView === 1 ? '' : 's'}`;
    syncPicks();
    syncUI();
    for (const [k, b] of Object.entries(sortBtns)) b.closest('th').setAttribute('aria-sort', state.key === k ? (state.dir === 1 ? 'ascending' : 'descending') : 'none');
    return list;
  }
  const setAll = (open) => apply().forEach((p) => setOpen(p.tr.querySelector('.pri-toggle'), p.detailRow, open));          // Expand / Collapse all act on the filtered rows

  const uiSync = [];                                                                                // every filter control registers how to redraw itself from `state`
  const syncUI = () => uiSync.forEach((f) => f());
  const chip = (set, key, label, testid) => {
    const b = h('button', { type: 'button', class: 'pri-chipbtn', 'aria-pressed': String(set.has(key)), 'data-testid': testid, onclick: () => {
      if (set.has(key)) set.delete(key); else set.add(key);
      apply();
    } }, label);
    uiSync.push(() => b.setAttribute('aria-pressed', String(set.has(key))));
    return b;
  };
  const statuses = [...new Set(d.rows.map((r) => r.status))];
  const statusSel = h('select', { class: 'pri-fselect', 'data-testid': 'pri-f-status', 'aria-label': 'Status', onchange: (e) => { state.status = e.target.value || null; apply(); } },
    h('option', { value: '' }, 'All statuses'), statuses.map((x) => h('option', { value: x }, x)));
  uiSync.push(() => { statusSel.value = state.status || ''; });
  const searchIn = h('input', { type: 'search', 'data-testid': 'pri-search', placeholder: 'cluster, ICP, pain point or story', value: state.q, oninput: (e) => { state.q = e.target.value; apply(); } });
  uiSync.push(() => { if (searchIn.value !== state.q) searchIn.value = state.q; });
  const nActive = () => (slugs.every((x) => state.products.has(x)) ? 0 : 1) + (sections.every((x) => state.sections.has(x)) ? 0 : 1) + (state.status ? 1 : 0) + (state.q.trim() ? 1 : 0) + (state.onlySel ? 1 : 0) + (state.brief !== 'all' ? 1 : 0);
  const clearBtn = h('button', { type: 'button', class: 'pri-clear', 'data-testid': 'pri-clear-filters', onclick: () => {
    slugs.forEach((x) => state.products.add(x)); sections.forEach((x) => state.sections.add(x)); state.status = null; state.q = ''; state.onlySel = false; state.brief = 'all'; apply();
  } }, 'Clear filters');
  const activeNote = h('span', { class: 'pri-activen', 'data-testid': 'pri-active-filters', 'aria-live': 'polite' });
  uiSync.push(() => { const n = nActive(); clearBtn.hidden = n === 0; activeNote.textContent = n ? `${n} filter${n === 1 ? '' : 's'} on` : ''; });
  const selChip = h('button', { type: 'button', class: 'pri-chipbtn pri-selchip', 'aria-pressed': 'false', 'data-testid': 'pri-f-selected', title: 'Show only the rows you ticked', onclick: () => { state.onlySel = !state.onlySel; apply(); } });
  uiSync.push(() => { selChip.textContent = `Selected (${sel.size})`; selChip.setAttribute('aria-pressed', String(state.onlySel)); selChip.disabled = sel.size === 0 && !state.onlySel; });
  const frow = (label, ...kids) => h('div', { class: 'pri-frow' }, h('span', { class: 'pri-flabel' }, label), h('div', { class: 'pri-fitems' }, ...kids));
  const inView = !!act || readonly;                                                              // inside a view the product chips carry the number of concepts per product
  const prodRow = frow('Product', h('div', { class: 'pri-fgroup', role: 'group', 'aria-label': 'Product' }, slugs.map((x) => chip(state.products, x, d.baselines[x].product_name + (inView ? ` (${d.rows.filter((r) => r.product === x && !r.done).length})` : ''), 'pri-f-product-' + x))));
  const segBrief = (v, label) => { const b = h('button', { type: 'button', class: 'pri-segbtn', 'aria-pressed': 'false', 'data-testid': 'pri-f-brief-' + v, onclick: () => { state.brief = v; apply(); } }, label); uiSync.push(() => b.setAttribute('aria-pressed', String(state.brief === v))); return b; };
  const hideBtn = h('button', { type: 'button', class: 'pri-chipbtn', 'aria-pressed': 'false', 'data-testid': 'pri-hide-briefed', title: 'Hide the concepts already briefed in', onclick: () => { state.brief = state.brief === 'open' ? 'all' : 'open'; apply(); } }, 'Hide briefed');
  uiSync.push(() => hideBtn.setAttribute('aria-pressed', String(state.brief === 'open')));
  const briefRow = hasBriefed ? frow('Briefed', h('div', { class: 'pri-briefctl' }, h('span', { class: 'pri-seg', role: 'group', 'aria-label': 'Briefed filter' }, segBrief('all', 'All'), segBrief('open', 'Not briefed'), segBrief('done', 'Briefed')), hideBtn, briefProgress, briefErr)) : null;
  const filters = h('div', { class: 'pri-fbar', role: 'group', 'aria-label': 'Filters', 'data-testid': 'pri-filters' },
    prodRow, briefRow,
    readonly ? null : frow('Section', h('div', { class: 'pri-fgroup', role: 'group', 'aria-label': 'Section' }, sections.map((k) => chip(state.sections, k, SECTION_LABEL[k], 'pri-f-section-' + k)))),
    h('div', { class: 'pri-frow pri-frow-inline' },
      readonly ? null : h('label', { class: 'pri-fl' }, h('span', { class: 'pri-flabel' }, 'Status'), statusSel),
      readonly ? null : h('label', { class: 'pri-fl pri-search' }, h('span', { class: 'pri-flabel' }, 'Search'), searchIn),
      d.rows.some((r) => r.manual && r.done) ? h('label', { class: 'pri-showdone' }, h('input', { type: 'checkbox', 'data-testid': 'pri-show-done', checked: state.showDone,
        onchange: (e) => { state.showDone = e.target.checked; apply(); } }), ' Show done') : null,
      readonly ? null : selChip, activeNote, clearBtn));

  const allBox = h('input', { type: 'checkbox', class: 'pri-pick', 'data-testid': 'pri-pick-all', 'aria-label': 'Select all shown rows', onchange: (e) => {
    shownList.forEach((p) => { if (e.target.checked) sel.add(keyOf(p.r)); else sel.delete(keyOf(p.r)); });
    selChanged();
  } });
  const head = h('thead', null, h('tr', null, h('th', { class: 'pri-th-tog' }, h('span', { class: 'pri-vh' }, 'Open'), readonly ? null : allBox), (hasBriefed ? [...COLS, ['briefed', 'Briefed']] : COLS).map(([k, label]) => {
    const b = h('button', { type: 'button', class: 'pri-sort', 'data-testid': 'pri-sort-' + k, onclick: () => {
      if (state.key === k) state.dir = -state.dir; else { state.key = k; state.dir = 1; }
      apply();
    } }, label, h('span', { class: 'pri-arrow', 'aria-hidden': 'true' }, ' ⇅'));
    sortBtns[k] = b;
    return h('th', { scope: 'col', 'aria-sort': 'none' }, b);
  })));
  const table = h('table', { class: 'pri-table pri-main', 'data-testid': 'pri-table' }, h('caption', { class: 'pri-vh' }, 'Creative priorities, one row per concept'), head, tbody);

  const weekSel = h('select', { id: 'pri-week', 'data-testid': 'pri-week', 'aria-label': 'Week', onchange: (e) => navigate(href('/priorities', { week: e.target.value })) },
    d.weeks.map((w) => h('option', { value: w.week_start, selected: w.week_start === d.week_start }, 'Week of ' + w.week_start)));
  const frozenMode = !!act && vm.mode === 'frozen';
  const csvBase = readonly ? `/api/priorities/views/${view.token}/csv` : frozenMode ? `/api/priorities/views/${tok}/csv` : '/api/priorities.csv' + qs({ week: d.week_start, name: act ? act.view.name : '', briefed_from: act ? tok : '' });
  const csvUrl = () => csvBase + (sel.size ? (csvBase.includes('?') ? '&' : '?') + [...sel].map((k) => 'id=' + encodeURIComponent(k)).join('&') : '');
  const csvLink = h('a', { class: 'btn', href: csvBase, download: '', 'data-testid': 'pri-csv' }, 'Download CSV');
  const printLabel = h('span', null, 'Print / save as PDF');
  // ---------- page header (built after the view switcher below exists) ----------
  const notes = () => [
    h('p', { class: 'pri-lead' }, `Week of ${d.week_start}. Data through ${d.data_through}. Numbers are Meta-reported (ROAS = purchase value / spend, CPA = cost per purchase = spend / purchases); all time = since each ad launched, last 30 days = the most recent 30 days of data. A hit is an ad that reached $10,000 spend; tested = $100+; launched = every ad with a launch date or any spend. Upstack new-customer ROAS is separate and shown in the row panels. `
      + 'This is prioritisation: consistent with the numbers, not proof that more concepts will work. ', h('span', { class: 'pri-screen-note' }, 'Every row is collapsed: open a row for the full detail.')),
    h('p', { class: 'pri-lead', 'data-testid': 'pri-comp-asof' }, d.competitors_as_of ? `Competitor ads: Meta Ad Library, as of ${d.competitors_as_of}; refreshed weekly. Running a long time is a signal, not proven performance.`
      + ((d.competitor_stale || []).length ? ` Stale (last pull failed, last good data kept): ${d.competitor_stale.join(', ')}.` : '') : 'No competitor ads loaded yet.')];
  const legend = (tid) => h('dl', { class: 'pri-legend', 'data-testid': tid }, (d.legend || []).flatMap((x) => [h('dt', null, x.tag), h('dd', null, x.meaning)]));
  const counts = d.stats && d.stats.status_counts_total ? Object.entries(d.stats.status_counts_total) : [];
  const baseLine = (b) => b.funnel
    ? ` ALL TIME: ROAS ${roasX(b.roas)}, CPA ${fmt.money(b.cpa)}; ${funnelText(b.funnel)}; hit rate ($10K+ spend) ${share(b.funnel.top, b.funnel.launched)} of launched, ${share(b.funnel.top, b.funnel.tested)} of tested. LAST 30 DAYS: ROAS ${roasX(b.roas_30d)}, CPA ${fmt.money(b.cpa_30d)}`
      + (b.funnel_30d ? `; ${funnel30Text(b.funnel_30d)} within the window` : '')
    : ` average ROAS ${roasX(b.roas)} all time, ${roasX(b.roas_30d)} last 30 days, CPA ${fmt.money(b.cpa)}, hit rate ${pct(b.hit_rate)}`;
  const buildTop = () => {
    const sub = `Week of ${d.week_start} · Data through ${d.data_through} · ` + (d.competitors_as_of ? `Competitor ads as of ${d.competitors_as_of}` : 'No competitor ads loaded yet');
    // status strip: the 'Lines by status' counts as chips with numbers; click = filter the table by that status
    const statTile = (k, v) => {
      const tone = STATUS_CLASS[k] || 'flat';
      const inner = [h('span', { class: 'pri-stat-n' }, String(v)), h('span', { class: 'pri-stat-k' }, k)];
      if (readonly) return h('div', { class: 'pri-stat pri-stat-' + tone, 'data-testid': 'pri-stat' }, inner);
      const b = h('button', { type: 'button', class: 'pri-stat pri-stat-' + tone, 'aria-pressed': 'false', 'data-testid': 'pri-stat', 'data-status': k, title: `${v} lines have this status in total. Click to show only the concepts with this status.`,
        onclick: () => { state.status = state.status === k ? null : k; apply(); } }, inner);
      uiSync.push(() => b.setAttribute('aria-pressed', String(state.status === k)));
      return b;
    };
    const delta = (cur, base, lowerBetter) => {                       // chip: last 30 days vs all time; green = better, red = worse
      if (!isNum(cur) || !isNum(base) || !base) return null;
      const dv = (cur - base) / base, good = lowerBetter ? dv < 0 : dv > 0, flat = Math.abs(dv) < 0.02;
      return h('span', { class: 'pri-delta ' + (flat ? 'pri-delta-flat' : good ? 'pri-delta-up' : 'pri-delta-down'), 'data-testid': 'pri-delta' }, `${dv > 0 ? '+' : dv < 0 ? '\u2212' : ''}${Math.abs(Math.round(dv * 100))}% vs all time`);
    };
    const prodTile = (x) => {
      const b = d.baselines[x], fn = b.funnel, f3 = b.funnel_30d;
      const stat = (label, v, chip) => h('div', { class: 'pri-pstat' }, h('span', { class: 'pri-label' }, label), h('strong', null, v), chip || null);
      const allBlock = h('div', { class: 'pri-pblock', 'data-testid': 'pri-pblock-all' },
        h('span', { class: 'pri-pbh' }, 'All time'),
        h('div', { class: 'pri-pstats' }, stat('ROAS (all time)', roasX(b.roas)), stat('CPA (all time)', fmt.money(b.cpa)), stat('Spend', b.spend != null ? compact$(b.spend) : '\u2013'), stat('Purchases', b.purchases != null ? fmt.int(b.purchases) : '\u2013')),
        fn ? h('div', { class: 'pri-hit', 'data-testid': 'pri-hit' },
          h('div', { class: 'pri-hit-h' }, h('span', { class: 'pri-label' }, 'Hit rate: ads that reached $10K+'), h('strong', null, share(fn.top, fn.launched)), h('span', { class: 'pri-k' }, ` of launched (${fmt.int(fn.top)} of ${fmt.int(fn.launched)}); ${share(fn.top, fn.tested)} of tested (${fmt.int(fn.top)} of ${fmt.int(fn.tested)} that spent $100+)`)),
          funnelBar(fn.under_tested, fn.tested, fn.top, fn.launched, funnelText(fn)),
          h('div', { class: 'pri-hit-l', 'data-testid': 'pri-funnel' }, funnelText(fn)))
          : h('div', { class: 'pri-hit', 'data-testid': 'pri-hit' }, h('div', { class: 'pri-hit-h' }, h('span', { class: 'pri-label' }, 'Ads that reached $1,000'), h('strong', null, pct(b.hit_rate)), h('span', { class: 'pri-k' }, ` (${b.hits} of ${b.judged} mature ads that spent $100+)`))));
      const liveBlock = h('div', { class: 'pri-pblock pri-pblock-30', 'data-testid': 'pri-pblock-30d' },
        h('span', { class: 'pri-pbh' }, 'Last 30 days'),
        h('div', { class: 'pri-pstats' }, stat('ROAS (30 days)', roasX(b.roas_30d), delta(b.roas_30d, b.roas, false)), stat('CPA (30 days)', fmt.money(b.cpa_30d), delta(b.cpa_30d, b.cpa, true)),
          stat('Spend', b.spend_30d != null ? fmt.money(b.spend_30d) : '\u2013'), stat('Purchases', b.purchases_30d != null ? fmt.int(b.purchases_30d) : '\u2013'),
          stat('Ads live now', b.live_ads != null ? fmt.int(b.live_ads) : '\u2013'), stat('New ads launched', fmt.int(b.new_ads_30d))),
        f3 ? h('div', { class: 'pri-hit', 'data-testid': 'pri-hit-30d' },
          h('div', { class: 'pri-hit-h' }, h('span', { class: 'pri-label' }, 'Ads that reached $10K+ in the 30 days'), h('strong', null, share(f3.top, f3.active)), h('span', { class: 'pri-k' }, ` of ads that spent (${fmt.int(f3.top)} of ${fmt.int(f3.active)})`)),
          funnelBar(f3.under_tested, f3.tested, f3.top, f3.active, funnel30Text(f3)),
          h('div', { class: 'pri-hit-l', 'data-testid': 'pri-funnel-30d' }, funnel30Text(f3))) : null);
      const inner = [h('span', { class: 'pri-pname' }, h('i', { class: 'pri-pdot', 'aria-hidden': 'true' }), b.product_name), allBlock, liveBlock];
      const tip = `${b.product_name}: ROAS ${roasX(b.roas)} all time, ${roasX(b.roas_30d)} last 30 days; CPA ${fmt.money(b.cpa)} all time. Click to show only ${b.product_name}.`;
      if (readonly) return h('div', { class: 'pri-ptile', 'data-prod': x, 'data-testid': 'pri-ptile' }, inner);
      const el = h('button', { type: 'button', class: 'pri-ptile', 'aria-pressed': 'false', 'data-testid': 'pri-ptile', 'data-product': x, 'data-prod': x, title: tip,
        onclick: () => { const only = state.products.size === 1 && state.products.has(x); state.products.clear(); (only ? slugs : [x]).forEach((y) => state.products.add(y)); apply(); } }, inner);
      uiSync.push(() => el.setAttribute('aria-pressed', String(state.products.size === 1 && state.products.has(x))));
      return el;
    };
    const through = d.data_through;
    const metricHelp = h('details', { class: 'pri-mhelp', 'data-testid': 'pri-metric-help' },
      h('summary', { 'aria-label': 'What do these numbers mean?' }, h('span', { class: 'pri-mq', 'aria-hidden': 'true' }, '?'), ' What do these numbers mean?'),
      h('ul', null,
        h('li', null, h('strong', null, 'ROAS'), ' = Meta-reported purchase value divided by spend. ', h('strong', null, 'All time'), ' = since each ad launched; ', h('strong', null, 'last 30 days'), ` = the 30 days up to ${through}.`),
        h('li', null, h('strong', null, 'Spend \u00b7 14d / ROAS \u00b7 14d'), ' (table columns) = the 14 days up to the data-through date, raw purchase value / spend; ROAS shows a dash under $100 spend in the window. The arrow compares it with the product\u2019s last-30-day ROAS (green up, red down, 5% or more).'),
        h('li', null, h('strong', null, 'CPA'), ' (cost per purchase) = spend divided by purchases, same two windows.'),
        h('li', null, h('strong', null, 'Launched'), ' = every ad with a launch date on the tracker or any spend (renamed re-uploads count as their own ad; ads with a launch date but $0 spend are launched and land in "under $100").'),
        h('li', null, h('strong', null, 'Tested'), ' = an ad that spent $100 or more. ', h('strong', null, 'Hit'), ' = an ad that reached $10,000 spend. Hit rate = hits / launched (the honest one); hits / tested is shown beside it.'),
        h('li', null, h('strong', null, 'Last 30 days'), ' funnel: of the ads that spent in the window, how many spent under $100, $100+ and $10K+ inside the window (a $10K+ ad needs $10,000 in 30 days, so this is much stricter than the all-time figure).'),
        h('li', null, 'The green / red chip compares the last 30 days with all time (higher ROAS and lower CPA are green).'),
        h('li', null, 'Upstack new-customer ROAS is a separate measure and is shown in each row panel.'),
        h('li', null, `Data through ${through}.`)));
    const about = (title, tid, ...kids) => h('details', { class: 'pri-about', 'data-testid': tid }, h('summary', null, title), h('div', { class: 'pri-about-b' }, ...kids));
    return h('div', { class: 'pri-top' },
      h('div', { class: 'pri-titlerow' },
        h('div', { class: 'pri-titlecol' },
          h('h1', { 'data-testid': 'pri-title' }, readonly ? `Creative priorities: ${view.name}` : act ? `Creative priorities: ${act.view.name}` : 'Creative priorities'),
          h('p', { class: 'pri-sub pri-hd-screen', 'data-testid': 'pri-sub' }, sub)),
        readonly ? null : h('div', { class: 'pri-headctl pri-hd-screen' },
          act ? null : h('label', { class: 'pri-weekl' }, h('span', { class: 'pri-flabel' }, 'Week'), weekSel), viewbar)),
      readonly ? h('p', { class: 'pri-lead', 'data-testid': 'pri-view-note' }, `Saved view of ${d.rows.length} concept${d.rows.length === 1 ? '' : 's'}, snapshot week of ${d.week_start} (frozen: it does not change when a newer snapshot arrives). `,
        h('a', { href: href('/priorities', {}), 'data-testid': 'pri-open-full' }, 'Open full priorities'), ' ',
        h('a', { href: viewUrl(view.token), 'data-testid': 'pri-open-live' }, 'Open as a view with the latest data'), ' ',
        h('button', { type: 'button', class: 'btn pri-screen-note', 'data-testid': 'pri-rename', onclick: async () => {
          const dr = await nameDialog({ title: 'Rename this view', initial: view.name, okLabel: 'Rename', note: 'The link stays the same.' });
          if (dr === null) return;
          const nn = dr.name;
          try {
            const r = await api('/api/priorities/views/' + view.token, {}, { method: 'PATCH', body: { name: nn } });
            view.name = r.name;
            document.querySelector('[data-testid=pri-title]').textContent = `Creative priorities: ${r.name}`;
            document.title = `${r.name} · Creative priorities · Marketing metrics`;
          } catch (e) { window.alert('Could not rename: ' + (e.message || 'error')); }
        } }, 'Rename')) : null,
      h('div', { class: 'pri-actions pri-hd-screen' },
        readonly || !opts.reload ? null : h('button', { type: 'button', class: 'btn primary pri-add', 'data-testid': 'pri-add-concept', onclick: () => mctl.add() }, 'Add concept'),
        h('span', { class: 'pri-btngroup', role: 'group', 'aria-label': 'Export' }, csvLink, h('button', { type: 'button', class: 'btn', 'data-testid': 'pri-print', onclick: () => window.print() }, printLabel))),
      h('div', { class: 'pri-dash pri-hd-screen' },
        counts.length ? h('div', { class: 'pri-statstrip', role: 'group', 'aria-label': 'Lines by status', 'data-testid': 'pri-statstrip' }, h('span', { class: 'pri-flabel' }, 'Lines by status'), h('div', { class: 'pri-stats' }, counts.map(([k, v]) => statTile(k, v)))) : null,
        metricHelp,
        h('div', { class: 'pri-ptiles', role: 'group', 'aria-label': 'Products' }, slugs.map(prodTile)),
        h('div', { class: 'pri-abouts' },
          about('What these tags mean', 'pri-about-tags', legend('pri-legend-ui')),
          about('About these numbers', 'pri-about-numbers', ...notes()))),
      // print copy of the intro: every sentence, the legend and the three product lines, exactly as before (hidden on screen)
      h('div', { class: 'pri-printintro', 'data-testid': 'pri-printintro' }, ...notes(),
        counts.length ? h('p', { class: 'pri-lead', 'data-testid': 'pri-counts' }, 'Lines by status: ' + counts.map(([k, v]) => `${k} ${v}`).join(', ')) : null,
        legend('pri-legend'),
        h('div', { class: 'pri-base' }, slugs.map((x) => h('div', null, h('strong', null, d.baselines[x].product_name), baseLine(d.baselines[x]))))));
  };

  // selection toolbar (sticky, only while something is selected)
  const selCount = h('strong', { 'data-testid': 'pri-sel-count' });
  const copyNote = h('span', { class: 'pri-k', 'aria-live': 'polite', 'data-testid': 'pri-copy-note' });
  const selCsv = h('a', { class: 'btn', href: csvBase, download: '', 'data-testid': 'pri-sel-csv' }, 'Download CSV');
  const linkBox = h('div', { class: 'pri-linkbox', hidden: true, 'data-testid': 'pri-linkbox', role: 'status' });
  const abs = (u) => location.origin + u;
  const copyLinkBtn = (u, label = 'Copy link') => h('button', { type: 'button', class: 'btn', 'data-testid': 'pri-copy-link', onclick: (e) => {
    const t = abs(u);
    const ok = () => { e.target.textContent = 'Copied'; setTimeout(() => { e.target.textContent = label; }, 1500); };
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(t).then(ok, () => window.prompt('Copy this link', t)); else window.prompt('Copy this link', t);
  } }, label);
  // Save view (both kinds) opens the naming dialog; `preferKind` is what the button that opened it stands for
  async function saveViewDialog(preferKind) {
    const hasSel = sel.size > 0;
    const dr = await nameDialog({ title: 'Save view', initial: defaultViewName(), okLabel: 'Save view', note: `${sel.size} concept${sel.size === 1 ? '' : 's'} selected`, kind: preferKind === 'rows' && hasSel ? 'rows' : preferKind === 'filters' ? 'filters' : (hasSel ? 'rows' : 'filters'),
      kinds: [['rows', `Selected rows (${sel.size} selected)`, !hasSel], ['filters', 'Current filters (product, section, search, sort), applied live']] });
    if (dr === null) return;
    const body = { name: dr.name, week: d.week_start, kind: dr.kind };
    if (dr.kind === 'rows') body.ids = [...sel]; else body.filters = currentFilters();
    try {
      const v = await api('/api/priorities/views', {}, { method: 'POST', body });
      linkBox.hidden = false;
      linkBox.replaceChildren(h('span', { 'data-testid': 'pri-saved-name' }, `Saved “${v.name}” `), h('span', { class: 'pri-k' }, `${v.kind === 'filters' ? `${v.count} concept${v.count === 1 ? '' : 's'} match now; new matches appear automatically` : `${v.count} concept${v.count === 1 ? '' : 's'}${v.missing ? ` (${v.missing} no longer in this snapshot)` : ''}`}. `),
        h('a', { href: viewUrl(v.token), 'data-testid': 'pri-view-open' }, 'Open it'), ' ', h('a', { href: v.url, 'data-testid': 'pri-view-link' }, abs(v.url)), ' ', copyLinkBtn(v.url));
      vm.views.unshift({ token: v.token, name: v.name, kind: v.kind, count: v.count });
      viewSel.append(h('option', { value: v.token }, viewLabel(v)));
    } catch (e) {
      linkBox.hidden = false;
      linkBox.replaceChildren(h('span', { class: 'pri-err', 'data-testid': 'pri-view-error' }, 'Could not save the view: ' + (e.message || 'error')));
    }
  }
  const selOnlyBtn = h('button', { type: 'button', class: 'btn pri-selonly', 'aria-pressed': 'false', 'data-testid': 'pri-sel-only', onclick: () => { state.onlySel = !state.onlySel; apply(); } }, 'Show selected only');
  uiSync.push(() => { selOnlyBtn.setAttribute('aria-pressed', String(state.onlySel)); selOnlyBtn.textContent = state.onlySel ? 'Show all rows' : 'Show selected only'; });
  const toolbar = h('div', { class: 'pri-seltools', role: 'region', 'aria-label': 'Selected rows', hidden: true, 'data-testid': 'pri-seltools' }, selCount, selCsv,
    h('button', { type: 'button', class: 'btn', 'data-testid': 'pri-sel-print', onclick: () => window.print() }, 'Print / PDF'),
    h('button', { type: 'button', class: 'btn', 'data-testid': 'pri-sel-copy', onclick: () => copyText() }, 'Copy as text'),
    selOnlyBtn,
    h('button', { type: 'button', class: 'btn', 'data-testid': 'pri-sel-save', onclick: () => saveViewDialog('rows') }, 'Save view'),
    h('button', { type: 'button', class: 'btn', 'data-testid': 'pri-sel-clear', onclick: () => { sel.clear(); selChanged(); } }, 'Clear'), copyNote);

  // the View switcher: All concepts + each saved view; choosing one filters this same table in place (URL ?view=<token>); Save view sits beside it
  const viewLabel = (v) => `${v.name || 'Untitled view'} (${v.kind === 'filters' ? 'filters' : `${v.count} concept${v.count === 1 ? '' : 's'}`})`;
  const viewSel = h('select', { id: 'pri-view-select', class: 'pri-viewsel', 'data-testid': 'pri-view-select', 'aria-label': 'View', onchange: (e) => {
    const t = e.target.value;
    lastView.set(t);
    navigate(t ? viewUrl(t) : href('/priorities', {}));
  } }, h('option', { value: '' }, 'All concepts'), vm.views.map((v) => h('option', { value: v.token, selected: v.token === tok }, viewLabel(v))));
  const goRefresh = () => navigate(location.pathname + location.search, { replace: true });
  async function activeAction(fn, okMsg) {
    try { await fn(); flash.set(okMsg); goRefresh(); } catch (e) { window.alert('Could not do that: ' + (e.message || 'error')); }
  }
  const viewTools = act ? (() => {
    const v = act.view; const kindF = v.kind === 'filters';
    const fromN = d.rows.filter((r) => r._from).length;
    return h('div', { class: 'pri-activebar', role: 'region', 'aria-label': 'Active view', 'data-testid': 'pri-activebar' },
      h('div', { class: 'pri-activeline' }, h('span', null, 'Viewing: ', h('strong', { 'data-testid': 'pri-view-name-shown' }, v.name || 'Untitled view'), ' (', viewCount, ')'),
        ' · ', h('a', { href: href('/priorities', {}), 'data-testid': 'pri-back-all', onclick: (e) => { e.preventDefault(); lastView.set(''); navigate(href('/priorities', {})); } }, 'Back to all'),
        h('span', { class: 'pri-k' }, kindF ? ' Saved filters, applied live: new matching concepts appear.' : ' A fixed set of concepts.'),
        fromN ? h('span', { class: 'pri-k', 'data-testid': 'pri-from-note' }, ` ${fromN} no longer in the current snapshot (shown from ${v.week_start}).`) : null),
      h('div', { class: 'pri-activebtns' },
        h('label', { class: 'pri-datamode' }, h('span', { class: 'pri-k' }, 'Data '), h('select', { 'data-testid': 'pri-data-mode', 'aria-label': 'Frozen as saved or latest data', onchange: (e) => navigate(viewUrl(tok, e.target.value === 'frozen' ? { data: 'frozen' } : {})) },
          h('option', { value: 'latest', selected: !frozenMode }, 'Latest data'), h('option', { value: 'frozen', selected: frozenMode }, `Frozen as saved (${v.week_start})`))),
        h('button', { type: 'button', class: 'btn', 'data-testid': 'pri-view-update', title: kindF ? 'Replace this view’s filters with the filters now on the page' : 'Replace this view’s rows with the rows now selected', onclick: () => {
          if (!kindF && !sel.size) { window.alert('Tick the rows to keep in this view, then Update this view.'); return; }
          activeAction(() => api('/api/priorities/views/' + tok, {}, { method: 'PUT', body: kindF ? { filters: currentFilters() } : { ids: [...sel] } }), `Updated “${v.name}”.`);
        } }, 'Update this view'),
        h('button', { type: 'button', class: 'btn', 'data-testid': 'pri-active-rename', onclick: async () => {
          const dr = await nameDialog({ title: 'Rename this view', initial: v.name, okLabel: 'Rename', note: 'The link stays the same.' });
          if (dr === null) return;
          activeAction(() => api('/api/priorities/views/' + tok, {}, { method: 'PATCH', body: { name: dr.name } }), `Renamed to “${dr.name}”.`);
        } }, 'Rename'),
        copyLinkBtn(`/priorities/v/${tok}`, 'Copy share link'),
        h('button', { type: 'button', class: 'btn', 'data-testid': 'pri-active-delete', onclick: async () => {
          if (!window.confirm(`Delete the view “${v.name}”? Anyone with its link will lose access.`)) return;
          try { await api('/api/priorities/views/' + tok, {}, { method: 'DELETE' }); } catch (e) { /* already gone */ }
          lastView.set(''); flash.set(`Deleted “${v.name}”.`); navigate(href('/priorities', {}));
        } }, 'Delete')));
  })() : null;
  const viewbar = h('div', { class: 'pri-viewbar', role: 'group', 'aria-label': 'Saved views', 'data-testid': 'pri-viewbar' },
    h('label', { class: 'pri-viewgrp' }, h('span', { class: 'pri-flabel' }, 'View'), viewSel),
    h('button', { type: 'button', class: 'btn pri-savebtn', 'data-testid': 'pri-save-open', onclick: () => saveViewDialog(null) }, 'Save view'),
    vm.notice ? h('span', { class: 'pri-notice', 'data-testid': 'pri-view-notice', role: 'status' }, vm.notice) : null);
  function syncPicks() {
    pairs.forEach((p) => { p.pick.checked = isSel(p.r); });
    const n = shownList.filter((p) => isSel(p.r)).length;
    allBox.checked = shownList.length > 0 && n === shownList.length;
    allBox.indeterminate = n > 0 && n < shownList.length;
  }
  function selChanged() {
    if (readonly) { buildPrint(); return; }
    saveSel(d.week_start, sel, tok);
    if (sel.size) selHint.textContent = '';
    if (state.onlySel) apply();                                                    // an unticked row leaves the selected-only list at once
    syncPicks();
    syncUI();
    const n = sel.size;
    toolbar.hidden = n === 0;
    selCount.textContent = `${n} selected`;
    csvLink.href = selCsv.href = csvUrl();
    csvLink.textContent = n ? `Download CSV (${n} selected)` : 'Download CSV';
    printLabel.textContent = n ? `Print / save as PDF (${n} selected)` : 'Print / save as PDF';
    copyNote.textContent = '';
    buildPrint();
  }
  function copyText() {
    const rows = d.rows.filter(isSel).sort((a, b) => (pOrder[a.product] - pOrder[b.product]) || (sOrder[a.section] - sOrder[b.section]) || (a.rank - b.rank));
    const text = rows.map((r) => [`Product: ${(d.baselines[r.product] || {}).product_name || r.product}`, `Section: ${SECTION_LABEL[r.section]}`,
      `Market Cluster: ${r.cluster}`, `ICP: ${r.icp}`, `Pain Point: ${r.pain_point}`, `Story: ${r.story || '\u2013'}`, `Status: ${r.status}`, `Key number: ${keyNumber(r, d)}`,
      `Concepts to brief: ${r.concepts || 0}`, ...(hasBriefed ? [`Briefed: ${isBriefed(r) ? briefTxt(r).replace('Briefed ', 'yes, ') : 'no'}`] : []), ...strip(r, true).map((t, i) => (i === 0 ? 'Notes: ' : '') + t)].join('\n')).join('\n\n');
    const done = (ok) => { copyNote.textContent = ok ? `Copied ${rows.length} row${rows.length === 1 ? '' : 's'}.` : 'Copy failed: select the text and copy by hand.'; };
    const fallback = () => {
      const ta = h('textarea', { style: { position: 'fixed', opacity: '0' } }); ta.value = text; document.body.appendChild(ta); ta.select();
      let ok = false; try { ok = document.execCommand('copy'); } catch (e) { ok = false; } ta.remove(); done(ok);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(() => done(true), fallback); else fallback();
  }

  // print copy (hidden on screen): a compact one-sheeter. Intro on its own first page; per product a fresh page with the headline columns, a 2-3 line strip under each row that has concepts to brief, the
  // Stop list as plain lines. Rows never split across pages, the header repeats on every page, fixed column widths.
  const trim = (t, n) => { t = String(t || ''); return t.length <= n ? t : t.slice(0, n).replace(/\s+\S*$/, '') + '\u2026'; };
  const strip = (r, full = false) => {
    const out0 = strip0(r, full);
    return has14(r) ? [...out0.slice(0, full ? out0.length : 2), `14d: ${fmt.money(r.metrics.spend_14d)}${roas14Of(r) === null ? '' : ' at ' + roasX(roas14Of(r))}`] : out0;
  };
  const strip0 = (r, full = false) => {
    if (r.manual) return manualStrip(r, full ? 0 : 300);
    const m = r.metrics || {};
    if (r.section === 'faded') {
      const st = r.stretch;
      const then = st ? `Worked ${st.start} to ${st.end}: ROAS ${roasX(st.roas)} on ${fmt.money(st.spend)}` : 'Strong before the weekly data began (2026-05-06)';
      const now = m.spend_30d >= 250 ? `now ${roasX(m.roas_30d)} on ${fmt.money(m.spend_30d)}${st && isNum(m.roas_30d) && st.roas ? ` (${Math.round((m.roas_30d / st.roas - 1) * 100)}%)` : ''}` : 'now idle (no spend in 30 days)';
      return [`${then} \u2192 ${now}.`, trim(r.revive_play, 230)];
    }
    if (r.section === 'iterate') return [trim(r.why, 260)];
    const out = [];
    if (r.competitor) out.push(`Others: ${r.competitor.text} (running a long time, not proven performance).`);
    const why = trim(r.why, r.competitor ? 170 : 250);
    out.push(why.charAt(0).toUpperCase() + why.slice(1));
    return out.slice(0, 2);
  };
  const PRINT_STATUS = { 'UNTESTED / UNDER-TESTED': 'UNTESTED', 'PROPOSED (draft)': 'DRAFT', 'FADING / WAS WORKING': 'FADING' };
  const printCell = (cls, ...kids) => h('td', { class: 'pri-c pri-c-' + cls }, ...kids);
  const printRow = (r) => h('tbody', { class: 'pri-rg', 'data-testid': 'pri-print-row' },
    h('tr', { class: 'pri-row' }, printCell('section', h('span', { class: 'pri-sec pri-sec-' + r.section }, SECTION_LABEL[r.section]), hasBriefed && isBriefed(r) ? h('span', { class: 'pri-bprint', 'data-testid': 'pri-print-briefed' }, ' \u2713 briefed') : null, r.manual ? h('span', { class: 'pri-mbadge' }, 'Manual') : null), printCell('cluster', r.cluster), printCell('icp', r.icp), printCell('pain', r.pain_point),
      printCell('story', r.story || '\u2013'), printCell('status', h('span', { class: 'pri-status pri-st-' + (STATUS_CLASS[r.status] || 'flat') }, PRINT_STATUS[r.status] || r.status)),
      printCell('concepts', r.manual ? '\u2013' : r.concepts ? String(r.concepts) : '\u2013'), printCell('key', keyNumber(r, d))),
    h('tr', { class: 'pri-strip' }, h('td', { colspan: '8' }, strip(r).map((t) => h('div', null, t)))));
  const widths = [10, 13, 13, 14, 22, 8, 7, 13];
  const printHead = () => h('thead', null, h('tr', null, COLS.filter(([k]) => k !== 'product' && k !== 'spend14' && k !== 'roas14').map(([k, label]) => h('th', { scope: 'col' }, k === 'concepts' ? 'To brief' : label))));
  const printBlocks = h('div', { class: 'pri-print', 'data-testid': 'pri-print-copy', 'aria-hidden': 'true' });
  const buildPrint = () => printBlocks.replaceChildren(...slugs.flatMap((s) => {
    const picking = sel.size > 0;
    const mine = d.rows.filter((r) => r.product === s && (picking ? isSel(r) : !r.done)).sort((a, b) => (sOrder[a.section] - sOrder[b.section]) || (a.rank - b.rank));
    if (picking && !mine.length) return [];                                          // product sections with no selected rows are omitted
    const shown = mine.filter((r) => r.section !== 'stop' && (picking || r.concepts > 0 || r.manual));
    const stops = mine.filter((r) => r.section === 'stop');
    const b = d.baselines[s];
    const skipped = picking ? 0 : mine.length - shown.length - stops.length;
    return [h('section', { class: 'pri-product', 'data-testid': 'pri-product-' + s },
      h('h2', null, `${b.product_name}`, h('span', { class: 'pri-h2note' }, ` average ROAS ${roasX(b.roas)} all time, ${roasX(b.roas_30d)} last 30 days, CPA ${fmt.money(b.cpa)} all time`)),
      h('table', { class: 'pri-table pri-ptable' }, h('colgroup', null, widths.map((w) => h('col', { style: { width: w + '%' } }))), printHead(), shown.map(printRow)),
      skipped > 0 ? h('p', { class: 'pri-pnote' }, `${skipped} more line${skipped === 1 ? '' : 's'} with no concepts to brief this week are on the page, not printed.`) : null,
      stops.length ? h('div', { class: 'pri-stoplist', 'data-testid': 'pri-stoplist' }, h('h3', null, `Stop or deprioritise (${stops.length})`),
        h('ul', null, stops.map((r) => h('li', null, h('strong', null, `${r.cluster} / ${r.icp} / ${r.pain_point}`), `: ${roasX((r.metrics || {}).roas)} lifetime vs ${roasX(b.roas)} product, ${fmt.money((r.metrics || {}).spend)} spent.`)))) : null)];
  }));

  apply();
  selChanged();
  const tablebar = h('div', { class: 'pri-tablebar' }, h('span', null, count, ' ', selHint), h('span', { class: 'pri-seg pri-expbar', role: 'group', 'aria-label': 'Expand or collapse rows' },
    h('button', { type: 'button', class: 'pri-segbtn pri-exp', 'data-testid': 'pri-all-expand', onclick: () => setAll(true) }, 'Expand all'),
    h('button', { type: 'button', class: 'pri-segbtn pri-exp', 'data-testid': 'pri-all-collapse', onclick: () => setAll(false) }, 'Collapse all')));
  const topEl = buildTop();
  syncUI();
  return h('div', { class: 'page pri-page', 'data-testid': 'pri-page' }, topEl, h('div', { class: 'pri-screen' }, readonly ? null : viewTools, filters, readonly ? null : toolbar, readonly ? null : linkBox, tablebar, table), printBlocks);
}

// the page data for an active view: live rows where the key still exists (latest mode) else the frozen copy; a row that left the current snapshot falls back to its frozen copy, marked `_from`.
function effective(d, act, mode) {
  const v = act.view; const frozen = act.rows;
  const { view, ...frozenData } = act;
  if (mode === 'frozen') return { ...frozenData, weeks: d.weeks, competitors_as_of: act.competitors_as_of };
  const baselines = { ...(act.baselines || {}), ...(d.baselines || {}) };
  if (v.kind === 'filters') return { ...d, baselines };
  const live = new Map(d.rows.map((r) => [keyOf(r), r])); const fz = new Map(frozen.map((r) => [keyOf(r), r]));
  const rows = (v.keys.length ? v.keys : frozen.map(keyOf)).map((k) => live.get(k) || (fz.get(k) ? { ...fz.get(k), _from: v.week_start } : null)).filter(Boolean);
  return { ...d, baselines, rows };
}

export async function prioritiesPage(ctx) {
  showControls(false);
  let token = ctx.params.view || '';
  const mode = ctx.params.data === 'frozen' ? 'frozen' : 'latest';
  const fetchSnap = () => api('/api/priorities', token || !ctx.params.week ? {} : { week: ctx.params.week }, { signal: ctx.signal });
  let d;
  try {
    d = await fetchSnap();
  } catch (e) {
    if (e && e.status === 404) {
      ctx.mount(h('div', { class: 'page pri-page' }, h('h1', null, 'Creative priorities'), h('p', { 'data-testid': 'pri-empty' }, e.message)));
      return;
    }
    throw e;
  }
  let views = [];
  try { views = (await api('/api/priorities/views', {}, { signal: ctx.signal })).views || []; } catch (e) { if (e && e.name === 'AbortError') throw e; views = []; }
  let notice = flash.take();
  if (!token) {                                                                    // remembered view: only when the URL names none
    const last = lastView.get();
    if (last && views.some((v) => v.token === last)) { token = last; navigate(viewUrl(last, mode === 'frozen' ? { data: 'frozen' } : {}), { replace: true, render: false }); } else if (last) lastView.set('');
  }
  let act = null;
  if (token) {
    try { act = await api('/api/priorities/views/' + encodeURIComponent(token), {}, { signal: ctx.signal }); } catch (e) {
      if (e && e.status === 404) { lastView.set(''); notice = 'That saved view was deleted or does not exist, so all concepts are shown.'; navigate(href('/priorities', {}), { replace: true, render: false }); token = ''; } else throw e;
    }
  }
  const ref = {};
  const vm = { views, active: act, mode, notice };
  const reload = async () => {                                                     // after a manual concept is added / edited / done / deleted: fetch again, keep the view, search, sort and Show done
    const nd = await fetchSnap();
    ctx.mount(render(act ? effective(nd, act, mode) : nd, null, { reload, ref, carry: ref.state, vm }));
  };
  ctx.mount(render(act ? effective(d, act, mode) : d, null, { reload, ref, vm }));
  if (act) document.title = `${act.view.name} · Creative priorities · Marketing metrics`;
}

export async function prioritiesViewPage(ctx) {
  showControls(false);
  let d;
  try {
    d = await api('/api/priorities/views/' + encodeURIComponent(ctx.match[1]), {}, { signal: ctx.signal });
  } catch (e) {
    if (e && e.status === 404) {
      ctx.mount(h('div', { class: 'page pri-page' }, h('h1', null, 'Creative priorities'), h('p', { 'data-testid': 'pri-empty' }, 'This view was deleted (or the link is wrong): it no longer exists. '), h('a', { href: href('/priorities', {}) }, 'Open full priorities')));
      return;
    }
    throw e;
  }
  ctx.mount(render(d, d.view));
  document.title = `${d.view.name} \u00b7 Creative priorities \u00b7 Marketing metrics`;
}
