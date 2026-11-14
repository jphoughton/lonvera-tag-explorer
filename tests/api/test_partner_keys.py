"""partner_push API keys (migration 261; app/api_keys.py, app/auth.py, app/partner_tickets.py): an outside creative partner, bound to one vendor, pushes tickets and creative links, sees only its own
vendor's tickets, reads only the creative and performance data (an explicit allow-list), and nothing else."""
import pytest

from app import api as api_mod
from app import api_keys as ak
from app import priorities_assign as pas
from etl import api_keys as cli

from .test_api_keys import bearer, fresh_throttle, mint, key, post_assign      # noqa: F401  (fixtures)
from .test_api_priorities_assign import MONDAY, NEXT_MONDAY, clean, one, snaps    # noqa: F401

BRIEF = "https://docs.google.com/document/d/abc123/edit"


def ticket(**kw):
    t = {"concept_name": "Knees that keep up", "product": "boneflex", "cluster": "Pain-Relief Alternatives", "concept_kind": "new", "for_week": MONDAY, "variations": [{"label": "Original"}],
         "brief_url": BRIEF, "brief": "A grandmother gets back on the floor with her grandkids."}
    t.update(kw)
    return t


@pytest.fixture()
def pkey(pg):
    pg.execute("DELETE FROM api_keys")
    pg.commit()
    made = {}

    def make(name="partner-madre", vendor="Madre"):
        raw, _ = cli.create_key(pg, name, "pytest", "partner_push", vendor)
        made[name] = raw
        return raw

    api_mod._partner_limiter = None
    yield make
    api_mod._partner_limiter = None
    pg.rollback()
    pg.execute("DELETE FROM api_keys")
    pg.commit()


@pytest.fixture()
def pk(pkey):
    return pkey()


def create(anon, raw, **kw):
    return anon.post("/api/priorities/tickets", json=ticket(**kw), headers=bearer(raw))


# ------------------------------------------------------------------------------------------------------------ keys and the CLI
def test_cli_makes_partner_keys_only_with_a_valid_vendor(pg, pkey):
    with pytest.raises(cli.CliError):
        cli.create_key(pg, "p1", "t", "partner_push", None)
    with pytest.raises(cli.CliError):
        cli.create_key(pg, "p1", "t", "partner_push", "Nobody Inc")
    with pytest.raises(cli.CliError):
        cli.create_key(pg, "p1", "t", "read_assign", "Madre")
    with pytest.raises(cli.CliError):
        cli.create_key(pg, "p1", "t", "admin", None)
    pkey("p-ok", "Studio Flow")
    row = pg.execute("SELECT scope, vendor FROM api_keys WHERE name = 'p-ok'").fetchone()
    assert tuple(row) == ("partner_push", "Studio Flow")
    assert [r[2:4] for r in cli.list_keys(pg)] == [("partner_push", "Studio Flow")]
    import psycopg
    with pytest.raises(psycopg.errors.CheckViolation):
        pg.execute("INSERT INTO api_keys (name, key_hash, prefix, scope) VALUES ('x', 'h', 'lvk_aaaa', 'partner_push')")      # a partner key without a vendor
    pg.rollback()


def test_cli_main_creates_lists_and_revokes(pg, pkey, capsys, monkeypatch):
    pg.execute("DELETE FROM api_keys")
    pg.commit()
    url = pg.info.dsn
    from etl import db
    monkeypatch.setattr(db, "connect", lambda u=None: __import__("psycopg").connect(pg.info.dsn))
    assert cli.main(["create", "--name", "cli-p", "--scope", "partner_push", "--vendor", "Victoria"]) == 0
    raw = capsys.readouterr().out.strip()
    assert ak.well_formed(raw)
    assert cli.main(["list"]) == 0
    out = capsys.readouterr().out
    assert "partner_push" in out and "Victoria" in out and raw not in out
    assert cli.main(["create", "--name", "cli-bad", "--scope", "partner_push"]) == 1
    assert cli.main(["revoke", "--name", "cli-p"]) == 0


