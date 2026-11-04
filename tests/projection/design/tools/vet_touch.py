"""Touch-target audit at 390px: every visible control smaller than 36px (WCAG 2.5.8 minimum 24px, our rule 44px on coarse pointers). Read-only.

    ~/lonvera-tag-explorer/.venv/bin/python tests/projection/design/tools/vet_touch.py <base_url> [--pages pacing,...]
"""
import argparse
import collections
import time

from playwright.sync_api import sync_playwright

JS = """() => {
  const out = [];
  const vis = (e) => { for (let x = e; x && x !== document.body; x = x.parentElement) { const cs = getComputedStyle(x); if (cs.display === 'none' || cs.visibility === 'hidden' || x.hidden) return false; } const r = e.getBoundingClientRect(); return r.width > 1 && r.height > 1; };
  for (const e of document.querySelectorAll('a[href], button, input:not([type=hidden]), select, textarea, [role=button], summary')) {
    if (!vis(e) || e.closest('.pg-scroll') && e.tagName === 'A') continue;
    if (e.tagName === 'A' && getComputedStyle(e).display === 'inline') continue;       // links inside sentences
    const r = e.getBoundingClientRect();
    if (e.type === 'checkbox' || e.type === 'radio') { const l = e.closest('label'); if (l) { const lr = l.getBoundingClientRect(); if (lr.height >= 36) continue; } }
    if (e.type === 'file' && e.style.opacity === '0') continue;
    const min = Math.min(r.width, r.height);
    if (min < 36) out.push({ sig: e.tagName.toLowerCase() + (e.id ? '#' + e.id.replace(/\\d+/g, 'N') : '') + '.' + [...e.classList].slice(0, 2).join('.'), w: Math.round(r.width), h: Math.round(r.height), text: (e.innerText || e.value || e.getAttribute('aria-label') || '').trim().slice(0, 24) });
  }
  return out;
}"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("base")
    ap.add_argument("--pages", default="pacing,overview,cohorts,drivers,pnl,inventory_demand,inventory_supply,financing")
    a = ap.parse_args()
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        page = ctx.new_page()
        total = collections.Counter()
        for name in a.pages.split(","):
            page.goto(f"{a.base}/projection/{name}")
            page.wait_for_function("document.body.dataset.pgState === 'ready'")
            time.sleep(1.5)
            res = page.evaluate(JS)
            by = collections.Counter((r["sig"], r["h"], r["w"] if r["w"] < 36 else 0) for r in res)
            print(f"== {name}: {len(res)} controls under 36px")
            for (sig, h, w), n in by.most_common(8):
                print(f"   {n:3}x {sig:50} h={h}" + (f" w={w}" if w else ""))
            total[name] = len(res)
        b.close()
        print(dict(total))


if __name__ == "__main__":
    main()
