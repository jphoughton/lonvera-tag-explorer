# Requirements requests (append-only)

- [Agent A / ETL] `tzdata` — `etl/meta_pull.py` uses `zoneinfo.ZoneInfo("America/Los_Angeles")` to pick "yesterday" (the last complete Pacific day). `python:3.12-slim` usually ships system tzdata, but if the image does not, ZoneInfo raises; add `tzdata` to requirements.txt to be safe.
- [Agent A / ETL] Already in requirements.txt and used by etl/: `psycopg[binary]`, `requests`, `pillow` (asset resize), `google-api-python-client` + `google-auth` (live sheet reads), `pytest` (dev).
