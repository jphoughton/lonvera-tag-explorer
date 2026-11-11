"""Failure and staleness alerts for the Lonvera Marketing metrics / Tag Explorer app. Read-only checks, one Slack message per problem.

    python -m etl.alerts [--dry-run] [--test]

Runs hourly as the Railway cron service `alerts` (scripts/deploy/run_alerts.sh), on the same image as `etl`. It never writes to etl_runs (an hourly row there would
mask the stale banner and churn the response cache); its only write is the de-duplication table alert_state (migration 197).

Checks (thresholds are constants below; docs/ALERTS.md lists them):
  job:<name>      a pipeline job's latest run failed / was killed mid-run / has not succeeded within 1.5x its cadence
                  (etl_runs: run_daily, upstack_daily, stage_moves, auto_tag, competitor_ads, creative_priorities, backup; heartbeat tables for the jobs that do not
                  write etl_runs: feeds = proj_feed_attempts, live = proj_live_today_snapshots, amazon_mtd = proj_amazon_mtd_snapshots, projection_daily = proj_refresh_runs)
  claude:token    the Claude subscription token (CLAUDE_CODE_OAUTH_TOKEN) is missing / expired: auto_tag or competitor_ads stopped on an auth error
  claude:limit    auto_tag or competitor_ads stopped on a usage limit
  fresh:<source>  newest day in a key table is older than the stale banner allows (meta, upstack, google, orders) and the projection feeds (fresh:feeds)
  web:health      GET <web>/healthz is not 200 or slower than 5 s (twice in a row)

De-duplication (alert_state): alert once when a problem appears, again every 24 h while it lasts (or at once when its fingerprint changes), one "RECOVERED" line when it
clears. At most MAX_MESSAGES messages per run; the rest are collapsed into one summary. A check that crashes, an unreachable database or an undeliverable message
makes the run exit 1 (Railway shows Failed) and, where a webhook is reachable, says so in Slack.

Env: DATABASE_URL, SLACK_ALERT_WEBHOOK_URL (never logged; unset = log only, with a WARNING), ALERTS_WEB_URL (public web app, default below).
--dry-run: reads everything, writes nothing (no state, no Slack) and prints what would be sent. --test: sends one clearly labelled test message and exits.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import sys
import time
import traceback
from dataclasses import dataclass, field
from typing import Callable, Protocol
from zoneinfo import ZoneInfo

import psycopg
import requests
from psycopg.rows import dict_row

from . import db

PT = ZoneInfo("America/Los_Angeles")

# ------------------------------------------------------------------------------------------------------------------------------ thresholds
NIGHTLY_H = 36                 # nightly jobs (cron once a day): 1.5x cadence
HOURLY_H = 3                   # hourly jobs: 3 runs missed
WEEKLY_H = 8 * 24              # Sunday jobs
RUNNING_MAX_H = 6              # a run still 'running' after this long was killed (auto_tag is the longest, about 1 h)
REALERT_H = 24                 # repeat an open alert every 24 h
MAX_MESSAGES = 5               # individual messages per run; the rest collapse into one summary
STALE_META_DAYS = 2            # same limit as the app's stale banner (app.queries.STALE_DATA_DAYS)
STALE_UPSTACK_DAYS = 2
STALE_GOOGLE_DAYS = 3          # Google's own numbers land a day later than Meta's
STALE_ORDERS_DAYS = 2
WEB_SLOW_SEC = 5.0
WEB_TIMEOUT_SEC = 10.0
WEB_RETRY_WAIT_SEC = 20.0      # a deploy takes the web app down for a minute: retry once before alerting
DEFAULT_WEB_URL = "https://web-production-358da.up.railway.app"
ERROR_SHOWN = 220              # characters of an error text put into a message


@dataclass(frozen=True)
class JobSpec:
    name: str
    cadence_h: float
    required: bool             # True: "never ran" is itself an alert. False: silent until the first row exists (job may not be deployed yet)
    kind: str = "etl_runs"     # etl_runs | heartbeat:<table>
    fix: str = "Open the Railway logs of service `etl` (latest run); the error above names the step."


JOBS = [
    JobSpec("run_daily", NIGHTLY_H, True, fix="Railway > etl > latest run logs. A Meta token error means replace META_ACCESS_TOKEN on etl; a tracker error means the Google Sheet is unreadable."),
    JobSpec("upstack_daily", NIGHTLY_H, True, fix="Railway > etl logs. Check UPSTACK_API_KEY / UPSTACK_PIXEL_ID and whether Upstack is up; the last good data stays in place."),
    JobSpec("stage_moves", NIGHTLY_H, True, fix="Railway > etl logs; Test to scale keeps serving the last good rows meanwhile."),
    JobSpec("auto_tag", NIGHTLY_H, False, fix="Railway > etl logs (new ads stay untagged until it runs)."),
    JobSpec("competitor_ads", WEEKLY_H, False, fix="Railway > etl logs of the last Sunday run (Ad Library scrape or classification)."),
    JobSpec("creative_priorities", WEEKLY_H, False, fix="Railway > etl logs of the last Sunday run."),
    # the dedicated `breakdowns` service runs every 2 hours but writes no row in the quiet hours (12:30-15:30 UTC) or when another run holds the lock, and a run lasts up to 110 minutes:
    # the longest normal gap between successful rows is about 5 h, so 6 h cadence = alert after 9 h. Silent until the first row exists.
    JobSpec("meta_breakdowns", 6, False, fix="Railway > breakdowns service logs (cron every 2 hours; token rejected, or a rate limit that outlasted the waits). The backfill resumes by itself; `python -m etl.meta_breakdowns --auto` on the service restarts it."),
    JobSpec("shopify_orders", NIGHTLY_H, False, fix="Railway > feeds service logs for etl.shopify_orders (ExpandFi login or warehouse query); the order tables keep the last good rows and shopify_orders.loaded_at shows how old they are."),
    JobSpec("backup", NIGHTLY_H, False, fix="Check the backup service logs on Railway and the last dump in the backup location (docs/ALERTS.md > backup)."),
    JobSpec("feeds", NIGHTLY_H, True, kind="heartbeat:feeds", fix="Railway > feeds service logs (cron 0 21 * * * UTC); `python -m etl.feeds verify` shows per-feed state."),
    JobSpec("live", HOURLY_H, True, kind="heartbeat:live", fix="Railway > live service logs; the Pacing cards fall back to typed values until it recovers."),
    JobSpec("amazon_mtd", NIGHTLY_H, True, kind="heartbeat:amazon_mtd", fix="Railway > etl or feeds logs for etl.amazon_mtd; the Pacing page computes the Amazon month live meanwhile."),
    JobSpec("projection_daily", NIGHTLY_H, True, kind="heartbeat:projection_daily", fix="Railway > etl logs for etl.projection_daily; the module named above failed."),
]
CLAUDE_JOBS = ("auto_tag", "competitor_ads")
TOKEN_RE = re.compile(r"authentication failed|CLAUDE_CODE_OAUTH_TOKEN|setup-token|not logged in|oauth", re.I)
LIMIT_RE = re.compile(r"usage / rate limit|usage limit|rate.?limit|limit reached", re.I)
TOKEN_FIX = ("Token expired (or missing): run `claude setup-token` on your laptop, then update CLAUDE_CODE_OAUTH_TOKEN on the Railway `etl` service "
             "(auto_tag and competitor_ads both use it) and redeploy / wait for the next 13:00 UTC run.")
LIMIT_FIX = "The Claude plan usage limit was hit; the next nightly run retries by itself. Only act if this repeats: check the plan's usage or lower DEFAULT_LIMIT."


# ------------------------------------------------------------------------------------------------------------------------------ model
@dataclass
class Finding:
    id: str                    # stable check id (de-duplication key)
    title: str                 # what broke, one sentence
    fix: str                   # what to do
    since: dt.datetime | None = None
    fingerprint: str = ""      # changes when the problem changes shape; a change re-alerts at once


@dataclass
class Ctx:
    now: dt.datetime
    web_url: str = DEFAULT_WEB_URL
    http_get: Callable = requests.get
    sleep: Callable[[float], None] = time.sleep
    jobs: list[JobSpec] = field(default_factory=lambda: list(JOBS))


def fmt_pt(t: dt.datetime | None) -> str:
    return t.astimezone(PT).strftime("%b %d %H:%M PT") if t else "unknown"


def age_text(now: dt.datetime, t: dt.datetime) -> str:
    h = (now - t).total_seconds() / 3600
    return f"{h:.0f} h" if h < 72 else f"{h / 24:.0f} days"


def short(text, n: int = ERROR_SHOWN) -> str:
    s = " ".join(db.scrub(text or "").split())
    return s if len(s) <= n else s[: n - 1] + "..."


def one(conn, sql: str, params=()) -> dict | None:
    return conn.execute(sql, params).fetchone()


# ------------------------------------------------------------------------------------------------------------------------------ checks
def _classify_claude(job: str, err: str | None) -> str | None:
    if job not in CLAUDE_JOBS or not err:
        return None
    if TOKEN_RE.search(err):
        return "token"
    if LIMIT_RE.search(err):
        return "limit"
    return None


def _job_findings(spec: JobSpec, now: dt.datetime, latest_started: dict | None, latest_finished: dict | None, last_ok_at: dt.datetime | None,
                  detail: str | None = None) -> list[Finding]:
    """The one finding (or none) of a job from its run history. Priority: failed > killed mid-run > no success in time."""
    cid = f"job:{spec.name}"
    ok_txt = f"Last success {fmt_pt(last_ok_at)}." if last_ok_at else "It has never succeeded."
    if latest_finished and latest_finished["status"] == "failed":
        err = latest_finished.get("error") or detail or "no error text recorded"
        kind = _classify_claude(spec.name, err)
        since = latest_finished["finished_at"]
        if kind == "token":
            return [Finding("claude:token", f"The Claude token was rejected: {spec.name} stopped on an authentication error ({short(err, 160)}).", TOKEN_FIX, since, "token")]
        if kind == "limit":
            return [Finding("claude:limit", f"{spec.name} stopped on a Claude usage limit ({short(err, 160)}).", LIMIT_FIX, since, "limit")]
        return [Finding(cid, f"{spec.name} failed: {short(err)} {ok_txt}", spec.fix, since, f"failed:{latest_finished.get('fp', '')}")]
    if latest_started and latest_started["finished_at"] is None and now - latest_started["started_at"] > dt.timedelta(hours=RUNNING_MAX_H):
        return [Finding(cid, f"{spec.name} started {fmt_pt(latest_started['started_at'])} and never finished (killed or crashed mid-run). {ok_txt}", spec.fix,
                        latest_started["started_at"], "stuck")]
    if last_ok_at is None:
        return [Finding(cid, f"{spec.name} has never completed a run.", spec.fix, None, "never")] if spec.required else []
    if now - last_ok_at > dt.timedelta(hours=spec.cadence_h):
        return [Finding(cid, f"{spec.name} has not succeeded for {age_text(now, last_ok_at)} (expected at least every {spec.cadence_h:g} h). {ok_txt}", spec.fix,
                        last_ok_at + dt.timedelta(hours=spec.cadence_h), "stale")]
    return []


def check_etl_jobs(conn, ctx: Ctx) -> list[Finding]:
    out: list[Finding] = []
    for spec in (s for s in ctx.jobs if s.kind == "etl_runs"):
        started = one(conn, "SELECT id, started_at, finished_at, status, error FROM etl_runs WHERE job=%s ORDER BY id DESC LIMIT 1", (spec.name,))
        fin = one(conn, "SELECT id, started_at, finished_at, status, error FROM etl_runs WHERE job=%s AND finished_at IS NOT NULL ORDER BY finished_at DESC, id DESC LIMIT 1", (spec.name,))
        ok = one(conn, "SELECT max(finished_at) AS t FROM etl_runs WHERE job=%s AND status='ok' AND finished_at IS NOT NULL", (spec.name,))
        if started is None and not spec.required:
            continue                                                   # never existed (backup / auto_tag not deployed yet): silent
        out += _job_findings(spec, ctx.now, started, fin, ok["t"] if ok else None)
    return out


def check_heartbeats(conn, ctx: Ctx) -> list[Finding]:
    """Jobs that write their own table instead of etl_runs: newest successful write must be within the cadence; failures that the table records are named."""
    out: list[Finding] = []
    for spec in (s for s in ctx.jobs if s.kind.startswith("heartbeat:")):
        which = spec.kind.split(":", 1)[1]
        failed_detail = None
        fin_row = None
        if which == "live":
            r = one(conn, "SELECT max(pulled_at) AS t FROM proj_live_today_snapshots")
        elif which == "amazon_mtd":
            r = one(conn, "SELECT max(computed_at) AS t FROM proj_amazon_mtd_snapshots")
        elif which == "projection_daily":
            r = one(conn, "SELECT max(finished_at) AS t FROM proj_refresh_runs WHERE status IN ('ok', 'skipped')")
            bad = conn.execute("SELECT module, finished_at, error FROM (SELECT DISTINCT ON (module) module, finished_at, status, error FROM proj_refresh_runs "
                               "WHERE finished_at > %s ORDER BY module, id DESC) x WHERE status = 'failed' ORDER BY module", (ctx.now - dt.timedelta(hours=spec.cadence_h),)).fetchall()
            if bad:
                failed_detail = "; ".join(f"{b['module']}: {short(b['error'], 80)}" for b in bad[:4]) + (f" (+{len(bad) - 4} more)" if len(bad) > 4 else "")
                fin_row = {"fp": "|".join(b["module"] for b in bad), "status": "failed", "finished_at": max(b["finished_at"] for b in bad), "error": "module(s) failed: " + failed_detail}
        elif which == "feeds":
            r = one(conn, "SELECT max(finished_at) AS t FROM proj_feed_attempts WHERE status IN ('ok', 'fallback', 'skipped')")
            bad = conn.execute("SELECT feed, finished_at, note FROM (SELECT DISTINCT ON (feed) feed, finished_at, status, note FROM proj_feed_attempts "
                               "WHERE finished_at > %s ORDER BY feed, id DESC) x WHERE status = 'failed' ORDER BY feed", (ctx.now - dt.timedelta(hours=spec.cadence_h),)).fetchall()
            if bad:
                names = [b["feed"] for b in bad]
                fin_row = {"fp": "|".join(names), "status": "failed", "finished_at": max(b["finished_at"] for b in bad),
                           "error": f"{len(names)} feed(s) failed their latest pull: " + "; ".join(f"{b['feed']} ({short(b['note'], 70)})" for b in bad[:4]) + (f" (+{len(names) - 4} more)" if len(names) > 4 else "")}
        else:
            continue
        last_ok = r["t"] if r else None
        if last_ok is None and fin_row is None and not spec.required:
            continue
        out += _job_findings(spec, ctx.now, None, fin_row, last_ok)
    return out


def check_freshness(conn, ctx: Ctx) -> list[Finding]:
    """Newest day of the key tables against the stale banner's limits (Los Angeles day)."""
    today = ctx.now.astimezone(PT).date()
    out: list[Finding] = []
    kv = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM app_meta WHERE key = ANY(%s)", (["data_through", "upstack_through"],)).fetchall()}
    sources = [("meta", "Meta ad data (family_daily / ad_metrics)", kv.get("data_through"), STALE_META_DAYS, "job:run_daily"),
               ("upstack", "Upstack source data", kv.get("upstack_through"), STALE_UPSTACK_DAYS, "job:upstack_daily")]
    for key, sql, days, label, owner in (("orders", "SELECT max(day) AS d FROM upstack_orders_daily", STALE_ORDERS_DAYS, "Upstack daily orders (upstack_orders_daily)", "job:upstack_daily"),
                                        ("google", "SELECT max(day) AS d FROM google_daily", STALE_GOOGLE_DAYS, "Google daily data (google_daily)", "job:upstack_daily")):
        r = one(conn, sql)
        sources.append((key, label, r["d"].isoformat() if r and r["d"] else None, days, owner))
    for key, label, through, limit, owner in sources:
        if not through:
            continue                                                   # source not loaded in this database: nothing to be stale
        d = dt.date.fromisoformat(str(through)[:10])
        lag = (today - d).days
        if lag > limit:
            out.append(Finding(f"fresh:{key}", f"{label} ends {d.isoformat()}, {lag} days behind today (limit {limit}).",
                               f"Look at the {owner.split(':')[1]} alert / logs first; the pages show their stale banner meanwhile.",
                               dt.datetime.combine(d + dt.timedelta(days=limit + 1), dt.time(0), tzinfo=PT), f"d{d.isoformat()}"))
    out += _feed_freshness(conn, ctx, today)
    return out


