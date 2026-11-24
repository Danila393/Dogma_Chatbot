"""LLM-independent apartment search: resolve names, query the DB, rank, explain."""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel

from app.db.models import ApartmentStatus
from app.db.repository import ApartmentQuery, ApartmentRepository, SyncRunRepository
from app.schemas.apartment import (
    ApartmentOut,
    ComplexListResponse,
    ComplexOut,
    DataFreshness,
    RankedApartment,
)
from app.schemas.search import SORT_DESCRIPTIONS, ApartmentSearchFilters
from app.services.complexes import resolve_city, resolve_complex
from app.services.explanation import explain
from app.services.text import plural


@dataclass(slots=True)
class SearchResult:
    filters: ApartmentSearchFilters  # filters as applied (resolved names, effective limit)
    total: int
    items: list[RankedApartment]
    freshness: DataFreshness
    notes: list[str] = field(default_factory=list)

    @property
    def message(self) -> str:
        return build_search_message(self)


class SearchService:
    def __init__(
        self,
        apartments: ApartmentRepository,
        sync_runs: SyncRunRepository,
        *,
        default_limit: int,
        max_limit: int,
    ) -> None:
        self._apartments = apartments
        self._sync_runs = sync_runs
        self._default_limit = default_limit
        self._max_limit = max_limit

    async def search(self, filters: ApartmentSearchFilters) -> SearchResult:
        notes: list[str] = []
        updates: dict[str, object] = {}
        complex_key: str | None = None
        if filters.complex_name is not None:
            names = [c.name for c in await self._apartments.list_complexes()]
            resolution = resolve_complex(filters.complex_name, names)
            complex_key = resolution.key
            updates["complex_name"] = resolution.value
            if not resolution.exact:
                notes.append(
                    f"ЖК «{filters.complex_name}» интерпретирован как «{resolution.value}»."
                )
        city: str | None = None
        if filters.city is not None:
            city_resolution = resolve_city(filters.city, await self._apartments.list_cities())
            city = city_resolution.value
            updates["city"] = city

        limit = min(filters.limit or self._default_limit, self._max_limit)
        updates["limit"] = limit
        applied = filters.model_copy(update=updates)

        total, rows = await self._apartments.search(
            ApartmentQuery(
                min_price=filters.min_price,
                max_price=filters.max_price,
                min_area=filters.min_area,
                max_area=filters.max_area,
                complex_name_normalized=complex_key,
                rooms=filters.rooms,
                city=city,
                min_price_per_sqm=filters.min_price_per_sqm,
                max_price_per_sqm=filters.max_price_per_sqm,
                min_floor=filters.min_floor,
                max_floor=filters.max_floor,
                sort_by=filters.sort_by,
                limit=limit,
            )
        )
        items = []
        for rank, row in enumerate(rows, start=1):
            base = ApartmentOut.model_validate(row)
            items.append(
                RankedApartment(
                    **base.model_dump(),
                    rank=rank,
                    explanation=explain(base, applied, rank=rank, total=total),
                )
            )
        return SearchResult(
            filters=applied,
            total=total,
            items=items,
            freshness=await self.freshness(),
            notes=notes,
        )

    async def list_complexes(self) -> ComplexListResponse:
        complexes = await self._apartments.list_complexes()
        return ComplexListResponse(
            items=[
                ComplexOut(
                    name=c.name,
                    city=c.city,
                    available_apartments=c.available_count,
                    min_price=c.min_price,
                    max_price=c.max_price,
                )
                for c in complexes
            ],
            data_freshness=await self.freshness(),
        )

    async def complex_names(self) -> list[str]:
        return [c.name for c in await self._apartments.list_complexes()]

    async def city_names(self) -> list[str]:
        return await self._apartments.list_cities()

    async def freshness(self) -> DataFreshness:
        run = await self._sync_runs.last_with_data()
        counts = await self._apartments.count_by_status()
        return DataFreshness(
            last_synced_at=run.finished_at if run else None,
            last_sync_status=run.status if run else None,
            available_apartments=counts.get(ApartmentStatus.AVAILABLE.value, 0),
        )


def build_search_message(result: SearchResult) -> str:
    """User-facing summary built only from the search result."""
    prefix = " ".join(result.notes)
    if result.freshness.available_apartments == 0:
        text = (
            "База квартир пока пуста: запустите синхронизацию "
            "(python -m app.cli sync), затем повторите поиск."
        )
    elif result.total == 0:
        text = "Подходящих квартир не найдено. " + _relaxation_hint(result.filters)
    else:
        shown = len(result.items)
        found = plural(
            result.total, "подходящая квартира", "подходящие квартиры", "подходящих квартир"
        )
        text = f"Нашлось {result.total} {found}. "
        if shown < result.total:
            text += f"Показываю {shown} лучших, {SORT_DESCRIPTIONS[result.filters.sort_by]}."
        else:
            text += f"Показываю все, {SORT_DESCRIPTIONS[result.filters.sort_by]}."
    return f"{prefix} {text}".strip()


def _relaxation_hint(filters: BaseModel) -> str:
    data = filters.model_dump(exclude_none=True)
    hints: list[str] = []
    if "max_price" in data:
        hints.append("увеличить бюджет")
    if "min_price" in data:
        hints.append("снизить минимальную цену")
    if "min_area" in data:
        hints.append("уменьшить минимальную площадь")
    if "max_area" in data:
        hints.append("увеличить максимальную площадь")
    if "rooms" in data:
        hints.append("изменить количество комнат")
    if "complex_name" in data:
        hints.append("рассмотреть другой ЖК")
    if "max_price_per_sqm" in data:
        hints.append("увеличить допустимую цену за м²")
    if "min_price_per_sqm" in data:
        hints.append("снизить минимальную цену за м²")
    if "min_floor" in data or "max_floor" in data:
        hints.append("расширить диапазон этажей")
    if not hints:
        return "Попробуйте изменить параметры поиска."
    return "Условия не ослабляются автоматически — попробуйте " + ", ".join(hints) + "."
