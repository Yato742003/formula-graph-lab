---
name: FormulaGraph Lab Design System
description: Scientific Precision & Epistemic Honesty — Dual 2D/3D Mathematical Evidence Graph & Verification Workbench
colors:
  background: "oklch(0.975 0.012 255)"
  foreground: "oklch(0.205 0.025 258)"
  card: "oklch(1 0 0)"
  primary: "oklch(0.46 0.18 256)"
  primary-foreground: "oklch(0.985 0.002 255)"
  secondary: "oklch(0.95 0.028 226)"
  secondary-foreground: "oklch(0.31 0.06 261)"
  muted: "oklch(0.955 0.014 250)"
  muted-foreground: "oklch(0.51 0.025 257)"
  accent: "oklch(0.92 0.06 190)"
  accent-foreground: "oklch(0.29 0.1 202)"
  destructive: "oklch(0.58 0.22 28)"
  border: "oklch(0.87 0.025 250)"
  ring: "oklch(0.58 0.16 220)"
  evidence: "oklch(0.55 0.19 255)"
  citation: "oklch(0.62 0.15 166)"
  derivation: "oklch(0.7 0.16 67)"
  review: "oklch(0.58 0.16 315)"
  surface-evidence: "oklch(0.965 0.025 255)"
  surface-citation: "oklch(0.96 0.035 166)"
  surface-derivation: "oklch(0.965 0.045 67)"
  surface-review: "oklch(0.965 0.03 315)"
typography:
  ui-sans:
    fontFamily: "var(--font-geist-sans), Arial, Helvetica, sans-serif"
    fontSize: "0.9375rem"
    fontWeight: 400
    lineHeight: 1.5
  mono-data:
    fontFamily: "var(--font-geist-mono), ui-monospace, monospace"
    fontSize: "0.75rem"
    fontWeight: 500
    lineHeight: 1.4
  formula-node:
    fontFamily: "KaTeX_Main, var(--font-geist-sans), serif"
    fontSize: "0.82rem"
    color: "#1d2a4d"
  formula-inspector:
    fontFamily: "KaTeX_Main, var(--font-geist-sans), serif"
    fontSize: "1.02rem"
rounded:
  base: "0.55rem"
  sm: "calc(var(--radius) * 0.6)"
  md: "calc(var(--radius) * 0.8)"
  lg: "var(--radius)"
  xl: "calc(var(--radius) * 1.4)"
  2xl: "calc(var(--radius) * 1.8)"
  swimlane: "18px"
  full: "9999px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "16px"
  lg: "24px"
  xl: "32px"
  2xl: "48px"
---

# FormulaGraph Lab — Design System & Surface Contracts

## 1. Design Direction: Scientific Precision & Epistemic Honesty

- **Single Navigation Surface**: Exactly one primary 5-stage navigation rail (`1. Graph` → `2. Spec` → `3. Compatibility` → `4. Proposals` → `5. Reports`) plus utility links (`/ops`, `/help`) in `components/workspace-shell.tsx` and `app/research-workspace.tsx`. Duplicate view-switching controls are prohibited (`docs/UI_UPGRADE_AUDIT.md`).
- **One Meaning, One Semantic Color Token**: Every graph role and verification boundary maps consistently across 2D nodes, legends, inspectors, and reports via `app/globals.css`:
  - **Evidence** (`--color-evidence` / `--surface-evidence`) — immutable source paper/equation artifacts.
  - **Citation / Concept** (`--color-citation` / `--surface-citation`) — structural/citation links.
  - **Derivation / Hypothesis** (`--color-derivation` / `--surface-derivation`) — candidate transformations and mathematical derivations.
  - **Review / Gate** (`--color-review` / `--surface-review`) — human review and protocol authorization gates.
- **No False Confidence**: Untested compatibility gates stay neutral (`background: var(--muted)`), example ProblemSpec presets carry draft labels, and synthetic research receipts prominently display `synthetic_operator_only_no_product_claim` alongside holdout uncertainty (`docs/PHASE5_FLOW_AUDIT.json`).

## 2. Visual Hierarchy & Surface Contracts

### A. 2D Evidence Flow Viewport (`app/evidence-graph-viewport.tsx`)
- **Section Swimlane Containers**: Frosted glass containers (`rgba(248, 250, 252, 0.72)`, `1.5px dashed rgba(148, 163, 184, 0.55)`, `border-radius: 18px`, `backdrop-filter: blur(12px)`), maximum `4` lanes per row (`MAX_LANES_PER_ROW = 4`) to keep a balanced aspect ratio.
- **Node Card Geometry**:
  - **Compact mode**: `220px × 58px`, gap `14px`, swimlane gap `36px`.
  - **Detailed mode**: `320px × 155px`, gap `20px`, swimlane gap `48px` (`.is-compact` must never be overridden by detailed card width rules).
  - **Source Paper Anchor**: Preceding column at `x: 36` inside its own `Source Paper` swimlane container (`swimlane-paper-root`), keeping structural containment wires suppressed inside swimlanes to prevent crossing clutter.

### B. 3D Sacred Timeline (`app/graph-3d-viewport.tsx`)
- **Dark Volumetric Canvas**: Deep cosmic backdrop paired with muted caption tokens (`#94a3b8` verified by `tests/contrast-tokens.test.ts`), deterministic golden-angle spiral layout, and cylindrical camera bounding-box framing.
- **Geometry-Coded Entities**:
  - `Equation`: Octahedron (`0x22d3ee`)
  - `Symbol`: Tetrahedron (`0xa855f7`)
  - `Hypothesis`: Dodecahedron (`0xc084fc`)
  - `Paper` / `PaperVersion` / `Section` / `Assumption`: Sphere (`0xffffff`, `0x67e8f9`, `0x38bdf8`, `0xf43f5e`)
- **Progressive Control Disclosure**: Primary camera controls remain visible; advanced visual knobs appear in fullscreen mode only (`tests/graph-3d-viewport.test.tsx`).

### C. Workbench & Verification Surfaces
- **Scorecards & Receipts**: Candidate comparison scorecards (`.research-benchmark-scorecard`), SHA-256 content hashes in `var(--font-geist-mono)` with `select-all` and copy buttons, and explicit `aria-live` status regions.
- **CSP Style Boundary**: Scripts use per-request random nonces (`'strict-dynamic'`, no `unsafe-inline`, no `unsafe-eval`); `style-src-attr 'unsafe-inline'` is retained solely as an explicit exception for runtime KaTeX and ReactFlow geometry attributes (`proxy.ts`).

## 3. Formula Ergonomics, Keyboard Navigation & Contrast Rules

- **Zero Formula Overflow**: Both `.flow-evidence-node .node-latex-math` and `.formula-display .inspector-latex-math` use single-line horizontal scrolling (`white-space: nowrap`) with a sleek `4px` custom scrollbar and hidden native arrow buttons so long tensors/integrals never blow out card widths.
- **Keyboard Ergonomics**:
  - `Ctrl+K` or `/` opens graph search.
  - Arrow keys traverse selected nodes inside `EvidenceGraphViewport`.
  - All primary buttons, stage tabs, and `.onboarding-toggle` controls maintain accessible contrast (`>= 4.5:1`) and minimum `44px` hit height where required (`tests/color-system.test.ts`, `tests/contrast-tokens.test.ts`).
