#!/usr/bin/env python3
"""Idempotently create / update the Railway cron service `backup` (nightly Postgres dump to Google Drive with restore verification, docs/BACKUP.md). DRY RUN BY DEFAULT: prints exactly what it would do and changes nothing.

What it manages (production environment of the linked project):
  * service `backup`, no GitHub source (deployed with `railway up -s backup` from the REPO ROOT; its own image: Dockerfile scripts/deploy/backup/Dockerfile = postgres:18 + python)
  * instance settings: dockerfilePath scripts/deploy/backup/Dockerfile, startCommand scripts/deploy/run_backup.sh, cronSchedule 30 8 * * * (UTC), restartPolicyType NEVER
  * variables, all by REFERENCE to the `etl` service (`${{etl.NAME}}`), so no secret is copied or printed: see REFERENCES below.
    BACKUP_DRIVE_ID (the Shared Drive id, not a secret) is set with --drive-id, or by Josh on the service.
It never touches `web` or `etl`, never sets a secret value, and never deploys.

Usage (from a directory linked to the project, e.g. ~/lonvera-tag-explorer):
    python3 scripts/deploy/apply_backup_service.py                 # dry run: show the plan and the current state
    python3 scripts/deploy/apply_backup_service.py --apply         # create / update (asks nothing else; run only after review)
    python3 scripts/deploy/apply_backup_service.py --drive-id <shared drive id>
Then deploy the code once:  railway up -s backup --ci (from the repo root)    (separate, deliberate step)
Auth: the Railway CLI's own login token (~/.railway/config.json); it is never printed. Stdlib only.
"""
import argparse
import json
import os
import subprocess
import urllib.error
import urllib.request

API = "https://backboard.railway.com/graphql/v2"
SERVICE = "backup"
DEFAULT_CRON = "30 8 * * *"           # 08:30 UTC daily (00:30 PST / 01:30 PDT): before etl (13:00) and feeds (21:00), so the dump holds the previous day's full run
INSTANCE = {"dockerfilePath": "scripts/deploy/backup/Dockerfile", "startCommand": "scripts/deploy/run_backup.sh", "restartPolicyType": "NEVER"}
# variable name -> the service it is referenced from. Names only; values stay on `etl`.
REFERENCES = ["DATABASE_URL", "GOOGLE_SA_JSON"]
OPTIONAL_REFERENCES = []
PLAIN = {}                                                       # BACKUP_DRIVE_ID is added from --drive-id
READ = "dockerfilePath startCommand cronSchedule restartPolicyType"


