"""Nightly auto-tagging: give every current ad that has NO linemap tag a Communication Overview line (cluster / ICP / pain point / trigger).

What it does: for Vita Bull, Beef Liver and Boneflex it finds ad families (tracker rows grouped by family_key) whose tracker rows carry no current
ad_tags row in tag_set 'linemap', that have spend or were launched in the last RECENT_DAYS, gathers the copy / hook / transcript (real audio first, then tracker, then family_content; up to TRANSCRIPT_CAP chars) and the stored
thumbnail (creative_assets; fetched from Meta first when missing), and asks headless Claude Code (`claude -p`, Sonnet, only the Read tool, cwd = a temp dir
holding the picture) to pick ONE line code from the product's catalog (etl/autotag/catalogs/<slug>.json). Auth = CLAUDE_CODE_OAUTH_TOKEN (Claude
subscription, from `claude setup-token`); no API key, usage counts against the plan.
Writes ad_tags(tag_set='linemap', version='auto-YYYY-MM-DD', tagger='auto-claude-sonnet-5-5', extra.source='auto'). It only fills rows that have NO current
linemap tag: human / cycle tags are never overwritten or marked not-current. Re-running the same day finds nothing new (the day's tags are current); with
--retag-today the day's own auto rows are replaced (never any other version). Videos: cover image + copy + hook + transcript only (method
'cover + copy (auto)', '+ audio' variant when the real-audio transcript was used, confidence capped at medium); no picture: method 'text only (auto)', capped at medium.
Safety: missing token / auth error / usage limit -> the run stops early and is recorded failed in etl_runs (the nightly pipeline carries on, see
scripts/deploy/run_etl.sh); hard cap per run (--limit, default 400 ads); 3 tries per ad with backoff.
Rules (2026-11-10): the ad's PAIN (the felt symptom) is chosen first, then ICP, then trigger; the tracker Pain Point is the default answer (kept_tracker_pain / override_reason).
Second source (2026-11-25, migration 285): Meta ad families that have NO tracker row (the tracker is not used for them). Candidates = families with Meta spend since NATIVE_SINCE (>= NATIVE_MIN_SPEND),
product from unmatched_meta, no live tracker row, no current tag in ad_family_line_tags; highest spend first. Same prompt, closed catalog and transcript order (real audio first, whole script up to 4000 chars, then
Meta copy); cover image from creative_assets. The tag is written to ad_family_line_tags keyed on the Meta family (family_key = COALESCE(meta_family_map.tracker_family_key, meta_ads.meta_family_key), the key
etl/own_ad_tags.py uses), supersede-never-delete. Production tickets with a line (creative_priorities_assignments.line_id) and Meta ad ids (launched_ad_ids / variations[].ad_id) tag the ad's family with the
ticket's own line, no model call (method 'ticket (production assignment)'); an existing tag is never overwritten. --retag-audio also upgrades native auto tags made without audio. No tracker row is created, read for tags or written.
Usage: python -m etl.auto_tag [--product slug] [--limit N] [--dry-run] [--retag-today] [--retag-audio] [--no-assets] [--native-only | --no-native] [--min-spend N] [--since YYYY-MM-DD]"""
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
DEFAULT_LIMIT = 400
RECENT_DAYS = 60                       # no spend yet: still a candidate when launched this recently
MAX_TRIES = 3
BACKOFF = (5, 20)                      # seconds before try 2 and 3
CALL_TIMEOUT = 240                     # seconds per claude call
MAX_FAILS = 3                          # a family that failed this many nights is left alone (app_meta autotag_fail:<family>)
NATIVE_SINCE = "2026-05-06"            # Meta ad-level data starts here; native candidates need spend on/after it
NATIVE_MIN_SPEND = 50.0                # a family below this lifetime spend (since NATIVE_SINCE) is not worth a model call
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
ENV_KEEP = ("PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TMPDIR", "TZ", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "https_proxy", "http_proxy", "no_proxy",
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


def audio_families(conn) -> set[str]:
    """Tracker family keys that have a real-audio transcript (directly or through meta_family_map)."""
    q = """SELECT x.family_key FROM ad_transcript x WHERE x.source <> 'tracker_copy' AND btrim(x.transcript) <> '' AND lower(btrim(x.transcript, ' []')) <> 'no speech'
           UNION SELECT m.tracker_family_key FROM ad_transcript x JOIN meta_family_map m ON m.meta_family_key = x.family_key
           WHERE x.source <> 'tracker_copy' AND btrim(x.transcript) <> '' AND lower(btrim(x.transcript, ' []')) <> 'no speech' AND m.tracker_family_key IS NOT NULL"""
    return {r[0] for r in conn.execute(q).fetchall()}


def candidates(conn, slugs, day: dt.date, retag_today: bool = False, retag_audio: bool = False) -> dict[str, list[dict]]:
    """{slug: [family dicts]} biggest spend first. A family qualifies when at least one of its (live, real) tracker rows has no current linemap tag and the
    family has spend (since launch) or a launch date within RECENT_DAYS. `rows` = ids of the family's tracker rows that need a tag.
    retag_audio: also the rows whose current tag is an AUTO tag made without the real-audio transcript, for families that now have one."""
    ver = "auto-" + day.isoformat()
    has_del = conn.execute("SELECT 1 FROM information_schema.columns WHERE table_name='tracker_rows' AND column_name='deleted_at'").fetchone()
    live = " AND t.deleted_at IS NULL" if has_del else ""
    sql = f"""
    SELECT p.slug, t.family_key, t.id, t.concept_id, t.ad_family, t.ad_type, t.concept_name, t.description, t.hypothesis, t.meta_ad_name, t.primary_text,
           t.headlines, t.ad_description, t.transcript, t.tracker_cluster, t.tracker_icp, t.tracker_pain, t.tracker_emotion,
           (btrim(coalesce(t.raw->>'Brief Link', '')) <> ''),
           coalesce(am.spend_launch, 0), coalesce(t.launch_date, am.launch_date),
           (SELECT g.version FROM ad_tags g WHERE g.tracker_row_id = t.id AND g.tag_set = 'linemap' AND g.is_current) AS cur_version,
           (SELECT (g.extra->>'source' = 'auto' AND coalesce(g.method, '') NOT LIKE '%%audio%%') FROM ad_tags g WHERE g.tracker_row_id = t.id AND g.tag_set = 'linemap' AND g.is_current) AS cur_auto_no_audio
    FROM tracker_rows t JOIN products p ON p.id = t.product_id LEFT JOIN ad_metrics am ON am.tracker_row_id = t.id
    WHERE p.slug = ANY(%s) AND btrim(t.concept_id) <> ''{live}
    ORDER BY t.family_key, t.sheet_row NULLS LAST, t.id"""
    aud = audio_families(conn) if retag_audio else set()
    fams: dict[tuple, dict] = {}
    for r in conn.execute(sql, (list(slugs),)).fetchall():
        (slug, fk, rid, cid, af, ty, cname, desc, hyp, mname, ptext, heads, adesc, trans, tcl, ticp, tpain, temo, briefed, spend, launch, curver, auto_no_audio) = r
        f = fams.setdefault((slug, fk), {"slug": slug, "family_key": fk, "rows": [], "spend": 0.0, "launch": None, "rep": None, "all_rows": 0})
        f["all_rows"] += 1
        f["spend"] = max(f["spend"], float(spend))
        if launch and (f["launch"] is None or launch > f["launch"]):
            f["launch"] = launch
        if curver is None or (retag_today and curver == ver) or (retag_audio and auto_no_audio and fk in aud):
            f["rows"].append(rid)
        copy_len = sum(len(x or "") for x in (ptext or [])) + sum(len(x or "") for x in (heads or [])) + len(adesc or "") + len(trans or "")
        if f["rep"] is None or copy_len > f["rep"]["_len"]:
            f["rep"] = make_rep(copy_len, cid, ty, cname, mname, desc, hyp, ptext, heads, adesc, trans, tcl, ticp, tpain, temo, bool(briefed))
        if briefed:
            f["rep"]["briefed"] = True
    cutoff = day - dt.timedelta(days=RECENT_DAYS)
    out: dict[str, list[dict]] = {s: [] for s in slugs}
    for f in fams.values():
        if f["rows"] and (f["spend"] > 0 or (f["launch"] is not None and f["launch"] >= cutoff)):
            out[f["slug"]].append(f)
    for s in out:
        out[s].sort(key=lambda f: (-f["spend"], str(f["launch"] or ""), f["family_key"]))
    return out


def make_rep(copy_len, cid, ty, cname, mname, desc, hyp, ptext, heads, adesc, trans, tcl, ticp, tpain, temo, briefed) -> dict:
    """The tracker-row fields the prompt is built from (shared with etl.retag_pain, which tags single rows)."""
    return {"_len": copy_len, "concept_id": cid, "type": ty, "name": mname or cname, "description": desc, "hypothesis": hyp,
            "primary_text": [x for x in (ptext or []) if x], "headlines": [x for x in (heads or []) if x], "ad_description": adesc,
            "transcript": trans, "briefed": bool(briefed), "existing_tags": {"cluster": tcl, "icp": ticp, "pain_point": tpain, "emotion": temo}}


def norm(x) -> str:
    return " ".join(str(x or "").split()).lower()


PLACEHOLDER_PAINS = {"", "no clear pain point", "no clear", "none", "n/a", "untagged", "(untagged)"}


def tracker_pain_lines(pain, cat: list[dict]) -> list[str]:
    """Codes of the product's lines whose pain point equals the tracker's Pain Point (case / space-insensitive). [] when the tracker pain is blank,
    a placeholder, or on no line of the product (then the tracker pain is not a usable default)."""
    n = norm(pain)
    if n in PLACEHOLDER_PAINS:
        return []
    return [c["line"] for c in cat if norm(c["pain"]) == n]


def annotate_tracker_pain(unit: dict, cat: list[dict]) -> dict:
    """Add the default-answer block to a prompt unit: tracker_pain_point, whether it was briefed, and the lines that carry it."""
    pain = (unit.get("existing_tags") or {}).get("pain_point")
    codes = tracker_pain_lines(pain, cat)
    if codes:
        briefed = bool(unit.get("briefed"))
        unit["tracker_pain_point"] = pain
        unit["tracker_pain_lines"] = codes
        unit["tracker_pain_status"] = ("BRIEFED: the team briefed this concept and chose this pain point on purpose (strong default)" if briefed else
                                       "NOT briefed: this pain point was tagged by a machine from the copy earlier (a strong prior, not proof)")
    unit.pop("briefed", None)
    return unit


def gather(conn, f: dict) -> tuple[dict, tuple | None]:
    """-> (unit dict for the prompt, (bytes, mime) or None). Copy from the tracker row with the most copy + family_content (what ran on Meta) + hook."""
    fk = f["family_key"]
    rep = {k: v for k, v in f["rep"].items() if k != "_len"}
    fc = conn.execute("SELECT meta_body, meta_title, meta_desc, transcript, is_video FROM family_content WHERE family_key=%s", (fk,)).fetchone()
    hook = conn.execute("SELECT h.hook FROM ad_hooks h JOIN tracker_rows t ON t.id = h.tracker_row_id WHERE t.family_key=%s AND t.id = ANY(%s) LIMIT 1",
                        (fk, f["rows"])).fetchone()
    img = conn.execute("SELECT bytes, mime FROM creative_assets WHERE family_key=%s AND kind='thumb' ORDER BY idx LIMIT 1", (fk,)).fetchone()
    is_video = (rep.get("type") or "").upper() == "VID" or bool(fc and fc[4])
    aud = audio_transcript(conn, fk)            # transcript source order: real audio, then the tracker's typed transcript, then family_content
    trans, tsrc = (aud, "audio") if aud else ((rep.get("transcript"), "tracker") if rep.get("transcript") else ((fc[3], "family_content") if fc and fc[3] else (None, None)))
    unit = {"name": _clip(rep.get("name"), 200), "concept_id": rep.get("concept_id"), "type": "video" if is_video else "static image",
            "primary_text": [_clip(x, 700) for x in rep["primary_text"][:3]], "headlines": [_clip(x, 200) for x in rep["headlines"][:3]],
            "ad_description": _clip(rep.get("ad_description"), 300), "description": _clip(rep.get("description"), 400), "hypothesis": _clip(rep.get("hypothesis"), 300),
            "hook": _clip(hook[0], 300) if hook else None, "meta_body": _clip(fc[0], 800) if fc else None, "meta_title": _clip(fc[1], 200) if fc else None,
            "transcript": _clip(trans, TRANSCRIPT_CAP), "existing_tags": rep["existing_tags"], "briefed": bool(rep.get("briefed"))}
    unit = {k: v for k, v in unit.items() if v not in (None, "", [], False)}
    unit["_is_video"] = is_video
    unit["_transcript_source"] = tsrc if unit.get("transcript") else None
    return unit, ((bytes(img[0]), img[1]) if img else None)


TRANSCRIPT_CAP = 4000                  # chars of the script handed to the tagger (the whole script for nearly every ad)
NO_SPEECH = re.compile(r"^\[?\s*no speech\s*\]?$", re.I)


def audio_transcript(conn, fk: str) -> str | None:
    """Real-audio transcript (ad_transcript, written by etl.transcribe_ads / etl.own_ad_tags) for the tracker family or any Meta family mapped to it.
    Rows whose source is 'tracker_copy' are typed text, not audio, and silent videos ('[no speech]') carry no words: both are ignored."""
    r = conn.execute("""SELECT x.transcript FROM ad_transcript x
                        WHERE x.source <> 'tracker_copy' AND btrim(x.transcript) <> ''
                          AND (x.family_key = %s OR x.family_key IN (SELECT m.meta_family_key FROM meta_family_map m WHERE m.tracker_family_key = %s))
                        ORDER BY (x.family_key = %s) DESC, length(x.transcript) DESC LIMIT 1""", (fk, fk, fk)).fetchone()
    t = " ".join(r[0].split()) if r and r[0] else ""
    return t if t and not NO_SPEECH.match(t) else None


def has_words(unit: dict) -> bool:
    return any(unit.get(k) for k in ("primary_text", "headlines", "hook", "meta_body", "meta_title", "transcript", "ad_description", "description"))


# ---- claude call ----------------------------------------------------------------------------------------------------------------------

def claude_cmd(bin_: str = "claude") -> list[str]:
    """Headless, Read only, nothing persisted, no user / project settings or MCP servers."""
    return [bin_, "-p", "--model", MODEL, "--output-format", "json", "--tools", "Read", "--allowedTools", "Read", "--permission-mode", "dontAsk",
            "--no-session-persistence", "--setting-sources", "", "--strict-mcp-config"]


def claude_env() -> dict:
    return {k: v for k, v in os.environ.items() if k in ENV_KEEP}


def parse_answer(text: str, catalog_codes: set[str], tracker_pain: str | None = None, cat: list[dict] | None = None) -> dict:
    """The model's final message -> validated dict {line, confidence, runner_up, why, saw, kept_tracker_pain, override_reason}. Raises ValueError with a short reason.
    With `tracker_pain` + `cat` (the tracker pain is a usable default for the product): kept_tracker_pain is computed from the chosen line (the model's own flag is
    ignored when it disagrees) and a choice that leaves the tracker pain must carry an override_reason, otherwise the answer is rejected (the call is retried)."""
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
    kept, reason = None, None
    if tracker_pain and cat is not None and tracker_pain_lines(tracker_pain, cat):
        kept = line in tracker_pain_lines(tracker_pain, cat)
        reason = _clip(d.get("override_reason"), 300)
        if kept:
            reason = None
        elif not reason:
            raise ValueError("the line leaves the tracker pain point but override_reason is empty")
    return {"line": line, "confidence": conf, "runner_up": ru, "why": _clip(d.get("why"), 240), "saw": _clip(d.get("saw"), 240),
            "kept_tracker_pain": kept, "override_reason": reason}


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
    u = annotate_tracker_pain({k: v for k, v in unit.items() if not k.startswith("_")}, cat)
    tpain = (u.get("tracker_pain_point") if u.get("tracker_pain_lines") else None)
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
                return parse_answer(env.get("result"), codes, tpain, cat), usage
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

def method_for(is_video: bool, has_image: bool, audio: bool = False) -> str:
    """'+audio' = the tagger read the real audio transcript (ad_transcript); without it the transcript, if any, was typed / Meta text."""
    a = " + audio" if audio else ""
    if not has_image:
        return f"text only{a} (auto)"
    return f"cover + copy{a} (auto)" if is_video else f"image + copy{a} (auto)"


def write_tags(conn, slug: str, f: dict, ans: dict, unit: dict, has_image: bool, version: str, attempts: int, line_ids: dict, supersede: bool = False) -> int:
    """Insert the tag on every still-untagged row of the family. Never touches another version or an existing current tag.
    supersede (--retag-audio): the family's listed rows hold a current AUTO tag made without audio; it is set is_current=false (kept, never deleted)
    and the new tag records extra.previous_tag_id / previous_line_id."""
    is_video = bool(unit.get("_is_video"))
    conf = final_confidence(ans["confidence"], is_video, has_image)
    method = method_for(is_video, has_image, unit.get("_transcript_source") == "audio")
    lid = line_ids[ans["line"]]
    cat_row = lid["row"]
    extra_json = json.dumps({"source": "auto", "model": MODEL, "line_code": ans["line"], "needs_review": conf == "low", "model_confidence": ans["confidence"],
                      "attempts": attempts, "has_image": has_image, "is_video": is_video, "transcript_source": unit.get("_transcript_source"),
                      "kept_tracker_pain": ans.get("kept_tracker_pain"), "override_reason": ans.get("override_reason")})
    extra = db.jsonb(json.loads(extra_json))
    with conn.transaction():
        conn.execute("DELETE FROM ad_tags WHERE tag_set='linemap' AND version=%s AND tracker_row_id = ANY(%s)", (version, f["rows"]))
        n = 0
        for rid in f["rows"]:
            if supersede:
                prev = conn.execute("UPDATE ad_tags SET is_current=false WHERE tracker_row_id=%s AND tag_set='linemap' AND is_current AND extra->>'source'='auto' "
                                    "AND coalesce(method,'') NOT LIKE '%%audio%%' RETURNING id, line_id", (rid,)).fetchone()
                if prev is None:
                    continue
                extra = db.jsonb({**json.loads(extra_json), "previous_tag_id": prev[0], "previous_line_id": prev[1]})
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



# ---- native (no tracker row) families --------------------------------------------------------------------------------------------------

NATIVE_SQL = """
WITH ad_sp AS (SELECT d.ad_id, sum(d.spend) AS spend FROM ad_daily d WHERE d.ad_id NOT LIKE 'fam:%%' AND d.day >= %(since)s GROUP BY d.ad_id HAVING sum(d.spend) > 0),
fam AS (SELECT COALESCE(m.tracker_family_key, a.meta_family_key) AS family_key, sum(s.spend) AS spend, count(*) AS ads,
               (array_agg(a.ad_id ORDER BY s.spend DESC, a.ad_id))[1] AS top_ad_id, (array_agg(a.ad_name ORDER BY s.spend DESC, a.ad_id))[1] AS ad_name,
               (array_agg(a.meta_family_key ORDER BY s.spend DESC, a.ad_id))[1] AS meta_family_key
        FROM ad_sp s JOIN meta_ads a ON a.ad_id = s.ad_id LEFT JOIN meta_family_map m ON m.meta_family_key = a.meta_family_key
        WHERE COALESCE(m.tracker_family_key, a.meta_family_key) IS NOT NULL AND COALESCE(m.tracker_family_key, a.meta_family_key) <> ''
        GROUP BY 1)
SELECT f.family_key, f.spend, f.ads, f.top_ad_id, f.ad_name, um.slug,
       EXISTS (SELECT 1 FROM creative_assets ca WHERE ca.family_key = f.family_key) AS has_assets,
       (SELECT g.id FROM ad_family_line_tags g WHERE g.family_key = f.family_key AND g.is_current) AS cur_id,
       (SELECT (g.extra->>'source' = 'auto' AND coalesce(g.method, '') NOT LIKE '%%audio%%') FROM ad_family_line_tags g WHERE g.family_key = f.family_key AND g.is_current) AS cur_auto_no_audio,
       (SELECT g.version FROM ad_family_line_tags g WHERE g.family_key = f.family_key AND g.is_current) AS cur_version
FROM fam f
JOIN LATERAL (SELECT pr.slug FROM unmatched_meta u JOIN products pr ON pr.id = u.product_id WHERE u.meta_family_key = f.meta_family_key LIMIT 1) um ON true
WHERE f.spend >= %(min_spend)s AND um.slug = ANY(%(slugs)s)
  AND NOT EXISTS (SELECT 1 FROM tracker_rows t WHERE t.family_key = f.family_key AND t.deleted_at IS NULL)
ORDER BY f.spend DESC, f.family_key
"""


def native_candidates(conn, slugs, day: dt.date, retag_today: bool = False, retag_audio: bool = False, min_spend: float = NATIVE_MIN_SPEND, since: str = NATIVE_SINCE) -> dict[str, list[dict]]:
    """{slug: [family dicts]} biggest spend first: Meta families with spend since `since` that are not on the tracker and have no current tag in ad_family_line_tags.
    retag_today: also the family whose current tag is today's own auto version; retag_audio: also families whose current tag is an auto tag made without audio and that now have a real-audio transcript."""
    ver = "auto-" + day.isoformat()
    aud = audio_native_families(conn) if retag_audio else set()
    out: dict[str, list[dict]] = {s: [] for s in slugs}
    for fk, spend, ads, top, name, slug, has_assets, cur_id, cur_noaud, cur_ver in conn.execute(NATIVE_SQL, {"since": since, "min_spend": min_spend, "slugs": list(slugs)}).fetchall():
        if cur_id is None or (retag_today and cur_ver == ver) or (retag_audio and cur_noaud and fk in aud):
            out[slug].append({"native": True, "slug": slug, "family_key": fk, "spend": float(spend), "ads": ads, "top_ad_id": top, "ad_name": name, "has_assets": bool(has_assets),
                              "supersede": bool(cur_id is not None and retag_audio and cur_noaud and fk in aud)})
    return out


def audio_native_families(conn) -> set[str]:
    q = """SELECT x.family_key FROM ad_transcript x WHERE x.source <> 'tracker_copy' AND btrim(x.transcript) <> '' AND lower(btrim(x.transcript, ' []')) <> 'no speech'"""
    return {r[0] for r in conn.execute(q).fetchall()}


def gather_native(conn, f: dict) -> tuple[dict, tuple | None]:
    """-> (unit for the prompt, (bytes, mime) or None). No tracker copy: the words are what ran on Meta (family_content) and the real-audio transcript (audio first, then family_content)."""
    fk = f["family_key"]
    fc = conn.execute("SELECT meta_body, meta_title, meta_desc, transcript, is_video FROM family_content WHERE family_key=%s", (fk,)).fetchone()
    img = conn.execute("SELECT bytes, mime FROM creative_assets WHERE family_key=%s AND kind='thumb' ORDER BY idx LIMIT 1", (fk,)).fetchone()
    frames = conn.execute("SELECT 1 FROM creative_assets WHERE family_key=%s AND kind <> 'thumb' LIMIT 1", (fk,)).fetchone()
    has_row = conn.execute("SELECT 1 FROM ad_transcript WHERE family_key=%s AND source <> 'tracker_copy' LIMIT 1", (fk,)).fetchone()
    is_video = bool(frames or has_row or (fc and fc[4]))
    aud = audio_transcript(conn, fk)
    trans, tsrc = (aud, "audio") if aud else ((fc[3], "family_content") if fc and fc[3] else (None, None))
    unit = {"name": _clip(f.get("ad_name"), 200), "type": "video" if is_video else "static image", "meta_body": _clip(fc[0], 800) if fc else None, "meta_title": _clip(fc[1], 200) if fc else None,
            "ad_description": _clip(fc[2], 300) if fc else None, "transcript": _clip(trans, TRANSCRIPT_CAP)}
    unit = {k: v for k, v in unit.items() if v not in (None, "", [], False)}
    unit["_is_video"] = is_video
    unit["_transcript_source"] = tsrc if unit.get("transcript") else None
    return unit, ((bytes(img[0]), img[1]) if img else None)


def write_native_tag(conn, slug: str, f: dict, ans: dict, unit: dict, has_image: bool, version: str, attempts: int, ids: dict) -> int:
    """Insert the tag for the family in ad_family_line_tags. Never overwrites a current tag; with f['supersede'] the current auto tag made without audio is set is_current=false first
    (kept, never deleted) and the new row records extra.previous_tag_id / previous_line_id."""
    is_video = bool(unit.get("_is_video"))
    conf = final_confidence(ans["confidence"], is_video, has_image)
    method = method_for(is_video, has_image, unit.get("_transcript_source") == "audio")
    lid = ids[ans["line"]]
    cat_row = lid["row"]
    base = {"source": "auto", "model": MODEL, "line_code": ans["line"], "needs_review": conf == "low", "model_confidence": ans["confidence"], "attempts": attempts, "has_image": has_image,
            "is_video": is_video, "transcript_source": unit.get("_transcript_source"), "kept_tracker_pain": None, "override_reason": None, "native": True}
    with conn.transaction():
        conn.execute("DELETE FROM ad_family_line_tags WHERE version=%s AND family_key=%s", (version, f["family_key"]))
        extra = base
        if f.get("supersede"):
            prev = conn.execute("UPDATE ad_family_line_tags SET is_current=false WHERE family_key=%s AND is_current AND extra->>'source'='auto' AND coalesce(method,'') NOT LIKE '%%audio%%' RETURNING id, line_id",
                                (f["family_key"],)).fetchone()
            if prev is None:
                return 0
            extra = {**base, "previous_tag_id": prev[0], "previous_line_id": prev[1]}
        return conn.execute("""INSERT INTO ad_family_line_tags (family_key, product_slug, ad_name, tag_set, version, is_current, line_id, cluster, icp, pain_point, emotional_trigger, confidence, method,
                               seen, why, runner_up, tagger, extra)
                               SELECT %s,%s,%s,'linemap',%s,true,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                               WHERE NOT EXISTS (SELECT 1 FROM ad_family_line_tags WHERE family_key=%s AND is_current)""",
                            (f["family_key"], slug, f.get("ad_name"), version, lid["id"], cat_row[0], cat_row[1], cat_row[2], cat_row[3], conf, method, ans.get("saw"), ans.get("why"),
                             ans.get("runner_up"), TAGGER, db.jsonb(extra), f["family_key"])).rowcount


TICKET_SQL = """
SELECT a.id, p.slug, l.id, l.line_code, l.cluster, l.icp, l.pain_point, l.emotional_trigger,
       ARRAY(SELECT DISTINCT x FROM (SELECT jsonb_array_elements_text(CASE WHEN jsonb_typeof(a.launched_ad_ids) = 'array' THEN a.launched_ad_ids ELSE '[]'::jsonb END) AS x
                                     UNION ALL SELECT v->>'ad_id' FROM jsonb_array_elements(CASE WHEN jsonb_typeof(a.variations) = 'array' THEN a.variations ELSE '[]'::jsonb END) v) q
             WHERE x ~ '^[0-9]+$') AS ad_ids
FROM creative_priorities_assignments a JOIN taxonomy_lines l ON l.id = a.line_id JOIN products p ON p.id = l.product_id
WHERE a.dropped_at IS NULL AND a.manual_id IS NULL AND p.slug = ANY(%s)
ORDER BY a.id
"""

FAMILY_OF_AD_SQL = """
SELECT COALESCE(m.tracker_family_key, a.meta_family_key), a.ad_name FROM meta_ads a LEFT JOIN meta_family_map m ON m.meta_family_key = a.meta_family_key
WHERE a.ad_id = %s AND COALESCE(m.tracker_family_key, a.meta_family_key) <> ''
"""


def tag_from_tickets(conn, slugs, day: dt.date, dry_run: bool = False) -> dict:
    """Production tickets with a taxonomy line and Meta ad ids: tag each ad's family (no tracker row, no current native tag) with the ticket's own line. No model call.
    Never overwrites or retires an existing tag; a family with a live tracker row is left to the tracker path."""
    version = "ticket-" + day.isoformat()
    st = {"tickets": 0, "tickets_with_ads": 0, "families_tagged": 0, "skipped_tracker": 0, "skipped_tagged": 0, "unknown_ad": 0}
    for aid, slug, lid, code, cl, icp, pain, trig, ad_ids in conn.execute(TICKET_SQL, (list(slugs),)).fetchall():
        st["tickets"] += 1
        if ad_ids:
            st["tickets_with_ads"] += 1
        for ad_id in ad_ids or []:
            r = conn.execute(FAMILY_OF_AD_SQL, (ad_id,)).fetchone()
            if r is None:
                st["unknown_ad"] += 1
                continue
            fk, name = r
            if conn.execute("SELECT 1 FROM tracker_rows WHERE family_key=%s AND deleted_at IS NULL LIMIT 1", (fk,)).fetchone():
                st["skipped_tracker"] += 1
                continue
            if conn.execute("SELECT 1 FROM ad_family_line_tags WHERE family_key=%s AND is_current", (fk,)).fetchone():
                st["skipped_tagged"] += 1
                continue
            if dry_run:
                st["families_tagged"] += 1
                continue
            n = conn.execute("""INSERT INTO ad_family_line_tags (family_key, product_slug, ad_name, tag_set, version, is_current, line_id, cluster, icp, pain_point, emotional_trigger, confidence, method,
                                seen, why, tagger, extra)
                                SELECT %s,%s,%s,'linemap',%s,true,%s,%s,%s,%s,%s,'high','ticket (production assignment)',NULL,%s,'assignment',%s
                                WHERE NOT EXISTS (SELECT 1 FROM ad_family_line_tags WHERE family_key=%s AND is_current)""",
                              (fk, slug, name, version, lid, cl, icp, pain, trig, f"Ticket {aid} was made for this line.",
                               db.jsonb({"source": "assignment", "assignment_id": aid, "line_code": code, "ad_id": ad_id, "native": True}), fk)).rowcount
            st["families_tagged"] += n
    if not dry_run:
        conn.commit()
    return st


def native_stage(conn, slugs, cands: dict, budget: int, stats: dict, fetch_assets: bool, version: str, fails: dict, runner, sleep, log) -> int:
    """Tag the native candidates in `cands` (spend order per product) while budget lasts. Returns the budget left. Raises StopRun like the tracker loop."""
    ns = stats["native"]
    if fetch_assets:
        need = [f for s in slugs for f in cands[s][:max(budget, 0)] if not f["has_assets"]]
        if need:
            try:
                from . import own_ad_tags
                ns["assets"] = own_ad_tags.fetch_missing_thumbs(conn, need, log=log)
            except Exception as e:                                                  # noqa: BLE001
                ns["assets_error"] = db.scrub(e)[:200]
    for s in slugs:
        cat = load_catalog(s)
        ids = taxonomy_ids(conn, s)
        for f in cands[s]:
            if budget <= 0:
                ns["capped"] = True
                return budget
            if int(fails.get(f"{s}:{f['family_key']}") or 0) >= MAX_FAILS:
                ns["skipped_repeat_failures"] += 1
                continue
            unit, img = gather_native(conn, f)
            if img is None and not has_words(unit):
                ns["no_content"] += 1
                continue
            budget -= 1
            try:
                ans, usage = tag_one(s, cat, unit, img, runner, sleep)
            except CallFailed as e:
                ns["failed"] += 1
                db.set_meta(conn, f"autotag_fail:{s}:{f['family_key']}", str(int(fails.get(f"{s}:{f['family_key']}") or 0) + 1))
                conn.commit()
                log(f"  native {s} {f['family_key']}: failed ({db.scrub(e)[:120]})")
                continue
            for k in ("calls", "attempts", "input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens"):
                stats[k] += usage[k]
            if ans["line"] not in ids:
                ns["failed"] += 1
                log(f"  native {s} {f['family_key']}: {ans['line']} is not in taxonomy_lines, skipped")
                continue
            n = write_native_tag(conn, s, f, ans, unit, img is not None, version, usage["attempts"], ids)
            conn.commit()
            if n:
                ns["tagged"] += 1
                ns["by_product"][s] = ns["by_product"].get(s, 0) + 1
                m = method_for(bool(unit.get("_is_video")), img is not None, unit.get("_transcript_source") == "audio")
                ns["by_method"][m] = ns["by_method"].get(m, 0) + 1
                c = final_confidence(ans["confidence"], bool(unit.get("_is_video")), img is not None)
                ns["confidence"][c] = ns["confidence"].get(c, 0) + 1
    return budget


# ---- run ------------------------------------------------------------------------------------------------------------------------------

def run(conn, slugs=PRODUCTS, limit: int = DEFAULT_LIMIT, dry_run: bool = False, retag_today: bool = False, fetch_assets: bool = True,
        retag_audio: bool = False, day: dt.date | None = None, runner=subprocess.run, sleep=time.sleep, log=db.log, native: bool = True, tracker: bool = True,
        native_min_spend: float = NATIVE_MIN_SPEND, native_since: str = NATIVE_SINCE) -> dict:
    day = day or dt.date.today()
    version = ("auto-audio-" if retag_audio else "auto-") + day.isoformat()
    t0 = time.time()
    stats: dict = {"version": version, "dry_run": dry_run, "limit": limit, "by_product": {}, "tagged_families": 0, "tagged_rows": 0, "no_content": 0,
                   "failed": 0, "skipped_repeat_failures": 0, "confidence": {}, "calls": 0, "attempts": 0, "input_tokens": 0, "output_tokens": 0,
                   "cache_read_tokens": 0, "cache_creation_tokens": 0, "stopped_early": None, "samples": []}
    if native:
        stats["native"] = {"candidates": {}, "candidate_total": 0, "tagged": 0, "by_product": {}, "by_method": {}, "confidence": {}, "no_content": 0, "failed": 0, "skipped_repeat_failures": 0,
                           "min_spend": native_min_spend, "since": native_since}
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
    cands = candidates(conn, slugs, day, retag_today, retag_audio) if tracker else {s: [] for s in slugs}
    ncands = native_candidates(conn, slugs, day, retag_today, retag_audio, native_min_spend, native_since) if native else {s: [] for s in slugs}
    if native:
        stats["native"]["candidates"] = {s: len(ncands[s]) for s in slugs}
        stats["native"]["candidate_total"] = sum(len(v) for v in ncands.values())
        stats["native"]["candidate_spend"] = round(sum(f["spend"] for v in ncands.values() for f in v))
    if retag_audio:                                    # bounded: the highest-spend families first, at most `limit` (the rest wait for a later run)
        stats["retag_audio_eligible"] = {s: {"families": len(c), "rows": sum(len(f["rows"]) for f in c)} for s, c in cands.items()}
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
            for f in ncands[s][:2]:
                unit, img = gather_native(conn, f)
                stats["samples"].append({"product": s, "native": True, "family": f["family_key"], "spend": round(f["spend"]), "has_image": bool(img),
                                         "unit": {k: v for k, v in unit.items() if not k.startswith("_")}})
        if native:
            stats["native"]["tickets"] = tag_from_tickets(conn, slugs, day, dry_run=True)
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
                n = write_tags(conn, s, f, ans, unit, img is not None, version, usage["attempts"], ids, retag_audio)
                conn.commit()
                stats["tagged_families"] += 1
                stats["tagged_rows"] += n
                stats["by_product"][s]["tagged"] += 1
                c = final_confidence(ans["confidence"], bool(unit.get("_is_video")), img is not None)
                stats["confidence"][c] = stats["confidence"].get(c, 0) + 1
            else:
                continue
            break
        if native and not stats["stopped_early"]:
            stats["native"]["tickets"] = tag_from_tickets(conn, slugs, day)         # the ticket's own line first, no model call: those families are then not candidates
            ncands = native_candidates(conn, slugs, day, retag_today, retag_audio, native_min_spend, native_since)
            stats["native"]["candidates_after_tickets"] = sum(len(v) for v in ncands.values())
            native_stage(conn, slugs, ncands, budget, stats, fetch_assets, version, fails, runner, sleep, log)
            stats["tagged_rows"] += stats["native"]["tagged"]
    except StopRun as e:
        stats["stopped_early"] = db.scrub(e)[:300]
    stats["duration_s"] = round(time.time() - t0, 1)
    stats["avg_s_per_ad"] = round(stats["duration_s"] / max(1, stats["tagged_families"] + stats["failed"]), 1)
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--product", choices=PRODUCTS)
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="hard cap on ads (claude calls' units) per run (default 400)")
    ap.add_argument("--dry-run", action="store_true", help="count candidates and show sample units; no claude call, no writes")
    ap.add_argument("--retag-today", action="store_true", help="also redo the rows tagged by today's own auto version (never other versions)")
    ap.add_argument("--retag-audio", action="store_true", help="redo auto tags made without the real-audio transcript for families that now have one (spend order, capped by --limit); old tags are kept as superseded")
    ap.add_argument("--no-assets", action="store_true", help="do not try to fetch missing thumbnails from Meta first")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--native-only", action="store_true", help="only Meta families with no tracker row (ad_family_line_tags); skip the tracker path")
    g.add_argument("--no-native", action="store_true", help="only the tracker path (as before migration 285)")
    ap.add_argument("--min-spend", type=float, default=NATIVE_MIN_SPEND, help=f"native families: minimum lifetime Meta spend since --since (default {NATIVE_MIN_SPEND:.0f})")
    ap.add_argument("--since", default=NATIVE_SINCE, help=f"native families: spend counted from this day (default {NATIVE_SINCE})")
    a = ap.parse_args()
    slugs = (a.product,) if a.product else PRODUCTS
    with db.JobRun("auto_tag") as job, db.connect() as conn:
        job.stats = {}
        st = run(conn, slugs, a.limit, a.dry_run, a.retag_today, not a.no_assets, a.retag_audio, native=not a.no_native, tracker=not a.native_only,
                 native_min_spend=a.min_spend, native_since=a.since)
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
