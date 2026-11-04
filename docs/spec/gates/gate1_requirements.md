# Gate 1: Requirements (EARS)
Each requirement has an ID, one EARS sentence, and a testable acceptance criterion.

## EARS patterns
- Ubiquitous:   THE SYSTEM SHALL <response>
- Event:        WHEN <trigger> THE SYSTEM SHALL <response>
- State:        WHILE <state> THE SYSTEM SHALL <response>
- Unwanted:     IF <condition> THEN THE SYSTEM SHALL <response>
- Optional:     WHERE <feature> THE SYSTEM SHALL <response>

## Rules
- IDs: `REQ-001` (greenfield), `REQ-BF-001` (brownfield, with `ref:`).
- One behaviour per requirement; no "and/or"; no vague words (fast, easy, robust).
- Every requirement states measurable acceptance: input, expected output, bound.
- Non-functional requirements get numbers (latency, size, cost).
- Out-of-scope list copied from the brief.

## Exit criteria
- No requirement lacks an acceptance criterion.
- User approved the list.