def _feed_freshness(conn, ctx: Ctx, today: dt.date) -> list[Finding]:
    from app.projection import freshness as F
    hooks = {k: F.hook_module(k) for k in F.MODULE_KEYS}
    feeds, errors = F.collect(conn, hooks, today)
    stale = sorted((f for f in feeds if f["cadence"] == "daily" and f["status"] == "stale"), key=lambda f: f["feed"])
    out = []
    if stale:
        names = [f["feed"] for f in stale]
        shown = "; ".join(f"{f['feed']} last data {f['data_through']} ({f['age_days']} d, limit {f['stale_after_days']})" for f in stale[:5])
        out.append(Finding("fresh:feeds", f"{len(stale)} projection feed(s) are stale: {shown}" + (f" (+{len(stale) - 5} more)" if len(stale) > 5 else "") + ".",
                           "Projections > Freshness lists them; the feeds service (21:00 UTC) or the sheet upload behind each one is the cause.", None, ",".join(names)))
    if errors:
        out.append(Finding("fresh:feed_hooks", "A projection freshness hook crashed: " + "; ".join(f"{e['module']}: {short(e['error'], 100)}" for e in errors[:3]) + ".",
                           "A projection module raised while reporting its freshness; see Railway > etl logs for etl.projection_daily.", None, ",".join(e["module"] for e in errors)))
    return out


