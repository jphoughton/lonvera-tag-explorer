// Daily story (/daily): one short explanation of why a day's performance was what it was, and every table behind it. Everything comes from /api/daily-story (computed nightly,
// stored in daily_story / daily_story_facts / daily_story_patterns; see docs/DAILY_STORY.md). Headline = new-customer return on ad spend (NC ROAS) = actual new-customer revenue /
// (Meta + Google spend); the ad-attributed ROAS in the cut tables is only a way to compare ads, products and structures. Class names and test ids start with `dly-`.
import { h, fmt, isNum, href, setParams } from '../util.js';
import { api } from '../api.js';
import { showControls } from '../controls.js';
import { pageHeader } from '../ui/pagehead.js';
import { filterRow } from '../ui/pagefilters.js';

const dash = '–';
const money = (v) => (isNum(v) ? (Math.abs(v) < 100 ? fmt.money2(v) : fmt.money(v)) : dash);
const num2 = (v) => (isNum(v) ? v.toFixed(2) : dash);
const num3 = (v) => (isNum(v) ? v.toFixed(3) : dash);
const sgn = (v, d = 2) => (isNum(v) ? (v > 0 ? '+' : '') + v.toFixed(d) : dash);
const pctSigned = (v) => (isNum(v) ? (v > 0 ? '+' : '') + (v * 100).toFixed(0) + '%' : dash);
const fmtUnit = (unit, v) => (!isNum(v) ? dash : unit === 'usd' ? money(v) : unit === 'pct' ? (v * 100).toFixed(1) + '%' : unit === 'x' ? num2(v) : fmt.int(v));
const pc1 = (v) => (isNum(v) ? (v * 100).toFixed(1) + '%' : dash);
const pc2 = (v) => (isNum(v) ? (v * 100).toFixed(2) + '%' : dash);
const weekday = (s) => ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'][new Date(s + 'T12:00:00Z').getUTCDay()];
const dayLabel = (s) => `${weekday(s)} ${fmt.day(s)}, ${String(s).slice(0, 4)}`;

const table = (testid, head, body, cls = '') => h('div', { class: 'tablebox' }, h('div', { class: 'tablewrap dly-wrap' }, h('table', { class: 'dt dly-table ' + cls, 'data-testid': testid }, h('thead', null, head), h('tbody', null, body))));
const th = (t, cls = 'num', title = null) => h('th', { class: cls, title }, t);
const td = (t, cls = 'num', extra = {}) => h('td', { class: cls, ...extra }, t);
const section = (title, ...kids) => h('section', { class: 'dly-section' }, h('h2', { class: 'dly-h2' }, title), ...kids);
const note = (t, testid) => h('p', { class: 'note-line dly-note', 'data-testid': testid || null }, t);

// ---------- story card
function storyCard(d) {
  const s = d.story;
  if (!s) return h('div', { class: 'dly-card' }, h('p', null, 'No story was written for this day.'));
  const lines = s.story.split('\n').filter(Boolean);
  const tldr = lines[0] || '';
  const rest = lines.slice(1);
  const conf = Object.entries(s.confidence || {}).map(([k, v]) => h('li', null, h('strong', null, k.replace(/_/g, ' ') + ': '), String(v)));
  return h('div', { class: 'dly-card', 'data-testid': 'dly-story' },
    h('p', { class: 'dly-tldr', 'data-testid': 'dly-tldr' }, tldr),
    rest.map((l) => h('p', { class: 'dly-line' }, l)),
    h('div', { class: 'dly-meta' },
      h('span', { class: 'badge muted-badge', 'data-testid': 'dly-source' }, s.source === 'llm' ? `Written by ${s.model} from the facts` : 'Template from the facts'),
      d.coverage !== 'full' ? h('span', { class: 'badge muted-badge', 'data-testid': 'dly-coverage' }, d.coverage === 'not_ready' ? 'Data not ready' : 'Some sources missing') : null,
      s.note ? h('span', { class: 'dly-small' }, s.note) : null),
    conf.length ? h('details', { class: 'dly-details' }, h('summary', null, 'How sure (confidence per claim)'), h('ul', null, conf)) : null);
}

