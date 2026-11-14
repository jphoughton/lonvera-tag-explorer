// Competitors page, learning layer: the Tags filter row, the "What competitors are doing" section (competitor share against Lonvera's share of spend, most copied combinations, landing page patterns),
// tagged landing page cards and their lightbox. Everything from the API goes in through textContent / h(). Class names and test ids start with `comp-` (ad blockers hide ad-, ads-, sponsor, banner, promo).
import { h, isNum, fmt } from '../util.js';
import { filterRow, selectCtl } from '../ui/pagefilters.js';
import { link, safeHttp, safeLpMedia, bars, tint } from './competitors_cards.js';

const pct = (v) => (isNum(v) ? Math.round(v * 100) + '%' : '–');
export const TAG_PARAMS = ['style', 'hook', 'device', 'offer'];
const TAG_NAMES = { style: 'Style', hook: 'Hook type', device: 'Device', offer: 'Offer type' };

/** The compact Tags row: four selects with only the values that exist this week, or the one "no tags yet" line. */
export function tagFilters(data, st, onChange, onClear) {
  const t = data.tags;
  if (!t) return null;
  const any = TAG_PARAMS.some((k) => st[k]);
  if (t.empty && !any) return h('p', { class: 'pri-sub comp-tags-empty', 'data-testid': 'comp-tags-empty' }, 'No tags yet. They fill in after the next tagging run.');
  const opts = t.options || {};
  const sels = TAG_PARAMS.filter((k) => (opts[k] || []).length || st[k]).map((k) => {
    const list = (opts[k] || []).map((o) => [o.value, `${o.value} (${o.count})`]);
    if (st[k] && !list.some(([v]) => v.toLowerCase() === st[k].toLowerCase())) list.unshift([st[k], st[k]]);
    const cur = (list.find(([v]) => v.toLowerCase() === (st[k] || '').toLowerCase()) || [''])[0];
    return selectCtl({ name: TAG_NAMES[k], options: [['', 'All'], ...list], value: cur, onChange: (v) => onChange(k, v), testid: 'comp-tag-' + k });
  });
  if (!sels.length) return null;
  const row = filterRow(h('span', { class: 'ctl-name comp-tags-lab' }, 'Tags'), ...sels,
    any ? h('button', { type: 'button', class: 'comp-bclose comp-tags-clear', 'data-testid': 'comp-tags-clear', onclick: onClear }, 'Clear tags') : null);
  row.setAttribute('data-testid', 'comp-tags');
  return row;
}

// ---------- learning section ----------
function dualBars(ch) {
  return h('ul', { class: 'comp-dbars', 'data-testid': 'comp-lbars-' + ch.key }, ch.rows.map((r) => h('li', { class: 'comp-dbar' + (r.hl ? ' comp-dbar-hl' : ''), 'data-hl': r.hl ? '1' : '0', 'data-value': r.value,
    title: `${r.ads} of ${ch.n} tagged ads` + (r.lonvera_share === null ? '' : `; Lonvera ${pct(r.lonvera_share)} of spend`) },
    h('span', { class: 'comp-bar-label' }, r.value),
    h('span', { class: 'comp-dbar-tracks' },
      h('span', { class: 'comp-bar-track' }, h('span', { class: 'comp-dbar-c', style: { width: Math.max(2, Math.round(r.share * 100)) + '%' } })),
      r.lonvera_share === null ? null : h('span', { class: 'comp-bar-track comp-dbar-lt' }, h('span', { class: 'comp-dbar-l', style: { width: Math.max(r.lonvera_share > 0 ? 2 : 0, Math.round(r.lonvera_share * 100)) + '%' } }))),
    h('span', { class: 'comp-bar-n' }, pct(r.share), r.lonvera_share === null ? null : h('small', null, pct(r.lonvera_share))))));
}

