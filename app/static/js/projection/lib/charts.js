// Projection UI: Chart.js (vendored, global `Chart`) helpers for month series.
// Actual months are solid; projected months are dashed (lines) or lighter (bars) and sit on a tinted band.
//   monthChart(canvas, {months, status, series, stacked, yFmt, y1Fmt, compare, legend})
//   lineChart / barChart / comboChart  = monthChart with a default series type
//   scenarioCompare(canvas, {months, status, baseline:{name,values}, others:[{name,values}], fmt})
//   destroyAll()  (the shell calls it on every navigation)
// series item: {label, values, type:'line'|'bar', axis:'y'|'y1', color, fmt, hidden, dashed, stack}
import * as F from './format.js';

export const PALETTE = ['#1a5fb4', '#d9822b', '#1d8a56', '#8a6bbf', '#c2410c', '#0b7a75', '#a02323', '#5b6676'];
const instances = new Set();

export function destroyAll() {
  for (const c of instances) c.destroy();
  instances.clear();
}
export function destroy(chart) {
  if (!chart) return;
  chart.destroy();
  instances.delete(chart);
}

const cssVar = (name, fallback) => {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
};
function alpha(hex, a) {
  const m = /^#([0-9a-f]{6})$/i.exec(hex || '');
  if (!m) return hex;
  const n = parseInt(m[1], 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
}

// tints everything from the first projected month on
const projectedShade = {
  id: 'projectedShade',
  beforeDatasetsDraw(chart, _a, opts) {
    if (!opts || opts.from == null) return;
    const { ctx, chartArea, scales } = chart;
    const xs = scales.x;
    if (!xs || !chartArea || chart.data.labels.length < 1) return;
    const step = chart.data.labels.length > 1 ? xs.getPixelForValue(1) - xs.getPixelForValue(0) : chartArea.width;
    const x0 = Math.max(chartArea.left, xs.getPixelForValue(opts.from) - step / 2);
    ctx.save();
    ctx.fillStyle = opts.color || 'rgba(26,95,180,0.07)';
    ctx.fillRect(x0, chartArea.top, chartArea.right - x0, chartArea.bottom - chartArea.top);
    ctx.restore();
  },
};

function build(canvas, config) {
  if (typeof Chart === 'undefined') throw new Error('Chart.js is not loaded');
  const chart = new Chart(canvas, { ...config, plugins: [...(config.plugins || []), projectedShade] });
  instances.add(chart);
  return chart;
}

const tickFmt = (fmt) => (v) => (fmt === 'pct' ? F.pct(v, 0) : fmt === 'x' ? F.x(v, 1) : fmt === 'num' ? F.compact(v, { prefix: '' }) : F.compact(v));

export function monthChart(canvas, opts) {
  const { months = [], status = [], series = [], stacked = false, yFmt = 'usd0', y1Fmt = 'num', compare = [], legend = true, title = null, defaultType = 'line' } = opts;
  const proj = months.map((_, i) => status[i] === 'projected');
  const firstProj = proj.indexOf(true);
  const muted = cssVar('--muted', '#5b6676');
  const grid = cssVar('--line-soft', '#e8ebf0');
  const labels = months.map((m) => F.monthLabel(m));
  const hasY1 = series.some((s) => s.axis === 'y1');

  const datasets = series.map((s, i) => {
    const color = s.color || PALETTE[i % PALETTE.length];
    const type = s.type || defaultType;
    const base = { label: s.label, data: s.values, type, yAxisID: s.axis || 'y', hidden: !!s.hidden, _fmt: s.fmt || (s.axis === 'y1' ? y1Fmt : yFmt), order: type === 'bar' ? 3 : 1 };
    if (type === 'bar') {
      return { ...base, backgroundColor: (c) => (proj[c.dataIndex] ? alpha(color, 0.45) : color), borderColor: color, borderWidth: (c) => (proj[c.dataIndex] ? 1 : 0), stack: stacked ? (s.stack || 's') : undefined };
    }
    return {
      ...base, borderColor: color, backgroundColor: color, borderWidth: 2, pointRadius: 0, pointHoverRadius: 3, tension: 0.15, spanGaps: true,
      borderDash: s.dashed ? [2, 3] : undefined,
      segment: { borderDash: (c) => (proj[c.p1DataIndex] ? [6, 4] : undefined) },
    };
  });
  compare.forEach((cmp, j) => {
    const color = cmp.color || PALETTE[(series.length + j + 1) % PALETTE.length];
    datasets.push({
      label: cmp.name || cmp.label, data: cmp.values, type: 'line', yAxisID: cmp.axis || 'y', borderColor: alpha(color, 0.9), backgroundColor: color,
      borderWidth: 1.5, pointRadius: 0, tension: 0.15, spanGaps: true, borderDash: [3, 3], order: 0, _fmt: cmp.fmt || yFmt,
    });
  });

  const scales = {
    x: { stacked, grid: { display: false }, ticks: { color: muted, maxRotation: 0, autoSkip: true, maxTicksLimit: 14 } },
    y: { stacked, position: 'left', grid: { color: grid }, ticks: { color: muted, callback: tickFmt(yFmt) }, beginAtZero: opts.beginAtZero !== false },
  };
  if (hasY1) scales.y1 = { position: 'right', grid: { drawOnChartArea: false }, ticks: { color: muted, callback: tickFmt(y1Fmt) }, beginAtZero: true };

  return build(canvas, {
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: { mode: 'index', intersect: false },
      scales,
      plugins: {
        legend: { display: legend, position: 'top', align: 'end', labels: { boxWidth: 10, boxHeight: 10, color: muted } },
        title: { display: !!title, text: title || '', color: muted, align: 'start', font: { size: 12, weight: '600' } },
        projectedShade: { from: firstProj < 0 ? null : firstProj },
        tooltip: {
          callbacks: {
            title: (items) => (items.length ? items[0].label + (proj[items[0].dataIndex] ? ' (projected)' : '') : ''),
            label: (c) => `${c.dataset.label}: ${F.value(c.parsed.y, c.dataset._fmt || yFmt)}`,
          },
        },
      },
    },
  });
}

export const lineChart = (canvas, opts) => monthChart(canvas, { ...opts, defaultType: 'line' });
export const barChart = (canvas, opts) => monthChart(canvas, { ...opts, defaultType: 'bar' });
export const comboChart = (canvas, opts) => monthChart(canvas, { ...opts });

// baseline scenario as a solid line, other scenarios as thin dotted overlays
export function scenarioCompare(canvas, { months, status, baseline, others = [], fmt = 'usd0', title = null }) {
  return monthChart(canvas, {
    months, status, yFmt: fmt, title,
    series: [{ label: baseline.name, values: baseline.values, type: 'line', color: PALETTE[0], fmt }],
    compare: others.map((o, i) => ({ name: o.name, values: o.values, color: PALETTE[(i + 1) % PALETTE.length], fmt })),
  });
}
