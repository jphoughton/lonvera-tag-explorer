// Projection UI: the shared monthly grid. Months are columns, lines are rows.
//
//   const grid = new Grid(el, { payload, onEdit, frozenCols: 1, density: 'compact', groupCollapse: true,
//                               yearBands: true, fy: false, highlight: null, readOnly: false, title: null, toolbar: true });
//   grid.update(payload)            // re-render, keeps scroll position and selection
//
// payload = {months:[iso], status:['actual'|'projected'], rows:[{key,label,kind,fmt,values,input,group?,note?,agg?,digits?,indent?}],
//            captions:[str], fy?:{years:[2025..], rows:{key:[per-year values]}}}
//   kind: input | calc | subtotal | total | header | ratio      fmt: usd | usd0 | pct | num | x | text | date
//   agg (FY column): 'sum' | 'avg' | 'last' | 'none'; default sum for money / units, none for rates and text.
//
// onEdit(edits) -> Promise; edits = [{line_key, month, value|null, previous}] (one entry per changed cell; a paste is one call).
//   Resolve = saved (cells flash green). Reject = not saved (cells revert, error shown). If the promise resolves with a fresh
//   grid payload the grid updates itself with it.
// highlight: (cell) => 'good'|'warn'|'bad'|'info'|null with cell = {key, month, value, status, row}, or an array of row keys to emphasise.
import { h, toast } from './util.js';
import * as F from './format.js';

const DEFAULTS = { frozenCols: 1, density: 'compact', groupCollapse: true, yearBands: true, fy: false, highlight: null, readOnly: false, title: null, toolbar: true, captions: true, rowHeader: '', onEdit: null, onSelect: null };
const MAX_UNDO = 50;
let seq = 0;

function normalize(p) {
  const months = (p && p.months) || [];
  const status = months.map((_, i) => (p.status && p.status[i]) || 'actual');
  const rows = ((p && p.rows) || []).map((r) => ({
    ...r,
    kind: r.kind || 'calc',
    fmt: r.fmt || 'num',
    values: months.map((_, i) => (r.values && r.values[i] !== undefined ? r.values[i] : null)),
  }));
  let cur = null;
  for (const r of rows) {
    if (r.kind === 'header') { cur = r.group ?? r.key; r._gid = cur; } else r._gid = r.group ?? cur;
  }
  return { months, status, rows, captions: (p && p.captions) || [], fy: (p && p.fy) || null };
}
const same = (a, b) => (a == null && b == null) || (typeof a === 'number' && typeof b === 'number' && Math.abs(a - b) < 1e-9) || a === b;

export class Grid {
  constructor(host, opts = {}) {
    this.opts = { ...DEFAULTS, ...opts };
    this.host = host;
    this.gid = 'pgg' + (++seq);
    this.collapsed = new Set(Array.isArray(opts.groupCollapse) ? opts.groupCollapse : []);
    this.showFY = !!this.opts.fy;
    this.cellState = new Map();
    this.undoStack = [];
    this.sel = null;
    this.editing = null;
    this.pendingPayload = null;
    this.inflight = 0;
    this.timers = new Set();
    this.cellEls = [];
    this.thEls = [];
    this.trEls = [];
    this.hoverC = null;
    this._buildFrame();
    if (opts.payload) this.update(opts.payload);
  }

  // ------------------------------------------------------------------ public API
  get el() { return this.root; }

