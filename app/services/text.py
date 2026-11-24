"""Text helpers shared by the parser and the search layer."""

from __future__ import annotations

import re
from decimal import Decimal

_COMPLEX_PREFIXES = ("жилой комплекс", "микрорайон", "квартал", "мкр.", "мкр", "жк")
_QUOTES = "«»\"'“”„`"
_SPACES = re.compile(r"\s+")


def normalize_text(value: str) -> str:
    """Case-insensitive, ё/е-insensitive form with collapsed whitespace."""
    lowered = value.casefold().replace("ё", "е")
    return _SPACES.sub(" ", lowered).strip()


def normalize_complex_name(value: str) -> str:
    """Canonical search key for a residential complex name.

    "ЖК «Самолёт»", "мкр Самолет" and "МКР Самолёт" all map to "самолет";
    "САМОЛЁТ 7" maps to "самолет 7" and stays distinct. The Latin "D" in the
    brand name "DОГМА" is mapped to the Cyrillic "д" so users can type it either way.
    """
    text = normalize_text(value)
    for quote in _QUOTES:
        text = text.replace(quote, " ")
    text = _SPACES.sub(" ", text).strip()
    changed = True
    while changed:
        changed = False
        for prefix in _COMPLEX_PREFIXES:
            if text == prefix:
                continue
            if text.startswith(prefix + " "):
                text = text[len(prefix) :].strip()
                changed = True
    text = text.replace("dогма", "догма").replace("dogma", "догма")
    return _SPACES.sub(" ", text).strip()


def format_rub(value: int) -> str:
    """12345678 -> '12 345 678 ₽' (non-breaking spaces as thousands separators)."""
    return f"{value:,}".replace(",", " ") + " ₽"


def format_area(value: Decimal) -> str:
    """Decimal('2.50') -> '2,5 м²'."""
    normalized = value.quantize(Decimal("0.01")).normalize()
    text = f"{normalized:f}".replace(".", ",")
    return f"{text} м²"


def rooms_label(rooms: int) -> str:
    if rooms == 0:
        return "студия"
    return f"{rooms}-комнатная"
