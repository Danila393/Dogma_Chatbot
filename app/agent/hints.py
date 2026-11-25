"""Deterministic extraction of explicit numeric constraints from a Russian query.

The LLM interprets the request (complex names, intent, sorting); numbers and room types the
user spelled out explicitly are parsed here, in Python, and win over the model's values.
Small local models regularly turn "9 лямов" into 90 000 000 or "студия" into one room —
this layer makes such errors impossible for constraints stated in plain words.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from app.services.text import format_area, format_rub

_WORD_NUMBERS: dict[str, Decimal] = {}
for _value, _forms in {
    "1": ("один", "одного", "одной", "одна"),
    "1.5": ("полтора", "полутора"),
    "2": ("два", "двух", "две"),
    "2.5": ("двух с половиной",),
    "3": ("три", "трех"),
    "4": ("четыре", "четырех"),
    "5": ("пять", "пяти"),
    "6": ("шесть", "шести"),
    "7": ("семь", "семи"),
    "8": ("восемь", "восьми"),
    "9": ("девять", "девяти"),
    "10": ("десять", "десяти"),
    "11": ("одиннадцать", "одиннадцати"),
    "12": ("двенадцать", "двенадцати"),
    "15": ("пятнадцать", "пятнадцати"),
    "20": ("двадцать", "двадцати"),
    "25": ("двадцать пять", "двадцати пяти"),
    "30": ("тридцать", "тридцати"),
    "40": ("сорок", "сорока"),
    "50": ("пятьдесят", "пятидесяти"),
    "100": ("сто", "ста"),
}.items():
    for _form in _forms:
        _WORD_NUMBERS[_form] = Decimal(_value)

_WORDS_RE = "|".join(sorted((re.escape(w) for w in _WORD_NUMBERS), key=len, reverse=True))
_NUM = rf"(?:\d{{1,3}}(?:[  ]\d{{3}})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?|(?:{_WORDS_RE})\b)"
_MONEY_MILLION = r"(?:млн\.?|миллион\w*|лям\w*|kk|кк)"
_MONEY_THOUSAND = r"(?:тыс\.?|тысяч\w*|косар\w*|[кk](?![а-яёa-z]))"
_SHORT_THOUSAND = r"[кk]"
# "200к" means thousands, but "3к" is how people write a 3-room flat: below this value
# the short form is not trusted as money.
MIN_SHORT_THOUSAND_VALUE = 10
_MONEY_RUB = r"(?:₽|руб\.?|рубл\w*|р\.)"
_AREA = r"(?:м2|м²|кв\.?\s*м\.?|квадрат\w*|метр\w*|кв\.?)"
_UNIT = rf"(?:{_MONEY_MILLION}|{_MONEY_THOUSAND}|{_MONEY_RUB}|{_AREA})"


def _qty(name: str, unit: str) -> str:
    return rf"(?P<{name}>{_NUM})\s*(?P<{unit}>{_UNIT})?"


_MAX_WORDS = r"(?:до|не дороже|не больше|не более|максимум|в пределах|бюджет\w*|дешевле|меньше)"
_MIN_WORDS = r"(?:от|не дешевле|не меньше|не менее|минимум|больше|более|дороже)"

_RANGE_RE = re.compile(
    rf"\bот\s+{_qty('low', 'low_unit')}\s*(?:-|–|—|до)\s*"
    rf"{_qty('high', 'high_unit')}"
)
_BOUND_RE = re.compile(rf"(?<!\w)(?P<kw>{_MAX_WORDS}|{_MIN_WORDS})\s+{_qty('value', 'unit')}")

_ROOM_PATTERNS: tuple[tuple[re.Pattern[str], int], ...] = (
    (re.compile(r"студи"), 0),
    (re.compile(r"однушк|однокомнатн|однокомн"), 1),
    (re.compile(r"двушк|двухкомнатн|двухкомн|двуш"), 2),
    (re.compile(r"трешк|трехкомнатн|трехкомн"), 3),
    (re.compile(r"четырехкомнатн|четырехкомн"), 4),
    (re.compile(r"\b(\d)\s*-?\s*(?:х\s*|ти\s*)?(?:комнатн|комн\b|к\b|комнат)"), -1),
)

MIN_BARE_RUBLES = 100_000
MIN_BARE_RUBLES_PER_SQM = 1_000

# "... 200 тыс за метр", "250000 ₽/м²"
_PER_SQM_AFTER = re.compile(
    r"\s*(?:₽|руб\.?|рубл\w*|р\.)?\s*(?:за|/)\s*(?:1\s*|один\s*|одного\s*)?"
    r"(?:кв\.?\s*м\.?|квадратн\w*\s+метр\w*|м2|м²|м\b|метр\w*|квадрат\w*)"
)
_BARE_SQM_BEFORE = re.compile(
    r"(?P<prev>\S*)\s*(?:кв\.?\s*м\.?|м2|м²|метр|квадрат)(?:\s+(?:стоит|обойдет\w*))?\s*$"
)
# "квадратный метр не дороже ...", "цена метра до ...", "стоимость за м² от ..."
_PER_SQM_BEFORE = re.compile(
    r"(?:квадратн\w*\s+метр\w*|(?:цен\w*|стоимост\w*)\s+(?:за\s+)?(?:1\s+|одного\s+)?"
    r"(?:кв\.?\s*м\.?|квадратн\w*\s+метр\w*|метр\w*|квадрат\w*|м2|м²))"
    r"(?:\s+(?:стоит|обойдет\w*|был\w*|должн\w*\s+быть))?\s*$"
)


@dataclass(slots=True)
class QueryHints:
    rooms: int | None = None
    min_price: int | None = None
    max_price: int | None = None
    min_area: Decimal | None = None
    max_area: Decimal | None = None
    min_price_per_sqm: int | None = None
    max_price_per_sqm: int | None = None
    contradiction: str | None = None
    _conflicts: set[str] = field(default_factory=set)

    def as_overrides(self) -> dict[str, object]:
        values = {
            "rooms": self.rooms,
            "min_price": self.min_price,
            "max_price": self.max_price,
            "min_area": self.min_area,
            "max_area": self.max_area,
            "min_price_per_sqm": self.min_price_per_sqm,
            "max_price_per_sqm": self.max_price_per_sqm,
        }
        return {k: v for k, v in values.items() if v is not None and k not in self._conflicts}

    def _set(self, name: str, value: object) -> None:
        current = getattr(self, name)
        if current is not None and current != value:
            self._conflicts.add(name)  # the text names two different values: trust neither
        setattr(self, name, value)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.casefold().replace("ё", "е")).strip()


def _to_decimal(raw: str) -> Decimal:
    raw = raw.strip()
    if raw in _WORD_NUMBERS:
        return _WORD_NUMBERS[raw]
    return Decimal(raw.replace(" ", "").replace(" ", "").replace(",", "."))


def _kind(unit: str | None) -> str | None:
    if not unit:
        return None
    if re.fullmatch(_AREA, unit):
        return "area"
    return "price"


def _money(value: Decimal, unit: str | None) -> int | None:
    if unit and re.fullmatch(_SHORT_THOUSAND, unit) and value < MIN_SHORT_THOUSAND_VALUE:
        return None
    if unit and re.fullmatch(_MONEY_MILLION, unit):
        value *= 1_000_000
    elif unit and re.fullmatch(_MONEY_THOUSAND, unit):
        value *= 1_000
    elif value < MIN_BARE_RUBLES:
        return None  # "до 7 ₽" or a bare small number: not a reliable price
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _is_per_sqm(text: str, start: int, end: int) -> bool:
    """The money amount is a price of one square metre, not of the whole apartment."""
    before = text[max(0, start - 40) : start]
    if _PER_SQM_AFTER.match(text, end) or _PER_SQM_BEFORE.search(before):
        return True
    # "..., метр не дороже 250 тыс" — a bare "метр" counts only when it is not an area
    # ("от 30 метров до 7 млн" has a number right before "метров").
    bare = _BARE_SQM_BEFORE.search(before)
    if bare is None:
        return False
    previous = bare.group("prev")
    return not any(ch.isdigit() for ch in previous) and previous not in _WORD_NUMBERS


def _money_per_sqm(value: Decimal, unit: str | None) -> int | None:
    if unit and re.fullmatch(_SHORT_THOUSAND, unit) and value < MIN_SHORT_THOUSAND_VALUE:
        return None
    if unit and re.fullmatch(_MONEY_MILLION, unit):
        value *= 1_000_000
    elif unit and re.fullmatch(_MONEY_THOUSAND, unit):
        value *= 1_000
    elif value < MIN_BARE_RUBLES_PER_SQM:
        return None
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _area(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _classify(value: Decimal, unit: str | None, keyword: str = "") -> str | None:
    kind = _kind(unit)
    if kind:
        return kind
    if keyword in ("не дороже", "не дешевле", "дешевле", "дороже") or keyword.startswith("бюджет"):
        return "price"
    if value >= MIN_BARE_RUBLES:
        return "price"
    return None


def extract_hints(text: str) -> QueryHints:
    hints = QueryHints()
    normalized = _normalize(text)

    for pattern, rooms in _ROOM_PATTERNS:
        for match in pattern.finditer(normalized):
            hints._set("rooms", int(match.group(1)) if rooms == -1 else rooms)

    consumed: list[tuple[int, int]] = []
    for match in _RANGE_RE.finditer(normalized):
        low_unit, high_unit = match.group("low_unit"), match.group("high_unit")
        unit_for_kind = high_unit or low_unit
        low, high = _to_decimal(match.group("low")), _to_decimal(match.group("high"))
        kind = _classify(max(low, high), unit_for_kind)
        if low_unit and high_unit and _kind(low_unit) != _kind(high_unit):
            continue  # "от 30 м² до 7 млн" — two different constraints, handled below
        per_sqm = _is_per_sqm(normalized, match.start(), match.end())
        if per_sqm and kind in ("price", None):
            low_sqm = _money_per_sqm(low, low_unit or high_unit)
            high_sqm = _money_per_sqm(high, high_unit or low_unit)
            if low_sqm is None or high_sqm is None:
                continue
            hints._set("min_price_per_sqm", low_sqm)
            hints._set("max_price_per_sqm", high_sqm)
        elif kind == "price":
            low_rub = _money(low, low_unit or high_unit)
            high_rub = _money(high, high_unit or low_unit)
            if low_rub is None or high_rub is None:
                continue
            hints._set("min_price", low_rub)
            hints._set("max_price", high_rub)
        elif kind == "area":
            hints._set("min_area", _area(low))
            hints._set("max_area", _area(high))
        else:
            continue
        consumed.append(match.span())

    for match in _BOUND_RE.finditer(normalized):
        if any(start <= match.start() < end for start, end in consumed):
            continue
        keyword = match.group("kw")
        value, unit = _to_decimal(match.group("value")), match.group("unit")
        is_max = re.fullmatch(_MAX_WORDS, keyword) is not None
        preceding = normalized[max(0, match.start() - 20) : match.start()]
        kind = _classify(value, unit, keyword)
        if kind is None and re.search(r"площад|метраж", preceding):
            kind = "area"
        if kind in ("price", None) and _is_per_sqm(normalized, match.start(), match.end()):
            per_sqm_rub = _money_per_sqm(value, unit)
            if per_sqm_rub is not None:
                hints._set("max_price_per_sqm" if is_max else "min_price_per_sqm", per_sqm_rub)
        elif kind == "price":
            rubles = _money(value, unit)
            if rubles is not None:
                hints._set("max_price" if is_max else "min_price", rubles)
        elif kind == "area":
            hints._set("max_area" if is_max else "min_area", _area(value))

    low_price, high_price = hints.min_price, hints.max_price
    if low_price is not None and high_price is not None and low_price > high_price:
        hints.contradiction = _inverted("цена", format_rub(low_price), format_rub(high_price))
    low_sqm, high_sqm = hints.min_price_per_sqm, hints.max_price_per_sqm
    if low_sqm is not None and high_sqm is not None and low_sqm > high_sqm:
        hints.contradiction = _inverted("цена за м²", format_rub(low_sqm), format_rub(high_sqm))
    low_area, high_area = hints.min_area, hints.max_area
    if low_area is not None and high_area is not None and low_area > high_area:
        hints.contradiction = _inverted("площадь", format_area(low_area), format_area(high_area))
    return hints


def _inverted(label: str, low: str, high: str) -> str:
    return (
        f"Условия противоречат друг другу: минимальное значение больше максимального "
        f"({label}: от {low} до {high}). Уточните, пожалуйста, диапазон."
    )
