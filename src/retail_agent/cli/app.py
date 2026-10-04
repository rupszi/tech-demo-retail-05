"""Command-line chat interface.

The interface owns everything the model must not control: who the user is, the confirmation of a
destructive action, and undo. It talks to the agent only through `ChatSession`.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace

from rich.console import Console
from rich.markdown import Markdown
from rich.prompt import Confirm
from rich.table import Table

from retail_agent.agent import ChatSession, TurnResult
from retail_agent.config import Settings
from retail_agent.data import create_backend
from retail_agent.llm import ResilientLLM
from retail_agent.llm.gemini import GeminiLLM, create_client
from retail_agent.observability import Tracer, compute_stats, read_traces
from retail_agent.safety import load_profiles

HELP = """\
Ask a question about sales, customers or products, or ask for a report.

  /reports       list your saved reports
  /report <id>   show a saved report
  /undo          restore the reports removed by your last delete
  /trace         show the steps behind the last answer
  /stats         show agent metrics across all recorded questions
  /help          show this help
  /quit          leave
"""
_PROGRESS = {
    "guard": "Checking the request…",
    "llm": "Thinking…",
    "sql": "Querying the data…",
    "llm_retry": "The model is busy, retrying…",
    "llm_wait": "The model's rate limit was reached, waiting for it (up to a minute)…",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="retail-agent", description="Retail data analysis chat")
    parser.add_argument("--user", help="user id from the profiles file (default: the first one)")
    parser.add_argument("--backend", choices=["duckdb", "bigquery"], help="override DATA_BACKEND")
    parser.add_argument("--list-users", action="store_true", help="show the available users")
    args = parser.parse_args(argv)
    console = Console()

    settings = Settings.from_env()
    if args.backend:
        settings = replace(settings, data_backend=args.backend)
    try:
        profiles = load_profiles(settings.profiles_path)
        if args.list_users:
            for p in profiles.values():
                console.print(f"[bold]{p.user_id}[/]  {p.name}  [dim]({p.describe_scope()})[/]")
            return 0
        profile = profiles[args.user or next(iter(profiles))]
        backend = create_backend(settings)
        client = create_client(settings)
    except KeyError:
        console.print(f"[red]Unknown user {args.user!r}.[/] Known users: {', '.join(profiles)}")
        return 1
    except Exception as e:  # noqa: BLE001 - startup problems are reported, not dumped as a trace
        console.print(f"[red]Could not start:[/] {e}")
        console.print("[dim]See the setup section of the README.[/]")
        return 1

    llm = ResilientLLM([GeminiLLM(client, model) for model in settings.gemini_models])
    status = {"current": None}

    def show_progress(step: str) -> None:
        label = _PROGRESS.get(step.split(":")[0])
        if label and status["current"]:
            status["current"].update(label)

    session = ChatSession(
        llm=llm,
        backend=backend,
        profile=profile,
        settings=settings,
        tracer=Tracer(settings.trace_dir, "cli", profile.user_id, on_step=show_progress),
    )

    def run(action) -> TurnResult:
        with console.status("Thinking…") as spinner:
            status["current"] = spinner
            try:
                return action()
            finally:
                status["current"] = None

    source = "BigQuery" if settings.data_backend == "bigquery" else "local mock data"
    console.print(f"[bold]Retail analysis assistant[/]  [dim]{source} · {llm.name}[/]")
    console.print(f"Signed in as [bold]{profile.name}[/]. Access: {profile.describe_scope()}.")
    console.print("[dim]Type /help for commands.[/]\n")

    interactive = sys.stdin.isatty()
    while True:
        try:
            text = console.input("[bold cyan]you>[/] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            return 0
        if not interactive:
            console.print(text)  # keep piped sessions readable as transcripts
        if not text:
            continue
        if text in ("/quit", "/exit"):
            return 0
        if text.startswith("/"):
            _command(text, session, settings, console)
            continue

        result = run(lambda question=text: session.ask(question))
        while result.confirmation:  # the decision is taken here, outside the model's reach
            approved = _confirm_delete(result.confirmation, console)
            if not interactive:
                console.print("y" if approved else "n")
            result = run(lambda decision=approved: session.confirm(decision))
        console.print(Markdown(result.answer))
        _footer(result, console)


def _confirm_delete(request: dict, console: Console) -> bool:
    reports = request["reports"]
    table = Table(title=f"About to delete {len(reports)} saved report(s)", title_justify="left")
    table.add_column("id", justify="right")
    table.add_column("title")
    for report in reports:
        table.add_row(str(report["id"]), report["title"])
    console.print(table)
    try:
        return Confirm.ask("Delete these reports?", default=False, console=console)
    except (EOFError, KeyboardInterrupt):
        return False


def _footer(result: TurnResult, console: Console) -> None:
    t = result.trace or {}
    console.print(
        f"[dim]trace {result.trace_id} · {t.get('sql_queries', 0)} queries · "
        f"{t.get('llm_calls', 0)} model calls · {t.get('tokens_in', 0) + t.get('tokens_out', 0):,} "
        f"tokens · {t.get('duration_ms', 0) / 1000:.1f}s[/]\n"
    )


def _command(text: str, session: ChatSession, settings: Settings, console: Console) -> None:
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
            table.add_row(str(r.id), r.title, r.created_at[:16].replace("T", " "))
        console.print(table)
    elif name == "/report":
        report = session.reports.get(owner, int(argument)) if argument.strip().isdigit() else None
        if report is None:
            console.print("No such report. Use /reports to see the ids.\n")
        else:
            console.print(Markdown(f"# {report.title}\n\n{report.content}"))
    elif name == "/undo":
        restored = session.undo_last_delete()
        console.print(
            f"Restored: {', '.join(restored)}\n" if restored else "There is nothing to restore.\n"
        )
    elif name == "/trace":
        _show_trace(session.tracer.last, settings, console)
    elif name == "/stats":
        table = Table("metric", "value")
        for key, value in compute_stats(read_traces(settings.trace_dir)).items():
            table.add_row(key, str(value))
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
        calls = ", ".join(step.get("tool_calls", [])) or "final answer"
        tokens = f"{step.get('tokens_in')}+{step.get('tokens_out')} tokens"
        return f"{step.get('model')} · {tokens} · {calls}"
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
        table.add_row(step["kind"], step["name"], str(step["ms"]), _step_detail(step))
    console.print(table)
    console.print(f"[dim]All traces are in {settings.trace_dir}/traces.jsonl[/]\n")
