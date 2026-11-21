"""Legacy social identity release gate. Read-only unless explicitly --apply."""
import argparse
from collections import Counter,defaultdict
from dataclasses import dataclass
from datetime import datetime,timezone
import hashlib,json,os
from urllib.parse import urlsplit
from uuid import uuid4
from sqlalchemy import create_engine,text

HOSTS={'staging':'postgres-ns3m.railway.internal','production':'postgres.railway.internal'}
PROVIDERS={'google','microsoft'}


class ReleaseBlocked(ValueError):pass


@dataclass(frozen=True)
class Entry:
    user_id:object
    provider:str
    subject:str | None
    status:str
    evidence_count:int


@dataclass(frozen=True)
class Plan:
    entries:tuple
    schema_ready:bool
    history_available:bool

    @property
    def candidates(self):return tuple(e for e in self.entries if e.status=='eligible')

    @property
    def blockers(self):return tuple(e for e in self.entries if e.status not in {'eligible','linked','inactive'})

    @property
    def fingerprint(self):
        values=[(str(e.user_id),str(e.provider or ''),e.subject,e.status) for e in self.entries]
        raw=json.dumps(sorted(values,key=lambda v:(v[0],v[1])),separators=(',',':'))
        return hashlib.sha256(raw.encode()).hexdigest()

    def summary(self):
        return {'schema_ready':self.schema_ready,'history_available':self.history_available,
            'eligible_backfills':len(self.candidates),'already_linked':sum(e.status=='linked' for e in self.entries),
            'unresolved':len(self.blockers),'blocked_reasons':dict(Counter(e.status for e in self.blockers)),
            'plan_sha256':self.fingerprint}


def classify(users,events,bindings):
    """Use recorded principal/subject pairs, never infer ownership from email."""
    groups=defaultdict(list);claims=defaultdict(set);bound={};owners={}
    for row in bindings:
        key=(row['user_id'],row['provider'])
        bound.setdefault(key,set()).add(row['subject'])
        owners.setdefault((row['provider'],row['subject']),set()).add(row['user_id'])
    for event in events:
        if event['user_id'] not in users:continue
        key=(event['user_id'],event['provider']);groups[key].append(event)
        if event['subject']:claims[(event['provider'],event['subject'])].add(event['user_id'])
    entries=[]
    for (uid,provider),records in groups.items():
        user=users[uid];subjects={r['subject'] for r in records if r['subject']}
        subject=next(iter(subjects)) if len(subjects)==1 else None
        if not user['is_active']:status='inactive'
        elif provider not in PROVIDERS:status='unsupported_provider'
        elif (uid,provider) in bound:
            current=bound[(uid,provider)]
            status='linked' if len(current)==1 and owners[(provider,next(iter(current)))]=={uid} else 'binding_conflict'
        elif not user['email_verified_at']:status='unverified_account'
        elif not subjects:status='missing_evidence'
        elif len(subjects)!=1:status='ambiguous_evidence'
        elif not subject.strip() or subject!=subject.strip() or len(subject)>255:status='invalid_evidence'
        elif claims[(provider,subject)]!={uid} or owners.get((provider,subject),{uid})!={uid}:status='identity_conflict'
        else:status='eligible'
        # A valid explicit current link supersedes older historical subjects.
        entries.append(Entry(uid,provider,subject,status,len(records)))
    return tuple(sorted(entries,key=lambda e:(str(e.user_id),str(e.provider))))


def exists(conn,name):
    return conn.execute(text('SELECT to_regclass(:name)'),{'name':name}).scalar() is not None


def inspect_coverage(conn):
    history=exists(conn,'auth_events') and exists(conn,'users')
    schema=exists(conn,'external_identities') and exists(conn,'audit_logs')
    if not history:return Plan((),schema,False)
    users={r['id']:r for r in conn.execute(text('SELECT id,is_active,email_verified_at FROM users')).mappings()}
    events=list(conn.execute(text("""SELECT user_id,meta->>'provider' AS provider,
        meta->>'provider_subject' AS subject FROM auth_events
        WHERE status='success' AND user_id IS NOT NULL
          AND (meta->>'method'='social' OR event_type='social_login')""")).mappings())
    bindings=list(conn.execute(text('SELECT user_id,provider,subject FROM external_identities')).mappings()) if exists(conn,'external_identities') else []
    return Plan(classify(users,events,bindings),schema,True)


def check_ready(plan):
    if not plan.history_available:raise ReleaseBlocked('Social authentication history is unavailable; readiness cannot be established.')
    if not plan.schema_ready:raise ReleaseBlocked('Identity/audit schema must be migrated before release.')
    if plan.blockers:raise ReleaseBlocked('Unresolved legacy identity evidence blocks release.')
    if plan.candidates:raise ReleaseBlocked('Missing legacy provider bindings block release.')