function chartCard(ch) {
  return h('article', { class: 'comp-lchart', 'data-testid': 'comp-lchart', 'data-key': ch.key },
    h('h3', null, ch.title), h('p', { class: 'comp-lsent', 'data-testid': 'comp-lsent' }, ch.sentence), dualBars(ch),
    ch.working && ch.working.sentence ? h('p', { class: 'comp-lwork', 'data-testid': 'comp-lwork', 'data-thin': ch.working.items.some((x) => x.thin) ? '1' : '0' }, h('strong', null, 'What is working for us. '), ch.working.sentence) : null,
    h('p', { class: 'pri-sub comp-lfoot' }, `${ch.n} tagged ads`, ch.has_lonvera ? '' : '. No Lonvera equivalent.'));
}

export function learning(data) {
  const L = data.learn, P = data.lp_patterns;
  if (!L && !P) return null;
  const kids = [];
  if (L) {
    kids.push(h('h2', null, 'What competitors are doing'),
      h('p', { class: 'pri-sub', 'data-testid': 'comp-lnote' }, `${L.tagged_ads} of ${L.total_ads} ads in this view are tagged. ${L.note}`),
      L.charts.some((c) => c.has_lonvera) ? h('p', { class: 'comp-legend', 'data-testid': 'comp-legend' },
        h('span', null, h('i', { class: 'comp-sw comp-sw-c' }), 'Competitors: share of tagged ads'), h('span', null, h('i', { class: 'comp-sw comp-sw-l' }), `Lonvera: share of spend, last 30 days${L.lonvera_through ? ' to ' + L.lonvera_through : ''}`),
        h('span', null, h('i', { class: 'comp-sw comp-sw-h' }), 'Competitors lean on it 15 points or more harder')) : null,
      L.charts.some((c) => c.working) ? h('p', { class: 'pri-sub comp-lwnote', 'data-testid': 'comp-lwnote' }, `What is working for us: NC ROAS is Upstack new customer revenue divided by spend (return on ad spend), last 30 days${L.best_through ? ' to ' + L.best_through : ''}, only values with at least $1,000 of spend. Thin sample means under $5,000 of spend or under 3 ad families.`) : null,
      h('div', { class: 'comp-lgrid', 'data-testid': 'comp-lgrid' }, L.charts.map(chartCard)));
    if (L.combos && L.combos.length) kids.push(h('div', { class: 'comp-combos-wrap' }, h('h3', null, 'Most copied this week'),
      h('p', { class: 'pri-sub' }, 'The hook and device pairings that show up most often across competitor ads.'),
      h('ol', { class: 'comp-combos', 'data-testid': 'comp-combos' }, L.combos.map((c) => {
        const u = safeHttp(c.example.ad_library_url);
        return h('li', { 'data-testid': 'comp-combo' }, h('strong', null, `${c.hook_type} + ${c.device}`), h('span', { class: 'pri-sub' }, ` ${c.ads} ad${c.ads === 1 ? '' : 's'} from ${c.brands} brand${c.brands === 1 ? '' : 's'}. `),
          u ? link(u, `Example: ${c.example.brand}`, { 'data-testid': 'comp-combo-link', title: c.example.hook }) : null);
      }))));
  }
  if (P && P.charts && P.charts.length) kids.push(h('div', { class: 'comp-lpat', 'data-testid': 'comp-lpat' }, h('h2', null, 'Landing pages'),
    h('p', { class: 'pri-sub' }, `${P.pages} landing page${P.pages === 1 ? '' : 's'} read this week. Counts are pages, not ads. Near-identical phrases are grouped only when they match exactly, ignoring capital letters.`),
    h('div', { class: 'comp-lgrid' }, P.charts.map((c) => h('article', { class: 'comp-lchart', 'data-testid': 'comp-lpchart', 'data-key': c.key }, h('h3', null, c.title),
      bars(c.rows.map((r) => ({ label: r.label, value: r.count, title: `${r.count} page${r.count === 1 ? '' : 's'}` })), 'comp-lpbars-' + c.key, 'pages'))))));
  return h('section', { class: 'comp-sec comp-learn', 'data-testid': 'comp-learn' }, kids);
}

