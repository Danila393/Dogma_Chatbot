"""Render API responses as Telegram HTML messages."""

from __future__ import annotations

from html import escape
from typing import Any

TELEGRAM_LIMIT = 4096


def _rub(value: int) -> str:
    return f"{value:,}".replace(",", " ") + " ₽"


def _area(value: float) -> str:
    return f"{value:g}".replace(".", ",") + " м²"


def _rooms(rooms: int) -> str:
    return "Студия" if rooms == 0 else f"{rooms}-комн."


def format_chat_response(payload: dict[str, Any]) -> str:
    """Everything shown comes from the API payload; nothing is computed or invented here."""
    parts = [escape(str(payload.get("message", "")))]
    for item in payload.get("items", []):
        price = _rub(int(item["price"]))
        if item.get("old_price"):
            price += f" <s>{_rub(int(item['old_price']))}</s>"
        name = escape(item["complex_name"])
        title = (
            f"<b>{item['rank']}. {name}</b> — {_rooms(int(item['rooms']))}, "
            f"{_area(float(item['area']))}, {item['floor']} этаж"
        )
        parts.append(
            f"{title}\n{price}\n{escape(item['explanation'])}\n"
            f'<a href="{escape(item["url"], quote=True)}">Открыть лот на dogma.ru</a>'
        )
    candidates = payload.get("candidates") or []
    if candidates:
        parts.append("Варианты: " + ", ".join(escape(c) for c in candidates))
    freshness = payload.get("data_freshness") or {}
    if freshness.get("last_synced_at"):
        parts.append(f"<i>Данные обновлены: {escape(freshness['last_synced_at'][:16])} UTC</i>")
    return _fit("\n\n".join(p for p in parts if p))


def _fit(text: str) -> str:
    if len(text) <= TELEGRAM_LIMIT:
        return text
    cut = text.rfind("\n\n", 0, TELEGRAM_LIMIT - 20)
    return text[: cut if cut > 0 else TELEGRAM_LIMIT - 20] + "\n\n…"
