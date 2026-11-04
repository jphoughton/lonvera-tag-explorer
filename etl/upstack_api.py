"""Upstack API client + the two pulls the Upstack source needs (read-only; nothing here touches the database).

  * attribution  /api/query-attribution, any-click, 30-day window, utm_source "Meta Ads": one call per Pacific day for campaign totals
    (dimension utm_campaign), then one call per campaign with orders for the ad split (dimension utm_ad; Upstack answers 502 on utm_ad without a
    campaign filter). Same method as lonvera-sync-main/upstack_ads_daily.py, the job behind the sheet's 'Upstack Ads - Daily' tab.
  * channel      /api/query-channel-details at campaign grain: the ad platform's own feed delivered through Upstack (spend, platform-reported
    purchases), Meta only. Used for the spend tie-out; Upstack's ad-grain channel feed is not read (63,960 ads, no spend sort, so a complete
    read would take hours).

Credentials: env UPSTACK_API_KEY / UPSTACK_PIXEL_ID (Railway `etl` service). For local runs only, the same two values are read from
~/.upstackrc when the env vars are unset. They are sent as headers and never logged.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor

import requests

from . import db

BASE = "https://api.upstackdata.com/db-mgmt"
CLI_VERSION = "1.0.7"
TIMEZONE = "America/Los_Angeles"
MODEL, WINDOW = "any_click", 30
SOURCE_FILTER = {"field": "attribution.utm_source", "op": "equals", "value": "Meta Ads"}
NOT_IDENTIFIED = "(ad not identified)"
MEASURES = ("orders.new_customers", "orders.new_customer_total_revenue", "orders.count", "orders.total_revenue")
CHANNEL_MEASURES = ("channel.spend", "channel.purchases", "channel.purchases_value", "channel.new_customer_purchases",
                    "channel.new_customer_purchases_value", "channel.link_clicks")
CHANNEL_PAGE = 250
META_INTEGRATION = "facebookIntegration"
MODEL_LABEL = "Upstack any-click attribution, 30-day window, Meta Ads source"


class UpstackError(RuntimeError):
    pass


def credentials() -> tuple[str, str]:
    key, pixel = os.environ.get("UPSTACK_API_KEY"), os.environ.get("UPSTACK_PIXEL_ID")
    if not (key and pixel):
        rc = os.path.expanduser("~/.upstackrc")
        if os.path.exists(rc):
            try:
                prof = json.load(open(rc))["profiles"]["default"]
                key, pixel = key or prof.get("apiKey"), pixel or prof.get("pixelId")
            except (OSError, ValueError, KeyError):
                pass
    if not (key and pixel):
        raise UpstackError("UPSTACK_API_KEY / UPSTACK_PIXEL_ID are not set")
    return key, pixel


def num(x) -> float:
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


class Client:
    def __init__(self, key: str | None = None, pixel: str | None = None):
        k, p = (key, pixel) if key and pixel else credentials()
        self.pixel = p
        self._headers = {"x-api-key": k, "x-pixel-id": p, "x-upstack-cli-version": CLI_VERSION, "Content-Type": "application/json"}
        self.calls = 0

    def post(self, path: str, body: dict, what: str, retries: int = 7) -> dict:
        last = None
        for i in range(retries):
            try:
                self.calls += 1
                r = requests.post(BASE + path, headers=self._headers, json=body, timeout=180)
                if r.status_code == 200:
                    return r.json()
                last = f"HTTP {r.status_code}: {db.scrub(r.text[:120])}"
                if 400 <= r.status_code < 500 and r.status_code != 429:
                    raise UpstackError(f"{what}: {last}")
            except UpstackError:
                raise
            except Exception as e:                                           # noqa: BLE001  (network / JSON errors are retried)
                last = db.scrub(repr(e)[:150])
            time.sleep(min(10 * 2 ** i, 120) if i else 5)
        raise UpstackError(f"{what} failed after {retries} tries: {last}")

    # ---- attribution --------------------------------------------------------------------------------------------------
    def attribution(self, day: dt.date, dimension: str, extra_filters: list[dict] | None = None, source: str = "Meta Ads") -> list[dict]:
        src = SOURCE_FILTER if source == "Meta Ads" else {**SOURCE_FILTER, "value": source}
        body = {"pixelId": self.pixel, "dimension": dimension, "dateRange": {"start": day.isoformat(), "end": day.isoformat()},
                "attributionModel": MODEL, "attributionWindow": WINDOW, "timezone": TIMEZONE, "valueSource": "orders",
                "filters": {"and": [src] + (extra_filters or [])}}
        res = self.post("/api/query-attribution", body, f"{day} {dimension}").get("results") or []
        return ((res[0].get("data") or {}).get("primary") or []) if res else []

    # ---- channel feed (campaign grain) --------------------------------------------------------------------------------
    def channel_campaigns(self, start: dt.date, end: dt.date, measures: tuple = CHANNEL_MEASURES) -> list[dict]:
        out, offset = [], 0
        while True:
            body = {"pixelId": self.pixel, "granularity": "day", "timezone": TIMEZONE, "limit": CHANNEL_PAGE, "offset": offset,
                    "sortBy": "sourceUpdatedAt", "sortDirection": "desc",
                    "dateRange": {"start": start.isoformat(), "end": end.isoformat()},
                    "measures": [{"measure": m} for m in measures], "dimensions": ["campaign"]}
            j = self.post("/api/query-channel-details", body, f"channel campaigns {start}..{end} @{offset}")
            out += j.get("results") or []
            pg = j.get("pagination") or {}
            if not pg.get("hasMore") or not (j.get("results") or []):
                return out
            offset += CHANNEL_PAGE


# ---------------------------------------------------------------------------------------------------------------- shaping (pure)

def _m(x: dict) -> list[float]:
    return [num(x.get(k)) for k in MEASURES]


def campaign_rows(primary: list[dict]) -> list[list]:
    """-> [[campaign, nc, nc_rev, orders, rev]] for campaigns with any activity; the same name on several rows is summed."""
    out: dict[str, list[float]] = {}
    for x in primary:
        m = _m(x)
        if not any(m):
            continue
        name = str(x.get("utm_campaign") or "")
        out[name] = [a + b for a, b in zip(out.get(name, [0.0] * 4), m)]
    return [[k] + v for k, v in out.items()]


def ad_rows(campaign: str, primary: list[dict]) -> list[list]:
    """-> [[campaign, ad, nc, nc_rev, orders, rev]] summed by ad name (the same ad runs in several ad sets)."""
    out: dict[str, list[float]] = {}
    for x in primary:
        m = _m(x)
        if not any(m):
            continue
        ad = str(x.get("utm_ad") or "(not set)")
        out[ad] = [a + b for a, b in zip(out.get(ad, [0.0] * 4), m)]
    return [[campaign, k] + v for k, v in out.items()]


def needs_ads(c: list) -> bool:
    return c[1] > 0 or c[3] > 0                     # new customers or orders


def day_record(camps: list[list], ads_by_camp: dict[str, list[list]]) -> dict:
    """One pulled day. A campaign whose ad call came back empty although it had orders is kept at campaign totals as ad '(ad not identified)'."""
    ads, unid = [], []
    for c in camps:
        if not needs_ads(c):
            continue
        got = ads_by_camp.get(c[0]) or []
        if not any(r[2] or r[4] for r in got):
            ads.append([c[0], NOT_IDENTIFIED] + c[1:5])
            unid.append(c[0])
        else:
            ads += got
    return {"camps": camps, "ads": ads, "unidentified": unid,
            "nc_campaigns": round(sum(c[1] for c in camps), 2), "nc_ads": round(sum(a[2] for a in ads), 2)}


def pull_day(client: Client, day: dt.date, workers: int = 4) -> dict:
    camps = campaign_rows(client.attribution(day, "utm_campaign"))
    todo = [c[0] for c in camps if needs_ads(c)]

    def one(c: str):
        try:
            return c, ad_rows(c, client.attribution(day, "utm_ad", [{"field": "attribution.utm_campaign", "op": "equals", "value": c}]))
        except UpstackError as e:
            if re.search(r"HTTP 4(?!29)\d\d", str(e)):       # filter rejected outright (odd campaign name): not identifiable, not a failure
                return c, []
            raise
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        got = dict(ex.map(one, todo))
    rec = day_record(camps, got)
    rec["calls"] = 1 + len(todo)
    return rec


def channel_rows(results: list[dict]) -> list[tuple]:
    """query-channel-details results (campaign dimension) -> [(day, campaign_id, campaign_name, spend, purchases, purchases_value, nc, nc_value, link_clicks)]
    for Meta campaigns only."""
    out = []
    for r in results:
        bd = r.get("basicDetails") or {}
        if bd.get("resourceSource") != META_INTEGRATION:
            continue
        cid, name = str(r.get("campaign") or ""), r.get("campaignName") or bd.get("resourceName") or ""
        for p in (r.get("data") or {}).get("primary") or []:
            vals = [num(p.get(m)) for m in CHANNEL_MEASURES]
            if cid and any(vals):
                out.append((dt.date.fromisoformat(str(p["dt"])[:10]), cid, name, *vals))
    return out
