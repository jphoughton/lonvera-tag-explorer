// Chart.js (vendored, global `Chart`) wrappers.
import { fmt } from './util.js';

const instances = new Set();
export function destroyCharts() {
  for (const c of instances) c.destroy();
  instances.clear();
}

const COLORS = {
  spend: '#9db7e0', purchases: '#d9822b', revenue: '#1d8a56',
  impressions: '#8a6bbf', clicks: '#c2410c',
  testing: '#1b9a96', scaling: '#5a4fd6', unclassified: '#b4bcc8',
};

// shades category ranges: opts.shades = [{i0, i1, color}]
const windowShade = {
  id: 'windowShade',
  beforeDatasetsDraw(chart, _args, opts) {
    if (!opts || !opts.shades || !opts.shades.length) return;
    const { ctx, chartArea, scales } = chart;
    const x = scales.x;
    if (!x || !chartArea) return;
    const step = chart.data.labels.length > 1 ? x.getPixelForValue(1) - x.getPixelForValue(0) : chartArea.width;
    for (const sh of opts.shades) {
      if (sh.i0 == null || sh.i1 == null) continue;
      const x0 = Math.max(chartArea.left, x.getPixelForValue(sh.i0) - step / 2);
      const x1 = Math.min(chartArea.right, x.getPixelForValue(sh.i1) + step / 2);
      ctx.save();
      ctx.fillStyle = sh.color || 'rgba(26,95,180,0.10)';
      ctx.fillRect(x0, chartArea.top, Math.max(1, x1 - x0), chartArea.bottom - chartArea.top);
      ctx.restore();
    }
  },
};

function baseOptions() {
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    interaction: { mode: 'index', intersect: false },
    plugins: { legend: { display: false } },
  };
}
const tickDay = function (value) {
  const s = this.getLabelForValue(value);
  return s ? fmt.day(s) : s;
};
// 'YYYY-MM-01' -> 'Oct 2026' (long: 'October 2026')
export function monthLabel(t, long = false) {
  const m = /^(\d{4})-(\d{2})/.exec(t || '');
  if (!m) return t || '';
  return new Date(Date.UTC(+m[1], +m[2] - 1, 1)).toLocaleString('en-US', { month: long ? 'long' : 'short', year: 'numeric', timeZone: 'UTC' });
}

// full daily series; window A (blue) and, when comparing, window B (orange) are shaded. shades = [{from, to, color, name}]
export function dailyChart(canvas, daily, shades = []) {
  const labels = daily.map((d) => d.day);
  const idxGE = (day) => { const i = labels.findIndex((l) => l >= day); return i < 0 ? labels.length - 1 : i; };
  const idxLE = (day) => { let r = -1; labels.forEach((l, i) => { if (l <= day) r = i; }); return r < 0 ? 0 : r; };
  const sh = (shades || []).filter((x) => x && x.from && x.to).map((x) => ({ i0: idxGE(x.from), i1: idxLE(x.to), color: x.color, name: x.name }));
  const chart = new Chart(canvas, {
    data: {
      labels,
      datasets: [
        { type: 'bar', label: 'Spend', data: daily.map((d) => d.spend), backgroundColor: COLORS.spend, yAxisID: 'y', order: 3 },
        { type: 'line', label: 'Revenue', data: daily.map((d) => d.revenue), borderColor: COLORS.revenue, backgroundColor: COLORS.revenue, yAxisID: 'y', pointRadius: 0, borderWidth: 1.5, tension: 0.2, order: 2 },
        { type: 'line', label: 'Purchases', data: daily.map((d) => d.purchases), borderColor: COLORS.purchases, backgroundColor: COLORS.purchases, yAxisID: 'y1', pointRadius: 0, borderWidth: 1.5, tension: 0.2, order: 1 },
        // traffic: off by default; days where Meta has no impressions stay null (a gap, not zero)
        { type: 'line', label: 'Impressions', hidden: true, data: daily.map((d) => (d.impressions == null ? null : d.impressions)), borderColor: COLORS.impressions, backgroundColor: COLORS.impressions, yAxisID: 'y2', pointRadius: 0, borderWidth: 1.5, tension: 0.2, spanGaps: false, order: 1 },
        { type: 'line', label: 'Clicks', hidden: true, data: daily.map((d) => (d.link_clicks == null ? null : d.link_clicks)), borderColor: COLORS.clicks, backgroundColor: COLORS.clicks, yAxisID: 'y3', pointRadius: 0, borderWidth: 1.5, tension: 0.2, order: 1 },
      ],
    },
    options: {
      ...baseOptions(),
      scales: {
        x: { ticks: { maxTicksLimit: 14, maxRotation: 0, callback: tickDay }, grid: { display: false } },
        y: { position: 'left', beginAtZero: true, ticks: { callback: (v) => fmt.money(v) }, title: { display: true, text: '$' } },
        y1: { position: 'right', beginAtZero: true, grid: { drawOnChartArea: false }, title: { display: true, text: 'Purchases' } },
        y2: { display: 'auto', position: 'right', beginAtZero: true, grid: { drawOnChartArea: false }, title: { display: true, text: 'Impressions' }, ticks: { callback: (v) => fmt.int(v) } },
        y3: { display: 'auto', position: 'right', beginAtZero: true, grid: { drawOnChartArea: false }, title: { display: true, text: 'Link clicks' }, ticks: { callback: (v) => fmt.int(v) } },
      },
      plugins: {
        legend: { display: false },
        windowShade: { shades: sh },
        tooltip: {
          callbacks: {
            label: (c) => `${c.dataset.label}: ${['Purchases', 'Impressions', 'Clicks'].includes(c.dataset.label) ? fmt.int(c.parsed.y) : fmt.money2(c.parsed.y)}`,
          },
        },
      },
    },
    plugins: [windowShade],
  });
  instances.add(chart);
  return chart;
}

