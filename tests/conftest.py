import pytest

from retail_agent.data.duckdb_backend import DuckDBBackend
from retail_agent.data.mock import generate


@pytest.fixture(scope="session")
def frames():
    return generate(seed=42)


@pytest.fixture(scope="session")
def backend(frames):
    return DuckDBBackend.from_frames(frames)
