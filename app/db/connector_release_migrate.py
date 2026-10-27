"""Explicit connector-only migration command; dry-run unless --apply is given."""
import argparse
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

EXPECTED_HOSTS = {
    'staging': 'postgres-ns3m.railway.internal',
    'production': 'postgres.railway.internal',
}

VERSIONS = (
    '20261026_assessment_fingerprint.sql',
    '20261026_connector_v04.sql',
    '20261027_connector_v050_observability.sql',
    '20261027_connector_v050_rate_limits.sql',
)


def prepare(environment, expected_host, manifest):
    if environment not in {'staging', 'production'} or os.getenv('APP_ENV') != environment:
        raise ValueError('APP_ENV must match the explicit release environment')
    if expected_host != EXPECTED_HOSTS[environment]:
        raise ValueError('Approved host does not match the release environment')
    url = os.environ['DATABASE_URL']
    parsed = urlsplit(url.replace('postgresql+psycopg:', 'postgresql:'))
    if parsed.scheme not in {'postgres', 'postgresql'} or parsed.hostname != expected_host:
        raise ValueError('Database target does not match the approved host')
    if not parsed.username or not parsed.password or not parsed.path.strip('/'):
        raise ValueError('Incomplete database target')
    hashes = json.loads(Path(manifest).read_text(encoding='utf-8'))
    scripts = []
    for version in VERSIONS:
        raw = (Path(__file__).parent / 'migrations' / version).read_bytes()
        if hashlib.sha256(raw).hexdigest() != hashes.get(version):
            raise ValueError('Migration fingerprint mismatch: ' + version)
        sql = raw.decode('utf-8-sig')
        # Preserve the historical source bytes; the release runner owns the transaction.
        if version == '20261026_assessment_fingerprint.sql':
            lines = sql.splitlines()
            if sum(line.strip().upper() == 'BEGIN;' for line in lines) != 1 or sum(line.strip().upper() == 'COMMIT;' for line in lines) != 1:
                raise ValueError('Unexpected fingerprint migration transaction structure')
            sql = '\n'.join(line for line in lines if line.strip().upper() not in {'BEGIN;', 'COMMIT;'})
        scripts.append((version, sql))
    return url.replace('postgresql+psycopg:', 'postgresql:'), scripts


def execute(conn, scripts, apply=False):
    with conn.cursor() as cur:
        cur.execute("SET LOCAL lock_timeout = '5s'")
        cur.execute("SET LOCAL statement_timeout = '30s'")
        cur.execute("SELECT to_regclass('public.users'), to_regclass('public.patients'), to_regclass('public.cases'), to_regclass('public.report_versions'), to_regclass('public.audit_logs')")
        if not all(cur.fetchone()):
            raise ValueError('Required existing application tables are missing')
        cur.execute("SELECT to_regclass('public.schema_migrations')")
        ledger_exists = bool(cur.fetchone()[0])
        applied = set()
        if ledger_exists:
            cur.execute('SELECT version FROM public.schema_migrations')
            applied = {row[0] for row in cur.fetchall()}
        if apply:
            cur.execute('SELECT pg_advisory_xact_lock(360050)')
            # Re-read under the lock so concurrent approved runners serialize.
            cur.execute('CREATE TABLE IF NOT EXISTS public.schema_migrations (version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ DEFAULT NOW())')
            cur.execute('SELECT version FROM public.schema_migrations')
            applied = {row[0] for row in cur.fetchall()}
            cur.execute('SET LOCAL search_path TO public')
        for version, sql in scripts:
            if version in applied:
                print('Already recorded: ' + version)
            elif apply:
                cur.execute(sql)
                cur.execute('INSERT INTO public.schema_migrations(version) VALUES (%s)', (version,))
                print('Applied in transaction: ' + version)
            else:
                print('Pending: ' + version)
        if not apply:
            conn.rollback()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--environment', required=True, choices=['staging', 'production'])
    parser.add_argument('--expected-host', required=True)
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    try:
        url, scripts = prepare(args.environment, args.expected_host, args.manifest)
        import psycopg
        options = '-c statement_timeout=30000 -c lock_timeout=5000'
        if not args.apply:
            options += ' -c default_transaction_read_only=on'
        with psycopg.connect(url, sslmode='require', connect_timeout=8, options=options) as conn:
            execute(conn, scripts, args.apply)
        print('Committed connector migrations' if args.apply else 'Read-only preflight complete; no schema changes')
    except Exception as exc:
        # Do not expose driver errors, SQL payloads or connection strings.
        raise SystemExit('Release migration stopped (' + type(exc).__name__ + '); investigate securely') from None


if __name__ == '__main__':
    main()
