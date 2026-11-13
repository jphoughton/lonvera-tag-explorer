// Competitors page parts: ad cards (a picture when one is stored, otherwise a typographic card), the lightbox, and the plain CSS bar charts.
// Everything from the API goes in through textContent / h(); links are h('a') with an http(s) check. Class names and test ids start with `comp-` (ad blockers hide ad-, ads-, sponsor, banner, promo).
import { h, isNum } from '../util.js';

export const safeHttp = (u) => { try { const x = new URL(u, location.origin); return x.protocol === 'https:' || x.protocol === 'http:' ? x.href : null; } catch (e) { return null; } };
/** A landing page link; the stored URL can lack its scheme (acme.com/offer), so one is added before the http(s) check. */
export const landing = (u) => { const t = String(u || '').trim(); return t ? safeHttp(/^[a-z][a-z0-9+.-]*:/i.test(t) ? t : 'https://' + t.replace(/^\/\//, '')) : null; };
export const safeMedia = (u) => (typeof u === 'string' && u.startsWith('/api/competitors/media/') ? u : null);

export function link(url, text, attrs) { return h('a', { href: url, target: '_blank', rel: 'noopener noreferrer', ...(attrs || {}) }, text); }

/** A stable tint per brand: the same brand always gets the same colour, so typographic cards group by brand at a glance. */
export function tint(el, brand) {
  let x = 0;
  for (const c of String(brand || '')) x = (x * 31 + c.charCodeAt(0)) >>> 0;
  const hue = x % 360;
  el.style.setProperty('--tint-bg', `hsl(${hue} 46% 90%)`);
  el.style.setProperty('--tint-ink', `hsl(${hue} 50% 15%)`);
  el.style.setProperty('--tint-edge', `hsl(${hue} 52% 42%)`);
  return el;
}

const FORMAT_LABEL = { video: 'Video', image: 'Image' };
export const formatBadge = (f) => h('span', { class: 'comp-fmt', 'data-format': f || 'image' }, FORMAT_LABEL[f] || 'Image');

/** The visual: the stored thumbnail, or the hook set large on a tinted block. big = lightbox size. */
export function visual(ad, big) {
  const src = safeMedia(ad.thumb_url);
  const box = tint(h('div', { class: 'comp-vis' + (big ? ' comp-vis-big' : '') + (src ? ' comp-has-img' : ' comp-typo'), 'data-testid': src ? 'comp-thumb' : 'comp-typo' }), ad.brand);
  if (src) {
    const img = h('img', { src, alt: `${ad.brand} ad: ${ad.hook || 'no text'}`.slice(0, 140), loading: 'lazy', decoding: 'async', draggable: 'false' });
    img.addEventListener('error', () => { box.classList.remove('comp-has-img'); box.classList.add('comp-typo'); box.setAttribute('data-testid', 'comp-typo'); img.remove(); box.prepend(typoText(ad)); });
    box.append(img);
  } else box.append(typoText(ad));
  box.append(h('span', { class: 'comp-vis-badge' }, ad.format === 'video' ? '▶ Video' : 'Image'));
  return box;
}
function typoText(ad) {
  const t = (ad.hook || ad.headline || '').trim();
  return h('span', { class: 'comp-typo-text' + (t.length > 90 ? ' comp-long' : '') }, t || 'No text on this ad');
}

/** A card: the picture area opens the lightbox; "Open ad" goes to the Ad Library. */
export function adCard(ad, onOpen, opts) {
  const o = opts || {};
  const u = safeHttp(ad.ad_library_url);
  const showHook = !!safeMedia(ad.thumb_url);
  return h('article', { class: 'comp-card' + (o.rail ? ' comp-card-rail' : ''), 'data-testid': o.testid || 'comp-card', 'data-id': ad.library_id, 'data-reason': ad.reason || null },
    h('button', { type: 'button', class: 'comp-card-open', 'aria-label': `Open ${ad.brand} ad: ${(ad.hook || '').slice(0, 80)}`, 'data-testid': 'comp-card-open', onclick: () => onOpen(ad) },
      visual(ad, false),
      h('span', { class: 'comp-card-meta' },
        h('span', { class: 'comp-card-brand' }, ad.brand),
        ad.badge ? h('span', { class: 'comp-badge', 'data-reason': ad.reason || '' }, ad.badge) : null,
        showHook ? h('span', { class: 'comp-hook2' }, ad.hook || ad.headline || '') : null)),
    h('div', { class: 'comp-card-foot' }, formatBadge(ad.format), u ? link(u, 'Open ad', { class: 'comp-open', 'data-testid': 'comp-adlink' }) : null));
}

/** The lightbox: a <dialog> that shows one ad of a list, with previous / next, swipe, Esc and the arrow keys. */
export function lightbox() {
  const dlg = h('dialog', { class: 'comp-lb', 'data-testid': 'comp-lb', 'aria-label': 'Ad detail' });
  const stage = h('div', { class: 'comp-lb-stage' });
  const info = h('div', { class: 'comp-lb-info' });
  const count = h('span', { class: 'comp-lb-count', 'data-testid': 'comp-lb-count', 'aria-live': 'polite' });
  const close = h('button', { type: 'button', class: 'comp-lb-close', 'data-testid': 'comp-lb-close', 'aria-label': 'Close', onclick: () => dlg.close() }, '✕');
  const prev = h('button', { type: 'button', class: 'comp-lb-nav comp-lb-prev', 'data-testid': 'comp-lb-prev', 'aria-label': 'Previous ad', onclick: () => go(-1) }, '‹');
  const next = h('button', { type: 'button', class: 'comp-lb-nav comp-lb-next', 'data-testid': 'comp-lb-next', 'aria-label': 'Next ad', onclick: () => go(1) }, '›');
  dlg.append(close, prev, h('div', { class: 'comp-lb-body' }, stage, info), next, count);
  let list = [], i = 0, from = null;

  function show() {
    const ad = list[i];
    const u = safeHttp(ad.ad_library_url), lp = landing(ad.landing_url);
    stage.replaceChildren(visual(ad, true));
    info.replaceChildren(...[
      h('p', { class: 'comp-lb-brand' }, ad.brand, ' ', formatBadge(ad.format)),
      ad.badge ? h('p', null, h('span', { class: 'comp-badge', 'data-reason': ad.reason || '' }, ad.badge)) : null,
      ad.headline ? h('h2', { class: 'comp-lb-headline', 'data-testid': 'comp-lb-headline' }, ad.headline) : null,
      h('p', { class: 'comp-lb-text', 'data-testid': 'comp-lb-text' }, ad.primary_text || ad.hook || 'No text on this ad.'),
      ad.angle ? h('p', { class: 'pri-sub' }, 'Angle: ', ad.angle, ad.is_new_angle ? ' (Lonvera does not run this)' : '') : null,
      isNum(ad.variant_count) && ad.variant_count > 1 ? h('p', { class: 'pri-sub' }, `${ad.variant_count} ads share this copy.`) : null,
      h('p', { class: 'comp-lb-links' },
        lp ? link(lp, ad.landing_domain ? 'Landing page: ' + ad.landing_domain : 'Landing page', { 'data-testid': 'comp-lb-landing' }) : null,
        u ? link(u, 'Open in the Ad Library', { 'data-testid': 'comp-lb-adlink' }) : null)].filter(Boolean));
    count.textContent = `${i + 1} / ${list.length}`;
    prev.hidden = next.hidden = list.length < 2;
  }
  function go(d) { if (list.length < 2) return; i = (i + d + list.length) % list.length; show(); }

  dlg.addEventListener('keydown', (e) => { if (e.key === 'ArrowLeft') { e.preventDefault(); go(-1); } else if (e.key === 'ArrowRight') { e.preventDefault(); go(1); } });
  dlg.addEventListener('click', (e) => { if (e.target === dlg) dlg.close(); });          // a click on the dimmed backdrop closes it
  dlg.addEventListener('close', () => { if (from && from.isConnected) from.focus(); });
  let sx = null;
  dlg.addEventListener('pointerdown', (e) => { if (e.target.closest('a,button')) return; sx = e.clientX; });
  dlg.addEventListener('pointerup', (e) => { if (sx === null) return; const dx = e.clientX - sx; sx = null; if (Math.abs(dx) > 50) go(dx < 0 ? 1 : -1); });
  dlg.addEventListener('pointercancel', () => { sx = null; });

  dlg.openAt = (ads, ad) => { list = ads; i = Math.max(0, ads.findIndex((a) => a.library_id === ad.library_id)); from = document.activeElement; show(); if (!dlg.open) dlg.showModal(); };
  return dlg;
}

/** Horizontal bars. rows: [{label, sub, value, hl, title}]. hl bars use the accent; others are grey. */
export function bars(rows, testid, unit) {
  const max = Math.max(1, ...rows.map((r) => r.value));
  return h('ul', { class: 'comp-bars', 'data-testid': testid }, rows.map((r) => h('li', { class: 'comp-bar' + (r.hl ? ' comp-bar-hl' : ''), 'data-hl': r.hl ? '1' : '0', title: r.title || null },
    h('span', { class: 'comp-bar-label' }, r.label, r.sub ? h('small', null, r.sub) : null),
    h('span', { class: 'comp-bar-track' }, h('span', { class: 'comp-bar-fill', style: { width: Math.max(3, Math.round(100 * r.value / max)) + '%' } })),
    h('span', { class: 'comp-bar-n' }, String(r.value) + (unit ? ' ' + (r.value === 1 ? unit.replace(/s$/, '') : unit) : '')))));
}
