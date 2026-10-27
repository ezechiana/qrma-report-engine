"""Release policy and HTTP security checks; no external services or live data."""
import os
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from fastapi import HTTPException, Response
from starlette.requests import Request
from app.services import connector_policy as policy
from app.services import connector_limits as limits


@pytest.fixture
def release(monkeypatch):
    values = {
        'APP_ENV': 'staging',
        'RAILWAY_ENVIRONMENT_NAME': 'staging',
        'GO360_CONNECTOR_ENABLED': '1',
        'GO360_CONNECTOR_RELEASE_ENV': 'staging',
        'GO360_CONNECTOR_TELEMETRY_ENABLED': '1',
        'GO360_CONNECTOR_LIMITER': 'postgresql',
        'GO360_CONNECTOR_ORIGIN': 'https://staging.go360.io',
        'DATABASE_URL': 'postgresql://synthetic:synthetic@postgres-ns3m.railway.internal/fixture',
        'JWT_SECRET_KEY': 'synthetic-signing-key-32-characters-qa',
        'GO360_CONNECTOR_RATE_LIMIT_KEY': 'synthetic-limiter-key-32-characters-qa',
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    return values


def test_explicit_release_and_telemetry_enabled_together(release):
    assert policy.enabled() and policy.telemetry_enabled()


@pytest.mark.parametrize('name,value', [
    ('GO360_CONNECTOR_ENABLED', ''),
    ('GO360_CONNECTOR_RELEASE_ENV', ''),
    ('GO360_CONNECTOR_RELEASE_ENV', 'production'),
    ('GO360_CONNECTOR_TELEMETRY_ENABLED', '0'),
    ('GO360_CONNECTOR_LIMITER', 'memory'),
    ('GO360_CONNECTOR_ORIGIN', 'https://app.go360.io'),
    ('GO360_CONNECTOR_ORIGIN', 'https://staging.go360.io.attacker.invalid'),
    ('GO360_CONNECTOR_ORIGIN', 'http://staging.go360.io'),
    ('GO360_CONNECTOR_ORIGIN', 'https://staging.go360.io/path'),
    ('DATABASE_URL', 'postgresql://synthetic:synthetic@postgres.railway.internal/fixture'),
    ('DATABASE_URL', 'postgresql://['),
    ('JWT_SECRET_KEY', 'change-me-in-production'),
    ('JWT_SECRET_KEY', 'short'),
    ('GO360_CONNECTOR_RATE_LIMIT_KEY', ''),
    ('GO360_CONNECTOR_RATE_LIMIT_KEY', 'short'),
    ('RAILWAY_ENVIRONMENT_NAME', 'production'),
    ('APP_ENV', 'unknown'),
])
def test_release_misconfiguration_disables_routes_and_audit(release, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    assert not policy.enabled()
    assert not policy.telemetry_enabled()


def test_changing_app_env_to_dev_cannot_bypass_railway_environment(release, monkeypatch):
    monkeypatch.setenv('APP_ENV', 'dev')
    assert not policy.enabled()


def test_production_requires_production_database_origin_and_opt_in(release, monkeypatch):
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setenv('RAILWAY_ENVIRONMENT_NAME', 'production')
    assert not policy.enabled()
    monkeypatch.setenv('GO360_CONNECTOR_RELEASE_ENV', 'production')
    monkeypatch.setenv('GO360_CONNECTOR_ORIGIN', 'https://app.go360.io')
    monkeypatch.setenv('DATABASE_URL', 'postgresql://synthetic:synthetic@postgres.railway.internal/fixture')
    assert policy.enabled()


def test_distinct_signing_and_limiter_keys_required(release, monkeypatch):
    monkeypatch.setenv('GO360_CONNECTOR_RATE_LIMIT_KEY', os.environ['JWT_SECRET_KEY'])
    assert not policy.enabled()


def test_gate_requires_https_and_canonical_host_before_using_limiter(release, monkeypatch):
    from app.api import routes_connector as routes
    limiter = Mock()
    monkeypatch.setattr(routes, 'require', limiter)
    def request(scheme='https', host='staging.go360.io'):
        return Request({'type':'http', 'scheme':scheme, 'method':'GET', 'path':'/api/connector-devices',
                        'root_path':'', 'query_string':b'', 'headers':[(b'host',host.encode())],
                        'server':(host,443), 'client':('192.0.2.1',1000)})
    for req, code in [(request('http'),400), (request(host='app.go360.io'),403)]:
        with pytest.raises(HTTPException) as exc:
            routes.enabled(req, Response())
        assert exc.value.status_code == code
    limiter.assert_not_called()
    routes.enabled(request(), Response())
    limiter.assert_called_once_with('request-all', '192.0.2.1', 240)


def test_release_rate_limit_never_uses_memory_fallback(release, monkeypatch):
    local = Mock(side_effect=AssertionError('Memory fallback used'))
    monkeypatch.setattr(limits, 'local_allow', local)
    monkeypatch.setattr(limits, 'shared_allow', Mock(side_effect=HTTPException(503)))
    with pytest.raises(HTTPException) as exc:
        limits.allow('pairing', '192.0.2.1', 8)
    assert exc.value.status_code == 503
    local.assert_not_called()


def test_database_unavailable_fails_closed_without_leaking_identity(release):
    broken = SimpleNamespace(dialect=SimpleNamespace(name='postgresql'),
                             begin=Mock(side_effect=RuntimeError('secret connection details')))
    with pytest.raises(HTTPException) as exc:
        limits.shared_allow('pairing', 'private-client', 8, engine=broken)
    assert exc.value.status_code == 503
    assert 'secret' not in str(exc.value.detail)


def test_quota_denial_returns_retry_after(release, monkeypatch):
    monkeypatch.setattr(limits, 'shared_allow', lambda *a: False)
    with pytest.raises(HTTPException) as exc:
        limits.require('pairing', '192.0.2.1', 8)
    assert exc.value.status_code == 429
    assert exc.value.headers['Retry-After'] == '60'


def test_dev_local_window_retains_sliding_semantics(monkeypatch):
    monkeypatch.setenv('APP_ENV','test')
    monkeypatch.delenv('RAILWAY_ENVIRONMENT_NAME', raising=False)
    limits._buckets.clear()
    assert limits.allow('qa-local', 'fixture', 1, clock=lambda:100)
    assert not limits.allow('qa-local', 'fixture', 1, clock=lambda:159)
    assert limits.allow('qa-local', 'fixture', 1, clock=lambda:160)

def test_limiter_uses_separate_bounded_tls_pool(release, monkeypatch):
    import sqlalchemy
    marker=object()
    factory=Mock(return_value=marker)
    monkeypatch.setattr(sqlalchemy,'create_engine',factory)
    limits.release_engine.cache_clear()
    try:
        assert limits.release_engine() is marker
        assert limits.release_engine() is marker
        factory.assert_called_once()
        kwargs=factory.call_args.kwargs
        assert kwargs['pool_size']==2 and kwargs['max_overflow']==2
        assert kwargs['pool_timeout']==2
        assert kwargs['connect_args']=={'sslmode':'require','connect_timeout':3}
    finally:
        limits.release_engine.cache_clear()
