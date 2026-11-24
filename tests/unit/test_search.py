from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repository import ApartmentRepository, SyncRunRepository
from app.exceptions import FilterValueNotResolvedError
from app.schemas.search import ApartmentSearchFilters, SortBy
from app.services.complexes import resolve_complex
from app.services.search import SearchService
from tests.factories import apartment_data

T0 = datetime(2026, 11, 1, tzinfo=UTC)

CATALOG = [
    # id, complex, rooms, price, area, floor
    ("1", "МКР Самолёт", 0, 5_365_500, "26.20", 5),
    ("2", "МКР Самолёт", 0, 6_450_000, "30.00", 7),
    ("3", "МКР Самолёт", 0, 6_450_000, "31.50", 2),
    ("4", "МКР Самолёт", 0, 7_000_000, "33.00", 9),
    ("5", "МКР Самолёт", 0, 7_000_001, "40.00", 3),
    ("6", "МКР Самолёт", 2, 11_721_675, "67.20", 4),
    ("7", "САМОЛЁТ 7", 0, 5_900_000, "30.10", 6),
    ("8", "Рекорд 2", 0, 7_175_445, "32.30", 3),
    ("9", "Рекорд 2", 1, 8_299_414, "44.98", 12),
    ("10", "DОГМА ПАРК", 3, 13_274_461, "76.91", 10),
    ("11", "Парк Победы", 2, 10_794_565, "67.30", 8),
    ("12", "Парк Победы 3", 1, 6_900_000, "38.00", 4),
]


@pytest.fixture
async def service(session: AsyncSession) -> SearchService:
    repo = ApartmentRepository(session)
    await repo.upsert_many(
        [
            apartment_data(ext, complex_name=name, rooms=rooms, price=price, area=area, floor=floor)
            for ext, name, rooms, price, area, floor in CATALOG
        ]
        + [apartment_data("99", complex_name="Снегири", city_id=6, city="Омск", rooms=0)],
        seen_at=T0,
    )
    await repo.upsert_many(
        [apartment_data("100", complex_name="МКР Самолёт", rooms=0, price=1_000_000, area="35")],
        seen_at=T0,
    )
    await repo.mark_missing_unavailable(
        city_id=3, seen_external_ids={row[0] for row in CATALOG}, now=T0
    )  # lot 100 is sold
    await session.commit()
    return SearchService(repo, SyncRunRepository(session), default_limit=5, max_limit=10)


async def _ids(service: SearchService, **filters: Any) -> list[str]:
    result = await service.search(ApartmentSearchFilters(**filters))
    return [item.external_id for item in result.items]


async def test_task_example_studio_in_samolet(service: SearchService) -> None:
    result = await service.search(
        ApartmentSearchFilters(complex_name="Самолёт", max_price=7_000_000, min_area=30, rooms=0)
    )
    assert [i.external_id for i in result.items] == ["3", "2", "4"]  # equal price: larger first
    assert result.total == 3
    assert result.filters.complex_name == "МКР Самолёт"
    assert all(i.complex_name == "МКР Самолёт" for i in result.items)


async def test_price_filter_bounds_are_inclusive(service: SearchService) -> None:
    ids = await _ids(service, max_price=7_000_000, min_price=7_000_000)
    assert ids == ["4"]


async def test_area_filter_is_exact_on_boundary(service: SearchService) -> None:
    assert "2" in await _ids(service, min_area=Decimal("30"), max_area=30, limit=10)
    assert await _ids(service, min_area="30.01", max_area="30.09") == []


async def test_complex_filter(service: SearchService) -> None:
    assert await _ids(service, complex_name="рекорд 2") == ["8", "9"]


async def test_rooms_filter(service: SearchService) -> None:
    assert await _ids(service, rooms=2) == ["11", "6"]


async def test_city_filter(service: SearchService) -> None:
    assert await _ids(service, city="омск") == ["99"]


async def test_floor_filter(service: SearchService) -> None:
    assert await _ids(service, min_floor=9, max_floor=10) == ["4", "10"]


async def test_combined_filters_use_and(service: SearchService) -> None:
    ids = await _ids(service, rooms=0, max_price=6_000_000, min_area=26)
    assert ids == ["1", "7", "99"]
    assert await _ids(service, rooms=0, max_price=6_000_000, min_area=26, city="Краснодар") == [
        "1",
        "7",
    ]


async def test_default_sort_price_then_bigger_area_then_id(service: SearchService) -> None:
    ids = await _ids(service, complex_name="Самолет", rooms=0, limit=10)
    assert ids == ["1", "3", "2", "4", "5"]  # 3 and 2 share the price; 3 is larger


