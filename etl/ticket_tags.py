"""Tag the tickets that were added with only a name, a vendor and a brief (docs/CREATIVE_PRIORITIES.md, Ticket contract).

What it does: for every Production ticket with tag_state = 'pending' (not dropped) it reads the name, the brief text and, when given, the brief link text, and asks headless Claude Code (`claude -p`, Sonnet, no tools beyond Read, the same
way etl.auto_tag does, CLAUDE_CODE_OAUTH_TOKEN) to choose the PRODUCT (vita-bull | beef-liver | boneflex | vita-belle), ONE line of the taxonomy (market cluster + ICP + pain point of the Communication Overview, products with lines),
and NEW vs ITERATION (an iteration is a variation or remake of an earlier concept). Then it fills ONLY what is empty on the ticket (product '', cluster '', icp '', pain_point '', concept_kind NULL, line_id NULL): anything a person or a
partner gave is never overwritten. The ticket becomes tag_state 'complete' with tagged_at, a tag_note (confidence + one-line reason) and a history row 'tagged'. A ticket that fails three runs becomes 'failed' (a person fills it in).
Safety: missing token / auth error / usage limit stops the run early and is recorded failed in etl_runs (the nightly pipeline carries on, scripts/deploy/run_etl.sh); hard cap per run (--limit, default 60).
Usage: python -m etl.ticket_tags [--limit N] [--id ASSIGNMENT_ID] [--dry-run]"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import time

from . import auto_tag as at
from . import db

SLUGS = ("vita-bull", "beef-liver", "boneflex", "vita-belle")
DEFAULT_LIMIT = 60
MAX_FAILS = 3
CONFIDENCES = ("high", "medium", "low")
INTRO = ("Lonvera sells three products: Vital Bull (beef-organ supplement for men roughly 40-65: testosterone, energy, prostate, performance), Beef Liver (grass-fed beef liver capsules, mostly women 30-65: hair, iron / B12, energy, "
         "belly, skin, alcohol, GLP-1, fertility) and Boneflex (joint and bone supplement for adults 50-75: joint pain, stiffness, mobility, bone density, menopause joints). Vita Belle is a fourth, smaller product for women "
         "(hormone-free, made for women) and has no catalog lines.")


def catalog_text(conn, product: str | None) -> tuple[str, dict]:
    """The taxonomy lines as one table the model picks from (only the ticket's product when it is already set), and {line_id: (slug, cluster, icp, pain, code)}."""
    rows = conn.execute("""SELECT l.id, p.slug, l.line_code, l.cluster, l.icp, l.pain_point FROM taxonomy_lines l JOIN products p ON p.id = l.product_id
                           WHERE (%s::text IS NULL OR p.slug = %s) ORDER BY p.slug, l.sort_order NULLS LAST, l.id""", (product or None, product or None)).fetchall()
    text = "\n".join(f"{r[0]} | {r[1]} | {r[3]} | {r[4]} | {r[5]}" for r in rows)
    return text, {r[0]: (r[1], r[3], r[4], r[5], r[2]) for r in rows}


def build_prompt(t: dict, cat_text: str) -> str:
    brief = (t["story"] or "").strip()[:6000]
    return (f"{INTRO}\n\nA creative partner added this ticket to our production board.\nConcept name: {t['concept_name']}\nVendor: {t['vendor']}\n"
            + (f"Product (already set, keep it): {t['product']}\n" if t["product"] else "") + (f"Brief link: {t['brief_url']}\n" if t["brief_url"] else "")
            + f"Brief:\n{brief or '(no text, only the name)'}\n\n"
            "Choose the product, ONE line of this catalog (id | product | market cluster | ICP | pain point) that the concept is aimed at, and whether the concept is NEW or an ITERATION (a variation, remake or new angle on an earlier concept; "
            "say iteration only when the brief says so). Pick the pain point the audience FEELS first, then the ICP. For Vita Belle (no lines) set line_id null and give cluster / icp / pain_point as short plain text.\n"
            f"Catalog:\n{cat_text}\n\n"
            'Answer with ONE JSON object and nothing else: {"product": "vita-bull|beef-liver|boneflex|vita-belle", "line_id": <number or null>, "cluster": "", "icp": "", "pain_point": "", "concept_kind": "new|iteration", '
            '"confidence": "high|medium|low", "reason": "one sentence"}')


def parse_answer(text: str, lines: dict, product: str | None) -> dict:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        raise ValueError("no JSON in the answer")
    d = json.loads(m.group(0))
    prod = d.get("product") if d.get("product") in SLUGS else None
    lid = d.get("line_id")
    if isinstance(lid, str) and lid.isdigit():
        lid = int(lid)
    line = lines.get(lid) if isinstance(lid, int) else None
    if line:
        prod = line[0]                                                               # the line decides the product
    if product:
        prod = product
        if line and line[0] != product:
            line, lid = None, None
    kind = d.get("concept_kind") if d.get("concept_kind") in ("new", "iteration") else None
    conf = d.get("confidence") if d.get("confidence") in CONFIDENCES else "low"
    txt = lambda k: re.sub(r"\s+", " ", str(d.get(k) or "")).strip()[:200]
    return {"product": prod, "line_id": lid if line else None, "line": line, "cluster": line[1] if line else txt("cluster"), "icp": line[2] if line else txt("icp"), "pain_point": line[3] if line else txt("pain_point"),
            "concept_kind": kind, "confidence": conf, "reason": txt("reason")}


def fetch_brief_link_text(url: str | None) -> str:
    return ""                                                                         # the brief link is passed to the model as text only; reading Google Docs needs the service account and is not done here


def tag_one(conn, t: dict, runner=subprocess.run, sleep=time.sleep) -> dict:
    cat_text, lines = catalog_text(conn, t["product"] or None)
    prompt = build_prompt(t, cat_text)
    last = None
    for attempt in range(3):
        try:
            with tempfile.TemporaryDirectory() as tmp:
                env = at.run_claude(prompt, tmp, runner=runner)
            return parse_answer(env.get("result") or "", lines, t["product"] or None)
        except at.StopRun:
            raise
        except Exception as e:                                                       # noqa: BLE001
            last = e
            if attempt < 2:
                sleep(at.BACKOFF[attempt])
    raise RuntimeError(db.scrub(last))


def write(conn, t: dict, a: dict) -> None:
    note = f"auto ({a['confidence']}): {a['reason']}"[:400]
    line = a["line"]
    conn.execute("""UPDATE creative_priorities_assignments SET
                      product = CASE WHEN product = '' THEN %s ELSE product END,
                      cluster = CASE WHEN cluster = '' THEN %s ELSE cluster END,
                      icp = CASE WHEN icp = '' THEN %s ELSE icp END,
                      pain_point = CASE WHEN pain_point = '' THEN %s ELSE pain_point END,
                      concept_kind = COALESCE(concept_kind, %s),
                      line_id = COALESCE(line_id, %s), line_code = CASE WHEN line_code = '' THEN %s ELSE line_code END,
                      tag_state = 'complete', tagged_at = now(), tag_note = %s, updated_at = now()
                    WHERE id = %s""", (a["product"] or "", a["cluster"], a["icp"], a["pain_point"], a["concept_kind"], a["line_id"], line[4] if line else "", note, t["id"]))
    conn.execute("INSERT INTO creative_priorities_assignment_events (assignment_id, actor, kind, stage_before, stage_after, detail) VALUES (%s,%s,'tagged',NULL,NULL,%s)",
                 (t["id"], "auto-" + at.MODEL, f"{a['product'] or '?'} / {a['cluster'] or '?'} / {a['pain_point'] or '?'} / {a['concept_kind'] or '?'} ({a['confidence']})"))


def run(conn, limit: int = DEFAULT_LIMIT, dry_run: bool = False, only_id: int | None = None, runner=subprocess.run, sleep=time.sleep) -> dict:
    st = {"candidates": 0, "tagged": 0, "failed": 0, "gave_up": 0, "stopped_early": None, "dry_run": dry_run}
    try:
        rows = conn.execute("""SELECT id, concept_name, vendor, product, story, brief_url, tag_note FROM creative_priorities_assignments
                               WHERE tag_state = 'pending' AND dropped_at IS NULL AND (%s::bigint IS NULL OR id = %s) ORDER BY id LIMIT %s""", (only_id, only_id, limit)).fetchall()
    except Exception:                                                                # noqa: BLE001  (migration 260 not applied yet)
        conn.rollback()
        db.log("  ticket_tags: tag_state does not exist yet, nothing to do")
        return st
    cols = ("id", "concept_name", "vendor", "product", "story", "brief_url", "tag_note")
    st["candidates"] = len(rows)
    for r in rows:
        t = dict(zip(cols, r))
        if dry_run:
            continue
        try:
            a = tag_one(conn, t, runner, sleep)
            write(conn, t, a)
            conn.commit()
            st["tagged"] += 1
        except at.StopRun as e:
            conn.rollback()
            st["stopped_early"] = str(e)[:300]
            break
        except Exception as e:                                                       # noqa: BLE001
            conn.rollback()
            m = re.search(r"attempt (\d+)", t["tag_note"] or "")
            fails = (int(m.group(1)) if m else 0) + 1
            gave_up = fails >= MAX_FAILS
            conn.execute("UPDATE creative_priorities_assignments SET tag_state = %s, tag_note = %s WHERE id = %s", ("failed" if gave_up else "pending", f"failed, attempt {fails}: {db.scrub(e)}"[:400], t["id"]))
            conn.commit()
            st["gave_up" if gave_up else "failed"] += 1
    db.log(f"  ticket_tags: {st['tagged']} tagged, {st['failed']} failed (retry), {st['gave_up']} gave up, of {st['candidates']}")
    return st


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    ap.add_argument("--id", type=int, help="only this assignment id")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    with db.JobRun("ticket_tags") as job, db.connect() as conn:
        job.stats = {}
        st = run(conn, a.limit, a.dry_run, a.id)
        job.stats = st
        job.rows_written = st["tagged"]
        if st["stopped_early"]:
            raise db.JobFailed("ticket_tags stopped early: " + st["stopped_early"])


if __name__ == "__main__":
    try:
        main()
    except Exception as e:                                                           # noqa: BLE001
        db.log("ticket_tags FAILED:", db.scrub(e))
        sys.exit(1)
