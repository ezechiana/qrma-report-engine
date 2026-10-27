"""DEV sliding windows; release quotas serialize atomically in PostgreSQL."""
from collections import OrderedDict, deque
from threading import Lock
from functools import lru_cache
import hashlib
import hmac
import os
import time
from fastapi import HTTPException

_buckets = OrderedDict()
_lock = Lock()


def allow(kind, identity, count, *, clock=time.monotonic):
    from app.services import connector_policy as policy
    if policy.release_environment():
        if not policy.enabled():
            raise HTTPException(503, 'Connector release configuration is unavailable.')
        return shared_allow(kind, identity, count)
    return local_allow(kind, identity, count, clock=clock)


@lru_cache(maxsize=1)
def release_engine():
    # Separate bounded pool: an ORM session must not starve its own quota query.
    from sqlalchemy import create_engine
    url = os.environ['DATABASE_URL']
    if url.startswith('postgres://'):
        url = url.replace('postgres://', 'postgresql+psycopg://', 1)
    elif url.startswith('postgresql://'):
        url = url.replace('postgresql://', 'postgresql+psycopg://', 1)
    return create_engine(url, pool_size=2, max_overflow=2, pool_timeout=2,
                         pool_pre_ping=True,
                         connect_args={'sslmode': 'require', 'connect_timeout': 3})


def shared_allow(kind, identity, count, *, engine=None):
    """Fixed 60s window, separate transaction; denied requests do not increment."""
    from sqlalchemy import text
    if not isinstance(count, int) or count < 1:
        raise ValueError('Invalid quota')
    secret = os.getenv('GO360_CONNECTOR_RATE_LIMIT_KEY', '')
    if len(secret) < 32:
        raise HTTPException(503, 'Connector rate limiting is unavailable.')
    material = '\x00'.join((os.getenv('APP_ENV', ''), str(kind), str(identity))).encode()
    key = hmac.new(secret.encode(), material, hashlib.sha256).hexdigest()
    try:
        if engine is None:
            engine = release_engine()
        if engine.dialect.name != 'postgresql':
            raise ValueError('PostgreSQL is required')
        with engine.begin() as connection:
            connection.execute(text("SET LOCAL lock_timeout = '2s'"))
            connection.execute(text("SET LOCAL statement_timeout = '3s'"))
            admitted = connection.execute(text('''
                INSERT INTO connector_rate_limits(bucket_key, hits, reset_at)
                VALUES (:key, 1, statement_timestamp() + interval '60 seconds')
                ON CONFLICT (bucket_key) DO UPDATE
                SET hits = CASE WHEN connector_rate_limits.reset_at <= statement_timestamp()
                                THEN 1 ELSE connector_rate_limits.hits + 1 END,
                    reset_at = CASE WHEN connector_rate_limits.reset_at <= statement_timestamp()
                                    THEN statement_timestamp() + interval '60 seconds'
                                    ELSE connector_rate_limits.reset_at END
                WHERE connector_rate_limits.reset_at <= statement_timestamp()
                   OR connector_rate_limits.hits < :quota
                RETURNING hits
            '''), {'key': key, 'quota': count}).scalar_one_or_none() is not None
            connection.execute(text('''DELETE FROM connector_rate_limits WHERE bucket_key IN (
                SELECT bucket_key FROM connector_rate_limits
                WHERE reset_at < statement_timestamp() - interval '5 minutes'
                ORDER BY reset_at LIMIT 32 FOR UPDATE SKIP LOCKED
            )'''))
        return admitted
    except Exception:
        raise HTTPException(503, 'Connector rate limiting is unavailable.') from None


def local_allow(kind, identity, count, *, clock=time.monotonic):
    now = clock()
    key = (kind, identity)
    with _lock:
        while _buckets and next(iter(_buckets.values()))[-1] <= now - 60:
            _buckets.popitem(last=False)
        bucket = _buckets.get(key)
        if bucket is None:
            if len(_buckets) >= 10000:
                return False
            bucket = _buckets[key] = deque()
        while bucket and bucket[0] <= now - 60:
            bucket.popleft()
        if len(bucket) >= count:
            return False
        bucket.append(now)
        _buckets.move_to_end(key)
        return True


def require(kind, identity, count):
    if not allow(kind, identity, count):
        raise HTTPException(429, 'Try again later.', headers={'Retry-After': '60'})
