from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from neo4j.exceptions import Neo4jError, ServiceUnavailable
from starlette.concurrency import run_in_threadpool

from app.auth import ServiceActor, require_human_service_actor, require_service_token
from app.enrichment import EnrichmentNeedsReconciliation
from app.evidence import build_evidence_graph
from app.evidence_store import EvidenceSnapshotDataError, Neo4jEvidenceStore
from app.extractor import PaperExtractionError, extract_paper
from app.fetcher import PaperFetchError, fetch_paper_html
from app.formula_ast import FormulaParseError, parse_formula
from app.graph_store import GraphitiResearchStore
from app.models import (
    ContractReviewCreateRequest,
    ContractReviewResponse,
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
    FormulaContractResponse,
    FormulaDomainAssessmentResponse,
    FormulaExtractionAssessmentResponse,
    FormulaParseRequest,
    FormulaParseResponse,
    FormulaSymbolResponse,
    FormulaValidationIssue,
    HealthResponse,
    PaperImportRequest,
    ReviewedFormulaContract,
    SymbolicCheckCreateRequest,
)
from app.search import (
    EvidenceSearchService,
    InvalidSearchCursor,
    SearchCursorCodec,
    SearchDataError,
)
from app.security import UnsafePaperUrl, normalize_arxiv_html_url
from app.symbol_contracts import (
    ContractReview,
    ReviewedContractValue,
    assess_domain_obligations,
    infer_contracts,
    infer_domain_obligations,
    infer_expression_shape,
)
from app.verification import CheckResultResponse, run_symbolic_check, symbolic_request_hash
from app.worker_auth import WorkerPrincipal, require_research_checks_enabled, require_worker