def apply_backfill(conn,approved_plan):
    """Caller owns the transaction. Binding/audit failures roll back all writes."""
    # Freeze existing principals and evidence during the reviewed data operation.
    # A final release check plus sign-in drain handles accounts created by the
    # old application during a rolling cutover; see the release runbook.
    conn.execute(text('SELECT id FROM users ORDER BY id FOR UPDATE')).all()
    if exists(conn,'auth_events'):conn.execute(text("SELECT id FROM auth_events WHERE status='success' ORDER BY id FOR SHARE")).all()
    plan=inspect_coverage(conn)
    if not plan.history_available or not plan.schema_ready:raise ReleaseBlocked('Required identity history/schema is missing.')
    if plan.blockers:raise ReleaseBlocked('Ambiguous, missing or conflicting evidence requires explicit resolution.')
    if not plan.candidates:return {'restored':0,'already_complete':True}
    if not approved_plan or approved_plan!=plan.fingerprint:raise ReleaseBlocked('Reviewed plan changed or no approved plan supplied.')
    # Only technical identity/audit rows may be written. Compare every principal
    # and the permission-bearing tables before commit, without exporting values.
    tables=['users','user_capabilities','subscriptions','clinic_memberships','app_settings']
    before={t:conn.execute(text('SELECT * FROM '+t+' ORDER BY 1')).all() for t in tables if exists(conn,t)}
    audit_columns=set(conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='audit_logs'")).scalars())
    if not {'id','user_id','action','metadata_json'}.issubset(audit_columns):raise ReleaseBlocked('Auditable identity restoration is unavailable.')
    for entry in plan.candidates:
        conn.execute(text('INSERT INTO external_identities(id,user_id,provider,subject) VALUES (:id,:uid,:provider,:subject)'),
            {'id':uuid4(),'uid':entry.user_id,'provider':entry.provider,'subject':entry.subject})
        metadata={'provider':entry.provider,'source':'historical_successful_social_login',
            'evidence_count':entry.evidence_count,'release_plan_sha256':approved_plan,'actor':'release_operator','outcome':'success'}
        params={'id':uuid4(),'uid':entry.user_id,'action':'identity.legacy_binding_restored','metadata':json.dumps(metadata)}
        columns=['id','user_id','action','metadata_json'];values=[':id',':uid',':action','CAST(:metadata AS jsonb)']
        for column,value in [('actor_type','system'),('outcome','success')]:
            if column in audit_columns:columns.append(column);values.append(':'+column);params[column]=value
        conn.execute(text('INSERT INTO audit_logs('+','.join(columns)+') VALUES ('+','.join(values)+')'),params)
    for table,original in before.items():
        if conn.execute(text('SELECT * FROM '+table+' ORDER BY 1')).all()!=original:
            raise ReleaseBlocked('Identity or permission state changed; transaction must roll back.')
    check_ready(inspect_coverage(conn))
    return {'restored':len(plan.candidates),'already_complete':False}


def target(environment,expected_host):
    if environment not in HOSTS or os.getenv('APP_ENV','').strip().lower()!=environment:
        raise ReleaseBlocked('Explicit environment must match APP_ENV.')
    if expected_host!=HOSTS[environment]:raise ReleaseBlocked('Approved host does not match environment.')
    url=os.environ.get('DATABASE_URL','')
    parsed=urlsplit(url.replace('postgresql+psycopg:','postgresql:'))
    if parsed.hostname!=expected_host or parsed.scheme not in {'postgres','postgresql'}:
        raise ReleaseBlocked('Database target does not match approved host.')
    if not parsed.username or not parsed.password or not parsed.path.strip('/'):
        raise ReleaseBlocked('Incomplete database target.')
    return url.replace('postgres://','postgresql+psycopg://',1).replace('postgresql://','postgresql+psycopg://',1)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--environment',required=True,choices=sorted(HOSTS))
    parser.add_argument('--expected-host',required=True)
    mode=parser.add_mutually_exclusive_group();mode.add_argument('--apply',action='store_true');mode.add_argument('--check',action='store_true')
    parser.add_argument('--approved-plan-sha256')
    args=parser.parse_args()
    try:
        url=target(args.environment,args.expected_host)
        options='-c statement_timeout=30000 -c lock_timeout=5000'
        if not args.apply:options+=' -c default_transaction_read_only=on'
        engine=create_engine(url,connect_args={'sslmode':'require','connect_timeout':8,'options':options})
        try:
            with engine.begin() as conn:
                conn.execute(text('SET LOCAL search_path TO public'))
                plan=inspect_coverage(conn)
                print(json.dumps(plan.summary(),sort_keys=True))
                if args.check:check_ready(plan)
                if args.apply:print(json.dumps(apply_backfill(conn,args.approved_plan_sha256),sort_keys=True))
        finally:engine.dispose()
    except Exception as exc:
        # Driver exceptions can contain connection strings, SQL and identifiers.
        message=str(exc) if isinstance(exc,ReleaseBlocked) else type(exc).__name__
        raise SystemExit('Legacy identity release stopped: '+message) from None


if __name__=='__main__':main()
