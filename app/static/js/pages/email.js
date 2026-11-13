// Lonvera, Email (/email): Klaviyo email and text message (SMS) performance by flow, by campaign and over time. Everything comes from /api/email/* (app/email_view.py).
// The window, the Flows | Campaigns | All choice and the Email | Text messages | All choice live in the URL; the chart's grain, metric and split are kept in the URL too
// (they redraw the chart without reloading the page). A message opens in a dialog (?message=<id>) with its copy, its tiles and a daily chart.
// Class names and test ids start with `eml-` (never ad-, ads-, sponsor, banner, promo: ad blockers hide those).
import { h, fmt, isNum, href, navigate, currentParams } from '../util.js';
import { api } from '../api.js';
import { trendChart, BAR_COLOR, LINE_COLOR } from '../charts.js';
import { showControls } from '../controls.js';
import { pageHeader } from '../ui/pagehead.js';
import { filterRow, seg, selectCtl, searchCtl, checkCtl } from '../ui/pagefilters.js';
import { createTable } from '../table.js';
import { openModal } from '../modal.js';

const WINDOWS = [['7d', 'Last 7 days'], ['30d', 'Last 30 days'], ['90d', 'Last 90 days'], ['all', 'All loaded days']];
const KINDS = [['flows', 'Flows', 'Automated messages sent when something happens: a welcome, a billing reminder, an abandoned cart'], ['campaigns', 'Campaigns', 'One-off messages sent to a list on a chosen day'], ['all', 'All']];
const CHANNELS = [['email', 'Email'], ['sms', 'Text messages'], ['all', 'All']];
const GRAINS = [['day', 'Day'], ['week', 'Week'], ['month', 'Month']];
const CHART_METRICS = [['revenue', 'Revenue'], ['sends', 'Sends'], ['delivered', 'Delivered'], ['opens', 'Opens (views)'], ['open_rate', 'Open rate'], ['click_rate', 'Click rate'], ['orders', 'Orders'], ['rev_per_delivered', 'Revenue per delivered']];
const KIND_OF = { int: 'int', rate: 'rate', money: 'money', money2: 'money2' };
const APPROX = new Set(['open_rate', 'click_rate', 'click_to_open', 'opens']);

const pick = (v, list, dflt) => (list.some((x) => x[0] === v) ? v : dflt);
const stateOf = (p) => ({
  window: pick(p.window, WINDOWS, '30d'), kind: pick(p.kind, KINDS, 'all'), channel: pick(p.channel, CHANNELS, 'all'),
  grain: pick(p.grain, GRAINS, 'day'), metric: pick(p.metric, CHART_METRICS, 'revenue'), split: p.split === '1',
});
const apiParams = (s) => ({ window: s.window, kind: s.kind, channel: s.channel });
// URL of the page with some choices changed; defaults are left out so links stay short
function urlOf(s, change = {}, extra = {}) {
  const x = { ...s, ...change };
  return href('/email', { window: x.window === '30d' ? null : x.window, kind: x.kind === 'all' ? null : x.kind, channel: x.channel === 'all' ? null : x.channel,
    grain: x.grain === 'day' ? null : x.grain, metric: x.metric === 'revenue' ? null : x.metric, split: x.split ? '1' : null, ...extra });
}

const fmtValue = (kind, v) => {
  if (!isNum(v)) return '–';
  switch (kind) {
    case 'money': return fmt.money(v);
    case 'money2': return fmt.money2(v);
    case 'rate': return fmt.rate(v);
    default: return fmt.int(v);
  }
};
const signed = (v, f) => (v > 0 ? '+' : v < 0 ? '-' : '') + f(Math.abs(v));

// the change against the prior period as text and a good / bad / flat class (by the metric's polarity)
function changeOf(def, ch) {
  if (!ch) return null;
  const flat = Math.abs(ch.v) < (ch.t === 'pp' ? 0.0005 : 0.005);
  const text = ch.t === 'pp' ? (flat ? '0.00 points' : signed(ch.v * 100, (x) => x.toFixed(2) + ' points')) : (flat ? '0.0%' : signed(ch.v * 100, (x) => x.toFixed(1) + '%'));
  const cls = flat || def.pol === 'neutral' ? 'eml-flat' : (def.pol === 'good') === (ch.v > 0) ? 'eml-good' : 'eml-bad';
  return { text, cls, dir: flat ? 'flat' : ch.v > 0 ? 'up' : 'down' };
}