// ---------- headline metric table (date windows as columns)
const MAIN = ['nc_roas', 'aov', 'cpnc', 'spend_total', 'spend_meta', 'spend_google', 'nc', 'nc_rev', 'cpm', 'ctr', 'cplc', 'conv', 'c2lpv', 'lpv2atc', 'atc2ic', 'atc2nc', 'meta_roas', 'ad_roas', 'capped_share', 'troas_scaling_share', 'd7_scaling_share', 'testing_share', 'families_live', 'families_launched', 'new_adsets', 'new_ad_share', 'new_visitors'];
function metricsTable(f) {
  const M = f.metrics || {};
  const head = h('tr', null, th('Metric', 'dly-first'), th('Day'), th('Same weekday, median of previous 4 weeks'), th('Change'), th('Previous 7 days'), th('z-score'), th('Basis', 'dly-first'));
  const rows = MAIN.filter((k) => M[k] && isNum(M[k].value)).map((k) => {
    const m = M[k];
    const flag = m.flag ? ` dly-flag-${m.flag}` : '';
    return h('tr', { 'data-metric': k, class: k === 'nc_roas' ? 'dly-headline' : null },
      h('th', { class: 'dly-first', scope: 'row' }, m.label), td(fmtUnit(m.unit, m.value), 'num', { 'data-testid': `dly-m-${k}` }),
      td(fmtUnit(m.unit, m.baseline_median)), td(m.unit === 'pct' && isNum(m.value) && isNum(m.baseline_median) ? fmt.pp((m.value - m.baseline_median) * 100) : pctSigned(m.pct_vs_baseline)),
      td(fmtUnit(m.unit, m.trailing_7d)), td(isNum(m.z) ? sgn(m.z, 1) : dash, 'num' + flag), h('td', { class: 'dly-first dly-basis' }, basisOf(k)));
  });
  return table('dly-metrics', head, rows);
}
const BASIS = { nc_roas: 'headline: actual new-customer revenue / (Meta + Google spend)', ad_roas: 'a cut: Upstack new customers attributed to Meta ads', meta_roas: 'side data: Meta-reported, counts returning customers', conv: 'actual new customers / Meta link clicks', atc2nc: 'actual new customers / Meta adds to cart' };
const basisOf = (k) => BASIS[k] || '';

// ---------- decomposition
function decompTable(f) {
  const d = f.decomposition;
  if (!d) return note('No decomposition: the same-weekday baseline or a funnel step is missing for this day.', 'dly-decomp-none');
  const head = h('tr', null, th('Term', 'dly-first'), th('Day'), th('Baseline (pooled)'), th('Effect on NC ROAS, percentage points'), th('Share of the move'));
  const fmtT = (t, v) => (t.name === 'aov' ? money(v) : t.name === 'cpm' ? money(v) : pc2(v));
  const rows = d.terms.map((t) => h('tr', { 'data-term': t.name }, h('th', { class: 'dly-first', scope: 'row' }, t.label), td(fmtT(t, t.day)), td(fmtT(t, t.base)), td(sgn(t.pct_points * 100, 1), 'num', { 'data-testid': 'dly-term-' + t.name }), td(isNum(t.share) ? (t.share * 100).toFixed(0) + '%' : dash)));
  rows.push(h('tr', { class: 'dly-sumrow' }, h('th', { class: 'dly-first', scope: 'row' }, 'NC ROAS'), td(num3(d.day)), td(num3(d.base)), td(sgn(d.pct_change * 100, 1), 'num', { 'data-testid': 'dly-term-total' }), td('100%')));
  return h('div', null, table('dly-decomp', head, rows), note('Exact arithmetic against the pooled same-weekday baseline (the terms add back to the total move). It shows which step moved, not why.'));
}

