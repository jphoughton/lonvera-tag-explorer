// Injected into a rendered Projection page. Returns every text node / control whose real rendered contrast, size or clipping is a problem.
// Contrast uses the composited background (all ancestors' background colors and opacity), WCAG 2.x relative luminance.
(() => {
  const _cv = document.createElement('canvas'); _cv.width = _cv.height = 1; const _cx = _cv.getContext('2d', { willReadFrequently: true });
  const parse = (s) => {
    if (!s) return null;
    let m = /^rgba?\(([^)]+)\)$/.exec(s.trim());
    if (m) { const p = m[1].split(/[ ,/]+/).filter(Boolean).map(Number); return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 }; }
    // hex, hsl, color-mix(), named, var-resolved values: let the canvas resolve them
    _cx.clearRect(0, 0, 1, 1); _cx.fillStyle = '#000'; _cx.fillStyle = s; const f = _cx.fillStyle; if (f === '#000000' && !/^(#000|black|rgb\(0, ?0, ?0\))/i.test(s.trim())) return null;
    _cx.fillRect(0, 0, 1, 1); const d = _cx.getImageData(0, 0, 1, 1).data; return { r: d[0], g: d[1], b: d[2], a: d[3] / 255 };
  };
  const over = (top, bot) => { const a = top.a + bot.a * (1 - top.a); return a === 0 ? { r: 0, g: 0, b: 0, a: 0 } : { r: (top.r * top.a + bot.r * bot.a * (1 - top.a)) / a, g: (top.g * top.a + bot.g * bot.a * (1 - top.a)) / a, b: (top.b * top.a + bot.b * bot.a * (1 - top.a)) / a, a }; };
  const lin = (c) => { c /= 255; return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; };
  const lum = (c) => 0.2126 * lin(c.r) + 0.7152 * lin(c.g) + 0.0722 * lin(c.b);
  const ratio = (a, b) => { const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x); return (hi + 0.05) / (lo + 0.05); };
  const hex = (c) => '#' + [c.r, c.g, c.b].map((v) => Math.round(v).toString(16).padStart(2, '0')).join('').toUpperCase();
  const pageBg = parse(getComputedStyle(document.documentElement).backgroundColor);
  const root = { r: 255, g: 255, b: 255, a: 1 };
  const chain = (el) => { const out = []; for (let e = el; e; e = e.parentElement) out.unshift(e); return out; };
  function effBg(el) {
    let bg = root; let hasImage = false;
    if (pageBg && pageBg.a > 0) bg = over(pageBg, bg);
    for (const e of chain(el)) {
      const cs = getComputedStyle(e);
      const c = parse(cs.backgroundColor);
      if (cs.backgroundImage && cs.backgroundImage !== 'none' && !/^url/.test(cs.backgroundImage) ) hasImage = true;
      if (c && c.a > 0) bg = over(c, bg);
      const op = parseFloat(cs.opacity); if (op < 1) bg = over({ ...bg, a: 1 }, bg); // opacity handled below for fg
    }
    return { bg, hasImage };
  }
  const effOpacity = (el) => { let o = 1; for (let e = el; e; e = e.parentElement) o *= parseFloat(getComputedStyle(e).opacity); return o; };
  function visible(el) {
    for (let e = el; e && e !== document.body; e = e.parentElement) {
      const cs = getComputedStyle(e);
      if (cs.display === 'none' || cs.visibility === 'hidden' || e.hidden) return false;
    }
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return false;
    // inside a scrolling ancestor: must intersect its visible box (skip cells scrolled away, collapsed menus, etc.)
    for (let e = el.parentElement; e && e !== document.body; e = e.parentElement) {
      const cs = getComputedStyle(e);
      if (/(auto|scroll|hidden|clip)/.test(cs.overflowX + cs.overflowY)) {
        const pr = e.getBoundingClientRect();
        if (r.right < pr.left + 1 || r.left > pr.right - 1 || r.bottom < pr.top + 1 || r.top > pr.bottom - 1) return false;
      }
    }
    return true;
  }
  const clean = (id) => id.replace(/\d+/g, 'N');
  const sig = (el) => { let s = el.tagName.toLowerCase(); if (el.id) s += '#' + clean(el.id); const c = [...el.classList].slice(0, 3).join('.'); if (c) s += '.' + c; const p = el.parentElement; if (p) { s = (p.id ? '#' + clean(p.id) : p.tagName.toLowerCase() + ([...p.classList][0] ? '.' + [...p.classList][0] : '')) + ' > ' + s; } return s; };
  const out = []; const seen = new Set();
  const push = (kind, el, extra) => { const key = kind + '|' + sig(el) + '|' + (extra.fg || '') + '|' + (extra.bg || '') + '|' + (extra.note || ''); if (seen.has(key)) { seen.get && 0; const f = out.find((x) => x.key === key); if (f) f.count++; return; } seen.add(key); out.push({ key, kind, sig: sig(el), count: 1, ...extra }); };
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let n;
  while ((n = walker.nextNode())) {
    const t = n.nodeValue.replace(/\s+/g, ' ').trim();
    if (!t) continue;
    const el = n.parentElement;
    if (!el || /^(SCRIPT|STYLE|NOSCRIPT|OPTION)$/.test(el.tagName)) continue;
    if (!visible(el)) continue;
    const cs = getComputedStyle(el);
    const fgRaw = parse(cs.color); if (!fgRaw) continue;
    const { bg, hasImage } = effBg(el);
    const op = effOpacity(el);
    const fg = over({ ...fgRaw, a: fgRaw.a * op }, bg);
    const r = ratio(fg, bg);
    const size = parseFloat(cs.fontSize); const bold = parseInt(cs.fontWeight, 10) >= 700;
    const large = size >= 24 || (size >= 18.66 && bold);
    const need = large ? 3 : 4.5;
    const base = { text: t.slice(0, 40), fg: hex(fg), bg: hex(bg), ratio: Math.round(r * 100) / 100, size, note: hasImage ? 'gradient-bg' : '' };
    if (r + 0.001 < need) push('contrast', el, { ...base, need });
    const caps = cs.textTransform === 'uppercase' && parseInt(cs.fontWeight, 10) >= 600 && size >= 11;      // tracked bold caps labels may be 11px
    if (size < 12 && !caps && !/^[\W\d]*$/.test(t) && !el.closest('.pg-note')) push('tiny', el, { ...base, note: 'font ' + size + 'px' });
    // clipped text (overflow hidden without an ellipsis) and text that spills out of its box
    if (/(hidden|clip)/.test(cs.overflowX) && el.scrollWidth > el.clientWidth + 2 && cs.textOverflow !== 'ellipsis' && el.children.length === 0) push('clipped', el, { ...base, note: `scrollW ${el.scrollWidth} > clientW ${el.clientWidth}` });
  }
  // controls: value text, placeholder, borders, disabled look
  for (const el of document.querySelectorAll('input, select, textarea, button')) {
    if (!visible(el)) continue;
    const cs = getComputedStyle(el);
    const { bg } = effBg(el);
    const fgRaw = parse(cs.color); const fg = over({ ...fgRaw, a: fgRaw.a * effOpacity(el) }, bg);
    const label = (el.value || el.textContent || el.getAttribute('aria-label') || el.placeholder || '').trim().slice(0, 30);
    if (el.tagName === 'SELECT' || (el.tagName === 'INPUT' && el.type !== 'checkbox' && el.type !== 'radio' && el.type !== 'file')) {
      const r = ratio(fg, bg);
      if (r < 4.5) push('control-text', el, { text: label, fg: hex(fg), bg: hex(bg), ratio: Math.round(r * 100) / 100, need: 4.5 });
      if (!label && !el.getAttribute('aria-label') && !(el.labels && el.labels.length) && !el.closest('label')) push('control-unlabeled', el, { text: '(no label)', note: 'no accessible name' });
    }
    if (el.tagName === 'BUTTON' && !label && !el.getAttribute('aria-label') && !el.title) push('control-unlabeled', el, { text: '(no label)', note: 'button without a name' });
    // border or fill must separate a control from its surroundings (3:1) unless it is a text-only link style
    const bc = parse(cs.borderTopColor); const bw = parseFloat(cs.borderTopWidth);
    const parentBg = effBg(el.parentElement || el).bg;
    const selfFill = parse(cs.backgroundColor);
    const fillOk = selfFill && selfFill.a > 0.5 && ratio(over(selfFill, parentBg), parentBg) >= 3;
    const borderOk = bw >= 1 && bc && bc.a > 0 && ratio(over({ ...bc, a: bc.a * 1 }, parentBg), parentBg) >= 3;
    const oc = parse(cs.outlineColor); const ow = parseFloat(cs.outlineWidth);
    const outlineOk = cs.outlineStyle !== 'none' && ow >= 2 && oc && oc.a > 0 && ratio(over(oc, parentBg), parentBg) >= 3;   // the in-cell editor draws its edge as a 2px focus-colored outline
    const isTextButton = el.tagName === 'BUTTON' && ((bw === 0 || (bc && bc.a === 0)) && !(selfFill && selfFill.a > 0.05));
    if (!fillOk && !borderOk && !outlineOk && !isTextButton && !el.closest('.segs, .is-seg, [class*="-seg"]') && !el.disabled && !(el.type === 'checkbox' || el.type === 'radio' || el.type === 'file' || el.type === 'hidden')) {
      const b = bc ? over(bc, parentBg) : parentBg;
      push('control-edge', el, { text: label, fg: hex(b), bg: hex(parentBg), ratio: Math.round(ratio(b, parentBg) * 100) / 100, need: 3, note: 'border/fill vs surroundings' });
    }
    if (el.disabled || el.getAttribute('aria-disabled') === 'true') push('disabled-note', el, { text: label, note: 'disabled' });
  }
  // charts: canvas text is not in the DOM; read the Chart.js options
  const charts = [];
  for (const cv of document.querySelectorAll('canvas')) {
    if (!visible(cv) || typeof Chart === 'undefined') continue;
    const ch = Chart.getChart(cv); if (!ch) continue;
    const { bg } = effBg(cv);
    const cols = {};
    const sc = ch.options.scales || {};
    for (const [k, v] of Object.entries(sc)) if (v.ticks && v.ticks.color) cols['ticks.' + k] = v.ticks.color;
    if (ch.options.plugins && ch.options.plugins.legend && ch.options.plugins.legend.labels) cols.legend = ch.options.plugins.legend.labels.color;
    const dsRatios = ch.data.datasets.map((d) => { const c = parse(typeof d.borderColor === 'string' ? d.borderColor : (typeof d.backgroundColor === 'string' ? d.backgroundColor : '')); return c ? { label: d.label, ratio: Math.round(ratio(over(c, bg), bg) * 100) / 100, color: hex(c) } : null; }).filter(Boolean);
    for (const [k, v] of Object.entries(cols)) { const c = parse(v) || parse(getComputedStyle(cv).color); if (!c) continue; const r = ratio(over(c, bg), bg); if (r < 4.5) push('chart-text', cv, { text: k, fg: hex(c), bg: hex(bg), ratio: Math.round(r * 100) / 100, need: 4.5 }); }
    for (const d of dsRatios) if (d.ratio < 3) push('chart-mark', cv, { text: d.label, fg: d.color, bg: hex(bg), ratio: d.ratio, need: 3 });
    charts.push({ id: cv.id || cv.getAttribute('aria-label') || '(canvas)', w: Math.round(cv.getBoundingClientRect().width), h: Math.round(cv.getBoundingClientRect().height), datasets: ch.data.datasets.length, hasAriaLabel: !!cv.getAttribute('aria-label') });
    if (!cv.getAttribute('aria-label')) push('chart-unlabeled', cv, { text: '(canvas)', note: 'canvas without aria-label' });
  }
  const docOver = document.documentElement.scrollWidth > document.documentElement.clientWidth + 1;
  return { violations: out.map(({ key, ...r }) => r), charts, pageOverflow: docOver, title: document.title };
})()
