"""Ad -> Facebook Page identity (which handle each Meta ad runs from) -> meta_ad_pages (+ the small lookup meta_pages). READ-ONLY Graph GETs, both ad accounts.

  python -m etl.meta_pages                  the nightly call: ads updated in the last 14 days (bulk listing) + any ad with spend in the last 14 days that has no row yet
                                            (batched /?ids= lookups, capped at 200 ids a run), then page names for pages still unnamed
  python -m etl.meta_pages --full           every ad of both accounts, including ARCHIVED (the one-time backfill; resumable: it commits after each page of 500)
  python -m etl.meta_pages --days 30        a wider incremental window
  python -m etl.meta_pages --dry-run        list and print counts, write nothing

Bulk listing: GET /act_<id>/ads?fields=id,created_time,updated_time,creative{effective_object_story_id,object_story_id,object_story_spec{page_id,instagram_user_id},instagram_actor_id}
&effective_status=[...all...]&limit=500, paged by the `after` cursor. When Meta answers "reduce the amount of data" the page size is halved and the same cursor retried.
Rate limits (codes 4, 17, 32, 613, 80000-80014) back off inside meta_api.graph_get. Never one call per ad.
Page id: the prefix of effective_object_story_id ('<page>_<post>'), else of object_story_id, else object_story_spec.page_id. A page the ad names that is not in meta_pages is added
(unnamed, is_lonvera false). Fails LOUDLY (etl_runs status failed): a token / rate-limit failure, or more than 5% of the last 14 days' Meta spend sitting on ads with no page.
Token: META_ACCESS_TOKEN (never printed). Every error text is scrubbed."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time

from . import db, meta_api

CREATIVE = "creative{effective_object_story_id,object_story_id,object_story_spec{page_id,instagram_user_id},instagram_actor_id}"
AD_FIELDS = "id,created_time,updated_time," + CREATIVE
# ARCHIVED is not in Meta's default list, so every status is named; DELETED is not accepted as a list filter (code 100), so deleted ads only come in through the capped /?ids= fallback
STATUSES = json.dumps(["ACTIVE", "PAUSED", "ARCHIVED", "IN_PROCESS", "WITH_ISSUES", "CAMPAIGN_PAUSED", "ADSET_PAUSED", "PENDING_REVIEW",
                       "DISAPPROVED", "PREAPPROVED", "PENDING_BILLING_INFO"])
PAGE_SIZE = 500
MIN_PAGE_SIZE = 25
INCREMENTAL_DAYS = 14
MISSING_CAP = 200                # ids looked up by /?ids= per run for ads with spend and no row yet
UNRESOLVED_FAIL_SHARE = 0.05     # share of the window's Meta spend on ads with no page that fails the run
PACE = 0.4
USAGE_PAUSE_AT = 90.0            # % of the ad account's cpu / time budget (x-business-use-case-usage) at which the listing waits before the next page
MAX_PAUSE = 1800                 # never wait longer than this between two pages (seconds)


# ------------------------------------------------------------------------------------------------------------------ parsing
def _s(x) -> str | None:
    return None if x is None or str(x).strip() == "" else str(x).strip()


def _page_of_story(story) -> str | None:
    s = _s(story)
    if not s or "_" not in s:
        return None
    p = s.split("_", 1)[0]
    return p if p.isdigit() else None


def parse_ad(ad: dict) -> dict:
    """One Graph ad object -> {ad_id, page_id, instagram_user_id, effective_object_story_id, creative_id, source}. Missing creative / no page -> page_id None, source 'unresolved'."""
    cr = ad.get("creative") or {}
    spec = cr.get("object_story_spec") or {}
    eos, os_ = _s(cr.get("effective_object_story_id")), _s(cr.get("object_story_id"))
    page, source = _page_of_story(eos), "effective_object_story_id"
    if not page:
        page, source = _page_of_story(os_), "object_story_id"
    if not page:
        sp = _s(spec.get("page_id"))
        page, source = (sp if sp and sp.isdigit() else None), "object_story_spec"
    ig = _s(spec.get("instagram_user_id")) or _s(cr.get("instagram_actor_id"))
    if not page:
        source = "instagram_only" if ig else "unresolved"          # creator / partnership ads run from an Instagram account with no Page of their own
    return {"ad_id": str(ad["id"]), "page_id": page, "instagram_user_id": ig,
            "effective_object_story_id": eos or os_, "creative_id": _s(cr.get("id")), "source": source}


# ------------------------------------------------------------------------------------------------------------------ Graph
def usage_pause() -> float:
    """Seconds to wait before the next page, from the account usage Meta reported on the last call: the whole 'estimated_time_to_regain_access' when Meta has throttled us, a short pause while
    cpu / time use is high. The ad account's budget is shared with every other job and person on this token (development access tier), so the backfill stays well under it."""
    u = meta_api.LAST_USAGE
    if not u:
        return 0.0
    if u.get("regain_minutes", 0) > 0:
        return min(MAX_PAUSE, u["regain_minutes"] * 60 + 5)
    hot = max(u.get("cpu", 0), u.get("time", 0))
    return min(120.0, (hot - USAGE_PAUSE_AT) * 3 + 10) if hot >= USAGE_PAUSE_AT else 0.0


