"""The composition root offline: startup validation and the configured model."""

from collections.abc import Callable

import pytest
from anthropic import AsyncAnthropic
from pydantic import SecretStr
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel

from data_concierge.bootstrap import CompositionRoot, DuckDBSessionFactory
from data_concierge.catalog.models import Catalog, DataRange, ProcedureMetadata
from data_concierge.core.context import RequestContext
from data_concierge.core.errors import ConfigurationError
from data_concierge.core.settings import Settings
from data_concierge.procedures.repository import ProcedureRepository


class MissingProcedures:
    """A database where agent_reader can execute none of the catalog procedures."""

    def describe(self, procedure: str) -> ProcedureMetadata | None:
        return None

    def read_data_range(self, procedure: str) -> DataRange:
        raise AssertionError("data ranges are read only from a validated catalog")


def test_catalog_that_does_not_match_the_database_stops_the_start(
    agent_settings: Settings,
    repository: ProcedureRepository,
    scripted: Callable[..., FunctionModel],
) -> None:
    metadata = MissingProcedures()

    with pytest.raises(ConfigurationError, match=r"api\.SalesOrder_List: the procedure does not"):
        CompositionRoot.assemble(agent_settings, metadata, repository, scripted())


def test_configured_anthropic_model_gets_the_key_from_the_settings(
    agent_settings: Settings,
) -> None:
    settings = agent_settings.model_copy(
        update={"model": "anthropic:claude-sonnet-5-5", "anthropic_api_key": SecretStr("sk-test")}
    )

    model = CompositionRoot.configured_model(settings)

    assert isinstance(model, AnthropicModel)
    assert model.model_name == "claude-sonnet-5-5"
    assert isinstance(model.client, AsyncAnthropic)
    assert model.client.api_key == "sk-test"


def test_model_comes_from_the_model_setting(agent_settings: Settings) -> None:
    settings = agent_settings.model_copy(update={"model": "test"})

    assert isinstance(CompositionRoot.configured_model(settings), TestModel)


def test_each_session_has_its_own_result_store(
    agent_settings: Settings, repository: ProcedureRepository, catalog: Catalog
) -> None:
    factory = DuckDBSessionFactory(repository, agent_settings)
    first, second = factory.create(), factory.create()

    first.procedures.call(
        catalog.entry("api.SalesOrder_List"),
        {"date_from": "2024-01-01", "date_to": "2025-01-01"},
        RequestContext(user_id=7, role="user"),
    )

    assert first.id != second.id
    assert first.results.handles() == ("r1",)
    assert second.results.handles() == ()


@pytest.mark.parametrize(
    ("mode", "dimension_stats"),
    [
        pytest.param("none", {"OrderDate"}, id="none"),
        pytest.param("minimal", {"OrderDate", "CustomerType", "Territory"}, id="minimal"),
    ],
)
def test_session_summaries_follow_the_configured_mode(
    agent_settings: Settings,
    repository: ProcedureRepository,
    catalog: Catalog,
    mode: str,
    dimension_stats: set[str],
) -> None:
    settings = agent_settings.model_copy(update={"model_data_access": mode})
    session = DuckDBSessionFactory(repository, settings).create()

    summary = session.procedures.call(
        catalog.entry("api.SalesOrder_List"),
        {"date_from": "2024-01-01", "date_to": "2025-01-01"},
        RequestContext(user_id=7, role="user"),
    )

    # CustomerName is a sensitive dimension, so neither mode shows it.
    assert set(summary.stats) == dimension_stats
