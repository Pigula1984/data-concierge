"""Arguments the model passes to a procedure, and results of procedure calls."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pyarrow as pa
from pydantic import BaseModel, Field, create_model

from data_concierge.catalog.models import CatalogEntry
from data_concierge.core.types import ColumnKind

_PYTHON_TYPES: dict[ColumnKind, type] = {
    ColumnKind.INT: int,
    ColumnKind.STRING: str,
    ColumnKind.DATE: date,
    ColumnKind.DATETIME: datetime,
    ColumnKind.BOOL: bool,
    ColumnKind.DECIMAL: Decimal,
}


class ProcedureArguments(BaseModel, frozen=True, extra="forbid"):
    """Base of the argument model of every catalog entry; unknown arguments are rejected,
    so the model cannot set an injected parameter such as `@UserId`."""

    @classmethod
    def for_entry(cls, entry: CatalogEntry) -> type[ProcedureArguments]:
        """The one argument model of a catalog entry: it validates the call and gives the
        tool its schema (plan KTD8). Optional arguments are `T | None`, i.e. anyOf with null."""
        fields: dict[str, Any] = {}
        for param in entry.model_params:
            assert param.arg is not None  # model_params always have an argument name
            python_type = _PYTHON_TYPES[param.type.kind]
            if param.required:
                fields[param.arg] = (python_type, Field(description=param.hint))
            else:
                fields[param.arg] = (
                    python_type | None,
                    Field(default=None, description=param.hint),
                )
        return create_model(f"{entry.tool}_arguments", __base__=cls, **fields)


@dataclass(frozen=True, slots=True)
class ProcedureResult:
    """Rows of a procedure as Arrow data typed by the catalog.

    `truncated` is set when the procedure returned more rows than the catalog's max_rows.
    """

    data: pa.Table
    truncated: bool