# ------------------------------------------------------------------------------------------------------------ writes: tickets
def test_a_partner_can_create_one_and_a_batch_and_vendor_is_forced(anon, pk, pg, snaps):
    r = create(anon, pk)                                                       # no vendor in the body: filled in
    assert r.status_code == 201, r.get_data(as_text=True)
    c = r.get_json()
    assert c["vendor"] == "Madre" and c["concept_name"] == "Knees that keep up" and c["needs_creative"] is True and set(c) >= {"id", "status", "variations", "tags"}
    assert "note" not in c and "created_by" not in c and "history" not in c
    row = pg.execute("SELECT created_by, updated_by, vendor FROM creative_priorities_assignments WHERE id = %s", (c["id"],)).fetchone()
    assert tuple(row) == ("key:partner-madre", "key:partner-madre", "Madre")
    ev = pg.execute("SELECT actor, kind FROM creative_priorities_assignment_events WHERE assignment_id = %s", (c["id"],)).fetchall()
    assert ("key:partner-madre", "assigned") in [tuple(e) for e in ev]
    assert create(anon, pk, vendor="Madre").status_code == 201                # the key's own vendor spelled out is fine
    r = anon.post("/api/priorities/tickets", json={"tickets": [ticket(concept_name=f"Batch {i}") for i in range(3)]}, headers=bearer(pk))
    assert r.status_code == 201 and [t["vendor"] for t in r.get_json()["created"]] == ["Madre"] * 3
    r = create(anon, pk, vendor="Victoria")
    assert r.status_code == 403 and "Madre" in r.get_json()["error"]
    r = anon.post("/api/priorities/tickets", json={"tickets": [ticket(), ticket(vendor="Victoria")]}, headers=bearer(pk))
    assert r.status_code == 403 and r.get_json()["error"].startswith("ticket 2:")
    n = pg.execute("SELECT count(*) FROM creative_priorities_assignments WHERE vendor = 'Victoria'").fetchone()[0]
    assert n == 0


def test_partner_ticket_validation_names_the_field(anon, pk, pg, snaps):
    r = anon.post("/api/priorities/tickets", json={"brief": "x", "vendor": "Madre"}, headers=bearer(pk))
    assert r.status_code == 400 and "concept_name" in r.get_json()["error"]
    r = anon.post("/api/priorities/tickets", json={"concept_name": "No brief"}, headers=bearer(pk))
    assert r.status_code == 400 and "brief" in r.get_json()["error"]
    assert create(anon, pk, status="in_review").status_code == 400
    assert create(anon, pk, status="in_production").status_code == 201
    assert create(anon, pk, brief_url="http://insecure.example.com/x").status_code == 400
    r = create(anon, pk, variations=[{"label": "Original", "url": "javascript:alert(1)"}])
    assert r.status_code == 400 and "variations[1].url" in r.get_json()["error"]
    r = anon.post("/api/priorities/tickets", json={"tickets": [ticket(), ticket(variations=[{"label": "A", "url": "http://x.example.com/a"}])]}, headers=bearer(pk))
    assert r.status_code == 400 and r.get_json()["error"].startswith("ticket 2: variations[1].url")
    assert pg.execute("SELECT count(*) FROM creative_priorities_assignments WHERE concept_name = 'Knees that keep up'").fetchone()[0] == 1       # the in_production one only: the batch saved nothing


def test_the_minimal_ticket(anon, pk, snaps):
    r = anon.post("/api/priorities/tickets", json={"concept_name": "Just a name", "brief": "Short brief"}, headers=bearer(pk))
    assert r.status_code == 201 and r.get_json()["vendor"] == "Madre"
    assert anon.post("/api/priorities/tickets", json={"brief": "Short brief"}, headers=bearer(pk)).status_code == 400


