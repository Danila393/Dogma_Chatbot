"""Domain exceptions. The API layer maps them to HTTP responses with a uniform error body."""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base class for expected, user-facing errors."""

    status_code: int = 500
    code: str = "internal_error"

    def __init__(self, message: str, *, details: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


class ComplexNotResolvedError(AppError):
    """The requested residential complex is unknown or ambiguous."""

    status_code = 422
    code = "complex_not_resolved"

    def __init__(self, query: str, candidates: list[str]) -> None:
        self.query = query
        self.candidates = candidates
        if candidates:
            message = (
                f"ЖК «{query}» не найден однозначно. Уточните, какой из вариантов вы имели "
                f"в виду: {', '.join(candidates)}."
            )
        else:
            message = f"ЖК «{query}» не найден в базе."
        super().__init__(message, details={"query": query, "candidates": candidates})


class LLMNotConfiguredError(AppError):
    status_code = 503
    code = "llm_not_configured"

    def __init__(self) -> None:
        super().__init__(
            "AI-агент не настроен: задайте LLM_MODEL и LLM_API_KEY (или LLM_BASE_URL). "
            "Структурированный поиск /api/v1/apartments/search работает без LLM."
        )


class LLMUnavailableError(AppError):
    status_code = 503
    code = "llm_unavailable"

    def __init__(
        self, message: str = "LLM-провайдер временно недоступен. Повторите позже."
    ) -> None:
        super().__init__(message)


class LLMRateLimitedError(AppError):
    status_code = 503
    code = "llm_rate_limited"

    def __init__(self) -> None:
        super().__init__("Превышен лимит запросов к LLM-провайдеру. Повторите позже.")


class LLMInvalidResponseError(AppError):
    status_code = 502
    code = "llm_invalid_response"

    def __init__(self) -> None:
        super().__init__(
            "Не удалось разобрать запрос: модель вернула некорректный ответ. "
            "Попробуйте переформулировать."
        )


class SourceError(Exception):
    """Unrecoverable problem talking to the data source (after retries)."""


class SourceFormatError(SourceError):
    """The data source returned a payload with an unexpected structure."""
