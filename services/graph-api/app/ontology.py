from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Paper(BaseModel):
    arxiv_id: str = Field(description="Stable arXiv identifier without version")


class PaperVersion(BaseModel):
    version: int | None = Field(description="arXiv revision number")
    source_url: str
    source_sha256: str


class Section(BaseModel):
    title: str
    order: int


class Equation(BaseModel):
    equation_id: str
    latex: str
    anchor: str
    extraction_confidence: float


class Symbol(BaseModel):
    notation: str
    semantic_name: str | None = None
    mathematical_type: str | None = None
    shape: list[str] | None = None


class Assumption(BaseModel):
    statement: str
    scope: str


class Claim(BaseModel):
    statement: str
    scope_paper_id: str
    status: Literal["reported", "supported", "challenged", "retracted"] = "reported"


class Concept(BaseModel):
    description: str


class Hypothesis(BaseModel):
    status: Literal[
        "draft",
        "invalid",
        "well_typed",
        "numerically_plausible",
        "symbolically_verified",
        "human_reviewed",
    ] = "draft"
    generated_by: str


class ProvenanceEdge(BaseModel):
    source_anchor: str
    extraction_confidence: float = Field(ge=0, le=1)


class TypedRelation(BaseModel):
    relation: Literal[
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
        "supersedes",
    ]
    source_anchor: str
    confidence: float = Field(ge=0, le=1)


ENTITY_TYPES = {
    "Paper": Paper,
    "PaperVersion": PaperVersion,
    "Section": Section,
    "Equation": Equation,
    "Symbol": Symbol,
    "Assumption": Assumption,
    "Claim": Claim,
    "Concept": Concept,
    "Hypothesis": Hypothesis,
}

EDGE_TYPES = {
    "ProvenanceEdge": ProvenanceEdge,
    "TypedRelation": TypedRelation,
}
