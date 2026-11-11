// Tag lookup (/lookup): paste an ad name as the ad platform shows it (or a Meta ad id, a Concept ID, an Ad Family) and read the new tags of that ad.
// One call to /api/tag-lookup. Class names and test ids start with `tl-` (never ad-, ads-, sponsor, banner, promo: ad blockers hide those).
import { h, href, navigate, safeUrl, badge, confBadge } from '../util.js';
import { api } from '../api.js';
import { showControls } from '../controls.js';
import { pageHeader } from '../ui/pagehead.js';

const EXAMPLE = 'Bull-526-Hook2 - Copy';
const KIND_TEXT = {
  exact: 'Matched the platform ad name exactly.',
  normalized: 'Matched the platform ad name after ignoring capital letters, spacing and a trailing "- Copy" or merge tag.',
  id: 'Matched the Meta ad id; the ad’s name leads to these tracker rows.',
  family: 'Matched the tracker’s Ad Family (or the tracker name underneath a prefix and encoded suffix).',
  concept: 'Matched a tracker Concept ID.',
};
const SLOW_MS = 15000;

const val = (v) => (v == null || String(v).trim() === '' ? '–' : String(v));

// the search form; `onSearch(text)` runs on Enter or the button
function searchForm({ value = '', onSearch, id = 'tl-q', testid = 'tl' }) {
  const input = h('input', { type: 'text', id, name: 'q', class: 'tl-input', value, maxlength: '300', autocomplete: 'off', spellcheck: 'false', autocapitalize: 'off',
    placeholder: `Paste an ad name, for example ${EXAMPLE}`, 'data-testid': testid + '-input' });
  const go = h('button', { type: 'submit', class: 'btn primary', 'data-testid': testid + '-go' }, 'Look up');
  const clear = h('button', { type: 'button', class: 'btn', 'data-testid': testid + '-clear', hidden: !value, onclick: () => { input.value = ''; clear.hidden = true; input.focus(); if (onSearch) onSearch(''); } }, 'Clear');
  input.addEventListener('input', () => { clear.hidden = input.value === ''; });
  const form = h('form', { class: 'tl-form card', role: 'search', 'data-testid': testid + '-form', onsubmit: (e) => { e.preventDefault(); const t = input.value.trim(); if (t) onSearch(t); else input.focus(); } },
    h('label', { class: 'tl-label', for: id }, 'Look up an ad by its platform name'), h('div', { class: 'tl-row' }, input, go, clear));
  return { el: form, input };
}


const goLookup = (text) => navigate(text ? href('/lookup', { q: text }) : '/lookup');

function dl(pairs) {
  return h('dl', { class: 'tl-list' }, pairs.filter(([, v]) => v != null && v !== '').flatMap(([k, v]) => [h('dt', null, k), h('dd', null, v)]));
}

function ingredientsNode(list) {
  if (!list || !list.length) return '–';
  return h('ul', { class: 'tl-ing' }, list.map((i) => h('li', null, h('strong', null, i.ingredient || '–'),
    i.attacks ? h('div', { class: 'tl-prose small' }, 'Attacks: ' + i.attacks) : null, i.supports ? h('div', { class: 'tl-prose small' }, 'Supports: ' + i.supports) : null)));
}

function newBlock(m) {
  const t = m.new_tags;
  if (!t) {
    return h('div', { class: 'tl-none', 'data-testid': 'tl-new-none' }, h('strong', null, 'No new tags for this one. '), m.new_tags_reason === 'not tagged yet'
      ? 'It has not been through the image and video tagging yet. The tracker’s own tags are below.' : (m.new_tags_reason || ''));
  }
  return h('div', { class: 'tl-new', 'data-testid': 'tl-new' },
    h('div', { class: 'tl-head' },
      h('span', { class: 'linecode big tl-code', 'data-testid': 'tl-line-code' }, t.line_code || 'no line'),
      h('span', { class: 'tl-linename', 'data-testid': 'tl-line-name' }, t.line_name || ''),
      confBadge(t.confidence), t.method ? badge(t.method, 'method', 'How the ad was tagged') : null),
    t.caveat ? h('div', { class: 'tl-caveat', 'data-testid': 'tl-caveat' }, t.caveat) : null,
    dl([['Market cluster', t.cluster], ['Ideal customer profile (ICP)', t.icp], ['Pain point', t.pain_point], ['Emotional trigger', t.emotional_trigger], ['Ingredient', ingredientsNode(t.ingredients)]]),
    t.seen ? h('div', { class: 'tl-block' }, h('div', { class: 'tl-k' }, 'What was seen'), h('p', { class: 'tl-prose' }, t.seen)) : null,
    t.why ? h('div', { class: 'tl-block' }, h('div', { class: 'tl-k' }, 'Why this line'), h('p', { class: 'tl-prose' }, t.why)) : null,
    t.runner_up ? h('div', { class: 'tl-block' }, h('div', { class: 'tl-k' }, 'Runner-up line'), h('p', { class: 'tl-prose' }, t.runner_up)) : null,
    h('div', { class: 'tl-ver' }, 'Tag version ' + (t.version || '–')));
}

function linkRow(m) {
  return h('div', { class: 'tl-links' }, h('a', { class: 'btnlink', href: m.links.ad_page, 'data-testid': 'tl-open-page' }, 'Open the ad page'));
}

