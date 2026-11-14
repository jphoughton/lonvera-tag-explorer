"""partner_read_all keys (migration 265): a partner key (one vendor, partner writes only) that may also READ everything except the secret-bearing routes."""
import re

import pytest

from app import api as api_mod
from app import api_keys as ak
from etl import api_keys as cli

from .test_api_keys import bearer, fresh_throttle, key, mint, post_assign      # noqa: F401  (fixtures)
from .test_api_priorities_assign import MONDAY, clean, one, snaps              # noqa: F401
from .test_partner_keys import BRIEF, create, ticket                          # noqa: F401


@pytest.fixture()
def ra(pg):
    pg.execute("DELETE FROM api_keys")
    pg.commit()
    api_mod._partner_limiter = None
    made = {}

    def make(name="studio-flow", vendor="Studio Flow", scope="partner_read_all"):
        raw, _ = cli.create_key(pg, name, "pytest", scope, vendor)
        made[name] = raw
        return raw

    yield make
    api_mod._partner_limiter = None
    pg.rollback()
    pg.execute("DELETE FROM api_keys")
    pg.commit()


READABLE = ["/api/projection/pacing/month", "/api/projection/pnl/table", "/api/projection/financing/cash", "/api/projection/inventory-supply/po", "/api/inventory/history", "/api/amazon/overview",
            "/api/amazon/fees", "/api/email/summary", "/api/daily-story", "/api/status", "/api/trends/plan", "/api/priorities/board", "/api/priorities/assignments", "/api/priorities/vendor-capacity",
            "/api/priorities/launch/settings", "/api/priorities.csv", "/api/priorities/manual", "/api/source/tieout", "/api/products", "/api/trends"]
SECRET = ["/api/brain/key", "/api/priorities/views", "/api/priorities/vendor-links"]


@pytest.mark.parametrize("url", READABLE)
def test_read_all_key_reads_the_rest_of_the_app(anon, ra, url, snaps):
    r = anon.get(url, headers=bearer(ra()))
    assert r.status_code not in (401, 403), (url, r.status_code, r.get_data(as_text=True)[:200])
    assert r.status_code in (200, 400, 404, 503), (url, r.status_code)          # 404 / 503: that module has no data in the fixture (launch dark, ...), never an auth refusal


@pytest.mark.parametrize("url", ["/api/projection/pacing/month", "/api/projection/pnl/table", "/api/amazon/overview", "/api/email/summary", "/api/priorities/board", "/api/priorities/assignments"])
def test_the_main_families_answer_200(anon, ra, url, snaps):
    assert anon.get(url, headers=bearer(ra())).status_code == 200


def test_partner_push_is_still_refused_on_those(anon, ra, snaps):
    k = ra("pusher", "Madre", "partner_push")
    for url in ("/api/projection/pacing/month", "/api/amazon/overview", "/api/email/summary", "/api/priorities/board", "/api/priorities/assignments"):
        r = anon.get(url, headers=bearer(k))
        assert r.status_code == 403 and r.get_json() == {"error": ak.PARTNER_MESSAGE}, url


@pytest.mark.parametrize("url", SECRET)
def test_secret_bearing_routes_stay_blocked_for_every_partner_scope(anon, ra, url, snaps):
    for scope in ("partner_read_all", "partner_push"):
        r = anon.get(url, headers=bearer(ra(f"k-{scope}", "Madre", scope)))
        assert r.status_code == 403 and r.get_json() == {"error": ak.PARTNER_MESSAGE}, (scope, url)


def test_no_secret_in_what_a_read_all_key_can_read(anon, ra, client, snaps):
    one(client, vendor="Victoria")
    tok = client.post("/api/priorities/vendor-links", json={"vendor": "Victoria"}).get_json()["token"]
    k = ra()
    for url in ("/api/priorities/assignments", "/api/priorities/board", "/api/priorities", "/api/status", "/api/priorities/partner/all-tickets"):
        text = anon.get(url, headers=bearer(k)).get_data(as_text=True)
        assert tok not in text and "lvk_" not in text and "key_hash" not in text, url


def test_writes_are_identical_to_partner_push(anon, ra, client, pg, snaps):
    k = ra()
    theirs = one(client, vendor="Madre")
    assert create(anon, k).status_code == 201 and create(anon, k, vendor="Madre").status_code == 403
    assert create(anon, k).get_json()["vendor"] == "Studio Flow"
    assert anon.patch(f"/api/priorities/partner/tickets/{theirs['id']}", json={"concept_name": "x"}, headers=bearer(k)).status_code == 404
    assert anon.post(f"/api/priorities/partner/tickets/{theirs['id']}/creative", json={"creative_url": "https://vimeo.com/1234567"}, headers=bearer(k)).status_code == 404
    bad = [(m, r) for m, r in _write_rules_all(anon.application) if (m, r) not in ak.PARTNER_ROUTES]
    assert len(bad) >= 15
    for m, rule in bad:
        resp = anon.open(_fill(rule), method=m, json={}, headers=bearer(k))
        assert resp.status_code == 403 and resp.get_json() == {"error": ak.PARTNER_MESSAGE}, (m, rule, resp.status_code)


