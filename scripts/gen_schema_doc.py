#!/usr/bin/env python3
"""Generate the data dictionary of the Lonvera marketing database from the LIVE schema plus the curated descriptions.

    docs/schema/descriptions.py   curated text (the only file people edit)           -> read
    Postgres (a migrated DB)      tables, views, columns, types, keys                -> introspected, never written

Outputs
    docs/DATA_SCHEMA.md           human + agent readable dictionary
    docs/schema.json              the same, machine readable
    COMMENT ON statements         for psql \\d+, DBeaver and agents that introspect (migrations 013 and 190, or a refresh migration)

Usage (DATABASE_URL = any database with every migration applied, e.g. a local scratch DB; the production DB works too, read-only):
    python scripts/gen_schema_doc.py --write                     regenerate docs/DATA_SCHEMA.md and docs/schema.json
    python scripts/gen_schema_doc.py --check                     exit 1 when those files are stale (the test suite does the same)
    python scripts/gen_schema_doc.py --problems                  list undescribed / stale entries and exit 1 if any
    python scripts/gen_schema_doc.py --catalog-sql               CREATE OR REPLACE VIEW statements for analytics.catalog and analytics.metric_glossary (needs no database);
                                                                 for a METRICS / domain / description change after migration 230 was applied write them (with `--comments 230`) to a NEW migration
    python scripts/gen_schema_doc.py --comments 013|015|190|191|230|all [--out FILE]
                                                                 COMMENT ON statements; for a description change after migrations 013 / 190 were applied
                                                                 write them to a NEW migration (migrations/NNN_schema_comments_<what>.sql)
    python scripts/gen_schema_doc.py --emit-migration 013|015|190|191|230  rewrite the comment section of migrations/013_schema_comments_analytics.sql, 015_creative_stage_moves.sql, 190_analytics_pacing_views.sql, 191_schema_comments_proj.sql
                                                                 (only before the migration was applied anywhere: applied migrations are immutable;
                                                                 013 keeps its hand-written DDL and only its comment section is rewritten)
    python scripts/gen_schema_doc.py --verify-live               compare docs/schema.json with the database: every documented column exists with the stated type
                                                                 (opens a READ ONLY session; run it against production)
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent.parent
DESC_PATH = ROOT / "docs" / "schema" / "descriptions.py"
COMMENTS_MARKER = "-- ---------------------------------------------------------------- comments\n"
MD_PATH = ROOT / "docs" / "DATA_SCHEMA.md"
JSON_PATH = ROOT / "docs" / "schema.json"
MIGRATIONS = {"013": ROOT / "migrations" / "013_schema_comments_analytics.sql", "015": ROOT / "migrations" / "015_creative_stage_moves.sql", "016": ROOT / "migrations" / "016_creative_stage_moves_tables.sql",
              "190": ROOT / "migrations" / "190_analytics_pacing_views.sql", "191": ROOT / "migrations" / "191_schema_comments_proj.sql",
              "192": ROOT / "migrations" / "192_proj_live_today.sql", "193": ROOT / "migrations" / "193_scaling_benchmarks.sql",
              "196": ROOT / "migrations" / "196_creative_priorities.sql", "197": ROOT / "migrations" / "197_alert_state.sql", "199": ROOT / "migrations" / "199_priorities_manual.sql",
              "202": ROOT / "migrations" / "202_priorities_assignments.sql", "203": ROOT / "migrations" / "203_creative_launch.sql",
              "204": ROOT / "migrations" / "204_assignment_format.sql", "205": ROOT / "migrations" / "205_api_keys.sql",
              "206": ROOT / "migrations" / "206_klaviyo_metrics.sql", "207": ROOT / "migrations" / "207_landing_pages.sql",
              "210": ROOT / "migrations" / "210_shopify_orders.sql", "220": ROOT / "migrations" / "220_brain_mcp.sql", "221": ROOT / "migrations" / "221_brain_open_app.sql",
              "230": ROOT / "migrations" / "230_analytics_brain_views.sql", "240": ROOT / "migrations" / "240_meta_breakdowns.sql"}
# the Test to scale views (created by migration 015, after 013 runs on a fresh database): they and their comments live in migration 015
# (migration 016 turned them into tables and added the placements tables; 015's own comment section is frozen history)
LATE_STAGE_MOVES = {f"public.{n}{b}" for n in ("creative_stage_moves", "creative_scaling_placements") for b in ("", "_upstack_nc", "_upstack_all")}
# analytics views that read proj_feed_* (created by migration 170, after 013 runs on a fresh database): they and their comments live in migration 190
LATE_ANALYTICS = {"analytics.meta_reach", "analytics.meta_campaign_daily_history", "analytics.data_coverage"}
# the hourly live-today snapshot table (created by migration 192, after 191 ran): it and its comments live in migration 192
LATE_LIVE = {"public.proj_live_today_snapshots"}
# the scaling benchmark table of the Test to scale views (created by migration 193): it and its comments live in migration 193
LATE_BENCH = {"public.scaling_benchmarks"}
# the weekly creative priorities snapshots (created by migration 196): it and its comments live in migration 196
LATE_PRIO = {"public.creative_priorities_snapshots"}
# the alert de-duplication state (created by migration 197): it and its comments live in migration 197
LATE_ALERTS = {"public.alert_state"}
# the manual /priorities concepts (created by migration 199): it and its comments live in migration 199
LATE_MANUAL = {"public.creative_priorities_manual"}
# the production assignments and vendor links of /priorities (created by migration 202): they and their comments live in migration 202
LATE_ASSIGN = {"public.creative_priorities_assignments", "public.creative_priorities_vendor_links"}
LATE_LAUNCH = {f"public.creative_launch_{n}" for n in ("settings", "pages", "vendor_prefixes", "drafts", "jobs", "ads", "adsets", "audit")}
# the creative formats list of the production assignments (created by migration 204): it and its comments live in migration 204 (the format_code column comment on the assignments table is written by hand there; 202 is frozen)
LATE_FORMATS = {"public.creative_formats"}
# the scoped API keys (created by migration 205): the table and its comments live in migration 205
LATE_APIKEYS = {"public.api_keys"}
# the order-level Shopify orders and lines of the last 120 days (created by migration 210): the tables and their comments live in migration 210
LATE_ORDERS120 = {"public.shopify_orders", "public.shopify_order_lines"}
# the Klaviyo flow and campaign tables (created by migration 206): they and their comments live in migration 206
LATE_KLAVIYO = {"public.klaviyo_campaigns", "public.klaviyo_flows", "public.klaviyo_flow_messages", "public.klaviyo_daily", "public.klaviyo_message_content", "public.klaviyo_message_performance"}
# the landing page copy store and ad to page map (created by migration 207): tables, the top-ads view and their comments live in migration 207
LATE_LANDING = {f"public.{n}" for n in ("landing_pages", "landing_page_versions", "landing_page_ads", "landing_page_ad_checks", "landing_page_top_ads")}
# the hosted brain endpoint's agent keys and audit log (created by migration 220, reworked by 221 for the open app): their comments live in migration 221 (220's comment section is frozen history)
LATE_BRAIN = {"public.brain_keys", "public.brain_audit"}
# the Meta breakdown pull (created by migration 240): the three tables, the analytics view and their comments live in migration 240
LATE_BREAKDOWNS = {"public.meta_breakdown_daily", "public.meta_breakdown_coverage", "public.meta_breakdown_progress", "analytics.meta_breakdown_daily"}
PROJ_GROUPS = {"proj_feeds", "proj_engine"}
SCHEMAS = ("public", "analytics")
KIND = {"r": "table", "p": "table", "v": "view", "m": "view"}
REQUIRED_FIELDS = ("purpose", "grain", "source", "refresh", "coverage")
SHORT_TYPES = {"timestamp with time zone": "timestamptz", "timestamp without time zone": "timestamp", "character varying": "varchar", "double precision": "float8"}


# ------------------------------------------------------------------------------------------------------------------ descriptions
def load_descriptions():
    spec = importlib.util.spec_from_file_location("lonvera_schema_descriptions", DESC_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def resolve_cols(key: str, rel: dict, _seen: tuple = ()) -> dict[str, str]:
    """Column descriptions of a relation: those inherited through `like` (recursively), overridden by its own."""
    if key in _seen:
        raise ValueError(f"circular 'like' at {key}")
    spec = rel[key]
    cols: dict[str, str] = {}
    if spec.get("like"):
        cols.update(resolve_cols(spec["like"], rel, _seen + (key,)))
    cols.update(spec.get("cols") or {})
    return cols


# ------------------------------------------------------------------------------------------------------------------ introspection
def introspect(conn) -> dict[str, dict]:
    """{'schema.name': {schema, name, kind, columns: [{name, type, nullable}], primary_key: [..], foreign_keys: [..]}} for the public and analytics schemas."""
    rels: dict[str, dict] = {}
    for sch, name, relkind, attname, typ, notnull in conn.execute(
            """SELECT n.nspname, c.relname, c.relkind, a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull
               FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
               JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
               WHERE n.nspname = ANY(%s) AND c.relkind IN ('r', 'p', 'v', 'm') AND NOT c.relispartition        -- partitions of a documented table are not separate relations
               ORDER BY n.nspname, c.relname, a.attnum""", (list(SCHEMAS),)).fetchall():
        r = rels.setdefault(f"{sch}.{name}", {"schema": sch, "name": name, "kind": KIND[relkind], "columns": [], "primary_key": [], "foreign_keys": []})
        r["columns"].append({"name": attname, "type": typ, "nullable": (not notnull) if r["kind"] == "table" else None})
    for sch, name, cols in conn.execute(
            """SELECT n.nspname, c.relname, array_agg(a.attname ORDER BY k.ord)
               FROM pg_index i JOIN pg_class c ON c.oid = i.indrelid JOIN pg_namespace n ON n.oid = c.relnamespace
               CROSS JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS k(attnum, ord)
               JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = k.attnum
               WHERE i.indisprimary AND n.nspname = ANY(%s) GROUP BY 1, 2""", (list(SCHEMAS),)).fetchall():
        if f"{sch}.{name}" in rels:
            rels[f"{sch}.{name}"]["primary_key"] = list(cols)
    for sch, name, fcols, rsch, rname, rcols in conn.execute(
            """SELECT n.nspname, c.relname,
                      (SELECT array_agg(a.attname ORDER BY x.ord) FROM unnest(k.conkey) WITH ORDINALITY x(attnum, ord) JOIN pg_attribute a ON a.attrelid = k.conrelid AND a.attnum = x.attnum),
                      rn.nspname, rc.relname,
                      (SELECT array_agg(a.attname ORDER BY x.ord) FROM unnest(k.confkey) WITH ORDINALITY x(attnum, ord) JOIN pg_attribute a ON a.attrelid = k.confrelid AND a.attnum = x.attnum)
               FROM pg_constraint k JOIN pg_class c ON c.oid = k.conrelid JOIN pg_namespace n ON n.oid = c.relnamespace
               JOIN pg_class rc ON rc.oid = k.confrelid JOIN pg_namespace rn ON rn.oid = rc.relnamespace
               WHERE k.contype = 'f' AND n.nspname = ANY(%s) ORDER BY 1, 2, 3""", (list(SCHEMAS),)).fetchall():
        if f"{sch}.{name}" in rels:
            rels[f"{sch}.{name}"]["foreign_keys"].append({"columns": list(fcols), "references": f"{rsch}.{rname}", "referenced_columns": list(rcols)})
    return rels


# ------------------------------------------------------------------------------------------------------------------ coverage check
def problems(live: dict[str, dict], d=None, require_analytics: bool = True) -> list[str]:
    """Everything that makes the dictionary lie or lag: an undescribed table / view / column, a description of something that no longer exists,
    a missing required field, an analytics view without examples. Empty list = the dictionary is complete."""
    d = d or load_descriptions()
    out: list[str] = []
    group_keys = {g for g, _ in d.GROUPS}
    for key in sorted(live):
        if key in d.EXCLUDED:
            continue
        if key not in d.REL:
            out.append(f"{key}: exists in the database but has no entry in docs/schema/descriptions.py (describe it, or add it to EXCLUDED with a reason)")
    for key, spec in d.REL.items():
        sch, _, name = key.partition(".")
        if sch not in SCHEMAS:
            out.append(f"{key}: schema must be one of {SCHEMAS}")
            continue
        if key in d.EXCLUDED:
            out.append(f"{key}: is both described and EXCLUDED")
        if key not in live:
            if sch == "analytics" and (not require_analytics or key in getattr(d, "DEFERRED", ())):
                continue                    # an analytics view that migration 231 creates only when its source table exists
            out.append(f"{key}: described in docs/schema/descriptions.py but it does not exist in the database (stale entry)")
            continue
        r = live[key]
        if spec.get("kind") != r["kind"]:
            out.append(f"{key}: described as a {spec.get('kind')} but it is a {r['kind']}")
        if spec.get("group") not in group_keys:
            out.append(f"{key}: unknown group {spec.get('group')!r}")
        for f in REQUIRED_FIELDS:
            if not str(spec.get(f) or "").strip():
                out.append(f"{key}: missing required field '{f}'")
        if sch == "analytics" and len(spec.get("examples") or []) < 2:
            out.append(f"{key}: an analytics view needs at least 2 example queries")
        if sch == "analytics":
            domains = {k for k, _ in getattr(d, "DOMAINS", [])}
            if spec.get("domain") not in domains:
                out.append(f"{key}: an analytics view needs a domain from DOMAINS (got {spec.get('domain')!r})")
            if not str(spec.get("purpose") or "").strip():
                out.append(f"{key}: an analytics view needs a description (purpose)")
            for t in [spec["span"][0]] if spec.get("span") else []:
                if f"public.{t}" not in live:
                    out.append(f"{key}: span table public.{t} does not exist")
            if spec.get("base") and f"public.{spec['base']}" not in live:
                out.append(f"{key}: base table public.{spec['base']} does not exist")
        try:
            cols = resolve_cols(key, d.REL)
        except (KeyError, ValueError) as e:
            out.append(f"{key}: bad 'like' ({e})")
            continue
        live_cols = [c["name"] for c in r["columns"]]
        for c in live_cols:
            if not str(cols.get(c) or "").strip():
                out.append(f"{key}.{c}: column has no description")
        for c in (spec.get("cols") or {}):
            if c not in live_cols:
                out.append(f"{key}.{c}: described but the column does not exist (stale)")
    out += metrics_problems(live, d)
    for key, why in d.EXCLUDED.items():
        if not why.strip():
            out.append(f"{key}: EXCLUDED needs a reason")
        if key.startswith("analytics."):
            out.append(f"{key}: nothing in analytics may be EXCLUDED")
    return out


def metrics_problems(live: dict[str, dict], d) -> list[str]:
    """The contract of the METRICS glossary (a tool server reads this exact shape) and that every `source` names a real analytics view and column."""
    out: list[str] = []
    metrics = getattr(d, "METRICS", None)
    if metrics is None:
        return out
    fields = tuple(d.METRIC_FIELDS)
    domains = {k for k, _ in d.DOMAINS}
    seen: set[str] = set()
    for m in metrics:
        name = m.get("name")
        if set(m) != set(fields):
            out.append(f"metric {name!r}: fields must be exactly {fields} (got {sorted(m)})")
            continue
        if name in seen or not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", name):
            out.append(f"metric {name!r}: name must be unique snake_case")
        seen.add(name)
        for f in ("label", "formula", "source", "default_basis"):
            if not isinstance(m[f], str) or not m[f].strip():
                out.append(f"metric {name}: {f} must be a non-empty string")
        for f in ("other_bases", "traps"):
            if not isinstance(m[f], list) or not m[f] or not all(isinstance(x, str) and x.strip() for x in m[f]):
                out.append(f"metric {name}: {f} must be a non-empty list of strings")
        if m["domain"] not in domains:
            out.append(f"metric {name}: unknown domain {m['domain']!r}")
        for part in str(m["source"]).split(";"):
            bits = part.strip().split(".")
            if len(bits) != 3 or bits[0] != "analytics":
                out.append(f"metric {name}: source part {part.strip()!r} is not analytics.<view>.<column(s)>")
                continue
            cols = {c["name"] for c in live.get(f"analytics.{bits[1]}", {}).get("columns", [])}
            if not cols:
                out.append(f"metric {name}: source view analytics.{bits[1]} does not exist")
                continue
            for c in bits[2].split(","):
                if c.strip() not in cols:
                    out.append(f"metric {name}: analytics.{bits[1]} has no column {c.strip()!r}")
    return out


# ------------------------------------------------------------------------------------------------------------------ model
def build_model(live: dict[str, dict], d=None) -> dict:
    d = d or load_descriptions()
    relations = []
    for key, spec in d.REL.items():
        if key not in live:
            continue
        r = live[key]
        cols = resolve_cols(key, d.REL)
        relations.append({
            "name": key, "schema": r["schema"], "relation": r["name"], "kind": r["kind"], "group": spec["group"],
            "purpose": spec["purpose"], "grain": spec["grain"], "primary_key": r["primary_key"], "foreign_keys": r["foreign_keys"],
            "source": spec["source"], "refresh": spec["refresh"], "coverage": spec["coverage"], "caveats": list(spec.get("caveats") or []),
            "like": spec.get("like"),
            "domain": spec.get("domain"),
            "columns": [{"name": c["name"], "type": c["type"], "nullable": c["nullable"], "description": cols.get(c["name"], "")} for c in r["columns"]],
            "examples": [{"title": t, "sql": s} for t, s in (spec.get("examples") or [])],
        })
    return {"groups": [{"key": k, "title": t} for k, t in d.GROUPS], "domains": [{"key": k, "title": t} for k, t in getattr(d, "DOMAINS", [])], "relations": relations,
            "excluded": [{"name": k, "reason": v} for k, v in d.EXCLUDED.items()],
            "metrics": [{f: m[f] for f in d.METRIC_FIELDS} for m in getattr(d, "METRICS", [])]}


def render_json(model: dict) -> str:
    return json.dumps(model, indent=1, ensure_ascii=False) + "\n"


def _cell(s) -> str:
    return str(s if s is not None else "").replace("|", "\\|").replace("\n", " ").strip()


def _short(t: str) -> str:
    return SHORT_TYPES.get(t, t)


def render_markdown(model: dict, d=None) -> str:
    d = d or load_descriptions()
    out = [d.INTRO_MD.rstrip("\n"), "", d.TABLE_REFERENCE_INTRO_MD.rstrip("\n"), ""]
    out.append("| Group | Relations |\n|---|---|")
    for g in model["groups"]:
        names = [r["name"] for r in model["relations"] if r["group"] == g["key"]]
        out.append(f"| {_cell(g['title'])} | " + ", ".join(f"[`{n}`](#{_anchor(n)})" for n in names) + " |")
    out.append("")
    if model.get("domains"):
        out.append("| Domain | Analytics views |\n|---|---|")
        for dm in model["domains"]:
            names = [r["name"] for r in model["relations"] if r.get("domain") == dm["key"]]
            out.append(f"| **{dm['key']}**: {_cell(dm['title'])} | " + ", ".join(f"[`{n.split('.')[1]}`](#{_anchor(n)})" for n in names) + " |")
        out.append("")
    for g in model["groups"]:
        rels = [r for r in model["relations"] if r["group"] == g["key"]]
        if not rels:
            continue
        out += [f"## {g['title']}", ""]
        for r in rels:
            out.append(f"### {r['name']}")
            out.append("")
            out.append(f"*{r['kind']}*" + (f", columns like `{r['like']}`" if r.get("like") else ""))
            out.append("")
            out.append(f"**Purpose.** {r['purpose']}")
            out.append("")
            key = f"primary key `({', '.join(r['primary_key'])})`; " if r["primary_key"] else ""
            if r.get("domain"):
                out.append(f"- **Domain:** {r['domain']}")
            out.append(f"- **Grain:** {key}{r['grain']}")
            if r["foreign_keys"]:
                out.append("- **References:** " + "; ".join(f"`({', '.join(f['columns'])})` -> `{f['references']}({', '.join(f['referenced_columns'])})`" for f in r["foreign_keys"]))
            out.append(f"- **Source:** {r['source']}")
            out.append(f"- **Refresh:** {r['refresh']}")
            out.append(f"- **Coverage:** {r['coverage']}")
            for c in r["caveats"]:
                out.append(f"- **Caveat:** {c}")
            out.append("")
            out.append("| Column | Type | Null | Meaning |\n|---|---|---|---|")
            for c in r["columns"]:
                nul = "" if c["nullable"] is None else ("yes" if c["nullable"] else "no")
                out.append(f"| `{c['name']}` | {_cell(_short(c['type']))} | {nul} | {_cell(c['description'])} |")
            out.append("")
            for ex in r["examples"]:
                out.append(f"*{ex['title']}*")
                out.append("")
                out.append("```sql")
                out.append(ex["sql"])
                out.append("```")
                out.append("")
            if not r["examples"] and r.get("like"):
                out.append(f"*Examples: same shape as `{r['like']}`; query it the same way.*")
                out.append("")
    if model.get("metrics"):
        out += ["## Metrics", "", d.METRICS_INTRO_MD.rstrip("\n"), ""]
        for m in model["metrics"]:
            out.append(f"### {m['name']}")
            out.append("")
            out.append(f"**{m['label']}** (domain: {m['domain']})")
            out.append("")
            out.append(f"- **Formula:** {m['formula']}")
            out.append(f"- **Source:** `{m['source']}`")
            out.append(f"- **Default basis:** {m['default_basis']}")
            out += [f"- **Other basis:** {b}" for b in m["other_bases"]]
            out += [f"- **Trap:** {t}" for t in m["traps"]]
            out.append("")
    out.append("## Regenerating")
    out.append("")
    out.append("Edit `docs/schema/descriptions.py`, then `python scripts/gen_schema_doc.py --write` (against a migrated database). "
               "`tests/api/test_schema_docs.py` fails when a table, view or column has no description, when a description names something that no longer exists, "
               "when an analytics example query does not run, and when this file or `docs/schema.json` is stale.")
    return "\n".join(out).rstrip("\n") + "\n"


def _anchor(name: str) -> str:
    return name.lower().replace(".", "")


# ------------------------------------------------------------------------------------------------------------------ comments
def relation_comment(r: dict) -> str:
    parts = [r["purpose"].strip(), f"Grain: {r['grain'].strip()}.", f"Source: {r['source'].strip()}.", f"Refresh: {r['refresh'].strip()}.", f"Coverage: {r['coverage'].strip()}."]
    if r["caveats"]:
        parts.append("Caveats: " + " | ".join(c.strip() for c in r["caveats"]))
    return " ".join(parts)


def expected_comments(model: dict, d=None) -> dict[tuple[str, str | None], str]:
    """{(relation key, column or None): comment text} for every described relation."""
    d = d or load_descriptions()
    out: dict[tuple[str, str | None], str] = {("analytics", None): d.ANALYTICS_SCHEMA_COMMENT}
    for r in model["relations"]:
        out[(r["name"], None)] = relation_comment(r)
        for c in r["columns"]:
            out[(r["name"], c["name"])] = c["description"]
    return out


def _lit(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def scope_of(r: dict, d=None) -> str:
    """Which migration carries the comments of a relation: 013 (marketing + core analytics), 190 (analytics views over proj feeds), 191 (proj_* tables),
    230 (the analytics views of migration 230, and the relations whose text 230 re-writes)."""
    d = d or load_descriptions()
    if r["name"] in getattr(d, "SCOPE_230", ()):
        return "230"
    if r["name"] in LATE_ANALYTICS:
        return "190"
    if r["name"] in LATE_STAGE_MOVES:
        return "016"
    if r["name"] in LATE_LIVE:
        return "192"
    if r["name"] in LATE_BENCH:
        return "193"
    if r["name"] in LATE_PRIO:
        return "196"
    if r["name"] in LATE_ALERTS:
        return "197"
    if r["name"] in LATE_MANUAL:
        return "199"
    if r["name"] in LATE_ASSIGN:
        return "202"
    if r["name"] in LATE_LAUNCH:
        return "203"
    if r["name"] in LATE_FORMATS:
        return "204"
    if r["name"] in LATE_APIKEYS:
        return "205"
    if r["name"] in LATE_ORDERS120:
        return "210"
    if r["name"] in LATE_KLAVIYO:
        return "206"
    if r["name"] in LATE_LANDING:
        return "207"
    if r["name"] in LATE_BRAIN:
        return "221"
    if r["name"] in LATE_BREAKDOWNS:
        return "240"
    return "191" if r["group"] in PROJ_GROUPS else "013"


def comment_sql(model: dict, scope: str = "all", d=None) -> str:
    """COMMENT ON statements for one migration's scope ('013' | '190' | '191') or everything ('all')."""
    d = d or load_descriptions()
    lines = []
    if scope in ("230", "all"):
        lines.append(f"COMMENT ON SCHEMA analytics IS {_lit(d.ANALYTICS_SCHEMA_COMMENT)};")
    recomment = getattr(d, "RECOMMENT", {})
    for r in model["relations"]:
        if scope != "all" and scope_of(r, d) != scope:
            continue
        obj = "VIEW" if r["kind"] == "view" else "TABLE"
        lines.append(f"COMMENT ON {obj} {r['schema']}.{r['relation']} IS {_lit(relation_comment(r))};")
        if recomment.get(r["name"]) == "relation":              # an existing relation whose relation-level text changed: its column comments are unchanged
            continue
        for c in r["columns"]:
            lines.append(f"COMMENT ON COLUMN {r['schema']}.{r['relation']}.{c['name']} IS {_lit(c['description'])};")
    return "\n".join(lines) + "\n"


