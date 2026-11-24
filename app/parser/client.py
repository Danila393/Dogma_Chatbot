"""HTTP client for the dogma.ru layouts-filter JSON API with timeouts, retries and rate limiting."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from typing import Any, Self

import httpx
from pydantic import ValidationError

from app.config import Settings
from app.exceptions import SourceError, SourceFormatError
from app.parser.schemas import RawConfig, RawObjectsData, RawObjectsResponse

logger = logging.getLogger(__name__)

RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
MAX_RETRY_AFTER_SECONDS = 60.0
MAX_RATE_LIMIT_COOLDOWN_SECONDS = 60.0
APARTMENT_TYPE = 1
AVAILABLE_STATUSES = (2,)


class RateLimiter:
    """Minimum interval between request starts, shared by all concurrent tasks.

    `throttle()` reacts to HTTP 429: every task pauses for the cooldown and the interval
    between requests doubles (up to `max_interval`). After `recovery_after` consecutive
    successful requests the interval is halved again, back down to the configured base.
    """

    def __init__(
        self,
        min_interval: float,
        *,
        max_interval: float = 5.0,
        recovery_after: int = 20,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._base_interval = min_interval
        self._interval = min_interval
        self._recovery_after = recovery_after
        self._successes = 0
        self._max_interval = max(max_interval, min_interval)
        self._clock = clock
        self._sleep = sleep
        self._lock = asyncio.Lock()
        self._next_allowed = 0.0

    @property
    def interval(self) -> float:
        return self._interval

    async def wait(self) -> None:
        async with self._lock:
            now = self._clock()
            delay = self._next_allowed - now
            if delay > 0:
                await self._sleep(delay)
                now = self._clock()
            self._next_allowed = now + self._interval

    async def throttle(self, cooldown: float) -> None:
        async with self._lock:
            self._successes = 0
            self._next_allowed = max(self._next_allowed, self._clock() + cooldown)
            self._interval = min(max(self._interval * 2, 0.5), self._max_interval)

    def record_success(self) -> None:
        if self._interval <= self._base_interval:
            return
        self._successes += 1
        if self._successes >= self._recovery_after:
            self._successes = 0
            self._interval = max(self._base_interval, self._interval / 2)


class DogmaApiClient:
    """Thin async wrapper over the two endpoints the catalog page uses."""

    def __init__(
        self,
        settings: Settings,
        http_client: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._settings = settings
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(
            base_url=settings.parser_base_url,
            timeout=httpx.Timeout(settings.parser_timeout),
            headers={
                "User-Agent": settings.parser_user_agent,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        self._rate_limiter = RateLimiter(settings.parser_request_delay, sleep=sleep)
        self._sleep = sleep
        self.requests_made = 0

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def fetch_config(self, city_id: int) -> RawConfig:
        body = {"type": APARTMENT_TYPE, "statuses": list(AVAILABLE_STATUSES), "city_ids": [city_id]}
        payload = await self._post_json("/config/all", body)
        data = payload.get("data", payload) if isinstance(payload, dict) else payload
        try:
            return RawConfig.model_validate(data)
        except ValidationError as exc:
            raise SourceFormatError(
                f"unexpected config/all payload: {exc.error_count()} errors"
            ) from exc

    async def fetch_objects(self, city_id: int, *, limit: int, offset: int) -> RawObjectsData:
        body = {
            "type": APARTMENT_TYPE,
            "statuses": list(AVAILABLE_STATUSES),
            "city_ids": [city_id],
            "order": {"field": "order", "type": "asc"},
            "limit": limit,
            "offset": offset,
        }
        payload = await self._post_json("/objects/filter", body)
        try:
            response = RawObjectsResponse.model_validate(payload)
        except ValidationError as exc:
            raise SourceFormatError(
                f"unexpected objects/filter payload: {exc.error_count()} errors"
            ) from exc
        if response.error:
            raise SourceFormatError(f"source reported an error: {response.error!r}")
        return response.data

    async def _post_json(self, path: str, body: dict[str, Any]) -> Any:
        max_attempts = self._settings.parser_max_retries + 1
        last_error: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            await self._rate_limiter.wait()
            started = time.monotonic()
            self.requests_made += 1
            try:
                response = await self._client.post(path, json=body)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
                retry_after: float | None = None
                reason = type(exc).__name__
            else:
                if response.status_code < 400:
                    self._rate_limiter.record_success()
                    try:
                        return response.json()
                    except ValueError as exc:
                        raise SourceFormatError(f"{path}: response is not JSON") from exc
                if response.status_code not in RETRYABLE_STATUS:
                    raise SourceError(f"{path}: HTTP {response.status_code}")
                last_error = SourceError(f"{path}: HTTP {response.status_code}")
                retry_after = _parse_retry_after(response.headers.get("Retry-After"))
                reason = f"HTTP {response.status_code}"

            if attempt == max_attempts:
                break
            rate_limited = reason == "HTTP 429"
            if retry_after is not None:
                delay = retry_after
            elif rate_limited:
                delay = self._rate_limit_backoff(attempt)
            else:
                delay = self._backoff(attempt)
            logger.warning(
                "source request failed, retrying",
                extra={
                    "path": path,
                    "attempt": attempt,
                    "reason": reason,
                    "retry_in_s": round(delay, 2),
                    "elapsed_s": round(time.monotonic() - started, 2),
                },
            )
            if rate_limited:
                # Pause every concurrent task and slow the whole run down, not just this one.
                await self._rate_limiter.throttle(delay)
            else:
                await self._sleep(delay)

        raise SourceError(
            f"{path}: giving up after {max_attempts} attempts (last error: {last_error})"
        ) from last_error

    def _rate_limit_backoff(self, attempt: int) -> float:
        base: float = self._settings.parser_rate_limit_backoff * (2 ** (attempt - 1))
        return min(base, MAX_RATE_LIMIT_COOLDOWN_SECONDS)

    def _backoff(self, attempt: int) -> float:
        base: float = self._settings.parser_backoff_base * (2 ** (attempt - 1))
        return base + random.uniform(0, base / 2)


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        return None
    return max(0.0, min(seconds, MAX_RETRY_AFTER_SECONDS))
