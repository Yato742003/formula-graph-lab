from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from app.episodes import workspace_group_id
from app.models import (
    EvidenceSearchHit,
    EvidenceSearchRequest,
    EvidenceSearchResponse,
    SearchScoreComponents,
)

_LOGGER = logging.getLogger(__name__)
_TOKEN = re.compile(r"[^\W\d_][\w]{0,63}|\d+(?:\.\d+)?", re.UNICODE)
_CURSOR_VERSION = 1
_RRF_K = 60
_RRF_SCALE = 1_000_000
MAX_SEARCH_CANDIDATES = 250


class InvalidSearchCursor(ValueError):
    """The cursor is malformed, forged, or belongs to another query."""


class SearchDataError(RuntimeError):
    """Persisted exact evidence is not structurally valid."""


@dataclass(frozen=True)
class EvidenceSearchFilters:
    group_id: str
    paper_id: str | None = None
    version: int | None = None
    entity_types: tuple[str, ...] = ()
    verification_statuses: tuple[str, ...] = ()
    as_of: datetime | None = None


@dataclass(frozen=True)
class EvidenceCandidate:
    uuid: str
    kind: str
    logical_id: str
    payload: str
    paper_id: str
    paper_version: int | None
    valid_at: datetime | None
    verification_status: str
    episode_uuids: tuple[str, ...]
    lexical_score: float | None = None
    graph_distance: int | None = None


class EvidenceSearchRepository(Protocol):
    async def lexical_search(
        self,
        query: str,
        filters: EvidenceSearchFilters,
        limit: int,
    ) -> list[EvidenceCandidate]: ...

    async def evidence_for_episodes(
        self,
        episode_uuids: list[str],
        filters: EvidenceSearchFilters,
        limit: int,
    ) -> list[EvidenceCandidate]: ...

    async def graph_neighbors(
        self,
        center_node_uuid: str,
        filters: EvidenceSearchFilters,
        limit: int,
    ) -> list[EvidenceCandidate]: ...


class SemanticSearchProvider(Protocol):
    async def search_episode_uuids(
        self,
        query: str,
        group_id: str,
        limit: int,
    ) -> list[str]: ...


@dataclass(frozen=True)
class _CursorPosition:
    score: int
    uuid: str