def check_web(conn, ctx: Ctx) -> list[Finding]:
    """GET /healthz (no auth, no data; 503 when the web app cannot reach its database). Two tries: a deploy restarts the app for about a minute."""
    url = ctx.web_url.rstrip("/") + "/healthz"
    last = ""
    for attempt in range(2):
        t0 = time.monotonic()
        try:
            r = ctx.http_get(url, timeout=WEB_TIMEOUT_SEC)
            took = time.monotonic() - t0
            if r.status_code == 200 and took <= WEB_SLOW_SEC:
                return []
            last = f"HTTP {r.status_code}" if r.status_code != 200 else f"answered in {took:.1f} s (limit {WEB_SLOW_SEC:g} s)"
        except Exception as e:                                          # noqa: BLE001  (never put the exception text in: it can carry the URL)
            last = f"no answer ({type(e).__name__})"
        if attempt == 0:
            ctx.sleep(WEB_RETRY_WAIT_SEC)
    return [Finding("web:health", f"The web app does not answer its health check ({url}) twice in a row: {last}.",
                    "Railway > web service: look at the latest deployment and its logs; HTTP 503 means the app cannot reach Postgres.", None, "down")]


# a data-gap finding is a symptom of the job that feeds it: when that job already has a finding in the same run, the gap is not reported separately
SYMPTOM_OF = {"fresh:meta": "job:run_daily", "fresh:upstack": "job:upstack_daily", "fresh:orders": "job:upstack_daily", "fresh:google": "job:upstack_daily",
              "fresh:feeds": "job:feeds"}
