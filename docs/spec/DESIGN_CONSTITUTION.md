# DESIGN_CONSTITUTION.md (v3.0 - Planner AI Canonical Standard)
# Target Budget: <= 1,500 tokens (Always Loaded in Frontend Context)

## LAYER 0: SURFACE MODE CLASSIFICATION (PRIMARY CONFLICT RESOLVER)
Before writing 1 line of frontend code, classify the target screen into exactly ONE surface mode:
- **MODE A: WORKSPACE / UTILITY (Dashboard, Admin, Table, Editor, Operational Tools)**
  -> Priority: Information density, rapid scanning, zero visual distraction.
  -> STRICTLY FORBIDDEN: Glassmorphism, neon border glows, floating decorative 3D elements, aurora gradients, gratuitous bento grids without data necessity.
- **MODE B: EDITORIAL / MARKETING (Landing Page, Portfolio, Pitch Deck)**
  -> Priority: Typographic rhythm, narrative hierarchy, visual poise.
  -> Permitted: Purposeful bento grid, high typographic scale contrast, subtle radial warm glow.

## LAYER 1: HARD ANTI-SLOP GUARDRAILS
1. **G-01 Zero AI Color Clichés:** Banned generic Tailwind Indigo/Purple (`#6366f1`, `indigo-500`, `purple-600`) as primary brand colors unless explicitly required.
2. **G-02 No Cardception:** Banned nesting cards inside cards (`card > card > card`). Use 1px hairline dividers or a subtle background luminance shift (2%-4%) to group content.
3. **G-03 No Decorative Blur in Workspaces:** Banned `backdrop-blur-*` on static content cards. Permitted only on sticky headers and modal backdrops.
4. **G-04 Zero Filler Copy:** Banned empty marketing jargon ("Supercharge your workflow", "Unlock next-gen potential"). Microcopy must be precise, functional, direct.
5. **G-05 High Contrast Accessibility:** Body text contrast ratio >= 4.5:1. Banned low-contrast gray text on light-gray backgrounds.

## LAYER 2: SPATIAL TOKENS & ARCHETYPES
All colors and dimensions MUST resolve to CSS Variables defined in `tokens.css`.
Choose exactly ONE base archetype per project:
- **Archetype 1: Precision Dark (Linear / Raycast vibe)**
  `--bg-canvas: #09090B; --bg-surface: #121215; --bg-elevated: #18181C; --border-subtle: rgba(255,255,255,0.07);`
- **Archetype 2: Industrial Warm (Braun / Teenage Engineering vibe)**
  `--bg-canvas: #F4F3EF; --bg-surface: #FFFFFF; --bg-elevated: #EAE8E1; --border-subtle: rgba(20,20,19,0.10); --accent: #FF4F00;`
- **Archetype 3: Editorial High-Contrast (Monocle / Stripe Docs vibe)**
  `--bg-canvas: #FAF9F6; --bg-surface: #FFFFFF; --border-subtle: #E5E4DF; --font-display: 'Instrument Serif', serif;`

## LAYER 3: OPTICAL & TACTILE CRAFT
1. **Concentric Border Radius:** for a container with padding p and inner child radius R_i, outer radius R_o = R_i + p. Never let an inner corner look pinched.
2. **Optical Borders:** `box-shadow: 0 0 0 1px var(--border-subtle)` or `outline: 1px solid` with `outline-offset: -1px`.
3. **Data Typography:** `font-variant-numeric: tabular-nums;` for all numbers, prices, counters, timestamps, tables.
4. **Optical Text Balance:** headings `text-wrap: balance; letter-spacing: -0.025em;`. Paragraphs `text-wrap: pretty; max-width: 65ch;`.
5. **Hit Targets:** controls < 32px get a pseudo-element expansion (`::after` with `inset: -6px`) so the touch zone is >= 44x44px.

## LAYER 4: KINETIC PHYSICS
1. **Hardware-Accelerated Properties Only:** animate ONLY `transform` and `opacity`. Banned: `transition: all`.
2. **Tactile Press Feedback:** active buttons yield: `scale(0.98)` with `100ms cubic-bezier(0.22, 1, 0.36, 1)`.
3. **Timing Budget:**
   - Micro-interactions (hover, focus, toggles): 120ms-160ms (`ease-out`).
   - Popover / Dropdown enter: 180ms (`cubic-bezier(0.16, 1, 0.3, 1)`), exit: 100ms-120ms (exits are always faster than entrances).
   - Workspace mode: no entrance animations on data tables or metrics.

## VERIFY
`python tools/design_lint.py <page.html> [--css file.css] --strict` must exit 0. Rules R01 AI colors, R02 concentric radius, R03 tabular-nums, R04 blur, R05 motion, R06 cardception, R07 filler copy, R08 contrast, R09 tokens.