  update(payload) {
    if (this.editing) { this.pendingPayload = payload; return; }
    const keep = this._selKeys();
    this.payload = normalize(payload);
    this._render();
    if (keep) this._restoreSel(keep);
  }
  setOptions(o) {
    Object.assign(this.opts, o);
    if ('fy' in o) this.showFY = !!o.fy;
    if (this.payload) this.update({ ...this.payload });
    this._syncToolbar();
  }
  focus() { if (this.table) this.table.focus({ preventScroll: true }); }
  select(rowKey, month, extend = false) {
    const r = this._rowIndex(rowKey);
    const c = this._colIndexByIso(month);
    if (r < 0 || c < 0) return;
    this._setSel(r, c, extend);
  }
  getSelection() {
    if (!this.sel) return { cells: [], rows: [], months: [] };
    const { r1, r2, c1, c2 } = this._rect();
    const cells = [];
    const rows = new Set();
    const months = new Set();
    for (const r of this._navRows()) {
      if (r < r1 || r > r2) continue;
      for (let c = c1; c <= c2; c++) {
        const col = this.cols[c];
        const row = this.payload.rows[r];
        cells.push({ key: row.key, month: col.iso || col.id, value: this._valueAt(r, c) });
        rows.add(row.key);
        months.add(col.iso || col.id);
      }
    }
    return { cells, rows: [...rows], months: [...months] };
  }
  toTSV({ selectionOnly = false } = {}) {
    const { rows } = this.payload;
    const lines = [];
    if (selectionOnly && this.sel) {
      const { r1, r2, c1, c2 } = this._rect();
      for (const r of this._navRows()) {
        if (r < r1 || r > r2) continue;
        const out = [];
        for (let c = c1; c <= c2; c++) out.push(this._plainAt(r, c));
        lines.push(out.join('\t'));
      }
      return lines.join('\n');
    }
    lines.push(['', ...this.cols.map((c) => c.label)].join('\t'));
    for (let r = 0; r < rows.length; r++) {
      if (this._hiddenRow(r)) continue;
      if (rows[r].kind === 'header') { lines.push(rows[r].label); continue; }
      lines.push([rows[r].label, ...this.cols.map((_, c) => this._plainAt(r, c))].join('\t'));
    }
    return lines.join('\n');
  }
  async copy({ selectionOnly = false } = {}) {
    const text = this.toTSV({ selectionOnly });
    await writeClipboard(text);
    toast(selectionOnly ? 'Copied selection' : 'Copied table (tab-separated)');
    return text;
  }
  undo() {
    const entry = this.undoStack.pop();
    this._syncToolbar();
    if (!entry) return Promise.resolve();
    const changes = [];
    for (const e of entry) {
      const r = this._rowIndex(e.line_key);
      const c = this._colIndexByIso(e.month);
      if (r >= 0 && c >= 0) changes.push({ r, c, value: e.previous });
    }
    return this._commit(changes, { undo: true });
  }
  collapseAll() { for (const r of this.payload.rows) if (r.kind === 'header') this.collapsed.add(r._gid); this._applyCollapse(); }
  expandAll() { this.collapsed.clear(); this._applyCollapse(); }
  toggleGroup(gid) { if (this.collapsed.has(gid)) this.collapsed.delete(gid); else this.collapsed.add(gid); this._applyCollapse(); }
  destroy() {
    for (const t of this.timers) clearTimeout(t);
    this.timers.clear();
    this.host.replaceChildren();
  }

  // ------------------------------------------------------------------ frame + toolbar
  _buildFrame() {
    this.statusEl = h('span', { class: 'pg-gstatus', 'aria-live': 'polite' });
    this.btnCollapse = h('button', { type: 'button', class: 'btn small', onclick: () => this.collapseAll() }, 'Collapse all');
    this.btnExpand = h('button', { type: 'button', class: 'btn small', onclick: () => this.expandAll() }, 'Expand all');
    this.btnFY = h('button', { type: 'button', class: 'btn small pg-tog', 'aria-pressed': String(this.showFY), onclick: () => this.setOptions({ fy: !this.showFY }) }, 'FY totals');
    this.btnUndo = h('button', { type: 'button', class: 'btn small', disabled: true, title: 'Undo last edit (Ctrl/Cmd+Z)', onclick: () => this.undo() }, 'Undo');
    this.btnCopy = h('button', { type: 'button', class: 'btn small', title: 'Copy the table as tab-separated text', onclick: () => this.copy().catch(() => toast('Copy failed', 'error')) }, 'Copy');
    this.toolbar = h('div', { class: 'pg-toolbar', hidden: !this.opts.toolbar },
      this.opts.title ? h('strong', { class: 'pg-gtitle' }, this.opts.title) : null,
      h('span', { class: 'spacer' }),
      this.statusEl, this.btnCollapse, this.btnExpand, this.btnFY, this.btnUndo, this.btnCopy);
    this.scroll = h('div', { class: 'pg-scroll' });
    this.captionEl = h('div', { class: 'pg-captions' });
    this.root = h('div', { class: 'pg-grid', 'data-density': this.opts.density }, this.toolbar, this.scroll, this.captionEl);
    this.host.replaceChildren(this.root);
  }
  _syncToolbar() {
    this.btnFY.setAttribute('aria-pressed', String(this.showFY));
    this.btnUndo.disabled = this.undoStack.length === 0;
    const groups = this.payload && this.opts.groupCollapse !== false && this.payload.rows.some((r) => r.kind === 'header');
    this.btnCollapse.hidden = !groups;
    this.btnExpand.hidden = !groups;
    this.root.dataset.density = this.opts.density;
  }
  _setStatus(text, kind = '') {
    this.statusEl.textContent = text;
    this.statusEl.className = 'pg-gstatus ' + kind;
  }
  _later(fn, ms) {
    const t = setTimeout(() => { this.timers.delete(t); fn(); }, ms);
    this.timers.add(t);
  }