@asynccontextmanager
async def lifespan(application: FastAPI):
    required = ("NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD")
    values = {key: os.getenv(key) for key in required}
    store = None
    semantic_store = None
    search_service = None
    uri = (values.get("NEO4J_URI") or "").strip().lower()
    if all(values.values()) and uri not in ("", "none", "offline", "disabled", "false"):
        try:
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
        except (ServiceUnavailable, Neo4jError, OSError):
            store = None
            semantic_store = None
            search_service = None
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
    uri = (os.getenv("NEO4J_URI") or "").strip().lower()
    graph_configured = bool(
        uri
        and uri not in ("", "none", "offline", "disabled", "false")
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


@app.post("/v1/contract-reviews", response_model=ContractReviewResponse)
async def append_contract_review(
    body: ContractReviewCreateRequest,
    idempotency_key: str = Header(  # noqa: B008
        min_length=16,
        max_length=200,
        alias="Idempotency-Key",
    ),
    actor: ServiceActor = Depends(require_human_service_actor),  # noqa: B008
    store: Neo4jEvidenceStore = Depends(require_evidence_store),  # noqa: B008
) -> ContractReviewResponse:
    if not idempotency_key.isascii() or not idempotency_key.strip():
        raise HTTPException(status_code=422, detail="Invalid idempotency key.")
    reviewed_value = None
    if body.reviewed_contract is not None:
        reviewed_value = ReviewedContractValue(
            name=body.reviewed_contract.name,
            category=body.reviewed_contract.category,
            shape=(
                tuple(body.reviewed_contract.shape)
                if body.reviewed_contract.shape is not None
                else None
            ),
            domain=body.reviewed_contract.domain,
            constraints=body.reviewed_contract.constraints,
            scope=body.reviewed_contract.scope,
        )
    review = ContractReview(
        review_id="pending-server-identity",
        symbol_name=body.symbol_name,
        reviewer_id=actor.actor_id,
        reviewer_role=actor.role,
        decision=body.decision,
        scope=body.equation_uuid,
        reviewed_contract=reviewed_value,
        evidence=body.evidence,
        reviewed_at=datetime.now(UTC),
    )
    try:
        receipt = await store.append_contract_review(
            workspace_id=body.workspace_id,
            equation_uuid=body.equation_uuid,
            idempotency_key=idempotency_key,
            review=review,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (Neo4jError, ServiceUnavailable, OSError) as exc:
        raise HTTPException(status_code=503, detail="Contract review storage failed.") from exc
    persisted = receipt.review
    return ContractReviewResponse(
        review_id=persisted.review_id,
        workspace_id=body.workspace_id,
        equation_uuid=body.equation_uuid,
        symbol_name=persisted.symbol_name,
        reviewer_id=persisted.reviewer_id,
        reviewer_role=persisted.reviewer_role,
        decision=persisted.decision,
        reviewed_contract=(
            ReviewedFormulaContract.model_validate(
                persisted.reviewed_contract.model_dump(mode="json")
            )
            if persisted.reviewed_contract is not None
            else None
        ),
        evidence=persisted.evidence,
        schema_version=persisted.schema_version,
        reviewed_at=persisted.reviewed_at,
        replayed=receipt.replayed,
    )


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
    result_shape, shape_errors = infer_expression_shape(contracts, parsed)
    domain_assessment = assess_domain_obligations(
        infer_domain_obligations(contracts, parsed)
    )
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
        syntax_hash=parsed.syntax_hash,
        syntax_hash_version=parsed.syntax_hash_version,
        canonicalizer_version=parsed.canonicalizer_version,
        canonical_hash=parsed.canonical_hash,
        extraction_assessment=FormulaExtractionAssessmentResponse(
            confidence=body.extraction_confidence,
            source="request",
        ),
        contracts=[
            FormulaContractResponse(
                name=c.name,
                category=c.category,
                shape=list(c.shape) if c.shape is not None else None,
                domain=c.domain,
                constraints=c.constraints,
                scope=c.scope,
                inference_confidence=c.inference_confidence,
                review_required=c.review_required,
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
        domain_assessment=FormulaDomainAssessmentResponse.model_validate(
            domain_assessment.model_dump(mode="json")
        ),
        requires_review=any(contract.review_required for contract in contracts),
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
    check = await run_in_threadpool(
        run_symbolic_check, body.formula_a, body.formula_b, source_format=body.format,
    )
    equiv = True if check.outcome == "supported" else False if check.outcome == "refuted" else None

    return FormulaCompareResponse(
        structurally_equal=structurally_equal,
        sympy_equivalent=equiv,
        verification=check.vector,
        hash_a=parsed_a.canonical_hash,
        hash_b=parsed_b.canonical_hash,
        syntax_hash_version=parsed_a.syntax_hash_version,
        canonicalizer_version=parsed_a.canonicalizer_version,
    )


@app.post(
    "/v1/checks/symbolic", response_model=CheckResultResponse,
    dependencies=[Depends(require_research_checks_enabled)],
)
async def create_symbolic_check(
    body: SymbolicCheckCreateRequest,
    idempotency_key: str = Header(  # noqa: B008
        min_length=16,
        max_length=200,
        alias="Idempotency-Key",
    ),
    actor: WorkerPrincipal = Depends(require_worker),  # noqa: B008
    store: Neo4jEvidenceStore = Depends(require_evidence_store),  # noqa: B008
) -> CheckResultResponse:
    if not idempotency_key.isascii() or not idempotency_key.strip():
        raise HTTPException(status_code=422, detail="Invalid idempotency key.")
    actor.require("checks:run", body.workspace_id)
    try:
        source = await store.equation_source(
            workspace_id=body.workspace_id, equation_uuid=body.target_uuid,
        )
        if body.format != "latex" or source.get("latex") != body.formula_a:
            raise ValueError("Check input does not match the target equation source.")
        request_hash = symbolic_request_hash(
            body.formula_a, body.formula_b, source_format=body.format,
            timeout_ms=body.timeout_ms,
        )
        existing = await store.lookup_check_result(
            workspace_id=body.workspace_id, target_uuid=body.target_uuid,
            idempotency_key=idempotency_key, request_hash=request_hash,
        )
        if existing is not None:
            return CheckResultResponse(**existing.result.model_dump(), replayed=True)
        result = await run_in_threadpool(
            run_symbolic_check, body.formula_a, body.formula_b,
            source_format=body.format, timeout_ms=body.timeout_ms,
        )
        receipt = await store.append_check_result(
            workspace_id=body.workspace_id,
            target_uuid=body.target_uuid,
            idempotency_key=idempotency_key,
            result=result,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (Neo4jError, ServiceUnavailable, OSError) as exc:
        raise HTTPException(status_code=503, detail="Check result storage failed.") from exc
    return CheckResultResponse(
        **receipt.result.model_dump(mode="python"),
        replayed=receipt.replayed,
    )