class SearchCursorCodec:
    def __init__(self, secret: str) -> None:
        encoded = secret.encode("utf-8")
        if len(encoded) < 32:
            raise ValueError("Search cursor secret must contain at least 32 UTF-8 bytes.")
        self._secret = encoded

    def encode(self, fingerprint: str, score: int, uuid: str) -> str:
        payload = json.dumps(
            {"v": _CURSOR_VERSION, "f": fingerprint, "s": score, "u": uuid},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        body = _base64_encode(payload)
        signature = _base64_encode(hmac.digest(self._secret, body.encode("ascii"), "sha256"))
        return f"{body}.{signature}"

    def decode(self, cursor: str, fingerprint: str) -> _CursorPosition:
        try:
            body, signature = cursor.split(".", 1)
            expected = hmac.digest(self._secret, body.encode("ascii"), "sha256")
            if not hmac.compare_digest(_base64_decode(signature), expected):
                raise InvalidSearchCursor("Search cursor signature is invalid.")
            value = json.loads(_base64_decode(body))
            if not isinstance(value, dict) or set(value) != {"v", "f", "s", "u"}:
                raise InvalidSearchCursor("Search cursor payload is invalid.")
            if value["v"] != _CURSOR_VERSION or value["f"] != fingerprint:
                raise InvalidSearchCursor("Search cursor does not belong to this query.")
            if (
                not isinstance(value["s"], int)
                or value["s"] < 0
                or not isinstance(value["u"], str)
                or not value["u"]
                or len(value["u"]) > 200
            ):
                raise InvalidSearchCursor("Search cursor position is invalid.")
            return _CursorPosition(score=value["s"], uuid=value["u"])
        except InvalidSearchCursor:
            raise
        except (UnicodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise InvalidSearchCursor("Search cursor is malformed.") from exc


class EvidenceSearchService:
    def __init__(
        self,
        repository: EvidenceSearchRepository,
        cursor_codec: SearchCursorCodec,
        semantic_provider: SemanticSearchProvider | None = None,
    ) -> None:
        self._repository = repository
        self._cursor_codec = cursor_codec
        self._semantic_provider = semantic_provider

    async def search(self, request: EvidenceSearchRequest) -> EvidenceSearchResponse:
        query = " ".join(request.query.split())
        as_of = request.as_of.astimezone(UTC) if request.as_of else None
        filters = EvidenceSearchFilters(
            group_id=workspace_group_id(request.workspace_id),
            paper_id=request.paper_id,
            version=request.version,
            entity_types=tuple(request.entity_types),
            verification_statuses=tuple(request.verification_statuses),
            as_of=as_of,
        )
        fingerprint = _request_fingerprint(query, request, filters)
        position = (
            self._cursor_codec.decode(request.cursor, fingerprint)
            if request.cursor
            else None
        )

        lexical_task = self._repository.lexical_search(
            query,
            filters,
            MAX_SEARCH_CANDIDATES,
        )
        semantic_task = self._semantic_episode_search(query, filters.group_id)
        if request.center_node_uuid:
            graph_task = self._repository.graph_neighbors(
                request.center_node_uuid,
                filters,
                MAX_SEARCH_CANDIDATES,
            )
        else:
            graph_task = _empty_candidates()
        lexical, semantic_result, neighbors = await asyncio.gather(
            lexical_task,
            semantic_task,
            graph_task,
        )
        semantic_episode_uuids, semantic_available = semantic_result
        semantic_candidates = (
            await self._repository.evidence_for_episodes(
                semantic_episode_uuids,
                filters,
                MAX_SEARCH_CANDIDATES,
            )
            if semantic_episode_uuids
            else []
        )

        ranked = _rank_candidates(
            lexical,
            semantic_candidates,
            semantic_episode_uuids,
            neighbors,
        )
        if position:
            ranked = [
                item
                for item in ranked
                if item.score < position.score
                or (item.score == position.score and item.uuid > position.uuid)
            ]
        page = ranked[: request.limit]
        next_cursor = None
        if len(ranked) > request.limit and page:
            last = page[-1]
            next_cursor = self._cursor_codec.encode(
                fingerprint,
                last.score,
                last.uuid,
            )
        return EvidenceSearchResponse(
            hits=page,
            next_cursor=next_cursor,
            semantic_available=semantic_available,
        )

    async def _semantic_episode_search(
        self,
        query: str,
        group_id: str,
    ) -> tuple[list[str], bool]:
        if self._semantic_provider is None:
            return [], False
        try:
            episode_uuids = await self._semantic_provider.search_episode_uuids(
                query,
                group_id,
                MAX_SEARCH_CANDIDATES,
            )
            return list(dict.fromkeys(episode_uuids))[:MAX_SEARCH_CANDIDATES], True
        except Exception as exc:
            _LOGGER.warning(
                "Semantic search unavailable (%s).",
                type(exc).__name__,
            )
            return [], False


async def _empty_candidates() -> list[EvidenceCandidate]:
    return []


def build_lucene_query(group_id: str, query: str) -> str:
    tokens = []
    for token in _TOKEN.findall(query):
        normalized = token.casefold()
        if normalized not in tokens:
            tokens.append(normalized)
        if len(tokens) == 32:
            break
    if not tokens:
        raise ValueError("Search query must contain a word, symbol name, or number.")
    terms = " OR ".join(f'"{token}"' for token in tokens)
    return f'group_id:"{group_id}" AND ({terms})'


def _rank_candidates(
    lexical: list[EvidenceCandidate],
    semantic: list[EvidenceCandidate],
    semantic_episode_uuids: list[str],
    neighbors: list[EvidenceCandidate],
) -> list[EvidenceSearchHit]:
    lexical_rank = {item.uuid: index for index, item in enumerate(lexical, 1)}
    episode_rank = {uuid: index for index, uuid in enumerate(semantic_episode_uuids, 1)}
    semantic_rank = {
        item.uuid: min(episode_rank[uuid] for uuid in item.episode_uuids if uuid in episode_rank)
        for item in semantic
        if any(uuid in episode_rank for uuid in item.episode_uuids)
    }
    graph_rank = {item.uuid: index for index, item in enumerate(neighbors, 1)}
    graph_distance = {item.uuid: item.graph_distance for item in neighbors}
    candidates: dict[str, EvidenceCandidate] = {}
    for item in [*lexical, *semantic, *neighbors]:
        existing = candidates.setdefault(item.uuid, item)
        if _candidate_identity(existing) != _candidate_identity(item):
            raise SearchDataError("Conflicting evidence candidate identity.")

    hits = []
    for uuid, item in candidates.items():
        lexical_position = lexical_rank.get(uuid)
        semantic_position = semantic_rank.get(uuid)
        graph_position = graph_rank.get(uuid)
        score = sum(
            _RRF_SCALE // (_RRF_K + rank)
            for rank in (lexical_position, semantic_position, graph_position)
            if rank is not None
        )
        payload = _payload_object(item.payload)
        match_sources = [
            source
            for source, rank in (
                ("lexical", lexical_position),
                ("semantic", semantic_position),
                ("graph", graph_position),
            )
            if rank is not None
        ]
        hits.append(
            EvidenceSearchHit(
                uuid=item.uuid,
                kind=item.kind,
                logical_id=item.logical_id,
                paper_id=item.paper_id,
                version=item.paper_version,
                valid_at=item.valid_at,
                verification_status=item.verification_status,
                payload=payload,
                episode_uuids=list(item.episode_uuids),
                score=score,
                match_sources=match_sources,
                score_components=SearchScoreComponents(
                    lexical_rank=lexical_position,
                    semantic_rank=semantic_position,
                    graph_rank=graph_position,
                    graph_distance=graph_distance.get(uuid),
                ),
            )
        )
    return sorted(hits, key=lambda item: (-item.score, item.uuid))


def _request_fingerprint(
    query: str,
    request: EvidenceSearchRequest,
    filters: EvidenceSearchFilters,
) -> str:
    value = {
        "query": query,
        "group_id": filters.group_id,
        "paper_id": request.paper_id,
        "version": request.version,
        "entity_types": sorted(request.entity_types),
        "verification_statuses": sorted(request.verification_statuses),
        "as_of": filters.as_of.isoformat() if filters.as_of else None,
        "center_node_uuid": request.center_node_uuid,
    }
    encoded = json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _candidate_identity(item: EvidenceCandidate) -> tuple[object, ...]:
    return (
        item.kind,
        item.logical_id,
        item.payload,
        item.paper_id,
        item.paper_version,
        item.verification_status,
    )


def _payload_object(value: str) -> dict[str, object]:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise SearchDataError("Evidence payload is not valid JSON.") from exc
    if not isinstance(payload, dict):
        raise SearchDataError("Evidence payload must be a JSON object.")
    return payload


def _base64_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _base64_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.b64decode(value + padding, altchars=b"-_", validate=True)
