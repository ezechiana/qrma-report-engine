# S0 regression fixtures

GO3-33 establishes a repeatable baseline before changing clinic identity and membership. Fixtures use deterministic UUIDs, synthetic accounts and similar patient names in two independent practices.

## Reproducing the checks

Install the repository requirements and Chromium using python -m playwright install --with-deps chromium. Supply dummy S3 credentials and a synthetic bucket name, disable EC2 metadata discovery and leave OPENAI_API_KEY empty. Run python -m pytest tests/s0 -q. All database writes use in-memory SQLite and object-storage calls are replaced in the fixture. The real report builder and Chromium PDF renderer run locally.

Set GO360_S0_INJECT_WRONG_CLINIC=1 and run tests/s0/test_practice_isolation.py with -k owner_lists to prove that the wrong clinic mapping exits with status 1. This control changes only fixture expectations.

## Evidence and baseline

Two reference profiles are stored in tests/s0/qrma_baseline.json: committed_1548a40 for commit 1548a40f59710e179fe2ee65061bb7965d37eccc, and working_2026_11_19 for the reviewed existing checkout edits. Each profile contains exact source fingerprints for the parser, report builder, PDF service and report template, plus source-fixture, parsed-report, viewer and HTML SHA-256 values. Source fingerprints normalize CRLF to LF. An unknown source revision fails before output comparison; references are never regenerated automatically. The synthetic report clock is fixed at noon UTC on 19 November 2026. The real PDF must build without warnings and have a PDF signature and end marker. Its hash and size are captured as run evidence, rather than compared byte-for-byte across renderer environments.

The fixture uses the tracked ResultOne.htm source, changes the report subject to synthetic Alex Morgan and keeps the two practice charts separate. Stable case, report and patient IDs are included in JUnit properties. A source hash change or output drift fails the selected baseline comparison.

Both the isolated committed snapshot and working checkout passed all 12 S0 tests on 19 November 2026. A deliberately incorrect clinic expectation produced the required failing exit status. The additional guard test confirms an unreviewed source revision cannot reuse either reference. Existing checkout edits correctly read image elements and consolidate duplicate Expert/Hand Analysis sections; report metadata, narrative selection and presentation also differ. All 14 existing ResultOne ingestion and canonical-parser tests passed against those edits. The original committed reference remains intact. This establishes regression evidence for the reviewed revisions, not clinical validation or approval of every existing checkout edit.

## Current and target scope

Current route checks establish practitioner-owned practice isolation for report lists, details, JSON, status, HTML/PDF export and archive mutations. Release and withdrawal preserve the report and independent share link. Actor identity is supplied through the normal authentication dependency override. Full sign-in and live object storage are outside this fixture.

scenario.json also specifies the target practitioner with different roles in two clinics and an actor without membership. These role assignments are the expected scenario for GO3-35/42/46/47/50. The current production model has no general clinic staff membership, so this run does not establish shared-clinic role enforcement. Extend the same fixture once those contracts exist.

## CI and promotion

The S0 foundation regression workflow runs the suite on pull requests, pushes to main and manual dispatch. It checks that the deliberately incorrect fixture fails and uploads JUnit and control output for 14 days.

The workflow is local and has not run in GitHub. Repository branch protection and Railway Wait for CI must require the S0 regression result before claiming that it prevents promotion. Those remote settings were not changed in this task. The recovery, audit, clinical and privacy gates in the approved blueprint remain separately attributable decisions.
