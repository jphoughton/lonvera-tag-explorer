"""Task walkthroughs for the Projection usability vet, headless, against a LOCAL copy of the app (these tasks write: never point at the live site).

    ~/lonvera-tag-explorer/.venv/bin/python tests/projection/design/tools/vet_tasks.py http://127.0.0.1:5296 <out_dir> [--scheme dark]

Each task logs wall-clock time, the number of user actions (clicks, typed fields, selects, key presses) until the answer is on screen, the answer text,
and takes screenshots. Results go to <out_dir>/tasks.json and <out_dir>/tasks.md.
"""
import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "tests" / "projection" / "pnl"))


class Task:
    def __init__(self, key, title, page, out):
        self.key, self.title, self.page, self.out = key, title, page, Path(out)
        self.t0 = time.time()
        self.actions = 0
        self.log = []
        self.answer = {}

    def step(self, label, action=False, shot=None, full=False):
        if action:
            self.actions += 1
        t = round(time.time() - self.t0, 1)
        if shot:
            self.page.screenshot(path=str(self.out / f"{self.key}-{shot}.png"), full_page=full)
        self.log.append({"t": t, "actions": self.actions, "step": label, "shot": f"{self.key}-{shot}.png" if shot else None})
        return t

    def done(self):
        return {"key": self.key, "title": self.title, "seconds": round(time.time() - self.t0, 1), "actions": self.actions, "log": self.log, "answer": self.answer}


def ready(page, extra=1.5):
    page.wait_for_function("document.body.dataset.pgState === 'ready'", timeout=30000)
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass
    time.sleep(extra)


def headline(page):
    return page.evaluate("(() => { const s = document.getElementById('pg-headline'); return s && !s.hidden ? [...s.querySelectorAll('.pg-kpi')].map(k => k.innerText.replace(/\\s+/g, ' ').trim()) : []; })()")


def tldr(page):
    return page.evaluate("(() => { const t = document.querySelector('.pg-tldr'); return t ? t.innerText.replace(/\\s+/g, ' ').trim() : null; })()")


def poll(page, fn, timeout=8.0, step=0.25):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = fn()
        if v:
            return v
        time.sleep(step)
    return None


def scenario_menu(page, act):
    page.click("#pg-menu-btn")
    page.click(f'#pg-menu-pop [data-act="{act}"]')


