from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl


class PaperImportRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)


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