def _first_last(d, spec: dict) -> tuple[str, str]:
    sp = spec.get("span")
    if not sp:
        return "NULL::date", "NULL::date"
    table, first = sp[0], sp[1]
    last = sp[2] if len(sp) > 2 else sp[1]
    return f"(SELECT min({first}) FROM public.{table})::date", f"(SELECT max({last}) FROM public.{table})::date"


def catalog_sql(d=None) -> str:
    """CREATE OR REPLACE VIEW statements for analytics.catalog and analytics.metric_glossary (the METRICS list); both are generated from docs/schema/descriptions.py and need no database.

    analytics.catalog reads the live metadata (every view in the analytics schema, at query time) and joins what descriptions.py knows about each view: domain, description, grain and the
    first / last day of its base table. A view that no migration has told the catalog about still appears, with domain 'unassigned', its COMMENT's first part as description and no days, so a
    view added by a later migration is listed without touching this one. Views that may not exist yet (migration 231's, and the ones in descriptions.EXTRA_DOMAINS) carry only domain /
    description / grain here (no day lookups, because their base tables may be missing); a view that is not in the schema yet simply does not match any live row."""
    d = d or load_descriptions()
    rows = []
    deferred = set(getattr(d, "DEFERRED", ()))
    for key, spec in d.REL.items():
        if not key.startswith("analytics."):
            continue
        name = key.split(".", 1)[1]
        if key in deferred:
            first, last, est = "NULL::date", "NULL::date", "NULL::bigint"
        else:
            first, last = _first_last(d, spec)
            est = (f"(SELECT NULLIF(c.reltuples, -1)::bigint FROM pg_class c WHERE c.oid = to_regclass('public.{spec['base']}'))" if spec.get("base") else "NULL::bigint")
        rows.append(f"SELECT {_lit(name)}::text AS view_name, {_lit(spec['domain'])}::text AS domain, {_lit(spec['purpose'].strip())}::text AS description, "
                    f"{_lit(spec['grain'].strip())}::text AS grain, {first} AS first_day, {last} AS last_day, {est} AS row_estimate")
    for name, domain in sorted(getattr(d, "EXTRA_DOMAINS", {}).items()):
        rows.append(f"SELECT {_lit(name)}::text, {_lit(domain)}::text, NULL::text, NULL::text, NULL::date, NULL::date, NULL::bigint")
    out = ["CREATE OR REPLACE VIEW analytics.catalog AS\n"
           "SELECT c.relname::text AS view_name,\n"
           "       COALESCE(k.domain, 'unassigned') AS domain,\n"
           "       COALESCE(k.description, split_part(obj_description(c.oid, 'pg_class'), ' Grain: ', 1)) AS description,\n"
           "       k.grain,\n"
           "       k.first_day,\n"
           "       k.last_day,\n"
           "       COALESCE(k.row_estimate, (SELECT NULLIF(t.reltuples, -1)::bigint FROM pg_class t WHERE t.oid = to_regclass('public.' || c.relname))) AS row_estimate\n"
           "FROM pg_class c\n"
           "JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'analytics'\n"
           "LEFT JOIN (\n" + "\nUNION ALL\n".join(rows) + "\n) k(view_name, domain, description, grain, first_day, last_day, row_estimate) ON k.view_name = c.relname\n"
           "WHERE c.relkind = 'v';"]

    def arr(xs):
        return "ARRAY[" + ", ".join(_lit(x) for x in xs) + "]::text[]"
    vals = ",\n".join("  (" + ", ".join([_lit(m["name"]), _lit(m["label"]), _lit(m["formula"]), _lit(m["source"]), _lit(m["default_basis"]),
                                        arr(m["other_bases"]), arr(m["traps"]), _lit(m["domain"])]) + ")" for m in d.METRICS)
    out.append("CREATE OR REPLACE VIEW analytics.metric_glossary AS\nSELECT g.name::text, g.label::text, g.formula::text, g.source::text, g.default_basis::text, g.other_bases, g.traps, g.domain::text\n"
               "FROM (VALUES\n" + vals + "\n) AS g(name, label, formula, source, default_basis, other_bases, traps, domain);")
    return "\n\n".join(out) + "\n"


