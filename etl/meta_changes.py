"""Meta change feeds (READ-ONLY Graph GETs, both ad accounts): the Marketing API activity log -> meta_change_log, and a snapshot of every ad set's settings ->
meta_adset_state_daily (the ledger view analytics.meta_change_ledger diffs consecutive snapshot days).

  python -m etl.meta_changes                       the nightly call: activity log of the last 3 days (overlap, deduped on a stable key) + today's ad set snapshot
  python -m etl.meta_changes --days 3 --no-snapshot
  python -m etl.meta_changes --backfill --since 2026-05-06 [--until D]    activity log history in weekly chunks (resumable: chunks already fully loaded are re-run harmlessly, rows dedupe)
  python -m etl.meta_changes --snapshot-only
  python -m etl.meta_changes --dry-run             pull and print counts, write nothing

Activity log (GET /act_<id>/activities, fields event_type, object_type, object_id, object_name, extra_data, event_time, actor_name, translated_event_type ...): Meta has no event id,
so event_key = sha1(account | event_time | event_type | object_id | extra_data | actor_id); two byte-identical events in the same second collapse to one row. Meta object types:
ADGROUP = an ad, CAMPAIGN = an ad SET (Meta's old name), CAMPAIGN_GROUP = a campaign, ACCOUNT = the ad account; object_level carries the plain word. Budgets and bids arrive in minor units
(cents) and are stored in dollars in old_amount / new_amount; the ROAS floor arrives x10000 (9000 = 0.90x) and is stored as a ratio. add_images / edit_images keep no extra_data in raw
(signed CDN URLs that expire); raw holds only extra_data (parsed), everything else is a column.
Snapshot (GET /act_<id>/adsets, non-archived ad sets plus archived ones touched in the last 3 days): one row per ad set per Pacific day, replaced on a same-day rerun. It starts accumulating on
the day the first run happens; nothing before that exists.
Token: META_ACCESS_TOKEN (never printed). Every error text is scrubbed."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import random
import sys
import time
from zoneinfo import ZoneInfo

from . import db, meta_api

PT = ZoneInfo("America/Los_Angeles")
ACT_FIELDS = "event_type,object_type,object_id,object_name,extra_data,event_time,actor_id,actor_name,translated_event_type,application_name"
ADSET_FIELDS = "id,name,campaign_id,status,effective_status,daily_budget,lifetime_budget,bid_strategy,bid_amount,bid_constraints,optimization_goal,attribution_spec,created_time,updated_time"
# DELETED is not accepted as a list filter by Meta; archived ad sets are pulled but only the recently touched ones are kept
ADSET_STATUSES = json.dumps(["ACTIVE", "PAUSED", "ARCHIVED", "IN_PROCESS", "WITH_ISSUES", "CAMPAIGN_PAUSED"])
LEVELS = {"ADGROUP": "ad", "CAMPAIGN": "adset", "CAMPAIGN_GROUP": "campaign", "ACCOUNT": "account"}
NO_EXTRA = {"add_images", "edit_images"}
ARCHIVED_KEEP_DAYS = 3


# ------------------------------------------------------------------------------------------------------------------ parsing
def _num(x):
    try:
        return None if x is None or isinstance(x, bool) else float(x)
    except (TypeError, ValueError):
        return None


def _money(x):
    """Minor units (cents) -> dollars; accepts a number or {type: payment_amount, old_value / new_value: n}."""
    if isinstance(x, dict):
        for k in ("new_value", "old_value"):
            if k in x:
                return _money(x[k])
        return None
    n = _num(x)
    return None if n is None else round(n / 100.0, 2)


def _flatten_spec(v) -> str | None:
    """Targeting description lists ([{content, children}]) -> 'Location: A, B | Age: 18 - 65+'."""
    if not isinstance(v, list):
        return None
    parts = []
    for x in v:
        if isinstance(x, dict) and x.get("content"):
            parts.append(f"{str(x['content']).rstrip(':')}: {', '.join(str(c) for c in (x.get('children') or []))}")
    return (" | ".join(parts))[:4000] or None


def _text(v, event_type) -> str | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        return v[:4000]
    if event_type == "update_ad_set_target_spec":
        return _flatten_spec(v)
    return None                                             # dicts / lists (budgets, creatives, audiences): amounts are parsed, the rest stays in raw


def _campaign_id(ev, ex) -> str | None:
    lvl = LEVELS.get(ev.get("object_type"))
    if lvl == "campaign":
        return str(ev.get("object_id")) if ev.get("object_id") else None
    c = ex.get("campaign_id") if isinstance(ex, dict) else None
    if isinstance(c, dict):
        c = c.get("new") or c.get("mutation_input")
    return str(c) if c else None


def parse_event(account: str, ev: dict) -> dict:
    """One activity-log event -> one meta_change_log row (dict). Never raises on odd extra_data: unparsable text stays only in raw."""
    et = ev.get("event_type") or ""
    ex_raw = ev.get("extra_data")
    try:
        ex = json.loads(ex_raw) if isinstance(ex_raw, str) and ex_raw else (ex_raw if isinstance(ex_raw, (dict, list)) else None)
    except ValueError:
        ex = None
    ex = ex if isinstance(ex, dict) else {}
    when = dt.datetime.fromisoformat(str(ev["event_time"]).replace("Z", "+00:00"))
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)
    old_v, new_v = ex.get("old_value"), ex.get("new_value")
    old_amt = new_amt = old_floor = new_floor = None
    if isinstance(old_v, dict) or isinstance(new_v, dict):                       # budgets: {type: payment_amount, old_value / new_value: cents}
        old_amt, new_amt = _money(old_v), _money(new_v)
    if et == "update_ad_set_bid_strategy":                                       # old / new = strategy name; the bid amount sits in additional_value
        add = ex.get("additional_value") if isinstance(ex.get("additional_value"), dict) else {}
        old_amt, new_amt = _money(add.get("old_value")), _money(add.get("new_value"))
        fl = ex.get("adset_minimum_return_on_ad_spend") if isinstance(ex.get("adset_minimum_return_on_ad_spend"), dict) else {}
        old_floor = None if _num(fl.get("old_value")) is None else round(_num(fl["old_value"]) / 10000.0, 4)
        new_floor = None if _num(fl.get("new_value")) is None else round(_num(fl["new_value"]) / 10000.0, 4)
    if et == "ad_account_billing_charge":
        new_amt = _money(ex.get("new_value"))
    # raw = the event's extra_data (parsed JSON), the only part of the event that is not already a column; add_images / edit_images keep none (signed image URLs that expire)
    raw = {} if et in NO_EXTRA else (ex if ex else ({"_unparsed": ex_raw} if ex_raw else {}))
    key = hashlib.sha1("|".join([account, str(ev.get("event_time")), et, str(ev.get("object_id")), str(ex_raw), str(ev.get("actor_id"))]).encode()).hexdigest()
    return {"event_key": key, "account": account, "event_time": when, "event_day": when.astimezone(PT).date(), "event_type": et,
            "translated_event_type": ev.get("translated_event_type"), "object_type": ev.get("object_type"), "object_level": LEVELS.get(ev.get("object_type"), (ev.get("object_type") or "").lower() or None),
            "object_id": str(ev["object_id"]) if ev.get("object_id") else None, "object_name": ev.get("object_name"), "campaign_id": _campaign_id(ev, ex),
            "actor_id": ev.get("actor_id"), "actor_name": ev.get("actor_name"), "application_name": ev.get("application_name"),
            "old_value": _text(old_v, et), "new_value": _text(new_v, et), "old_amount": old_amt, "new_amount": new_amt,
            "old_roas_floor": old_floor, "new_roas_floor": new_floor, "raw": raw}


LOG_COLS = ["event_key", "account", "event_time", "event_day", "event_type", "translated_event_type", "object_type", "object_level", "object_id", "object_name", "campaign_id",
            "actor_id", "actor_name", "application_name", "old_value", "new_value", "old_amount", "new_amount", "old_roas_floor", "new_roas_floor", "raw"]


# ------------------------------------------------------------------------------------------------------------------ pulls
TRANSIENT = (meta_api.TooBig, meta_api.GraphError)       # TooBig = Graph code 1 / 'reduce the amount of data' / 'An unknown error occurred'; GraphError = exhausted transient retries
HEAL_DAYS = 7                                            # nightly run also re-pulls any account-day in the last 7 days (before the 3-day overlap) that holds no events at all
RECENT_DAYS = 2                                          # a failed window only fails the job when it reaches back further than today - 2: the next nightly overlap re-pulls newer days


def _backoff(attempt: int, base=5.0, cap=120.0) -> float:
    """Exponential backoff: base * 2^attempt with +-25% jitter, capped."""
    w = min(cap, base * 2 ** attempt)
    return w * (0.75 + random.random() * 0.5)


def _fetch(tok, url, acct, an, since, until, limit, pace) -> list[dict]:
    params = {"access_token": tok, "since": since.isoformat(), "until": (until + dt.timedelta(days=1)).isoformat(), "limit": limit, "fields": ACT_FIELDS}
    out, nxt = [], url
    while nxt:
        j = meta_api.graph_get(nxt, params, f"{an} activities {since}..{until}", max_retries=3)
        out += j.get("data", [])
        nxt = j.get("paging", {}).get("next")
        params = None
        time.sleep(pace)
    return out


def _fetch_retry(tok, url, acct, an, since, until, limit, pace, attempts) -> list[dict]:
    """One window at one page size, retried with exponential backoff + jitter on transient Graph errors (a mid-pagination failure restarts the window, no half-kept rows).
    Token rejection and rate-limit exhaustion (graph_get already backed off for minutes) are not retried here."""
    for i in range(attempts):
        try:
            return _fetch(tok, url, acct, an, since, until, limit, pace)
        except (meta_api.TokenRejected, meta_api.RateLimited):
            raise
        except TRANSIENT as e:
            if i == attempts - 1:
                raise
            w = _backoff(i)
            db.log(f"  activity log {an} {since}..{until}: transient error ({db.scrub(e)[:60]}), retry {i + 1}/{attempts - 1} in {w:.0f}s")
            time.sleep(w)
    raise AssertionError("unreachable")


def _pull(tok, url, acct, an, since, until, pace, failures: list) -> list[dict]:
    """Events of [since, until]; every window that cannot be loaded is appended to `failures` as (since, until, exception) and the rest still comes back."""
    multi = since < until
    try:
        if multi:
            return _fetch_retry(tok, url, acct, an, since, until, 500, pace, attempts=2)
        last = None
        for limit in (500, 100, 25):                      # a stubborn single day: smaller pages, each size retried with backoff
            try:
                return _fetch_retry(tok, url, acct, an, since, until, limit, pace, attempts=3 if limit == 500 else 2)
            except meta_api.RateLimited:
                raise
            except TRANSIENT as e:
                last = e
        raise last
    except meta_api.TokenRejected:
        raise
    except meta_api.RateLimited as e:
        failures.append((since, until, e))                # splitting does not help against a rate limit; the heal step re-pulls these days next night
        return []
    except TRANSIENT as e:
        if not multi:
            failures.append((since, until, e))
            return []
        mid = since + (until - since) // 2
        db.log(f"  activity log {an} {since}..{until}: still failing ({db.scrub(e)[:60]}), splitting at {mid}")
        return _pull(tok, url, acct, an, since, mid, pace, failures) + _pull(tok, url, acct, an, mid + dt.timedelta(days=1), until, pace, failures)


def pull_activities(tok, acct, an, since: dt.date, until: dt.date, pace=0.3, failures: list | None = None) -> list[dict]:
    """Activity log of [since, until] (Meta's `until` is exclusive of that date, so one day is added), paged.
    Transient Graph errors (code 1 'An unknown error occurred', which Meta also gives to a window with too many events, 2, 4, 17, 32, 613, 80004, 5xx, network) are retried with
    exponential backoff + jitter; a window that keeps failing is split in half down to one day, and a single day is retried with smaller pages (500, 100, 25).
    With `failures` (a list) a day that never loads is recorded there and every other day still returns; without it the first such failure is raised."""
    url = f"{meta_api.G}/{acct}/activities"
    local: list = []
    out = _pull(tok, url, acct, an, since, until, pace, local if failures is None else failures)
    if failures is None and local:
        raise local[0][2]
    return out


def load_log(conn, rows: list[dict]) -> int:
    """Insert new events (dedupe on event_key). Returns how many were new."""
    if not rows:
        return 0
    before = conn.execute("SELECT count(*) FROM meta_change_log").fetchone()[0]
    sql = f"INSERT INTO meta_change_log ({', '.join(LOG_COLS)}) VALUES ({', '.join(['%s'] * len(LOG_COLS))}) ON CONFLICT (event_key) DO NOTHING"
    db.execmany(conn, sql, [tuple(db.jsonb(r[c]) if c == "raw" else r[c] for c in LOG_COLS) for r in rows])
    return conn.execute("SELECT count(*) FROM meta_change_log").fetchone()[0] - before


def run_log(conn, tok, since: dt.date, until: dt.date, chunk_days=7, dry_run=False, accounts=None, heal=None) -> dict:
    """Pull [since, until] in chunks per account. Each (account, chunk) is independent: a failure is recorded in out['failures'] and the rest still loads.
    `heal` = {account name: [days]}: single days pulled in addition (days an earlier night failed to load)."""
    accounts = accounts or meta_api.ACCOUNTS
    out = {"since": since.isoformat(), "until": until.isoformat(), "pulled": 0, "inserted": 0, "by_account": {}, "chunks": 0, "failures": [], "healed_days": 0}
    windows = []
    d = since
    while d <= until:
        e = min(until, d + dt.timedelta(days=chunk_days - 1))
        windows.append((d, e))
        d = e + dt.timedelta(days=1)
    jobs = [(acct, an, w, False) for w in windows for acct, an in accounts]
    for acct, an in accounts:
        for day in sorted((heal or {}).get(an, [])):
            jobs.append((acct, an, (day, day), True))
    for acct, an, (d, e), is_heal in jobs:
        fails: list = []
        try:
            evs = pull_activities(tok, acct, an, d, e, failures=fails)
            rows = [parse_event(an, ev) for ev in evs]
            n = 0 if dry_run else load_log(conn, rows)
            if not dry_run:
                conn.commit()
        except meta_api.TokenRejected:
            raise
        except Exception as ex:                                  # noqa: BLE001 — one bad window must not lose the others
            conn.rollback()
            fails.append((d, e, ex))
            rows, n = [], 0
        for fs, fe, ex in fails:
            out["failures"].append({"account": an, "since": fs.isoformat(), "until": fe.isoformat(), "error": db.scrub(ex)[:160]})
        out["pulled"] += len(rows); out["inserted"] += n
        out["by_account"][an] = out["by_account"].get(an, 0) + len(rows)
        if is_heal and not fails:
            out["healed_days"] += 1
        db.log(f"  activity log {an} {d}..{e}: {len(rows)} events, {n} new" + (f", {len(fails)} window(s) FAILED" if fails else ""))
    out["chunks"] = len(windows)
    return out


def missing_days(conn, accounts, lo: dt.date, hi: dt.date) -> dict:
    """Account-days in [lo, hi] with no events in meta_change_log: a day an earlier run failed to load (a real day on the Main account always has dozens of events)."""
    heal = {}
    for acct, an in accounts:
        have = {r[0] for r in conn.execute("SELECT DISTINCT event_day FROM meta_change_log WHERE account=%s AND event_day BETWEEN %s AND %s", (an, lo, hi)).fetchall()}
        d, miss = lo, []
        while d <= hi:
            if d not in have:
                miss.append(d)
            d += dt.timedelta(days=1)
        heal[an] = miss
    return heal


def fatal_failures(failures: list, today: dt.date) -> list:
    """Failures that leave data older than RECENT_DAYS missing (the nightly 3-day overlap re-pulls anything newer)."""
    cut = today - dt.timedelta(days=RECENT_DAYS)
    return [f for f in failures if dt.date.fromisoformat(f["since"]) < cut]


def _cents(x):
    n = _num(x)
    return None if n is None else round(n / 100.0, 2)


def adset_row(an: str, day: dt.date, a: dict) -> tuple:
    bc = a.get("bid_constraints") if isinstance(a.get("bid_constraints"), dict) else {}
    fl = _num(bc.get("roas_average_floor"))
    return (str(a["id"]), day, an, str(a["campaign_id"]) if a.get("campaign_id") else None, a.get("name"), a.get("status"), a.get("effective_status"),
            _cents(a.get("daily_budget")), _cents(a.get("lifetime_budget")), a.get("bid_strategy"), _cents(a.get("bid_amount")),
            None if fl is None else round(fl / 10000.0, 4), a.get("optimization_goal"),
            db.jsonb(a["attribution_spec"]) if a.get("attribution_spec") is not None else None, a.get("created_time"), a.get("updated_time"))


def pull_adsets(tok, acct, an, pace=0.3, attempts=3) -> list[dict]:
    """All ad sets of one account; a transient Graph error restarts the pull with exponential backoff + jitter (nothing half-kept)."""
    for i in range(attempts):
        url = f"{meta_api.G}/{acct}/adsets"
        params = {"access_token": tok, "limit": 500, "fields": ADSET_FIELDS, "effective_status": ADSET_STATUSES}
        out = []
        try:
            while url:
                j = meta_api.graph_get(url, params, f"{an} adsets", max_retries=3)
                out += j.get("data", [])
                url = j.get("paging", {}).get("next")
                params = None
                time.sleep(pace)
            return out
        except (meta_api.TokenRejected, meta_api.RateLimited):
            raise
        except TRANSIENT as e:
            if i == attempts - 1:
                raise
            w = _backoff(i)
            db.log(f"  adsets {an}: transient error ({db.scrub(e)[:60]}), retry {i + 1}/{attempts - 1} in {w:.0f}s")
            time.sleep(w)
    return []


def keep_adset(a: dict, today: dt.date) -> bool:
    """Live ad sets always; archived ones only when touched in the last ARCHIVED_KEEP_DAYS days (so the ledger sees the archive step, not 4,000 dead rows a day)."""
    if a.get("effective_status") != "ARCHIVED" and a.get("status") != "ARCHIVED":
        return True
    u = a.get("updated_time")
    if not u:
        return False
    try:
        ut = dt.datetime.fromisoformat(str(u).replace("Z", "+00:00")).astimezone(PT).date()
    except ValueError:
        return False
    return ut >= today - dt.timedelta(days=ARCHIVED_KEEP_DAYS)


SNAP_SQL = ("INSERT INTO meta_adset_state_daily (adset_id, day, account, campaign_id, adset_name, status, effective_status, daily_budget, lifetime_budget, bid_strategy, bid_amount, "
            "roas_average_floor, optimization_goal, attribution_spec, created_time, updated_time) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (adset_id, day) DO UPDATE SET account=EXCLUDED.account, campaign_id=EXCLUDED.campaign_id, adset_name=EXCLUDED.adset_name, status=EXCLUDED.status, "
            "effective_status=EXCLUDED.effective_status, daily_budget=EXCLUDED.daily_budget, lifetime_budget=EXCLUDED.lifetime_budget, bid_strategy=EXCLUDED.bid_strategy, "
            "bid_amount=EXCLUDED.bid_amount, roas_average_floor=EXCLUDED.roas_average_floor, optimization_goal=EXCLUDED.optimization_goal, attribution_spec=EXCLUDED.attribution_spec, "
            "created_time=EXCLUDED.created_time, updated_time=EXCLUDED.updated_time, snapshot_at=now()")


def run_snapshot(conn, tok, today: dt.date | None = None, dry_run=False, accounts=None) -> dict:
    """Pull each account's ad sets independently (an account that fails is reported in 'failures', the other account's rows are still written), then upsert today's rows in one transaction."""
    today = today or dt.datetime.now(PT).date()
    rows, per, failures = [], {}, []
    for acct, an in (accounts or meta_api.ACCOUNTS):
        try:
            sets = [a for a in pull_adsets(tok, acct, an) if keep_adset(a, today)]
        except meta_api.TokenRejected:
            raise
        except Exception as e:                                  # noqa: BLE001
            failures.append({"account": an, "error": db.scrub(e)[:160]})
            continue
        per[an] = len(sets)
        rows += [adset_row(an, today, a) for a in sets]
    if not dry_run and rows:
        with conn.transaction():
            db.execmany(conn, SNAP_SQL, rows)
        conn.commit()
    db.log(f"  adset snapshot {today}: {per}" + (f" FAILED {failures}" if failures else ""))
    return {"day": today.isoformat(), "adsets": len(rows), "by_account": per, "failures": failures}


