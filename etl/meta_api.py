"""Minimal read-only Meta Graph client: token loading, GET with retry/backoff on rate limits (incl. code 80004), insights pull with split/async
fallback (ported from lonvera-upstack-sync/meta_history.py). Tokens are never printed or logged; every exception text is scrubbed."""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import time

from . import db
from .resolver import pick, pick_clicks

G = "https://graph.facebook.com/v23.0"
ACCOUNTS = [("act_1671338033619223", "Lonvera Main"), ("act_1543579384022317", "Lonvera BackUp")]
CHUNK_DAYS = 14
RATE_CODES = {4, 17, 32, 613, 80000, 80001, 80002, 80003, 80004, 80005, 80006, 80008, 80009, 80014}
LOCAL_TOK_DIR = "/Users/joshhoughton/Desktop/Google Sheets/lonvera_creative_key"

INSIGHT_FIELDS = "ad_id,ad_name,campaign_id,campaign_name,adset_id,adset_name,spend,actions,action_values,inline_link_clicks,date_start,date_stop"


class TooBig(Exception):
    pass


class GraphError(Exception):
    pass


class RateLimited(GraphError):
    pass


def get_token() -> str:
    t = os.environ.get("META_ACCESS_TOKEN")
    if t:
        return t
    try:                                                    # local dev: the same helper the Creative Key scripts use
        sys.path.insert(0, LOCAL_TOK_DIR)
        from meta_tok import token                          # type: ignore
        return token()
    except Exception as e:                                  # noqa: BLE001
        raise GraphError("no Meta token available (set META_ACCESS_TOKEN): " + db.scrub(e)) from None


def graph_get(url: str, params: dict | None, what: str, max_retries: int = 7, sleep=time.sleep):
    import requests
    fails = 0
    while True:
        code, j = 0, None
        try:
            r = requests.get(url, params=params, timeout=180)
            code = r.status_code
            j = r.json()
        except Exception as e:                              # noqa: BLE001 — network / JSON errors are retried
            j = {"error": {"message": db.scrub(e)[:200], "code": -1}}
        if "error" not in j:
            return j
        err = j["error"]
        msg = db.scrub(err.get("message", ""))
        ecode = err.get("code")
        if "reduce the amount of data" in msg.lower() or ecode == 1:
            raise TooBig(msg)
        rate = code == 429 or ecode in RATE_CODES or "too many calls" in msg.lower() or "rate limit" in msg.lower()
        transient = code >= 500 or ecode in (2, -1) or code == 0
        if not (rate or transient):
            raise GraphError(f"{what}: HTTP {code} code {ecode}: {msg[:200]}")
        fails += 1
        if fails > max_retries:
            raise (RateLimited if rate else GraphError)(f"{what}: gave up after {max_retries} retries: HTTP {code} code {ecode}: {msg[:160]}")
        wait = min(900, 60 * 2 ** (fails - 1)) if rate else 15 * fails
        db.log(f"  {what}: {'rate limit' if rate else 'transient error'} (HTTP {code}, code {ecode}) retry {fails}/{max_retries} in {wait}s")
        sleep(wait)


def rows_from(data) -> list[dict]:
    out = []
    for a in data:
        out.append({"day": dt.date.fromisoformat(a["date_start"]), "ad_id": str(a["ad_id"]), "ad_name": str(a.get("ad_name", "")),
                    "campaign_id": str(a.get("campaign_id", "") or ""), "campaign_name": str(a.get("campaign_name", "") or ""),
                    "adset_id": str(a.get("adset_id", "") or ""), "adset_name": str(a.get("adset_name", "") or ""),
                    "spend": float(a.get("spend") or 0), "purchases": pick(a.get("actions")), "value": pick(a.get("action_values")),
                    "clicks": pick_clicks(a)})
    return out


def _paged(url, params, what, pace=0.4):
    rows = []
    while url:
        j = graph_get(url, params, what)
        rows += rows_from(j.get("data", []))
        url = j.get("paging", {}).get("next")
        params = None
        time.sleep(pace)
    return rows


