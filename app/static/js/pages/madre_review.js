// Lonvera, Madre review (/madre-review): a weekly deck for one creative vendor (default Madre), Meta-reported. The server (app/madre_review.py) decides the slides, the
// order and the copy; this file only draws them: fixed 16:9 slides scaled to fit, section jump, arrow keys / swipe, #s5 and #vita-bull anchors, and two side panels
// (an ad that plays, a ticket). Class names and test ids start with `mr-` (never ad-, ads-, sponsor: ad blockers hide those).
import { h, fmt, isNum, setParams, empty } from '../util.js';
import { api, ApiError } from '../api.js';
import { showControls } from '../controls.js';

const money = (v, d = 0) => (isNum(v) ? '$' + v.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d }) : 'n/a');
const num = (v) => (isNum(v) ? Math.round(v).toLocaleString('en-US') : 'n/a');
const pct = (v, d = 1) => (isNum(v) ? (v * 100).toFixed(d) + '%' : 'n/a');
const rat = (v) => (isNum(v) ? v.toFixed(2) + 'x' : 'n/a');
const FMT = { money: (v) => money(v), money2: (v) => money(v, 2), num, pct, rat };

const ROWS = {
  spend: ['Spend', (v) => money(v)], purchases: ['Purchases', num], cpa: ['CPA', (v) => money(v, 2)], roas: ['ROAS', rat], ctr: ['CTR', (v) => pct(v, 2)],
  cpc: ['CPC (link)', (v) => money(v, 2)], cvr: ['CVR', (v) => pct(v, 2)], cpm: ['CPM', (v) => money(v, 2)], aov: ['AOV', (v) => money(v, 2)], atc_rate: ['ATC rate', pct], atc_to_purchase: ['ATC to purchase', pct],
};
const LOWER_BETTER = new Set(['cpa', 'cpc', 'cpm']);
const NEUTRAL = new Set(['spend', 'purchases']);

function deltaClass(k, cur, prev) {
  if (!isNum(cur) || !isNum(prev) || NEUTRAL.has(k) || prev === 0) return '';
  const ch = (cur - prev) / Math.abs(prev);
  if (Math.abs(ch) < 0.05) return '';
  return ((LOWER_BETTER.has(k) ? ch < 0 : ch > 0)) ? 'mr-up' : 'mr-down';
}

function metricTable(t) {
  const tops = [];
  for (const c of t.cols) { if (tops.length && tops[tops.length - 1][0] === c.group) tops[tops.length - 1][1] += 1; else tops.push([c.group, 1]); }
  const head1 = h('tr', null, h('th'), tops.map(([g, n]) => h('th', { colspan: n, class: 'mr-grp' }, g)));
  const head2 = h('tr', null, h('th'), t.cols.map((c) => h('th', { class: 'mr-win' }, c.window)));
  const body = t.keys.map((k) => h('tr', null, h('td', { class: 'mr-lab' }, ROWS[k][0]),
    t.cols.map((c) => h('td', { class: 'mr-n ' + (c.p ? deltaClass(k, c.m[k], c.p[k]) : '') }, ROWS[k][1](c.m[k])))));
  return h('table', { class: 'mr-mt' }, h('thead', null, head1, head2), h('tbody', null, body));
}

