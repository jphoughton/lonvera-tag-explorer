// Shared helpers for the Production board (priorities_production.js), the vendor page (priorities_vendor.js) and the Assign dialog (priorities_assign.js).
// Class names and test ids start with `prod-` (never ad-, ads-, sponsor, banner, promo: ad blockers hide those).
import { h, fmt, isNum } from '../util.js';

export const STAGES = { assigned: 'Assigned', brief_sent: 'Brief sent', creative_in: 'Creative delivered', ready: 'Ready to launch', launched: 'Launched', dropped: 'Dropped' };
export const VENDOR_FALLBACK = ['Madre', 'Victoria', 'TrybeDCShop', 'TrybeOrion', '10xROAS', 'Internal', 'PuredMedia', 'CreateMastery', 'OpenProgram'];
export const PRODUCT_NAMES = { 'vita-bull': 'Vita Bull', 'beef-liver': 'Beef Liver', boneflex: 'Boneflex' };

export const md = (s) => { const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(s || ''); return m ? `${+m[2]}/${+m[3]}` : (s || ''); };
export const weekLabel = (s) => 'Week of ' + md(s);
export const productName = (p) => PRODUCT_NAMES[p] || p || '';
export const stageLabel = (r) => r.stage_label || STAGES[r.stage] || r.stage || '';
export const today = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; };
export const mondayOf = (iso) => { const [y, m, d] = iso.split('-').map(Number); const t = new Date(Date.UTC(y, m - 1, d)); t.setUTCDate(t.getUTCDate() - ((t.getUTCDay() + 6) % 7)); return t.toISOString().slice(0, 10); };
export const addDays = (iso, n) => { const [y, m, d] = iso.split('-').map(Number); const t = new Date(Date.UTC(y, m - 1, d + n)); return t.toISOString().slice(0, 10); };
// "F1 Long animated story, openly AI, told by the character (90s to 3 min)": a creative format as shown in the pick lists and on the vendor page
export const formatLabel = (f) => `${f.code} ${f.name}` + (f.length ? ` (${f.length})` : '');
export const rowFormat = (r) => (r.format_code ? { code: r.format_code, name: r.format_name || '', length: r.format_length || '' } : null);

// "Launched 11/3, $1,240 spent, 1.80 return" from the linked ads (earliest launch date, total spend, spend-weighted return), or '' when nothing is launched
export function resultText(r) {
  const ads = (r.ads || []).filter((a) => a.launch_date);
  if (!ads.length) return '';
  const first = ads.map((a) => a.launch_date).sort()[0];
  const spend = ads.reduce((s, a) => s + (isNum(a.spend) ? a.spend : 0), 0);
  const val = ads.reduce((s, a) => s + (isNum(a.spend) && isNum(a.roas) ? a.spend * a.roas : 0), 0);
  const ret = spend > 0 ? val / spend : null;
  return `Launched ${md(first)}, ${fmt.money(spend)} spent` + (ret !== null ? `, ${fmt.roas(ret)} return` : '');
}

export function link(url, text, attrs) {
  return h('a', { href: url, target: '_blank', rel: 'noopener noreferrer', ...(attrs || {}) }, text);
}
export const short = (u) => { try { const x = new URL(u); return (x.host + x.pathname).replace(/\/$/, '').slice(0, 38) + ((x.host + x.pathname).length > 38 ? '…' : ''); } catch (e) { return u; } };
