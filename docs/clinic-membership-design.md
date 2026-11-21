# GO3-42 / GO3-269 — Multi-clinic membership design draft v1

20 November 2026. Prepared after GO3-38 implementation verification. Product
design and the bounded administrative-membership scope approved by Eze Chiana in
chat on 20 November 2026; approval recorded on GO3-269. No professional-vetting decision or independent specialist
approval is inferred. Product owner: Eze Chiana. Execution: Codex across architecture,
backend, frontend/design and test disciplines. Security/privacy review: Healthcare
Security and Privacy Specialist. Clinical authority: Clinical Safety and Quality
Assurance Lead with the clinic operator. Coordination: Technical Programme Manager.

## Current boundary and proposed scope

Reuse immutable Organisation, Clinic and User IDs. Existing ClinicAssociation is
a patient's association to a clinic; it is not practitioner membership and must
remain unchanged. GO3-38 preserves the same principal across clinics and provider
callbacks. Existing owner-scoped QRMA records must not be reassigned to clinics
by email, display name or this migration.

CLINIC owns a unique (clinic_id, practitioner_user_id) membership with immutable ID,
status active/suspended/revoked, revision and attributable transition history.
Reactivation revises the same row. Never delete history or rewrite authorship.
One clinic's transition never changes the person's global active status, identity,
subscription or another clinic's membership.

Administrative membership is not itself a professional grant. Membership status,
clinical role approval, purchased seats and patient/record authority remain separate
decisions. Platform admin eligibility does not confer patient access. A membership
policy query returns the current server-side decision for the explicit clinic,
actor and action; fail closed if clinic/status/role approval is missing. Consumers
must check at execution time rather than trusting a cached token or selected UI.

Scope of this slice: directly manage memberships for existing practitioner accounts
under an administered clinic. New invitations and joining approval remain on their
own stories. Clinical context switching and full record isolation integrate through
their downstream stories; do not claim those workflows already exist.

## Decision that must be cleared

The approved blueprint D20 DEC-11 is **Open / Pending sign-off**. Its stated timing
is **Before granting privileged clinical roles**. It requires organization-specific
roles/credentials, separation of apprentice and approver on the same prescription,
and separate external dispensary vetting. Its owners are clinical lead, clinic
operator and security. Evidence must cover approval matrix, expiry, renewal and
appeal transitions. BR-006 requires per-clinic evidence/outcome; external dispensing
requires separate site approval. A product design approval alone does not clear it.

Approved bounded resolution for GO3-42: implement administrative membership and its
live suspension policy now; grant no new clinical/prescribing/dispensing authority
from membership activation. Keep DEC-11 approval explicit for later role grants.
Clinical activation remains unavailable until
the approval matrix and credential lifecycle are decided and verified. Never
auto-vet a practitioner because they can sign in or pay for a seat.

## API and concurrency proposal

| Action | Endpoint proposal | Authority |
| --- | --- | --- |
| List clinic members | GET /api/clinics/{clinic_id}/memberships | Current clinic administrator or explicit platform scope |
| Add existing practitioner | POST same collection | Same administrator; server validates target capability and verified/active status |
| Suspend/reactivate/revoke | PATCH /api/clinics/{clinic_id}/memberships/{id} | Same administrator; immutable clinic/subject, expected revision |
| Own memberships | GET /api/practitioner/memberships/me | Effective actor only; no other-person lookup |
| Resolve protected operation | Published CLINIC membership policy interface | Explicit clinic/action/actor; fresh database state |

Creation uses an actor-scoped request key and uniqueness; retries return the same
membership or a clear conflict. Transition and audit commit atomically, with
optimistic revision checking. IDs from the wrong clinic return a non-disclosing
refusal. Status updates cannot change identity, tenant or roles. A no-op retry
does not add duplicate transition history. Audit stores actor, membership and clinic
IDs, prior/new status, revision and outcome; no health values or credentials.
Impersonation cannot administer memberships.

## Seats and lifecycle

Accepted policy: clinical work consumes one practitioner seat per named clinic,
regardless of location count. Pending invitations consume no assigned seat;
purely administrative owners/receptionists consume no clinical seat. Activation
requires purchased capacity; last-seat allocation is atomic under contention.
SUBS owns purchased quantity and pricing under GO3-43, not the membership row.

Proposed mechanics: a separate allocation references membership and clinic. Lock
the clinic capacity boundary when assigning/releasing a clinical seat, verify live
purchased capacity, then commit membership/grant/allocation and audit together.
Do not invent purchased capacity while GO3-43 is unfinished. Synthetic engineering
fixtures may explicitly supply capacity; absent production capacity must refuse a
new clinical allocation. Administrative-only membership can exist independently.
Suspension/revocation stops protected membership operations immediately; paid
quantity never changes automatically. Seat release/reuse and reactivation must
be explicit, preserving allocation history and checking capacity again.

## Annotated flow

Account → Organisations & clinics → selected clinic → Members.

```text
Members — [clinic name]                         [Add existing practitioner]
Membership status controls clinic participation. Clinical approval and seats
are managed separately; an active membership does not grant patient access.

Practitioner        Membership       Clinical approval        Seat
Person A            Active           Not granted              Unassigned
[durable ID]         [Suspend] [Revoke]
Person B            Suspended        [read-only status]       [status]
[durable ID]         [Reactivate] [Revoke]
```

Add flow uses a known account identifier, not a public directory or unrestricted
email search. Show the clinic and practitioner before confirmation. Status
confirmation explicitly names the one clinic affected. Do not offer role approval
or capacity purchases through these buttons. Own-memberships view is read-only.

| State | UI outcome |
| --- | --- |
| Loading/empty | Labelled loading; empty explanation and permitted Add action |
| Forbidden/missing | Non-disclosing explanation; no actionable controls |
| Saving | Disable duplicate submission and retain confirmation context |
| Success | Refresh selected row and announce resulting status |
| Stale revision | Explain newer state; reload before further changes |
| Network uncertainty | Retain intent/request key; permit safe retry |
| Reactivation cannot obtain required clinical seat | Explain capacity restriction without changing paid quantity |
| Support impersonation | Read-only explanation; all writes server-refused |

Reuse current styling, native labels/buttons, visible focus, dialog focus return,
role=status confirmations and focused error summary. On narrow screens use member
cards; wrap UUIDs and retain clinic identity above every action.

## Required verification and release evidence

Persist one principal in A and B. Suspend A; its published policy denies A while B
continues; revoke/revoke retry and reactivation preserve identity/authorship.
Verify actual API decisions and attributable audit, not a UI badge alone. Include
wrong-clinic/member IDs, unauthorized actor, stale session, impersonation,
concurrent transitions, audit failure rollback and safe retries. Clinical seat
tests cover last-seat contention, pending invitation, administrative-only member,
multiple locations, suspended seat release/reuse and capacity recheck. Test delayed
work against current status at execution. No clinical data movement in migrations.

GO3-195 owns readiness/DEC-11 boundary. GO3-215 implements the reviewed contract.
GO3-269/275 own design/UI; GO3-237 verifies feature-specific adverse cases and
specialist conclusions; GO3-258 retains CI, migration rehearsal and Staging/UAT.
Neither this draft nor GO3-38 product approval closes those release gates.