const dayRange = (a, b) => (a === b ? fmt.day(a) : `${fmt.day(a)} to ${fmt.day(b)}`);

function tile(def, value, d, plain = false) {
  const c = d && d.change ? changeOf(def, d.change[def.key]) : null;
  let sub;
  if (plain) sub = null;
  else if (!d || !d.prior_totals) sub = h('span', { class: 'eml-nochg', title: d && d.window ? d.window.prior_why : null, 'data-testid': 'eml-nochange' }, 'No prior period');
  else if (!c) sub = h('span', { class: 'eml-nochg' }, 'No change shown');
  else sub = h('span', { class: 'eml-chg ' + c.cls, 'data-dir': c.dir, 'data-testid': 'eml-change-' + def.key }, c.text, h('span', { class: 'eml-vs' }, ' vs prior'));
  return h('div', { class: 'kpi eml-kpi', 'data-testid': 'eml-tile-' + def.key, title: def.help },
    h('div', { class: 'kpi-label' }, def.label),
    h('div', { class: 'kpi-value', 'data-testid': 'eml-tile-' + def.key + '-value', 'data-value': isNum(value) ? String(value) : '' }, fmtValue(KIND_OF[def.kind], value)),
    sub ? h('div', { class: 'kpi-sub' }, sub) : null,
    APPROX.has(def.key) ? h('div', { class: 'kpi-sub eml-approx' }, 'approximate') : null);
}

const DEFS = [
  ['Flow and campaign', 'A flow is a set of automated messages sent when something happens, such as a welcome, a billing reminder or an abandoned cart. A campaign is a one-off message sent to a list on a chosen day.'],
  ['Sends and delivered', 'Sends is every message Klaviyo tried to send. Delivered is the ones that arrived (sends minus bounces).'],
  ['Open rate', 'Unique opens divided by delivered email. Text messages have no opens, so they are left out of this rate. It is approximate: Apple Mail Privacy Protection opens messages on its own, which inflates it, and people who open on two different days are counted twice when days are added up. Use it to compare messages, and read click rate first.'],
  ['Opens (views)', 'The number of people who opened, counted per day and added over the days in the window, so it reads a little high (about 20 percent for campaign email and 50 percent for flow email against Klaviyo\'s own single-window count).'],
  ['Click rate', 'Unique clicks divided by delivered, email and text messages together. Clicks that Klaviyo flags as made by bots are left out. It is approximate for the same added-over-days reason.'],
  ['Click to open', 'Email unique clicks divided by unique opens: of the people who opened, how many clicked.'],
  ['Revenue and orders (Klaviyo-attributed)', 'Orders and revenue that Klaviyo credits to a message, from its "Placed Order - Upstackified" feed, dated by the day the order happened (not the day the message was sent). Klaviyo also credits orders to an open without a click, so this revenue is not additive with Meta, Google or Upstack revenue. Never add it to ad platform revenue and never divide ad spend into it.'],
  ['Revenue per delivered', 'Klaviyo-attributed revenue divided by delivered messages. Orders per delivered is the same with orders.'],
  ['Unsubscribe rate', 'Unsubscribes divided by delivered. Klaviyo does not report text message unsubscribes for flows, and some campaign text message unsubscribes cannot be matched to a campaign, so text message unsubscribe figures are incomplete and flow text messages are left out of this rate.'],
  ['Every rate is a ratio of sums', 'A rate is the total on top divided by the total underneath over the whole window, never an average of daily rates.'],
  ['Partial periods', 'A week or month that runs past the last loaded day, or starts before the first loaded day, is marked partial and drawn lighter or dashed. It is not a drop.'],
  ['Recent days still move', 'The last loaded day is yesterday in Pacific time. Klaviyo records opens, clicks and orders late, so the last few days rise after the fact. The nightly job reloads the last 14 days.'],
  ['Copy', 'A message shows its latest copy only. When a message was edited, older results are read against the new text. Messages that were deleted from a flow have no copy.'],
];

