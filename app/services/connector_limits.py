"""Single-process DEV quota. Shared limiter required before multi-worker rollout."""
from collections import OrderedDict, deque
from threading import Lock
import time
from fastapi import HTTPException

_buckets = OrderedDict()
_lock = Lock()


def allow(kind, identity, count, *, clock=time.monotonic):
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
