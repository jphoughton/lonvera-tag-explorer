"""Accept one SELECT / WITH statement and nothing else.

The database role (analytics_reader) already cannot write; this check is the first of three layers (this check, a READ ONLY transaction, the role itself).
It strips comments and quoted text first, so a keyword inside a string or a quoted identifier is not mistaken for code and a keyword hidden after a comment is not missed.
"""
from __future__ import annotations

import re

FORBIDDEN = frozenset({
    # anything that writes or changes state, in a single statement (a writable CTE, SELECT INTO, FOR UPDATE, COPY ... TO PROGRAM)
    "insert", "update", "delete", "merge", "truncate", "drop", "alter", "create", "grant", "revoke", "copy", "call", "do", "execute", "vacuum", "refresh", "lock", "into",
    # functions that sleep, read the server's files, reach other servers or change settings
    "pg_sleep", "pg_sleep_for", "pg_sleep_until", "dblink", "dblink_exec", "lo_import", "lo_export", "pg_read_file", "pg_read_binary_file", "pg_ls_dir", "set_config",
    "pg_terminate_backend", "pg_cancel_backend", "pg_advisory_lock", "pg_notify",
})


class SqlRejected(ValueError):
    pass


def _strip(sql: str) -> str:
    """The SQL with comments removed and every string / quoted identifier replaced by a placeholder. Raises SqlRejected on an unterminated quote or comment."""
    out: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        c = sql[i]
        two = sql[i:i + 2]
        if two == "--":
            j = sql.find("\n", i)
            i = n if j < 0 else j
            out.append(" ")
        elif two == "/*":
            depth, i = 1, i + 2                                  # Postgres block comments nest
            while i < n and depth:
                if sql[i:i + 2] == "/*":
                    depth, i = depth + 1, i + 2
                elif sql[i:i + 2] == "*/":
                    depth, i = depth - 1, i + 2
                else:
                    i += 1
            if depth:
                raise SqlRejected("unterminated comment")
            out.append(" ")
        elif c in "'\"":
            j = i + 1
            while True:
                j = sql.find(c, j)
                if j < 0:
                    raise SqlRejected("unterminated quote")
                if sql[j + 1:j + 2] == c:                       # doubled quote = literal quote
                    j += 2
                    continue
                break
            out.append(" q ")
            i = j + 1
        elif c == "$":
            m = re.match(r"\$([A-Za-z_]\w*)?\$", sql[i:])      # dollar-quoted string
            if m:
                tag = m.group(0)
                j = sql.find(tag, i + len(tag))
                if j < 0:
                    raise SqlRejected("unterminated dollar quote")
                out.append(" q ")
                i = j + len(tag)
            else:
                out.append(c)
                i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def check_select(sql: str) -> str:
    """Return the query without a trailing semicolon, or raise SqlRejected with a plain reason."""
    if not isinstance(sql, str) or not sql.strip():
        raise SqlRejected("empty query")
    if len(sql) > 20000:
        raise SqlRejected("query is longer than 20,000 characters")
    code = _strip(sql).strip()
    while code.endswith(";"):
        code = code[:-1].rstrip()
    if ";" in code:
        raise SqlRejected("only one statement is allowed")
    words = re.findall(r"[A-Za-z_][A-Za-z_0-9]*", code.lower())
    if not words or words[0] not in ("select", "with"):
        raise SqlRejected("only SELECT or WITH queries are allowed")
    bad = sorted({w for w in words if w in FORBIDDEN})
    if bad:
        raise SqlRejected("not allowed in a read-only query: " + ", ".join(bad))
    s = sql.strip()
    while s.endswith(";"):
        s = s[:-1].rstrip()
    return s