function helpBlock() {
  return h('details', { class: 'eml-help', 'data-testid': 'eml-help' }, h('summary', null, 'Definitions and caveats'),
    h('dl', null, DEFS.flatMap(([t, body]) => [h('dt', null, t), h('dd', null, body)])));
}

function notesFor(d, s) {
  const out = [
    h('p', { 'data-testid': 'eml-note-revenue' }, h('strong', null, 'Klaviyo-attributed. '), 'Revenue and orders are what Klaviyo credits to messages, including orders credited to an open. They are not additive with Meta, Google or Upstack revenue, so do not add them or divide ad spend into them.'),
    h('p', { 'data-testid': 'eml-note-opens' }, 'Open rate is approximate: Apple Mail Privacy Protection inflates opens. Read click rate first.'),
  ];
  if (d.flags && d.flags.sms_unsub_incomplete) out.push(h('p', { 'data-testid': 'eml-note-sms' }, 'Text message (SMS) unsubscribes are incomplete: Klaviyo does not report them for flows and some campaign ones cannot be matched to a campaign, so the unsubscribe rate leaves out flow text messages.'));
  if (d.window) out.push(h('p', { class: 'muted', 'data-testid': 'eml-note-fresh' }, `Data through ${fmt.day(d.window.through)} (yesterday, Pacific time). The last few days still move because Klaviyo records events late; the nightly job reloads the last 14 days.`));
  return out;
}

// ---------------------------------------------------------------------------------------------------------------- chart

function chartConfig(d, metricKey, s) {
  const def = d.metrics.find((m) => m.key === metricKey);
  const kind = KIND_OF[def.kind];
  const bks = d.buckets;
  const labels = bks.map((b) => b.start);
  const val = (b, src) => (src ? b.split[src] : b.v)[metricKey];
  const isRate = def.kind === 'rate' || def.kind === 'money2';
  const left = { kind, title: def.label };
  const datasets = [];
  const partial = bks.map((b) => b.partial);
  if (isRate) {
    if (d.split) {
      datasets.push({ axis: 'left', type: 'line', label: 'Flows', data: bks.map((b) => val(b, 'flows')), color: BAR_COLOR, kind, partialDash: true });
      datasets.push({ axis: 'left', type: 'line', label: 'Campaigns', data: bks.map((b) => val(b, 'campaigns')), color: LINE_COLOR, kind, partialDash: true });
    } else datasets.push({ axis: 'left', type: 'line', label: def.label, data: bks.map((b) => val(b)), color: BAR_COLOR, kind, partialDash: true });
  } else if (d.split) {
    datasets.push({ axis: 'left', type: 'bar', label: 'Flows', data: bks.map((b) => val(b, 'flows')), color: BAR_COLOR, kind, stack: 'src' });
    datasets.push({ axis: 'left', type: 'bar', label: 'Campaigns', data: bks.map((b) => val(b, 'campaigns')), color: LINE_COLOR, kind, stack: 'src' });
  } else {
    datasets.push({ axis: 'left', type: 'bar', label: 'Complete periods', data: bks.map((b) => (b.partial ? null : val(b))), color: BAR_COLOR, kind });
    datasets.push({ axis: 'left', type: 'bar', label: 'Partial period', data: bks.map((b) => (b.partial ? val(b) : null)), color: '#d9822b', kind });
  }
  return { labels, grain: d.grain, partial, left, right: null, datasets, stackedBars: !!d.split && !isRate };
}

