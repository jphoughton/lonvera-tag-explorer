"""Idempotent migration runner: applies migrations/*.sql in filename order, tracked in schema_migrations.
Usage: python -m etl.migrate"""
from __future__ import annotations

import os
from pathlib import Path

from . import db


def migrations_dir() -> Path:
    return Path(os.environ.get("MIGRATIONS_DIR") or Path(__file__).resolve().parent.parent / "migrations")


def migrate(conn=None, directory: Path | None = None) -> list[str]:
    own = conn is None
    conn = conn or db.connect()
    applied: list[str] = []
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
        conn.commit()
        done = {r[0] for r in conn.execute("SELECT version FROM schema_migrations").fetchall()}
        for f in sorted((directory or migrations_dir()).glob("*.sql")):
            if f.name in done:
                continue
            conn.execute(f.read_text())
            conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (f.name,))
            conn.commit()
            applied.append(f.name)
            db.log(f"migrate: applied {f.name}")
        if not applied:
            db.log("migrate: up to date")
    finally:
        if own:
            conn.close()
    return applied


def main():
    migrate()


if __name__ == "__main__":
    main()