// stacked daily spend by stage
export function stageChart(canvas, rows) {
  const labels = rows.map((r) => r.day);
  const ds = (key, color) => ({ label: key[0].toUpperCase() + key.slice(1), data: rows.map((r) => r[key] || 0), backgroundColor: color, stack: 's' });
  const chart = new Chart(canvas, {
    type: 'bar',
    data: { labels, datasets: [ds('testing', COLORS.testing), ds('scaling', COLORS.scaling), ds('unclassified', COLORS.unclassified)] },
    options: {
      ...baseOptions(),
      scales: {
        x: { stacked: true, ticks: { maxTicksLimit: 14, maxRotation: 0, callback: tickDay }, grid: { display: false } },
        y: { stacked: true, beginAtZero: true, ticks: { callback: (v) => fmt.money(v) } },
      },
      plugins: {
        legend: { display: true, position: 'top', align: 'end', labels: { boxWidth: 10, boxHeight: 10 } },
        tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${fmt.money(c.parsed.y)}` } },
      },
    },
  });
  instances.add(chart);
  return chart;
}

// ---------- trend panel chart ----------
export function fmtMetric(kind, v) {
  if (v == null || !Number.isFinite(v)) return '–';
  switch (kind) {
    case 'money': return fmt.money(v);
    case 'money2': return fmt.money2(v);
    case 'roas': return fmt.roas(v);
    case 'rate': return fmt.rate(v);
    default: return fmt.int(v);
  }
}
const nfCompact = new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 });
function tickFmt(kind, v) {
  switch (kind) {
    case 'money': return '$' + nfCompact.format(v);
    case 'money2': return '$' + v.toFixed(2);
    case 'roas': return v.toFixed(2);
    case 'rate': return (v * 100).toFixed(1) + '%';
    default: return nfCompact.format(v);
  }
}
function hexA(hex, a) {
  const m = /^#([0-9a-f]{6})$/i.exec(hex || '');
  if (!m) return hex;
  const n = parseInt(m[1], 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
}
const daysInMonth = (iso) => { const [y, m] = iso.split('-').map(Number); return new Date(Date.UTC(y, m, 0)).getUTCDate(); };
const addDays = (iso, n) => { const [y, m, d] = iso.split('-').map(Number); return new Date(Date.UTC(y, m - 1, d + n)).toISOString().slice(0, 10); };

// category index range [i0, i1] of the buckets overlapping [from, to]
export function shadeIdx(labels, grain, from, to) {
  const end = (t) => (grain === 'week' ? addDays(t, 6) : grain === 'month' ? addDays(t, daysInMonth(t) - 1) : t);
  let i0 = labels.findIndex((t) => end(t) >= from);
  let i1 = -1;
  labels.forEach((t, i) => { if (t <= to) i1 = i; });
  if (i0 < 0) i0 = labels.length - 1;
  if (i1 < 0) i1 = 0;
  return [i0, Math.max(i0, i1)];
}

const BAR_COLOR = '#1a5fb4';
const LINE_COLOR = '#d9822b';
export { BAR_COLOR, LINE_COLOR };

/*
 Dual-axis chart: bars on the left axis, a line on the right axis.
 cfg: { labels:[t], grain, partial:[bool], shades:[{from,to,color}], left:{kind,title}|null, right:{kind,title}|null, stackedBars,
        datasets:[{axis:'left'|'right', type:'bar'|'line', label, data, color, fill?, stack?, dash?, outline?, width?, order?, cmpDates?, partialDash?}] }
*/
export function trendChart(canvas, cfg) {
  const { labels, grain } = cfg;
  const sh = (cfg.shades || []).filter((x) => x && x.from && x.to).map((x) => { const [i0, i1] = shadeIdx(labels, grain, x.from, x.to); return { i0, i1, color: x.color }; });
  const datasets = cfg.datasets.map((ds) => {
    const bar = ds.type === 'bar';
    const o = {
      type: ds.type, label: ds.label, data: ds.data, yAxisID: ds.axis === 'right' ? 'y1' : 'y',
      borderColor: ds.color, backgroundColor: ds.outline ? 'rgba(0,0,0,0)' : (bar ? hexA(ds.color, ds.opaque ? 0.85 : 0.45) : ds.color),
      borderWidth: ds.width || (bar ? 1 : 2.2), order: ds.order == null ? 1 : ds.order,
      _kind: ds.kind,
      _cmpDates: ds.cmpDates || null,
    };
    if (bar) {
      o.borderRadius = 0;
      if (ds.outline) { o.borderDash = ds.dash || [3, 3]; o.grouped = false; o.borderWidth = 1.2; }
      if (ds.stack) o.stack = ds.stack;
      else if (!ds.outline) o.stack = 'bars';
    } else {
      o.pointRadius = 0; o.pointHoverRadius = 3; o.tension = 0.12; o.spanGaps = false; o.borderDash = ds.dash;
      if (ds.partialDash && cfg.partial && cfg.partial.some(Boolean)) {
        o.segment = { borderDash: (c) => (cfg.partial[c.p1DataIndex] || cfg.partial[c.p0DataIndex] ? [3, 3] : undefined) };
      }
    }
    return o;
  });
  const axis = (side, a, color, position) => (a ? {
    position, stacked: side === 'left' && !!cfg.stackedBars, beginAtZero: !['roas', 'rate', 'money2'].includes(a.kind) || side === 'left',
    grid: side === 'left' ? undefined : { drawOnChartArea: false },
    border: { color },
    ticks: { color, callback: (v) => tickFmt(a.kind, v) },
    title: { display: true, text: a.title, color, font: { weight: '600' } },
  } : { display: false, position, grid: { drawOnChartArea: false } });
  const chart = new Chart(canvas, {
    type: 'bar',
    data: { labels, datasets },
    options: {
      ...baseOptions(),
      scales: {
        x: { ticks: { maxTicksLimit: 14, maxRotation: 0, callback: grain === 'month' ? function (v) { return monthLabel(this.getLabelForValue(v)); } : tickDay }, grid: { display: false } },
        y: axis('left', cfg.left, BAR_COLOR, 'left'),
        y1: axis('right', cfg.right, '#c2610f', 'right'),
      },
      plugins: {
        legend: { display: true, position: 'top', align: 'end', labels: { boxWidth: 10, boxHeight: 10 } },
        windowShade: { shades: sh },
        tooltip: {
          callbacks: {
            title: (items) => {
              if (!items.length) return '';
              const i = items[0].dataIndex;
              const base = grain === 'week' ? `Week of ${fmt.day(labels[i])}` : grain === 'month' ? monthLabel(labels[i], true) : fmt.day(labels[i]);
              return cfg.partial && cfg.partial[i] ? base + ' (partial)' : base;
            },
            label: (c) => {
              const cd = c.dataset._cmpDates;
              const bd = cd && cd[c.dataIndex] ? (grain === 'month' ? monthLabel(cd[c.dataIndex]) : fmt.day(cd[c.dataIndex])) : null;
              const name = bd ? `${c.dataset.label} (${bd})` : c.dataset.label;
              return `${name}: ${fmtMetric(c.dataset._kind, c.parsed.y)}`;
            },
          },
        },
      },
    },
    plugins: [windowShade],
  });
  instances.add(chart);
  return chart;
}
