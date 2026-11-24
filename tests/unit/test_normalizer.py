from datetime import date
from decimal import Decimal

import pytest

from app.parser.normalizer import NormalizationError, normalize_object
from app.parser.schemas import CityInfo, RawObject, RawObjectsResponse
from app.services.text import format_area, format_rub, normalize_complex_name
from tests.factories import load_fixture, raw_object

CITY = CityInfo(city_id=3, name="Краснодар", complexes={3: "МКР Самолёт", 5: "Рекорд 2"})
SITE = "https://dogma.ru"


def _normalize(**overrides: object):  # type: ignore[no-untyped-def]
    return normalize_object(
        RawObject.model_validate(raw_object(1, **overrides)), CITY, site_url=SITE
    )


def test_real_fixture_page_is_parsed() -> None:
    response = RawObjectsResponse.model_validate(load_fixture("objects_page.json"))
    apartments = [normalize_object(o, CITY, site_url=SITE) for o in response.data.objects or []]

    discounted = apartments[0]
    assert discounted.external_id == "55951"
    assert discounted.complex_name == "Рекорд 2"
    assert discounted.price == 7_175_445
    assert discounted.old_price == 9_567_260
    assert discounted.area == Decimal("32.30")
    assert discounted.rooms == 0
    assert discounted.floor == 3
    assert discounted.url == "https://dogma.ru/flat/55951"
    assert discounted.city == "Краснодар"
    assert discounted.building == "3"
    assert discounted.completion_date == date(2025, 12, 31)


def test_discount_price_uses_cost_sale_and_recomputes_price_per_sqm() -> None:
    apartment = _normalize(cost=10_000_000, cost_sale=8_000_000, area=40)
    assert apartment.price == 8_000_000
    assert apartment.old_price == 10_000_000
    assert apartment.price_per_square_meter == 200_000


def test_cost_sale_not_lower_than_cost_is_ignored() -> None:
    apartment = _normalize(cost=5_000_000, cost_sale=5_500_000)
    assert apartment.price == 5_000_000
    assert apartment.old_price is None


def test_studio_is_zero_rooms() -> None:
    assert _normalize(room=0).rooms == 0


def test_area_is_decimal_with_two_places() -> None:
    assert _normalize(area=19.999).area == Decimal("20.00")
    assert _normalize(area="44.98").area == Decimal("44.98")


def test_missing_optional_fields_become_none() -> None:
    apartment = _normalize(address="  ", layout_url="", letter_name=None, construction_deadline="")
    assert apartment.address is None
    assert apartment.layout_url is None
    assert apartment.building is None
    assert apartment.completion_date is None


def test_zero_date_is_none() -> None:
    assert _normalize(construction_deadline="0000-01-01").completion_date is None


def test_complex_name_falls_back_to_city_directory() -> None:
    assert _normalize(project_name="", project_id=5).complex_name == "Рекорд 2"


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("cost", 0, "price"),
        ("cost", None, "price"),
        ("area", 0, "area"),
        ("area", None, "area"),
        ("room", None, "rooms"),
        ("room", -1, "rooms"),
        ("floor", None, "floor"),
        ("status", None, "status"),
    ],
)
def test_required_fields_are_validated(field: str, value: object, reason: str) -> None:
    with pytest.raises(NormalizationError, match=reason):
        _normalize(**{field: value})


def test_unknown_complex_is_rejected() -> None:
    with pytest.raises(NormalizationError, match="complex"):
        _normalize(project_name=None, project_id=999)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("МКР Самолёт", "самолет"),
        ("ЖК «Самолет»", "самолет"),
        ("жк самолёт", "самолет"),
        ("САМОЛЁТ 7", "самолет 7"),
        ("DОГМА ПАРК", "догма парк"),
        ("Догма Парк", "догма парк"),
        ("  Парк   Победы 3 ", "парк победы 3"),
        ("Догма Юкки", "догма юкки"),
    ],
)
def test_complex_name_normalization(raw: str, expected: str) -> None:
    assert normalize_complex_name(raw) == expected


def test_formatting_helpers() -> None:
    assert format_rub(7_000_000) == "7 000 000 ₽"
    assert format_area(Decimal("2.50")) == "2,5 м²"
    assert format_area(Decimal("30.00")) == "30 м²"