function matchCard(m) {
  return h('article', { class: 'card tl-card', 'data-testid': 'tl-match', 'data-id': String(m.id) },
    h('div', { class: 'tl-who' },
      h('strong', { class: 'tl-concept', 'data-testid': 'tl-concept' }, m.concept_id), m.ad_family && m.ad_family !== m.concept_id ? h('span', { class: 'tl-muted' }, ' \u00b7 family ' + m.ad_family) : null,
      h('div', { class: 'tl-meta' }, badge(m.product.name, 'muted-badge'), m.status ? badge(m.status, 'muted-badge') : null)),
    newBlock(m),
    linkRow(m));
}

function platformNote(d) {
  const p = d.platform;
  if (!p) return null;
  const a = p.ads[0];
  return h('div', { class: 'tl-platform', 'data-testid': 'tl-platform' },
    h('strong', null, `On the platform: ${p.ad_count} ad${p.ad_count === 1 ? '' : 's'} with this name. `),
    a ? `${a.status || ''}${a.adset_name ? ' in ad set ' + a.adset_name : ''}. ` : '',
    a && safeUrl(a.ads_manager_url) ? h('a', { href: safeUrl(a.ads_manager_url), target: '_blank', rel: 'noopener noreferrer' }, 'Open in Ads Manager ↗') : null);
}

function noMatch(d) {
  return h('div', { class: 'tl-nomatch', 'data-testid': 'tl-nomatch', role: 'status' },
    h('h2', { class: 'tl-h' }, 'No tags found'),
    h('p', { class: 'tl-prose' }, d.reason || 'Nothing matched.'),
    platformNote(d),
    h('p', { class: 'tl-muted' }, 'Check the spelling or paste the name straight from Ads Manager.'));
}

function results(d, text) {
  if (d.match_kind === 'none') return noMatch(d);
  const n = d.total_matches;
  return h('div', { class: 'tl-results', 'data-testid': 'tl-results' },
    h('h2', { class: 'tl-h tl-results-head', 'data-testid': 'tl-results-head' }, `Results for \u201c${text}\u201d`),
    h('section', { class: 'tl-group tl-how', 'aria-label': 'How it matched' },
      h('div', { class: 'tl-summary', 'data-testid': 'tl-summary', 'data-kind': d.match_kind }, h('strong', null, `${n} tracker row${n === 1 ? '' : 's'}. `), KIND_TEXT[d.match_kind] || '',
        d.truncated ? ` Showing the first ${d.matches.length}.` : ''),
      d.notes.map((t) => h('div', { class: 'tl-note', 'data-testid': 'tl-note' }, t)),
      platformNote(d)),
    h('section', { class: 'tl-group', 'aria-label': 'Tracker rows' },
      h('h3', { class: 'tl-sub' }, n === 1 ? 'Tracker row and its tags' : 'Tracker rows and their tags'),
      d.matches.map(matchCard)),
    h('div', { class: 'tl-foot' }, 'New tags come from the image and video tagging of each ad.'));
}

async function load(text, signal) {
  const ctl = new AbortController();
  let slow = false;
  const timer = setTimeout(() => { slow = true; ctl.abort(); }, SLOW_MS);
  const relay = () => ctl.abort();
  signal.addEventListener('abort', relay);
  try {
    return await api('/api/tag-lookup', { q: text }, { signal: ctl.signal });
  } catch (e) {
    if (slow) throw new Error(`The lookup took longer than ${SLOW_MS / 1000} seconds. Press Retry.`);
    throw e;
  } finally {
    clearTimeout(timer);
    signal.removeEventListener('abort', relay);
  }
}

export async function lookupPage(ctx) {
  const { params: p, signal } = ctx;
  showControls(false);
  document.title = 'Tag lookup · Lonvera';
  const text = (p.q || '').trim();
  const out = h('div', { class: 'tl-out', 'aria-live': 'polite', 'data-testid': 'tl-out' });
  const form = searchForm({ value: text, onSearch: goLookup });
  const page = h('div', { class: 'page tl-page', 'data-testid': 'tl-page' },
    pageHeader({ title: 'Tag lookup', description: 'The results page for the tag search in the top bar. Paste an ad name exactly as Ads Manager shows it. A Meta ad id, a Concept ID or an Ad Family works too.', testid: 'tl-head' }),
    form.el, out);
  ctx.mount(page);
  if (!text) {
    out.append(h('div', { class: 'tl-empty', 'data-testid': 'tl-empty' }, h('p', { class: 'tl-prose' }, 'Nothing searched yet. Type an ad name above or in the top bar. The new tags come from the image and video tagging of Vita Bull, Beef Liver and Boneflex ads.')));
    form.input.focus();
    return;
  }
  const run = async () => {
    out.replaceChildren(h('div', { class: 'loading', 'data-testid': 'tl-loading' }, 'Looking up…'));
    try {
      const d = await load(text, signal);
      if (!ctx.alive()) return;
      out.replaceChildren(results(d, text));
    } catch (e) {
      if (!ctx.alive() || (e && e.name === 'AbortError')) return;
      out.replaceChildren(h('div', { class: 'errorbox', role: 'alert', 'data-testid': 'tl-error' }, h('strong', null, 'Could not look that up. '), h('span', null, e && e.message ? e.message : String(e)),
        h('div', null, h('button', { type: 'button', class: 'btn', 'data-testid': 'tl-retry', onclick: run }, 'Retry'))));
    }
  };
  await run();
}
