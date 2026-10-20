"""Composition root: the only place that creates connections and concrete implementations
(docs/standards/wzorce-projektowe.md, section 7)."""

from collections.abc import Callable
from datetime import date
from typing import Any
from uuid import uuid4

import pydantic_ai
from fastapi import FastAPI
from pydantic_ai.models import Model, infer_model
from pydantic_ai.providers import Provider, infer_provider
from pydantic_ai.providers.anthropic import AnthropicProvider

from data_concierge.agent.assembler import ResponseAssembler
from data_concierge.agent.prompts import AgentInstructions
from data_concierge.agent.registry import ProcedureToolFactory, ToolRegistry
from data_concierge.agent.service import AgentService
from data_concierge.api.controller import router
from data_concierge.api.service import AskService
from data_concierge.api.sessions import InMemorySessionRepository, Session
from data_concierge.catalog.repository import (
    ProcedureMetadataRepository,
    SqlServerMetadataRepository,
    YamlCatalogRepository,
)
from data_concierge.catalog.service import CatalogService
from data_concierge.core.settings import Settings
from data_concierge.core.sqlserver import SqlServerClient
from data_concierge.procedures.repository import ProcedureRepository, SqlServerProcedureRepository
from data_concierge.procedures.service import ProcedureService
from data_concierge.query.policy import QueryPolicy
from data_concierge.query.service import QueryService
from data_concierge.results.repository import DuckDBResultStore
from data_concierge.results.service import ResultService


class DuckDBSessionFactory:
    """A new session: its own locked DuckDB store and the services over it (plan KTD19)."""

    def __init__(self, procedures: ProcedureRepository, settings: Settings) -> None:
        self._procedures = procedures
        self._settings = settings

    def create(self) -> Session:
        settings = self._settings
        results = ResultService(
            DuckDBResultStore(max_query_rows=settings.max_query_rows),
            preview_rows=settings.preview_rows,
            full_preview_max_rows=settings.full_preview_max_rows,
        )
        return Session(
            id=str(uuid4()),
            procedures=ProcedureService(self._procedures, results),
            queries=QueryService(results, QueryPolicy()),
            results=results,
            assembler=ResponseAssembler(results, settings.max_response_rows),
        )


class CompositionRoot:
    """Builds the application once at startup; a catalog that does not match the database
    stops the start with a ConfigurationError (architecture, section 5.3)."""

    def __init__(self, ask_service: AskService) -> None:
        self.ask_service = ask_service

    def http_app(self) -> FastAPI:
        app = FastAPI(title="Data Concierge")
        app.state.ask_service = self.ask_service
        app.include_router(router)
        return app

    @classmethod
    def from_settings(cls, settings: Settings) -> CompositionRoot:
        # The CLI and the server own their output; no Pydantic AI banner on the first run.
        pydantic_ai.BANNER_ENABLED = False
        client = SqlServerClient.from_settings(settings)
        return cls.assemble(
            settings,
            SqlServerMetadataRepository(client),
            SqlServerProcedureRepository(client),
            cls._model(settings),
        )

    @classmethod
    def assemble(
        cls,
        settings: Settings,
        metadata: ProcedureMetadataRepository,
        procedures: ProcedureRepository,
        model: Model,
        today: Callable[[], date] = date.today,
    ) -> CompositionRoot:
        catalog_service = CatalogService(
            YamlCatalogRepository(settings.catalog_path).load(), metadata
        )
        catalog_service.validate()
        catalog = catalog_service.catalog
        data_ranges = catalog_service.load_data_ranges()
        strict = settings.strict_tools
        agent = AgentService(
            model,
            ToolRegistry(catalog, ProcedureToolFactory(data_ranges, strict), strict).tools(),
            AgentInstructions(catalog, data_ranges, settings.full_preview_max_rows).text(),
            settings,
        )
        sessions = InMemorySessionRepository()
        factory = DuckDBSessionFactory(procedures, settings)
        return cls(AskService(agent, sessions, factory, today))

    @staticmethod
    def _model(settings: Settings) -> Model:
        """The `provider:model` from the settings (R15); the Anthropic key comes from the
        settings, because `.env` is not in the process environment the provider reads."""
        key = settings.anthropic_api_key

        def provider(name: str) -> Provider[Any]:
            if name == "anthropic" and key is not None:
                return AnthropicProvider(api_key=key.get_secret_value())
            return infer_provider(name)

        return infer_model(settings.model, provider)


# module-level: `uvicorn data_concierge.bootstrap:create_app --factory` needs a module-level
# callable without arguments.
def create_app() -> FastAPI:
    return CompositionRoot.from_settings(Settings()).http_app()
