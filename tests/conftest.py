from pathlib import Path

import pytest

from retail_agent.data.duckdb_backend import DuckDBBackend
from retail_agent.data.mock import generate


@pytest.fixture(autouse=True)
def no_local_env_file(monkeypatch):
    """The tests never read the developer's own `.env`: no real key, and no local setting that
    would make a test about the defaults fail."""
    monkeypatch.setattr("retail_agent.config.load_dotenv", lambda *args, **kwargs: False)
    for name in ("DATA_BACKEND", "GEMINI_MODELS", "MAX_ROWS", "MAX_LLM_CALLS", "GOLDEN_DIR"):
        monkeypatch.delenv(name, raising=False)


class FakeClock:
    """Stands in for the `time` module: it only moves when a test, or a sleep, moves it."""

    def __init__(self, now=1000.0):
        self.now = now

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    """Put the agent's steps and tools on a clock the test controls."""
    from retail_agent.agent import graph, tools

    fake = FakeClock()
    monkeypatch.setattr(graph, "time", fake)
    monkeypatch.setattr(tools, "time", fake)
    return fake


@pytest.fixture(scope="session")
def frames():
    return generate(seed=42)


@pytest.fixture(scope="session")
def backend(frames):
    return DuckDBBackend.from_frames(frames)


@pytest.fixture(scope="session")
def profiles():
    from retail_agent.safety import load_profiles

    return load_profiles(Path(__file__).parents[1] / "config" / "users.duckdb.json")


@pytest.fixture(scope="session")
def gateway(backend, profiles):
    """Gateway factory with a row limit high enough to compare full result sets."""
    from retail_agent.safety import QueryGateway

    def make(user, max_rows=1_000_000):
        profile = profiles[user] if isinstance(user, str) else user
        return QueryGateway(backend, profile, max_rows=max_rows)

    return make


@pytest.fixture
def settings(tmp_path):
    from retail_agent.config import Settings

    root = Path(__file__).parents[1]
    return Settings(
        data_backend="duckdb",
        reports_db_path=str(tmp_path / "reports.sqlite"),
        trace_dir=str(tmp_path / "logs"),
        persona_path=str(root / "config" / "persona.md"),
        golden_dir=str(root / "golden_bucket"),
    )


@pytest.fixture
def chat(backend, profiles, settings):
    """Start a conversation driven by a scripted model: chat(*responses, user="alice")."""
    from retail_agent.agent import ChatSession

    from .fakes import ScriptedLLM

    def start(*script, user="alice", llm=None, **overrides):
        from dataclasses import replace

        model = llm or ScriptedLLM(*script)
        session = ChatSession(
            llm=model,
            backend=backend,
            profile=profiles[user],
            settings=replace(settings, **overrides),
        )
        session.model = model
        return session

    return start
