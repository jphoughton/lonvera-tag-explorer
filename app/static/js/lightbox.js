// Click any thumbnail -> full-screen overlay with the large image (frames for videos), ad name, tag,
// transcript snippet and two buttons: "Open ad page" and "See live ad".
import { h, enc, safeUrl, href, globalParams, currentParams, navigate } from './util.js';
import { api, AuthError } from './api.js';
import { openModal, closeAllModals } from './modal.js';
import { openPreviewModal, adLinks } from './preview.js';

const media = (id) => '/media/' + enc(id);

/* ctx: { assetId, name, primaryId, metaAdId, tag:{cluster,icp,pain}, detail (an /api/ads/<id> response, optional), links } */
export function openLightbox(ctx) {
  let images = ctx.assetId != null ? [{ id: ctx.assetId, label: 'Thumbnail' }] : [];
  let idx = 0;
  let detail = ctx.detail || null;
  const name = ctx.name || 'Ad';
  const img = h('img', { class: 'lb-img', alt: `${name} — thumbnail` });
  const counter = h('div', { class: 'lb-counter muted small', 'aria-live': 'polite' });
  const prev = h('button', { type: 'button', class: 'lb-nav prev', 'aria-label': 'Previous frame', hidden: true }, '‹');
  const next = h('button', { type: 'button', class: 'lb-nav next', 'aria-label': 'Next frame', hidden: true }, '›');
  const strip = h('div', { class: 'lb-strip', role: 'group', 'aria-label': 'Frames', hidden: true });
  const tagEl = h('div', { class: 'lb-tag muted small' });
  const transcript = h('div', { class: 'lb-transcript small' });
  const info = h('div', { class: 'lb-info' }, tagEl, transcript);
  const openAd = h('a', { class: 'btn primary', id: 'lb-open', href: ctx.primaryId != null ? href('/ad/' + enc(ctx.primaryId), globalParams(currentParams())) : '#', hidden: ctx.primaryId == null }, 'Open ad page');
  openAd.addEventListener('click', (e) => {
    e.preventDefault();
    const target = openAd.getAttribute('href');
    closeAllModals();
    navigate(target);
  });
  const live = h('button', { type: 'button', class: 'btn', id: 'lb-live' }, 'See live ad');
  live.addEventListener('click', () => openPreviewModal({ primaryId: ctx.primaryId, metaAdId: ctx.metaAdId, name, links: ctx.links || (detail && detail.links) }));
  if (ctx.primaryId == null && ctx.metaAdId == null) live.hidden = true;
  const linkEl = h('span', { class: 'lb-links' });

  function show(i) {
    if (!images.length) { img.removeAttribute('src'); counter.textContent = ''; return; }
    idx = (i + images.length) % images.length;
    img.src = media(images[idx].id);
    img.alt = `${name} — ${images.length > 1 ? `frame ${idx + 1} of ${images.length}` : 'thumbnail'}`;
    counter.textContent = images.length > 1 ? `${idx + 1} / ${images.length}` : '';
    prev.hidden = next.hidden = images.length < 2;
    strip.hidden = images.length < 2;
    [...strip.children].forEach((b, j) => b.setAttribute('aria-current', String(j === idx)));
  }
  function rebuildStrip() {
    strip.replaceChildren(...images.map((im, j) => {
      const b = h('button', { type: 'button', class: 'lb-frame', 'aria-label': `Frame ${j + 1} of ${images.length}`, 'aria-current': String(j === idx) },
        h('img', { src: media(im.id), alt: '', loading: 'lazy' }));
      b.addEventListener('click', () => show(j));
      return b;
    }));
  }
  function fill(d) {
    detail = d;
    const a = d.assets || {};
    const list = [];
    if (a.thumb != null) list.push({ id: a.thumb, label: 'Thumbnail' });
    for (const f of (a.frames || []).slice(0, 4)) list.push({ id: f, label: 'Frame' });
    if (list.length) {
      images = list;
      const si = list.findIndex((x) => String(x.id) === String(ctx.assetId));
      if (si >= 0) idx = si;
      rebuildStrip();
      show(Math.min(idx, list.length - 1));
    }
    const t = d.tag || {};
    const tt = d.tracker_tag || {};
    const path = [t.cluster || tt.cluster, t.icp || tt.icp, t.pain_point || tt.pain_point].filter(Boolean).join(' › ');
    tagEl.textContent = path ? 'Tag: ' + path : '';
    const tr = d.content && d.content.transcript;
    transcript.textContent = tr ? 'Transcript: ' + (tr.length > 300 ? tr.slice(0, 300).trim() + '…' : tr) : '';
    linkEl.replaceChildren(adLinks(d.links) || '');
  }
  if (ctx.tag) tagEl.textContent = 'Tag: ' + [ctx.tag.cluster, ctx.tag.icp, ctx.tag.pain].filter(Boolean).join(' › ');

  const m = openModal({
    label: `Ad image: ${name}`,
    className: 'lightbox',
    build(box) {
      box.append(
        h('div', { class: 'lb-head' }, h('h3', { class: 'modal-title' }, name)),
        h('div', { class: 'lb-stage' }, prev, h('div', { class: 'lb-imgwrap' }, img, counter), next),
        strip, info,
        h('div', { class: 'lb-actions' }, openAd, live, linkEl));
    },
    onKey(e) {
      if (e.key === 'ArrowLeft' && images.length > 1) { e.preventDefault(); show(idx - 1); }
      else if (e.key === 'ArrowRight' && images.length > 1) { e.preventDefault(); show(idx + 1); }
    },
  });
  prev.addEventListener('click', () => show(idx - 1));
  next.addEventListener('click', () => show(idx + 1));
  show(0);
  if (images.length) rebuildStrip();
  if (detail) fill(detail);
  else if (ctx.primaryId != null) {
    api('/api/ads/' + enc(ctx.primaryId), null).then((d) => { if (m.el.isConnected) fill(d); }).catch((e) => { if (!(e instanceof AuthError)) console.warn('lightbox detail failed', e); });
  }
  return m;
}

document.addEventListener('tagexp:lightbox', (e) => openLightbox(e.detail || {}));