CHECKS: list[tuple[str, Callable]] = [("etl_jobs", check_etl_jobs), ("heartbeats", check_heartbeats), ("freshness", check_freshness), ("web", check_web)]


def run_checks(conn, ctx: Ctx, checks=None) -> tuple[list[Finding], list[Finding]]:
    """(findings, crashes). A check that raises becomes a crash finding (alert id alerts:check:<name>) and never stops the others."""
    findings: list[Finding] = []
    crashes: list[Finding] = []
    for name, fn in checks or CHECKS:
        try:
            findings += fn(conn, ctx)
        except Exception as e:                                          # noqa: BLE001
            crashes.append(Finding(f"alerts:check:{name}", f"The alerts check '{name}' crashed: {type(e).__name__}: {short(e, 160)}. Alerts for it are NOT being evaluated.",
                                   "Railway > alerts service logs have the traceback; fix etl/alerts.py or the table it reads.", ctx.now, type(e).__name__))
            db.log(f"[alerts] check {name} crashed:", db.scrub(traceback.format_exc()[-1500:]))
        finally:
            try:
                conn.rollback()                                         # a failed query leaves the read transaction aborted
            except Exception:                                           # noqa: BLE001
                pass
    ids = {f.id for f in findings}
    return [f for f in findings if SYMPTOM_OF.get(f.id) not in ids], crashes


