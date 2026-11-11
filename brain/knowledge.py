"""The offline knowledge base: docs/schema.json, every docs/**/*.md and docs/questions/. Loaded once per process (per root), plain BM25 keyword search, no external services."""
from __future__ import annotations

import json
import math
import os
import re
import threading
from collections import Counter
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parent.parent
SKIP_DOCS = {"DATA_SCHEMA.md"}          # the same text as schema.json, which is indexed per view and column
_TOKEN = re.compile(r"[a-z0-9_]+")
_STOP = frozenset("a an and are as at be by for from how in is it of on or that the this to was what when where which who why with".split())


# Query-side synonyms (weight 0.5): the shorthand Josh and the docs use for the same thing. Keys and values are tokens after singularising.
SYNONYMS = {
    "nc": ["new", "customer", "first", "order"], "cpa": ["cost", "acquisition", "purchase"], "cac": ["cost", "acquisition", "customer"], "roas": ["return", "revenue", "spend"],
    "mer": ["revenue", "spend", "total"], "cpm": ["cost", "thousand", "impression"], "cpc": ["cost", "click"], "ctr": ["click", "impression"], "ltv": ["lifetime", "value", "cohort"],
    "tof": ["cold"], "dtc": ["shopify"], "ad": ["creative"], "creative": ["ad"], "hit": ["reached", "threshold"], "scaling": ["scale", "stage"], "testing": ["test", "stage"],
    "frequency": ["reach", "impression"], "cancel": ["cancelled", "cancellation"], "refund": ["net", "gross"], "bid": ["cap", "cost", "floor"], "backup": ["dump", "restore"],
    "cron": ["schedule", "service"], "schedule": ["cron", "service"], "nightly": ["daily", "cron"], "endpoint": ["route", "api"], "page": ["route", "url"], "launch": ["first", "spend", "family"],
}


def _norm(t: str) -> str:
    return t[:-1] if len(t) > 3 and t.endswith("s") and not t.endswith("ss") else t


def tokens(text: str) -> list[str]:
    out = []
    for t in _TOKEN.findall((text or "").lower()):
        if t in _STOP:
            continue
        t = _norm(t)
        out.append(t)
        if "_" in t:
            out.extend(_norm(p) for p in t.split("_") if p and p not in _STOP)
    return out


class Index:
    """BM25 over small documents {kind, ref, title, text, ...}."""

    K1, B = 1.5, 0.75

    def __init__(self, docs: list[dict]):
        self.docs = docs
        self.tf = [Counter(tokens(d["title"] + " " + d["title"] + " " + d["text"])) for d in docs]
        self.len = [sum(c.values()) or 1 for c in self.tf]
        self.avg = sum(self.len) / max(1, len(docs))
        df: Counter = Counter()
        for c in self.tf:
            df.update(c.keys())
        n = max(1, len(docs))
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def search(self, query: str, limit: int = 10, kinds: set[str] | None = None) -> list[tuple[float, dict]]:
        base = list(dict.fromkeys(tokens(query)))
        if not base:
            return []
        w = {t: 1.0 for t in base}
        for t in base:
            for x in SYNONYMS.get(t, []):
                x = _norm(x)
                w.setdefault(x, 0.5)
        q = list(w)
        scored = []
        for i, d in enumerate(self.docs):
            if kinds and d["kind"] not in kinds:
                continue
            tf = self.tf[i]
            s = 0.0
            for t in q:
                f = tf.get(t, 0)
                if f:
                    s += w[t] * self.idf.get(t, 0) * f * (self.K1 + 1) / (f + self.K1 * (1 - self.B + self.B * self.len[i] / self.avg))
            if s > 0:
                scored.append((s, d))
        scored.sort(key=lambda x: -x[0])
        return scored[:limit]


