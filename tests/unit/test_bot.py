from typing import Any

import httpx

from app.bot.api_client import UNAVAILABLE_MESSAGE, ChatApiClient
from app.bot.formatting import TELEGRAM_LIMIT, format_chat_response
from app.bot.main import HELP_TEXT, answer_text, build_router


def _item(rank: int, **overrides: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "rank": rank,
        "complex_name": "МКР Самолёт",
        "rooms": 0,
        "area": 31.5,
        "floor": 2,
        "price": 6_450_000,
        "old_price": None,
        "explanation": "Соответствует всем указанным условиям <тест>.",
        "url": "https://dogma.ru/flat/3",
    }
    item.update(overrides)
    return item


def _api(handler: Any) -> ChatApiClient:
    http = httpx.AsyncClient(base_url="http://api", transport=httpx.MockTransport(handler))
    return ChatApiClient("http://api", timeout=5, http_client=http)


def test_formatting_uses_payload_values_and_escapes_html() -> None:
    text = format_chat_response(
        {
            "message": "Нашлось 1 подходящая квартира.",
            "items": [_item(1, old_price=7_000_000)],
            "data_freshness": {"last_synced_at": "2026-11-24T10:00:00Z"},
        }
    )
    assert "<b>1. МКР Самолёт</b> — Студия, 31,5 м², 2 этаж" in text
    assert "6 450 000 ₽ <s>7 000 000 ₽</s>" in text
    assert "&lt;тест&gt;" in text
    assert '<a href="https://dogma.ru/flat/3">' in text
    assert "Данные обновлены: 2026-11-24T10:00 UTC" in text


def test_formatting_respects_telegram_limit() -> None:
    items = [_item(i, explanation="x" * 1500) for i in range(1, 6)]
    text = format_chat_response({"message": "m", "items": items})
    assert len(text) <= TELEGRAM_LIMIT


def test_candidates_are_listed() -> None:
    text = format_chat_response({"message": "Уточните ЖК", "items": [], "candidates": ["A", "B"]})
    assert text.endswith("Варианты: A, B")


async def test_answer_text_calls_api() -> None:
    seen: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.content)
        return httpx.Response(200, json={"message": "Нашлось 1", "items": [_item(1)]})

    text = await answer_text("  студия  ", _api(handler))
    assert "Нашлось 1" in text
    assert seen and b'"message"' in seen[0]


async def test_answer_text_shows_api_error_message() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503, json={"error": {"code": "llm_not_configured", "message": "AI-агент не настроен"}}
        )

    assert await answer_text("студия", _api(handler)) == "AI-агент не настроен"


async def test_answer_text_hides_internal_errors() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"code": "internal_error", "message": "boom"}})

    assert await answer_text("студия", _api(handler)) == UNAVAILABLE_MESSAGE


async def test_answer_text_handles_connection_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    assert await answer_text("студия", _api(handler)) == UNAVAILABLE_MESSAGE


async def test_answer_text_validates_input_locally() -> None:
    def handler(_: httpx.Request) -> httpx.Response:  # pragma: no cover - must not be called
        raise AssertionError("API must not be called")

    assert await answer_text("   ", _api(handler)) == HELP_TEXT
    assert "слишком длинное" in await answer_text("a" * 1001, _api(handler))


def test_router_is_built() -> None:
    router = build_router(_api(lambda _: httpx.Response(200, json={})))
    assert router.message.handlers
