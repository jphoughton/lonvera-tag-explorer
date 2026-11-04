// Projection UI: scenario store. One instance shared by the shell and (through ctx) by module pages.
//   scenarios.list / .slug / .current / .version
//   scenarios.on('list' | 'change' | 'version', fn) -> off()
import { createEmitter } from './util.js';
import * as api from './api.js';

const LS_KEY = 'projection.scenario';
const emitter = createEmitter();

function lsGet() { try { return localStorage.getItem(LS_KEY); } catch { return null; } }
function lsSet(v) { try { localStorage.setItem(LS_KEY, v); } catch { /* ignore */ } }

export const scenarios = {
  list: [],
  slug: null,
  on: emitter.on,

  get current() { return this.list.find((s) => s.slug === this.slug) || null; },
  get version() { const c = this.current; return c ? c.version : null; },
  get isDefault() { const c = this.current; return !!(c && c.is_default); },
  get defaultSlug() { const d = this.list.find((s) => s.is_default); return d ? d.slug : (this.list[0] ? this.list[0].slug : null); },
  has(slug) { return this.list.some((s) => s.slug === slug); },

  // load the list and pick a scenario: preferred (URL) > remembered > default
  async load(preferred) {
    const r = await api.get('/scenarios');
    this.list = (r && r.scenarios) || [];
    const pick = [preferred, lsGet(), this.defaultSlug].find((s) => s && this.has(s)) || null;
    const changed = pick !== this.slug;
    this.slug = pick;
    if (pick) lsSet(pick);
    emitter.emit('list', this.list);
    if (changed) emitter.emit('change', this.current);
    return this.current;
  },
  async refresh() {
    const r = await api.get('/scenarios');
    this.list = (r && r.scenarios) || [];
    if (!this.has(this.slug)) this.slug = this.defaultSlug;
    emitter.emit('list', this.list);
    return this.current;
  },
  select(slug, { silent = false } = {}) {
    if (!this.has(slug) || slug === this.slug) return;
    this.slug = slug;
    lsSet(slug);
    if (!silent) emitter.emit('change', this.current);
  },

  // create a scenario (blank per the server's rules) or clone one; returns the new slug
  async create(name, cloneFrom = null) {
    let res;
    try {
      res = await api.post('/scenarios', cloneFrom ? { name, clone_from: cloneFrom } : { name });
    } catch (e) {
      if (!(cloneFrom && (e.status === 404 || e.status === 405))) throw e;
      res = await api.post(`/scenarios/${encodeURIComponent(cloneFrom)}/clone`, { name });   // contract v1 spelling
    }
    const made = (res && (res.scenario || res)) || {};
    await this.refresh();
    const slug = made.slug && this.has(made.slug) ? made.slug : (this.list.find((s) => s.name === name) || {}).slug;
    if (slug) this.select(slug);
    return slug || null;
  },
  async rename(slug, name) {
    await api.patch(`/scenarios/${encodeURIComponent(slug)}`, { name });
    await this.refresh();
    emitter.emit('change', this.current);       // header text changed; modules re-render with the same data
  },
  // deleting needs the slug typed back: {confirm: slug}
  async remove(slug, confirm) {
    await api.del(`/scenarios/${encodeURIComponent(slug)}`, { confirm });
    const was = this.slug;
    await this.refresh();
    if (was === slug || !this.has(was)) { this.slug = this.defaultSlug; lsSet(this.slug); emitter.emit('change', this.current); }
  },

  // History drawer: restorable versions (GET /scenarios/<slug>/history) and the audit rows that explain them (GET /audit?scenario=)
  async history(slug = this.slug) {
    const listOf = (r, keys) => (Array.isArray(r) ? r : (r && keys.map((k) => r[k]).find(Array.isArray)) || []);
    const [hist, audit] = await Promise.all([
      api.get(`/scenarios/${encodeURIComponent(slug)}/history`, { limit: 200 }),
      api.get('/audit', { scenario: slug, limit: 1000 }).catch((e) => { if (e && e.name === 'AuthError') throw e; return null; }),
    ]);
    return { versions: listOf(hist, ['history', 'versions', 'items']), audit: listOf(audit, ['audit', 'entries', 'items']) };
  },
  async restore(slug, version) {
    const res = await api.post(`/scenarios/${encodeURIComponent(slug)}/restore`, { version });
    await this.refresh();
    emitter.emit('change', this.current);
    return res;
  },

  setVersion(version) {
    const c = this.current;
    if (c && version != null && c.version !== version) { c.version = version; emitter.emit('version', version); }
  },
};

api.events.on('saved', ({ scenario, version }) => {
  if (scenario === scenarios.slug) scenarios.setVersion(version);
});
