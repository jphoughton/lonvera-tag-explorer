"""The one clock for projection: today's date in Pacific time (the business's day), with a pin for tests and demos.

    from app.projection.clock import today_pt
    today_pt()                       # date(2026, 11, 3)

Env PROJECTION_TODAY=YYYY-MM-DD pins the date (an unparseable value is ignored). Never use date.today(): on the server (UTC) it is one day ahead of
Pacific from 16:00 to 24:00, which put pages a day apart and closed a month eight hours early.
"""
from __future__ import annotations

import os
from datetime import date, datetime
from zoneinfo import ZoneInfo

PACIFIC = ZoneInfo("America/Los_Angeles")
PIN_ENV = "PROJECTION_TODAY"


def today_pt() -> date:
    pinned = os.environ.get(PIN_ENV)
    if pinned:
        try:
            return date.fromisoformat(pinned.strip()[:10])
        except ValueError:
            pass
    return datetime.now(PACIFIC).date()
