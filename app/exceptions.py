"""Domain exceptions. The API layer maps them to HTTP responses with a uniform error body."""

from __future__ import annotations

from typing import Any, ClassVar


class AppError(Exception):
    """Base class for expected, user-facing errors."""

    status_code: int = 500
    code: str = "internal_error"

    def __init__(self, message: str, *, details: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


class FilterValueNotResolvedError(AppError):
    """A residential complex or city named by the user is unknown or ambiguous."""

    status_code = 422
    code = "filter_value_not_resolved"

    _LABELS: ClassVar[dict[str, str]] = {"complex_name": "ЖК", "city": "Город"}

    def __init__(self, field: str, query: str, candidates: list[str]) -> None:
        self.field = field
        self.query = query
        self.candidates = candidates
        label = self._LABELS.get(field, field)
        if candidates:
            message = (
                f"{label} «{query}» не найден однозначно. Уточните, что вы имели в виду: "
                f"{', '.join(candidates)}."
            )
        else:
            message = f"{label} «{query}» не найден в базе."
        super().__init__(
            message, details={"field": field, "query": query, "candidates": candidates}
        )


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
