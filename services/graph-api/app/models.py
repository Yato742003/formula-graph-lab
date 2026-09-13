from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator

from app.verification import VerificationVector


class PaperImportRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)


class EvidenceImportRequest(PaperImportRequest):
    workspace_id: str = Field(min_length=1, max_length=200, pattern=r".*\S.*")


class ExtractedEquation(BaseModel):
    equation_id: str
    anchor: str
    anchor_is_source: bool = True
    latex: str
    source_fragments: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    equation_number: str | None = None
    section: str | None = None
    section_id: str | None = None
    preceding_text: str | None = None
    following_text: str | None = None
    extraction_method: Literal["tex_annotation", "alttext", "mathml_text", "assembled_tex"]
    confidence: float = Field(ge=0, le=1)


class ExtractedSection(BaseModel):
    section_id: str
    anchor: str
    anchor_is_source: bool = True
    title: str
    order: int = Field(ge=1)
    parent_section_id: str | None = None
    text: str = ""
    equation_ids: list[str] = Field(default_factory=list)


class ExtractedPaper(BaseModel):
    paper_id: str
    version: int | None = None
    title: str
    authors: list[str] = Field(default_factory=list)
    version_published_at: date | None = None
    metadata_warnings: list[str] = Field(default_factory=list)
    source_url: HttpUrl
    source_sha256: str
    sections: list[ExtractedSection] = Field(default_factory=list)
    equations: list[ExtractedEquation]


class HealthResponse(BaseModel):
    status: Literal["ok"]
    graph_backend: Literal["not_configured", "configured"]
    checked_at: datetime


class EvidenceImportReceipt(BaseModel):
    import_uuid: str
    node_count: int = Field(ge=0)
    edge_count: int = Field(ge=0)
    episode_count: int = Field(ge=1)
    replayed: bool


class EvidenceImportResponse(BaseModel):
    paper: ExtractedPaper
    receipt: EvidenceImportReceipt


EvidenceEntityType = Literal[
    "Paper",
    "PaperVersion",
    "Section",
    "Equation",
    "Symbol",
    "Assumption",
    "Claim",
    "Concept",
    "Method",
    "Experiment",
    "Hypothesis",
]
VerificationStatus = Literal[
    "reported",
    "draft",
    "invalid",
    "well_typed",
    "numerically_plausible",
    "symbolically_verified",
    "human_reviewed",
]
EvidenceRelationType = Literal[
    "has_version",
    "contains",
    "defines",
    "uses",
    "assumes",
    "cites",
    "makes_claim",
    "about",
    "derived_from",
    "approximates",
    "generalizes",
    "equivalent_under",
    "disagrees_with",
    "supersedes",
]


class EvidenceSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500, pattern=r".*\S.*")
    workspace_id: str = Field(min_length=1, max_length=200, pattern=r".*\S.*")
    paper_id: str | None = Field(default=None, pattern=r"^\d{4}\.\d{4,5}$")
    version: int | None = Field(default=None, ge=1)
    entity_types: list[EvidenceEntityType] = Field(default_factory=list, max_length=20)
    verification_statuses: list[VerificationStatus] = Field(
        default_factory=list,
        max_length=10,
    )
    as_of: datetime | None = None
    center_node_uuid: str | None = Field(default=None, max_length=200)
    limit: int = Field(default=20, ge=1, le=50)
    cursor: str | None = Field(default=None, max_length=2_048)

    @field_validator("entity_types", "verification_statuses")
    @classmethod
    def require_unique_filters(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("Search filters must not contain duplicates.")
        return values

    @field_validator("as_of")
    @classmethod
    def require_aware_time(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("Search time must be timezone-aware.")
        return value

    @field_validator("center_node_uuid")
    @classmethod
    def require_uuid(cls, value: str | None) -> str | None:
        if value is not None:
            UUID(value)
        return value


class SearchScoreComponents(BaseModel):
    lexical_rank: int | None = Field(default=None, ge=1)
    semantic_rank: int | None = Field(default=None, ge=1)
    graph_rank: int | None = Field(default=None, ge=1)
    graph_distance: int | None = Field(default=None, ge=1, le=2)


class EvidenceSearchHit(BaseModel):
    uuid: str
    kind: EvidenceEntityType
    logical_id: str
    paper_id: str
    version: int | None
    valid_at: datetime | None
    verification_status: VerificationStatus
    payload: dict[str, Any]
    episode_uuids: list[str]
    score: int = Field(ge=1)
    match_sources: list[Literal["lexical", "semantic", "graph"]]
    score_components: SearchScoreComponents


class EvidenceSearchResponse(BaseModel):
    hits: list[EvidenceSearchHit]
    next_cursor: str | None
    semantic_available: bool


class EvidenceGraphSnapshotRequest(BaseModel):
    workspace_id: str = Field(min_length=1, max_length=200, pattern=r".*\S.*")
    paper_id: str = Field(pattern=r"^\d{4}\.\d{4,5}$")
    version: int = Field(ge=1)


class EvidenceGraphNode(BaseModel):
    uuid: str = Field(min_length=1, max_length=200)
    kind: EvidenceEntityType
    logical_id: str = Field(min_length=1, max_length=500)
    paper_id: str
    version: int | None
    valid_at: datetime | None
    verification_status: VerificationStatus
    payload: dict[str, Any]
    episode_uuids: list[str]


class EvidenceGraphEdge(BaseModel):
    uuid: str = Field(min_length=1, max_length=500)
    source_uuid: str = Field(min_length=1, max_length=200)
    target_uuid: str = Field(min_length=1, max_length=200)
    relation: EvidenceRelationType
    source_anchor: str = Field(max_length=500)
    episode_uuids: list[str]
    valid_at: datetime | None


class EvidenceGraphSnapshotResponse(BaseModel):
    nodes: list[EvidenceGraphNode]
    edges: list[EvidenceGraphEdge]
    truncated: bool


# ── Formula API models (FGL-402/403) ─────────────────────────────


ConcreteSymbolCategory = Literal[
    "scalar",
    "vector",
    "matrix",
    "tensor",
    "function",
    "distribution",
    "index",
]
FormulaSymbolCategory = Literal[
    "scalar",
    "vector",
    "matrix",
    "tensor",
    "function",
    "distribution",
    "index",
    "unknown",
]
FormulaSymbolDomain = Literal["real", "positive", "non_negative", "complex", "integer"]


class FormulaParseRequest(BaseModel):
    latex: str = Field(min_length=1, max_length=20_000)
    format: Literal["latex", "mathml"] = "latex"
    section_id: str | None = Field(default=None, min_length=1, max_length=500)
    extraction_confidence: float = Field(default=1.0, ge=0, le=1)

    # Review records are created through an authenticated human-review workflow,
    # never inline with untrusted parse/model input.
    model_config = {"extra": "forbid"}


class FormulaSymbolResponse(BaseModel):
    name: str
    category: FormulaSymbolCategory
    indices: list[str]
    style: str | None


class FormulaContractResponse(BaseModel):
    name: str
    category: FormulaSymbolCategory
    shape: list[int | str] | None
    domain: FormulaSymbolDomain
    constraints: list[str]
    scope: str | None
    inference_confidence: float
    review_required: bool


class FormulaExtractionAssessmentResponse(BaseModel):
    confidence: float = Field(ge=0, le=1)
    source: Literal["request", "equation_extraction"]


class ReviewedFormulaContract(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    category: ConcreteSymbolCategory
    shape: list[int | str] | None = Field(default=None, max_length=16)
    domain: FormulaSymbolDomain = "real"
    constraints: list[str] = Field(default_factory=list, max_length=50)
    scope: str | None = Field(default=None, min_length=1, max_length=500)

    model_config = {"extra": "forbid"}


class ContractReviewCreateRequest(BaseModel):
    workspace_id: str = Field(min_length=1, max_length=200, pattern=r".*\S.*")
    equation_uuid: str = Field(min_length=1, max_length=200)
    symbol_name: str = Field(min_length=1, max_length=200)
    decision: Literal["accepted", "rejected"]
    reviewed_contract: ReviewedFormulaContract | None = None
    evidence: list[str] = Field(default_factory=list, max_length=50)

    model_config = {"extra": "forbid"}

    @field_validator("equation_uuid")
    @classmethod
    def require_equation_uuid(cls, value: str) -> str:
        UUID(value)
        return value

    @field_validator("evidence")
    @classmethod
    def validate_evidence(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value or len(value) > 500 for value in normalized):
            raise ValueError("Review evidence references must be non-empty and bounded.")
        if len(normalized) != len(set(normalized)):
            raise ValueError("Review evidence references must be unique.")
        return normalized

    @model_validator(mode="after")
    def require_accepted_contract(self):
        if self.decision == "accepted" and self.reviewed_contract is None:
            raise ValueError("An accepted review requires the reviewed contract.")
        if (
            self.reviewed_contract is not None
            and self.reviewed_contract.name != self.symbol_name
        ):
            raise ValueError("Reviewed contract must match the reviewed symbol.")
        return self


class ContractReviewResponse(BaseModel):
    review_id: str
    workspace_id: str
    equation_uuid: str
    symbol_name: str
    reviewer_id: str
    reviewer_role: Literal["researcher", "reviewer", "admin"]
    decision: Literal["accepted", "rejected"]
    reviewed_contract: ReviewedFormulaContract | None
    evidence: list[str]
    schema_version: Literal["contract-review.v1"]
    reviewed_at: datetime
    replayed: bool


class FormulaValidationIssue(BaseModel):
    message: str
    location: str
    symbols: list[str]


class FormulaDomainObligationResponse(BaseModel):
    obligation_id: str
    predicate: Literal[
        "nonzero", "zero", "positive", "negative", "non_negative", "non_positive",
    ]
    expression: dict[str, Any]
    expression_hash: str
    location: str
    origin: Literal["inferred"]
    status: Literal[
        "discharged", "conditional", "unresolved", "contradictory", "unsupported",
    ]
    discharged_by: list[str]
    conditions: list[str]


class FormulaDomainAssessmentResponse(BaseModel):
    status: Literal[
        "discharged", "conditional", "unresolved", "contradictory", "unsupported",
    ]
    obligations: list[FormulaDomainObligationResponse]
    contradictions: list[tuple[str, str]]


class FormulaParseResponse(BaseModel):
    ast: dict[str, Any]
    symbols: list[FormulaSymbolResponse]
    free_variables: list[str]
    bound_variables: list[str]
    syntax_hash: str
    syntax_hash_version: str
    canonicalizer_version: str
    # Kept during the FGL-H5 migration window for older clients.
    canonical_hash: str
    extraction_assessment: FormulaExtractionAssessmentResponse
    contracts: list[FormulaContractResponse]
    result_shape: list[int | str] | None
    shape_errors: list[FormulaValidationIssue]
    domain_assessment: FormulaDomainAssessmentResponse
    requires_review: bool


class FormulaCompareRequest(BaseModel):
    formula_a: str = Field(min_length=1, max_length=20_000)
    formula_b: str = Field(min_length=1, max_length=20_000)
    format: Literal["latex", "mathml"] = "latex"


class FormulaCompareResponse(BaseModel):
    structurally_equal: bool
    sympy_equivalent: bool | None
    verification: VerificationVector
    hash_a: str
    hash_b: str
    syntax_hash_version: str
    canonicalizer_version: str


class SymbolicCheckCreateRequest(BaseModel):
    workspace_id: str = Field(min_length=1, max_length=200, pattern=r".*\S.*")
    target_uuid: str = Field(min_length=1, max_length=200)
    formula_a: str = Field(min_length=1, max_length=20_000)
    formula_b: str = Field(min_length=1, max_length=20_000)
    format: Literal["latex", "mathml"] = "latex"
    timeout_ms: int = Field(default=2_000, ge=10, le=30_000)

    model_config = {"extra": "forbid"}
