"""The interface every data source implements, plus the errors the agent reasons about."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

import pandas as pd

from retail_agent.data.schema import ColumnInfo

ErrorKind = Literal["syntax", "execution", "too_expensive", "unavailable"]


class DataError(Exception):
    """Base error. `kind` tells the agent whether a retry can help.

    - syntax:        bad SQL or unknown table/column; the model can fix it
    - execution:     failed at run time; the model may be able to fix it
    - too_expensive: scan limit exceeded; the model must narrow the query
    - unavailable:   backend down or throttled; retrying the same SQL later may work
    """

    def __init__(self, kind: ErrorKind, message: str):
        super().__init__(message)
        self.kind: ErrorKind = kind
        self.message = message


@dataclass(frozen=True)
class DryRunResult:
    bytes_processed: int | None  # None when the backend cannot estimate (local DuckDB)


class DataBackend(Protocol):
    """SQL is written in `dialect`; each backend adapts it to its engine."""

    dialect: str

    def list_tables(self) -> list[str]: ...

    def get_schema(self, table: str) -> list[ColumnInfo]: ...

    def dry_run(self, sql: str) -> DryRunResult:
        """Validate the query without paying for it. Raises DataError on invalid SQL."""
        ...

    def execute(self, sql: str) -> pd.DataFrame:
        """Run a read-only query. Raises DataError."""
        ...