  // ------------------------------------------------------------------ columns / values
  _buildCols() {
    const { months, status } = this.payload;
    const cols = [];
    const years = [];
    months.forEach((iso, i) => {
      const y = F.yearOf(iso);
      cols.push({ id: iso, type: 'month', i, iso, year: y, status: status[i], label: F.monthLabel(iso), short: F.shortMonth(iso) });
      const nextY = i + 1 < months.length ? F.yearOf(months[i + 1]) : null;
      let yr = years[years.length - 1];
      if (!yr || yr.year !== y) { yr = { year: y, from: cols.length - 1, n: 0 }; years.push(yr); }
      yr.n++;
      if (nextY !== y) {
        if (this.showFY) {
          const sts = months.map((m, k) => (F.yearOf(m) === y ? status[k] : null)).filter(Boolean);
          const st = sts.every((s) => s === 'projected') ? 'projected' : sts.every((s) => s === 'actual') ? 'actual' : 'mixed';
          cols.push({ id: 'fy:' + y, type: 'fy', year: y, status: st, label: 'FY' + String(y).slice(-2) });
          yr.n++;
        }
      }
    });
    this.cols = cols;
    this.years = years;
    this.firstProj = cols.findIndex((c, k) => c.type === 'month' && c.status === 'projected' && (k === 0 || cols[k - 1].status !== 'projected'));
  }
  _fyValue(row, year) {
    const fy = this.payload.fy;
    if (fy && fy.rows && fy.rows[row.key] && fy.years) {
      const k = fy.years.indexOf(year);
      return k >= 0 ? fy.rows[row.key][k] : null;
    }
    const agg = row.agg || F.defaultAgg(row.fmt, row.kind);
    if (agg === 'none') return null;
    const vals = [];
    this.payload.months.forEach((m, i) => { if (F.yearOf(m) === year && typeof row.values[i] === 'number' && Number.isFinite(row.values[i])) vals.push(row.values[i]); });
    if (!vals.length) return null;
    if (agg === 'last') return vals[vals.length - 1];
    const s = vals.reduce((a, b) => a + b, 0);
    return agg === 'avg' ? s / vals.length : s;
  }
  _valueAt(r, c) {
    const row = this.payload.rows[r];
    const col = this.cols[c];
    return col.type === 'month' ? row.values[col.i] : this._fyValue(row, col.year);
  }
  _plainAt(r, c) {
    const row = this.payload.rows[r];
    if (row.kind === 'header') return '';
    const v = this._valueAt(r, c);
    return v == null ? '' : F.plain(v, row.fmt);
  }
  _rowIndex(key) { return this.payload.rows.findIndex((r) => r.key === key); }
  _colIndexByIso(iso) { return this.cols.findIndex((c) => c.id === iso); }
  _editable(r, c) {
    const row = this.payload.rows[r];
    const col = this.cols[c];
    return !!(this.opts.onEdit && !this.opts.readOnly && row && row.input && row.kind !== 'header' && col && col.type === 'month');
  }
  _hiddenRow(r) {
    const row = this.payload.rows[r];
    if (row.kind === 'header' || row.kind === 'subtotal' || row.kind === 'total') return false;
    return !!(row._gid != null && this.collapsed.has(row._gid));
  }
  _navRows() {
    const out = [];
    this.payload.rows.forEach((row, r) => { if (row.kind !== 'header' && !this._hiddenRow(r)) out.push(r); });
    return out;
  }

