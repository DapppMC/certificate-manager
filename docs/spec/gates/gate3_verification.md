# Gate 3: Verification and Reflexion
## Traceability matrix
`REQ-ID -> test id -> command -> expected result`. No requirement without a row.
Protected tests are listed in each ticket and are never edited to pass.

## Reflexion loop (anti-"dungu" guard)
1. A test fails -> compute `python tools/gate3/failure_signature.py <test> <error> [frame]`.
2. Before ANY edit write in `reflection_log.md`:
   `## <signature> / attempt <n>` then `Hypothesis: <falsifiable cause, >= 10 chars>`.
3. `tools/gate3/reflect_check.py <sig> <n>` must exit 0 or the edit is blocked.
4. Same signature after 3 attempts -> write `ESCALATION.md` and HALT. A human or a
   higher-tier model replans; do not try a 4th variation.

## Classify failures
test wrong / code wrong / spec wrong / environment. Only "spec wrong" changes requirements.

## Exit criteria
- All acceptance commands exit 0; matrix complete; no open escalations.
