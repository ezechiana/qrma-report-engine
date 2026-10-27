"""Read-only schema/configuration readiness for the connector release."""
import os
from sqlalchemy import text
from app.services import connector_policy as policy

MIGRATIONS = {
    '20261026_assessment_fingerprint.sql',
    '20261026_connector_v04.sql',
    '20261027_connector_v050_observability.sql',
    '20261027_connector_v050_rate_limits.sql',
}
COLUMNS = {
    ('cases', 'assessment_fingerprint'),
    ('connector_devices', 'last_seen_at'), ('connector_devices', 'last_heartbeat_at'),
    ('connector_devices', 'last_ip_address'), ('connector_devices', 'connector_version'),
    ('connector_devices', 'platform'),
    ('audit_logs', 'connector_id'), ('audit_logs', 'job_id'),
    ('audit_logs', 'assessment_id'), ('audit_logs', 'actor_type'),
    ('audit_logs', 'outcome'), ('audit_logs', 'request_id'),
    ('audit_logs', 'ip_address'), ('audit_logs', 'event_key'),
    ('connector_rate_limits', 'bucket_key'), ('connector_rate_limits', 'hits'),
    ('connector_rate_limits', 'reset_at'),
}
INDEXES = {
    'ix_cases_user_assessment_fingerprint', 'uq_connector_job',
    'ix_connector_devices_last_seen_at', 'uq_audit_logs_event_key',
    'ix_audit_logs_connector_id', 'ix_audit_logs_job_id', 'ix_connector_audit_time',
    'ix_connector_assessments_device_received', 'ix_connector_rate_limits_reset_at',
}


def ready(engine):
    if not policy.release_environment():
        return True
    environment = os.getenv('APP_ENV', '').lower()
    if engine.dialect.name != 'postgresql' or engine.url.host != policy.RELEASES[environment][1]:
        return False
    if os.getenv('AUTO_CREATE_TABLES', 'true').lower() != 'false':
        return False
    if len(os.getenv('JWT_SECRET_KEY', '')) < 32:
        return False
    if os.getenv('GO360_CONNECTOR_ENABLED') == '1' and not policy.enabled():
        return False
    try:
        with engine.connect() as connection:
            connection.execute(text("SET LOCAL statement_timeout = '3s'"))
            versions = set(connection.execute(text('SELECT version FROM public.schema_migrations')).scalars())
            columns = set(connection.execute(text("""SELECT table_name,column_name
                FROM information_schema.columns WHERE table_schema='public'""")))
            indexes = set(connection.execute(text("""SELECT indexname FROM pg_indexes
                WHERE schemaname='public'""")).scalars())
            return MIGRATIONS <= versions and COLUMNS <= columns and INDEXES <= indexes
    except Exception:
        return False