  // ------------------------------------------------------------------ render
  _render() {
    const sc = this.scroll;
    const pos = { l: sc.scrollLeft, t: sc.scrollTop };
    this._buildCols();
    const { rows, captions } = this.payload;
    const { cols } = this;
    const yb = this.opts.yearBands;
    this.cellEls = rows.map(() => []);
    this.trEls = [];
    this.thEls = [];

    const colgroup = h('colgroup', null, h('col', { class: 'pg-cl' }), cols.map((c) => h('col', { class: 'pg-cc' + (c.type === 'fy' ? ' fy' : '') })));
    const frozen = Math.max(0, (this.opts.frozenCols || 1) - 1);       // month columns frozen after the label
    const corner = (rowspan) => h('th', { class: 'pg-th pg-corner', scope: 'col', rowspan: rowspan > 1 ? rowspan : null }, this.opts.rowHeader || '');
    const monthTh = (c, k) => {
      const th = h('th', {
        class: `pg-th pg-mh s-${c.status}` + (c.type === 'fy' ? ' pg-fy' : '') + (k === this.firstProj ? ' pg-bound' : '') + (c.type === 'month' && c.i < frozen ? ' pg-frozen' : ''),
        scope: 'col', 'data-c': k, title: c.type === 'fy' ? `${c.year} total` + (c.status !== 'actual' ? ' (includes projected months)' : '') : c.label + (c.status === 'projected' ? ' (projected)' : ''),
        style: c.type === 'month' && c.i < frozen ? { left: `calc(var(--pg-label-w) + ${c.i} * var(--pg-col-w))` } : null,
      }, yb || c.type === 'fy' ? (c.type === 'fy' ? c.label : c.short) : c.label);
      this.thEls[k] = th;
      return th;
    };
    const thead = h('thead', null);
    if (yb) {
      thead.append(h('tr', { class: 'pg-yearrow' }, corner(2), this.years.map((y) => {
        const slice = cols.slice(y.from, y.from + y.n);
        const proj = slice.every((c) => c.status === 'projected');
        return h('th', { class: 'pg-th pg-yh' + (proj ? ' s-projected' : ''), colspan: y.n, scope: 'colgroup' }, String(y.year));
      })));
      thead.append(h('tr', { class: 'pg-monthrow' }, cols.map((c, k) => monthTh(c, k))));
    } else {
      thead.append(h('tr', { class: 'pg-monthrow' }, corner(1), cols.map((c, k) => monthTh(c, k))));
    }

    const hlFn = typeof this.opts.highlight === 'function' ? this.opts.highlight : null;
    const hlRows = Array.isArray(this.opts.highlight) ? new Set(this.opts.highlight) : null;
    const showCaret = this.opts.groupCollapse !== false;
    const tbody = h('tbody', null);
    rows.forEach((row, r) => {
      const tr = h('tr', { class: `pg-row kind-${row.kind}` + (hlRows && hlRows.has(row.key) ? ' pg-hl' : ''), 'data-key': row.key, 'data-r': r });
      this.trEls[r] = tr;
      const indent = row.indent != null ? row.indent : (row.kind === 'ratio' ? 2 : row.kind === 'calc' || row.kind === 'input' ? 1 : 0);
      const labelKids = [];
      if (row.kind === 'header' && showCaret) {
        const open = !this.collapsed.has(row._gid);
        labelKids.push(h('button', { type: 'button', class: 'pg-caret', 'aria-expanded': String(open), 'aria-label': `${open ? 'Collapse' : 'Expand'} ${row.label}`, 'data-gid': row._gid }, open ? '▾' : '▸'));
      }
      labelKids.push(h('span', { class: 'pg-lt' }, row.label));
      if (row.note) labelKids.push(h('span', { class: 'pg-note', title: row.note, 'aria-label': row.note }, 'i'));
      tr.append(h('th', { class: 'pg-label', scope: 'row', title: row.label, style: { paddingLeft: `calc(var(--pg-pad) + ${indent * 12}px)` } }, labelKids));
      if (row.kind === 'header') {
        tr.append(h('td', { class: 'pg-band', colspan: cols.length }));
      } else {
        cols.forEach((col, c) => {
          const td = h('td', { role: 'gridcell', id: `${this.gid}-${r}-${c}`, 'data-r': r, 'data-c': c });
          this.cellEls[r][c] = td;
          this._paintCell(td, r, c, hlFn);
          tr.append(td);
        });
      }
      tbody.append(tr);
    });
    this.tbody = tbody;
    this.thead = thead;

    const table = h('table', { class: 'pg-table', role: 'grid', tabindex: '0', 'aria-label': this.opts.title || 'Monthly projection', 'aria-rowcount': rows.length, 'aria-colcount': cols.length + 1, style: { '--pg-n': cols.length } }, colgroup, thead, tbody);
    this.table = table;
    this._wire(table);
    this.scroll.replaceChildren(table);
    sc.scrollLeft = pos.l;
    sc.scrollTop = pos.t;

    this.captionEl.replaceChildren(...(this.opts.captions ? captions.map((c) => h('div', { class: 'pg-caption' }, c)) : []));
    this._applyCollapse();
    this._syncToolbar();
    this._paintSel();
  }

  _paintCell(td, r, c, hlFn = typeof this.opts.highlight === 'function' ? this.opts.highlight : null) {
    const row = this.payload.rows[r];
    const col = this.cols[c];
    const v = this._valueAt(r, c);
    const d = F.display(v, row.fmt, { digits: row.digits });
    const projected = col.status === 'projected' || col.status === 'mixed';
    const st = this.cellState.get(row.key + '|' + col.id);
    let cls = `pg-cell f-${row.fmt} s-${col.status}`;
    if (col.type === 'fy') cls += ' pg-fy';
    if (c === this.firstProj) cls += ' pg-bound';
    if (col.type === 'month' && col.i < Math.max(0, (this.opts.frozenCols || 1) - 1)) cls += ' pg-frozen';
    if (this._editable(r, c)) cls += ' pg-in';
    if (d.neg) cls += ' neg';
    if (d.zero) cls += ' zero';
    if (st) cls += ' pg-' + st.state;
    if (hlFn) {
      const tone = hlFn({ key: row.key, month: col.iso || col.id, value: v, status: col.status, row });
      if (tone) cls += ' tone-' + tone;
    }
    td.className = cls + (this._isSelCell(r, c) ? ' pg-range' : '') + (this.hoverC === c ? ' pg-colhover' : '');
    if (st && st.state === 'error' && st.msg) td.title = st.msg; else td.removeAttribute('title');
    if (col.type === 'month' && col.i < Math.max(0, (this.opts.frozenCols || 1) - 1)) td.style.left = `calc(var(--pg-label-w) + ${col.i} * var(--pg-col-w))`;
    if (row.fmt === 'text' && !d.blank) {
      const tone = F.statusTone(d.text);
      td.replaceChildren(tone ? h('span', { class: 'pg-chip ' + tone }, d.text) : d.text);
    } else if (projected && !d.blank && !d.zero && row.fmt !== 'text' && row.fmt !== 'date') {
      td.replaceChildren(d.text, h('span', { class: 'pg-ast' }, '*'));
    } else {
      td.textContent = d.text;
    }
  }
  _repaintRow(r) {
    const tds = this.cellEls[r];
    if (!tds) return;
    tds.forEach((td, c) => { if (td) this._paintCell(td, r, c); });
  }

