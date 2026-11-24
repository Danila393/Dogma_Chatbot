from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.models import Apartment, ApartmentStatus, SyncRun, SyncStatus
from app.parser.client import DogmaApiClient
from app.parser.service import CatalogCollector
from app.services.sync import SyncService
from tests.factories import FakeSource, raw_object


async def _no_sleep(_: float) -> None:
    return None


def _service(
    source: FakeSource, settings: Settings, factory: async_sessionmaker[AsyncSession]
) -> SyncService:
    client = DogmaApiClient(settings, http_client=source.client(settings), sleep=_no_sleep)
    collector = CatalogCollector(
        client, site_url=settings.parser_site_url, page_size=2, concurrency=2
    )
    return SyncService(collector, factory)


async def _apartments(factory: async_sessionmaker[AsyncSession]) -> dict[str, Apartment]:
    async with factory() as session:
        rows = (await session.scalars(select(Apartment))).all()
    return {row.external_id: row for row in rows}


async def test_full_sync_then_resync_without_duplicates(
    settings: Settings, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    source = FakeSource([raw_object(i) for i in range(1, 6)])
    service = _service(source, settings, session_factory)

    first = await service.run([3])
    second = await service.run([3])

    assert first.status is SyncStatus.SUCCESS
    assert (first.created, first.updated) == (5, 0)
    assert (second.created, second.updated, second.unchanged) == (0, 0, 5)
    assert len(await _apartments(session_factory)) == 5


async def test_price_and_status_changes_are_applied(
    settings: Settings, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    objects: list[dict[str, Any]] = [raw_object(i) for i in range(1, 5)]
    source = FakeSource(objects)
    service = _service(source, settings, session_factory)
    await service.run([3])

    source.objects = [raw_object(1, cost=5_000_000), raw_object(2), raw_object(3)]  # 4 sold
    report = await service.run([3])

    assert report.status is SyncStatus.SUCCESS
    assert report.updated == 1
    assert report.marked_unavailable == 1
    rows = await _apartments(session_factory)
    assert rows["1"].price == 5_000_000
    assert rows["4"].status == ApartmentStatus.UNAVAILABLE


async def test_partial_sync_keeps_existing_availability(
    settings: Settings, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    source = FakeSource([raw_object(i) for i in range(1, 7)])
    service = _service(source, settings, session_factory)
    await service.run([3])

    source.broken_offsets = {2}  # lots 3 and 4 cannot be fetched this time
    report = await service.run([3])

    assert report.status is SyncStatus.PARTIAL
    assert report.marked_unavailable == 0
    rows = await _apartments(session_factory)
    assert all(row.status == ApartmentStatus.AVAILABLE for row in rows.values())


async def test_demo_limited_sync_does_not_mark_unavailable(
    settings: Settings, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    source = FakeSource([raw_object(i) for i in range(1, 7)])
    service = _service(source, settings, session_factory)
    await service.run([3])

    report = await service.run([3], max_lots=2)

    assert report.status is SyncStatus.PARTIAL
    assert report.marked_unavailable == 0
    assert len(await _apartments(session_factory)) == 6


async def test_network_failure_keeps_data_and_records_failed_run(
    settings: Settings, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    source = FakeSource([raw_object(i) for i in range(1, 4)])
    service = _service(source, settings, session_factory)
    await service.run([3])

    source.config_override = lambda: httpx.Response(503)
    report = await service.run([3])

    assert report.status is SyncStatus.FAILED
    rows = await _apartments(session_factory)
    assert len(rows) == 3
    assert all(row.status == ApartmentStatus.AVAILABLE for row in rows.values())
    async with session_factory() as session:
        runs = (await session.scalars(select(SyncRun).order_by(SyncRun.id))).all()
    assert [run.status for run in runs] == ["success", "failed"]
    assert runs[-1].errors and "503" in runs[-1].errors


async def test_one_failed_city_does_not_block_others(
    settings: Settings, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    source = FakeSource([raw_object(i) for i in range(1, 4)])
    original = source.handler

    def handler(request: httpx.Request) -> httpx.Response:
        if b'"city_ids":[7]' in request.content.replace(b" ", b""):
            return httpx.Response(200, json={"cities": []})
        return original(request)

    client = DogmaApiClient(
        settings,
        http_client=httpx.AsyncClient(
            base_url=settings.parser_base_url, transport=httpx.MockTransport(handler)
        ),
        sleep=_no_sleep,
    )
    collector = CatalogCollector(client, site_url="https://dogma.ru", page_size=2, concurrency=2)
    report = await SyncService(collector, session_factory).run([7, 3])

    assert report.status is SyncStatus.PARTIAL
    assert report.cities[0].error is not None
    assert report.cities[1].complete
    assert len(await _apartments(session_factory)) == 3
