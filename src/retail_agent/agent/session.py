"""One conversation with one user. This is what an interface (the CLI, later an API) talks to."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from typing import Any

from langgraph.types import Command

from retail_agent.agent.graph import AgentDeps, build_graph
from retail_agent.agent.tools import Toolbox
from retail_agent.config import Settings
from retail_agent.data.base import DataBackend
from retail_agent.golden import load_trios
from retail_agent.llm import LLM
from retail_agent.observability import Tracer
from retail_agent.reports import ReportStore
from retail_agent.safety import QueryGateway, UserProfile

MSG_INTERNAL_ERROR = (
    "Something went wrong on my side and I could not finish that. "
    "Your conversation is intact; please try again."
)


@dataclass(frozen=True)
class TurnResult:
    """Either an answer, or a request for the user to confirm a destructive action."""

    trace_id: str
    answer: str = ""
    outcome: str = ""
    confirmation: dict[str, Any] | None = None
    trace: dict[str, Any] | None = None
    scope_note: str = ""  # said by the application: the figures cover only the user's brands


class ChatSession:
    def __init__(
        self,
        *,
        llm: LLM,
        backend: DataBackend,
        profile: UserProfile,
        settings: Settings,
        reports: ReportStore | None = None,
        tracer: Tracer | None = None,
        conversation_id: str | None = None,
    ):
        self.conversation_id = conversation_id or uuid.uuid4().hex[:8]
        self.profile = profile
        self.reports = reports or ReportStore(settings.reports_db_path)
        self.tracer = tracer or Tracer(settings.trace_dir, self.conversation_id, profile.user_id)
        if hasattr(llm, "on_failure"):  # make model retries, fallbacks and waits visible
            llm.on_failure = lambda model, attempt, error: self.tracer.event(
                "llm_retry", model, attempt=attempt, error=str(error)[:200]
            )
            llm.on_wait = lambda seconds: self.tracer.event(
                "llm_wait", "rate_limit", seconds=round(seconds)
            )
        # The agent is given the gateway, never the backend: there is no path to the data that
        # skips validation, scoping and scrubbing.
        gateway = QueryGateway(
            backend, profile, max_rows=settings.max_rows, dataset=settings.bq_dataset
        )
        self.toolbox = Toolbox(gateway, self.reports, self.conversation_id, settings, self.tracer)
        self._graph = build_graph(
            AgentDeps(
                llm=llm,
                toolbox=self.toolbox,
                reports=self.reports,
                profile=profile,
                settings=settings,
                tracer=self.tracer,
                trios=load_trios(settings.golden_dir),
            )
        )
        # The thread id is the key under which the checkpointer keeps this conversation's state.
        # The recursion limit is a backstop only: it is set above what the limit on model calls
        # allows (two graph steps per call), so that limit is always the one that stops a turn.
        self._config = {
            "configurable": {"thread_id": self.conversation_id},
            "recursion_limit": 2 * settings.max_llm_calls + 10,
        }
        self._trace_id = ""
        self.awaiting_confirmation = False

    def ask(self, question: str) -> TurnResult:
        dropped = None
        if self.awaiting_confirmation:
            # An unanswered confirmation counts as "no", and the turn it belonged to ends there.
            dropped = self._decide(approved=False, abandoned=True)
        # The trace is scrubbed as a whole when it is written, the question included.
        self._trace_id = self.tracer.start_turn(question)
        result = self._run({"question": question})
        if dropped is not None and dropped.answer:
            # Say what became of the request, so a "yes" typed into the chat is not left hanging.
            result = replace(result, answer=f"{dropped.answer}\n\n{result.answer}".strip())
        return result

    def confirm(self, approved: bool) -> TurnResult:
        """Deliver the user's decision on a pending destructive action."""
        if not self.awaiting_confirmation:
            raise RuntimeError("There is nothing to confirm.")
        return self._decide(approved)

    def _decide(self, approved: bool, abandoned: bool = False) -> TurnResult:
        self.tracer.resume()
        # Resuming re-enters the graph at the paused step, with the decision as its input.
        return self._run(Command(resume={"approved": approved, "abandoned": abandoned}))

    def _run(self, graph_input: Any) -> TurnResult:
        self.awaiting_confirmation = False
        try:
            state = self._graph.invoke(graph_input, self._config)
        except KeyboardInterrupt:
            # The interface interrupted the question (Ctrl-C). Its trace is closed here; what
            # happens to the chat is the interface's decision.
            self.tracer.event("error", "interrupted")
            self.tracer.end_turn("failed", "")
            raise
        except Exception as e:  # noqa: BLE001 - nothing may crash the interface
            self.tracer.event("error", "unhandled", error=f"{type(e).__name__}: {e}"[:500])
            trace = self.tracer.end_turn("failed", MSG_INTERNAL_ERROR)
            return TurnResult(self._trace_id, MSG_INTERNAL_ERROR, "failed", trace=trace)
        interrupts = state.get("__interrupt__")
        if interrupts:
            # The graph stopped to ask the user. The turn is not over: the trace stays open and
            # its clock is paused until `confirm` is called.
            self.awaiting_confirmation = True
            self.tracer.pause()
            return TurnResult(self._trace_id, confirmation=interrupts[0].value)
        trace = self.tracer.end_turn(state["outcome"], state["answer"])
        return TurnResult(
            self._trace_id,
            state["answer"],
            state["outcome"],
            trace=trace,
            scope_note=self._scope_note(trace),
        )

    def _scope_note(self, trace: dict[str, Any]) -> str:
        """Written by the application, not the model, so it is always there. Every query is cut
        down to the user's brands, so a figure is never the company's unless they have them all."""
        # Only an answer shows figures: a failure message or an apology has nothing to explain.
        if self.profile.all_brands or trace.get("outcome") != "answered":
            return ""
        if not trace.get("sql_queries"):
            return ""
        brands = ", ".join(self.profile.brands) or "none"
        return f"These figures cover only the brands you have access to: {brands}."
