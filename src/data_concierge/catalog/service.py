"""CatalogService: the catalog checked against the database at startup, and data ranges."""

import logging
from collections.abc import Sequence

from data_concierge.catalog.models import Catalog, CatalogEntry, DataRange, SqlField
from data_concierge.catalog.repository import ProcedureMetadataRepository
from data_concierge.core.errors import CatalogError, ConfigurationError
from data_concierge.core.types import ColumnType

logger = logging.getLogger(__name__)


class CatalogService:
    """Owns the loaded catalog; `validate` compares it with the database (section 5.3)."""

    def __init__(self, catalog: Catalog, metadata: ProcedureMetadataRepository) -> None:
        self._catalog = catalog
        self._metadata = metadata

    @property
    def catalog(self) -> Catalog:
        return self._catalog

    def validate(self) -> None:
        """Raises one ConfigurationError listing every mismatch of every catalog entry.

        Which parameters are optional is decided by the YAML alone: SQL Server reports no
        defaults for T-SQL procedure parameters (plan KTD16).
        """
        problems = [problem for entry in self._catalog.entries for problem in self._check(entry)]
        if problems:
            raise ConfigurationError(
                "the catalog does not match the database:\n"
                + "\n".join(f"- {problem}" for problem in problems)
            )

    def load_data_ranges(self) -> dict[str, DataRange]:
        """Data ranges by tool name; each range procedure runs once (plan KTD15)."""
        by_procedure: dict[str, DataRange] = {}
        ranges: dict[str, DataRange] = {}
        for entry in self._catalog.entries:
            if entry.data_range is None:
                continue
            procedure = entry.data_range.procedure
            if procedure not in by_procedure:
                by_procedure[procedure] = self._metadata.read_data_range(procedure)
            ranges[entry.tool] = by_procedure[procedure]
        return ranges

    def _check(self, entry: CatalogEntry) -> list[str]:
        described = self._metadata.describe(entry.procedure)
        if described is None:
            return [
                f"{entry.procedure}: the procedure does not exist or agent_reader cannot execute it"
            ]
        params = [(param.name, param.type) for param in entry.params]
        problems = self._compare(entry.procedure, "parameter", params, described.params)
        if described.columns is None:
            logger.warning(
                "%s: the database cannot describe the result columns; the catalog columns "
                "are checked on every call",
                entry.procedure,
            )
            return problems
        columns = [(column.name, column.type) for column in entry.columns]
        problems += self._compare(entry.procedure, "column", columns, described.columns)
        if not problems and not self._same_order(columns, described.columns):
            problems.append(
                f"{entry.procedure}: columns are in a different order; the catalog has "
                f"{', '.join(name for name, _ in columns)}, the database "
                f"{', '.join(field.name for field in described.columns)}"
            )
        return problems

    def _compare(
        self,
        procedure: str,
        kind: str,
        declared: Sequence[tuple[str, ColumnType]],
        actual: Sequence[SqlField],
    ) -> list[str]:
        """Names compare ignoring case, as SQL Server does; types through ColumnType."""
        found = {field.name.casefold(): field for field in actual}
        problems: list[str] = []
        for name, expected in declared:
            field = found.pop(name.casefold(), None)
            if field is None:
                problems.append(
                    f"{procedure}: {kind} {name} is in the catalog but not in the database"
                )
            elif not self._equivalent(field.sql_type, expected):
                problems.append(
                    f"{procedure}: {kind} {name} is {expected} in the catalog but "
                    f"{field.sql_type} in the database"
                )
        problems += [
            f"{procedure}: {kind} {field.name} is in the database but not in the catalog"
            for field in found.values()
        ]
        return problems

    @staticmethod
    def _equivalent(sql_type: str, expected: ColumnType) -> bool:
        try:
            return ColumnType.from_sql_server(sql_type) == expected
        except CatalogError:
            return False

    @staticmethod
    def _same_order(declared: Sequence[tuple[str, ColumnType]], actual: Sequence[SqlField]) -> bool:
        """The adapter maps result columns by position, so the order has to match too."""
        return [name.casefold() for name, _ in declared] == [f.name.casefold() for f in actual]
