"""Moves one variable in memory and reports which engine (or planner) outputs changed."""
from __future__ import annotations

from app.projection import engine
from app.projection.variables import access as A, proof as P


def apply_setup(ctx, spec, scn=None):
    """Scenario with the spec's `setup` values applied first (variables that must be on before this one has an effect)."""
    from app.projection.variables import registry
    scn = scn or ctx.scn
    setup = (spec.extra or {}).get("setup") or {}
    for vid, val in setup.items():
        other = registry.get(vid)
        c2 = A.Context(ctx.conn, scn=scn)
        scn = A.scenario_with(c2, other, val)
    return scn


def moved(ctx, spec, current):
    base_scn = apply_setup(ctx, spec)
    base_ctx = A.Context(ctx.conn, scn=base_scn)
    v = A.coerce(spec, A.moved_value(spec, current))
    scn2 = A.scenario_with(base_ctx, spec, v)
    return base_scn, scn2, v


def plan_fingerprint(conn, scn):
    from app.projection.inventory_supply import loaders, planner
    from app.projection.inventory_supply.model import Settings
    run = engine.run(scn, conn)
    inputs = loaders.load_inputs(conn, scn)
    res = run.results["inventory_supply"]
    plan = planner.suggest(inputs, run.results.get("inventory_demand"), res.today, Settings.from_params(scn.params), replace_auto=True)
    return P._summ(plan)


def changed_outputs(ctx, spec, current) -> list[str]:
    conn = ctx.conn
    base_scn, scn2, _ = moved(ctx, spec, current)
    if spec.probe == "plan":
        return P.diff({"plan": plan_fingerprint(conn, base_scn)}, {"plan": plan_fingerprint(conn, scn2)})
    a = P.fingerprint(engine.run(base_scn, conn))
    b = P.fingerprint(engine.run(scn2, conn))
    return P.diff(a, b)
