"""SQLAlchemy ORM models."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import Date, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.db.types import ScaledDecimal, UTCDateTime


def utcnow() -> datetime:
    return datetime.now(UTC)


class ApartmentStatus(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


class SyncStatus(StrEnum):
    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


class Base(DeclarativeBase):
    pass


class Apartment(Base):
    __tablename__ = "apartments"
    __table_args__ = (
        UniqueConstraint("external_id", name="uq_apartments_external_id"),
        Index("ix_apartments_search", "status", "complex_name_normalized", "rooms", "price"),
        Index("ix_apartments_status_price", "status", "price"),
        Index("ix_apartments_status_area", "status", "area"),
        Index("ix_apartments_status_ppsm", "status", "price_per_square_meter"),
        Index("ix_apartments_city_status", "city_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    external_id: Mapped[str] = mapped_column(String(32))
    city_id: Mapped[int] = mapped_column(Integer)
    city: Mapped[str] = mapped_column(String(128))
    complex_id: Mapped[int | None] = mapped_column(Integer)
    complex_name: Mapped[str] = mapped_column(String(255))
    complex_name_normalized: Mapped[str] = mapped_column(String(255))
    building: Mapped[str | None] = mapped_column(String(64))
    address: Mapped[str | None] = mapped_column(String(512))
    price: Mapped[int] = mapped_column(Integer)
    old_price: Mapped[int | None] = mapped_column(Integer)
    price_per_square_meter: Mapped[int] = mapped_column(Integer)
    area: Mapped[Decimal] = mapped_column(ScaledDecimal(2))
    rooms: Mapped[int] = mapped_column(Integer)
    floor: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), default=ApartmentStatus.AVAILABLE.value)
    source_status: Mapped[int] = mapped_column(Integer)
    url: Mapped[str] = mapped_column(String(512))
    layout_url: Mapped[str | None] = mapped_column(String(1024))
    completion_date: Mapped[date | None] = mapped_column(Date)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class SyncRun(Base):
    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    status: Mapped[str] = mapped_column(String(16), default=SyncStatus.RUNNING.value)
    scope: Mapped[str] = mapped_column(String(255), default="")
    fetched: Mapped[int] = mapped_column(Integer, default=0)
    created: Mapped[int] = mapped_column(Integer, default=0)
    updated: Mapped[int] = mapped_column(Integer, default=0)
    unchanged: Mapped[int] = mapped_column(Integer, default=0)
    marked_unavailable: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[str | None] = mapped_column(Text)
