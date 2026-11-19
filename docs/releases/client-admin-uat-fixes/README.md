# Client and Platform Admin UAT fixes

The Staging `/client` landing route was absent although its template and API had shipped. Restore it and the existing linked Account destination, reusing the reviewed source from DEV. Do not grant practitioner API access to ordinary client-role logins.

Report detail lacked both the individual-share price control and a new-version action. Restore GBP pricing with exact minor-unit conversion and validation. A new Assessment version uses the existing generation endpoint and retains an actor/case-scoped creation key on uncertain responses. The server still checks report/case ownership, commercial access and applicable quotas. Trend reports do not show an Assessment regeneration action.

Use a shared `effective_access` resolver for commercial authorization, subscription-status UI and Assessment tier selection. Authoritative admins (role or saved allowlist) receive the known implemented commercial features and unlimited Assessment access in their own session. Retain actual billing state and identifiers; never mutate a subscription or manufacture Stripe readiness. An impersonated user's effective identity governs access, without inheriting the restoring admin's privileges. Resource permissions, platform availability and payment/integration checks remain separate.

Restoring pricing exposed an existing payment gap: Assessment public HTML, data and download paths did not enforce payment. All now enforce the payment state; password verification and `preview=1` do not bypass it. Checkout returns to the correct report type and a completed-but-unpaid Stripe session cannot unlock access. Price configuration and link creation commit atomically. The pricing control is disabled with an explanation when platform paid-sharing availability is off, rather than implying that admin access enables a disabled integration.

## Verification and rollout

- Local candidate suite: 112 checks passed, including 14 new PostgreSQL/real-login entitlement, pricing and payment tests and four report/client browser checks. DEV's 18 focused checks also pass. Screenshots were captured with synthetic accounts; no real Stripe charge was made.
- The underlying PDF/report build is covered by the existing QRMA baseline; new-version browser retry tests simulate generation responses, and endpoint tests verify actual ownership/entitlement checks with a stubbed generator. Hosted real report generation remains part of product UAT.
- `20261119_report_share_pricing.sql` makes existing Staging payment fields reproducible on a fresh database. It only adds missing columns, preserving existing prices, Stripe IDs and payment states. Fresh Staging schema/ledger rehearsal: 26→27 versions; previous ledger entries retained; second replay unchanged. See `migration-rehearsal.json`.
- Apply that migration explicitly before advancing Staging. Keep Production unchanged. Code rollback can return to `5515dab`; keep additive columns and ledger history. Previously created paid links must retain payment enforcement during recovery rather than being served through older unguarded Assessment routes.
- Exact hosted CI, deployment and synthetic smoke evidence will be recorded on GO3-230/GO3-251. Product/UAT and applicable manual review are not inferred from engineering checks.

DEV source edits were applied selectively with local backups. DEV uses packaged container code rather than a source bind mount, so the scoped runtime files were installed and the local application restarted. A future DEV image rebuild must include these checkout changes. Unrelated existing checkout edits and the original staged parser rename remain preserved; the release commit is made only in the isolated candidate worktree.
