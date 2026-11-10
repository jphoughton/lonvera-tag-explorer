"""etl.competitor_ads with the browser and `claude -p` faked: no network, no token. Card parsing, concept grouping, weekly snapshot (idempotent), failed brand keeps its last
good rows flagged stale, the competitor_concepts view, classification (catalog line / NEW / OUT, cache, cap, early stop), the Ad Library URL, empty-result judging."""
import datetime as dt
import json
import subprocess

import pytest

from etl import auto_tag as at
from etl import competitor_ads as ca

TODAY = dt.date(2026, 11, 8)                  # a Sunday
WEEK = dt.date(2026, 11, 2)


@pytest.fixture(autouse=True)
def clean(conn, monkeypatch):
    conn.execute("TRUNCATE competitor_ads, competitor_concept_class, competitor_brands CASCADE")
    conn.commit()
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-testtesttesttesttest")
    monkeypatch.setattr(at, "BACKOFF", (0, 0))
    monkeypatch.setattr(ca, "BRAND_PAUSE", 0)


def card(lib, adv, start, text, domain="shop.example.com/products/x", headline="Shop the organ blend", n=None, vids=0):
    lines = ["​", "Active", f"Library ID: {lib}", f"Started running on {start} · Total active time 30 days", "Platforms", "​"]
    if n:
        lines.append(f"{n} ads use this creative and text")
    lines += ["Open Dropdown", "See ad details", adv, "Sponsored", *text.split("\n"), domain.upper(), headline, "Shop now"]
    return {"text": "\n".join(lines), "hrefs": ["https://www.facebook.com/example/", "https://l.facebook.com/l.php?u=" + "https%3A%2F%2F" + domain.replace("/", "%2F") + "%3Futm%3D1&h=zz"], "vids": vids}


class FakeBrowser:
    def __init__(self, pages):
        self.pages, self.urls = pages, []

    def load(self, url, max_cards=150):
        self.urls.append(url)
        for k, v in self.pages.items():
            if k in url:
                if isinstance(v, Exception):
                    raise v
                return v
        return [], "No ads match your search criteria"


def answer_for(items):
    return json.dumps([{"i": i, "line": "L07", "product": "vita-bull", "confidence": "medium", "why": "says low testosterone"} for i in range(items)])


class FakeClaude:
    def __init__(self, script):
        self.script, self.calls = list(script), []

    def __call__(self, cmd, **kw):
        self.calls.append(kw.get("input"))
        rc, out, err = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if callable(out):
            out = out(kw.get("input"))
        return subprocess.CompletedProcess(cmd, rc, json.dumps({"is_error": False, "result": out, "usage": {}}) if rc == 0 else out, err)


def echo(prompt):
    n = prompt.count('"hook"')
    return answer_for(n)


PAGES = {"Primal%20Viking": ([card("1", "Primal Viking", "Jun 2, 2026", "Low T is robbing you.\nTry our organs.", n=3),
                              card("2", "Primal Viking", "Aug 1, 2026", "Low T is robbing you.\nTry our organs."),     # same copy: same concept
                              card("3", "Primal Viking", "Oct 20, 2026", "Reindeer vs beef organs", vids=1),
                              card("9", "Some Creator with Primal Viking", "Oct 20, 2026", "not theirs")], "")}


def pages_for(name_key="Primal+Viking"):
    return {name_key: PAGES["Primal%20Viking"]}


def test_parse_card_fields():
    c = ca.parse_card(card("123", "Heart & Soil", "Aug 1, 2026", "Restore your health.\nSecond line.", n=4, vids=1))
    assert c["library_id"] == "123" and c["advertiser"] == "Heart & Soil" and c["start_date"] == dt.date(2026, 8, 1)
    assert c["variant_count"] == 4 and c["format"] == "video" and c["hook"] == "Restore your health."
    assert c["headline"] == "Shop the organ blend" and c["landing_url"] == "shop.example.com/products/x"
    assert ca.parse_card({"text": "nothing", "hrefs": []}) is None


def test_concept_key_groups_near_duplicates_and_is_brand_scoped():
    a = ca.concept_key("Primal Viking", "Low T is robbing you!  Try our organs.", "", "")
    assert a == ca.concept_key("Primal Viking", "low t is robbing you try our organs", "other headline", "x.com")
    assert a != ca.concept_key("Heart & Soil", "Low T is robbing you", "", "")
    assert ca.concept_key("A", "", "Headline only", "d.com").startswith("a-")


def test_library_url_is_active_us_exact_phrase_sorted():
    u = ca.library_url("Heart & Soil")
    assert "active_status=active" in u and "country=US" in u and "keyword_exact_phrase" in u and "Heart+%26+Soil" in u.replace("%22", "")