function chartSection(p0, d0, load) {
  let s = stateOf(p0);
  let d = d0;
  const canvas = h('canvas', { id: 'eml-chart', role: 'img', 'aria-label': 'Email over time' });
  const title = h('h2', { 'data-testid': 'eml-chart-title' });
  const note = h('span', { class: 'note', 'data-testid': 'eml-chart-note' });
  const grainHolder = h('div', { class: 'eml-ctlslot' });
  const metricHolder = h('div', { class: 'eml-ctlslot' });
  const splitHolder = h('div', { class: 'eml-ctlslot' });
  const box = h('div', { class: 'chartbox trendbox' }, canvas);
  let chart = null;
  const sync = () => navigate(urlOf(s, {}, currentParams().message ? { message: currentParams().message } : {}), { replace: true, render: false, scroll: false });
  function draw() {
    const def = d.metrics.find((m) => m.key === s.metric);
    title.textContent = `${def.label} by ${s.grain}`;
    if (chart) { chart.destroy(); chart = null; }
    if (d.empty || !d.buckets.length) { note.textContent = 'Nothing to draw.'; return; }
    chart = trendChart(canvas, chartConfig({ ...d, split: d.split }, s.metric, s));
    const parts = d.buckets.filter((b) => b.partial);
    note.textContent = parts.length
      ? `${parts.map((b) => b.sub ? `${b.label} (${b.sub})` : b.label).join(', ')} ${parts.length > 1 ? 'are' : 'is'} partial: ${parts.length > 1 ? 'they run' : 'it runs'} past the loaded days or start before them, so ${parts.length > 1 ? 'they are' : 'it is'} still filling in, not a drop. Opens (views) are people per day added over days.`
      : 'Every period shown is complete. Opens (views) are people per day added over days.';
  }
  function controls() {
    grainHolder.replaceChildren(seg({ name: 'Group by', testid: 'eml-grain', value: s.grain, options: GRAINS.map(([k, t]) => [k, t, null, 'eml-grain-' + k]),
      onChange: (k) => { s = { ...s, grain: k }; controls(); sync(); reload(); } }));
    metricHolder.replaceChildren(selectCtl({ name: 'Measure', testid: 'eml-metric', value: s.metric, options: CHART_METRICS, onChange: (k) => { s = { ...s, metric: k }; sync(); draw(); } }));
    const can = s.kind === 'all';
    const cb = checkCtl({ label: 'Split flows and campaigns', checked: s.split && can, testid: 'eml-split', onChange: (v) => { s = { ...s, split: v }; sync(); reload(); } });
    if (!can) { cb.querySelector('input').disabled = true; cb.title = 'Choose All above to split flows from campaigns.'; }
    splitHolder.replaceChildren(cb);
  }
  async function reload() {
    box.setAttribute('aria-busy', 'true');
    try { d = await load(s); } finally { box.removeAttribute('aria-busy'); }
    draw();
  }
  controls();
  draw();
  const el = h('section', { class: 'trend eml-chart', 'data-testid': 'eml-chart-panel' },
    h('div', { class: 'trend-head' }, title),
    h('div', { class: 'ctl-row pagefilters' }, grainHolder, metricHolder, splitHolder),
    box, h('div', { class: 'trend-note' }, note));
  return el;
}

// ---------------------------------------------------------------------------------------------------------------- tables

function metricCols(defs, sortOf) {
  const col = (key, label, title) => ({ key, label, num: true, title: title || defs[key].help, sort: (r) => sortOf(r, key), cell: (r) => fmtValue(KIND_OF[defs[key].kind], r.m[key]),
    total: (t) => fmtValue(KIND_OF[defs[key].kind], t[key]) });
  return [col('sends', 'Sends'), col('delivered', 'Delivered'), col('open_rate', 'Open rate (approx.)'), col('click_rate', 'Click rate (approx.)'), col('orders', 'Orders'),
    col('revenue', 'Revenue (Klaviyo-attributed)'), col('rev_per_delivered', 'Revenue per delivered')];
}

const nameCell = (text, extra) => h('span', { class: 'eml-name', title: text }, text, extra || null);

