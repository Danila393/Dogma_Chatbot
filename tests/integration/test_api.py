from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from app.agent.provider import LLMProvider
from app.db.repository import ApartmentRepository
from app.exceptions import LLMUnavailableError
from app.main import create_app
from tests.factories import CATALOG, catalog_apartments, make_settings
from tests.unit.test_agent import ScriptedProvider, reply_call, search_call

T0 = datetime(2026, 11, 1, tzinfo=UTC)
KNOWN_URLS = {f"https://dogma.ru/flat/{row[0]}" for row in CATALOG}


async def _client(
    provider: LLMProvider | None, seed: bool = True
) -> AsyncIterator[httpx.AsyncClient]:
    app: FastAPI = create_app(make_settings(), provider=provider)
    async with app.router.lifespan_context(app):
        if seed:
            async with app.state.session_factory() as session:
                await ApartmentRepository(session).upsert_many(catalog_apartments(), seen_at=T0)
                await session.commit()
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    async for c in _client(provider=None):
        yield c


async def test_health(client: httpx.AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_ready(client: httpx.AsyncClient) -> None:
    response = await client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "database": "ok",
        "llm_configured": False,
        "available_apartments": len(CATALOG),
    }


async def test_structured_search_task_example(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v1/apartments/search",
        json={"max_price": 7000000, "min_area": 30, "complex_name": "Самолёт", "rooms": 0},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total_found"] == 3
    assert body["filters"]["complex_name"] == "МКР Самолёт"
    first = body["items"][0]
    for field in ("complex_name", "price", "area", "rooms", "floor", "status", "url"):
        assert field in first
    assert first["area"] == 31.5  # number, not a string
    assert first["url"] in KNOWN_URLS
    assert "550 000" in body["items"][1]["explanation"]
    assert body["data_freshness"]["available_apartments"] == len(CATALOG)


async def test_structured_search_validation_error_format(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v1/apartments/search", json={"min_price": 9000000, "max_price": 1, "limit": 500}
    )
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    fields = {d["field"] for d in error["details"]}
    assert "limit" in fields


async def test_structured_search_ambiguous_complex(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/v1/apartments/search", json={"complex_name": "Парк"})
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "filter_value_not_resolved"
    assert error["details"]["candidates"] == ["DОГМА ПАРК", "Парк Победы", "Парк Победы 3"]


async def test_complexes(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/complexes")
    assert response.status_code == 200
    names = [item["name"] for item in response.json()["items"]]
    assert names == sorted(names)
    assert "Рекорд 2" in names


async def test_chat_without_llm_returns_configuration_error(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/v1/chat", json={"message": "студия"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "llm_not_configured"


@pytest.mark.parametrize("message", ["", "   ", "а" * 1001])
async def test_chat_message_length_is_validated(client: httpx.AsyncClient, message: str) -> None:
    response = await client.post("/api/v1/chat", json={"message": message})
    assert response.status_code == 422


async def test_chat_with_mocked_llm() -> None:
    provider = ScriptedProvider(
        search_call({"complex_name": "Самолет", "max_price": 7_000_000, "min_area": 30, "rooms": 0})
    )
    async for client in _client(provider):
        response = await client.post(
            "/api/v1/chat", json={"message": "Найди студию в ЖК Самолёт до 7 млн, от 30 квадратов"}
        )
    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    assert body["status"] == "ok"
    assert body["query"].startswith("Найди студию")
    assert body["filters"]["max_price"] == 7_000_000
    assert body["total_found"] == 3
    assert [i["external_id"] for i in body["items"]] == ["3", "2", "4"]
    assert all(i["url"] in KNOWN_URLS and i["explanation"] for i in body["items"])
    assert body["message"]
    assert "data_freshness" in body


async def test_chat_off_topic() -> None:
    async for client in _client(ScriptedProvider(reply_call("off_topic", "x"))):
        response = await client.post("/api/v1/chat", json={"message": "расскажи анекдот"})
    assert response.json()["status"] == "off_topic"


async def test_chat_llm_unavailable_maps_to_503() -> None:
    async for client in _client(ScriptedProvider(LLMUnavailableError())):
        response = await client.post("/api/v1/chat", json={"message": "студия"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "llm_unavailable"


async def test_unexpected_error_hides_details() -> None:
    async for client in _client(ScriptedProvider(RuntimeError("secret internals"))):
        response = await client.post("/api/v1/chat", json={"message": "студия"})
    assert response.status_code == 500
    assert response.json() == {
        "error": {"code": "internal_error", "message": "Внутренняя ошибка сервиса"}
    }


async def test_empty_database_message() -> None:
    async for client in _client(provider=None, seed=False):
        response = await client.post("/api/v1/apartments/search", json={})
    body = response.json()
    assert body["total_found"] == 0
    assert "синхронизацию" in body["message"]


async def test_openapi_documents_endpoints(client: httpx.AsyncClient) -> None:
    schema = (await client.get("/openapi.json")).json()
    assert {
        "/health",
        "/ready",
        "/api/v1/apartments/search",
        "/api/v1/chat",
        "/api/v1/complexes",
    } <= set(schema["paths"])
    assert (await client.get("/docs")).status_code == 200
