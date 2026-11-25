from decimal import Decimal

import pytest

from app.agent.hints import extract_hints


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # every phrasing listed in the assignment
        ("До семи миллионов", {"max_price": 7_000_000}),
        ("До 7 млн", {"max_price": 7_000_000}),
        ("Не дороже 7 лямов", {"max_price": 7_000_000}),
        ("От 30 квадратов", {"min_area": Decimal("30.00")}),
        ("Двушка", {"rooms": 2}),
        ("Однушка", {"rooms": 1}),
        ("Трёшка", {"rooms": 3}),
        ("Студия", {"rooms": 0}),
        ("От 5 до 7 миллионов", {"min_price": 5_000_000, "max_price": 7_000_000}),
        # the queries that a 7B model got wrong in the live run
        (
            "Найди студию в ЖК Самолёт до 7 млн, от 30 квадратов",
            {"rooms": 0, "max_price": 7_000_000, "min_area": Decimal("30.00")},
        ),
        ("Однушка в Рекорд 2 не дороже 9 лямов", {"rooms": 1, "max_price": 9_000_000}),
        # more variants
        ("2-комнатная до 12,5 млн", {"rooms": 2, "max_price": 12_500_000}),
        ("3-х комнатная квартира", {"rooms": 3}),
        ("бюджет 8 000 000 ₽", {"max_price": 8_000_000}),
        ("до 6500000", {"max_price": 6_500_000}),
        ("от 40 до 55 м²", {"min_area": Decimal("40.00"), "max_area": Decimal("55.00")}),
        ("площадью от 45", {"min_area": Decimal("45.00")}),
        ("до 800 тыс", {"max_price": 800_000}),
        ("до полутора миллионов", {"max_price": 1_500_000}),
        ("от 30 м² до 7 млн", {"min_area": Decimal("30.00"), "max_price": 7_000_000}),
        ("от 5 до 7,5 млн", {"min_price": 5_000_000, "max_price": 7_500_000}),
        ("ЖК Самолёт 7 до 9 млн", {"max_price": 9_000_000}),
        # price of one square metre is not the apartment budget
        (
            "Студия, где квадратный метр не дороже 200 тысяч",
            {"rooms": 0, "max_price_per_sqm": 200_000},
        ),
        ("до 700 тыс за метр", {"max_price_per_sqm": 700_000}),
        ("цена метра до 180 тыс", {"max_price_per_sqm": 180_000}),
        ("до 250000 руб за кв.м", {"max_price_per_sqm": 250_000}),
        (
            "от 150 до 200 тыс за м²",
            {"min_price_per_sqm": 150_000, "max_price_per_sqm": 200_000},
        ),
        (
            "двушка до 9 млн, метр не дороже 250 тыс",
            {"rooms": 2, "max_price": 9_000_000, "max_price_per_sqm": 250_000},
        ),
        ("от 30 метров до 7 млн", {"min_area": Decimal("30.00"), "max_price": 7_000_000}),
        ("студия до 7 млн за квартиру", {"rooms": 0, "max_price": 7_000_000}),
        # short thousands: "200к", "800 k"
        ("квадрат не дороже 200к", {"max_price_per_sqm": 200_000}),
        ("до 800к", {"max_price": 800_000}),
        ("до 800 k", {"max_price": 800_000}),
        (
            "двушка до 9 млн, метр до 250к",
            {"rooms": 2, "max_price": 9_000_000, "max_price_per_sqm": 250_000},
        ),
        ("3к квартира до 7 млн", {"rooms": 3, "max_price": 7_000_000}),  # "3к" = 3 rooms
        ("рядом с метро от 7 млн", {"min_price": 7_000_000}),
    ],
)
def test_explicit_constraints(text: str, expected: dict[str, object]) -> None:
    hints = extract_hints(text)
    assert hints.as_overrides() == expected
    assert hints.contradiction is None


@pytest.mark.parametrize(
    "text",
    [
        "Найди квартиру в ЖК Самолёт",
        "самая просторная",
        "на 5 этаже",  # floors are left to the model
        "от 30",  # unit-less small number: ambiguous, leave it to the model
        "квартира за 1 рубль",
        # a price per m² without "до"/"от" has no direction: neither a bound nor a budget
        "250000 ₽/м²",
        "квартиры по 250000 ₽/м²",
    ],
)
def test_nothing_is_invented(text: str) -> None:
    assert extract_hints(text).as_overrides() == {}


def test_inverted_range_is_a_contradiction() -> None:
    hints = extract_hints("Трёшка от 10 до 5 млн")
    assert hints.rooms == 3
    assert hints.contradiction is not None
    assert (
        "цена: от 10\u00a0000\u00a0000\u00a0₽ до 5\u00a0000\u00a0000\u00a0₽" in hints.contradiction
    )


def test_conflicting_values_are_not_trusted() -> None:
    hints = extract_hints("студия или двушка до 7 млн")
    assert hints.as_overrides() == {"max_price": 7_000_000}


def test_inverted_price_per_sqm_range_is_a_contradiction() -> None:
    hints = extract_hints("от 200 до 150 тыс за метр")
    assert hints.contradiction is not None and "цена за м²" in hints.contradiction
