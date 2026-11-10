// Creative priorities (/priorities): the weekly one-sheeter. Each item reads as a brief: Market Cluster -> ICP -> Pain Point -> Story, full text, each with a plain-English
// description underneath; numbers, hooks and the reason sit below. New to produce / Iterate on what works, per product. Print view = this page with print CSS.
// Class names and test ids start with `pri-` (never ad-, ads-, sponsor, banner, promo: ad blockers hide those).
import { h, fmt, isNum, href, navigate, qs } from '../util.js';
import { api } from '../api.js';
import { showControls } from '../controls.js';

const PRODUCT_ORDER = ['vita-bull', 'beef-liver', 'boneflex'];
const SECTIONS = [['iterate', 'Iterate on what is working', 'WORKING NOW and RISING lines: their ads beat the product average on enough spend; more concepts should help.'],
                  ['revive', 'Revive or rework', 'FADING / WAS WORKING: strong earlier or over its lifetime, now below the product average or idle.'],
                  ['new', 'New and untested clusters', 'Lines with no ads (or almost none), ranked by evidence the demand exists (our adjacent winners, other products, competitors).'],
                  ['proposal', 'Proposed new market clusters', 'Drafts in the Communication Overview shape for Josh\u2019s approval (NEW CLUSTER or a new line inside an existing cluster). Evidence is competitor activity and our adjacent results only; no performance is claimed.'],
                  ['competitor', 'Competitor angles we have not made', 'Angles competitors run that are not on our Communication Overview. Running a long time is a signal, not proof it performs.'],
                  ['stop', 'Stop or deprioritise', 'NOT WORKING: enough spend, weak over its history.']];
const STATUS_CLASS = { 'WORKING NOW': 'ok', 'RISING': 'up', 'FADING / WAS WORKING': 'fade', 'NOT WORKING': 'bad', 'AVERAGE': 'flat', 'TOO EARLY': 'flat', 'UNTESTED / UNDER-TESTED': 'new', 'PROPOSED (draft)': 'new' };
const roasX = (v) => (isNum(v) ? fmt.roas(v) + 'x' : '–');
const pct = (v) => (isNum(v) ? fmt.pct(v) : '–');

function tag(label, value, desc, cls) {
  return h('div', { class: 'pri-tag pri-t-' + cls, 'data-testid': 'pri-' + cls }, h('div', { class: 'pri-label' }, label), h('div', { class: 'pri-value' }, value || '–'), desc ? h('div', { class: 'pri-desc' }, desc) : null);
}

function stat(k, v) { return h('span', { class: 'pri-stat' }, h('span', { class: 'pri-k' }, k), ' ', h('strong', null, v)); }

function numbers(r) {
  const m = r.metrics;
  if (r.section === 'iterate' || r.section === 'revive' || r.section === 'stop') {
    return [stat('Spend', fmt.money(m.spend)), stat('ROAS', roasX(m.roas)), stat('CPA', fmt.money(m.cpa)), stat('Ads tested', fmt.int(m.judged)),
      stat('Hit rate', `${pct(m.hit_rate)} (${m.hits}/${m.mature})`), stat('Last 30 days', `${roasX(m.roas_30d)} on ${fmt.money(m.spend_30d)}`), stat('Prior 30 days', `${roasX(m.roas_prior30)} on ${fmt.money(m.spend_prior30)}`),
      stat('New ads, 30 days', fmt.int(m.launches_30d)), stat('Live ads', fmt.int(m.live_ads)), stat('Upstack new-customer ROAS', roasX(m.nc_roas)), stat('ROAS, strong tags only', roasX(m.roas_strong_tags))];
  }
  if (r.section === 'competitor') return [];
  return [stat('Ads tagged', fmt.int(m.tagged_ads)), stat('Spend so far', fmt.money(m.spend))];
}

function card(r) {
  const hooks = r.hooks && r.hooks.length ? h('div', { class: 'pri-hooks' }, h('span', { class: 'pri-k' }, r.section === 'proposal' ? 'Competitor hooks seen' : r.section === 'new' ? 'Hooks to borrow (same product, adjacent winner)' : r.section === 'revive' ? 'Old winners\u2019 hooks' : 'Proven hooks to iterate from'),
    h('ul', null, r.hooks.map((k) => h('li', null, k.ad_id != null ? h('a', { href: href('/ad/' + encodeURIComponent(k.ad_id), {}), title: k.ad }, k.hook) : k.hook,
      k.spend != null ? h('span', { class: 'pri-hookn' }, ` ${fmt.money(k.spend)} at ${roasX(k.roas)}`) : h('span', { class: 'pri-hookn' }, ` (${k.line_code})`))))) : null;
  return h('article', { class: 'pri-card pri-' + r.section, 'data-testid': 'pri-card' },
    h('div', { class: 'pri-head' }, h('span', { class: 'pri-rank' }, '#' + r.rank), h('span', { class: 'pri-status pri-st-' + (STATUS_CLASS[r.status] || 'flat'), 'data-testid': 'pri-status' }, r.status),
      h('span', { class: 'pri-concepts' }, r.concepts ? `${r.concepts} new concept${r.concepts === 1 ? '' : 's'} this week` : 'No new concepts'),
      h('span', { class: 'pri-conf pri-conf-' + r.confidence, title: 'Confidence in this row' }, r.confidence + ' confidence'), h('span', { class: 'pri-code' }, r.line_code)),
    h('div', { class: 'pri-brief' },
      tag('Market Cluster', r.cluster, r.cluster_description, 'cluster'), h('span', { class: 'pri-arrow', 'aria-hidden': 'true' }, '→'),
      tag('ICP', r.icp, r.icp_description, 'icp'), h('span', { class: 'pri-arrow', 'aria-hidden': 'true' }, '→'),
      tag('Pain Point', r.pain_point, r.pain_description, 'pain'), h('span', { class: 'pri-arrow', 'aria-hidden': 'true' }, '→'),
      tag('Story', r.story, null, 'story')),
    h('div', { class: 'pri-second' },
      r.then_now ? h('p', { class: 'pri-thennow', 'data-testid': 'pri-thennow' }, r.then_now) : null,
      r.competitor ? h('p', { class: 'pri-others', 'data-testid': 'pri-others' }, h('span', { class: 'pri-k' }, 'What others are running: '), h('strong', null, r.competitor.text), ' (running a long time, not proven performance)') : null,
      h('div', { class: 'pri-stats' }, numbers(r)),
      h('p', { class: 'pri-why' }, r.why),
      r.approach ? h('p', { class: 'pri-approach', 'data-testid': 'pri-approach' }, h('span', { class: 'pri-k' }, 'Suggested approach: '), r.approach) : null,
      r.flags && r.flags.length ? h('p', { class: 'pri-flags' }, 'Flags: ' + r.flags.join('; ')) : null,
      hooks));
}

