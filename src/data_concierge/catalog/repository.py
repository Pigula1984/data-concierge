"""Loading the agent catalog from its YAML file."""

from pathlib import Path
from typing import Protocol

import yaml
from pydantic import ValidationError
from pydantic_core import ErrorDetails

from data_concierge.catalog.models import Catalog
from data_concierge.core.errors import CatalogError


class CatalogRepository(Protocol):
    def load(self) -> Catalog: ...


class YamlCatalogRepository:
    """Reads the catalog from a YAML file whose root is the list of procedure entries."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> Catalog:
        try:
            data = yaml.safe_load(self._path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise CatalogError(f"cannot read catalog file '{self._path}': {exc}") from exc
        if not isinstance(data, list):
            raise CatalogError(f"catalog file '{self._path}' must contain a list of procedures")
        try:
            return Catalog.model_validate({"entries": data})
        except ValidationError as exc:
            problems = "; ".join(_describe(error, data) for error in exc.errors())
            raise CatalogError(f"invalid catalog file '{self._path}': {problems}") from exc


def _describe(error: ErrorDetails, entries: list[object]) -> str:
    """Formats one Pydantic error as `<procedure>.<field path>: <message>`."""
    location = list(error["loc"])
    if location[:1] == ["entries"]:
        location.pop(0)
    index = location[0] if location else None
    if isinstance(index, int):
        entry = entries[index] if index < len(entries) else None
        procedure = entry.get("procedure") if isinstance(entry, dict) else None
        location[0] = procedure or f"entry {index + 1}"
    cause = error.get("ctx", {}).get("error")
    message = str(cause) if isinstance(cause, ValueError) else error["msg"]
    return f"{'.'.join(str(part) for part in location)}: {message}"