function flowMessagesTable(rows, total, defs) {
  const head = ['Message', 'Channel', 'Sends', 'Delivered', 'Open rate (approx.)', 'Click rate (approx.)', 'Orders', 'Revenue (Klaviyo-attributed)', 'Revenue per delivered'];
  const num = (i) => i >= 2;
  const cells = (r) => [r.subject || r.name || r.id, r.channel === 'sms' ? 'Text message' : 'Email', ...['sends', 'delivered', 'open_rate', 'click_rate', 'orders', 'revenue', 'rev_per_delivered'].map((k) => fmtValue(KIND_OF[defs[k].kind], r.m[k]))];
  const tbody = h('tbody');
  for (const r of rows) {
    const tr = h('tr', { class: 'clickable', tabindex: '0', 'data-testid': 'eml-msg-row', 'data-message': r.id, title: r.has_copy ? 'Open this message and its copy' : 'Open this message (copy not available)' });
    cells(r).forEach((c, i) => tr.append(h('td', { class: num(i) ? 'num' : '' }, i === 0 ? nameCell(c, r.has_copy ? null : h('span', { class: 'badge muted-badge eml-nocopy' }, 'copy not available')) : c)));
    tbody.append(tr);
  }
  if (!rows.length) tbody.append(h('tr', null, h('td', { colspan: String(head.length), class: 'eml-empty' }, 'No messages with activity in this window.')));
  return h('div', { class: 'tablewrap eml-sub' }, h('table', { class: 'dt eml-grid', 'data-testid': 'eml-msg-table' }, h('thead', h('tr', head.map((x, i) => h('th', { class: num(i) ? 'num' : '' }, x)))), tbody));
}

function flowsSection(d, s, onOpen) {
  const defs = Object.fromEntries(d.metrics.map((m) => [m.key, m]));
  const rows = d.rows.map((r) => ({ ...r, share_v: r.share }));
  const cols = [
    { key: 'name', label: 'Flow', sticky: true, cls: 'eml-namecol', sort: (r) => (r.name || '').toLowerCase(), cell: (r) => nameCell(r.name, r.status && r.status !== 'live' ? h('span', { class: 'badge muted-badge eml-status' }, r.status) : null), total: () => 'Total' },
    { key: 'messages', label: 'Messages', num: true, title: 'Messages with activity in this window', sort: (r) => r.messages, cell: (r) => fmt.int(r.messages), total: () => '' },
    ...metricCols(defs, (r, k) => r.m[k]),
    { key: 'share', label: 'Share of flow revenue', num: true, title: 'This flow\'s Klaviyo-attributed revenue divided by the revenue of all flows shown', sort: (r) => r.share, cell: (r) => (isNum(r.share) ? (r.share * 100).toFixed(1) + '%' : '–'), total: () => (d.total.revenue ? '100.0%' : '–') },
  ];
  const tbl = createTable({ columns: cols, rows, total: d.total, sortKey: 'revenue', sortDir: 'desc', testid: 'eml-flows-table', tableClass: 'eml-grid', rowKey: (r) => r.id, emptyText: 'No flow activity in this window.',
    rowClickable: () => true, onRowClick: (r, e, x) => x.toggle(), rowLabel: (r) => r.name, expand: { render: async (r) => {
      const m = await api(`/api/email/flows/${encodeURIComponent(r.id)}/messages`, apiParams(s));
      const node = flowMessagesTable(m.rows, m.total, defs);
      node.addEventListener('click', (e) => { const tr = e.target.closest('tr[data-message]'); if (tr) onOpen(tr.dataset.message); });
      node.addEventListener('keydown', (e) => { const tr = e.target.closest('tr[data-message]'); if (tr && e.key === 'Enter') onOpen(tr.dataset.message); });
      return node;
    }, }, expandLabel: 'Show this flow\'s messages' });
  // the whole row is the click target, the caret is a second way in; both open the flow's messages
  return h('section', { class: 'eml-section', 'data-testid': 'eml-flows' },
    h('h2', null, 'By flow'),
    h('p', { class: 'muted eml-lead' }, `Every flow with activity ${d.window.name === 'all' ? 'in the loaded days' : 'in the last ' + d.window.days + ' days'}, biggest revenue first. Click a flow to see its messages.`),
    tbl.el);
}

