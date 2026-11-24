"""LLM provider abstraction and the OpenAI-compatible implementation."""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import openai

from app.config import Settings
from app.exceptions import LLMRateLimitedError, LLMUnavailableError

logger = logging.getLogger(__name__)

Message = dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True, slots=True)
class LLMToolCall:
    id: str
    name: str
    arguments: str


@dataclass(frozen=True, slots=True)
class LLMReply:
    content: str | None
    tool_calls: list[LLMToolCall] = field(default_factory=list)


class LLMProvider(Protocol):
    async def complete(self, messages: list[Message], tools: list[ToolSpec]) -> LLMReply:
        """Return the model reply. Raises LLMUnavailableError / LLMRateLimitedError."""
        ...


_TRANSIENT_ERRORS: tuple[type[Exception], ...] = (
    openai.APITimeoutError,
    openai.APIConnectionError,
    openai.InternalServerError,
)


class OpenAICompatibleProvider:
    """Works with any endpoint implementing the Chat Completions tool-calling protocol."""

    def __init__(
        self,
        settings: Settings,
        client: openai.AsyncOpenAI | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._model = settings.llm_model
        self._temperature = settings.llm_temperature
        self._max_retries = settings.llm_max_retries
        self._sleep = sleep
        self._client = client or openai.AsyncOpenAI(
            api_key=settings.llm_api_key.get_secret_value() or "not-required",
            base_url=settings.llm_base_url or None,
            timeout=settings.llm_timeout,
            max_retries=0,  # retries are handled here, with logging and a hard cap
        )

    async def complete(self, messages: list[Message], tools: list[ToolSpec]) -> LLMReply:
        tool_payload: list[dict[str, Any]] = [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in tools
        ]
        request: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "tools": tool_payload,
            "tool_choice": "required",
            "temperature": self._temperature,
        }
        for attempt in range(1, self._max_retries + 2):
            try:
                response = await self._client.chat.completions.create(**request)
            except openai.RateLimitError as exc:
                if attempt > self._max_retries:
                    raise LLMRateLimitedError() from exc
                await self._backoff(attempt, "rate_limited")
            except _TRANSIENT_ERRORS as exc:
                if attempt > self._max_retries:
                    raise LLMUnavailableError() from exc
                await self._backoff(attempt, type(exc).__name__)
            except openai.APIStatusError as exc:
                logger.error(
                    "LLM request rejected",
                    extra={"status_code": exc.status_code, "error_type": type(exc).__name__},
                )
                raise LLMUnavailableError(
                    "LLM-провайдер отклонил запрос. "
                    "Проверьте LLM_MODEL, LLM_API_KEY и LLM_BASE_URL."
                ) from exc
            else:
                return self._parse(response)
        raise LLMUnavailableError()  # pragma: no cover - loop always returns or raises

    async def _backoff(self, attempt: int, reason: str) -> None:
        delay = min(2 ** (attempt - 1), 8) + random.uniform(0, 0.5)
        logger.warning("LLM call failed, retrying", extra={"attempt": attempt, "reason": reason})
        await self._sleep(delay)

    @staticmethod
    def _parse(response: Any) -> LLMReply:
        if not getattr(response, "choices", None):
            return LLMReply(content=None)
        message = response.choices[0].message
        calls = [
            LLMToolCall(
                id=call.id, name=call.function.name, arguments=call.function.arguments or ""
            )
            for call in (message.tool_calls or [])
            if getattr(call, "function", None) is not None
        ]
        return LLMReply(content=message.content, tool_calls=calls)
