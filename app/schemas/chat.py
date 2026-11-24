"""Chat endpoint schemas."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

from app.schemas.apartment import DataFreshness, RankedApartment
from app.schemas.search import ApartmentSearchFilters

MAX_MESSAGE_LENGTH = 1000


class ChatRequest(BaseModel):
    message: str = Field(
        min_length=1,
        max_length=MAX_MESSAGE_LENGTH,
        description="Запрос в свободной форме на русском языке",
        examples=["Найди студию в ЖК Самолёт до 7 млн, от 30 квадратов"],
    )

    @field_validator("message")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Сообщение не должно быть пустым")
        return stripped


class ChatStatus(StrEnum):
    OK = "ok"
    NO_RESULTS = "no_results"
    CLARIFICATION_NEEDED = "clarification_needed"
    CONTRADICTION = "contradiction"
    OFF_TOPIC = "off_topic"


class ChatResponse(BaseModel):
    query: str = Field(description="Исходный запрос пользователя")
    status: ChatStatus
    message: str = Field(description="Текстовый ответ пользователю (формируется backend)")
    filters: ApartmentSearchFilters | None = Field(
        description="Распознанные и применённые параметры поиска"
    )
    total_found: int = Field(description="Сколько всего квартир подходит под фильтры")
    items: list[RankedApartment] = Field(description="Топ квартир с объяснениями")
    candidates: list[str] = Field(
        default_factory=list, description="Варианты для уточнения (например, названия ЖК)"
    )
    data_freshness: DataFreshness
