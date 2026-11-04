# Gate 2: Architecture and Design
Keep the design as small as the requirements allow. Prefer boring parts.

## Contents of the design doc
1. Components and data flow (one Mermaid diagram).
2. Data model and interfaces (names, types, error cases).
3. Decisions as short ADRs: context, choice, rejected options, reason.
4. Tooling: smallest set of CLIs/MCP servers; human-only steps listed.

## UI projects
Read `design/DESIGN_CONSTITUTION.md` (always loaded for frontend work).
1. Classify each screen: MODE A (workspace) or MODE B (editorial). Exactly one.
2. Pick one archetype; pull tokens from `design/tokens.css`.
3. For inspiration use `python tools/odcat.py search --archetype <editorial|minimal|workspace-light|precision-dark|industrial-warm>` and `card <id>`; never open raw catalogs. The 14 systems are bundled in `catalog/source/`.
4. Every UI ticket's acceptance includes `python tools/design_lint.py <page.html> --strict` (exit 0). It fails on indigo/purple defaults, non-concentric radii, numbers without tabular-nums, `transition: all`, nested cards and low contrast.

## Exit criteria
- Every requirement maps to a component.
- Every external dependency justified in an ADR.
