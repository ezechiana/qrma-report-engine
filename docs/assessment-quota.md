# Assessment quota rollout and recovery

Free practitioners can generate five Assessment report versions per UTC calendar month. Paid Pro and unexpired Pro trials have unlimited Assessment generation. Usage is tracked on every tier so a practitioner who moves to Free keeps that month's usage.

The allowance is enforced against the practitioner who owns the generation request. An in-flight generation reserves a slot in its start month. Saving the ready report consumes that slot in the same transaction. A terminal failure or cancellation releases it. Viewing, sharing, and downloading an existing report do not consume quota. Deleting a report leaves its usage recorded.

## Rollout

1. On an isolated copy of the target schema and migration ledger, apply `2026_11_17_assessment_quota.sql` with the candidate runner. It includes delivered Assessment versions from the current UTC month. Reconcile the initial consumed rows against delivered report versions before enabling the candidate.
2. Back up Staging and run the standard migration procedure before deploying. Release startup does not apply migrations automatically. Confirm no old report workers are generating reports during the migration and deployment window.
3. With synthetic users, verify five successful Assessment versions for Free, a `429 assessment_quota_exhausted` on the sixth request, and unlimited generation on Pro and an unexpired Pro trial. Verify one successful reservation when two requests race for the fifth slot, no extra debit on replay, and restored allowance after a terminal failure.
4. Verify the browser and connector paths, account isolation, the first-of-month UTC reset, and the carry-forward of usage on downgrade. Record Staging results on GO3-29 before promoting.

## Investigation and recovery

Owners can inspect their current usage with `GET /api/cases/assessment-quota`. Compare the quota ledger's `reserved`, `consumed`, and `released` rows with the linked report versions and their timestamps. `409 assessment_in_progress` means a retry found a reserved request. Use the same browser request key or connector job ID to retrieve the eventual result. Missing quota storage is a service error and should be investigated before retrying generation.

A process crash can leave a reservation open. Confirm the worker is no longer running before releasing it, and verify that the linked version was never delivered. Reconcile the report status and reservation together in a reviewed database transaction under the same practitioner lock used for generation. Record the reason and affected identifiers in the incident record. Leave consumed rows intact and do not reset remaining allowance by deleting reports or ledger entries.
