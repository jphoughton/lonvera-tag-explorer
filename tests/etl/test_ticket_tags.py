"""etl.ticket_tags: tickets added with a name, a vendor and a brief get product, market cluster / ICP / pain point (from one taxonomy line) and new-vs-iteration from the brief.
Fills only what is empty (a given tag is never overwritten), records tag_note and a history row, retries then gives up after three runs, stops early on auth / usage errors, and is a no-op without migration 260."""
import json
import subprocess

import pytest

from etl import ticket_tags as tt
from etl import auto_tag as at

MONDAY = "2026-11-09"


@pytest.fixture(autouse=True)
def clean(conn):
    conn.execute("TRUNCATE creative_priorities_assignments RESTART IDENTITY CASCADE")
    conn.commit()


def line(conn, slug="boneflex", code="BF99", cluster="Aging in Place", icp="Trip Saver", pain="Joint Pain & Mobility Loss"):
    pid = conn.execute("SELECT id FROM products WHERE slug=%s", (slug,)).fetchone()[0]
    lid = conn.execute("INSERT INTO taxonomy_lines (product_id, line_code, cluster, icp, pain_point, emotional_trigger) VALUES (%s,%s,%s,%s,%s,'Fear') RETURNING id", (pid, code, cluster, icp, pain)).fetchone()[0]
    conn.commit()
    return lid


def ticket(conn, name="Stairs", brief="A daughter notices her mom takes the stairs one at a time.", product="", cluster="", kind=None, state="pending"):
    i = conn.execute("""INSERT INTO creative_priorities_assignments (concept_key, text_key, product, vendor, cluster, story, for_week, concept_name, concept_kind, tag_state)
                        VALUES ('ticket:'||gen_random_uuid()::text, 't', %s, 'Studio Flow', %s, %s, %s, %s, %s, %s) RETURNING id""", (product, cluster, brief, MONDAY, name, kind, state)).fetchone()[0]
    conn.commit()
    return i


def row(conn, i):
    return conn.execute("SELECT product, cluster, icp, pain_point, concept_kind, line_id, tag_state, tag_note, tagged_at IS NOT NULL FROM creative_priorities_assignments WHERE id=%s", (i,)).fetchone()


class Fake:
    def __init__(self, script):
        self.script, self.calls = list(script), []

    def __call__(self, cmd, **kw):
        self.calls.append({"cmd": cmd, **kw})
        rc, out, err = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        return subprocess.CompletedProcess(cmd, rc, out if isinstance(out, str) else json.dumps(out), err)


def says(**d):
    base = {"product": "boneflex", "line_id": None, "cluster": "", "icp": "", "pain_point": "", "concept_kind": "new", "confidence": "high", "reason": "daughter, stairs, mom"}
    base.update(d)
    return [(0, {"result": json.dumps(base), "is_error": False}, "")]


def test_a_pending_ticket_gets_the_line_the_product_and_the_kind(conn):
    lid = line(conn)
    i = ticket(conn)
    fake = Fake(says(line_id=lid, concept_kind="iteration"))
    st = tt.run(conn, runner=fake, sleep=lambda s: None)
    assert st["tagged"] == 1 and st["candidates"] == 1
    r = row(conn, i)
    assert r[:6] == ("boneflex", "Aging in Place", "Trip Saver", "Joint Pain & Mobility Loss", "iteration", lid) and r[6] == "complete" and r[7].startswith("auto (high)") and r[8]
    ev = conn.execute("SELECT actor, kind FROM creative_priorities_assignment_events WHERE assignment_id=%s", (i,)).fetchall()
    assert ev == [("auto-" + at.MODEL, "tagged")]
    assert "Stairs" in fake.calls[0]["input"] and "daughter notices her mom" in fake.calls[0]["input"] and "Aging in Place" in fake.calls[0]["input"]


