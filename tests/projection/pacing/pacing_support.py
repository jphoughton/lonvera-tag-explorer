"""Shared constants for the pacing tests (a uniquely named module: the test directories are not packages)."""
import datetime as dt
import os
from pathlib import Path

SNAP = Path(os.environ.get("PROJECTION_SNAPSHOT_DIR", "~/lonvera-tag-explorer-data/projection-snapshot-2026-11-03")).expanduser()
SNAPSHOT_DAY = dt.date(2026, 11, 3)          # the sheet's TODAY() when the snapshot was read
THR = dt.date(2026, 11, 2)                   # last complete day


PASSWORD = "throwaway-test-password"


def make_app(url, **overrides):
    """A Flask app on `url` with one named user (logins on) unless AUTH_DISABLED is passed."""
    from werkzeug.security import generate_password_hash
    from app import create_app
    cfg = {"DATABASE_URL": url, "APP_USERS": {"JoshHoughton": generate_password_hash(PASSWORD, method="scrypt:1024:8:1")}, "SECRET_KEY": "test-secret-key",
           "DEV_INSECURE_COOKIES": True, "DB_POOL_MAX": 4, "PROJ_RATE_MUTATIONS_PER_MIN": 100000, "PROJ_RATE_UPLOADS_PER_HOUR": 100000}
    cfg.update(overrides)
    return create_app(cfg)
