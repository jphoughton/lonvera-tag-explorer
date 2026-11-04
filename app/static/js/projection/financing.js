// Cash & debt page: monthly cash from close, term loan, payback, spend x return grid, scale-payback payout schedule.
// Data: /api/projection/financing/{cash,debt,payback,matrix,payout,inputs}. Edits: term-loan principal -> PUT financing/debt;
// tax / starting cash / equity payback -> PUT /series (override or input); levers -> PUT financing/inputs. All audited and versioned by the core.
// Layout, colors and spacing come from brand.css / shell.css tokens only.

const GRID_FMT = { money: 'usd0', money2: 'usd', int: 'num', num2: 'num', pct: 'pct', pct1: 'pct', x: 'x', text: 'text', date: 'date' };
const GRID_KIND = { line: 'calc', memo: 'ratio' };
const OVERRIDE_KEYS = new Set(['financing.tax', 'financing.opening_cash', 'financing.noi_before_inventory']);
const PRINCIPAL = 'financing.principal_paid';

// server rows (money / line / memo) -> the shared grid's names; months before `from` (the seller's months) are dropped
function toGrid(p, from) {
  const i0 = Math.max(0, from ? p.months.indexOf(from) : 0);
  const cut = (a) => (a || []).slice(i0);
  return {
    ...p, months: cut(p.months), status: cut(p.status),
    rows: p.rows.map((r) => ({
      ...r, values: cut(r.values), fmt: GRID_FMT[r.fmt] || r.fmt,
      kind: r.input && r.kind !== 'header' ? 'input' : (GRID_KIND[r.kind] || r.kind),
    })),
  };
}

