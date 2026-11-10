"""Nightly auto-tagging: give every current ad that has NO linemap tag a Communication Overview line (cluster / ICP / pain point / trigger).

What it does: for Vita Bull, Beef Liver and Boneflex it finds ad families (tracker rows grouped by family_key) whose tracker rows carry no current
ad_tags row in tag_set 'linemap', that have spend or were launched in the last RECENT_DAYS, gathers the copy / hook / transcript start and the stored
thumbnail (creative_assets; fetched from Meta first when missing), and asks headless Claude Code (`claude -p`, Sonnet, only the Read tool, cwd = a temp dir
holding the picture) to pick ONE line code from the product's catalog (etl/autotag/catalogs/<slug>.json). Auth = CLAUDE_CODE_OAUTH_TOKEN (Claude
subscription, from `claude setup-token`); no API key, usage counts against the plan.
Writes ad_tags(tag_set='linemap', version='auto-YYYY-MM-DD', tagger='auto-claude-sonnet-5-5', extra.source='auto'). It only fills rows that have NO current
linemap tag: human / cycle tags are never overwritten or marked not-current. Re-running the same day finds nothing new (the day's tags are current); with
--retag-today the day's own auto rows are replaced (never any other version). Videos: cover image + copy + hook + transcript start only (method
'cover + copy (auto)', confidence capped at medium); no picture: method 'text only (auto)', capped at medium.
Safety: missing token / auth error / usage limit -> the run stops early and is recorded failed in etl_runs (the nightly pipeline carries on, see
scripts/deploy/run_etl.sh); hard cap per run (--limit, default 150 ads); 3 tries per ad with backoff.
Usage: python -m etl.auto_tag [--product slug] [--limit N] [--dry-run] [--retag-today] [--no-assets]"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from . import db

PRODUCTS = ("vita-bull", "beef-liver", "boneflex")
MODEL = "claude-sonnet-5-5"
DEFAULT_LIMIT = 150
RECENT_DAYS = 60                       # no spend yet: still a candidate when launched this recently
MAX_TRIES = 3
BACKOFF = (5, 20)                      # seconds before try 2 and 3
CALL_TIMEOUT = 240                     # seconds per claude call
MAX_FAILS = 3                          # a family that failed this many nights is left alone (app_meta autotag_fail:<family>)
CONFIDENCES = ("high", "medium", "low")
TAGGER = "auto-claude-sonnet-5-5"
HERE = Path(__file__).resolve().parent / "autotag"
INTROS = {
    "vita-bull": "Vital Bull is a beef-organ supplement sold to men roughly 40-65.",
    "beef-liver": "Lonvera Beef Liver is a grass-fed beef liver capsule, sold mostly to women roughly 30-65 (hair, energy, iron / B12, belly, skin, alcohol, GLP-1) and some men.",
    "boneflex": "Lonvera Boneflex is a joint and bone supplement (bone marrow with collagen, glucosamine, chondroitin, MSM and curcumin), sold mostly to adults roughly 50-75, "
                "men and women (joint pain, stiffness, mobility and independence, bone density, menopause joints, GLP-1 muscle and bone loss, 'tried glucosamine before').",
}
# env passed to the claude subprocess: nothing else from the service (database url, Meta / Upstack tokens ...) is handed over
ENV_KEEP = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "TZ", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "https_proxy", "http_proxy", "no_proxy",
            "SSL_CERT_FILE", "NODE_EXTRA_CA_CERTS", "CLAUDE_CODE_OAUTH_TOKEN")
AUTH_RE = re.compile(r"invalid api key|please run /login|not logged in|authentication|unauthorized|oauth|token (has )?expired|401", re.I)
LIMIT_RE = re.compile(r"usage limit|limit reached|rate.?limit|quota|too many requests|429|exceeded your", re.I)


class StopRun(Exception):
    """Auth failure / usage limit: no point calling again tonight."""


class CallFailed(Exception):
    """One ad could not be tagged after the tries (the run goes on with the next ad)."""


# ---- catalogs / prompt ----------------------------------------------------------------------------------------------------------------

def load_catalog(slug: str) -> list[dict]:
    return json.loads((HERE / "catalogs" / f"{slug}.json").read_text())


def lines_table(cat: list[dict]) -> str:
    rows = ["| Code | Market Cluster | ICP | Pain Point | Emotional Trigger | Story |", "|---|---|---|---|---|---|"]
    for c in cat:
        rows.append(f"| {c['line']} | {c['cluster']} | {c['icp']} | {c['pain']} | {c.get('trigger') or ''} | {c.get('story') or ''} |")
    return "\n".join(rows)


def instructions(slug: str, cat: list[dict]) -> str:
    return (HERE / "instructions.md").read_text().replace("{PRODUCT_INTRO}", INTROS[slug]).replace("{LINES_TABLE}", lines_table(cat))


def build_prompt(slug: str, cat: list[dict], unit: dict) -> str:
    return instructions(slug, cat) + "\n## The ad\n" + json.dumps(unit, ensure_ascii=False, indent=1) + "\n"


# ---- candidates -----------------------------------------------------------------------------------------------------------------------

def _clip(s, n) -> str | None:
    s = " ".join(str(s).split()) if s else ""
    return s[:n] if s else None


def candidates(conn, slugs, day: dt.date, retag_today: bool = False) -> dict[str, list[dict]]:
    """{slug: [family dicts]} biggest spend first. A family qualifies when at least one of its (live, real) tracker rows has no current linemap tag and the
    family has spend (since launch) or a launch date within RECENT_DAYS. `rows` = ids of the family's tracker rows that need a tag."""
    ver = "auto-" + day.isoformat()
    has_del = conn.execute("SELECT 1 FROM information_schema.columns WHERE table_name='tracker_rows' AND column_name='deleted_at'").fetchone()
    live = " AND t.deleted_at IS NULL" if has_del else ""
    sql = f"""
    SELECT p.slug, t.family_key, t.id, t.concept_id, t.ad_family, t.ad_type, t.concept_name, t.description, t.hypothesis, t.meta_ad_name, t.primary_text,
           t.headlines, t.ad_description, t.transcript, t.tracker_cluster, t.tracker_icp, t.tracker_pain, t.tracker_emotion,
           coalesce(am.spend_launch, 0), coalesce(t.launch_date, am.launch_date),
           (SELECT g.version FROM ad_tags g WHERE g.tracker_row_id = t.id AND g.tag_set = 'linemap' AND g.is_current) AS cur_version
    FROM tracker_rows t JOIN products p ON p.id = t.product_id LEFT JOIN ad_metrics am ON am.tracker_row_id = t.id
    WHERE p.slug = ANY(%s) AND btrim(t.concept_id) <> ''{live}
    ORDER BY t.family_key, t.sheet_row NULLS LAST, t.id"""
    fams: dict[tuple, dict] = {}
    for r in conn.execute(sql, (list(slugs),)).fetchall():
        (slug, fk, rid, cid, af, ty, cname, desc, hyp, mname, ptext, heads, adesc, trans, tcl, ticp, tpain, temo, spend, launch, curver) = r
        f = fams.setdefault((slug, fk), {"slug": slug, "family_key": fk, "rows": [], "spend": 0.0, "launch": None, "rep": None, "all_rows": 0})
        f["all_rows"] += 1
        f["spend"] = max(f["spend"], float(spend))
        if launch and (f["launch"] is None or launch > f["launch"]):
            f["launch"] = launch
        if curver is None or (retag_today and curver == ver):
            f["rows"].append(rid)
        copy_len = sum(len(x or "") for x in (ptext or [])) + sum(len(x or "") for x in (heads or [])) + len(adesc or "") + len(trans or "")
        if f["rep"] is None or copy_len > f["rep"]["_len"]:
            f["rep"] = {"_len": copy_len, "concept_id": cid, "type": ty, "name": mname or cname, "description": desc, "hypothesis": hyp,
                        "primary_text": [x for x in (ptext or []) if x], "headlines": [x for x in (heads or []) if x], "ad_description": adesc,
                        "transcript": trans, "existing_tags": {"cluster": tcl, "icp": ticp, "pain_point": tpain, "emotion": temo}}
    cutoff = day - dt.timedelta(days=RECENT_DAYS)
    out: dict[str, list[dict]] = {s: [] for s in slugs}
    for f in fams.values():
        if f["rows"] and (f["spend"] > 0 or (f["launch"] is not None and f["launch"] >= cutoff)):
            out[f["slug"]].append(f)
    for s in out:
        out[s].sort(key=lambda f: (-f["spend"], str(f["launch"] or ""), f["family_key"]))
    return out


