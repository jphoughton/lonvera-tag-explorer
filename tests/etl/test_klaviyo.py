"""etl.klaviyo with the Klaviyo HTTP layer faked: no network. Covers aggregation, the window replace, idempotence, 429 retry and the missing-key exit."""
import datetime as dt
import json
import subprocess
from pathlib import Path

import pytest

from etl import klaviyo as K

ROOT = Path(__file__).resolve().parents[2]
TODAY = dt.date(2026, 11, 10)


def cell(dims, **m):
    return {"dimensions": dims, "measurements": {k: v for k, v in m.items()}}


HTML = ('<html><head><title>x</title><style>p{color:red}</style></head><body><p>Hello   <b>there</b> {% if x %}</p><div>{% unsubscribe %}</div><p><div>Shop <a href="https://lonvera.com/p?a=1">the sale</a></div>'
        '<img src="https://img/x.png" width="600"><img src="https://t/open.gif" width="1"><script>var a=1</script></body></html>')


class Fake:
    """Answers the endpoints etl.klaviyo uses. Dates: two Pacific days 11-08 and 11-09."""
    D = ["2026-11-08T00:00:00-08:00", "2026-11-09T00:00:00-08:00"]

    def __init__(self):
        self.calls = []

    def paged(self, path, params=None, with_included=False):
        self.calls.append(path)
        if path == "/campaigns/":
            ch = params["filter"].split("'")[1]
            data = [{"id": f"c_{ch}", "attributes": {"name": f"Camp {ch}", "status": "Sent", "send_time": "2026-11-08T17:00:00+00:00", "created_at": "2026-11-01T00:00:00+00:00", "updated_at": None},
                     "relationships": {"campaign-messages": {"data": [{"id": f"m_{ch}"}]}}}]
            inc = [{"type": "campaign-message", "id": f"m_{ch}", "attributes": {"definition": {"label": "A", "channel": ch, "content": ({"subject": f"Hi {ch}", "preview_text": "pv", "from_email": "a@b.c", "from_label": "Lonvera"} if ch == "email" else {"body": "Text me https://x.co/a now", "media_url": None})}}}]
            return (data, inc) if with_included else data
        if path == "/flows/":
            return [{"id": "f1", "attributes": {"name": "Welcome", "status": "live", "trigger_type": "List", "created": "2026-01-01T00:00:00+00:00", "updated": "2026-02-01T00:00:00+00:00"}}]
        if path == "/flows/f1/flow-actions/":
            return [{"id": "a1"}]
        if path == "/flow-actions/a1/flow-messages/":
            return [{"id": "fm1", "attributes": {"channel": "Email", "definition": {"name": "Welcome #1", "subject_line": "Welcome!"}}}]
        raise AssertionError(path)

    def req(self, method, path, **kw):
        if method == "GET":
            self.calls.append(path)
            if path in ("/campaign-messages/m_email/template/", "/flow-messages/fm1/template/"):
                return {"data": {"id": "T" + path.split("/")[2], "attributes": {"editor_type": "CODE", "html": HTML}}}
            raise RuntimeError("404 " + path)
        assert method == "POST" and path == "/metric-aggregates/"
        a = kw["json"]["data"]["attributes"]
        mid, by = a["metric_id"], a["by"]
        rows = {
            K.M["recvE"]: [cell(["f1", "fm1", ""], count=[10, 20], unique=[10, 20]), cell(["", "c_email", "m_email"], count=[100, 0], unique=[100, 0])],
            K.M["bounceE"]: [cell(["", "c_email", "m_email"], count=[5, 0], unique=[5, 0])],
            K.M["openE"]: [cell(["f1", "fm1", ""], count=[6, 9], unique=[4, 5]), cell(["", "c_email", ""], count=[3, 0], unique=[3, 0])],
            K.M["clickE"]: [cell(["f1", "fm1", "", "0.0"], count=[2, 1], unique=[2, 1]), cell(["f1", "fm1", "", "1.0"], count=[50, 50], unique=[50, 50])],
            K.M["spamE"]: [cell(["", "c_email", "m_email"], count=[1, 0], unique=[1, 0])],
            K.M["recvS"]: [cell(["", "c_sms", "m_sms"], count=[7, 0], unique=[7, 0])],
            K.M["unsubS"]: [cell(["Camp sms"], count=[2, 0], unique=[2, 0]), cell(["Nobody"], count=[9, 0], unique=[9, 0])],
            K.CONV_METRIC: [cell(["f1", "fm1", "", "email"], count=[1, 2], sum_value=[50.0, 120.5]), cell(["", "c_sms", "m_sms", "sms"], count=[1, 0], sum_value=[30.0, 0]),
                            cell(["", "", "", "email"], count=[4, 4], sum_value=[1.0, 1.0])],
        }.get(mid, [])
        data = []
        for g in rows:
            if len(g["dimensions"]) != len(by):
                continue
            data.append({"dimensions": g["dimensions"], "measurements": {m: g["measurements"].get(m, [0, 0]) for m in a["measurements"]}})
        return {"data": {"attributes": {"dates": self.D, "data": data}}, "links": {}}


