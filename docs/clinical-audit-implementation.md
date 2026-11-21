# Clinical audit logging implementation and release controls

GO3-36 / GO360-012 / G360-08-001 / T-GO360-012. 21 November 2026.
Eze Chiana approved the bounded S0 recommendation. GO3-189 records the role-based
design dispositions; Codex implements and verifies engineering, with Eze as
product and delivery owner. This is synthetic engineering evidence, not an
independent human release opinion or permission to deploy.

## Delivered boundary

GET /api/reports/{report_version_id}/json preserves authentication and
Case.user_id ownership. Missing and foreign UUIDs both return the established
404 without a foreign-record lookup for enrichment. Allowed reads commit a
minimal event before returning a snapshot of clinical JSON. Allowed audit
failure returns a sanitised 503 with no report content; denial audit failure
retains the same 404. Operational failure logs contain only a fixed code and
server-generated correlation UUID. Successful JSON responses are no-store.

POST /api/cases/{case_id}/generate-report uses the caller-owned transaction path
when the case already has a ready/final Assessment. This is **Assessment report
regenerated**, not note signing or a correction to source observations. New
version, quota reservation/settlement, legacy lifecycle events and the clinical
success event commit together. Failure rolls back the candidate and preserves
the prior version and its shares. The unique resulting-version constraint
prevents duplicate success evidence. Replaying an existing consumed request key
returns its original version, without rendering, quota use or another success
event. Pre-existing legacy reservations are not retroactively fabricated into
new clinical events. Five monthly Free/Foundation Assessments and paid/trial
usage policy remain unchanged.

The route runs in FastAPI's worker pool, using its own asyncio.run for rendering.
A synchronous competing PostgreSQL lock therefore cannot block the ASGI event
loop while a renderer awaits PDF I/O. The owner lock serialises quota/version
allocation through commit, including competing distinct keys. Its acquisition
timeout is five seconds; failure is safe to retry with the same key. This is a
bounded synchronous workflow, not a new background queue or a capacity/SLO claim.

Imports, connector ConfirmationSession and other generation callers keep the
existing default service semantics. log_action still commits by default; its
explicit commit=False path flushes inside the caller's transaction. No clinical
event records the report JSON, patient names, credentials, arbitrary request
bodies, exception strings or arbitrary metadata.

Clinical events use immutable UUID snapshots without source-deletion FKs. Actor
and practice owner come from the authenticated owned-record boundary. Clinic and
organisation remain null, with scope_reason=legacy_unresolved. Administrative
memberships do not create clinical authority. This demonstrates two-practice
isolation, not a completed clinic ownership migration. No audit viewer or public
audit CRUD/readback API is added. Alternative viewer/export/share disclosure
paths are outside this selected-read acceptance slice.

## Support sessions and existing session cutover

New access and refresh tokens carry a signed clinical-session revision; support
tokens also carry the initiating administrator UUID. Refresh preserves both.
Both selected operations explicitly refuse support mode. Removing the restore
cookie, switching to bearer authentication or refreshing cannot remove the
restriction. A cookie alone never proves an initiating actor. Unknown restore
context is refused conservatively without inventing an actor.

Pre-cutover tokens cannot distinguish an old impersonation session from a normal
target login. They must sign in again for these operations (401 with an explicit
instruction); refreshing an old token does not upgrade its clinical-session
revision. Other existing access remains subject to its established rules. This
is a required rollout notice and UAT check, not an email/password/role migration.

## External artifacts and uncertain commits

Regeneration writes only to a server-owned attempt namespace:
users/{owner UUID}/clinical-attempts/{new version UUID}/report.html and report.pdf,
under the existing storage prefix. Retrying cannot overwrite prior-version
artifacts. The builder's returned keys must match the expected attempt keys.
Before writing, verify all four S3 public-access blocks and a non-public bucket
policy. A missing policy is acceptable only after the blocks are verified;
AccessDenied, incomplete checks and public configuration fail closed.

The application exposes generated artifact references only via committed,
authorised report records. There is no /generated static mount. Rollback cleanup
uses a fresh connection and the same owner lock before checking version absence;
an uncertain in-flight commit must settle first. Keep artifacts if the version
committed or the database/storage outcome cannot be verified. A lost commit
acknowledgement can therefore be retried safely without deleting committed files.

Interrupted cleanup can be reconciled idempotently using:

```powershell
python -m app.services.clinical_artifacts
python -m app.services.clinical_artifacts --apply --minimum-age-hours 24
```

The first command is read-only. Both inspect only the reserved attempt namespace
and emit aggregate outcomes; recent, committed and unverified attempts stay.
No background schedule, hosted S3 permissions or bucket configuration has been
changed. A deleted owner/unverifiable database must be reviewed by operations;
the reconciler deliberately does not guess that deletion is safe. Retention is
not settled by this mechanism. Real object-storage recovery is not claimed from
the synthetic transport used in tests.

