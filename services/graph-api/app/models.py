from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl, field_validator


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