  _applyCollapse() {
    this.payload.rows.forEach((row, r) => { if (this.trEls[r]) this.trEls[r].hidden = this._hiddenRow(r); });
    for (const b of this.table.querySelectorAll('.pg-caret')) {
      const open = !this.collapsed.has(b.dataset.gid);
      b.textContent = open ? '▾' : '▸';
      b.setAttribute('aria-expanded', String(open));
    }
    if (this.sel && this._hiddenRow(this.sel.r)) { const n = this._navRows(); if (n.length) { this.sel.r = this.sel.r2 = n[0]; } }
    this._paintSel();
  }

  // ------------------------------------------------------------------ events
  _wire(table) {
    table.addEventListener('click', (e) => this._onClick(e));
    table.addEventListener('dblclick', (e) => {
      const td = e.target.closest('td.pg-cell');
      if (td && !this.editing) this._beginEdit(+td.dataset.r, +td.dataset.c);
    });
    table.addEventListener('keydown', (e) => this._onKey(e));
    table.addEventListener('copy', (e) => {
      if (this.editing || !this.sel) return;
      e.preventDefault();
      e.clipboardData.setData('text/plain', this.toTSV({ selectionOnly: true }));
    });
    table.addEventListener('paste', (e) => {
      if (this.editing || !this.sel) return;
      e.preventDefault();
      this._paste(e.clipboardData.getData('text/plain'));
    });
    table.addEventListener('mouseover', (e) => {
      const el = e.target.closest('td.pg-cell, th.pg-mh');
      this._hover(el ? +el.dataset.c : null);
    });
    table.addEventListener('mouseleave', () => this._hover(null));
  }
  _hover(c) {
    if (c === this.hoverC) return;
    const apply = (k, on) => {
      if (k == null) return;
      if (this.thEls[k]) this.thEls[k].classList.toggle('pg-colhover', on);
      for (const tds of this.cellEls) if (tds[k]) tds[k].classList.toggle('pg-colhover', on);
    };
    apply(this.hoverC, false);
    this.hoverC = c;
    apply(c, true);
  }
  _onClick(e) {
    const caret = e.target.closest('.pg-caret');
    if (caret) { this.toggleGroup(caret.dataset.gid); return; }
    const td = e.target.closest('td.pg-cell');
    if (!td || td.querySelector('.pg-editor')) return;
    const r = +td.dataset.r;
    const c = +td.dataset.c;
    if (e.shiftKey) { this._setSel(r, c, true); return; }
    this._setSel(r, c, false);
    if (!e.metaKey && !e.ctrlKey && this._editable(r, c)) this._beginEdit(r, c);
  }
  _onKey(e) {
    if (e.target.closest && e.target.closest('.pg-editor')) return;
    if (e.target.closest && e.target.closest('.pg-caret') && (e.key === 'Enter' || e.key === ' ')) return;
    const mod = e.ctrlKey || e.metaKey;
    if (mod && (e.key === 'z' || e.key === 'Z') && !e.shiftKey) { e.preventDefault(); this.undo(); return; }
    if (mod) return;                                                   // copy / paste handled by their own events
    if (!this.sel) {
      const n = this._navRows();
      if (n.length && ['ArrowDown', 'ArrowUp', 'ArrowLeft', 'ArrowRight', 'Enter', 'Home'].includes(e.key)) { e.preventDefault(); this._setSel(n[0], 0, false); }
      return;
    }
    const nav = this._navRows();
    const pos = nav.indexOf(this.sel.r2 != null && e.shiftKey ? this.sel.r2 : this.sel.r);
    const lastC = this.cols.length - 1;
    const curC = e.shiftKey && this.sel.c2 != null ? this.sel.c2 : this.sel.c;
    const go = (r, c) => { e.preventDefault(); this._setSel(Math.max(0, r), Math.min(lastC, Math.max(0, c)), e.shiftKey); };
    switch (e.key) {
      case 'ArrowDown': go(nav[Math.min(nav.length - 1, pos + 1)], curC); return;
      case 'ArrowUp': go(nav[Math.max(0, pos - 1)], curC); return;
      case 'ArrowRight': go(nav[pos], curC + 1); return;
      case 'ArrowLeft': go(nav[pos], curC - 1); return;
      case 'PageDown': go(nav[Math.min(nav.length - 1, pos + 10)], curC); return;
      case 'PageUp': go(nav[Math.max(0, pos - 10)], curC); return;
      case 'Home': go(e.ctrlKey ? nav[0] : nav[pos], 0); return;
      case 'End': go(nav[pos], lastC); return;
      case 'Escape': this._setSel(this.sel.r, this.sel.c, false); return;
      case 'Tab': {
        const c = this.sel.c + (e.shiftKey ? -1 : 1);
        if (c < 0 || c > lastC) {
          const p = nav.indexOf(this.sel.r) + (e.shiftKey ? -1 : 1);
          if (p < 0 || p >= nav.length) return;                        // leave the grid
          e.preventDefault();
          this._setSel(nav[p], e.shiftKey ? lastC : 0, false);
        } else { e.preventDefault(); this._setSel(this.sel.r, c, false); }
        return;
      }
      case 'Enter':
      case 'F2':
        e.preventDefault();
        if (this._editable(this.sel.r, this.sel.c)) this._beginEdit(this.sel.r, this.sel.c);
        else if (e.key === 'Enter') this._setSel(nav[Math.min(nav.length - 1, nav.indexOf(this.sel.r) + 1)], this.sel.c, false);
        return;
      case 'Delete':
      case 'Backspace': {
        e.preventDefault();
        const ch = this._rangeCells().filter(([r, c]) => this._editable(r, c)).map(([r, c]) => ({ r, c, value: null }));
        if (ch.length) this._commit(ch);
        return;
      }
      default:
        if (e.key.length === 1 && !e.altKey && this._editable(this.sel.r, this.sel.c)) {
          e.preventDefault();
          this._beginEdit(this.sel.r, this.sel.c, e.key);
        }
    }
  }

