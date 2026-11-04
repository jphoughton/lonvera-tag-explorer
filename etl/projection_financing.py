"""Seed the financing module's "Current plan" from the frozen sheet snapshot, and emit migration 160's SQL.

The sheet's typed cells become levers (`params['financing']`) and monthly series (`financing.*`) of the `current` scenario:

    python -m etl.projection_financing --check          # print what the snapshot holds, compare with the code defaults
    python -m etl.projection_financing --sql            # print the SQL that migrations/160_proj_financing.sql contains
    python -m etl.projection_financing --apply          # write the seed into DATABASE_URL (idempotent: never overwrites an existing financing namespace or cell)

Source cells: Term Debt Monthly Paydown B4:B6 and E10:E58, P&L W108 / X110 / row 120, Payback B7 B8 D11 B12, Spend-ROAS Matrix B4 B7,
P&L F180:F183 G186:G187 G194:I202 G205:G206 J205:J206 I208.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts" / "projection") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts" / "projection"))

from app.projection.financing import params as P            # noqa: E402
from app.projection.interfaces import MONTHS, add_months    # noqa: E402

PL = "P&L"
TERM = "Term Debt Monthly Paydown"
CLOSE = date(2026, 9, 1)


def _sr():
    import snapshot_read
    return snapshot_read


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def term_debt_terms() -> dict:
    sr = _sr()
    return {"principal": float(sr.cell(TERM, "B4")), "rate": float(sr.cell(TERM, "B5")), "fixed_payment": float(sr.cell(TERM, "B6"))}


def typed_principal() -> dict[date, float]:
    """Typed principal paid by month (Term Debt Monthly Paydown column E; row 10 = the close month)."""
    sr = _sr()
    out: dict[date, float] = {}
    for i, v in enumerate(sr.col(TERM, "E", 10, 58)):
        m = add_months(CLOSE, i)
        if _num(v) and m in MONTHS:
            out[m] = float(v)
    return out


def _pl_months() -> dict[str, date]:
    sr = _sr()
    hdr = sr.row(PL, 8, "C", "BJ")
    return {sr.col_letter(3 + i): sr.serial_to_date(v) for i, v in enumerate(hdr) if _num(v)}


def tax_payments() -> tuple[float, list[dict]]:
    """Tax rate and payment months from P&L row 120's typed formulas. The FY roll-up columns BM..BP hold the NOI each payment taxes."""
    sr = _sr()
    months = _pl_months()
    basis = {"BM": (date(2026, 9, 1), date(2026, 12, 1), "Sep-Dec 2026"), "BN": (date(2027, 1, 1), date(2027, 12, 1), "2027"),
             "BO": (date(2028, 1, 1), date(2028, 12, 1), "2028"), "BP": (date(2029, 1, 1), date(2029, 12, 1), "2029")}
    rate = None
    pays = []
    for col, m in months.items():
        f = sr.cell(PL, f"{col}120", "formulas")
        if not (isinstance(f, str) and f.startswith("=")):
            continue
        ref = re.search(r"\b(B[M-P])100\b", f)
        r = re.search(r"\*\s*(0?\.\d+)\s*$", f)
        if ref and r:
            rate = float(r.group(1))
            lo, hi, label = basis[ref.group(1)]
            pays.append({"month": m.isoformat(), "from": lo.isoformat(), "to": hi.isoformat(), "label": label})
    return (rate if rate is not None else 0.21), pays


def payout_columns() -> list[dict]:
    """Bonus columns of the schedule (P&L G193:I202): first paying tier, its typed bonus and the step-up multipliers read from the formulas."""
    sr = _sr()
    cols = []
    for letter, month in (("G", 4), ("H", 3), ("I", 2)):
        rows = list(range(193, 203))
        first = next(r for r in rows if _num(sr.cell(PL, f"{letter}{r}")) and not str(sr.cell(PL, f"{letter}{r}", "formulas")).startswith("="))
        steps = []
        for r in rows:
            f = sr.cell(PL, f"{letter}{r}", "formulas")
            m = re.match(rf"^={letter}{r - 1}\*([0-9.]+)$", str(f)) if isinstance(f, str) else None
            if m:
                steps.append((float(sr.cell(PL, f"F{r}")), float(m.group(1)) - 1.0))
        up = next(s for scale, s in steps if scale <= 1_000_000)
        above = next(s for scale, s in steps if scale > 1_000_000)
        cols.append({"payback": month, "first_scale": float(sr.cell(PL, f"F{first}")), "first_bonus": float(sr.cell(PL, f"{letter}{first}")),
                     "step_up": round(up, 6), "step_above": round(above, 6)})
    return cols


# Levers added after migration 160 shipped: they live in the code defaults only, so the migration (already applied) never changes.
POST_SEED_LEVERS = (("payback", "loaded_lines"), ("payback", "loaded_months"), ("payback", "loaded_rate"), ("matrix", "margin"))


