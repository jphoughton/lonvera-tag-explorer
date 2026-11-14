"""Scoped API keys (table api_keys, migration 205).

A key looks like lvk_ plus 40 characters from secrets.token_urlsafe (240 bits). Only its sha256 hash is stored. The scope 'read_assign' lets a key read everything the app serves (GET and HEAD) and
make exactly five writes on the production board: POST /api/priorities/assignments, POST /api/priorities/tickets (the ticket contract: concept_name, product, variations, cluster, concept_kind, vendor, for_week), PATCH /api/priorities/assignments/<aid>, POST .../<aid>/move and POST .../<aid>/comments (not vendor capacity). Nothing else is writable with a key (see ALLOWED_WRITES). app/auth.py enforces this in its
request guard; keys never satisfy the launch gate, never open the login route and are refused on the route that returns vendor link tokens (BLOCKED_READS).
"""
from __future__ import annotations

import hashlib
import re
import secrets

import psycopg
from flask import Request

KEY_PREFIX = "lvk_"
KEY_BODY_CHARS = 40
KEY_RE = re.compile(r"lvk_[A-Za-z0-9_-]{40}")
SCOPE = "read_assign"
PARTNER_SCOPE = "partner_push"
PARTNER_READ_ALL_SCOPE = "partner_read_all"          # a partner key that may also READ everything except the secret-bearing routes (migration 265); writes are identical to partner_push
PARTNER_SCOPES = (PARTNER_SCOPE, PARTNER_READ_ALL_SCOPE)
SCOPES = (SCOPE, PARTNER_SCOPE, PARTNER_READ_ALL_SCOPE)
SAFE_METHODS = ("GET", "HEAD")
ALLOWED_WRITES = frozenset({("POST", "/api/priorities/assignments"), ("PATCH", "/api/priorities/assignments/<int:aid>"), ("POST", "/api/priorities/assignments/<int:aid>/move"),
                            ("POST", "/api/priorities/assignments/<int:aid>/comments"), ("POST", "/api/priorities/tickets")})      # (method, Flask rule) pairs, nothing else
# What a partner_push key may do (migration 261). WRITES: the partner routes below, nothing else. READS: only the creative and performance families of PARTNER_READ_RULES (default deny: a GET that is
# not on that list is a 403). Every /api/ and /media/ GET rule of the app must be in PARTNER_READ_RULES or PARTNER_READ_BLOCKED (tests/api/test_partner_keys.py fails when a new route is in neither),
# so a future route can never leak to a partner by default. A partner reads its OWN tickets only through /api/priorities/partner/tickets. Every route also checks the key's vendor in app/partner_tickets.py.
PARTNER_ROUTES = frozenset({("POST", "/api/priorities/tickets"), ("GET", "/api/priorities/partner/tickets"), ("GET", "/api/priorities/partner/all-tickets"), ("GET", "/api/priorities/partner/all-tickets/<int:tid>"), ("PATCH", "/api/priorities/partner/tickets/<int:tid>"),
                            ("POST", "/api/priorities/partner/tickets/<int:tid>/creative"), ("POST", "/api/priorities/partner/creative"), ("GET", "/api/priorities/partner/contract")})
