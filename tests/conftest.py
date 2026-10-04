from pathlib import Path

import pytest

from retail_agent.data.duckdb_backend import DuckDBBackend
from retail_agent.data.mock import generate


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
