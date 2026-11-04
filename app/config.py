"""App configuration. Everything comes from environment variables (Railway); tests pass overrides to create_app()."""
from __future__ import annotations

import os


def _flag(v: str | None) -> bool:
    return str(v or "").strip().lower() in ("1", "true", "yes", "on")


def load_config(overrides: dict | None = None) -> dict:
    env = os.environ
    insecure = _flag(env.get("DEV_INSECURE_COOKIES"))
    cfg = {
        "DATABASE_URL": env.get("DATABASE_URL", ""),
        "APP_PASSWORD": env.get("APP_PASSWORD", ""),
        # A missing SECRET_KEY is only tolerated in local dev (DEV_INSECURE_COOKIES=1): sessions would otherwise
        # differ per gunicorn worker.
        "SECRET_KEY": env.get("SECRET_KEY", "") or ("dev-only-secret-key" if insecure else ""),
        "DEV_INSECURE_COOKIES": insecure,
        "SESSION_DAYS": int(env.get("SESSION_DAYS", "30")),
        "LOGIN_MAX_FAILS": int(env.get("LOGIN_MAX_FAILS", "5")),
        "LOGIN_FAIL_WINDOW_SEC": int(env.get("LOGIN_FAIL_WINDOW_SEC", "600")),
        "DB_POOL_MIN": int(env.get("DB_POOL_MIN", "1")),
        "DB_POOL_MAX": int(env.get("DB_POOL_MAX", "8")),
        "STATEMENT_TIMEOUT_MS": int(env.get("STATEMENT_TIMEOUT_MS", "20000")),
        # Railway terminates TLS at a proxy; trust exactly one hop for the client IP / scheme.
        "TRUST_PROXY": _flag(env.get("TRUST_PROXY")) or bool(env.get("RAILWAY_ENVIRONMENT")),
    }
    if overrides:
        cfg.update(overrides)
    return cfg