def snippet(text: str, query: str, width: int = 360) -> str:
    """The window of `width` characters that holds the most distinct query words (ties: the earliest), so a definition is not cut off by an earlier stray match."""
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= width:
        return text
    low = text.lower()
    toks = list(dict.fromkeys(tokens(query)))
    pos = sorted({m.start() for t in toks for m in re.finditer(re.escape(t), low)})
    best, best_n = 0, -1
    for p0 in pos or [0]:
        start = max(0, p0 - width // 6)
        win = low[start:start + width]
        n = 100 * sum(1 for t in toks if t in win) + sum(win.count(t) for t in toks)
        if n > best_n:
            best, best_n = start, n
    s = text[best:best + width]
    return ("..." if best else "") + s + ("..." if best + width < len(text) else "")


ENV_HOLDOUT = "BRAIN_HOLDOUT_FILE"       # eval only: a file of ledger item ids whose answers the brain must not see (the hosted server never sets it)
_NGRAM = 6


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


class Holdout:
    """Eval-only blind spot. Holds the ids of ledger items to hide and the word n-grams of their answers; any line of text that shares an n-gram with a held-out answer is dropped."""

    def __init__(self, ids: set[str], answers: list[str]):
        self.ids = ids
        self.grams: set[tuple[str, ...]] = set()
        self.sizes: set[int] = set()
        for a in answers:
            w = _words(a)
            if len(w) < 4:
                continue
            n = min(_NGRAM, len(w))
            self.sizes.add(n)
            self.grams.update(tuple(w[i:i + n]) for i in range(len(w) - n + 1))

    def leaks(self, text: str) -> bool:
        if not self.grams:
            return False
        w = _words(text)
        for n in self.sizes:
            for i in range(len(w) - n + 1):
                if tuple(w[i:i + n]) in self.grams:
                    return True
        return False


def read_holdout_ids(path: str | Path) -> set[str]:
    raw = Path(path).expanduser().read_text(encoding="utf-8")
    try:
        v = json.loads(raw)
        if isinstance(v, dict):
            v = v.get("source_item_ids") or v.get("ids") or []
        return {str(x) for x in v}
    except ValueError:
        return {ln.strip() for ln in raw.splitlines() if ln.strip() and not ln.startswith("#")}


def _md_chunks(path: Path, rel: str, holdout: "Holdout | None" = None) -> list[dict]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    if holdout:                                              # blank every line that repeats a held-out answer (keeps fences and line numbers intact)
        lines = ["" if holdout.leaks(ln) else ln for ln in lines]
    out, head, start, buf = [], path.stem, 1, []

    def flush():
        body = "\n".join(buf).strip()
        if body:
            for k in range(0, len(body), 1000):                  # long sections become several hits
                out.append({"kind": "doc", "ref": f"{rel}#{head}" if head != path.stem else rel, "path": rel, "line": start, "title": head, "text": body[k:k + 1000]})

    fence = False
    for n, line in enumerate(lines, 1):
        if line.lstrip().startswith("```"):
            fence = not fence
        m = re.match(r"^(#{1,4})\s+(.*)", line) if not fence else None
        if m:
            flush()
            head, start, buf = m.group(2).strip(), n, []
        else:
            buf.append(line)
    flush()
    return out


FIELD_ALIASES = {
    "id": ("id", "item_id", "qid", "n"),
    "date": ("date", "asked_at", "asked", "ts", "timestamp", "when", "day"),
    "verbatim": ("verbatim", "question", "text", "prompt", "ask", "message"),
    "answer": ("answer", "response", "reply", "result", "resolution", "summary", "finding"),
    "status": ("status", "state", "outcome", "resolved"),
}


def _answer_text(item: dict) -> str:
    a = pick(item, "answer")
    return a if isinstance(a, str) else json.dumps(a) if a else ""


def pick(item: dict, field: str):
    for k in FIELD_ALIASES[field]:
        if k in item and item[k] not in (None, ""):
            return item[k]
    return None


class Knowledge:
    def __init__(self, root: Path | str | None = None, holdout_file: str | None = None):
        self.root = Path(root or DEFAULT_ROOT)
        docs_dir = self.root / "docs"
        self.holdout: Holdout | None = None
        all_q = self._load_questions(docs_dir / "questions" / "lonvera_items.jsonl")
        if holdout_file:
            ids = read_holdout_ids(holdout_file)
            self.holdout = Holdout(ids, [str(pick(q, "answer") or "") for q in all_q if str(pick(q, "id")) in ids])
        self.schema = self._json(docs_dir / "schema.json") or {}
        self.relations: list[dict] = self.schema.get("relations") or []
        self.metrics: list[dict] | None = self.schema.get("metrics") if isinstance(self.schema.get("metrics"), list) else None
        self.groups = {g["key"]: g.get("title", g["key"]) for g in self.schema.get("groups") or []}
        self.by_name = {r["name"]: r for r in self.relations}
        h = self.holdout
        # held-out items go, and so does any other item whose answer repeats a held-out answer (the ledger has near-duplicate items)
        self.questions = [q for q in all_q if not h or (str(pick(q, "id")) not in h.ids and not h.leaks(_answer_text(q)))]
        docs: list[dict] = []
        for r in self.relations:
            cols = "; ".join(f"{c['name']}: {c.get('description') or ''}" for c in r.get("columns") or [])
            docs.append({"kind": "view" if r.get("kind") == "view" else "table", "ref": r["name"], "title": r["name"],
                         "text": " ".join(str(r.get(k) or "") for k in ("purpose", "grain", "source", "coverage")) + " " + " ".join(r.get("caveats") or []) + " " + cols})
            for c in r.get("columns") or []:
                docs.append({"kind": "column", "ref": f"{r['name']}.{c['name']}", "title": c["name"], "text": f"{r['name']} {c.get('type') or ''} {c.get('description') or ''}"})
        for m in self.metrics or []:
            docs.append({"kind": "metric", "ref": m.get("name", ""), "title": f"{m.get('name', '')} {m.get('label', '')}", "text": " ".join(str(v) for v in m.values() if isinstance(v, (str, list)))})
        if docs_dir.is_dir():
            for p in sorted(docs_dir.rglob("*.md")):
                rel = p.relative_to(self.root).as_posix()
                if p.name in SKIP_DOCS and p.parent == docs_dir:
                    continue
                if h and rel.startswith("docs/questions/"):      # the ledger and the why files hold the answers
                    continue
                docs.extend(_md_chunks(p, rel, h))
        for i, it in enumerate(self.questions):
            ans = pick(it, "answer")
            docs.append({"kind": "question", "ref": f"docs/questions/lonvera_items.jsonl#{pick(it, 'id') or i + 1}", "title": str(pick(it, "date") or ""),
                         "text": f"{pick(it, 'verbatim') or ''} {ans if isinstance(ans, str) else json.dumps(ans) if ans else ''}", "index": i})
        self.index = Index(docs)

    @staticmethod
    def _json(path: Path):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    @staticmethod
    def _load_questions(path: Path) -> list[dict]:
        out = []
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    try:
                        v = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(v, dict):
                        out.append(v)
        except OSError:
            pass
        return out


_cache: dict[str, Knowledge] = {}
_lock = threading.Lock()


def load(root: Path | str | None = None) -> Knowledge:
    """One Knowledge per root per process (the files are baked into the image and do not change while it runs)."""
    hf = (os.environ.get(ENV_HOLDOUT) or "").strip() or None
    key = f"{Path(root or DEFAULT_ROOT).resolve()}|{hf or ''}"
    with _lock:
        k = _cache.get(key)
        if k is None:
            k = _cache[key] = Knowledge(str(Path(root or DEFAULT_ROOT).resolve()), hf)
        return k