def gather(conn, f: dict) -> tuple[dict, tuple | None]:
    """-> (unit dict for the prompt, (bytes, mime) or None). Copy from the tracker row with the most copy + family_content (what ran on Meta) + hook."""
    fk = f["family_key"]
    rep = {k: v for k, v in f["rep"].items() if k != "_len"}
    fc = conn.execute("SELECT meta_body, meta_title, meta_desc, transcript, is_video FROM family_content WHERE family_key=%s", (fk,)).fetchone()
    hook = conn.execute("SELECT h.hook FROM ad_hooks h JOIN tracker_rows t ON t.id = h.tracker_row_id WHERE t.family_key=%s AND t.id = ANY(%s) LIMIT 1",
                        (fk, f["rows"])).fetchone()
    img = conn.execute("SELECT bytes, mime FROM creative_assets WHERE family_key=%s AND kind='thumb' ORDER BY idx LIMIT 1", (fk,)).fetchone()
    is_video = (rep.get("type") or "").upper() == "VID" or bool(fc and fc[4])
    unit = {"name": _clip(rep.get("name"), 200), "concept_id": rep.get("concept_id"), "type": "video" if is_video else "static image",
            "primary_text": [_clip(x, 700) for x in rep["primary_text"][:3]], "headlines": [_clip(x, 200) for x in rep["headlines"][:3]],
            "ad_description": _clip(rep.get("ad_description"), 300), "description": _clip(rep.get("description"), 400), "hypothesis": _clip(rep.get("hypothesis"), 300),
            "hook": _clip(hook[0], 300) if hook else None, "meta_body": _clip(fc[0], 800) if fc else None, "meta_title": _clip(fc[1], 200) if fc else None,
            "transcript": _clip(rep.get("transcript") or (fc[3] if fc else None), 900), "existing_tags": rep["existing_tags"]}
    unit = {k: v for k, v in unit.items() if v not in (None, "", [])}
    unit["_is_video"] = is_video
    return unit, ((bytes(img[0]), img[1]) if img else None)


