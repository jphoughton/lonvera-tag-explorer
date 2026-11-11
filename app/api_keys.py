"""Scoped API keys (table api_keys, migration 205).

A key looks like lvk_ plus 40 characters from secrets.token_urlsafe (240 bits). Only its sha256 hash is stored. The scope 'read_assign' lets a key read everything the app serves (GET and HEAD) and
make exactly two writes: POST /api/priorities/assignments and PATCH /api/priorities/assignments/<aid>. Nothing else is writable with a key (see ALLOWED_WRITES). app/auth.py enforces this in its
request guard; keys never satisfy the launch gate, never open the login route and are refused on the route that returns vendor link tokens (BLOCKED_READS).
"""
from __future__ import annotations

import hashlib
import re
import secrets

from flask import Request

KEY_PREFIX = "lvk_"
KEY_BODY_CHARS = 40
KEY_RE = re.compile(r"lvk_[A-Za-z0-9_-]{40}")
SCOPE = "read_assign"
SAFE_METHODS = ("GET", "HEAD")
ALLOWED_WRITES = frozenset({("POST", "/api/priorities/assignments"), ("PATCH", "/api/priorities/assignments/<int:aid>")})      # (method, Flask rule) pairs, nothing else
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
    row = conn.execute("SELECT id, name, scope, last_used_at, (last_used_at IS NULL OR last_used_at < now() - make_interval(secs => %s)) AS stale FROM api_keys "
                       "WHERE key_hash = %s AND revoked_at IS NULL", (TOUCH_EVERY_SEC, hash_key(raw))).fetchone()
    if row is None:
        return None
    row = row if isinstance(row, dict) else dict(zip(("id", "name", "scope", "last_used_at", "stale"), row))
    if row["stale"]:
        _touch(row["id"])
    return {"id": row["id"], "name": row["name"], "scope": row["scope"]}


def _touch(key_id: int) -> None:
    try:
        from .projection import rwdb                       # the app's own pool is read-only
        with rwdb.get_rw() as w:
            w.execute("UPDATE api_keys SET last_used_at = now() WHERE id = %s AND (last_used_at IS NULL OR last_used_at < now() - make_interval(secs => %s))", (key_id, TOUCH_EVERY_SEC))
    except Exception:                                      # noqa: BLE001 - bookkeeping must never block a request
        pass


def scope_allows(req: Request) -> bool:
    """True when a key may make this request: any GET / HEAD, or exactly one of ALLOWED_WRITES (matched on the Flask URL rule, so /assignments/12 matches <int:aid>)."""
    if req.method in SAFE_METHODS:
        return req.url_rule is None or req.url_rule.rule not in BLOCKED_READS
    rule = req.url_rule.rule if req.url_rule is not None else None
    return (req.method, rule) in ALLOWED_WRITES


def blocked_read(req: Request) -> bool:
    return req.method in SAFE_METHODS and req.url_rule is not None and req.url_rule.rule in BLOCKED_READS