# ------------------------------------------------------------------------------------------------------------ reads: own tickets
def test_list_shows_only_own_vendor_in_a_minimal_shape(anon, pk, pkey, client, snaps):
    mine = create(anon, pk).get_json()
    other = one(client, vendor="Victoria")
    anon_victoria = pkey("partner-victoria", "Victoria")
    r = anon.get("/api/priorities/partner/tickets", headers=bearer(pk))
    assert r.status_code == 200
    body = r.get_json()
    assert [t["id"] for t in body["tickets"]] == [mine["id"]] and body["count"] == 1 and body["limit"] == 100
    assert set(body["tickets"][0]) == {"id", "concept_name", "product", "cluster", "icp", "pain_point", "concept_kind", "variations", "vendor", "for_week", "due_date", "status", "created_at", "updated_at", "needs_creative", "tags"}
    assert body["tickets"][0]["tags"] in ("pending", "complete")
    assert [t["id"] for t in anon.get("/api/priorities/partner/tickets", headers=bearer(anon_victoria)).get_json()["tickets"]] == [other["id"]]
    assert anon.get("/api/priorities/partner/tickets?status=in_production", headers=bearer(pk)).get_json()["count"] == 0
    assert anon.get(f"/api/priorities/partner/tickets?week={MONDAY}", headers=bearer(pk)).get_json()["count"] == 1
    assert anon.get(f"/api/priorities/partner/tickets?week={NEXT_MONDAY}", headers=bearer(pk)).get_json()["count"] == 0
    assert anon.get("/api/priorities/partner/tickets?status=nope", headers=bearer(pk)).status_code == 400
    assert anon.get("/api/priorities/partner/tickets?limit=501", headers=bearer(pk)).status_code == 400
    for _ in range(2):
        create(anon, pk)
    page = anon.get("/api/priorities/partner/tickets?limit=2&offset=1", headers=bearer(pk)).get_json()
    assert page["count"] == 3 and len(page["tickets"]) == 2 and page["offset"] == 1
    assert client.get("/api/priorities/partner/tickets").status_code == 403           # a login has no vendor: partner keys only


def test_dropped_tickets_are_not_listed(anon, pk, client, snaps):
    t = create(anon, pk).get_json()
    assert client.patch(f"/api/priorities/assignments/{t['id']}", json={"dropped": True}).status_code == 200
    assert anon.get("/api/priorities/partner/tickets", headers=bearer(pk)).get_json()["count"] == 0


# ------------------------------------------------------------------------------------------------------------ patch
def test_patch_own_ticket_only_allowed_fields(anon, pk, pkey, pg, client, snaps):
    t = create(anon, pk).get_json()
    url = f"/api/priorities/partner/tickets/{t['id']}"
    r = anon.patch(url, json={"concept_name": "New name", "cluster": "Menopause", "concept_kind": "iteration", "story": "Longer story",
                              "brief_url": BRIEF + "2", "variations": [{"label": "Original", "url": "https://drive.google.com/file/d/AAA/view"}, {"label": "Hook 2"}]}, headers=bearer(pk))
    assert r.status_code == 200, r.get_data(as_text=True)
    j = r.get_json()
    assert j["concept_name"] == "New name" and j["cluster"] == "Menopause" and j["concept_kind"] == "iteration" and len(j["variations"]) == 2 and j["needs_creative"] is False
    row = pg.execute("SELECT story, updated_by, brief_url FROM creative_priorities_assignments WHERE id = %s", (t["id"],)).fetchone()
    assert tuple(row) == ("Longer story", "key:partner-madre", BRIEF + "2")
    for bad in ({"vendor": "Victoria"}, {"status": "ready"}, {"note": "x"}, {"priority": "high"}, {"for_week": NEXT_MONDAY}):
        r = anon.patch(url, json=bad, headers=bearer(pk))
        assert r.status_code == 400 and next(iter(bad)) in r.get_json()["error"], bad
    assert anon.patch(url, json={"concept_kind": "maybe"}, headers=bearer(pk)).status_code == 400
    assert anon.patch(url, json={"creative_url": "http://x.example.com/a"}, headers=bearer(pk)).status_code == 400
    assert anon.patch(url, json={}, headers=bearer(pk)).status_code == 400
    other = one(client, vendor="Victoria")
    other_key = pkey("partner-victoria", "Victoria")
    r = anon.patch(f"/api/priorities/partner/tickets/{other['id']}", json={"concept_name": "hijack"}, headers=bearer(pk))
    assert r.status_code == 404 and r.get_json() == {"error": "Ticket not found."}
    assert anon.patch("/api/priorities/partner/tickets/99999999", json={"concept_name": "x"}, headers=bearer(pk)).get_json() == r.get_json()      # same answer as a real ticket of someone else
    assert pg.execute("SELECT concept_name FROM creative_priorities_assignments WHERE id = %s", (other["id"],)).fetchone()[0] != "hijack"
    assert other_key
    client.patch(f"/api/priorities/assignments/{t['id']}", json={"dropped": True})
    assert anon.patch(url, json={"concept_name": "x"}, headers=bearer(pk)).status_code == 409


