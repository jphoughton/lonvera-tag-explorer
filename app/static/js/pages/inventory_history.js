// Inventory history (/inventory/history): units on hand and allocated for any past day, per product and location with a Total column, and a daily trend per location.
// Everything comes from /api/inventory/history. Amazon and the international warehouse have no stock feed: their numbers are estimates (the last typed count minus units
// shipped since) except on the day a count was typed, and every estimate is marked. A location with no snapshot on a day of the range is shown as a gap, never filled.
// Class names and test ids start with `ih-`.
import { h, fmt, isNum, href, setParams } from '../util.js';
import { api } from '../api.js';
import { showControls } from '../controls.js';
import { pageHeader } from '../ui/pagehead.js';
import { filterRow, seg } from '../ui/pagefilters.js';

const RANGES = [['7', '7 days'], ['30', '30 days'], ['90', '90 days']];
const DEFAULT_DAYS = 30;

const num = (v) => (isNum(v) ? fmt.int(v) : '–');
const estTag = () => h('span', { class: 'ih-est', title: 'An estimate: the last typed count minus units shipped since' }, 'estimate');
const dayLabel = (s) => `${fmt.day(s)}, ${String(s).slice(0, 4)}`;

function snapshotTable(d) {
  const locs = d.locations.filter((l) => l.first_day && l.first_day <= d.date);
  const cell = (loc, prod, field) => d.snapshot.find((r) => r.location === loc && r.product === prod) || null;
  const prods = d.products.filter((p) => d.snapshot.some((r) => r.product === p.key));
  const head1 = h('tr', null, h('th', { rowspan: 2, class: 'ih-first' }, 'Product'),
    ...locs.map((l) => h('th', { colspan: 2, class: 'ih-loc', 'data-testid': 'ih-head-' + l.key }, l.label)),
    h('th', { colspan: 2, class: 'ih-loc' }, 'Total'));
  const head2 = h('tr', null, ...[...locs, { key: 'total' }].flatMap(() => [h('th', { class: 'num' }, 'On hand'), h('th', { class: 'num' }, 'Allocated')]));
  const body = prods.map((p) => {
    const tds = [];
    let oh = 0, al = 0;
    for (const l of locs) {
      const r = cell(l.key, p.key);
      if (!r) { tds.push(h('td', { class: 'num ih-none', colspan: 2, 'data-testid': `ih-cell-${l.key}-${p.key}` }, d.missing_on_date.includes(l.key) ? 'No snapshot' : '–')); continue; }
      oh += r.on_hand; al += r.allocated;
      tds.push(h('td', { class: 'num', 'data-testid': `ih-cell-${l.key}-${p.key}`, 'data-estimate': String(r.estimate) }, num(r.on_hand), r.estimate ? [' ', estTag()] : null),
        h('td', { class: 'num' }, r.estimate ? '–' : num(r.allocated)));
    }
    tds.push(h('td', { class: 'num ih-total', 'data-testid': `ih-total-${p.key}` }, num(oh)), h('td', { class: 'num ih-total' }, num(al)));
    return h('tr', { 'data-product': p.key }, h('th', { class: 'ih-first', scope: 'row' }, p.label), tds);
  });
  const tot = [];
  let toh = 0, tal = 0;
  for (const l of locs) {
    const rs = d.snapshot.filter((r) => r.location === l.key);
    const sum = rs.reduce((a, r) => a + r.on_hand, 0), sal = rs.reduce((a, r) => a + r.allocated, 0);
    toh += sum; tal += sal;
    tot.push(h('td', { class: 'num', 'data-testid': 'ih-sum-' + l.key }, rs.length ? num(sum) : '–', rs.some((r) => r.estimate) ? [' ', estTag()] : null), h('td', { class: 'num' }, rs.length && !rs.some((r) => r.estimate) ? num(sal) : '–'));
  }
  tot.push(h('td', { class: 'num ih-total', 'data-testid': 'ih-sum-total' }, num(toh)), h('td', { class: 'num ih-total' }, num(tal)));
  body.push(h('tr', { class: 'ih-sumrow' }, h('th', { class: 'ih-first', scope: 'row' }, 'All products'), tot));
  return h('div', { class: 'tablebox' }, h('div', { class: 'tablewrap' }, h('table', { class: 'dt ih-table', 'data-testid': 'ih-snapshot' }, h('thead', null, head1, head2), h('tbody', null, body))));
}

