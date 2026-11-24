"""Convert raw source objects into validated `ApartmentData`."""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from pydantic import ValidationError

from app.parser.schemas import ApartmentData, CityInfo, RawObject
from app.services.text import normalize_complex_name

AREA_QUANTUM = Decimal("0.01")
AVAILABLE_SOURCE_STATUS = 2


class NormalizationError(ValueError):
    """A raw object cannot be turned into a valid apartment."""

    def __init__(self, external_id: str, reason: str) -> None:
        super().__init__(f"lot {external_id}: {reason}")
        self.external_id = external_id
        self.reason = reason


def _clean_str(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _parse_date(value: str | None) -> date | None:
    text = _clean_str(value)
    if text is None or text.startswith("0000"):
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _resolve_prices(cost: int | None, cost_sale: int | None) -> tuple[int, int | None]:
    """Return (current price, old price).

    The site shows `cost_sale` as the current price and `cost` crossed out when
    0 < cost_sale < cost (verified on lot /flat/55951).
    """
    if cost is None or cost <= 0:
        raise ValueError("missing or non-positive price")
    if cost_sale is not None and 0 < cost_sale < cost:
        return cost_sale, cost
    return cost, None


def build_lot_url(site_url: str, external_id: str) -> str:
    return f"{site_url.rstrip('/')}/flat/{external_id}"


def normalize_object(raw: RawObject, city: CityInfo, *, site_url: str) -> ApartmentData:
    """Validate and normalize one lot. Raises NormalizationError on missing required data."""
    external_id = str(raw.id)

    complex_name = _clean_str(raw.project_name)
    if complex_name is None and raw.project_id is not None:
        complex_name = city.complexes.get(raw.project_id)
    if complex_name is None:
        raise NormalizationError(external_id, "missing complex name")
    if raw.room is None or raw.room < 0:
        raise NormalizationError(external_id, "missing or negative rooms")
    if raw.floor is None:
        raise NormalizationError(external_id, "missing floor")
    if raw.status is None:
        raise NormalizationError(external_id, "missing status")

    try:
        area = (raw.area or Decimal(0)).quantize(AREA_QUANTUM, rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise NormalizationError(external_id, "invalid area") from exc
    if area <= 0:
        raise NormalizationError(external_id, "missing or non-positive area")

    try:
        price, old_price = _resolve_prices(raw.cost, raw.cost_sale)
    except ValueError as exc:
        raise NormalizationError(external_id, str(exc)) from exc

    price_per_sqm = int((Decimal(price) / area).quantize(Decimal(1), rounding=ROUND_HALF_UP))

    try:
        return ApartmentData(
            external_id=external_id,
            city_id=city.city_id,
            city=city.name,
            complex_id=raw.project_id,
            complex_name=complex_name,
            complex_name_normalized=normalize_complex_name(complex_name),
            building=_clean_str(raw.letter_name),
            address=_clean_str(raw.address),
            price=price,
            old_price=old_price,
            price_per_square_meter=price_per_sqm,
            area=area,
            rooms=raw.room,
            floor=raw.floor,
            source_status=raw.status,
            url=build_lot_url(site_url, external_id),
            layout_url=_clean_str(raw.layout_url),
            completion_date=_parse_date(raw.construction_deadline),
        )
    except ValidationError as exc:
        reason = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        raise NormalizationError(external_id, reason) from exc
