"""Pure functions of app/priorities_assign.py (no database): concept keys, week snapping, validation, stage derivation and the 'In production' text."""
import datetime as dt

import pytest

from app import priorities_assign as pa
from app import queries as q

D = dt.date
LINE = {"product": "vita-bull", "section": "iterate", "cluster": "Energy", "icp": "Given Up Guy", "pain_point": "Low T", "line_id": 7, "line_code": "VB07"}


def bad(fn, *a, msg=None):
    with pytest.raises(q.ApiError) as e:
        fn(*a)
    assert e.value.status == 400
    if msg:
        assert msg in e.value.message
    return e.value


# ---- keys
def test_concept_key_ignores_section_and_line_code():
    assert pa.concept_key(LINE) == "line:7"
    assert pa.concept_key({**LINE, "section": "faded", "line_code": "VB99"}) == "line:7"


def test_concept_key_manual_and_text():
    assert pa.concept_key({"manual": True, "id": 12, "key": "manual:12", "product": "boneflex", "cluster": "a", "icp": "b", "pain_point": "c"}) == "manual:12"
    draft = {"product": "beef-liver", "cluster": "  Hair  Thinning ", "icp": "Denise", "pain_point": "Shedding", "line_id": None}
    assert pa.concept_key(draft) == "text:beef-liver|hair thinning|denise|shedding"


def test_text_key_is_stored_for_every_kind_and_matches_a_later_line():
    draft = {"product": "beef-liver", "cluster": "Hair Thinning", "icp": "Denise", "pain_point": "Shedding", "line_id": None}
    later = {**draft, "line_id": 55, "section": "new"}
    assert pa.text_key(draft) == pa.text_key(later) == pa.concept_key(draft)
    assert pa.concept_key(later) == "line:55"
    assert pa.text_key(LINE) == "text:vita-bull|energy|given up guy|low t"


def test_monday_of():
    assert pa.monday_of(D(2026, 11, 9)) == D(2026, 11, 9)
    assert pa.monday_of(D(2026, 11, 15)) == D(2026, 11, 9)
    assert pa.monday_of("2026-11-12") == D(2026, 11, 9)
    assert pa.monday_of(dt.datetime(2026, 11, 10, 23, 0)) == D(2026, 11, 9)
    assert pa.monday_of(D(2026, 11, 8)) == D(2026, 11, 2)       # Sunday goes back
    bad(pa.monday_of, "nope")
    bad(pa.monday_of, 5)


# ---- urls
@pytest.mark.parametrize("u", ["https://docs.google.com/document/d/abc", "http://example.com/x?y=1", "HTTPS://Example.com"])
def test_clean_url_ok(u):
    assert pa.clean_url(u) == u


def test_clean_url_strips_control_characters_and_whitespace():
    assert pa.clean_url("  https://example.com/a\x00b\x07\n ") == "https://example.com/ab"


@pytest.mark.parametrize("u", ["javascript:alert(1)", "ftp://x.com/a", "//example.com", "example.com", "https://", "data:text/html,hi", "https://a.com/a b", "", "   ", None, 5, "https://x.com/" + "a" * 2000])
def test_clean_url_rejects(u):
    bad(pa.clean_url, u)


def test_clean_url_limit_is_2000():
    ok = "https://x.com/" + "a" * (2000 - len("https://x.com/"))
    assert pa.clean_url(ok) == ok


# ---- create / patch
def test_clean_create():
    out = pa.clean_create({"vendor": "Madre", "for_week": "2026-11-11", "note": "  rush \r\n second ", "keys": ["x"], "snapshot_week": "2026-11-08"})
    assert out == {"vendor": "Madre", "for_week": D(2026, 11, 9), "note": "rush\nsecond", "format_code": None}
    assert pa.clean_create({"vendor": "10xROAS", "for_week": "2026-11-09"})["note"] == ""


def test_clean_create_rejects():
    bad(pa.clean_create, [])
    bad(pa.clean_create, {"vendor": "Nobody", "for_week": "2026-11-09"}, msg="vendor must be one of")
    bad(pa.clean_create, {"vendor": "Madre"}, msg="week is required")
    bad(pa.clean_create, {"vendor": "Madre", "for_week": "soon"})
    bad(pa.clean_create, {"vendor": "Madre", "for_week": "2026-11-09", "note": "x" * 501})
    bad(pa.clean_create, {"vendor": "Madre", "for_week": "2026-11-09", "note": 5})


