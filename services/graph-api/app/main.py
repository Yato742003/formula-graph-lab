from __future__ import annotations

import os
from datetime import UTC, datetime

from fastapi import Depends, FastAPI, HTTPException

from app.auth import require_service_token
from app.extractor import PaperExtractionError, extract_paper
from app.fetcher import PaperFetchError, fetch_paper_html
from app.models import ExtractedPaper, HealthResponse, PaperImportRequest
from app.security import UnsafePaperUrl, normalize_arxiv_html_url


app = FastAPI(
    title="FormulaGraph API",
    version="0.1.0",
    docs_url="/docs" if os.getenv("APP_ENV", "development") != "production" else None,
)


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
    try:
        canonical_url = normalize_arxiv_html_url(request.url)
        html_text, final_url = await fetch_paper_html(canonical_url)
    except UnsafePaperUrl as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PaperFetchError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    try:
        return extract_paper(html_text, final_url)
    except PaperExtractionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
