"""AI agent endpoint."""

from __future__ import annotations

from fastapi import APIRouter

from app.dependencies import AgentDep
from app.exceptions import LLMNotConfiguredError
from app.schemas.chat import ChatRequest, ChatResponse

router = APIRouter(tags=["chat"])


@router.post(
    "/chat",
    response_model=ChatResponse,
    summary="Подбор квартир по запросу на естественном языке",
    responses={
        422: {"description": "Пустое или слишком длинное сообщение"},
        502: {"description": "LLM вернула некорректный ответ"},
        503: {"description": "LLM не настроен, недоступен или превышен лимит запросов"},
    },
)
async def chat(request: ChatRequest, agent: AgentDep) -> ChatResponse:
    if agent is None:
        raise LLMNotConfiguredError()
    return await agent.handle(request.message)
