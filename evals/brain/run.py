"""Run the eval: one fresh, tool-restricted agent per case against a local stdio brain with the holdout set, then a separate tool-less grader.

    PYTHONPATH=. python evals/brain/run.py --split tune --run r0 [--only a01,b02] [--jobs 4]

The agent is `claude -p` (Sonnet) in an empty temp directory with every built-in tool switched off, CLAUDE.md and auto memory disabled, only the lonvera-brain MCP tools allowed.
It cannot read the repo, the ledger or the machine's memory files. The grader is a second `claude -p` call with no tools. The brain URL file is never printed or stored.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
MODEL = "claude-sonnet-5-5"
PY = os.environ.get("EVAL_PYTHON", str(Path.home() / "lonvera-tag-explorer-brain-mcp/.venv/bin/python"))   # any venv with the mcp package (requirements-dev.txt)
ENV_OFF = {"CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1", "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"}
AGENT_PROMPT = "{question}\n\nAnswer from the data; name the basis and the window."
GRADER_PROMPT = """You grade an AI agent's answer to a business-data question against a known key. Be strict about facts and numbers, lenient about wording.

QUESTION (as of {as_of}):
{question}

EXPECTED ANSWER (the key):
{expected}

MUST MENTION (each item should appear or be clearly implied; numbers may be formatted differently, e.g. 80.8K for 80,797):
{must_mention}

MUST NOT (claims or behaviours that make the answer wrong):
{must_not}

GRADING NOTES:
{notes}

AGENT'S ANSWER:
{answer}

TOOLS THE AGENT CALLED (names only): {tools}

