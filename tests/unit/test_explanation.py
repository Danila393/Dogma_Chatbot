import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from app.schemas.apartment import ApartmentOut
from app.schemas.search import ApartmentSearchFilters, SortBy
from app.services.explanation import explain


def _apartment(**overrides: Any) -> ApartmentOut:
    values: dict[str, Any] = {
        "id": 1,
        "external_id": "55951",
        "complex_name": "МКР Самолёт",
        "city": "Краснодар",
        "address": None,
        "building": "3",
        "price": 6_450_000,
        "old_price": None,
        "price_per_square_meter": 198_462,
        "area": Decimal("32.50"),
        "rooms": 0,
        "floor": 3,
        "status": "available",
        "url": "https://dogma.ru/flat/55951",
        "layout_url": None,
        "completion_date": None,
        "updated_at": datetime(2026, 11, 1, tzinfo=UTC),
    }
    values.update(overrides)
    return ApartmentOut(**values)


def test_task_example_numbers_are_computed_from_data() -> None:
    filters = ApartmentSearchFilters(
        complex_name="МКР Самолёт", max_price=7_000_000, min_area=30, rooms=0
    )
    text = explain(_apartment(), filters, rank=1, total=3)

    assert text.startswith("Соответствует всем указанным условиям: находится в ЖК «МКР Самолёт»")
    assert "студия, как в запросе" in text
    assert "на 550 000 ₽ меньше максимального бюджета" in text
    assert "на 2,5 м² больше минимальной" in text
    assert "Самая доступная по цене среди 3 подходящих вариантов." in text


def test_price_range_and_area_max() -> None:
    filters = ApartmentSearchFilters(min_price=5_000_000, max_price=7_000_000, max_area=40)
    text = explain(_apartment(), filters, rank=2, total=21)
    assert "в диапазоне 5 000 000 ₽ – 7 000 000 ₽" in text
    assert "на 7,5 м² меньше максимальной" in text
    assert "2-я по цене среди 21 подходящего варианта." in text


def test_exact_boundaries() -> None:
    filters = ApartmentSearchFilters(max_price=6_450_000, min_area="32.5")
    text = explain(_apartment(), filters, rank=1, total=1)
    assert "по верхней границе бюджета" in text
    assert "ровно минимальная" in text


def test_no_constraints() -> None:
    text = explain(_apartment(), ApartmentSearchFilters(), rank=1, total=100)
    assert text.startswith("Ограничений в запросе нет")


def test_sort_specific_reason() -> None:
    area_text = explain(
        _apartment(), ApartmentSearchFilters(sort_by=SortBy.AREA_DESC), rank=1, total=5
    )
    assert "Самая просторная среди 5 подходящих вариантов (32,5 м²)." in area_text
    ppsm_text = explain(
        _apartment(), ApartmentSearchFilters(sort_by=SortBy.PRICE_PER_SQM_ASC), rank=3, total=5
    )
    assert "3-я по цене за м²" in ppsm_text and "198 462 ₽" in ppsm_text


def test_discount_is_mentioned() -> None:
    text = explain(
        _apartment(price=7_175_445, old_price=9_567_260),
        ApartmentSearchFilters(),
        rank=1,
        total=1,
    )
    assert "Скидка 2 391 815 ₽" in text


def test_every_number_in_text_matches_data() -> None:
    apartment = _apartment()
    filters = ApartmentSearchFilters(max_price=7_000_000, min_area=30)
    text = explain(apartment, filters, rank=1, total=1)
    numbers = {n.replace(" ", "") for n in re.findall(r"\d[\d ]*(?:,\d+)?", text)}
    allowed = {
        str(apartment.price),
        str(7_000_000 - apartment.price),
        "32,5",
        "2,5",
        "1",
    }
    assert numbers <= allowed
