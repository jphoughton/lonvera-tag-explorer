// Library demo page: /projection/_demo?demo=1. Sample data only; edits stay in the browser and nothing is sent to the API.
// It exercises Grid (inputs, calc/subtotal/total/ratio rows, groups, FY columns, status chips) and the chart helpers.
const START = 2025;
const N = 60;
const months = Array.from({ length: N }, (_, i) => `${START + Math.floor(i / 12)}-${String((i % 12) + 1).padStart(2, '0')}-01`);
const ACTUAL_THROUGH = 21;            // Oct 2026
const status = months.map((_, i) => (i <= ACTUAL_THROUGH ? 'actual' : 'projected'));

const seed = {
  spend: months.map((_, i) => (i <= ACTUAL_THROUGH ? Math.round(900000 - i * 14000 + (i % 3) * 22000) : 350000)),
  cpa: months.map((_, i) => (i <= ACTUAL_THROUGH ? 78 + (i % 5) * 4 : 95)),
  aov: months.map((_, i) => (i <= ACTUAL_THROUGH ? 66 + (i % 4) * 2 : 69)),
  amazon: months.map((_, i) => (i <= ACTUAL_THROUGH ? 150000 + i * 1200 : 175000)),
};

function compute(inp) {
  const orders = months.map((_, i) => (inp.cpa[i] ? inp.spend[i] / inp.cpa[i] : 0));
  const ncRev = orders.map((o, i) => o * inp.aov[i]);
  const repeat = months.map((_, i) => { let s = 0; for (let k = 1; k <= 12 && i - k >= 0; k++) s += ncRev[i - k] * 0.045 * Math.pow(0.93, k - 1); return s; });
  const dtc = ncRev.map((v, i) => v + repeat[i]);
  const total = dtc.map((v, i) => v + inp.amazon[i]);
  const cogs = total.map((v) => v * 0.27);
  const fees = total.map((v, i) => inp.amazon[i] * 0.31 + dtc[i] * 0.09);
  const opex = months.map(() => 215000);
  const noi = total.map((v, i) => v - cogs[i] - fees[i] - inp.spend[i] - opex[i]);
  const cover = months.map((_, i) => 3.4 - (i % 9) * 0.45);
  return { orders, ncRev, repeat, dtc, total, cogs, fees, opex, noi, cover };
}

function payload(inp) {
  const c = compute(inp);
  const row = (key, label, kind, fmt, values, extra = {}) => ({ key, label, kind, fmt, values, input: false, ...extra });
  return {
    months, status,
    rows: [
      row('h_rev', 'Revenue', 'header', 'text', []),
      row('spend', 'Ad spend', 'input', 'usd0', inp.spend, { input: true, note: 'Typed monthly plan' }),
      row('nc_cpa', 'New-customer CPA', 'input', 'usd', inp.cpa, { input: true, agg: 'avg' }),
      row('nc_aov', 'New-customer AOV', 'input', 'usd', inp.aov, { input: true, agg: 'avg' }),
      row('nc_orders', 'New-customer orders', 'calc', 'num', c.orders),
      row('nc_rev', 'New-customer revenue', 'calc', 'usd0', c.ncRev),
      row('repeat_rev', 'Repeat revenue', 'calc', 'usd0', c.repeat),
      row('dtc_rev', 'DTC revenue', 'subtotal', 'usd0', c.dtc),
      row('amazon_rev', 'Amazon revenue', 'input', 'usd0', inp.amazon, { input: true }),
      row('total_rev', 'Total revenue', 'total', 'usd0', c.total),
      row('h_cost', 'Costs', 'header', 'text', []),
      row('cogs', 'Cost of goods', 'calc', 'usd0', c.cogs),
      row('fees', 'Platform and fulfillment fees', 'calc', 'usd0', c.fees),
      row('ad_spend', 'Marketing', 'calc', 'usd0', inp.spend),
      row('opex', 'Overhead', 'calc', 'usd0', c.opex),
      row('noi', 'Net operating income', 'total', 'usd0', c.noi),
      row('noi_margin', 'NOI margin', 'ratio', 'pct', c.noi.map((v, i) => (c.total[i] ? v / c.total[i] : null))),
      row('roas', 'New-customer ROAS', 'ratio', 'x', c.ncRev.map((v, i) => (inp.spend[i] ? v / inp.spend[i] : null))),
      row('h_inv', 'Inventory', 'header', 'text', []),
      row('cover', 'Months of cover', 'calc', 'num', c.cover, { digits: 1 }),
      row('flag', 'Stock status', 'calc', 'text', c.cover.map((v) => (v < 0.5 ? 'OUT' : v < 1.5 ? 'ORDER SOON' : 'OK'))),
    ],
    captions: ['Sample data for the library demo. Edits stay in this browser tab.'],
  };
}

