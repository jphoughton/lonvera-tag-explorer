#!/usr/bin/env python3
"""Idempotently (re)apply the Railway service settings for web + etl.

Why this exists: services created after Railway deprecated Config-as-Code ignore railway.json, so build/start/
cron/healthcheck live in the service settings. This script is the source of truth for those settings.

Usage (from a directory linked to the project: `railway link`):
    python3 scripts/deploy/apply_service_config.py            # apply
    python3 scripts/deploy/apply_service_config.py --show     # print current values only
    python3 scripts/deploy/apply_service_config.py --cron "0 14 * * *"   # change the daily refresh time (UTC)

Auth: the Railway CLI's own login token (~/.railway/config.json); it is never printed. Stdlib only.
"""
import argparse
import json
import os
import subprocess
import urllib.error
import urllib.request

API = "https://backboard.railway.com/graphql/v2"
DEFAULT_CRON = "0 13 * * *"  # 13:00 UTC = 06:00 PDT / 05:00 PST

WEB = {
    "dockerfilePath": "Dockerfile",
    "healthcheckPath": "/healthz",
    "healthcheckTimeout": 300,
    "restartPolicyType": "ON_FAILURE",
    "restartPolicyMaxRetries": 5,
}
ETL = {
    "dockerfilePath": "Dockerfile",
    "startCommand": "scripts/deploy/run_etl.sh",
    "cronSchedule": DEFAULT_CRON,
    "restartPolicyType": "NEVER",
}
READ = "builder dockerfilePath startCommand cronSchedule healthcheckPath healthcheckTimeout restartPolicyType restartPolicyMaxRetries"


def gql(query, variables=None):
    cfg = json.load(open(os.path.expanduser("~/.railway/config.json")))
    tok = cfg["user"]["token"]
    req = urllib.request.Request(
        API,
        data=json.dumps({"query": query, "variables": variables or {}}).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + tok, "User-Agent": "railway-cli"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise SystemExit("Railway API %s: %s" % (e.code, e.read().decode()[:300]))


def ids():
    st = json.loads(subprocess.check_output(["railway", "status", "--json"]))
    env = next(e["node"] for e in st["environments"]["edges"] if e["node"]["name"] == "production")
    svc = {s["node"]["serviceName"]: s["node"]["serviceId"] for s in env["serviceInstances"]["edges"]}
    return env["id"], svc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--cron", default=DEFAULT_CRON, help="UTC crontab for the etl service (min 5 min apart)")
    a = ap.parse_args()
    env_id, svc = ids()
    etl = dict(ETL, cronSchedule=a.cron)
    for name, want in (("web", WEB), ("etl", etl)):
        sid = svc[name]
        if not a.show:
            for k, v in want.items():  # one field per call: the API rejects the whole call if one field is bad
                r = gql(
                    "mutation($s:String!,$e:String!,$i:ServiceInstanceUpdateInput!){serviceInstanceUpdate(serviceId:$s,environmentId:$e,input:$i)}",
                    {"s": sid, "e": env_id, "i": {k: v}},
                )
                if r.get("errors"):
                    raise SystemExit("%s.%s failed: %s" % (name, k, r["errors"][0]["message"]))
        cur = gql("query($s:String!,$e:String!){serviceInstance(serviceId:$s,environmentId:$e){%s}}" % READ, {"s": sid, "e": env_id})
        print(name, json.dumps(cur["data"]["serviceInstance"]))


if __name__ == "__main__":
    main()