def next_size(size: int) -> int:
    """Adaptive page size from the account usage Meta reported on the last call: halve when the page used more than the whole budget (cpu / time >= 100%), double (up to PAGE_SIZE) when it used under 20%."""
    u = meta_api.LAST_USAGE
    if not u:
        return size
    hot = max(u.get("cpu", 0), u.get("time", 0))
    if hot >= 100 and size > MIN_PAGE_SIZE:
        return max(MIN_PAGE_SIZE, size // 2)
    if hot < 20 and size < PAGE_SIZE:
        return min(PAGE_SIZE, size * 2)
    return size


def list_ads(tok, acct: str, an: str, since: dt.datetime | None, on_page, sleep=time.sleep) -> int:
    """Bulk-list ads (optionally updated_time >= since), calling on_page(list_of_graph_ads) after every page. Returns the number of ads listed."""
    params = {"access_token": tok, "fields": AD_FIELDS, "limit": PAGE_SIZE, "effective_status": STATUSES}
    if since is not None:
        params["filtering"] = json.dumps([{"field": "updated_time", "operator": "GREATER_THAN", "value": int(since.timestamp())}])
    n, after, size = 0, None, PAGE_SIZE
    while True:
        p = dict(params, limit=size)
        if after:
            p["after"] = after
        try:
            j = meta_api.graph_get(f"{meta_api.G}/{acct}/ads", p, f"{an} ads")
        except meta_api.TooBig:
            if size <= MIN_PAGE_SIZE:
                raise meta_api.GraphError(f"{an} ads: still too big at page size {size}")
            size = max(MIN_PAGE_SIZE, size // 2)
            db.log(f"  {an} ads page too big -> page size {size}")
            continue
        data = j.get("data", [])
        if data:
            on_page(data)
            n += len(data)
        after = (j.get("paging", {}).get("cursors") or {}).get("after") if j.get("paging", {}).get("next") else None
        if not after:
            return n
        size = next_size(size)
        wait = usage_pause()
        if wait:
            db.log(f"  {an} ads: account usage {meta_api.LAST_USAGE} -> waiting {wait:.0f}s before the next page")
        sleep(max(PACE, wait))


def lookup_ids(tok, ids: list[str], batch: int = 50, sleep=time.sleep) -> list[dict]:
    """The capped fallback: /?ids=a,b,c&fields=... in batches of 50 (one call per 50 ads, never per ad). A batch Meta refuses is skipped (its ads stay unresolved)."""
    out = []
    for i in range(0, len(ids), batch):
        try:
            j = meta_api.graph_get(f"{meta_api.G}/", {"ids": ",".join(ids[i:i + batch]), "fields": "id," + CREATIVE, "access_token": tok}, "ad page lookup", max_retries=4)
        except meta_api.TokenRejected:
            raise
        except (meta_api.GraphError, meta_api.TooBig) as e:
            db.log(f"  ad page lookup batch {i // batch} skipped: {db.scrub(e)[:120]}")
            continue
        out += [v for v in j.values() if isinstance(v, dict) and v.get("id")]
        sleep(PACE)
    return out


def page_names(tok, page_ids: list[str]) -> dict[str, str]:
    """Name of each page the token can read (one batched call; pages it cannot read are simply absent)."""
    if not page_ids:
        return {}
    try:
        j = meta_api.graph_get(f"{meta_api.G}/", {"ids": ",".join(page_ids[:50]), "fields": "id,name", "access_token": tok}, "page names", max_retries=2)
    except (meta_api.GraphError, meta_api.TooBig) as e:
        db.log(f"  page names not resolved: {db.scrub(e)[:120]}")
        return {}
    return {str(k): v["name"] for k, v in j.items() if isinstance(v, dict) and v.get("name")}


# ------------------------------------------------------------------------------------------------------------------ database
def upsert(conn, account: str, parsed: list[dict], today: dt.date) -> int:
    if not parsed:
        return 0
    db.execmany(conn, """INSERT INTO meta_ad_pages (ad_id, ad_account, page_id, instagram_user_id, effective_object_story_id, creative_id, first_seen, last_seen, source)
                         VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                         ON CONFLICT (ad_id) DO UPDATE SET ad_account = EXCLUDED.ad_account, last_seen = EXCLUDED.last_seen, loaded_at = now(),
                           page_id = coalesce(EXCLUDED.page_id, meta_ad_pages.page_id), source = CASE WHEN EXCLUDED.page_id IS NULL AND meta_ad_pages.page_id IS NOT NULL THEN meta_ad_pages.source ELSE EXCLUDED.source END,
                           instagram_user_id = coalesce(EXCLUDED.instagram_user_id, meta_ad_pages.instagram_user_id),
                           effective_object_story_id = coalesce(EXCLUDED.effective_object_story_id, meta_ad_pages.effective_object_story_id),
                           creative_id = coalesce(EXCLUDED.creative_id, meta_ad_pages.creative_id)""",
                [(p["ad_id"], account, p["page_id"], p["instagram_user_id"], p["effective_object_story_id"], p["creative_id"], today, today, p["source"]) for p in parsed])
    pages = sorted({p["page_id"] for p in parsed if p["page_id"]})
    if pages:
        db.execmany(conn, "INSERT INTO meta_pages (page_id) VALUES (%s) ON CONFLICT (page_id) DO NOTHING", [(x,) for x in pages])
    return len(parsed)


def missing_ids(conn, today: dt.date, days: int = INCREMENTAL_DAYS, cap: int = MISSING_CAP) -> list[str]:
    """Ads with Meta spend in the last `days` days and no meta_ad_pages row yet, biggest spend first."""
    rows = conn.execute("""SELECT d.ad_id FROM ad_daily d LEFT JOIN meta_ad_pages p ON p.ad_id = d.ad_id
                           WHERE d.day > %s AND d.spend > 0 AND p.ad_id IS NULL GROUP BY d.ad_id ORDER BY sum(d.spend) DESC LIMIT %s""",
                        (today - dt.timedelta(days=days), cap)).fetchall()
    return [r[0] for r in rows]


def unresolved_share(conn, today: dt.date, days: int = INCREMENTAL_DAYS) -> tuple[float, float, int]:
    """(spend with no page identity at all, total spend, ads with none) over the last `days` days. An Instagram-only ad (creator / partnership, source 'instagram_only') HAS an identity: its Instagram account."""
    r = conn.execute("""SELECT coalesce(sum(d.spend) FILTER (WHERE p.page_id IS NULL AND coalesce(p.source, 'unresolved') = 'unresolved'), 0), coalesce(sum(d.spend), 0), count(DISTINCT d.ad_id) FILTER (WHERE p.page_id IS NULL AND coalesce(p.source, 'unresolved') = 'unresolved' AND d.spend > 0)
                        FROM ad_daily d LEFT JOIN meta_ad_pages p ON p.ad_id = d.ad_id WHERE d.day > %s""", (today - dt.timedelta(days=days),)).fetchone()
    return float(r[0]), float(r[1]), int(r[2])


def spend_first(conn, tok, today: dt.date, since: dt.date, sleep=time.sleep, batch: int = 50) -> dict:
    """Backfill in order of spend: ads with Meta spend since `since` and no row, biggest spend first, `batch` ids per /?ids= call (halved when the account is hot, doubled back when cheap),
    paced by the reported usage. Ads Meta does not return (deleted) get an 'unresolved' row so they leave the queue. Resumable: it only ever works on ads with no row."""
    tried, st = set(), {"looked_up": 0, "unresolved": 0, "calls": 0}
    days = (today - since).days
    while True:
        ids = [i for i in missing_ids(conn, today, days, cap=batch + len(tried)) if i not in tried][:batch]
        if not ids:
            return st
        tried.update(ids)
        found = lookup_ids(tok, ids, batch=batch, sleep=sleep)
        st["calls"] += 1
        acct_of = dict(conn.execute("SELECT ad_id, account FROM meta_ads WHERE ad_id = ANY(%s)", (ids,)).fetchall())
        got = {str(f["id"]) for f in found}
        for an in {acct_of.get(str(f["id"]), "Lonvera Main") for f in found}:
            upsert(conn, an, [parse_ad(f) for f in found if acct_of.get(str(f["id"]), "Lonvera Main") == an], today)
        gone = [i for i in ids if i not in got]
        if gone:
            upsert(conn, "Lonvera Main", [{"ad_id": i, "page_id": None, "instagram_user_id": None, "effective_object_story_id": None, "creative_id": None, "source": "unresolved"} for i in gone], today)
        conn.commit()
        st["looked_up"] += len(found)
        st["unresolved"] += len(gone)
        batch = next_size(batch) if batch else batch
        batch = max(10, min(50, batch))
        wait = usage_pause()
        db.log(f"  spend-first: {st['looked_up']} resolved, {st['unresolved']} unresolved, usage {meta_api.LAST_USAGE or '-'}" + (f", waiting {wait:.0f}s" if wait else ""))
        sleep(max(PACE, wait))


def run(full: bool = False, days: int = INCREMENTAL_DAYS, dry: bool = False, today: dt.date | None = None, sleep=time.sleep, spend_since: dt.date | None = None) -> dict:
    today = today or dt.datetime.now(meta_api.PT).date()
    with db.JobRun("meta_pages") as jr, db.connect() as conn:
        tok = meta_api.get_token()
        if spend_since:
            stats = {"mode": f"spend-first since {spend_since}"}
            jr.stats = stats
            try:
                stats.update(spend_first(conn, tok, today, spend_since, sleep=sleep))
            except meta_api.TokenRejected:
                raise db.JobFailed("meta token rejected") from None
            except (meta_api.GraphError, meta_api.TooBig) as e:
                raise db.JobFailed(f"spend-first lookup failed: {db.scrub(e)[:300]}") from None
            jr.rows_written, jr.data_through = stats["looked_up"] + stats["unresolved"], today
            return stats
        since = None if full else dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
        stats: dict = {"mode": "full" if full else f"incremental {days}d", "listed": {}, "looked_up": 0, "written": 0}
        jr.stats = stats
        for acct, an in meta_api.ACCOUNTS:
            written = [0]

            def on_page(data, an=an, written=written):
                parsed = [parse_ad(a) for a in data]
                if not dry:
                    written[0] += upsert(conn, an, parsed, today)
                    conn.commit()
            try:
                stats["listed"][an] = list_ads(tok, acct, an, since, on_page, sleep=sleep)
            except meta_api.TokenRejected:
                raise db.JobFailed("meta token rejected") from None
            except (meta_api.GraphError, meta_api.TooBig) as e:
                raise db.JobFailed(f"{an} ad listing failed: {db.scrub(e)[:300]}") from None
            stats["written"] += written[0]
        if not full:
            ids = missing_ids(conn, today, days)
            stats["missing_before_lookup"] = len(ids)
            if ids:
                try:
                    found = lookup_ids(tok, ids, sleep=sleep)
                except meta_api.TokenRejected:
                    raise db.JobFailed("meta token rejected") from None
                stats["looked_up"] = len(found)
                if not dry:
                    acct_of = dict(conn.execute("SELECT ad_id, account FROM meta_ads WHERE ad_id = ANY(%s)", (ids,)).fetchall())
                    for an in {acct_of.get(str(f["id"]), "Lonvera Main") for f in found}:
                        stats["written"] += upsert(conn, an, [parse_ad(f) for f in found if acct_of.get(str(f["id"]), "Lonvera Main") == an], today)
                    conn.commit()
        if dry:
            return stats
        unnamed = [r[0] for r in conn.execute("SELECT page_id FROM meta_pages WHERE name IS NULL ORDER BY page_id").fetchall()]
        names = page_names(tok, unnamed)
        for pid, nm in names.items():
            conn.execute("UPDATE meta_pages SET name = %s, loaded_at = now() WHERE page_id = %s AND name IS NULL", (nm, pid))
        conn.commit()
        stats["pages_named"] = len(names)
        stats["pages_total"] = conn.execute("SELECT count(*) FROM meta_pages").fetchone()[0]
        spend_no_page, spend_all, n_ads = unresolved_share(conn, today, days)
        stats["window_spend"], stats["window_spend_no_page"], stats["window_ads_no_page"] = round(spend_all, 2), round(spend_no_page, 2), n_ads
        jr.rows_written = stats["written"]
        jr.data_through = today
        if spend_all > 0 and spend_no_page / spend_all > UNRESOLVED_FAIL_SHARE:
            raise db.JobFailed(f"{spend_no_page / spend_all:.1%} of the last {days} days' Meta spend (${spend_no_page:,.0f}, {n_ads} ads) has no page identity")
        if n_ads:
            jr.note = f"{n_ads} ads with spend in the last {days} days have no page identity (${spend_no_page:,.0f} of ${spend_all:,.0f})"
        return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--days", type=int, default=INCREMENTAL_DAYS)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--spend-since", help="backfill by spend: ads with Meta spend since this day and no row, biggest first (e.g. 2026-05-06)")
    a = ap.parse_args()
    try:
        st = run(a.full, a.days, a.dry_run, spend_since=dt.date.fromisoformat(a.spend_since) if a.spend_since else None)
    except Exception as e:                                                    # noqa: BLE001
        db.log("meta_pages FAILED:", db.scrub(e))
        sys.exit(1)
    db.log("meta_pages ok:", json.dumps(st, default=str))


if __name__ == "__main__":
    main()
