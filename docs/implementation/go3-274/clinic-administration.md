# GO3-274 — Organisation and clinic administration

Implemented on 19 November 2026 against the approved GO3-273 design and GO3-188 readiness contract. This is the application UI using the registry API, rather than the offline prototype.

## Delivered behavior

- Account → Organisations & clinics, with an Account Settings shortcut. Navigation uses fresh `GET /api/organisations/access`; cached roles never grant access.
- List/create organisations and clinics at `/app/organisations`. Identical labels remain separate records, with distinct short references and expandable immutable UUIDs. Selection only affects administration on this page.
- Server access checks and denied/expired-session states. Client-role logins with practitioner capability can return to the exact registry page; neighbouring practitioner routes remain blocked.
- Validation, loading, empty, refresh failure, unknown result and conflict states. Names render as text. Modal keyboard focus, Escape/return focus, focus outlines and responsive layouts at 320/390 px are exercised.
- A strict actor-scoped sessionStorage journal is written and read-verified before POST. Unknown results retain the original actor/kind/parent/key/payload tuple for explicit retry. Known rejection permits correction; conflicts require explicit review before a separate intent. Key loss never triggers automatic creation or name-based reconciliation.
- Fresh identity and authority before mutation. Logout and identity changes invalidate other tabs using payload-free signals; automatic expiry retains the same-actor journal. Returning to a cached/hidden page revalidates access. A confirmed row survives refresh or journal-cleanup failure.

The normal application router includes the page and API. No change to `app/main.py`, migration, membership, chart permissions, clinical context, seats or billing is required. Existing unrelated checkout edits and staged rename were preserved.

## Local verification

All data was synthetic. Playwright drives the actual Jinja templates, shell, JavaScript and registry endpoints through a loopback FastAPI server. Normal active-user and registry authorization execute; token decoding is replaced with synthetic cookie identities. This does not prove live login/JWT issuance or hosted infrastructure behavior.

| Evidence | Result |
|---|---|
| `tests/test_clinic_registry_ui.py` / `evidence/browser.xml` | 18 passed in Chromium: duplicate names/new keys, keyboard/mobile, before/after-commit failures, wrong parent, reload/same-key recovery, changed actor, expiry/reauthentication, copied-tab retry/logout, storage failures, 422/409, revocation, stale lists, corrupt journal, text escaping, refresh failure, navigation and invalid parent |
| Registry PostgreSQL, access and client-claim suites / `evidence/backend.xml` | 40 passed; isolated disposable PostgreSQL 16, including additive migration replay and concurrent creation |
| Exact client login-return boundary / `evidence/login-return.xml` | 1 passed; external and adjacent practitioner destinations rejected |

59 distinct checks passed across these final runs, with dependency deprecation warnings. The earlier 38-check `browser-access.xml` overlaps these results and must not be added to the total. One preliminary assertion incorrectly assumed database row order; it was corrected to verify focus by the new record identity, and the final browser suite passed.

Desktop, mobile and creation-dialog images in `evidence/` were visually inspected. The S0 GitHub workflow now runs the browser suite and publishes JUnit evidence; no hosted run is claimed.

## Remaining delivery gates

The implementation is included in the scoped GO3-274 commit; its identifier is recorded in Linear. Before committing, the exact candidate snapshot (excluding unrelated checkout edits) passed all 41 browser/access checks again. GO3-230/GO3-251 retain hosted CI, Staging authentication/impersonation and UAT (including assistive-technology review), release evidence and deployment. Prior independent release approval is recorded separately; it is not an additional specialist review of these UI changes. No deployment was performed.

Recovery is tab-lifetime and browser-dependent. If a browser refuses storage deletion, best-effort logout invalidation hides the UI and future operations fail closed; inaccessible browser data cannot be guaranteed erased by JavaScript. With a lost key there is no server lookup-by-key endpoint or exactly-once guarantee for a deliberately separate creation.

## Reproduce

With project dependencies and Playwright Chromium installed:

```powershell
python -m pytest tests/test_clinic_registry_ui.py tests/test_clinic_registry_access.py -q
```

Set `GO360_DESIGN_CHROMIUM` only to use an existing compatible Chromium executable. Optional `GO360_CLINIC_UI_EVIDENCE` selects the screenshot directory. PostgreSQL tests require `GO360_CLINIC_TEST_DATABASE_URL` pointing exclusively to a disposable database. `tests/clinic_ui_app.py` is a synthetic harness and is never mounted in the application.