# ------------------------------------------------------------------------------------------------------------ the creative
def test_creative_merges_by_label_and_never_deletes(anon, pk, pg, snaps):
    t = create(anon, pk, variations=[{"label": "Original"}, {"label": "Hook 2"}]).get_json()
    url = f"/api/priorities/partner/tickets/{t['id']}/creative"
    r = anon.post(url, json={"variations": [{"label": "Hook 2", "url": "https://frame.io/presentations/aaa", "concept_id": "Madre-BF-1.2"}]}, headers=bearer(pk))
    assert r.status_code == 200, r.get_data(as_text=True)
    j = r.get_json()
    assert j["submitted"] is False and j["ticket"]["needs_creative"] is False
    assert [(v["label"], v["url"]) for v in j["ticket"]["variations"]] == [("Original", ""), ("Hook 2", "https://frame.io/presentations/aaa")]
    r = anon.post(url, json={"variations": [{"label": "hook 3", "url": "https://www.dropbox.com/s/xyz/hook3.mp4?dl=0"}, {"label": "ORIGINAL", "url": "https://vimeo.com/123456789"}]}, headers=bearer(pk))
    vs = r.get_json()["ticket"]["variations"]
    assert [v["label"] for v in vs] == ["Original", "Hook 2", "hook 3"] and vs[0]["url"] == "https://vimeo.com/123456789" and vs[1]["concept_id"] == "Madre-BF-1.2" and vs[1]["url"] == "https://frame.io/presentations/aaa"
    row = pg.execute("SELECT creative_url, creative_at IS NOT NULL, tracker_concept_ids FROM creative_priorities_assignments WHERE id = %s", (t["id"],)).fetchone()
    assert row[0] == "https://frame.io/presentations/aaa" and row[1] is True and row[2] == ["Madre-BF-1.2"]        # the first url that was there
    ev = pg.execute("SELECT actor FROM creative_priorities_assignment_events WHERE assignment_id = %s AND kind = 'creative_added'", (t["id"],)).fetchall()
    assert len(ev) == 2 and {e[0] for e in ev} == {"key:partner-madre"}


def test_creative_shorthand_is_the_original(anon, pk, snaps):
    t = create(anon, pk, variations=[{"label": "Original"}]).get_json()
    r = anon.post(f"/api/priorities/partner/tickets/{t['id']}/creative", json={"creative_url": "https://www.loom.com/share/0123456789abcdef"}, headers=bearer(pk))
    assert r.status_code == 200 and r.get_json()["ticket"]["variations"][0] == {"label": "Original", "url": "https://www.loom.com/share/0123456789abcdef", "concept_id": ""}
    bad = anon.post(f"/api/priorities/partner/tickets/{t['id']}/creative", json={"creative_url": "x", "variations": []}, headers=bearer(pk))
    assert bad.status_code == 400


def test_submit_moves_to_review_only_from_in_production(anon, pk, pg, snaps):
    a = create(anon, pk, status="in_production").get_json()
    b = create(anon, pk, status="brief_sent").get_json()
    body = {"creative_url": "https://drive.google.com/file/d/AAA/view", "submit": True}
    r = anon.post(f"/api/priorities/partner/tickets/{a['id']}/creative", json=body, headers=bearer(pk)).get_json()
    assert r["submitted"] is True and r["status"] == "in_review" and r["ticket"]["status"] == "in_review"
    ev = pg.execute("SELECT actor FROM creative_priorities_assignment_events WHERE assignment_id = %s AND kind = 'status_changed' AND stage_after = 'in_review'", (a["id"],)).fetchall()
    assert [e[0] for e in ev] == ["key:partner-madre"]
    r = anon.post(f"/api/priorities/partner/tickets/{b['id']}/creative", json=body, headers=bearer(pk))
    j = r.get_json()
    assert r.status_code == 200 and j["submitted"] is False and j["status"] == "brief_sent" and "stays" in j["message"]


