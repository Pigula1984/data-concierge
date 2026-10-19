"""Agent tools built from the catalog (Registry + Factory, docs/standards/wzorce-projektowe.md)."""

from collections.abc import Mapping
from typing import cast

from pydantic_ai import RunContext, Tool

from data_concierge.agent.deps import AgentDeps
from data_concierge.agent.tools import describe_result, query, retry_on_domain_error
from data_concierge.catalog.models import Catalog, CatalogEntry, ColumnSpec, DataRange
from data_concierge.core.types import ColumnRole
from data_concierge.procedures.models import ProcedureArguments
from data_concierge.results.models import ResultSummary


class ProcedureToolFactory:
    """Builds one tool per catalog entry; its argument model gives both the schema and the
    validation (plan KTD8), and injected parameters such as `@UserId` are not in it."""

    def __init__(self, data_ranges: Mapping[str, DataRange], strict: bool) -> None:
        self._data_ranges = data_ranges
        self._strict = strict

    def create(self, entry: CatalogEntry) -> Tool[AgentDeps]:
        arguments = ProcedureArguments.for_entry(entry)

        # A closure, because Pydantic AI reads the argument model from the annotation; mypy
        # cannot check a type built at runtime, hence the ignore and the cast.
        @retry_on_domain_error
        def call(ctx: RunContext[AgentDeps], args: arguments) -> ResultSummary:  # type: ignore[valid-type]
            validated = cast(ProcedureArguments, args)
            return ctx.deps.procedures.call(entry, validated.model_dump(), ctx.deps.context)

        return Tool(
            call, name=entry.tool, description=self._description(entry), strict=self._strict
        )

    def _description(self, entry: CatalogEntry) -> str:
        """Summary, grain, when to use and not to use, measures and the data range (section 6.1).

        Parameter hints are in the schema's field descriptions, so they are not repeated here.
        Measures are listed because the model chooses one before it sees the result, e.g. which
        column "sprzedaż" means.
        """
        sentences = [entry.summary]
        if entry.grain:
            sentences.append(entry.grain[0].upper() + entry.grain[1:] + ".")
        if entry.use_when:
            sentences.append(f"Używaj do: {'; '.join(entry.use_when)}.")
        if entry.not_for:
            sentences.append(f"Nie używaj do: {'; '.join(entry.not_for)}.")
        measures = [
            self._measure(column) for column in entry.columns if column.role is ColumnRole.MEASURE
        ]
        if measures:
            sentences.append(f"Miary: {', '.join(measures)}.")
        data_range = self._data_ranges.get(entry.tool)
        if data_range is not None:
            sentences.append(f"Dane: od {data_range.first} do {data_range.last}.")
        return " ".join(sentences)

    @staticmethod
    def _measure(column: ColumnSpec) -> str:
        """`SubTotal (Sprzedaż netto, ...; domyślna dla: sprzedaż)`, or the bare name."""
        notes = [column.hint] if column.hint else []
        if column.default_for:
            notes.append(f"domyślna dla: {', '.join(column.default_for)}")
        return f"{column.name} ({'; '.join(notes)})" if notes else column.name


class ToolRegistry:
    """The agent's tools in a fixed order: exposed procedures in catalog order, then `query`
    and `describe_result`. The order and the definitions never change within a process, so
    the start of every conversation stays byte-identical (plan KTD11)."""

    def __init__(self, catalog: Catalog, factory: ProcedureToolFactory, strict: bool) -> None:
        self._tools: tuple[Tool[AgentDeps], ...] = (
            *(factory.create(entry) for entry in catalog.exposed_entries),
            Tool(query, strict=strict),
            Tool(describe_result),
        )

    def tools(self) -> tuple[Tool[AgentDeps], ...]:
        return self._tools
