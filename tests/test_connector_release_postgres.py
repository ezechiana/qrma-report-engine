"""Opt-in disposable PostgreSQL migration and multi-process quota QA."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid
import pytest
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict,make_conninfo
from sqlalchemy import create_engine,text
from sqlalchemy.engine import make_url
from app.db import connector_release_migrate as release
from app.services.connector_limits import shared_allow


@pytest.fixture
def disposable():
    url=os.getenv('GO360_CONNECTOR_TEST_DATABASE_URL')
    if not url:
        pytest.skip('Dedicated disposable local PostgreSQL URL is required')
    normalized=url.replace('postgresql+psycopg:','postgresql:')
    params=conninfo_to_dict(normalized)
    if params.get('host') not in {'localhost','127.0.0.1','::1'} or not params.get('dbname','').startswith('go360_connector_test_'):
        pytest.fail('Only a localhost go360_connector_test_ database is permitted')
    name='go360_connector_test_release_'+uuid.uuid4().hex
    with psycopg.connect(normalized,autocommit=True) as admin:
        admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    child=make_conninfo(**{**params,'dbname':name})
    try:
        with psycopg.connect(child) as conn:
            conn.execute("""
                CREATE TABLE users(id UUID PRIMARY KEY);
                CREATE TABLE patients(id UUID PRIMARY KEY);
                CREATE TABLE cases(id UUID PRIMARY KEY,user_id UUID REFERENCES users(id));
                CREATE TABLE report_versions(id UUID PRIMARY KEY);
                CREATE TABLE audit_logs(id UUID PRIMARY KEY,created_at TIMESTAMPTZ DEFAULT NOW());
                INSERT INTO users VALUES ('00000000-0000-0000-0000-000000000001');
                INSERT INTO patients VALUES ('00000000-0000-0000-0000-000000000001');
                INSERT INTO cases VALUES ('00000000-0000-0000-0000-000000000001','00000000-0000-0000-0000-000000000001');
                INSERT INTO report_versions VALUES ('00000000-0000-0000-0000-000000000001');
                INSERT INTO audit_logs(id) VALUES ('00000000-0000-0000-0000-000000000001');
            """)
        scripts=[]
        for name in release.VERSIONS:
            content=(Path(release.__file__).parent/'migrations'/name).read_text(encoding='utf-8-sig')
            if name==release.VERSIONS[0]:
                content='\n'.join(line for line in content.splitlines() if line.strip().upper() not in {'BEGIN;','COMMIT;'})
            scripts.append((name,content))
        yield child,scripts
    finally:
        # Only this generated empty-test database can be dropped.
        generated=conninfo_to_dict(child)['dbname']
        assert generated.startswith('go360_connector_test_release_') and len(generated)==len('go360_connector_test_release_')+32
        with psycopg.connect(normalized,autocommit=True) as admin:
            admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(generated)))


def apply(url,scripts):
    with psycopg.connect(url) as conn:
        release.execute(conn,scripts,apply=True)


def test_actual_runner_dryrun_apply_replay_and_preservation(disposable):
    url,scripts=disposable
    with psycopg.connect(url,options='-c default_transaction_read_only=on') as conn:
        release.execute(conn,scripts,apply=False)
    with psycopg.connect(url) as conn:
        assert conn.execute("SELECT to_regclass('schema_migrations')").fetchone()[0] is None
    apply(url,scripts)
    apply(url,scripts)
    with psycopg.connect(url) as conn:
        assert {row[0] for row in conn.execute('SELECT version FROM schema_migrations')}==set(release.VERSIONS)
        for table in ('users','patients','cases','report_versions','audit_logs'):
            assert conn.execute(sql.SQL('SELECT count(*) FROM {}').format(sql.Identifier(table))).fetchone()[0]==1


def test_actual_runner_failure_rolls_back_schema_and_ledger(disposable):
    url,scripts=disposable
    broken=[scripts[0],(scripts[1][0],scripts[1][1]+'\nSELECT 1/0;')]
    with pytest.raises(psycopg.errors.DivisionByZero):
        apply(url,broken)
    with psycopg.connect(url) as conn:
        assert conn.execute("SELECT to_regclass('schema_migrations'),to_regclass('connector_devices')").fetchone()==(None,None)
        assert not conn.execute("SELECT 1 FROM information_schema.columns WHERE table_name='cases' AND column_name='assessment_fingerprint'").fetchone()


def test_parallel_migration_runners_serialize(disposable):
    url,scripts=disposable
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _:apply(url,scripts),range(2)))
    with psycopg.connect(url) as conn:
        assert conn.execute('SELECT count(*) FROM schema_migrations').fetchone()[0]==4


def test_lock_timeout_does_not_advance_ledger(disposable):
    url,scripts=disposable
    with psycopg.connect(url) as blocker:
        blocker.execute('LOCK TABLE cases IN ACCESS EXCLUSIVE MODE')
        with pytest.raises(psycopg.errors.LockNotAvailable):
            apply(url,scripts)
        blocker.rollback()
    with psycopg.connect(url) as conn:
        assert conn.execute("SELECT to_regclass('schema_migrations')").fetchone()[0] is None


def test_shared_budget_across_two_processes(disposable,monkeypatch):
    url,scripts=disposable
    apply(url,scripts)
    env=dict(os.environ, QA_LOCAL_URL=url, APP_ENV='staging',
             GO360_CONNECTOR_RATE_LIMIT_KEY='synthetic-shared-limiter-secret-for-qa')
    code="""
import json,os
from sqlalchemy import create_engine
from app.services.connector_limits import shared_allow
from psycopg.conninfo import conninfo_to_dict
from sqlalchemy.engine import URL
p=conninfo_to_dict(os.environ['QA_LOCAL_URL'])
engine=create_engine(URL.create('postgresql+psycopg',username=p['user'],password=p.get('password'),host=p['host'],port=int(p.get('port',5432)),database=p['dbname']))
try:
 print(json.dumps(sum(shared_allow('parallel','synthetic-client',7,engine=engine) for _ in range(12))))
finally:
 engine.dispose()
"""
    workers=[subprocess.Popen([sys.executable,'-c',code],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for _ in range(2)]
    accepted=[]
    try:
        for worker in workers:
            stdout,stderr=worker.communicate(timeout=45)
            assert worker.returncode==0,'Quota worker failed'
            accepted.append(json.loads(stdout))
        assert sum(accepted)==7
        with psycopg.connect(url) as conn:
            row=conn.execute('SELECT bucket_key,hits FROM connector_rate_limits').fetchone()
            assert len(row[0])==64 and row[1]==7
    finally:
        for worker in workers:
            if worker.poll() is None:
                worker.kill()
                worker.wait()
