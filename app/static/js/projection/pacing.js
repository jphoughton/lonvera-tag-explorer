// Pacing page: where the month is landing against the plan, the month snapshot, and day / week / month trends.
// Reads /api/projection/pacing/{month,snapshot,rollup,intraday}. Read-only: nothing on this page edits the plan.
// Styles are injected once and use only the --lv-* tokens from brand.css.

const CSS = `
.pacing-controls { display: flex; flex-wrap: wrap; align-items: center; gap: var(--lv-sp-2); }
.pacing-controls label { color: var(--lv-muted); font-size: var(--lv-fs-xs); font-weight: var(--lv-fw-bold); letter-spacing: var(--lv-track-kicker); text-transform: uppercase; }
.pacing-controls select, .pacing-controls input[type="number"], .pacing-controls input[type="time"] { font: inherit; font-size: var(--lv-fs-base); min-height: 32px; padding: 0 var(--lv-sp-2); border: 1px solid var(--lv-line-strong); border-radius: var(--lv-r-sm); background: var(--lv-surface); color: var(--lv-ink); }
.pacing-cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(196px, 1fr)); gap: var(--lv-sp-3); }
.pacing-card { display: flex; flex-direction: column; gap: 6px; background: var(--lv-surface); border: 1px solid var(--lv-line); border-radius: var(--lv-r-md); padding: var(--lv-sp-3) var(--lv-sp-4); }
.pacing-card .t { color: var(--lv-muted); font-size: var(--lv-fs-xs); font-weight: var(--lv-fw-bold); letter-spacing: var(--lv-track-kicker); text-transform: uppercase; }
.pacing-card .v { font-size: var(--lv-fs-xl); font-weight: var(--lv-fw-heavy); letter-spacing: -0.02em; font-variant-numeric: tabular-nums; }
.pacing-ctx { color: var(--lv-muted); font-size: var(--lv-fs-xs); font-weight: var(--lv-fw-medium); }
.pacing-card .g { color: var(--lv-muted); font-size: var(--lv-fs-sm); font-variant-numeric: tabular-nums; }
.pacing-card .row { display: flex; align-items: center; justify-content: space-between; gap: var(--lv-sp-2); }
.pacing-bar { position: relative; height: 8px; border-radius: var(--lv-r-pill); background: var(--lv-surface-3); overflow: visible; min-width: 70px; flex: 1 1 70px; }
.pacing-bar .fill { position: absolute; left: 0; top: 0; bottom: 0; border-radius: var(--lv-r-pill); background: var(--lv-neutral); }
.pacing-bar.good .fill { background: var(--lv-good-solid); }
.pacing-bar.warn .fill { background: var(--lv-warn-solid); }
.pacing-bar.bad .fill { background: var(--lv-bad-solid); }
.pacing-bar .tick { position: absolute; top: -3px; bottom: -3px; width: 2px; background: var(--lv-ink); opacity: 0.55; }
.pacing-note { display: flex; align-items: center; gap: var(--lv-sp-2); color: var(--lv-ink-2); font-size: var(--lv-fs-sm); }
.pacing-table-wrap { overflow: auto; border: 1px solid var(--lv-line); border-radius: var(--lv-r-md); background: var(--lv-surface); }
table.pacing-table { border-collapse: separate; border-spacing: 0; width: 100%; min-width: 860px; font-size: var(--lv-fs-base); font-variant-numeric: tabular-nums; }
.pacing-table th, .pacing-table td { padding: 0 var(--lv-sp-3); height: var(--lv-row-h, 26px); border-bottom: 1px solid var(--lv-line-soft); white-space: nowrap; text-align: right; background: var(--lv-surface); }
.pacing-table thead th { position: sticky; top: 0; z-index: 3; background: var(--lv-surface-2); font-weight: var(--lv-fw-semi); font-size: var(--lv-fs-sm); border-bottom: 1px solid var(--lv-line); height: 32px; }
.pacing-table thead th.proj { background: var(--lv-proj-head); color: var(--lv-proj-ink); }
.pacing-table th.lab, .pacing-table td.lab { position: sticky; left: 0; z-index: 2; text-align: left; font-weight: inherit; min-width: 220px; border-right: 1px solid var(--lv-line); }
.pacing-table thead th.lab { z-index: 4; }
.pacing-table tr.grp td { background: var(--lv-band-bg); font-size: var(--lv-fs-2xs); font-weight: var(--lv-fw-bold); letter-spacing: var(--lv-track-kicker); text-transform: uppercase; text-align: left; border-top: 1px solid var(--lv-line); }
.pacing-table tr.grp td.lab { z-index: 2; }
.pacing-table tr.subtotal td, .pacing-table tr.subtotal th.lab { font-weight: var(--lv-fw-semi); background: var(--lv-subtotal-bg); border-top: 1px solid var(--lv-line-strong); }
.pacing-table tr.total td, .pacing-table tr.total th.lab { font-weight: var(--lv-fw-heavy); background: var(--lv-total-bg); border-top: 2px solid var(--lv-total-rule); }
.pacing-table tr.ratio td, .pacing-table tr.ratio th.lab, .pacing-table tr.memo td, .pacing-table tr.memo th.lab { color: var(--lv-muted); }
.pacing-table td.neg { color: var(--lv-neg); }
.pacing-table td.zero { color: var(--lv-muted); }
.pacing-table td.pace { text-align: left; min-width: 250px; }
.pacing-table td.pace .p { display: flex; align-items: center; gap: var(--lv-sp-2); }
.pacing-table td.pace .pct { min-width: 46px; text-align: right; font-weight: var(--lv-fw-semi); }
.pacing-table td.proj { background: var(--lv-proj-cell); }
.pacing-table abbr { text-decoration: underline dotted var(--lv-line-strong); text-underline-offset: 2px; cursor: help; }
.pacing-ast { margin-left: 1px; font-weight: var(--lv-fw-bold); }
.pacing-two { display: grid; grid-template-columns: 1fr; gap: var(--lv-sp-3); }
@media (min-width: 1100px) { .pacing-two { grid-template-columns: 1fr 1fr; } }
.pacing-kv { display: grid; grid-template-columns: 1fr auto auto; gap: 4px var(--lv-sp-4); font-size: var(--lv-fs-base); font-variant-numeric: tabular-nums; }
.pacing-kv .h { color: var(--lv-muted); font-size: var(--lv-fs-xs); font-weight: var(--lv-fw-bold); text-align: right; }
.pacing-kv .n { text-align: right; }
.pacing-feeds { display: flex; flex-wrap: wrap; gap: var(--lv-sp-2); }
.pacing-feed { display: inline-flex; align-items: center; gap: 6px; font-size: var(--lv-fs-xs); color: var(--lv-ink-2); }
.pacing-seg-row { display: flex; flex-wrap: wrap; align-items: center; gap: var(--lv-sp-3); }
.pacing-trend-scroll { overflow: auto; max-height: 70vh; border: 1px solid var(--lv-line); border-radius: var(--lv-r-md); background: var(--lv-surface); }
.pacing-trend-scroll table { border-collapse: separate; border-spacing: 0; font-size: var(--lv-fs-sm); font-variant-numeric: tabular-nums; }
.pacing-trend-scroll th, .pacing-trend-scroll td { height: var(--lv-row-h, 26px); padding: 0 var(--lv-sp-2); min-width: 84px; text-align: right; white-space: nowrap; border-bottom: 1px solid var(--lv-line-soft); border-right: 1px solid var(--lv-line-soft); background: var(--lv-surface); }
.pacing-trend-scroll thead th { position: sticky; top: 0; z-index: 3; background: var(--lv-actual-head); color: var(--lv-actual-ink); font-weight: var(--lv-fw-semi); }
.pacing-trend-scroll thead th.proj { background: var(--lv-proj-head); color: var(--lv-proj-ink); border-left: 2px solid var(--lv-proj-rule); }
.pacing-trend-scroll th.lab, .pacing-trend-scroll td.lab { position: sticky; left: 0; z-index: 2; min-width: 230px; max-width: 280px; text-align: left; overflow: hidden; text-overflow: ellipsis; font-weight: inherit; border-right: 1px solid var(--lv-line); }
.pacing-trend-scroll thead th.lab { z-index: 4; background: var(--lv-actual-head); }
.pacing-trend-scroll td.proj { background: var(--lv-proj-cell); }
.pacing-trend-scroll tr.band td { background: var(--lv-band-bg); font-size: var(--lv-fs-2xs); font-weight: var(--lv-fw-bold); letter-spacing: var(--lv-track-kicker); text-transform: uppercase; text-align: left; border-top: 1px solid var(--lv-line); cursor: pointer; }
.pacing-trend-scroll tr.sub td { background: var(--lv-surface-2); text-align: left; font-weight: var(--lv-fw-semi); }
.pacing-trend-scroll tr.subtotal td { font-weight: var(--lv-fw-semi); background: var(--lv-subtotal-bg); }
.pacing-trend-scroll tr.ratio td { color: var(--lv-ink-2); }
.pacing-trend-scroll td.neg { color: var(--lv-neg); }
.pacing-trend-scroll td.zero { color: var(--lv-muted); }
.pacing-caret { display: inline-block; width: 14px; }
.pacing-vars { display: grid; grid-template-columns: 1fr; gap: var(--lv-sp-2) var(--lv-sp-5); margin-top: var(--lv-sp-3); }
@media (min-width: 900px) { .pacing-vars { grid-template-columns: 1fr 1fr; } }
.pacing-var { display: grid; grid-template-columns: minmax(0, 1fr) 150px auto; align-items: center; gap: var(--lv-sp-2); padding: 6px 0; border-bottom: 1px solid var(--lv-line-soft); }
.pacing-var .nm { font-weight: var(--lv-fw-semi); }
.pacing-var .nm small { display: block; color: var(--lv-muted); font-weight: var(--lv-fw-medium); font-size: var(--lv-fs-xs); }
.pacing-var input[type="text"] { font: inherit; width: 100%; min-height: 30px; padding: 0 var(--lv-sp-2); border: 1px solid var(--lv-input-edge); border-radius: var(--lv-r-sm); background: var(--lv-input-bg); color: var(--lv-input-ink); font-variant-numeric: tabular-nums; text-align: right; }
.pacing-var input[type="text"]:focus { background: var(--lv-editor-bg); }
.pacing-var input[type="text"].bad { border-color: var(--lv-bad); }
.pacing-var .def { color: var(--lv-muted); font-size: var(--lv-fs-xs); white-space: nowrap; }
.pacing-details > summary { cursor: pointer; font-weight: var(--lv-fw-heavy); font-size: var(--lv-fs-md); }
@media (max-width: 640px) { .pacing-table th.lab, .pacing-table td.lab { min-width: 150px; } .pacing-trend-scroll th.lab, .pacing-trend-scroll td.lab { min-width: 140px; } }
`;

