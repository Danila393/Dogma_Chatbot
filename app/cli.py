"""Command line entry point: `python -m app.cli sync | init-db | stats`."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from collections.abc import Sequence

from app.config import Settings, get_settings
from app.db.models import SyncStatus
from app.db.repository import ApartmentRepository, SyncRunRepository
from app.db.session import create_engine, create_session_factory, init_db
from app.exceptions import SourceError
from app.logging_config import configure_logging
from app.parser.client import DogmaApiClient
from app.parser.service import CatalogCollector
from app.services.sync import SyncReport, SyncService
from app.services.text import normalize_text

logger = logging.getLogger("app.cli")

EXIT_CODES = {SyncStatus.SUCCESS: 0, SyncStatus.PARTIAL: 2, SyncStatus.FAILED: 1}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    sync = commands.add_parser("sync", help="Collect apartments from dogma.ru into SQLite")
    sync.add_argument(
        "--city",
        action="append",
        default=None,
        help="City id or name (repeatable). Default: PARSER_CITY_IDS",
    )
    sync.add_argument("--max-pages", type=int, default=None, help="Pages per city (demo limit)")
    sync.add_argument("--max-lots", type=int, default=None, help="Lots per city (demo limit)")
    sync.add_argument("--page-size", type=int, default=None, help="Override PARSER_PAGE_SIZE")

    commands.add_parser("init-db", help="Create database tables")
    commands.add_parser("stats", help="Print database statistics as JSON")
    return parser


async def _resolve_city_ids(
    raw_values: Sequence[str] | None, settings: Settings, collector: CatalogCollector
) -> list[int]:
    if not raw_values:
        return list(settings.parser_city_ids)
    ids: list[int] = []
    names: list[str] = []
    for value in raw_values:
        if value.strip().isdigit():
            ids.append(int(value))
        else:
            names.append(normalize_text(value))
    for city_id in settings.parser_city_ids:
        if not names:
            break
        try:
            info = await collector.fetch_city_info(city_id)
        except SourceError:
            continue
        if normalize_text(info.name) in names:
            names.remove(normalize_text(info.name))
            ids.append(city_id)
    if names:
        raise SystemExit(
            f"Unknown city: {', '.join(names)} (known ids: {settings.parser_city_ids})"
        )
    return list(dict.fromkeys(ids))


async def run_sync(args: argparse.Namespace, settings: Settings) -> int:
    page_size = args.page_size or settings.parser_page_size
    engine = create_engine(settings)
    try:
        await init_db(engine)
        session_factory = create_session_factory(engine)
        async with DogmaApiClient(settings) as client:
            collector = CatalogCollector(
                client,
                site_url=settings.parser_site_url,
                page_size=page_size,
                concurrency=settings.parser_concurrency,
            )
            city_ids = await _resolve_city_ids(args.city, settings, collector)
            report = await SyncService(collector, session_factory).run(
                city_ids, max_pages=args.max_pages, max_lots=args.max_lots
            )
            requests_made = client.requests_made
    finally:
        await engine.dispose()
    print(_format_report(report, requests_made))
    return EXIT_CODES[report.status]


def _format_report(report: SyncReport, requests_made: int) -> str:
    lines = [
        f"Sync run #{report.run_id}: {report.status.value} ({requests_made} HTTP requests)",
        f"{'city':<26}{'fetched':>9}{'new':>8}{'updated':>9}{'same':>8}{'removed':>9}  complete",
    ]
    for city in report.cities:
        name = city.city or f"id={city.city_id}"
        complete = "yes" if city.complete else "no"
        error = f"  error: {city.error[:120]}" if city.error else ""
        lines.append(
            f"{name:<26}{city.fetched:>9}{city.stats.created:>8}{city.stats.updated:>9}"
            f"{city.stats.unchanged:>8}{city.marked_unavailable:>9}  {complete}{error}"
        )
    lines.append(
        f"{'TOTAL':<26}{report.fetched:>9}{report.created:>8}{report.updated:>9}"
        f"{report.unchanged:>8}{report.marked_unavailable:>9}"
    )
    return "\n".join(lines)


async def run_init_db(settings: Settings) -> int:
    engine = create_engine(settings)
    try:
        await init_db(engine)
    finally:
        await engine.dispose()
    print("Database initialised")
    return 0


async def run_stats(settings: Settings) -> int:
    engine = create_engine(settings)
    try:
        await init_db(engine)
        async with create_session_factory(engine)() as session:
            repository = ApartmentRepository(session)
            by_status = await repository.count_by_status()
            complexes = await repository.list_complexes()
            last_run = await SyncRunRepository(session).latest()
    finally:
        await engine.dispose()
    payload = {
        "apartments_by_status": by_status,
        "complexes": [
            {"name": c.name, "city": c.city, "available": c.available_count} for c in complexes
        ],
        "last_sync": None
        if last_run is None
        else {
            "id": last_run.id,
            "status": last_run.status,
            "started_at": last_run.started_at.isoformat(),
            "finished_at": last_run.finished_at.isoformat() if last_run.finished_at else None,
            "fetched": last_run.fetched,
            "created": last_run.created,
            "updated": last_run.updated,
            "marked_unavailable": last_run.marked_unavailable,
        },
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    configure_logging(settings.log_level)
    if args.command == "sync":
        return asyncio.run(run_sync(args, settings))
    if args.command == "init-db":
        return asyncio.run(run_init_db(settings))
    return asyncio.run(run_stats(settings))


if __name__ == "__main__":
    sys.exit(main())