def test_creative_other_vendor_is_404_and_bad_urls_name_the_field(anon, pk, client, snaps):
    other = one(client, vendor="Victoria")
    assert anon.post(f"/api/priorities/partner/tickets/{other['id']}/creative", json={"creative_url": "https://drive.google.com/file/d/A/view"}, headers=bearer(pk)).status_code == 404
    t = create(anon, pk).get_json()
    url = f"/api/priorities/partner/tickets/{t['id']}/creative"
    for u in ("http://drive.google.com/x", "javascript:alert(1)", "data:text/html,hi", "file:///etc/passwd", "https://localhost/x", "https://127.0.0.1/x", "https://intranet/x", "https://user:pw@drive.google.com/x",
              "https://drive.google.com:8443/x", "https://drive.google.com/a b", "https://" + "a" * 2100 + ".com/x", "ftp://x.example.com/f", "//drive.google.com/x"):
        r = anon.post(url, json={"variations": [{"label": "Original", "url": u}]}, headers=bearer(pk))
        assert r.status_code == 400 and "variations[1].url" in r.get_json()["error"], u
    r = anon.post(url, json={"variations": [{"label": "Original"}]}, headers=bearer(pk))
    assert r.status_code == 400 and "variations[1].url" in r.get_json()["error"]
    assert anon.post(url, json={"variations": [{"url": "https://drive.google.com/file/d/A/view"}]}, headers=bearer(pk)).status_code == 400
    assert anon.post(url, json={"creative_url": "http://x.example.com/a"}, headers=bearer(pk)).get_json()["error"].startswith("creative_url")
    for good in ("https://app.frame.io/reviews/abc", "https://www.dropbox.com/s/x/y.mp4", "https://box.com/s/abc", "https://vimeo.com/1234567", "https://youtu.be/abcDEF12345", "https://drive.google.com/file/d/A/view"):
        assert anon.post(url, json={"creative_url": good}, headers=bearer(pk)).status_code == 200, good


def test_creative_batch_is_all_or_nothing(anon, pk, pg, client, snaps):
    a, b = create(anon, pk).get_json(), create(anon, pk).get_json()
    other = one(client, vendor="Victoria")
    ok = {"ticket_id": a["id"], "variations": [{"label": "Original", "url": "https://drive.google.com/file/d/AAA/view"}]}
    r = anon.post("/api/priorities/partner/creative", json={"creatives": [ok, {"ticket_id": b["id"], "creative_url": "http://nope.example.com/x"}]}, headers=bearer(pk))
    assert r.status_code == 400 and r.get_json()["error"].startswith("creative 2: creative_url")
    r = anon.post("/api/priorities/partner/creative", json={"creatives": [ok, {"ticket_id": other["id"], "creative_url": "https://drive.google.com/file/d/B/view"}]}, headers=bearer(pk))
    assert r.status_code == 404 and r.get_json()["error"].startswith("creative 2:")
    assert pg.execute("SELECT creative_url FROM creative_priorities_assignments WHERE id = %s", (a["id"],)).fetchone()[0] is None          # nothing was saved
    r = anon.post("/api/priorities/partner/creative", json={"creatives": [ok, {"ticket_id": b["id"], "creative_url": "https://vimeo.com/1234567", "submit": True}]}, headers=bearer(pk))
    assert r.status_code == 200 and [x["ticket_id"] for x in r.get_json()["results"]] == [a["id"], b["id"]] and r.get_json()["results"][1]["submitted"] is False
    assert anon.post("/api/priorities/partner/creative", json={"creatives": []}, headers=bearer(pk)).status_code == 400
    assert anon.post("/api/priorities/partner/creative", json={"creatives": [{"ticket_id": "x"}]}, headers=bearer(pk)).get_json()["error"].startswith("creative 1:")


def test_contract_describes_the_key(anon, pk):
    j = anon.get("/api/priorities/partner/contract", headers=bearer(pk)).get_json()
    assert j["vendor"] == "Madre" and "boneflex" in j["products"] and j["statuses_you_may_set"] == ["assigned", "brief_sent", "in_production"] and j["limits"]["writes_per_minute"] == 60
    assert j["required_fields"][0] == "concept_name"


