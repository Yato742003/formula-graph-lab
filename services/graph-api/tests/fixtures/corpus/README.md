# Frozen arXiv structure excerpts

Collected 2026-09-05. Attribution/source URLs and expected block anchors are in
expected.json. These are reduced test excerpts, not copies of the papers.
They retain one initial display block (or group) per source, the page title, and
the arXiv version watermark. Presentation MathML is omitted where TeX is already
present in an annotation or alttext. No scripts, external assets or styles run.

The 10 display-containing fixtures exercise extraction of observed source
structures; CLIP and the arXiv HTML article provide two zero-display cases.
expected.json records each source tbody/block's MathML fragments and equation
number, independently of the production extractor. Rows with no MathML aren't
counted. BERT contains text laid out as mathematics; its extracted MathML portion
must be flagged for human review, not presented as a complete mathematical law.

scripts/collect_corpus_fixtures.py prints candidates for review. It does not
overwrite fixtures or silently update expected values. The opt-in live suite
checks whole current pages; frozen excerpts give repeatable offline regression.
Neither suite proves equation precision/recall over all arXiv papers. That
requires the separately reviewed beta dataset in Sprint 7.
