"""Natural-language apartment agent: LLM parses the request, the backend does everything else."""

from __future__ import annotations

import json
import logging
from decimal import Decimal

from pydantic import ValidationError

from app.agent.hints import QueryHints, extract_hints
from app.agent.prompts import build_system_prompt
from app.agent.provider import LLMProvider, LLMReply, LLMToolCall, Message
from app.agent.tools import (
    REPLY_TOOL,
    SEARCH_TOOL,
    TOOLS,
    ReplyToolArguments,
    SearchToolArguments,
)
from app.exceptions import FilterValueNotResolvedError, LLMInvalidResponseError
from app.schemas.chat import ChatResponse, ChatStatus
from app.schemas.search import ApartmentSearchFilters, RangeError
from app.services.search import SearchService

logger = logging.getLogger(__name__)

MAX_REPAIR_ATTEMPTS = 1

OFF_TOPIC_MESSAGE = (
    "Я помогаю подобрать квартиру в проектах DOGMA. Опишите, что ищете: ЖК, бюджет, "
    "площадь, количество комнат. Например: «студия в ЖК Самолёт до 7 млн от 30 м²»."
)


class _InvalidToolCallError(Exception):
    """The model's tool call cannot be used; the reason is fed back to the model once."""


class _ContradictionError(Exception):
    """The user's own constraints are mutually exclusive (e.g. min price > max price)."""


class ApartmentAgent:
    def __init__(self, provider: LLMProvider, search: SearchService) -> None:
        self._provider = provider
        self._search = search

    async def handle(self, message: str) -> ChatResponse:
        hints = extract_hints(message)
        if hints.contradiction is not None:
            # An explicit inverted range needs no model: explain it right away.
            return await self._empty_response(
                message, ChatStatus.CONTRADICTION, hints.contradiction
            )
        complexes = await self._search.complex_names()
        cities = await self._search.city_names()
        messages: list[Message] = [
            {"role": "system", "content": build_system_prompt(complexes, cities)},
            {"role": "user", "content": message},
        ]

        for attempt in range(MAX_REPAIR_ATTEMPTS + 1):
            reply = await self._provider.complete(messages, TOOLS)
            call = reply.tool_calls[0] if reply.tool_calls else None
            try:
                return await self._dispatch(message, call, hints)
            except _InvalidToolCallError as exc:
                logger.warning(
                    "invalid tool call from LLM",
                    extra={
                        "attempt": attempt + 1,
                        "tool": call.name if call else None,
                        "problem": str(exc),
                    },
                )
                messages.extend(self._repair_messages(reply, call, str(exc)))
        raise LLMInvalidResponseError()

    async def _dispatch(
        self, message: str, call: LLMToolCall | None, hints: QueryHints
    ) -> ChatResponse:
        if call is None:
            raise _InvalidToolCallError(
                f"Нужно вызвать инструмент {SEARCH_TOOL} или {REPLY_TOOL}, а не отвечать текстом."
            )
        arguments = self._load_arguments(call)
        if call.name == REPLY_TOOL:
            try:
                reply_args = ReplyToolArguments.model_validate(arguments)
            except ValidationError as exc:
                raise _InvalidToolCallError(_describe(exc)) from exc
            return await self._reply_without_search(message, reply_args)
        if call.name == SEARCH_TOOL:
            try:
                filters = self._build_filters(_apply_hints(arguments, hints))
            except _ContradictionError as exc:
                return await self._empty_response(message, ChatStatus.CONTRADICTION, str(exc))
            return await self._run_search(message, filters)
        raise _InvalidToolCallError(f"Неизвестный инструмент {call.name!r}.")

    @staticmethod
    def _load_arguments(call: LLMToolCall) -> dict[str, object]:
        try:
            arguments = json.loads(call.arguments or "{}")
        except json.JSONDecodeError as exc:
            raise _InvalidToolCallError(
                "Аргументы инструмента должны быть корректным JSON."
            ) from exc
        if not isinstance(arguments, dict):
            raise _InvalidToolCallError("Аргументы инструмента должны быть JSON-объектом.")
        return arguments

    @staticmethod
    def _build_filters(arguments: dict[str, object]) -> ApartmentSearchFilters:
        try:
            return SearchToolArguments.model_validate(arguments).to_filters()
        except ValidationError as exc:
            range_error = _find_range_error(exc)
            if range_error is not None:
                # The user asked for something impossible: explain instead of guessing.
                raise _ContradictionError(
                    f"Условия противоречат друг другу: {range_error}. "
                    "Уточните, пожалуйста, диапазон."
                ) from exc
            raise _InvalidToolCallError(_describe(exc)) from exc

    async def _run_search(self, message: str, filters: ApartmentSearchFilters) -> ChatResponse:
        try:
            result = await self._search.search(filters)
        except FilterValueNotResolvedError as exc:
            return ChatResponse(
                query=message,
                status=ChatStatus.CLARIFICATION_NEEDED,
                message=exc.message,
                filters=filters,
                total_found=0,
                items=[],
                candidates=exc.candidates,
                data_freshness=await self._search.freshness(),
            )
        return ChatResponse(
            query=message,
            status=ChatStatus.OK if result.items else ChatStatus.NO_RESULTS,
            message=result.message,
            filters=result.filters,
            total_found=result.total,
            items=result.items,
            data_freshness=result.freshness,
        )

    async def _reply_without_search(self, message: str, args: ReplyToolArguments) -> ChatResponse:
        if args.reason == "off_topic":
            return await self._empty_response(message, ChatStatus.OFF_TOPIC, OFF_TOPIC_MESSAGE)
        status = (
            ChatStatus.CONTRADICTION
            if args.reason == "contradiction"
            else ChatStatus.CLARIFICATION_NEEDED
        )
        return await self._empty_response(message, status, args.message)

    async def _empty_response(self, query: str, status: ChatStatus, message: str) -> ChatResponse:
        return ChatResponse(
            query=query,
            status=status,
            message=message,
            filters=None,
            total_found=0,
            items=[],
            data_freshness=await self._search.freshness(),
        )

    @staticmethod
    def _repair_messages(reply: LLMReply, call: LLMToolCall | None, problem: str) -> list[Message]:
        if call is None:
            return [
                {"role": "assistant", "content": reply.content or ""},
                {"role": "user", "content": f"Ошибка: {problem}"},
            ]
        return [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {"name": call.name, "arguments": call.arguments},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": call.id,
                "content": f"Ошибка валидации аргументов: {problem} Исправь и вызови снова.",
            },
        ]


def _apply_hints(arguments: dict[str, object], hints: QueryHints) -> dict[str, object]:
    """Explicit values parsed from the text by Python win over the model's numbers."""
    merged = dict(arguments)
    for name, value in hints.as_overrides().items():
        model_value = merged.get(name)
        hint_value: object = float(value) if isinstance(value, Decimal) else value
        if model_value != hint_value:
            logger.info(
                "LLM value replaced by explicit value from the text",
                extra={"field": name, "llm_value": model_value, "text_value": hint_value},
            )
        merged[name] = hint_value
    return merged


def _find_range_error(exc: ValidationError) -> RangeError | None:
    for error in exc.errors():
        ctx_error = (error.get("ctx") or {}).get("error")
        if isinstance(ctx_error, RangeError):
            return ctx_error
    return None


def _describe(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(map(str, e['loc'])) or 'arguments'}: {e['msg']}" for e in exc.errors()
    )