def migration_text(which: str, model: dict, d=None) -> str:
    """013, 015 and 190 keep their hand-written DDL (everything above COMMENTS_MARKER) and get the comment section rewritten; 191 is comments only."""
    if which in ("013", "015", "016", "190", "192", "193", "196", "197", "199", "202", "203", "204", "205", "206", "207", "210", "220", "221", "230", "240"):
        path = MIGRATIONS[which]
        if not path.exists():
            raise SystemExit(f"{path} must exist with its hand-written DDL and the comments marker before comments can be emitted")
        head = path.read_text().split(COMMENTS_MARKER, 1)[0] + COMMENTS_MARKER
        if which == "230":                                         # the catalog and the glossary are generated, so they sit below the marker, before their comments
            head += "-- analytics.catalog and analytics.metric_glossary: generated from docs/schema/descriptions.py (domains, descriptions, grains, METRICS)\n" + catalog_sql(d) + "\n"
        return head + comment_sql(model, which, d)
    if which == "191":
        head = ("-- 191: COMMENT ON for the projection tables (proj_*), which migrations 100-180 create after 013 runs.\n"
                "-- GENERATED by `python scripts/gen_schema_doc.py --emit-migration 191` from docs/schema/descriptions.py. Idempotent; immutable once applied.\n\n")
        return head + comment_sql(model, "191", d)
    raise ValueError(which)


