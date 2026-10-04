"""The conversation flow, as a LangGraph graph.

    START -> guard -> agent <-> tools -> confirm_delete -> END
               |        |                      |
              END      END                   agent (only when other tools ran in the same step)

- guard:          rule-based check of the user's message; a blocked message never reaches the model
- agent:          one model call; it either asks for tools or gives the final answer
- tools:          runs the requested tools; a delete request is only prepared here, never executed
- confirm_delete: pauses the graph until the user decides, permanently deletes exactly what was
                  shown, and writes the outcome itself, so what the user is told about a delete
                  never depends on the model
"""

from __future__ import annotations

import json
import math
import operator
import time
from dataclasses import dataclass
from typing import Annotated, Any, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from retail_agent.agent.prompts import build_system_prompt, load_persona
from retail_agent.agent.tools import TOOL_SPECS, Toolbox
from retail_agent.config import Settings
from retail_agent.golden import Trio, find_similar
from retail_agent.llm import LLM, LLMUnavailable, Message
from retail_agent.observability import Tracer
from retail_agent.reports import ReportStore
from retail_agent.safety import UserProfile, check_input, scrub_text

HISTORY_MESSAGES = 20  # earlier questions and answers kept in the model's context

# Fixed replies, written by the application. The model never words a failure or a limit.
MSG_UNAVAILABLE = (
    "I can't reach the language model right now. Nothing was lost; please try again in a moment."
)
MSG_RATE_LIMITED = "The language model's usage limit has been reached. Please try again in {wait}."
MSG_TIME = (
    "I stopped because this question passed the time limit ({limit}). "
    "Please narrow it or split it into smaller steps."
)
MSG_BUDGET = (
    "I reached the work limit for a single question before finishing. "
    "Please narrow the question or split it into smaller steps."
)
# Added to the tool results when one model call is left, so the work ends in an answer.
LAST_STEP = (
    "This was the last step allowed for this question. Do not call any more tools. Answer now "
    "from the results you already have, and say plainly what you could not check."
)
ONE_DELETE = "Only one delete request can be handled per question."


class AgentState(TypedDict, total=False):
    question: str  # the incoming message; enters `messages` only if the guard allows it
    messages: Annotated[list[Message], operator.add]  # appended to, never replaced
    turn_start: int  # index in `messages` where the current question starts
    turn_started_at: float  # wall-clock time, for the time limit
    llm_calls: int
    tokens: int
    sql_failures: int
    empty_results: int
    pending_delete: dict[str, Any] | None  # what the user is being asked to confirm
    paused_at: float  # when the wait for the user began; that wait is not charged to the limit
    delete_outcome: str  # what happened to a delete in this question; always shown first
    answer: str
    outcome: str  # answered | blocked | gave_up | failed


@dataclass
class AgentDeps:
    llm: LLM
    toolbox: Toolbox
    reports: ReportStore
    profile: UserProfile
    settings: Settings
    tracer: Tracer
    trios: list[Trio]


def _human_duration(seconds: float) -> str:
    if seconds < 90:
        return f"about {math.ceil(seconds)} seconds"
    if seconds < 5400:
        return f"about {round(seconds / 60)} minutes"
    return f"about {round(seconds / 3600)} hours"


def _text_message(text: str) -> Message:
    return {"role": "assistant", "text": text, "tool_calls": [], "raw": None}


def _context(messages: list[Message], turn_start: int) -> list[Message]:
    """What the model sees: earlier questions and answers, plus everything in this turn.

    Tool calls and result tables from earlier turns are left out. This keeps the cost of a long
    conversation flat; if old numbers are needed again the model can query for them.
    """
    earlier = [
        m
        for m in messages[:turn_start]
        if m["role"] == "user" or (m["role"] == "assistant" and not m.get("tool_calls"))
    ][-HISTORY_MESSAGES:]
    # Cutting the history can leave an answer without its question; a conversation must start
    # with the user.
    while earlier and earlier[0]["role"] != "user":
        earlier.pop(0)
    return earlier + messages[turn_start:]


