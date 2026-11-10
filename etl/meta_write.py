"""The only module in this repo allowed to write to Meta. It uploads image and video files to the Lonvera ad account, builds creatives, clones an ad set from a template
and creates ads and ad sets, and every ad and ad set it creates is created paused. It never turns anything on, never edits an existing object and never removes anything:
there is exactly one POST helper and it can only post to an ad account edge (for example act_123/ads), never to an existing object id.

The token comes only from the environment variable META_LAUNCH_TOKEN (not the nightly read token). It is never logged, printed, returned or placed in an exception: every
message is scrubbed. All functions take an optional `http` transport so tests run with no network. Reads (GET) retry on rate limits and short outages; writes (POST)
retry only on rate limits, because Meta rejects those requests before doing anything, so a retry cannot create a duplicate.
Callers should call `find_ad_by_name` before creating an ad, so a half-finished earlier run is adopted instead of repeated."""
from __future__ import annotations

import json
import os
import time

from . import db
from .meta_api import G, RATE_CODES, TOKEN_CODES

TOKEN_ENV = "META_LAUNCH_TOKEN"
SINGLE_POST_MAX = 50 * 1024 * 1024               # one request up to 50 MB
VIDEO_HARD_CAP = 500 * 1024 * 1024               # refuse anything larger
IMAGE_HARD_CAP = 30 * 1024 * 1024
CHUNK = 8 * 1024 * 1024
_PAUSED = "PAUSED"                               # the only status this module ever sends
POST_EDGES = {"adimages", "advideos", "adcreatives", "ads", "adsets"}
DEFAULT_URL_TAGS = "utm_source=facebook&utm_medium=paid_social&utm_campaign={{campaign.name}}&utm_content={{ad.name}}"
DEFAULT_CTA = {"image": "LEARN_MORE", "video": "SHOP_NOW"}
ADSET_FIELDS = "account_id,campaign_id,billing_event,optimization_goal,bid_strategy,promoted_object,attribution_spec,targeting,dsa_beneficiary,dsa_payor"


class MetaWriteError(Exception):
    """A write-side problem. The text is always scrubbed and never contains the token."""


class MissingToken(MetaWriteError):
    pass


class FileTooBig(MetaWriteError):
    pass


def get_token() -> str:
    t = os.environ.get(TOKEN_ENV)
    if not t:
        raise MissingToken(f"No launch token: set {TOKEN_ENV} on the service that creates ads. The nightly read token is never used for this.")
    return t


def scrub(text, token: str | None = None) -> str:
    """db.scrub plus the launch token itself, wherever it appears."""
    s = str(text)
    t = token or os.environ.get(TOKEN_ENV)
    if t and len(t) > 3:
        s = s.replace(t, "***")
    return db.scrub(s)


def parse_usage(headers) -> float:
    """Highest usage percentage Meta reports in its usage headers (x-app-usage, x-ad-account-usage, x-business-use-case-usage); 0.0 when absent."""
    best = 0.0

    def walk(v):
        nonlocal best
        if isinstance(v, dict):
            for k, x in v.items():
                if k in ("call_count", "total_cputime", "total_time", "acc_id_util_pct") and isinstance(x, (int, float)):
                    best = max(best, float(x))
                else:
                    walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
    for name in ("x-app-usage", "x-ad-account-usage", "x-business-use-case-usage"):
        raw = (headers or {}).get(name) or (headers or {}).get(name.title())
        if raw:
            try:
                walk(json.loads(raw))
            except (ValueError, TypeError):
                pass
    return best


