"""The tools. One registry (TOOLS) feeds both front ends: the hosted /mcp endpoint and the local stdio server. Every tool returns plain JSON-able data."""
from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import knowledge as K
from . import rundb
from .sqlguard import SqlRejected

GLOSSARY_MISSING = "glossary not loaded yet (docs/schema.json has no 'metrics' list on this build); falling back to keyword search of the docs and column descriptions"


class ToolError(Exception):
    """A tool failed in a way the caller should read (bad input, SQL rejected, database not reachable)."""


def _first_sentence(text: str | None, cap: int = 220) -> str:
    t = re.sub(r"\s+", " ", text or "").strip()
    m = re.match(r"(.+?[.!?])(\s|$)", t)
    s = m.group(1) if m else t
    return s if len(s) <= cap else s[:cap - 3] + "..."


class Brain:
    def __init__(self, root: Path | str | None = None, allow_file_url: bool = False):
        self.root = root
        self.allow_file_url = allow_file_url

    @property
    def kn(self) -> K.Knowledge:
        return K.load(self.root)

    # ----------------------------------------------------------------- catalog
    def catalog(self, scope: str = "analytics") -> dict:
        kn = self.kn
        warning = None
        if rundb.configured(self.allow_file_url):
            try:
                live = rundb.run_select("select * from analytics.catalog", limit=1000, allow_file=self.allow_file_url)
                return self._live_catalog(live, kn)
            except (rundb.SqlUnavailable, SqlRejected) as e:
                warning = f"live catalog unavailable ({e}); showing docs/schema.json"
        rels = [r for r in kn.relations if scope == "all" or r.get("schema") == "analytics"]
        domains: dict[str, list] = {}
        for r in rels:
            dom = r.get("domain") or (kn.groups.get(r.get("group")) if r.get("schema") != "analytics" else None) or ("analytics (not yet assigned a domain)" if r.get("schema") == "analytics" else "other")
            domains.setdefault(dom, []).append({"name": r["name"], "kind": r.get("kind"), "description": _first_sentence(r.get("purpose")), "grain": r.get("grain"), "coverage": r.get("coverage")})
        out = {"source": "docs/schema.json", "glossary_loaded": kn.metrics is not None, "scope": scope,
               "note": "Agents can query only the analytics schema (sql tool). Pass scope='all' to also list the underlying public tables.",
               "domains": [{"domain": d, "relations": v} for d, v in domains.items()]}
        if kn.metrics is None:
            out["glossary"] = "glossary not loaded yet"
        if warning:
            out["warning"] = warning
        return out

    def _live_catalog(self, live: dict, kn: K.Knowledge) -> dict:
        cols = live["columns"]
        rows = [dict(zip(cols, r)) for r in live["rows"]]
        dkey = "domain" if "domain" in cols else None
        domains: dict[str, list] = {}
        for r in rows:
            domains.setdefault(str(r.get(dkey) or "other") if dkey else "all", []).append(r)
        return {"source": "analytics.catalog (live)", "glossary_loaded": kn.metrics is not None, "columns": cols,
                "domains": [{"domain": d, "relations": v} for d, v in domains.items()]}

    # ----------------------------------------------------------------- describe
    def describe(self, name: str) -> dict:
        kn = self.kn
        n = (name or "").strip().lower().replace('"', "")
        cands = [n, "analytics." + n, "public." + n]
        r = next((kn.by_name[c] for c in cands if c in kn.by_name), None)
        if r is None:
            close = difflib.get_close_matches(n.split(".")[-1], [x["relation"] for x in kn.relations], n=5, cutoff=0.5)
            raise ToolError(f"no view or table named {name!r}." + (f" Did you mean: {', '.join(close)}?" if close else " Use catalog() to list them."))
        ex = r.get("examples") or []
        if not ex and r.get("schema") == "analytics":
            ex = [{"title": "First rows", "sql": f"select * from {r['name']} limit 10"}]
        return {"name": r["name"], "kind": r.get("kind"), "domain": r.get("domain"), "purpose": r.get("purpose"), "grain": r.get("grain"), "source": r.get("source"), "refresh": r.get("refresh"),
                "coverage": r.get("coverage"), "caveats": r.get("caveats") or [], "queryable_by_agents": r.get("schema") == "analytics",
                "columns": [{"name": c["name"], "type": c.get("type"), "description": c.get("description")} for c in r.get("columns") or []], "examples": ex}

    # ----------------------------------------------------------------- search
    def search(self, text: str, limit: int = 10) -> dict:
        kn = self.kn
        limit = max(1, min(int(limit or 10), 25))
        hits = []
        for score, d in kn.index.search(text, limit):
            h = {"kind": d["kind"], "ref": d["ref"], "score": round(score, 2), "snippet": K.snippet(d["text"], text)}
            if d["kind"] == "doc":
                h["path"], h["line"] = d["path"], d["line"]
            elif d["kind"] == "question":
                h["path"] = "docs/questions/lonvera_items.jsonl"
                it = kn.questions[d["index"]]
                h["date"], h["status"] = K.pick(it, "date"), K.pick(it, "status")
            elif d["kind"] in ("view", "table", "column"):
                h["view"] = d["ref"].split(".")[1] if d["kind"] != "column" else ".".join(d["ref"].split(".")[:2])
            hits.append(h)
        out: dict[str, Any] = {"query": text, "hits": hits}
        if kn.metrics is None:
            out["note"] = "glossary not loaded yet: metric definitions are not in the index, only docs and column descriptions"
        if not kn.questions:
            out["questions"] = "docs/questions/ not present on this build"
        return out

    # ----------------------------------------------------------------- metric
    def metric(self, name: str) -> dict:
        kn = self.kn
        q = re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()
        if not q:
            raise ToolError("give a metric name, for example roas, nc cpa, hit rate")
        if kn.metrics is None:
            hits = self.search(name, 6)["hits"]
            return {"loaded": False, "note": GLOSSARY_MISSING, "query": name, "related": hits}
        scored = []
        qt = set(K.tokens(q))
        for m in kn.metrics:
            label = re.sub(r"[^a-z0-9]+", " ", f"{m.get('name', '')} {m.get('label', '')}".lower())
            s = 0.0
            if q == re.sub(r"[^a-z0-9]+", " ", str(m.get("name", "")).lower()):
                s = 10
            elif q and q in label:
                s = 5
            elif qt and qt <= set(K.tokens(label)):
                s = 4
            elif qt & set(K.tokens(label)):
                s = 2 * len(qt & set(K.tokens(label))) / len(qt)
            else:
                s = max(difflib.SequenceMatcher(None, q, label.strip()).ratio(), difflib.SequenceMatcher(None, q, str(m.get("name", "")).replace("_", " ")).ratio())
                s = s * 2 if s > 0.6 else 0
            if s > 0:
                scored.append((s, m))
        scored.sort(key=lambda x: -x[0])
        if not scored:
            return {"loaded": True, "query": name, "matches": [], "note": "no glossary metric matches; the docs hits below may hold the definition", "available": [m.get("name") for m in kn.metrics],
                    "related": self.search(name, 6)["hits"]}
        top = scored[0][0]
        keep = [m for s, m in scored if s >= max(1.5, top * 0.4)][:12]
        return {"loaded": True, "query": name, "matches": keep}

    # ----------------------------------------------------------------- past_questions
    def past_questions(self, text: str, limit: int = 8) -> dict:
        kn = self.kn
        if not kn.questions:
            return {"loaded": False, "note": "docs/questions/lonvera_items.jsonl is not present on this build", "matches": []}
        limit = max(1, min(int(limit or 8), 25))
        out = []
        for score, d in kn.index.search(text, limit, kinds={"question"}):
            it = kn.questions[d["index"]]
            out.append({"id": K.pick(it, "id"), "date": K.pick(it, "date"), "verbatim": K.pick(it, "verbatim"), "answer": K.pick(it, "answer"), "status": K.pick(it, "status"), "score": round(score, 2)})
        return {"loaded": True, "query": text, "matches": out, "reminder": "Re-check the numbers before reusing an old answer; every 'why did X move' answer needs a like-for-like control."}

    # ----------------------------------------------------------------- app_map
    def app_map(self, section: str | None = None, match: str | None = None) -> dict:
        p = Path(self.root or K.DEFAULT_ROOT) / "docs" / "app_map.json"
        try:
            data = json.loads(p.read_text())
        except (OSError, ValueError):
            raise ToolError("docs/app_map.json is missing; run python scripts/gen_app_map.py") from None
        if section and section not in data:
            raise ToolError(f"unknown section {section!r}; sections: {', '.join(data)}")
        out = {section: data[section]} if section else data
        if match:
            m = match.strip().lower()
            out = {k: [x for x in v if m in json.dumps(x, default=str).lower()] if isinstance(v, list) else v for k, v in out.items()}
        return out

    # ----------------------------------------------------------------- sql / freshness
    def sql(self, query: str, limit: int = 200) -> dict:
        try:
            return rundb.run_select(query, limit, allow_file=self.allow_file_url)
        except SqlRejected as e:
            raise ToolError(f"query rejected: {e}") from None
        except rundb.SqlUnavailable as e:
            msg = str(e)
            if "does not exist" in msg or "permission denied" in msg:
                msg += ". Only the analytics views are readable (call catalog); public tables are not."
            raise ToolError(msg) from None

    def why(self, args: dict) -> dict:
        from . import why as W
        def run(q, limit):
            return rundb.run_select(q, limit, allow_file=self.allow_file_url)
        try:
            res = W.run_why(args, run, self.kn)
            return res if args.get("detail") else W.compact(res)
        except W.WhyError as e:
            raise ToolError(str(e)) from None
        except SqlRejected as e:
            raise ToolError(f"query rejected: {e}") from None
        except rundb.SqlUnavailable as e:
            raise ToolError(str(e)) from None

    def why_tree(self, include_sql: bool = False) -> dict:
        from . import why_tree as WT
        return WT.tree_definition(bool(include_sql))

    def freshness(self) -> dict:
        cov = self.sql("select source, first_day, last_day, days, rows, loaded_at, note from analytics.data_coverage order by source", 200)
        failed = self.sql("select id, job, started_at, finished_at, status, data_through, left(error, 300) as error from analytics.etl_runs "
                          "where status = 'failed' and started_at > now() - interval '7 days' order by started_at desc", 50)
        return {"coverage": {"columns": cov["columns"], "rows": cov["rows"]}, "failed_runs_last_7_days": {"columns": failed["columns"], "rows": failed["rows"]},
                "row_count": cov["row_count"] + failed["row_count"],
                "reminder": "The last loaded day is yesterday (Pacific). The last 3 to 7 days still move."}


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    schema: dict
    run: Callable[[Brain, dict], Any]


