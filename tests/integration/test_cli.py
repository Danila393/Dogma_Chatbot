import json
from pathlib import Path
from typing import Any

import pytest

from app import cli
from app.config import Settings
from app.parser.client import DogmaApiClient
from tests.factories import FakeSource, make_settings, raw_object


@pytest.fixture
def file_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    settings = make_settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'db' / 'apartments.db'}",
        parser_city_ids=(3,),
    )
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    return settings


@pytest.fixture
def fake_source(monkeypatch: pytest.MonkeyPatch) -> FakeSource:
    source = FakeSource([raw_object(i) for i in range(1, 6)])

    class PatchedClient(DogmaApiClient):
        def __init__(self, settings: Settings, **kwargs: Any) -> None:
            async def no_sleep(_: float) -> None:
                return None

            super().__init__(settings, http_client=source.client(settings), sleep=no_sleep)

    monkeypatch.setattr(cli, "DogmaApiClient", PatchedClient)
    return source


def test_sync_then_stats(
    file_settings: Settings, fake_source: FakeSource, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["sync"]) == 0
    output = capsys.readouterr().out
    assert "success" in output
    assert "Краснодар" in output

    assert cli.main(["sync"]) == 0  # idempotent re-run
    second = capsys.readouterr().out
    assert "TOTAL" in second
    total_line = next(line for line in second.splitlines() if line.startswith("TOTAL"))
    assert total_line.split()[1:] == ["5", "0", "0", "5", "0"]  # fetched new updated same removed


def test_stats_json(
    file_settings: Settings, fake_source: FakeSource, capsys: pytest.CaptureFixture[str]
) -> None:
    cli.main(["sync"])
    capsys.readouterr()
    cli.main(["stats"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["apartments_by_status"] == {"available": 5}
    assert payload["last_sync"]["status"] == "success"
    assert payload["complexes"][0]["available"] == 5


def test_demo_limit_returns_partial_exit_code(
    file_settings: Settings, fake_source: FakeSource
) -> None:
    assert cli.main(["sync", "--max-lots", "2", "--page-size", "2"]) == 2


def test_sync_by_city_name(file_settings: Settings, fake_source: FakeSource) -> None:
    assert cli.main(["sync", "--city", "краснодар"]) == 0
    with pytest.raises(SystemExit, match="Unknown city"):
        cli.main(["sync", "--city", "Москва"])


def test_failed_sync_exit_code(file_settings: Settings, fake_source: FakeSource) -> None:
    fake_source.broken_offsets = {0}
    assert cli.main(["sync"]) == 1


def test_init_db_creates_file(file_settings: Settings, tmp_path: Path) -> None:
    assert cli.main(["init-db"]) == 0
    assert (tmp_path / "db" / "apartments.db").exists()
