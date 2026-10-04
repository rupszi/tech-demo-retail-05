from retail_agent.cli.app import main


def test_list_users(capsys, monkeypatch):
    monkeypatch.setenv("DATA_BACKEND", "duckdb")
    assert main(["--list-users"]) == 0
    out = capsys.readouterr().out
    assert "alice" in out and "carol" in out and "all brands" in out


def test_unknown_user_is_reported_without_a_stack_trace(capsys, monkeypatch):
    monkeypatch.setenv("DATA_BACKEND", "duckdb")
    assert main(["--user", "mallory"]) == 1
    out = capsys.readouterr().out
    assert "Unknown user" in out and "Traceback" not in out


def test_a_failing_command_does_not_end_the_chat(tmp_path, capsys):
    from rich.console import Console

    from retail_agent.cli.app import _command
    from retail_agent.config import Settings

    # there is no session here, so the command raises inside; the chat must survive it
    _command("/reports", None, Settings(trace_dir=str(tmp_path)), Console())
    assert "That command failed" in capsys.readouterr().out


def test_the_confirmation_names_the_reports_and_says_it_is_permanent(monkeypatch, capsys):
    from rich.console import Console

    from retail_agent.cli.app import _confirm_delete

    request = {"action": "delete_reports", "reports": [{"id": 7, "title": "Q1 review"}]}
    monkeypatch.setattr("builtins.input", lambda *_: "n")
    assert _confirm_delete(request, Console()) is False
    shown = capsys.readouterr().out
    assert "Q1 review" in shown and "permanently" in shown and "cannot be undone" in shown
    monkeypatch.setattr("builtins.input", lambda *_: "y")
    assert _confirm_delete(request, Console()) is True


def test_bigquery_is_the_default_and_a_missing_setup_names_the_offline_option(capsys, monkeypatch):
    from retail_agent.cli import app

    seen = {}

    def no_credentials(settings):
        seen["backend"] = settings.data_backend
        raise RuntimeError("Your default credentials were not found.")

    monkeypatch.delenv("DATA_BACKEND", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(app, "create_backend", no_credentials)
    assert main(["--user", "alice"]) == 1
    out = " ".join(capsys.readouterr().out.split())
    assert seen["backend"] == "bigquery"
    assert "default credentials were not found" in out and "--backend duckdb" in out
    assert "Traceback" not in out


def test_a_missing_model_key_is_reported_without_the_bigquery_hint(capsys, monkeypatch):
    from retail_agent.cli import app
    from retail_agent.llm import LLMError

    def no_key(settings):
        raise LLMError("GEMINI_API_KEY is not set. Add it to your .env file.", transient=False)

    monkeypatch.setenv("DATA_BACKEND", "duckdb")
    monkeypatch.setattr(app, "create_client", no_key)
    assert main(["--user", "alice"]) == 1
    out = capsys.readouterr().out
    assert "GEMINI_API_KEY is not set" in out and "--backend duckdb" not in out


def test_text_from_users_and_the_model_is_shown_as_written(monkeypatch, capsys):
    """Rich reads square brackets as formatting; an unmatched one would end the chat."""
    from types import SimpleNamespace

    from rich.console import Console

    from retail_agent.cli.app import _command, _confirm_delete, _show_trace
    from retail_agent.config import Settings
    from retail_agent.reports import ReportStore

    title = "Q1 review[/] [bold]plan"
    monkeypatch.setattr("builtins.input", lambda *_: "n")
    assert _confirm_delete({"reports": [{"id": 7, "title": title}]}, Console()) is False
    assert title in capsys.readouterr().out

    reports = ReportStore(":memory:")
    reports.save("alice", "c1", title, "content")
    session = SimpleNamespace(reports=reports, profile=SimpleNamespace(user_id="alice"))
    _command("/reports", session, Settings(), Console())
    listed = capsys.readouterr().out
    assert title in listed and "That command failed" not in listed

    sql = "SELECT '[/]' AS x"
    step = {"kind": "sql", "name": "run_sql", "ms": 1, "rows": 1, "sql": sql}
    _show_trace({"trace_id": "t1", "outcome": "answered", "steps": [step]}, Settings(), Console())
    assert sql in capsys.readouterr().out
