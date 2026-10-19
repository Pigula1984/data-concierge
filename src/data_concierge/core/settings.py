"""Application settings read once from environment variables or `.env`."""

from pathlib import Path
from typing import Literal

from pydantic import Field, NonNegativeInt, PositiveInt, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

type Effort = Literal["low", "medium", "high", "xhigh", "max"]


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
    # Standard provider variable name; passed explicitly to the provider by the composition root.
    anthropic_api_key: SecretStr | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")

    catalog_path: Path = Path("catalog/agent-procedures.yaml")

    # Output budget of one model request, thinking included.
    max_output_tokens: PositiveInt = 16_000
    # Corrections the model may make per tool and for the final answer (ModelRetry).
    agent_retries: NonNegativeInt = 2

    max_agent_steps: PositiveInt = 10
    procedure_timeout_seconds: PositiveInt = 30
    preview_rows: PositiveInt = 10
    full_preview_max_rows: PositiveInt = 50
    max_response_rows: PositiveInt = 10_000
    # A query result above this is dropped and the model asked to aggregate (section 7.4).
    max_query_rows: PositiveInt = 100_000
