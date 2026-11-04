"""QA probe (read-only GETs): force one module to raise and report what each endpoint says. Run:
DATABASE_URL=postgresql://localhost/tagexp_proj_qa AUTH_DISABLED=1 SECRET_KEY=x PROJECTION_TODAY=2026-11-03 PYTHONPATH=. python tests/projection/qa/probe_stub_fallback.py <module_key>"""
import json, sys, os
from app import create_app
from app.projection import registry

key = sys.argv[1]
app = create_app({"DATABASE_URL": os.environ["DATABASE_URL"], "AUTH_DISABLED": True, "SECRET_KEY": "x", "DEV_INSECURE_COOKIES": True})
c = app.test_client()
found = registry.discover()
mod = found[key].module
def boom(*a, **k): raise RuntimeError("QA forced failure")
object.__setattr__(mod, "compute", boom) if hasattr(mod, "__dataclass_fields__") else setattr(mod, "compute", boom)
P = "/api/projection"
r = c.get(f"{P}/run?scenario=current").get_json()
print("RUN modules flagged:", {k: {"stub": v["stub"], "error": v.get("error")} for k, v in r["modules"].items() if v["stub"] or v.get("error")})
print("RUN headline:", json.dumps({k: r["headline"].get(k) for k in ("fy", "lowest_ending_cash", "lifetime_multiple")})[:300])
paths = {"pnl": ["/pnl/table", "/pnl/summary", "/pnl/ev"], "drivers": ["/drivers/table"], "cohorts": ["/cohorts/table", "/cohorts/summary"],
         "financing": ["/financing/table", "/financing/payback"], "inventory_demand": ["/inventory-demand/table"], "inventory_supply": ["/inventory-supply/table", "/inventory-supply/summary"],
         "pacing": ["/pacing/month", "/pacing/snapshot"]}
for k, ps in paths.items():
    for p in ps:
        resp = c.get(P + p)
        js = resp.get_json(silent=True)
        info = ""
        if isinstance(js, dict):
            if "error" in js: info = "error=" + str(js["error"])[:80]
            elif "rows" in js and js["rows"]:
                row = js["rows"][0]; nums = [x for rr in js["rows"] for x in (rr.get("values") or []) if isinstance(x, (int, float))]
                info = f"rows={len(js['rows'])} nonzero_cells={sum(1 for x in nums if x)} captions={js.get('captions')}"
            else: info = "keys=" + ",".join(list(js)[:6])
        print(f"{p:32s} {resp.status_code} {info}")
