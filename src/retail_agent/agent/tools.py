"""The tools the model can call, and the code that runs them.

Adding a capability (a chart, an email, a web search) means adding one `ToolSpec` and one method
here, and one branch in the `tools` step of the graph, which is where calls are dispatched. Tools
never raise: a failure is returned to the model as a result it can act on.
"""

from __future__ import annotations

import json
import time
from typing import Any

from retail_agent.config import Settings
from retail_agent.data.base import DataError
from retail_agent.llm import ToolSpec
from retail_agent.observability import Tracer
from retail_agent.reports import Report, ReportStore
from retail_agent.safety import QueryGateway, QueryResult, SqlRejected, scrub_text


def _schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    """The JSON schema of a tool's arguments."""
    return {"type": "object", "properties": properties, "required": required or []}


# What the model is told it can do. There is deliberately no tool that confirms a deletion, and
# none that reaches the database except through the gateway.
TOOL_SPECS = [
    ToolSpec(
        "run_sql",
        "Run one read-only BigQuery SQL query on the retail tables and return the rows.",
        _schema({"sql": {"type": "string", "description": "A single SELECT statement."}}, ["sql"]),
    ),
    ToolSpec(
        "save_report",
        "Save a report to the user's Saved Reports library.",
        _schema(
            {
                "title": {"type": "string"},
                "content": {"type": "string", "description": "The full report in markdown."},
            },
            ["title", "content"],
        ),
    ),
    ToolSpec("list_reports", "List the user's saved reports (id, title, date).", _schema({})),
    ToolSpec(
        "get_report",
        "Read one saved report.",
        _schema({"report_id": {"type": "integer"}}, ["report_id"]),
    ),
    ToolSpec(
        "delete_reports",
        "Request permanent deletion of saved reports. The application asks the user to confirm "
        "before anything is deleted. Give at least one way of choosing the reports.",
        _schema(
            {
                "mentioning": {
                    "type": "string",
                    "description": "Delete reports whose title or content contains this text.",
                },
                "this_conversation": {
                    "type": "boolean",
                    "description": "Delete the reports created in the current conversation.",
                },
                "report_ids": {"type": "array", "items": {"type": "integer"}},
                "all_reports": {"type": "boolean", "description": "Delete every saved report."},
            }
        ),
    ),
]

# Sent with a result when no further query will run, so the model explains and does not retry.
_STOP = "Do not run more queries for this question. Tell the user plainly what you could not do."


