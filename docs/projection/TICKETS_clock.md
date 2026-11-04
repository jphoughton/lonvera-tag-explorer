# Tickets for module owners: use the Pacific clock

Filed by proj-core. `app/projection/clock.py` has `today_pt()`: today's date in Pacific time (the business's day), honoring `PROJECTION_TODAY=YYYY-MM-DD` for tests and demos. `engine.run` and `freshness` use it already. A module that calls `date.today()` runs on server time (UTC), which is one day ahead of Pacific from 16:00 to 24:00, so pages disagree about which month is current and a month closes eight hours early on its last day.

Replace each with `from app.projection.clock import today_pt` (same return type, a `date`):

| owner | where | change |
|---|---|---|
| inventory_supply | `loaders.py:22`, `freshness.py:27` | `date.today()` -> `today_pt()` |
| pnl | `store.py:75`, `model.py:274`, `views.py:164` | `date.today()` -> `today_pt()` (decides which months are closed) |
| any module | anything else that calls `date.today()` or `datetime.now()` for a business date | same |

Test pattern: set `PROJECTION_TODAY` with `monkeypatch.setenv` instead of patching `date`.