class Transport:
    """Thin HTTP layer: adds the token, retries, scrubs errors, tracks the latest usage percentage. Tests pass a fake session and a no-op sleep."""

    def __init__(self, token: str | None = None, session=None, sleep=time.sleep, max_retries: int = 7):
        self.token = token or get_token()
        if session is None:
            import requests
            session = requests.Session()
        self.session, self.sleep, self.max_retries = session, sleep, max_retries
        self.usage_pct = 0.0

    def _call(self, method: str, url: str, what: str, params=None, data=None, files=None, retry_transient=True) -> dict:
        fails = 0
        while True:
            code, j = 0, None
            try:
                if method == "GET":
                    r = self.session.get(url, params={**(params or {}), "access_token": self.token}, timeout=180)
                else:
                    r = self.session.post(url, data={**(data or {}), "access_token": self.token}, files=files, timeout=900)
                code = r.status_code
                self.usage_pct = parse_usage(getattr(r, "headers", None))
                j = r.json()
            except Exception as e:                                          # noqa: BLE001 - network and JSON problems
                j = {"error": {"message": scrub(e, self.token)[:200], "code": -1}}
            if "error" not in j:
                return j
            err = j["error"]
            msg = scrub(err.get("message", ""), self.token)
            detail = scrub(err.get("error_user_msg", ""), self.token)
            ecode = err.get("code")
            if ecode in TOKEN_CODES:
                raise MetaWriteError(f"{what}: Meta rejected the launch token (code {ecode}). Replace META_LAUNCH_TOKEN.")
            rate = code == 429 or ecode in RATE_CODES or "too many calls" in msg.lower() or "rate limit" in msg.lower()
            transient = retry_transient and (code >= 500 or ecode in (2, -1) or code == 0)
            if not (rate or transient):
                raise MetaWriteError(f"{what}: HTTP {code} code {ecode}: {msg[:200]}" + (f" | {detail[:200]}" if detail else ""))
            fails += 1
            if fails > self.max_retries:
                raise MetaWriteError(f"{what}: gave up after {self.max_retries} retries: HTTP {code} code {ecode}: {msg[:160]}")
            wait = min(900, 60 * 2 ** (fails - 1)) if rate else 15 * fails
            db.log(f"  {what}: {'rate limit' if rate else 'transient error'} (HTTP {code}, code {ecode}) retry {fails}/{self.max_retries} in {wait}s")
            self.sleep(wait)

    def get(self, path: str, what: str, params: dict | None = None) -> dict:
        return self._call("GET", f"{G}/{path}", what, params=params)

    def post_edge(self, account_id: str, edge: str, what: str, data: dict | None = None, files=None, validate_only: bool = False) -> dict:
        """The one and only write call: a POST to an ad account edge (creates something new). Existing objects are never posted to."""
        if not str(account_id).startswith("act_") or edge not in POST_EDGES:
            raise MetaWriteError(f"{what}: refused, writes go only to an ad account's {', '.join(sorted(POST_EDGES))}.")
        body = dict(data or {})
        if validate_only:
            body["execution_options"] = json.dumps(["validate_only"])
        return self._call("POST", f"{G}/{account_id}/{edge}", what, data=body, files=files, retry_transient=False)


def _http(http):
    return http if http is not None else Transport()


def _acct(account_id: str) -> str:
    a = str(account_id)
    return a if a.startswith("act_") else "act_" + a


# ---------- media ----------
def upload_image(account_id: str, data: bytes, filename: str, http=None) -> str:
    """Upload image bytes to the ad account and return Meta's image hash."""
    if len(data) > IMAGE_HARD_CAP:
        raise FileTooBig(f"{filename}: images over 30 MB are not accepted.")
    h = _http(http)
    j = h.post_edge(_acct(account_id), "adimages", f"upload image {filename}", files={"filename": (filename, data)})
    imgs = (j.get("images") or {})
    first = next(iter(imgs.values()), None)
    if not first or not first.get("hash"):
        raise MetaWriteError(f"upload image {filename}: Meta returned no image hash.")
    return str(first["hash"])