function render(d) {
  const byProduct = {};
  for (const r of d.rows) (byProduct[r.product] = byProduct[r.product] || []).push(r);
  const slugs = PRODUCT_ORDER.filter((s) => byProduct[s]);
  const weekSel = h('select', { id: 'pri-week', 'data-testid': 'pri-week', 'aria-label': 'Week', onchange: (e) => navigate(href('/priorities', { week: e.target.value })) },
    d.weeks.map((w) => h('option', { value: w.week_start, selected: w.week_start === d.week_start }, 'Week of ' + w.week_start)));
  const csvHref = '/api/priorities.csv' + qs({ week: d.week_start });
  const top = h('div', { class: 'pri-top' },
    h('h1', null, 'Creative priorities'),
    h('div', { class: 'pri-bar' }, h('label', { for: 'pri-week' }, 'Week '), weekSel,
      h('a', { class: 'btn', href: csvHref, download: '', 'data-testid': 'pri-csv' }, 'Download CSV'),
      h('button', { type: 'button', class: 'btn', 'data-testid': 'pri-print', onclick: () => window.print() }, 'Print / save as PDF')),
    h('p', { class: 'pri-lead' }, `Week of ${d.week_start}. Data through ${d.data_through}. Numbers are Meta-reported (ROAS = purchase value / spend, CPA = spend / purchases); Upstack new-customer ROAS beside them. `
      + 'This is prioritisation: consistent with the numbers, not proof that more concepts will work.'),
    h('p', { class: 'pri-lead', 'data-testid': 'pri-comp-asof' }, d.competitors_as_of ? `Competitor ads: Meta Ad Library, as of ${d.competitors_as_of}; refreshed weekly. Running a long time is a signal, not proven performance.`
      + ((d.competitor_stale || []).length ? ` Stale (last pull failed, last good data kept): ${d.competitor_stale.join(', ')}.` : '') : 'No competitor ads loaded yet.'),
    d.stats && d.stats.status_counts_total ? h('p', { class: 'pri-lead', 'data-testid': 'pri-counts' }, 'Lines by status: ' + Object.entries(d.stats.status_counts_total).map(([k, v]) => `${k} ${v}`).join(', ')) : null,
    h('dl', { class: 'pri-legend', 'data-testid': 'pri-legend' }, (d.legend || []).flatMap((x) => [h('dt', null, x.tag), h('dd', null, x.meaning)])));
  const base = h('div', { class: 'pri-base' }, slugs.map((s) => { const b = d.baselines[s]; return h('div', null, h('strong', null, b.product_name), ` average ROAS ${roasX(b.roas)} since launch, ${roasX(b.roas_30d)} last 30 days, CPA ${fmt.money(b.cpa)}, hit rate ${pct(b.hit_rate)}`); }));
  const sections = slugs.map((s) => h('section', { class: 'pri-product', 'data-testid': 'pri-product-' + s },
    h('h2', null, d.baselines[s].product_name),
    SECTIONS.map(([k, title, sub]) => {
      const rows = byProduct[s].filter((r) => r.section === k).sort((a, b) => a.rank - b.rank);
      if (k === 'competitor' && !d.competitors_as_of) return null;
      if (k === 'proposal' && !rows.length) return null;
      return h('div', { class: 'pri-section', 'data-testid': 'pri-section-' + k },
        h('h3', null, title, h('span', { class: 'pri-sub' }, ' ' + sub)),
        rows.length ? rows.map(card) : h('p', { class: 'pri-none' }, 'Nothing qualifies this week.'));
    })));
  return h('div', { class: 'page pri-page', 'data-testid': 'pri-page' }, top, base, sections);
}

export async function prioritiesPage(ctx) {
  showControls(false);
  let d;
  try {
    d = await api('/api/priorities', ctx.params.week ? { week: ctx.params.week } : {}, { signal: ctx.signal });
  } catch (e) {
    if (e && e.status === 404) {
      ctx.mount(h('div', { class: 'page pri-page' }, h('h1', null, 'Creative priorities'), h('p', { 'data-testid': 'pri-empty' }, e.message)));
      return;
    }
    throw e;
  }
  ctx.mount(render(d));
}
