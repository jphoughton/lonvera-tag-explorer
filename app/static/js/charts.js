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
  testing: '#7a5ad8', scaling: '#1b9a96', unclassified: '#b4bcc8',
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