PARTNER_READ_RULES = frozenset({
    # ads and ad pages (spend, return, hook, tags, transcript of that ad), the product / family / tag tree views, campaigns, platform and Google performance, test-to-scale
    "/api/ads/<aint:row_id>", "/api/ads/<aint:row_id>/preview", "/api/meta-ads/<ad_id>/preview",
    "/api/products", "/api/products/<slug>/ads", "/api/products/<slug>/campaigns", "/api/products/<slug>/coverage", "/api/products/<slug>/families", "/api/products/<slug>/rollup",
    "/api/products/<slug>/stage-daily", "/api/products/<slug>/tree", "/api/products/<slug>/trend", "/api/products/<slug>/unmatched",
    "/api/campaigns", "/api/campaigns/all", "/api/campaigns/trend", "/api/platforms/kpis", "/api/platforms/overview", "/api/platforms/trend", "/api/google/rollup", "/api/google/trend",
    "/api/promotions", "/api/promotions/scale", "/api/trends", "/api/meta/breakdowns", "/api/meta/breakdowns/coverage",
    # tags, hooks, landing pages and their top ads, competitors and their concepts
    "/api/tag-lookup", "/api/landing-pages", "/api/landing-pages/<int:page_id>", "/api/competitors", "/api/competitors/<path:brand>", "/api/competitors/media/<library_id>", "/api/competitors/video/<library_id>", "/api/competitors/lp-media/<lp_id>",
    # the weekly ideas list, what is working on one line, and the Communication Overview tree (product, cluster, ICP, pain point) the tags come from
    "/api/priorities", "/api/priorities/lines/<int:line_id>/insights", "/api/priorities/manual/options",
    # thumbnails and creative files
    "/media/<aint:asset_id>"})
PARTNER_READ_BLOCKED_PREFIXES = ("/api/projection/",)         # finance, plan, cash, P&L, financing, inventory, supply, purchase orders, scenarios: the whole projection module, whatever routes it gains
PARTNER_READ_BLOCKED = frozenset({
    # the plan behind Trends, inventory history, the daily story (finance facts), tie-out and status
    "/api/trends/plan", "/api/inventory/history", "/api/daily-story", "/api/source/tieout", "/api/status",
    # Amazon (fees, settlement, profit, ads cost), email and SMS revenue
    "/api/amazon/ad-search-terms", "/api/amazon/ad-search-terms/matches", "/api/amazon/campaign-trend", "/api/amazon/campaigns", "/api/amazon/fees", "/api/amazon/keywords", "/api/amazon/negatives",
    "/api/amazon/overview", "/api/amazon/products", "/api/amazon/search-terms", "/api/amazon/search-terms/history", "/api/amazon/trend",
    "/api/email/campaigns", "/api/email/flows", "/api/email/flows/<flow_id>/messages", "/api/email/message/<message_id>", "/api/email/summary", "/api/email/timeseries",
    # keys, saved-view tokens, vendor link tokens, other vendors' tickets and everything about them
    "/api/brain/key", "/api/priorities.csv", "/api/priorities/views", "/api/priorities/views/<token>", "/api/priorities/views/<token>/csv",
    "/api/priorities/vendor-links", "/api/priorities/vendor/<token>", "/api/priorities/vendor/<token>/<int:aid>/comments", "/api/priorities/vendor/<token>/<int:aid>/detail",
    "/api/priorities/assignments", "/api/priorities/assignments/<int:aid>/comments", "/api/priorities/assignments/<int:aid>/detail", "/api/priorities/assignments/<int:aid>/launch",
    "/api/priorities/assignments/<int:aid>/transcripts", "/api/priorities/board", "/api/priorities/vendor-capacity", "/api/priorities/launch/settings",
    # the team's own idea notes (manual concepts carry internal notes)
    "/api/priorities/manual", "/api/priorities/manual/<int:rid>"})
# Reads that stay blocked for EVERY partner scope, partner_read_all included, because they return or open something that gives access: saved-view tokens, vendor link tokens, the brain key.
# (API key admin has no HTTP route at all; /mcp is refused separately in app/brain_mcp.py; no route serves key hashes or environment values.)
SECRET_BLOCKED = frozenset({"/api/brain/key", "/api/priorities/views", "/api/priorities/views/<token>", "/api/priorities/views/<token>/csv", "/api/priorities/vendor-links",
                            "/api/priorities/vendor/<token>", "/api/priorities/vendor/<token>/<int:aid>/comments", "/api/priorities/vendor/<token>/<int:aid>/detail"})
