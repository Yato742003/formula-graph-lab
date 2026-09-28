"""Content-addressed analysis records; source evidence is never rewritten."""

from __future__ import annotations

import hashlib
import json
from uuid import NAMESPACE_URL, uuid5

SCHEMA_VERSION = "formula-analysis.v1"
ANALYZER_VERSION = "formula-analyzer.v7"
MIGRATION_VERSION = "analysis-separation.v1"


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)


def source_payload(payload: str) -> dict:
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise ValueError("Equation source must be an object.")
    return {key: item for key, item in value.items() if key != "formula_analysis"}


def source_hash(payload: str) -> str:
    return hashlib.sha256(canonical_json(source_payload(payload)).encode("utf-8")).hexdigest()


def make_analysis_version(
    equation_uuid: str,
    source: str,
    analysis: dict,
    *,
    analyzer_version: str = ANALYZER_VERSION,
) -> dict:
    payload = canonical_json(analysis)
    input_hash = source_hash(source)
    canonicalizer = analysis.get("canonicalizer_version")
    identity = canonical_json([
        equation_uuid, SCHEMA_VERSION, analyzer_version, canonicalizer, input_hash, payload,
    ])
    return {
        "uuid": str(uuid5(NAMESPACE_URL, identity)),
        "equation_uuid": equation_uuid,
        "payload": payload,
        "schema_version": SCHEMA_VERSION,
        "analyzer_version": analyzer_version,
        "canonicalizer_version": canonicalizer,
        "syntax_hash": analysis.get("syntax_hash"),
        "source_hash": input_hash,
    }
