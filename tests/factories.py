"""Builders for source-format payloads and domain objects used across tests."""

from __future__ import annotations

import json
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

from app.config import Settings
from app.parser.schemas import ApartmentData
from app.services.text import normalize_complex_name

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def make_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "app_env": "test",
        "database_url": "sqlite+aiosqlite:///:memory:",
        "parser_base_url": "https://service.test/api",
        "parser_site_url": "https://dogma.ru",
        "parser_request_delay": 0,
        "parser_backoff_base": 0,
        "parser_max_retries": 2,
        "parser_page_size": 2,
        "parser_concurrency": 2,
        "llm_api_key": "",
        "llm_model": "",
        "llm_base_url": "",
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)  # type: ignore[call-arg]


def raw_object(object_id: int, **overrides: Any) -> dict[str, Any]:
    obj: dict[str, Any] = {
        "id": object_id,
        "type": 1,
        "status": 2,
        "room": 1,
        "floor": 4,
        "area": 40.5,
        "cost": 6_000_000,
        "cost_sale": 0,
        "project_id": 3,
        "project_name": "МКР Самолёт",
        "letter_name": "1",
        "address": "Краснодар, ул. Западный обход",
        "layout_url": "https://bitrix.1dogma.net/upload/plan.png",
        "construction_deadline": "2027-06-30",
    }
    obj.update(overrides)
    return obj


def config_payload(city_id: int = 3, city_name: str = "Краснодар") -> dict[str, Any]:
    return {
        "cities": [
            {
                "city_id": city_id,
                "city_name": city_name,
                "projects": [{"project_id": 3, "project_name": "МКР Самолёт"}],
            }
        ],
        "count_objects": 0,
    }


def objects_payload(objects: list[dict[str, Any]], count: int) -> dict[str, Any]:
    return {"status": "200", "error": None, "data": {"count": count, "objects": objects}}


class FakeSource:
    """In-memory imitation of the source API, served through httpx.MockTransport."""

    def __init__(self, objects: list[dict[str, Any]], *, city_id: int = 3) -> None:
        self.objects = objects
        self.city_id = city_id
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail_offsets: dict[int, int] = {}  # offset -> number of 503 responses left
        self.broken_offsets: set[int] = set()  # always 503
        self.max_page_size: int | None = None
        self.count_override: int | None = None
        self.config_override: Callable[[], httpx.Response] | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        self.calls.append((request.url.path, body))
        if request.url.path.endswith("/config/all"):
            if self.config_override is not None:
                return self.config_override()
            return httpx.Response(200, json=config_payload(self.city_id))
        offset = int(body["offset"])
        limit = int(body["limit"])
        if self.max_page_size is not None:
            limit = min(limit, self.max_page_size)
        if offset in self.broken_offsets:
            return httpx.Response(503)
        if self.fail_offsets.get(offset, 0) > 0:
            self.fail_offsets[offset] -= 1
            return httpx.Response(503)
        page = self.objects[offset : offset + limit]
        count = self.count_override if self.count_override is not None else len(self.objects)
        return httpx.Response(200, json=objects_payload(page, count))

    def client(self, settings: Settings) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=settings.parser_base_url, transport=httpx.MockTransport(self.handler)
        )


def apartment_data(external_id: str, **overrides: Any) -> ApartmentData:
    complex_name = overrides.pop("complex_name", "МКР Самолёт")
    price = overrides.pop("price", 6_000_000)
    area = Decimal(str(overrides.pop("area", "40.00")))
    values: dict[str, Any] = {
        "external_id": external_id,
        "city_id": 3,
        "city": "Краснодар",
        "complex_id": 3,
        "complex_name": complex_name,
        "complex_name_normalized": normalize_complex_name(complex_name),
        "building": "1",
        "address": "Краснодар, ул. Западный обход",
        "price": price,
        "old_price": None,
        "price_per_square_meter": round(price / area),
        "area": area,
        "rooms": 1,
        "floor": 3,
        "source_status": 2,
        "url": f"https://dogma.ru/flat/{external_id}",
        "layout_url": None,
        "completion_date": None,
    }
    values.update(overrides)
    return ApartmentData(**values)
