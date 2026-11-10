# Lonvera Tag Explorer: one image, two Railway services.
#   web  -> default CMD (scripts/deploy/start_web.sh: migrate, then gunicorn)
#   etl  -> start command override in railway.etl.json (python -m etl.run_daily), run on a cron schedule
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8080

WORKDIR /app

# Dependencies first so the layer is cached until requirements.txt changes.
COPY requirements.txt ./
RUN pip install -r requirements.txt

# Headless Chromium for etl.competitor_ads (public Meta Ad Library). Browsers live outside /root so the non-root runtime user can launch them.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN playwright install --with-deps chromium \
 && chmod -R a+rX /ms-playwright \
 && rm -rf /var/lib/apt/lists/*

# Node + the Claude Code CLI (pinned): etl.auto_tag shells out to `claude -p` (auth = CLAUDE_CODE_OAUTH_TOKEN env var on the etl service). Node is copied from the
# official node image (the python image keeps python in /usr/local, so only node + npm are taken, not the whole tree).
COPY --from=node:22-bookworm-slim /usr/local/bin/node /usr/local/bin/node
COPY --from=node:22-bookworm-slim /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && npm install -g @anthropic-ai/claude-code@2.1.321 \
 && npm cache clean --force \
 && claude --version

# Non-root runtime user.
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin app

COPY --chown=app:app . .
RUN chmod +x scripts/deploy/*.sh

USER app
EXPOSE 8080

# Railway ignores Docker HEALTHCHECK; its healthcheck is railway.json -> deploy.healthcheckPath (/healthz).
CMD ["scripts/deploy/start_web.sh"]
