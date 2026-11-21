# Explicit provider linking for existing accounts

20 November 2026. Follow-up to GO3-38 UAT: an existing platform administrator could
not use Google because durable provider bindings were introduced without a linking
workflow. The user authorized implementation of Account → Sign-in methods. The
sign-in method proves who the user is; platform roles remain a separate decision.

## User flow

Sign in with existing email/password (use Forgot password first if the account
previously used social sign-in only). Account → Sign-in methods displays the
current GO360 account and Google/Microsoft linked status. Link starts a fresh OAuth
authorization using the existing configured callback URL, so no provider-console
redirect change is required. After provider verification the page identifies the
selected provider account. Confirmation requires the current GO360 password.
Only then is the provider bound to the existing immutable User.id.

The provider email may differ from the GO360 sign-in email: both account proofs
are required and the user explicitly confirms the displayed identity. Neither the
GO360 email, roles, capabilities, subscriptions, clinical grants, memberships nor
records change. This works for platform admins, allowlisted admins, practitioners
and clients. Clients get an entry from their Account page. Support impersonation
cannot start or confirm linking. Existing links are visible; removal/replacement
is excluded to avoid account lockout or silent reassignment.

## API and security contract

* GET /account/sign-in-methods serves an empty branded shell; identity data comes
  only from authenticated GET /auth/sign-in-methods. Responses are no-store.
* POST /auth/sign-in-methods/{provider}/link checks active/verified actor,
  same-origin Origin, no support mode and provider configuration. It creates a
  ten-minute server-side attempt with random state, browser proof and an exact
  authenticated-session fingerprint. Only hashes are stored for those proofs.
* A Secure/HttpOnly/SameSite=Lax cookie on /auth holds the browser proof and PKCE
  verifier. PKCE S256 is used with both providers. No provider access/refresh
  tokens are stored. Local HTTP tests/development may use an insecure cookie;
  hosted and unknown environments always require Secure.
* The existing /auth/social/{provider}/callback detects the linking state and
  never falls through to ordinary sign-in. It checks the same user, browser,
  session, provider, pending phase and expiry before requesting trusted UserInfo.
  A successful callback records a candidate proof, not a binding or new login.
* POST /auth/sign-in-methods/confirm/{attempt_id} requires the same session/browser,
  fresh password verification and one-time verified attempt. Five failed password
  attempts invalidate the candidate. Actor refresh under lock checks current
  password and active/verification state. Concurrent bindings are constrained by
  unique provider/subject and one provider per user. Another user's provider is
  refused even to platform admins; accounts are never merged automatically.
* Binding, attempt consumption and minimal audit commit together. Audit failure
  rolls back the binding. Candidate subject/email are cleared on completion or
  terminal refusal; expired transient attempts are pruned on methods access/start.
  Audit records user ID, provider and outcome only, not passwords, email, subjects,
  OAuth codes, states, verifier, session tokens or provider tokens.

UI covers loading, configuration unavailable, support/unverified read-only,
cancelled/failed/expired flow, password error, success and reload. Labels, focused
errors, keyboard submit, live status, escaped provider labels and mobile wrapping
reuse GO360 branding. Password input is cleared after each confirmation attempt;
no credentials or proof state go into browser storage or query-string notices.

## Migration and verification

20261120_provider_identity_linking.sql runs after practitioner_identity, preserves
all existing IDs/bindings and adds only a nullable provider label, user/provider
uniqueness and transient link-attempt table/indexes. It guesses no historical
provider subject or email. Existing ambiguous duplicates cause migration failure
rather than account merging. Rollout requires exact-candidate CI and a fresh
Staging schema/ledger replay. Keep tables and bindings on application rollback;
the earlier restriction against rolling back to email-based social matching
still applies.

Feature checks exercise both providers; normal social login after linking resolves
the same principal, original email and admin/client authority. Adverse checks cover
wrong origin/session/browser, expiry, replay/cancellation, failed password limit,
unverified provider proof, another user's binding, concurrent account claims and
audit rollback. Migration checks preserve old binding IDs and ledger timestamps
and verify a second replay is unchanged. Actual browser tests drive the link and
password-confirmation UI on loopback; only the external provider transport is
synthetic. Real Google/Microsoft authorization remains the user's final UAT action.
