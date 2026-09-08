from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import Depends, FastAPI, HTTPException, Request
from neo4j.exceptions import Neo4jError, ServiceUnavailable

from app.auth import require_service_token
from app.enrichment import EnrichmentNeedsReconciliation
from app.evidence import build_evidence_graph
from app.evidence_store import EvidenceSnapshotDataError, Neo4jEvidenceStore
from app.extractor import PaperExtractionError, extract_paper
from app.fetcher import PaperFetchError, fetch_paper_html
from app.formula_ast import FormulaParseError, parse_formula, sympy_equivalent
from app.graph_store import GraphitiResearchStore
from app.models import (
    EvidenceGraphSnapshotRequest,
    EvidenceGraphSnapshotResponse,
    EvidenceImportReceipt,
    EvidenceImportRequest,
    EvidenceImportResponse,
    EvidenceSearchRequest,
    EvidenceSearchResponse,
    ExtractedPaper,
    FormulaCompareRequest,
    FormulaCompareResponse,
    FormulaContractConfirmation,
    FormulaContractResponse,
    FormulaParseRequest,
    FormulaParseResponse,
    FormulaSymbolResponse,
    FormulaValidationIssue,
    HealthResponse,
    PaperImportRequest,
)
from app.search import (
    EvidenceSearchService,
    InvalidSearchCursor,
    SearchCursorCodec,
    SearchDataError,
)
from app.security import UnsafePaperUrl, normalize_arxiv_html_url
from app.symbol_contracts import (
    SymbolContract,
    apply_contract_confirmations,
    check_denominator_domain,
    infer_contracts,
    infer_expression_shape,
)


@asynccontextmanager
async def lifespan(application: FastAPI):
    required = ("NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD")
    values = {key: os.getenv(key) for key in required}
    store = None
    semantic_store = None
    search_service = None
    if all(values.values()):
        store = Neo4jEvidenceStore.connect(
            values["NEO4J_URI"] or "",
            values["NEO4J_USER"] or "",
            values["NEO4J_PASSWORD"] or "",
        )
        await store.initialize()
        if os.getenv("OPENAI_API_KEY", "").strip():
            semantic_store = GraphitiResearchStore.connect(
                values["NEO4J_URI"] or "",
                values["NEO4J_USER"] or "",
                values["NEO4J_PASSWORD"] or "",
            )
            await semantic_store.initialize()
        cursor_secret = os.getenv("SEARCH_CURSOR_SECRET") or os.getenv("SERVICE_TOKEN")
        if cursor_secret:
            try:
                cursor_codec = SearchCursorCodec(cursor_secret)
            except ValueError:
                cursor_codec = None
            if cursor_codec is not None:
                search_service = EvidenceSearchService(
                    store,
                    cursor_codec,
                    semantic_store,
                )
    application.state.evidence_store = store
    application.state.semantic_store = semantic_store
    application.state.search_service = search_service
    try:
        yield
    finally:
        if semantic_store is not None:
            await semantic_store.close()
        if store is not None:
            await store.close()


app = FastAPI(
    title="FormulaGraph API",
    version="0.1.0",
    docs_url="/docs" if os.getenv("APP_ENV", "development") != "production" else None,
    lifespan=lifespan,
)


def require_evidence_store(request: Request) -> Neo4jEvidenceStore:
    store = getattr(request.app.state, "evidence_store", None)
    if store is None:
        raise HTTPException(
            status_code=503,
            detail="Exact evidence storage is not configured.",
        )
    return store


def require_search_service(request: Request) -> EvidenceSearchService:
    service = getattr(request.app.state, "search_service", None)
    if service is None:
        raise HTTPException(
            status_code=503,
            detail="Evidence search is not configured.",
        )
    return service


async def extract_request(url: str) -> ExtractedPaper:
    try:
        canonical_url = normalize_arxiv_html_url(url)
        html_text, final_url = await fetch_paper_html(canonical_url)
    except UnsafePaperUrl as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PaperFetchError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    try:
        return extract_paper(html_text, final_url)
    except PaperExtractionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    graph_configured = bool(
        os.getenv("NEO4J_URI")
        and os.getenv("NEO4J_USER")
        and os.getenv("NEO4J_PASSWORD")
    )
    return HealthResponse(
        status="ok",
        graph_backend="configured" if graph_configured else "not_configured",
        checked_at=datetime.now(UTC),
    )


@app.post("/v1/extractions/preview", response_model=ExtractedPaper)
async def preview_extraction(
    request: PaperImportRequest,
    _: None = Depends(require_service_token),
) -> ExtractedPaper:
    return await extract_request(request.url)


@app.post("/v1/imports", response_model=EvidenceImportResponse)
async def import_evidence(
    body: EvidenceImportRequest,
    http_request: Request,
    _: None = Depends(require_service_token),
    store: Neo4jEvidenceStore = Depends(require_evidence_store),  # noqa: B008
) -> EvidenceImportResponse:
    paper = await extract_request(body.url)
    try:
        graph = build_evidence_graph(paper, workspace_id=body.workspace_id)
        receipt = await store.ingest(graph)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (Neo4jError, ServiceUnavailable, OSError) as exc:
        raise HTTPException(status_code=503, detail="Exact evidence storage failed.") from exc
    semantic_store = getattr(http_request.app.state, "semantic_store", None)
    if semantic_store is not None:
        try:
            await semantic_store.ingest_paper_version(
                workspace_id=body.workspace_id,
                paper=paper,
                receipts=store,
            )
        except EnrichmentNeedsReconciliation as exc:
            raise HTTPException(
                status_code=409,
                detail="Semantic enrichment requires operator reconciliation.",
            ) from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Semantic enrichment failed.") from exc
    return EvidenceImportResponse(
        paper=paper,
        receipt=EvidenceImportReceipt.model_validate(receipt.__dict__),
    )


