# GO3-38 implementation and verification record

20 November 2026. Candidate branch codex/practitioner-identity-foundation starts
from origin/main fc4cee4. Work is isolated from all existing DEV edits, including
the pre-existing staged parser rename. GO3-271 draft v1 was explicitly approved
by Eze Chiana; design approval is recorded on Linear.

## Delivered engineering scope

AUTH persists unique provider/subject bindings referencing existing User.id.
Repeated/concurrent callbacks resolve one principal and one personal record.
Provider email changes retain the original principal and sign-in email. A
different unbound subject cannot acquire an existing login by matching email.
New principal, binding, personal record, capability and minimal identity audit
commit atomically. Google verification requires a positive boolean claim.
The existing trusted Microsoft UserInfo endpoint remains the provider proof;
no email-based linking or professional vetting is inferred from it.

GET/PATCH /api/practitioner/profile/me and /app/profile provide the approved
self-service workflow. Active/verified practitioner-capable users can edit only
name, phone and valid IANA timezone. Target UUID, sign-in email, capabilities,
roles, clinic affiliations and billing are immutable through this API. Support
mode is read-only. GETs, updates and authorization/conflict refusals have minimal
audit records; schema-invalid requests fail validation before profile mutation.
No profile values or provider credentials/subjects are copied into audit metadata.
Conditional revision updates prevent lost writes; uncertain-response retries that
already match current values are safe. Audit failure rolls back profile changes.

Existing report branding/preferences remain separate. The page reuses the shell,
adds a Settings/Account entry, retains inputs on errors, focuses the error summary,
announces success, disables support-mode editing and wraps identifiers on mobile.

## Observed evidence

Disposable PostgreSQL 18, real password/JWT authentication and Chromium; synthetic
data only. Prior baseline: 4 signup tests passed. The expanded regression run
passed 129 checks and failed one newly added migration fixture because its raw
legacy insert omitted required recommendation mode/timezone. Fixing the fixture
gave a passing migration replay. Final focused run: **17 passed**, including
10 identity/API/concurrency/migration tests, four signup/personal-record tests and
three actual-browser tests. A subsequent browser run passed all four browser tests,
adding desktop keyboard save and read-only identifier checks: **18 distinct focused
checks passed across the final runs**. No skips in these runs. Dependency deprecations remain.

The expanded regression checks cover S0 isolation/QRMA golden and actual PDF
generation, registry/API/browser, module contracts, migrations, claims, shares,
admin entitlements and report controls. Local XML and synthetic mobile screenshot
are under artifacts/ (not production data and not a hosted CI result). Visual
inspection found and corrected a shared-CSS hidden-button issue; the final mobile
screen and hidden-state regression were checked again.

The migration test creates a populated prior-schema/ledger equivalent, applies
20261120_practitioner_identity.sql, checks the user's UUID/name remain unchanged,
checks that no provider identity was guessed, preserves all earlier ledger
timestamps and proves the second replay leaves the ledger unchanged. Historical
migration filenames are unchanged. This is synthetic rehearsal, not a fresh
Staging schema snapshot rehearsal.

Release review on 20 November found that legacy auth routes exposed development
reset/verification links in all non-production environments, including Staging.
The release hardening restricts those links to development/dev/local/test only;
Staging, Production and unknown environments return no mailbox proof. Regression
checks exercise forgot-password, registration and verification resend, including
identical generic known/unknown reset responses in hosted environments. This
change is required before the authorized Staging rollout.

CI now includes the feature-specific backend and browser suites. Hosted CI,
independent specialist release review, Staging snapshot rehearsal/deployment and
UAT remain outstanding on GO3-233/255; no deployment or commit is asserted here.

## Compatibility and rollout limits

Legacy users have no new binding by default. Their email/password and password
reset remain available; social login refuses an unbound subject whose email
matches an existing account and displays a clear sign-in/reset instruction.
There is no self-service provider-linking UI in this slice. Before release,
quantify legacy social-only account impact using aggregate counts without
exporting identifiers and verify password-reset delivery on the target environment.
Do not silently backfill from email or claim this compatibility assessment done.

Migration is additive; do not drop identity bindings or profile audit evidence
to roll back an application. An older email-based social implementation is unsafe
after a bound provider changes email: it could create a second principal. A
rollback therefore requires blocking social start AND callback routes at the
ingress (including outstanding callbacks), retaining email/password/reset access,
and restoring a binding-aware implementation before reopening social sign-in.
The old implementation's start-only feature toggle is not a callback barrier.
This operational rollback has not been rehearsed on hosted infrastructure.

## GO3-42 readiness

The next design is in clinic-membership-design.md. Existing patient clinic
associations must not be reused as practitioner memberships. The source DEC-11
decision remains open before privileged clinical roles; the proposed next slice
manages administrative membership and live suspension without inventing clinical
approval, purchased capacity or patient access. Product scope/design acceptance
and clinical/security conclusions remain explicit gates for their respective work.
