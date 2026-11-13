"""Meta Ads breakdowns: every Insights API breakdown, pulled at ad x day grain (read only) into meta_breakdown_daily, with meta_breakdown_coverage saying what exists.

READ ONLY. Requests are GETs to /act_<id>/insights through etl.meta_api.graph_get (the repo's read client: retry and back-off on rate limits including code 80004).
No etl.meta_write. The one non-GET call is the standard async report run, `POST /act_<id>/insights`, used ONLY when a plain paged GET for a one-day window is too big or keeps
timing out (it creates a report, writes nothing to the ad account); sets that needed it are listed in the run stats (`async_sets`). The token is never printed: all logged or
stored text goes through db.scrub.

Every set is pulled at ad level. Only when Meta itself refuses a set at ad level is it pulled at campaign level, then account level (the refusal is kept in
meta_breakdown_coverage, one row per set and level).

Nightly (default): trailing --days (7) re-pulled and upserted, one window.
Backfill (--since D and/or --max-days N): NEWEST window first (--window-days, default 30), every set in a window (cheap sets first, region / dma / hourly last) fully
committed before the next older window starts; progress is recorded per window x set x account, so running the same command again resumes. Pass the same --until
after midnight to resume cleanly. On a rate limit it waits and continues.

Service mode (`--auto`, the dedicated Railway cron service, every 2 hours): the backfill anchor (--until and --max-days) is stored in meta_breakdown_progress on first start, so no
arguments are needed to resume. Each run: (1) once a day per set, a catch-up of the newest days (at least the trailing 7; sets already refreshed that day are skipped, so a catch-up
that did not finish resumes next run) limited to 40% of the run's time; (2) the backfill, newest 30-day window first, for every set (except the permanently excluded ones, see EXCLUDED_SETS) out to the full depth.
Sets Meta refuses are asked again after 7 days; a set whose response never has the requested keys, or that fails 3 runs in a row with no rows, is quarantined for 7 days;
retry waiting on transient errors is capped at 90 s without a successful request. A run never starts a request batch in the quiet hours (META_BREAKDOWNS_QUIET_UTC, default 12:30-15:30, the main nightly
Meta pull), stops after --max-minutes (default 110) and exits 0 so the next cron run resumes; a Postgres advisory lock keeps two runs from overlapping (the second exits 0).

Usage: python -m etl.meta_breakdowns --auto
       python -m etl.meta_breakdowns [--days 7 | --since D --until D | --max-days 365] [--window-days 30] [--sets 'age;age+gender'] [--dry-run] [--restart]
       python -m etl.meta_breakdowns --list | --probe
Docs: docs/META_BREAKDOWNS.md."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import re
import sys
import time
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from . import db, meta_api
from .resolver import pick, pick_atc, pick_ic

PT = ZoneInfo("America/Los_Angeles")
LIMIT = 500
CHUNK_DAYS = 7                       # request window; halved when Meta says "reduce the amount of data"
HEAVY_CHUNK_DAYS = 1                 # region, dma, hourly ...: one day per request
PACE = 0.4                           # seconds between pages (same as etl.meta_api paging)
SET_PAUSE = 1.0                      # seconds between breakdown sets
RATE_WAIT = 900                      # seconds to sleep after the client's own retries are used up, before trying the same window again
RECHECK_UNSUPPORTED_DAYS = 7         # a (set, level) Meta refused is asked again after this long (always asked when named with --sets)
QUARANTINE_DAYS = 7                  # a set that fails deterministically (wrong response shape) or keeps failing is not asked again for this long
MAX_BUDGET_SPLITS = 4                # per set pull: how many times a request window that keeps failing (after the set has worked) is split in two / sent as async
FAIL_LIMIT = 3                       # consecutive failing runs with no rows before a set is quarantined
TRANSIENT_BUDGET_S = 90              # retry waiting (HTTP 5xx, "temporarily unavailable") one set may spend per set pull (all pages, both accounts); then it is an error for this run
CATCHUP_SHARE = 0.4                  # at most this share of a run's time goes to the recent-days catch-up; the rest is the backfill's
# PERMANENT EXCLUDE LIST (Josh, 2026-11-13: "dont pull any of the heavy ones ever; focus on the other ones going back 365 days"). These sets are NEVER requested, in any phase
# (catch-up, backfill, retries, async, probe, --sets, --fill-funnel): select_sets drops them. The last one is a redundant combination of two sets that are pulled anyway.
# Rows already stored for them stay; coverage shows them as excluded. META_BREAKDOWNS_EXCLUDE (set names separated by ';', a plus works inside a set like --sets) can only ADD to this list, never remove.
EXCLUDED_SETS = ("comscore_market", "region", "hourly_stats_aggregated_by_audience_time_zone", "hourly_stats_aggregated_by_advertiser_time_zone",
                 "publisher_platform,platform_position,impression_device")
EXCLUDED_NOTE = "EXCLUDED: not pulled any more (permanent exclude list, 2026-11-13); rows already stored are kept"
PURCHASE_TYPES = ("omni_purchase", "purchase", "offsite_conversion.fb_pixel_purchase")      # same priority as etl.resolver.pick
LEVELS = ("ad", "campaign", "account")
MAX_DIMS = 3
ASYNC_POLL_S = 5
ASYNC_POLLS = 120                    # 10 minutes per async report job
RETENTION_RX = re.compile(r"retention|too far|37 months|months? (ago|back)|beyond|older than|before .{0,20}(date|year)|not available for|start date|since", re.I)
DEFAULT_MAX_DAYS = 365
DEFAULT_QUIET_UTC = "12:30-15:30"
DEFAULT_MAX_MINUTES = 110.0
LOCK_KEY = 240_240                   # pg_try_advisory_lock key: one breakdown run at a time
ANCHOR_SET, DONE_SET, CAUGHT_SET = "__anchor__", "__backfill_done__", "__caught_up__"     # bookkeeping rows in meta_breakdown_progress (level and account '-')
CHUNK_PREFIX = "__chunk__"            # record of one committed request window inside a window that is not finished yet (same key shape as a window record, name prefixed)
CATCHUP_PREFIX = "__catchup__"       # per set x level x account stamp 'refreshed on day D' (level = '<level>:<D>'): the recent-days catch-up resumes and skips what is done today
QUARANTINE_PREFIX, FAILS_PREFIX, ASYNC_PREFIX = "__quarantine__:", "__fails__:", "__async__:"     # per-set rows (level and account '-'), see set_quarantine / note_outcome / mark_async_first


class Unsupported(Exception):
    """Meta refused the request itself (invalid parameter, breakdown not available, combination not allowed)."""


class ShapeError(Exception):
    """The response does not have the shape the catalog entry expects (for example no dimension keys)."""


class StopRun(Exception):
    """The run must stop cleanly (quiet hours, wall-clock budget): everything pulled so far is committed."""


class SetBudgetExceeded(meta_api.GraphError):
    """A set used up its retry-wait budget on transient failures: an error for this run (a later run tries again)."""


class AsyncTimeout(meta_api.GraphError):
    """An async report job did not finish in time."""


class TooBigError(Exception):
    """Too large even for one day and the async report failed."""


# ---------------------------------------------------------------------------------------------------------------- catalog
@dataclass(frozen=True)
class Entry:
    breakdowns: tuple[str, ...]
    action: bool = False             # True: the names are ACTION breakdowns (split only purchases and purchase value, via action_breakdowns)
    label: str | None = None         # set name when it must differ from the joined names (keeps action and standard readings of one name apart)
    tier: int = 1                    # 0 cheap (few rows per ad), 1 normal, 2 heavy (region, dma, hourly ...): pulled last, one day per request
    note: str = ""

    @property
    def name(self) -> str:
        return self.label or ",".join(self.breakdowns)

    @property
    def chunk_days(self) -> int:
        return HEAVY_CHUNK_DAYS if self.tier >= 2 else CHUNK_DAYS


def _e(names: str, tier: int = 1, action: bool = False, label: str | None = None, note: str = "") -> Entry:
    b = tuple(names.split(","))
    assert len(b) <= MAX_DIMS, names
    return Entry(b, action, label, tier, note)


# Every breakdown Meta documents for the Insights API at the Graph version in etl.meta_api.G (the enums shipped in Meta's own Python SDK), plus useful combinations.
# A name Meta no longer serves, or serves only to some accounts, is recorded in meta_breakdown_coverage as unsupported and skipped.
CATALOG: list[Entry] = [
    # tier 0: the cheap, everyday cuts
    _e("user_segment_key", 0, note="audience segment (existing customers vs new / engaged)"), _e("age", 0), _e("gender", 0), _e("age,gender", 0), _e("country", 0),
    _e("publisher_platform", 0), _e("platform_position", 0), _e("publisher_platform,platform_position", 0), _e("impression_device", 0), _e("device_platform", 0),
    # tier 1
    _e("frequency_value"), _e("is_conversion_id_modeled"), _e("landing_destination"), _e("place_page_id"), _e("product_id"),
    _e("image_asset"), _e("video_asset"), _e("title_asset"), _e("body_asset"), _e("description_asset"), _e("call_to_action_asset"), _e("link_url_asset"), _e("ad_format_asset"),
    _e("creative_relaxation_asset_type"), _e("flexible_format_asset_type"), _e("gen_ai_asset_type"), _e("creative_automation_asset_id"),
    _e("creative_fingerprint_details"), _e("creative_format_classification"), _e("creative_media_type_breakdown"), _e("fidelity_type"),
    _e("media_asset_url"), _e("media_creator"), _e("media_destination_url"), _e("media_format"), _e("media_id"), _e("media_origin_url"), _e("media_text_content"), _e("media_type"),
    _e("ad_extension_domain"), _e("ad_extension_url"), _e("affiliate_link_url"), _e("existing_post_id"), _e("pa_creator_ig_handle"),
    _e("instagram_ads_follow_type"), _e("instagram_ads_instagram_media_product_type"), _e("instagram_ads_time_since_creation_bucket"),
    _e("reels_trending_topic"), _e("rta_ugc_topic"), _e("is_rendered_as_delayed_skip_ad"), _e("is_auto_advance"), _e("placement_path"),
    _e("user_persona_id"), _e("user_persona_name"), _e("overlap_segment"), _e("mdsa_landing_destination"),
    _e("product_brand_breakdown"), _e("product_category_breakdown"), _e("product_group_content_id_breakdown"),
    _e("product_custom_label_0_breakdown"), _e("product_custom_label_1_breakdown"), _e("product_custom_label_2_breakdown"),
    _e("product_custom_label_3_breakdown"), _e("product_custom_label_4_breakdown"),
    _e("app_id"), _e("marketing_messages_btn_name"), _e("breakdown_ad_objective"), _e("breakdown_reporting_ad_id"), _e("msa_seller_name"),
    _e("mmm"), _e("hsid"), _e("signal_source"),
    _e("skan_campaign_id"), _e("skan_conversion_id"), _e("skan_version"), _e("coarse_conversion_value"), _e("postback_sequence_index"), _e("redownload"),
    _e("sot_attribution_model_type"), _e("sot_attribution_window"), _e("sot_channel"), _e("sot_event_type"), _e("sot_source"),
    _e("rule_set_id"), _e("rule_set_name"), _e("internal_campaign_id"),
    _e("crm_advertiser_l12_territory_ids"), _e("crm_advertiser_subvertical_id"), _e("crm_advertiser_vertical_id"), _e("crm_ult_advertiser_id"),
    # std: readings of the three names Meta lists as both a standard and an action breakdown (the action reading keeps the plain name)
    _e("conversion_destination", label="std:conversion_destination"), _e("signal_source_bucket", label="std:signal_source_bucket"),
    _e("standard_event_content_type", label="std:standard_event_content_type"),
    # action breakdowns: they split the action metrics (purchases, purchase value) only, never spend or impressions
    _e("action_device", action=True), _e("action_destination", action=True), _e("action_reaction", action=True), _e("action_video_sound", action=True),
    _e("action_video_type", action=True), _e("action_carousel_card_id", action=True), _e("action_carousel_card_name", action=True),
    _e("action_carousel_card_id,action_carousel_card_name", action=True), _e("action_canvas_component_name", action=True), _e("action_target_id", action=True),
    _e("conversion_destination", action=True), _e("signal_source_bucket", action=True), _e("standard_event_content_type", action=True),
    _e("matched_persona_id", action=True), _e("matched_persona_name", action=True), _e("is_business_ai_assisted", action=True),
    # tier 2: heavy (many rows per ad per day): last in every window, one day per request
    _e("publisher_platform,platform_position,impression_device", 2), _e("region", 2), _e("dma", 2), _e("zip", 2), _e("comscore_market", 2),
    _e("affiliate_click_region", 2),
    _e("hourly_stats_aggregated_by_advertiser_time_zone", 2), _e("hourly_stats_aggregated_by_audience_time_zone", 2), _e("impression_view_time_advertiser_hour_v2", 2),
]
BY_NAME: dict[str, Entry] = {e.name: e for e in CATALOG}
assert len(BY_NAME) == len(CATALOG), "duplicate set names in the catalog"

BASE_FIELDS = ["spend", "impressions", "reach", "clicks", "inline_link_clicks", "outbound_clicks", "unique_outbound_clicks", "actions", "action_values"]
FIELD_LADDER = [BASE_FIELDS, [f for f in BASE_FIELDS if f != "reach"],        # a field Meta will not combine with a breakdown: retry without reach, then with the core set
                ["spend", "impressions", "clicks", "inline_link_clicks", "actions", "action_values"]]
ACTION_FIELDS = ["actions", "action_values"]
ID_FIELDS = {"ad": ["ad_id", "adset_id", "campaign_id"], "campaign": ["campaign_id"], "account": []}
KNOWN_KEYS = {"ad_id", "adset_id", "campaign_id", "ad_name", "campaign_name", "adset_name", "account_id", "date_start", "date_stop", "spend", "impressions", "reach", "clicks",
              "inline_link_clicks", "outbound_clicks", "unique_outbound_clicks", "actions", "action_values", "frequency", "cpm", "cpc", "ctr", "cpp"}


def parse_sets_arg(s: str | None) -> list[str] | None:
    """--sets separates sets with ';' (a set may contain commas: age,gender); a plus also works inside a set (age+gender)."""
    if not s:
        return None
    return [x.strip().replace("+", ",") for x in re.split(r"[;|]", s) if x.strip()]


def ordered(entries: list[Entry]) -> list[Entry]:
    return sorted(entries, key=lambda e: e.tier)           # stable: catalog order inside a tier


def excluded_names() -> frozenset[str]:
    """The permanent exclude list plus whatever META_BREAKDOWNS_EXCLUDE adds (the environment can only add)."""
    extra = set(parse_sets_arg(os.environ.get("META_BREAKDOWNS_EXCLUDE")) or [])
    return frozenset(EXCLUDED_SETS) | extra


def select_sets(names: list[str] | None) -> list[Entry]:
    """The sets to pull, cheap first. Excluded sets are dropped here, so no phase can ever ask Meta for one (also when named with --sets)."""
    skip = excluded_names()
    if not names:
        return ordered([e for e in CATALOG if e.name not in skip])
    out = []
    for n in names:
        if n not in BY_NAME:
            raise SystemExit(f"unknown breakdown set {n!r}; run with --list")
        if n in skip:
            print(f"breakdown set {n!r} is on the permanent exclude list: not pulled", file=sys.stderr)
            continue
        out.append(BY_NAME[n])
    return ordered(out)


def mark_excluded(conn) -> int:
    """Coverage rows of the excluded sets say so (last_error carries the note; an 'error' status becomes 'ok' because the stored rows are fine). No request, rows untouched."""
    names = sorted(excluded_names())
    n = conn.execute("UPDATE meta_breakdown_coverage SET last_error = %s, status = CASE WHEN status = 'error' THEN 'ok' ELSE status END "
                     "WHERE breakdown_set = ANY(%s) AND last_error IS DISTINCT FROM %s", (EXCLUDED_NOTE, names, EXCLUDED_NOTE)).rowcount
    conn.commit()
    return n


# ---------------------------------------------------------------------------------------------------------------- requests
def fields_for(entry: Entry, level: str, ladder_step: int = 0) -> str:
    base = ACTION_FIELDS if entry.action else FIELD_LADDER[min(ladder_step, len(FIELD_LADDER) - 1)]
    return ",".join(ID_FIELDS[level] + base + ["date_start"])


def build_params(entry: Entry, level: str, tok: str, since: dt.date, until: dt.date, ladder_step: int = 0, limit: int = LIMIT, filtering: list | None = None,
                 time_increment: int | None = 1) -> dict:
    """Query parameters of one Insights request for a catalog entry (the token is included here only; callers never log params)."""
    p: dict = {"access_token": tok, "level": level, "limit": limit, "fields": fields_for(entry, level, ladder_step),
               "time_range": json.dumps({"since": since.isoformat(), "until": until.isoformat()})}
    if time_increment:
        p["time_increment"] = time_increment
    if entry.action:
        p["action_breakdowns"] = ",".join(("action_type",) + entry.breakdowns)
    else:
        p["breakdowns"] = ",".join(entry.breakdowns)
    if filtering:
        p["filtering"] = json.dumps(filtering)
    return p


def _classify(e: Exception) -> Exception:
    """A refusal of the request itself (Graph code 100) becomes Unsupported; token and rate-limit failures stay as they are."""
    if isinstance(e, (meta_api.TokenRejected, meta_api.RateLimited)):
        return e
    msg = db.scrub(e)
    if re.search(r"\bcode 100:", msg):
        return Unsupported(msg[:300])
    return e


class Client:
    """GET-only paging over the repo's read client (plus the async report run). sleep is injectable so tests never wait."""

    def __init__(self, tok: str, pace: float | None = None, sleep=None, max_retries: int = 7, transient_budget: float | None = TRANSIENT_BUDGET_S):
        self.tok, self.pace, self.sleep, self.max_retries = tok, PACE if pace is None else pace, sleep or time.sleep, max_retries
        self.calls = 0
        self.async_sets: set[str] = set()                    # sets that needed an async report in this run (run stats)
        self.async_first: set[str] = set()                   # sets known to be too big even for one day: asked as an async report straight away (persisted by run())
        self.transient_budget, self.transient_spent = transient_budget, 0.0
        self.progressed, self.splits_left = False, MAX_BUDGET_SPLITS

    def begin_set(self) -> None:
        """A new set pull starts: its retry-wait budget is full again (it is also refilled by every request that succeeds)."""
        self.transient_spent = 0.0
        self.progressed, self.splits_left = False, MAX_BUDGET_SPLITS

    def _on_wait(self, wait: float, rate: bool) -> None:
        if rate or self.transient_budget is None:
            return                                           # rate limits are the Waiter's business, not a per-set failure
        if self.transient_spent + wait > self.transient_budget:
            raise SetBudgetExceeded(f"transient failures used the {self.transient_budget:g}s retry budget for this set (HTTP 5xx / temporarily unavailable)")
        self.transient_spent += wait

    def get(self, url, params, what):
        self.calls += 1
        j = meta_api.graph_get(url, params, what, max_retries=self.max_retries, sleep=self.sleep, on_wait=self._on_wait)
        self.progressed = True
        self.transient_spent = 0.0                           # a request that got through: the budget bounds time spent failing with no progress, not a long productive pull
        return j

    def pages_url(self, url: str, params: dict | None, what: str, max_pages: int | None = None):
        n = 0
        while url:
            j = self.get(url, params, what)
            yield j.get("data", [])
            n += 1
            url, params = j.get("paging", {}).get("next"), None          # the next-page URL carries the token: only ever passed on, never printed
            if max_pages and n >= max_pages:
                return
            if url:
                self.sleep(self.pace)

    def fetch(self, acct: str, params: dict, what: str, max_pages: int | None = None) -> list[dict]:
        out: list[dict] = []
        try:
            for page in self.pages_url(f"{meta_api.G}/{acct}/insights", params, what, max_pages):
                out += page
        except meta_api.GraphError as e:
            raise _classify(e) from None
        return out

    def fetch_async(self, acct: str, params: dict, what: str) -> list[dict]:
        """Async report run (POST creates a report only), polled with GET, then paged. Used for a one-day window a plain GET cannot deliver."""
        import requests
        p = {k: v for k, v in params.items() if k != "limit"}
        try:
            j = requests.post(f"{meta_api.G}/{acct}/insights", data=p, timeout=120).json()
        except Exception as e:                                                # noqa: BLE001
            raise meta_api.GraphError(f"{what}: async report did not start: {db.scrub(e)[:120]}") from None
        if "error" in j:
            raise _classify(meta_api.GraphError(f"{what}: HTTP 400 code {j['error'].get('code')}: {db.scrub(j['error'].get('message', ''))[:200]}"))
        rid = j.get("report_run_id")
        if not rid:
            raise meta_api.GraphError(f"{what}: async report did not start")
        try:
            for _ in range(ASYNC_POLLS):
                self.sleep(ASYNC_POLL_S)
                s = self.get(f"{meta_api.G}/{rid}", {"access_token": self.tok}, what + " async poll")
                if s.get("async_status") == "Job Completed":
                    break
                if s.get("async_status") in ("Job Failed", "Job Skipped"):
                    raise meta_api.GraphError(f"{what}: async job {s.get('async_status')}")
            else:
                raise AsyncTimeout(f"{what}: async job timed out")
            out: list[dict] = []
            for page in self.pages_url(f"{meta_api.G}/{rid}/insights", {"access_token": self.tok, "limit": LIMIT}, what + " async"):
                out += page
            return out
        except meta_api.GraphError as e:
            raise _classify(e) from None


