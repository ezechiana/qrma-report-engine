# Clinic identity foundation (GO3-35)

Requirement G360-01-001 / T-GO360-008. CLINIC owns the organisation and clinic registry. Organisations and clinics have independent UUIDs; labels are not identities. A clinic belongs to one organisation. This is an administrative registry, not a clinical permission, practitioner membership, location, subscription or seat assignment.

## API and policy revision

`clinic-registry-v1` requires an active, email-verified practitioner (including an explicit practitioner capability) or platform administrator. An organisation's creator is its initial administrative authority. Other practitioners receive 404 for its clinic operations. Platform administrators use the existing authoritative platform-admin gate. No original administrator's privileges are inherited during impersonation: the effective authenticated user is evaluated. GO3-42 will replace the initial creator adapter with explicit membership policy; creation history must remain attributable separately from membership.

* `GET /api/organisations`: organisations administered by the caller; explicit platform administrators can see all.
* `POST /api/organisations`: create a named organisation.
* `GET /api/organisations/{organisation_id}/clinics`: clinics within the authorised organisation.
* `POST /api/organisations/{organisation_id}/clinics`: create a clinic within that organisation.

POST bodies contain only `display_name`. A UUID `Idempotency-Key` header is required. The creation key is scoped to the actor and resource type. Same key and normalized input returns the original ID (200); a fresh key creates another ID (201), even with the same label. Reusing the key with a different name or parent returns 409. Authority is checked again on every retry. Unknown body fields, invalid keys and blank names fail validation. IDs are server assigned and PostgreSQL triggers reject changing them.

Provisioning locks the actor row until commit, serializing concurrent retries. Unique constraints additionally protect creation keys. Registry mutation and its audit event commit together. Audit failure rolls creation back. Authorisation refusals and conflicting retries record a denial without retaining the submitted label or inaccessible target IDs. Framework-level authentication/input-validation failures do not create registry events. Events contain actor/resource IDs, action, outcome, policy revision and check time, without clinical values or request payloads.

## Additive legacy mapping

New migration: `20261119_tenant_organisations_clinics.sql`. Existing ledger filenames are unchanged. Each existing practitioner/admin, practitioner-capable account or account with practitioner settings receives a deterministic organisation and clinic mapped by `legacy_owner_user_id`. Identical clinic labels never merge accounts. Practitioner settings supply the label first, then the user clinic label, then `Legacy practice`. Original source fields and creation history remain intact. This mapping records the source owner, not credential verification or professional approval.

Patient/case/report/share IDs, author and owner fields, relationships and historical artifacts are untouched. Until GO3-42/45 introduce membership and tenant-scoped data access, the existing practitioner ownership checks remain authoritative. The legacy owner reference supplies a stable bridge for that later migration; this release does not move records or grant access through a matching name.

## Verification and rollout

`tests/test_clinic_registry.py` uses a disposable PostgreSQL database selected by `GO360_CLINIC_TEST_DATABASE_URL`. It creates and removes a unique schema per test. The suite verifies equal-name identities, retry and conflict semantics, concurrent retries, scoped discovery, cross-organisation denials, privileged provisioning, ineligible and anonymous actors, immutable IDs, audit rollback/retry, transactional DDL rollback, repeatable backfill and preservation of an existing ledger entry.

Local PostgreSQL 16 rehearsal on 19 November 2026: initial fixture setup failed because separate table creation attempted to create the shared recommendation enum twice. The fixture now creates the legacy tables together. No production migration failed. The corrected clinic suite and S0 suite pass on the isolated candidate built from committed HEAD plus only this story's changes. The existing QRMA references are unchanged.

The CI workflow includes disposable PostgreSQL and the new tests. Hosted CI, peer review, fresh Staging schema/ledger rehearsal and Staging UAT remain release evidence to collect; no hosted environment has been changed. DEC-11 professional vetting scope remains open and is not represented as cleared by administrative registry creation. Keep GO3-35 In Progress pending its decision/release gates.

On 19 November 2026 Eze Chiana supplied independent release approval for the current foundation/readiness and approved-design candidate and requested its commit. GO3-251 records that approval. GO3-188 has settled engineering readiness and reviewer-role assignment; professional privileges remain outside registry registration. Production UI implementation, hosted CI, fresh Staging/UAT and deployment are still separate work; GO3-35 remains open.

Rollback rehearsal proves the new DDL and backfill can roll back together before commit. After deployment, application rollback can leave these additive tables in place without affecting legacy routes. Do not delete registry tables after users have provisioned clinics: preserve evidence and reconcile those records before any destructive rollback.
