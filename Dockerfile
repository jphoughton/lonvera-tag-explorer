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

# Non-root runtime user.
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin app

COPY --chown=app:app . .
RUN chmod +x scripts/deploy/*.sh

USER app
EXPOSE 8080

# Railway ignores Docker HEALTHCHECK; its healthcheck is railway.json -> deploy.healthcheckPath (/healthz).
CMD ["scripts/deploy/start_web.sh"]
