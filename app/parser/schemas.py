"""Pydantic models of the dogma.ru layouts-filter API (raw source format) and parser outputs."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

# --------------------------------------------------------------------------- raw source models


class _SourceModel(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class RawProject(_SourceModel):
    project_id: int
    project_name: str


class RawCity(_SourceModel):
    city_id: int
    city_name: str
    projects: list[RawProject] = Field(default_factory=list)


class RawConfig(_SourceModel):
    """Payload of POST /config/all (only fields we rely on)."""

    cities: list[RawCity] | None = None
    count_objects: int | None = None


class RawObject(_SourceModel):
    """One lot from POST /objects/filter.

    Only `id` is strictly required here so that one malformed lot does not invalidate the whole
    page; business-level validation of the remaining fields happens in the normalizer.
    """

    id: int
    type: int | None = None
    status: int | None = None
    room: int | None = None
    floor: int | None = None
    area: Decimal | None = None
    cost: int | None = None
    cost_sale: int | None = None
    project_id: int | None = None
    project_name: str | None = None
    letter_name: str | None = None
    address: str | None = None
    layout_url: str | None = None
    construction_deadline: str | None = None


class RawObjectsData(_SourceModel):
    count: int = Field(ge=0)
    objects: list[RawObject] | None = None


class RawObjectsResponse(_SourceModel):
    data: RawObjectsData
    error: object | None = None


# ------------------------------------------------------------------------------ parser outputs


class ApartmentData(BaseModel):
    """A normalized, validated lot ready to be persisted."""

    model_config = ConfigDict(frozen=True)

    external_id: str = Field(min_length=1)
    city_id: int
    city: str
    complex_id: int | None
    complex_name: str = Field(min_length=1)
    complex_name_normalized: str = Field(min_length=1)
    building: str | None
    address: str | None
    price: int = Field(gt=0)
    old_price: int | None = Field(default=None, gt=0)
    price_per_square_meter: int = Field(gt=0)
    area: Decimal = Field(gt=0, max_digits=8, decimal_places=2)
    rooms: int = Field(ge=0)
    floor: int
    source_status: int
    url: str
    layout_url: str | None
    completion_date: date | None


@dataclass(frozen=True, slots=True)
class CityInfo:
    city_id: int
    name: str
    complexes: dict[int, str]


@dataclass(slots=True)
class CityCollection:
    """Result of collecting one city's catalog."""

    city: CityInfo
    reported_total: int = 0
    apartments: list[ApartmentData] = field(default_factory=list)
    seen_external_ids: set[str] = field(default_factory=set)
    invalid_count: int = 0
    failed_pages: int = 0
    limited: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        """True only when every lot reported by the source was observed."""
        return (
            not self.limited
            and self.failed_pages == 0
            and not self.errors
            and len(self.seen_external_ids) >= self.reported_total
        )
