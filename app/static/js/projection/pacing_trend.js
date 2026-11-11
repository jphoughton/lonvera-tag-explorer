// The Lonvera "Trend" panel (app/static/js/trend.js), mounted at the top of the Pacing page.
// Same component and same /api/platforms/trend endpoint as the Overview page; here it has no page filters, so it starts at
// since launch, Overview, Bars = Spend, Line = NC ROAS (total), Grain = Day, Split = Total, Numbers = Own reporting.
// Hide / Show is remembered in its own localStorage key (the Marketing pages keep theirs). Styles come from /static/app.css (loaded by projection.html).
import { createTrendPanel, platformTrendSource } from '../trend.js';

const KEY = 'lv.projection.trend.collapsed';
const store = {
  get() { try { return localStorage.getItem(KEY) === '1'; } catch { return false; } },
  set(on) { try { localStorage.setItem(KEY, on ? '1' : '0'); } catch { /* ignore */ } },
};

// the page's own gap spaces the blocks; tighter control gaps keep Hide on the first row at 1,440 px
const CSS = `.pg-trendpanel { margin: 0; }
.pg-trendpanel .trend-controls { gap: 6px 10px; }
.pg-trendpanel .trend-head { flex-wrap: wrap; }
.pg-trendpanel .trendbox { max-width: 100%; }`;
function injectCss() {
  if (document.getElementById('pg-trendpanel-css')) return;
  const st = document.createElement('style');
  st.id = 'pg-trendpanel-css';
  st.textContent = CSS;
  document.head.append(st);
}

export function pacingTrendPanel() {
  injectCss();
  const ac = new AbortController();
  const panel = createTrendPanel({
    p: {}, mode: 'source', local: true, collapseStore: store, signal: ac.signal,
    source: platformTrendSource({}, {}, {}, { basis: true }),
  });
  panel.el.classList.add('pg-trendpanel');
  panel.load();
  return { el: panel.el, destroy() { ac.abort(); panel.destroy(); } };
}
