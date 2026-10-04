"""One conversation with one user. This is what an interface (the CLI, later an API) talks to."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
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
from retail_agent.safety import QueryGateway, UserProfile, scrub_text

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
        gateway = QueryGateway(
            backend, profile, max_rows=settings.max_rows, dataset=settings.bq_dataset
        )
        toolbox = Toolbox(gateway, self.reports, self.conversation_id, settings, self.tracer)
        self._graph = build_graph(
            AgentDeps(
                llm=llm,
                toolbox=toolbox,
                reports=self.reports,
                profile=profile,
                settings=settings,
                tracer=self.tracer,
                trios=load_trios(settings.golden_dir),
            )
        )
        self._config = {"configurable": {"thread_id": self.conversation_id}, "recursion_limit": 60}
        self._trace_id = ""
        self.awaiting_confirmation = False

    def ask(self, question: str) -> TurnResult:
        if self.awaiting_confirmation:  # an unanswered confirmation counts as "no"
            self.confirm(False)
        self._trace_id = self.tracer.start_turn(scrub_text(question)[0])
        return self._run({"question": question})

    def confirm(self, approved: bool) -> TurnResult:
        """Deliver the user's decision on a pending destructive action."""
        if not self.awaiting_confirmation:
            raise RuntimeError("There is nothing to confirm.")
        self.tracer.resume()
        return self._run(Command(resume={"approved": approved}))

    def undo_last_delete(self) -> list[str]:
        return [r.title for r in self.reports.restore_last(self.profile.user_id)]

    def _run(self, graph_input: Any) -> TurnResult:
        self.awaiting_confirmation = False
        try:
            state = self._graph.invoke(graph_input, self._config)
        except Exception as e:  # noqa: BLE001 - nothing may crash the interface
            self.tracer.event("error", "unhandled", error=f"{type(e).__name__}: {e}"[:500])
            trace = self.tracer.end_turn("failed", MSG_INTERNAL_ERROR)
            return TurnResult(self._trace_id, MSG_INTERNAL_ERROR, "failed", trace=trace)
        interrupts = state.get("__interrupt__")
        if interrupts:
            self.awaiting_confirmation = True
            self.tracer.pause()
            return TurnResult(self._trace_id, confirmation=interrupts[0].value)
        trace = self.tracer.end_turn(state["outcome"], state["answer"])
        return TurnResult(self._trace_id, state["answer"], state["outcome"], trace=trace)
