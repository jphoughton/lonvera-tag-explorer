"""Screenshots of the Variables page (desktop 1440 and phone 390, light and dark) plus a scripted edit, against a running app.

    ~/lonvera-tag-explorer/.venv/bin/python tests/projection/variables/tools/shoot_variables.py http://127.0.0.1:5208 /path/to/out
Prints console errors and horizontal overflow per view. Not part of the pytest run.
"""
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5208"
out = Path(sys.argv[2] if len(sys.argv) > 2 else "shots")
out.mkdir(parents=True, exist_ok=True)

with sync_playwright() as p:
    b = p.chromium.launch()
    for scheme in ("light", "dark"):
        for label, size, mobile in (("desktop", (1440, 900), False), ("mobile", (390, 844), True)):
            ctx = b.new_context(viewport={"width": size[0], "height": size[1]}, color_scheme=scheme, is_mobile=mobile, has_touch=mobile)
            page = ctx.new_page()
            errors = []
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(base + "/projection/variables")
            page.wait_for_selector(".va-mod", timeout=20000)
            page.wait_for_selector(".va-bar, .va-sens-wrap", timeout=30000)
            time.sleep(0.8)
            over = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
            page.screenshot(path=str(out / f"variables-{label}-{scheme}-top.png"))
            for sel, name in (("#va-cohorts", "cohorts"), ("#va-pnl", "pnl"), ("#va-missing", "missing")):
                el = page.query_selector(sel)
                if el:
                    if name == "missing":
                        page.evaluate("document.getElementById('va-missing').open = true")
                    el.scroll_into_view_if_needed()
                    el.screenshot(path=str(out / f"variables-{label}-{scheme}-{name}.png"))
            print(label, scheme, "overflow:", over, "console errors:", errors)
            ctx.close()
    b.close()
