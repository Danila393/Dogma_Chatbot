"""FastAPI dependency providers."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.provider import LLMProvider
from app.agent.service import ApartmentAgent
from app.config import Settings
from app.db.repository import ApartmentRepository, SyncRunRepository
from app.services.search import SearchService


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.session_factory() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_search_service(session: SessionDep, settings: SettingsDep) -> SearchService:
    return SearchService(
        ApartmentRepository(session),
        SyncRunRepository(session),
        default_limit=settings.default_search_limit,
        max_limit=settings.max_search_limit,
    )


SearchServiceDep = Annotated[SearchService, Depends(get_search_service)]


def get_agent(request: Request, search: SearchServiceDep) -> ApartmentAgent | None:
    """None when no LLM is configured; the endpoint reports it after request validation."""
    provider: LLMProvider | None = request.app.state.llm_provider
    if provider is None:
        return None
    return ApartmentAgent(provider, search)


AgentDep = Annotated[ApartmentAgent | None, Depends(get_agent)]
