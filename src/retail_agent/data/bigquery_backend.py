"""BigQuery backend over `thelook_ecommerce`.

Extends the lean runner provided with the brief (execute a query, fetch a table schema) with the
guards a production agent needs: a dry-run, a hard cap on bytes billed, a timeout, and errors
classified for the retry logic.

Jobs run without a default dataset on purpose: a bare table name resolves to nothing, so the only
tables a query can reach are the fully qualified ones written by the SQL gate.
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd
from google.api_core import exceptions as gexc
from google.auth import exceptions as auth_exc
from google.cloud import bigquery

from retail_agent.data.base import DataError, DryRunResult
from retail_agent.data.schema import DATASET, TABLES, ColumnInfo

log = logging.getLogger(__name__)

# Failures that say nothing about the SQL: trying the same query again later may work.
_UNAVAILABLE = (
    gexc.ServiceUnavailable,
    gexc.InternalServerError,
    gexc.TooManyRequests,
    gexc.GatewayTimeout,
    gexc.DeadlineExceeded,
    gexc.RetryError,
    auth_exc.GoogleAuthError,  # expired or missing credentials: rewriting the SQL cannot help
    TimeoutError,
    ConnectionError,
)


class BigQueryBackend:
    dialect = "bigquery"

    def __init__(
        self,
        project_id: str | None,
        dataset: str = DATASET,
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
            # The live schema, used by the BigQuery tests to check the schema kept in code.
            live = self.client.get_table(f"{self.dataset}.{table}")
        except _UNAVAILABLE as e:
            raise DataError("unavailable", str(e)) from e
        return [ColumnInfo(f.name, f.field_type, f.description or "", f.mode) for f in live.schema]

    def _config(self, dry_run: bool) -> bigquery.QueryJobConfig:
        # No default dataset is set, on purpose (see the module docstring). The byte cap is
        # enforced by BigQuery itself, so it holds even if our own estimate check were skipped.
        return bigquery.QueryJobConfig(
            maximum_bytes_billed=self.max_bytes_billed,
            dry_run=dry_run,
            use_query_cache=not dry_run,
        )

    def _classify(self, e: Exception) -> DataError:
        """Map a client error to the kind the retry logic acts on."""
        if isinstance(e, _UNAVAILABLE):
            return DataError("unavailable", str(e))
        text = str(e)
        if "bytes billed" in text.lower():
            return DataError("too_expensive", text)
        # BigQuery reports an exhausted quota or rate limit as 403. Rewriting the SQL cannot help.
        if isinstance(e, gexc.Forbidden) and (
            "quota" in text.lower() or "rate limit" in text.lower()
        ):
            return DataError("unavailable", text)
        if isinstance(e, gexc.BadRequest | gexc.NotFound):
            return DataError("syntax", text)
        return DataError("execution", text)

    def dry_run(self, sql: str) -> DryRunResult:
        """Free: BigQuery checks the query and says how much it would scan, without running it."""
        try:
            job = self.client.query(sql, job_config=self._config(dry_run=True))
        except Exception as e:  # noqa: BLE001 - every client error is classified
            raise self._classify(e) from e
        # Refuse an oversized query here, with a message the model can act on, and not only
        # through BigQuery's own cap at run time.
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

    def execute(self, sql: str, timeout_s: float | None = None) -> pd.DataFrame:
        # Never longer than the backend's own timeout; shorter when the question has less left.
        timeout = self.timeout_s if timeout_s is None else max(1.0, min(self.timeout_s, timeout_s))
        job = None
        try:
            job = self.client.query(sql, job_config=self._config(dry_run=False))
            return job.result(timeout=timeout).to_dataframe(create_bqstorage_client=False)
        except TimeoutError as e:
            # Our own timeout, not an outage. The job would keep running on BigQuery, so it is
            # cancelled, and the model is told to narrow the query: running the same SQL again
            # would only time out again.
            if job is not None:
                try:
                    job.cancel()
                except Exception:  # noqa: BLE001 - best effort; the timeout is what gets reported
                    log.warning("Could not cancel BigQuery job after a timeout")
            message = f"The query ran for more than {timeout:.0f} seconds and was cancelled."
            raise DataError("too_expensive", message) from e
        except Exception as e:  # noqa: BLE001 - every client error is classified
            log.warning("BigQuery execution failed: %s", e)
            raise self._classify(e) from e