# ------------------------------------------------------------------------------------------------------------------ entry
def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=3, help="activity log window ending today (Pacific); overlap is deduped")
    ap.add_argument("--backfill", action="store_true", help="activity log history from --since (default 2026-05-06) through today, weekly chunks")
    ap.add_argument("--since")
    ap.add_argument("--until")
    ap.add_argument("--chunk-days", type=int, default=7)
    ap.add_argument("--no-snapshot", action="store_true")
    ap.add_argument("--snapshot-only", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    today = dt.datetime.now(PT).date()
    until = dt.date.fromisoformat(a.until) if a.until else today
    if a.backfill:
        since = dt.date.fromisoformat(a.since or "2026-05-06")
    else:
        since = dt.date.fromisoformat(a.since) if a.since else until - dt.timedelta(days=a.days - 1)
    with db.JobRun("meta_changes") as run_:
        tok = meta_api.get_token()
        stats: dict = {}
        errors, notes = [], []
        with db.connect() as conn:
            if not a.snapshot_only:
                heal = None
                if not a.backfill and not a.since and not a.dry_run:                    # nightly: also re-pull account-days before the overlap that never loaded
                    heal = missing_days(conn, meta_api.ACCOUNTS, today - dt.timedelta(days=HEAL_DAYS), since - dt.timedelta(days=1))
                    heal = {k: v for k, v in heal.items() if v}
                try:
                    stats["log"] = run_log(conn, tok, since, until, chunk_days=a.chunk_days, dry_run=a.dry_run, heal=heal)
                except meta_api.TokenRejected:
                    raise db.JobFailed("meta token rejected") from None
                except Exception as e:                                  # noqa: BLE001 — the snapshot still runs; the job is failed at the end
                    conn.rollback()
                    errors.append("activity log: " + db.scrub(e)[:200])
                else:
                    fl = stats["log"]["failures"]
                    bad = fatal_failures(fl, today)
                    desc = "; ".join(f"{f['account']} {f['since']}..{f['until']}: {f['error']}" for f in fl)[:300]
                    if bad:
                        errors.append(f"activity log: {len(bad)} window(s) older than {RECENT_DAYS} days not loaded: " + desc)
                    elif fl:
                        notes.append(f"activity log: {len(fl)} recent window(s) failed, the next run re-pulls them: " + desc)
            if not a.no_snapshot and not a.backfill:
                try:
                    stats["snapshot"] = run_snapshot(conn, tok, today, dry_run=a.dry_run)
                except meta_api.TokenRejected:
                    raise db.JobFailed("meta token rejected") from None
                except Exception as e:                                  # noqa: BLE001
                    conn.rollback()
                    errors.append("adset snapshot: " + db.scrub(e)[:200])
                else:
                    for f in stats["snapshot"]["failures"]:
                        errors.append(f"adset snapshot {f['account']}: {f['error']}")
        run_.stats = stats
        run_.rows_written = (stats.get("log", {}).get("inserted", 0)) + (stats.get("snapshot", {}).get("adsets", 0))
        if notes:
            run_.stats["notes"] = notes
            run_.note = "; ".join(notes)[:480]
        if errors:
            run_.stats["errors"] = errors
            raise db.JobFailed("; ".join(errors + notes)[:480])
    return 0


if __name__ == "__main__":
    sys.exit(main())
