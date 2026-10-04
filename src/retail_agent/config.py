"""Settings loaded from the environment (and a local .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    data_backend: str = "duckdb"
    duckdb_path: str = "data/mock.duckdb"
    gcp_project_id: str | None = None
    bq_dataset: str = "bigquery-public-data.thelook_ecommerce"
    bq_max_bytes_billed: int = 1_000_000_000
    max_sql_retries: int = 2
    max_rows: int = 500
    turn_token_budget: int = 60_000
    reports_db_path: str = "data/reports.sqlite"
    trace_dir: str = "logs"

    @classmethod
    def from_env(cls) -> Settings:
        load_dotenv()
        env = os.environ.get
        return cls(
            data_backend=env("DATA_BACKEND", "duckdb"),
            duckdb_path=env("DUCKDB_PATH", "data/mock.duckdb"),
            gcp_project_id=env("GCP_PROJECT_ID") or None,
            bq_dataset=env("BQ_DATASET", "bigquery-public-data.thelook_ecommerce"),
            bq_max_bytes_billed=int(env("BQ_MAX_BYTES_BILLED", "1000000000")),
            max_sql_retries=int(env("MAX_SQL_RETRIES", "2")),
            max_rows=int(env("MAX_ROWS", "500")),
            turn_token_budget=int(env("TURN_TOKEN_BUDGET", "60000")),
            reports_db_path=env("REPORTS_DB_PATH", "data/reports.sqlite"),
            trace_dir=env("TRACE_DIR", "logs"),
        )