// ---------- segment cuts
function cutTable(t, open) {
  const head = h('tr', null, th(t.label, 'dly-first'), th('Spend share'), th('Same-weekday share'), th('Spend'), th('Ad families'), th('Ads'), th('Attributed ROAS'), th('Baseline ROAS'), th('CPM'), th('CTR'), th('Cost per link click'), th('Conv rate'), th('AOV'),
    th('Click to page view'), th('Page view to cart'), th('Cart to new customer'), th('Mix effect'), th('Rate effect'));
  const rows = t.rows.map((r) => {
    const m = r.day;
    return h('tr', { 'data-key': r.key }, h('th', { class: 'dly-first dly-key', scope: 'row', title: r.key }, r.key), td(pc1(r.share)), td(pc1(r.base_share)), td(m ? money(m.spend) : dash), td(m ? fmt.int(m.families) : dash), td(m ? fmt.int(m.ads) : dash),
      td(m ? num2(m.attr_roas) : dash), td(num2(r.base.attr_roas)), td(m ? money(m.cpm) : dash), td(m ? pc2(m.ctr) : dash), td(m ? money(m.cplc) : dash), td(m ? pc2(m.conv) : dash), td(m ? money(m.aov) : dash),
      td(m ? pc1(m.c2lpv) : dash), td(m ? pc1(m.lpv2atc) : dash), td(m ? pc1(m.atc2nc) : dash), td(r.effect ? sgn(r.effect.mix, 3) : dash), td(r.effect ? sgn(r.effect.rate, 3) : dash));
  });
  if (t.other_rows) rows.push(h('tr', { class: 'dly-sumrow' }, h('th', { class: 'dly-first' }, `${t.other_rows} smaller segments`), td(pc1(t.other_share)), ...Array(16).fill(td(''))));
  const mr = t.mix_rate;
  const cap = mr ? `Ad-attributed ROAS ${num2(mr.day)} vs ${num2(mr.base)} (${sgn(mr.change)}): spend moving between segments (mix) ${sgn(mr.mix, 3)}, per-dollar change inside them (rate) ${sgn(mr.rate, 3)}.` : 'No mix versus rate split for this day.';
  return h('details', { class: 'dly-details dly-cut', open: open ? '' : null, 'data-testid': 'dly-cut-' + t.cut }, h('summary', null, t.label, h('span', { class: 'dly-small' }, ` (${t.n_segments} segments)`)), note(cap), table('dly-cuttable-' + t.cut, head, rows));
}

function movers(f) {
  const out = [];
  const fam = (f.movers || {}).families;
  if (fam && fam.rows.length) {
    const head = h('tr', null, th('Ad (family)', 'dly-first'), th('Cluster', 'dly-first'), th('Format', 'dly-first'), th('Age, days'), th('Spend'), th('Share vs same-weekday share'), th('Attributed ROAS vs baseline'), th('Effect on ROAS'), th('Hook', 'dly-first'));
    const rows = fam.rows.map((r) => h('tr', null, h('th', { class: 'dly-first dly-key', scope: 'row', title: r.name }, r.name), td(r.cluster, 'dly-first'), td(r.format, 'dly-first'), td(isNum(r.age_days) ? fmt.int(r.age_days) : dash), td(money(r.spend)),
      td(`${pc1(r.share)} vs ${pc1(r.base_share)}`), td(`${num2(r.attr_roas)} vs ${num2(r.base_attr_roas)}`), td(sgn(r.total, 3), 'num', { 'data-testid': 'dly-mover-effect' }), h('td', { class: 'dly-first dly-hook', title: r.hook || '' }, r.hook || dash)));
    out.push(h('div', null, table('dly-movers', head, rows), note(`Ad-attributed ROAS moved ${sgn(fam.change)} (${num2(fam.day)} vs ${num2(fam.base)}); each ad's effect is mix plus rate, summing to that move.`)));
  }
  return out;
}

function ledger(f) {
  const c = f.changes || [];
  if (!c.length) return note('No changes detected in the last 7 days.', 'dly-ledger-none');
  const head = h('tr', null, th('First day', 'dly-first'), th('Change', 'dly-first'), th('Size', 'dly-first'), th('Detail', 'dly-first'), th('Share of the day\'s Meta spend touched'), th('Source', 'dly-first'));
  const rows = c.slice().reverse().map((x) => h('tr', { 'data-type': x.type }, td(fmt.day(x.first_day), 'dly-first'), td(x.label, 'dly-first'), td(x.bucket, 'dly-first'), h('td', { class: 'dly-first dly-wrapcell' }, x.detail || dash), td(isNum(x.share_of_D_meta_spend) ? pc1(x.share_of_D_meta_spend) : dash), td(x.source, 'dly-first')));
  return h('div', null, table('dly-ledger', head, rows), note('Changes are listed, not blamed: each is a change that showed in the data in the 7 days up to this day. "Consistent with" is the strongest claim; the test that would settle one is a like-for-like control (same weekday, same structure, change held out).'));
}

