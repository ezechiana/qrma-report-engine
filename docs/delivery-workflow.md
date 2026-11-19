# GO360 delivery workflow

Linear is the executable backlog. Read the live issue, comments and prerequisites before work. The approved 19 November blueprint remains source and architecture evidence; an offline snapshot does not establish implementation or approval.

## Reconciliation evidence

On 19 November 2026, the live GO360 Platform project contains all 153 unique blueprint stories: S0 21, S1 38, S2 34, S3 38, S4 22. Twenty-one existing stable story issues were preserved and 132 were added. All 260 declared blocking dependencies were re-read and verified. There are no missing or duplicate stable IDs, missing task prerequisites, bad task parents or dependency/completion cycles. All 26 earlier project issues retain their accepted scope and status and now have role coverage; overlapping scopes are related rather than blindly duplicated.

The 21 S0 stories have 84 core subtasks (contract/readiness, implementation, feature verification/specialist review, release evidence) and eight additional UI subtasks (six design, two dedicated implementation). For four other workflows frontend work is explicitly included in the existing implementation task. All six browser/accessibility suites are explicitly included in feature verification. Total unique S0 subtasks: 92. See s0-role-task-map.json and blueprint-linear-delivery-map.json.

Linear rejected further creation with its free workspace issue-limit error. No issue was deleted or archived and no subscription was changed. Consolidation preserves immediate scope; later S1–S4 task refinement needs a deliberate capacity approach. Role/timing coverage and source decision/external gates are already recorded on those future stories.

## Roles and timing

| Discipline | When and responsibility |
|---|---|
| Chief Product Officer / Product Manager | Eze Chiana owns product decisions and acceptance; define value, scope and approved policy before promotion. |
| Healthcare Business Analyst | Before Ready: workflow, exceptions, terminology and acceptance examples. |
| Clinical Informatics Architect | Before clinical data/contracts change: clinical semantics, provenance, identity and record boundaries. |
| Principal Software Architect | Before implementation: domain interfaces, dependencies, ADRs and cross-domain ownership. |
| Senior Backend Engineer | After contract review: server authority, integrity, idempotency and APIs/jobs. |
| Senior Frontend Engineer | After design/API review: accessible components, user workflows and code quality. |
| Database Architect | Before schema/data change: keys, constraints, ownership, backfill and rehearsed rollback. |
| Healthcare Security and Privacy Specialist | Before protected flows and release: scoped authority, data handling, audit and attributable specialist conclusions. |
| Clinical Safety and Quality Assurance Lead | Before clinical use/release: hazards, validation, residual risks and accountable clinical review. |
| Test Automation Architect | Define the acceptance/adverse test plan before implementation; verify API, integration, browser, regression, migration and concurrency as applicable. |
| DevOps and Platform Architect | Before rollout: environments, CI, migration/recovery, operational evidence and monitoring. |
| SaaS Subscription and Entitlement Architect | Before commercial changes: plan versions, clinic funding, seats, quotas and lifecycle distinct from clinical permissions. |
| Technical Programme Manager | Eze Chiana coordinates ownership, dependency readiness, delivery/UAT batches and evidence. |
| Competitive Market Research Analyst | Before promoting opportunities: dated public evidence, comparison and product approval; GO3-21/26 retain their own scope. |
| UI/UX Designer | Before frontend implementation: journeys, permission/error/retry/empty states, focus, keyboard and responsive design; justify contract-only UI exclusions. |
| UI/UX Implementer | Implement approved interactions faithfully and verify integration with authoritative server eligibility. |
| Product Story Translator | Before Ready: map source requirements into bounded work, acceptance examples, roles and dependencies without duplicating earlier scope. |
| Technical Debt Cleaner | During analogue reconciliation/implementation: identify necessary bounded cleanup, preserve existing edits and history; no unsolicited rewrites. |

Role labels identify disciplines, not invented human appointments. Only Eze Chiana is a named workspace member. GO3-6 (coordination) and GO3-20 (product decisions) are assigned to Eze. Name execution owners and applicable independent reviewers before Ready; one person may cover compatible roles. AI engineering output does not supply independent clinical/legal approval.

## Completion and readiness

Keep the existing Linear workflow. Backend implemented, complete workflow verified and release validated are distinct evidence checkpoints, not new statuses. A user-facing parent remains open until applicable design, UI, browser/accessibility, specialist and release work is complete. Contract-only work records its UI applicability and consuming-story responsibilities; MOD-019 owns the future package administration console.

Before Ready: confirm scope and current analogue, dependencies, named owner/reviewer, acceptance/adverse cases and applicable decisions. Passing tests and importing a ticket never clear policy or external gates. Reuse prior approvals without requesting them again or expanding their scope silently.

Before release: exact commit/candidate, hosted CI, applicable fresh Staging schema/ledger rehearsal, recovery evidence, product/UAT acceptance and applicable specialist approval. Preserve established Staging/Production branch policy. This reconciliation changes no hosted service or deployment.

GO3-6 remains In Progress for named specialist assignments, scoped gate clearance and next-batch/cadence decisions. Recommended next slice: GO3-35 UI design (GO3-273), GO3-38 identity contract review, and GO3-33/39 hosted review/CI evidence. GO3-42 retains both clinic and practitioner identity prerequisites. Later sequences are Backlog and receive finer role tasks before activation.
