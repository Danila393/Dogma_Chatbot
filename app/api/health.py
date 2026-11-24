"""Liveness and readiness probes."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text

from app.dependencies import SearchServiceDep, SessionDep

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str


class ReadyResponse(BaseModel):
    status: str
    database: str
    llm_configured: bool
    available_apartments: int


@router.get("/health", response_model=HealthResponse, summary="Процесс жив")
async def health() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get(
    "/ready",
    response_model=ReadyResponse,
    summary="Готовность: база доступна",
    responses={503: {"description": "База данных недоступна"}},
)
async def ready(
    request: Request, session: SessionDep, search: SearchServiceDep
) -> ReadyResponse | JSONResponse:
    try:
        await session.execute(text("SELECT 1"))
        freshness = await search.freshness()
    except Exception:
        logger.exception("readiness check failed")
        body: dict[str, Any] = {"error": {"code": "not_ready", "message": "База данных недоступна"}}
        return JSONResponse(status_code=503, content=body)
    return ReadyResponse(
        status="ready",
        database="ok",
        llm_configured=request.app.state.llm_provider is not None,
        available_apartments=freshness.available_apartments,
    )
