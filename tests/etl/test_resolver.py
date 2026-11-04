from etl.resolver import Resolver, classify_stage, fam, norm, pick, pick_clicks, triple


def test_fam_strips_copy_suffixes():
    assert fam("cedAn18 - Copy") == "cedAn18"
    assert fam("Bull-526-Hook2 - Copy 2") == "Bull-526-Hook2"
    assert fam("X - Copy - Copy 3") == "X"
    assert fam("  spaced name  ") == "spaced name"
    assert fam("Copy of Thing") == "Copy of Thing"
    assert fam("Thing - Copy of it") == "Thing - Copy of it"
    assert fam(510) == "510"


def test_norm_and_triple():
    assert norm("Lonvera_VitalBull Video-7.3") == "lonvera-vitalbull-video-7.3"
    assert triple("Lonvera-VitalBull-Video-7.3") == ("bull", "video", "7.3")
    assert triple("Lonvera-BeefLiver-Static-3.2") == ("liver", "static", "3.2")
    assert triple("random name") is None


def rows():
    return [{"family": "Bull-526-Hook2", "concept": "Bull-526-Hook2", "adname": ""},
            {"family": "Lonvera-VitalBull-Video-7.3.2", "concept": "Lonvera-VitalBull-Video-7.3.2", "adname": "My Ad Name"},
            {"family": "Lonvera-VitalBull-Video-7.3", "concept": "Lonvera-VitalBull-Video-7.3", "adname": ""}]


def test_resolver_rules():
    r = Resolver(rows(), created={"lonvera-vitalbull-video-7.3 x": "2026-07-01"})
    assert r.resolve_rule("Bull-526-Hook2 - Copy 3") == ("Bull-526-Hook2", "exact")
    assert r.resolve_rule("bull-526-hook2_VID_Foo_Bar") == ("Bull-526-Hook2", "relaunch")
    assert r.resolve_rule("my ad name") == ("Lonvera-VitalBull-Video-7.3.2", "adname")
    assert r.resolve_rule("Lonvera-VitalBull-Video-7.3-hook9") == ("Lonvera-VitalBull-Video-7.3", "prefix")
    assert r.resolve("totally unknown") is None


def test_resolver_triple_needs_created_after_cutoff():
    base = [{"family": "Lonvera-VitalBull-Video-7.3", "concept": "Lonvera-VitalBull-Video-7.3", "adname": ""}]
    name = "VitalBull video 7.3 something new"
    assert Resolver(base, created={}).resolve(name) is None
    assert Resolver(base, created={name.lower(): "2026-05-01"}).resolve(name) is None          # before 2026-06-20 cutoff
    assert Resolver(base, created={name.lower(): "2026-07-15"}).resolve_rule(name) == ("Lonvera-VitalBull-Video-7.3", "triple")


def test_pick_and_stage():
    assert pick([{"action_type": "purchase", "value": "3"}, {"action_type": "omni_purchase", "value": "5"}]) == 5.0
    assert pick([{"action_type": "purchase", "value": "3"}]) == 3.0
    assert pick(None) == 0.0
    assert pick_clicks({"actions": [{"action_type": "link_click", "value": "7"}], "inline_link_clicks": "9"}) == 7.0
    assert pick_clicks({"inline_link_clicks": "9"}) == 9.0
    assert classify_stage("Boneflex TEST campaign") == "testing"
    assert classify_stage("Boneflex Scaling") == "scaling"