def gql(query, variables=None):
    tok = json.load(open(os.path.expanduser("~/.railway/config.json")))["user"]["token"]
    req = urllib.request.Request(API, data=json.dumps({"query": query, "variables": variables or {}}).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer " + tok, "User-Agent": "railway-cli"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise SystemExit("Railway API %s: %s" % (e.code, e.read().decode()[:300]))


def project_state(status_json=None):
    st = status_json or json.loads(subprocess.check_output(["railway", "status", "--json"]))
    env = next(e["node"] for e in st["environments"]["edges"] if e["node"]["name"] == "production")
    svc = {s["node"]["serviceName"]: s["node"]["serviceId"] for s in env["serviceInstances"]["edges"]}
    return st["id"], env["id"], svc


def etl_variable_names(env_id, project_id, etl_id, run=gql):
    r = run("query($p:String!,$e:String!,$s:String!){variables(projectId:$p,environmentId:$e,serviceId:$s)}", {"p": project_id, "e": env_id, "s": etl_id})
    return sorted(k for k in (r.get("data") or {}).get("variables", {}) if not k.startswith("RAILWAY_"))


def make_plan(project_id, env_id, services, etl_names, cron, current=None):
    """Pure: what apply would do. `current` = the existing backup instance settings (None when the service does not exist)."""
    want = dict(INSTANCE, cronSchedule=cron)
    refs = {n: "${{etl.%s}}" % n for n in REFERENCES + [n for n in OPTIONAL_REFERENCES if n in etl_names]}
    plan = {"service": SERVICE, "create_service": SERVICE not in services, "instance": want,
            "instance_changes": {k: v for k, v in want.items() if (current or {}).get(k) != v},
            "variables": {**refs, **PLAIN}, "missing_on_etl": [n for n in REFERENCES if n not in etl_names],
            "optional_present": [n for n in OPTIONAL_REFERENCES if n in etl_names]}
    return plan


def describe(plan):
    lines = [f"service      : {plan['service']} ({'CREATE, no GitHub source; deploy later with: railway up -s backup --ci (from the repo root)' if plan['create_service'] else 'exists'})",
             "image        : own Dockerfile scripts/deploy/backup/Dockerfile (postgres:18 + python, build context = repo root)",
             f"start command: {plan['instance']['startCommand']}", f"cron (UTC)   : {plan['instance']['cronSchedule']}",
             f"restart      : {plan['instance']['restartPolicyType']}", "instance changes: " + (", ".join(f"{k}={v}" for k, v in plan["instance_changes"].items()) or "none"),
             "variables    :"]
    for k, v in plan["variables"].items():
        lines.append(f"    {k} = {v}")
    if plan["missing_on_etl"]:
        lines.append("WARNING: not set on etl yet (the reference would be empty until they are): " + ", ".join(plan["missing_on_etl"]))
    return "\n".join(lines)


def apply_plan(plan, project_id, env_id, services, run=gql):
    sid = services.get(SERVICE)
    if plan["create_service"]:
        r = run("mutation($i:ServiceCreateInput!){serviceCreate(input:$i){id name}}", {"i": {"name": SERVICE, "projectId": project_id, "environmentId": env_id}})
        if r.get("errors"):
            raise SystemExit("serviceCreate failed: %s" % r["errors"][0]["message"])
        sid = r["data"]["serviceCreate"]["id"]
    for k, v in plan["instance_changes"].items():          # one field per call: the API rejects the whole call if one field is bad
        r = run("mutation($s:String!,$e:String!,$i:ServiceInstanceUpdateInput!){serviceInstanceUpdate(serviceId:$s,environmentId:$e,input:$i)}",
                {"s": sid, "e": env_id, "i": {k: v}})
        if r.get("errors"):
            raise SystemExit("%s failed: %s" % (k, r["errors"][0]["message"]))
    r = run("mutation($i:VariableCollectionUpsertInput!){variableCollectionUpsert(input:$i)}",
            {"i": {"projectId": project_id, "environmentId": env_id, "serviceId": sid, "variables": plan["variables"], "skipDeploys": True}})
    if r.get("errors"):
        raise SystemExit("variableCollectionUpsert failed: %s" % r["errors"][0]["message"])
    return sid


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--apply", action="store_true", help="make the changes (default: dry run)")
    ap.add_argument("--cron", default=DEFAULT_CRON)
    ap.add_argument("--drive-id", default=None, help="id of the Shared Drive 'Lonvera DB Backups' (set as BACKUP_DRIVE_ID)")
    a = ap.parse_args(argv)
    project_id, env_id, services = project_state()
    names = etl_variable_names(env_id, project_id, services["etl"])
    current = None
    if SERVICE in services:
        r = gql("query($s:String!,$e:String!){serviceInstance(serviceId:$s,environmentId:$e){%s}}" % READ, {"s": services[SERVICE], "e": env_id})
        current = (r.get("data") or {}).get("serviceInstance")
    plan = make_plan(project_id, env_id, services, names, a.cron, current)
    if a.drive_id:
        plan["variables"]["BACKUP_DRIVE_ID"] = a.drive_id
    print(("APPLY" if a.apply else "DRY RUN (nothing is changed; add --apply to create)"))
    print(describe(plan))
    if a.apply:
        sid = apply_plan(plan, project_id, env_id, services)
        print("done: service %s (%s). Deploy the code with: railway up -s backup --ci (from the repo root)" % (SERVICE, sid[:8]))


if __name__ == "__main__":
    main()