def fetch_window(client: Client, acct: str, entry: Entry, level: str, since: dt.date, until: dt.date, an: str, ladder_step: int = 0) -> list[dict]:
    """Rows for [since, until]. Too big -> halve the window; one day too big or timing out -> the async report run."""
    what = f"{an} {entry.name} {since}..{until}"
    params = build_params(entry, level, client.tok, since, until, ladder_step)
    if entry.name in client.async_first:                     # learned: even one day is too big as a plain request, so skip the plain attempts and the halving
        try:
            return _async_day(client, acct, params, entry, what)
        except (meta_api.TokenRejected, meta_api.RateLimited, Unsupported, SetBudgetExceeded, AsyncTimeout):
            raise
        except (meta_api.GraphError, TooBigError):
            if since >= until:
                raise
            mid = since + (until - since) // 2
            db.log(f"  {what} async report failed -> splitting")
            return (fetch_window(client, acct, entry, level, since, mid, an, ladder_step)
                    + fetch_window(client, acct, entry, level, mid + dt.timedelta(days=1), until, an, ladder_step))
    try:
        return client.fetch(acct, params, what)
    except meta_api.TooBig as e:
        if since < until:
            mid = since + (until - since) // 2
            db.log(f"  {what} too big -> splitting ({db.scrub(e)[:50]})")
            return (fetch_window(client, acct, entry, level, since, mid, an, ladder_step)
                    + fetch_window(client, acct, entry, level, mid + dt.timedelta(days=1), until, an, ladder_step))
        db.log(f"  {what} too big at one day -> async report run (this set goes straight to async from now on)")
        client.async_first.add(entry.name)
        return _async_day(client, acct, params, entry, what)
    except SetBudgetExceeded:
        if not client.progressed or client.splits_left <= 0:
            raise                                            # nothing has worked for this set yet (or enough tries were spent): an error for this run
        client.splits_left -= 1                              # the set works but this request window keeps failing as a plain request: ask for smaller pieces, then async
        client.transient_spent = 0.0
        if since < until:
            mid = since + (until - since) // 2
            db.log(f"  {what} keeps failing as a plain request -> splitting")
            return (fetch_window(client, acct, entry, level, since, mid, an, ladder_step)
                    + fetch_window(client, acct, entry, level, mid + dt.timedelta(days=1), until, an, ladder_step))
        db.log(f"  {what} keeps failing as a plain request -> async report run")
        return _async_day(client, acct, params, entry, what)
    except (meta_api.TokenRejected, meta_api.RateLimited, Unsupported):
        raise
    except meta_api.GraphError as e:
        if since < until or "gave up" not in str(e):
            raise
        db.log(f"  {what} keeps failing as a plain request -> async report run")
        return _async_day(client, acct, params, entry, what)


