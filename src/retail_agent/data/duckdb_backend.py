"""Local backend over the mock DuckDB file.

The mock file is attached under the BigQuery project name and its tables live in a schema named
after the dataset, so `project.dataset.table` resolves exactly as it does in BigQuery and a bare
table name resolves to nothing. SQL arrives in BigQuery dialect and is transpiled.
"""

from __future__ import annotations

import duckdb
import pandas as pd
import sqlglot
from sqlglot.errors import SqlglotError

from retail_agent.data.base import DataError, DryRunResult
from retail_agent.data.mock import write_duckdb
from retail_agent.data.schema import DATASET, TABLES, ColumnInfo, split_dataset

_SYNTAX_ERRORS = (duckdb.ParserException, duckdb.BinderException, duckdb.CatalogException)


class DuckDBBackend:
    dialect = "bigquery"

    def __init__(self, con: duckdb.DuckDBPyConnection):
        self._con = con

    @classmethod
    def from_path(cls, path: str, dataset: str = DATASET) -> DuckDBBackend:
        project, _ = split_dataset(dataset)
        con = duckdb.connect()
        escaped = str(path).replace("'", "''")
        con.execute(f"ATTACH '{escaped}' AS \"{project}\" (READ_ONLY)")
        return cls(con)

    @classmethod
    def from_frames(cls, frames: dict[str, pd.DataFrame], dataset: str = DATASET) -> DuckDBBackend:
        """In-memory database, used by the tests."""
        project, name = split_dataset(dataset)
        con = duckdb.connect()
        con.execute(f"ATTACH ':memory:' AS \"{project}\"")
        write_duckdb(frames, con, f'"{project}".{name}')
        return cls(con)

    def list_tables(self) -> list[str]:
        return list(TABLES)

    def get_schema(self, table: str) -> list[ColumnInfo]:
        if table not in TABLES:
            raise DataError("syntax", f"Unknown table: {table}")
        return list(TABLES[table])

    def _to_duckdb(self, sql: str) -> str:
        try:
            return sqlglot.transpile(sql, read="bigquery", write="duckdb")[0]
        except SqlglotError as e:
            raise DataError("syntax", f"SQL syntax error: {e}") from e

    def dry_run(self, sql: str) -> DryRunResult:
        duck_sql = self._to_duckdb(sql)
        try:
            self._con.cursor().execute(f"EXPLAIN {duck_sql}")
        except _SYNTAX_ERRORS as e:
            raise DataError("syntax", str(e)) from e
        except duckdb.Error as e:
            raise DataError("execution", str(e)) from e
        return DryRunResult(bytes_processed=None)

    def execute(self, sql: str) -> pd.DataFrame:
        duck_sql = self._to_duckdb(sql)
        try:
            return self._con.cursor().execute(duck_sql).df()
        except _SYNTAX_ERRORS as e:
            raise DataError("syntax", str(e)) from e
        except duckdb.Error as e:
            raise DataError("execution", str(e)) from e