/** Facts for the "What changed this week" box: landing pages whose content changed. */
export function lpChanges(data) {
  const c = data.lp_patterns && data.lp_patterns.changed;
  if (!c || !c.length) return null;
  return h('div', { 'data-testid': 'comp-lpchanged' }, h('h4', { class: 'comp-chgh' }, 'Landing pages that changed'),
    h('ul', null, c.map((x) => { const u = safeHttp(x.final_url); return h('li', { 'data-kind': 'lp', 'data-testid': 'comp-lpfact' }, `${x.brand}: ${x.change_note || 'page changed'} `, u ? link(u, 'Open page', { 'data-testid': 'comp-lpfactlink' }) : null); })));
}

// ---------- landing page cards ----------
function lpVisual(lp, big) {
  const src = safeLpMedia(lp.lp_thumb_url);
  const box = tint(h('div', { class: 'comp-vis comp-lp-vis' + (big ? ' comp-vis-big' : '') + (src ? ' comp-has-img' : ' comp-typo'), 'data-testid': src ? 'comp-lpthumb' : 'comp-lptypo' }), lp.brand);
  const typo = () => h('span', { class: 'comp-typo-text' + ((lp.hero_headline || '').length > 90 ? ' comp-long' : '') }, lp.hero_headline || lp.key_theme || lp.page_type || 'Landing page');
  if (src) {
    const img = h('img', { src, alt: `${lp.brand} landing page: ${lp.hero_headline || lp.key_theme || ''}`.slice(0, 140), loading: 'lazy', decoding: 'async' });
    img.addEventListener('error', () => { box.classList.remove('comp-has-img'); box.classList.add('comp-typo'); box.setAttribute('data-testid', 'comp-lptypo'); img.remove(); box.prepend(typo()); });
    box.append(img);
  } else box.append(typo());
  return box;
}
const chipList = (cls, list, testid) => (list && list.length ? h('span', { class: 'comp-lpchips', 'data-testid': testid }, list.map((x) => h('span', { class: cls }, x))) : null);
const offerLine = (lp) => [lp.offer_summary, lp.price_text, lp.guarantee].filter(Boolean).join(' · ');

export function lpCards(list, onOpen) {
  return h('div', { class: 'comp-lpgrid', 'data-testid': 'comp-lpgrid' }, list.map((lp) => {
    const u = safeHttp(lp.final_url);
    const offer = offerLine(lp);
    return h('article', { class: 'comp-lpcard', 'data-testid': 'comp-lpcard', 'data-lp': lp.lp_id },
      h('button', { type: 'button', class: 'comp-card-open comp-lpopen', 'data-testid': 'comp-lpopen', 'aria-label': `Open landing page summary: ${lp.hero_headline || lp.key_theme || lp.page_type || ''}`.slice(0, 120), onclick: () => onOpen(lp, list) },
        lpVisual(lp, false),
        h('span', { class: 'comp-lptop' }, lp.page_type ? h('span', { class: 'comp-lpchip', 'data-testid': 'comp-lptype' }, lp.page_type) : null,
          lp.changed ? h('span', { class: 'comp-lpchanged', 'data-testid': 'comp-lpchg', title: lp.change_note || '' }, 'Changed this week') : null),
        lp.changed && lp.change_note ? h('span', { class: 'pri-sub comp-lpnote' }, lp.change_note) : null,
        lp.key_theme ? h('span', { class: 'comp-lptheme', 'data-testid': 'comp-lptheme' }, lp.key_theme) : null,
        lp.selling_points.length ? h('ul', { class: 'comp-lppoints', 'data-testid': 'comp-lppoints' }, lp.selling_points.slice(0, 5).map((p) => h('li', null, p))) : null,
        offer ? h('span', { class: 'comp-lpoffer', 'data-testid': 'comp-lpoffer' }, offer) : null,
        chipList('comp-pchip', lp.proof_elements, 'comp-lpproof'), chipList('comp-pchip comp-pchip-u', lp.urgency_devices, 'comp-lpurgency')),
      h('div', { class: 'comp-card-foot' }, h('span', { class: 'comp-fmt', 'data-testid': 'comp-lpads' }, lp.own ? `${lp.ad_count} ad ${lp.ad_count === 1 ? 'family points' : 'families point'} here` : `${lp.ad_count} ad${lp.ad_count === 1 ? '' : 's'} point here`),
        u ? link(u, 'Open page', { class: 'comp-open', 'data-testid': 'comp-lplink' }) : null));
  }));
}

