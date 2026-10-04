"""One structured trace per question, appended to a JSONL file.

A trace records every step taken to answer a question: the guard decision, each model call (model,
tokens, retries), each query (the SQL, rows, errors), confirmations, and the outcome. It answers
the two operational questions: is the agent failing, and why did this particular answer go wrong.
The metrics shown by `/stats` are computed from the same file.
"""

from __future__ import annotations

import json
import logging
import math
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from retail_agent.safety.scrubber import scrub_value

TRACE_FILE = "traces.jsonl"
log = logging.getLogger(__name__)


class Tracer:
    def __init__(
        self,
        trace_dir: str | Path,
        session_id: str,
        user_id: str,
        on_step: Callable[[str], None] | None = None,
    ):
        self._path = Path(trace_dir) / TRACE_FILE
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:  # reported when the first trace cannot be written
            pass
        self._session_id = session_id
        self._user_id = user_id
        self._on_step = on_step  # lets the interface show progress ("Querying the data…")
        self._turn: dict[str, Any] | None = None  # the trace being built; None between questions
        self._started = 0.0
        self._paused_at: float | None = None
        self.last: dict[str, Any] | None = None  # the finished trace of the last question

    def start_turn(self, question: str) -> str:
        self._started = time.perf_counter()
        self._turn = {
            "trace_id": uuid.uuid4().hex[:12],
            "session_id": self._session_id,
            "user": self._user_id,
            "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "question": question,
            "steps": [],
        }
        return self._turn["trace_id"]

    def pause(self) -> None:
        """Stop the clock while waiting for the user, so latency measures the agent only."""
        self._paused_at = time.perf_counter()

    def resume(self) -> None:
        if self._paused_at is not None:
            # Moving the start forward by the length of the pause takes it out of the duration.
            self._started += time.perf_counter() - self._paused_at
            self._paused_at = None

    @contextmanager
    def step(self, kind: str, name: str, **attrs: Any) -> Iterator[dict[str, Any]]:
        """Time a step. The caller adds details (tokens, rows, error) to the yielded dict."""
        if self._on_step:
            self._on_step(f"{kind}: {name}")
        record: dict[str, Any] = {"kind": kind, "name": name, **attrs}
        started = time.perf_counter()
        try:
            yield record
        except Exception as e:
            record["error"] = f"{type(e).__name__}: {e}"
            raise
        finally:
            # Recorded whether the step succeeded or raised, so a failure is in the trace too.
            record["ms"] = round((time.perf_counter() - started) * 1000)
            if self._turn is not None:
                self._turn["steps"].append(record)

    def event(self, kind: str, name: str, **attrs: Any) -> None:
        """Record something that happened at a point in time and has no duration."""
        if self._on_step:
            self._on_step(f"{kind}: {name}")
        if self._turn is not None:
            self._turn["steps"].append({"kind": kind, "name": name, "ms": 0, **attrs})

    def end_turn(self, outcome: str, answer: str = "") -> dict[str, Any]:
        turn, self._turn = self._turn, None
        if turn is None:
            raise RuntimeError("end_turn called without start_turn")
        steps = turn["steps"]
        llm = [s for s in steps if s["kind"] == "llm"]
        sql = [s for s in steps if s["kind"] == "sql"]
        # Totals are stored with the trace, so most metrics are plain sums over traces.
        turn.update(
            outcome=outcome,
            answer=answer,  # already scrubbed; kept so a bad answer can be read next to its steps
            answer_chars=len(answer),
            duration_ms=round((time.perf_counter() - self._started) * 1000),
            llm_calls=len(llm),
            llm_retries=sum(1 for s in steps if s["kind"] == "llm_retry"),
            tokens_in=sum(s.get("tokens_in", 0) for s in llm),
            tokens_out=sum(s.get("tokens_out", 0) for s in llm),
            sql_queries=len(sql),
            sql_errors=sum(1 for s in sql if s.get("error")),
            empty_results=sum(1 for s in sql if s.get("rows") == 0),
            redactions=sum(sum(s.get("redactions", {}).values()) for s in steps),
        )
        # Everything in the record is scrubbed, not only the question and the answer: the SQL
        # the model wrote and the error texts can repeat something a user typed.
        turn = scrub_value(turn)
        # One line per question, appended: the file is the log and the source of the metrics.
        try:
            with self._path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(turn, default=str) + "\n")
        except OSError as e:
            # A log that cannot be written must not take the answer down with it.
            log.warning("Could not write the trace to %s: %s", self._path, e)
        self.last = turn
        return turn