# ------------------------------------------------------------------------------------------------------------ guards
def test_rate_limit_per_key_and_body_cap(anon, pkey, app, snaps):
    a, b = pkey("p-a", "Madre"), pkey("p-b", "Victoria")
    old = app.config.get("PARTNER_WRITES_PER_MIN")
    app.config["PARTNER_WRITES_PER_MIN"] = 3
    api_mod._partner_limiter = None
    try:
        for _ in range(3):
            assert create(anon, a).status_code == 201
        r = create(anon, a)
        assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1
        assert create(anon, b, vendor="Victoria").status_code == 201                      # another key has its own budget
        assert anon.get("/api/priorities/partner/tickets", headers=bearer(a)).status_code == 200      # reads are not writes
    finally:
        app.config["PARTNER_WRITES_PER_MIN"] = old
        api_mod._partner_limiter = None
    big = ticket(story="x" * (1024 * 1024 + 10))
    assert anon.post("/api/priorities/tickets", json=big, headers=bearer(a)).status_code in (413, 400)


def test_origin_check_still_applies(anon, pk, snaps):
    assert create(anon, pk).status_code == 201
    r = anon.post("/api/priorities/tickets", json=ticket(), headers={**bearer(pk), "Origin": "http://evil.example"})
    assert r.status_code == 403


def test_revoked_partner_key_is_401(anon, pk, pg):
    assert anon.get("/api/priorities/partner/contract", headers=bearer(pk)).status_code == 200
    cli.revoke_key(pg, "partner-madre")
    assert anon.get("/api/priorities/partner/contract", headers=bearer(pk)).status_code == 401
    assert create(anon, pk).status_code == 401


def test_a_partner_key_never_logs_in_nor_uses_vendor_pages_nor_the_brain(anon, pk, client, snaps):
    r = anon.post("/api/login", json={}, headers=bearer(pk))
    assert r.status_code == 401 and r.headers.get("Set-Cookie") is None
    assert anon.get("/api/priorities/vendor-links", headers=bearer(pk)).status_code == 403
    assert anon.get("/api/priorities/vendor/" + "A" * 30, headers=bearer(pk)).status_code == 404         # the public vendor page: the token is the credential, a key neither helps nor hurts
    r = anon.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers=bearer(pk))
    assert r.status_code in (401, 403) and r.status_code != 200


def test_a_read_assign_key_is_unchanged_and_cannot_use_partner_routes(anon, key, snaps):
    assert anon.get("/api/trends", headers=bearer(key)).status_code == 200
    assert anon.get("/api/priorities/board", headers=bearer(key)).status_code == 200
    r = anon.get("/api/priorities/partner/tickets", headers=bearer(key))
    assert r.status_code == 403 and "partner" in r.get_json()["error"].lower()
    assert anon.get("/api/priorities/partner/contract", headers=bearer(key)).status_code == 403
    assert anon.post("/api/priorities/partner/creative", json={}, headers=bearer(key)).status_code == 403
    assert anon.patch("/api/priorities/partner/tickets/1", json={}, headers=bearer(key)).status_code == 403
    assert post_assign(anon, key).status_code == 201


# ------------------------------------------------------------------------------------------------------------ the read allow-list
ALLOWED_SAMPLE = ["/api/products", "/api/products/vita-bull/rollup", "/api/products/vita-bull/families", "/api/products/vita-bull/ads", "/api/ads/1000", "/api/campaigns", "/api/platforms/overview",
                  "/api/trends", "/api/tag-lookup?q=zz", "/api/landing-pages", "/api/competitors", "/api/priorities", "/api/priorities/manual/options", "/api/promotions"]
BLOCKED_SAMPLE = ["/api/projection/pacing/month", "/api/projection/pnl/table", "/api/projection/financing/cash", "/api/priorities/vendor-links", "/api/priorities/assignments", "/api/priorities/board",
                  "/api/priorities/launch/settings", "/api/priorities/vendor-capacity", "/api/projection/inventory-supply/po", "/api/inventory/history", "/api/amazon/fees", "/api/amazon/overview", "/api/email/summary",
                  "/api/brain/key", "/api/status", "/api/daily-story", "/api/trends/plan", "/api/priorities/views", "/api/priorities/manual", "/api/priorities.csv", "/api/source/tieout"]


