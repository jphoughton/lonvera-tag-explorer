"""Load a hook-extraction cycle's results as ad_hooks for one product.
A hook is the first spoken line + the on-image headline / first on-screen text of an ad, boiled down to one short line. The file is a JSON list of
{row, concept_id, ad_family, unit_id, hook, spoken, on_screen, hook_source, confidence, note} (row / concept_id / ad_family as in the tag-map row files).
Rows are matched to tracker_rows exactly like etl.load_tags does (load_tags.match_rows): by (concept_id, family) + ordinal among rows sharing that pair
(ordered by the file's `row`), never by sheet row. The ordinals are counted over EVERY entry of the file; entries with an empty hook are skipped only after
the match, so they cannot shift their neighbours.
The tracker keeps growing after a cycle, so the match is NOT strict: tracker rows without a hook stay without one until a later cycle, hooks whose key is
no longer in the tracker are skipped (listed as a warning); the load fails only when the matched coverage of the product's real tracker rows is below
--min-coverage (default 0.90), or when a hook_source / confidence value is not one of the known ones.
One row per tracker row (ad_hooks.tracker_row_id is the key): a load upserts, so re-running a version replaces its rows and a newer version replaces the
older hook of every row it covers; rows the file does not cover (or covers with an empty hook) keep what they have. The whole write is one transaction.
Usage: python -m etl.load_hooks --file F --version hooks-v1-2026-11-05 --product vita-bull [--min-coverage 0.90] [--allow-mismatch] [--extractor claude-sonnet-5-5]"""
from __future__ import annotations

import argparse
import collections

from . import db
from .load_tags import _REAL, _fmt_keys, _live, load_file, match_rows

SOURCES = ("spoken", "on_screen", "both", "copy", "none")
CONFIDENCES = ("high", "medium", "low")
MAX_WORDS = 18           # "one short line"; longer hooks are loaded but counted in the stats and logged


def _text(v) -> str | None:
    """One line: whitespace (incl. newlines) collapsed; empty -> None."""
    if v is None:
        return None
    s = " ".join(str(v).split())
    return s or None


def _enum(v, allowed: tuple[str, ...], what: str) -> str | None:
    """Lower-cased value of a closed list ('on-screen' / 'On screen' -> 'on_screen'); empty -> None; anything else is an error."""
    s = _text(v)
    if s is None:
        return None
    s = s.lower().replace("-", "_").replace(" ", "_")
    if s not in allowed:
        raise RuntimeError(f"hook {what} {v!r} is not one of {', '.join(allowed)}")
    return s


def _check_entries(entries) -> None:
    if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
        raise RuntimeError("hook file must be a JSON list of objects")
    for i, e in enumerate(entries):
        if e.get("row") is None or _text(e.get("concept_id")) is None:
            raise RuntimeError(f"hook entry {i} has no row / concept_id (needed to match the tracker): {e}")


