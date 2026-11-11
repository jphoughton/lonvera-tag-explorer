"""python -m etl.api_keys: create prints the key once and stores only its hash, --out writes a 0600 file and prints no key, list never shows a key or hash, revoke stops the key."""
import hashlib
import os
import stat

import psycopg
import pytest

from app import api_keys as ak
from etl import api_keys as cli


@pytest.fixture(autouse=True)
def clean(dburl):
    with psycopg.connect(dburl, autocommit=True) as c:
        c.execute("DELETE FROM api_keys")
    yield
    with psycopg.connect(dburl, autocommit=True) as c:
        c.execute("DELETE FROM api_keys")


def rows(dburl):
    with psycopg.connect(dburl) as c:
        return c.execute("SELECT name, key_hash, prefix, scope, created_by, last_used_at, revoked_at FROM api_keys ORDER BY id").fetchall()


def test_create_prints_the_key_once_and_stores_only_the_hash(dburl, capsys):
    assert cli.main(["create", "--name", "Alice-1"], url=dburl) == 0
    out, err = capsys.readouterr()
    raw = out.strip()
    assert ak.well_formed(raw) and out.count(raw) == 1 and raw not in err
    (name, h, prefix, scope, by, last, revoked), = rows(dburl)
    assert (name, scope, last, revoked) == ("Alice-1", "read_assign", None, None) and prefix == raw[:8] and by
    assert h == hashlib.sha256(raw.encode()).hexdigest() and raw not in (name, h, prefix, scope, by)


def test_create_out_writes_a_private_file_and_prints_no_key(dburl, capsys, tmp_path):
    f = tmp_path / "key.txt"
    assert cli.main(["create", "--name", "bob", "--out", str(f)], url=dburl) == 0
    out, err = capsys.readouterr()
    raw = f.read_text().strip()
    assert ak.well_formed(raw) and raw not in out and raw not in err
    assert stat.S_IMODE(os.stat(f).st_mode) == 0o600
    assert rows(dburl)[0][1] == hashlib.sha256(raw.encode()).hexdigest()
    assert cli.main(["create", "--name", "bob2", "--out", str(f)], url=dburl) == 1                    # an existing file is never overwritten
    assert f.read_text().strip() == raw and len(rows(dburl)) == 1


def test_names_must_be_unique_and_tidy(dburl, capsys):
    assert cli.main(["create", "--name", "dup"], url=dburl) == 0
    capsys.readouterr()
    assert cli.main(["create", "--name", "dup"], url=dburl) == 1
    assert "already exists" in capsys.readouterr().err
    for bad in ("", " x", "a b", "x" * 41, "key:evil", "a/b"):
        assert cli.main(["create", "--name", bad], url=dburl) == 1, bad
    assert len(rows(dburl)) == 1


def test_list_never_shows_a_key_or_a_hash(dburl, capsys):
    assert cli.main(["list"], url=dburl) == 0 and "(no keys)" in capsys.readouterr().out
    cli.main(["create", "--name", "one"], url=dburl)
    raw = capsys.readouterr().out.strip()
    cli.main(["create", "--name", "two"], url=dburl)
    capsys.readouterr()
    cli.main(["revoke", "--name", "two"], url=dburl)
    capsys.readouterr()
    assert cli.main(["list"], url=dburl) == 0
    out = capsys.readouterr().out
    h = rows(dburl)[0][1]
    assert "one" in out and "two" in out and raw[:8] + "..." in out and raw not in out and raw[4:] not in out and h not in out
    lines = {ln.split()[0]: ln for ln in out.splitlines()[1:]}
    assert lines["one"].rstrip().endswith("-") and not lines["two"].rstrip().endswith(" -")        # revoked column: '-' while active, a timestamp once revoked


def test_revoke_stops_the_key_and_is_idempotent(dburl, capsys):
    cli.main(["create", "--name", "gone"], url=dburl)
    raw = capsys.readouterr().out.strip()
    with psycopg.connect(dburl) as c:
        assert ak.lookup(c, raw)["name"] == "gone"
    assert cli.main(["revoke", "--name", "gone"], url=dburl) == 0 and "revoked" in capsys.readouterr().out
    with psycopg.connect(dburl) as c:
        assert ak.lookup(c, raw) is None
    assert cli.main(["revoke", "--name", "gone"], url=dburl) == 0 and "already revoked" in capsys.readouterr().out
    assert cli.main(["revoke", "--name", "nobody"], url=dburl) == 1 and "no key named" in capsys.readouterr().err
    assert cli.main(["create", "--name", "gone"], url=dburl) == 1                                    # the name stays taken so history is unambiguous
