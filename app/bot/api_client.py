"""HTTP client of the bot to the service's own REST API."""

from __future__ import annotations

import logging
from typing import Any, Self

import httpx

logger = logging.getLogger(__name__)

UNAVAILABLE_MESSAGE = "Сервис подбора квартир временно недоступен. Попробуйте позже."


class ChatApiError(Exception):
    """API answered with an error; `message` is safe to show to the user."""

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class ChatApiClient:
    def __init__(
        self, base_url: str, *, timeout: float, http_client: httpx.AsyncClient | None = None
    ) -> None:
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(base_url=base_url, timeout=timeout)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def chat(self, message: str) -> dict[str, Any]:
        try:
            response = await self._client.post("/api/v1/chat", json={"message": message})
        except httpx.HTTPError as exc:
            logger.error("API request failed", extra={"error_type": type(exc).__name__})
            raise ChatApiError(UNAVAILABLE_MESSAGE) from exc
        try:
            payload: dict[str, Any] = response.json()
        except ValueError as exc:
            raise ChatApiError(UNAVAILABLE_MESSAGE) from exc
        if response.status_code >= 400:
            error = payload.get("error") or {}
            if response.status_code >= 500 and error.get("code") not in (
                "llm_not_configured",
                "llm_unavailable",
                "llm_rate_limited",
                "llm_invalid_response",
            ):
                raise ChatApiError(UNAVAILABLE_MESSAGE, error.get("code"))
            raise ChatApiError(error.get("message") or UNAVAILABLE_MESSAGE, error.get("code"))
        return payload