def _fill(rule):
    return re.sub(r"<(?:int:|aint:|path:)?[a-z_]+>", lambda m: "1" if m.group(0).startswith(("<int", "<aint")) else "x", rule)


def _write_rules_all(app):
    from app import auth
    out = []
    for r in app.url_map.iter_rules():
        extra = set(r.methods) - {"GET", "HEAD", "OPTIONS"}
        if extra and r.rule.startswith(auth.PROTECTED_PREFIXES) and r.rule not in auth.PUBLIC_API and not auth.PUBLIC_API_RE.fullmatch(_fill(r.rule)):
            out += [(m, r.rule) for m in sorted(extra)]
    return out


def test_every_get_route_is_classified_for_read_all(app):
    """Each GET route is either readable by a partner_read_all key or on SECRET_BLOCKED, and everything secret is also blocked for partner_push."""
    from app import auth
    rules = {r.rule for r in app.url_map.iter_rules() if "GET" in r.methods and r.rule.startswith(auth.PROTECTED_PREFIXES)}
    assert ak.SECRET_BLOCKED.isdisjoint(ak.PARTNER_READ_RULES)
    for rule in rules - ak.SECRET_BLOCKED:
        assert rule in ak.PARTNER_READ_RULES or rule in ak.PARTNER_READ_BLOCKED or rule.startswith(ak.PARTNER_READ_BLOCKED_PREFIXES) or ("GET", rule) in ak.PARTNER_ROUTES, rule
    for rule in ak.SECRET_BLOCKED:
        assert rule in ak.PARTNER_READ_BLOCKED or rule.startswith(ak.PARTNER_READ_BLOCKED_PREFIXES), rule


def test_set_scope_upgrades_a_key_in_place_and_takes_effect_at_once(anon, ra, pg, snaps):
    k = ra("studio-flow", "Studio Flow", "partner_push")
    assert anon.get("/api/amazon/overview", headers=bearer(k)).status_code == 403
    before = pg.execute("SELECT key_hash, prefix, vendor FROM api_keys WHERE name = 'studio-flow'").fetchone()
    assert cli.set_scope(pg, "studio-flow", "partner_read_all") is True
    assert cli.set_scope(pg, "studio-flow", "partner_read_all") is False
    assert tuple(pg.execute("SELECT key_hash, prefix, vendor FROM api_keys WHERE name = 'studio-flow'").fetchone()) == tuple(before)
    assert anon.get("/api/amazon/overview", headers=bearer(k)).status_code == 200
    assert cli.set_scope(pg, "studio-flow", "partner_push") is True
    assert anon.get("/api/amazon/overview", headers=bearer(k)).status_code == 403


def test_set_scope_refuses_bad_targets(pg, ra, key):
    ra("p", "Madre", "partner_push")
    with pytest.raises(cli.CliError):
        cli.set_scope(pg, "p", "read_assign")
    with pytest.raises(cli.CliError):
        cli.set_scope(pg, "nope", "partner_read_all")
    with pytest.raises(cli.CliError):
        cli.set_scope(pg, "ci-colleague", "partner_read_all")                       # a read_assign key is not a partner key
    cli.revoke_key(pg, "p")
    with pytest.raises(cli.CliError):
        cli.set_scope(pg, "p", "partner_read_all")


def test_create_read_all_needs_a_vendor_and_list_shows_the_scope(pg, ra):
    with pytest.raises(cli.CliError):
        cli.create_key(pg, "x", "t", "partner_read_all", None)
    ra("ra1", "Studio Flow", "partner_read_all")
    assert ("partner_read_all", "Studio Flow") in [r[2:4] for r in cli.list_keys(pg)]
    import psycopg
    with pytest.raises(psycopg.errors.CheckViolation):
        pg.execute("INSERT INTO api_keys (name, key_hash, prefix, scope) VALUES ('y', 'h', 'lvk_aaaa', 'partner_read_all')")
    pg.rollback()


def test_read_all_key_never_logs_in_and_cannot_read_a_vendor_page_token_list(anon, ra, snaps):
    k = ra()
    r = anon.post("/api/login", json={}, headers=bearer(k))
    assert r.status_code == 401 and r.headers.get("Set-Cookie") is None
