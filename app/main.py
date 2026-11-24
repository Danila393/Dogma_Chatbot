"""FastAPI application factory."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.agent.provider import LLMProvider, OpenAICompatibleProvider
from app.api.router import api_router
from app.config import Settings, get_settings
from app.db.session import create_engine, create_session_factory, init_db
from app.exceptions import AppError
from app.logging_config import configure_logging

logger = logging.getLogger(__name__)

DESCRIPTION = """
Интеллектуальный поиск квартир застройщика DOGMA.

* **POST /api/v1/chat** — запрос на естественном языке (AI-агент).
* **POST /api/v1/apartments/search** — структурированный поиск без LLM.
* **GET /api/v1/complexes** — список ЖК в базе.

Данные берутся из локальной SQLite-базы, которую наполняет `python -m app.cli sync`.
"""


def create_app(settings: Settings | None = None, provider: LLMProvider | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings)
        await init_db(engine)
        app.state.settings = settings
        app.state.engine = engine
        app.state.session_factory = create_session_factory(engine)
        app.state.llm_provider = provider or (
            OpenAICompatibleProvider(settings) if settings.llm_configured else None
        )
        logger.info(
            "application started",
            extra={"env": settings.app_env, "llm_configured": app.state.llm_provider is not None},
        )
        try:
            yield
        finally:
            await engine.dispose()
            logger.info("application stopped")

    app = FastAPI(
        title="DOGMA Apartment AI Agent",
        version="1.0.0",
        description=DESCRIPTION,
        lifespan=lifespan,
    )
    app.include_router(api_router)
    _register_error_handlers(app)
    return app


def _error(status_code: int, code: str, message: str, details: Any = None) -> JSONResponse:
    body: dict[str, Any] = {"error": {"code": code, "message": message}}
    if details is not None:
        body["error"]["details"] = details
    return JSONResponse(status_code=status_code, content=body)


def _register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
        log = logger.warning if exc.status_code < 500 else logger.error
        log("request failed", extra={"code": exc.code, "status_code": exc.status_code})
        return _error(exc.status_code, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {
                "field": ".".join(str(part) for part in error["loc"] if part != "body"),
                "message": error["msg"],
            }
            for error in exc.errors()
        ]
        return _error(422, "validation_error", "Некорректные параметры запроса", details)

    @app.exception_handler(HTTPException)
    async def http_error_handler(_: Request, exc: HTTPException) -> JSONResponse:
        return _error(exc.status_code, "http_error", str(exc.detail))

    @app.exception_handler(Exception)
    async def unexpected_error_handler(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error", exc_info=exc)
        return _error(500, "internal_error", "Внутренняя ошибка сервиса")


app = create_app()
