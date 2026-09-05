# Ingestion validation — 2026-09-05

The live suite fetched whole arXiv HTML pages through the production gate and
fetcher, with three seconds between requests. It checked deterministic IDs,
section mappings, revision metadata and existence of asserted source anchors.

| arXiv ID | Revision | Display blocks | Sections |
| --- | --- | ---: | ---: |
| 1706.03762 | v7 | 7 | 30 |
| 1810.04805 | v2 | 4 | 46 |
| 2005.14165 | v4 | 1 | 56 |
| 2010.11929 | v2 | 8 | 36 |
| 2103.00020 | v1 | 0 | 55 |
| 2205.14135 | v2 | 42 | 61 |
| 2302.13971 | v1 | 2 | 44 |
| 2307.09288 | v2 | 6 | 126 |
| 2210.02747 | v2 | 94 | 44 |
| 2006.11239 | v2 | 26 | 31 |
| 2106.09685 | v2 | 6 | 42 |
| 2402.08954 | v1 | 0 | 20 |

These are extractor yields, not independently labelled recall/precision.
Blocks can contain pseudocode/examples or partial content requiring review.
Mathematical correctness and novelty are not validated.

Frozen excerpts with source block/fragment expectations are under
services/graph-api/tests/fixtures/corpus. The collector prints candidates and
does not overwrite golden files. Frozen and live checks have different scopes.

Fixes include tbody equation IDs, assembly of alignment cells, preservation of
TeX comments/newlines and long formulas, watermark identity checks, zero-equation
sections and restoration of parent section ownership.

SDK contract tests exposed invalid colon-containing group IDs and nonexistent
episode UUIDs. Real Neo4j integration is a separate unverified gate.
