"""Image / video tag of a ticket (migration 276, etl/media_type.py): the panel save marks it manual, the backfill follows the evidence order and never overwrites a person, and is re-runnable."""
import json

import pytest
from psycopg.rows import dict_row, tuple_row

from etl import media_type as M
from etl.asset_sync import FOLDER

from .test_api_priorities_assign import MONDAY, clean, one, patch, snaps                      # noqa: F401  (fixtures)


class Drive:
    def __init__(self, tree):
        self.tree = tree                                                                    # id -> (mime, name, parent)

    def get(self, fid):
        if fid not in self.tree:
            from etl.asset_sync import LinkError
            raise LinkError("private", permanent=True)
        m, n, _ = self.tree[fid]
        return {"id": fid, "name": n, "mimeType": m}

    def children(self, fid, fields=""):
        return [{"id": k, "name": n, "mimeType": m} for k, (m, n, p) in self.tree.items() if p == fid]


def mk(pg, **kw):
    cols = {"note": "", "story": "", "creative_url": None, "variations": [], "tracker_concept_ids": [], "format_code": None}
    cols.update(kw)
    return pg.execute("""INSERT INTO creative_priorities_assignments (concept_key, text_key, product, vendor, for_week, note, story, creative_url, variations, tracker_concept_ids, format_code, position)
        VALUES (%s,'t','boneflex','Madre',%s,%s,%s,%s,%s::jsonb,%s,%s,1) RETURNING id""",
        (f"k{pg.execute('SELECT count(*) FROM creative_priorities_assignments').fetchone()[0]}{cols['note']}", MONDAY, cols["note"], cols["story"], cols["creative_url"], json.dumps(cols["variations"]), cols["tracker_concept_ids"], cols["format_code"])).fetchone()[0]


def test_evidence_rules():
    assert M.from_note("Format: Video | x") == ("video", "format_line")
    assert M.from_note("Source: a Format: Video clip") is None                               # only a line that starts with Format:
    assert M.from_note("a\nFormat: Image\nb") == ("image", "format_line")
    assert M.from_note("Imported\nType: VID\nStyle: UGC") == ("video", "type_line")
    assert M.from_note("Format: Image\nFormat: Video") == ("video", "format_line")
    assert M.from_concept_ids(["Victoria-BeefLiver-Static-1"]) == ("image", "concept_ids")
    assert M.from_concept_ids(["a-Static-1", "a-Video-1"]) == ("video", "concept_ids")
    assert M.from_ticket({"format_code": "F2"}) == ("video", "format_code")
    assert M.from_ticket({"note": "Format: Image", "format_code": "F2"}) == ("image", "format_line")   # an explicit line beats the format
    assert M.from_ticket({"note": "Beef Liver for Denise: a video about hair"}) is None                 # never guessed from words in the text


def test_panel_save_is_manual_and_blocks_the_automatic_fill(client, pg, snaps):
    a = one(client)
    assert patch(client, a["id"], media_type="video").get_json()["media_type"] == "video"
    assert pg.execute("SELECT media_type_source, media_type_basis FROM creative_priorities_assignments WHERE id = %s", (a["id"],)).fetchone() == ("manual", None)
    assert patch(client, a["id"], media_type="").get_json()["media_type"] is None                       # cleared by a person
    pg.execute("UPDATE creative_priorities_assignments SET note = 'Format: Image' WHERE id = %s", (a["id"],))
    pg.commit()
    pg.row_factory = dict_row
    try:
        assert M.autotag(pg) == 0 and M.autotag(pg, a["id"]) == 0
    finally:
        pg.row_factory = tuple_row
    assert pg.execute("SELECT media_type FROM creative_priorities_assignments WHERE id = %s", (a["id"],)).fetchone()[0] is None