def upload_video(account_id: str, source, size: int, filename: str, http=None) -> str:
    """Upload a video and return its id. `source` is a file path or an open binary file. One request up to 50 MB, chunked start/transfer/finish above, refused above 500 MB."""
    if size > VIDEO_HARD_CAP:
        raise FileTooBig(f"{filename}: videos over 500 MB are not accepted ({size // (1024 * 1024)} MB).")
    h = _http(http)
    acct = _acct(account_id)
    opened = isinstance(source, (str, os.PathLike))
    f = open(source, "rb") if opened else source
    try:
        if size <= SINGLE_POST_MAX:
            f.seek(0)
            j = h.post_edge(acct, "advideos", f"upload video {filename}", data={"name": filename}, files={"source": (filename, f.read())})
            if not j.get("id"):
                raise MetaWriteError(f"upload video {filename}: Meta returned no video id.")
            return str(j["id"])
        start = h.post_edge(acct, "advideos", f"start upload {filename}", data={"upload_phase": "start", "file_size": str(size)})
        sid, vid = start.get("upload_session_id"), start.get("video_id")
        lo, hi = int(start.get("start_offset", 0)), int(start.get("end_offset", 0))
        if not sid or not vid:
            raise MetaWriteError(f"start upload {filename}: Meta returned no upload session.")
        while lo < hi:
            f.seek(lo)
            piece = f.read(hi - lo)
            t = h.post_edge(acct, "advideos", f"upload video part {filename}", data={"upload_phase": "transfer", "upload_session_id": sid, "start_offset": str(lo)},
                            files={"video_file_chunk": (filename, piece)})
            lo, hi = int(t.get("start_offset", hi)), int(t.get("end_offset", hi))
        fin = h.post_edge(acct, "advideos", f"finish upload {filename}", data={"upload_phase": "finish", "upload_session_id": sid, "title": filename})
        if fin.get("success") is False:
            raise MetaWriteError(f"finish upload {filename}: Meta did not accept the finished upload.")
        return str(vid)
    finally:
        if opened:
            f.close()


def wait_video_ready(video_id: str, timeout: int = 900, http=None, sleep=time.sleep, clock=time.monotonic, poll: int = 10) -> bool:
    """Poll until Meta has processed the video. Returns True when ready; raises if Meta reports an error or the wait runs out."""
    h = _http(http)
    t0 = clock()
    while True:
        j = h.get(str(video_id), "video status", {"fields": "status"})
        st = (j.get("status") or {}).get("video_status")
        if st == "ready":
            return True
        if st == "error":
            raise MetaWriteError(f"video {video_id}: Meta could not process the video.")
        if clock() - t0 >= timeout:
            raise MetaWriteError(f"video {video_id}: still processing after {timeout} seconds.")
        sleep(poll)


def video_thumbnail(video_id: str, http=None) -> str:
    """Preferred thumbnail url for a processed video (first one if none is marked preferred)."""
    j = _http(http).get(f"{video_id}/thumbnails", "video thumbnails")
    th = j.get("data") or []
    if not th:
        raise MetaWriteError(f"video {video_id}: Meta has no thumbnail yet.")
    return str(next((t["uri"] for t in th if t.get("is_preferred")), th[0]["uri"]))


# ---------- creative ----------
def build_creative_spec(name: str, page_id: str, instagram_actor_id: str | None, link: str, kind: str, bodies: list[str], titles: list[str],
                        image_hash: str | None = None, video_id: str | None = None, thumbnail_url: str | None = None, cta: str | None = None,
                        url_tags: str = DEFAULT_URL_TAGS, feature_keys: list[str] | tuple[str, ...] = ()) -> dict:
    """Pure function: the creative payload (not yet JSON encoded). Up to 3 texts and 3 headlines, a call to action (Learn more on images, Shop now on videos by default),
    URL tags, and every key in `feature_keys` (taken from a current ad at prepare time) set to opt out under Advantage+ creative."""
    if kind not in ("image", "video"):
        raise MetaWriteError("kind must be 'image' or 'video'.")
    bodies = [b.strip() for b in bodies if b and b.strip()]
    titles = [t.strip() for t in titles if t and t.strip()]
    if not 1 <= len(bodies) <= 3 or not 1 <= len(titles) <= 3:
        raise MetaWriteError("A creative needs 1 to 3 primary texts and 1 to 3 headlines.")
    if kind == "image" and not image_hash:
        raise MetaWriteError("An image ad needs an image hash.")
    if kind == "video" and not (video_id and thumbnail_url):
        raise MetaWriteError("A video ad needs a video id and a thumbnail.")
    story = {"page_id": str(page_id)}
    if instagram_actor_id:
        story["instagram_actor_id"] = str(instagram_actor_id)
    feed = {"bodies": [{"text": b} for b in bodies], "titles": [{"text": t} for t in titles], "link_urls": [{"website_url": link}],
            "call_to_action_types": [cta or DEFAULT_CTA[kind]]}
    if kind == "image":
        feed.update(ad_formats=["SINGLE_IMAGE"], images=[{"hash": image_hash}])
    else:
        feed.update(ad_formats=["SINGLE_VIDEO"], videos=[{"video_id": str(video_id), "thumbnail_url": thumbnail_url}])
    spec = {"name": name[:90], "object_story_spec": story, "asset_feed_spec": feed, "url_tags": url_tags}
    keys = [k for k in dict.fromkeys(feature_keys) if k]
    if keys:
        spec["degrees_of_freedom_spec"] = {"creative_features_spec": {k: {"enroll_status": "OPT_OUT"} for k in keys}}
    return spec


