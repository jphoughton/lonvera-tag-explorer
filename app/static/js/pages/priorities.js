// Creative priorities (/priorities): the weekly one-sheeter as tables. Each section (Was working now isn't / Iterate / New / Proposed new clusters / Competitor angles / Stop) is a table with one
// row per concept, ALL COLLAPSED on load (nothing is remembered). A row shows Market Cluster | ICP | Pain Point | Story (full text) | Status | Concepts | one key number; the chevron button (or a click on
// the row) opens a detail panel directly under it with everything else. Print CSS expands every panel. Class names and test ids start with `pri-` (never ad-, ads-, sponsor, banner, promo).
import { h, fmt, isNum, href, navigate, qs } from '../util.js';
import { api } from '../api.js';
import { showControls } from '../controls.js';

const PRODUCT_ORDER = ['vita-bull', 'beef-liver', 'boneflex'];
const SECTIONS = [['faded', 'Was working, now isn’t', 'Lines with a sustained strong stretch (3+ weeks above the product average) that are now below average, collapsed or idle: when it worked, how it stopped, the former winners, a hedged diagnosis and a revive play.'],
                  ['iterate', 'Iterate on what is working', 'WORKING NOW and RISING lines: their ads beat the product average on enough spend; more concepts should help.'],
                  ['new', 'New and untested clusters', 'Lines with no ads (or almost none), ranked by evidence the demand exists (our adjacent winners, other products, competitors).'],
                  ['proposal', 'Proposed new market clusters', 'Drafts in the Communication Overview shape for Josh’s approval (NEW CLUSTER or a new line inside an existing cluster). Evidence is competitor activity and our adjacent results only; no performance is claimed.'],
                  ['competitor', 'Competitor angles we have not made', 'Angles competitors run that are not on our Communication Overview. Running a long time is a signal, not proof it performs.'],
                  ['stop', 'Stop or deprioritise', 'NOT WORKING and never worked: enough spend, weak over its history.']];
const STATUS_CLASS = { 'FADED': 'fade', 'COLLAPSED': 'bad', 'STOPPED': 'flat', 'WORKING NOW': 'ok', 'RISING': 'up', 'FADING / WAS WORKING': 'fade', 'NOT WORKING': 'bad', 'AVERAGE': 'flat', 'TOO EARLY': 'flat', 'UNTESTED / UNDER-TESTED': 'new', 'PROPOSED (draft)': 'new' };
const roasX = (v) => (isNum(v) ? fmt.roas(v) + 'x' : '–');
const pct = (v) => (isNum(v) ? fmt.pct(v) : '–');

function stat(k, v) { return h('span', { class: 'pri-stat' }, h('span', { class: 'pri-k' }, k), ' ', h('strong', null, v)); }

function numbers(r) {
  const m = r.metrics;
  if (r.section === 'iterate' || r.section === 'faded' || r.section === 'stop') {
    return [stat('Spend', fmt.money(m.spend)), stat('ROAS', roasX(m.roas)), stat('CPA', fmt.money(m.cpa)), stat('Ads tested', fmt.int(m.judged)),
      stat('Hit rate', `${pct(m.hit_rate)} (${m.hits}/${m.mature})`), stat('Last 30 days', `${roasX(m.roas_30d)} on ${fmt.money(m.spend_30d)}`), stat('Prior 30 days', `${roasX(m.roas_prior30)} on ${fmt.money(m.spend_prior30)}`),
      stat('New ads, 30 days', fmt.int(m.launches_30d)), stat('Live ads', fmt.int(m.live_ads)), stat('Upstack new-customer ROAS', roasX(m.nc_roas)), stat('ROAS, strong tags only', roasX(m.roas_strong_tags))];
  }
  if (r.section === 'competitor' || r.section === 'proposal') return [];
  return [stat('Ads tagged', fmt.int(m.tagged_ads)), stat('Spend so far', fmt.money(m.spend))];
}

