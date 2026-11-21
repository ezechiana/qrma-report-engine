"""Private attempt keys and conservative cleanup after rollback/uncertain commit."""
from uuid import UUID
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from app.db.models import ReportVersion, User
from app.services import storage_service as storage


def attempt_prefix(owner_id, version_id):
    return f'users/{UUID(str(owner_id))}/clinical-attempts/{UUID(str(version_id))}/'


def attempt_filenames(owner_id, version_id):
    prefix = attempt_prefix(owner_id, version_id)
    return prefix + 'report.html', prefix + 'report.pdf'


def require_private_storage():
    flags = storage.s3.get_public_access_block(Bucket=storage.S3_BUCKET)['PublicAccessBlockConfiguration']
    if not all(flags.get(k) is True for k in (
            'BlockPublicAcls', 'IgnorePublicAcls', 'BlockPublicPolicy', 'RestrictPublicBuckets')):
        raise RuntimeError('Private artifact storage required')
    try:
        public = storage.s3.get_bucket_policy_status(Bucket=storage.S3_BUCKET)['PolicyStatus']['IsPublic']
    except storage.ClientError as exc:
        if exc.response.get('Error', {}).get('Code') != 'NoSuchBucketPolicy':
            raise
        public = False  # No policy, with all four public-access blocks verified.
    if public:
        raise RuntimeError('Private artifact storage required')


def reconcile_attempt(bind, owner_id, version_id, *, remove=True):
    """Safe to retry, including after a lost commit acknowledgement.

    Acquire the same owner lock on a fresh connection before deciding absence.
    An in-flight transaction must finish first; inability to verify means KEEP.
    The operations caller must enumerate only this reserved attempt namespace.
    """
    with Session(bind=bind) as probe:
        probe.execute(text("SET LOCAL lock_timeout = '2s'"))
        probe.execute(select(User.id).where(User.id == owner_id).with_for_update()).scalar_one()
        if probe.get(ReportVersion, version_id) is not None:
            return 'kept_committed'
        if not remove:
            return 'would_remove_unreferenced'
        prefix = storage.normalise_key(attempt_prefix(owner_id, version_id))
        for page in storage.s3.get_paginator('list_objects_v2').paginate(Bucket=storage.S3_BUCKET, Prefix=prefix):
            for item in page.get('Contents', []):
                key = item['Key']
                if not key.startswith(prefix):
                    raise RuntimeError('Unexpected artifact namespace')
                storage.s3.delete_object(Bucket=storage.S3_BUCKET, Key=key)
        return 'removed_unreferenced'


def main():
    """Default read-only reconciliation; interrupted attempts remain private."""
    import argparse
    import json
    import re
    from datetime import datetime, timezone, timedelta
    from app.db.session import engine
    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--minimum-age-hours', type=int, default=24)
    args = parser.parse_args()
    if args.minimum_age_hours < 1:
        parser.error('minimum age must be at least one hour')
    require_private_storage()
    pattern = re.compile(r'^users/([0-9a-f-]{36})/clinical-attempts/([0-9a-f-]{36})/')
    prefix = storage.normalise_key('users/')
    root = storage.normalise_key('')
    cutoff = datetime.now(timezone.utc) - timedelta(hours=args.minimum_age_hours)
    attempts = {}
    for page in storage.s3.get_paginator('list_objects_v2').paginate(Bucket=storage.S3_BUCKET, Prefix=prefix):
        for item in page.get('Contents', []):
            key = item['Key']
            match = pattern.match(key[len(root):] if key.startswith(root) else '')
            if match:
                owner, version = map(UUID, match.groups())
                attempts[(owner, version)] = max(attempts.get((owner, version), item['LastModified']), item['LastModified'])
    counts = {}
    for (owner, version), modified in attempts.items():
        if modified > cutoff:
            outcome = 'kept_recent'
        else:
            try:
                outcome = reconcile_attempt(engine, owner, version, remove=args.apply)
            except Exception:
                outcome = 'kept_unverified'
        counts[outcome] = counts.get(outcome, 0) + 1
    print(json.dumps({'apply': args.apply, 'outcomes': counts}))


if __name__ == '__main__':
    main()
