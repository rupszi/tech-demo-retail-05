from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from retail_agent.data.base import DataBackend, DataError, DryRunResult

if TYPE_CHECKING:
    from retail_agent.config import Settings


def create_backend(settings: Settings) -> DataBackend:
    """Pick the backend from settings. DuckDB builds the mock database on first use."""
    if settings.data_backend == "bigquery":
        from retail_agent.data.bigquery_backend import BigQueryBackend

        return BigQueryBackend(
            settings.gcp_project_id,
            dataset=settings.bq_dataset,
            max_bytes_billed=settings.bq_max_bytes_billed,
        )
    if settings.data_backend == "duckdb":
        from retail_agent.data.duckdb_backend import DuckDBBackend
        from retail_agent.data.mock import build_mock_db

        if not Path(settings.duckdb_path).exists():
            build_mock_db(settings.duckdb_path, settings.bq_dataset)
        return DuckDBBackend.from_path(settings.duckdb_path, settings.bq_dataset)
    raise ValueError(f"Unknown DATA_BACKEND: {settings.data_backend!r}")


__all__ = ["DataBackend", "DataError", "DryRunResult", "create_backend"]
