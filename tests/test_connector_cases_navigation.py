"""Exercise the shipped UI and connector route order without a live database."""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def application(monkeypatch):
    monkeypatch.setenv('APP_ENV', 'test')
    monkeypatch.setenv('GO360_CONNECTOR_ENABLED', '1')
    monkeypatch.setenv('AUTO_CREATE_TABLES', 'false')
    monkeypatch.setenv('S3_BUCKET', 'synthetic-test-bucket')
    monkeypatch.setenv('AWS_ACCESS_KEY_ID', 'synthetic')
    monkeypatch.setenv('AWS_SECRET_ACCESS_KEY', 'synthetic')
    monkeypatch.setenv('AWS_EC2_METADATA_DISABLED', 'true')
    monkeypatch.delenv('RAILWAY_ENVIRONMENT_NAME', raising=False)
    from app.main import create_app
    return create_app()


def test_cases_exposes_incoming_only_when_enabled(application, monkeypatch):
    client = TestClient(application, base_url='https://localhost')
    response = client.get('/app/cases')
    assert response.status_code == 200
    assert 'href="/app/cases/incoming"' in response.text
    monkeypatch.setenv('GO360_CONNECTOR_ENABLED', '0')
    assert 'href="/app/cases/incoming"' not in client.get('/app/cases').text


def test_incoming_route_precedes_case_detail_and_requires_auth(application, monkeypatch):
    from starlette.routing import Match
    matches = [route for route in application.routes if route.matches({
        'type': 'http', 'path': '/app/cases/incoming', 'method': 'GET',
    })[0] == Match.FULL]
    assert matches[0].endpoint.__name__ == 'incoming_page'
    client = TestClient(application, base_url='https://localhost')
    assert client.get('/app/cases/incoming', follow_redirects=False).status_code == 303
    assert client.get('/api/incoming-assessments').status_code == 401
    assert client.post('/api/connector/heartbeat', json={}).status_code == 404
    monkeypatch.setenv('GO360_CONNECTOR_TELEMETRY_ENABLED', '1')
    from app.api import routes_connector
    monkeypatch.setattr(routes_connector, 'authentication_denied', lambda *args: None)
    assert client.post('/api/connector/heartbeat', json={}).status_code == 401


def test_authenticated_incoming_renders_template(application):
    from app.api.routes_connector import browser_user
    application.dependency_overrides[browser_user] = lambda: SimpleNamespace(id='fixture')
    response = TestClient(application, base_url='https://localhost').get('/app/cases/incoming')
    assert response.status_code == 200
    assert '<h1>Incoming Assessments</h1>' in response.text
    assert '/api/incoming-assessments?offset=' in response.text
    assert '/api/connector-devices' in response.text
