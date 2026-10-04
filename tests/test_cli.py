from retail_agent.cli.app import main


def test_list_users(capsys, monkeypatch):
    monkeypatch.setenv("DATA_BACKEND", "duckdb")
    assert main(["--list-users"]) == 0
    out = capsys.readouterr().out
    assert "alice" in out and "carol" in out and "all products" in out


def test_unknown_user_is_reported_without_a_stack_trace(capsys, monkeypatch):
    monkeypatch.setenv("DATA_BACKEND", "duckdb")
    assert main(["--user", "mallory"]) == 1
    out = capsys.readouterr().out
    assert "Unknown user" in out and "Traceback" not in out
