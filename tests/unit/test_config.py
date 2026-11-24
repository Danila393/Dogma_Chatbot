from pydantic import SecretStr

from app.config import Settings


def test_city_ids_parsed_from_comma_separated_string() -> None:
    settings = Settings(parser_city_ids="3, 6,7", _env_file=None)  # type: ignore[arg-type,call-arg]
    assert settings.parser_city_ids == (3, 6, 7)


def test_llm_not_configured_without_model() -> None:
    settings = Settings(llm_api_key=SecretStr("k"), llm_model="", _env_file=None)  # type: ignore[call-arg]
    assert settings.llm_configured is False


def test_llm_configured_with_key_and_model() -> None:
    settings = Settings(llm_api_key=SecretStr("k"), llm_model="m", _env_file=None)  # type: ignore[call-arg]
    assert settings.llm_configured is True


def test_llm_configured_with_local_endpoint_without_key() -> None:
    settings = Settings(llm_base_url="http://localhost:11434/v1", llm_model="m", _env_file=None)  # type: ignore[call-arg]
    assert settings.llm_configured is True
