import pytest

from data_concierge.catalog.repository import SqlServerMetadataRepository
from data_concierge.core.sqlserver import SqlServerClient


@pytest.fixture
def metadata(agent_client: SqlServerClient) -> SqlServerMetadataRepository:
    """Reads metadata as agent_reader, which has nothing but EXECUTE (plan KTD16)."""
    return SqlServerMetadataRepository(agent_client)