function campaignsSection(d, s, onOpen) {
  const defs = Object.fromEntries(d.metrics.map((m) => [m.key, m]));
  const cols = [
    { key: 'send_date', label: 'Send date', sort: (r) => r.send_date || '', firstDir: 'desc', title: 'The day the campaign was scheduled to send', cell: (r) => (r.send_date ? fmt.day(r.send_date) + ', ' + r.send_date.slice(0, 4) : '–'), total: () => 'Total' },
    { key: 'name', label: 'Campaign', sticky: true, cls: 'eml-namecol', sort: (r) => (r.name || '').toLowerCase(), cell: (r) => nameCell(r.name, r.sent_in_window ? null : h('span', { class: 'badge muted-badge eml-status', title: 'Sent before this window; the numbers are what happened in the window' }, 'sent earlier')), total: () => '' },
    { key: 'subject', label: 'Subject', sort: (r) => (r.subject || '').toLowerCase(), cell: (r) => (r.subject ? nameCell(r.subject) : h('span', { class: 'muted' }, '–')), total: () => '' },
    { key: 'channel', label: 'Channel', sort: (r) => r.channel, cell: (r) => (r.channel === 'sms' ? 'Text message' : 'Email'), total: () => '' },
    ...metricCols(defs, (r, k) => r.m[k]),
  ];
  let q = '';
  const count = h('span', { class: 'muted eml-count', 'data-testid': 'eml-campaign-count' });
  const tbl = createTable({ columns: cols, rows: d.rows, total: d.total, sortKey: 'send_date', sortDir: 'desc', testid: 'eml-campaigns-table', tableClass: 'eml-grid', rowKey: (r) => r.id,
    emptyText: 'No campaigns with activity in this window.', rowClickable: (r) => !!r.message_id, onRowClick: (r) => onOpen(r.message_id),
    rowLabel: (r) => r.name, onCount: (n, all) => { count.textContent = n === all ? `${all} campaigns` : `${n} of ${all} campaigns`; } });
  const search = searchCtl({ name: 'Search', placeholder: 'Campaign name or subject', testid: 'eml-campaign-search', onInput: (v) => {
    q = v.trim().toLowerCase();
    tbl.setFilter(q ? (r) => (r.name || '').toLowerCase().includes(q) || (r.subject || '').toLowerCase().includes(q) : null);
  } });
  return h('section', { class: 'eml-section', 'data-testid': 'eml-campaigns' },
    h('h2', null, 'By campaign'),
    h('p', { class: 'muted eml-lead' }, 'Numbers are what happened in the window, so a campaign sent just before it shows only its later opens, clicks and orders. Click a campaign to read it.'),
    h('div', { class: 'ctl-row pagefilters' }, search, count), tbl.el);
}

// ---------------------------------------------------------------------------------------------------------------- one message

const BODY_PREVIEW = 600;