Rules:
- Numeric answers within 2% of the key count as correct.
- verdict "correct" (score 2): the answer gives the key's substance, uses the right basis and window, nothing in MUST NOT.
- verdict "partial" (score 1): part of the key (e.g. right number but missing the basis or the cause, or an honest "this is not in the database" where the key also needs a mechanism), or a correct number with one minor error.
- verdict "wrong" (score 0): wrong number, wrong basis, invented data, fabricated cause stated as fact, claims the data exists when it does not, or no answer.
- guardrail_violations: list any of: mixed bases in one ratio, added Meta and Google purchases or revenue, claimed a cause without saying "consistent with" or a control, stated a threshold-less hit rate, used a number the data cannot support, said revenue is net when it is gross.
Return ONLY a JSON object: {{"verdict": "correct|partial|wrong", "score": 0|1|2, "missing": [..], "errors": [..], "guardrail_violations": [..], "reason": "one sentence"}}"""

lock = threading.Lock()


def mcp_config(holdout: Path | None, tmp: Path) -> Path:
    env = {"PYTHONPATH": str(ROOT)}
    if holdout:
        env["BRAIN_HOLDOUT_FILE"] = str(holdout)
    cfg = {"mcpServers": {"lonvera-brain": {"command": PY, "args": ["-m", "brain.server"], "cwd": str(ROOT), "env": env}}}
    p = tmp / "mcp.json"
    p.write_text(json.dumps(cfg))
    return p


def claude(prompt: str, extra: list[str], cwd: Path, timeout: int) -> list[dict]:
    cmd = ["claude", "-p", "--model", MODEL, "--output-format", "stream-json", "--verbose", "--no-session-persistence", "--disable-slash-commands", *extra]
    p = subprocess.run(cmd, input=prompt, capture_output=True, text=True, cwd=cwd, timeout=timeout, env={**os.environ, **ENV_OFF})
    ev = []
    for line in p.stdout.splitlines():
        try:
            ev.append(json.loads(line))
        except ValueError:
            pass
    if not ev:
        ev.append({"type": "result", "is_error": True, "result": f"no output; rc={p.returncode}; stderr={p.stderr[-300:]}"})
    return ev


def run_agent(case: dict, cfg: Path, cwd: Path) -> dict:
    t0 = time.time()
    ev = claude(AGENT_PROMPT.format(question=case["question"]), ["--tools", "", "--strict-mcp-config", "--mcp-config", str(cfg), "--allowedTools", "mcp__lonvera-brain__*"], cwd, 900)
    calls, results = [], {}
    for e in ev:
        if e.get("type") == "assistant":
            for b in (e.get("message") or {}).get("content") or []:
                if b.get("type") == "tool_use":
                    calls.append({"id": b.get("id"), "name": str(b.get("name")).replace("mcp__lonvera-brain__", ""), "input": b.get("input")})
        elif e.get("type") == "user":
            for b in (e.get("message") or {}).get("content") or []:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    c = b.get("content")
                    results[b.get("tool_use_id")] = (c if isinstance(c, str) else json.dumps(c))[:600]
    for c in calls:
        c["result_head"] = results.get(c.pop("id"), "")
    res = next((e for e in reversed(ev) if e.get("type") == "result"), {})
    return {"answer": res.get("result") or "", "is_error": bool(res.get("is_error")), "tool_calls": calls, "turns": res.get("num_turns"), "cost_usd": res.get("total_cost_usd"), "seconds": round(time.time() - t0, 1)}


def grade(case: dict, ans: dict, cwd: Path) -> dict:
    prompt = GRADER_PROMPT.format(as_of=case["as_of"], question=case["question"], expected=case["expected_answer"], must_mention=json.dumps(case["must_mention"]), must_not=json.dumps(case["must_not"]),
                                  notes=case["grading_notes"] or "none", answer=ans["answer"] or "(no answer)", tools=", ".join(c["name"] for c in ans["tool_calls"]) or "none")
    ev = claude(prompt, ["--tools", "", "--strict-mcp-config"], cwd, 300)
    res = next((e for e in reversed(ev) if e.get("type") == "result"), {})
    txt = res.get("result") or ""
    a, b = txt.find("{"), txt.rfind("}")
    try:
        g = json.loads(txt[a:b + 1])
        g["score"] = {"correct": 2, "partial": 1, "wrong": 0}.get(g.get("verdict"), 0)
    except ValueError:
        g = {"verdict": "wrong", "score": 0, "missing": [], "errors": ["grader returned no JSON"], "guardrail_violations": [], "reason": txt[:200]}
    return g


def one(case: dict, cfg: Path, cwd: Path) -> dict:
    ans = run_agent(case, cfg, cwd)
    g = grade(case, ans, cwd)
    return {"id": case["id"], "split": case["split"], "type": case["type"], "question": case["question"], "expected_answer": case["expected_answer"], **ans, "grade": g}


def summarize(rows: list[dict]) -> dict:
    out = {"n": len(rows), "score": round(sum(r["grade"]["score"] for r in rows) / (2 * max(1, len(rows))) * 100, 1), "correct": sum(r["grade"]["verdict"] == "correct" for r in rows),
           "partial": sum(r["grade"]["verdict"] == "partial" for r in rows), "wrong": sum(r["grade"]["verdict"] == "wrong" for r in rows), "by_type": {}}
    for t in sorted({r["type"] for r in rows}):
        rs = [r for r in rows if r["type"] == t]
        out["by_type"][t] = {"n": len(rs), "score": round(sum(r["grade"]["score"] for r in rs) / (2 * len(rs)) * 100, 1), "correct": sum(r["grade"]["verdict"] == "correct" for r in rs),
                             "partial": sum(r["grade"]["verdict"] == "partial" for r in rs), "wrong": sum(r["grade"]["verdict"] == "wrong" for r in rs)}
    out["guardrail_violations"] = sum(len(r["grade"].get("guardrail_violations") or []) for r in rows)
    out["avg_tool_calls"] = round(sum(len(r["tool_calls"]) for r in rows) / max(1, len(rows)), 1)
    out["cost_usd"] = round(sum(r.get("cost_usd") or 0 for r in rows), 2)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["tune", "test", "all"], default="tune")
    ap.add_argument("--run", required=True)
    ap.add_argument("--only", default="")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--no-holdout", action="store_true", help="debug only: leaves the answers visible")
    a = ap.parse_args()
    cases = [json.loads(ln) for ln in (HERE / "cases.jsonl").read_text().splitlines() if ln.strip()]
    if a.split != "all":
        cases = [c for c in cases if c["split"] == a.split]
    if a.only:
        keep = set(a.only.split(","))
        cases = [c for c in cases if c["id"] in keep]
    out_dir = HERE / "results"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"{a.run}.jsonl"
    done = {json.loads(ln)["id"] for ln in out.read_text().splitlines()} if out.exists() else set()
    todo = [c for c in cases if c["id"] not in done]
    tmp = Path(tempfile.mkdtemp(prefix="brain-eval-"))
    cfg = mcp_config(None if a.no_holdout else HERE / "holdout.json", tmp)
    cwd = tmp / "cwd"
    cwd.mkdir()
    print(f"[{a.run}] {len(todo)} cases to run ({len(done)} already done), jobs={a.jobs}, holdout={'OFF' if a.no_holdout else 'on'}", flush=True)
    t0, n = time.time(), 0

    def work(c):
        try:
            return one(c, cfg, cwd)
        except Exception as e:  # keep the run going
            return {"id": c["id"], "split": c["split"], "type": c["type"], "question": c["question"], "expected_answer": c["expected_answer"], "answer": "", "is_error": True, "tool_calls": [],
                    "grade": {"verdict": "wrong", "score": 0, "missing": [], "errors": [f"runner error: {e}"], "guardrail_violations": [], "reason": "runner error"}}

    with cf.ThreadPoolExecutor(a.jobs) as ex:
        for r in ex.map(work, todo):
            n += 1
            with lock, out.open("a") as fh:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            el = time.time() - t0
            print(f"[{a.run}] {n}/{len(todo)} {r['id']} {r['grade']['verdict']:7} calls={len(r['tool_calls'])} elapsed={el / 60:.1f}m eta={(el / n) * (len(todo) - n) / 60:.1f}m", flush=True)
    rows = [json.loads(ln) for ln in out.read_text().splitlines()]
    s = summarize(rows)
    (out_dir / f"{a.run}.summary.json").write_text(json.dumps(s, indent=1))
    print(json.dumps(s, indent=1))


if __name__ == "__main__":
    sys.exit(main())
