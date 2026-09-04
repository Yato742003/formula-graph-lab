from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl


class PaperImportRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)


class ExtractedEquation(BaseModel):
    equation_id: str
    anchor: str
    latex: str
    equation_number: str | None = None
    section: str | None = None
    preceding_text: str | None = None
    following_text: str | None = None
    extraction_method: Literal["tex_annotation", "alttext", "mathml_text"]
    confidence: float = Field(ge=0, le=1)


class ExtractedPaper(BaseModel):
    paper_id: str
    version: int | None = None
    title: str
    authors: list[str] = Field(default_factory=list)
    source_url: HttpUrl
    source_sha256: str
    equations: list[ExtractedEquation]


class HealthResponse(BaseModel):
    status: Literal["ok"]
    graph_backend: Literal["not_configured", "configured"]
    checked_at: datetime