# ------------------------------------------------------------------------------------------------------------------ live verification
def verify_live(conn, schema_json: dict) -> list[str]:
    """docs/schema.json vs a live database (production, read-only): every documented relation / column exists with the documented type.
    Returns the differences; analytics views that the database does not have yet (migrations 013 / 190 not applied) come back as 'NOTE:' lines, which are not differences."""
    live = introspect(conn)
    out = []
    for r in schema_json["relations"]:
        if r["schema"] == "analytics" and r["name"] not in live:
            out.append(f"NOTE: {r['name']}: not in this database yet (migrations 013 / 190 not applied)")
            continue
        lr = live.get(r["name"])
        if lr is None:
            out.append(f"{r['name']}: missing in the database")
            continue
        if lr["kind"] != r["kind"]:
            out.append(f"{r['name']}: documented as {r['kind']}, database has a {lr['kind']}")
        have = {c["name"]: c for c in lr["columns"]}
        for c in r["columns"]:
            lc = have.get(c["name"])
            if lc is None:
                out.append(f"{r['name']}.{c['name']}: documented, missing in the database")
            elif lc["type"] != c["type"]:
                out.append(f"{r['name']}.{c['name']}: documented {c['type']}, database has {lc['type']}")
            elif c["nullable"] is not None and lc["nullable"] != c["nullable"]:
                out.append(f"{r['name']}.{c['name']}: nullability differs (documented {c['nullable']}, database {lc['nullable']})")
        for c in lr["columns"]:
            if c["name"] not in {x["name"] for x in r["columns"]}:
                out.append(f"{r['name']}.{c['name']}: in the database, not documented")
    documented = {r["name"] for r in schema_json["relations"]} | {e["name"] for e in schema_json["excluded"]}
    for key in sorted(live):
        if key not in documented:
            out.append(f"{key}: in the database, not documented")
    return out


