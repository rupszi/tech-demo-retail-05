"""Command-line chat interface.

The interface owns what the model must not control: who the user is, and the confirmation of a
destructive action. It talks to the agent only through `ChatSession`.
"""

from __future__ import annotations

import argparse
import sys
import uuid
from dataclasses import replace

from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape
from rich.prompt import Confirm
from rich.table import Table

from retail_agent.agent import ChatSession, TurnResult
from retail_agent.config import BACKENDS, Settings
from retail_agent.data import create_backend
from retail_agent.llm import LLMError, ResilientLLM
from retail_agent.llm.gemini import GeminiLLM, create_client
from retail_agent.observability import Tracer, compute_stats, read_traces
from retail_agent.safety import load_profiles

HELP = """\
Ask a question about sales, customers or products, or ask for a report.

  /reports       list your saved reports
  /report <id>   show a saved report
  /trace [id]    show the steps behind the last answer, or the answer with that trace id
  /stats         show agent metrics across all recorded questions
  /help          show this help
  /quit          leave
"""
# What the spinner says while each kind of step runs.
_PROGRESS = {
    "guard": "Checking the request…",
    "llm": "Thinking…",
    "sql": "Querying the data…",
    "llm_retry": "The model is busy, retrying…",
    "llm_wait": "The model's rate limit was reached, waiting for it (up to a minute)…",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="retail-agent", description="Retail data analysis chat")
    parser.add_argument("--user", help="which sample user to sign in as (default: the first one)")
    parser.add_argument(
        "--backend",
        choices=list(BACKENDS),
        help="data source: bigquery (default) or duckdb, an offline mock",
    )
    parser.add_argument("--list-users", action="store_true", help="show the available users")
    args = parser.parse_args(argv)
    console = Console()

    try:
        settings = Settings.from_env()
    except ValueError as e:  # for example MAX_ROWS=five
        console.print(f"[red]Could not read the settings:[/] {escape(str(e))}")
        return 1
    if args.backend:  # the command line wins over DATA_BACKEND
        settings = replace(settings, data_backend=args.backend)
    try:
        profiles = load_profiles(settings.profiles_path)
    except (OSError, ValueError, KeyError) as e:
        console.print(f"[red]Could not read the user profiles:[/] {escape(str(e))}")
        return 1
    if args.list_users:
        for p in profiles.values():
            scope = escape(p.describe_scope())
            console.print(f"[bold]{escape(p.user_id)}[/]  {escape(p.name)}  [dim]({scope})[/]")
        return 0
    user = args.user or next(iter(profiles))
    if user not in profiles:
        known = escape(", ".join(profiles))
        console.print(f"[red]Unknown user {escape(repr(user))}.[/] Known users: {known}")
        return 1
    # This stands in for a verified token: the profile fixes who the user is and what they may
    # see for the whole session, and nothing typed into the chat can change it.
    profile = profiles[user]
    status = {"current": None}  # the spinner on screen, if any, so steps can update its text

    def show_progress(step: str) -> None:
        label = _PROGRESS.get(step.split(":")[0])
        if label and status["current"]:
            status["current"].update(label)

    # One id for the conversation, shared by the traces and the saved reports, so the two can be
    # read side by side.
    conversation_id = uuid.uuid4().hex[:8]
    try:
        client = create_client(settings)
        backend = create_backend(settings)
        backend.dry_run("SELECT 1")  # free; fails now rather than in the middle of a question
        # One adapter per configured model, tried in order, behind the retry and fallback logic.
        llm = ResilientLLM([GeminiLLM(client, model) for model in settings.gemini_models])
        session = ChatSession(
            llm=llm,
            backend=backend,
            profile=profile,
            settings=settings,
            tracer=Tracer(settings.trace_dir, conversation_id, profile.user_id, show_progress),
            conversation_id=conversation_id,
        )
    except Exception as e:  # noqa: BLE001 - startup problems are reported, not dumped as a trace
        console.print(f"[red]Could not start:[/] {escape(str(e))}")
        console.print(f"[dim]{_startup_hint(settings, e)}[/]")
        return 1

    def run(action) -> TurnResult:
        with console.status("Thinking…") as spinner:
            status["current"] = spinner
            try:
                return action()
            finally:
                status["current"] = None

    source = "BigQuery" if settings.data_backend == "bigquery" else "local mock data"
    # The models are tried in this order; the line under each answer names the one that answered.
    models = ", then ".join(settings.gemini_models)
    console.print(f"[bold]Retail analysis assistant[/]  [dim]{source} · {escape(models)}[/]")
    # Rich reads square brackets as formatting. Text that comes from a token, a user, the model
    # or an error is escaped wherever it is printed, so it is shown as written and a stray
    # bracket cannot break the display.
    access = escape(profile.describe_scope())
    console.print(f"Signed in as [bold]{escape(profile.name)}[/]. Access: {access}.")
    console.print("[dim]Type /help for commands.[/]\n")

    interactive = sys.stdin.isatty()
    while True:
        try:
            text = console.input("[bold cyan]you>[/] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            return 0
        if not interactive:
            console.print(text, markup=False)  # keep piped sessions readable as transcripts
        if not text:
            continue
        if text in ("/quit", "/exit"):
            return 0
        if text.startswith("/"):  # commands are handled here and never reach the model
            _command(text, session, settings, console)
            continue

        try:
            result = run(lambda question=text: session.ask(question))
            while result.confirmation:  # the decision is taken here, outside the model's reach
                approved = _confirm_delete(result.confirmation, console)
                if not interactive:
                    console.print("y" if approved else "n")
                result = run(lambda decision=approved: session.confirm(decision))
        except KeyboardInterrupt:
            # Ctrl-C while a question is being worked on drops that question, not the chat.
            console.print("\n[dim]Interrupted. That question was dropped.[/]\n")
            continue
        console.print(Markdown(result.answer))
        if result.scope_note:
            console.print(f"[dim italic]{escape(result.scope_note)}[/]")
        _footer(result, console)


def _startup_hint(settings: Settings, error: Exception) -> str:
    if isinstance(error, LLMError) or settings.data_backend != "bigquery":
        return "See Quick start in the README."
    return (
        "BigQuery needs Google Cloud credentials and GCP_PROJECT_ID in .env (see the README). "
        "To try the assistant on local mock data without a cloud account, add --backend duckdb."
    )


def _confirm_delete(request: dict, console: Console) -> bool:
    reports = request["reports"]
    table = Table(title=f"About to delete {len(reports)} saved report(s)", title_justify="left")
    table.add_column("id", justify="right")
    table.add_column("title")
    for report in reports:
        table.add_row(str(report["id"]), escape(report["title"]))
    console.print(table)
    try:
        # The default is no: pressing Enter, closing the input or Ctrl-C all leave the reports.
        return Confirm.ask(
            "Delete these reports permanently? This cannot be undone",
            default=False,
            console=console,
        )
    except (EOFError, KeyboardInterrupt):
        return False


def _footer(result: TurnResult, console: Console) -> None:
    t = result.trace or {}
    # The models that answered in this turn; usually one, more after a fallback.
    answered = dict.fromkeys(
        s["name"] for s in t.get("steps", []) if s["kind"] == "llm" and "error" not in s
    )
    models = f" ({escape(', '.join(answered))})" if answered else ""
    console.print(
        f"[dim]trace {result.trace_id} · {t.get('sql_queries', 0)} queries · "
        f"{t.get('llm_calls', 0)} model calls{models} · "
        f"{t.get('tokens_in', 0) + t.get('tokens_out', 0):,} tokens · "
        f"{t.get('duration_ms', 0) / 1000:.1f}s[/]\n"
    )


def _command(text: str, session: ChatSession, settings: Settings, console: Console) -> None:
    try:
        _run_command(text, session, settings, console)
    except Exception as e:  # noqa: BLE001 - a command must not end the chat
        console.print(f"[red]That command failed:[/] {escape(str(e))}\n")


def _run_command(text: str, session: ChatSession, settings: Settings, console: Console) -> None:
    name, _, argument = text.partition(" ")
    owner = session.profile.user_id
    if name == "/help":
        console.print(HELP)
    elif name == "/reports":
        reports = session.reports.list(owner)
        if not reports:
            console.print("You have no saved reports.\n")
            return
        table = Table("id", "title", "created")
        for r in reports:
            table.add_row(str(r.id), escape(r.title), r.created_at[:16].replace("T", " "))
        console.print(table)
    elif name == "/report":
        report = session.reports.get(owner, int(argument)) if argument.strip().isdigit() else None
        if report is None:
            console.print("No such report. Use /reports to see the ids.\n")
        else:
            console.print(Markdown(f"# {report.title}\n\n{report.content}"))
    elif name == "/trace":
        wanted = argument.strip()
        if not wanted:
            _show_trace(session.tracer.last, settings, console)
            return
        # Any answer can be looked up by the trace id printed under it, also from earlier chats.
        found = [t for t in read_traces(settings.trace_dir) if t.get("trace_id") == wanted]
        if found and found[-1].get("user") == owner:
            _show_trace(found[-1], settings, console)
        else:
            console.print("No trace of yours has that id.\n")
    elif name == "/stats":
        table = Table("metric", "value")
        for key, value in compute_stats(read_traces(settings.trace_dir)).items():
            table.add_row(key, escape(str(value)))
        console.print(table)
    else:
        console.print("Unknown command. Type /help.\n")


def _step_detail(step: dict) -> str:
    """A one-line summary. The full record, including the SQL that ran, is in the trace file."""
    if step["kind"] == "sql":
        sql = " ".join(step.get("sql", "").split())
        problem = f"{step.get('error')}: {step.get('error_message', '')[:60]}"
        result = problem if "error" in step else f"{step.get('rows')} rows"
        return f"{result} · {sql[:90]}{'…' if len(sql) > 90 else ''}"
    if step["kind"] == "sql_retry" or step["kind"] == "llm_retry":
        return f"attempt {step.get('attempt', 1)} failed: {step.get('error', '')[:70]}"
    if step["kind"] == "llm" and "error" not in step:
        # The step's name is the model that answered, so only tokens and tool calls go here.
        calls = ", ".join(step.get("tool_calls", [])) or "final answer"
        return f"{step.get('tokens_in')}+{step.get('tokens_out')} tokens · {calls}"
    detail = {k: v for k, v in step.items() if k not in ("kind", "name", "ms")}
    return str(detail)[:160] if detail else ""


def _show_trace(trace: dict | None, settings: Settings, console: Console) -> None:
    if trace is None:
        console.print("No question has been asked yet.\n")
        return
    table = Table(title=f"trace {trace['trace_id']} · {trace['outcome']}", title_justify="left")
    for column in ("step", "name", "ms", "detail"):
        table.add_column(column, overflow="fold")
    for step in trace["steps"]:
        detail = escape(_step_detail(step))  # holds SQL and error text
        table.add_row(step["kind"], escape(str(step["name"])), str(step["ms"]), detail)
    console.print(table)
    console.print(f"[dim]All traces are in {settings.trace_dir}/traces.jsonl[/]\n")
