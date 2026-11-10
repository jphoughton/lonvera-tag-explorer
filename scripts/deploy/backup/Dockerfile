# `backup` service image: the official Postgres 18 image (matches the production server, so pg_dump / pg_restore / initdb are the right version and the scratch
# restore server is real Postgres 18) plus a small Python venv for etl.backup. Built from the REPO ROOT (build context = repo root), Dockerfile path
# scripts/deploy/backup/Dockerfile; the shared root Dockerfile (Chromium, Node, Claude CLI) is not used. Runs as the non-root `postgres` user (initdb refuses root).
FROM postgres:18-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 BACKUP_PG_BIN=/usr/lib/postgresql/18/bin

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-venv python3-pip tzdata \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY scripts/deploy/backup/requirements.txt ./requirements.txt
RUN python3 -m venv /opt/venv && /opt/venv/bin/pip install -r requirements.txt
ENV PATH="/opt/venv/bin:${PATH}"

COPY --chown=postgres:postgres etl ./etl
COPY --chown=postgres:postgres scripts/deploy/run_backup.sh ./scripts/deploy/run_backup.sh
RUN chmod +x scripts/deploy/run_backup.sh && mkdir -p /scratch && chown postgres:postgres /scratch
ENV BACKUP_TMPDIR=/scratch

USER postgres
ENTRYPOINT []
CMD ["scripts/deploy/run_backup.sh"]
