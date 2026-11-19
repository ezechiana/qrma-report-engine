# GO3-188 — Clinic registry implementation readiness

19 November 2026. GO3-188 is complete for the bounded engineering contract/readiness and reviewer-role assignment. Design authority: GO3-273 v1 approved by Eze Chiana. Execution: Codex in backend/architecture/story-translation roles. The user directed selection of the relevant project role for review: **Healthcare Security and Privacy Specialist** owns security/privacy review; **Technical Programme Manager** owns coordination and **Eze Chiana** owns product decisions. No individual specialist or independent sign-off is invented.

This record settles the bounded engineering contract for GO3-274. It does not complete GO3-35, appoint a clinician, approve professional vetting, claim independent peer review or authorise deployment. Actual independent specialist conclusions and release evidence remain on GO3-230/251. Earlier checklist wording asking for an individual name is superseded by the user's role-based assignment instruction.

**Approval update:** On 19 November 2026 Eze Chiana explicitly supplied independent release approval and requested the scoped commit followed by finalisation of GO3-210. That approval is recorded on GO3-251 and supersedes the pending-approval wording below for this candidate. No additional reviewer identity/report is invented. The unimplemented production UI, hosted CI, Staging/UAT and deployment evidence remain separate; they are not asserted completed by this approval.

## Contract and ownership

CLINIC owns organisations, clinics and registry events. AUTH resolves the current effective actor. The registry API uses its existing active/verified practitioner capability or authoritative platform-admin gate. The organisation creator is the v1 administrative authority adapter, not an unchangeable membership or a clinical grant. GO3-42/45/46/47 retain membership, clinical isolation and role-policy work. SUBS retains subscriptions, seats, limits and commercial eligibility. No module in this slice writes those domains.

The approved UI is Account → Organisations & clinics, proposed page `/app/organisations`. Selecting an organisation is local administrative navigation. IDs are server-assigned immutable UUIDs; display names may repeat. Do not infer identities, permissions or billing principals from labels. The existing API returns only `id`, `display_name` and the clinic's `organisation_id`; do not invent owner, membership, plan, seat or credential indicators.

Four existing commands/queries are sufficient: GET/POST `/api/organisations` and GET/POST `/api/organisations/{id}/clinics`. POST accepts only `display_name` (trimmed, 1–255 Unicode code points, no control characters) and a UUID `Idempotency-Key`. 201 is new, 200 is replay, 409 is reused key with different input. Parent/authority is checked again before replay. The actor/type creation-key constraint and actor row lock serialize retries; mutation and registry event commit together. GET is scoped by server authority; 404 does not distinguish absent from unauthorised.

Successful scoped discovery implies administration under v1 only. If read-only memberships are introduced, add an explicit server capability contract before reusing this inference. Cached auth data is a navigation hint, never authority.

## Access gap resolved

`get_current_user` now applies `clinic_registry.authorize` to **GET/HEAD of the exact `/app/organisations` path**. This aligns the future page with the API for a client-role login that explicitly has practitioner capability, role admins and the saved admin allowlist. Other paths, descendants, lookalike prefixes and client-role POSTs do not receive this exception. No page, UI controls or production route is added by this change; GO3-274 must use the normal authenticated dependency on the new page.

`tests/test_clinic_registry_access.py` executes that normal dependency plus the real registry policy against an in-memory synthetic database. Only access-token decoding is replaced; this is not a JWT/sign-in or full impersonation test. The real saved allowlist is exercised. Twenty-one checks cover page/API eligibility, other practitioner-path denials, safe methods, capability and allowlist revocation, effective actor, anonymous/invalid token rejection, cookie sessions and lack of organisation authority from a page query parameter. Initial setup failed because PostgreSQL UUID types could not compile on SQLite; the fixture now temporarily uses generic UUID types and restores them through pytest monkeypatch. No application migration failed.

The 21 new checks plus existing client/claim regressions passed together: **28 passed**, with existing dependency deprecation warnings only. The CI workflow includes the new suite, but no hosted CI run is claimed. Five additional native Chromium storage probes passed; evidence is `docs/readiness/go3-188/storage-checks.json`. They confirm exact round-trip, same-tab reload retention, opener-tab copying, independent deletion in copied tabs and an empty fresh browser context. These are primitive behavior checks, not tests of the production helper (which GO3-274 has not implemented).

