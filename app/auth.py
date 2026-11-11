"""Named-user auth: APP_USERS (username -> password hash), signed session cookie, per-user session invalidation, throttle per (IP, user) and per IP.

Passwords never live in env or code: APP_USERS holds werkzeug scrypt hashes (see scripts/auth/hash_password.py). If APP_USERS is missing, empty or
invalid the app starts but nobody can log in (fail closed) and a clear error is logged. Hashes are never returned or logged.
"""
from __future__ import annotations

import collections
import hashlib
import hmac
import json
import logging
import re
import threading
import time
from datetime import timedelta

from flask import Blueprint, current_app, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

log = logging.getLogger("tagexp")
bp = Blueprint("auth", __name__)

PROTECTED_PREFIXES = ("/api/", "/media/")
PUBLIC_API = {"/api/login", "/api/logout"}
PUBLIC_API_RE = re.compile(r"/api/priorities/vendor/[A-Za-z0-9_-]{22,64}(/[0-9]+(/detail)?)?")      # the vendor link page (the list, a row edit, and GET .../<id>/detail): the unguessable link is the access (see app/priorities_assign.py); nothing else is public
GENERIC_LOGIN_ERROR = "invalid username or password"
MAX_USERNAME_LEN = 64


class LoginThrottle:
    """In-memory sliding window of failed logins per key (per process; best effort)."""

    def __init__(self, max_fails: int, window_sec: int):
        self.max_fails = max_fails
        self.window = window_sec
        self._fails: dict[str, collections.deque] = {}
        self._lock = threading.Lock()

    def _purge(self, key: str, now: float) -> collections.deque:
        q = self._fails.setdefault(key, collections.deque())
        while q and now - q[0] > self.window:
            q.popleft()
        return q

    def retry_after(self, key: str) -> int:
        """Seconds until another attempt is allowed (0 = allowed)."""
        now = time.monotonic()
        with self._lock:
            q = self._purge(key, now)
            if len(q) >= self.max_fails:
                return max(1, int(self.window - (now - q[0])) + 1)
            return 0

    def fail(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            if len(self._fails) > 5000:                      # bound memory
                for k in [k for k, v in self._fails.items() if not v or now - v[-1] > self.window]:
                    self._fails.pop(k, None)
            self._purge(key, now).append(now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._fails.pop(key, None)


def parse_users(raw) -> dict[str, str]:
    """APP_USERS (JSON string or dict) -> {canonical username: hash}. Anything unusable -> {} (fail closed); never logs the content."""
    try:
        data = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
    except ValueError:
        return {}
    if not isinstance(data, dict) or not data:
        return {}
    users: dict[str, str] = {}
    for name, h in data.items():
        if not isinstance(name, str) or not name.strip() or not isinstance(h, str) or ":" not in h or name.strip().lower() in {u.lower() for u in users}:
            return {}                                         # one bad entry makes the whole value untrustworthy
        users[name.strip()] = h
    return users


def init_auth(app) -> None:
    app.config.update(
        SESSION_COOKIE_NAME="tagexp_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=not app.config.get("DEV_INSECURE_COOKIES"),
        PERMANENT_SESSION_LIFETIME=timedelta(days=app.config["SESSION_DAYS"]),
    )
    users = parse_users(app.config.get("APP_USERS"))
    if app.config.get("AUTH_DISABLED"):
        log.warning("AUTH_DISABLED is on: the app is open to everyone (user = guest). Unset AUTH_DISABLED to turn the APP_USERS logins back on.")
    elif not users:
        log.error("APP_USERS is missing, empty or invalid: nobody can log in. Set it to a JSON object {\"user\": \"<hash>\"} "
                  "(hashes from scripts/auth/hash_password.py).")
    app.extensions["auth_users"] = users                                             # canonical name -> hash
    app.extensions["auth_lookup"] = {u.lower(): u for u in users}                     # lowercase -> canonical name
    app.extensions["auth_dummy_hash"] = generate_password_hash("dummy password, never matches", method="scrypt")   # same cost as real hashes
    window = app.config["LOGIN_FAIL_WINDOW_SEC"]
    app.extensions["login_throttle_pair"] = LoginThrottle(app.config["LOGIN_MAX_FAILS"], window)
    app.extensions["login_throttle_ip"] = LoginThrottle(app.config.get("LOGIN_MAX_FAILS_IP", 20), window)
    app.register_blueprint(bp)
    app.before_request(_guard)


def _fingerprint(user: str, pw_hash: str) -> str:
    """Changes when SECRET_KEY or this user's hash changes, which signs that user out (other users are unaffected)."""
    key = str(current_app.config["SECRET_KEY"]).encode()
    return hmac.new(key, b"user:" + user.encode() + b"\0" + pw_hash.encode(), hashlib.sha256).hexdigest()[:24]


GUEST = "guest"


def auth_disabled() -> bool:
    return bool(current_app.config.get("AUTH_DISABLED"))


def current_user() -> str | None:
    """Canonical username of the signed-in user, or None (not signed in, user removed, or password changed). 'guest' when auth is disabled."""
    if auth_disabled():
        return GUEST
    user = session.get("u")
    pw_hash = current_app.extensions["auth_users"].get(user) if isinstance(user, str) else None
    if not pw_hash or not hmac.compare_digest(str(session.get("fp", "")), _fingerprint(user, pw_hash)):
        return None
    return user


def is_authenticated() -> bool:
    return current_user() is not None


def _guard():
    path = request.path
    if path in PUBLIC_API or PUBLIC_API_RE.fullmatch(path) or not path.startswith(PROTECTED_PREFIXES):
        return None
    if not is_authenticated():
        return jsonify(error="unauthorized"), 401
    return None


def _client_ip() -> str:
    return request.remote_addr or "unknown"          # ProxyFix (create_app) already resolved X-Forwarded-For when trusted


def _too_many(wait: int):
    resp = jsonify(error="too_many_attempts", retry_after=wait)
    resp.status_code = 429
    resp.headers["Retry-After"] = str(wait)
    return resp


@bp.post("/api/login")
def login():
    if auth_disabled():
        return "", 204                                # nothing to log in to: harmless no-op (no cookie, no throttling)
    users: dict[str, str] = current_app.extensions["auth_users"]
    if not users:
        return jsonify(error="auth_not_configured"), 503
    data = request.get_json(silent=True)
    username = data.get("username") if isinstance(data, dict) else None
    password = data.get("password") if isinstance(data, dict) else None
    username = username.strip() if isinstance(username, str) else ""
    ip = _client_ip()
    pair = f"{ip}|{username.lower()[:MAX_USERNAME_LEN]}"
    t_pair, t_ip = current_app.extensions["login_throttle_pair"], current_app.extensions["login_throttle_ip"]
    wait = max(t_pair.retry_after(pair), t_ip.retry_after(ip))
    if wait:
        return _too_many(wait)                       # refused before any password is looked at
    canonical = current_app.extensions["auth_lookup"].get(username.lower())
    # ALWAYS verify a hash (a dummy one for unknown users) so response time does not reveal which usernames exist
    stored = users[canonical] if canonical else current_app.extensions["auth_dummy_hash"]
    try:
        ok = check_password_hash(stored, password if isinstance(password, str) else "")
    except ValueError:                                # a malformed stored hash must not become a 500 or a bypass
        ok = False
    if not (ok and canonical and isinstance(password, str)):
        t_pair.fail(pair)
        t_ip.fail(ip)
        return jsonify(error=GENERIC_LOGIN_ERROR), 401
    t_pair.reset(pair)
    session.clear()
    session["u"] = canonical                          # canonical spelling from APP_USERS, whatever the user typed
    session["fp"] = _fingerprint(canonical, stored)
    session.permanent = True
    return "", 204


@bp.post("/api/logout")
def logout():
    session.clear()
    return "", 204
