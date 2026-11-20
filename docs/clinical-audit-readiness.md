# GO3-36 clinical audit readiness and verification design

20 November 2026. Trace: GO360-012 / G360-08-001 / T-GO360-012.
Scope: S0 / M0 synthetic two-practice engineering demonstration.

This is a proposed implementation contract, not an implemented audit control or
specialist approval. Eze Chiana has approved one protected read, one versioned
amendment and the fail-closed policy. GO3-189 still requires technical disposition
and reviewer assignment before dependent GO3-208 implementation. No application,
schema, deployment, real patient data or runtime-role changes have been made.

## Source and checkout reconciliation

Read GO3-36, GO3-189, GO3-208, GO3-231 and GO3-250, including comments and relations,
and PM-01 (GO3-6). Read the original D10 ADR-013 and D20 DEC-12 in the approved
GO360_Modular_EHR_Blueprint_2026-11-19.zip; the imported generic story is not the
technical contract. ADR-013 says to secure read evidence before disclosure,
write minimal audit alongside mutations, segregate access logs and export signed
manifests independently. Its requirement is accepted; its design is proposed.
D20 DEC-12 remains open: hosting, recovery and service objectives, with engineering,
operations, privacy and clinical owners signing before S1 data onboarding.

Root DEV is main at 3a80de9 with substantial pre-existing changes, including a
staged parser rename. It is not a clean integration baseline. This isolated
branch, codex/clinical-audit-readiness, starts at 4a527ac (the practitioner identity
candidate on PR #2), which includes the latest authentication, membership and
legacy-identity safeguards. PR #2 is a prerequisite, not silently merged here.
origin/main was fc4cee4 at the preceding identity release preparation. Revalidate
ancestry before preparing a delivery candidate. Root edits and index are untouched.

## Workflow selection for technical approval

### Protected read

Propose **GET /api/reports/{report_version_id}/json** in
app/api/routes_reports.py:get_report_json. It releases the persisted report JSON
through the ordinary authenticated API, rather than a synthetic service-only
entry point. get_current_user in app/api/deps.py validates the bearer token or
access-token cookie through auth_service, checks the active/verified account,
and excludes client accounts from practitioner paths. _get_owned_report joins
ReportVersion to Case and requires Case.user_id == current_user.id. Missing and
foreign reports both return 404 {"detail":"Report not found"}. An owned report
without JSON returns the existing 409 and discloses no clinical JSON.

There is currently no durable access event on this route. Proposed sequence:
authenticate; owner-scoped lookup; build a minimal decision event; flush and
commit that event; only then construct/return clinical JSON. Snapshot the
authorised response before commit without serialising it into the audit. An
authenticated ownership denial also commits a minimal denial before the same
404. Do not perform an unrestricted lookup to populate a denial with the foreign
record's patient, owner or clinic. An unknown/foreign target gets no resolved
target reference; use controlled reason `not_found_or_not_owned`.

If allowed-read audit commit fails, roll back and return a sanitised 503 without
clinical JSON. If denial auditing fails, keep the same non-disclosing 404 and
emit a sanitised operational failure signal. No raw exception, SQL parameters,
clinical values, token or request body in that signal. Correlate with a newly
generated server UUID. Denied dependency-level client/inactive/unauthenticated
requests are separate from the selected handler boundary; do not claim these
are audited by the proposed handler. Include authenticated wrong-practice
requests in scenario A.

This slice does not audit every alternative disclosure path: /viewer,
/viewer-data, /pdf, /html, client releases and public shares remain separate
follow-on coverage. Do not call the whole product's read auditing complete.

### Versioned amendment candidate and incompatibility

Propose **POST /api/cases/{case_id}/generate-report** in
app/api/routes_cases.py:generate_report, specifically regeneration of a case with
an existing report. It is the existing practitioner refresh/new-version action.
It preserves old ReportVersion rows and takes an optional UUID Idempotency-Key.
The owner check is _get_owned_case (Case.id and Case.user_id). The route also
enforces the existing report_generation entitlement and requires uploaded HTML.

Clinical Informatics must confirm that report regeneration represents the
approved *clinical amendment*. It is not an editable clinical note or sign-off.
PATCH /api/reports/{id}/overrides and PATCH /api/cases/{id} mutate in place;
neither qualifies as an existing versioned amendment. Do not silently select
either or invent a new note workflow to satisfy the acceptance wording.

Current call chain: generate_report_version -> create_report_version ->
build_report_version. Creation locks the user in check_assessment_slot, allocates
max(version_number)+1, reserves quota and commits a queued row. report_queued is
committed separately. The builder commits processing, obtains practitioner
settings (another helper can commit), writes HTML/PDF outside PostgreSQL, commits
ready + consumed quota, then separately commits report_generated. Failure can
persist a failed version. The route rolls back on exception but cannot undo
those earlier commits; its generic error currently includes str(exc).

Replay: request keys are browser:<UUID>; quota uniqueness is (user_id,request_key).
Consumed reservation returns the existing report; reserved returns 409; a
released reservation permits retry. The user row lock serialises allocation.
The wrapper skips rebuilding an existing ready report. Preserve these semantics,
five Foundation/Free monthly Assessments and unlimited eligible paid/trial
usage; do not use subscription or admin status as a clinical access grant.

Proposed bounded implementation after review: add an explicit caller-owned
transaction mode to the existing service for the selected regeneration route,
defaulting to legacy behaviour for imports and connector/background callers.
Refactor all transitive commits (including settings and audit helpers) for that
mode. Keep quota locking/idempotency and all new version/ready state/quota/success
event writes in one transaction; rollback removes the candidate version on audit
or amendment failure while preserving the previous version. Replay returns the
original version and success event without creating a second one. A unique
success-event key bound to resulting version enforces this across retries.

This changes the selected route's *failure* persistence to meet the approved
rollback rule; it must not accidentally change connector/import semantics.
Check connector_confirmation.py's existing ConfirmationSession adapter, which
converts nested commits into flushes and commits at the connector boundary.
Do not accidentally bypass that adapter or commit it early. Do not hold a
public artifact URL in an unaudited response. External artifact writes are not
rolled back by PostgreSQL: review staging/cleanup of candidate artifacts and
ensure failed candidates cannot overwrite prior version files. Test failure
after artifact creation as well as before it. No claim of database atomicity
for S3 itself. Redact generic exception details on the selected route.

## Trusted scope and support attribution

Clinical Case and Patient currently have user_id ownership, not clinic_id.
ReportVersion inherits Case ownership. ClinicMembership is administrative and
explicitly grants no clinical authority; patient clinic associations are not a
chart ownership or practitioner clinical grant. Names, posted clinic IDs, user
profile clinic_name, billing scope and an arbitrary one of several memberships
must never be used to infer clinical ownership.

Recommended S0 disposition: retain the established owner/practice boundary and
record `scope_kind=legacy_practice` plus trusted practice-owner UUID. Organisation
and clinic UUIDs remain null with controlled scope reason `legacy_unresolved`.
The two fixtures represent two practices; do not present their labels as verified
clinical clinic lineage. If acceptance requires non-null clinical clinic IDs,
an explicitly reviewed clinical ownership mapping is a prerequisite. It is not
a safe incidental backfill in this story. GO3-189 expressly permits recording
unresolved legacy scope rather than guessing. Obtain technical agreement here.

Effective actor comes from the validated session subject. Support mode currently
issues a normal target access token plus an admin_restore_token cookie. Existing
membership routes validate that restore token through restore_admin_from_token;
selected report routes do not yet do so. The restore token is not bound to a
particular target and its helper does not check the purpose claim. Proposed
support attribution must verify purpose, active admin and linkage to the effective
target, not accept a client-posted UUID or the mere presence of a cookie. Review
whether a server-side support-session binding is necessary. Until reliable
attribution is established, explicitly refuse the selected audited clinical
operations in support mode; do not silently attribute them solely to the target.
This restriction and its consuming workflow need approval and a regression test.

## Proposed event and storage contract

CORE owns append and restricted evidence retrieval. Proposed fixed fields:

| Field | Source and constraint |
| --- | --- |
| id, occurred_at | Server UUID and database UTC timestamptz; stable after commit |
| action, outcome | Enumerated report.read allowed/denied or report.version_created success |
| effective_actor_id | Validated session User.id, immutable snapshot |
| initiating_actor_id | Verified bound support principal, otherwise null |
| scope_kind, practice_owner_id | Trusted legacy practice boundary; never input from the client |
| organisation_id, clinic_id, scope_reason | Only authoritative resolved scope; otherwise null/legacy_unresolved |
| target_type, target_id | report_version/case; opaque authorised IDs only, null on unresolved denial |
| resulting_version_id | Required for amendment success; preserved on replay |
| correlation_id | New server-generated UUID per request; replay returns original event linkage |
| reason, schema_version, event_key | Controlled enum, 1, unique amendment success key |

No arbitrary metadata extension. Exclude clinical report JSON, patient names,
email, IP unless separately justified, request bodies, headers, credentials,
tokens and exception strings. Validate allowlists at the append boundary and
constrain action/outcome/required field combinations in PostgreSQL.

AuditLog/log_action currently accepts free-form metadata and commits + refreshes
its Session. Connector audit already uses a caller-owned append without commit,
but is optionally disabled and connector-specific: it cannot gate clinical audit.
Preserve log_action's default legacy behaviour. Propose an explicit non-committing
append path for caller-owned clinical writes; no ambient mode or hidden commit.

Recommended storage for review: a dedicated clinical_audit_events table with
immutable identifier snapshots, separate from mixed legacy/connector audit_logs.
This implements logical access/change-log segregation without rewriting legacy
records. AuditLog's user/case/report foreign keys use ON DELETE SET NULL; a blanket
append-only trigger there would conflict with deletion and existing lifecycle
operations. Do not install such a trigger without reviewing that impact. The
dedicated table must not use cascading/nulling FKs that rewrite its snapshots.
Cross-domain IDs are evidence, not a grant or a second copy of clinical data.

## Database controls and restricted readback proposal

No reviewed runtime-role provisioning or clinical audit append-only/RLS migration
was found in the candidate. SessionLocal uses DATABASE_URL; absence of provisioning
in code is not proof of deployed role privilege. Inspect actual role privileges
at approved rollout, without exposing credentials. Do not assume Railway's default
database account is a safe writer.

Propose a distinct NOLOGIN migration owner, a non-owner/NOSUPERUSER/NOBYPASSRLS
runtime writer, and a separate restricted reviewer identity. Writer may INSERT,
not UPDATE/DELETE/TRUNCATE; must not own table/schema or inherit owner/reviewer
roles, alter triggers, assume owner or disable row security. Add mutation and
truncate refusal triggers owned by the migration owner as defence in depth.
Review INSERT RETURNING/refresh needs: prefer server-known IDs and a controlled
append function rather than granting broad readback to the app for ORM refresh.
No public application audit query, edit/delete route or new viewer in this slice.

Reviewer access is separately provisioned with explicitly assigned practice/clinic
scope and SELECT only (reviewer cannot INSERT or mutate). If RLS uses session
context, an ordinary writer must not be able to self-assign reviewer scope with
SET or SET ROLE. Test with distinct login roles and allowed/foreign/unknown scope,
not only as the superuser fixture. Restrict evidence extraction to the named
reviewer role; exported acceptance samples omit patient content and operational
identifiers unless needed for synthetic correlation. Broader custody/retention
policy remains DEC-10; no automatic audit deletion policy is introduced here.

## Migration and recovery plan for review

Add a new filename-ledger migration, preserving every historical migration and
AuditLog row/ID. Separate schema-owner execution from runtime credentials. Test
the existing migrate.sql_statements splitter for trigger/function dollar quotes,
schema-qualified role objects and unchanged second replay. Provision cluster
roles through an explicit idempotent operations step; never embed passwords or
assume permissions from Base.metadata.create_all. Rehearse populated synthetic
legacy audit rows, constraints, role privileges, triggers and grants. Before
rollout use a fresh representative Staging schema and ledger without patient data.

Recovery is forward-compatible: retain both audit tables, ledger, versions and
ownership history; never drop new evidence to downgrade code. Restore schema,
data, append-only functions/triggers, role memberships and restricted reviewer
grants into an isolated database with a separate runtime login. Do not reopen
clinical writes with an old version that bypasses mandatory auditing. A storage
backup alone does not prove role restoration or independent tamper resistance.

Proposed DEC-12 disposition: a timed synthetic restore with content/version and
authorization/audit reconciliation is sufficient engineering evidence for this
bounded S0 slice only. It is not a hosting approval, RTO/RPO/SLO promise, region
decision, real-data approval or closure of wider DEC-12. Operations/privacy/clinical
owners must accept that disposition. Proposed ADR-013 disposition: dedicated
clinical event storage and separate permissions now; independent signed-manifest
export to separately administered storage remains explicit follow-on work before
any release requiring that guarantee. Its deferral needs architecture/security
agreement, with no claim that local triggers protect against a database owner.

## Feature verification design

All scenarios use normal selected routes and synthetic fixtures; PostgreSQL is
mandatory for atomicity and roles. Use real signed synthetic bearer tokens for
at least the authentication/attribution checks, not just dependency overrides.

| Test | Expected observation and failure controls |
| --- | --- |
| A | A reads B and a nonexistent UUID: identical 404/body, no payload or foreign scope; each durable denial attributed to A with controlled reason and server correlation. Forged clinic/correlation/actor input does not change evidence. |
| B | A reads A: observer on a second connection sees the committed allowed event before response disclosure. Block audit flush/commit: no clinical JSON; safe 503 and sanitised correlated operational signal. |
| C | Existing report regenerates: exactly one new committed ready version, quota settlement and success event; prior version contents/hash unchanged. Repeat key returns original version/event. Parallel same-key requests and distinct-key version allocation preserve existing lock/retry semantics. |
| D | Ordinary HTTP actor has no audit CRUD/readback route; direct runtime login cannot update/delete/truncate/alter/disable triggers or grant itself reviewer privileges. Reviewer reads only assigned scope and cannot append/mutate. Failure is a real privilege failure, not a missing-row result. |
| E | Independently fail audit flush, audit commit, build, version insert and final version commit. Verify preceding version intact, no committed candidate/quota consumption or false success; safe retry after recovery yields one success. Fail after external artifact creation; no overwritten prior artifact or usable unaudited link. Repeat dependency denial/support-attribution negative cases. |
| F | Dump/restore the synthetic database and globals/role provisioning into a fresh isolated target. Compare counts and canonical hashes of versions and all audit IDs/fields, ledger history and ownership. Re-run A/B/D plus retry. Record elapsed dump/restore/reconciliation separately and observed missing rows/values; zero is an observation, not a guaranteed RPO. |

Run existing S0 backend suite, Chromium suites and deliberate wrong-clinic
negative control. These baseline checks do not substitute for A-F. No new browser
suite is needed for a contract-only append mechanism; the existing refresh/read
flow must be checked if errors/support restrictions or response behaviour change.

## Execution, reviews and remaining decisions

Engineering reconnaissance/test design: Codex. Proposed implementation executor:
Codex in Senior Backend Engineer/Test Automation Architect roles, coordinated by
Eze Chiana (Product Owner/Technical Programme Manager). This is not independent
specialist sign-off. Prior user-directed role-based assignments on registry work
do not imply new clinical approval for this slice.

Required reviewers/dispositions before GO3-208:

1. Principal Software Architect + Clinical Informatics Architect: selected JSON
   read and whether regeneration is the intended clinical amendment; accept
   legacy-practice/null-clinic scope or supply an authoritative mapping; approve
   transaction/artifact and support attribution boundaries.
2. Database Architect + Healthcare Security and Privacy Specialist: dedicated
   table vs additive legacy-table approach; writer/owner/reviewer privileges,
   scoped readback, immutable snapshots and migration/restore mechanism. Name
   the assigned reviewer(s), or explicitly record the accepted role-based
   execution arrangement; no fictional individuals or independent opinions.
3. Clinical Safety and Quality Assurance Lead: review no-disclosure/no-partial-
   amendment hazards, prior-version preservation and recovery/retry behaviour.
   DevOps and Platform Architect with DEC-12 owners: accept bounded synthetic
   recovery evidence and explicit ADR-013 export/segregation disposition above.

Product scope and fail-closed policy are already approved; do not ask for them
again. No specialist technical conclusion, wider DEC-12 clearance, release or
story closure is inferred from this record. GO3-208/231/250 feature implementation,
acceptance and release evidence remain outstanding until these gates are settled.

## Baseline verification and handoff

Executed against the unchanged runtime at 4a527acf14d5140bcdfa5251e348084bf015385f
in this isolated worktree, using a fresh disposable PostgreSQL 18 container and
synthetic credentials/data only. No DEV, Staging or Production database used.

| Check | Observed |
| --- | --- |
| Existing S0 backend command from s0-regression.yml | 140 passed, zero failures/errors/skips; 77.71 seconds |
| Existing five Chromium suites from the same workflow | 33 passed, zero failures/errors/skips; 72.55 seconds |
| GO360_S0_INJECT_WRONG_CLINIC=1 owner-list negative control | Two expected assertion failures, exit 1; harness correctly rejects wrong practice |
| Hosted CI on unchanged base 4a527ac | Run 44705440642 completed successfully; existing baseline evidence only |
| New clinical audit scenarios A-F | Not run: dependent implementation and technical dispositions outstanding |
| Clinical audit migration/replay, runtime-role restrictions and restore | Not run: no clinical audit migration/control implemented or approved |
| Clinical audit feature CI / Staging UAT / Production deployment | Not run; none authorised by this slice |

The local baseline emitted dependency deprecation warnings (Starlette/anyio,
httpx and websockets); no failing baseline check was observed. JUnit and the
negative-control output remain under this worktree's artifacts/ directory.
The checked-in evidence summary records their hashes and counts, not clinical
payloads. No new feature acceptance or specialist approval follows from a green
baseline. No candidate deployment or story closure occurred.
