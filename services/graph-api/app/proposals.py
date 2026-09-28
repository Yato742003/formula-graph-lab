"""Strict P1 proposal boundary; model output remains an untrusted draft."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from pydantic import Field, ValidationError, field_validator, model_validator

from app.analysis_versions import canonical_json
from app.problem_spec import FrozenInput, ProblemSpecSnapshot, definition_hash
from app.transformation_dsl import validate_transform

MAX_PROPOSAL_BYTES = 16 * 1024
MAX_PROPOSAL_ATTEMPTS = 3
_ID = r"^[A-Za-z0-9_:-]{1,200}$"
_HTML_TAG = re.compile(r"</?[A-Za-z][^>]*>")
_SOURCE_SPAN_ID = re.compile(r"^span_[A-Za-z0-9_-]{1,600}$")


def make_source_span_id(source_entity_id: str, anchor: str) -> str:
    if not re.fullmatch(_ID, source_entity_id) or not anchor or len(anchor) > 200:
        raise ValueError("Source span reference is invalid.")
    encoded = base64.urlsafe_b64encode(
        canonical_json([source_entity_id, anchor]).encode("utf-8")
    ).decode("ascii").rstrip("=")
    return f"span_{encoded}"


def parse_source_span_id(value: str) -> tuple[str, str]:
    if not isinstance(value, str) or not _SOURCE_SPAN_ID.fullmatch(value):
        raise ValueError("Source span ID is invalid.")
    encoded = value[5:]
    try:
        decoded = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        pair = json.loads(decoded)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Source span ID is invalid.") from exc
    if (
        not isinstance(pair, list)
        or len(pair) != 2
        or not all(isinstance(part, str) for part in pair)
        or make_source_span_id(pair[0], pair[1]) != value
    ):
        raise ValueError("Source span ID is invalid.")
    return pair[0], pair[1]


class ProposalDraft(FrozenInput):
    """Only model-authored fields; identity and trust labels are server-owned."""

    problem_spec_id: str = Field(min_length=1, max_length=200)
    parent_ids: tuple[str, ...] = Field(min_length=1, max_length=8)
    source_span_ids: tuple[str, ...] = Field(min_length=1, max_length=32)
    transform: dict[str, Any]
    assumptions: tuple[str, ...] = Field(default=(), max_length=32)
    rationale: str = Field(min_length=1, max_length=4000)
    expected_effect: str = Field(min_length=1, max_length=1000)

    @field_validator("parent_ids", mode="before")
    @classmethod
    def validate_parent_ids(cls, value: Any) -> Any:
        if not isinstance(value, (list, tuple)) or any(
            not isinstance(item, str) or not re.fullmatch(_ID, item)
            for item in value
        ):
            raise ValueError("Proposal references must be bounded identifiers.")
        if len(set(value)) != len(value):
            raise ValueError("Proposal references must not contain duplicates.")
        return value

    @field_validator("source_span_ids", mode="before")
    @classmethod
    def validate_source_span_ids(cls, value: Any) -> Any:
        if not isinstance(value, (list, tuple)) or any(
            not isinstance(item, str) for item in value
        ):
            raise ValueError("Proposal source spans must be encoded source references.")
        for item in value:
            parse_source_span_id(item)
        if len(set(value)) != len(value):
            raise ValueError("Proposal source spans must not contain duplicates.")
        return value

    @field_validator("assumptions", mode="before")
    @classmethod
    def validate_assumptions(cls, value: Any) -> Any:
        if not isinstance(value, (list, tuple)) or any(
            not isinstance(item, str)
            or not item.strip()
            or len(item) > 500
            or _HTML_TAG.search(item)
            for item in value
        ):
            raise ValueError("Proposed assumptions must be plain bounded text.")
        return tuple(item.strip() for item in value)

    @field_validator("rationale", "expected_effect", mode="before")
    @classmethod
    def validate_text(cls, value: Any) -> str:
        if not isinstance(value, str) or not value.strip() or _HTML_TAG.search(value):
            raise ValueError("Proposal explanation must be plain text without HTML markup.")
        return value.strip()


class ProposedAssumption(FrozenInput):
    text: str = Field(min_length=1, max_length=500)
    origin: Literal["ai_proposed"] = "ai_proposed"
    discharged: Literal[False] = False


class ResearchProposal(FrozenInput):
    proposal_id: str = Field(pattern=r"^prop_[0-9a-f]{32}$")
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    workspace_id: str = Field(min_length=1, max_length=200)
    problem_spec_id: str = Field(min_length=1, max_length=200)
    problem_spec_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    parent_ids: tuple[str, ...] = Field(min_length=1, max_length=8)
    source_span_ids: tuple[str, ...] = Field(min_length=1, max_length=32)
    transform_json: str = Field(min_length=2, max_length=8 * 1024)
    operator: str = Field(min_length=1, max_length=100)
    operator_version: str = Field(min_length=1, max_length=50)
    semantics_class: Literal["preserving", "approximation", "hypothesis_changing"]
    assumptions: tuple[ProposedAssumption, ...] = Field(max_length=32)
    rationale: str = Field(min_length=1, max_length=4000)
    expected_effect: str = Field(min_length=1, max_length=1000)
    review_state: Literal["pending"] = "pending"

    @model_validator(mode="after")
    def verify_identity(self) -> ResearchProposal:
        identity = self.model_dump(mode="json", exclude={"proposal_id", "content_hash"})
        digest = hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest()
        if self.content_hash != digest or self.proposal_id != f"prop_{digest[:32]}":
            raise ValueError("Proposal identity does not match its immutable content.")
        return self


class ProposalReviewCreateRequest(FrozenInput):
    decision: Literal["accept_for_compilation", "reject"]
    notes: str = Field(min_length=1, max_length=2000)

    @field_validator("notes", mode="before")
    @classmethod
    def validate_notes(cls, value: Any) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > 2000:
            raise ValueError("A bounded reviewer rationale is required.")
        return value.strip()


class ProposalReviewRecord(FrozenInput):
    review_id: str = Field(pattern=r"^prev_[0-9a-f]{32}$")
    review_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    workspace_id: str = Field(min_length=1, max_length=200)
    proposal_id: str = Field(pattern=r"^prop_[0-9a-f]{32}$")
    proposal_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviewer_id: str = Field(min_length=1, max_length=200)
    reviewer_role: Literal["researcher", "reviewer", "admin"]
    decision: Literal["accept_for_compilation", "reject"]
    notes: str = Field(min_length=1, max_length=2000)
    reviewed_at: datetime
    schema_version: Literal["proposal-review.v1"] = "proposal-review.v1"

    @model_validator(mode="after")
    def verify_identity(self) -> ProposalReviewRecord:
        if self.reviewed_at.tzinfo is None or self.reviewed_at.utcoffset() is None:
            raise ValueError("Proposal review time must be timezone-aware.")
        identity = self.model_dump(mode="json", exclude={"review_id", "review_hash"})
        digest = hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest()
        if self.review_hash != digest or self.review_id != f"prev_{digest[:32]}":
            raise ValueError("Proposal review identity does not match its immutable content.")
        return self


class ProposalReviewResponse(FrozenInput):
    review: ProposalReviewRecord
    replayed: bool


@dataclass(frozen=True)
class ProposalGeneration:
    proposal: ResearchProposal
    attempts: int


class ProposalGenerationError(ValueError):
    def __init__(self, attempts: int):
        self.attempts = attempts
        super().__init__("PROPOSAL_RETRY_LIMIT_REACHED")


class ProposalCreateRequest(FrozenInput):
    workspace_id: str = Field(min_length=1, max_length=200)
    output_json: str = Field(min_length=2, max_length=MAX_PROPOSAL_BYTES)


class ProposalGenerateRequest(FrozenInput):
    spec_id: str = Field(min_length=1, max_length=200)
    parent_ids: tuple[str, ...] = Field(min_length=2, max_length=8)
    source_span_ids: tuple[str, ...] = Field(min_length=2, max_length=8)
    research_question: str = Field(min_length=1, max_length=1000)

    @field_validator("parent_ids", mode="before")
    @classmethod
    def validate_generation_parents(cls, value: Any) -> Any:
        if not isinstance(value, (list, tuple)) or any(
            not isinstance(item, str) or not re.fullmatch(_ID, item) for item in value
        ):
            raise ValueError("Generation parents must be bounded identifiers.")
        if len(value) < 2 or len(set(value)) != len(value):
            raise ValueError("Choose at least two distinct equation parents.")
        return value

    @field_validator("source_span_ids", mode="before")
    @classmethod
    def validate_generation_sources(cls, value: Any) -> Any:
        if not isinstance(value, (list, tuple)) or any(not isinstance(item, str) for item in value):
            raise ValueError("Generation sources must be stable source-span IDs.")
        for item in value:
            parse_source_span_id(item)
        if len(value) < 2 or len(value) > 8 or len(set(value)) != len(value):
            raise ValueError("Choose two to eight distinct source spans.")
        return value

    @field_validator("research_question", mode="before")
    @classmethod
    def validate_research_question(cls, value: Any) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > 1000:
            raise ValueError("A bounded research question is required.")
        return value.strip()


class ProposalCreateResponse(FrozenInput):
    proposal: ResearchProposal
    replayed: bool


def _parse_model_output(raw: object) -> object:
    if isinstance(raw, bytes):
        if len(raw) > MAX_PROPOSAL_BYTES:
            raise ValueError("Proposal output exceeds the size limit.")
        try:
            raw = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ValueError("Proposal output must be UTF-8 JSON.") from exc
    if isinstance(raw, str):
        if len(raw.encode("utf-8")) > MAX_PROPOSAL_BYTES:
            raise ValueError("Proposal output exceeds the size limit.")

        def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("Proposal JSON must not contain duplicate keys.")
                result[key] = value
            return result

        try:
            return json.loads(raw, object_pairs_hook=reject_duplicate_keys)
        except (json.JSONDecodeError, RecursionError) as exc:
            raise ValueError("Proposal output must be valid JSON.") from exc
    try:
        if len(canonical_json(raw).encode("utf-8")) > MAX_PROPOSAL_BYTES:
            raise ValueError("Proposal output exceeds the size limit.")
    except (TypeError, RecursionError) as exc:
        raise ValueError("Proposal output must be finite JSON data.") from exc
    return raw


def _require_scope(workspace_id: str, spec: ProblemSpecSnapshot) -> None:
    if (
        not workspace_id.strip()
        or spec.workspace_id != workspace_id
        or definition_hash(spec.definition) != spec.content_hash
    ):
        raise ValueError("Proposal ProblemSpec is not a valid snapshot in this workspace.")


def validate_model_proposal(
    raw: object,
    *,
    workspace_id: str,
    spec: ProblemSpecSnapshot,
    allowed_parent_ids: frozenset[str],
    allowed_source_span_ids: frozenset[str],
    allowed_target_node_ids: frozenset[str],
) -> ResearchProposal:
    """Validate a model draft against server-resolved scope, then assign trust labels."""
    _require_scope(workspace_id, spec)
    draft = ProposalDraft.model_validate(_parse_model_output(raw))
    if draft.problem_spec_id != spec.spec_id:
        raise ValueError("Proposal references a different ProblemSpec.")
    if set(draft.parent_ids) - allowed_parent_ids:
        raise ValueError("Proposal contains an unresolved or unauthorized parent.")
    if set(draft.source_span_ids) - allowed_source_span_ids:
        raise ValueError("Proposal contains an unresolved or unauthorized source span.")

    transform, manifest = validate_transform(
        draft.transform, spec.definition.allowed_transforms
    )
    if transform.target_node_id not in allowed_target_node_ids:
        raise ValueError("Proposal transform target is not in the authorized snapshot.")

    proposal_data = {
        "workspace_id": workspace_id,
        "problem_spec_id": spec.spec_id,
        "problem_spec_hash": spec.content_hash,
        "parent_ids": draft.parent_ids,
        "source_span_ids": draft.source_span_ids,
        "transform_json": canonical_json(transform.model_dump(mode="json", by_alias=True)),
        "operator": manifest.name,
        "operator_version": manifest.version,
        "semantics_class": manifest.semantics_class,
        "assumptions": [
            ProposedAssumption(text=text).model_dump(mode="json") for text in draft.assumptions
        ],
        "rationale": draft.rationale,
        "expected_effect": draft.expected_effect,
        "review_state": "pending",
    }
    digest = hashlib.sha256(canonical_json(proposal_data).encode("utf-8")).hexdigest()
    return ResearchProposal(
        proposal_id=f"prop_{digest[:32]}",
        content_hash=digest,
        **proposal_data,
    )


def generate_validated_proposal(
    invoke: Callable[[int, bool], object],
    *,
    workspace_id: str,
    spec: ProblemSpecSnapshot,
    allowed_parent_ids: frozenset[str],
    allowed_source_span_ids: frozenset[str],
    allowed_target_node_ids: frozenset[str],
) -> ProposalGeneration:
    """Retry malformed output at most three times; provider failures are not hidden."""
    _require_scope(workspace_id, spec)
    for attempt in range(1, MAX_PROPOSAL_ATTEMPTS + 1):
        raw = invoke(attempt, attempt > 1)
        try:
            proposal = validate_model_proposal(
                raw,
                workspace_id=workspace_id,
                spec=spec,
                allowed_parent_ids=allowed_parent_ids,
                allowed_source_span_ids=allowed_source_span_ids,
                allowed_target_node_ids=allowed_target_node_ids,
            )
            return ProposalGeneration(proposal=proposal, attempts=attempt)
        except (ValidationError, ValueError):
            if attempt == MAX_PROPOSAL_ATTEMPTS:
                raise ProposalGenerationError(attempt) from None
    raise AssertionError("unreachable")
