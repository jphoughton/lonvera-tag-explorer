// Projection page: Inventory supply.
// What it shows: when each product runs out at each warehouse, when an order has to be placed and land, the purchase orders and Amazon FBA
// transfers behind it, what stock is worth and what the orders cost in cash. Orders, transfers and stock counts are edited in place; a plan
// can be proposed, compared and then accepted. Nothing is written until you accept or edit a cell.
import { events } from './lib/api.js';
import { confirmBox, setChildren } from './lib/util.js';

const ID = 'is-style';
const CSS = `
.is-cols { display: grid; grid-template-columns: minmax(0, 1fr); gap: var(--lv-sp-4); }
.is-card-head { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: baseline; gap: 2px var(--lv-sp-4); margin-bottom: var(--lv-sp-2); }
.is-card-head h2 { margin: 0; font-size: var(--lv-fs-md); font-weight: var(--lv-fw-heavy); }
.is-card-head .sub { margin: 0; font-size: var(--lv-fs-xs); color: var(--lv-muted); }
.is-chips { display: flex; flex-wrap: wrap; gap: var(--lv-sp-2); align-items: center; }
.is-chips .pg-chip { white-space: normal; max-width: 100%; line-height: 1.3; padding-top: 2px; padding-bottom: 2px; }
.is-checks { display: flex; flex-direction: column; gap: var(--lv-sp-2); margin: var(--lv-sp-2) 0 0; padding: 0; list-style: none; }
.is-check { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 2px var(--lv-sp-3); align-items: start; font-size: var(--lv-fs-sm); }
.is-check b { font-weight: var(--lv-fw-semi); }
.is-check p { margin: 0; color: var(--lv-muted); }
details.is-more > summary { cursor: pointer; font-weight: var(--lv-fw-semi); font-size: var(--lv-fs-sm); min-height: 28px; display: flex; align-items: center; }
.is-scroll { overflow-x: auto; max-width: 100%; }
.is-tbl { width: 100%; border-collapse: collapse; font-size: var(--lv-fs-sm); font-variant-numeric: tabular-nums; }
.is-tbl th, .is-tbl td { padding: 3px 8px; text-align: right; border-bottom: 1px solid var(--lv-line-soft); white-space: nowrap; vertical-align: middle; }
.is-tbl th:first-child, .is-tbl td:first-child, .is-tbl .l { text-align: left; }
.is-tbl thead th { position: sticky; top: 0; background: var(--lv-surface-2); font-size: var(--lv-fs-2xs); text-transform: uppercase; letter-spacing: var(--lv-track-kicker); color: var(--lv-muted); font-weight: var(--lv-fw-bold); }
.is-tbl tfoot td { font-weight: var(--lv-fw-heavy); border-top: 2px solid var(--lv-total-rule); background: var(--lv-total-bg); }
.is-tbl td.neg { color: var(--lv-neg); }
.is-tbl tr.is-dim td { color: var(--lv-muted); }
.is-tbl tr.is-flag td:first-child { box-shadow: inset 3px 0 0 var(--lv-warn); }
.is-tbl .pg-chip { margin-right: 4px; }
.is-in { font: inherit; font-size: var(--lv-fs-sm); min-height: 26px; width: 100%; min-width: 64px; padding: 0 6px; text-align: right; font-variant-numeric: tabular-nums; font-weight: var(--lv-fw-semi); color: var(--lv-ink);
  background: var(--lv-input-bg); border: 1px solid var(--lv-input-edge); border-radius: var(--lv-r-xs); }
select.is-in, .is-tbl .l .is-in { text-align: left; }
.is-in:hover { background: var(--lv-input-hover); }
.is-in:focus-visible { outline: 2px solid var(--lv-focus); outline-offset: 1px; background: var(--lv-surface); }
.is-in.bad { border-color: var(--lv-bad); outline: 2px solid var(--lv-bad); }
.is-in.date { min-width: 118px; }
.is-in.wide { min-width: 120px; }
.is-in.ghost::placeholder { color: var(--lv-muted); }
.is-in.derived { font-style: italic; font-weight: var(--lv-fw-regular); color: var(--lv-ink-2); background: var(--lv-surface); }
.is-in.w-po { min-width: 92px; } .is-in.w-prod { min-width: 112px; } .is-in.w-vendor { min-width: 100px; } .is-in.w-wh { min-width: 142px; }
.is-in.w-status { min-width: 112px; } .is-in.w-num { min-width: 84px; } .is-in.w-pct { min-width: 66px; }
.is-scroll.tall { max-height: 560px; overflow-y: auto; }
.is-in.ovr { border-color: var(--lv-info); box-shadow: inset 0 -2px 0 var(--lv-info); }
.is-tbl tr.is-ovr td:first-child { box-shadow: inset 3px 0 0 var(--lv-info); }
.is-tbl tr.is-grp td { background: var(--lv-band-bg); font-size: var(--lv-fs-2xs); text-transform: uppercase; letter-spacing: var(--lv-track-kicker); font-weight: var(--lv-fw-bold); color: var(--lv-muted); text-align: left; }
.is-tbl td.src { white-space: normal; text-align: left; color: var(--lv-muted); font-size: var(--lv-fs-xs); min-width: 160px; }
.is-tbl td.unit { text-align: left; color: var(--lv-muted); }
.is-poc { border: 1px solid var(--lv-line); border-radius: var(--lv-r-sm); background: var(--lv-surface); margin-bottom: var(--lv-sp-2); }
.is-poc > summary { list-style: none; display: flex; flex-direction: column; gap: 2px; padding: var(--lv-sp-3); min-height: 44px; cursor: pointer; }
.is-poc > summary::-webkit-details-marker { display: none; }
.is-poc > summary:hover { background: var(--lv-hover); }
.is-poc .top { display: flex; justify-content: space-between; align-items: center; gap: var(--lv-sp-2); font-weight: var(--lv-fw-heavy); font-size: var(--lv-fs-md); }
.is-poc .sub2 { color: var(--lv-muted); font-size: var(--lv-fs-sm); }
.is-poc-body { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--lv-sp-2) var(--lv-sp-3); padding: 0 var(--lv-sp-3) var(--lv-sp-3); }
.is-poc-f { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.is-poc-f > label { font-size: var(--lv-fs-xs); font-weight: var(--lv-fw-semi); color: var(--lv-ink-2); }
.is-poc-f .is-in { min-width: 0; width: 100%; text-align: left; }
.is-poc-f .ro { font-variant-numeric: tabular-nums; font-size: var(--lv-fs-md); min-height: 26px; display: flex; align-items: center; }
.is-poc-wide { grid-column: 1 / -1; }
.is-poc.is-ovr { border-left: 3px solid var(--lv-info); }
.is-poc.is-flag { border-left: 3px solid var(--lv-warn); }
.is-poc.is-add { background: var(--lv-surface-2); }
.is-poc.is-dim > summary { color: var(--lv-muted); }
.is-cardsum { margin: var(--lv-sp-2) 0 0; font-size: var(--lv-fs-sm); font-weight: var(--lv-fw-semi); color: var(--lv-ink-2); }
.is-note { margin: 0 0 var(--lv-sp-2); font-size: var(--lv-fs-sm); color: var(--lv-ink-2); }
.is-add td { background: var(--lv-surface-2); }
.is-status { min-height: 1.2em; font-size: var(--lv-fs-xs); color: var(--lv-muted); margin: var(--lv-sp-1) 0 0; }
.is-status.err { color: var(--lv-bad); font-weight: var(--lv-fw-semi); }
.is-seg { display: inline-flex; flex-wrap: wrap; gap: 0; border: 1px solid var(--lv-line-strong); border-radius: var(--lv-r-sm); overflow: hidden; }
.is-seg button { font: inherit; font-size: var(--lv-fs-sm); font-weight: var(--lv-fw-semi); min-height: 30px; padding: 0 var(--lv-sp-3); border: 0; border-right: 1px solid var(--lv-line); background: var(--lv-surface); color: var(--lv-ink-2); cursor: pointer; }
.is-seg button:last-child { border-right: 0; }
.is-seg button:hover { background: var(--lv-hover); }
.is-seg button[aria-pressed="true"] { background: var(--lv-ink); color: var(--lv-bg); }
.is-tools { display: flex; flex-wrap: wrap; gap: var(--lv-sp-2) var(--lv-sp-3); align-items: center; }
.is-tools label { font-size: var(--lv-fs-xs); font-weight: var(--lv-fw-semi); color: var(--lv-ink-2); display: inline-flex; gap: 6px; align-items: center; }
.is-tools select { min-height: 28px; }
.is-plan-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: var(--lv-sp-3); }
.is-plan-cards > div { background: var(--lv-surface-2); border-radius: var(--lv-r-sm); padding: var(--lv-sp-3); }
.is-plan-cards b { display: block; font-size: var(--lv-fs-xl); font-weight: var(--lv-fw-heavy); letter-spacing: -0.02em; font-variant-numeric: tabular-nums; }
.is-plan-cards span { font-size: var(--lv-fs-xs); color: var(--lv-muted); }
.is-list { margin: var(--lv-sp-2) 0 0; padding-left: 18px; font-size: var(--lv-fs-sm); color: var(--lv-ink-2); }
.is-actions { display: flex; flex-wrap: wrap; gap: var(--lv-sp-2); align-items: center; margin-top: var(--lv-sp-3); }
.is-small-tables { display: flex; flex-wrap: wrap; gap: var(--lv-sp-4) var(--lv-sp-5); margin-top: var(--lv-sp-3); }
.is-small-tables > div { flex: 0 1 auto; max-width: 100%; min-width: 0; }
.is-small-tables h3 { margin: 0 0 var(--lv-sp-2); font-size: var(--lv-fs-sm); font-weight: var(--lv-fw-heavy); }
.is-empty { border: 1px dashed var(--lv-line-strong); border-radius: var(--lv-r-sm); padding: var(--lv-sp-4); color: var(--lv-muted); font-size: var(--lv-fs-sm); }
@media (min-width: 1100px) { .is-cols.two { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 640px) { .is-in { min-height: 44px; } .is-seg button { min-height: 44px; } }
`;

