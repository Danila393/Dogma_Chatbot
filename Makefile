.PHONY: install lint format typecheck test cov check run sync bot docker-up docker-sync

install:
	uv sync

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff check --fix .
	uv run ruff format .

typecheck:
	uv run mypy app

test:
	uv run pytest -v

cov:
	uv run pytest --cov=app --cov-report=term-missing

check: lint typecheck cov

run:
	uv run uvicorn app.main:app --reload

sync:
	uv run python -m app.cli sync

bot:
	uv run python -m app.bot

docker-up:
	docker compose up --build

docker-sync:
	docker compose run --rm api python -m app.cli sync
