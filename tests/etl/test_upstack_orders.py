"""Upstack orders per Pacific day (the actual new-customer revenue behind Total NC ROAS): chunking, shaping, the empty-answer guard. A fake client, no network;
the write path runs against the test database."""
import datetime as dt

import pytest

from etl import upstack_api as api, upstack_orders as O

D = dt.date


class Fake:
    pixel = "px"

    def __init__(self, days):
        self.days, self.calls = days, []

    def post(self, path, body, what):
        assert path == "/api/query" and body["granularity"] == "day" and body["timezone"] == "America/Los_Angeles"
        a, b = (D.fromisoformat(body["dateRange"][k]) for k in ("start", "end"))
        self.calls.append((a, b))
        prim = [{"dt": f"{d}T00:00:00Z", **dict(zip(O.MEASURES, v))} for d, v in sorted(self.days.items()) if a <= d <= b]
        return {"results": [{"data": {"primary": prim}}]}


DAYS = {D(2026, 10, 1): ("100.126", "50", "3", "150.13", "9"), D(2026, 10, 2): ("0", "0", "0", "0", "0"), D(2026, 10, 3): ("200", "10.5", "4", "210.5", "12")}


def test_pull_keeps_days_with_orders_rounds_to_cents_and_chunks_long_windows():
    c = Fake(DAYS)
    rows = O.pull(c, D(2026, 10, 1), D(2026, 10, 3))
    assert rows == [(D(2026, 10, 1), 100.13, 50.0, 3.0, 150.13, 9.0), (D(2026, 10, 3), 200.0, 10.5, 4.0, 210.5, 12.0)]       # the all-zero day is absent, not a zero row
    c = Fake({})
    O.pull(c, D(2026, 1, 1), D(2026, 4, 1))                                                                                       # 91 days: two 60-day chunks at most
    assert c.calls == [(D(2026, 1, 1), D(2026, 3, 1)), (D(2026, 3, 2), D(2026, 4, 1))]


def test_run_refuses_an_empty_answer_for_a_real_window_but_not_a_single_quiet_day():
    with pytest.raises(api.UpstackError):
        O.run(None, Fake({}), D(2026, 10, 1), D(2026, 10, 7), dry_run=True)
    assert O.run(None, Fake({}), D(2026, 10, 1), D(2026, 10, 1), dry_run=True)["days"] == 0


def test_write_replaces_the_window_and_leaves_other_days(conn):
    conn.execute("DELETE FROM upstack_orders_daily")
    conn.execute("INSERT INTO upstack_orders_daily (day, nc_rev) VALUES ('2026-09-30', 7), ('2026-10-02', 99)")
    st = O.run(conn, Fake(DAYS), D(2026, 10, 1), D(2026, 10, 3))
    conn.commit()
    got = {str(r[0]): float(r[1]) for r in conn.execute("SELECT day, nc_rev FROM upstack_orders_daily ORDER BY day").fetchall()}
    assert got == {"2026-09-30": 7.0, "2026-10-01": 100.13, "2026-10-03": 200.0}            # 10-02 had no orders in the restated pull: replaced, not kept
    assert st["days"] == 2 and st["nc_rev"] == 300.13 and st["from"] == "2026-10-01" and st["to"] == "2026-10-03"
    O.run(conn, Fake(DAYS), D(2026, 10, 1), D(2026, 10, 3))                                    # idempotent
    conn.commit()
    assert conn.execute("SELECT count(*) FROM upstack_orders_daily").fetchone()[0] == 3
