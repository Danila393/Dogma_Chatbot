import httpx
import pytest

from app.exceptions import SourceError, SourceFormatError
from app.parser.client import DogmaApiClient, RateLimiter
from app.parser.service import CatalogCollector, CityNotFoundError
from tests.factories import FakeSource, load_fixture, make_settings, raw_object


async def _no_sleep(_: float) -> None:
    return None


def _collector(source: FakeSource, **settings_overrides: object) -> CatalogCollector:
    settings = make_settings(**settings_overrides)
    client = DogmaApiClient(settings, http_client=source.client(settings), sleep=_no_sleep)
    return CatalogCollector(
        client,
        site_url=settings.parser_site_url,
        page_size=settings.parser_page_size,
        concurrency=settings.parser_concurrency,
    )


async def test_collects_all_pages() -> None:
    source = FakeSource([raw_object(i) for i in range(1, 8)])
    collection = await _collector(source).collect_city(3)

    assert collection.reported_total == 7
    assert sorted(a.external_id for a in collection.apartments) == [str(i) for i in range(1, 8)]
    assert collection.complete
    offsets = sorted(body["offset"] for path, body in source.calls if "objects" in path)
    assert offsets == [0, 2, 4, 6]
    assert all(body["city_ids"] == [3] for _, body in source.calls)


async def test_follows_server_page_size_cap() -> None:
    source = FakeSource([raw_object(i) for i in range(1, 8)])
    source.max_page_size = 3
    collection = await _collector(source, parser_page_size=5).collect_city(3)

    assert len(collection.apartments) == 7
    assert collection.complete


async def test_duplicates_are_removed() -> None:
    objects = [raw_object(1), raw_object(2), raw_object(2), raw_object(3)]
    collection = await _collector(FakeSource(objects)).collect_city(3)

    assert sorted(a.external_id for a in collection.apartments) == ["1", "2", "3"]
    assert len(collection.seen_external_ids) == 3


async def test_missing_lots_make_collection_incomplete() -> None:
    source = FakeSource([raw_object(i) for i in range(1, 5)])
    source.count_override = 5  # source claims more lots than it actually returns
    collection = await _collector(source).collect_city(3)

    assert len(collection.apartments) == 4
    assert not collection.complete


async def test_invalid_lot_is_skipped_but_counted_as_seen() -> None:
    source = FakeSource([raw_object(1), raw_object(2, cost=0), raw_object(3)])
    collection = await _collector(source).collect_city(3)

    assert sorted(a.external_id for a in collection.apartments) == ["1", "3"]
    assert collection.invalid_count == 1
    assert "2" in collection.seen_external_ids
    assert collection.complete


async def test_transient_errors_are_retried() -> None:
    source = FakeSource([raw_object(i) for i in range(1, 5)])
    source.fail_offsets = {0: 2, 2: 1}
    collection = await _collector(source, parser_max_retries=2).collect_city(3)

    assert collection.complete
    assert len(collection.apartments) == 4


async def test_partial_failure_marks_collection_incomplete() -> None:
    source = FakeSource([raw_object(i) for i in range(1, 7)])
    source.broken_offsets = {2}
    collection = await _collector(source, parser_max_retries=1).collect_city(3)

    assert collection.failed_pages == 1
    assert not collection.complete
    assert sorted(a.external_id for a in collection.apartments) == ["1", "2", "5", "6"]


async def test_first_page_failure_raises() -> None:
    source = FakeSource([raw_object(1)])
    source.broken_offsets = {0}
    with pytest.raises(SourceError):
        await _collector(source, parser_max_retries=1).collect_city(3)


async def test_network_error_is_retried_then_raised() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectError("boom", request=request)

    settings = make_settings(parser_max_retries=2)
    http = httpx.AsyncClient(
        base_url=settings.parser_base_url, transport=httpx.MockTransport(handler)
    )
    client = DogmaApiClient(settings, http_client=http, sleep=_no_sleep)
    with pytest.raises(SourceError, match="3 attempts"):
        await client.fetch_objects(3, limit=10, offset=0)
    assert attempts == 3


async def test_timeout_is_retried() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(200, json=load_fixture("objects_page.json"))

    settings = make_settings()
    http = httpx.AsyncClient(
        base_url=settings.parser_base_url, transport=httpx.MockTransport(handler)
    )
    page = await DogmaApiClient(settings, http_client=http, sleep=_no_sleep).fetch_objects(
        3, limit=10, offset=0
    )
    assert page.count == 12152
    assert calls == 2


async def test_retry_after_header_is_respected() -> None:
    delays: list[float] = []

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    responses = [httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(200, json={})]

    def handler(request: httpx.Request) -> httpx.Response:
        return responses.pop(0)

    settings = make_settings()
    http = httpx.AsyncClient(
        base_url=settings.parser_base_url, transport=httpx.MockTransport(handler)
    )
    with pytest.raises(SourceFormatError):
        await DogmaApiClient(settings, http_client=http, sleep=record_sleep).fetch_objects(
            3, limit=1, offset=0
        )
    assert delays == [7.0]


async def test_client_error_is_not_retried() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(404)

    settings = make_settings()
    http = httpx.AsyncClient(
        base_url=settings.parser_base_url, transport=httpx.MockTransport(handler)
    )
    with pytest.raises(SourceError, match="404"):
        await DogmaApiClient(settings, http_client=http, sleep=_no_sleep).fetch_config(3)
    assert calls == 1


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="<html>maintenance</html>"),
        httpx.Response(200, json={"data": {"objects": []}}),
        httpx.Response(200, json={"error": "bad", "data": {"count": 0, "objects": []}}),
    ],
)
async def test_malformed_source_responses_raise_format_error(response: httpx.Response) -> None:
    settings = make_settings()
    http = httpx.AsyncClient(
        base_url=settings.parser_base_url, transport=httpx.MockTransport(lambda _: response)
    )
    with pytest.raises(SourceFormatError):
        await DogmaApiClient(settings, http_client=http, sleep=_no_sleep).fetch_objects(
            3, limit=1, offset=0
        )


async def test_unknown_city_raises() -> None:
    source = FakeSource([])
    source.config_override = lambda: httpx.Response(200, json={"cities": []})
    with pytest.raises(CityNotFoundError):
        await _collector(source).collect_city(42)


async def test_config_fixture_is_parsed() -> None:
    source = FakeSource([])
    source.config_override = lambda: httpx.Response(200, json=load_fixture("config_krasnodar.json"))
    city = await _collector(source).fetch_city_info(3)
    assert city.name == "Краснодар"
    assert city.complexes[12] == "САМОЛЁТ 7"


@pytest.mark.parametrize(
    ("max_pages", "max_lots", "expected_count", "expected_requests"),
    [(1, None, 2, 1), (2, None, 4, 2), (None, 3, 3, 2), (None, 100, 7, 4)],
)
async def test_demo_limits(
    max_pages: int | None, max_lots: int | None, expected_count: int, expected_requests: int
) -> None:
    source = FakeSource([raw_object(i) for i in range(1, 8)])
    collection = await _collector(source).collect_city(3, max_pages=max_pages, max_lots=max_lots)

    assert len(collection.apartments) == expected_count
    assert len([c for c in source.calls if "objects" in c[0]]) == expected_requests
    assert collection.limited is (expected_count < 7)


async def test_rate_limiter_spaces_requests() -> None:
    now = 0.0
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        nonlocal now
        sleeps.append(delay)
        now += delay

    limiter = RateLimiter(0.5, clock=lambda: now, sleep=fake_sleep)
    for _ in range(3):
        await limiter.wait()
    assert sleeps == [0.5, 0.5]
