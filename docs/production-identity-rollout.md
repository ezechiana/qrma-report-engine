# Production rollout guard for legacy social sign-in

20 November 2026. This prevents the missing-provider-binding problem found during
GO3-38 Staging UAT. It is a release preparation record, not authorization to deploy
or modify Production. Production was inspected read-only; no provider identifier,
password or application row was exported.

## Current inventory

The aggregate Production audit found seven recorded social-login accounts with
one unambiguous historical provider ID each and no conflicting owner. The current
Production database has no external_identities table and four migration-ledger
entries. Staging is substantially ahead: do not assume that applying only the
last linking migration prepares Production. Recheck live evidence at release time.

The release must include data compatibility, not only CREATE TABLE statements.
An active legacy social account must have its exact historical provider identity
restored before the strict sign-in resolver serves it. Admin accounts follow the
same rule; no special email/admin authentication bypass is introduced.

## Required sequence before advancing the production branch

1. Finish Staging/UAT and pin the exact candidate commit with successful CI.
   Keep production at the currently approved commit until this checklist passes.
2. Take a fresh Production schema and migration ledger snapshot read-only and
   rehearse the candidate's **complete** pending schema migration set locally.
   Preserve historical ledger filenames/timestamps; verify second replay is a
   no-op. Use synthetic application fixtures, including legacy social accounts,
   without copying health records or raw provider credentials.
3. Run the legacy identity preflight against the verified Production target.
   Inspect counts and the plan fingerprint. Exact successful historical
   principal/provider/subject evidence may be backfilled. Missing, ambiguous,
   shared or conflicting evidence blocks the release; it is not resolved by
   guessing from email. Inactive principals are not given new bindings.
   The gate covers retained social history. Confirm its retention/completeness:
   it cannot reconstruct events that were never stored or already deleted.
   Any additional known legacy cohort needs trusted evidence or an explicit
   recovery/linking plan. Verify Production email recovery during the approved
   rollout rather than assuming the Staging email test proves it.
4. Arrange a brief social sign-in/registration drain for the final cutover. An
   old application instance can otherwise create an email-matched account after
   the final inventory. Do not leave old and strict resolvers serving OAuth
   callbacks concurrently. Keep existing sessions/continuity access where safe.
5. Under explicit Production release approval, apply only the fully rehearsed
   schema migrations, then re-run the preflight and approve its exact current
   fingerprint. Apply the guarded data backfill and immediately run --check.
6. Require the successful data/readiness gate in Railway's pre-deploy command
   before promoting the candidate image. Preserve other required migration gates,
   including the connector checks; they are not a substitute for this gate or
   for the rest of the full schema migration plan.
7. Confirm the exact deployed commit, perform authenticated API/browser smoke
   with synthetic accounts, and verify a real legacy Google/Microsoft login
   retains its User.id, admin eligibility and memberships. Re-enable social
   sign-in only with the new resolver and completed coverage. Record the outcome.

## Guarded command

Run inside the reviewed release environment, where APP_ENV=production and the
database points to postgres.railway.internal. Public-proxy local tooling must
separately verify Railway project/environment/service IDs; never weaken the
host guard to accept an arbitrary URL.

```sh
# Read-only plan; aggregates and an opaque fingerprint, no provider IDs.
python -m app.db.legacy_identity_release \
  --environment production --expected-host postgres.railway.internal

# After reviewing and authorizing this exact plan, apply in one transaction.
python -m app.db.legacy_identity_release \
  --environment production --expected-host postgres.railway.internal \
  --apply --approved-plan-sha256 <reviewed-plan-sha256>

# Required release gate: exits nonzero while any active legacy account is unresolved.
python -m app.db.legacy_identity_release \
  --environment production --expected-host postgres.railway.internal --check
```

The deployment hook must run the approved apply followed by check after schema
migrations, or check alone when the data backfill was already applied separately.
Do not configure a check-only hook as a replacement for doing the backfill. A new
or changed plan requires review; the tool must not accept ambiguity automatically.
**Production currently has no pre-deploy hook. Installing this hook is still a
required action in the separately approved Production rollout; it has not been
changed as part of this preparation.**

## Invariants and failure behaviour

Only external_identities and per-binding audit records are inserted. Existing
users, passwords, roles, capabilities, subscriptions, clinic memberships and saved
settings are compared before commit and remain unchanged. Provider subjects and
emails are not guessed or written to audit metadata. No real-user auth session is
minted by the release tool. Provider IDs and credentials are not printed.

Each restored binding and its audit are atomic. A conflict, stale approved plan,
missing audit schema or failed audit insert rolls back the whole backfill.
Replaying an already complete backfill creates no duplicate bindings or audits.
Read-only readiness fails for missing history/schema, missing bindings and
unresolved evidence. A valid explicit user-confirmed binding is preserved even
when older historical provider information differs.

Retain provider bindings and audit history on rollback. Returning to the old
email-based resolver after provider emails change can duplicate identities;
block social starts and callbacks during such a rollback until a binding-aware
version is restored. Restoring access must never mean dropping identity history
or resetting an existing user's password without their authorization.