def test_clean_patch_only_present_keys_and_clears_links():
    assert pa.clean_patch({}) == {}
    out = pa.clean_patch({"vendor": "Victoria", "for_week": "2026-11-17", "note": "n", "brief_url": "https://a.com/b", "creative_url": "", "tracker_concept_ids": [" A-1 ", "A-1", "", "B-2"], "ready": True, "launched": False, "dropped": True})
    assert out == {"vendor": "Victoria", "for_week": D(2026, 11, 16), "note": "n", "brief_url": "https://a.com/b", "creative_url": None, "tracker_concept_ids": ["A-1", "B-2"], "ready": True, "launched": False, "dropped": True}
    assert pa.clean_patch({"brief_url": None}) == {"brief_url": None}


def test_clean_patch_rejects():
    bad(pa.clean_patch, {"id": 3}, msg="unknown field")
    bad(pa.clean_patch, {"vendor_note": "x"}, msg="unknown field")
    bad(pa.clean_patch, {"ready": "yes"})
    bad(pa.clean_patch, {"ready": 1})
    bad(pa.clean_patch, {"brief_url": "javascript:x"})
    bad(pa.clean_patch, {"tracker_concept_ids": "A-1"})
    bad(pa.clean_patch, {"tracker_concept_ids": [1]})
    bad(pa.clean_patch, {"tracker_concept_ids": [f"c{i}" for i in range(21)]})
    bad(pa.clean_patch, {"for_week": ""})
    bad(pa.clean_patch, {"vendor": "madre"})


def test_clean_vendor_patch_only_its_fields():
    assert pa.clean_vendor_patch({"creative_url": "https://drive.google.com/x", "vendor_note": " done \n "}) == {"creative_url": "https://drive.google.com/x", "vendor_note": "done"}
    assert pa.clean_vendor_patch({"creative_url": ""}) == {"creative_url": None}
    for k in ("brief_url", "vendor", "note", "for_week", "launched", "dropped", "tracker_concept_ids"):
        bad(pa.clean_vendor_patch, {k: "x"}, msg="unknown field")
    assert pa.clean_vendor_patch({"ready": True}) == {"ready": True} and pa.clean_vendor_patch({"ready": False}) == {"ready": False}
    for v in ("true", 1, None, "yes"):
        bad(pa.clean_vendor_patch, {"ready": v}, msg="true or false")
    bad(pa.clean_vendor_patch, {"vendor_note": "x" * 1001})
    bad(pa.clean_vendor_patch, {"creative_url": "ftp://x"})


# ---- stages
NOW = D(2026, 11, 9)


def test_stage_from_the_assignment_alone():
    assert pa.stage_of({}) == "assigned"
    assert pa.stage_of({"brief_url": "https://a.com"}) == "brief_sent"
    assert pa.stage_of({"brief_url": "https://a.com", "creative_url": "https://b.com"}) == "creative_in"
    assert pa.stage_of({"creative_url": "https://b.com", "ready_at": NOW}) == "ready"
    assert pa.stage_of({"ready_at": NOW, "launched_at": NOW}) == "launched"
    assert pa.stage_of({"launched_at": NOW, "brief_url": "x"}) == "launched"


def test_dropped_wins():
    assert pa.stage_of({"dropped_at": NOW, "launched_at": NOW}, ["8 - Launched"]) == "dropped"


@pytest.mark.parametrize("status,stage", [("2 - Up Next For Briefing", "assigned"), ("3 - Briefing", "brief_sent"), ("4 - Up Next For Creative", "brief_sent"), ("5 - Creative In Progress", "brief_sent"),
                                          ("6 - Creative Done", "creative_in"), ("7 - Approved - Ready To Launch", "ready"), ("8 - Launched", "launched"), ("9 - Relaunched", "launched"),
                                          ("1 - Idea", "assigned"), ("", "assigned"), (None, "assigned"), ("weird", "assigned")])
def test_tracker_status_sets_the_stage(status, stage):
    assert pa.stage_of({}, [status]) == stage


def test_tracker_wins_only_when_further_along():
    assert pa.stage_of({"brief_url": "x"}, ["8 - Launched"]) == "launched"
    assert pa.stage_of({"ready_at": NOW}, ["3 - Briefing"]) == "ready"                       # own is further: tracker does not pull it back
    assert pa.stage_of({"creative_url": "x"}, ["5 - Creative In Progress"]) == "creative_in"
    assert pa.stage_of({}, ["3 - Briefing", "7 - Approved", "5 - Creative In Progress"]) == "ready"
    assert pa.stage_of({}, ()) == "assigned"
    assert pa.stage_of({}, None) == "assigned"


def test_stage_labels():
    assert [pa.STAGES[k] for k in ("assigned", "brief_sent", "creative_in", "ready", "launched", "dropped")] == ["Assigned", "Brief sent", "Creative delivered", "Ready to launch", "Launched", "Dropped"]


