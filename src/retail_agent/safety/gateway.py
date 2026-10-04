"""The only path from the agent to the data.

The agent is handed a `QueryGateway`, never a raw backend, so a query cannot reach the database
without being validated, scoped to the user, cost-checked and scrubbed.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from retail_agent.data.base import DataBackend
from retail_agent.data.schema import DATASET
from retail_agent.safety.profiles import UserProfile
from retail_agent.safety.scrubber import scrub_frame
from retail_agent.safety.validator import validate_query


@dataclass(frozen=True)
class QueryResult:
    sql: str  # the SQL that actually ran
    frame: pd.DataFrame
    tables: tuple[str, ...]
    bytes_processed: int | None
    redactions: dict[str, int]
    truncated: bool  # the row limit was reached, so the result may be incomplete


class QueryGateway:
    def __init__(
        self, backend: DataBackend, profile: UserProfile, *, max_rows: int, dataset: str = DATASET
    ):
        self._backend = backend
        self.profile = profile
        self.max_rows = max_rows
        self.dataset = dataset

    def run(self, sql: str) -> QueryResult:
        """Raises SqlRejected (rule broken) or DataError (backend failure)."""
        query = validate_query(sql, self.profile, max_rows=self.max_rows, dataset=self.dataset)
        estimate = self._backend.dry_run(query.sql)  # free: catches errors and oversized scans
        frame, redactions = scrub_frame(self._backend.execute(query.sql))
        return QueryResult(
            sql=query.sql,
            frame=frame,
            tables=query.tables,
            bytes_processed=estimate.bytes_processed,
            redactions=redactions,
            truncated=len(frame) >= query.limit,
        )
