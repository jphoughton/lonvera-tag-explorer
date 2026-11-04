"""Dev tool: load the P&L page from a running server in headless Chromium and save screenshots + console errors.

    ~/lonvera-tag-explorer/.venv/bin/python tests/projection/pnl/shoot_pnl.py http://127.0.0.1:5203 OUT_DIR [dark]
"""
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

base, out = sys.argv[1], Path(sys.argv[2])
schemes = ["light", "dark"] if len(sys.argv) > 3 else ["light"]
out.mkdir(parents=True, exist_ok=True)
with sync_playwright() as p:
    b = p.chromium.launch()
    for scheme in schemes:
        for label, size, mobile in (("desktop", (1440, 900), False), ("mobile", (390, 844), True)):
            ctx = b.new_context(viewport={"width": size[0], "height": size[1]}, color_scheme=scheme, is_mobile=mobile, has_touch=mobile)
            page = ctx.new_page()
            errors = []
            page.on("console", lambda m: errors.append(m.text) if m.type in ("error", "warning") else None)
            page.on("pageerror", lambda e: errors.append("pageerror: " + str(e)))
            page.goto(base + "/projection/pnl")
            page.wait_for_selector("body[data-pg-state=ready]", timeout=20000)
            page.wait_for_load_state("networkidle")
            time.sleep(0.8)
            page.screenshot(path=str(out / f"pnl-{label}-{scheme}.png"), full_page=True)
            print(label, scheme, "console:", errors)
            ctx.close()
    b.close()