@pytest.mark.parametrize("url", ALLOWED_SAMPLE)
def test_a_partner_can_read_the_creative_and_performance_data(anon, pk, url, snaps):
    r = anon.get(url, headers=bearer(pk))
    assert r.status_code == 200, (url, r.status_code, r.get_data(as_text=True)[:200])


def test_a_partner_can_read_media(anon, pk):
    assert anon.get("/media/1", headers=bearer(pk)).status_code not in (401, 403)


@pytest.mark.parametrize("url", BLOCKED_SAMPLE)
def test_a_partner_cannot_read_anything_else(anon, pk, url, snaps):
    r = anon.get(url, headers=bearer(pk))
    assert r.status_code == 403 and r.get_json() == {"error": ak.PARTNER_MESSAGE}, (url, r.status_code)


def _fill(rule):
    import re
    return re.sub(r"<(?:int:|aint:|path:)?[a-z_]+>", lambda m: "1" if m.group(0).startswith(("<int", "<aint")) else "x", rule)


def test_every_get_route_is_classified(app):
    """A new /api/ or /media/ GET route must be put on PARTNER_READ_RULES (a partner may read it) or PARTNER_READ_BLOCKED (it may not) before the build passes: no route reaches a partner by default."""
    from app import auth
    partner_gets = {r for m, r in ak.PARTNER_ROUTES if m == "GET"}
    rules = {r.rule for r in app.url_map.iter_rules() if "GET" in r.methods and r.rule.startswith(auth.PROTECTED_PREFIXES)}
    both = ak.PARTNER_READ_RULES & ak.PARTNER_READ_BLOCKED
    assert not both, both
    rules = {r for r in rules if not r.startswith(ak.PARTNER_READ_BLOCKED_PREFIXES)}
    unclassified = sorted(rules - ak.PARTNER_READ_RULES - ak.PARTNER_READ_BLOCKED - partner_gets - auth.PUBLIC_API)
    assert not unclassified, f"classify these GET routes in app/api_keys.py (PARTNER_READ_RULES or PARTNER_READ_BLOCKED): {unclassified}"
    stale = sorted((ak.PARTNER_READ_RULES | ak.PARTNER_READ_BLOCKED) - rules)
    assert not stale, f"no such GET route any more: {stale}"


def test_every_other_route_and_write_is_refused_for_a_partner(app, anon, pk, snaps):
    from app import auth
    n = 0
    for r in app.url_map.iter_rules():
        extra = set(r.methods) - {"GET", "HEAD", "OPTIONS"}
        if not r.rule.startswith(auth.PROTECTED_PREFIXES) or r.rule in auth.PUBLIC_API or auth.PUBLIC_API_RE.fullmatch(_fill(r.rule)):
            continue
        for m in sorted(extra):
            if (m, r.rule) in ak.PARTNER_ROUTES:
                continue
            resp = anon.open(_fill(r.rule), method=m, json={}, headers=bearer(pk))
            assert resp.status_code == 403 and resp.get_json() == {"error": ak.PARTNER_MESSAGE}, (m, r.rule, resp.status_code)
            n += 1
    assert n >= 20


def test_partner_routes_are_exactly_these(app):
    assert ak.PARTNER_ROUTES == {("POST", "/api/priorities/tickets"), ("GET", "/api/priorities/partner/tickets"), ("PATCH", "/api/priorities/partner/tickets/<int:tid>"),
                                 ("POST", "/api/priorities/partner/tickets/<int:tid>/creative"), ("POST", "/api/priorities/partner/creative"), ("GET", "/api/priorities/partner/contract"),
                                 ("GET", "/api/priorities/partner/all-tickets"), ("GET", "/api/priorities/partner/all-tickets/<int:tid>")}
    have = {(m, r.rule) for r in app.url_map.iter_rules() for m in r.methods if r.rule.startswith("/api/priorities/partner/")}
    assert {x for x in ak.PARTNER_ROUTES if "/partner/" in x[1]} <= have


