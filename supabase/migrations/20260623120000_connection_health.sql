-- Connector health columns — backs the scheduled connector health monitor
-- (app/connector_health.py). The monitor re-validates every active connector's
-- stored OAuth/API token on an interval and persists the result here, so the
-- connectors UI can surface a "disconnected" connector proactively (instead of
-- only on-open) and we can email a healthy→disconnected transition alert.
--
--   health               'connected' | 'disconnected'; NULL = never checked
--   last_health_error    provider error / probe detail from the last UNHEALTHY check
--   last_health_check_at when the last health probe ran (any result)
--
-- These are independent of last_sync_at / last_sync_error (which track KG ingest,
-- not token validity).

alter table connections
    add column if not exists health                text,
    add column if not exists last_health_error     text,
    add column if not exists last_health_check_at   timestamptz;