# ---- summary
def A(vendor, week, stage, id=1):
    return {"id": id, "vendor": vendor, "for_week": week, "stage": stage}


def test_summary_empty_and_dropped_only():
    assert pa.summary_for_row([]) == ""
    assert pa.summary_for_row(None) == ""
    assert pa.summary_for_row([A("Madre", D(2026, 11, 9), "dropped")]) == ""


def test_summary_single():
    assert pa.summary_for_row([A("Madre", D(2026, 11, 9), "brief_sent")]) == "Madre, week of 11/9: brief sent"
    assert pa.summary_for_row([A("Victoria", "2026-12-07", "creative_in")]) == "Victoria, week of 12/7: creative delivered"
    assert pa.summary_for_row([A("Madre", D(2026, 11, 9), "ready")]) == "Madre, week of 11/9: ready to launch"


def test_summary_latest_open_and_earlier_count():
    rows = [A("Madre", D(2026, 11, 2), "launched", 1), A("Victoria", D(2026, 11, 16), "assigned", 2), A("Madre", D(2026, 11, 9), "brief_sent", 3), A("Internal", D(2026, 11, 23), "dropped", 4)]
    assert pa.summary_for_row(rows) == "Victoria, week of 11/16: assigned, +2 earlier"


def test_summary_prefers_open_over_a_later_launched_one_and_shows_launched_when_none_open():
    rows = [A("Madre", D(2026, 11, 9), "brief_sent", 1), A("Victoria", D(2026, 11, 16), "launched", 2)]
    assert pa.summary_for_row(rows) == "Madre, week of 11/9: brief sent, +1 earlier"
    done = [A("Madre", D(2026, 11, 2), "launched", 1), A("Victoria", D(2026, 11, 9), "launched", 2)]
    assert pa.summary_for_row(done) == "Victoria, week of 11/9: launched, +1 earlier"


def test_summary_computes_stage_when_missing_and_breaks_week_ties_by_id():
    a = {"id": 1, "vendor": "Madre", "for_week": D(2026, 11, 9), "brief_url": "https://a.com"}
    assert pa.summary_for_row([a]) == "Madre, week of 11/9: brief sent"
    t = {"id": 2, "vendor": "Madre", "for_week": D(2026, 11, 9), "tracker_statuses": ["8 - Launched"]}
    assert pa.summary_for_row([a, t]) == "Madre, week of 11/9: brief sent, +1 earlier"
    rows = [A("Madre", D(2026, 11, 9), "assigned", 1), A("Victoria", D(2026, 11, 9), "assigned", 2)]
    assert pa.summary_for_row(rows).startswith("Victoria")


def test_vendors_are_the_nine_tracker_spellings():
    assert pa.VENDORS == ["Madre", "Victoria", "TrybeDCShop", "TrybeOrion", "10xROAS", "Internal", "PuredMedia", "CreateMastery", "OpenProgram"]
    for v in ("PuredMedia", "CreateMastery", "OpenProgram"):
        assert pa.clean_create({"vendor": v, "for_week": "2026-11-09"})["vendor"] == v
    bad(pa.clean_create, {"vendor": "Other", "for_week": "2026-11-09"})


# ---------------------------------------------------------------------------------------------------------------- creative format
def test_format_code_is_cleaned_and_checked_against_the_active_list():
    assert pa.clean_create({"vendor": "Madre", "for_week": "2026-11-09"})["format_code"] is None
    assert pa.clean_create({"vendor": "Madre", "for_week": "2026-11-09", "format_code": " F2 "})["format_code"] == "F2"
    assert pa.clean_create({"vendor": "Madre", "for_week": "2026-11-09", "format_code": ""})["format_code"] is None
    assert pa.clean_patch({"format_code": None}) == {"format_code": None} and pa.clean_patch({"format_code": "  "}) == {"format_code": None} and pa.clean_patch({"format_code": "F4"}) == {"format_code": "F4"}
    assert "format_code" not in pa.clean_patch({"note": "x"})
    with pytest.raises(q.ApiError) as e:
        pa.clean_patch({"format_code": 3})
    assert e.value.status == 400
    assert pa.check_format(None, pa.FORMATS) is None and pa.check_format("F6", pa.FORMATS) == "F6"
    with pytest.raises(q.ApiError) as e:
        pa.check_format("F7", pa.FORMATS[:2])
    assert e.value.status == 400 and "F1, F2" in e.value.message
    with pytest.raises(q.ApiError):
        pa.clean_vendor_patch({"format_code": "F1"})                                    # not a vendor field
