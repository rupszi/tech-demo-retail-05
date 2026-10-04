"""Local backend over the mock DuckDB file. SQL arrives in BigQuery dialect and is transpiled."""

from __future__ import annotations

import duckdb
import pandas as pd
import sqlglot
from sqlglot.errors import SqlglotError

from retail_agent.data.base import DataError, DryRunResult
from retail_agent.data.schema import TABLES, ColumnInfo

_SYNTAX_ERRORS = (duckdb.ParserException, duckdb.BinderException, duckdb.CatalogException)


class DuckDBBackend:
    dialect = "bigquery"

    def __init__(self, con: duckdb.DuckDBPyConnection):
        self._con = con

    @classmethod
    def from_path(cls, path: str) -> DuckDBBackend:
        return cls(duckdb.connect(path, read_only=True))

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
