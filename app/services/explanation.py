"""Deterministic, data-driven explanations: every number in the text comes from the record."""

from __future__ import annotations

from decimal import Decimal

from app.schemas.apartment import ApartmentOut
from app.schemas.search import ApartmentSearchFilters, SortBy
from app.services.text import format_area, format_rub, plural, rooms_label


def explain(
    apartment: ApartmentOut,
    filters: ApartmentSearchFilters,
    *,
    rank: int,
    total: int,
) -> str:
    reasons = _constraint_reasons(apartment, filters)
    if reasons:
        text = "Соответствует всем указанным условиям: " + ", ".join(reasons) + "."
    else:
        text = "Ограничений в запросе нет, квартира доступна к покупке."
    text += " " + _ranking_reason(apartment, filters.sort_by, rank=rank, total=total)
    if apartment.old_price is not None and apartment.old_price > apartment.price:
        text += f" Скидка {format_rub(apartment.old_price - apartment.price)} от прежней цены."
    return text


def _constraint_reasons(apartment: ApartmentOut, filters: ApartmentSearchFilters) -> list[str]:
    reasons: list[str] = []
    if filters.complex_name is not None:
        reasons.append(f"находится в ЖК «{apartment.complex_name}»")
    if filters.city is not None:
        reasons.append(f"город {apartment.city}")
    if filters.rooms is not None:
        reasons.append(f"{rooms_label(apartment.rooms)}, как в запросе")

    price = _price_reason(apartment.price, filters.min_price, filters.max_price)
    if price:
        reasons.append(price)
    area = _area_reason(apartment.area, filters.min_area, filters.max_area)
    if area:
        reasons.append(area)
    if filters.min_floor is not None or filters.max_floor is not None:
        reasons.append(f"{apartment.floor} этаж в заданном диапазоне")
    return reasons


def _price_reason(price: int, low: int | None, high: int | None) -> str | None:
    shown = format_rub(price)
    if low is not None and high is not None:
        return f"цена {shown} в диапазоне {format_rub(low)} – {format_rub(high)}"
    if high is not None:
        diff = high - price
        if diff == 0:
            return f"стоит ровно {shown} — по верхней границе бюджета"
        return f"стоит {shown} — на {format_rub(diff)} меньше максимального бюджета"
    if low is not None:
        diff = price - low
        if diff == 0:
            return f"стоит {shown} — ровно по нижней границе цены"
        return f"стоит {shown} — на {format_rub(diff)} больше минимальной цены"
    return None


def _area_reason(area: Decimal, low: Decimal | None, high: Decimal | None) -> str | None:
    shown = format_area(area)
    if low is not None and high is not None:
        return f"площадь {shown} в диапазоне {format_area(low)} – {format_area(high)}"
    if low is not None:
        diff = area - low
        if diff == 0:
            return f"площадь {shown} — ровно минимальная"
        return f"площадь {shown} — на {format_area(diff)} больше минимальной"
    if high is not None:
        diff = high - area
        if diff == 0:
            return f"площадь {shown} — ровно максимальная"
        return f"площадь {shown} — на {format_area(diff)} меньше максимальной"
    return None


def _ranking_reason(apartment: ApartmentOut, sort_by: SortBy, *, rank: int, total: int) -> str:
    noun = plural(total, "подходящего варианта", "подходящих вариантов", "подходящих вариантов")
    pool = f"среди {total} {noun}"
    if sort_by is SortBy.AREA_DESC:
        if rank == 1:
            return f"Самая просторная {pool} ({format_area(apartment.area)})."
        return f"{rank}-я по площади {pool}."
    if sort_by is SortBy.PRICE_PER_SQM_ASC:
        per_sqm = format_rub(apartment.price_per_square_meter)
        if rank == 1:
            return f"Самая низкая цена за м² {pool}: {per_sqm}."
        return f"{rank}-я по цене за м² {pool}: {per_sqm}."
    if rank == 1:
        return f"Самая доступная по цене {pool}."
    return f"{rank}-я по цене {pool}."