/** The landing page lightbox: same pattern as the ad lightbox (a <dialog>, previous / next, Esc, arrow keys) with the larger screenshot and every field. */
export function lpLightbox() {
  const dlg = h('dialog', { class: 'comp-lb', 'data-testid': 'comp-lplb', 'aria-label': 'Landing page detail', tabindex: '-1' });
  const stage = h('div', { class: 'comp-lb-stage' });
  const info = h('div', { class: 'comp-lb-info' });
  const count = h('span', { class: 'comp-lb-count', 'aria-live': 'polite' });
  const close = h('button', { type: 'button', class: 'comp-lb-close', 'data-testid': 'comp-lplb-close', 'aria-label': 'Close', onclick: () => dlg.close() }, '✕');
  const prev = h('button', { type: 'button', class: 'comp-lb-nav comp-lb-prev', 'aria-label': 'Previous page', onclick: () => go(-1) }, '‹');
  const next = h('button', { type: 'button', class: 'comp-lb-nav comp-lb-next', 'aria-label': 'Next page', onclick: () => go(1) }, '›');
  dlg.append(close, h('div', { class: 'comp-lb-body' }, stage, info), h('div', { class: 'comp-lb-bar' }, prev, count, next));
  let list = [], i = 0, from = null;
  const sec = (label, body) => (body ? h('div', { class: 'comp-lpsec' }, h('h4', null, label), body) : null);
  const bullets = (a) => (a && a.length ? h('ul', { class: 'comp-lppoints' }, a.map((x) => h('li', null, x))) : null);
  function show() {
    const lp = list[i];
    const u = safeHttp(lp.final_url);
    stage.replaceChildren(lpVisual(lp, true));
    info.replaceChildren(...[
      h('p', { class: 'comp-lb-brand' }, lp.brand, ' ', lp.page_type ? h('span', { class: 'comp-lpchip' }, lp.page_type) : null, lp.changed ? h('span', { class: 'comp-lpchanged' }, 'Changed this week') : null),
      lp.changed && lp.change_note ? h('p', { class: 'pri-sub' }, lp.change_note) : null,
      lp.hero_headline ? h('h2', { class: 'comp-lb-headline', 'data-testid': 'comp-lplb-headline' }, lp.hero_headline) : null,
      lp.key_theme ? h('p', { class: 'comp-lptheme', 'data-testid': 'comp-lplb-theme' }, lp.key_theme) : null,
      sec('Selling points', bullets(lp.selling_points)), sec('Offer', offerLine(lp) ? h('p', null, offerLine(lp)) : null),
      sec('Proof', chipList('comp-pchip', lp.proof_elements)), sec('Urgency', chipList('comp-pchip comp-pchip-u', lp.urgency_devices)),
      sec('Objections it answers', bullets(lp.objections)), sec('Call to action', lp.cta ? h('p', null, lp.cta) : null),
      sec('Why it works', lp.why_it_works ? h('p', { 'data-testid': 'comp-lplb-why' }, lp.why_it_works) : null),
      lp.angle ? h('p', { class: 'pri-sub' }, 'Angle: ', lp.angle) : null,
      h('p', { class: 'pri-sub' }, lp.own ? `${lp.ad_count} ad ${lp.ad_count === 1 ? 'family points' : 'families point'} to this page.` : `${lp.ad_count} ad${lp.ad_count === 1 ? '' : 's'} point to this page.`),
      u ? h('p', { class: 'comp-lb-links' }, link(u, 'Open page', { 'data-testid': 'comp-lplb-open' })) : null].filter(Boolean));
    count.textContent = `${i + 1} / ${list.length}`;
    prev.hidden = next.hidden = list.length < 2;
  }
  function go(d) { if (list.length < 2) return; i = (i + d + list.length) % list.length; show(); }
  function onKey(e) {
    if (!dlg.open || e.defaultPrevented || e.altKey || e.ctrlKey || e.metaKey) return;
    const tag = e.target && e.target.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
    if (e.key === 'ArrowLeft') { e.preventDefault(); go(-1); } else if (e.key === 'ArrowRight') { e.preventDefault(); go(1); }
  }
  dlg.addEventListener('click', (e) => { if (e.target === dlg) dlg.close(); });
  dlg.addEventListener('close', () => { document.removeEventListener('keydown', onKey); if (from && from.isConnected) from.focus(); });
  dlg.openAt = (lps, lp) => { list = lps; i = Math.max(0, lps.findIndex((x) => x.lp_id === lp.lp_id)); from = document.activeElement; show(); if (!dlg.open) dlg.showModal(); document.removeEventListener('keydown', onKey); document.addEventListener('keydown', onKey); };
  return dlg;
}