async def test_sort_area_desc(service: SearchService) -> None:
    assert (await _ids(service, sort_by=SortBy.AREA_DESC, limit=2)) == ["10", "11"]


async def test_sort_price_per_sqm(service: SearchService) -> None:
    result = await service.search(
        ApartmentSearchFilters(sort_by=SortBy.PRICE_PER_SQM_ASC, limit=10)
    )
    values = [i.price_per_square_meter for i in result.items]
    assert values == sorted(values)


async def test_default_limit_is_five_and_total_is_reported(service: SearchService) -> None:
    result = await service.search(ApartmentSearchFilters())
    assert len(result.items) == 5
    assert result.total == 13
    assert [i.rank for i in result.items] == [1, 2, 3, 4, 5]


async def test_fewer_results_than_limit(service: SearchService) -> None:
    result = await service.search(ApartmentSearchFilters(complex_name="Рекорд 2", limit=10))
    assert len(result.items) == result.total == 2


async def test_no_results_suggests_changes(service: SearchService) -> None:
    result = await service.search(ApartmentSearchFilters(max_price=1_500_000, rooms=3))
    assert result.items == []
    assert result.total == 0
    assert "не найдено" in result.message
    assert "увеличить бюджет" in result.message


async def test_unavailable_apartments_are_excluded(service: SearchService) -> None:
    ids = await _ids(service, max_price=2_000_000)
    assert "100" not in ids


async def test_ambiguous_complex_requires_clarification(service: SearchService) -> None:
    with pytest.raises(FilterValueNotResolvedError) as error:
        await service.search(ApartmentSearchFilters(complex_name="Парк"))
    assert error.value.candidates == ["DОГМА ПАРК", "Парк Победы", "Парк Победы 3"]


async def test_unknown_complex_lists_known_ones(service: SearchService) -> None:
    with pytest.raises(FilterValueNotResolvedError) as error:
        await service.search(ApartmentSearchFilters(complex_name="Изумрудный город"))
    assert "Рекорд 2" in error.value.candidates


async def test_partial_name_resolves_with_note(service: SearchService) -> None:
    result = await service.search(ApartmentSearchFilters(complex_name="Снегирь"))
    assert result.filters.complex_name == "Снегири"
    assert result.notes and "интерпретирован" in result.notes[0]


async def test_message_mentions_count(service: SearchService) -> None:
    result = await service.search(ApartmentSearchFilters(rooms=0, complex_name="Самолёт"))
    assert result.message.startswith("Нашлось 5 подходящих квартир.")


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("Самолет", "МКР Самолёт"),
        ("ЖК «САМОЛЁТ»", "МКР Самолёт"),
        ("самолёт 7", "САМОЛЁТ 7"),
        ("Догма парк", "DОГМА ПАРК"),
        ("Парк победы 3", "Парк Победы 3"),
        ("Юкки", "Догма Юкки"),
        ("Публицыст", "Публицист"),
    ],
)
def test_complex_resolution(query: str, expected: str) -> None:
    names = [
        "МКР Самолёт",
        "САМОЛЁТ 7",
        "DОГМА ПАРК",
        "Парк Победы",
        "Парк Победы 3",
        "Догма Юкки",
        "Публицист",
    ]
    assert resolve_complex(query, names).value == expected


@pytest.mark.parametrize(
    "filters",
    [
        {"min_price": 0},
        {"max_price": -5},
        {"min_area": 0},
        {"rooms": -1},
        {"limit": 0},
        {"limit": 11},
        {"sort_by": "random"},
        {"min_price": 8_000_000, "max_price": 7_000_000},
        {"min_area": 50, "max_area": 30},
        {"min_floor": 10, "max_floor": 2},
        {"unknown": 1},
    ],
)
def test_invalid_filters_are_rejected(filters: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ApartmentSearchFilters(**filters)


async def test_empty_database_message(session: AsyncSession) -> None:
    service = SearchService(
        ApartmentRepository(session), SyncRunRepository(session), default_limit=5, max_limit=10
    )
    result = await service.search(ApartmentSearchFilters())
    assert result.total == 0
    assert "База квартир пока пуста" in result.message


async def test_complex_list(service: SearchService) -> None:
    complexes = await service.list_complexes()
    names = [c.name for c in complexes.items]
    assert "МКР Самолёт" in names and "Снегири" in names
    samolet = next(c for c in complexes.items if c.name == "МКР Самолёт")
    assert samolet.available_apartments == 6  # the sold lot is not counted
    assert complexes.data_freshness.available_apartments == 13