## Pending creation intent — binding contract for GO3-274

**Container:** same-origin, actor-scoped session storage, one registry journal for this tab. A journal record contains schema version, effective actor UUID, resource kind, parent UUID (null for organisation), UUID creation key, canonical name payload, lifecycle state and creation time. No tokens, credentials, clinical values, original administrator identity or telemetry copies are stored. This is tab-lifetime recovery, not durable cross-device storage. Never use localStorage to prolong the journal beyond a browser session.

**Before POST:** resolve the actor freshly; require the shared server eligibility result. Probe storage availability, construct a UUID key and canonical payload, write then read-verify the intent before sending. If persistence is unavailable, disable creation with an actionable storage message. Never send first and discover later that recovery data could not be saved. Validate the parsed journal shape strictly; do not execute or render its strings as HTML. Storage is not trusted as authority.

**Same actor reload/automatic reauthentication:** restore only the matching actor's pending journal after fresh authentication and authorisation. A 401 redirect retains the pending journal in this tab so the same actor can retry after sign-in. A different actor, including an impersonated identity, never sees or retries its payload. Clear the previous actor's journal on a known identity transition before displaying any registry data. Do not restore using cached `current_user`, email or role strings. A browser-native duplicate tab can copy session storage: a copied existing pending key must remain the same key, not silently rotate.

**Explicit logout:** attempt journal removal before the logout request/redirect, even if that request fails; this is separate from automatic 401 recovery. Do not persist an unconfirmed business label after explicit logout. Record no payload in logs. A subsequent sign-in is a fresh tab session for registry recovery purposes, not proof the prior request failed. Since the key may be gone, require review before a separate creation; never resubmit the old payload automatically with a new key. Logout cleanup belongs in GO3-274's integration with the existing auth helpers, without changing how authentication tokens work.

**Other open tabs:** sessionStorage deletion does not clear a copied tab's journal. GO3-274 must send an explicit same-origin logout/identity-change invalidation (BroadcastChannel, with a transient payload-free localStorage event fallback where available); registry tabs clear their journal and displayed data on receipt. The signal contains only event kind and a nonce, not actor IDs, names, tokens or health values. An automatic 401 event hides protected UI but preserves the same-actor recovery journal; distinguish that from explicit logout. Every pageshow/visibility return revalidates effective identity before showing or retrying an intent. If signalling is unavailable, fail closed on fresh identity checks and never auto-send a stored POST. Test this across two tabs; don't treat sessionStorage key scoping as an authentication boundary.

**Storage/key lost or corrupt:** there is no current lookup-by-key endpoint. Do not guess the record from its name, infer success from an equal-name list row, or promise exactly-once recovery after key loss. Show that earlier outcome cannot be confirmed, load the authorised list and require an explicit separate creation intent if the user wants to proceed. Retain no malformed payload. If storage cannot be read/removed, do not display it or send a recovery POST; use an explicit recovery/error state.

**Switch/close:** an unsent draft can be cancelled. While sending, keep parent/name fixed and prevent duplicate submits; use a bounded timeout, after which Escape/Close for now is available. Closing an unknown-result dialog retains its journal and marks it resumable, not cancelled on the server. Do not silently rebind an intent to another organisation. Selecting a different organisation cannot accidentally reuse its key/payload; resolve or explicitly abandon the previous intent with an honest prior-outcome warning first.

| Transition | Required behavior |
|---|---|
| New explicit intent | New UUID; save actor/type/parent/canonical payload before POST. A repeated label is allowed. |
| Network loss, timeout, abort or 5xx | Mark outcome unknown. Retry only the exact actor/type/parent/key/payload tuple after a fresh eligibility check. No background auto-retry and no automatic key expiry/rotation. |
| HTTP 200/201 | Check returned parent for a clinic; deduplicate UI by returned UUID. Retire journal after confirmation. Retain the returned row even if subsequent GET refresh fails. |
| Confirmation cleanup fails | Keep a known-confirmed state in memory, block a new creation until journal cleanup/read verification succeeds, and explain the storage problem. A retained old key is safe to replay but must not masquerade as a separate intent. |
| First known pre-write 422 | Preserve input; allow correction and a new canonical intent after retiring the rejected one. Never treat a refusal on retry after an unknown result as proof the earlier attempt failed. |
| 409 conflict | Keep the original intent, block blind retry, allow review and only an explicitly separate creation with a warning about a possible existing record. |
| 401 | Clear protected page data and reauthenticate. Retain only the actor-bound pending journal for same-actor recovery in this tab. |
| 403/revocation or 404 parent | Clear affected displayed records and stop mutation. The earlier outcome can remain unknown; no inherited original-admin authority and no automatic new intent. |
| Explicit separate creation | Warn that the earlier request may have completed; retire/abandon the previous intent only through this explicit action and establish a new key. Do not call the previous save rolled back. |