def ce_opens(by):
    return by[("2026-11-08", "campaign", "c_email", "m_email", "email")]["opens"]   # event without $variation lands on the campaign's only message


def test_pull_aggregates_and_maps():
    f = Fake()
    start, end = K.window(2, TODAY)
    assert (start, end) == ("2026-11-08", "2026-11-10")
    camps, flows = K.pull_lists(f)
    rows, notes = K.pull_daily(f, camps, flows, start, end)
    by = {(r["day"], r["source"], r["parent_id"], r["message_id"], r["channel"]): r for r in rows}
    fl8 = by[("2026-11-08", "flow", "f1", "fm1", "email")]
    assert (fl8["delivered"], fl8["sends"], fl8["opens"], fl8["unique_opens"], fl8["clicks"], fl8["orders"], fl8["revenue"]) == (10, 10, 6, 4, 2, 1, 50.0)   # bot click row dropped
    assert fl8["parent_name"] == "Welcome"
    assert ce_opens(by) == 3
    assert by[("2026-11-09", "flow", "f1", "fm1", "email")]["revenue"] == 120.5
    ce = by[("2026-11-08", "campaign", "c_email", "m_email", "email")]
    assert (ce["delivered"], ce["bounced"], ce["sends"], ce["spam_complaints"], ce["parent_name"]) == (100, 5, 105, 1, "Camp email")
    cs = by[("2026-11-08", "campaign", "c_sms", "m_sms", "sms")]
    assert (cs["delivered"], cs["orders"], cs["revenue"]) == (7, 1, 30.0)
    assert by[("2026-11-08", "campaign", "c_sms", "", "sms")]["unsubscribes"] == 2   # matched by campaign name
    assert notes["sms_unsubs_unattributed"] == 9
    assert not any(r["parent_id"] == "" for r in rows)   # conversions without a flow or message are dropped


def test_run_writes_and_is_idempotent(conn, tmp_path):
    f = Fake()
    conn.execute("TRUNCATE klaviyo_daily, klaviyo_campaigns, klaviyo_flows, klaviyo_flow_messages, klaviyo_message_content")
    conn.execute("INSERT INTO klaviyo_daily (day, source, parent_id, channel, sends) VALUES ('2026-11-08','flow','stale','email',1), ('2026-11-01','flow','old','email',3)")
    conn.commit()
    s1 = K.run(f, 2, False, str(tmp_path), conn, TODAY)
    conn.commit()
    assert s1["daily_rows"] == 5 and s1["flow_messages"] == 1 and s1["campaigns"] == 2 and s1["flows"] == 1
    n = conn.execute("SELECT count(*), sum(revenue) FROM klaviyo_daily").fetchone()
    assert n == (6, 200.5)   # 5 new + the row outside the window, the stale in-window row is gone
    assert conn.execute("SELECT count(*) FROM klaviyo_daily WHERE parent_id='stale'").fetchone()[0] == 0
    K.run(f, 2, False, None, conn, TODAY)
    conn.commit()
    assert conn.execute("SELECT count(*), sum(revenue) FROM klaviyo_daily").fetchone() == n
    assert conn.execute("SELECT name, subject FROM klaviyo_flow_messages WHERE id='fm1'").fetchone() == ("Welcome #1", "Welcome!")
    assert conn.execute("SELECT subject FROM klaviyo_campaigns WHERE id='c_email'").fetchone()[0] == "Hi email"
    cc = {r[0]: r for r in conn.execute("SELECT message_id, source, channel, subject, preview_text, body_text, image_count, link_urls, editor_type, content_hash, fetched_at FROM klaviyo_message_content")}
    assert set(cc) == {"m_email", "m_sms", "fm1"} and s1["content_messages"] == 3 and s1["content_with_copy"] == 3
    assert cc["m_email"][3:5] == ("Hi email", "pv") and cc["m_email"][5] == "Hello there\nShop the sale" and cc["m_email"][6] == 1 and cc["m_email"][7] == ["https://lonvera.com/p?a=1"]
    assert cc["m_sms"][5] == "Text me https://x.co/a now" and cc["m_sms"][3] is None and cc["m_sms"][7] == ["https://x.co/a"]
    assert cc["fm1"][1] == "flow" and cc["fm1"][8] == "CODE"
    first = cc["m_email"][10]
    p = conn.execute("SELECT message_id, subject, body_text, has_copy, sends, delivered, orders, revenue, unique_opens_daily_sum, first_send_day FROM klaviyo_message_performance ORDER BY message_id").fetchall()
    assert [r[0] for r in p] == ["fm1", "m_email", "m_sms"] and p[0][3] and p[0][2] == "Hello there\nShop the sale" and p[0][6:8] == (3, 170.5) and p[0][5] == 30 and p[0][8] == 9
    assert str(p[0][9]) == "2026-11-08"
    # a changed template moves fetched_at; an unchanged one does not
    K.run(f, 2, False, None, conn, TODAY)
    conn.commit()
    assert conn.execute("SELECT fetched_at FROM klaviyo_message_content WHERE message_id='m_email'").fetchone()[0] == first
    globals()["HTML"] = HTML.replace("Hello", "Howdy")
    K.run(f, 2, False, None, conn, TODAY)
    conn.commit()
    assert conn.execute("SELECT body_text, fetched_at FROM klaviyo_message_content WHERE message_id='m_email'").fetchone()[0].startswith("Howdy")
    assert conn.execute("SELECT fetched_at > %s FROM klaviyo_message_content WHERE message_id='m_email'", (first,)).fetchone()[0]
    assert len((tmp_path / "klaviyo_daily.jsonl").read_text().splitlines()) == 5