# ------------------------------------------------------------------------------------------------------------------------------ notifier
class NotifyError(Exception):
    """Delivery failed. The message never contains the webhook URL."""


class Notifier(Protocol):
    def send(self, text: str) -> None: ...


class SlackNotifier:
    """Slack incoming webhook. The URL lives only in this object; errors carry the HTTP status or the exception class, never the URL."""

    def __init__(self, url: str, post: Callable = requests.post, timeout: float = 10.0):
        self._url, self._post, self._timeout = url, post, timeout

    def send(self, text: str) -> None:
        try:
            r = self._post(self._url, json={"text": text}, timeout=self._timeout)
        except Exception as e:                                          # noqa: BLE001
            raise NotifyError(f"Slack webhook unreachable ({type(e).__name__})") from None
        if r.status_code != 200:
            raise NotifyError(f"Slack webhook answered HTTP {r.status_code}")


def notifier_from_env(env=None) -> Notifier | None:
    url = (env if env is not None else os.environ).get("SLACK_ALERT_WEBHOOK_URL", "").strip()
    return SlackNotifier(url) if url else None


# ------------------------------------------------------------------------------------------------------------------------------ messages + state
def render(kind: str, f: Finding, st: dict | None, now: dt.datetime, web_url: str) -> str:
    since = f.since or (st["first_seen"] if st else None) or now
    if kind == "repeat":
        head = f"STILL FAILING (alert {int((st or {}).get('alert_count') or 0) + 1}, open since {fmt_pt((st or {}).get('first_seen') or since)}): {f.title}"
    elif kind == "changed":
        head = f"CHANGED: {f.title}"
    else:
        head = f"ALERT: {f.title}"
    return f"{head}\nSince {fmt_pt(since)}. Fix: {f.fix}\n{web_url}"


