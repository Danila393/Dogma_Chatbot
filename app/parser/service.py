"""Collect a city's catalog from the source: pagination, bounded concurrency, dedup, validation."""

from __future__ import annotations

import asyncio
import logging
import math

from app.exceptions import SourceError
from app.parser.client import DogmaApiClient
from app.parser.normalizer import NormalizationError, normalize_object
from app.parser.schemas import ApartmentData, CityCollection, CityInfo, RawObject

logger = logging.getLogger(__name__)


class CityNotFoundError(SourceError):
    """The source has no catalog for the requested city id."""


class CatalogCollector:
    def __init__(
        self,
        client: DogmaApiClient,
        *,
        site_url: str,
        page_size: int,
        concurrency: int,
    ) -> None:
        self._client = client
        self._site_url = site_url
        self._page_size = page_size
        self._semaphore = asyncio.Semaphore(concurrency)

    async def fetch_city_info(self, city_id: int) -> CityInfo:
        config = await self._client.fetch_config(city_id)
        for city in config.cities or []:
            if city.city_id == city_id:
                return CityInfo(
                    city_id=city.city_id,
                    name=city.city_name.strip(),
                    complexes={p.project_id: p.project_name.strip() for p in city.projects},
                )
        raise CityNotFoundError(f"city_id={city_id} is not present in the source catalog")

    async def collect_city(
        self,
        city_id: int,
        *,
        max_pages: int | None = None,
        max_lots: int | None = None,
    ) -> CityCollection:
        """Fetch every page of a city. Raises SourceError if the city or its first page fails."""
        city = await self.fetch_city_info(city_id)
        collection = CityCollection(city=city)
        by_id: dict[str, ApartmentData] = {}

        first_page = await self._client.fetch_objects(city_id, limit=self._page_size, offset=0)
        collection.reported_total = first_page.count
        first_objects = first_page.objects or []
        self._consume(first_objects, collection, by_id)

        page_size = self._effective_page_size(len(first_objects), first_page.count)
        offsets = list(range(page_size, first_page.count, page_size))
        offsets, limited = self._apply_limits(
            offsets, page_size, first_page.count, max_pages, max_lots
        )
        collection.limited = limited

        results = await asyncio.gather(
            *(self._fetch_page(city_id, offset) for offset in offsets), return_exceptions=True
        )
        for offset, result in zip(offsets, results, strict=True):
            if isinstance(result, BaseException):
                if not isinstance(result, SourceError):
                    raise result
                collection.failed_pages += 1
                collection.errors.append(f"offset={offset}: {result}")
                logger.error(
                    "page fetch failed",
                    extra={"city_id": city_id, "offset": offset, "error": str(result)},
                )
                continue
            self._consume(result, collection, by_id)

        apartments = list(by_id.values())
        if max_lots is not None and len(apartments) > max_lots:
            apartments = apartments[:max_lots]
            collection.limited = True
        collection.apartments = apartments

        log = logger.info if collection.complete else logger.warning
        log(
            "city collected",
            extra={
                "city_id": city_id,
                "city": city.name,
                "reported_total": collection.reported_total,
                "seen": len(collection.seen_external_ids),
                "valid": len(collection.apartments),
                "invalid": collection.invalid_count,
                "failed_pages": collection.failed_pages,
                "limited": collection.limited,
                "complete": collection.complete,
            },
        )
        return collection

    async def _fetch_page(self, city_id: int, offset: int) -> list[RawObject]:
        async with self._semaphore:
            page = await self._client.fetch_objects(city_id, limit=self._page_size, offset=offset)
        return page.objects or []

    def _effective_page_size(self, first_len: int, total: int) -> int:
        """If the server caps the page size below what we asked for, follow the server."""
        if 0 < first_len < self._page_size and first_len < total:
            logger.info(
                "source capped page size",
                extra={"requested": self._page_size, "returned": first_len},
            )
            return first_len
        return self._page_size

    @staticmethod
    def _apply_limits(
        offsets: list[int],
        page_size: int,
        total: int,
        max_pages: int | None,
        max_lots: int | None,
    ) -> tuple[list[int], bool]:
        allowed = len(offsets)
        if max_pages is not None:
            allowed = min(allowed, max(max_pages - 1, 0))
        if max_lots is not None:
            allowed = min(allowed, max(math.ceil(max_lots / page_size) - 1, 0))
        limited = allowed < len(offsets) or (max_lots is not None and max_lots < total)
        return offsets[:allowed], limited

    def _consume(
        self,
        objects: list[RawObject],
        collection: CityCollection,
        by_id: dict[str, ApartmentData],
    ) -> None:
        for raw in objects:
            external_id = str(raw.id)
            collection.seen_external_ids.add(external_id)
            if external_id in by_id:
                continue
            try:
                by_id[external_id] = normalize_object(raw, collection.city, site_url=self._site_url)
            except NormalizationError as exc:
                collection.invalid_count += 1
                logger.warning(
                    "invalid lot skipped",
                    extra={"external_id": exc.external_id, "reason": exc.reason},
                )
