#!/usr/bin/env python3
# Make a login hash for APP_USERS. The password is read from stdin (hidden prompt on a terminal, or piped) and is never echoed or stored.
#   .venv/bin/python scripts/auth/hash_password.py          # prompts; prints one scrypt hash line
#   printf '%s' "$PW" | .venv/bin/python scripts/auth/hash_password.py
# Put the printed hash into APP_USERS, e.g. {"JoshHoughton":"<hash>","Lazer":"<hash>"} (Railway variable), never the password itself.
# Changing a user's hash signs that user out everywhere; removing the user from APP_USERS removes the login.
import getpass
import sys

from werkzeug.security import generate_password_hash


def main() -> int:
    if sys.stdin.isatty():
        pw = getpass.getpass("Password (hidden): ")
        if pw != getpass.getpass("Again: "):
            print("passwords differ", file=sys.stderr)
            return 1
    else:
        pw = sys.stdin.read().rstrip("\r\n")
    if not pw:
        print("empty password", file=sys.stderr)
        return 1
    print(generate_password_hash(pw, method="scrypt"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
