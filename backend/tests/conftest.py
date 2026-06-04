import pytest

from app.graph.ingest import ingest_all


@pytest.fixture(scope="session", autouse=True)
def seed():
    """Ingest the graph once before the suite (idempotent MERGE)."""
    ingest_all()
