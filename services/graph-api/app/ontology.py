from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, create_model, model_validator


class OntologyModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Paper(OntologyModel):
    arxiv_id: str = Field(description="Stable arXiv identifier without version")


class PaperVersion(OntologyModel):
    version: int | None = Field(description="arXiv revision number")
    source_url: str
    source_sha256: str


class Section(OntologyModel):
    title: str
    order: int


class Equation(OntologyModel):
    equation_id: str
    latex: str
    anchor: str
    extraction_confidence: float


class Symbol(OntologyModel):
    notation: str
    semantic_name: str | None = None
    mathematical_type: str | None = None
    shape: list[str] | None = None


class Assumption(OntologyModel):
    statement: str
    scope: str


class Claim(OntologyModel):
    statement: str
    scope_paper_id: str
    status: Literal["reported", "supported", "challenged", "retracted"] = "reported"


class Concept(OntologyModel):
    description: str


class Method(OntologyModel):
    description: str
    scope_paper_id: str


class Experiment(OntologyModel):
    description: str
    scope_paper_id: str
    reported_result: str | None = None


class Hypothesis(OntologyModel):
    status: Literal[
        "draft",
        "invalid",
        "well_typed",
        "numerically_plausible",
        "symbolically_verified",
        "human_reviewed",
    ] = "draft"
    generated_by: str


class ProvenanceEdge(OntologyModel):
    source_anchor: str
    extraction_confidence: float = Field(ge=0, le=1)


class TypedRelation(OntologyModel):
    relation: Literal[
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
        "supersedes",
    ]
    source_anchor: str
    confidence: float = Field(ge=0, le=1)
    conditions: list[str] = Field(default_factory=list)


ALLOWED_RELATION_PAIRS = {
    "has_version": {("Paper", "PaperVersion")},
    "contains": {("PaperVersion", "Section"), ("Section", "Section"),
                 ("Section", "Equation"), ("PaperVersion", "Equation"),
                 ("PaperVersion", "Method"), ("PaperVersion", "Experiment")},
    "defines": {("Equation", "Symbol"), ("Section", "Symbol"), ("Method", "Concept")},
    "uses": {("Equation", "Symbol"), ("Method", "Equation"), ("Experiment", "Method")},
    "assumes": {("Equation", "Assumption"), ("Method", "Assumption")},
    "cites": {("PaperVersion", "Paper")},
    "makes_claim": {("PaperVersion", "Claim"), ("Experiment", "Claim")},
    "about": {("Claim", "Concept"), ("Claim", "Equation"), ("Claim", "Method")},
    "derived_from": {("Equation", "Equation")},
    "approximates": {("Equation", "Equation")},
    "generalizes": {("Equation", "Equation"), ("Method", "Method")},
    "equivalent_under": {("Equation", "Equation")},
    "supersedes": {("PaperVersion", "PaperVersion")},
}


class RelationContract(TypedRelation):
    source_type: str
    target_type: str
    conditions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_endpoints(self) -> "RelationContract":
        if (self.source_type, self.target_type) not in ALLOWED_RELATION_PAIRS[self.relation]:
            raise ValueError("The relation does not allow this source/target entity pair.")
        if self.relation == "equivalent_under" and not self.conditions:
            raise ValueError("Equivalence requires explicit mathematical conditions.")
        return self


ENTITY_TYPES = {
    "Paper": Paper,
    "PaperVersion": PaperVersion,
    "Section": Section,
    "Equation": Equation,
    "Symbol": Symbol,
    "Assumption": Assumption,
    "Claim": Claim,
    "Concept": Concept,
    "Method": Method,
    "Experiment": Experiment,
    "Hypothesis": Hypothesis,
}

EDGE_TYPES: dict[str, type[BaseModel]] = {}
EDGE_TYPE_MAP: dict[tuple[str, str], list[str]] = {}
for relation_name, allowed_pairs in ALLOWED_RELATION_PAIRS.items():
    model_name = "".join(part.title() for part in relation_name.split("_")) + "Relation"
    extra_fields = (
        {"conditions": (list[str], Field(min_length=1))}
        if relation_name == "equivalent_under" else {}
    )
    EDGE_TYPES[model_name] = create_model(
        model_name, __base__=TypedRelation,
        relation=(Literal[relation_name], relation_name),
        **extra_fields,
    )
    for pair in allowed_pairs:
        EDGE_TYPE_MAP.setdefault(pair, []).append(model_name)