def build_graph(deps: AgentDeps):
    settings, tracer, toolbox = deps.settings, deps.tracer, deps.toolbox
    owner = deps.profile.user_id

    def time_used(state: AgentState) -> float:
        return time.time() - state["turn_started_at"]

    def guard(state: AgentState) -> dict:
        with tracer.step("guard", "allowed") as step:
            verdict = check_input(state["question"])
            if not verdict.allowed:
                step.update(name=verdict.category, blocked=True)
        if not verdict.allowed:
            # Nothing is added to `messages`: a blocked message is never part of the conversation
            # the model sees, so it cannot influence a later answer.
            return {"answer": verdict.message, "outcome": "blocked"}
        return {
            "messages": [{"role": "user", "text": state["question"]}],
            "turn_start": len(state.get("messages", [])),
            "turn_started_at": time.time(),
            # Every limit is per question, so the counters start again here.
            "llm_calls": 0,
            "tokens": 0,
            "sql_failures": 0,
            "empty_results": 0,
            "pending_delete": None,
            "delete_outcome": "",
            "answer": "",
            "outcome": "",
        }

    def agent(state: AgentState) -> dict:
        def finish(text: str, outcome: str) -> dict:
            # A delete decided earlier in this question is reported first and by the application,
            # whatever the model says or fails to say afterwards.
            if state.get("delete_outcome"):
                text = f"{state['delete_outcome']}\n\n{text}"
            return {"messages": [_text_message(text)], "answer": text, "outcome": outcome}

        def out_of_time(seconds: float) -> dict:
            limit = settings.turn_time_budget_s
            tracer.event("budget", "time", seconds=round(seconds), limit=limit)
            return finish(MSG_TIME.format(limit=_human_duration(limit)), "failed")

        # The limits are checked before a model call is paid for, not after.
        out_of_calls = state["llm_calls"] >= settings.max_llm_calls
        if out_of_calls or state["tokens"] >= settings.turn_token_budget:
            limit = "calls" if out_of_calls else "tokens"
            tracer.event("budget", limit, llm_calls=state["llm_calls"], tokens=state["tokens"])
            return finish(MSG_BUDGET, "failed")
        used = time_used(state)
        if used >= settings.turn_time_budget_s:
            return out_of_time(used)

        # The instructions are rebuilt for every call: the tone file may have been edited, and
        # the analyst examples depend on the question.
        examples = find_similar(state["question"], deps.trios)
        system = build_system_prompt(deps.profile, load_persona(settings.persona_path), examples)
        failure: LLMUnavailable | None = None
        # The step is named after the model that answers. The trace also keeps which analyst
        # examples the model was given, to explain an answer later.
        with tracer.step("llm", "unanswered", examples=[t.name for t in examples]) as step:
            try:
                response = deps.llm.generate(
                    system,
                    _context(state["messages"], state["turn_start"]),
                    TOOL_SPECS,
                    # The time that is left is the deadline for this call, retries included.
                    time_left=settings.turn_time_budget_s - used,
                )
            except LLMUnavailable as e:
                step["error"] = str(e)[:300]
                failure = e
            else:
                step.update(
                    name=response.model,
                    tokens_in=response.input_tokens,
                    tokens_out=response.output_tokens,
                    tool_calls=[c.name for c in response.tool_calls],
                )
        if failure is not None:
            used = time_used(state)
            if used >= settings.turn_time_budget_s:  # the deadline cut the retries short
                return out_of_time(used)
            if failure.retry_after:  # every model is rate-limited: say how long to wait
                wait = _human_duration(failure.retry_after)
                return finish(MSG_RATE_LIMITED.format(wait=wait), "failed")
            return finish(MSG_UNAVAILABLE, "failed")

        usage = {
            "llm_calls": state["llm_calls"] + 1,
            "tokens": state["tokens"] + response.input_tokens + response.output_tokens,
        }
        if response.tool_calls:
            return {"messages": [response.to_message()], **usage}

        text, redactions = scrub_text(response.text)  # last check before anything is shown
        if redactions:
            tracer.event("scrub", "answer", redactions=redactions)
        gave_up = state["sql_failures"] > settings.max_sql_retries
        return {**finish(text, "gave_up" if gave_up else "answered"), **usage}

    def tools(state: AgentState) -> dict:
        results: list[Message] = []
        failures, empties = state["sql_failures"], state["empty_results"]
        # A query gets what is left of the question's time, and none is started after it.
        deadline = state["turn_started_at"] + settings.turn_time_budget_s
        pending = None
        for call in state["messages"][-1]["tool_calls"]:
            name, args = call["name"], call["args"]
            try:
                if name == "run_sql":
                    result, failures, empties = toolbox.run_sql(
                        str(args.get("sql", "")), failures, empties, deadline
                    )
                elif name == "save_report":
                    result = toolbox.save_report(
                        str(args.get("title", "")), str(args.get("content", ""))
                    )
                elif name == "list_reports":
                    result = toolbox.list_reports()
                elif name == "get_report":
                    result = toolbox.get_report(args.get("report_id", 0))
                elif name == "delete_reports":
                    # One confirmation per question: a second request in the same step, or after
                    # the user has already decided, is refused and never reaches the user.
                    if pending is not None or state.get("delete_outcome"):
                        result = {"error": ONE_DELETE}
                    else:
                        found, problem = toolbox.find_reports_to_delete(args)
                        if problem:
                            result = {"error": problem}
                        elif not found:
                            result = {"deleted": 0, "note": "No saved reports matched."}
                        else:
                            # Snapshot exactly what will be deleted. Nothing is deleted here:
                            # the result for this call is written by `confirm_delete`.
                            ids = [r.id for r in found]
                            pending = {
                                "call_id": call["id"],
                                "ids": ids,
                                "reports": [{"id": r.id, "title": r.title} for r in found],
                            }
                            requested = {"criteria": args, "titles": [r.title for r in found]}
                            deps.reports.log(owner, "delete_requested", ids, json.dumps(requested))
                            continue
                else:
                    result = {"error": f"Unknown tool: {name}"}
            except Exception as e:  # noqa: BLE001 - a tool failure must not end the conversation
                tracer.event("error", f"tool:{name}", error=f"{type(e).__name__}: {e}"[:300])
                result = {"error": "The tool failed unexpectedly. Tell the user it did not work."}
            results.append({"role": "tool", "call_id": call["id"], "name": name, "result": result})
        # One model call is left: say so, so the model answers from what it has and the work
        # done so far is not thrown away by the limit.
        if results and state["llm_calls"] >= settings.max_llm_calls - 1:
            tracer.event("budget", "last_step", llm_calls=state["llm_calls"])
            for message in results:
                message["result"] = {**message["result"], "instruction": LAST_STEP}
        return {
            "messages": results,
            "sql_failures": failures,
            "empty_results": empties,
            "pending_delete": pending,
            "paused_at": time.time(),
        }

    def confirm_delete(state: AgentState) -> dict:
        """Wait for the user's decision. The model has no way to supply it.

        On resume this function runs again from the top and `interrupt` returns the decision, so
        nothing before it may have side effects. The reports deleted are the ones snapshotted in
        the state by `tools`, not a fresh search. The outcome message is written here rather than
        by the model: it is exact, and it cannot be lost to a model outage after the delete.
        """
        pending = state["pending_delete"]
        decision = interrupt({"action": "delete_reports", "reports": pending["reports"]})
        # Only an explicit yes from the interface counts. Anything else leaves the reports alone.
        approved = isinstance(decision, dict) and decision.get("approved") is True
        if approved:
            deleted = deps.reports.delete(owner, pending["ids"])
            titles = "\n".join(f"- {r.title}" for r in deleted)
            outcome = f"Deleted {len(deleted)} report(s):\n{titles}\n\nThis cannot be undone."
            result = {"deleted": len(deleted), "titles": [r.title for r in deleted]}
        else:
            deps.reports.log(owner, "delete_cancelled", pending["ids"])
            outcome = "Nothing was deleted."
            result = {"deleted": 0, "note": "The user declined."}
        tracer.event("confirmation", "delete_reports", approved=approved, count=len(pending["ids"]))
        tool_result = {
            "role": "tool",
            "call_id": pending["call_id"],
            "name": "delete_reports",
            "result": result,
        }
        request = next(m for m in reversed(state["messages"]) if m["role"] == "assistant")
        if len(request["tool_calls"]) > 1:
            # Other tools ran in the same step and their results still need an answer, so the
            # model continues. The outcome is kept in the state and put in front of whatever
            # follows. The time spent waiting for the user is not charged to the question.
            return {
                "messages": [tool_result],
                "pending_delete": None,
                "delete_outcome": outcome,
                "turn_started_at": state["turn_started_at"] + time.time() - state["paused_at"],
            }
        return {
            "messages": [tool_result, _text_message(outcome)],
            "pending_delete": None,
            "answer": outcome,
            "outcome": "answered",
        }

    graph = StateGraph(AgentState)
    graph.add_node("guard", guard)
    graph.add_node("agent", agent)
    graph.add_node("tools", tools)
    graph.add_node("confirm_delete", confirm_delete)
    graph.add_edge(START, "guard")
    graph.add_conditional_edges(
        "guard", lambda s: END if s.get("outcome") == "blocked" else "agent", ["agent", END]
    )
    graph.add_conditional_edges(
        "agent", lambda s: "tools" if s["messages"][-1].get("tool_calls") else END, ["tools", END]
    )
    graph.add_conditional_edges(
        "tools",
        lambda s: "confirm_delete" if s.get("pending_delete") else "agent",
        ["confirm_delete", "agent"],
    )
    graph.add_conditional_edges(
        "confirm_delete", lambda s: END if s["answer"] else "agent", ["agent", END]
    )
    # The checkpointer is what lets the graph stop for the user and resume where it stopped.
    return graph.compile(checkpointer=InMemorySaver())