def _async_day(client: Client, acct: str, params: dict, entry: Entry, what: str) -> list[dict]:
    client.async_sets.add(entry.name)
    try:
        return client.fetch_async(acct, params, what)
    except meta_api.TooBig:
        raise TooBigError(f"{what}: too big even as an async report") from None


def fetch_entry_window(client: Client, acct: str, entry: Entry, level: str, since, until, an: str) -> list[dict]:
    """fetch_window with the field ladder: when Meta refuses the request, retry with fewer fields before calling the set unsupported at this level."""
    last: Exception | None = None
    for step in range(1 if entry.action else len(FIELD_LADDER)):
        try:
            return fetch_window(client, acct, entry, level, since, until, an, step)
        except Unsupported as e:
            last = e
    raise last or Unsupported("refused")


# ---------------------------------------------------------------------------------------------------------------- normalisation
def _esc(v) -> str:
    s = v if isinstance(v, str) else ("" if v is None else json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
    return s.replace("%", "%25").replace("|", "%7C").replace("=", "%3D")


def dims_key(dims: dict) -> str:
    """Stable text form of a dimension dict: names sorted, name=value joined with '|' ('%', '|' and '=' inside a value are percent-escaped)."""
    return "|".join(f"{_esc(k)}={_esc(dims[k])}" for k in sorted(dims))


def _dim_text(v) -> str | None:
    if v is None:
        return None
    return v if isinstance(v, str) else json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _num(v) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _int(v) -> int | None:
    n = _num(v)
    return None if n is None else int(round(n))


def _outbound(a: dict, field: str) -> int | None:
    v = a.get(field)
    if v is None:
        return None
    if isinstance(v, list):
        return int(round(sum(float(x.get("value") or 0) for x in v if x.get("action_type") in (None, "outbound_click"))))
    return _int(v)


def extract_dims(entry: Entry, a: dict) -> dict:
    """The breakdown values of a standard row: the requested names. When Meta names them differently, the unexpected non-metric keys; none at all is a ShapeError."""
    dims = {b: a[b] for b in entry.breakdowns if b in a}
    if dims:
        return {b: dims.get(b) for b in entry.breakdowns}
    extra = {k: v for k, v in a.items() if k not in KNOWN_KEYS}
    if not extra:
        raise ShapeError(f"{entry.name}: the response rows carry none of the requested breakdown keys")
    return dict(list(extra.items())[:MAX_DIMS])


def _entity(entry: Entry, level: str, acct: str, a: dict) -> tuple[str, str | None, str | None]:
    camp = str(a["campaign_id"]) if a.get("campaign_id") else None
    if level == "ad":
        if not a.get("ad_id"):
            raise ShapeError(f"{entry.name}: ad-level row without ad_id")
        return str(a["ad_id"]), camp, str(a["adset_id"]) if a.get("adset_id") else None
    if level == "campaign":
        if not camp:
            raise ShapeError(f"{entry.name}: campaign-level row without campaign_id")
        return camp, camp, None
    return acct, None, None


def _purch(d: dict) -> float:
    return d.get("omni_purchase", d.get("purchase", d.get("offsite_conversion.fb_pixel_purchase", 0.0)))


def _row(day, acct, level, eid, camp, adset, name, dims: dict, basics: tuple, purchases, value, atc=None, ic=None) -> tuple:
    vals = [_dim_text(v) for v in dims.values()][:MAX_DIMS]
    vals += [None] * (MAX_DIMS - len(vals))
    return (day, acct, level, eid, camp, adset, name, vals[0], vals[1], vals[2], dims_key(dims), *basics, purchases, value, atc, ic)


def normalise(entry: Entry, level: str, acct: str, raw: list[dict]) -> list[tuple]:
    """API rows -> table rows (day, account_id, level, entity_id, campaign_id, adset_id, breakdown_set, dim1, dim2, dim3, dims_key, spend, impressions, reach, clicks,
    inline_link_clicks, outbound_clicks, unique_outbound_clicks, purchases, purchase_value, add_to_cart, initiate_checkout). Standard sets: one row per API row
    (adds to cart and initiated checkouts with the same omni / plain / pixel priority as the purchases). Action sets: one row per API row x action-breakdown value that has a
    purchase (the basics and the two funnel columns are NULL: an action breakdown does not split spend, impressions or the funnel steps)."""
    out: list[tuple] = []
    nulls = (None,) * 7
    for a in raw:
        day = dt.date.fromisoformat(a["date_start"])
        eid, camp, adset = _entity(entry, level, acct, a)
        if entry.action:
            groups: dict[tuple, dict] = {}
            for field, slot in (("actions", "p"), ("action_values", "v")):
                for x in a.get(field) or []:
                    dim = tuple(x.get(b) for b in entry.breakdowns)
                    groups.setdefault(dim, {"p": {}, "v": {}})[slot][x.get("action_type")] = float(x.get("value") or 0)
            for dim, g in groups.items():
                if not any(t in g["p"] or t in g["v"] for t in PURCHASE_TYPES):
                    continue
                out.append(_row(day, acct, level, eid, camp, adset, entry.name, dict(zip(entry.breakdowns, dim)), nulls, round(_purch(g["p"]), 2), round(_purch(g["v"]), 2)))
            continue
        basics = (round(_num(a.get("spend")) or 0.0, 2), _int(a.get("impressions")), _int(a.get("reach")), _int(a.get("clicks")), _int(a.get("inline_link_clicks")),
                  _outbound(a, "outbound_clicks"), _outbound(a, "unique_outbound_clicks"))
        out.append(_row(day, acct, level, eid, camp, adset, entry.name, extract_dims(entry, a), basics, round(pick(a.get("actions")), 2), round(pick(a.get("action_values")), 2),
                        round(pick_atc(a.get("actions")), 2), round(pick_ic(a.get("actions")), 2)))
    return out


# ---------------------------------------------------------------------------------------------------------------- storage
COLS = ("day, account_id, level, entity_id, campaign_id, adset_id, breakdown_set, dim1, dim2, dim3, dims_key, spend, impressions, reach, clicks, inline_link_clicks, "
        "outbound_clicks, unique_outbound_clicks, purchases, purchase_value, add_to_cart, initiate_checkout")
UPSERT = f"""INSERT INTO meta_breakdown_daily ({COLS}) VALUES ({",".join(["%s"] * 22)})
ON CONFLICT (day, account_id, level, entity_id, breakdown_set, dims_hash) DO UPDATE SET campaign_id=EXCLUDED.campaign_id, adset_id=EXCLUDED.adset_id,
  dim1=EXCLUDED.dim1, dim2=EXCLUDED.dim2, dim3=EXCLUDED.dim3, spend=EXCLUDED.spend, impressions=EXCLUDED.impressions, reach=EXCLUDED.reach, clicks=EXCLUDED.clicks,
  inline_link_clicks=EXCLUDED.inline_link_clicks, outbound_clicks=EXCLUDED.outbound_clicks, unique_outbound_clicks=EXCLUDED.unique_outbound_clicks,
  purchases=EXCLUDED.purchases, purchase_value=EXCLUDED.purchase_value, add_to_cart=COALESCE(EXCLUDED.add_to_cart, meta_breakdown_daily.add_to_cart),
  initiate_checkout=COALESCE(EXCLUDED.initiate_checkout, meta_breakdown_daily.initiate_checkout), pulled_at=now()"""
KEY_IDX = (0, 1, 2, 3, 6, 10)


def dedupe(rows: list[tuple]) -> tuple[list[tuple], int]:
    """Last row wins per primary key (an INSERT .. ON CONFLICT cannot touch one row twice). Returns (rows, number of duplicates dropped)."""
    seen: dict[tuple, tuple] = {}
    for r in rows:
        seen[tuple(r[i] for i in KEY_IDX)] = r
    return list(seen.values()), len(rows) - len(seen)


def upsert(conn, rows: list[tuple]) -> tuple[int, int]:
    rows, dups = dedupe(rows)
    for i in range(0, len(rows), 5000):
        db.execmany(conn, UPSERT, rows[i:i + 5000])
    return len(rows), dups


FILL_SETS = ("publisher_platform", "publisher_platform,platform_position", "impression_device", "device_platform", "age", "gender", "country")   # the cuts the why tool decomposes
FILL_SQL = ("UPDATE meta_breakdown_daily SET add_to_cart = COALESCE(add_to_cart, %s), initiate_checkout = COALESCE(initiate_checkout, %s) "
            "WHERE day = %s AND account_id = %s AND level = %s AND entity_id = %s AND breakdown_set = %s AND dims_hash = md5(%s) AND add_to_cart IS NULL")
FILL_LOCK_KEY = 240_241


def fill_writer(conn, rows: list[tuple]) -> tuple[int, int]:
    """FILL-ONLY writer for the two funnel columns: UPDATE add_to_cart / initiate_checkout of rows that exist and still have add_to_cart NULL. Spend, impressions, clicks, purchases
    and every other column of an existing row are never written; a pulled row with no stored counterpart is not inserted. Action-set rows (no funnel values) are skipped."""
    upd = [(r[-2], r[-1], r[0], r[1], r[2], r[3], r[6], r[10]) for r in dedupe(rows)[0] if r[-2] is not None]
    for i in range(0, len(upd), 5000):
        db.execmany(conn, FILL_SQL, upd[i:i + 5000])
    return len(upd), 0


def fill_todo(conn, name: str, level: str, ws: dt.date, we: dt.date) -> int:
    """Stored rows of the set in [ws, we] that carry spend and still have add_to_cart NULL (what a fill pass would fill)."""
    return conn.execute("SELECT count(*) FROM meta_breakdown_daily WHERE breakdown_set = %s AND level = %s AND day BETWEEN %s AND %s AND spend IS NOT NULL AND add_to_cart IS NULL",
                        (name, level, ws, we)).fetchone()[0]


def fill_funnel(conn, since: dt.date, until: dt.date, sets: list[str] | None = None, stopper: Stopper | None = None, client: Client | None = None, accounts=None,
                max_rate_waits: int = 24, pause=None, window_days: int = CHUNK_DAYS) -> dict:
    """History fill of add_to_cart / initiate_checkout for rows pulled before those columns existed. Re-pulls [since, until] (read-only Meta GETs, one weekly request window per
    set, newest first) at AD level and UPDATEs only those two columns of existing rows that have add_to_cart NULL (fill_writer). Resumable: a window x set with nothing left to
    fill costs no request. Honours the quiet hours and the time budget like the main pull; coverage and progress tables are not touched."""
    client = client or Client(meta_api.get_token())
    pause = pause or time.sleep
    accounts = accounts or meta_api.ACCOUNTS
    waiter = Waiter(client, max_rate_waits, stopper=stopper)
    entries = select_sets(list(sets) if sets else list(FILL_SETS))
    st: dict = {"rows": 0}
    out = {"windows": 0, "windows_filled": 0, "windows_skipped": 0, "per_set": {}, "errors": {}, "stopped_early": False}
    try:
        for ws, we in windows_newest_first(since, until, window_days):
            out["windows"] += 1
            for entry in entries:
                todo = fill_todo(conn, entry.name, "ad", ws, we)
                if not todo:
                    out["windows_skipped"] += 1
                    continue
                if stopper:
                    stopper.check()
                t0, r0 = time.monotonic(), st["rows"]
                status, err, _ = pull_set_level(conn, client, waiter, entry, "ad", ws, we, accounts, st, False, False, writer=fill_writer)
                left = fill_todo(conn, entry.name, "ad", ws, we)
                db.log(f"  fill {entry.name} {ws}..{we}: {status}, {todo} stored rows without add_to_cart before, {left} after, {time.monotonic() - t0:.0f}s")
                ps = out["per_set"].setdefault(entry.name, {"filled": 0, "left": 0})
                ps["filled"] += todo - left
                ps["left"] += left
                if status != "ok":
                    out["errors"][f"{entry.name} {ws}"] = (status, err)
                else:
                    out["windows_filled"] += 1
                pause(SET_PAUSE)
    except StopRun as e:
        out["stopped_early"], out["stop_reason"] = True, str(e)
        conn.rollback()
        db.log(f"fill stopping cleanly: {e}")
    out["rows_seen"] = st["rows"]
    out["api_calls"] = client.calls
    return out


def ensure_partitions(conn, since: dt.date, until: dt.date) -> int:
    """Create the monthly partitions (meta_breakdown_daily_YYYY_MM) that [since, until] needs. Migration 240 pre-creates 2025-01 .. 2028-12; this covers later months."""
    n, d = 0, dt.date(since.year, since.month, 1)
    while d <= until:
        nxt = dt.date(d.year + (d.month == 12), d.month % 12 + 1, 1)
        conn.execute(f"CREATE TABLE IF NOT EXISTS meta_breakdown_daily_{d:%Y_%m} PARTITION OF meta_breakdown_daily FOR VALUES FROM ('{d.isoformat()}') TO ('{nxt.isoformat()}')")
        n += 1
        d = nxt
    conn.commit()
    return n


def record_coverage(conn, entry: Entry, level: str, status: str, err: str | None) -> None:
    conn.execute("""INSERT INTO meta_breakdown_coverage (breakdown_set, level, status, first_day, last_day, last_error, rows, checked_at)
                    SELECT %s, %s, %s, min(day), max(day), %s, count(*), now() FROM meta_breakdown_daily WHERE breakdown_set = %s AND level = %s
                    ON CONFLICT (breakdown_set, level) DO UPDATE SET status = EXCLUDED.status, first_day = EXCLUDED.first_day, last_day = EXCLUDED.last_day,
                      last_error = EXCLUDED.last_error, rows = EXCLUDED.rows, checked_at = EXCLUDED.checked_at""",
                 (entry.name, level, status, db.scrub(err)[:500] if err else None, entry.name, level))


def recently_unsupported(conn) -> set[tuple[str, str]]:
    return {(r[0], r[1]) for r in conn.execute("SELECT breakdown_set, level FROM meta_breakdown_coverage WHERE status = 'unsupported' "
                                              "AND checked_at > now() - make_interval(days => %s)", (RECHECK_UNSUPPORTED_DAYS,)).fetchall()}


def progress_key(name: str, level: str, catchup_day: dt.date | None) -> tuple[str, str]:
    """Where a finished window is recorded: the backfill under (set, level); the catch-up under a per-day stamp ('__catchup__<set>', '<level>:<day>')."""
    return (name, level) if catchup_day is None else (CATCHUP_PREFIX + name, f"{level}:{catchup_day.isoformat()}")


def window_done(conn, name: str, level: str, acct: str, ws: dt.date, we: dt.date) -> bool:
    """True when a finished window that covers [ws, we] is recorded for this set, level and account (resume)."""
    return conn.execute("SELECT 1 FROM meta_breakdown_progress WHERE breakdown_set=%s AND level=%s AND account_id=%s AND window_start <= %s AND window_end >= %s LIMIT 1",
                        (name, level, acct, ws, we)).fetchone() is not None


def mark_window(conn, name: str, level: str, acct: str, ws: dt.date, we: dt.date, rows: int) -> None:
    conn.execute("""INSERT INTO meta_breakdown_progress (breakdown_set, level, account_id, window_start, window_end, rows, done_at) VALUES (%s,%s,%s,%s,%s,%s,now())
                    ON CONFLICT (breakdown_set, level, account_id, window_start, window_end) DO UPDATE SET rows = EXCLUDED.rows, done_at = now()""",
                 (name, level, acct, ws, we, rows))


# ---------------------------------------------------------------------------------------------------------------- per-set state (rows in meta_breakdown_progress, no extra table)
def _put(conn, name: str, level: str, start: dt.date, end: dt.date, rows: int = 0) -> None:
    """One row per (name, level), replacing any older row of that name and level (account '-')."""
    conn.execute("DELETE FROM meta_breakdown_progress WHERE breakdown_set=%s AND level=%s AND account_id='-'", (name, level))
    conn.execute("INSERT INTO meta_breakdown_progress (breakdown_set, level, account_id, window_start, window_end, rows, done_at) VALUES (%s,%s,'-',%s,%s,%s,now())",
                 (name, level, start, end, rows))


def quarantined_sets(conn, today: dt.date) -> dict[str, str]:
    """set -> kind ('shape' | 'failing') of the sets that are not asked again until their re-probe day."""
    return {r[0][len(QUARANTINE_PREFIX):]: r[1] for r in conn.execute(
        "SELECT breakdown_set, level FROM meta_breakdown_progress WHERE breakdown_set LIKE %s AND account_id='-' AND window_end > %s", (QUARANTINE_PREFIX.replace("_", "\\_") + "%", today)).fetchall()}


def set_quarantine(conn, name: str, kind: str, today: dt.date) -> None:
    conn.execute("DELETE FROM meta_breakdown_progress WHERE breakdown_set=%s AND account_id='-'", (QUARANTINE_PREFIX + name,))
    _put(conn, QUARANTINE_PREFIX + name, kind, today, today + dt.timedelta(days=QUARANTINE_DAYS))
    conn.execute("DELETE FROM meta_breakdown_progress WHERE breakdown_set=%s AND account_id='-'", (FAILS_PREFIX + name,))


def note_outcome(conn, name: str, status: str, kind: str, rows_written: int, today: dt.date) -> str | None:
    """Failure bookkeeping after one set pull. 'shape' (the response never carries the requested keys) quarantines at once; any other failure that wrote no rows counts as a
    failing run, FAIL_LIMIT in a row quarantine the set; a clean pull (or one that wrote rows) clears the count. Returns the quarantine kind if one was set."""
    if status == "error" and kind == "shape":
        set_quarantine(conn, name, "shape", today)
        return "shape"
    if status == "ok" or rows_written > 0:
        conn.execute("DELETE FROM meta_breakdown_progress WHERE breakdown_set=%s AND account_id='-'", (FAILS_PREFIX + name,))
        return None
    if status == "error":
        r = conn.execute("SELECT rows FROM meta_breakdown_progress WHERE breakdown_set=%s AND account_id='-'", (FAILS_PREFIX + name,)).fetchone()
        n = (int(r[0]) if r else 0) + 1
        if n >= FAIL_LIMIT:
            set_quarantine(conn, name, "failing", today)
            return "failing"
        _put(conn, FAILS_PREFIX + name, "-", today, today, n)
    return None


def async_first_sets(conn) -> set[str]:
    return {r[0][len(ASYNC_PREFIX):] for r in conn.execute("SELECT breakdown_set FROM meta_breakdown_progress WHERE breakdown_set LIKE %s AND account_id='-'",
                                                          (ASYNC_PREFIX.replace("_", "\\_") + "%",)).fetchall()}


def mark_async_first(conn, name: str, today: dt.date) -> None:
    _put(conn, ASYNC_PREFIX + name, "-", today, today)


def clear_old_catchup_stamps(conn, day: dt.date) -> None:
    for prefix in (CATCHUP_PREFIX, CHUNK_PREFIX + CATCHUP_PREFIX):
        conn.execute("DELETE FROM meta_breakdown_progress WHERE breakdown_set LIKE %s AND level NOT LIKE %s", (prefix.replace("_", "\\_") + "%", "%:" + day.isoformat()))


def catchup_counts(conn, entries: list["Entry"], day: dt.date, accounts) -> tuple[int, int]:
    """(sets refreshed on `day`, sets still to do) for the catch-up: a set is done when every account has a stamp at some level; sets quarantined or refused at every
    level count as done (they are skipped)."""
    stamped: dict[str, set] = {}
    for n, lv, ac in conn.execute("SELECT breakdown_set, level, account_id FROM meta_breakdown_progress WHERE breakdown_set LIKE %s AND level LIKE %s",
                                  (CATCHUP_PREFIX.replace("_", "\\_") + "%", "%:" + day.isoformat())).fetchall():
        stamped.setdefault(n[len(CATCHUP_PREFIX):], set()).add(ac)
    skip = _skipped_sets(conn, entries, day)
    done = sum(1 for e in entries if e.name in skip or len(stamped.get(e.name, ())) >= len(accounts))
    return done, len(entries) - done


def _skipped_sets(conn, entries, today: dt.date) -> set[str]:
    q = quarantined_sets(conn, today)
    ref = recently_unsupported(conn)
    return {e.name for e in entries if e.name in q or all((e.name, lv) in ref for lv in LEVELS)}


def backfill_counts(conn, entries: list["Entry"], ws: dt.date, we: dt.date, accounts, today: dt.date) -> tuple[int, int]:
    """(sets finished, sets still to do) in backfill window [ws, we]. Finished = progress rows for every account at some level, or skipped (quarantined, refused everywhere)."""
    got: dict[str, set] = {}
    for n, ac in conn.execute("SELECT breakdown_set, account_id FROM meta_breakdown_progress WHERE window_start <= %s AND window_end >= %s AND level IN ('ad','campaign','account')",
                              (ws, we)).fetchall():
        got.setdefault(n, set()).add(ac)
    skip = _skipped_sets(conn, entries, today)
    done = sum(1 for e in entries if e.name in skip or len(got.get(e.name, ())) >= len(accounts))
    return done, len(entries) - done


def backfill_rows(conn) -> int:
    """Number of finished backfill window x set x level x account records (grows whenever the backfill advances)."""
    return int(conn.execute("SELECT count(*) FROM meta_breakdown_progress WHERE level IN ('ad','campaign','account')").fetchone()[0])


# ---------------------------------------------------------------------------------------------------------------- the run
def default_until() -> dt.date:
    """Last complete day: yesterday in Pacific (same rule as etl.meta_pull)."""
    return dt.datetime.now(PT).date() - dt.timedelta(days=1)


def windows_newest_first(since: dt.date, until: dt.date, window_days: int = 30) -> list[tuple[dt.date, dt.date]]:
    out, e = [], until
    while e >= since:
        s = max(since, e - dt.timedelta(days=window_days - 1))
        out.append((s, e))
        e = s - dt.timedelta(days=1)
    return out


def parse_quiet(spec: str | None) -> tuple[int, int] | None:
    """'12:30-15:30' (UTC) -> (start minute, end minute) of the day; empty / 'off' -> None. A window that wraps midnight (22:00-02:00) is allowed."""
    if spec is None or spec.strip().lower() in ("", "off", "none"):
        return None
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*", spec)
    if not m or int(m[1]) > 23 or int(m[3]) > 23 or int(m[2]) > 59 or int(m[4]) > 59:
        raise SystemExit(f"META_BREAKDOWNS_QUIET_UTC must look like 12:30-15:30, got {spec!r}")
    return int(m[1]) * 60 + int(m[2]), int(m[3]) * 60 + int(m[4])


def in_quiet(quiet: tuple[int, int] | None, now: dt.datetime) -> bool:
    if quiet is None:
        return False
    t = now.astimezone(dt.timezone.utc).hour * 60 + now.astimezone(dt.timezone.utc).minute
    a, b = quiet
    return a <= t < b if a <= b else (t >= a or t < b)


class Stopper:
    """check() raises StopRun when the wall-clock budget is used up or the quiet hours have begun. Injectable clocks for tests."""

    def __init__(self, max_minutes: float | None = None, quiet: tuple[int, int] | None = None, clock=None, now=None, label: str = "time budget"):
        self.max_minutes, self.quiet, self.clock, self.label = max_minutes or None, quiet, clock or time.monotonic, label
        self.now = now or (lambda: dt.datetime.now(dt.timezone.utc))
        self.t0 = self.clock()

    def child(self, share: float, label: str) -> "Stopper":
        """A stopper for one phase of the run: `share` of this stopper's whole budget (no limit of its own when this one has none), never more than what is left; the quiet hours still apply."""
        if not self.max_minutes:
            return Stopper(None, self.quiet, self.clock, self.now, label)
        left = self.max_minutes - (self.clock() - self.t0) / 60
        return Stopper(max(0.0, min(self.max_minutes * share, left)) or 1e-9, self.quiet, self.clock, self.now, label)

    def reason(self) -> str | None:
        if self.max_minutes and (self.clock() - self.t0) / 60 > self.max_minutes:
            return f"{self.label} of {self.max_minutes:g} minutes used up"
        if in_quiet(self.quiet, self.now()):
            return "quiet hours began (the main nightly Meta pull needs the rate limit)"
        return None

    def check(self) -> None:
        r = self.reason()
        if r:
            raise StopRun(r)


class Waiter:
    """Sleeps and continues when the client's own retries ran out on a rate limit; gives up after max_waits."""

    def __init__(self, client: Client, max_waits: int, wait_s: float = RATE_WAIT, stopper: Stopper | None = None):
        self.client, self.max_waits, self.wait_s, self.n, self.stopper = client, max_waits, wait_s, 0, stopper

    def call(self, fn, what: str):
        while True:
            try:
                return fn()
            except meta_api.RateLimited as e:
                self.n += 1
                if self.n > self.max_waits:
                    raise
                if self.stopper:
                    self.stopper.check()                    # no point sleeping into the quiet hours or past the budget
                db.log(f"  rate limited ({what}); waiting {self.wait_s}s then retrying ({self.n}/{self.max_waits}): {db.scrub(e)[:100]}")
                self.client.sleep(self.wait_s)


def pull_set_level(conn, client: Client, waiter: Waiter, entry: Entry, level: str, ws: dt.date, we: dt.date, accounts, st: dict, backfill: bool, restart: bool,
                   catchup_day: dt.date | None = None, writer=None) -> tuple[str, str | None, str]:
    """One set at one level over window [ws, we], both accounts. Each request window is committed as it arrives; with backfill (or, for the daily catch-up, catchup_day) the
    finished window is recorded per account and skipped on a rerun. conn None = dry run (pull and count, write nothing). Returns (status, error, kind); kind says why a failure
    happened: 'shape' (the response never carries the requested keys: deterministic), 'transient' (HTTP 5xx / the retry budget), 'other'; '' when ok."""
    status, err, kind, any_ok = "ok", None, "", False
    track = (backfill or catchup_day is not None) and conn is not None
    pname, plevel = progress_key(entry.name, level, catchup_day)
    client.begin_set()
    for ai, (acct, an) in enumerate(accounts):
        if track and not restart and window_done(conn, pname, plevel, acct, ws, we):
            any_ok = True
            st["windows_skipped"] = st.get("windows_skipped", 0) + 1
            continue
        try:
            n_acct = 0
            for cs, ce in meta_api.date_chunks(ws, we, entry.chunk_days):
                whole = (cs, ce) == (ws, we)
                if track and not whole and not restart and window_done(conn, CHUNK_PREFIX + pname, plevel, acct, cs, ce):
                    continue                                # this request window finished in an earlier run that then failed later in the window: do not pull it again
                if waiter.stopper:
                    waiter.stopper.check()                  # never start a request batch in the quiet hours or past the budget
                raw = waiter.call(lambda cs=cs, ce=ce: fetch_entry_window(client, acct, entry, level, cs, ce, an), f"{an} {entry.name} {cs}..{ce}")
                rows = normalise(entry, level, acct, raw)
                if conn is not None:
                    n, dups = (writer or upsert)(conn, rows)
                    conn.commit()
                else:
                    n, dups = len(dedupe(rows)[0]), 0
                n_acct += n
                st["rows"] = st.get("rows", 0) + n
                st["duplicates_dropped"] = st.get("duplicates_dropped", 0) + dups
                if track and not whole:
                    mark_window(conn, CHUNK_PREFIX + pname, plevel, acct, cs, ce, n)          # chunk record: the committed request windows of an unfinished window are kept
                    conn.commit()
            if track:
                mark_window(conn, pname, plevel, acct, ws, we, n_acct)
                conn.commit()
            any_ok = True
        except Unsupported as e:
            if conn is not None:
                conn.rollback()
            if not any_ok and ai == 0:
                return "unsupported", db.scrub(e)[:300], ""          # Meta refuses the request itself, so the second account would refuse it too
            status, err, kind = "error", f"{an}: {db.scrub(e)[:250]}", "other"
        except ShapeError as e:
            if conn is not None:
                conn.rollback()
            status, err, kind = "error", f"{an}: {db.scrub(e)[:250]}", "shape"
        except TooBigError as e:
            if conn is not None:
                conn.rollback()
            status, err, kind = "error", f"{an}: {db.scrub(e)[:250]}", "other"
        except (meta_api.TokenRejected, meta_api.RateLimited, StopRun):
            if conn is not None:
                conn.rollback()
            raise
        except meta_api.GraphError as e:                        # includes SetBudgetExceeded and AsyncTimeout
            if conn is not None:
                conn.rollback()
            status, err, kind = "error", f"{an}: {db.scrub(e)[:250]}", "transient"
            if isinstance(e, SetBudgetExceeded):
                break                                           # the budget is per set: the other account would only spend more of it
        except Exception as e:                                  # noqa: BLE001  one set failing (a bad value, a database error) must not end the run
            if conn is not None:
                conn.rollback()
            status, err, kind = "error", f"{an}: {type(e).__name__}: {db.scrub(e)[:230]}", "other"
            db.log(f"  {entry.name} {level} {ws}..{we} {an} FAILED, continuing with the next set: {err}")
    return status, err, kind


def run(conn, windows: list[tuple[dt.date, dt.date]], sets: list[str] | None = None, dry_run: bool = False, client: Client | None = None, stopper: Stopper | None = None,
        accounts=None, backfill: bool = False, restart: bool = False, max_rate_waits: int = 3, pause=None, catchup_day: dt.date | None = None, today: dt.date | None = None,
        entries: list[Entry] | None = None) -> dict:
    """Pull `windows` (given order: newest first for a backfill), every chosen set in a window before the next window starts. Never fatal per set: unsupported / error sets are
    recorded in meta_breakdown_coverage and skipped. A set Meta refuses at ad level is pulled at campaign level, then account level. A rejected token, or a rate limit that
    outlasts max_rate_waits, stops the run (raises). A StopRun from the stopper (quiet hours, time budget) ends the run cleanly: stats['stopped_early'] / ['stop_reason'].
    catchup_day: the recent-days catch-up of that day (a set x level x account refreshed that day is skipped, so a stopped catch-up resumes and runs once a day).
    Sets quarantined by earlier runs (see note_outcome) are skipped until their re-probe day unless named with `sets`. `entries` overrides the catalog selection (backfill stages)."""
    client = client or Client(meta_api.get_token())
    pause = pause or time.sleep
    accounts = accounts or meta_api.ACCOUNTS
    waiter = Waiter(client, max_rate_waits, stopper=stopper)
    entries = entries if entries is not None else select_sets(sets)
    today = today or dt.datetime.now(PT).date()
    live = conn is not None and not dry_run
    conn = conn if live else None
    if live:
        mark_excluded(conn)
        ensure_partitions(conn, min(w[0] for w in windows), max(w[1] for w in windows))
        client.async_first |= async_first_sets(conn)
    recent = recently_unsupported(conn) if (live and not sets) else set()
    quarantine = quarantined_sets(conn, today) if (live and not sets) else {}
    state: dict[str, str] = {}                  # set -> level that works (decided in the first window that pulls it)
    dead: set[str] = set()
    exhausted: set[str] = set()                 # sets whose older windows Meta no longer serves (retention): not asked again
    stats: dict = {"windows": len(windows), "windows_done": 0, "sets_requested": len(entries), "rows": 0, "levels": {}, "errors": {}, "stopped_early": False,
                   "sets_skipped_quarantined": 0, "quarantined_failing": 0, "sets_pulled": 0}
    final: dict[str, str] = {}
    try:
        _windows_loop(conn, live, client, waiter, stopper, windows, entries, accounts, backfill, restart, recent, state, dead, exhausted, stats, final, pause, catchup_day, quarantine, today)
    except StopRun as e:
        stats["stopped_early"], stats["stop_reason"] = True, str(e)
        db.log(f"stopping cleanly: {e}")
        if live:
            conn.rollback()
            for name, lv in state.items():                      # coverage shows the days committed so far
                record_coverage(conn, BY_NAME[name], lv, final.get(name, "ok") if final.get(name) != "unsupported" else "ok", None)
            conn.commit()
    stats["levels"] = dict(state)
    stats.update(sets_ok=sum(1 for v in final.values() if v == "ok"), sets_error=sum(1 for v in final.values() if v == "error"),
                 sets_unsupported=sum(1 for v in final.values() if v == "unsupported"), rate_waits=waiter.n, async_sets=sorted(client.async_sets), api_calls=client.calls,
                 quarantined_failing=sum(1 for n, k in quarantine.items() if k == "failing" and any(e.name == n for e in entries)))
    return stats


def _windows_loop(conn, live, client, waiter, stopper, windows, entries, accounts, backfill, restart, recent, state, dead, exhausted, stats, final, pause, catchup_day, quarantine, today):
    failed_run: set[str] = set()                # sets that already counted as a failing run in this run (one count per run, not per window)
    for wi, (ws, we) in enumerate(windows):
        w_rows0 = stats["rows"]
        db.log(f"window {wi + 1}/{len(windows)} {ws}..{we}: starting {len(entries)} sets")
        for si, entry in enumerate(entries):
            if stopper:
                stopper.check()
            if entry.name in quarantine:
                stats["sets_skipped_quarantined"] += 1
                continue
            if entry.name in dead or entry.name in exhausted:
                continue
            levels = [state[entry.name]] if entry.name in state else [lv for lv in LEVELS if (entry.name, lv) not in recent]
            st = stats                                          # pull_set_level adds rows / duplicates straight into the run stats (so a stop mid-set still counts them)
            result, kind_final, set_rows0, calls0, t_set = "unsupported", "", st["rows"], client.calls, time.monotonic()
            for lv in levels:
                t0, rows0 = time.monotonic(), st["rows"]
                status, err, kind = pull_set_level(conn, client, waiter, entry, lv, ws, we, accounts, st, backfill, restart, catchup_day)
                db.log(f"  set {entry.name} level {lv} window {ws}..{we}: {status}, {st['rows'] - rows0} rows, {time.monotonic() - t0:.1f}s")
                if entry.name in state and (status == "unsupported" or (status == "error" and RETENTION_RX.search(err or ""))):
                    # an older window than one Meta already served: Meta's own retention limit for this breakdown. Keep what we have, say so in coverage, move on.
                    exhausted.add(entry.name)
                    status, err, kind = "ok", f"Meta serves nothing before {we + dt.timedelta(days=1)} for this breakdown (retention limit): {db.scrub(err)[:200]}", ""
                    db.log(f"  {entry.name}: older windows not served, stopping this set at {we + dt.timedelta(days=1)}")
                if live:
                    record_coverage(conn, entry, lv, status, err)
                    conn.commit()
                if status == "unsupported":
                    db.log(f"  {entry.name} refused at {lv} level: {err}")
                    continue
                state[entry.name] = lv
                result, kind_final = status, kind
                if status != "ok":
                    if kind != "shape":                         # a deterministic shape error is quarantined below and must not keep the backfill open
                        stats["error_pieces"] = stats.get("error_pieces", 0) + 1
                    stats["errors"][entry.name] = f"{status} ({lv}): {err}"
                    db.log(f"  {entry.name}: {status}: {err}")
                break
            else:
                dead.add(entry.name)
                stats["errors"][entry.name] = "unsupported at every level"
            final[entry.name] = result
            if live and client.async_first and entry.name in client.async_first:
                mark_async_first(conn, entry.name, today)
                conn.commit()
            if live and result != "unsupported" and not (result == "error" and entry.name in failed_run):
                q = note_outcome(conn, entry.name, result, kind_final, st["rows"] - set_rows0, today)
                conn.commit()
                if result == "error":
                    failed_run.add(entry.name)
                if q:
                    quarantine[entry.name] = q
                    db.log(f"  {entry.name}: quarantined for {QUARANTINE_DAYS} days ({q}); asked again after that")
            if client.calls != calls0:
                ps = stats.setdefault("per_set", {}).setdefault(entry.name, {"status": result, "rows": 0, "seconds": 0.0})
                ps["status"], ps["rows"], ps["seconds"] = result, ps["rows"] + st["rows"] - set_rows0, round(ps["seconds"] + time.monotonic() - t_set, 1)
                stats["sets_pulled"] += 1
                pause(SET_PAUSE)                                # a set skipped as already done costs no request and no pause
        else:
            stats["windows_done"] += 1
            db.log(f"window {wi + 1}/{len(windows)} {ws}..{we} complete: {stats['rows'] - w_rows0} rows this window, {stats['rows']} total, "
                   f"{sum(1 for v in final.values() if v == 'ok')} sets ok, {sum(1 for v in final.values() if v == 'error')} errors, "
                   f"{sum(1 for v in final.values() if v == 'unsupported')} unsupported")
            continue
        break


# ---------------------------------------------------------------------------------------------------------------- the probe
def probe(client: Client, acct: str, an: str, day: dt.date, entries: list[Entry], sample_ads: int = 25, out=print) -> list[dict]:
    """One recent day on one account, read only, every set at AD level (campaign then account only when Meta refuses ad level). Per set: does Meta accept it (one request,
    limit 5), the shape of a row, and an estimated row count for the day at the level that worked. Ad level is counted on a sample of `sample_ads` of the day's ads
    (filtered by ad.id) and scaled to the day's ad count. Stops at the first rate-limit error (the client must be built with max_retries=0)."""
    res: list[dict] = []
    try:
        base = {"access_token": client.tok, "level": "ad", "limit": LIMIT, "fields": "ad_id", "time_range": json.dumps({"since": day.isoformat(), "until": day.isoformat()})}
        ads = sorted({str(r["ad_id"]) for r in client.fetch(acct, base, f"{an} ads {day}") if r.get("ad_id")})
    except meta_api.RateLimited:
        out("probe stopped: rate limited before the first set")
        return res
    step = max(1, len(ads) // sample_ads) if ads else 1
    sample = ads[::step][:sample_ads]
    out(f"probe {an} {day}: {len(ads)} ads with delivery, sample {len(sample)}")
    for entry in entries:
        rec: dict = {"set": entry.name, "action": entry.action, "accepted": False, "refused_levels": {}}
        try:
            got, used, used_level = None, 0, None
            for lv in LEVELS:
                for st in range(1 if entry.action else len(FIELD_LADDER)):
                    try:
                        got = client.fetch(acct, build_params(entry, lv, client.tok, day, day, st, limit=5), f"{an} probe {entry.name}", max_pages=1)
                        used, used_level = st, lv
                        break
                    except Unsupported as e:
                        rec["refused_levels"][lv] = db.scrub(e)[:200]
                if got is not None:
                    break
            if got is None:
                res.append(rec)
                out(f"  {entry.name:<72}refused at every level")
                continue
            rec.update(accepted=True, level=used_level, fields_step=used, first_page_rows=len(got))
            if got:
                try:
                    rec["dims_sample"] = extract_dims(entry, got[0]) if not entry.action else {b: None for b in entry.breakdowns}
                except ShapeError as e:
                    rec["shape_error"] = str(e)
                rec["row_keys"] = sorted(got[0].keys())
            if used_level == "ad":
                if sample:
                    raw = client.fetch(acct, build_params(entry, "ad", client.tok, day, day, used, filtering=[{"field": "ad.id", "operator": "IN", "value": sample}]),
                                       f"{an} probe-count {entry.name}", max_pages=10)
                    n = len(normalise(entry, "ad", acct, raw)) if raw else 0
                    rec["sample_rows"] = n
                    rec["rows_per_day_est"] = round(n / len(sample) * len(ads))
                    rec["row_count_basis"] = f"sample of {len(sample)} of {len(ads)} ads"
                else:
                    rec["rows_per_day_est"], rec["row_count_basis"] = 0, "no ads with delivery"
            else:
                raw = client.fetch(acct, build_params(entry, used_level, client.tok, day, day, used), f"{an} probe-count {entry.name}", max_pages=10)
                n = len(normalise(entry, used_level, acct, raw)) if raw else 0
                rec["rows_per_day_est"] = n
                rec["row_count_basis"] = "full day" if n < 5000 else "at least (10 pages)"
        except meta_api.RateLimited:
            rec["error"] = "rate limited: probe stopped"
            res.append(rec)
            out("probe stopped on a rate-limit error")
            break
        except (meta_api.GraphError, meta_api.TooBig, ShapeError, TooBigError) as e:
            rec["error"] = db.scrub(e)[:200]
        res.append(rec)
        out(f"  {entry.name:<72}{rec.get('level', '-'):<9}{'accepted' if rec['accepted'] else 'rejected':<10}{rec.get('rows_per_day_est', '')}")
        time.sleep(SET_PAUSE)
    return res


def backfill_estimate(rows_per_day: float, days: int = 365, chunk_days: int = CHUNK_DAYS) -> dict:
    """Rows and API calls for `days` days of one set on one account: pages of LIMIT rows plus one request per request window (more if Meta asks for splits)."""
    rows = rows_per_day * days
    return {"rows": round(rows), "calls": math.ceil(rows / LIMIT) + math.ceil(days / chunk_days)}


# ---------------------------------------------------------------------------------------------------------------- service mode (--auto)
def _special(conn, name: str):
    return conn.execute("SELECT window_start, window_end, rows FROM meta_breakdown_progress WHERE breakdown_set=%s AND level='-' AND account_id='-'", (name,)).fetchone()


def _set_special(conn, name: str, start: dt.date, end: dt.date, rows: int = 0) -> None:
    conn.execute("""INSERT INTO meta_breakdown_progress (breakdown_set, level, account_id, window_start, window_end, rows, done_at) VALUES (%s,'-','-',%s,%s,%s,now())
                    ON CONFLICT (breakdown_set, level, account_id, window_start, window_end) DO UPDATE SET rows = EXCLUDED.rows, done_at = now()""", (name, start, end, rows))
    conn.execute("DELETE FROM meta_breakdown_progress WHERE breakdown_set=%s AND level='-' AND account_id='-' AND (window_start, window_end) <> (%s, %s)", (name, start, end))


def get_anchor(conn):
    """(oldest day, anchor day = the --until of the backfill, max days) of the backfill, or None before the first --auto run."""
    r = _special(conn, ANCHOR_SET)
    return (r[0], r[1], int(r[2])) if r else None


def reset_progress(conn) -> None:
    """--restart: forget the anchor, the finished marker, the caught-up day and every recorded window."""
    conn.execute("DELETE FROM meta_breakdown_progress")
    conn.commit()


def try_lock(conn) -> bool:
    return bool(conn.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_KEY,)).fetchone()[0])


