"""API representations of apartments, complexes and data freshness."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer

# Decimal is exact internally; JSON clients get a plain number (e.g. 32.3), not a string.
AreaM2 = Annotated[
    Decimal, PlainSerializer(lambda value: float(value), return_type=float, when_used="json")
]


class ApartmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    external_id: str = Field(description="Идентификатор лота на dogma.ru")
    complex_name: str = Field(description="ЖК")
    city: str
    address: str | None
    building: str | None = Field(description="Корпус / литер")
    price: int = Field(description="Цена, ₽")
    old_price: int | None = Field(description="Цена до скидки, ₽")
    price_per_square_meter: int = Field(description="Цена за м², ₽")
    area: AreaM2 = Field(description="Площадь, м²")
    rooms: int = Field(description="Количество комнат, 0 — студия")
    floor: int
    status: str
    url: str = Field(description="Ссылка на лот на dogma.ru")
    layout_url: str | None
    completion_date: date | None = Field(description="Срок сдачи дома по данным источника")
    updated_at: datetime


class RankedApartment(ApartmentOut):
    rank: int = Field(description="Позиция в выдаче, начиная с 1")
    explanation: str = Field(description="Почему квартира подходит под запрос")


class DataFreshness(BaseModel):
    last_synced_at: datetime | None = Field(
        description="Окончание последней синхронизации, сохранившей данные"
    )
    last_sync_status: str | None
    available_apartments: int


class ComplexOut(BaseModel):
    name: str
    city: str
    available_apartments: int
    min_price: int
    max_price: int


class ComplexListResponse(BaseModel):
    items: list[ComplexOut]
    data_freshness: DataFreshness
