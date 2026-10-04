"""BigQuery backend over `thelook_ecommerce`.

Extends the lean runner provided with the brief (execute a query, fetch a table schema) with the
guards a production agent needs: a dry-run, a hard cap on bytes billed, a timeout, a default
dataset so generated SQL can use bare table names, and errors classified for the retry logic.
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd
from google.api_core import exceptions as gexc
from google.cloud import bigquery

from retail_agent.data.base import DataError, DryRunResult
from retail_agent.data.schema import TABLES, ColumnInfo

log = logging.getLogger(__name__)

_UNAVAILABLE = (
    gexc.ServiceUnavailable,
    gexc.InternalServerError,
    gexc.TooManyRequests,
    gexc.GatewayTimeout,
    gexc.DeadlineExceeded,
    gexc.RetryError,
    TimeoutError,
    ConnectionError,
)


class BigQueryBackend:
    dialect = "bigquery"

    def __init__(
        self,
        project_id: str | None,
        dataset: str = "bigquery-public-data.thelook_ecommerce",
        max_bytes_billed: int = 1_000_000_000,
        timeout_s: float = 60.0,
        client: Any | None = None,
    ):
        self.dataset = dataset
        self.max_bytes_billed = max_bytes_billed
        self.timeout_s = timeout_s
        self.client = client or bigquery.Client(project=project_id)

    def list_tables(self) -> list[str]:
        return list(TABLES)

    def get_schema(self, table: str) -> list[ColumnInfo]:
        if table not in TABLES:
            raise DataError("syntax", f"Unknown table: {table}")
        try:
            live = self.client.get_table(f"{self.dataset}.{table}")
        except _UNAVAILABLE as e:
            raise DataError("unavailable", str(e)) from e
        return [ColumnInfo(f.name, f.field_type, f.description or "", f.mode) for f in live.schema]

    def _config(self, dry_run: bool) -> bigquery.QueryJobConfig:
        return bigquery.QueryJobConfig(
            default_dataset=self.dataset,
            maximum_bytes_billed=self.max_bytes_billed,
            dry_run=dry_run,
            use_query_cache=not dry_run,
        )

    def _classify(self, e: Exception) -> DataError:
        if isinstance(e, _UNAVAILABLE):
            return DataError("unavailable", str(e))
        text = str(e)
        if "bytes billed" in text.lower():
            return DataError("too_expensive", text)
        if isinstance(e, gexc.BadRequest | gexc.NotFound):
            return DataError("syntax", text)
        return DataError("execution", text)

    def dry_run(self, sql: str) -> DryRunResult:
        try:
            job = self.client.query(sql, job_config=self._config(dry_run=True))
        except Exception as e:  # noqa: BLE001 - every client error is classified
            raise self._classify(e) from e
        if (
            job.total_bytes_processed is not None
            and job.total_bytes_processed > self.max_bytes_billed
        ):
            raise DataError(
                "too_expensive",
                f"Query would scan {job.total_bytes_processed:,} bytes, limit is "
                f"{self.max_bytes_billed:,}.",
            )
        return DryRunResult(bytes_processed=job.total_bytes_processed)

    def execute(self, sql: str) -> pd.DataFrame:
        try:
            job = self.client.query(sql, job_config=self._config(dry_run=False))
            return job.result(timeout=self.timeout_s).to_dataframe(create_bqstorage_client=False)
        except Exception as e:  # noqa: BLE001 - every client error is classified
            log.warning("BigQuery execution failed: %s", e)
            raise self._classify(e) from e
