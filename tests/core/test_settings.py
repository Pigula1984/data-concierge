from pathlib import Path

import pydantic
import pytest
from pydantic import SecretStr

from data_concierge.core.settings import ModelExposure, Settings


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    prefix = Settings.model_config["env_prefix"]
    for field in Settings.model_fields:
        monkeypatch.delenv(f"{prefix}{field.upper()}", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def _set_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DC_SQL_SERVER", "localhost")
    monkeypatch.setenv("DC_SQL_PASSWORD", "s3cret")


def test_settings_read_values_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required(monkeypatch)
    monkeypatch.setenv("DC_SQL_TRUST_SERVER_CERTIFICATE", "true")
    monkeypatch.setenv("DC_MODEL", "anthropic:claude-sonnet-5-5")
    monkeypatch.setenv("DC_STRICT_TOOLS", "false")
    monkeypatch.setenv("DC_PROCEDURE_TIMEOUT_SECONDS", "45")

    settings = Settings(_env_file=None)

    assert settings.sql_server == "localhost"
    assert settings.sql_password.get_secret_value() == "s3cret"
    assert settings.sql_trust_server_certificate is True
    assert settings.model == "anthropic:claude-sonnet-5-5"
    assert settings.strict_tools is False
    assert settings.procedure_timeout_seconds == 45


def test_settings_defaults_follow_architecture_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required(monkeypatch)

    settings = Settings(_env_file=None)

    assert settings.sql_user == "agent_reader"
    assert settings.sql_trust_server_certificate is False
    assert settings.model == "anthropic:claude-haiku-5-5"
    assert settings.effort == "medium"
    assert settings.strict_tools is True
    assert settings.max_agent_steps == 10
    assert settings.procedure_timeout_seconds == 30
    assert settings.model_max_rows == 50
    assert settings.max_response_rows == 10_000
    assert settings.max_query_rows == 100_000
    assert settings.model_data_access == "none"


@pytest.mark.parametrize(
    ("present", "missing"),
    [("DC_SQL_SERVER", "sql_password"), ("DC_SQL_PASSWORD", "sql_server")],
)
def test_settings_require_sql_connection(
    monkeypatch: pytest.MonkeyPatch, present: str, missing: str
) -> None:
    monkeypatch.setenv(present, "value")

    with pytest.raises(pydantic.ValidationError, match=missing):
        Settings(_env_file=None)


def test_settings_read_anthropic_key_from_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DC_SQL_SERVER=localhost\nDC_SQL_PASSWORD=s3cret\nANTHROPIC_API_KEY=sk-test\n",
        encoding="utf-8",
    )

    settings = Settings(_env_file=env_file)

    assert settings.anthropic_api_key is not None
    assert settings.anthropic_api_key.get_secret_value() == "sk-test"


def test_model_data_access_accepts_minimal_and_rejects_other_modes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_required(monkeypatch)
    monkeypatch.setenv("DC_MODEL_DATA_ACCESS", "minimal")

    assert Settings(_env_file=None).model_data_access == "minimal"

    monkeypatch.setenv("DC_MODEL_DATA_ACCESS", "full")
    with pytest.raises(pydantic.ValidationError, match="model_data_access"):
        Settings(_env_file=None)


@pytest.mark.parametrize(
    ("mode", "shown"),
    [pytest.param("none", False, id="none"), pytest.param("minimal", True, id="minimal")],
)
def test_model_exposure_follows_the_mode(
    monkeypatch: pytest.MonkeyPatch, mode: str, shown: bool
) -> None:
    _set_required(monkeypatch)
    monkeypatch.setenv("DC_MODEL_DATA_ACCESS", mode)

    exposure = ModelExposure.from_settings(Settings(_env_file=None))

    assert (exposure.dimension_values, exposure.comment_phase) == (shown, shown)


ENV_EXAMPLE = Path(__file__).parents[2] / ".env.example"


def test_env_example_loads_into_settings() -> None:
    settings = Settings(_env_file=ENV_EXAMPLE, sql_password=SecretStr("s3cret"))

    assert settings.sql_server == "localhost"
    assert settings.anthropic_api_key is None
    assert settings.model_data_access == "none"


def test_empty_password_in_env_file_counts_as_missing() -> None:
    with pytest.raises(pydantic.ValidationError, match="sql_password"):
        Settings(_env_file=ENV_EXAMPLE)


def test_settings_hide_password_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required(monkeypatch)

    assert "s3cret" not in repr(Settings(_env_file=None))