def test_what_was_given_is_never_overwritten_and_the_catalog_is_limited_to_the_given_product(conn):
    lid_bf = line(conn)
    line(conn, slug="beef-liver", code="B99", cluster="Hair Thinning", icp="Told It's Normal", pain="Hair Loss")
    i = ticket(conn, product="boneflex", cluster="My Own Cluster", kind="new")
    fake = Fake(says(product="beef-liver", line_id=lid_bf, concept_kind="iteration"))
    tt.run(conn, runner=fake, sleep=lambda s: None)
    r = row(conn, i)
    assert r[0] == "boneflex" and r[1] == "My Own Cluster" and r[4] == "new"                           # given values stay
    assert r[2] == "Trip Saver" and r[3] == "Joint Pain & Mobility Loss" and r[6] == "complete"        # the empty ones are filled
    assert "Hair Thinning" not in fake.calls[0]["input"]                                               # a boneflex ticket is shown only the boneflex lines


def test_a_line_of_another_product_than_the_given_one_is_dropped_and_vita_belle_keeps_free_text(conn):
    other = line(conn, slug="beef-liver", code="B98", cluster="Hair Thinning", icp="Told It's Normal", pain="Hair Loss")
    i = ticket(conn, product="boneflex")
    tt.run(conn, runner=Fake(says(product="beef-liver", line_id=other, cluster="Menopause", icp="Her", pain_point="Hot flashes")), sleep=lambda s: None)
    r = row(conn, i)
    assert r[0] == "boneflex" and r[5] is None and r[1] == "Menopause"                                   # not the other product's line: the free text it gave
    j = ticket(conn, name="Hormone-free")
    tt.run(conn, runner=Fake(says(product="vita-belle", line_id=None, cluster="Hormone-Free Menopause", icp="Health-Conscious Woman 40+", pain_point="Hot flashes", concept_kind="new")), sleep=lambda s: None)
    r = row(conn, j)
    assert r[0] == "vita-belle" and r[1] == "Hormone-Free Menopause" and r[5] is None and r[6] == "complete"


def test_a_failing_ticket_is_retried_and_gives_up_after_three_runs_without_stopping_the_others(conn):
    lid = line(conn)
    bad = ticket(conn, name="Bad")
    good = ticket(conn, name="Good")
    class Picky(Fake):
        def __call__(self, cmd, **kw):
            self.calls.append(1)
            if "Concept name: Bad" in kw["input"]:
                return subprocess.CompletedProcess(cmd, 0, json.dumps({"result": "not json", "is_error": False}), "")
            return subprocess.CompletedProcess(cmd, 0, json.dumps({"result": json.dumps({"product": "boneflex", "line_id": lid, "concept_kind": "new", "confidence": "medium", "reason": "ok"}), "is_error": False}), "")
    for n in (1, 2):
        st = tt.run(conn, runner=Picky([]), sleep=lambda s: None)
        assert row(conn, bad)[6] == "pending" and st["failed"] == 1
    st = tt.run(conn, runner=Picky([]), sleep=lambda s: None)
    assert row(conn, bad)[6] == "failed" and st["gave_up"] == 1 and "attempt 3" in row(conn, bad)[7]
    assert row(conn, good)[6] == "complete"
    assert tt.run(conn, runner=Picky([]), sleep=lambda s: None)["candidates"] == 0                       # a failed ticket is left to a person


def test_auth_or_usage_errors_stop_the_run_and_leave_the_tickets_pending(conn):
    line(conn)
    a, b = ticket(conn, name="A"), ticket(conn, name="B")
    st = tt.run(conn, runner=Fake([(1, {"result": "Invalid API key. Please run /login", "is_error": True}, "")]), sleep=lambda s: None)
    assert st["stopped_early"] and st["tagged"] == 0 and row(conn, a)[6] == "pending" and row(conn, b)[6] == "pending"


def test_dry_run_and_limit_and_dropped_tickets(conn):
    line(conn)
    ids = [ticket(conn, name=f"T{n}") for n in range(3)]
    conn.execute("UPDATE creative_priorities_assignments SET dropped_at = now() WHERE id = %s", (ids[2],))
    conn.commit()
    fake = Fake(says())
    assert tt.run(conn, dry_run=True, runner=fake)["candidates"] == 2 and fake.calls == []
    st = tt.run(conn, limit=1, runner=Fake(says()), sleep=lambda s: None)
    assert st["tagged"] == 1 and row(conn, ids[1])[6] == "pending"