PARTNER_MESSAGE = "This API key can only push tickets and creative links for its own vendor, look at tickets, and read the data its scope allows (see docs/PARTNER_API.md)."
BLOCKED_READS = frozenset({"/api/priorities/vendor-links"})        # returns vendor link tokens, which are credentials for the vendor pages: a login can see them, a key cannot
TOUCH_EVERY_SEC = 60
READ_ONLY_MESSAGE = "This API key is read only except for assigning items on the production board."
BLOCKED_MESSAGE = "This API key cannot read this."


def generate_key() -> str:
    """A new random key: 'lvk_' + 40 URL-safe characters."""
    return KEY_PREFIX + secrets.token_urlsafe(48)[:KEY_BODY_CHARS]


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def well_formed(raw: str) -> bool:
    return bool(KEY_RE.fullmatch(raw or ""))


def lookup(conn, raw: str) -> dict | None:
    """The active key row {id, name, scope} for a raw key, or None (unknown, revoked, malformed). The row is found by an equality match on the sha256 hash (an index lookup: the raw key is
    never compared, so there is no timing signal about it). last_used_at is touched at most once a minute, in its own short read-write transaction; a failed touch never fails the request.
    conn is any connection (the app's read-only one is fine)."""
    if not well_formed(raw):
        return None
    sql = ("SELECT id, name, scope, {v} AS vendor, last_used_at, (last_used_at IS NULL OR last_used_at < now() - make_interval(secs => %s)) AS stale FROM api_keys "
           "WHERE key_hash = %s AND revoked_at IS NULL")
    try:
        row = conn.execute(sql.format(v="vendor"), (TOUCH_EVERY_SEC, hash_key(raw))).fetchone()
    except psycopg.errors.UndefinedColumn:                 # migration 261 not applied yet: only read_assign keys exist
        conn.rollback()
        row = conn.execute(sql.format(v="NULL::text"), (TOUCH_EVERY_SEC, hash_key(raw))).fetchone()
    if row is None:
        return None
    row = row if isinstance(row, dict) else dict(zip(("id", "name", "scope", "vendor", "last_used_at", "stale"), row))
    if row["stale"]:
        _touch(row["id"])
    return {"id": row["id"], "name": row["name"], "scope": row["scope"], "vendor": row["vendor"]}


def _touch(key_id: int) -> None:
    try:
        from .projection import rwdb                       # the app's own pool is read-only
        with rwdb.get_rw() as w:
            w.execute("UPDATE api_keys SET last_used_at = now() WHERE id = %s AND (last_used_at IS NULL OR last_used_at < now() - make_interval(secs => %s))", (key_id, TOUCH_EVERY_SEC))
    except Exception:                                      # noqa: BLE001 - bookkeeping must never block a request
        pass


def scope_allows(req: Request, scope: str = SCOPE) -> bool:
    """True when a key may make this request. read_assign: any GET / HEAD, or exactly one of ALLOWED_WRITES (matched on the Flask URL rule, so /assignments/12 matches <int:aid>).
    partner_push: one of PARTNER_ROUTES, or a GET / HEAD on PARTNER_READ_RULES. partner_read_all: the same writes, and a GET / HEAD on every route except SECRET_BLOCKED. Everything else is refused."""
    if scope in PARTNER_SCOPES:
        rule = req.url_rule.rule if req.url_rule is not None else None
        if rule is None:
            return False
        if (req.method, rule) in PARTNER_ROUTES:
            return True
        if req.method not in SAFE_METHODS:
            return False
        return rule in PARTNER_READ_RULES or (scope == PARTNER_READ_ALL_SCOPE and rule not in SECRET_BLOCKED)
    if req.method in SAFE_METHODS:
        return req.url_rule is None or req.url_rule.rule not in BLOCKED_READS
    rule = req.url_rule.rule if req.url_rule is not None else None
    return (req.method, rule) in ALLOWED_WRITES


def blocked_read(req: Request) -> bool:
    return req.method in SAFE_METHODS and req.url_rule is not None and req.url_rule.rule in BLOCKED_READS
