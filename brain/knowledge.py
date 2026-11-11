"""The offline knowledge base: docs/schema.json, every docs/**/*.md and docs/questions/. Loaded once per process (per root), plain BM25 keyword search, no external services."""
from __future__ import annotations

import json
import math
import re
import threading
from collections import Counter
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parent.parent
SKIP_DOCS = {"DATA_SCHEMA.md"}          # the same text as schema.json, which is indexed per view and column
_TOKEN = re.compile(r"[a-z0-9_]+")
_STOP = frozenset("a an and are as at be by for from how in is it of on or that the this to was what when where which who why with".split())


def tokens(text: str) -> list[str]:
    out = []
    for t in _TOKEN.findall((text or "").lower()):
        if t in _STOP:
            continue
        out.append(t)
        if "_" in t:
            out.extend(p for p in t.split("_") if p and p not in _STOP)
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
        q = list(dict.fromkeys(tokens(query)))
        if not q:
            return []
        scored = []
        for i, d in enumerate(self.docs):
            if kinds and d["kind"] not in kinds:
                continue
            tf = self.tf[i]
            s = 0.0
            for t in q:
                f = tf.get(t, 0)
                if f:
                    s += self.idf.get(t, 0) * f * (self.K1 + 1) / (f + self.K1 * (1 - self.B + self.B * self.len[i] / self.avg))
            if s > 0:
                scored.append((s, d))
        scored.sort(key=lambda x: -x[0])
        return scored[:limit]


def snippet(text: str, query: str, width: int = 260) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    low = text.lower()
    pos = -1
    for t in dict.fromkeys(tokens(query)):
        p = low.find(t)
        if p >= 0 and (pos < 0 or p < pos):
            pos = p
    start = max(0, pos - width // 3) if pos >= 0 else 0
    s = text[start:start + width]
    return ("..." if start else "") + s + ("..." if start + width < len(text) else "")


def _md_chunks(path: Path, rel: str) -> list[dict]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    out, head, start, buf = [], path.stem, 1, []

    def flush():
        body = "\n".join(buf).strip()
        if body:
            for k in range(0, len(body), 1800):                  # long sections become several hits
                out.append({"kind": "doc", "ref": f"{rel}#{head}" if head != path.stem else rel, "path": rel, "line": start, "title": head, "text": body[k:k + 1800]})

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


def pick(item: dict, field: str):
    for k in FIELD_ALIASES[field]:
        if k in item and item[k] not in (None, ""):
            return item[k]
    return None


class Knowledge:
    def __init__(self, root: Path | str | None = None):
        self.root = Path(root or DEFAULT_ROOT)
        docs_dir = self.root / "docs"
        self.schema = self._json(docs_dir / "schema.json") or {}
        self.relations: list[dict] = self.schema.get("relations") or []
        self.metrics: list[dict] | None = self.schema.get("metrics") if isinstance(self.schema.get("metrics"), list) else None
        self.groups = {g["key"]: g.get("title", g["key"]) for g in self.schema.get("groups") or []}
        self.by_name = {r["name"]: r for r in self.relations}
        self.questions = self._load_questions(docs_dir / "questions" / "lonvera_items.jsonl")
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
                docs.extend(_md_chunks(p, rel))
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
    key = str(Path(root or DEFAULT_ROOT).resolve())
    with _lock:
        k = _cache.get(key)
        if k is None:
            k = _cache[key] = Knowledge(key)
        return k
