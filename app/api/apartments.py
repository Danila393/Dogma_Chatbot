"""Structured search (no LLM) and the complex directory."""

from __future__ import annotations

from fastapi import APIRouter

from app.dependencies import SearchServiceDep
from app.schemas.apartment import ComplexListResponse
from app.schemas.search import ApartmentSearchFilters, SearchResponse

router = APIRouter(tags=["apartments"])

ERROR_RESPONSES: dict[int | str, dict[str, str]] = {
    422: {"description": "Некорректные фильтры или неоднозначное название ЖК/города"},
}


@router.post(
    "/apartments/search",
    response_model=SearchResponse,
    summary="Поиск квартир по параметрам (без LLM)",
    responses=ERROR_RESPONSES,
)
async def search_apartments(
    filters: ApartmentSearchFilters, search: SearchServiceDep
) -> SearchResponse:
    result = await search.search(filters)
    return SearchResponse(
        filters=result.filters,
        total_found=result.total,
        items=result.items,
        message=result.message,
        notes=result.notes,
        data_freshness=result.freshness,
    )


@router.get("/complexes", response_model=ComplexListResponse, summary="Жилые комплексы в базе")
async def list_complexes(search: SearchServiceDep) -> ComplexListResponse:
    return await search.list_complexes()