const ABBR = {
  NC: 'new customer', CPA: 'cost per acquisition', ROAS: 'return on ad spend', AOV: 'average order value', MER: 'marketing efficiency ratio',
  CPC: 'cost per click', CPM: 'cost per thousand impressions', CTR: 'click-through rate', CVR: 'conversion rate',
  EBITDA: 'earnings before interest, taxes, depreciation and amortization', NOI: 'net operating income', TACoS: 'total advertising cost of sales',
  DTC: 'direct to consumer',
};
const ABBR_RX = new RegExp('\\b(' + Object.keys(ABBR).join('|') + ')\\b', 'g');
const FMT = { money: ['usd0'], money2: ['usd'], int: ['num', 0], num2: ['num', 2], pct: ['pct', 1], pct1: ['pct', 1], x: ['x', 2] };

const UP = { good: 'On pace', warn: 'Behind', bad: 'Well behind' };
const DOWN = { good: 'Within plan', warn: 'Over plan', bad: 'Well over plan' };

function installStyle() {
  if (document.getElementById('pg-pacing-style')) return;
  const s = document.createElement('style');
  s.id = 'pg-pacing-style';
  s.textContent = CSS;
  document.head.append(s);
}

export async function render(el, ctx) {
  installStyle();
  const { h, ui, fmt } = ctx;
  const asOf = ctx.params && ctx.params.as_of ? { as_of: ctx.params.as_of } : {};
  const state = { month: (ctx.params && ctx.params.month) || null, grain: 'month', view: 'rollup', delta: false, collapsed: null };

  const fd = (v, f) => {
    const [kind, digits] = FMT[f] || ['num'];
    return fmt.display(v, kind, digits == null ? {} : { digits });
  };
  const abbr = (text) => {
    const out = [];
    let last = 0;
    String(text).replace(ABBR_RX, (m, key, idx) => {
      if (idx > last) out.push(text.slice(last, idx));
      out.push(h('abbr', { title: ABBR[key] }, key));
      last = idx + m.length;
      return m;
    });
    if (last < text.length) out.push(text.slice(last));
    return out.length ? out : text;
  };
  const wordFor = (row) => (row.direction === 'down' ? DOWN : UP)[row.status];
  const toneOf = (row) => (row.status === 'good' || row.status === 'warn' || row.status === 'bad' ? row.status : null);

  // ---- layout hosts
  const headHost = h('div');
  const cardsHost = h('div');
  const noteHost = h('div');
  const tableHost = h('div');
  const extraHost = h('div');
  const varsHost = h('div');
  const snapHost = h('div');
  const trendHost = h('div');
  const feedHost = h('div');
  el.replaceChildren(h('div', { class: 'pg-page' }, headHost, cardsHost, noteHost, tableHost, extraHost, varsHost, snapHost, trendHost, feedHost));

  const monthSelect = h('select', { 'aria-label': 'Month', onchange: () => { state.month = monthSelect.value; ctx.setParams({ month: state.month }); loadMonth(); } });
  const feedSummary = h('span');
  const stateChip = h('span');

  // ---- month pacing
  function monthStateChip(state) {
    const words = { books: ['Books', 'neutral', 'The books (QuickBooks) hold this month.'], feeds: ['Feeds', 'neutral', 'Complete in the daily feeds; not yet in the books.'],
      projected: ['Projected*', 'info', 'Still running: the figures to date are projected to month end.'] }[state];
    if (!words) return '';
    const c = ui.chip(words[0], words[1]);
    c.title = words[2];
    return c;
  }
  function paceCell(row) {
    if (row.pacing == null) return h('td', { class: 'pace' }, row.direction == null ? '' : '—');
    const pct = row.pacing * 100;
    const width = Math.max(0, Math.min(150, pct)) / 150 * 100;
    const tone = toneOf(row);
    const kids = [h('div', { class: 'pacing-bar ' + (tone || ''), role: 'img', 'aria-label': `${Math.round(pct)}% of goal` },
      h('span', { class: 'fill', style: { width: width + '%' } }), h('span', { class: 'tick', style: { left: (100 / 150 * 100) + '%' } })),
    h('span', { class: 'pct' }, `${Math.round(pct)}%`)];
    if (tone) kids.push(ui.chip(wordFor(row), tone));
    return h('td', { class: 'pace' }, h('div', { class: 'p' }, kids));
  }
  function numCell(row, v, opts = {}) {
    const d = fd(v, row.fmt);
    const cls = [d.neg ? 'neg' : '', d.zero ? 'zero' : '', opts.proj ? 'proj' : ''].filter(Boolean).join(' ');
    if (d.blank) return h('td', { class: cls });
    const c = opts.context;
    return h('td', { class: cls }, d.text, opts.star && !d.zero ? h('span', { class: 'pacing-ast' }, '*') : null,
      c && c.value != null ? h('div', { class: 'pacing-ctx', title: `${c.days} days of spend and new customers ending at the last complete day` }, `${c.label} ${fd(c.value, row.fmt).text}`) : null);
  }
  function monthTable(rows) {
    const head = h('tr', null,
      h('th', { class: 'lab', scope: 'col' }, 'Metric'),
      h('th', { scope: 'col', style: { textAlign: 'left' } }, 'Pace against the goal'),
      h('th', { scope: 'col' }, 'Month to date'),
      h('th', { scope: 'col' }, 'Last 7-day average'),
      h('th', { scope: 'col' }, 'Daily pace needed'),
      h('th', { scope: 'col' }, 'Month to date, on plan'),
      h('th', { scope: 'col' }, 'Month goal'),
      h('th', { scope: 'col', class: 'proj' }, 'Month-end projection*'));
    const body = h('tbody');
    let group = null;
    for (const r of rows) {
      if (r.group !== group) {
        group = r.group;
        body.append(h('tr', { class: 'grp' }, h('td', { class: 'lab', colspan: 1 }, group), h('td', { colspan: 7 })));
      }
      body.append(h('tr', { class: r.kind },
        h('th', { class: 'lab', scope: 'row', style: { paddingLeft: `calc(var(--lv-sp-3) + ${r.indent * 14}px)` }, title: r.note || null },
          abbr(r.label), r.note ? h('span', { class: 'pg-note', title: r.note, 'aria-label': r.note }, 'i') : null),
        paceCell(r), numCell(r, r.mtd, { context: r.context }), numCell(r, r.last7), numCell(r, r.daily_needed), numCell(r, r.should_be), numCell(r, r.goal),
        numCell(r, r.projection, { proj: true, star: r.fmt !== 'pct1' && r.kind !== 'ratio' })));
    }
    return h('div', { class: 'pacing-table-wrap' }, h('table', { class: 'pacing-table', 'aria-label': 'Month pacing against the plan' },
      h('thead', null, head), body));
  }

  function cards(data) {
    const by = Object.fromEntries(data.rows.map((r) => [r.key, r]));
    const spec = [['total_rev', 'Revenue'], ['noi', 'Net operating income'], ['advertising', 'Ad spend'], ['nc_cpa', 'Cost per new customer'], ['nc_roas', 'New-customer return on ad spend']];
    const out = spec.filter(([k]) => by[k]).map(([k, label]) => {
      const r = by[k];
      const tone = toneOf(r);
      const width = r.pacing == null ? 0 : Math.max(0, Math.min(150, r.pacing * 100)) / 150 * 100;
      return h('div', { class: 'pacing-card', 'data-key': k },
        h('div', { class: 't' }, label),
        h('div', { class: 'v' }, fd(r.projection, r.fmt).text, r.kind === 'ratio' ? null : h('span', { class: 'pacing-ast' }, '*')),
        h('div', { class: 'g' }, `Goal ${fd(r.goal, r.fmt).text} · ${r.pacing == null ? 'no pace' : Math.round(r.pacing * 100) + '%'}`),
        r.context && r.context.value != null ? h('div', { class: 'pacing-ctx' }, `${r.context.label} ${fd(r.context.value, r.fmt).text}${r.context.pacing != null ? ` · ${Math.round(r.context.pacing * 100)}% of goal` : ''}`) : null,
        h('div', { class: 'row' }, h('div', { class: 'pacing-bar ' + (tone || ''), role: 'img', 'aria-label': r.pacing == null ? 'No goal' : `${Math.round(r.pacing * 100)}% of goal` },
          h('span', { class: 'fill', style: { width: width + '%' } }), h('span', { class: 'tick', style: { left: (100 / 150 * 100) + '%' } })),
        tone ? ui.chip(wordFor(r), tone) : null));
    });
    const pb = data.payback && data.payback.available ? data.payback : null;
    if (pb) {
      const m = pb.loaded ? pb.loaded.months : pb.months;
      const tone = m.status === 'none' ? null : m.status;
      out.push(h('div', { class: 'pacing-card', 'data-key': 'payback' },
        h('div', { class: 't' }, pb.loaded ? 'Payback after all variable costs' : 'Payback on product margin'),
        h('div', { class: 'v' }, `${m.mtd_label} months`),
        h('div', { class: 'g' }, `Goal ${m.goal_label} months${pb.loaded ? ` · ${pb.months.mtd_label} on product margin` : ''}`),
        h('div', { class: 'row' }, tone ? ui.chip(tone === 'good' ? 'On pace' : tone === 'warn' ? 'Behind' : 'Well behind', tone) : null)));
    }
    return h('div', { class: 'pacing-cards' }, out);
  }

  function tldrPart(data) {
    const by = Object.fromEntries(data.rows.map((r) => [r.key, r]));
    const kp = (k, label, note) => {
      const r = by[k];
      if (!r) return null;
      return { label, value: r.pacing == null ? '—' : `${Math.round(r.pacing * 100)}%`, tone: toneOf(r) === 'good' ? 'good' : toneOf(r) === 'warn' ? 'warn' : toneOf(r) === 'bad' ? 'bad' : null, note };
    };
    const kpis = [kp('total_rev', 'Revenue pace', fmt.compact(by.total_rev && by.total_rev.projection) + '*'),
      kp('noi', 'Net operating income pace', fmt.compact(by.noi && by.noi.projection) + '*'),
      kp('nc_roas', 'New-customer ROAS', by.nc_roas ? `${fd(by.nc_roas.mtd, 'x').text} vs ${fd(by.nc_roas.goal, 'x').text}` : ''),
      kp('advertising', 'Ad spend pace', fmt.compact(by.advertising && by.advertising.projection) + '*')].filter(Boolean);
    const pb = data.payback && data.payback.available ? data.payback : null;
    if (pb) {
      const m = pb.loaded ? pb.loaded.months : pb.months;
      kpis.push({ label: pb.loaded ? 'Payback after all costs, months' : 'Payback, months', value: String(m.mtd_label), tone: m.status === 'none' ? null : m.status,
        note: pb.loaded ? `${pb.months.mtd_label} on product margin · goal ${m.goal_label}` : `goal ${m.goal_label}` });
    }
    return kpis;
  }

  function tldrLine(data) {
    if (!data.rows.length) return data.days_elapsed === 0 ? 'The month has not started. Pick an earlier month.' : 'No data for this month yet.';
    const by = Object.fromEntries(data.rows.map((r) => [r.key, r]));
    const rev = by.total_rev, noi = by.noi;
    const left = data.days_remaining;
    const first = rev && rev.pacing != null
      ? `Revenue is pacing to ${fmt.compact(rev.projection)}*, ${Math.round(rev.pacing * 100)}% of goal`
      : 'Revenue has no goal for this month';
    const second = noi && noi.pacing != null ? `; net operating income ${Math.round(noi.pacing * 100)}%` : '';
    return `${first}${second}${left > 0 ? `, ${left} days left` : ''}.`;
  }

  function freshnessChips(list) {
    return h('div', { class: 'pacing-feeds', 'aria-label': 'Data feeds' }, list.map((f) => h('span', { class: 'pacing-feed', title: f.label },
      ui.chip(f.status === 'good' ? 'Fresh' : f.age_days == null ? 'No data' : `${f.age_days} days old`, f.status === 'good' ? 'good' : f.status === 'warn' ? 'warn' : 'bad'),
      f.label.replace(/ \(.*\)$/, '').replace(/:.*$/, '') + (f.data_through ? `: through ${fmt.date(f.data_through).replace(/, \d{4}$/, '')}` : ''))));
  }

  function paybackBox(data) {
    const pb = data.payback;
    if (!pb || !pb.available) return h('div', { class: 'pg-card' }, h('h2', { class: 'pg-h2' }, 'Payback'), h('p', { class: 'muted' }, pb ? pb.reason : 'Payback needs the month to have data and the cohort curve.'));
    const d = (v, f) => fd(v, f).text || '—';
    const ld = pb.loaded;
    return h('div', { class: 'pg-card', 'data-box': 'payback' },
      h('h2', { class: 'pg-h2' }, 'Payback'),
      h('div', { class: 'pacing-kv' },
        h('span', { class: 'h' }), h('span', { class: 'h' }, 'Month to date'), h('span', { class: 'h' }, 'Goal'),
        h('span', null, abbr('NC AOV')), h('span', { class: 'n' }, d(pb.aov.mtd, 'money2')), h('span', { class: 'n' }, d(pb.aov.goal, 'money2')),
        h('span', null, abbr('NC CPA')), h('span', { class: 'n' }, d(pb.cpa.mtd, 'money2')), h('span', { class: 'n' }, d(pb.cpa.goal, 'money2')),
        h('span', null, 'First-order margin on product margin'), h('span', { class: 'n' }, d(pb.first_order_margin.mtd, 'money2')), h('span', { class: 'n' }, d(pb.first_order_margin.goal, 'money2')),
        h('span', { title: 'Product cost and fulfillment only' }, 'Payback on product margin, months'), h('span', { class: 'n' }, pb.months.mtd_label), h('span', { class: 'n' }, pb.months.goal_label),
        ld ? h('span', null, 'First-order margin after all variable costs') : null, ld ? h('span', { class: 'n' }, d(ld.first_order_margin.mtd, 'money2')) : null, ld ? h('span', { class: 'n' }, d(ld.first_order_margin.goal, 'money2')) : null,
        ld ? h('span', { title: 'Also payment processing, chargebacks, inbound shipping and software' }, 'Payback after all variable costs, months') : null, ld ? h('span', { class: 'n' }, ld.months.mtd_label) : null, ld ? h('span', { class: 'n' }, ld.months.goal_label) : null),
      ui.source(`Month-to-date order value and cost per customer on the live curve, against the plan on the goal curve. On product margin: product cost ${fmt.pct(pb.product_cost_pct, 2)} of order value and fulfillment ${fmt.money(pb.fulfillment_per_order, { digits: 2 })} per order.`
        + (ld ? ` After all variable costs: the plan's cost rates take ${fmt.pct(ld.variable_cost_rate, 1)} of each order (the same figure as Cash and debt).` : '')),
      !ld && pb.loaded_unavailable ? h('p', { class: 'pacing-note' }, ui.chip('Unavailable', 'warn'), `Payback after all variable costs: ${pb.loaded_unavailable}`) : null);
  }

  function intradayBox() {
    const spent = h('input', { type: 'number', min: '0', step: '1', inputmode: 'decimal', 'aria-label': 'Meta spend so far today' });
    const now = new Date(new Date().toLocaleString('en-US', { timeZone: 'America/Los_Angeles' }));
    const time = h('input', { type: 'time', 'aria-label': 'Time of day, Pacific', value: `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}` });
    const out = h('p', { class: 'pacing-note', role: 'status' }, 'Enter today\'s Meta spend so far to project the end-of-day total.');
    let timer = null;
    const run = () => {
      clearTimeout(timer);
      timer = setTimeout(async () => {
        if (!spent.value) return;
        try {
          const r = await ctx.api.get('pacing/intraday', { spent: spent.value, time: time.value });
          if (!ctx.alive()) return;
          const note = r.estimate ? [ui.chip('Estimate', 'info', { estimate: true }), ' '] : [];
          if (r.projected_eod == null) { out.replaceChildren(...note, r.message || 'Too early in the day to project.'); return; }
          out.replaceChildren(...note, `Projected end-of-day Meta spend ${fmt.money(r.projected_eod)}*, ${fmt.money(r.to_go)} still to go (${fmt.pct(r.share_of_day, 0)} of a typical day's spend is out by ${time.value}). `, r.message || '');
        } catch (e) { out.textContent = e.message; }
      }, 250);
    };
    spent.addEventListener('input', run);
    time.addEventListener('input', run);
    return h('div', { class: 'pg-card', 'data-box': 'intraday' },
      h('h2', { class: 'pg-h2' }, 'Meta spend today'),
      h('div', { class: 'pacing-controls' }, h('label', null, 'Spent so far $'), spent, h('label', null, 'Time (Pacific)'), time),
      out,
      ui.source('Share of a typical day\'s spend already out by that time, from the last 30 days of hourly Meta spend.'));
  }

  // ---- variables: every number behind the pacing, with its sheet value and source
  const fromDisplay = (lev, text) => {
    const t = String(text).trim();
    if (t === '') return null;
    if (lev.unit === 'fraction') { const n = Number(t.replace('%', '')); return Number.isFinite(n) ? n / 100 : NaN; }
    if (lev.unit === 'rows') return t.split(',').map((x) => Number(x.trim())).filter((x) => x === x);
    if (lev.unit === 'map') { try { return JSON.parse(t); } catch { return NaN; } }
    if (lev.unit === 'month') return t;
    return Number(t);
  };
  const toDisplay = (lev, v) => {
    if (v == null) return '';
    if (lev.unit === 'fraction') return String(Math.round(v * 10000) / 100) + '%';
    if (lev.unit === 'rows') return v.join(', ');
    if (lev.unit === 'map') return JSON.stringify(v);
    return String(v);
  };
  async function loadVariables(host) {
    let data, rules;
    try {
      [data, rules] = await Promise.all([ctx.api.get('pacing/levers'), ctx.api.get('pacing/rules')]);
    } catch (e) { host.replaceChildren(h('div', { class: 'errorbox', role: 'alert' }, e.message)); return; }
    if (!ctx.alive()) return;
    const save = async (key, value, input) => {
      if (typeof value === 'number' && Number.isNaN(value)) { input.classList.add('bad'); ctx.toast('Not a valid value', 'error'); return; }
      input.classList.remove('bad');
      try {
        await ctx.api.put('pacing/levers', { scenario: ctx.scenario, levers: { [key]: value } });
        ctx.toast('Saved', 'ok');
        loadVariables(host); loadMonth(); loadTrend();
      } catch (e) { input.classList.add('bad'); ctx.toast(e.message, 'error'); }
    };
    const row = (lev) => {
      const input = h('input', { type: 'text', value: toDisplay(lev, lev.value), 'aria-label': lev.label, inputmode: 'decimal' });
      input.addEventListener('change', () => save(lev.key, fromDisplay(lev, input.value), input));
      return h('div', { class: 'pacing-var', 'data-lever': lev.key },
        h('div', { class: 'nm', title: lev.source }, lev.label, h('small', null, lev.help || lev.source)),
        input,
        h('div', { class: 'def' }, lev.is_default ? 'sheet value' : `sheet ${toDisplay(lev, lev.default)}`,
          lev.is_default ? null : h('button', { type: 'button', class: 'btn small', style: { marginLeft: '6px' }, onclick: () => save(lev.key, null, input) }, 'Reset'),
          lev.owner !== 'pacing' ? h('span', { class: 'pg-chip info', style: { marginLeft: '6px' }, title: `Stored with the ${lev.owner} variables` }, lev.owner) : null));
    };
    const rulesRows = rules.rules.map((r) => {
      const text = r.key === 'stage_keyword' ? r.value : JSON.stringify(r.value);
      const input = h('input', { type: 'text', value: text, 'aria-label': r.key, style: { textAlign: 'left' } });
      input.addEventListener('change', async () => {
        let v = input.value.trim();
        if (r.key !== 'stage_keyword') { try { v = JSON.parse(v); } catch { input.classList.add('bad'); ctx.toast('Enter the patterns as {"Product": "pattern"}', 'error'); return; } }
        try { await ctx.api.put('pacing/rules', { rules: { [r.key]: v } }); ctx.toast('Saved. Applies at the next refresh.', 'ok'); loadVariables(host); } catch (e) { input.classList.add('bad'); ctx.toast(e.message, 'error'); }
      });
      return h('div', { class: 'pacing-var', style: { gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 2fr) auto' } },
        h('div', { class: 'nm', title: r.source }, r.key === 'stage_keyword' ? 'Testing campaign keyword' : r.key === 'product_rules' ? 'Product patterns, campaign groups' : 'Product patterns, spend by product', h('small', null, r.source)),
        input, h('div', { class: 'def' }, r.is_default ? 'sheet value' : h('button', { type: 'button', class: 'btn small', onclick: async () => { await ctx.api.put('pacing/rules', { rules: { [r.key]: null } }); loadVariables(host); } }, 'Reset')));
    });
    host.replaceChildren(
      h('p', { class: 'pg-source' }, `Variables of the ${ctx.scenarioInfo.name} scenario. Amber cells are inputs; each shows the sheet's value and where it came from. Changes are saved, versioned and shown in History.`),
      h('div', { class: 'pacing-vars' }, data.levers.map(row)),
      h('h3', { class: 'pg-h2', style: { marginTop: 'var(--lv-sp-4)' } }, 'Feed rules (all scenarios)'),
      h('div', { class: 'pacing-vars', style: { gridTemplateColumns: '1fr' } }, rulesRows));
  }
  function variablesBox() {
    const host = h('div');
    const box = h('details', { class: 'pg-card pacing-details', 'data-box': 'variables' }, h('summary', null, 'Variables'), host);
    let loaded = false;
    box.addEventListener('toggle', () => { if (box.open && !loaded) { loaded = true; loadVariables(host); } });
    return box;
  }

  async function loadMonth() {
    ui.loading(tableHost, 'Loading the month…');
    let data;
    try {
      data = await ctx.api.get('pacing/month', { month: state.month, ...asOf });
    } catch (e) { ui.error(tableHost, e, loadMonth); return; }
    if (!ctx.alive()) return;
    state.month = data.month;
    monthSelect.replaceChildren(...data.months.map((m) => h('option', { value: m.month, selected: m.month === data.month }, m.label)));
    monthSelect.value = data.month;
    stateChip.replaceChildren(monthStateChip(data.state));
    const chips = data.freshness || [];
    const behind = chips.filter((c) => c.status !== 'good').length;
    feedSummary.replaceChildren(behind ? ui.chip(`${behind} ${behind === 1 ? 'feed' : 'feeds'} behind`, chips.some((c) => c.status === 'bad') ? 'bad' : 'warn') : ui.chip(`Data through ${fmt.date(data.data_through).replace(/, \d{4}$/, '')}`, 'good'));
    const missing = data.missing_goal_rows && data.missing_goal_rows.length;
    headHost.replaceChildren(ui.pageHeader({
      title: 'Pacing',
      purpose: 'Where the month is landing against the plan, by day, week and month. NC = new customer, ROAS = return on ad spend, CPA = cost per acquisition.',
      actions: [h('div', { class: 'pacing-controls' }, h('label', { for: 'pacing-month' }, 'Month'), Object.assign(monthSelect, { id: 'pacing-month' }), stateChip, feedSummary)],
      tldr: { line: tldrLine(data), kpis: tldrPart(data) },
    }));
    cardsHost.replaceChildren(data.rows.length ? cards(data) : '');
    const notes = [];
    if (data.early) notes.push(h('div', { class: 'pacing-note' }, ui.chip('Early in the month', 'info'), `${data.days_elapsed} of ${data.days_in_month} days so far. Early projections swing: subscription renewals land in the first days of the month.`));
    if (missing) notes.push(h('div', { class: 'pacing-note' }, ui.chip('Plan lines missing', 'warn'), 'The scenario P&L has no values for some cost lines yet, so cost and profit goals read as zero for them.'));
    noteHost.replaceChildren(...notes);
    if (!data.rows.length) {
      ui.empty(tableHost, data.days_elapsed === 0 ? 'This month has not started. Pick an earlier month.' : 'No actuals for this month yet.');
    } else {
      tableHost.replaceChildren(monthTable(data.rows), ui.source(data.captions[0]));
    }
    extraHost.replaceChildren(h('div', { class: 'pacing-two' }, paybackBox(data), intradayBox()));
    feedHost.replaceChildren(h('section', { class: 'pg-section' }, h('h2', { class: 'pg-h2' }, 'Data feeds'), freshnessChips(chips),
      ui.source('Each feed refreshes daily and should reach yesterday. Meta spend comes from the Tag Explorer data; the rest is read from the Lonvera Projections sheet.')));
    loadSnapshot();
  }

  // ---- month snapshot
  async function loadSnapshot() {
    let data;
    try {
      data = await ctx.api.get('pacing/snapshot', { month: state.month, ...asOf });
    } catch (e) { snapHost.replaceChildren(h('div', { class: 'errorbox', role: 'alert' }, e.message)); return; }
    if (!ctx.alive()) return;
    const body = h('tbody');
    let group = null;
    for (const r of data.rows) {
      if (r.group !== group) { group = r.group; body.append(h('tr', { class: 'grp' }, h('td', { class: 'lab' }, group), h('td', { colspan: 5 }))); }
      const dd = (v) => fd(v, r.fmt);
      const cell = (v, extra = {}) => { const d = dd(v); return h('td', { class: [d.neg ? 'neg' : '', extra.cls || ''].join(' ').trim() }, d.text, extra.star && !d.blank ? h('span', { class: 'pacing-ast' }, '*') : null); };
      const dp = fd(r.diff_pct, 'pct');
      const tone = r.status === 'good' || r.status === 'warn' || r.status === 'bad' ? r.status : null;
      body.append(h('tr', null, h('th', { class: 'lab', scope: 'row' }, abbr(r.label)), cell(r.actual), cell(r.projected, { cls: 'proj', star: data.partial && r.fmt !== 'x' && r.fmt !== 'money2' }),
        cell(r.target), cell(r.diff), h('td', { class: dp.neg ? 'neg' : '' }, dp.text, tone ? ' ' : null, tone ? ui.chip((r.direction === 'down' ? DOWN : UP)[tone], tone) : null)));
    }
    if (data.payback) {
      const pb = data.payback;
      body.append(h('tr', { class: 'grp' }, h('td', { class: 'lab' }, 'PAYBACK (DTC)'), h('td', { colspan: 5 })));
      if (pb.loaded) {
        body.append(h('tr', null, h('th', { class: 'lab', scope: 'row' }, 'Payback after all variable costs, months'), h('td', null, pb.loaded.months.actual_label), h('td', { class: 'proj' }, pb.loaded.months.actual_label),
          h('td', null, pb.loaded.months.target_label), h('td'), h('td')));
      }
      body.append(h('tr', null, h('th', { class: 'lab', scope: 'row' }, 'First-order margin'), h('td', null, fd(pb.first_order_margin.actual, 'money2').text), h('td', { class: 'proj' }, fd(pb.first_order_margin.actual, 'money2').text),
        h('td', null, fd(pb.first_order_margin.target, 'money2').text), h('td'), h('td')));
      body.append(h('tr', null, h('th', { class: 'lab', scope: 'row' }, 'Payback on product margin, months'), h('td', null, pb.months.actual_label), h('td', { class: 'proj' }, pb.months.actual_label),
        h('td', null, pb.months.target_label), h('td', null, pb.months.actual != null && pb.months.target != null ? String(pb.months.actual - pb.months.target) : ''), h('td')));
    }
    const table = h('div', { class: 'pacing-table-wrap' }, h('table', { class: 'pacing-table', style: { minWidth: '640px' }, 'aria-label': 'Month snapshot' },
      h('thead', null, h('tr', null, h('th', { class: 'lab', scope: 'col' }, 'Metric'), h('th', null, 'Actual'), h('th', { class: 'proj' }, data.partial ? 'Projected*' : 'Month total'), h('th', null, 'Plan'), h('th', null, 'Difference'), h('th', null, 'Difference %'))), body));
    snapHost.replaceChildren(h('section', { class: 'pg-section' }, h('h2', { class: 'pg-h2' }, `Month snapshot, ${fmt.monthLabel(data.month, true)}`), h('p', { class: 'pacing-note' }, monthStateChip(data.state), data.status_text), table,
      ui.source('Actuals through yesterday. Revenue is on the plan\'s basis (Shopify less the estimated sales tax, plus Amazon). EBITDA and net operating income are estimates from the plan\'s cost rates.')));
  }

  // ---- trends
  const seg = (items, current, onPick, label) => h('div', { class: 'segs', role: 'group', 'aria-label': label },
    items.map(([val, text]) => h('button', { type: 'button', class: 'seg', 'aria-pressed': String(val === current), onclick: () => onPick(val) }, text)));

  async function loadTrend() {
    ui.loading(trendHost, 'Loading trends…');
    let data;
    try {
      data = await ctx.api.get('pacing/rollup', { grain: state.grain, view: state.view, delta: state.delta ? 1 : null, ...asOf });
    } catch (e) { ui.error(trendHost, e, loadTrend); return; }
    if (!ctx.alive()) return;
    if (state.collapsed == null) state.collapsed = new Set(data.rows.filter((r) => r.kind === 'header').map((r) => r.key).filter((k) => k !== 'band:plan' && k !== 'band:Business-wide' && k !== 'band:Meta campaigns: All Meta'));
    drawTrend(data);
  }
  function drawTrend(data) {
    const controls = h('div', { class: 'pacing-seg-row' },
      seg([['day', 'Day'], ['week', 'Week'], ['month', 'Month']], state.grain, (v) => { state.grain = v; state.collapsed = null; loadTrend(); }, 'Period'),
      seg([['rollup', 'All channels'], ['meta', 'Meta campaigns']], state.view, (v) => { state.view = v; state.collapsed = null; loadTrend(); }, 'View'),
      h('label', { class: 'pacing-note' }, h('input', { type: 'checkbox', checked: state.delta, onchange: (e) => { state.delta = e.target.checked; loadTrend(); } }), 'Show change from the prior period'),
      h('button', { type: 'button', class: 'btn small', onclick: () => { state.collapsed = new Set(data.rows.filter((r) => r.kind === 'header').map((r) => r.key)); drawTrend(data); } }, 'Collapse all'),
      h('button', { type: 'button', class: 'btn small', onclick: () => { state.collapsed = new Set(); drawTrend(data); } }, 'Expand all'),
      h('button', { type: 'button', class: 'btn small', onclick: () => copyTsv(data) }, 'Copy as text'));
    const head = h('tr', null, h('th', { class: 'lab', scope: 'col' }, 'Metric'), data.months.map((m, i) => h('th', { scope: 'col', class: data.status[i] === 'projected' ? 'proj' : '',
      title: `${data.labels[i]}${data.status[i] === 'projected' ? ` (so far: ${data.days[i]} of ${data.days_in_period[i]} days)` : ''}` }, data.labels[i], data.status[i] === 'projected' ? h('span', { class: 'pacing-ast' }, '*') : null)));
    const body = h('tbody');
    let hidden = false;
    let band = null;
    for (const r of data.rows) {
      if (r.kind === 'header') {
        hidden = state.collapsed.has(r.key);
        band = r.key;
        const tr = h('tr', { class: 'band', 'data-key': r.key, onclick: () => { if (state.collapsed.has(r.key)) state.collapsed.delete(r.key); else state.collapsed.add(r.key); drawTrend(data); } },
          h('td', { class: 'lab', colspan: 1 }, h('span', { class: 'pacing-caret' }, hidden ? '▸' : '▾'), abbr(r.label)), h('td', { colspan: data.months.length }));
        body.append(tr);
        continue;
      }
      if (hidden) continue;
      if (r.kind === 'subheader') { body.append(h('tr', { class: 'sub' }, h('td', { class: 'lab' }, abbr(r.label)), h('td', { colspan: data.months.length }))); continue; }
      const vals = state.delta && r.delta ? r.delta : r.values;
      const f = state.delta && r.delta ? 'pct' : r.fmt;
      body.append(h('tr', { class: r.kind }, h('th', { class: 'lab', scope: 'row', title: r.note || r.label }, abbr(r.label)),
        vals.map((v, i) => {
          const d = fd(v, f);
          const star = data.status[i] === 'projected' && r.key.startsWith('plan_') && r.key.endsWith('_proj') && !d.blank;
          return h('td', { class: [d.neg ? 'neg' : '', d.zero ? 'zero' : '', data.status[i] === 'projected' ? 'proj' : ''].filter(Boolean).join(' ') }, d.text, star ? h('span', { class: 'pacing-ast' }, '*') : null);
        })));
    }
    const title = state.view === 'meta' ? 'Meta campaigns' : 'Trends';
    trendHost.replaceChildren(h('section', { class: 'pg-section' }, h('h2', { class: 'pg-h2' }, `${title} by ${state.grain}`), controls,
      h('div', { class: 'pacing-trend-scroll', tabindex: '0', role: 'region', 'aria-label': `${title} by ${state.grain}` }, h('table', { 'aria-label': `${title} by ${state.grain}` }, h('thead', null, head), body)),
      ui.source(data.captions[0])));
  }
  function copyTsv(data) {
    const lines = [['', ...data.labels].join('\t')];
    for (const r of data.rows) {
      if (r.kind === 'header') { lines.push(r.label); continue; }
      if (r.kind === 'subheader') continue;
      lines.push([r.label, ...(state.delta && r.delta ? r.delta : r.values).map((v) => (v == null ? '' : String(v)))].join('\t'));
    }
    const text = lines.join('\n');
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(() => ctx.toast('Copied', 'ok'), () => ctx.toast('Could not copy', 'error'));
  }

  headHost.replaceChildren(ui.pageHeader({ title: 'Pacing', purpose: 'Where the month is landing against the plan.' }));
  varsHost.replaceChildren(variablesBox());
  await loadMonth();
  loadTrend();
  return () => {};
}