def load(conn, product_slug: str, version: str, entries: list[dict], allow_mismatch: bool = False, min_coverage: float = 0.90,
         extractor: str | None = None) -> dict:
    _check_entries(entries)
    p = conn.execute("SELECT id FROM products WHERE slug=%s", (product_slug,)).fetchone()
    if p is None:
        raise RuntimeError(f"unknown product {product_slug!r}")
    pid = p[0]
    matches, problems = match_rows(conn, pid, entries)          # every entry takes part in the ordinals, empty hooks included
    n_real = conn.execute("SELECT count(*) FROM tracker_rows WHERE product_id=%s" + _live(conn) + _REAL, (pid,)).fetchone()[0]
    coverage = len(matches) / n_real if n_real else 0.0
    if coverage < min_coverage and not allow_mismatch:
        raise RuntimeError(f"hook file does not match the tracker: {len(matches)} of {n_real} tracker rows matched ({coverage:.1%}) < min coverage {min_coverage:.0%}; "
                           f"{ {k: _fmt_keys(v, 5) if k != 'count_mismatch' else v[:5] for k, v in problems.items() if v} }")
    rows, empty, long_hooks = [], 0, 0
    for e, rid in matches:
        hook = _text(e.get("hook"))
        if hook is None:
            empty += 1
            continue
        long_hooks += len(hook.split()) > MAX_WORDS
        rows.append((rid, hook, _text(e.get("spoken")), _text(e.get("on_screen")), _enum(e.get("hook_source"), SOURCES, "source"),
                     _enum(e.get("confidence"), CONFIDENCES, "confidence"), _text(e.get("note")), version, _text(e.get("extractor")) or extractor))
    with conn.transaction():
        db.execmany(conn, """INSERT INTO ad_hooks (tracker_row_id, hook, spoken, on_screen, hook_source, confidence, note, version, extractor, extracted_at)
                             VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,now())
                             ON CONFLICT (tracker_row_id) DO UPDATE SET hook=EXCLUDED.hook, spoken=EXCLUDED.spoken, on_screen=EXCLUDED.on_screen,
                               hook_source=EXCLUDED.hook_source, confidence=EXCLUDED.confidence, note=EXCLUDED.note, version=EXCLUDED.version,
                               extractor=EXCLUDED.extractor, extracted_at=now()""", rows)
        prod_rows = [r[0] for r in conn.execute("SELECT id FROM tracker_rows WHERE product_id=%s" + _live(conn) + _REAL, (pid,)).fetchall()]
        hooked = conn.execute("SELECT count(*) FROM ad_hooks WHERE tracker_row_id = ANY(%s)", (prod_rows,)).fetchone()[0]
    return {"product": product_slug, "version": version, "hooks_in_file": len(entries), "matched": len(matches), "tracker_rows": n_real,
            "coverage_pct": round(100 * coverage, 1), "hooks_written": len(rows), "skipped_empty_hook": empty,
            "tags_skipped_not_in_tracker": len(problems["tags_without_tracker_row"]), "skipped_keys": _fmt_keys(problems["tags_without_tracker_row"]),
            "tracker_rows_without_hook": n_real - hooked, "hooked_pct": round(100 * hooked / n_real, 1) if n_real else 0.0,
            "hooks_over_18_words": long_hooks, "problems": {k: len(v) for k, v in problems.items()},
            "hook_source": dict(collections.Counter(r[4] for r in rows)), "confidence": dict(collections.Counter(r[5] for r in rows))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True, help="JSON list of {row, concept_id, ad_family, unit_id, hook, spoken, on_screen, hook_source, confidence, note}")
    ap.add_argument("--version", required=True, help="e.g. hooks-v1-2026-11-05")
    ap.add_argument("--product", required=True, help="products.slug: vita-bull | beef-liver | boneflex")
    ap.add_argument("--min-coverage", type=float, default=0.90, help="fail below this share of the product's tracker rows matched (default 0.90)")
    ap.add_argument("--allow-mismatch", action="store_true", help="do not fail on low coverage")
    ap.add_argument("--extractor", help="who / what produced the file (stored on rows whose entry has no `extractor` of its own)")
    a = ap.parse_args()
    with db.JobRun("load_hooks") as run, db.connect() as conn:
        stats = load(conn, a.product, a.version, load_file(a.file), a.allow_mismatch, a.min_coverage, a.extractor)
        conn.commit()
        run.rows_written = stats["hooks_written"]
        run.stats = stats
        db.log(f"  {a.product}: matched {stats['matched']} of {stats['tracker_rows']} tracker rows ({stats['coverage_pct']}%), wrote {stats['hooks_written']} hooks "
               f"({stats['skipped_empty_hook']} empty skipped), {stats['tags_skipped_not_in_tracker']} hook(s) skipped (key not in tracker), "
               f"{stats['tracker_rows_without_hook']} tracker rows without a hook ({stats['hooked_pct']}% hooked)")
        db.log(f"  by hook_source {stats['hook_source']}, by confidence {stats['confidence']}")
        if stats["skipped_keys"]:
            db.log(f"  WARN hooks skipped, not in the tracker: {stats['skipped_keys']}")
        if stats["hooks_over_18_words"]:
            db.log(f"  WARN {stats['hooks_over_18_words']} hook(s) longer than {MAX_WORDS} words")


if __name__ == "__main__":
    main()
