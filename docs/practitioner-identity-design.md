# GO3-38 / GO3-271 — Practitioner identity and profile, draft v1

19 November 2026. Product design approved by Eze Chiana in chat on 19 November
2026; approval recorded on GO3-271. Implementation verified locally on 20 November
2026; independent release review and hosted acceptance remain separate. Execution: Codex in architecture,
backend, frontend and design roles. Product acceptance: Eze Chiana.
Security/privacy review responsibility: Healthcare Security and Privacy Specialist.
Verification: Test Automation Architect and Clinical Safety and Quality Assurance
Lead as applicable. Release coordination: Technical Programme Manager.
These responsibilities do not claim independent review has occurred.

## Findings and boundary

The candidate starts at origin/main fc4cee4. Existing User.id is a durable UUID;
retain it as the authenticated principal and practitioner reference. Never create
a new principal for another clinic, location, subscription or profile revision.
User capability records describe enabled workflows, not professional vetting.
CLINIC owns the practitioner profile and, in GO3-42, memberships; AUTH owns
provider bindings and authentication. SUBS owns plans and purchased seats.

Current social login resolves by normalized email. provider_subject is passed
through but is not persisted as a unique identity binding. Existing settings
are report branding/preferences, not a controlled identity-profile contract.
Reusing them indiscriminately would mix clinic branding with the person's name.

## Proposed contract

* Preserve all existing user IDs, clinical authorship and subscription records.
* Persist a unique trusted provider/subject binding to User.id. Require a nonempty
  subject and a positively verified provider identity; missing proof fails closed.
  Provider scopes must match the configured OAuth issuer/client contract.
* Resolve an existing binding before considering email. A later provider email
  change must not create a second user or silently change the sign-in email.
* Do not silently bind an unrecognized provider subject to an existing account
  solely because email matches. Require an authenticated linking flow or return
  a safe sign-in instruction. No account-linking UI in this slice.
* Create new social principal, binding and required signup records atomically.
  Concurrent callbacks converge through database uniqueness and bounded retry;
  no orphan principal, capability or personal record on failure.
* Profile GET/PATCH /api/practitioner/profile/me resolves its target exclusively
  from the effective authenticated actor. No target ID parameter or mass assignment.
* Allow name, optional phone and IANA timezone. Reject all other write fields,
  including id, email, role, capability, membership, subscription and credentials.
  Validate lengths, whitespace and timezone before mutation.
* Restrict to active, verified practitioner-capable principals, including an
  eligible platform admin's own profile. Support impersonation reads the effective
  user's profile; writes are disabled and server-refused during impersonation.
* Use an explicit integer profile revision for optimistic concurrency. A stale
  write returns 409 without overwriting another edit. A no-op retry is safe.
* Commit profile and audit event together. Store actor/subject IDs, field names,
  outcome and revision, with original admin context for refusals; omit profile
  values, provider tokens, raw subjects, clinical values and request bodies.
* Audit authorization refusals without changing the target. Unauthenticated
  requests disclose no profile. API responses must not disclose other users.

## Annotated flow and wireframe

Account / Settings → Personal profile (existing application shell and styling).

```text
Personal profile
Your profile identifies you across clinics. Clinic roles are managed separately.

Practitioner ID    [server UUID, read only]
Sign-in email      [current email, read only]
Full name          [____________________________]
Phone (optional)   [____________________________]
Timezone           [Europe/London               ]

[Save profile]     [Cancel changes]
Status / field errors appear here.
```

The page must not imply that changing a name verifies professional credentials.
It does not expose another person's profile to clinic administrators. No email
change, role assignment, professional credential, paid-plan or membership controls.
An administrator changes their own profile through the same self-service rules.

| State | Presentation and action | Contract |
| --- | --- | --- |
| Loading | Labelled progress; form unavailable | GET own profile |
| Ready | Read-only identifiers; labelled inputs | Save current revision |
| Dirty | Save enabled; cancel restores loaded values | No background mutation |
| Saving | Prevent duplicate submit; retain entered values | PATCH allowed fields |
| Saved | Polite live-region confirmation | Render returned revision |
| Invalid | Field error and focused error summary | 422; no changes |
| Conflict | Explain newer changes; Reload action | 409; no automatic overwrite |
| Network/server error | Retain values; Retry action | Never report success on uncertainty |
| Signed out | Sign-in action, no identity data | 401 |
| Forbidden | Clear access explanation | 403 |
| Support impersonation | Read-only form with support-mode explanation | PATCH 403 |

Use native labels and controls, logical keyboard order, visible focus, and
aria-describedby for field errors. Error summary receives focus; success uses
role=status. Single-column layout at narrow widths, wrapping UUID/email without
horizontal scrolling. Render names as text, never HTML.

## Implementation and evidence plan

GO3-191 settles this contract. GO3-271 records product design review. GO3-212
implements the approved contract and UI. GO3-233 owns normal/adverse verification;
GO3-255 retains independent release approval and Staging acceptance separately.

Migration adds provider bindings and profile revision without renaming existing
ledger files or deriving historical bindings from email. Rehearse fresh and
existing-schema replay; verify second replay changes neither ledger history nor
IDs. Record legacy social users as requiring controlled first binding, not guessed
provider identities. Quantify this compatibility impact before release.

Required tests: replay and concurrent provider callbacks; provider email change;
same email/different subject refusal; missing verification/subject refusal;
inactive account; rollback at each signup boundary; authenticated own-profile
GET/update; another principal's injected ID and privilege fields rejected;
client-only denial; impersonation denial; stale revision; duplicate retry; audit
write failure; audit data minimization. Run real PostgreSQL constraint/race tests
and browser tests for success, validation, conflict, network failure, keyboard,
narrow layout, escaped input and support mode. Preserve existing signup,
claim/personal-record, registry, entitlement and report regressions.

## GO3-42 handoff

Membership references this stable User.id, never email or a clinic-specific copy.
Model independent active/suspended/revoked states per clinic. Clinic A suspension
must immediately refuse A operations while clinic B remains usable; retain
authorship. Clinical membership activation and seat allocation are atomic; pending
invitations consume no assigned practitioner seat, multiple locations in one clinic
consume no extra seat, and purely administrative staff do not consume clinical
seats. Subscription quantity remains separate from membership state.

Do not reuse patient ClinicAssociation as practitioner membership. GO3-195 must
settle the DEC-11 professional-vetting boundary before clinical activation;
GO3-269/275 retain membership administration design/UI work. This handoff does not
assert GO3-42 implementation or policy clearance is complete.
