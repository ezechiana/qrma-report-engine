# Module registry

GO3-39 implements the first MOD-001 slice from the approved 19 November 2026 blueprint.

The versioned catalogue is app/config/module_registry.json. Each entry has a stable uppercase code, display name, owning domain, classification and declared dependencies. CORE and CLINIC are foundations. SUBS and COMMS are shared services. The remaining entries describe optional workflows. Catalogue inclusion describes architecture and does not establish released functionality or a subscription grant.

The application validates the catalogue at startup before database work. Platform administrators can read its version and definitions at GET /api/platform-settings/modules through the existing administrator dependency.

ModuleRegistry and its nested definitions are immutable. register returns a validated next revision and leaves the original revision intact. Duplicate codes, missing targets, self references, conflicting duplicate dependency declarations, invalid owners and required/conditional cycles fail validation. Optional reciprocal integrations are permitted. Conditional dependencies require a named condition, such as managed_stock_required for DISP to STOCK.

The file is deployed as a reviewed configuration revision. Runtime publication, clinic activation, package allocations and effective access evaluation belong to subsequent S0 stories. The current helper does not persist a new revision or modify a clinic subscription. Module ownership is logical within the existing deployment.

The local synthetic test suite verifies duplicate rejection without changing the original revision, valid registration, invalid graphs, the 16-module blueprint seed and administrator-only API access.