// the one number on the collapsed row
function keyNumber(r, d) {
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

function labelled(label, value, desc) {
  return h('div', { class: 'pri-tag' }, h('div', { class: 'pri-label' }, label), h('div', { class: 'pri-dval' }, value || '–'), desc ? h('div', { class: 'pri-desc' }, desc) : null);
}

function detail(r) {
  const hooks = r.hooks && r.hooks.length ? h('div', { class: 'pri-hooks' }, h('span', { class: 'pri-k' }, r.section === 'proposal' ? 'Competitor hooks seen' : r.section === 'faded' ? 'Hooks from the working period'
    : r.section === 'new' ? 'Hooks to borrow (same product, adjacent winner)' : 'Proven hooks to iterate from'),
    h('ul', null, r.hooks.map((k) => h('li', null, k.ad_id != null ? h('a', { href: href('/ad/' + encodeURIComponent(k.ad_id), {}), title: k.ad }, k.hook) : k.hook,
      k.spend != null ? h('span', { class: 'pri-hookn' }, ` ${fmt.money(k.spend)} at ${roasX(k.roas)}`) : h('span', { class: 'pri-hookn' }, ` (${k.line_code})`))))) : null;
  return h('div', { class: 'pri-panel' },
    h('div', { class: 'pri-descs' }, labelled('Market Cluster', r.cluster, r.cluster_description), labelled('ICP', r.icp, r.icp_description), labelled('Pain Point', r.pain_point, r.pain_description)),
    r.then_now ? h('p', { class: 'pri-thennow', 'data-testid': 'pri-thennow' }, r.then_now) : null,
    r.when_it_worked ? h('div', { class: 'pri-story-block', 'data-testid': 'pri-faded' },
      h('p', null, h('strong', null, 'When it worked: '), r.when_it_worked), h('p', null, h('strong', null, 'When and how it stopped: '), r.how_it_stopped),
      h('p', null, h('strong', null, 'Diagnosis (consistent with): '), r.diagnosis), h('p', { 'data-testid': 'pri-play' }, h('strong', null, 'Revive play: '), r.revive_play),
      r.former_winners && r.former_winners.length ? h('div', null, h('span', { class: 'pri-k' }, 'Former winning ads'), h('ul', null, r.former_winners.map((w) => h('li', null,
        w.ad_id != null ? h('a', { href: href('/ad/' + encodeURIComponent(w.ad_id), {}) }, w.ad) : w.ad,
        ` (${w.kind}) ${fmt.money(w.spend)} at ${roasX(w.roas)}${w.peak_week ? ', peak week of ' + w.peak_week : ''}${w.stopped ? ', stopped ' + w.stopped : ''}`)))) : null,
      r.winners_summary ? h('p', { class: 'pri-k' }, `${r.winners_summary.still_working} of ${r.winners_summary.winners} winning ads still working; new ads in the last 30 days: ${r.winners_summary.launches_last_30d}${r.winners_summary.launches_in_peak != null ? ' (' + r.winners_summary.launches_in_peak + ' launched during the working stretch)' : ''}.`) : null) : null,
    r.competitor ? h('p', { class: 'pri-others', 'data-testid': 'pri-others' }, h('span', { class: 'pri-k' }, 'What others are running: '), h('strong', null, r.competitor.text), ' (running a long time, not proven performance)') : null,
    h('div', { class: 'pri-stats' }, numbers(r)),
    h('p', { class: 'pri-why' }, r.why),
    r.flags && r.flags.length ? h('p', { class: 'pri-flags' }, 'Flags: ' + r.flags.join('; ')) : null,
    h('p', { class: 'pri-k' }, `Line ${r.line_code}. Confidence: ${r.confidence}. Score ${r.score}.`),
    hooks);
}

const SECTION_LABEL = { faded: 'Was working, now isn’t', iterate: 'Iterate on what is working', new: 'New and untested', proposal: 'Proposed new cluster', competitor: 'Competitor angle', stop: 'Stop' };
const SECTION_ORDER = SECTIONS.map(([k]) => k);
const SECTION_NOTE = Object.fromEntries(SECTIONS.map(([k, , sub]) => [k, sub]));
const COLS = [['product', 'Product'], ['section', 'Section'], ['cluster', 'Market Cluster'], ['icp', 'ICP'], ['pain', 'Pain Point'], ['story', 'Story'], ['status', 'Status'], ['concepts', 'Concepts'], ['key', 'Key number']];

let uid = 0;
function setOpen(btn, panelRow, open) {
  btn.setAttribute('aria-expanded', String(open));
  btn.setAttribute('aria-label', (open ? 'Collapse: ' : 'Expand: ') + btn.dataset.name);
  panelRow.hidden = !open;
  panelRow.previousElementSibling.classList.toggle('pri-open', open);
}

// one row (collapsed) + its detail row (hidden). `printed` rows have no toggle and an always-visible detail (the print-only copy).
function rowPair(r, d, printed = false) {
  const id = 'pri-detail-' + (++uid);
  const name = `${r.cluster}, ${r.icp}, ${r.pain_point}`;
  const cell = (label, cls, ...kids) => h('td', { class: 'pri-c pri-c-' + cls, 'data-label': label }, ...kids);
  const detailRow = h('tr', { class: 'pri-detail', id, hidden: !printed, 'data-testid': 'pri-detail' }, h('td', { colspan: '10' }, detail(r)));
  const btn = h('button', { type: 'button', class: 'pri-toggle', 'aria-expanded': 'false', 'aria-controls': id, 'aria-label': 'Expand: ' + name, 'data-name': name, 'data-testid': 'pri-toggle' },
    h('span', { class: 'pri-chev', 'aria-hidden': 'true' }, '▸'));
  const tr = h('tr', { class: 'pri-row', 'data-testid': 'pri-card', 'data-section': r.section, 'data-product': r.product },
    h('td', { class: 'pri-c pri-c-tog' }, printed ? null : btn),
    cell('Product', 'product', (d.baselines[r.product] || {}).product_name || r.product),
    cell('Section', 'section', h('span', { class: 'pri-sec pri-sec-' + r.section, title: SECTION_NOTE[r.section] }, SECTION_LABEL[r.section])),
    cell('Market Cluster', 'cluster', r.cluster), cell('ICP', 'icp', r.icp), cell('Pain Point', 'pain', r.pain_point), cell('Story', 'story', r.story || '–'),
    cell('Status', 'status', h('span', { class: 'pri-status pri-st-' + (STATUS_CLASS[r.status] || 'flat'), 'data-testid': 'pri-status' }, r.status)),
    cell('Concepts', 'concepts', r.concepts ? `${r.concepts} concept${r.concepts === 1 ? '' : 's'}` : 'None'),
    cell('Key number', 'key', keyNumber(r, d)));
  if (!printed) {
    const toggle = () => setOpen(btn, detailRow, btn.getAttribute('aria-expanded') !== 'true');
    btn.addEventListener('click', (e) => { e.stopPropagation(); toggle(); });
    tr.addEventListener('click', (e) => { if (!e.target.closest('a, button')) toggle(); });
  }
  return { tr, detailRow, r };
}

function render(d) {
  const slugs = PRODUCT_ORDER.filter((s) => d.rows.some((r) => r.product === s));
  const sections = SECTION_ORDER.filter((k) => d.rows.some((r) => r.section === k));
  const pOrder = Object.fromEntries(PRODUCT_ORDER.map((s, i) => [s, i]));
  const sOrder = Object.fromEntries(SECTION_ORDER.map((s, i) => [s, i]));
  const pairs = d.rows.map((r) => rowPair(r, d));
  const state = { products: new Set(slugs), sections: new Set(sections), q: '', key: null, dir: 1 };
  const value = {
    product: (r) => pOrder[r.product], section: (r) => sOrder[r.section], cluster: (r) => r.cluster.toLowerCase(), icp: (r) => r.icp.toLowerCase(), pain: (r) => r.pain_point.toLowerCase(),
    story: (r) => (r.story || '').toLowerCase(), status: (r) => r.status.toLowerCase(), concepts: (r) => r.concepts || 0, key: (r) => keyNumber(r, d).toLowerCase(),
  };
  const tbody = h('tbody', { 'data-testid': 'pri-body' });
  const count = h('span', { class: 'pri-shown', 'aria-live': 'polite', 'data-testid': 'pri-shown' });
  const sortBtns = {};

  function apply() {
    const q = state.q.trim().toLowerCase();
    let list = pairs.filter((p) => state.products.has(p.r.product) && state.sections.has(p.r.section)
      && (!q || [p.r.cluster, p.r.icp, p.r.pain_point, p.r.story].some((t) => (t || '').toLowerCase().includes(q))));
    const def = (a, b) => (sOrder[a.r.section] - sOrder[b.r.section]) || (pOrder[a.r.product] - pOrder[b.r.product]) || (a.r.rank - b.r.rank);
    if (state.key) {
      const f = value[state.key];
      list = list.sort((a, b) => { const x = f(a.r), y = f(b.r); return (x < y ? -1 : x > y ? 1 : 0) * state.dir || def(a, b); });
    } else list = list.sort(def);
    tbody.replaceChildren(...list.flatMap((p) => [p.tr, p.detailRow]));
    count.textContent = `${list.length} of ${pairs.length} concepts shown`;
    for (const [k, b] of Object.entries(sortBtns)) b.closest('th').setAttribute('aria-sort', state.key === k ? (state.dir === 1 ? 'ascending' : 'descending') : 'none');
    return list;
  }
  const setAll = (open) => apply().forEach((p) => setOpen(p.tr.querySelector('.pri-toggle'), p.detailRow, open));          // Expand / Collapse all act on the filtered rows

  const chip = (set, key, label, testid) => {
    const b = h('button', { type: 'button', class: 'pri-chipbtn', 'aria-pressed': 'true', 'data-testid': testid, onclick: () => {
      if (set.has(key)) set.delete(key); else set.add(key);
      b.setAttribute('aria-pressed', String(set.has(key)));
      apply();
    } }, label);
    return b;
  };
  const filters = h('div', { class: 'pri-filters', role: 'group', 'aria-label': 'Filters' },
    h('div', { class: 'pri-fgroup', role: 'group', 'aria-label': 'Product' }, h('span', { class: 'pri-k' }, 'Product '), slugs.map((s) => chip(state.products, s, d.baselines[s].product_name, 'pri-f-product-' + s))),
    h('div', { class: 'pri-fgroup', role: 'group', 'aria-label': 'Section' }, h('span', { class: 'pri-k' }, 'Section '), sections.map((k) => chip(state.sections, k, SECTION_LABEL[k], 'pri-f-section-' + k))),
    h('label', { class: 'pri-search' }, h('span', { class: 'pri-k' }, 'Search '), h('input', { type: 'search', 'data-testid': 'pri-search', placeholder: 'cluster, ICP, pain point or story',
      oninput: (e) => { state.q = e.target.value; apply(); } })));

  const head = h('thead', null, h('tr', null, h('th', { class: 'pri-vh' }, 'Open'), COLS.map(([k, label]) => {
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
  const csvHref = '/api/priorities.csv' + qs({ week: d.week_start });
  const top = h('div', { class: 'pri-top' },
    h('h1', null, 'Creative priorities'),
    h('div', { class: 'pri-bar' }, h('label', { for: 'pri-week' }, 'Week '), weekSel,
      h('a', { class: 'btn', href: csvHref, download: '', 'data-testid': 'pri-csv' }, 'Download CSV'),
      h('button', { type: 'button', class: 'btn', 'data-testid': 'pri-print', onclick: () => window.print() }, 'Print / save as PDF'),
      h('span', { class: 'pri-expbar' },
        h('button', { type: 'button', class: 'btn pri-exp', 'data-testid': 'pri-all-expand', onclick: () => setAll(true) }, 'Expand all'),
        h('button', { type: 'button', class: 'btn pri-exp', 'data-testid': 'pri-all-collapse', onclick: () => setAll(false) }, 'Collapse all'))),
    h('p', { class: 'pri-lead' }, `Week of ${d.week_start}. Data through ${d.data_through}. Numbers are Meta-reported (ROAS = purchase value / spend, CPA = spend / purchases); Upstack new-customer ROAS beside them. `
      + 'This is prioritisation: consistent with the numbers, not proof that more concepts will work. ', h('span', { class: 'pri-screen-note' }, 'Every row is collapsed: open a row for the full detail.')),
    h('p', { class: 'pri-lead', 'data-testid': 'pri-comp-asof' }, d.competitors_as_of ? `Competitor ads: Meta Ad Library, as of ${d.competitors_as_of}; refreshed weekly. Running a long time is a signal, not proven performance.`
      + ((d.competitor_stale || []).length ? ` Stale (last pull failed, last good data kept): ${d.competitor_stale.join(', ')}.` : '') : 'No competitor ads loaded yet.'),
    d.stats && d.stats.status_counts_total ? h('p', { class: 'pri-lead', 'data-testid': 'pri-counts' }, 'Lines by status: ' + Object.entries(d.stats.status_counts_total).map(([k, v]) => `${k} ${v}`).join(', ')) : null,
    h('dl', { class: 'pri-legend', 'data-testid': 'pri-legend' }, (d.legend || []).flatMap((x) => [h('dt', null, x.tag), h('dd', null, x.meaning)])));
  const base = h('div', { class: 'pri-base' }, slugs.map((s) => { const b = d.baselines[s]; return h('div', null, h('strong', null, b.product_name), ` average ROAS ${roasX(b.roas)} since launch, ${roasX(b.roas_30d)} last 30 days, CPA ${fmt.money(b.cpa)}, hit rate ${pct(b.hit_rate)}`); }));

  // print copy (hidden on screen): a compact one-sheeter. Intro on its own first page; per product a fresh page with the headline columns, a 2-3 line strip under each row that has concepts to brief, the
  // Stop list as plain lines. Rows never split across pages, the header repeats on every page, fixed column widths.
  const trim = (t, n) => { t = String(t || ''); return t.length <= n ? t : t.slice(0, n).replace(/\s+\S*$/, '') + '\u2026'; };
  const strip = (r) => {
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
    h('tr', { class: 'pri-row' }, printCell('section', h('span', { class: 'pri-sec pri-sec-' + r.section }, SECTION_LABEL[r.section])), printCell('cluster', r.cluster), printCell('icp', r.icp), printCell('pain', r.pain_point),
      printCell('story', r.story || '\u2013'), printCell('status', h('span', { class: 'pri-status pri-st-' + (STATUS_CLASS[r.status] || 'flat') }, PRINT_STATUS[r.status] || r.status)),
      printCell('concepts', r.concepts ? String(r.concepts) : '\u2013'), printCell('key', keyNumber(r, d))),
    h('tr', { class: 'pri-strip' }, h('td', { colspan: '8' }, strip(r).map((t) => h('div', null, t)))));
  const widths = [10, 13, 13, 14, 22, 8, 7, 13];
  const printHead = () => h('thead', null, h('tr', null, COLS.filter(([k]) => k !== 'product').map(([k, label]) => h('th', { scope: 'col' }, k === 'concepts' ? 'To brief' : label))));
  const printBlocks = h('div', { class: 'pri-print', 'data-testid': 'pri-print-copy', 'aria-hidden': 'true' }, slugs.map((s) => {
    const mine = d.rows.filter((r) => r.product === s).sort((a, b) => (sOrder[a.section] - sOrder[b.section]) || (a.rank - b.rank));
    const shown = mine.filter((r) => r.section !== 'stop' && r.concepts > 0);
    const stops = mine.filter((r) => r.section === 'stop');
    const b = d.baselines[s];
    const skipped = mine.length - shown.length - stops.length;
    return h('section', { class: 'pri-product', 'data-testid': 'pri-product-' + s },
      h('h2', null, `${b.product_name}`, h('span', { class: 'pri-h2note' }, ` average ROAS ${roasX(b.roas)} since launch, ${roasX(b.roas_30d)} last 30 days, CPA ${fmt.money(b.cpa)}`)),
      h('table', { class: 'pri-table pri-ptable' }, h('colgroup', null, widths.map((w) => h('col', { style: { width: w + '%' } }))), printHead(), shown.map(printRow)),
      skipped > 0 ? h('p', { class: 'pri-pnote' }, `${skipped} more line${skipped === 1 ? '' : 's'} with no concepts to brief this week are on the page, not printed.`) : null,
      stops.length ? h('div', { class: 'pri-stoplist', 'data-testid': 'pri-stoplist' }, h('h3', null, `Stop or deprioritise (${stops.length})`),
        h('ul', null, stops.map((r) => h('li', null, h('strong', null, `${r.cluster} / ${r.icp} / ${r.pain_point}`), `: ${roasX((r.metrics || {}).roas)} lifetime vs ${roasX(b.roas)} product, ${fmt.money((r.metrics || {}).spend)} spent.`)))) : null);
  }));

  apply();
  return h('div', { class: 'page pri-page', 'data-testid': 'pri-page' }, top, base, h('div', { class: 'pri-screen' }, filters, count, table), printBlocks);
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
