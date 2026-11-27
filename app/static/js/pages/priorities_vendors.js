// Lonvera, Vendors (/priorities/vendors): performance by creative vendor. One row per vendor for the chosen dates (spend, new-customer return, cost and click numbers of the ads the
// vendor made) next to where its concepts stand now; click a row to split it by product. Both tables come from GET /api/priorities/cuts (group=vendor and group=vendor,product), so
// every number matches the By cut view of the Creative Pipeline. Class names and test ids start with `vp-` (never ad-, ads-, sponsor, banner, promo: ad blockers hide those).
import { h, fmt, isNum, href, navigate, empty } from '../util.js';
import { api, ApiError } from '../api.js';
import { showControls } from '../controls.js';
import { presetRange, gradeOf, openCount, GRADE_MIN_SPEND } from './production_cuts.js';

const PRESETS = [['7', 'Last 7 days'], ['14', 'Last 14 days'], ['30', 'Last 30 days'], ['90', 'Last 90 days'], ['mtd', 'This month'], ['lastmonth', 'Last month']];
const PRODUCTS = [['', 'All products'], ['vita-bull', 'Vita Bull'], ['beef-liver', 'Beef Liver'], ['boneflex', 'Boneflex'], ['vita-belle', 'Vita Belle']];
const roasX = (v) => (isNum(v) ? fmt.roas(v) + 'x' : '–');
const pct2 = (v) => (isNum(v) ? (v * 100).toFixed(2) + '%' : '–');
const pct1 = (v) => (isNum(v) ? (v * 100).toFixed(1) + '%' : '–');
// [key, header, formatter, tooltip]; every key is a field of a row's stats
const COLS = [
  ['spend', 'Spend', fmt.money, 'Meta spend on the ads the vendor made, inside the chosen dates'],
  ['spend_share', '% of spend', pct1, 'Share of all spend in the table'],
  ['nc_revenue', 'NC revenue', fmt.money, 'Upstack new-customer revenue (first orders only)'],
  ['nc_roas', 'NC ROAS', roasX, 'New-customer revenue / spend (Upstack); the headline return'],
  ['nc_cpa', 'NC CPA', fmt.money2, 'Spend per new customer (Upstack)'],
  ['meta_roas', 'Meta ROAS', roasX, 'Meta-reported revenue / spend; counts returning customers, so a side number only'],
  ['cplc', 'Cost per link click', fmt.money2, 'Spend / link clicks'],
  ['ctr', 'CTR', pct2, 'Link clicks / impressions'],
  ['cpm', 'CPM', fmt.money2, 'Spend per 1,000 impressions'],
  ['conv_rate', 'Conv rate', pct2, 'New customers / link clicks'],
  ['aov', 'AOV', fmt.money2, 'Average order value of new customers'],
  ['ads_spending', 'Ads with spend', fmt.int, 'Unique ads that spent anything inside the chosen dates'],
  ['live_ads', 'Live ads', fmt.int, 'Unique ads that spent in the last two loaded days, whatever the dates chosen'],
];
const PIPE = [
  ['open', 'In pipeline', 'Concepts assigned to the vendor and not yet launched (assigned through ready to launch), as of now'],
  ['launched', 'Launched', 'Concepts the vendor delivered that are launched, all time'],
];
const NAME = { '(none)': 'No vendor on the tracker' };
const label = (v) => NAME[v] || v;