def test_new_ticket_with_a_choice_is_manual(client, pg, snaps):
    r = client.post("/api/priorities/tickets", json={"concept_name": "Hook test", "product": "boneflex", "vendor": "Madre", "media_type": "image", "brief_url": "https://docs.example/b"})
    assert r.status_code in (200, 201), r.get_data(as_text=True)
    aid = r.get_json().get("id") or r.get_json()["created"][0]["id"]
    assert pg.execute("SELECT media_type, media_type_source FROM creative_priorities_assignments WHERE id = %s", (aid,)).fetchone() == ("image", "manual")


def test_backfill_order_idempotent_and_the_null_list(pg, snaps):
    drive = Drive({"fold000000000000000000000": (FOLDER, "Shoot", None), "f1": ("image/png", "a.png", "fold000000000000000000000"), "f2": ("video/mp4", "b.mp4", "fold000000000000000000000"),
                   "imgs000000000000000000000": (FOLDER, "Stills", None), "f3": ("image/jpeg", "c.jpg", "imgs000000000000000000000"), "empty00000000000000000000": (FOLDER, "Empty", None), "doc": ("application/vnd.google-apps.document", "d", "empty00000000000000000000")})
    t_line = mk(pg, note="Format: Video\nExecution: x", tracker_concept_ids=["a-Static-1"])                  # the note line beats Concept IDs
    t_ids = mk(pg, tracker_concept_ids=["x-Static-3"])
    t_fmt = mk(pg, format_code="F1")
    t_files = mk(pg)
    pg.execute("INSERT INTO asset_sync_files (assignment_id, url, source_key, mime, source_name, status) VALUES (%s,'u','k1','video/mp4','x.mp4','synced'), (%s,'u','k2','image/png','y.png','duplicate')", (t_files, t_files))
    t_folder = mk(pg, creative_url="https://drive.google.com/drive/folders/fold000000000000000000000")                            # both kinds: video
    t_still = mk(pg, variations=[{"label": "Original", "url": "https://drive.google.com/drive/folders/imgs000000000000000000000", "concept_id": ""}])
    t_vimeo = mk(pg, creative_url="https://vimeo.com/123456789")
    t_none = mk(pg)
    t_priv = mk(pg, creative_url="https://drive.google.com/file/d/gone0000000000000000000/view")
    t_empty = mk(pg, creative_url="https://drive.google.com/drive/folders/empty00000000000000000000")
    t_manual = mk(pg, note="Format: Video")
    pg.execute("UPDATE creative_priorities_assignments SET media_type = 'image', media_type_source = 'manual' WHERE id = %s", (t_manual,))
    pg.commit()
    pg.row_factory = dict_row
    try:
        dry = M.backfill(pg, drive, dry_run=True)
        assert dry["video"] + dry["image"] == 7 and pg.execute("SELECT count(*) AS n FROM creative_priorities_assignments WHERE media_type IS NOT NULL").fetchone()["n"] == 1       # a dry run writes nothing
        res = M.backfill(pg, drive)
        again = M.backfill(pg, drive)
    finally:
        pg.row_factory = tuple_row
    got = {r[0]: r[1:] for r in pg.execute("SELECT id, media_type, media_type_source, media_type_basis FROM creative_priorities_assignments").fetchall()}
    assert got[t_line] == ("video", "auto", "format_line") and got[t_ids] == ("image", "auto", "concept_ids") and got[t_fmt] == ("video", "auto", "format_code")
    assert got[t_files] == ("video", "auto", "asset_files") and got[t_folder] == ("video", "auto", "drive_link") and got[t_still] == ("image", "auto", "drive_link")
    assert got[t_vimeo] == ("video", "auto", "video_host") and got[t_manual] == ("image", "manual", None)
    assert got[t_none][0] is None and got[t_priv][0] is None and got[t_empty][0] is None
    assert {n["id"]: n["why"] for n in res["null"]}.keys() == {t_none, t_priv, t_empty} and "private" in {n["id"]: n["why"] for n in res["null"]}[t_priv]
    assert res["video"] == 5 and res["image"] == 2 and res["already_tagged"] == 1 and sum(res["tagged"].values()) == 7
    assert again["video"] == 0 and again["image"] == 0 and len(again["null"]) == 3                     # re-run: nothing new, same nulls, nothing overwritten
