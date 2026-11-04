"""Screenshot the style guide, the library demo and the top bar states in light / dark at 1440, 1024, 768 and 390 px, against the UI mock server.

    ~/lonvera-tag-explorer/.venv/bin/python tests/projection/design/tools/shoot.py [out_dir]

Writes PNGs to tests/projection/design/shots (default). Not part of the pytest run.
"""
import socket
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "tests" / "projection" / "design" / "shots"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    port = free_port()
    srv = subprocess.Popen([sys.executable, str(ROOT / "tests/projection/ui/mock_server.py"), str(port)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", port), 0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        with sync_playwright() as p:
            b = p.chromium.launch()
            shots = [("styleguide", "/static/projection/styleguide.html"), ("demo", "/projection/_demo?demo=1"), ("overview", "/projection")]
            for scheme in ("light", "dark"):
                for label, size, mobile in (("desktop", (1440, 900), False), ("mobile", (390, 844), True)):
                    ctx = b.new_context(viewport={"width": size[0], "height": size[1]}, color_scheme=scheme, is_mobile=mobile, has_touch=mobile, device_scale_factor=1)
                    page = ctx.new_page()
                    errors = []
                    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
                    for name, path in shots:
                        page.goto(base + path)
                        page.wait_for_load_state("networkidle")
                        time.sleep(0.6)
                        page.screenshot(path=str(OUT / f"{name}-{label}-{scheme}.png"), full_page=True)
                    print(label, scheme, "console errors:", errors)
                    ctx.close()
            # the single top bar: overview, a module page, a scenario copy, both menus open
            for scheme in ("light", "dark"):
                for w, h_ in ((1440, 900), (1024, 768), (768, 1024), (390, 844)):
                    ctx = b.new_context(viewport={"width": w, "height": h_}, color_scheme=scheme, is_mobile=(w <= 480), has_touch=(w <= 480))
                    page = ctx.new_page()
                    errors = []
                    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
                    tag = f"bar-{w}-{scheme}"
                    page.goto(base + "/projection")
                    page.wait_for_selector(".pg-mcard")
                    page.screenshot(path=str(OUT / f"{tag}-overview.png"))
                    page.goto(base + "/projection/drivers")
                    page.wait_for_selector(".pg-grid .pg-table")
                    page.screenshot(path=str(OUT / f"{tag}-module.png"))
                    page.evaluate("fetch('/api/projection/scenarios', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({name:'Bull case %s %s'})})" % (w, scheme))
                    page.goto(base + f"/projection/drivers?scenario=bull-case-{w}-{scheme}")
                    page.wait_for_selector(".pg-grid .pg-table")
                    page.click("#pg-menu-btn")
                    page.screenshot(path=str(OUT / f"{tag}-copy-menu.png"))
                    page.keyboard.press("Escape")
                    if w <= 900:
                        page.click("#pg-navbtn")
                        page.screenshot(path=str(OUT / f"{tag}-nav-menu.png"))
                    print(tag, "console errors:", errors, "overflow:", page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth"),
                          "bar height:", page.evaluate("Math.round(document.querySelector('#pg-chrome').getBoundingClientRect().height)"))
                    ctx.close()
            b.close()
    finally:
        srv.terminate()


if __name__ == "__main__":
    main()