def _obj(props: dict | None = None, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props or {}, "required": required or [], "additionalProperties": False}


S = {"type": "string"}
TOOLS: list[Tool] = [
    Tool("catalog", "Start here for any data question: the queryable analytics views by domain, each with a one-line description, grain and start date. Then search / describe / sql.",
         _obj({"scope": {"type": "string", "enum": ["analytics", "all"], "description": "analytics (default) = what sql can read; all = also the underlying tables"}}), lambda b, a: b.catalog(a.get("scope") or "analytics")),
    Tool("describe", "Columns, types, descriptions, caveats, coverage and an example query for one view. Use before writing SQL. queryable_by_agents=false means sql cannot read it.",
         _obj({"name": {**S, "description": "view name, with or without the analytics. prefix"}}, ["name"]), lambda b, a: b.describe(a.get("name", ""))),
    Tool("search", "Keyword search over views, columns, definitions, docs (thresholds, schedules, routes) and past questions. For why questions use `why`, not search. NC = new customer.",
         _obj({"text": S, "limit": {"type": "integer", "minimum": 1, "maximum": 25}}, ["text"]), lambda b, a: b.search(a.get("text", ""), a.get("limit") or 10)),
    Tool("metric", "What a metric means: formula, source, default basis, other bases and traps. Fuzzy: 'roas' lists every ROAS variant. Use before quoting any ratio. For definitions it lacks, it searches the docs.",
         _obj({"name": S}, ["name"]), lambda b, a: b.metric(a.get("name", ""))),
    Tool("freshness", "Reads analytics.data_coverage (each source: first and last loaded day, loaded_at) and failed etl_runs of the last 7 days. Use before trusting a recent number.",
         _obj(), lambda b, a: b.freshness()),
    Tool("sql", "Run ONE read-only SELECT or WITH on the analytics views only (30 s limit, rows capped). Use after catalog and describe; never guess a column. Name the basis and window in the answer.",
         _obj({"query": S, "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "description": "row cap, default 200"}}, ["query"]), lambda b, a: b.sql(a.get("query", ""), a.get("limit") or 200)),
    Tool("why", "CALL FIRST for any why / what caused / what explains question, and for 'does X raise/cause Y'. Walks the metric tree live, runs controls, lists known causes and data gaps. Needs metric + start/end.",
         _obj({"metric": {**S, "description": "node id or plain words: spend (cliff), meta_spend, frequency, cpm, cpc, ctr, nc_cpa, nc_roas, nc_revenue (new customers), repeat_revenue (renewals, rebills), nc_aov, conversion"}, "start": {**S, "description": "target window start, YYYY-MM-DD"}, "end": {**S, "description": "target window end"}, "detail": {"type": "boolean", "description": "true = full tree and every finding (long)"}, "compare": {"type": "string", "enum": ["prior_period", "last_month", "last_year", "custom"]}, "compare_start": S, "compare_end": S, "product": S,
               "platform": {"type": "string", "enum": ["meta", "google"]}, "max_depth": {"type": "integer", "minimum": 1, "maximum": 12}, "grain": {"type": "string", "enum": ["auto", "day", "week", "month"]},
               "max_steps": {"type": "integer", "minimum": 5, "maximum": 80}}), lambda b, a: b.why(a)),
    Tool("why_tree", "The why-tree definition: nodes, formulas, children, materiality, linked past questions, known causes, data gaps. Read-only, runs no SQL.",
         _obj({"include_sql": {"type": "boolean"}}), lambda b, a: b.why_tree(a.get("include_sql", False))),
    Tool("past_questions", "Search Josh's past business questions and the answers given. Past questions and answers. For a why question call `why` first; these are leads to re-check, not facts.",
         _obj({"text": S, "limit": {"type": "integer", "minimum": 1, "maximum": 25}}, ["text"]), lambda b, a: b.past_questions(a.get("text", ""), a.get("limit") or 8)),
    Tool("app_map", "Services (cron schedules), pages, API routes and ETL job order, from the code. Filter with match=. For how or why something runs, also search the docs (DEPLOY.md, feature pages).",
         _obj({"section": {"type": "string", "enum": ["services", "etl_jobs", "pages", "routes"]}, "match": {**S, "description": "keep only entries containing this text, e.g. 'mcp', 'feeds', 'promotions'"}}), lambda b, a: b.app_map(a.get("section"), a.get("match"))),
]
BY_NAME = {t.name: t for t in TOOLS}


def call(brain: Brain, name: str, args: dict | None) -> Any:
    t = BY_NAME.get(name)
    if t is None:
        raise ToolError(f"unknown tool {name!r}; tools: {', '.join(BY_NAME)}")
    args = args or {}
    if not isinstance(args, dict):
        raise ToolError("arguments must be an object")
    allowed = set(t.schema["properties"])
    extra = set(args) - allowed
    if extra:
        raise ToolError(f"unknown argument(s): {', '.join(sorted(extra))}")
    for r in t.schema["required"]:
        if r not in args or (isinstance(args[r], str) and not args[r].strip()):
            raise ToolError(f"{r} is required")
    for k, v in args.items():
        spec = t.schema["properties"][k]
        if spec.get("type") == "string" and not isinstance(v, str):
            raise ToolError(f"{k} must be text")
        if spec.get("type") == "integer" and (not isinstance(v, int) or isinstance(v, bool)):
            raise ToolError(f"{k} must be a whole number")
    return t.run(brain, args)


def row_count(result: Any) -> int | None:
    """Rows returned, for the audit row (never the rows themselves)."""
    if isinstance(result, dict):
        for k in ("row_count",):
            if isinstance(result.get(k), int):
                return result[k]
        for k in ("hits", "matches", "domains"):
            if isinstance(result.get(k), list):
                return len(result[k])
    return None


def dumps(result: Any) -> str:
    return json.dumps(result, ensure_ascii=False, default=str)
