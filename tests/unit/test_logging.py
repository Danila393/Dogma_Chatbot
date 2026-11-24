import json
import logging

from app.logging_config import JsonFormatter


def _format(**extra: object) -> dict[str, object]:
    record = logging.getLogger("t").makeRecord(
        "t", logging.INFO, __file__, 1, "hello %s", ("world",), None, extra=extra
    )
    result: dict[str, object] = json.loads(JsonFormatter().format(record))
    return result


def test_json_line_with_extra_fields() -> None:
    payload = _format(city_id=3, city="Краснодар")
    assert payload["message"] == "hello world"
    assert payload["city_id"] == 3
    assert payload["city"] == "Краснодар"
    assert payload["level"] == "INFO"


def test_secret_like_fields_are_masked() -> None:
    payload = _format(api_key="sk-123", auth_token="t", password="p")
    assert payload["api_key"] == payload["auth_token"] == payload["password"] == "***"