function anomalies(f) {
  const a = f.anomalies || [];
  if (!a.length) return note('No metric is outside its normal noise range (|z| under 2).', 'dly-anom-none');
  return table('dly-anomalies', h('tr', null, th('Metric', 'dly-first'), th('Day'), th('Same-weekday median'), th('z-score')),
    a.map((x) => { const u = (f.metrics[x.key] || {}).unit; return h('tr', null, h('th', { class: 'dly-first', scope: 'row' }, x.label), td(fmtUnit(u, x.value)), td(fmtUnit(u, x.baseline_median)), td(sgn(x.z, 1), 'num dly-flag-' + x.flag)); }));
}

// ---------- sources
function klaviyo(f) {
  const k = (f.sections || {}).klaviyo;
  if (!k || !k.available) return note((k && k.note) || 'No Klaviyo data in the database for this day.');
  const head = h('tr', null, th('Kind', 'dly-first'), th('Channel', 'dly-first'), th('Sends'), th('Median sends, same weekday'), th('Delivered'), th('Opens'), th('Clicks'), th('Unsubscribes'), th('Klaviyo-attributed orders'), th('Klaviyo-attributed revenue'));
  const rows = k.cells.map((c) => h('tr', null, td(c.source, 'dly-first'), td(c.channel, 'dly-first'), td(fmt.int(c.day.sends)), td(fmt.int(c.base.sends)), td(fmt.int(c.day.delivered)), td(fmt.int(c.day.opens)), td(fmt.int(c.day.clicks)), td(fmt.int(c.day.unsubs)), td(fmt.int(c.day.orders)), td(money(c.day.revenue))));
  rows.push(h('tr', { class: 'dly-sumrow' }, td('All', 'dly-first'), td('', 'dly-first'), td(fmt.int(k.total.sends)), td(fmt.int(k.total_base.sends)), td(fmt.int(k.total.delivered)), td(fmt.int(k.total.opens)), td(fmt.int(k.total.clicks)), td(fmt.int(k.total.unsubs)), td(fmt.int(k.total.orders)), td(money(k.total.revenue))));
  const big = (k.biggest_sends || []).slice(0, 4).map((s) => `${s.name || '(unnamed)'} (${s.source} ${s.channel}, ${fmt.int(s.sends)} sends)`).join('; ');
  return h('div', null, table('dly-klaviyo', head, rows), note(`Basis: ${k.basis}. Loaded through ${fmt.day(k.loaded_through)}. ${big ? 'Biggest sends: ' + big + '.' : ''}`));
}
function google(f) {
  const g = (f.sections || {}).google;
  if (!g || !g.available) return note((g && g.note) || 'No Google data for this day.');
  const head = h('tr', null, th('Campaign type', 'dly-first'), th('Spend'), th('Median spend, same weekday'), th('Clicks'), th('Cost per click'), th('Google-reported purchases'), th('Google-reported revenue'), th('Upstack new customers'), th('Upstack new-customer revenue'), th('Upstack NC ROAS'));
  const rows = g.by_type.map((t) => h('tr', null, td(t.type, 'dly-first'), td(money(t.spend)), td(money(t.base_spend)), td(fmt.int(t.clicks)), td(money(t.cpc)), td(num2(t.google_purchases)), td(money(t.google_revenue)), td(fmt.int(t.nc)), td(money(t.nc_rev)), td(num2(t.nc_roas))));
  rows.push(h('tr', { class: 'dly-sumrow' }, td('All Google', 'dly-first'), td(money(g.spend)), td(money(g.base_spend)), td(fmt.int(g.clicks)), td(money(g.cpc)), td(num2(g.google_purchases)), td(money(g.google_revenue)), td(fmt.int(g.upstack_nc)), td(money(g.upstack_nc_rev)), td(num2(g.upstack_nc_roas))));
  return h('div', null, table('dly-google', head, rows), note(g.basis));
}
function amazon(f) {
  const a = (f.sections || {}).amazon;
  if (!a || !a.available) return note((a && a.note) || 'Amazon data is not in the database for this day.', 'dly-amazon-none');
  const head = h('tr', null, th('Amazon', 'dly-first'), th('Day'), th('Same-weekday median'));
  const row = (l, v, b) => h('tr', null, h('th', { class: 'dly-first', scope: 'row' }, l), td(v), td(b));
  return h('div', null, table('dly-amazon', head, [row('Revenue', money(a.revenue), money(a.base_revenue)), row('Ad spend', money(a.ad_spend), money(a.base_ad_spend)), row('Units', fmt.int(a.units), dash), row('New-customer revenue', money(a.nc_revenue), dash),
    row('Ad-attributed sales', money(a.attributed_sales), dash), row('ACOS (ad spend / ad-attributed sales)', pc1(a.acos), dash), row('TACoS (ad spend / total revenue)', pc1(a.tacos), dash)]),
    note(`Loaded through ${fmt.day(a.loaded_through)}${a.lag_days ? ` (${a.lag_days} days behind this day)` : ''}. ${a.basis}. ${a.note}`));
}
function placement(f) {
  const p = (f.sections || {}).placement;
  if (!p || !p.available) return note((p && p.note) || 'No placement breakdown for this day.');
  const head = h('tr', null, th('Placement', 'dly-first'), th('Spend share'), th('Same-weekday share'), th('CPM'), th('Baseline CPM'), th('CTR'), th('Baseline CTR'), th('Cost per link click'), th('Baseline cost per link click'), th('Meta-reported ROAS'));
  return h('div', null, table('dly-placement', head, p.rows.map((r) => h('tr', null, td(r.placement, 'dly-first'), td(pc1(r.share)), td(pc1(r.base_share)), td(money(r.cpm)), td(money(r.base_cpm)), td(pc2(r.ctr)), td(pc2(r.base_ctr)), td(money(r.cplc)), td(money(r.base_cplc)), td(num2(r.meta_roas))))), note(p.basis));
}
function ordersMix(f) {
  const M = f.metrics || {};
  const keys = [['pack_1', 'New-customer orders with 1 bottle'], ['pack_2', '2 bottles'], ['pack_3', '3 bottles'], ['pack_4p', '4 or more bottles'], ['discount_share', 'Using a discount code'], ['refund_share', 'Refunded share of new-customer revenue']];
  const rows = keys.filter(([k]) => M[k] && isNum(M[k].value)).map(([k, l]) => h('tr', null, h('th', { class: 'dly-first', scope: 'row' }, l), td(pc1(M[k].value)), td(pc1(M[k].baseline_median)), td(pc1(M[k].trailing_7d))));
  if (!rows.length) return note('Order mix (pack size, discount codes) starts 2026-07-16 and is not available for this day.');
  return h('div', null, table('dly-orders', h('tr', null, th('Shopify new-customer orders', 'dly-first'), th('Day'), th('Same-weekday median'), th('Previous 7 days')), rows), note('Rebills, Trybe gift orders, cancelled and test orders are excluded.'));
}