function ensureStyle() {
  if (document.getElementById(ID)) return;
  const s = document.createElement('style');
  s.id = ID;
  s.textContent = CSS;
  document.head.append(s);
}

const PN = { bull: 'Vital Bull', liver: 'Beef Liver', boneflex: 'Boneflex', belle: 'Vital Belle', flow: 'Vital Flow' };
const PRODUCTS = Object.keys(PN);
const LN = { revamp: 'Revamp Utah', amazon: 'Amazon FBA', intl: 'International 3PL' };
const WH = { revamp: 'Revamp Utah', intl: 'International 3PL' };
const PO_STATUS = ['planned', 'ordered', 'in transit', 'landed', 'cancelled'];
const TR_STATUS = ['planned', 'shipped', 'received', 'cancelled'];
const STATUS_TONE = { planned: 'neutral', ordered: 'info', 'in transit': 'info', landed: 'good', cancelled: 'neutral', shipped: 'info', received: 'good' };
const FLAG_WORDS = {
  duplicate_po: 'Duplicate?', planned_order_in_past: 'Order date passed', landed_still_inbound: 'Land date passed',
  past_land_not_landed: 'Land date passed', transfer_receive_before_ship: 'Arrives first', missing_unit_cost: 'No unit cost',
  missing_vendor_lead: 'No lead time', outside_axis: 'Outside window', missing_deposit_pct: 'No deposit',
};
const PRODUCT_SLOT = { bull: 0, boneflex: 1, liver: 2, belle: 3, flow: 4 };
const LOC_SLOT = { revamp: 0, amazon: 1, intl: 2 };

