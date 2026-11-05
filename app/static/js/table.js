// Generic sortable table: header click toggles asc/desc, numeric-aware compare, nulls last, stable,
// pinned total row, clickable rows (mouse + Enter), optional inline row expansion, progressive rendering.
import { h, navigate, cmpView, onCmpRefresh } from './util.js';

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
   rows, total, sortKey, sortDir, sortBy ('value'|'delta'), onSort(key,dir,by),
   rowHref(row)->string|null, rowKey(row)->string, rowClass(row)->string,
   expand: {render(row)->Promise<Node>|Node}   // adds a caret column (expandLabel = its tooltip, default 'Show ads')
   pageSize, emptyText, tableClass, testid
 }
*/
export function createTable(opts) {
  let rows = opts.rows || [];
  let total = opts.total || null;
  let filter = null;
  let sortKey = opts.sortKey || null;
  let sortDir = opts.sortDir || null;
  let sortBy = opts.sortBy === 'delta' ? 'delta' : 'value';
  const pageSize = opts.pageSize || 400;
  let limit = pageSize;
  const expanded = new Set();
  const expCache = new Map();
  const columns = opts.columns;
  const rowKey = opts.rowKey || ((r, i) => String(i));

  const wrap = h('div', { class: 'tablewrap' });
  const foot = h('div', { class: 'tablefoot' });
  // shown when columns sit beyond the right edge: names them and scrolls on click
  const hint = h('div', { class: 'scrollhint', 'data-testid': 'scroll-hint', hidden: true });
  const root = h('div', { class: 'tablebox', 'data-testid': opts.testid || null }, wrap, foot, hint);

  // columns may hide themselves (c.hidden()): the Testing/Scaling columns only make sense when both stages are included
  let visCols = columns;
  let colCount = columns.length + (opts.expand ? 1 : 0);

  const labelOf = (c) => (typeof c.label === 'function' ? c.label() : c.label);

  function view() {
    const list = filter ? rows.filter(filter) : rows.slice();
    if (opts.externalSort) return list;
    if (!sortKey) return list;
    const col = columns.find((c) => c.key === sortKey);
    if (!col || col.nosort) return list;
    const byDelta = sortBy === 'delta' && cmpView.on && col.deltaSort;
    const get = byDelta ? col.deltaSort : (col.sort || ((r) => r[col.key]));
    return list
      .map((r, i) => ({ r, i, v: get(r) }))
      .sort((x, y) => compare(x.v, y.v, sortDir) || x.i - y.i)
      .map((x) => x.r);
  }

  function setSort(c, by) {
    const first = by === 'delta' ? 'desc' : (c.firstDir || (c.num ? 'desc' : 'asc'));
    if (sortKey === c.key && sortBy === by) sortDir = sortDir === 'asc' ? 'desc' : 'asc';
    else { sortKey = c.key; sortBy = by; sortDir = first; }
    if (opts.onSort) opts.onSort(sortKey, sortDir, sortBy);
    render();
  }

  function headerCell(c) {
    const th = h('th', { class: [c.num ? 'num' : '', c.sticky ? 'sticky' : '', c.cls || ''].join(' ').trim(), title: c.title || null, 'data-col': c.key });
    if (c.header) { th.append(c.header); return th; }
    if (c.nosort) { th.append(labelOf(c)); return th; }
    const active = sortKey === c.key;
    const effBy = active && sortBy === 'delta' && cmpView.on && c.deltaSort ? 'delta' : 'value';
    th.setAttribute('aria-sort', active ? (sortDir === 'asc' ? 'ascending' : 'descending') : 'none');
    th.dataset.sortby = active ? effBy : '';
    const btn = h('button', { type: 'button', class: 'thbtn' + (active && effBy === 'value' ? ' active' : '') },
      h('span', labelOf(c)),
      h('span', { class: 'arrow' }, active && effBy === 'value' ? (sortDir === 'asc' ? '▲' : '▼') : ''));
    btn.addEventListener('click', (e) => setSort(c, e.shiftKey && c.deltaSort && cmpView.on ? 'delta' : 'value'));
    th.append(btn);
    if (cmpView.on && c.deltaSort) {
      const d = h('button', { type: 'button', class: 'deltabtn' + (active && effBy === 'delta' ? ' active' : ''), title: 'Sort by % change (A vs B)', 'aria-label': 'Sort ' + labelOf(c) + ' by change' },
        'Δ' + (active && effBy === 'delta' ? (sortDir === 'asc' ? '▲' : '▼') : ''));
      d.addEventListener('click', () => setSort(c, 'delta'));
      th.append(d);
    }
    return th;
  }

  function cellContent(c, r) {
    const v = c.cell ? c.cell(r) : r[c.key];
    return v == null ? '' : v;
  }

  function rowEl(r, i) {
    const href = opts.rowHref ? opts.rowHref(r) : null;
    const key = rowKey(r, i);
    const clickable = opts.onRowClick ? (opts.rowClickable ? opts.rowClickable(r) : !!href) : !!href;
    const tr = h('tr', {
      class: [clickable ? 'clickable' : '', opts.rowClass ? opts.rowClass(r) : ''].join(' ').trim(),
      tabindex: clickable ? '0' : null,
      'data-key': key,
      'data-label': opts.rowLabel ? opts.rowLabel(r) : null,
      'data-href': href || null,
    });
    if (opts.expand) {
      const open = expanded.has(key);
      const btn = h('button', { type: 'button', class: 'caret', 'aria-expanded': String(open), title: opts.expandLabel || 'Show ads', 'aria-label': opts.expandLabel || 'Show ads' }, open ? '▾' : '▸');
      btn.addEventListener('click', (e) => { e.stopPropagation(); toggle(key, r); });
      tr.append(h('td', { class: 'caretcell' }, btn));
    }
    for (const c of visCols) {
      tr.append(h('td', {
        class: [c.num ? 'num' : '', c.sticky ? 'sticky' : '', c.cls || ''].join(' ').trim(),
        title: c.cellTitle ? c.cellTitle(r) : null,
      }, cellContent(c, r)));
    }
    if (clickable) {
      const act = (e) => { if (opts.onRowClick) opts.onRowClick(r, e, { key, href, toggle: () => toggle(key, r) }); else navigate(href); };
      tr.addEventListener('click', (e) => {
        if (e.target.closest('a,button,input,select,label,summary,textarea')) return;
        act(e);
      });
      tr.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && e.target === tr) act(e);
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

  // footer rows: the main "Total" plus optional extra rows (opts.extraTotals: [{label, total, cls}])
  function totalRow(t, label, cls) {
    const tr = h('tr', { class: 'total ' + (cls || '') });
    if (opts.expand) tr.append(h('td', { class: 'caretcell' }));
    visCols.forEach((c, i) => {
      const v = i === 0 && label != null ? label : (c.total ? c.total(t) : (i === 0 ? 'Total' : ''));
      tr.append(h('td', { class: [c.num ? 'num' : '', c.sticky ? 'sticky' : '', c.cls || ''].join(' ').trim() }, v == null ? '' : v));
    });
    return tr;
  }

  function render() {
    visCols = columns.filter((c) => !(c.hidden && c.hidden()));
    colCount = visCols.length + (opts.expand ? 1 : 0);
    const list = view();
    const shown = list.slice(0, limit);
    const table = h('table', { class: 'dt ' + (opts.tableClass || '') });
    const headRow = h('tr');
    if (opts.expand) headRow.append(h('th', { class: 'caretcell' }));
    visCols.forEach((c) => headRow.append(headerCell(c)));
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
    if (total) {
      const extra = (opts.extraTotals || []).filter((x) => x && x.total);
      table.append(h('tfoot', totalRow(total, opts.totalLabel), ...extra.map((x) => totalRow(x.total, x.label, x.cls))));
    }
    const st = wrap.scrollTop, sl = wrap.scrollLeft;
    // keep keyboard focus on the same row / caret / header button across a re-render
    const ae = document.activeElement;
    let refocus = null;
    if (ae && wrap.contains(ae)) {
      const tr = ae.closest('tbody tr[data-key]');
      const th = ae.closest('thead th');
      if (tr) refocus = { row: tr.dataset.key, sel: ae === tr ? null : (ae.classList.contains('caret') ? 'button.caret' : null) };
      else if (th) refocus = { head: th.dataset.col, sel: ae.classList.contains('deltabtn') ? 'button.deltabtn' : 'button.thbtn' };
    }
    wrap.replaceChildren(table);
    // several footer rows pin on top of each other: each one sits above the rows below it
    const frows = table.tFoot ? [...table.tFoot.rows] : [];
    if (frows.length > 1) {
      let off = 0;
      for (let i = frows.length - 1; i >= 0; i--) {
        for (const td of frows[i].cells) td.style.bottom = off + 'px';
        off += frows[i].offsetHeight;
      }
    }
    wrap.scrollTop = st; wrap.scrollLeft = sl;
    if (refocus) {
      let target = null;
      if (refocus.row != null) {
        const tr = [...table.querySelectorAll('tbody tr[data-key]')].find((x) => x.dataset.key === refocus.row);
        target = tr ? (refocus.sel ? tr.querySelector(refocus.sel) : tr) : null;
      } else {
        const th = [...table.querySelectorAll('thead th')].find((x) => x.dataset.col === refocus.head);
        target = th ? th.querySelector(refocus.sel) : null;
      }
      if (target) target.focus({ preventScroll: true });
    }

    foot.replaceChildren();
    if (list.length > shown.length) {
      foot.append(
        h('span', { class: 'muted' }, `Showing ${shown.length.toLocaleString('en-US')} of ${list.length.toLocaleString('en-US')} `),
        h('button', { type: 'button', class: 'btn small', onclick: () => { limit += pageSize; render(); } }, `Show ${Math.min(pageSize, list.length - shown.length)} more`),
        h('button', { type: 'button', class: 'btn small', onclick: () => { limit = list.length; render(); } }, 'Show all'),
      );
    }
    if (opts.onCount) opts.onCount(list.length, rows.length);
    updateHint();
  }

  function updateHint() {
    const left = wrap.scrollWidth - wrap.clientWidth - wrap.scrollLeft;
    if (!wrap.clientWidth || left <= 4) { hint.hidden = true; hint.replaceChildren(); return; }
    const wr = wrap.getBoundingClientRect();
    const names = [...wrap.querySelectorAll(':scope > table > thead th[data-col]')]
      .filter((th) => th.getBoundingClientRect().right > wr.right + 1)
      .map((th) => { const c = columns.find((x) => x.key === th.dataset.col); return c ? labelOf(c) : null; })
      .filter((x) => x && String(x).trim());
    hint.replaceChildren(
      h('span', { class: 'muted' }, names.length ? `More columns to the right: ${names.join(', ')}` : 'More columns to the right'),
      h('button', { type: 'button', class: 'btn small', onclick: () => wrap.scrollBy({ left: Math.round(wrap.clientWidth * 0.8), behavior: 'smooth' }) }, 'Scroll right ›'));
    hint.hidden = false;
  }
  wrap.addEventListener('scroll', updateHint, { passive: true });
  if (typeof ResizeObserver !== 'undefined') new ResizeObserver(() => updateHint()).observe(wrap);

  render();
  onCmpRefresh(render); // per-day / column-group toggles re-render without a refetch
  return {
    el: root,
    wrap,
    refresh: render,
    toggle: (key, r) => toggle(key, r),
    setRows(r, t) { rows = r; if (t !== undefined) total = t; limit = pageSize; render(); },
    setFilter(fn) { filter = fn; limit = pageSize; render(); },
    sortState: () => ({ key: sortKey, dir: sortDir, by: sortBy }),
  };
}
