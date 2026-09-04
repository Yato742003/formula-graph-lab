from __future__ import annotations

import json
from uuid import NAMESPACE_URL, uuid5

from app.models import ExtractedPaper


def paper_episode_uuid(paper: ExtractedPaper) -> str:
    version = paper.version if paper.version is not None else "latest"
    return str(uuid5(NAMESPACE_URL, f"arxiv:{paper.paper_id}:v{version}:{paper.source_sha256}"))


def build_paper_episode(paper: ExtractedPaper) -> str:
    payload = {
        "kind": "paper_version",
        "paper": {
            "arxiv_id": paper.paper_id,
            "version": paper.version,
            "title": paper.title,
            "authors": paper.authors,
            "source_url": str(paper.source_url),
            "source_sha256": paper.source_sha256,
        },
        "equations": [
            {
                "equation_id": equation.equation_id,
                "anchor": equation.anchor,
                "latex": equation.latex,
                "equation_number": equation.equation_number,
                "section": equation.section,
                "extraction_confidence": equation.confidence,
                "evidence": {
                    "preceding_text": equation.preceding_text,
                    "following_text": equation.following_text,
                },
            }
            for equation in paper.equations
        ],
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