def has_words(unit: dict) -> bool:
    return any(unit.get(k) for k in ("primary_text", "headlines", "hook", "meta_body", "meta_title", "transcript", "ad_description", "description"))


# ---- claude call ----------------------------------------------------------------------------------------------------------------------

def claude_cmd(bin_: str = "claude") -> list[str]:
    """Headless, Read only, nothing persisted, no user / project settings or MCP servers."""
    return [bin_, "-p", "--model", MODEL, "--output-format", "json", "--tools", "Read", "--allowedTools", "Read", "--permission-mode", "dontAsk",
            "--no-session-persistence", "--setting-sources", "", "--strict-mcp-config"]


def claude_env() -> dict:
    return {k: v for k, v in os.environ.items() if k in ENV_KEEP}


def parse_answer(text: str, catalog_codes: set[str]) -> dict:
    """The model's final message -> validated dict {line, confidence, runner_up, why, saw}. Raises ValueError with a short reason."""
    s = (text or "").strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s)
    if not s.startswith("{"):
        m = re.search(r"\{.*\}", s, re.S)
        s = m.group(0) if m else s
    try:
        d = json.loads(s)
    except json.JSONDecodeError as e:
        raise ValueError(f"not JSON: {e}") from None
    if not isinstance(d, dict):
        raise ValueError("answer is not an object")
    line = str(d.get("line") or "").strip().upper()
    if line not in catalog_codes:
        raise ValueError(f"line {line!r} is not in the catalog")
    conf = str(d.get("confidence") or "").strip().lower()
    if conf not in CONFIDENCES:
        raise ValueError(f"confidence {d.get('confidence')!r} is not high / medium / low")
    ru = str(d.get("runner_up") or "").strip().upper() or None
    if ru not in catalog_codes or ru == line:
        ru = None
    return {"line": line, "confidence": conf, "runner_up": ru, "why": _clip(d.get("why"), 240), "saw": _clip(d.get("saw"), 240)}