## Database release procedure

1. Integrate the identity foundation prerequisite and select an exact reviewed
   candidate. Preserve the separate Production/Staging branch policy. Use the
   existing Production identity backfill runbook as well; clinical audit does
   not replace that gate.
2. As the migration identity, apply the additive
   20261121_clinical_audit.sql and 20261121_runtime_table_compatibility.sql
   through the filename-ledger runner. Existing
   AuditLog IDs/rows and ledger history are untouched. Authentication tables
   and indexes previously created at runtime are provisioned additively here,
   with identical legacy definitions. Auth helpers reuse complete existing
   objects, so a restricted login can sign in and use recovery without DDL.
   The compatibility migration also provisions sharing analytics, webhook
   idempotency and platform-admin audit objects. Their legacy helpers reuse
   existing objects without DDL; local development retains its fallback.
3. Operations provisions three independently controlled roles: a NOLOGIN
   non-superuser owner, a LOGIN application writer, and a separate LOGIN reviewer.
   Writer/reviewer must not own or CREATE objects in the application schema,
   have privileged role memberships, bypass RLS or inherit the owner. Keep
   passwords in the environment's secret mechanism, never in SQL files or Git.
   Existing application-table permissions must be provisioned separately; this
   tool deliberately changes only clinical-evidence ACLs.
4. Execute with the migration credential, using actual role/schema names:

   ```powershell
   python -m app.db.clinical_audit_roles --schema public --owner clinical_owner --writer application_runtime --reviewer clinical_reviewer
   ```

   The idempotent tool assigns clinical table/function ownership, revokes public
   and direct runtime/reviewer grants, grants INSERT only to writer and scoped
   SELECT only to reviewer, then checks effective/inherited permissions. The
   writer has no SELECT, UPDATE, DELETE, TRUNCATE, TRIGGER or REFERENCES, including
   column grants. Mutation/truncate refusal triggers and forced event-table RLS
   add defence in depth. Scope administration belongs to the independent owner;
   reviewer scopes are RLS-filtered by session_user, never by a client-set GUC.
5. Through the independently controlled owner, assign reviewer/practice UUID
   pairs in clinical_audit_reviewer_scopes. Do not grant the application access
   to that table or membership of reviewer/owner roles. Reviewers cannot create
   or change evidence. Explicitly verify positive and foreign-scope readback.
6. Switch only the approved target environment's application credential to the
   restricted writer after complete legacy privilege rehearsal. Hosted Staging
   and Production startup checks refuse missing/unsafe append permissions, owner
   or privileged memberships, schema CREATE, column leaks, missing RLS or missing
   mutation safeguards. Do not disable this check to keep an old owner credential.
7. Verify the real bucket's private-access controls and application IAM, including
   GetBucketPublicAccessBlock, GetBucketPolicyStatus, private object write/read,
   namespace-limited list/delete and the existing authorised delivery path.
   Synthetic S3 tests do not certify hosted IAM. Notify testers to sign in again;
   UAT normal read/regeneration, replay, support refusal and recovery access.

The implementation has not applied these hosted operations. Current database
owner credentials are intentionally unsuitable for a future hosted candidate.
No Staging/Production deployment, credential rotation or role provisioning is
authorised or asserted by this engineering handoff.

## Recovery and evidence limits

Retain both audit stores, original identifiers, clinical versions, quota ledger
and migration history during rollback. Restore schema/data plus role definitions,
memberships, grants, reviewer scope and enforcement objects into isolation, then
reconcile content and run positive/negative readback. Do not reopen these
operations on an application version that bypasses auditing. Never discard audit
history to downgrade code. Database owners can alter enforcement; this is not
independent tamper evidence.

The automated recovery scenario dumps a populated synthetic schema and role
definitions, creates an independent PostgreSQL container, restores them,
re-establishes synthetic login credentials, compares ten tables field-for-field,
and rechecks real writer/reviewer logins and allowed/denied clinical routes.
Elapsed dump, restore and reconciliation time and observed loss are recorded in
the evidence. These observations are not DEC-12 hosting, RTO/RPO, SLO, latency or
region commitments. They satisfy the approved bounded S0 evidence target only.

Segregated clinical storage and restricted access are delivered. ADR-013
independent signed-manifest export to separately administered storage remains
explicit follow-on work **before real clinical use**. DEC-10 retention and wider
DEC-12 decisions remain open. Independent specialist release approval, hosted
credential/IAM verification, Staging UAT and product acceptance remain release
gates; no parent or release story is closed by these tests.