def _async(tok, acct, since, until, an):
    import requests
    p = {"access_token": tok, "level": "ad", "time_increment": 1, "fields": INSIGHT_FIELDS,
         "time_range": json.dumps({"since": since.isoformat(), "until": until.isoformat()}),
         "filtering": json.dumps([{"field": "spend", "operator": "GREATER_THAN", "value": 0}])}
    try:
        rid = requests.post(f"{G}/{acct}/insights", data=p, timeout=120).json().get("report_run_id")
    except Exception as e:                                  # noqa: BLE001
        raise GraphError(f"{an}: async report did not start: {db.scrub(e)[:120]}") from None
    if not rid:
        raise GraphError(f"{an}: async report did not start")
    for _ in range(120):
        time.sleep(10)
        s = graph_get(f"{G}/{rid}", {"access_token": tok}, an + " async poll")
        if s.get("async_status") == "Job Completed":
            break
        if s.get("async_status") in ("Job Failed", "Job Skipped"):
            raise GraphError(f"{an}: async job {s.get('async_status')}")
    else:
        raise GraphError(f"{an}: async job timed out")
    return _paged(f"{G}/{rid}/insights", {"access_token": tok, "limit": 200}, an + " async")


def pull_chunk(tok, acct, an, since: dt.date, until: dt.date) -> list[dict]:
    """Ad-level daily insights for one account, [since, until]. Splits the window on 'reduce the amount of data', async at 1 day."""
    params = {"access_token": tok, "level": "ad", "time_increment": 1, "limit": 500, "fields": INSIGHT_FIELDS,
              "time_range": json.dumps({"since": since.isoformat(), "until": until.isoformat()}),
              "filtering": json.dumps([{"field": "spend", "operator": "GREATER_THAN", "value": 0}])}
    try:
        rows = _paged(f"{G}/{acct}/insights", params, f"{an} {since}..{until}")
    except TooBig as e:
        if since < until:
            mid = since + (until - since) // 2
            db.log(f"  {an} {since}..{until} too big -> splitting ({str(e)[:50]})")
            return pull_chunk(tok, acct, an, since, mid) + pull_chunk(tok, acct, an, mid + dt.timedelta(days=1), until)
        db.log(f"  {an} {since} too big at 1 day -> async")
        rows = _async(tok, acct, since, until, an)
    for r in rows:
        r["account"] = an
    return rows


def account_spend(tok, acct, since, until) -> float:
    j = graph_get(f"{G}/{acct}/insights", {"access_token": tok, "fields": "spend",
                                           "time_range": json.dumps({"since": since.isoformat(), "until": until.isoformat()})}, "account spend")
    return float(j["data"][0]["spend"]) if j.get("data") else 0.0


def date_chunks(since: dt.date, until: dt.date, days: int = CHUNK_DAYS):
    out, d = [], since
    while d <= until:
        e = min(d + dt.timedelta(days=days - 1), until)
        out.append((d, e))
        d = e + dt.timedelta(days=1)
    return out


def fetch_ad_meta(tok, ad_ids: list[str], batch: int = 50, pace: float = 0.6) -> dict[str, dict]:
    """created_time / effective_status / name for specific ads (best effort; stops quietly if rate-limited)."""
    out = {}
    for i in range(0, len(ad_ids), batch):
        chunk = ad_ids[i:i + batch]
        try:
            j = graph_get(f"{G}/", {"ids": ",".join(chunk), "fields": "name,created_time,effective_status", "access_token": tok}, "ad meta", max_retries=3)
        except GraphError as e:
            db.log(f"  ad meta fetch stopped at {i}/{len(ad_ids)}: {str(e)[:120]}")
            break
        for k, v in j.items():
            if isinstance(v, dict):
                out[str(k)] = {"created": (v.get("created_time") or "")[:10] or None, "status": v.get("effective_status"), "name": v.get("name")}
        time.sleep(pace)
    return out
