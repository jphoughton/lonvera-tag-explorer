-- 197: de-duplication state of the failure / staleness alerts (python -m etl.alerts, Railway cron service `alerts`, hourly). One row per check id: when the problem was first seen,
-- when Slack was last told, when it cleared. Lets the job alert once, repeat every 24 hours while the problem lasts, and say "recovered". Additive and idempotent.
-- No secrets: last_message is plain text built by the checks (scrubbed), never a URL with a credential.
CREATE TABLE IF NOT EXISTS alert_state (
  check_id      text        PRIMARY KEY,
  first_seen    timestamptz NOT NULL,
  last_seen     timestamptz NOT NULL,
  last_alerted  timestamptz,
  resolved_at   timestamptz,
  alert_count   integer     NOT NULL DEFAULT 0,
  fingerprint   text,
  last_message  text
);

-- ---------------------------------------------------------------- comments
COMMENT ON TABLE public.alert_state IS 'De-duplication state of the failure and staleness alerts sent to Slack by etl.alerts: one row per check (a job that failed, a table that went stale, the web health check ...), so the job alerts once when a problem appears, repeats every 24 hours while it lasts, and sends one ''recovered'' line when it clears. Grain: one row per check id. Source: etl.alerts (hourly Railway cron service `alerts`). Refresh: hourly. Coverage: from the first run of the alerts service. Caveats: resolved_at is NULL while the problem is open; a problem that comes back reopens the same row (first_seen restarts, alert_count restarts). | last_alerted is NULL when the problem was seen but no message could be sent (for example SLACK_ALERT_WEBHOOK_URL is not set yet): the alert goes out on the first run after the webhook works.';
COMMENT ON COLUMN public.alert_state.check_id IS 'Stable id of the check, for example job:run_daily, fresh:meta, web:health (primary key).';
COMMENT ON COLUMN public.alert_state.first_seen IS 'When the current problem was first seen.';
COMMENT ON COLUMN public.alert_state.last_seen IS 'Last run that still saw the problem (or the run that saw it clear, when resolved).';
COMMENT ON COLUMN public.alert_state.last_alerted IS 'When Slack was last told about it; NULL if never delivered.';
COMMENT ON COLUMN public.alert_state.resolved_at IS 'When the problem cleared; NULL while open.';
COMMENT ON COLUMN public.alert_state.alert_count IS 'Messages sent for the current problem.';
COMMENT ON COLUMN public.alert_state.fingerprint IS 'Short text that changes when the problem changes shape (for example another feed started failing); a change re-alerts at once.';
COMMENT ON COLUMN public.alert_state.last_message IS 'Text of the last alert (what broke, since when, how to fix).';
