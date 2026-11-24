"""Live checks against the real source and LLM. Run explicitly: `pytest -m smoke`.

They are excluded from the default run and CI because they depend on network access,
the availability of dogma.ru and (for the LLM check) real credentials.
"""

import pytest

from app.agent.provider import OpenAICompatibleProvider
from app.agent.tools import SEARCH_TOOL, TOOLS
from app.config import Settings
from app.parser.client import DogmaApiClient
from app.parser.service import CatalogCollector

pytestmark = pytest.mark.smoke


async def test_live_source_returns_valid_apartments() -> None:
    settings = Settings()
    async with DogmaApiClient(settings) as client:
        collector = CatalogCollector(
            client, site_url=settings.parser_site_url, page_size=20, concurrency=1
        )
        collection = await collector.collect_city(3, max_pages=1)
    assert collection.city.name
    assert collection.reported_total > 0
    assert len(collection.apartments) > 0
    sample = collection.apartments[0]
    assert sample.price > 0 and sample.area > 0
    assert sample.url.startswith("https://dogma.ru/flat/")


async def test_live_llm_calls_search_tool() -> None:
    settings = Settings()
    if not settings.llm_configured:
        pytest.skip("LLM is not configured")
    provider = OpenAICompatibleProvider(settings)
    reply = await provider.complete(
        [
            {"role": "system", "content": "Преобразуй запрос в вызов инструмента."},
            {"role": "user", "content": "Найди студию в ЖК Самолёт до 7 млн, от 30 квадратов"},
        ],
        TOOLS,
    )
    assert reply.tool_calls and reply.tool_calls[0].name == SEARCH_TOOL
