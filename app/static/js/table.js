// Generic sortable table: header click toggles asc/desc, numeric-aware compare, nulls last, stable,
// pinned total row, clickable rows (mouse + Enter), optional inline row expansion, progressive rendering.
import { h, navigate } from './util.js';

const collator = new Intl.Collator('en-US', { numeric: true, sensitivity: 'base' });
const isNil = (v) => v == null || v === '' || (typeof v === 'number' && !Number.isFinite(v));

export function compare(a, b, dir) {
  const an = isNil(a), bn = isNil(b);
  if (an || bn) return an && bn ? 0 : an ? 1 : -1; // nulls last in both directions
  const c = typeof a === 'number' && typeof b === 'number' ? a - b : collator.compare(String(a), String(b));
  return dir === 'desc' ? -c : c;
}

/*
 opts: {
   columns: [{key,label,num,sort(row),cell(row)->Node|string,total(totalObj)->Node|string,title,cls,sticky,nosort,header(Node),firstDir}],
   rows, total, sortKey, sortDir, onSort(key,dir),
   rowHref(row)->string|null, rowKey(row)->string, rowClass(row)->string,
   expand: {render(row)->Promise<Node>|Node}   // adds a caret column
   pageSize, emptyText, tableClass, testid
 }
*/
export function createTable(opts) {
  let rows = opts.rows || [];
  let total = opts.total || null;
  let filter = null;
  let sortKey = opts.sortKey || null;
  let sortDir = opts.sortDir || null;
  const pageSize = opts.pageSize || 400;
  let limit = pageSize;
  const expanded = new Set();
  const expCache = new Map();
  const columns = opts.columns;
  const rowKey = opts.rowKey || ((r, i) => String(i));

  const wrap = h('div', { class: 'tablewrap' });
  const foot = h('div', { class: 'tablefoot' });
  const root = h('div', { class: 'tablebox', 'data-testid': opts.testid || null }, wrap, foot);

  const colCount = columns.length + (opts.expand ? 1 : 0);

  function view() {
    const list = filter ? rows.filter(filter) : rows.slice();
    if (!sortKey) return list;
    const col = columns.find((c) => c.key === sortKey);
    if (!col || col.nosort) return list;
    const get = col.sort || ((r) => r[col.key]);
    return list
      .map((r, i) => ({ r, i, v: get(r) }))
      .sort((x, y) => compare(x.v, y.v, sortDir) || x.i - y.i)
      .map((x) => x.r);
  }

  function headerCell(c) {
    const th = h('th', { class: [c.num ? 'num' : '', c.sticky ? 'sticky' : '', c.cls || ''].join(' ').trim(), title: c.title || null, 'data-col': c.key });
    if (c.header) { th.append(c.header); return th; }
    if (c.nosort) { th.append(c.label); return th; }
    const active = sortKey === c.key;
    th.setAttribute('aria-sort', active ? (sortDir === 'asc' ? 'ascending' : 'descending') : 'none');
    const btn = h('button', { type: 'button', class: 'thbtn' + (active ? ' active' : '') },
      h('span', c.label),
      h('span', { class: 'arrow' }, active ? (sortDir === 'asc' ? '▲' : '▼') : ''));
    btn.addEventListener('click', () => {
      const first = c.firstDir || (c.num ? 'desc' : 'asc');
      if (sortKey === c.key) sortDir = sortDir === 'asc' ? 'desc' : 'asc';
      else { sortKey = c.key; sortDir = first; }
      if (opts.onSort) opts.onSort(sortKey, sortDir);
      render();
    });
    th.append(btn);
    return th;
  }

  function cellContent(c, r) {
    const v = c.cell ? c.cell(r) : r[c.key];
    return v == null ? '' : v;
  }

  function rowEl(r, i) {
    const href = opts.rowHref ? opts.rowHref(r) : null;
    const key = rowKey(r, i);
    const tr = h('tr', {
      class: [href ? 'clickable' : '', opts.rowClass ? opts.rowClass(r) : ''].join(' ').trim(),
      tabindex: href ? '0' : null,
      'data-key': key,
      'data-href': href || null,
    });
    if (opts.expand) {
      const open = expanded.has(key);
      const btn = h('button', { type: 'button', class: 'caret', 'aria-expanded': String(open), title: 'Show Meta ads', 'aria-label': 'Show Meta ads' }, open ? '▾' : '▸');
      btn.addEventListener('click', (e) => { e.stopPropagation(); toggle(key, r); });
      tr.append(h('td', { class: 'caretcell' }, btn));
    }
    for (const c of columns) {
      tr.append(h('td', {
        class: [c.num ? 'num' : '', c.sticky ? 'sticky' : '', c.cls || ''].join(' ').trim(),
        title: c.cellTitle ? c.cellTitle(r) : null,
      }, cellContent(c, r)));
    }
    if (href) {
      tr.addEventListener('click', (e) => {
        if (e.target.closest('a,button,input,select,label,summary,textarea')) return;
        navigate(href);
      });
      tr.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && e.target === tr) navigate(href);
      });
    }
    return tr;
  }

  function expandRow(r, key) {
    if (!expCache.has(key)) {
      const holder = h('div', { class: 'expand-body' }, h('span', { class: 'muted' }, 'Loading…'));
      expCache.set(key, holder);
      Promise.resolve()
        .then(() => opts.expand.render(r))
        .then((node) => holder.replaceChildren(node))
        .catch((e) => holder.replaceChildren(h('span', { class: 'err' }, 'Failed to load: ' + (e && e.message ? e.message : e))));
    }
    return h('tr', { class: 'expandrow', 'data-expand-for': key }, h('td', { colspan: String(colCount) }, expCache.get(key)));
  }

  function toggle(key, r) {
    if (expanded.has(key)) expanded.delete(key); else expanded.add(key);
    render();
  }

  function totalRow() {
    const tr = h('tr', { class: 'total' });
    if (opts.expand) tr.append(h('td', { class: 'caretcell' }));
    columns.forEach((c, i) => {
      const v = c.total ? c.total(total) : (i === 0 ? 'Total' : '');
      tr.append(h('td', { class: [c.num ? 'num' : '', c.sticky ? 'sticky' : '', c.cls || ''].join(' ').trim() }, v == null ? '' : v));
    });
    return tr;
  }

  function render() {
    const list = view();
    const shown = list.slice(0, limit);
    const table = h('table', { class: 'dt ' + (opts.tableClass || '') });
    const headRow = h('tr');
    if (opts.expand) headRow.append(h('th', { class: 'caretcell' }));
    columns.forEach((c) => headRow.append(headerCell(c)));
    table.append(h('thead', headRow));
    const tbody = h('tbody');
    shown.forEach((r, i) => {
      const key = rowKey(r, i);
      tbody.append(rowEl(r, i));
      if (opts.expand && expanded.has(key)) tbody.append(expandRow(r, key));
    });
    if (!list.length) {
      tbody.append(h('tr', { class: 'emptyrow' }, h('td', { colspan: String(colCount) }, opts.emptyText || 'No rows.')));
    }
    table.append(tbody);
    if (total) table.append(h('tfoot', totalRow()));
    const st = wrap.scrollTop, sl = wrap.scrollLeft;
    wrap.replaceChildren(table);
    wrap.scrollTop = st; wrap.scrollLeft = sl;

    foot.replaceChildren();
    if (list.length > shown.length) {
      foot.append(
        h('span', { class: 'muted' }, `Showing ${shown.length.toLocaleString('en-US')} of ${list.length.toLocaleString('en-US')} `),
        h('button', { type: 'button', class: 'btn small', onclick: () => { limit += pageSize; render(); } }, `Show ${Math.min(pageSize, list.length - shown.length)} more`),
        h('button', { type: 'button', class: 'btn small', onclick: () => { limit = list.length; render(); } }, 'Show all'),
      );
    }
    if (opts.onCount) opts.onCount(list.length, rows.length);
  }

  render();
  return {
    el: root,
    setRows(r, t) { rows = r; if (t !== undefined) total = t; limit = pageSize; render(); },
    setFilter(fn) { filter = fn; limit = pageSize; render(); },
    sortState: () => ({ key: sortKey, dir: sortDir }),
  };
}
