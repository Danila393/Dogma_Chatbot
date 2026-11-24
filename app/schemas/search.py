"""Search filter schema shared by the structured API, the search service and the AI agent."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_PRICE_RUB = 10_000_000_000
MAX_AREA_M2 = Decimal(1000)
MAX_LIMIT = 10


class SortBy(StrEnum):
    PRICE_ASC = "price_asc"
    AREA_DESC = "area_desc"
    PRICE_PER_SQM_ASC = "price_per_sqm_asc"


SORT_DESCRIPTIONS: dict[SortBy, str] = {
    SortBy.PRICE_ASC: "сначала дешёвые (при равной цене — большая площадь)",
    SortBy.AREA_DESC: "сначала просторные (при равной площади — дешевле)",
    SortBy.PRICE_PER_SQM_ASC: "по минимальной стоимости квадратного метра",
}


class RangeError(ValueError):
    """min > max for one of the ranges; the agent treats it as a contradiction in the request."""


class ApartmentSearchFilters(BaseModel):
    """All filters are optional and combined with AND. Bounds are inclusive."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        json_schema_extra={
            "examples": [
                {
                    "max_price": 7_000_000,
                    "min_area": 30,
                    "complex_name": "Самолёт",
                    "rooms": 0,
                    "limit": 5,
                }
            ]
        },
    )

    min_price: int | None = Field(
        default=None, gt=0, le=MAX_PRICE_RUB, description="Минимальная цена, ₽"
    )
    max_price: int | None = Field(
        default=None, gt=0, le=MAX_PRICE_RUB, description="Максимальная цена (бюджет), ₽"
    )
    min_area: Decimal | None = Field(
        default=None, gt=0, le=MAX_AREA_M2, decimal_places=2, description="Минимальная площадь, м²"
    )
    max_area: Decimal | None = Field(
        default=None, gt=0, le=MAX_AREA_M2, decimal_places=2, description="Максимальная площадь, м²"
    )
    complex_name: str | None = Field(
        default=None, min_length=1, max_length=100, description="Название ЖК"
    )
    rooms: int | None = Field(
        default=None, ge=0, le=10, description="Количество комнат, 0 — студия"
    )
    city: str | None = Field(default=None, min_length=1, max_length=100, description="Город")
    min_floor: int | None = Field(default=None, ge=1, le=200, description="Минимальный этаж")
    max_floor: int | None = Field(default=None, ge=1, le=200, description="Максимальный этаж")
    sort_by: SortBy = Field(default=SortBy.PRICE_ASC, description="Порядок сортировки")
    limit: int | None = Field(
        default=None,
        ge=1,
        le=MAX_LIMIT,
        description="Размер выдачи (по умолчанию 5, максимум 10)",
    )

    @field_validator("min_area", "max_area", mode="before")
    @classmethod
    def _round_area(cls, value: object) -> object:
        if isinstance(value, float | int | str) and not isinstance(value, bool):
            return Decimal(str(value)).quantize(Decimal("0.01"))
        return value

    @model_validator(mode="after")
    def _check_ranges(self) -> Self:
        pairs = (
            ("min_price", "max_price", "цена"),
            ("min_area", "max_area", "площадь"),
            ("min_floor", "max_floor", "этаж"),
        )
        for low_name, high_name, label in pairs:
            low, high = getattr(self, low_name), getattr(self, high_name)
            if low is not None and high is not None and low > high:
                raise RangeError(
                    f"Минимальное значение больше максимального ({label}): {low} > {high}"
                )
        return self

    def has_constraints(self) -> bool:
        return any(
            value is not None
            for value in (
                self.min_price,
                self.max_price,
                self.min_area,
                self.max_area,
                self.complex_name,
                self.rooms,
                self.city,
                self.min_floor,
                self.max_floor,
            )
        )
