"""Readability audit of transient states (menus, History drawer, dialogs, cell editor, toasts) on a LOCAL app. Writes to the scenario store (opens dialogs, never submits).

    ~/lonvera-tag-explorer/.venv/bin/python tests/projection/design/tools/vet_states.py http://127.0.0.1:5296 <out_dir> [--schemes dark,light] [--widths 1440,390]
"""
import argparse
import json
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

JS = (Path(__file__).resolve().parent / "audit_readability.js").read_text()


def summarize(res):
    out = {}
    for v in res["violations"]:
        if v["kind"] == "disabled-note":
            continue
        out[v["kind"]] = out.get(v["kind"], 0) + v["count"]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("base")
    ap.add_argument("out")
    ap.add_argument("--schemes", default="dark,light")
    ap.add_argument("--widths", default="1440,390")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    report = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        for scheme in a.schemes.split(","):
            for w in [int(x) for x in a.widths.split(",")]:
                ctx = b.new_context(viewport={"width": w, "height": 900 if w > 480 else 844}, color_scheme=scheme, is_mobile=w <= 480, has_touch=w <= 480)
                page = ctx.new_page()

                def snap(state, extra=0.5):
                    time.sleep(extra)
                    res = page.evaluate(JS)
                    page.screenshot(path=str(out / f"state-{state}-{w}-{scheme}.png"))
                    s = summarize(res)
                    bad = [v for v in res["violations"] if v["kind"] in ("contrast", "control-text", "chart-text", "clipped") ]
                    report.append({"state": state, "scheme": scheme, "width": w, "summary": s, "worst": bad[:6]})
                    print(f"{scheme:5} {w:4} {state:22} {s}")

                page.goto(a.base + "/projection/drivers")
                page.wait_for_function("document.body.dataset.pgState === 'ready'")
                time.sleep(1.5)
                snap("page")
                page.click("#pg-menu-btn")
                snap("scenario-menu")
                page.keyboard.press("Escape")
                page.click("#pg-menu-btn")
                page.click('#pg-menu-pop [data-act="history"]')
                page.wait_for_selector("#pg-hist li", timeout=10000)
                snap("history-drawer", 0.8)
                page.click("#pg-drawer-close")
                page.click("#pg-menu-btn")
                page.click('#pg-menu-pop [data-act="new"]')
                snap("new-scenario-dialog")
                page.keyboard.press("Escape")
                page.click("#pg-menu-btn")
                page.click('#pg-menu-pop [data-act="clone"]')
                page.fill("dialog input", "x" * 3)
                snap("clone-dialog")
                page.keyboard.press("Escape")
                # cell editor with an invalid value
                cell = page.locator('tr[data-key="drivers.spend_total"] td[data-c="25"]')
                cell.scroll_into_view_if_needed()
                cell.click()
                page.keyboard.press("Enter")
                page.keyboard.type("abc")
                page.keyboard.press("Enter")
                snap("cell-editor-invalid")
                page.keyboard.press("Escape")
                cell.click()
                snap("cell-selected")
                if w <= 900:
                    page.click("#pg-navbtn")
                    snap("nav-menu")
                    page.keyboard.press("Escape")
                ctx.close()
        b.close()
    (out / "states.json").write_text(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
