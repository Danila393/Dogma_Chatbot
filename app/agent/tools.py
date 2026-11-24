"""Tools exposed to the LLM and strict validation of their arguments."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.agent.provider import ToolSpec
from app.schemas.search import MAX_LIMIT, ApartmentSearchFilters, SortBy

SEARCH_TOOL = "search_apartments"
REPLY_TOOL = "reply_without_search"
MIN_PLAUSIBLE_PRICE = 100_000
MAX_REPLY_LENGTH = 300


class SearchToolArguments(BaseModel):
    """What the model may extract from the user's message. Omit everything not stated."""

    model_config = ConfigDict(extra="ignore")

    min_price: int | None = Field(
        default=None, description="Нижняя граница цены в рублях, целое число (5 млн → 5000000)"
    )
    max_price: int | None = Field(
        default=None,
        description="Верхняя граница цены (бюджет) в рублях, целое число (до 7 млн → 7000000)",
    )
    min_area: float | None = Field(default=None, description="Минимальная площадь, м²")
    max_area: float | None = Field(default=None, description="Максимальная площадь, м²")
    complex_name: str | None = Field(
        default=None,
        description="Название ЖК так, как его назвал пользователь, в именительном падеже, "
        "без слова «ЖК»",
    )
    rooms: int | None = Field(
        default=None,
        description="Количество комнат: студия → 0, однушка → 1, двушка → 2, трёшка → 3",
    )
    city: str | None = Field(default=None, description="Город, если пользователь его назвал")
    min_floor: int | None = Field(default=None, description="Минимальный этаж")
    max_floor: int | None = Field(default=None, description="Максимальный этаж")
    sort_by: Literal["price_asc", "area_desc", "price_per_sqm_asc"] | None = Field(
        default=None,
        description="price_asc — «самая дешёвая»/по умолчанию; area_desc — «самая просторная»; "
        "price_per_sqm_asc — «самый дешёвый квадратный метр»",
    )
    limit: int | None = Field(
        default=None,
        description=f"Сколько вариантов показать, только если пользователь назвал число "
        f"(максимум {MAX_LIMIT})",
    )

    @field_validator("min_price", "max_price")
    @classmethod
    def _price_in_rubles(cls, value: int | None) -> int | None:
        if value is not None and 0 < value < MIN_PLAUSIBLE_PRICE:
            raise ValueError("цена должна быть в рублях целым числом (например, 7 млн = 7000000)")
        return value

    def to_filters(self) -> ApartmentSearchFilters:
        """Convert to the domain filter model (raises pydantic.ValidationError / RangeError)."""
        data = self.model_dump(exclude_none=True)
        for key in ("min_area", "max_area"):
            if key in data:
                data[key] = Decimal(str(round(data[key], 2)))
        if "limit" in data:
            data["limit"] = max(1, min(int(data["limit"]), MAX_LIMIT))
        if "sort_by" in data:
            data["sort_by"] = SortBy(data["sort_by"])
        return ApartmentSearchFilters(**data)


class ReplyToolArguments(BaseModel):
    model_config = ConfigDict(extra="ignore")

    reason: Literal["off_topic", "clarification_needed", "contradiction"] = Field(
        description="off_topic — запрос не о подборе квартир; clarification_needed — без "
        "уточнения искать нельзя; contradiction — условия противоречат друг другу"
    )
    message: str = Field(
        min_length=1,
        description="Короткий вопрос или пояснение для пользователя (без цен, квартир и ссылок)",
    )

    @field_validator("message")
    @classmethod
    def _truncate(cls, value: str) -> str:
        value = value.strip()
        return value if len(value) <= MAX_REPLY_LENGTH else value[: MAX_REPLY_LENGTH - 1] + "…"


def _simplify_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Flatten `anyOf: [T, null]` produced for Optional fields; drop titles.

    Optional means "omit the key", which every tool-calling provider understands, while
    `anyOf` with null is rejected by some OpenAI-compatible backends.
    """
    properties: dict[str, Any] = {}
    for name, prop in schema.get("properties", {}).items():
        prop = {k: v for k, v in prop.items() if k not in ("title", "default")}
        variants = prop.pop("anyOf", None)
        if variants:
            non_null = [v for v in variants if v.get("type") != "null"]
            if len(non_null) == 1:
                prop.update(non_null[0])
        properties[name] = prop
    result: dict[str, Any] = {"type": "object", "properties": properties}
    required = schema.get("required")
    if required:
        result["required"] = required
    return result


TOOLS: list[ToolSpec] = [
    ToolSpec(
        name=SEARCH_TOOL,
        description="Найти квартиры DOGMA в базе по параметрам из запроса пользователя. "
        "Передавай только условия, которые пользователь явно назвал.",
        parameters=_simplify_schema(SearchToolArguments.model_json_schema()),
    ),
    ToolSpec(
        name=REPLY_TOOL,
        description="Ответить без поиска: запрос не о квартирах, нужно уточнение или условия "
        "противоречат друг другу.",
        parameters=_simplify_schema(ReplyToolArguments.model_json_schema()),
    ),
]