export async function render(el, ctx) {
  const { h, fmt, charts, ui } = ctx;
  const inputs = { spend: [...seed.spend], cpa: [...seed.cpa], aov: [...seed.aov], amazon: [...seed.amazon] };
  const keyOf = { spend: 'spend', nc_cpa: 'cpa', nc_aov: 'aov', amazon_rev: 'amazon' };
  const gridHost = h('div', { id: 'demo-grid' });
  const canvas = h('canvas', { id: 'demo-chart', 'aria-label': 'New-customer revenue and NOI by month' });
  const cmpCanvas = h('canvas', { id: 'demo-compare', 'aria-label': 'NOI by scenario' });
  el.replaceChildren(h('div', { class: 'pg-page', 'data-testid': 'demo' },
    ui.pageHeader({
      title: 'Library demo',
      purpose: 'Every shared component on one page, with sample data. Nothing here is saved.',
      tldr: { line: 'Spend falls to $350K a month in November, so NOI loses money for the rest of the plan.', kpis: [
        { label: 'FY27 NOI', value: '-$3.0M', tone: 'bad', note: 'vs $0.9M in FY26' },
        { label: 'Spend from Nov 2026*', value: '$350K', note: 'per month' },
        { label: 'New-customer ROAS', value: '0.73x*', tone: 'warn' },
        { label: 'Lowest cover', value: '0.3 mo', tone: 'bad', note: 'Vital Bull, Mar 2027*' },
      ] },
    }),
    h('section', { class: 'pg-section' }, h('h2', 'Monthly grid'), gridHost),
    h('section', { class: 'pg-section' }, h('h2', 'Charts'),
      h('div', { class: 'pg-card' }, h('div', { class: 'pg-chartbox' }, canvas)),
      h('div', { class: 'pg-card', style: { marginTop: '10px' } }, h('div', { class: 'pg-chartbox' }, cmpCanvas)))));

  const drawCharts = () => {
    charts.destroyAll();
    const p = payload(inputs);
    const by = (k) => p.rows.find((r) => r.key === k).values;
    charts.comboChart(canvas, {
      months, status, yFmt: 'usd0', y1Fmt: 'pct',
      series: [
        { label: 'New-customer revenue', values: by('nc_rev'), type: 'bar', color: charts.PALETTE[1] },
        { label: 'NOI', values: by('noi'), type: 'line', color: charts.PALETTE[2] },
        { label: 'NOI margin', values: by('noi_margin'), type: 'line', axis: 'y1', color: charts.PALETTE[4], fmt: 'pct' },
      ],
    });
    const hi = { ...inputs, spend: inputs.spend.map((v, i) => (i > ACTUAL_THROUGH ? v * 1.4 : v)) };
    const lo = { ...inputs, spend: inputs.spend.map((v, i) => (i > ACTUAL_THROUGH ? v * 0.6 : v)) };
    charts.scenarioCompare(cmpCanvas, {
      months, status, title: 'NOI by scenario',
      baseline: { name: 'Current plan', values: by('noi') },
      others: [{ name: 'Spend +40%', values: payload(hi).rows.find((r) => r.key === 'noi').values }, { name: 'Spend -40%', values: payload(lo).rows.find((r) => r.key === 'noi').values }],
    });
  };

  const grid = ctx.grid(gridHost, {
    payload: payload(inputs),
    title: 'Operating plan',
    fy: false,
    highlight: (cell) => (cell.key === 'noi' && typeof cell.value === 'number' && cell.value < 0 ? 'bad' : null),
    onEdit: async (edits) => {
      for (const e of edits) inputs[keyOf[e.line_key]][months.indexOf(e.month)] = e.value == null ? 0 : e.value;
      drawCharts();
      return payload(inputs);                                         // returned payloads update the grid in place
    },
  });
  drawCharts();
  void fmt;
  return () => { grid.destroy(); };
}
