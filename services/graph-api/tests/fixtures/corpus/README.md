# Frozen arXiv structure excerpts

Collected 2026-09-05; selected attention excerpts were extended 2026-09-27.
Attribution/source URLs and expected equation anchors are in expected.json.
These are reduced test excerpts, not copies of the papers. They retain selected
display blocks, small contextual/source-reference passages for the attention
lineage integration test, page titles, and arXiv version watermarks.
Presentation MathML is omitted where TeX is already in an annotation or alttext.
No scripts, external assets or styles run.

The display-containing fixtures exercise extraction of observed source
structures; CLIP and the arXiv HTML article provide zero-display cases. The
attention seed set includes Transformer, Linear Transformers, Performer and
FlashAttention. Each excerpt is attributed and version-pinned; equation anchors
are fixed in `expected.json`. A Neo4j integration test exercises source-backed
citation, within-paper derivation, approximation, and implementation assertions
as separate edge types. The assertions remain `asserted`, not human-reviewed.
These excerpts are not full paper snapshots or evidence of official code
execution or benchmark results.
expected.json records each source tbody/block's MathML fragments and equation
number, independently of the production extractor. Rows with no MathML aren't
counted. BERT contains text laid out as mathematics; its extracted MathML portion
must be flagged for human review, not presented as a complete mathematical law.

scripts/collect_corpus_fixtures.py prints candidates for review. It does not
overwrite fixtures or silently update expected values. The opt-in live suite
checks whole current pages; frozen excerpts give repeatable offline regression.
Neither suite proves equation precision/recall over all arXiv papers. That
requires the separately reviewed beta dataset in Sprint 7.
