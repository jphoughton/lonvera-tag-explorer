"""Shared-password auth: signed session cookie, constant-time compare, per-IP failure throttle."""
from __future__ import annotations

import collections
import hashlib
import hmac
import threading
import time
from datetime import timedelta

from flask import Blueprint, current_app, jsonify, request, session

bp = Blueprint("auth", __name__)

PROTECTED_PREFIXES = ("/api/", "/media/")
PUBLIC_API = {"/api/login", "/api/logout"}


class LoginThrottle:
    """In-memory sliding window of failed logins per client IP (per process; best effort)."""

    def __init__(self, max_fails: int, window_sec: int):
        self.max_fails = max_fails
        self.window = window_sec
        self._fails: dict[str, collections.deque] = {}
        self._lock = threading.Lock()

    def _purge(self, ip: str, now: float) -> collections.deque:
        q = self._fails.setdefault(ip, collections.deque())
        while q and now - q[0] > self.window:
            q.popleft()
        return q

    def retry_after(self, ip: str) -> int:
        """Seconds until another attempt is allowed (0 = allowed)."""
        now = time.monotonic()
        with self._lock:
            q = self._purge(ip, now)
            if len(q) >= self.max_fails:
                return max(1, int(self.window - (now - q[0])) + 1)
            return 0

    def fail(self, ip: str) -> None:
        now = time.monotonic()
        with self._lock:
            if len(self._fails) > 5000:                      # bound memory
                for k in [k for k, v in self._fails.items() if not v or now - v[-1] > self.window]:
                    self._fails.pop(k, None)
            self._purge(ip, now).append(now)

    def reset(self, ip: str) -> None:
        with self._lock:
            self._fails.pop(ip, None)


def init_auth(app) -> None:
    app.config.update(
        SESSION_COOKIE_NAME="tagexp_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=not app.config.get("DEV_INSECURE_COOKIES"),
        PERMANENT_SESSION_LIFETIME=timedelta(days=app.config["SESSION_DAYS"]),
    )
    app.extensions["login_throttle"] = LoginThrottle(app.config["LOGIN_MAX_FAILS"], app.config["LOGIN_FAIL_WINDOW_SEC"])
    app.register_blueprint(bp)
    app.before_request(_guard)


def _fingerprint() -> str:
    """Changes when APP_PASSWORD or SECRET_KEY changes, which signs everybody out."""
    key = str(current_app.config["SECRET_KEY"]).encode()
    return hmac.new(key, b"pw:" + str(current_app.config["APP_PASSWORD"]).encode(), hashlib.sha256).hexdigest()[:24]


def is_authenticated() -> bool:
    if not current_app.config.get("APP_PASSWORD"):
        return False
    return bool(session.get("ok")) and hmac.compare_digest(str(session.get("fp", "")), _fingerprint())


def _guard():
    path = request.path
    if path in PUBLIC_API or not path.startswith(PROTECTED_PREFIXES):
        return None
    if not is_authenticated():
        return jsonify(error="unauthorized"), 401
    return None


def _client_ip() -> str:
    return request.remote_addr or "unknown"          # ProxyFix (create_app) already resolved X-Forwarded-For when trusted


def _digest(s: str) -> bytes:
    return hashlib.sha256(s.encode("utf-8")).digest()


@bp.post("/api/login")
def login():
    throttle: LoginThrottle = current_app.extensions["login_throttle"]
    ip = _client_ip()
    wait = throttle.retry_after(ip)
    if wait:
        resp = jsonify(error="too_many_attempts", retry_after=wait)
        resp.status_code = 429
        resp.headers["Retry-After"] = str(wait)
        return resp
    expected = current_app.config.get("APP_PASSWORD") or ""
    if not expected:
        return jsonify(error="auth_not_configured"), 503
    data = request.get_json(silent=True)
    pw = data.get("password") if isinstance(data, dict) else None
    ok = isinstance(pw, str) and hmac.compare_digest(_digest(pw), _digest(expected))   # digests: equal length, constant time
    if not ok:
        throttle.fail(ip)
        return jsonify(error="unauthorized"), 401
    throttle.reset(ip)
    session.clear()
    session["ok"] = True
    session["fp"] = _fingerprint()
    session.permanent = True
    return "", 204


@bp.post("/api/logout")
def logout():
    session.clear()
    return "", 204
