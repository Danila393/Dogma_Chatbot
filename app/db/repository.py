"""Data access layer. All queries are parameterized SQLAlchemy expressions."""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, case, func, literal_column, or_, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement, UnaryExpression

from app.db.models import Apartment, ApartmentStatus, SyncRun, SyncStatus, utcnow
from app.parser.schemas import ApartmentData
from app.schemas.search import SortBy

UPSERT_BATCH_SIZE = 500

# Columns compared to decide whether a lot really changed (and `updated_at` must move).
_TRACKED_FIELDS: tuple[str, ...] = (
    "city_id",
    "city",
    "complex_id",
    "complex_name",
    "complex_name_normalized",
    "building",
    "address",
    "price",
    "old_price",
    "price_per_square_meter",
    "area",
    "rooms",
    "floor",
    "status",
    "source_status",
    "url",
    "layout_url",
    "completion_date",
)


@dataclass(slots=True)
class UpsertStats:
    created: int = 0
    updated: int = 0
    unchanged: int = 0

    def __iadd__(self, other: UpsertStats) -> UpsertStats:
        self.created += other.created
        self.updated += other.updated
        self.unchanged += other.unchanged
        return self


@dataclass(frozen=True, slots=True)
class ApartmentQuery:
    """Search criteria after values were validated and resolved against the database."""

    min_price: int | None = None
    max_price: int | None = None
    min_area: Decimal | None = None
    max_area: Decimal | None = None
    complex_name_normalized: str | None = None
    rooms: int | None = None
    city: str | None = None
    min_floor: int | None = None
    max_floor: int | None = None
    sort_by: SortBy = SortBy.PRICE_ASC
    limit: int = 5


@dataclass(frozen=True, slots=True)
class ComplexSummary:
    name: str
    normalized_name: str
    city: str
    available_count: int
    min_price: int
    max_price: int


_SORTS: dict[SortBy, tuple[UnaryExpression[Any], ...]] = {
    SortBy.PRICE_ASC: (Apartment.price.asc(), Apartment.area.desc(), Apartment.id.asc()),
    SortBy.AREA_DESC: (Apartment.area.desc(), Apartment.price.asc(), Apartment.id.asc()),
    SortBy.PRICE_PER_SQM_ASC: (
        Apartment.price_per_square_meter.asc(),
        Apartment.price.asc(),
        Apartment.id.asc(),
    ),
}


class ApartmentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------ writes (sync only)

    async def upsert_many(
        self, items: Iterable[ApartmentData], *, seen_at: datetime
    ) -> UpsertStats:
        """Insert new lots, update existing ones. Caller controls the transaction."""
        stats = UpsertStats()
        batch: list[ApartmentData] = []
        for item in items:
            batch.append(item)
            if len(batch) >= UPSERT_BATCH_SIZE:
                stats += await self._upsert_batch(batch, seen_at)
                batch = []
        if batch:
            stats += await self._upsert_batch(batch, seen_at)
        return stats

    async def _upsert_batch(self, batch: list[ApartmentData], seen_at: datetime) -> UpsertStats:
        rows = [{**item.model_dump(), "status": ApartmentStatus.AVAILABLE.value} for item in batch]
        existing = await self._load_tracked([row["external_id"] for row in rows])
        stats = UpsertStats()
        for row in rows:
            current = existing.get(row["external_id"])
            if current is None:
                stats.created += 1
            elif any(current[name] != row[name] for name in _TRACKED_FIELDS):
                stats.updated += 1
            else:
                stats.unchanged += 1

        statement = sqlite_insert(Apartment).values(
            [
                {**row, "created_at": seen_at, "updated_at": seen_at, "last_seen_at": seen_at}
                for row in rows
            ]
        )
        excluded = statement.excluded
        changed = or_(
            *(getattr(Apartment, name).is_not(getattr(excluded, name)) for name in _TRACKED_FIELDS)
        )
        statement = statement.on_conflict_do_update(
            index_elements=[Apartment.external_id],
            set_={
                **{name: getattr(excluded, name) for name in _TRACKED_FIELDS},
                "last_seen_at": excluded.last_seen_at,
                "updated_at": case((changed, excluded.updated_at), else_=Apartment.updated_at),
            },
        )
        await self._session.execute(statement)
        return stats

    async def _load_tracked(self, external_ids: list[str]) -> dict[str, dict[str, object]]:
        columns = [getattr(Apartment, name) for name in _TRACKED_FIELDS]
        result = await self._session.execute(
            select(Apartment.external_id, *columns).where(Apartment.external_id.in_(external_ids))
        )
        return {row.external_id: row._asdict() for row in result}

    async def mark_missing_unavailable(
        self, *, city_id: int, seen_external_ids: set[str], now: datetime
    ) -> int:
        """After a confirmed complete sync of a city, flag lots that disappeared from the source."""
        seen = (
            select(literal_column("value"))
            .select_from(func.json_each(json.dumps(sorted(seen_external_ids))))
            .scalar_subquery()
        )
        statement = (
            update(Apartment)
            .where(
                Apartment.city_id == city_id,
                Apartment.status == ApartmentStatus.AVAILABLE.value,
                Apartment.external_id.not_in(seen),
            )
            .values(status=ApartmentStatus.UNAVAILABLE.value, updated_at=now)
        )
        result = await self._session.execute(statement)
        return int(getattr(result, "rowcount", 0) or 0)

    # ------------------------------------------------------------------------------ reads

    async def search(self, query: ApartmentQuery) -> tuple[int, Sequence[Apartment]]:
        conditions = self._conditions(query)
        total = await self._session.scalar(
            select(func.count()).select_from(Apartment).where(*conditions)
        )
        statement = (
            select(Apartment).where(*conditions).order_by(*_SORTS[query.sort_by]).limit(query.limit)
        )
        items = (await self._session.scalars(statement)).all()
        return int(total or 0), items

    @staticmethod
    def _conditions(query: ApartmentQuery) -> list[ColumnElement[bool]]:
        conditions: list[ColumnElement[bool]] = [
            Apartment.status == ApartmentStatus.AVAILABLE.value
        ]
        if query.min_price is not None:
            conditions.append(Apartment.price >= query.min_price)
        if query.max_price is not None:
            conditions.append(Apartment.price <= query.max_price)
        if query.min_area is not None:
            conditions.append(Apartment.area >= query.min_area)
        if query.max_area is not None:
            conditions.append(Apartment.area <= query.max_area)
        if query.complex_name_normalized is not None:
            conditions.append(Apartment.complex_name_normalized == query.complex_name_normalized)
        if query.rooms is not None:
            conditions.append(Apartment.rooms == query.rooms)
        if query.city is not None:
            conditions.append(Apartment.city == query.city)
        if query.min_floor is not None:
            conditions.append(Apartment.floor >= query.min_floor)
        if query.max_floor is not None:
            conditions.append(Apartment.floor <= query.max_floor)
        return [and_(*conditions)]

    async def list_complexes(self) -> list[ComplexSummary]:
        statement = (
            select(
                Apartment.complex_name,
                Apartment.complex_name_normalized,
                Apartment.city,
                func.count().label("available_count"),
                func.min(Apartment.price).label("min_price"),
                func.max(Apartment.price).label("max_price"),
            )
            .where(Apartment.status == ApartmentStatus.AVAILABLE.value)
            .group_by(Apartment.complex_name_normalized, Apartment.complex_name, Apartment.city)
            .order_by(Apartment.city, Apartment.complex_name)
        )
        result = await self._session.execute(statement)
        return [
            ComplexSummary(
                name=row.complex_name,
                normalized_name=row.complex_name_normalized,
                city=row.city,
                available_count=int(row.available_count),
                min_price=int(row.min_price),
                max_price=int(row.max_price),
            )
            for row in result
        ]

    async def list_cities(self) -> list[str]:
        result = await self._session.scalars(
            select(Apartment.city)
            .where(Apartment.status == ApartmentStatus.AVAILABLE.value)
            .distinct()
            .order_by(Apartment.city)
        )
        return list(result)

    async def count_by_status(self) -> dict[str, int]:
        result = await self._session.execute(
            select(Apartment.status, func.count()).group_by(Apartment.status)
        )
        return {str(row[0]): int(row[1]) for row in result}

    async def get_by_external_id(self, external_id: str) -> Apartment | None:
        return await self._session.scalar(
            select(Apartment)
            .where(Apartment.external_id == external_id)
            .execution_options(populate_existing=True)
        )


class SyncRunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def start(self, scope: str) -> SyncRun:
        run = SyncRun(scope=scope, status=SyncStatus.RUNNING.value, started_at=utcnow())
        self._session.add(run)
        await self._session.flush()
        return run

    async def last_with_data(self) -> SyncRun | None:
        """Latest finished run that stored data (success or partial)."""
        return await self._session.scalar(
            select(SyncRun)
            .where(SyncRun.status.in_([SyncStatus.SUCCESS.value, SyncStatus.PARTIAL.value]))
            .order_by(SyncRun.finished_at.desc(), SyncRun.id.desc())
            .limit(1)
        )

    async def latest(self) -> SyncRun | None:
        return await self._session.scalar(select(SyncRun).order_by(SyncRun.id.desc()).limit(1))
