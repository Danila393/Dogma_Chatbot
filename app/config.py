"""Centralised application settings loaded from environment variables / .env."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

DEFAULT_CITY_IDS = (1, 2, 3, 4, 5, 6, 7)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: Literal["development", "production", "test"] = "development"
    log_level: str = "INFO"

    database_url: str = "sqlite+aiosqlite:///./data/apartments.db"
    sqlite_busy_timeout_ms: int = Field(default=5000, ge=0)

    # LLM (any OpenAI-compatible endpoint)
    llm_api_key: SecretStr = SecretStr("")
    llm_base_url: str = ""
    llm_model: str = ""
    llm_timeout: float = Field(default=30.0, gt=0)
    llm_max_retries: int = Field(default=2, ge=0, le=5)
    llm_temperature: float = Field(default=0.0, ge=0, le=2)

    # Parser
    parser_base_url: str = "https://service.dogma.ru/api/layouts-filter/v4"
    parser_site_url: str = "https://dogma.ru"
    parser_timeout: float = Field(default=30.0, gt=0)
    parser_concurrency: int = Field(default=3, ge=1, le=10)
    parser_max_retries: int = Field(default=3, ge=0, le=10)
    parser_backoff_base: float = Field(default=0.5, ge=0)
    parser_request_delay: float = Field(default=0.3, ge=0)
    parser_page_size: int = Field(default=100, ge=1, le=500)
    parser_city_ids: Annotated[tuple[int, ...], NoDecode] = DEFAULT_CITY_IDS
    parser_user_agent: str = (
        "DogmaApartmentAgent/1.0 (test assignment; catalog sync; low request rate)"
    )

    # Search
    default_search_limit: int = Field(default=5, ge=1, le=10)
    max_search_limit: int = Field(default=10, ge=1, le=50)
    chat_max_message_length: int = Field(default=1000, ge=10, le=10000)

    @field_validator("parser_city_ids", mode="before")
    @classmethod
    def _parse_city_ids(cls, value: object) -> object:
        if isinstance(value, str):
            parts = [part.strip() for part in value.split(",") if part.strip()]
            return tuple(int(part) for part in parts)
        return value

    @property
    def llm_configured(self) -> bool:
        """LLM is usable when a model is set and either a key or a custom endpoint is given."""
        has_credentials = bool(self.llm_api_key.get_secret_value()) or bool(self.llm_base_url)
        return bool(self.llm_model) and has_credentials


@lru_cache
def get_settings() -> Settings:
    return Settings()