function messageDialog(id, s) {
  const holder = h('div', { class: 'eml-detail', 'data-testid': 'eml-detail' }, h('p', { class: 'muted' }, 'Loading…'));
  let chart = null;
  const modal = openModal({ label: 'Message detail', className: 'eml-modal', build: (box) => box.append(holder), onClose: () => {
    if (chart) chart.destroy();
    const p = currentParams();
    if (p.message) { delete p.message; navigate(href('/email', p), { replace: true, render: false, scroll: false }); }
  } });
  let scope = 'all';                                  // a message's own story, not just the page's window: a campaign sent in August would read zero in the last 30 days
  const show = async () => {
    let d;
    if (chart) { chart.destroy(); chart = null; }
    try { d = await api(`/api/email/message/${encodeURIComponent(id)}`, { window: scope === 'all' ? 'all' : s.window }); } catch (e) {
      holder.replaceChildren(h('p', { class: 'err', 'data-testid': 'eml-detail-error' }, e && e.message === 'message not found' ? 'This message is not in the loaded data.' : `Could not load this message: ${e && e.message ? e.message : e}`));
      return;
    }
    const m = d.message;
    const defs = Object.fromEntries(d.metrics.map((x) => [x.key, x]));
    const sourceText = m.source === 'flow' ? 'Flow' : 'Campaign';
    const tiles = ['sends', 'delivered', 'open_rate', 'click_rate', 'orders', 'revenue', 'rev_per_delivered'].map((k) => tile(defs[k], d.totals[k], null, true));
    // copy
    const copy = [];
    if (m.has_copy) {
      let open = false;
      const body = h('p', { class: 'eml-body', 'data-testid': 'eml-body' });
      const toggle = h('button', { type: 'button', class: 'linkbtn', 'data-testid': 'eml-body-toggle', 'aria-expanded': 'false' });
      const paint = () => {
        body.textContent = open || m.body_chars <= BODY_PREVIEW ? m.body_text : m.body_text.slice(0, BODY_PREVIEW).trimEnd() + '...';
        toggle.textContent = open ? 'Show less' : `Show all ${fmt.int(m.body_chars)} characters`;
        toggle.setAttribute('aria-expanded', String(open));
      };
      toggle.addEventListener('click', () => { open = !open; paint(); });
      paint();
      copy.push(h('div', { class: 'eml-copyfacts muted' }, [m.link_count != null ? `${m.link_count} link${m.link_count === 1 ? '' : 's'}` : null, m.image_count != null ? `${m.image_count} image${m.image_count === 1 ? '' : 's'}` : null].filter(Boolean).join(' · ')), body, m.body_chars > BODY_PREVIEW ? toggle : null);
    } else copy.push(h('p', { class: 'eml-nocopy-text', 'data-testid': 'eml-nocopy' }, m.copy_fetched_at ? 'This message has no readable text (it is mostly images).' : 'Copy not available.',
      m.copy_fetched_at ? '' : ' Klaviyo no longer returns this message, for example because it was deleted from the flow.'));
    const asOf = m.copy_fetched_at ? h('p', { class: 'muted eml-asof', 'data-testid': 'eml-asof' }, `Copy as of ${fmt.dateTime(m.copy_fetched_at)} (latest version only). Results above are read against this copy even if the message was edited later.`) : null;
    // the daily chart: one measure at a time
    let metric = 'sends';
    const canvas = h('canvas', { 'aria-label': 'Daily results of this message' });
    const mHolder = h('div', { class: 'eml-ctlslot' });
    const drawChart = () => {
      if (chart) chart.destroy();
      const labelOf = { sends: 'Sends', clicks: 'Unique clicks', revenue: 'Revenue (Klaviyo-attributed)' }[metric];
      const kind = metric === 'revenue' ? 'money' : 'int';
      chart = trendChart(canvas, { labels: d.series.map((x) => x.day), grain: 'day', partial: d.series.map(() => false), left: { kind, title: labelOf }, right: null,
        datasets: [{ axis: 'left', type: 'bar', label: labelOf, data: d.series.map((x) => x[metric]), color: BAR_COLOR, kind }] });
      mHolder.replaceChildren(seg({ name: 'Daily', testid: 'eml-detail-metric', value: metric, options: [['sends', 'Sends', null, 'eml-dm-sends'], ['clicks', 'Clicks', null, 'eml-dm-clicks'], ['revenue', 'Revenue', null, 'eml-dm-revenue']], onChange: (k) => { metric = k; drawChart(); } }));
    };
    const scopeCtl = s.window === 'all' ? null : seg({ name: 'Numbers for', testid: 'eml-detail-scope', value: scope, options: [['all', 'All loaded days', null, 'eml-scope-all'], ['page', WINDOWS.find((x) => x[0] === s.window)[1], null, 'eml-scope-page']],
      onChange: (k) => { scope = k; show(); } });
    holder.replaceChildren(...[
      h('h2', { class: 'eml-dtitle', 'data-testid': 'eml-detail-title' }, m.subject || m.name || m.parent_name || m.id),
      h('p', { class: 'muted eml-dsub' }, `${sourceText}${m.parent_name || m.parent_id ? ': ' + (m.parent_name || m.parent_id) : ''}`, m.name && m.source === 'flow' ? ` · ${m.name}` : '', ` · ${m.channel === 'sms' ? 'Text message' : 'Email'}`),
      m.subject ? h('p', { class: 'eml-subject', 'data-testid': 'eml-subject' }, h('strong', null, 'Subject: '), m.subject) : null,
      m.preview_text ? h('p', { class: 'eml-preview', 'data-testid': 'eml-preview' }, h('strong', null, 'Preview text: '), m.preview_text) : null,
      m.from_name ? h('p', { class: 'eml-from', 'data-testid': 'eml-from' }, h('strong', null, 'From name: '), m.from_name) : null,
      scopeCtl ? h('div', { class: 'ctl-row pagefilters' }, scopeCtl) : null,
      h('div', { class: 'kpis eml-dtiles' }, h('div', { class: 'kpi-row' }, tiles)),
      h('p', { class: 'muted eml-dwin', 'data-testid': 'eml-detail-window' }, `${dayRange(d.window.from, d.window.to)} (${d.window.days} days). Revenue is Klaviyo-attributed, not additive with ad platform revenue.`),
      h('div', { class: 'eml-copy' }, h('h3', null, 'Copy'), copy, asOf),
      h('div', { class: 'eml-dchart' }, h('div', { class: 'ctl-row pagefilters' }, mHolder), h('div', { class: 'chartbox eml-dbox' }, canvas)),
    ].filter(Boolean));   // replaceChildren turns a null into the text "null"
    drawChart();
  };
  show();
  return modal;
}

