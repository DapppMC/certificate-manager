# Gate -1: Evidence-Bound EARS and Behavioral Pinning (brownfield)
Rule: every requirement mined from legacy code is a HYPOTHESIS until a test pins it.

## Format
`REQ-BF-014 [INVARIANT] [ref: src/services/checkout.ts:L88-L112]: WHEN <trigger> THE SYSTEM SHALL <response>`
A requirement without a `ref:` file and line range is rejected.

## Three buckets (mandatory)
- `[INVARIANT]`     confirmed business logic. Preserve 100%.
- `[LEGACY_ANOMALY]` hacks, magic numbers, hardcoded ids (e.g. `if (user.id == 42)`).
  NEVER change or delete silently. Raise each one at Gate 0 for a user decision.
- `[DEAD_WEIGHT]`   unreachable per Gate -2 cartography. Marked for safe removal,
  removed only in a ticket of its own.

## Characterization (golden master)
1. Run the ORIGINAL code on realistic inputs; record actual outputs to `tests/golden/`.
2. Fixtures are the regression oracle. They are protected tests: never edited to go green.
3. Work in the hermetic twin (`tools/sandbox.py create`), never in the live tree.

## Exit criteria
- Each INVARIANT has at least one golden fixture id.
- ANOMALY list handed to Gate 0 with the question to ask the user.
