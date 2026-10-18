from datetime import date

import pytest

from data_concierge.catalog.models import Catalog, DataRange
from data_concierge.catalog.repository import SqlServerMetadataRepository
from data_concierge.catalog.service import CatalogService
from data_concierge.core.errors import ConfigurationError
from data_concierge.core.types import ColumnType

pytestmark = pytest.mark.integration


def test_repository_catalog_matches_the_deployed_database(
    catalog: Catalog, metadata: SqlServerMetadataRepository
) -> None:
    CatalogService(catalog, metadata).validate()


def test_broken_type_in_a_catalog_copy_stops_startup_naming_the_entry(
    catalog: Catalog, metadata: SqlServerMetadataRepository
) -> None:
    entry = catalog.entry("api.SalesOrder_List")
    broken_param = entry.params[0].model_copy(update={"type": ColumnType.parse("datetime")})
    broken = entry.model_copy(update={"params": (broken_param, *entry.params[1:])})
    entries = tuple(broken if e is entry else e for e in catalog.entries)

    with pytest.raises(ConfigurationError, match=r"api\.SalesOrder_List: parameter DateFrom"):
        CatalogService(catalog.model_copy(update={"entries": entries}), metadata).validate()


def test_sales_order_list_data_range(
    catalog: Catalog, metadata: SqlServerMetadataRepository
) -> None:
    ranges = CatalogService(catalog, metadata).load_data_ranges()

    assert ranges["sales_order_list"] == DataRange(first=date(2022, 5, 30), last=date(2025, 6, 29))
