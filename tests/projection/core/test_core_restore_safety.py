"""Restore never drops what was seeded outside the audited path: cells and params unknown to the snapshot stay unless an audited change created them."""
from __future__ import annotations

from core_helpers import P, create, get_series, item, put, scenario


def seed(pg, slug, cells):
    sid = pg.execute("SELECT id FROM proj_scenarios WHERE slug = %s", (slug,)).fetchone()["id"]
    for key, month, value in cells:
        pg.execute("INSERT INTO proj_series (scenario_id, line_key, month, value, kind, note) VALUES (%s,%s,%s,%s,'input','seeded')", (sid, key, month, value))


def series_of(client, slug, *keys):
    return get_series(client, slug, ",".join(keys))["series"]


def restore(client, slug, version):
    r = client.post(f"{P}/scenarios/{slug}/restore", json={"version": version})
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()


def test_seeded_then_edited_then_restore_round_trip(client, pg):
    seed(pg, "current", [("seed.a", "2026-11-01", 10), ("seed.a", "2026-12-01", 20), ("seed.b", "2026-11-01", 5)])        # written by a loader, no audit, no version
    put(client, "current", item(key="seed.a", value=11))                                  # v2: first audited write captures the seeded state as the baseline
    put(client, "current", item(key="new.n", value=99))                                   # v3: a cell created through the audited path
    put(client, "current", item(key="seed.b", value=None))                                # v4: a seeded cell deleted
    assert series_of(client, "current", "seed.a", "seed.b", "new.n") == {"seed.a": {"2026-11-01": 11.0, "2026-12-01": 20.0}, "seed.b": {}, "new.n": {"2026-11-01": 99.0}}
    out = restore(client, "current", 1)
    assert series_of(client, "current", "seed.a", "seed.b", "new.n") == {"seed.a": {"2026-11-01": 10.0, "2026-12-01": 20.0}, "seed.b": {"2026-11-01": 5.0}, "new.n": {}}
    assert out["scenario"]["version"] == 5 and out["restore"] == {"cells_changed": 3, "kept": 0}
    restore(client, "current", 4)                                                         # and the restore itself can be undone
    assert series_of(client, "current", "seed.a", "seed.b", "new.n") == {"seed.a": {"2026-11-01": 11.0, "2026-12-01": 20.0}, "seed.b": {}, "new.n": {"2026-11-01": 99.0}}


def test_cells_seeded_after_a_snapshot_survive_a_restore_to_it(client, pg):
    create(client, "Plan")                                                                # v1 snapshot: empty
    put(client, "plan", item(key="x.x", value=1))                                         # v2
    seed(pg, "plan", [("late.seed", "2026-11-01", 7), ("late.seed", "2026-12-01", 8)])    # after v2: v1's snapshot can never contain these
    put(client, "plan", item(key="y.y", value=2))                                         # v3
    out = restore(client, "plan", 1)
    s = series_of(client, "plan", "x.x", "y.y", "late.seed")
    assert s == {"x.x": {}, "y.y": {}, "late.seed": {"2026-11-01": 7.0, "2026-12-01": 8.0}}       # audited cells removed, seeded cells kept
    assert out["restore"] == {"cells_changed": 2, "kept": 2}


def test_a_cell_seeded_after_the_snapshot_and_then_edited_keeps_its_current_value(client, pg):
    create(client, "Plan")
    put(client, "plan", item(key="x.x", value=1))                                         # v2
    seed(pg, "plan", [("late.seed", "2026-11-01", 7)])
    put(client, "plan", item(key="late.seed", value=70))                                  # v3: audited UPDATE of a cell the old snapshots never held
    restore(client, "plan", 1)
    assert series_of(client, "plan", "x.x", "late.seed") == {"x.x": {}, "late.seed": {"2026-11-01": 70.0}}


def test_a_cell_created_deleted_and_recreated_through_the_audited_path_is_removed(client):
    create(client, "Plan")
    put(client, "plan", item(key="x.x", value=1))                                         # v2 set
    put(client, "plan", item(key="x.x", value=None))                                      # v3 delete
    put(client, "plan", item(key="x.x", value=3))                                         # v4 set again
    restore(client, "plan", 1)
    assert series_of(client, "plan", "x.x") == {"x.x": {}}


def test_params_seeded_outside_the_audited_path_survive_a_restore(client, pg):
    create(client, "Plan")                                                                # v1: params {}
    client.patch(f"{P}/scenarios/plan", json={"params": {"a": {"x": 1}}})                 # v2
    pg.execute("UPDATE proj_scenarios SET params = params || '{\"seeded\": {\"k\": 5}}'::jsonb WHERE slug = 'plan'")      # a loader adds a namespace
    client.patch(f"{P}/scenarios/plan", json={"params": {"a": {"y": 2}}})                 # v3
    restore(client, "plan", 1)
    assert scenario(client, "plan")["params"] == {"seeded": {"k": 5}}                     # a.x and a.y were audited changes: gone, and the empty namespace is pruned


def test_params_changed_after_the_snapshot_revert_key_by_key(client):
    client.patch(f"{P}/scenarios/current", json={"params": {"drivers": {"a": 1, "b": 2}, "pnl": {"r": 0.5}}})       # v2
    client.patch(f"{P}/scenarios/current", json={"params": {"drivers": {"a": 10, "c": 3}, "pnl": None}})            # v3
    restore(client, "current", 2)
    assert scenario(client, "current")["params"] == {"drivers": {"a": 1, "b": 2}, "pnl": {"r": 0.5}}
    restore(client, "current", 1)
    assert scenario(client, "current")["params"] == {}


def test_baseline_follows_loader_writes_made_before_the_first_audited_change(client, pg):
    """The snapshot of the current version is refreshed from the live state at the next audited write, so a restore brings back loader data too."""
    put(client, "current", item(key="a.a", value=1))                                      # v2
    seed(pg, "current", [("loader.l", "2026-11-01", 4)])                                  # loader write after v2
    put(client, "current", item(key="a.a", value=2))                                      # v3: v2's snapshot is refreshed first
    put(client, "current", item(key="loader.l", value=None))                              # v4: the loader cell deleted through the API
    restore(client, "current", 2)
    assert series_of(client, "current", "a.a", "loader.l") == {"a.a": {"2026-11-01": 1.0}, "loader.l": {"2026-11-01": 4.0}}


def test_cloning_copies_seeded_cells_and_restore_in_the_clone_keeps_them(client, pg):
    seed(pg, "current", [("seed.a", "2026-11-01", 10)])
    c = create(client, "Copy")
    assert series_of(client, c["slug"], "seed.a") == {"seed.a": {"2026-11-01": 10.0}}
    put(client, c["slug"], item(key="seed.a", value=12))
    restore(client, c["slug"], 1)
    assert series_of(client, c["slug"], "seed.a") == {"seed.a": {"2026-11-01": 10.0}}