class Toolbox:
    def __init__(
        self,
        gateway: QueryGateway,
        reports: ReportStore,
        conversation_id: str,
        settings: Settings,
        tracer: Tracer,
    ):
        self._gateway = gateway
        self._reports = reports
        self._owner = gateway.profile.user_id  # every report operation is limited to this user
        self._conversation_id = conversation_id
        self._settings = settings
        self._tracer = tracer

    # ---- data ------------------------------------------------------------------------------
    def run_sql(
        self, sql: str, failures: int, empties: int, deadline: float | None = None
    ) -> tuple[dict, int, int]:
        """Run a query. Returns the result for the model and the updated failure counters.

        `failures` is how many queries have already failed for this question. Once it passes the
        limit, no more queries run: this is what bounds the cost of self-correction. `deadline`
        is the wall-clock time at which the question runs out of time.
        """
        limit = self._settings.max_sql_retries
        if failures > limit:
            refused = {"error": "The query limit for this question was reached."}
            return {**refused, "instruction": _STOP}, failures, empties
        if deadline is not None and time.time() >= deadline:
            refused = {"error": "The time limit for this question was reached."}
            return {**refused, "instruction": _STOP}, failures, empties
        with self._tracer.step("sql", "run_sql", sql=sql) as step:
            try:
                result = self._execute(sql, deadline)
            except SqlRejected as e:
                step.update(error=e.code, error_message=e.message[:300], rejected=True)
                # An honest mistake costs one attempt. Something a well-behaved model would not
                # write (a write statement, a forbidden function) ends the attempts at once.
                failures = failures + 1 if e.retryable else limit + 1
                return self._failure(e.message, failures), failures, empties
            except DataError as e:
                step.update(error=e.kind, error_message=e.message[:300])
                if e.kind == "unavailable":  # not the model's fault: do not ask it to rewrite
                    message = "The data warehouse is temporarily unavailable."
                    return {"error": message, "instruction": _STOP}, limit + 1, empties
                failures += 1
                return self._failure(e.message, failures), failures, empties
            step.update(
                rows=len(result.frame),
                executed_sql=result.sql,
                bytes_processed=result.bytes_processed,
                truncated=result.truncated,
                redactions=result.redactions,
            )
        payload = self._payload(result)
        if not len(result.frame):
            empties += 1
            payload["note"] = (
                "No rows matched. If a filter value may be wrong (spelling, case, date range), "
                "check the available values with one more query. Otherwise tell the user that "
                "no data matched."
                if empties == 1
                else "No rows matched again. Do not retry; tell the user that no data matched."
            )
        return payload, failures, empties

    def _execute(self, sql: str, deadline: float | None = None) -> QueryResult:
        """One immediate retry when the backend is briefly unavailable."""

        def time_left() -> float | None:
            return None if deadline is None else deadline - time.time()

        try:
            return self._gateway.run(sql, timeout_s=time_left())
        except DataError as e:
            left = time_left()
            if e.kind != "unavailable" or (left is not None and left <= 1.0):
                raise  # not worth retrying, or no time left to retry in
            self._tracer.event("sql_retry", "backend_unavailable", error=e.message[:200])
            time.sleep(1.0)
            return self._gateway.run(sql, timeout_s=time_left())

    def _failure(self, message: str, failures: int) -> dict:
        """The error as the model sees it, with what it should do next."""
        left = self._settings.max_sql_retries - failures + 1
        if left <= 0:
            return {"error": message, "instruction": _STOP}
        return {
            "error": message,
            "attempts_left": left,
            "instruction": "Fix the query and try again.",
        }

    def _payload(self, result: QueryResult) -> dict:
        """The result as the model sees it: at most `rows_to_model` rows, and a note if cut."""
        shown = result.frame.head(self._settings.rows_to_model)
        table = json.loads(shown.to_json(orient="split", index=False, date_format="iso"))
        payload = {
            "columns": table["columns"],
            "rows": table["data"],
            "row_count": len(result.frame),
        }
        if result.truncated:
            payload["note"] = (
                f"The result was cut off at {len(result.frame)} rows and is incomplete. "
                "Aggregate or filter before drawing conclusions."
            )
        elif len(result.frame) > len(shown):
            payload["note"] = (
                f"Only the first {len(shown)} of {len(result.frame)} rows are shown. "
                "Aggregate or filter to see the whole picture."
            )
        return payload

    # ---- reports ---------------------------------------------------------------------------
    def save_report(self, title: str, content: str) -> dict:
        # A saved report is output too, so it goes through the same scrubber as an answer.
        title, _ = scrub_text(title.strip() or "Untitled report")
        content, redactions = scrub_text(content)
        report = self._reports.save(self._owner, self._conversation_id, title, content)
        self._tracer.event("report", "save", report_id=report.id, redactions=redactions)
        return {"saved": True, "report_id": report.id, "title": report.title}

    def list_reports(self) -> dict:
        return {
            "reports": [
                {
                    "id": r.id,
                    "title": r.title,
                    "created_at": r.created_at,
                    "this_conversation": r.conversation_id == self._conversation_id,
                }
                for r in self._reports.list(self._owner)
            ]
        }

    def get_report(self, report_id: int) -> dict:
        report = self._reports.get(self._owner, int(report_id))
        if report is None:
            return {"error": f"There is no saved report with id {report_id}."}
        return {"id": report.id, "title": report.title, "content": report.content}

    def find_reports_to_delete(self, args: dict) -> tuple[list[Report], str]:
        """Resolve a delete request to the user's own matching reports. Deletes nothing."""
        mentioning = (args.get("mentioning") or "").strip()
        this_conversation = bool(args.get("this_conversation"))
        report_ids = args.get("report_ids") or None
        # A request with no criterion would match every report, so it must be explicit.
        if not (mentioning or this_conversation or report_ids or args.get("all_reports")):
            return [], "Say which reports to delete: by text, by conversation, by id, or all."
        found = self._reports.find(
            self._owner,
            mentioning=mentioning or None,
            conversation_id=self._conversation_id if this_conversation else None,
            report_ids=[int(i) for i in report_ids] if report_ids else None,
        )
        return found, ""