def render_recovered(st: dict, now: dt.datetime) -> str:
    return f"RECOVERED: {st['last_message']} (open since {fmt_pt(st['first_seen'])}, cleared {fmt_pt(now)})"


def load_state(conn, missing_ok: bool) -> dict[str, dict]:
    try:
        return {r["check_id"]: r for r in conn.execute("SELECT * FROM alert_state").fetchall()}
    except psycopg.errors.UndefinedTable:
        conn.rollback()
        if missing_ok:
            return {}
        raise


def plan(findings: list[Finding], state: dict[str, dict], now: dt.datetime) -> list[tuple[str, str, Finding | None, dict | None]]:
    """[(kind, check_id, finding, state_row)]: kind = new | changed | repeat | recovered | resolve_quiet | quiet. Pure."""
    out = []
    seen = set()
    for f in sorted(findings, key=lambda x: x.id):
        if f.id in seen:
            continue
        seen.add(f.id)
        st = state.get(f.id)
        if st is None or st["resolved_at"] is not None:
            out.append(("new", f.id, f, st))
        elif st["last_alerted"] is None:
            out.append(("new", f.id, f, st))                           # seen before but never delivered (webhook unset / Slack down): deliver now
        elif (f.fingerprint or "") != (st["fingerprint"] or ""):
            out.append(("changed", f.id, f, st))
        elif now - st["last_alerted"] >= dt.timedelta(hours=REALERT_H):
            out.append(("repeat", f.id, f, st))
        else:
            out.append(("quiet", f.id, f, st))
    for cid, st in sorted(state.items()):
        if cid not in seen and st["resolved_at"] is None:
            out.append(("recovered" if st["last_alerted"] is not None else "resolve_quiet", cid, None, st))
    order = {"new": 0, "changed": 1, "repeat": 2, "recovered": 3, "resolve_quiet": 4, "quiet": 5}
    return sorted(out, key=lambda a: (order[a[0]], a[1].startswith("fresh:"), a[1]))          # root causes (jobs, web) before their data symptoms when the cap bites


