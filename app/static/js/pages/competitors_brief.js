// "Send to Proposed Concept" on a competitor ad: a small form prefilled from what the page knows about the ad (its angle, format, hook, how long it has run), where every tag is confirmed or filled in before the ad
// goes to the Proposed Concept lane as a concept for a week (POST /api/priorities/backlog). The lane needs all five tags (product, market cluster, ICP, pain point, image or video).
// Everything from the API goes in through textContent / h().
import { h, href } from '../util.js';
import { api } from '../api.js';
import { openModal } from '../modal.js';
import { today, mondayOf, addDays, weekLabel, toast, PRODUCT_NAMES } from './production_common.js';

const words = (s, n) => String(s || '').replace(/\s+/g, ' ').trim().split(' ').slice(0, n).join(' ');

export function briefDefaults(ad, brand) {
  const b = brand || ad.brand || 'a competitor';
  const days = Number.isFinite(ad.days_running) ? `${ad.days_running} days running` : 'running';
  const shared = ad.variant_count > 1 ? `, ${ad.variant_count} ads share it` : '';
  const product = ad.mapped_product && PRODUCT_NAMES[ad.mapped_product] ? ad.mapped_product : '';
  const angle = ad.cluster || ad.pain_point ? '' : (ad.new_angle_label || '');
  return {
    product, cluster: ad.cluster || angle, icp: '', pain_point: ad.pain_point || '', media_type: ad.format === 'video' || ad.format === 'image' ? ad.format : '',
    story: words(ad.hook || ad.headline, 15),
    note: `From a competitor: ${b}, ${days}${shared}.${ad.hook ? ' Hook: "' + words(ad.hook, 30) + '".' : ''}${ad.ad_library_url ? ' ' + ad.ad_library_url : ''}`.slice(0, 500),
  };
}

export function sendToProposed(ad, brand) {
  const f = { ...briefDefaults(ad, brand), for_week: mondayOf(today()) <= today() ? addDays(mondayOf(today()), 7) : mondayOf(today()) };
  const err = h('p', { class: 'pri-err', role: 'alert', 'data-testid': 'comp-brief-err' });
  const txt = (key, label, tid, max = 200) => h('label', { class: 'ctl' }, h('span', { class: 'ctl-label' }, label), h('input', { type: 'text', value: f[key] || '', maxlength: max, 'data-testid': tid, oninput: (e) => { f[key] = e.target.value; } }));
  const weeks = Array.from({ length: 6 }, (_, i) => addDays(mondayOf(today()), 7 * i));
  openModal({ label: 'Send to Proposed Concept', className: 'prod-cutads comp-brief', build(box) {
    box.append(h('h3', { class: 'prod-cutads-title', 'data-testid': 'comp-brief-title' }, 'Send to Proposed Concept'),
      h('p', { class: 'pri-sub' }, `${brand || ad.brand || 'Competitor'}: ${ad.hook ? '"' + words(ad.hook, 18) + '"' : 'no text'}. Confirm the tags: every proposed concept needs all five.`),
      h('div', { class: 'ctl-row prod-bl-form' },
        h('label', { class: 'ctl' }, h('span', { class: 'ctl-label' }, 'Product (required)'), h('select', { 'data-testid': 'comp-brief-product', onchange: (e) => { f.product = e.target.value; } }, [h('option', { value: '' }, 'Choose'), ...Object.entries(PRODUCT_NAMES).map(([k, v]) => h('option', { value: k, selected: k === f.product }, v))])),
        h('label', { class: 'ctl' }, h('span', { class: 'ctl-label' }, 'Image or video (required)'), h('select', { 'data-testid': 'comp-brief-media', onchange: (e) => { f.media_type = e.target.value; } }, [['', 'Choose'], ['video', 'Video'], ['image', 'Image']].map(([v, l]) => h('option', { value: v, selected: v === f.media_type }, l)))),
        txt('cluster', 'Market cluster (required)', 'comp-brief-cluster'), txt('icp', 'ICP (required)', 'comp-brief-icp'), txt('pain_point', 'Pain point (required)', 'comp-brief-pain'),
        h('label', { class: 'ctl' }, h('span', { class: 'ctl-label' }, 'For week'), h('select', { 'data-testid': 'comp-brief-week', onchange: (e) => { f.for_week = e.target.value; } }, weeks.map((w) => h('option', { value: w, selected: w === f.for_week }, weekLabel(w)))))),
      txt('story', 'Story (15 words or fewer)', 'comp-brief-story', 300), h('label', { class: 'prod-bl-openlabel' }, 'Note', h('textarea', { rows: 3, maxlength: 500, 'data-testid': 'comp-brief-note', oninput: (e) => { f.note = e.target.value; } }, f.note)),
      h('div', { class: 'prod-bl-openbar' }, h('button', { type: 'button', class: 'btn primary small', 'data-testid': 'comp-brief-send', onclick: async (e) => {
        err.textContent = '';
        const missing = [['product', 'product'], ['cluster', 'market cluster'], ['icp', 'ICP'], ['pain_point', 'pain point'], ['media_type', 'image or video']].filter(([k]) => !String(f[k] || '').trim()).map(([, n]) => n);
        if (missing.length) { err.textContent = 'Add ' + missing.join(', ') + ' before this goes to Proposed Concept.'; return; }
        const btn = e.currentTarget; btn.disabled = true;
        try {
          const r = await api('/api/priorities/backlog', {}, { method: 'POST', body: { items: [{ product: f.product, cluster: f.cluster.trim(), icp: f.icp.trim(), pain_point: f.pain_point.trim(), media_type: f.media_type, story: f.story.trim(), note: f.note }], for_week: f.for_week } });
          toast(r.created.length ? 'Sent to Proposed Concept.' : 'Already in Proposed Concept for that week.');
          document.querySelector('.comp-brief .modal-close')?.click();
        } catch (x) { err.textContent = x.message || 'Could not send.'; btn.disabled = false; }
      } }, 'Send to Proposed Concept'), h('a', { href: href('/priorities/production', {}), class: 'pri-sub', 'data-testid': 'comp-brief-open' }, 'Open the Board')), err);
  } });
}