// ---------------------------------------------------------------------------------------------------------------- page

export async function emailPage(ctx) {
  const { params: p, signal } = ctx;
  showControls(false);                                  // the page has its own controls: the global range / stage / compare bar does not apply
  document.title = 'Email · Lonvera';
  const s = stateOf(p);
  const ap = apiParams(s);
  const tsParams = (st) => ({ ...apiParams(st), grain: st.grain, split: st.split && st.kind === 'all' ? 1 : null });
  const [sum, ts] = await Promise.all([api('/api/email/summary', ap, { signal }), api('/api/email/timeseries', tsParams(s), { signal })]);
  const needFlows = s.kind !== 'campaigns', needCamps = s.kind !== 'flows';
  const [fl, cp] = sum.empty ? [null, null] : await Promise.all([needFlows ? api('/api/email/flows', ap, { signal }) : null, needCamps ? api('/api/email/campaigns', ap, { signal }) : null]);
  if (!ctx.alive()) return;

  const link = (key, list, title) => seg({ name: title, testid: 'eml-ctl-' + key, value: s[key], href: (v) => urlOf(s, { [key]: v }),
    options: list.map(([k, t, ti]) => [k, t, ti || null, `eml-${key}-${k}`]) });
  const controls = h('div', { class: 'eml-controls', 'data-testid': 'eml-controls' }, filterRow(link('window', WINDOWS, 'Window'), link('kind', KINDS, 'Show'), link('channel', CHANNELS, 'Channel')));

  const head = pageHeader({ title: 'Email', testid: 'eml-head', description: 'Klaviyo email and text message (SMS) results by flow, by campaign and over time. Revenue is what Klaviyo credits to messages, not new money on top of ad platform revenue.' });
  const parts = [head, controls];
  if (sum.empty) {
    parts.push(h('p', { class: 'eml-empty', 'data-testid': 'eml-empty' }, sum.note));
    ctx.mount(h('div', { class: 'page eml-page', 'data-testid': 'eml-page' }, parts));
    return;
  }
  const defs = Object.fromEntries(sum.metrics.map((m) => [m.key, m]));
  const w = sum.window;
  const winLine = h('p', { class: 'muted eml-winline', 'data-testid': 'eml-window' },
    `${dayRange(w.from, w.to)} (${w.days} days)`, w.prior ? `, compared with ${dayRange(w.prior.from, w.prior.to)}` : '. No earlier period to compare with: ' + (w.prior_why || ''));
  const strip = h('section', { class: 'kpis eml-kpis', 'data-testid': 'eml-totals', 'aria-label': 'Totals for the window' }, h('div', { class: 'kpi-row' }, sum.tiles.map((k) => tile(defs[k], sum.totals[k], sum))));
  const onOpen = (id) => {
    navigate(urlOf(s, {}, { message: id }), { replace: true, render: false, scroll: false });
    messageDialog(id, s);
  };
  const load = (st) => api('/api/email/timeseries', tsParams(st));
  const chart = chartSection(p, ts, load);
  parts.push(winLine, strip, h('div', { class: 'eml-notes', 'data-testid': 'eml-notes' }, notesFor(sum, s)), helpBlock(), chart);
  if (fl) parts.push(flowsSection(fl, s, onOpen));
  if (cp) parts.push(campaignsSection(cp, s, onOpen));
  ctx.mount(h('div', { class: 'page eml-page', 'data-testid': 'eml-page' }, parts));
  if (p.message) messageDialog(p.message, s);
}