# ------------------------------------------------------------------------------------------------------------------ cli
def connect(url: str | None):
    url = url or os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL is not set (point it at a database with every migration applied)")
    return psycopg.connect(url, autocommit=True)


def generate(conn) -> tuple[str, str, dict]:
    d = load_descriptions()
    live = introspect(conn)
    model = build_model(live, d)
    return render_markdown(model, d), render_json(model), model


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--database-url")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--write", action="store_true")
    g.add_argument("--check", action="store_true")
    g.add_argument("--problems", action="store_true")
    g.add_argument("--comments", choices=("013", "015", "016", "190", "191", "192", "193", "196", "197", "199", "202", "203", "204", "205", "206", "207", "210", "220", "221", "230", "240", "all"))
    g.add_argument("--catalog-sql", action="store_true")
    g.add_argument("--emit-migration", choices=sorted(MIGRATIONS))
    g.add_argument("--verify-live", action="store_true")
    ap.add_argument("--out", help="with --comments: write to this file instead of stdout")
    a = ap.parse_args(argv)
    if a.catalog_sql:
        sys.stdout.write(catalog_sql())
        return 0
    conn = connect(a.database_url)
    try:
        if a.verify_live:
            conn.execute("SET default_transaction_read_only = on")
            bad = verify_live(conn, json.loads(JSON_PATH.read_text()))
            for b in bad:
                print(b)
            diffs = [b for b in bad if not b.startswith("NOTE:")]
            checked = len(json.loads(JSON_PATH.read_text())["relations"])
            print(f"verify-live: {len(diffs)} difference(s) over {checked} documented relations ({len(bad) - len(diffs)} analytics view(s) not applied yet)")
            return 1 if diffs else 0
        d = load_descriptions()
        live = introspect(conn)
        probs = problems(live, d)
        if a.problems or probs:
            for p in probs:
                print("PROBLEM:", p)
            if a.problems:
                print(f"{len(probs)} problem(s)")
                return 1 if probs else 0
            print(f"{len(probs)} problem(s): refusing to generate", file=sys.stderr)
            return 1
        model = build_model(live, d)
        md, js = render_markdown(model, d), render_json(model)
        if a.comments:
            sql = comment_sql(model, a.comments, d)
            if a.out:
                Path(a.out).write_text(sql)
            else:
                sys.stdout.write(sql)
            return 0
        if a.emit_migration:
            MIGRATIONS[a.emit_migration].write_text(migration_text(a.emit_migration, model, d))
            print("wrote", MIGRATIONS[a.emit_migration].relative_to(ROOT))
            return 0
        if a.check:
            stale = [p.name for p, text in ((MD_PATH, md), (JSON_PATH, js)) if not p.exists() or p.read_text() != text]
            if stale:
                print("STALE:", ", ".join(stale), "- run: python scripts/gen_schema_doc.py --write")
                return 1
            print("docs/DATA_SCHEMA.md and docs/schema.json are up to date")
            return 0
        MD_PATH.write_text(md)
        JSON_PATH.write_text(js)
        print(f"wrote {MD_PATH.relative_to(ROOT)} ({len(md.splitlines())} lines) and {JSON_PATH.relative_to(ROOT)} ({len(model['relations'])} relations)")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