def seed_params() -> dict:
    """`params['financing']` as the sheet holds it (equal to the code defaults except AOV and CAC, which the sheet reads from pacing)."""
    sr = _sr()
    t = term_debt_terms()
    rate, pays = tax_payments()
    prm = P.deep_merge(P.DEFAULTS, {})
    for group, key in POST_SEED_LEVERS:
        prm[group].pop(key, None)
    prm["debt"].update({"principal": t["principal"], "rate": t["rate"], "fixed_payment": t["fixed_payment"], "mode": "typed"})
    prm["tax"].update({"rate": rate, "payments": pays})
    prm["payback"].update({"aov": float(sr.cell("Payback", "B7")), "cac": float(sr.cell("Payback", "B8")),
                           "product_cost_pct": float(sr.cell("Payback", "D11")), "fulfillment": float(sr.cell("Payback", "B12"))})
    prm["matrix"].update({"target_month": int(sr.cell("Spend-ROAS Matrix", "B4")), "fixed_cost": float(sr.cell("Spend-ROAS Matrix", "B7"))})
    prm["matrix"]["spends"] = [float(sr.cell("Spend-ROAS Matrix", f"A{r}")) for r in range(12, 31)]
    prm["matrix"]["roas"] = [float(v) for v in sr.row("Spend-ROAS Matrix", 11, "B", "N")]
    prm["payout"].update({
        "media_spend": float(sr.cell(PL, "F180")), "cpa": float(sr.cell(PL, "F182")), "aov": float(sr.cell(PL, "F183")),
        "lazar": float(sr.cell(PL, "G205")), "designer": float(sr.cell(PL, "G206")), "samples_count": int(sr.cell(PL, "J205")),
        "sample_cost": float(sr.cell(PL, "J206")), "creative_production_rate": float(sr.cell(PL, "I208")),
        "cogs_rate": float(sr.cell(PL, "G186")), "fulfillment_rate": float(sr.cell(PL, "G187")),
        "scales": [float(sr.cell(PL, f"F{r}")) for r in range(193, 203)], "columns": payout_columns(),
    })
    return prm


def seed_series() -> list[dict]:
    """[{line_key, month, value, kind, note}] for the `current` scenario."""
    sr = _sr()
    out: list[dict] = []
    for i, v in enumerate(sr.col(TERM, "E", 10, 58)):
        m = add_months(CLOSE, i)
        if _num(v) and m in MONTHS:
            out.append({"line_key": P.S_PRINCIPAL, "month": m.isoformat(), "value": float(v), "kind": "input",
                        "note": f"Typed principal paid ({TERM} E{10 + i})"})
    w108 = sr.cell(PL, "W108", "formulas")
    if _num(w108):
        out.append({"line_key": P.S_NOI_BI, "month": CLOSE.isoformat(), "value": float(w108), "kind": "override",
                    "note": "Typed in the sheet (P&L W108): the buyer owns only the part of Sep-26 after close"})
    for col, m in _pl_months().items():
        if m != date(2026, 10, 1):
            continue
        v = sr.cell(PL, f"{col}110", "formulas")
        if _num(v):
            out.append({"line_key": P.S_OPENING, "month": m.isoformat(), "value": float(v), "kind": "override",
                        "note": f"Typed 0 in the sheet (P&L {col}110): Sep-26 ending cash is not carried into Oct-26. Clear this cell to carry it."})
    return out


# ------------------------------------------------------------------------------------------------------------------ SQL / apply

def _q(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def build_sql() -> str:
    prm = seed_params()
    cells = seed_series()
    values = ",\n  ".join(f"({_q(c['line_key'])}, {_q(c['month'])}, {c['value']!r}, {_q(c['kind'])}, {_q(c['note'])})" for c in cells)
    return (
        "-- Financing module (\"Cash & debt\"): seed the Current plan from the sheet's typed cells. Additive and idempotent.\n"
        "-- Levers live in proj_scenarios.params['financing']; monthly cells in proj_series under the 'financing.' prefix.\n"
        "-- No tables: everything a scenario can change is a lever or a series (CONTRACT: data conventions), and nothing here is scenario-independent.\n\n"
        "UPDATE proj_scenarios\n"
        f"   SET params = jsonb_set(params, '{{financing}}', $json${json.dumps(prm, indent=1, sort_keys=True)}$json$::jsonb, true)\n"
        " WHERE slug = 'current' AND NOT (params ? 'financing');\n\n"
        "INSERT INTO proj_series (scenario_id, line_key, month, value, kind, note)\n"
        "SELECT s.id, v.line_key, v.month::date, v.value::numeric, v.kind, v.note\n"
        "  FROM proj_scenarios s,\n"
        "       (VALUES\n  " + values + "\n"
        "       ) AS v(line_key, month, value, kind, note)\n"
        " WHERE s.slug = 'current'\n"
        "   AND NOT EXISTS (SELECT 1 FROM proj_series x WHERE x.scenario_id = s.id AND x.line_key LIKE 'financing.%')\n"
        "ON CONFLICT (scenario_id, line_key, month) DO NOTHING;\n"
    )


SINGLE_STORE_MIGRATION = ROOT / "migrations" / "161_proj_financing_payback_store.sql"


def apply_seed(conn, slug: str = "current") -> dict:
    """Run migrations 160 (the sheet's typed cells) and 161 (one store for the payback inputs) against `conn` (a psycopg connection), so the Current
    plan is what a fresh database holds. Skips whatever is already there."""
    conn.execute(build_sql())
    conn.execute(SINGLE_STORE_MIGRATION.read_text())
    row = conn.execute("SELECT count(*) AS n FROM proj_series WHERE line_key LIKE 'financing.%'").fetchone()
    return {"series": row["n"] if isinstance(row, dict) else row[0]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true")
    g.add_argument("--sql", action="store_true")
    g.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)
    if a.sql:
        print(build_sql())
    elif a.check:
        prm = seed_params()
        diff = {k: (P.DEFAULTS.get(k), v) for k, v in prm.items() if v != P.DEFAULTS.get(k)}
        print(json.dumps({"series_cells": len(seed_series()), "params_differing_from_defaults": diff}, indent=1, default=str))
    else:
        import psycopg
        url = os.environ.get("DATABASE_URL")
        if not url:
            print("DATABASE_URL is not set", file=sys.stderr)
            return 2
        with psycopg.connect(url) as conn:
            print(apply_seed(conn))
    return 0


if __name__ == "__main__":
    sys.exit(main())
