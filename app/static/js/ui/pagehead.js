// The one page header: title, one plain-English line saying what the page is for, and a slot on the right for actions.
// It renders the same markup the Meta, Trends and Test to scale pages already use (.pagehead > .titleline > h1 + .note-line), so adopting it changes nothing visually.
//
//   pageHeader({ title, description, badge, actions, testid, titleTestid })
//     title        the page name from the naming table in docs/UI_SYSTEM.md (Overview, Meta, Google, Campaigns, Trends, Priorities, Production, Test to scale, Projections)
//     description  one sentence, plain English, no unexpanded acronyms
//     badge        optional short qualifier shown next to the title ("Meta only")
//     actions      node or array of nodes (buttons, links) placed at the right of the title line
import { h } from '../util.js';

export function pageHeader({ title, description, badge, actions, testid, titleTestid } = {}) {
  const acts = actions == null ? [] : [].concat(actions).filter(Boolean);
  return h('div', { class: 'pagehead', 'data-testid': testid || null },
    h('div', { class: 'titleline' },
      h('h1', { 'data-testid': titleTestid || null }, title),
      badge ? h('span', { class: 'badge muted-badge' }, badge) : null,
      acts.length ? h('div', { class: 'pagehead-actions' }, acts) : null),
    description ? h('div', { class: 'note-line' }, description) : null);
}