def _upsert(conn, f: Finding, st: dict | None, now: dt.datetime, delivered: bool) -> None:
    """Record that the problem is open. Only a delivered message sets last_alerted / the fingerprint; an undelivered one leaves the row pending."""
    reopened = st is None or st["resolved_at"] is not None
    first = now if reopened else st["first_seen"]
    count = (0 if reopened else int(st["alert_count"])) + (1 if delivered else 0)
    last_alerted = now if delivered else (None if reopened else st["last_alerted"])
    fingerprint = f.fingerprint if (delivered or reopened) else st["fingerprint"]
    conn.execute(
        "INSERT INTO alert_state (check_id, first_seen, last_seen, last_alerted, resolved_at, alert_count, fingerprint, last_message) VALUES (%s,%s,%s,%s,NULL,%s,%s,%s) "
        "ON CONFLICT (check_id) DO UPDATE SET first_seen=EXCLUDED.first_seen, last_seen=EXCLUDED.last_seen, last_alerted=EXCLUDED.last_alerted, resolved_at=NULL, "
        "alert_count=EXCLUDED.alert_count, fingerprint=EXCLUDED.fingerprint, last_message=EXCLUDED.last_message",
        (f.id, first, now, last_alerted, count, fingerprint or "", f.title))


def reconcile(state_conn, findings: list[Finding], now: dt.datetime, notifier: Notifier | None, web_url: str, *, dry_run: bool = False,
              out: Callable[[str], None] = print) -> dict:
    """Plan, send (cap + summary), record. Returns {sent, pending, quiet, recovered, failed_delivery}."""
    state = load_state(state_conn, missing_ok=dry_run)
    actions = plan(findings, state, now)
    sendable = [a for a in actions if a[0] in ("new", "changed", "repeat", "recovered")]
    res = {"sent": 0, "pending": 0, "quiet": sum(1 for a in actions if a[0] == "quiet"), "recovered": 0, "failed_delivery": None, "actions": [(a[0], a[1]) for a in actions]}

    def text_of(a):
        kind, _cid, f, st = a
        return render_recovered(st, now) if kind == "recovered" else render(kind, f, st, now, web_url)

    individual, rest = sendable[:MAX_MESSAGES], sendable[MAX_MESSAGES:]
    messages: list[tuple[str, list]] = [(text_of(a), [a]) for a in individual]
    if rest:
        lines = "\n".join(f"- {a[1]}: {(a[2].title if a[2] else a[3]['last_message'])[:110]}" for a in rest)
        messages.append((f"{len(rest)} more alert(s) (message cap {MAX_MESSAGES} per run), see the web app and Railway logs:\n{lines}\n{web_url}", rest))

    delivered: set[str] = set()
    if dry_run:
        for text, acts in messages:
            out("--- would send ---\n" + text)
        for kind, cid, f, st in actions:
            if kind == "quiet":
                out(f"--- already alerted, quiet until {fmt_pt(st['last_alerted'] + dt.timedelta(hours=REALERT_H))}: {cid}")
        return res
    for text, acts in messages:
        if notifier is None:
            break
        try:
            notifier.send(text)
        except NotifyError as e:
            res["failed_delivery"] = str(e)
            db.log(f"[alerts] WARNING: delivery failed ({e}); alerts stay pending and retry next run")
            break
        res["sent"] += 1
        delivered |= {a[1] for a in acts}
    for kind, cid, f, st in actions:
        if kind in ("new", "changed", "repeat", "quiet"):
            ok = cid in delivered
            _upsert(state_conn, f, st, now, ok)
            if kind != "quiet" and not ok:
                res["pending"] += 1
        elif kind == "recovered" and (cid in delivered or notifier is None):
            state_conn.execute("UPDATE alert_state SET resolved_at=%s, last_seen=%s WHERE check_id=%s", (now, now, cid))
            res["recovered"] += 1
        elif kind == "resolve_quiet":
            state_conn.execute("UPDATE alert_state SET resolved_at=%s, last_seen=%s WHERE check_id=%s", (now, now, cid))
    state_conn.commit()
    return res


