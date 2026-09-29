# FormulaGraph Lab UI upgrade audit

## Input and evidence

- Source mode: local repository (`app/research-workspace.tsx`, `app/globals.css`).
- Manual evidence: local `http://localhost:3000/` at a 1280 × 720 viewport, viewed in the Codex in-app browser on 2026-09-29.
- Scope: the research workspace shell, lineage graph, paper source rail, inspector rail, and research-move entry point.
- Privacy: local-only; no external assets or user data were collected.

## Ranked findings

| Priority | Issue | Evidence | Impact | Fix direction |
| --- | --- | --- | --- | --- |
| P1 | The workflow is implicit. | Problem Spec, Lineage Graph, and Compatibility are tabs, while Research move is a detached dock below the graph. | A new researcher cannot tell what to do next or whether a candidate is ready to move from evidence to verification. | Add a compact, keyboard-accessible four-step workflow rail that keeps the active stage and next transition visible. |
| P1 | Primary action is separated from context. | The active paper strip is above the three-column workspace; the graph state is inside the center panel; the candidate action is at the bottom. | Users must scan multiple regions to understand “where am I?” before acting. | Keep a single status rail directly below the graph header and link each stage to the existing view/action. |
| P2 | Dense source navigation competes with the graph. | The persisted paper exposes a long multi-paper and extracted-structure list beside the graph. | Useful provenance is present, but the next research action can be visually buried. | Improve hierarchy without removing provenance: reserve the new rail for task state and leave the source list intact. |

## Fix brief

- Reuse the existing three view transitions and the existing research-move panel; do not add a new router or state machine.
- Use a restrained surface, one active accent, and text labels that remain meaningful without color.
- Preserve the existing evidence/verification boundary: the rail must not imply that a candidate is verified.
- Keep the rail responsive so it wraps into a scrollable row on narrow viewports.

## What not to change

- Do not add decorative gradients, hero copy, or generic metric cards.
- Do not hide paper provenance, source anchors, or the inspector.
- Do not add a model-generated status or fitness claim to the workflow chrome.

## Verification plan

- Add a DOM-level test for the four workflow stages and the active stage.
- Run the focused workspace test, TypeScript, lint, and production build.
- Re-check the local page at desktop and narrow viewport widths.