def run_claude(prompt: str, cwd: str, runner=subprocess.run) -> dict:
    """One `claude -p` call -> the parsed CLI envelope (dict). Raises StopRun on auth / usage-limit errors, RuntimeError (transient) otherwise."""
    try:
        p = runner(claude_cmd(), input=prompt, capture_output=True, text=True, cwd=cwd, env=claude_env(), timeout=CALL_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"claude timed out after {CALL_TIMEOUT}s") from None
    except FileNotFoundError:
        raise StopRun("claude CLI not found on PATH (@anthropic-ai/claude-code is not installed in this image)") from None
    out = (p.stdout or "").strip()
    env = None
    try:
        env = json.loads(out) if out else None
    except json.JSONDecodeError:
        pass
    failed = bool(p.returncode) or (isinstance(env, dict) and bool(env.get("is_error")))
    msg = ""
    if failed:
        raw = env.get("result") if isinstance(env, dict) and env.get("result") else (p.stderr or out)
        msg = db.scrub(raw)[:300]
    if failed:
        if LIMIT_RE.search(msg):
            raise StopRun("claude usage / rate limit reached: " + msg)
        if AUTH_RE.search(msg):
            raise StopRun("claude authentication failed (check CLAUDE_CODE_OAUTH_TOKEN): " + msg)
        raise RuntimeError(f"claude exit {p.returncode}: {msg}")
    if not isinstance(env, dict) or "result" not in env:
        raise RuntimeError("claude output was not the JSON envelope")
    return env


def tag_one(slug: str, cat: list[dict], unit: dict, image: tuple | None, runner=subprocess.run, sleep=time.sleep) -> tuple[dict, dict]:
    """-> (answer, usage). Writes the picture into a fresh temp dir (the claude cwd), validates the answer, retries MAX_TRIES times."""
    codes = {c["line"] for c in cat}
    u = {k: v for k, v in unit.items() if not k.startswith("_")}
    tmp = tempfile.mkdtemp(prefix="autotag-")
    usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_creation_tokens": 0, "attempts": 0}
    try:
        if image:
            name = "ad." + ("png" if "png" in image[1] else "jpg")
            Path(tmp, name).write_bytes(image[0])
            u["image_file"] = name
        prompt = build_prompt(slug, cat, u)
        last = "no attempt"
        for attempt in range(1, MAX_TRIES + 1):
            usage["attempts"] = attempt
            try:
                env = run_claude(prompt, tmp, runner)
                usage["calls"] += 1
                us = env.get("usage") or {}
                usage["input_tokens"] += int(us.get("input_tokens") or 0)
                usage["output_tokens"] += int(us.get("output_tokens") or 0)
                usage["cache_read_tokens"] += int(us.get("cache_read_input_tokens") or 0)
                usage["cache_creation_tokens"] += int(us.get("cache_creation_input_tokens") or 0)
                return parse_answer(env.get("result"), codes), usage
            except ValueError as e:
                last = str(e)
            except RuntimeError as e:
                last = str(e)
            if attempt < MAX_TRIES:
                sleep(BACKOFF[attempt - 1])
        raise CallFailed(last)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def final_confidence(conf: str, is_video: bool, has_image: bool) -> str:
    """Videos (cover frame only) and ads with no picture are capped at medium."""
    if (is_video or not has_image) and conf == "high":
        return "medium"
    return conf


# ---- write ----------------------------------------------------------------------------------------------------------------------------

def method_for(is_video: bool, has_image: bool) -> str:
    if not has_image:
        return "text only (auto)"
    return "cover + copy (auto)" if is_video else "image + copy (auto)"