@app.post("/v1/search", response_model=EvidenceSearchResponse)
async def search_evidence(
    body: EvidenceSearchRequest,
    _: None = Depends(require_service_token),
    service: EvidenceSearchService = Depends(require_search_service),  # noqa: B008
) -> EvidenceSearchResponse:
    try:
        return await service.search(body)
    except InvalidSearchCursor as exc:
        raise HTTPException(status_code=400, detail="Invalid search cursor.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (SearchDataError, Neo4jError, ServiceUnavailable, OSError) as exc:
        raise HTTPException(status_code=503, detail="Evidence search failed.") from exc


@app.post("/v1/graphs/snapshot", response_model=EvidenceGraphSnapshotResponse)
async def graph_snapshot(
    body: EvidenceGraphSnapshotRequest,
    _: None = Depends(require_service_token),
    store: Neo4jEvidenceStore = Depends(require_evidence_store),  # noqa: B008
) -> EvidenceGraphSnapshotResponse:
    try:
        return await store.graph_snapshot(body)
    except (EvidenceSnapshotDataError, Neo4jError, ServiceUnavailable, OSError) as exc:
        raise HTTPException(status_code=503, detail="Evidence graph failed.") from exc


# ── Formula endpoints (FGL-402/403) ──────────────────────────────


@app.post("/v1/formulas/parse", response_model=FormulaParseResponse)
async def formula_parse(
    body: FormulaParseRequest,
    _: None = Depends(require_service_token),
) -> FormulaParseResponse:
    try:
        parsed = parse_formula(body.latex, source_format=body.format)
    except FormulaParseError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": exc.code, "message": str(exc), "position": exc.position},
        ) from exc

    contracts = infer_contracts(
        parsed,
        section_id=body.section_id,
        extraction_confidence=body.extraction_confidence,
    )
    try:
        contracts = apply_contract_confirmations(
            contracts,
            [_confirmed_contract(item, body.section_id) for item in body.confirmations],
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    result_shape, shape_errors = infer_expression_shape(contracts, parsed)
    domain_errors = check_denominator_domain(contracts, parsed)
    return FormulaParseResponse(
        ast=parsed.root.to_dict(),
        symbols=[
            FormulaSymbolResponse(
                name=s.name,
                category=s.category,
                indices=list(s.indices),
                style=s.style,
            )
            for s in parsed.symbols
        ],
        free_variables=list(parsed.free_variables),
        bound_variables=list(parsed.bound_variables),
        canonical_hash=parsed.canonical_hash,
        contracts=[
            FormulaContractResponse(
                name=c.name,
                category=c.category,
                shape=list(c.shape) if c.shape is not None else None,
                domain=c.domain,
                constraints=c.constraints,
                scope=c.scope,
                confidence=c.confidence,
                confirmed=c.confirmed,
            )
            for c in contracts
        ],
        result_shape=list(result_shape) if result_shape is not None else None,
        shape_errors=[
            FormulaValidationIssue(
                message=error.message,
                location=error.node_path,
                symbols=list(error.symbols),
            )
            for error in shape_errors
        ],
        domain_errors=[
            FormulaValidationIssue(
                message=error.message,
                location=error.location,
                symbols=[error.symbol],
            )
            for error in domain_errors
        ],
        requires_confirmation=any(not contract.confirmed for contract in contracts),
    )


def _confirmed_contract(
    confirmation: FormulaContractConfirmation,
    default_scope: str | None,
) -> SymbolContract:
    if confirmation.scope and default_scope and confirmation.scope != default_scope:
        raise ValueError("Contract confirmation scope does not match the formula section.")
    return SymbolContract(
        name=confirmation.name,
        category=confirmation.category,
        shape=tuple(confirmation.shape) if confirmation.shape is not None else None,
        domain=confirmation.domain,
        constraints=confirmation.constraints,
        scope=confirmation.scope or default_scope,
        confidence=1,
        confirmed=True,
    )


@app.post("/v1/formulas/compare", response_model=FormulaCompareResponse)
async def formula_compare(
    body: FormulaCompareRequest,
    _: None = Depends(require_service_token),
) -> FormulaCompareResponse:
    try:
        parsed_a = parse_formula(body.formula_a, source_format=body.format)
        parsed_b = parse_formula(body.formula_b, source_format=body.format)
    except FormulaParseError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": exc.code, "message": str(exc), "position": exc.position},
        ) from exc

    structurally_equal = parsed_a.canonical_hash == parsed_b.canonical_hash
    equiv = sympy_equivalent(parsed_a.root, parsed_b.root)

    return FormulaCompareResponse(
        structurally_equal=structurally_equal,
        sympy_equivalent=equiv,
        hash_a=parsed_a.canonical_hash,
        hash_b=parsed_b.canonical_hash,
    )