// ---------- our own landing pages ----------
const roasTxt = (v) => (isNum(v) ? v.toFixed(2) : '–');
function ownPerf(lp) {
  const fam = lp.families || [];
  if (!lp.ad_count) return h('p', { class: 'pri-sub comp-own-perf', 'data-testid': 'comp-own-perf' }, 'No ad family is linked to this page yet.');
  return h('div', { class: 'comp-own-perf', 'data-testid': 'comp-own-perf' },
    h('p', { class: 'comp-own-nums' }, `Last 30 days: ${fmt.money(lp.spend)} spend, NC ROAS ${roasTxt(lp.nc_roas)}, Meta ROAS ${roasTxt(lp.meta_roas)}`),
    fam.length ? h('ul', { class: 'comp-own-fams', 'data-testid': 'comp-own-fams' }, fam.map((f) => h('li', null, h('span', { class: 'comp-own-fn' }, f.name), ' ', h('span', { class: 'pri-sub' }, fmt.money(f.spend)))),
      lp.more_families ? h('li', { class: 'pri-sub' }, `and ${lp.more_families} more`) : null) : null);
}

/** "Our landing pages": the same cards as the competitor pages, with the ad families that point to each page and their last 30 days, and one-line comparisons against the competitor pages. Hidden when there are none. */
export function ownLandingPages(data, onOpen) {
  const o = data.own_lp;
  if (!o || !o.pages || !o.pages.length) return null;
  const grid = lpCards(o.pages, onOpen);
  const arts = grid.querySelectorAll('.comp-lpcard');
  o.pages.forEach((lp, i) => { if (arts[i]) { arts[i].setAttribute('data-testid', 'comp-own-card'); arts[i].append(ownPerf(lp)); } });
  return h('section', { class: 'comp-sec comp-own', 'data-testid': 'comp-own' }, h('h2', null, 'Our landing pages'),
    h('p', { class: 'pri-sub' }, `${o.pages.length} page${o.pages.length === 1 ? '' : 's'} read for the week of ${o.week}. NC ROAS is Upstack new customer revenue divided by spend (return on ad spend); Meta ROAS is Meta's own reporting. Ad family numbers are the last ${o.days} days${o.through ? ' to ' + o.through : ''}.`),
    o.compare && o.compare.length ? h('ul', { class: 'comp-own-cmp', 'data-testid': 'comp-own-compare' }, o.compare.map((t) => h('li', null, t))) : null, grid);
}
