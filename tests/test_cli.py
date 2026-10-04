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

    (tmp_path / "traces.jsonl").write_text("this is not json\n")
    _command("/stats", None, Settings(trace_dir=str(tmp_path)), Console())
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
