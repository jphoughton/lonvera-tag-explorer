// Live ad preview (Meta preview iframe), "See the ad" section, preview modal and the per-ad links.
import { h, safeUrl } from './util.js';
import { api, AuthError } from './api.js';
import { openModal } from './modal.js';

export const FORMATS = [
  ['mobile_feed', 'Mobile feed', 400, 700],
  ['desktop_feed', 'Desktop feed', 540, 600],
  ['reels', 'Reels', 400, 700],
  ['stories', 'Stories', 400, 700],
];
const LOAD_TIMEOUT_MS = 15000;

// 'Facebook post ↗' and 'Ads Manager ↗' (only safe http(s) URLs are linked)
export function adLinks(links, { cls = '' } = {}) {
  if (!links) return null;
  const mk = (url, text, id) => {
    const u = safeUrl(url);
    return u ? h('a', { href: u, target: '_blank', rel: 'noopener noreferrer', class: 'ext ' + cls, 'data-link': id }, text + ' ↗') : null;
  };
  const a = mk(links.story_url, 'Facebook post', 'facebook-post');
  const b = mk(links.ads_manager_url, 'Ads Manager', 'ads-manager');
  return a || b ? h('span', { class: 'adlinks' }, a, a && b ? ' ' : null, b) : null;
}

// eye button: opens the live preview of one Meta ad in a modal (/api/meta-ads/<ad_id>/preview)
export function eyeButton(r) {
  if (r.ad_id == null) return null;
  const b = h('button', { type: 'button', class: 'eye', title: 'See live ad', 'aria-label': 'See live ad: ' + (r.ad_name || r.ad_id) }, '👁');
  b.addEventListener('click', (e) => { e.stopPropagation(); openPreviewModal({ metaAdId: r.ad_id, name: r.ad_name, links: r.links }); });
  return b;
}
export function adActions(r) {
  const l = adLinks(r.links);
  const e = eyeButton(r);
  return l || e ? h('span', { class: 'adactions' }, e, e && l ? ' ' : null, l) : null;
}

/*
 createPreview({ endpoint, links, name, signal }) -> { el, load() }
 endpoint = '/api/ads/<id>/preview' or '/api/meta-ads/<ad_id>/preview'; response { enabled, src, format, error? }
*/
export function createPreview({ endpoint, links, name, signal }) {
  let format = 'mobile_feed';
  let seq = 0;
  const btns = FORMATS.map(([k, label]) => {
    const b = h('button', { type: 'button', class: 'seg', 'data-format': k, 'aria-pressed': String(k === format) }, label);
    b.addEventListener('click', () => { format = k; btns.forEach((x) => x.setAttribute('aria-pressed', String(x.dataset.format === k))); load(); });
    return b;
  });
  const stage = h('div', { class: 'preview-stage', 'aria-live': 'polite' });
  const linkRow = h('div', { class: 'preview-links' }, adLinks(links));
  const switcher = h('div', { class: 'preview-formats segs', role: 'group', 'aria-label': 'Preview format', hidden: true }, btns);
  const el = h('div', { class: 'preview' }, switcher, stage, linkRow);

  const fallback = (title, detail) => h('div', { class: 'preview-msg', 'data-state': 'unavailable' },
    h('strong', title), detail ? h('div', { class: 'muted small' }, detail) : null, links ? h('div', null, adLinks(links)) : null);

  async function load() {
    const my = ++seq;
    stage.replaceChildren(h('div', { class: 'preview-msg', 'data-state': 'loading' }, h('span', { class: 'spinner', role: 'status', 'aria-label': 'Loading preview' }), ' Loading preview…'));
    let d;
    try {
      d = await api(endpoint, { format }, { signal });
    } catch (e) {
      if (my !== seq || e instanceof AuthError || (e && e.name === 'AbortError')) return;
      switcher.hidden = true;
      stage.replaceChildren(fallback('Preview unavailable', e && e.message ? e.message : null));
      return;
    }
    if (my !== seq) return;
    if (!d || d.enabled === false) {
      switcher.hidden = true;
      stage.replaceChildren(h('div', { class: 'preview-msg', 'data-state': 'off' }, h('strong', 'Live preview off'), h('div', { class: 'muted small' }, 'Open the ad from the links below.')));
      return;
    }
    switcher.hidden = false;
    const src = d.error ? null : safeUrl(d.src);
    if (!src) { stage.replaceChildren(fallback('Preview unavailable', d.error || 'No preview URL was returned.')); return; }
    const fmt = FORMATS.find((f) => f[0] === (d.format || format)) || FORMATS.find((f) => f[0] === format);
    const frame = h('iframe', {
      class: 'preview-frame', src, title: `Live preview${name ? ': ' + name : ''} (${fmt[1]})`,
      sandbox: 'allow-scripts allow-same-origin allow-popups', loading: 'lazy', referrerpolicy: 'no-referrer',
      width: String(fmt[2]), height: String(fmt[3]), style: { width: fmt[2] + 'px', height: fmt[3] + 'px' },
    });
    const spin = h('div', { class: 'preview-msg overlay', 'data-state': 'loading' }, h('span', { class: 'spinner', role: 'status', 'aria-label': 'Loading preview' }));
    const wrap = h('div', { class: 'preview-wrap', style: { width: fmt[2] + 'px', minHeight: fmt[3] + 'px' } }, frame, spin);
    stage.replaceChildren(wrap);
    const timer = setTimeout(() => { if (my === seq && spin.isConnected) stage.replaceChildren(fallback('Preview unavailable', 'The preview did not load.')); }, LOAD_TIMEOUT_MS);
    frame.addEventListener('load', () => { clearTimeout(timer); spin.remove(); });
    frame.addEventListener('error', () => { clearTimeout(timer); if (my === seq) stage.replaceChildren(fallback('Preview unavailable')); });
  }
  return { el, load };
}

// modal around a preview: { primaryId | metaAdId, name, links }
export function openPreviewModal({ primaryId, metaAdId, name, links }) {
  const endpoint = metaAdId != null ? `/api/meta-ads/${encodeURIComponent(metaAdId)}/preview` : `/api/ads/${encodeURIComponent(primaryId)}/preview`;
  let pv = null;
  const m = openModal({
    label: `Live ad preview${name ? ': ' + name : ''}`,
    className: 'preview-modal',
    build(box) {
      pv = createPreview({ endpoint, links, name });
      box.append(h('h3', { class: 'modal-title' }, 'See live ad', name ? h('span', { class: 'muted' }, ' · ' + name) : null), pv.el);
    },
  });
  pv.load();
  return m;
}
