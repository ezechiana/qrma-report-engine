# Client claim and release audit

This local audit checks the current solo-practice access boundary and identifies decisions needed before shared clinics. It uses the working checkout on 19 November 2026 and synthetic accounts. Existing edits were preserved. No application behavior was changed.

## Local remediation on 19 November 2026

After the audit, the user authorized implementation of the first two fixes. Existing verified clients can now accept invitations through an authenticated API and the invitation page, with explicit consent and matching email. Sign-in returns the client to the invitation. Claims are marked accepted only after the matching clinic association is active. A conflicting current association returns HTTP 409 before accepting the invitation or claiming its chart.

Claim completion locks the account, invitation and chart rows. This is partial concurrency protection. Other association and invitation mutation paths still need a coordinated PostgreSQL concurrency review.

Thirty scoped tests passed after these changes, including the new API regression for successful acceptance, missing authentication, wrong account, missing consent, unverified account, expiry, replay and conflict in both acceptance paths. Claim-page JavaScript syntax and scoped diff whitespace checks passed. Browser interaction and hosted verification remain pending. The source changes are included in the scoped remediation commit.

## Verification

Twenty-nine existing tests passed in the local QA image across claim invitations and accounts, clinic associations, claim termination, association releases, released reports and notes, progress, client entries, laboratory panels, wearable data and independent report share links. The suite completed with dependency deprecation warnings.

The tests exercise expired, cancelled and replayed invitations, wrong invitation email, verified account completion, separate personal and practice records, release withdrawal, record preservation and public share-link independence. This is local automated evidence. Hosted authentication, browser flows and concurrent PostgreSQL requests were not exercised.

## Reproduced gaps

### Existing clients cannot accept through the account creation route

The public claim route calls begin_client_claim. A synthetic verified client with the invited email received HTTP 409 with “An account with this email already exists.” The service accepts an existing account only when that account already reserved this invite.

Affected surfaces include an existing client accepting a new practice invitation and reclaiming access after unlinking. A separate request-by-practitioner-email flow exists, but does not complete the emailed invitation.

Recommended fix is an authenticated invitation acceptance operation. It must require a verified matching account, current explicit consent and an unexpired invitation. It must reject wrong accounts and resolve the active-association policy before writing.

### Completion can mark a second chart claimed without its association

A synthetic service-level test reserved a second practice invitation for a client with an existing active association. complete_reserved_claim marked the second invite accepted and its chart claimed. activate_practitioner_association returned the first active association, so the second chart had no matching active association.

This test deliberately set the reservation directly to exercise completion independently of the existing-account rejection. It does not demonstrate that the current public route permits a client to perform the entire sequence.

Recommended fix is to validate the association outcome in the same transaction before accepting the invite. Under the current one-active-association policy, return a clear conflict and leave the invitation and chart untouched. Supporting multiple practices is a separate product decision.

## Additional review findings

- Claim completion selects a pending invite and writes its state without explicit row locking. Invite creation can cancel and replace pending invitations without an explicit patient lock. Concurrent acceptance, cancellation and reissue need a PostgreSQL regression test before assigning a confirmed race defect.
- Claim termination and association termination have separate service paths. end_claim_access clears the chart claim and selected releases. Association termination withdraws association releases and changes association status. Verify every directional release and record listing after either entry point before consolidating these operations.
- Application tests currently establish the solo-practitioner boundary. Shared clinic staff access remains outside that model.

## Architecture decisions

Patient.user_id, invitation practitioner_user_id, association practitioner_user_id and release practitioner_user_id currently identify individual practitioners. Existing route ownership checks compare these IDs directly. ClinicAssociation connects a personal record with a practitioner-owned chart and is not a staff membership model.

Before implementing shared clinics, establish a Clinic identity, active staff memberships, roles and record ownership. Preserve creator and actor identity separately for audit. Plan migration from existing practitioner ownership to one-member clinics, including invitations, releases, associations, billing and all chart entry points. Membership authorizes staff access and plan entitlements authorize product capabilities.

The current one-active-association rule also needs an explicit decision before supporting clients who attend multiple practices. Keep tests for independent solo practices while defining same-clinic colleague access separately.

## Remediation sequence

1. Resolve claim and association state consistency under the existing one-active-association rule.
2. Add verified, authenticated invitation acceptance for existing accounts.
3. Test concurrent acceptance, cancellation and reissue against PostgreSQL.
4. Complete the directional release and listing matrix after both termination operations.
5. Run authenticated browser and Staging verification with synthetic accounts before signing off GO3-8.

Check the applicable GOV-01 gate before changes affecting access. Shared clinic implementation can follow the current audit, with its ownership and migration design agreed before extending practitioner-based authorization.
