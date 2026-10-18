"""Shared pytest configuration: tests never call a real model by default.

Integration fixtures connect to the local SQL Server deployed with db/deploy.ps1 and read the
connection settings from `.env`; only tests marked `integration` request them.
"""

import os
from pathlib import Path

import pytest
from pydantic import SecretStr
from pydantic_ai import models

from data_concierge.catalog.models import Catalog
from data_concierge.catalog.repository import YamlCatalogRepository
from data_concierge.core.settings import Settings
from data_concierge.core.sqlserver import SqlServerClient

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")
models.ALLOW_MODEL_REQUESTS = False

CATALOG_PATH = Path(__file__).parents[1] / Settings.model_fields["catalog_path"].default


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings()


@pytest.fixture(scope="session")
def agent_client(settings: Settings) -> SqlServerClient:
    """Connects as agent_reader, the account the backend uses."""
    return SqlServerClient.from_settings(settings)


@pytest.fixture(scope="session")
def admin_client(settings: Settings) -> SqlServerClient:
    """Connects with Windows authentication, for reference queries on the tables."""
    trust = "yes" if settings.sql_trust_server_certificate else "no"
    return SqlServerClient(
        SecretStr(
            f"DRIVER={{{settings.sql_driver}}};SERVER={settings.sql_server};"
            f"DATABASE={settings.sql_database};Trusted_Connection=yes;Encrypt=yes;"
            f"TrustServerCertificate={trust}"
        ),
        settings.procedure_timeout_seconds,
    )


@pytest.fixture(scope="session")
def catalog() -> Catalog:
    """The catalog from the repository."""
    return YamlCatalogRepository(CATALOG_PATH).load()