export async function vendorsPage(ctx) {
  const { params: p, signal } = ctx;
  showControls(false);
  document.title = 'Vendors · Lonvera';
  const st = { preset: PRESETS.some(([k]) => k === p.preset) ? p.preset : '30', product: PRODUCTS.some(([k]) => k === p.product) ? p.product : '', sort: p.sort || 'spend', dir: p.dir === 'asc' ? 'asc' : 'desc', hide: p.hide === '1', open: new Set((p.open || '').split('|').filter(Boolean)) };
  let vendors = null, split = null, loading = true, err = '';
  const root = h('div', { class: 'page vp-page', 'data-testid': 'vp-page' });
  const body = h('div', { class: 'vp-body' });
  const sync = () => navigate(href('/priorities/vendors', { preset: st.preset === '30' ? '' : st.preset, product: st.product, sort: st.sort === 'spend' ? '' : st.sort, dir: st.dir === 'desc' ? '' : st.dir, hide: st.hide ? 1 : '', open: [...st.open].join('|') }), { replace: true, render: false });

  async function load() {
    loading = true; err = ''; draw();
    const r = presetRange(st.preset);
    const q = { from: r.from, to: r.to, ...(st.product ? { product: st.product } : {}) };
    try {
      [vendors, split] = await Promise.all([api('/api/priorities/cuts', { ...q, group: 'vendor' }, { signal }), api('/api/priorities/cuts', { ...q, group: 'vendor,product' }, { signal })]);
    } catch (e) {
      if (!ctx.alive()) return;
      err = e instanceof ApiError ? e.message : 'Could not load the vendors.';
    }
    if (!ctx.alive()) return;
    loading = false; draw();
  }

  const valueOf = (row, key, total) => {
    if (key === 'vendor') return label(row.labels.vendor).toLowerCase();
    if (key === 'open') return openCount(row);
    if (key === 'launched') return row.stages.launched || 0;
    if (key === 'spend_share') return total ? (row.stats.spend || 0) / total : null;
    return row.stats[key];
  };
  const cell = (key, fmtFn, row, total, base, child) => {
    const v = valueOf(row, key, total);
    const heat = !child && base ? gradeOf(key, v, base.stats[key], row.stats.spend) : '';
    return h('td', { class: 'vp-n' + (heat ? ' prod-heat-' + heat : ''), 'data-metric': key }, fmtFn(v));
  };
  const pipeCells = (row) => PIPE.map(([k]) => h('td', { class: 'vp-n', 'data-metric': k }, (valueOf(row, k) || '–').toString()));

  function table() {
    const all = (vendors && vendors.rows) || [];
    const totalSpend = (vendors && vendors.totals.stats.spend) || 0;
    const shown = all.filter((r) => !st.hide || (r.stats.spend || 0) > 0);
    const dir = st.dir === 'asc' ? 1 : -1;
    const key = (r) => { const v = valueOf(r, st.sort, totalSpend); return v === null || v === undefined || (typeof v === 'number' && !Number.isFinite(v)) ? null : v; };
    shown.sort((a, b) => {
      const x = key(a), y = key(b);
      if (x === null || y === null) return x === y ? 0 : (x === null ? 1 : -1);
      return (typeof x === 'string' ? x.localeCompare(y) : x - y) * dir;
    });
    const sortBtn = (k, text, tip) => {
      const on = st.sort === k;
      return h('th', { class: 'vp-th' + (k === 'vendor' ? ' vp-l' : ''), 'aria-sort': on ? (st.dir === 'asc' ? 'ascending' : 'descending') : 'none', title: tip || '' },
        h('button', { type: 'button', class: 'vp-sortbtn', 'data-testid': 'vp-sort-' + k, onclick: () => { if (on) st.dir = st.dir === 'asc' ? 'desc' : 'asc'; else { st.sort = k; st.dir = k === 'vendor' ? 'asc' : 'desc'; } sync(); draw(); } }, text, h('span', { 'aria-hidden': 'true' }, on ? (st.dir === 'asc' ? ' ▲' : ' ▼') : '')));
    };
    const rows = [];
    for (const r of shown) {
      const v = r.labels.vendor;
      const kids = split ? split.rows.filter((c) => c.labels.vendor === v) : [];
      const isOpen = st.open.has(v);
      const toggle = h('button', { type: 'button', class: 'vp-tog', 'aria-expanded': String(isOpen), 'aria-label': `${label(v)} by product`, 'data-testid': 'vp-toggle', disabled: kids.length < 2 ? true : null,
        onclick: () => { if (isOpen) st.open.delete(v); else st.open.add(v); sync(); draw(); } }, kids.length < 2 ? '' : (isOpen ? '▾' : '▸'));
      rows.push(h('tr', { class: 'vp-row' + ((r.stats.spend || 0) > 0 ? '' : ' vp-quiet'), 'data-testid': 'vp-row', 'data-vendor': v },
        h('th', { scope: 'row', class: 'vp-l vp-name' }, toggle, h('span', { class: 'vp-vname' }, label(v))),
        COLS.map(([k, , f]) => cell(k, f, r, totalSpend, vendors.totals)), pipeCells(r)));
      if (isOpen) {
        for (const c of kids.sort((a, b) => (b.stats.spend || 0) - (a.stats.spend || 0))) {
          rows.push(h('tr', { class: 'vp-row vp-child' + ((c.stats.spend || 0) > 0 ? '' : ' vp-quiet'), 'data-testid': 'vp-child' },
            h('th', { scope: 'row', class: 'vp-l vp-name' }, h('span', { class: 'vp-pname' }, c.labels.product)),
            COLS.map(([k, , f]) => cell(k, f, c, totalSpend, null, true)), pipeCells(c)));
        }
      }
    }
    const t = vendors.totals;
    const totalRow = h('tr', { class: 'vp-row vp-total', 'data-testid': 'vp-total' }, h('th', { scope: 'row', class: 'vp-l vp-name' }, 'All vendors'),
      COLS.map(([k, , f]) => cell(k, f, { stats: t.stats, stages: t.stages, labels: {} }, totalSpend, null, true)), pipeCells({ stats: t.stats, stages: t.stages }));
    return h('div', { class: 'vp-scroll' }, h('table', { class: 'vp-table', 'data-testid': 'vp-table' },
      h('thead', null, h('tr', null, sortBtn('vendor', 'Vendor'), COLS.map(([k, t2, , tip]) => sortBtn(k, t2, tip)), PIPE.map(([k, t2, tip]) => sortBtn(k, t2, tip)))),
      h('tbody', null, rows, totalRow)));
  }

  function draw() {
    const presetBtns = PRESETS.map(([k, t]) => h('button', { type: 'button', class: 'seg', 'aria-pressed': String(st.preset === k), 'data-testid': 'vp-preset-' + k, onclick: () => { st.preset = k; sync(); load(); } }, t));
    const prodSel = h('select', { class: 'vp-select', 'aria-label': 'Product', 'data-testid': 'vp-product', onchange: (e) => { st.product = e.target.value; sync(); load(); } },
      PRODUCTS.map(([k, t]) => h('option', { value: k, selected: st.product === k ? true : null }, t)));
    const hideBox = h('label', { class: 'vp-check' }, h('input', { type: 'checkbox', checked: st.hide ? true : null, 'data-testid': 'vp-hide', onchange: (e) => { st.hide = e.target.checked; sync(); draw(); } }), ' Hide vendors with no spend');
    let main;
    if (loading) main = h('p', { role: 'status', class: 'vp-note' }, 'Loading…');
    else if (err || !vendors) main = empty(err || 'No vendor data yet.');
    else if (!vendors.rows.length) main = empty('No vendors in these dates.');
    else {
      const t = vendors.totals.stats;
      main = h('div', null,
        h('div', { class: 'vp-kpis', 'data-testid': 'vp-kpis' },
          [['Spend', fmt.money(t.spend)], ['NC ROAS', roasX(t.nc_roas)], ['NC CPA', fmt.money2(t.nc_cpa)], ['Vendors with spend', String(vendors.rows.filter((r) => (r.stats.spend || 0) > 0).length)]]
            .map(([l, v]) => h('div', { class: 'vp-kpi' }, h('div', { class: 'vp-kpi-l' }, l), h('div', { class: 'vp-kpi-v' }, v)))),
        table(),
        h('p', { class: 'vp-note', 'data-testid': 'vp-basis' }, vendors.basis + ` Vendor is the Vendor column of the Creative Tracker; ads with none are grouped as "No vendor on the tracker". Colour compares each vendor with the all-vendors row once it has spent ${fmt.money(GRADE_MIN_SPEND)} or more. In pipeline and Launched are today's counts, not tied to the dates. NC = new customer.`),
        vendors.warning ? h('p', { class: 'vp-note vp-warn' }, vendors.warning) : null);
    }
    const range = vendors ? `${fmt.day(vendors.from)} – ${fmt.day(vendors.to)}` : '';
    body.replaceChildren(
      h('div', { class: 'vp-controls' }, h('span', { class: 'segs', role: 'group', 'aria-label': 'Dates' }, presetBtns), prodSel, hideBox, h('span', { class: 'vp-range', 'data-testid': 'vp-range' }, range)),
      main);
  }

  root.append(h('header', { class: 'vp-head' }, h('h1', { class: 'vp-title' }, 'Vendors'), h('span', { class: 'vp-sub' }, 'Performance by creative vendor')), body);
  ctx.mount(root);
  await load();
}