export async function render(el, ctx) {
  const { h, ui, fmt, charts } = ctx;
  const root = h('div', { class: 'pg-page' });
  el.replaceChildren(root);
  ui.loading(root);
  const cleanups = [];
  const S = { cash: null, debt: null, payback: null, matrix: null, payout: null, inputs: null };
  const view = { cashGrid: null, debtGrid: null, whatIf: {}, matrixOver: {}, payoutOver: {} };

  // status chip that wraps on narrow screens instead of widening the page
  const chip = (text, tone) => {
    const c = ui.chip(text, tone);
    Object.assign(c.style, { whiteSpace: 'normal', alignItems: 'flex-start', lineHeight: '1.4', paddingTop: '2px', paddingBottom: '2px', borderRadius: 'var(--lv-r-sm)', maxWidth: '100%' });
    return c;
  };
  const money = (v, o) => (v == null ? '—' : fmt.money(v, { parens: true, ...(o || {}) }));
  const money2 = (v) => money(v, { digits: 2 });
  const isProjected = (iso) => { const i = S.cash ? S.cash.months.indexOf(iso) : -1; return i >= 0 && S.cash.status[i] === 'projected'; };
  const monthName = (iso) => (iso ? fmt.monthLabel(iso) + (isProjected(iso) ? '*' : '') : '');
  const num = (v, d = 0) => (v == null ? '—' : fmt.num(v, d));

  // ------------------------------------------------------------------------------------------------------ data
  const q = (o) => Object.fromEntries(Object.entries(o).filter(([, v]) => v != null && v !== ''));
  async function loadAll() {
    const [cash, debt, payback, matrix, payout, inputs] = await Promise.all([
      ctx.api.get('financing/cash'), ctx.api.get('financing/debt'), ctx.api.get('financing/payback', q(view.whatIf)),
      ctx.api.get('financing/matrix', q({ ...view.whatIf, ...view.matrixOver })), ctx.api.get('financing/payout', q(view.payoutOver)),
      ctx.api.get('financing/inputs')]);
    Object.assign(S, { cash, debt, payback, matrix, payout, inputs });
  }

  async function saveInputs(patch, okMessage) {
    await ctx.api.put('financing/inputs', { scenario: ctx.scenario, params: patch });
    ctx.refreshHeadline();
    if (okMessage) ctx.toast(okMessage, 'ok');
    view.whatIf = {}; view.matrixOver = {}; view.payoutOver = {};
    await refresh();
  }

  async function saveTerms(terms) {
    await ctx.api.put('financing/debt', { scenario: ctx.scenario, terms });
    ctx.refreshHeadline();
    ctx.toast('Loan terms saved', 'ok');
    await refresh();
  }

  async function refresh() {
    await loadAll();
    if (!ctx.alive()) return;
    drawAll();
    return S.cash;
  }

  // ------------------------------------------------------------------------------------------------------ small builders
  function field(label, input, hint) {
    return h('label', { title: hint || null, style: { display: 'flex', flexDirection: 'column', gap: '4px', minWidth: '130px', flex: '1 1 130px' } },
      h('span', { class: 'muted', style: { fontSize: 'var(--lv-fs-xs)', fontWeight: 'var(--lv-fw-semi)' } }, label), input);
  }
  function numberInput(value, { step = 'any', min, max, id } = {}) {
    return h('input', { type: 'number', class: 'pg-input', step, min, max, id, value: value == null ? '' : String(value), inputmode: 'decimal' });
  }
  function selectInput(options, value) {
    const s = h('select', { class: 'pg-input' }, options.map(([v, label]) => h('option', { value: v }, label)));
    s.value = value;
    return s;
  }
  const rowFlex = (...kids) => h('div', { style: { display: 'flex', flexWrap: 'wrap', gap: 'var(--lv-sp-3)', alignItems: 'flex-end' } }, kids);
  const val = (inp) => (inp.value === '' ? null : Number(inp.value));
  function section(title, ...kids) {
    return h('section', { class: 'pg-section' }, h('h2', null, title), ...kids);
  }
  function sourceLine(text) { return ui.source ? ui.source(text) : h('p', { class: 'pg-source' }, text); }
  function guard(fn) {
    return async (...a) => { try { await fn(...a); } catch (e) { if (e && e.name !== 'AuthError') ctx.toast(e.message || String(e), 'error'); } };
  }

  // ------------------------------------------------------------------------------------------------------ header + TLDR
  const headerHost = h('div');
  function drawHeader() {
    const s = S.cash.summary;
    const low = s.lowest_ending_cash || {};
    const pb = S.payback.result;
    const ye = (s.debt_balance_year_end || []).filter((y) => !y.partial);
    const thisYear = new Date().getFullYear();
    const at = Math.max(0, ye.findIndex((y) => y.year >= thisYear));
    const pick = ye[at];
    const after = ye[at + 1];
    const monthsWithCash = S.cash.rows.find((r) => r.key === 'ending_cash').values.filter((v) => v != null).length;
    const neg = s.negative_months;
    const line = low.value == null ? 'No cash months to report.'
      : (neg ? `Cash bottoms at ${fmt.compact(low.value)} in ${monthName(low.month)}; ${neg} of ${monthsWithCash} months are negative.`
        : `Cash never falls below ${fmt.compact(low.value)}, in ${monthName(low.month)}.`);
    const horizon = pb ? pb.ages.length - 1 : 36;
    const monthsText = (n) => (n == null ? `Over ${horizon} months` : n + (n === 1 ? ' month' : ' months'));
    function paybackKpi() {
      if (!pb) return { label: 'Payback on a new customer', value: 'Needs inputs', note: S.payback.unavailable ? 'see payback panel' : 'Enter order value' };
      const lp = S.payback.loaded && S.payback.loaded.result;
      if (lp) return { label: 'Payback after all costs', value: monthsText(lp.months), note: `${pb.months == null ? 'Over ' + horizon : pb.months} on product margin`, tone: lp.months == null ? 'bad' : undefined };
      return { label: 'Payback on product margin', value: monthsText(pb.months), note: `Acquisition cost ${money2(pb.cac)}`, tone: pb.months == null ? 'bad' : undefined };
    }
    const kpis = [
      { label: 'Lowest ending cash', value: money(low.value), note: monthName(low.month), tone: low.value != null && low.value < 0 ? 'bad' : 'good' },
      { label: 'Months below zero', value: String(neg), note: neg ? `first ${monthName(s.first_negative_month)}` : 'none', tone: neg ? 'bad' : 'good' },
      paybackKpi(),
      pick ? { label: `Term loan, ${monthName(pick.month)}`, value: money(pick.balance), note: after ? `${monthName(after.month)} ${fmt.compact(after.balance)}` : '' } : null,
      { label: 'Monthly top-up to stay at zero', value: money(s.monthly_top_up || 0),
        note: s.close_funding > 0.5 ? `plus ${fmt.compact(s.close_funding)} at close` : (s.monthly_top_up > 0 ? 'a month' : 'cash stays positive'), tone: s.monthly_top_up > 0 || s.close_funding > 0.5 ? 'warn' : 'good' },
    ].filter(Boolean);
    headerHost.replaceChildren(ui.pageHeader({
      title: 'Cash & debt',
      purpose: 'Monthly cash from the Sep 2026 close, the term loan behind it, and what a new customer and a media buyer cost.',
      tldr: { line, kpis },
    }));
  }

  // ------------------------------------------------------------------------------------------------------ cash section
  const warnHost = h('div', { style: { display: 'flex', flexDirection: 'column', gap: '6px' } });
  const cashChart = h('canvas', { 'aria-label': 'Ending cash and net cash flow by month' });
  const cashChartHost = h('div', { class: 'pg-card' }, h('div', { class: 'pg-chartbox' }, cashChart));
  const cashGridHost = h('div');
  let cashChartObj = null;
  function drawWarnings() {
    const kids = S.cash.summary.warnings.filter((w) => !w.startsWith('Term loan')).map((w) => h('div', null, chip(w, w.startsWith('Ending cash') ? 'bad' : 'warn')));
    const d = S.cash.summary.low_driver;
    if (d) kids.push(h('p', { class: 'pg-purpose', style: { margin: '2px 0 0' } }, d.sentence));
    warnHost.replaceChildren(...kids);
  }
  function drawCashChart() {
    charts.destroy(cashChartObj);
    const p = toGrid(S.cash, S.cash.close_month);
    const rowOf = (k) => p.rows.find((r) => r.key === k).values;
    cashChartObj = charts.comboChart(cashChart, {
      months: p.months, status: p.status, yFmt: 'usd0',
      series: [{ label: 'Net cash flow', values: rowOf('net_cash_flow'), type: 'bar' }, { label: 'Ending cash', values: rowOf('ending_cash'), type: 'line' }],
    });
  }
  function highlightCash(cell) {
    if (cell.value == null) return null;
    if (cell.key === 'ending_cash') {
      if (cell.value < 0) return 'bad';
      const floor = S.cash.summary.floor || 0;
      return floor && cell.value < floor ? 'warn' : null;
    }
    const ov = S.cash.overrides[cell.key];
    return ov && ov.includes(cell.month) ? 'info' : null;
  }
  async function onCashEdit(edits) {
    const principal = edits.filter((e) => e.line_key === PRINCIPAL);
    const rest = edits.filter((e) => e.line_key !== PRINCIPAL);
    if (principal.length) await ctx.api.put('financing/debt', { scenario: ctx.scenario, payments: principal.map((e) => ({ month: e.month, principal_paid: e.value })) });
    if (rest.length) await ctx.api.saveSeries(rest.map((e) => ({ line_key: e.line_key, month: e.month, value: e.value, kind: OVERRIDE_KEYS.has(e.line_key) ? 'override' : 'input' })));
    ctx.refreshHeadline();
    await refresh();
    return null; // drawAll() has already refreshed the grid in place
  }
  function drawCash() {
    drawWarnings();
    drawCashChart();
    const payload = toGrid(S.cash, S.cash.close_month);
    if (view.cashGrid) view.cashGrid.update(payload);
    else {
      view.cashGrid = ctx.grid(cashGridHost, { payload, onEdit: onCashEdit, highlight: highlightCash, groupCollapse: false });
      cleanups.push(() => view.cashGrid && view.cashGrid.destroy());
    }
  }

  // ------------------------------------------------------------------------------------------------------ term loan section
  const termsHost = h('div', { class: 'pg-card' });
  const sellerHost = h('div', { class: 'pg-card' });
  const debtGridHost = h('div');
  const debtWarnHost = h('div', { style: { display: 'flex', flexDirection: 'column', gap: '6px' } });
  function drawTerms() {
    const t = S.debt.terms;
    const principal = numberInput(t.principal, { step: 1000, min: 0 });
    const rate = numberInput(+(t.rate * 100).toFixed(4), { step: 0.25, min: 0, max: 100 });
    const mode = selectInput([['typed', 'Typed principal per month'], ['fixed_payment', 'Fixed monthly payment']], t.mode);
    const fixed = numberInput(t.fixed_payment, { step: 1000, min: 0 });
    const save = h('button', { type: 'button', class: 'btn primary', onclick: guard(async () => {
      await saveTerms({ principal: val(principal), rate: val(rate) / 100, mode: mode.value, fixed_payment: val(fixed) });
    }) }, 'Save loan terms');
    termsHost.replaceChildren(
      h('h3', { class: 'pg-h2' }, 'Term loan'),
      rowFlex(field('Principal', principal), field('Annual rate (%)', rate), field('Payments', mode), field('Fixed monthly payment', fixed, 'Used only in fixed-payment mode'), save),
      h('p', { class: 'pg-source' }, 'Interest is the beginning balance × the annual rate ÷ 12. Type principal per month in the grid below; a month without a value pays interest only.'));
    drawSeller();
  }
  function drawSeller() {
    const sn = S.debt.seller_note;
    const t = sn.terms;
    const enabled = h('input', { type: 'checkbox', id: 'sn-enabled' });
    enabled.checked = !!sn.enabled;
    const principal = numberInput(t.principal, { step: 1000, min: 0 });
    const rate = numberInput(+(t.rate * 100).toFixed(4), { step: 0.25, min: 0, max: 100 });
    const first = h('input', { type: 'date', class: 'pg-input', value: t.first_payment });
    const n = numberInput(t.installments, { step: 1, min: 1, max: 20 });
    const floor = numberInput(t.ebitda_floor, { step: 10000, min: 0 });
    const save = h('button', { type: 'button', class: 'btn', onclick: guard(async () => {
      await saveInputs({ seller_note: { enabled: enabled.checked, principal: val(principal), rate: val(rate) / 100, first_payment: first.value, installments: val(n), ebitda_floor: val(floor) } }, 'Seller note saved');
    }) }, 'Save seller note');
    const rows = (sn.payments || []).map((p) => h('tr', null,
      h('td', null, monthName(p.month)), h('td', { style: { textAlign: 'right' } }, money(p.principal)), h('td', { style: { textAlign: 'right' } }, money(p.interest)),
      h('td', { style: { textAlign: 'right' } }, money(p.balance)),
      h('td', { style: { textAlign: 'right' } }, p.ttm_noi == null ? '—' : money(p.ttm_noi)),
      h('td', null, p.below_floor == null ? '' : chip(p.below_floor ? 'Below floor' : 'Above floor', p.below_floor ? 'warn' : 'good'))));
    sellerHost.replaceChildren(
      h('h3', { class: 'pg-h2' }, 'Seller note'),
      h('label', { style: { display: 'flex', gap: '8px', alignItems: 'center', marginBottom: 'var(--lv-sp-2)' } }, enabled, h('span', null, 'Include the seller note in cash')),
      rowFlex(field('Principal', principal), field('Annual rate (%)', rate), field('First installment', first), field('Annual installments', n),
        field('EBITDA floor (flag only)', floor, 'Compared with trailing-year net operating income; never changes the cash'), save),
      rows.length ? h('div', { style: { overflowX: 'auto', marginTop: 'var(--lv-sp-3)' } }, h('table', { class: 'pg-mini' },
        h('thead', null, h('tr', null, ['Installment', 'Principal', 'Interest', 'Balance after', 'Trailing-year net operating income', 'EBITDA floor'].map((c, i) => h('th', { style: { textAlign: i && i < 5 ? 'right' : 'left' } }, c)))),
        h('tbody', null, rows)))
        : h('p', { class: 'pg-source' }, 'EBITDA is earnings before interest, taxes, depreciation and amortization. The floor is a flag only. The seller note is off in the current plan, so it is not part of the cash above.'));
  }
  function drawDebt() {
    drawTerms();
    debtWarnHost.replaceChildren(...S.debt.warnings.map((w) => h('div', null, chip(w, 'warn'))));
    const payload = toGrid(S.debt, S.cash.close_month);
    if (view.debtGrid) view.debtGrid.update(payload);
    else {
      view.debtGrid = ctx.grid(debtGridHost, {
        payload, groupCollapse: false,
        onEdit: async (edits) => {
          await ctx.api.put('financing/debt', { scenario: ctx.scenario, payments: edits.map((e) => ({ month: e.month, principal_paid: e.value })) });
          ctx.refreshHeadline();
          await refresh();
          return null;
        },
      });
      cleanups.push(() => view.debtGrid && view.debtGrid.destroy());
    }
  }

  // ------------------------------------------------------------------------------------------------------ payback section
  const paybackHost = h('div', { class: 'pg-card' });
  const paybackCanvas = h('canvas', { 'aria-label': 'Cumulative contribution margin per customer against customer acquisition cost' });
  let paybackChartObj = null;
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  function drawPayback() {
    const { inputs, result: r } = S.payback;
    const lo = S.payback.loaded || {};
    const L = lo.result;
    const info = lo.info || {};
    const aov = numberInput(r ? +r.aov.toFixed(2) : inputs.aov, { step: 0.01, min: 0.01 });
    const cac = numberInput(r ? +r.cac.toFixed(2) : inputs.cac, { step: 0.01, min: 0 });
    const cost = numberInput(+(inputs.product_cost_pct * 100).toFixed(2), { step: 0.1, min: 0, max: 100 });
    const ful = numberInput(inputs.fulfillment, { step: 0.01, min: 0 });
    const loadedPct = numberInput(info.source === 'typed' && info.rate != null ? +(info.rate * 100).toFixed(2) : '', { step: 0.1, min: 0, max: 99 });
    loadedPct.placeholder = info.rate != null ? `${(info.rate * 100).toFixed(1)} (P&L rates)` : 'P&L rates';
    const curve = selectInput([['live', 'Live curve (recent cohorts)'], ['goal', 'Goal curve (snapshot)']], S.payback.curve);
    const apply = guard(async () => {
      view.whatIf = q({ aov: val(aov), cac: val(cac), product_cost_pct: val(cost) == null ? null : val(cost) / 100, fulfillment: val(ful), curve: curve.value,
        loaded_rate: val(loadedPct) == null ? null : val(loadedPct) / 100 });
      await refresh();
    });
    const save = h('button', { type: 'button', class: 'btn primary', onclick: guard(async () => {
      await saveInputs({ payback: { aov: val(aov), cac: val(cac), product_cost_pct: val(cost) / 100, fulfillment: val(ful), curve: curve.value,
        loaded_rate: val(loadedPct) == null ? null : val(loadedPct) / 100 } }, 'Payback inputs saved');
    }) }, 'Save to scenario');
    const ref = inputs.reference || {};
    const useRef = ref.aov && ref.cac ? h('button', { type: 'button', class: 'btn', onclick: () => { aov.value = ref.aov.toFixed(2); cac.value = ref.cac.toFixed(2); apply(); } },
      `Use ${monthName(ref.month)} figures`) : null;
    const controls = rowFlex(field('Average order value (AOV)', aov), field('Customer acquisition cost (CAC)', cac), field('Product cost (% of order value)', cost),
      field('Fulfillment per order', ful), field('All variable costs (% of order value)', loadedPct, 'Leave blank to read the P&L cost rates'), field('Repeat curve', curve),
      h('button', { type: 'button', class: 'btn', onclick: apply }, 'Recalculate'), save, useRef);
    const kids = [h('h3', { class: 'pg-h2' }, 'Payback on a new customer'), controls];
    if (!r) {
      kids.push(h('p', { class: 'muted' }, S.payback.unavailable || 'Enter the average order value and customer acquisition cost to see the payback.'));
      paybackHost.replaceChildren(...kids);
      return;
    }
    const horizon = r.ages.length - 1;
    const card = (title, res, missingText, small) => h('div', { style: { flex: '1 1 300px', minWidth: '0', border: '1px solid var(--lv-line)', borderRadius: 'var(--lv-r-sm)', padding: 'var(--lv-sp-3)', display: 'flex', flexDirection: 'column', gap: '6px' } },
      h('strong', null, title),
      res ? [
        chip(res.months == null ? `Does not pay back in ${horizon} months` : `Pays back in month ${res.months}`, res.months == null ? 'bad' : 'good'),
        chip(`First-order margin ${money2(res.cm_first)} (${fmt.pct(res.cm_pct, 1)} of order value)`, 'info'),
        ...(small ? [] : [chip(`Gap after the first order ${money2(res.gap)}`, res.gap > 0 ? 'warn' : 'good'), chip(`${horizon}-month surplus ${money2(res.surplus)}`, res.surplus >= 0 ? 'good' : 'bad')]),
      ] : h('p', { class: 'muted', style: { margin: 0 } }, missingText));
    const missingText = info.missing && info.missing.length ? `The P&L has no result for: ${info.missing.join(', ')}.` : 'The P&L cost rates are not available.';
    const LEAD = { scenario: 'At typed inputs', query: 'At the inputs entered above', 'last closed month': 'At last closed month' };
    const same = inputs.aov_source === inputs.cac_source;
    const lead = same ? (LEAD[inputs.aov_source] || `At ${inputs.aov_source} realized`) : 'At the inputs above';
    const srcLines = same ? [inputs.aov_source_label] : [`Average order value: ${inputs.aov_source_label}`, `Acquisition cost: ${inputs.cac_source_label}`];
    const plan = S.payback.plan || {};
    const pl = plan.inputs;
    kids.push(
      h('h4', { style: { margin: 'var(--lv-sp-3) 0 4px', fontSize: 'var(--lv-fs-md)' } }, `${lead} (average order value ${money2(r.aov)}, acquisition cost ${money2(r.cac)})`),
      h('p', { class: 'pg-source', style: { margin: '0 0 6px' } }, `Inputs: ${srcLines.filter(Boolean).join('; ')}.`),
      ...[['Average order value', inputs.aov_fallback_reason], ['Acquisition cost', inputs.cac_fallback_reason]].filter(([, why]) => why).map(([what, why]) =>
        h('div', { style: { marginBottom: '6px' } }, chip(`${what} is not read from the last ${(inputs.realized && inputs.realized.days) || 28} days: ${why}. It uses the last closed month.`, 'warn'))),
      h('div', { style: { display: 'flex', flexWrap: 'wrap', gap: 'var(--lv-sp-3)', marginBottom: 'var(--lv-sp-3)' } }, card('On product margin', r), card('After all variable costs', L, missingText)),
      ...(pl && plan.result ? [
        h('h4', { style: { margin: 'var(--lv-sp-3) 0 4px', fontSize: 'var(--lv-fs-md)' } }, `At plan inputs (average order value ${money2(pl.aov)}, cost per acquisition ${money2(pl.cac)}, ${monthName(pl.month)})`),
        h('div', { style: { display: 'flex', flexWrap: 'wrap', gap: 'var(--lv-sp-3)', marginBottom: 'var(--lv-sp-3)' } }, card('On product margin', plan.result, '', true), card('After all variable costs', plan.loaded && plan.loaded.result, missingText, true)),
      ] : []),
      h('div', { style: { display: 'flex', flexWrap: 'wrap', gap: '8px', marginBottom: 'var(--lv-sp-3)' } }, chip(`New-customer return on ad spend ${fmt.x(r.roas, 2)}`, 'info')));
    if (L && info.parts && info.parts.length) {
      const parts = info.parts.map((x) => h('tr', null, h('td', null, x.label), h('td', { style: { textAlign: 'right' } }, fmt.pct(x.rate, 1))));
      parts.push(h('tr', null, h('th', { style: { textAlign: 'left' } }, 'Variable cost, all lines'), h('th', { style: { textAlign: 'right' } }, fmt.pct(info.rate, 1))));
      kids.push(h('details', { style: { marginBottom: 'var(--lv-sp-3)' } }, h('summary', null, `What counts as variable cost: ${fmt.pct(info.rate, 1)} of order value`),
        h('div', { style: { overflowX: 'auto' } }, h('table', { class: 'pg-mini', style: { maxWidth: '420px' } }, h('tbody', null, parts))),
        h('p', { class: 'pg-source' }, info.source === 'typed' ? 'Typed rate.' : `The P&L's own cost lines as a share of DTC revenue, ${monthName(info.months[0])} to ${monthName(info.months[1])}.`)));
    }
    kids.push(h('div', { class: 'pg-chartbox' }, paybackCanvas));
    const rows = r.ages.map((a, i) => h('tr', null,
      h('td', null, String(a)), h('td', { style: { textAlign: 'right' } }, r.retention[i] == null ? '—' : fmt.pct(r.retention[i], 1)),
      h('td', { style: { textAlign: 'right' } }, money2(r.cumulative[i])), h('td', { style: { textAlign: 'right' } }, L ? money2(L.cumulative[i]) : '—'),
      h('td', null, (L ? L.paid[i] : r.paid[i]) ? chip('Paid back', 'good') : '')));
    kids.push(h('details', { style: { marginTop: 'var(--lv-sp-3)' } }, h('summary', null, 'Month by month'),
      h('div', { style: { overflow: 'auto', maxHeight: '320px' } }, h('table', { class: 'pg-mini' },
        h('thead', null, h('tr', null, ['Month after first order', 'Repeat revenue per $1 of first order', 'Cumulative margin, product', 'Cumulative margin, all variable costs', ''].map((c, i) => h('th', { style: { textAlign: i && i < 4 ? 'right' : 'left' } }, c)))),
        h('tbody', null, rows)))));
    kids.push(sourceLine('Margin per order = order value − variable costs; repeat margin = first-order margin × the curve for that month. Product margin counts product cost and fulfillment only.'));
    paybackHost.replaceChildren(...kids);
    charts.destroy(paybackChartObj);
    const ink = css('--lv-viz-ink') || css('--lv-ink');
    const accent = css('--lv-viz-1') || css('--lv-action');
    const second = css('--lv-viz-2') || css('--lv-info');
    const muted = css('--lv-viz-axis') || css('--lv-muted');
    const datasets = [{ label: 'Cumulative margin, product', data: r.cumulative, borderColor: accent, backgroundColor: accent, borderWidth: 2, pointRadius: 0, tension: 0.15 }];
    if (L) datasets.push({ label: 'Cumulative margin, all variable costs', data: L.cumulative, borderColor: second, backgroundColor: second, borderWidth: 2, pointRadius: 0, tension: 0.15 });
    datasets.push({ label: 'Customer acquisition cost', data: r.ages.map(() => r.cac), borderColor: ink, backgroundColor: ink, borderWidth: 1.5, borderDash: [6, 4], pointRadius: 0 });
    paybackChartObj = new Chart(paybackCanvas, {
      type: 'line',
      data: { labels: r.ages.map((a) => (a === 0 ? 'First order' : 'Month ' + a)), datasets },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false, interaction: { mode: 'index', intersect: false },
        scales: { x: { grid: { display: false }, ticks: { color: muted, maxTicksLimit: 10, maxRotation: 0 } }, y: { ticks: { color: muted, callback: (v) => fmt.money(v) }, beginAtZero: true } },
        plugins: { legend: { position: 'top', align: 'end', labels: { boxWidth: 10, boxHeight: 10, color: muted } },
          tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${fmt.money(c.parsed.y, { digits: 2 })}` } } },
      },
    });
    cleanups.push(() => charts.destroy(paybackChartObj));
  }

  // ------------------------------------------------------------------------------------------------------ spend x ROAS grid
  const matrixHost = h('div', { class: 'pg-card' });
  function heatCell(v, max) {
    const strength = Math.min(1, Math.abs(v) / (max || 1));
    const wash = h('i', { 'aria-hidden': 'true', style: { position: 'absolute', inset: '0', background: v >= 0 ? 'var(--lv-good)' : 'var(--lv-bad)', opacity: String(0.06 + 0.3 * strength) } });
    return h('td', { style: { position: 'relative', textAlign: 'right', padding: '4px 8px', fontVariantNumeric: 'tabular-nums', color: v < 0 ? 'var(--lv-neg)' : 'var(--lv-ink)' } },
      wash, h('span', { style: { position: 'relative' } }, money(v)));
  }
  function drawMatrix() {
    const r = S.matrix.result;
    if (!r) { matrixHost.replaceChildren(h('h3', { class: 'pg-h2' }, 'Profit by spend and return'), h('p', { class: 'muted' }, S.matrix.unavailable || 'Needs the order value and acquisition cost from the payback inputs.')); return; }
    const target = numberInput(r.target_month, { step: 1, min: 0, max: 60 });
    const basis = selectInput([['cm', 'Contribution margin only'], ['loaded', 'Fully loaded (less fixed cost)']], r.basis);
    const hasLoaded = !!(S.payback.loaded && S.payback.loaded.result);
    const margin = selectInput([['product', 'On product margin'], ...(hasLoaded ? [['loaded', 'After all variable costs']] : [])], hasLoaded ? r.margin : 'product');
    const fixed = numberInput(r.fixed_cost, { step: 1000, min: 0 });
    const apply = guard(async () => {
      view.matrixOver = q({ target_month: val(target), basis: basis.value, margin: margin.value, fixed_cost: val(fixed) });
      await refresh();
    });
    const save = h('button', { type: 'button', class: 'btn', onclick: guard(async () => {
      await saveInputs({ matrix: { target_month: val(target), basis: basis.value, margin: margin.value, fixed_cost: val(fixed) } }, 'Grid inputs saved');
    }) }, 'Save to scenario');
    const max = Math.max(...r.profit.flat().map((v) => Math.abs(v)));
    const cur = S.payback.result ? S.payback.result.roas : null;
    const head = h('tr', null, h('th', { style: { textAlign: 'left', padding: '4px 8px' } }, 'Monthly spend \\ return on ad spend (ROAS)'),
      r.roas.map((x) => h('th', { style: { textAlign: 'right', padding: '4px 8px' } }, fmt.x(x, 2))));
    const body = r.spends.map((s, i) => h('tr', null, h('th', { style: { textAlign: 'left', padding: '4px 8px', fontWeight: 'var(--lv-fw-semi)' } }, money(s)), r.profit[i].map((v) => heatCell(v, max))));
    const be = h('tr', null, h('th', { style: { textAlign: 'left', padding: '4px 8px' } }, r.basis === 'loaded' ? 'Break-even monthly spend' : 'Profitable at any spend'),
      r.breakeven_spend.map((v) => h('td', { style: { textAlign: 'right', padding: '4px 8px', fontVariantNumeric: 'tabular-nums' } }, v == null ? 'No' : v === 0 ? 'Yes' : money(v))));
    const verdict = cur == null || r.breakeven_roas == null ? null
      : chip(`Return on ad spend (ROAS) ${fmt.x(cur, 2)} vs break-even ${fmt.x(r.breakeven_roas, 2)} for payback by month ${r.target_month}`, cur >= r.breakeven_roas ? 'good' : 'bad');
    matrixHost.replaceChildren(
      h('h3', { class: 'pg-h2' }, `Profit at month ${r.target_month} by spend and return on ad spend`),
      rowFlex(field('Target payback month', target), field('Margin rate', margin), field('Basis', basis), field('Fixed cost per month', fixed, 'Fully loaded basis only'),
        h('button', { type: 'button', class: 'btn', onclick: apply }, 'Recalculate'), save),
      h('div', { style: { display: 'flex', flexWrap: 'wrap', gap: '8px', margin: 'var(--lv-sp-3) 0' } }, verdict,
        chip(`Margin rate ${fmt.pct(r.cm_rate, 1)} ${r.margin === 'loaded' ? 'after all variable costs' : 'on product margin'} · revenue multiple by month ${r.target_month}: ${fmt.x(r.cum_retention, 2)}`, 'info')),
      h('div', { style: { overflowX: 'auto' } }, h('table', { class: 'pg-mini' }, h('thead', null, head), h('tbody', null, body), h('tfoot', null, be))),
      sourceLine('Profit = spend × return on ad spend × margin rate × revenue multiple − spend; the multiple is 1 plus the repeat curve through the target month.'));
  }

  // ------------------------------------------------------------------------------------------------------ payout
  const payoutHost = h('div', { class: 'pg-card' });
  function drawPayout() {
    const r = S.payout.result;
    const inp = S.inputs.params.payout;
    if (!r) { payoutHost.replaceChildren(h('h3', { class: 'pg-h2' }, 'Scale-payback payout schedule'), h('p', { class: 'muted' }, S.payout.unavailable || 'The payout schedule is not available.')); return; }
    const spend = numberInput(r.media_spend, { step: 10000, min: 0 });
    const cpa = numberInput(r.cpa, { step: 1, min: 0.01 });
    const aov = numberInput(r.aov, { step: 1, min: 0.01 });
    const src = selectInput([['goal', 'Goal curve'], ['live', 'Live curve'], ['typed', 'Typed ratios']], S.payout.repeat_source);
    const apply = guard(async () => { view.payoutOver = q({ media_spend: val(spend), cpa: val(cpa), aov: val(aov), repeat_source: src.value }); await refresh(); });
    const save = h('button', { type: 'button', class: 'btn', onclick: guard(async () => {
      await saveInputs({ payout: { media_spend: val(spend), cpa: val(cpa), aov: val(aov), repeat_source: src.value } }, 'Payout inputs saved');
    }) }, 'Save to scenario');
    const sel = r.selected || {};
    const cols = r.columns;
    const cell = (v, on) => h('td', { style: { textAlign: 'right', padding: '4px 8px', fontVariantNumeric: 'tabular-nums', background: on ? 'var(--lv-info-bg)' : undefined, fontWeight: on ? 'var(--lv-fw-heavy)' : undefined } }, v == null ? '—' : money(v));
    const head = h('tr', null, h('th', { style: { textAlign: 'left', padding: '4px 8px' } }, 'Monthly media spend'),
      cols.map((c) => h('th', { style: { textAlign: 'right', padding: '4px 8px' } }, `Bonus, payback in ${c} months`)),
      cols.map((c) => h('th', { style: { textAlign: 'right', padding: '4px 8px' } }, `Total pay, ${c} months`)));
    const body = r.scales.map((s, i) => h('tr', null, h('th', { style: { textAlign: 'left', padding: '4px 8px', fontWeight: 'var(--lv-fw-semi)' } }, money(s)),
      cols.map((c) => cell(r.bonus[c][i], sel.column === c && sel.scale === s)), cols.map((c) => cell(r.total_comp[c][i], sel.column === c && sel.scale === s))));
    const verdict = sel.column == null ? chip(r.payback_month == null ? 'Payback is later than the schedule' : 'Payback is outside the schedule', 'warn')
      : chip(sel.bonus == null ? `Payback month ${r.payback_month}: no bonus at ${money(sel.scale)} for the ${sel.column}-month column` : `Payback month ${r.payback_month}: bonus ${money(sel.bonus)} at ${money(sel.scale)}`, sel.bonus == null ? 'warn' : 'good');
    const runRows = r.repeat_ratio.map((x, i) => h('tr', null, h('td', null, 'Month ' + (i + 1)), h('td', { style: { textAlign: 'right' } }, fmt.pct(x, 1)),
      h('td', { style: { textAlign: 'right' } }, money(r.repeat_rev[i])), h('td', { style: { textAlign: 'right' } }, money(r.repeat_cm[i])), h('td', { style: { textAlign: 'right' } }, money(r.running_cm[i]))));
    payoutHost.replaceChildren(
      h('h3', { class: 'pg-h2' }, 'Scale-payback payout schedule'),
      rowFlex(field('Monthly media spend', spend), field('Cost per new customer (CPA)', cpa), field('Average order value (AOV)', aov), field('Repeat ratios', src), h('button', { type: 'button', class: 'btn', onclick: apply }, 'Recalculate'), save),
      h('div', { style: { display: 'flex', flexWrap: 'wrap', gap: '8px', margin: 'var(--lv-sp-3) 0' } }, verdict,
        chip(`New-customer revenue ${money(r.nc_rev)}`, 'info'), chip(`Marketing expenses ${money(r.marketing_expenses)}`, 'info'),
        chip(`First-order margin after spend ${money(r.cm)}`, r.cm < 0 ? 'warn' : 'good')),
      h('p', { class: 'pg-source' }, `Marketing expenses = retainer ${money(inp.lazar)} + designer ${money(inp.designer)} + samples ${money(r.samples)} + creative production ${money(r.creative_production)} (${fmt.pct(inp.creative_production_rate, 0)} of media spend; vendor production cost, not pay).`),
      h('div', { style: { overflowX: 'auto' } }, h('table', { class: 'pg-mini' }, h('thead', null, head), h('tbody', null, body))),
      h('details', { style: { marginTop: 'var(--lv-sp-3)' } }, h('summary', null, 'Running margin by month'),
        h('div', { style: { overflowX: 'auto' } }, h('table', { class: 'pg-mini' },
          h('thead', null, h('tr', null, ['Month', 'Repeat per $1 of first order', 'Repeat revenue', 'Repeat margin', 'Running margin'].map((c, i) => h('th', { style: { textAlign: i ? 'right' : 'left' } }, c)))),
          h('tbody', null, runRows)))),
      sourceLine('Bonus steps up each spend tier from the first paying tier; total pay = bonus + the monthly retainer.'));
  }

  // ------------------------------------------------------------------------------------------------------ assemble
  function drawAll() {
    drawHeader();
    drawCash();
    drawDebt();
    drawPayback();
    drawMatrix();
    drawPayout();
  }

  try {
    await loadAll();
    if (!ctx.alive()) return () => {};
    if (!document.getElementById('pg-fin-css')) {
      const style = h('style', { id: 'pg-fin-css' },
        '.pg-mini{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums;font-size:var(--lv-fs-sm)}'
        + '.pg-mini th,.pg-mini td{padding:4px 8px;border-bottom:1px solid var(--lv-line-soft);white-space:nowrap}'
        + '.pg-mini thead th{color:var(--lv-muted);font-size:var(--lv-fs-xs);font-weight:var(--lv-fw-bold);position:sticky;top:0;background:var(--lv-surface)}'
        + '.pg-mini tfoot td,.pg-mini tfoot th{border-top:2px solid var(--lv-line);border-bottom:0}'
        + '@media (max-width:640px){.pg-body .pg-card .pg-input,.pg-body .pg-card .btn{min-height:44px}.pg-body .pg-card input[type=checkbox]{width:22px;height:22px}}');
      document.head.append(style);
    }
    root.replaceChildren(
      headerHost,
      section('Cash by month', warnHost, cashChartHost, cashGridHost),
      section('Debt', debtWarnHost, termsHost, debtGridHost, sellerHost),
      section('New-customer payback', paybackHost),
      section('Spend and return', matrixHost),
      section('Media-buyer payout', payoutHost));
    drawAll();
    document.body.dataset.pgState = 'ready';
  } catch (e) {
    ui.error(root, e, ctx.reload);
  }
  return () => { cleanups.forEach((f) => { try { f(); } catch { /* already gone */ } }); charts.destroy(cashChartObj); };
}
