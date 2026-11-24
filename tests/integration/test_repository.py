from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Apartment, ApartmentStatus
from app.db.repository import ApartmentRepository
from tests.factories import apartment_data

T0 = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
T1 = T0 + timedelta(days=1)


async def _count(session: AsyncSession) -> int:
    return int(await session.scalar(select(func.count()).select_from(Apartment)) or 0)


async def test_insert_new_apartment(session: AsyncSession) -> None:
    repo = ApartmentRepository(session)
    stats = await repo.upsert_many([apartment_data("1", area="32.30")], seen_at=T0)
    await session.commit()

    assert (stats.created, stats.updated, stats.unchanged) == (1, 0, 0)
    stored = await repo.get_by_external_id("1")
    assert stored is not None
    assert stored.area == Decimal("32.30")
    assert stored.status == ApartmentStatus.AVAILABLE
    assert stored.created_at == T0
    assert stored.updated_at.tzinfo is not None


async def test_area_is_stored_exactly(session: AsyncSession) -> None:
    raw = await ApartmentRepository(session).upsert_many(
        [apartment_data("1", area="30.00")], seen_at=T0
    )
    assert raw.created == 1
    stored_raw = await session.scalar(select(Apartment.area).where(Apartment.external_id == "1"))
    assert stored_raw == Decimal("30.00")
    int_value = await session.scalar(
        select(func.typeof(Apartment.area)).where(Apartment.external_id == "1")
    )
    assert int_value == "integer"


async def test_repeated_upsert_does_not_create_duplicates(session: AsyncSession) -> None:
    repo = ApartmentRepository(session)
    items = [apartment_data(str(i)) for i in range(1, 4)]
    await repo.upsert_many(items, seen_at=T0)
    stats = await repo.upsert_many(items, seen_at=T1)
    await session.commit()

    assert (stats.created, stats.updated, stats.unchanged) == (0, 0, 3)
    assert await _count(session) == 3
    stored = await repo.get_by_external_id("1")
    assert stored is not None
    assert stored.updated_at == T0  # nothing changed
    assert stored.last_seen_at == T1


async def test_price_change_updates_row_and_timestamp(session: AsyncSession) -> None:
    repo = ApartmentRepository(session)
    await repo.upsert_many([apartment_data("1", price=6_000_000)], seen_at=T0)
    stats = await repo.upsert_many(
        [apartment_data("1", price=5_500_000, old_price=6_000_000)], seen_at=T1
    )
    await session.commit()

    assert stats.updated == 1
    stored = await repo.get_by_external_id("1")
    assert stored is not None
    assert stored.price == 5_500_000
    assert stored.old_price == 6_000_000
    assert stored.updated_at == T1
    assert stored.created_at == T0


async def test_status_change_and_reappearance(session: AsyncSession) -> None:
    repo = ApartmentRepository(session)
    await repo.upsert_many([apartment_data("1"), apartment_data("2")], seen_at=T0)
    marked = await repo.mark_missing_unavailable(city_id=3, seen_external_ids={"1"}, now=T1)
    await session.commit()

    assert marked == 1
    gone = await repo.get_by_external_id("2")
    assert gone is not None
    assert gone.status == ApartmentStatus.UNAVAILABLE
    assert gone.updated_at == T1

    stats = await repo.upsert_many([apartment_data("2")], seen_at=T1 + timedelta(days=1))
    await session.commit()
    assert stats.updated == 1
    back = await repo.get_by_external_id("2")
    assert back is not None
    assert back.status == ApartmentStatus.AVAILABLE


async def test_mark_missing_is_scoped_to_city(session: AsyncSession) -> None:
    repo = ApartmentRepository(session)
    await repo.upsert_many(
        [apartment_data("1", city_id=3), apartment_data("2", city_id=6, city="Омск")], seen_at=T0
    )
    marked = await repo.mark_missing_unavailable(city_id=3, seen_external_ids=set(), now=T1)
    await session.commit()

    assert marked == 1
    other_city = await repo.get_by_external_id("2")
    assert other_city is not None
    assert other_city.status == ApartmentStatus.AVAILABLE


async def test_large_batches(session: AsyncSession) -> None:
    repo = ApartmentRepository(session)
    stats = await repo.upsert_many([apartment_data(str(i)) for i in range(1, 1201)], seen_at=T0)
    await session.commit()
    assert stats.created == 1200
    assert await _count(session) == 1200
    marked = await repo.mark_missing_unavailable(
        city_id=3, seen_external_ids={str(i) for i in range(1, 1101)}, now=T1
    )
    assert marked == 100


async def test_transaction_rollback_keeps_previous_data(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session, session.begin():
        await ApartmentRepository(session).upsert_many(
            [apartment_data("1", price=6_000_000)], seen_at=T0
        )

    with pytest.raises(RuntimeError):
        async with session_factory() as session, session.begin():
            repo = ApartmentRepository(session)
            await repo.upsert_many(
                [apartment_data("1", price=1_000_000), apartment_data("2")], seen_at=T1
            )
            await repo.mark_missing_unavailable(city_id=3, seen_external_ids=set(), now=T1)
            raise RuntimeError("simulated failure mid-sync")

    async with session_factory() as session:
        repo = ApartmentRepository(session)
        stored = await repo.get_by_external_id("1")
        assert stored is not None
        assert stored.price == 6_000_000
        assert stored.status == ApartmentStatus.AVAILABLE
        assert await repo.get_by_external_id("2") is None


async def test_list_complexes_counts_only_available(session: AsyncSession) -> None:
    repo = ApartmentRepository(session)
    await repo.upsert_many(
        [
            apartment_data("1", complex_name="МКР Самолёт", price=5_000_000),
            apartment_data("2", complex_name="МКР Самолёт", price=7_000_000),
            apartment_data("3", complex_name="Рекорд 2", price=9_000_000),
        ],
        seen_at=T0,
    )
    await repo.mark_missing_unavailable(city_id=3, seen_external_ids={"1", "2"}, now=T1)
    await session.commit()

    complexes = await repo.list_complexes()
    assert [(c.name, c.available_count, c.min_price, c.max_price) for c in complexes] == [
        ("МКР Самолёт", 2, 5_000_000, 7_000_000)
    ]
    assert await repo.list_cities() == ["Краснодар"]
    assert await repo.count_by_status() == {"available": 2, "unavailable": 1}
