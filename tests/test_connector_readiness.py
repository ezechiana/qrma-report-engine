"""Read-only readiness decisions and release-runner target checks."""
from pathlib import Path
from types import SimpleNamespace
import hashlib
import json
import pytest
from app.services import connector_readiness as readiness
from app.db import connector_release_migrate as migration


class Result:
    def __init__(self, rows): self.rows=rows
    def scalars(self): return iter(self.rows)
    def __iter__(self): return iter(self.rows)


class Connection:
    def __init__(self, versions, columns, indexes):
        self.versions,self.columns,self.indexes=versions,columns,indexes
        self.statements=[]
    def __enter__(self): return self
    def __exit__(self,*args): return False
    def execute(self, statement):
        text=str(statement)
        self.statements.append(text)
        if 'SELECT version' in text: return Result(self.versions)
        if 'information_schema.columns' in text: return Result(self.columns)
        if 'pg_indexes' in text: return Result(self.indexes)
        return Result([])


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv('APP_ENV','staging')
    monkeypatch.setenv('AUTO_CREATE_TABLES','false')
    monkeypatch.setenv('JWT_SECRET_KEY','synthetic-signing-key-32-characters-qa')
    monkeypatch.setenv('GO360_CONNECTOR_ENABLED','0')
    conn=Connection(readiness.MIGRATIONS,readiness.COLUMNS,readiness.INDEXES)
    engine=SimpleNamespace(dialect=SimpleNamespace(name='postgresql'),
                           url=SimpleNamespace(host='postgres-ns3m.railway.internal'),
                           connect=lambda:conn)
    return engine,conn


def test_readiness_checks_schema_without_mutations(configured):
    engine,conn=configured
    assert readiness.ready(engine)
    assert all(s.startswith(('SELECT','SET LOCAL')) for s in conn.statements)


@pytest.mark.parametrize('part', ['versions','columns','indexes'])
def test_readiness_rejects_partial_migration(configured,part):
    engine,conn=configured
    setattr(conn,part,set())
    assert not readiness.ready(engine)


def test_readiness_rejects_wrong_database_even_with_connector_off(configured):
    engine,conn=configured
    engine.url.host='postgres.railway.internal'
    assert not readiness.ready(engine)
    assert not conn.statements


def test_readiness_rejects_auto_schema_creation(configured,monkeypatch):
    monkeypatch.setenv('AUTO_CREATE_TABLES','true')
    assert not readiness.ready(configured[0])


def test_readiness_rejects_requested_but_misconfigured_release(configured,monkeypatch):
    monkeypatch.setenv('GO360_CONNECTOR_ENABLED','1')
    monkeypatch.delenv('GO360_CONNECTOR_RELEASE_ENV',raising=False)
    assert not readiness.ready(configured[0])


def test_runner_cannot_relabel_production_host_as_staging(tmp_path,monkeypatch):
    monkeypatch.setenv('APP_ENV','staging')
    monkeypatch.setenv('DATABASE_URL','postgresql://fixture:fixture@postgres.railway.internal/railway')
    with pytest.raises(ValueError,match='Approved host'):
        migration.prepare('staging','postgres.railway.internal',tmp_path/'not-read.json')


def test_runner_rejects_migration_tampering_before_connection(tmp_path,monkeypatch):
    monkeypatch.setenv('APP_ENV','staging')
    monkeypatch.setenv('DATABASE_URL','postgresql://fixture:fixture@postgres-ns3m.railway.internal/railway')
    manifest=tmp_path/'hashes.json'
    manifest.write_text('{}')
    with pytest.raises(ValueError,match='fingerprint mismatch'):
        migration.prepare('staging','postgres-ns3m.railway.internal',manifest)


def test_runner_prepares_only_approved_chain_without_nested_transactions(tmp_path,monkeypatch):
    monkeypatch.setenv('APP_ENV','staging')
    monkeypatch.setenv('DATABASE_URL','postgresql://fixture:fixture@postgres-ns3m.railway.internal/railway')
    directory=Path(migration.__file__).parent/'migrations'
    hashes={name:hashlib.sha256((directory/name).read_bytes()).hexdigest() for name in migration.VERSIONS}
    manifest=tmp_path/'hashes.json'
    manifest.write_text(json.dumps(hashes))
    _,scripts=migration.prepare('staging','postgres-ns3m.railway.internal',manifest)
    assert [name for name,_ in scripts] == list(migration.VERSIONS)
    assert len(scripts)==4
    assert all(line.strip().upper() not in {'BEGIN;','COMMIT;'} for _,sql in scripts for line in sql.splitlines())
