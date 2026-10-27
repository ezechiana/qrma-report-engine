"""Default-off connector policy shared by registration, requests and auditing."""
import os
from urllib.parse import urlsplit

DEVELOPMENT = {'dev', 'development', 'local', 'test'}
RELEASES = {
    'staging': ('https://staging.go360.io', 'postgres-ns3m.railway.internal'),
    'production': ('https://app.go360.io', 'postgres.railway.internal'),
}


def release_environment():
    return os.getenv('APP_ENV', '').lower() in RELEASES


def enabled():
    environment = os.getenv('APP_ENV', '').lower()
    railway_environment = os.getenv('RAILWAY_ENVIRONMENT_NAME', '').lower()
    if railway_environment and railway_environment != environment:
        return False
    if os.getenv('GO360_CONNECTOR_ENABLED') != '1':
        return False
    if environment in DEVELOPMENT:
        return True
    if environment not in RELEASES:
        return False
    expected_origin, expected_database_host = RELEASES[environment]
    try:
        database_host = urlsplit(os.getenv('DATABASE_URL', '').replace('postgresql+psycopg:', 'postgresql:')).hostname
    except ValueError:
        return False
    signing_key = os.getenv('JWT_SECRET_KEY', '')
    limiter_key = os.getenv('GO360_CONNECTOR_RATE_LIMIT_KEY', '')
    return all((
        os.getenv('GO360_CONNECTOR_RELEASE_ENV') == environment,
        os.getenv('GO360_CONNECTOR_TELEMETRY_ENABLED') == '1',
        os.getenv('GO360_CONNECTOR_LIMITER') == 'postgresql',
        os.getenv('GO360_CONNECTOR_ORIGIN') == expected_origin,
        database_host == expected_database_host,
        len(signing_key) >= 32,
        signing_key != 'change-me-in-production',
        len(limiter_key) >= 32,
        limiter_key != signing_key,
    ))


def telemetry_enabled():
    return enabled() and os.getenv('GO360_CONNECTOR_TELEMETRY_ENABLED') == '1'