def test_judge_empty():
    assert ca.judge_empty("No ads match your search criteria") is None
    assert "age" in ca.judge_empty("Please confirm your age to see these ads")
    assert ca.judge_empty("loading...") is not None


def run(conn, browser, **kw):
    kw.setdefault("brand", "Primal Viking")
    kw.setdefault("week", WEEK)
    kw.setdefault("today", TODAY)
    kw.setdefault("sleep", lambda s: None)
    kw.setdefault("do_classify", False)
    return ca.run(conn, browser=browser, **kw)


def test_run_stores_snapshot_filters_advertiser_and_is_idempotent(conn):
    b = FakeBrowser(pages_for())
    st = run(conn, b)
    assert st["ads"] == 3 and st["failed_brands"] == [] and st["brands"]["Primal Viking"]["concepts"] == 2        # creator "with Primal Viking" excluded
    rows = conn.execute("SELECT library_id, days_running, variant_count, format, carried_from FROM competitor_ads ORDER BY library_id").fetchall()
    assert rows == [("1", 159, 3, "image", None), ("2", 99, 1, "image", None), ("3", 19, 1, "video", None)]
    run(conn, FakeBrowser(pages_for()))
    assert conn.execute("SELECT count(*) FROM competitor_ads").fetchone()[0] == 3
    ok = conn.execute("SELECT last_ok_at IS NOT NULL, last_error, last_ad_count FROM competitor_brands WHERE brand='Primal Viking'").fetchone()
    assert ok == (True, None, 3)


def test_view_contract_columns_and_aggregation(conn):
    run(conn, FakeBrowser(pages_for()))
    cur = conn.execute("SELECT * FROM competitor_concepts WHERE brand='Primal Viking' ORDER BY days_running DESC")
    cols = [d.name for d in cur.description]
    assert cols == ["week_start", "brand", "concept_key", "first_ad_start_date", "days_running", "variant_count", "format", "hook", "primary_text_excerpt", "mapped_product",
                    "line_code", "cluster", "icp", "pain_point", "confidence", "is_new_angle", "new_angle_label", "as_of", "is_stale"]
    r = dict(zip(cols, cur.fetchall()[0]))
    assert r["week_start"] == WEEK and r["days_running"] == 159 and r["variant_count"] == 4 and r["first_ad_start_date"] == dt.date(2026, 6, 2)
    assert r["hook"] == "Low T is robbing you." and r["confidence"] == "low" and r["is_new_angle"] is False and r["is_stale"] is False and r["as_of"] == TODAY
    assert r["mapped_product"] is None and r["line_code"] is None


def test_failed_brand_keeps_last_good_rows_flagged_stale(conn):
    run(conn, FakeBrowser(pages_for()))
    nxt = WEEK + dt.timedelta(days=7)
    st = run(conn, FakeBrowser({"Primal+Viking": ca.BrandFailed("age-gated")}), week=nxt, today=TODAY + dt.timedelta(days=7))
    assert st["failed_brands"][0]["error"] == "age-gated" and st["carried_rows"] == 3
    rows = conn.execute("SELECT week_start, is_stale, as_of FROM competitor_concepts WHERE week_start=%s", (nxt,)).fetchall()
    assert len(rows) == 2 and all(r[1] and r[2] == TODAY for r in rows)                 # carried: stale, as_of = the day the data was really read
    assert conn.execute("SELECT last_error FROM competitor_brands WHERE brand='Primal Viking'").fetchone()[0] == "age-gated"
    run(conn, FakeBrowser(pages_for()), week=nxt, today=TODAY + dt.timedelta(days=7))      # recovery replaces the carried rows
    assert conn.execute("SELECT count(*) FILTER (WHERE carried_from IS NOT NULL), count(*) FROM competitor_ads WHERE week_start=%s", (nxt,)).fetchone() == (0, 3)
    assert conn.execute("SELECT last_error FROM competitor_brands WHERE brand='Primal Viking'").fetchone()[0] is None


def test_zero_cards_wall_fails_the_brand_but_real_empty_result_does_not(conn):
    st = run(conn, FakeBrowser({"Primal+Viking": ([], "loading")}))
    assert st["failed_brands"] and st["ads"] == 0
    st = run(conn, FakeBrowser({"Primal+Viking": ([], "No ads match your search criteria")}))
    assert st["failed_brands"] == [] and st["ads"] == 0


def test_two_phrases_merge_and_dedupe_by_library_id(conn):
    pages = {"My+Marrow": ([card("1", "My Marrow", "Oct 1, 2026", "Marrow for joints")], ""), "mymarrow.com": ([card("1", "My Marrow", "Oct 1, 2026", "Marrow for joints"),
                                                                                                         card("2", "My Marrow", "Oct 2, 2026", "Second angle")], "")}
    st = run(conn, FakeBrowser(pages), brand="My Marrow")
    assert st["ads"] == 2


