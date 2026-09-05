from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, time
from uuid import NAMESPACE_URL, uuid5

from app.models import ExtractedPaper

EPISODE_SCHEMA_VERSION = 2


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def workspace_group_id(workspace_id: str) -> str:
    if not workspace_id.strip() or len(workspace_id) > 200:
        raise ValueError("A nonempty bounded workspace ID is required.")
    # Graphiti allows ASCII letters/digits/dash/underscore, not colons.
    return "workspace_" + hashlib.sha256(workspace_id.encode()).hexdigest()


def paper_episode_uuid(paper: ExtractedPaper, *, workspace_id: str = "offline") -> str:
    key = [workspace_group_id(workspace_id), paper.paper_id, paper.version,
           paper.source_sha256, EPISODE_SCHEMA_VERSION]
    return str(uuid5(NAMESPACE_URL, _json(key)))


def build_paper_episode(paper: ExtractedPaper) -> str:
    """Lossless extraction export; ordered ingestion uses build_paper_episodes."""
    return _json({
        "schema_version": EPISODE_SCHEMA_VERSION,
        "kind": "paper_version",
        "paper": paper.model_dump(mode="json", exclude={"equations", "sections"}),
        "sections": [s.model_dump(mode="json") for s in paper.sections],
        "equations": [e.model_dump(mode="json") for e in paper.equations],
    })


@dataclass(frozen=True)
class ResearchEpisode:
    uuid: str
    name: str
    kind: str
    body: str
    group_id: str
    saga: str
    reference_time: datetime
    previous_uuid: str | None


def build_paper_episodes(
    paper: ExtractedPaper,
    *,
    workspace_id: str,
    reference_time: datetime | None = None,
) -> list[ResearchEpisode]:
    if reference_time is None:
        if paper.version_published_at is None:
            raise ValueError("A source reference time is required when the version date is absent.")
        reference_time = datetime.combine(paper.version_published_at, time.min, tzinfo=UTC)
    if reference_time.tzinfo is None or reference_time.utcoffset() is None:
        raise ValueError("The source reference time must be timezone-aware.")
    if paper.version_published_at is not None:
        if reference_time.astimezone(UTC).date() != paper.version_published_at:
            raise ValueError("The reference time must match the source version publication date.")
    group_id = workspace_group_id(workspace_id)
    root_uuid = paper_episode_uuid(paper, workspace_id=workspace_id)
    version_key = paper.version if paper.version is not None else paper.source_sha256
    saga = f"arxiv_{paper.paper_id.replace('.', '_')}_v{version_key}"
    metadata = paper.model_dump(mode="json", exclude={"equations", "sections"})
    sections = sorted(paper.sections, key=lambda item: item.order)
    if len({s.order for s in sections}) != len(sections):
        raise ValueError("Section order must be unique.")
    section_ids = {s.section_id for s in sections}
    if len(section_ids) != len(sections):
        raise ValueError("Section IDs must be unique.")
    equation_map = {e.equation_id: e for e in paper.equations}
    if len(equation_map) != len(paper.equations):
        raise ValueError("Equation IDs must be unique.")
    for equation in paper.equations:
        if equation.section_id and equation.section_id not in section_ids:
            raise ValueError("An equation references an unknown section.")
    for section in sections:
        expected = [e.equation_id for e in paper.equations if e.section_id == section.section_id]
        if expected != section.equation_ids:
            raise ValueError("Section/equation provenance mapping is inconsistent.")
    base = {"schema_version": EPISODE_SCHEMA_VERSION, "paper": metadata}
    bodies = [("paper_metadata", {
        **base, "kind": "paper_metadata",
        "section_order": [s.section_id for s in sections],
        "unsectioned_equations": [
            e.model_dump(mode="json") for e in paper.equations if e.section_id is None
        ],
    })]
    for section in sections:
        bodies.append(("section", {
            **base, "kind": "section", "section": section.model_dump(mode="json"),
            "equations": [equation_map[eid].model_dump(mode="json") for eid in section.equation_ids],
        }))
    episodes = []
    previous_uuid = None
    for index, (kind, body) in enumerate(bodies):
        episode_uuid = root_uuid if index == 0 else str(uuid5(
            NAMESPACE_URL, _json([root_uuid, sections[index - 1].section_id])
        ))
        episodes.append(ResearchEpisode(
            uuid=episode_uuid, name=f"{saga}_{index:04d}", kind=kind,
            body=_json(body), group_id=group_id, saga=saga,
            reference_time=reference_time.astimezone(UTC), previous_uuid=previous_uuid,
        ))
        previous_uuid = episode_uuid
    return episodes
