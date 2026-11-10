# Nightly database backup

Last verified: 2026-11-09 (dump, restore and count check run against production data locally; Drive upload / re-download / prune run against a test folder; the Railway service itself is NOT created or deployed yet).

## What it does

A Railway cron service `backup` runs `python -m etl.backup` at 08:30 UTC every day (before `etl` at 13:00 and `feeds` at 21:00):

1. `pg_dump -Fc` of the production database (custom format, compressed, no owners / grants). Read-only: one consistent snapshot, nothing is written to production.
2. Uploads the dump to Google Drive, Shared Drive **Lonvera DB Backups**, folder `lonvera-tag-explorer`, as `lonvera-tag-explorer_YYYYMMDDTHHMMSSZ.dump` (resumable upload). Checks Drive's own size and md5 against the local file, then **downloads the file again** and checks it is byte-identical.
3. Restores the downloaded copy with `pg_restore` into database `backup_verify_<date>` on a **scratch Postgres 18 started inside the container** (initdb in a temp dir, listens on 127.0.0.1 only). The production server is never used for the restore test.
4. Compares exact row counts of 15 key tables (`ad_metrics`, `ad_tags`, `etl_runs`, `ad_daily`, `ad_daily_upstack`, `meta_ads`, `tracker_rows`, `creative_assets`, `app_meta`, `family_daily`, `upstack_attr_daily`, `meta_ad_lifetime`, `creative_stage_moves`, `family_content`, `meta_family_map`) and the list of tables with the live database. The dump is one snapshot taken between two live counts, so a restored count must sit inside the live before / after window plus 2% (minimum 5 rows). Then drops the scratch database and server.
5. Only if all of the above passed: deletes dumps in the folder that are at least 7 days old, **always keeping the newest 3 good dumps**. Only files named `lonvera-tag-explorer_<timestamp>.dump` are ever deleted (plus `UNVERIFIED-` copies older than 7 days); other files in the folder are never touched.

A failed step fails the run: `etl_runs` row `job='backup'`, `status='failed'` with the error, the Railway deployment shows Failed, exit code 1, nothing is pruned. A dump that fails the restore check is renamed `UNVERIFIED-<name>` in Drive so it never counts as a good backup. The alerts job (`etl/alerts.py`) flags a failed backup or no success in 36 hours.

`etl_runs.stats` of a good run: `file`, `size_bytes`, `dump_seconds`, `upload_seconds`, `verify_seconds`, `drive_file_id`, `drive_roundtrip_ok`, `verified`, `tables_verified` (live / live_after / restored per table), `pruned` (names), `kept`, `server_major`, `pg_dump_major`.

The dump contains all business data. The container deletes its working copies at the end of every run (also on failure). Access to the Drive destination: Josh (`joshphoughton@gmail.com`) and the service account `kill-scale-sheet-writer@shopify-data-451316.iam.gserviceaccount.com` only; no link sharing.

## Measured on production (2026-11-09)

Database 716 MB (Postgres 18.6, 118 tables). Dump 469.5 MB (469,510,261 bytes) in 26 s. Restore into a scratch Postgres 18 plus count check 7 s. All 15 tables matched exactly. A 120 MB test upload to Drive took 8.6 s, so the nightly upload should take about 30-40 s.

## Command

```
python -m etl.backup [--dry-run] [--keep-days 7] [--no-verify] [--no-record]
```

- `--dry-run`: dump and restore-verify only. No upload, no prune, no `etl_runs` row. Prints `would_prune` when Drive is reachable.
- `--no-record`: full run without the `etl_runs` row.
- `--no-verify`: skips the restore check. Never use it for the scheduled run.
- Environment: `DATABASE_URL` (what to back up), `GOOGLE_SA_JSON`, `BACKUP_DRIVE_ID` (the Shared Drive id), optional `BACKUP_PG_BIN` (directory of `pg_dump`, `pg_restore`, `initdb`, `pg_ctl`; must be the server's major version or newer, the run refuses an older `pg_dump`), `BACKUP_TMPDIR`.

Local dry run against production (read-only), from a directory linked to the Railway project:

```
brew install postgresql@18      # once: the server is Postgres 18, pg_dump 16 refuses it
railway run -s etl sh -c 'DATABASE_URL="$DATABASE_PUBLIC_URL" BACKUP_PG_BIN=/opt/homebrew/opt/postgresql@18/bin PYTHONPATH=. python -m etl.backup --dry-run'
```

## Deploy (one time)

1. In Google Drive (the service account cannot create Shared Drives): create Shared Drive **Lonvera DB Backups**, add `kill-scale-sheet-writer@shopify-data-451316.iam.gserviceaccount.com` as **Content manager**. Keep it to you and that account. Copy the drive id from its URL (`drive.google.com/drive/folders/<id>`).
2. `python3 scripts/deploy/apply_backup_service.py --drive-id <id>` (dry run), then add `--apply`. Creates service `backup` (cron `30 8 * * *`, restart NEVER, own Dockerfile `scripts/deploy/backup/Dockerfile` = `postgres:18-bookworm` + Python, start command `scripts/deploy/run_backup.sh`); `DATABASE_URL` and `GOOGLE_SA_JSON` are references to the `etl` service, `BACKUP_DRIVE_ID` is a plain variable.
3. From the repo root: `railway up -s backup --ci`. The Dockerfile path is a service setting, the build context is the repo root.
4. Run it once by hand (Railway dashboard > backup > Deployments > Redeploy, or wait for 08:30 UTC) and check the log, the `etl_runs` row and the file in Drive.

## Restore (step by step)

Use a **scratch** database first, never restore over production without deciding to.

1. Download the newest good `lonvera-tag-explorer_*.dump` from Drive (Shared Drive Lonvera DB Backups > lonvera-tag-explorer). Ignore `UNVERIFIED-` files.
2. Postgres 18 client tools are required (`brew install postgresql@18`; `export PATH=/opt/homebrew/opt/postgresql@18/bin:$PATH`).
3. Restore to a local scratch database to inspect:
   ```
   createdb restore_check
   pg_restore --no-owner --no-acl -j 4 -d restore_check lonvera-tag-explorer_YYYYMMDDTHHMMSSZ.dump
   psql restore_check -c "select count(*) from ad_metrics"
   ```
4. To bring production back: create a new Railway Postgres (or empty the broken database), run the same `pg_restore` against its public URL (`--clean --if-exists` when restoring into a database that already has objects), then point `DATABASE_URL` on `web`, `etl`, `feeds`, `live`, `backup` at it. The app runs `python -m etl.migrate` on start, which is a no-op when the dump already holds every migration.
5. Delete the downloaded dump when done: it holds all business data.

## Add a second destination

`etl/backup.py` talks to Drive only through the `Drive` class (`ensure_folder`, `upload`, `download`, `list_files`, `rename`, `delete`). To add another destination (S3, B2, a second Shared Drive): write a class with those six methods, build both in `run()`, upload to each, require every upload to round-trip (`md5`), and prune each independently. The cheapest second copy that needs no code: a second Shared Drive owned by a different Google account with the same service account as Content manager, `BACKUP_DRIVE_ID` set per service, and a second `backup2` service built by `apply_backup_service.py` with a different `SERVICE` name and cron.

## Known limits

- The service account is Content manager on the Shared Drive, which Drive lets trash but not permanently delete: pruned dumps go to the Drive trash and are purged after 30 days (they leave the folder immediately).
- The scratch restore proves the dump restores and the key tables match. It does not compare every table or every row.
- The cron container needs about 2x the dump size plus the restored database in free disk (about 1.7 GB for today's data); it uses `BACKUP_TMPDIR=/scratch`.