def _phase_line(kind: str, phase: str, window, done: int, remaining: int, advanced: bool | None = None) -> str:
    w = f"{window[0]}..{window[1]}" if window else "-"
    return f"run {kind}: phase={phase} window={w} sets_done={done} sets_remaining={remaining}" + ("" if advanced is None else f" backfill_advanced={'yes' if advanced else 'no'}")


def _first_open_window(conn, entries, windows, accounts, today):
    """(window, done, remaining) of the newest backfill window that still has sets to do; the last window with 0 remaining when everything is done."""
    for w in windows:
        d, r = backfill_counts(conn, entries, w[0], w[1], accounts, today)
        if r:
            return w, d, r
    return windows[-1], len(entries), 0


def backfill_stages(entries: list[Entry], windows: list[tuple[dt.date, dt.date]]) -> list[tuple[str, list, list[Entry]]]:
    """The backfill order: (1) every set, newest window only; (2) every set through all older windows. Each stage resumes from the recorded progress.
    (The former heavy probe and heavy stages are gone: the heavy sets are on EXCLUDED_SETS.)"""
    stages = [("light-newest-window", windows[:1], entries), ("light", windows[1:], entries)]
    return [x for x in stages if x[1] and x[2]]


def auto(conn, stopper: Stopper, names=None, max_days: int = DEFAULT_MAX_DAYS, window_days: int = 30, client: Client | None = None, accounts=None, today: dt.date | None = None,
         pause=None) -> dict:
    """One service run. 1) first start: store the backfill anchor (until = yesterday, max days). 2) catch-up, once a day per set (sets already refreshed that day are skipped, so a
    catch-up that did not finish resumes in the next run): the days since the last catch-up (at least the trailing 7, which Meta restates). It gets at most CATCHUP_SHARE of the
    run's time, so the backfill always has the rest. 3) the backfill (see backfill_stages): every non-excluded set, newest 30-day window first, out to the full depth. A StopRun (quiet hours, budget) ends the run cleanly after committing."""
    client = client or Client(meta_api.get_token())
    yesterday = (today - dt.timedelta(days=1)) if today else default_until()
    today_d = yesterday + dt.timedelta(days=1)
    anchor = get_anchor(conn)
    if anchor is None:
        until = yesterday
        anchor = (until - dt.timedelta(days=max_days - 1), until, max_days)
        _set_special(conn, ANCHOR_SET, anchor[0], anchor[1], max_days)
        conn.commit()
        db.log(f"backfill anchor stored: {anchor[0]}..{anchor[1]} ({max_days} days, {window_days}-day windows, newest first)")
    since, until, _ = anchor
    done = _special(conn, DONE_SET) is not None
    caught = _special(conn, CAUGHT_SET)
    out: dict = {"anchor": [since.isoformat(), until.isoformat()], "backfill_done": done, "stopped_early": False, "rows": 0}
    accts = accounts or meta_api.ACCOUNTS
    entries = select_sets(names)
    kw = dict(client=client, accounts=accounts, max_rate_waits=24, pause=pause, today=today_d)
    caught_end = caught[1] if caught else None
    adv0, phase, last_window, last_ents = backfill_rows(conn), "idle", None, entries
    if caught_end != yesterday and (done or until < yesterday):          # once a day: the new days, plus the trailing 7 (Meta restates recent days)
        start = max(min((caught_end or until) + dt.timedelta(days=1), yesterday - dt.timedelta(days=6)), since)
        windows = windows_newest_first(start, yesterday, window_days)
        clear_old_catchup_stamps(conn, yesterday)
        conn.commit()
        d, r = catchup_counts(conn, entries, yesterday, accts)
        phase, last_window = "catch-up", (start, yesterday)
        db.log(f"catch-up {start}..{yesterday}")
        db.log(_phase_line("start", phase, last_window, d, r))
        sub = stopper.child(CATCHUP_SHARE, f"catch-up time share ({CATCHUP_SHARE:.0%} of the run)")
        st = run(conn, windows, names, backfill=False, catchup_day=yesterday, stopper=sub, **kw)
        out["catch_up"] = st
        out["rows"] += st["rows"]
        if st["stopped_early"]:
            if stopper.reason():                                         # the run itself is over (budget, quiet hours)
                d, r = catchup_counts(conn, entries, yesterday, accts)
                out.update(stopped_early=True, stop_reason=st.get("stop_reason"))
                db.log(_phase_line("end", phase, last_window, d, r, backfill_rows(conn) > adv0))
                return out
            db.log(f"catch-up paused ({st.get('stop_reason')}); it resumes in the next run, the backfill takes the rest of this one")
        else:
            _set_special(conn, CAUGHT_SET, yesterday, yesterday)
            conn.commit()
    if not done:
        windows = windows_newest_first(since, until, window_days)
        merged: dict = {"rows": 0, "error_pieces": 0, "stopped_early": False, "stages": [], "blocking_quarantine": 0, "per_set": {}}
        finished = True
        for label, w, ents in backfill_stages(entries, windows):
            win, d, r = _first_open_window(conn, ents, w, accts, today_d)
            if r == 0:
                continue                                                 # every window of this stage is already recorded
            phase, last_window, last_ents = f"backfill/{label}", win, ents
            db.log(_phase_line("start", phase, win, d, r))
            st = run(conn, w, names, backfill=True, stopper=stopper, entries=ents, **kw)
            out["rows"] += st["rows"]
            merged["rows"] += st["rows"]
            merged["error_pieces"] += st.get("error_pieces", 0)
            merged["blocking_quarantine"] += st.get("quarantined_failing", 0)
            merged["per_set"].update(st.get("per_set", {}))
            merged["stages"].append({"stage": label, "windows_done": st["windows_done"], "windows": st["windows"], "rows": st["rows"], "stopped_early": st["stopped_early"]})
            if st["stopped_early"]:
                finished = False
                merged["stopped_early"] = True
                out.update(stopped_early=True, stop_reason=st.get("stop_reason"))
                break
            elif st["windows_done"] != st["windows"]:
                finished = False
        out["backfill"] = merged
        if finished and not merged["error_pieces"] and not merged["blocking_quarantine"] and not merged["stopped_early"]:
            _set_special(conn, DONE_SET, yesterday, yesterday)
            conn.commit()
            out["backfill_done"] = True
            db.log("backfill finished: from now on each run pulls the trailing 7 days")
    if last_window is None:
        db.log(_phase_line("start", phase, None, len(entries), 0))
        db.log(_phase_line("end", phase, None, len(entries), 0, False))
    elif phase.startswith("backfill"):
        d, r = backfill_counts(conn, last_ents, last_window[0], last_window[1], accts, today_d)
        db.log(_phase_line("end", phase, last_window, d, r, backfill_rows(conn) > adv0))
    else:
        d, r = catchup_counts(conn, entries, yesterday, accts)
        db.log(_phase_line("end", phase, last_window, d, r, backfill_rows(conn) > adv0))
    return out


