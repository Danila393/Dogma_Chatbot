"""Custom column types."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import DateTime, Dialect, Integer
from sqlalchemy.types import TypeDecorator


class ScaledDecimal(TypeDecorator[Decimal]):
    """Exact fixed-point Decimal stored as an INTEGER of `10**-scale` units.

    SQLite has no real DECIMAL type (NUMERIC is stored as REAL), which would make range
    comparisons such as `area >= 30` subject to floating-point error. Storing hundredths
    of a square metre as an integer keeps comparisons and ordering exact.
    """

    impl = Integer
    cache_ok = True

    def __init__(self, scale: int = 2) -> None:
        super().__init__()
        self.scale = scale
        self._factor = Decimal(10) ** scale
        self._quantum = Decimal(1).scaleb(-scale)

    def process_bind_param(
        self, value: Decimal | int | float | None, dialect: Dialect
    ) -> int | None:
        if value is None:
            return None
        decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
        return int((decimal_value * self._factor).to_integral_value(rounding=ROUND_HALF_UP))

    def process_result_value(self, value: int | None, dialect: Dialect) -> Decimal | None:
        if value is None:
            return None
        return (Decimal(value) / self._factor).quantize(self._quantum)


class UTCDateTime(TypeDecorator[datetime]):
    """Timezone-aware UTC datetimes on top of SQLite's naive storage."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime is not allowed, use UTC-aware values")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC)
