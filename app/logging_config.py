"""Structured (JSON lines) logging configuration."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

_RESERVED = set(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {"message"}
_SECRET_MARKERS = ("key", "token", "secret", "password", "authorization")


class JsonFormatter(logging.Formatter):
    """Render records as one JSON object per line; `extra` fields become top-level keys."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in _RESERVED or key.startswith("_"):
                continue
            if any(marker in key.lower() for marker in _SECRET_MARKERS):
                value = "***"
            payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    # Third-party clients log full URLs and headers at INFO/DEBUG; keep them quiet.
    for noisy in ("httpx", "httpcore", "openai", "aiosqlite", "aiogram.event"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