# ---------------------------------------------------------------------------------------------------------------- command line
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--auto", action="store_true", help="service mode: continue the backfill until finished, then pull the trailing 7 days (anchor stored in the database)")
    ap.add_argument("--days", type=int, default=7, help="nightly mode: trailing days to pull and upsert (default 7, the settle window)")
    ap.add_argument("--since", help="backfill: oldest day (default: --max-days before --until)")
    ap.add_argument("--until", help="newest day (default: yesterday, Pacific). Pass the same value when resuming a backfill after midnight")
    ap.add_argument("--max-days", type=int, help="backfill: how far back from --until (default 365 when --since is not given). Giving either makes this a backfill; with --auto it sets the depth on first start")
    ap.add_argument("--window-days", type=int, default=30, help="backfill window length; windows run newest first, each fully committed before the next (default 30)")
    ap.add_argument("--sets", help="set names separated by ';' (a set may contain commas: age,gender); a plus also works inside a set (age+gender); default: the whole catalog")
    ap.add_argument("--dry-run", action="store_true", help="pull and count, write nothing")
    ap.add_argument("--restart", action="store_true", help="forget the stored anchor and every recorded backfill window, pull everything again")
    ap.add_argument("--list", action="store_true", help="print the catalog and exit")
    ap.add_argument("--fill-funnel", action="store_true", help="history fill of add_to_cart / initiate_checkout (UPDATE of those two columns of existing rows only) for --since..--until and --sets (default: the seven cuts the why tool uses)")
    ap.add_argument("--probe", action="store_true", help="read-only probe for one day on the main account (no database writes)")
    ap.add_argument("--probe-day", help="day for --probe (default: 2 days ago, Pacific)")
    ap.add_argument("--probe-out", help="write the probe result as JSON to this path")
    ap.add_argument("--max-minutes", type=float, default=DEFAULT_MAX_MINUTES, help=f"wall-clock budget per run, then stop cleanly and exit 0 (default {DEFAULT_MAX_MINUTES:g}; 0 = none)")
    ap.add_argument("--ignore-quiet", action="store_true", help="ignore the quiet hours (META_BREAKDOWNS_QUIET_UTC, default 12:30-15:30 UTC) for a manual run")
    a = ap.parse_args(argv)
    names = parse_sets_arg(a.sets)
    if a.list:
        for e in ordered(CATALOG):
            print(f"{e.name:<75}tier {e.tier}  {'action' if e.action else 'standard'}" + ("  EXCLUDED (never pulled)" if e.name in excluded_names() else ""))
        return 0
    entries = select_sets(names)
    if a.probe:
        try:
            tok = meta_api.get_token()
        except meta_api.GraphError as e:
            print("probe skipped: no Meta token available locally:", db.scrub(e)[:120])
            return 2
        day = dt.date.fromisoformat(a.probe_day) if a.probe_day else dt.datetime.now(PT).date() - dt.timedelta(days=2)
        acct, an = meta_api.ACCOUNTS[0]
        res = probe(Client(tok, max_retries=0), acct, an, day, entries)
        if a.probe_out:
            with open(a.probe_out, "w") as f:
                json.dump({"day": day.isoformat(), "account": an, "results": res}, f, indent=1, default=str)
        return 0
    until = dt.date.fromisoformat(a.until) if a.until else default_until()
    if a.fill_funnel:
        if not a.since:
            ap.error("--fill-funnel needs --since")
        quiet = None if a.ignore_quiet else parse_quiet(os.environ.get("META_BREAKDOWNS_QUIET_UTC", DEFAULT_QUIET_UTC))
        stopper = Stopper(a.max_minutes, quiet)
        with db.connect() as conn:
            if not conn.execute("SELECT pg_try_advisory_lock(%s)", (FILL_LOCK_KEY,)).fetchone()[0]:
                db.log("another funnel fill holds the lock; exiting")
                return 0
            if stopper.reason() and in_quiet(stopper.quiet, stopper.now()):
                db.log("quiet hours: nothing started")
                return 0
            with db.JobRun("meta_breakdowns_fill") as job:
                stats = fill_funnel(conn, dt.date.fromisoformat(a.since), until, names, stopper)
                job.stats = stats
                job.rows_written = sum(v["filled"] for v in stats["per_set"].values())
                job.data_through = until
                db.log(f"funnel fill done: {json.dumps(stats, default=str)[:600]}")
            conn.execute("SELECT pg_advisory_unlock(%s)", (FILL_LOCK_KEY,))
        return 0
    backfill = bool(a.since or a.max_days) and not a.auto
    quiet = None if a.ignore_quiet else parse_quiet(os.environ.get("META_BREAKDOWNS_QUIET_UTC", DEFAULT_QUIET_UTC))
    if a.dry_run and not a.auto:
        windows = _manual_windows(a, until, backfill)
        print(json.dumps(run(None, windows, names, dry_run=True, backfill=backfill, restart=a.restart, max_rate_waits=24 if backfill else 3), indent=1))
        return 0
    stopper = Stopper(a.max_minutes, quiet if (a.auto or backfill) else None)         # the legacy nightly window (trailing days) ignores quiet hours; it only honours the budget
    with db.connect() as conn:
        if not try_lock(conn):
            db.log("another meta_breakdowns run holds the lock; exiting")
            return 0
        if stopper.reason() and in_quiet(stopper.quiet, stopper.now()):
            db.log(f"quiet hours ({os.environ.get('META_BREAKDOWNS_QUIET_UTC', DEFAULT_QUIET_UTC)} UTC): nothing started")
            return 0
        if a.restart:
            reset_progress(conn)
        with db.JobRun("meta_breakdowns") as job:
            try:
                if a.auto:
                    stats = auto(conn, stopper, names, a.max_days or DEFAULT_MAX_DAYS, a.window_days)
                else:
                    stats = run(conn, _manual_windows(a, until, backfill), names, stopper=stopper, backfill=backfill, restart=a.restart, max_rate_waits=24 if backfill else 3)
            except (meta_api.TokenRejected, meta_api.RateLimited) as e:
                raise db.JobFailed(f"meta breakdown pull stopped: {db.scrub(e)[:200]}") from None
            job.stats = stats
            job.rows_written = stats["rows"]
            job.data_through = default_until() if a.auto else until
            db.log(f"breakdowns run done: rows {stats['rows']}" + (f", stopped early: {stats.get('stop_reason')}" if stats.get("stopped_early") else ""))
        conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))
    return 0


def _manual_windows(a, until: dt.date, backfill: bool) -> list[tuple[dt.date, dt.date]]:
    if backfill:
        since = dt.date.fromisoformat(a.since) if a.since else until - dt.timedelta(days=(a.max_days or DEFAULT_MAX_DAYS) - 1)
        if a.since and a.max_days:
            since = max(since, until - dt.timedelta(days=a.max_days - 1))
        return windows_newest_first(since, until, a.window_days)
    return [(until - dt.timedelta(days=a.days - 1), until)]


if __name__ == "__main__":
    sys.exit(main())
