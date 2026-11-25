"""etl.auto_tag second source (migration 285): Meta families with NO tracker row get a line tag in ad_family_line_tags, keyed on the Meta family. Candidate selection (spend floor, product,
no tracker row, no current tag), the write (no tracker row / ad_tags row is ever created), supersede-never-delete on --retag-audio, production tickets with a line (no model call), and the
union into the Priorities snapshot inputs and the cuts query (tracker wins where a family is on both)."""
import datetime as dt
import json
import subprocess

import pytest

from etl import auto_tag as at
from etl import creative_priorities as cp

TODAY = dt.date(2026, 11, 25)
CODE = "BF18"


@pytest.fixture(autouse=True)
def token(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-testtesttesttesttest")
    monkeypatch.setattr(at, "BACKOFF", (0, 0))


def envelope(result):
    return {"type": "result", "subtype": "success", "is_error": False, "result": result, "usage": {"input_tokens": 10, "output_tokens": 50}}


def answer(line=CODE, conf="high"):
    return json.dumps({"line": line, "confidence": conf, "runner_up": "BF21", "why": "man at the gym", "saw": "man on court"})


class Fake:
    def __init__(self, line=CODE):
        self.calls, self.line = [], line

    def __call__(self, cmd, **kw):
        self.calls.append(kw)
        return subprocess.CompletedProcess(cmd, 0, json.dumps(envelope(answer(self.line))), "")


def seed(conn):
    """Boneflex taxonomy; native families nat-a (600), nat-b (66), nat-low (9), nat-belle (vita-belle, 600); tracker family trk (on the tracker, 702)."""
    pid = conn.execute("SELECT id FROM products WHERE slug='boneflex'").fetchone()[0]
    for c in at.load_catalog("boneflex"):
        conn.execute("INSERT INTO taxonomy_lines (product_id, line_code, cluster, icp, pain_point, emotional_trigger) VALUES (%s,%s,%s,%s,%s,%s)",
                     (pid, c["line"], c["cluster"], c["icp"], c["pain"], c["trigger"]))
    bel = conn.execute("SELECT id FROM products WHERE slug='vita-belle'").fetchone()[0]
    for fk, ad, spend, prod in (("nat-a", "100", 600, pid), ("nat-b", "101", 66, pid), ("nat-low", "102", 9, pid), ("nat-belle", "103", 600, bel), ("trk", "104", 702, pid)):
        conn.execute("INSERT INTO meta_ads (ad_id, ad_name, meta_family_key, family_name) VALUES (%s,%s,%s,%s)", (ad, fk.title(), fk, fk.title()))
        for d in range(3):
            conn.execute("INSERT INTO ad_daily (ad_id, day, meta_family_key, meta_family, campaign_id, spend, purchases, conv_value, source) VALUES (%s,%s,%s,%s,'c1',%s,2,150,'meta_api')",
                         (ad, dt.date(2026, 11, 20 + d), fk, fk.title(), spend / 3))
        conn.execute("INSERT INTO unmatched_meta (meta_family_key, meta_family, product_id) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING", (fk, fk, prod))
        conn.execute("INSERT INTO creative_assets (family_key, kind, idx, mime, bytes) VALUES (%s,'thumb',0,'image/jpeg',%s)", (fk, b"\xff\xd8x"))
        conn.execute("INSERT INTO family_content (family_key, meta_body, meta_title) VALUES (%s,%s,%s)", (fk, f"copy of {fk}: knees that hurt", "Move again"))
    conn.execute("""INSERT INTO tracker_rows (row_key, concept_id, family_key, product_id, sheet_row) SELECT 'r1', 'c-trk', 'trk', id, 2 FROM products WHERE slug='boneflex'""")
    conn.commit()


def run(conn, fake, **kw):
    kw.setdefault("fetch_assets", False)
    kw.setdefault("slugs", ("boneflex",))
    kw.setdefault("tracker", False)
    return at.run(conn, day=TODAY, runner=fake, sleep=lambda s: None, **kw)


def native(conn, current=True):
    return conn.execute("SELECT family_key, product_slug, version, method, confidence, tagger, extra->>'transcript_source', is_current FROM ad_family_line_tags "
                        + ("WHERE is_current " if current else "") + "ORDER BY family_key, id").fetchall()


def test_candidates_spend_floor_product_tracker_and_existing_tag(conn):
    seed(conn)
    c = at.native_candidates(conn, ("boneflex", "vita-bull"), TODAY)
    assert [f["family_key"] for f in c["boneflex"]] == ["nat-a", "nat-b"]       # nat-low under $50, trk is on the tracker, nat-belle belongs to a product with no catalog
    assert [f["spend"] for f in c["boneflex"]] == [600.0, 66.0]                  # highest spend first
    assert at.native_candidates(conn, ("boneflex",), TODAY, min_spend=5)["boneflex"][-1]["family_key"] == "nat-low"
    conn.execute("INSERT INTO ad_family_line_tags (family_key, product_slug, version, tagger) VALUES ('nat-a','boneflex','x','human')")
    conn.commit()
    assert [f["family_key"] for f in at.native_candidates(conn, ("boneflex",), TODAY)["boneflex"]] == ["nat-b"]


def test_run_tags_native_families_only_in_the_new_table(conn):
    seed(conn)
    before = conn.execute("SELECT (SELECT count(*) FROM tracker_rows), (SELECT count(*) FROM ad_tags)").fetchone()
    fake = Fake()
    st = run(conn, fake)
    assert st["native"]["tagged"] == 2 and st["native"]["by_product"] == {"boneflex": 2} and st["native"]["by_method"] == {"image + copy (auto)": 2}
    rows = native(conn)
    assert [r[0] for r in rows] == ["nat-a", "nat-b"] and all(r[1] == "boneflex" and r[2] == "auto-2026-11-25" and r[5] == at.TAGGER and r[6] is None for r in rows)
    assert conn.execute("SELECT count(*) FROM ad_family_line_tags WHERE line_id IS NOT NULL AND cluster IS NOT NULL AND icp IS NOT NULL").fetchone()[0] == 2
    assert conn.execute("SELECT (SELECT count(*) FROM tracker_rows), (SELECT count(*) FROM ad_tags)").fetchone() == before            # no tracker row, no ad_tags row
    prompt = fake.calls[0]["input"]
    assert "copy of nat-a" in prompt and "image_file" in prompt
    n = len(fake.calls)
    run(conn, fake)                                                                                                         # idempotent: nothing new
    assert len(fake.calls) == n and len(native(conn)) == 2


def test_dry_run_counts_without_calls_or_writes(conn):
    seed(conn)
    fake = Fake()
    st = run(conn, fake, dry_run=True)
    assert st["native"]["candidate_total"] == 2 and st["native"]["candidate_spend"] == 666 and not fake.calls and native(conn) == []


def test_audio_transcript_is_read_first_and_retag_audio_supersedes_never_deletes(conn):
    seed(conn)
    run(conn, Fake(), limit=1)                                                           # nat-a only, without audio
    assert [(r[0], r[3]) for r in native(conn)] == [("nat-a", "image + copy (auto)")]
    old_id = conn.execute("SELECT id FROM ad_family_line_tags WHERE family_key='nat-a'").fetchone()[0]
    conn.execute("INSERT INTO ad_transcript (family_key, transcript, source) VALUES ('nat-a','My knees hurt and the stairs are the worst part.','whisper')")
    conn.commit()
    try:
        fake = Fake("BF21")
        st = run(conn, fake, retag_audio=True, limit=5)
        assert "My knees hurt and the stairs" in fake.calls[0]["input"]
        assert st["native"]["by_method"] == {"cover + copy + audio (auto)": 1, "image + copy (auto)": 1}      # nat-a upgraded (a transcript row means video), nat-b tagged for the first time
        allrows = native(conn, current=False)
        assert [(r[0], r[7]) for r in allrows if r[0] == "nat-a"] == [("nat-a", False), ("nat-a", True)]                       # old row kept, not current
        new = conn.execute("SELECT extra->>'previous_tag_id', extra->>'transcript_source', version FROM ad_family_line_tags WHERE family_key='nat-a' AND is_current").fetchone()
        assert new == (str(old_id), "audio", "auto-audio-2026-11-25")
        assert conn.execute("SELECT count(*) FROM ad_family_line_tags WHERE family_key='nat-a' AND is_current").fetchone()[0] == 1
        n = len(fake.calls)
        run(conn, fake, retag_audio=True, limit=5)                                       # an audio tag is not redone
        assert [c for c in fake.calls[n:] if "knees hurt and the stairs" in c["input"]] == []
    finally:
        conn.execute("DELETE FROM ad_transcript WHERE family_key='nat-a'")
        conn.commit()


def test_ticket_path_tags_without_a_model_call_and_never_overwrites(conn):
    seed(conn)
    line = conn.execute("SELECT id, line_code FROM taxonomy_lines WHERE line_code='BF05'").fetchone()
    conn.execute("TRUNCATE creative_priorities_assignments RESTART IDENTITY CASCADE")
    conn.execute("""INSERT INTO creative_priorities_assignments (concept_key, text_key, product, vendor, for_week, line_id, line_code, launched_ad_ids, variations)
                    VALUES ('line:1','t','boneflex','Madre','2026-11-23',%s,%s,'["100","104","999","abc"]'::jsonb,'[{"ad_id":"101"}]'::jsonb)""", (line[0], line[1]))
    conn.execute("INSERT INTO ad_family_line_tags (family_key, product_slug, version, tagger) VALUES ('nat-b','boneflex','human','human')")   # nat-b already tagged: untouched
    conn.commit()
    try:
        fake = Fake()
        st = run(conn, fake, limit=0)                                                    # no budget: only the ticket step can write
        assert not fake.calls
        assert st["native"]["tickets"] == {"tickets": 1, "tickets_with_ads": 1, "families_tagged": 1, "skipped_tracker": 1, "skipped_tagged": 1, "unknown_ad": 1}
        got = {r[0]: r for r in native(conn)}
        assert got["nat-a"][3] == "ticket (production assignment)" and got["nat-a"][4] == "high" and got["nat-a"][5] == "assignment"
        assert got["nat-b"][2] == "human" and "trk" not in got                           # existing tag kept; the tracker family is the tracker's business
        assert at.tag_from_tickets(conn, ("boneflex",), TODAY)["families_tagged"] == 0
    finally:
        conn.execute("TRUNCATE creative_priorities_assignments RESTART IDENTITY CASCADE")
        conn.commit()


def test_snapshot_inputs_and_cuts_see_native_families_tracker_wins(conn):
    from app import priorities_cuts as pc
    seed(conn)
    run(conn, Fake())
    conn.execute("INSERT INTO family_daily (family_key, day, stage, spend, purchases, revenue) VALUES ('trk', '2026-11-22', 'scaling', 700, 5, 400)")
    conn.execute("INSERT INTO ad_metrics (tracker_row_id, product_id, family_key, is_first_row, spend_launch) SELECT id, product_id, 'trk', true, 700 FROM tracker_rows WHERE family_key='trk'")
    # the tracker family also carries a native tag: it must not be counted twice
    conn.execute("INSERT INTO ad_family_line_tags (family_key, product_slug, version, line_id, tagger) SELECT 'trk','boneflex','x', id,'t' FROM taxonomy_lines WHERE line_code='BF05'")
    conn.commit()
    inp = cp.load_inputs(conn)
    byk = {f["family_key"]: f for f in inp["families"]}
    assert byk["nat-a"]["tracker_row_id"] is None and byk["nat-a"]["product"] == "boneflex" and byk["nat-a"]["line_id"] is not None and byk["nat-a"]["spend_launch"] == 600
    assert byk["nat-a"]["live"] is True and byk["nat-a"]["first_day"] == dt.date(2026, 11, 20)
    assert "nat-belle" not in byk and "nat-low" not in byk                                # not tagged (product / spend floor)
    assert [f["tracker_row_id"] is not None for f in inp["families"] if f["family_key"] == "trk"] == [True]       # tracker wins, one row
    assert inp["win"]["meta"]["nat-a"]["s_all"] == 600 and inp["win"]["meta"]["trk"]["s_all"] == 700
    assert inp["weekly"]["nat-a"]
    rows = conn.execute(pc.CUT_CTE + " SELECT family_key, cluster IS NOT NULL FROM tagged ORDER BY 1").fetchall()
    assert ("nat-a", True) in rows and [r[1] for r in rows if r[0] == "trk"] == [False]       # one row; tracker wins, so its (empty) tracker tag stands
    r = conn.execute(pc.SQL_META.format(flt=""), (dt.date(2026, 11, 1), dt.date(2026, 11, 30))).fetchall()
    assert round(sum(float(x[6]) for x in r)) == 1368                                      # nat-a 600 + nat-b 66 + the tracker family 702; its native tag is ignored (tracker wins)