def read_traces(trace_dir: str | Path) -> list[dict[str, Any]]:
    path = Path(trace_dir) / TRACE_FILE
    if not path.exists():
        return []
    traces = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            trace = json.loads(line)
        except json.JSONDecodeError:
            continue  # a damaged line costs one trace, not the whole log
        if isinstance(trace, dict):
            traces.append(trace)
    return traces


def _recovered(trace: dict[str, Any]) -> bool:
    """A query succeeded after one had failed: the self-correction worked."""
    failed = False
    for step in trace["steps"]:
        if step["kind"] != "sql":
            continue
        if step.get("error"):
            failed = True
        elif failed:
            return True
    return False


def _percentile(values: list[float], q: float) -> float:
    """Nearest-rank percentile: always one of the observed values."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)] if ordered else 0.0


def compute_stats(traces: list[dict[str, Any]]) -> dict[str, Any]:
    """Agent-level metrics over a set of traces."""
    n = len(traces)
    if n == 0:
        return {"questions": 0}

    def share(predicate: Callable[[dict], bool]) -> float:
        return round(sum(1 for t in traces if predicate(t)) / n, 3)

    def steps(kind: str) -> list[dict]:
        return [s for t in traces for s in t["steps"] if s["kind"] == kind]

    queries = sum(t["sql_queries"] for t in traces)
    # Questions in which at least one query failed: the base for the recovery rate below.
    had_sql_error = [t for t in traces if t["sql_errors"]]
    durations = [t["duration_ms"] for t in traces]
    confirmations = steps("confirmation")
    return {
        "questions": n,
        "answered": share(lambda t: t["outcome"] == "answered"),
        "blocked_by_guard": share(lambda t: t["outcome"] == "blocked"),
        "gave_up": share(lambda t: t["outcome"] == "gave_up"),
        "failed": share(lambda t: t["outcome"] == "failed"),
        "latency_ms_p50": _percentile(durations, 0.5),
        "latency_ms_p95": _percentile(durations, 0.95),
        "tokens_per_question": round(sum(t["tokens_in"] + t["tokens_out"] for t in traces) / n),
        "llm_calls_per_question": round(sum(t["llm_calls"] for t in traces) / n, 2),
        "llm_retries": sum(t["llm_retries"] for t in traces),  # failed attempts, see below
        "llm_failures_by_model": _count(s["name"] for s in steps("llm_retry")),
        "sql_queries": queries,
        "sql_error_rate": round(sum(t["sql_errors"] for t in traces) / queries, 3)
        if queries
        else 0,
        "empty_result_rate": (
            round(sum(t["empty_results"] for t in traces) / queries, 3) if queries else 0
        ),
        "recovered_after_sql_error": (
            round(sum(1 for t in had_sql_error if _recovered(t)) / len(had_sql_error), 3)
            if had_sql_error
            else None
        ),
        "sql_errors_by_code": _count(s["error"] for s in steps("sql") if s.get("error")),
        "guard_blocks_by_category": _count(s["name"] for s in steps("guard") if s.get("blocked")),
        "pii_redactions": sum(t["redactions"] for t in traces),
        "deletes_confirmed": sum(1 for s in confirmations if s.get("approved")),
        "deletes_cancelled": sum(1 for s in confirmations if not s.get("approved")),
    }


def _count(values: Iterator[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return counts