This lifecycle is implementation work for GO3-274, not functionality added to the in-memory design prototype. The approved prototype's 15 interaction groups provide examples; production session storage, auth transitions, race suppression and real API behavior still need their own tests.

## Database and migration boundary

Reuse `20261119_tenant_organisations_clinics.sql`; no additional schema change is needed for page eligibility or a browser intent journal. Legacy mappings use source user IDs rather than display names. Existing patient/case/report/share ownership and history stay intact. The earlier 13 PostgreSQL registry checks and rollback/ledger rehearsal remain historical evidence; they are not a fresh Staging rehearsal. Fresh Staging schema/ledger, candidate CI and rollout recovery evidence stay on GO3-230/251. Preserve the staged parser rename and unrelated checkout edits.

## Security/privacy role assessment and DEC-11 scope

Assigned role: **Healthcare Security and Privacy Specialist**, per the user's instruction to select the relevant project role. Engineering self-assessment by Codex is recorded separately from independent specialist release acceptance.

| Review area | Finding / required boundary |
|---|---|
| Identity and scope | Exact-page authority parity is tested. Other practitioner paths remain denied for capable clients. Fresh actor/allowlist/capability checks apply; selecting a UUID grants nothing. |
| Clinical authority | Registry registration creates administrative identities only. No membership, clinical role, record release, prescription authority, credential approval or patient data is written or returned. |
| DEC-11 | Professional-vetting policy remains open for the stories that confer professional privileges. GO3-188 records this bounded exclusion; it does not approve or waive professional vetting. Approved registry design explicitly excludes it. Any later expansion requires the applicable gate before protected clinical use. |
| Minimal data | Name/ID data and attributable decisions only. Pending browser journal has no health values or credentials, has actor separation and explicit logout cleanup, and is not sent to analytics. |
| Retries and failure | Stable request tuple, transactional server creation/audit, honest uncertainty and explicit recovery prevent automatic duplicate creation. Key loss is an acknowledged recovery limit. |
| Browser authority | Storage and cached roles are untrusted; output must be escaped and mutation/refusal results remain server-authoritative. API-derived access must change visibly on revocation. |
| Release review | Independent privacy/security conclusions, production browser/auth/storage checks, Staging/UAT and release evidence remain explicitly outstanding on GO3-230/251. No professional approval is claimed here. |

## Implementation acceptance handoff

GO3-274 must test: same-actor reload; 401→same/different actor; explicit logout (including failed logout request); impersonation/restore; blocked/quota-exhausted/corrupt storage; double submit; duplicate tab; same-name new intent; timeout before/after commit; changed parent; 409/422/403/404; wrong-parent response; success followed by refresh/cleanup failure; stale GET response after switching organisation; and keyboard/mobile behavior. Add a bounded storage test harness when the actual helper is implemented; do not substitute prototype checks for this integration.

The engineering decisions and reviewer-role assignment are recorded here. Actual production implementation, independent release review, commit, hosted CI and deployment acceptance are separate work. Eze's approved design is reused without another approval request.

## GO3-274 implementation update — 19 November 2026

The application page, shared auth lifecycle integration and recovery helper are now implemented locally. See `docs/implementation/go3-274/clinic-administration.md` for the delivered scope, 59 passing checks, screenshots and remaining release gates. Earlier statements above that the helper/page have not been implemented describe the readiness baseline. They are superseded by this implementation update; hosted CI, Staging authentication/UAT and deployment remain unverified. No new migration was introduced.
