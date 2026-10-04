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

    def run(self, sql: str, timeout_s: float | None = None) -> QueryResult:
        """Raises SqlRejected (rule broken) or DataError (backend failure)."""
        # 1. Parse, check and rewrite. From here on only `query.sql` is used, never `sql`.
        query = validate_query(sql, self.profile, max_rows=self.max_rows, dataset=self.dataset)
        # 2. Dry-run. Free: catches errors and oversized scans before anything is billed.
        estimate = self._backend.dry_run(query.sql)
        # 3. Execute, then mask anything that looks like personal data in the result.
        frame, redactions = scrub_frame(self._backend.execute(query.sql, timeout_s=timeout_s))
        return QueryResult(
            sql=query.sql,
            frame=frame,
            tables=query.tables,
            bytes_processed=estimate.bytes_processed,
            redactions=redactions,
            # Cut off means our own row limit was reached; the model is then told to aggregate.
            # A smaller LIMIT that the model chose itself (a top 5) is a complete answer.
            truncated=len(frame) >= self.max_rows,
        )
