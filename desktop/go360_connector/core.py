"""Durable encrypted captures and account-bound transfer state. No patient logs."""
import base64
import hashlib
import json
from pathlib import Path
import random
import sqlite3
import time
import uuid

PROFILE = 'go360-wellness-intelligence-v1'
DEFAULT_FOLDER = r'C:\Program Files (x86)\Go360 Wellness Intelligence'
MAX_HTML = 8*1024*1024


def validate_folder(folder, profile=PROFILE):
    root = Path(folder).resolve(strict=True)
    if profile != PROFILE:
        raise ValueError('Only the validated GO360 profile is supported.')
    source = root / 'ResultOne.htm'
    if not root.is_dir() or not source.is_file() or source.is_symlink():
        raise ValueError('Choose the GO360 installation folder containing ResultOne.htm.')
    if not (root / 'Result01.htm').is_file() or not (root / 'ReportE').is_dir():
        raise ValueError('Expected GO360 report structure was not found. This product needs validation.')
    return source


def envelope(protection, value):
    return protection.encrypt(json.dumps(value, separators=(',', ':')).encode())


def unpack(protection, value):
    return json.loads(protection.decrypt(value))


class Queue:
    def __init__(self, path, protection, clock=time.time):
        self.path, self.protection, self.clock = Path(path), protection, clock
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                PRAGMA secure_delete=ON;
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, account TEXT NOT NULL, state TEXT NOT NULL,
                    created REAL NOT NULL, accepted REAL, next_attempt REAL NOT NULL DEFAULT 0,
                    attempts INTEGER NOT NULL DEFAULT 0, payload BLOB, detail BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS cursors (key TEXT PRIMARY KEY, value BLOB NOT NULL);
            ''')
            db.execute("UPDATE jobs SET state='failed_retryable',next_attempt=0 WHERE state='uploading'")

    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA synchronous=FULL')
        db.execute('PRAGMA secure_delete=ON')
        return db

    @staticmethod
    def account_key(origin, account):
        return hashlib.sha256((origin+'|'+account).encode()).hexdigest()

    def capture(self, origin, account, source, raw, profile=PROFILE):
        if not raw or len(raw) > MAX_HTML or b'<html' not in raw[:65536].lower() or b'\x00' in raw:
            raise ValueError('Assessment HTML is incomplete or unsupported.')
        # Detect common partial saves without recreating the authoritative parser.
        if b'</html>' not in raw[-65536:].lower():
            raise ValueError('Assessment is still being written.')
        digest = hashlib.sha256(raw).hexdigest()
        account_key = self.account_key(origin, account)
        cursor = hashlib.sha256((account_key+'|'+str(source)).encode()).hexdigest()
        job = str(uuid.uuid4())
        stamp = self.clock()
        info = dict(origin=origin, account_id=account, profile=profile, sha256=digest,
                    captured_at=stamp, source=str(source), error=None, server=None)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            last = db.execute('SELECT value FROM cursors WHERE key=?', (cursor,)).fetchone()
            if last and unpack(self.protection, last['value']) == digest:
                return None
            db.execute('INSERT INTO jobs(id,account,state,created,payload,detail) VALUES(?,?,?,?,?,?)',
                       (job, account_key, 'captured', stamp, self.protection.encrypt(raw), envelope(self.protection, info)))
            db.execute('INSERT OR REPLACE INTO cursors VALUES(?,?)', (cursor, envelope(self.protection, digest)))
            db.execute("UPDATE jobs SET state='pending_upload' WHERE id=?", (job,))
        return job

    def jobs(self, origin, account):
        with self.connect() as db:
            rows = db.execute('SELECT * FROM jobs WHERE account=? ORDER BY created', (self.account_key(origin, account),)).fetchall()
        return [dict(row) for row in rows]

    def info(self, job):
        return unpack(self.protection, job['detail'])

    def patch(self, job, *, state=None, server=None, error=None, retry=False):
        info = self.info(job)
        if server is not None:
            info['server'] = server
        info['error'] = error
        attempts = job['attempts'] + (1 if retry else 0)
        delay = random.uniform(0.5, 1.0) * min(3600, 2 ** min(attempts, 12)) if retry else 0
        with self.connect() as db:
            db.execute('UPDATE jobs SET state=?,detail=?,attempts=?,next_attempt=? WHERE id=?',
                       (state or job['state'], envelope(self.protection, info), attempts, self.clock()+(60 if server is not None else delay), job['id']))
            if server is not None:
                # A duplicate hold is unresolved: retain its payload until resolved.
                accepted = self.clock() if server['status'] != 'needs_attention' else None
                db.execute('UPDATE jobs SET accepted=COALESCE(accepted,?) WHERE id=?', (accepted,job['id']))

    def retry(self, origin, account):
        with self.connect() as db:
            db.execute("UPDATE jobs SET state='pending_upload',next_attempt=0 WHERE account=? AND accepted IS NULL AND payload IS NOT NULL",
                       (self.account_key(origin, account),))

    def cleanup(self):
        with self.connect() as db:
            count = db.execute("UPDATE jobs SET payload=NULL WHERE accepted IS NOT NULL AND accepted<=? AND state IN ('awaiting_practitioner','uploaded','case_created','completed') AND payload IS NOT NULL",
                               (self.clock()-7*86400,)).rowcount
            db.execute('PRAGMA wal_checkpoint(TRUNCATE)') if not db.in_transaction else None
        return count


def stable_capture(source, read, wait=time.sleep, quiet=1.5):
    before = source.stat()
    first = read(source)
    wait(quiet)
    second = read(source)
    after = source.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or first != second:
        raise OSError('Assessment changed during capture; will retry.')
    return second
