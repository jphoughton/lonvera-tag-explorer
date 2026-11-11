"""Manage scoped API keys (table api_keys, migration 204; what a key may do is in app/api_keys.py).

    python -m etl.api_keys create --name <name> [--out <file>]   make a key; the key is printed ONCE to stdout (or written to <file> with mode 0600) and can never be shown again
    python -m etl.api_keys list                                  name, prefix, created, last used, revoked (never the key or its hash)
    python -m etl.api_keys revoke --name <name>                  the key stops working at once (the row stays for the history)

Only the sha256 hash of a key is stored. The key is never logged, never put in etl_runs and never printed by list. DATABASE_URL picks the database (production keys are made against production).
"""
from __future__ import annotations

import argparse
import getpass
import os
import re
import sys

import psycopg

from app import api_keys as ak

from . import db

NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,39}")


class CliError(Exception):
    pass


def create_key(conn, name: str, created_by: str | None = None) -> tuple[str, str]:
    """Insert a new active key; returns (raw key, prefix). Raises CliError for a bad or taken name."""
    if not NAME_RE.fullmatch(name or ""):
        raise CliError("name must be 1 to 40 characters: letters, digits, dot, dash or underscore, starting with a letter or digit")
    raw = ak.generate_key()
    try:
        conn.execute("INSERT INTO api_keys (name, key_hash, prefix, scope, created_by) VALUES (%s, %s, %s, %s, %s)", (name, ak.hash_key(raw), raw[:8], ak.SCOPE, created_by))
    except psycopg.errors.UniqueViolation:
        conn.rollback()
        raise CliError(f"a key named {name!r} already exists (revoked keys keep their name; pick another)") from None
    except psycopg.errors.UndefinedTable:
        conn.rollback()
        raise CliError("migration 204 has not been applied to this database (python -m etl.migrate)") from None
    conn.commit()
    return raw, raw[:8]


def list_keys(conn) -> list[tuple]:
    return conn.execute("SELECT name, prefix, scope, created_at, created_by, last_used_at, revoked_at FROM api_keys ORDER BY id").fetchall()


def revoke_key(conn, name: str) -> bool:
    """True when an active key was revoked; False when it was already revoked. Raises CliError when no such key."""
    row = conn.execute("SELECT revoked_at FROM api_keys WHERE name = %s", (name,)).fetchone()
    if row is None:
        raise CliError(f"no key named {name!r}")
    if row[0] is not None:
        return False
    conn.execute("UPDATE api_keys SET revoked_at = now() WHERE name = %s AND revoked_at IS NULL", (name,))
    conn.commit()
    return True


def _write_private(path: str, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)          # never overwrites, never world-readable
    with os.fdopen(fd, "w") as f:
        f.write(text)


def _ts(v) -> str:
    return v.strftime("%Y-%m-%d %H:%M") if v else "-"


def main(argv=None, url: str | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m etl.api_keys", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create", help="make a key and print it once")
    c.add_argument("--name", required=True)
    c.add_argument("--out", help="write the key to this new file (mode 0600) instead of printing it")
    sub.add_parser("list", help="show every key (never the key itself)")
    r = sub.add_parser("revoke", help="stop a key from working")
    r.add_argument("--name", required=True)
    a = ap.parse_args(argv)
    try:
        with db.connect(url) as conn:
            if a.cmd == "create":
                if a.out and os.path.exists(a.out):
                    raise CliError(f"{a.out} already exists; it is not overwritten")
                raw, prefix = create_key(conn, a.name, getpass.getuser())
                if a.out:
                    _write_private(a.out, raw + "\n")
                    print(f"Key {a.name!r} ({prefix}...) written to {a.out} (mode 0600). It cannot be shown again.")
                else:
                    print(raw)
                    print("Shown once. It cannot be shown again. Stored only as a hash.", file=sys.stderr)
            elif a.cmd == "list":
                rows = list_keys(conn)
                print(f"{'name':<24}{'prefix':<10}{'created':<18}{'last used':<18}revoked")
                for name, prefix, _scope, created, _by, last, revoked in rows:
                    print(f"{name:<24}{prefix + '...':<10}{_ts(created):<18}{_ts(last):<18}{_ts(revoked)}")
                if not rows:
                    print("(no keys)")
            else:
                print(f"Key {a.name!r} revoked." if revoke_key(conn, a.name) else f"Key {a.name!r} was already revoked.")
    except CliError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