def test_dry_run_writes_nothing(conn):
    conn.execute("TRUNCATE klaviyo_daily, klaviyo_campaigns, klaviyo_flows, klaviyo_flow_messages, klaviyo_message_content")
    conn.commit()
    K.run(Fake(), 2, True, None, None, TODAY)
    assert conn.execute("SELECT count(*) FROM klaviyo_daily").fetchone()[0] == 0


def test_client_retries_429(monkeypatch):
    import requests
    seq = [429, 500, 200]
    sleeps = []

    class R:
        def __init__(self, code):
            self.status_code, self.headers, self.text = code, {"Retry-After": "3"}, ""

        def json(self):
            return {"ok": True}

    monkeypatch.setattr(requests, "request", lambda *a, **k: R(seq.pop(0)))
    assert K.Client("k", sleep=sleeps.append).req("GET", "/flows/") == {"ok": True}
    assert sleeps[0] == 3.5 and len(sleeps) == 2


def test_client_error_does_not_leak_key(monkeypatch):
    import requests

    class R:
        status_code, headers, text = 400, {}, "bad"

    monkeypatch.setattr(requests, "request", lambda *a, **k: R())
    with pytest.raises(RuntimeError) as e:
        K.Client("SECRETKEY123456", sleep=lambda s: None).req("GET", "/flows/")
    assert "SECRETKEY" not in str(e.value)


def test_missing_key_exits_zero(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("KLAVIYO_API_KEY", raising=False)
    monkeypatch.delenv("KLAVIYO_ENV_FILE", raising=False)
    monkeypatch.setattr(K, "DEFAULT_ENV_FILE", tmp_path / "nope.env")
    assert K.main(["--days", "14"]) == 0
    assert "KLAVIYO_API_KEY is not set" in capsys.readouterr().out


def test_key_from_env_file(monkeypatch, tmp_path):
    f = tmp_path / ".env"
    f.write_text("OTHER=1\nKLAVIYO_API_KEY=abc123\n")
    monkeypatch.delenv("KLAVIYO_API_KEY", raising=False)
    monkeypatch.setenv("KLAVIYO_ENV_FILE", str(f))
    assert K.load_key() == "abc123"


def test_run_etl_sh_has_nonfatal_klaviyo_step():
    s = (ROOT / "scripts" / "deploy" / "run_etl.sh").read_text()
    line = next(x for x in s.splitlines() if "python -m etl.klaviyo --days 14" in x)
    assert "|| echo" in line and "non-fatal" in line
    assert s.index("python -m etl.assignment_tags ||") < s.index("python -m etl.klaviyo --days 14 ||") < s.index("python -m etl.auto_tag ||")
