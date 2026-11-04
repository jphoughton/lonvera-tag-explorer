"""Readability and layout audit of every Projection page against a running app, headless.

    ~/lonvera-tag-explorer/.venv/bin/python tests/projection/design/tools/vet.py <base_url> <out_dir> [--schemes dark,light] [--widths 1440,1024,768,390] [--pages pacing,overview,...]

For each scheme x width x page: waits for the page, runs audit_readability.js (WCAG contrast of every text node and control against its real,
composited background; tiny text; clipped text; charts; page overflow), takes a viewport and a full-page screenshot, and writes audit.json + summary.md.
Read-only: it only navigates and opens menus; it never edits.
"""
import argparse
import json
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
JS = (HERE / "audit_readability.js").read_text()
PAGES = ["pacing", "overview", "cohorts", "drivers", "pnl", "inventory_demand", "inventory_supply", "financing", "variables"]


def settle(page, extra=1.2):
    try:
        page.wait_for_function("document.body.dataset.pgState === 'ready' || document.body.dataset.pgState === 'error'", timeout=30000)
    except Exception:
        pass
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass
    time.sleep(extra)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("base")
    ap.add_argument("out")
    ap.add_argument("--schemes", default="dark,light")
    ap.add_argument("--widths", default="1440,1024,768,390")
    ap.add_argument("--pages", default=",".join(PAGES))
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    results = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        for scheme in a.schemes.split(","):
            for w in [int(x) for x in a.widths.split(",")]:
                mobile = w <= 480
                ctx = b.new_context(viewport={"width": w, "height": 900 if w > 480 else 844}, color_scheme=scheme, is_mobile=mobile, has_touch=mobile)
                page = ctx.new_page()
                errs = []
                page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
                page.on("pageerror", lambda e: errs.append("pageerror: " + str(e)))
                for name in a.pages.split(","):
                    errs.clear()
                    t0 = time.time()
                    page.goto(f"{a.base}/projection/{name}")
                    settle(page)
                    load_s = round(time.time() - t0, 1)
                    res = page.evaluate(JS)
                    res.update({"scheme": scheme, "width": w, "page": name, "load_s": load_s, "console_errors": list(errs)})
                    tag = f"{name}-{w}-{scheme}"
                    page.screenshot(path=str(out / f"{tag}.png"))
                    page.screenshot(path=str(out / f"{tag}-full.png"), full_page=True)
                    results.append(res)
                    kinds = {}
                    for v in res["violations"]:
                        kinds[v["kind"]] = kinds.get(v["kind"], 0) + v["count"]
                    print(f"{scheme:5} {w:4} {name:17} load {load_s:>4}s overflow={res['pageOverflow']} console={len(errs)} {kinds}")
                ctx.close()
        b.close()
    (out / "audit.json").write_text(json.dumps(results, indent=1))
    # unique violations across everything
    uniq = {}
    for r in results:
        for v in r["violations"]:
            if v["kind"] in ("disabled-note",):
                continue
            k = (v["kind"], r["page"], v["sig"], v.get("fg"), v.get("bg"))
            u = uniq.setdefault(k, {**v, "page": r["page"], "where": set()})
            u["where"].add(f"{r['scheme']}@{r['width']}")
    lines = ["# Readability audit (auto)", ""]
    for k, u in sorted(uniq.items(), key=lambda kv: (kv[0][1], kv[0][0], kv[1].get("ratio", 99))):
        lines.append(f"- [{u['kind']}] {u['page']}: `{u['sig']}` text={u.get('text')!r} fg={u.get('fg')} bg={u.get('bg')} ratio={u.get('ratio')} need={u.get('need','')} {u.get('note','')} x{u['count']} ({', '.join(sorted(u['where']))})")
    (out / "summary.md").write_text("\n".join(lines) + "\n")
    print("unique findings:", len(uniq))


if __name__ == "__main__":
    main()
