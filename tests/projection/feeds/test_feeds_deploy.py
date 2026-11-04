"""Deploy helpers: the `feeds` service plan (dry run), the entrypoint script, and that nothing in the module prints a secret."""
from __future__ import annotations

import importlib.util
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def load_script():
    spec = importlib.util.spec_from_file_location("apply_feeds_service", ROOT / "scripts/deploy/apply_feeds_service.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_plan_for_a_new_service_lists_what_it_would_create():
    m = load_script()
    plan = m.make_plan("p", "e", {"web": "w", "etl": "t"}, ["DATABASE_URL", "GOOGLE_SA_JSON", "META_ACCESS_TOKEN", "SECRET_KEY", "UPSTACK_API_KEY",
                                                             "UPSTACK_PIXEL_ID", "WAREHANCE_API_KEY"], m.DEFAULT_CRON)
    assert plan["create_service"] and plan["missing_on_etl"] == []
    assert plan["instance"] == {"dockerfilePath": "Dockerfile", "startCommand": "scripts/deploy/run_feeds.sh", "restartPolicyType": "NEVER", "cronSchedule": "0 21 * * *"}
    assert plan["variables"]["UPSTACK_API_KEY"] == "${{etl.UPSTACK_API_KEY}}" and plan["variables"]["FEEDS_VERIFY"] == "1"
    assert "FEEDS_ENC_KEY" not in plan["variables"]                                      # referenced only once it exists on etl
    text = m.describe(plan)
    assert "CREATE" in text and "0 21 * * *" in text and "scripts/deploy/run_feeds.sh" in text


def test_plan_is_idempotent_when_the_service_matches_and_warns_about_missing_variables():
    m = load_script()
    cur = {"dockerfilePath": "Dockerfile", "startCommand": "scripts/deploy/run_feeds.sh", "restartPolicyType": "NEVER", "cronSchedule": "0 21 * * *"}
    plan = m.make_plan("p", "e", {"etl": "t", "feeds": "f"}, ["DATABASE_URL", "FEEDS_ENC_KEY"], "0 21 * * *", cur)
    assert not plan["create_service"] and plan["instance_changes"] == {} and "UPSTACK_API_KEY" in plan["missing_on_etl"]
    assert plan["variables"]["FEEDS_ENC_KEY"] == "${{etl.FEEDS_ENC_KEY}}"
    assert "WARNING" in m.describe(plan)
    drift = m.make_plan("p", "e", {"etl": "t", "feeds": "f"}, [], "0 20 * * *", cur)
    assert drift["instance_changes"] == {"cronSchedule": "0 20 * * *"}


def test_apply_makes_one_call_per_field_and_never_sets_a_secret_value():
    m = load_script()
    calls = []

    def fake(query, variables=None):
        calls.append((query, variables))
        if "serviceCreate" in query:
            return {"data": {"serviceCreate": {"id": "new-id-1234", "name": "feeds"}}}
        return {"data": {}}
    plan = m.make_plan("p", "e", {"etl": "t"}, m.REFERENCES, m.DEFAULT_CRON)
    sid = m.apply_plan(plan, "p", "e", {"etl": "t"}, run=fake)
    assert sid == "new-id-1234"
    assert sum("serviceInstanceUpdate" in q for q, _ in calls) == 4 and sum("variableCollectionUpsert" in q for q, _ in calls) == 1
    upsert = next(v for q, v in calls if "variableCollectionUpsert" in q)["i"]
    assert upsert["skipDeploys"] is True and all(v.startswith("${{etl.") or v == "1" for v in upsert["variables"].values())


def test_entrypoint_script_is_valid_shell_and_executable():
    sh = ROOT / "scripts/deploy/run_feeds.sh"
    assert sh.stat().st_mode & 0o111
    assert subprocess.run(["sh", "-n", str(sh)], capture_output=True).returncode == 0
    text = sh.read_text()
    assert "python -m etl.migrate" in text and "python -m etl.feeds run" in text and "python -m etl.feeds verify" in text


def test_no_secret_shaped_literals_in_the_feeds_code():
    bad = re.compile(r"(EA[A-Za-z0-9]{40,}|postgres(?:ql)?://[^\s'\"]+:[^\s'\"@]+@|Bearer\s+[A-Za-z0-9\-_.]{20,})")
    for p in list((ROOT / "etl/feeds").glob("*.py")) + [ROOT / "scripts/deploy/run_feeds.sh", ROOT / "scripts/deploy/apply_feeds_service.py"]:
        assert not bad.search(p.read_text()), p.name
