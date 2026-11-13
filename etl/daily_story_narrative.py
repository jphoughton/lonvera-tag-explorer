"""The daily story text: a deterministic template built from the facts, and an optional model-written version that is only accepted when every number in it can be
found in the facts. The model never touches the database: it receives a condensed copy of the facts JSON and nothing else.

Backends for the model step, in order: ANTHROPIC_API_KEY (Messages API, model claude-sonnet-5-5), then the headless `claude -p` CLI on CLAUDE_CODE_OAUTH_TOKEN (the same
route etl.auto_tag uses), else the template. DAILY_STORY_LLM=0 turns the model off; api | cli forces one backend. A rejected or failed model answer falls back to the template.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess

MODEL = "claude-sonnet-5-5"
MAX_WORDS = 170
SYSTEM_PROMPT = f"""You write ONE very concise explanation of why yesterday's ad performance was what it was, for the owner of a direct-to-consumer supplement brand. You are given a JSON
object of facts computed from the database. Use ONLY those facts: never invent a number, a cause, a change or a metric that is not in the JSON, and never use outside knowledge.

Rules (non-negotiable):
- Output JSON only: {{"headline": "<one sentence>", "story": "<the story>", "confidence": {{"<claim>": "<label>"}}}}. The story is at most {MAX_WORDS} words in total counting every word of every line: aim for 120 to 150 and count before you answer.
- Length budget (hard): the TLDR line at most 25 words; at most 5 further lines of at most 22 words each; the final "Cannot see:" line at most 15 words. Choose the 5 most informative lines; leave the rest out.
- The story starts with a line "TLDR: ..." (one sentence, the answer), then 3 to 6 short lines, then a final line "Cannot see: ..." naming what the database cannot show (use the facts' cannot_see list).
- The headline return is New-customer return on ad spend (NC ROAS) = actual new-customer revenue / (Meta + Google spend). The ad-attributed ROAS in the cuts is only a way to compare ads, products and structures, never the day's headline. Meta-reported ROAS is a side data point.
- Name the basis of every figure. Spell out every acronym on first use (new customer (NC), return on ad spend (ROAS), cost per 1,000 impressions (CPM), click-through rate (CTR), cost per link click (CPLC), average order value (AOV), ideal customer profile (ICP), target ROAS (tROAS)).
- Compare with the same-weekday baseline in the facts. Say whether the move is inside normal noise (|z| under 2) or outside it. Use "consistent with", never "caused" or "because", unless the facts contain a clean like-for-like control (they do not).
- Explain the move with the decomposition terms (exact arithmetic), then the mix-versus-rate split (spend moving between segments versus per-dollar change), then the changes in the ledger that line up in time, then email, Google, Amazon and creative only when they moved. For a change, name the test that would settle it.
- Include the key funnel numbers that moved (CPM, CTR, CPLC, conversion rate, AOV, cost per new customer).
- Never describe audience quality, intent, fatigue, creative quality, saturation or any mechanism the facts do not state, and never conclude that something is ruled out or "does not explain" a move because a number went the other way. Name only changes that are in the ledger or the cuts. Never write caused, because, due to, drove, driven by, led to or explains.
- Plain prose and short lines. No em dashes. No compliance or policy commentary. No advice.
- Every number must appear in the facts (rounding is fine). Do not compute new numbers."""


# ------------------------------------------------------------------------------------------------------------------------------------ number checking

NUM_RE = re.compile(r"(?<![\w.])[-+]?\$?\d[\d,]*(?:\.\d+)?%?")
SMALL_OK = set(range(0, 32)) | {28, 90, 100, 170, 1000, 2026}


def _leaves(o, out):
    if isinstance(o, bool) or o is None:
        return
    if isinstance(o, (int, float)):
        out.append(float(o))
    elif isinstance(o, str):
        for m in re.finditer(r"\d[\d,]*(?:\.\d+)?", o):
            try:
                out.append(float(m.group(0).replace(",", "")))
            except ValueError:
                pass
    elif isinstance(o, dict):
        for v in o.values():
            _leaves(v, out)
    elif isinstance(o, (list, tuple)):
        for v in o:
            _leaves(v, out)


def allowed_numbers(facts: dict) -> list[float]:
    out: list[float] = []
    _leaves(facts, out)
    return out


def _variants(v: float) -> set[float]:
    s = set()
    for scale in (1.0, 100.0, 0.01, 1000.0, 0.001):
        x = abs(v) * scale
        for dec in (0, 1, 2, 3, 4):
            s.add(round(x, dec))
    return s


def numbers_in(text: str) -> list[tuple[str, float, int]]:
    """(raw, value, decimals) of every number in the text (dollar signs, commas and percent signs stripped)."""
    res = []
    for m in NUM_RE.finditer(text):
        raw = m.group(0)
        t = raw.replace("$", "").replace(",", "").replace("%", "").lstrip("+-")
        if not t:
            continue
        dec = len(t.split(".")[1]) if "." in t else 0
        res.append((raw, float(t), dec))
    return res


def validate(text: str, facts: dict) -> tuple[bool, list[str]]:
    """Every number in the text must be a number in the facts (up to rounding, as a percentage, or per thousand), a small counting number, or a year.
    Returns (ok, offending raw numbers)."""
    pool = build_pool(facts)
    bad = []
    for raw, v, dec in numbers_in(text):
        if dec == 0 and int(v) in SMALL_OK:
            continue
        if round(v, dec) in pool.get(dec, ()):
            continue
        bad.append(raw)
    return (not bad), bad


def build_pool(facts: dict) -> dict[int, set]:
    pool: dict[int, set] = {d: set() for d in range(0, 5)}
    for v in allowed_numbers(facts):
        for x in _variants(v):
            for dec in range(0, 5):
                pool[dec].add(round(x, dec))
    return pool


CAUSAL = re.compile(r"\b(caused|causes|because|due to|drove|driven by|led to|resulted from|thanks to|explain(s|ed)?|low-intent|intent|fatigue|saturat\w*)\b", re.I)


def word_count(text: str) -> int:
    return len(re.findall(r"\S+", text))


# ------------------------------------------------------------------------------------------------------------------------------------ formatting

def money(v, d=0):
    return "n/a" if v is None else f"${v:,.{d}f}"


def pct(v, d=0, sign=False):
    return "n/a" if v is None else f"{v * 100:{'+' if sign else ''}.{d}f}%"


def num(v, d=2):
    return "n/a" if v is None else f"{v:,.{d}f}"


def fmt(unit, v):
    if v is None:
        return "n/a"
    return {"usd": money(v, 2 if abs(v) < 100 else 0), "pct": pct(v, 1), "x": num(v, 2), "count": f"{v:,.0f}", "ratio": num(v, 3)}.get(unit, num(v, 2))


# ------------------------------------------------------------------------------------------------------------------------------------ condensed facts for the model

def condense(f: dict) -> dict:
    """The facts the model sees: the headline, the drivers, the biggest segment moves, the ledger, per-source lines. Every number here is a number in the full facts."""
    M = f.get("metrics", {})
    keep = ("nc_roas", "aov", "cpnc", "spend_total", "spend_meta", "spend_google", "nc", "nc_rev", "cpm", "ctr", "cplc", "conv", "c2lpv", "lpv2atc", "atc2ic", "atc2nc", "meta_roas", "ad_roas",
            "capped_share", "troas_scaling_share", "d7_scaling_share", "testing_share", "new_ad_share", "families_live", "families_launched", "new_adsets", "pack_1", "pack_2", "pack_3", "pack_4p", "discount_share")
    met = {}
    for k in keep:
        m = M.get(k)
        if m and m["value"] is not None:
            met[k] = {"label": m["label"], "unit": m["unit"], "value": m["value"], "baseline_median_same_weekday": m["baseline_median"], "trailing_7d": m["trailing_7d"], "z": m["z"], "flag": m["flag"]}
    dec = f.get("decomposition")
    out = {"day": f["day"], "weekday": f["weekday"], "headline_definition": "NC ROAS = actual new-customer revenue / (Meta + Google spend)", "metrics": met,
           "decomposition": None if not dec else {"day": dec["day"], "baseline_pooled_same_weekday": dec["base"],
                                                 "pct_change_total": dec["pct_change"] * 100, "terms": [{"label": t["label"], "day": t["day"], "base": t["base"], "effect_percentage_points": t["pct_points"] * 100} for t in dec["terms"]]},
           "control": f.get("control"), "anomalies": f.get("anomalies", [])[:6], "cannot_see": f.get("cannot_see", [])}
    mr = {}
    for cut, t in (f.get("cuts") or {}).items():
        if not t or not t.get("mix_rate"):
            continue
        rows = sorted([r for r in t["rows"] if r.get("effect")], key=lambda r: -abs(r["effect"]["total"]))[:3]
        mr[cut] = {"label": t["label"], "ad_attributed_roas_day": t["mix_rate"]["day"], "base": t["mix_rate"]["base"], "mix_effect": t["mix_rate"]["mix"], "rate_effect": t["mix_rate"]["rate"],
                   "biggest": [{"segment": r["key"], "share": r["share"], "base_share": r["base_share"], "roas": r["day"]["attr_roas"] if r["day"] else None, "base_roas": r["base"]["attr_roas"],
                                "mix": r["effect"]["mix"], "rate": r["effect"]["rate"]} for r in rows]}
    out["mix_vs_rate"] = {k: mr[k] for k in ("product", "structure", "cluster", "icp", "format") if k in mr}
    mv = (f.get("movers") or {}).get("families")
    if mv:
        out["top_ad_movers"] = [{"name": r["name"], "cluster": r["cluster"], "icp": r["icp"], "format": r["format"], "age_days": r["age_days"], "spend": r["spend"], "base_spend_per_day": r["base_spend_per_day"],
                                 "roas": r["attr_roas"], "base_roas": r["base_attr_roas"], "effect_points": r["total"]} for r in mv["rows"][:4]]
    out["changes"] = [{"first_day": c["first_day"], "what": c["label"], "detail": c["detail"], "share_of_spend": c["share_of_D_meta_spend"]} for c in (f.get("changes") or [])[-12:]]
    sec = f.get("sections", {})
    out["email_sms"] = sec.get("klaviyo")
    out["google"] = {k: sec.get("google", {}).get(k) for k in ("spend", "base_spend", "clicks", "cpc", "base_cpc", "upstack_nc_roas", "share_of_total_spend")} if sec.get("google", {}).get("available") else sec.get("google")
    out["amazon"] = sec.get("amazon")
    return _round(out)


def _round(o):
    """Floats to 4 decimals (1 above 1,000): fewer tokens, and every rounded value is still a rounding of a number in the facts."""
    if isinstance(o, float):
        return round(o, 1) if abs(o) >= 1000 else round(o, 4)
    if isinstance(o, dict):
        return {k: _round(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_round(v) for v in o]
    return o


# ------------------------------------------------------------------------------------------------------------------------------------ template

def _driver_phrase(t):
    """'lower CPM +18.7 points': the direction of the underlying metric, then what it did to NC ROAS (percentage points of the total move)."""
    d = t["pct_points"] * 100
    up = t["day"] > t["base"] if t["name"] != "spend_mix" else t["day"] > t["base"]
    word = {"aov": "average order value (AOV)", "spend_mix": "Meta share of total spend", "cpm": "cost per 1,000 impressions (CPM)", "ctr": "link click-through rate (CTR)",
            "c2lpv": "link click to landing page view rate", "lpv2atc": "landing page view to add to cart rate", "atc2nc": "add to cart to new customer rate",
            "atc2ic": "add to cart to initiated checkout rate", "ic2nc": "initiated checkout to new customer rate"}[t["name"]]
    return f"{'higher' if up else 'lower'} {word} {d:+.1f} points"


def _short_cannot(items: list[str], n: int = 3) -> list[str]:
    out = []
    for t in items[:n]:
        t = t.split(" (")[0].strip()
        out.append(t[:70])
    return out


def _pick_changes(changes: list[dict], n: int = 3) -> list[dict]:
    """The changes worth naming: those touching spend first (largest share), then the others by size; one per type, newest first."""
    seen, out = set(), []
    for c in sorted(changes, key=lambda c: (-(c["share_of_D_meta_spend"] or 0), c["days_before_D"])):
        if c["type"] in seen or (c["share_of_D_meta_spend"] is not None and c["share_of_D_meta_spend"] < 0.05):
            continue
        seen.add(c["type"])
        out.append(c)
    return out[:n]


def template(f: dict) -> dict:
    """Deterministic story from the facts. Same structure the model is asked for, <= 170 words."""
    if f.get("readiness", {}).get("ready") is False:
        miss = ", ".join(f["readiness"]["missing_core"])
        return {"headline": f"Data not ready for {f['day']}: {miss}.", "story": f"TLDR: data not ready for {f['day']}; waiting for {miss}.\nCannot see: anything about that day until those sources load.",
                "confidence": {"data": "not ready"}, "cannot_see": [f"{miss} not loaded for {f['day']}"]}
    M = f["metrics"]
    h = M["nc_roas"]
    noise = "outside normal noise" if h["flag"] else "inside normal noise" if h["z"] is not None else "noise range not available yet"
    tl = (f"TLDR: new-customer return on ad spend (NC ROAS) was {num(h['value'], 2)} vs {num(h['baseline_median'], 2)} for the same weekday over the previous 4 weeks "
          f"({pct(h['pct_vs_baseline'], 0, True)}), {noise}.")
    lines = [tl]
    dec = f.get("decomposition")
    if dec:
        top = sorted(dec["terms"], key=lambda t: -abs(t["pct_points"]))[:3]
        lines.append("Drivers (exact arithmetic): " + "; ".join(_driver_phrase(t) for t in top) + ".")
    t = (f.get("cuts") or {}).get("structure")
    if t and t.get("mix_rate"):
        mr = t["mix_rate"]
        lines.append(f"By campaign structure, ad-attributed ROAS moved {mr['change']:+.2f}: spend mix {mr['mix']:+.2f}, per-dollar rate {mr['rate']:+.2f}.")
    mv = (f.get("movers") or {}).get("families")
    if mv and mv["rows"]:
        r = mv["rows"][0]
        lines.append(f"Top ad mover: {r['name'][:40]} ({r['cluster']}), {pct(r['share'], 0)} of spend vs {pct(r['base_share'], 0)}.")
    ch = _pick_changes(f.get("changes") or [])
    if ch:
        lines.append("Changes in the last 7 days, consistent with but untested: " + "; ".join(f"{c['detail'][:90]} ({c['first_day']})" if c["type"].endswith(("share_up", "share_down")) else f"{c['label'].lower()}{': ' + c['subject'][:40] if c.get('subject') else ''} ({c['first_day']})" for c in ch) + ".")
    sec = f.get("sections", {})
    g = sec.get("google", {})
    if g.get("available") and g.get("base_spend") and abs(g["spend"] / g["base_spend"] - 1) >= 0.25:
        lines.append(f"Google spend {money(g['spend'])} vs {money(g['base_spend'])}.")
    kv = sec.get("klaviyo", {})
    if kv.get("available") and kv.get("total_base", {}).get("sends") and abs(kv["total"]["sends"] / max(kv["total_base"]["sends"], 1) - 1) >= 0.5:
        lines.append(f"Email and text sends {kv['total']['sends']:,.0f} vs {kv['total_base']['sends']:,.0f}.")
    am = sec.get("amazon", {})
    if am.get("available") and am.get("base_ad_spend") and abs(am["ad_spend"] / am["base_ad_spend"] - 1) >= 0.25:
        lines.append(f"Amazon ad spend {money(am['ad_spend'])} vs {money(am['base_ad_spend'])} (not in NC ROAS).")
    lines.append("Cannot see: " + "; ".join(_short_cannot(f.get("cannot_see", []))) + ".")
    story = "\n".join(lines)
    while word_count(story) > MAX_WORDS and len(lines) > 3:
        del lines[-2]
        story = "\n".join(lines)
    return {"headline": tl.replace("TLDR: ", "", 1), "story": story,
            "confidence": {"move": noise, "drivers": "exact arithmetic on the day's numbers; shows which terms moved, not why", "mix_vs_rate": "exact arithmetic on ad-attributed ROAS, a cut only",
                           "changes": "consistent with; ordering in time only, no like-for-like control", "cause": "not established: no randomized holdout or clean control"},
            "cannot_see": f.get("cannot_see", [])}


# ------------------------------------------------------------------------------------------------------------------------------------ model backends

def _call_api(system: str, user: str) -> str:
    import requests
    key = os.environ["ANTHROPIC_API_KEY"]
    base = os.environ.get("DAILY_STORY_API_BASE", "https://api.anthropic.com")
    r = requests.post(base + "/v1/messages", headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                      json={"model": MODEL, "max_tokens": 1500, "system": system, "messages": [{"role": "user", "content": user}]}, timeout=120)
    r.raise_for_status()
    return "".join(b.get("text", "") for b in r.json().get("content", []))


_CLI_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "SSL_CERT_FILE", "NODE_EXTRA_CA_CERTS", "CLAUDE_CODE_OAUTH_TOKEN", "USER")


def _call_cli(system: str, user: str, runner=subprocess.run) -> str:
    exe = shutil.which("claude")
    if not exe:
        raise RuntimeError("claude CLI not found")
    env = {k: os.environ[k] for k in _CLI_ENV if k in os.environ}
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = runner([exe, "-p", "--model", MODEL, "--output-format", "json", "--tools", "", "--permission-mode", "dontAsk", "--system-prompt", system], input=user, capture_output=True,
                   text=True, timeout=240, cwd=td, env=env)
    if p.returncode != 0:
        raise RuntimeError("claude CLI failed: " + (p.stderr or p.stdout)[:200])
    env_ = json.loads(p.stdout)
    if env_.get("is_error"):
        raise RuntimeError("claude CLI error: " + str(env_.get("result"))[:200])
    return env_.get("result", "")


def backend() -> str | None:
    mode = os.environ.get("DAILY_STORY_LLM", "auto").lower()
    if mode in ("0", "off", "false", "no"):
        return None
    if mode in ("api", "auto") and os.environ.get("ANTHROPIC_API_KEY"):
        return "api"
    if mode in ("cli", "auto") and shutil.which("claude") and (os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or mode == "cli"):
        return "cli"
    return None


def _parse(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no JSON object in the model answer")
    o = json.loads(m.group(0))
    if not isinstance(o.get("story"), str) or not isinstance(o.get("headline"), str):
        raise ValueError("story / headline missing")
    return o


def generate(facts: dict, caller=None) -> dict:
    """The story for these facts: the model's when it is available and passes validation, else the template. Returns {headline, story, confidence, cannot_see, model, source, note}."""
    tpl = template(facts)
    be = backend() if caller is None else "injected"
    if facts.get("readiness", {}).get("ready") is False or be is None:
        return {**tpl, "model": None, "source": "template", "note": "model step off (set ANTHROPIC_API_KEY on the etl service, or CLAUDE_CODE_OAUTH_TOKEN with the claude CLI)" if be is None else "data not ready"}
    user = json.dumps(condense(facts), default=str, separators=(",", ":"))
    fn = caller or (_call_api if be == "api" else _call_cli)
    err = ""
    for attempt in range(3):
        try:
            prompt = user if not err else user + f"\n\nYour previous answer was rejected: {err}. Fix it and answer with the JSON only."
            o = _parse(fn(SYSTEM_PROMPT, prompt))
            if word_count(o["story"]) > MAX_WORDS:
                err = f"the story had {word_count(o['story'])} words, the limit is {MAX_WORDS}: cut at least {word_count(o['story']) - 140} words (drop the least important line) and stay under 150"
                continue
            cm = CAUSAL.search(o["headline"] + "\n" + o["story"])
            if cm:
                err = f"the word {cm.group(0)!r} claims a cause or a mechanism the facts do not support; use 'consistent with'"
                continue
            ok, bad = validate(o["headline"] + "\n" + o["story"], facts)
            if not ok:
                err = "these numbers are not in the facts: " + ", ".join(bad[:8])
                continue
            if "Cannot see" not in o["story"]:
                err = 'the story must end with a "Cannot see:" line'
                continue
            return {"headline": o["headline"], "story": o["story"], "confidence": o.get("confidence") or tpl["confidence"], "cannot_see": facts.get("cannot_see", []), "model": MODEL, "source": "llm", "note": ""}
        except Exception as e:                                                # noqa: BLE001
            err = f"{type(e).__name__}: {str(e)[:160]}"
            if isinstance(e, (RuntimeError, OSError)) or "HTTP" in err:
                break
    return {**tpl, "model": None, "source": "template", "note": "model answer rejected, template used: " + err[:300]}