# ------------------------------------------------------------------------------------------------------------------------------ main
def _send_crash(notifier: Notifier | None, text: str) -> None:
    db.log("[alerts] " + text)
    if notifier is None:
        db.log("[alerts] WARNING: SLACK_ALERT_WEBHOOK_URL is not set, so this problem was NOT sent to Slack")
        return
    try:
        notifier.send("ALERT: " + text)
    except NotifyError as e:
        db.log(f"[alerts] WARNING: could not send it to Slack either ({e})")


def main(argv: list[str] | None = None, *, env=None, notifier: Notifier | None = None, connect: Callable | None = None, ctx: Ctx | None = None,
         out: Callable[[str], None] = print) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="read everything, write and send nothing; print what would be sent")
    ap.add_argument("--test", action="store_true", help="send one clearly labelled test message to the Slack webhook and exit")
    a = ap.parse_args(argv)
    env = os.environ if env is None else env
    notifier = notifier if notifier is not None else notifier_from_env(env)
    web_url = (env.get("ALERTS_WEB_URL") or DEFAULT_WEB_URL).strip()
    if a.test:
        if notifier is None:
            db.log("[alerts] SLACK_ALERT_WEBHOOK_URL is not set: nothing sent. Create a Slack incoming webhook and set it on the Railway `alerts` service (docs/ALERTS.md).")
            return 2
        try:
            notifier.send(f"TEST ONLY: this is a test message from the Lonvera alerts job (etl.alerts --test). If you can read it the webhook works; no action needed.\n{web_url}")
        except NotifyError as e:
            db.log(f"[alerts] test message FAILED: {e}")
            return 1
        db.log("[alerts] test message sent")
        return 0
    if notifier is None and not a.dry_run:
        db.log("[alerts] WARNING: SLACK_ALERT_WEBHOOK_URL is not set: alerts are only logged here, nothing goes to Slack. Set it on the Railway `alerts` service (docs/ALERTS.md).")
    now = dt.datetime.now(dt.timezone.utc)
    ctx = ctx or Ctx(now=now, web_url=web_url)
    connect = connect or (lambda: psycopg.connect(db.database_url(), row_factory=dict_row))
    try:
        read = connect()
        state_conn = connect()
        if a.dry_run:
            state_conn.read_only = True
    except Exception as e:                                               # noqa: BLE001
        msg = f"the alerts job cannot reach the database ({type(e).__name__}); no check ran. This repeats every hour until it is back."
        if a.dry_run:
            out("DATABASE UNREACHABLE: " + msg)
        else:
            _send_crash(notifier, msg + f" Fix: Railway > Postgres service, then web {web_url}/healthz.")
        return 1
    try:
        read.read_only = True
        findings, crashes = run_checks(read, ctx)
        res = reconcile(state_conn, findings + crashes, ctx.now, notifier, web_url, dry_run=a.dry_run, out=out)
    except Exception as e:                                               # noqa: BLE001  (state table missing, connection lost mid-run ...)
        _send_crash(notifier, f"the alerts job crashed: {type(e).__name__}: {short(e, 200)}. Fix: Railway > alerts service logs.")
        return 1
    finally:
        for c in (read, state_conn):
            try:
                c.close()
            except Exception:                                            # noqa: BLE001
                pass
    n_open = sum(1 for k, _c in res["actions"] if k in ("new", "changed", "repeat", "quiet"))
    db.log(f"[alerts] {len(findings)} finding(s), {len(crashes)} check crash(es); sent {res['sent']}, pending {res['pending']}, quiet {res['quiet']}, recovered {res['recovered']}, open {n_open}"
           + (" (dry run, nothing written or sent)" if a.dry_run else ""))
    if a.dry_run:
        for k, cid in res["actions"]:
            out(f"  {k:14s} {cid}")
    return 1 if (crashes or res["failed_delivery"]) and not a.dry_run else 0


if __name__ == "__main__":
    sys.exit(main())