def write_tags(conn, slug: str, f: dict, ans: dict, unit: dict, has_image: bool, version: str, attempts: int, line_ids: dict) -> int:
    """Insert the tag on every still-untagged row of the family. Never touches another version or an existing current tag."""
    is_video = bool(unit.get("_is_video"))
    conf = final_confidence(ans["confidence"], is_video, has_image)
    method = method_for(is_video, has_image)
    lid = line_ids[ans["line"]]
    cat_row = lid["row"]
    extra = db.jsonb({"source": "auto", "model": MODEL, "line_code": ans["line"], "needs_review": conf == "low", "model_confidence": ans["confidence"],
                      "attempts": attempts, "has_image": has_image, "is_video": is_video})
    with conn.transaction():
        conn.execute("DELETE FROM ad_tags WHERE tag_set='linemap' AND version=%s AND tracker_row_id = ANY(%s)", (version, f["rows"]))
        n = 0
        for rid in f["rows"]:
            n += conn.execute("""INSERT INTO ad_tags (tracker_row_id, tag_set, version, is_current, line_id, cluster, icp, pain_point, emotional_trigger, confidence, method,
                                 seen, why, runner_up, tagger, extra)
                                 SELECT %s,'linemap',%s,true,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                                 WHERE NOT EXISTS (SELECT 1 FROM ad_tags WHERE tracker_row_id=%s AND tag_set='linemap' AND is_current)""",
                              (rid, version, lid["id"], cat_row[0], cat_row[1], cat_row[2], cat_row[3], conf, method, ans.get("saw"), ans.get("why"),
                               ans.get("runner_up"), TAGGER, extra, rid)).rowcount
    return n


def taxonomy_ids(conn, slug: str) -> dict[str, dict]:
    return {code: {"id": i, "row": (c, ic, p, t)} for i, code, c, ic, p, t in conn.execute(
        "SELECT l.id, l.line_code, l.cluster, l.icp, l.pain_point, l.emotional_trigger FROM taxonomy_lines l JOIN products p ON p.id=l.product_id WHERE p.slug=%s", (slug,)).fetchall()}


# ---- run ------------------------------------------------------------------------------------------------------------------------------

