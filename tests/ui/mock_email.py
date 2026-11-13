"""Mock of GET /api/email/* for tests/ui/mock_server.py.

Made-up Klaviyo days (2026-07-13 .. 2026-11-09, the production span) for three flows and twelve campaigns, run through the REAL payload builders in
app/email_view.py over an in-memory source with the same methods as the database one. Only the database is replaced. `?fail=1` answers a 503 (error box test).
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

from flask import jsonify, request

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from app import email_view as E, queries as q            # noqa: E402

D = dt.date
FIRST, THROUGH = D(2026, 7, 13), D(2026, 11, 9)
DAYS = [FIRST + dt.timedelta(days=i) for i in range((THROUGH - FIRST).days + 1)]
FETCHED = dt.datetime(2026, 11, 9, 18, 0, tzinfo=dt.timezone.utc)
BODY = "Hi there, your plan renews on the 1st. Tap the button to change or skip. " * 12


def _row(day, source, channel, pid, mid, name, sends, delivered, uo, uc, unsub, orders, revenue):
    return dict(day=day, source=source, channel=channel, parent_id=pid, message_id=mid, parent_name=name, sends=sends, delivered=delivered, opens=uo * 2, unique_opens=uo,
                clicks=uc * 2, unique_clicks=uc, unsubscribes=unsub, orders=orders, revenue=revenue)


FLOWS = [("F1", "Billing Reminder Flow", "live", [("fm1", "Reminder 1", "Your plan renews soon"), ("fm2", "Reminder 2", "Last chance to skip a shipment")]),
         ("F2", "Welcome Series", "live", [("fm3", "Welcome 1", "Welcome to Lonvera"), ("fm4", "Welcome 2", "How to take it")]),
         ("F3", "Welcome Text Flow", "draft", [("fm5", "Text 1", None)])]
CAMPAIGN_NAMES = ["Spring Sale", "Black Friday Early Access", "Beef Liver Restock", "Boneflex Launch", "Vital Bull Offer", "Newsletter October", "Winback 90 Days",
                  "Flash Text", "Holiday Gift Guide", "Review Request", "Free Shipping Weekend", "Last Call Text"]


def _build():
    rows, flows, camps, content, fmsgs = [], {}, {}, {}, {}
    for fid, fname, status, msgs in FLOWS:
        flows[fid] = {"id": fid, "name": fname, "status": status}
        for k, (mid, mname, subj) in enumerate(msgs):
            sms = fid == "F3"
            fmsgs[mid] = {"id": mid, "flow_id": fid, "name": mname, "channel": "sms" if sms else "email", "subject": subj}
            if mid != "fm2":                                    # one flow message has no copy (deleted from the flow)
                content[mid] = {"message_id": mid, "source": "flow", "channel": "sms" if sms else "email", "parent_id": fid, "subject": subj, "preview_text": "Preview for " + mname,
                                "from_name": "Lonvera", "body_text": BODY if not sms else "Welcome text: reply STOP to opt out", "image_count": 2, "link_count": 3, "fetched_at": FETCHED}
            for i, d in enumerate(DAYS):
                s = 150 + 40 * k + (i % 7) * 12 + (30 if fid == "F1" else 0)
                dl = s - 2 - i % 3
                if sms:
                    rows.append(_row(d, "flow", "sms", fid, mid, fname, s // 3, s // 3 - 1, 0, 3 + i % 4, 0, i % 2, (i % 2) * 22.0))
                else:
                    uo = dl * (45 + 10 * k) // 100
                    o = (i + k) % 4
                    rows.append(_row(d, "flow", "email", fid, mid, fname, s, dl, uo, uo // (6 + 3 * k), 1 if i % 5 == 0 else 0, o, o * 41.5 + (i % 2) * 3))
    for n, name in enumerate(CAMPAIGN_NAMES):
        cid, mid = f"C{n + 1:02d}", f"cm{n + 1:02d}"
        sms = "Text" in name
        send = FIRST + dt.timedelta(days=5 + n * 9)
        camps[cid] = {"id": cid, "name": name + " Campaign", "channel": "sms" if sms else "email", "status": "Sent", "subject": None if sms else f"{name}: read this first", "send_date": send}
        content[mid] = {"message_id": mid, "source": "campaign", "channel": "sms" if sms else "email", "parent_id": cid, "subject": None if sms else f"{name}: read this first",
                        "preview_text": None if sms else "Open for the details", "from_name": None if sms else "Lonvera", "body_text": "Text sale today, 20 percent off" if sms else f"{name} copy. " + BODY,
                        "image_count": 1, "link_count": 2, "fetched_at": FETCHED}
        size = 4000 + 700 * n
        for k in range(6):
            d = send + dt.timedelta(days=k)
            if d > THROUGH:
                break
            first = k == 0
            uo = 0 if sms else (size * 55 // 100 if first else size // (5 * (k + 1)))
            uc = size // (60 * (k + 1))
            rows.append(_row(d, "campaign", "sms" if sms else "email", cid, mid, camps[cid]["name"], size if first else 0, size - 40 if first else 0, uo, uc, 4 if first else 0,
                             (n % 5 + 6) // (k + 1), round((n % 5 + 6) * 38.0 / (k + 1) + n, 2)))
    return rows, flows, camps, content, fmsgs


ROWS, FLOW_DIMS, CAMP_DIMS, CONTENT, FMSGS = _build()


class MemSource:
    def bounds(self):
        return FIRST, THROUGH

    def rows(self, start, end, kind, channel, parent_id=None, message_id=None):
        src = {"flows": "flow", "campaigns": "campaign"}.get(kind)
        return [r for r in ROWS if start <= r["day"] <= end and (src is None or r["source"] == src) and (channel == "all" or r["channel"] == channel)
                and (parent_id is None or r["parent_id"] == parent_id) and (message_id is None or r["message_id"] == message_id)]

    def flows(self): return FLOW_DIMS
    def campaigns(self): return CAMP_DIMS
    def flow_messages(self, flow_id=None): return {k: v for k, v in FMSGS.items() if flow_id is None or v["flow_id"] == flow_id}
    def content(self, ids): return {i: CONTENT[i] for i in ids if i in CONTENT}


def register(app, deps):
    Bad = deps["Bad"]
    src = MemSource()

    def guarded(fn):
        if request.args.get("fail") == "1":
            return jsonify(error="query timed out"), 503
        try:
            return jsonify(fn())
        except q.ApiError as e:
            if e.status == 404:
                return jsonify(error=e.message), 404
            raise Bad(e.message)

    @app.get("/api/email/summary")
    def email_summary_mock():
        return guarded(lambda: E.summary(src, E.parse_params(request.args)))

    @app.get("/api/email/timeseries")
    def email_timeseries_mock():
        return guarded(lambda: E.timeseries(src, E.parse_params(request.args), (request.args.get("grain") or "day").lower(), request.args.get("split") == "1"))

    @app.get("/api/email/flows")
    def email_flows_mock():
        return guarded(lambda: E.flows(src, E.parse_params(request.args)))

    @app.get("/api/email/flows/<flow_id>/messages")
    def email_flow_messages_mock(flow_id):
        return guarded(lambda: E.flow_messages(src, E.parse_params(request.args), flow_id))

    @app.get("/api/email/campaigns")
    def email_campaigns_mock():
        return guarded(lambda: E.campaigns(src, E.parse_params(request.args), request.args.get("q")))

    @app.get("/api/email/message/<message_id>")
    def email_message_mock(message_id):
        return guarded(lambda: E.message(src, E.parse_params(request.args), message_id))