def fill_range_flat(t, page, from_iso, amount):
    page.click("#dr-open-tool")
    t.step("Open Fill a range", True)
    page.select_option("#dr-from", from_iso)
    t.step("Choose the first month", True)
    page.get_by_role("button", name="Through Dec 2029").click()
    t.step("Choose Through Dec 2029", True)
    page.fill("#dr-value", amount)
    t.step(f"Type {amount} per month", True)
    time.sleep(1.0)
    page.get_by_role("button", name="Apply").first.click()
    t.step("Apply", True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("base")
    ap.add_argument("out")
    ap.add_argument("--scheme", default="dark")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    results = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={"width": 1440, "height": 900}, color_scheme=a.scheme, permissions=["clipboard-read", "clipboard-write"])
        # the plan-of-record guard asks once per tab session; the walkthroughs time the edit itself, so answer "Edit the plan of record" up front
        # (the ask card is covered by tests/projection/ui/test_shell.py)
        ctx.add_init_script("try { sessionStorage.setItem('projection.por.edit', '1'); } catch (e) {}")
        page = ctx.new_page()
        errs = []
        page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)

        # ---------------------------------------------------------------- (e) this month's pacing vs goal (the default page)
        t = Task("e", "This month's pacing vs goal (the default page)", page, out)
        page.goto(a.base + "/projection")
        ready(page, 0.2)
        t.step("Landed (/projection redirects to Pacing)", shot="above-fold")
        ready(page, 1.5)
        t.answer["url"] = page.url.replace(a.base, "")
        t.answer["tldr"] = tldr(page)
        t.answer["tldr_in_first_screen"] = page.evaluate("(() => { const e = document.querySelector('.pg-tldr'); if (!e) return null; const r = e.getBoundingClientRect(); return {top: Math.round(r.top), bottom: Math.round(r.bottom)}; })()")
        t.answer["headline_strip"] = headline(page)
        t.answer["month_picker"] = page.evaluate("(() => { const s = document.querySelector('main select'); return s ? s.value || s.options[s.selectedIndex].text : null; })()")
        t.step("Read the TLDR", shot="read")
        results.append(t.done())
        page.set_viewport_size({"width": 390, "height": 844})
        page.reload()
        ready(page)
        t2 = Task("e-phone", "Pacing on a phone", page, out)
        t2.answer["tldr"] = tldr(page)
        t2.answer["tldr_box"] = page.evaluate("(() => { const e = document.querySelector('.pg-tldr'); if (!e) return null; const r = e.getBoundingClientRect(); return {top: Math.round(r.top), bottom: Math.round(r.bottom), vh: innerHeight}; })()")
        t2.step("Pacing at 390px", shot="above-fold")
        results.append(t2.done())
        page.set_viewport_size({"width": 1440, "height": 900})

        # ---------------------------------------------------------------- (a) change ad spend from Dec-26 on; FY27 NOI and implied value
        page.goto(a.base + "/projection/drivers")
        ready(page)
        t = Task("a", "Change monthly ad spend from Dec 2026; watch FY27 net operating income and implied value", page, out)
        before = headline(page)
        t.answer["headline_before"] = before
        t.answer["tldr_before"] = tldr(page)
        t.step("Spend plan loaded", shot="before")
        t.t0 = time.time()
        t.actions = 0
        fill_range_flat(t, page, "2026-12-01", "450k")
        changed = poll(page, lambda: headline(page) != before and headline(page), 10)
        t.answer["headline_after"] = headline(page)
        t.answer["tldr_after"] = tldr(page)
        t.answer["headline_changed"] = bool(changed)
        t.step("Headline numbers moved" if changed else "Headline did not change", shot="after")
        results.append(t.done())

        # ---------------------------------------------------------------- (f) undo a bad edit, then restore a scenario version
        page.goto(a.base + "/projection/drivers")
        ready(page)
        t = Task("f", "Undo a bad edit; restore an earlier version of the scenario", page, out)
        cell = lambda row, c: page.locator(f'tr[data-key="{row}"] td[data-c="{c}"]')
        ver0 = page.locator("#pg-version").inner_text()
        old = cell("drivers.spend_total", 28).inner_text()
        cell("drivers.spend_total", 28).click()
        t.step("Select a spend cell (Apr 2027)", True)
        page.keyboard.type("99m")
        page.keyboard.press("Enter")
        t.step("Type a bad value (99m) and press Enter", True, shot="bad-edit")
        time.sleep(1.2)
        bad = cell("drivers.spend_total", 28).inner_text()
        page.keyboard.press("Control+z")
        t.step("Undo (Ctrl+Z)", True)
        time.sleep(1.2)
        back = cell("drivers.spend_total", 28).inner_text()
        t.answer.update({"cell_before": old, "cell_after_bad_edit": bad, "cell_after_undo": back, "undo_restored": back == old, "version_before": ver0, "version_after_undo": page.locator("#pg-version").inner_text()})
        t.step("Value is back", shot="undone")
        # now two more edits, then restore the version before them from History
        for c, v in ((29, "2m"), (30, "3m")):
            cell("drivers.spend_total", c).click()
            page.keyboard.type(v)
            page.keyboard.press("Enter")
            time.sleep(0.9)
        ver_edit = page.locator("#pg-version").inner_text()
        t3 = time.time()
        page.click("#pg-menu-btn")
        page.click('#pg-menu-pop [data-act="history"]')
        t.step("Open History", True, shot="history")
        page.locator("#pg-hist li").nth(1).locator("button[data-act=restore]").click()
        t.step("Click Restore on an earlier version", True, shot="restore-confirm")
        dlg = page.locator("dialog[open]")
        t.answer["restore_dialog_text"] = dlg.inner_text().replace("\n", " ")[:260]
        dlg.get_by_role("button", name="Restore").click()
        t.step("Confirm Restore", True)
        time.sleep(1.5)
        t.answer.update({"version_after_edits": ver_edit, "version_after_restore": page.locator("#pg-version").inner_text(), "cell29_after_restore": cell("drivers.spend_total", 29).inner_text(), "cell30_after_restore": cell("drivers.spend_total", 30).inner_text()})
        t.step("Restored", shot="restored")
        results.append(t.done())

        # ---------------------------------------------------------------- (b) clone the plan as "Bull" and compare
        page.goto(a.base + "/projection/drivers")
        ready(page)
        t = Task("b", "Clone the plan as Bull, change it, compare with the plan of record", page, out)
        scenario_menu(page, "clone")
        t.step("Scenario menu, Clone this scenario", True, shot="clone-dialog")
        page.fill("dialog input", "Bull")
        t.step("Name it Bull", True)
        page.click("dialog button[type=submit]")
        t.step("Create the clone", True)
        page.wait_for_function("document.querySelector('#pg-scenario').selectedOptions[0].textContent === 'Bull'")
        ready(page, 1.2)
        t.answer["cue"] = {"mode_chip": page.locator("#pg-mode").inner_text() if page.locator("#pg-mode").is_visible() else None, "body": page.evaluate("document.body.dataset.pgScenario"), "cue_strip": page.evaluate("(() => { const c = document.querySelector('.dr-cue'); return c ? c.innerText.replace(/\\s+/g, ' ') : null; })()")}
        t.step("In the copy", shot="in-copy")
        fill_range_flat(t, page, "2026-12-01", "650k")
        time.sleep(2.0)
        t.answer["bull_headline"] = headline(page)
        t.step("Bull edited", shot="bull-edited")
        sec = page.locator(".dr-compare")
        sec.scroll_into_view_if_needed()
        sec.get_by_role("button", name="Current plan").first.click()
        t.step("Pick Current plan in Compare scenarios", True)
        poll(page, lambda: page.locator(".dr-fy").count() > 0, 8)
        time.sleep(1.0)
        t.answer["compare_table"] = page.evaluate("(() => { const t = document.querySelector('.dr-fy'); return t ? t.innerText.replace(/\\s+/g, ' ').slice(0, 400) : null; })()")
        sec.scroll_into_view_if_needed()
        t.step("Compare table on screen", shot="compare")
        results.append(t.done())

        # ---------------------------------------------------------------- (c) which product runs out first, when must it be ordered
        page.goto(a.base + "/projection/inventory_supply")
        ready(page)
        t = Task("c", "Which product runs out of stock first, and when must it be ordered", page, out)
        t.answer["tldr"] = tldr(page)
        t.answer["tldr_box"] = page.evaluate("(() => { const e = document.querySelector('.pg-tldr'); if (!e) return null; const r = e.getBoundingClientRect(); return {top: Math.round(r.top), bottom: Math.round(r.bottom), vh: innerHeight}; })()")
        t.answer["reorder_first_visible_y"] = page.evaluate("(() => { const e = [...document.querySelectorAll('main *')].find(x => /order by|order-by|reorder/i.test(x.textContent) && x.children.length < 4 && x.getBoundingClientRect().height > 0 && /h2|h3|th|caption/i.test(x.tagName)); return e ? Math.round(e.getBoundingClientRect().top) : null; })()")
        t.step("Read the page", shot="read")
        t.answer["page_text_head"] = page.evaluate("document.querySelector('main').innerText.replace(/\\s+/g, ' ').slice(0, 900)")
        results.append(t.done())

        # ---------------------------------------------------------------- (d) QuickBooks upload preview (never commit)
        page.goto(a.base + "/projection/pnl")
        ready(page)
        t = Task("d", "Upload a QuickBooks month and see the diff before committing (preview only)", page, out)
        from pnl_helpers import qb_rows, to_csv
        tmp = Path(tempfile.mkdtemp()) / "qb-export.csv"
        tmp.write_bytes(to_csv(qb_rows()))
        page.get_by_role("button", name="Upload QuickBooks export").click()
        t.step("Open the upload dialog", True, shot="dialog")
        page.set_input_files('input[type=file]', str(tmp))
        t.step("Choose the file", True)
        poll(page, lambda: page.locator(".pn-up .counts").count() > 0, 15)
        time.sleep(0.8)
        t.answer["preview_text"] = page.locator("dialog .pn-up").inner_text().replace("\n", " ")[:600]
        t.answer["commit_button"] = page.locator("dialog button.primary").last.inner_text()
        t.step("Diff shown", shot="preview")
        page.keyboard.press("Escape")
        t.step("Closed without committing")
        results.append(t.done())
        t.answer["console_errors"] = list(errs)
        b.close()
    (out / "tasks.json").write_text(json.dumps(results, indent=1))
    lines = ["# Task walkthroughs (auto)", ""]
    for r in results:
        lines.append(f"## {r['key']}: {r['title']}  ({r['seconds']}s, {r['actions']} actions)")
        for s in r["log"]:
            lines.append(f"- {s['t']}s [{s['actions']}] {s['step']}" + (f" ({s['shot']})" if s["shot"] else ""))
        lines.append(f"- answer: {json.dumps(r['answer'])[:1200]}")
        lines.append("")
    (out / "tasks.md").write_text("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
