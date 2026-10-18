"""Checks on the repository's own catalog file, catalog/agent-procedures.yaml."""

from data_concierge.catalog.models import Catalog


def test_catalog_describes_every_read_procedure(catalog: Catalog) -> None:
    # Architecture section 5.4 plus the data-range procedure (KTD15).
    assert {entry.procedure for entry in catalog.entries} == {
        "api.Customer_Search",
        "api.Customer_List",
        "api.SalesOrder_List",
        "api.SalesOrderLine_List",
        "api.Product_Search",
        "api.SalesOrder_DataRange",
        "dbo.uspGetBillOfMaterials",
        "dbo.uspGetWhereUsedProductID",
        "dbo.uspGetEmployeeManagers",
        "dbo.uspGetManagerEmployees",
    }


def test_tool_names_are_unique(catalog: Catalog) -> None:
    tools = [entry.tool for entry in catalog.entries]

    assert len(tools) == len(set(tools))


def test_only_the_data_range_procedure_is_hidden_from_the_model(catalog: Catalog) -> None:
    hidden = [entry.procedure for entry in catalog.entries if not entry.exposed]

    assert hidden == ["api.SalesOrder_DataRange"]


def test_examples_use_only_parameters_the_model_supplies(catalog: Catalog) -> None:
    for entry in catalog.entries:
        model_args = {param.arg for param in entry.model_params}
        for example in entry.examples:
            assert set(example.call) <= model_args, (entry.procedure, example.prompt)


def test_api_procedures_receive_the_user_id_except_data_range(catalog: Catalog) -> None:
    # dbo.* procedures have no @UserId; adding one would fail validation against the database.
    for entry in catalog.entries:
        injected = [param.name for param in entry.params if param.inject is not None]
        expects_user_id = entry.procedure.startswith("api.") and entry.exposed
        assert injected == (["UserId"] if expects_user_id else []), entry.procedure