// ---------- scoreboard
function scoreboard(d) {
  const s = d.scoreboard || [];
  if (!s.length) return note('The scoreboard fills as days accumulate: a pattern appears once it has at least 6 days.', 'dly-score-none');
  const NAMES = { nc_roas: 'NC ROAS', cpm: 'CPM', ctr: 'CTR', conv: 'Conversion rate', aov: 'AOV' };
  const head = h('tr', null, th('Change type', 'dly-first'), th('Outcome (next 1 to 3 days vs same weekday)', 'dly-first'), th('Days (n)'), th('Rose more than 5%'), th('Fell more than 5%'), th('Rise rate'), th('Rise rate on all days'), th('Fall rate'), th('Fall rate on all days'), th('Median change'));
  return h('div', null, table('dly-scoreboard', head, s.map((r) => h('tr', null, h('th', { class: 'dly-first', scope: 'row' }, r.change_type.replace(/_/g, ' ')), td(NAMES[r.metric] || r.metric, 'dly-first'), td(fmt.int(r.n)), td(fmt.int(r.rises)), td(fmt.int(r.falls)), td(pc1(r.rise_rate)), td(pc1(r.base_rise_rate)), td(pc1(r.fall_rate)), td(pc1(r.base_fall_rate)), td(pctSigned(r.median_delta))))),
    note('Correlation, not cause: changes often land together and the 1 to 3 day outcome windows of neighbouring days overlap. Only patterns with at least 6 days are listed; compare each rate with the rate on all days.', 'dly-score-note'));
}