function trendTable(d) {
  const locs = d.trend.filter((t) => t.first_day);
  const days = d.trend.length ? d.trend[0].series.map((s) => s.day).reverse() : [];
  const head = h('tr', null, h('th', { class: 'ih-first' }, 'Day'), ...locs.map((t) => h('th', { class: 'num' }, t.label)), h('th', { class: 'num' }, 'Total'));
  const body = days.map((day) => {
    let total = 0, any = false;
    const tds = locs.map((t) => {
      const s = t.series.find((x) => x.day === day);
      if (s && isNum(s.on_hand)) { total += s.on_hand; any = true; return h('td', { class: 'num', 'data-testid': `ih-t-${day}-${t.location}`, 'data-estimate': String(s.estimate) }, num(s.on_hand), s.estimate ? [' ', estTag()] : null); }
      const gap = t.gaps.includes(day);
      return h('td', { class: 'num ' + (gap ? 'ih-gap' : 'ih-none'), 'data-testid': `ih-t-${day}-${t.location}`, 'data-gap': String(gap) }, gap ? 'Missing' : '–');
    });
    return h('tr', { 'data-day': day, class: day === d.date ? 'ih-picked' : null }, h('th', { class: 'ih-first', scope: 'row' }, dayLabel(day)), tds, h('td', { class: 'num ih-total' }, any ? num(total) : '–'));
  });
  return h('div', { class: 'tablebox' }, h('div', { class: 'tablewrap' }, h('table', { class: 'dt ih-table', 'data-testid': 'ih-trend' }, h('thead', null, head), h('tbody', null, body))));
}

export async function inventoryHistoryPage(ctx) {
  const { params: p, signal } = ctx;
  showControls(false);
  document.title = 'Inventory history · Lonvera';
  const d = await api('/api/inventory/history', { date: p.date, days: p.days }, { signal });
  if (!ctx.alive()) return;
  const days = String(d.days);

  const dateInput = h('input', { type: 'date', value: d.date || '', max: d.newest || null, 'aria-label': 'Day', 'data-testid': 'ih-date' });
  const applyDate = () => { if (dateInput.value) setParams({ date: dateInput.value === d.newest ? null : dateInput.value }); };
  dateInput.addEventListener('change', applyDate);
  const dateCtl = h('div', { class: 'ctl' }, h('span', { class: 'ctl-name' }, 'Day'), dateInput);
  const rangeCtl = seg({ name: 'Trend length', testid: 'ih-range', value: days, options: RANGES.map(([k, t]) => [k, t, null, 'ih-range-' + k]),
    href: (k) => href('/inventory/history', { ...p, days: Number(k) === DEFAULT_DAYS ? null : k }) });

  const gapLocs = d.trend.filter((t) => t.gaps.length);
  const gapBox = gapLocs.length
    ? h('div', { class: 'errorbox ih-gaps', role: 'status', 'data-testid': 'ih-gap-box' }, h('strong', null, 'Missing days. '),
      gapLocs.map((t) => `${t.label}: ${t.gaps.length} day${t.gaps.length === 1 ? '' : 's'} with no snapshot (${t.gaps.slice(0, 6).map(fmt.day).join(', ')}${t.gaps.length > 6 ? ', and more' : ''}).`).join(' '))
    : null;

  const header = pageHeader({ title: 'Inventory history', testid: 'ih-head',
    description: 'Units on hand and allocated for any past day, at the US warehouse, Amazon and the international warehouse. Allocated units are promised to open orders.' });

  if (!d.date) {
    ctx.mount(h('div', { class: 'page ih-page', 'data-testid': 'ih-page' }, header, h('p', { class: 'tl-prose', 'data-testid': 'ih-empty' }, 'No daily stock snapshot has been stored yet.')));
    return;
  }
  const note = h('p', { class: 'note-line ih-note', 'data-testid': 'ih-note' }, 'The US warehouse count is read from the warehouse system every night. Amazon and the international warehouse have no live feed, so each day is an ',
    estTag(), ': the last count typed in by hand, minus units shipped since, plus received transfers or purchase orders. On the day a count was typed the number is the count itself. Allocated units are not estimated.');
  ctx.mount(h('div', { class: 'page ih-page', 'data-testid': 'ih-page' }, header,
    h('div', { class: 'ih-controls', 'data-testid': 'ih-controls' }, filterRow(dateCtl, rangeCtl)),
    gapBox,
    h('h2', { class: 'ih-h2', 'data-testid': 'ih-snap-title' }, `Units on ${dayLabel(d.date)}`),
    snapshotTable(d),
    note,
    h('h2', { class: 'ih-h2' }, `Units on hand by day, ${fmt.day(d.from)} to ${fmt.day(d.date)}`),
    trendTable(d)));
}