def run(conn, slugs=PRODUCTS, limit: int = DEFAULT_LIMIT, dry_run: bool = False, retag_today: bool = False, fetch_assets: bool = True,
        day: dt.date | None = None, runner=subprocess.run, sleep=time.sleep, log=db.log) -> dict:
    day = day or dt.date.today()
    version = "auto-" + day.isoformat()
    t0 = time.time()
    stats: dict = {"version": version, "dry_run": dry_run, "limit": limit, "by_product": {}, "tagged_families": 0, "tagged_rows": 0, "no_content": 0,
                   "failed": 0, "skipped_repeat_failures": 0, "confidence": {}, "calls": 0, "attempts": 0, "input_tokens": 0, "output_tokens": 0,
                   "cache_read_tokens": 0, "cache_creation_tokens": 0, "stopped_early": None, "samples": []}
    if not dry_run and not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        raise db.JobFailed("CLAUDE_CODE_OAUTH_TOKEN is not set: auto-tagging needs the Claude subscription token (generate it once with `claude setup-token`, "
                           "set it on the etl service). Nothing was tagged.")
    flag = conn.execute("SELECT slug, tag_set FROM products WHERE slug = ANY(%s)", (list(slugs),)).fetchall()
    stats["products_not_linemap"] = [s for s, ts in flag if ts != "linemap"]
    if stats["products_not_linemap"]:
        log(f"  WARN tags are written but not shown for {stats['products_not_linemap']}: products.tag_set is not 'linemap'")
    if fetch_assets and not dry_run:                                       # thumbnails for new ads (best effort; the Meta token is the etl service's own)
        try:
            from . import assets_pull
            for s in slugs:
                stats.setdefault("assets", {})[s] = assets_pull.run(conn, limit=limit, product=s)
        except Exception as e:                                           # noqa: BLE001
            stats["assets_error"] = db.scrub(e)[:200]
            log(f"  assets_pull skipped: {stats['assets_error']}")
    cands = candidates(conn, slugs, day, retag_today)
    fails = {k[len("autotag_fail:"):]: v for k, v in conn.execute("SELECT key, value FROM app_meta WHERE key LIKE 'autotag_fail:%'").fetchall()}
    budget = limit
    for s in slugs:
        stats["by_product"][s] = {"candidates": len(cands[s]), "candidate_rows": sum(len(f["rows"]) for f in cands[s]), "tagged": 0}
    if dry_run:
        for s in slugs:
            cat = load_catalog(s)
            for f in cands[s][:2]:
                unit, img = gather(conn, f)
                stats["samples"].append({"product": s, "family": f["family_key"], "rows": len(f["rows"]), "has_image": bool(img),
                                         "prompt_chars": len(build_prompt(s, cat, {k: v for k, v in unit.items() if not k.startswith('_')})),
                                         "unit": {k: v for k, v in unit.items() if not k.startswith("_")}})
        stats["duration_s"] = round(time.time() - t0, 1)
        return stats
    try:
        for s in slugs:
            cat = load_catalog(s)
            ids = taxonomy_ids(conn, s)
            missing = sorted({c["line"] for c in cat} - set(ids))
            if missing:
                log(f"  WARN {s}: catalog codes not in taxonomy_lines (skipped when chosen): {missing}")
            for f in cands[s]:
                if budget <= 0:
                    stats["capped"] = True
                    break
                if int(fails.get(f"{s}:{f['family_key']}") or 0) >= MAX_FAILS:
                    stats["skipped_repeat_failures"] += 1
                    continue
                unit, img = gather(conn, f)
                if img is None and not has_words(unit):
                    stats["no_content"] += 1
                    continue
                budget -= 1
                try:
                    ans, usage = tag_one(s, cat, unit, img, runner, sleep)
                except CallFailed as e:
                    stats["failed"] += 1
                    k = f"autotag_fail:{s}:{f['family_key']}"
                    db.set_meta(conn, k, str(int(fails.get(f"{s}:{f['family_key']}") or 0) + 1))
                    conn.commit()
                    log(f"  {s} {f['family_key']}: failed ({db.scrub(e)[:120]})")
                    continue
                for k in ("calls", "attempts", "input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens"):
                    stats[k] += usage[k]
                if ans["line"] not in ids:
                    stats["failed"] += 1
                    log(f"  {s} {f['family_key']}: {ans['line']} is not in taxonomy_lines, skipped")
                    continue
                n = write_tags(conn, s, f, ans, unit, img is not None, version, usage["attempts"], ids)
                conn.commit()
                stats["tagged_families"] += 1
                stats["tagged_rows"] += n
                stats["by_product"][s]["tagged"] += 1
                c = final_confidence(ans["confidence"], bool(unit.get("_is_video")), img is not None)
                stats["confidence"][c] = stats["confidence"].get(c, 0) + 1
            else:
                continue
            break
    except StopRun as e:
        stats["stopped_early"] = db.scrub(e)[:300]
    stats["duration_s"] = round(time.time() - t0, 1)
    stats["avg_s_per_ad"] = round(stats["duration_s"] / max(1, stats["tagged_families"] + stats["failed"]), 1)
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--product", choices=PRODUCTS)
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="hard cap on ads (claude calls' units) per run (default 150)")
    ap.add_argument("--dry-run", action="store_true", help="count candidates and show sample units; no claude call, no writes")
    ap.add_argument("--retag-today", action="store_true", help="also redo the rows tagged by today's own auto version (never other versions)")
    ap.add_argument("--no-assets", action="store_true", help="do not try to fetch missing thumbnails from Meta first")
    a = ap.parse_args()
    slugs = (a.product,) if a.product else PRODUCTS
    with db.JobRun("auto_tag") as job, db.connect() as conn:
        job.stats = {}
        st = run(conn, slugs, a.limit, a.dry_run, a.retag_today, not a.no_assets)
        job.stats = st
        job.rows_written = st["tagged_rows"]
        db.log(f"  {json.dumps({k: v for k, v in st.items() if k != 'samples'}, default=str)}")
        if st["stopped_early"]:
            raise db.JobFailed("auto_tag stopped early: " + st["stopped_early"])


if __name__ == "__main__":
    try:
        main()
    except Exception as e:                                                # noqa: BLE001
        db.log("auto_tag FAILED:", db.scrub(e))
        sys.exit(1)