  // ------------------------------------------------------------------ selection
  _rect() {
    const s = this.sel;
    const r2 = s.r2 ?? s.r;
    const c2 = s.c2 ?? s.c;
    return { r1: Math.min(s.r, r2), r2: Math.max(s.r, r2), c1: Math.min(s.c, c2), c2: Math.max(s.c, c2) };
  }
  _isSelCell(r, c) {
    if (!this.sel) return false;
    const q = this._rect();
    return r >= q.r1 && r <= q.r2 && c >= q.c1 && c <= q.c2 && (q.r1 !== q.r2 || q.c1 !== q.c2);
  }
  _rangeCells() {
    const q = this._rect();
    const out = [];
    for (const r of this._navRows()) if (r >= q.r1 && r <= q.r2) for (let c = q.c1; c <= q.c2; c++) out.push([r, c]);
    return out;
  }
  _setSel(r, c, extend) {
    if (extend && this.sel) { this.sel.r2 = r; this.sel.c2 = c; } else this.sel = { r, c, r2: null, c2: null };
    this._paintSel();
    const td = this.cellEls[this.sel.r2 != null && extend ? this.sel.r2 : this.sel.r][this.sel.c2 != null && extend ? this.sel.c2 : this.sel.c];
    if (td) this._ensureVisible(td);
    if (this.opts.onSelect) {
      const row = this.payload.rows[this.sel.r];
      this.opts.onSelect({ key: row.key, month: this.cols[this.sel.c].iso || this.cols[this.sel.c].id, value: this._valueAt(this.sel.r, this.sel.c), row });
    }
  }
  _paintSel() {
    for (const el of this.table ? this.table.querySelectorAll('.pg-sel, .pg-range') : []) el.classList.remove('pg-sel', 'pg-range');
    this.table && this.table.removeAttribute('aria-activedescendant');
    if (!this.sel) return;
    const { r, c } = this.sel;
    if (this.sel.r2 != null || this.sel.c2 != null) for (const [rr, cc] of this._rangeCells()) { const t = this.cellEls[rr][cc]; if (t) t.classList.add('pg-range'); }
    const td = this.cellEls[r] && this.cellEls[r][c];
    if (td) { td.classList.add('pg-sel'); this.table.setAttribute('aria-activedescendant', td.id); }
  }
  _selKeys() {
    if (!this.sel || !this.payload) return null;
    const row = this.payload.rows[this.sel.r];
    const col = this.cols && this.cols[this.sel.c];
    return row && col ? { key: row.key, id: col.id } : null;
  }
  _restoreSel(k) {
    const r = this._rowIndex(k.key);
    const c = this._colIndexByIso(k.id);
    this.sel = r >= 0 && c >= 0 ? { r, c, r2: null, c2: null } : null;
    this._paintSel();
  }
  _ensureVisible(td) {
    const sc = this.scroll;
    const sr = sc.getBoundingClientRect();
    const tr = td.getBoundingClientRect();
    const corner = this.thead.querySelector('.pg-corner');
    let leftEdge = sr.left + (corner ? corner.offsetWidth : 0);
    for (const th of this.thead.querySelectorAll('.pg-mh.pg-frozen')) leftEdge += th.offsetWidth;
    const topEdge = sr.top + this.thead.offsetHeight;
    if (tr.left < leftEdge) sc.scrollLeft -= leftEdge - tr.left;
    else if (tr.right > sr.right) sc.scrollLeft += tr.right - sr.right;
    if (tr.top < topEdge) sc.scrollTop -= topEdge - tr.top;
    else if (tr.bottom > sr.bottom) sc.scrollTop += tr.bottom - sr.bottom;
  }
  _move(dr, dc) {
    const nav = this._navRows();
    const p = nav.indexOf(this.sel.r);
    const r = nav[Math.max(0, Math.min(nav.length - 1, p + dr))];
    const c = Math.max(0, Math.min(this.cols.length - 1, this.sel.c + dc));
    this._setSel(r, c, false);
    this.focus();
  }

