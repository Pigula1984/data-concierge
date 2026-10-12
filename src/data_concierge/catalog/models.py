"""Agent catalog: procedure descriptions written for the model, loaded from YAML."""

from collections import Counter
from datetime import date
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, PositiveInt, model_validator

from data_concierge.core.errors import CatalogError
from data_concierge.core.types import Column, ColumnRole, ColumnType, TableSchema

DEFAULT_MAX_ROWS = 100_000

type ExampleValue = str | int | bool | date | None


class _CatalogModel(BaseModel, frozen=True, extra="forbid"):
    """Rejects unknown keys, so a typo in the YAML fails loudly instead of being dropped."""


class InjectSource(StrEnum):
    """Backend values a parameter can be bound to; the model never sees these parameters."""

    USER_ID = "request_context.user_id"


class ParamSpec(_CatalogModel):
    """Stored procedure parameter; `name` is the database name without `@`."""

    name: str
    type: ColumnType
    arg: str | None = None
    required: bool = False
    hint: str | None = None
    inject: InjectSource | None = None

    @model_validator(mode="after")
    def _check_binding(self) -> Self:
        if self.inject is None:
            if self.arg is None:
                raise ValueError(f"parameter '{self.name}' needs 'arg' or 'inject'")
            return self
        if self.required:
            raise ValueError(f"parameter '{self.name}' cannot be both 'inject' and 'required'")
        if self.arg is not None:
            raise ValueError(f"injected parameter '{self.name}' cannot have an 'arg'")
        return self


class ColumnSpec(_CatalogModel):
    name: str
    type: ColumnType
    role: ColumnRole
    hint: str | None = None
    values: tuple[str, ...] | None = None
    default_for: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _check_default_for(self) -> Self:
        if self.default_for and self.role is not ColumnRole.MEASURE:
            raise ValueError(
                f"column '{self.name}' has role '{self.role}'; "
                "only 'measure' columns can have 'default_for'"
            )
        return self


class DataRangeSpec(_CatalogModel):
    """Points to the procedure that returns the min/max dates of the data."""

    procedure: str


class Example(_CatalogModel):
    prompt: str
    call: dict[str, ExampleValue]
    then: str | None = None


class CatalogEntry(_CatalogModel):
    """One read procedure as the agent sees it."""

    procedure: str
    tool: str
    # False for procedures only the backend calls, such as the data-range procedure.
    exposed: bool = True
    access: Literal["read"] = "read"
    summary: str
    use_when: tuple[str, ...] = ()
    not_for: tuple[str, ...] = ()
    grain: str | None = None
    synonyms: tuple[str, ...] = ()
    max_rows: PositiveInt = DEFAULT_MAX_ROWS
    params: tuple[ParamSpec, ...] = ()
    columns: tuple[ColumnSpec, ...]
    data_range: DataRangeSpec | None = None
    examples: tuple[Example, ...] = ()

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        _require_unique("parameter", [param.name.casefold() for param in self.params])
        _require_unique("argument", [param.arg for param in self.params if param.arg])
        _require_unique("column", [column.name.casefold() for column in self.columns])
        model_args = {param.arg for param in self.params if param.arg}
        for example in self.examples:
            unknown = sorted(set(example.call) - model_args)
            if unknown:
                raise ValueError(
                    f"example '{example.prompt}' uses undeclared arguments: {', '.join(unknown)}"
                )
        return self

    @property
    def table_schema(self) -> TableSchema:
        return TableSchema(
            columns=tuple(
                Column(name=column.name, type=column.type, role=column.role)
                for column in self.columns
            )
        )

    @property
    def model_params(self) -> tuple[ParamSpec, ...]:
        """Parameters the model supplies; injected ones are filled in by the backend."""
        return tuple(param for param in self.params if param.inject is None)


class Catalog(_CatalogModel):
    entries: tuple[CatalogEntry, ...]

    @model_validator(mode="after")
    def _check_references(self) -> Self:
        _require_unique("procedure", [entry.procedure.casefold() for entry in self.entries])
        owners: dict[str, str] = {}
        for entry in self.entries:
            if entry.tool in owners:
                raise ValueError(
                    f"tool name '{entry.tool}' is used by both "
                    f"{owners[entry.tool]} and {entry.procedure}"
                )
            owners[entry.tool] = entry.procedure
        procedures = {entry.procedure for entry in self.entries}
        for entry in self.entries:
            if entry.data_range and entry.data_range.procedure not in procedures:
                raise ValueError(
                    f"{entry.procedure}: data_range procedure "
                    f"'{entry.data_range.procedure}' is not in the catalog"
                )
        return self

    @property
    def exposed_entries(self) -> tuple[CatalogEntry, ...]:
        return tuple(entry for entry in self.entries if entry.exposed)

    def entry(self, procedure: str) -> CatalogEntry:
        for entry in self.entries:
            if entry.procedure == procedure:
                return entry
        raise CatalogError(f"procedure '{procedure}' is not in the catalog")


def _require_unique(kind: str, names: list[str]) -> None:
    duplicates = sorted(name for name, count in Counter(names).items() if count > 1)
    if duplicates:
        raise ValueError(f"duplicate {kind} names: {', '.join(duplicates)}")
