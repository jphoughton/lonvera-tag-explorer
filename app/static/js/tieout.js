// "Meta vs Upstack" tie-out panel: totals for the current window from /api/source/tieout. Opened from the source banner; nothing here changes the page's numbers.
import { h, fmt, isNum, apiWindow, currentParams } from './util.js';
import { api } from './api.js';

const sgn = (v) => (isNum(v) ? (v > 0 ? '+' : v < 0 ? '−' : '') + (Math.abs(v) * 100).toFixed(1) + '%' : '–');
const ratio = (a, b) => (isNum(a) && isNum(b) && b > 0 ? a / b : null);

function table(head, rows, cls = '') {
  return h('table', { class: 'tieout-table ' + cls },
    h('thead', h('tr', head.map((t, i) => h('th', { class: i ? 'num' : '' }, t)))),
    h('tbody', rows.map((r) => h('tr', { class: r.cls || '' }, r.cells.map((c, i) => h(i ? 'td' : 'th', { class: i ? 'num' : '', scope: i ? null : 'row' }, c))))));
}

function render(d) {
  const meta = d.meta, feed = d.upstack_feed, camp = d.upstack_campaigns, ads = d.upstack_ads, map = d.mapped, un = d.unmapped, tags = d.tags_pages;
  const money = fmt.money, int = fmt.int;
  const head = ['Source', 'Spend', 'Purchases / orders', 'Revenue', 'New customers', 'NC revenue', 'ROAS', 'CPA', 'NC ROAS', 'NC CPA'];
  const rows = [
    { cells: ['Meta Marketing API, every ad', money(meta.spend), int(meta.purchases), money(meta.revenue), '–', '–', fmt.roas(meta.roas), money(meta.cpa), '–', '–'] },
    { cells: ['Upstack ad-platform feed, Meta campaigns', money(feed.spend), int(feed.purchases), money(feed.revenue), '–', '–', fmt.roas(ratio(feed.revenue, feed.spend)), money(ratio(feed.spend, feed.purchases)), '–', '–'] },
    { cells: ['Upstack attribution, campaign level', money(feed.spend), int(camp.orders), money(camp.rev), int(camp.nc), money(camp.nc_rev), fmt.roas(ratio(camp.rev, feed.spend)), money(ratio(feed.spend, camp.orders)), fmt.roas(ratio(camp.nc_rev, feed.spend)), money(ratio(feed.spend, camp.nc))] },
    { cells: ['Upstack attribution, ad level (as pulled)', money(feed.spend), int(ads.orders), money(ads.rev), int(ads.nc), money(ads.nc_rev), fmt.roas(ratio(ads.rev, feed.spend)), money(ratio(feed.spend, ads.orders)), fmt.roas(ratio(ads.nc_rev, feed.spend)), money(ratio(feed.spend, ads.nc))] },
    { cls: 'shown', cells: ['Upstack, tied to Meta ads (what Upstack mode shows)', money(map.spend), int(map.orders), money(map.rev), int(map.nc), money(map.nc_rev), fmt.roas(map.roas_all), money(map.cpa_all), fmt.roas(map.roas_nc), money(map.cpa_nc)] },
    { cls: 'muted', cells: ['Upstack ad rows tied to no Meta ad (in no row)', '–', int(un.orders), money(un.rev), int(un.nc), money(un.nc_rev), '–', '–', '–', '–'] },
  ];
  const t2 = table(['Tags pages total (creatives on the tracker)', 'Spend', 'Purchases / orders / new customers', 'Revenue', 'ROAS', 'CPA'], [
    { cells: ['Meta', money(tags.meta.spend), int(tags.meta.purchases), money(tags.meta.revenue), fmt.roas(ratio(tags.meta.revenue, tags.meta.spend)), money(ratio(tags.meta.spend, tags.meta.purchases))] },
    { cells: ['Upstack · new customers', money(tags.upstack_nc.spend), int(tags.upstack_nc.purchases), money(tags.upstack_nc.revenue), fmt.roas(ratio(tags.upstack_nc.revenue, tags.upstack_nc.spend)), money(ratio(tags.upstack_nc.spend, tags.upstack_nc.purchases))] },
    { cells: ['Upstack · all orders', money(tags.upstack_all.spend), int(tags.upstack_all.purchases), money(tags.upstack_all.revenue), fmt.roas(ratio(tags.upstack_all.revenue, tags.upstack_all.spend)), money(ratio(tags.upstack_all.spend, tags.upstack_all.purchases))] },
  ]);
  const early = feed.first_day && d.from < feed.first_day;
  const lines = [
    `Spend: Upstack's feed is ${sgn(feed.spend_vs_meta_pct)} vs Meta (same dollars, two routes). Upstack mode uses Meta's spend per ad.` + (early ? ` Upstack's own spend feed starts ${fmt.day(feed.first_day)}; days before that are blank in it, so this check covers ${fmt.day(feed.first_day)} on.` : ''),
    `Orders: Upstack tied to Meta ads is ${sgn(map.orders_vs_meta_purchases_pct)} vs Meta purchases; revenue ${sgn(map.rev_vs_meta_revenue_pct)}. Campaign level is ${sgn(camp.orders_vs_meta_purchases_pct)} (any-click credits an order to each ad clicked in the 30 days before it).`,
    `Ad rows carry ${isNum(ads.ads_vs_campaigns_nc) ? (ads.ads_vs_campaigns_nc * 100).toFixed(0) + '%' : '–'} of the campaign-level new customers and ${isNum(ads.ads_vs_campaigns_orders) ? (ads.ads_vs_campaigns_orders * 100).toFixed(0) + '%' : '–'} of the orders; the rest are credited to a campaign but to no identifiable ad. ${int(un.nc)} new customers / ${int(un.orders)} orders have an ad name that matches no Meta ad.`,
  ];
  return h('div', null,
    h('div', { class: 'tieout-head' }, h('strong', null, 'Meta vs Upstack · '), d.window_info && d.window_info.label ? d.window_info.label : `${d.from} – ${d.to}`),
    h('div', { class: 'tieout-scroll' }, table(head, rows, 'main')),
    h('div', { class: 'tieout-scroll' }, t2),
    h('ul', { class: 'tieout-notes' }, lines.map((l) => h('li', null, l))),
    h('div', { class: 'muted small' }, `${d.model}. Days are Pacific for Upstack and each ad account's own day for Meta.`));
}

export async function loadTieout(container) {
  container.replaceChildren(h('div', { class: 'muted small' }, 'Loading tie-out…'));
  try {
    const w = apiWindow(currentParams());
    const params = {};
    if (w.from && w.to) { params.from = w.from; params.to = w.to; } else if (w.window) params.window = w.window;
    const d = await api('/api/source/tieout', params);
    container.replaceChildren(render(d));
  } catch (e) {
    container.replaceChildren(h('div', { class: 'err' }, 'Tie-out unavailable: ' + (e && e.message ? e.message : 'error')));
  }
}