const CREATIVE_CUTS = ['cluster', 'icp', 'pain_point', 'emotional_trigger', 'line', 'media', 'format', 'vendor', 'age'];

export async function dailyPage(ctx) {
  const { params: p, signal } = ctx;
  showControls(false);
  document.title = 'Daily story · Lonvera';
  const d = await api('/api/daily-story', { day: p.day }, { signal });
  if (!ctx.alive()) return;
  const header = pageHeader({ title: 'Daily story', testid: 'dly-head', description: 'Why a day went the way it did: one short story, and every number behind it compared with the same weekday over the previous four weeks.' });
  if (!d.available) {
    ctx.mount(h('div', { class: 'page dly-page', 'data-testid': 'dly-page' }, header, h('p', { class: 'tl-prose', 'data-testid': 'dly-empty' }, d.reason)));
    return;
  }
  const f = d.facts;
  const dateInput = h('input', { type: 'date', value: d.day, min: d.first_day, max: d.last_day, 'aria-label': 'Day', 'data-testid': 'dly-date' });
  dateInput.addEventListener('change', () => { if (dateInput.value) setParams({ day: dateInput.value === d.last_day ? null : dateInput.value }); });
  const idx = d.days.findIndex((x) => x.day === d.day);
  const older = idx >= 0 && d.days[idx + 1] ? d.days[idx + 1].day : null;
  const newer = idx > 0 ? d.days[idx - 1].day : null;
  const nav = h('div', { class: 'ctl' }, h('span', { class: 'ctl-name' }, 'Day'), dateInput,
    older ? h('a', { class: 'seg', href: href('/daily', { day: older }), 'data-testid': 'dly-prev' }, 'Previous day') : null,
    newer ? h('a', { class: 'seg', href: href('/daily', { day: newer === d.last_day ? null : newer }), 'data-testid': 'dly-next' }, 'Next day') : null);
  const kids = [header, h('div', { class: 'dly-controls' }, filterRow(nav)), h('h2', { class: 'dly-day', 'data-testid': 'dly-day' }, dayLabel(d.day)), storyCard(d)];
  if (!f.readiness.ready) {
    kids.push(h('p', { class: 'tl-prose', 'data-testid': 'dly-notready' }, `Data not ready for this day: ${f.readiness.missing_core.join(', ')}.`));
  } else {
    kids.push(section('Key numbers, date windows as columns', metricsTable(f)));
    kids.push(section('What moved NC ROAS (exact decomposition)', decompTable(f)));
    kids.push(section('Outside normal noise', anomalies(f)));
    const cuts = f.cuts || {};
    kids.push(section('Product, stage and campaign structure', ...['product', 'stage', 'structure'].filter((k) => cuts[k]).map((k) => cutTable(cuts[k], true))));
    kids.push(section('Placement', placement(f)));
    const cl = f.creative_live;
    kids.push(section('Creative live that day', cl ? note(`${fmt.int(cl.families_live)} ad families with spend (${fmt.int(cl.ads_live)} ads; median ${fmt.int(cl.baseline_families_median)} on the same weekday), ${pc1(cl.tagged_spend_share)} of spend on tagged families. ${cl.note}.`, 'dly-live-note') : null,
      ...CREATIVE_CUTS.filter((k) => cuts[k]).map((k) => cutTable(cuts[k], k === 'cluster' || k === 'icp'))));
    kids.push(section('Top movers', ...movers(f)));
    kids.push(section('Changes in the last 7 days', ledger(f)));
    kids.push(section('Email and text (Klaviyo)', klaviyo(f)));
    kids.push(section('Google', google(f)));
    kids.push(section('Amazon', amazon(f)));
    kids.push(section('Orders', ordersMix(f)));
  }
  kids.push(section('Pattern scoreboard', scoreboard(d)));
  const cs = (d.story && d.story.cannot_see) || f.cannot_see || [];
  if (cs.length) kids.push(section('What the database cannot show', h('ul', { class: 'dly-list', 'data-testid': 'dly-cannot' }, cs.map((x) => h('li', null, x)))));
  ctx.mount(h('div', { class: 'page dly-page', 'data-testid': 'dly-page' }, kids));
}
