# Clinic registry release candidate — 19 November 2026

Candidate branch: `codex/clinic-registry-staging-candidate`.

## Integration

Merge local `3a80de9` into the previously reconciled quota candidate `0dd5148`, which descends from `origin/main` (`7dc78ee`). This retains the release baseline and the two quota follow-ups (`ec2dd58`, `0dd5148`) rather than replacing them with the earlier local quota commit. Six initial conflicts were predominantly line-ending differences. Resolve the two semantic startup conflicts by retaining explicit release migrations and adding module validation and clinic route registration. Preserve historical SQL filenames and source ownership. No working-tree changes from the everyday checkout were imported.

## Verification

- Candidate suites: 88 passed, one local setup failure because PDF generation required missing Playwright Chromium 1105. After installing that pinned browser, both QRMA baseline checks passed. Thus all 89 distinct selected checks have passing results; the initial failure is retained in `candidate-tests.xml` and corrected by `qrma-rerun.xml`.
- Fresh read-only Staging schema export and migration ledger: 23 historical entries, three pending migrations, 26 final entries. Previous versions/timestamps preserved; second replay unchanged. Only schema and ledger were copied, never application rows. Hashes and exact pending filenames: `migration-rehearsal.json`. Local rehearsal used PostgreSQL 18 to match Staging.
- Full application in Staging mode against that replayed schema: 31 authenticated endpoint checks passed with synthetic users, real password verification and JWT cookies. Covers duplicate labels/distinct IDs, same-key recovery, existing practitioner page/data endpoints, ordinary client denial, cross-organisation denial, admin impersonation and restore, and logout. See `authenticated-smoke.json`.
- Browser interaction tests use synthetic token decoding; the separate full-application smoke supplies real login/cookie coverage. Neither substitutes for hosted Staging browser/UAT or assistive-technology acceptance.

## Release boundary and recovery

Observed hosted Staging: `179723e`; Production: `9fee6a4`. Preparing this candidate does not advance either branch. Hosted CI and Staging/UAT results must be recorded before closing GO3-230/251 or parent GO3-35. Prior supplied independent approval remains attributable to the earlier foundation/design candidate; no new human review is invented.

The deployed Staging configuration has `APP_ENV=staging` and `AUTO_CREATE_TABLES=false`; apply reviewed migrations explicitly before advancing its deploy branch. The UI requires the registry migration and must not precede it. Rollback application code to the prior Staging commit if checks fail; retain additive registry tables, new quota tables and migration ledger history rather than deleting new records or rewriting ledger entries. Review policy/data effects of the Free-selling migration before rollout; code rollback alone does not reverse its setting update. Production stays on its existing branch until a separately approved release candidate is advanced.

The raw schema/ledger and disposable test database remain local/private for rehearsal. No credentials, real users or application health data are included in these release artifacts.
