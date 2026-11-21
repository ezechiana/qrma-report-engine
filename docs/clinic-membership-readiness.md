# GO3-42 administrative membership implementation record

20 November 2026. Product design and administrative-only scope approved explicitly
by Eze Chiana in chat and recorded on GO3-269. This candidate includes the GO3-38
identity/profile foundation in the isolated codex/practitioner-identity-foundation
worktree based on origin/main fc4cee4. Existing DEV edits and its staged parser
rename are preserved; no commit or deployment is asserted by this record.

Execution: Codex in architecture, backend, frontend/design and test roles. Product
acceptance: Eze Chiana. Healthcare Security and Privacy Specialist owns specialist
review; Clinical Safety and Quality Assurance Lead and clinic operator retain
authority over professional approval. Technical Programme Manager coordinates
release. No independent human conclusion or clinical approval is invented.

## Delivered scope and API

One immutable membership identifies one User.id in one Clinic.id, with independent
active/suspended/revoked status and revision. Reactivation preserves that row and
history. Organisation-owner administration is the existing registry authority
adapter; explicit platform scope may administer memberships, but does not bypass
the member's participation policy. Membership alone does not make an administrator.

* GET /api/clinics/{clinic_id}/memberships: scoped administration list.
* POST that collection: add an existing active/verified practitioner by UUID,
  requiring Idempotency-Key and X-Expected-Actor. No email directory or invitation.
* PATCH /api/clinics/{clinic_id}/memberships/{membership_id}: scoped status command,
  expected revision and X-Expected-Actor. Strict body fields prohibit identity,
  tenant, role, clinical approval and capacity changes.
* GET /api/practitioner/memberships/me: own states, including suspended/revoked,
  with no other-person lookup.
* GET /api/clinics/{clinic_id}/membership-context: real protected membership
  participation query. Calls the live CLINIC policy and audits allowed/refused.
  Suspension/revocation in A denies A while B's active membership continues.

The published Python interface is
`app.services.clinic_membership.membership_decision(db, principal_id, clinic_id, action)`.
It returns immutable MembershipDecision with policy_revision membership-admin-v1.
Only membership.context is supported; clinical and unknown actions are refused.
It refreshes live user/membership state even with an older ORM instance loaded.
It neither commits caller transactions nor creates clinical authority. Future
consumers must re-evaluate at execution time and own their action/audit transaction.

**Boundary:** existing QRMA/clinical/patient APIs have not been reassigned to clinic
membership or retrofitted with tenant isolation here. Their downstream context,
record authority and professional-role stories remain necessary. The verified
protected operation in this slice is membership-context, not a claim that every
legacy clinical operation now obeys membership suspension.

## Transactions, audit and UI

Creation is unique by clinic/principal and actor/request key. Actor NO KEY UPDATE
and clinic locks serialize replay/duplicate adds without blocking reciprocal
administrator foreign-key checks. Row locks and revision checks serialize state
changes. Retry of an already achieved state is safe and creates no duplicate
transition event. Creation/status and audit commit together; audit failure rolls
back both. IDs, ownership, prior/new status, revision and decision are attributable;
names, health values, credentials and provider subjects are absent from events.
Wrong-clinic/unauthorized access is non-disclosing. Support-mode writes are denied.

Organisations & clinics has Manage members links; Account has My clinic memberships.
Actual UI supports add confirmation, Suspend/Reactivate/Revoke, read-only own and
support views, empty/loading/error/retry/conflict states, native dialog keyboard
behaviour, focus return and mobile cards. The actor-scoped tab journal retains
only command identifiers, status/revision and creation key, not names or secrets.
Unknown commit outcomes reuse the same command/key. Reload can reconcile a
successful uncertain write. Another account cannot execute an old form because
the API compares its expected actor with the live authenticated actor. Journals
are cleaned on authentication loss and isolated by account/clinic.

## Observed checks

Synthetic PostgreSQL 18 and Chromium with real password/JWT sessions:

* Focused membership run: 12 passed (eight backend/migration, four browser).
* Expanded S0/identity/membership/API/browser regression: **143 passed**, no skips.
  Includes existing QRMA golden/PDF, claims/releases/shares, registry, entitlements
  and identity/profile checks. Dependency deprecations remain.
* Subsequent final lock adjustment: **nine backend checks passed**, adding the
  reciprocal-administrator concurrency scenario. Strengthened cached-object
  revocation check passed separately. These final checks follow the 143-test run;
  do not describe the combined candidate as a single 144-test run.
* Browser tests cover mobile add/status operations, keyboard confirmation and
  Escape/focus return, committed-but-lost-response retry with identical key,
  stale revision, changed-login refusal, own and impersonated read-only views.
  Initial dialog-focus failure was corrected and rerun. Mobile screenshot visually
  inspected with no overflow; irrelevant commercial banners suppressed on these
  administration pages without changing billing or entitlements.

Local evidence: artifacts/foundation-membership-regression.xml,
membership-focused.xml, membership-locking.xml and clinic-memberships-mobile.png.
CI includes the new backend/browser suites; no hosted CI result is asserted yet.

## Migration and rollout

20261120_clinic_memberships.sql creates only membership/event tables and indexes.
No existing ledger filename changes; no automatic memberships, seat allocations,
credential approvals, ownership moves or user identity changes. Populated synthetic
prior-schema/ledger rehearsal preserved clinic/organisation/user references and
all previous ledger timestamps; second replay was unchanged. A fresh Staging
schema/ledger rehearsal remains outstanding before rollout.

Roll back by withholding the new routes/UI and dependent consumers; retain tables
and attributable history rather than dropping them. Older application versions
must not become authority adapters for new clinical consumers. The separate
GO3-38 social-binding rollback restriction also applies to this combined candidate.

## Remaining gates

Commit/review, hosted CI, independent specialist release review, fresh Staging
schema/ledger rehearsal and Staging/UAT remain on GO3-237/258 and GO3-233/255.
The story remains open through those gates. No shared/hosted database was changed.

DEC-11 remains open **before privileged clinical roles**. Active membership reports
clinical_access=false, clinical_approval=not_granted, seat_status=unassigned.
The seat labels describe this slice's absence of clinical allocation, not an
evaluation of future purchased capacity. No subscription quantity is invented or
changed. Purchased capacity, clinical seat allocation/last-seat contention and
professional-role/credential approval must integrate under their reviewed contracts
before clinical work is enabled. This is the approved administrative boundary,
not completion of the broader clinical vetting or commercial-seat implementation.
