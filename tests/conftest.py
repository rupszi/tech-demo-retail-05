import duckdb
import pytest

from retail_agent.data.duckdb_backend import DuckDBBackend
from retail_agent.data.mock import generate, write_duckdb


@pytest.fixture(scope="session")
def frames():
    return generate(seed=42)


@pytest.fixture(scope="session")
def backend(frames):
    con = duckdb.connect(":memory:")
    write_duckdb(frames, con)
    return DuckDBBackend(con)