export async function render(el, ctx) {
  ensureStyle();
  const { h, fmt, ui, charts, toast } = ctx;
  const own = [];                                              // chart instances created here
  const root = h('div', { class: 'pg-page' });
  el.replaceChildren(root);
  ui.loading(root, 'Loading inventory supply');

  const get = (p, params) => ctx.api.get('inventory-supply/' + p, params);
  const isPlan = !!ctx.scenarioInfo.is_default;                                // the plan of record edits stored rows; a copy keeps its own changes
  const q = isPlan ? '' : `?scenario=${encodeURIComponent(ctx.scenario)}`;
  let sum, pos, trs, stock, vend, outlay, value, vs, hist;
  const loadAll = async () => {
    [sum, pos, trs, stock, vend, outlay, value, vs, hist] = await Promise.all([get('summary'), get('po'), get('transfers'), get('stock'), get('vendors'),
      get('outlay'), get('value'), get('variables'), get('history')]);
  };
  try { await loadAll(); } catch (e) { ui.error(root, e, ctx.reload); return undefined; }
  if (!ctx.alive()) return undefined;

  const star = (iso) => (iso ? `${fmt.date(iso)}*` : '');
  const num0 = (v) => fmt.num(v, 0);
  const PNAME = (p) => PN[p] || p;

  // ---------------------------------------------------------------- header and TLDR
  const headHost = h('div');
  function tldrParts() {
    const t = sum.tldr;
    const fo = t.first_stockout;
    const hasStock = sum.stats.length > 0 && sum.stats.some((s) => s.available_today);
    let line;
    if (!hasStock) line = 'No stock counts yet. Enter a count to see when stock runs out.';
    else if (!fo) line = `No product runs out in the next ${sum.horizon} months at the current demand.`;
    else {
      const by = fo.status === 'OVERDUE' ? `the order was due ${fmt.date(fo.order_by)}` : `order by ${fmt.date(fo.order_by)}*`;
      line = `${PNAME(fo.product)} at ${fo.location === 'intl' ? 'the ' : ''}${LN[fo.location]} runs out ${fmt.date(fo.est_stockout)}*; ${by}.`;
    }
    const nx = t.next_order_by;
    const old = t.oldest_stock;
    const kpis = [
      { label: 'First stock-out', value: fo ? star(fo.est_stockout) : 'None', note: fo ? PNAME(fo.product) : `in ${sum.horizon} months`, tone: fo ? (fo.status === 'OVERDUE' ? 'bad' : fo.status === 'ORDER SOON' ? 'warn' : null) : 'good' },
      { label: 'Order by', value: nx ? star(nx.order_by) : 'None', note: nx ? (nx.status === 'OVERDUE' ? 'overdue' : nx.status === 'ORDER SOON' ? 'order soon' : 'on time') : '', tone: nx ? (nx.status === 'OVERDUE' ? 'bad' : nx.status === 'ORDER SOON' ? 'warn' : 'good') : null },
      { label: `Cash out, next ${t.cash_window_days} days`, value: `${fmt.compact(t.cash_next_90_days)}*`, note: 'deposits and balances' },
      { label: 'Stock at landed cost', value: fmt.compact(t.inventory_value_now), note: 'all locations' },
    ];
    if (old) kpis.push({ label: 'Oldest stock count', value: `${old.age_days} day${old.age_days === 1 ? '' : 's'}`, note: LN[old.location], tone: old.age_days > sum.stale_stock_days ? 'warn' : 'good' });
    return { line, kpis };
  }
  const newPoBtn = h('button', { type: 'button', class: 'btn primary', onclick: () => { const a = document.getElementById('is-po-add'); if (a && a.tagName === 'DETAILS') a.open = true; a?.querySelector('select, input')?.focus(); } }, 'Add purchase order');
  const planBtn = h('button', { type: 'button', class: 'btn', onclick: () => showPlan() }, 'Suggest plan');
  function drawHeader() {
    setChildren(headHost, ui.pageHeader({
      title: 'Inventory supply',
      purpose: 'What runs out, when each purchase order (PO) must land, and what it costs, at Revamp Utah, Amazon FBA (Fulfilled by Amazon) and the international third-party logistics warehouse (3PL).',
      actions: [newPoBtn, planBtn],
      tldr: tldrParts(),
    }));
  }

  // ---------------------------------------------------------------- data checks
  const openState = { vars: false, lead: false, checks: false };                              // collapsed sections stay as the user left them across re-renders
  const checksHost = h('section', { class: 'pg-card', 'aria-label': 'Data checks' });
  function drawChecks() {
    const chips = sum.freshness.map((f) => {
      const day = f.age_days === 0 ? 'today' : `${f.age_days} day${f.age_days === 1 ? '' : 's'} old`;
      if (f.status === 'missing') return ui.chip(`${f.label}: nothing loaded`, 'bad');
      const tone = f.status === 'STALE' ? (f.cadence === 'daily' ? 'bad' : 'warn') : 'good';
      const text = f.feed === 'purchase_orders' ? `${f.label} last edited ${fmt.date(f.data_through)}` : `${f.label}: ${day}`;
      return ui.chip(f.status === 'STALE' ? `${text}, stale` : text, tone, { estimate: f.feed === 'stock_intl' && /estimate/.test(f.label) });
    });
    if (sum.demand_stub) chips.push(ui.chip('Demand forecast not available', 'bad'));
    const warns = sum.warnings.filter((w) => w.level === 'warn');
    const infos = sum.warnings.filter((w) => w.level !== 'warn');
    const item = (w) => h('li', { class: 'is-check' }, ui.chip(w.level === 'warn' ? 'CHECK' : 'NOTE', w.level === 'warn' ? 'warn' : 'info'),
      h('div', null, h('b', w.title), h('p', w.detail)));
    setChildren(checksHost,
      h('div', { class: 'is-card-head' }, h('h2', 'Data checks'), h('p', { class: 'sub' }, warns.length ? `${warns.length} to look at, ${infos.length} notes` : 'Nothing to fix')),
      h('div', { class: 'is-chips' }, chips),
      sum.warnings.length ? h('details', { class: 'is-more', open: openState.checks ? '' : null, ontoggle: (e) => { openState.checks = e.target.open; } }, h('summary', warns.length ? `${warns.length} check${warns.length === 1 ? '' : 's'} to look at` : 'What the checks found'),
        h('ul', { class: 'is-checks' }, [...warns, ...infos].map(item))) : null);
  }

  // ---------------------------------------------------------------- what to order
  const orderHost = h('section', { class: 'pg-card', 'aria-label': 'What to order' });
  function drawOrders() {
    const rows = sum.reorder;
    const head = h('thead', null, h('tr', null, ['Product', 'Where', 'Status', 'Order by', 'Runs out', 'Must land by', 'Days to order', 'Suggested order', ''].map((t, i) => h('th', { class: i < 3 ? 'l' : '' }, t))));
    const body = rows.map((r) => {
      const d = r.days_until_order;
      const tone = r.status === 'OVERDUE' ? 'bad' : r.status === 'ORDER SOON' ? 'warn' : 'good';
      const dd = d == null ? null : fmt.display(Math.floor(d), 'num', { digits: 0 });
      return h('tr', null,
        h('td', { class: 'l' }, PNAME(r.product)), h('td', { class: 'l' }, LN[r.location]),
        h('td', { class: 'l' }, ui.chip(r.no_stockout ? 'OK' : r.status, tone, { solid: r.status === 'OVERDUE' })),
        h('td', null, r.no_stockout ? '' : star(r.order_by)),
        h('td', null, r.no_stockout ? `Not in ${sum.horizon} months` : star(r.est_stockout)),
        h('td', null, r.no_stockout ? '' : star(r.must_land_by)),
        h('td', { class: dd && dd.neg ? 'neg' : '' }, dd ? `${dd.text}*` : ''),
        h('td', null, r.no_stockout ? '' : `${num0(r.suggested_po_units)} units`),
        h('td', null, r.no_stockout ? null : h('button', { type: 'button', class: 'btn small', onclick: () => prefillOrder(r) }, 'Add order')));
    });
    setChildren(orderHost,
      h('div', { class: 'is-card-head' }, h('h2', 'What to order'), h('p', { class: 'sub' }, 'Most urgent first')),
      rows.length ? h('div', { class: 'is-scroll' }, h('table', { class: 'is-tbl' }, head, h('tbody', null, body))) : h('div', { class: 'is-empty' }, 'No stock counts or demand yet. Enter a stock count to see when each product runs out.'),
      ui.source('Order by = stock-out date minus safety stock minus lead time. Projected dates are marked *.'));
  }

  // ---------------------------------------------------------------- burn
  let loc = 'revamp';
  let burnGrid = null;
  let burnChart = null;
  const burnHost = h('section', { class: 'pg-card', 'aria-label': 'Stock by month' });
  const burnGridHost = h('div');
  const burnCanvas = h('canvas', { 'aria-label': 'Closing stock by month' });
  const seg = h('div', { class: 'is-seg', role: 'group', 'aria-label': 'Location' });
  const LOC_TABS = [['revamp', 'Revamp Utah'], ['amazon', 'Amazon FBA'], ['intl', 'International 3PL'], ['total', 'All locations']];
  async function drawBurn() {
    for (const b of seg.children) b.setAttribute('aria-pressed', String(b.dataset.loc === loc));
    ui.loading(burnGridHost, 'Loading stock by month');
    let p;
    try { p = await get('burn', { location: loc }); } catch (e) { ui.error(burnGridHost, e, drawBurn); return; }
    if (!ctx.alive()) return;
    if (burnChart) { charts.destroy(burnChart); burnChart = null; }
    burnGridHost.replaceChildren();
    if (burnGrid) { burnGrid.destroy(); burnGrid = null; }
    const closing = p.rows.filter((r) => r.key.endsWith('.closing'));
    burnChart = charts.lineChart(burnCanvas, {
      months: p.months, status: p.status, yFmt: 'num',
      series: closing.map((r) => { const pk = r.key.split('.')[1]; return { label: PNAME(pk), values: r.values, fmt: 'num', color: charts.PALETTE[PRODUCT_SLOT[pk] ?? 5] }; }),
    });
    own.push(burnChart);
    burnGrid = ctx.grid(burnGridHost, {
      payload: p, readOnly: true, title: p.location_label, groupCollapse: false,
      highlight: (c) => (c.key.endsWith('.closing') && c.value < 0 ? 'bad' : c.key.endsWith('.cover') && c.value != null && c.value >= 0 && c.value < 1 ? 'warn' : null),
    });
  }
  for (const [k, label] of LOC_TABS) seg.append(h('button', { type: 'button', 'data-loc': k, 'aria-pressed': 'false', onclick: () => { loc = k; drawBurn(); } }, label));
  burnHost.append(
    h('div', { class: 'is-card-head' }, h('h2', 'Stock by month'), seg),
    h('div', { class: 'pg-chartbox' }, burnCanvas),
    burnGridHost);

  // ---------------------------------------------------------------- purchase orders (edited in place)
  const poHost = h('section', { class: 'pg-card', 'aria-label': 'Purchase orders', id: 'is-po' });
  const poStatusLine = h('p', { class: 'is-status', role: 'status' });
  const poFilter = { status: 'open', product: 'all' };
  let vendorNames = () => vend.vendors.map((v) => v.vendor);
  const say = (host, msg, err = false) => { host.textContent = msg; host.className = 'is-status' + (err ? ' err' : ''); };

  async function afterWrite() {
    events.emit('saved', { scenario: ctx.scenario, version: ctx.scenarioVersion });
    await loadAll();
    if (!ctx.alive()) return;
    drawHeader(); drawChecks(); drawOrders(); drawPOs(); drawTransfers(); drawStock(); drawMoney(); drawLead(); drawVars(); drawHistory();
    await drawBurn();
  }

  function editor(opts) {
    // opts: {value, kind: text|num|date|pct|select, options, placeholder, label, wide, onSave(value) -> Promise, host}
    const { kind, label, placeholder = '', host } = opts;
    let input;
    if (kind === 'select') {
      input = h('select', { class: `is-in ${opts.cls || ''}`, 'aria-label': label }, opts.options.map(([v, t]) => h('option', { value: v }, t)));
      input.value = opts.value ?? '';
    } else {
      input = h('input', { type: kind === 'date' ? 'date' : 'text', inputmode: kind === 'num' || kind === 'pct' ? 'decimal' : null, class: `is-in${kind === 'date' ? ' date' : ''}${opts.wide ? ' wide' : ''} ghost ${opts.cls || ''}${opts.derived ? ' derived' : ''}`,
        'aria-label': label, placeholder, autocomplete: 'off', list: opts.list || null, title: opts.title || (opts.derived ? 'Land date minus the vendor lead time. Type a date to override it.' : null) });
      input.value = opts.value ?? '';
    }
    input.dataset.saved = input.value;
    const revert = () => { input.value = input.dataset.saved; input.classList.remove('bad'); };
    const commit = async () => {
      const text = input.value.trim();
      if (text === input.dataset.saved) { input.classList.remove('bad'); return; }
      let v = text;
      if (kind === 'num' || kind === 'pct') {
        if (text === '') v = null;
        else { const r = fmt.parse(text, kind === 'pct' ? 'pct' : 'num'); if (!r.ok) { input.classList.add('bad'); say(host, `${label}: ${r.error}`, true); return; } v = r.value; }
      } else if (text === '') v = kind === 'date' ? null : text;
      input.classList.remove('bad');
      say(host, 'Saving…');
      try {
        await opts.onSave(v);
        input.dataset.saved = input.value;
        say(host, 'Saved');
      } catch (e) {
        input.classList.add('bad');
        say(host, e.message, true);
        toast(e.message, 'error');
        revert();
      }
    };
    input.addEventListener(kind === 'select' ? 'change' : 'blur', commit);
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); input.blur(); }
      if (e.key === 'Escape') { e.preventDefault(); revert(); input.blur(); }
    });
    return input;
  }
  const pctText = (v) => (v == null ? '' : fmt.pct(v, 0));
  const opt = (obj) => Object.entries(obj);

  // ---------------------------------------------------------------- phone layout: one card per order or transfer, fields built on tap
  const mq = window.matchMedia('(max-width: 640px)');
  let narrow = mq.matches;
  const onMq = () => { if (mq.matches !== narrow) { narrow = mq.matches; drawPOs(); drawTransfers(); } };
  mq.addEventListener('change', onMq);
  const openCards = new Set();                                                  // cards the user opened stay open across re-renders
  const fld = (label, el, wide = false) => h('div', { class: 'is-poc-f' + (wide ? ' is-poc-wide' : '') }, h('label', null, label), el);
  function card({ key, cls = '', top, sub, chips = [], build, open = false }) {
    const det = h('details', { class: `is-poc ${cls}`, 'data-id': key.split(':')[1], open: open || openCards.has(key) ? '' : null });
    let built = false;
    const fill = () => { if (!built) { built = true; det.append(build()); } };
    det.append(h('summary', null, h('div', { class: 'top' }, ...top), h('div', { class: 'sub2' }, sub), chips.filter(Boolean).length ? h('div', { class: 'is-chips' }, chips.filter(Boolean)) : null));
    det.addEventListener('toggle', () => { if (det.open) { openCards.add(key); fill(); } else openCards.delete(key); });
    if (det.open) fill();
    return det;
  }

  function drawPOs() {
    const all = pos.pos;
    const shown = all.filter((r) => (poFilter.status === 'all' || (poFilter.status === 'open' ? !['landed', 'cancelled'].includes(r.status) : r.status === poFilter.status))
      && (poFilter.product === 'all' || r.product === poFilter.product));
    const save = (id, field) => async (v) => { await ctx.api.patch(`inventory-supply/po/${id}${q}`, { [field]: v }); await afterWrite(); };
    // the editors of one order, shared by the table row and the phone card
    const E = (r) => {
      const ed = (field, kind, extra = {}) => editor({ kind, host: poStatusLine, label: `${PNAME(r.product)} ${field.replace('_', ' ')}`,
        value: kind === 'num' ? (r[field] == null ? '' : String(r[field])) : (r[field] ?? ''), onSave: save(r.ref, field), ...extra });
      return {
        po_number: ed('po_number', 'text', { cls: 'w-po' }), product: ed('product', 'select', { options: PRODUCTS.map((p) => [p, PN[p]]), cls: 'w-prod' }),
        vendor: ed('vendor', 'text', { list: 'is-vendors', cls: 'w-vendor' }), warehouse: ed('warehouse', 'select', { options: opt(WH), cls: 'w-wh' }),
        units: editor({ kind: 'num', host: poStatusLine, label: 'Units', value: num0(r.units), cls: 'w-num', onSave: save(r.ref, 'units') }),
        order_date: editor({ kind: 'date', host: poStatusLine, label: 'Order date', value: r.order_date || '', derived: !!r.order_date_derived, onSave: save(r.ref, 'order_date') }),
        land_date: editor({ kind: 'date', host: poStatusLine, label: 'Land date', value: r.land_date, onSave: save(r.ref, 'land_date') }),
        status: ed('status', 'select', { options: PO_STATUS.map((s) => [s, s[0].toUpperCase() + s.slice(1)]), cls: 'w-status' }),
        unit_cost: editor({ kind: 'num', host: poStatusLine, label: 'Unit cost', cls: 'w-num', value: r.unit_cost_override == null ? '' : String(r.unit_cost_override), placeholder: r.unit_cost == null ? '' : fmt.money(r.unit_cost, { digits: 2 }), onSave: save(r.ref, 'unit_cost') }),
        deposit: editor({ kind: 'pct', host: poStatusLine, label: 'Deposit', cls: 'w-pct', value: r.deposit_pct_override == null ? '' : pctText(r.deposit_pct_override), placeholder: pctText(r.deposit_pct), onSave: save(r.ref, 'deposit_pct') }),
      };
    };
    const flagChips = (r) => [r.scenario_only ? ui.chip('Only here', 'info') : (r.overridden.length ? h('span', { title: `Changed in this scenario: ${r.overridden.join(', ')}` }, ui.chip('Changed here', 'info')) : null),
      ...r.flags.map((f) => h('span', { title: (sum.warnings.find((w) => w.code === f) || {}).detail || f }, ui.chip(FLAG_WORDS[f] || f, 'warn')))];
    const rowClass = (r) => (r.status === 'landed' || r.status === 'cancelled' ? 'is-dim ' : '') + (r.flags.length ? 'is-flag ' : '') + (r.overridden.length || r.scenario_only ? 'is-ovr' : '');
    const money = (v) => (v == null ? '' : fmt.money(v));
    const cols = ['PO number', 'Product', 'Vendor', 'Ship to', 'Units', 'Order date', 'Land date', 'Status', 'Unit cost', 'PO value', 'Deposit', 'Down payment', 'Balance', 'Checks', ''];
    const tableRow = (r) => {
      const e = E(r);
      return h('tr', { class: rowClass(r), 'data-id': r.ref },
        h('td', { class: 'l' }, e.po_number), h('td', { class: 'l' }, e.product), h('td', { class: 'l' }, e.vendor), h('td', { class: 'l' }, e.warehouse), h('td', null, e.units),
        h('td', null, e.order_date), h('td', null, e.land_date), h('td', { class: 'l' }, e.status), h('td', null, e.unit_cost), h('td', null, money(r.po_value)), h('td', null, e.deposit),
        h('td', null, money(r.down_payment)), h('td', null, money(r.balance)), h('td', { class: 'l' }, flagChips(r)),
        h('td', null, h('button', { type: 'button', class: 'btn small', onclick: () => deletePO(r) }, 'Delete')));
    };
    const poCard = (r) => card({
      key: `po:${r.ref}`, cls: rowClass(r),
      top: [h('span', null, `${PNAME(r.product)}, ${num0(r.units)} units`), ui.chip(r.status.toUpperCase(), STATUS_TONE[r.status] || 'neutral')],
      sub: `Lands ${fmt.date(r.land_date)} at ${WH[r.warehouse]}${r.po_value == null ? '' : `, ${fmt.money(r.po_value)}`}`, chips: flagChips(r),
      build: () => { const e = E(r); return h('div', { class: 'is-poc-body' }, fld('PO number', e.po_number), fld('Product', e.product), fld('Vendor', e.vendor), fld('Ship to', e.warehouse), fld('Units', e.units), fld('Status', e.status),
        fld('Order date', e.order_date), fld('Land date', e.land_date), fld('Unit cost', e.unit_cost), fld('Deposit', e.deposit),
        fld('Down payment', h('div', { class: 'ro' }, money(r.down_payment))), fld('Balance', h('div', { class: 'ro' }, money(r.balance))),
        fld('', h('button', { type: 'button', class: 'btn', onclick: () => deletePO(r) }, 'Delete order'), true)); },
    });
    // new order
    const nf = { product: 'bull', vendor: '', warehouse: 'revamp', units: '', order_date: '', land_date: '', status: 'planned' };
    const inp = (field, kind, extra = {}) => {
      const x = kind === 'select'
        ? h('select', { class: `is-in w-${field === 'product' ? 'prod' : field === 'warehouse' ? 'wh' : 'status'}`, 'aria-label': `New order ${field}`, onchange: (ev) => { nf[field] = ev.target.value; } }, extra.options.map(([v, t]) => h('option', { value: v }, t)))
        : h('input', { type: kind === 'date' ? 'date' : 'text', class: `is-in${kind === 'date' ? ' date' : ''} ghost`, 'aria-label': `New order ${field}`, placeholder: extra.placeholder || '', list: extra.list || null, autocomplete: 'off',
          oninput: (ev) => { nf[field] = ev.target.value; } });
      if (kind === 'select') x.value = nf[field];
      x.dataset.field = field;
      return x;
    };
    const submit = async (ev) => {
      const r = fmt.parse(nf.units || '', 'num');
      if (!r.ok) { say(poStatusLine, 'Units: enter a number', true); return; }
      ev.target.disabled = true;
      try {
        await ctx.api.post('inventory-supply/po' + q, { product: nf.product, vendor: nf.vendor.trim(), warehouse: nf.warehouse, units: r.value, order_date: nf.order_date || null, land_date: nf.land_date, status: nf.status });
        toast('Purchase order added', 'ok');
        await afterWrite();
      } catch (e) { say(poStatusLine, e.message, true); toast(e.message, 'error'); ev.target.disabled = false; }
    };
    const addFields = () => ({ product: inp('product', 'select', { options: PRODUCTS.map((p) => [p, PN[p]]) }), vendor: inp('vendor', 'text', { list: 'is-vendors', placeholder: 'Vendor' }),
      warehouse: inp('warehouse', 'select', { options: opt(WH) }), units: inp('units', 'text', { placeholder: 'Units' }), order_date: inp('order_date', 'date'), land_date: inp('land_date', 'date'),
      status: inp('status', 'select', { options: PO_STATUS.map((s) => [s, s[0].toUpperCase() + s.slice(1)]) }) });
    const addRow = () => { const f = addFields(); return h('tr', { class: 'is-add', id: 'is-po-add' }, h('td'), h('td', { class: 'l' }, f.product), h('td', { class: 'l' }, f.vendor), h('td', { class: 'l' }, f.warehouse), h('td', null, f.units),
      h('td', null, f.order_date), h('td', null, f.land_date), h('td', { class: 'l' }, f.status), h('td', { colspan: 6 }, h('span', { class: 'sub' }, 'Cost and deposit come from the vendor price list unless typed.')),
      h('td'), h('td', null, h('button', { type: 'button', class: 'btn primary small', onclick: submit }, 'Add'))); };
    const addCard = () => { const f = addFields(); const det = h('details', { class: 'is-poc is-add', id: 'is-po-add' }, h('summary', null, h('div', { class: 'top' }, h('span', null, 'Add purchase order'))),
      h('div', { class: 'is-poc-body' }, fld('Product', f.product), fld('Ship to', f.warehouse), fld('Vendor', f.vendor), fld('Units', f.units), fld('Order date', f.order_date), fld('Land date', f.land_date), fld('Status', f.status),
        fld('', h('button', { type: 'button', class: 'btn primary', onclick: submit }, 'Add order'), true))); return det; };
    const tv = shown.reduce((a, r) => a + (r.po_value || 0), 0), tu = shown.reduce((a, r) => a + r.units, 0);
    const statusSel = h('select', { 'aria-label': 'Order status filter', onchange: (ev) => { poFilter.status = ev.target.value; drawPOs(); } },
      [['open', 'Open orders'], ['all', 'All orders'], ...PO_STATUS.filter((x) => x !== 'planned' && x !== 'ordered' && x !== 'in transit').map((x) => [x, x[0].toUpperCase() + x.slice(1)])].map(([v, t]) => h('option', { value: v }, t)));
    statusSel.value = poFilter.status;
    const prodSel = h('select', { 'aria-label': 'Product filter', onchange: (ev) => { poFilter.product = ev.target.value; drawPOs(); } },
      [['all', 'All products'], ...PRODUCTS.map((p) => [p, PN[p]])].map(([v, t]) => h('option', { value: v }, t)));
    prodSel.value = poFilter.product;
    let content;
    if (narrow) {
      content = h('div', { class: 'is-cards' }, addCard(), shown.map(poCard), h('p', { class: 'is-cardsum' }, `${shown.length} orders shown, ${num0(tu)} units, ${fmt.money(tv)}`));
    } else {
      const head = h('thead', null, h('tr', null, cols.map((t, i) => h('th', { class: i < 4 || i === 7 || i === 13 ? 'l' : '' }, t))));
      const foot = h('tfoot', null, h('tr', null, h('td', { colspan: 4, class: 'l' }, `${shown.length} orders shown`), h('td', null, num0(tu)), h('td', { colspan: 4 }), h('td', null, fmt.money(tv)), h('td'),
        h('td', null, fmt.money(shown.reduce((a, r) => a + (r.down_payment || 0), 0))), h('td', null, fmt.money(shown.reduce((a, r) => a + (r.balance || 0), 0))), h('td'), h('td')));
      content = h('div', { class: 'is-scroll tall' }, h('table', { class: 'is-tbl' }, head, h('tbody', null, addRow(), shown.map(tableRow)), foot));
    }
    setChildren(poHost,
      h('div', { class: 'is-card-head' }, h('h2', 'Purchase orders'), h('div', { class: 'is-tools' }, statusSel, prodSel)),
      isPlan ? null : h('p', { class: 'is-note' }, `Edits here change only ${ctx.scenarioInfo.name}. The plan of record keeps its own orders.`),
      h('datalist', { id: 'is-vendors' }, vendorNames().map((v) => h('option', { value: v }))),
      content, poStatusLine,
      ui.source(`Typed by hand. Last edited ${sum.freshness[0].data_through ? fmt.date(sum.freshness[0].data_through) : 'never'}.`));
  }
  async function deletePO(r) {
    await confirmDelete(`Delete this ${PNAME(r.product)} order?`, `${num0(r.units)} units from ${r.vendor}, landing ${fmt.date(r.land_date)}. Cancel it instead to keep the record; a delete is saved in the change history.`,
      async () => { await ctx.api.del(`inventory-supply/po/${r.ref}${q}`); await afterWrite(); });
  }
  async function confirmDelete(title, message, onConfirm) {
    await confirmBox({ title, message, confirmText: 'Delete', onConfirm });
  }
  function prefillOrder(r) {
    const row = document.getElementById('is-po-add');
    if (!row) return;
    if (row.tagName === 'DETAILS') row.open = true;
    poFilter.status = 'open';
    const set = (f, v) => { const x = row.querySelector(`[data-field="${f}"]`); if (x) { x.value = v; x.dispatchEvent(new Event(x.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true })); } };
    const wh = r.location === 'intl' ? 'intl' : 'revamp';
    const roundTo = (vs.levers.find((x) => x.key === 'po_round_units') || { value: 1000 }).value;
    const v = vend.vendors.find((x) => x.default_warehouse === wh);
    set('product', r.product); set('warehouse', wh); set('vendor', v ? v.vendor : ''); set('units', String(Math.ceil(r.suggested_po_units / roundTo) * roundTo)); set('land_date', r.must_land_by);
    document.getElementById('is-po').scrollIntoView({ behavior: 'smooth', block: 'start' });
    row.querySelector('[data-field="units"]')?.focus();
  }

  // ---------------------------------------------------------------- FBA transfers
  const trHost = h('section', { class: 'pg-card', 'aria-label': 'FBA transfers' });
  const trStatusLine = h('p', { class: 'is-status', role: 'status' });
  function drawTransfers() {
    const rows = trs.transfers.filter((t) => !['received', 'cancelled'].includes(t.status));
    const save = (id, field) => async (v) => { await ctx.api.patch(`inventory-supply/transfers/${id}${q}`, { [field]: v }); await afterWrite(); };
    const E = (t) => ({
      product: editor({ kind: 'select', host: trStatusLine, label: 'Product', cls: 'w-prod', options: PRODUCTS.map((p) => [p, PN[p]]), value: t.product, onSave: save(t.ref, 'product') }),
      units: editor({ kind: 'num', host: trStatusLine, label: 'Units', cls: 'w-num', value: num0(t.units), onSave: save(t.ref, 'units') }),
      ship: editor({ kind: 'date', host: trStatusLine, label: 'Ship date', value: t.ship_date, onSave: save(t.ref, 'ship_date') }),
      receive: editor({ kind: 'date', host: trStatusLine, label: 'Receive date', value: t.receive_date, onSave: save(t.ref, 'receive_date') }),
      status: editor({ kind: 'select', host: trStatusLine, label: 'Status', cls: 'w-status', options: TR_STATUS.map((s) => [s, s[0].toUpperCase() + s.slice(1)]), value: t.status, onSave: save(t.ref, 'status') }),
    });
    const chips = (t) => [t.scenario_only ? ui.chip('Only here', 'info') : (t.overridden.length ? ui.chip('Changed here', 'info') : null), ...t.flags.map((f) => ui.chip(FLAG_WORDS[f] || f, 'warn'))];
    const cls = (t) => (t.flags.length ? 'is-flag ' : '') + (t.overridden.length || t.scenario_only ? 'is-ovr' : '');
    const del = (t) => confirmDelete('Delete this transfer?', `${num0(t.units)} ${PNAME(t.product)} shipping ${fmt.date(t.ship_date)}. A delete is saved in the change history.`,
      async () => { await ctx.api.del(`inventory-supply/transfers/${t.ref}${q}`); await afterWrite(); });
    const tableRow = (t) => { const e = E(t); return h('tr', { class: cls(t) }, h('td', { class: 'l' }, t.transfer_no || ''), h('td', { class: 'l' }, e.product), h('td', null, e.units), h('td', null, e.ship), h('td', null, e.receive),
      h('td', { class: t.in_transit_days < 0 ? 'neg' : '' }, String(t.in_transit_days)), h('td', { class: 'l' }, e.status), h('td', { class: 'l' }, t.note || ''), h('td', { class: 'l' }, chips(t)),
      h('td', null, h('button', { type: 'button', class: 'btn small', onclick: () => del(t) }, 'Delete'))); };
    const trCard = (t) => card({
      key: `tr:${t.ref}`, cls: cls(t), top: [h('span', null, `${PNAME(t.product)}, ${num0(t.units)} units`), ui.chip(t.status.toUpperCase(), STATUS_TONE[t.status] || 'neutral')],
      sub: `Ships ${fmt.date(t.ship_date)}, arrives ${fmt.date(t.receive_date)}`, chips: chips(t),
      build: () => { const e = E(t); return h('div', { class: 'is-poc-body' }, fld('Product', e.product), fld('Units', e.units), fld('Ship date', e.ship), fld('Receive date', e.receive), fld('Status', e.status),
        fld('Days in transit', h('div', { class: 'ro' }, String(t.in_transit_days))), t.note ? fld('Note', h('div', { class: 'ro' }, t.note), true) : null,
        fld('', h('button', { type: 'button', class: 'btn', onclick: () => del(t) }, 'Delete transfer'), true)); },
    });
    const nt = { product: 'bull', units: '', ship_date: '', receive_date: '', status: 'planned' };
    const ninp = (field, kind, extra = {}) => {
      const x = kind === 'select' ? h('select', { class: `is-in w-${field === 'product' ? 'prod' : 'status'}`, 'aria-label': `New transfer ${field}`, onchange: (e) => { nt[field] = e.target.value; } }, extra.options.map(([v, t]) => h('option', { value: v }, t)))
        : h('input', { type: kind === 'date' ? 'date' : 'text', class: `is-in${kind === 'date' ? ' date' : ''} ghost`, 'aria-label': `New transfer ${field}`, placeholder: extra.placeholder || '', autocomplete: 'off', oninput: (e) => { nt[field] = e.target.value; } });
      if (kind === 'select') x.value = nt[field];
      return x;
    };
    const submit = async (ev) => {
      const r = fmt.parse(nt.units || '', 'num');
      if (!r.ok) { say(trStatusLine, 'Units: enter a number', true); return; }
      ev.target.disabled = true;
      try { await ctx.api.post('inventory-supply/transfers' + q, { product: nt.product, units: r.value, ship_date: nt.ship_date, receive_date: nt.receive_date, status: nt.status }); toast('Transfer added', 'ok'); await afterWrite(); }
      catch (e) { say(trStatusLine, e.message, true); toast(e.message, 'error'); ev.target.disabled = false; }
    };
    const af = () => ({ product: ninp('product', 'select', { options: PRODUCTS.map((p) => [p, PN[p]]) }), units: ninp('units', 'text', { placeholder: 'Units' }), ship: ninp('ship_date', 'date'), receive: ninp('receive_date', 'date'),
      status: ninp('status', 'select', { options: TR_STATUS.map((s) => [s, s[0].toUpperCase() + s.slice(1)]) }) });
    let content;
    if (narrow) {
      const f = af();
      content = h('div', { class: 'is-cards' }, h('details', { class: 'is-poc is-add' }, h('summary', null, h('div', { class: 'top' }, h('span', null, 'Add transfer'))),
        h('div', { class: 'is-poc-body' }, fld('Product', f.product), fld('Units', f.units), fld('Ship date', f.ship), fld('Receive date', f.receive), fld('Status', f.status), fld('', h('button', { type: 'button', class: 'btn primary', onclick: submit }, 'Add transfer'), true))),
        rows.map(trCard), h('p', { class: 'is-cardsum' }, `${rows.length} open transfers, ${num0(rows.reduce((a, t) => a + t.units, 0))} units`));
    } else {
      const f = af();
      const head = h('thead', null, h('tr', null, ['Transfer', 'Product', 'Units', 'Ships', 'Arrives', 'Days', 'Status', 'Note', 'Checks', ''].map((t, i) => h('th', { class: i === 0 || i === 1 || i >= 6 ? 'l' : '' }, t))));
      const add = h('tr', { class: 'is-add' }, h('td'), h('td', { class: 'l' }, f.product), h('td', null, f.units), h('td', null, f.ship), h('td', null, f.receive), h('td'), h('td', { class: 'l' }, f.status), h('td'), h('td'),
        h('td', null, h('button', { type: 'button', class: 'btn primary small', onclick: submit }, 'Add')));
      content = h('div', { class: 'is-scroll tall' }, h('table', { class: 'is-tbl' }, head, h('tbody', null, add, rows.map(tableRow))));
    }
    setChildren(trHost,
      h('div', { class: 'is-card-head' }, h('h2', 'Transfers to Amazon FBA'), h('p', { class: 'sub' }, 'Units leave Revamp in the ship month and reach FBA stock in the receive month.')),
      content, trStatusLine, ui.source('Typed by hand. Shipped and received transfers drop out of the open list.'));
  }

  // ---------------------------------------------------------------- stock counts
  const stockHost = h('section', { class: 'pg-card', 'aria-label': 'Stock counts' });
  const stockStatus = h('p', { class: 'is-status', role: 'status' });
  function drawStock() {
    const today = sum.today;
    const LORD = { revamp: 0, amazon: 1, intl: 2 };
    const rows = [...stock.latest].sort((a, b) => (LORD[a.location] - LORD[b.location]) || (PRODUCTS.indexOf(a.product) - PRODUCTS.indexOf(b.product)));
    const save = (r, patch) => async (v) => {
      await ctx.api.post('inventory-supply/stock', { location: r.location, product: r.product, as_of: today, on_hand: r.on_hand, allocated: r.allocated, inbound_per_3pl: r.inbound_per_3pl, estimate: false, note: 'Entered on the page', ...patch(v) });
      await afterWrite();
    };
    const head = h('thead', null, h('tr', null, ['Where', 'Product', 'Counted', 'Age', 'On hand', 'Held for orders', 'Starting stock', ''].map((t, i) => h('th', { class: i < 2 ? 'l' : '' }, t))));
    const body = rows.map((r) => {
      const age = Math.round((new Date(today) - new Date(r.as_of)) / 86400000);
      const tone = age > sum.stale_stock_days ? 'warn' : 'good';
      return h('tr', null, h('td', { class: 'l' }, LN[r.location]), h('td', { class: 'l' }, PNAME(r.product)), h('td', null, fmt.date(r.as_of)),
        h('td', { class: 'l' }, ui.chip(age === 0 ? 'TODAY' : `${age} DAYS OLD`, tone), r.estimate ? ui.chip('ESTIMATE', 'info', { estimate: true }) : null),
        h('td', null, editor({ kind: 'num', host: stockStatus, label: `${LN[r.location]} ${PNAME(r.product)} on hand`, value: num0(r.on_hand), cls: 'w-num', onSave: save(r, (v) => ({ on_hand: v ?? 0 })) })),
        h('td', null, r.location === 'amazon' ? '' : editor({ kind: 'num', host: stockStatus, label: 'Held for orders', value: num0(r.allocated), cls: 'w-num', onSave: save(r, (v) => ({ allocated: v ?? 0 })) })),
        h('td', null, num0(r.opening)),
        h('td'));
    });
    setChildren(stockHost,
      h('div', { class: 'is-card-head' }, h('h2', 'Stock counts'), h('p', { class: 'sub' }, `A change is saved as today's count (${fmt.date(today)}); earlier counts stay on record.`)),
      rows.length ? h('div', { class: 'is-scroll' }, h('table', { class: 'is-tbl' }, head, h('tbody', null, body))) : h('div', { class: 'is-empty' }, 'No stock counts yet. Load the sheet snapshot or post a count.'),
      stockStatus,
      ui.source('Revamp is read from Warehance every day; Amazon FBA and the international 3PL are counted by hand. Counts are shared by every scenario.'));
  }

  // ---------------------------------------------------------------- cash and value
  const moneyHost = h('section', { class: 'is-cols two', 'aria-label': 'Cash and value' });
  let moneyGrid = null;
  let valueGrid = null;
  const outCanvas = h('canvas', { 'aria-label': 'Purchase order cash by month' });
  const valCanvas = h('canvas', { 'aria-label': 'Inventory value by month' });
  const outGridHost = h('div');
  const valGridHost = h('div');
  function drawMoney() {
    for (const c of own.splice(0)) if (c !== burnChart) charts.destroy(c);
    const start = outlay.status.indexOf('projected');
    const from = Math.max(0, start - 1);
    const slice = (arr) => arr.slice(from, from + 24);
    const r = (k) => outlay.rows.find((x) => x.key === k);
    own.push(charts.barChart(outCanvas, { months: slice(outlay.months), status: slice(outlay.status), stacked: true, yFmt: 'usd0',
      series: [{ label: 'Deposits', values: slice(r('deposits').values), color: charts.PALETTE[3] }, { label: 'Balances', values: slice(r('balances').values), color: charts.PALETTE[5] }] }));
    const tot = value.rows.find((x) => x.key === 'all.total');
    own.push(charts.lineChart(valCanvas, { months: value.months, status: value.status, yFmt: 'usd0',
      series: [{ label: 'Inventory value', values: tot.values }] }));
    if (moneyGrid) moneyGrid.destroy();
    if (valueGrid) valueGrid.destroy();
    moneyGrid = ctx.grid(outGridHost, { payload: outlay, readOnly: true, title: 'Cash for purchase orders', groupCollapse: false });
    valueGrid = ctx.grid(valGridHost, { payload: value, readOnly: true, title: 'Inventory value', groupCollapse: true });
  }
  moneyHost.append(
    h('div', { class: 'pg-card' }, h('div', { class: 'is-card-head' }, h('h2', 'Cash for purchase orders'), h('p', { class: 'sub' }, 'Next 24 months')), h('div', { class: 'pg-chartbox' }, outCanvas),
      h('details', { class: 'is-more' }, h('summary', 'Month by month'), outGridHost), ui.source('Purchase orders: deposit in the order month, balance in the landing month. Projected months are marked *.')),
    h('div', { class: 'pg-card' }, h('div', { class: 'is-card-head' }, h('h2', 'Inventory value'), h('p', { class: 'sub' }, 'Closing stock at landed cost')), h('div', { class: 'pg-chartbox' }, valCanvas),
      h('details', { class: 'is-more' }, h('summary', 'By warehouse and product'), valGridHost), ui.source('Closing stock times China landed unit cost per product.')));

  // ---------------------------------------------------------------- lead times and prices
  const leadHost = h('section', { class: 'pg-card', 'aria-label': 'Lead times and prices' });
  const leadStatus = h('p', { class: 'is-status', role: 'status' });
  // a changed variable gets an edge and a tooltip with the value it replaced
  const mark = (overridden, field, dflt) => ((overridden || []).includes(field) ? { cls: 'ovr', title: `Changed in this scenario. Plan of record: ${dflt == null ? 'none' : fmt.num(dflt, 2)}` } : {});
  function drawLead() {
    const put = (path, body) => async () => { const sep = path.includes('?') ? '&' : '?'; await ctx.api.put('inventory-supply/' + path + (q ? `${sep}${q.slice(1)}` : ''), body); await afterWrite(); };
    const vendTbl = h('table', { class: 'is-tbl' }, h('thead', null, h('tr', null, ['Vendor', 'Lead time (weeks)', 'Minimum order', 'Supplies'].map((t, i) => h('th', { class: i === 0 || i === 3 ? 'l' : '' }, t)))),
      h('tbody', null, vend.vendors.map((v) => h('tr', null, h('td', { class: 'l' }, v.vendor),
        h('td', null, editor({ kind: 'num', host: leadStatus, label: `${v.vendor} lead time`, value: String(v.lead_weeks), ...mark(v.overridden, 'lead_weeks', v.default && v.default.lead_weeks), onSave: (x) => put(`vendors/${encodeURIComponent(v.vendor)}`, { lead_weeks: x })() })),
        h('td', null, editor({ kind: 'num', host: leadStatus, label: `${v.vendor} minimum order`, value: String(v.moq_units), ...mark(v.overridden, 'moq_units', v.default && v.default.moq_units), onSave: (x) => put(`vendors/${encodeURIComponent(v.vendor)}`, { lead_weeks: v.lead_weeks, moq_units: x ?? 0 })() })),
        h('td', { class: 'l' }, WH[v.default_warehouse] || v.default_warehouse)))));
    const priceTbl = h('table', { class: 'is-tbl' }, h('thead', null, h('tr', null, ['Vendor', 'Product', 'Unit cost', 'Deposit'].map((t, i) => h('th', { class: i < 2 ? 'l' : '' }, t)))),
      h('tbody', null, vend.vendor_costs.map((c) => h('tr', null, h('td', { class: 'l' }, c.vendor), h('td', { class: 'l' }, PNAME(c.product)),
        h('td', null, editor({ kind: 'num', host: leadStatus, label: `${c.vendor} ${PNAME(c.product)} unit cost`, value: String(c.unit_cost), ...mark(c.overridden, 'unit_cost', c.default && c.default.unit_cost), onSave: (x) => put('vendor-costs', { vendor: c.vendor, product: c.product, unit_cost: x ?? 0, deposit_pct: c.deposit_pct })() })),
        h('td', null, editor({ kind: 'pct', host: leadStatus, label: `${c.vendor} ${PNAME(c.product)} deposit`, value: pctText(c.deposit_pct), ...mark(c.overridden, 'deposit_pct', c.default && c.default.deposit_pct), onSave: (x) => put('vendor-costs', { vendor: c.vendor, product: c.product, unit_cost: c.unit_cost, deposit_pct: x })() }))))));
    const rpTbl = h('table', { class: 'is-tbl' }, h('thead', null, h('tr', null, ['Where', 'Product', 'Lead time (days)', 'Safety stock (days)'].map((t, i) => h('th', { class: i < 2 ? 'l' : '' }, t)))),
      h('tbody', null, vend.reorder_params.map((p) => h('tr', null, h('td', { class: 'l' }, LN[p.location]), h('td', { class: 'l' }, PNAME(p.product)),
        h('td', null, editor({ kind: 'num', host: leadStatus, label: `${LN[p.location]} ${PNAME(p.product)} lead time`, value: String(p.lead_days), ...mark(p.overridden, 'lead_days', p.default && p.default.lead_days), onSave: (x) => put('reorder-params', { location: p.location, product: p.product, lead_days: x ?? 0, safety_days: p.safety_days })() })),
        h('td', null, editor({ kind: 'num', host: leadStatus, label: `${LN[p.location]} ${PNAME(p.product)} safety stock`, value: String(p.safety_days), ...mark(p.overridden, 'safety_days', p.default && p.default.safety_days), onSave: (x) => put('reorder-params', { location: p.location, product: p.product, lead_days: p.lead_days, safety_days: x ?? 0 })() }))))));
    const costTbl = h('table', { class: 'is-tbl' }, h('thead', null, h('tr', null, ['Product', 'SKU', 'Landed unit cost', 'Source'].map((t, i) => h('th', { class: i < 2 || i === 3 ? 'l' : '' }, t)))),
      h('tbody', null, vend.product_costs.map((c) => h('tr', null, h('td', { class: 'l' }, PNAME(c.product)), h('td', { class: 'l' }, c.sku || ''),
        h('td', null, editor({ kind: 'num', host: leadStatus, label: `${PNAME(c.product)} landed unit cost`, value: String(c.china_unit_cost), ...mark(c.overridden, 'china_unit_cost', c.default && c.default.china_unit_cost), onSave: (x) => put('product-costs', { product: c.product, china_unit_cost: c.sku ? x : (x ?? 0) })() })),
        h('td', { class: 'l', title: c.source === 'P&L cost model' ? 'Set in the P&L cost model: editing it here or there changes both' : 'Stored value. Editing it writes the P&L cost model for this scenario' }, c.source || 'table')))));
    setChildren(leadHost,
      h('details', { class: 'is-more', open: openState.lead ? '' : null, ontoggle: (e) => { openState.lead = e.target.open; } }, h('summary', 'Lead times, prices and safety stock'),
        h('div', { class: 'is-small-tables' }, h('div', null, h('h3', 'Vendors'), h('div', { class: 'is-scroll' }, vendTbl)), h('div', null, h('h3', 'Price list'), h('div', { class: 'is-scroll' }, priceTbl)),
          h('div', null, h('h3', 'Reorder timing'), h('div', { class: 'is-scroll' }, rpTbl)), h('div', null, h('h3', 'Stock valuation'), h('div', { class: 'is-scroll' }, costTbl))),
        leadStatus, ui.source('Order date = land date minus the vendor lead time. Reorder timing: days from placing an order to landing at that warehouse, and days of demand to keep on hand.')));
  }

  // ---------------------------------------------------------------- history of orders, transfers, counts and prices (plan of record)
  const historyHost = h('section', { class: 'pg-card', 'aria-label': 'History of orders and stock' });
  const histStatus = h('p', { class: 'is-status', role: 'status' });
  const doHistory = async (path, body, okMsg) => {
    try { const r = await ctx.api.post('inventory-supply/' + path, body); toast(okMsg, 'ok'); await afterWrite(); return r; } catch (e) { say(histStatus, e.message, true); toast(e.message, 'error'); return null; }
  };
  function drawHistory() {
    if (!isPlan) {
      setChildren(historyHost, h('div', { class: 'is-card-head' }, h('h2', 'History of orders and stock')),
        h('p', { class: 'is-note' }, `Changes to ${ctx.scenarioInfo.name} are listed in History at the top of the page. Orders, transfers and counts of the plan of record have their own history there.`));
      return;
    }
    const list = hist.changes;
    const when = (iso) => fmt.dateTime(iso);
    const rows = list.map((c) => h('tr', { class: c.reverted || c.is_revert ? 'is-dim' : '' },
      h('td', { class: 'l' }, when(c.at)), h('td', { class: 'l' }, c.actor || ''), h('td', { class: 'l' }, c.summary),
      h('td', { class: 'l' }, c.is_revert ? ui.chip('REVERT', 'info') : (c.reverted ? ui.chip('REVERTED', 'neutral') : null)),
      h('td', null, c.revertible ? h('button', { type: 'button', class: 'btn small', onclick: async () => {
        const ok = await confirmBox({ title: 'Revert this change?', danger: false, confirmText: 'Revert', message: `${c.summary}. The previous values come back as a new version; nothing is deleted from History.`,
          onConfirm: async () => { await ctx.api.post('inventory-supply/history/revert', { version: c.version }); } });
        if (ok) { toast('Change reverted', 'ok'); await afterWrite(); }
      } }, 'Revert') : null),
      h('td', null, !c.is_revert ? h('button', { type: 'button', class: 'btn small', onclick: async () => {
        const later = list.filter((x) => x.version > c.version && !x.reverted && !x.is_revert).length;
        if (!later) { say(histStatus, 'Nothing was changed after this version.', false); return; }
        const ok = await confirmBox({ title: 'Restore supply to this point?', danger: false, confirmText: 'Restore', message: `${later} later change${later === 1 ? '' : 's'} to orders, transfers, counts and prices ${later === 1 ? 'is' : 'are'} undone as one new version.`,
          onConfirm: async () => { await ctx.api.post('inventory-supply/history/restore', { to_version: c.version }); } });
        if (ok) { toast('Supply restored', 'ok'); await afterWrite(); }
      } }, 'Restore to here') : null)));
    const at = h('input', { type: 'datetime-local', class: 'is-in date', 'aria-label': 'Restore supply to this time' });
    const restoreAt = h('button', { type: 'button', class: 'btn small', onclick: async () => {
      if (!at.value) { say(histStatus, 'Pick a date and time.', true); return; }
      const iso = new Date(at.value).toISOString();
      const ok = await confirmBox({ title: 'Restore supply to this time?', danger: false, confirmText: 'Restore', message: `Orders, transfers, counts and prices go back to how they were at ${at.value.replace('T', ' ')}. Every change after it that is still in force is undone as one new version.`,
        onConfirm: async () => { await ctx.api.post('inventory-supply/history/restore', { at: iso }); } });
      if (ok) { toast('Supply restored', 'ok'); await afterWrite(); }
    } }, 'Restore');
    setChildren(historyHost,
      h('div', { class: 'is-card-head' }, h('h2', 'History of orders and stock'), h('div', { class: 'is-tools' }, h('label', null, 'Restore supply to', at), restoreAt)),
      list.length ? h('div', { class: 'is-scroll tall' }, h('table', { class: 'is-tbl' }, h('thead', null, h('tr', null, ['When', 'Who', 'What', '', '', ''].map((t, i) => h('th', { class: i < 4 ? 'l' : '' }, t)))), h('tbody', null, rows)))
        : h('div', { class: 'is-empty' }, 'No changes yet. Every edit to an order, transfer, count or price is listed here and can be reverted.'),
      histStatus,
      ui.source('Plan of record only. Each change is one version in History at the top of the page; a revert is a new version and nothing is deleted.'));
  }

  // ---------------------------------------------------------------- planning variables and scenario changes
  const varsHost = h('section', { class: 'pg-card', 'aria-label': 'Planning variables' });
  const varsStatus = h('p', { class: 'is-status', role: 'status' });
  const ORIGIN = { default: ['SHEET VALUE', 'neutral'], scenario: ['CHANGED', 'info'], inherited: ['FROM BASE', 'neutral'] };
  const putVars = async (items) => { await ctx.api.put('inventory-supply/variables', { scenario: ctx.scenario, items }); await afterWrite(); };
  const CHANGE_LABEL = {
    pos: (k) => `Purchase order ${k}`, transfers: (k) => `Transfer ${k}`, vendors: (k) => `Vendor ${k}`, prices: (k) => `Price ${k.replace('|', ' ')}`,
    reorder: (k) => `Reorder timing ${k.replace('|', ' ')}`, product_costs: (k) => `Unit cost ${PNAME(k)}`,
  };
  function changeItems(c, whole) {
    const [a, b] = c.key.split('|');
    const base = { pos: { kind: 'po', id: c.key }, transfers: { kind: 'transfer', id: c.key }, vendors: { kind: 'vendor', vendor: c.key }, prices: { kind: 'price', vendor: a, product: b },
      reorder: { kind: 'reorder', location: a, product: b }, product_costs: { kind: 'product_cost', product: c.key } }[c.table];
    return whole ? [{ ...base, field: null, value: null }] : c.fields.map((f) => ({ ...base, field: f, value: null }));
  }
  function drawVars() {
    const groups = [];
    for (const v of vs.levers) { let g = groups.find((x) => x.name === v.group); if (!g) { g = { name: v.group, items: [] }; groups.push(g); } g.items.push(v); }
    const dflt = (v) => (v.default == null ? (v.kind === 'date' ? 'today' : 'none') : fmt.num(v.default));
    const rows = groups.flatMap((g) => [h('tr', { class: 'is-grp' }, h('td', { colspan: 6 }, g.name)), ...g.items.map((v) => {
      const [word, tone] = ORIGIN[v.origin];
      return h('tr', null, h('td', { class: 'l' }, v.label),
        h('td', null, editor({ kind: v.kind === 'date' ? 'date' : 'num', host: varsStatus, label: v.label, value: v.value == null ? '' : String(v.value), placeholder: v.kind === 'date' ? '' : dflt(v), cls: `w-num${v.origin === 'scenario' ? ' ovr' : ''}`,
          title: `Sheet value: ${dflt(v)}. Leave empty to go back to it.`, onSave: (x) => putVars([{ kind: 'lever', name: v.key, value: x }]) })),
        h('td', { class: 'unit' }, v.unit), h('td', null, dflt(v)), h('td', { class: 'l' }, ui.chip(word, tone)), h('td', { class: 'src' }, v.source));
    })]);
    const changes = vs.changes.map((c) => h('li', null, `${CHANGE_LABEL[c.table](c.key)}: ${c.fields.map((f) => `${f.replace('_', ' ')} ${c.values[f] === true ? 'removed' : fmt.num(Number.isFinite(c.values[f]) ? c.values[f] : NaN, 2) || c.values[f]}`).join(', ')} `,
      h('button', { type: 'button', class: 'btn small', onclick: () => putVars(changeItems(c, c.key.startsWith('new:'))) }, 'Revert')));
    setChildren(varsHost,
      h('details', { class: 'is-more', open: openState.vars ? '' : null, ontoggle: (e) => { openState.vars = e.target.open; } }, h('summary', `Planning variables${vs.changes.length ? ` (${vs.changes.length} changed rows in this scenario)` : ''}`),
        isPlan ? null : h('p', { class: 'is-note' }, `Changes here apply to ${ctx.scenarioInfo.name} only.`),
        h('div', { class: 'is-scroll' }, h('table', { class: 'is-tbl' }, h('thead', null, h('tr', null, ['Variable', 'Value', 'Unit', 'Sheet value', 'Status', 'Source'].map((t, i) => h('th', { class: i === 0 || i >= 4 ? 'l' : '' }, t)))), h('tbody', null, rows))),
        varsStatus,
        vs.changes.length ? h('div', null, h('h3', { style: { margin: 'var(--lv-sp-3) 0 var(--lv-sp-2)', fontSize: 'var(--lv-fs-sm)' } }, 'Changed orders, transfers and prices in this scenario'), h('ul', { class: 'is-list' }, changes)) : null,
        vs.notes.length ? h('ul', { class: 'is-list' }, vs.notes.map((n) => h('li', n))) : null,
        ui.source('Every value starts at the sheet. An empty value goes back to it. Orders, transfers, prices and lead times are changed in their own tables.')));
  }

  // ---------------------------------------------------------------- plan
  const planHost = h('section', { 'aria-label': 'Suggested plan' });
  async function showPlan(replaceAuto = true) {
    planHost.replaceChildren(h('div', { class: 'pg-card' }, h('div', { class: 'loading', role: 'status' }, 'Working out a plan')));
    planHost.scrollIntoView({ behavior: 'smooth', block: 'start' });
    let plan;
    try { plan = await get('plan/suggest', { replace_auto: replaceAuto ? 1 : 0 }); } catch (e) { planHost.replaceChildren(h('div', { class: 'pg-card' }, h('div', { class: 'errorbox', role: 'alert' }, e.message))); return; }
    if (!ctx.alive()) return;
    const s = plan.summary;
    const stockouts = (o) => Object.entries(o).map(([k, ms]) => { const [l, p] = k.split('|'); return `${PNAME(p)} at ${LN[l]}: ${ms.slice(0, 3).map((m) => fmt.monthLabel(m)).join(', ')}${ms.length > 3 ? ` and ${ms.length - 3} more` : ''}`; });
    const after = stockouts(s.stockouts_after);
    const delta = s.outlay_total_after - s.outlay_total_before;
    const poTbl = plan.pos.length ? h('div', { class: 'is-scroll' }, h('table', { class: 'is-tbl' },
      h('thead', null, h('tr', null, ['Product', 'Ship to', 'Vendor', 'Units', 'Order date', 'Land date', 'PO value', 'Down payment'].map((t, i) => h('th', { class: i < 3 ? 'l' : '' }, t)))),
      h('tbody', null, plan.pos.map((p) => h('tr', null, h('td', { class: 'l' }, PNAME(p.product)), h('td', { class: 'l' }, WH[p.warehouse]), h('td', { class: 'l' }, p.vendor), h('td', null, num0(p.units)), h('td', null, fmt.date(p.order_date)),
        h('td', null, fmt.date(p.land_date)), h('td', null, fmt.money(p.po_value || 0)), h('td', null, fmt.money(p.down_payment || 0))))))) : h('div', { class: 'is-empty' }, 'No new purchase orders needed.');
    const trTbl = plan.transfers.length ? h('div', { class: 'is-scroll' }, h('table', { class: 'is-tbl' },
      h('thead', null, h('tr', null, ['Product', 'Units', 'Ships', 'Arrives'].map((t, i) => h('th', { class: i === 0 ? 'l' : '' }, t)))),
      h('tbody', null, plan.transfers.map((t) => h('tr', null, h('td', { class: 'l' }, PNAME(t.product)), h('td', null, num0(t.units)), h('td', null, fmt.date(t.ship_date)), h('td', null, fmt.date(t.receive_date))))))) : null;
    const replaceBox = h('input', { type: 'checkbox', 'aria-label': 'Replace the existing auto-planned rows', onchange: (e) => showPlan(e.target.checked) });
    replaceBox.checked = replaceAuto;
    const accept = h('button', { type: 'button', class: 'btn primary', disabled: plan.pos.length + plan.transfers.length === 0 ? '' : null, onclick: async () => {
      const ok = await confirmBox({
        title: 'Accept this plan?', danger: false, confirmText: 'Accept plan',
        message: `${plan.pos.length} purchase orders and ${plan.transfers.length} transfers are added${replaceAuto ? `; ${s.removed_pos} auto-planned orders and ${s.removed_transfers} transfers are replaced` : ''}. Your own rows are not touched. Every change is saved in the change history.`,
        onConfirm: async () => { await ctx.api.post('inventory-supply/plan/accept' + q, { pos: plan.pos.map(({ temp_id, po_value, down_payment, ...p }) => p), transfers: plan.transfers.map(({ temp_id, ...t }) => t), replace_auto: replaceAuto, expect_replaced: { pos: s.removed_pos, transfers: s.removed_transfers } }); },
      });
      if (ok) { toast('Plan accepted', 'ok'); planHost.replaceChildren(); await afterWrite(); }
    } }, 'Accept plan');
    planHost.replaceChildren(h('section', { class: 'pg-card' },
      h('div', { class: 'is-card-head' }, h('h2', 'Suggested plan'), h('p', { class: 'sub' }, 'Nothing is saved until you accept.')),
      h('div', { class: 'is-plan-cards' },
        h('div', null, h('b', `${s.added_pos} orders`), h('span', `${num0(s.added_units)} units, ${fmt.money(s.added_value)}`)),
        h('div', null, h('b', `${s.added_transfers} transfers`), h('span', 'to Amazon FBA')),
        h('div', null, h('b', replaceAuto ? `${s.removed_pos + s.removed_transfers} rows` : 'None'), h('span', replaceAuto ? `auto-planned rows replaced (${fmt.money(s.removed_value)} of orders)` : 'existing rows kept')),
        h('div', null, h('b', fmt.money(delta)), h('span', 'change in total order cash'))),
      h('div', { class: 'is-tools', style: { marginTop: 'var(--lv-sp-3)' } }, h('label', null, replaceBox, 'Replace the existing auto-planned rows')),
      h('h3', { style: { margin: 'var(--lv-sp-3) 0 0', fontSize: 'var(--lv-fs-sm)' } }, 'Months still out of stock after the plan'),
      after.length ? h('ul', { class: 'is-list' }, after.map((t) => h('li', t))) : h('p', { class: 'is-status' }, 'None.'),
      plan.shortfalls.length ? h('div', null, h('h3', { style: { margin: 'var(--lv-sp-3) 0 0', fontSize: 'var(--lv-fs-sm)' } }, 'What cannot be fixed in time'),
        h('ul', { class: 'is-list' }, plan.shortfalls.map((x) => h('li', `${PNAME(x.product)} at ${LN[x.location]}: ${x.reason}`)))) : null,
      h('h3', { style: { margin: 'var(--lv-sp-3) 0 var(--lv-sp-2)', fontSize: 'var(--lv-fs-sm)' } }, 'Proposed purchase orders'), poTbl,
      trTbl ? h('div', null, h('h3', { style: { margin: 'var(--lv-sp-3) 0 var(--lv-sp-2)', fontSize: 'var(--lv-fs-sm)' } }, 'Proposed transfers'), trTbl) : null,
      h('div', { class: 'is-actions' }, accept, h('button', { type: 'button', class: 'btn', onclick: () => planHost.replaceChildren() }, 'Discard')),
      ui.source(`Orders keep stock above the safety line, sized to ${plan.settings.plan_cover_months} months of demand, rounded up to ${fmt.num(plan.settings.po_round_units)} and not below the vendor minimum. Transfers keep Amazon FBA at ${plan.settings.fba_target_months} months of cover.`)));
  }

  // ---------------------------------------------------------------- assemble
  drawHeader(); drawChecks(); drawOrders(); drawPOs(); drawTransfers(); drawStock(); drawMoney(); drawLead(); drawVars(); drawHistory();
  root.replaceChildren(headHost, orderHost, checksHost, planHost, burnHost, poHost, trHost, stockHost, moneyHost, varsHost, leadHost, historyHost);
  await drawBurn();
  return () => {
    mq.removeEventListener('change', onMq);
    if (burnGrid) burnGrid.destroy();
    if (moneyGrid) moneyGrid.destroy();
    if (valueGrid) valueGrid.destroy();
  };
}
