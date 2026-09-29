# FormulaGraph Lab UI upgrade audit

## Input and evidence

- Source mode: local repository (`app/research-workspace.tsx`, `app/globals.css`).
- Manual evidence: local `http://localhost:3000/` at a 1280 × 720 viewport, viewed in the Codex in-app browser on 2026-09-29.
- Scope: the research workspace shell, lineage graph, paper source rail, inspector rail, and research-move entry point.
- Privacy: local-only; no external assets or user data were collected.

## Ranked findings

| Priority | Issue | Evidence | Impact | Fix direction |
| --- | --- | --- | --- | --- |
| P1 | Two navigation systems expose the same views. | The top bar has Papers / Graph / Research while the workspace exposes Lineage Graph / Problem Spec / Compatibility; both change the same view state. | Users see duplicate controls and must learn two labels for one action. | Keep the workspace tabs as the single view navigation; remove the duplicate top-nav buttons. |
| P1 | Primary action is separated from context. | The active paper strip is above the three-column workspace; the graph state is inside the center panel; the candidate action is at the bottom. | Users must scan multiple regions to understand “where am I?” before acting. | Keep one compact current-stage status and one Research move action directly below the graph header. |
| P2 | Dense source navigation competes with the graph. | The persisted paper exposes a long multi-paper and extracted-structure list beside the graph. | Useful provenance is present, but the next research action can be visually buried. | Improve hierarchy without removing provenance: reserve the new rail for task state and leave the source list intact. |

## Fix brief

- Reuse the existing three workspace tabs and research-move panel; do not add a new router or state machine.
- Use a restrained surface, one active accent, and text labels that remain meaningful without color.
- Keep only short stage labels (`01 · Scope`, `02 · Trace`, `03 · Verify`) in the status rail; the tabs carry the full view names.
- Preserve the existing evidence/verification boundary: the rail must not imply that a candidate is verified.
- Keep the rail responsive so the status and action wrap without horizontal overflow on narrow viewports.

## What not to change

- Do not add decorative gradients, hero copy, or generic metric cards.
- Do not hide paper provenance, source anchors, or the inspector.
- Do not add a model-generated status or fitness claim to the workflow chrome.

## Verification plan

- Add a DOM-level test that confirms there is one view-navigation surface and that the compact status follows the selected tab.
- Run the focused workspace test, TypeScript, lint, and production build.
- Re-check the local page at desktop and narrow viewport widths.
