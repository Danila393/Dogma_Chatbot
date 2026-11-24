"""Synchronisation: collect the catalog from the source and persist it safely."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import SyncRun, SyncStatus, utcnow
from app.db.repository import ApartmentRepository, SyncRunRepository, UpsertStats
from app.exceptions import SourceError
from app.parser.service import CatalogCollector

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class CitySyncResult:
    city_id: int
    city: str | None = None
    fetched: int = 0
    stats: UpsertStats = field(default_factory=UpsertStats)
    marked_unavailable: int = 0
    complete: bool = False
    persisted: bool = False
    error: str | None = None


@dataclass(slots=True)
class SyncReport:
    run_id: int
    status: SyncStatus
    cities: list[CitySyncResult]

    @property
    def fetched(self) -> int:
        return sum(c.fetched for c in self.cities)

    @property
    def created(self) -> int:
        return sum(c.stats.created for c in self.cities)

    @property
    def updated(self) -> int:
        return sum(c.stats.updated for c in self.cities)

    @property
    def unchanged(self) -> int:
        return sum(c.stats.unchanged for c in self.cities)

    @property
    def marked_unavailable(self) -> int:
        return sum(c.marked_unavailable for c in self.cities)


class SyncService:
    def __init__(
        self,
        collector: CatalogCollector,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._collector = collector
        self._session_factory = session_factory

    async def run(
        self,
        city_ids: list[int],
        *,
        max_pages: int | None = None,
        max_lots: int | None = None,
    ) -> SyncReport:
        run_id = await self._start_run(city_ids)
        results: list[CitySyncResult] = []
        for city_id in city_ids:
            result = await self._sync_city(city_id, max_pages=max_pages, max_lots=max_lots)
            results.append(result)

        status = self._overall_status(results)
        report = SyncReport(run_id=run_id, status=status, cities=results)
        await self._finish_run(report)
        logger.info(
            "sync finished",
            extra={
                "run_id": run_id,
                "status": status.value,
                "fetched": report.fetched,
                "created": report.created,
                "updated": report.updated,
                "unchanged": report.unchanged,
                "marked_unavailable": report.marked_unavailable,
            },
        )
        return report

    async def _sync_city(
        self, city_id: int, *, max_pages: int | None, max_lots: int | None
    ) -> CitySyncResult:
        result = CitySyncResult(city_id=city_id)
        started_at = utcnow()
        try:
            collection = await self._collector.collect_city(
                city_id, max_pages=max_pages, max_lots=max_lots
            )
        except SourceError as exc:
            result.error = str(exc)
            logger.error("city sync failed", extra={"city_id": city_id, "error": str(exc)})
            return result

        result.city = collection.city.name
        result.fetched = len(collection.apartments)
        result.complete = collection.complete
        if collection.errors:
            result.error = "; ".join(collection.errors)[:2000]

        # One short transaction per city: either the whole city is stored or nothing changes.
        try:
            async with self._session_factory() as session, session.begin():
                repository = ApartmentRepository(session)
                result.stats = await repository.upsert_many(
                    collection.apartments, seen_at=started_at
                )
                if collection.complete:
                    result.marked_unavailable = await repository.mark_missing_unavailable(
                        city_id=city_id,
                        seen_external_ids=collection.seen_external_ids,
                        now=started_at,
                    )
                else:
                    logger.warning(
                        "incomplete collection: availability of missing lots left unchanged",
                        extra={"city_id": city_id, "limited": collection.limited},
                    )
            result.persisted = True
        except Exception as exc:
            logger.exception("failed to persist city", extra={"city_id": city_id})
            result.stats = UpsertStats()
            result.marked_unavailable = 0
            result.complete = False
            result.error = f"database error: {type(exc).__name__}"
        return result

    @staticmethod
    def _overall_status(results: list[CitySyncResult]) -> SyncStatus:
        if results and all(r.complete and r.persisted and r.error is None for r in results):
            return SyncStatus.SUCCESS
        if any(r.persisted and r.fetched for r in results):
            return SyncStatus.PARTIAL
        return SyncStatus.FAILED

    async def _start_run(self, city_ids: list[int]) -> int:
        async with self._session_factory() as session, session.begin():
            run = await SyncRunRepository(session).start(",".join(map(str, city_ids)))
            return run.id

    async def _finish_run(self, report: SyncReport) -> None:
        errors = [f"city {c.city_id}: {c.error}" for c in report.cities if c.error]
        async with self._session_factory() as session, session.begin():
            run = await session.get(SyncRun, report.run_id)
            if run is None:
                return
            run.finished_at = utcnow()
            run.status = report.status.value
            run.fetched = report.fetched
            run.created = report.created
            run.updated = report.updated
            run.unchanged = report.unchanged
            run.marked_unavailable = report.marked_unavailable
            run.errors = "\n".join(errors)[:10_000] or None