  // ------------------------------------------------------------------ editing
  _beginEdit(r, c, typed = null) {
    if (!this._editable(r, c) || this.editing) return;
    const row = this.payload.rows[r];
    const td = this.cellEls[r][c];
    if (!this.sel || this.sel.r !== r || this.sel.c !== c) this._setSel(r, c, false);
    const input = h('input', {
      class: 'pg-editor', type: 'text', autocomplete: 'off', spellcheck: 'false', inputmode: row.fmt === 'text' || row.fmt === 'date' ? 'text' : 'decimal',
      'aria-label': `${row.label}, ${this.cols[c].label}`,
    });
    input.value = typed != null ? typed : F.editText(row.values[this.cols[c].i], row.fmt);
    this.editing = { r, c, td, input, done: false };
    td.classList.add('pg-editing');
    td.replaceChildren(input);
    input.focus();
    if (typed == null) input.select(); else input.setSelectionRange(input.value.length, input.value.length);
    input.addEventListener('keydown', (e) => {
      e.stopPropagation();
      if (e.key === 'Enter') { e.preventDefault(); this._endEdit(true, e.shiftKey ? -1 : 1, 0, true); }
      else if (e.key === 'Tab') { e.preventDefault(); this._endEdit(true, 0, e.shiftKey ? -1 : 1, true); }
      else if (e.key === 'Escape') { e.preventDefault(); this._endEdit(false); }
      else if (e.key === 'ArrowDown') { e.preventDefault(); this._endEdit(true, 1, 0, true); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); this._endEdit(true, -1, 0, true); }
    });
    input.addEventListener('input', () => { input.classList.remove('invalid'); input.removeAttribute('title'); });
    input.addEventListener('blur', () => { if (this.editing && this.editing.input === input) this._endEdit(true, 0, 0, false); });
    input.addEventListener('paste', (e) => {
      const t = e.clipboardData.getData('text/plain');
      if (/[\t\n]/.test(t.replace(/[\r\n]+$/, ''))) {
        e.preventDefault();
        const { r: er, c: ec } = this.editing;
        this._endEdit(false);
        this._setSel(er, ec, false);
        this._paste(t);
      }
    });
  }
  // commit = true parses and saves; false restores. keepOpen: an invalid entry keeps the editor open (Enter / Tab), blur cancels it.
  _endEdit(commit, dr = 0, dc = 0, keepOpen = false) {
    const ed = this.editing;
    if (!ed || ed.done) return;
    const row = this.payload.rows[ed.r];
    let change = null;
    if (commit) {
      const p = F.parse(ed.input.value, row.fmt);
      if (!p.ok) {
        if (keepOpen) { ed.input.classList.add('invalid'); this._setStatus(p.error, 'error'); ed.input.title = p.error; return; }
        this._setStatus(`Not saved: ${p.error}`, 'error');
      } else change = { r: ed.r, c: ed.c, value: p.value };
    }
    ed.done = true;
    this.editing = null;
    ed.td.classList.remove('pg-editing');
    this._paintCell(ed.td, ed.r, ed.c);
    if (change) this._commit([change]);
    if (this.pendingPayload) { const p = this.pendingPayload; this.pendingPayload = null; this.update(p); }
    if (keepOpen && (dr || dc)) this._move(dr, dc); else if (keepOpen) this.focus();
    else if (document.activeElement === document.body) this.focus();
  }

  async _commit(changes, { undo = false } = {}) {
    const rows = this.payload.rows;
    const edits = [];
    for (const ch of changes) {
      const row = rows[ch.r];
      const col = this.cols[ch.c];
      if (!row || !col || col.type !== 'month') continue;
      const prev = row.values[col.i];
      if (same(prev, ch.value)) continue;
      edits.push({ line_key: row.key, month: col.iso, value: ch.value, previous: prev });
    }
    if (!edits.length) return;
    this._applyValues(edits, 'value', 'saving');
    this.inflight++;
    this._setStatus('Saving…', 'saving');
    try {
      const out = await this.opts.onEdit(edits.map((e) => ({ ...e })));
      this._markState(edits, 'saved');
      if (!undo) { this.undoStack.push(edits); if (this.undoStack.length > MAX_UNDO) this.undoStack.shift(); }
      this._setStatus(undo ? 'Undone' : `Saved ${edits.length} cell${edits.length === 1 ? '' : 's'}`, 'ok');
      if (out && Array.isArray(out.rows) && Array.isArray(out.months)) this.update(out);
      this._later(() => { for (const e of edits) this._clearState(e); }, 1400);
    } catch (err) {
      if (err && err.name === 'AuthError') return;
      this._applyValues(edits, 'previous', null);
      this._markState(edits, 'error', err && err.message ? err.message : 'Not saved');
      this._setStatus(`Not saved: ${err && err.message ? err.message : 'error'}`, 'error');
      toast(`Not saved: ${err && err.message ? err.message : 'error'}`, 'error');
    } finally {
      this.inflight--;
      this._syncToolbar();
    }
  }
  _applyValues(edits, field, state) {
    for (const e of edits) {
      const r = this._rowIndex(e.line_key);
      const c = this._colIndexByIso(e.month);
      if (r < 0 || c < 0) continue;
      this.payload.rows[r].values[this.cols[c].i] = e[field];
      if (state) this.cellState.set(e.line_key + '|' + e.month, { state });
      else this.cellState.delete(e.line_key + '|' + e.month);
      this._repaintRow(r);
    }
  }
  _markState(edits, state, msg) {
    for (const e of edits) {
      this.cellState.set(e.line_key + '|' + e.month, { state, msg });
      const r = this._rowIndex(e.line_key);
      const c = this._colIndexByIso(e.month);
      if (r >= 0 && c >= 0 && this.cellEls[r][c]) this._paintCell(this.cellEls[r][c], r, c);
    }
  }
  _clearState(e) {
    const k = e.line_key + '|' + e.month;
    const st = this.cellState.get(k);
    if (!st || st.state !== 'saved') return;
    this.cellState.delete(k);
    const r = this._rowIndex(e.line_key);
    const c = this._colIndexByIso(e.month);
    if (r >= 0 && c >= 0 && this.cellEls[r] && this.cellEls[r][c]) this._paintCell(this.cellEls[r][c], r, c);
  }

  // paste tab-separated text starting at the selected cell: lines go down the rows, fields across the months
  _paste(text) {
    if (!text || !this.sel) return;
    const lines = text.replace(/\r\n?/g, '\n').replace(/\n+$/, '').split('\n').map((l) => l.split('\t'));
    const single = lines.length === 1 && lines[0].length === 1;
    const changes = [];
    let skipped = 0;
    if (single && (this.sel.r2 != null || this.sel.c2 != null)) {
      for (const [r, c] of this._rangeCells()) {
        if (!this._editable(r, c)) { skipped++; continue; }
        const p = F.parse(lines[0][0], this.payload.rows[r].fmt);
        if (p.ok) changes.push({ r, c, value: p.value }); else skipped++;
      }
    } else {
      const nav = this._navRows();
      const r0 = nav.indexOf(this.sel.r);
      const monthCols = this.cols.map((col, k) => (col.type === 'month' ? k : -1)).filter((k) => k >= 0);
      const m0 = monthCols.indexOf(this.sel.c);
      if (r0 < 0 || m0 < 0) { toast('Select a month cell to paste into', 'info'); return; }
      lines.forEach((fields, i) => {
        const r = nav[r0 + i];
        if (r == null) return;
        fields.forEach((f, j) => {
          const c = monthCols[m0 + j];
          if (c == null) return;
          if (f.trim() === '') return;
          if (!this._editable(r, c)) { skipped++; return; }
          const p = F.parse(f, this.payload.rows[r].fmt);
          if (p.ok) changes.push({ r, c, value: p.value }); else skipped++;
        });
      });
    }
    if (!changes.length) { toast(skipped ? `Nothing pasted: ${skipped} cell${skipped === 1 ? '' : 's'} not editable or not valid` : 'Nothing to paste', 'info'); return; }
    if (skipped) toast(`Pasted ${changes.length} cell${changes.length === 1 ? '' : 's'}; skipped ${skipped}`, 'info');
    return this._commit(changes);
  }
}

async function writeClipboard(text) {
  if (navigator.clipboard && window.isSecureContext) {
    try { await navigator.clipboard.writeText(text); return; } catch { /* fall back */ }
  }
  const ta = h('textarea', { style: { position: 'fixed', opacity: '0', left: '-1000px' } });
  ta.value = text;
  document.body.append(ta);
  ta.select();
  const ok = document.execCommand && document.execCommand('copy');
  ta.remove();
  if (!ok) throw new Error('clipboard unavailable');
}

export const createGrid = (el, opts) => new Grid(el, opts);
export { normalize as normalizePayload };