def test_parse_batch_validates_codes():
    cat = ca.all_catalog()
    out = ca.parse_batch(json.dumps([
        {"i": 0, "line": "L07", "product": "x", "confidence": "high", "why": "w"},
        {"i": 1, "line": "ZZ99", "product": "boneflex", "confidence": "high"},                       # invented code: dropped
        {"i": 2, "line": "NEW", "product": "boneflex", "confidence": "medium", "new_label": "Pickleball knees", "cluster": "Sports", "icp": "Player", "pain_point": "Knees"},
        {"i": 3, "line": "OUT", "product": "none", "confidence": "high"},
        {"i": 4, "line": "L07", "confidence": "maybe"},                                              # bad confidence: dropped
        {"i": 9, "line": "L07", "confidence": "low"}]), cat, 4)                                       # out of range: dropped
    assert sorted(out) == [0, 2, 3]
    assert out[0]["product"] == "vita-bull" and out[0]["cluster"] and not out[0]["is_new"]             # product comes from the catalog, not the model
    assert out[2]["is_new"] and out[2]["new_label"] == "Pickleball knees" and out[2]["product"] == "boneflex" and out[2]["line"] is None
    assert out[3]["product"] == "none"
    with pytest.raises(ValueError):
        ca.parse_batch("sorry", cat, 2)


def test_classify_caches_by_concept_batches_and_fills_the_view(conn):
    run(conn, FakeBrowser(pages_for()))
    fake = FakeClaude([(0, echo, "")])
    st = ca.classify(conn, WEEK, 150, runner=fake, sleep=lambda s: None)
    assert st["to_classify"] == 2 and st["classified"] == 2 and len(fake.calls) == 1                  # one call for both concepts
    assert "L07" in fake.calls[0] and "B01" in fake.calls[0] and "BF01" in fake.calls[0]              # all three catalogs in the prompt
    r = conn.execute("SELECT mapped_product, line_code, cluster, confidence FROM competitor_concepts ORDER BY days_running DESC").fetchone()
    assert r[0] == "vita-bull" and r[1] == "L07" and r[2] and r[3] == "medium"
    st = ca.classify(conn, WEEK, 150, runner=fake, sleep=lambda s: None)
    assert st["to_classify"] == 0 and len(fake.calls) == 1                                            # cached: no new usage
    run(conn, FakeBrowser(pages_for()), week=WEEK + dt.timedelta(days=7))
    assert ca.classify(conn, WEEK + dt.timedelta(days=7), 150, runner=fake, sleep=lambda s: None)["to_classify"] == 0   # same concepts next week: still cached


def test_classify_cap_and_early_stop(conn, monkeypatch):
    run(conn, FakeBrowser(pages_for()))
    monkeypatch.setattr(ca, "BATCH", 1)
    fake = FakeClaude([(0, echo, "")])
    st = ca.classify(conn, WEEK, 1, runner=fake, sleep=lambda s: None)
    assert st["capped"] and st["classified"] == 1 and len(fake.calls) == 1
    limit = FakeClaude([(1, "usage limit reached", "")])
    st = ca.classify(conn, WEEK, 150, runner=limit, sleep=lambda s: None)
    assert st["stopped_early"] and "limit" in st["stopped_early"] and st["classified"] == 0 and len(limit.calls) == 1


def test_classify_failed_batch_is_non_fatal_and_retried_next_run(conn):
    run(conn, FakeBrowser(pages_for()))
    bad = FakeClaude([(0, "not json at all", "")])
    st = ca.classify(conn, WEEK, 150, runner=bad, sleep=lambda s: None)
    assert st["failed_batches"] == 1 and st["classified"] == 0 and len(bad.calls) == at.MAX_TRIES
    assert len(ca.unclassified(conn, WEEK)) == 2


def test_run_without_token_scrapes_but_skips_classification(conn, monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN")
    st = run(conn, FakeBrowser(pages_for()), do_classify=True)
    assert st["ads"] == 3 and "classify" not in st


def test_dry_run_writes_nothing(conn):
    st = run(conn, FakeBrowser(pages_for()), dry_run=True)
    assert st["ads"] == 3 and conn.execute("SELECT count(*) FROM competitor_ads").fetchone()[0] == 0


def test_week_start_is_monday():
    assert ca.week_start_for(dt.date(2026, 11, 8)) == dt.date(2026, 11, 2) and ca.week_start_for(dt.date(2026, 11, 9)) == dt.date(2026, 11, 9)