def create_creative(account_id: str, spec: dict, validate_only: bool = False, http=None) -> str | None:
    """Create the creative. Returns its id (None when validate_only, since nothing is created)."""
    body = {k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in spec.items()}
    j = _http(http).post_edge(_acct(account_id), "adcreatives", f"create creative {spec.get('name', '')[:40]}", data=body, validate_only=validate_only)
    if validate_only:
        return None
    if not j.get("id"):
        raise MetaWriteError("create creative: Meta returned no creative id.")
    return str(j["id"])


# ---------- ads and ad sets ----------
def find_ad_by_name(account_id: str, name: str, http=None) -> dict | None:
    """Exact-name lookup (GET) so a caller can adopt an ad an earlier run already made. Returns {id, name, effective_status} or None."""
    j = _http(http).get(f"{_acct(account_id)}/ads", "find ad by name", {"fields": "id,name,effective_status", "limit": 25,
                                                                         "filtering": json.dumps([{"field": "name", "operator": "EQUAL", "value": name}])})
    for a in j.get("data") or []:
        if a.get("name") == name:
            return {"id": str(a["id"]), "name": a["name"], "effective_status": a.get("effective_status")}
    return None


def create_paused_ad(account_id: str, adset_id: str, creative_id: str, name: str, validate_only: bool = False, http=None) -> str | None:
    """Create an ad that is paused. There is deliberately no status argument."""
    data = {"name": name, "adset_id": str(adset_id), "status": _PAUSED, "creative": json.dumps({"creative_id": str(creative_id)})}
    j = _http(http).post_edge(_acct(account_id), "ads", f"create ad {name[:40]}", data=data, validate_only=validate_only)
    if validate_only:
        return None
    if not j.get("id"):
        raise MetaWriteError(f"create ad {name[:40]}: Meta returned no ad id.")
    return str(j["id"])


def clone_adset_paused(template_adset_id: str, name: str, daily_budget: int, bid_amount: int, validate_only: bool = False, http=None) -> str | None:
    """Read a template ad set (GET) and create a new paused ad set from it in the same campaign. Amounts are in the account's minor units (cents). Drops the
    subscriber audience and adds Explore to the Instagram placements whenever Explore Home is present, because Meta requires both."""
    h = _http(http)
    s = h.get(str(template_adset_id), "read template ad set", {"fields": ADSET_FIELDS})
    tg = json.loads(json.dumps(s.get("targeting") or {}))
    tg.pop("subscriber_universe", None)
    ip = tg.get("instagram_positions") or []
    if "explore_home" in ip and "explore" not in ip:
        ip.append("explore")
        tg["instagram_positions"] = ip
    acct = s.get("account_id")
    if not acct or not s.get("campaign_id"):
        raise MetaWriteError("read template ad set: Meta did not return the account or campaign.")
    body = {"name": name, "campaign_id": s["campaign_id"], "status": _PAUSED, "billing_event": s["billing_event"], "optimization_goal": s["optimization_goal"],
            "daily_budget": str(int(daily_budget)), "bid_amount": str(int(bid_amount)), "targeting": json.dumps(tg),
            "dsa_beneficiary": s.get("dsa_beneficiary", "Lonvera"), "dsa_payor": s.get("dsa_payor", "Lonvera")}
    if s.get("bid_strategy"):
        body["bid_strategy"] = s["bid_strategy"]
    for k in ("promoted_object", "attribution_spec"):
        if s.get(k) is not None:
            body[k] = json.dumps(s[k])
    j = h.post_edge(_acct(acct), "adsets", f"create ad set {name[:40]}", data=body, validate_only=validate_only)
    if validate_only:
        return None
    if not j.get("id"):
        raise MetaWriteError(f"create ad set {name[:40]}: Meta returned no ad set id.")
    return str(j["id"])


def usage_pct(http) -> float:
    """Latest usage percentage seen by this transport; a caller pauses new work at 75."""
    return float(getattr(http, "usage_pct", 0.0))