# ------------------------------------------------------------------------------------------------------------ every vendor's tickets: read only
def test_a_partner_reads_other_vendors_tickets_in_full_but_cannot_change_them(anon, pk, pkey, client, pg, snaps):
    theirs = pkey("partner-flow", "Studio Flow")
    t = anon.post("/api/priorities/tickets", json=ticket(concept_name="Flow concept", brief="Secret brief text", note="internal note", variations=[{"label": "Original", "url": "https://frame.io/x/abc", "concept_id": "SF-1"}], product="beef-liver"),
                  headers=bearer(theirs)).get_json()
    mine = create(anon, pk).get_json()
    r = anon.get("/api/priorities/partner/all-tickets", headers=bearer(pk))
    assert r.status_code == 200
    body = r.get_json()
    assert {x["id"] for x in body["tickets"]} == {t["id"], mine["id"]} and body["count"] == 2
    flow = next(x for x in body["tickets"] if x["id"] == t["id"])
    assert flow["vendor"] == "Studio Flow" and flow["note"] == "internal note" and flow["variations"][0]["url"] == "https://frame.io/x/abc" and "history" in flow and "comment_count" in flow
    assert "created_by" not in flow and "updated_by" not in flow
    assert all(e["actor"] != "key:partner-flow" for e in flow["history"]) and "api" in {e["actor"] for e in flow["history"]}
    text = r.get_data(as_text=True)
    assert theirs not in text and pk not in text and "lvk_" not in text and "key_hash" not in text and "token" not in text.lower().replace("tokens", "")
    one = anon.get(f"/api/priorities/partner/all-tickets/{t['id']}", headers=bearer(pk))
    assert one.status_code == 200 and set(one.get_json()) == {"ticket", "comments", "transcripts"} and one.get_json()["ticket"]["id"] == t["id"]
    assert anon.get("/api/priorities/partner/all-tickets/99999999", headers=bearer(pk)).status_code == 404
    # filters
    assert [x["id"] for x in anon.get("/api/priorities/partner/all-tickets?vendor=Studio%20Flow", headers=bearer(pk)).get_json()["tickets"]] == [t["id"]]
    assert [x["id"] for x in anon.get("/api/priorities/partner/all-tickets?product=boneflex", headers=bearer(pk)).get_json()["tickets"]] == [mine["id"]]
    assert anon.get("/api/priorities/partner/all-tickets?vendor=Nobody", headers=bearer(pk)).status_code == 400
    assert anon.get("/api/priorities/partner/all-tickets?status=assigned&limit=1", headers=bearer(pk)).get_json()["count"] == 2
    # and it stays read only: another vendor's ticket is a 404 on every write route
    assert anon.patch(f"/api/priorities/partner/tickets/{t['id']}", json={"concept_name": "hijack"}, headers=bearer(pk)).status_code == 404
    assert anon.post(f"/api/priorities/partner/tickets/{t['id']}/creative", json={"creative_url": "https://vimeo.com/1234567"}, headers=bearer(pk)).status_code == 404
    assert anon.post("/api/priorities/partner/creative", json={"creatives": [{"ticket_id": t["id"], "creative_url": "https://vimeo.com/1234567"}]}, headers=bearer(pk)).status_code == 404
    assert anon.post(f"/api/priorities/assignments/{t['id']}/comments", json={"body": "hi"}, headers=bearer(pk)).status_code == 403
    assert pg.execute("SELECT concept_name FROM creative_priorities_assignments WHERE id = %s", (t["id"],)).fetchone()[0] == "Flow concept"
    # the key's own list still shows only its own tickets
    assert [x["id"] for x in anon.get("/api/priorities/partner/tickets", headers=bearer(pk)).get_json()["tickets"]] == [mine["id"]]
    # dropped tickets are excluded
    client.patch(f"/api/priorities/assignments/{t['id']}", json={"dropped": True})
    assert anon.get("/api/priorities/partner/all-tickets", headers=bearer(pk)).get_json()["count"] == 1
    assert anon.get(f"/api/priorities/partner/all-tickets/{t['id']}", headers=bearer(pk)).status_code == 404


def test_all_tickets_is_for_partner_keys_only(anon, key, client, snaps):
    assert anon.get("/api/priorities/partner/all-tickets", headers=bearer(key)).status_code == 403
    assert client.get("/api/priorities/partner/all-tickets").status_code == 403