export async function madreReviewPage(ctx) {
  const { params: p, signal } = ctx;
  showControls(false);
  document.title = 'Madre review · Lonvera';
  let d;
  try {
    d = await api('/api/madre-review', { week: p.week, vendor: p.vendor }, { signal });
  } catch (e) {
    if (!ctx.alive()) return;
    ctx.mount(h('div', { class: 'page mr-page', 'data-testid': 'mr-page' }, empty(e instanceof ApiError ? e.message : 'Could not load the review.')));
    return;
  }
  if (!ctx.alive()) return;
  if (d.empty || !d.slides.length) { ctx.mount(h('div', { class: 'page mr-page', 'data-testid': 'mr-page' }, empty(d.message || 'No data yet.'))); return; }

  // ------------------------------------------------------------------ slides
  const adTile = (key, big) => {
    const a = d.ads[key];
    const bad = !isNum(a.m.cpa) || (isNum(a.bench) && a.m.cpa > a.bench);
    const thumb = h('span', { class: 'mr-thumb' }, a.poster ? h('img', { src: a.poster, alt: '', loading: 'lazy' }) : h('span', { class: 'mr-noimg' }, a.product_name.slice(0, 2).toUpperCase()), h('span', { class: 'mr-play', 'aria-hidden': 'true' }));
    return h('button', { class: 'mr-tile', type: 'button', 'data-ad': key, 'data-testid': 'mr-tile' }, thumb,
      h('span', { class: 'mr-tmeta' }, h('span', { class: 'mr-tname' }, a.name), h('span', { class: 'mr-tline' }, `${a.product_name} · ${a.stage}`),
        h('span', { class: 'mr-tnums' }, h('b', { class: bad ? 'mr-bad' : 'mr-good' }, isNum(a.m.cpa) ? money(a.m.cpa) : 'no sales'), ` CPA · ${money(a.m.spend)} · ${num(a.m.purchases)} purchases`)));
  };
  const ck = (t) => h('span', { class: 'mr-ck' }, t);
  const foot = (s) => (s ? h('p', { class: 'mr-foot' }, s) : null);
  const askSegs = (segs) => segs.map((s) => (s.ad ? h('button', { class: 'mr-inl', type: 'button', 'data-ad': s.ad }, s.label) : s.t));

  const render = {
    cover: (s) => h('section', { class: 'mr-slide mr-cover' }, h('div', { class: 'mr-cv' }, ck(s.kicker || `${d.vendor} weekly review · Lonvera`), h('h1', null, s.title),
      h('div', { class: 'mr-big' }, s.big.map(([lab, v, f]) => h('div', null, h('b', null, FMT[f](v)), h('span', null, lab)))), h('p', { class: 'mr-sub' }, s.foot || ''))),
    week: (s) => body(s, h('div', { class: 'mr-cols3' }, s.cards.map((c) => h('div', { class: 'mr-card' }, ck(c.label), h('b', null, c.value), h('p', null, c.text)))),
      h('ul', { class: 'mr-list' }, s.bullets.map((b) => h('li', null, b)))),
    table: (s) => body(s, metricTable(s.table), foot(s.foot), s.vendors ? vendorTable(s.vendors) : null),
    ads: (s) => body(s, s.ads.length ? h('div', { class: 'mr-tiles' }, s.ads.map((k) => adTile(k))) : h('p', { class: 'mr-empty' }, s.empty), foot(s.foot)),
    pockets: (s) => body(s, pocketTable(s), foot(s.foot)),
    board: (s) => body(s, s.columns.length ? h('div', { class: 'mr-board', style: { gridTemplateColumns: `repeat(${s.columns.length}, minmax(0, 1fr))` } }, s.columns.map((c) => h('div', { class: 'mr-stcol' }, ck(c.label),
      c.tickets.map((k) => { const t = d.tickets[k]; return h('button', { class: 'mr-chip', type: 'button', 'data-ticket': k, 'data-testid': 'mr-chip' }, h('span', { class: 'mr-cpr' }, t.product_name), t.id.replace(/^Lonvera-/, '')); })))) : h('p', { class: 'mr-empty' }, s.empty), foot(s.foot)),
    suggest: (s) => body(s, s.cards.length ? h('div', { class: 'mr-cols3' }, s.cards.map(suggestCard)) : h('p', { class: 'mr-empty' }, s.empty), foot(s.foot)),
    asks: (s) => body(s, h('ol', { class: 'mr-asks' }, s.items.map((segs) => h('li', null, askSegs(segs))))),
    notes: (s) => body(s, h('ul', { class: 'mr-notes' }, s.bullets.map((b) => h('li', null, b)))),
  };
  render.section = render.cover;

  function body(s, ...kids) {
    return h('section', { class: 'mr-slide' }, h('header', null, ck(s.kicker), h('h2', null, s.title)), h('div', { class: 'mr-body' }, kids));
  }
  function vendorTable(rows) {
    return h('table', { class: 'mr-mt mr-vt' }, h('thead', null, h('tr', null, h('th'), ['Spend', 'Purchases', 'CPA', 'ROAS'].map((t) => h('th', { class: 'mr-win' }, t)))),
      h('tbody', null, rows.map((r) => h('tr', null, h('td', { class: 'mr-lab' }, r.vendor), h('td', { class: 'mr-n' }, money(r.m.spend)), h('td', { class: 'mr-n' }, num(r.m.purchases)), h('td', { class: 'mr-n' }, money(r.m.cpa, 2)), h('td', { class: 'mr-n' }, rat(r.m.roas))))));
  }
  function pocketTable(s) {
    if (!s.rows.length) return h('p', { class: 'mr-empty' }, 'No pockets with spend or open concepts.');
    return h('table', { class: 'mr-pt' }, h('thead', null, h('tr', null, h('th', null, 'Pocket'), ['Spend ' + s.window, 'CPA', 'ROAS', 'CVR', 'Open', 'Ready to launch', 'Launched 14d'].map((t) => h('th', { class: 'mr-win' }, t)), h('th'))),
      h('tbody', null, s.rows.map((r) => h('tr', { 'data-testid': 'mr-pocket' }, h('td', { class: 'mr-lab' }, h('span', { class: 'mr-pk' }, r.cluster), h('span', { class: 'mr-pki' }, `${r.icp} · ${r.pain}`)),
        h('td', { class: 'mr-n' }, money(r.m.spend)), h('td', { class: 'mr-n' }, isNum(r.m.cpa) ? money(r.m.cpa) : 'n/a'), h('td', { class: 'mr-n' }, r.m.spend ? rat(r.m.roas) : 'n/a'), h('td', { class: 'mr-n' }, pct(r.m.cvr, 2)),
        h('td', { class: 'mr-n' }, r.open), h('td', { class: 'mr-n' }, r.ready), h('td', { class: 'mr-n' }, r.launched), h('td', null, r.flag ? h('span', { class: 'mr-pill mr-pill-' + r.flag }, r.flag_label) : null)))));
  }
  function suggestCard(c) {
    const status = h('span', { class: 'mr-more', role: 'status' });
    const btn = h('button', { class: 'mr-send', type: 'button', 'data-testid': 'mr-send-idea' }, 'Send to Production as ideation');
    btn.addEventListener('click', async () => {
      btn.disabled = true; status.textContent = 'Sending...';
      try {
        const r = await api('/api/priorities/backlog', null, { method: 'POST', body: { items: [c.idea] } });
        status.textContent = (r.created && r.created.length) ? 'Added to Needs brief.' : ((r.skipped && r.skipped[0] && r.skipped[0].reason) || 'Already there.');
      } catch (e) { status.textContent = e && e.message ? e.message : 'Could not add it.'; btn.disabled = false; }
    });
    return h('div', { class: 'mr-card', 'data-testid': 'mr-suggest' }, ck(c.product_name), h('b', { class: 'mr-pt2' }, c.title), h('p', null, `${c.cluster}. ${c.why}`), btn, status);
  }

  // ------------------------------------------------------------------ shell: deck, bar, panel
  const deck = h('div', { class: 'mr-deck', 'data-testid': 'mr-deck' }, d.slides.map((s) => render[s.kind](s)));
  const slideEls = [...deck.children];
  const stage = h('div', { class: 'mr-stage' }, deck);
  const sectionOf = (s) => d.sections.find((x) => x.id === s.section) || { label: '' };
  const firstOf = (sec) => d.slides.findIndex((s) => s.section === sec);
  const count = h('span', { class: 'mr-count', 'data-testid': 'mr-count' });
  const dots = h('div', { class: 'mr-dots' }, d.slides.map((_, n) => h('i', { title: 'Slide ' + (n + 1), onclick: () => go(n) })));
  const secBtns = d.sections.filter((x) => firstOf(x.id) >= 0).map((x) => h('button', { type: 'button', class: 'mr-sec', 'data-sec': x.id, 'data-testid': 'mr-sec-' + x.id, onclick: () => go(firstOf(x.id)) }, x.label));
  const weekSel = h('select', { 'aria-label': 'Week', class: 'mr-week', 'data-testid': 'mr-week' }, d.weeks.map((w) => h('option', { value: w.start }, w.label)));
  weekSel.value = d.week.start;
  weekSel.addEventListener('change', () => setParams({ week: weekSel.value === d.default_week ? null : weekSel.value }));
  const bar = h('nav', { class: 'mr-bar', 'aria-label': 'Slides' },
    h('div', { class: 'mr-secs' }, secBtns), h('div', { class: 'mr-pager' }, h('button', { type: 'button', class: 'mr-nav', 'aria-label': 'Previous slide', 'data-testid': 'mr-prev', onclick: () => go(i - 1) }, '‹'), count,
      h('button', { type: 'button', class: 'mr-nav', 'aria-label': 'Next slide', 'data-testid': 'mr-next', onclick: () => go(i + 1) }, '›'), dots), h('div', { class: 'mr-meta' }, weekSel));
  const pc = h('div', { class: 'mr-pc' });
  const closeBtn = h('button', { type: 'button', class: 'mr-close', 'aria-label': 'Close', 'data-testid': 'mr-close' }, '×');
  const panel = h('aside', { class: 'mr-panel', hidden: true, role: 'dialog', 'aria-modal': 'true', 'data-testid': 'mr-panel' }, closeBtn, pc);
  const scrim = h('div', { class: 'mr-scrim', hidden: true });
  const head = h('div', { class: 'mr-head' }, h('h1', { class: 'mr-pagetitle' }, 'Madre review'), h('span', { class: 'mr-fresh', 'data-testid': 'mr-fresh' }, d.freshness));
  const wrap = h('div', { class: 'page mr-page', 'data-testid': 'mr-page' }, head, stage, bar, scrim, panel);
  ctx.mount(wrap);

  let i = 0;
  const anchorFor = (n) => { const s = d.slides[n]; return firstOf(s.section) === n && s.section !== 'overview' ? '#' + s.section : '#s' + (n + 1); };
  function go(n, write = true) {
    i = Math.max(0, Math.min(d.slides.length - 1, n));
    slideEls.forEach((el, k) => el.classList.toggle('mr-on', k === i));
    [...dots.children].forEach((el, k) => el.classList.toggle('mr-on', k === i));
    const s = d.slides[i];
    count.textContent = `${i + 1} / ${d.slides.length} · ${sectionOf(s).label}`;
    secBtns.forEach((b) => b.classList.toggle('mr-on', b.dataset.sec === s.section));
    if (write) { try { history.replaceState(history.state, '', location.pathname + location.search + anchorFor(i)); } catch (e) { /* ignore */ } }
  }
  function fromHash() {
    const hs = location.hash.replace(/^#/, '');
    const m = /^s(\d+)$/.exec(hs);
    if (m) return go(+m[1] - 1, false);
    const k = hs ? firstOf(hs) : -1;
    go(k >= 0 ? k : 0, false);
  }
  const fit = () => {
    const top = wrap.getBoundingClientRect().top + window.scrollY;
    const avail = Math.max(260, window.innerHeight - head.getBoundingClientRect().bottom - bar.offsetHeight - 24);
    stage.style.height = avail + 'px';
    const s = Math.min(stage.clientWidth / 1280, avail / 720);
    deck.style.transform = `translate(-50%, -50%) scale(${s})`;
    return top;
  };

  // panels
  const kv = (list) => h('div', { class: 'mr-kv' }, list.map(([k, v]) => h('div', null, h('span', null, k), h('b', null, v))));
  const tags = (list) => h('div', { class: 'mr-tags' }, list.filter(Boolean).map((t) => h('span', null, t)));
  const ext = (u, label) => (u && /^https?:\/\//i.test(u) ? h('a', { class: 'mr-ext', href: u, target: '_blank', rel: 'noopener noreferrer' }, label + ' ↗') : null);
  let lastFocus = null;
  function openPanel(...kids) { lastFocus = document.activeElement; pc.replaceChildren(...kids.filter(Boolean)); panel.hidden = false; scrim.hidden = false; closeBtn.focus(); }
  function closePanel() { const v = pc.querySelector('video'); if (v) { v.pause(); v.removeAttribute('src'); v.load(); } panel.hidden = true; scrim.hidden = true; if (lastFocus && lastFocus.focus) lastFocus.focus(); }
  closeBtn.addEventListener('click', closePanel); scrim.addEventListener('click', closePanel);

  function showAd(key) {
    const a = d.ads[key]; if (!a) return;
    const holder = h('div', { class: 'mr-media', 'data-testid': 'mr-media' });
    const fallback = () => holder.replaceChildren(a.poster ? h('img', { class: 'mr-full', src: a.poster, alt: '' }) : null, h('div', { class: 'mr-nomedia', 'data-testid': 'mr-nomedia' }, 'No preview for this ad. ', ext(a.asset, 'Open the asset') || 'No asset link on the tracker.'));
    const v = h('video', { controls: true, playsinline: true, autoplay: true, preload: 'auto', poster: a.poster || null, 'data-testid': 'mr-video' });
    v.addEventListener('error', fallback);
    v.src = a.video;                                     // only set when the panel opens: nothing is fetched before that
    holder.append(v);
    const m = a.m;
    openPanel(ck(`${a.product_name} · ${a.stage}`), h('h3', null, a.name), holder, a.hook ? h('p', { class: 'mr-hook' }, a.hook) : null,
      ck(`${d.week.label} · ${a.product_name} avg CPA ${money(a.bench, 2)}`),
      kv([['Spend', money(m.spend)], ['Purchases', num(m.purchases)], ['CPA', money(m.cpa, 2)], ['ROAS', rat(m.roas)], ['CTR', pct(m.ctr, 2)], ['CPC (link)', money(m.cpc, 2)], ['CPM', money(m.cpm, 2)], ['CVR', pct(m.cvr, 2)], ['AOV', money(m.aov, 2)]]),
      ck('Since May 6'), kv([['Spend', money(a.life.spend)], ['Purchases', num(a.life.purchases)], ['CPA', money(a.life.cpa, 2)]]), tags(a.tags), ext(a.asset, 'Open source asset'));
  }
  function showTicket(key) {
    const t = d.tickets[key]; if (!t) return;
    const told = t.told ? h('div', { class: 'mr-told', 'data-testid': 'mr-told' }, h('b', null, `${t.told.n} ads already launched in this pocket`),
      h('span', null, `Same product, market cluster and pain point, any vendor. ${money(t.told.spend)} spend, ${money(t.told.cpa, 2)} CPA.`),
      h('table', null, h('tbody', null, t.told.top.map((r) => h('tr', null, h('td', null, r.family + ' ', h('span', { class: 'mr-mute' }, r.vendor || 'Untagged')), h('td', null, `${money(r.spend)} · ${money(r.cpa)} CPA`))))))
      : h('div', { class: 'mr-told' }, h('b', null, 'No ad launched in this pocket yet.'));
    openPanel(ck(`${t.product_name} · ${t.vendor}`), h('h3', null, t.id), h('span', { class: 'mr-status' }, t.status), kv([['Format', t.format], ['Rows on tracker', String(t.variants)]]),
      tags([t.cluster, t.icp, t.pain]), told, ext(t.asset, 'Open asset'), h('a', { class: 'mr-ext', href: '/priorities/production' }, 'Open Creative Pipeline →'));
  }
  wrap.addEventListener('click', (e) => {
    const b = e.target.closest('[data-ad],[data-ticket]'); if (!b) return;
    if (b.dataset.ad) showAd(b.dataset.ad); else showTicket(b.dataset.ticket);
  });

  // keys, swipe, resize
  const onKey = (e) => {
    if (!wrap.isConnected) { document.removeEventListener('keydown', onKey); return; }
    if (!panel.hidden) { if (e.key === 'Escape') closePanel(); return; }
    if (/^(select|input|textarea)$/i.test((e.target && e.target.tagName) || '')) return;
    if (e.key === 'ArrowRight' || e.key === 'PageDown') { e.preventDefault(); go(i + 1); }
    if (e.key === 'ArrowLeft' || e.key === 'PageUp') { e.preventDefault(); go(i - 1); }
  };
  document.addEventListener('keydown', onKey);
  let x0 = null;
  stage.addEventListener('touchstart', (e) => { x0 = e.touches[0].clientX; }, { passive: true });
  stage.addEventListener('touchend', (e) => { if (x0 === null) return; const dx = e.changedTouches[0].clientX - x0; if (Math.abs(dx) > 50) go(i + (dx < 0 ? 1 : -1)); x0 = null; });
  const onResize = () => { if (!wrap.isConnected) { window.removeEventListener('resize', onResize); return; } fit(); };
  window.addEventListener('resize', onResize);
  const onHash = () => { if (!wrap.isConnected) { window.removeEventListener('hashchange', onHash); return; } fromHash(); };
  window.addEventListener('hashchange', onHash);
  fit(); fromHash(); fit();
}
