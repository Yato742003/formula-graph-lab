"""G2 Multi-paper Lineage Assertions, Traversal, and Coverage."""

from __future__ import annotations

import hashlib
import re
from datetime import date
from typing import Any, Literal

from pydantic import (
    Field,
    field_validator,
    model_validator,
)

from app.analysis_versions import canonical_json
from app.problem_spec import FrozenInput, _reject_whitespace_and_length

LineageRelationType = Literal[
    "citation",
    "revision",
    "mathematical_derivation",
    "approximation",
    "shared_objective",
    "implementation",
]

CYCLE_PROHIBITED_RELATIONS: frozenset[str] = frozenset(
    {
        "revision",
        "mathematical_derivation",
    }
)

HEX_64_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
MAX_TRAVERSAL_DEPTH = 8
MAX_TRAVERSAL_NODES = 500


class LineageError(ValueError):
    """Base error for lineage operations."""


class LineageCycleError(LineageError):
    """Adding this assertion would create an illegal cycle."""


class LineageEndpoint(FrozenInput):
    kind: Literal["paper", "equation"]
    id: str = Field(min_length=1, max_length=200)
    version: int | None = Field(default=None, ge=1)

    @field_validator("id", mode="before")
    @classmethod
    def check_id(cls, v: Any) -> str:
        return _reject_whitespace_and_length(v, 200, "id")


class EvidenceReference(FrozenInput):
    source_entity_id: str = Field(min_length=1, max_length=200)
    anchor: str = Field(min_length=1, max_length=200)
    source_hash: str = Field(min_length=64, max_length=64)

    @field_validator("source_entity_id", "anchor", mode="before")
    @classmethod
    def check_strings(cls, v: Any, info: Any) -> str:
        return _reject_whitespace_and_length(v, 200, info.field_name)

    @field_validator("source_hash", mode="before")
    @classmethod
    def check_hash(cls, v: Any) -> str:
        if not isinstance(v, str) or not HEX_64_PATTERN.match(v.strip()):
            raise ValueError("source_hash must be a 64-character hexadecimal string.")
        return v.strip().lower()


class LineageAssertionCreateRequest(FrozenInput):
    relation_type: LineageRelationType
    source: LineageEndpoint  # predecessor
    target: LineageEndpoint  # descendant
    evidence: tuple[EvidenceReference, ...] = Field(min_length=1, max_length=16)
    description: str | None = Field(default=None, max_length=1000)

    @field_validator("description", mode="before")
    @classmethod
    def check_description(cls, v: Any) -> str | None:
        if v is None:
            return None
        return _reject_whitespace_and_length(v, 1000, "description")

    @model_validator(mode="after")
    def validate_endpoints(self) -> LineageAssertionCreateRequest:
        if self.relation_type in {"citation", "revision"} and (
            self.source.kind != "paper" or self.target.kind != "paper"
        ):
            raise ValueError("Citation and revision require paper endpoints.")
        if self.relation_type in {"mathematical_derivation", "approximation"} and (
            self.source.kind != "equation" or self.target.kind != "equation"
        ):
            raise ValueError("Mathematical derivation and approximation require equations.")
        if (
            self.source.kind == self.target.kind
            and self.source.id == self.target.id
            and self.source.version == self.target.version
        ):
            raise ValueError(
                f"Self-loop assertion on {self.source.kind}:{self.source.id} is forbidden."
            )
        return self


class LineageReviewCreateRequest(FrozenInput):
    decision: Literal["reviewed", "rejected"]
    notes: str = Field(default="", max_length=1000)

    @field_validator("notes", mode="before")
    @classmethod
    def check_notes(cls, v: Any) -> str:
        if not isinstance(v, str):
            raise ValueError("notes must be a string.")
        return v.strip()

    @model_validator(mode="after")
    def require_review_rationale(self) -> LineageReviewCreateRequest:
        if not self.notes:
            raise ValueError("A lineage review requires a non-empty rationale.")
        return self


class LineageReview(FrozenInput):
    review_id: str = Field(min_length=1, max_length=200)
    assertion_id: str = Field(min_length=1, max_length=200)
    reviewer_id: str = Field(min_length=1, max_length=200)
    reviewer_role: str = Field(min_length=1, max_length=100)
    decision: Literal["reviewed", "rejected"]
    notes: str = Field(default="", max_length=1000)
    reviewed_at: str = Field(min_length=1, max_length=100)


class LineageAssertionRecord(FrozenInput):
    assertion_id: str = Field(min_length=1, max_length=200)
    workspace_id: str = Field(min_length=1, max_length=200)
    relation_type: LineageRelationType
    source: LineageEndpoint
    target: LineageEndpoint
    evidence: tuple[EvidenceReference, ...]
    description: str | None = None
    created_by: str = Field(min_length=1, max_length=200)
    created_at: str = Field(min_length=1, max_length=100)
    status: Literal["asserted", "reviewed", "rejected"] = "asserted"
    review: LineageReview | None = None


class PaperCoverageRecord(FrozenInput):
    paper_id: str = Field(min_length=1, max_length=200)
    version: int | None = None
    title: str = Field(min_length=1, max_length=500)
    has_html: bool
    first_publication_at: str | None = None
    version_published_at: str | None = None
    venue_published_at: str | None = None
    ingested_at: str = Field(min_length=1, max_length=100)
    warnings: tuple[str, ...] = Field(default=())
    equation_count: int = 0
    source_reference: str | None = None
    registered_by: str | None = None


class MetadataPaperCreateRequest(FrozenInput):
    """A human metadata observation, never an imported HTML source or formula."""

    paper_id: str = Field(min_length=1, max_length=200)
    version: int | None = Field(default=None, ge=1, strict=True)
    title: str = Field(min_length=1, max_length=500)
    source_reference: str = Field(min_length=1, max_length=2048)
    first_publication_at: date | None = None
    version_published_at: date | None = None
    venue_published_at: date | None = None

    @field_validator("paper_id", "title", "source_reference", mode="before")
    @classmethod
    def require_text(cls, value: Any, info: Any) -> str:
        limits = {"paper_id": 200, "title": 500, "source_reference": 2048}
        return _reject_whitespace_and_length(value, limits[info.field_name], info.field_name)


class LineageCoverageResponse(FrozenInput):
    workspace_id: str
    indexed_papers: tuple[PaperCoverageRecord, ...]
    coverage_gaps: tuple[str, ...]
    timestamp: str


def generate_assertion_id(
    workspace_id: str,
    relation_type: str,
    source_key: str,
    target_key: str,
    content: object | None = None,
) -> str:
    raw = canonical_json([workspace_id, relation_type, source_key, target_key, content])
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return f"lineage_{digest}"


def check_cycle(
    existing_edges: list[tuple[str, str, str]],  # (source_key, target_key, relation_type)
    new_source: str,
    new_target: str,
    relation_type: str,
) -> bool:
    """Return True if adding new_source -> new_target with relation_type creates a cycle."""
    if new_source == new_target:
        return True
    if relation_type not in CYCLE_PROHIBITED_RELATIONS:
        return False

    # Check if a directed path already exists from new_target -> new_source
    adj: dict[str, list[str]] = {}
    for s, t, r in existing_edges:
        if r in CYCLE_PROHIBITED_RELATIONS:
            adj.setdefault(s, []).append(t)

    visited: set[str] = set()
    queue = [new_target]
    while queue:
        current = queue.pop()
        if current == new_source:
            return True
        if current not in visited:
            visited.add(current)
            for neighbor in adj.get(current, []):
                if neighbor not in visited:
                    queue.append(neighbor)
    return False
