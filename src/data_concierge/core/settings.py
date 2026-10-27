"""Application settings read once from environment variables or `.env`."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, NonNegativeInt, PositiveInt, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

type Effort = Literal["low", "medium", "high", "xhigh", "max"]
# What data the model provider receives (architecture 002, section 3).
type ModelDataAccess = Literal["none", "minimal"]


class Settings(BaseSettings):
    """Connection, model and limit settings; loaded in the composition root and injected."""

    # extra="ignore": a shared .env may hold variables of other tools.
    # env_ignore_empty: an unfilled template entry (`DC_SQL_PASSWORD=`) counts as missing.
    model_config = SettingsConfigDict(
        env_prefix="DC_", env_file=".env", frozen=True, extra="ignore", env_ignore_empty=True
    )

    sql_server: str
    sql_database: str = "AdventureWorks2025"
    sql_user: str = "agent_reader"
    sql_password: SecretStr
    sql_driver: str = "ODBC Driver 18 for SQL Server"
    # Trusting a self-signed certificate is acceptable only for a local instance.
    sql_trust_server_certificate: bool = False

    model: str = "anthropic:claude-haiku-5-5"
    effort: Effort = "medium"
    strict_tools: bool = True
    model_data_access: ModelDataAccess = "none"
    # Standard provider variable name; passed explicitly to the provider by the composition root.
    anthropic_api_key: SecretStr | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")

    catalog_path: Path = Path("catalog/agent-procedures.yaml")

    # The audit log of every turn (architecture 8.1). A separate switch, because an empty
    # path in `.env` counts as missing and falls back to the default.
    audit_log_enabled: bool = True
    audit_log_path: Path = Path("logs/audit.jsonl")

    # Output budget of one model request, thinking included.
    max_output_tokens: PositiveInt = 16_000
    # Corrections the model may make per tool and for the final answer (ModelRetry).
    agent_retries: NonNegativeInt = 2

    max_agent_steps: PositiveInt = 10
    procedure_timeout_seconds: PositiveInt = 30
    # Results up to this many rows give the model their ids and clarification candidates
    # (architecture 002, sections 4.2 and 4.3).
    model_max_rows: PositiveInt = 50
    max_response_rows: PositiveInt = 10_000
    # A query result above this is dropped and the model asked to aggregate (section 7.4).
    max_query_rows: PositiveInt = 100_000


class ModelExposure(BaseModel, frozen=True):
    """What the model sees of the data in the process's mode (architecture 002, sections 4-5).

    `dimension_values`: dimension values (up to 20, never of sensitive columns) in result
    summaries. `comment_phase`: a second model pass writes the comment after seeing the tables.
    """

    mode: ModelDataAccess
    dimension_values: bool
    comment_phase: bool

    @classmethod
    def for_mode(cls, mode: ModelDataAccess) -> ModelExposure:
        minimal = mode == "minimal"
        return cls(mode=mode, dimension_values=minimal, comment_phase=minimal)

    @classmethod
    def from_settings(cls, settings: Settings) -> ModelExposure:
        return cls.for_mode(settings.model_data_access)


# The default mode of the service (D7), for components built without settings, e.g. in tests.
DEFAULT_EXPOSURE = ModelExposure.for_mode("none")
